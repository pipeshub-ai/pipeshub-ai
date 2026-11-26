"""Whether the MCP hub is on (``MCP_HUB``, F3 matrix) and its settings.

Set, ``MCP_HUB`` decides for every surface. Unset (D-MCP-1, as narrowed), the hub is on only for
the agent harness's own runs, and only where the harness is (``AGENT_HARNESS_WORKER_ENABLED``): a durable run's
admission pins, its assembly and the MCP calls its tools make (the run's ``AgentContext.harness_run``, carried to
the URL guard and discovery by ``harness_surface``). Main's classic agent chat, its builder and its agent save keep
today's behaviour unless an operator sets ``MCP_HUB``.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.utils.env_flags import (
    AGENT_HARNESS_WORKER_ENABLED,
    MCP_HUB,
    env_flag,
    env_flag_set,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

DEFAULT_SESSION_IDLE_TTL_S = 300
DEFAULT_SESSION_MAX_AGE_S = 3600
DEFAULT_SCHEMA_TTL_S = 600
DEFAULT_BREAKER_FAILURES = 5
DEFAULT_BREAKER_COOLDOWN_S = 30


def harness_enabled() -> bool:
    """What ``MCP_HUB`` follows when unset: whether this install runs the agent harness."""
    return env_flag(AGENT_HARNESS_WORKER_ENABLED)


_HARNESS_SURFACE: ContextVar[bool] = ContextVar("mcp_hub_harness_surface", default=False)


@contextmanager
def harness_surface(*, on: bool = True) -> Iterator[None]:
    """MCP work done inside is a harness run's (``on``), so an unset ``MCP_HUB`` reads as the harness's default."""
    token = _HARNESS_SURFACE.set(on)
    try:
        yield
    finally:
        _HARNESS_SURFACE.reset(token)


def hub_enabled(*, harness: bool | None = None) -> bool:
    """``MCP_HUB``; unset, on only for a harness surface (``harness``, else the current ``harness_surface``) of an
    install that runs the harness."""
    on_harness = _HARNESS_SURFACE.get() if harness is None else harness
    return env_flag(MCP_HUB, follows=on_harness and harness_enabled())


def hub_on_by_default() -> bool:
    """On for harness runs because the harness is, ``MCP_HUB`` unset: where an install meets the approval gate on
    upgrade. An operator who set ``MCP_HUB`` already ran approvals (or chose not to): nothing is baselined for them."""
    return hub_enabled(harness=True) and not env_flag_set(MCP_HUB)


@dataclass(frozen=True, slots=True)
class HubSettings:
    session_idle_ttl_s: int
    session_max_age_s: int
    schema_ttl_s: int
    # MX-08: failures in a row that open a server's circuit (0: no breaker), and seconds before a probe.
    breaker_failures: int = DEFAULT_BREAKER_FAILURES
    breaker_cooldown_s: int = DEFAULT_BREAKER_COOLDOWN_S


def _positive_int(name: str, value: str | None, default: int, *, zero: bool = False) -> int:
    raw = (value or "").strip()
    if not raw:
        return default
    if not raw.isdigit() or (int(raw) <= 0 and not zero):
        raise ValueError(f"{name}={raw!r} must be a positive whole number")
    return int(raw)


def hub_settings() -> HubSettings:
    """Blank reads as unset (F3 FM-F1); a bad value refuses rather than running with a guess."""
    return HubSettings(
        session_idle_ttl_s=_positive_int("MCP_HUB_SESSION_IDLE_TTL_S", os.getenv("MCP_HUB_SESSION_IDLE_TTL_S"), DEFAULT_SESSION_IDLE_TTL_S),
        session_max_age_s=_positive_int("MCP_HUB_SESSION_MAX_AGE_S", os.getenv("MCP_HUB_SESSION_MAX_AGE_S"), DEFAULT_SESSION_MAX_AGE_S),
        schema_ttl_s=_positive_int("MCP_HUB_SCHEMA_TTL_S", os.getenv("MCP_HUB_SCHEMA_TTL_S"), DEFAULT_SCHEMA_TTL_S),
        breaker_failures=_positive_int(
            "MCP_HUB_BREAKER_FAILURES", os.getenv("MCP_HUB_BREAKER_FAILURES"), DEFAULT_BREAKER_FAILURES, zero=True
        ),
        breaker_cooldown_s=_positive_int(
            "MCP_HUB_BREAKER_COOLDOWN_S", os.getenv("MCP_HUB_BREAKER_COOLDOWN_S"), DEFAULT_BREAKER_COOLDOWN_S
        ),
    )


__all__ = ["HubSettings", "harness_enabled", "harness_surface", "hub_enabled", "hub_on_by_default", "hub_settings"]
