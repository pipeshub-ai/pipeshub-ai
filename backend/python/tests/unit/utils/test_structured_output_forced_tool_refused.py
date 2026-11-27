"""A Bedrock model that refuses forced tool use still gives structured answers.

langchain-aws forces the tool for every model id with "claude" in it, and newer Claude models
answer that with a 400. These tests run the real ChatBedrockConverse against a stub Bedrock
client that answers as Bedrock does.
"""

from __future__ import annotations

from typing import Any

import pytest
from botocore.exceptions import ClientError
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from app.services.llm_gateway import wire
from app.utils import streaming
from app.utils.streaming import (
    _apply_structured_output,
    invoke_with_structured_output_and_reflection,
)

REFUSAL = 'The model returned the following errors: tool_choice: type "tool" and "any" are not supported for this model.'


class Answer(BaseModel):
    answer: str


def _refused(message: str = REFUSAL, code: str = "ValidationException") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "Converse")


class StubBedrock:
    """Answers Converse with a tool call, or refuses forced tool use when ``refuse_forced`` is set."""

    def __init__(
        self, *, refuse_forced: bool = True, error: ClientError | None = None, text_only: bool = False,
        text: str = "Finance, invoices", on_wire: bool = False,
    ) -> None:
        self.refuse_forced = refuse_forced
        self.error = error
        self.text_only = text_only
        self.text = text
        self.on_wire = on_wire
        self.tool_choices: list[Any] = []

    def converse(self, **params: Any) -> dict:  # noqa: ANN401
        choice = (params.get("toolConfig") or {}).get("toolChoice")
        self.tool_choices.append(choice)
        if self.on_wire:  # what the gateway's botocore hooks record for each request
            wire.note_request_sent()
        if self.error is not None:
            raise self.error
        if self.refuse_forced and choice and choice.keys() & {"tool", "any"}:
            if self.on_wire:
                wire.note_response(400)
            raise _refused()
        if self.on_wire:
            wire.note_response(200)
        if self.text_only:
            return {
                "output": {"message": {"role": "assistant", "content": [{"text": self.text}]}},
                "stopReason": "end_turn",
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 1},
            }
        return {
            "output": {"message": {"role": "assistant", "content": [
                {"toolUse": {"toolUseId": "t1", "name": "Answer", "input": {"answer": "classified"}}},
            ]}},
            "stopReason": "tool_use",
            "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
            "metrics": {"latencyMs": 1},
        }


def _bedrock(stub: StubBedrock, model: str = "us.anthropic.claude-fable-5-2") -> ChatBedrockConverse:
    return ChatBedrockConverse(
        model=model, region_name="us-east-1", client=stub, aws_access_key_id="x", aws_secret_access_key="y",
    )


@pytest.fixture(autouse=True)
def _forget_refusing_models() -> None:
    streaming._models_refusing_forced_tool.clear()


@pytest.mark.asyncio
async def test_a_model_refusing_forced_tool_use_is_asked_without_forcing_it() -> None:
    stub = StubBedrock()

    reply = await _apply_structured_output(_bedrock(stub), schema=Answer).ainvoke([HumanMessage("classify")])

    assert reply == Answer(answer="classified")
    assert stub.tool_choices == [{"tool": {"name": "Answer"}}, None]


@pytest.mark.asyncio
async def test_classification_gets_its_answer_from_such_a_model() -> None:
    stub = StubBedrock()

    result = await invoke_with_structured_output_and_reflection(
        _bedrock(stub), [HumanMessage("classify")], Answer, raise_call_errors=True,
    )

    assert result == Answer(answer="classified")


@pytest.mark.asyncio
async def test_an_unforced_reply_without_the_tool_ends_in_none_not_an_error() -> None:
    """Then classification takes its plain-summary fallback instead of retrying the record."""
    stub = StubBedrock(text_only=True)

    result = await invoke_with_structured_output_and_reflection(
        _bedrock(stub), [HumanMessage("classify")], Answer, raise_call_errors=True,
    )

    assert result is None
    assert stub.tool_choices == [{"tool": {"name": "Answer"}}, None, None, None]


@pytest.mark.asyncio
async def test_the_refusal_is_remembered_so_later_calls_are_not_forced() -> None:
    stub = StubBedrock()
    await _apply_structured_output(_bedrock(stub), schema=Answer).ainvoke([HumanMessage("first")])

    await _apply_structured_output(_bedrock(stub), schema=Answer).ainvoke([HumanMessage("second")])

    assert stub.tool_choices == [{"tool": {"name": "Answer"}}, None, None]


@pytest.mark.asyncio
async def test_the_raw_reply_is_kept_when_asked_for() -> None:
    stub = StubBedrock()

    reply = await _apply_structured_output(_bedrock(stub), schema=Answer, include_raw=True).ainvoke(
        [HumanMessage("classify")],
    )

    assert reply["parsed"] == Answer(answer="classified")
    assert reply["raw"].tool_calls


def test_the_sync_call_is_asked_without_forcing_too() -> None:
    stub = StubBedrock()

    assert _apply_structured_output(_bedrock(stub), schema=Answer).invoke([HumanMessage("x")]) == Answer(
        answer="classified",
    )


@pytest.mark.asyncio
async def test_a_model_that_accepts_forced_tool_use_is_still_forced() -> None:
    stub = StubBedrock(refuse_forced=False)

    reply = await _apply_structured_output(_bedrock(stub), schema=Answer).ainvoke([HumanMessage("classify")])

    assert reply == Answer(answer="classified")
    assert stub.tool_choices == [{"tool": {"name": "Answer"}}]


@pytest.mark.parametrize("error", [
    _refused("Malformed input request: messages.0 is empty"),
    _refused("Too many requests", code="ThrottlingException"),
    _refused(REFUSAL, code="AccessDeniedException"),
], ids=["other-validation", "throttled", "other-code"])
@pytest.mark.asyncio
async def test_other_bedrock_errors_are_raised_as_before(error: ClientError) -> None:
    stub = StubBedrock(error=error)

    with pytest.raises(ClientError) as raised:
        await _apply_structured_output(_bedrock(stub), schema=Answer).ainvoke([HumanMessage("classify")])

    assert raised.value is error
    assert len(stub.tool_choices) == 1
    assert not streaming._models_refusing_forced_tool


@pytest.mark.parametrize("model", ["us.anthropic.claude-sonnet-4-6", "us.anthropic.claude-opus-4-7-v1:0", "us.anthropic.claude-fable-5-2"])
@pytest.mark.asyncio
async def test_an_answer_sent_as_text_is_read_whatever_the_model_id(model: str) -> None:
    """langchain-aws's own unforced path raises for Claude 4 ids and drops a JSON answer sent as text."""
    stub = StubBedrock(text_only=True, text='{"answer": "from text"}')

    result = await invoke_with_structured_output_and_reflection(
        _bedrock(stub, model), [HumanMessage("classify")], Answer, raise_call_errors=True,
    )

    assert result == Answer(answer="from text")
    assert stub.tool_choices == [{"tool": {"name": "Answer"}}, None]


@pytest.mark.parametrize("model", ["us.anthropic.claude-sonnet-4-6", "us.anthropic.claude-opus-4-7-v1:0"])
@pytest.mark.asyncio
async def test_a_claude_4_reply_without_the_tool_is_asked_again_not_raised(model: str) -> None:
    stub = StubBedrock(text_only=True)

    result = await invoke_with_structured_output_and_reflection(
        _bedrock(stub, model), [HumanMessage("classify")], Answer, raise_call_errors=True,
    )

    assert result is None
    assert stub.tool_choices == [{"tool": {"name": "Answer"}}, None, None, None]


@pytest.mark.asyncio
async def test_the_raw_reply_without_the_tool_is_kept_for_the_caller() -> None:
    stub = StubBedrock(text_only=True, text='{"answer": "from text"}')

    reply = await _apply_structured_output(_bedrock(stub), schema=Answer, include_raw=True).ainvoke([HumanMessage("x")])

    assert reply["parsed"] is None and reply["parsing_error"] is None
    assert reply["raw"].content == '{"answer": "from text"}'


@pytest.mark.asyncio
async def test_the_refused_request_is_not_counted_as_a_failed_send() -> None:
    stub = StubBedrock(on_wire=True)

    with wire.wire_scope() as seen:
        await _apply_structured_output(_bedrock(stub), schema=Answer).ainvoke([HumanMessage("classify")])

    assert (seen.requests, seen.responses, seen.statuses, seen.status) == (1, 1, [200], 200)
    assert seen.judge(raised=None) == wire.OK


@pytest.mark.asyncio
async def test_the_fallback_is_only_built_after_a_refusal() -> None:
    structured = _apply_structured_output(_bedrock(StubBedrock(refuse_forced=False)), schema=Answer)

    await structured.ainvoke([HumanMessage("classify")])

    assert structured._unforced is None


@pytest.mark.parametrize("message", [
    "Thinking may not be enabled when tool_choice forces tool use.",
    "tool_choice.name: tool Foo not found. cache_control is not supported here.",
])
def test_only_the_forced_tool_refusal_is_recognised(message: str) -> None:
    assert not streaming._refuses_forced_tool(_refused(message))
    assert streaming._refuses_forced_tool(_refused(REFUSAL))


def test_a_refusal_is_remembered_per_thinking_setting() -> None:
    plain = _bedrock(StubBedrock())
    thinking = plain.model_copy(update={"additional_model_request_fields": {"thinking": {"type": "adaptive"}}})

    assert streaming._forced_tool_key(plain) == ("us.anthropic.claude-fable-5-2", "")
    assert streaming._forced_tool_key(thinking) == ("us.anthropic.claude-fable-5-2", "adaptive")
