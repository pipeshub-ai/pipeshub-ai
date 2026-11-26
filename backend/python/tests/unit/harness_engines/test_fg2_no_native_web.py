"""FG2: an engine reaches the web only through the tool gateway, where the flow guard sees the data
a request carries. Its native web tools are off in every profile: egress rules see the host, never the data."""

from __future__ import annotations

import itertools
import json
import tomllib

import pytest

from harness_core.access.types import AccessRouteRef, ModelApi
from harness_core.engines.policy import NativeTool, expected_native_tools
from harness_core.engines.types import (
    EngineMode,
    EngineProfile,
    EngineVersion,
    GatewayEndpoints,
    SubAgentPolicy,
)
from harness_engines.claude_code import launch as claude_code
from harness_engines.codex import launch as codex
from harness_engines.opencode import launch as opencode

PROFILES = [
    (mode, sub_agents)
    for mode, sub_agents in itertools.product(EngineMode, (False, True))
]


def _profile(kind: str, mode: EngineMode, sub_agents: bool) -> EngineProfile:
    return EngineProfile(
        engine=EngineVersion(kind, "1.0.0", "sha256:" + "d" * 64),
        mode=mode,
        access=AccessRouteRef("route-1", "d1"),
        sub_agents=SubAgentPolicy(enabled=sub_agents),
    )


def _endpoints(api: ModelApi) -> GatewayEndpoints:
    return GatewayEndpoints(
        model_base_url="http://model-gateway:8097/v1",
        model_api=api,
        tools_mcp_url="http://tool-gateway:8097/mcp",
        token_placeholder="pxh-model-placeholder-0123",
        tools_token_placeholder="pxh-tools-placeholder-4567",
    )


@pytest.mark.parametrize(("mode", "sub_agents"), PROFILES)
@pytest.mark.parametrize("skills", [False, True])
def test_no_profile_lets_an_engine_bring_its_own_web_tool(mode: EngineMode, sub_agents: bool, skills: bool) -> None:
    assert NativeTool.WEB not in expected_native_tools(_profile("opencode", mode, sub_agents), skills=skills)


@pytest.mark.parametrize(("mode", "sub_agents"), PROFILES)
def test_claude_code_runs_without_web_fetch_or_web_search(mode: EngineMode, sub_agents: bool) -> None:
    launch = claude_code.render_launch(_profile("claude_code", mode, sub_agents), _endpoints(ModelApi.ANTHROPIC_MESSAGES), "/skills")
    tools = launch.argv[launch.argv.index("--tools") + 1].split(",")
    assert not {"WebFetch", "WebSearch"} & set(tools)
    assert launch.env["CLAUDE_CODE_DISABLE_WEB_FETCH"] == "1"


@pytest.mark.parametrize(("mode", "sub_agents"), PROFILES)
def test_codex_runs_without_web_search(mode: EngineMode, sub_agents: bool) -> None:
    launch = codex.render_launch(_profile("codex", mode, sub_agents), _endpoints(ModelApi.RESPONSES), "/skills")
    assert tomllib.loads(launch.files[codex.CONFIG_PATH].decode())["web_search"] == "disabled"


@pytest.mark.parametrize(("mode", "sub_agents"), PROFILES)
def test_opencode_denies_its_web_tools(mode: EngineMode, sub_agents: bool) -> None:
    launch = opencode.render_launch(_profile("opencode", mode, sub_agents), _endpoints(ModelApi.CHAT_COMPLETIONS), "/skills")
    permission = json.loads(launch.env["OPENCODE_CONFIG_CONTENT"])["permission"]
    assert permission["*"] == "deny"
    assert all(permission.get(tool, "deny") == "deny" for tool in ("webfetch", "websearch"))
