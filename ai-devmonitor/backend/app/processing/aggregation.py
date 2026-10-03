from datetime import datetime, timedelta
from statistics import mean, median

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Incident, LogEntry, Metric, Service


def series(db: Session, service: str, name: str, since: datetime, until: datetime | None = None):
    q = select(Metric.ts, Metric.value).where(
        Metric.service == service, Metric.name == name, Metric.ts >= since
    )
    if until is not None:
        q = q.where(Metric.ts <= until)
    return [(ts, v) for ts, v in db.execute(q.order_by(Metric.ts)).all()]


def window_mean(db: Session, service: str, name: str, now: datetime, minutes: int) -> float | None:
    values = [v for _, v in series(db, service, name, now - timedelta(minutes=minutes), now)]
    return mean(values) if values else None


def baseline(db: Session, service: str, name: str, before: datetime, lookback_min: int = 60, gap_min: int = 10):
    """Median of the metric in [before-lookback, before-gap]; robust to the incident ramp-up."""
    values = [
        v for _, v in series(db, service, name, before - timedelta(minutes=lookback_min), before - timedelta(minutes=gap_min))
    ]
    return median(values) if values else None


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def known_services(db: Session) -> list[Service]:
    return list(db.scalars(select(Service).order_by(Service.name)))


def ensure_service(db: Session, name: str, kind: str | None = None) -> Service:
    svc = db.scalar(select(Service).where(Service.name == name))
    if svc is None:
        svc = Service(name=name, kind=kind or _guess_kind(name))
        db.add(svc)
        db.flush()
    return svc


def _guess_kind(name: str) -> str:
    n = name.lower()
    for kind, hints in {
        "database": ("postgres", "mysql", "db", "mongo"),
        "cache": ("redis", "memcache", "cache"),
        "worker": ("worker", "queue", "celery"),
        "frontend": ("web", "frontend", "ui"),
    }.items():
        if any(h in n for h in hints):
            return kind
    return "api"


LATEST_METRICS = ("cpu", "ram", "disk", "error_rate", "latency_p95", "requests", "db_connections")


def service_overview(db: Session, now: datetime, window_min: int = 5) -> list[dict]:
    open_inc: dict[str, Incident] = {}
    for i in db.scalars(select(Incident).where(Incident.status != "resolved").order_by(Incident.started_at)):
        cur = open_inc.get(i.service)
        if cur is None or (i.severity == "critical" and cur.severity != "critical"):
            open_inc[i.service] = i
    out = []
    for svc in known_services(db):
        latest = {}
        for name in LATEST_METRICS:
            val = window_mean(db, svc.name, name, now, window_min)
            if val is not None:
                latest[name] = round(val, 2)
        inc = open_inc.get(svc.name)
        last_log = db.scalar(select(LogEntry.ts).where(LogEntry.service == svc.name).order_by(LogEntry.ts.desc()))
        if inc is not None:
            status = "red" if inc.severity == "critical" else "yellow"
        elif not latest and last_log is None:
            status = "unknown"
        else:
            status = "green"
        out.append(
            {
                "name": svc.name,
                "kind": svc.kind,
                "status": status,
                "metrics": latest,
                "open_incident_id": inc.id if inc else None,
            }
        )
    return out
