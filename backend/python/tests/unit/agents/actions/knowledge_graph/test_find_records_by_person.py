"""``find_records_by_person``: resolving the person, the reply for each
outcome, and how the tool is routed to."""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR, ToolMeta
from app.agents.actions.knowledge_graph.entity_search_surface import variant_meta
from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph
from app.agents.actions.knowledge_graph.ops import entity_filters, people
from app.agents.actions.knowledge_graph.ops.entity_filters import LIMITED_SCOPE_NOTE
from app.agents.actions.knowledge_graph.ops.entity_records import LOOKUP_FAILED_MSG
from app.agents.actions.knowledge_graph.ops.people import (
    NO_FURTHER_RECORDS_MSG,
    ROLE_EDGES,
    execute_find_records_by_person,
)
from app.agents.agent_loop.domain_agents import DOMAIN_AGENT_DEFINITIONS
from app.agents.agent_loop.entity_search_tool import ENTITY_SEARCH_OFFERED_STATE_KEY
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    EntityAccessError,
    EntityRecordPage,
)
from app.modules.retrieval.provenance import RETRIEVED_BY_KEY
from app.modules.retrieval.selection_scope import SelectionScope
from tests.unit.modules.agents.test_source_catalog import (
    CONF_ID,
    _catalog_from_knowledge,
    _make_app_entry,
)

CONTEXT = EntityAccessContext(
    org_id="org-1",
    user_key="ukey",
    app_level_app_ids=frozenset(),
    record_level_app_ids=frozenset({"jira-1"}),
    record_group_ids=frozenset(),
    app_names={"jira-1": "Jira"},
)
DARSHAN = {"id": "u-d", "fullName": "Darshan Godase", "email": "darshan@x.io"}
DARSHAN_STUB = {"id": "u-d2", "fullName": "", "email": "Darshan@x.io"}
NAMESAKE = {"id": "u-n", "fullName": "Darshan Godase", "email": "dgodase@y.io"}


def _row(key: str) -> dict:
    return {"_key": key, "recordName": f"Ticket {key}", "recordType": "TICKET", "connectorId": "jira-1",
            "virtualRecordId": f"v-{key}"}


def _state(users: list[dict] | Exception = (), **overrides: object) -> dict:
    graph = MagicMock()
    graph.find_users = AsyncMock(side_effect=users) if isinstance(users, Exception) else AsyncMock(
        return_value=list(users),
    )
    return {"org_id": "org-1", "user_id": "user-1", "user_email": "me@x.io", "graph_provider": graph,
            "include_retrieval_context": True, **overrides}


@pytest.fixture
def listing(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Pages by the first user id asked; a person with none lists nothing."""
    pages: dict[str, EntityRecordPage] = {}
    mock = AsyncMock(side_effect=lambda graph, context, *, user_ids, **_: pages.get(
        user_ids[0], EntityRecordPage(records=[], next_cursor=None),
    ))
    mock.pages = pages
    monkeypatch.setattr(entity_filters, "get_entity_access_context", AsyncMock(return_value=CONTEXT))
    monkeypatch.setattr(people, "list_accessible_person_records", mock)
    monkeypatch.setattr(people, "resolve_frontend_url", AsyncMock(return_value=None))
    return mock


class TestResolvingThePerson:
    async def test_one_match_lists_its_records(self, listing) -> None:
        listing.pages["u-d"] = EntityRecordPage(records=[_row("r1"), _row("r2")], next_cursor=None)
        ok, text = await execute_find_records_by_person(_state([DARSHAN]), "darshan@x.io")
        assert ok is True
        assert text.startswith("Records linked to Darshan Godase (darshan@x.io)")
        assert "Ticket r1" in text and "Ticket r2" in text
        assert "That is all 2 readable, indexed record(s)." in text

    async def test_nodes_sharing_an_email_are_one_person(self, listing) -> None:
        listing.pages["u-d"] = EntityRecordPage(records=[_row("r1")], next_cursor=None)
        ok, text = await execute_find_records_by_person(_state([DARSHAN, DARSHAN_STUB]), "Darshan Godase")
        assert ok is True and "Ticket r1" in text
        listing.assert_awaited_once()
        assert listing.await_args.kwargs["user_ids"] == ["u-d", "u-d2"]

    async def test_namesakes_with_readable_records_are_listed_without_records(self, listing) -> None:
        listing.pages["u-d"] = EntityRecordPage(records=[_row("r1")], next_cursor=None)
        listing.pages["u-n"] = EntityRecordPage(records=[_row("r2")], next_cursor=None)
        state = _state([DARSHAN, NAMESAKE])
        ok, text = await execute_find_records_by_person(state, "Darshan Godase")
        assert ok is True
        assert "darshan@x.io" in text and "dgodase@y.io" in text
        assert "Ticket" not in text
        assert "with the person's email" in text
        assert not state.get(RETRIEVED_BY_KEY)

    async def test_a_candidate_without_readable_records_is_never_named(self, listing) -> None:
        listing.pages["u-d"] = EntityRecordPage(records=[_row("r1")], next_cursor=None)
        ok, text = await execute_find_records_by_person(_state([DARSHAN, NAMESAKE]), "Darshan")
        assert ok is True
        assert "dgodase@y.io" not in text
        assert "Ticket r1" in text
        assert 'Other people also match "Darshan"' in text

    async def test_a_single_match_has_no_namesake_note(self, listing) -> None:
        listing.pages["u-d"] = EntityRecordPage(records=[_row("r1")], next_cursor=None)
        _, text = await execute_find_records_by_person(_state([DARSHAN]), "Darshan")
        assert "Other people" not in text

    async def test_no_such_user_and_no_readable_records_read_the_same(self, listing) -> None:
        _, nobody = await execute_find_records_by_person(_state([]), "Darshan")
        _, unreadable = await execute_find_records_by_person(_state([DARSHAN]), "Darshan")
        assert nobody == unreadable
        assert "knowledgegraph__search." in nobody
        assert "search_by_entities" not in nobody

    async def test_the_text_search_pointer_names_search_by_entities_only_when_offered(self, listing) -> None:
        _, text = await execute_find_records_by_person(
            _state([], **{ENTITY_SEARCH_OFFERED_STATE_KEY: True}), "Darshan",
        )
        assert "knowledgegraph__search_by_entities" in text

    async def test_a_limited_turn_says_only_its_sources_were_searched(self, listing, monkeypatch) -> None:
        limited = EntityAccessContext(**{**CONTEXT.__dict__, "row_scopes": (SelectionScope(),)})
        monkeypatch.setattr(entity_filters, "get_entity_access_context", AsyncMock(return_value=limited))
        _, text = await execute_find_records_by_person(_state([DARSHAN]), "Darshan")
        assert LIMITED_SCOPE_NOTE in text

    async def test_too_many_namesakes_without_readable_records_asks_for_an_email(self, listing) -> None:
        many = [{"id": f"u-{i}", "fullName": "Alex Kim", "email": f"alex{i}@x.io"} for i in range(7)]
        _, text = await execute_find_records_by_person(_state(many), "Alex Kim")
        assert "Many people match" in text and "email" in text
        assert listing.await_count == 5

    @pytest.mark.parametrize("me", ["me", "Myself", " I "])
    async def test_me_is_the_caller_and_their_other_nodes(self, listing, me) -> None:
        mine = {"id": "u-me2", "fullName": "Me", "email": "ME@x.io"}
        other = {"id": "u-x", "fullName": "Mel", "email": "mel@x.io"}
        listing.pages["ukey"] = EntityRecordPage(records=[_row("r1")], next_cursor=None)
        state = _state([mine, other])
        ok, text = await execute_find_records_by_person(state, me)
        assert ok is True and "Ticket r1" in text
        state["graph_provider"].find_users.assert_awaited_once_with("org-1", "me@x.io", limit=20)
        assert listing.await_args.kwargs["user_ids"] == ["ukey", "u-me2"]


class TestRoles:
    async def test_a_single_role_string_is_one_role(self, listing) -> None:
        ok, _ = await execute_find_records_by_person(_state([DARSHAN]), "Darshan", roles="reporter")
        assert ok is True
        assert listing.await_args.kwargs["edge_types"] == ["REPORTED_BY"]

    async def test_roles_map_to_their_relations(self, listing) -> None:
        await execute_find_records_by_person(_state([DARSHAN]), "Darshan", roles=["Reporter", "assignee"])
        assert listing.await_args.kwargs["edge_types"] == ["REPORTED_BY", "ASSIGNED_TO"]

    async def test_no_roles_means_every_role(self, listing) -> None:
        await execute_find_records_by_person(_state([DARSHAN]), "Darshan")
        assert listing.await_args.kwargs["edge_types"] == list(ROLE_EDGES.values())

    async def test_an_unknown_role_is_refused_with_the_valid_ones(self, listing) -> None:
        ok, text = await execute_find_records_by_person(_state([DARSHAN]), "Darshan", roles=["owner"])
        assert ok is False
        assert "owner" in text and all(role in text for role in ROLE_EDGES)
        listing.assert_not_awaited()


class TestPaging:
    async def test_more_records_print_the_continue_call_and_no_total(self, listing) -> None:
        listing.pages["u-d"] = EntityRecordPage(records=[_row("r1")], next_cursor="40")
        _, text = await execute_find_records_by_person(
            _state([DARSHAN]), "Darshan Godase", roles=["reporter"], record_types=["ticket"],
        )
        assert (
            'knowledgegraph__find_records_by_person(person="darshan@x.io", roles=["reporter"], '
            'record_types=["TICKET"], cursor="40")'
        ) in text
        assert "That is all" not in text

    async def test_a_capped_scan_gives_no_total(self, listing) -> None:
        listing.pages["u-d"] = EntityRecordPage(records=[_row("r1")], next_cursor=None, capped=True)
        _, text = await execute_find_records_by_person(_state([DARSHAN]), "Darshan")
        assert "That is all" not in text
        assert "more linked records than one listing can scan" in text

    async def test_a_later_page_gives_no_total(self, listing) -> None:
        listing.pages["u-d"] = EntityRecordPage(records=[_row("r9")], next_cursor=None)
        _, text = await execute_find_records_by_person(_state([DARSHAN]), "darshan@x.io", cursor="20")
        assert "That is all" not in text
        assert listing.await_args.kwargs["cursor"] == "20"

    async def test_the_last_page_says_it_is_the_last(self, listing) -> None:
        listing.pages["u-d"] = EntityRecordPage(records=[_row("r9")], next_cursor=None)
        _, text = await execute_find_records_by_person(_state([DARSHAN]), "darshan@x.io", cursor="20")
        assert "That was the last page" in text

    async def test_the_continue_call_keeps_a_non_default_limit(self, listing) -> None:
        listing.pages["u-d"] = EntityRecordPage(records=[_row("r1")], next_cursor="50")
        _, text = await execute_find_records_by_person(_state([DARSHAN]), "darshan@x.io", limit=50)
        assert 'person="darshan@x.io", limit=50, cursor="50")' in text

    async def test_paging_past_the_end_says_so(self, listing) -> None:
        ok, text = await execute_find_records_by_person(_state([DARSHAN]), "darshan@x.io", cursor="20")
        assert (ok, text) == (True, NO_FURTHER_RECORDS_MSG)

    async def test_an_empty_window_with_more_to_scan_continues_instead_of_no_match(self, listing) -> None:
        listing.pages["u-d"] = EntityRecordPage(records=[], next_cursor="200")
        ok, text = await execute_find_records_by_person(_state([DARSHAN]), "Darshan Godase")
        assert ok is True
        assert "No readable records are linked" not in text
        assert 'find_records_by_person(person="darshan@x.io", cursor="200")' in text

    async def test_me_pages_as_me_even_without_an_email(self, listing) -> None:
        listing.pages["ukey"] = EntityRecordPage(records=[_row("r1")], next_cursor="20")
        _, text = await execute_find_records_by_person(_state([], user_email=""), "me")
        assert 'find_records_by_person(person="me", cursor="20")' in text

    async def test_a_person_without_an_email_pages_by_the_name_asked(self, listing) -> None:
        listing.pages["u-s"] = EntityRecordPage(records=[_row("r1")], next_cursor="20")
        stub = {"id": "u-s", "fullName": "Sam Roe", "email": ""}
        _, text = await execute_find_records_by_person(_state([stub]), "sam roe")
        assert 'find_records_by_person(person="sam roe", cursor="20")' in text

    async def test_limit_is_bounded(self, listing) -> None:
        await execute_find_records_by_person(_state([DARSHAN]), "Darshan", limit=500)
        assert listing.await_args.kwargs["limit"] == 50


class TestShownRecords:
    async def test_only_shown_records_are_remembered_and_attributed(self, listing) -> None:
        listing.pages["u-d"] = EntityRecordPage(records=[_row("r1"), _row("r2")], next_cursor="2")
        state = _state([DARSHAN])
        await execute_find_records_by_person(state, "Darshan")
        assert set(state[RETRIEVED_BY_KEY]) == {"v-r1", "v-r2"}
        assert all("person" in entry.layers for entry in state[RETRIEVED_BY_KEY].values())


class TestFailures:
    async def test_access_failure_is_a_failed_call_not_none(self, listing) -> None:
        listing.side_effect = EntityAccessError("down")
        ok, text = await execute_find_records_by_person(_state([DARSHAN]), "Darshan")
        assert (ok, text) == (False, LOOKUP_FAILED_MSG)

    async def test_user_lookup_failure_is_a_failed_call(self, listing) -> None:
        ok, text = await execute_find_records_by_person(_state(RuntimeError("neo4j down")), "Darshan")
        assert (ok, text) == (False, LOOKUP_FAILED_MSG)

    async def test_blank_person_is_refused(self, listing) -> None:
        ok, _ = await execute_find_records_by_person(_state([DARSHAN]), "   ")
        assert ok is False


def _meta() -> ToolMeta:
    return getattr(KnowledgeGraph.find_records_by_person, TOOL_META_ATTR)


class TestRouting:
    def test_tool_roles_are_the_ops_roles_and_description_is_short(self) -> None:
        meta = _meta()
        roles = next(p for p in meta.parameters if p.name == "roles")
        assert roles.items["enum"] == list(ROLE_EDGES)
        assert len(meta.description.split()) <= 120
        assert "assigned" in meta.description and "\"me\"" in meta.description

    def test_tool_method_passes_every_argument(self) -> None:
        assert list(inspect.signature(KnowledgeGraph.find_records_by_person).parameters) == [
            "self", "person", "roles", "record_types", "limit", "cursor",
        ]

    def test_search_by_entities_points_role_questions_to_the_tool(self) -> None:
        meta = getattr(KnowledgeGraph.search_by_entities, TOOL_META_ATTR)
        assert "knowledgegraph__find_records_by_person" in meta.description

    def test_offered_search_entities_names_both_people_tools(self) -> None:
        declared = getattr(KnowledgeGraph.search_entities, TOOL_META_ATTR)
        offered = variant_meta("knowledgegraph__search_entities", declared).description
        assert "knowledgegraph__find_records_by_person" in offered
        assert "knowledgegraph__search_by_entities" in offered
        assert "search_by_entities" not in declared.description

    def test_catalog_routes_role_questions_only_when_granted(self) -> None:
        catalog = _catalog_from_knowledge([_make_app_entry("Jira", "jira", CONF_ID)])
        granted = catalog.render(tool_names=["knowledgegraph__search", "knowledgegraph__find_records_by_person"])
        assert "`knowledgegraph__find_records_by_person(person=...)`" in granted
        assert "find_records_by_person" not in catalog.render(tool_names=["knowledgegraph__search"])

    def test_the_exploration_child_gets_the_tool(self) -> None:
        explorer = next(d for d in DOMAIN_AGENT_DEFINITIONS if d.name == "internal_exploration_agent")
        assert "knowledgegraph__find_records_by_person" in explorer.tool_names
