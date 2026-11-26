"""WP-13 GW-1..GW-9 (OWD-8, C3.05): the model gateway's Gemini wire, pure and over real HTTP.

Gemini CLI reaches the org's model only through this wire (``GOOGLE_GEMINI_BASE_URL`` at the gateway, the gateway
token as its bearer): ``generateContent``, ``streamGenerateContent?alt=sse`` and ``countTokens`` become the kernel's
messages and come back in Google's shapes. The provider under it is the rig's recorded transport (no live model).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import pytest
from aiohttp.test_utils import TestServer

from harness_core.access.types import ENGINE_MODEL_ID, ModelApi, RunBudget
from harness_core.gateway.errors import InvalidGeminiRequest, UnsupportedParameter
from harness_core.gateway.gemini_wire import (
    GEMINI_WIRE,
    ContentEncoder,
    content_body,
    envelope,
    gemini_error,
    model_method,
    parse_gemini_request,
)
from harness_core.gateway.model_server import REFUSAL_HEADER, create_model_gateway_app
from harness_core.gateway.wire import WireCall
from harness_core.kernel.core.messages import (
    MALFORMED_TOOL_CALL_ARGS_KEY,
    MALFORMED_TOOL_CALL_ERROR_KEY,
    AssistantMessage,
    ImagePart,
    TextPart,
    ThinkingPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from harness_core.kernel.core.responses import ModelResponse, StopReason, TokenUsage
from harness_core.kernel.core.streaming import (
    StreamCompleteEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
)
from harness_core.testing.model_gateway import GatewayRig, RecordedTransport
from tests.support.loopback_client import loopback_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# The shape Gemini CLI 0.63.0 sends (recorded on the real binary, trimmed): a system instruction, a session
# context turn, a tool call and its response by id, and declarations in both of Gemini's schema forms.
CLI_BODY: dict[str, Any] = {
    "contents": [
        {"role": "user", "parts": [{"text": "<session_context>\nThis is the Gemini CLI.\n</session_context>"}]},
        {"role": "user", "parts": [{"text": "list the files then say hi"}]},
        {
            "role": "model",
            "parts": [
                {"text": "Looking.", "thought": True, "thoughtSignature": "c2ln"},
                {"functionCall": {"id": "list_directory__c1", "name": "list_directory", "args": {"dir_path": "."}}},
            ],
        },
        {
            "role": "user",
            "parts": [
                {"functionResponse": {"id": "list_directory__c1", "name": "list_directory", "response": {"output": "a.txt"}}}
            ],
        },
    ],
    "systemInstruction": {"role": "user", "parts": [{"text": "You are an interactive CLI agent."}]},
    "tools": [
        {
            "functionDeclarations": [
                {
                    "name": "list_directory",
                    "description": "Lists a directory.",
                    "parameters": {"type": "OBJECT", "properties": {"dir_path": {"type": "STRING"}}, "required": ["dir_path"]},
                },
                {
                    "name": "mcp_tools_search",
                    "description": "Search PipesHub.",
                    "parametersJsonSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
                },
            ]
        }
    ],
    "generationConfig": {
        "temperature": 1,
        "topP": 0.95,
        "topK": 64,
        "maxOutputTokens": 8192,
        "thinkingConfig": {"includeThoughts": True, "thinkingLevel": "HIGH"},
    },
}
CALL = WireCall(model_id=ENGINE_MODEL_ID, response_id="gemini-1", created=1)


def _envelope(body: dict[str, Any] | None = None, *, stream: bool = False) -> dict[str, Any]:
    return envelope(ENGINE_MODEL_ID, body or CLI_BODY, stream=stream)


def _response(*, text: str = "Hello.", calls: list[ToolCall] | None = None, thinking: str = "") -> ModelResponse:
    content: list[Any] = [ThinkingPart(thinking=thinking)] if thinking else []
    content.append(TextPart(text=text))
    return ModelResponse(
        message=AssistantMessage(content=content, tool_calls=calls),
        usage=TokenUsage(input_tokens=120, output_tokens=30, cache_read_tokens=100, reasoning_tokens=7),
        stop_reason=StopReason.TOOL_USE if calls else StopReason.END_TURN,
    )


class TestGW1Parse:
    def test_gw1_cli_request_becomes_kernel_messages(self) -> None:
        request = parse_gemini_request(_envelope())
        assert request.model == ENGINE_MODEL_ID and request.stream is False
        assert request.system == "You are an interactive CLI agent."
        user1, user2, assistant, tool = request.messages
        assert isinstance(user1, UserMessage) and "Gemini CLI" in str(user1.content)
        assert isinstance(user2, UserMessage) and user2.content == "list the files then say hi"
        assert isinstance(assistant, AssistantMessage)
        assert assistant.tool_calls == [ToolCall(id="list_directory__c1", name="list_directory", arguments={"dir_path": "."})]
        assert assistant.text == ""
        assert isinstance(tool, ToolMessage) and tool.tool_call_id == "list_directory__c1" and tool.content == "a.txt"
        assert request.reasoning_items == 1
        assert request.max_output_tokens == 8192 and request.output_limit == 8192
        assert request.reasoning_effort == "high"
        assert set(request.dropped) == {"generationConfig.temperature", "generationConfig.topK", "generationConfig.topP"}

    def test_gw1_declarations_in_both_schema_forms_become_json_schema(self) -> None:
        tools = {t.name: t for t in parse_gemini_request(_envelope()).tools}
        assert tools["list_directory"].input_schema == {
            "type": "object",
            "properties": {"dir_path": {"type": "string"}},
            "required": ["dir_path"],
        }
        assert tools["mcp_tools_search"].input_schema == {"type": "object", "properties": {"q": {"type": "string"}}}

    def test_gw1_calls_without_ids_pair_with_their_responses_by_name_in_order(self) -> None:
        body = {
            "contents": [
                {"role": "user", "parts": [{"text": "go"}]},
                {
                    "role": "model",
                    "parts": [
                        {"functionCall": {"name": "read_file", "args": {"p": "a"}}},
                        {"functionCall": {"name": "read_file", "args": {"p": "b"}}},
                    ],
                },
                {
                    "role": "user",
                    "parts": [
                        {"functionResponse": {"name": "read_file", "response": {"output": "A"}}},
                        {"functionResponse": {"name": "read_file", "response": {"error": "no such file"}}},
                    ],
                },
            ]
        }
        _, assistant, first, second = parse_gemini_request(_envelope(body)).messages
        assert isinstance(assistant, AssistantMessage) and assistant.tool_calls is not None
        ids = [c.id for c in assistant.tool_calls]
        assert len(set(ids)) == 2
        assert isinstance(first, ToolMessage) and (first.tool_call_id, first.content, first.is_error) == (ids[0], "A", False)
        assert isinstance(second, ToolMessage) and (second.tool_call_id, second.is_error) == (ids[1], True)

    def test_gw1_a_call_renamed_by_gemini_cli_still_pairs_with_its_response(self) -> None:
        """Gemini CLI renames a call ``<name>__<id>`` once it runs it; a resumed history can hold the call under
        its first id and the response under the new one."""
        body = {
            "contents": [
                {"role": "user", "parts": [{"text": "go"}]},
                {"role": "model", "parts": [{"functionCall": {"id": "call_1", "name": "mcp_tools_create_issue", "args": {}}}]},
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "id": "mcp_tools_create_issue__call_1",
                                "name": "mcp_tools_create_issue",
                                "response": {"output": "filed"},
                            }
                        }
                    ],
                },
            ]
        }
        *_, tool = parse_gemini_request(_envelope(body)).messages
        assert isinstance(tool, ToolMessage) and tool.tool_call_id == "call_1" and tool.content == "filed"

    def test_gw1_a_repeated_response_is_dropped_not_refused(self) -> None:
        """Gemini CLI 0.63.0 replays a resumed session's tool result twice (recorded on the real binary, CF-5)."""
        response = {"functionResponse": {"id": "call_1", "name": "t", "response": {"output": "waiting"}}}
        body = {
            "contents": [
                {"role": "user", "parts": [{"text": "go"}]},
                {"role": "model", "parts": [{"functionCall": {"id": "call_1", "name": "t", "args": {}}}]},
                {"role": "user", "parts": [response, response]},
            ]
        }
        request = parse_gemini_request(_envelope(body))
        assert [type(m).__name__ for m in request.messages] == ["UserMessage", "AssistantMessage", "ToolMessage"]
        assert "contents.parts.functionResponse.repeated" in request.dropped

    def test_gw1_a_response_naming_no_open_call_is_refused(self) -> None:
        body = {"contents": [{"role": "user", "parts": [{"functionResponse": {"id": "x", "name": "y", "response": {}}}]}]}
        with pytest.raises(InvalidGeminiRequest):
            parse_gemini_request(_envelope(body))

    def test_gw1_inline_images_are_kept_and_other_media_refused(self) -> None:
        image = {"contents": [{"parts": [{"text": "see"}, {"inlineData": {"mimeType": "image/png", "data": "iVBO"}}]}]}
        (message,) = parse_gemini_request(_envelope(image)).messages
        assert isinstance(message, UserMessage) and isinstance(message.content, list)
        assert isinstance(message.content[1], ImagePart)
        pdf = {"contents": [{"parts": [{"inlineData": {"mimeType": "application/pdf", "data": "JVBE"}}]}]}
        with pytest.raises(UnsupportedParameter):
            parse_gemini_request(_envelope(pdf))
        remote = {"contents": [{"parts": [{"fileData": {"mimeType": "image/png", "fileUri": "gs://b/x.png"}}]}]}
        with pytest.raises(UnsupportedParameter):
            parse_gemini_request(_envelope(remote))


class TestGW2Refusals:
    @pytest.mark.parametrize(
        ("patch", "param"),
        [
            ({"cachedContent": "cachedContents/1"}, "cachedContent"),
            ({"tools": [{"googleSearch": {}}]}, "tools.googleSearch"),
            ({"tools": [{"codeExecution": {}}]}, "tools.codeExecution"),
            ({"toolConfig": {"functionCallingConfig": {"mode": "ANY"}}}, "toolConfig.functionCallingConfig.mode"),
            ({"generationConfig": {"responseMimeType": "application/json"}}, "generationConfig.responseMimeType"),
            ({"generationConfig": {"responseJsonSchema": {"type": "object"}}}, "generationConfig.responseJsonSchema"),
            ({"generationConfig": {"stopSequences": ["x"]}}, "generationConfig.stopSequences"),
            ({"generationConfig": {"candidateCount": 2}}, "generationConfig.candidateCount"),
            ({"generationConfig": {"responseModalities": ["IMAGE"]}}, "generationConfig.responseModalities"),
        ],
    )
    def test_gw2_what_the_transports_cannot_honour_is_refused_not_ignored(self, patch: dict[str, Any], param: str) -> None:
        with pytest.raises(UnsupportedParameter) as refused:
            parse_gemini_request(_envelope({**CLI_BODY, **patch}))
        assert refused.value.param == param

    def test_gw2_a_request_without_contents_or_model_is_invalid(self) -> None:
        with pytest.raises(InvalidGeminiRequest):
            parse_gemini_request(_envelope({"contents": []}))
        with pytest.raises(InvalidGeminiRequest):
            parse_gemini_request({"model": "", "stream": False, "request": CLI_BODY})

    def test_gw2_safety_settings_and_labels_are_dropped(self) -> None:
        request = parse_gemini_request(_envelope({**CLI_BODY, "safetySettings": [], "labels": {"a": "b"}}))
        assert {"safetySettings", "labels"} <= set(request.dropped)


class TestGW3Answers:
    def test_gw3_a_whole_answer_is_a_generate_content_response(self) -> None:
        call = ToolCall(id="c9", name="mcp_tools_search", arguments={"q": "x"})
        body = content_body(_response(text="Searching.", calls=[call], thinking="hm"), model_id=ENGINE_MODEL_ID, response_id="g-1")
        (candidate,) = body["candidates"]
        assert candidate["content"]["role"] == "model"
        assert candidate["content"]["parts"] == [
            {"text": "hm", "thought": True},
            {"text": "Searching."},
            {"functionCall": {"id": "c9", "name": "mcp_tools_search", "args": {"q": "x"}}},
        ]
        assert candidate["finishReason"] == "STOP"
        assert body["usageMetadata"] == {
            "promptTokenCount": 120,
            "candidatesTokenCount": 30,
            "totalTokenCount": 150,
            "cachedContentTokenCount": 100,
            "thoughtsTokenCount": 7,
        }
        assert (body["modelVersion"], body["responseId"]) == (ENGINE_MODEL_ID, "g-1")

    def test_gw3_a_malformed_call_finishes_as_gemini_says_it(self) -> None:
        bad = ToolCall(id="c1", name="x", arguments={MALFORMED_TOOL_CALL_ARGS_KEY: "{", MALFORMED_TOOL_CALL_ERROR_KEY: "bad"})
        body = content_body(_response(calls=[bad]), model_id=ENGINE_MODEL_ID, response_id="g")
        assert body["candidates"][0]["finishReason"] == "MALFORMED_FUNCTION_CALL"
        assert body["candidates"][0]["content"]["parts"][-1]["functionCall"]["args"] == {}

    def test_gw3_max_tokens(self) -> None:
        response = _response().model_copy(update={"stop_reason": StopReason.MAX_TOKENS})
        assert content_body(response, model_id="m", response_id="r")["candidates"][0]["finishReason"] == "MAX_TOKENS"

    def test_gw3_stream_sends_text_as_it_comes_and_calls_whole_at_the_end(self) -> None:
        encoder = ContentEncoder(ENGINE_MODEL_ID, "g-2")
        call = ToolCall(id="c1", name="read_file", arguments={"p": "a"})
        chunks = [
            *encoder.start(),
            *encoder.on_event(ThinkingDeltaEvent(delta="hm")),
            *encoder.on_event(TextDeltaEvent(delta="Read")),
            *encoder.on_event(TextDeltaEvent(delta="ing.")),
            *encoder.on_event(ToolCallDeltaEvent(index=0, id="c1", name="read_file", arguments_delta='{"p"')),
            *encoder.on_event(StreamCompleteEvent(response=_response(text="Reading.", calls=[call], thinking="hm"))),
        ]
        parts = [c["candidates"][0]["content"]["parts"] for c in chunks]
        assert parts == [
            [{"text": "hm", "thought": True}],
            [{"text": "Read"}],
            [{"text": "ing."}],
            [{"functionCall": {"id": "c1", "name": "read_file", "args": {"p": "a"}}}],
        ]
        assert "finishReason" not in chunks[0]["candidates"][0]
        assert chunks[-1]["candidates"][0]["finishReason"] == "STOP"
        assert chunks[-1]["usageMetadata"]["totalTokenCount"] == 150
        assert GEMINI_WIRE.frame(chunks[0]).startswith(b"data: {") and GEMINI_WIRE.frame(chunks[0]).endswith(b"\r\n\r\n")
        assert GEMINI_WIRE.terminal == b""

    def test_gw3_text_reported_only_at_the_end_is_sent_whole(self) -> None:
        encoder = ContentEncoder(ENGINE_MODEL_ID, "g-3")
        (chunk,) = encoder.on_event(StreamCompleteEvent(response=_response(text="All at once.")))
        assert chunk["candidates"][0]["content"]["parts"] == [{"text": "All at once."}]


class TestGW4Errors:
    def test_gw4_google_error_envelope_tagged_with_the_gateway_code(self) -> None:
        body, headers = gemini_error(402, "budget_exhausted", "the run's budget is spent")
        assert headers == {}
        error = cast("dict[str, Any]", body["error"])
        assert error["code"] == 402 and error["status"] == "RESOURCE_EXHAUSTED"
        assert error["message"] == "the run's budget is spent [gateway:budget_exhausted]"
        assert error["details"][0]["reason"] == "budget_exhausted"

    def test_gw4_the_path_names_a_model_and_one_of_three_methods(self) -> None:
        assert model_method("engine-model:streamGenerateContent") == ("engine-model", "streamGenerateContent")
        assert model_method("models/engine-model:countTokens") == ("engine-model", "countTokens")
        for bad in ("engine-model", "engine-model:embedContent", ":generateContent", "a/b:generateContent"):
            assert model_method(bad) is None

    def test_gw4_count_tokens_counts_the_wrapped_request(self) -> None:
        wrapped = envelope(ENGINE_MODEL_ID, {"generateContentRequest": CLI_BODY}, stream=False)
        assert wrapped["request"] is CLI_BODY


# Over HTTP: the gateway app with its real admission (token, grant, budget) and the rig's recorded provider.


@pytest.fixture
async def served() -> AsyncIterator[tuple[GatewayRig, str, str]]:
    rig = GatewayRig()
    binding = rig.pin(engine="acp-gemini")
    server = TestServer(create_model_gateway_app(rig.gateway), host="127.0.0.1")
    await server.start_server()
    try:
        yield rig, f"http://127.0.0.1:{server.port}", rig.token(binding)
    finally:
        await server.close()


def _headers(token: str, **extra: str) -> dict[str, str]:
    # What Gemini CLI sends on a gateway: its custom bearer header, and an empty API key header.
    return {"Authorization": f"Bearer {token}", "x-goog-api-key": "", **extra}


class TestGW5OverHttp:
    async def test_gw5_generate_content(self, served: tuple[GatewayRig, str, str]) -> None:
        rig, url, token = served
        async with loopback_client() as client:
            answer = await client.post(f"{url}/v1beta/models/{ENGINE_MODEL_ID}:generateContent", json=CLI_BODY, headers=_headers(token))
        assert answer.status_code == 200, answer.text
        body = answer.json()
        assert body["candidates"][0]["content"]["parts"] == [{"text": "hello"}]
        assert body["usageMetadata"]["promptTokenCount"] == 10
        (call,) = rig.transport.calls
        assert call.system == "You are an interactive CLI agent." and call.stream is False
        assert [t.name for t in call.tools or []] == ["list_directory", "mcp_tools_search"]

    async def test_gw6_stream_generate_content_is_sse(self, served: tuple[GatewayRig, str, str]) -> None:
        rig, url, token = served
        rig.transport.events = [  # pyright: ignore[reportAttributeAccessIssue] - a list of StreamEvent members
            TextDeltaEvent(delta="hel"),
            TextDeltaEvent(delta="lo"),
            StreamCompleteEvent(response=_response(text="hello")),
        ]
        async with loopback_client() as client:
            answer = await client.post(
                f"{url}/v1beta/models/{ENGINE_MODEL_ID}:streamGenerateContent?alt=sse", json=CLI_BODY, headers=_headers(token)
            )
        assert answer.status_code == 200 and answer.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line.removeprefix("data: ")) for line in answer.text.split("\r\n\r\n") if line]
        texts = [p.get("text") for e in events for p in e["candidates"][0]["content"]["parts"]]
        assert texts == ["hel", "lo"]
        assert events[-1]["candidates"][0]["finishReason"] == "STOP"
        assert rig.transport.calls[0].stream is True

    async def test_gw7_count_tokens_reaches_no_provider(self, served: tuple[GatewayRig, str, str]) -> None:
        rig, url, token = served
        async with loopback_client() as client:
            answer = await client.post(
                f"{url}/v1beta/models/{ENGINE_MODEL_ID}:countTokens", json={"contents": CLI_BODY["contents"]}, headers=_headers(token)
            )
        assert answer.status_code == 200 and answer.json()["totalTokens"] > 0
        assert rig.transport.calls == []

    async def test_gw8_a_real_api_key_header_or_another_model_is_refused_in_googles_shape(
        self, served: tuple[GatewayRig, str, str]
    ) -> None:
        rig, url, token = served
        async with loopback_client() as client:
            keyed = await client.post(
                f"{url}/v1beta/models/{ENGINE_MODEL_ID}:generateContent",
                json=CLI_BODY,
                headers=_headers(token, **{"x-goog-api-key": "some-key"}),
            )
            other = await client.post(f"{url}/v1beta/models/gemini-2.5-pro:generateContent", json=CLI_BODY, headers=_headers(token))
            unknown = await client.post(f"{url}/v1beta/models/{ENGINE_MODEL_ID}:embedContent", json={}, headers=_headers(token))
            anonymous = await client.post(f"{url}/v1beta/models/{ENGINE_MODEL_ID}:generateContent", json=CLI_BODY)
        assert keyed.status_code == 403 and keyed.headers[REFUSAL_HEADER] == "caller_credential_header"
        assert keyed.json()["error"]["status"] == "PERMISSION_DENIED"
        assert other.status_code == 404 and other.json()["error"]["details"][0]["reason"] == "model_not_granted"
        assert unknown.status_code == 404 and unknown.json()["error"]["details"][0]["reason"] == "route_not_allowed"
        assert anonymous.status_code == 401 and "[gateway:" in anonymous.json()["error"]["message"]
        assert rig.transport.calls == []

    async def test_gw9_a_spent_budget_is_402_which_gemini_cli_does_not_retry(self) -> None:
        rig = GatewayRig(transport=RecordedTransport())
        binding = rig.pin(engine="acp-gemini", budget=RunBudget(max_cost_usd=None, max_tokens=1))
        server = TestServer(create_model_gateway_app(rig.gateway), host="127.0.0.1")
        await server.start_server()
        try:
            async with loopback_client() as client:
                answer = await client.post(
                    f"http://127.0.0.1:{server.port}/v1beta/models/{ENGINE_MODEL_ID}:generateContent",
                    json=CLI_BODY,
                    headers=_headers(rig.token(binding)),
                )
        finally:
            await server.close()
        assert answer.status_code == 402
        error = answer.json()["error"]
        assert error["details"][0]["reason"] in {"budget_exhausted", "budget_insufficient"}
        assert error["message"].endswith(f"[gateway:{error['details'][0]['reason']}]")


def test_gw_wire_names_its_api() -> None:
    assert GEMINI_WIRE.api is ModelApi.GEMINI_GENERATE_CONTENT
