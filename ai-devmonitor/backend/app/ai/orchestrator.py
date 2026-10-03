"""AI Orchestrator: picks provider by mode, redacts in hybrid mode, falls back on failure."""
import time

from ..core.config import settings
from .base import AIError, AIProvider, AnalysisResult
from .mock import MockProvider
from .providers import AnthropicProvider, OllamaProvider, OpenAIProvider
from .redact import redact

PROVIDERS: dict[str, type[AIProvider]] = {
    "mock": MockProvider,
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
    "anthropic": AnthropicProvider,
}


def get_provider(name: str) -> AIProvider:
    try:
        return PROVIDERS[name]()
    except KeyError as exc:
        raise AIError(f"Unknown AI provider '{name}'") from exc


def resolve(mode: str | None = None) -> tuple[str, AIProvider, bool]:
    """Returns (mode, provider, needs_redaction)."""
    mode = (mode or settings.ai_mode).lower()
    if mode == "local":
        return mode, get_provider(settings.ai_local_provider), False
    if mode == "cloud":
        return mode, get_provider(settings.ai_provider), False
    if mode == "hybrid":
        return mode, get_provider(settings.ai_provider), True
    raise AIError(f"Unknown AI mode '{mode}' (expected local|cloud|hybrid)")


def analyze(context: dict, mode: str | None = None) -> dict:
    mode, provider, needs_redaction = resolve(mode)
    # Local providers keep data on the machine, so redaction is only for outbound calls.
    sensitive_out = needs_redaction and not provider.is_local
    redactions = 0
    payload = context
    if sensitive_out:
        payload, redactions = redact(context)
    started = time.perf_counter()
    fallback_reason = None
    try:
        result: AnalysisResult = provider.analyze(payload)
        used = provider.name
    except AIError as exc:
        fallback_reason = str(exc)
        result = MockProvider().analyze(context)
        used = "mock"
    return {
        **result.model_dump(),
        "meta": {
            "mode": mode,
            "provider": used,
            "requested_provider": provider.name,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "redactions": redactions,
            "sent_externally": sensitive_out or (mode == "cloud" and not provider.is_local and used != "mock"),
            "fallback_reason": fallback_reason,
        },
    }


def ask(question: str, context: dict, mode: str | None = None) -> dict:
    mode, provider, needs_redaction = resolve(mode)
    payload = context
    if needs_redaction and not provider.is_local:
        payload, _ = redact(context)
    try:
        answer, used = provider.ask(question, payload), provider.name
    except AIError:
        answer, used = MockProvider().ask(question, context), "mock"
    return {"answer": answer, "provider": used, "mode": mode}
