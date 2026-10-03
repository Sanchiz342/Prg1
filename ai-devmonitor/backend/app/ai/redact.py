"""Strip sensitive data from text/context before it leaves the server (hybrid mode)."""
import re
from typing import Any

PATTERNS = [
    (re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+\b"), "<jwt>"),
    (re.compile(r"(?i)\bbearer\s+[\w.\-~+/]+=*"), "Bearer <token>"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|authorization)\b\s*[=:]\s*\S+"), r"\1=<redacted>"),
    (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "<email>"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "<card>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<ip>"),
    (re.compile(r"\b(?:sk|pk|ghp|gho|xox[abp])[-_][\w-]{10,}\b"), "<key>"),
    (re.compile(r"(?i)\b(postgres(?:ql)?|mysql|mongodb|redis)://\S+"), r"\1://<redacted>"),
]


def redact_text(text: str) -> tuple[str, int]:
    count = 0
    for pattern, repl in PATTERNS:
        text, n = pattern.subn(repl, text)
        count += n
    return text, count


def redact(obj: Any) -> tuple[Any, int]:
    """Recursively redact all strings in a JSON-like structure. Returns (clean_copy, redaction_count)."""
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, list):
        total, out = 0, []
        for item in obj:
            clean, n = redact(item)
            out.append(clean)
            total += n
        return out, total
    if isinstance(obj, dict):
        total, out = 0, {}
        for k, v in obj.items():
            clean, n = redact(v)
            out[k] = clean
            total += n
        return out, total
    return obj, 0
