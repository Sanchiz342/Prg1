import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import events, metrics
from ..deps import current_user, get_session
from ..models import Channel, ChannelMember, Message, Reaction, User, utcnow
from ..permissions import RANK, require_channel, workspace_role
from ..services.messages import mentioned_users, serialize
from ..services.notifications import notify

router = APIRouter(tags=["messages"])


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    reply_to: str | None = None


async def _load(s: AsyncSession, message_id: str, user: User) -> tuple[Message, Channel]:
    m = await s.get(Message, message_id)
    if m is None:
        raise HTTPException(404, "Message not found")
    return m, await require_channel(s, m.channel_id, user.id)  # hides messages of inaccessible channels


@router.post("/channels/{channel_id}/messages", status_code=201)
async def create_message(channel_id: str, body: MessageIn, request: Request, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    ch = await require_channel(s, channel_id, user.id)
    parent = None
    if body.reply_to:
        parent = await s.get(Message, body.reply_to)
        if parent is None or parent.channel_id != channel_id or parent.deleted_at:
            raise HTTPException(400, "Invalid reply_to")
    m = Message(channel_id=channel_id, author_id=user.id, content=body.content, reply_to=body.reply_to)
    s.add(m)
    await s.commit()
    data = (await serialize(s, [m]))[0]
    hub = request.app.state.hub
    await hub.publish(events.channel_room(channel_id), {"type": events.MESSAGE_CREATED, "message": data})
    metrics.counters["chatspace_messages_created_total"] += 1

    notified: set[str] = set()
    if parent and parent.author_id != user.id:
        notified.add(parent.author_id)
        await notify(s, hub, parent.author_id, "reply", f"{user.username} replied to you", m.id, channel_id)
    for u in await mentioned_users(s, body.content, ch.workspace_id, user.id):
        if u.id in notified:
            continue
        if ch.type == "PRIVATE" and await s.get(ChannelMember, (ch.id, u.id)) is None:
            continue  # never reveal private channel content via a mention
        await notify(s, hub, u.id, "mention", f"{user.username} mentioned you in #{ch.name}", m.id, channel_id)
    return data


@router.get("/channels/{channel_id}/messages")
async def list_messages(channel_id: str, limit: int = 50, before: str | None = None, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Cursor pagination: newest first; pass `before=<oldest message id seen>` for the previous page."""
    await require_channel(s, channel_id, user.id)
    limit = min(max(limit, 1), 100)
    q = select(Message).where(Message.channel_id == channel_id, Message.reply_to.is_(None))
    if before:
        cur = await s.get(Message, before)
        if cur is None or cur.channel_id != channel_id:
            raise HTTPException(400, "Invalid cursor")
        q = q.where(or_(Message.created_at < cur.created_at, and_(Message.created_at == cur.created_at, Message.id < cur.id)))
    rows = list(await s.scalars(q.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {"messages": await serialize(s, rows, user.id), "has_more": has_more, "next_before": rows[-1].id if has_more else None}


@router.get("/messages/{message_id}/replies")
async def replies(message_id: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    m, _ = await _load(s, message_id, user)
    rows = await s.scalars(select(Message).where(Message.reply_to == m.id).order_by(Message.created_at, Message.id))
    return await serialize(s, list(rows), user.id)


@router.patch("/messages/{message_id}")
async def edit_message(message_id: str, body: MessageIn, request: Request, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    m, _ = await _load(s, message_id, user)
    if m.author_id != user.id:
        raise HTTPException(403, "Only the author can edit")
    if m.deleted_at:
        raise HTTPException(410, "Message deleted")
    m.content, m.updated_at = body.content, utcnow()
    await s.commit()
    data = (await serialize(s, [m]))[0]
    await request.app.state.hub.publish(events.channel_room(m.channel_id), {"type": events.MESSAGE_UPDATED, "message": data})
    return {**data, "mine": (await serialize(s, [m], user.id))[0]["mine"]}


@router.delete("/messages/{message_id}", status_code=204)
async def delete_message(message_id: str, request: Request, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    m, ch = await _load(s, message_id, user)
    role = await workspace_role(s, ch.workspace_id, user.id)
    if m.author_id != user.id and RANK[role] < RANK["ADMIN"]:
        raise HTTPException(403, "Only the author or an admin can delete")
    if not m.deleted_at:
        m.deleted_at = utcnow()  # soft delete keeps threads intact
        await s.commit()
        await request.app.state.hub.publish(events.channel_room(m.channel_id), {"type": events.MESSAGE_DELETED, "message_id": m.id, "channel_id": m.channel_id})


@router.put("/messages/{message_id}/reactions/{emoji}", status_code=204)
async def add_reaction(message_id: str, emoji: str, request: Request, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    m, _ = await _load(s, message_id, user)
    if not 0 < len(emoji) <= 32:
        raise HTTPException(400, "Invalid reaction")
    if await s.get(Reaction, (m.id, user.id, emoji)) is None:
        s.add(Reaction(message_id=m.id, user_id=user.id, reaction=emoji))
        await s.commit()
        await request.app.state.hub.publish(events.channel_room(m.channel_id), {"type": events.REACTION_ADDED, "message_id": m.id, "user_id": user.id, "reaction": emoji})


@router.delete("/messages/{message_id}/reactions/{emoji}", status_code=204)
async def remove_reaction(message_id: str, emoji: str, request: Request, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    m, _ = await _load(s, message_id, user)
    r = await s.get(Reaction, (m.id, user.id, emoji))
    if r:
        await s.delete(r)
        await s.commit()
        await request.app.state.hub.publish(events.channel_room(m.channel_id), {"type": events.REACTION_REMOVED, "message_id": m.id, "user_id": user.id, "reaction": emoji})


WORD = re.compile(r"\w+")


def _prefix_tsquery(q: str) -> str | None:
    """'data conn' -> "'data':* & 'conn':*". Only \w tokens survive, so no tsquery syntax can be injected."""
    words = WORD.findall(q)[:10]
    return " & ".join(f"'{w}':*" for w in words) if words else None


async def search_messages(s: AsyncSession, workspace_id: str, user_id: str, q: str, limit: int) -> list[dict]:
    """ACL-aware search: only channels the user can see, applied in the same query as the text match.

    PostgreSQL: full-text (GIN-indexed tsvector, AND of prefix terms, ranked). Other dialects (SQLite
    dev/test): case-insensitive substring fallback.
    """
    q = q.strip()
    if not q:
        return []
    member_of = select(ChannelMember.channel_id).where(ChannelMember.user_id == user_id)
    visible = select(Channel.id).where(
        Channel.workspace_id == workspace_id, or_(Channel.type == "PUBLIC", Channel.id.in_(member_of))
    )
    stmt = select(Message).where(Message.channel_id.in_(visible), Message.deleted_at.is_(None))
    if s.get_bind().dialect.name == "postgresql":
        tsq = _prefix_tsquery(q)
        if tsq is None:
            return []
        vector = literal_column("messages.search_vector")
        query = func.to_tsquery("simple", tsq)
        stmt = stmt.where(vector.op("@@")(query)).order_by(func.ts_rank_cd(vector, query).desc(), Message.created_at.desc(), Message.id.desc())
    else:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(Message.content.ilike(pattern, escape="\\")).order_by(Message.created_at.desc(), Message.id.desc())
    rows = list(await s.scalars(stmt.limit(limit)))
    return await serialize(s, rows, user_id)
