import asyncio
import os

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Set TEST_DATABASE_URL=postgresql+asyncpg://... to run the whole suite against PostgreSQL
# (each test starts from an empty schema and gets it through the Alembic migrations).
PG_URL = os.environ.get("TEST_DATABASE_URL", "")


def make_settings(tmp_path, **kw) -> Settings:
    base = dict(database_url=PG_URL or f"sqlite+aiosqlite:///{tmp_path}/t.db", redis_url="", jwt_secret="test-secret-0123456789-0123456789-xx",
                jwt_ttl_minutes=60, presence_ttl=60, typing_ttl=5, auth_rate_limit=1000, auto_migrate=True)
    return Settings(**{**base, **kw})


async def _reset_schema(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


def fetch_all(url: str, sql: str, **params) -> list[tuple]:
    async def run():
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                return [tuple(r) for r in await conn.execute(text(sql), params)]
        finally:
            await engine.dispose()
    return asyncio.run(run())


@pytest.fixture
def db_url(tmp_path) -> str:
    if PG_URL:
        asyncio.run(_reset_schema(PG_URL))
    return make_settings(tmp_path).database_url


@pytest.fixture
def client(tmp_path, db_url):
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
