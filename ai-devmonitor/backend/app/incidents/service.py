"""Incident lifecycle: detection, timeline, AI analysis, resolution summary, similar incidents."""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai import orchestrator
from ..core.config import settings
from ..ml import anomaly as anomaly_ml
from ..ml.embeddings import cosine, embed
from ..models import Deployment, Incident, IncidentEvent, IncidentVector, LogEntry
from ..processing import rules
from ..processing.aggregation import baseline, known_services, series
from .context import build_context

ANOMALY_METRICS = ("requests", "cpu", "latency_p95", "db_connections", "ram")
TIMELINE_METRICS = ("db_connections", "cpu", "ram", "latency_p95", "requests", "error_rate")
RESOLVE_FACTOR = 0.7  # hysteresis: resolve once below 70% of the warning threshold


def _open_incident(db: Session, service: str, metric: str) -> Incident | None:
    return db.scalar(
        select(Incident).where(Incident.service == service, Incident.trigger_metric == metric, Incident.status != "resolved")
    )


def _title(service: str, metric: str, severity: str, source: str) -> str:
    names = {"error_rate": "Error Rate", "latency_p95": "P95 Latency", "cpu": "CPU", "db_connections": "DB Connections",
             "disk": "Disk Usage", "requests": "Traffic", "ram": "RAM"}
    kind = "Anomaly" if source == "anomaly" else severity.capitalize()
    return f"{service}: {names.get(metric, metric)} {kind}"


def build_timeline(db: Session, inc: Incident) -> list[tuple[datetime, str, str]]:
    events: list[tuple[datetime, str, str]] = []
    for d in db.scalars(
        select(Deployment).where(Deployment.ts >= inc.started_at - timedelta(minutes=45), Deployment.ts <= inc.started_at)
    ):
        events.append((d.ts, "deployment", f"Deployment {d.version} of {d.service}"))
    for svc in sorted(set(inc.affected_services or []) | {inc.service}):
        for name in TIMELINE_METRICS:
            base = baseline(db, svc, name, inc.started_at)
            if base is None:
                continue
            pts = series(db, svc, name, inc.started_at - timedelta(minutes=30), inc.started_at)
            for ts, v in pts:
                if v > base * 1.5 and v - base > max(abs(base) * 0.1, 1.0):
                    events.append((ts, "metric", f"{svc} {name} increased ({base:.1f} -> {v:.1f})"))
                    break
    events.append((inc.started_at, "detected", f"Incident detected: {inc.title}"))
    events.sort(key=lambda e: e[0])
    return events


def _save_events(db: Session, inc: Incident) -> None:
    inc.events.clear()
    for ts, kind, desc in build_timeline(db, inc):
        inc.events.append(IncidentEvent(ts=ts, kind=kind, description=desc))
    if inc.resolved_at:
        inc.events.append(IncidentEvent(ts=inc.resolved_at, kind="resolved", description="Service recovered"))


def _affected(db: Session, service: str, now: datetime) -> list[str]:
    since = now - timedelta(minutes=settings.window_minutes * 2)
    rows = db.execute(
        select(LogEntry.service).where(LogEntry.ts >= since, LogEntry.level.in_(("ERROR", "CRITICAL"))).distinct()
    ).scalars().all()
    return sorted(set(rows) | {service})


def signature(inc: Incident, context: dict) -> str:
    parts = [inc.service, inc.trigger_metric, inc.severity]
    parts += [c["template"] for c in context.get("log_clusters", [])[:5]]
    if inc.analysis:
        parts += [c["cause"] for c in inc.analysis.get("possible_causes", [])[:2]]
    return " ".join(parts)


def analyze_incident(db: Session, inc: Incident, mode: str | None = None, now: datetime | None = None) -> Incident:
    ctx = build_context(db, inc, now)
    inc.analysis = orchestrator.analyze(ctx, mode)
    vec = embed(signature(inc, ctx))
    row = db.get(IncidentVector, inc.id)
    if row is None:
        db.add(IncidentVector(incident_id=inc.id, vector=vec, dim=len(vec)))
    else:
        row.vector, row.dim = vec, len(vec)
    db.commit()
    return inc


def similar_incidents(db: Session, inc: Incident, limit: int = 3, min_score: float = 0.35) -> list[dict]:
    mine = db.get(IncidentVector, inc.id)
    if mine is None:
        return []
    scored = []
    for vec in db.scalars(select(IncidentVector).where(IncidentVector.incident_id != inc.id)):
        s = cosine(mine.vector, vec.vector)
        if s >= min_score:
            other = db.get(Incident, vec.incident_id)
            scored.append({"id": other.id, "title": other.title, "status": other.status, "severity": other.severity,
                           "score": round(s, 3), "likely_cause": (other.analysis or {}).get("possible_causes", [{}])[0].get("cause")})
    return sorted(scored, key=lambda x: x["score"], reverse=True)[:limit]


def make_summary(db: Session, inc: Incident) -> dict:
    end = inc.resolved_at or datetime.utcnow()
    analysis = inc.analysis or {}
    causes = analysis.get("possible_causes") or []
    deploys = [e.description for e in inc.events if e.kind == "deployment"]
    return {
        "incident_id": inc.id,
        "duration_minutes": round((end - inc.started_at).total_seconds() / 60, 1),
        "affected_services": inc.affected_services,
        "impact": f"{inc.trigger_metric} {inc.baseline_value:.1f} -> peak {inc.peak_value:.1f} on {inc.service}",
        "likely_cause": causes[0]["cause"] if causes else None,
        "related_deployment": deploys[-1] if deploys else None,
        "timeline": [{"ts": e.ts.strftime("%H:%M:%S"), "kind": e.kind, "description": e.description} for e in inc.events],
        "note": "Likely cause is a hypothesis derived from correlated signals.",
    }


def resolve_incident(db: Session, inc: Incident, now: datetime | None = None) -> Incident:
    inc.status = "resolved"
    inc.resolved_at = now or datetime.utcnow()
    _save_events(db, inc)
    inc.summary = make_summary(db, inc)
    db.commit()
    return inc


def _open(db: Session, now: datetime, service: str, metric: str, severity: str, value: float, source: str, base: float | None) -> Incident:
    inc = Incident(
        title=_title(service, metric, severity, source),
        severity=severity,
        service=service,
        trigger_metric=metric,
        baseline_value=base if base is not None else 0.0,
        peak_value=value,
        source=source,
        started_at=now,
        affected_services=_affected(db, service, now),
    )
    db.add(inc)
    db.flush()
    _save_events(db, inc)
    db.commit()
    try:
        analyze_incident(db, inc, now=now)
    except Exception:  # analysis must never block detection
        db.rollback()
    return inc


def run_detection(db: Session, now: datetime | None = None) -> list[dict]:
    """One detection pass. Returns change events: {type: opened|updated|resolved, incident}."""
    now = now or datetime.utcnow()
    changes: list[dict] = []
    breaches, values = rules.evaluate(db, now)

    seen_open = set()
    for b in breaches:
        existing = _open_incident(db, b.service, b.metric)
        seen_open.add((b.service, b.metric))
        if existing is None:
            base = baseline(db, b.service, b.metric, now, lookback_min=60, gap_min=settings.window_minutes)
            inc = _open(db, now, b.service, b.metric, b.severity, b.value, "rule", base)
            changes.append({"type": "opened", "incident": inc})
        else:
            dirty = False
            if b.value > existing.peak_value:
                existing.peak_value, dirty = b.value, True
            if b.severity == "critical" and existing.severity != "critical":
                existing.severity, dirty = "critical", True
            if dirty:
                db.commit()
                changes.append({"type": "updated", "incident": existing})

    warn_by_metric = {m: w for m, w, _ in rules.rule_table()}
    for inc in db.scalars(select(Incident).where(Incident.status != "resolved")):
        key = (inc.service, inc.trigger_metric)
        if key in seen_open:
            continue
        val = values.get(key)
        if inc.source == "anomaly":
            continue  # handled by the anomaly pass below
        warn = warn_by_metric.get(inc.trigger_metric)
        if val is not None and warn is not None and val < warn * RESOLVE_FACTOR:
            changes.append({"type": "resolved", "incident": resolve_incident(db, inc, now)})

    changes += _anomaly_pass(db, now, seen_open)
    return changes


def _anomaly_pass(db: Session, now: datetime, rule_open: set) -> list[dict]:
    changes = []
    recent_n = max(3, settings.window_minutes)
    for svc in known_services(db):
        for metric in ANOMALY_METRICS:
            if (svc.name, metric) in rule_open:
                continue
            pts = series(db, svc.name, metric, now - timedelta(hours=3), now)
            values = [v for _, v in pts]
            existing = _open_incident(db, svc.name, metric)
            if len(values) < anomaly_ml.MIN_HISTORY + recent_n:
                continue
            res = anomaly_ml.detect(values[:-recent_n], values[-recent_n:])
            if res is None:
                continue
            if res.is_anomaly and existing is None:
                base = baseline(db, svc.name, metric, now, lookback_min=180, gap_min=settings.window_minutes)
                inc = _open(db, now, svc.name, metric, "warning", max(values[-recent_n:]), "anomaly", base)
                inc.analysis = {**(inc.analysis or {}), "anomaly": {"score": res.score, "z": res.z}}
                db.commit()
                changes.append({"type": "opened", "incident": inc})
            elif not res.is_anomaly and existing is not None and existing.source == "anomaly" and res.score < 0.5:
                changes.append({"type": "resolved", "incident": resolve_incident(db, existing, now)})
    return changes
