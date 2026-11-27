"""P4.17 open item 2 (P4.17L RT-1..RT-4): each call's reasoning share of its output is kept per call, where the
provider reports it, so a runaway reads as reasoning or as a long reply from its own ``model.response`` line."""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage

from app.agents.agent_loop.converters import token_usage_from_ai_message
from harness_core.kernel.core.responses import TokenUsage
from harness_core.models.transports.gemini import GeminiTransport
from harness_core.models.transports.openai import OpenAITransport
from harness_core.models.transports.openai_responses import responses_reasoning_tokens


def test_rt1_langchain_usage_keeps_the_reasoning_share() -> None:
    message = AIMessage(
        content="",
        usage_metadata={
            "input_tokens": 50,
            "output_tokens": 4_096,
            "total_tokens": 4_146,
            "output_token_details": {"reasoning": 4_000},
        },
    )
    usage = token_usage_from_ai_message(message)
    assert (usage.output_tokens, usage.reasoning_tokens) == (4_096, 4_000)
    plain = AIMessage(content="hi", usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7})
    assert token_usage_from_ai_message(plain).reasoning_tokens == 0


def test_rt2_openai_chat_and_responses_keep_the_reasoning_share() -> None:
    transport = OpenAITransport(api_key="sk-test")
    chat = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=300,
            prompt_tokens_details=None,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=256),
        )
    )
    assert transport._usage_from(chat).reasoning_tokens == 256  # pyright: ignore[reportPrivateUsage]
    response = SimpleNamespace(usage=SimpleNamespace(output_tokens_details=SimpleNamespace(reasoning_tokens=7)))
    assert responses_reasoning_tokens(response) == 7
    assert responses_reasoning_tokens(SimpleNamespace(usage=None)) == 0


def test_rt3_gemini_keeps_its_thoughts_count() -> None:
    transport = GeminiTransport(api_key="k", model="gemini-3-flash-preview")
    response = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=10, candidates_token_count=20, thoughts_token_count=30, cached_content_token_count=0
        )
    )
    usage = transport._record_usage_from(response)  # pyright: ignore[reportPrivateUsage]
    assert (usage.output_tokens, usage.reasoning_tokens) == (50, 30)


def test_rt4_a_usage_without_it_reads_zero() -> None:
    assert TokenUsage(input_tokens=1, output_tokens=2).reasoning_tokens == 0
