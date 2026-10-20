"""A transport for a client whose address a user chose, such as a personal action's site URL.

Every origin the client reaches, redirects included, is resolved and checked once and then
connected to the address that was checked, so a DNS answer that changes after the check can't
steer the client into the deployment's network. It is the guard MCP servers' traffic goes
through, with private addresses always refused.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agents.mcp.errors import MCPUrlBlockedError
from app.agents.mcp.url_guard import MCPUrlPolicy, PerOriginGuardedTransport

if TYPE_CHECKING:
    import httpx


class AddressNotAllowedError(Exception):
    """A request went to an address a public-only client may not reach."""


class PublicOnlyTransport(PerOriginGuardedTransport):
    def __init__(self, *, inner: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(MCPUrlPolicy(allow_private=False), inner=inner)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return await super().handle_async_request(request)
        except MCPUrlBlockedError as e:
            # The guard's message names MCP servers; this client isn't one.
            raise AddressNotAllowedError(
                "This action's address isn't allowed: it must be a public address this server can reach."
            ) from e
