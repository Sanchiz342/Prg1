import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str  # empty -> in-process fakeredis (dev/tests only, single instance)
    jwt_secret: str
    jwt_ttl_minutes: int
    presence_ttl: int
    typing_ttl: int
    auth_rate_limit: int  # requests per minute per client on /auth/*


def load_settings() -> Settings:
    env = os.environ.get
    return Settings(
        database_url=env("DATABASE_URL", "sqlite+aiosqlite:///./chatspace.db"),
        redis_url=env("REDIS_URL", ""),
        jwt_secret=env("JWT_SECRET", "dev-secret-change-me"),
        jwt_ttl_minutes=int(env("JWT_TTL_MINUTES", "720")),
        presence_ttl=int(env("PRESENCE_TTL", "60")),
        typing_ttl=int(env("TYPING_TTL", "5")),
        auth_rate_limit=int(env("AUTH_RATE_LIMIT", "30")),
    )
