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

    def __init__(self, *, refuse_forced: bool = True, error: ClientError | None = None, text_only: bool = False) -> None:
        self.refuse_forced = refuse_forced
        self.error = error
        self.text_only = text_only
        self.tool_choices: list[Any] = []

    def converse(self, **params: Any) -> dict:  # noqa: ANN401
        choice = (params.get("toolConfig") or {}).get("toolChoice")
        self.tool_choices.append(choice)
        if self.error is not None:
            raise self.error
        if self.refuse_forced and choice and choice.keys() & {"tool", "any"}:
            raise _refused()
        if self.text_only:
            return {
                "output": {"message": {"role": "assistant", "content": [{"text": "Finance, invoices"}]}},
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
    getattr(streaming, "_models_refusing_forced_tool", set()).clear()


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
    assert not getattr(streaming, "_models_refusing_forced_tool", set())
