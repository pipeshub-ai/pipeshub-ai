"""R26-LAZY stage (c), ADR-OWD-07 D-LAZY-2: admission lists no MCP server for a worker that lists them at first use.

Node asks for the definition with ``mcpFirstUse`` only while a live worker advertises ``mcp_first_use``. Then a saved
agent's servers grant their attached tools unpinned, the default chat's servers are granted whole (``atFirstUse``),
and under an edition's approval gate the stored approvals are pinned: none of them lists a server. Without it (an
older worker during a rolling deploy) admission lists and pins as before.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.run_definition import ASSISTANT_AGENT_KEY
from tests.support.agent_versions import versions_beside
from tests.support.bound_org import bind_test_org
from tests.support.mcp_tool_admission import (  # noqa: F401 - the fixtures, used by name
    mcp_approvals_open,
    mcp_approvals_required,
)
from tests.unit.agents import test_assist1_assistant_definition as assist
from tests.unit.agents import test_run_definition as saved

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _bound_org() -> Iterator[str]:
    yield from bind_test_org("org-1")


@pytest.fixture(autouse=True)
def _agent_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    versions_beside(monkeypatch, "app.api.routes.agent")


async def _saved(*, first_use: bool, hub: bool = True) -> tuple[dict[str, Any], AsyncMock]:
    from app.api.routes.agent import get_agent_run_definition

    services = saved._services({"role": "READER"}, saved._agent())
    services["graph_provider"].get_document = AsyncMock(return_value={"orgId": "org-1"})
    listing = AsyncMock(return_value=({"mcp-9": {"list_prs": "sha256:" + "d" * 64}}, frozenset({"mcp-9"})))
    with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
         patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock,
               return_value={"_key": "uk1", "email": "u1@x"}), \
         patch("app.api.routes.agent._enrich_agent_models", new_callable=AsyncMock), \
         patch("app.services.featureflag.platform_settings.is_actions_enabled", new_callable=AsyncMock,
               return_value=True), \
         patch("app.agents.mcp.service.is_mcp_enabled", new_callable=AsyncMock, return_value=True), \
         patch("app.harness.mcp_hub.settings.hub_enabled", return_value=hub), \
         patch("app.api.routes.agent._live_mcp_listing", listing):
        response = await get_agent_run_definition(saved._request(), "agent-1", first_use=first_use)
    return json.loads(response.body)["definition"], listing


async def _assistant(*, first_use: bool, hub: bool = True) -> tuple[dict[str, Any], AsyncMock]:
    from app.api.routes.agent import get_agent_run_definition

    services = assist._services()
    listing = AsyncMock(return_value=({"mcp-1": {"lookup_note": "sha256:" + "c" * 64}}, frozenset({"mcp-1"})))
    with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
         patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}), \
         patch("app.api.routes.agent.get_assistant_agent", AsyncMock(return_value=(dict(assist._ASSISTANT), {}))), \
         patch("app.api.routes.agent.resolve_web_search", new_callable=AsyncMock, return_value=None), \
         patch("app.services.featureflag.platform_settings.is_actions_enabled", new_callable=AsyncMock,
               return_value=True), \
         patch("app.agents.mcp.service.is_mcp_enabled", new_callable=AsyncMock, return_value=True), \
         patch("app.harness.mcp_hub.settings.hub_enabled", return_value=hub), \
         patch("app.harness.assembly.descriptor_pins.ToolDescriptors",
               return_value=assist._Approvals({"mcp-1": {"lookup_note": "sha256:" + "b" * 64}})), \
         patch("app.api.routes.agent._live_mcp_listing", listing):
        response = await get_agent_run_definition(
            assist._request(assist._SESSION), ASSISTANT_AGENT_KEY, first_use=first_use
        )
    return json.loads(response.body)["definition"], listing


@pytest.mark.asyncio
@pytest.mark.usefixtures("mcp_approvals_open")
async def test_la1_a_saved_agents_servers_are_granted_their_attached_tools_unlisted() -> None:
    definition, listing = await _saved(first_use=True)
    listing.assert_not_awaited()
    [server] = definition["mcpServers"]
    assert [t["name"] for t in server["tools"]] == ["list_prs"]
    assert "descriptorDigest" not in server["tools"][0], "pinned at the run's first use, not here"
    assert "atFirstUse" not in server


@pytest.mark.asyncio
@pytest.mark.usefixtures("mcp_approvals_open")
async def test_la2_an_older_worker_still_gets_the_listing_and_its_pins() -> None:
    definition, listing = await _saved(first_use=False)
    listing.assert_awaited_once()
    assert definition["mcpServers"][0]["tools"][0]["descriptorDigest"] == "sha256:" + "d" * 64


@pytest.mark.asyncio
@pytest.mark.usefixtures("mcp_approvals_open")
async def test_la3_the_default_chats_servers_are_granted_whole_and_nothing_is_listed() -> None:
    definition, listing = await _assistant(first_use=True)
    listing.assert_not_awaited()
    [server] = definition["mcpServers"]
    assert server["atFirstUse"] is True
    assert server["tools"] == [] and "unavailable" not in server


@pytest.mark.asyncio
@pytest.mark.usefixtures("mcp_approvals_open")
async def test_la5_the_default_chat_for_an_older_worker_is_named_by_its_listing() -> None:
    definition, listing = await _assistant(first_use=False)
    listing.assert_awaited_once()
    [server] = definition["mcpServers"]
    assert "atFirstUse" not in server and [t["name"] for t in server["tools"]] == ["lookup_note"]
