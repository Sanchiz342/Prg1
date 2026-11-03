"""Embedding back-ends behind one small interface.

* ``HashingEmbedder`` — default. Signed feature hashing of words, word bigrams and character n-grams.
  Fully offline and deterministic: no model download, works in CI. Character n-grams make it tolerant to
  inflection and typos (important for Ukrainian), but it captures lexical, not deep semantic, similarity.
* ``SentenceTransformerEmbedder`` — optional real neural embeddings (``pip install sentence-transformers``).
"""

from __future__ import annotations

import math
import zlib
from collections import Counter
from typing import Protocol

import numpy as np

from app.text import STOPWORDS, tokenize


class Embedder(Protocol):
    @property
    def id(self) -> str:
        """Identifies the vector space; stored in the DB so mixed vectors are detected."""

    @property
    def dim(self) -> int: ...

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return an (n, dim) float32 matrix of L2-normalised vectors."""


def _normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class HashingEmbedder:
    WORD_WEIGHT = 1.0
    BIGRAM_WEIGHT = 0.6
    CHAR_WEIGHT = 0.35

    def __init__(self, dim: int = 1024, char_ngrams: tuple[int, int] = (3, 5)) -> None:
        if dim < 64:
            raise ValueError("dim must be >= 64")
        self._dim = dim
        self._ngrams = char_ngrams

    @property
    def id(self) -> str:
        return f"hashing-v1-{self._dim}-c{self._ngrams[0]}{self._ngrams[1]}"

    @property
    def dim(self) -> int:
        return self._dim

    def _features(self, text: str) -> Counter[str]:
        features: Counter[str] = Counter()
        words = [t for t in tokenize(text) if t not in STOPWORDS]
        lo, hi = self._ngrams
        for word in words:
            features["w:" + word] += 1
            padded = f" {word} "
            for n in range(lo, hi + 1):
                for i in range(len(padded) - n + 1):
                    features["c:" + padded[i : i + n]] += 1
        for a, b in zip(words, words[1:]):
            features[f"b:{a}_{b}"] += 1
        return features

    def _vector(self, text: str) -> np.ndarray:
        vec = np.zeros(self._dim, dtype=np.float64)
        for feature, count in self._features(text).items():
            h = zlib.crc32(feature.encode("utf-8"))
            sign = 1.0 if (h >> 31) & 1 else -1.0
            kind = feature[0]
            weight = self.WORD_WEIGHT if kind == "w" else self.BIGRAM_WEIGHT if kind == "b" else self.CHAR_WEIGHT
            vec[h % self._dim] += sign * weight * (1.0 + math.log(count))  # sublinear term frequency
        return vec

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        return _normalise(np.vstack([self._vector(t) for t in texts]))


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "RAG_EMBEDDER=sentence-transformers needs `pip install -r requirements-optional.txt`"
            ) from exc
        self._model = SentenceTransformer(model_name)
        self._name = model_name
        self._dim = int(self._model.get_sentence_embedding_dimension())

    @property
    def id(self) -> str:
        return f"st-{self._name}"

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        vectors = self._model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return np.asarray(vectors, dtype=np.float32)


def build_embedder(kind: str, dim: int, st_model: str) -> Embedder:
    if kind == "hashing":
        return HashingEmbedder(dim=dim)
    if kind == "sentence-transformers":
        return SentenceTransformerEmbedder(st_model)
    raise ValueError(f"Unknown RAG_EMBEDDER '{kind}' (use 'hashing' or 'sentence-transformers')")
