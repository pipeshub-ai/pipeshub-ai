"""OC-1..OC-4: render_launch, env lock-down, rendered config, permission emulation vs captures."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from harness_core.access.types import AccessRouteRef, ModelApi
from harness_core.budget.run_budget import NATIVE_STEP_HEADROOM
from harness_core.engines.jsonview import as_list, as_object, as_str
from harness_core.engines.policy import NativeTool
from harness_core.engines.types import (
    EngineMode,
    EngineProfile,
    GatewayEndpoints,
    RunLimits,
    SubAgentPolicy,
)
from harness_engines.opencode.launch import (
    LOCKED_ENV,
    NATIVE_TOOLS,
    RUNTIME_NOTE,
    exposed_tool_name,
    render_launch,
)
from harness_engines.opencode.permissions import Rule, evaluate, flatten, visible_tools
from tests.unit.harness_engines.opencode.conftest import PINNED

FIXTURES = Path(__file__).parent / "fixtures"
ENDPOINTS = GatewayEndpoints(
    model_base_url="http://model-gateway.internal/v1",
    model_api=ModelApi.CHAT_COMPLETIONS,
    tools_mcp_url="http://tool-gateway.internal/mcp",
    token_placeholder="placeholder-token",
)


def _profile(mode: EngineMode = EngineMode.BUILD, *, sub_agents: bool = False) -> EngineProfile:
    return EngineProfile(
        engine=PINNED,
        mode=mode,
        access=AccessRouteRef("route", "d"),
        sub_agents=SubAgentPolicy(enabled=sub_agents),
        limits=RunLimits(max_steps=30),
    )


def _config(mode: EngineMode = EngineMode.BUILD, *, sub_agents: bool = False) -> dict[str, object]:
    launch = render_launch(_profile(mode, sub_agents=sub_agents), ENDPOINTS, "/skills")
    return json.loads(launch.env["OPENCODE_CONFIG_CONTENT"])


def test_oc1_launch_is_pure_and_serves_on_loopback() -> None:
    first = render_launch(_profile(), ENDPOINTS, "/skills")
    assert first == render_launch(_profile(), ENDPOINTS, "/skills")
    assert first.argv == ("/usr/local/bin/opencode", "serve", "--hostname", "127.0.0.1", "--port", "4096")
    assert first.port == 4096
    assert first.engine == PINNED
    assert first.files == {}


def test_oc2_env_has_every_locked_flag_and_no_password() -> None:
    probe_env = dict(
        re.findall(r"^export (\w+)=(\S+)$", (FIXTURES / "env.locked.sh").read_text(), re.MULTILINE)
    )
    launch = render_launch(_profile(), ENDPOINTS, "/skills")
    for key, value in probe_env.items():
        if key == "OPENCODE_SERVER_PASSWORD":
            assert key not in launch.env
        elif key == "OPENCODE_CLIENT":
            assert launch.env[key] not in ("cli", "app", "desktop")
        else:
            assert launch.env[key] == value, key
    assert launch.env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
    assert launch.env["OPENCODE_PURE"] == "1"
    assert launch.env["XDG_CONFIG_HOME"] == LOCKED_ENV["XDG_CONFIG_HOME"]
    assert launch.env["HARNESS_GATEWAY_TOKEN"] == "placeholder-token"


def test_oc3_config_locks_provider_model_and_side_effects() -> None:
    config = as_object(_config())
    provider = as_object(as_object(config.get("provider")).get("gateway"))
    assert config.get("enabled_providers") == ["gateway"]
    assert provider.get("npm") == "@ai-sdk/openai-compatible"
    options = as_object(provider.get("options"))
    assert options.get("baseURL") == ENDPOINTS.model_base_url
    assert options.get("apiKey") == "{env:HARNESS_GATEWAY_TOKEN}"
    model = as_str(config.get("model"))
    assert not any(marker in model for marker in ("gpt-", "claude", "gemini"))
    for key, value in {"autoupdate": False, "share": "disabled", "snapshot": False, "lsp": False, "formatter": False}.items():
        assert config.get(key) == value
    agents = as_object(config.get("agent"))
    assert config.get("default_agent") == "harness"
    for builtin in ("build", "plan", "general", "explore", "title"):
        assert as_object(agents.get(builtin)).get("disable") is True
    # WP70-NATIVE: a little above the profile's 30, so the controller's step limit (and its wrap-up) binds first.
    assert as_object(agents.get("harness")).get("steps") == 30 + NATIVE_STEP_HEADROOM
    mcp = as_object(as_object(config.get("mcp")).get("tools"))
    assert mcp.get("oauth") is False
    assert mcp.get("url") == ENDPOINTS.tools_mcp_url
    assert isinstance(mcp.get("timeout"), int) and mcp.get("timeout", 0) >= 150_000  # type: ignore[operator]
    assert as_object(mcp.get("headers")).get("Authorization") == "Bearer {env:HARNESS_TOOLS_TOKEN}"
    assert as_list(as_object(config.get("skills")).get("paths")) == ["{env:HARNESS_SKILLS_DIR}"]
    assert "placeholder-token" not in json.dumps(config)


UNIVERSE = ["bash", "edit", "glob", "grep", "read", "task", "todowrite", "webfetch", "write", "skill", "question"]


@pytest.mark.parametrize(
    ("mode", "sub_agents", "expected_native"),
    [
        (EngineMode.BUILD, False, {NativeTool.READ, NativeTool.SEARCH, NativeTool.EDIT, NativeTool.SHELL, NativeTool.SKILL}),
        (EngineMode.PLAN, False, {NativeTool.READ, NativeTool.SEARCH, NativeTool.SKILL}),
        (EngineMode.BUILD, True, {NativeTool.READ, NativeTool.SEARCH, NativeTool.EDIT, NativeTool.SHELL, NativeTool.SKILL, NativeTool.SUBAGENT}),
    ],
)
def test_oc3_permissions_leave_exactly_the_mode_tools(
    mode: EngineMode, sub_agents: bool, expected_native: set[NativeTool]
) -> None:
    rules = [Rule("*", "*", "allow"), *flatten(as_object(_config(mode, sub_agents=sub_agents).get("permission")))]
    universe = [*UNIVERSE, exposed_tool_name("search"), "other_admin"]
    # Every mode also has the engine's own task list, the run's plan (UXR-B1).
    expected = {name for tool in expected_native for name in NATIVE_TOOLS[tool]} | {exposed_tool_name("search"), "todowrite"}
    assert set(visible_tools(rules, universe)) == expected
    if sub_agents:
        assert evaluate(rules, "task", "helper") == "allow"
        assert evaluate(rules, "task", "general") == "deny"
    assert evaluate(rules, "skill", "customize-opencode") == "deny"


def _captured(name: str) -> list[dict[str, object]]:
    return json.loads((FIXTURES / "captured-tool-lists.json").read_text())[name]


def test_oc4_emulation_reproduces_the_captured_locked_tool_list() -> None:
    locked = as_object(json.loads((FIXTURES / "config.locked.json").read_text()))
    default_universe = set(as_list(_captured("native-default")[1]["tools"]))  # type: ignore[arg-type]
    rules = [Rule("*", "*", "allow"), *flatten(as_object(locked.get("permission")))]
    captured = _captured("native-locked")[0]["tools"]
    assert visible_tools(rules, default_universe) == captured == ["pipeshub_pipeshub_search", "skill"]


def test_oc4_session_rules_come_after_config_rules() -> None:
    locked = as_object(json.loads((FIXTURES / "config.locked.json").read_text()))
    rules = [Rule("*", "*", "allow"), *flatten(as_object(locked.get("permission"))), Rule("bash", "*", "ask")]
    default_universe = set(as_list(_captured("native-default")[1]["tools"]))  # type: ignore[arg-type]
    bash_session = next(c for c in _captured("native-locked") if "bash" in c["tools"])  # type: ignore[operator]
    assert visible_tools(rules, default_universe) == bash_session["tools"]


def test_oc4_default_config_shows_every_tool() -> None:
    universe = _captured("native-default")[1]["tools"]
    assert visible_tools([Rule("*", "*", "allow")], universe) == universe  # type: ignore[arg-type]


def test_eg5_model_and_tool_gateways_get_their_own_placeholders() -> None:
    two = GatewayEndpoints(
        model_base_url=ENDPOINTS.model_base_url,
        model_api=ENDPOINTS.model_api,
        tools_mcp_url=ENDPOINTS.tools_mcp_url,
        token_placeholder="placeholder-model-0123",
        tools_token_placeholder="placeholder-tools-4567",
    )
    env = render_launch(_profile(), two, "/skills").env
    assert (env["HARNESS_GATEWAY_TOKEN"], env["HARNESS_TOOLS_TOKEN"]) == ("placeholder-model-0123", "placeholder-tools-4567")
    single = render_launch(_profile(), ENDPOINTS, "/skills").env
    assert single["HARNESS_TOOLS_TOKEN"] == single["HARNESS_GATEWAY_TOKEN"] == "placeholder-token"


def test_oc17_the_helper_cannot_spawn_helpers() -> None:
    """Found on the real binary: the config-level `task` allowance reached the helper (CF-11)."""
    helper = as_object(as_object(_config(sub_agents=True).get("agent")).get("helper"))
    assert as_object(helper.get("permission")) == {"task": "deny"}


def test_oc16_skill_resources_are_readable_and_nothing_else_outside_the_workspace() -> None:
    """Found on the real binary: `"*": deny` also denied `read` of a skill's own files."""
    launch = render_launch(_profile(), ENDPOINTS, "/opt/skills/")
    rules = [Rule("*", "*", "allow"), *flatten(as_object(json.loads(launch.env["OPENCODE_CONFIG_CONTENT"]).get("permission")))]
    assert evaluate(rules, "external_directory", "/opt/skills/report/scripts/run.py") == "allow"
    assert evaluate(rules, "external_directory", "/etc/passwd") == "deny"
    assert evaluate(rules, "external_directory", "/opt/skills-evil/x") == "deny"
    # R3: the run's tool index and ph's docs; nothing beside them.
    assert evaluate(rules, "external_directory", "/pipeshub/tools.txt") == "allow"
    assert evaluate(rules, "external_directory", "/pipeshub-evil/x") == "deny"


def test_pr1_the_image_runtime_note_is_an_instructions_file() -> None:
    """E0b PR-1: the tools image describes what it ships at a fixed path; opencode adds it to the system
    prompt when it exists (PR-2) and skips it when it does not (PR-3)."""
    assert _config()["instructions"] == [RUNTIME_NOTE]
    assert RUNTIME_NOTE == "/usr/share/pxh-engine/RUNTIME.md"


def test_oc18_the_shell_outlasts_an_approval_hold() -> None:
    """``ph git clone`` runs in the shell tool and its call may be held for approval up to ``APPROVAL_HOLD_S``. With
    opencode's default shell timeout (120 s, the hold's own length) the shell killed ``ph`` before the held answer
    came back, so the engine never learned the interaction and never called the clone again once it was approved."""
    from harness_core.interactions.hold import APPROVAL_HOLD_S
    from harness_engines.opencode.launch import BASH_TIMEOUT_ENV

    launch = render_launch(_profile(), ENDPOINTS, "/skills")
    shell_ms = int(launch.env[BASH_TIMEOUT_ENV])
    assert BASH_TIMEOUT_ENV == "OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS"
    assert shell_ms == ENDPOINTS.tool_timeout_s * 1000
    assert shell_ms >= (APPROVAL_HOLD_S + 30) * 1000
