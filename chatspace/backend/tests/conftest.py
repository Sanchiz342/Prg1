import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_settings(tmp_path, **kw) -> Settings:
    base = dict(database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db", redis_url="", jwt_secret="test-secret-0123456789-0123456789-xx",
                jwt_ttl_minutes=60, presence_ttl=60, typing_ttl=5, auth_rate_limit=1000)
    return Settings(**{**base, **kw})


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_settings(tmp_path), redis=FakeAsyncRedis(server=FakeServer()))) as c:
        yield c


class Api:
    """Tiny helper: register users and call the API as them."""

    def __init__(self, client: TestClient):
        self.c = client

    def user(self, name: str) -> dict:
        r = self.c.post("/api/auth/register", json={"username": name, "email": f"{name}@x.io", "password": "password123"})
        assert r.status_code == 201, r.text
        d = r.json()
        return {"id": d["user"]["id"], "name": name, "token": d["access_token"], "h": {"Authorization": f"Bearer {d['access_token']}"}}

    def call(self, method: str, url: str, who: dict, **kw):
        return self.c.request(method, "/api" + url, headers=who["h"], **kw)


@pytest.fixture
def api(client):
    return Api(client)


@pytest.fixture
def team(api):
    """alice=OWNER, bob=MEMBER, carol=MEMBER; workspace with #general and private #secret (alice only)."""
    alice, bob, carol = api.user("alice"), api.user("bob"), api.user("carol")
    ws = api.call("POST", "/workspaces", alice, json={"name": "PRG1"}).json()
    for u in (bob, carol):
        assert api.call("POST", f"/workspaces/{ws['id']}/members", alice, json={"email": f"{u['name']}@x.io"}).status_code == 201
    chans = {c["name"]: c for c in api.call("GET", f"/workspaces/{ws['id']}/channels", alice).json()}
    secret = api.call("POST", f"/workspaces/{ws['id']}/channels", alice, json={"name": "secret", "type": "PRIVATE"}).json()
    return dict(alice=alice, bob=bob, carol=carol, ws=ws, general=chans["general"], secret=secret)
