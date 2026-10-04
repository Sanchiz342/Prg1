import json

from app import db

from .conftest import sign, wait_done
from .test_pipeline import make


def test_webhook_flow(client, repo):
    p = make(client, repo)
    payload = {"ref": "refs/heads/main", "after": "", "repository": {"clone_url": str(repo)},
               "head_commit": {"author": {"name": "Alice"}}}
    body = json.dumps(payload).encode()
    h = {"X-GitHub-Event": "push", "content-type": "application/json"}
    assert client.post("/api/webhooks/github", content=body, headers=h).status_code == 401
    assert client.post("/api/webhooks/github", content=body, headers={**h, "X-Hub-Signature-256": sign("wrong", body)}).status_code == 401
    r = client.post("/api/webhooks/github", content=body, headers={**h, "X-Hub-Signature-256": sign(p["webhook_secret"], body)})
    assert r.status_code == 200
    dep = wait_done(client, r.json()["deployments"][0])
    assert dep["trigger"] == "webhook" and dep["author"] == "Alice"
    # other branch is ignored
    other = json.dumps({**payload, "ref": "refs/heads/dev"}).encode()
    r = client.post("/api/webhooks/github", content=other, headers={**h, "X-Hub-Signature-256": sign(p["webhook_secret"], other)})
    assert "ignored" in r.json()


def test_rollback_targets_last_success(client, repo):
    from app.main import pool
    pool.stop()  # keep rollback job queued; we only test target selection
    p = make(client, repo)
    with db.SessionLocal() as s:
        for status, image in (("SUCCESS", "shop:aaa"), ("SUCCESS", "shop:bbb"), ("FAILED", "")):
            s.add(db.Deployment(project_id=p["id"], status=status, image=image, commit=image))
        s.commit()
    ds = client.get(f"/api/projects/{p['id']}/deployments").json()
    failed = ds[0]
    rb = client.post(f"/api/deployments/{failed['id']}/rollback")
    assert rb.status_code == 202
    assert rb.json()["image"] == "shop:bbb" and rb.json()["trigger"] == "rollback" and rb.json()["rollback_of"] == ds[1]["id"]
    first = client.post(f"/api/deployments/{ds[2]['id']}/rollback").json()
    assert first["image"] == "shop:aaa"  # successful deployment rolls back to itself
    only_failed = client.post("/api/projects", json={"name": "z", "repo_url": "r"}).json()
    with db.SessionLocal() as s:
        d = db.Deployment(project_id=only_failed["id"], status="FAILED")
        s.add(d); s.commit(); did = d.id
    assert client.post(f"/api/deployments/{did}/rollback").status_code == 409


def test_websocket_replays_and_streams(client, repo):
    p = make(client, repo)
    client.put(f"/api/projects/{p['id']}/variables", json={"key": "GREETING", "value": "hello"})
    did = client.post(f"/api/projects/{p['id']}/deploy").json()["id"]
    lines, seen = [], set()
    with client.websocket_connect(f"/ws/deployments/{did}") as ws:
        while True:
            ev = ws.receive_json()
            if ev["type"] == "log":
                assert ev["id"] not in seen
                seen.add(ev["id"]); lines.append(ev["line"])
            elif ev["type"] == "deployment" and ev["status"] in ("SUCCESS", "FAILED"):
                break
    assert "building" in lines and ev["status"] == "SUCCESS"


def test_project_crud(client):
    p = client.post("/api/projects", json={"name": "a", "repo_url": "u"}).json()
    assert len(p["pipeline"]) == 7 and p["webhook_secret"]
    assert client.post("/api/projects", json={"name": "a", "repo_url": "u"}).status_code == 409
    assert client.patch(f"/api/projects/{p['id']}", json={"branch": "dev"}).json()["branch"] == "dev"
    assert client.delete(f"/api/projects/{p['id']}").status_code == 204
    assert client.get(f"/api/projects/{p['id']}").status_code == 404
