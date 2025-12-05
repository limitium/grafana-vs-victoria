#!/usr/bin/env python3
"""
Trace Verification Test
Generates specific traces with unique identifiers and verifies they're in both backends.
"""
import os
import time
import requests
import sys
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource

# Configuration
OTLP_TEMPO = os.getenv("OTLP_TEMPO", "http://localhost:4319")
OTLP_VTRACES = os.getenv("OTLP_VTRACES", "http://localhost:9410")

print("=" * 70)
print("Trace Verification Test")
print("=" * 70)
print(f"Tempo endpoint: {OTLP_TEMPO}/v1/traces")
print(f"VictoriaTraces endpoint: {OTLP_VTRACES}/insert/opentelemetry/v1/traces")
print()

# Setup OpenTelemetry
resource = Resource.create({
    "service.name": "trace-verification-test",
    "service.version": "1.0.0",
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

# Create processors with fast flushing
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

# Generate test traces
test_traces = []

print("\nGenerating test traces...")
print("-" * 70)

# First 10 traces
print("\n1. Generating FIRST 10 traces...")
for i in range(1, 11):
    trace_id = f"FIRST-{i:03d}"
    with tracer.start_as_current_span(f"test-trace-{trace_id}") as span:
        span.set_attribute("test.batch", "first")
        span.set_attribute("test.trace_id", trace_id)
        span.set_attribute("test.number", i)
        span.set_attribute("test.timestamp", time.time())
    test_traces.append(trace_id)
    print(f"  Generated trace: {trace_id}")
    time.sleep(0.1)  # Small delay between traces

# Wait a bit
print("\nWaiting 5 seconds...")
time.sleep(5)

# Middle 10 traces
print("\n2. Generating MIDDLE 10 traces...")
for i in range(1, 11):
    trace_id = f"MIDDLE-{i:03d}"
    with tracer.start_as_current_span(f"test-trace-{trace_id}") as span:
        span.set_attribute("test.batch", "middle")
        span.set_attribute("test.trace_id", trace_id)
        span.set_attribute("test.number", i + 10)
        span.set_attribute("test.timestamp", time.time())
    test_traces.append(trace_id)
    print(f"  Generated trace: {trace_id}")
    time.sleep(0.1)

# Wait a bit
print("\nWaiting 5 seconds...")
time.sleep(5)

# Last 10 traces
print("\n3. Generating LAST 10 traces...")
for i in range(1, 11):
    trace_id = f"LAST-{i:03d}"
    with tracer.start_as_current_span(f"test-trace-{trace_id}") as span:
        span.set_attribute("test.batch", "last")
        span.set_attribute("test.trace_id", trace_id)
        span.set_attribute("test.number", i + 20)
        span.set_attribute("test.timestamp", time.time())
    test_traces.append(trace_id)
    print(f"  Generated trace: {trace_id}")
    time.sleep(0.1)

print(f"\nTotal traces generated: {len(test_traces)}")

# Flush all traces
print("\nFlushing traces...")
time.sleep(2)
tracer_provider.shutdown()
time.sleep(3)  # Wait for exports to complete

print("\n" + "=" * 70)
print("Verifying traces in backends...")
print("=" * 70)

# Check Tempo
print("\nChecking Tempo...")
try:
    # Query Tempo for traces with test.batch attribute
    for batch in ["first", "middle", "last"]:
        # Tempo query API - try to find traces
        query_url = f"{OTLP_TEMPO.replace('/v1/traces', '')}/api/search"
        params = {
            "tags": f"test.batch={batch}",
            "limit": 20
        }
        try:
            resp = requests.get(query_url, params=params, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                print(f"  {batch.capitalize()} batch: Found {len(data.get('traces', []))} traces")
            else:
                print(f"  {batch.capitalize()} batch: Query failed ({resp.status_code})")
        except Exception as e:
            print(f"  {batch.capitalize()} batch: Error - {e}")
except Exception as e:
    print(f"Error checking Tempo: {e}")

# Check VictoriaTraces
print("\nChecking VictoriaTraces...")
try:
    # VictoriaTraces query API
    for batch in ["first", "middle", "last"]:
        query_url = f"{OTLP_VTRACES}/select/logsql/query"
        # Use LogSQL to query traces
        query = f'test.batch="{batch}"'
        params = {
            "query": query,
            "limit": 20
        }
        try:
            resp = requests.get(query_url, params=params, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                print(f"  {batch.capitalize()} batch: Found {len(data.get('hits', []))} traces")
            else:
                print(f"  {batch.capitalize()} batch: Query failed ({resp.status_code})")
        except Exception as e:
            print(f"  {batch.capitalize()} batch: Error - {e}")
except Exception as e:
    print(f"Error checking VictoriaTraces: {e}")

# Get metrics
print("\n" + "=" * 70)
print("Backend Metrics:")
print("=" * 70)

try:
    tempo_resp = requests.get(f"{OTLP_TEMPO.replace('/v1/traces', '')}/metrics", timeout=5)
    if tempo_resp.status_code == 200:
        for line in tempo_resp.text.split('\n'):
            if 'tempo_distributor_spans_received_total' in line and not line.startswith('#'):
                parts = line.split()
                if len(parts) >= 2:
                    print(f"Tempo spans received: {float(parts[-1]):,.0f}")
except:
    pass

try:
    vtraces_resp = requests.get(f"{OTLP_VTRACES}/metrics", timeout=5)
    if vtraces_resp.status_code == 200:
        for line in vtraces_resp.text.split('\n'):
            if 'vt_rows_ingested_total' in line and 'opentelemetry' in line and not line.startswith('#'):
                parts = line.split()
                if len(parts) >= 2:
                    print(f"VictoriaTraces spans received: {float(parts[-1]):,.0f}")
except:
    pass

print("\n" + "=" * 70)
print("Test complete!")
print("=" * 70)

