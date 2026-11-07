"""The recall check: records search finds for a record query that the graph
did not match. See "Recall check" in ``docs/record-query.md``.

Graph extraction is imperfect: a ticket whose text names Ravi may have no
edge to him, a deal note may hold an amount no field carries. So beside each
record query, a bounded hybrid search runs over the query's own terms
(people's and entities' names, field values, mention text) within the same
permissions and scope a search has, and every hit the engine did not match is
read back from the graph. A known contradicting fact (another assignee, a
status that differs, outside the date window or container) drops it; when
the graph cannot tell (no edge, no field, not classified, no extracted
mention) it is a possible match, listed apart and never counted in a total.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.org_context import spawn_in_request
from app.modules.retrieval.entity_permissions import admitted_node_ids
from app.modules.retrieval.record_fields import FieldKind
from app.modules.retrieval.record_fields import field as field_spec
from app.modules.retrieval.record_query.evaluate import EDGE_ENTITY_TYPES, evaluate_all
from app.modules.retrieval.record_query.model import (
    EntityCondition,
    FieldCondition,
    MentionCondition,
    OrganizationCondition,
    PersonCondition,
    walk_conditions,
)
from app.modules.retrieval.record_query.pushdown import pushdown

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from app.modules.retrieval.entity_permissions import EntityAccessContext
    from app.modules.retrieval.record_query.evaluate import Row
    from app.modules.retrieval.record_query.model import RecordQuery, RecordQueryResult
    from app.modules.retrieval.record_query.pushdown import Pushdown
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

    # (query text, time range as search takes it) -> record ids, best first.
    Search = Callable[[str, dict[str, int] | None], Awaitable[list[str]]]

logger = logging.getLogger(__name__)

RECALL_TOP_K = 50
RECALL_DEADLINE_SECONDS = 4.0
MAX_POSSIBLE_SHOWN = 10
_SERVER_GRACE_SECONDS = 1.0
# Fields search has nothing to look for in: they are filters, which the
# graph decides on each hit.
_NOT_SEARCH_TERMS = frozenset({"source", "record_type", "container"})
# Pushdown keys the recall scan drops: their edges come from extraction, so a
# hit without one is unknown and must reach the evaluator.
_EDGE_KEYS = ("people", "organizations", "records")
SELF_ID = "me"


@dataclass
class RecallCheck:
    """``ran`` False with ``skipped`` (why) or ``failed``; else the possible matches, newest first."""

    ran: bool = False
    skipped: str | None = None
    failed: bool = False
    possible: list[Row] = field(default_factory=list)
    searched: int = 0
    dropped: int = 0


def _number(value: float) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".").replace(",", "")


def recall_terms(query: RecordQuery, names: Mapping[str, str]) -> list[str]:
    """What search can look for in ``query``: people's and entities' names
    (``names`` maps ids, and person names as asked, to a display name), the
    values of value fields, and mention text. Never ids, never filters."""
    terms: list[str] = []
    for cond in walk_conditions(query.where):
        if isinstance(cond, PersonCondition):
            # The caller, however asked (``me`` or their own email), is not a name to search for.
            if cond.person.name and SELF_ID not in (cond.person.ids or ()) and cond.person.name.casefold() not in (
                "me", "myself", "i",
            ):
                terms.append(names.get(cond.person.name, cond.person.name))
            terms += [names[i] for i in cond.person.ids or () if i in names and i != SELF_ID]
        elif isinstance(cond, OrganizationCondition):
            terms += [names[i] for i in cond.organization.ids if i in names]
        elif isinstance(cond, EntityCondition) and cond.entity.id in names:
            terms.append(names[cond.entity.id])
        elif (isinstance(cond, FieldCondition) and cond.in_ and cond.field not in _NOT_SEARCH_TERMS
              and field_spec(cond.field).kind in (FieldKind.VALUE, FieldKind.TEXT)):
            terms += [v.replace("_", " ").lower() for v in cond.in_]
        elif isinstance(cond, MentionCondition):
            m = cond.mentions
            terms += [m.name] if m.name else []
            if m.amount:
                terms += [_number(v) for v in (m.amount.from_, m.amount.to) if v]
                terms += [m.amount.currency] if m.amount.currency else []
            if m.percent:
                terms += [f"{_number(v * 100)}%" for v in (m.percent.from_, m.percent.to) if v]
    return list(dict.fromkeys(t.strip() for t in terms if t and t.strip()))


def search_time_range(query: RecordQuery) -> dict[str, int] | None:
    """The created and updated windows of ``query`` as search takes them (inclusive ms)."""
    pushed, out = pushdown(query), {}
    for key, after, before in (("created", "source_created_after_ms", "source_created_before_ms"),
                               ("updated", "source_updated_after_ms", "source_updated_before_ms")):
        start, end = ([*pushed.get(key, []), None, None])[:2]
        if start is not None:
            out[after] = int(start)
        if end is not None:
            out[before] = int(end)
    return out or None


def start_search(query: RecordQuery, names: Mapping[str, str], search: Search, *,
                 deadline_seconds: float = RECALL_DEADLINE_SECONDS) -> asyncio.Task[list[str]] | None:
    """The bounded search, started so it runs beside the engine; None when
    the query has nothing search can look for."""
    terms = recall_terms(query, names)
    if not terms:
        return None
    return spawn_in_request(asyncio.wait_for(search(" ".join(terms), search_time_range(query)), deadline_seconds))


def _recall_pushdown(resolved: RecordQuery, hit_ids: list[str]) -> Pushdown:
    """The query's structural conditions, narrowed to ``hit_ids``: what the
    graph knows for certain about a hit (source, type, container, window,
    link, taxonomy). Edges extraction writes are left to the evaluator."""
    pushed: dict[str, Any] = {k: v for k, v in pushdown(resolved).items() if k not in _EDGE_KEYS}
    entities = [e for e in pushed.pop("entities", []) if e["type"] not in EDGE_ENTITY_TYPES]
    if entities:
        pushed["entities"] = entities
    pushed["records"] = [hit_ids]
    return pushed  # type: ignore[return-value]


async def reconcile(
    graph: IGraphDBProvider, context: EntityAccessContext, result: RecordQueryResult, hit_ids: list[str], *,
    correlation_id: str = "", deadline_seconds: float = RECALL_DEADLINE_SECONDS,
) -> RecallCheck:
    """``hit_ids`` (search's record ids) the engine did not match, each judged
    on what the graph holds: dropped when a fact contradicts the query,
    possible when the graph cannot tell. Only ids the access check admits are read."""
    resolved = result.resolved_query
    started = time.monotonic()
    outside = [i for i in dict.fromkeys(hit_ids) if i not in result.matched_ids]
    check = RecallCheck(ran=True, searched=len(set(hit_ids)))
    if not outside or resolved is None or not result.connector_ids:
        return check
    pushed = _recall_pushdown(resolved, outside)
    timeout = deadline_seconds + _SERVER_GRACE_SECONDS
    found = await graph.get_record_query_candidates(
        context.org_id, connector_ids=result.connector_ids, pushdown=pushed, scan_cap=len(outside),
        timeout_seconds=timeout, within=[scope.candidate_filter() for scope in context.row_scopes] or None,
    )
    kept = [k.id for k in found.keys]
    admitted = await admitted_node_ids(graph, context, kept)
    ids = [i for i in kept if i in admitted]
    rows = await graph.get_record_query_rows(
        context.org_id, record_ids=ids, pushdown=pushed, timeout_seconds=timeout,
    ) if ids else []
    sort_ts = {k.id: k.sort_ts for k in found.keys}
    kinds: Counter[str] = Counter()
    for row in rows:
        if row.get("virtualRecordId") and str(row["virtualRecordId"]) in result.matched_virtual_ids:
            continue  # another copy of a record already counted
        verdict = evaluate_all(resolved.where, row, recall=True)
        if verdict.value is False:
            continue
        check.possible.append({**row, "sortTs": sort_ts.get(str(row.get("_key")), 0)})
        kinds.update(verdict.unknown_fields or {"unchecked"})
    check.possible.sort(key=lambda r: (-int(r.get("sortTs") or 0), str(r.get("_key"))))
    check.dropped = len(outside) - len(check.possible)
    by_connector = Counter(str(r.get("connectorName") or "?") for r in check.possible)
    logger.info(
        "record query %s recall: searched=%d outside=%d in_scope=%d admitted=%d possible=%d by_connector=%s "
        "by_condition=%s ms=%d", correlation_id, check.searched, len(outside), len(kept), len(ids),
        len(check.possible), dict(by_connector), dict(kinds), (time.monotonic() - started) * 1000,
    )
    return check


async def finish_recall(
    graph: IGraphDBProvider, context: EntityAccessContext, result: RecordQueryResult,
    task: asyncio.Task[list[str]] | None, *, correlation_id: str = "",
) -> RecallCheck:
    """The recall check once the engine is done: never an error for the
    whole call; a failed or late search says the check did not run."""
    if task is None:
        return RecallCheck(skipped="no_terms")
    try:
        hits = await task
        return await asyncio.wait_for(
            reconcile(graph, context, result, hits, correlation_id=correlation_id),
            RECALL_DEADLINE_SECONDS,
        )
    except Exception as exc:  # a timeout too: the answer stands, the check just did not run
        logger.info("record query %s recall: did not run (%s)", correlation_id, type(exc).__name__)
        return RecallCheck(failed=True)


__all__ = [
    "MAX_POSSIBLE_SHOWN", "RECALL_DEADLINE_SECONDS", "RECALL_TOP_K", "RecallCheck", "finish_recall", "recall_terms",
    "reconcile", "search_time_range", "start_search",
]
