"""Runs dbt as a subprocess against a given DuckDB file and summarises its run_results.json.

A subprocess (rather than the in-process dbtRunner) means dbt's adapter cannot keep the DuckDB file open
afterwards, and each run starts from a clean state.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.config import DATA_DIR, DBT_DIR, WAREHOUSE


@dataclass
class DbtOutcome:
    success: bool
    passed: int = 0
    warned: int = 0
    failed: int = 0
    results: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    log: str = ""


def _dbt_executable() -> list[str]:
    exe = Path(sys.executable).parent / "dbt"
    return [str(exe)] if exe.exists() else [sys.executable, "-c", "from dbt.cli.main import cli; cli()"]


def run_dbt(command: list[str], warehouse: Path = WAREHOUSE, work_dir: Path | None = None) -> DbtOutcome:
    """`command` e.g. ["build"], ["build", "--full-refresh"], ["source", "freshness"]."""
    work = Path(work_dir or DATA_DIR / "dbt")
    target = work / "target"
    env = {**os.environ, "ALERTS_WAREHOUSE": str(Path(warehouse).resolve()), "DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}
    args = [
        *_dbt_executable(), *command,
        "--project-dir", str(DBT_DIR),
        "--profiles-dir", str(DBT_DIR),
        "--target-path", str(target),
        "--log-path", str(work / "logs"),
    ]
    artifact = target / ("sources.json" if command[:2] == ["source", "freshness"] else "run_results.json")
    artifact.unlink(missing_ok=True)  # never report a previous run's results as this run's
    proc = subprocess.run(args, env=env, capture_output=True, text=True)
    outcome = DbtOutcome(success=proc.returncode == 0, log=proc.stdout + proc.stderr)

    if artifact.exists():
        data = json.loads(artifact.read_text(encoding="utf-8"))
        for r in data.get("results", []):
            status = str(r.get("status", "")).lower()
            row = {"name": r.get("unique_id"), "status": status, "failures": r.get("failures"),
                   "message": r.get("message") or r.get("error")}
            if "max_loaded_at" in r:
                row.update(max_loaded_at=r["max_loaded_at"], age_seconds=r.get("max_loaded_at_time_ago_in_s"))
                outcome.sources.append(row)
            outcome.results.append(row)
            if status in ("pass", "success"):
                outcome.passed += 1
            elif status == "warn":
                outcome.warned += 1
            elif status in ("fail", "error", "runtime error"):
                outcome.failed += 1
    return outcome
