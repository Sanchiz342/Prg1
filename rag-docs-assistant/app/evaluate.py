"""Offline evaluation of retrieval and answer quality.

    python -m app.evaluate                       # sample docs + eval/questions.json, current provider
    python -m app.evaluate --provider anthropic  # same questions, answered by Claude
    python -m app.evaluate --json report.json    # machine-readable report (e.g. for CI regression checks)

Metrics
-------
Retrieval (answerable questions):
  hit@1 / hit@k   expected document (and section, if given) among the top 1 / top k sources
  MRR             mean reciprocal rank of the first correct source
Answers:
  answered        share of answerable questions that got a cited answer
  keyword recall  share of expected key facts (e.g. "24 hours") present in the answer
  citation prec.  share of cited sources that come from the expected document
  support         lexical groundedness of answer sentences in their cited sources (heuristic)
  abstention      share of unanswerable questions where the system declined to answer
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

from app.config import BASE_DIR, Settings
from app.services import Services, build_services

DEFAULT_QUESTIONS = BASE_DIR / "eval" / "questions.json"


@dataclass
class EvalItem:
    id: str
    question: str
    answerable: bool = True
    expected_doc: str | None = None
    expected_heading: str | None = None
    keywords: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict) -> "EvalItem":
        return cls(
            id=data["id"],
            question=data["question"],
            answerable=data.get("answerable", True),
            expected_doc=data.get("expected_doc"),
            expected_heading=data.get("expected_heading"),
            keywords=tuple(data.get("keywords", ())),
        )

    def matches(self, document: str, heading: str | None) -> bool:
        if document != self.expected_doc:
            return False
        return not self.expected_heading or self.expected_heading.lower() in (heading or "").lower()


def load_items(path: Path) -> list[EvalItem]:
    return [EvalItem.from_dict(d) for d in json.loads(path.read_text(encoding="utf-8"))]


def _rank(item: EvalItem, sources: list[tuple[str, str | None]]) -> int | None:
    for i, (doc, heading) in enumerate(sources, start=1):
        if item.matches(doc, heading):
            return i
    return None


def evaluate_retrieval(services: Services, items: list[EvalItem], k: int) -> dict[str, dict[str, float]]:
    """Ablation: the same questions through dense-only, BM25-only and hybrid retrieval."""
    out = {}
    answerable = [i for i in items if i.answerable]
    for mode in ("dense", "sparse", "hybrid"):
        ranks = []
        for item in answerable:
            hits = services.retriever.search(item.question, k, mode=mode)  # type: ignore[arg-type]
            ranks.append(_rank(item, [(h.chunk.document, h.chunk.heading) for h in hits]))
        out[mode] = {
            "hit@1": mean(r == 1 for r in ranks),
            f"hit@{k}": mean(r is not None for r in ranks),
            "mrr": mean(1 / r if r else 0.0 for r in ranks),
        }
    return out


def evaluate_answers(services: Services, items: list[EvalItem], k: int) -> tuple[dict[str, float], list[dict]]:
    rows = []
    for item in items:
        ans = services.pipeline.ask(item.question, k=k)
        row: dict = {
            "id": item.id,
            "question": item.question,
            "answerable": item.answerable,
            "grounded": ans.grounded,
            "answer": ans.answer,
            "provider": ans.provider,
            "support": ans.support,
            "cited": [c.title for c in ans.citations],
            "latency_ms": ans.timings_ms["total"],
        }
        if item.answerable:
            text = ans.answer.lower()
            row["keyword_recall"] = mean(kw.lower() in text for kw in item.keywords) if item.keywords else None
            row["citation_precision"] = (
                mean(c.document == item.expected_doc for c in ans.citations) if ans.citations else 0.0
            )
            row["rank"] = _rank(item, [(s.document, s.heading) for s in ans.sources])
        else:
            row["abstained"] = not ans.grounded
        rows.append(row)

    pos = [r for r in rows if r["answerable"]]
    neg = [r for r in rows if not r["answerable"]]
    summary = {
        "answered": mean(r["grounded"] for r in pos) if pos else 0.0,
        "keyword_recall": mean(r["keyword_recall"] for r in pos if r["keyword_recall"] is not None) if pos else 0.0,
        "citation_precision": mean(r["citation_precision"] for r in pos) if pos else 0.0,
        "support": mean(r["support"] for r in pos if r["support"] is not None) if any(
            r["support"] is not None for r in pos) else 0.0,
        "abstention": mean(r["abstained"] for r in neg) if neg else 0.0,
        "latency_ms_avg": mean(r["latency_ms"] for r in rows) if rows else 0.0,
    }
    return summary, rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate DocuRAG on a question set")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--docs", type=Path, default=None, help="documents to index (default: sample_docs)")
    parser.add_argument("--provider", default=None, help="extractive | ollama | anthropic")
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument("--json", type=Path, default=None, help="write the full report here")
    parser.add_argument("--min-hit", type=float, default=None, help="exit 1 if hybrid hit@k is below this")
    args = parser.parse_args(argv)

    settings = Settings.from_env(db_path=Path(":memory:"))
    services = build_services(settings, provider=args.provider)
    docs = args.docs or settings.samples_dir
    indexed = [r for _, r in services.ingestor.ingest_path(docs) if not isinstance(r, Exception)]
    items = load_items(args.questions)
    print(f"Indexed {len(indexed)} documents ({services.store.stats()['chunks']} chunks) from {docs}")
    print(f"Questions: {len(items)} ({sum(i.answerable for i in items)} answerable) · embedder "
          f"{services.embedder.id} · provider {services.pipeline.generator.name}\n")

    retrieval = evaluate_retrieval(services, items, args.k)
    print(f"{'retrieval':<10} {'hit@1':>7} {f'hit@{args.k}':>7} {'MRR':>7}")
    for mode, m in retrieval.items():
        print(f"{mode:<10} {m['hit@1']:>7.2f} {m[f'hit@{args.k}']:>7.2f} {m['mrr']:>7.2f}")

    summary, rows = evaluate_answers(services, items, args.k)
    print("\nanswers")
    for key, value in summary.items():
        print(f"  {key:<20} {value:.1f}" if key.startswith("latency") else f"  {key:<20} {value:.2f}")

    failures = [r for r in rows if (r["answerable"] and (not r["grounded"] or r.get("keyword_recall") == 0))
                or (not r["answerable"] and not r["abstained"])]
    if failures:
        print("\nneeds attention")
        for r in failures:
            print(f"  {r['id']} {r['question']!r} -> {r['answer'][:110]!r}")

    if args.json:
        report = {"retrieval": retrieval, "answers": summary, "rows": rows, "k": args.k,
                  "embedder": services.embedder.id, "provider": services.pipeline.generator.name}
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nreport written to {args.json}")
    services.close()

    if args.min_hit is not None and retrieval["hybrid"][f"hit@{args.k}"] < args.min_hit:
        print(f"hybrid hit@{args.k} below threshold {args.min_hit}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
