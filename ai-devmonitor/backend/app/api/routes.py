import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai import orchestrator
from ..core.config import settings
from ..core.db import SessionLocal, get_db, utcnow
from ..core.security import authenticate, create_token, current_user, decode_token, require_admin, require_ingest_key
from ..hub import hub
from ..incidents import service as inc_service
from ..incidents.context import recent_logs
from ..ml.forecast import forecast
from ..ml.logclusters import cluster
from ..models import Deployment, Incident, LogEntry, Metric
from ..processing.aggregation import ensure_service, series, service_overview
from ..schemas import AskIn, DeploymentIn, LogBatch, LoginIn, MetricBatch

router = APIRouter()


def _naive(ts: datetime | None) -> datetime:
    if ts is None:
        return utcnow()
    return ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts


def incident_dict(inc: Incident, detail: bool = False) -> dict:
    d = {
        "id": inc.id,
        "title": inc.title,
        "severity": inc.severity,
        "status": inc.status,
        "service": inc.service,
        "trigger_metric": inc.trigger_metric,
        "baseline_value": inc.baseline_value,
        "peak_value": inc.peak_value,
        "source": inc.source,
        "started_at": inc.started_at.isoformat(),
        "resolved_at": inc.resolved_at.isoformat() if inc.resolved_at else None,
        "affected_services": inc.affected_services,
        "analysis": inc.analysis,
    }
    if detail:
        d["summary"] = inc.summary
        d["timeline"] = [{"ts": e.ts.isoformat(), "kind": e.kind, "description": e.description} for e in inc.events]
    return d


def _get_incident(db: Session, incident_id: int) -> Incident:
    inc = db.get(Incident, incident_id)
    if inc is None:
        raise HTTPException(404, "Incident not found")
    return inc


# ---------- auth ----------
@router.post("/auth/login")
def login(body: LoginIn):
    role = authenticate(body.username, body.password)
    if role is None:
        raise HTTPException(401, "Invalid credentials")
    return {"access_token": create_token(body.username, role), "token_type": "bearer", "role": role}


@router.get("/auth/me")
def me(user: dict = Depends(current_user)):
    return {"username": user["sub"], "role": user["role"]}


# ---------- ingest (API key) ----------
@router.post("/ingest/metrics", dependencies=[Depends(require_ingest_key)])
def ingest_metrics(batch: MetricBatch, db: Session = Depends(get_db)):
    for svc in {m.service for m in batch.metrics}:
        ensure_service(db, svc)
    db.add_all(Metric(service=m.service, name=m.name, value=m.value, ts=_naive(m.ts)) for m in batch.metrics)
    db.commit()
    return {"accepted": len(batch.metrics)}


@router.post("/ingest/logs", dependencies=[Depends(require_ingest_key)])
def ingest_logs(batch: LogBatch, db: Session = Depends(get_db)):
    for svc in {m.service for m in batch.logs}:
        ensure_service(db, svc)
    db.add_all(LogEntry(service=m.service, level=m.level, message=m.message, ts=_naive(m.ts)) for m in batch.logs)
    db.commit()
    return {"accepted": len(batch.logs)}


@router.post("/ingest/deployments", dependencies=[Depends(require_ingest_key)])
def ingest_deployment(body: DeploymentIn, db: Session = Depends(get_db)):
    ensure_service(db, body.service)
    db.add(Deployment(service=body.service, version=body.version, notes=body.notes, ts=_naive(body.ts)))
    db.commit()
    return {"accepted": 1}


# ---------- read API ----------
@router.get("/services")
def services(db: Session = Depends(get_db), _: dict = Depends(current_user)):
    return service_overview(db, utcnow(), settings.window_minutes)


@router.get("/metrics")
def metrics(
    service: str,
    name: str,
    minutes: int = Query(60, ge=1, le=1440),
    db: Session = Depends(get_db),
    _: dict = Depends(current_user),
):
    since = utcnow() - timedelta(minutes=minutes)
    return [{"ts": ts.isoformat(), "value": v} for ts, v in series(db, service, name, since)]


@router.get("/metrics/forecast")
def metrics_forecast(
    service: str,
    name: str,
    minutes: int = Query(60, ge=5, le=1440),
    horizon_minutes: int = Query(30, ge=1, le=720),
    threshold: float | None = None,
    db: Session = Depends(get_db),
    _: dict = Depends(current_user),
):
    pts = series(db, service, name, utcnow() - timedelta(minutes=minutes))
    result = forecast([(ts.timestamp(), v) for ts, v in pts], horizon_minutes * 60, threshold)
    if result is None:
        raise HTTPException(422, "Not enough data points to forecast")
    return result


@router.get("/logs")
def logs(
    service: str | None = None,
    level: str | None = None,
    minutes: int = Query(30, ge=1, le=1440),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    _: dict = Depends(current_user),
):
    q = select(LogEntry).where(LogEntry.ts >= utcnow() - timedelta(minutes=minutes))
    if service:
        q = q.where(LogEntry.service == service)
    if level:
        q = q.where(LogEntry.level == level.upper())
    rows = db.scalars(q.order_by(LogEntry.ts.desc()).limit(limit)).all()
    return [{"id": r.id, "ts": r.ts.isoformat(), "service": r.service, "level": r.level, "message": r.message} for r in rows]


@router.get("/logs/clusters")
def log_clusters(
    service: str | None = None,
    minutes: int = Query(30, ge=1, le=1440),
    db: Session = Depends(get_db),
    _: dict = Depends(current_user),
):
    now = utcnow()
    entries = recent_logs(db, now - timedelta(minutes=minutes), now, [service] if service else None)
    out = cluster(entries)
    for c in out:
        c["first_seen"], c["last_seen"] = c["first_seen"].isoformat(), c["last_seen"].isoformat()
    return out[:50]


@router.get("/deployments")
def deployments(db: Session = Depends(get_db), _: dict = Depends(current_user)):
    rows = db.scalars(select(Deployment).order_by(Deployment.ts.desc()).limit(50)).all()
    return [{"id": d.id, "service": d.service, "version": d.version, "notes": d.notes, "ts": d.ts.isoformat()} for d in rows]


# ---------- incidents ----------
@router.get("/incidents")
def list_incidents(status: str | None = None, db: Session = Depends(get_db), _: dict = Depends(current_user)):
    q = select(Incident).order_by(Incident.started_at.desc()).limit(100)
    if status:
        q = q.where(Incident.status == status)
    return [incident_dict(i) for i in db.scalars(q)]


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: int, db: Session = Depends(get_db), _: dict = Depends(current_user)):
    return incident_dict(_get_incident(db, incident_id), detail=True)


@router.get("/incidents/{incident_id}/logs")
def incident_logs(incident_id: int, db: Session = Depends(get_db), _: dict = Depends(current_user)):
    inc = _get_incident(db, incident_id)
    end = inc.resolved_at or utcnow()
    entries = recent_logs(db, inc.started_at - timedelta(minutes=15), end, sorted(set(inc.affected_services) | {inc.service}))
    clusters = cluster(entries)
    for c in clusters:
        c["first_seen"], c["last_seen"] = c["first_seen"].isoformat(), c["last_seen"].isoformat()
    tail = [{**e, "ts": e["ts"].isoformat()} for e in entries if e["level"] in ("ERROR", "CRITICAL", "WARN")][-50:]
    return {"clusters": clusters[:20], "logs": tail}


@router.get("/incidents/{incident_id}/metrics")
def incident_metrics(incident_id: int, db: Session = Depends(get_db), _: dict = Depends(current_user)):
    inc = _get_incident(db, incident_id)
    end = inc.resolved_at or utcnow()
    since = inc.started_at - timedelta(minutes=30)
    out = {}
    for svc in sorted(set(inc.affected_services) | {inc.service}):
        for name in ("error_rate", "latency_p95", "cpu", "db_connections", "requests"):
            pts = series(db, svc, name, since, end)
            if pts:
                out[f"{svc}/{name}"] = [{"ts": ts.isoformat(), "value": v} for ts, v in pts]
    return out


@router.get("/incidents/{incident_id}/similar")
def similar(incident_id: int, db: Session = Depends(get_db), _: dict = Depends(current_user)):
    return inc_service.similar_incidents(db, _get_incident(db, incident_id))


@router.post("/incidents/{incident_id}/analyze")
async def reanalyze(incident_id: int, mode: str | None = None, db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    inc = _get_incident(db, incident_id)
    try:
        await asyncio.to_thread(_analyze_in_thread, incident_id, mode)
    except orchestrator.AIError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.refresh(inc)
    await hub.broadcast({"type": "incident_updated", "incident": incident_dict(inc)})
    return incident_dict(inc, detail=True)


def _analyze_in_thread(incident_id: int, mode: str | None):
    with SessionLocal() as s:
        inc_service.analyze_incident(s, s.get(Incident, incident_id), mode)


@router.post("/incidents/{incident_id}/resolve")
async def resolve(incident_id: int, db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    inc = _get_incident(db, incident_id)
    if inc.status == "resolved":
        return incident_dict(inc, detail=True)
    inc_service.resolve_incident(db, inc)
    await hub.broadcast({"type": "incident_resolved", "incident": incident_dict(inc)})
    return incident_dict(inc, detail=True)


@router.post("/incidents/{incident_id}/status")
def set_status(incident_id: int, status: str = Query(pattern="^(investigating|identified|monitoring)$"), db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    inc = _get_incident(db, incident_id)
    if inc.status == "resolved":
        raise HTTPException(409, "Incident already resolved")
    inc.status = status
    db.commit()
    return incident_dict(inc)


@router.post("/detect")
async def detect(db: Session = Depends(get_db), _: dict = Depends(require_admin)):
    changes = await asyncio.to_thread(_detect_in_thread)
    for c in changes:
        await hub.broadcast({"type": f"incident_{c['type']}", "incident": c["incident"]})
    return {"changes": [{"type": c["type"], "incident": c["incident"]["id"]} for c in changes]}


def _detect_in_thread() -> list[dict]:
    with SessionLocal() as s:
        changes = inc_service.run_detection(s)
        return [{"type": c["type"], "incident": incident_dict(c["incident"])} for c in changes]


# ---------- AI ----------
@router.get("/ai/config")
def ai_config(_: dict = Depends(current_user)):
    return {"mode": settings.ai_mode, "provider": settings.ai_provider, "local_provider": settings.ai_local_provider}


@router.post("/ask")
async def ask(body: AskIn, db: Session = Depends(get_db), _: dict = Depends(current_user)):
    ctx: dict = {
        "services": service_overview(db, utcnow(), settings.window_minutes),
        "open_incidents": [incident_dict(i) for i in db.scalars(select(Incident).where(Incident.status != "resolved"))],
    }
    if body.incident_id is not None:
        from ..incidents.context import build_context

        inc = _get_incident(db, body.incident_id)
        ctx.update(build_context(db, inc))
        ctx["analysis"] = inc.analysis
    try:
        return await asyncio.to_thread(orchestrator.ask, body.question, ctx)
    except orchestrator.AIError as exc:
        raise HTTPException(400, str(exc)) from exc


# ---------- live updates ----------
@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str | None = None):
    try:
        if not token:
            raise ValueError
        decode_token(token)
    except Exception:
        await ws.close(code=4401)
        return
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()  # keep-alive; clients may send pings
    except WebSocketDisconnect:
        hub.disconnect(ws)
