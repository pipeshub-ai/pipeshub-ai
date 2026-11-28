"""The PipesHub presenter on durable runs (L-18): failures the legacy stream answered gracefully."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.models.stream_watch import ModelStalled
from tests.contracts.golden_support import Scenario
from tests.contracts.test_durable_goldens import run_durably

if TYPE_CHECKING:
    from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


def _server_error(transport: ScriptedTransport) -> None:
    # The kernel retries a transient failure twice on the same route (P4.9) before the run fails.
    for _ in range(3):
        _ = transport.add_error(Exception("Error code: 500 - {'error': {'message': 'internal server error'}}"))


def _stalled(transport: ScriptedTransport) -> None:
    for _ in range(3):
        _ = transport.add_error(ModelStalled("idle", 120.0, 1))


def _rate_limited(transport: ScriptedTransport) -> None:
    _ = transport.add_error(Exception("Error code: 429 - rate limit exceeded"))


async def test_l18_a_model_failure_ends_in_the_legacy_error_answer() -> None:
    events, _ = await run_durably(Scenario("agent_llm_error", "agent", "quick", "Hi there", _server_error))
    types = [e.type for e in events]
    assert types[-2:] == ["x.pipeshub.answer.finalized", "run.failed"]
    finalized = events[-2].data
    assert finalized["outcome"] == "error"
    assert finalized["answerMatchType"] == "Error"
    assert finalized["errorCode"] == "server_error"
    assert finalized["partTexts"] == [finalized["answer"]]
    assert "internal server error" not in str(finalized)
    assert events[-1].data == {"errorType": "server_error"}


async def test_st15_a_stalled_model_ends_in_the_legacy_timeout_answer() -> None:
    events, _ = await run_durably(Scenario("agent_llm_stalled", "agent", "quick", "Hi there", _stalled))
    finalized = events[-2].data
    assert (events[-2].type, finalized["outcome"], finalized["errorCode"]) == (
        "x.pipeshub.answer.finalized",
        "error",
        "timeout",
    )
    assert "took too long" in finalized["answer"]
    assert events[-1].data == {"errorType": "timeout"}
    assert len([e for e in events if e.type == "model.response"]) == 0


async def test_a_rate_limit_is_a_backoff_the_run_yields_on() -> None:
    events, _ = await run_durably(Scenario("agent_rate_limited", "agent", "quick", "Hi there", _rate_limited))
    yielded = [e for e in events if e.type == "run.yielded"]
    assert [e.data["reason"] for e in yielded] == ["llm_backoff"]
    assert "run.failed" not in [e.type for e in events]


def test_lc2fix_a_run_its_budget_stopped_answers_with_that_code_and_says_so() -> None:
    # LIFECYCLE-2: a budget-exhausted run answered "Something went wrong" (errorCode unknown).
    from app.agents.agent_loop.error_classification import _USER_MESSAGES
    from app.harness.contracts.generated.pipeshub.enums import ANSWER_ERROR_CODES
    from app.harness.presentation import _error_answer

    class _Collector:
        parts: list[object] = []

        def replace_final_text(self, text: str) -> None:
            self.text = text

    for code in ("budget_exhausted", "budget_insufficient"):
        answer = _error_answer(_Collector(), code)
        assert answer["errorCode"] == code
        assert answer["answer"].startswith("This reply stopped because")
        assert "spending limit" in answer["answer"]
    # Every code the worker answers with is one the web app words (ERR-UX).
    assert set(_USER_MESSAGES) <= set(ANSWER_ERROR_CODES)
