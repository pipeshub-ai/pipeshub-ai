"""ACP-GOOSE-7 (C2.41): an agent that names its tools only in a tool call's ``_meta`` and reports every kind ``other``.

Goose 1.57.0 titles a tool call for display ("tools: search · …", underscores turned to spaces), always sends ACP's
default kind ``other``, and names the tool only in ``_meta.goose.toolCall.toolName`` of its ``tool_call``; its
``session/request_permission`` for that call carries the display title alone. The profile's ``toolNamePath`` says
where the name is; the driver remembers it per call id, so the ask is decided by the tool's real name, and a native
tool the profile names takes that tool's ACP kind (a ``shell`` call is a command).
"""

from __future__ import annotations

import json
from importlib import resources
from typing import TYPE_CHECKING, cast

import pytest

from harness_core.access.types import AccessRouteRef
from harness_core.engines.events import CommandFinished, CommandStarted, ToolCallSeen
from harness_core.engines.types import EngineMode, EngineProfile
from harness_core.testing.engines.contract import ENDPOINTS
from harness_engines.acp.launch import SESSION_POLICY_PATH, render_launch
from harness_engines.acp.mapper import AcpEventMapper
from harness_engines.acp.permissions import SessionPolicy, classify
from harness_engines.acp.profiles import (
    AcpProfileInvalid,
    parse_acp_agents,
    parse_profile,
)
from harness_engines.acp.registry import AGENTS_RESOURCE

if TYPE_CHECKING:
    from harness_core.engines.types import JsonValue

SHIPPED = resources.files("harness_engines").joinpath(AGENTS_RESOURCE).read_bytes()
ACCESS = AccessRouteRef(route_id="route-1", digest="d1")


def _policy(mode: EngineMode = EngineMode.BUILD) -> SessionPolicy:
    agent = next(p for p in parse_acp_agents(SHIPPED) if p.id == "goose")
    launch = render_launch(agent, EngineProfile(engine=agent.engine, mode=mode, access=ACCESS), ENDPOINTS, "/skills")
    return SessionPolicy.from_json(launch.files[SESSION_POLICY_PATH])


def _goose_call(call_id: str, tool: str, title: str, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {
        "sessionUpdate": "tool_call",
        "toolCallId": call_id,
        "title": title,
        "kind": "other",
        "status": "pending",
        "rawInput": cast("JsonValue", args),
        "_meta": {"goose": {"toolCall": {"toolName": tool}}},
    }


def _ask(call_id: str, title: str) -> dict[str, JsonValue]:
    """What Goose sends in ``session/request_permission``: no name, no meta, kind ``other``."""
    return {"toolCallId": call_id, "title": title, "kind": "other", "status": "pending", "rawInput": {}}


class TestAcpGooseNamesItsToolsInMeta:
    def test_acp_goose_7_the_profile_says_where_goose_names_a_tool(self) -> None:
        assert _policy().tool_name_path == ("_meta", "goose", "toolCall", "toolName")

    @pytest.mark.parametrize("path", [[], ["_meta", ""], "toolName", [1]], ids=["empty", "blank-key", "not-a-list", "not-strings"])
    def test_acp_goose_7_a_tool_name_path_that_is_not_a_key_path_is_refused(self, path: object) -> None:
        raw = cast("dict[str, object]", next(a for a in json.loads(SHIPPED)["agents"] if a["id"] == "goose"))
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile({**raw, "toolNamePath": path})

    def test_acp_goose_7_a_gateway_tool_asked_by_its_display_title_is_the_gateways(self) -> None:
        mapper = AcpEventMapper(turn_id="t-1", policy=_policy())
        (seen,) = mapper.update(_goose_call("c1", "tools__search", "tools: search · q", {"q": 1}))
        assert isinstance(seen, ToolCallSeen) and seen.tool == "search"
        ask = _ask("c1", "tools: search · q")
        assert classify(_policy(), ask)[0] == "ask"
        assert classify(_policy(), mapper.named(ask)) == ("allow", "gateway_tool")

    def test_acp_goose_7_a_shell_call_is_a_command_and_is_allowed_by_the_mode(self) -> None:
        mapper = AcpEventMapper(turn_id="t-1", policy=_policy())
        (started,) = mapper.update(_goose_call("c2", "shell", "shell · ls -la", {"command": "ls -la"}))
        assert isinstance(started, CommandStarted) and started.command == "ls -la"
        assert classify(_policy(), mapper.named(_ask("c2", "shell · ls -la"))) == ("allow", "shell")
        finished = mapper.update(
            {"sessionUpdate": "tool_call_update", "toolCallId": "c2", "status": "completed", "content": [
                {"type": "content", "content": {"type": "text", "text": "total 0"}}
            ]}
        )
        assert [type(e) for e in finished if isinstance(e, CommandFinished)] == [CommandFinished]

    def test_acp_goose_7_plan_mode_refuses_the_shell_and_a_repositorys_tool_whatever_its_title(self) -> None:
        plan = AcpEventMapper(turn_id="t-1", policy=_policy(EngineMode.PLAN))
        plan.update(_goose_call("c3", "shell", "shell · rm -rf x", {"command": "rm -rf x"}))
        assert classify(_policy(EngineMode.PLAN), plan.named(_ask("c3", "shell · rm -rf x"))) == ("ask", "shell")
        mapper = AcpEventMapper(turn_id="t-1", policy=_policy())
        mapper.update(_goose_call("c4", "repo__run", "tools: search · x", {}))
        assert classify(_policy(), mapper.named(_ask("c4", "tools: search · x")))[0] == "ask"

    def test_acp_goose_7_an_ask_for_a_call_never_announced_is_judged_as_sent(self) -> None:
        mapper = AcpEventMapper(turn_id="t-1", policy=_policy())
        ask = _ask("c9", "tools__search")
        assert mapper.named(ask) == ask


class TestAcpGooseGatewayEntry:
    def test_acp_goose_9_the_session_sends_the_gateway_with_the_profiles_meta(self) -> None:
        """ACP's HTTP server entry has ``_meta``; the profile's ``mcp.meta`` (rendered per run) goes there, and the
        entry is otherwise the one every session-declaring agent gets."""
        from harness_engines.acp.session import mcp_server_entry

        policy = _policy()
        (entry,) = mcp_server_entry(policy)
        assert entry == {
            "type": "http",
            "name": "tools",
            "url": policy.tools_url,
            "headers": [{"name": "Authorization", "value": f"Bearer {policy.tools_placeholder}"}],
            "_meta": {"timeout": ENDPOINTS.tool_timeout_s},
        }

    def test_acp_goose_9_an_mcp_meta_that_is_not_an_object_is_refused(self) -> None:
        raw = cast("dict[str, object]", next(a for a in json.loads(SHIPPED)["agents"] if a["id"] == "goose"))
        mcp = dict(cast("dict[str, object]", raw["mcp"]))
        mcp["meta"] = ["timeout"]
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile({**raw, "mcp": mcp})
