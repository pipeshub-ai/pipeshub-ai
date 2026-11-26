"""CL-1..CL-8 (E9): Claude Code's launch is a pure function of the profile and the gateway endpoints."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from harness_core.access.types import AccessRouteRef, ModelApi
from harness_core.engines.types import (
    MIN_TOOL_TIMEOUT_S,
    EngineMode,
    EngineProfile,
    EngineVersion,
    GatewayEndpoints,
    SubAgentPolicy,
)
from harness_engines.claude_code.launch import (
    AGENTS_PATH,
    BASE_PROMPT,
    BUILTIN_PLUGINS_OFF,
    CONFIG_DIR,
    MCP_CONFIG_PATH,
    MCP_SERVER,
    MODEL,
    PERMISSION_MODE,
    SETTINGS_PATH,
    SKILLS_ENV,
    SUB_AGENT,
    TOOLS_TOKEN_ENV,
    USER_MEMORY_PATH,
    USER_SETTINGS_PATH,
    VENDOR_MARKERS,
    gateway_root,
    render_launch,
    system_prompt,
)

CLAUDE = EngineVersion("claude_code", "2.1.289", "sha256:" + "a" * 64)
ENDPOINTS = GatewayEndpoints(
    model_base_url="http://model-gateway:8097/v1",
    model_api=ModelApi.ANTHROPIC_MESSAGES,
    tools_mcp_url="http://tool-gateway:8097/mcp",
    token_placeholder="pxh-model-placeholder-0123",
    tools_token_placeholder="pxh-tools-placeholder-4567",
)


def _profile(**changes: Any) -> EngineProfile:  # noqa: ANN401
    base = EngineProfile(engine=CLAUDE, mode=EngineMode.BUILD, access=AccessRouteRef("route-1", "d1"))
    return dataclasses.replace(base, **changes)


def _flag(argv: tuple[str, ...], name: str) -> str:
    return argv[argv.index(name) + 1]


def _tools(profile: EngineProfile, skills_dir: str = "/skills") -> list[str]:
    return _flag(render_launch(profile, ENDPOINTS, skills_dir).argv, "--tools").split(",")


def test_cl1_the_launch_is_pure_and_holds_placeholders_only() -> None:
    launch = render_launch(_profile(), ENDPOINTS, "/skills")
    assert launch == render_launch(_profile(), ENDPOINTS, "/skills")
    assert launch.argv[:9] == (
        "/usr/local/bin/claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
        "--permission-mode", PERMISSION_MODE,
    )
    assert PERMISSION_MODE == "dontAsk"
    assert (launch.workdir, launch.port) == ("/workspace", None)
    env = launch.env
    assert env["CLAUDE_CONFIG_DIR"] == CONFIG_DIR and CONFIG_DIR.startswith("/state/")
    assert env["HOME"] == "/tmp"
    assert env["ANTHROPIC_BASE_URL"] == "http://model-gateway:8097"
    assert env["ANTHROPIC_AUTH_TOKEN"] == ENDPOINTS.token_placeholder
    assert env[TOOLS_TOKEN_ENV] == ENDPOINTS.tools_placeholder
    assert env[SKILLS_ENV] == "/skills"
    assert "ANTHROPIC_API_KEY" not in env
    # GA-15: each model setting names its role's neutral id, never a vendor's model.
    for name in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                 "ANTHROPIC_DEFAULT_FABLE_MODEL"):
        assert env[name] == MODEL == "engine-model"
    for name in ("ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL"):
        assert env[name] == "engine-model-fast"
    assert env["CLAUDE_CODE_SUBAGENT_MODEL"] == "engine-model-subagent"
    assert _flag(launch.argv, "--model") == MODEL
    assert set(launch.files) == {MCP_CONFIG_PATH, SETTINGS_PATH, USER_SETTINGS_PATH, USER_MEMORY_PATH}
    assert launch.files[USER_MEMORY_PATH] == b""
    assert json.loads(launch.files[USER_SETTINGS_PATH]) == {}
    assert all(path.startswith(CONFIG_DIR + "/") for path in launch.files)
    text = "\n".join([*launch.argv, *launch.env.values(), *(f.decode() for f in launch.files.values())])
    assert ENDPOINTS.tools_placeholder not in b"".join(launch.files.values()).decode()
    assert "sk-" not in text


def test_cl1_the_gateway_root_drops_v1_only() -> None:
    assert gateway_root("http://model-gateway:8097/v1") == "http://model-gateway:8097"
    assert gateway_root("http://gw:1/base/v1/") == "http://gw:1/base"


def test_cl2_one_mcp_server_the_tool_gateway_with_the_placeholder_from_the_environment() -> None:
    launch = render_launch(_profile(), ENDPOINTS, "/skills")
    config = json.loads(launch.files[MCP_CONFIG_PATH])
    assert config == {
        "mcpServers": {
            MCP_SERVER: {
                "type": "http",
                "url": ENDPOINTS.tools_mcp_url,
                "headers": {"Authorization": f"Bearer ${{{TOOLS_TOKEN_ENV}}}"},
            }
        }
    }
    assert "--strict-mcp-config" in launch.argv
    assert _flag(launch.argv, "--mcp-config") == MCP_CONFIG_PATH
    assert int(launch.env["MCP_TOOL_TIMEOUT"]) >= MIN_TOOL_TIMEOUT_S * 1000


@pytest.mark.parametrize(
    ("mode", "sub_agents", "skills_dir", "expected"),
    [
        (EngineMode.BUILD, False, "/skills", ["Read", "Grep", "Glob", "Edit", "Write", "Bash", "Skill"]),
        (EngineMode.BUILD, True, "/skills", ["Read", "Grep", "Glob", "Edit", "Write", "Bash", "Skill", "Agent"]),
        (EngineMode.BUILD, False, "", ["Read", "Grep", "Glob", "Edit", "Write", "Bash"]),
        (EngineMode.PLAN, False, "/skills", ["Read", "Grep", "Glob", "Skill"]),
        (EngineMode.REVIEW, True, "/skills", ["Read", "Grep", "Glob", "Skill", "Agent"]),
    ],
)
def test_cl3_the_profile_decides_the_native_tools(
    mode: EngineMode, sub_agents: bool, skills_dir: str, expected: list[str]
) -> None:
    profile = _profile(mode=mode, sub_agents=SubAgentPolicy(enabled=sub_agents, max_depth=3, max_concurrent=2))
    launch = render_launch(profile, ENDPOINTS, skills_dir)
    # Every mode also has the engine's own task list, the run's plan (UXR-B1).
    assert _tools(profile, skills_dir) == [*expected, "TodoWrite"]
    assert _flag(launch.argv, "--allowedTools").split(",") == [*expected, "TodoWrite", f"mcp__{MCP_SERVER}"]
    if sub_agents:
        assert _flag(launch.argv, "--agents") == AGENTS_PATH
        agents = json.loads(launch.files[AGENTS_PATH])
        assert list(agents) == [SUB_AGENT]
        assert "Agent" not in agents[SUB_AGENT]["tools"]
        assert f"mcp__{MCP_SERVER}" in agents[SUB_AGENT]["tools"]
        # Depth 1 whatever the profile asks: a deeper profile is narrowed, never widened.
        assert launch.env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] == "1"
        assert launch.env["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] == "2"
    else:
        assert "--agents" not in launch.argv and AGENTS_PATH not in launch.files
        assert "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH" not in launch.env


def test_cl3_effort_and_instructions() -> None:
    assert "--effort" not in render_launch(_profile(), ENDPOINTS, "").argv
    assert _flag(render_launch(_profile(reasoning_effort="low"), ENDPOINTS, "").argv, "--effort") == "low"
    assert system_prompt("") == BASE_PROMPT
    assert system_prompt("House style.") == f"{BASE_PROMPT}\n\nHouse style."


@pytest.mark.parametrize(
    "change",
    [
        {"profile": {"engine": EngineVersion("codex", "0.160.0", "sha256:" + "d" * 64)}},
        {"endpoints": {"model_api": ModelApi.RESPONSES}},
        {"endpoints": {"model_api": ModelApi.CHAT_COMPLETIONS}},
        {"endpoints": {"model_base_url": "http://model-gateway:8097"}},
    ],
)
def test_cl4_refusals(change: dict[str, dict[str, Any]]) -> None:
    profile = _profile(**change.get("profile", {}))
    endpoints = dataclasses.replace(ENDPOINTS, **change.get("endpoints", {}))
    with pytest.raises(ValueError):
        render_launch(profile, endpoints, "/skills")


def test_cl5_a_result_schema_is_never_sent() -> None:
    launch = render_launch(_profile(result_schema={"type": "object"}), ENDPOINTS, "/skills")
    assert "--json-schema" not in launch.argv
    assert "outputSchema" not in "\n".join(launch.argv)


def test_cl6_pipeshub_restricts_no_login_method_and_never_bypasses_permissions() -> None:
    launch = render_launch(_profile(), ENDPOINTS, "/skills")
    text = "\n".join([*launch.argv, *launch.env, *(f.decode() for f in launch.files.values())])
    for banned in ("auto", "bypassPermissions", "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions",
                   "--bare", "forceLoginMethod", "allowedProviders", "DISABLE_LOGIN_COMMAND", "DISABLE_LOGOUT_COMMAND",
                   "ANTHROPIC_API_KEY", "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST"):
        assert banned not in launch.argv and banned not in text.split(), banned
    assert _flag(launch.argv, "--permission-mode") == "dontAsk"


def test_cl7_neutral_names() -> None:
    launch = render_launch(_profile(sub_agents=SubAgentPolicy(enabled=True)), ENDPOINTS, "/skills")
    names = [MCP_SERVER, MODEL, SUB_AGENT, *json.loads(launch.files[AGENTS_PATH])]
    for name in names:
        assert not any(marker in name.lower() for marker in VENDOR_MARKERS), name


def test_cl8_extras_are_off() -> None:
    launch = render_launch(_profile(), ENDPOINTS, "/skills")
    for name in ("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "DISABLE_TELEMETRY", "DISABLE_ERROR_REPORTING",
                 "DISABLE_AUTOUPDATER", "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS", "CLAUDE_CODE_DISABLE_POLICY_SKILLS",
                 "CLAUDE_CODE_DISABLE_AUTO_MEMORY", "CLAUDE_CODE_DISABLE_CLAUDE_MDS",
                 "CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS", "CLAUDE_CODE_DISABLE_WEB_FETCH",
                 "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"):
        assert launch.env[name] == "1", name
    assert launch.env["ENABLE_TOOL_SEARCH"] == "false"
    settings = json.loads(launch.files[SETTINGS_PATH])
    assert settings["enabledPlugins"] == dict.fromkeys(BUILTIN_PLUGINS_OFF, False)
    assert settings["attribution"] == {"commit": "", "pr": ""}
    # Only Claude Code's user scope (its config folder here, regenerated per sandbox) is read: no project files.
    assert _flag(launch.argv, "--setting-sources") == "user"
    assert _flag(launch.argv, "--settings") == SETTINGS_PATH
