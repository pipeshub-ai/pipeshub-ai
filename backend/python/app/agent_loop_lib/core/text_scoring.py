"""Generic keyword token-overlap scoring — the fallback/tie-breaking
behavior shared by every keyword-based search index in this library
(skills, tools, and any future one), so none of them can silently drift
from a second copy of the same tokenizer/scoring rule.

Originally lived under `modules/providers/skills/text_scoring.py`
(skill-specific); promoted here once `tools/index.py` needed the exact same
scoring for tool search — see that module's docstring. Skill-specific
helpers (e.g. building a skill's search haystack from its metadata) stay in
`modules/providers/skills/text_scoring.py`, which re-exports the two names
below for backward compatibility.
"""

from __future__ import annotations

import re

__all__ = ["tokenize", "keyword_overlap_score"]

_WORD_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def keyword_overlap_score(query_tokens: set[str], haystack: set[str]) -> tuple[float, set[str]]:
    """`(score, matched_tokens)` — score is the fraction of query tokens
    found in the haystack (0 if no overlap)."""
    if not query_tokens:
        return 0.0, set()
    overlap = query_tokens & haystack
    if not overlap:
        return 0.0, overlap
    return len(overlap) / len(query_tokens), overlap
