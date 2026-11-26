"""In-memory ``ToolGatewayHost``: mounts a run's ``ToolEndpoint`` where a fake egress proxy can reach it.

Each mount gets ``<base>/<key>``; ``handle`` answers the conformance engines'
tool wire (``tools/list``, ``tools/call``, as ``ConformanceGateway`` does) by
calling the mounted endpoint with claims for the mount's binding. It verifies
no token: the HTTP host (``ToolGatewayApp``) is where tokens are checked.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

from harness_core.engines.jsonview import as_object, as_str
from harness_core.gateway.types import GatewayClaims
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from pydantic import JsonValue

    from harness_core.gateway.ports import ToolEndpoint
    from harness_core.gateway.types import GatewayBinding, McpResult
    from harness_core.ids import IdSource
    from harness_core.testing.sandbox.egress import EgressRequest

DEFAULT_BASE = "http://tool-gateway-host.memory"
_NEVER_EXPIRES = 4_102_444_800


class InMemoryToolGatewayHost:
    def __init__(self, *, base: str = DEFAULT_BASE, ids: IdSource | None = None) -> None:
        self.base = base.rstrip("/")
        self._ids = ids or SeededIds()
        self._mounts: dict[str, tuple[ToolEndpoint, GatewayBinding]] = {}
        self.calls: list[tuple[str, str]] = []

    @contextlib.asynccontextmanager
    async def serve(self, endpoint: ToolEndpoint, binding: GatewayBinding) -> AsyncIterator[str]:
        key = self._ids.new_id("mount")
        self._mounts[key] = (endpoint, binding)
        try:
            yield f"{self.base}/{key}"
        finally:
            del self._mounts[key]

    @property
    def mounted(self) -> int:
        return len(self._mounts)

    async def call(self, name: str, arguments: Mapping[str, JsonValue]) -> McpResult:
        """A call as the sandbox would send it to the one endpoint mounted now (what ``ph`` does from a turn)."""
        [(endpoint, binding)] = self._mounts.values()
        return await endpoint.call_tool(GatewayClaims(binding, kid="in-memory", expires_at=_NEVER_EXPIRES), name, arguments)

    async def handle(self, request: EgressRequest) -> JsonValue:
        key = urlsplit(request.url).path.strip("/").split("/", 1)[0]
        mounted = self._mounts.get(key)
        if mounted is None:
            return {"content": "no such binding", "isError": True, "heldS": 0}
        endpoint, binding = mounted
        claims = GatewayClaims(binding, kid="in-memory", expires_at=_NEVER_EXPIRES)
        body = as_object(request.body)
        method = as_str(body.get("method"))
        if method == "tools/list":
            tools = await endpoint.list_tools(claims)
            return {"tools": [{"name": t.name, "description": t.description} for t in tools]}
        params = as_object(body.get("params"))
        name = as_str(params.get("name"))
        self.calls.append((key, name))
        arguments = cast("Mapping[str, JsonValue]", as_object(params.get("arguments")))
        result = await endpoint.call_tool(claims, name, arguments)
        return {"content": result.text, "isError": result.is_error, "heldS": 0}


__all__ = ["DEFAULT_BASE", "InMemoryToolGatewayHost"]
