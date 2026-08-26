"""Message/tool-schema round-trip conversion between LangChain and
agent-loop's provider-agnostic types (`app/agents/agent_loop/converters.py`)."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.messages import ToolMessage as LCToolMessage
from pydantic import ValidationError

from app.agent_loop_lib.core.messages import (
    MALFORMED_TOOL_CALL_ARGS_KEY,
    MALFORMED_TOOL_CALL_ERROR_KEY,
    AssistantMessage,
    ImagePart,
    ImageSource,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.messages import (
    SystemMessage as AgentSystemMessage,
)
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agents.agent_loop.converters import (
    _clamp_tool_call_id,
    convert_assistant_message_from_langchain,
    convert_message_from_langchain,
    convert_message_to_langchain,
    convert_messages_to_langchain,
    convert_tool_call_from_langchain,
    convert_tool_schema_to_langchain_dict,
    convert_tool_schemas_to_langchain,
    output_schema_to_pydantic_model,
    token_usage_from_ai_message,
)


class TestMessageToLangchain:
    def test_system_message(self) -> None:
        result = convert_message_to_langchain(AgentSystemMessage(content="be nice"))
        assert isinstance(result, SystemMessage)
        assert result.content == "be nice"

    def test_user_message_plain_text(self) -> None:
        result = convert_message_to_langchain(UserMessage(content="hello"))
        assert isinstance(result, HumanMessage)
        assert result.content == "hello"

    def test_user_message_with_parts(self) -> None:
        result = convert_message_to_langchain(UserMessage(content=[TextPart(text="hi")]))
        assert isinstance(result, HumanMessage)
        assert result.content == [{"type": "text", "text": "hi"}]

    def test_assistant_message_with_tool_calls(self) -> None:
        msg = AssistantMessage(
            content="calling a tool",
            tool_calls=[ToolCall(id="tc1", name="search", arguments={"q": "x"})],
        )
        result = convert_message_to_langchain(msg)
        assert isinstance(result, AIMessage)
        assert len(result.tool_calls) == 1
        call = result.tool_calls[0]
        assert call["name"] == "search"
        assert call["args"] == {"q": "x"}
        assert call["id"] == "tc1"

    def test_tool_message(self) -> None:
        msg = ToolMessage(content="result text", tool_call_id="tc1", is_error=False)
        result = convert_message_to_langchain(msg)
        assert isinstance(result, LCToolMessage)
        assert result.tool_call_id == "tc1"
        assert result.status == "success"

    def test_tool_message_error_status(self) -> None:
        msg = ToolMessage(content="boom", tool_call_id="tc1", is_error=True)
        result = convert_message_to_langchain(msg)
        assert result.status == "error"

    def test_convert_messages_prepends_system(self) -> None:
        converted = convert_messages_to_langchain([UserMessage(content="hi")], system="sys prompt")
        assert isinstance(converted[0], SystemMessage)
        assert converted[0].content == "sys prompt"
        assert isinstance(converted[1], HumanMessage)

    def test_overlong_tool_call_id_is_clamped_on_assistant_message(self) -> None:
        """OpenAI/Azure reject tool_calls[].id > 64 chars with a 400.
        IDs exceeding the limit must be shortened at the outgoing
        boundary (this converter) before they reach the provider."""
        long_id = "call_" + "a" * 70  # 75 chars, well over 64
        msg = AssistantMessage(
            content="calling",
            tool_calls=[ToolCall(id=long_id, name="search", arguments={"q": "x"})],
        )
        result = convert_message_to_langchain(msg)
        assert len(result.tool_calls[0]["id"]) <= 64
        assert result.tool_calls[0]["id"].startswith("call_")

    def test_overlong_tool_call_id_is_clamped_on_tool_message(self) -> None:
        long_id = "call_" + "b" * 70
        msg = ToolMessage(content="result", tool_call_id=long_id, is_error=False)
        result = convert_message_to_langchain(msg)
        assert len(result.tool_call_id) <= 64

    def test_clamped_ids_match_between_assistant_and_tool_messages(self) -> None:
        """The same long ID must produce the same clamped value on both
        the AssistantMessage (tool_calls[].id) and the ToolMessage
        (tool_call_id), or the provider sees an unmatched pair."""
        long_id = "call_" + "c" * 70
        assistant_msg = AssistantMessage(
            content="",
            tool_calls=[ToolCall(id=long_id, name="t", arguments={})],
        )
        tool_msg = ToolMessage(content="ok", tool_call_id=long_id)
        ai = convert_message_to_langchain(assistant_msg)
        tm = convert_message_to_langchain(tool_msg)
        assert ai.tool_calls[0]["id"] == tm.tool_call_id

    def test_short_tool_call_id_passes_through_unchanged(self) -> None:
        short_id = "call_abc123"
        msg = AssistantMessage(
            content="",
            tool_calls=[ToolCall(id=short_id, name="t", arguments={})],
        )
        result = convert_message_to_langchain(msg)
        assert result.tool_calls[0]["id"] == short_id

    def test_multipart_tool_message_produces_content_blocks(self) -> None:
        """A ToolMessage carrying images (search/fetch tools surfacing
        IMAGE blocks) must serialize to LangChain's list-of-blocks content
        shape, not be collapsed into a string."""
        msg = ToolMessage(
            content=[
                TextPart(text="[ref1] (image)"),
                ImagePart(source=ImageSource(type="base64", media_type="image/png", data="abc123")),
            ],
            tool_call_id="tc1",
        )
        result = convert_message_to_langchain(msg)
        assert isinstance(result, LCToolMessage)
        assert result.content == [
            {"type": "text", "text": "[ref1] (image)"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
        ]

    def test_multipart_tool_message_appends_step_footer_as_text_block(self) -> None:
        msg = ToolMessage(
            content=[TextPart(text="hello")],
            tool_call_id="tc1",
            step_footer="\n\n[loop: step 1/5]",
        )
        result = convert_message_to_langchain(msg)
        assert result.content[-1] == {"type": "text", "text": "\n\n[loop: step 1/5]"}

    def test_strip_tool_images_serializes_text_only_for_ollama_fallback(self) -> None:
        """Ollama's `/api/chat` rejects multipart tool-result content — see
        `LangChainTransport._supports_multipart_tool_result`. When
        `strip_tool_images=True`, a multipart ToolMessage must serialize to
        a plain text string (text parts + step_footer), not the
        list-of-blocks shape, so the image never reaches the provider
        (the PRE_MODEL fallback hook re-injects it separately)."""
        msg = ToolMessage(
            content=[
                TextPart(text="[ref1] (image)"),
                ImagePart(source=ImageSource(type="base64", media_type="image/png", data="abc123")),
            ],
            tool_call_id="tc1",
            step_footer="\n\n[loop: step 1/5]",
        )
        result = convert_message_to_langchain(msg, strip_tool_images=True)
        assert isinstance(result, LCToolMessage)
        assert isinstance(result.content, str)
        assert result.content == "[ref1] (image)\n\n[loop: step 1/5]"

    def test_strip_tool_images_false_keeps_multipart_content(self) -> None:
        """Default behavior (non-Ollama providers) must be unaffected."""
        msg = ToolMessage(
            content=[TextPart(text="[ref1] (image)")],
            tool_call_id="tc1",
        )
        result = convert_message_to_langchain(msg, strip_tool_images=False)
        assert isinstance(result.content, list)

    def test_convert_messages_to_langchain_threads_strip_tool_images(self) -> None:
        msg = ToolMessage(
            content=[
                TextPart(text="text"),
                ImagePart(source=ImageSource(type="url", data="https://x/y.png")),
            ],
            tool_call_id="tc1",
        )
        converted = convert_messages_to_langchain([msg], strip_tool_images=True)
        assert isinstance(converted[0].content, str)

    def test_plain_string_tool_message_still_serializes_as_string(self) -> None:
        """Backward compatibility: the overwhelmingly common str-content
        case must keep producing plain string LangChain content, not a
        single-item list."""
        msg = ToolMessage(content="result text", tool_call_id="tc1", step_footer=" [footer]")
        result = convert_message_to_langchain(msg)
        assert result.content == "result text [footer]"


class TestToolMessageFromLangchain:
    def test_multipart_content_round_trips_to_parts(self) -> None:
        lc_msg = LCToolMessage(
            content=[
                {"type": "text", "text": "a description"},
                {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
            ],
            tool_call_id="tc1",
        )
        result = convert_message_from_langchain(lc_msg)
        assert isinstance(result, ToolMessage)
        assert isinstance(result.content, list)
        assert result.content[0] == TextPart(text="a description")
        assert result.content[1] == ImagePart(source=ImageSource(type="url", data="https://example.com/img.png"))
        assert result.text == "a description"

    def test_plain_string_content_stays_a_string(self) -> None:
        lc_msg = LCToolMessage(content="plain result", tool_call_id="tc1")
        result = convert_message_from_langchain(lc_msg)
        assert result.content == "plain result"

    def test_error_status_round_trips(self) -> None:
        lc_msg = LCToolMessage(content="boom", tool_call_id="tc1", status="error")
        result = convert_message_from_langchain(lc_msg)
        assert result.is_error is True


class TestAssistantMessageFromLangchain:
    def test_plain_text_response(self) -> None:
        ai_message = AIMessage(content="The answer is 42.")
        result = convert_assistant_message_from_langchain(ai_message)
        assert result.text == "The answer is 42."
        assert result.tool_calls is None
        assert result.truncated is False

    def test_response_with_tool_calls(self) -> None:
        ai_message = AIMessage(
            content="",
            tool_calls=[{"name": "search", "args": {"q": "x"}, "id": "call_1"}],
        )
        result = convert_assistant_message_from_langchain(ai_message)
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "search"
        assert result.tool_calls[0].arguments == {"q": "x"}
        assert result.tool_calls[0].id == "call_1"

    def test_truncated_response(self) -> None:
        ai_message = AIMessage(content="cut off", response_metadata={"finish_reason": "length"})
        result = convert_assistant_message_from_langchain(ai_message)
        assert result.truncated is True

    def test_truncated_response_from_responses_api_incomplete_details(self) -> None:
        """OpenAI's Responses API never sets `finish_reason` — it reports a
        cut-off answer as `status="incomplete"` plus `incomplete_details`."""
        ai_message = AIMessage(
            content=[{"type": "text", "text": "cut off", "annotations": []}],
            response_metadata={
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
            },
        )
        result = convert_assistant_message_from_langchain(ai_message)
        assert result.truncated is True

    def test_completed_responses_api_answer_is_not_truncated(self) -> None:
        ai_message = AIMessage(
            content=[{"type": "text", "text": "all done", "annotations": []}],
            response_metadata={"status": "completed", "incomplete_details": None},
        )
        result = convert_assistant_message_from_langchain(ai_message)
        assert result.truncated is False

    def test_content_filter_incomplete_is_not_truncation(self) -> None:
        """`incomplete_details.reason` also carries non-length stops, which
        must not be mistaken for hitting the output-token cap."""
        ai_message = AIMessage(
            content=[{"type": "text", "text": "", "annotations": []}],
            response_metadata={
                "status": "incomplete",
                "incomplete_details": {"reason": "content_filter"},
            },
        )
        result = convert_assistant_message_from_langchain(ai_message)
        assert result.truncated is False

    def test_tool_call_round_trip(self) -> None:
        call = convert_tool_call_from_langchain({"name": "foo", "args": {"a": 1}, "id": "x1"})
        assert call.name == "foo"
        assert call.arguments == {"a": 1}
        assert call.id == "x1"

    def test_tool_call_missing_id_defaults_empty(self) -> None:
        call = convert_tool_call_from_langchain({"name": "foo", "args": {}})
        assert call.id == ""

    def test_tool_call_missing_name_defaults_to_unknown_tool(self) -> None:
        """A malformed provider dict without a `name` key must not crash
        the turn with a `KeyError` — matches `_recover_invalid_tool_call`'s
        sentinel fallback for the same field."""
        call = convert_tool_call_from_langchain({"args": {}, "id": "x1"})
        assert call.name == "unknown_tool"

    def test_overlong_name_is_clamped_to_provider_limit(self) -> None:
        """A name over OpenAI's 128-char function-name cap must never enter
        `AssistantMessage.tool_calls` unclamped — see `_clamp_tool_call_name`'s
        docstring for why an unclamped name left in message history can grow
        across turns and get an entire later request rejected outright."""
        overlong = "knowledgegraph__search" * 10  # 220 chars
        call = convert_tool_call_from_langchain({"name": overlong, "args": {}, "id": "x1"})
        assert len(call.name) == 128

    def test_overlong_name_clamping_does_not_collide_with_a_real_tool_name(self) -> None:
        """A plain `name[:128]` prefix could coincidentally equal a real,
        shorter registered tool name — turning an invalid/hallucinated call
        into a DIFFERENT tool actually executing instead of failing with
        "unknown tool". The clamped name must not be a plain prefix of the
        original."""
        overlong = "knowledgegraph__search" * 10  # 220 chars
        call = convert_tool_call_from_langchain({"name": overlong, "args": {}, "id": "x1"})
        assert call.name != overlong[:128]
        assert not overlong.startswith(call.name)

    def test_name_at_or_under_limit_is_untouched(self) -> None:
        call = convert_tool_call_from_langchain({"name": "a" * 128, "args": {}})
        assert call.name == "a" * 128


class TestInvalidToolCallRecovery:
    """`AIMessage.invalid_tool_calls` must never be silently dropped — a
    dropped call makes the turn look exactly like a plain no-tool-call
    response, letting a weak model "finish" without ever having invoked
    the tool it clearly meant to call. See `_recover_invalid_tool_call`."""

    def test_repairable_markdown_fence_is_recovered_as_a_normal_call(self) -> None:
        ai_message = AIMessage(
            content="",
            invalid_tool_calls=[{
                "name": "run_code",
                "args": '```json\n{"code": "print(1)", "language": "python"}\n```',
                "id": "call_1",
                "error": "invalid json",
            }],
        )
        result = convert_assistant_message_from_langchain(ai_message)

        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        call = result.tool_calls[0]
        assert call.name == "run_code"
        assert call.arguments == {"code": "print(1)", "language": "python"}
        assert MALFORMED_TOOL_CALL_ARGS_KEY not in call.arguments

    def test_repairable_trailing_comma_is_recovered(self) -> None:
        ai_message = AIMessage(
            content="",
            invalid_tool_calls=[{
                "name": "run_code",
                "args": '{"code": "print(1)",}',
                "id": "call_1",
                "error": "invalid json",
            }],
        )
        result = convert_assistant_message_from_langchain(ai_message)

        assert result.tool_calls[0].arguments == {"code": "print(1)"}

    def test_unrepairable_json_becomes_sentinel_call_not_dropped(self) -> None:
        ai_message = AIMessage(
            content="",
            invalid_tool_calls=[{
                "name": "run_code",
                "args": '{"code": "print(1)"  NOT VALID JSON AT ALL',
                "id": "call_1",
                "error": "invalid json",
            }],
        )
        result = convert_assistant_message_from_langchain(ai_message)

        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        call = result.tool_calls[0]
        assert call.name == "run_code"
        assert call.id == "call_1"
        assert MALFORMED_TOOL_CALL_ARGS_KEY in call.arguments
        assert MALFORMED_TOOL_CALL_ERROR_KEY in call.arguments

    def test_overlong_name_is_clamped_here_too(self) -> None:
        ai_message = AIMessage(
            content="",
            invalid_tool_calls=[{
                "name": "run_code" * 20,
                "args": "not json",
                "id": "call_1",
                "error": "invalid json",
            }],
        )
        result = convert_assistant_message_from_langchain(ai_message)
        assert len(result.tool_calls[0].name) == 128

    def test_missing_name_defaults_to_unknown_tool(self) -> None:
        ai_message = AIMessage(
            content="",
            invalid_tool_calls=[{"name": None, "args": "not json", "id": "call_1"}],
        )
        result = convert_assistant_message_from_langchain(ai_message)
        assert result.tool_calls[0].name == "unknown_tool"

    def test_combines_with_valid_tool_calls_on_the_same_response(self) -> None:
        ai_message = AIMessage(
            content="",
            tool_calls=[{"name": "search", "args": {"q": "x"}, "id": "call_ok"}],
            invalid_tool_calls=[{"name": "run_code", "args": "{bad", "id": "call_bad", "error": "e"}],
        )
        result = convert_assistant_message_from_langchain(ai_message)

        assert result.tool_calls is not None
        assert len(result.tool_calls) == 2
        names = {c.name for c in result.tool_calls}
        assert names == {"search", "run_code"}

    def test_no_invalid_tool_calls_leaves_behavior_unchanged(self) -> None:
        ai_message = AIMessage(content="just text")
        result = convert_assistant_message_from_langchain(ai_message)
        assert result.tool_calls is None


class TestTokenUsage:
    def test_missing_usage_metadata_defaults_to_zero(self) -> None:
        ai_message = AIMessage(content="hi")
        usage = token_usage_from_ai_message(ai_message)
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0

    def test_usage_metadata_extracted(self) -> None:
        ai_message = AIMessage(
            content="hi",
            usage_metadata={
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "input_token_details": {"cache_read": 2, "cache_creation": 1},
            },
        )
        usage = token_usage_from_ai_message(ai_message)
        assert usage.input_tokens == 10
        assert usage.output_tokens == 5
        assert usage.cache_read_tokens == 2
        assert usage.cache_write_tokens == 1


class TestConvertToolSchemaToLangchainDict:
    """`convert_tool_schema_to_langchain_dict` replaced a round-trip through
    a dynamically-built Pydantic model (`StructuredTool.args_schema`) with a
    direct OpenAI function-calling dict, matching `openai.py::_format_tools`
    exactly and passing `ToolSchema.input_schema` through unmodified —
    `bind_tools()` accepts this dict shape on every LangChain chat model
    integration in use (see the function's own docstring)."""

    def test_builds_openai_function_dict_verbatim(self) -> None:
        input_schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "search text"},
                "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 100},
                "expand": {"type": "string", "enum": ["summary", "status"]},
            },
            "required": ["query"],
        }
        schema = ToolSchema(
            name="jira_search_issues",
            description="Search Jira issues",
            input_schema=input_schema,
        )
        result = convert_tool_schema_to_langchain_dict(schema)

        assert result == {
            "type": "function",
            "function": {
                "name": "jira_search_issues",
                "description": "Search Jira issues",
                "parameters": input_schema,
            },
        }

    def test_empty_input_schema_defaults_to_empty_object(self) -> None:
        schema = ToolSchema(name="t", description="d", input_schema={})
        result = convert_tool_schema_to_langchain_dict(schema)
        assert result["function"]["parameters"] == {"type": "object", "properties": {}}

    def test_strips_schema_meta_keywords_but_keeps_everything_else(self) -> None:
        """`$schema`/`$id` (and any leftover `$ref`) are stripped because they
        carry no meaning in a function-calling `parameters` schema, but
        `enum`/`default`/`additionalProperties` must survive — those are
        exactly what the previous Pydantic round-trip dropped, and every
        provider either honors them or ignores them harmlessly."""
        schema = ToolSchema(
            name="t",
            description="d",
            input_schema={
                "$schema": "http://json-schema.org/draft-07/schema#",
                "$id": "https://example.com/schema.json",
                "type": "object",
                "properties": {
                    "opts": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                },
            },
        )
        result = convert_tool_schema_to_langchain_dict(schema)
        parameters = result["function"]["parameters"]
        assert "$schema" not in parameters
        assert "$id" not in parameters
        assert parameters["properties"]["opts"]["additionalProperties"] == {"type": "string"}

    def test_convert_tool_schemas_empty_list(self) -> None:
        assert convert_tool_schemas_to_langchain(None) == []
        assert convert_tool_schemas_to_langchain([]) == []

    def test_convert_tool_schemas_matches_openai_format_tools_shape(self) -> None:
        """Parity guard: the dict shape this emits for `bind_tools()` must be
        indistinguishable from `OpenAITransport._format_tools`'s own output
        for the same `ToolSchema`, since that's the shape every LangChain
        chat model integration in this app already accepts."""
        from app.agent_loop_lib.transport.openai import OpenAITransport

        schema = ToolSchema(
            name="search",
            description="search",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
        )
        openai_transport = OpenAITransport(api_key="test-key")
        lc_dicts = convert_tool_schemas_to_langchain([schema])
        openai_dicts = openai_transport._format_tools([schema])
        assert lc_dicts == openai_dicts

class TestClampToolCallId:
    """The OpenAI/Azure API hard-rejects `tool_calls[].id` longer than 64
    chars. `_clamp_tool_call_id` shortens them deterministically so the
    assistant message and its matching tool-result message always agree."""

    def test_short_id_passes_through(self) -> None:
        assert _clamp_tool_call_id("call_abc") == "call_abc"

    def test_exactly_64_passes_through(self) -> None:
        id_64 = "x" * 64
        assert _clamp_tool_call_id(id_64) == id_64

    def test_65_chars_is_shortened(self) -> None:
        id_65 = "x" * 65
        result = _clamp_tool_call_id(id_65)
        assert len(result) == 64

    def test_deterministic(self) -> None:
        long = "call_" + "z" * 100
        assert _clamp_tool_call_id(long) == _clamp_tool_call_id(long)

    def test_different_long_ids_produce_different_clamped_values(self) -> None:
        a = "call_" + "a" * 100
        b = "call_" + "b" * 100
        assert _clamp_tool_call_id(a) != _clamp_tool_call_id(b)


class TestOutputSchemaToPydanticModel:
    def test_output_schema_to_pydantic_model_handles_nested_object(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "route": {"type": "string", "enum": ["a", "b"]},
                "meta": {
                    "type": "object",
                    "properties": {"count": {"type": "integer"}},
                },
            },
            "required": ["route"],
        }
        model = output_schema_to_pydantic_model(schema)
        instance = model(route="a", meta={"count": 3})
        assert instance.route == "a"
        assert instance.meta.count == 3

    def test_typed_enum_rejects_a_value_outside_the_enum(self) -> None:
        """`{"type": "string", "enum": [...]}` must keep BOTH constraints. A
        bare `Any` (or the primitive type alone) lets a provider's structured
        response put an object — or an unlisted string — in the field, and
        this model is exactly what `complete_structured` validates against."""
        model = output_schema_to_pydantic_model({
            "type": "object",
            "properties": {"route": {"type": "string", "enum": ["a", "b"]}},
            "required": ["route"],
        })

        assert model(route="b").route == "b"
        with pytest.raises(ValidationError):
            model(route={"invalid": "shape"})
        with pytest.raises(ValidationError):
            model(route="not_in_enum")

    def test_enum_with_non_literal_members_falls_back_to_the_declared_type(self) -> None:
        """JSON Schema allows an object as an `enum` member, which `Literal`
        can't hold — the declared primitive type is still enforced rather
        than degrading the field to `Any`."""
        model = output_schema_to_pydantic_model({
            "type": "object",
            "properties": {"shape": {"type": "string", "enum": [{"a": 1}, {"b": 2}]}},
            "required": ["shape"],
        })

        assert model(shape="anything").shape == "anything"
        with pytest.raises(ValidationError):
            model(shape={"a": 1})

    def test_list_valued_type_does_not_raise(self) -> None:
        """A list-valued `type` is unhashable, so looking it up in
        `_JSON_SCHEMA_TYPE_MAP` without an `isinstance` guard raised
        `TypeError` from inside the model build."""
        model = output_schema_to_pydantic_model({
            "type": "object",
            "properties": {"note": {"type": ["string", "null"]}},
            "required": [],
        })

        assert model(note="x").note == "x"
