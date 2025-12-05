#!/usr/bin/env python3
"""
Query traces from both Tempo and VictoriaTraces by label.

This script queries both backends for traces with the benchmark_app label
and counts them to verify ingestion.
"""
import os
import sys
import json
import requests
import time
from datetime import datetime, timedelta

# Configuration
OTLP_TEMPO = os.getenv("OTLP_TEMPO", "http://localhost:4318")
OTLP_VTRACES = os.getenv("OTLP_VTRACES", "http://localhost:9410")
BENCHMARK_APP_LABEL = os.getenv("BENCHMARK_APP_LABEL", "trace-load-generator")
BENCHMARK_RUN_ID = os.getenv("BENCHMARK_RUN_ID", None)

# Time range for queries (last hour by default)
END_TIME = int(time.time())
START_TIME = END_TIME - 3600  # 1 hour ago

print("=" * 70)
print("Trace Query by Label")
print("=" * 70)
print(f"Tempo endpoint: {OTLP_TEMPO}")
print(f"VictoriaTraces endpoint: {OTLP_VTRACES}")
print(f"Label: benchmark_app={BENCHMARK_APP_LABEL}")
if BENCHMARK_RUN_ID:
    print(f"Run ID: benchmark_run_id={BENCHMARK_RUN_ID}")
print(f"Time range: {datetime.fromtimestamp(START_TIME)} to {datetime.fromtimestamp(END_TIME)}")
print()

def query_tempo_traces():
    """Query Tempo for traces with benchmark_app label."""
    print("Querying Tempo...")
    print("-" * 70)
    
    # Tempo search API is on port 3200
    # Format: /api/search?tags=key=value&limit=N&start=timestamp&end=timestamp
    tempo_base = OTLP_TEMPO.replace(':4318', ':3200').replace('/v1/traces', '')
    query_url = f"{tempo_base}/api/search"
    
    # Try querying by benchmark_app label (no dots)
    params = {
        "tags": f"benchmark_app={BENCHMARK_APP_LABEL}",
        "limit": 10000,  # Large limit to get all traces
        "start": START_TIME,
        "end": END_TIME,
    }
    
    if BENCHMARK_RUN_ID:
        # If run_id is specified, query by both labels
        params["tags"] = f"benchmark_app={BENCHMARK_APP_LABEL} benchmark_run_id={BENCHMARK_RUN_ID}"
    
    try:
        resp = requests.get(query_url, params=params, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            traces = data.get("traces", [])
            
            # Count unique trace IDs
            trace_ids = set()
            total_spans = 0
            
            for trace in traces:
                trace_id = trace.get("traceID", "")
                if trace_id:
                    trace_ids.add(trace_id)
                # Count spans in this trace
                spans = trace.get("spans", [])
                total_spans += len(spans)
            
            print(f"  Status: OK")
            print(f"  Unique traces: {len(trace_ids):,}")
            print(f"  Total spans: {total_spans:,}")
            print(f"  Average spans per trace: {total_spans / len(trace_ids) if trace_ids else 0:.2f}")
            
            return {
                "traces": len(trace_ids),
                "spans": total_spans,
                "status": "ok"
            }
        else:
            print(f"  Status: Error {resp.status_code}")
            print(f"  Response: {resp.text[:200]}")
            return {"traces": 0, "spans": 0, "status": f"error_{resp.status_code}"}
    except Exception as e:
        print(f"  Status: Exception - {e}")
        return {"traces": 0, "spans": 0, "status": f"exception: {e}"}

def query_victoriatraces_traces():
    """Query VictoriaTraces for traces with benchmark_app label using Jaeger API."""
    print("\nQuerying VictoriaTraces (Jaeger API)...")
    print("-" * 70)
    
    # VictoriaTraces exposes Jaeger-compatible API at /select/jaeger/api/traces
    # Format: /select/jaeger/api/traces?tags=key=value&limit=N&start=timestamp&end=timestamp
    query_url = f"{OTLP_VTRACES}/select/jaeger/api/traces"
    
    # Calculate time range in microseconds
    end_time = END_TIME * 1000000
    start_time = START_TIME * 1000000
    
    # Jaeger API requires service name and tags in JSON format
    import json
    # Use underscores instead of dots
    tags_dict = {"benchmark_app": BENCHMARK_APP_LABEL}
    if BENCHMARK_RUN_ID:
        tags_dict["benchmark_run_id"] = BENCHMARK_RUN_ID
    
    params = {
        "service": "trace-load-generator",  # Service name from resource (must match)
        "tags": json.dumps(tags_dict),  # JSON-encoded tags (resource attributes appear in process tags)
        "limit": 10000,
        "start": start_time,
        "end": end_time,
    }
    
    try:
        resp = requests.get(query_url, params=params, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            
            # Jaeger API returns traces in "data" field
            traces = data.get("data", [])
            if not traces:
                traces = data.get("traces", [])
            
            # Count unique trace IDs and total spans
            trace_ids = set()
            total_spans = 0
            
            for trace in traces:
                trace_id = trace.get("traceID", "")
                if trace_id:
                    trace_ids.add(trace_id)
                spans = trace.get("spans", [])
                total_spans += len(spans)
            
            print(f"  Status: OK")
            print(f"  Unique traces: {len(trace_ids):,}")
            print(f"  Total spans: {total_spans:,}")
            print(f"  Average spans per trace: {total_spans / len(trace_ids) if trace_ids else 0:.2f}")
            
            return {
                "traces": len(trace_ids),
                "spans": total_spans,
                "status": "ok"
            }
        else:
            print(f"  Status: Error {resp.status_code}")
            print(f"  Response: {resp.text[:200]}")
            return {"traces": 0, "spans": 0, "status": f"error_{resp.status_code}"}
    except Exception as e:
        print(f"  Status: Exception - {e}")
        return {"traces": 0, "spans": 0, "status": f"exception: {e}"}

def get_metrics():
    """Get metrics from both backends."""
    print("\n" + "=" * 70)
    print("Backend Metrics:")
    print("=" * 70)
    
    # Tempo metrics
    print("\nTempo Metrics:")
    print("-" * 70)
    try:
        tempo_base = OTLP_TEMPO.replace(':4318', ':3200').replace('/v1/traces', '')
        resp = requests.get(f"{tempo_base}/metrics", timeout=5)
        if resp.status_code == 200:
            for line in resp.text.split('\n'):
                if 'tempo_distributor_spans_received_total' in line and not line.startswith('#'):
                    parts = line.split()
                    if len(parts) >= 2:
                        print(f"  Spans received: {float(parts[-1]):,.0f}")
                        break
    except Exception as e:
        print(f"  Error: {e}")
    
    # VictoriaTraces metrics
    print("\nVictoriaTraces Metrics:")
    print("-" * 70)
    try:
        resp = requests.get(f"{OTLP_VTRACES}/metrics", timeout=5)
        if resp.status_code == 200:
            for line in resp.text.split('\n'):
                if 'vt_rows_ingested_total' in line and 'opentelemetry' in line and not line.startswith('#'):
                    parts = line.split()
                    if len(parts) >= 2:
                        print(f"  Rows ingested: {float(parts[-1]):,.0f}")
                        break
    except Exception as e:
        print(f"  Error: {e}")

if __name__ == "__main__":
    # Query both backends
    tempo_result = query_tempo_traces()
    vtraces_result = query_victoriatraces_traces()
    
    # Get metrics
    get_metrics()
    
    # Summary
    print("\n" + "=" * 70)
    print("Summary:")
    print("=" * 70)
    print(f"Tempo:")
    print(f"  Traces: {tempo_result['traces']:,}")
    print(f"  Spans: {tempo_result['spans']:,}")
    print(f"\nVictoriaTraces:")
    print(f"  Traces: {vtraces_result['traces']:,}")
    print(f"  Spans: {vtraces_result['spans']:,}")
    
    if tempo_result['traces'] > 0 and vtraces_result['traces'] > 0:
        trace_ratio = vtraces_result['traces'] / tempo_result['traces']
        print(f"\nTrace Ratio (VictoriaTraces / Tempo): {trace_ratio:.2f}x")
    
    if tempo_result['spans'] > 0 and vtraces_result['spans'] > 0:
        span_ratio = vtraces_result['spans'] / tempo_result['spans']
        print(f"Span Ratio (VictoriaTraces / Tempo): {span_ratio:.2f}x")

