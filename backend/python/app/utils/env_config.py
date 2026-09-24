"""Environment-variable readers shared by the services' tuning knobs.

Deployment-time constants only — per-installation settings live in
``ConfigurationService``. Two hazards these exist to handle:

* The shipped Compose files pass every optional knob as ``${VAR:-}``, so an
  unset one arrives as the empty string. A bare ``int(os.getenv(name))``
  raises on that at import and takes the whole service down.
* ``float()`` accepts ``nan``/``inf``, and both survive to ``int(x * 1000)``
  in the lease calls where they raise — indistinguishable from a Redis
  failure to the caller. A negative duration is quieter and worse: it makes
  the retry backoff return a negative delay, and ``asyncio.sleep`` of that
  returns immediately, spinning against the service it is backing off from.

A malformed value falls back to the default and is logged once, rather than
silently substituting: an operator who typo'd a knob otherwise has no way to
discover the override never took.
"""
from __future__ import annotations

import logging
import math
import os

_logger = logging.getLogger(__name__)


def _warn(name: str, raw: str, default: object) -> None:
    _logger.warning(
        "Ignoring malformed %s=%r; falling back to %r", name, raw, default
    )


def env_int(name: str, default: int) -> int:
    """Read an int env var, treating empty/malformed as unset."""
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        _warn(name, raw, default)
        return default


def env_seconds(name: str, default: float) -> float:
    """Read a duration env var. Non-finite and negative values are rejected."""
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        _warn(name, raw, default)
        return default
    if not math.isfinite(value) or value < 0:
        _warn(name, raw, default)
        return default
    return value


def env_float(name: str, default: float, *, low: float, high: float) -> float:
    """Read a float env var and clamp it to ``[low, high]``.

    The clamp keeps a typo'd override from pushing a control loop into a
    degenerate always-grow or always-shrink state. NaN is rejected *before*
    clamping: every comparison against NaN is False, so ``max(low, min(high,
    nan))`` silently returns ``high`` — a typo would pin the knob to its
    maximum rather than fall back here.
    """
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        _warn(name, raw, default)
        return default
    if not math.isfinite(value):
        _warn(name, raw, default)
        return default
    return max(low, min(high, value))
