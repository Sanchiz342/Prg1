"""The question-answering pipeline: retrieve -> relevance gate -> generate -> validate citations."""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field

from app.citations import normalize_citations, sentence_support
from app.generation import Context, ExtractiveGenerator, GenerationError, Generator
from app.retrieval import Hit, HybridRetriever
from app.text import not_found_message

log = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 2000


@dataclass(frozen=True)
class Source:
    n: int
    chunk_id: int
    document: str
    heading: str | None
    page: int | None
    text: str
    score: float
    dense: float
    sparse: float

    @property
    def title(self) -> str:
        parts = [self.document]
        if self.heading:
            parts.append(self.heading)
        title = " › ".join(parts)
        return f"{title}, p. {self.page}" if self.page else title


@dataclass
class Answer:
    question: str
    answer: str
    grounded: bool  # True when the answer cites at least one source
    citations: list[Source]
    sources: list[Source]
    provider: str
    support: float | None
    fallback_used: bool = False
    warnings: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ("citations", "sources"):
            for item, src in zip(data[key], getattr(self, key)):
                item["title"] = src.title
        return data


class RAGPipeline:
    def __init__(
        self,
        retriever: HybridRetriever,
        generator: Generator,
        *,
        top_k: int = 5,
        min_relevance: float = 0.12,
        fallback: Generator | None = None,
    ) -> None:
        self.retriever = retriever
        self.generator = generator
        self.top_k = top_k
        self.min_relevance = min_relevance
        self.fallback = fallback if fallback is not None else ExtractiveGenerator()

    def _relevant(self, hits: list[Hit]) -> list[Hit]:
        """Keep hits that are similar enough to the query.

        Below the threshold the "best" chunk is just the least-bad match; passing it on invites the generator
        to answer from irrelevant text. A strong BM25 match on a rare term also counts as relevant.
        """
        best_sparse = max((h.sparse for h in hits), default=0.0)
        return [
            h for h in hits if h.dense >= self.min_relevance or (best_sparse > 0 and h.sparse >= 0.6 * best_sparse
                                                                 and h.dense >= self.min_relevance * 0.6)
        ]

    def ask(self, question: str, k: int | None = None, doc_ids: set[int] | None = None) -> Answer:
        question = question.strip()
        if not question:
            raise ValueError("Question is empty")
        if len(question) > MAX_QUESTION_CHARS:
            raise ValueError(f"Question is longer than {MAX_QUESTION_CHARS} characters")

        t0 = time.perf_counter()
        hits = self._relevant(self.retriever.search(question, k or self.top_k, doc_ids))
        t1 = time.perf_counter()

        sources = [
            Source(
                n=i,
                chunk_id=h.chunk.id,
                document=h.chunk.document,
                heading=h.chunk.heading,
                page=h.chunk.page,
                text=h.chunk.text,
                score=h.score,
                dense=round(h.dense, 4),
                sparse=round(h.sparse, 4),
            )
            for i, h in enumerate(hits, start=1)
        ]
        warnings: list[str] = []
        fallback_used = False
        provider = self.generator.name

        if not sources:
            raw = not_found_message(question)
            provider = "none"
        else:
            contexts = [Context(n=s.n, title=s.title, text=s.text) for s in sources]
            try:
                raw = self.generator.answer(question, contexts)
            except GenerationError as exc:
                log.warning("generator %s failed, using fallback: %s", self.generator.name, exc)
                warnings.append(f"{self.generator.name} failed ({exc}); answered with {self.fallback.name}")
                raw = self.fallback.answer(question, contexts)
                provider, fallback_used = self.fallback.name, True
        t2 = time.perf_counter()

        text, used, invalid = normalize_citations(raw, len(sources))
        if invalid:
            warnings.append(f"Removed citations to non-existent sources: {sorted(set(invalid))}")
        by_n = {s.n: s for s in sources}
        citations = [by_n[n] for n in used]
        support = sentence_support(text, {s.n: s.text for s in sources}) if citations else None

        return Answer(
            question=question,
            answer=text,
            grounded=bool(citations),
            citations=citations,
            sources=sources,
            provider=provider,
            support=support,
            fallback_used=fallback_used,
            warnings=warnings,
            timings_ms={
                "retrieval": round((t1 - t0) * 1000, 1),
                "generation": round((t2 - t1) * 1000, 1),
                "total": round((time.perf_counter() - t0) * 1000, 1),
            },
        )
