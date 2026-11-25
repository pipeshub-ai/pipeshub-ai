"""P4.17 WF-1..WF-5: each direct transport puts the call's output limit in its provider's own field, keeps a
smaller configured cap, keeps a thinking budget inside it, and sends nothing new without a limit."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from harness_core.kernel.core.messages import UserMessage
from harness_core.models.sigv4 import AwsCredentials
from harness_core.models.transports.anthropic import AnthropicTransport
from harness_core.models.transports.bedrock_converse import BedrockConverseTransport
from harness_core.models.transports.gemini import GeminiTransport
from harness_core.models.transports.ollama import OllamaTransport
from harness_core.models.transports.openai import OpenAITransport, RequestDefaults

_MESSAGES = [UserMessage(content="write the report")]


class _Captured(Exception):
    pass


def _capture(seen: list[dict[str, Any]]) -> Any:  # noqa: ANN401
    async def create(**kwargs: Any) -> Any:  # noqa: ANN401
        seen.append(kwargs)
        raise _Captured

    return create


def _openai(seen: list[dict[str, Any]], *, responses: bool, max_tokens: int | None = None) -> OpenAITransport:
    transport = OpenAITransport(
        api_key="sk-test",
        model="gpt-6-luna",
        defaults=RequestDefaults(
            model="gpt-6-luna",
            use_responses_api=responses,
            reasoning={"effort": "high"} if responses else None,
            max_tokens=max_tokens,
        ),
    )
    create = _capture(seen)
    transport._client = SimpleNamespace(  # pyright: ignore[reportAttributeAccessIssue]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        responses=SimpleNamespace(create=create),
    )
    return transport


async def test_wf1_chat_completions_carries_max_completion_tokens() -> None:
    seen: list[dict[str, Any]] = []
    transport = _openai(seen, responses=False)
    assert transport.accepts_output_limit
    with pytest.raises(Exception):  # noqa: B017 - the stub stops the call once it is captured
        await transport.complete(_MESSAGES, max_output_tokens=32_768)
    with pytest.raises(Exception):  # noqa: B017
        async for _ in transport.stream(_MESSAGES, max_output_tokens=32_768):
            pass
    with pytest.raises(Exception):  # noqa: B017
        await transport.complete_structured(_MESSAGES, {"type": "object"}, max_output_tokens=8_192)
    assert [s.get("max_completion_tokens") for s in seen] == [32_768, 32_768, 8_192]
    assert all("max_tokens" not in s and "max_output_tokens" not in s for s in seen)


async def test_wf1_no_limit_sends_no_field_and_a_smaller_configured_cap_is_kept() -> None:
    seen: list[dict[str, Any]] = []
    with pytest.raises(Exception):  # noqa: B017
        await _openai(seen, responses=False).complete(_MESSAGES)
    with pytest.raises(Exception):  # noqa: B017
        await _openai(seen, responses=False, max_tokens=4_000).complete(_MESSAGES, max_output_tokens=32_768)
    assert "max_completion_tokens" not in seen[0]
    assert seen[1]["max_completion_tokens"] == 4_000


async def test_wf2_responses_carries_max_output_tokens() -> None:
    seen: list[dict[str, Any]] = []
    transport = _openai(seen, responses=True)

    async def raw_events(kwargs: dict[str, Any]) -> Any:  # noqa: ANN401 - the SDK's streaming entry, stubbed
        seen.append(kwargs)
        raise _Captured
        yield

    transport._raw_response_events = raw_events  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(Exception):  # noqa: B017
        await transport.complete(_MESSAGES, max_output_tokens=32_768)
    with pytest.raises(Exception):  # noqa: B017
        async for _ in transport.stream(_MESSAGES, max_output_tokens=32_768):
            pass
    assert [s.get("max_output_tokens") for s in seen] == [32_768, 32_768]
    assert all("max_completion_tokens" not in s for s in seen)


def _anthropic(seen: list[dict[str, Any]], **kwargs: Any) -> AnthropicTransport:  # noqa: ANN401
    transport = AnthropicTransport(api_key="sk-test", model="claude-x", **kwargs)

    def stream(**body: Any) -> Any:  # noqa: ANN401
        seen.append(body)
        raise _Captured

    transport._client = SimpleNamespace(  # pyright: ignore[reportAttributeAccessIssue]
        messages=SimpleNamespace(create=_capture(seen), stream=stream)
    )
    return transport


async def test_wf3_messages_carries_max_tokens_and_keeps_thinking_inside_it() -> None:
    seen: list[dict[str, Any]] = []
    with pytest.raises(Exception):  # noqa: B017
        await _anthropic(seen, max_tokens=64_000).complete(_MESSAGES, max_output_tokens=32_768)
    with pytest.raises(Exception):  # noqa: B017
        async for _ in _anthropic(seen, max_tokens=16_384).stream(_MESSAGES, max_output_tokens=32_768):
            pass
    with pytest.raises(Exception):  # noqa: B017
        await _anthropic(seen, max_tokens=64_000).complete(_MESSAGES, thinking_budget=60_000, max_output_tokens=32_768)
    with pytest.raises(Exception):  # noqa: B017
        await _anthropic(seen, max_tokens=64_000, thinking={"type": "enabled", "budget_tokens": 50_000}).complete(
            _MESSAGES, max_output_tokens=32_768
        )
    assert seen[0]["max_tokens"] == 32_768
    assert seen[1]["max_tokens"] == 16_384
    assert seen[2]["thinking"] == {"type": "enabled", "budget_tokens": 28_672}
    assert seen[2]["max_tokens"] == 32_768
    assert seen[3]["thinking"]["budget_tokens"] == 28_672 and seen[3]["max_tokens"] == 32_768


async def test_wf3_without_a_limit_thinking_grows_max_tokens_as_before() -> None:
    seen: list[dict[str, Any]] = []
    with pytest.raises(Exception):  # noqa: B017
        await _anthropic(seen, max_tokens=20_000).complete(_MESSAGES, thinking_budget=60_000)
    assert seen[0]["max_tokens"] == 61_024
    assert seen[0]["thinking"]["budget_tokens"] == 60_000


def test_wf4_converse_carries_inference_max_tokens() -> None:
    transport = BedrockConverseTransport(
        region="us-east-1", credentials=AwsCredentials("AKID", "secret"), model="anthropic.claude-x", max_tokens=64_000
    )
    assert transport.accepts_output_limit
    body = transport._body(_MESSAGES, None, None, None, None, 32_768)  # pyright: ignore[reportPrivateUsage]
    thinking = transport._body(_MESSAGES, None, None, None, 60_000, 32_768)  # pyright: ignore[reportPrivateUsage]
    unbounded = transport._body(_MESSAGES, None, None, None, None)  # pyright: ignore[reportPrivateUsage]
    assert body["inferenceConfig"]["maxTokens"] == 32_768
    assert thinking["additionalModelRequestFields"]["thinking"]["budget_tokens"] == 28_672
    assert thinking["inferenceConfig"]["maxTokens"] == 32_768
    assert unbounded["inferenceConfig"]["maxTokens"] == 64_000


def test_wf5_gemini_carries_max_output_tokens() -> None:
    transport = GeminiTransport(api_key="k", model="gemini-3-flash-preview", thinking_budget=60_000)
    assert transport.accepts_output_limit
    config = transport._build_config(None, None, None, limit=32_768)  # pyright: ignore[reportPrivateUsage]
    assert config.max_output_tokens == 32_768
    assert config.thinking_config.thinking_budget == 28_672
    assert transport._build_config(None, None, None).max_output_tokens is None  # pyright: ignore[reportPrivateUsage]


async def test_wf5_ollama_carries_num_predict_and_reports_the_cut() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "partial"}, "done": True, "done_reason": "length",
                  "prompt_eval_count": 3, "eval_count": 32},
        )

    transport = OllamaTransport()
    transport._client = httpx.AsyncClient(base_url="http://ollama", transport=httpx.MockTransport(handler))  # pyright: ignore[reportPrivateUsage]
    assert transport.accepts_output_limit
    response = await transport.complete(_MESSAGES, max_output_tokens=32)
    unbounded = await transport.complete(_MESSAGES)
    assert bodies[0]["options"]["num_predict"] == 32
    assert "num_predict" not in bodies[1]["options"]
    assert response.message.truncated and unbounded.message.truncated
