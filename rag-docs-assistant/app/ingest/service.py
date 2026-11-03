"""Ingestion: file bytes -> sections -> chunks -> embeddings -> store. Idempotent by content hash."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Literal

from app.embeddings import Embedder
from app.ingest.chunker import chunk_sections
from app.ingest.loaders import SUPPORTED_EXTENSIONS, LoaderError, load_file
from app.store import DocumentRecord, Store

EMBEDDER_META_KEY = "embedder_id"


@dataclass(frozen=True)
class IngestResult:
    status: Literal["added", "replaced", "unchanged"]
    document: DocumentRecord


def safe_name(filename: str) -> str:
    name = PurePath(filename.replace("\\", "/")).name.strip()
    if not name or name in {".", ".."}:
        raise LoaderError("Empty file name")
    return name[:200]


class Ingestor:
    def __init__(self, store: Store, embedder: Embedder, chunk_words: int = 120, chunk_overlap: int = 30) -> None:
        self.store = store
        self.embedder = embedder
        self.chunk_words = chunk_words
        self.chunk_overlap = chunk_overlap

    def check_embedder(self) -> bool:
        """True if stored vectors were produced by the current embedder (or the store is empty)."""
        stored = self.store.get_meta(EMBEDDER_META_KEY)
        if stored is None or self.store.stats()["chunks"] == 0:
            self.store.set_meta(EMBEDDER_META_KEY, self.embedder.id)
            return True
        return stored == self.embedder.id

    def ingest_bytes(self, filename: str, data: bytes) -> IngestResult:
        name = safe_name(filename)
        digest = hashlib.sha256(data).hexdigest()
        existing = self.store.get_document_by_name(name)
        if existing and existing.sha256 == digest:
            return IngestResult("unchanged", existing)

        kind, sections = load_file(name, data)
        chunks = chunk_sections(sections, self.chunk_words, self.chunk_overlap)
        if not chunks:
            raise LoaderError("No text chunks could be produced from the file")
        vectors = self.embedder.embed([c.search_text for c in chunks])
        doc = self.store.save_document(
            name=name, kind=kind, sha256=digest, size_bytes=len(data), chunks=chunks, embeddings=vectors
        )
        self.store.set_meta(EMBEDDER_META_KEY, self.embedder.id)
        return IngestResult("replaced" if existing else "added", doc)

    def ingest_path(self, path: Path) -> list[tuple[Path, IngestResult | LoaderError]]:
        """Ingest a file or every supported file under a directory."""
        files = (
            sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS)
            if path.is_dir()
            else [path]
        )
        results: list[tuple[Path, IngestResult | LoaderError]] = []
        for file in files:
            try:
                results.append((file, self.ingest_bytes(file.name, file.read_bytes())))
            except LoaderError as exc:
                results.append((file, exc))
        return results

    def reindex(self) -> int:
        """Re-embed every chunk with the current embedder (after switching embedders)."""
        records = self.store.list_chunks()
        if records:
            vectors = self.embedder.embed([r.search_text for r in records])
            self.store.update_embeddings([(r.id, v) for r, v in zip(records, vectors)])
        self.store.set_meta(EMBEDDER_META_KEY, self.embedder.id)
        return len(records)
