"""UI-ADOPT2: the tools a chat-drafted agent names (ADR-AGB-01 D1) are checked against the real tool lists of the
toolset and MCP instance each binds to, at confirmation. A tool no list has is left out and reported, never created;
a list that cannot be read reports its tools as unverified; an action or server left with none is not attached."""

from __future__ import annotations

from typing import Any

from app.agents.portable import ImportCatalog, check_drafted_tools, plan_import


def _agent(toolsets: list[dict[str, Any]] | None = None, mcp: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A portable agent as Node maps a `propose_agent` draft (`portableAgentOf`)."""
    return {
        "name": "Triage", "description": "", "startMessage": "", "systemPrompt": "Triage bugs.", "instructions": None,
        "tags": [], "sendUserContext": True, "webSearch": None, "defaultReasoningEffort": None, "engineProfile": None,
        "models": [], "roleModels": {}, "toolsets": toolsets or [], "mcpServers": mcp or [], "knowledge": [],
        "skills": [], "gates": None,
    }


def _ts(name: str, *tools: str) -> dict[str, Any]:
    return {
        "name": name, "displayName": name, "type": "app", "instanceName": None,
        "tools": [{"name": t, "fullName": t if "." in t else f"{name}.{t}", "description": ""} for t in tools],
    }


def _mcp(name: str, *tools: str, all_tools: bool = False) -> dict[str, Any]:
    return {
        "name": name, "displayName": name, "typeId": None, "allTools": all_tools,
        "tools": [{"name": t, "fullName": f"{name}.{t}", "description": ""} for t in tools],
    }


CATALOG = ImportCatalog(
    llm_entries=[],
    toolset_instances=[{"_id": "inst-jira", "orgId": "org-1", "instanceName": "Jira Cloud", "toolsetType": "jira"}],
    mcp_instances=[{"_id": "mcp-1", "name": "github", "typeId": "github"}],
    connectors=[],
)

JIRA = [
    {"name": "search_issues", "fullName": "jira.search_issues", "description": "Search issues"},
    {"name": "create_issue", "fullName": "jira.create_issue", "description": "Create an issue"},
]
GITHUB = [{"name": "list_prs", "fullName": "mcp_github_list_prs", "description": "List pull requests"}]


def _rebind(plan: Any) -> list[tuple[str, str, str]]:  # noqa: ANN401
    return [(r["kind"], r["name"], r["reason"]) for r in plan.rebind]


def test_a_named_tool_the_toolset_has_binds_under_its_real_name() -> None:
    plan = plan_import(_agent([_ts("jira", "Search_Issues", "jira.create_issue")]), CATALOG, sole_instance=True)

    check_drafted_tools(plan, toolset_tools={"jira": JIRA}, mcp_tools={})

    assert plan.body["toolsets"][0]["tools"] == JIRA
    assert plan.body["toolsets"][0]["instanceId"] == "inst-jira"
    assert plan.rebind == []


def test_a_tool_the_toolset_does_not_have_is_left_out_and_reported() -> None:
    plan = plan_import(_agent([_ts("jira", "search_issues", "close_everything")]), CATALOG, sole_instance=True)

    check_drafted_tools(plan, toolset_tools={"jira": JIRA}, mcp_tools={})

    assert [t["name"] for t in plan.body["toolsets"][0]["tools"]] == ["search_issues"]
    assert _rebind(plan) == [("tool", "jira.close_everything", "not_found")]


def test_an_action_left_with_none_of_its_tools_is_not_attached() -> None:
    plan = plan_import(_agent([_ts("jira", "close_everything")]), CATALOG, sole_instance=True)

    check_drafted_tools(plan, toolset_tools={"jira": JIRA}, mcp_tools={})

    assert plan.body["toolsets"] == []
    assert _rebind(plan) == [("tool", "jira.close_everything", "not_found"), ("toolset", "jira", "no_tools")]
    assert {"kind": "toolset", "name": "jira"} not in plan.bound


def test_a_toolset_whose_tool_list_cannot_be_read_reports_its_tools_unverified() -> None:
    plan = plan_import(_agent([_ts("jira", "search_issues")]), CATALOG, sole_instance=True)

    check_drafted_tools(plan, toolset_tools={"jira": None}, mcp_tools={})

    assert plan.body["toolsets"] == []
    assert _rebind(plan) == [("tool", "jira.search_issues", "unverified"), ("toolset", "jira", "no_tools")]


def test_an_mcp_server_keeps_the_tools_it_offers_and_reports_the_rest() -> None:
    plan = plan_import(_agent(mcp=[_mcp("github", "LIST_PRS", "force_push")]), CATALOG, sole_instance=True)

    check_drafted_tools(plan, toolset_tools={}, mcp_tools={"mcp-1": GITHUB})

    assert plan.body["mcpServers"][0]["tools"] == GITHUB
    assert _rebind(plan) == [("tool", "github.force_push", "not_found")]


def test_an_mcp_server_whose_tools_cannot_be_listed_is_not_attached_with_unverified_tools() -> None:
    plan = plan_import(_agent(mcp=[_mcp("github", "list_prs")]), CATALOG, sole_instance=True)

    check_drafted_tools(plan, toolset_tools={}, mcp_tools={"mcp-1": None})

    assert plan.body["mcpServers"] == []
    assert _rebind(plan) == [("tool", "github.list_prs", "unverified"), ("mcpServer", "github", "no_tools")]
    assert {"kind": "mcpServer", "name": "github"} not in plan.bound


def test_an_mcp_server_with_all_its_tools_needs_no_list() -> None:
    plan = plan_import(_agent(mcp=[_mcp("github", all_tools=True)]), CATALOG, sole_instance=True)

    check_drafted_tools(plan, toolset_tools={}, mcp_tools={})

    assert plan.body["mcpServers"][0]["allTools"] is True
    assert plan.rebind == []


def test_what_names_no_tool_is_not_checked() -> None:
    """A platform action carried without tools (an export's) has nothing to check."""
    plan = plan_import(_agent([_ts("calculator")]), CATALOG, sole_instance=True)

    check_drafted_tools(plan, toolset_tools={}, mcp_tools={})

    assert plan.body["toolsets"] == [{"name": "calculator", "displayName": "calculator", "type": "app", "tools": []}]
    assert plan.rebind == []
