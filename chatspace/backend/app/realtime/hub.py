"""Connection hub: local WebSocket rooms fed by Redis Pub/Sub.

Every instance publishes to one Redis channel and every instance listens to it,
so an event raised on instance A reaches sockets connected to instance B.
"""
import asyncio
import json
import logging

from fastapi import WebSocket

from .. import metrics

log = logging.getLogger("chatspace.hub")
BUS = "chatspace:events"


class Hub:
    def __init__(self, redis):
        self.redis = redis
        self.rooms: dict[str, set[WebSocket]] = {}
        self.sockets_by_user: dict[str, set[WebSocket]] = {}
        self._owner: dict[WebSocket, str] = {}
        self._pubsub = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._pubsub = self.redis.pubsub()
        await self._pubsub.subscribe(BUS)
        self._task = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._pubsub:
            await self._pubsub.aclose()

    def register(self, user_id: str, ws: WebSocket) -> None:
        self.sockets_by_user.setdefault(user_id, set()).add(ws)
        self._owner[ws] = user_id

    def join(self, room: str, ws: WebSocket) -> None:
        self.rooms.setdefault(room, set()).add(ws)

    def leave(self, room: str, ws: WebSocket) -> None:
        self.rooms.get(room, set()).discard(ws)
        if room in self.rooms and not self.rooms[room]:
            del self.rooms[room]

    async def control(self, op: str, user_id: str, rooms: list[str]) -> None:
        """Membership changed over REST: make the user's *live sockets on every instance* join/leave rooms."""
        await self.redis.publish(BUS, json.dumps({"op": op, "user_id": user_id, "rooms": rooms}))

    def leave_all(self, ws: WebSocket) -> None:
        user_id = self._owner.pop(ws, None)
        if user_id:
            self.sockets_by_user.get(user_id, set()).discard(ws)
            if not self.sockets_by_user.get(user_id):
                self.sockets_by_user.pop(user_id, None)
        for room in [r for r, s in self.rooms.items() if ws in s]:
            self.rooms[room].discard(ws)
            if not self.rooms[room]:
                del self.rooms[room]

    def in_room(self, room: str, ws: WebSocket) -> bool:
        return ws in self.rooms.get(room, ())

    async def publish(self, room: str, event: dict) -> None:
        await self.redis.publish(BUS, json.dumps({"room": room, "event": event}))
        metrics.counters["chatspace_events_published_total"] += 1

    async def _listen(self) -> None:
        while True:
            try:
                msg = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg is None:
                    await asyncio.sleep(0.01)
                    continue
                data = json.loads(msg["data"])
                if "op" in data:
                    for ws in list(self.sockets_by_user.get(data["user_id"], ())):
                        for room in data["rooms"]:
                            (self.join if data["op"] == "join" else self.leave)(room, ws)
                else:
                    await self._deliver(data["room"], data["event"])
            except asyncio.CancelledError:
                raise
            except Exception:  # keep the bus alive on bad frames / transient redis errors
                log.exception("hub listener error")
                await asyncio.sleep(0.5)

    async def _deliver(self, room: str, event: dict) -> None:
        for ws in list(self.rooms.get(room, ())):
            try:
                await ws.send_json(event)
            except Exception:
                self.leave_all(ws)
