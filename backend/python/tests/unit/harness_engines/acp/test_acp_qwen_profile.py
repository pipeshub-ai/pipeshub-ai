"""ACP-QWEN (C2.41, WP-91): the shipped Qwen Code (ACP) profile, its image floor and its launch, pure.

Qwen Code 0.26.5's own ACP mode (``qwen --acp``) merges its system settings over a workspace's ``.qwen/settings.json``,
which merges over the user settings. So the image's ``/etc/qwen-code/settings.json`` is the floor (the OpenAI auth
type pinned to the model gateway, usage statistics, auto-update, hooks, web, memory and extension skills off, every
editing or shell tool asking), the run's tool gateway is declared once beside it in the sealed copy the driver seeds
(``QWEN_CODE_SYSTEM_SETTINGS_PATH``) with the run's settings: the model, the built-in tools the mode allows
(``tools.core``) and ``AGENTS.md`` as the context file; the user settings are empty (CF-02). The real binary under the real gateways is
``tests/unit/harness/engines/test_real_qwen_acp_conformance.py``.
"""

from __future__ import annotations

import dataclasses
import json
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.access.types import ENGINE_MODEL_ID, AccessRouteRef, ModelApi
from harness_core.engines.events import TextDelta, UsageReported
from harness_core.engines.types import EngineMode, EngineProfile, GatewayEndpoints
from harness_core.testing.engines.contract import ENDPOINTS
from harness_engines import release
from harness_engines.acp.adapter import AcpAdapter
from harness_engines.acp.launch import SESSION_POLICY_PATH, render_launch
from harness_engines.acp.mapper import AcpEventMapper
from harness_engines.acp.permissions import SessionPolicy, classify
from harness_engines.acp.profiles import (
    AcpProfileInvalid,
    McpStatus,
    parse_acp_agents,
    parse_profile,
)
from harness_engines.acp.registry import AGENTS_RESOURCE

if TYPE_CHECKING:
    from harness_core.engines.types import JsonValue
    from harness_engines.acp.profiles import AcpAgentProfile

IMAGE_ETC = Path(__file__).resolve().parents[6] / "deployment" / "sandbox-images" / "acp-qwen" / "etc"
DOCKERFILE = IMAGE_ETC.parent / "Dockerfile"
SHIPPED = resources.files("harness_engines").joinpath(AGENTS_RESOURCE).read_bytes()
ACCESS = AccessRouteRef(route_id="route-1", digest="d1")
TOKEN = "pxh-model-placeholder-0123456789"
TOOLS = "pxh-tools-placeholder-0123456789"
SKILLS = "/skills"
SETTINGS = "/state/acp/qwen/settings.json"
SYSTEM_SETTINGS = "/pxh-sealed/qwen/settings.json"
# Qwen Code 0.26.5's built-ins that keep a control plane of its own (goals, background agents and their messages,
# stopping tasks, an artifact registry, review findings).
QWEN_CONTROL_PLANE = (
    "get_goal",
    "update_goal",
    "propose_goal",
    "list_agents",
    "send_message",
    "task_stop",
    "record_artifact",
    "report_findings",
)
# Hosts Qwen Code's own providers, sign-in and statistics name by default; none may reach what it obeys.
DEFAULT_HOSTS = ("dashscope", "aliyuncs", "qwen.ai", "openai.com", "googleapis", "anthropic.com", "openrouter", "npmjs")


def _qwen() -> AcpAgentProfile:
    return next(p for p in parse_acp_agents(SHIPPED) if p.id == "qwen")


def _floor() -> dict[str, Any]:
    return json.loads((IMAGE_ETC / "settings.json").read_text())


def _endpoints() -> GatewayEndpoints:
    return dataclasses.replace(
        ENDPOINTS,
        model_base_url="http://model-gateway:8097/v1",
        tools_mcp_url="http://tool-gateway:8097/mcp",
        token_placeholder=TOKEN,
        tools_token_placeholder=TOOLS,
    )


def _launch(mode: EngineMode = EngineMode.BUILD) -> tuple[dict[str, str], dict[str, Any], dict[str, bytes]]:
    agent = _qwen()
    launch = render_launch(agent, EngineProfile(engine=agent.engine, mode=mode, access=ACCESS), _endpoints(), SKILLS)
    return dict(launch.env), json.loads(launch.files[SYSTEM_SETTINGS]), dict(launch.files)


class TestAcpQwenShipped:
    def test_acp_qwen_1_the_profile_is_its_allowlist_entry_and_its_own_image(self) -> None:
        """C2.41: Qwen Code ships as an ACP profile pinned by version, entry point digest and its own image."""
        agent = _qwen()
        entry = next(r for r in release.release_allowlist() if r.version.kind == "acp-qwen")
        assert entry.version == agent.engine and entry.image == agent.image == "pxh-qwen-tools:0.26.5-ph7"
        assert entry.name == agent.name == "Qwen Code (ACP)"
        assert (agent.agent_name, agent.agent_version) == ("qwen-code", "0.26.5")
        assert agent.argv == ("--acp",)
        assert agent.resume == "load" and agent.state_paths == ("/state/qwen",)

    def test_acp_qwen_1_the_driver_runs_the_pinned_entry_point_never_the_self_updating_launcher(self) -> None:
        """The package's ``cli-entry.js`` (its npm ``bin``) runs an update it finds under ``$QWEN_HOME/updates/npm``
        in place of the pinned tree; ``cli.js`` is the pinned entry point, launched directly, and no ``qwen`` link is
        made in the image."""
        agent = _qwen()
        assert agent.binary == "/usr/local/lib/qwen-code/cli.js"
        dockerfile = DOCKERFILE.read_text()
        assert 'sha256sum -c' in dockerfile and "/usr/local/lib/qwen-code/cli.js" in dockerfile
        assert "/usr/local/bin/qwen" not in dockerfile


class TestAcpQwenGatewayOnly:
    def test_acp_qwen_2_model_traffic_reaches_only_the_gateways_chat_wire_with_the_placeholder(self) -> None:
        env, settings, _ = _launch()
        assert env["OPENAI_BASE_URL"] == "http://model-gateway:8097/v1"
        assert env["OPENAI_API_KEY"] == TOKEN
        assert env["OPENAI_MODEL"] == settings["model"]["name"] == ENGINE_MODEL_ID
        assert _qwen().model_api is ModelApi.CHAT_COMPLETIONS
        assert AcpAdapter(_qwen()).capabilities().model_apis == frozenset({ModelApi.CHAT_COMPLETIONS})

    def test_acp_qwen_2_the_auth_type_is_pinned_and_nothing_signs_in(self) -> None:
        floor = _floor()
        assert floor["security"]["auth"] == {"selectedType": "openai", "enforcedType": "openai"}
        env, _, files = _launch()
        assert env["NO_BROWSER"] == "true"
        rendered = json.dumps({"env": env, "files": {k: v.decode() for k, v in files.items()}})
        assert not any(host in rendered for host in DEFAULT_HOSTS)
        for credential in ("DASHSCOPE_API_KEY", "QWEN_OAUTH", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
            assert credential not in rendered

    def test_acp_qwen_3_statistics_update_hooks_memory_and_unasked_edits_are_off_in_the_floor(self) -> None:
        floor = _floor()
        assert floor["privacy"]["usageStatisticsEnabled"] is False
        assert floor["general"]["enableAutoUpdate"] is False
        assert floor["ide"]["enabled"] is False
        assert floor["disableAllHooks"] is True
        assert floor["memory"] == {"enableManagedAutoMemory": False, "enableManagedAutoDream": False, "enableAutoSkill": False}
        # Qwen's own default is its LLM classifier, which runs tools unasked; default asks for edits and shell.
        assert floor["tools"]["approvalMode"] == "default"
        assert floor["ui"]["allowedApprovalModes"] == ["default", "plan"]
        assert {"web_fetch", "web_search", "agent"} <= set(floor["tools"]["exclude"])
        # Found on the binary: these three reach the model whatever tools.core says. tool_search would also hide the
        # gateway's tools behind a deferred search (tool_call); the gateway's ask_user is the run's question tool.
        assert {"ask_user_question", "tool_search", "tool_call"} <= set(floor["tools"]["exclude"])
        # WP-102, found on the binary: Qwen's own goals, background agents and artifact registry reach the model
        # whatever tools.core says. PipesHub is the run's control plane (the task, its children, its stop and its
        # files), so each is excluded by name; ``skill`` stays, it reads the run's skills.
        assert set(QWEN_CONTROL_PLANE) <= set(floor["tools"]["exclude"])
        assert floor["tools"]["toolSearch"] == {"enabled": False}
        assert floor["tools"]["discoveryCommand"] == "" and floor["tools"]["callCommand"] == ""
        assert "mcpServers" not in floor


class TestAcpQwenConfig:
    def test_cf02_the_runs_settings_are_above_a_repositorys(self) -> None:
        """CF-02: Qwen Code merges a workspace's ``.qwen/settings.json`` over its user settings (a repository's
        ``model.name`` replaced the run's on 0.26.5), so the run's settings are its system settings, the one layer
        above the workspace; the user settings hold only what Qwen UNION-merges, which a workspace cannot take back."""
        _, settings, files = _launch()
        assert json.loads(files[SETTINGS]) == {"skills": {"disabledLevels": ["project"]}}
        assert {"model", "context", "tools", "mcpServers"} <= set(settings)

    def test_adr_eng01_d1_the_repositorys_skill_folders_are_switched_off_and_the_runs_are_not(self) -> None:
        """ADR-ENG-01 D1: ``project`` is the level of a repository's ``.qwen/skills`` and ``.agents/skills``; the run's
        skills are the user level (the skills link under QWEN_HOME), and the image's floor already skips bundled and
        extension skills."""
        agent = _qwen()
        _, settings, files = _launch()
        levels = set(json.loads(files[SETTINGS])["skills"]["disabledLevels"]) | set(_floor()["skills"]["disabledLevels"])
        assert levels == {"project", "bundled", "extension"}
        assert "user" not in levels and agent.skills_link == "/state/acp/qwen/skills"
        assert "skills" not in settings or "disabledLevels" not in settings["skills"]

    def test_acp_qwen_4_the_tool_gateway_is_declared_once_in_the_sealed_system_settings(self) -> None:
        agent = _qwen()
        env, _, files = _launch()
        assert "mcpServers" not in json.loads(files[SETTINGS])
        assert {"mcpServers": json.loads(files[SYSTEM_SETTINGS])["mcpServers"]} == {
            "mcpServers": {
                "tools": {
                    "httpUrl": "http://tool-gateway:8097/mcp",
                    "headers": {"Authorization": f"Bearer {TOOLS}"},
                    "timeout": ENDPOINTS.tool_timeout_s * 1000,
                    "trust": False,
                    "alwaysLoadTools": True,
                }
            }
        }
        (sealed,) = [c for c in agent.configs if c.sealed]
        assert (sealed.path, sealed.base) == (SYSTEM_SETTINGS, "/etc/qwen-code/settings.json")
        assert env["QWEN_CODE_SYSTEM_SETTINGS_PATH"] == SYSTEM_SETTINGS
        assert agent.mcp_declare == "config"

    def test_wp102_qwen_1_the_gateways_tools_are_in_the_first_model_call(self) -> None:
        """WP-102, found on the binary: Qwen Code 0.26.5 defers every MCP tool behind its tool_search/tool_call bridge
        unless the server says ``alwaysLoadTools``, and with the bridge off (the floor) a deferred tool is never
        offered to the model. It also discovers MCP servers in the background after answering ``session/new``,
        unless ``QWEN_CODE_LEGACY_MCP_BLOCKING`` makes it connect inside the request, as Gemini CLI does: the
        gateway's tools are registered before the session's answer, and the witness is read once."""
        agent = _qwen()
        env, _, files = _launch()
        assert json.loads(files[SYSTEM_SETTINGS])["mcpServers"]["tools"]["alwaysLoadTools"] is True
        assert env["QWEN_CODE_LEGACY_MCP_BLOCKING"] == "1"
        assert agent.mcp_status == McpStatus(api="gateway", runtime="/usr/bin/node", connects="session")

    @pytest.mark.parametrize(
        ("mode", "core"),
        [
            (EngineMode.BUILD, ["read_file", "list_directory", "glob", "grep_search", "edit", "write_file", "run_shell_command", "skill"]),
            (EngineMode.PLAN, ["read_file", "list_directory", "glob", "grep_search", "skill"]),
            (EngineMode.REVIEW, ["read_file", "list_directory", "glob", "grep_search", "skill"]),
        ],
    )
    def test_acp_qwen_5_the_built_in_tools_are_exactly_what_the_mode_allows(self, mode: EngineMode, core: list[str]) -> None:
        """``tools.core`` is the allowlist of built-in tools (an empty list would keep all of them): new built-ins a
        later release adds stay off, and plan and review modes show no editing or shell tool. ACP carries no
        sub-agents, so ``agent`` is never listed."""
        _, settings, _ = _launch(mode)
        assert settings["tools"]["core"] == core

    def test_acp_qwen_6_its_home_skills_and_instructions(self) -> None:
        agent = _qwen()
        env, settings, _ = _launch()
        assert env["QWEN_HOME"] == "/state/acp/qwen" and env["QWEN_RUNTIME_DIR"] == "/state/qwen"
        assert agent.homes == ("/state/acp/qwen",)
        assert agent.skills_link == env["QWEN_HOME"] + "/skills"
        assert agent.instructions_file == env["QWEN_HOME"] + "/AGENTS.md"
        assert settings["context"]["fileName"] == ["AGENTS.md"]
        assert settings["context"]["includeDirectories"] == ["/pipeshub", SKILLS]
        assert env["QWEN_CODE_NO_RELAUNCH"] == "true"


def _qwen_policy(mode: EngineMode = EngineMode.BUILD) -> SessionPolicy:
    agent = _qwen()
    launch = render_launch(agent, EngineProfile(engine=agent.engine, mode=mode, access=ACCESS), _endpoints(), SKILLS)
    return SessionPolicy.from_json(launch.files[SESSION_POLICY_PATH])


def _qwen_ask(call_id: str, tool: str, title: str, kind: str) -> dict[str, JsonValue]:
    """What Qwen Code 0.26.5 sends in ``session/request_permission``: a display title (an MCP tool's is its
    description), the kind it mapped and the tool's own name in ``_meta.toolName``."""
    return {"toolCallId": call_id, "title": title, "kind": kind, "status": "pending", "rawInput": {}, "_meta": {"toolName": tool}}


class TestAcpQwenNamesItsToolsInMeta:
    def test_wp102_qwen_2_the_profile_says_where_qwen_names_a_tool(self) -> None:
        assert _qwen_policy().tool_name_path == ("_meta", "toolName")

    def test_wp102_qwen_2_a_gateway_tool_asked_by_its_title_is_the_gateways(self) -> None:
        """Found on the binary: Qwen asks before every MCP tool (``trust: false``) and titles the ask for display, so
        without the name in ``_meta`` the driver could not tell the gateway's tool and the turn waited on an ask
        nobody could answer."""
        ask = _qwen_ask("call_1", "mcp__tools__search", "Search the knowledge base.", "other")
        assert classify(_qwen_policy(), ask) == ("allow", "gateway_tool")

    def test_wp102_qwen_2_a_native_tool_is_judged_by_the_mode(self) -> None:
        shell = _qwen_ask("call_2", "run_shell_command", "ls -la", "execute")
        assert classify(_qwen_policy(), shell) == ("allow", "shell")
        assert classify(_qwen_policy(EngineMode.PLAN), shell)[0] == "ask"


def _usage_chunk(input_tokens: int, output_tokens: int) -> dict[str, JsonValue]:
    """Qwen Code's usage update after each model call: an empty ``agent_message_chunk`` with the call's tokens."""
    return {
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": ""},
        "_meta": {"usage": {"inputTokens": input_tokens, "outputTokens": output_tokens, "totalTokens": input_tokens + output_tokens}},
    }


class TestAcpQwenReportsUsagePerCall:
    def test_wp102_qwen_3_the_turns_usage_is_the_sum_of_its_calls(self) -> None:
        """CF-13, found on the binary: Qwen Code's ``session/prompt`` answer carries no usage; it reports each model
        call's tokens on an empty message chunk (``_meta.usage``). The profile's ``callUsagePath`` says where, and
        the turn's usage is their sum (never more than the gateway's ledger, which saw each call)."""
        assert _qwen_policy().call_usage_path == ("_meta", "usage")
        mapper = AcpEventMapper(turn_id="t-1", policy=_qwen_policy())
        events = [
            *mapper.update(_usage_chunk(4540, 8)),
            *mapper.update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "ok"}}),
            *mapper.update(_usage_chunk(4600, 1)),
            *mapper.completed({"stopReason": "end_turn"}),
        ]
        (usage,) = [e for e in events if isinstance(e, UsageReported)]
        assert (usage.input_tokens, usage.output_tokens) == (9140, 9)
        assert [e.text for e in events if isinstance(e, TextDelta)] == ["ok"]

    def test_wp102_qwen_3_an_answer_that_carries_usage_wins(self) -> None:
        mapper = AcpEventMapper(turn_id="t-1", policy=_qwen_policy())
        _ = mapper.update(_usage_chunk(10, 2))
        events = mapper.completed({"stopReason": "end_turn", "usage": {"inputTokens": 7, "outputTokens": 1}})
        (usage,) = [e for e in events if isinstance(e, UsageReported)]
        assert (usage.input_tokens, usage.output_tokens) == (7, 1)

    @pytest.mark.parametrize("path", [[], ["_meta", ""], "usage"], ids=["empty", "blank-key", "not-a-list"])
    def test_wp102_qwen_3_a_call_usage_path_that_is_not_a_key_path_is_refused(self, path: object) -> None:
        raw = next(a for a in json.loads(SHIPPED)["agents"] if a["id"] == "qwen")
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile({**raw, "callUsagePath": path})
