"""Running a record query: candidates from the provider, every one decided by
the access check every entity tool uses, then evaluated and aggregated over
the admitted rows only. See ``docs/record-query.md``.

A total is exact unless the scan cap or the deadline stopped the scan, and
then it says so (``approximate``); a provider or access failure raises
``EntityAccessError``, never an empty answer.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
import uuid
from collections import Counter
from typing import TYPE_CHECKING, Any

from app.modules.retrieval.entity_permissions import (
    RECORD_GROUP_ENTITY_TYPE,
    EntityAccessError,
    admitted_node_ids,
)
from app.modules.retrieval.person_identity import people_of
from app.modules.retrieval.record_query.evaluate import (
    NOT_CLASSIFIED,
    ROLE_OF_EDGE,
    aggregate_value,
    field_value,
    groups,
    tally,
)
from app.modules.retrieval.record_query.model import (
    MAX_DESCRIBED_VALUES,
    Group,
    PersonCondition,
    RecordQuery,
    RecordQueryResult,
    walk_conditions,
)
from app.modules.retrieval.record_query.paging import page
from app.modules.retrieval.record_query.pushdown import pushdown
from app.services.graph_db.common.record_query_rows import classification_pending

if TYPE_CHECKING:
    from app.modules.retrieval.entity_permissions import EntityAccessContext
    from app.modules.retrieval.record_query.evaluate import Row
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

# Candidates read per query before the total is reported as approximate.
SCAN_CAP = 20_000
WINDOW = 500
DEADLINE_SECONDS = 20.0
_SERVER_GRACE_SECONDS = 2.0
# A person id that names the user asking.
SELF_ID = "me"
# Records one query hands to classification on first use, as one search does.
MAX_TO_CLASSIFY = 50
_FIRST_USE_FIELDS = ("_key", "virtualRecordId", "contentRev", "connectorId", "connectorName", "mimeType",
                     "indexingStatus", "extractionStatus")


def _connector_ids(context: EntityAccessContext, query: RecordQuery) -> list[str]:
    """As ``connector_scope``: a record group the user cannot reach lists only
    app-level connectors' records, where app access alone grants them."""
    groups_named = [e["id"] for e in pushdown(query).get("entities", []) if e["type"] == RECORD_GROUP_ENTITY_TYPE]
    if any(g not in context.record_group_ids for g in groups_named):
        return sorted(context.app_level_app_ids)
    return sorted(context.app_ids)


async def run_record_query(
    graph_provider: IGraphDBProvider,
    context: EntityAccessContext,
    query: RecordQuery,
    *,
    scan_cap: int = SCAN_CAP,
    window: int = WINDOW,
    deadline_seconds: float = DEADLINE_SECONDS,
    correlation_id: str | None = None,
) -> RecordQueryResult:
    qid = correlation_id or uuid.uuid4().hex[:12]
    connector_ids = _connector_ids(context, query)
    if not connector_ids:
        logger.debug("record query %s: no connector in scope", qid)
        return RecordQueryResult(total=0)
    query = await resolve_people(graph_provider, context, query, query_id=qid)
    pushed = pushdown(query)
    started = time.monotonic()
    deadline = started + deadline_seconds
    logger.debug("record query %s: scan start org=%s connectors=%d keys=%s", qid, context.org_id,
                 len(connector_ids), sorted(pushed))
    try:
        found = await graph_provider.get_record_query_candidates(
            context.org_id, connector_ids=connector_ids, pushdown=pushed, scan_cap=scan_cap,
            timeout_seconds=deadline_seconds + _SERVER_GRACE_SECONDS,
            within=[scope.candidate_filter() for scope in context.row_scopes] or None,
        )
    except Exception as exc:
        logger.debug("record query %s: scan failed", qid)
        raise EntityAccessError("Record query failed") from exc
    logger.debug("record query %s: scan done candidates=%d capped=%s ms=%d", qid, len(found.keys), found.capped,
                 (time.monotonic() - started) * 1000)
    admitted_rows: list[Row] = []
    approximate, checked = found.capped, 0
    for start in range(0, len(found.keys), window):
        if start and time.monotonic() >= deadline:
            approximate = True
            break
        keys = found.keys[start:start + window]
        admitted = await admitted_node_ids(graph_provider, context, (k.id for k in keys))
        ids = [k.id for k in keys if k.id in admitted]
        try:
            rows = await graph_provider.get_record_query_rows(
                context.org_id, record_ids=ids, pushdown=pushed,
                timeout_seconds=max(0.0, deadline - time.monotonic()) + _SERVER_GRACE_SECONDS,
            ) if ids else []
        except Exception as exc:
            logger.debug("record query %s: rows failed at window %d", qid, start // window)
            raise EntityAccessError("Record query failed") from exc
        by_id = {str(r.get("_key")): r for r in rows}
        # Rows keep the scan's order and the time it ordered them by.
        admitted_rows += [{**by_id[k.id], "sortTs": k.sort_ts} for k in keys if k.id in by_id]
        checked += len(keys)
    result = _result(query, admitted_rows, approximate=approximate)
    logger.info(
        "record query %s org=%s scanned=%d checked=%d admitted=%d total=%d approximate=%s not_classified=%d ms=%d",
        qid, context.org_id, len(found.keys), checked, len(admitted_rows), result.total, result.approximate,
        result.unknown.get(NOT_CLASSIFIED, 0), (time.monotonic() - started) * 1000,
    )
    return result


async def resolve_people(
    graph_provider: IGraphDBProvider, context: EntityAccessContext, query: RecordQuery, *, query_id: str = "",
) -> RecordQuery:
    """``query`` with each person condition naming every node of the people
    its ids name (``person_identity``), and ``me`` as the user asking. Ids
    are resolved inside the user's org only; one the org does not hold stays
    as given and matches nothing."""
    if not any(isinstance(c, PersonCondition) for c in walk_conditions(query.where)):
        return query
    resolved = query.model_copy(deep=True)
    conditions = [c for c in walk_conditions(resolved.where) if isinstance(c, PersonCondition)]

    def named(ids: list[str]) -> list[str]:
        return [context.user_key if i.strip().casefold() == SELF_ID else i for i in ids]

    asked = list(dict.fromkeys(i for c in conditions for i in named(c.person.ids)))
    try:
        people = await people_of(graph_provider, context.org_id, asked)
    except Exception as exc:
        raise EntityAccessError("Record query failed") from exc
    mates = {i: person.ids for person in people for i in person.ids}
    for cond in conditions:
        ids = named(cond.person.ids)
        cond.person.ids = list(dict.fromkeys([*ids, *(m for i in ids for m in mates.get(i, ()))]))
    logger.debug(
        "record query id=%s people resolved: conditions=%d asked=%d people=%d nodes=%d",
        query_id, len(conditions), len(asked), len(people), len(mates),
    )
    return resolved


def _result(query: RecordQuery, admitted: list[Row], *, approximate: bool) -> RecordQueryResult:
    found = tally(query, admitted, keep_not_classified=MAX_TO_CLASSIFY)
    matched = found.matched
    value, _ = aggregate_value(query, matched) if query.aggregate.op != "count" else (float(len(matched)), 0)
    shown, start, next_cursor = page(
        matched, sort=query.sort, cursor=query.cursor, limit=query.limit, value_of=field_value,
    )
    waiting = {r["_key"]: r for r in [*shown, *found.not_classified] if classification_pending(r)}
    return RecordQueryResult(
        total=len(matched), approximate=approximate, value=value, groups=groups(query, matched),
        unknown=dict(found.unknown), described=_described(query, matched),
        rows=[row_view(r) for r in shown], offset=start, next_cursor=next_cursor,
        not_classified=[_first_use_doc(r) for r in list(waiting.values())[:MAX_TO_CLASSIFY]],
    )


def _first_use_doc(row: Row) -> dict[str, Any]:
    return {k: row.get(k) for k in _FIRST_USE_FIELDS}


def _described(query: RecordQuery, matched: list[Row]) -> dict[str, list[Group]]:
    out: dict[str, list[Group]] = {}
    for name in query.describe:
        counts = Counter(str(v) for r in matched if (v := field_value(r, name)) is not None)
        out[name] = [Group(key=k, label=k, count=n) for k, n in counts.most_common(MAX_DESCRIBED_VALUES)]
    return out


def _day(epoch_ms: int | None) -> str | None:
    return None if epoch_ms is None else dt.datetime.fromtimestamp(epoch_ms / 1000, tz=dt.UTC).date().isoformat()


def row_view(row: Row) -> dict[str, Any]:
    """A row as the agent reads it: names, not ids, first; empty values left out."""
    ticket, deal = row.get("ticket") or {}, row.get("deal") or {}
    people: dict[str, list[str]] = {}
    for p in row.get("people") or []:
        role = ROLE_OF_EDGE.get(p["role"], p["role"].lower())
        people.setdefault(role, []).append(p.get("name") or p["id"])
    view = {
        "title": row.get("recordName"), "type": row.get("recordType"), "source": row.get("connectorName"),
        "status": ticket.get("sourceStatus") or ticket.get("status"), "priority": ticket.get("priority"),
        "created": _day(row.get("createdAt")), "updated": _day(row.get("updatedAt")),
        "resolved": _day(ticket.get("resolvedAt")), "amount": deal.get("amount"),
        "outcome": field_value(row, "deal_outcome"), "close_date": deal.get("closeDate"),
        "people": {k: sorted(set(v)) for k, v in people.items()} or None,
        "organizations": sorted({o.get("name") or o["id"] for o in row.get("organizations") or []}) or None,
        # No URL: a row is cited by record_id, and a link the source hides must never show.
        "record_id": row.get("_key"),
    }
    return {k: v for k, v in view.items() if v not in (None, "", [], {})}


__all__ = [
    "DEADLINE_SECONDS", "MAX_TO_CLASSIFY", "SCAN_CAP", "SELF_ID", "WINDOW", "resolve_people", "row_view",
    "run_record_query",
]
