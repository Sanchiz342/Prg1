"""Quality regression gate: fails if a change makes retrieval or abstention on the sample set worse."""

from app.evaluate import DEFAULT_QUESTIONS, evaluate_answers, evaluate_retrieval, load_items


def test_quality_does_not_regress(seeded):
    items = load_items(DEFAULT_QUESTIONS)
    retrieval = evaluate_retrieval(seeded, items, k=5)
    summary, _ = evaluate_answers(seeded, items, k=5)

    assert retrieval["hybrid"]["hit@5"] >= 0.95
    assert retrieval["hybrid"]["mrr"] >= 0.85
    assert summary["answered"] >= 0.9
    assert summary["keyword_recall"] >= 0.85
    assert summary["citation_precision"] >= 0.9
    assert summary["abstention"] == 1.0
