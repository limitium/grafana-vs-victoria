#!/usr/bin/env python3
"""
VictoriaLogs Forwarder

Reads logs from Docker containers using Docker API and forwards them to VictoriaLogs.
Similar to Promtail but specifically for VictoriaLogs /insert/jsonline endpoint.
"""
import os
import sys
import json
import time
import requests
import docker

VICTORIALOGS_URL = os.getenv("VICTORIALOGS_URL", "http://victorialogs:9428")
CONTAINER_NAME = os.getenv("CONTAINER_NAME", "logs_load")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "2500"))
BATCH_TIMEOUT = float(os.getenv("BATCH_TIMEOUT", "0.5"))

session = requests.Session()

def parse_log_line(line):
    """Parse a log line from Docker logs - Docker adds timestamp prefix."""
    line = line.strip()
    if not line:
        return None
    
    # Docker logs format: "2024-01-01T00:00:00.000000000Z stdout F {...json...}"
    # Or just the JSON part if already extracted
    # Try to find JSON in the line
    json_start = line.find('{')
    if json_start >= 0:
        json_part = line[json_start:]
        try:
            entry = json.loads(json_part)
            # Ensure _time field exists
            if "_time" not in entry:
                entry["_time"] = entry.get("timestamp", time.time())
            return entry
        except json.JSONDecodeError:
            pass
    
    # Not JSON - create a structured entry from plain text
    return {
        "_msg": line,
        "_time": time.time(),
    }

def send_batch(batch):
    """Send a batch of log entries to VictoriaLogs with retry logic."""
    if not batch:
        return True

    jsonl = "\n".join(json.dumps(entry) for entry in batch) + "\n"
    max_retries = 5
    backoff = 1.0

    for attempt in range(max_retries):
        try:
            resp = session.post(
                f"{VICTORIALOGS_URL}/insert/jsonline",
                data=jsonl,
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            if resp.status_code in (200, 204):
                return True
            else:
                print(f"Error sending batch (attempt {attempt+1}/{max_retries}): {resp.status_code} {resp.text}", file=sys.stderr)
        except Exception as e:
            print(f"Error sending batch (attempt {attempt+1}/{max_retries}): {e}", file=sys.stderr)

        time.sleep(backoff)
        backoff = min(backoff * 2, 10)

    print("Failed to send batch after retries; will retry later", file=sys.stderr)
    return False

def forwarder():
    """Main forwarder loop - reads Docker container logs and forwards to VictoriaLogs."""
    client = docker.from_env()
    
    try:
        # Find container by name pattern
        containers = client.containers.list(filters={"name": CONTAINER_NAME})
        if not containers:
            print(f"Container {CONTAINER_NAME} not found", file=sys.stderr)
            sys.exit(1)
        
        container = containers[0]
        print(f"Following logs from {container.name} -> {VICTORIALOGS_URL}", file=sys.stderr)
        
        batch = []
        last_send = time.time()
        
        # Stream logs from container - start from NOW (tail mode) to match Promtail behavior
        # This ensures we don't backfill old logs and get apples-to-apples comparison
        # Docker API expects Unix timestamp for 'since' parameter
        since_timestamp = int(time.time())
        
        for line in container.logs(stream=True, follow=True, since=since_timestamp):
            try:
                # Docker logs are bytes, decode
                log_line = line.decode('utf-8', errors='replace')
                entry = parse_log_line(log_line)
                if entry:
                    batch.append(entry)
                
                # Send batch if full or timeout
                now = time.time()
                if len(batch) >= BATCH_SIZE or (batch and now - last_send >= BATCH_TIMEOUT):
                    if send_batch(batch):
                        batch = []
                        last_send = now
            except Exception as e:
                print(f"Error processing line: {e}", file=sys.stderr)
        
        # Send remaining batch
        if batch:
            send_batch(batch)
    except KeyboardInterrupt:
        if batch:
            send_batch(batch)
    except Exception as e:
        print(f"Forwarder error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    forwarder()

