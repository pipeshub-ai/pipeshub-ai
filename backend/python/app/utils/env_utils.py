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


def env_bool(name: str, default: bool) -> bool:
    """True iff the env var `name` is `"true"` (case-insensitive).

    Absent -> `default`. Any non-`"true"` non-empty value -> False. This is
    the dominant pattern in the codebase:

        os.getenv("PIPESHUB_*", "true").strip().lower() == "true"
    """
    return os.getenv(name, str(default)).strip().lower() == "true"


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
