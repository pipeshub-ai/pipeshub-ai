"""The gateways' entry point binds the org a verified run token names (ADR-SEC-02, main #4703's ``org_context``).

The Tool Gateway (uvicorn) and the Model Gateway (aiohttp) serve each request in a task of its own, so the run task's
binding never reaches them; without this every org-scoped read a gateway request makes runs unbound."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.org_context import OrgContext, set_request_context
from harness_core.gateway.token import GatewayTokenVerifier

if TYPE_CHECKING:
    from harness_core.gateway.types import GatewayClaims

GATEWAY_TOKEN_SOURCE = "gateway_token"


class OrgBoundGatewayTokenVerifier(GatewayTokenVerifier):
    async def verify(self, token: str | None) -> GatewayClaims:
        claims = await super().verify(token)
        binding = claims.binding
        # The token names no person: the run's org is what the request acts for; the run id traces it.
        set_request_context(OrgContext(binding.tenant, None, "service", GATEWAY_TOKEN_SOURCE, binding.run_id))
        return claims


__all__ = ["GATEWAY_TOKEN_SOURCE", "OrgBoundGatewayTokenVerifier"]
