import re
from collections import defaultdict
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Message, Reaction, User, WorkspaceMember


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


async def serialize(s: AsyncSession, messages: list[Message], viewer_id: str | None = None) -> list[dict]:
    """Batch-serialize (3 queries total) to avoid N+1 on a 50-message page."""
    ids = [m.id for m in messages]
    reactions: dict[str, dict[str, int]] = defaultdict(dict)
    replies: dict[str, int] = {}
    mine: dict[str, list[str]] = defaultdict(list)
    if ids:
        rows = await s.execute(
            select(Reaction.message_id, Reaction.reaction, func.count())
            .where(Reaction.message_id.in_(ids))
            .group_by(Reaction.message_id, Reaction.reaction)
        )
        for mid, emoji, n in rows:
            reactions[mid][emoji] = n
        rows = await s.execute(
            select(Message.reply_to, func.count())
            .where(Message.reply_to.in_(ids), Message.deleted_at.is_(None))
            .group_by(Message.reply_to)
        )
        replies = {mid: n for mid, n in rows}
        if viewer_id:
            rows = await s.execute(
                select(Reaction.message_id, Reaction.reaction).where(Reaction.message_id.in_(ids), Reaction.user_id == viewer_id)
            )
            for mid, emoji in rows:
                mine[mid].append(emoji)
    out = [
        {
            "id": m.id,
            "channel_id": m.channel_id,
            "author_id": m.author_id,
            "content": "" if m.deleted_at else m.content,
            "reply_to": m.reply_to,
            "created_at": iso(m.created_at),
            "updated_at": iso(m.updated_at),
            "deleted_at": iso(m.deleted_at),
            "reactions": reactions.get(m.id, {}),
            "reply_count": replies.get(m.id, 0),
        }
        for m in messages
    ]
    if viewer_id:  # per-viewer data: only on REST responses, never in broadcasts
        for d in out:
            d["mine"] = mine.get(d["id"], [])
    return out


MENTION = re.compile(r"(?<!\w)@([A-Za-z0-9_]{3,32})")


async def mentioned_users(s: AsyncSession, content: str, workspace_id: str, exclude: str) -> list[User]:
    names = set(MENTION.findall(content))
    if not names:
        return []
    rows = await s.scalars(
        select(User)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .where(User.username.in_(names), WorkspaceMember.workspace_id == workspace_id, User.id != exclude)
    )
    return list(rows)
