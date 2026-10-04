import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import settings
from app.db import models as m
from app.jobs import MemoryQueue
from app.services import deployments

from .conftest import deploy, make_project, set_var, wait_done, wait_for


@pytest.fixture
def manual(monkeypatch):
    monkeypatch.setattr(settings, "embedded_workers", False)


def crashed_deployment(project_id: int, env_name: str = "production", attempts: int = 1, lease_ago: float = 10.0, stages=("RUNNING", "PENDING")) -> int:
    """A deployment row exactly as a worker that died mid-run would leave it."""
    with db.new_session() as s:
        env = s.query(m.Environment).filter_by(project_id=project_id, name=env_name).one()
        d = m.Deployment(project_id=project_id, environment_id=env.id, status="RUNNING", attempts=attempts, branch="main",
                         started_at=m.now(), lease_expires_at=m.now() - timedelta(seconds=lease_ago))
        d.stages = [m.Stage(position=i, name=f"s{i}", status=st, started_at=m.now() if st == "RUNNING" else None) for i, st in enumerate(stages)]
        s.add(d)
        s.commit()
        return d.id


def test_requeue_expired_resets_job(manual, client, admin, repo):
    p = make_project(client, admin, repo)
    did = crashed_deployment(p["id"])
    q = MemoryQueue()
    with db.new_session() as s:
        assert deployments.requeue_expired(s, q) == [did]
    assert q.reserve(0.1) == did
    with db.new_session() as s:
        d = s.get(m.Deployment, did)
        assert d.status == "QUEUED" and d.attempts == 1 and d.lease_expires_at is None
        assert [st.status for st in d.stages] == ["PENDING", "PENDING"] and d.stages[0].started_at is None


def test_live_lease_is_left_alone(manual, client, admin, repo):
    p = make_project(client, admin, repo)
    did = crashed_deployment(p["id"], lease_ago=-60)  # lease still valid: the worker is alive
    q = MemoryQueue()
    with db.new_session() as s:
        assert deployments.requeue_expired(s, q) == []
        assert s.get(m.Deployment, did).status == "RUNNING"
    assert q.size() == 0


def test_gives_up_after_max_attempts(manual, client, admin, repo):
    p = make_project(client, admin, repo)
    did = crashed_deployment(p["id"], attempts=settings.max_attempts)
    q = MemoryQueue()
    with db.new_session() as s:
        assert deployments.requeue_expired(s, q) == []
        d = s.get(m.Deployment, did)
        assert d.status == "FAILED" and "worker lost" in d.error and d.finished_at
        assert [st.status for st in d.stages] == ["FAILED", "SKIPPED"]
    assert q.size() == 0


def test_recover_reenqueues_queued_jobs_lost_with_the_queue(manual, client, admin, repo):
    p = make_project(client, admin, repo)
    a, b = deploy(client, admin, p)["id"], deploy(client, admin, p)["id"]
    fresh = MemoryQueue()  # the old queue (and its entries) died with the process
    with db.new_session() as s:
        assert deployments.recover(s, fresh) == [a, b]
    assert [fresh.reserve(0.1), fresh.reserve(0.1)] == [a, b]


def test_restart_resumes_interrupted_and_queued_jobs(client, admin, repo, monkeypatch):
    """Simulates a full process restart: state is in the database, the first app instance is gone."""
    p = make_project(client, admin, repo)
    set_var(client, admin, p, "production", "GREETING", "hello")
    monkeypatch.setattr(settings, "embedded_workers", False)
    from app.main import app
    with TestClient(app) as first:
        queued = deploy(first, admin, p)["id"]      # accepted, never executed
        crashed = crashed_deployment(p["id"])       # was mid-run when the process died
    # second boot, with embedded workers: startup recovery picks both up
    monkeypatch.setattr(settings, "embedded_workers", True)
    monkeypatch.setattr(settings, "reaper_interval", 0.2)
    with TestClient(app) as second:
        done_q = wait_done(second, admin, queued)
        done_c = wait_done(second, admin, crashed)
    assert done_q["status"] == "SUCCESS" and done_q["attempts"] == 1
    # the interrupted job ran again from scratch (its fake stage rows are not in its pipeline, so only the lifecycle is asserted)
    assert done_c["status"] in ("SUCCESS", "FAILED") and done_c["attempts"] == 2 and done_c["finished_at"]


def test_heartbeat_keeps_long_running_job_from_being_stolen(client, admin, repo, monkeypatch):
    monkeypatch.setattr(settings, "lease_seconds", 1.5)
    monkeypatch.setattr(settings, "reaper_interval", 0.3)
    from app.main import app
    with TestClient(app) as c:
        p = make_project(c, admin, repo, pipeline=[{"name": "checkout", "kind": "checkout", "retry_delay": 0},
                                                   {"name": "long", "kind": "shell", "run": "sleep 4"}])
        d = wait_done(c, admin, deploy(c, admin, p)["id"], timeout=30)
    assert d["status"] == "SUCCESS" and d["attempts"] == 1  # lease (1.5s) was renewed while the 4s stage ran


def test_lost_worker_detected_by_running_reaper(client, admin, repo, monkeypatch):
    p = make_project(client, admin, repo)
    set_var(client, admin, p, "production", "GREETING", "hello")
    monkeypatch.setattr(settings, "embedded_workers", False)
    from app.main import app
    with TestClient(app):
        pass
    # worker "dies" later; a live pool's reaper must notice without any restart
    monkeypatch.setattr(settings, "embedded_workers", True)
    monkeypatch.setattr(settings, "reaper_interval", 0.2)
    with TestClient(app) as c:
        did = deploy(c, admin, p)["id"]
        wait_done(c, admin, did)
        lost = crashed_deployment(p["id"])
        d = wait_done(c, admin, lost, timeout=30)
    assert d["attempts"] == 2
