import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from .. import events, metrics
from ..models import Channel, User, WorkspaceMember
from ..permissions import can_access_channel
from ..security import decode_token

router = APIRouter()
STATUSES = {"ONLINE", "AWAY"}


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = ""):
    app = ws.app
    st = app.state
    async with st.sessionmaker() as s:
        user_id = decode_token(token, st.settings.jwt_secret)
        user = await s.get(User, user_id) if user_id else None
        if user is None:
            # Accept, then close with an application code: a close *before* accept becomes a bare HTTP 403
            # that browsers report as a generic failure (code 1006), so the client could not tell
            # "token expired -> log out" from "network down -> retry". Nothing is subscribed or sent.
            await ws.accept()
            await ws.close(code=4401)
            return
        workspace_ids = list(await s.scalars(select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id)))
    await ws.accept()
    hub, presence = st.hub, st.presence
    hub.register(user.id, ws)
    hub.join(events.user_room(user.id), ws)
    for wid in workspace_ids:
        hub.join(events.workspace_room(wid), ws)

    st.connections[user.id] = st.connections.get(user.id, 0) + 1
    metrics.gauges["chatspace_ws_connections"] += 1
    first = st.connections[user.id] == 1
    await presence.set(user.id)
    if first:
        for wid in workspace_ids:
            await hub.publish(events.workspace_room(wid), {"type": events.USER_ONLINE, "user_id": user.id})
    await ws.send_json({"type": "ready", "user_id": user.id})

    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type") if isinstance(msg, dict) else None
            cid = msg.get("channel_id") if isinstance(msg, dict) else None
            if kind == "heartbeat":
                status = msg.get("status")
                await presence.set(user.id, status if status in STATUSES else "ONLINE")
            elif kind == "subscribe" and isinstance(cid, str):
                async with st.sessionmaker() as s:
                    channel = await s.get(Channel, cid)
                    ok = channel is not None and await can_access_channel(s, channel, user.id)
                if ok:
                    hub.join(events.channel_room(cid), ws)
                    await ws.send_json({"type": "subscribed", "channel_id": cid})
                else:
                    await ws.send_json({"type": "error", "code": "forbidden", "channel_id": cid})
            elif kind in ("typing.started", "typing.stopped") and isinstance(cid, str):
                if not hub.in_room(events.channel_room(cid), ws):
                    await ws.send_json({"type": "error", "code": "not_subscribed", "channel_id": cid})
                    continue
                if kind == "typing.started":
                    await presence.typing(cid, user.id)
                else:
                    await presence.stop_typing(cid, user.id)
                await hub.publish(events.channel_room(cid), {"type": kind, "channel_id": cid, "user_id": user.id})
            else:
                await ws.send_json({"type": "error", "code": "bad_request"})
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        hub.leave_all(ws)
        metrics.gauges["chatspace_ws_connections"] -= 1
        st.connections[user.id] -= 1
        if st.connections[user.id] <= 0:
            st.connections.pop(user.id, None)
            await presence.clear(user.id)
            for wid in workspace_ids:
                await hub.publish(events.workspace_room(wid), {"type": events.USER_OFFLINE, "user_id": user.id})
