"""ADR-SCH-03 D8 (HT1 fix): a schedule tool reached through ``call_tool`` on the durable loop.

The loop journals the model's call, the ``call_tool`` wrapper, so the approval is bound to the wrapper's digest while
the tool runs with the arguments inside it. The tool's context says how it was called (``called_as``) and the body
sent to Node carries it, so Node ties the wrapper's digest to the arguments it receives. Each case here runs the real
kernel (``RunDriver``: unwrap, hold, approval, the tool), and the body it sends is the fixture Node's own check
accepts in ``tests/modules/agent-harness/sch03-call-tool-approval.test.ts``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from app.harness.schedules import NodeSchedules, ScheduleAsks, schedule_tools
from app.schedules.arguments import TOOL_IDS
from harness_core.contracts import canonical_digest
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.types import ToolContext, ToolEffect, ToolExecution, allow_all
from harness_core.logs import HarnessLog
from harness_core.testing.interactions import ManualWakeup
from harness_core.tools.durable_port import _CALL  # pyright: ignore[reportPrivateUsage]
from tests.unit.harness_core.durable.support import RUN, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

NOW = datetime(2026, 11, 26, 23, 42, tzinfo=UTC)
ONCE: dict[str, Any] = {
    "name": "Joke in five minutes",
    "ask": "Tell me a short, clean joke.",
    "when": {"kind": "once", "at": "2026-11-26T15:47:00-08:00", "timeZone": "America/Los_Angeles"},
}
VIEW: dict[str, Any] = {
    "id": "sch_1", "name": "Joke in five minutes", "status": "active", "revision": 3,
    "trigger": ONCE["when"], "nextFireAt": "2026-11-26T23:47:00.000Z", "input": {"query": ONCE["ask"]},
}
FIXTURE = (
    Path(__file__).resolve().parents[4] / "nodejs/apps/tests/modules/agent-harness/fixtures/sch03-call-tool-approval.json"
)


class _Route:
    """Node's harness state API: what the tool posted, and a schedule to read."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def log(self, name: str) -> HarnessLog:
        return HarnessLog.named(name)

    async def get(self, suffix: str, *, params: Any = None, operation: str) -> httpx.Response:  # noqa: ANN401
        return httpx.Response(200, json=VIEW, request=httpx.Request("GET", "http://node/x"))

    async def post(self, suffix: str, *, body: dict[str, Any], operation: str) -> httpx.Response:
        self.posts.append((suffix, body))
        if suffix == "/schedules":
            answer: object = {"schedule": {**VIEW, "id": "sch-new"}, "created": True}
        else:
            answer = {"schedule": VIEW if suffix.endswith("/update") else None, "changed": True}
        return httpx.Response(200, json=answer, request=httpx.Request("POST", "http://node/x"))


# A delete is a write the risk class marks destructive, as the production tag derivation makes it.
_MARKS = {"schedule_create": None, "schedule_update": None, "schedule_delete": "destructive"}


def _world(route: _Route) -> World:
    world = World()
    tools = {t.name: t for t in schedule_tools(NodeSchedules(route), timezone="UTC")}  # type: ignore[arg-type]

    async def unreachable(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        raise AssertionError("call_tool is unwrapped before it runs")

    world.tools.add("call_tool", unreachable, tags=[("category", "read")])
    for name, mark in _MARKS.items():
        tool = tools[name]

        async def run(args: Mapping[str, JsonValue], ctx: ToolContext, tool: Any = tool) -> ToolExecution:  # noqa: ANN401
            token = _CALL.set(ctx)
            try:
                out = await tool.execute(**dict(args))
            finally:
                _CALL.reset(token)
            return ToolExecution(status="ok" if out.success else "error", content=out.data if out.success else out.error)

        effect = ToolEffect(
            TOOL_IDS[name], "write", False, SinkDescriptor(egress="self", args_leave_boundary=False), mark=mark,  # type: ignore[arg-type]
        )
        world.tools.add(name, run, effect=effect)
    return world


async def _approved_run(route: _Route, *calls: tuple[str, dict[str, Any]], tamper: dict[str, Any] | None = None) -> World:
    """One run: the model makes ``calls`` in one turn, the person approves each, the model answers."""
    world = _world(route)
    world.admit()
    _, port = scripted(reply("", *calls), reply("done"))
    wakeup = ManualWakeup(world.clock)
    asks = ScheduleAsks(allow_all, now=lambda: NOW, read=NodeSchedules(route).read)  # type: ignore[arg-type]

    def on_wait(waits: int) -> None:
        if waits == 1:
            for requested in world.events("interaction.requested"):
                world.journal.resolve_interaction(RUN, str(requested.data["interactionId"]), "approved")
            wakeup.wake(RUN)

    wakeup.on_wait = on_wait
    done = await world.run(
        world.kernel(port, approval=asks, interactions=wakeup, approval_hold_s=10.0, interaction_poll_s=2.0)
    )
    assert done.status == "completed"
    return world


def _wrapped(name: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    return "call_tool", {"name": name, "arguments": arguments}


async def test_sch03_a_create_through_call_tool_sends_the_wrapper_it_was_approved_as() -> None:
    route = _Route()
    world = await _approved_run(route, _wrapped("schedule_create", ONCE))
    [(suffix, body)] = route.posts
    [requested] = world.events("interaction.requested")
    [response] = world.events("model.response")[:1]
    [called] = response.data["toolCalls"]  # type: ignore[misc]
    wrapper = canonical_digest({"name": "schedule_create", "arguments": ONCE})
    # The HT1 failure: the journaled digest is the wrapper's, not that of the arguments the tool runs with.
    assert called["argsDigest"] == wrapper != canonical_digest(ONCE)  # type: ignore[index]
    assert suffix == "/schedules"
    assert body == {
        "interactionId": requested.data["interactionId"], "toolId": TOOL_IDS["schedule_create"],
        "callArgsDigest": wrapper, "arguments": ONCE, "calledAs": "schedule_create",
    }
    # What Node's check reads, pinned as the fixture its own test runs the real check on.
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["create"] == {"runId": RUN, "body": body, "bindingDigest": requested.data["bindingDigest"]}


async def test_sch03_a_direct_create_is_sent_without_a_wrapper() -> None:
    route = _Route()
    await _approved_run(route, ("schedule_create", ONCE))
    [(_suffix, body)] = route.posts
    assert "calledAs" not in body and body["callArgsDigest"] == canonical_digest(ONCE)


async def test_sch03_an_update_and_a_delete_through_call_tool_carry_the_wrapper() -> None:
    route = _Route()
    update = {"id": "sch_1", "revision": 3, "action": "pause"}
    delete = {"id": "sch_1", "revision": 3}
    world = await _approved_run(route, _wrapped("schedule_update", update), _wrapped("schedule_delete", delete))
    posted = dict(route.posts)
    assert posted["/schedules/sch_1/update"]["calledAs"] == "schedule_update"
    assert posted["/schedules/sch_1/update"]["callArgsDigest"] == canonical_digest({"name": "schedule_update", "arguments": update})
    assert posted["/schedules/sch_1/delete"]["calledAs"] == "schedule_delete"
    assert posted["/schedules/sch_1/delete"]["callArgsDigest"] == canonical_digest({"name": "schedule_delete", "arguments": delete})
    by_id = {str(e.data["interactionId"]): e.data["bindingDigest"] for e in world.events("interaction.requested")}
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    body = posted["/schedules/sch_1/update"]
    assert fixture["update"] == {"runId": RUN, "body": body, "bindingDigest": by_id[str(body["interactionId"])]}
