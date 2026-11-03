"""An agent can use only the tools picked for each of its toolsets.

The agent builder saves the tools picked for each toolset, and the chat
composer narrows them per message, but the loader registered every tool of
an attached toolset: an agent shared as "search only" could still create and
delete. MCP servers already loaded only their attached tools.

Drives the real `PipesHubToolLoader`; the toolset catalogue and the client
factories (the OAuth/network boundary) are replaced with fakes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.tools.decorators import tool
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.tool_loader import PipesHubToolLoader
from app.agents.tools.factories.registry import ClientFactoryRegistry

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from app.agent_loop_lib.tools.registry import ToolRegistry


class _Factory:
    async def create_client(self, config_service: Any, logger: Any, config: dict, state: dict) -> dict:  # noqa: ANN401
        return {"config": config}


class _Jira:
    def __init__(self, client: dict) -> None:
        self.client = client

    @tool(path="/tools/jira/search_issues", short_description="Search issues", description="Search Jira issues")
    async def search_issues(self) -> tuple[bool, str]:
        return True, "[]"

    @tool(path="/tools/jira/create_issue", short_description="Create issue", description="Create a Jira issue")
    async def create_issue(self) -> tuple[bool, str]:
        return True, "{}"


class _GoogleDrive:
    def __init__(self, client: dict) -> None:
        self.client = client

    @tool(path="/tools/drive/list_files", short_description="List files", description="List Drive files")
    async def list_files(self) -> tuple[bool, str]:
        return True, "[]"

    @tool(path="/tools/drive/delete_file", short_description="Delete file", description="Delete a Drive file")
    async def delete_file(self) -> tuple[bool, str]:
        return True, "{}"


class _OneDrive:
    def __init__(self, client: dict) -> None:
        self.client = client

    @tool(path="/tools/onedrive/list_items", short_description="List items", description="List OneDrive items")
    async def list_items(self) -> tuple[bool, str]:
        return True, "[]"


class _Calculator:
    @tool(path="/tools/calculator/add", short_description="Add", description="Add numbers")
    async def add(self) -> tuple[bool, str]:
        return True, "0"

    @tool(path="/tools/calculator/multiply", short_description="Multiply", description="Multiply numbers")
    async def multiply(self) -> tuple[bool, str]:
        return True, "0"


@pytest.fixture(autouse=True)
def _factories(monkeypatch: pytest.MonkeyPatch) -> None:
    google = _Factory()
    # One factory per connector, Google's under both its short and long names, as in
    # production: `configured_name_matches` tells "onedrive" from "drive" by factory.
    monkeypatch.setattr(ClientFactoryRegistry, "_factories", {
        "jira": _Factory(), "drive": google, "googledrive": google, "onedrive": _Factory(),
    })
    monkeypatch.setattr(ClientFactoryRegistry, "_initialized", True)


def _context(toolsets: list[dict]) -> AgentContext:
    creds = {"auth": {"token": "t"}}
    return AgentContext(
        org_id="org-1", user_id="user-1", user_email="u@example.com", logger=MagicMock(),
        retrieval_service=MagicMock(config_service=MagicMock()),
        agent_toolsets=toolsets,
        toolset_configs={"jira-1": creds, "jira-2": creds, "gd-1": creds, "od-1": creds},
    )


def _catalogue() -> "AbstractContextManager[MagicMock]":
    fake = MagicMock()
    fake.get_all_toolsets.return_value = {
        "jira": {"class": _Jira, "isInternal": False, "description": "Jira"},
        "drive": {"class": _GoogleDrive, "isInternal": False, "description": "Google Drive"},
        "onedrive": {"class": _OneDrive, "isInternal": False, "description": "OneDrive"},
        "calculator": {"class": _Calculator, "isInternal": True, "description": "Calculator"},
    }
    return patch("app.agents.registry.toolset_registry.get_toolset_registry", return_value=fake)


def _picked(*names: str) -> list[dict]:
    return [{"name": n, "fullName": f"jira.{n}", "description": ""} for n in names]


async def _load(toolsets: list[dict]) -> tuple["ToolRegistry", AgentContext]:
    context = _context(toolsets)
    with _catalogue():
        registry = await PipesHubToolLoader().load(context)
    return registry, context


def _stale_warnings(context: AgentContext) -> list[tuple]:
    """(toolset, missing picks) of each "picked tools no longer exist" warning."""
    return [
        call.args[1:] for call in context.logger.warning.call_args_list
        if "no longer exist" in call.args[0]
    ]


class TestOnlyPickedToolsLoad:
    async def test_unpicked_tool_is_not_registered(self) -> None:
        registry, _ = await _load([{"name": "jira", "instanceId": "jira-1", "tools": _picked("search_issues")}])

        assert registry.has("jira__search_issues")
        assert not registry.has("jira__create_issue")
        assert registry.tools_in_toolset("jira") == ["jira__search_issues"]

    @pytest.mark.parametrize("entry", [
        {"name": "jira", "instanceId": "jira-1"},
        {"name": "jira", "instanceId": "jira-1", "tools": []},
    ])
    async def test_entry_without_tools_keeps_every_tool(self, entry: dict) -> None:
        registry, _ = await _load([entry])

        assert sorted(registry.tools_in_toolset("jira")) == ["jira__create_issue", "jira__search_issues"]

    async def test_entry_without_tools_keeps_every_tool_beside_one_with_picks(self) -> None:
        registry, _ = await _load([
            {"name": "jira", "instanceId": "jira-1", "tools": []},
            {"name": "jira", "instanceId": "jira-2", "tools": _picked("search_issues")},
        ])

        assert sorted(registry.tools_in_toolset("jira")) == ["jira__create_issue", "jira__search_issues"]

    async def test_picks_apply_through_the_long_configured_name(self) -> None:
        registry, _ = await _load([
            {"name": "googledrive", "instanceId": "gd-1", "tools": [{"name": "list_files"}]},
        ])

        assert registry.tools_in_toolset("drive") == ["drive__list_files"]

    async def test_onedrive_picks_do_not_narrow_google_drive(self) -> None:
        registry, _ = await _load([
            {"name": "onedrive", "instanceId": "od-1", "tools": [{"name": "list_items"}]},
            {"name": "googledrive", "instanceId": "gd-1"},
        ])

        assert sorted(registry.tools_in_toolset("drive")) == ["drive__delete_file", "drive__list_files"]
        assert registry.tools_in_toolset("onedrive") == ["onedrive__list_items"]

    async def test_picks_from_every_instance_of_a_toolset_are_combined(self) -> None:
        registry, _ = await _load([
            {"name": "jira", "instanceId": "jira-1", "tools": _picked("search_issues")},
            {"name": "jira", "instanceId": "jira-2", "tools": _picked("create_issue")},
        ])

        assert sorted(registry.tools_in_toolset("jira")) == ["jira__create_issue", "jira__search_issues"]

    async def test_picks_that_no_longer_exist_drop_only_that_toolset(self) -> None:
        registry, context = await _load([
            {"name": "jira", "instanceId": "jira-1", "tools": _picked("renamed_tool")},
            {"name": "onedrive", "instanceId": "od-1"},
        ])

        assert "jira" not in {g.name for g in registry.toolsets()}
        assert not any(name.startswith("jira__") for name in registry.names())
        assert registry.tools_in_toolset("onedrive") == ["onedrive__list_items"]
        assert ("jira", ["renamed_tool"]) in _stale_warnings(context)

    async def test_a_pick_renamed_since_the_agent_was_saved_is_reported(self) -> None:
        registry, context = await _load([
            {"name": "jira", "instanceId": "jira-1", "tools": _picked("search_issues", "get_issue_comments")},
        ])

        assert registry.tools_in_toolset("jira") == ["jira__search_issues"]
        assert _stale_warnings(context) == [("jira", ["get_issue_comments"])]

    async def test_internal_toolset_ignores_picks(self) -> None:
        registry, _ = await _load([{"name": "calculator", "tools": [{"name": "add"}]}])

        assert sorted(registry.tools_in_toolset("calculator")) == ["calculator__add", "calculator__multiply"]
