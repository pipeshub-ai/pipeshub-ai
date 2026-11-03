"""Records found by their name whose content is not indexed yet.

Content search reads only indexed text, so a record that was just synced, or
whose automatic indexing is off, is invisible to it. Every record has a title
entry in the entity index, and a search over those entries finds it by name or
meaning. Each hit passes the same graph permission check as any entity hit
(``entity_permissions.search_entities_for_user``); this module only keeps the
ones whose content is not readable yet and shapes them for the model and for
the public search response, labelled with ``utils.not_indexed``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from app.config.constants.arangodb import FOLDER_MIME_TYPES
from app.modules.retrieval.entity_permissions import (
    RECORD_ENTITY_TYPE,
    EntityAccessContext,
    EntityHit,
    search_entities_for_user,
)
from app.utils.not_indexed import not_indexed_note

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.modules.transformers.entity_vectorstore import EntityVectorStore
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

# Titles of indexed records share the search, and only the unindexed ones are
# kept, so the vector search reads more than the caller shows.
TITLE_SEARCH_TOP_K = 25
MAX_UNINDEXED_MATCHES = 10
# Cosine of the query to a record name below which the record is not a match.
# Measured with text-embedding-3-small: meaning matches 0.50-0.95 ("staff policy
# document" to "Employee handbook" 0.59), near misses up to 0.37, gibberish up to
# 0.25, unrelated text up to 0.17. Other models spread differently; re-measure.
MIN_TITLE_COSINE = 0.45
_MAX_NAME_CHARS = 200

TITLE_BLOCK_HEADER = (
    "Records whose names match, but whose content is not indexed yet "
    "(search cannot see their text):"
)


class _Shortener(Protocol):
    def get_or_create_short_id(self, full_id: str) -> str: ...


@dataclass(frozen=True)
class UnindexedMatch:
    record_id: str
    name: str
    score: float
    indexing_status: str | None
    connector: str | None = None
    connector_id: str | None = None
    record_type: str | None = None
    virtual_record_id: str | None = None

    @property
    def note(self) -> str:
        return not_indexed_note(self.indexing_status)

    def payload(self) -> dict[str, Any]:
        """The additive ``unindexedMatches`` entry of the public search response."""
        return {
            "recordId": self.record_id,
            "recordName": self.name,
            "connector": self.connector,
            "connectorId": self.connector_id,
            "recordType": self.record_type,
            "indexingStatus": self.indexing_status,
            "note": self.note,
        }


def _one_line(name: str) -> str:
    """A name on one line, so it cannot open a line of its own in the result."""
    text = " ".join(name.split())
    return text if len(text) <= _MAX_NAME_CHARS else text[: _MAX_NAME_CHARS - 1] + "…"


def _names_the_query(query: str, name: str) -> bool:
    """Whether the name contains a word of the query: a literal match always passes."""
    text = name.casefold()
    return any(word in text for word in re.findall(r"\w{3,}", query.casefold()))


def unindexed_matches(
    hits: Iterable[EntityHit], context: EntityAccessContext, limit: int = MAX_UNINDEXED_MATCHES,
) -> list[UnindexedMatch]:
    """The record hits whose content is not indexed, best score first."""
    matches: list[UnindexedMatch] = []
    for hit in sorted(hits, key=lambda h: h.score, reverse=True):
        if hit.entity_type != RECORD_ENTITY_TYPE or hit.indexed or not hit.records:
            continue
        row = hit.records[0]
        if row.get("mimeType") in FOLDER_MIME_TYPES:  # folders are never indexed
            continue
        connector_id = row.get("connectorId")
        matches.append(UnindexedMatch(
            record_id=hit.entity_id,
            name=_one_line(str(row.get("recordName") or hit.name or hit.entity_id)),
            score=hit.score,
            indexing_status=hit.indexing_status,
            connector=context.app_names.get(connector_id) or row.get("connectorName"),
            connector_id=connector_id,
            record_type=row.get("recordType"),
            virtual_record_id=row.get("virtualRecordId"),
        ))
        if len(matches) >= limit:
            break
    return matches


async def find_unindexed_matches(
    store: EntityVectorStore,
    graph_provider: IGraphDBProvider,
    context: EntityAccessContext,
    query: str,
    *,
    limit: int = MAX_UNINDEXED_MATCHES,
) -> list[UnindexedMatch]:
    """Permitted records matching ``query`` by name whose content is not indexed.

    Raises what ``search_entities_for_user`` raises: ``EntitySearchTimedOut``
    and ``EntityAccessError``. Callers add a gap note; content results stand.
    """
    hits = await search_entities_for_user(
        store, graph_provider, context, query,
        entity_types=[RECORD_ENTITY_TYPE], top_k=TITLE_SEARCH_TOP_K, include_named=False,
    )
    candidates = unindexed_matches(hits, context, TITLE_SEARCH_TOP_K)
    if not candidates:
        return []
    # The hit scores are rank fused (the best hit scores the same for any query),
    # so relevance is the dense cosine of the query to the name.
    cosines = await store.dense_similarities(query, [m.name for m in candidates])
    return [
        m for m, cosine in zip(candidates, cosines)
        if cosine >= MIN_TITLE_COSINE or _names_the_query(query, m.name)
    ][:limit]


def _line(match: UnindexedMatch, shortener: _Shortener | None) -> str:
    record_id = shortener.get_or_create_short_id(match.record_id) if shortener else match.record_id
    parts = [match.name, match.connector or "", f"status: {match.indexing_status or 'unknown'}"]
    return "- " + " | ".join(p for p in parts if p) + f" | Record ID: {record_id}"


def render_title_block(
    matches: list[UnindexedMatch], max_chars: int, shortener: _Shortener | None = None,
) -> tuple[str, list[UnindexedMatch]]:
    """The text block for ``matches`` in at most ``max_chars``, and the matches it
    names. Matches are dropped from the end (lowest score) until it fits; a
    block that cannot hold even one is empty."""
    for count in range(len(matches), 0, -1):
        shown = matches[:count]
        lines = [TITLE_BLOCK_HEADER, *(_line(m, shortener) for m in shown)]
        if count < len(matches):
            lines.append(f"({len(matches) - count} more left out to fit the result size.)")
        statuses = list(dict.fromkeys(m.indexing_status for m in shown))
        lines.extend(not_indexed_note(status) for status in statuses)
        text = "\n".join(lines) + "\n\n"
        if len(text) <= max_chars:
            return text, shown
    return "", []
