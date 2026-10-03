"""Local anomaly detection (no data leaves the server)."""
from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import IsolationForest

MIN_HISTORY = 20


@dataclass
class AnomalyResult:
    score: float  # 0..1, higher = more anomalous (Isolation Forest score)
    z: float  # robust z-score of the recent mean vs. history
    is_anomaly: bool


def robust_z(history: np.ndarray, recent: np.ndarray) -> float:
    med = float(np.median(history))
    mad = float(np.median(np.abs(history - med))) * 1.4826
    scale = max(mad, abs(med) * 0.02, 1e-6)
    return float((np.mean(recent) - med) / scale)


def detect(history: list[float], recent: list[float], score_threshold: float = 0.6, z_threshold: float = 4.0) -> AnomalyResult | None:
    """Fit an Isolation Forest on `history` and score `recent` points.

    An anomaly needs both a high forest score *and* a large robust z-score, which
    keeps flat/noisy-but-healthy series from producing false positives.
    """
    if len(history) < MIN_HISTORY or not recent:
        return None
    h = np.asarray(history, dtype=float).reshape(-1, 1)
    r = np.asarray(recent, dtype=float).reshape(-1, 1)
    forest = IsolationForest(n_estimators=100, contamination="auto", random_state=42).fit(h)
    score = float(np.mean(-forest.score_samples(r)))
    z = robust_z(h.ravel(), r.ravel())
    return AnomalyResult(score=round(score, 3), z=round(z, 2), is_anomaly=score >= score_threshold and abs(z) >= z_threshold)
