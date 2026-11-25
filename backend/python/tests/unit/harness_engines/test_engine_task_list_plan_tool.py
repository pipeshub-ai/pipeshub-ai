"""FIX-P2 C3: every engine is given its own task-list tool in every mode, and its plan reaches the timeline as
the same ``PlanUpdated``.

Codex 0.160 offers ``update_plan`` only when ``[tools.update_plan] enabled = true`` (verified on the pinned
binary: ``UpdatePlanToolConfig``, a struct with one boolean); rendered without it, no Codex request offered the
tool, so a Codex plan was a file the timeline could not show.
"""

from __future__ import annotations

import json
import tomllib
from typing import TYPE_CHECKING

import pytest

from harness_core.access.types import AccessRouteRef, ModelApi
from harness_core.engines.events import PlanItem, PlanUpdated
from harness_core.engines.types import (
    EngineMode,
    EngineProfile,
    EngineVersion,
    GatewayEndpoints,
)
from harness_core.testing.engines.script import (
    _todo_args,  # pyright: ignore[reportPrivateUsage]
)
from harness_engines.claude_code import launch as claude_launch
from harness_engines.claude_code.mapper import TODO_TOOL, ClaudeEventMapper
from harness_engines.codex import launch as codex_launch
from harness_engines.codex.mapper import CodexEventMapper
from harness_engines.opencode import launch as opencode_launch
from harness_engines.opencode.mapper import OpencodeEventMapper

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.engines.events import EngineEvent

MODES = (EngineMode.BUILD, EngineMode.PLAN, EngineMode.REVIEW)
PLAN = (
    PlanItem("Read the failing test", "completed"),
    PlanItem("Fix the parser", "in_progress"),
    PlanItem("Run the tests", "pending"),
)
SCRIPTED = json.dumps([{"title": item.title, "status": item.status} for item in PLAN])


def _endpoints(api: ModelApi) -> GatewayEndpoints:
    return GatewayEndpoints(
        model_base_url="http://model-gateway:8097/v1",
        model_api=api,
        tools_mcp_url="http://tool-gateway:8097/mcp",
        token_placeholder="pxh-model-placeholder-0123",
        tools_token_placeholder="pxh-tools-placeholder-4567",
    )


def _profile(kind: str, version: str, mode: EngineMode) -> EngineProfile:
    return EngineProfile(
        engine=EngineVersion(kind, version, "sha256:" + "d" * 64), mode=mode, access=AccessRouteRef("route-1", "d1")
    )


@pytest.mark.parametrize("mode", MODES)
def test_fixp2_c3_codex_offers_update_plan_in_every_mode(mode: EngineMode) -> None:
    launch = codex_launch.render_launch(
        _profile("codex", "0.160.0", mode), _endpoints(ModelApi.RESPONSES), "/skills"
    )
    config = tomllib.loads(launch.files[codex_launch.CONFIG_PATH].decode())
    assert config["tools"][codex_launch.PLAN_TOOL] == {"enabled": True}
    assert codex_launch.PLAN_TOOL == "update_plan"


@pytest.mark.parametrize("mode", MODES)
def test_fixp2_c3_claude_code_and_opencode_offer_their_task_list_in_every_mode(mode: EngineMode) -> None:
    claude = claude_launch.render_launch(
        _profile("claude_code", "2.1.294", mode), _endpoints(ModelApi.ANTHROPIC_MESSAGES), "/skills"
    )
    tools = claude.argv[claude.argv.index("--tools") + 1].split(",")
    assert TODO_TOOL in tools
    opencode = opencode_launch.render_config(
        _profile("opencode", "1.18.34", mode), _endpoints(ModelApi.CHAT_COMPLETIONS), "/skills"
    )
    permission = opencode["permission"]
    assert isinstance(permission, dict) and permission[opencode_launch.PLAN_TOOL] == "allow"


def _codex(arguments: dict[str, object]) -> list[EngineEvent]:
    mapper = CodexEventMapper(thread_id="th_1", turn_id="trn_1")
    mapper.begin("cx_1")
    # Codex reports an `update_plan` call as this notification, the call's own arguments as its plan.
    return mapper.feed(
        {"method": "turn/plan/updated", "params": {"threadId": "th_1", "turnId": "cx_1", **arguments}}
    )


def _claude(arguments: dict[str, object]) -> list[EngineEvent]:
    mapper = ClaudeEventMapper(turn_id="trn_1")
    use = {"type": "tool_use", "id": "tu_1", "name": TODO_TOOL, "input": arguments}
    return mapper.feed({"type": "assistant", "message": {"id": "m1", "content": [use]}})


def _opencode(arguments: dict[str, object]) -> list[EngineEvent]:
    mapper = OpencodeEventMapper(session_id="ses_1", turn_id="trn_1")
    _ = mapper.feed({"type": "session.status", "properties": {"sessionID": "ses_1", "status": {"type": "busy"}}})
    part = {
        "type": "tool",
        "id": "p1",
        "messageID": "msg_1",
        "callID": "call_1",
        "tool": opencode_launch.PLAN_TOOL,
        "state": {"status": "running", "input": arguments},
    }
    return mapper.feed({"type": "message.part.updated", "properties": {"sessionID": "ses_1", "part": part}})


@pytest.mark.parametrize(
    ("tool", "feed"),
    [("update_plan", _codex), (TODO_TOOL, _claude), ("todowrite", _opencode)],
    ids=["codex", "claude_code", "opencode"],
)
def test_fixp2_c3_each_engines_task_list_call_is_the_same_plan_event(
    tool: str, feed: Callable[[dict[str, object]], list[EngineEvent]]
) -> None:
    """The arguments each engine's model sends its plan tool (the scripted brain's shape for that tool) become the
    same ``PlanUpdated``, which the engine kernel turns into the run's ``task.*`` plan."""
    arguments = _todo_args(tool, SCRIPTED)
    events = feed(dict(arguments))
    assert [e.items for e in events if isinstance(e, PlanUpdated)] == [PLAN]
