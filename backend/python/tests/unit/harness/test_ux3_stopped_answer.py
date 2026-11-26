"""UX3 ST-5: the answer a stopped durable run saves is the text the user saw.

Today's loop finalizes a Stop with the text streamed so far, written into the
message that was streaming (`replace_final_text`). The durable presenter does
the same: the partial goes into a part of its own, so the earlier turns' text
the user saw complete is kept. Stopped between steps, it ends as today's loop does.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.agent_loop import respond
from app.harness.presentation import PipesHubPresenter
from harness_core.kernel.durable.ports import RunEnding
from harness_core.tools.registry import ToolRegistry


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    class Finalizer:
        def __init__(self, context: Any, *_args: Any) -> None:  # noqa: ANN401
            self._context = context

        async def run(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
            seen.update(kwargs)
            collector = self._context.transcript_collector
            seen["parts"] = [dict(part) for part in collector.parts]
            collector.replace_final_text(kwargs["streamed_answer"])
            return {"answer": kwargs["streamed_answer"], "parts": collector.parts, "citations": []}

    monkeypatch.setattr(respond, "AnswerFinalizer", Finalizer)
    return seen


def _presenter() -> PipesHubPresenter:
    context = SimpleNamespace(tool_state={}, root_agent_spec=None, conversation_id=None, run_id="run-1")
    return PipesHubPresenter(context, ToolRegistry())


def _finalized(events: list[Any]) -> dict[str, Any]:
    [event] = [e for e in events if e.type == "x.pipeshub.answer.finalized"]
    return dict(event.data)


async def test_st5_a_stop_mid_message_saves_the_streamed_text_after_the_earlier_turns(seen: dict[str, Any]) -> None:
    events = await _presenter().finalize(
        RunEnding(outcome="stopped", texts=("Let me look that up.",), answer="The Q3 plan is to grow")
    )
    assert seen["agent_cancelled"] is True
    assert seen["streamed_answer"] == "The Q3 plan is to grow"
    data = _finalized(events)
    assert data["outcome"] == "stopped"
    assert data["answer"] == "The Q3 plan is to grow"
    assert data["partTexts"] == ["Let me look that up.", "The Q3 plan is to grow"]


async def test_st5_a_stop_between_steps_ends_as_todays_loop_does(seen: dict[str, Any]) -> None:
    # No call was streaming: the stopped answer is empty, as legacy's is (C-24 parity, agent_cancel_mid_stream).
    events = await _presenter().finalize(RunEnding(outcome="stopped", texts=("Let me look that up.",)))
    assert seen["streamed_answer"] == ""
    assert _finalized(events)["partTexts"] == [""]
