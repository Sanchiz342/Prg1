import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import db
from ..db import FINISHED
from ..db import repositories as repo
from ..errors import DeployBoardError
from ..events import bus
from ..services import auth, projects

router = APIRouter()


@router.websocket("/ws/deployments/{did}")
async def ws_deployment(ws: WebSocket, did: int, token: str = ""):
    """Replays stored logs, then streams new ones. The bearer token travels as ?token= because
    browsers cannot set headers on WebSocket handshakes."""
    with db.new_session() as s:
        try:
            user = auth.authenticate(s, token)
            projects.get_deployment(s, user, did)
        except DeployBoardError:
            await ws.close(code=4401)
            return
    await ws.accept()
    q = bus.subscribe(did)  # subscribe before replaying so nothing is lost in between
    try:
        last = 0

        async def replay() -> str:
            """Send stored logs newer than `last`; return the deployment status read *before* the logs,
            so a FINISHED status guarantees every log line has been sent."""
            nonlocal last
            with db.new_session() as s:
                status = repo.get_deployment(s, did).status
                rows = repo.logs_after(s, did, last)
            for row in rows:
                last = row.id
                await ws.send_json({"type": "log", "id": row.id, "stage": row.stage, "line": row.line, "ts": row.ts.isoformat()})
            return status

        status = await replay()
        await ws.send_json({"type": "deployment", "status": status})
        if status in FINISHED:
            return
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=15)
            except asyncio.TimeoutError:
                # an event may have been missed (e.g. published by another process): re-check the database
                status = await replay()
                if status in FINISHED:
                    await ws.send_json({"type": "deployment", "status": status})
                    return
                continue
            if ev["type"] == "log" and ev["id"] <= last:
                continue  # already sent
            if ev["type"] == "log":
                last = ev["id"]
            await ws.send_json(ev)
            if ev["type"] == "deployment" and ev["status"] in FINISHED:
                return
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(did, q)
