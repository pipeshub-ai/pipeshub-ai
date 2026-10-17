"""Environment-variable readers shared across every layer.

`app/agents/agent_loop/env_utils.py` already owned `env_bool`, but it sits in
the adapter layer -- reaching down into it from `app/utils/` would invert the
dependency direction (shared utilities must not import the agent loop). The
implementations live here; that module re-exports `env_bool` so its existing
callers are unaffected.

Both readers treat a malformed value as absent rather than raising: these run
while building a model for a live request, and refusing to answer because an
env var has a typo in it is worse than falling back to the shipped default.
"""

from __future__ import annotations

import os


def env_bool(name: str, default: bool) -> bool:  # noqa: FBT001
    """True iff the env var `name` is `"true"` (case-insensitive).

    Absent -> `default`. Any non-`"true"` non-empty value -> False. This is
    the dominant pattern in the codebase:

        os.getenv("PIPESHUB_*", "true").strip().lower() == "true"
    """
    return os.getenv(name, str(default)).strip().lower() == "true"


_OFF_VALUES = frozenset({"false", "0", "no", "off"})


def enabled_unless_off(name: str) -> bool:
    """A feature that is on by default and that a deployment can turn off.

    Unset or blank is on (Compose passes `${NAME:-}` through as ""); "false",
    "0", "no" or "off" turns it off. It does not follow `HIGH_ACCURACY_MODE`:
    the mode is for features that cost more per question, and these are on
    for everyone.
    """
    return os.getenv(name, "").strip().lower() not in _OFF_VALUES


HIGH_ACCURACY_MODE_ENV_VAR = "HIGH_ACCURACY_MODE"


def high_accuracy_mode() -> bool:
    """``HIGH_ACCURACY_MODE=true``: features that may cost more compute,
    tokens or latency per question for better answers. Off by default; each
    feature reads it through `accuracy_feature_enabled`."""
    return env_bool(HIGH_ACCURACY_MODE_ENV_VAR, False)


def accuracy_feature_enabled(name: str) -> bool:
    """Whether the high-accuracy feature switched by env var `name` is on.

    Unset or blank follows `HIGH_ACCURACY_MODE`, so one setting turns on every
    such feature; set, it decides for that feature alone (`"true"` on, any
    other value off), so one feature can be measured or turned off by itself.
    Blank counts as unset because Compose passes `${NAME:-}` through as "".
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return high_accuracy_mode()
    return raw.strip().lower() == "true"


def env_int(
    name: str,
    default: int | None = None,
    *,
    lo: int | None = None,
    hi: int | None = None,
) -> int | None:
    """Integer value of the env var `name`, clamped to `[lo, hi]`.

    Absent, blank, or non-numeric -> `default` (which may be `None`, letting
    a caller distinguish "operator said nothing" from "operator said 0").
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def uvicorn_worker_healthcheck_timeout() -> int:
    """Seconds a uvicorn worker may go without answering its supervisor before
    it is killed and replaced (`UVICORN_WORKER_HEALTHCHECK_TIMEOUT_SECONDS`).

    uvicorn's own default is 5. A worker that is still loading models at
    startup, or is busy with a large document, does not answer that fast, so
    with more than one worker the supervisor killed healthy workers in a loop
    and each restart re-ran startup recovery.
    """
    return env_int("UVICORN_WORKER_HEALTHCHECK_TIMEOUT_SECONDS", 60, lo=5) or 60


__all__ = ["env_bool", "env_int", "uvicorn_worker_healthcheck_timeout"]
