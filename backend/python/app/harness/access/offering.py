"""Which offering this install is (A1): the vendor rules allow some routes only in some offerings.

PipesHub has no licence key or edition marker to read it from (the edition is a
build-time module swap that names no edition), so it is a deployment flag in the
F3 matrix. Unset or blank is ``managed``, the offering the rules restrict most:
a forgotten flag can only refuse a route, never allow one. Node reads the same
flag (``application/model-access-service.ts``).
"""

from __future__ import annotations

import os
from typing import Final

from harness_core.access.vendor_rules import OFFERINGS, Offering
from harness_core.contracts.generated.policy_data import RELEASE_DEFAULTS

AGENT_HARNESS_OFFERING: Final = "AGENT_HARNESS_OFFERING"
DEFAULT_OFFERING: Final[Offering] = RELEASE_DEFAULTS.models.offering


class InvalidOffering(ValueError):
    reason_code = "invalid_offering"

    def __init__(self, value: str) -> None:
        super().__init__(f"{AGENT_HARNESS_OFFERING}={value!r} is not one of {', '.join(OFFERINGS)}")


def parse_offering(raw: str | None) -> Offering:
    value = (raw or "").strip().lower()
    if not value:
        return DEFAULT_OFFERING
    if value not in OFFERINGS:
        raise InvalidOffering(raw or "")
    return value


def harness_offering() -> Offering:
    """The install's offering now; raises ``InvalidOffering`` (the role refuses to start)."""
    return parse_offering(os.getenv(AGENT_HARNESS_OFFERING))


__all__ = ["AGENT_HARNESS_OFFERING", "DEFAULT_OFFERING", "InvalidOffering", "harness_offering", "parse_offering"]
