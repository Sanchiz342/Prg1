import asyncio

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from fakeredis import FakeAsyncRedis, FakeServer
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app import migrate, models  # noqa: F401
from app.db import Base
from app.main import create_app
from tests.conftest import PG_URL, fetch_all, make_settings

pg_only = pytest.mark.skipif(not PG_URL, reason="needs PostgreSQL: set TEST_DATABASE_URL=postgresql+asyncpg://...")

APP_TABLES = {"users", "workspaces", "workspace_members", "channels", "channel_members",
              "messages", "message_reactions", "notifications"}
EXPECTED_INDEXES = {
    "users": {"ix_users_email", "ix_users_username"},
    "channels": {"ix_channels_workspace_id"},
    "messages": {"ix_messages_channel_created", "ix_messages_reply_to"},
    "notifications": {"ix_notifications_user_id"},
}


def run(coro):
    return asyncio.run(coro)


async def _inspect(url, fn):
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(lambda c: fn(inspect(c)))
    finally:
        await engine.dispose()


def tables(url):
    return run(_inspect(url, lambda i: set(i.get_table_names())))


def index_names(url, table):
    return run(_inspect(url, lambda i: {ix["name"] for ix in i.get_indexes(table)}))


def drift(url):
    async def go():
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                def f(sync_conn):
                    ctx = MigrationContext.configure(sync_conn, opts={"compare_type": True, "include_object": migrate.include_object})
                    return compare_metadata(ctx, Base.metadata)
                return await conn.run_sync(f)
        finally:
            await engine.dispose()
    return run(go())


def create_all_legacy(url):
    """What the app did before Alembic: Base.metadata.create_all() and nothing else."""
    async def go():
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()
    run(go())


def seed(url):
    """One user/workspace/channel/message written with plain SQL (independent of the ORM)."""
    async def go():
        from sqlalchemy import text
        engine = create_async_engine(url)
        async with engine.begin() as c:
            await c.execute(text("INSERT INTO users (id, username, email, password_hash, created_at) VALUES ('u1','alice','a@x.io','h','2025-01-01 00:00:00')"))
            await c.execute(text("INSERT INTO workspaces (id, name, created_at) VALUES ('w1','PRG1','2025-01-01 00:00:00')"))
            await c.execute(text("INSERT INTO workspace_members (workspace_id, user_id, role) VALUES ('w1','u1','OWNER')"))
            await c.execute(text("INSERT INTO channels (id, workspace_id, name, type, created_at) VALUES ('c1','w1','general','PUBLIC','2025-01-01 00:00:00')"))
            await c.execute(text("INSERT INTO messages (id, channel_id, author_id, content, created_at) VALUES ('m1','c1','u1','legacy database tuning notes','2025-01-01 00:00:00')"))
        await engine.dispose()
    run(go())


# ---------------------------------------------------------------- both dialects

def test_fresh_database_upgrades_to_head(db_url):
    migrate.upgrade(db_url)
    assert run(migrate.current_revision(db_url)) == migrate.head_revision() == "0002"
    assert tables(db_url) == APP_TABLES | {"alembic_version"}


def test_upgrade_is_idempotent(db_url):
    migrate.upgrade(db_url)
    migrate.upgrade(db_url)
    assert run(migrate.current_revision(db_url)) == "0002"


def test_migrations_match_models(db_url):
    migrate.upgrade(db_url)
    assert drift(db_url) == [], "models changed without a migration: run `alembic revision --autogenerate`"


def test_expected_indexes_exist(db_url):
    migrate.upgrade(db_url)
    for table, wanted in EXPECTED_INDEXES.items():
        assert wanted <= index_names(db_url, table), table


def test_downgrade_to_base_and_back(db_url):
    migrate.upgrade(db_url)
    migrate.downgrade(db_url, "base")
    assert tables(db_url) == {"alembic_version"}
    migrate.upgrade(db_url)
    assert tables(db_url) == APP_TABLES | {"alembic_version"}


def test_database_created_by_old_create_all_is_adopted_without_data_loss(db_url):
    create_all_legacy(db_url)  # no alembic_version table, like every pre-Alembic deployment
    seed(db_url)
    migrate.upgrade(db_url)
    assert run(migrate.current_revision(db_url)) == "0002"
    assert fetch_all(db_url, "select content from messages") == [("legacy database tuning notes",)]
    assert drift(db_url) == []


def test_app_refuses_to_start_on_unmigrated_database(tmp_path, db_url):
    settings = make_settings(tmp_path, auto_migrate=False)
    with pytest.raises(RuntimeError, match="python -m app.migrate"):
        with TestClient(create_app(settings, redis=FakeAsyncRedis(server=FakeServer()))):
            pass


def test_app_starts_once_migrated_without_auto_migrate(tmp_path, db_url):
    migrate.upgrade(db_url)
    with TestClient(create_app(make_settings(tmp_path, auto_migrate=False), redis=FakeAsyncRedis(server=FakeServer()))) as c:
        assert c.get("/api/health").json()["status"] == "ok"


# ---------------------------------------------------------------- PostgreSQL only

@pg_only
def test_gin_index_and_generated_column(db_url):
    migrate.upgrade(db_url)
    (defn,) = fetch_all(db_url, "select indexdef from pg_indexes where indexname = 'ix_messages_search_vector'")[0]
    assert "USING gin (search_vector)" in defn
    (generated,) = fetch_all(db_url, "select is_generated from information_schema.columns where table_name='messages' and column_name='search_vector'")[0]
    assert generated == "ALWAYS"


@pg_only
def test_existing_messages_are_backfilled_when_upgrading_0001_to_0002(db_url):
    migrate.upgrade(db_url, "0001")
    seed(db_url)  # data written before FTS existed
    assert "search_vector" not in {c for (c,) in fetch_all(db_url, "select column_name from information_schema.columns where table_name='messages'")}
    migrate.upgrade(db_url)
    assert fetch_all(db_url, "select id from messages where search_vector @@ to_tsquery('simple', 'tuning:*')") == [("m1",)]


@pg_only
def test_downgrade_from_0002_drops_fts_but_keeps_data(db_url):
    migrate.upgrade(db_url)
    seed(db_url)
    migrate.downgrade(db_url, "0001")
    cols = {c for (c,) in fetch_all(db_url, "select column_name from information_schema.columns where table_name='messages'")}
    assert "search_vector" not in cols
    assert not fetch_all(db_url, "select 1 from pg_indexes where indexname='ix_messages_search_vector'")
    assert fetch_all(db_url, "select count(*) from messages") == [(1,)]


@pg_only
def test_concurrent_instances_can_migrate_at_the_same_time(db_url):
    """Real deployments start several containers at once: separate processes, one advisory lock."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = {**os.environ, "DATABASE_URL": db_url}
    backend = Path(__file__).resolve().parents[1]
    procs = [subprocess.Popen([sys.executable, "-m", "app.migrate"], cwd=backend, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) for _ in range(4)]
    results = [(p.wait(timeout=60), p.stdout.read()) for p in procs]
    assert [code for code, _ in results] == [0, 0, 0, 0], results
    assert run(migrate.current_revision(db_url)) == "0002"
    assert fetch_all(db_url, "select count(*) from alembic_version") == [(1,)]


# ---------------------------------------------------------------- search behaviour (PostgreSQL FTS)

def _post(api, who, channel, text):
    r = api.call("POST", f"/channels/{channel['id']}/messages", who, json={"content": text})
    assert r.status_code == 201
    return r.json()


def _search(api, who, wid, q):
    r = api.call("GET", f"/workspaces/{wid}/search", who, params={"q": q})
    assert r.status_code == 200, r.text
    return [m["content"] for m in r.json()]


@pg_only
def test_fts_prefix_and_all_terms(api, team):
    t, wid = team, team["ws"]["id"]
    _post(api, t["alice"], t["general"], "PostgreSQL database connection pool")
    _post(api, t["alice"], t["general"], "database backup schedule")
    assert len(_search(api, t["bob"], wid, "datab")) == 2                       # prefix
    assert _search(api, t["bob"], wid, "DATABASE pool") == ["PostgreSQL database connection pool"]  # case-insens. AND
    assert _search(api, t["bob"], wid, "database nonexistent") == []


@pg_only
def test_fts_cyrillic(api, team):
    t, wid = team, team["ws"]["id"]
    _post(api, t["alice"], t["general"], "Налаштували базу даних для продакшену")
    assert _search(api, t["bob"], wid, "БАЗУ дани") == ["Налаштували базу даних для продакшену"]


@pg_only
@pytest.mark.parametrize("q", ["'; drop table users; --", "a & | ! ( ) :*", "\\", "%_", "((", "!!!", "'", "x" * 500])
def test_fts_hostile_queries_never_error_or_inject(api, team, q):
    t = team
    _post(api, t["alice"], t["general"], "hello world")
    assert isinstance(_search(api, t["bob"], t["ws"]["id"], q), list)
    assert api.call("GET", "/auth/me", t["alice"]).status_code == 200  # users table still there


@pg_only
def test_fts_index_follows_edits_and_deletes(api, team):
    t, wid = team, team["ws"]["id"]
    m = _post(api, t["alice"], t["general"], "deploy staging tomorrow")
    api.call("PATCH", f"/messages/{m['id']}", t["alice"], json={"content": "rollback production today"})
    assert _search(api, t["bob"], wid, "staging") == []
    assert _search(api, t["bob"], wid, "rollback") == ["rollback production today"]
    api.call("DELETE", f"/messages/{m['id']}", t["alice"])
    assert _search(api, t["bob"], wid, "rollback") == []


@pg_only
def test_fts_ranks_better_matches_first(api, team):
    t, wid = team, team["ws"]["id"]
    _post(api, t["alice"], t["general"], "deploy once")
    _post(api, t["alice"], t["general"], "deploy deploy deploy pipeline deploy")
    assert _search(api, t["bob"], wid, "deploy")[0].startswith("deploy deploy")


@pg_only
def test_fts_never_returns_messages_the_user_cannot_access(api, team):
    t, wid = team, team["ws"]["id"]
    _post(api, t["alice"], t["general"], "launch codename falcon (public)")
    _post(api, t["alice"], t["secret"], "launch codename falcon (private)")
    # a different workspace the searcher is not in
    other = api.user("mallory")
    ows = api.call("POST", "/workspaces", other, json={"name": "other"}).json()
    ogen = api.call("GET", f"/workspaces/{ows['id']}/channels", other).json()[0]
    _post(api, other, ogen, "launch codename falcon (other workspace)")

    assert _search(api, t["alice"], wid, "falcon") == ["launch codename falcon (private)", "launch codename falcon (public)"]
    assert _search(api, t["bob"], wid, "falcon") == ["launch codename falcon (public)"]
    assert _search(api, other, ows["id"], "falcon") == ["launch codename falcon (other workspace)"]
    assert api.call("GET", f"/workspaces/{wid}/search", other, params={"q": "falcon"}).status_code == 404
    # access granted later -> becomes searchable; no stale per-user index involved
    api.call("POST", f"/channels/{t['secret']['id']}/members", t["alice"], json={"user_id": t["bob"]["id"]})
    assert len(_search(api, t["bob"], wid, "falcon")) == 2


@pg_only
def test_fts_query_uses_the_gin_index(api, team, db_url):
    from sqlalchemy import text

    async def plan():
        engine = create_async_engine(db_url)
        async with engine.begin() as c:
            await c.execute(text("SET LOCAL enable_seqscan = off"))
            rows = await c.execute(text("EXPLAIN SELECT id FROM messages WHERE search_vector @@ to_tsquery('simple', 'falcon:*')"))
            out = "\n".join(r[0] for r in rows)
        await engine.dispose()
        return out
    assert "ix_messages_search_vector" in run(plan())
