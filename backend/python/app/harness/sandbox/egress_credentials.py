"""Credentials PipesHub hands the egress proxy for a sandbox, sealed with the deployment's key (OWD-1, WP-06).

The legacy code sandbox (a package registry's or the operator database's password) and MCP runners (a server's own
secret) give the sandbox a placeholder and put the real value, sealed, in the sandbox's egress route. The proxy
opens it with the same ``SECRET_KEY`` and puts it on the request on the way out. Without the key nothing is sealed:
a caller refuses to start a sandbox that would need one rather than hand it the secret.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.utils.encryption.secret_key_ring import deployment_ring
from harness_core.sandbox.egress.sealed import (
    PLACEHOLDER_PREFIX,
    CredentialSealer,
    SealedTokenSource,
    new_placeholder,
)

if TYPE_CHECKING:
    from harness_core.clock import Clock

# The deployment's key, as the proxy's durable state reads it (``egress_state.ENV_SECRET_KEY``).
ENV_SECRET_KEY = "SECRET_KEY"


class CredentialSealingUnavailable(RuntimeError):
    """``SECRET_KEY`` is not set here, so a secret cannot be handed to the proxy; the sandbox is not started."""

    reason_code = "credential_sealing_unavailable"

    def __init__(self) -> None:
        super().__init__("this service cannot seal credentials for the egress proxy: SECRET_KEY is not set")


def credential_sealer() -> CredentialSealer | None:
    """Seals under ``SECRET_KEY``; opens what ``SECRET_KEY_PREVIOUS`` or ``SECRET_KEY_NEXT`` sealed too (ADR-KEY-01)."""
    ring = deployment_ring()
    if ring is None:
        return None
    current, accepted = ring.raw()
    return CredentialSealer(current, accepted=accepted)


def required_sealer() -> CredentialSealer:
    sealer = credential_sealer()
    if sealer is None:
        raise CredentialSealingUnavailable
    return sealer


def sealed_token_source(clock: Clock) -> SealedTokenSource | None:
    """The proxy's opener; ``None`` when the deployment has no key (sealed routes are then refused, never sent)."""
    sealer = credential_sealer()
    return SealedTokenSource(sealer, clock) if sealer is not None else None


__all__ = [
    "PLACEHOLDER_PREFIX",
    "CredentialSealingUnavailable",
    "credential_sealer",
    "new_placeholder",
    "required_sealer",
    "sealed_token_source",
]
