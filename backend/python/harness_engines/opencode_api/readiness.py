"""The tool gateway is connected before opencode's first turn (E2E3-F6).

opencode 1.18.34 connects its MCP servers when the instance first needs them and waits for one that accepts and
answers slowly. One that refuses (the egress proxy answers 503 while nothing listens behind it) is ``failed`` at
once and never tried again, and every turn of the session goes out without any gateway tool (verified on the pinned
binary). So the adapter asks ``GET /mcp`` until the gateway is ``connected``, reconnects it
(``POST /mcp/{name}/connect``) while it is not, and calls an engine whose gateway never connects unhealthy.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpcore

from harness_core.engines.errors import EngineUnhealthy
from harness_core.engines.jsonview import as_object, as_str
from harness_engines.opencode_api.http import OpencodeHttpError

if TYPE_CHECKING:
    from harness_core.logs import HarnessLog
    from harness_engines.opencode_api.http import OpencodeHttp

# As long as Codex waits for its required gateway (``harness_engines.codex.launch.MCP_STARTUP_TIMEOUT_S``).
GATEWAY_TIMEOUT_S = 30.0
# A reconnect opencode could not make (nothing behind the proxy yet) is tried again after this long.
RECONNECT_PAUSE_S = 0.5
CONNECTED = "connected"


async def gateway_status(http: OpencodeHttp, *, server: str, timeout_s: float) -> str:
    """The tool gateway's state as opencode reports it (``connected``, ``failed``…); "" if unlisted. The first ask
    waits while opencode connects."""
    body = as_object(await http.request("GET", "/mcp", timeout_s=timeout_s))
    return as_str(as_object(body.get(server)).get("status"))


async def await_tool_gateway(
    http: OpencodeHttp, log: HarnessLog, *, server: str, timeout_s: float = GATEWAY_TIMEOUT_S
) -> None:
    """Returns once opencode has the tool gateway (its MCP server named ``server``, as the engine's config names it)
    connected; ``EngineUnhealthy`` if it is not within ``timeout_s``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    status = ""
    reconnects = 0
    try:
        while (remaining := deadline - loop.time()) > 0:
            status = await gateway_status(http, server=server, timeout_s=remaining)
            if status == CONNECTED:
                if reconnects:
                    log.info("engine.tool_gateway.connected", reconnects=reconnects)
                return
            if reconnects:
                await asyncio.sleep(min(RECONNECT_PAUSE_S, max(0.0, deadline - loop.time())))
            reconnects += 1
            _ = await http.request("POST", f"/mcp/{server}/connect", timeout_s=max(0.01, deadline - loop.time()))
    except httpcore.TimeoutException:
        pass
    except OpencodeHttpError as error:
        log.warning("engine.tool_gateway.unknown", status=error.status, reasonCode=EngineUnhealthy.reason_code)
        raise EngineUnhealthy("opencode cannot report or reconnect the tool gateway") from error
    log.warning(
        "engine.tool_gateway.unconnected",
        status=status or "unlisted",
        reconnects=reconnects,
        reasonCode=EngineUnhealthy.reason_code,
    )
    raise EngineUnhealthy(f"opencode did not connect the tool gateway within {timeout_s:g} s ({status or 'unlisted'})")


__all__ = ["GATEWAY_TIMEOUT_S", "await_tool_gateway", "gateway_status"]
