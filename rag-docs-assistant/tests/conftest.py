from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.services import Services, build_services

ROOT = Path(__file__).resolve().parent.parent


def make_pdf(pages: list[str]) -> bytes:
    """Build a minimal valid PDF (one Helvetica text line per page) without any PDF-writing dependency."""
    objects: list[bytes] = []
    n = len(pages)
    page_ids = [3 + 2 * i for i in range(n)]
    font_id = 3 + 2 * n
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    for i, text in enumerate(pages):
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {page_ids[i] + 1} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.from_env(
        db_path=tmp_path / "test.sqlite3",
        provider="extractive",
        embedder="hashing",
        api_key=None,
        seed_samples=False,
        samples_dir=ROOT / "sample_docs",
    )


@pytest.fixture
def services(settings: Settings) -> Services:
    svc = build_services(settings)
    yield svc
    svc.close()


@pytest.fixture
def seeded(services: Services) -> Services:
    services.ingestor.ingest_path(ROOT / "sample_docs")
    return services
