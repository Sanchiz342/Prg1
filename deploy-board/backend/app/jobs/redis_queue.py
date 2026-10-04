import redis

KEY = "deployboard:jobs"


class RedisQueue:
    """Redis list shared by the API and any number of worker processes (LPUSH / BRPOP => FIFO)."""

    def __init__(self, url: str, key: str = KEY) -> None:
        self.key = key
        self.client = redis.Redis.from_url(url, socket_timeout=None, socket_connect_timeout=5)

    def enqueue(self, deployment_id: int) -> None:
        self.client.lpush(self.key, deployment_id)

    def reserve(self, timeout: float) -> int | None:
        item = self.client.brpop([self.key], timeout=max(1, int(timeout)))
        return int(item[1]) if item else None

    def size(self) -> int:
        return int(self.client.llen(self.key))

    def close(self) -> None:
        self.client.close()
