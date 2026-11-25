"""Gateway tokens from Node, for whoever injects them (the egress proxy's ``TokenSource``).

One issuance path (E2, Node ``gateway-token-authority.ts``):
``POST /api/v1/agent-harness/internal/runs/{runId}/gateway-token`` with the
egress proxy's role credential, the attempt the sandbox is bound to and the
broker's proof of that binding (R38IDENT-FU-7). Node
binds the token from its own run record and pinned spec (org, session, fence,
grant digests) and refuses once that attempt no longer holds the lease, which is
what makes on-demand refresh "while leased". Tokens never appear in logs or
reprs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import quote

from app.core.org_context import org_of
from app.harness.journal_node.client import INTERNAL_PREFIX, error_code
from app.harness.journal_node.http import NodeServiceClient
from harness_core.engines.attempt_labels import bound_attempt
from harness_core.logs import HarnessLog
from harness_core.sandbox.egress.errors import TokenUnavailable
from harness_core.sandbox.egress.types import IssuedToken

if TYPE_CHECKING:
    import httpx

    from app.harness.contracts.state import WorkerCredentialProvider
    from harness_core.sandbox.egress.types import EgressBinding
    from harness_core.sandbox.types import TokenRoute

LOGGER = "app.harness.gateway.tokens"


@dataclass(frozen=True, slots=True)
class IssuedGatewayToken:
    value: str = field(repr=False)
    expires_at: datetime


class GatewayTokenUnavailable(Exception):
    """Node would not issue (``reason``: Node's code, e.g. ``RUN_NOT_LEASED``), or could not be reached."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"gateway token unavailable: {reason}")
        self.reason = reason


def _issued(response: httpx.Response) -> IssuedGatewayToken:
    body = response.json()
    token, expires = body.get("token"), body.get("expiresAt")
    if not isinstance(token, str) or not isinstance(expires, int):
        raise GatewayTokenUnavailable("malformed_response")
    return IssuedGatewayToken(token, datetime.fromtimestamp(expires / 1000, tz=UTC))


class NodeGatewayTokens:
    def __init__(
        self,
        *,
        base_url: str,
        credentials: WorkerCredentialProvider,
        http_client: httpx.AsyncClient | None = None,
        timeout_s: float = 10.0,
        log: HarnessLog | None = None,
    ) -> None:
        self._credentials = credentials
        self._node = NodeServiceClient(
            base_url=base_url, timeout_s=timeout_s, http_client=http_client, service_name="node.gateway_tokens"
        )
        self._log = log or HarnessLog.named(LOGGER)

    async def for_attempt(self, org_id: str, run_id: str, attempt_id: str, binding_proof: str) -> IssuedGatewayToken:
        credential = await self._credentials.worker_credential()
        # Node looks the run up within its org and issues only against the broker's proof for exactly this attempt
        # (R38IDENT-FU-7); the route's body schema is strict and requires all three.
        return await self._post(
            f"{INTERNAL_PREFIX}/runs/{quote(run_id, safe='')}/gateway-token",
            credential,
            {"orgId": org_id, "attemptId": attempt_id, "bindingProof": binding_proof},
            orgId=org_id,
            runId=run_id,
            attemptId=attempt_id,
        )

    async def _post(self, path: str, bearer: str, body: dict[str, str], **fields: str) -> IssuedGatewayToken:
        def unreachable(exc: httpx.HTTPError) -> GatewayTokenUnavailable:
            self._log.warning("gateway_token.unavailable", reason="node_unreachable", errorType=type(exc).__name__, **fields)
            return GatewayTokenUnavailable("node_unreachable")

        response = await self._node.send("POST", path, bearer=bearer, json=body, unreachable=unreachable)
        if response.status_code != 200:
            reason = error_code(response) or f"http_{response.status_code}"
            self._log.warning("gateway_token.unavailable", reason=reason, status=response.status_code, **fields)
            raise GatewayTokenUnavailable(reason)
        issued = _issued(response)
        self._log.info("gateway_token.issued", expiresAt=issued.expires_at.isoformat(), **fields)
        return issued


class NodeGatewayTokenSource:
    """The egress proxy's ``TokenSource`` (P5.4) bound to Node's gateway-token authority (E2 decision 2).

    The sandbox's binding labels name the run attempt (a kept sign-in sandbox's under the bound keys); Node binds every other
    claim from its own records and refuses once that attempt no longer holds the
    run, so the proxy's refresh-before-expiry is "while leased". Both gateways
    get the same kind of token: each verifies it against Node's published keys.
    """

    def __init__(self, tokens: NodeGatewayTokens) -> None:
        self._tokens = tokens

    async def issue(self, route: TokenRoute, binding: EgressBinding) -> IssuedToken:
        try:
            issued = await self._issue(binding)
        except GatewayTokenUnavailable as exc:
            raise TokenUnavailable(exc.reason) from exc
        return IssuedToken(issued.value, issued.expires_at)

    async def _issue(self, binding: EgressBinding) -> IssuedGatewayToken:
        org_id, attempt = org_of(binding.labels), bound_attempt(binding.labels)
        if not org_id or attempt is None:
            raise TokenUnavailable("the sandbox's binding names no run attempt")
        if not binding.attestation:
            # Node would refuse it; not asking keeps a broker that signs nothing from looking like Node's outage.
            raise TokenUnavailable("binding_unattested")
        return await self._tokens.for_attempt(org_id, *attempt, binding.attestation)


__all__ = [
    "GatewayTokenUnavailable",
    "IssuedGatewayToken",
    "NodeGatewayTokenSource",
    "NodeGatewayTokens",
]
