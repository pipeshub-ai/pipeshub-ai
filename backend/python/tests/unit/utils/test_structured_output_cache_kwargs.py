"""Prompt-cache request parameters reach every structured call, and a provider
that refuses one gets the request again without them."""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from app.agent_loop_lib.transport.prompt_cache import REJECTED_CACHE_PARAMS
from app.utils import streaming
from app.utils.streaming import invoke_with_structured_output_and_reflection

if TYPE_CHECKING:
    from collections.abc import Iterator

KWARGS = {"prompt_cache_key": "pipeshub:abc:document-extraction:3"}


class _Answer(BaseModel):
    value: int


class _BadRequest(Exception):
    status_code = 400


class _Llm:
    def __init__(self, replies: list[object]) -> None:
        self.replies = list(replies)
        self.seen: list[dict] = []

    async def ainvoke(self, messages: list, **kwargs: object) -> AIMessage:
        self.seen.append(dict(kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return AIMessage(content=reply)


@pytest.fixture(autouse=True)
def no_structured_output() -> Iterator[None]:
    REJECTED_CACHE_PARAMS.clear()
    with patch.object(streaming, "_apply_structured_output", side_effect=lambda llm, schema: llm):
        yield
    REJECTED_CACHE_PARAMS.clear()


async def test_the_kwargs_reach_the_first_call_and_every_reflection() -> None:
    llm = _Llm(["not json", '{"value": 3}'])
    result = await invoke_with_structured_output_and_reflection(
        llm, [HumanMessage(content="q")], _Answer, call_kwargs=KWARGS,
    )
    assert result == _Answer(value=3)
    assert llm.seen == [KWARGS, KWARGS]


async def test_a_refused_cache_parameter_is_dropped_and_the_call_resent() -> None:
    refused = _BadRequest("Error code: 400 - Unrecognized request argument supplied: prompt_cache_key")
    llm = _Llm([refused, '{"value": 7}'])
    with patch.object(streaming, "forget_cache_params") as forget:
        result = await invoke_with_structured_output_and_reflection(
            llm, [HumanMessage(content="q")], _Answer, call_kwargs=KWARGS,
        )
    assert result == _Answer(value=7)
    assert llm.seen == [KWARGS, {}]
    forget.assert_called_once_with(llm)


async def test_any_other_error_is_not_resent() -> None:
    llm = _Llm([_BadRequest("Error code: 400 - context length exceeded")])
    result = await invoke_with_structured_output_and_reflection(
        llm, [HumanMessage(content="q")], _Answer, call_kwargs=KWARGS,
    )
    assert result is None
    assert llm.seen == [KWARGS]


async def test_no_kwargs_by_default() -> None:
    llm = _Llm(['{"value": 1}'])
    await invoke_with_structured_output_and_reflection(llm, [HumanMessage(content="q")], _Answer)
    assert llm.seen == [{}]


async def test_a_reflection_call_refused_is_resent_without_them() -> None:
    refused = _BadRequest("Error code: 400 - Unrecognized request argument supplied: prompt_cache_key")
    llm = _Llm(["not json", refused, '{"value": 3}'])
    with patch.object(streaming, "forget_cache_params"):
        result = await invoke_with_structured_output_and_reflection(
            llm, [HumanMessage(content="q")], _Answer, call_kwargs=KWARGS,
        )
    assert result == _Answer(value=3)
    assert llm.seen == [KWARGS, KWARGS, {}]


async def test_calls_after_a_refusal_carry_no_cache_parameters() -> None:
    refused = _BadRequest("Error code: 400 - Unrecognized request argument supplied: prompt_cache_key")
    llm = _Llm([refused, "not json", '{"value": 5}'])
    with patch.object(streaming, "forget_cache_params"):
        result = await invoke_with_structured_output_and_reflection(
            llm, [HumanMessage(content="q")], _Answer, call_kwargs=KWARGS,
        )
    assert result == _Answer(value=5)
    assert llm.seen == [KWARGS, {}, {}]


async def test_the_refusal_is_recorded_through_the_callers_callback() -> None:
    refused = _BadRequest("Error code: 400 - Unrecognized request argument supplied: prompt_cache_key")
    llm = _Llm([refused, '{"value": 7}'])
    recorded: list[str] = []
    with patch.object(streaming, "forget_cache_params") as forget:
        await invoke_with_structured_output_and_reflection(
            llm, [HumanMessage(content="q")], _Answer, call_kwargs=KWARGS,
            on_cache_rejected=lambda: recorded.append("caller"),
        )
    assert recorded == ["caller"]
    forget.assert_not_called()

