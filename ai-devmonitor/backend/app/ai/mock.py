"""Deterministic, fully local heuristic analyser.

Doubles as the offline default and as the fallback when a remote provider fails.
"""
from .base import AIProvider, AnalysisResult, Cause

CATEGORY_CAUSES = {
    "database": ("Database connection exhaustion or slow queries", ["Inspect the DB connection pool and active connections", "Check slow query log"]),
    "timeout": ("Upstream dependency timeouts", ["Check latency of downstream services", "Review timeout and retry settings"]),
    "memory": ("Memory pressure / leak", ["Inspect memory usage and recent allocations", "Check for OOM kills"]),
    "network": ("Network connectivity problem", ["Check DNS and network policies", "Verify dependent services are reachable"]),
    "auth": ("Authentication / permission failures", ["Check credential or token expiry", "Review recent auth config changes"]),
}


class MockProvider(AIProvider):
    name = "mock"
    is_local = True

    def analyze(self, context: dict) -> AnalysisResult:
        inc = context.get("incident", {})
        metrics = {(m["service"], m["name"]): m for m in context.get("metrics", [])}
        clusters = context.get("log_clusters", [])
        deploys = context.get("deployments", [])

        scores: dict[str, float] = {}
        checks: list[str] = []
        total_errors = sum(c["count"] for c in clusters if c["level"] in ("ERROR", "CRITICAL")) or 1
        for c in clusters:
            if c["level"] not in ("ERROR", "CRITICAL", "WARN") or c["category"] not in CATEGORY_CAUSES:
                continue
            label, c_checks = CATEGORY_CAUSES[c["category"]]
            scores[label] = scores.get(label, 0) + 0.55 * c["count"] / total_errors
            checks += c_checks
        for (_svc, name), m in metrics.items():
            if name == "db_connections" and m["current"] >= 85:
                label = CATEGORY_CAUSES["database"][0]
                scores[label] = scores.get(label, 0) + 0.3
            if name == "cpu" and m["current"] >= 85:
                scores["CPU saturation"] = scores.get("CPU saturation", 0) + 0.4
                checks.append("Inspect CPU-heavy processes / recent traffic growth")
            if name == "memory" or (name == "ram" and m["current"] >= 90):
                scores["Memory pressure / leak"] = scores.get("Memory pressure / leak", 0) + 0.3
        if deploys:
            d = deploys[0]
            scores[f"Recent deployment {d['version']} of {d['service']}"] = 0.4 + (0.2 if d["minutes_before_incident"] <= 15 else 0)
            checks.append(f"Review changes and consider rollback of deployment {d['version']}")
        if not scores:
            scores[f"Anomalous {inc.get('trigger_metric', 'metric')} behaviour with no clear log signature"] = 0.3
            checks.append("Inspect the affected service dashboards and recent changes")

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:3]
        causes = [Cause(cause=k, confidence=round(min(v, 0.9), 2)) for k, v in ranked]
        sev = inc.get("severity", "warning")
        ratio = ""
        if inc.get("baseline_value"):
            ratio = f" ({inc['baseline_value']:.1f} -> {inc['peak_value']:.1f})"
        summary = f"{inc.get('trigger_metric', 'metric')} on {inc.get('service', 'service')} deviated from normal{ratio}."
        if deploys:
            summary += f" A deployment ({deploys[0]['version']}) happened {deploys[0]['minutes_before_incident']} min earlier."
        if clusters:
            top = clusters[0]
            summary += f" Dominant log pattern: '{top['template'][:80]}' x{top['count']}."
        related = sorted({s for c in clusters for s in c["services"]} | {inc.get("service", "")} - {""})
        return AnalysisResult(
            summary=summary,
            severity=sev if sev in ("info", "warning", "critical") else "warning",
            confidence=causes[0].confidence,
            possible_causes=causes,
            related_services=related,
            recommended_checks=list(dict.fromkeys(checks))[:6],
        )

    def ask(self, question: str, context: dict) -> str:
        inc = context.get("incident")
        services = context.get("services", [])
        q = question.lower()
        if inc and any(w in q for w in ("why", "cause", "чому", "причин")):
            a = context.get("analysis") or self.analyze(context).model_dump()
            return f"Most likely: {a['possible_causes'][0]['cause']} (confidence {a['possible_causes'][0]['confidence']:.0%}). {a['summary']}"
        if any(w in q for w in ("status", "статус", "broken", "зламал", "down")):
            bad = [s["name"] for s in services if s["status"] in ("red", "yellow")]
            return f"Degraded services: {', '.join(bad)}." if bad else "All monitored services look healthy."
        open_n = len(context.get("open_incidents", []))
        return f"{open_n} open incident(s); {len(services)} service(s) monitored."
