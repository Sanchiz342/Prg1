import hashlib
import hmac
import json
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app import crypto
from app.config import settings


@pytest.fixture
def client(tmp_path):
    settings.database_url = f"sqlite:///{tmp_path}/test.db"
    settings.workspace_dir = tmp_path / "ws"
    settings.key_file = tmp_path / "key"
    settings.secret_key = ""
    settings.runner = "local"
    settings.start_workers = True
    crypto.reset()
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def repo(tmp_path):
    """A real local git repository with a trivial 'app'."""
    path = tmp_path / "repo"
    path.mkdir()
    env = {"GIT_AUTHOR_NAME": "Dev", "GIT_AUTHOR_EMAIL": "d@x", "GIT_COMMITTER_NAME": "Dev", "GIT_COMMITTER_EMAIL": "d@x",
           "PATH": "/usr/bin:/bin"}
    run = lambda *a: subprocess.run(a, cwd=path, env=env, check=True, capture_output=True)
    run("git", "init", "-q", "-b", "main")
    (path / "check.sh").write_text('echo "token=$API_KEY"; test "$GREETING" = hello\n')
    run("git", "add", ".")
    run("git", "commit", "-qm", "init")
    return path


def wait_done(client, dep_id, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        d = client.get(f"/api/deployments/{dep_id}").json()
        if d["status"] in ("SUCCESS", "FAILED"):
            return d
        time.sleep(0.1)
    raise AssertionError("deployment did not finish")


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
