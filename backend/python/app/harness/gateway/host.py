"""``ToolGatewayHost`` over E2's ``ToolGatewayApp``: a run's endpoint at an address the egress proxy reaches.

The durable worker mounts ``ToolGatewayApp`` under ``base_url``; each run gets ``{base_url}/{key}``, and the proxy
appends the path the engine asked for (``/mcp``). The key is random and lives only as long as the turn; the gateway
token is what authorizes.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.harness.gateway.mcp import ToolGatewayApp
    from harness_core.gateway.ports import ToolEndpoint
    from harness_core.gateway.types import GatewayBinding


class HttpToolGatewayHost:
    def __init__(self, app: ToolGatewayApp, *, base_url: str) -> None:
        self._app = app
        self._base = base_url.rstrip("/")

    @contextlib.asynccontextmanager
    async def serve(self, endpoint: ToolEndpoint, binding: GatewayBinding) -> AsyncIterator[str]:
        async with self._app.serving(endpoint, binding) as key:
            yield f"{self._base}/{key}"


__all__ = ["HttpToolGatewayHost"]
