"""P4 FL-3 on the product path: with ``HARNESS_CONTEXT_COMPILER`` on, a PipesHub durable run (the real
assembler, hooks and tools) answers as with it off, and every request it sends is compiled in bands:
the tools sorted by registry path, the run's context journaled as a note before its input (P4.11), each
request a prefix of the next one's."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from tests.contracts.test_durable_goldens import (
    AGENT_SCENARIOS,
    durable_transcript,
    run_durably,
)

if TYPE_CHECKING:
    from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

# Scripted runs that write after reading, compared with today's loop or with recordings made before the flow
# guard (on by default since UX1), which would hold those writes for a person. The guard is tested on its own.
pytestmark = pytest.mark.usefixtures("flow_guard_off")

SCENARIO = next(s for s in AGENT_SCENARIOS if s.chat_mode == "react" and not s.llm_unavailable)


def _is_note(message: Any) -> bool:  # noqa: ANN401
    from harness_core.context.notes import NOTE_HEADER
    from harness_core.kernel.core.messages import UserMessage

    return isinstance(message, UserMessage) and message.injected and str(message.content).startswith(NOTE_HEADER)


def _history(call: dict[str, Any]) -> list[str]:
    """The whole request: nothing in it is dropped by the next one (P4.11)."""
    return [json.dumps(m.model_dump(mode="json"), sort_keys=True) for m in call["messages"]]


async def test_fl3_a_pipeshub_run_with_the_compiler_answers_and_sends_banded_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HARNESS_CONTEXT_COMPILER", "false")
    off: list[ScriptedTransport] = []
    events, content = await run_durably(SCENARIO, transports=off)
    baseline = await durable_transcript(events, content)

    monkeypatch.setenv("HARNESS_CONTEXT_COMPILER", "true")
    on: list[ScriptedTransport] = []
    events, content = await run_durably(SCENARIO, transports=on)
    compiled = await durable_transcript(events, content)
    assert compiled.answer == baseline.answer
    assert compiled.tools == baseline.tools

    order = [e.data["slots"]["context.toolOrder"] for e in events if e.type == "loop.state_saved" and "context.toolOrder" in e.data["slots"]]
    assert order and order[0] == sorted(order[0])
    # The agent's turns (the intent pre-run's call is not a turn); each extends the last.
    calls = _turns(on[0])
    assert len(calls) >= 2
    for before, after in zip(calls, calls[1:], strict=False):
        assert _history(after)[: len(_history(before))] == _history(before)
        assert after["system"] == before["system"]


# ---- P4.6: the run's input is the goal on both paths; P4.5: time and the step leave history and the system band ----

PARITY_SCENARIOS = [
    next(s for s in AGENT_SCENARIOS if s.name == "agent_react_tool_error_then_success"),
    next(s for s in AGENT_SCENARIOS if s.name == "agent_quick_tool_call"),
]


def _first_inputs(transport: ScriptedTransport) -> list[str]:
    """Each request's first message that is not the system's note (the run's input)."""
    return [
        json.dumps(next(m for m in c["messages"] if not _is_note(m)).model_dump(mode="json"), sort_keys=True)
        for c in transport.calls
    ]


def _goal(events: list[Any]) -> str:
    (slot,) = [
        e.data["slots"]["pre_run.goal"]
        for e in events
        if e.type == "loop.state_saved" and "pre_run.goal" in e.data["slots"]
    ]
    return str(slot["description"])


def _turns(transport: ScriptedTransport) -> list[dict[str, Any]]:
    """The agent's turns with the compiler on: each carries the run's note (the time context)."""
    return [c for c in transport.calls if any(_is_note(m) and "Current time" in str(m.content) for m in c["messages"])]


async def test_p46_4_with_the_compiler_the_react_run_starts_from_the_persons_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The intent rewrite is the goal the planners read; the run's first message is what the person typed
    (main #4534), as legacy's ``Goal.request_text``."""
    scenario = PARITY_SCENARIOS[0]
    monkeypatch.setenv("HARNESS_CONTEXT_COMPILER", "true")
    on: list[ScriptedTransport] = []
    events, _ = await run_durably(scenario, transports=on)
    assert _goal(events) != scenario.query, "the scripted intent call rewrites a react query"
    turns = _turns(on[0])
    assert turns
    assert all(c["messages"][0].content == scenario.query and _is_note(c["messages"][1]) for c in turns)


@pytest.mark.parametrize("scenario", PARITY_SCENARIOS, ids=[s.name for s in PARITY_SCENARIOS])
async def test_p46_5_the_shaper_and_the_compiler_send_the_same_goal_text(
    scenario: Any,  # noqa: ANN401
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HARNESS_CONTEXT_COMPILER", "false")
    off: list[ScriptedTransport] = []
    off_events, _ = await run_durably(scenario, transports=off)
    monkeypatch.setenv("HARNESS_CONTEXT_COMPILER", "true")
    on: list[ScriptedTransport] = []
    on_events, _ = await run_durably(scenario, transports=on)
    assert _goal(on_events) == _goal(off_events)
    assert _first_inputs(on[0]) == _first_inputs(off[0])


async def test_p46_5_deep_every_agent_starts_from_the_same_goal_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.contracts.deep_scenarios import DEEP_SCENARIOS
    from tests.contracts.test_durable_deep_goldens import run_deep_durably

    deep = DEEP_SCENARIOS[0]
    monkeypatch.setenv("HARNESS_CONTEXT_COMPILER", "false")
    off = await run_deep_durably(deep)
    monkeypatch.setenv("HARNESS_CONTEXT_COMPILER", "true")
    on = await run_deep_durably(deep)
    assert sorted(on.script.agents) == sorted(off.script.agents)
    for key, agent in off.script.agents.items():
        if not agent.calls:
            continue
        assert _first_inputs(on.script.agents[key]) == _first_inputs(agent), key


async def test_p45_1_p48_p411_time_is_a_note_after_the_input_and_the_step_on_each_result_as_with_the_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P4.8 FB-11: the step footer is history (fixed once its batch is complete), so the compiled
    request carries it where the shaper does. P4.11: the time leaves the system band for the run's note, right
    after its input (P4.15), and nothing the system says follows the results."""
    from harness_core.kernel.core.messages import ToolMessage

    monkeypatch.setenv("HARNESS_CONTEXT_COMPILER", "false")
    off: list[ScriptedTransport] = []
    _ = await run_durably(SCENARIO, transports=off)
    monkeypatch.setenv("HARNESS_CONTEXT_COMPILER", "true")
    on: list[ScriptedTransport] = []
    _ = await run_durably(SCENARIO, transports=on)
    turns = _turns(on[0])
    assert len(turns) >= 2
    for call in turns:
        assert "Current time" not in (call["system"] or "")
        assert _is_note(call["messages"][1]) and "Current time" in str(call["messages"][1].content)
        assert not any(isinstance(m, ToolMessage) for m in call["messages"]) or not _is_note(call["messages"][-1])
        assert "each tool result ends with `[loop: step N/MAX, stale_rounds=K]`" in call["system"]

    def footers(call: dict[str, Any]) -> list[tuple[str | None, str]]:
        return [(m.tool_call_id, m.step_footer) for m in call["messages"] if isinstance(m, ToolMessage)]

    assert footers(turns[-1]) == footers(off[0].calls[-1])
    assert any(f for _, f in footers(turns[-1]))


async def test_p45_3_with_the_flag_off_footers_and_wording_are_today_s(monkeypatch: pytest.MonkeyPatch) -> None:
    from harness_core.kernel.core.messages import ToolMessage

    monkeypatch.setenv("HARNESS_CONTEXT_COMPILER", "false")
    off: list[ScriptedTransport] = []
    _ = await run_durably(SCENARIO, transports=off)
    last = off[0].calls[-1]
    assert "each tool result ends with `[loop: step N/MAX, stale_rounds=K]`" in last["system"]
    assert any(isinstance(m, ToolMessage) and "[loop: step" in m.step_footer for m in last["messages"])
