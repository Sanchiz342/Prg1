import numpy as np

from app.embeddings import HashingEmbedder
from app.retrieval import BM25
from app.text import content_stems, stem


def test_hashing_embedder_is_normalised_and_deterministic():
    e = HashingEmbedder(dim=256)
    a, b = e.embed(["access tokens expire", "access tokens expire"])
    assert np.isclose(np.linalg.norm(a), 1.0)
    assert np.allclose(a, b)
    assert e.embed([]).shape == (0, 256)


def test_hashing_embedder_prefers_related_text():
    e = HashingEmbedder()
    q, related, unrelated = e.embed(["when do tokens expire", "Access tokens expire after 24 hours.", "Borscht recipe."])
    assert q @ related > q @ unrelated


def test_stemmer_merges_inflections():
    assert stem("hosting") == stem("hosted")
    assert stem("refunds") == stem("refunded")
    assert stem("копії") == stem("копія")


def test_bm25_ranks_rare_term_first():
    docs = [content_stems(t) for t in ["error ZK-409 conflict", "error general text", "nothing here"]]
    scores = BM25(docs).scores(content_stems("ZK-409"))
    assert scores.argmax() == 0
    assert scores[2] == 0


def test_hybrid_search_finds_expected_section(seeded):
    hits = seeded.retriever.search("How long do access tokens last?", k=3)
    assert hits[0].chunk.document == "api-reference.md"
    assert "Authentication" in hits[0].chunk.heading


def test_search_in_ukrainian(seeded):
    hits = seeded.retriever.search("Як часто створюються резервні копії?", k=3)
    assert hits[0].chunk.document == "polityka-bezpeky.md"


def test_search_can_be_restricted_to_documents(seeded):
    billing = seeded.store.get_document_by_name("billing-faq.md")
    hits = seeded.retriever.search("How long do access tokens last?", k=5, doc_ids={billing.id})
    assert hits and all(h.chunk.document == "billing-faq.md" for h in hits)


def test_index_refreshes_after_delete(seeded):
    doc = seeded.store.get_document_by_name("api-reference.md")
    seeded.store.delete_document(doc.id)
    hits = seeded.retriever.search("What does error ZK-409 mean?", k=5)
    assert all(h.chunk.document != "api-reference.md" for h in hits)


def test_empty_index_returns_nothing(services):
    assert services.retriever.search("anything") == []
