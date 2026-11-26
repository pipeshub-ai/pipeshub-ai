"""SPENDCONT: an engine run a limit cuts short saves its work, and "continue" picks up from it instead of starting over.

A Codex child gathering the PP project's security bugs made 131 Jira calls, its budget stopped it,
its wrap-up's 8.3 KB partial inventory was the answer and no file was saved; each "continue" started a child that
searched Jira again from scratch and stopped at the same limit. Here a scripted engine fetches three tickets a run
before its budget stops it, and the person says "continue" twice: no ticket is fetched twice, the requested file is
saved partial after each cut and grows, and the third run finishes it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness_core.effects.types import ToolExecution
from harness_core.engines.briefing import RESUME_FROM_HANDOFF
from harness_core.engines.durable.handoff import (
    FETCHED_DIR,
    HANDOFF_PATH,
    PARTIAL_ANSWER_PATH,
)
from harness_core.engines.events import (
    EngineError,
    EngineEventMeta,
    TextDelta,
    TurnCompleted,
)
from harness_core.engines.outcome.deliverable import resolve_deliverable
from harness_core.interactions.ports import RunBinding
from harness_core.testing.engines.durable import (
    SESSION,
    EngineWorld,
    ScriptedEngineAdapter,
)
from harness_core.testing.engines.script import (
    ChatMessage,
    Directive,
    ModelReply,
    _call_for,  # pyright: ignore[reportPrivateUsage]
    current_turn,
)
from harness_core.workspace.types import ObjectScope

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from pydantic import JsonValue

    from harness_core.effects.types import ToolContext
    from harness_core.engines.durable.ports import GatheredResult
    from harness_core.engines.events import EngineEvent
    from harness_core.engines.types import EngineTask, FollowUp
    from harness_core.journal.types import Claim
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.core.turn_limit import CutShort
    from harness_core.testing.sandbox.egress import EgressRequest

TICKETS = [f"PP-{n}" for n in range(1, 8)]
FILE = "security-bugs.md"
REQUEST = f"Gather every security bug in PP ({', '.join(TICKETS)}) and make a file {FILE} for Claude Code."
PER_RUN = 3
STOP = "STOPPED-FOR-BUDGET"
_KEY = re.compile(r'"key": "(PP-\d+)"')


def _finding(key: str) -> str:
    return f"{key}: SSRF finding in {key.lower()}"


async def _lookup(call: ToolCall, ctx: ToolContext) -> ToolExecution:
    del ctx
    return ToolExecution(status="ok", content=_finding(str(call.arguments["key"])))


@dataclass
class _Engine:
    """A diligent engine: it reads what its first prompt says is already done, fetches the tickets still missing,
    and writes the whole file once it has every ticket. Its budget allows ``PER_RUN`` fetches a run."""

    prompts: list[str] = field(default_factory=list[str])

    async def __call__(self, request: EgressRequest) -> JsonValue:
        body = request.body
        assert isinstance(body, dict)
        messages = [ChatMessage.from_json(m) for m in body.get("messages") or []]  # type: ignore[union-attr]
        tools = [str(t.get("name")) for t in body.get("tools") or [] if isinstance(t, dict)]  # type: ignore[union-attr]
        turn = current_turn(messages)
        prompt = next(m.content for m in turn if m.role == "user")
        if not self.prompts or self.prompts[-1] != prompt:
            self.prompts.append(prompt)
        done = set(_KEY.findall(prompt))
        fetched = [k for m in turn if m.role == "tool" for k in re.findall(r"(PP-\d+): SSRF", m.content)]
        wrote = any(c.name.lower() == "write" for m in turn for c in m.tool_calls)
        missing = [k for k in TICKETS if k not in done and k not in fetched]
        if wrote:
            return ModelReply(content=f"I finished {FILE} with all {len(TICKETS)} tickets.").to_json()
        if not missing:
            content = "# Security bugs\n" + "\n".join(f"- {_finding(k)}" for k in TICKETS)
            call = _call_for(Directive("write", f"out/{FILE} {content}"), tools, f"w{len(messages)}", messages)
            assert call is not None
            return ModelReply(tool_calls=(call,)).to_json()
        if len(fetched) >= PER_RUN:
            return ModelReply(content=STOP).to_json()
        call = _call_for(
            Directive("tool", f"lookup {json.dumps({'key': missing[0]})}"), tools, f"c{len(messages)}", messages
        )
        assert call is not None
        return ModelReply(tool_calls=(call,)).to_json()


@dataclass
class _WrapUp:
    """The run's wrap-up model: it keeps the earlier partial inventory and adds what this run fetched."""

    earlier: list[str | None] = field(default_factory=list["str | None"])
    requests: list[str] = field(default_factory=list[str])

    async def hold(self) -> bool:
        return True

    async def answer(
        self, request: str, gathered: Sequence[GatheredResult], reason: CutShort, *, earlier: str | None = None
    ) -> str | None:
        del reason
        self.earlier.append(earlier)
        self.requests.append(request)
        kept = [line for line in (earlier or "").splitlines() if line.startswith("- PP-")]
        found = kept + [f"- {g.text}" for g in gathered]
        missing = [k for k in TICKETS if not any(line.startswith(f"- {k}:") for line in found)]
        return "# Security bugs\n" + "\n".join(found) + "\n\n## Still to do\n" + ", ".join(missing)

    async def release(self) -> None:
        return None


class _BudgetStops(ScriptedEngineAdapter):
    """The engine's gateway refuses its next model call for budget when the scripted engine says ``STOP``."""

    async def open(self, sandbox, launch, restore, *, correlation=None):  # type: ignore[no-untyped-def] # noqa: ANN202
        return _Stopping(await super().open(sandbox, launch, restore, correlation=correlation))


class _Stopping:
    def __init__(self, inner: object) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        async for event in self._inner.run_turn(task):  # type: ignore[attr-defined]
            if isinstance(event, TextDelta) and STOP in event.text:
                continue
            if isinstance(event, TurnCompleted) and STOP in event.final_text:
                yield EngineError(EngineEventMeta(task.turn_id, event.meta.seq), "budget_insufficient", "refused")
                yield TurnCompleted(EngineEventMeta(task.turn_id, event.meta.seq + 1), "failed", final_text="")
                return
            yield event


def _world() -> tuple[EngineWorld, _Engine, _WrapUp]:
    engine, wrap_up = _Engine(), _WrapUp()

    def build(claim: Claim) -> _WrapUp:
        del claim
        return wrap_up

    world = EngineWorld(adapter=_BudgetStops(), model=engine, wrap_up=build, handlers={"lookup": _lookup})
    return world, engine, wrap_up


async def _answer(world: EngineWorld, run_id: str) -> str:
    return await world.plain_answer(run_id)


async def _file(world: EngineWorld, path: str) -> str | None:
    head = await world.store.head(ObjectScope("org-e1"), f"ws:{SESSION}")
    entry = head.files.get(path)
    if entry is None:
        return None
    return (await world.objects.get(ObjectScope("org-e1", f"ws:{SESSION}"), entry.content)).decode("utf-8")


def _completed(world: EngineWorld, run_id: str) -> list[JsonValue]:
    return [e.data for e in world.events(run_id) if e.type == "run.completed"]


async def test_continue_twice_resumes_the_cut_short_task_without_refetching() -> None:
    world, engine, wrap_up = _world()

    world.admit("run-1", REQUEST)
    assert (await world.attempt("run-1")).status == "completed"
    assert _completed(world, "run-1") == [{"outcome": "answered", "truncatedBy": "budget"}]
    answer = await _answer(world, "run-1")
    assert "- PP-3: SSRF" in answer and "This result is partial." in answer
    assert f"Saved so far: **{FILE}**, marked partial." in answer and '"continue"' in answer
    first = await _file(world, f"out/{FILE}")
    assert first is not None and first.startswith("> **Partial**") and "- PP-3:" in first and "- PP-4:" not in first
    handoff = json.loads(await _file(world, HANDOFF_PATH) or "{}")
    assert handoff["deliverable"] == f"out/{FILE}" and handoff["stoppedAt"] == "budget"
    assert [d["arguments"] for d in handoff["done"]] == [{"key": k} for k in TICKETS[:3]]
    assert all(str(d["result"]).startswith(f"{FETCHED_DIR}/") for d in handoff["done"])
    assert await _file(world, str(handoff["done"][0]["result"])) == _finding("PP-1")
    assert "- PP-3:" in (await _file(world, PARTIAL_ANSWER_PATH) or "")

    world.admit("run-2", "continue")
    assert (await world.attempt("run-2")).status == "completed"
    assert RESUME_FROM_HANDOFF in engine.prompts[-1] and '"key": "PP-3"' in engine.prompts[-1]
    # The wrap-up of the continuation extends the earlier result, for the task's own request.
    assert wrap_up.earlier[-1] is not None and "- PP-1:" in wrap_up.earlier[-1]
    assert REQUEST in wrap_up.requests[-1]
    second = await _file(world, f"out/{FILE}")
    assert second is not None and "- PP-1:" in second and "- PP-6:" in second and "- PP-7:" not in second
    handoff = json.loads(await _file(world, HANDOFF_PATH) or "{}")
    assert [d["arguments"]["key"] for d in handoff["done"]] == TICKETS[:6]
    assert handoff["runs"] == ["run-1", "run-2"] and REQUEST in handoff["request"]

    world.admit("run-3", "continue")
    assert (await world.attempt("run-3")).status == "completed"
    assert _completed(world, "run-3") == [{"outcome": "answered"}]
    final = await _file(world, f"out/{FILE}")
    assert final is not None and not final.startswith("> **Partial**")
    assert all(f"- {_finding(k)}" in final for k in TICKETS)

    # The finished task closes its handoff: a later task of the conversation is not told to continue it.
    closed = json.loads(await _file(world, HANDOFF_PATH) or "{}")
    assert closed["status"] == "completed" and closed["completedBy"] == "run-3"

    # No ticket was fetched twice across the three runs.
    fetched = {digest: n for (tool, digest), n in world.effects.items() if tool == "lookup"}
    assert len(fetched) == len(TICKETS) and set(fetched.values()) == {1}
    # One artifact, a version per run: partial, more partial, complete.
    published = [
        record
        for run in ("run-1", "run-2", "run-3")
        for record in (await world.publications.history(RunBinding("org-e1", SESSION, run))).published
    ]
    assert len({r.artifact_id for r in published}) == 1 and [r.version for r in published] == [1, 2, 3]

    world.admit("run-4", "say hello")
    assert (await world.attempt("run-4")).status == "completed"
    assert RESUME_FROM_HANDOFF not in engine.prompts[-1]


async def test_a_request_for_no_file_saves_the_handoff_but_writes_no_deliverable() -> None:
    world, _, _ = _world()
    world.admit("run-1", f"Which of {', '.join(TICKETS)} are security bugs?")
    assert (await world.attempt("run-1")).status == "completed"
    assert await _file(world, f"out/{FILE}") is None
    answer = await _answer(world, "run-1")
    assert "This result is partial." in answer and "Saved so far" not in answer
    handoff = json.loads(await _file(world, HANDOFF_PATH) or "{}")
    assert handoff["deliverable"] is None and len(handoff["done"]) == PER_RUN


def deliverable_name(request: str, *, fallback: str | None = None) -> str | None:
    """The file a cut-short run saves its partial as, from the deliverable the request resolves to."""
    return resolve_deliverable(request).partial_name(fallback)


def test_the_deliverable_is_named_as_requested() -> None:
    assert deliverable_name("make a file `PP-security-bugs-for-claude-code.md` for them") == (
        "PP-security-bugs-for-claude-code.md"
    )
    assert deliverable_name("save it as report.docx") == "report-partial.md"
    assert deliverable_name("gather the bugs and make a file for them", fallback="pp security") == "pp-security.md"
    assert deliverable_name("which tickets are security bugs?") is None
