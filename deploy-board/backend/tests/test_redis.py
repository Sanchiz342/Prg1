"""Integration tests against a real redis-server process (skipped when the binary is missing)."""
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import settings
from app.db import models as m
from app.events import EventBus
from app.jobs import create_queue
from app.jobs.redis_queue import RedisQueue

from .conftest import deploy, make_project, set_var, wait_done, wait_for

BACKEND = Path(__file__).resolve().parents[1]


def test_redis_queue_fifo_blocking_and_size(redis_url):
    q = RedisQueue(redis_url)
    assert q.reserve(timeout=1) is None
    for i in (5, 6, 7):
        q.enqueue(i)
    assert q.size() == 3 and [q.reserve(1) for _ in range(3)] == [5, 6, 7]
    got = []
    t = threading.Thread(target=lambda: got.append(q.reserve(5)))
    t.start()
    time.sleep(0.3)
    RedisQueue(redis_url).enqueue(42)  # another connection (like another process) wakes the blocked consumer
    t.join(5)
    assert got == [42]
    q.close()


def test_factory_selects_backend(redis_url, monkeypatch):
    assert type(create_queue()).__name__ == "MemoryQueue"
    monkeypatch.setattr(settings, "redis_url", redis_url)
    assert type(create_queue()).__name__ == "RedisQueue"


def test_event_bridge_between_processes(redis_url):
    import asyncio

    api_bus, worker_bus = EventBus(), EventBus()
    api_bus.attach_redis(redis_url, listen=True)
    worker_bus.attach_redis(redis_url, listen=False)
    time.sleep(0.5)  # let the listener subscribe

    async def scenario():
        q = api_bus.subscribe(7)
        worker_bus.publish(7, {"type": "log", "id": 1, "line": "from worker"})
        worker_bus.publish(8, {"type": "log", "id": 2, "line": "other deployment"})
        api_bus.publish(7, {"type": "log", "id": 3, "line": "local"})
        got = [await asyncio.wait_for(q.get(), 5), await asyncio.wait_for(q.get(), 5)]
        assert q.empty()  # exactly once: no echo of the API's own event, nothing from deployment 8
        return got

    got = asyncio.run(scenario())
    assert {g["line"] for g in got} == {"from worker", "local"}
    api_bus.detach_redis()
    worker_bus.detach_redis()


def test_api_with_redis_and_embedded_workers(redis_url, monkeypatch, repo):
    monkeypatch.setattr(settings, "redis_url", redis_url)
    from app.main import app
    with TestClient(app) as c:
        admin = {"Authorization": "Bearer " + c.post("/api/auth/register", json={"username": "admin", "password": "correct horse"}).json()["token"]}
        p = make_project(c, admin, repo)
        set_var(c, admin, p, "production", "GREETING", "hello")
        d = wait_done(c, admin, deploy(c, admin, p)["id"])
        assert d["status"] == "SUCCESS"
        with c.websocket_connect(f"/ws/deployments/{d['id']}?token={admin['Authorization'].split()[1]}") as ws:
            assert ws.receive_json()["type"] == "log"


def worker_process(redis_url, env_extra=None):
    env = {**os.environ, "DB_REDIS_URL": redis_url, "DB_URL": settings.database_url, "DB_WORKSPACE": str(settings.workspace_dir),
           "DB_KEY_FILE": str(settings.key_file), "DB_LEASE_SECONDS": "2", "DB_REAPER_INTERVAL": "0.5", "DB_WORKERS": "1",
           "PYTHONPATH": str(BACKEND), **(env_extra or {})}
    return subprocess.Popen([sys.executable, "-m", "app.worker_main"], cwd=BACKEND, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def test_standalone_worker_runs_jobs_and_streams_logs_to_api(redis_url, monkeypatch, repo):
    monkeypatch.setattr(settings, "redis_url", redis_url)
    monkeypatch.setattr(settings, "embedded_workers", False)
    from app.main import app
    with TestClient(app) as c:
        admin = {"Authorization": "Bearer " + c.post("/api/auth/register", json={"username": "admin", "password": "correct horse"}).json()["token"]}
        p = make_project(c, admin, repo, pipeline=[{"name": "checkout", "kind": "checkout", "retry_delay": 0},
                                                   {"name": "talk", "kind": "shell", "run": "echo hello-from-worker; sleep 1; echo bye"}])
        w = worker_process(redis_url)
        try:
            d = deploy(c, admin, p)
            lines = []
            with c.websocket_connect(f"/ws/deployments/{d['id']}?token={admin['Authorization'].split()[1]}") as ws:
                while True:
                    ev = ws.receive_json()
                    if ev["type"] == "log":
                        lines.append(ev["line"])
                    if ev["type"] == "deployment" and ev["status"] in ("SUCCESS", "FAILED"):
                        break
            assert "hello-from-worker" in lines and "bye" in lines  # logs crossed the process boundary via Redis pub/sub
            assert wait_done(c, admin, d["id"])["status"] == "SUCCESS"
        finally:
            w.send_signal(signal.SIGTERM)
            w.wait(timeout=20)


def test_worker_killed_midway_is_recovered_by_another_worker(redis_url, monkeypatch, repo):
    """Real crash: SIGKILL a worker process in the middle of a stage; a second worker must finish the job."""
    monkeypatch.setattr(settings, "redis_url", redis_url)
    monkeypatch.setattr(settings, "embedded_workers", False)
    from app.main import app
    with TestClient(app) as c:
        admin = {"Authorization": "Bearer " + c.post("/api/auth/register", json={"username": "admin", "password": "correct horse"}).json()["token"]}
        p = make_project(c, admin, repo, pipeline=[{"name": "checkout", "kind": "checkout", "retry_delay": 0},
                                                   {"name": "long", "kind": "shell", "run": "echo started; sleep 4; echo finished"}])
        w1 = worker_process(redis_url)
        try:
            did = deploy(c, admin, p)["id"]
            wait_for(lambda: (c.get(f"/api/deployments/{did}", headers=admin).json()["stages"] or [{}])[-1].get("status") == "RUNNING", timeout=20)
            w1.kill()  # no cleanup, lease is left behind
            w1.wait(timeout=10)
            assert c.get(f"/api/deployments/{did}", headers=admin).json()["status"] == "RUNNING"
            w2 = worker_process(redis_url)
            try:
                d = wait_done(c, admin, did, timeout=60)
            finally:
                w2.send_signal(signal.SIGTERM)
                w2.wait(timeout=20)
        finally:
            if w1.poll() is None:
                w1.kill()
    assert d["status"] == "SUCCESS" and d["attempts"] == 2
