"""The supply chain's extension points (ADR-SEC-01 D2, D3, D5), read through ``harness_edition().port``.

- ``TrustRootsProvider``: the keys each artefact kind accepts attestations from. This build's default holds the
  release's keys alone, so a skill is trusted only first party or by an admin's approval of its digest.
- ``ImageTrustProvider``: the sandbox broker's image check. This build's default checks the release's engine images
  once engine signatures are required, and nothing else.

Both are abstract classes, so an edition's port is checked to implement its kind when the edition is built.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.supply_chain.images import ImageTrust
    from harness_core.supply_chain.trust import TrustRoots


class TrustRootsProvider(ABC):
    @abstractmethod
    def roots(self, env: Mapping[str, str]) -> TrustRoots:
        """The roots per kind, read with the process's settings ``env``. Never raises: a setting it cannot use adds
        no key (signed content is then untrusted, and approvals still hold)."""


class ImageTrustProvider(ABC):
    @abstractmethod
    def build(
        self,
        release_images: frozenset[str],
        base_images: frozenset[str],
        env: Mapping[str, str],
    ) -> ImageTrust | None:
        """The broker's image check, or None when nothing asks for one. ``base_images``: the PipesHub images whose
        layers a check may compare an image against. Raises ``ValueError`` on a setting that is set and unusable,
        which stops the broker at start."""


__all__ = ["ImageTrustProvider", "TrustRootsProvider"]
