"""The trust roots this process checks against (ADR-SEC-01 D2), read by the query service (skills) and the sandbox
broker (images) alike.

They come from the edition's ``TrustRootsProvider``; this build's default holds the release's keys alone, so a skill
is trusted first party or by an admin's approval of its digest, and an engine image by the release key.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Final, override

from harness_core.supply_chain.ports import TrustRootsProvider
from harness_engines.trust_roots import release_trust_roots

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.supply_chain.trust import TrustRoots


class ReleaseTrustRoots(TrustRootsProvider):
    """The release's roots (release data, engine images), whatever the settings say."""

    @override
    def roots(self, env: Mapping[str, str]) -> TrustRoots:
        del env
        return release_trust_roots()


RELEASE_TRUST_ROOTS: Final = ReleaseTrustRoots()


def trust_roots(env: Mapping[str, str] | None = None) -> TrustRoots:
    """The roots per artefact kind, from the edition's provider (``RELEASE_TRUST_ROOTS`` by default)."""
    from app.edition_services import harness_edition

    provider = harness_edition().port(TrustRootsProvider, RELEASE_TRUST_ROOTS)
    return provider.roots(os.environ if env is None else env)


__all__ = ["RELEASE_TRUST_ROOTS", "ReleaseTrustRoots", "trust_roots"]
