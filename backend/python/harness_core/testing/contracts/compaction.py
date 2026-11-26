"""Contract suite for ``CompactionStrategy`` (P4.4).

Subclass :class:`CompactionStrategyContract` and provide a ``strategy`` fixture. A strategy that
calls a model is given one that answers (a scripted model); the kernel journals whatever it returns
as ``context.compacted``, so the contract is about the summary only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.context.ports import CompactionInputEmpty, CompactionRequest
from harness_core.context.types import SeqSpan
from harness_core.kernel.core.messages import (
    AssistantMessage,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)

if TYPE_CHECKING:
    from harness_core.context.ports import CompactionStrategy
    from harness_core.kernel.core.messages import Message


def range_messages() -> tuple[Message, ...]:
    """A range with a question, a call, its (long) result and an answer."""
    return (
        UserMessage(content="Which tickets mention the outage on 12 March?"),
        AssistantMessage(
            content=[TextPart(text="Searching.")],
            tool_calls=[ToolCall(id="c1", name="search_tickets", arguments={"q": "outage"})],
        ),
        ToolMessage(content="TICKET-7 outage on 12 March; " * 200, tool_call_id="c1"),
        AssistantMessage(content=[TextPart(text="TICKET-7 mentions it.")]),
    )


class CompactionStrategyContract:
    @pytest.fixture
    def strategy(self) -> CompactionStrategy:
        raise NotImplementedError("provide a strategy fixture")

    async def test_cs1_a_range_becomes_a_non_empty_summary_within_its_size(self, strategy: CompactionStrategy) -> None:
        request = CompactionRequest(span=SeqSpan(1, 9), messages=range_messages(), max_chars=600)
        summary = await strategy.compact(request)
        assert summary.text.strip()
        assert len(summary.text) <= 600
        assert summary.strategy == strategy.name

    async def test_cs1_an_empty_range_is_refused(self, strategy: CompactionStrategy) -> None:
        with pytest.raises(CompactionInputEmpty) as refused:
            _ = await strategy.compact(CompactionRequest(span=SeqSpan(1, 1), messages=()))
        assert refused.value.reason_code == "compaction_input_empty"

    async def test_cs1_the_request_is_not_mutated(self, strategy: CompactionStrategy) -> None:
        messages = range_messages()
        before = [m.model_dump(mode="json") for m in messages]
        _ = await strategy.compact(CompactionRequest(span=SeqSpan(1, 9), messages=messages))
        assert [m.model_dump(mode="json") for m in messages] == before


__all__ = ["CompactionStrategyContract", "range_messages"]
