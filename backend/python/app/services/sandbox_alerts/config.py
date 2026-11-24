"""``SANDBOX_ALERT*`` settings, read once when the alert loop starts."""

from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

ENV_ENABLED = "SANDBOX_ALERTS"
ENV_WINDOW_S = "SANDBOX_ALERT_WINDOW_S"
ENV_EVAL_S = "SANDBOX_ALERT_EVAL_S"
ENV_WARN_BUSY_PCT = "SANDBOX_ALERT_WARN_BUSY_PCT"
ENV_WARN_MIN_WAITED = "SANDBOX_ALERT_WARN_MIN_WAITED"
ENV_CRIT_MIN_REFUSED = "SANDBOX_ALERT_CRIT_MIN_REFUSED"
ENV_CRIT_REFUSED_PCT = "SANDBOX_ALERT_CRIT_REFUSED_PCT"
ENV_COOLDOWN_S = "SANDBOX_ALERT_COOLDOWN_S"
ENV_SCOPE = "SANDBOX_ALERT_SCOPE"

DEFAULT_SCOPE = "install"
_SCOPE_RE = re.compile(r"[A-Za-z0-9._-]{1,63}")
# The scope is a key path segment: these would point at another level of it.
_SCOPE_RESERVED = frozenset({".", ".."})
_FALSY = {"false", "off", "0", "no"}
_TRUTHY = {"true", "on", "1", "yes"}
# Below this the cooldown no longer limits alerts to about one a day.
_COOLDOWN_TESTING_BELOW_S = 3600


def _number(env: Mapping[str, str], key: str, default: float, low: float, high: float | None) -> float:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        logger.warning("Ignoring %s=%r: not a number; using %s", key, raw, _fmt(default))
        return default
    clamped = max(low, value if high is None else min(high, value))
    if clamped != value:
        logger.warning("%s=%r is out of range; using %s", key, raw, _fmt(clamped))
    return clamped


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


@dataclass(frozen=True)
class AlertSettings:
    enabled: bool = True
    window_s: int = 1800
    eval_s: int = 60
    warn_busy_pct: float = 60.0
    warn_min_waited: int = 30
    crit_min_refused: int = 10
    crit_refused_pct: float = 5.0
    cooldown_s: int = 86400
    scope: str = DEFAULT_SCOPE

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AlertSettings:
        """Blank means the default; out-of-range values are clamped with one
        warning naming the variable and the value used."""
        env = os.environ if env is None else env
        raw_enabled = (env.get(ENV_ENABLED) or "").strip().lower()
        enabled = True
        if raw_enabled in _FALSY:
            enabled = False
        elif raw_enabled and raw_enabled not in _TRUTHY:
            logger.warning("Ignoring %s=%r: use true or false; alerts stay on", ENV_ENABLED, raw_enabled)
        cooldown_s = int(_number(env, ENV_COOLDOWN_S, 86400, 60, 604800))
        if cooldown_s < _COOLDOWN_TESTING_BELOW_S:
            logger.warning(
                "%s=%d: alerts can repeat every %d s; use values below 3600 for testing only",
                ENV_COOLDOWN_S, cooldown_s, cooldown_s,
            )
        scope = (env.get(ENV_SCOPE) or "").strip()
        if not scope:
            scope = DEFAULT_SCOPE
        elif not _SCOPE_RE.fullmatch(scope) or scope in _SCOPE_RESERVED:
            logger.warning(
                "Ignoring %s=%r: use 1-63 letters, digits, '.', '_' or '-' (not '.' or '..'); using %r",
                ENV_SCOPE, scope, DEFAULT_SCOPE,
            )
            scope = DEFAULT_SCOPE
        return cls(
            enabled=enabled,
            window_s=int(_number(env, ENV_WINDOW_S, 1800, 120, 3600)),
            eval_s=int(_number(env, ENV_EVAL_S, 60, 10, 600)),
            warn_busy_pct=_number(env, ENV_WARN_BUSY_PCT, 60, 1, 100),
            warn_min_waited=int(_number(env, ENV_WARN_MIN_WAITED, 30, 1, None)),
            crit_min_refused=int(_number(env, ENV_CRIT_MIN_REFUSED, 10, 1, None)),
            crit_refused_pct=_number(env, ENV_CRIT_REFUSED_PCT, 5, 0, 100),
            cooldown_s=cooldown_s,
            scope=scope,
        )
