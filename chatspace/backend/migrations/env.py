import asyncio

from alembic import context
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.config import load_settings
from app.db import Base
from app.migrate import include_object

config = context.config
target_metadata = Base.metadata
MIGRATION_LOCK = 727274  # arbitrary app-wide id for the PostgreSQL advisory lock


def db_url() -> str:
    return config.get_main_option("sqlalchemy.url") or load_settings().database_url


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, include_object=include_object,
        compare_type=True, render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        if connection.dialect.name == "postgresql":
            # several backend instances may start together: serialize, the late ones then find nothing to do
            connection.execute(text(f"SELECT pg_advisory_xact_lock({MIGRATION_LOCK})"))
        context.run_migrations()


async def run_async() -> None:
    engine = create_async_engine(db_url())
    try:
        async with engine.connect() as conn:
            await conn.run_sync(do_run_migrations)
    finally:  # a failed migration must release its connection (and with it the advisory lock)
        await engine.dispose()


def run_offline() -> None:
    context.configure(url=db_url(), target_metadata=target_metadata, include_object=include_object,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_async())
