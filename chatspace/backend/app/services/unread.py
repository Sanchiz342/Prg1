from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Channel, ChannelMember, ChannelRead, Message, Notification, utcnow


def visible_channel_ids(workspace_id: str, user_id: str):
    member_of = select(ChannelMember.channel_id).where(ChannelMember.user_id == user_id)
    return select(Channel.id).where(
        Channel.workspace_id == workspace_id, or_(Channel.type == "PUBLIC", Channel.id.in_(member_of))
    )


async def unread_for_workspace(s: AsyncSession, workspace_id: str, user_id: str) -> dict[str, dict[str, int]]:
    """{channel_id: {"unread": n, "mentions": m}} for channels the user can see (only non-zero entries).

    A message is unread when it is newer than the user's marker (or than the channel's creation when there
    is no marker yet), is not deleted and was not written by the user. Replies count like any message.
    """
    visible = visible_channel_ids(workspace_id, user_id)
    marker = func.coalesce(ChannelRead.last_read_at, Channel.created_at)
    rows = await s.execute(
        select(Message.channel_id, func.count())
        .join(Channel, Channel.id == Message.channel_id)
        .outerjoin(ChannelRead, and_(ChannelRead.channel_id == Message.channel_id, ChannelRead.user_id == user_id))
        .where(
            Message.channel_id.in_(visible), Message.deleted_at.is_(None),
            Message.author_id != user_id, Message.created_at > marker,
        )
        .group_by(Message.channel_id)
    )
    out = {cid: {"unread": n, "mentions": 0} for cid, n in rows}
    rows = await s.execute(
        select(Notification.channel_id, func.count())
        .where(Notification.user_id == user_id, Notification.kind == "mention", Notification.read_at.is_(None),
               Notification.channel_id.in_(visible))
        .group_by(Notification.channel_id)
    )
    for cid, n in rows:
        out.setdefault(cid, {"unread": 0, "mentions": 0})["mentions"] = n
    return out


async def latest_message_time(s: AsyncSession, channel: Channel):
    return await s.scalar(select(func.max(Message.created_at)).where(Message.channel_id == channel.id)) or channel.created_at


async def mark_read(s: AsyncSession, channel: Channel, user_id: str) -> None:
    """Move the marker to the newest message (never backwards) and clear this channel's mention/reply notifications."""
    latest = await latest_message_time(s, channel)
    row = await s.get(ChannelRead, (channel.id, user_id))
    if row is None:
        s.add(ChannelRead(channel_id=channel.id, user_id=user_id, last_read_at=latest))
    elif latest > row.last_read_at:
        row.last_read_at = latest
    await s.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.channel_id == channel.id,
               Notification.read_at.is_(None), Notification.kind.in_(("mention", "reply")))
        .values(read_at=utcnow())
    )
    try:
        await s.commit()
    except IntegrityError:  # two tabs created the first marker at the same moment: the other one won
        await s.rollback()


async def init_markers(s: AsyncSession, user_id: str, channels: list[Channel]) -> None:
    """A user who gains access to existing channels starts "caught up"; only future messages are unread."""
    for ch in channels:
        if await s.get(ChannelRead, (ch.id, user_id)) is None:
            s.add(ChannelRead(channel_id=ch.id, user_id=user_id, last_read_at=await latest_message_time(s, ch)))
    try:
        await s.commit()
    except IntegrityError:
        await s.rollback()
