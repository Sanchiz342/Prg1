from ..config import settings
from .base import JobQueue
from .memory import MemoryQueue


def create_queue() -> JobQueue:
    if settings.redis_url:
        from .redis_queue import RedisQueue
        return RedisQueue(settings.redis_url)
    return MemoryQueue()


__all__ = ["JobQueue", "MemoryQueue", "create_queue"]
