from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import events
from ..deps import current_user, get_session
from ..models import Channel, ChannelMember, User, WorkspaceMember
from ..permissions import require_channel, require_workspace

router = APIRouter(tags=["channels"])


class ChannelIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    type: str = Field(default="PUBLIC", pattern="^(PUBLIC|PRIVATE)$")


class ChannelMemberIn(BaseModel):
    user_id: str


def ch_dict(c: Channel) -> dict:
    return {"id": c.id, "workspace_id": c.workspace_id, "name": c.name, "type": c.type}


@router.post("/workspaces/{workspace_id}/channels", status_code=201)
async def create_channel(workspace_id: str, body: ChannelIn, request: Request, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    await require_workspace(s, workspace_id, user.id, "ADMIN")
    c = Channel(workspace_id=workspace_id, name=body.name, type=body.type)
    s.add(c)
    try:
        await s.flush()
    except IntegrityError:
        await s.rollback()
        raise HTTPException(409, "Channel name already exists")
    s.add(ChannelMember(channel_id=c.id, user_id=user.id))
    await s.commit()
    if c.type == "PUBLIC":
        await request.app.state.hub.publish(events.workspace_room(workspace_id), {"type": events.CHANNEL_CREATED, "channel": ch_dict(c)})
    else:
        await request.app.state.hub.publish(events.user_room(user.id), {"type": events.CHANNEL_CREATED, "channel": ch_dict(c)})
    return ch_dict(c)


@router.get("/workspaces/{workspace_id}/channels")
async def list_channels(workspace_id: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    await require_workspace(s, workspace_id, user.id)
    member_of = select(ChannelMember.channel_id).where(ChannelMember.user_id == user.id)
    rows = await s.scalars(
        select(Channel)
        .where(Channel.workspace_id == workspace_id, or_(Channel.type == "PUBLIC", Channel.id.in_(member_of)))
        .order_by(Channel.name)
    )
    return [ch_dict(c) for c in rows]


@router.delete("/channels/{channel_id}", status_code=204)
async def delete_channel(channel_id: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    from sqlalchemy import delete, update  # noqa: PLC0415
    from ..models import Message, Notification, Reaction  # noqa: PLC0415
    c = await require_channel(s, channel_id, user.id)
    await require_workspace(s, c.workspace_id, user.id, "ADMIN")
    msg_ids = select(Message.id).where(Message.channel_id == c.id)
    await s.execute(delete(Reaction).where(Reaction.message_id.in_(msg_ids)))
    await s.execute(delete(Notification).where(Notification.message_id.in_(msg_ids)))
    await s.execute(update(Message).where(Message.channel_id == c.id).values(reply_to=None))
    await s.execute(delete(Message).where(Message.channel_id == c.id))
    await s.execute(delete(ChannelMember).where(ChannelMember.channel_id == c.id))
    await s.delete(c)
    await s.commit()


@router.post("/channels/{channel_id}/members", status_code=201)
async def add_channel_member(channel_id: str, body: ChannelMemberIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    c = await require_channel(s, channel_id, user.id)
    await require_workspace(s, c.workspace_id, user.id, "ADMIN")
    if await s.get(WorkspaceMember, (c.workspace_id, body.user_id)) is None:
        raise HTTPException(404, "User is not a workspace member")
    if await s.get(ChannelMember, (c.id, body.user_id)) is None:
        s.add(ChannelMember(channel_id=c.id, user_id=body.user_id))
        await s.commit()
    return {"channel_id": c.id, "user_id": body.user_id}
