"""The live limit's environment variables, parsed.

A malformed or out-of-range value falls back to its default (or is clamped)
with one warning, the same as the capacity variables in ``capacity.py``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.agent_loop_lib.sandbox.capacity import _float_in, _int_or_none, _warn_once

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["DEFAULT_MEMORY_OVERCOMMIT", "LiveSettings", "SidecarSource"]

ENV_LIVE_LIMIT = "SANDBOX_LIVE_LIMIT"
ENV_LIVE_INTERVAL_S = "SANDBOX_LIVE_INTERVAL_S"
ENV_HOST_MEM_FLOOR_MB = "SANDBOX_HOST_MEM_FLOOR_MB"
ENV_HOST_MEM_SOFT_MB = "SANDBOX_HOST_MEM_SOFT_MB"
ENV_HOST_CPU_BRAKE = "SANDBOX_HOST_CPU_BRAKE"
ENV_HOST_CPU_PSI_BRAKE = "SANDBOX_HOST_CPU_PSI_BRAKE"
ENV_MEMORY_OVERCOMMIT = "SANDBOX_MEMORY_OVERCOMMIT"
ENV_SIDECAR_SOURCE = "SANDBOX_SIDECAR_SOURCE"
ENV_SIDECAR_MEM_HIGH = "SANDBOX_SIDECAR_MEM_HIGH"
ENV_SIDECAR_MEM_HARD = "SANDBOX_SIDECAR_MEM_HARD"
ENV_SIDECAR_CPU_BRAKE = "SANDBOX_SIDECAR_CPU_BRAKE"
ENV_SIDECAR_CPU_THROTTLE = "SANDBOX_SIDECAR_CPU_THROTTLE"
ENV_YIELD_INDEXING = "SANDBOX_YIELD_INDEXING"
ENV_YIELD_TTL_S = "SANDBOX_YIELD_TTL_S"

DEFAULT_LIVE_INTERVAL_S = 15.0
DEFAULT_HOST_CPU_BRAKE = 0.90
DEFAULT_HOST_CPU_PSI_BRAKE = 0.75
# Each run is counted at its full memory limit unless this is raised; the
# live reading is what catches a burst of heavy runs when it is.
DEFAULT_MEMORY_OVERCOMMIT = 1.0
MAX_MEMORY_OVERCOMMIT = 4.0
# The Docker-in-Docker sidecar only holds the sandboxes, so its thresholds sit
# at the edge of its reserve: they hold runs back only when it is nearly full.
DEFAULT_SIDECAR_MEM_HIGH = 0.90
DEFAULT_SIDECAR_MEM_HARD = 0.95
DEFAULT_SIDECAR_CPU_BRAKE = 0.95
DEFAULT_SIDECAR_CPU_THROTTLE = 0.25
# How long one refresh of the indexing back-off signal lasts.
DEFAULT_YIELD_TTL_S = 20.0
_MIN_MEM_MB = 256

SidecarSource = Literal["auto", "cgroup", "containers"]
_MAX_MEM_MB = 262144


def _mb(env: Mapping[str, str], key: str) -> int | None:
    value = _int_or_none(env, key)
    if value is None:
        return None
    clamped = min(max(value, _MIN_MEM_MB), _MAX_MEM_MB)
    if clamped != value:
        _warn_once(key, str(value), "%s=%s is outside %d..%d; using %d", key, value, _MIN_MEM_MB, _MAX_MEM_MB, clamped)
    return clamped


def _choice(env: Mapping[str, str], key: str, allowed: tuple[str, ...]) -> str:
    raw = (env.get(key) or "").strip().lower()
    if not raw:
        return allowed[0]
    if raw not in allowed:
        _warn_once(key, raw, "Ignoring %s=%r: use one of %s; using %s", key, raw, "/".join(allowed), allowed[0])
        return allowed[0]
    return raw


@dataclass(frozen=True)
class LiveSettings:
    enabled: bool = True
    interval_s: float = DEFAULT_LIVE_INTERVAL_S
    host_mem_floor_mb: int | None = None
    host_mem_soft_mb: int | None = None
    host_cpu_brake: float = DEFAULT_HOST_CPU_BRAKE
    host_cpu_psi_brake: float = DEFAULT_HOST_CPU_PSI_BRAKE
    memory_overcommit: float = DEFAULT_MEMORY_OVERCOMMIT
    sidecar_source: SidecarSource = "auto"
    sidecar_mem_high: float = DEFAULT_SIDECAR_MEM_HIGH
    sidecar_mem_hard: float = DEFAULT_SIDECAR_MEM_HARD
    sidecar_cpu_brake: float = DEFAULT_SIDECAR_CPU_BRAKE
    sidecar_cpu_throttle: float = DEFAULT_SIDECAR_CPU_THROTTLE
    yield_indexing: bool = True
    yield_ttl_s: float = DEFAULT_YIELD_TTL_S

    @property
    def host_tuned(self) -> bool:
        """Whether any of the Docker host's thresholds was set."""
        return (
            self.host_mem_floor_mb is not None or self.host_mem_soft_mb is not None
            or self.host_cpu_brake != DEFAULT_HOST_CPU_BRAKE
            or self.host_cpu_psi_brake != DEFAULT_HOST_CPU_PSI_BRAKE
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LiveSettings:
        env = os.environ if env is None else env
        high = _float_in(env, ENV_SIDECAR_MEM_HIGH, DEFAULT_SIDECAR_MEM_HIGH, 0.50, 0.97)
        hard = _float_in(env, ENV_SIDECAR_MEM_HARD, DEFAULT_SIDECAR_MEM_HARD, 0.51, 0.99)
        # The tolerance keeps a gap of exactly 0.01 valid despite float rounding (0.93 + 0.01 > 0.94).
        if hard < high + 0.01 - 1e-9:
            _warn_once(
                ENV_SIDECAR_MEM_HARD, f"{high}/{hard}",
                "%s=%g must be at least %s + 0.01 (%g); using %g and %g", ENV_SIDECAR_MEM_HARD, hard,
                ENV_SIDECAR_MEM_HIGH, high, DEFAULT_SIDECAR_MEM_HIGH, DEFAULT_SIDECAR_MEM_HARD,
            )
            high, hard = DEFAULT_SIDECAR_MEM_HIGH, DEFAULT_SIDECAR_MEM_HARD
        return cls(
            enabled=_choice(env, ENV_LIVE_LIMIT, ("auto", "off")) == "auto",
            interval_s=_float_in(env, ENV_LIVE_INTERVAL_S, DEFAULT_LIVE_INTERVAL_S, 5.0, 60.0),
            host_mem_floor_mb=_mb(env, ENV_HOST_MEM_FLOOR_MB),
            host_mem_soft_mb=_mb(env, ENV_HOST_MEM_SOFT_MB),
            host_cpu_brake=_float_in(env, ENV_HOST_CPU_BRAKE, DEFAULT_HOST_CPU_BRAKE, 0.10, 1.0),
            host_cpu_psi_brake=_float_in(env, ENV_HOST_CPU_PSI_BRAKE, DEFAULT_HOST_CPU_PSI_BRAKE, 0.05, 1.0),
            memory_overcommit=_float_in(
                env, ENV_MEMORY_OVERCOMMIT, DEFAULT_MEMORY_OVERCOMMIT, 1.0, MAX_MEMORY_OVERCOMMIT,
            ),
            sidecar_source=_choice(env, ENV_SIDECAR_SOURCE, ("auto", "cgroup", "containers")),  # type: ignore[arg-type]
            sidecar_mem_high=high,
            sidecar_mem_hard=hard,
            sidecar_cpu_brake=_float_in(env, ENV_SIDECAR_CPU_BRAKE, DEFAULT_SIDECAR_CPU_BRAKE, 0.50, 1.0),
            sidecar_cpu_throttle=_float_in(
                env, ENV_SIDECAR_CPU_THROTTLE, DEFAULT_SIDECAR_CPU_THROTTLE, 0.01, 1.0,
            ),
            yield_indexing=_choice(env, ENV_YIELD_INDEXING, ("on", "off")) == "on",
            yield_ttl_s=_float_in(env, ENV_YIELD_TTL_S, DEFAULT_YIELD_TTL_S, 10.0, 120.0),
        )

