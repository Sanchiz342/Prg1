from sqlalchemy.ext.asyncio import AsyncSession

from .. import events
from ..models import Notification
from .messages import iso


def to_dict(n: Notification) -> dict:
    return {
        "id": n.id, "kind": n.kind, "message_id": n.message_id, "channel_id": n.channel_id,
        "text": n.text, "read_at": iso(n.read_at), "created_at": iso(n.created_at),
    }


async def notify(s: AsyncSession, hub, user_id: str, kind: str, text: str,
                 message_id: str | None = None, channel_id: str | None = None) -> None:
    n = Notification(user_id=user_id, kind=kind, text=text[:300], message_id=message_id, channel_id=channel_id)
    s.add(n)
    await s.commit()
    await hub.publish(events.user_room(user_id), {"type": events.NOTIFICATION_CREATED, "notification": to_dict(n)})
