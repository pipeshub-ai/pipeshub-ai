"""Unit tests for the Responses API helpers (`transport/openai_responses.py`).

These cover the parser the live Azure path actually runs: `azure_direct` against
a reasoning model sends `/v1/responses`, so `parse_responses_output` -- not the
Chat Completions parser -- is what turns a real answer into an AssistantMessage.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent_loop_lib.core.responses import StopReason
from app.agent_loop_lib.transport.openai_responses import (
    RawEvent,
    format_responses_input,
    format_responses_tools,
    normalise_tool_call,
    parse_responses_output,
    responses_usage_fields,
    stop_reason_from_responses,
)
from app.agents.agent_loop.converters import MALFORMED_TOOL_CALL_ARGS_KEY


def _function_call(name="search", args='{"q": "x"}', call_id="call_1"):
    return SimpleNamespace(type="function_call", name=name, arguments=args, call_id=call_id, id="item_1")


def _text_item(text="hello"):
    return SimpleNamespace(
        type="message", content=[SimpleNamespace(type="output_text", text=text)]
    )


class TestParseResponsesOutput:
    def test_text_and_tool_call_together(self) -> None:
        response = SimpleNamespace(output=[_text_item("hi"), _function_call()])
        msg = parse_responses_output(response)
        assert msg.text == "hi"
        assert msg.tool_calls[0].name == "search"
        assert msg.tool_calls[0].arguments == {"q": "x"}

    def test_call_id_is_preferred_over_item_id(self) -> None:
        """`function_call_output` must echo `call_id`; sending the item id back
        makes the provider reject the next turn."""
        msg = parse_responses_output(SimpleNamespace(output=[_function_call()]))
        assert msg.tool_calls[0].id == "call_1"

    def test_malformed_arguments_carry_the_correction_sentinels(self) -> None:
        """The regression this file exists for: this parser had its own silent
        `{}` fallback, so the repair applied everywhere else never reached the
        endpoint the live deployment actually uses."""
        msg = parse_responses_output(
            SimpleNamespace(output=[_function_call(args="{not json")])
        )
        assert MALFORMED_TOOL_CALL_ARGS_KEY in msg.tool_calls[0].arguments

    def test_repairable_arguments_are_repaired(self) -> None:
        msg = parse_responses_output(
            SimpleNamespace(output=[_function_call(args='```json\n{"q": "hi",}\n```')])
        )
        assert msg.tool_calls[0].arguments == {"q": "hi"}

    def test_reasoning_items_are_skipped(self) -> None:
        response = SimpleNamespace(
            output=[SimpleNamespace(type="reasoning", summary=[]), _text_item("answer")]
        )
        assert parse_responses_output(response).text == "answer"

    def test_empty_output_is_an_empty_message_not_a_crash(self) -> None:
        msg = parse_responses_output(SimpleNamespace(output=None))
        assert not msg.text
        assert msg.tool_calls is None


class TestStopReason:
    def test_truncation_beats_tool_calls(self) -> None:
        """Arguments cut off mid-JSON are unparseable; reporting TOOL_USE would
        send the loop off to execute a garbage call."""
        response = SimpleNamespace(
            incomplete_details=SimpleNamespace(reason="max_output_tokens"), status="incomplete"
        )
        assert stop_reason_from_responses(response, True) == StopReason.MAX_TOKENS

    def test_tool_calls_beat_a_plain_completion(self) -> None:
        response = SimpleNamespace(incomplete_details=None, status="completed")
        assert stop_reason_from_responses(response, True) == StopReason.TOOL_USE

    def test_plain_completion_ends_the_turn(self) -> None:
        response = SimpleNamespace(incomplete_details=None, status="completed")
        assert stop_reason_from_responses(response, False) == StopReason.END_TURN


class TestUsageFields:
    def test_reads_the_responses_field_names(self) -> None:
        """Chat Completions names (`prompt_tokens`, `prompt_tokens_details`)
        would silently report zeros here."""
        usage = SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            input_tokens_details=SimpleNamespace(cached_tokens=64, cache_write_tokens=8),
        )
        assert responses_usage_fields(SimpleNamespace(usage=usage)) == (100, 20, 64, 8)

    def test_missing_usage_is_zeros(self) -> None:
        assert responses_usage_fields(SimpleNamespace(usage=None)) == (0, 0, 0, 0)


class TestToolFormatting:
    def test_tools_are_flat_not_nested_under_function(self) -> None:
        schema = SimpleNamespace(
            name="search", description="d", input_schema={"type": "object", "properties": {}}
        )
        formatted = format_responses_tools([schema])[0]
        assert formatted["name"] == "search"
        assert "function" not in formatted, "nested shape is the Chat Completions form"
        assert formatted["strict"] is False

    def test_no_tools_is_none(self) -> None:
        assert format_responses_tools(None) is None


class TestNormaliseToolCall:
    def test_over_long_name_is_clamped(self) -> None:
        call = normalise_tool_call("id", "n" * 300, "{}")
        assert len(call.name) == 128

    @pytest.mark.parametrize("raw", [None, ""])
    def test_absent_arguments_are_an_empty_dict(self, raw) -> None:
        assert normalise_tool_call("id", "search", raw).arguments == {}


class TestFormatResponsesInput:
    def test_system_becomes_a_message_item(self) -> None:
        """Matches langchain_openai, which appends the system message as an
        input item rather than using the separate `instructions` field."""
        items = format_responses_input([], system="be brief")
        assert items == [{"type": "message", "role": "system", "content": "be brief"}]


class TestRawEvent:
    """The shim that replaces validated SDK models on the stream."""

    def test_missing_key_lets_getattr_default_apply(self) -> None:
        """The whole point of raising AttributeError instead of returning None:
        the stream loop is written as `getattr(event, "output_index", 0) or 0`,
        and a None-returning shim would defeat every one of those defaults."""
        ev = RawEvent({"type": "response.output_text.delta"})
        assert getattr(ev, "output_index", 0) == 0
        assert getattr(ev, "delta", "") == ""
        with pytest.raises(AttributeError):
            getattr(ev, "nope")

    def test_index_zero_is_preserved(self) -> None:
        """`0 or 0` must stay 0 and not be confused with a missing field."""
        assert getattr(RawEvent({"output_index": 0}), "output_index", 7) == 0

    def test_nested_dicts_and_lists_are_wrapped_on_access(self) -> None:
        ev = RawEvent({
            "item": {"type": "function_call", "name": "search", "call_id": "call_1"},
            "output": [{"type": "message"}, {"type": "function_call"}],
        })
        assert ev.item.type == "function_call"
        assert ev.item.call_id == "call_1"
        assert [o.type for o in ev.output] == ["message", "function_call"]

    def test_scalars_pass_through(self) -> None:
        ev = RawEvent({"delta": "hi", "sequence_number": 3, "logprobs": []})
        assert ev.delta == "hi"
        assert ev.sequence_number == 3
        assert ev.logprobs == []

    def test_parsers_accept_the_shim_unchanged(self) -> None:
        """`parse_responses_output` and friends were written against SDK models;
        they must work on the shim without modification, since that is what the
        stream now hands them."""
        response = RawEvent({
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "hi"}]},
                {"type": "function_call", "name": "search",
                 "arguments": '{"q": "x"}', "call_id": "call_1"},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 2,
                      "input_tokens_details": {"cached_tokens": 4, "cache_write_tokens": 1}},
            "incomplete_details": None,
            "status": "completed",
        })
        msg = parse_responses_output(response)
        assert msg.text == "hi"
        assert msg.tool_calls[0].name == "search"
        assert msg.tool_calls[0].arguments == {"q": "x"}
        assert msg.tool_calls[0].id == "call_1"
        assert responses_usage_fields(response) == (10, 2, 4, 1)
        assert stop_reason_from_responses(response, True) == StopReason.TOOL_USE


class TestSDKDrift:
    def test_sse_decoder_is_where_we_import_it_from(self) -> None:
        """`SSEDecoder` is a private SDK symbol. This pin makes an `openai`
        upgrade that moves or renames it fail the build rather than the
        stream."""
        from openai._streaming import SSEDecoder

        assert hasattr(SSEDecoder(), "aiter_bytes")

    def test_streaming_response_helper_is_public_api(self) -> None:
        from openai.resources.responses import AsyncResponses

        assert hasattr(AsyncResponses, "with_streaming_response")
