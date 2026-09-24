"""Coverage tests for `app/agent_loop_lib/transport/ollama.py`.

`OllamaTransport` talks to Ollama over raw `httpx` (no SDK), so tests mock
`self._client.post`/`self._client.stream` directly with `AsyncMock`/fakes —
no real HTTP calls are made.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ImagePart,
    ImageSource,
    SystemMessage,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.streaming import StreamCompleteEvent, TextDeltaEvent
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agent_loop_lib.transport.ollama import OllamaTransport


def _transport(**kwargs: Any) -> OllamaTransport:
    return OllamaTransport(**kwargs)


def _http_response(json_body: dict[str, Any], status_code: int = 200) -> MagicMock:
    request = httpx.Request("POST", "http://localhost:11434/api/chat")
    resp = MagicMock()
    resp.json.return_value = json_body
    resp.status_code = status_code
    if status_code >= 400:
        real_resp = httpx.Response(status_code=status_code, request=request, json=json_body)
        resp.raise_for_status.side_effect = httpx.HTTPStatusError("error", request=request, response=real_resp)
    else:
        resp.raise_for_status.return_value = None
    return resp


class _FakeStreamResponse:
    def __init__(self, lines: list[str], status_code: int = 200, raise_status: bool = False) -> None:
        self._lines = lines
        self.status_code = status_code
        self._raise_status = raise_status

    def raise_for_status(self) -> None:
        if self._raise_status:
            request = httpx.Request("POST", "http://localhost:11434/api/chat")
            response = httpx.Response(status_code=self.status_code, request=request)
            raise httpx.HTTPStatusError("bad status", request=request, response=response)

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamCM:
    def __init__(self, response: _FakeStreamResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeStreamResponse:
        return self._response

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


class _RaisingStreamCM:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def __aenter__(self) -> Any:
        raise self._exc

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


class TestInit:
    def test_defaults(self) -> None:
        transport = _transport()
        assert transport.provider == "ollama"
        assert transport.model_name == OllamaTransport.DEFAULT_MODEL
        assert str(transport._client.base_url) == OllamaTransport.DEFAULT_BASE_URL

    def test_base_url_trailing_slash_stripped(self) -> None:
        transport = _transport(base_url="http://localhost:11434/")
        assert transport._base_url == "http://localhost:11434"

    def test_options_include_fixed_sampler_settings(self) -> None:
        transport = _transport(temperature=0.7)
        assert transport._options["temperature"] == 0.7
        assert transport._options["repeat_penalty"] == 1.0
        assert transport._options["top_p"] == 1.0
        assert "num_ctx" not in transport._options

    def test_num_ctx_included_when_given(self) -> None:
        transport = _transport(num_ctx=4096)
        assert transport._options["num_ctx"] == 4096

    def test_cumulative_counters_start_at_zero(self) -> None:
        transport = _transport()
        assert transport.total_input_tokens == 0
        assert transport.total_output_tokens == 0
        assert transport.total_llm_calls == 0

    async def test_aclose_closes_http_client(self) -> None:
        transport = _transport()
        transport._client.aclose = AsyncMock()
        await transport.aclose()
        transport._client.aclose.assert_awaited_once()


class TestFormatMessage:
    def test_tool_message_has_no_id_field_and_includes_footer(self) -> None:
        transport = _transport()
        msg = ToolMessage(content="result", tool_call_id="tc1", step_footer=" [meta]")
        formatted = transport._format_message(msg)
        assert formatted == {"role": "tool", "content": "result [meta]"}
        assert "tool_call_id" not in formatted

    def test_multipart_tool_message_strips_images_keeps_text(self) -> None:
        """Ollama's /api/chat has no multipart tool-result support — images
        must be stripped here (delivered instead via the PRE_MODEL fallback
        hook) while the text parts are preserved."""
        transport = _transport()
        msg = ToolMessage(
            content=[
                TextPart(text="[ref1] (image)"),
                ImagePart(source=ImageSource(type="base64", media_type="image/png", data="abc123")),
                TextPart(text=" more text"),
            ],
            tool_call_id="tc1",
            step_footer=" [meta]",
        )
        formatted = transport._format_message(msg)
        assert formatted == {"role": "tool", "content": "[ref1] (image) more text [meta]"}

    def test_assistant_message_with_tool_calls(self) -> None:
        transport = _transport()
        msg = AssistantMessage(
            content=[TextPart(text="checking")],
            tool_calls=[ToolCall(id="1", name="search", arguments={"q": "x"})],
        )
        formatted = transport._format_message(msg)
        assert formatted["content"] == "checking"
        assert formatted["tool_calls"] == [{"function": {"name": "search", "arguments": {"q": "x"}}}]

    def test_assistant_message_without_tool_calls_gets_empty_list(self) -> None:
        transport = _transport()
        msg = AssistantMessage(content=[TextPart(text="hi")])
        formatted = transport._format_message(msg)
        assert formatted["tool_calls"] == []

    def test_user_message_with_list_content_joins_text(self) -> None:
        transport = _transport()
        msg = UserMessage(content=[TextPart(text="a"), TextPart(text="b")])
        formatted = transport._format_message(msg)
        assert formatted == {"role": "user", "content": "a b"}

    def test_system_message_plain_branch(self) -> None:
        transport = _transport()
        formatted = transport._format_message(SystemMessage(content="be nice"))
        assert formatted == {"role": "system", "content": "be nice"}


class TestFormatMessages:
    def test_prepends_system_when_given(self) -> None:
        transport = _transport()
        result = transport._format_messages([UserMessage(content="hi")], system="be terse")
        assert result[0] == {"role": "system", "content": "be terse"}

    def test_no_system_when_absent(self) -> None:
        transport = _transport()
        result = transport._format_messages([UserMessage(content="hi")], system=None)
        assert len(result) == 1


class TestFormatTools:
    def test_none_when_empty(self) -> None:
        transport = _transport()
        assert transport._format_tools(None) is None
        assert transport._format_tools([]) is None

    def test_maps_tool_schema_to_function_shape(self) -> None:
        transport = _transport()
        tools = [ToolSchema(name="search", description="d", input_schema={"type": "object"})]
        result = transport._format_tools(tools)
        assert result[0]["function"]["name"] == "search"

    def test_missing_input_schema_defaults(self) -> None:
        transport = _transport()
        tools = [ToolSchema(name="noop", description="d", input_schema={})]
        result = transport._format_tools(tools)
        assert result[0]["function"]["parameters"] == {"type": "object", "properties": {}}


class TestParseMessage:
    def test_no_tool_calls_returns_none(self) -> None:
        transport = _transport()
        msg = transport._parse_message({"content": "hi"})
        assert msg.text == "hi"
        assert msg.tool_calls is None

    def test_string_arguments_parsed_as_json(self) -> None:
        transport = _transport()
        raw = {"content": "", "tool_calls": [{"id": "1", "function": {"name": "search", "arguments": '{"q": "x"}'}}]}
        msg = transport._parse_message(raw)
        assert msg.tool_calls[0].arguments == {"q": "x"}

    def test_malformed_string_arguments_become_empty_dict(self) -> None:
        transport = _transport()
        raw = {"tool_calls": [{"id": "1", "function": {"name": "search", "arguments": "{not json"}}]}
        msg = transport._parse_message(raw)
        assert msg.tool_calls[0].arguments == {}

    def test_dict_arguments_used_as_is(self) -> None:
        transport = _transport()
        raw = {"tool_calls": [{"id": "1", "function": {"name": "search", "arguments": {"q": "x"}}}]}
        msg = transport._parse_message(raw)
        assert msg.tool_calls[0].arguments == {"q": "x"}

    def test_missing_id_generates_indexed_placeholder(self) -> None:
        transport = _transport()
        raw = {"tool_calls": [{"function": {"name": "search", "arguments": {}}}]}
        msg = transport._parse_message(raw)
        assert msg.tool_calls[0].id == "call_0"

    def test_empty_content_becomes_none(self) -> None:
        transport = _transport()
        msg = transport._parse_message({"content": ""})
        assert msg.content == []


class TestUsageFrom:
    def test_extracts_and_accumulates(self) -> None:
        transport = _transport()
        usage = transport._usage_from({"prompt_eval_count": 20, "eval_count": 10})
        assert usage.input_tokens == 20
        assert usage.output_tokens == 10
        assert transport.total_input_tokens == 20
        assert transport.total_llm_calls == 1

    def test_missing_fields_default_to_zero(self) -> None:
        transport = _transport()
        usage = transport._usage_from({})
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0

    def test_none_values_default_to_zero(self) -> None:
        transport = _transport()
        usage = transport._usage_from({"prompt_eval_count": None, "eval_count": None})
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0


class TestWrapError:
    def test_connect_error_is_retryable(self) -> None:
        transport = _transport()
        exc = httpx.ConnectError("connection refused")
        wrapped = transport._wrap_error(exc, "complete")
        assert wrapped.retryable is True
        assert wrapped.status_code is None

    def test_timeout_is_retryable(self) -> None:
        transport = _transport()
        exc = httpx.TimeoutException("timed out")
        wrapped = transport._wrap_error(exc, "stream")
        assert wrapped.retryable is True

    def test_http_status_error_429_is_retryable(self) -> None:
        transport = _transport()
        request = httpx.Request("POST", "http://localhost:11434/api/chat")
        response = httpx.Response(status_code=429, request=request)
        exc = httpx.HTTPStatusError("rate limited", request=request, response=response)
        wrapped = transport._wrap_error(exc, "complete")
        assert wrapped.status_code == 429
        assert wrapped.retryable is True

    def test_http_status_error_400_is_not_retryable(self) -> None:
        transport = _transport()
        request = httpx.Request("POST", "http://localhost:11434/api/chat")
        response = httpx.Response(status_code=400, request=request)
        exc = httpx.HTTPStatusError("bad request", request=request, response=response)
        wrapped = transport._wrap_error(exc, "complete")
        assert wrapped.status_code == 400
        assert wrapped.retryable is False

    def test_generic_exception_not_retryable(self) -> None:
        transport = _transport()
        wrapped = transport._wrap_error(ValueError("boom"), "complete")
        assert wrapped.status_code is None
        assert wrapped.retryable is False


class TestComplete:
    async def test_maps_payload_and_response(self) -> None:
        transport = _transport(model="llama3.2")
        body = {
            "message": {"content": "42", "tool_calls": []},
            "prompt_eval_count": 8,
            "eval_count": 4,
        }
        transport._client.post = AsyncMock(return_value=_http_response(body))

        result = await transport.complete([UserMessage(content="what is the answer?")])

        transport._client.post.assert_awaited_once()
        args, kwargs = transport._client.post.call_args
        assert args[0] == "/api/chat"
        payload = kwargs["json"]
        assert payload["model"] == "llama3.2"
        assert payload["stream"] is False
        assert "tools" not in payload
        assert result.message.text == "42"
        assert result.usage.input_tokens == 8
        assert result.model == "llama3.2"

    async def test_system_blocks_joined_when_system_absent(self) -> None:
        transport = _transport()
        transport._client.post = AsyncMock(return_value=_http_response({"message": {"content": "ok"}}))

        await transport.complete([UserMessage(content="hi")], system_blocks=["A", "B"])

        _, kwargs = transport._client.post.call_args
        assert kwargs["json"]["messages"][0] == {"role": "system", "content": "A\n\nB"}

    async def test_tools_included_when_present(self) -> None:
        transport = _transport()
        transport._client.post = AsyncMock(return_value=_http_response({"message": {"content": "ok"}}))
        tools = [ToolSchema(name="search", description="d", input_schema={"type": "object"})]

        await transport.complete([UserMessage(content="hi")], tools=tools)

        _, kwargs = transport._client.post.call_args
        assert kwargs["json"]["tools"][0]["function"]["name"] == "search"

    async def test_default_model_used_when_not_overridden(self) -> None:
        transport = _transport()
        transport._client.post = AsyncMock(return_value=_http_response({"message": {"content": "ok"}}))
        result = await transport.complete([UserMessage(content="hi")])
        assert result.model == OllamaTransport.DEFAULT_MODEL

    async def test_http_error_wrapped_as_transport_error(self) -> None:
        transport = _transport()
        transport._client.post = AsyncMock(return_value=_http_response({}, status_code=500))
        with pytest.raises(TransportError) as exc_info:
            await transport.complete([UserMessage(content="hi")])
        assert exc_info.value.retryable is True

    async def test_connect_error_wrapped(self) -> None:
        transport = _transport()
        transport._client.post = AsyncMock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(TransportError) as exc_info:
            await transport.complete([UserMessage(content="hi")])
        assert exc_info.value.retryable is True


class TestCompleteStructured:
    async def test_parses_valid_json_content(self) -> None:
        transport = _transport()
        body = {"message": {"content": '{"route": "react"}'}, "prompt_eval_count": 1, "eval_count": 1}
        transport._client.post = AsyncMock(return_value=_http_response(body))

        result = await transport.complete_structured([UserMessage(content="classify")], output_schema={"type": "object"})

        assert result.data == {"route": "react"}
        _, kwargs = transport._client.post.call_args
        assert kwargs["json"]["format"] == {"type": "object"}

    async def test_empty_content_returns_empty_dict(self) -> None:
        transport = _transport()
        body = {"message": {"content": ""}}
        transport._client.post = AsyncMock(return_value=_http_response(body))
        result = await transport.complete_structured([UserMessage(content="x")], output_schema={})
        assert result.data == {}

    async def test_invalid_json_raises_transport_error(self) -> None:
        transport = _transport()
        body = {"message": {"content": "{not json"}}
        transport._client.post = AsyncMock(return_value=_http_response(body))
        with pytest.raises(TransportError, match="not valid JSON"):
            await transport.complete_structured([UserMessage(content="x")], output_schema={})

    async def test_http_error_wrapped(self) -> None:
        transport = _transport()
        transport._client.post = AsyncMock(return_value=_http_response({}, status_code=503))
        with pytest.raises(TransportError) as exc_info:
            await transport.complete_structured([UserMessage(content="x")], output_schema={})
        assert exc_info.value.retryable is True


class TestStream:
    def _ndjson(self, *objs: dict[str, Any]) -> list[str]:
        return [json.dumps(o) for o in objs]

    async def test_text_deltas_and_final_assembly(self) -> None:
        transport = _transport()
        lines = self._ndjson(
            {"message": {"content": "Hel"}},
            {"message": {"content": "lo"}},
            {"message": {"content": ""}, "done": True, "prompt_eval_count": 5, "eval_count": 2},
        )
        fake_response = _FakeStreamResponse(lines)
        transport._client.stream = MagicMock(return_value=_FakeStreamCM(fake_response))

        events = [event async for event in transport.stream([UserMessage(content="hi")])]

        text_events = [e for e in events if isinstance(e, TextDeltaEvent)]
        assert [e.delta for e in text_events] == ["Hel", "lo"]
        final = next(e for e in events if isinstance(e, StreamCompleteEvent)).response
        assert final.message.text == "Hello"
        assert final.usage.input_tokens == 5

    async def test_blank_lines_are_skipped(self) -> None:
        transport = _transport()
        lines = ["", "   ", json.dumps({"message": {"content": "ok"}, "done": True})]
        fake_response = _FakeStreamResponse(lines)
        transport._client.stream = MagicMock(return_value=_FakeStreamCM(fake_response))

        events = [event async for event in transport.stream([UserMessage(content="hi")])]
        text_events = [e for e in events if isinstance(e, TextDeltaEvent)]
        assert [e.delta for e in text_events] == ["ok"]

    async def test_tool_calls_captured_in_final_message(self) -> None:
        transport = _transport()
        lines = self._ndjson(
            {"message": {"content": "", "tool_calls": [{"function": {"name": "search", "arguments": {"q": "x"}}}]}},
            {"message": {"content": ""}, "done": True},
        )
        fake_response = _FakeStreamResponse(lines)
        transport._client.stream = MagicMock(return_value=_FakeStreamCM(fake_response))

        events = [event async for event in transport.stream([UserMessage(content="search")])]
        final = next(e for e in events if isinstance(e, StreamCompleteEvent)).response
        assert final.message.tool_calls[0].name == "search"

    async def test_done_without_prior_message_uses_final_chunk_message(self) -> None:
        transport = _transport()
        lines = [json.dumps({"message": {"content": "solo"}, "done": True, "prompt_eval_count": 1, "eval_count": 1})]
        fake_response = _FakeStreamResponse(lines)
        transport._client.stream = MagicMock(return_value=_FakeStreamCM(fake_response))

        events = [event async for event in transport.stream([UserMessage(content="hi")])]
        final = next(e for e in events if isinstance(e, StreamCompleteEvent)).response
        assert final.message.text == "solo"

    async def test_tools_and_system_blocks_forwarded(self) -> None:
        transport = _transport()
        lines = [json.dumps({"message": {"content": "ok"}, "done": True})]
        fake_response = _FakeStreamResponse(lines)
        transport._client.stream = MagicMock(return_value=_FakeStreamCM(fake_response))
        tools = [ToolSchema(name="search", description="d", input_schema={"type": "object"})]

        async for _ in transport.stream([UserMessage(content="hi")], tools=tools, system_blocks=["A", "B"]):
            pass

        _, kwargs = transport._client.stream.call_args
        assert kwargs["json"]["tools"][0]["function"]["name"] == "search"
        assert kwargs["json"]["messages"][0] == {"role": "system", "content": "A\n\nB"}

    async def test_http_status_error_on_stream_wrapped(self) -> None:
        transport = _transport()
        fake_response = _FakeStreamResponse([], status_code=500, raise_status=True)
        transport._client.stream = MagicMock(return_value=_FakeStreamCM(fake_response))
        with pytest.raises(TransportError) as exc_info:
            async for _ in transport.stream([UserMessage(content="hi")]):
                pass
        assert exc_info.value.retryable is True

    async def test_connect_error_opening_stream_wrapped(self) -> None:
        transport = _transport()
        transport._client.stream = MagicMock(return_value=_RaisingStreamCM(httpx.ConnectError("down")))
        with pytest.raises(TransportError) as exc_info:
            async for _ in transport.stream([UserMessage(content="hi")]):
                pass
        assert exc_info.value.retryable is True
