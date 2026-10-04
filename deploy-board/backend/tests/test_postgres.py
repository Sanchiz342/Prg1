"""The same application code against a real PostgreSQL server (skipped when none can be started)."""
import threading

from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app import db
from app.config import settings
from app.db import models as m
from app.jobs import MemoryQueue
from app.services import deployments
from app.worker import WorkerPool

from .conftest import deploy, log_text, make_project, set_var, wait_done


def boot(pg_url, monkeypatch, **overrides):
    monkeypatch.setattr(settings, "database_url", pg_url)
    for k, v in overrides.items():
        monkeypatch.setattr(settings, k, v)
    from app.main import app
    return TestClient(app)


def test_schema_and_full_deployment_flow_on_postgres(pg_url, monkeypatch, repo):
    with boot(pg_url, monkeypatch) as c:
        assert db.new_session().get_bind().dialect.name == "postgresql"
        assert {"users", "projects", "environments", "deployments", "stages", "logs", "variables"} <= set(inspect(db.new_session().get_bind()).get_table_names())
        admin = {"Authorization": "Bearer " + c.post("/api/auth/register", json={"username": "admin", "password": "correct horse"}).json()["token"]}
        p = make_project(c, admin, repo, environments=[{"name": "staging", "branch": "main"}, {"name": "production", "branch": "main"}])
        set_var(c, admin, p, "staging", "API_KEY", "s3cr3t-value", secret=True)
        set_var(c, admin, p, "staging", "GREETING", "hello")
        ok = wait_done(c, admin, deploy(c, admin, p, "staging")["id"])
        bad = wait_done(c, admin, deploy(c, admin, p, "production")["id"])  # no GREETING there -> fails
        assert ok["status"] == "SUCCESS"
        assert bad["status"] == "FAILED" and [s["status"] for s in bad["stages"]] == ["SUCCESS", "FAILED", "SKIPPED"]
        assert "token=***" in log_text(c, admin, ok["id"])
        assert c.get(f"/api/projects/{p['id']}/deployments?environment=staging", headers=admin).json()[0]["id"] == ok["id"]


def test_claim_is_atomic_on_postgres(pg_url, monkeypatch, repo):
    with boot(pg_url, monkeypatch, embedded_workers=False) as c:
        admin = {"Authorization": "Bearer " + c.post("/api/auth/register", json={"username": "admin", "password": "correct horse"}).json()["token"]}
        p = make_project(c, admin, repo, pipeline=[{"name": "checkout", "kind": "checkout", "retry_delay": 0},
                                                   {"name": "work", "kind": "shell", "run": "sleep 1"}])
        did = deploy(c, admin, p)["id"]
        results: list[str] = []
        threads = [threading.Thread(target=lambda: results.append(WorkerPool(c.app.state.queue).process(did))) for _ in range(4)]
        [t.start() for t in threads]
        [t.join(timeout=60) for t in threads]
        assert sorted(results) == ["SUCCESS", "skipped", "skipped", "skipped"]
        assert c.get(f"/api/deployments/{did}", headers=admin).json()["attempts"] == 1


def test_recovery_on_postgres(pg_url, monkeypatch, repo):
    from datetime import timedelta
    with boot(pg_url, monkeypatch, embedded_workers=False) as c:
        admin = {"Authorization": "Bearer " + c.post("/api/auth/register", json={"username": "admin", "password": "correct horse"}).json()["token"]}
        p = make_project(c, admin, repo)
        did = deploy(c, admin, p)["id"]
        with db.new_session() as s:
            d = s.get(m.Deployment, did)
            d.status, d.attempts, d.lease_expires_at = "RUNNING", 1, m.now() - timedelta(seconds=5)
            s.commit()
        q = MemoryQueue()
        with db.new_session() as s:
            assert deployments.requeue_expired(s, q) == [did]
            assert s.get(m.Deployment, did).status == "QUEUED"
