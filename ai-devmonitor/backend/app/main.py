import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import _detect_in_thread, router
from .core.config import settings
from .core.db import init_db
from .hub import hub

log = logging.getLogger("devmonitor")


async def detection_loop():
    while True:
        await asyncio.sleep(settings.detection_interval_s)
        try:
            changes = await asyncio.to_thread(_detect_in_thread)
            for c in changes:
                await hub.broadcast({"type": f"incident_{c['type']}", "incident": c["incident"]})
            await hub.broadcast({"type": "tick"})
        except Exception:
            log.exception("detection pass failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(detection_loop()) if settings.background_detection else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="AI DevMonitor", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
