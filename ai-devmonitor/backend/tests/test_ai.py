import pytest

from app.ai import orchestrator
from app.ai.base import AIError, AIProvider, parse_result
from app.ai.redact import redact, redact_text
from app.core.config import settings


def test_redaction():
    text, n = redact_text("login bob@example.com from 10.1.2.3 password=hunter2 Bearer abc.def-ghi postgres://u:p@db/x")
    assert "bob@example.com" not in text and "10.1.2.3" not in text and "hunter2" not in text and "u:p@db" not in text
    assert n >= 5
    clean, count = redact({"a": ["mail me a@b.io"], "n": 3})
    assert clean["a"][0] == "mail me <email>" and clean["n"] == 3 and count == 1


def test_parse_result_validates():
    ok = '{"summary":"s","severity":"critical","confidence":0.8,"possible_causes":[{"cause":"x","confidence":0.7}]}'
    assert parse_result("here you go:\n" + ok).severity == "critical"
    with pytest.raises(AIError):
        parse_result('{"summary":"s","severity":"apocalypse","confidence":2,"possible_causes":[]}')
    with pytest.raises(AIError):
        parse_result("no json at all")


CTX = {
    "incident": {"service": "api", "trigger_metric": "error_rate", "severity": "critical", "baseline_value": 1.0, "peak_value": 20.0},
    "metrics": [{"service": "api", "name": "db_connections", "current": 97, "baseline": 40, "peak": 98, "change_ratio": 2.4}],
    "log_clusters": [{"template": "DatabaseConnectionError <n>", "category": "database", "level": "ERROR", "count": 50, "services": ["api"]}],
    "deployments": [{"service": "api", "version": "v1.8.2", "minutes_before_incident": 6, "notes": ""}],
}


def test_mock_analysis_finds_db_and_deploy():
    out = orchestrator.analyze(CTX, "local")
    causes = " ".join(c["cause"] for c in out["possible_causes"])
    assert "Database" in causes and "v1.8.2" in causes
    assert out["meta"]["sent_externally"] is False


class Spy(AIProvider):
    name, is_local = "spy", False
    seen = None

    def analyze(self, context):
        Spy.seen = context
        raise AIError("boom")

    def ask(self, q, c):
        raise AIError("boom")


def test_hybrid_redacts_and_falls_back(monkeypatch):
    monkeypatch.setitem(orchestrator.PROVIDERS, "spy", Spy)
    monkeypatch.setattr(settings, "ai_provider", "spy")
    ctx = {**CTX, "logs": [{"message": "failed for bob@example.com"}]}
    out = orchestrator.analyze(ctx, "hybrid")
    assert "bob@example.com" not in str(Spy.seen) and out["meta"]["redactions"] == 1
    assert out["meta"]["provider"] == "mock" and out["meta"]["fallback_reason"] == "boom"
    # local mode must not touch the remote provider
    Spy.seen = None
    assert orchestrator.analyze(ctx, "local")["meta"]["provider"] == "mock" and Spy.seen is None


def test_bad_mode():
    with pytest.raises(AIError):
        orchestrator.analyze(CTX, "quantum")
