"""Gateway tokens: compact JWS over Ed25519, signed by the issuer (Node), verified here.

The format mirrors the attempt token (P1a S2): header ``{alg: EdDSA, typ: JWT,
kid}``, one audience and issuer, strict claims, ``exp`` in epoch seconds. One
binding kind (``bnd``): ``run`` (one attempt of one run); any other kind is
malformed, ``request`` included (stage 1's, retired by S1R). Verification needs
only the issuer's published public keys; a ``kid`` this process has not seen makes it re-read them once (rate limited),
so a rotation on another replica is picked up without a restart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from harness_core.gateway.errors import TokenRefused
from harness_core.gateway.types import GatewayClaims, RunAttemptBinding
from harness_core.jws import (
    JWS_ALGORITHM,
    MIN_REFRESH_INTERVAL_S,
    JwsFailure,
    JwsProfile,
    JwsVerifier,
)

if TYPE_CHECKING:
    from harness_core.clock import Clock
    from harness_core.gateway.errors import TokenRefusalReason
    from harness_core.gateway.ports import GatewayKeySource
    from harness_core.gateway.types import GatewayBinding

GATEWAY_TOKEN_ALGORITHM = JWS_ALGORITHM
GATEWAY_TOKEN_ISSUER = "pipeshub-node/agent-harness"
GATEWAY_TOKEN_AUDIENCE = "pipeshub-agent-harness/gateway"
GATEWAY_TOKEN_TTL_S = 10 * 60
# Node replicas' clocks may disagree a little; a longer-lived token was not minted by the issuer.
MAX_TOKEN_LIFETIME_S = GATEWAY_TOKEN_TTL_S + 60
# A retired key keeps verifying for the longest token it signed plus a replica's stale keyring.
RETIRED_KEY_RETENTION_MS = 15 * 60 * 1000
MAX_TOKEN_LENGTH = 4096

_COMMON = frozenset({"iss", "aud", "bnd", "orgId", "sessionId", "iat", "exp"})
_RUN = _COMMON | {
    "runId",
    "parentRunId",
    "attemptId",
    "fence",
    "toolIdsDigest",
    "routesDigest",
}
_ID_MAX = 128

_REFUSALS: Final[dict[JwsFailure, tuple[TokenRefusalReason, str]]] = {
    "missing": ("token_missing", "gateway token required"),
    "malformed": ("token_malformed", "gateway token is malformed"),
    "algorithm": ("token_algorithm", "gateway token algorithm refused"),
    "header": ("token_malformed", "gateway token header is malformed"),
    "unknown_key": ("token_unknown_key", "gateway token signing key is unknown"),
    "key_type": ("token_unknown_key", "gateway token signing key is not Ed25519"),
    "signature": ("token_signature", "gateway token signature is invalid"),
    "issuer": ("token_issuer", "gateway token issuer refused"),
    "audience": ("token_audience", "gateway token audience refused"),
    "claims": ("token_malformed", "gateway token claims are malformed"),
    "lifetime": ("token_malformed", "gateway token lifetime exceeds the issuer's"),
    "expired": ("token_expired", "gateway token expired"),
}


def _refuse(reason: TokenRefusalReason, message: str) -> TokenRefused:
    return TokenRefused(reason, message)


def _refusal(failure: JwsFailure) -> TokenRefused:
    return TokenRefused(*_REFUSALS[failure])


def _ident(payload: dict[str, object], key: str, *, nullable: bool = False) -> str | None:
    value = payload.get(key)
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value or len(value) > _ID_MAX:
        raise _refuse("token_malformed", "gateway token claims are malformed")
    return value


def _int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _refuse("token_malformed", "gateway token claims are malformed")
    return value


def binding_of(payload: dict[str, object]) -> GatewayBinding:
    """The binding a token's payload names; any extra or missing claim is malformed."""
    if payload.get("bnd") != "run" or frozenset(payload) != _RUN:
        raise _refuse("token_malformed", "gateway token claims are malformed")
    tenant, scope = _ident(payload, "orgId"), _ident(payload, "sessionId")
    assert tenant is not None and scope is not None
    run, attempt = _ident(payload, "runId"), _ident(payload, "attemptId")
    tools, routes = _ident(payload, "toolIdsDigest"), _ident(payload, "routesDigest")
    assert run is not None and attempt is not None and tools is not None and routes is not None
    return RunAttemptBinding(
        tenant,
        scope,
        run,
        _ident(payload, "parentRunId", nullable=True),
        attempt,
        _int(payload, "fence"),
        tools,
        routes,
    )


def binding_claims(binding: GatewayBinding) -> dict[str, object]:
    """The payload claims naming ``binding`` (the issuer adds ``iss``, ``aud``, ``iat``, ``exp``)."""
    return {
        "bnd": "run",
        "orgId": binding.tenant,
        "sessionId": binding.scope,
        "runId": binding.run_id,
        "parentRunId": binding.parent_run_id,
        "attemptId": binding.attempt_id,
        "fence": binding.fence,
        "toolIdsDigest": binding.tool_ids_digest,
        "routesDigest": binding.routes_digest,
    }


class GatewayTokenVerifier:
    def __init__(
        self,
        keys: GatewayKeySource,
        clock: Clock,
        *,
        issuer: str = GATEWAY_TOKEN_ISSUER,
        audience: str = GATEWAY_TOKEN_AUDIENCE,
    ) -> None:
        profile = JwsProfile(issuer, audience, MAX_TOKEN_LIFETIME_S, MAX_TOKEN_LENGTH, RETIRED_KEY_RETENTION_MS)
        self._jws = JwsVerifier(keys, clock, profile, _refusal)

    async def verify(self, token: str | None) -> GatewayClaims:
        """Signature, issuer, audience, expiry and claim shape. Whether the claims are
        this endpoint's binding is the endpoint's check (``BindingMismatch``)."""
        verified = await self._jws.verify(token, binding_of)
        return GatewayClaims(verified.claims, verified.kid, verified.expires_at_s)


__all__ = [
    "GATEWAY_TOKEN_ALGORITHM",
    "GATEWAY_TOKEN_AUDIENCE",
    "GATEWAY_TOKEN_ISSUER",
    "GATEWAY_TOKEN_TTL_S",
    "MAX_TOKEN_LENGTH",
    "MAX_TOKEN_LIFETIME_S",
    "MIN_REFRESH_INTERVAL_S",
    "RETIRED_KEY_RETENTION_MS",
    "GatewayTokenVerifier",
    "binding_claims",
    "binding_of",
]
