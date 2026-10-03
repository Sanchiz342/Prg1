#!/usr/bin/env python3
"""Demo telemetry generator (stdlib only).

  python agent.py --url http://localhost:8000 --scenario backfill   # 90 min of history ending in an incident
  python agent.py --url http://localhost:8000 --scenario live        # real-time healthy telemetry
  python agent.py --url http://localhost:8000 --scenario loop        # backfill, then live forever (docker demo)

Scenario: deployment v1.8.2 -> DB connections climb -> latency up -> timeouts -> HTTP 500s.
"""
import argparse
import json
import random
import time
import urllib.request
from datetime import datetime, timedelta, timezone

rnd = random.Random()


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def post(url, key, path, body):
    req = urllib.request.Request(
        url + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "X-API-Key": key}
    )
    for attempt in range(30):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r)
        except Exception as exc:  # backend may still be starting
            if attempt == 29:
                raise
            print("waiting for backend:", exc)
            time.sleep(2)


def sample(ts, ramp):
    """One minute of telemetry. ramp: 0 = healthy ... 1 = full incident."""
    m = lambda svc, name, v: {"service": svc, "name": name, "value": round(v, 2), "ts": ts.isoformat()}  # noqa: E731
    metrics = [
        m("user-api", "cpu", 35 + rnd.uniform(-3, 3) + 20 * ramp),
        m("user-api", "ram", 50 + rnd.uniform(-2, 2)),
        m("user-api", "requests", 110 + rnd.uniform(-8, 8)),
        m("user-api", "latency_p95", 200 + rnd.uniform(-15, 15) + 1500 * ramp**2),
        m("user-api", "error_rate", max(0.0, 0.7 + rnd.uniform(-0.2, 0.2) + 24 * ramp**2)),
        m("user-api", "db_connections", 40 + rnd.uniform(-4, 4) + 56 * min(1, ramp * 1.6)),
        m("postgres", "cpu", 30 + rnd.uniform(-3, 3) + 15 * ramp),
        m("postgres", "disk", 62 + rnd.uniform(-0.5, 0.5)),
        m("redis", "ram", 28 + rnd.uniform(-1, 1)),
        m("email-worker", "cpu", 12 + rnd.uniform(-2, 2)),
    ]
    logs = [{"service": "user-api", "level": "INFO", "message": f"GET /users 200 {rnd.randint(20, 90)}ms", "ts": ts.isoformat()}]
    if ramp > 0.3:
        for _ in range(int(ramp * 8)):
            logs.append({"service": "user-api", "level": "ERROR", "ts": ts.isoformat(),
                         "message": f"DatabaseConnectionError: connection pool exhausted (pool={rnd.randint(95, 100)})"})
    if ramp > 0.6:
        logs.append({"service": "user-api", "level": "ERROR", "ts": ts.isoformat(), "message": "TimeoutError: request timed out after 30000ms"})
    if ramp > 0.3:
        logs.append({"service": "postgres", "level": "WARN", "ts": ts.isoformat(), "message": "too many connections for role app_user"})
    return metrics, logs


def backfill(url, key, minutes=90, incident_minutes=12):
    end = now_utc()
    deploy_ts = end - timedelta(minutes=incident_minutes)
    metrics, logs = [], []
    for i in range(minutes):
        ts = end - timedelta(minutes=minutes - i)
        ramp = max(0.0, (ts - deploy_ts).total_seconds() / 60) / incident_minutes
        mm, ll = sample(ts, ramp)
        metrics += mm
        logs += ll
    post(url, key, "/ingest/deployments", {"service": "user-api", "version": "v1.8.1", "notes": "stable release",
                                           "ts": (end - timedelta(minutes=minutes - 5)).isoformat()})
    post(url, key, "/ingest/deployments", {"service": "user-api", "version": "v1.8.2", "notes": "connection pool refactor",
                                           "ts": deploy_ts.isoformat()})
    for i in range(0, len(metrics), 1000):
        post(url, key, "/ingest/metrics", {"metrics": metrics[i:i + 1000]})
    for i in range(0, len(logs), 1000):
        post(url, key, "/ingest/logs", {"logs": logs[i:i + 1000]})
    print(f"backfilled {len(metrics)} metrics, {len(logs)} logs; deployment v1.8.2 at {deploy_ts:%H:%M:%S}")


def live(url, key, interval=10, recover=False):
    print("streaming live telemetry (Ctrl+C to stop)")
    while True:
        mm, ll = sample(now_utc(), 0.0)
        post(url, key, "/ingest/metrics", {"metrics": mm})
        post(url, key, "/ingest/logs", {"logs": ll})
        time.sleep(interval)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--key", default="dev-ingest-key")
    ap.add_argument("--scenario", choices=["backfill", "live", "loop"], default="backfill")
    a = ap.parse_args()
    if a.scenario in ("backfill", "loop"):
        backfill(a.url, a.key)
    if a.scenario in ("live", "loop"):
        live(a.url, a.key)
