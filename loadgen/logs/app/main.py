#!/usr/bin/env python3
"""
Logs Load Generator

Emits structured JSON logs to stdout (for Promtail/Loki) and directly to VictoriaLogs HTTP API.
"""
import os
import sys
import json
import time
import random
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import uuid

# Configuration
LOG_RATE = float(os.getenv("LOG_RATE", "5000"))  # lines per second
JSON_FIELDS = int(os.getenv("JSON_FIELDS", "8"))
BURST_FACTOR = float(os.getenv("BURST_FACTOR", "5"))
SEED = int(os.getenv("SEED", "42"))

# Unique label for this benchmark run - allows querying logs from this specific run
BENCHMARK_RUN_ID = os.getenv("BENCHMARK_RUN_ID", f"benchmark-{uuid.uuid4().hex[:8]}")
BENCHMARK_APP_LABEL = "logs-load-generator"

random.seed(SEED)

# Control state
control_state = {
    "rate": LOG_RATE,
    "burst_factor": BURST_FACTOR,
    "high_cardinality": False,
}

# Log templates
SEVERITIES = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
SERVICES = ["api", "web", "db", "cache", "worker", "scheduler", "auth", "payment"]
OPERATIONS = ["GET", "POST", "PUT", "DELETE", "PATCH"]

def generate_log_entry(high_cardinality=False):
    """Generate a single log entry."""
    timestamp = time.time()
    severity = random.choice(SEVERITIES)
    service = random.choice(SERVICES)
    
    entry = {
        "timestamp": timestamp,
        "severity": severity,
        "service": service,
        "trace_id": str(uuid.uuid4()),
        "span_id": format(random.randint(0, 2**64-1), "016x"),
        "msg": f"Processing request {random.randint(1000, 9999)}",
        "method": random.choice(OPERATIONS),
        "path": f"/api/v{random.randint(1,3)}/resource/{random.randint(1,100)}",
        "status_code": random.choice([200, 200, 200, 201, 400, 404, 500]),
        "duration_ms": random.uniform(10, 1000),
        # Add benchmark labels to identify logs from this run (no dots for Loki compatibility)
        "benchmark_app": BENCHMARK_APP_LABEL,
        "benchmark_run_id": BENCHMARK_RUN_ID,
    }
    
    if high_cardinality:
        # Add many unique attributes
        entry.update({
            "host": f"host-{random.randint(1, 1000)}",
            "pod": f"pod-{random.randint(1, 5000)}",
            "region": random.choice(["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"]),
            "datacenter": f"dc-{random.randint(1, 100)}",
            "rack": f"rack-{random.randint(1, 50)}",
            "customer_id": f"customer-{random.randint(1, 1000)}",
            "user_id": f"user-{random.randint(1, 10000)}",
            "session_id": str(uuid.uuid4()),
            "request_id": str(uuid.uuid4()),
            "version": f"v{random.randint(1, 20)}.{random.randint(0, 99)}",
            "build": f"build-{random.randint(1000, 9999)}",
            "env": random.choice(["prod", "staging", "dev", "test"]),
            "team": f"team-{random.randint(1, 50)}",
        })
    
    # Add additional JSON fields
    for i in range(JSON_FIELDS):
        entry[f"field_{i}"] = f"value-{random.randint(1, 100)}"
    
    return entry

def log_writer():
    """Write logs to stdout - Promtail handles Loki, forwarder handles VictoriaLogs."""
    while True:
        state = control_state.copy()
        current_rate = state["rate"] * state.get("burst_multiplier", 1.0)
        
        # Calculate sleep time per log
        if current_rate > 0:
            sleep_time = 1.0 / current_rate
        else:
            sleep_time = 0.1
        
        # Generate log entry
        entry = generate_log_entry(state.get("high_cardinality", False))
        
        # Write to stdout - both Promtail (Loki) and forwarder (VictoriaLogs) will read this
        json.dump(entry, sys.stdout)
        sys.stdout.write("\n")
        sys.stdout.flush()
        
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
        
        if "rate" in params:
            control_state["rate"] = float(params["rate"][0])
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
    """Start the logs load generator."""
    print(f"Starting logs load generator:", file=sys.stderr)
    print(f"  LOG_RATE: {LOG_RATE} lines/sec", file=sys.stderr)
    print(f"  JSON_FIELDS: {JSON_FIELDS}", file=sys.stderr)
    print(f"  BURST_FACTOR: {BURST_FACTOR}", file=sys.stderr)
    print(f"  SEED: {SEED}", file=sys.stderr)
    
    # Start log writer thread
    writer_thread = threading.Thread(target=log_writer, daemon=True)
    writer_thread.start()
    
    # Start control HTTP server
    server = HTTPServer(("0.0.0.0", 8080), ControlHandler)
    control_thread = threading.Thread(target=server.serve_forever, daemon=True)
    control_thread.start()
    
    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()

