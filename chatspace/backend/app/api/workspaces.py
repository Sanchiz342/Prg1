from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import events
from ..deps import current_user, get_session
from ..models import Channel, ChannelMember, Message, Notification, Reaction, User, Workspace, WorkspaceMember
from ..permissions import RANK, require_workspace
from ..services.notifications import notify
from .messages import search_messages

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class MemberIn(BaseModel):
    email: str
    role: str = Field(default="MEMBER", pattern="^(ADMIN|MEMBER)$")


class RoleIn(BaseModel):
    role: str = Field(pattern="^(ADMIN|MEMBER)$")


def ws_dict(w: Workspace, role: str | None = None) -> dict:
    return {"id": w.id, "name": w.name, "role": role}


@router.post("", status_code=201)
async def create_workspace(body: WorkspaceIn, request: Request, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    w = Workspace(name=body.name)
    s.add(w)
    await s.flush()
    s.add(WorkspaceMember(workspace_id=w.id, user_id=user.id, role="OWNER"))
    general = Channel(workspace_id=w.id, name="general", type="PUBLIC")
    s.add(general)
    await s.commit()
    # sockets that were opened before this workspace existed must start receiving its events
    await request.app.state.hub.control("join", user.id, [events.workspace_room(w.id)])
    return ws_dict(w, "OWNER")


@router.get("")
async def my_workspaces(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    rows = await s.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
    )
    return [ws_dict(w, r) for w, r in rows]


@router.delete("/{workspace_id}", status_code=204)
async def delete_workspace(workspace_id: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    await require_workspace(s, workspace_id, user.id, "OWNER")
    chan_ids = select(Channel.id).where(Channel.workspace_id == workspace_id)
    msg_ids = select(Message.id).where(Message.channel_id.in_(chan_ids))
    # explicit order: works the same on SQLite (no FK enforcement) and PostgreSQL
    await s.execute(delete(Reaction).where(Reaction.message_id.in_(msg_ids)))
    await s.execute(delete(Notification).where(Notification.message_id.in_(msg_ids)))
    await s.execute(update(Message).where(Message.channel_id.in_(chan_ids)).values(reply_to=None))
    await s.execute(delete(Message).where(Message.channel_id.in_(chan_ids)))
    await s.execute(delete(ChannelMember).where(ChannelMember.channel_id.in_(chan_ids)))
    await s.execute(delete(Channel).where(Channel.workspace_id == workspace_id))
    await s.execute(delete(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id))
    await s.execute(delete(Workspace).where(Workspace.id == workspace_id))
    await s.commit()


@router.get("/{workspace_id}/members")
async def members(workspace_id: str, request: Request, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    await require_workspace(s, workspace_id, user.id)
    rows = (await s.execute(
        select(User, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(User.username)
    )).all()
    status = await request.app.state.presence.get_many([u.id for u, _ in rows])
    return [{"id": u.id, "username": u.username, "role": r, "presence": status[u.id]} for u, r in rows]


@router.post("/{workspace_id}/members", status_code=201)
async def add_member(workspace_id: str, body: MemberIn, request: Request, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    actor = await require_workspace(s, workspace_id, user.id, "ADMIN")
    if body.role == "ADMIN" and actor != "OWNER":
        raise HTTPException(403, "Only OWNER can add admins")
    target = await s.scalar(select(User).where(User.email == body.email.strip().lower()))
    if target is None:
        raise HTTPException(404, "User not found")
    if await s.get(WorkspaceMember, (workspace_id, target.id)):
        raise HTTPException(409, "Already a member")
    s.add(WorkspaceMember(workspace_id=workspace_id, user_id=target.id, role=body.role))
    await s.commit()
    w = await s.get(Workspace, workspace_id)
    await request.app.state.hub.control("join", target.id, [events.workspace_room(workspace_id)])
    await notify(s, request.app.state.hub, target.id, "invitation", f"{user.username} added you to {w.name}")
    return {"user_id": target.id, "role": body.role}


@router.patch("/{workspace_id}/members/{user_id}")
async def set_role(workspace_id: str, user_id: str, body: RoleIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    await require_workspace(s, workspace_id, user.id, "OWNER")
    m = await s.get(WorkspaceMember, (workspace_id, user_id))
    if m is None:
        raise HTTPException(404, "Member not found")
    if m.role == "OWNER":
        raise HTTPException(400, "Cannot change the owner's role")
    m.role = body.role
    await s.commit()
    return {"user_id": user_id, "role": m.role}


@router.delete("/{workspace_id}/members/{user_id}", status_code=204)
async def remove_member(workspace_id: str, user_id: str, request: Request, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    actor = await require_workspace(s, workspace_id, user.id)
    m = await s.get(WorkspaceMember, (workspace_id, user_id))
    if m is None:
        raise HTTPException(404, "Member not found")
    if m.role == "OWNER":
        raise HTTPException(400, "Owner cannot be removed")
    if user_id != user.id and RANK[actor] <= RANK[m.role]:
        raise HTTPException(403, "Insufficient role")
    await s.delete(m)
    await s.commit()
    # revoke live access too: workspace events and every channel room of this workspace
    channel_ids = await s.scalars(select(Channel.id).where(Channel.workspace_id == workspace_id))
    rooms = [events.workspace_room(workspace_id), *(events.channel_room(c) for c in channel_ids)]
    await request.app.state.hub.control("leave", user_id, rooms)


@router.get("/{workspace_id}/search")
async def search(workspace_id: str, q: str, limit: int = 20, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    await require_workspace(s, workspace_id, user.id)
    return await search_messages(s, workspace_id, user.id, q, min(max(limit, 1), 50))
