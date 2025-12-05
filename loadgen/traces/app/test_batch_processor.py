#!/usr/bin/env python3
"""
Test BatchSpanProcessor to verify it works correctly.
Generates traces and verifies they're received by backends.
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
import logging

# Enable OpenTelemetry logging to see export errors
logging.basicConfig(level=logging.INFO)
logging.getLogger("opentelemetry.exporter.otlp.proto.http.trace_exporter").setLevel(logging.DEBUG)

# Configuration
OTLP_TEMPO = os.getenv("OTLP_TEMPO", "http://localhost:4319")
OTLP_VTRACES = os.getenv("OTLP_VTRACES", "http://localhost:9410")
TEST_DURATION = int(os.getenv("TEST_DURATION", "30"))  # 30 seconds
SPANS_PER_SEC = int(os.getenv("SPANS_PER_SEC", "100"))  # 100 spans/sec for testing

print(f"BatchSpanProcessor Test")
print(f"  Duration: {TEST_DURATION} seconds")
print(f"  Rate: {SPANS_PER_SEC} spans/sec")
print(f"  Expected: {SPANS_PER_SEC * TEST_DURATION} spans")
print(f"  Tempo endpoint: {OTLP_TEMPO}/v1/traces")
print(f"  VictoriaTraces endpoint: {OTLP_VTRACES}/insert/opentelemetry/v1/traces")
print()

# Setup OpenTelemetry
resource = Resource.create({
    "service.name": "batch-processor-test",
    "service.version": "1.0.0",
})

trace.set_tracer_provider(TracerProvider(resource=resource))
tracer_provider = trace.get_tracer_provider()

# Create exporters with error handling
print("Creating exporters...")
try:
tempo_exporter = OTLPSpanExporter(
    endpoint=f"{OTLP_TEMPO}/v1/traces",
)
    print(f"  ✓ Tempo exporter created: {OTLP_TEMPO}/v1/traces")
except Exception as e:
    print(f"  ✗ Tempo exporter failed: {e}")
    sys.exit(1)

try:
vtraces_exporter = OTLPSpanExporter(
    endpoint=f"{OTLP_VTRACES}/insert/opentelemetry/v1/traces",
)
    print(f"  ✓ VictoriaTraces exporter created: {OTLP_VTRACES}/insert/opentelemetry/v1/traces")
except Exception as e:
    print(f"  ✗ VictoriaTraces exporter failed: {e}")
    sys.exit(1)

# Create processors with VERY fast flushing for testing
# Use smaller batch size and shorter delay to ensure all spans are sent
tempo_processor = BatchSpanProcessor(
    tempo_exporter,
    max_queue_size=512,
    max_export_batch_size=50,  # Export in smaller batches
    export_timeout_millis=5000,  # Longer timeout
    schedule_delay_millis=50,  # Flush every 50ms (very frequent)
)

vtraces_processor = BatchSpanProcessor(
    vtraces_exporter,
    max_queue_size=512,
    max_export_batch_size=50,  # Export in smaller batches
    export_timeout_millis=5000,  # Longer timeout
    schedule_delay_millis=50,  # Flush every 50ms (very frequent)
)

tracer_provider.add_span_processor(tempo_processor)
tracer_provider.add_span_processor(vtraces_processor)

tracer = trace.get_tracer(__name__)

# Counters
spans_generated = 0
start_time = time.time()

def generate_span(span_num):
    """Generate a single span."""
    global spans_generated
    with tracer.start_as_current_span(f"test-span-{span_num}") as span:
        span.set_attribute("span.number", span_num)
        span.set_attribute("test.timestamp", time.time())
        spans_generated += 1

# Generate spans
print("Generating spans...")
sleep_time = 1.0 / SPANS_PER_SEC
end_time = start_time + TEST_DURATION

while time.time() < end_time:
    generate_span(spans_generated + 1)
    
    # Print progress every second
    elapsed = time.time() - start_time
    if spans_generated % SPANS_PER_SEC == 0:
        rate = spans_generated / elapsed if elapsed > 0 else 0
        remaining = TEST_DURATION - elapsed
        print(f"  Generated: {spans_generated:,} spans | Rate: {rate:.1f}/sec | Remaining: {remaining:.1f}s")
    
    time.sleep(sleep_time)

elapsed_total = time.time() - start_time
print(f"\nGeneration complete:")
print(f"  Total spans generated: {spans_generated:,}")
print(f"  Total time: {elapsed_total:.2f} seconds")
print(f"  Average rate: {spans_generated/elapsed_total:.1f} spans/sec")
print(f"  Expected: {SPANS_PER_SEC * TEST_DURATION} spans")

# Flush remaining spans
print("\nFlushing remaining spans...")
# Force flush by calling shutdown which flushes all pending spans
print("Shutting down processors (this will flush all pending spans)...")
tracer_provider.shutdown()

# Wait longer for final exports
print("Waiting for final exports to complete...")
time.sleep(5)  # Wait longer for HTTP requests to complete

# Get metrics BEFORE test
print("\nGetting baseline metrics...")
tempo_before = 0
vtraces_before = 0

try:
    tempo_resp = requests.get(f"{OTLP_TEMPO.replace('/v1/traces', '')}/metrics", timeout=5)
    if tempo_resp.status_code == 200:
        for line in tempo_resp.text.split('\n'):
            if 'tempo_distributor_spans_received_total' in line and not line.startswith('#'):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        tempo_before = float(parts[-1])
                    except:
                        pass
except:
    pass

try:
    vtraces_resp = requests.get(f"{OTLP_VTRACES}/metrics", timeout=5)
    if vtraces_resp.status_code == 200:
        for line in vtraces_resp.text.split('\n'):
            if 'vt_rows_ingested_total' in line and 'opentelemetry' in line and not line.startswith('#'):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        vtraces_before = float(parts[-1])
                    except:
                        pass
except:
    pass

# Wait a bit for final exports
print("Waiting for final exports...")
time.sleep(3)

# Check metrics AFTER test
print("\n" + "="*60)
print("Checking backend metrics...")
print("="*60)

tempo_after = 0
vtraces_after = 0

try:
    tempo_resp = requests.get(f"{OTLP_TEMPO.replace('/v1/traces', '')}/metrics", timeout=5)
    if tempo_resp.status_code == 200:
        for line in tempo_resp.text.split('\n'):
            if 'tempo_distributor_spans_received_total' in line and not line.startswith('#'):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        tempo_after = float(parts[-1])
                    except:
                        pass
        tempo_received = tempo_after - tempo_before
        print(f"Tempo spans received: {tempo_received:,.0f} (before: {tempo_before:,.0f}, after: {tempo_after:,.0f})")
except Exception as e:
    print(f"Error checking Tempo: {e}")

try:
    vtraces_resp = requests.get(f"{OTLP_VTRACES}/metrics", timeout=5)
    if vtraces_resp.status_code == 200:
        for line in vtraces_resp.text.split('\n'):
            if 'vt_rows_ingested_total' in line and 'opentelemetry' in line and not line.startswith('#'):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        vtraces_after = float(parts[-1])
                    except:
                        pass
        vtraces_received = vtraces_after - vtraces_before
        print(f"VictoriaTraces spans received: {vtraces_received:,.0f} (before: {vtraces_before:,.0f}, after: {vtraces_after:,.0f})")
except Exception as e:
    print(f"Error checking VictoriaTraces: {e}")

print("\n" + "="*60)
print("Test Summary:")
print(f"  Spans generated: {spans_generated:,}")
print(f"  Expected: {SPANS_PER_SEC * TEST_DURATION:,}")
print(f"  Generation rate: {spans_generated/elapsed_total:.1f} spans/sec")
print("="*60)

