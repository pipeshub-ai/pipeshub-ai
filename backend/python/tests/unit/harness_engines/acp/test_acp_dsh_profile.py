"""ACP-DSH (C2.43, WP-105): the shipped DeepSeek Harness profile, its image and its launch, pure.

DeepSeek Harness 0.2.2-rc.1 (``@deepseek-ai/dsh``, MIT) is a stack of plugin patch layers over its bundles. Its ACP
profile (``dsh --profile acp``) is the automation surface: ``initialize``, ``session/new``/``resume``/``close``,
``session/prompt`` and ``cancel``, ``session/request_permission``, HTTP MCP servers. The profile renders one patch,
passed as ``--patch`` (the top layer: above its bundles and anything under its home, which lives in /tmp): the model
on pi-ai's openai-completions route to the model gateway, DeepSeek's account, keys, telemetry and request extensions
off, its own control plane (goals, sub-agents, workflows, jobs, plan mode, web, plugin manager) off, the run's mode
as plugin switches, and the run's skills as its only skill root. The real binary under the real gateways is
``tests/unit/harness/engines/test_real_dsh_acp_conformance.py``.
"""

from __future__ import annotations

import dataclasses
import json
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from harness_core.access.types import ENGINE_MODEL_ID, AccessRouteRef, ModelApi
from harness_core.engines.types import EngineMode, EngineProfile, GatewayEndpoints
from harness_core.testing.engines.contract import ENDPOINTS
from harness_engines import release
from harness_engines.acp.adapter import AcpAdapter
from harness_engines.acp.launch import render_launch
from harness_engines.acp.profiles import parse_acp_agents
from harness_engines.acp.registry import AGENTS_RESOURCE

if TYPE_CHECKING:
    from harness_engines.acp.profiles import AcpAgentProfile

IMAGE = Path(__file__).resolve().parents[6] / "deployment" / "sandbox-images" / "acp-dsh"
ALLOWLIST = Path(__file__).resolve().parents[4] / "harness_engines" / "allowlist.json"
SHIPPED = resources.files("harness_engines").joinpath(AGENTS_RESOURCE).read_bytes()
ACCESS = AccessRouteRef(route_id="route-1", digest="d1")
TOKEN = "pxh-model-placeholder-0123456789"
TOOLS = "pxh-tools-placeholder-0123456789"
PATCH = "/state/acp/dsh/patch.yml"
# dsh's plugin rows that reach DeepSeek or keep a control plane of its own; each is off by id in the rendered patch.
OFF = (
    "otel",
    "session-telemetry-otel",
    "deepseek-account",
    "llm-deepseek-account",
    "llm-deepseek",
    "deepseek-llm-api-extensions",
    "session-log-deepseek",
    "plugin-package-inventory-deepseek",
    "plugin-manager",
    "remote-access",
    "typert-gateway",
    "web",
    "web-search-deepseek",
    "web-deepseek-api",
    "web-fetch-http",
    "tool-web",
    "goal",
    "goal-round-driver",
    "command-goal",
    "tool-goal",
    "subagent",
    "subagent-spawn-in-process",
    "subagent-fork-in-process",
    "tool-subagent-control",
    "tool-subagent",
    "tool-subagent-fork",
    "workflow-ptc",
    "tool-workflow",
    "ptc-runtime",
    "jobs",
    "tool-jobs",
    "plan-mode",
    "mcp-resources",
)


def _dsh() -> AcpAgentProfile:
    return next(p for p in parse_acp_agents(SHIPPED) if p.id == "dsh")


def _endpoints() -> GatewayEndpoints:
    return dataclasses.replace(
        ENDPOINTS,
        model_base_url="http://model-gateway:8097/v1",
        tools_mcp_url="http://tool-gateway:8097/mcp",
        token_placeholder=TOKEN,
        tools_token_placeholder=TOOLS,
    )


def _launch(mode: EngineMode = EngineMode.BUILD) -> tuple[dict[str, str], dict[str, dict[str, Any]], dict[str, bytes]]:
    agent = _dsh()
    launch = render_launch(agent, EngineProfile(engine=agent.engine, mode=mode, access=ACCESS), _endpoints(), "/skills")
    rows = json.loads(launch.files[PATCH])
    return dict(launch.env), {row["id"]: row for row in rows}, dict(launch.files)


class TestAcpDshShipped:
    def test_acp_dsh_1_the_profile_is_its_allowlist_entry_and_its_own_image(self) -> None:
        agent = _dsh()
        entry = next(r for r in release.release_allowlist() if r.version.kind == "acp-dsh")
        assert entry.version == agent.engine and entry.image == agent.image == "pxh-dsh-tools:0.2.2-rc.1-ph7"
        assert entry.name == agent.name == "DeepSeek Harness"
        assert (agent.agent_name, agent.agent_version) == ("deepseek-harness-acp", "0.2.2-rc.1")
        # Its ACP server reports 0.0.1 whatever the release (found on the binary); the pin is the release and digest.
        assert agent.reported_version == "0.0.1"
        assert agent.argv == ("--profile", "acp", "--patch", PATCH)

    def test_acp_dsh_1_the_pinned_entry_point_and_its_tree_are_what_the_image_installs(self) -> None:
        agent = _dsh()
        assert agent.binary == "/usr/local/lib/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js"
        dockerfile = (IMAGE / "Dockerfile").read_text()
        assert "npm ci --omit=dev --ignore-scripts" in dockerfile and agent.binary in dockerfile
        assert dockerfile.count("sha256sum -c") >= 2
        raw = next(e for e in json.loads(ALLOWLIST.read_text())["engines"] if e["kind"] == "acp-dsh")
        lock = json.loads((IMAGE / raw["asset"]["lock"]).read_text())
        pinned = lock["packages"]["node_modules/@deepseek-ai/dsh"]
        assert pinned["version"] == agent.agent_version
        assert pinned["integrity"].startswith("sha512-ijR9UEVYkG15Q")
        # Every package of the tree is pinned by its integrity; none comes from anywhere but the npm registry.
        packages = [v for k, v in lock["packages"].items() if k]
        assert all(p.get("integrity", "").startswith("sha512-") for p in packages)
        assert all(p["resolved"].startswith("https://registry.npmjs.org/") for p in packages)


class TestAcpDshGatewayOnly:
    def test_acp_dsh_2_model_traffic_reaches_only_the_gateways_chat_wire_with_the_placeholder(self) -> None:
        env, rows, _ = _launch()
        (route,) = rows["llm-pi-ai"]["config"]["providers"].values()
        assert route["api"] == "openai-completions" and route["baseURL"] == "http://model-gateway:8097/v1"
        assert route["apiKeyEnv"] == "HARNESS_GATEWAY_TOKEN" and env["HARNESS_GATEWAY_TOKEN"] == TOKEN
        # DeepSeek's thinking format: reasoning goes back as reasoning_content on tool turns (MG-05).
        assert route["compat"] == {"thinkingFormat": "deepseek"}
        assert [m["id"] for m in route["models"]] == [ENGINE_MODEL_ID]
        assert rows["acp"]["config"] == rows["agent-default-model"]["config"] == {"provider": "gateway", "model": ENGINE_MODEL_ID}
        assert _dsh().model_api is ModelApi.CHAT_COMPLETIONS
        assert AcpAdapter(_dsh()).capabilities().model_apis == frozenset({ModelApi.CHAT_COMPLETIONS})

    def test_acp_dsh_2_no_deepseek_host_key_or_account_is_named(self) -> None:
        env, _, files = _launch()
        rendered = json.dumps({"env": env, "files": {k: v.decode() for k, v in files.items()}})
        for named in ("deepseek.com", "deepseeksvc", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "otel-collector"):
            assert named not in rendered


class TestAcpDshControlPlaneOff:
    def test_acp_dsh_3_telemetry_account_extensions_and_its_own_control_plane_are_off(self) -> None:
        env, rows, _ = _launch()
        assert all(rows[row]["disabled"] is True for row in OFF), [r for r in OFF if rows[r].get("disabled") is not True]
        assert env["DSH_TELEMETRY_DISABLED"] == "1"

    def test_acp_dsh_3_native_addons_load_from_the_read_only_image(self) -> None:
        """Found on the image: the addon loader copies each .node into /tmp before loading it, and /tmp is noexec in an
        engine sandbox ("failed to map segment"), so dsh died before initialize; it loads them where they are."""
        env, _, _ = _launch()
        assert env["NARB_DISABLE_NATIVE_CACHE"] == "1"

    def test_acp_dsh_3_the_engine_sandbox_is_the_boundary_and_asks_reach_the_driver(self) -> None:
        env, rows, _ = _launch()
        # CF-18: no inner sandbox inside the engine sandbox, as Codex's danger-full-access; an ask dsh sends anyway is
        # the driver's to decide (session/request_permission).
        assert rows["sandbox-policy"]["config"] == {"mode": "danger-full-access", "workspaceRoot": "/workspace"}
        assert rows["approval"]["config"] == {"policy": "ask"}
        # Pinned in the launch, so a repository's .env (dsh fills only what the launcher left unset) changes neither.
        assert env["DSH_PERMISSION_MODE"] == "danger-full-access"

    def test_acp_dsh_3_its_home_is_never_captured_and_the_patch_is_the_top_layer(self) -> None:
        agent = _dsh()
        env, rows, _ = _launch()
        # dsh rewrites its profile folder at every boot, so its home cannot be read-only; it lives in /tmp, outside
        # what a checkpoint captures, and the root-owned --patch is applied after it.
        assert env["DSH_HOME"].startswith("/tmp/")
        assert agent.state_paths == ("/state/dsh/sessions",)
        assert rows["session-persistence-jsonl"]["config"] == {"root": "/state/dsh/sessions"}
        assert rows["storage-json"]["config"]["root"].startswith("/tmp/")
        assert any(c.path == PATCH for c in agent.configs)


class TestAcpDshTools:
    def test_acp_dsh_4_build_has_the_shell_and_file_writes(self) -> None:
        _, rows, _ = _launch(EngineMode.BUILD)
        assert rows["tool-bash"]["disabled"] is False
        assert (rows["tool-fs"]["config"]["write"], rows["tool-fs"]["config"]["edit"]) == (True, True)
        assert rows["tool-fs"]["config"]["applyPatch"] is False

    def test_acp_dsh_4_plan_has_neither(self) -> None:
        _, rows, _ = _launch(EngineMode.PLAN)
        assert rows["tool-bash"]["disabled"] is True
        assert (rows["tool-fs"]["config"]["write"], rows["tool-fs"]["config"]["edit"]) == (False, False)
        assert rows["tool-fs"]["config"]["read"] is True

    def test_acp_dsh_4_native_names_are_dshs(self) -> None:
        agent = _dsh()
        assert {t.value: list(n) for t, n in agent.native_tools.items()} == {
            "read": ["read", "read_image"],
            "search": ["glob", "grep"],
            "edit": ["write", "edit"],
            "shell": ["bash"],
            "skill": ["skill"],
        }
        assert agent.plan_tool == "todo_write"
        assert agent.exposed_tool_name("search") == "mcp__tools__search"


class TestAcpDshSkillsAndInstructions:
    def test_acp_dsh_5_only_the_runs_skills_and_its_instructions_as_the_shared_agents_home(self) -> None:
        agent = _dsh()
        env, rows, _ = _launch()
        assert rows["skill-filesystem"]["config"] == {"includeDefaultRoots": False, "customSkillDirs": ["/skills"], "watch": False}
        assert env["DSH_AGENTS_HOME"] == "/state/acp/dsh-agents"
        assert agent.instructions_file == "/state/acp/dsh-agents/AGENTS.md"

    def test_cf02_no_project_instruction_file_is_read(self) -> None:
        """dsh's agent-instructions plugin reads AGENTS.md, CLAUDE.md and their .local overlays from every folder
        between the project root and its working folder by default, into the same reminder as the run's
        instructions; the patch leaves it the shared agents home's file alone."""
        _, rows, _ = _launch()
        assert rows["agent-instructions"]["config"] == {
            "maxBytes": 65536,
            "instructionFileCandidates": [],
            "localInstructionFileCandidates": [],
        }


class TestAcpDshSession:
    def test_acp_dsh_6_sessions_resume_by_session_resume_from_its_log(self) -> None:
        agent = _dsh()
        assert agent.resume == "resume" and agent.resumes_from_files
        assert agent.session_record == "dsh"
        assert agent.mcp_status is not None and agent.mcp_status.runtime == "/usr/local/bin/node"
        assert agent.mcp_declare == "session"
