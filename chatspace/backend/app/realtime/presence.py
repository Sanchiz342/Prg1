"""Ephemeral state (presence, typing) lives only in Redis with TTLs, never in PostgreSQL."""


class Presence:
    def __init__(self, redis, presence_ttl: int, typing_ttl: int):
        self.redis = redis
        self.presence_ttl = presence_ttl
        self.typing_ttl = typing_ttl

    @staticmethod
    def _key(user_id: str) -> str:
        return f"presence:user:{user_id}"

    async def set(self, user_id: str, status: str = "ONLINE") -> None:
        await self.redis.set(self._key(user_id), status, ex=self.presence_ttl)

    async def clear(self, user_id: str) -> None:
        await self.redis.delete(self._key(user_id))

    async def get_many(self, user_ids: list[str]) -> dict[str, str]:
        if not user_ids:
            return {}
        values = await self.redis.mget([self._key(u) for u in user_ids])
        return {u: (v.decode() if isinstance(v, bytes) else v) or "OFFLINE" for u, v in zip(user_ids, values)}

    async def typing(self, channel_id: str, user_id: str) -> None:
        await self.redis.set(f"typing:{channel_id}:{user_id}", "1", ex=self.typing_ttl)

    async def stop_typing(self, channel_id: str, user_id: str) -> None:
        await self.redis.delete(f"typing:{channel_id}:{user_id}")
