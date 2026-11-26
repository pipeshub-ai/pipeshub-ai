"""The tool gateway is connected before Claude Code's first turn (E2E3-F6).

Claude Code 2.1.294 connects its MCP servers as it starts and waits, at a turn's first model request, for one that
is still connecting. One that has failed is not waited for: a gateway that refused its first ``initialize`` (the
egress proxy answers 503 while nothing listens behind it) is ``failed`` at once, and the first request goes out
without any gateway tool (verified on the pinned binary). Claude Code retries such a server only with a backoff of
its own, seconds apart. So the adapter asks ``mcp_status`` until the gateway is ``connected``, reconnects it
(``mcp_reconnect``) while it is ``failed``, and calls an engine whose gateway never connects unhealthy.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from harness_core.engines.errors import EngineUnhealthy
from harness_core.engines.jsonview import as_list, as_object, as_str
from harness_engines.claude_code.launch import MCP_SERVER
from harness_engines.claude_code.stream import ClaudeControlError

if TYPE_CHECKING:
    from harness_core.logs import HarnessLog
    from harness_engines.claude_code.stream import ClaudeStream

# As long as Codex waits for its required gateway (``harness_engines.codex.launch.MCP_STARTUP_TIMEOUT_S``).
GATEWAY_TIMEOUT_S = 30.0
POLL_S = 0.1
# Reconnects are this far apart: one Claude Code makes at once while nothing listens behind the proxy.
RECONNECT_PAUSE_S = 0.5
CONNECTED = "connected"
FAILED = "failed"


async def gateway_status(stream: ClaudeStream, *, timeout_s: float) -> str:
    """The tool gateway's state as Claude Code reports it: ``pending``, ``connected``, ``failed``…; "" if unlisted."""
    reply = await stream.control("mcp_status", timeout_s=timeout_s)
    for server in as_list(reply.get("mcpServers")):
        entry = as_object(server)
        if as_str(entry.get("name")) == MCP_SERVER:
            return as_str(entry.get("status"))
    return ""


async def _reconnect(stream: ClaudeStream, *, timeout_s: float) -> None:
    """One reconnect. Claude Code answers once it has tried, with an error when the gateway refused again; the next
    ``mcp_status`` tells either way."""
    with contextlib.suppress(ClaudeControlError):
        _ = await stream.control("mcp_reconnect", {"serverName": MCP_SERVER}, timeout_s=timeout_s)


async def await_tool_gateway(stream: ClaudeStream, log: HarnessLog, *, timeout_s: float = GATEWAY_TIMEOUT_S) -> None:
    """Returns once Claude Code has the tool gateway connected; ``EngineUnhealthy`` if it is not within ``timeout_s``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    status = ""
    reconnects = 0
    try:
        while (remaining := deadline - loop.time()) > 0:
            status = await gateway_status(stream, timeout_s=remaining)
            if status == CONNECTED:
                if reconnects:
                    log.info("engine.tool_gateway.connected", reconnects=reconnects)
                return
            if status != FAILED:
                await asyncio.sleep(min(POLL_S, max(0.0, deadline - loop.time())))
                continue
            if reconnects:
                await asyncio.sleep(min(RECONNECT_PAUSE_S, max(0.0, deadline - loop.time())))
            reconnects += 1
            await _reconnect(stream, timeout_s=max(0.01, deadline - loop.time()))
    except TimeoutError:
        pass
    except ClaudeControlError as error:
        log.warning("engine.tool_gateway.unknown", reasonCode=EngineUnhealthy.reason_code)
        raise EngineUnhealthy("Claude Code cannot report the tool gateway's state") from error
    log.warning(
        "engine.tool_gateway.unconnected",
        status=status or "unlisted",
        reconnects=reconnects,
        reasonCode=EngineUnhealthy.reason_code,
    )
    raise EngineUnhealthy(f"Claude Code did not connect the tool gateway within {timeout_s:g} s ({status or 'unlisted'})")


__all__ = ["GATEWAY_TIMEOUT_S", "await_tool_gateway", "gateway_status"]
