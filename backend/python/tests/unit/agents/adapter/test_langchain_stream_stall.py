"""P4.9 ST-14: when the caller's stall watch gives up on a LangChain stream, the provider read is
cancelled and its iterator closed, so no task is left holding the connection."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from langchain_core.messages import AIMessageChunk

from app.agent_loop_lib.core.messages import UserMessage
from app.agents.agent_loop.langchain_transport import LangChainTransport
from harness_core.models.stream_watch import ModelStalled, StreamTimeouts
from harness_core.models.transport_model import TransportModel

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _StallingModel:
    """Streams ``before`` chunks, then waits forever for the next one, recording how it ended."""

    def __init__(self, before: int) -> None:
        self.before = before
        self.ended: list[str] = []

    def bind_tools(self, tools: list[Any]) -> _StallingModel:
        return self

    async def astream(self, messages: list, config: Any = None) -> AsyncIterator[AIMessageChunk]:  # noqa: ANN401
        try:
            for i in range(self.before):
                yield AIMessageChunk(content=f"part{i} ")
            await asyncio.Event().wait()
            yield AIMessageChunk(content="never")
        except asyncio.CancelledError:
            self.ended.append("cancelled")
            raise
        except GeneratorExit:
            self.ended.append("closed")
            raise


@pytest.mark.parametrize("before", [0, 2])
async def test_st14_a_stalled_langchain_stream_is_cancelled_and_closed(before: int) -> None:
    llm = _StallingModel(before)
    model = TransportModel(
        LangChainTransport(llm, model_name="fake"),  # type: ignore[arg-type]
        timeouts=StreamTimeouts(first_event_s=0.2, idle_s=0.05, response_s=0.2),
    )
    seen: list[Any] = []
    with pytest.raises(ModelStalled) as caught:
        async for event in model.stream([UserMessage(content="hi")]):
            seen.append(event)  # noqa: PERF401 - a comprehension loses the events before the stall
    assert len(seen) == before
    assert caught.value.retryable is (before == 0)
    await asyncio.sleep(0)
    assert llm.ended, "the provider iterator was cancelled or closed, not left reading"
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    assert pending == [], "no orphaned chunk read"
