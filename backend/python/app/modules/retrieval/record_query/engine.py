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
from collections import Counter
from typing import TYPE_CHECKING, Any

from app.modules.retrieval.entity_permissions import (
    RECORD_GROUP_ENTITY_TYPE,
    EntityAccessError,
    admitted_node_ids,
)
from app.modules.retrieval.record_query.evaluate import (
    ROLE_OF_EDGE,
    aggregate_value,
    field_value,
    groups,
    tally,
)
from app.modules.retrieval.record_query.model import (
    MAX_DESCRIBED_VALUES,
    Group,
    RecordQuery,
    RecordQueryResult,
)
from app.modules.retrieval.record_query.pushdown import pushdown

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
) -> RecordQueryResult:
    connector_ids = _connector_ids(context, query)
    if not connector_ids:
        return RecordQueryResult(total=0)
    pushed = pushdown(query)
    deadline = time.monotonic() + deadline_seconds
    admitted_rows: list[Row] = []
    offset, approximate = 0, False
    while True:
        if offset >= scan_cap or (offset and time.monotonic() >= deadline):
            approximate = True
            break
        asked = min(window, scan_cap - offset)
        try:
            found = await graph_provider.get_record_query_candidates(
                context.org_id, connector_ids=connector_ids, pushdown=pushed, offset=offset,
                window=asked, scan_cap=scan_cap,
                timeout_seconds=max(0.0, deadline - time.monotonic()) + _SERVER_GRACE_SECONDS,
                within=[scope.candidate_filter() for scope in context.row_scopes] or None,
            )
        except Exception as exc:
            raise EntityAccessError("Record query failed") from exc
        admitted = await admitted_node_ids(graph_provider, context, (str(r.get("_key") or "") for r in found.rows))
        admitted_rows.extend(r for r in found.rows if str(r.get("_key")) in admitted)
        offset += found.window_size
        if found.window_size < asked:
            approximate = found.capped
            break
    result = _result(query, admitted_rows, approximate=approximate)
    logger.info(
        "record query org=%s scanned=%d admitted=%d total=%d approximate=%s",
        context.org_id, offset, len(admitted_rows), result.total, result.approximate,
    )
    return result


def _result(query: RecordQuery, admitted: list[Row], *, approximate: bool) -> RecordQueryResult:
    found = tally(query, admitted)
    matched = found.matched
    value, _ = aggregate_value(query, matched) if query.aggregate.op != "count" else (float(len(matched)), 0)
    start = int(query.cursor) if query.cursor and query.cursor.isdigit() else 0
    page = matched[start:start + query.limit]
    end = start + len(page)
    return RecordQueryResult(
        total=len(matched), approximate=approximate, value=value, groups=groups(query, matched),
        unknown=dict(found.unknown), described=_described(query, matched),
        rows=[row_view(r) for r in page], next_cursor=str(end) if end < len(matched) else None,
    )


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


__all__ = ["DEADLINE_SECONDS", "SCAN_CAP", "WINDOW", "row_view", "run_record_query"]
