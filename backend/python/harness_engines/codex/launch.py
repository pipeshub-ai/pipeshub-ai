"""``render_launch`` for Codex: a pure function from a profile to argv, env and ``config.toml``.

Codex 0.160 reads its settings from ``$CODEX_HOME/config.toml``; the file is
rendered here from the profile and the gateway endpoints and written into the
engine-state mount, never into the workspace. Every network and side-effect
path the pinned binary has is switched off (F2 §3.2, §10; E8 "What the pinned
binary speaks"): the model sees the shell (build mode only), the MCP resource
readers Codex adds for any MCP server, sub-agent tools when the profile allows
them, and the tool gateway's tools.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from harness_core.access.types import ENGINE_MODEL_ID, ModelApi
from harness_core.engines.native_policy import HOOK_TIMEOUT_S, policy_hook_command
from harness_core.engines.policy import NativeTool
from harness_core.engines.types import EngineLaunch, EngineMode
from harness_engines.pipeshub import AGENTS_POINTER, POLICY_HOOK_PATH

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.types import EngineProfile, GatewayEndpoints, JsonValue

BINARY_PATH = "/usr/local/bin/codex-app-server"
PROVIDER = "gateway"
MODEL = ENGINE_MODEL_ID
MCP_SERVER = "tools"
# ``clientInfo.name``: Codex sends it as the ``originator`` header and the user-agent prefix.
CLIENT_NAME = "harness"
CODEX_HOME = "/state/codex"
CONFIG_PATH = f"{CODEX_HOME}/config.toml"
SESSIONS_DIR = f"{CODEX_HOME}/sessions"
# Global instructions Codex reads from its home: rendered empty and read-only, so nothing can be planted (SEC1).
INSTRUCTION_PATHS = (f"{CODEX_HOME}/AGENTS.md", f"{CODEX_HOME}/AGENTS.override.md")
WORKDIR = "/workspace"
TOKEN_ENV = "HARNESS_GATEWAY_TOKEN"
TOOLS_TOKEN_ENV = "HARNESS_TOOLS_TOKEN"
SKILLS_ENV = "HARNESS_SKILLS_DIR"
# Each retry of a model call is charged; the run's budget bounds them (E3b open item). Codex treats a refused
# stream (the gateway's 402 budget stop included) as a dropped one and re-sends it stream_max_retries times
# (verified on 0.160.0), so streams are not retried; a request answered 429 or 5xx is, once.
REQUEST_MAX_RETRIES = 1
STREAM_MAX_RETRIES = 0
MCP_STARTUP_TIMEOUT_S = 30
# E2E3-F6: Codex 0.160 waits about a second for an MCP server that is not ``required``, then sends the turn's first
# model request without that server's tools, whatever ``startup_timeout_sec`` says (verified on the pinned binary: a
# tool gateway answering ``initialize`` after 3 s left every gateway tool out of the request). A worker under load
# answers later than that, and the model got no PipesHub tool for the whole turn. Required, ``thread/start`` waits
# for the gateway up to the startup timeout and fails, loudly, if it never comes up.
MCP_REQUIRED = True
# Codex's own sub-agent depth is rendered as 1 whatever the profile asks: depth 1 is what CF-11 verifies.
SUB_AGENT_DEPTH = 1
# Codex runs commands itself; PipesHub's sandbox is the boundary, and Codex's bwrap is neither in the
# standalone binary nor possible without user namespaces (F2 §7).
SANDBOX_MODE = "danger-full-access"
APPROVAL_POLICY = "never"
VENDOR_MARKERS = ("gpt-", "claude", "gemini", "openai")

BASE_PROMPT = (
    "You are a coding engine. Work in the current folder, use the tools you are given, "
    "and finish with the result of the task. " + AGENTS_POINTER
)

# Features 0.160 enables by default that reach a vendor backend, act outside the workspace, or add tools
# the run did not ask for (codex-rs/features/src/lib.rs).
FEATURES_OFF: tuple[str, ...] = (
    "apps",
    "plugins",
    "tool_suggest",
    "remote_plugin",
    "plugin_sharing",
    "image_generation",
    "goals",
    "memories",
    "browser_use",
    "computer_use",
    "shell_snapshot",
)

# Codex's own image viewer (FIXPDFSKILL-FU-1): how it looks at the pages ``check_layout.py`` renders. It reads any path
# the process can (it runs under ``danger-full-access``, so Codex's own file sandbox confines nothing), so it is offered
# only with the managed policy hook, whose check keeps it to files under the workspace (``harness_core.engines.
# native_policy``). Codex itself sends only a file that decodes as an image, never other bytes.
IMAGE_TOOL = "view_image"

# The names the model sees, after the model gateway flattens a namespace to ``<namespace>__<tool>`` (E3b).
_SUB_AGENT_NAMESPACE = "multi_agent_v1"
NATIVE_TOOLS: Mapping[NativeTool, tuple[str, ...]] = {
    NativeTool.SHELL: ("exec_command", "write_stdin"),
    # Added for any MCP server; they read only from the tool gateway, which serves no resources.
    NativeTool.READ: ("list_mcp_resources", "list_mcp_resource_templates", "read_mcp_resource", IMAGE_TOOL),
    NativeTool.SEARCH: (),
    NativeTool.EDIT: (),
    NativeTool.SKILL: (),
    NativeTool.SUBAGENT: tuple(
        f"{_SUB_AGENT_NAMESPACE}__{name}"
        for name in ("spawn_agent", "send_input", "wait_agent", "close_agent", "resume_agent")
    ),
}

# The engine's own task list, the run's plan (UXR-B1): it changes nothing outside the session, so every mode has it.
# 0.160 offers it only when ``[tools.update_plan] enabled = true`` (FIX-P2 C3); Codex reports each call as
# ``turn/plan/updated``.
PLAN_TOOL = "update_plan"

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")

# The managed policy hook (G-25). Codex 0.160 reports its shell to hooks as ``Bash`` and its image viewer by its own
# name; a matcher of names joined by ``|`` matches exactly one of them. It runs a hook from its home's
# config.toml only when that file also trusts the hook by its hash (``hooks.state."<key>".trusted_hash``): an untrusted
# one is skipped without a word (verified on 0.160.0). The config is root's and read-only, so the agent can neither
# trust a hook of its own nor withdraw this one.
POLICY_HOOK_MATCHER = f"Bash|{IMAGE_TOOL}"
# ``<source path>:<event>:<matcher group>:<handler>``, as ``hooks/list`` keys it.
POLICY_HOOK_KEY = f"{CONFIG_PATH}:pre_tool_use:0:0"
# The hash Codex 0.160.0 gives each hook definition PipesHub renders (``hooks/list`` currentHash), by command. The
# docker lane checks it on the pinned binary; a hook without one is never rendered, and the adapter refuses to start a
# Codex that did not load the hook as trusted (``CodexAdapter.open``).
TRUSTED_HOOKS: Mapping[str, str] = {
    policy_hook_command(POLICY_HOOK_PATH): "sha256:ec3a3c94d3a256c8f7897f3cda999e079a78f4d1892df0a3a4cb73bb6ea2dae8",
}


def exposed_tool_name(granted: str) -> str:
    """The name the model sees for a gateway tool: Codex's namespace ``mcp__<server>`` flattened by E3b."""
    return f"mcp__{MCP_SERVER}__{granted}"


def thread_settings(instructions: str) -> dict[str, JsonValue]:
    """``thread/start`` and ``thread/resume`` settings; the task's instructions are developer instructions."""
    settings: dict[str, JsonValue] = {
        "cwd": WORKDIR,
        "model": MODEL,
        "modelProvider": PROVIDER,
        "approvalPolicy": APPROVAL_POLICY,
        "sandbox": SANDBOX_MODE,
        "baseInstructions": BASE_PROMPT,
    }
    if instructions:
        settings["developerInstructions"] = instructions
    return settings


def render_hooks(policy_hook: str) -> dict[str, JsonValue]:
    """``[hooks]``: the managed ``PreToolUse`` hook and its trust (``TRUSTED_HOOKS``)."""
    command = policy_hook_command(policy_hook)
    trusted = TRUSTED_HOOKS.get(command)
    if trusted is None:
        raise ValueError(f"no trusted hash is pinned for the policy hook {policy_hook} on Codex: it would never run")
    handler: dict[str, JsonValue] = {"type": "command", "command": command, "timeout": HOOK_TIMEOUT_S}
    return {
        "PreToolUse": [{"matcher": POLICY_HOOK_MATCHER, "hooks": [handler]}],
        "state": {POLICY_HOOK_KEY: {"trusted_hash": trusted}},
    }


def render_config(profile: EngineProfile, endpoints: GatewayEndpoints) -> dict[str, JsonValue]:
    sub_agents = profile.sub_agents
    features: dict[str, JsonValue] = dict.fromkeys(FEATURES_OFF, False)
    features["shell_tool"] = profile.mode is EngineMode.BUILD
    features["multi_agent"] = sub_agents.enabled
    # Hooks run only when PipesHub renders its own; off otherwise, so no other hook source is ever read.
    features["hooks"] = endpoints.policy_hook is not None
    features[IMAGE_TOOL] = endpoints.policy_hook is not None
    config: dict[str, JsonValue] = {
        "model": MODEL,
        "model_provider": PROVIDER,
        "web_search": "disabled",
        "approval_policy": APPROVAL_POLICY,
        "sandbox_mode": SANDBOX_MODE,
        "project_doc_max_bytes": 0,
        # CF-02: a thread started with danger-full-access trusts its folder, and Codex then loads the repository's
        # own .codex/config.toml (instructions, model, MCP servers, hooks) over this file; untrusted, it loads none.
        "projects": {WORKDIR: {"trust_level": "untrusted"}},
        "check_for_update_on_startup": False,
        "include_permissions_instructions": False,
        "include_environment_context": False,
    }
    if profile.reasoning_effort is not None:
        config["model_reasoning_effort"] = profile.reasoning_effort
    config |= {
        "model_providers": {
            PROVIDER: {
                "name": "Gateway",
                "base_url": endpoints.model_base_url,
                "wire_api": "responses",
                "env_key": TOKEN_ENV,
                "request_max_retries": REQUEST_MAX_RETRIES,
                "stream_max_retries": STREAM_MAX_RETRIES,
            }
        },
        "mcp_servers": {
            MCP_SERVER: {
                "url": endpoints.tools_mcp_url,
                "bearer_token_env_var": TOOLS_TOKEN_ENV,
                "tool_timeout_sec": endpoints.tool_timeout_s,
                "startup_timeout_sec": MCP_STARTUP_TIMEOUT_S,
                "required": MCP_REQUIRED,
            }
        },
        "tools": {"experimental_request_user_input": {"enabled": False}, PLAN_TOOL: {"enabled": True}},
        # ADR-ENG-01 D1's exception: 0.160.0 also lists a repository's .agents/skills and .codex/skills (scope repo)
        # whatever the project's trust, and disables a skill only by name or path, so they are lower-trust content.
        "skills": {"include_instructions": True, "bundled": {"enabled": False}},
        "analytics": {"enabled": False},
        "feedback": {"enabled": False},
        "otel": {"metrics_exporter": "none"},
        "features": features,
        "agents": {"max_depth": SUB_AGENT_DEPTH, "max_concurrent_threads_per_session": sub_agents.max_concurrent},
    }
    if endpoints.policy_hook is not None:
        config["hooks"] = render_hooks(endpoints.policy_hook)
    return config


def renders_policy_hook(launch: EngineLaunch) -> bool:
    """Whether the launch's config holds the managed policy hook, which ``open`` then requires Codex to have loaded."""
    return POLICY_HOOK_KEY.encode() in launch.files.get(CONFIG_PATH, b"")


_TOML_ESCAPES = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


def toml_string(value: str) -> str:
    """A TOML basic string. Not ``json.dumps``: its surrogate-pair escapes for non-BMP characters are invalid TOML."""
    escaped = (
        _TOML_ESCAPES.get(ch) or (f"\\u{ord(ch):04x}" if ord(ch) < 0x20 or ord(ch) == 0x7F else ch) for ch in value
    )
    return '"' + "".join(escaped) + '"'


def _key(name: str) -> str:
    return name if _BARE_KEY.match(name) else toml_string(name)


def _scalar(value: JsonValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        return toml_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(_scalar(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_scalar(v)}" for k, v in value.items()) + " }"
    raise TypeError(f"no TOML rendering for {type(value).__name__}")


def render_toml(document: Mapping[str, JsonValue]) -> str:
    """TOML for nested tables of strings, booleans, numbers and arrays (of those or of inline tables), all
    ``config.toml`` uses."""
    lines: list[str] = []

    def table(path: tuple[str, ...], values: Mapping[str, JsonValue]) -> None:
        scalars = [(k, v) for k, v in values.items() if not isinstance(v, dict)]
        tables = [(k, v) for k, v in values.items() if isinstance(v, dict)]
        if path and scalars:
            lines.append(f"[{'.'.join(_key(p) for p in path)}]")
        lines.extend(f"{_key(k)} = {_scalar(v)}" for k, v in scalars)
        if scalars:
            lines.append("")
        for name, sub in tables:
            table((*path, name), sub)

    table((), document)
    return "\n".join(lines)


def render_launch(profile: EngineProfile, endpoints: GatewayEndpoints, skills_dir: str) -> EngineLaunch:
    if profile.engine.kind != "codex":
        raise ValueError(f"Codex cannot launch {profile.engine.kind}")
    if endpoints.model_api is not ModelApi.RESPONSES:
        raise ValueError(f"Codex speaks only the Responses API, not {endpoints.model_api}")
    env = {
        "CODEX_HOME": CODEX_HOME,
        "HOME": "/tmp",
        TOKEN_ENV: endpoints.token_placeholder,
        TOOLS_TOKEN_ENV: endpoints.tools_placeholder,
        SKILLS_ENV: skills_dir,
    }
    return EngineLaunch(
        engine=profile.engine,
        # A key the pinned binary does not know stops it instead of being ignored (verified on 0.160.0).
        argv=(BINARY_PATH, "--strict-config", "--listen", "stdio://"),
        env=env,
        files={CONFIG_PATH: render_toml(render_config(profile, endpoints)).encode(), **dict.fromkeys(INSTRUCTION_PATHS, b"")},
        workdir=WORKDIR,
        port=None,
    )


__all__ = [
    "BASE_PROMPT",
    "BINARY_PATH",
    "CLIENT_NAME",
    "CODEX_HOME",
    "CONFIG_PATH",
    "FEATURES_OFF",
    "IMAGE_TOOL",
    "MCP_SERVER",
    "MODEL",
    "NATIVE_TOOLS",
    "PLAN_TOOL",
    "POLICY_HOOK_KEY",
    "POLICY_HOOK_MATCHER",
    "PROVIDER",
    "INSTRUCTION_PATHS",
    "SESSIONS_DIR",
    "SKILLS_ENV",
    "TOKEN_ENV",
    "TOOLS_TOKEN_ENV",
    "TRUSTED_HOOKS",
    "exposed_tool_name",
    "render_config",
    "render_hooks",
    "render_launch",
    "render_toml",
    "renders_policy_hook",
    "thread_settings",
    "toml_string",
]
