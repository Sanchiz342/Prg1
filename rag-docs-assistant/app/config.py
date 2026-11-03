"""Runtime configuration, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    db_path: Path
    # Embeddings: "hashing" (offline, no downloads) or "sentence-transformers" (optional dependency).
    embedder: str
    embed_dim: int
    st_model: str
    # Answer generation: "extractive" (offline), "ollama" (local LLM) or "anthropic" (Claude API).
    provider: str
    anthropic_model: str
    anthropic_effort: str
    ollama_url: str
    ollama_model: str
    # Chunking / retrieval.
    chunk_words: int
    chunk_overlap: int
    top_k: int
    min_relevance: float
    # API.
    max_upload_mb: int
    api_key: str | None
    seed_samples: bool
    samples_dir: Path

    @classmethod
    def from_env(cls, **overrides: object) -> "Settings":
        env = os.environ.get
        settings = cls(
            db_path=Path(env("RAG_DB_PATH", str(BASE_DIR / "data" / "rag.sqlite3"))),
            embedder=env("RAG_EMBEDDER", "hashing"),
            embed_dim=int(env("RAG_EMBED_DIM", "1024")),
            st_model=env("RAG_ST_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"),
            provider=env("RAG_PROVIDER", "extractive"),
            anthropic_model=env("ANTHROPIC_MODEL", "claude-opus-5-5"),
            anthropic_effort=env("ANTHROPIC_EFFORT", "medium"),
            ollama_url=env("OLLAMA_URL", "http://localhost:11434"),
            ollama_model=env("OLLAMA_MODEL", "llama3.1"),
            chunk_words=int(env("RAG_CHUNK_WORDS", "120")),
            chunk_overlap=int(env("RAG_CHUNK_OVERLAP", "30")),
            top_k=int(env("RAG_TOP_K", "5")),
            min_relevance=float(env("RAG_MIN_RELEVANCE", "0.12")),
            max_upload_mb=int(env("RAG_MAX_UPLOAD_MB", "20")),
            api_key=env("RAG_API_KEY") or None,
            seed_samples=_bool(env("RAG_SEED_SAMPLES", "true")),
            samples_dir=Path(env("RAG_SAMPLES_DIR", str(BASE_DIR / "sample_docs"))),
        )
        return replace(settings, **overrides) if overrides else settings
