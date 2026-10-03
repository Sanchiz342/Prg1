import httpx

from ..core.config import settings
from .base import AIError, ChatProvider


def _post(url: str, headers: dict, payload: dict) -> dict:
    try:
        r = httpx.post(url, headers=headers, json=payload, timeout=settings.ai_timeout_s)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AIError(f"Provider request failed: {exc}") from exc


class OpenAIProvider(ChatProvider):
    name = "openai"

    def _chat(self, system: str, user: str) -> str:
        if not settings.openai_api_key:
            raise AIError("OPENAI_API_KEY is not set")
        data = _post(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {settings.openai_api_key}"},
            {
                "model": settings.openai_model,
                "temperature": 0.2,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
        )
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise AIError("Unexpected OpenAI response shape") from exc


class AnthropicProvider(ChatProvider):
    name = "anthropic"

    def _chat(self, system: str, user: str) -> str:
        if not settings.anthropic_api_key:
            raise AIError("ANTHROPIC_API_KEY is not set")
        data = _post(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": settings.anthropic_api_key, "anthropic-version": "2023-06-01"},
            {
                "model": settings.anthropic_model,
                "max_tokens": 1024,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        try:
            return "".join(b.get("text", "") for b in data["content"])
        except (KeyError, TypeError) as exc:
            raise AIError("Unexpected Anthropic response shape") from exc


class OllamaProvider(ChatProvider):
    name = "ollama"
    is_local = True

    def _chat(self, system: str, user: str) -> str:
        data = _post(
            f"{settings.ollama_url.rstrip('/')}/api/chat",
            {},
            {
                "model": settings.ollama_model,
                "stream": False,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
        )
        try:
            return data["message"]["content"]
        except KeyError as exc:
            raise AIError("Unexpected Ollama response shape") from exc
