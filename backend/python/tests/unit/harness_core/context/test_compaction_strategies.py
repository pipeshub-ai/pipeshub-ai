"""P4.4 CS-1..CS-3: the contract on both adapters, and what each adds."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from harness_core.context import (
    CompactionFailed,
    CompactionRequest,
    ModelCompaction,
    SeqSpan,
    TruncationCompaction,
)
from harness_core.context.quoting import OPEN
from harness_core.kernel.core.messages import AssistantMessage, TextPart, UserMessage
from harness_core.kernel.core.responses import ModelResponse, StructuredResponse
from harness_core.models.base import Model
from harness_core.testing.contracts.compaction import (
    CompactionStrategyContract,
    range_messages,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.core.streaming import StreamEvent
    from harness_core.kernel.core.tool_schema import ToolSchema


class AnsweringModel(Model):
    """Answers every call with ``text`` and keeps what it was sent."""

    def __init__(self, text: str = "Goal: find outage tickets.\nDecisions made: TICKET-7 is the one.") -> None:
        self.text = text
        self.sent: list[list[Message]] = []

    @property
    def model_name(self) -> str:
        return "answering"

    async def complete(self, messages: list[Message], tools: list[ToolSchema] | None = None, system: str | None = None, **_: Any) -> ModelResponse:  # noqa: ANN401
        self.sent.append(list(messages))
        return ModelResponse(message=AssistantMessage(content=[TextPart(text=self.text)]))

    async def complete_structured(self, messages: list[Message], output_schema: dict[str, Any], system: str | None = None, model: str | None = None) -> StructuredResponse:
        raise NotImplementedError

    def stream(self, messages: list[Message], tools: list[ToolSchema] | None = None, system: str | None = None, **_: Any) -> AsyncIterator[StreamEvent]:  # noqa: ANN401
        raise NotImplementedError


class TestTruncationCompaction(CompactionStrategyContract):
    @pytest.fixture
    def strategy(self) -> TruncationCompaction:
        return TruncationCompaction()


class TestModelCompaction(CompactionStrategyContract):
    @pytest.fixture
    def strategy(self) -> ModelCompaction:
        return ModelCompaction(AnsweringModel())


async def test_cs2_truncation_is_deterministic() -> None:
    request = CompactionRequest(span=SeqSpan(1, 9), messages=range_messages(), max_chars=500)
    one, two = await TruncationCompaction().compact(request), await TruncationCompaction().compact(request)
    assert one.text == two.text
    assert "(called search_tickets)" in one.text


async def test_cs3_the_summarising_call_sends_the_range_quoted_with_the_instruction() -> None:
    model = AnsweringModel()
    summary = await ModelCompaction(model).compact(CompactionRequest(span=SeqSpan(1, 9), messages=range_messages()))
    assert summary.text.startswith("Goal: find outage tickets.")
    [sent] = model.sent
    assert len(sent) == 1 and isinstance(sent[0], UserMessage) and isinstance(sent[0].content, str)
    prompt = sent[0].content
    assert prompt.startswith("Summarise the conversation quoted below")
    assert OPEN in prompt and "- user: Which tickets mention the outage" in prompt
    # No tool-use blocks for a provider to pair.
    assert all(not isinstance(m, AssistantMessage) for m in sent)


async def test_cs3_an_empty_answer_is_compaction_failed() -> None:
    with pytest.raises(CompactionFailed) as failed:
        _ = await ModelCompaction(AnsweringModel("  ")).compact(
            CompactionRequest(span=SeqSpan(1, 9), messages=range_messages())
        )
    assert failed.value.reason_code == "compaction_failed"
