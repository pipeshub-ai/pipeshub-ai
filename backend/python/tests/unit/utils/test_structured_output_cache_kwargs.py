"""A prompt cache's parameters and markers reach every structured call, and a
provider that refuses some gets the request again without the ones it named."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from app.agent_loop_lib.transport.prompt_cache import (
    REJECTED_CACHE_PARAMS,
    OpenAICachePlan,
)
from app.agents.agent_loop.prompt_cache import (
    AnthropicCacheStrategy,
    BedrockCacheStrategy,
    CacheStrategy,
    OpenAICacheStrategy,
)
from app.utils import streaming
from app.utils.streaming import invoke_with_structured_output_and_reflection

if TYPE_CHECKING:
    from collections.abc import Iterator

    from langchain_core.messages import BaseMessage

KWARGS = {"prompt_cache_key": "pipeshub:abc:document-extraction:3"}
BOTH = {**KWARGS, "prompt_cache_retention": "24h"}
OPENAI = OpenAICacheStrategy(OpenAICachePlan(KWARGS))
REFUSED_KEY = "Error code: 400 - Unrecognized request argument supplied: prompt_cache_key"
REFUSED_RETENTION = "Error code: 400 - Unrecognized request argument supplied: prompt_cache_retention"


class _Answer(BaseModel):
    value: int


class _BadRequest(Exception):
    status_code = 400


class _Llm:
    def __init__(self, replies: list[object]) -> None:
        self.replies = list(replies)
        self.seen: list[dict[str, object]] = []
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
    return ",".join(name for name in ("cache_control", "cachePoint", "prompt_cache_breakpoint") if name in text)


@pytest.fixture(autouse=True)
def no_structured_output() -> Iterator[None]:
    REJECTED_CACHE_PARAMS.clear()
    with patch.object(streaming, "_apply_structured_output", side_effect=lambda llm, schema: llm):
        yield
    REJECTED_CACHE_PARAMS.clear()


async def _invoke(
    llm: _Llm, cache: CacheStrategy = OPENAI, messages: list[BaseMessage] | None = None, **kwargs: object,
) -> _Answer | None:
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
    forget.assert_called_once_with(llm, params=frozenset({"prompt_cache_key"}))


async def test_any_other_error_is_not_resent() -> None:
    llm = _Llm([_BadRequest("Error code: 400 - context length exceeded")])
    assert await _invoke(llm) is None
    assert llm.seen == [KWARGS]


async def test_a_400_naming_another_parameter_is_not_a_cache_refusal() -> None:
    llm = _Llm([_BadRequest("Error code: 400 - Unrecognized request argument supplied: temperature")])
    recorded: list[frozenset[str]] = []
    with patch.object(streaming, "forget_cache_params") as forget:
        assert await _invoke(llm, OpenAICacheStrategy(OpenAICachePlan(BOTH)), on_cache_rejected=recorded.append) is None
    assert llm.seen == [BOTH]
    assert recorded == []
    forget.assert_not_called()


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
    recorded: list[frozenset[str]] = []
    with patch.object(streaming, "forget_cache_params") as forget:
        await _invoke(llm, on_cache_rejected=recorded.append)
    assert recorded == [frozenset({"prompt_cache_key"})]
    forget.assert_not_called()


async def test_a_refused_retention_keeps_the_key() -> None:
    """Only what the 400 names is dropped, as LangChainTransport narrows its
    strategy: the key still routes the resend and every later call."""
    llm = _Llm([_BadRequest(REFUSED_RETENTION), "not json", '{"value": 2}'])
    recorded: list[frozenset[str]] = []
    assert await _invoke(llm, OpenAICacheStrategy(OpenAICachePlan(BOTH)), on_cache_rejected=recorded.append) == _Answer(
        value=2,
    )
    assert llm.seen == [BOTH, KWARGS, KWARGS]
    assert recorded == [frozenset({"prompt_cache_retention"})]


async def test_each_refusal_drops_what_it_names() -> None:
    llm = _Llm([_BadRequest(REFUSED_RETENTION), _BadRequest(REFUSED_KEY), '{"value": 6}'])
    recorded: list[frozenset[str]] = []
    assert await _invoke(llm, OpenAICacheStrategy(OpenAICachePlan(BOTH)), on_cache_rejected=recorded.append) == _Answer(
        value=6,
    )
    assert llm.seen == [BOTH, KWARGS, {}]
    assert recorded == [frozenset({"prompt_cache_retention"}), frozenset({"prompt_cache_key"})]


async def test_a_refused_breakpoint_is_stripped_and_the_key_kept() -> None:
    cache = OpenAICacheStrategy(OpenAICachePlan(KWARGS, breakpoint=True))
    messages = [*cache.system("Classify the document.", ""), HumanMessage(content="q")]
    llm = _Llm([_BadRequest("Error code: 400 - prompt_cache_breakpoint is not supported on this model"), '{"value": 4}'])
    recorded: list[frozenset[str]] = []
    assert await _invoke(llm, cache, messages, on_cache_rejected=recorded.append) == _Answer(value=4)
    assert [_markers(request) for request in llm.requests] == ["prompt_cache_breakpoint", ""]
    assert llm.seen == [KWARGS, KWARGS]
    assert recorded == [frozenset({"prompt_cache_breakpoint"})]


@pytest.mark.parametrize(
    ("cache", "refusal"),
    [
        (BedrockCacheStrategy(cache_tools=False), "ValidationException: 400 extraneous key [cachePoint] is not permitted"),
        (AnthropicCacheStrategy({"type": "ephemeral"}, top_level=False), "Error code: 400 - cache_control: Extra inputs are not permitted"),
        (AnthropicCacheStrategy({"type": "ephemeral"}, top_level=True), "Error code: 400 - cache_control: Extra inputs are not permitted"),
    ],
    ids=["bedrock", "anthropic-in-message", "anthropic-top-level"],
)
async def test_a_refused_marker_in_the_messages_is_stripped_too(cache: CacheStrategy, refusal: str) -> None:
    """Bedrock and Anthropic put their markers on the messages, not only in the
    kwargs: the resend and every later call carry none of them."""
    messages = [*cache.system("Classify the document.", ""), HumanMessage(content="q")]
    llm = _Llm([_BadRequest(refusal), "not json", '{"value": 4}'])
    recorded: list[frozenset[str]] = []
    assert await _invoke(llm, cache, messages, on_cache_rejected=recorded.append) == _Answer(value=4)
    assert _markers(llm.requests[0]) or llm.seen[0]
    assert [_markers(request) for request in llm.requests[1:]] == ["", ""]
    assert llm.seen[1:] == [{}, {}]
    assert recorded == [cache.sent_params]
