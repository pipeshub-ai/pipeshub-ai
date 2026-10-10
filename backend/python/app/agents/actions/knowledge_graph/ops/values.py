"""Find accessible records that mention a typed named-entity value."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.ops.entity_discovery import (
    NAMED_ENTITIES_DISABLED_MSG,
    _named_entities_enabled,
)
from app.agents.actions.knowledge_graph.ops.entity_filters import (
    load_entity_access_context,
)
from app.agents.actions.knowledge_graph.ops.entity_records import LOOKUP_FAILED_MSG
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.modules.agents.qna.chat_state import remember_record_ids
from app.modules.retrieval.entity_filters import (
    TOO_BROAD_MESSAGE,
    EntityFilterHit,
    EntityFilterResolver,
    EntityFilterUnavailableError,
)
from app.modules.retrieval.entity_permissions import EntityAccessError

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

_MAX_ROWS = 50
# Hits checked per permission query; the check stops at the first readable
# record past _MAX_ROWS instead of checking every match.
_CHECK_BATCH = 1000
# Matches listed to check for permission when the first lookup is too broad.
# Only a filter matching more falls back to counting within everything the user
# can read, which loads that whole set.
_SCAN_LIMIT = 20_000
TOO_MANY_RECORDS_MSG = (
    f"More than {_MAX_ROWS} accessible records match; narrow the filter "
    "(a tighter range, a name, or fewer kinds)."
)


async def execute_find_records_by_value(
    state: "ChatState",
    *,
    kinds: list[str] | None = None,
    name: str | None = None,
    date_from: int | None = None,
    date_to: int | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    currency: str | None = None,
    quantity_min: float | None = None,
    quantity_max: float | None = None,
    dimension: str | None = None,
    percent_min: float | None = None,
    percent_max: float | None = None,
) -> tuple[bool, str]:
    if not state or not state.get("graph_provider"):
        return False, "Knowledge graph tool state not initialized."
    if not await _named_entities_enabled(state):
        return False, NAMED_ENTITIES_DISABLED_MSG
    raw: dict[str, Any] = {}
    if kinds:
        raw["kinds"] = kinds
    if name:
        raw["name"] = name
    if _given(date_from, date_to):
        raw["mentionedDate"] = {"from": date_from or None, "to": date_to or None}
    if _given(amount_min, amount_max) or currency:
        raw["amount"] = {"min": amount_min, "max": amount_max, "currency": currency or ""}
    if _given(quantity_min, quantity_max) or dimension:
        raw["quantity"] = {"min": quantity_min, "max": quantity_max, "dimension": dimension or ""}
    if _given(percent_min, percent_max):
        raw["percent"] = {"min": percent_min, "max": percent_max}
    if not raw:
        return False, "Pass a kind, name, date, amount, quantity, or percentage."
    org_id = state.get("org_id") or ""
    user_id = state.get("user_id") or ""
    graph = state["graph_provider"]
    try:
        resolver = EntityFilterResolver(graph)
        match = await resolver.match(org_id, raw)
        if not match.error and not match.constrained:
            return False, "Pass a kind, name, date, amount, quantity, or percentage."
        context = await load_entity_access_context(state)
        if match.error == TOO_BROAD_MESSAGE:
            # Too broad across the org says nothing yet about what this user may
            # read. List more matches and check them for permission below, which
            # stops once more than _MAX_ROWS are readable.
            if not context.app_ids:
                return True, json.dumps({"status": "success", "records": [], "message": "No accessible records matched"})
            match = await resolver.match(org_id, raw, limit=_SCAN_LIMIT)
            if match.error == TOO_BROAD_MESSAGE:
                # Too many matches to list: count within the user's readable records,
                # so "too many" is never decided by records they cannot read.
                readable = await graph.get_accessible_virtual_record_ids(
                    user_id, org_id, {"apps": sorted(context.app_ids)}, raise_on_error=True,
                )
                match = await resolver.match(org_id, raw, within=list(dict.fromkeys(readable.values())))
                if match.error == TOO_BROAD_MESSAGE:
                    return False, TOO_MANY_RECORDS_MSG
        if match.error:
            return False, match.error
        in_scope = [hit for hit in match.hits if hit.connector_id in context.app_ids]
        rows = await _readable_records(graph, in_scope, user_id, org_id)
    except (EntityAccessError, EntityFilterUnavailableError, PermissionVerificationUnavailableError):
        logger.warning("find_records_by_value failed", exc_info=True)
        return False, LOOKUP_FAILED_MSG
    if rows is None:
        return False, TOO_MANY_RECORDS_MSG
    if not rows:
        return True, json.dumps({"status": "success", "records": [], "message": "No accessible records matched"})
    remember_record_ids(state, [row["recordId"] for row in rows])
    return True, json.dumps({"status": "success", "records": rows})


def _given(low: float | None, high: float | None) -> bool:
    """Whether a range was asked for. A strict tool schema makes the model send
    every parameter, so an unused range arrives as 0 to 0, not as absent."""
    return bool(low) or bool(high)


async def _readable_records(
    graph: "IGraphDBProvider", hits: list[EntityFilterHit], user_id: str, org_id: str,
) -> list[dict[str, str]] | None:
    """The readable hits, or None once more than ``_MAX_ROWS`` are readable."""
    unique = list({hit.record_id: hit for hit in hits}.values())
    rows: list[dict[str, str]] = []
    for start in range(0, len(unique), _CHECK_BATCH):
        batch = unique[start:start + _CHECK_BATCH]
        readable = await graph.filter_accessible_record_ids(
            [hit.record_id for hit in batch], user_id, org_id,
        )
        for hit in batch:
            if hit.record_id not in readable:
                continue
            if len(rows) == _MAX_ROWS:
                return None
            rows.append({"virtualRecordId": hit.virtual_record_id, "recordId": hit.record_id})
    return rows
