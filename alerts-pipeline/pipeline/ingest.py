"""Download the raw source files idempotently.

For every source:
  * conditional GET with the previous ETag -> 304 means nothing to do;
  * the body is streamed to a temporary file, its size checked against Content-Length and its SHA-256 computed;
  * CSV sources must have exactly the expected header (schema-drift guard);
  * only then is the file atomically renamed over `latest.<ext>`: a failed or partial download never replaces
    the last good copy;
  * every attempt is appended to a JSON-lines manifest (what, when, outcome, hash, size, rows).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pipeline.config import MANIFEST, RAW_DIR, Source

USER_AGENT = "alerts-pipeline/0.1 (+https://github.com/sanchiz342/prg1)"
CHUNK = 1 << 16

Status = Literal["downloaded", "not_modified", "unchanged", "offline"]


class IngestError(RuntimeError):
    pass


@dataclass
class IngestResult:
    source: str
    status: Status
    path: str
    sha256: str
    bytes: int
    rows: int | None
    etag: str | None
    fetched_at: str
    url: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def latest_path(source: Source, raw_dir: Path = RAW_DIR) -> Path:
    ext = ".geojson" if source.url.endswith(".geojson") else ".csv"
    return raw_dir / source.name / f"latest{ext}"


def _meta_path(source: Source, raw_dir: Path) -> Path:
    return raw_dir / source.name / "latest.meta.json"


def _read_meta(source: Source, raw_dir: Path) -> dict:
    try:
        return json.loads(_meta_path(source, raw_dir).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _check_csv(path: Path, source: Source) -> int:
    """Validates the header and returns the number of data rows."""
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            raise IngestError(f"{source.name}: empty file")
        if tuple(header) != source.columns:
            raise IngestError(f"{source.name}: header changed upstream: expected {list(source.columns)}, got {header}")
        rows = 0
        for row in reader:
            if len(row) != len(source.columns):
                raise IngestError(f"{source.name}: row {rows + 2} has {len(row)} fields, expected {len(source.columns)}")
            rows += 1
    return rows


def _log(result: IngestResult, manifest: Path) -> None:
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")


def fetch(source: Source, *, raw_dir: Path = RAW_DIR, manifest: Path = MANIFEST, offline: bool = False,
          timeout: float = 60) -> IngestResult:
    target = latest_path(source, raw_dir)
    meta = _read_meta(source, raw_dir)

    if offline:
        if not target.exists():
            raise IngestError(f"{source.name}: --offline but no local copy at {target}")
        result = IngestResult(source.name, "offline", str(target), meta.get("sha256", ""), target.stat().st_size,
                              meta.get("rows"), meta.get("etag"), _now(), source.url)
        _log(result, manifest)
        return result

    headers = {"User-Agent": USER_AGENT}
    if meta.get("etag") and target.exists():
        headers["If-None-Match"] = meta["etag"]
    request = urllib.request.Request(source.url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            result = IngestResult(source.name, "not_modified", str(target), meta["sha256"], target.stat().st_size,
                                  meta.get("rows"), meta.get("etag"), _now(), source.url)
            _log(result, manifest)
            return result
        raise IngestError(f"{source.name}: HTTP {e.code} from {source.url}") from e
    except urllib.error.URLError as e:
        raise IngestError(f"{source.name}: cannot reach {source.url}: {e.reason}") from e

    target.parent.mkdir(parents=True, exist_ok=True)
    expected = response.headers.get("Content-Length")
    digest = hashlib.sha256()
    size = 0
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".download-")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, response:
            while chunk := response.read(CHUNK):
                out.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        if expected is not None and int(expected) != size:
            raise IngestError(f"{source.name}: truncated download ({size} of {expected} bytes)")
        rows = _check_csv(tmp, source) if source.columns else None
        sha = digest.hexdigest()
        status: Status = "unchanged" if sha == meta.get("sha256") and target.exists() else "downloaded"
        os.replace(tmp, target)  # atomic on the same filesystem
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    etag = response.headers.get("ETag")
    _meta_path(source, raw_dir).write_text(
        json.dumps({"sha256": sha, "etag": etag, "rows": rows, "fetched_at": _now()}), encoding="utf-8"
    )
    result = IngestResult(source.name, status, str(target), sha, size, rows, etag, _now(), source.url)
    _log(result, manifest)
    return result


def read_manifest(manifest: Path = MANIFEST) -> list[dict]:
    if not manifest.exists():
        return []
    return [json.loads(line) for line in io.open(manifest, encoding="utf-8") if line.strip()]
