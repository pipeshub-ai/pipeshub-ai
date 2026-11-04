"""Live check that the direct transports replay one turn's parallel tool calls and results in a
shape the provider accepts and binds correctly (CE-20).

Opt-in and paid: runs only with ``PIPESHUB_LIVE_PARALLEL_TOOLS=1`` and the provider's
credentials in the environment, otherwise every test skips.

- Gemini: ``TEST_GEMINI_API_KEY`` or ``GEMINI_API_KEY``; model from
  ``PIPESHUB_LIVE_GEMINI_MODEL`` (default ``gemini-3-flash-preview``).
- Anthropic: ``TEST_ANTHROPIC_API_KEY`` or ``ANTHROPIC_API_KEY``; model from
  ``PIPESHUB_LIVE_ANTHROPIC_MODEL`` (default ``claude-haiku-5-5``).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.core.messages import ToolMessage, UserMessage
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agent_loop_lib.transport.anthropic import AnthropicTransport
from app.agent_loop_lib.transport.gemini import GeminiTransport

if TYPE_CHECKING:
    from app.agent_loop_lib.transport.base import LLMTransport

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("PIPESHUB_LIVE_PARALLEL_TOOLS") != "1", reason="live, paid: set PIPESHUB_LIVE_PARALLEL_TOOLS=1",
    ),
]

TOOLS = [ToolSchema(name="get_weather", description="Current weather for one city.", input_schema={
    "type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"],
})]
SYSTEM = "You are terse. When asked for several cities, call get_weather for all of them in one turn."
QUESTION = UserMessage(content="Weather in Paris, London and Tokyo? Call get_weather for all three at once.")
TEMPERATURES = {"paris": 11, "london": 23, "tokyo": 37}


def _env(*names: str) -> str:
    return next((os.environ[name] for name in names if os.environ.get(name)), "")


async def _answer_after_parallel_results(transport: LLMTransport) -> tuple[str, set[str]]:
    """The model's answer after its parallel calls' results, and the known cities it called."""
    first = await transport.complete([QUESTION], tools=TOOLS, system=SYSTEM)
    calls = first.message.tool_calls or []
    called = {str(c.arguments.get("city", "")).lower() for c in calls} & TEMPERATURES.keys()
    if len(calls) < 2 or len(called) < 2:
        pytest.skip("the model made no parallel calls for two known cities this time")
    results = [
        ToolMessage(
            content=f"{TEMPERATURES.get(str(c.arguments.get('city', '')).lower(), 0)}C",
            tool_call_id=c.id,
            step_footer="\n\n[loop: step 2/20]",
        )
        for c in calls
    ]
    second = await transport.complete([QUESTION, first.message, *results], tools=TOOLS, system=SYSTEM)
    return second.message.text, called


async def test_gemini_binds_each_parallel_result_to_its_call() -> None:
    key = _env("TEST_GEMINI_API_KEY", "GEMINI_API_KEY")
    if not key:
        pytest.skip("no Gemini key")
    model = os.getenv("PIPESHUB_LIVE_GEMINI_MODEL", "gemini-3-flash-preview")

    answer, called = await _answer_after_parallel_results(GeminiTransport(api_key=key, model=model))

    for city in called:
        assert str(TEMPERATURES[city]) in answer, city


async def test_anthropic_binds_each_parallel_result_to_its_call() -> None:
    key = _env("TEST_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("no Anthropic key")
    model = os.getenv("PIPESHUB_LIVE_ANTHROPIC_MODEL", "claude-haiku-5-5")

    answer, called = await _answer_after_parallel_results(AnthropicTransport(api_key=key, model=model, max_tokens=512))

    for city in called:
        assert str(TEMPERATURES[city]) in answer, city
