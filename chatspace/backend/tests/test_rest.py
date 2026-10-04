import pytest


def test_register_login_and_me(api):
    u = api.user("alice")
    assert api.call("GET", "/auth/me", u).json()["username"] == "alice"
    assert api.c.post("/api/auth/login", json={"email": "alice@x.io", "password": "password123"}).status_code == 200
    assert api.c.post("/api/auth/login", json={"email": "alice@x.io", "password": "wrong-pass"}).status_code == 401
    dup = api.c.post("/api/auth/register", json={"username": "alice", "email": "other@x.io", "password": "password123"})
    assert dup.status_code == 409


def test_requires_auth(client):
    assert client.get("/api/workspaces").status_code == 401
    assert client.get("/api/workspaces", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_passwords_are_hashed(client, db_url):
    from tests.conftest import fetch_all
    client.post("/api/auth/register", json={"username": "dave", "email": "d@x.io", "password": "password123"})
    row = fetch_all(db_url, "select password_hash from users")[0]
    assert "password123" not in row[0] and row[0].startswith("$2")


def test_rbac(api, team):
    t = team
    wid = t["ws"]["id"]
    # MEMBER cannot create channels or add members
    assert api.call("POST", f"/workspaces/{wid}/channels", t["bob"], json={"name": "dev"}).status_code == 403
    assert api.call("POST", f"/workspaces/{wid}/members", t["bob"], json={"email": "carol@x.io"}).status_code == 403
    # only OWNER changes roles; promoted ADMIN can create channels but not add admins
    assert api.call("PATCH", f"/workspaces/{wid}/members/{t['bob']['id']}", t["bob"], json={"role": "ADMIN"}).status_code == 403
    assert api.call("PATCH", f"/workspaces/{wid}/members/{t['bob']['id']}", t["alice"], json={"role": "ADMIN"}).status_code == 200
    assert api.call("POST", f"/workspaces/{wid}/channels", t["bob"], json={"name": "dev"}).status_code == 201
    dave = api.user("dave")
    assert api.call("POST", f"/workspaces/{wid}/members", t["bob"], json={"email": "dave@x.io", "role": "ADMIN"}).status_code == 403
    # outsiders see 404, not 403
    assert api.call("GET", f"/workspaces/{wid}/channels", dave).status_code == 404
    # only OWNER deletes the workspace
    assert api.call("DELETE", f"/workspaces/{wid}", t["bob"]).status_code == 403
    assert api.call("DELETE", f"/workspaces/{wid}", t["alice"]).status_code == 204
    assert api.call("GET", "/workspaces", t["alice"]).json() == []


def test_private_channel_visibility(api, team):
    t = team
    wid, sid = t["ws"]["id"], t["secret"]["id"]
    names = lambda u: {c["name"] for c in api.call("GET", f"/workspaces/{wid}/channels", u).json()}
    assert names(t["alice"]) == {"general", "secret"}
    assert names(t["bob"]) == {"general"}
    assert api.call("GET", f"/channels/{sid}/messages", t["bob"]).status_code == 404
    assert api.call("POST", f"/channels/{sid}/messages", t["bob"], json={"content": "hi"}).status_code == 404
    api.call("POST", f"/channels/{sid}/members", t["alice"], json={"user_id": t["bob"]["id"]})
    assert api.call("POST", f"/channels/{sid}/messages", t["bob"], json={"content": "hi"}).status_code == 201


def test_message_lifecycle(api, team):
    t, gid = team, team["general"]["id"]
    m = api.call("POST", f"/channels/{gid}/messages", t["bob"], json={"content": "hello"}).json()
    # edit: author only
    assert api.call("PATCH", f"/messages/{m['id']}", t["carol"], json={"content": "x"}).status_code == 403
    edited = api.call("PATCH", f"/messages/{m['id']}", t["bob"], json={"content": "hello!"}).json()
    assert edited["content"] == "hello!" and edited["updated_at"]
    # delete: author or admin, not other members
    assert api.call("DELETE", f"/messages/{m['id']}", t["carol"]).status_code == 403
    assert api.call("DELETE", f"/messages/{m['id']}", t["alice"]).status_code == 204
    page = api.call("GET", f"/channels/{gid}/messages", t["bob"]).json()["messages"]
    assert page[0]["deleted_at"] and page[0]["content"] == ""


def test_cursor_pagination(api, team):
    t, gid = team, team["general"]["id"]
    for i in range(7):
        api.call("POST", f"/channels/{gid}/messages", t["bob"], json={"content": f"m{i}"})
    seen, before = [], None
    while True:
        url = f"/channels/{gid}/messages?limit=3" + (f"&before={before}" if before else "")
        d = api.call("GET", url, t["bob"]).json()
        seen += [m["content"] for m in d["messages"]]
        if not d["has_more"]:
            break
        before = d["next_before"]
    assert seen == [f"m{i}" for i in range(6, -1, -1)]


def test_threads_and_reactions(api, team):
    t, gid = team, team["general"]["id"]
    parent = api.call("POST", f"/channels/{gid}/messages", t["alice"], json={"content": "DeployBoard ready"}).json()
    r = api.call("POST", f"/channels/{gid}/messages", t["bob"], json={"content": "Nice", "reply_to": parent["id"]})
    assert r.status_code == 201
    assert api.call("POST", f"/channels/{gid}/messages", t["bob"], json={"content": "x", "reply_to": "nope"}).status_code == 400
    top = api.call("GET", f"/channels/{gid}/messages", t["alice"]).json()["messages"]
    assert [m["id"] for m in top] == [parent["id"]] and top[0]["reply_count"] == 1
    assert len(api.call("GET", f"/messages/{parent['id']}/replies", t["alice"]).json()) == 1

    for u in (t["alice"], t["bob"]):
        assert api.call("PUT", f"/messages/{parent['id']}/reactions/👍", u).status_code == 204
    api.call("PUT", f"/messages/{parent['id']}/reactions/👍", t["bob"])  # idempotent
    top = api.call("GET", f"/channels/{gid}/messages", t["alice"]).json()["messages"]
    assert top[0]["reactions"] == {"👍": 2} and top[0]["mine"] == ["👍"]
    assert api.call("GET", f"/channels/{gid}/messages", t["carol"]).json()["messages"][0]["mine"] == []
    api.call("DELETE", f"/messages/{parent['id']}/reactions/👍", t["bob"])
    top = api.call("GET", f"/channels/{gid}/messages", t["alice"]).json()["messages"]
    assert top[0]["reactions"] == {"👍": 1}


def test_search_respects_acl(api, team):
    t, wid = team, team["ws"]["id"]
    api.call("POST", f"/channels/{t['general']['id']}/messages", t["alice"], json={"content": "PostgreSQL database tuning"})
    api.call("POST", f"/channels/{t['secret']['id']}/messages", t["alice"], json={"content": "secret database password"})
    alice = api.call("GET", f"/workspaces/{wid}/search?q=database", t["alice"]).json()
    bob = api.call("GET", f"/workspaces/{wid}/search?q=database", t["bob"]).json()
    assert len(alice) == 2 and len(bob) == 1 and bob[0]["content"].startswith("PostgreSQL")
    assert api.call("GET", f"/workspaces/{wid}/search?q=%25", t["bob"]).json() == []  # wildcard is escaped


def test_notifications_for_mentions_and_replies(api, team):
    t, gid = team, team["general"]["id"]
    parent = api.call("POST", f"/channels/{gid}/messages", t["alice"], json={"content": "hello"}).json()
    api.call("POST", f"/channels/{gid}/messages", t["bob"], json={"content": "@carol look", "reply_to": parent["id"]})
    kinds = lambda u: sorted(n["kind"] for n in api.call("GET", "/notifications", u).json())
    assert kinds(t["carol"]) == ["invitation", "mention"]
    assert kinds(t["alice"]) == ["reply"]
    n = api.call("GET", "/notifications?unread=true", t["carol"]).json()[0]
    assert api.call("POST", f"/notifications/{n['id']}/read", t["alice"]).status_code == 404
    assert api.call("POST", f"/notifications/{n['id']}/read", t["carol"]).status_code == 204
    assert len(api.call("GET", "/notifications?unread=true", t["carol"]).json()) == 1


def test_private_mention_does_not_leak(api, team):
    t = team
    api.call("POST", f"/channels/{t['secret']['id']}/messages", t["alice"], json={"content": "@bob psst"})
    assert [n["kind"] for n in api.call("GET", "/notifications", t["bob"]).json()] == ["invitation"]


def test_health_and_metrics(client):
    assert client.get("/api/health").json() == {"database": "ok", "redis": "ok", "status": "ok"}
    assert "chatspace_http_requests_total" in client.get("/metrics").text


def test_auth_rate_limit(tmp_path, db_url):
    from fakeredis import FakeAsyncRedis, FakeServer
    from fastapi.testclient import TestClient
    from app.main import create_app
    from tests.conftest import make_settings
    with TestClient(create_app(make_settings(tmp_path, auth_rate_limit=3), redis=FakeAsyncRedis(server=FakeServer()))) as c:
        codes = [c.post("/api/auth/login", json={"email": "a@x.io", "password": "x"}).status_code for _ in range(5)]
    assert codes == [401, 401, 401, 429, 429]
