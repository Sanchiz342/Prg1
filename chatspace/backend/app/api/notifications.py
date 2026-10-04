from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import current_user, get_session
from ..models import Notification, User, utcnow
from ..services.notifications import to_dict

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(unread: bool = False, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    q = select(Notification).where(Notification.user_id == user.id)
    if unread:
        q = q.where(Notification.read_at.is_(None))
    return [to_dict(n) for n in await s.scalars(q.order_by(Notification.created_at.desc()).limit(100))]


@router.post("/{notification_id}/read", status_code=204)
async def mark_read(notification_id: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    n = await s.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise HTTPException(404, "Notification not found")
    n.read_at = n.read_at or utcnow()
    await s.commit()
