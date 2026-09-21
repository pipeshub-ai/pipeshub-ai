"""Citation metrics: integrity (deterministic), agreement with gold articles
(deterministic) and ALCE recall/precision from judged claim support."""

from __future__ import annotations

import re

from collections.abc import Callable, Collection, Sequence

from rapidfuzz import fuzz

from benchmarks.harness.citation_markers import cited_indices
from benchmarks.harness.models import Citation, ClaimSupport

_WHITESPACE = re.compile(r"\s+")
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MD_EMPHASIS = re.compile(r"(\*\*|__|`)")
_MATCH_PREFIX_CHARS = 300


def _normalise(text: str) -> str:
    """PipesHub blocks keep inline markdown (`[text](url)`, `**bold**`) that
    the plain-text article view doesn't have; drop it before matching."""
    text = _MD_EMPHASIS.sub("", _MD_LINK.sub(r"\1", text))
    return _WHITESPACE.sub(" ", text).strip().lower()


_WORD = re.compile(r"[a-z0-9]+")


def content_matches_source(content: str, source_text: str, threshold: int) -> bool:
    """The cited block text really comes from the cited article (fuzzy, so
    markdown/whitespace differences between PipesHub blocks and our text view
    don't count as mismatches). Table rows are serialized differently on each
    side (`Header: value, ...` vs `a | b`), so a block whose words all occur
    in the article also counts."""
    needle = _normalise(content)[:_MATCH_PREFIX_CHARS]
    if not needle:
        return True
    haystack = _normalise(source_text)
    if fuzz.partial_ratio(needle, haystack) >= threshold:
        return True
    words = set(_WORD.findall(_normalise(content)))
    if not words:
        return True
    return 100 * len(words & set(_WORD.findall(haystack))) / len(words) >= threshold


def citation_integrity(
    answer: str,
    citations: Sequence[Citation],
    *,
    known_record_ids: Collection[str],
    url_for: Callable[[Citation], str | None],
    text_for: Callable[[str], str],
    threshold: int,
) -> bool:
    markers = set(cited_indices(answer))
    indexes = {c.display_index for c in citations}
    if not markers <= indexes:
        return False
    for citation in citations:
        if citation.record_id not in known_record_ids:
            return False
        url = url_for(citation)
        if url is None or not content_matches_source(citation.content, text_for(url), threshold):
            return False
    return True


def cited_vs_gold(cited: Sequence[str], gold: Sequence[str]) -> tuple[float | None, float | None, bool | None]:
    """(precision, recall, all gold articles cited) over distinct articles."""
    if not gold:
        return None, None, None
    cited_set, gold_set = set(cited), set(gold)
    hits = len(cited_set & gold_set)
    precision = hits / len(cited_set) if cited_set else None
    return precision, hits / len(gold_set), gold_set <= cited_set


def alce_scores(claims: Sequence[ClaimSupport]) -> tuple[float | None, float | None]:
    """(citation recall, citation precision) — ALCE definitions on LongCite's scale."""
    if not claims:
        return None, None
    recall = sum(1.0 for c in claims if c.cited_display_indices and c.support == 1.0) / len(claims)
    judged = [(c, needed) for c in claims for needed in c.necessary]
    if not judged:
        return recall, None
    precision = sum(1.0 for c, needed in judged if c.support == 1.0 and needed) / len(judged)
    return recall, precision
