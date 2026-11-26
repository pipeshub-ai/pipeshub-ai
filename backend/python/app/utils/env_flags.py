"""The one boolean parse for the agent harness's environment flags (F3, ``contracts/harness/flags.json``).

``1/true/yes/on`` and ``0/false/no/off`` in any case; unset or blank is the flag's
default (Compose renders ``${X:-}`` as blank); anything else is refused with
``InvalidEnvFlag`` naming the flag, on a hardened install. The compatibility profile (ADR-OPS-07,
``PIPESHUB_HARDENED`` unset) reads it as a release without this parse did, with a warning. Each process that reads these flags calls
``validate_env_flags()`` at startup, so a typo stops the process instead of
silently becoming the default on the first request. Node's twin is
``backend/nodejs/apps/src/libs/utils/env-flags.ts``; the image's entrypoint has
``env_flag`` in the Dockerfile.
"""

from __future__ import annotations

import os

from app.utils.security_profile import hardened_profile, warn_once

AGENT_HARNESS_BUDGET_LEDGER = "AGENT_HARNESS_BUDGET_LEDGER"
# The operator's instance-wide incident stop; the egress proxy reads it too (C6.14).
AGENT_HARNESS_KILL_SWITCH = "AGENT_HARNESS_KILL_SWITCH"
SANDBOX_ALLOW_NETWORK = "SANDBOX_ALLOW_NETWORK"
HARNESS_ENGINE_RUNS = "HARNESS_ENGINE_RUNS"
HARNESS_ENGINE_SNAPSHOTS = "HARNESS_ENGINE_SNAPSHOTS"
MCP_STDIO_RUNNERS = "MCP_STDIO_RUNNERS"
HARNESS_CONTEXT_COMPILER = "HARNESS_CONTEXT_COMPILER"
HARNESS_AUTO_COMPACT = "HARNESS_AUTO_COMPACT"
# DEEPROUTE: a loop run that may hand off to a coding engine does so on its own for work bigger than a chat reply.
HARNESS_DEEP_ROUTING = "HARNESS_DEEP_ROUTING"
HARNESS_OTEL = "HARNESS_OTEL"
# The OpenTelemetry GenAI conventions' own opt-in for prompts, answers and tool arguments on spans.
OTEL_GENAI_CAPTURE_CONTENT = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"
MCP_HUB = "MCP_HUB"
# The agent harness's switch (ADR-OPS-06, amended 2026-11-20), on unless the operator turns it off: the durable worker,
# its gateways and every harness surface follow it (idle until an org turns on Agent harness in Labs); the MCP hub
# follows it for harness runs when MCP_HUB is unset.
AGENT_HARNESS_WORKER_ENABLED = "AGENT_HARNESS_WORKER_ENABLED"
EGRESS_CONNECT_TUNNELS = "EGRESS_CONNECT_TUNNELS"
INSTANCE_TOOL_IDS = "INSTANCE_TOOL_IDS"
HARNESS_RECONCILE = "HARNESS_RECONCILE"
HARNESS_FLOW_GUARD = "HARNESS_FLOW_GUARD"
HARNESS_PROVIDER_STATE = "HARNESS_PROVIDER_STATE"
# G-48 (WP-100): a run whose model provider is down moves to an alternate model its pin names (the loop's kernel
# and the Model Gateway for engines). It replaced HARNESS_MODEL_POLICY, which gated nothing else by then.
HARNESS_MODEL_FALLBACK = "HARNESS_MODEL_FALLBACK"
EGRESS_STATE_REQUIRED = "EGRESS_STATE_REQUIRED"
# Main's operator switches for the agent loop's code execution, skills and custom STDIO MCP servers (R2 F13).
MCP_ALLOW_CUSTOM_STDIO = "MCP_ALLOW_CUSTOM_STDIO"
PIPESHUB_ENABLE_SKILLS = "PIPESHUB_ENABLE_SKILLS"
PIPESHUB_ENABLE_CODE_EXECUTION = "PIPESHUB_ENABLE_CODE_EXECUTION"
PIPESHUB_SKILLS_AUTO_APPROVE = "PIPESHUB_SKILLS_AUTO_APPROVE"
PIPESHUB_SKILLS_WRITE_APPROVAL = "PIPESHUB_SKILLS_WRITE_APPROVAL"
PIPESHUB_SKILLS_LEARNING_ENABLED = "PIPESHUB_SKILLS_LEARNING_ENABLED"
# CPD-8: toolset and MCP credentials live in control-plane bindings (read first, written by the binder, refreshed
# by the one TokenRefresher); the old credential paths become read-only.
CONTROL_PLANE_CREDENTIALS = "CONTROL_PLANE_CREDENTIALS"

# Name -> the default when unset or blank; ``None``: it follows another setting, which the reader passes as
# ``follows`` (flags.json ``defaultFollows``). C-16 keeps this equal to flags.json.
ENV_FLAG_DEFAULTS: dict[str, bool | None] = {
    AGENT_HARNESS_BUDGET_LEDGER: True,
    AGENT_HARNESS_KILL_SWITCH: False,
    SANDBOX_ALLOW_NETWORK: False,
    HARNESS_ENGINE_RUNS: False,
    HARNESS_ENGINE_SNAPSHOTS: False,
    MCP_STDIO_RUNNERS: False,
    HARNESS_CONTEXT_COMPILER: False,
    HARNESS_AUTO_COMPACT: True,
    HARNESS_DEEP_ROUTING: True,
    HARNESS_OTEL: False,
    OTEL_GENAI_CAPTURE_CONTENT: False,
    MCP_HUB: None,
    AGENT_HARNESS_WORKER_ENABLED: True,
    CONTROL_PLANE_CREDENTIALS: False,
    EGRESS_CONNECT_TUNNELS: False,
    INSTANCE_TOOL_IDS: False,
    HARNESS_RECONCILE: False,
    HARNESS_FLOW_GUARD: True,
    HARNESS_PROVIDER_STATE: False,
    HARNESS_MODEL_FALLBACK: True,
    EGRESS_STATE_REQUIRED: None,
    MCP_ALLOW_CUSTOM_STDIO: False,
    PIPESHUB_ENABLE_SKILLS: True,
    PIPESHUB_ENABLE_CODE_EXECUTION: True,
    PIPESHUB_SKILLS_AUTO_APPROVE: False,
    PIPESHUB_SKILLS_WRITE_APPROVAL: True,
    PIPESHUB_SKILLS_LEARNING_ENABLED: False,
}

# R51-COMPAT (ADR-OPS-07): how a release without this parse read a value that is not a boolean, for the flags it
# already had. The compatibility profile reads such a value this way, with a warning, instead of refusing to start;
# a flag missing here reads its default.
LEGACY_UNRECOGNISED: dict[str, bool] = {
    # `not in ("0", "false", "no", "off")`: anything else allowed the network.
    SANDBOX_ALLOW_NETWORK: True,
    # `== "true"`: anything else was off.
    PIPESHUB_ENABLE_SKILLS: False,
    MCP_ALLOW_CUSTOM_STDIO: False,
    PIPESHUB_SKILLS_AUTO_APPROVE: False,
    PIPESHUB_SKILLS_WRITE_APPROVAL: False,
    PIPESHUB_SKILLS_LEARNING_ENABLED: False,
    # Unrecognised fell through to the default, on.
    PIPESHUB_ENABLE_CODE_EXECUTION: True,
}

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


class InvalidEnvFlag(ValueError):
    reason_code = "invalid_env_flag"

    def __init__(self, name: str, value: str) -> None:
        super().__init__(f"{name}={value!r} is not a boolean: use one of 1/true/yes/on or 0/false/no/off")
        self.name = name


def parse_env_flag(name: str, raw: str | None, *, default: bool) -> bool:
    value = (raw or "").strip().lower()
    if not value:
        return default
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    if hardened_profile():
        raise InvalidEnvFlag(name, raw or "")
    reading = LEGACY_UNRECOGNISED.get(name, default)
    warn_once(
        f"{InvalidEnvFlag(name, raw or '')}. Read as {str(reading).lower()} because PIPESHUB_HARDENED is not true "
        "(the compatibility profile); the hardened profile refuses to start on it."
    )
    return reading


def env_flag(name: str, *, follows: bool | None = None) -> bool:
    """The flag's value now; ``name`` must be one of ``ENV_FLAG_DEFAULTS``. A flag whose default follows another
    setting takes that setting's value as ``follows``."""
    default = ENV_FLAG_DEFAULTS[name]
    if default is None:
        if follows is None:
            raise TypeError(f"{name}'s default follows another setting: pass follows=")
        default = follows
    return parse_env_flag(name, os.getenv(name), default=default)


def env_flag_set(name: str) -> bool:
    """Whether the operator set ``name`` (not unset or blank): a default that follows another setting was overridden."""
    _ = ENV_FLAG_DEFAULTS[name]
    return bool((os.getenv(name) or "").strip())


def validate_env_flags() -> None:
    """Refuse to start on a malformed flag (raises ``InvalidEnvFlag``); the compatibility profile warns instead."""
    for name, default in ENV_FLAG_DEFAULTS.items():
        _ = parse_env_flag(name, os.getenv(name), default=bool(default))


__all__ = [
    "AGENT_HARNESS_BUDGET_LEDGER",
    "AGENT_HARNESS_KILL_SWITCH",
    "AGENT_HARNESS_WORKER_ENABLED",
    "CONTROL_PLANE_CREDENTIALS",
    "EGRESS_CONNECT_TUNNELS",
    "EGRESS_STATE_REQUIRED",
    "ENV_FLAG_DEFAULTS",
    "HARNESS_AUTO_COMPACT",
    "HARNESS_DEEP_ROUTING",
    "MCP_ALLOW_CUSTOM_STDIO",
    "PIPESHUB_ENABLE_CODE_EXECUTION",
    "PIPESHUB_ENABLE_SKILLS",
    "PIPESHUB_SKILLS_AUTO_APPROVE",
    "PIPESHUB_SKILLS_LEARNING_ENABLED",
    "PIPESHUB_SKILLS_WRITE_APPROVAL",
    "SANDBOX_ALLOW_NETWORK",
    "LEGACY_UNRECOGNISED",
    "InvalidEnvFlag",
    "env_flag",
    "env_flag_set",
    "parse_env_flag",
    "validate_env_flags",
]
