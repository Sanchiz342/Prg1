import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .api import auth, projects, webhooks, ws
from .config import settings
from .errors import DeployBoardError
from .events import bus
from .jobs import create_queue
from .worker import WorkerPool

log = logging.getLogger("deployboard")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    db.init_db()
    queue = create_queue()
    app.state.queue = queue
    if settings.redis_url:
        bus.attach_redis(settings.redis_url, listen=True)
    pool = WorkerPool(queue) if settings.embedded_workers else None
    app.state.pool = pool
    if pool:
        pool.start()
    yield
    if pool:
        pool.stop()
    bus.detach_redis()
    queue.close()


app = FastAPI(title="DeployBoard", version="0.2.0", lifespan=lifespan)
for r in (auth.router, projects.router, webhooks.router, ws.router):
    app.include_router(r)


@app.exception_handler(DeployBoardError)
async def domain_error(_: Request, e: DeployBoardError):
    headers = {"WWW-Authenticate": "Bearer"} if e.status == 401 else None
    return JSONResponse({"detail": e.message}, status_code=e.status, headers=headers)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# The React build, when present (`npm run build` in frontend/), is served by the API itself.
_dist = settings.frontend_dir
if (_dist / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith(("api/", "ws/")):
            return JSONResponse({"detail": "not found"}, status_code=404)
        file = (_dist / path).resolve()
        if path and file.is_file() and _dist.resolve() in file.parents:
            return FileResponse(file)
        return FileResponse(_dist / "index.html")
