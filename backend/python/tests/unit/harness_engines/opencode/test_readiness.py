"""E2E3-F6: opencode's first turn waits until it has the tool gateway connected (``readiness``)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpcore
import pytest

from harness_core.engines.errors import EngineUnhealthy
from harness_core.logs import HarnessLog
from harness_engines.opencode.launch import MCP_SERVER
from harness_engines.opencode_api.http import OpencodeHttpError
from harness_engines.opencode_api.readiness import await_tool_gateway

if TYPE_CHECKING:
    from harness_core.engines.types import JsonValue

LOG = HarnessLog(logging.getLogger("test.opencode.readiness"))


@dataclass
class Http:
    """opencode's ``/mcp`` routes: the gateway's state, connected by ``connect_after`` reconnects (None: never)."""

    status: str = "failed"
    connect_after: int | None = None
    missing: bool = False
    slow: bool = False
    requests: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    async def request(
        self, method: str, path: str, body: JsonValue | None = None, *, timeout_s: float = 30.0
    ) -> JsonValue:
        self.requests.append((method, path))
        if self.missing:
            raise OpencodeHttpError(404, path)
        if self.slow:
            raise httpcore.ReadTimeout("opencode is still connecting")
        if method == "POST":
            assert path == f"/mcp/{MCP_SERVER}/connect"
            if self.connect_after is not None and self.requests.count(("POST", path)) >= self.connect_after:
                self.status = "connected"
            return self.status == "connected"
        assert (method, path) == ("GET", "/mcp")
        return {MCP_SERVER: {"status": self.status, **({"error": "SSE error"} if self.status == "failed" else {})}}


async def test_e2e3_f6_a_connected_gateway_is_asked_once() -> None:
    http = Http(status="connected")
    await await_tool_gateway(http, LOG, server="tools", timeout_s=5)  # type: ignore[arg-type]
    assert http.requests == [("GET", "/mcp")]


async def test_e2e3_f6_a_gateway_that_refused_at_start_is_reconnected_until_it_answers() -> None:
    http = Http(connect_after=3)
    await await_tool_gateway(http, LOG, server="tools", timeout_s=5)  # type: ignore[arg-type]
    connect = ("POST", f"/mcp/{MCP_SERVER}/connect")
    assert http.requests == [("GET", "/mcp"), connect] * 3 + [("GET", "/mcp")]


@pytest.mark.parametrize("http", [Http(), Http(slow=True)], ids=["failed", "never_answers"])
async def test_e2e3_f6_a_gateway_that_never_connects_is_an_engine_that_never_came_up(
    http: Http, caplog: pytest.LogCaptureFixture
) -> None:
    with pytest.raises(EngineUnhealthy):
        await await_tool_gateway(http, LOG, server="tools", timeout_s=0.3)  # type: ignore[arg-type]
    assert "engine.tool_gateway.unconnected" in caplog.text


async def test_e2e3_f6_a_binary_without_the_mcp_routes_is_unhealthy() -> None:
    with pytest.raises(EngineUnhealthy) as failed:
        await await_tool_gateway(Http(missing=True), LOG, server="tools", timeout_s=5)  # type: ignore[arg-type]
    assert isinstance(failed.value.__cause__, OpencodeHttpError)
