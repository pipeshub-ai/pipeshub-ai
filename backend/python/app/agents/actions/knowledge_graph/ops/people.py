"""``find_records_by_person`` operation — records a person is linked to by a
role the connectors write (assignee, reporter, creator, lead, mentioned,
involved), limited to what the user can read inside the turn, newest first,
paged by cursor.

Listing and access go through the entity listing engine
(``list_accessible_person_records``). A name that matches no one and a person
with no readable linked records get the same reply, and candidates are only
named when they link to a record the user can read.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.ops.entity_filters import (
    limited_scope_note,
    load_entity_access_context,
)
from app.agents.actions.knowledge_graph.ops.entity_records import (
    LOOKUP_FAILED_MSG,
    record_line,
)
from app.agents.actions.knowledge_graph.views import _read_hint, _trunc
from app.agents.agent_loop.entity_search_tool import entity_search_offered_in
from app.config.constants.arangodb import EntityRelations
from app.modules.agents.qna.chat_state import remember_record_ids
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    EntityRecordPage,
    list_accessible_person_records,
)
from app.modules.retrieval.provenance import graph_record_refs, note_retrieved_by
from app.utils.chat_helpers import (
    get_record_id_shortener_if_enabled,
    resolve_frontend_url,
)

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

ROLE_EDGES: dict[str, str] = {
    "assignee": EntityRelations.ASSIGNED_TO.value,
    "reporter": EntityRelations.REPORTED_BY.value,
    "creator": EntityRelations.CREATED_BY.value,
    "lead": EntityRelations.LEAD_BY.value,
    "mentioned": EntityRelations.MENTIONED_IN.value,
    "involved": EntityRelations.INVOLVED_IN.value,
}
SELF_NAMES = frozenset({"me", "myself", "i"})
_MAX_LIMIT = 50
_DEFAULT_LIMIT = 20
_MAX_READ_HINT_IDS = 8
_USER_MATCH_LIMIT = 20
# Each candidate costs one listing; a name this common needs an email anyway.
_MAX_PEOPLE_PROBED = 5
NO_FURTHER_RECORDS_MSG = (
    "No further readable records for this person - the previous page was the last."
)
_CAPPED_NOTE = (
    "This person has more linked records than one listing can scan; these are the newest "
    "of a sample. Narrow with roles=[...] or record_types=[...]."
)


async def execute_find_records_by_person(
    state: "ChatState",
    person: str | None,
    roles: list[str] | None = None,
    record_types: list[str] | None = None,
    limit: int = _DEFAULT_LIMIT,
    cursor: str | None = None,
) -> tuple[bool, str]:
    if not state:
        return False, "Knowledge graph tool state not initialized."
    graph = state.get("graph_provider")
    if not graph:
        return False, "Graph provider not available."
    asked = " ".join((person or "").split())
    if not asked:
        return False, "person is required: a full name, an email, or \"me\"."
    if isinstance(roles, str):
        roles = [roles]
    wanted_roles = list(dict.fromkeys(str(r).strip().lower() for r in roles or () if str(r).strip()))
    unknown = [r for r in wanted_roles if r not in ROLE_EDGES]
    if unknown:
        return False, f"Unknown role(s) {', '.join(unknown)} — pass any of: {', '.join(ROLE_EDGES)}."
    edge_types = [ROLE_EDGES[r] for r in wanted_roles or ROLE_EDGES]
    wanted_types = [str(t).strip().upper() for t in (record_types or []) if str(t).strip()] or None
    bounded_limit = min(max(1, limit or _DEFAULT_LIMIT), _MAX_LIMIT)

    async def listing(user_ids: list[str]) -> EntityRecordPage:
        return await list_accessible_person_records(
            graph, context, user_ids=user_ids, edge_types=edge_types,
            record_types=wanted_types, limit=bounded_limit, cursor=cursor,
        )

    is_self = asked.casefold() in SELF_NAMES
    try:
        context = await load_entity_access_context(state)
        if is_self:
            people = [await _caller(state, graph, context)]
        else:
            people = _people(await graph.find_users(context.org_id, asked, limit=_USER_MATCH_LIMIT))
        probed = people[:_MAX_PEOPLE_PROBED]
        pages = await asyncio.gather(*(listing([u["id"] for u in group]) for group in probed))
    except ValueError as exc:
        return False, str(exc)
    except Exception:  # access, permission-service and query failures all read the same to the model
        logger.warning("find_records_by_person failed", exc_info=True)
        return False, LOOKUP_FAILED_MSG

    found = [(group, page) for group, page in zip(probed, pages) if page.records]
    if len(found) > 1:
        return True, _ambiguous(asked, [group for group, _ in found], more=len(people) > len(probed))
    if not found:
        if len(people) == 1 and pages[0].next_cursor is not None:
            # The scan window ran out before reaching a readable record; later ones may be.
            again = _continue_call(
                asked, probed[0], wanted_roles, wanted_types, pages[0].next_cursor,
                is_self=is_self, limit=bounded_limit,
            )
            return True, f"No readable records in this part of the listing yet. Continue with: {again}"
        if cursor and len(people) == 1:
            return True, NO_FURTHER_RECORDS_MSG
        if len(people) > len(probed):
            return True, _too_many(asked)
        return True, _no_match(state, asked, context)

    group, page = found[0]
    record_ids = [r["_key"] for r in page.records if r.get("_key")]
    remember_record_ids(
        state, record_ids, names={r["_key"]: r.get("recordName") or "" for r in page.records if r.get("_key")},
    )
    note_retrieved_by(state, "person", graph_record_refs(page.records))
    shortener = get_record_id_shortener_if_enabled(state)
    frontend_url = await resolve_frontend_url(state.get("config_service"))
    who = _label(group)
    lines = [f"Records linked to {who} as {' / '.join(wanted_roles or ROLE_EDGES)}, newest first:"]
    lines.extend(record_line(record, context, shortener, frontend_url) for record in page.records)
    lines.append("")
    if len(people) > 1:
        # Only this person links to a readable record; the others stay unnamed.
        lines.append(
            f'Other people also match "{_trunc(asked)}". If {who} is not the one meant, '
            "call again with that person's email."
        )
    if page.next_cursor is not None:
        again = _continue_call(
            asked, group, wanted_roles, wanted_types, page.next_cursor, is_self=is_self, limit=bounded_limit,
        )
        lines.append(f"More records may exist: {again}")
    elif page.capped:
        lines.append(_CAPPED_NOTE)
    elif not cursor:
        lines.append(f"That is all {len(page.records)} readable, indexed record(s).")
    else:
        lines.append("That was the last page of readable, indexed records for this person.")
    lines.append(
        f"Next: {_read_hint(record_ids[:_MAX_READ_HINT_IDS], shortener)}, or "
        "knowledgegraph__navigate(node_id=...) to see where a record sits."
    )
    return True, "\n".join(lines)


async def _caller(state: "ChatState", graph: Any, context: EntityAccessContext) -> list[dict[str, Any]]:  # noqa: ANN401
    """The caller's own user nodes: theirs and any other node of their email."""
    email = (state.get("user_email") or "").strip()
    same_email = await graph.find_users(context.org_id, email, limit=_USER_MATCH_LIMIT) if email else []
    mine = [u for u in same_email if (u.get("email") or "").casefold() == email.casefold()]
    if not any(u["id"] == context.user_key for u in mine):
        mine.insert(0, {"id": context.user_key, "fullName": "you", "email": email})
    return mine


def _people(users: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Users grouped into people: nodes sharing an email are one person."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for user in users:
        if not user.get("id"):
            continue
        email = (user.get("email") or "").strip().casefold()
        groups.setdefault(email or f"id:{user['id']}", []).append(user)
    return list(groups.values())


def _label(group: list[dict[str, Any]]) -> str:
    name = next((u["fullName"] for u in group if (u.get("fullName") or "").strip()), "")
    email = next((u["email"] for u in group if u.get("email")), "")
    if name and email:
        return f"{_trunc(name)} ({email})"
    return _trunc(name or email or "this person")


def _ambiguous(asked: str, groups: list[list[dict[str, Any]]], *, more: bool) -> str:
    lines = [f'Several people match "{_trunc(asked)}":']
    lines.extend(f"- {_label(group)}" for group in groups)
    if more:
        lines.append("Others match too.")
    lines.append("Call knowledgegraph__find_records_by_person again with the person's email.")
    return "\n".join(lines)


def _too_many(asked: str) -> str:
    return (
        f'Many people match "{_trunc(asked)}" and none of the first {_MAX_PEOPLE_PROBED} checked has '
        "readable linked records. Call knowledgegraph__find_records_by_person again with the person's email."
    )


def _no_match(state: "ChatState", asked: str, context: EntityAccessContext) -> str:
    text_tool = (
        "knowledgegraph__search_by_entities" if entity_search_offered_in(state) else "knowledgegraph__search"
    )
    return " ".join(filter(None, (
        f'No readable records are linked to a person matching "{_trunc(asked)}". '
        f"To find records whose text mentions the name, use {text_tool}.",
        limited_scope_note(context),
    )))


def _continue_call(
    asked: str, group: list[dict[str, Any]], roles: list[str], record_types: list[str] | None,
    cursor: str, *, is_self: bool, limit: int,
) -> str:
    # The cursor offsets this person's candidate set, so the next call must resolve to the same nodes:
    # "me" stays "me" (it keeps the caller's own node), anyone else is pinned by email when there is one.
    who = "me" if is_self else next((u["email"] for u in group if u.get("email")), "") or asked
    args = [f'person="{who}"']
    if roles:
        args.append(f"roles={roles!r}".replace("'", '"'))
    if record_types:
        args.append(f"record_types={record_types!r}".replace("'", '"'))
    if limit != _DEFAULT_LIMIT:
        args.append(f"limit={limit}")
    args.append(f'cursor="{cursor}"')
    return f"knowledgegraph__find_records_by_person({', '.join(args)})"


__all__ = ["ROLE_EDGES", "SELF_NAMES", "execute_find_records_by_person"]
