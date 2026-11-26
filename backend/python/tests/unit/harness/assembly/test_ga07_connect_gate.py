"""GA-07 (G-12): the assembled run tells its kernel which calls wait for their item to be connected.

Over ``PipesHubRuntimeAssembler`` with the assembly test world: a run granted a Jira toolset and an MCP server,
each unconnected for the run's owner, then connected. The gate is assembled per attempt, so the attempt after
the owner connects finds nothing to wait on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.harness.credentials.connect import CatalogConnectGate, declined_text
from harness_core.interactions.connect import ConnectGate, ConnectNeed
from harness_core.kernel.core.messages import ToolCall

from . import world as w
from .test_assembler import JIRA_SEARCH, MCP_PRS, _build

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    with w.deployment():
        yield


def _call(name: str) -> ToolCall:
    return ToolCall(id="c", name=name, arguments={})


async def test_ga07_an_unconnected_toolset_and_mcp_server_make_their_calls_wait_on_connect() -> None:
    services = w.Services()
    services.config.answer("/services/toolsets/", lambda _path: None)
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr("app.agents.mcp.service.is_effective_auth_authenticated", lambda auth: False)
        runtime = await _build(services, tool_ids=[JIRA_SEARCH, MCP_PRS], mcp=[w.MCP])
    gate = runtime.connect
    assert isinstance(gate, ConnectGate)
    jira = gate.need(_call("jira__search_issues"))
    github = gate.need(_call("mcp_github_list_prs"))
    assert jira is not None and (jira.item_kind, jira.instance_id) == ("toolset", w.JIRA)
    assert github is not None and (github.item_kind, github.instance_id) == ("mcp", w.MCP)
    assert jira.item and github.item
    # A platform tool never waits.
    assert gate.need(_call("knowledgegraph__search")) is None


async def test_ga07_once_connected_the_next_attempt_has_nothing_to_wait_on() -> None:
    runtime = await _build(w.Services(), tool_ids=[JIRA_SEARCH, MCP_PRS], mcp=[w.MCP])
    assert runtime.connect is not None
    assert runtime.connect.need(_call("jira__search_issues")) is None
    assert runtime.connect.need(_call("mcp_github_list_prs")) is None


def test_ga07_a_declined_connect_tells_the_model_the_call_was_not_made_and_where_to_connect() -> None:
    gate = CatalogConnectGate({"jira__search_issues": ConnectNeed("Jira", "toolset", w.JIRA)})
    text = gate.declined(_call("jira__search_issues"))
    assert text == declined_text("Jira", "toolset")
    assert "was not made" in text and "/workspace/actions" in text
    assert "not called" in gate.declined(_call("other"))
    mcp = declined_text("GitHub", "mcp")
    assert "/workspace/mcp-servers/personal" in mcp
