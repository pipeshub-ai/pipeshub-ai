"""The deployment layer a process reads from its environment, once, when it starts (02-lld §3.1).

The operator's flow rule (SEC1): ``HARNESS_FLOW_GUARD`` with ``HARNESS_FLOW_GUARD_LEVEL`` and
``HARNESS_FLOW_GUARD_ABOVE``. Node reads the same three into the layer it pins from
(``contracts/harness/vectors/flow-settings.json`` holds the two to one answer); a worker meets a pinned run's rules
with its own, so a worker the operator made stricter than Node stays stricter.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast, get_args

from app.utils.env_flags import HARNESS_FLOW_GUARD, env_flag
from harness_core.effects.flow import FlowLevel, FlowRule
from harness_core.effects.policy import Threshold

if TYPE_CHECKING:
    from harness_core.logs import HarnessLog

FLOW_LEVEL_ENV: Final = "HARNESS_FLOW_GUARD_LEVEL"
FLOW_ABOVE_ENV: Final = "HARNESS_FLOW_GUARD_ABOVE"


def _setting(name: str, value: str | None, allowed: tuple[str, ...], strictest: str, log: HarnessLog) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return allowed[0]
    if raw in allowed:
        return raw
    log.warning("approvals.flow_setting_invalid", reasonCode="flow_setting_invalid", setting=name)
    return strictest


def flow_rule_of(*, enabled: bool, level: str | None, above: str | None, log: HarnessLog) -> FlowRule | None:
    """The flow rule the three settings say, or ``None`` while the guard is off.

    An unreadable level asks; an unreadable threshold is the lowest (fail closed, never wider)."""
    if not enabled:
        return None
    return FlowRule(
        cast("FlowLevel", _setting(FLOW_LEVEL_ENV, level, get_args(FlowLevel), "ask", log)),
        cast("Threshold", _setting(FLOW_ABOVE_ENV, above, get_args(Threshold), "public", log)),
    )


def flow_guard_enabled() -> bool:
    return env_flag(HARNESS_FLOW_GUARD)


def deployment_flow_rule(log: HarnessLog) -> FlowRule | None:
    """The deployment's flow rule as this process's environment says it now."""
    return flow_rule_of(
        enabled=flow_guard_enabled(), level=os.getenv(FLOW_LEVEL_ENV), above=os.getenv(FLOW_ABOVE_ENV), log=log
    )


@dataclass(frozen=True, slots=True)
class DeploymentLayer:
    """What this process's operator set, as a pinned run is narrowed by it."""

    flow_rule: FlowRule | None = None

    @classmethod
    def from_environment(cls, log: HarnessLog) -> DeploymentLayer:
        return cls(flow_rule=deployment_flow_rule(log))


__all__ = ["FLOW_ABOVE_ENV", "FLOW_LEVEL_ENV", "DeploymentLayer", "deployment_flow_rule", "flow_guard_enabled", "flow_rule_of"]
