"""IAppEventVerifier base and registry (strategy pattern)."""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.services.events.models import AppCredentials, AppEvent, RawWebhookRequest

__all__ = [
    "IAppEventVerifier",
    "REPLAY_TOLERANCE_S",
    "VerifierRegistry",
    "assert_within_replay_window",
    "get_verifier_registry",
]

logger = logging.getLogger(__name__)

# Matches the tolerance Slack itself documents; applied to every provider that
# gives us a trustworthy timestamp so one provider is not laxer than another.
REPLAY_TOLERANCE_S = 300


class VerificationError(Exception):
    """Raised by verifiers when the request signature or format is invalid."""


def assert_within_replay_window(
    epoch_seconds: float | None,
    *,
    provider: str,
    tolerance_s: int = REPLAY_TOLERANCE_S,
) -> None:
    """Reject a body a valid signature was captured from and replayed later.

    A signature stays valid forever, so without a freshness bound a single
    captured request can be resent indefinitely -- each replay re-firing every
    trigger subscribed to it. Dedupe alone is not enough: its key expires.

    A provider that sends no usable timestamp passes `None`; those providers
    rely on delivery-id dedupe instead.
    """
    if epoch_seconds is None:
        return
    if abs(time.time() - epoch_seconds) > tolerance_s:
        raise VerificationError(
            f"{provider} timestamp outside the {tolerance_s}s replay window",
        )


class IAppEventVerifier(Protocol):
    """Strategy for provider-specific webhook verification and normalization."""
    async def verify(self, req: "RawWebhookRequest", cfg: "AppCredentials") -> "AppEvent": ...


class VerifierRegistry:
    """Maps source_app → IAppEventVerifier."""

    def __init__(self) -> None:
        self._verifiers: dict[str, IAppEventVerifier] = {}

    def register(self, source_app: str, verifier: IAppEventVerifier) -> None:
        self._verifiers[source_app] = verifier

    def get(self, source_app: str) -> IAppEventVerifier | None:
        return self._verifiers.get(source_app)

    @property
    def supported_apps(self) -> list[str]:
        return list(self._verifiers.keys())


_registry = VerifierRegistry()


def get_verifier_registry() -> VerifierRegistry:
    return _registry
