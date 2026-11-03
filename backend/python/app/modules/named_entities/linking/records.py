"""The readable records that mention the linked entities, and for several names
together, where they appear near each other.

Every record set starts from ``list_accessible_entity_records`` (permission
checked in the query). For "all", the names are intersected from the one with
the fewest readable records: the others are matched inside that set
(``get_records_for_named_entities(within=...)``), so a name on thousands of
records never meets a scan cap. Co-mention uses the block indexes on the mention
edges, in the same index space as search hits and ``fetch_record``'s
``start_block``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from itertools import zip_longest
from typing import TYPE_CHECKING, Any

from app.modules.retrieval.entity_permissions import list_accessible_entity_records

if TYPE_CHECKING:
    from app.modules.named_entities.linking.service import LinkResult
    from app.modules.retrieval.entity_permissions import EntityAccessContext
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

NAMED_ENTITY = "named_entity"
RECORDS_PER_ENTITY = 100
MAX_SCOPE_RECORDS = 500
NEARBY_BLOCKS = 2
LISTING_CONCURRENCY = 4

SAME_BLOCK, NEARBY, SAME_RECORD = 0, 1, 2


@dataclass(frozen=True)
class NameRecords:
    mention: str
    record_ids: list[str]
    """Readable records mentioning any accepted entity of the name, newest first."""
    complete: bool
    """False when an entity had more readable records than were listed."""

    @property
    def count_label(self) -> str:
        return f"{len(self.record_ids)}{'' if self.complete else '+'}"


@dataclass
class RecordScope:
    per_name: list[NameRecords]
    record_ids: list[str] = field(default_factory=list)
    virtual_record_ids: list[str] = field(default_factory=list)
    together: dict[str, tuple[int, int]] = field(default_factory=dict)
    """For "all": record id to (tier, block) where the names appear together."""
    record_names: dict[str, str] = field(default_factory=dict)
    note: str = ""


def co_mention(blocks: list[set[int]], window: int = NEARBY_BLOCKS) -> tuple[int, int]:
    """``(tier, anchor block)`` for one record, given each name's block indexes.
    Tier 0: one block holds every name; 1: every name within ``window`` blocks of
    one of the first name's blocks; 2: only the same record."""
    if not blocks or any(not names for names in blocks):
        return SAME_RECORD, -1
    first, *others = blocks
    nearby = -1
    for anchor in sorted(first):
        if all(anchor in other for other in others):
            return SAME_BLOCK, anchor
        if nearby < 0 and all(any(abs(anchor - block) <= window for block in other) for other in others):
            nearby = anchor
    return (NEARBY, nearby) if nearby >= 0 else (SAME_RECORD, -1)


async def readable_records(
    graph: IGraphDBProvider,
    context: EntityAccessContext,
    links: list[LinkResult],
    *,
    match_all: bool,
) -> RecordScope:
    rows_by_key: dict[str, dict[str, Any]] = {}
    gate = asyncio.Semaphore(LISTING_CONCURRENCY)

    async def _entity(entity_id: str) -> tuple[list[dict[str, Any]], bool]:
        async with gate:
            page = await list_accessible_entity_records(
                graph, context, entity_id=entity_id, entity_type=NAMED_ENTITY, limit=RECORDS_PER_ENTITY,
            )
        return page.records, page.next_cursor is None and not page.capped

    linked = [link for link in links if link.accepted]
    listings = await asyncio.gather(*(
        asyncio.gather(*(_entity(c.entity_id) for c in link.accepted)) for link in linked
    ))
    per_name: list[NameRecords] = []
    for link, pages in zip(linked, listings):
        ordered: list[str] = []
        for rows, _ in pages:
            for row in rows:
                key = str(row.get("_key") or "")
                if key and row.get("virtualRecordId"):
                    rows_by_key.setdefault(key, row)
                    if key not in ordered:
                        ordered.append(key)
        per_name.append(NameRecords(mention=link.mention.name, record_ids=ordered, complete=all(done for _, done in pages)))

    scope = RecordScope(per_name=per_name)
    if not per_name or (match_all and len(linked) < len(links)):
        # A name with no entity has no records, so no record mentions every name;
        # the names that did link keep their counts.
        return scope
    if not match_all or len(per_name) == 1:
        # Round-robin over the names, so the scope cap below never drops a whole name.
        selected = list(dict.fromkeys(
            key for group in zip_longest(*(name.record_ids for name in per_name)) for key in group if key
        ))
        partial = [name.mention for name in per_name if not name.complete]
        if partial:
            # Listing is newest first, so the older records of a popular name are
            # not in this search at all; say so rather than imply they were.
            scope.note = (
                f"{', '.join(repr(name) for name in partial)}: more than {RECORDS_PER_ENTITY} readable "
                "records each, so only the newest were searched; add a query or a rarer name to reach "
                "older ones. "
            )
    else:
        selected, sampled = await _intersection(graph, context.org_id, linked, per_name)
        if sampled:
            scope.note = (
                f"Every name is on more than {RECORDS_PER_ENTITY} readable records, so only the newest were "
                "intersected; add a rarer name or a query to narrow it. "
            )
        if selected:
            # Every record that mentions them all is searched; the nearest come
            # first, so they are the ones the scope cap keeps.
            scope.together = await _together(graph, context.org_id, linked, selected)
            selected = sorted(selected, key=lambda key: scope.together.get(key, (SAME_RECORD, -1))[0])
    if len(selected) > MAX_SCOPE_RECORDS:
        scope.note += f"Only {MAX_SCOPE_RECORDS} of the {len(selected)} readable records were searched. "
        selected = selected[:MAX_SCOPE_RECORDS]
    scope.record_ids = selected
    scope.record_names = {key: str(rows_by_key[key].get("recordName") or "") for key in selected if key in rows_by_key}
    scope.virtual_record_ids = list(dict.fromkeys(
        str(rows_by_key[key]["virtualRecordId"]) for key in selected if key in rows_by_key
    ))
    return scope


async def _intersection(
    graph: IGraphDBProvider, org_id: str, linked: list[LinkResult], per_name: list[NameRecords],
) -> tuple[list[str], bool]:
    """Records mentioning every name, and whether the start was only a sample."""
    order = sorted(range(len(per_name)), key=lambda i: (not per_name[i].complete, len(per_name[i].record_ids)))
    sampled = not per_name[order[0]].complete
    selected = per_name[order[0]].record_ids
    for index in order[1:]:
        if not selected:
            break
        ids = [c.entity_id for c in linked[index].accepted]
        found = await graph.get_records_for_named_entities(org_id, entity_ids=ids, within=selected)
        within = {str(hit.get("recordId")) for hit in (found or {}).get("hits") or []}
        selected = [key for key in selected if key in within]
    return selected, sampled


async def _together(
    graph: IGraphDBProvider, org_id: str, linked: list[LinkResult], record_ids: list[str],
) -> dict[str, tuple[int, int]]:
    owner = {c.entity_id: index for index, link in enumerate(linked) for c in link.accepted}
    rows = await graph.get_named_entity_mentions_in_records(org_id, sorted(owner), record_ids)
    blocks: dict[str, list[set[int]]] = {key: [set() for _ in linked] for key in record_ids}
    for row in rows:
        index = owner.get(str(row.get("entityId")))
        key = str(row.get("recordId"))
        if index is None or key not in blocks:
            continue
        blocks[key][index].update(int(b) for b in row.get("blockIndexes") or [] if isinstance(b, int))
    return {key: co_mention(names) for key, names in blocks.items()}
