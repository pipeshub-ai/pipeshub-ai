"""ACP-GOOSE (C2.41): the shipped Goose (ACP) profile and its launch, pure.

Goose 1.57.0's own ACP mode (``goose acp``) obeys one file the profile renders, root's and read-only: its user
``config.yaml`` under ``XDG_CONFIG_HOME`` (JSON, which is YAML). It names the gateway's chat-completions wire as its
OpenAI provider, asks the driver before every tool (``GOOSE_MODE: approve``), switches telemetry and the keyring off,
and lists the only extensions Goose loads: its developer tools narrowed to what the run's mode allows
(``available_tools``), its skills tool, and the tool gateway with the run's tool timeout. The real binary under the
real gateways is ``tests/unit/harness/engines/test_real_goose_acp_conformance.py``.
"""

from __future__ import annotations

import dataclasses
import json
from importlib import resources
from typing import TYPE_CHECKING, Any, cast

import pytest

from harness_core.access.types import ENGINE_MODEL_ID, AccessRouteRef, ModelApi
from harness_core.engines.policy import NativeTool
from harness_core.engines.types import EngineMode, EngineProfile, GatewayEndpoints
from harness_core.testing.engines.contract import ENDPOINTS
from harness_engines import release
from harness_engines.acp.adapter import AcpAdapter
from harness_engines.acp.launch import SESSION_POLICY_PATH, render_launch
from harness_engines.acp.permissions import SessionPolicy, classify
from harness_engines.acp.profiles import (
    AcpProfileInvalid,
    McpStatus,
    parse_acp_agents,
    parse_profile,
)
from harness_engines.acp.registry import AGENTS_RESOURCE

if TYPE_CHECKING:
    from harness_engines.acp.profiles import AcpAgentProfile

SHIPPED = resources.files("harness_engines").joinpath(AGENTS_RESOURCE).read_bytes()
ACCESS = AccessRouteRef(route_id="route-1", digest="d1")
TOKEN = "pxh-model-placeholder-0123456789"
TOOLS = "pxh-tools-placeholder-0123456789"
SKILLS = "/skills"
CONFIG = "/state/acp/goose/goose/config.yaml"
# Hosts Goose's providers, telemetry and registries name by default; none may reach what it obeys.
DEFAULT_HOSTS = ("openai.com", "posthog", "anthropic.com", "openrouter", "googleapis", "github.com", "osv.dev")


def _goose() -> AcpAgentProfile:
    return next(p for p in parse_acp_agents(SHIPPED) if p.id == "goose")


def _endpoints() -> GatewayEndpoints:
    return dataclasses.replace(
        ENDPOINTS,
        model_base_url="http://model-gateway:8097/v1",
        tools_mcp_url="http://tool-gateway:8097/mcp",
        token_placeholder=TOKEN,
        tools_token_placeholder=TOOLS,
    )


def _launch(mode: EngineMode = EngineMode.BUILD) -> tuple[dict[str, str], dict[str, Any], dict[str, bytes]]:
    agent = _goose()
    launch = render_launch(agent, EngineProfile(engine=agent.engine, mode=mode, access=ACCESS), _endpoints(), SKILLS)
    return dict(launch.env), json.loads(launch.files[CONFIG]), dict(launch.files)


class TestAcpGooseShipped:
    def test_acp_goose_1_the_profile_is_its_allowlist_entry_and_its_own_image(self) -> None:
        """C2.41: Goose ships as an ACP profile pinned by version, binary digest and its own image."""
        agent = _goose()
        entry = next(r for r in release.release_allowlist() if r.version.kind == "acp-goose")
        assert entry.version == agent.engine and entry.image == agent.image == "pxh-goose-tools:1.57.0-ph7"
        assert entry.name == agent.name == "Goose (ACP)"
        assert (agent.agent_name, agent.agent_version) == ("goose", "1.57.0")
        assert agent.binary == "/usr/local/bin/goose" and agent.argv == ("acp",)
        assert agent.resume == "load" and agent.state_paths == ("/state/goose",)

    def test_acp_goose_1_a_tool_rules_form_outside_the_three_is_refused(self) -> None:
        raw = cast("dict[str, object]", next(a for a in json.loads(SHIPPED)["agents"] if a["id"] == "goose"))
        configs = [dict(c) for c in cast("list[dict[str, object]]", raw["configs"])]
        (ruled,) = [c for c in configs if "toolRules" in c]
        ruled["toolRulesForm"] = "kept"
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile({**raw, "configs": configs})
        ruled["toolRulesForm"], ruled["toolRules"] = "allowed", []
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile({**raw, "configs": configs})


class TestAcpGooseGatewayOnly:
    def test_acp_goose_2_model_traffic_reaches_only_the_gateways_chat_wire_with_the_placeholder(self) -> None:
        env, config, _ = _launch()
        assert env["OPENAI_HOST"] == "http://model-gateway:8097"
        assert env["OPENAI_BASE_PATH"] == "v1/chat/completions"
        assert env["OPENAI_API_KEY"] == TOKEN
        assert env["GOOSE_PROVIDER"] == config["GOOSE_PROVIDER"] == "openai"
        assert env["GOOSE_MODEL"] == config["GOOSE_MODEL"] == ENGINE_MODEL_ID
        assert _goose().model_api is ModelApi.CHAT_COMPLETIONS
        assert AcpAdapter(_goose()).capabilities().model_apis == frozenset({ModelApi.CHAT_COMPLETIONS})

    def test_acp_goose_2_nothing_but_placeholders_and_no_default_host_in_what_it_obeys(self) -> None:
        env, _, files = _launch()
        rendered = json.dumps({"env": env, "files": {k: v.decode() for k, v in files.items()}})
        assert not any(host in rendered for host in DEFAULT_HOSTS)
        for credential in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GOOGLE_API_KEY", "DATABRICKS_TOKEN"):
            assert credential not in rendered

    def test_acp_goose_3_telemetry_keyring_and_unasked_tools_are_off(self) -> None:
        env, config, _ = _launch()
        assert env["GOOSE_TELEMETRY_OFF"] == "1" and config["GOOSE_TELEMETRY_ENABLED"] is False
        assert env["GOOSE_DISABLE_KEYRING"] == "1" and config["GOOSE_DISABLE_KEYRING"] is True
        # Every tool asks the driver, which answers by the run's mode; Goose's own auto mode would run them unasked.
        assert env["GOOSE_MODE"] == config["GOOSE_MODE"] == "approve"
        # Its own session titles would be an extra model call per session, without tools.
        assert env["GOOSE_DISABLE_SESSION_NAMING"] == "true" and config["GOOSE_DISABLE_SESSION_NAMING"] is True
        # Its config and home are root's (CONFIG_ROOT); its sessions are its own, under /state.
        assert env["XDG_CONFIG_HOME"] == "/state/acp/goose" and env["XDG_DATA_HOME"] == "/state"
        assert env["HOME"] == "/state/acp/goose/home"


class TestAcpGooseExtensions:
    def test_acp_goose_4_only_the_developer_skills_and_gateway_extensions_load(self) -> None:
        """Goose loads its developer, analyze and skills tools and the tool gateway, and none of its other platform
        extensions (extension manager, summon's sub-agents, apps, scheduler, top of mind, ...)."""
        _, config, _ = _launch()
        enabled = sorted(name for name, entry in config["extensions"].items() if entry["enabled"])
        assert enabled == ["analyze", "developer", "skills"]
        # Goose starts its default-on platform extensions unless the config turns each off by name (found on the
        # binary: extensionmanager, apps and summon's delegate/load reached the model).
        disabled = sorted(name for name, entry in config["extensions"].items() if not entry["enabled"])
        assert disabled == sorted(
            ["apps", "extensionmanager", "summon", "scheduler", "tom", "todo", "chatrecall", "summarize", "code_execution"]
        )

    @pytest.mark.parametrize(
        ("mode", "developer"),
        [
            (EngineMode.BUILD, ["analyze", "read_image", "tree", "write", "edit", "shell", "load_skill"]),
            (EngineMode.PLAN, ["analyze", "read_image", "tree", "load_skill"]),
            (EngineMode.REVIEW, ["analyze", "read_image", "tree", "load_skill"]),
        ],
    )
    def test_acp_goose_4_the_developer_tools_are_what_the_mode_allows(self, mode: EngineMode, developer: list[str]) -> None:
        """``available_tools`` lists what Goose keeps (an empty list would keep all): plan and review modes show no
        editing or shell tool, so the model never sees one."""
        _, config, _ = _launch(mode)
        assert config["extensions"]["developer"]["available_tools"] == developer
        # Its tree-sitter code views read only, in every mode.
        assert "available_tools" not in config["extensions"]["analyze"]

    def test_acp_goose_5_the_tool_gateway_is_the_sessions_server_with_the_runs_timeout(self) -> None:
        """Goose keeps a session's extensions in its session store and, on ``session/load``, restores them from there
        (config extensions are not read again), replacing one by name only with a server the request sends. So the
        gateway travels in ``session/new`` and ``session/load`` (a resumed sandbox's new URL and placeholder replace
        the stored ones; found on the binary, CF-5), with the run's tool timeout in the entry's ``_meta``, which Goose
        reads (else its 300 s default). The config declares no gateway."""
        agent = _goose()
        _, config, files = _launch()
        assert "tools" not in config["extensions"]
        assert agent.mcp_declare == "session"
        policy = json.loads(files[SESSION_POLICY_PATH])
        assert policy["mcpMeta"] == {"timeout": ENDPOINTS.tool_timeout_s}
        assert agent.exposed_tool_name("search") == "tools__search"
        # Goose connects its extensions inside session/new and session/load and reports nothing: the driver lists the
        # gateway's tools first, with the image's Node (E2E3-F6).
        # It lists the tools only once prompted, so its handshake inside session/new is the witness (R30ENG-FU-3).
        assert agent.mcp_status == McpStatus(api="gateway", runtime="/usr/bin/node", witness="handshake")

    def test_acp_goose_5_a_gateway_tool_is_allowed_and_an_unknown_extensions_tool_asks(self) -> None:
        agent = _goose()
        launch = render_launch(agent, EngineProfile(engine=agent.engine, mode=EngineMode.BUILD, access=ACCESS), _endpoints(), SKILLS)
        policy = SessionPolicy.from_json(launch.files[SESSION_POLICY_PATH])
        assert classify(policy, {"toolCallId": "c1", "title": "tools__search", "kind": "other"}) == ("allow", "gateway_tool")
        assert classify(policy, {"toolCallId": "c2", "title": "repo__run", "kind": "other"})[0] == "ask"

    def test_acp_goose_6_skills_are_linked_where_goose_finds_global_skills(self) -> None:
        agent = _goose()
        env, _, _ = _launch()
        assert agent.skills_link == env["HOME"] + "/.agents/skills"
        assert env[agent.gateway_env["skillsDir"]] == SKILLS
        assert NativeTool.SKILL in agent.native_tools and AcpAdapter(agent).capabilities().native_skills is True

    def test_acp_goose_6_the_runs_instructions_are_its_global_hints(self) -> None:
        agent = _goose()
        env, _, _ = _launch()
        assert agent.instructions_file == env["XDG_CONFIG_HOME"] + "/goose/.goosehints"


class TestAcpGooseBudgetStop:
    def test_acp_goose_8_a_spent_budget_ends_the_turn_as_a_budget_stop(self) -> None:
        """Goose turns the model gateway's 402 into its own credits error (found on the binary): the ``session/prompt``
        error's message is Goose's top-up text, without the gateway's ``[gateway:<code>]``, and its data says
        ``reason: credits_exhausted``. The profile maps that reason to a budget stop, so the controller stops for the
        budget rather than failing the run as an agent error."""
        from harness_engines.acp.mapper import error_code

        policy = SessionPolicy.from_json(_launch()[2][SESSION_POLICY_PATH])
        message = "Please add credits to your account, then resend your message to continue."
        assert error_code(message, {"reason": "credits_exhausted"}, policy.error_reasons) == "budget_exhausted"
        assert error_code(message, {"reason": "turn_failed"}, policy.error_reasons) == "acp_error"
        # The gateway's own code, when an agent passes it on, wins over a mapped reason.
        tagged = "[gateway:budget_insufficient] less budget is left than the call needs"
        assert error_code(tagged, {"reason": "credits_exhausted"}, policy.error_reasons) == "budget_insufficient"

    def test_acp_goose_8_an_error_reason_must_map_to_an_engine_code(self) -> None:
        raw = cast("dict[str, object]", next(a for a in json.loads(SHIPPED)["agents"] if a["id"] == "goose"))
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile({**raw, "errorReasons": {"credits_exhausted": "Budget Gone!"}})
