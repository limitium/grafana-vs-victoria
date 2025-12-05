#!/bin/bash
# Smoke test script - runs short tests to validate setup

set -e

echo "=== Smoke Test ==="
echo "This will run 60-second tests for each scenario at low rates"

# Set low rates for smoke test
export SERIES_TOTAL=10000
export LOG_RATE=1000
export SPANS_PER_SEC=500
export DURATION_STEADY=60
export DURATION_BURST=60
export DURATION_CARD=60

echo "Starting services..."
make up

echo "Waiting for services to be healthy..."
sleep 30

echo "Running steady scenario..."
docker compose --project-name obs-bench run --rm orchestrator python /app/main.py run --scenario steady

echo "Running burst scenario..."
docker compose --project-name obs-bench run --rm orchestrator python /app/main.py run --scenario burst

echo "Running cardinality scenario..."
docker compose --project-name obs-bench run --rm orchestrator python /app/main.py run --scenario cardinality

echo "Generating report..."
make report

echo "=== Smoke test complete! ==="
echo "Check reports/report.md and reports/report.html"




