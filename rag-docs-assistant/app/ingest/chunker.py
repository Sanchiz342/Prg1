"""Sentence-aware chunking with overlap. Chunks never cross a section (heading / page) boundary,
so every chunk can be cited precisely. Inside a chunk, sentences / list items are separated by newlines."""

from __future__ import annotations

from dataclasses import dataclass

from app.ingest.loaders import Section
from app.text import split_sentences


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    text: str
    heading: str | None
    page: int | None

    @property
    def search_text(self) -> str:
        """Text used for indexing: the heading path adds useful context to short chunks."""
        return f"{self.heading}\n{self.text}" if self.heading else self.text


def _units(text: str, max_words: int) -> list[str]:
    units: list[str] = []
    for sentence in split_sentences(text):
        words = sentence.split()  # also collapses line breaks inside a sentence
        sentence = " ".join(words)
        if len(words) <= max_words:
            units.append(sentence)
        else:  # a "sentence" longer than a chunk (tables, code, run-on text): hard-split it
            units.extend(" ".join(words[i : i + max_words]) for i in range(0, len(words), max_words))
    return units


def chunk_sections(sections: list[Section], max_words: int = 120, overlap_words: int = 30) -> list[Chunk]:
    if max_words <= 0 or not 0 <= overlap_words < max_words:
        raise ValueError("need max_words > 0 and 0 <= overlap_words < max_words")

    texts: list[tuple[str, Section]] = []
    for section in sections:
        current: list[str] = []
        length = 0
        for unit in _units(section.text, max_words):
            n = len(unit.split())
            if current and length + n > max_words:
                texts.append(("\n".join(current), section))
                # Carry the trailing sentences (up to overlap_words) into the next chunk.
                kept: list[str] = []
                kept_len = 0
                for sentence in reversed(current):
                    size = len(sentence.split())
                    if kept_len + size > overlap_words:
                        break
                    kept.insert(0, sentence)
                    kept_len += size
                current, length = kept, kept_len
            current.append(unit)
            length += n
        if current:
            texts.append(("\n".join(current), section))

    return [Chunk(ordinal=i, text=t, heading=s.heading, page=s.page) for i, (t, s) in enumerate(texts)]
