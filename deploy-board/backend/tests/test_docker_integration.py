"""Real Docker integration tests: they call the actual Docker CLI/daemon and are SKIPPED when no daemon is
reachable. Everything else in the suite is independent of Docker.

They use host networking (DB_DOCKER_NETWORK=host) so they also work on daemons without bridge networking,
which means published-port mapping (`-p`) is intentionally NOT covered here."""
import shutil
import subprocess
import time
import urllib.request

import pytest

from app.config import settings
from app.runners.docker import DockerRunner

from .conftest import GitRepo, deploy, free_port, log_text, make_project, set_var, wait_done, wait_for

pytestmark = pytest.mark.docker
IMAGE = "alpine:3.20"

# alpine's busybox has no httpd applet, so a tiny nc loop answers every request with $GREETING
DOCKERFILE = r'''FROM alpine:3.20
CMD ["sh", "-c", "while true; do printf 'HTTP/1.1 200 OK\\r\\nConnection: close\\r\\n\\r\\n%s\\n' \"$GREETING\" | nc -l -p \"$PORT\" >/dev/null; done"]
'''
BROKEN_DOCKERFILE = '''FROM alpine:3.20
CMD ["sh", "-c", "echo crashing on purpose; exit 1"]
'''


def docker(*args: str, check=False) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, timeout=120)


@pytest.fixture(scope="module", autouse=True)
def docker_available():
    if not shutil.which("docker") or docker("info").returncode != 0:
        pytest.skip("no reachable Docker daemon")
    if docker("image", "inspect", IMAGE).returncode != 0 and docker("pull", IMAGE).returncode != 0:
        pytest.skip(f"cannot obtain {IMAGE}")


@pytest.fixture(autouse=True)
def cleanup_docker():
    yield
    ids = docker("ps", "-aq", "--filter", "label=deployboard.managed=true").stdout.split()
    if ids:
        docker("rm", "-f", *ids)
    images = docker("images", "-q", "--filter", "label=deployboard.project").stdout.split()
    if images:
        docker("rmi", "-f", *images)


@pytest.fixture
def docker_settings(monkeypatch):
    monkeypatch.setattr(settings, "runner", "docker")
    monkeypatch.setattr(settings, "docker_network", "host")
    monkeypatch.setattr(settings, "runner_image", IMAGE)


def http_get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.read().decode().strip()


# ---- DockerRunner ---------------------------------------------------------------------------
def test_runner_executes_in_container_with_env_workspace_and_user(tmp_path, docker_settings):
    (tmp_path / "marker.txt").write_text("from-host\n")
    lines: list[str] = []
    code = DockerRunner().run('cat marker.txt; echo "$GREETING"; echo "u=$(id -u)"; touch created-in-container',
                              cwd=tmp_path, env={"GREETING": 'hello "quoted" world'}, on_line=lines.append, timeout=60)
    assert code == 0, lines
    assert lines[:2] == ["from-host", 'hello "quoted" world']
    import os
    assert f"u={os.getuid()}" in lines  # runs as the worker's user, not root
    assert (tmp_path / "created-in-container").stat().st_uid == os.getuid()  # and the workspace stays removable


def test_runner_exit_code_and_timeout_removes_container(tmp_path, docker_settings):
    lines: list[str] = []
    assert DockerRunner().run("exit 5", cwd=tmp_path, env={}, on_line=lines.append, timeout=60) == 5
    t = time.time()
    code = DockerRunner().run("sleep 60", cwd=tmp_path, env={}, on_line=lines.append, timeout=3)
    assert code == 124 and time.time() - t < 30
    assert docker("ps", "-aq", "--filter", "label=deployboard.managed=true").stdout.split() == []  # no orphaned container


# ---- full pipeline ---------------------------------------------------------------------------
@pytest.fixture
def app_repo(tmp_path):
    r = GitRepo(tmp_path / "apprepo")
    r.write("Dockerfile", DOCKERFILE)
    r.write("check.sh", 'test "$GREETING" = v1 || test "$GREETING" = v2\n')
    r.commit("app v1")
    return r


def full_pipeline(port: int):
    return [
        {"name": "checkout", "kind": "checkout", "retry_delay": 0},
        {"name": "test", "kind": "shell", "run": "sh check.sh"},  # runs inside a runner container
        {"name": "docker", "kind": "docker_build", "timeout": 300},
        {"name": "deploy", "kind": "docker_run"},
        {"name": "healthcheck", "kind": "healthcheck", "attempts": 10, "interval": 0.5},
    ], {"name": "production", "branch": "main", "health_url": f"http://127.0.0.1:{port}/health"}


def test_build_deploy_healthcheck_and_rollback(client, admin, app_repo, docker_settings):
    port = free_port()
    pipeline, env = full_pipeline(port)
    p = make_project(client, admin, app_repo, name="demo-app", pipeline=pipeline, environments=[env])
    set_var(client, admin, p, "production", "PORT", str(port))
    set_var(client, admin, p, "production", "GREETING", "v1")
    set_var(client, admin, p, "production", "RUNNER_IMAGE", IMAGE)

    v1 = wait_done(client, admin, deploy(client, admin, p)["id"], timeout=240)
    assert v1["status"] == "SUCCESS", log_text(client, admin, v1["id"])
    assert [s["status"] for s in v1["stages"]] == ["SUCCESS"] * 5
    assert v1["image"] == f"demo-app-production:{v1['commit'][:7]}"
    assert http_get(f"http://127.0.0.1:{port}/health") == "v1"  # container got its env vars
    name = "deployboard-demo-app-production"
    assert docker("inspect", "-f", "{{.State.Running}} {{index .Config.Labels \"deployboard.deployment\"}}", name).stdout.split() == ["true", str(v1["id"])]

    # a broken release: the container crashes -> health check fails -> deployment FAILED
    app_repo.write("Dockerfile", BROKEN_DOCKERFILE)
    app_repo.commit("v2 broken")
    v2 = wait_done(client, admin, deploy(client, admin, p)["id"], timeout=240)
    assert v2["status"] == "FAILED" and [s["status"] for s in v2["stages"]][-2:] == ["SUCCESS", "FAILED"]
    assert "health check failed" in v2["error"] and "crashing on purpose" in log_text(client, admin, v2["id"])  # container logs shown

    # rollback re-deploys v1's image without building anything
    rb = client.post(f"/api/deployments/{v2['id']}/rollback", headers=admin).json()
    assert rb["rollback_of"] == v1["id"] and rb["image"] == v1["image"]
    rb = wait_done(client, admin, rb["id"], timeout=120)
    assert rb["status"] == "SUCCESS", log_text(client, admin, rb["id"])
    assert [s["name"] for s in rb["stages"]] == ["deploy", "healthcheck"]
    wait_for(lambda: http_get(f"http://127.0.0.1:{port}/health") == "v1", timeout=15)
    assert docker("ps", "-q", "--filter", f"name=^{name}$").stdout.split() != []  # exactly one container for this environment


def test_docker_build_failure_is_reported(client, admin, app_repo, docker_settings):
    app_repo.write("Dockerfile", "FROM alpine:3.20\nRUN exit 3\n")
    app_repo.commit("bad build")
    port = free_port()
    pipeline, env = full_pipeline(port)
    p = make_project(client, admin, app_repo, name="badbuild", pipeline=pipeline, environments=[env])
    set_var(client, admin, p, "production", "GREETING", "v1")
    set_var(client, admin, p, "production", "RUNNER_IMAGE", IMAGE)
    d = wait_done(client, admin, deploy(client, admin, p)["id"], timeout=240)
    assert d["status"] == "FAILED" and [s["status"] for s in d["stages"]] == ["SUCCESS", "SUCCESS", "FAILED", "SKIPPED", "SKIPPED"]
    assert "docker build exited" in d["error"] and d["image"] == ""


def test_secrets_not_in_process_list_or_logs(client, admin, app_repo, docker_settings):
    port = free_port()
    pipeline, env = full_pipeline(port)
    p = make_project(client, admin, app_repo, name="secretapp", pipeline=pipeline, environments=[env])
    for k, v in (("PORT", str(port)), ("GREETING", "v1"), ("RUNNER_IMAGE", IMAGE)):
        set_var(client, admin, p, "production", k, v)
    set_var(client, admin, p, "production", "DATABASE_URL", "postgres://user:topsecret-pw@db/x", secret=True)
    d = wait_done(client, admin, deploy(client, admin, p)["id"], timeout=240)
    assert d["status"] == "SUCCESS", log_text(client, admin, d["id"])
    assert "topsecret-pw" not in log_text(client, admin, d["id"])
    cfg = docker("inspect", "-f", "{{.Config.Cmd}} {{.Args}} {{.Path}}", "deployboard-secretapp-production").stdout
    assert "topsecret-pw" not in cfg
    env_in_container = docker("exec", "deployboard-secretapp-production", "env").stdout
    assert "DATABASE_URL=postgres://user:topsecret-pw@db/x" in env_in_container  # but the app itself receives it
