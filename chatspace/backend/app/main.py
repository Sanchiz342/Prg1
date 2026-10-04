import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from . import metrics
from .api import auth, channels, messages, notifications, workspaces
from .config import Settings, load_settings
from .db import Base, make_engine, make_sessionmaker
from .realtime import ws
from .realtime.hub import Hub
from .realtime.presence import Presence


def make_redis(url: str):
    if url:
        import redis.asyncio as aioredis
        return aioredis.from_url(url)
    from fakeredis import FakeAsyncRedis  # dev/tests only: single process, not shared
    return FakeAsyncRedis()


def create_app(settings: Settings | None = None, redis=None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        app.state.redis = redis or make_redis(settings.redis_url)
        app.state.hub = Hub(app.state.redis)
        app.state.presence = Presence(app.state.redis, settings.presence_ttl, settings.typing_ttl)
        app.state.connections = {}
        await app.state.hub.start()
        yield
        await app.state.hub.stop()
        await app.state.redis.aclose()
        await engine.dispose()

    app = FastAPI(title="ChatSpace", lifespan=lifespan)
    app.state.settings = settings

    @app.middleware("http")
    async def count_requests(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        metrics.counters["chatspace_http_requests_total"] += 1
        metrics.counters["chatspace_http_request_seconds_sum"] += time.perf_counter() - start
        return response

    for r in (auth.router, workspaces.router, channels.router, messages.router, notifications.router):
        app.include_router(r, prefix="/api")
    app.include_router(ws.router)

    @app.get("/api/health")
    async def health(request: Request):
        st = request.app.state
        out = {}
        try:
            async with st.sessionmaker() as s:
                await s.execute(text("SELECT 1"))
            out["database"] = "ok"
        except Exception:
            out["database"] = "down"
        try:
            await st.redis.ping()
            out["redis"] = "ok"
        except Exception:
            out["redis"] = "down"
        out["status"] = "ok" if all(v == "ok" for v in out.values()) else "degraded"
        return out

    @app.get("/metrics", response_class=PlainTextResponse)
    async def prometheus():
        return metrics.render()

    static = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="frontend")
    return app



def get_app() -> FastAPI:  # uvicorn app.main:get_app --factory
    return create_app()
