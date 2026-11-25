"""The release's trust roots (ADR-SEC-01 D2): the key that signs release data (OWD-8) also vouches for the release's
engine images, and nothing a deployment configures can add a key for either kind."""

from __future__ import annotations

from harness_core.supply_chain.trust import RELEASE_KINDS, TrustRoots
from harness_engines.release_keys import trusted_release_keys


def release_trust_roots() -> TrustRoots:
    """The release keys in force now: a revoked key or one past its not-after vouches for no engine image or release
    data (R41SUPPLY-FU-6)."""
    keys = trusted_release_keys()
    return TrustRoots({kind: dict(keys) for kind in RELEASE_KINDS})


__all__ = ["release_trust_roots"]
