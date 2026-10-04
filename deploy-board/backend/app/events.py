"""Pub/sub for live logs and status changes.

Workers publish from threads; WebSocket handlers subscribe with asyncio queues. In single-process mode
that is all. With Redis configured, every event is also published on a Redis channel and a listener
thread in the API process relays events produced by *other* processes (standalone workers)."""
import asyncio
import json
import logging
import threading
import uuid
from collections import defaultdict

log = logging.getLogger("deployboard.events")
CHANNEL = "deployboard:events"


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[int, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = defaultdict(list)
        self._lock = threading.Lock()
        self._redis = None
        self._origin = uuid.uuid4().hex
        self._stop = threading.Event()
        self._listener: threading.Thread | None = None

    # ---- local subscribers ----
    def subscribe(self, key: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subs[key].append((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, key: int, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs[key] = [s for s in self._subs[key] if s[1] is not q]
            if not self._subs[key]:
                del self._subs[key]

    def _dispatch(self, key: int, event: dict) -> None:
        with self._lock:
            subs = list(self._subs.get(key, ()))
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except RuntimeError:  # loop closed
                pass

    def publish(self, key: int, event: dict) -> None:
        self._dispatch(key, event)
        if self._redis is not None:
            try:
                self._redis.publish(CHANNEL, json.dumps({"origin": self._origin, "key": key, "event": event}))
            except Exception:
                log.warning("could not publish event to redis", exc_info=True)

    # ---- redis bridge ----
    def attach_redis(self, url: str, listen: bool) -> None:
        import redis
        self._redis = redis.Redis.from_url(url)
        if listen:
            self._stop.clear()
            self._listener = threading.Thread(target=self._listen, args=(url,), name="event-listener", daemon=True)
            self._listener.start()

    def _listen(self, url: str) -> None:
        import redis
        sub = redis.Redis.from_url(url).pubsub(ignore_subscribe_messages=True)
        sub.subscribe(CHANNEL)
        while not self._stop.is_set():
            try:
                msg = sub.get_message(timeout=1.0)
                if not msg:
                    continue
                data = json.loads(msg["data"])
                if data["origin"] != self._origin:
                    self._dispatch(data["key"], data["event"])
            except Exception:
                log.warning("event listener error", exc_info=True)
                self._stop.wait(1)
        sub.close()

    def detach_redis(self) -> None:
        self._stop.set()
        if self._listener:
            self._listener.join(timeout=3)
            self._listener = None
        if self._redis is not None:
            self._redis.close()
            self._redis = None


bus = EventBus()
