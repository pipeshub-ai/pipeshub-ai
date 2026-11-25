"""The records a search named that are still being indexed (KN-09 for search, WP-100).

Search finds only indexed content, so a search scoped to a record that is queued or being indexed comes back empty.
When it names its records (a selection of one record, ``recordsExact``, or of a file or folder, ``records``), each
one the person may read and that is still indexing is described, so the answer says it is indexing rather than
"No results found". Each id passes the access check fetch applies first: a record the person may not read, or one
that does not exist, is left out, and the answer is the one a search over nothing gives (ADR-OWD-02).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Final

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import bound_org_id
from app.harness.knowledge.caller import caller_user_id_of
from app.services.graph_db.common.record_visibility import is_searchable_record
from app.utils.not_indexed import (
    INDEX_NOT_READY,
    index_not_ready_line,
    is_index_pending,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

# The selection keys that name records rather than groups of them (``SELECTION_FILTER_KEYS`` less record groups).
TARGET_KEYS: Final = ("recordsExact", "records")
# A selection of many records is a scope, not a target the person waits on; past this none are looked up.
MAX_TARGETS: Final = 20
_READABLE: Final = frozenset({"own", "published_full"})


def targeted_records(filters: Mapping[str, Any] | None) -> list[str]:
    """The record ids a search's filters select one by one, in order, without repeats; none past ``MAX_TARGETS``."""
    ids: list[str] = []
    for key in TARGET_KEYS:
        value = (filters or {}).get(key) or ()
        ids.extend(str(rid) for rid in value if isinstance(rid, str) and rid)
    unique = list(dict.fromkeys(ids))
    return unique if len(unique) <= MAX_TARGETS else []


async def pending_records(state: Mapping[str, Any], record_ids: list[str]) -> list[dict[str, str]]:
    """``{"recordId", "name", "indexingStatus"}`` of each of ``record_ids`` the person may read whose content is still
    being indexed; a failed check names none (the search's own answer stands)."""
    graph = state.get("graph_provider")
    org_id, user_id = bound_org_id(), state.get("user_id")
    if not record_ids or graph is None or not org_id or not user_id:
        return []
    from app.harness.knowledge.read_gate import RecordReadGate

    gate = RecordReadGate(graph, org_id, user_id, caller_user_id=caller_user_id_of(state.get("user_info")))
    try:
        grants = await gate.decide_grants(record_ids)
        pending: list[dict[str, str]] = []
        for record_id in record_ids:
            grant = grants.get(record_id)
            if grant is None or grant.decision not in _READABLE:
                continue
            document = await graph.get_document(document_key=record_id, collection=CollectionNames.RECORDS.value)
            if not isinstance(document, dict) or is_searchable_record(document):
                continue
            status = document.get("indexingStatus")
            if is_index_pending(status):
                pending.append(
                    {"recordId": record_id, "name": str(document.get("recordName") or ""), "indexingStatus": status}
                )
        return pending
    except Exception:
        logger.warning("knowledgegraph__search: indexing status of %d named record(s) unread", len(record_ids))
        return []


def pending_lines(pending: list[dict[str, str]]) -> str:
    """The plain line of each pending record (``pending_records``), one per line."""
    return "\n".join(index_not_ready_line(record["name"] or None) for record in pending)


def with_pending(empty: str, pending: list[dict[str, str]], *, every: bool) -> str:
    """An empty search's answer with the pending records named: a typed ``index_not_ready`` answer when ``every``
    named record is pending, else the empty answer with their lines added. An error answer is left as it is."""
    if not pending:
        return empty
    body = json.loads(empty)
    if body.get("status") != "success":
        return empty
    lines = pending_lines(pending)
    if every:
        return json.dumps({
            "status": INDEX_NOT_READY,
            "records": pending,
            "message": f"{lines}\n\n{body['message']}",
            "results": [],
            "result_count": 0,
        })
    return json.dumps({**body, "message": f"{body['message']}\n\n{lines}"})


__all__ = ["MAX_TARGETS", "TARGET_KEYS", "pending_lines", "pending_records", "targeted_records", "with_pending"]
