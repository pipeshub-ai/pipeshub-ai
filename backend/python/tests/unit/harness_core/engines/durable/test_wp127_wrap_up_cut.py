"""WP-127: a wrap-up answer the model's output limit cut ends on a whole paragraph.

Two recorded wrap-ups both stopped at exactly their 8,000-token limit (``engine.wrap_up.answered
outputTokens=8000``, 27,596 and 32,909 characters), so the person's answer ended mid-sentence and without the
"Still to do" section the prompt asks for last. The run's notice already says the result is partial; the text it
sits under should not end in half a word. Fake transport only.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.types import Cap, PinnedBudget, RunAccountRef
from harness_core.budget.wrap_up import WrapUpReserve, WrapUpSize
from harness_core.engines.durable.wrap_up import ModelEngineWrapUp
from harness_core.kernel.core.messages import AssistantMessage, TextPart
from harness_core.kernel.core.responses import ModelResponse, StopReason, TokenUsage
from harness_core.kernel.core.streaming import StreamCompleteEvent
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.engine_wrap_up import GATHERED, REQUEST
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_gateway import RecordedTransport

if TYPE_CHECKING:
    import pytest

WHOLE = "## Findings\n\nPP-102 is the highest priority [source](https://jira.example/PP-102).\n\nPP-101 fixes login."
CUT_TAIL = "\n\n| Ticket | Why |\n|---|---|\n| PP-103 | Blocks the rel"


def _wrap_up(text: str, stop: StopReason) -> ModelEngineWrapUp:
    ledger = InMemoryBudgetLedger(ManualClock())
    run = RunAccountRef("org-1", "engine-1")
    ledger.pin(run, PinnedBudget(Cap(None, 500_000)))
    reserve = WrapUpReserve(accounts=ledger, account=run, pricing_model="gpt-x", ids=SeededIds(1), size=WrapUpSize(4_000, 1_000))
    response = ModelResponse(
        message=AssistantMessage(content=[TextPart(text=text)]),
        usage=TokenUsage(input_tokens=900, output_tokens=1_000),
        stop_reason=stop,
    )
    return ModelEngineWrapUp(reserve=reserve, transport=RecordedTransport([StreamCompleteEvent(response=response)]))


async def test_wp127_wrap_1_an_answer_cut_at_its_limit_ends_on_its_last_whole_paragraph(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    answer = await _wrap_up(WHOLE + CUT_TAIL, StopReason.MAX_TOKENS).answer(REQUEST, GATHERED, "time")
    assert answer == WHOLE
    assert any("engine.wrap_up.answered" in r.getMessage() and "cut=True" in r.getMessage() for r in caplog.records)


async def test_wp127_wrap_2_a_finished_answer_is_kept_whole() -> None:
    answer = await _wrap_up(WHOLE + CUT_TAIL, StopReason.END_TURN).answer(REQUEST, GATHERED, "time")
    assert answer == WHOLE + CUT_TAIL


async def test_wp127_wrap_3_a_cut_answer_of_one_paragraph_keeps_its_whole_sentences() -> None:
    one = "PP-102 is first. PP-101 is second. PP-103 blocks the rel"
    answer = await _wrap_up(one, StopReason.MAX_TOKENS).answer(REQUEST, GATHERED, "time")
    assert answer == "PP-102 is first. PP-101 is second."
