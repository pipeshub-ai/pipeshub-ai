"""E2E3-F6: Claude Code's first turn waits until it has the tool gateway connected (``readiness``)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.errors import EngineUnhealthy
from harness_core.logs import HarnessLog
from harness_engines.claude_code.launch import MCP_SERVER
from harness_engines.claude_code.readiness import await_tool_gateway
from harness_engines.claude_code.stream import ClaudeControlError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.types import JsonValue

LOG = HarnessLog(logging.getLogger("test.claude.readiness"))


@dataclass
class Stream:
    """Claude Code's answers to ``mcp_status``, in order (the last one repeats); ``mcp_reconnect`` connects the
    gateway once ``reconnect_after`` reconnects were refused."""

    statuses: list[str]
    reconnect_after: int | None = None
    unsupported: bool = False
    controls: list[str] = field(default_factory=list[str])

    async def control(
        self, subtype: str, fields: Mapping[str, JsonValue] | None = None, *, timeout_s: float = 60.0
    ) -> Mapping[str, JsonValue]:
        self.controls.append(subtype)
        if self.unsupported:
            raise ClaudeControlError(subtype, "Unsupported control request subtype")
        if subtype == "mcp_reconnect":
            assert fields == {"serverName": MCP_SERVER}
            refused = self.controls.count("mcp_reconnect")
            if self.reconnect_after is None or refused <= self.reconnect_after:
                raise ClaudeControlError(subtype, "Error POSTing to endpoint: ")
            self.statuses = ["connected"]
            return {}
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return {"mcpServers": [{"name": "other", "status": "connected"}, {"name": MCP_SERVER, "status": status}]}


async def test_e2e3_f6_a_gateway_still_connecting_is_waited_for() -> None:
    stream = Stream(["pending", "pending", "pending", "connected"])
    await await_tool_gateway(stream, LOG, timeout_s=5)  # type: ignore[arg-type]
    assert stream.controls == ["mcp_status"] * 4, "a connecting gateway is never reconnected"


async def test_e2e3_f6_a_gateway_that_refused_at_start_is_reconnected_until_it_answers() -> None:
    stream = Stream(["failed"], reconnect_after=2)
    await await_tool_gateway(stream, LOG, timeout_s=5)  # type: ignore[arg-type]
    assert stream.controls == ["mcp_status", "mcp_reconnect"] * 3 + ["mcp_status"]


@pytest.mark.parametrize("status", ["failed", "pending", "needs-auth"])
async def test_e2e3_f6_a_gateway_that_never_connects_is_an_engine_that_never_came_up(
    status: str, caplog: pytest.LogCaptureFixture
) -> None:
    stream = Stream([status])
    with pytest.raises(EngineUnhealthy):
        await await_tool_gateway(stream, LOG, timeout_s=0.3)  # type: ignore[arg-type]
    assert "engine.tool_gateway.unconnected" in caplog.text and f"status={status}" in caplog.text


async def test_e2e3_f6_a_gateway_claude_code_does_not_list_is_never_connected() -> None:
    stream = Stream(["connected"])

    async def unlisted(subtype: str, fields: object = None, *, timeout_s: float = 60.0) -> Mapping[str, JsonValue]:
        return {"mcpServers": []}

    stream.control = unlisted  # type: ignore[method-assign]
    with pytest.raises(EngineUnhealthy, match="unlisted"):
        await await_tool_gateway(stream, LOG, timeout_s=0.3)  # type: ignore[arg-type]


async def test_e2e3_f6_a_binary_that_cannot_report_its_gateway_is_unhealthy() -> None:
    with pytest.raises(EngineUnhealthy) as failed:
        await await_tool_gateway(Stream(["connected"], unsupported=True), LOG, timeout_s=5)  # type: ignore[arg-type]
    assert isinstance(failed.value.__cause__, ClaudeControlError)
