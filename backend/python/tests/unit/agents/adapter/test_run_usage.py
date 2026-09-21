"""`run_usage` frame payload, the auto-compact usage callback, and the
benchmark env knobs on the agent factory."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.agent_loop_lib.core.responses import RunUsage, TokenUsage
from app.agent_loop_lib.core.types import UserMessage
from app.agent_loop_lib.hooks.middleware.builtin.auto_compact import make_llm_summarizer
from app.agents.agent_loop.protocol.formatter import AGUI_FORMATTER
from app.agents.agent_loop.protocol.run_usage import RunUsagePayload
from tests.unit.agents.adapter.conftest import make_context


def _usage(requests: int, inp: int, out: int, cache_read: int = 0) -> RunUsage:
    usage = RunUsage()
    for _ in range(requests):
        usage.add(TokenUsage(input_tokens=inp, output_tokens=out, cache_read_tokens=cache_read))
    return usage


def test_payload_sums_loop_and_auxiliary_usage() -> None:
    payload = RunUsagePayload.from_usage(_usage(3, 100, 10, 40), _usage(1, 500, 50), model="m")
    assert payload.llmCalls == 4
    assert payload.inputTokens == 800
    assert payload.outputTokens == 80
    assert payload.cacheReadTokens == 120
    assert payload.auxiliaryLlmCalls == 1
    assert payload.calls == [[100, 10, 40]] * 3 + [[500, 50, 0]]


def test_agui_frame_is_custom_run_usage() -> None:
    context = make_context(protocol="agui", run_id="run-1")
    [frame] = AGUI_FORMATTER.run_usage(context, payload=RunUsagePayload(llmCalls=2, inputTokens=5))
    assert frame["data"]["type"] == "CUSTOM"
    assert frame["data"]["name"] == "run_usage"
    assert frame["data"]["value"]["inputTokens"] == 5


@pytest.mark.asyncio
async def test_summarizer_reports_usage() -> None:
    seen: list[TokenUsage] = []

    class _Transport:
        async def complete(self, **_: Any) -> Any:  # noqa: ANN401
            return SimpleNamespace(
                message=SimpleNamespace(text="summary"),
                usage=TokenUsage(input_tokens=7, output_tokens=3),
            )

    registry = SimpleNamespace(resolve=lambda _provider: _Transport())
    summarize = make_llm_summarizer(registry, "langchain", "m", on_usage=seen.append)
    assert await summarize([UserMessage(content="hi")]) == "summary"
    assert seen == [TokenUsage(input_tokens=7, output_tokens=3)]
