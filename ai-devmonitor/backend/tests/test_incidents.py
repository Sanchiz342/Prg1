from datetime import timedelta

from app.core.db import utcnow
from tests.conftest import KEY, seed_incident_scenario


def test_end_to_end_incident_flow(client, admin, viewer):
    seed_incident_scenario(client)
    changes = client.post("/detect", headers=admin).json()["changes"]
    assert any(c["type"] == "opened" for c in changes)

    incidents = client.get("/incidents", headers=viewer).json()
    inc = next(i for i in incidents if i["trigger_metric"] == "error_rate")
    assert inc["severity"] == "critical" and inc["status"] == "investigating" and inc["service"] == "user-api"

    detail = client.get(f"/incidents/{inc['id']}", headers=viewer).json()
    analysis = detail["analysis"]
    causes = " ".join(c["cause"] for c in analysis["possible_causes"])
    assert "Database" in causes and "v1.8.2" in causes
    assert "disclaimer" in analysis and analysis["meta"]["mode"] == "hybrid"
    kinds = [e["kind"] for e in detail["timeline"]]
    assert "deployment" in kinds and "detected" in kinds
    assert any("db_connections" in e["description"] for e in detail["timeline"])

    # PII in logs is redacted before leaving (mock is local, so check redaction pipeline separately)
    logs = client.get(f"/incidents/{inc['id']}/logs", headers=viewer).json()
    assert logs["clusters"][0]["category"] == "database" and logs["clusters"][0]["count"] > 10
    assert client.get(f"/incidents/{inc['id']}/metrics", headers=viewer).json()

    # status workflow + RBAC
    assert client.post(f"/incidents/{inc['id']}/status?status=identified", headers=viewer).status_code == 403
    assert client.post(f"/incidents/{inc['id']}/status?status=identified", headers=admin).json()["status"] == "identified"

    services = {s["name"]: s for s in client.get("/services", headers=viewer).json()}
    assert services["user-api"]["status"] == "red" and services["postgres"]["status"] == "green"

    # dedupe: second pass opens nothing new for the same metric
    again = client.post("/detect", headers=admin).json()["changes"]
    assert not [c for c in again if c["type"] == "opened" and c["incident"] == inc["id"]]

    resolved = client.post(f"/incidents/{inc['id']}/resolve", headers=admin).json()
    assert resolved["status"] == "resolved" and resolved["summary"]["likely_cause"]
    assert resolved["summary"]["related_deployment"].startswith("Deployment v1.8.2")
    assert resolved["timeline"][-1]["kind"] == "resolved"


def test_auto_resolve_after_recovery(client, admin, viewer):
    seed_incident_scenario(client)
    client.post("/detect", headers=admin)
    now = utcnow()
    recovery = [
        {"service": "user-api", "name": n, "value": v, "ts": (now + timedelta(minutes=m)).isoformat()}
        for m in range(1, 8)
        for n, v in (("error_rate", 0.5), ("latency_p95", 200.0), ("cpu", 35.0), ("db_connections", 40.0))
    ]
    client.post("/ingest/metrics", json={"metrics": recovery}, headers=KEY)
    # evaluate at a future instant by backdating nothing: detection uses "now", so push recovery into the window
    from app.core import db as dbmod
    from app.incidents import service as svc

    with dbmod.SessionLocal() as s:
        changes = svc.run_detection(s, now + timedelta(minutes=8))
    assert any(c["type"] == "resolved" for c in changes)
    assert client.get("/incidents?status=resolved", headers=viewer).json()


def test_similar_incidents(client, admin, viewer):
    seed_incident_scenario(client)
    client.post("/detect", headers=admin)
    first = next(i for i in client.get("/incidents", headers=viewer).json() if i["trigger_metric"] == "error_rate")
    from app.core import db as dbmod
    from app.incidents import service as svc
    from app.models import Incident

    with dbmod.SessionLocal() as s:
        base = s.get(Incident, first["id"])
        twin = Incident(title="user-api: Error Rate Critical", severity="critical", service="user-api", trigger_metric="error_rate",
                        status="resolved", started_at=utcnow() - timedelta(days=3), affected_services=["user-api"], source="rule")
        s.add(twin)
        s.commit()
        svc.analyze_incident(s, twin)
        assert twin.id in [x["id"] for x in svc.similar_incidents(s, base, limit=10, min_score=0.1)]


def test_ask_and_forecast(client, admin, viewer):
    seed_incident_scenario(client)
    client.post("/detect", headers=admin)
    inc_id = client.get("/incidents", headers=viewer).json()[0]["id"]
    out = client.post("/ask", json={"question": "Why did it break?", "incident_id": inc_id}, headers=viewer).json()
    assert "Most likely" in out["answer"]
    assert "Degraded" in client.post("/ask", json={"question": "What is the status?"}, headers=viewer).json()["answer"]
    f = client.get("/metrics/forecast?service=user-api&name=db_connections&threshold=100", headers=viewer)
    assert f.status_code == 200 and "slope_per_min" in f.json()
    assert client.get("/metrics/forecast?service=nope&name=x", headers=viewer).status_code == 422


def test_anomaly_incident_without_threshold_breach(client, admin, viewer):
    import random

    rnd, now, metrics = random.Random(3), utcnow(), []
    for i in range(120):
        ts = now - timedelta(minutes=120 - i)
        v = 105 + rnd.uniform(-8, 8) + (320 if i >= 115 else 0)
        metrics.append({"service": "shop-api", "name": "requests", "value": v, "ts": ts.isoformat()})
    client.post("/ingest/metrics", json={"metrics": metrics}, headers=KEY)
    changes = client.post("/detect", headers=admin).json()["changes"]
    assert changes
    inc = client.get("/incidents", headers=viewer).json()[0]
    assert inc["source"] == "anomaly" and inc["trigger_metric"] == "requests"


def test_websocket_requires_token_and_receives_events(client, admin):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws"):
            pass
    token = admin["Authorization"].split()[1]
    seed_incident_scenario(client)
    with client.websocket_connect(f"/ws?token={token}") as ws:
        client.post("/detect", headers=admin)
        msg = ws.receive_json()
        assert msg["type"] == "incident_opened"
