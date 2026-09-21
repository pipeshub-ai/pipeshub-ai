"""The `[n]` citation marker: one definition, three readers.

Systems write markers into their answers, grading strips them out of a claim
before judging it, and the citation metrics count them. Those three had their
own regex, and they had already drifted — only one handled the
`[1](https://…)` form a model produces when it writes a markdown link, so the
same answer yielded different citation sets depending on which layer looked.

A leaf module on purpose: metrics, grading and systems may all import it, and
it may import none of them.
"""

from __future__ import annotations

import re

# The trailing group matches the markdown-link form, so stripping a marker
# removes the whole `[1](url)` rather than leaving a bare `(url)` behind.
MARKER = re.compile(r"\[(\d+)\](?:\([^)\s]*\))?")


def cited_indices(text: str | None) -> tuple[int, ...]:
    """Display indices cited in `text`, in first-seen order, de-duplicated."""
    return tuple(dict.fromkeys(int(n) for n in MARKER.findall(text or "")))


def strip_markers(text: str) -> str:
    """`text` with its citation markers removed."""
    return MARKER.sub("", text).strip()
