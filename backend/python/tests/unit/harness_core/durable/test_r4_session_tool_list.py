"""R4 (recs audit, fixed in recs2): one tool list for the whole chat session, on the native durable loop.

A host that names a list key (``ModelCall.tool_list_key``) gets the session's list on every request: the first
request journals it (``session.toolLists``), and every later one sends it byte for byte, in the same run or a later
one, after a crash and resume, after a checkpoint. A tool found later is called through ``call_tool``, which the kernel
unwraps into the named tool's call (its own effect, approval and journal). A listed tool the run lost, or whose
arguments changed, fails closed with a plain message; a description-only change runs.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from harness_core.failpoints import Arm, Nth, Raise
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.kernel.durable import ToolExecution
from harness_core.kernel.fold import fold_run, fold_session, make_checkpoint
from harness_core.kernel.session_tools import SESSION_TOOLS_SLOT, tools_digest
from harness_core.testing import armed
from harness_core.testing.durable import FunctionToolPort
from harness_core.testing.run_spec import minimal_run_spec
from harness_core.tools.call_tool import (
    CALL_TOOL,
    CALL_TOOL_DESCRIPTION,
    CALL_TOOL_INPUT_SCHEMA,
)

from .support import ORG, RUN, SESSION, Crash, World, reply, scripted

if TYPE_CHECKING:
    from harness_core.kernel.durable.ports import ModelCall
    from harness_core.kernel.fold import RunState

NEXT = "run2"
_OBJECT = {"type": "object", "properties": {"q": {"type": "string"}}}


def _schema(name: str, description: str = "", schema: dict[str, Any] | None = None) -> ToolSchema:
    return ToolSchema(name=name, description=description or f"The {name} tool.", input_schema=schema or _OBJECT)


@dataclass
class SchemaPort(FunctionToolPort):
    """The world's tools with the schemas a registry would answer."""

    shown: dict[str, ToolSchema] = field(default_factory=dict[str, ToolSchema])

    def schemas(self) -> list[ToolSchema] | None:
        return [self.shown.get(name) or _schema(name) for name in self.tools]


@dataclass
class GrowingShaper:
    """A host whose list grows the way lazy disclosure grew it: by what earlier calls found, and by what an admin
    granted later. ``key`` None: a host that does not fix its list."""

    world: World
    key: str | None = "react"
    extra: list[str] = field(default_factory=list[str])

    async def shape(self, call: ModelCall, turn: int, state: RunState) -> ModelCall:
        found = [f"found_{i}" for i in range(len(state.invocations))]
        tools = [_schema(CALL_TOOL, CALL_TOOL_DESCRIPTION, CALL_TOOL_INPUT_SCHEMA), _schema("lookup")]
        tools += [_schema(n) for n in sorted({*found, *self.extra})]
        return dataclasses.replace(call, tools=tools, tool_list_key=self.key)

    async def enter(self, turn: int, state: RunState) -> None:
        return None


def _world() -> World:
    world = World(tools=SchemaPort(shown={CALL_TOOL: _schema(CALL_TOOL, CALL_TOOL_DESCRIPTION, CALL_TOOL_INPUT_SCHEMA)}))
    world.tools.add(CALL_TOOL, _never, tags=[("category", "read")])
    world.admit(text="first request")
    return world


async def _never(args: Any, ctx: Any) -> ToolExecution:  # noqa: ANN401
    return ToolExecution(status="error", content="call_tool needs the name of a tool search_tools found")


def _admit_next(world: World) -> None:
    world.journal.admit(NEXT, org_id=ORG, session_id=SESSION, spec=minimal_run_spec(max_turns=8), input_text="next")


def _digests(calls: list[ModelCall]) -> set[str]:
    return {tools_digest(c.tools or []) for c in calls}


async def test_r4_every_request_of_the_session_sends_the_first_requests_list_byte_for_byte() -> None:
    world = _world()
    shaper = GrowingShaper(world)
    _, port = scripted(reply("", ("lookup", {"q": "a"})), reply("", ("lookup", {"q": "b"})), reply("done"))
    assert (await world.run(world.kernel(port, shaper=shaper))).status == "completed"
    first = port.calls[0].tools

    shaper.extra = ["granted_later"]
    _admit_next(world)
    _, later = scripted(reply("", ("lookup", {"q": "c"})), reply("done again"))
    assert (await world.run(world.kernel(later, shaper=shaper), NEXT)).status == "completed"

    assert len(port.calls) + len(later.calls) == 5
    assert _digests([*port.calls, *later.calls]) == {tools_digest(first or [])}
    assert [t.name for t in first or []] == [CALL_TOOL, "lookup"]
    pinned = [e for e in world.events("loop.state_saved") if SESSION_TOOLS_SLOT in e.data["slots"]]
    assert len(pinned) == 1, "journaled once, with the session's first request"
    session = fold_session(world.journal.events(SESSION), stream_id=SESSION)
    entry = session.tool_lists["react"]
    assert isinstance(entry, dict) and entry["digest"] == tools_digest(first or []) and entry["count"] == 2


async def test_r4_a_crash_and_a_resume_on_a_new_worker_send_the_same_list() -> None:
    world = _world()
    shaper = GrowingShaper(world)
    _, port = scripted(reply("", ("lookup", {"q": "a"})), reply("", ("lookup", {"q": "b"})), reply("done"))
    with armed({"worker.model.after_response_commit": Arm(Raise(Crash), Nth(2))}):
        await world.crash(world.kernel(port, shaper=shaper))
    assert (await world.run(world.kernel(port, shaper=GrowingShaper(world)))).status == "completed"
    assert len(_digests(port.calls)) == 1
    assert len(port.calls) == 3


async def test_r4_a_checkpoint_from_before_session_lists_is_refolded() -> None:
    from harness_core.kernel.fold import checkpoint_is_valid

    world = _world()
    _, port = scripted(reply("done"))
    assert (await world.run(world.kernel(port, shaper=GrowingShaper(world)))).status == "completed"
    events = world.journal.events(SESSION)
    checkpoint = make_checkpoint(fold_run(events, run_id=RUN), fold_session(events, stream_id=SESSION))
    session = checkpoint.state["session"]
    assert isinstance(session, dict)
    older = checkpoint.model_copy(
        update={"state": {**checkpoint.state, "session": {k: v for k, v in session.items() if k != "tool_lists"}}}
    )
    head = events[-1].seq
    assert checkpoint_is_valid(checkpoint, run_id=RUN, head_seq=head, ref="r", referenced_ref="r")
    assert not checkpoint_is_valid(older, run_id=RUN, head_seq=head, ref="r", referenced_ref="r")


async def test_r4_a_host_without_a_list_key_keeps_its_own_tools() -> None:
    world = _world()
    _, port = scripted(reply("", ("lookup", {"q": "a"})), reply("done"))
    assert (await world.run(world.kernel(port, shaper=GrowingShaper(world, key=None)))).status == "completed"
    assert len(_digests(port.calls)) == 2
    assert not [e for e in world.events("loop.state_saved") if SESSION_TOOLS_SLOT in e.data["slots"]]


async def test_r4_call_tool_runs_the_named_tool_as_its_own_call() -> None:
    """The write's own effect path (an intent before the SaaS call), not ``call_tool``'s read."""
    world = _world()
    _, port = scripted(
        reply("", (CALL_TOOL, {"name": "create_issue", "arguments": {"title": "t"}})),
        reply("done"),
    )
    assert (await world.run(world.kernel(port, shaper=GrowingShaper(world)))).status == "completed"
    assert [name for name, _ in world.tools.executed] == ["create_issue"]
    assert len(world.events("tool.intent")) == 1
    assert len(world.saas.effects_for("create_issue")) == 1
    assert "create_issue" not in {t.name for t in port.calls[-1].tools or []}, "never added to the list"


async def test_r4_a_malformed_call_tool_gets_its_own_refusal() -> None:
    world = _world()
    _, port = scripted(reply("", (CALL_TOOL, {"arguments": {"q": "a"}})), reply("done"))
    assert (await world.run(world.kernel(port, shaper=GrowingShaper(world)))).status == "completed"
    assert [name for name, _ in world.tools.executed] == [CALL_TOOL]


async def _second_run_calling_lookup(world: World) -> list[str]:
    _admit_next(world)
    _, port = scripted(reply("", ("lookup", {"q": "z"})), reply("done"))
    assert (await world.run(world.kernel(port, shaper=GrowingShaper(world)), NEXT)).status == "completed"
    outcomes = list(world.events("tool.outcome", run_id=NEXT))
    assert len(outcomes) == 1
    return [json.dumps(e.data) for e in outcomes]


async def _first_run(world: World) -> None:
    _, port = scripted(reply("done"))
    assert (await world.run(world.kernel(port, shaper=GrowingShaper(world)))).status == "completed"


async def _result_text(world: World) -> tuple[str, str]:
    (outcome,) = world.events("tool.outcome", run_id=NEXT)
    content = (await world.blob(outcome.data["resultRef"])).decode()  # type: ignore[arg-type]
    return str(outcome.data.get("status")), content


async def test_r4_a_listed_tool_the_server_removed_fails_closed_with_a_plain_message() -> None:
    world = _world()
    await _first_run(world)
    del world.tools.tools["lookup"]
    _ = await _second_run_calling_lookup(world)
    status, content = await _result_text(world)
    assert status == "error"
    assert "lookup is no longer available" in content and "new chat" in content


async def test_r4_a_listed_tool_whose_arguments_changed_fails_closed() -> None:
    world = _world()
    await _first_run(world)
    assert isinstance(world.tools, SchemaPort)
    world.tools.shown["lookup"] = _schema("lookup", schema={"type": "object", "properties": {"query": {"type": "string"}}})
    _ = await _second_run_calling_lookup(world)
    status, content = await _result_text(world)
    assert status == "error" and "lookup changed after this chat started" in content
    assert ("lookup", f"{NEXT}.t1.0") not in world.tools.executed


async def test_r4_a_description_only_change_still_runs() -> None:
    world = _world()
    await _first_run(world)
    assert isinstance(world.tools, SchemaPort)
    world.tools.shown["lookup"] = _schema("lookup", description="A new description, same arguments.")
    _ = await _second_run_calling_lookup(world)
    status, _ = await _result_text(world)
    assert status == "ok"
