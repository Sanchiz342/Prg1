import numpy as np


def forecast(points: list[tuple[float, float]], horizon_s: float, threshold: float | None = None) -> dict | None:
    """Linear-trend forecast over (epoch_seconds, value) points.

    Returns slope per minute, projected value after `horizon_s`, and (when a threshold is given
    and the trend is heading towards it) the estimated minutes until it is reached.
    """
    if len(points) < 5:
        return None
    t = np.array([p[0] for p in points], dtype=float)
    v = np.array([p[1] for p in points], dtype=float)
    t0 = t[-1]
    slope, intercept = np.polyfit(t - t0, v, 1)
    projected = float(intercept + slope * horizon_s)
    out = {"slope_per_min": round(float(slope) * 60, 4), "projected": round(projected, 2), "horizon_s": horizon_s}
    if threshold is not None and slope > 1e-9 and v[-1] < threshold:
        out["minutes_to_threshold"] = round(float((threshold - intercept) / slope) / 60, 1)
    return out
