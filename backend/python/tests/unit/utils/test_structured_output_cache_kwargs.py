"""A prompt cache's parameters and markers reach every structured call, and a
provider that refuses one gets the request again without any of them."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from app.agent_loop_lib.transport.prompt_cache import REJECTED_CACHE_PARAMS
from app.agents.agent_loop.prompt_cache import (
    AnthropicCacheStrategy,
    BedrockCacheStrategy,
    OpenAICacheStrategy,
)
from app.utils import streaming
from app.utils.streaming import invoke_with_structured_output_and_reflection

if TYPE_CHECKING:
    from collections.abc import Iterator

    from langchain_core.messages import BaseMessage

KWARGS = {"prompt_cache_key": "pipeshub:abc:document-extraction:3"}
OPENAI = OpenAICacheStrategy(KWARGS)
REFUSED_KEY = "Error code: 400 - Unrecognized request argument supplied: prompt_cache_key"


class _Answer(BaseModel):
    value: int


class _BadRequest(Exception):
    status_code = 400


class _Llm:
    def __init__(self, replies: list[object]) -> None:
        self.replies = list(replies)
        self.seen: list[dict] = []
        self.requests: list[list[BaseMessage]] = []

    async def ainvoke(self, messages: list, **kwargs: object) -> AIMessage:
        self.seen.append(dict(kwargs))
        self.requests.append(list(messages))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return AIMessage(content=reply)


def _markers(messages: list[BaseMessage]) -> str:
    text = json.dumps([message.content for message in messages])
    return ",".join(name for name in ("cache_control", "cachePoint") if name in text)


@pytest.fixture(autouse=True)
def no_structured_output() -> Iterator[None]:
    REJECTED_CACHE_PARAMS.clear()
    with patch.object(streaming, "_apply_structured_output", side_effect=lambda llm, schema: llm):
        yield
    REJECTED_CACHE_PARAMS.clear()


async def _invoke(llm: _Llm, cache: object = OPENAI, messages: list | None = None, **kwargs: object) -> _Answer | None:
    return await invoke_with_structured_output_and_reflection(
        llm, messages or [HumanMessage(content="q")], _Answer, prompt_cache=cache, **kwargs,
    )


async def test_the_kwargs_reach_the_first_call_and_every_reflection() -> None:
    llm = _Llm(["not json", '{"value": 3}'])
    assert await _invoke(llm) == _Answer(value=3)
    assert llm.seen == [KWARGS, KWARGS]


async def test_a_refused_cache_parameter_is_dropped_and_the_call_resent() -> None:
    llm = _Llm([_BadRequest(REFUSED_KEY), '{"value": 7}'])
    with patch.object(streaming, "forget_cache_params") as forget:
        assert await _invoke(llm) == _Answer(value=7)
    assert llm.seen == [KWARGS, {}]
    forget.assert_called_once_with(llm)


async def test_any_other_error_is_not_resent() -> None:
    llm = _Llm([_BadRequest("Error code: 400 - context length exceeded")])
    assert await _invoke(llm) is None
    assert llm.seen == [KWARGS]


async def test_no_kwargs_by_default() -> None:
    llm = _Llm(['{"value": 1}'])
    await invoke_with_structured_output_and_reflection(llm, [HumanMessage(content="q")], _Answer)
    assert llm.seen == [{}]


async def test_a_reflection_call_refused_is_resent_without_them() -> None:
    llm = _Llm(["not json", _BadRequest(REFUSED_KEY), '{"value": 3}'])
    with patch.object(streaming, "forget_cache_params"):
        assert await _invoke(llm) == _Answer(value=3)
    assert llm.seen == [KWARGS, KWARGS, {}]


async def test_calls_after_a_refusal_carry_no_cache_parameters() -> None:
    llm = _Llm([_BadRequest(REFUSED_KEY), "not json", '{"value": 5}'])
    with patch.object(streaming, "forget_cache_params"):
        assert await _invoke(llm) == _Answer(value=5)
    assert llm.seen == [KWARGS, {}, {}]


async def test_the_refusal_is_recorded_through_the_callers_callback() -> None:
    llm = _Llm([_BadRequest(REFUSED_KEY), '{"value": 7}'])
    recorded: list[str] = []
    with patch.object(streaming, "forget_cache_params") as forget:
        await _invoke(llm, on_cache_rejected=lambda: recorded.append("caller"))
    assert recorded == ["caller"]
    forget.assert_not_called()


async def test_a_refusal_turns_the_whole_cache_off() -> None:
    """A refused retention also drops the key, as LangChainTransport falls back
    to no strategy at all."""
    both = {**KWARGS, "prompt_cache_retention": "24h"}
    refused = _BadRequest("Error code: 400 - Unrecognized request argument supplied: prompt_cache_retention")
    llm = _Llm([refused, '{"value": 2}'])
    with patch.object(streaming, "forget_cache_params"):
        assert await _invoke(llm, OpenAICacheStrategy(both)) == _Answer(value=2)
    assert llm.seen == [both, {}]


@pytest.mark.parametrize(
    ("cache", "refusal"),
    [
        (BedrockCacheStrategy(cache_tools=False), "ValidationException: 400 extraneous key [cachePoint] is not permitted"),
        (AnthropicCacheStrategy({"type": "ephemeral"}, top_level=False), "Error code: 400 - cache_control: Extra inputs are not permitted"),
        (AnthropicCacheStrategy({"type": "ephemeral"}, top_level=True), "Error code: 400 - cache_control: Extra inputs are not permitted"),
    ],
    ids=["bedrock", "anthropic-in-message", "anthropic-top-level"],
)
async def test_a_refused_marker_in_the_messages_is_stripped_too(cache: object, refusal: str) -> None:
    """Bedrock and Anthropic put their markers on the messages, not only in the
    kwargs: the resend and every later call carry none of them."""
    messages = [cache.system("Classify the document.", ""), HumanMessage(content="q")]
    llm = _Llm([_BadRequest(refusal), "not json", '{"value": 4}'])
    recorded: list[str] = []
    assert await _invoke(llm, cache, messages, on_cache_rejected=lambda: recorded.append("x")) == _Answer(value=4)
    assert _markers(llm.requests[0]) or llm.seen[0]
    assert [_markers(request) for request in llm.requests[1:]] == ["", ""]
    assert llm.seen[1:] == [{}, {}]
    assert recorded == ["x"]
