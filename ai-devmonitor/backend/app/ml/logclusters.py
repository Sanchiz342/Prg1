"""Log pattern clustering: normalise messages into templates and group them."""
import re
from collections import defaultdict

_SUBS = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<hex>"),
    (re.compile(r"\b[0-9a-f]{16,}\b", re.I), "<hex>"),
    (re.compile(r"(['\"]).*?\1"), "<str>"),
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "<n>"),
]

CATEGORIES = [
    ("database", ("database", "postgres", "sql", "connection pool", "db ", "deadlock", "query")),
    ("timeout", ("timeout", "timed out", "deadline")),
    ("memory", ("out of memory", "oom", "memory", "heap")),
    ("network", ("connection refused", "connection reset", "unreachable", "dns", "socket")),
    ("auth", ("unauthorized", "forbidden", "token", "auth", "permission")),
]


def normalize(message: str) -> str:
    out = message.strip()
    for pattern, repl in _SUBS:
        out = pattern.sub(repl, out)
    return re.sub(r"\s+", " ", out)[:300]


def categorize(message: str) -> str:
    low = message.lower()
    for cat, hints in CATEGORIES:
        if any(h in low for h in hints):
            return cat
    return "other"


def cluster(entries: list[dict]) -> list[dict]:
    """entries: dicts with ts, service, level, message. Returns clusters sorted by severity then count."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in entries:
        groups[(e["level"], normalize(e["message"]))].append(e)
    level_rank = {"CRITICAL": 4, "ERROR": 3, "WARN": 2, "INFO": 1, "DEBUG": 0}
    out = []
    for (level, template), items in groups.items():
        out.append(
            {
                "template": template,
                "category": categorize(items[0]["message"]),
                "level": level,
                "count": len(items),
                "first_seen": min(i["ts"] for i in items),
                "last_seen": max(i["ts"] for i in items),
                "services": sorted({i["service"] for i in items}),
                "example": items[0]["message"],
            }
        )
    out.sort(key=lambda c: (level_rank.get(c["level"], 0), c["count"]), reverse=True)
    return out
