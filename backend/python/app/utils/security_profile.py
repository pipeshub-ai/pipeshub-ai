"""The security profile (ADR-OPS-07): ``PIPESHUB_HARDENED`` decides whether this install runs the hardened defaults
the security programme added or keeps the behaviour of a release without them (compatibility).

Unset reads compatibility, so an upgraded install, a plain ``docker compose up`` with an older ``.env`` and a
from-source setup behave as before; ``install.sh`` writes ``true`` on a fresh install. The switch itself never stops a
process: a value that is not a boolean reads compatibility with a warning. Stdlib only, so the Docker API proxy can
read it without the app's dependencies. Node's twin is ``backend/nodejs/apps/src/libs/utils/security-profile.ts``.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

HARDENED_ENV = "PIPESHUB_HARDENED"

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})
_LOG = logging.getLogger(__name__)
_warned: set[str] = set()


def warn_once(message: str) -> None:
    """One warning per message per process: a compatibility fallback is logged once, not per request."""
    if message in _warned:
        return
    _warned.add(message)
    _LOG.warning(message)


def reset_compat_warnings_for_tests() -> None:
    _warned.clear()


def hardened_profile(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    raw = (source.get(HARDENED_ENV) or "").strip().lower()
    if not raw or raw in _FALSE:
        return False
    if raw in _TRUE:
        return True
    warn_once(
        f"{HARDENED_ENV}={source.get(HARDENED_ENV)!r} is not a boolean: running the compatibility profile. "
        "Set it to true to adopt the hardened profile."
    )
    return False


__all__ = ["HARDENED_ENV", "hardened_profile", "reset_compat_warnings_for_tests", "warn_once"]
