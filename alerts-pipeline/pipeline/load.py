"""Load the latest raw files into DuckDB's `raw` schema, as text, exactly as published.

Typing, parsing and cleaning happen in dbt (staging), where failures are counted and tested instead of
silently coerced here. Each raw table carries lineage columns: which file (hash) and when it was loaded.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from pipeline.config import RAW_DIR, SOURCES, WAREHOUSE, Source
from pipeline.ingest import IngestError, latest_path


def load_source(con: duckdb.DuckDBPyConnection, source: Source, path: Path, sha256: str) -> int:
    columns = ", ".join(f"'{c}': 'VARCHAR'" for c in source.columns)
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE raw.{source.name}_alerts AS
        SELECT *, ?::VARCHAR AS _source_sha256, now() AS _loaded_at
        FROM read_csv(?, header = true, columns = {{{columns}}}, quote = '"', escape = '"',
                      strict_mode = true, all_varchar = true)
        """,
        [sha256, str(path)],
    )
    return con.execute(f"SELECT count(*) FROM raw.{source.name}_alerts").fetchone()[0]


def load_all(warehouse: Path = WAREHOUSE, raw_dir: Path = RAW_DIR, hashes: dict[str, str] | None = None) -> dict[str, int]:
    warehouse.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    with duckdb.connect(str(warehouse)) as con:
        for source in SOURCES:
            path = latest_path(source, raw_dir)
            if not path.exists():
                raise IngestError(f"{source.name}: nothing to load at {path} (run ingest first)")
            counts[source.name] = load_source(con, source, path, (hashes or {}).get(source.name, ""))
    return counts
