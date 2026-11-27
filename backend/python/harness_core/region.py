"""The region a process runs in (OWD-12): a worker claims only runs recorded in its region (it names it on every
claim), and a sandbox broker creates a sandbox only for a caller in its own region (the region rides on the
sandbox's labels, so the container keeps it).

Which region a process is in is an extension point, ``RegionPort``, read through ``harness_edition().port`` (see
``app.harness.placement``). This build's default runs every process in one region, ``default``. Node's
``domain/region.ts`` is the twin: the same default and name rule.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

# The region of a process no extension point places: one region for the whole deployment.
UNSET_REGION: Final = "default"
REGION_LABEL: Final = "pipeshub.region"
_REGION: Final = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


def is_region(value: object) -> bool:
    return isinstance(value, str) and _REGION.fullmatch(value) is not None


class RegionPort(ABC):
    """Which region this process runs in."""

    @abstractmethod
    def region(self, env: Mapping[str, str]) -> str:
        """The region, read with the process's settings ``env``. May raise ``ValueError`` on a setting that is set
        and unusable, which stops the process rather than guess."""


class SingleRegion(RegionPort):
    """Every process in one region, ``default``."""

    def region(self, env: Mapping[str, str]) -> str:
        return UNSET_REGION


SINGLE_REGION: Final = SingleRegion()


__all__ = ["REGION_LABEL", "SINGLE_REGION", "UNSET_REGION", "RegionPort", "SingleRegion", "is_region"]
