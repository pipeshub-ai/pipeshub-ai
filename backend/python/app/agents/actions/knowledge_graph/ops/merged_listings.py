"""The listing tools merged into ``knowledgegraph__find_records`` when record
query is on: ``find_records_by_entity``, ``find_records_by_person`` and
``find_records_by_value``. They are never offered then, but agents and plans
written against them still call them by name for one release; each call runs
the equivalent find_records query and says the tool was merged, naming the
call to use instead. See ``docs/record-query.md``.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.ops.entity_filters import ENTITY_INDEX_CACHE_KEY
from app.agents.actions.knowledge_graph.ops.record_query import execute_find_records
from app.modules.retrieval.entity_permissions import (
    ORGANIZATION_ENTITY_TYPE,
    PERSON_ENTITY_TYPE,
)
from app.modules.retrieval.record_fields import ROLE_ALIASES
from app.utils.chat_helpers import get_record_id_shortener_if_enabled

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

_FIND_RECORDS = "knowledgegraph__find_records"
_OLD_CURSOR_MSG = (
    "{old} was merged into " + _FIND_RECORDS + ", whose pages use their own cursor; this cursor cannot "
    "continue there. Call " + _FIND_RECORDS + "(where={where}) for the first page and follow its cursor."
)


def _record_types(record_types: list[str] | None) -> list[dict[str, Any]]:
    wanted = [str(t).strip().upper() for t in record_types or () if str(t).strip()]
    return [{"field": "record_type", "in": wanted}] if wanted else []


async def _run(
    state: ChatState, old: str, where: list[dict[str, Any]], *, limit: int | None, cursor: str | None,
    sources: list[str] | None = None,
) -> tuple[bool, str]:
    shown = json.dumps(where, separators=(",", ":"))
    if cursor:
        return False, _OLD_CURSOR_MSG.format(old=old, where=shown)
    logger.info("merged tool %s routed to find_records conditions=%d", old, len(where))
    ok, text = await execute_find_records(state, where=where, limit=limit, sources=sources)
    note = f"{old} was merged into {_FIND_RECORDS}; this is its answer. Next time call {_FIND_RECORDS}(where={shown})."
    return ok, f"{note}\n{text}"


async def find_records_for_entity(
    state: ChatState, *, entity_id: str | None = None, entity_type: str | None = None,
    record_types: list[str] | None = None, limit: int | None = None, cursor: str | None = None,
) -> tuple[bool, str]:
    old = "knowledgegraph__find_records_by_entity"
    raw_id = (entity_id or "").strip()
    if not raw_id:
        return False, "entity_id is required."
    shortener = get_record_id_shortener_if_enabled(state)
    resolved = shortener.resolve(raw_id) if shortener is not None else raw_id
    indexed = (state.get(ENTITY_INDEX_CACHE_KEY) or {}).get(resolved) or {}
    kind = (entity_type or "").strip().lower() or indexed.get("type")
    if not kind:
        return False, "entity_type is required for an entity that search_entities did not return in this turn."
    if kind == PERSON_ENTITY_TYPE:
        cond: dict[str, Any] = {"person": {"ids": [raw_id]}}
    elif kind == ORGANIZATION_ENTITY_TYPE:
        cond = {"organization": {"ids": [raw_id]}}
    else:
        cond = {"entity": {"type": kind, "id": raw_id}}
    return await _run(state, old, [cond, *_record_types(record_types)], limit=limit, cursor=cursor)


async def find_records_for_person(
    state: ChatState, *, person: str | None = None, roles: list[str] | str | None = None,
    record_types: list[str] | None = None, limit: int | None = None, cursor: str | None = None,
) -> tuple[bool, str]:
    old = "knowledgegraph__find_records_by_person"
    asked = " ".join((person or "").split())
    if not asked:
        return False, 'person is required: a full name, an email, or "me".'
    if isinstance(roles, str):
        roles = [roles]
    wanted = list(dict.fromkeys(ROLE_ALIASES.get(r, r) for r in (str(r).strip().lower() for r in roles or ()) if r))
    if len(wanted) > 1:
        cond: dict[str, Any] = {"any_of": [{"person": {"role": r, "name": asked}} for r in wanted]}
    else:
        cond = {"person": {"name": asked, **({"role": wanted[0]} if wanted else {})}}
    return await _run(state, old, [cond, *_record_types(record_types)], limit=limit, cursor=cursor)


def _range(low: float | None, high: float | None, **extra: str | None) -> dict[str, Any] | None:
    """A range as the merged tool took it: a strict schema sends an unused one as 0 to 0."""
    if not (low or high or any(extra.values())):
        return None
    return {k: v for k, v in {"from": low or None, "to": high or None, **extra}.items() if v not in (None, "")}


async def find_records_for_value(
    state: ChatState, *, kinds: list[str] | None = None, name: str | None = None,
    date_from: int | None = None, date_to: int | None = None,
    amount_min: float | None = None, amount_max: float | None = None, currency: str | None = None,
    quantity_min: float | None = None, quantity_max: float | None = None, dimension: str | None = None,
    percent_min: float | None = None, percent_max: float | None = None,
    record_types: list[str] | None = None, source_ids: list[str] | None = None,
    limit: int | None = None, cursor: str | None = None,
) -> tuple[bool, str]:
    old = "knowledgegraph__find_records_by_value"
    mention: dict[str, Any] = {}
    if kinds:
        mention["kind"] = list(kinds)
    if name:
        mention["name"] = name
    # Epoch ms, date_to inclusive: a UTC midnight covers that whole day, as the tool said.
    parts = {
        "date": _range(date_from, date_to), "amount": _range(amount_min, amount_max, currency=currency),
        "quantity": _range(quantity_min, quantity_max, dimension=dimension),
        "percent": _range(percent_min, percent_max),
    }
    mention.update({k: v for k, v in parts.items() if v})
    if not mention:
        return False, "Pass a kind, name, date, amount, quantity, or percentage."
    sources = list(dict.fromkeys(str(s).strip() for s in source_ids or () if str(s).strip())) or None
    where = [{"mentions": mention}, *_record_types(record_types)]
    return await _run(state, old, where, limit=limit, cursor=cursor, sources=sources)


__all__ = ["find_records_for_entity", "find_records_for_person", "find_records_for_value"]
