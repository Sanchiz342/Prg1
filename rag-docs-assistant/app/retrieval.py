"""Hybrid retrieval: dense cosine similarity + BM25, merged with Reciprocal Rank Fusion (RRF).

Dense vectors catch paraphrases / inflected forms, BM25 catches exact rare terms (error codes, flags, names).
RRF fuses the two rankings without having to calibrate their very different score scales.
"""

from __future__ import annotations

import math
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Literal

import numpy as np

from app.embeddings import Embedder
from app.store import ChunkRecord, Store
from app.text import content_stems

Mode = Literal["hybrid", "dense", "sparse"]


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.doc_len = np.array([len(d) for d in docs], dtype=np.float64)
        self.avgdl = float(self.doc_len.mean()) if self.n and self.doc_len.sum() else 1.0
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for i, doc in enumerate(docs):
            for term, tf in Counter(doc).items():
                self.postings[term].append((i, tf))
        self.idf = {
            t: math.log(1.0 + (self.n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()
        }

    def scores(self, query: list[str]) -> np.ndarray:
        out = np.zeros(self.n, dtype=np.float64)
        for term in set(query):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i, tf in self.postings[term]:
                norm = tf + self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avgdl)
                out[i] += idf * tf * (self.k1 + 1) / norm
        return out


@dataclass(frozen=True)
class Hit:
    chunk: ChunkRecord
    score: float  # fused RRF score (only meaningful for ranking)
    dense: float  # cosine similarity in [-1, 1]
    sparse: float  # raw BM25 score
    dense_rank: int | None
    sparse_rank: int | None


@dataclass
class _Index:
    revision: int
    records: list[ChunkRecord]
    matrix: np.ndarray
    bm25: BM25


class HybridRetriever:
    def __init__(self, store: Store, embedder: Embedder, rrf_k: int = 60, candidates: int = 30) -> None:
        self.store = store
        self.embedder = embedder
        self.rrf_k = rrf_k
        self.candidates = candidates
        self._index: _Index | None = None
        self._lock = threading.Lock()

    def _current_index(self) -> _Index:
        with self._lock:
            revision = self.store.revision
            if self._index is None or self._index.revision != revision:
                records, matrix = self.store.load_index_data(self.embedder.dim)
                bm25 = BM25([content_stems(r.search_text) for r in records])
                self._index = _Index(revision, records, matrix, bm25)
            return self._index

    def term_idf(self, term: str) -> float:
        """IDF of a stemmed term in the corpus; unseen terms get the maximum (they are the most informative)."""
        bm25 = self._current_index().bm25
        idf = bm25.idf.get(term)
        return idf if idf is not None else math.log(1.0 + (bm25.n + 0.5) / 0.5)

    def search(
        self, query: str, k: int = 5, doc_ids: set[int] | None = None, mode: Mode = "hybrid"
    ) -> list[Hit]:
        index = self._current_index()
        if not index.records or k <= 0:
            return []

        allowed = np.ones(len(index.records), dtype=bool)
        if doc_ids:
            allowed = np.array([r.doc_id in doc_ids for r in index.records])
            if not allowed.any():
                return []

        query_vec = self.embedder.embed([query])[0]
        dense = index.matrix @ query_vec
        sparse = index.bm25.scores(content_stems(query))

        def ranking(scores: np.ndarray, require_positive: bool) -> list[int]:
            order = [int(i) for i in np.argsort(-scores, kind="stable") if allowed[i]]
            if require_positive:
                order = [i for i in order if scores[i] > 0]
            return order[: self.candidates]

        dense_order = ranking(dense, require_positive=False) if mode != "sparse" else []
        sparse_order = ranking(sparse, require_positive=True) if mode != "dense" else []
        dense_rank = {i: r for r, i in enumerate(dense_order)}
        sparse_rank = {i: r for r, i in enumerate(sparse_order)}

        fused: dict[int, float] = defaultdict(float)
        for ranks in (dense_rank, sparse_rank):
            for i, r in ranks.items():
                fused[i] += 1.0 / (self.rrf_k + r + 1)

        top = sorted(fused.items(), key=lambda kv: (-kv[1], -dense[kv[0]]))[:k]
        return [
            Hit(
                chunk=index.records[i],
                score=round(score, 6),
                dense=float(dense[i]),
                sparse=float(sparse[i]),
                dense_rank=dense_rank.get(i),
                sparse_rank=sparse_rank.get(i),
            )
            for i, score in top
        ]
