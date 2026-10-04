import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import settings
from app.db import models as m
from app.jobs import MemoryQueue
from app.pipeline.engine import Execution
from app.worker import WorkerPool

from .conftest import deploy, log_text, make_project, set_var, wait_done, wait_for

SLEEP_PIPE = [{"name": "checkout", "kind": "checkout", "retry_delay": 0}, {"name": "work", "kind": "shell", "run": "sleep 1"}]


@pytest.fixture
def manual(monkeypatch):
    """App with no embedded workers: the test drives a WorkerPool by hand."""
    monkeypatch.setattr(settings, "embedded_workers", False)


def status(client, admin, did):
    return client.get(f"/api/deployments/{did}", headers=admin).json()


def test_memory_queue_fifo_and_timeout():
    q = MemoryQueue()
    assert q.reserve(timeout=0.05) is None
    for i in (3, 1, 2):
        q.enqueue(i)
    assert q.size() == 3 and [q.reserve(0.1) for _ in range(3)] == [3, 1, 2]


def test_http_request_only_queues_the_job(manual, client, admin, repo):
    p = make_project(client, admin, repo)
    d = deploy(client, admin, p)
    time.sleep(0.3)
    d = status(client, admin, d["id"])
    assert d["status"] == "QUEUED" and d["stages"] == [] and d["started_at"] is None
    assert client.app.state.queue.size() == 1
    assert not settings.workspace_dir.exists() or not any(settings.workspace_dir.rglob("*.sh"))  # nothing executed


def test_process_runs_job_to_completion(manual, client, admin, repo):
    p = make_project(client, admin, repo)
    set_var(client, admin, p, "production", "GREETING", "hello")
    did = deploy(client, admin, p)["id"]
    pool = WorkerPool(client.app.state.queue)
    assert pool.process(did) == "SUCCESS"
    assert pool.process(did) == "skipped"  # already finished: a duplicate queue entry is harmless
    assert pool.process(99999) == "skipped"  # unknown id
    d = status(client, admin, did)
    assert d["status"] == "SUCCESS" and d["attempts"] == 1


def test_only_one_worker_wins_a_job(manual, client, admin, repo):
    p = make_project(client, admin, repo, pipeline=SLEEP_PIPE)
    did = deploy(client, admin, p)["id"]
    results: list[str] = []
    pools = [WorkerPool(client.app.state.queue) for _ in range(4)]
    threads = [threading.Thread(target=lambda pool=pool: results.append(pool.process(did))) for pool in pools]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    assert sorted(results) == ["SUCCESS", "skipped", "skipped", "skipped"]
    assert status(client, admin, did)["attempts"] == 1


def test_job_waits_while_environment_is_busy(manual, client, admin, repo):
    p = make_project(client, admin, repo)
    set_var(client, admin, p, "production", "GREETING", "hello")
    first, second = deploy(client, admin, p)["id"], deploy(client, admin, p)["id"]
    with db.new_session() as s:  # `first` is being executed by some other worker
        d = s.get(m.Deployment, first)
        d.status = "RUNNING"
        s.commit()
    q = client.app.state.queue
    while q.reserve(0.05) is not None:
        pass
    pool = WorkerPool(q)
    assert pool.process(second) == "busy"
    assert q.reserve(timeout=5) == second  # re-queued shortly after
    with db.new_session() as s:
        s.get(m.Deployment, first).status = "SUCCESS"
        s.commit()
    assert pool.process(second) == "SUCCESS"


def test_deployments_of_one_environment_never_overlap(client, admin, repo):
    p = make_project(client, admin, repo, pipeline=SLEEP_PIPE)
    ids = [deploy(client, admin, p)["id"] for _ in range(3)]
    done = sorted((wait_done(client, admin, i, timeout=60) for i in ids), key=lambda d: d["started_at"])
    assert [d["status"] for d in done] == ["SUCCESS"] * 3
    for a, b in zip(done, done[1:]):
        assert a["finished_at"] <= b["started_at"], "two deployments of one environment ran at the same time"


def test_different_environments_run_in_parallel(client, admin, repo):
    p = make_project(client, admin, repo, pipeline=SLEEP_PIPE,
                     environments=[{"name": "staging", "branch": "main"}, {"name": "production", "branch": "main"}])
    a, b = deploy(client, admin, p, "staging")["id"], deploy(client, admin, p, "production")["id"]
    da, db_ = wait_done(client, admin, a), wait_done(client, admin, b)
    assert da["started_at"] < db_["finished_at"] and db_["started_at"] < da["finished_at"]


def test_runner_setup_failure_marks_deployment_failed(manual, client, admin, repo):
    p = make_project(client, admin, repo)
    did = deploy(client, admin, p)["id"]

    def broken():
        raise RuntimeError("no runner available")

    assert WorkerPool(client.app.state.queue, runner_factory=broken).process(did) == "FAILED"
    d = status(client, admin, did)
    assert d["status"] == "FAILED" and "no runner available" in d["error"] and d["finished_at"]


def test_engine_bug_never_leaves_job_running(manual, client, admin, repo, monkeypatch):
    p = make_project(client, admin, repo)
    did = deploy(client, admin, p)["id"]
    monkeypatch.setattr(Execution, "_emit_stage", lambda self, st: (_ for _ in ()).throw(RuntimeError("boom")))
    assert WorkerPool(client.app.state.queue).process(did) == "FAILED"
    d = status(client, admin, did)
    assert d["status"] == "FAILED" and "boom" in d["error"] and d["finished_at"]
    assert not any(s["status"] in ("RUNNING", "PENDING") for s in d["stages"])


def test_unexpected_error_in_stage_handler_fails_that_stage(manual, client, admin, repo, monkeypatch):
    p = make_project(client, admin, repo)
    did = deploy(client, admin, p)["id"]
    monkeypatch.setattr(Execution, "_do_shell", lambda self, d, t: (_ for _ in ()).throw(KeyError("oops")))
    assert WorkerPool(client.app.state.queue).process(did) == "FAILED"
    d = status(client, admin, did)
    assert [s["status"] for s in d["stages"]] == ["SUCCESS", "FAILED", "SKIPPED"] and "internal error: KeyError" in d["error"]
    assert d["stages"][1]["attempts"] == 1  # internal errors are not retried


def test_pool_start_stop_processes_queue(manual, client, admin, repo):
    p = make_project(client, admin, repo)
    set_var(client, admin, p, "production", "GREETING", "hello")
    pool = WorkerPool(client.app.state.queue, size=2)
    pool.start()
    try:
        did = deploy(client, admin, p)["id"]
        assert wait_done(client, admin, did)["status"] == "SUCCESS"
    finally:
        pool.stop()
    assert not pool._threads
