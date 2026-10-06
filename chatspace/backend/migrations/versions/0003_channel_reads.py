"""Per-user read markers for unread counters

Existing users get a marker at the newest message of every channel they can currently see, so
upgrading does not turn their whole history into "unread".

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "channel_reads",
        sa.Column("channel_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("last_read_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("channel_id", "user_id"),
    )
    op.execute(
        """
        INSERT INTO channel_reads (channel_id, user_id, last_read_at)
        SELECT c.id, wm.user_id,
               COALESCE((SELECT MAX(m.created_at) FROM messages m WHERE m.channel_id = c.id), c.created_at)
        FROM channels c
        JOIN workspace_members wm ON wm.workspace_id = c.workspace_id
        WHERE c.type = 'PUBLIC'
           OR EXISTS (SELECT 1 FROM channel_members cm WHERE cm.channel_id = c.id AND cm.user_id = wm.user_id)
        """
    )


def downgrade() -> None:
    op.drop_table("channel_reads")
