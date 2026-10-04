import http.server
import threading

from .conftest import wait_done

PIPE = [
    {"name": "checkout", "kind": "checkout"},
    {"name": "test", "kind": "shell", "run": "sh check.sh"},
    {"name": "build", "kind": "shell", "run": "echo building"},
]


def make(client, repo, **kw):
    r = client.post("/api/projects", json={"name": "Shop API", "repo_url": str(repo), "pipeline": PIPE, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_success_flow_with_secrets_masked(client, repo):
    p = make(client, repo)
    client.put(f"/api/projects/{p['id']}/variables", json={"key": "GREETING", "value": "hello"})
    client.put(f"/api/projects/{p['id']}/variables", json={"key": "API_KEY", "value": "s3cr3t-value", "is_secret": True})
    d = client.post(f"/api/projects/{p['id']}/deploy").json()
    d = wait_done(client, d["id"])
    assert d["status"] == "SUCCESS"
    assert [s["status"] for s in d["stages"]] == ["SUCCESS"] * 3
    assert len(d["commit"]) == 40 and d["author"] == "Dev"
    logs = "\n".join(l["line"] for l in client.get(f"/api/deployments/{d['id']}/logs").json())
    assert "token=***" in logs and "s3cr3t-value" not in logs
    # secrets never leave the API
    vars_ = {v["key"]: v for v in client.get(f"/api/projects/{p['id']}/variables").json()}
    assert vars_["API_KEY"]["value"] != "s3cr3t-value" and vars_["GREETING"]["value"] == "hello"


def test_failed_stage_skips_rest(client, repo):
    p = make(client, repo)  # GREETING missing -> `test` stage fails
    d = wait_done(client, client.post(f"/api/projects/{p['id']}/deploy").json()["id"])
    assert d["status"] == "FAILED"
    assert [s["status"] for s in d["stages"]] == ["SUCCESS", "FAILED", "SKIPPED"]
    assert d["stages"][1]["exit_code"] == 1 and "test" in d["error"]


def test_healthcheck_stage(client, repo):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/health" else 500)
            self.end_headers()

        def log_message(self, *a): ...

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    pipe = [{"name": "checkout", "kind": "checkout"}, {"name": "health", "kind": "healthcheck", "retries": 2, "interval": 0.1}]
    base = f"http://127.0.0.1:{srv.server_port}"
    for path, expect in (("/health", "SUCCESS"), ("/bad", "FAILED")):
        p = client.post("/api/projects", json={"name": "h" + expect, "repo_url": str(repo), "pipeline": pipe, "health_url": base + path}).json()
        assert wait_done(client, client.post(f"/api/projects/{p['id']}/deploy").json()["id"])["status"] == expect
    srv.shutdown()


def test_timeout_and_bad_pipeline(client, repo):
    r = client.post("/api/projects", json={"name": "x", "repo_url": "r", "pipeline": [{"name": "a", "kind": "nope"}]})
    assert r.status_code == 422
