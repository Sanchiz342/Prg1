# Architecture

```
            ┌───────────── ingest ─────────────┐
 file ──▶ loaders ──▶ chunker ──▶ embedder ──▶ Store (SQLite: documents, chunks + float32 vectors)
 (pdf/md/txt) sections    chunks      vectors            │
                                                         ▼ rebuilt when Store.revision changes
            ┌───────────── ask ───────────────────── in-memory index (matrix + BM25)
 question ──▶ HybridRetriever ── dense top-N ─┐
                              └─ BM25 top-N ──┴─▶ RRF fusion ──▶ relevance gate ──▶ Generator ──▶ citation check
                                                                                    │ fails?        + groundedness
                                                                                    └─▶ extractive fallback
```

| Module | Responsibility |
|---|---|
| `app/ingest/loaders.py` | File → `Section`s with a heading path (Markdown) or page number (PDF). Tables become one sentence per row. |
| `app/ingest/chunker.py` | Sentence-aware packing to ~`RAG_CHUNK_WORDS` with sentence-level overlap; never crosses a section. |
| `app/ingest/service.py` | SHA-256 idempotency, atomic replace, reindex after an embedder change. |
| `app/embeddings.py` | `HashingEmbedder` (signed feature hashing: words, bigrams, char 3–5-grams) and optional sentence-transformers. |
| `app/store.py` | SQLite persistence; `revision` counter invalidates the search index. |
| `app/retrieval.py` | BM25 (inverted index), cosine search, Reciprocal Rank Fusion (k = 60), document filter, corpus IDF. |
| `app/generation.py` | Prompt construction and the `extractive` / `ollama` / `anthropic` generators. |
| `app/citations.py` | Marker normalisation/validation and the lexical groundedness score. |
| `app/rag.py` | The pipeline: retrieve → gate → generate (with fallback) → validate → `Answer`. |
| `app/evaluate.py` | Retrieval ablation and answer metrics over `eval/questions.json`. |
| `app/main.py`, `app/static/` | FastAPI app and the chat UI. |

## Why hybrid retrieval
Dense vectors handle paraphrase and inflection ("expire" ↔ "last", "копії" ↔ "копія"); BM25 nails exact rare tokens
(`ZK-409`, `X-Zirka-Signature`). Their scores live on different scales, so instead of a weighted sum the two rankings
are fused with RRF: `score = Σ 1 / (60 + rank)`. The evaluation command prints all three modes side by side.

## How "I don't know" works
1. **Relevance gate** (`RAG_MIN_RELEVANCE`): chunks with low cosine similarity are dropped (a strong BM25 match can
   rescue a borderline one). No chunk left → fixed "not found" answer, no generator call.
2. **Generator rules**: the LLM must answer with the exact "not found" sentence when the sources are insufficient;
   the extractive generator requires the question's terms to be covered, weighted by corpus IDF so ubiquitous words
   ("Zirka") can't carry an answer on their own.
3. **Grounded flag**: an answer without a single valid citation is reported as `grounded: false`.

## Citations
Generators emit `[n]` markers. `normalize_citations` canonicalises `[1, 2]` → `[1][2]`, drops numbers that don't
match a source (and reports them in `warnings`), and returns the cited sources in order of first use. The UI turns
markers into buttons that open the chunk with the question's terms highlighted.
