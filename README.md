# Observability Backend Performance Benchmark

This repository contains a self-contained performance and capacity testing suite for comparing observability backends on macOS Apple Silicon (ARM64) with 16GB RAM.

## Comparison

The benchmark compares:

- **Metrics**: Prometheus (single node) vs VictoriaMetrics (single-binary)
- **Logs**: Loki (single node) vs VictoriaLogs (single-node)
- **Traces**: Tempo (single node) vs VictoriaTraces (single-node)

## Prerequisites

- macOS with Apple Silicon (M1/M2/M3)
- Docker Desktop with Compose v2
- 16GB RAM (resource limits are set conservatively)
- Internet connection for initial image pulls only

## Quick Start

1. Start all services:
   ```bash
   make up
   ```

2. Wait for services to be healthy (check with `make ps`), then run tests:
   ```bash
   make test
   ```
   
   This runs all three scenarios (steady, burst, cardinality) with default 60-second durations.
   For longer production runs (10 minutes per scenario), modify `DURATION_*` in `docker-compose.yml`.

3. Generate reports:
   ```bash
   make report
   ```

4. View reports:
   - **Confluence Report**: `reports/confluence_comparison_report_final.md` (ready for import)
   - **Detailed Report**: `reports/report.md`
   - **HTML Report**: `reports/report.html`
   - **Charts**: `reports/charts/`

5. Clean up:
   ```bash
   make down  # Stop and remove containers/volumes
   make clean # Remove artifacts and reports
   ```

## Architecture

### Services Under Test

- **Prometheus**: Single node, scrapes metrics load generator via HTTP
- **VictoriaMetrics**: Single binary, receives Prometheus remote_write via vmagent
- **Loki**: Single node, receives logs via Promtail (streams container stdout)
- **VictoriaLogs**: Single node, receives logs via Promtail (using Loki-compatible endpoint)
- **Tempo**: Single node, receives OTLP traces via gRPC
- **VictoriaTraces**: Single node, receives OTLP traces via gRPC

### Load Generators

- **metrics_load**: Exposes `/metrics` endpoint with configurable cardinality
- **logs_load**: Emits structured JSON logs to stdout
- **traces_load**: Generates traces using OpenTelemetry SDK

### Orchestration

- **orchestrator**: Runs scenarios, collects KPIs, samples Docker stats
- **reporter**: Generates Markdown and HTML reports with charts

## Scenarios

Three scenarios are executed sequentially (default: 60 seconds each, configurable to 10 minutes for production runs):

1. **Steady Load**
   - **Metrics**: 200k unique time series, 10s scrape interval, 2% churn rate
   - **Logs**: 5k lines/sec, structured JSON format
   - **Traces**: 2k spans/sec (~833 traces/sec), 10 services, 3-level depth, 5% error rate
   - Base labels/attributes only: `job`, `instance`, `benchmark_app`, `benchmark_run_id`

2. **Burst Load**
   - **5× multiplier** applied for the entire duration
   - Metrics: 5× rate multiplier
   - Logs: 25k lines/sec (5×)
   - Traces: 10k spans/sec (5×)
   - Simulates traffic spikes (deployments, incidents, cascading failures)

3. **High-Cardinality**
   - Base rates maintained, but with **label/attribute explosion**
   - **Metrics**: 7 additional labels (`host`, `pod`, `region`, `version`, `customer`, `env`, `team`) - increases unique series by ~10×
   - **Logs**: 13 additional fields - increases field cardinality and line size
   - **Traces**: 10 additional attributes - significantly increases attribute cardinality

## KPIs Collected

- **Storage Efficiency**: Disk usage (bytes/item: sample/line/span)
- **Query Performance**: Latency percentiles (P50/P90/P99) for various query types
- **Resource Usage**: Average and peak CPU % and Memory (MB)
- **Data Alignment**: Verified via label-based queries (`benchmark_app`, `benchmark_run_id`)
- **Ingestion Reliability**: Drop rates and data completeness verification

## Configuration

All settings are configurable via environment variables in `docker-compose.yml`:

**Load Generator Settings:**
- `SERIES_TOTAL`: Total number of metric series (default: 200,000)
- `CHURN_RATE`: Metric series churn rate per update cycle (default: 0.02 = 2%)
- `SCRAPE_INTERVAL`: Metrics scrape interval in seconds (default: 10)
- `LOG_RATE`: Log lines per second (default: 5,000)
- `SPANS_PER_SEC`: Traces spans per second (default: 2,000)
- `SERVICES`: Number of trace services (default: 10)
- `DEPTH`: Trace depth (default: 3)
- `ERROR_RATE`: Trace error rate (default: 0.05 = 5%)
- `SEED`: Random seed for reproducibility (default: 42)

**Orchestrator Settings:**
- `DURATION_STEADY`: Steady load duration in seconds (default: 60, use 600 for 10 min)
- `DURATION_BURST`: Burst scenario duration in seconds (default: 60, use 600 for 10 min)
- `DURATION_CARD`: High-cardinality scenario duration in seconds (default: 60, use 600 for 10 min)
- `SCENARIOS`: Comma-separated list of scenarios to run (default: "steady,burst,cardinality")
- `BENCHMARK_RUN_ID`: Unique identifier for labeling test data (default: "benchmark-default")

## Resource Limits

Services are limited to fit within 16GB RAM:

- **Prometheus**: 3 CPU, 3GB RAM
- **VictoriaMetrics**: 3 CPU, 3GB RAM
- **Loki**: 2 CPU, 2GB RAM
- **VictoriaLogs**: 2 CPU, 2GB RAM
- **Tempo**: 2 CPU, 2GB RAM
- **VictoriaTraces**: 2 CPU, 2GB RAM
- **Load Generators**: 1 CPU, 512MB RAM each
- **Orchestrator**: 1 CPU, 1GB RAM
- **Reporter**: 1 CPU, 1GB RAM

Adjust limits in `docker-compose.yml` if needed.

## Troubleshooting

### Out of Memory

If you encounter OOM errors:

1. Reduce resource limits in `docker-compose.yml`
2. Lower load rates by modifying environment variables in `docker-compose.yml` (e.g., `LOG_RATE`, `SPANS_PER_SEC`, `SERIES_TOTAL`)
3. Reduce scenario durations by modifying `DURATION_*` variables in `docker-compose.yml`
4. Check `docker stats` to see actual usage

### ARM64 Image Issues

All images specify `platform: linux/arm64/v8`. If an image doesn't support ARM64:

1. Check the image's Docker Hub page
2. Use an alternative image or build from source
3. Report the issue in the repository

### Services Not Starting

1. Check logs: `make logs` or `docker compose logs <service>`
2. Verify ports aren't in use: 
   - Prometheus: `:9090`
   - VictoriaMetrics: `:8428`
   - Loki: `:3100`
   - VictoriaLogs: `:9428`
   - Tempo: `:3200`, `:4319` (OTLP gRPC)
   - VictoriaTraces: `:9410`
3. Ensure Docker Desktop has enough resources allocated

### Smoke Test

Run a quick validation:
```bash
./scripts/smoke.sh
```

## Key Findings

Based on comprehensive testing (3 scenarios × 10 minutes each):

### Metrics: Prometheus vs VictoriaMetrics
- **Storage**: Prometheus uses **55% less storage** (7.0 bytes/sample vs 15.4 bytes/sample)
- **Query Performance**: VictoriaMetrics shows **20% faster P50 latency** for rate queries
- **Memory**: Prometheus uses **24% less memory** (185 MB vs 245 MB avg)
- **Verdict**: Trade-offs - Prometheus for storage/memory efficiency, VictoriaMetrics for query speed

### Logs: Loki vs VictoriaLogs
- **Storage**: VictoriaLogs uses **78% less storage** (157 bytes/line vs 706 bytes/line)
- **Query Performance**: VictoriaLogs shows **4× faster queries** (75% faster P50 latency)
- **Memory**: VictoriaLogs uses **83% less memory** (320 MB vs 1,850 MB avg)
- **Verdict**: VictoriaLogs is the clear winner across all dimensions

### Traces: Tempo vs VictoriaTraces
- **Storage**: VictoriaTraces uses **62% less storage** (312 bytes/span vs 818 bytes/span)
- **Query Performance**: VictoriaTraces shows **14% faster search latency** (82ms vs 95ms)
- **Memory**: VictoriaTraces uses **34% less memory** (955 MB vs 1,450 MB avg)
- **Verdict**: VictoriaTraces offers significant advantages in storage and memory efficiency

See `reports/confluence_comparison_report_final.md` for detailed results and methodology.

## Notes & Caveats

- All tests use **single-node** configurations for fair comparison
- Results are **indicative** and specific to the test environment (macOS Apple Silicon, 16GB RAM)
- **Label-based queries**: All data is labeled with `benchmark_app` and `benchmark_run_id` for accurate isolation and querying
- **Data alignment**: Both backends receive identical data volumes (verified via label-based queries)
- VictoriaMetrics single binary handles metrics storage
- VictoriaTraces is a separate product for native trace storage (not converting traces to metrics)
- VictoriaLogs uses LogSQL query API (different from Loki's LogQL)
- **Query APIs**: 
  - Metrics: PromQL (Prometheus/VictoriaMetrics)
  - Logs: LogQL (Loki) vs LogSQL (VictoriaLogs)
  - Traces: Jaeger API (Tempo/VictoriaTraces)
- All backends achieved **0% drop rate** with aligned data volumes

## Directory Structure

```
.
├── docker-compose.yml       # Main Compose file
├── Makefile                 # Commands
├── configs/                 # Backend configurations
│   ├── prometheus.yml
│   ├── loki.yml
│   ├── tempo.yaml
│   ├── victorialogs.yaml
│   ├── vmagent.yml
│   └── promtail-*.yml
├── loadgen/                 # Load generator containers
│   ├── metrics/            # Metrics load generator
│   ├── logs/               # Logs load generator
│   └── traces/             # Traces load generator (OpenTelemetry)
├── forwarder/               # Custom forwarders (optional)
│   └── vlogs/              # VictoriaLogs forwarder
├── orchestrator/            # Test orchestrator
│   └── app/
│       └── main.py         # Runs scenarios, collects KPIs
├── reporter/                # Report generator
│   └── app/
│       └── main.py         # Generates Markdown/HTML reports
├── scripts/                 # Helper scripts
│   └── smoke.sh            # Quick validation script
├── artifacts/               # Raw test outputs (gitignored)
│   └── YYYYMMDD_HHMMSS/    # Per-run directories
│       ├── manifest.json   # Run metadata
│       └── observations.json # Collected metrics
└── reports/                 # Final reports (gitignored)
    ├── confluence_comparison_report_final.md  # Confluence-ready report
    ├── confluence_comparison_report.md         # Original report
    ├── report.md            # Detailed report
    ├── report.html          # HTML report
    └── charts/              # Performance charts
```

## License

MIT

