import queue


class MemoryQueue:
    """In-process queue: single-process local mode."""

    def __init__(self) -> None:
        self._q: queue.Queue[int] = queue.Queue()

    def enqueue(self, deployment_id: int) -> None:
        self._q.put(deployment_id)

    def reserve(self, timeout: float) -> int | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def size(self) -> int:
        return self._q.qsize()

    def close(self) -> None:
        pass
