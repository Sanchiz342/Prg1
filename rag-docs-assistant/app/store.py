"""SQLite persistence for documents, chunks and their embeddings.

Vectors are stored as float32 BLOBs next to the chunk text; the in-memory search index is rebuilt from here
whenever ``revision`` changes. For a portfolio-sized corpus (thousands of chunks) exact NumPy search is fast
and avoids an external vector DB; the store interface is small enough to swap for pgvector or Chroma.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app.ingest.chunker import Chunk

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    n_chunks INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    heading TEXT,
    page INTEGER,
    text TEXT NOT NULL,
    embedding BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass(frozen=True)
class DocumentRecord:
    id: int
    name: str
    kind: str
    sha256: str
    size_bytes: int
    n_chunks: int
    created_at: str


@dataclass(frozen=True)
class ChunkRecord:
    id: int
    doc_id: int
    document: str
    ordinal: int
    heading: str | None
    page: int | None
    text: str

    @property
    def search_text(self) -> str:
        return f"{self.heading}\n{self.text}" if self.heading else self.text


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(_SCHEMA)
        self._revision = 0

    @property
    def revision(self) -> int:
        """Bumped on every write; retrievers use it to know when to rebuild their index."""
        return self._revision

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- meta -----------------------------------------------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ---- documents ------------------------------------------------------------------------------------------
    @staticmethod
    def _doc(row: sqlite3.Row) -> DocumentRecord:
        return DocumentRecord(**{k: row[k] for k in row.keys()})

    def list_documents(self) -> list[DocumentRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM documents ORDER BY name").fetchall()
        return [self._doc(r) for r in rows]

    def get_document(self, doc_id: int) -> DocumentRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return self._doc(row) if row else None

    def get_document_by_name(self, name: str) -> DocumentRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM documents WHERE name = ?", (name,)).fetchone()
        return self._doc(row) if row else None

    def save_document(
        self,
        *,
        name: str,
        kind: str,
        sha256: str,
        size_bytes: int,
        chunks: list[Chunk],
        embeddings: np.ndarray,
    ) -> DocumentRecord:
        """Insert a document, atomically replacing an existing one with the same name."""
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings length mismatch")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        vectors = np.asarray(embeddings, dtype=np.float32)
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM documents WHERE name = ?", (name,))
            cur = self._conn.execute(
                "INSERT INTO documents(name, kind, sha256, size_bytes, n_chunks, created_at) VALUES(?,?,?,?,?,?)",
                (name, kind, sha256, size_bytes, len(chunks), now),
            )
            doc_id = cur.lastrowid
            self._conn.executemany(
                "INSERT INTO chunks(doc_id, ordinal, heading, page, text, embedding) VALUES(?,?,?,?,?,?)",
                [(doc_id, c.ordinal, c.heading, c.page, c.text, v.tobytes()) for c, v in zip(chunks, vectors)],
            )
            self._revision += 1
        doc = self.get_document(doc_id)
        assert doc is not None
        return doc

    def delete_document(self, doc_id: int) -> bool:
        with self._lock, self._conn:
            deleted = self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,)).rowcount > 0
            if deleted:
                self._revision += 1
        return deleted

    # ---- chunks ---------------------------------------------------------------------------------------------
    _CHUNK_SQL = (
        "SELECT c.id, c.doc_id, d.name AS document, c.ordinal, c.heading, c.page, c.text, c.embedding "
        "FROM chunks c JOIN documents d ON d.id = c.doc_id"
    )

    @staticmethod
    def _chunk(row: sqlite3.Row) -> ChunkRecord:
        return ChunkRecord(
            id=row["id"],
            doc_id=row["doc_id"],
            document=row["document"],
            ordinal=row["ordinal"],
            heading=row["heading"],
            page=row["page"],
            text=row["text"],
        )

    def get_chunk(self, chunk_id: int) -> ChunkRecord | None:
        with self._lock:
            row = self._conn.execute(self._CHUNK_SQL + " WHERE c.id = ?", (chunk_id,)).fetchone()
        return self._chunk(row) if row else None

    def list_chunks(self, doc_id: int | None = None) -> list[ChunkRecord]:
        """Chunk records without their vectors (safe even if vectors are from another embedder)."""
        sql, args = self._CHUNK_SQL, ()
        if doc_id is not None:
            sql, args = sql + " WHERE c.doc_id = ?", (doc_id,)
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY c.doc_id, c.ordinal", args).fetchall()
        return [self._chunk(r) for r in rows]

    def load_index_data(self, dim: int) -> tuple[list[ChunkRecord], np.ndarray]:
        """All chunks plus an (n, dim) embedding matrix, in the same order."""
        with self._lock:
            rows = self._conn.execute(self._CHUNK_SQL + " ORDER BY c.doc_id, c.ordinal").fetchall()
        records = [self._chunk(r) for r in rows]
        if not rows:
            return records, np.zeros((0, dim), dtype=np.float32)
        matrix = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        if matrix.shape[1] != dim:
            raise ValueError(f"Stored vectors have dim {matrix.shape[1]}, embedder has {dim}; run reindex")
        return records, matrix

    def update_embeddings(self, pairs: list[tuple[int, np.ndarray]]) -> None:
        with self._lock, self._conn:
            self._conn.executemany(
                "UPDATE chunks SET embedding = ? WHERE id = ?",
                [(np.asarray(v, dtype=np.float32).tobytes(), cid) for cid, v in pairs],
            )
            self._revision += 1

    def stats(self) -> dict[str, int]:
        with self._lock:
            docs = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chunks = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return {"documents": docs, "chunks": chunks}
