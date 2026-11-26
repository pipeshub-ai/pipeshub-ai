"""Kernel mechanics around the crash windows: fold safety, journal faults, ordering, slots, leases."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from harness_core.failpoints import Arm, Nth, Raise
from harness_core.kernel.core.messages import (
    AssistantMessage,
    TextPart,
    ThinkingPart,
    ToolMessage,
    UserMessage,
)
from harness_core.kernel.core.responses import ModelResponse, TokenUsage
from harness_core.kernel.core.scope import RunScope, StateSlot
from harness_core.kernel.core.turn_limit import FINAL_ANSWER_NOTE
from harness_core.kernel.durable import (
    classify_tool_effect,
    tool_id_for,
)
from harness_core.kernel.durable.adapters import TransportModelPort
from harness_core.kernel.durable.ports import ModelCall
from harness_core.kernel.durable.slots import (
    CUT_SHORT_SLOT,
    FINAL_ANSWER_SLOT,
    restore_persisted_slots,
    scope_turn_hook,
)
from harness_core.kernel.fold import FOLD_VERSION, fold_run
from harness_core.models.base import Model
from harness_core.testing import armed
from harness_core.tools.base import Tag

from .support import RUN, SESSION, Crash, World, assert_paired, reply, scripted

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic import JsonValue

    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.core.responses import StructuredResponse
    from harness_core.kernel.core.streaming import StreamEvent
    from harness_core.kernel.core.tool_schema import ToolSchema
    from harness_core.kernel.fold import RunState


def _world() -> World:
    world = World()
    world.admit()
    return world


@pytest.mark.journal_history_unchecked("injects an event type from a newer release")
async def test_k13_unknown_critical_event_releases_the_claim() -> None:
    world = _world()
    _ = world.journal.inject(
        "ses1",
        {"runId": RUN, "type": "tool.teleported", "critical": True, "writer": "worker"},
    )
    _, port = scripted(reply("never"))
    released = await world.run(world.kernel(port))
    assert released.status == "released" and "tool.teleported" in (
        released.reason or ""
    )
    assert world.journal.status(RUN) == "queued"
    assert port.calls == []


async def test_k13_unknown_non_critical_event_is_ignored() -> None:
    world = _world()
    _ = world.journal.inject(
        "ses1", {"runId": RUN, "type": "x.acme.ping.sent", "critical": False}
    )
    _, port = scripted(reply("fine"))
    assert (await world.run(world.kernel(port))).status == "completed"


async def test_spec_from_a_newer_fold_version_is_released() -> None:
    world = World()
    from harness_core.testing.run_spec import minimal_run_spec

    world.journal.admit(
        RUN,
        org_id="org1",
        session_id="ses1",
        spec=minimal_run_spec(fold_version=FOLD_VERSION + 1),
        input_text="x",
    )
    _, port = scripted()
    assert (await world.run(world.kernel(port))).reason == "spec_fold_version"


async def test_stale_read_is_retried_and_a_lagging_journal_releases_the_claim() -> None:
    world = _world()
    world.journal.stale_reads = 1
    _, port = scripted(reply("ok"))
    assert (await world.run(world.kernel(port))).status == "completed"

    world.admit("run2")
    world.journal.stale_reads = 10
    _, port2 = scripted(reply("never"))
    released = await world.run(world.kernel(port2), "run2")
    assert released.reason == "journal_behind_claim"


async def test_transient_commit_failures_retry_the_same_command() -> None:
    world = _world()
    world.journal.unavailable_calls = 2
    _, port = scripted(reply("", ("create_issue", {"title": "t"})), reply("ok"))
    assert (
        await world.run(world.kernel(port, journal_retry_s=0))
    ).status == "completed"
    assert len(world.events("model.response")) == 2
    assert len(world.saas.effects) == 1


async def test_results_render_in_tool_call_order_not_journal_order() -> None:
    world = _world()
    _, port = scripted(
        reply("", ("create_issue", {"title": "a"}), ("lookup", {"q": "b"})),
        reply("done"),
    )
    assert (await world.run(world.kernel(port))).status == "completed"
    journal_order = [e.data["invocationId"] for e in world.events("tool.outcome")]
    assert journal_order == ["t1.1", "t1.0"], "reads commit before the serialized write"
    rendered = [
        m.tool_call_id for m in port.calls[-1].messages if isinstance(m, ToolMessage)
    ]
    assert rendered == ["call_0_0", "call_0_1"]


async def test_commit_rules_per_command() -> None:
    world = _world()
    _, port = scripted(
        reply("", ("post_message", {"text": "x"}), ("lookup", {"q": "y"})), reply("ok")
    )
    assert (await world.run(world.kernel(port))).status == "completed"
    commits = [types for _, types in world.journal.commits(RUN)]
    assert commits == [
        ["message.completed", "turn.started", "model.request", "model.response"],
        ["tool.intent", "tool.outcome"],  # the read: intent rides with its outcome
        ["tool.intent"],  # the write: its own fenced commit before the effect
        ["tool.outcome"],
        ["turn.completed", "turn.started", "model.request", "model.response"],
        ["message.completed", "turn.completed", "run.completed"],
    ]
    assert all(cid.startswith(f"{RUN}.a1:") for cid, _ in world.journal.commits(RUN))


CRASH = Raise(Crash)


async def test_max_turns_without_an_answer_completes_cut_short_with_what_it_gathered() -> None:
    """BUDGETEND: a run past its turns with no text to answer with still completes, flagged as cut short, on an
    answer written without a model that names what it gathered, never a bare failure."""
    world = World()
    world.admit(max_turns=1)
    model, port = scripted(reply("", ("lookup", {"q": "a"})), reply(""), reply("never"))
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    assert model.remaining == 1, "one answer-only turn past the budget, never a second"
    assert world.events("run.completed")[0].data == {"outcome": "answered", "truncatedBy": "turns"}
    final = [e for e in world.events("message.completed") if e.data.get("isFinal")]
    answer = (await world.blob(cast("dict[str, Any]", final[-1].data["textRef"]))).decode()
    assert "step limit" in answer and "- Lookup" in answer and '"continue"' in answer


async def test_j15_past_the_budget_after_a_tool_turn_one_answer_only_turn_answers() -> None:
    """Legacy ``_finish_after_max_turns`` (main #3808): the model has not seen the last turn's results, so it
    is asked once more, told to answer; that request's text is the answer."""
    world = World()
    world.admit(max_turns=1)
    model, port = scripted(reply("", ("lookup", {"q": "a"})), reply("The answer from what I found."))
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    assert model.remaining == 0
    last = port.calls[-1].messages[-1]
    # On the newest result's footer, as legacy's ``add_loop_note`` sends it (main #4106), never journaled.
    assert isinstance(last, ToolMessage) and last.step_footer.endswith(FINAL_ANSWER_NOTE)
    assert not [e for e in world.events("message.completed") if e.data.get("injected")]
    assert world.journal.status(RUN) == "completed"
    saved = [e.data["slots"] for e in world.events("loop.state_saved")]
    assert {FINAL_ANSWER_SLOT: 2, CUT_SHORT_SLOT: "turns"} in saved
    assert world.events("run.completed")[0].data == {"outcome": "answered", "truncatedBy": "turns"}


async def test_j15_the_answer_only_turn_never_runs_a_tool_that_does_not_end_the_run() -> None:
    world = World()
    world.admit(max_turns=1)
    long_text = "The ticket is resolved and Priya owns it, as of this morning."
    _, port = scripted(reply(long_text, ("lookup", {"q": "a"})), reply("", ("create_issue", {"title": "t"})))
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    assert len(world.events("tool.intent")) == 1, "only the first turn's call ran"
    assert world.saas.effects == []


async def test_j15_a_crash_after_the_answer_only_response_never_takes_a_second() -> None:
    world = World()
    world.admit(max_turns=1)
    model, port = scripted(reply("", ("lookup", {"q": "a"})), reply("The answer from what I found."), reply("x"))
    with armed({"worker.model.after_response_commit": Arm(CRASH, Nth(2))}):
        await world.crash(world.kernel(port))
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    assert model.remaining == 1
    assert len([e for e in world.events("loop.state_saved") if FINAL_ANSWER_SLOT in e.data["slots"]]) == 1


async def test_j15_a_crash_before_the_answer_only_response_takes_it_again() -> None:
    world = World()
    world.admit(max_turns=1)
    model, port = scripted(reply("", ("lookup", {"q": "a"})), reply("first"), reply("The answer, asked again."))
    with armed({"worker.model.before_response_commit": Arm(CRASH, Nth(2))}):
        await world.crash(world.kernel(port))
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    assert model.remaining == 0
    assert port.calls[-1].messages[-1].step_footer.endswith(FINAL_ANSWER_NOTE)


async def test_tl1_past_the_budget_the_last_substantive_text_is_the_answer() -> None:
    """Legacy ``_finish_after_max_turns``: when the answer-only turn answers nothing, 40+ characters of text on
    the last turn are returned as a degraded answer instead of failing the run."""
    world = World()
    world.admit(max_turns=2)
    long_text = "The ticket is resolved and Priya owns it, as of this morning."
    model, port = scripted(
        reply("Looking it up.", ("lookup", {"q": "a"})),
        reply(long_text, ("lookup", {"q": "b"})),
        reply(""),
        reply("never"),
    )
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    assert model.remaining == 1
    final = [
        e for e in world.journal.events(SESSION)
        if e.type == "message.completed" and e.data["isFinal"]
    ]
    assert len(final) == 1
    assert (await world.content.get(_ref(final[0].data["textRef"]))).decode() == long_text
    assert world.journal.status(RUN) == "completed"


async def test_tl1_a_short_last_text_is_no_answer_past_the_budget() -> None:
    world = World()
    world.admit(max_turns=2)
    _, port = scripted(
        reply("A long enough preamble that is not the last text at all.", ("lookup", {"q": "a"})),
        reply("Checking.", ("lookup", {"q": "b"})),
        reply(""),
        reply("never"),
    )
    done = await world.run(world.kernel(port))
    # Legacy reads the LAST assistant text with any content, not the longest: too short to be the answer, so the
    # run answers with what it gathered (BUDGETEND), never with the earlier preamble.
    assert done.status == "completed"
    final = [e for e in world.events("message.completed") if e.data.get("isFinal")]
    answer = (await world.blob(cast("dict[str, Any]", final[-1].data["textRef"]))).decode()
    assert answer.startswith("This reply stopped early") and "preamble" not in answer


async def test_turn_hook_journals_phase_slots_and_a_scope_restores_them() -> None:
    phase: StateSlot[str] = StateSlot(
        "test.durable.phase", default_factory=lambda: "plan", persist=True
    )
    world = _world()
    scope = RunScope(
        identity=cast("Any", None),
        spec=cast("Any", None),
        runtime=cast("Any", None),
        goal=cast("Any", None),
    )
    scope.set(phase, "execute")
    _, port = scripted(reply("", ("lookup", {"q": "a"})), reply("done"))
    assert (
        await world.run(world.kernel(port, turn_hook=scope_turn_hook(scope)))
    ).status == "completed"
    saved = world.events("loop.state_saved")
    assert len(saved) == 1, "unchanged slots are not journaled again"
    state = fold_run(world.journal.events("ses1"), run_id=RUN)
    fresh = RunScope(
        identity=cast("Any", None),
        spec=cast("Any", None),
        runtime=cast("Any", None),
        goal=cast("Any", None),
    )
    restore_persisted_slots(fresh, state)
    assert fresh.get(phase) == "execute"


async def test_hook_updates_are_visible_to_the_model_call() -> None:
    world = _world()

    def hook(state: RunState) -> dict[str, JsonValue]:
        return {"phase": f"turn{state.turn + 1}"}

    _, port = scripted(reply("", ("lookup", {"q": "a"})), reply("done"))
    assert (await world.run(world.kernel(port, turn_hook=hook))).status == "completed"
    assert [c.slots["phase"] for c in port.calls] == ["turn1", "turn2"]


# ---- effects metadata ----


@pytest.mark.parametrize(
    ("tags", "effect", "idempotent"),
    [
        ([("category", "write")], "write", False),
        ([("type", "write")], "write", False),
        ([("type", "create"), ("category", "customer_support")], "write", False),
        ([("category", "execute")], "execute", False),
        ([("category", "read")], "read", True),
        ([("type", "search")], "read", True),
        ([("category", "storage")], "write", False),
        ([], "write", False),
        ([("effect", "workspace")], "workspace", True),
        ([("type", "create"), ("idempotent", "true")], "write", True),
    ],
)
def test_effect_classification_fails_closed(
    tags: list[tuple[str, str]], effect: str, idempotent: bool
) -> None:
    result = classify_tool_effect("tool", [Tag(k, v) for k, v in tags])
    assert (result.effect, result.idempotent) == (effect, idempotent)


def test_tool_ids_match_the_contract_pattern() -> None:
    import re

    pattern = re.compile(
        r"^[a-z0-9][a-z0-9_-]{0,63}@[A-Za-z0-9_-]{1,128}/[A-Za-z0-9_.-]{1,128}$"
    )
    for name, source in [
        ("jira.create", "Atlassian"),
        ("x y", "_weird"),
        ("a" * 300, ""),
    ]:
        assert pattern.match(tool_id_for(name, source=source)), (name, source)
    tagged = classify_tool_effect(
        "search", [Tag("provider", "atlassian"), Tag("type", "search")]
    )
    assert tagged.tool_id == "atlassian@default/search"


# ---- transport adapter ----


class _FakeModel(Model):
    @property
    def model_name(self) -> str:
        return "fake-1"

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        *,
        tool_choice: object = None,
    ) -> ModelResponse:
        message = AssistantMessage(
            content=[ThinkingPart(thinking="sig"), TextPart(text="hi")]
        )
        return ModelResponse(
            message=message,
            usage=TokenUsage(input_tokens=3, output_tokens=1),
            model="fake-1",
        )

    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict[str, object],
        system: str | None = None,
        model: str | None = None,
    ) -> StructuredResponse:
        raise NotImplementedError

    def stream(self, *args: object, **kwargs: object) -> AsyncIterator[StreamEvent]:
        raise NotImplementedError


async def test_transport_port_journals_the_full_message_as_raw_state() -> None:
    port = TransportModelPort(
        _FakeModel(), provider="anthropic", transport="anthropic-native"
    )
    deltas: list[str] = []

    async def on_delta(kind: str, text: str) -> None:
        deltas.append(text)

    sampled = await port.sample(ModelCall([UserMessage(content="q")]), on_delta)
    assert sampled.raw is not None and deltas == ["hi"]
    restored = port.restore(
        sampled.raw, provider="anthropic", transport="anthropic-native"
    )
    assert restored is not None and isinstance(restored.content[0], ThinkingPart)
    assert port.restore(sampled.raw, provider="openai", transport="openai-chat") is None


async def test_end_to_end_transcript_is_provider_valid() -> None:
    world = _world()
    _, port = scripted(
        reply("", ("lookup", {"q": "a"}), ("create_issue", {"t": 1})),
        reply("", ("lookup", {"q": "b"})),
        reply("done"),
    )
    done = await world.run(world.kernel(port))
    assert done.status == "completed"
    assert_paired(list(done.transcript))


def _ref(raw: object) -> Any:  # noqa: ANN401
    from harness_core.contracts.generated.common import ContentRef

    return ContentRef.model_validate(raw)


def _harness_lines(caplog: pytest.LogCaptureFixture, event: str) -> list[Any]:
    return [r for r in caplog.records if getattr(r, "harness_event", None) == event]


async def test_pa19_a_fold_read_outage_is_retried_and_the_run_completes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Node restarting while the worker folds the journal at claim: the read
    is retried while the lease holds, like a commit; the run never fails."""
    caplog.set_level("INFO")
    world = _world()
    world.journal.unavailable_reads = 3
    _, port = scripted(reply("", ("lookup", {"q": "a"})), reply("done"))
    done = await world.run(world.kernel(port, journal_retry_s=0))
    assert done.status == "completed"
    assert world.events("run.failed") == []
    retries = _harness_lines(caplog, "run.fold_retry")
    assert [r.harness["retries"] for r in retries] == [1, 2, 3]
    assert all(r.harness["runId"] == RUN and r.harness["attemptId"] for r in retries)


async def test_pa19_a_read_outage_mid_run_is_retried_too() -> None:
    world = _world()

    def outage(w: World) -> None:
        w.journal.unavailable_reads = 2

    world.on_read = outage
    _, port = scripted(reply("", ("lookup", {"q": "a"})), reply("done"))
    done = await world.run(world.kernel(port, journal_retry_s=0))
    assert done.status == "completed"
    assert world.events("run.failed") == []
    assert len(world.events("model.response")) == 2


async def test_pa19_an_outage_that_outlasts_the_lease_releases_the_claim(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The lease runs out while the journal cannot be read: the worker hands
    the run back (never fails it) and a later claim runs it."""
    caplog.set_level("INFO")
    world = _world()
    world.journal.unavailable_reads = 1_000

    async def wait(seconds: float) -> None:
        _ = world.clock.advance(seconds)

    _, port = scripted(reply("done"))
    released = await world.run(world.kernel(port, journal_retry_s=5.0, sleep=wait))
    assert (released.status, released.reason) == ("released", "journal_unavailable")
    assert world.events("run.failed") == []
    [line] = _harness_lines(caplog, "run.release")
    assert line.harness["reason"] == "journal_unavailable" and line.harness["runId"] == RUN
    assert _harness_lines(caplog, "run.fold_retry")

    world.journal.unavailable_reads = 0
    done = await world.run(world.kernel(port, journal_retry_s=0))
    assert done.status == "completed"
    assert world.events("run.failed") == []
    assert len(world.events("run.claimed")) == 2
