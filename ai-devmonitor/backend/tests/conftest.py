import os
import random
from datetime import timedelta

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["BACKGROUND_DETECTION"] = "false"
os.environ["AI_PROVIDER"] = "mock"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import db as dbmod  # noqa: E402
from app.core.db import Base, utcnow  # noqa: E402
from app.main import app  # noqa: E402

KEY = {"X-API-Key": "dev-ingest-key"}


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(dbmod.engine)
    dbmod.init_db()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin(client):
    r = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def viewer(client):
    r = client.post("/auth/login", json={"username": "viewer", "password": "viewer"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def seed_incident_scenario(client, minutes=90, incident_minutes=12, seed=1):
    """Healthy history, then deploy v1.8.2 -> DB connections up -> latency up -> 500s."""
    rnd = random.Random(seed)
    now = utcnow()
    metrics, logs = [], []
    start = now - timedelta(minutes=minutes)
    deploy_ts = now - timedelta(minutes=incident_minutes)
    for i in range(minutes):
        ts = start + timedelta(minutes=i)
        since_deploy = (ts - deploy_ts).total_seconds() / 60  # <0 before deploy
        ramp = max(0.0, since_deploy) / incident_minutes  # 0..1
        vals = {
            "cpu": 35 + rnd.uniform(-3, 3) + 20 * ramp,
            "ram": 50 + rnd.uniform(-2, 2),
            "requests": 110 + rnd.uniform(-8, 8),
            "latency_p95": 200 + rnd.uniform(-15, 15) + 1500 * ramp**2,
            "error_rate": 0.7 + rnd.uniform(-0.2, 0.2) + 24 * ramp**2,
            "db_connections": 40 + rnd.uniform(-4, 4) + 56 * min(1, ramp * 1.6),
        }
        for name, v in vals.items():
            metrics.append({"service": "user-api", "name": name, "value": v, "ts": ts.isoformat()})
        metrics.append({"service": "postgres", "name": "cpu", "value": 30 + rnd.uniform(-3, 3), "ts": ts.isoformat()})
        logs.append({"service": "user-api", "level": "INFO", "message": f"GET /users 200 {rnd.randint(20, 90)}ms", "ts": ts.isoformat()})
        if ramp > 0.3:
            for _ in range(int(ramp * 8)):
                logs.append({"service": "user-api", "level": "ERROR",
                             "message": f"DatabaseConnectionError: connection pool exhausted (pool={rnd.randint(95, 100)}) user=bob@example.com",
                             "ts": ts.isoformat()})
        if ramp > 0.6:
            logs.append({"service": "user-api", "level": "ERROR", "message": "TimeoutError: request timed out after 30000ms", "ts": ts.isoformat()})
    client.post("/ingest/metrics", json={"metrics": metrics}, headers=KEY)
    client.post("/ingest/logs", json={"logs": logs}, headers=KEY)
    client.post("/ingest/deployments", json={"service": "user-api", "version": "v1.8.2", "notes": "pool refactor", "ts": deploy_ts.isoformat()}, headers=KEY)
    return now
