"""Small, dependency-free text utilities shared by retrieval, generation and evaluation."""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
# Sentence boundary: end punctuation followed by whitespace, or a blank line / list item break.
_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+|\n\s*\n|\n(?=\s*(?:[-*•]|\d+[.)])\s)")
_CYRILLIC_RE = re.compile(r"[а-яіїєґ]", re.IGNORECASE)

STOPWORDS = frozenset(
    """
    a an the and or but if then else of to in on at by for with from as is are was were be been being it its
    this that these those there here what which who whom whose when where why how do does did can could should
    would will shall may might must i you he she we they me my your our their them his her not no yes so than
    too very just about into over under also any all some such only own same other more most each both few
    use used using get set
    і й та а але або чи як що це цей ця ці той та те ті не ні так там тут де коли чому хто який яка яке які
    для до від на в у з із зі по за при про через над під між же ж би б є був була було були бути мене мені
    ви вам вас ми нам нас вони їх його її він вона воно моє мій моя мої твій ваш наш їхній свій
    """.split()
)


def tokenize(text: str) -> list[str]:
    """Lower-cased word tokens (Unicode-aware, so Ukrainian works as well as English)."""
    return _TOKEN_RE.findall(text.lower())


_LATIN_SUFFIXES = ("ments", "ment", "ing", "ies", "ed", "ly", "es", "s")
_CYRILLIC_SUFFIXES = (
    "ання", "ення", "ями", "ами", "ові", "ого", "ому", "ими", "ій", "ий", "их", "ів", "ах", "ям", "ом", "ою",
    "ею", "ти", "ть", "ся", "а", "я", "і", "и", "у", "ю", "е", "о", "ї", "є",
)  # fmt: skip


def stem(token: str) -> str:
    """Crude stemmer: strip one common English/Ukrainian suffix, then keep at most 6 characters.

    It merges inflected forms ("hosting"/"hosted", "копії"/"копія") at the cost of occasional false merges.
    It is deliberately tiny and dependency-free; dense embeddings cover what it misses.
    """
    suffixes = _CYRILLIC_SUFFIXES if _CYRILLIC_RE.match(token) else _LATIN_SUFFIXES
    for suffix in suffixes:
        if len(token) - len(suffix) >= 3 and token.endswith(suffix):
            token = token[: -len(suffix)]
            break
    return token[:6]


def content_stems(text: str) -> list[str]:
    return [stem(t) for t in tokenize(text) if t not in STOPWORDS and (len(t) > 1 or t.isdigit())]


def split_sentences(text: str) -> list[str]:
    return [p.strip() for p in _SENTENCE_RE.split(text) if p and p.strip()]


def is_ukrainian(text: str) -> bool:
    return bool(_CYRILLIC_RE.search(text))


def not_found_message(question: str) -> str:
    if is_ukrainian(question):
        return "У завантажених документах немає відповіді на це питання."
    return "I couldn't find the answer to this question in the uploaded documents."
