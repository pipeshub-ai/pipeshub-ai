"""Coding engines' sandbox floor (``policy/release.json`` ``engineIsolation``).

The release floor is gVisor: on a production install whose Docker daemon has no ``runsc``, an engine run is refused
with ``gvisor_unavailable`` and an admin's sentence naming the install guide, never run on plain runc. A dev or test
install may lower the floor with the one documented override (``devOverride.env`` set to ``true``, off by default);
the deployment reads it (``harness_engines.release``), never a layer below. PipesHub's own ``run_code`` sandboxes
are not engine runs and keep their own floor.
"""

from __future__ import annotations

from typing import Final

from harness_core.contracts.generated.policy_data import RELEASE_DEFAULTS
from harness_core.engines.types import isolation_rank
from harness_core.sandbox.errors import IsolationUnavailable
from harness_core.sandbox.types import RuntimeMode

_ISOLATION = RELEASE_DEFAULTS.engine_isolation
OVERRIDE_ENV: Final = _ISOLATION.dev_override.env
INSTALL_GUIDE: Final = _ISOLATION.install_guide
RELEASE_FLOOR: Final = RuntimeMode(RELEASE_DEFAULTS.engine_profile.isolation_min)
OVERRIDE_FLOOR: Final = RuntimeMode(_ISOLATION.dev_override.isolation_min)
GVISOR_UNAVAILABLE: Final = "gvisor_unavailable"
MODE_UNAVAILABLE: Final = "mode_unavailable"
GVISOR_REQUIRED_MESSAGE: Final = (
    f"Coding engines need the gVisor sandbox runtime on this server — install guide: {INSTALL_GUIDE}"
)
WEAKER_ISOLATION_MESSAGE: Final = "Coding engines run without gVisor — weaker isolation"


def override_on(value: str | None) -> bool:
    """Whether the override variable's value turns it on: exactly ``true`` (any case), so a typo stays production."""
    return value is not None and value.strip().lower() == "true"


def release_floor(*, override: bool) -> RuntimeMode:
    """The floor every release engine profile carries: gVisor, or the override's on a dev or test install."""
    return OVERRIDE_FLOOR if override else RELEASE_FLOOR


def isolation_refusal(floor: RuntimeMode) -> IsolationUnavailable:
    """Why no offered runtime meets ``floor``: at gVisor's rank, the admin's words with the install guide."""
    if isolation_rank(floor) == isolation_rank(RuntimeMode.GVISOR):
        return IsolationUnavailable(GVISOR_REQUIRED_MESSAGE, reason=GVISOR_UNAVAILABLE)
    return IsolationUnavailable(f"no runtime at or above {floor}", reason=MODE_UNAVAILABLE)


__all__ = [
    "GVISOR_REQUIRED_MESSAGE",
    "GVISOR_UNAVAILABLE",
    "INSTALL_GUIDE",
    "MODE_UNAVAILABLE",
    "OVERRIDE_ENV",
    "OVERRIDE_FLOOR",
    "RELEASE_FLOOR",
    "WEAKER_ISOLATION_MESSAGE",
    "isolation_refusal",
    "override_on",
    "release_floor",
]
