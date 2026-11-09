"""``find_records_by_person`` operation — records a person is linked to by a
role the connectors write (assignee, reporter, creator, author, owner,
editor, reviewer, recipient, lead, mentioned, involved), limited to what the user can read inside the turn, newest first,
paged by cursor.

Listing and access go through the entity listing engine
(``list_accessible_person_records``). A person is shown (named, counted,
offered as a choice or a continuation) only when they link to a record the
user can read: a name that matches no one, a person with no readable linked
records and a probe that ran out of time all get the same reply. That holds
for people who are not members (Person nodes) as for members: they are
candidates only through the same readable-record probe.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.listing_citations import cite_listed_records
from app.agents.actions.knowledge_graph.ops.entity_filters import (
    limited_scope_note,
    load_entity_access_context,
)
from app.agents.actions.knowledge_graph.ops.entity_records import (
    LOOKUP_FAILED_MSG,
    cite_note,
    listing_facts,
    record_line,
)
from app.agents.actions.knowledge_graph.views import _read_hint, _trunc
from app.agents.agent_loop.entity_search_tool import entity_search_offered_in
from app.config.constants.arangodb import CollectionNames, EntityRelations
from app.modules.agents.qna.chat_state import remember_record_ids
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    list_accessible_person_records,
    parse_cursor,
    readable_person_offsets,
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
    "author": EntityRelations.AUTHORED_BY.value,
    "owner": EntityRelations.OWNED_BY.value,
    "editor": EntityRelations.LAST_MODIFIED_BY.value,
    "reviewer": EntityRelations.REVIEWED_BY.value,
    "recipient": EntityRelations.ADDRESSED_TO.value,
    "lead": EntityRelations.LEAD_BY.value,
    "mentioned": EntityRelations.MENTIONED_IN.value,
    "involved": EntityRelations.INVOLVED_IN.value,
}
SELF_NAMES = frozenset({"me", "myself", "i"})
_MAX_LIMIT = 50
_DEFAULT_LIMIT = 20
_MAX_READ_HINT_IDS = 8
_USER_MATCH_LIMIT = 20
# Non-members are fetched wider than they are shown: the one the user can reach must not be cut by namesakes they cannot.
_PERSON_MATCH_LIMIT = 200
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

    is_self = asked.casefold() in SELF_NAMES
    try:
        # Before anyone is looked up, so a bad cursor reads the same whoever is named.
        parse_cursor(cursor)
        context = await load_entity_access_context(state)
        if is_self:
            group, start = await _caller(state, graph, context), None
        else:
            shown = await _shown_people(graph, context, asked, edge_types, wanted_types)
            if len(shown) > 1:
                return True, _ambiguous(asked, [g for g, _ in shown])
            if not shown:
                return True, _no_match(state, asked, context)
            group, start = shown[0]
        page = await list_accessible_person_records(
            graph, context, nodes=_nodes(group), edge_types=edge_types,
            record_types=wanted_types, limit=bounded_limit,
            # The probe already walked the newer candidates and found none readable.
            cursor=cursor or (str(start) if start else None),
        )
    except ValueError as exc:
        return False, str(exc)
    except Exception:  # access, permission-service and query failures all read the same to the model
        logger.warning("find_records_by_person failed", exc_info=True)
        return False, LOOKUP_FAILED_MSG

    if not page.records:
        # Only the caller or a person with a readable record gets here, so naming them is allowed.
        if page.next_cursor is not None:
            again = _continue_call(
                asked, group, wanted_roles, wanted_types, page.next_cursor,
                is_self=is_self, limit=bounded_limit,
            )
            return True, f"No readable records in this part of the listing yet. Continue with: {again}"
        if cursor:
            return True, NO_FURTHER_RECORDS_MSG
        return True, _no_match(state, asked, context)

    record_ids = [r["_key"] for r in page.records if r.get("_key")]
    remember_record_ids(
        state, record_ids, names={r["_key"]: r.get("recordName") or "" for r in page.records if r.get("_key")},
    )
    note_retrieved_by(state, "person", graph_record_refs(page.records))
    shortener = get_record_id_shortener_if_enabled(state)
    frontend_url = await resolve_frontend_url(state.get("config_service"))
    who = _label(group)
    # A record reached only through a Person of a member's email is not the member's own.
    outside = await _reached_only_by_person(graph, context, group, page.records)
    # The roles asked for, as the header says them: a record's own role is not known here.
    roles = wanted_roles or list(ROLE_EDGES)
    role_text = f"as {'one of ' if len(roles) > 1 else ''}{' / '.join(roles)}"
    linked = f"{'possibly ' if outside is None else ''}linked to {who} {role_text}"
    not_member = f"linked to {_person_name(group)} (not a member) {role_text}"
    outside = outside or set()
    cite_refs = await cite_listed_records(
        state, graph, context.org_id,
        {r["_key"]: listing_facts(r, not_member if r["_key"] in outside else linked) for r in page.records if r.get("_key")},
    )
    lines = [f"Records {linked}, newest first:", *cite_note(page.records, cite_refs)]
    lines.extend(
        record_line(record, context, shortener, frontend_url, cite=cite_refs.get(record["_key"]))
        + (f" (linked to {_person_name(group)} (not a member), not to {who})" if record.get("_key") in outside else "")
        for record in page.records
    )
    lines.append("")
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


async def _shown_people(
    graph: Any, context: EntityAccessContext, asked: str,  # noqa: ANN401
    edge_types: list[str], record_types: list[str] | None,
) -> list[tuple[list[dict[str, Any]], int]]:
    """The people ``asked`` names that link to a record the user can read, each
    with the candidate offset of the newest one; nobody else may be shown."""
    # Both lookups always run, so a name that matches only a non-member costs the same as one that matches no one.
    members, outsiders = await asyncio.gather(
        graph.find_users(context.org_id, asked, limit=_USER_MATCH_LIMIT),
        graph.find_people(context.org_id, asked, limit=_PERSON_MATCH_LIMIT),
    )
    people = _people([
        *({**u, "collection": CollectionNames.USERS.value} for u in members),
        *({**p, "collection": CollectionNames.PEOPLE.value} for p in outsiders),
    ])
    if not people:
        return []
    # A group of a member and a Person is probed as its two halves in the same batch, so
    # a member with no readable record of her own is told apart from one who has.
    halves = [
        [_nodes(_members(g)), _nodes([u for u in g if u not in _members(g)])]
        if _members(g) and len(_members(g)) < len(g) else [_nodes(g)]
        for g in people
    ]
    starts = iter(await readable_person_offsets(
        graph, context, [nodes for pair in halves for nodes in pair],
        edge_types=edge_types, record_types=record_types,
    ))
    shown = []
    for group, pair in zip(people, halves):
        found = [next(starts) for _ in pair]
        readable = [s for s in found if s is not None]
        if not readable:
            continue
        if len(pair) == 2 and found[0] is None:
            # Only the Person's records are readable: she is shown as the non-member she is, never as the member.
            shown_group = [u for u in group if u not in _members(group)]
        else:
            shown_group = group
        # Each half's offset counts its own candidates, which are never later than the union's.
        shown.append((shown_group, min(readable)))
    return shown


def _people(users: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Nodes (User or Person) grouped into people: nodes sharing an email are one person."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for user in users:
        if not user.get("id"):
            continue
        email = (user.get("email") or "").strip().casefold()
        groups.setdefault(email or f"{user.get('collection')}:{user['id']}", []).append(user)
    return list(groups.values())


def _nodes(group: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(u.get("collection") or CollectionNames.USERS.value, u["id"]) for u in group]


def _members(group: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [u for u in group if u.get("collection") != CollectionNames.PEOPLE.value]


def _person_name(group: list[dict[str, Any]]) -> str:
    outsiders = [u for u in group if u.get("collection") == CollectionNames.PEOPLE.value]
    return _trunc(next((u["fullName"] for u in outsiders if (u.get("fullName") or "").strip()), "") or "this person")


async def _reached_only_by_person(
    graph: Any, context: EntityAccessContext, group: list[dict[str, Any]],  # noqa: ANN401
    records: list[dict[str, Any]],
) -> set[str] | None:
    """Ids of the listed records that link to none of the group's members, or None
    when the links could not be read. Only a group of a member and a Person needs the question."""
    members = {u["id"] for u in _members(group)}
    if not members or len(members) == len(group):
        return set()
    # shortcut: one read per listed record (at most 50), only for a member who shares an email with a Person.
    keyed = [r for r in records if r.get("_key")]
    try:
        linked = await asyncio.gather(*(graph.get_record_people(r["_key"], context.org_id) for r in keyed))
    except Exception:  # unknown: naming the Person here could name one with no readable record
        logger.warning("find_records_by_person could not attribute records", exc_info=True)
        return None
    return {r["_key"] for r, people in zip(keyed, linked) if not members & {p["id"] for p in people}}


def _label(group: list[dict[str, Any]]) -> str:
    # A person who is not a member is named, never given an email: the caller may not know it.
    if not _members(group):
        return f"{_person_name(group)} (not a member)"
    group = _members(group)
    name = next((u["fullName"] for u in group if (u.get("fullName") or "").strip()), "")
    email = next((u["email"] for u in group if u.get("email")), "")
    if name and email:
        return f"{_trunc(name)} ({email})"
    return _trunc(name or email or "this person")


def _ambiguous(asked: str, groups: list[list[dict[str, Any]]]) -> str:
    lines = [f'Several people match "{_trunc(asked)}":']
    lines.extend(f"- {_label(group)}" for group in groups[:_USER_MATCH_LIMIT])
    if any(_members(group) for group in groups):
        lines.append("Call knowledgegraph__find_records_by_person again with the person's email.")
    if any(not _members(group) for group in groups):
        lines.append("People marked (not a member) have no email to give: ask the user for more of the name.")
    return "\n".join(lines)


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
    who = "me" if is_self else next((u["email"] for u in _members(group) if u.get("email")), "") or asked
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
