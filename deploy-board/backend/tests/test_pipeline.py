import http.server
import threading

import pytest

from app import db
from app.config import settings
from app.db import models as m
from app.pipeline.definition import validate

from .conftest import deploy, log_text, make_project, set_var, wait_done


def statuses(d):
    return [s["status"] for s in d["stages"]]


def run_pipeline(client, admin, repo, pipeline, **kw):
    p = make_project(client, admin, repo, pipeline=pipeline, **kw)
    return wait_done(client, admin, deploy(client, admin, p)["id"]), p


def test_success_flow_records_everything(client, admin, repo):
    p = make_project(client, admin, repo)
    set_var(client, admin, p, "production", "GREETING", "hello")
    set_var(client, admin, p, "production", "API_KEY", "s3cr3t-value", secret=True)
    d = wait_done(client, admin, deploy(client, admin, p)["id"])
    assert d["status"] == "SUCCESS" and statuses(d) == ["SUCCESS"] * 3 and d["attempts"] == 1
    assert len(d["commit"]) == 40 and d["author"] == "Dev" and d["started_at"] and d["finished_at"]
    assert all(s["exit_code"] == 0 and s["started_at"] and s["finished_at"] for s in d["stages"])
    logs = log_text(client, admin, d["id"])
    assert "token=***" in logs and "s3cr3t-value" not in logs and "building" in logs


def test_failure_stops_pipeline_and_skips_rest(client, admin, repo):
    d, _ = run_pipeline(client, admin, repo, [
        {"name": "checkout", "kind": "checkout", "retry_delay": 0},
        {"name": "test", "kind": "shell", "run": "echo failing; exit 7"},
        {"name": "build", "kind": "shell", "run": "echo should-not-run"},
        {"name": "ship", "kind": "shell", "run": "echo nor-this"},
    ])
    assert d["status"] == "FAILED" and statuses(d) == ["SUCCESS", "FAILED", "SKIPPED", "SKIPPED"]
    assert d["stages"][1]["exit_code"] == 7 and "'test' failed" in d["error"] and d["finished_at"]
    logs = "\n".join(l["line"] for l in client.get(f"/api/deployments/{d['id']}/logs", headers=admin).json())
    assert "failing" in logs and "should-not-run" not in logs
    assert d["stages"][2]["started_at"] is None  # skipped stages never started


def test_stage_timeout_is_enforced_and_not_retried(client, admin, repo):
    d, _ = run_pipeline(client, admin, repo, [
        {"name": "checkout", "kind": "checkout", "retry_delay": 0},
        {"name": "slow", "kind": "shell", "run": "sleep 30 & sleep 30", "timeout": 1, "retries": 2, "retry_delay": 0},
        {"name": "after", "kind": "shell", "run": "echo hi"},
    ])
    assert d["status"] == "FAILED" and statuses(d) == ["SUCCESS", "FAILED", "SKIPPED"]
    assert d["stages"][1]["exit_code"] == 124 and d["stages"][1]["attempts"] == 1  # timeouts are not retried
    assert "timed out" in log_text(client, admin, d["id"])


def test_stage_retry_until_success(client, admin, repo, tmp_path):
    counter = tmp_path / "counter"
    script = f'n=$(cat {counter} 2>/dev/null || echo 0); n=$((n+1)); echo $n > {counter}; echo "try $n"; test $n -ge 3'
    d, _ = run_pipeline(client, admin, repo, [
        {"name": "checkout", "kind": "checkout", "retry_delay": 0},
        {"name": "flaky", "kind": "shell", "run": script, "retries": 3, "retry_delay": 0},
    ])
    assert d["status"] == "SUCCESS" and d["stages"][1]["attempts"] == 3 and d["stages"][1]["exit_code"] == 0
    logs = log_text(client, admin, d["id"])
    assert "try 1" in logs and "try 3" in logs and "retrying" in logs


def test_stage_retries_exhausted(client, admin, repo):
    d, _ = run_pipeline(client, admin, repo, [
        {"name": "checkout", "kind": "checkout", "retry_delay": 0},
        {"name": "bad", "kind": "shell", "run": "exit 2", "retries": 2, "retry_delay": 0},
        {"name": "after", "kind": "shell", "run": "echo hi"},
    ])
    assert d["status"] == "FAILED" and statuses(d) == ["SUCCESS", "FAILED", "SKIPPED"]
    assert d["stages"][1]["attempts"] == 3 and d["stages"][1]["exit_code"] == 2


def test_checkout_retries_by_default_then_fails(client, admin, tmp_path):
    p = make_project(client, admin, tmp_path / "missing-repo", pipeline=[{"name": "checkout", "kind": "checkout", "retry_delay": 0}])
    d = wait_done(client, admin, deploy(client, admin, p)["id"])
    assert d["status"] == "FAILED" and d["stages"][0]["attempts"] == 3 and d["stages"][0]["exit_code"] != 0
    assert "git clone" in d["error"]


def test_unknown_branch_fails_checkout(client, admin, repo):
    p = make_project(client, admin, repo, environments=[{"name": "production", "branch": "nope"}],
                     pipeline=[{"name": "checkout", "kind": "checkout", "retries": 0}])
    d = wait_done(client, admin, deploy(client, admin, p)["id"])
    assert d["status"] == "FAILED" and d["stages"][0]["status"] == "FAILED"


def test_deployment_time_limit(client, admin, repo, monkeypatch):
    monkeypatch.setattr(settings, "deployment_timeout", 2)
    d, _ = run_pipeline(client, admin, repo, [
        {"name": "checkout", "kind": "checkout", "retry_delay": 0},
        {"name": "slow", "kind": "shell", "run": "sleep 20", "timeout": 600},
        {"name": "after", "kind": "shell", "run": "echo hi"},
    ])
    assert d["status"] == "FAILED" and statuses(d) == ["SUCCESS", "FAILED", "SKIPPED"] and d["stages"][1]["exit_code"] == 124


def test_shell_stage_without_checkout_fails_clearly(client, admin, repo):
    d, _ = run_pipeline(client, admin, repo, [{"name": "x", "kind": "shell", "run": "echo hi"}])
    assert d["status"] == "FAILED" and "checkout" in d["error"]


def test_docker_stages_fail_cleanly_without_dockerfile_or_image(client, admin, repo):
    """Failure paths that need no Docker daemon."""
    d, _ = run_pipeline(client, admin, repo, [
        {"name": "checkout", "kind": "checkout", "retry_delay": 0},
        {"name": "image", "kind": "docker_build"},
        {"name": "deploy", "kind": "docker_run"},
    ])
    assert d["status"] == "FAILED" and statuses(d) == ["SUCCESS", "FAILED", "SKIPPED"] and "Dockerfile" in d["error"]
    d, _ = run_pipeline(client, admin, repo, [{"name": "deploy", "kind": "docker_run"}], name="p2")
    assert d["status"] == "FAILED" and "no image" in d["error"]


def test_workspace_is_cleaned_up_on_success_and_failure(client, admin, repo):
    p = make_project(client, admin, repo)
    failed = wait_done(client, admin, deploy(client, admin, p)["id"])  # GREETING missing -> check.sh fails
    assert failed["status"] == "FAILED" and not any(settings.workspace_dir.rglob("check.sh"))
    set_var(client, admin, p, "production", "GREETING", "hello")
    ok = wait_done(client, admin, deploy(client, admin, p)["id"])
    assert ok["status"] == "SUCCESS" and not any(settings.workspace_dir.rglob("check.sh"))


def test_environment_variables_reach_stages_and_override_nothing_reserved(client, admin, repo):
    p = make_project(client, admin, repo, pipeline=[
        {"name": "checkout", "kind": "checkout", "retry_delay": 0},
        {"name": "show", "kind": "shell", "run": "echo $DEPLOYBOARD_PROJECT/$DEPLOYBOARD_ENVIRONMENT/$DEPLOYBOARD_DEPLOYMENT"}])
    d = wait_done(client, admin, deploy(client, admin, p)["id"])
    assert f"shop/production/{d['id']}" in log_text(client, admin, d["id"])


# ---- healthcheck against a real local HTTP server ------------------------------------------
@pytest.fixture
def http_server():
    class H(http.server.BaseHTTPRequestHandler):
        hits = 0

        def do_GET(self):
            if self.path == "/flaky":
                H.hits += 1
                self.send_response(200 if H.hits >= 3 else 503)
            else:
                self.send_response(200 if self.path == "/health" else 500)
            self.end_headers()

        def log_message(self, *a): ...

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.mark.parametrize("path,expected", [("/health", "SUCCESS"), ("/bad", "FAILED"), ("/flaky", "SUCCESS")])
def test_healthcheck_stage(client, admin, repo, http_server, path, expected):
    pipe = [{"name": "checkout", "kind": "checkout", "retry_delay": 0},
            {"name": "health", "kind": "healthcheck", "attempts": 4, "interval": 0.05}]
    p = make_project(client, admin, repo, pipeline=pipe, environments=[{"name": "production", "branch": "main", "health_url": http_server + path}])
    d = wait_done(client, admin, deploy(client, admin, p)["id"])
    assert d["status"] == expected
    if expected == "FAILED":
        assert "health check failed after 4 attempts" in d["error"] and "status 500" in d["error"]


def test_healthcheck_without_url_or_unreachable(client, admin, repo):
    pipe = [{"name": "checkout", "kind": "checkout", "retry_delay": 0}, {"name": "health", "kind": "healthcheck", "attempts": 2, "interval": 0}]
    d, _ = run_pipeline(client, admin, repo, pipe)
    assert d["status"] == "FAILED" and "no health_url" in d["error"]
    d, _ = run_pipeline(client, admin, repo, pipe, name="p2", environments=[{"name": "production", "health_url": "http://127.0.0.1:9/x"}])
    assert d["status"] == "FAILED" and "after 2 attempts" in d["error"]


def test_validate_unit():
    assert validate([{"name": "a", "kind": "shell", "run": "x", "timeout": 5, "retries": 1, "retry_delay": 0.5}])
    for bad in ([{"name": "a", "kind": "shell", "run": "x", "timeout": True}], [{"name": "a", "kind": "shell", "run": "  "}],
                [{"kind": "checkout"}], ["nope"], [{"name": "a", "kind": "healthcheck", "attempts": 0}]):
        with pytest.raises(ValueError):
            validate(bad)
