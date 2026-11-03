"""Wires settings into concrete components; shared by the API, the CLI and the evaluator."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import Settings
from app.embeddings import Embedder, build_embedder
from app.generation import ExtractiveGenerator, build_generator
from app.ingest.service import Ingestor
from app.rag import RAGPipeline
from app.retrieval import HybridRetriever
from app.store import Store

log = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    store: Store
    embedder: Embedder
    ingestor: Ingestor
    retriever: HybridRetriever
    pipeline: RAGPipeline

    def close(self) -> None:
        self.store.close()


def build_services(settings: Settings, *, provider: str | None = None) -> Services:
    store = Store(settings.db_path)
    embedder = build_embedder(settings.embedder, settings.embed_dim, settings.st_model)
    ingestor = Ingestor(store, embedder, settings.chunk_words, settings.chunk_overlap)
    if not ingestor.check_embedder():
        log.warning("Stored vectors come from another embedder; re-embedding all chunks with %s", embedder.id)
        ingestor.reindex()
    retriever = HybridRetriever(store, embedder)
    generator = build_generator(
        provider or settings.provider,
        anthropic_model=settings.anthropic_model,
        anthropic_effort=settings.anthropic_effort,
        ollama_url=settings.ollama_url,
        ollama_model=settings.ollama_model,
        term_weight=retriever.term_idf,
    )
    pipeline = RAGPipeline(
        retriever,
        generator,
        top_k=settings.top_k,
        min_relevance=settings.min_relevance,
        fallback=ExtractiveGenerator(term_weight=retriever.term_idf),
    )
    return Services(settings, store, embedder, ingestor, retriever, pipeline)


def seed_samples(services: Services) -> int:
    """Load the bundled demo documents into an empty store."""
    if services.store.stats()["documents"] or not services.settings.samples_dir.is_dir():
        return 0
    results = services.ingestor.ingest_path(services.settings.samples_dir)
    return sum(1 for _, r in results if not isinstance(r, Exception))
