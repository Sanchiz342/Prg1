import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import crypto, db, github, schemas
from .config import settings
from .events import bus
from .pipeline.definition import DEFAULT_PIPELINE, validate
from .worker import WorkerPool

STATIC = Path(__file__).parent / "static"
pool: WorkerPool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    db.init_db()
    pool = WorkerPool(settings.workers)
    if settings.start_workers:
        pool.start()
        pool.recover()
    yield
    pool.stop()


app = FastAPI(title="DeployBoard", version="0.1.0", lifespan=lifespan)


def get_db():
    with db.SessionLocal() as s:
        yield s


def _project(s: Session, pid: int) -> db.Project:
    p = s.get(db.Project, pid)
    if not p:
        raise HTTPException(404, "project not found")
    return p


def _project_out(p: db.Project) -> schemas.ProjectOut:
    out = schemas.ProjectOut.model_validate(p)
    out.last_deployment = schemas.DeploymentOut.model_validate(p.deployments[0]) if p.deployments else None
    return out


def _queue(s: Session, p: db.Project, **kw) -> db.Deployment:
    d = db.Deployment(project_id=p.id, **kw)
    s.add(d)
    s.commit()
    pool.enqueue(d.id)
    return d


@app.get("/api/health")
def health():
    return {"status": "ok"}


# ---- projects -----------------------------------------------------------------
@app.post("/api/projects", response_model=schemas.ProjectOut, status_code=201)
def create_project(body: schemas.ProjectIn, s: Session = Depends(get_db)):
    if s.scalar(select(db.Project).where(db.Project.name == body.name)):
        raise HTTPException(409, "project name already exists")
    try:
        pipeline = validate(body.pipeline) if body.pipeline else DEFAULT_PIPELINE
    except ValueError as e:
        raise HTTPException(422, str(e))
    p = db.Project(**body.model_dump(exclude={"pipeline"}), pipeline=pipeline, webhook_secret=secrets.token_hex(20))
    s.add(p)
    s.commit()
    return _project_out(p)


@app.get("/api/projects", response_model=list[schemas.ProjectOut])
def list_projects(s: Session = Depends(get_db)):
    return [_project_out(p) for p in s.scalars(select(db.Project).order_by(db.Project.name))]


@app.get("/api/projects/{pid}", response_model=schemas.ProjectOut)
def get_project(pid: int, s: Session = Depends(get_db)):
    return _project_out(_project(s, pid))


@app.patch("/api/projects/{pid}", response_model=schemas.ProjectOut)
def patch_project(pid: int, body: schemas.ProjectPatch, s: Session = Depends(get_db)):
    p = _project(s, pid)
    data = body.model_dump(exclude_unset=True)
    if "pipeline" in data:
        try:
            data["pipeline"] = validate(data["pipeline"])
        except ValueError as e:
            raise HTTPException(422, str(e))
    for k, v in data.items():
        setattr(p, k, v)
    s.commit()
    return _project_out(p)


@app.delete("/api/projects/{pid}", status_code=204)
def delete_project(pid: int, s: Session = Depends(get_db)):
    p = _project(s, pid)
    s.query(db.LogLine).filter(db.LogLine.deployment_id.in_([d.id for d in p.deployments])).delete()
    s.delete(p)
    s.commit()


# ---- variables / secrets --------------------------------------------------------
@app.get("/api/projects/{pid}/variables", response_model=list[schemas.VariableOut])
def list_variables(pid: int, s: Session = Depends(get_db)):
    return [schemas.VariableOut(key=v.key, is_secret=v.is_secret, value="••••••••" if v.is_secret else v.value)
            for v in _project(s, pid).variables]


@app.put("/api/projects/{pid}/variables", status_code=204)
def put_variable(pid: int, body: schemas.VariableIn, s: Session = Depends(get_db)):
    p = _project(s, pid)
    stored = crypto.encrypt(body.value) if body.is_secret else body.value
    existing = next((v for v in p.variables if v.key == body.key), None)
    if existing:
        existing.value, existing.is_secret = stored, body.is_secret
    else:
        p.variables.append(db.Variable(key=body.key, value=stored, is_secret=body.is_secret))
    s.commit()


@app.delete("/api/projects/{pid}/variables/{key}", status_code=204)
def delete_variable(pid: int, key: str, s: Session = Depends(get_db)):
    p = _project(s, pid)
    v = next((v for v in p.variables if v.key == key), None)
    if not v:
        raise HTTPException(404, "variable not found")
    p.variables.remove(v)
    s.commit()


# ---- deployments ----------------------------------------------------------------
@app.post("/api/projects/{pid}/deploy", response_model=schemas.DeploymentOut, status_code=202)
def deploy(pid: int, s: Session = Depends(get_db)):
    p = _project(s, pid)
    return _queue(s, p, branch=p.branch, trigger="manual")


@app.get("/api/projects/{pid}/deployments", response_model=list[schemas.DeploymentOut])
def list_deployments(pid: int, limit: int = 50, s: Session = Depends(get_db)):
    _project(s, pid)
    q = select(db.Deployment).where(db.Deployment.project_id == pid).order_by(db.Deployment.id.desc()).limit(limit)
    return list(s.scalars(q))


def _deployment(s: Session, did: int) -> db.Deployment:
    d = s.get(db.Deployment, did)
    if not d:
        raise HTTPException(404, "deployment not found")
    return d


@app.get("/api/deployments/{did}", response_model=schemas.DeploymentOut)
def get_deployment(did: int, s: Session = Depends(get_db)):
    return _deployment(s, did)


@app.get("/api/deployments/{did}/logs", response_model=list[schemas.LogOut])
def get_logs(did: int, after: int = 0, s: Session = Depends(get_db)):
    _deployment(s, did)
    return list(s.scalars(select(db.LogLine).where(db.LogLine.deployment_id == did, db.LogLine.id > after).order_by(db.LogLine.id)))


@app.post("/api/deployments/{did}/rollback", response_model=schemas.DeploymentOut, status_code=202)
def rollback(did: int, s: Session = Depends(get_db)):
    """Redeploy a previously built image. If `did` itself succeeded it is the target;
    otherwise the most recent earlier successful deployment is used."""
    d = _deployment(s, did)
    if d.status == "SUCCESS" and d.image:
        target = d
    else:
        target = s.scalar(select(db.Deployment).where(
            db.Deployment.project_id == d.project_id, db.Deployment.id < d.id,
            db.Deployment.status == "SUCCESS", db.Deployment.image != "").order_by(db.Deployment.id.desc()))
    if not target:
        raise HTTPException(409, "no earlier successful deployment with an image to roll back to")
    p = _project(s, d.project_id)
    return _queue(s, p, trigger="rollback", rollback_of=target.id, image=target.image,
                  commit=target.commit, branch=target.branch, author=target.author)


# ---- GitHub webhook ---------------------------------------------------------------
@app.post("/api/webhooks/github")
async def github_webhook(request: Request, x_github_event: str = Header(""), x_hub_signature_256: str | None = Header(None),
                         s: Session = Depends(get_db)):
    body = await request.body()
    if x_github_event == "ping":
        return {"ok": True}
    if x_github_event != "push":
        return {"ignored": f"event {x_github_event!r}"}
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "invalid JSON")
    candidates = [p for p in s.scalars(select(db.Project)) if github.repo_matches(p.repo_url, payload.get("repository", {}))]
    if not candidates:
        raise HTTPException(404, "no project for this repository")
    authed = [p for p in candidates if github.verify_signature(p.webhook_secret, body, x_hub_signature_256)]
    if not authed:
        raise HTTPException(401, "invalid signature")
    push = github.parse_push(payload)
    if not push:
        return {"ignored": "not a branch push"}
    created = [_queue(s, p, trigger="webhook", **push).id for p in authed if p.branch == push["branch"]]
    return {"deployments": created} if created else {"ignored": f"branch {push['branch']!r} not deployed"}


# ---- live logs ----------------------------------------------------------------------
@app.websocket("/ws/deployments/{did}")
async def ws_deployment(ws: WebSocket, did: int):
    await ws.accept()
    q = bus.subscribe(did)  # subscribe first so nothing is lost between replay and live
    try:
        last = 0
        with db.SessionLocal() as s:
            d = s.get(db.Deployment, did)
            if not d:
                await ws.close(code=4404)
                return
            for row in s.scalars(select(db.LogLine).where(db.LogLine.deployment_id == did).order_by(db.LogLine.id)):
                last = row.id
                await ws.send_json({"type": "log", "id": row.id, "stage": row.stage, "line": row.line, "ts": row.ts.isoformat()})
            finished = d.status in ("SUCCESS", "FAILED")
            status = d.status
        await ws.send_json({"type": "deployment", "status": status})
        if finished:
            return
        while True:
            ev = await q.get()
            if ev["type"] == "log" and ev["id"] <= last:
                continue  # already replayed
            await ws.send_json(ev)
            if ev["type"] == "deployment" and ev["status"] in ("SUCCESS", "FAILED"):
                return
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(did, q)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
