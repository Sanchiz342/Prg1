"""Programmatic Alembic helpers + `python -m app.migrate` (the command containers run before the API starts)."""
import asyncio
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from .config import load_settings

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "0001"  # schema that create_all() produced before Alembic was introduced

# PostgreSQL-only full-text objects are managed by raw SQL in migration 0002 and are deliberately
# not mapped on the (dialect-neutral) ORM models, so autogenerate/compare must ignore them.
_FTS_COLUMNS = {"search_vector"}
_FTS_INDEXES = {"ix_messages_search_vector"}


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "column" and name in _FTS_COLUMNS:
        return False
    if type_ == "index" and name in _FTS_INDEXES:
        return False
    return True


def make_config(url: str | None = None) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", (url or load_settings().database_url).replace("%", "%%"))
    return cfg


def head_revision() -> str:
    return ScriptDirectory.from_config(make_config("sqlite://")).get_current_head()


async def _tables(url: str) -> set[str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
    finally:
        await engine.dispose()


async def current_revision(url: str) -> str | None:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(lambda c: MigrationContext.configure(c).get_current_revision())
    finally:
        await engine.dispose()


def upgrade(url: str | None = None, revision: str = "head") -> None:
    """Upgrade (sync; call from a thread when an event loop is running).

    A database created by the old `create_all()` startup has our tables but no alembic_version:
    that schema is exactly revision 0001, so adopt it by stamping instead of failing on "table exists".
    """
    url = url or load_settings().database_url
    cfg = make_config(url)
    tables = asyncio.run(_tables(url))
    if "users" in tables and "alembic_version" not in tables:
        command.stamp(cfg, BASELINE)
    command.upgrade(cfg, revision)


def downgrade(url: str | None, revision: str) -> None:
    command.downgrade(make_config(url), revision)


async def ensure_current(url: str) -> None:
    """Fail fast (instead of failing later with 'no such table') when the DB isn't at the code's schema."""
    have, want = await current_revision(url), head_revision()
    if have != want:
        raise RuntimeError(
            f"Database schema is at revision {have!r} but this build expects {want!r}. "
            "Run `python -m app.migrate` (or `alembic upgrade head`) first, or set AUTO_MIGRATE=true."
        )


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "head"
    upgrade(revision=target)
    print(f"database is at revision {asyncio.run(current_revision(load_settings().database_url))}")
