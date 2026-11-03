"""Command-line interface.

    python -m app.cli ingest sample_docs/ my.pdf
    python -m app.cli ask "How long do access tokens last?"
    python -m app.cli list
    python -m app.cli delete 3
    python -m app.cli reindex
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.config import Settings
from app.ingest.loaders import LoaderError
from app.services import build_services


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docurag", description="RAG over your documents")
    parser.add_argument("--db", type=Path, help="SQLite path (default: RAG_DB_PATH or data/rag.sqlite3)")
    parser.add_argument("--provider", help="extractive | ollama | anthropic (default: RAG_PROVIDER)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="index files or directories")
    p_ingest.add_argument("paths", nargs="+", type=Path)
    p_ask = sub.add_parser("ask", help="ask a question")
    p_ask.add_argument("question")
    p_ask.add_argument("-k", type=int, default=None)
    p_ask.add_argument("--json", action="store_true", help="print the full JSON response")
    sub.add_parser("list", help="list indexed documents")
    p_del = sub.add_parser("delete", help="delete a document by id")
    p_del.add_argument("doc_id", type=int)
    sub.add_parser("reindex", help="re-embed all chunks with the current embedder")

    args = parser.parse_args(argv)
    overrides = {"db_path": args.db} if args.db else {}
    services = build_services(Settings.from_env(**overrides), provider=args.provider)
    try:
        return _run(args, services)
    finally:
        services.close()


def _run(args: argparse.Namespace, services) -> int:
    if args.command == "ingest":
        failed = 0
        for path in args.paths:
            if not path.exists():
                print(f"✗ {path}: not found", file=sys.stderr)
                failed += 1
                continue
            for file, result in services.ingestor.ingest_path(path):
                if isinstance(result, LoaderError):
                    print(f"✗ {file}: {result}", file=sys.stderr)
                    failed += 1
                else:
                    print(f"✓ {file}: {result.status} ({result.document.n_chunks} chunks)")
        return 1 if failed else 0

    if args.command == "ask":
        answer = services.pipeline.ask(args.question, k=args.k)
        if args.json:
            print(json.dumps(answer.to_dict(), ensure_ascii=False, indent=2))
            return 0
        print(answer.answer)
        for c in answer.citations:
            print(f"  [{c.n}] {c.title}")
        support = "n/a" if answer.support is None else f"{answer.support:.0%}"
        print(f"\nprovider={answer.provider} support={support} time={answer.timings_ms['total']} ms")
        for w in answer.warnings:
            print(f"warning: {w}", file=sys.stderr)
        return 0

    if args.command == "list":
        for d in services.store.list_documents():
            print(f"{d.id:>4}  {d.name:<40} {d.kind:<9} {d.n_chunks:>4} chunks  {d.created_at}")
        return 0

    if args.command == "delete":
        if services.store.delete_document(args.doc_id):
            print(f"deleted {args.doc_id}")
            return 0
        print(f"document {args.doc_id} not found", file=sys.stderr)
        return 1

    if args.command == "reindex":
        print(f"re-embedded {services.ingestor.reindex()} chunks with {services.embedder.id}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
