#!/usr/bin/env python3
"""
Generate labeled traces and then query them to verify counts.
"""
import os
import time
import sys
import json
import requests
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
import uuid

# Configuration
OTLP_TEMPO = os.getenv("OTLP_TEMPO", "http://tempo:4318")
OTLP_VTRACES = os.getenv("OTLP_VTRACES", "http://victoriatraces:9410")
BENCHMARK_RUN_ID = f"test-{uuid.uuid4().hex[:8]}"
BENCHMARK_APP_LABEL = "trace-load-generator"

print("=" * 70)
print("Labeled Trace Test")
print("=" * 70)
print(f"Run ID: {BENCHMARK_RUN_ID}")
print(f"App Label: {BENCHMARK_APP_LABEL}")
print(f"Tempo: {OTLP_TEMPO}/v1/traces")
print(f"VictoriaTraces: {OTLP_VTRACES}/insert/opentelemetry/v1/traces")
print()

# Setup OpenTelemetry
# Add benchmark labels to resource attributes so they appear in Jaeger process tags
resource = Resource.create({
    "service.name": "trace-load-generator",  # Must match the service name used in main.py
    "service.version": "1.0.0",
    "benchmark_app": BENCHMARK_APP_LABEL,
    "benchmark_run_id": BENCHMARK_RUN_ID,
})

trace.set_tracer_provider(TracerProvider(resource=resource))
tracer_provider = trace.get_tracer_provider()

# Create exporters
tempo_exporter = OTLPSpanExporter(
    endpoint=f"{OTLP_TEMPO}/v1/traces",
)

vtraces_exporter = OTLPSpanExporter(
    endpoint=f"{OTLP_VTRACES}/insert/opentelemetry/v1/traces",
)

# Create processors
tempo_processor = BatchSpanProcessor(
    tempo_exporter,
    max_queue_size=10000,
    max_export_batch_size=1000,
    export_timeout_millis=5000,
    schedule_delay_millis=50,
)

vtraces_processor = BatchSpanProcessor(
    vtraces_exporter,
    max_queue_size=10000,
    max_export_batch_size=1000,
    export_timeout_millis=5000,
    schedule_delay_millis=50,
)

tracer_provider.add_span_processor(tempo_processor)
tracer_provider.add_span_processor(vtraces_processor)

tracer = trace.get_tracer(__name__)

# Generate 50 traces with labels
NUM_TRACES = 50
print(f"\nGenerating {NUM_TRACES} labeled traces...")
print("-" * 70)

generated_traces = []
generated_spans = 0

for i in range(NUM_TRACES):
    with tracer.start_as_current_span(f"test-trace-{i}") as span:
        # Add benchmark labels (no dots)
        span.set_attribute("benchmark_app", BENCHMARK_APP_LABEL)
        span.set_attribute("benchmark_run_id", BENCHMARK_RUN_ID)
        span.set_attribute("test.trace_number", i)
        span.set_attribute("test.timestamp", time.time())
        
        generated_traces.append(i)
        generated_spans += 1
    
    if (i + 1) % 10 == 0:
        print(f"  Generated {i + 1}/{NUM_TRACES} traces")

print(f"\nGenerated: {len(generated_traces)} traces, {generated_spans} spans")

# Flush traces
print("\nFlushing traces...")
tracer_provider.shutdown()
time.sleep(5)  # Wait for exports

# Wait for indexing
print("Waiting 10 seconds for traces to be indexed...")
time.sleep(10)

print("\n" + "=" * 70)
print("Querying Backends...")
print("=" * 70)

# Query Tempo
print("\nQuerying Tempo...")
tempo_traces = 0
tempo_spans = 0
try:
    # Tempo API is on port 3200, not 4318
    tempo_base = OTLP_TEMPO.replace(':4318', ':3200').replace('/v1/traces', '')
    query_url = f"{tempo_base}/api/search"
    params = {
        "tags": f"benchmark_app={BENCHMARK_APP_LABEL} benchmark_run_id={BENCHMARK_RUN_ID}",
        "limit": 1000,
    }
    resp = requests.get(query_url, params=params, timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        traces = data.get("traces", [])
        trace_ids = set()
        for t in traces:
            trace_id = t.get("traceID", "")
            if trace_id:
                trace_ids.add(trace_id)
            # Tempo search returns trace summaries, not full spans
            # Count spans from the trace summary
            spans = t.get("spans", [])
            if not spans:
                # If no spans in summary, try to get from rootSpanName or other fields
                # The search API might not include full span details
                spans_count = t.get("spanCount", 0)
                if spans_count:
                    tempo_spans += spans_count
                else:
                    tempo_spans += 1  # At least 1 span per trace
            else:
                tempo_spans += len(spans)
        tempo_traces = len(trace_ids)
        print(f"  Found {tempo_traces} traces, {tempo_spans} spans")
        if traces and len(traces) > 0:
            print(f"  Sample trace keys: {list(traces[0].keys())}")
    else:
        print(f"  Query failed: {resp.status_code} - {resp.text[:200]}")
except Exception as e:
    print(f"  Error: {e}")

# Query VictoriaTraces using Jaeger-compatible API
print("\nQuerying VictoriaTraces (Jaeger API)...")
vtraces_traces = 0
vtraces_spans = 0
try:
    # VictoriaTraces exposes Jaeger-compatible API at /select/jaeger/api/traces
    # Format: /select/jaeger/api/traces?tags=key=value&limit=N&start=timestamp&end=timestamp
    query_url = f"{OTLP_VTRACES}/select/jaeger/api/traces"
    
    # Calculate time range (last hour)
    end_time = int(time.time() * 1000000)  # microseconds
    start_time = end_time - (3600 * 1000000)  # 1 hour ago
    
    # Jaeger API requires service name and tags in JSON format
    # Tags format: JSON object like {"key":"value"}
    tags_dict = {"benchmark_app": BENCHMARK_APP_LABEL}
    if BENCHMARK_RUN_ID:
        tags_dict["benchmark_run_id"] = BENCHMARK_RUN_ID
    
    # First try with tags
    params = {
        "service": "trace-load-generator",  # Required by Jaeger API
        "tags": json.dumps(tags_dict),  # JSON-encoded tags
        "limit": 1000,
        "start": start_time,
        "end": end_time,
    }
    print(f"  Query: {query_url}")
    print(f"  Service: {params['service']}")
    print(f"  Tags: {params['tags']}")
    resp = requests.get(query_url, params=params, timeout=30)
    
    # If no results, try without tags to see if any traces exist
    if resp.status_code == 200:
        data = resp.json()
        traces = data.get("data", []) or data.get("traces", [])
        if len(traces) == 0:
            print(f"  No traces with tags, trying without tags...")
            params_no_tags = {
                "service": "trace-load-generator",
                "limit": 1000,
                "start": start_time,
                "end": end_time,
            }
            resp = requests.get(query_url, params=params_no_tags, timeout=30)
            if resp.status_code == 200:
                data_no_tags = resp.json()
                traces_no_tags = data_no_tags.get("data", []) or data_no_tags.get("traces", [])
                print(f"  Found {len(traces_no_tags)} traces without tag filter")
                if traces_no_tags:
                    print(f"  Sample trace keys: {list(traces_no_tags[0].keys())}")
                    # Check what tags/attributes are available
                    sample_trace = traces_no_tags[0]
                    if "spans" in sample_trace and len(sample_trace["spans"]) > 0:
                        sample_span = sample_trace["spans"][0]
                        print(f"  Sample span keys: {list(sample_span.keys())[:15]}")
                        # Check for tags/attributes
                        if "tags" in sample_span:
                            all_span_tags = sample_span['tags']
                            print(f"  Sample span tags ({len(all_span_tags)} total): {all_span_tags[:15] if len(all_span_tags) > 15 else all_span_tags}")
                            # Look for benchmark attributes
                            benchmark_tags = [t for t in all_span_tags if 'benchmark' in t.get('key', '').lower()]
                            if benchmark_tags:
                                print(f"  Found benchmark tags in span: {benchmark_tags}")
                        if "process" in sample_span and "tags" in sample_span.get("process", {}):
                            all_proc_tags = sample_span['process']['tags']
                            print(f"  Sample process tags ({len(all_proc_tags)} total): {all_proc_tags[:10] if len(all_proc_tags) > 10 else all_proc_tags}")
                            benchmark_proc_tags = [t for t in all_proc_tags if 'benchmark' in t.get('key', '').lower()]
                            if benchmark_proc_tags:
                                print(f"  Found benchmark tags in process: {benchmark_proc_tags}")
                    # Also check processes in trace
                    if "processes" in sample_trace:
                        for proc_id, proc in list(sample_trace["processes"].items())[:1]:
                            if "tags" in proc:
                                all_trace_proc_tags = proc['tags']
                                print(f"  Sample process tags from trace ({len(all_trace_proc_tags)} total): {all_trace_proc_tags[:10] if len(all_trace_proc_tags) > 10 else all_trace_proc_tags}")
                                benchmark_trace_proc_tags = [t for t in all_trace_proc_tags if 'benchmark' in t.get('key', '').lower()]
                                if benchmark_trace_proc_tags:
                                    print(f"  Found benchmark tags in trace process: {benchmark_trace_proc_tags}")
    if resp.status_code == 200:
        # Jaeger API returns traces in the same format as Tempo
        data = resp.json()
        traces = data.get("data", [])  # Jaeger uses "data" instead of "traces"
        if not traces:
            # Try "traces" as fallback
            traces = data.get("traces", [])
        
        # The Jaeger API should filter by tags, but if it doesn't, filter in Python
        # Check process tags (where resource attributes appear)
        filtered_traces = []
        for trace in traces:
            has_benchmark = False
            # Check process tags (resource attributes appear here)
            for proc_id, proc in trace.get("processes", {}).items():
                proc_tags = proc.get("tags", [])
                has_app = any(t.get("key") == "benchmark_app" and t.get("value") == BENCHMARK_APP_LABEL for t in proc_tags)
                has_run_id = any(t.get("key") == "benchmark_run_id" and t.get("value") == BENCHMARK_RUN_ID for t in proc_tags)
                if has_app and has_run_id:
                    has_benchmark = True
                    break
            if has_benchmark:
                filtered_traces.append(trace)
        
        # Use filtered traces if Jaeger didn't filter, otherwise use all traces
        if len(filtered_traces) > 0 or len(traces) == 0:
            traces_to_count = filtered_traces
        else:
            # Jaeger filtered, use all traces
            traces_to_count = traces
        
        trace_ids = set()
        total_spans = 0
        
        for trace in traces_to_count:
            # Jaeger trace format: {"traceID": "...", "spans": [...]}
            trace_id = trace.get("traceID", "")
            if trace_id:
                trace_ids.add(trace_id)
            
            # Count spans in this trace
            spans = trace.get("spans", [])
            total_spans += len(spans)
        
        vtraces_traces = len(trace_ids)
        vtraces_spans = total_spans
        print(f"  Found {vtraces_traces} traces (filtered from {len(traces)} total), {vtraces_spans} spans")
        if filtered_traces and len(filtered_traces) > 0:
            print(f"  Sample trace keys: {list(filtered_traces[0].keys())}")
    else:
        print(f"  Query failed: {resp.status_code} - {resp.text[:200]}")
except Exception as e:
    print(f"  Error: {e}")

# Summary
print("\n" + "=" * 70)
print("Summary:")
print("=" * 70)
print(f"Generated: {NUM_TRACES} traces, {generated_spans} spans")
print(f"Tempo: {tempo_traces} traces, {tempo_spans} spans")
print(f"VictoriaTraces: {vtraces_traces} traces, {vtraces_spans} spans")
print()
print(f"Tempo match: {tempo_traces == NUM_TRACES} ({tempo_traces}/{NUM_TRACES})")
print(f"VictoriaTraces match: {vtraces_traces == NUM_TRACES} ({vtraces_traces}/{NUM_TRACES})")
if tempo_spans > 0 and vtraces_spans > 0:
    print(f"Span ratio (VictoriaTraces / Tempo): {vtraces_spans / tempo_spans:.2f}x")

