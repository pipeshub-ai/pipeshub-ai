"""``render_launch`` for Claude Code: a pure function from a profile to argv, env and three files.

Claude Code 2.1.289 runs headless as ``claude -p`` with stream-json on both
stdio streams. Its settings come from the command line, the environment and
the files rendered here into the engine-state mount (never the workspace,
whose ``.claude/`` is stripped on materialize): one MCP server (the tool
gateway), a settings file, and the sub-agent definition when the profile
allows sub-agents. User, project and local settings files are not read.

Vendor conditions (ARCHITECTURE §4.6.4, F10 §1.2): the binary is run as
published and PipesHub removes no login method; the gateway credential is
configured as ``ANTHROPIC_AUTH_TOKEN``, which Claude Code documents for LLM
gateways. Every extra that reaches a vendor backend, acts outside the
workspace or adds tools the run did not ask for is switched off (E9 "What the
pinned binary speaks").
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from harness_core.access.types import ENGINE_MODEL_ID, ROLE_MODEL_IDS, ModelApi
from harness_core.engines.native_policy import HOOK_TIMEOUT_S, policy_hook_command
from harness_core.engines.policy import NativeTool, expected_native_tools
from harness_core.engines.types import EngineLaunch
from harness_engines.claude_code.mapper import TODO_TOOL
from harness_engines.pipeshub import AGENTS_POINTER

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.types import EngineProfile, GatewayEndpoints, JsonValue

BINARY_PATH = "/usr/local/bin/claude"
MODEL = ENGINE_MODEL_ID
MCP_SERVER = "tools"
WORKDIR = "/workspace"
CONFIG_DIR = "/state/claude"
MCP_CONFIG_PATH = f"{CONFIG_DIR}/harness-mcp.json"
SETTINGS_PATH = f"{CONFIG_DIR}/harness-settings.json"
AGENTS_PATH = f"{CONFIG_DIR}/harness-agents.json"
# The user scope ``--setting-sources user`` reads: rendered empty and read-only, so nothing can be planted there (SEC1).
USER_SETTINGS_PATH = f"{CONFIG_DIR}/settings.json"
# The user memory (off through the environment too): empty and read-only for the same reason.
USER_MEMORY_PATH = f"{CONFIG_DIR}/CLAUDE.md"
# Claude Code reads user-scope skills from here; the adapter links it to the run's skills folder.
SKILLS_LINK = f"{CONFIG_DIR}/skills"
# The transcript of a session: ``projects/<slug of the cwd>/<session id>.jsonl`` (verified on 2.1.289).
PROJECTS_DIR = f"{CONFIG_DIR}/projects"
TRANSCRIPT_DIR = f"{PROJECTS_DIR}/{WORKDIR.replace('/', '-')}"
TOOLS_TOKEN_ENV = "HARNESS_TOOLS_TOKEN"
SKILLS_ENV = "HARNESS_SKILLS_DIR"
# The one sub-agent the run may spawn; the scripted conformance model asks for it by this name.
SUB_AGENT = "helper"
# dontAsk: anything not pre-approved is denied without asking anyone. Never ``auto`` (it sends a vendor-side
# safeguards classifier request and two betas the gateway refuses, E3c FB-4) or ``bypassPermissions``.
PERMISSION_MODE = "dontAsk"
VENDOR_MARKERS = ("gpt-", "claude-", "gemini", "openai")

# The names the model sees for each native tool (the ``--tools`` names; the model gateway forwards them as is).
NATIVE_TOOLS: Mapping[NativeTool, tuple[str, ...]] = {
    NativeTool.READ: ("Read",),
    NativeTool.SEARCH: ("Grep", "Glob"),
    NativeTool.EDIT: ("Edit", "Write"),
    NativeTool.SHELL: ("Bash",),
    NativeTool.SKILL: ("Skill",),
    NativeTool.SUBAGENT: ("Agent",),
}
# The native tools the managed policy hook checks before each call (G-25): what changes files, runs commands or reaches
# out. Reads stay in the sandbox and run in every mode; the gateway's own tools are checked by the gateway.
POLICY_HOOK_TOOLS = ("Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch")
# Built-in plugins 2.1.289 enables by default; each adds skills or instructions the run did not ask for.
BUILTIN_PLUGINS_OFF = ("cc-plugin-agents-md@builtin", "cc-plugin-plugin-authoring@builtin")

BASE_PROMPT = (
    "You are a coding engine. Work in the current folder, use the tools you are given, "
    "and finish with the result of the task. " + AGENTS_POINTER
)

# Fixed environment: extras off. Login commands and auth methods are left as published (vendor condition).
FIXED_ENV: Mapping[str, str] = {
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "DISABLE_AUTOUPDATER": "1",
    "DISABLE_UPDATES": "1",
    "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1",
    "CLAUDE_CODE_DISABLE_POLICY_SKILLS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS": "1",
    "CLAUDE_CODE_DISABLE_WEB_FETCH": "1",
    # Background sub-agents end the turn early and start another one when they finish (verified):
    # the turn must include its helpers' work.
    "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
    # Deferred tools would hide granted tools behind a search tool; the grant is the tool list.
    "ENABLE_TOOL_SEARCH": "false",
    # 2.1's file-backed task lists (TaskCreate…) hide TodoWrite; the run's plan is the session's TodoWrite (UXR-B1).
    "CLAUDE_CODE_ENABLE_TASKS": "false",
}


def system_prompt(instructions: str) -> str:
    """Appended to Claude Code's own system prompt by the session's ``initialize`` request, which replaces any
    ``--append-system-prompt``: the task's instructions are known only at the first turn."""
    return f"{BASE_PROMPT}\n\n{instructions}" if instructions else BASE_PROMPT


def gateway_root(model_base_url: str) -> str:
    """``ANTHROPIC_BASE_URL``: Claude Code appends ``/v1/messages`` itself, so the endpoint's ``/v1`` goes."""
    root = model_base_url.rstrip("/")
    if not root.endswith("/v1"):
        raise ValueError("the model endpoint must end in /v1 for Claude Code")
    return root[: -len("/v1")]


def exposed_tool_name(granted: str) -> str:
    """The name the model sees for a gateway tool."""
    return f"mcp__{MCP_SERVER}__{granted}"


def native_tools(profile: EngineProfile, *, skills: bool) -> tuple[str, ...]:
    allowed = expected_native_tools(profile, skills=skills)
    return tuple(name for tool, names in NATIVE_TOOLS.items() if tool in allowed for name in names)


def render_mcp_config(endpoints: GatewayEndpoints) -> dict[str, JsonValue]:
    # Claude Code expands ${VAR} from its environment: the placeholder stays out of the file.
    server: dict[str, JsonValue] = {
        "type": "http",
        "url": endpoints.tools_mcp_url,
        "headers": {"Authorization": f"Bearer ${{{TOOLS_TOKEN_ENV}}}"},
    }
    return {"mcpServers": {MCP_SERVER: server}}


def render_hooks(policy_hook: str) -> dict[str, JsonValue]:
    """The managed ``PreToolUse`` hook: the run's policy asked through the Tool Gateway before each native call the
    hook covers (G-25). It lives in the ``--settings`` file, root's and read-only like every file Claude Code obeys;
    the user scope is rendered empty and read-only and no other scope is read, so nothing can add, change or switch off
    a hook."""
    handler: dict[str, JsonValue] = {"type": "command", "command": policy_hook_command(policy_hook), "timeout": HOOK_TIMEOUT_S}
    return {"PreToolUse": [{"matcher": "|".join(POLICY_HOOK_TOOLS), "hooks": [handler]}]}


def render_settings(policy_hook: str | None = None) -> dict[str, JsonValue]:
    settings: dict[str, JsonValue] = {
        "enabledPlugins": dict.fromkeys(BUILTIN_PLUGINS_OFF, False),
        "attribution": {"commit": "", "pr": ""},
    }
    if policy_hook is not None:
        settings["hooks"] = render_hooks(policy_hook)
        settings["disableAllHooks"] = False
    return settings


def render_agents(profile: EngineProfile) -> dict[str, JsonValue]:
    """The one sub-agent, without the sub-agent tool: depth 1 whatever the profile asks (CF-11 verifies 1)."""
    child = tuple(name for name in native_tools(profile, skills=False) if name not in NATIVE_TOOLS[NativeTool.SUBAGENT])
    return {
        SUB_AGENT: {
            "description": "A helper for a self-contained sub-task.",
            "prompt": BASE_PROMPT,
            "tools": [*child, f"mcp__{MCP_SERVER}"],
        }
    }


def _env(profile: EngineProfile, endpoints: GatewayEndpoints, skills_dir: str) -> dict[str, str]:
    env = {
        "CLAUDE_CONFIG_DIR": CONFIG_DIR,
        "HOME": "/tmp",
        "ANTHROPIC_BASE_URL": gateway_root(endpoints.model_base_url),
        "ANTHROPIC_AUTH_TOKEN": endpoints.token_placeholder,
        TOOLS_TOKEN_ENV: endpoints.tools_placeholder,
        SKILLS_ENV: skills_dir,
        **FIXED_ENV,
        # Approvals are held up to APPROVAL_HOLD_S in the tool gateway: the engine must wait longer.
        "MCP_TOOL_TIMEOUT": str(endpoints.tool_timeout_s * 1000),
        "MCP_TIMEOUT": "30000",
    }
    # GA-15: each of Claude Code's model settings names its role's neutral id; the gateway maps it to the role's
    # route. Its compaction runs on the main model, so it has no setting of its own.
    env["ANTHROPIC_MODEL"] = MODEL
    env["ANTHROPIC_SMALL_FAST_MODEL"] = ROLE_MODEL_IDS["fast"]
    env["CLAUDE_CODE_SUBAGENT_MODEL"] = ROLE_MODEL_IDS["subagent"]
    env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = ROLE_MODEL_IDS["fast"]
    for tier in ("SONNET", "OPUS", "FABLE"):
        env[f"ANTHROPIC_DEFAULT_{tier}_MODEL"] = MODEL
    if profile.sub_agents.enabled:
        env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] = "1"
        env["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] = str(profile.sub_agents.max_concurrent)
    return env


def _argv(profile: EngineProfile, tools: tuple[str, ...]) -> tuple[str, ...]:
    allowed = (*tools, f"mcp__{MCP_SERVER}")
    argv = [
        BINARY_PATH,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        PERMISSION_MODE,
        "--setting-sources",
        "user",
        "--settings",
        SETTINGS_PATH,
        "--strict-mcp-config",
        "--mcp-config",
        MCP_CONFIG_PATH,
        "--tools",
        ",".join(tools),
        "--allowedTools",
        ",".join(allowed),
        "--model",
        MODEL,
    ]
    if profile.sub_agents.enabled:
        argv += ["--agents", AGENTS_PATH]
    if profile.reasoning_effort is not None:
        argv += ["--effort", profile.reasoning_effort]
    return tuple(argv)


def render_launch(profile: EngineProfile, endpoints: GatewayEndpoints, skills_dir: str) -> EngineLaunch:
    if profile.engine.kind != "claude_code":
        raise ValueError(f"Claude Code cannot launch {profile.engine.kind}")
    if endpoints.model_api is not ModelApi.ANTHROPIC_MESSAGES:
        raise ValueError(f"Claude Code speaks only the Anthropic Messages API, not {endpoints.model_api}")
    # The engine's own task list, the run's plan (UXR-B1): it changes nothing outside the session, so every mode has it.
    tools = (*native_tools(profile, skills=bool(skills_dir)), TODO_TOOL)
    files = {
        MCP_CONFIG_PATH: _json(render_mcp_config(endpoints)),
        SETTINGS_PATH: _json(render_settings(endpoints.policy_hook)),
        USER_SETTINGS_PATH: _json({}),
        USER_MEMORY_PATH: b"",
    }
    if profile.sub_agents.enabled:
        files[AGENTS_PATH] = _json(render_agents(profile))
    return EngineLaunch(
        engine=profile.engine,
        argv=_argv(profile, tools),
        env=_env(profile, endpoints, skills_dir),
        files=files,
        workdir=WORKDIR,
        port=None,
    )


def _json(document: Mapping[str, JsonValue]) -> bytes:
    return json.dumps(document, sort_keys=True, indent=1).encode()


__all__ = [
    "AGENTS_PATH",
    "BASE_PROMPT",
    "BINARY_PATH",
    "BUILTIN_PLUGINS_OFF",
    "CONFIG_DIR",
    "FIXED_ENV",
    "MCP_CONFIG_PATH",
    "MCP_SERVER",
    "MODEL",
    "NATIVE_TOOLS",
    "PERMISSION_MODE",
    "POLICY_HOOK_TOOLS",
    "PROJECTS_DIR",
    "SETTINGS_PATH",
    "SKILLS_ENV",
    "SKILLS_LINK",
    "SUB_AGENT",
    "TOOLS_TOKEN_ENV",
    "TRANSCRIPT_DIR",
    "USER_MEMORY_PATH",
    "USER_SETTINGS_PATH",
    "VENDOR_MARKERS",
    "exposed_tool_name",
    "gateway_root",
    "native_tools",
    "render_agents",
    "render_hooks",
    "render_launch",
    "render_mcp_config",
    "render_settings",
    "system_prompt",
]
