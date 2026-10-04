import hashlib
import hmac
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import crypto, db
from app.config import settings

GIT_ENV = {"GIT_AUTHOR_NAME": "Dev", "GIT_AUTHOR_EMAIL": "d@x", "GIT_COMMITTER_NAME": "Dev", "GIT_COMMITTER_EMAIL": "d@x",
           "PATH": os.environ["PATH"], "HOME": "/tmp"}

PIPE = [
    {"name": "checkout", "kind": "checkout", "retry_delay": 0},
    {"name": "test", "kind": "shell", "run": "sh check.sh"},
    {"name": "build", "kind": "shell", "run": "echo building"},
]


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    values = dict(database_url=f"sqlite:///{tmp_path}/test.db", workspace_dir=tmp_path / "ws", key_file=tmp_path / "key",
                  secret_key="", keep_workspace=False, redis_url="", workers=2, embedded_workers=True, lease_seconds=30.0,
                  reaper_interval=10.0, max_attempts=3, runner="local", stage_timeout=60, deployment_timeout=300,
                  docker_network="", token_ttl_hours=1, frontend_dir=tmp_path / "no-frontend")
    for k, v in values.items():
        monkeypatch.setattr(settings, k, v)
    crypto.reset()


@pytest.fixture
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


def _auth(client: TestClient, username: str, password: str = "correct horse") -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def admin(client):
    r = client.post("/api/auth/register", json={"username": "admin", "password": "correct horse"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def user2(client, admin):
    r = client.post("/api/users", json={"username": "bob", "password": "correct horse"}, headers=admin)
    assert r.status_code == 201, r.text
    return _auth(client, "bob")


class GitRepo:
    def __init__(self, path: Path):
        self.path = path
        path.mkdir()
        self.git("init", "-q", "-b", "main")
        self.write("check.sh", 'echo "token=$API_KEY env=$DEPLOYBOARD_ENVIRONMENT"; test "$GREETING" = hello\n')
        self.commit("init")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.path, env=GIT_ENV, check=True, capture_output=True, text=True).stdout.strip()

    def write(self, name: str, content: str) -> None:
        (self.path / name).write_text(content)

    def commit(self, message: str) -> str:
        self.git("add", ".")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    return GitRepo(tmp_path / "repo")


def make_project(client, headers, repo, name="shop", pipeline=None, environments=None, **kw):
    body = {"name": name, "repo_url": str(repo.path if hasattr(repo, "path") else repo),
            "pipeline": PIPE if pipeline is None else pipeline,
            "environments": environments or [{"name": "production", "branch": "main"}], **kw}
    r = client.post("/api/projects", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def deploy(client, headers, project, env="production"):
    r = client.post(f"/api/projects/{project['id']}/environments/{env}/deploy", headers=headers)
    assert r.status_code == 202, r.text
    return r.json()


def wait_done(client, headers, dep_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        d = client.get(f"/api/deployments/{dep_id}", headers=headers).json()
        if d["status"] in ("SUCCESS", "FAILED"):
            return d
        time.sleep(0.05)
    raise AssertionError(f"deployment {dep_id} did not finish: {d}")


def wait_for(predicate, timeout=20, interval=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if v := predicate():
            return v
        time.sleep(interval)
    raise AssertionError("condition not met in time")


def log_text(client, headers, dep_id) -> str:
    return "\n".join(l["line"] for l in client.get(f"/api/deployments/{dep_id}/logs", headers=headers).json())


def set_var(client, headers, project, env, key, value, secret=False):
    r = client.put(f"/api/projects/{project['id']}/environments/{env}/variables",
                   json={"key": key, "value": value, "is_secret": secret}, headers=headers)
    assert r.status_code == 204, r.text


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---- real Redis (skipped if redis-server is not installed) --------------------------------
@pytest.fixture
def redis_url(tmp_path):
    exe = shutil.which("redis-server")
    if not exe:
        pytest.skip("redis-server not installed")
    port = free_port()
    proc = subprocess.Popen([exe, "--port", str(port), "--save", "", "--appendonly", "no", "--dir", str(tmp_path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import redis
    c = redis.Redis(port=port)
    wait_for(lambda: _ping(c), timeout=10)
    yield f"redis://127.0.0.1:{port}/0"
    proc.terminate()
    proc.wait(timeout=10)


def _ping(c) -> bool:
    try:
        return c.ping()
    except Exception:
        return False


# ---- real PostgreSQL (skipped unless server binaries and a `postgres` OS user exist) --------------
@pytest.fixture
def pg_url(tmp_path, monkeypatch):
    import glob
    import pwd
    bins = sorted(glob.glob("/usr/lib/postgresql/*/bin"))
    if not bins or os.geteuid() != 0:
        pytest.skip("PostgreSQL server binaries not available (or not running as root to switch to the postgres user)")
    try:
        pwd.getpwnam("postgres")
    except KeyError:
        pytest.skip("no postgres OS user")
    import tempfile
    base = Path(tempfile.mkdtemp(prefix="deployboard-pg-"))  # pytest's tmp dirs are not traversable by the postgres user
    shutil.chown(base, "postgres")
    bin_dir, data, port = bins[-1], base / "data", free_port()

    def pg(cmd: str):
        return subprocess.run(["su", "postgres", "-s", "/bin/sh", "-c", cmd], capture_output=True, text=True)

    r = pg(f"{bin_dir}/initdb -D {data} -A trust -U postgres")
    assert r.returncode == 0, r.stderr
    r = pg(f"{bin_dir}/pg_ctl -D {data} -o '-p {port} -k {base} -c listen_addresses=127.0.0.1' -l {base}/pg.log -w start")
    assert r.returncode == 0, r.stderr + (base / "pg.log").read_text()
    pg(f"{bin_dir}/createdb -h 127.0.0.1 -p {port} -U postgres deployboard")
    yield f"postgresql+psycopg://postgres@127.0.0.1:{port}/deployboard"
    pg(f"{bin_dir}/pg_ctl -D {data} -m immediate stop")
    shutil.rmtree(base, ignore_errors=True)
