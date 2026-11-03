"""Citation markers ([1], [2][3], [1, 2]) — parsing, validation and a lexical groundedness check."""

from __future__ import annotations

import re

from app.text import content_stems, split_sentences

_MARKER_RE = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")
_MARKERS_BEFORE_PUNCT = re.compile(r"([.!?…])\s*((?:\[\d+(?:\s*[,;]\s*\d+)*\]\s*)+)")


def attach_citation(sentence: str, n: int) -> str:
    """'Text.' -> 'Text [n].' (marker goes before the final punctuation; a missing period is added)."""
    sentence = sentence.rstrip()
    if sentence and sentence[-1] in ".!?…":
        return f"{sentence[:-1]} [{n}]{sentence[-1]}"
    return f"{sentence.rstrip(':;,')} [{n}]."


def normalize_citations(answer: str, n_sources: int) -> tuple[str, list[int], list[int]]:
    """Canonicalise markers to [n] form and drop the ones that point at no source.

    Returns (clean_answer, cited_numbers_in_order_of_first_use, invalid_numbers).
    Removing invalid markers matters: a citation the user cannot click is worse than none.
    """
    used: list[int] = []
    invalid: list[int] = []

    def repl(match: re.Match[str]) -> str:
        numbers = [int(x) for x in re.findall(r"\d+", match.group(1))]
        good = []
        for x in numbers:
            if 1 <= x <= n_sources:
                good.append(x)
                if x not in used:
                    used.append(x)
            else:
                invalid.append(x)
        return "".join(f"[{x}]" for x in dict.fromkeys(good))

    text = _MARKER_RE.sub(repl, answer)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +([.,;:!?])", r"\1", text)
    return text.strip(), used, invalid


def sentence_support(answer: str, sources: dict[int, str], threshold: float = 0.5) -> float | None:
    """Share of answer sentences whose content words are mostly found in the sources they cite.

    A cheap, deterministic proxy for faithfulness: a sentence with no citation, or whose cited sources don't
    contain >= ``threshold`` of its content stems, counts as unsupported. It catches fabricated claims and
    wrong citations, but it is lexical — a correct paraphrase with new words can score low. Returns None
    when the answer has no checkable sentences (e.g. an abstention).
    """
    # Move markers that follow punctuation ("Text. [1]") in front of it, so sentences keep their markers.
    answer = _MARKERS_BEFORE_PUNCT.sub(lambda m: f" {m.group(2).strip()}{m.group(1)} ", answer)
    source_stems = {n: set(content_stems(t)) for n, t in sources.items()}
    checked = supported = 0
    for sentence in split_sentences(answer):
        cited = {int(x) for group in _MARKER_RE.findall(sentence) for x in re.findall(r"\d+", group)}
        stems = set(content_stems(_MARKER_RE.sub(" ", sentence)))
        stems = {s for s in stems if not s.isdigit() or len(s) > 1}
        if len(stems) < 2:
            continue  # headings, "Yes.", list bullets...
        checked += 1
        pool: set[str] = set().union(*(source_stems.get(n, set()) for n in cited)) if cited else set()
        if pool and len(stems & pool) / len(stems) >= threshold:
            supported += 1
    return round(supported / checked, 3) if checked else None
