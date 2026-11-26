"""A per-call timeout bounds the provider call only, never the wait for
the shared indexing model slot, and applies to every reflection retry."""
from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from app.services.llm_gateway.gateway import LLMGateway
from app.utils import streaming
from app.utils.streaming import (
    _ainvoke_throttled,
    invoke_with_structured_output_and_reflection,
)
from tests.support.loop_clock import virtual_loop_time

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


class _Answer(BaseModel):
    value: int


class _Llm:
    """``ainvoke`` takes ``delays[i]`` seconds and returns ``replies[i]``."""

    def __init__(self, replies: list[str], delays: list[float]) -> None:
        self.replies, self.delays, self.calls = list(replies), list(delays), 0

    async def ainvoke(self, messages: list) -> AIMessage:
        i = self.calls
        self.calls += 1
        await asyncio.sleep(self.delays[i])
        return AIMessage(content=self.replies[i])


@contextlib.asynccontextmanager
async def _slow_slot(delay: float) -> AsyncIterator[None]:
    await asyncio.sleep(delay)
    yield


def _fresh_gateway(monkeypatch: pytest.MonkeyPatch) -> LLMGateway:

    fresh = LLMGateway(4)
    monkeypatch.setattr(streaming, "get_llm_gateway", lambda: fresh)
    monkeypatch.setattr("app.services.llm_gateway.gateway.get_llm_gateway", lambda: fresh)
    return fresh


@pytest.fixture(autouse=True)
async def _loop_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    # Every timeout here is on a loop clock the test controls. On the wall clock a busy runner's stall inside a 0.1 s
    # budget timed out a call that had already returned, and the slot test failed although the slot wait was not timed.
    virtual_loop_time(monkeypatch)


@pytest.fixture
def no_structured_output() -> Iterator[None]:
    with patch.object(streaming, "_apply_structured_output", side_effect=lambda llm, schema: llm):
        yield


class TestAinvokeThrottled:
    async def test_slow_call_times_out(self) -> None:
        llm = _Llm(["x"], [1.0])
        with pytest.raises(asyncio.TimeoutError):
            await _ainvoke_throttled(llm, [HumanMessage(content="q")], call_timeout=0.05)

    async def test_waiting_for_the_slot_is_not_timed(self, monkeypatch) -> None:
        gateway = _fresh_gateway(monkeypatch)
        monkeypatch.setattr(gateway.limiter, "slot", lambda: _slow_slot(0.2))
        llm = _Llm(["ok"], [0.0])
        loop = asyncio.get_running_loop()
        started = loop.time()
        result = await _ainvoke_throttled(llm, [HumanMessage(content="q")], call_timeout=0.1)
        assert result.content == "ok"
        assert loop.time() - started == pytest.approx(0.2)  # the whole 0.2 s slot wait, past the 0.1 s call budget

    async def test_no_timeout_by_default(self) -> None:
        llm = _Llm(["ok"], [0.05])
        result = await _ainvoke_throttled(llm, [HumanMessage(content="q")])
        assert result.content == "ok"

    async def test_timeout_is_not_retried_as_a_rate_limit(self) -> None:
        llm = _Llm(["x", "x"], [1.0, 0.0])
        with pytest.raises(asyncio.TimeoutError):
            await _ainvoke_throttled(llm, [HumanMessage(content="q")], call_timeout=0.05)
        assert llm.calls == 1

    async def test_the_callers_timeout_does_not_count_against_the_provider(self, monkeypatch) -> None:
        gateway = _fresh_gateway(monkeypatch)
        for _ in range(gateway.breaker("p").failure_threshold):
            with pytest.raises(asyncio.TimeoutError):
                await _ainvoke_throttled(_Llm(["x"], [1.0]), [], provider="p", call_timeout=0.01)
        assert gateway.breaker("p").state == "closed"

    async def test_a_rate_limit_retry_is_counted_in_the_slot_stats(self, monkeypatch) -> None:
        from app.services.llm_gateway import gateway as gateway_module
        from app.utils import concurrency

        _fresh_gateway(monkeypatch)

        class _RateLimitedOnce:
            calls = 0

            async def ainvoke(self, messages: list) -> AIMessage:
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("429 Too Many Requests: rate limit exceeded")
                return AIMessage(content="ok")

        with patch.object(gateway_module.asyncio, "sleep", AsyncMock()):
            result = await _ainvoke_throttled(_RateLimitedOnce(), [HumanMessage(content="q")])

        assert result.content == "ok"
        stats = concurrency.indexing_llm_stats()
        assert stats["rate_limit_retries"] == 1
        assert stats["acquired"] == 2


class TestStructuredOutputTimeout:
    async def test_timed_out_call_returns_none(self, no_structured_output) -> None:
        llm = _Llm(['{"value": 1}'], [1.0])
        result = await invoke_with_structured_output_and_reflection(
            llm, [HumanMessage(content="q")], _Answer, call_timeout=0.05,
        )
        assert result is None

    async def test_reflection_retry_is_timed_too(self, no_structured_output) -> None:
        llm = _Llm(["not json", '{"value": 2}'], [0.0, 1.0])
        loop = asyncio.get_running_loop()
        started = loop.time()
        result = await invoke_with_structured_output_and_reflection(
            llm, [HumanMessage(content="q")], _Answer, max_retries=1, call_timeout=0.05,
        )
        assert result is None
        assert loop.time() - started == pytest.approx(0.05)  # the retry was cut at its 0.05 s budget, not after 1 s

    async def test_without_timeout_behaviour_is_unchanged(self, no_structured_output) -> None:
        llm = _Llm(['{"value": 3}'], [0.05])
        result = await invoke_with_structured_output_and_reflection(
            llm, [HumanMessage(content="q")], _Answer,
        )
        assert result == _Answer(value=3)



class TestReflectionRetryAfterAFailedCall:
    async def test_a_raising_retry_moves_on_to_the_next_attempt(self, no_structured_output) -> None:
        """A retry that raises before producing any text used to hit an unbound
        name and escape the helper."""
        llm = _Llm(["not json", "unused", '{"value": 4}'], [0.0, 1.0, 0.0])
        result = await invoke_with_structured_output_and_reflection(
            llm, [HumanMessage(content="q")], _Answer, max_retries=2, call_timeout=0.05,
        )
        assert result == _Answer(value=4)
        assert llm.calls == 3
