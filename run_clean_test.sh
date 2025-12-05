#!/bin/bash
set -e

echo "=== Cleaning everything ==="
docker compose --project-name obs-bench down -v || true
docker system prune -f || true

echo "=== Starting services ==="
docker compose --project-name obs-bench up -d --build

echo "=== Waiting for services to be healthy ==="
sleep 20

echo "=== Running benchmark ==="
make test

echo "=== Generating report ==="
make report

echo "=== DONE ==="
echo "Report available at: reports/report.html"


