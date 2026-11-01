"""``knowledgegraph__find_records``: exact filters, totals and groups over every
record the user may open. See ``docs/record-query.md``.

The output leads with the total and the conditions as applied, so the model
answers "how many" from a number, never by counting rows; a page of rows
follows for citing. Values are never listed up front: ``describe`` returns a
field's top values within the user's permissions when the model needs them.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from app.agents.actions.knowledge_graph.ops.entity_filters import (
    load_entity_access_context,
)
from app.agents.agent_loop.knowledge_graph_policy import knowledge_graph_policy_in
from app.modules.agents.qna.chat_state import remember_record_ids
from app.modules.retrieval.entity_permissions import EntityAccessError
from app.modules.retrieval.provenance import graph_record_refs, note_retrieved_by
from app.modules.retrieval.record_fields import FIELDS, ROLE_EDGES, FieldKind, Store
from app.modules.retrieval.record_query.engine import run_record_query
from app.modules.retrieval.record_query.model import (
    EntityCondition,
    LinkCondition,
    OrganizationCondition,
    PersonCondition,
    RecordQuery,
    walk_conditions,
)
from app.modules.retrieval.record_query.paging import NEWEST
from app.utils.chat_helpers import get_record_id_shortener_if_enabled

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState
    from app.modules.retrieval.record_query.model import RecordQueryResult

logger = logging.getLogger(__name__)

NOT_ENABLED_MSG = "find_records is not enabled for this request."
LOOKUP_FAILED_MSG = "The record query failed - try again, or narrow it. This is not an empty result."
PEOPLE_NOT_ALLOWED_MSG = (
    "People and organisation conditions are not available in this workspace; filter by the other fields."
)
_TOP_ROWS_SHOWN_AS_IDS = 50


def field_guide() -> str:
    """Field names only, for the tool description: values come from ``describe``."""
    by_kind: dict[str, list[str]] = {}
    for f in FIELDS.values():
        if f.store is not Store.EDGE:  # people, organisations and links have their own conditions
            by_kind.setdefault(f.kind.value, []).append(f.name)
    return "; ".join(f"{kind}: {', '.join(names)}" for kind, names in by_kind.items())


def _resolve_ids(query: RecordQuery, state: ChatState) -> None:
    """Ids the model copied from earlier output may be shortened; resolve them in place."""
    shortener = get_record_id_shortener_if_enabled(state)
    if shortener is None:
        return
    for cond in walk_conditions(query.where):
        if isinstance(cond, PersonCondition):
            cond.person.ids = [shortener.resolve(i) for i in cond.person.ids]
        elif isinstance(cond, OrganizationCondition):
            cond.organization.ids = [shortener.resolve(i) for i in cond.organization.ids]
        elif isinstance(cond, LinkCondition):
            cond.linked_to.ids = [shortener.resolve(i) for i in cond.linked_to.ids]
        elif isinstance(cond, EntityCondition):
            cond.entity.id = shortener.resolve(cond.entity.id)


def _errors(exc: ValidationError) -> str:
    seen: list[str] = []
    for err in exc.errors():
        msg = str(err.get("msg", "")).removeprefix("Value error, ")
        if msg not in seen:
            seen.append(msg)
    return "Invalid query: " + "; ".join(seen[:5])


async def execute_find_records(state: ChatState, **args: Any) -> tuple[bool, str]:  # noqa: ANN401
    policy = knowledge_graph_policy_in(state)
    if not policy.record_query:
        return False, NOT_ENABLED_MSG
    if not state.get("graph_provider"):
        return False, "Graph provider not available."
    raw = {k: v for k, v in args.items() if v is not None}
    try:
        query = RecordQuery.model_validate(raw)
    except ValidationError as exc:
        return False, _errors(exc)
    conditions = walk_conditions(query.where)
    by_people = any(isinstance(c, (PersonCondition, OrganizationCondition)) for c in conditions)
    if (by_people or (query.group_by and (query.group_by.organization or query.group_by.by_person
                                          or query.group_by.person_role))) and not policy.people_and_companies:
        return False, PEOPLE_NOT_ALLOWED_MSG
    for cond in conditions:
        if isinstance(cond, EntityCondition) and not policy.allows(cond.entity.type):
            return False, f"Entity type {cond.entity.type!r} is not available in this workspace."
    _resolve_ids(query, state)
    try:
        context = await load_entity_access_context(state)
        result = await run_record_query(state["graph_provider"], context, query)
    except EntityAccessError:
        logger.warning("find_records failed", exc_info=True)
        return False, LOOKUP_FAILED_MSG
    keys = [r["record_id"] for r in result.rows if r.get("record_id")]
    if keys:
        remember_record_ids(state, keys, names={r["record_id"]: r.get("title") or "" for r in result.rows})
        note_retrieved_by(state, "entity", graph_record_refs([{"_key": k} for k in keys]))
    return True, render(query, result, shortener=get_record_id_shortener_if_enabled(state))


def _applied(query: RecordQuery) -> str:
    return json.dumps(query.model_dump(mode="json", by_alias=True, exclude_none=True)["where"], separators=(",", ":"))


def _number(value: float) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def render(query: RecordQuery, result: RecordQueryResult, *, shortener: Any = None) -> str:  # noqa: ANN401
    """Totals first, then what was computed, then a page of rows to cite."""
    total = f"at least {result.total}" if result.approximate else str(result.total)
    lines = [f"Total: {total} matching records you can open" + (
        " (approximate: the scan stopped at its limit; add a narrower condition such as a date window, "
        "a container or a person)" if result.approximate else " (exact)"
    )]
    if query.where:
        lines.append(f"Conditions as applied: {_applied(query)}")
    # A stopped scan saw only part of the matches: every figure from it is partial, not just the total.
    kind = "approximate" if result.approximate else "exact"
    if query.aggregate.op != "count" and result.value is not None:
        lines.append(f"{query.aggregate.op} of {query.aggregate.field}: {_number(result.value)} ({kind})")
    if result.groups:
        by = query.group_by
        label = by.field or by.month or ("organization" if by.organization else (by.person_role or "person"))
        lines.append(f"Groups by {label} (top {len(result.groups)}, {kind} counts):")
        lines += [f"- {g.label}: {'at least ' if result.approximate else ''}{g.count}"
                  + (f", {query.aggregate.op} {_number(g.value)}" if g.value is not None else "") + f" ({kind})"
                  for g in result.groups]
    if result.unknown:
        lines.append("Not counted, because the record does not carry the field: " + ", ".join(
            f"{n} lack {name}" for name, n in result.unknown.items()))
    for name, values in result.described.items():
        lines.append(f"Values of {name} among the matches: " + (
            ", ".join(f"{g.key} ({g.count})" for g in values) or "none"))
    if result.rows:
        start = result.offset
        order = "" if query.sort == NEWEST else f", {query.sort}"
        lines.append(f"Records {start + 1}-{start + len(result.rows)} of {total}{order}:")
        for row in result.rows[:_TOP_ROWS_SHOWN_AS_IDS]:
            shown = dict(row)
            if shortener is not None and shown.get("record_id"):
                shown["record_id"] = shortener.shorten_record_ids_in_text(shown["record_id"])
            lines.append("- " + json.dumps(shown, ensure_ascii=False, separators=(",", ":")))
    if result.next_cursor:
        lines.append(f'More records: call again with the same arguments and cursor="{result.next_cursor}".')
    return "\n".join(lines)


def roles_guide() -> str:
    return ", ".join(ROLE_EDGES)


def value_fields() -> list[str]:
    return [f.name for f in FIELDS.values() if f.kind in (FieldKind.VALUE, FieldKind.TEXT)]


__all__ = ["execute_find_records", "field_guide", "render", "roles_guide", "value_fields"]
