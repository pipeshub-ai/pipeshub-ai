"""WP-13 (C3.05, C3.53; WS-05, WS-06, GA-23): the shipped Gemini CLI (ACP) profile and its launch, pure.

What Gemini CLI obeys is rendered from the profile: its ``~/.gemini/settings.json`` (``mcpServers`` naming the tool
gateway, ``context.fileName`` AGENTS.md, the auth type pinned to the model gateway, the built-in tools the run's mode
allows), the env that points its model client at the gateway's Gemini wire, and the root-owned skills link in its
``~/.gemini/skills``. The
real binary under the real gateways is ``tests/unit/harness/engines/test_real_gemini_acp_conformance.py``.
"""

from __future__ import annotations

import dataclasses
import io
import json
import tarfile
import tomllib
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from harness_core.access.types import ENGINE_MODEL_ID, AccessRouteRef, ModelApi
from harness_core.engines.events import ApprovalRequested, ToolCallSeen, UsageReported
from harness_core.engines.policy import NativeTool
from harness_core.engines.types import EngineMode, EngineProfile, GatewayEndpoints
from harness_core.gateway.asking import ASK_USER, ASK_USER_ALIAS, ask_user_tool
from harness_core.sandbox.tar import config_tar
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
    from harness_engines.acp.profiles import AcpAgentProfile

IMAGE_ETC = Path(__file__).resolve().parents[6] / "deployment" / "sandbox-images" / "acp-gemini" / "etc"
SHIPPED = resources.files("harness_engines").joinpath(AGENTS_RESOURCE).read_bytes()
ACCESS = AccessRouteRef(route_id="route-1", digest="d1")
TOKEN = "pxh-model-placeholder-0123456789"
TOOLS = "pxh-tools-placeholder-0123456789"
SKILLS = "/skills"
SETTINGS = "/state/acp/gemini/.gemini/settings.json"
SYSTEM_SETTINGS = "/pxh-sealed/gemini/settings.json"
GOOGLE_HOSTS = ("googleapis.com", "google.com", "accounts.google", "oauth2", "cloudcode-pa", "play.googleapis")


def _gemini() -> AcpAgentProfile:
    return next(p for p in parse_acp_agents(SHIPPED) if p.id == "gemini")


def _endpoints() -> GatewayEndpoints:
    return dataclasses.replace(
        ENDPOINTS,
        model_base_url="http://model-gateway:8097/v1",
        model_api=ModelApi.GEMINI_GENERATE_CONTENT,
        tools_mcp_url="http://tool-gateway:8097/mcp",
        token_placeholder=TOKEN,
        tools_token_placeholder=TOOLS,
    )


def _launch(mode: EngineMode = EngineMode.BUILD) -> tuple[dict[str, str], dict[str, Any]]:
    agent = _gemini()
    launch = render_launch(agent, EngineProfile(engine=agent.engine, mode=mode, access=ACCESS), _endpoints(), SKILLS)
    return dict(launch.env), json.loads(launch.files[SYSTEM_SETTINGS])


class TestWS05GeminiNativeConfig:
    def test_cf02_the_runs_settings_are_above_a_repositorys(self) -> None:
        """CF-02: Gemini CLI merges a trusted workspace's ``.gemini/settings.json`` over its user settings (a
        repository's ``model.name`` replaced the run's on 0.63.0), so the run's settings are its system settings, the
        one layer above the workspace; the user settings are empty."""
        agent = _gemini()
        launch = render_launch(agent, EngineProfile(engine=agent.engine, mode=EngineMode.BUILD, access=ACCESS), _endpoints(), SKILLS)
        assert json.loads(launch.files[SETTINGS]) == {}
        system = json.loads(launch.files[SYSTEM_SETTINGS])
        assert {"general", "model", "context", "tools", "skills", "mcpServers"} <= set(system)

    def test_ws05_settings_json_names_the_tool_gateway_as_its_only_mcp_server(self) -> None:
        """WP-59 round 3: the gateway is declared once, in Gemini's system settings under ``SEALED_DIR`` (the one
        layer above a repository's ``.gemini/settings.json``), beside the image's own system settings (``base``)."""
        agent = _gemini()
        launch = render_launch(agent, EngineProfile(engine=agent.engine, mode=EngineMode.BUILD, access=ACCESS), _endpoints(), SKILLS)
        assert "mcpServers" not in json.loads(launch.files[SETTINGS])
        assert {"mcpServers": json.loads(launch.files[SYSTEM_SETTINGS])["mcpServers"]} == {
            "mcpServers": {
                "tools": {
                    "httpUrl": "http://tool-gateway:8097/mcp",
                    "headers": {"Authorization": f"Bearer {TOOLS}"},
                    "timeout": ENDPOINTS.tool_timeout_s * 1000,
                    "trust": False,
                }
            }
        }
        (sealed,) = [c for c in agent.configs if c.sealed]
        assert (sealed.path, sealed.base) == (SYSTEM_SETTINGS, "/etc/gemini-cli/settings.json")
        assert launch.env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] == SYSTEM_SETTINGS
        floor = json.loads((IMAGE_ETC / "settings.json").read_text())
        assert floor["mcp"]["allowed"] == ["tools"] and "mcpServers" not in floor

    def test_ws05_context_file_name_is_agents_md_and_the_run_instructions_are_its_global_one(self) -> None:
        agent = _gemini()
        _, settings = _launch()
        assert settings["context"]["fileName"] == ["AGENTS.md"]
        assert settings["context"]["includeDirectories"] == ["/pipeshub", SKILLS]
        assert agent.instructions_file == "/state/acp/gemini/.gemini/AGENTS.md"
        env, _ = _launch()
        assert env["GEMINI_CLI_HOME"] + "/.gemini/AGENTS.md" == agent.instructions_file

    def test_ws05_the_model_client_reaches_only_the_gateways_gemini_wire_with_the_placeholder(self) -> None:
        env, settings = _launch()
        assert env["GOOGLE_GEMINI_BASE_URL"] == "http://model-gateway:8097"
        assert env["GEMINI_CLI_CUSTOM_HEADERS"] == f"Authorization: Bearer {TOKEN}"
        assert settings["model"]["name"] == ENGINE_MODEL_ID
        assert _gemini().model_api is ModelApi.GEMINI_GENERATE_CONTENT
        assert AcpAdapter(_gemini()).capabilities().model_apis == frozenset({ModelApi.GEMINI_GENERATE_CONTENT})

    def test_ws05_its_settings_are_rendered_where_it_reads_them_and_the_image_holds_the_floor(self) -> None:
        """The user settings are rendered (root's, read-only, empty). The workspace is trusted, since Gemini CLI starts
        no MCP server otherwise, so what a repository's ``.gemini`` must never change is in the system settings, which
        merge over workspace settings (WS-07, CF-02)."""
        agent = _gemini()
        launch = render_launch(agent, EngineProfile(engine=agent.engine, mode=EngineMode.BUILD, access=ACCESS), _endpoints(), SKILLS)
        env = dict(launch.env)
        assert env["GEMINI_CLI_HOME"] + "/.gemini/settings.json" == SETTINGS
        assert env["GEMINI_CLI_TRUST_WORKSPACE"] == "true" and env["GEMINI_CLI_NO_RELAUNCH"] == "true"
        # The system settings are the sealed copy of the image's (WP-59); nothing else is moved.
        assert [name for name in env if name.startswith("GEMINI_CLI_SYSTEM")] == ["GEMINI_CLI_SYSTEM_SETTINGS_PATH"]
        floor = json.loads((IMAGE_ETC / "settings.json").read_text())
        assert floor["hooksConfig"] == {"enabled": False}
        assert floor["mcp"]["allowed"] == [agent.mcp_server]
        assert floor["advanced"]["ignoreLocalEnv"] is True
        assert floor["admin"]["extensions"]["enabled"] is False
        assert floor["experimental"]["enableAgents"] is False
        assert floor["tools"]["discoveryCommand"] == "" and floor["tools"]["callCommand"] == ""
        assert SESSION_POLICY_PATH in launch.files

    def test_ws05_a_repository_cannot_let_an_editing_tool_run_unasked(self) -> None:
        """Every editing or shell tool asks the driver (``confirmationRequired``, which the image's system settings
        hold, so neither a repository's settings nor its tier-3 policies change it), and nothing is pre-allowed.
        Gemini CLI's user policy folder is root's, so no rule is added there either."""
        floor = json.loads((IMAGE_ETC / "settings.json").read_text())
        assert floor["tools"]["confirmationRequired"] == ["replace", "write_file", "run_shell_command"]
        assert floor["tools"]["allowed"] == []
        policy = tomllib.loads((IMAGE_ETC / "policies" / "pipeshub.toml").read_text())
        (rule,) = policy["rule"]
        assert rule["decision"] == "deny"
        assert {"web_fetch", "google_web_search", "invoke_agent", "enter_plan_mode"} <= set(rule["toolName"])
        agent = _gemini()
        launch = render_launch(agent, EngineProfile(engine=agent.engine, mode=EngineMode.BUILD, access=ACCESS), _endpoints(), SKILLS)
        assert launch.files["/state/acp/gemini/.gemini/policies/run.toml"].startswith(b"#")

    def test_ws05_nothing_but_placeholders_and_no_google_host_in_what_it_obeys(self) -> None:
        agent = _gemini()
        launch = render_launch(agent, EngineProfile(engine=agent.engine, mode=EngineMode.BUILD, access=ACCESS), _endpoints(), SKILLS)
        rendered = json.dumps({"env": dict(launch.env), "files": {k: v.decode() for k, v in launch.files.items()}})
        assert not any(host in rendered for host in GOOGLE_HOSTS)
        for credential in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_GENAI_USE"):
            assert credential not in rendered


class TestGA23GeminiSignsInNowhere:
    def test_ga23_the_auth_type_is_pinned_to_the_gateway(self) -> None:
        floor = json.loads((IMAGE_ETC / "settings.json").read_text())
        assert floor["security"]["auth"] == {"selectedType": "gateway", "enforcedType": "gateway"}
        assert floor["privacy"]["usageStatisticsEnabled"] is False
        assert floor["telemetry"]["enabled"] is False
        assert floor["general"]["enableAutoUpdate"] is False
        env, _ = _launch()
        assert env["NO_BROWSER"] == "true"

    def test_ga23_the_driver_never_sends_authenticate(self) -> None:
        """ACP's ``authenticate`` is how a client would sign Gemini CLI in (``oauth-personal`` among its methods):
        the driver has no such request, so the only credential is the proxy's placeholder."""
        import inspect

        from harness_engines.acp import adapter, protocol, rpc, session

        for module in (adapter, protocol, rpc, session):
            assert '"authenticate"' not in inspect.getsource(module)


class TestWS06GeminiSkills:
    def test_ws06_skills_are_linked_into_gemini_user_skills_and_its_bundled_ones_are_off(self) -> None:
        agent = _gemini()
        env, settings = _launch()
        assert agent.skills_link == env["GEMINI_CLI_HOME"] + "/.gemini/skills"
        assert env[agent.gateway_env["skillsDir"]] == SKILLS
        assert settings["skills"] == {"enabled": True}
        floor = json.loads((IMAGE_ETC / "settings.json").read_text())
        assert floor["skills"]["disabled"] == ["skill-creator", "antigravity-support"]
        assert AcpAdapter(agent).capabilities().native_skills is True

    def test_ws06_the_link_is_roots_inside_a_home_whose_parents_are_roots(self) -> None:
        agent = _gemini()
        assert agent.skills_link is not None
        homes = ("/state/acp", *agent.homes)
        tar = _tar({SETTINGS: b"{}"}, homes=homes, links={agent.skills_link: SKILLS})
        members = {m.name: m for m in tar.getmembers()}
        link = members["state/acp/gemini/.gemini/skills"]
        assert link.issym() and link.linkname == SKILLS and link.uid == 0
        assert members["state/acp/gemini/.gemini"].mode == 0o1777
        # A folder the user made earlier (restored state) becomes root's, so the home cannot be renamed away.
        assert members["state/acp/gemini"].isdir() and members["state/acp/gemini"].mode == 0o755
        assert members["state/acp/gemini/.gemini/settings.json"].mode == 0o444


def _tar(files: dict[str, bytes], *, homes: tuple[str, ...], links: dict[str, str]) -> tarfile.TarFile:
    import asyncio

    async def read() -> bytes:
        return b"".join([chunk async for chunk in config_tar(
            {k.removeprefix("/"): v for k, v in files.items()},
            homes=[h.removeprefix("/") for h in homes],
            links={k.removeprefix("/"): v for k, v in links.items()},
            own_parents=True,
        )])

    return tarfile.open(fileobj=io.BytesIO(asyncio.run(read())))


class TestModesAndAsks:
    def test_the_mode_excludes_its_denied_tools_and_web_and_sub_agents_always(self) -> None:
        """``tools.exclude`` (a union: a repository's settings add to it, never remove from it) holds what the run's
        mode denies; web search and fetch never come from the engine (FG2) and ACP carries no sub-agent sessions."""
        _, build = _launch(EngineMode.BUILD)
        _, plan = _launch(EngineMode.PLAN)
        assert build["tools"]["exclude"] == ["web_fetch", "google_web_search", "invoke_agent"]
        assert plan["tools"]["exclude"] == [
            "web_fetch",
            "google_web_search",
            "replace",
            "write_file",
            "run_shell_command",
            "list_background_processes",
            "read_background_output",
            "invoke_agent",
        ]

    def test_a_gateway_tool_named_only_in_its_call_id_is_allowed_and_shown_by_name(self) -> None:
        agent = _gemini()
        launch = render_launch(agent, EngineProfile(engine=agent.engine, mode=EngineMode.BUILD, access=ACCESS), _endpoints(), SKILLS)
        policy = SessionPolicy.from_json(launch.files[SESSION_POLICY_PATH])
        call = {"toolCallId": "mcp_tools_search__c1", "title": "search (tools MCP Server)", "kind": "other"}
        assert classify(policy, call) == ("allow", "gateway_tool")
        mapper = AcpEventMapper(turn_id="t-1", policy=policy)
        (seen,) = mapper.update({"sessionUpdate": "tool_call", "status": "in_progress", **call})
        assert isinstance(seen, ToolCallSeen) and seen.tool == "search"
        web = {"toolCallId": "web_fetch__c2", "title": "https://example.com", "kind": "fetch"}
        assert classify(policy, web)[0] == "ask"
        asked = mapper.ask("r1", web, "web")
        assert isinstance(asked, ApprovalRequested) and asked.patterns == ("web_fetch",)

    def test_the_gateways_ask_user_is_offered_under_a_name_gemini_keeps(self) -> None:
        """Gemini CLI 0.63.0 excludes its own ``ask_user`` in ACP mode and drops an MCP tool by its unprefixed name
        too, so the engine reserves the bare name and the gateway's question tool travels under its alias."""
        agent = _gemini()
        assert agent.reserved_tool_names == frozenset({ASK_USER})
        reserved = AcpAdapter(agent).capabilities().reserved_tool_names
        assert ask_user_tool(reserved).name == ASK_USER_ALIAS
        assert agent.exposed_tool_name(ASK_USER_ALIAS) == "mcp_tools_pipeshub_ask_user"
        assert ask_user_tool().name == ASK_USER

    def test_e2e3_f6_the_driver_asks_the_gateway_itself_with_geminis_own_runtime(self) -> None:
        """Gemini CLI has no MCP status API and never retries a gateway that refused it inside ``session/new``
        (WP-59): the driver lists the gateway's tools before it opens a session, with the Node that runs Gemini."""
        status = _gemini().mcp_status
        assert status == McpStatus(api="gateway", runtime="/usr/bin/node")
        assert status is not None and status.before_session

    def test_the_turns_usage_comes_from_gemini_quota_meta(self) -> None:
        agent = _gemini()
        launch = render_launch(agent, EngineProfile(engine=agent.engine, mode=EngineMode.BUILD, access=ACCESS), _endpoints(), SKILLS)
        mapper = AcpEventMapper(turn_id="t-1", policy=SessionPolicy.from_json(launch.files[SESSION_POLICY_PATH]))
        events = mapper.completed(
            {"stopReason": "end_turn", "_meta": {"quota": {"token_count": {"input_tokens": 120, "output_tokens": 30}}}}
        )
        (usage,) = [e for e in events if isinstance(e, UsageReported)]
        assert (usage.input_tokens, usage.output_tokens) == (120, 30)


class TestShippedAndParsed:
    def test_the_gemini_profile_is_its_allowlist_entry(self) -> None:
        agent = _gemini()
        entry = next(r for r in release.release_allowlist() if r.version.kind == "acp-gemini")
        assert entry.version == agent.engine and entry.image == agent.image
        assert agent.binary == "/usr/local/lib/gemini-cli/gemini.js" and agent.argv == ("--acp",)
        assert agent.resume == "load" and agent.state_paths == ("/state/acp/gemini/.gemini/tmp",)
        assert set(agent.native_tools) == {
            NativeTool.READ,
            NativeTool.SEARCH,
            NativeTool.EDIT,
            NativeTool.SHELL,
            NativeTool.SKILL,
            NativeTool.SUBAGENT,
        }

    @pytest.mark.parametrize(
        "change",
        [
            {"skillsLink": "/etc/skills"},
            {"homes": ["/state/elsewhere"]},
            {"callIdNameSeparator": ""},
            {"usagePath": []},
            {"gatewayEnv": {"modelToken": "HARNESS_GATEWAY_TOKEN"}},
        ],
        ids=["link-outside-config", "home-outside-config", "empty-separator", "empty-usage-path", "link-without-skills-env"],
    )
    def test_a_profile_outside_the_rules_is_refused(self, change: dict[str, object]) -> None:
        raw = cast("dict[str, object]", next(a for a in json.loads(SHIPPED)["agents"] if a["id"] == "gemini"))
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile({**raw, **change})

    def test_a_denied_form_needs_a_key_path(self) -> None:
        raw = cast("dict[str, object]", next(a for a in json.loads(SHIPPED)["agents"] if a["id"] == "gemini"))
        configs = [dict(c) for c in cast("list[dict[str, object]]", raw["configs"])]
        (ruled,) = [c for c in configs if "toolRules" in c]
        ruled["toolRules"] = []
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile({**raw, "configs": configs})
