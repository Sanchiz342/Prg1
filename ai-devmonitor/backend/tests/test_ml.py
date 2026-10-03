import random

from app.ml import anomaly, embeddings, forecast, logclusters


def test_isolation_forest_flags_spike_not_noise():
    rnd = random.Random(0)
    hist = [110 + rnd.uniform(-8, 8) for _ in range(120)]
    assert anomaly.detect(hist, [112, 108, 115]).is_anomaly is False
    spike = anomaly.detect(hist, [420, 430, 415])
    assert spike.is_anomaly and spike.score > 0.6


def test_anomaly_needs_history():
    assert anomaly.detect([1, 2, 3], [100]) is None


def test_forecast_threshold():
    pts = [(i * 60.0, 50 + i * 2.0) for i in range(20)]
    out = forecast.forecast(pts, 1800, threshold=100)
    assert out["slope_per_min"] > 1.9 and out["minutes_to_threshold"] > 0
    assert forecast.forecast(pts[:2], 60) is None


def test_log_normalisation_and_clustering():
    msgs = [
        {"ts": i, "service": "api", "level": "ERROR", "message": f"DatabaseConnectionError: pool exhausted (pool={90 + i}) host 10.0.0.{i}"}
        for i in range(5)
    ] + [{"ts": 9, "service": "api", "level": "INFO", "message": "started"}]
    out = logclusters.cluster(msgs)
    assert out[0]["count"] == 5 and out[0]["category"] == "database" and len(out) == 2


def test_similarity():
    a = embeddings.embed("user-api error_rate database connection pool exhausted")
    b = embeddings.embed("billing-api error_rate database connection pool exhausted")
    c = embeddings.embed("worker disk usage image resize backlog")
    assert embeddings.cosine(a, b) > embeddings.cosine(a, c)
