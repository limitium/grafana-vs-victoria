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

# Create exporters
tempo_exporter = OTLPSpanExporter(
    endpoint=f"{OTLP_TEMPO}/v1/traces",
)

vtraces_exporter = OTLPSpanExporter(
    endpoint=f"{OTLP_VTRACES}/insert/opentelemetry/v1/traces",
)

# Create processors with fast flushing for testing
tempo_processor = BatchSpanProcessor(
    tempo_exporter,
    max_queue_size=512,
    export_timeout_millis=1000,
    schedule_delay_millis=100,  # Flush every 100ms
)

vtraces_processor = BatchSpanProcessor(
    vtraces_exporter,
    max_queue_size=512,
    export_timeout_millis=1000,
    schedule_delay_millis=100,  # Flush every 100ms
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
time.sleep(2)  # Give processors time to flush

# Shutdown processors
print("Shutting down processors...")
tracer_provider.shutdown()

# Wait a bit for final exports
time.sleep(1)

# Check metrics from backends
print("\n" + "="*60)
print("Checking backend metrics...")
print("="*60)

try:
    # Check Tempo
    tempo_resp = requests.get(f"{OTLP_TEMPO.replace('/v1/traces', '')}/metrics", timeout=5)
    if tempo_resp.status_code == 200:
        tempo_metrics = tempo_resp.text
        # Look for spans received
        for line in tempo_metrics.split('\n'):
            if 'tempo_distributor_spans_received_total' in line and not line.startswith('#'):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        tempo_spans = float(parts[-1])
                        print(f"Tempo spans received: {tempo_spans:,.0f}")
                    except:
                        pass
except Exception as e:
    print(f"Error checking Tempo: {e}")

try:
    # Check VictoriaTraces
    vtraces_resp = requests.get(f"{OTLP_VTRACES}/metrics", timeout=5)
    if vtraces_resp.status_code == 200:
        vtraces_metrics = vtraces_resp.text
        # Look for spans ingested
        for line in vtraces_metrics.split('\n'):
            if 'vt_rows_ingested_total' in line and 'opentelemetry' in line and not line.startswith('#'):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        vtraces_spans = float(parts[-1])
                        print(f"VictoriaTraces spans received: {vtraces_spans:,.0f}")
                    except:
                        pass
except Exception as e:
    print(f"Error checking VictoriaTraces: {e}")

print("\n" + "="*60)
print("Test Summary:")
print(f"  Spans generated: {spans_generated:,}")
print(f"  Expected: {SPANS_PER_SEC * TEST_DURATION:,}")
print(f"  Generation rate: {spans_generated/elapsed_total:.1f} spans/sec")
print("="*60)

