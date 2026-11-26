"""SLASH2 C2 ``/compact [focus]`` on the durable loop: the run that takes the request compacts before its first
request (the ladder's ``forced_compaction`` with only the last response kept), this run's input follows the
summary word for word (earlier messages are summarised, as the ladder does), one ``x.pipeshub.context.compacted``
records before/after estimates and the focus, a resumed attempt never compacts twice, and the pin the run was
admitted with is the one it runs on. A user who types ``/compact`` without the built-in sends text."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.harness.contracts.generated.pipeshub.events.x_pipeshub_context_compacted import (
    PipeshubContextCompactedEvent,
)
from harness_core.context import TruncationCompaction
from harness_core.context.user_compact import CONTEXT_COMPACTED_EVENT, USER_COMPACT_SLOT
from harness_core.contracts import validate_contract
from harness_core.failpoints import Raise
from harness_core.journal.types import CONTEXT_COMPACTED, CompactAsk
from harness_core.kernel.core.messages import UserMessage
from harness_core.kernel.durable import ToolExecution
from harness_core.testing import armed
from harness_core.testing.run_spec import minimal_run_spec
from tests.unit.harness_core.context.kernel_support import answered, compiler, looked_up
from tests.unit.harness_core.durable.support import (
    ORG,
    RUN,
    SESSION,
    Crash,
    World,
    scripted,
)

if TYPE_CHECKING:
    from harness_core.context.ports import CompactionRequest, CompactionSummary
    from harness_core.kernel.durable.ports import ModelCall, SampledTurn
    from harness_core.testing.durable import ScriptedModelPort

NEXT = "run2"
WRITERS = {CONTEXT_COMPACTED_EVENT: frozenset({"worker"})}


@dataclass
class Recorder:
    """The model port as the provider sees it: every request the run sends."""

    port: ScriptedModelPort
    sent: list[ModelCall] = field(default_factory=list["ModelCall"])

    async def sample(self, call: ModelCall, on_delta: Any) -> SampledTurn:  # noqa: ANN401
        self.sent.append(call)
        return await self.port.sample(call, on_delta)

    def restore(self, raw: bytes, *, provider: str, transport: str) -> Any:  # noqa: ANN401
        return self.port.restore(raw, provider=provider, transport=transport)


@dataclass
class Summaries:
    """The truncation strategy, keeping what it was asked."""

    asked: list[CompactionRequest] = field(default_factory=list["CompactionRequest"])

    async def compact(self, request: CompactionRequest) -> CompactionSummary:
        self.asked.append(request)
        return await TruncationCompaction().compact(request)


def _world(**kwargs: Any) -> World:  # noqa: ANN401
    world = World(extension_writers=WRITERS, **kwargs)

    async def lookup(args: Any, ctx: Any) -> ToolExecution:  # noqa: ANN401
        return ToolExecution(status="ok", content={"text": f"{args['q']}:" + "x" * 3_000})

    world.tools.add("lookup", lookup, tags=[("category", "read")])
    return world


async def _first_run(world: World, *replies: Any) -> None:  # noqa: ANN401
    world.admit(text="what is the travel cap", max_turns=12)
    _, port = scripted(*replies)
    done = await world.run(world.kernel(port, context=compiler()))
    assert done.status == "completed"


def _admit_next(world: World, text: str, compact: CompactAsk | None) -> None:
    world.journal.admit(
        NEXT, org_id=ORG, session_id=SESSION, spec=minimal_run_spec(max_turns=8), input_text=text, compact=compact
    )


async def _next_run(world: World, summaries: Summaries | None = None, **kernel: Any) -> Recorder:  # noqa: ANN401
    _, port = scripted(answered(text="second answer"))
    recorder = Recorder(port)
    done = await world.run(
        world.kernel(recorder, context=compiler(strategy=summaries or Summaries()), **kernel),  # type: ignore[arg-type]
        NEXT,
    )
    assert done.status == "completed"
    return recorder


def _user_texts(call: ModelCall) -> list[str]:
    return [str(m.content) for m in call.messages if isinstance(m, UserMessage)]


async def test_compact_summarises_before_the_first_request_and_keeps_the_persons_messages() -> None:
    world = _world()
    await _first_run(world, looked_up(300, "q0"), looked_up(300, "q1"), answered(text="first answer"))
    _admit_next(world, "and the per diem?", CompactAsk("keep the cap numbers"))
    summaries = Summaries()
    recorder = await _next_run(world, summaries)

    (compaction,) = world.events("context.compacted", run_id=NEXT)
    (recorded,) = world.events(CONTEXT_COMPACTED_EVENT, run_id=NEXT)
    validate_contract(PipeshubContextCompactedEvent, recorded.envelope())
    data = recorded.data
    assert (data["trigger"], data["executor"], data["outcome"], data["userMessagesKept"]) == (
        "user", "loop", "compacted", False,
    )
    assert data["focus"] == "keep the cap numbers"
    assert isinstance(data["beforeTokens"], int) and isinstance(data["afterTokens"], int)
    assert data["afterTokens"] < data["beforeTokens"]
    assert [r.focus for r in summaries.asked] == ["keep the cap numbers"]
    types = world.types(NEXT)
    assert types.index("context.compacted") < types.index(CONTEXT_COMPACTED_EVENT) < types.index("model.response")
    assert compaction.data["range"]["fromSeq"] <= compaction.data["range"]["toSeq"]

    first = _user_texts(recorder.sent[0])
    (summary,) = [text for text in first if "a summary of the earlier conversation" in text]
    assert "what is the travel cap" in summary
    assert "what is the travel cap" not in first, "an earlier run's message is summarised, as the ladder does"
    assert "and the per diem?" in first, "this run's own input follows the summary word for word"
    assert not any("x" * 3_000 in text for text in first), "the long results were summarised away"


async def test_the_pin_is_the_one_the_run_was_admitted_with() -> None:
    world = _world()
    await _first_run(world, looked_up(300, "q0"), answered(text="first answer"))
    _admit_next(world, "next", CompactAsk())
    await _next_run(world)
    pins = world.events("run.spec_pinned", run_id=NEXT)
    assert len(pins) == 1
    assert pins[0].data["spec"]["model"] == world.events("run.spec_pinned", run_id=RUN)[0].data["spec"]["model"]
    assert all(e.type != "run.spec_pinned" for e in world.events(run_id=NEXT)[1:] if e.seq > pins[0].seq)


async def test_a_resumed_attempt_never_compacts_twice() -> None:
    world = _world()
    await _first_run(world, looked_up(300, "q0"), looked_up(300, "q1"), answered(text="first answer"))
    _admit_next(world, "and the per diem?", CompactAsk())
    _, port = scripted(answered(text="second answer"))
    with armed({"worker.model.before_response_commit": Raise(Crash)}):
        await world.crash(world.kernel(port, context=compiler(strategy=Summaries())), NEXT)
    assert len(world.events(CONTEXT_COMPACTED_EVENT, run_id=NEXT)) == 1
    summaries = Summaries()
    await _next_run(world, summaries)
    assert summaries.asked == [], "the journaled slot says the request was honoured"
    assert len(world.events("context.compacted", run_id=NEXT)) == 1
    assert len(world.events(CONTEXT_COMPACTED_EVENT, run_id=NEXT)) == 1
    saved = [e.data["slots"] for e in world.events("loop.state_saved", run_id=NEXT) if USER_COMPACT_SLOT in e.data["slots"]]
    assert saved == [{USER_COMPACT_SLOT: "compacted"}]


async def test_nothing_older_than_the_last_reply_is_nothing_to_compact() -> None:
    world = _world()
    await _first_run(world, answered(text="first answer"))
    _admit_next(world, "next", CompactAsk())
    summaries = Summaries()
    await _next_run(world, summaries)
    assert summaries.asked == []
    assert world.events("context.compacted", run_id=NEXT) == []
    (recorded,) = world.events(CONTEXT_COMPACTED_EVENT, run_id=NEXT)
    assert recorded.data["outcome"] == "nothing_to_compact"
    assert recorded.data["beforeTokens"] == recorded.data["afterTokens"]
    assert "focus" not in recorded.data


async def test_an_older_journal_gets_the_compaction_without_the_new_event() -> None:
    world = _world(journal_accepts=frozenset())
    await _first_run(world, looked_up(300, "q0"), looked_up(300, "q1"), answered(text="first answer"))
    _admit_next(world, "next", CompactAsk())
    await _next_run(world)
    assert len(world.events("context.compacted", run_id=NEXT)) == 1
    assert world.events(CONTEXT_COMPACTED_EVENT, run_id=NEXT) == []
    assert CONTEXT_COMPACTED == "context_compacted"


async def test_a_typed_slash_compact_is_text_and_compacts_nothing() -> None:
    world = _world()
    await _first_run(world, looked_up(300, "q0"), looked_up(300, "q1"), answered(text="first answer"))
    _admit_next(world, "/compact keep the cap numbers", None)
    summaries = Summaries()
    recorder = await _next_run(world, summaries)
    assert summaries.asked == []
    assert world.events("context.compacted", run_id=NEXT) == []
    assert world.events(CONTEXT_COMPACTED_EVENT, run_id=NEXT) == []
    assert "/compact keep the cap numbers" in _user_texts(recorder.sent[0])


async def _shaped_first_run(world: World) -> None:
    world.admit(text="what is the travel cap", max_turns=12)
    _, port = scripted(looked_up(300, "q0"), looked_up(300, "q1"), answered(text="first answer"))
    assert (await world.run(world.kernel(port))).status == "completed"


async def test_without_the_compiler_the_compactor_summarises_and_every_later_request_holds_the_summary() -> None:
    """``HARNESS_CONTEXT_COMPILER`` off (the default): requests are shaped as before, the compactor summarises, and
    the shaped history applies the journaled compaction on this run and every later one."""
    world = _world()
    await _shaped_first_run(world)
    _admit_next(world, "and the per diem?", CompactAsk())
    _, port = scripted(answered(text="second answer"))
    recorder = Recorder(port)
    done = await world.run(world.kernel(recorder, compactor=compiler(strategy=Summaries())), NEXT)  # type: ignore[arg-type]
    assert done.status == "completed"
    (recorded,) = world.events(CONTEXT_COMPACTED_EVENT, run_id=NEXT)
    assert recorded.data["outcome"] == "compacted"
    first = _user_texts(recorder.sent[0])
    assert any("a summary of the earlier conversation" in text for text in first), first
    assert "and the per diem?" in first
    assert not any("x" * 3_000 in text for text in first)

    later_run = "run3"
    world.journal.admit(
        later_run, org_id=ORG, session_id=SESSION, spec=minimal_run_spec(max_turns=8), input_text="and hotels?"
    )
    _, port = scripted(answered(text="third answer"))
    later = Recorder(port)
    assert (await world.run(world.kernel(later), later_run)).status == "completed"  # type: ignore[arg-type]
    third = _user_texts(later.sent[0])
    assert any("a summary of the earlier conversation" in text for text in third), "the compaction holds afterwards"
    assert "what is the travel cap" not in third


async def test_with_nothing_to_summarise_with_the_request_is_recorded_unsupported() -> None:
    world = _world()
    await _shaped_first_run(world)
    _admit_next(world, "next", CompactAsk())
    _, port = scripted(answered(text="second answer"))
    assert (await world.run(world.kernel(port), NEXT)).status == "completed"
    (recorded,) = world.events(CONTEXT_COMPACTED_EVENT, run_id=NEXT)
    assert recorded.data["outcome"] == "unsupported"
    assert world.events("context.compacted", run_id=NEXT) == []
