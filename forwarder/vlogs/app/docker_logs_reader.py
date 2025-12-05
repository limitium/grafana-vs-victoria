#!/usr/bin/env python3
"""
Docker Logs Reader

Reads logs from Docker containers using Docker API and forwards to VictoriaLogs.
This is a simpler alternative to Promtail for VictoriaLogs.
"""
import os
import sys
import json
import time
import requests
import docker
from datetime import datetime

VICTORIALOGS_URL = os.getenv("VICTORIALOGS_URL", "http://victorialogs:9428")
CONTAINER_NAME = os.getenv("CONTAINER_NAME", "logs_load")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))
BATCH_TIMEOUT = float(os.getenv("BATCH_TIMEOUT", "1.0"))

def parse_log_line(line):
    """Parse a log line from Docker logs."""
    line = line.strip()
    if not line:
        return None
    
    # Docker logs format: timestamp stream message
    # Try to parse as JSON first
    try:
        entry = json.loads(line)
        if "_time" not in entry:
            entry["_time"] = entry.get("timestamp", time.time())
        return entry
    except json.JSONDecodeError:
        # Plain text - create structured entry
        return {
            "_msg": line,
            "_time": time.time(),
        }

def send_batch(batch):
    """Send batch to VictoriaLogs."""
    if not batch:
        return
    
    jsonl = "\n".join(json.dumps(entry) for entry in batch) + "\n"
    
    try:
        resp = requests.post(
            f"{VICTORIALOGS_URL}/insert/jsonline",
            data=jsonl,
            headers={"Content-Type": "application/json"},
            timeout=5
        )
        if resp.status_code not in [200, 204]:
            print(f"Error: {resp.status_code}", file=sys.stderr)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)

def main():
    """Read Docker container logs and forward to VictoriaLogs."""
    client = docker.from_env()
    
    try:
        # Find container by name pattern
        containers = client.containers.list(filters={"name": CONTAINER_NAME})
        if not containers:
            print(f"Container {CONTAINER_NAME} not found", file=sys.stderr)
            sys.exit(1)
        
        container = containers[0]
        print(f"Following logs from {container.name}", file=sys.stderr)
        
        batch = []
        last_send = time.time()
        
        # Stream logs
        for line in container.logs(stream=True, follow=True):
            try:
                # Docker logs are bytes, decode
                log_line = line.decode('utf-8', errors='replace')
                entry = parse_log_line(log_line)
                if entry:
                    batch.append(entry)
                
                # Send batch if full or timeout
                now = time.time()
                if len(batch) >= BATCH_SIZE or (batch and now - last_send >= BATCH_TIMEOUT):
                    send_batch(batch)
                    batch = []
                    last_send = now
            except Exception as e:
                print(f"Error processing line: {e}", file=sys.stderr)
        
    except KeyboardInterrupt:
        if batch:
            send_batch(batch)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()




