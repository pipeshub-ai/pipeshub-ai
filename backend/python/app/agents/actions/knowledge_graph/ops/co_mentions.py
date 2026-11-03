"""``find_co_mentioned_entities``: the named entities mentioned in the records that
mention an entity, with how many of them, counted only over records the user can read.

The readable records come first, from the same access-checked listing as
``find_records_by_entity``; the mentions are read from those records only, and every
name is spelled as those records spell it. So nothing about a record the user cannot
read (its names, or that it exists) reaches the reply.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.ops.entity_discovery import (
    _named_entities_enabled,
)
from app.agents.actions.knowledge_graph.ops.entity_filters import (
    load_entity_access_context,
    remember_entities,
)
from app.agents.actions.knowledge_graph.ops.entity_records import (
    LOOKUP_FAILED_MSG,
    record_line,
)
from app.agents.actions.knowledge_graph.ops.entity_search import (
    COUNT_NOTE,
    COVERAGE_NOTE,
    DEADLINE_SECONDS,
)
from app.agents.actions.knowledge_graph.views import _short
from app.agents.agent_loop.entity_search_tool import entity_search_offered_in
from app.modules.agents.qna.chat_state import remember_record_ids
from app.modules.named_entities.domain.kinds import (
    VALUE_KINDS,
    EntityKind,
    kind_family,
    spec_for,
)
from app.modules.retrieval.entity_permissions import (
    NAMED_ENTITY_TYPE,
    SEARCH_SCOPE_MAX_ENTITIES,
    SEARCH_SCOPE_MAX_RECORDS,
    SEARCH_SCOPE_MAX_SCAN,
    EntityAccessError,
    list_accessible_entity_records,
    spellings_on,
)
from app.modules.retrieval.provenance import graph_record_refs, note_retrieved_by
from app.utils.chat_helpers import (
    get_record_id_shortener_if_enabled,
    resolve_frontend_url,
)

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState
    from app.modules.retrieval.entity_permissions import EntityAccessContext
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50
_EXAMPLES = 3
# The example records are printed in full and remembered for citation: a bound keeps a
# 50-entity reply readable.
_MAX_EXAMPLE_RECORDS = 30
NO_RECORDS_MSG = "No readable records mention that entity."
NAMED_ENTITIES_DISABLED_MSG = "Named-entity lookup is not enabled."
TOO_SLOW_MSG = "Listing the records took too long; try again, or pass fewer entity ids."


@dataclass
class _Readable:
    """The readable records that mention the entity, one per document."""

    records: list[dict[str, Any]] = field(default_factory=list)
    complete: bool = True


def _default_kinds() -> frozenset[EntityKind]:
    """Named things, not contact details: an email or phone pattern is listed only when asked for."""
    return frozenset(kind for kind in EntityKind if (spec := spec_for(kind)) is not None and spec.resolvable)


def _wanted_kinds(requested: list[str] | None) -> tuple[frozenset[str], list[str]]:
    """The kinds to list, each with the kinds the extractor may file it under ("location"
    takes in cities), and a note per requested kind that is not an entity kind."""
    if isinstance(requested, str):
        requested = [requested]  # one kind sent bare, not split into letters
    if not requested:
        return frozenset(kind.value for kind in _default_kinds()), []
    kinds: set[str] = set()
    notes: list[str] = []
    for value in (str(k).strip().casefold() for k in requested if str(k).strip()):
        try:
            kind = EntityKind(value)
        except ValueError:
            notes.append(f"'{value}' is not a named-entity kind and was ignored.")
            continue
        if kind in VALUE_KINDS:
            notes.append(f"'{value}' values are found with knowledgegraph__find_records_by_value.")
            continue
        kinds.update(member.value for member in kind_family(kind))
    return frozenset(kinds), notes


async def execute_find_co_mentioned_entities(
    state: ChatState,
    *,
    entity_ids: list[str] | None,
    kinds: list[str] | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> tuple[bool, str]:
    if not state or not state.get("graph_provider"):
        return False, "Knowledge graph tool state not initialized."
    if not await _named_entities_enabled(state):
        return False, NAMED_ENTITIES_DISABLED_MSG
    shortener = get_record_id_shortener_if_enabled(state)
    wanted = [str(i).strip() for i in entity_ids or [] if str(i).strip()]
    ids = list(dict.fromkeys(shortener.resolve(i) if shortener is not None else i for i in wanted))
    if not ids:
        return False, "Pass entity_ids: the ids search_entities returned for one entity."
    wanted_kinds, kind_notes = _wanted_kinds(kinds)
    if not wanted_kinds:
        valid = ", ".join(sorted(kind.value for kind in EntityKind if kind not in VALUE_KINDS))
        return False, " ".join([*kind_notes, f"Pass kinds from: {valid}."])
    graph: IGraphDBProvider = state["graph_provider"]
    deadline = time.monotonic() + DEADLINE_SECONDS
    readable = _Readable()
    try:
        context = await load_entity_access_context(state)
        org_id = context.org_id
        # An id from an earlier answer may have been merged since: list the node it moved to.
        redirects = await graph.resolve_named_entity_redirects(org_id, ids) or {}
        ids = list(dict.fromkeys(redirects.get(i) or i for i in ids))
        try:
            await asyncio.wait_for(_fill_readable(graph, context, ids, readable), deadline - time.monotonic())
        except TimeoutError:
            # Keep what was listed: a sample, said so, rather than nothing.
            readable.complete = False
        if not readable.records:
            # The same reply for an unknown id, one the user cannot reach, one with no
            # readable records and one whose scan ran out first: none may be told apart.
            return True, NO_RECORDS_MSG
        rows = await asyncio.wait_for(
            graph.get_named_entity_mentions_from_records(org_id, [r["_key"] for r in readable.records]),
            max(deadline - time.monotonic(), 1.0),
        )
    except TimeoutError:
        return False, TOO_SLOW_MSG
    except EntityAccessError:
        logger.warning("find_co_mentioned_entities failed", exc_info=True)
        return False, LOOKUP_FAILED_MSG
    return True, await _render(state, context, ids, readable, rows or [], wanted_kinds, kind_notes, limit, shortener)


async def _render(
    state: ChatState,
    context: EntityAccessContext,
    ids: list[str],
    readable: _Readable,
    rows: list[dict[str, Any]],
    wanted_kinds: frozenset[str],
    kind_notes: list[str],
    limit: int,
    shortener: object,
) -> str:
    by_key = {r["_key"]: r for r in readable.records}
    # Only the readable records' own mentions count, whatever the read returned.
    rows = [row for row in rows if row.get("recordId") in by_key]
    own = set(ids)
    named = spellings_on(row for row in rows if row.get("entityId") in own)
    others = [row for row in rows if row.get("entityId") not in own and str(row.get("kind")) in wanted_kinds]
    spelled = spellings_on(others)
    mentioned_in: dict[str, list[str]] = {}
    kind_of: dict[str, str] = {}
    for row in others:
        entity_id = str(row["entityId"])
        if entity_id not in spelled:
            continue  # no readable record spells it: never show the stored name
        records = mentioned_in.setdefault(entity_id, [])
        if row["recordId"] not in records:
            records.append(row["recordId"])
        kind_of[entity_id] = str(row.get("kind") or "")
    try:
        asked = int(limit or _DEFAULT_LIMIT)
    except (TypeError, ValueError):
        asked = _DEFAULT_LIMIT
    bounded = min(max(1, asked), _MAX_LIMIT)
    ranked = sorted(mentioned_in, key=lambda e: (-len(mentioned_in[e]), spelled[e].casefold(), e))[:bounded]
    subject_name = next((named[i] for i in ids if i in named), None)
    subject = f'"{subject_name}"' if subject_name else "that entity"
    # The cap is shared across ids, so with several the sample is not the newest overall.
    newest = " (the newest)" if len(ids) == 1 else ""
    scope = (
        f"all {len(readable.records)} readable records"
        if readable.complete else f"a sample of {len(readable.records)} readable records{newest}"
    )
    notes = " ".join(kind_notes)
    if not ranked:
        return " ".join(filter(None, [f"No other entities of these kinds are mentioned in {scope} that mention {subject}.", notes]))

    frontend_url = await resolve_frontend_url(state.get("config_service"))
    lines = [f"Entities mentioned with {subject}, counted over {scope} that mention it:"]
    if notes:
        lines.append(notes)
    examples: list[str] = []
    for entity_id in ranked:
        pool = mentioned_in[entity_id]
        # Prefer records already shown, so the examples stay few.
        sample = sorted(pool, key=lambda r: r not in examples)[:_EXAMPLES]
        sample = [r for r in sample if r in examples or len(examples) < _MAX_EXAMPLE_RECORDS]
        examples.extend(r for r in sample if r not in examples)
        refs = ", ".join(_short(r, shortener) for r in sample)  # type: ignore[arg-type]
        lines.append(
            f"- {spelled[entity_id]} ({kind_of[entity_id]}) | entity_id={entity_id} | "
            f"{len(pool)} record{'s' if len(pool) != 1 else ''}" + (f", e.g. {refs}" if refs else "")
        )
    lines.append("")
    lines.append("Example records:")
    lines.extend(record_line(by_key[r], context, shortener, frontend_url) for r in examples)  # type: ignore[arg-type]
    lines.append("")
    lines.append(f"Note: {COUNT_NOTE} {COVERAGE_NOTE}")
    hint = "Next: knowledgegraph__find_records_by_entity(entity_id=...) lists every record of one of them"
    if entity_search_offered_in(state):
        hint += '; knowledgegraph__search_by_entities(mentions=[...], match="all") lists the records naming both'
    lines.append(hint + ".")
    # So a follow-up find_records_by_entity or search(entity_ids=...) knows these ids.
    remember_entities(state, [{"entityId": e, "entityType": NAMED_ENTITY_TYPE, "name": spelled[e]} for e in ranked])
    remember_record_ids(state, examples, names={r: by_key[r].get("recordName") or "" for r in examples})
    note_retrieved_by(state, "entity", graph_record_refs([by_key[r] for r in examples]))
    return "\n".join(lines)


async def _fill_readable(
    graph: IGraphDBProvider, context: EntityAccessContext, entity_ids: list[str], readable: _Readable,
) -> None:
    """Readable records mentioning any of ``entity_ids``, one per document (copies of a
    file count once), up to the search-scope cap shared across the ids. Fills
    ``readable`` as it goes, so a deadline keeps what was listed."""
    seen: set[str] = set()
    if len(entity_ids) > SEARCH_SCOPE_MAX_ENTITIES:
        readable.complete = False
    for entity_id in entity_ids[:SEARCH_SCOPE_MAX_ENTITIES]:
        remaining = SEARCH_SCOPE_MAX_RECORDS - len(readable.records)
        if remaining <= 0:
            readable.complete = False
            return
        page = await list_accessible_entity_records(
            graph, context, entity_id=entity_id, entity_type=NAMED_ENTITY_TYPE,
            limit=remaining, max_scan=SEARCH_SCOPE_MAX_SCAN,
        )
        if page.next_cursor is not None or page.capped:
            readable.complete = False
        for record in page.records:
            document = record.get("virtualRecordId") or record.get("_key")
            if record.get("_key") and document not in seen:
                seen.add(document)
                readable.records.append(record)


__all__ = ["execute_find_co_mentioned_entities"]
