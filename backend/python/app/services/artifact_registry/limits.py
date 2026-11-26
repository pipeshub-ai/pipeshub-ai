"""How large a file the harness takes as a run's input, and so how large an artifact the registry keeps (J-26).

The deployment sets it (``HARNESS_INPUT_MAX_MB``); the registry's cap is the same, so any artifact it holds fits as
an input. An org's ``sandbox.inputMaxMb`` lowers it for its runs, never raises it.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

INPUT_MAX_ENV: Final = "HARNESS_INPUT_MAX_MB"
# A long scanned PDF or a data export; inputs over 8 MiB reach the sandbox only when the engine reads them.
DEFAULT_INPUT_MAX_MB: Final = 100
_MIB: Final = 1024 * 1024


class InvalidInputMax(ValueError):
    reason_code = "invalid_input_max"


def input_max_bytes(env: Mapping[str, str] | None = None) -> int:
    """``HARNESS_INPUT_MAX_MB`` in bytes: unset or blank is 100 MiB; anything but a positive whole number refuses,
    rather than silently take the default the operator meant to change."""
    raw = ((os.environ if env is None else env).get(INPUT_MAX_ENV) or "").strip()
    if not raw:
        return DEFAULT_INPUT_MAX_MB * _MIB
    if not raw.isdigit() or int(raw) <= 0:
        raise InvalidInputMax(f"{INPUT_MAX_ENV}={raw!r} is not a positive whole number of MiB")
    return int(raw) * _MIB


def effective_input_max_bytes(deployment_max_bytes: int, pinned_max_mb: int | None) -> int:
    """The deployment's cap, lowered by the run pin's ``sandbox.inputMaxMb`` (None: not set)."""
    if pinned_max_mb is None:
        return deployment_max_bytes
    return min(deployment_max_bytes, pinned_max_mb * _MIB)


__all__ = [
    "DEFAULT_INPUT_MAX_MB",
    "INPUT_MAX_ENV",
    "InvalidInputMax",
    "effective_input_max_bytes",
    "input_max_bytes",
]
