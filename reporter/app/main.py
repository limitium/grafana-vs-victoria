#!/usr/bin/env python3
"""
Report Generator

Generates Markdown and HTML reports with charts from test artifacts.
"""
import os
import json
import statistics
from pathlib import Path
from datetime import datetime
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from jinja2 import Template
import markdown
from tabulate import tabulate

ARTIFACTS_DIR = Path("/artifacts")
REPORTS_DIR = Path("/reports")
CHARTS_DIR = REPORTS_DIR / "charts"

def find_latest_run():
    """Find the latest test run directory."""
    if not ARTIFACTS_DIR.exists():
        return None
    
    runs = [d for d in ARTIFACTS_DIR.iterdir() if d.is_dir()]
    if not runs:
        return None
    
    return max(runs, key=lambda x: x.stat().st_mtime)

def load_run_data(run_dir):
    """Load data from a test run."""
    with open(run_dir / "manifest.json") as f:
        manifest = json.load(f)
    
    with open(run_dir / "observations.json") as f:
        observations = json.load(f)
    
    return manifest, observations

def calculate_stats(values):
    """Calculate statistics for a list of values."""
    if not values:
        return {"mean": 0, "p50": 0, "p90": 0, "p99": 0, "min": 0, "max": 0}
    
    sorted_vals = sorted(values)
    return {
        "mean": statistics.mean(values),
        "p50": statistics.median(sorted_vals),
        "p90": statistics.quantiles(sorted_vals, n=10)[8] if len(sorted_vals) >= 10 else sorted_vals[-1],
        "p99": statistics.quantiles(sorted_vals, n=100)[98] if len(sorted_vals) >= 100 else sorted_vals[-1],
        "min": min(values),
        "max": max(values),
    }

def extract_metrics(observations):
    """Extract time series metrics from observations."""
    metrics = {
        "prometheus": {"cpu": [], "memory": [], "timestamps": []},
        "victoriametrics": {"cpu": [], "memory": [], "timestamps": []},
        "loki": {"cpu": [], "memory": [], "timestamps": []},
        "victorialogs": {"cpu": [], "memory": [], "timestamps": []},
        "tempo": {"cpu": [], "memory": [], "timestamps": []},
        "victoriatraces": {"cpu": [], "memory": [], "timestamps": []},
    }
    
    for obs in observations:
        timestamp = obs["timestamp"]
        for service_name, service_data in obs.get("services", {}).items():
            if service_name in metrics:
                docker_stats = service_data.get("docker_stats", {})
                metrics[service_name]["cpu"].append(docker_stats.get("cpu_percent", 0))
                metrics[service_name]["memory"].append(docker_stats.get("memory_bytes", 0) / 1024 / 1024)  # MB
                metrics[service_name]["timestamps"].append(datetime.fromtimestamp(timestamp))
    
    return metrics

def generate_charts(metrics, run_id):
    """Generate charts from metrics data."""
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    
    # CPU usage over time
    fig, ax = plt.subplots(figsize=(12, 6))
    for service_name, data in metrics.items():
        if data["timestamps"]:
            ax.plot(data["timestamps"], data["cpu"], label=service_name, linewidth=2)
    ax.set_xlabel("Time")
    ax.set_ylabel("CPU %")
    ax.set_title("CPU Usage Over Time")
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / f"{run_id}_cpu.png", dpi=150)
    plt.close()
    
    # Memory usage over time
    fig, ax = plt.subplots(figsize=(12, 6))
    for service_name, data in metrics.items():
        if data["timestamps"]:
            ax.plot(data["timestamps"], data["memory"], label=service_name, linewidth=2)
    ax.set_xlabel("Time")
    ax.set_ylabel("Memory (MB)")
    ax.set_title("Memory Usage Over Time")
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / f"{run_id}_memory.png", dpi=150)
    plt.close()
    
    # Query latency comparison (bar chart)
    # This would need to be extracted from manifest query_latencies
    # For now, create a placeholder
    
    return [
        f"{run_id}_cpu.png",
        f"{run_id}_memory.png",
    ]

def format_bytes(size_bytes):
    """Format bytes to human readable."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"

def extract_query_results(observations):
    """Extract query results including label-based trace queries."""
    query_results = {
        "tempo_traces": [],
        "victoriatraces_traces": [],
        "tempo_spans": [],
        "victoriatraces_spans": [],
    }
    
    for obs in observations:
        queries = obs.get("queries", {})
        # Extract trace query results
        tempo_query = queries.get("tempo:traces_by_label", {})
        if tempo_query.get("success"):
            query_results["tempo_traces"].append(tempo_query.get("trace_count", 0))
            query_results["tempo_spans"].append(tempo_query.get("span_count", 0))
        
        vtraces_query = queries.get("victoriatraces:traces_by_label", {})
        if vtraces_query.get("success"):
            query_results["victoriatraces_traces"].append(vtraces_query.get("trace_count", 0))
            query_results["victoriatraces_spans"].append(vtraces_query.get("span_count", 0))
    
    return query_results

def generate_summary_tables(manifest, observations):
    """Generate summary comparison tables."""
    tables = {}
    
    # Load volume information
    load_volume = manifest.get("load_volume", {})
    if load_volume:
        tables["load_volume"] = pd.DataFrame([{
            "Metric": "Time Series",
            "Value": f"{load_volume.get('series_total', 0):,} series",
            "Rate": f"{load_volume.get('metrics_series', 0):,} active"
        }, {
            "Metric": "Log Lines",
            "Value": f"{load_volume.get('log_lines', 0):,} lines",
            "Rate": f"{load_volume.get('log_rate', 0):.0f} lines/sec"
        }, {
            "Metric": "Trace Spans",
            "Value": f"{load_volume.get('traces_spans', 0):,} spans",
            "Rate": f"{load_volume.get('spans_rate', 0):.0f} spans/sec"
        }])
    
    # Extract query latencies from manifest
    query_latencies = manifest.get("query_latencies", {})
    
    # Metrics comparison
    metrics_data = []
    for query_key, latencies in query_latencies.items():
        if "prometheus" in query_key or "victoriametrics" in query_key:
            service = "prometheus" if "prometheus" in query_key else "victoriametrics"
            metrics_data.append({
                "Service": service,
                "Query": query_key.split(":")[-1],
                "P50 (ms)": round(latencies.get("p50", 0), 2),
                "P90 (ms)": round(latencies.get("p90", 0), 2),
                "P99 (ms)": round(latencies.get("p99", 0), 2),
            })
    
    if metrics_data:
        tables["metrics"] = pd.DataFrame(metrics_data)
    
    # Logs comparison with error tracking
    logs_data = []
    query_errors = {}
    for obs in observations:
        for query_key, result in obs.get("queries", {}).items():
            if not result.get("success", False):
                if query_key not in query_errors:
                    query_errors[query_key] = 0
                query_errors[query_key] += 1
    
    for query_key, latencies in query_latencies.items():
        if "loki" in query_key or "victorialogs" in query_key:
            service = "loki" if "loki" in query_key else "victorialogs"
            error_count = query_errors.get(query_key, 0)
            logs_data.append({
                "Service": service,
                "Query": query_key.split(":")[-1],
                "P50 (ms)": round(latencies.get("p50", 0), 2),
                "P90 (ms)": round(latencies.get("p90", 0), 2),
                "P99 (ms)": round(latencies.get("p99", 0), 2),
                "Errors": error_count,
            })
    
    if logs_data:
        tables["logs"] = pd.DataFrame(logs_data)
    
    # Traces comparison (Tempo vs VictoriaTraces)
    # Use label-based query results if available, otherwise fall back to ingest metrics
    query_results = extract_query_results(observations)
    traces_data = []
    
    # Get trace counts from label-based queries (most accurate)
    tempo_trace_count = max(query_results["tempo_traces"]) if query_results["tempo_traces"] else 0
    tempo_span_count = max(query_results["tempo_spans"]) if query_results["tempo_spans"] else 0
    vtraces_trace_count = max(query_results["victoriatraces_traces"]) if query_results["victoriatraces_traces"] else 0
    vtraces_span_count = max(query_results["victoriatraces_spans"]) if query_results["victoriatraces_spans"] else 0
    
    # Fall back to ingest metrics if query results not available
    if tempo_trace_count == 0 or tempo_span_count == 0:
        tempo_ingest = []
        tempo_dropped = []
        for obs in observations:
            tempo_svc = obs.get("services", {}).get("tempo", {})
            ingest = tempo_svc.get("ingest_metrics", {})
            if ingest.get("spans_received"):
                tempo_ingest.append(ingest["spans_received"])
            if ingest.get("spans_dropped"):
                tempo_dropped.append(ingest["spans_dropped"])
        if tempo_ingest:
            tempo_span_count = max(tempo_ingest) if tempo_ingest else 0
            max_dropped = max(tempo_dropped) if tempo_dropped else 0
        else:
            max_dropped = 0
    else:
        max_dropped = 0  # Query results don't include dropped count
    
    drop_rate = round((max_dropped / tempo_span_count * 100) if tempo_span_count > 0 else 0, 2)
    traces_data.append({
        "Service": "Tempo",
        "Traces (by label)": int(tempo_trace_count),
        "Spans (by label)": int(tempo_span_count),
        "Spans Dropped": int(max_dropped),
        "Drop Rate %": drop_rate,
    })
    
    # VictoriaTraces
    if vtraces_trace_count == 0 or vtraces_span_count == 0:
        vtraces_ingest = []
        vtraces_dropped = []
        for obs in observations:
            vtraces_svc = obs.get("services", {}).get("victoriatraces", {})
            ingest = vtraces_svc.get("ingest_metrics", {})
            if ingest.get("spans_received"):
                vtraces_ingest.append(ingest["spans_received"])
            if ingest.get("spans_dropped"):
                vtraces_dropped.append(ingest["spans_dropped"])
        if vtraces_ingest:
            vtraces_span_count = max(vtraces_ingest) if vtraces_ingest else 0
            max_dropped = max(vtraces_dropped) if vtraces_dropped else 0
        else:
            max_dropped = 0
    else:
        max_dropped = 0  # Query results don't include dropped count
    
    drop_rate = round((max_dropped / vtraces_span_count * 100) if vtraces_span_count > 0 else 0, 2)
    traces_data.append({
        "Service": "VictoriaTraces",
        "Traces (by label)": int(vtraces_trace_count),
        "Spans (by label)": int(vtraces_span_count),
        "Spans Dropped": int(max_dropped),
        "Drop Rate %": drop_rate,
    })
    
    if traces_data:
        tables["traces"] = pd.DataFrame(traces_data)
    
    # Storage sizes
    storage = manifest.get("final_storage_bytes", {})
    if storage:
        storage_data = []
        for service, size_bytes in storage.items():
            storage_data.append({
                "Service": service,
                "Storage Size": format_bytes(size_bytes),
                "Size (bytes)": size_bytes,
            })
        if storage_data:
            tables["storage"] = pd.DataFrame(storage_data)
    
    # Ingest throughput - use maximum value from all observations
    ingest_data = []
    ingest_max = {}
    
    for obs in observations:
        for service_name, service_data in obs.get("services", {}).items():
            ingest_metrics = service_data.get("ingest_metrics", {})
            if not ingest_metrics:
                continue
            
            if service_name in ["prometheus", "victoriametrics"]:
                samples = ingest_metrics.get("samples_ingested", 0) or ingest_metrics.get("samples_appended", 0)
                if service_name not in ingest_max:
                    ingest_max[service_name] = {"type": "Samples", "ingested": 0, "dropped": 0}
                ingest_max[service_name]["ingested"] = max(ingest_max[service_name]["ingested"], samples)
                ingest_max[service_name]["dropped"] = max(ingest_max[service_name]["dropped"], ingest_metrics.get("samples_dropped", 0))
            elif service_name in ["loki", "victorialogs"]:
                lines = ingest_metrics.get("lines_ingested", 0) or ingest_metrics.get("rows_ingested", 0)
                if service_name not in ingest_max:
                    ingest_max[service_name] = {"type": "Lines", "ingested": 0, "dropped": 0}
                ingest_max[service_name]["ingested"] = max(ingest_max[service_name]["ingested"], lines)
                ingest_max[service_name]["dropped"] = max(ingest_max[service_name]["dropped"], ingest_metrics.get("lines_dropped", 0))
            elif service_name in ["tempo", "victoriatraces"]:
                spans = ingest_metrics.get("spans_received", 0)
                if service_name not in ingest_max:
                    ingest_max[service_name] = {"type": "Spans", "ingested": 0, "dropped": 0}
                ingest_max[service_name]["ingested"] = max(ingest_max[service_name]["ingested"], spans)
                ingest_max[service_name]["dropped"] = max(ingest_max[service_name]["dropped"], ingest_metrics.get("spans_dropped", 0))
    
    if ingest_max:
        ingest_rows = []
        for service, data in ingest_max.items():
            ingest_rows.append({
                "Service": service,
                "Type": data["type"],
                "Total Ingested": int(data["ingested"]),
                "Total Dropped": int(data["dropped"]),
            })
        tables["ingest"] = pd.DataFrame(ingest_rows)
    
    # CPU and Memory averages
    metrics = extract_metrics(observations)
    resource_data = []
    for service_name, data in metrics.items():
        if data["cpu"]:
            resource_data.append({
                "Service": service_name,
                "Avg CPU %": round(statistics.mean(data["cpu"]), 2),
                "Max CPU %": round(max(data["cpu"]), 2),
                "Avg Memory (MB)": round(statistics.mean(data["memory"]), 2),
                "Max Memory (MB)": round(max(data["memory"]), 2),
            })
    
    if resource_data:
        tables["resources"] = pd.DataFrame(resource_data)
    
    return tables

def generate_markdown_report(manifest, observations, charts, tables):
    """Generate Markdown report."""
    run_id = manifest["run_id"]
    timestamp = manifest["timestamp"]
    scenarios = manifest["scenarios"]
    
    md = f"""# Performance Benchmark Report

**Run ID**: {run_id}  
**Timestamp**: {timestamp}  
**Scenarios**: {', '.join(scenarios)}  
**Platform**: {manifest.get('host_info', {}).get('platform', 'Unknown')}  
**RAM**: {manifest.get('host_info', {}).get('ram_gb', 'Unknown')} GB

## Executive Summary

This report compares the performance of observability backends:
- **Metrics**: Prometheus vs VictoriaMetrics
- **Logs**: Loki vs VictoriaLogs  
- **Traces**: Tempo vs VictoriaTraces

## Methods

### Hardware
- Platform: macOS Apple Silicon (ARM64)
- RAM: 16GB
- All services run in Docker containers with resource limits

### Test Scenarios
1. **Steady Load**: Baseline sustained load
2. **Burst**: 5× load spike for 60s
3. **High-Cardinality**: Label/attribute explosion

### Metrics Collected
- Ingest throughput (events/sec, series/sec, spans/sec)
- Query latency (p50/p90/p99)
- CPU and Memory usage
- Error rates
- Storage size

## Load Generation Details

### Metrics Load Generation

**How Metrics Are Generated:**

The metrics load generator creates a Prometheus-compatible metrics endpoint that exposes time series with configurable cardinality and churn:

- **Base Configuration:**
  - **Total Series**: 200,000 unique time series (configurable via `SERIES_TOTAL`)
  - **Scrape Interval**: 10 seconds (Prometheus scrapes every 10s)
  - **Churn Rate**: 2% per update cycle (series are added/removed dynamically)
  - **Metric Types**: Counter, Gauge, and Histogram for each series

- **Label Structure:**
  - Base labels: `job`, `instance`, `benchmark_app`, `benchmark_run_id`
  - High-cardinality mode adds: `host`, `pod`, `region`, `version`, `customer`, `env`, `team`
  - Labels use underscores (no dots) for Prometheus compatibility

- **Metric Updates:**
  - Metrics are updated continuously in a background thread
  - Each series gets new values every scrape interval
  - Counters increment by random values (0.1-10.0 × rate multiplier)
  - Gauges are set to random values (0-100)
  - Histograms observe random values (0-1000)

- **Dynamic Control:**
  - Rate multiplier: Adjusts update frequency (1.0 = normal, 5.0 = 5× burst)
  - Series count: Dynamically adds/removes series based on churn rate
  - High cardinality: Enables additional labels for cardinality testing

**What We Expect to Test:**

1. **Ingestion Performance**: How many samples/sec can each backend ingest?
   - Prometheus: Direct scrape from metrics endpoint
   - VictoriaMetrics: Via vmagent remote_write (Prometheus → vmagent → VictoriaMetrics)

2. **Storage Efficiency**: How much disk space for the same data?
   - Both backends store the same time series data
   - Comparison shows compression and storage efficiency

3. **Query Performance**: How fast are queries with different cardinality?
   - Steady load: 200k series with base labels
   - High cardinality: 200k series with 8+ labels per series
   - Queries: `rate()` and `topk()` operations

4. **Scalability**: How do backends handle:
   - Series churn (2% addition/removal per cycle)
   - Burst loads (5× multiplier)
   - High cardinality (thousands of unique label combinations)

### Traces Load Generation

**How Traces Are Generated:**

The traces load generator uses OpenTelemetry SDK to create distributed traces and sends them via OTLP/HTTP to both Tempo and VictoriaTraces:

- **Base Configuration:**
  - **Target Rate**: 2,000 spans/second (configurable via `SPANS_PER_SEC`)
  - **Services**: 10 different service names
  - **Trace Depth**: 3 levels (parent → child → grandchild)
  - **Error Rate**: 5% of spans marked as errors
  - **Average Spans per Trace**: ~2.4 spans (due to depth and branching)

- **Trace Structure:**
  - Each trace starts with a root span from a random service
  - With DEPTH=3, traces have 1-3 levels of child spans
  - 70% chance of creating child spans at each level
  - 1-3 child spans per parent (random)
  - Total: ~833 traces/second = ~2,000 spans/second

- **Span Attributes:**
  - Standard: `service.name`, `http.method`, `http.route`, `http.status_code`
  - Benchmark labels: `benchmark_app`, `benchmark_run_id` (as resource attributes for Jaeger queryability)
  - High-cardinality mode adds: `host`, `pod`, `region`, `customer_id`, `user_id`, `session_id`, `request_id`, `version`, `env`, `team`

- **Export Configuration:**
  - **BatchSpanProcessor** with:
    - Queue size: 10,000 spans
    - Batch size: 1,000 spans
    - Flush interval: 50ms (frequent flushing to prevent drops)
    - Export timeout: 5 seconds
  - Separate exporters for Tempo and VictoriaTraces
  - OTLP/HTTP protocol to both backends

- **Dynamic Control:**
  - `spans_per_sec`: Adjust target generation rate
  - `burst_multiplier`: Temporary rate increase (e.g., 5× for burst scenario)
  - `error_rate`: Percentage of spans marked as errors
  - `high_cardinality`: Enables additional attributes

**What We Expect to Test:**

1. **Ingestion Throughput**: How many spans/second can each backend handle?
   - Tempo: Native trace storage via OTLP/HTTP
   - VictoriaTraces: Native trace storage via OTLP/HTTP
   - Both receive the same traces simultaneously

2. **Trace Completeness**: Are all generated traces queryable?
   - Query by `benchmark_app` and `benchmark_run_id` labels
   - Verify trace count matches generation rate
   - Verify span count matches expected (traces × ~2.4 spans/trace)

3. **Query Performance**: How fast are trace queries?
   - Search by service name
   - Search by tags/attributes
   - Retrieve full trace details
   - Count traces/spans by label

4. **Storage Efficiency**: How much disk space for traces?
   - Both backends store traces natively (not as metrics)
   - Comparison shows compression and indexing efficiency

5. **Error Handling**: How do backends handle:
   - High ingestion rates (2k spans/sec sustained)
   - Burst loads (5× multiplier = 10k spans/sec)
   - High cardinality attributes (many unique attribute combinations)
   - Batch export failures and retries

### Logs Load Generation

**How Logs Are Generated:**

The logs load generator emits structured JSON logs to stdout, which are then collected by:
- **Loki**: Promtail tails container stdout and forwards to Loki
- **VictoriaLogs**: Custom forwarder tails container stdout and forwards to VictoriaLogs

- **Base Configuration:**
  - **Target Rate**: 5,000 lines/second (configurable via `LOG_RATE`)
  - **JSON Fields**: 8 additional fields per log entry
  - **Burst Factor**: 5× multiplier for burst scenarios

- **Log Structure:**
  - Standard fields: `timestamp`, `severity`, `service`, `trace_id`, `span_id`, `msg`, `method`, `path`, `status_code`, `duration_ms`
  - Benchmark labels: `benchmark_app`, `benchmark_run_id` (in JSON for queryability)
  - High-cardinality mode adds: `host`, `pod`, `region`, `datacenter`, `rack`, `customer_id`, `user_id`, `session_id`, `request_id`, `version`, `build`, `env`, `team`

- **Ingestion Path:**
  - Logs written to stdout as JSON lines
  - Promtail (for Loki) and custom forwarder (for VictoriaLogs) both read the same stdout stream
  - Ensures both backends receive identical log data

**What We Expect to Test:**

1. **Ingestion Throughput**: How many log lines/second can each backend handle?
2. **Query Performance**: How fast are log queries with JSON field filtering?
3. **Storage Efficiency**: How much disk space for the same log data?

## Results

### Load Volume

"""
    if "load_volume" in tables:
        md += tabulate(tables["load_volume"], headers="keys", tablefmt="pipe", showindex=False)
        md += "\n\n"

    md += """### Metrics Backend Comparison

"""
    
    if "metrics" in tables:
        md += tabulate(tables["metrics"], headers="keys", tablefmt="pipe", showindex=False)
        md += "\n\n"
    
    md += "### Logs Backend Comparison\n\n"
    if "logs" in tables:
        md += tabulate(tables["logs"], headers="keys", tablefmt="pipe", showindex=False)
        md += "\n\n"
    
    md += "### Traces Backend Comparison\n\n"
    if "traces" in tables:
        md += tabulate(tables["traces"], headers="keys", tablefmt="pipe", showindex=False)
        md += "\n\n"
    
    md += "### Ingest Throughput\n\n"
    if "ingest" in tables:
        md += tabulate(tables["ingest"], headers="keys", tablefmt="pipe", showindex=False)
        md += "\n\n"
    
    md += "### Storage Size\n\n"
    if "storage" in tables:
        # Show formatted size
        storage_display = tables["storage"][["Service", "Storage Size"]].copy()
        md += tabulate(storage_display, headers="keys", tablefmt="pipe", showindex=False)
        md += "\n\n"
    
    md += "### Resource Usage\n\n"
    if "resources" in tables:
        md += tabulate(tables["resources"], headers="keys", tablefmt="pipe", showindex=False)
        md += "\n\n"
    
    md += "## Charts\n\n"
    for chart in charts:
        md += f"![{chart}](charts/{chart})\n\n"
    
    md += """## Notes

- All tests use single-node configurations
- Results are indicative and specific to the test environment
- Query sets are documented in the observations
- VictoriaMetrics single binary handles metrics storage
- VictoriaTraces is a separate product for native trace storage (not converting traces to metrics)
- **VictoriaLogs**: Uses a custom forwarder (similar to Promtail) to stream logs from container stdout

## Caveats

- Results may vary based on system load and Docker Desktop resource allocation
- Query performance depends on data distribution and cardinality
- Some APIs may not be available in all products; fallbacks are used where noted
"""
    
    return md

def generate_html_report(md_content, charts, tables):
    """Generate HTML report from Markdown."""
    html_template = Template("""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Performance Benchmark Report</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            line-height: 1.6;
        }
        h1 { color: #2c3e50; }
        h2 { color: #34495e; border-bottom: 2px solid #ecf0f1; padding-bottom: 10px; }
        table {
            border-collapse: collapse;
            width: 100%;
            margin: 20px 0;
        }
        th, td {
            border: 1px solid #ddd;
            padding: 12px;
            text-align: left;
        }
        th {
            background-color: #3498db;
            color: white;
        }
        tr:nth-child(even) {
            background-color: #f2f2f2;
        }
        img {
            max-width: 100%;
            height: auto;
            margin: 20px 0;
        }
        .chart-container {
            margin: 30px 0;
        }
        code {
            background-color: #f4f4f4;
            padding: 2px 6px;
            border-radius: 3px;
        }
    </style>
</head>
<body>
    {{ content | safe }}
</body>
</html>
""")
    
    # Convert markdown to HTML using markdown library
    html_content = markdown.markdown(md_content, extensions=['tables', 'fenced_code'])
    
    return html_template.render(content=html_content)

def main():
    """Main entry point."""
    run_dir = find_latest_run()
    if not run_dir:
        print("No test run found in artifacts directory")
        return
    
    print(f"Processing run: {run_dir.name}")
    
    manifest, observations = load_run_data(run_dir)
    
    # Extract metrics
    metrics = extract_metrics(observations)
    
    # Generate charts
    charts = generate_charts(metrics, manifest["run_id"])
    print(f"Generated {len(charts)} charts")
    
    # Generate tables
    tables = generate_summary_tables(manifest, observations)
    print(f"Generated {len(tables)} tables")
    
    # Generate Markdown report
    md_content = generate_markdown_report(manifest, observations, charts, tables)
    
    # Save Markdown
    with open(REPORTS_DIR / "report.md", "w") as f:
        f.write(md_content)
    print("Saved report.md")
    
    # Generate and save HTML
    html_content = generate_html_report(md_content, charts, tables)
    with open(REPORTS_DIR / "report.html", "w") as f:
        f.write(html_content)
    print("Saved report.html")
    
    print(f"\nReport generation complete!")
    print(f"  Markdown: {REPORTS_DIR / 'report.md'}")
    print(f"  HTML: {REPORTS_DIR / 'report.html'}")
    print(f"  Charts: {CHARTS_DIR}")

if __name__ == "__main__":
    main()

