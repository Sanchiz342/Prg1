from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from ..core.config import settings
from .aggregation import known_services, window_mean


@dataclass
class Breach:
    service: str
    metric: str
    value: float
    threshold: float
    severity: str  # warning | critical


def rule_table():
    """(metric, warning_threshold, critical_threshold). None disables a level."""
    return [
        ("error_rate", settings.error_rate_warning, settings.error_rate_critical),
        ("latency_p95", settings.latency_p95_critical_ms / 2, settings.latency_p95_critical_ms),
        ("cpu", settings.cpu_critical - 10, settings.cpu_critical),
        ("db_connections", 85.0, 95.0),
        ("disk", 85.0, 95.0),
    ]


def evaluate(db: Session, now: datetime) -> tuple[list[Breach], dict[tuple[str, str], float]]:
    """Returns current breaches and the windowed value of every checked (service, metric)."""
    breaches: list[Breach] = []
    values: dict[tuple[str, str], float] = {}
    for svc in known_services(db):
        for metric, warn, crit in rule_table():
            val = window_mean(db, svc.name, metric, now, settings.window_minutes)
            if val is None:
                continue
            values[(svc.name, metric)] = val
            if val >= crit:
                breaches.append(Breach(svc.name, metric, val, crit, "critical"))
            elif val >= warn:
                breaches.append(Breach(svc.name, metric, val, warn, "warning"))
    return breaches, values
