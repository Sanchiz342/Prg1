"""Exports the marts into one compact JSON file for the static dashboard (dashboard/data.json).

Region shapes are projected and simplified here (equirectangular projection scaled at Ukraine's mid-latitude,
Douglas-Peucker simplification), so the browser only draws ready-made SVG paths.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from pipeline.config import DASHBOARD_DIR, GEO_SOURCE, RAW_DIR, SOURCES, WAREHOUSE
from pipeline.ingest import latest_path, read_manifest

MAP_WIDTH = 1000.0
LAT0 = math.radians(48.5)
SIMPLIFY_TOLERANCE = 0.6  # in map units (~pixels at 1000 px width)


def _project(lon: float, lat: float, bbox: tuple[float, float, float, float], scale: float) -> tuple[float, float]:
    min_lon, _, _, max_lat = bbox
    return ((lon - min_lon) * math.cos(LAT0) * scale, (max_lat - lat) * scale)


def _simplify(points: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas-Peucker. A closed ring (first point == last) has a zero-length base segment, which would make
    every distance 0 and collapse the polygon, so it is split at the point farthest from the start and each
    half is simplified as an open line."""
    if len(points) < 4:
        return points
    if points[0] == points[-1]:
        x0, y0 = points[0]
        k = max(range(len(points)), key=lambda i: (points[i][0] - x0) ** 2 + (points[i][1] - y0) ** 2)
        if 0 < k < len(points) - 1:
            return _simplify_open(points[: k + 1], tol)[:-1] + _simplify_open(points[k:], tol)
    return _simplify_open(points, tol)


def _simplify_open(points: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Iterative Douglas-Peucker on an open polyline (no recursion-depth limits on long borders)."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = points[a], points[b]
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy) or 1e-12
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            x0, y0 = points[i]
            d = abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / norm
            if d > best:
                best, idx = d, i
        if best > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(points, keep) if k]


def _rings(geometry: dict) -> list[list[list[float]]]:
    if geometry["type"] == "Polygon":
        return geometry["coordinates"]
    if geometry["type"] == "MultiPolygon":
        return [ring for poly in geometry["coordinates"] for ring in poly]
    return []


def region_shapes(geojson_path: Path) -> tuple[dict[str, dict], dict]:
    features = json.loads(geojson_path.read_text(encoding="utf-8"))["features"]
    lons, lats = [], []
    for f in features:
        for ring in _rings(f["geometry"]):
            for lon, lat in ring:
                lons.append(lon)
                lats.append(lat)
    bbox = (min(lons), min(lats), max(lons), max(lats))
    scale = MAP_WIDTH / ((bbox[2] - bbox[0]) * math.cos(LAT0))
    shapes: dict[str, dict] = {}
    for f in features:
        parts, area_best, centroid = [], -1.0, (0.0, 0.0)
        for ring in _rings(f["geometry"]):
            pts = _simplify([_project(lon, lat, bbox, scale) for lon, lat in ring], SIMPLIFY_TOLERANCE)
            if len(pts) < 3:
                continue
            parts.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z")
            # Label point: centroid of the largest ring (shoelace formula).
            a = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1])) / 2
            if abs(a) > area_best and a != 0:
                cx = sum((x1 + x2) * (x1 * y2 - x2 * y1) for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1])) / (6 * a)
                cy = sum((y1 + y2) * (x1 * y2 - x2 * y1) for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1])) / (6 * a)
                area_best, centroid = abs(a), (round(cx, 1), round(cy, 1))
        shapes[f["properties"]["shapeISO"]] = {"path": "".join(parts), "label": centroid}
    height = (bbox[3] - bbox[1]) * scale
    return shapes, {"width": MAP_WIDTH, "height": round(height, 1)}


def export(warehouse: Path = WAREHOUSE, out_dir: Path = DASHBOARD_DIR, raw_dir: Path = RAW_DIR,
           extra_meta: dict | None = None) -> Path:
    with duckdb.connect(str(warehouse), read_only=True) as con:
        def rows(sql: str) -> list[tuple]:
            return con.execute(sql).fetchall()

        regions = rows("select iso_code, region, name_uk, in_dataset, note from regions order by name_uk")
        monthly = rows("""select iso_code, strftime(month, '%Y-%m'), any_alert_hours, oblastwide_alert_hours,
                                 volunteer_alert_hours, any_episodes, volunteer_episodes, official_days,
                                 volunteer_days, days_in_month
                          from agg_region_monthly order by iso_code, month""")
        heat = rows("select metric, iso_code, weekday, hour, episodes from agg_start_hour_weekday")
        longest = rows("""select metric, rank, iso_code, name_uk, strftime(timezone('Europe/Kyiv', started_at), '%Y-%m-%d %H:%M'),
                                 strftime(timezone('Europe/Kyiv', finished_at), '%Y-%m-%d %H:%M'), hours, alerts_merged
                          from agg_longest_episodes where rank <= 10 order by metric, rank""")
        quality = rows("select source, check_name, value, severity, detail from rpt_data_quality")
        coverage = rows("""select source, strftime(min(timezone('Europe/Kyiv', started_at)), '%Y-%m-%d'),
                                  strftime(max(timezone('Europe/Kyiv', started_at)), '%Y-%m-%d'), count(*)
                           from fct_alerts group by source""")

    by_iso: dict[str, list] = {}
    for iso, *values in monthly:
        by_iso.setdefault(iso, []).append(values)
    heatmap: dict[str, dict[str, list[list[int]]]] = {}
    for metric, iso, weekday, hour, n in heat:
        grid = heatmap.setdefault(metric, {}).setdefault(iso, [[0] * 24 for _ in range(7)])
        grid[int(weekday) - 1][int(hour)] = int(n)

    shapes, size = region_shapes(latest_path(GEO_SOURCE, raw_dir))
    manifest = {m["source"]: m for m in read_manifest() if m["status"] != "offline"}

    data = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "timezone": "Europe/Kyiv",
            "coverage": {src: {"first_day": a, "last_day": b, "alerts": n} for src, a, b, n in coverage},
            "sources": [
                {"name": s.name, "url": s.url, "sha256": manifest.get(s.name, {}).get("sha256"),
                 "fetched_at": manifest.get(s.name, {}).get("fetched_at")}
                for s in (*SOURCES, GEO_SOURCE)
            ],
            "quality": [dict(zip(("source", "check", "value", "severity", "detail"), q)) for q in quality],
            **(extra_meta or {}),
        },
        "map": size,
        "regions": [
            {"iso": iso, "region": reg, "name": name, "inDataset": bool(ind), "note": note or "",
             **shapes.get(iso, {"path": "", "label": [0, 0]})}
            for iso, reg, name, ind, note in regions
        ],
        "monthlyColumns": ["month", "any", "oblastwide", "volunteer", "anyEpisodes", "volunteerEpisodes",
                           "officialDays", "volunteerDays", "daysInMonth"],
        "monthly": by_iso,
        "heatmap": heatmap,
        "longest": [dict(zip(("metric", "rank", "iso", "name", "start", "end", "hours", "merged"), r)) for r in longest],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "data.json"
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str), encoding="utf-8")
    return out
