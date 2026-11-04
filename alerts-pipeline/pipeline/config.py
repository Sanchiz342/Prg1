"""Sources, paths and expected schemas. Everything the pipeline touches is declared here."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ALERTS_DATA_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
WAREHOUSE = Path(os.environ.get("ALERTS_WAREHOUSE", DATA_DIR / "warehouse.duckdb"))
MANIFEST = DATA_DIR / "ingest_manifest.jsonl"
RUN_LOG = DATA_DIR / "run_log.jsonl"
DBT_DIR = ROOT / "dbt"
DASHBOARD_DIR = ROOT / "dashboard"

DATASET_REPO = "https://raw.githubusercontent.com/Vadimkin/ukrainian-air-raid-sirens-dataset/main/datasets"


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    columns: tuple[str, ...]
    """Exact CSV header. A different header means the upstream schema changed: fail loudly, keep the old copy."""


SOURCES: tuple[Source, ...] = (
    Source(
        name="official",
        url=f"{DATASET_REPO}/official_data_en.csv",
        columns=("oblast", "raion", "hromada", "level", "started_at", "finished_at", "source"),
    ),
    Source(
        name="volunteer",
        url=f"{DATASET_REPO}/volunteer_data_en.csv",
        columns=("region", "started_at", "finished_at", "naive"),
    ),
)

# Region boundaries for the map (geoBoundaries gbOpen UKR ADM1, ODbL; served through Git LFS).
GEO_SOURCE = Source(
    name="regions_geo",
    url="https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/main/releaseData/gbOpen/UKR/ADM1/"
    "geoBoundaries-UKR-ADM1_simplified.geojson",
    columns=(),
)

TIMEZONE = "Europe/Kyiv"
