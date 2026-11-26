"""Whether STDIO MCP servers run in runners (``MCP_STDIO_RUNNERS``, F3 matrix) and the runners' settings."""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.harness.sandbox.images import mcp_runner_image
from app.utils.env_flags import MCP_STDIO_RUNNERS, env_flag
from harness_core.sandbox.types import RuntimeMode

DEFAULT_IDLE_TTL_S = 900
DEFAULT_MAX_AGE_S = 6 * 3600
DEFAULT_MAX_PER_ORG = 10


def runners_enabled() -> bool:
    return env_flag(MCP_STDIO_RUNNERS)


@dataclass(frozen=True, slots=True)
class RunnerSettings:
    image: str
    isolation_floor: RuntimeMode
    idle_ttl_s: int
    max_age_s: int
    # Runners one org keeps in a process's pool (R36COVER-FU-4), `MCP_RUNNER_MAX_PER_ORG`.
    max_per_org: int = DEFAULT_MAX_PER_ORG


def _positive_int(name: str, value: str | None, default: int) -> int:
    raw = (value or "").strip()
    if not raw:
        return default
    if not raw.isdigit() or int(raw) <= 0:
        raise ValueError(f"{name}={raw!r} must be a positive number of seconds")
    return int(raw)


def runner_settings() -> RunnerSettings:
    """Blank reads as unset (F3 FM-F1); a bad value refuses rather than running with a guess."""
    floor = (os.getenv("MCP_RUNNER_ISOLATION") or "").strip() or RuntimeMode.DOCKER_HARDENED.value
    try:
        isolation_floor = RuntimeMode(floor)
    except ValueError as exc:
        raise ValueError(f"MCP_RUNNER_ISOLATION={floor!r} is not a runtime mode") from exc
    return RunnerSettings(
        image=mcp_runner_image(),
        isolation_floor=isolation_floor,
        idle_ttl_s=_positive_int("MCP_RUNNER_IDLE_TTL_S", os.getenv("MCP_RUNNER_IDLE_TTL_S"), DEFAULT_IDLE_TTL_S),
        max_age_s=_positive_int("MCP_RUNNER_MAX_AGE_S", os.getenv("MCP_RUNNER_MAX_AGE_S"), DEFAULT_MAX_AGE_S),
        max_per_org=_positive_int("MCP_RUNNER_MAX_PER_ORG", os.getenv("MCP_RUNNER_MAX_PER_ORG"), DEFAULT_MAX_PER_ORG),
    )


__all__ = ["RunnerSettings", "runner_settings", "runners_enabled"]
