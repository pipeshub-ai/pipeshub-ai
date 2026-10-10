"""A saved agent's knowledge is the ceiling for every turn's source filters.

The client may narrow an agent's sources (the chat source picker, a project
scope) but never add to them: a service-account agent runs retrieval as its
creator, so a foreign id in ``filters`` would read the creator's data on the
caller's behalf. The universal agent (``agentIdPlaceholder``) has no curated
knowledge, so its filters stay the caller's own selection.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.agents.knowledge_scope import (
    MAX_CALLER_COLLECTIONS,
    MAX_COLLECTION_CANDIDATES,
    AgentScope,
    UnreadableSourceLimitError,
    admit_caller_project_collections,
    read_source_limit,
    resolve_agent_filters,
)

ORG = "org-1"

AGENT_KNOWLEDGE = [
    {"connectorId": "jira-1", "type": "JIRA"},
    {"connectorId": "drive-1", "type": "DRIVE"},
    {"connectorId": "kb-1", "type": "KB"},
    {"connectorId": "kb-2", "type": "KB"},
]


def _resolve(requested, *, knowledge=AGENT_KNOWLEDGE, universal=False) -> AgentScope:
    return resolve_agent_filters(knowledge, requested, is_universal_agent=universal)


class TestNoRequestedFilters:
    @pytest.mark.parametrize("requested", [None, {}])
    def test_absent_filters_mean_all_agent_sources(self, requested) -> None:
        scope = _resolve(requested)
        assert scope.filters == {"apps": ["jira-1", "drive-1"], "kb": ["kb-1", "kb-2"]}
        assert scope.dropped_app_ids == ()
        assert scope.dropped_kb_ids == ()

    def test_none_valued_keys_mean_all_agent_sources_of_that_type(self) -> None:
        scope = _resolve({"apps": None, "kb": None})
        assert scope.filters["apps"] == ["jira-1", "drive-1"]
        assert scope.filters["kb"] == ["kb-1", "kb-2"]

    def test_missing_key_falls_back_only_for_that_type(self) -> None:
        scope = _resolve({"apps": ["drive-1"]})
        assert scope.filters == {"apps": ["drive-1"], "kb": ["kb-1", "kb-2"]}


class TestNarrowing:
    def test_subset_of_agent_sources_is_kept(self) -> None:
        scope = _resolve({"apps": ["drive-1"], "kb": ["kb-2"]})
        assert scope.filters == {"apps": ["drive-1"], "kb": ["kb-2"]}

    def test_explicit_empty_lists_mean_none_of_that_type(self) -> None:
        scope = _resolve({"apps": [], "kb": []})
        assert scope.filters == {"apps": [], "kb": []}
        assert scope.dropped_app_ids == () and scope.dropped_kb_ids == ()

    def test_duplicates_and_blanks_are_removed_in_order(self) -> None:
        scope = _resolve({"apps": ["drive-1", "", "jira-1", "drive-1", None], "kb": ["kb-2", "kb-2"]})
        assert scope.filters["apps"] == ["drive-1", "jira-1"]
        assert scope.filters["kb"] == ["kb-2"]

    def test_no_kb_sentinel_is_not_reported_as_dropped(self) -> None:
        scope = _resolve({"apps": ["jira-1"], "kb": ["NO_KB_SELECTED"]})
        assert scope.filters["kb"] == []
        assert scope.dropped_kb_ids == ()


class TestForeignIdsAreDropped:
    def test_foreign_app_is_dropped_not_substituted(self) -> None:
        """The exploit: naming a connector the agent was never given."""
        scope = _resolve({"apps": ["creator-private-gmail"], "kb": []})
        assert scope.filters["apps"] == []
        assert scope.dropped_app_ids == ("creator-private-gmail",)

    def test_mixed_own_and_foreign_keeps_own_only(self) -> None:
        scope = _resolve({"apps": ["jira-1", "slack-9"], "kb": ["kb-1", "kb-9"]})
        assert scope.filters == {"apps": ["jira-1"], "kb": ["kb-1"]}
        assert scope.dropped_app_ids == ("slack-9",)
        assert scope.dropped_kb_ids == ("kb-9",)

    def test_an_app_id_in_the_kb_bucket_is_not_a_collection(self) -> None:
        scope = _resolve({"apps": [], "kb": ["jira-1"]})
        assert scope.filters == {"apps": [], "kb": []}
        assert scope.dropped_kb_ids == ("jira-1",)

    def test_a_collection_sent_under_apps_is_one_of_the_agents_collections(self) -> None:
        """The picker sends a collection like any other app."""
        scope = _resolve({"apps": ["jira-1", "kb-1"], "kb": []})
        assert scope.filters == {"apps": ["jira-1"], "kb": ["kb-1"]}
        assert scope.dropped_app_ids == () and scope.dropped_kb_ids == ()

    def test_a_foreign_collection_under_apps_is_still_dropped(self) -> None:
        scope = _resolve({"apps": ["kb-9"], "kb": []})
        assert scope.filters == {"apps": [], "kb": []}
        assert scope.dropped_app_ids == ("kb-9",)

    def test_agent_without_knowledge_gets_nothing(self) -> None:
        scope = _resolve({"apps": ["jira-1"], "kb": ["kb-1"]}, knowledge=[])
        assert scope.filters == {"apps": [], "kb": []}
        assert scope.dropped_app_ids == ("jira-1",)
        assert scope.dropped_kb_ids == ("kb-1",)

    def test_malformed_knowledge_entries_are_ignored(self) -> None:
        knowledge = [None, "x", {"type": "JIRA"}, {"connectorId": "jira-1", "type": "jira"}]
        scope = _resolve({"apps": ["jira-1"]}, knowledge=knowledge)
        assert scope.filters["apps"] == ["jira-1"]

    def test_whitespace_around_ids_is_ignored(self) -> None:
        knowledge = [{"connectorId": " jira-1 ", "type": "JIRA"}, {"connectorId": "kb-1", "type": " kb "}]
        scope = _resolve({"apps": ["jira-1 "], "kb": ["  kb-1"]}, knowledge=knowledge)
        assert scope.filters == {"apps": ["jira-1"], "kb": ["kb-1"]}
        assert scope.dropped_app_ids == () and scope.dropped_kb_ids == ()

    def test_non_list_requested_value_is_treated_as_empty(self) -> None:
        scope = _resolve({"apps": "jira-1", "kb": {"x": 1}})
        assert scope.filters == {"apps": [], "kb": []}


class TestOtherKeysAndUniversalAgent:
    def test_unrelated_keys_pass_through(self) -> None:
        scope = _resolve({"apps": ["jira-1"], "kb": [], "strictScope": True, "departments": ["Legal"]})
        assert scope.filters["strictScope"] is True
        assert scope.filters["departments"] == ["Legal"]

    def test_requested_dict_is_not_mutated(self) -> None:
        requested = {"apps": ["jira-1", "slack-9"], "kb": ["kb-1"]}
        _resolve(requested)
        assert requested == {"apps": ["jira-1", "slack-9"], "kb": ["kb-1"]}

    def test_universal_agent_keeps_the_callers_selection(self) -> None:
        scope = _resolve({"apps": ["anything"], "kb": ["kb-x"]}, knowledge=[], universal=True)
        assert scope.filters == {"apps": ["anything"], "kb": ["kb-x"]}
        assert scope.dropped_app_ids == () and scope.dropped_kb_ids == ()

    def test_universal_agent_fills_absent_keys_from_its_knowledge(self) -> None:
        scope = _resolve({"apps": ["anything"]}, universal=True)
        assert scope.filters["kb"] == ["kb-1", "kb-2"]


# ---------------------------------------------------------------------------
# admit_caller_project_collections
# ---------------------------------------------------------------------------


def _graph(*, apps: dict, roles: dict, user_key: str | None = "caller-key") -> MagicMock:
    graph = MagicMock()

    async def _nodes(collection: str, field: str, values: list, return_fields=None) -> list:
        return [{"id": v, **apps[v]} for v in values if v in apps]

    graph.get_nodes_by_field_in = AsyncMock(side_effect=_nodes)
    graph.get_user_by_user_id = AsyncMock(
        return_value={"_key": user_key} if user_key else None
    )

    async def _role(kb_id: str, user: str) -> str | None:
        return roles.get((kb_id, user))

    graph.get_user_kb_permission = AsyncMock(side_effect=_role)
    return graph


HIDDEN = {"type": "KB", "isHidden": True, "orgId": ORG}


async def _admit(graph: MagicMock, kb_ids: list, *, caller: str = "caller-user") -> list:
    return await admit_caller_project_collections(
        graph, kb_ids=kb_ids, caller_user_id=caller, org_id=ORG, logger=logging.getLogger("t"),
    )


class TestAdmitCallerProjectCollections:
    async def test_callers_hidden_project_collection_is_admitted(self) -> None:
        graph = _graph(apps={"proj-kb": HIDDEN}, roles={("proj-kb", "caller-key"): "READER"})
        assert await _admit(graph, ["proj-kb"]) == ["proj-kb"]
        graph.get_user_by_user_id.assert_awaited_once_with("caller-user")

    async def test_visible_kb_is_not_admitted_even_with_access(self) -> None:
        graph = _graph(apps={"kb-v": {**HIDDEN, "isHidden": False}}, roles={("kb-v", "caller-key"): "OWNER"})
        assert await _admit(graph, ["kb-v"]) == []
        graph.get_user_kb_permission.assert_not_called()

    async def test_caller_without_a_role_is_refused(self) -> None:
        graph = _graph(apps={"proj-kb": HIDDEN}, roles={})
        assert await _admit(graph, ["proj-kb"]) == []

    async def test_other_org_collection_is_refused(self) -> None:
        graph = _graph(apps={"proj-kb": {**HIDDEN, "orgId": "org-2"}}, roles={("proj-kb", "caller-key"): "READER"})
        assert await _admit(graph, ["proj-kb"]) == []

    async def test_non_kb_app_is_refused(self) -> None:
        graph = _graph(apps={"gmail-1": {**HIDDEN, "type": "GMAIL"}}, roles={("gmail-1", "caller-key"): "READER"})
        assert await _admit(graph, ["gmail-1"]) == []

    async def test_unknown_id_is_refused(self) -> None:
        graph = _graph(apps={}, roles={("ghost", "caller-key"): "READER"})
        assert await _admit(graph, ["ghost"]) == []

    async def test_unknown_caller_is_refused(self) -> None:
        graph = _graph(apps={"proj-kb": HIDDEN}, roles={}, user_key=None)
        assert await _admit(graph, ["proj-kb"]) == []
        graph.get_nodes_by_field_in.assert_not_called()

    async def test_missing_caller_id_is_refused_without_lookups(self) -> None:
        graph = _graph(apps={"proj-kb": HIDDEN}, roles={})
        assert await _admit(graph, ["proj-kb"], caller="") == []
        graph.get_user_by_user_id.assert_not_called()

    async def test_lookup_error_fails_closed_and_logs(self, caplog) -> None:
        graph = _graph(apps={}, roles={})
        graph.get_nodes_by_field_in = AsyncMock(side_effect=RuntimeError("graph down"))
        with caplog.at_level(logging.WARNING):
            assert await _admit(graph, ["proj-kb"]) == []
        assert any("proj-kb" in r.getMessage() for r in caplog.records)

    async def test_permission_error_skips_only_that_collection(self, caplog) -> None:
        graph = _graph(apps={"a": HIDDEN, "b": HIDDEN}, roles={("b", "caller-key"): "READER"})

        async def _role(kb_id: str, user: str) -> str | None:
            if kb_id == "a":
                raise RuntimeError("boom")
            return "READER"

        graph.get_user_kb_permission = AsyncMock(side_effect=_role)
        with caplog.at_level(logging.WARNING):
            assert await _admit(graph, ["a", "b"]) == ["b"]
        assert any("a" in r.getMessage() and "not admitted" in r.getMessage() for r in caplog.records)

    async def test_hidden_collection_after_many_visible_ones_is_still_found(self) -> None:
        """Node's project scope sends the project's collections with the hidden
        one last; a bound applied before the hidden check would never see it."""
        visible = [f"kb-{i}" for i in range(MAX_CALLER_COLLECTIONS + 3)]
        apps = {k: {**HIDDEN, "isHidden": False} for k in visible} | {"proj-kb": HIDDEN}
        graph = _graph(apps=apps, roles={("proj-kb", "caller-key"): "READER"})
        assert await _admit(graph, [*visible, "proj-kb"]) == ["proj-kb"]
        graph.get_nodes_by_field_in.assert_awaited_once()
        assert graph.get_user_kb_permission.await_count == 1

    async def test_lookups_and_permission_checks_are_bounded(self) -> None:
        many = [f"kb-{i}" for i in range(MAX_COLLECTION_CANDIDATES + 10)]
        graph = _graph(apps=dict.fromkeys(many, HIDDEN), roles={(k, "caller-key"): "READER" for k in many})
        admitted = await _admit(graph, many)
        assert admitted == many[:MAX_CALLER_COLLECTIONS]
        (_, _, looked_up), _ = graph.get_nodes_by_field_in.await_args
        assert looked_up == many[:MAX_COLLECTION_CANDIDATES]
        assert graph.get_user_kb_permission.await_count == MAX_CALLER_COLLECTIONS

    async def test_nothing_to_admit_makes_no_calls(self) -> None:
        graph = _graph(apps={}, roles={})
        assert await _admit(graph, []) == []
        graph.get_user_by_user_id.assert_not_called()



class TestSelectionBelowAppLevel:
    """A selection of record groups, folders or records is the whole scope of
    the turn, for a saved agent and for the universal one."""

    @pytest.mark.parametrize("universal", [False, True])
    def test_a_bucket_that_was_not_given_means_none_not_all(self, universal) -> None:
        scope = _resolve({"records": ["r1"]}, universal=universal)
        assert scope.filters == {"records": ["r1"], "apps": [], "kb": []}

    def test_whole_apps_beside_a_selection_are_still_bounded_by_the_agent(self) -> None:
        scope = _resolve({"apps": ["jira-1", "slack-9"], "recordGroups": ["g1"]})
        assert scope.filters == {"apps": ["jira-1"], "kb": [], "recordGroups": ["g1"]}

    def test_a_universal_agent_keeps_its_collection_sent_under_apps(self) -> None:
        scope = _resolve({"apps": ["jira-1", "kb-1"], "kb": []}, universal=True)
        assert scope.filters == {"apps": ["jira-1"], "kb": ["kb-1"]}

    @pytest.mark.parametrize("universal", [False, True])
    def test_server_set_keys_from_a_client_are_dropped(self, universal) -> None:
        scope = _resolve(
            {"apps": ["jira-1"], "kb": [], "allowedApps": ["x"], "allowedRecords": ["y"], "selectionApps": ["z"]},
            universal=universal,
        )
        assert scope.filters == {"apps": ["jira-1"], "kb": []}


LIMITED_KNOWLEDGE = [
    {"connectorId": "jira-1", "type": "JIRA", "filtersParsed": {"recordGroups": ["proj-a"], "records": []}},
    {"connectorId": "drive-1", "type": "DRIVE", "filters": '{"records": ["folder-x"]}'},
    {"connectorId": "kb-1", "type": "KB", "filtersParsed": {}},
    {"connectorId": "kb-2", "type": "KB", "filtersParsed": {"records": ["kb2-folder"]}},
]


class TestSourcesLimitedToSomeNodes:
    """A source limited to record groups, folders or records is never searched whole."""

    def test_an_enabled_limited_source_is_searched_as_its_nodes(self) -> None:
        scope = _resolve(None, knowledge=LIMITED_KNOWLEDGE)
        assert scope.filters == {
            "apps": [], "kb": ["kb-1"], "recordGroups": ["proj-a"], "records": ["folder-x", "kb2-folder"],
        }

    def test_a_source_the_turn_leaves_out_contributes_nothing(self) -> None:
        scope = _resolve({"apps": ["jira-1"], "kb": []}, knowledge=LIMITED_KNOWLEDGE)
        assert scope.filters == {"apps": [], "kb": [], "recordGroups": ["proj-a"]}

    def test_the_agents_bound_lists_whole_sources_and_the_nodes_of_limited_ones(self) -> None:
        assert _resolve(None, knowledge=LIMITED_KNOWLEDGE).sources == {
            "apps": ["kb-1"], "recordGroups": ["proj-a"], "records": ["folder-x", "kb2-folder"],
        }

    def test_the_callers_own_selection_stands_beside_the_sources_it_enables(self) -> None:
        scope = _resolve({"apps": ["drive-1"], "kb": [], "records": ["picked"]}, knowledge=LIMITED_KNOWLEDGE)
        assert scope.filters == {"apps": [], "kb": [], "records": ["picked", "folder-x"]}

    def test_a_selection_alone_enables_no_source(self) -> None:
        scope = _resolve({"records": ["picked"]}, knowledge=LIMITED_KNOWLEDGE)
        assert scope.filters == {"apps": [], "kb": [], "records": ["picked"]}

    def test_an_agent_of_whole_sources_is_unchanged(self) -> None:
        scope = _resolve(None)
        assert scope.filters == {"apps": ["jira-1", "drive-1"], "kb": ["kb-1", "kb-2"]}
        assert scope.sources == {
            "apps": ["jira-1", "drive-1", "kb-1", "kb-2"], "recordGroups": [], "records": [],
        }

    def test_a_list_naming_a_source_itself_is_not_a_limit(self) -> None:
        """Agents saved before each collection became its own source listed it there."""
        knowledge = [{"connectorId": "kb-1", "type": "KB", "filtersParsed": {"recordGroups": ["kb-1"]}}]
        scope = _resolve(None, knowledge=knowledge)
        assert scope.filters == {"apps": [], "kb": ["kb-1"]}

    @pytest.mark.parametrize("stored", [
        "{bad", "[1, 2]", 7, {"records": "folder-x"}, {"records": [123, None]}, {"recordGroups": ["g", ""]},
    ])
    def test_a_stored_limit_that_cannot_be_read_searches_nothing_of_that_source(self, stored) -> None:
        """Never the whole source: the owner meant to limit it."""
        knowledge = [
            {"connectorId": "drive-1", "type": "DRIVE", "filters": stored},
            {"connectorId": "kb-1", "type": "KB"},
        ]
        scope = _resolve(None, knowledge=knowledge)
        assert scope.filters == {"apps": [], "kb": ["kb-1"]}
        assert scope.sources == {"apps": ["kb-1"], "recordGroups": [], "records": []}
        assert scope.unreadable_source_ids == ("drive-1",)

    def test_the_stored_text_wins_over_a_parsed_copy_that_hides_it(self) -> None:
        """The graph reads text it cannot parse as an empty object."""
        knowledge = [{"connectorId": "drive-1", "type": "DRIVE", "filters": "{bad", "filtersParsed": {}}]
        assert _resolve(None, knowledge=knowledge).filters == {"apps": [], "kb": []}

    @pytest.mark.parametrize("stored", [None, "", "  ", "{}", {}, [], {"sync": {"x": 1}}, {"records": []}])
    def test_nothing_stored_is_a_whole_source(self, stored) -> None:
        knowledge = [{"connectorId": "drive-1", "type": "DRIVE", "filters": stored}]
        scope = _resolve(None, knowledge=knowledge)
        assert scope.filters == {"apps": ["drive-1"], "kb": []} and scope.unreadable_source_ids == ()


class TestReadSourceLimit:
    def test_lists_come_back_trimmed_and_without_repeats(self) -> None:
        assert read_source_limit('{"recordGroups": ["g1", " g1 "], "records": ["r1"]}') == {
            "recordGroups": ["g1"], "records": ["r1"],
        }

    @pytest.mark.parametrize("stored", ["{bad", "[1]", [1, 2], 7, {"records": "x"}, {"records": [1]}, {"records": [""]}])
    def test_anything_else_is_refused(self, stored) -> None:
        with pytest.raises(UnreadableSourceLimitError):
            read_source_limit(stored)

    def test_the_universal_agent_has_no_bound_of_its_own(self) -> None:
        scope = _resolve(None, knowledge=LIMITED_KNOWLEDGE, universal=True)
        assert scope.sources == {}
        assert scope.filters == {"apps": ["jira-1", "drive-1"], "kb": ["kb-1", "kb-2"]}
