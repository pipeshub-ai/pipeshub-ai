"""Message/tool-schema round-trip conversion between LangChain and
agent-loop's provider-agnostic types (`app/agents/agent_loop/converters.py`)."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.messages import ToolMessage as LCToolMessage

from app.agent_loop_lib.core.messages import (
    AssistantMessage,
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
    convert_assistant_message_from_langchain,
    convert_message_to_langchain,
    convert_messages_to_langchain,
    convert_tool_call_from_langchain,
    convert_tool_schema_to_langchain,
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

    def test_tool_call_round_trip(self) -> None:
        call = convert_tool_call_from_langchain({"name": "foo", "args": {"a": 1}, "id": "x1"})
        assert call.name == "foo"
        assert call.arguments == {"a": 1}
        assert call.id == "x1"

    def test_tool_call_missing_id_defaults_empty(self) -> None:
        call = convert_tool_call_from_langchain({"name": "foo", "args": {}})
        assert call.id == ""


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


class TestToolSchemaConversion:
    def test_convert_tool_schema_to_langchain_builds_structured_tool(self) -> None:
        schema = ToolSchema(
            name="jira_search_issues",
            description="Search Jira issues",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "search text"},
                    "limit": {"type": "integer", "description": "max results"},
                },
                "required": ["query"],
            },
        )
        structured_tool = convert_tool_schema_to_langchain(schema)
        assert structured_tool.name == "jira_search_issues"
        assert structured_tool.description == "Search Jira issues"
        fields = structured_tool.args_schema.model_fields
        assert "query" in fields
        assert "limit" in fields

    def test_convert_tool_schema_coroutine_is_unbound(self) -> None:
        schema = ToolSchema(name="t", description="d", input_schema={"type": "object", "properties": {}})
        structured_tool = convert_tool_schema_to_langchain(schema)
        import asyncio

        with pytest.raises(RuntimeError):
            asyncio.run(structured_tool.coroutine())

    def test_convert_tool_schemas_empty_list(self) -> None:
        assert convert_tool_schemas_to_langchain(None) == []
        assert convert_tool_schemas_to_langchain([]) == []

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
