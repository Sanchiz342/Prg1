import time
from datetime import timedelta

import pytest
from starlette.websockets import WebSocketDisconnect

from app import db, security
from app.db import models as m

from .conftest import deploy, make_project, set_var, wait_done


def test_password_hashing_unit():
    h = security.hash_password("s3cret-pass")
    assert h.startswith("scrypt$") and "s3cret-pass" not in h
    assert security.verify_password("s3cret-pass", h)
    assert not security.verify_password("wrong", h)
    assert not security.verify_password("x", "garbage")
    assert security.hash_password("s3cret-pass") != h  # salted


def test_register_only_first_user_then_closed(client):
    assert client.get("/api/auth/status").json() == {"registration_open": True}
    assert client.post("/api/auth/register", json={"username": "admin", "password": "short"}).status_code == 422
    r = client.post("/api/auth/register", json={"username": "admin", "password": "correct horse"})
    assert r.status_code == 201 and r.json()["user"]["role"] == "admin"
    assert client.post("/api/auth/register", json={"username": "eve", "password": "correct horse"}).status_code == 403
    assert client.get("/api/auth/status").json() == {"registration_open": False}


def test_passwords_and_tokens_not_stored_in_clear(client, admin):
    with db.new_session() as s:
        user = s.query(m.User).one()
        assert "correct horse" not in user.password_hash and user.password_hash.startswith("scrypt$")
        raw = admin["Authorization"].split()[1]
        assert all(raw not in t.token_hash for t in s.query(m.AuthToken))


def test_login_logout_and_token_required(client, admin):
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/projects", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "wrong password"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "ghost", "password": "wrong password"}).status_code == 401
    assert client.get("/api/auth/me", headers=admin).json()["username"] == "admin"
    assert client.post("/api/auth/logout", headers=admin).status_code == 204
    assert client.get("/api/auth/me", headers=admin).status_code == 401


def test_expired_token_rejected(client, admin):
    with db.new_session() as s:
        for t in s.query(m.AuthToken):
            t.expires_at = m.now() - timedelta(seconds=1)
        s.commit()
    assert client.get("/api/auth/me", headers=admin).status_code == 401


def test_only_admin_manages_users(client, admin, user2):
    assert client.post("/api/users", json={"username": "carol", "password": "correct horse"}, headers=user2).status_code == 403
    assert client.get("/api/users", headers=user2).status_code == 403
    assert client.post("/api/users", json={"username": "bob", "password": "correct horse"}, headers=admin).status_code == 409
    assert {u["username"] for u in client.get("/api/users", headers=admin).json()} == {"admin", "bob"}


def test_users_cannot_see_each_others_projects(client, admin, user2, repo):
    p = make_project(client, user2, repo, name="bobs")
    assert client.get("/api/projects", headers=admin).json()[0]["name"] == "bobs"  # admin sees everything
    # a third user sees nothing
    client.post("/api/users", json={"username": "carol", "password": "correct horse"}, headers=admin)
    carol = {"Authorization": "Bearer " + client.post("/api/auth/login", json={"username": "carol", "password": "correct horse"}).json()["token"]}
    assert client.get("/api/projects", headers=carol).json() == []
    d = deploy(client, user2, p)
    for call in (client.get(f"/api/projects/{p['id']}", headers=carol),
                 client.get(f"/api/projects/{p['id']}/deployments", headers=carol),
                 client.get(f"/api/deployments/{d['id']}", headers=carol),
                 client.get(f"/api/deployments/{d['id']}/logs", headers=carol),
                 client.post(f"/api/deployments/{d['id']}/rollback", headers=carol),
                 client.post(f"/api/projects/{p['id']}/environments/production/deploy", headers=carol),
                 client.put(f"/api/projects/{p['id']}/environments/production/variables", json={"key": "A", "value": "b"}, headers=carol),
                 client.delete(f"/api/projects/{p['id']}", headers=carol)):
        assert call.status_code == 404
    wait_done(client, user2, d["id"])


def test_secrets_never_returned_and_stored_encrypted(client, admin, repo):
    p = make_project(client, admin, repo)
    set_var(client, admin, p, "production", "API_KEY", "s3cr3t-value", secret=True)
    set_var(client, admin, p, "production", "GREETING", "hello")
    vars_ = {v["key"]: v for v in client.get(f"/api/projects/{p['id']}/environments/production/variables", headers=admin).json()}
    assert vars_["GREETING"]["value"] == "hello" and vars_["API_KEY"]["value"] != "s3cr3t-value"
    d = wait_done(client, admin, deploy(client, admin, p)["id"])
    assert d["status"] == "SUCCESS"
    everything = client.get(f"/api/projects/{p['id']}", headers=admin).text + client.get(f"/api/deployments/{d['id']}", headers=admin).text
    assert "s3cr3t-value" not in everything
    with db.new_session() as s:
        stored = s.query(m.Variable).filter_by(key="API_KEY").one().value
        assert stored != "s3cr3t-value" and "s3cr3t-value" not in str(s.query(m.Project).one().webhook_secret)


def test_webhook_endpoint_needs_no_bearer_token(client):
    assert client.post("/api/webhooks/github", content=b"{}", headers={"X-GitHub-Event": "ping"}).status_code == 200


def test_websocket_requires_valid_token(client, admin, user2, repo):
    p = make_project(client, admin, repo)
    d = deploy(client, admin, p)
    wait_done(client, admin, d["id"])
    for path in (f"/ws/deployments/{d['id']}", f"/ws/deployments/{d['id']}?token=bogus",
                 f"/ws/deployments/{d['id']}?token={user2['Authorization'].split()[1]}"):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(path) as ws:
                ws.receive_json()
