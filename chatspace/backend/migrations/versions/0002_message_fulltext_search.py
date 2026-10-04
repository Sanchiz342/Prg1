"""PostgreSQL full-text search over messages

Adds a STORED generated tsvector column (always in sync with `content`, including edits, and
backfilled for existing rows by the ALTER itself) plus a GIN index. The 'simple' configuration is
deliberate: the content is multilingual (e.g. Ukrainian + English) and PostgreSQL ships no Ukrainian
stemmer, so we index normalized word forms and match by prefix at query time.

No-op on other dialects (SQLite dev/test keeps a LIKE fallback).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    encoding = bind.exec_driver_sql("SHOW server_encoding").scalar()
    if encoding != "UTF8":
        # with SQL_ASCII/C the text-search parser silently ignores non-ASCII letters: searches would
        # "work" but never match Ukrainian text. Refuse instead of shipping a broken index.
        raise RuntimeError(f"Database encoding is {encoding!r}; ChatSpace full-text search requires a UTF8 database")
    op.execute(
        "ALTER TABLE messages ADD COLUMN search_vector tsvector "
        "GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, content)) STORED"
    )
    op.execute("CREATE INDEX ix_messages_search_vector ON messages USING GIN (search_vector)")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_messages_search_vector")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS search_vector")
