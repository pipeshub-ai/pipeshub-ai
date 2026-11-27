"""CL-1..CL-7 (E8): Codex's launch is a pure function of the profile and the gateway endpoints."""

from __future__ import annotations

import dataclasses
import tomllib
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from harness_core.access.types import AccessRouteRef, ModelApi
from harness_core.engines.types import (
    MIN_TOOL_TIMEOUT_S,
    EngineMode,
    EngineProfile,
    EngineVersion,
    GatewayEndpoints,
    SubAgentPolicy,
)
from harness_engines.codex.launch import (
    CODEX_HOME,
    CONFIG_PATH,
    FEATURES_OFF,
    INSTRUCTION_PATHS,
    MCP_SERVER,
    MODEL,
    PROVIDER,
    SKILLS_ENV,
    TOKEN_ENV,
    TOOLS_TOKEN_ENV,
    VENDOR_MARKERS,
    render_launch,
    render_toml,
    thread_settings,
)

CODEX = EngineVersion("codex", "0.160.0", "sha256:" + "d" * 64)
ENDPOINTS = GatewayEndpoints(
    model_base_url="http://model-gateway:8097/v1",
    model_api=ModelApi.RESPONSES,
    tools_mcp_url="http://tool-gateway:8097/mcp",
    token_placeholder="pxh-model-placeholder-0123",
    tools_token_placeholder="pxh-tools-placeholder-4567",
)


def _profile(**changes: Any) -> EngineProfile:  # noqa: ANN401
    base = EngineProfile(engine=CODEX, mode=EngineMode.BUILD, access=AccessRouteRef("route-1", "d1"))
    return dataclasses.replace(base, **changes)


def _config(profile: EngineProfile | None = None) -> dict[str, Any]:
    launch = render_launch(profile or _profile(), ENDPOINTS, "/skills")
    return tomllib.loads(launch.files[CONFIG_PATH].decode())


def test_cl1_the_launch_is_pure_and_holds_placeholders_only() -> None:
    first = render_launch(_profile(), ENDPOINTS, "/skills")
    assert first == render_launch(_profile(), ENDPOINTS, "/skills")
    assert first.argv == ("/usr/local/bin/codex-app-server", "--strict-config", "--listen", "stdio://")
    assert (first.workdir, first.port) == ("/workspace", None)
    assert first.env == {
        "CODEX_HOME": CODEX_HOME,
        "HOME": "/tmp",
        TOKEN_ENV: ENDPOINTS.token_placeholder,
        TOOLS_TOKEN_ENV: ENDPOINTS.tools_placeholder,
        SKILLS_ENV: "/skills",
    }
    assert set(first.files) == {CONFIG_PATH, *INSTRUCTION_PATHS}
    assert all(first.files[path] == b"" for path in INSTRUCTION_PATHS)
    assert CONFIG_PATH.startswith("/state/")
    config = first.files[CONFIG_PATH].decode()
    assert ENDPOINTS.token_placeholder not in config and ENDPOINTS.tools_placeholder not in config


def test_cl2_the_config_reaches_only_the_gateways_with_every_extra_off() -> None:
    config = _config()
    assert (config["model"], config["model_provider"]) == (MODEL, PROVIDER)
    assert config["model"] == "engine-model"
    provider = config["model_providers"][PROVIDER]
    assert provider == {
        "name": "Gateway",
        "base_url": ENDPOINTS.model_base_url,
        "wire_api": "responses",
        "env_key": TOKEN_ENV,
        "request_max_retries": 1,
        "stream_max_retries": 0,
    }
    assert "auth" not in provider and "experimental_bearer_token" not in provider
    assert config["web_search"] == "disabled"
    assert (config["approval_policy"], config["sandbox_mode"]) == ("never", "danger-full-access")
    mcp = config["mcp_servers"]
    assert set(mcp) == {MCP_SERVER}
    assert mcp[MCP_SERVER]["url"] == ENDPOINTS.tools_mcp_url
    assert mcp[MCP_SERVER]["bearer_token_env_var"] == TOOLS_TOKEN_ENV
    assert mcp[MCP_SERVER]["tool_timeout_sec"] >= MIN_TOOL_TIMEOUT_S
    assert config["analytics"] == {"enabled": False}
    assert config["feedback"] == {"enabled": False}
    assert config["otel"] == {"metrics_exporter": "none"}
    assert config["check_for_update_on_startup"] is False
    assert config["project_doc_max_bytes"] == 0
    assert config["projects"] == {"/workspace": {"trust_level": "untrusted"}}
    assert config["tools"]["experimental_request_user_input"] == {"enabled": False}
    assert config["skills"]["bundled"] == {"enabled": False}
    assert config["include_permissions_instructions"] is False
    assert all(config["features"][name] is False for name in FEATURES_OFF)
    assert {"apps", "plugins", "memories", "image_generation", "goals"} <= set(FEATURES_OFF)
    # FIXPDFSKILL-FU-1: Codex looks at the pages the document checker renders, under the managed hook only.
    assert "view_image" not in FEATURES_OFF


@pytest.mark.parametrize(
    ("mode", "shell"), [(EngineMode.BUILD, True), (EngineMode.PLAN, False), (EngineMode.REVIEW, False)]
)
def test_cl3_plan_and_review_have_no_shell(mode: EngineMode, shell: bool) -> None:
    assert _config(_profile(mode=mode))["features"]["shell_tool"] is shell


def test_cl3_sub_agents_follow_the_profile_at_depth_one() -> None:
    assert _config()["features"]["multi_agent"] is False
    enabled = _config(_profile(sub_agents=SubAgentPolicy(enabled=True, max_depth=3, max_concurrent=2)))
    assert enabled["features"]["multi_agent"] is True
    assert enabled["agents"] == {"max_depth": 1, "max_concurrent_threads_per_session": 2}


def test_cl3_effort_is_the_profiles() -> None:
    assert "model_reasoning_effort" not in _config()
    assert _config(_profile(reasoning_effort="high"))["model_reasoning_effort"] == "high"


def test_cl4_another_engine_or_model_api_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot launch"):
        render_launch(_profile(engine=dataclasses.replace(CODEX, kind="opencode")), ENDPOINTS, "/skills")
    chat = dataclasses.replace(ENDPOINTS, model_api=ModelApi.CHAT_COMPLETIONS)
    with pytest.raises(ValueError, match="Responses"):
        render_launch(_profile(), chat, "/skills")


def test_cl5_a_result_schema_never_reaches_codex() -> None:
    profile = _profile(result_schema={"type": "object", "properties": {"answer": {"type": "string"}}})
    launch = render_launch(profile, ENDPOINTS, "/skills")
    text = launch.files[CONFIG_PATH].decode()
    assert "schema" not in text.lower() and "answer" not in text
    assert "outputSchema" not in thread_settings("instructions")


@given(st.text())
def test_cl6_toml_strings_round_trip(value: str) -> None:
    document = {"key": value, "table": {"nested key": value}}
    assert tomllib.loads(render_toml(document)) == document


def test_cl6_toml_escapes_quotes_backslashes_and_controls() -> None:
    value = 'a "quoted" \\ path\nwith\ttabs \x00 and é'
    assert tomllib.loads(render_toml({"v": value})) == {"v": value}


def test_cl7_ids_are_neutral() -> None:
    for name in (PROVIDER, MCP_SERVER, MODEL):
        assert not any(marker in name for marker in VENDOR_MARKERS)


def test_thread_settings_carry_the_tasks_instructions_only_when_given() -> None:
    assert "developerInstructions" not in thread_settings("")
    settings = thread_settings("Follow the house style.")
    assert settings["developerInstructions"] == "Follow the house style."
    assert (settings["approvalPolicy"], settings["sandbox"], settings["cwd"]) == ("never", "danger-full-access", "/workspace")


def test_e2e3_f6_the_tool_gateway_is_a_required_mcp_server() -> None:
    """Codex 0.160 sends a turn's first model request without the tools of a server that is not required once
    about a second has passed (E2E3-F6, ``test_e2e3_f6_mcp_startup_real_binary.py``): the gateway is required, so
    the thread opens only once its tools are listed, within the startup timeout."""
    server = _config()["mcp_servers"][MCP_SERVER]
    assert server["required"] is True
    assert server["startup_timeout_sec"] == 30
