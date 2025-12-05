#!/usr/bin/env python3
"""
Metrics Load Generator

Exposes a Prometheus metrics endpoint with configurable cardinality and churn.
"""
import os
import random
import time
import threading
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from prometheus_client import Counter, Gauge, Histogram, start_http_server, generate_latest
from urllib.parse import urlparse, parse_qs
import json

# Configuration
SERIES_TOTAL = int(os.getenv("SERIES_TOTAL", "200000"))
CHURN_RATE = float(os.getenv("CHURN_RATE", "0.02"))
SCRAPE_INTERVAL = int(os.getenv("SCRAPE_INTERVAL", "10"))
SEED = int(os.getenv("SEED", "42"))

# Unique label for this benchmark run - allows querying metrics from this specific run
BENCHMARK_RUN_ID = os.getenv("BENCHMARK_RUN_ID", f"benchmark-{uuid.uuid4().hex[:8]}")
BENCHMARK_APP_LABEL = "metrics-load-generator"

random.seed(SEED)

# Metrics registry
metrics = {}
metrics_lock = threading.Lock()
current_series = set()
control_state = {
    "rate_multiplier": 1.0,
    "series_count": SERIES_TOTAL,
    "churn_rate": CHURN_RATE,
}

# Generate label combinations
def generate_labels(series_idx, include_all=False):
    """Generate a label set for a series index."""
    # Base labels - always include benchmark labels (no dots for Prometheus compatibility)
    labels = {
        "job": f"test-job-{series_idx % 10}",
        "instance": f"instance-{series_idx % 100}",
        "benchmark_app": BENCHMARK_APP_LABEL,
        "benchmark_run_id": BENCHMARK_RUN_ID,
    }
    
    if include_all:
        # High cardinality labels for cardinality scenario
        labels.update({
            "host": f"host-{series_idx % 1000}",
            "pod": f"pod-{series_idx % 5000}",
            "region": f"region-{series_idx % 10}",
            "version": f"v{series_idx % 20}",
            "customer": f"customer-{series_idx % 100}",
            "env": ["prod", "staging", "dev"][series_idx % 3],
            "team": f"team-{series_idx % 50}",
        })
    
    return labels

def create_metric(name, labels):
    """Create or get a metric with given labels."""
    label_str = ",".join([f'{k}="{v}"' for k, v in sorted(labels.items())])
    key = f"{name}{{{label_str}}}"
    
    with metrics_lock:
        if key not in metrics:
            metrics[key] = {
                "counter": Counter(f"test_metric_{len(metrics)}", "Test metric", list(labels.keys())),
                "gauge": Gauge(f"test_gauge_{len(metrics)}", "Test gauge", list(labels.keys())),
                "histogram": Histogram(f"test_histogram_{len(metrics)}", "Test histogram", list(labels.keys())),
            }
        return metrics[key]

def update_metrics():
    """Continuously update metric values."""
    while True:
        with metrics_lock:
            state = control_state.copy()
        
        target_series = int(state["series_count"] * state["rate_multiplier"])
        
        # Add/remove series based on churn
        if len(current_series) < target_series:
            # Add new series
            needed = target_series - len(current_series)
            include_all = state.get("high_cardinality", False)
            for _ in range(min(needed, int(target_series * state["churn_rate"]))):
                if len(current_series) >= SERIES_TOTAL * 2:
                    break
                idx = random.randint(0, SERIES_TOTAL * 2)
                if idx not in current_series:
                    labels = generate_labels(idx, include_all)
                    create_metric("test_metric", labels)
                    current_series.add(idx)
        
        # Remove series based on churn
        if len(current_series) > target_series:
            to_remove = int(len(current_series) * state["churn_rate"])
            removed = random.sample(list(current_series), min(to_remove, len(current_series) - target_series))
            for idx in removed:
                current_series.discard(idx)
        
        # Update existing metrics
        for idx in list(current_series)[:1000]:  # Update up to 1000 at a time
            labels = generate_labels(idx, state.get("high_cardinality", False))
            metric_set = create_metric("test_metric", labels)
            metric_set["counter"].labels(**labels).inc(random.uniform(0.1, 10.0) * state["rate_multiplier"])
            metric_set["gauge"].labels(**labels).set(random.uniform(0, 100))
            metric_set["histogram"].labels(**labels).observe(random.uniform(0, 1000))
        
        time.sleep(SCRAPE_INTERVAL / 10.0)

class MetricsHandler(BaseHTTPRequestHandler):
    """HTTP handler for metrics and control endpoints."""
    
    def do_GET(self):
        if self.path == "/metrics":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(generate_latest())
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "healthy"}).encode())
        elif self.path.startswith("/control"):
            self.handle_control()
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
        
        if "rate_multiplier" in params:
            control_state["rate_multiplier"] = float(params["rate_multiplier"][0])
        if "series_count" in params:
            control_state["series_count"] = int(params["series_count"][0])
        if "churn_rate" in params:
            control_state["churn_rate"] = float(params["churn_rate"][0])
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
    """Start the metrics load generator."""
    print(f"Starting metrics load generator:")
    print(f"  SERIES_TOTAL: {SERIES_TOTAL}")
    print(f"  CHURN_RATE: {CHURN_RATE}")
    print(f"  SCRAPE_INTERVAL: {SCRAPE_INTERVAL}")
    print(f"  SEED: {SEED}")
    
    # Start metrics update thread
    update_thread = threading.Thread(target=update_metrics, daemon=True)
    update_thread.start()
    
    # Start HTTP server
    server = HTTPServer(("0.0.0.0", 9100), MetricsHandler)
    print("Metrics server listening on :9100")
    server.serve_forever()

if __name__ == "__main__":
    main()




