"""Contract suite for ``EngineWrapUp`` (ENGBUDGET): an engine run's answer after a limit stopped its engine.

A subclass provides ``engine_wrap_ups``, an ``EngineWrapUpHarness`` that builds the port over an account with room
for the reserve (``fresh``) and over one without (``starved``), and reads back what the account holds and what the
model was sent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.engines.durable.ports import GatheredResult

if TYPE_CHECKING:
    from harness_core.engines.durable.ports import EngineWrapUp

REQUEST = "Which PP tickets should be picked up first?"
GATHERED = (
    GatheredResult("searchJiraIssuesUsingJql", '{"issues": [{"key": "PP-101", "summary": "Fix login"}]}'),
    GatheredResult("getJiraIssue", '{"key": "PP-102", "priority": "High"}'),
)


class EngineWrapUpHarness(Protocol):
    def fresh(self) -> EngineWrapUp: ...

    def starved(self) -> EngineWrapUp: ...

    async def reserved_tokens(self) -> int: ...

    async def committed_tokens(self) -> int: ...

    def prompts(self) -> list[str]:
        """Every request the model was sent, its messages' text joined."""
        ...


class EngineWrapUpContract:
    @pytest.fixture
    def engine_wrap_ups(self) -> EngineWrapUpHarness:
        raise NotImplementedError("provide an engine_wrap_ups fixture")

    async def test_ew1_hold_holds_the_reserve_back(self, engine_wrap_ups: EngineWrapUpHarness) -> None:
        port = engine_wrap_ups.fresh()
        assert await port.hold()
        assert await engine_wrap_ups.reserved_tokens() > 0

    async def test_ew2_the_answer_is_paid_from_the_reserve(self, engine_wrap_ups: EngineWrapUpHarness) -> None:
        port = engine_wrap_ups.fresh()
        assert await port.hold()
        answer = await port.answer(REQUEST, GATHERED, "budget")
        assert answer
        assert await engine_wrap_ups.reserved_tokens() == 0, "drawn and settled, nothing left held"
        assert await engine_wrap_ups.committed_tokens() > 0

    async def test_ew3_the_model_reads_the_request_and_the_results(self, engine_wrap_ups: EngineWrapUpHarness) -> None:
        port = engine_wrap_ups.fresh()
        assert await port.hold()
        _ = await port.answer(REQUEST, GATHERED, "budget")
        sent = engine_wrap_ups.prompts()[-1]
        assert REQUEST in sent and "PP-101" in sent and "PP-102" in sent

    async def test_ew6_a_continuation_extends_the_earlier_partial_result(
        self, engine_wrap_ups: EngineWrapUpHarness
    ) -> None:
        """SPENDCONT: the wrap-up of a run that continues a cut-short task reads what the earlier run produced, so
        the partial result grows instead of being replaced by this run's results alone."""
        port = engine_wrap_ups.fresh()
        assert await port.hold()
        _ = await port.answer(REQUEST, GATHERED, "budget", earlier="## Inventory\n- PP-007: SSRF in fetch")
        sent = engine_wrap_ups.prompts()[-1]
        assert "PP-007: SSRF in fetch" in sent and "PP-101" in sent

    async def test_ew4_no_room_answers_none_without_raising(self, engine_wrap_ups: EngineWrapUpHarness) -> None:
        port = engine_wrap_ups.starved()
        assert not await port.hold()
        assert await port.answer(REQUEST, GATHERED, "budget") is None

    async def test_ew5_release_returns_the_reserve(self, engine_wrap_ups: EngineWrapUpHarness) -> None:
        port = engine_wrap_ups.fresh()
        assert await port.hold()
        await port.release()
        assert await engine_wrap_ups.reserved_tokens() == 0
        await port.release()


__all__ = ["GATHERED", "REQUEST", "EngineWrapUpContract", "EngineWrapUpHarness"]
