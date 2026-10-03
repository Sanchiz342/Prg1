"""Lightweight local text embeddings for similar-incident search.

Uses a hashing vectoriser (no model download, deterministic). The interface is
`embed(text) -> list[float]`, so it can be swapped for a transformer model and stored in pgvector.
"""
import numpy as np
from sklearn.feature_extraction.text import HashingVectorizer

DIM = 256
_vec = HashingVectorizer(n_features=DIM, alternate_sign=False, norm="l2", ngram_range=(1, 2))


def embed(text: str) -> list[float]:
    return [float(x) for x in _vec.transform([text]).toarray()[0]]


def cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a), np.asarray(b)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(va @ vb / denom) if denom else 0.0
