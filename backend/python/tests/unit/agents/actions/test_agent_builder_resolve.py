"""Name resolution for `draft_agent`: knowledge, action tools and web search."""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.actions.agent_builder.resolve import (
    KnowledgeEntry,
    external_toolsets,
    resolve_knowledge,
    resolve_tools,
    resolve_web_search,
)

SUPPORTED = {"duckduckgo", "serper", "tavily", "exa"}
CATALOG = [
    KnowledgeEntry("kb-hr", "HR Policies", "collection"),
    KnowledgeEntry("kb-hr-eu", "HR Policies EU", "collection"),
    KnowledgeEntry("kb-wiki", "Engineering Wiki", "collection"),
    KnowledgeEntry("app-slack", "Slack Workspace", "connector", "SLACK"),
]


def _toolset(instance: str, kind: str, display: str, tools: list[str], instance_name: str | None = None) -> dict[str, Any]:
    return {
        "instanceId": instance, "name": kind, "toolsetType": kind, "displayName": display,
        "instanceName": instance_name or display, "iconPath": f"/{kind}.svg", "category": "app",
        "tools": [{"name": t, "fullName": f"{kind}.{t}", "description": ""} for t in tools],
    }


JIRA = _toolset("i-jira", "jira", "Jira", ["create_issue", "search_issues", "add_comment"])
SLACK = _toolset("i-slack", "slack", "Slack", ["send_message", "add_comment"])
GITHUB_REG = {"name": "github", "display_name": "GitHub"}


def _ids(resolved: list) -> list[str]:
    return [k.id for k in resolved]


def test_a_name_resolves_case_insensitively_to_its_collection() -> None:
    resolved, missing = resolve_knowledge(["hr policies"], CATALOG)
    assert _ids(resolved) == ["kb-hr"]
    assert missing == []


def test_an_id_passes_through_and_duplicates_collapse() -> None:
    resolved, _ = resolve_knowledge(["kb-wiki", "Engineering Wiki", "  ENGINEERING wiki "], CATALOG)
    assert _ids(resolved) == ["kb-wiki"]


def test_a_unique_prefix_or_substring_matches_and_carries_the_connector_type() -> None:
    resolved, _ = resolve_knowledge(["slack", "wiki"], CATALOG)
    assert [(k.id, k.kind, k.connectorType) for k in resolved] == [
        ("app-slack", "connector", "SLACK"), ("kb-wiki", "collection", None),
    ]


def test_an_exact_name_wins_over_longer_names_that_share_its_prefix() -> None:
    resolved, missing = resolve_knowledge(["HR Policies"], CATALOG)
    assert _ids(resolved) == ["kb-hr"] and missing == []


def test_several_matches_are_ambiguous_with_candidates_capped_at_five() -> None:
    many = [KnowledgeEntry(f"k{i}", f"Team {i} notes", "collection") for i in range(8)]
    resolved, missing = resolve_knowledge(["team"], many)
    assert resolved == []
    assert missing[0].reason == "ambiguous"
    assert len(missing[0].candidates) == 5


def test_nothing_matching_is_not_found_and_the_query_is_trimmed() -> None:
    _, missing = resolve_knowledge(["x" * 500], CATALOG)
    assert missing[0].reason == "not_found"
    assert len(missing[0].query) == 200


@pytest.mark.parametrize("query", ["jira.create_issue", "jira__create_issue", "Jira create issue", "Create issue in Jira", "create_issue"])
def test_each_spelling_of_a_tool_resolves_to_that_tool(query: str) -> None:
    resolved, missing = resolve_tools([query], [JIRA, SLACK])
    assert missing == []
    assert [(t.instanceId, [x.fullName for x in t.tools]) for t in resolved] == [("i-jira", ["jira.create_issue"])]


def test_a_toolset_name_selects_all_its_tools_by_type_display_or_instance_name() -> None:
    cloud = _toolset("i-j", "jira", "Jira", ["create_issue", "search_issues"], instance_name="Acme Jira")
    for query in ("jira", "JIRA", "Acme Jira"):
        resolved, _ = resolve_tools([query], [cloud, SLACK])
        assert [[x.name for x in t.tools] for t in resolved] == [["create_issue", "search_issues"]]
    resolved, _ = resolve_tools(["Jira tools"], [cloud, SLACK])
    assert len(resolved[0].tools) == 2


def test_a_unique_bare_tool_name_resolves_and_a_shared_one_is_ambiguous() -> None:
    resolved, missing = resolve_tools(["send_message", "add_comment"], [JIRA, SLACK])
    assert [t.instanceId for t in resolved] == ["i-slack"]
    assert missing[0].query == "add_comment"
    assert missing[0].reason == "ambiguous"
    assert set(missing[0].candidates) == {"Jira: add_comment", "Slack: add_comment"}


def test_picks_for_one_instance_are_merged() -> None:
    resolved, _ = resolve_tools(["jira.create_issue", "jira.search_issues", "jira.create_issue"], [JIRA])
    assert len(resolved) == 1
    assert [t.name for t in resolved[0].tools] == ["create_issue", "search_issues"]
    assert (resolved[0].displayName, resolved[0].iconPath, resolved[0].category) == ("Jira", "/jira.svg", "app")


def test_an_app_that_exists_but_is_not_connected_says_so() -> None:
    _, missing = resolve_tools(["GitHub", "Zendesk", "github create issue"], [JIRA], [GITHUB_REG])
    assert [(m.query, m.reason) for m in missing] == [
        ("GitHub", "not_connected"), ("Zendesk", "not_found"), ("github create issue", "not_connected"),
    ]


def test_internal_toolsets_are_never_offered() -> None:
    builder = _toolset("i-ab", "agentbuilder", "Agent Builder", ["draft_agent"])
    calc = _toolset("i-calc", "calculator", "Calculator", ["add"])
    kept = external_toolsets([JIRA, builder, calc], ["AgentBuilder", "calculator"])
    assert [t["instanceId"] for t in kept] == ["i-jira"]
    resolved, missing = resolve_tools(["draft_agent", "calculator"], kept)
    assert resolved == [] and len(missing) == 2


def test_web_search_uses_the_default_provider_and_labels_it() -> None:
    web, missing = resolve_web_search({"provider": "duckduckgo", "configuration": {}}, SUPPORTED)
    assert missing is None
    assert web is not None and (web.provider, web.providerLabel) == ("duckduckgo", "DuckDuckGo")
    web, _ = resolve_web_search({"provider": "Tavily"}, SUPPORTED)
    assert web is not None and web.providerLabel == "Tavily"


@pytest.mark.parametrize("config", [{"provider": "bing"}, {}, None])
def test_web_search_without_a_supported_provider_is_unavailable(config: dict | None) -> None:
    web, missing = resolve_web_search(config, SUPPORTED)
    assert web is None
    assert missing is not None and (missing.kind, missing.reason) == ("webSearch", "unavailable")
