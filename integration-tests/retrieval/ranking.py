"""Turning a list of search hits into a statement about documents.

Kept out of the test module so it can be exercised without a running stack.
The one piece of real logic here is collapsing hits to documents, and getting
it wrong quietly changes what every ranking assertion means.
"""

from __future__ import annotations

from typing import Any, Callable

NOT_IN_CORPUS = "<not in corpus>"
NO_VIRTUAL_ID = "<no virtual id>"


def virtual_id_of(hit: dict[str, Any]) -> str | None:
    """The record a hit came from, whichever shape the hit arrived in.

    Hits that have been through the chat flattening carry a top-level
    ``virtual_record_id``; anything still in the raw vector-store shape keeps it
    at ``metadata.virtualRecordId``. Reading only the first would label the
    second as untraceable and quietly drop it out of every ranking.
    """
    top_level = hit.get("virtual_record_id")
    if top_level:
        return str(top_level)
    metadata = hit.get("metadata") or {}
    nested = metadata.get("virtualRecordId") if isinstance(metadata, dict) else None
    return str(nested) if nested else None


def ranked_slugs(
    hits: list[dict[str, Any]], slug_of: Callable[[str | None], str]
) -> list[str]:
    """The documents behind the hits, best first, one entry per document.

    A document contributes several blocks and therefore several hits. Ranking
    questions are about documents, so the first appearance of each is kept and
    later ones dropped — otherwise one chatty document could fill the top of
    the list and hide everything ranked below it.

    Collapsing is done on the record id, never on the label. Every record
    outside the corpus shares one label, so folding by label would merge two
    unrelated foreign documents into a single position and let a "top two"
    assertion pass on something that actually ranked third.

    A hit with no id at all keeps its own position, because nothing says it is
    the same document as the last untraceable hit. That makes rankings stricter
    rather than more forgiving, which is the safe direction for a test.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for hit in hits:
        record_id = virtual_id_of(hit)
        if record_id is None:
            ordered.append(NO_VIRTUAL_ID)
            continue
        if record_id in seen:
            continue
        seen.add(record_id)
        ordered.append(slug_of(record_id))
    return ordered


def describe(hits: list[dict[str, Any]], slug_of: Callable[[str | None], str]) -> str:
    """The top hits, for a failure message that can be acted on."""
    lines = []
    for index, hit in enumerate(hits[:8]):
        slug = slug_of(virtual_id_of(hit))
        content = (hit.get("content") or "")[:70].replace("\n", " ")
        lines.append(f"    {index + 1}. [{slug}] score={hit.get('score')} {content!r}")
    return "\n".join(lines) or "    (no hits)"


def top(ranked: list[str]) -> str:
    """The best-ranked document, or a marker when nothing came back.

    Used in failure messages: indexing ``ranked[0]`` to explain why an
    assertion failed raises ``IndexError`` on an empty result and replaces the
    real problem — that search returned nothing — with a confusing one.
    """
    return ranked[0] if ranked else "<no results>"
