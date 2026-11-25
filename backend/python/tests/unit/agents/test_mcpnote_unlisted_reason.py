"""MCPNOTE-1: a server of the assistant whose tools admission could not name carries why on the run definition.

Node words it to the person ("Atlassian Rovo wasn't used: sign in to it first"), so the reason comes from what the
server's listing at admission recorded, never a guess, and the definition holds no credential or URL for it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.agent_loop.mcp_tool_loader import NO_TOOLS_OFFERED
from app.agents.run_definition import (
    ASSISTANT_AGENT_KEY,
    assistant_mcp_servers,
    build_run_definition,
    unlisted_mcp_reason,
)
from harness_core.mcp_hub.errors import ToolChanged, ToolUnapproved
from tests.support.bound_org import bind_test_org

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _bound_org() -> Iterator[str]:
    yield from bind_test_org("org-1")


_ROVO = {"instanceId": "mcp-rovo", "name": "Atlassian Rovo", "typeId": "atlassian", "authMode": "oauth"}
_NOTES = {"instanceId": "mcp-1", "name": "e2esaas", "typeId": None, "authMode": "none"}


class _Approvals:
    def __init__(self, by_instance: dict[str, dict[str, str]]) -> None:
        self._by_instance = by_instance

    async def approved_mcp_tools(self, instance_id: str) -> Any:  # noqa: ANN401
        tools = self._by_instance.get(instance_id)
        return None if tools is None else type("Approved", (), {"tools": tools})()

    def toolset_tool(self, toolset: str, tool: str) -> str | None:
        return None


class TestTheReason:
    @pytest.mark.parametrize(
        ("failures", "hub", "reason"),
        [
            (["unauthorized"], False, "sign_in"),
            (["auth_expired"], True, "sign_in"),
            (["needs_permission"], False, "sign_in"),
            # A refused sign-in hides everything else: nothing can be approved until the person signs in again.
            (["timeout", "unauthorized", ToolUnapproved.reason_code], True, "sign_in"),
            ([ToolUnapproved.reason_code], True, "not_approved"),
            ([ToolChanged.reason_code], True, "not_approved"),
            ([NO_TOOLS_OFFERED], False, "no_tools"),
            (["timeout"], False, "unreachable"),
            (["unreachable"], True, "unreachable"),
            (["error"], False, "unreachable"),
            # Nothing recorded: the hub's approvals dropped it, or a discovery that ran out of time did.
            ([], True, "not_approved"),
            ([], False, "unreachable"),
        ],
    )
    def test_each_listing_outcome_reads_as_what_the_person_can_do(
        self, failures: list[str], hub: bool, reason: str
    ) -> None:
        assert unlisted_mcp_reason(failures, hub=hub) == reason


class TestTheDefinition:
    @pytest.mark.asyncio
    async def test_each_server_without_tools_names_its_own_reason_and_only_oauth_signs_in_here(self) -> None:
        token = {**_ROVO, "instanceId": "mcp-tok", "name": "Linear", "authMode": "api_token"}
        servers = await assistant_mcp_servers(
            [_NOTES, _ROVO, token],
            _Approvals({"mcp-1": {"lookup_note": "sha256:" + "a" * 64}}),
            failures=[
                {"instanceId": "mcp-rovo", "reason": "unauthorized"},
                {"instanceId": "mcp-tok", "reason": "unauthorized"},
                {"instanceId": None, "reason": "timeout"},
            ],
            hub=True,
        )
        assert "unavailable" not in servers[0]
        assert servers[1]["unavailable"] == {"reason": "sign_in", "signInHere": True}
        assert servers[2]["unavailable"] == {"reason": "sign_in", "signInHere": False}

    def test_the_wire_carries_it_only_for_a_server_left_out(self) -> None:
        definition = build_run_definition(
            {"mcpServers": [
                {**_NOTES, "tools": [{"name": "lookup_note", "fullName": "mcp_e2esaas_lookup_note"}]},
                {**_ROVO, "tools": [], "unavailable": {"reason": "sign_in", "signInHere": True}},
            ]},
            agent_key=ASSISTANT_AGENT_KEY,
            actions_enabled=True,
            mcp_enabled=True,
            web_search_provider=None,
        )
        wire = json.loads(definition.model_dump_json(by_alias=True))["mcpServers"]
        assert "unavailable" not in wire[0]
        assert wire[1] == {
            "instanceId": "mcp-rovo",
            "name": "Atlassian Rovo",
            "typeId": "atlassian",
            "tools": [],
            "unavailable": {"reason": "sign_in", "signInHere": True},
        }

    def test_a_reason_this_build_does_not_know_is_refused_not_passed_on(self) -> None:
        with pytest.raises(ValueError, match="reason"):
            build_run_definition(
                {"mcpServers": [{**_ROVO, "unavailable": {"reason": "forgot"}}]},
                agent_key=ASSISTANT_AGENT_KEY,
                actions_enabled=True,
                mcp_enabled=True,
                web_search_provider=None,
            )


class TestTheListingRecordsWhy:
    @pytest.mark.asyncio
    async def test_failures_receive_each_listing_failure_and_each_server_that_lost_its_sign_in(self) -> None:
        from app.api.routes.agent import _live_mcp_listing

        servers = [_NOTES, _ROVO, {**_ROVO, "instanceId": "mcp-gone", "name": "Gone"}]
        configs = {"mcp-1": {"instance": {"_id": "mcp-1"}, "ownerId": "u1"}}

        async def resolve(*_args: object, blocked: list[dict[str, Any]], **_kwargs: object) -> tuple:
            blocked.append({"instanceId": "mcp-rovo", "name": "Atlassian Rovo", "problem": "not_connected"})
            blocked.append({"instanceId": "mcp-gone", "name": "Gone", "problem": "not_found"})
            return configs, [_NOTES], [], []

        async def discovered(self: object, context: Any) -> tuple[dict[str, dict[str, str]], frozenset[str]]:  # noqa: ANN401
            context.mcp_tool_load_failures.append(
                {"instanceId": "mcp-1", "name": "e2esaas", "reason": "timeout", "detail": "secret-free"}
            )
            context.mcp_tool_load_failures.append(
                {"instanceId": "mcp-1", "name": "e2esaas", "reason": "mcp_tool_description_flagged", "tools": ["exfil"]}
            )
            return {}, frozenset()

        failures: list[dict[str, Any]] = []
        with patch("app.api.routes.agent._resolve_mcp_server_configs", resolve), \
             patch("app.agents.agent_loop.mcp_tool_loader.MCPToolProvider.live_listing", discovered), \
             patch("app.agents.agent_loop.context.AgentContext.close_credentials", new_callable=AsyncMock):
            await _live_mcp_listing(
                servers, AsyncMock(), {"userId": "u1", "orgId": "org-1"}, {"email": "u1@x"}, MagicMock(),
                failures=failures,
            )
        assert failures == [
            {"instanceId": "mcp-rovo", "reason": "unauthorized"},
            {"instanceId": "mcp-gone", "reason": "error"},
            {"instanceId": "mcp-1", "reason": "timeout"},
            # The lane found it: the held tools' names must reach the definition, or nobody is told.
            {"instanceId": "mcp-1", "reason": "mcp_tool_description_flagged", "tools": ["exfil"]},
        ]
