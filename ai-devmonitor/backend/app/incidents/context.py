"""Builds the compact, relevant context handed to the AI layer (never the whole database)."""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ml.logclusters import cluster
from ..models import Deployment, Incident, LogEntry, Metric
from ..processing.aggregation import baseline

MAX_LOGS = 20
CONTEXT_METRICS = ("error_rate", "latency_p95", "requests", "cpu", "ram", "db_connections")


def _iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%S")


def recent_logs(db: Session, since: datetime, until: datetime, services: list[str] | None = None) -> list[dict]:
    q = select(LogEntry).where(LogEntry.ts >= since, LogEntry.ts <= until)
    if services:
        q = q.where(LogEntry.service.in_(services))
    rows = db.scalars(q.order_by(LogEntry.ts)).all()
    return [{"ts": r.ts, "service": r.service, "level": r.level, "message": r.message} for r in rows]


def build_context(db: Session, inc: Incident, now: datetime | None = None, window_min: int = 15) -> dict:
    end = inc.resolved_at or now or datetime.utcnow()
    since = inc.started_at - timedelta(minutes=window_min)
    services = sorted(set(inc.affected_services or []) | {inc.service})

    metrics = []
    for svc in services:
        for name in CONTEXT_METRICS:
            rows = db.execute(
                select(Metric.value).where(
                    Metric.service == svc, Metric.name == name, Metric.ts >= since, Metric.ts <= end
                )
            ).scalars().all()
            if not rows:
                continue
            base = baseline(db, svc, name, inc.started_at)
            cur = rows[-1]
            metrics.append(
                {
                    "service": svc,
                    "name": name,
                    "baseline": round(base, 2) if base is not None else None,
                    "current": round(cur, 2),
                    "peak": round(max(rows), 2),
                    "change_ratio": round(cur / base, 2) if base else None,
                }
            )

    logs = recent_logs(db, since, end, services)
    clusters = cluster(logs)
    for c in clusters:
        c["first_seen"], c["last_seen"] = _iso(c["first_seen"]), _iso(c["last_seen"])

    # Most relevant raw logs: errors first, one representative per template, newest first.
    seen, relevant = set(), []
    for entry in sorted(logs, key=lambda e: e["ts"], reverse=True):
        if entry["level"] not in ("ERROR", "CRITICAL", "WARN"):
            continue
        key = (entry["level"], entry["message"][:60])
        if key in seen:
            continue
        seen.add(key)
        relevant.append({**entry, "ts": _iso(entry["ts"])})
        if len(relevant) >= MAX_LOGS:
            break

    deploys = db.scalars(
        select(Deployment)
        .where(Deployment.ts >= inc.started_at - timedelta(minutes=60), Deployment.ts <= inc.started_at)
        .order_by(Deployment.ts.desc())
    ).all()

    return {
        "incident": {
            "id": inc.id,
            "title": inc.title,
            "severity": inc.severity,
            "service": inc.service,
            "trigger_metric": inc.trigger_metric,
            "baseline_value": inc.baseline_value,
            "peak_value": inc.peak_value,
            "started_at": _iso(inc.started_at),
            "source": inc.source,
        },
        "metrics": metrics,
        "log_clusters": clusters[:10],
        "logs": relevant,
        "deployments": [
            {
                "service": d.service,
                "version": d.version,
                "notes": d.notes,
                "minutes_before_incident": int((inc.started_at - d.ts).total_seconds() // 60),
            }
            for d in deploys
        ],
    }
