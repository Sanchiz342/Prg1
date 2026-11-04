"""Command-line entry point.

    python -m pipeline run                  ingest -> load -> dbt build -> freshness -> export
    python -m pipeline run --offline        same, from the last downloaded files
    python -m pipeline run --full-refresh   rebuild incremental models from scratch
    python -m pipeline run --strict         also fail (exit 2) when a source is stale
    python -m pipeline ingest | load | transform | freshness | export

Exit codes: 0 success, 1 a step failed, 2 only freshness failed under --strict.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

from pipeline.config import GEO_SOURCE, RUN_LOG, SOURCES, WAREHOUSE
from pipeline.export import export
from pipeline.ingest import IngestError, fetch
from pipeline.load import load_all
from pipeline.transform import run_dbt


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline", description="Air-raid alerts data pipeline")
    parser.add_argument("command", choices=["run", "ingest", "load", "transform", "freshness", "export"])
    parser.add_argument("--offline", action="store_true", help="use the last downloaded files, no network")
    parser.add_argument("--full-refresh", action="store_true", help="rebuild incremental models from scratch")
    parser.add_argument("--strict", action="store_true", help="fail when a source is stale")
    args = parser.parse_args(argv)

    steps = ["ingest", "load", "transform", "freshness", "export"] if args.command == "run" else [args.command]
    timings: dict[str, float] = {}
    summary: dict = {"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "command": args.command}
    hashes: dict[str, str] = {}
    exit_code = 0
    dbt_meta: dict = {}

    try:
        for step in steps:
            t0 = time.perf_counter()
            if step == "ingest":
                for source in (*SOURCES, GEO_SOURCE):
                    r = fetch(source, offline=args.offline)
                    hashes[r.source] = r.sha256
                    log(f"ingest {r.source}: {r.status}, {r.bytes:,} bytes" + (f", {r.rows:,} rows" if r.rows else ""))
                summary["ingest"] = hashes
            elif step == "load":
                counts = load_all(hashes=hashes)
                log("load: " + ", ".join(f"{k} {v:,} rows" for k, v in counts.items()))
                summary["load"] = counts
            elif step == "transform":
                outcome = run_dbt(["build", *(["--full-refresh"] if args.full_refresh else [])])
                log(f"dbt build: {outcome.passed} passed, {outcome.warned} warnings, {outcome.failed} failed")
                for r in outcome.results:
                    if r["status"] in ("warn", "fail", "error"):
                        log(f"  {r['status'].upper()}: {r['name']} {r['message'] or ''}".rstrip())
                dbt_meta["dbt"] = {"passed": outcome.passed, "warned": outcome.warned, "failed": outcome.failed}
                summary["dbt"] = dbt_meta["dbt"]
                if not outcome.success:
                    print(outcome.log[-4000:], file=sys.stderr)
                    raise RuntimeError("dbt build failed")
            elif step == "freshness":
                outcome = run_dbt(["source", "freshness"])
                fresh = [{"source": s["name"].split(".")[-1], "status": s["status"],
                          "age_days": round((s.get("age_seconds") or 0) / 86400, 1)} for s in outcome.sources]
                # A source whose freshness query crashed is absent from sources.json: report it, never drop it.
                for name in (f"{src.name}_alerts" for src in SOURCES):
                    if name not in {f["source"] for f in fresh}:
                        fresh.append({"source": name, "status": "runtime error", "age_days": None})
                for f in fresh:
                    age = "unknown" if f["age_days"] is None else f"{f['age_days']} days old"
                    log(f"freshness {f['source']}: {f['status']} (newest alert {age})")
                dbt_meta["freshness"] = fresh
                summary["freshness"] = fresh
                if args.strict and any(f["status"] in ("error", "runtime error") for f in fresh):
                    exit_code = 2
            elif step == "export":
                out = export(warehouse=WAREHOUSE, extra_meta=dbt_meta)
                log(f"export: {out} ({out.stat().st_size / 1024:.0f} KiB)")
            timings[step] = round(time.perf_counter() - t0, 2)
    except (IngestError, RuntimeError) as e:
        log(f"FAILED: {e}")
        exit_code = 1
    except Exception as e:  # noqa: BLE001 - anything unexpected still ends up in the run log with exit code 1
        log(f"FAILED unexpectedly: {type(e).__name__}: {e}")
        summary["error"] = f"{type(e).__name__}: {e}"
        exit_code = 1

    summary.update(timings_s=timings, exit_code=exit_code)
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    log("timings: " + ", ".join(f"{k} {v}s" for k, v in timings.items()))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
