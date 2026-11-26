"""``render_launch`` for opencode: a pure function from a profile to argv, env and config.

Everything opencode needs comes from ``OPENCODE_CONFIG_CONTENT`` and env flags;
no config file is written into the workspace (F4 §11.5). The flags and config
switch off every default network and side-effect path F4 §1.4 measured, and
the permission rules deny every tool except what the profile allows, which
removes the rest from the model's tool list (F4 §4).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from harness_core.access.types import ENGINE_MODEL_ID, ROLE_MODEL_IDS
from harness_core.budget.run_budget import native_steps
from harness_core.contracts.generated.enums import REASONING_EFFORTS
from harness_core.contracts.generated.policy_data import RELEASE_DEFAULTS
from harness_core.engines.policy import NativeTool, expected_native_tools
from harness_core.engines.types import EngineLaunch, JsonValue
from harness_engines.pipeshub import AGENTS_POINTER

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.types import EngineProfile, GatewayEndpoints

BINARY_PATH = "/usr/local/bin/opencode"
HOST = "127.0.0.1"
PORT = 4096
PROVIDER = "gateway"
MODEL = ENGINE_MODEL_ID
FAST_MODEL = ROLE_MODEL_IDS["fast"]
SUBAGENT_MODEL = ROLE_MODEL_IDS["subagent"]
MCP_SERVER = "tools"
AGENT = "harness"
SUBAGENT = "helper"
TOKEN_ENV = "HARNESS_GATEWAY_TOKEN"
TOOLS_TOKEN_ENV = "HARNESS_TOOLS_TOKEN"
SKILLS_ENV = "HARNESS_SKILLS_DIR"
CONFIG_HOME = "/opt/oc-xdg-config"
DATA_HOME = "/state"
CACHE_HOME = "/tmp/oc-cache"
WORKDIR = "/workspace"
# The image's own list of what it ships (E0b: written by the tools image build). opencode adds an
# existing instructions file to the system prompt and skips a missing one, so images without it are fine.
RUNTIME_NOTE = "/usr/share/pxh-engine/RUNTIME.md"
_RELEASE_STEPS = RELEASE_DEFAULTS.engine_profile.limits.max_steps
if _RELEASE_STEPS is None:
    raise ValueError("policy/release.json must bound an engine run's steps: opencode needs a number")
# opencode needs a step bound; a profile without one gets the release's.
DEFAULT_STEPS: int = _RELEASE_STEPS.root
CONTEXT_TOKENS = 200_000
OUTPUT_TOKENS = 8192
# Model ids containing these switch opencode's tools and stock prompts (F4 §3).
VENDOR_MARKERS = ("gpt-", "claude", "gemini")

BASE_PROMPT = (
    "You are a coding engine. Work in the current folder, use the tools you are given, "
    "and finish with the result of the task. " + AGENTS_POINTER
)

BASH_TIMEOUT_ENV = "OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS"

# F4 env.locked.sh, minus the server password: open() adds a fresh one per sandbox.
LOCKED_ENV: Mapping[str, str] = {
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_SHARE": "1",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
    "OPENCODE_PURE": "1",
    "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE": "1",
    "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
    "OPENCODE_DISABLE_EMBEDDED_WEB_UI": "1",
    "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
    # Any client other than cli/app/desktop removes the ask-the-user tool.
    "OPENCODE_CLIENT": "harness",
    # A read-only dir with a pre-seeded .gitignore stops opencode's npm install (F4 §1.4).
    "XDG_CONFIG_HOME": CONFIG_HOME,
    "XDG_DATA_HOME": DATA_HOME,
    "XDG_CACHE_HOME": CACHE_HOME,
    "XDG_STATE_HOME": "/tmp/oc-state",
}

NATIVE_TOOLS: Mapping[NativeTool, tuple[str, ...]] = {
    NativeTool.READ: ("read",),
    NativeTool.SEARCH: ("glob", "grep"),
    NativeTool.EDIT: ("edit", "write"),
    NativeTool.SHELL: ("bash",),
    NativeTool.SKILL: ("skill",),
    NativeTool.SUBAGENT: ("task",),
}
# The engine's own task list, the run's plan (UXR-B1): it changes nothing outside the session, so every mode has it.
PLAN_TOOL = "todowrite"
# Permission keys: edit covers write and apply_patch.
_PERMISSION_KEYS: Mapping[NativeTool, tuple[str, ...]] = {
    **NATIVE_TOOLS,
    NativeTool.EDIT: ("edit",),
}


# The folder `ph` and the run's tool index live in (``harness_engines.pipeshub.FOLDER``).
PIPESHUB_DIR = "/pipeshub"


def exposed_tool_name(granted: str) -> str:
    """The name the model sees for a gateway tool: ``<server>_<tool>``."""
    return f"{MCP_SERVER}_{granted}"


def _permission(profile: EngineProfile, skills_dir: str) -> dict[str, JsonValue]:
    rules: dict[str, JsonValue] = {
        "*": "deny",
        f"{MCP_SERVER}_*": "allow",
        PLAN_TOOL: "allow",
        # "*": deny overrides opencode's own allowances: for its spill files of long tool output,
        # and for skill folders, whose resources the model reads with `read` (found on the real binary).
        "external_directory": {
            "*": "deny",
            f"{DATA_HOME}/opencode/tool-output/*": "allow",
            "/tmp/opencode/*": "allow",
            f"{skills_dir.rstrip('/')}/*": "allow",
            # R3: the run's tool index and `ph`'s docs (`harness_engines.pipeshub`), read-only to the engine.
            f"{PIPESHUB_DIR}/*": "allow",
        },
    }
    for tool in sorted(expected_native_tools(profile, skills=True)):
        if tool is NativeTool.SKILL:
            rules["skill"] = {"*": "allow", "customize-opencode": "deny"}
        elif tool is NativeTool.SUBAGENT:
            rules["task"] = {"*": "deny", SUBAGENT: "allow"}
        else:
            for key in _PERMISSION_KEYS[tool]:
                rules[key] = "allow"
    return rules


# opencode's sub-agent depth defaults to 1 and its concurrency to one task call at a
# time; neither is rendered (the depth key is unverified), so deeper or wider
# profiles are narrowed to that, never widened.
def _agents(profile: EngineProfile) -> dict[str, JsonValue]:
    steps = native_steps(profile.limits.max_steps, DEFAULT_STEPS)
    primary: dict[str, JsonValue] = {
        "mode": "primary",
        "description": "Engine run agent",
        "prompt": BASE_PROMPT,
        "steps": steps,
    }
    if profile.reasoning_effort is not None:
        primary["variant"] = profile.reasoning_effort
    agents: dict[str, JsonValue] = {
        AGENT: primary,
        "build": {"disable": True},
        "plan": {"disable": True},
        "general": {"disable": True},
        "explore": {"disable": True},
        "title": {"disable": True},
    }
    if profile.sub_agents.enabled:
        agents[SUBAGENT] = {
            "mode": "subagent",
            "description": "Helper for a bounded part of the task",
            "prompt": BASE_PROMPT,
            "steps": steps,
            "model": f"{PROVIDER}/{SUBAGENT_MODEL}",
            # Found on the real binary: the config's `task` allowance reached the helper too, so it could
            # spawn helpers of its own. Depth 1 is the only depth rendered (see above).
            "permission": {"task": "deny"},
        }
    return agents


def render_config(
    profile: EngineProfile, endpoints: GatewayEndpoints, skills_dir: str = "/skills"
) -> dict[str, JsonValue]:
    variants: dict[str, JsonValue] = {
        effort: {"reasoningEffort": effort} for effort in REASONING_EFFORTS
    }
    return {
        "autoupdate": False,
        "share": "disabled",
        "snapshot": False,
        "formatter": False,
        "lsp": False,
        "enabled_providers": [PROVIDER],
        "provider": {
            PROVIDER: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Gateway",
                "options": {"baseURL": endpoints.model_base_url, "apiKey": "{env:" + TOKEN_ENV + "}"},
                # GA-15: one model per role an opencode run calls; the gateway maps each id to its role's route.
                "models": {
                    model_id: {
                        "name": name,
                        "tool_call": True,
                        "limit": {"context": CONTEXT_TOKENS, "output": OUTPUT_TOKENS},
                        "variants": variants,
                    }
                    for model_id, name in (
                        (MODEL, "Engine model"),
                        (FAST_MODEL, "Engine fast model"),
                        (SUBAGENT_MODEL, "Engine helper model"),
                    )
                },
            }
        },
        "model": f"{PROVIDER}/{MODEL}",
        "small_model": f"{PROVIDER}/{FAST_MODEL}",
        "default_agent": AGENT,
        "instructions": [RUNTIME_NOTE],
        "agent": _agents(profile),
        "permission": _permission(profile, skills_dir),
        "skills": {"paths": ["{env:" + SKILLS_ENV + "}"]},
        "mcp": {
            MCP_SERVER: {
                "type": "remote",
                "url": endpoints.tools_mcp_url,
                "headers": {"Authorization": "Bearer {env:" + TOOLS_TOKEN_ENV + "}"},
                "oauth": False,
                "timeout": endpoints.tool_timeout_s * 1000,
            }
        },
    }


def render_launch(
    profile: EngineProfile, endpoints: GatewayEndpoints, skills_dir: str
) -> EngineLaunch:
    if profile.engine.kind != "opencode":
        raise ValueError(f"opencode cannot launch {profile.engine.kind}")
    config = render_config(profile, endpoints, skills_dir)
    env = {
        **LOCKED_ENV,
        TOKEN_ENV: endpoints.token_placeholder,
        TOOLS_TOKEN_ENV: endpoints.tools_placeholder,
        SKILLS_ENV: skills_dir,
        # ``ph`` runs in the shell tool, and a call it makes may be held for approval up to the gateway's hold (120 s,
        # opencode's own shell default): a shell killed first loses the held answer and the call is never made again.
        BASH_TIMEOUT_ENV: str(endpoints.tool_timeout_s * 1000),
        # Key order is kept: opencode's permission rules are last-match-wins in declaration order.
        "OPENCODE_CONFIG_CONTENT": json.dumps(config, separators=(",", ":")),
    }
    return EngineLaunch(
        engine=profile.engine,
        argv=(BINARY_PATH, "serve", "--hostname", HOST, "--port", str(PORT)),
        env=env,
        workdir=WORKDIR,
        port=PORT,
    )


__all__ = [
    "AGENT",
    "BASE_PROMPT",
    "BINARY_PATH",
    "LOCKED_ENV",
    "MCP_SERVER",
    "MODEL",
    "NATIVE_TOOLS",
    "PORT",
    "RUNTIME_NOTE",
    "SUBAGENT",
    "TOKEN_ENV",
    "TOOLS_TOKEN_ENV",
    "exposed_tool_name",
    "render_config",
    "render_launch",
]
