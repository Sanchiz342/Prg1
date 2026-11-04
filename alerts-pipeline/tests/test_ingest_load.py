"""Ingest against a local HTTP server that behaves like raw.githubusercontent.com (ETag, 304), plus loading."""

from __future__ import annotations

import hashlib
import http.server
import threading
from pathlib import Path

import duckdb
import pytest

from pipeline.config import Source
from pipeline.ingest import IngestError, fetch, latest_path, read_manifest
from pipeline.load import load_source

SRC_COLUMNS = ("region", "started_at", "finished_at", "naive")
GOOD = b"region,started_at,finished_at,naive\nKyiv City,2024-01-01 10:00:00+00:00,2024-01-01 10:30:00+00:00,False\n"


class FakeGitHub(http.server.BaseHTTPRequestHandler):
    body = GOOD
    truncate = False
    requests: list[dict] = []

    def do_GET(self):  # noqa: N802
        etag = '"' + hashlib.sha1(self.body).hexdigest() + '"'
        FakeGitHub.requests.append({"if_none_match": self.headers.get("If-None-Match")})
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body[: len(self.body) // 2] if self.truncate else self.body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    FakeGitHub.body, FakeGitHub.truncate, FakeGitHub.requests = GOOD, False, []
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/volunteer.csv"
    httpd.shutdown()


def source(url: str) -> Source:
    return Source(name="volunteer", url=url, columns=SRC_COLUMNS)


def test_download_then_not_modified_then_changed(server, tmp_path: Path):
    raw, manifest = tmp_path / "raw", tmp_path / "manifest.jsonl"
    first = fetch(source(server), raw_dir=raw, manifest=manifest)
    assert first.status == "downloaded" and first.rows == 1
    assert first.sha256 == hashlib.sha256(GOOD).hexdigest()
    assert latest_path(source(server), raw).read_bytes() == GOOD

    second = fetch(source(server), raw_dir=raw, manifest=manifest)
    assert second.status == "not_modified"
    assert FakeGitHub.requests[-1]["if_none_match"] is not None, "conditional request uses the stored ETag"

    FakeGitHub.body = GOOD + b"Lvivska oblast,2024-01-02 10:00:00+00:00,2024-01-02 11:00:00+00:00,True\n"
    third = fetch(source(server), raw_dir=raw, manifest=manifest)
    assert third.status == "downloaded" and third.rows == 2
    assert [r["status"] for r in read_manifest(manifest)] == ["downloaded", "not_modified", "downloaded"]


def test_failed_downloads_never_replace_the_last_good_copy(server, tmp_path: Path):
    raw, manifest = tmp_path / "raw", tmp_path / "m.jsonl"
    fetch(source(server), raw_dir=raw, manifest=manifest)
    good = latest_path(source(server), raw).read_bytes()

    FakeGitHub.body = b"region,started,ended\nx,y,z\n"  # upstream renamed columns
    with pytest.raises(IngestError, match="header changed upstream"):
        fetch(source(server), raw_dir=raw, manifest=manifest)
    assert latest_path(source(server), raw).read_bytes() == good

    FakeGitHub.body = GOOD + b"Lvivska oblast,2024-01-02 10:00:00+00:00\n"  # ragged row
    with pytest.raises(IngestError, match="fields"):
        fetch(source(server), raw_dir=raw, manifest=manifest)

    FakeGitHub.body, FakeGitHub.truncate = GOOD * 1 + b"Lvivska oblast,a,b,True\n", True
    with pytest.raises(IngestError):
        fetch(source(server), raw_dir=raw, manifest=manifest)
    assert latest_path(source(server), raw).read_bytes() == good
    assert not [p for p in (raw / "volunteer").iterdir() if p.name.startswith(".download-")], "temp files cleaned up"


def test_offline_mode_and_unreachable_source(tmp_path: Path):
    src = source("http://127.0.0.1:9/never")
    with pytest.raises(IngestError, match="no local copy"):
        fetch(src, raw_dir=tmp_path, manifest=tmp_path / "m.jsonl", offline=True)
    with pytest.raises(IngestError, match="cannot reach"):
        fetch(src, raw_dir=tmp_path, manifest=tmp_path / "m.jsonl", timeout=2)
    latest_path(src, tmp_path).parent.mkdir(parents=True)
    latest_path(src, tmp_path).write_bytes(GOOD)
    assert fetch(src, raw_dir=tmp_path, manifest=tmp_path / "m.jsonl", offline=True).status == "offline"


def test_load_keeps_text_and_lineage(tmp_path: Path):
    path = tmp_path / "v.csv"
    path.write_bytes(GOOD + b'"Odeska oblast","not a date",,True\n')
    with duckdb.connect(str(tmp_path / "w.duckdb")) as con:
        n = load_source(con, Source("volunteer", "x", SRC_COLUMNS), path, "abc123")
        assert n == 2
        rows = con.execute("SELECT region, started_at, finished_at, _source_sha256 FROM raw.volunteer_alerts ORDER BY 1").fetchall()
    # Raw keeps values exactly as published (even unparseable ones); staging decides what is valid.
    assert rows == [
        ("Kyiv City", "2024-01-01 10:00:00+00:00", "2024-01-01 10:30:00+00:00", "abc123"),
        ("Odeska oblast", "not a date", None, "abc123"),
    ]


def test_duckdb_timezone_handles_kyiv_dst_without_network():
    con = duckdb.connect()
    kyiv = lambda ts: con.execute(f"SELECT timezone('Europe/Kyiv', TIMESTAMPTZ '{ts}')::VARCHAR").fetchone()[0]
    assert kyiv("2024-01-15 12:00:00+00") == "2024-01-15 14:00:00"  # winter UTC+2
    assert kyiv("2024-07-15 12:00:00+00") == "2024-07-15 15:00:00"  # summer UTC+3
    assert kyiv("2024-03-31 00:59:59+00") == "2024-03-31 02:59:59"  # last second of winter time
    assert kyiv("2024-03-31 01:00:00+00") == "2024-03-31 04:00:00"  # clocks jump forward
    assert con.execute("SELECT install_mode FROM duckdb_extensions() WHERE extension_name = 'icu'").fetchone()[0] == "STATICALLY_LINKED"
