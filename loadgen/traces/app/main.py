#!/usr/bin/env python3
"""
Traces Load Generator

Generates traces using OpenTelemetry SDK and sends to both Tempo and VictoriaTraces.
"""
import os
import time
import random
import threading
import uuid
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace.propagation import set_span_in_context
from opentelemetry.context import Context
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json

# Configuration
OTLP_TEMPO = os.getenv("OTLP_TEMPO", "http://tempo:4318")
# VictoriaTraces accepts OTLP on standard OTLP endpoint
OTLP_VTRACES = os.getenv("OTLP_VTRACES", "http://victoriatraces:4317")
SPANS_PER_SEC = float(os.getenv("SPANS_PER_SEC", "2000"))
SERVICES = int(os.getenv("SERVICES", "10"))
DEPTH = int(os.getenv("DEPTH", "3"))
ERROR_RATE = float(os.getenv("ERROR_RATE", "0.05"))
SEED = int(os.getenv("SEED", "42"))

# Unique label for this benchmark run - allows querying traces from this specific run
BENCHMARK_RUN_ID = os.getenv("BENCHMARK_RUN_ID", f"benchmark-{uuid.uuid4().hex[:8]}")
BENCHMARK_APP_LABEL = "trace-load-generator"

random.seed(SEED)

# Control state
control_state = {
    "spans_per_sec": SPANS_PER_SEC,
    "error_rate": ERROR_RATE,
    "high_cardinality": False,
}

# Setup OpenTelemetry
# Add benchmark labels to resource attributes so they appear in Jaeger process tags
resource = Resource.create({
    "service.name": "trace-load-generator",
    "service.version": "1.0.0",
    "benchmark_app": BENCHMARK_APP_LABEL,
    "benchmark_run_id": BENCHMARK_RUN_ID,
})

trace.set_tracer_provider(TracerProvider(resource=resource))
tracer_provider = trace.get_tracer_provider()

# Create exporters for Tempo and VictoriaTraces
tempo_exporter = OTLPSpanExporter(
    endpoint=f"{OTLP_TEMPO}/v1/traces",
)

# Add span processor for Tempo
# With DEPTH=3, each trace has ~2.4 spans, so 2000 traces/sec = ~4800 spans/sec
# Need larger queue and more frequent flushing to prevent drops
tempo_processor = BatchSpanProcessor(
    tempo_exporter,
    max_queue_size=10000,  # Large queue to prevent drops during bursts
    max_export_batch_size=1000,
    export_timeout_millis=5000,  # Longer timeout for slow exports
    schedule_delay_millis=50,  # Flush every 50ms (more frequent)
)
tracer_provider.add_span_processor(tempo_processor)

# VictoriaTraces OTLP traces support
# VictoriaTraces uses /insert/opentelemetry/v1/traces endpoint
vtraces_exporter = None
try:
    vtraces_exporter = OTLPSpanExporter(
        endpoint=f"{OTLP_VTRACES}/insert/opentelemetry/v1/traces",
    )
    vtraces_processor = BatchSpanProcessor(
        vtraces_exporter,
        max_queue_size=10000,  # Large queue to prevent drops during bursts
        max_export_batch_size=1000,
        export_timeout_millis=5000,  # Longer timeout for slow exports
        schedule_delay_millis=50,  # Flush every 50ms (more frequent)
    )
    tracer_provider.add_span_processor(vtraces_processor)
    print(f"VictoriaTraces OTLP exporter configured: {OTLP_VTRACES}/insert/opentelemetry/v1/traces")
    print(f"Benchmark labels: benchmark_app={BENCHMARK_APP_LABEL}, benchmark_run_id={BENCHMARK_RUN_ID}")
except Exception as e:
    print(f"Warning: VictoriaTraces OTLP exporter setup: {e}")
    # Continue - Tempo will still work

tracer = trace.get_tracer(__name__)

# Service names
SERVICE_NAMES = [f"service-{i}" for i in range(SERVICES)]
OPERATIONS = ["GET", "POST", "PUT", "DELETE", "PATCH"]
ENDPOINTS = ["/api/v1/users", "/api/v1/orders", "/api/v1/products", "/api/v1/payments", "/api/v1/auth"]

def create_span(service_name, operation, parent_context=None, depth=0, high_cardinality=False):
    """Create a span with attributes."""
    # parent_context should be a Context object or None
    with tracer.start_as_current_span(
        f"{service_name}:{operation}",
        context=parent_context,
        kind=trace.SpanKind.SERVER,
    ) as span:
        # Add benchmark labels to identify traces from this run (no dots for compatibility)
        span.set_attribute("benchmark_app", BENCHMARK_APP_LABEL)
        span.set_attribute("benchmark_run_id", BENCHMARK_RUN_ID)
        
        # Add standard attributes
        span.set_attribute("service.name", service_name)
        span.set_attribute("http.method", operation)
        span.set_attribute("http.route", random.choice(ENDPOINTS))
        span.set_attribute("http.status_code", random.choice([200, 200, 200, 201, 400, 404, 500]))
        
        # Add high cardinality attributes
        if high_cardinality:
            span.set_attribute("host", f"host-{random.randint(1, 1000)}")
            span.set_attribute("pod", f"pod-{random.randint(1, 5000)}")
            span.set_attribute("region", random.choice(["us-east-1", "us-west-2", "eu-west-1"]))
            span.set_attribute("customer_id", f"customer-{random.randint(1, 1000)}")
            span.set_attribute("user_id", f"user-{random.randint(1, 10000)}")
            span.set_attribute("session_id", f"session-{random.randint(1, 100000)}")
            span.set_attribute("request_id", f"req-{random.randint(1, 1000000)}")
            span.set_attribute("version", f"v{random.randint(1, 20)}")
            span.set_attribute("env", random.choice(["prod", "staging", "dev"]))
            span.set_attribute("team", f"team-{random.randint(1, 50)}")
        
        # No sleep needed - we're just generating load, not simulating real work
        # Sleep was blocking the generator and preventing it from reaching target rate
        
        # Add error
        if random.random() < ERROR_RATE:
            span.record_exception(Exception("Simulated error"))
            span.set_status(trace.Status(trace.StatusCode.ERROR, "Simulated error"))
        
        # Create child spans if depth allows
        if depth < DEPTH and random.random() < 0.7:
            child_service = random.choice(SERVICE_NAMES)
            child_operation = random.choice(OPERATIONS)
            num_children = random.randint(1, 3)
            # Get current context for child spans
            current_context = trace.context_api.get_current()
            for _ in range(num_children):
                create_span(
                    child_service,
                    child_operation,
                    current_context,
                    depth + 1,
                    high_cardinality,
                )
        
        return span

# Global counters
span_count = {"total": 0, "start_time": None}

def trace_generator():
    """Generate traces at the target rate."""
    global span_count
    if span_count["start_time"] is None:
        span_count["start_time"] = time.time()
    
    while True:
        state = control_state.copy()
        current_rate = state["spans_per_sec"] * state.get("burst_multiplier", 1.0)
        
        # Calculate sleep time per trace
        # SPANS_PER_SEC is the target SPAN rate, not trace rate
        # Each trace has ~2.4 spans (with DEPTH=3)
        # So to get current_rate spans/sec, need: current_rate / 2.4 traces/sec
        if current_rate > 0:
            traces_per_sec = current_rate / 2.4  # Convert spans/sec to traces/sec
            sleep_time = 1.0 / traces_per_sec
        else:
            sleep_time = 0.1
        
        # Generate a trace
        service = random.choice(SERVICE_NAMES)
        operation = random.choice(OPERATIONS)
        # create_span creates a trace with multiple spans (due to DEPTH)
        # Count the trace, not individual spans
        create_span(service, operation, high_cardinality=state.get("high_cardinality", False))
        span_count["total"] += 1  # This counts traces, not spans
        
        # Print stats every 10 seconds
        elapsed = time.time() - span_count["start_time"]
        if span_count["total"] % int(current_rate / 2.4 * 10) == 0:  # Convert to traces
            traces_per_sec = span_count["total"] / elapsed if elapsed > 0 else 0
            spans_per_sec = traces_per_sec * 2.4  # Convert traces to spans
            print(f"[Traces] Generated: {span_count['total']:,} traces (~{int(span_count['total']*2.4):,} spans) | Elapsed: {elapsed:.1f}s | Rate: {traces_per_sec:.1f} traces/sec ({spans_per_sec:.1f} spans/sec)", flush=True)
        
        time.sleep(sleep_time)

class ControlHandler(BaseHTTPRequestHandler):
    """HTTP handler for control endpoint."""
    
    def do_GET(self):
        if self.path.startswith("/control"):
            self.handle_control()
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "healthy"}).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def do_POST(self):
        if self.path.startswith("/control"):
            self.handle_control()
        else:
            self.send_response(404)
            self.end_headers()
    
    def handle_control(self):
        """Handle control endpoint for dynamic rate adjustment."""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        
        if "spans_per_sec" in params:
            control_state["spans_per_sec"] = float(params["spans_per_sec"][0])
        if "error_rate" in params:
            control_state["error_rate"] = float(params["error_rate"][0])
            global ERROR_RATE
            ERROR_RATE = control_state["error_rate"]
        if "burst_multiplier" in params:
            control_state["burst_multiplier"] = float(params["burst_multiplier"][0])
        if "high_cardinality" in params:
            control_state["high_cardinality"] = params["high_cardinality"][0].lower() == "true"
        
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(control_state).encode())
    
    def log_message(self, format, *args):
        # Suppress default logging
        pass

def main():
    """Start the traces load generator."""
    global span_count
    print(f"Starting traces load generator:")
    print(f"  OTLP_TEMPO: {OTLP_TEMPO}")
    print(f"  OTLP_VTRACES: {OTLP_VTRACES}")
    print(f"  SPANS_PER_SEC: {SPANS_PER_SEC}")
    print(f"  SERVICES: {SERVICES}")
    print(f"  DEPTH: {DEPTH}")
    print(f"  ERROR_RATE: {ERROR_RATE}")
    print(f"  SEED: {SEED}")
    
    span_count["start_time"] = time.time()
    
    # Start trace generator thread
    generator_thread = threading.Thread(target=trace_generator, daemon=True)
    generator_thread.start()
    
    # Start control HTTP server
    server = HTTPServer(("0.0.0.0", 8080), ControlHandler)
    control_thread = threading.Thread(target=server.serve_forever, daemon=True)
    control_thread.start()
    
    # Keep main thread alive and print periodic stats
    try:
        last_print = time.time()
        while True:
            time.sleep(5)
            elapsed = time.time() - span_count["start_time"]
            if elapsed > 0:
                traces_per_sec = span_count["total"] / elapsed
                spans_per_sec = traces_per_sec * 2.4
                print(f"[Stats] {span_count['total']:,} traces (~{int(span_count['total']*2.4):,} spans) in {elapsed:.1f}s ({traces_per_sec:.1f} traces/sec, {spans_per_sec:.1f} spans/sec)", flush=True)
    except KeyboardInterrupt:
        # Flush remaining spans before shutdown
        elapsed = time.time() - span_count["start_time"]
        traces_per_sec = span_count["total"] / elapsed if elapsed > 0 else 0
        spans_per_sec = traces_per_sec * 2.4
        print(f"\n[Shutdown] Total traces generated: {span_count['total']:,} (~{int(span_count['total']*2.4):,} spans)")
        print(f"[Shutdown] Total time: {elapsed:.1f}s")
        print(f"[Shutdown] Average rate: {traces_per_sec:.1f} traces/sec ({spans_per_sec:.1f} spans/sec)" if elapsed > 0 else "")
        print("[Shutdown] Flushing remaining spans...")
        # Give processors time to flush
        time.sleep(2)
        # Force shutdown of processors
        tracer_provider.shutdown()

if __name__ == "__main__":
    main()

