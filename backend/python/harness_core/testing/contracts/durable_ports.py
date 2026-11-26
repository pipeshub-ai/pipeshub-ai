"""Contract suites for the durable kernel's model, tool and run-lifecycle ports (WRK-G4).

Case ids are ``DP<port>.<n>``, test names ``test_dp<port>_<n>_…``; the turn and delegation
ports (DP10, DP11, DP13..DP18) are in ``durable_turns``. Each contract states what
``harness_core.kernel.durable.driver`` relies on; subclass it once per adapter and provide
the fixture its docstring names.

* DP1 ``DurableModelPortContract`` (``model_ports``: ``ModelPortHarness``)
* DP2 ``DurableToolPortContract`` (``durable_tools``: a ``ToolCatalogHarness``)
* DP3 ``PreRunStepContract`` (``pre_run_step``)
* DP4 ``PostRunStepContract`` (``post_run_step``)
* DP5 ``PreRunGateContract`` (``gate_case``: ``GateCase``)
* DP6 ``CapabilityCheckContract`` (``capability_check``)
* DP7 ``RunPresenterContract`` (``presenters``: a factory of fresh presenters)
* DP8 ``RunRestoreContract`` (``restores``: ``RestoreHarness``)
* DP9 ``InputRendererContract`` (``renderers``: a factory of fresh renderers)
* DP12 ``ImportedHistoryContract`` (``imported_history``)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.contracts.canonical_json import canonical_json
from harness_core.journal.types import JournalEvent
from harness_core.kernel.core.messages import (
    AssistantMessage,
    TextPart,
    ToolCall,
    UserMessage,
)
from harness_core.kernel.durable.capability import WITHDRAWN
from harness_core.kernel.durable.ports import (
    ModelBackoff,
    ModelCall,
    ModelFailed,
    RunEnding,
    SteeringInput,
    TerminalTools,
)
from harness_core.testing.contracts.tool_catalog import (
    CTX,
    DELETE,
    GRANT,
    LOOKUP,
    TOOLS,
    call,
)
from harness_core.testing.model_script import Reply, ToolCallSpec

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.journal.types import NewEvent
    from harness_core.kernel.durable.ports import (
        CapabilityCheck,
        DeltaKind,
        DurableModelPort,
        DurableToolPort,
        ImportedHistory,
        InputRenderer,
        PostRunStep,
        PreRunGate,
        PreRunStep,
        RunEndingOutcome,
        RunPresenter,
        RunRestore,
    )
    from harness_core.testing.contracts.tool_catalog import OfferedTool

# What a key may be: it names a slot (``pre_run.<key>``) and appears in logs.
SLOT_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
OUTCOMES: tuple[RunEndingOutcome, ...] = ("answered", "needs_input", "stopped", "error")


def journaled(value: object) -> JsonValue:
    """``value`` as a later attempt reads it back from the journal."""
    return json.loads(canonical_json(value))


def ending(outcome: RunEndingOutcome) -> RunEnding:
    return RunEnding(
        outcome,
        texts=("Looking it up.", "The deploy finished at 10:42.") if outcome != "error" else (),
        error_type="model_error" if outcome == "error" else None,
        tool_calls=(ToolCall(id="c1", name="lookup", arguments={"q": "deploy"}),),
    )


def journal_event(seq: int, event_type: str, data: dict[str, JsonValue], *, run_id: str = "run-1") -> JournalEvent:
    return JournalEvent(
        org_id="org-1", session_id="ses-1", stream_id="ses-1", seq=seq, run_id=run_id,
        attempt_id="att-1", fence=1, type=event_type, critical=False,
        ts="2026-10-01T12:00:00Z", writer="worker", data=data,
    )


def as_journal_events(events: Sequence[NewEvent], *, first_seq: int = 10) -> list[JournalEvent]:
    """What the journal hands back for ``events`` once committed."""
    return [
        journal_event(first_seq + i, e.type, journaled(e.data))  # type: ignore[arg-type]
        for i, e in enumerate(events)
    ]


@dataclass
class Deltas:
    """An ``on_delta`` sink: what the live bus would have shown."""

    seen: list[tuple[DeltaKind, str]] = field(default_factory=list["tuple[DeltaKind, str]"])

    async def __call__(self, kind: DeltaKind, text: str) -> None:
        self.seen.append((kind, text))

    def text(self) -> str:
        return "".join(t for k, t in self.seen if k == "text_delta")


# ---- DP1 DurableModelPort ----


class ModelPortHarness(Protocol):
    def port(self, reply: Reply) -> DurableModelPort:
        """A port whose provider answers the next call with ``reply`` (text, tool calls)."""
        ...

    def failing(self) -> DurableModelPort:
        """A port whose provider fails the next call in a way no retry on it fixes."""
        ...

    def rate_limited(self) -> DurableModelPort | None:
        """A port whose provider asks to wait; ``None`` for a layer that leaves provider
        errors to the port wrapping it (``ClassifiedModelPort`` in PipesHub)."""
        ...


def _call(text: str = "When did the deploy finish?") -> ModelCall:
    return ModelCall(messages=[UserMessage(content=text)], system="Be brief.")


class DurableModelPortContract:
    @pytest.fixture
    def model_ports(self) -> ModelPortHarness:
        raise NotImplementedError("provide a model_ports fixture")

    async def test_dp1_1_the_streamed_text_is_the_journaled_text(self, model_ports: ModelPortHarness) -> None:
        port = model_ports.port(Reply(text="It finished at 10:42.", chunks=3))
        deltas = Deltas()
        turn = await port.sample(_call(), deltas)
        assert turn.message.text == "It finished at 10:42."
        assert deltas.text() == turn.message.text
        assert not turn.message.tool_calls

    async def test_dp1_2_a_tool_call_reaches_the_message_with_its_arguments(
        self, model_ports: ModelPortHarness
    ) -> None:
        reply = Reply(tool_calls=[ToolCallSpec(id="call_1", name="lookup", args={"q": "deploy"})])
        turn = await model_ports.port(reply).sample(_call(), Deltas())
        assert [(c.name, c.arguments) for c in turn.message.tool_calls or []] == [("lookup", {"q": "deploy"})]

    async def test_dp1_3_a_resume_on_the_same_provider_restores_the_sampled_message(
        self, model_ports: ModelPortHarness
    ) -> None:
        port = model_ports.port(Reply(text="It finished at 10:42.", reasoning="The log says 10:42."))
        turn = await port.sample(_call(), Deltas())
        assert turn.raw is not None, "a turn without raw state resumes from the normalized message only"
        assert port.restore(turn.raw, provider=turn.provider, transport=turn.transport) == turn.message

    async def test_dp1_4_another_providers_or_transports_state_is_never_restored(
        self, model_ports: ModelPortHarness
    ) -> None:
        port = model_ports.port(Reply(text="ok"))
        turn = await port.sample(_call(), Deltas())
        assert turn.raw is not None
        assert port.restore(turn.raw, provider=turn.provider + "-other", transport=turn.transport) is None
        assert port.restore(turn.raw, provider=turn.provider, transport=turn.transport + "-other") is None

    async def test_dp1_5_a_provider_failure_reaches_the_kernel_as_model_failed(
        self, model_ports: ModelPortHarness
    ) -> None:
        with pytest.raises(ModelFailed) as failed:
            _ = await model_ports.failing().sample(_call(), Deltas())
        assert failed.value.error_type

    async def test_dp1_6_a_rate_limit_is_a_backoff_with_its_wait(self, model_ports: ModelPortHarness) -> None:
        port = model_ports.rate_limited()
        if port is None:
            pytest.skip("this layer leaves provider errors to the port that wraps it")
        with pytest.raises(ModelBackoff) as backoff:
            _ = await port.sample(_call(), Deltas())
        assert backoff.value.retry_after_s > 0


# ---- DP2 DurableToolPort (and its TerminalTools facet) ----


class DurableToolHarness(Protocol):
    """``ToolCatalogHarness`` building a ``DurableToolPort``."""

    @property
    def runs(self) -> list[str]: ...

    def build(self, offered: Sequence[OfferedTool], grant: Collection[str]) -> DurableToolPort: ...


class DurableToolPortContract:
    """The harness builds the port over the offered tools (``tool_catalog.TOOLS``), granting
    ``GRANT``; ``ToolCatalogContract`` covers what it shares with ``ToolCatalog``."""

    @pytest.fixture
    def durable_tools(self) -> DurableToolHarness:
        raise NotImplementedError("provide a durable_tools fixture")

    def test_dp2_1_a_granted_tool_is_described_as_its_calls_are(self, durable_tools: DurableToolHarness) -> None:
        port = durable_tools.build(TOOLS, GRANT)
        described, by_call = port.describe(LOOKUP.name), port.describe_call(call(LOOKUP.name))
        assert described is not None and by_call is not None
        assert (described.tool_id, described.effect) == (by_call.tool_id, by_call.effect)
        assert described.tool_id == LOOKUP.effect.tool_id

    def test_dp2_2_a_withheld_or_unknown_tool_is_not_described(self, durable_tools: DurableToolHarness) -> None:
        port = durable_tools.build(TOOLS, GRANT)
        assert port.describe(DELETE.name) is None
        assert port.describe("ghost") is None

    async def test_dp2_3_a_call_to_a_tool_the_run_lacks_answers_with_unknown_and_runs_nothing(
        self, durable_tools: DurableToolHarness
    ) -> None:
        port = durable_tools.build(TOOLS, GRANT)
        message = port.unknown("ghost")
        assert "ghost" in message
        execution = await port.execute(call("ghost"), CTX)
        assert (execution.status, execution.content) == ("error", message)
        assert durable_tools.runs == []

    def test_dp2_4_only_a_granted_terminal_tool_ends_the_run(self, durable_tools: DurableToolHarness) -> None:
        port = durable_tools.build(TOOLS, GRANT)
        if not isinstance(port, TerminalTools):
            pytest.skip("the adapter has no terminal tools (TerminalTools is optional)")
        assert not port.ends_run(LOOKUP.name)
        assert not port.ends_run(DELETE.name)
        assert not port.ends_run("ghost")


# ---- DP3 PreRunStep ----


class PreRunStepContract:
    @pytest.fixture
    def pre_run_step(self) -> PreRunStep:
        raise NotImplementedError("provide a pre_run_step fixture")

    def test_dp3_1_the_key_names_one_slot_for_good(self, pre_run_step: PreRunStep) -> None:
        assert SLOT_KEY.match(pre_run_step.key)
        assert pre_run_step.key == pre_run_step.key

    async def test_dp3_2_the_result_reads_back_from_the_journal_unchanged(self, pre_run_step: PreRunStep) -> None:
        result = await pre_run_step.run("When did the deploy finish?")
        assert journaled(result) == result


# ---- DP4 PostRunStep ----


class PostRunStepContract:
    @pytest.fixture
    def post_run_step(self) -> PostRunStep:
        raise NotImplementedError("provide a post_run_step fixture")

    def test_dp4_1_the_key_names_one_slot_for_good(self, post_run_step: PostRunStep) -> None:
        assert SLOT_KEY.match(post_run_step.key)
        assert post_run_step.key == post_run_step.key

    @pytest.mark.parametrize("outcome", OUTCOMES)
    async def test_dp4_2_every_ending_settles_to_a_result_the_journal_holds(
        self, post_run_step: PostRunStep, outcome: RunEndingOutcome
    ) -> None:
        result = await post_run_step.run(ending(outcome))
        assert journaled(result) == result


# ---- DP5 PreRunGate ----


@dataclass(frozen=True)
class GateCase:
    """``asks``: pre-run slots the gate must ask on; ``proceeds``: slots it must run on."""

    gate: PreRunGate
    asks: Mapping[str, JsonValue]
    proceeds: Mapping[str, JsonValue]


class PreRunGateContract:
    @pytest.fixture
    def gate_case(self) -> GateCase:
        raise NotImplementedError("provide a gate_case fixture")

    def test_dp5_1_a_run_without_pre_run_results_never_asks(self, gate_case: GateCase) -> None:
        assert gate_case.gate.needs_input({}) is False

    def test_dp5_2_the_decision_is_the_same_on_the_journaled_slots(self, gate_case: GateCase) -> None:
        gate = gate_case.gate
        assert gate.needs_input(gate_case.asks) is True
        assert gate.needs_input(journaled(gate_case.asks)) is True  # type: ignore[arg-type]
        assert gate.needs_input(gate_case.proceeds) is False
        assert gate.needs_input(journaled(gate_case.proceeds)) is False  # type: ignore[arg-type]


# ---- DP6 CapabilityCheck ----


class CapabilityCheckContract:
    @pytest.fixture
    def capability_check(self) -> CapabilityCheck:
        raise NotImplementedError("provide a capability_check fixture")

    async def test_dp6_1_an_observation_is_digests_by_name(self, capability_check: CapabilityCheck) -> None:
        observed = await capability_check.observe()
        assert all(isinstance(k, str) for k in observed)

    async def test_dp6_2_unchanged_capabilities_observe_what_the_first_attempt_journaled(
        self, capability_check: CapabilityCheck
    ) -> None:
        recorded = journaled(dict(await capability_check.observe()))
        assert dict(await capability_check.observe()) == recorded

    async def test_dp6_3_a_withheld_capability_is_observed_with_its_recorded_mark(
        self, capability_check: CapabilityCheck
    ) -> None:
        """WP79-FU-5: what the first attempt was built without stays withheld, observed as it was recorded."""
        observed = dict(await capability_check.observe())
        await capability_check.withhold({})
        assert dict(await capability_check.observe()) == observed
        tool = next((k for k, v in sorted(observed.items()) if k.startswith("tool:") and isinstance(v, str)), None)
        if tool is None:
            return
        mark: dict[str, JsonValue] = {WITHDRAWN: "Example server"}
        await capability_check.withhold({tool: mark})
        assert (await capability_check.observe())[tool] == mark


# ---- DP7 RunPresenter ----

def _presented(message_text: str = "It finished at 10:42.") -> AssistantMessage:
    return AssistantMessage(
        content=[TextPart(text=message_text)],
        tool_calls=[ToolCall(id="c1", name="lookup", arguments={"q": "deploy"})],
    )


class RunPresenterContract:
    @pytest.fixture
    def presenters(self) -> Callable[[], RunPresenter]:
        raise NotImplementedError("provide a presenters fixture (a factory of fresh presenters)")

    async def _everything(self, presenter: RunPresenter) -> list[NewEvent]:
        events = [*presenter.pre_run({}), *presenter.response(_presented()), *presenter.response(AssistantMessage(content=[]))]
        for outcome in OUTCOMES:
            events.extend(await presenter.finalize(ending(outcome)))
        return events

    async def test_dp7_1_presentation_is_never_critical(self, presenters: Callable[[], RunPresenter]) -> None:
        assert all(not e.critical for e in await self._everything(presenters()))

    async def test_dp7_2_what_it_presents_reads_back_from_the_journal_unchanged(
        self, presenters: Callable[[], RunPresenter]
    ) -> None:
        for event in await self._everything(presenters()):
            assert journaled(event.data) == event.data, event.type

    @pytest.mark.parametrize("outcome", OUTCOMES)
    async def test_dp7_3_a_re_driven_attempt_finalizes_the_ending_as_the_first_did(
        self, presenters: Callable[[], RunPresenter], outcome: RunEndingOutcome
    ) -> None:
        first, second = presenters(), presenters()
        assert await first.finalize(ending(outcome)) == await second.finalize(ending(outcome))


# ---- DP8 RunRestore ----


class RestoreHarness(Protocol):
    def fresh(self) -> RunRestore:
        """A new attempt's instance, before it restores anything."""
        ...

    async def live(self) -> tuple[RunRestore, list[NewEvent]]:
        """An instance that ran part of a run, and the events it had journaled for it."""
        ...

    def state(self, restore: RunRestore) -> JsonValue:
        """The product state the instance keeps in process, comparably."""
        ...


class RunRestoreContract:
    @pytest.fixture
    def restores(self) -> RestoreHarness:
        raise NotImplementedError("provide a restores fixture")

    async def test_dp8_1_restoring_nothing_leaves_a_new_attempts_state(self, restores: RestoreHarness) -> None:
        restored = restores.fresh()
        await restored.restore([])
        assert restores.state(restored) == restores.state(restores.fresh())

    async def test_dp8_2_a_restored_attempt_continues_from_the_state_the_journal_rebuilt(
        self, restores: RestoreHarness
    ) -> None:
        live, events = await restores.live()
        assert events, "the harness's live run journals something to restore"
        restored = restores.fresh()
        await restored.restore(as_journal_events(events))
        assert restores.state(restored) == restores.state(live)

    async def test_dp8_3_events_it_does_not_own_change_nothing(self, restores: RestoreHarness) -> None:
        restored = restores.fresh()
        await restored.restore(
            [
                journal_event(1, "model.response", {"turn": 1, "messageRef": {"sha256": "0" * 64, "size": 1}}),
                journal_event(2, "x.other.product", {"anything": [1, 2]}),
            ]
        )
        assert restores.state(restored) == restores.state(restores.fresh())


# ---- DP9 InputRenderer ----


class InputRendererContract:
    @pytest.fixture
    def renderers(self) -> Callable[[], InputRenderer]:
        raise NotImplementedError("provide a renderers fixture (a factory of fresh renderers)")

    async def test_dp9_1_the_model_reads_what_the_user_typed(self, renderers: Callable[[], InputRenderer]) -> None:
        rendered = await renderers().render(SteeringInput(message_seq=7, text="Also list the failed jobs."))
        assert "Also list the failed jobs." in "".join(p.text for p in rendered.parts if isinstance(p, TextPart))

    async def test_dp9_2_a_render_again_before_the_consumption_committed_is_the_same_message(
        self, renderers: Callable[[], InputRenderer]
    ) -> None:
        message = SteeringInput(message_seq=7, text="Also list the failed jobs.")
        first = await renderers().render(message)
        again = await renderers().render(message)
        assert again.parts == first.parts

    async def test_dp9_3_its_events_are_returned_for_the_consumption_commit_never_critical(
        self, renderers: Callable[[], InputRenderer]
    ) -> None:
        rendered = await renderers().render(SteeringInput(message_seq=7, text="Also list the failed jobs."))
        assert all(not e.critical and journaled(e.data) == e.data for e in rendered.events)


# ---- DP12 ImportedHistory ----

IMPORTED = json.dumps(
    [
        {"role": "user_query", "content": "When did the deploy finish?"},
        {"role": "bot_response", "content": "At 10:42."},
    ]
).encode("utf-8")


class ImportedHistoryContract:
    @pytest.fixture
    def imported_history(self) -> ImportedHistory:
        raise NotImplementedError("provide an imported_history fixture")

    async def test_dp12_1_an_imported_exchange_is_the_user_then_the_assistant_message(
        self, imported_history: ImportedHistory
    ) -> None:
        messages = await imported_history.messages(IMPORTED)
        assert [type(m) for m in messages] == [UserMessage, AssistantMessage]
        assert "When did the deploy finish?" in _text(messages[0])
        assert "At 10:42." in _text(messages[1])

    async def test_dp12_2_an_empty_import_adds_nothing(self, imported_history: ImportedHistory) -> None:
        assert await imported_history.messages(b"[]") == []

    async def test_dp12_3_every_attempt_renders_the_same_history(self, imported_history: ImportedHistory) -> None:
        assert await imported_history.messages(IMPORTED) == await imported_history.messages(IMPORTED)


def _text(message: UserMessage | AssistantMessage | object) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return "".join(getattr(p, "text", "") for p in content)


__all__ = [
    "IMPORTED",
    "OUTCOMES",
    "CapabilityCheckContract",
    "Deltas",
    "DurableModelPortContract",
    "DurableToolHarness",
    "DurableToolPortContract",
    "GateCase",
    "ImportedHistoryContract",
    "InputRendererContract",
    "ModelPortHarness",
    "PostRunStepContract",
    "PreRunGateContract",
    "PreRunStepContract",
    "RestoreHarness",
    "RunPresenterContract",
    "RunRestoreContract",
    "as_journal_events",
    "ending",
    "journal_event",
    "journaled",
]
