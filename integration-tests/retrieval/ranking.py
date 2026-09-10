"""Turning a list of search hits into a statement about documents.

Kept out of the test module so it can be exercised without a running stack.
The one piece of real logic here is collapsing hits to documents, and getting
that wrong would quietly change what every ranking assertion means.
"""

from __future__ import annotations

from typing import Any, Callable

NOT_IN_CORPUS = "<not in corpus>"
NO_VIRTUAL_ID = "<no virtual id>"


def ranked_slugs(
    hits: list[dict[str, Any]], slug_of: Callable[[str | None], str]
) -> list[str]:
    """The documents behind the hits, best first, one entry per document.

    A document contributes several blocks and therefore several hits. Ranking
    questions are about documents, so the first appearance of each is kept and
    later ones dropped — otherwise one chatty document could fill the top of
    the list and hide everything ranked below it.
    """
    ordered: list[str] = []
    for hit in hits:
        slug = slug_of(hit.get("virtual_record_id"))
        if slug not in ordered:
            ordered.append(slug)
    return ordered


def describe(hits: list[dict[str, Any]], slug_of: Callable[[str | None], str]) -> str:
    """The top hits, for a failure message that can be acted on."""
    lines = []
    for index, hit in enumerate(hits[:8]):
        slug = slug_of(hit.get("virtual_record_id"))
        content = (hit.get("content") or "")[:70].replace("\n", " ")
        lines.append(f"    {index + 1}. [{slug}] score={hit.get('score')} {content!r}")
    return "\n".join(lines) or "    (no hits)"
