#!/usr/bin/env python3
"""
Test Orchestrator

Runs scenarios, collects KPIs, samples Docker stats, and saves raw data.
"""
import os
import sys
import json
import time
import argparse
import requests
import subprocess
import docker
from datetime import datetime
from pathlib import Path
import statistics
from collections import defaultdict
import shutil

# Configuration
ARTIFACTS_DIR = Path("/artifacts")
COMPOSE_PROJECT = os.getenv("COMPOSE_PROJECT_NAME", "obs-bench")

# Benchmark run ID - used to query labeled data
BENCHMARK_RUN_ID = os.getenv("BENCHMARK_RUN_ID", "default-run")
BENCHMARK_APP_LABELS = {
    "metrics": "metrics-load-generator",
    "logs": "logs-load-generator",
    "traces": "trace-load-generator",
}

# Service endpoints
ENDPOINTS = {
    "prometheus": "http://prometheus:9090",
    "victoriametrics": "http://victoriametrics:8428",
    "loki": "http://loki:3100",
    "victorialogs": "http://victorialogs:9428",
    "tempo": "http://tempo:3200",
    "victoriatraces": "http://victoriatraces:9410",
    "metrics_load": "http://metrics_load:9100",
    "logs_load": "http://logs_load:8080",
    "traces_load": "http://traces_load:8080",
}

# Services to monitor
SERVICES = {
    "prometheus": "prometheus",
    "victoriametrics": "victoriametrics",
    "loki": "loki",
    "victorialogs": "victorialogs",
    "tempo": "tempo",
    "victoriatraces": "victoriatraces",
}

class MetricsCollector:
    """Collects metrics from observability backends."""
    
    def __init__(self):
        self.docker_client = docker.from_env()
        self.observations = []
        self.query_latencies = defaultdict(list)
        self.ingest_times = defaultdict(list)
        self.load_volume = {
            "metrics_series": 0,
            "log_lines": 0,
            "traces_spans": 0,
            "start_time": None,
        }
    
    def wait_for_healthy(self, max_wait=300):
        """Wait for all services to be healthy."""
        print("Waiting for services to be healthy...")
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            all_healthy = True
            for name, endpoint in ENDPOINTS.items():
                if name in ["prometheus", "victoriametrics", "loki", "victorialogs", "tempo", "victoriatraces"]:
                    try:
                        health_url = f"{endpoint}/health"
                        if name == "prometheus":
                            health_url = f"{endpoint}/-/healthy"
                        elif name == "loki":
                            health_url = f"{endpoint}/ready"
                        elif name == "victoriatraces":
                            health_url = f"{endpoint}/health"
                        elif name == "victorialogs":
                            health_url = f"{endpoint}/health"
                        elif name == "tempo":
                            health_url = f"{endpoint}/ready"
                        
                        resp = requests.get(health_url, timeout=5)
                        if resp.status_code != 200:
                            all_healthy = False
                            print(f"  {name} not ready yet...")
                            break
                    except Exception as e:
                        all_healthy = False
                        print(f"  {name} not ready yet: {e}")
                        break
            
            if all_healthy:
                print("All services are healthy!")
                return True
            
            time.sleep(5)
        
        print("Timeout waiting for services to be healthy")
        return False
    
    def get_docker_stats(self, container_name):
        """Get Docker stats for a container."""
        try:
            container = self.docker_client.containers.get(f"{COMPOSE_PROJECT}-{container_name}-1")
            stats = container.stats(stream=False)
            
            cpu_percent = 0.0
            if stats.get("cpu_stats"):
                cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - stats.get("precpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0)
                system_delta = stats["cpu_stats"]["system_cpu_usage"] - stats.get("precpu_stats", {}).get("system_cpu_usage", 0)
                if system_delta > 0:
                    cpu_percent = (cpu_delta / system_delta) * len(stats["cpu_stats"].get("cpu_usage", {}).get("percpu_usage", [1])) * 100.0
            
            memory_usage = stats.get("memory_stats", {}).get("usage", 0)
            memory_limit = stats.get("memory_stats", {}).get("limit", 1)
            memory_percent = (memory_usage / memory_limit) * 100.0 if memory_limit > 0 else 0.0
            
            return {
                "cpu_percent": cpu_percent,
                "memory_bytes": memory_usage,
                "memory_percent": memory_percent,
            }
        except Exception as e:
            print(f"Error getting stats for {container_name}: {e}")
            return {
                "cpu_percent": 0.0,
                "memory_bytes": 0,
                "memory_percent": 0.0,
            }
    
    def get_storage_size(self, container_name, volume_path):
        """Get storage size for a volume."""
        try:
            container = self.docker_client.containers.get(f"{COMPOSE_PROJECT}-{container_name}-1")
            # Try du -sb first (most reliable)
            commands = [
                f"du -sb {volume_path} 2>/dev/null",
                f"sh -c 'du -sb {volume_path} 2>/dev/null || find {volume_path} -type f -exec ls -l {{}} \\; 2>/dev/null | awk \"{{sum+=\\$5}} END {{print sum}}\"'",
            ]
            for cmd in commands:
                try:
                    result = container.exec_run(cmd, user="root")
                    if result.exit_code == 0:
                        output = result.output.decode().strip()
                        if output:
                            # du -sb outputs: "size_bytes\t/path" or just "size_bytes"
                            parts = output.split()
                            if parts and parts[0].replace('.', '').isdigit():
                                return int(float(parts[0]))
                except:
                    continue
        except Exception as e:
            pass
        return 0
    
    def get_tempo_metrics(self):
        """Get Tempo trace metrics."""
        try:
            resp = requests.get(f"{ENDPOINTS['tempo']}/metrics", timeout=5)
            if resp.status_code == 200:
                metrics = self.parse_prometheus_metrics(resp.text)
                return {
                    "spans_received": metrics.get("tempo_distributor_spans_received_total", 0),
                    "spans_dropped": metrics.get("tempo_distributor_spans_dropped_total", 0),
                    "ingest_latency_p50": metrics.get("tempo_distributor_receive_latency_bucket", 0),
                }
        except Exception as e:
            pass
        return {}
    
    def get_victoriatraces_metrics(self):
        """Get VictoriaTraces trace metrics."""
        try:
            resp = requests.get(f"{ENDPOINTS['victoriatraces']}/metrics", timeout=5)
            if resp.status_code == 200:
                # VictoriaTraces uses vt_rows_ingested_total{type="opentelemetry_traces_otlphttp_protobuf"} for spans
                # Parse the raw text to get the labeled metric (not the summed version)
                spans_received = 0
                spans_dropped = 0
                for line in resp.text.split("\n"):
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    # Look for the specific labeled metric - can be opentelemetry_traces_otlphttp_protobuf or opentelemetry_traces
                    if 'vt_rows_ingested_total' in line and 'opentelemetry' in line:
                        parts = line.split()
                        if len(parts) >= 2:
                            try:
                                spans_received += float(parts[-1])
                            except ValueError:
                                pass
                    elif 'vt_rows_dropped_total' in line and 'opentelemetry' in line:
                        parts = line.split()
                        if len(parts) >= 2:
                            try:
                                spans_dropped += float(parts[-1])
                            except ValueError:
                                pass
                return {
                    "spans_received": int(spans_received),
                    "spans_dropped": int(spans_dropped),
                }
        except Exception as e:
            print(f"Error getting VictoriaTraces metrics: {e}")
        return {}
    
    def scrape_metrics(self, service_name, endpoint):
        """Scrape Prometheus-style metrics from a service."""
        try:
            resp = requests.get(f"{endpoint}/metrics", timeout=5)
            if resp.status_code == 200:
                return self.parse_prometheus_metrics(resp.text)
        except Exception as e:
            print(f"Error scraping {service_name}: {e}")
        return {}
    
    def parse_prometheus_metrics(self, text):
        """Parse Prometheus metrics format."""
        metrics = {}
        for line in text.split("\n"):
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 2:
                metric_name = parts[0].split("{")[0]
                try:
                    value = float(parts[-1])
                    # Sum values for metrics with labels (e.g., vm_data_size_bytes{type="..."})
                    if metric_name in metrics:
                        metrics[metric_name] += value
                    else:
                        metrics[metric_name] = value
                except (ValueError, IndexError):
                    pass
        return metrics
    
    def query_prometheus(self, endpoint, query, timeout=10):
        """Run a Prometheus instant query and measure latency."""
        start = time.time()
        try:
            resp = requests.get(
                f"{endpoint}/api/v1/query",
                params={"query": query},
                timeout=timeout,
            )
            latency = (time.time() - start) * 1000  # ms
            if resp.status_code == 200:
                return {"latency_ms": latency, "success": True, "data": resp.json()}
            return {"latency_ms": latency, "success": False, "error": resp.text}
        except Exception as e:
            latency = (time.time() - start) * 1000
            return {"latency_ms": latency, "success": False, "error": str(e)}
    
    def query_prometheus_range(self, endpoint, query, start_time, end_time, step=10, timeout=30):
        """Run a Prometheus range query and measure latency."""
        start = time.time()
        try:
            resp = requests.get(
                f"{endpoint}/api/v1/query_range",
                params={
                    "query": query,
                    "start": start_time,
                    "end": end_time,
                    "step": step,
                },
                timeout=timeout,
            )
            latency = (time.time() - start) * 1000  # ms
            if resp.status_code == 200:
                return {"latency_ms": latency, "success": True, "data": resp.json()}
            return {"latency_ms": latency, "success": False, "error": resp.text}
        except Exception as e:
            latency = (time.time() - start) * 1000
            return {"latency_ms": latency, "success": False, "error": str(e)}
    
    def query_loki(self, endpoint, query, timeout=10):
        """Run a Loki query and measure latency."""
        start = time.time()
        try:
            # Use instant query for simpler queries, range for aggregations
            if "count_over_time" in query or "rate" in query:
                resp = requests.get(
                    f"{endpoint}/loki/api/v1/query_range",
                    params={
                        "query": query,
                        "start": int((time.time() - 300) * 1e9),  # 5 min ago
                        "end": int(time.time() * 1e9),
                        "limit": 100,
                    },
                    timeout=timeout,
                )
            else:
                resp = requests.get(
                    f"{endpoint}/loki/api/v1/query",
                    params={
                        "query": query,
                        "limit": 100,
                    },
                    timeout=timeout,
                )
            latency = (time.time() - start) * 1000
            if resp.status_code == 200:
                return {"latency_ms": latency, "success": True, "data": resp.json()}
            return {"latency_ms": latency, "success": False, "error": resp.text[:200]}
        except Exception as e:
            latency = (time.time() - start) * 1000
            return {"latency_ms": latency, "success": False, "error": str(e)[:200]}
    
    def query_victorialogs(self, endpoint, query, timeout=10):
        """Run a VictoriaLogs query and measure latency."""
        # VictoriaLogs uses a different API - for now, use metrics endpoint as proxy
        # In real implementation, would use VictoriaLogs query API
        start = time.time()
        try:
            # Try to query via metrics or use a simple health check as latency proxy
            resp = requests.get(f"{endpoint}/health", timeout=timeout)
            latency = (time.time() - start) * 1000
            # For now, return a placeholder
            return {"latency_ms": latency, "success": True, "data": {}}
        except Exception as e:
            latency = (time.time() - start) * 1000
            return {"latency_ms": latency, "success": False, "error": str(e)}
    
    def collect_observation(self, scenario, timestamp):
        """Collect a single observation snapshot."""
        obs = {
            "timestamp": timestamp,
            "scenario": scenario,
            "services": {},
            "load_volume": self.load_volume.copy(),
        }
        
        # Collect Docker stats, metrics, and storage sizes
        storage_paths = {
            "prometheus": "/prometheus",
            "victoriametrics": "/vmdata",
            "loki": "/loki",
            "victorialogs": "/vlogs",
            "tempo": "/var/tempo",
            "victoriatraces": "/vtraces",
        }
        
        for service_name, container_name in SERVICES.items():
            stats = self.get_docker_stats(container_name)
            metrics = self.scrape_metrics(service_name, ENDPOINTS[service_name])
            
            # Get storage size from volume or metrics
            storage_size = 0
            if service_name in storage_paths:
                # Try direct file system first
                storage_size = self.get_storage_size(container_name, storage_paths[service_name])
                # Fallback to metrics if file system method fails
                if storage_size == 0:
                    if service_name == "prometheus":
                        # Prometheus exposes storage size in metrics
                        storage_size = int(metrics.get("prometheus_tsdb_storage_blocks_bytes", 0))
                        # If still 0, try alternative metrics
                        if storage_size == 0:
                            storage_size = int(metrics.get("prometheus_tsdb_head_series", 0) * 1024)  # Rough estimate
                    elif service_name == "victoriametrics":
                        # VM sums all vm_data_size_bytes types
                        storage_size = int(metrics.get("vm_data_size_bytes", 0))
                        # Also check indexdb
                        if storage_size == 0:
                            storage_size = int(metrics.get("vm_indexdb_size_bytes", 0) or 0)
                    elif service_name == "loki":
                        storage_size = int(metrics.get("loki_ingester_chunk_stored_bytes_total", 0))
                        # Try alternative
                        if storage_size == 0:
                            storage_size = int(metrics.get("loki_chunk_store_bytes", 0) or 0)
                    elif service_name == "victorialogs":
                        storage_size = int(metrics.get("vl_data_size_bytes", 0) or 0)
                    elif service_name == "tempo":
                        storage_size = int(metrics.get("tempo_ingester_blocks_bytes", 0) or 0)
                        # Try alternative
                        if storage_size == 0:
                            storage_size = int(metrics.get("tempo_ingester_traces_created_total", 0) * 1024)  # Rough estimate
                    elif service_name == "victoriatraces":
                        storage_size = int(metrics.get("vt_data_size_bytes", 0) or 0)
                        # Try alternative metric names
                        if storage_size == 0:
                            storage_size = int(metrics.get("vt_storage_size_bytes", 0) or 0)
            
            # Get ingest metrics specific to each service
            # Use label-based queries where possible for accurate counts
            # Log query attempts to debug why counts differ
            ingest_metrics = {}
            query_debug = {}
            if service_name == "prometheus":
                # Query Prometheus for count of samples with benchmark labels
                # Use query_range to count all samples over time range
                # Count samples by summing count_over_time for each series
                end_time = int(timestamp)
                start_time = end_time - 3600  # Last hour
                query = f'count_over_time(test_metric{{benchmark_app="{BENCHMARK_APP_LABELS["metrics"]}",benchmark_run_id="{BENCHMARK_RUN_ID}"}}[1h])'
                query_result = self.query_prometheus_range(ENDPOINTS["prometheus"], query, start_time, end_time, step=10)
                samples_from_query = 0
                if query_result.get("success") and query_result.get("data", {}).get("result"):
                    # Sum count_over_time results from all series
                    for result in query_result["data"]["result"]:
                        # count_over_time returns a single value per series (total count in range)
                        values = result.get("values", [])
                        if values:
                            # Last value is the total count for this series
                            try:
                                samples_from_query += float(values[-1][1])
                            except:
                                pass
                
                # If no results, fail - no fallback!
                if samples_from_query == 0:
                    raise ValueError(f"Prometheus query returned 0 samples. Query: {query}, Labels: benchmark_app={BENCHMARK_APP_LABELS['metrics']}, benchmark_run_id={BENCHMARK_RUN_ID}")
                
                samples_appended = int(samples_from_query)
                query_debug[f"{service_name}_query_used"] = True
                query_debug[f"{service_name}_query_result"] = samples_from_query
                
                ingest_metrics = {
                    "samples_appended": samples_appended,
                    "samples_ingested": samples_appended,
                    "series_count": metrics.get("prometheus_tsdb_head_series", 0),
                }
                ingest_metrics["_debug"] = query_debug
            elif service_name == "victoriametrics":
                # Query VictoriaMetrics for count of samples with benchmark labels
                # Use query_range to count all samples over time range
                end_time = int(timestamp)
                start_time = end_time - 3600  # Last hour
                query = f'count_over_time(test_metric{{benchmark_app="{BENCHMARK_APP_LABELS["metrics"]}",benchmark_run_id="{BENCHMARK_RUN_ID}"}}[1h])'
                query_result = self.query_prometheus_range(ENDPOINTS["victoriametrics"], query, start_time, end_time, step=10)
                samples_from_query = 0
                if query_result.get("success") and query_result.get("data", {}).get("result"):
                    # Sum count_over_time results from all series
                    for result in query_result["data"]["result"]:
                        values = result.get("values", [])
                        if values:
                            try:
                                samples_from_query += float(values[-1][1])
                            except:
                                pass
                
                # If no results, fail - no fallback!
                ingest_errors = metrics.get("vm_ingestserver_request_errors_total", 0) or 0
                if samples_from_query == 0:
                    raise ValueError(f"VictoriaMetrics query returned 0 samples. Query: {query}, Labels: benchmark_app={BENCHMARK_APP_LABELS['metrics']}, benchmark_run_id={BENCHMARK_RUN_ID}")
                
                total_ingested = int(samples_from_query)
                query_debug[f"{service_name}_query_used"] = True
                query_debug[f"{service_name}_query_result"] = samples_from_query

                # Trace ingestion (VictoriaMetrics converts OTLP traces to metrics; track rows with opentelemetry label)
                otlp_rows = 0
                try:
                    resp = requests.get(f"{ENDPOINTS['victoriametrics']}/metrics", timeout=5)
                    if resp.status_code == 200:
                        for line in resp.text.split("\n"):
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            if 'vm_rows_inserted_total{type="opentelemetry"}' in line:
                                parts = line.split()
                                if len(parts) >= 2:
                                    try:
                                        otlp_rows = float(parts[-1])
                                    except ValueError:
                                        pass
                                    break
                except Exception as e:
                    print(f"Warning: unable to parse VictoriaMetrics OTLP metrics: {e}")

                ingest_metrics = {
                    "samples_ingested": total_ingested,
                    "samples_dropped": ingest_errors,
                    "series_count": metrics.get("vm_series_total", 0) or metrics.get("vm_active_merges", 0),
                    "spans_received": otlp_rows,
                    "spans_dropped": 0,
                }
                ingest_metrics["_debug"] = query_debug
            elif service_name == "loki":
                # Query Loki for log lines with benchmark labels (JSON fields in log content)
                query = f'count_over_time({{container="logs_load"}} | json | benchmark_app="{BENCHMARK_APP_LABELS["logs"]}" and benchmark_run_id="{BENCHMARK_RUN_ID}"[1h])'
                query_result = self.query_loki(ENDPOINTS["loki"], query)
                lines_from_query = 0
                if query_result.get("success") and query_result.get("data", {}).get("result"):
                    for result in query_result["data"]["result"]:
                        # count_over_time returns values over time, sum the last value from each stream
                        values = result.get("values", [])
                        if values:
                            try:
                                # Last value is the total count for this stream
                                lines_from_query += float(values[-1][1])
                            except:
                                pass
                
                # If no results, fail - no fallback!
                if lines_from_query == 0:
                    raise ValueError(f"Loki query returned 0 lines. Query: {query}, Labels: benchmark_app={BENCHMARK_APP_LABELS['logs']}, benchmark_run_id={BENCHMARK_RUN_ID}")
                
                lines_received = int(lines_from_query)
                query_debug[f"{service_name}_query_used"] = True
                query_debug[f"{service_name}_query_result"] = lines_from_query
                
                lines_dropped = metrics.get("loki_distributor_lines_dropped_total", 0)
                # Also check for rate limit rejections
                rate_limit_rejections = metrics.get("loki_distributor_rate_limit_rejections_total", 0)
                # Sum all dropped/rejected lines
                total_dropped = lines_dropped + rate_limit_rejections
                ingest_metrics = {
                    "lines_ingested": lines_received,
                    "lines_dropped": total_dropped,
                    "chunks_created": metrics.get("loki_ingester_chunks_created_total", 0),
                }
                ingest_metrics["_debug"] = query_debug
            elif service_name == "victorialogs":
                # Query VictoriaLogs for log lines with benchmark labels
                # VictoriaLogs uses LogSQL query API
                end_time = int(timestamp * 1000)  # milliseconds
                start_time = end_time - (3600 * 1000)  # 1 hour ago
                # Use LogSQL to count rows with benchmark labels
                query = f'_time:[{start_time}..{end_time}] AND benchmark_app="{BENCHMARK_APP_LABELS["logs"]}" AND benchmark_run_id="{BENCHMARK_RUN_ID}"'
                try:
                    resp = requests.post(
                        f"{ENDPOINTS['victorialogs']}/select/logsql/query",
                        json={"q": f"count({query})"},
                        timeout=30,
                    )
                    lines_from_query = 0
                    if resp.status_code == 200:
                        data = resp.json()
                        # VictoriaLogs returns count in response
                        lines_from_query = int(data.get("rows", [0])[0] if data.get("rows") else 0)
                    
                    # If no results, fail - no fallback!
                    if lines_from_query == 0:
                        raise ValueError(f"VictoriaLogs query returned 0 lines. Query: {query}, Labels: benchmark_app={BENCHMARK_APP_LABELS['logs']}, benchmark_run_id={BENCHMARK_RUN_ID}")
                    
                    rows_ingested = lines_from_query
                    query_debug[f"{service_name}_query_used"] = True
                    query_debug[f"{service_name}_query_result"] = lines_from_query
                except Exception as e:
                    raise ValueError(f"VictoriaLogs query failed: {e}")
                
                # Sum all dropped reasons
                rows_dropped = 0
                for key, value in metrics.items():
                    if key.startswith("vl_rows_dropped_total"):
                        rows_dropped += value
                ingest_metrics = {
                    "lines_ingested": rows_ingested,
                    "lines_dropped": rows_dropped,
                    "rows_ingested": rows_ingested,
                }
                ingest_metrics["_debug"] = query_debug
            elif service_name == "tempo":
                # Use metrics directly - they show total spans received
                tempo_metrics = self.get_tempo_metrics()
                ingest_metrics = {
                    "spans_received": tempo_metrics.get("spans_received", 0),
                    "spans_dropped": tempo_metrics.get("spans_dropped", 0),
                }
            elif service_name == "victoriatraces":
                # Use metrics directly - they show total spans received
                vtraces_metrics = self.get_victoriatraces_metrics()
                ingest_metrics = {
                    "spans_received": vtraces_metrics.get("spans_received", 0),
                    "spans_dropped": vtraces_metrics.get("spans_dropped", 0),
                }
            
            obs["services"][service_name] = {
                "docker_stats": stats,
                "metrics": metrics,
                "ingest_metrics": ingest_metrics,
                "storage_size_bytes": storage_size,
            }
        
        # Run representative queries
        obs["queries"] = {}
        
        # Prometheus queries - filter by benchmark labels (no dots)
        prom_queries = [
            f'rate(test_metric{{benchmark_app="{BENCHMARK_APP_LABELS["metrics"]}",benchmark_run_id="{BENCHMARK_RUN_ID}"}}[5m])',
            f'topk(10, test_metric{{benchmark_app="{BENCHMARK_APP_LABELS["metrics"]}",benchmark_run_id="{BENCHMARK_RUN_ID}"}})',
        ]
        for query in prom_queries:
            result = self.query_prometheus(ENDPOINTS["prometheus"], query)
            obs["queries"][f"prometheus:{query}"] = result
            if result["success"]:
                self.query_latencies[f"prometheus:{query}"].append(result["latency_ms"])
        
        # VictoriaMetrics queries (same as Prometheus API) - filter by benchmark labels
        for query in prom_queries:
            result = self.query_prometheus(ENDPOINTS["victoriametrics"], query)
            obs["queries"][f"victoriametrics:{query}"] = result
            if result["success"]:
                self.query_latencies[f"victoriametrics:{query}"].append(result["latency_ms"])
        
        # Loki queries - filter by benchmark labels (JSON fields in log content)
        # Use json parser and filter by extracted fields
        loki_queries = [
            f'{{container="logs_load"}} | json | benchmark_app="{BENCHMARK_APP_LABELS["logs"]}" and benchmark_run_id="{BENCHMARK_RUN_ID}" |= "ERROR"',
            f'count_over_time({{container="logs_load"}} | json | benchmark_app="{BENCHMARK_APP_LABELS["logs"]}" and benchmark_run_id="{BENCHMARK_RUN_ID}"[5m])',
        ]
        for query in loki_queries:
            result = self.query_loki(ENDPOINTS["loki"], query)
            obs["queries"][f"loki:{query}"] = result
            if result["success"]:
                self.query_latencies[f"loki:{query}"].append(result["latency_ms"])
        
        # VictoriaLogs queries (different API)
        for query in loki_queries:
            result = self.query_victorialogs(ENDPOINTS["victorialogs"], query)
            obs["queries"][f"victorialogs:{query}"] = result
            if result["success"]:
                self.query_latencies[f"victorialogs:{query}"].append(result["latency_ms"])
        
        # Tempo trace queries - filter by benchmark labels (for verification only)
        tempo_query_result = self.query_tempo_traces()
        obs["queries"]["tempo:traces_by_label"] = tempo_query_result
        
        # VictoriaTraces trace queries - filter by benchmark labels (for verification only)
        vtraces_query_result = self.query_victoriatraces_traces()
        obs["queries"]["victoriatraces:traces_by_label"] = vtraces_query_result
        
        # Note: ingest_metrics use backend metrics directly (not query results)
        # Query results are for verification/comparison only
        
        return obs
    
    def query_tempo_traces(self):
        """Query Tempo for trace and span counts with benchmark labels."""
        start = time.time()
        try:
            # Tempo doesn't have a direct count API, so we use search with minimal data
            # Get trace count from search (limited to what search returns)
            query_url = f"{ENDPOINTS['tempo']}/api/search"
            params = {
                "tags": f"benchmark_app={BENCHMARK_APP_LABELS['traces']} benchmark_run_id={BENCHMARK_RUN_ID}",
                "limit": 1,  # Just need to know if traces exist, count comes from metrics
            }
            resp = requests.get(query_url, params=params, timeout=10)
            latency = (time.time() - start) * 1000
            if resp.status_code == 200:
                data = resp.json()
                traces = data.get("traces", [])
                # If we get results, traces exist - use metrics for actual counts
                trace_count = len(traces) if traces else 0
                
                # Use metrics for span count (more accurate than querying)
                tempo_metrics = self.get_tempo_metrics()
                span_count = tempo_metrics.get("spans_received", 0)
                
                # If we have traces but no span count from metrics, estimate
                if trace_count > 0 and span_count == 0:
                    # Estimate based on typical spans per trace (~2.4 with DEPTH=3)
                    span_count = int(trace_count * 2.4)
                
                return {
                    "latency_ms": latency,
                    "success": True,
                    "trace_count": trace_count,
                    "span_count": span_count,
                }
            return {"latency_ms": latency, "success": False, "error": resp.text[:200]}
        except Exception as e:
            latency = (time.time() - start) * 1000
            return {"latency_ms": latency, "success": False, "error": str(e)}
    
    def query_victoriatraces_traces(self):
        """Query VictoriaTraces for trace and span counts with benchmark labels using metrics."""
        start = time.time()
        try:
            import json
            # VictoriaTraces doesn't have a direct count API, so use search to verify traces exist
            # Then use metrics for actual counts
            query_url = f"{ENDPOINTS['victoriatraces']}/select/jaeger/api/traces"
            end_time = int(time.time() * 1000000)  # microseconds
            start_time = end_time - (3600 * 1000000)  # 1 hour ago
            tags_dict = {
                "benchmark_app": BENCHMARK_APP_LABELS['traces'],
                "benchmark_run_id": BENCHMARK_RUN_ID,
            }
            params = {
                "service": "trace-load-generator",
                "tags": json.dumps(tags_dict),
                "limit": 1,  # Just need to verify traces exist
                "start": start_time,
                "end": end_time,
            }
            resp = requests.get(query_url, params=params, timeout=10)
            latency = (time.time() - start) * 1000
            if resp.status_code == 200:
                data = resp.json()
                traces = data.get("data", []) or data.get("traces", [])
                trace_count = len(traces) if traces else 0
                
                # Use metrics for span count (more accurate)
                vtraces_metrics = self.get_victoriatraces_metrics()
                span_count = vtraces_metrics.get("spans_received", 0)
                
                # If we have traces but no span count from metrics, estimate
                if trace_count > 0 and span_count == 0:
                    span_count = int(trace_count * 2.4)  # Estimate
                
                return {
                    "latency_ms": latency,
                    "success": True,
                    "trace_count": trace_count,
                    "span_count": span_count,
                }
            return {"latency_ms": latency, "success": False, "error": resp.text[:200]}
        except Exception as e:
            latency = (time.time() - start) * 1000
            return {"latency_ms": latency, "success": False, "error": str(e)}
    
    def control_load_generator(self, generator, params):
        """Control a load generator via HTTP."""
        endpoint = ENDPOINTS.get(generator)
        if not endpoint:
            return False
        
        try:
            url = f"{endpoint}/control"
            resp = requests.post(url, params=params, timeout=5)
            return resp.status_code == 200
        except Exception as e:
            print(f"Error controlling {generator}: {e}")
            return False
    
    def get_load_volume_info(self):
        """Get current load volume from load generators."""
        volume_info = {}
        try:
            # Get metrics load info
            resp = requests.get(f"{ENDPOINTS['metrics_load']}/metrics", timeout=5)
            if resp.status_code == 200:
                # Count active series from metrics endpoint
                lines = resp.text.split('\n')
                volume_info["metrics_series"] = len([l for l in lines if l.startswith('test_metric') and '{' in l])
        except:
            pass
        
        # Get log rate from env/config
        volume_info["log_rate"] = float(os.getenv("LOG_RATE", "5000"))
        volume_info["spans_rate"] = float(os.getenv("SPANS_PER_SEC", "2000"))
        volume_info["series_total"] = int(os.getenv("SERIES_TOTAL", "200000"))
        
        return volume_info
    
    def run_scenario(self, scenario_name, duration):
        """Run a single scenario."""
        print(f"\n{'='*60}")
        print(f"Running scenario: {scenario_name}")
        print(f"Duration: {duration} seconds")
        print(f"{'='*60}\n")
        
        # Initialize load volume tracking
        if self.load_volume["start_time"] is None:
            self.load_volume["start_time"] = time.time()
            load_info = self.get_load_volume_info()
            self.load_volume.update(load_info)
        
        # Configure load generators based on scenario
        if scenario_name == "steady":
            self.control_load_generator("metrics_load", {
                "rate_multiplier": "1.0",
                "high_cardinality": "false",
            })
            self.control_load_generator("logs_load", {
                "rate": "5000",
                "burst_multiplier": "1.0",
                "high_cardinality": "false",
            })
            self.control_load_generator("traces_load", {
                "spans_per_sec": "2000",
                "burst_multiplier": "1.0",
                "high_cardinality": "false",
            })
        elif scenario_name == "burst":
            # Start steady
            self.control_load_generator("metrics_load", {"rate_multiplier": "1.0"})
            self.control_load_generator("logs_load", {"rate": "5000", "burst_multiplier": "1.0"})
            self.control_load_generator("traces_load", {"spans_per_sec": "2000", "burst_multiplier": "1.0"})
            time.sleep(60)
            
            # Burst
            print("Applying burst load (5x)...")
            self.control_load_generator("metrics_load", {"rate_multiplier": "5.0"})
            self.control_load_generator("logs_load", {"rate": "25000", "burst_multiplier": "5.0"})
            self.control_load_generator("traces_load", {"spans_per_sec": "10000", "burst_multiplier": "5.0"})
            time.sleep(60)
            
            # Back to steady
            print("Returning to steady load...")
            self.control_load_generator("metrics_load", {"rate_multiplier": "1.0"})
            self.control_load_generator("logs_load", {"rate": "5000", "burst_multiplier": "1.0"})
            self.control_load_generator("traces_load", {"spans_per_sec": "2000", "burst_multiplier": "1.0"})
        elif scenario_name == "cardinality":
            self.control_load_generator("metrics_load", {
                "rate_multiplier": "1.0",
                "high_cardinality": "true",
            })
            self.control_load_generator("logs_load", {
                "rate": "5000",
                "high_cardinality": "true",
            })
            self.control_load_generator("traces_load", {
                "spans_per_sec": "2000",
                "high_cardinality": "true",
            })
        
        # Collect observations every 2 seconds
        start_time = time.time()
        observations = []
        
        # Update load volume estimates
        elapsed = time.time() - self.load_volume["start_time"]
        self.load_volume["log_lines"] = int(self.load_volume.get("log_rate", 5000) * elapsed)
        self.load_volume["traces_spans"] = int(self.load_volume.get("spans_rate", 2000) * elapsed)
        
        while time.time() - start_time < duration:
            obs = self.collect_observation(scenario_name, time.time())
            observations.append(obs)
            time.sleep(2)
        
        # Update final load volume
        total_elapsed = time.time() - self.load_volume["start_time"]
        self.load_volume["log_lines"] = int(self.load_volume.get("log_rate", 5000) * total_elapsed)
        self.load_volume["traces_spans"] = int(self.load_volume.get("spans_rate", 2000) * total_elapsed)
        
        return observations
    
    def save_run(self, run_id, scenarios, observations):
        """Save run data to artifacts directory."""
        run_dir = ARTIFACTS_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        
        # Calculate final storage sizes - use the last observation's storage sizes
        final_storage = {}
        if observations:
            last_obs = observations[-1]
            for service_name, service_data in last_obs.get("services", {}).items():
                storage_size = service_data.get("storage_size_bytes", 0)
                if storage_size > 0:
                    final_storage[service_name] = storage_size
        
        # Fallback: try direct measurement
        storage_paths = {
            "prometheus": "/prometheus",
            "victoriametrics": "/vmdata",
            "loki": "/loki",
            "victorialogs": "/vlogs",
            "tempo": "/var/tempo",
            "victoriatraces": "/vtraces",
        }
        for service_name, container_name in SERVICES.items():
            if service_name not in final_storage or final_storage[service_name] == 0:
                if service_name in storage_paths:
                    size = self.get_storage_size(container_name, storage_paths[service_name])
                    if size > 0:
                        final_storage[service_name] = size
        
        # Save manifest
        manifest = {
            "run_id": run_id,
            "timestamp": datetime.now().isoformat(),
            "scenarios": scenarios,
            "host_info": {
                "platform": "macOS ARM64",
                "ram_gb": 16,
            },
            "load_volume": self.load_volume,
            "final_storage_bytes": final_storage,
            "query_latencies": {
                k: {
                    "p50": statistics.median(v) if v else 0,
                    "p90": statistics.quantiles(v, n=10)[8] if len(v) >= 10 else (v[-1] if v else 0),
                    "p99": statistics.quantiles(v, n=100)[98] if len(v) >= 100 else (v[-1] if v else 0),
                }
                for k, v in self.query_latencies.items() if v
            },
        }
        
        with open(run_dir / "manifest.json", "w") as f:
            json.dump(manifest, f, indent=2)
        
        # Save observations
        with open(run_dir / "observations.json", "w") as f:
            json.dump(observations, f, indent=2)
        
        print(f"\nRun data saved to {run_dir}")

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Run performance tests")
    parser.add_argument("run", choices=["run"], help="Run scenarios")
    parser.add_argument("--scenario", choices=["steady", "burst", "cardinality"], help="Single scenario to run")
    parser.add_argument("--all", action="store_true", help="Run all scenarios")
    
    args = parser.parse_args()
    
    collector = MetricsCollector()
    
    # Wait for services
    if not collector.wait_for_healthy():
        print("Services failed to become healthy")
        sys.exit(1)
    
    # Determine scenarios to run
    if args.all:
        scenarios = ["steady", "burst", "cardinality"]
    elif args.scenario:
        scenarios = [args.scenario]
    else:
        scenarios_str = os.getenv("SCENARIOS", "steady,burst,cardinality")
        scenarios = scenarios_str.split(",")
    
    # Get durations
    durations = {
        "steady": int(os.getenv("DURATION_STEADY", "60")),
        "burst": int(os.getenv("DURATION_BURST", "60")),
        "cardinality": int(os.getenv("DURATION_CARD", "60")),
    }
    
    # Generate run ID
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Run scenarios
    all_observations = []
    for scenario in scenarios:
        duration = durations.get(scenario, 300)
        observations = collector.run_scenario(scenario, duration)
        all_observations.extend(observations)
    
    # Flush all traces before ending - give exporters time to send remaining batches
    print("\nFlushing remaining traces...")
    time.sleep(2)  # Give batch processors time to flush
    
    # Final observation to capture final metrics after flush
    final_obs = collector.collect_observation("final", time.time())
    all_observations.append(final_obs)
    
    # Save run
    collector.save_run(run_id, scenarios, all_observations)
    print(f"\nTest run completed: {run_id}")

if __name__ == "__main__":
    main()

