"""In-process pub/sub used to stream live logs / status changes to WebSocket clients.

Workers publish from threads; subscribers are asyncio queues bound to the event loop that
created them. Swap for Redis pub/sub when workers move to separate processes.
"""
import asyncio
import threading
from collections import defaultdict


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[int, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, key: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subs[key].append((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, key: int, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs[key] = [s for s in self._subs[key] if s[1] is not q]

    def publish(self, key: int, event: dict) -> None:
        with self._lock:
            subs = list(self._subs.get(key, ()))
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except RuntimeError:  # loop closed
                pass


bus = EventBus()
