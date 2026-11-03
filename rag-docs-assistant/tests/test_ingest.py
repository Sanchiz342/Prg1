import pytest

from app.ingest.chunker import chunk_sections
from app.ingest.loaders import LoaderError, Section, detect_kind, load_file, load_markdown
from tests.conftest import make_pdf


def test_markdown_sections_keep_heading_path():
    md = "# Guide\nIntro.\n## Install\nRun pip.\n### Linux\nUse apt.\n## Usage\nRun it.\n"
    sections = load_markdown(md)
    assert [(s.heading, s.text) for s in sections] == [
        ("Guide", "Intro."),
        ("Guide › Install", "Run pip."),
        ("Guide › Install › Linux", "Use apt."),
        ("Guide › Usage", "Run it."),
    ]


def test_markdown_ignores_headings_inside_code_blocks():
    md = "# Real\n```\n# not a heading\n```\nText."
    sections = load_markdown(md)
    assert len(sections) == 1
    assert sections[0].heading == "Real"
    assert "# not a heading" in sections[0].text


def test_markdown_tables_become_sentences():
    md = "# Errors\n| Code | Meaning |\n|---|---|\n| E1 | Token expired |\n"
    (section,) = load_markdown(md)
    assert section.text == "Code — Meaning.\nE1 — Token expired."


def test_pdf_pages_are_tracked():
    data = make_pdf(["Alpha page talks about invoices.", "Beta page talks about refunds."])
    kind, sections = load_file("report.pdf", data)
    assert kind == "pdf"
    assert [s.page for s in sections] == [1, 2]
    assert "refunds" in sections[1].text


def test_rejects_unsupported_and_broken_files():
    with pytest.raises(LoaderError):
        detect_kind("photo.png")
    with pytest.raises(LoaderError):
        load_file("broken.pdf", b"not a pdf at all")
    with pytest.raises(LoaderError):
        load_file("empty.md", b"   \n")


def test_cp1251_text_is_decoded():
    _, sections = load_file("legacy.txt", "Привіт, світ.".encode("cp1251"))
    assert sections[0].text == "Привіт, світ."


def test_chunks_respect_size_overlap_and_sections():
    sentences = " ".join(f"Sentence number {i} has five words." for i in range(40))  # 6 words each
    sections = [Section(text=sentences, heading="A"), Section(text="Short tail.", heading="B")]
    chunks = chunk_sections(sections, max_words=30, overlap_words=12)

    assert all(len(c.text.split()) <= 30 + 12 for c in chunks)
    assert chunks[-1].heading == "B" and chunks[-1].text == "Short tail."
    a_chunks = [c for c in chunks if c.heading == "A"]
    # Consecutive chunks share their boundary sentences: 12 overlap words = the last two 6-word sentences ...
    assert a_chunks[0].text.split("\n")[-2:] == a_chunks[1].text.split("\n")[:2]
    # ... and together they cover every sentence.
    covered = {line for c in a_chunks for line in c.text.split("\n")}
    assert len(covered) == 40
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_ingest_is_idempotent_and_replaces_changed_files(services):
    first = services.ingestor.ingest_bytes("notes.md", b"# Notes\nThe launch date is May 5.")
    again = services.ingestor.ingest_bytes("notes.md", b"# Notes\nThe launch date is May 5.")
    changed = services.ingestor.ingest_bytes("notes.md", b"# Notes\nThe launch date is June 9.")

    assert (first.status, again.status, changed.status) == ("added", "unchanged", "replaced")
    assert services.store.stats()["documents"] == 1
    texts = [c.text for c in services.store.list_chunks()]
    assert texts == ["The launch date is June 9."]


def test_file_names_are_sanitised(services):
    result = services.ingestor.ingest_bytes("../../etc/passwd.txt", b"hello there")
    assert result.document.name == "passwd.txt"
