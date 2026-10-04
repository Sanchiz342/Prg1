from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Channel, ChannelMember, WorkspaceMember

RANK = {"MEMBER": 1, "ADMIN": 2, "OWNER": 3}


async def workspace_role(s: AsyncSession, workspace_id: str, user_id: str) -> str | None:
    return await s.scalar(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id
        )
    )


async def require_workspace(s: AsyncSession, workspace_id: str, user_id: str, min_role: str = "MEMBER") -> str:
    role = await workspace_role(s, workspace_id, user_id)
    if role is None:
        raise HTTPException(404, "Workspace not found")  # don't leak existence
    if RANK[role] < RANK[min_role]:
        raise HTTPException(403, f"{min_role} role required")
    return role


async def can_access_channel(s: AsyncSession, channel: Channel, user_id: str) -> bool:
    if await workspace_role(s, channel.workspace_id, user_id) is None:
        return False
    if channel.type == "PUBLIC":
        return True
    return (
        await s.scalar(
            select(ChannelMember.user_id).where(
                ChannelMember.channel_id == channel.id, ChannelMember.user_id == user_id
            )
        )
        is not None
    )


async def require_channel(s: AsyncSession, channel_id: str, user_id: str) -> Channel:
    channel = await s.get(Channel, channel_id)
    if channel is None or not await can_access_channel(s, channel, user_id):
        raise HTTPException(404, "Channel not found")
    return channel
