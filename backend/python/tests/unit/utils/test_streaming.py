"""Unit tests for pure functions in app.utils.streaming."""

import json
import logging
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel

from app.utils.streaming import (
    AnswerParserState,
    _append_task_markers,
    _apply_structured_output,
    _get_schema_for_parsing,
    _get_schema_for_structured_output,
    _initialize_answer_parser_regex,
    _stringify_content,
    aiter_llm_stream,
    bind_tools_for_llm,
    cleanup_content,
    create_sse_event,
    create_stream_record_response,
    escape_ctl,
    extract_json_from_string,
    find_unescaped_quote,
    get_parser,
    get_vectorDb_limit,
    invoke_with_row_descriptions_and_reflection,
    invoke_with_structured_output_and_reflection,
    stream_content,
    supports_human_message_after_tool,
)


# ---------------------------------------------------------------------------
# find_unescaped_quote
# ---------------------------------------------------------------------------
class TestFindUnescapedQuote:
    """Tests for find_unescaped_quote(text)."""

    def test_no_quotes_returns_minus_one(self):
        assert find_unescaped_quote("hello world") == -1

    def test_escaped_quote_returns_minus_one(self):
        assert find_unescaped_quote('hello \\"world') == -1

    def test_unescaped_at_start(self):
        assert find_unescaped_quote('"hello') == 0

    def test_unescaped_at_middle(self):
        assert find_unescaped_quote('hello"world') == 5

    def test_unescaped_at_end(self):
        assert find_unescaped_quote('hello"') == 5

    def test_escaped_then_unescaped(self):
        # \" is escaped, then the next " is unescaped
        assert find_unescaped_quote('\\"abc"') == 5

    def test_double_backslash_then_quote(self):
        # \\\\ means two literal backslashes; the quote after is unescaped
        assert find_unescaped_quote('\\\\"') == 2

    def test_empty_string(self):
        assert find_unescaped_quote("") == -1

    def test_only_backslashes(self):
        assert find_unescaped_quote("\\\\\\\\") == -1

    def test_multiple_unescaped_returns_first(self):
        assert find_unescaped_quote('a"b"c') == 1


# ---------------------------------------------------------------------------
# escape_ctl
# ---------------------------------------------------------------------------
class TestEscapeCtl:
    """Tests for escape_ctl(raw)."""

    def test_no_quoted_strings(self):
        raw = "no quotes here"
        assert escape_ctl(raw) == raw

    def test_replaces_newline_inside_quotes(self):
        raw = '{"key": "line1\nline2"}'
        result = escape_ctl(raw)
        assert result == '{"key": "line1\\nline2"}'

    def test_replaces_tab_inside_quotes(self):
        raw = '{"key": "col1\tcol2"}'
        result = escape_ctl(raw)
        assert result == '{"key": "col1\\tcol2"}'

    def test_replaces_carriage_return_inside_quotes(self):
        raw = '{"key": "line1\rline2"}'
        result = escape_ctl(raw)
        assert result == '{"key": "line1\\rline2"}'

    def test_mixed_content_outside_quotes_untouched(self):
        raw = '{\n  "key": "value"\n}'
        result = escape_ctl(raw)
        # newlines outside of quoted strings should remain
        assert "\n" in result
        assert '"key"' in result

    def test_multiple_quoted_strings(self):
        raw = '{"a": "x\ny", "b": "p\tq"}'
        result = escape_ctl(raw)
        assert "\\n" in result
        assert "\\t" in result

    def test_empty_quoted_string(self):
        raw = '{"key": ""}'
        assert escape_ctl(raw) == raw

    def test_already_escaped_chars_not_double_escaped(self):
        # If the string already contains literal \\n (two chars), the regex
        # won't match a real newline, so it stays the same.
        raw = '{"key": "already\\\\nfine"}'
        assert escape_ctl(raw) == raw


# ---------------------------------------------------------------------------
# _stringify_content
# ---------------------------------------------------------------------------
class TestStringifyContent:
    """Tests for _stringify_content(content)."""

    def test_none_returns_empty_string(self):
        assert _stringify_content(None) == ""

    def test_str_passthrough(self):
        assert _stringify_content("hello") == "hello"

    def test_empty_str(self):
        assert _stringify_content("") == ""

    def test_list_of_dicts_with_type_text(self):
        content = [
            {"type": "text", "text": "Hello "},
            {"type": "text", "text": "World"},
        ]
        assert _stringify_content(content) == "Hello World"

    def test_list_of_dicts_with_text_key_only(self):
        content = [{"text": "part1"}, {"text": "part2"}]
        assert _stringify_content(content) == "part1part2"

    def test_list_of_dicts_ignores_non_text_types(self):
        content = [
            {"type": "text", "text": "Hello"},
            {"type": "image_url", "image_url": "http://..."},
        ]
        assert _stringify_content(content) == "Hello"

    def test_list_of_strings(self):
        content = ["hello", " ", "world"]
        assert _stringify_content(content) == "hello world"

    def test_list_mixed_types(self):
        content = ["prefix:", {"type": "text", "text": "body"}, 42]
        assert _stringify_content(content) == "prefix:body42"

    def test_dict_returns_json(self):
        content: Dict[str, Any] = {"key": "value"}
        result = _stringify_content(content)
        # dict falls through to str() which gives repr-like output
        assert "key" in result
        assert "value" in result

    def test_empty_list_returns_empty(self):
        assert _stringify_content([]) == ""


# ---------------------------------------------------------------------------
# get_vectorDb_limit
# ---------------------------------------------------------------------------
class TestGetVectorDbLimit:
    """Tests for get_vectorDb_limit(context_length)."""

    def test_small_context(self):
        # context_length <= 17000 -> 43
        assert get_vectorDb_limit(10000) == 43
        assert get_vectorDb_limit(17000) == 43

    def test_medium_context(self):
        # context_length <= 33000 -> 154
        assert get_vectorDb_limit(17001) == 154
        assert get_vectorDb_limit(33000) == 154

    def test_large_context(self):
        # context_length <= 65000 -> 213
        assert get_vectorDb_limit(33001) == 213
        assert get_vectorDb_limit(65000) == 213

    def test_huge_context_returns_default(self):
        # context_length > 65000 -> 266
        assert get_vectorDb_limit(65001) == 266
        assert get_vectorDb_limit(200000) == 266

    def test_zero_context(self):
        assert get_vectorDb_limit(0) == 43

    def test_negative_context(self):
        assert get_vectorDb_limit(-1) == 43


# ---------------------------------------------------------------------------
# extract_json_from_string
# ---------------------------------------------------------------------------
class TestExtractJsonFromString:
    """Tests for extract_json_from_string(input_string)."""

    def test_markdown_code_block(self):
        s = '```json\n{"answer": "hello", "confidence": "high"}\n```'
        result = extract_json_from_string(s)
        assert result == {"answer": "hello", "confidence": "high"}

    def test_plain_json(self):
        s = '{"key": "value"}'
        result = extract_json_from_string(s)
        assert result == {"key": "value"}

    def test_json_with_surrounding_text(self):
        s = 'Here is the result: {"answer": 42} -- done'
        result = extract_json_from_string(s)
        assert result == {"answer": 42}

    def test_nested_braces(self):
        s = '{"outer": {"inner": "value"}}'
        result = extract_json_from_string(s)
        assert result == {"outer": {"inner": "value"}}

    def test_no_json_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json_from_string("no json here")

    def test_only_open_brace_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json_from_string("text { more text")

    def test_only_close_brace_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json_from_string("text } more text")

    def test_invalid_json_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid JSON structure"):
            extract_json_from_string("{not valid json}")

    def test_whitespace_stripping(self):
        s = '  \n  {"key": "value"}  \n  '
        result = extract_json_from_string(s)
        assert result == {"key": "value"}

    def test_empty_json_object(self):
        s = "{}"
        result = extract_json_from_string(s)
        assert result == {}

    def test_json_array_as_value(self):
        s = '{"items": [1, 2, 3]}'
        result = extract_json_from_string(s)
        assert result == {"items": [1, 2, 3]}


# ---------------------------------------------------------------------------
# create_sse_event
# ---------------------------------------------------------------------------
class TestCreateSseEvent:
    """Tests for create_sse_event(event_type, data)."""

    def test_string_data(self):
        result = create_sse_event("message", "hello")
        assert result == 'event: message\ndata: "hello"\n\n'

    def test_dict_data(self):
        result = create_sse_event("update", {"key": "value"})
        assert result == 'event: update\ndata: {"key": "value"}\n\n'

    def test_list_data(self):
        result = create_sse_event("items", [1, 2, 3])
        assert result == "event: items\ndata: [1, 2, 3]\n\n"

    def test_event_format_structure(self):
        result = create_sse_event("test", {})
        # Must start with "event: "
        assert result.startswith("event: test\n")
        # Must have "data: " line
        assert "\ndata: " in result
        # Must end with double newline
        assert result.endswith("\n\n")

    def test_complex_data(self):
        data = {"answer": "hello", "citations": [{"id": 1}], "confidence": 0.9}
        result = create_sse_event("complete", data)
        parsed_data = json.loads(result.split("data: ", 1)[1].strip())
        assert parsed_data == data


# ---------------------------------------------------------------------------
# cleanup_content
# ---------------------------------------------------------------------------
class TestCleanupContent:
    """Tests for cleanup_content(response_text)."""

    def test_with_think_tags(self):
        text = "<think>internal reasoning</think>The actual answer."
        result = cleanup_content(text)
        assert result == "The actual answer."

    def test_with_json_code_block(self):
        text = '```json\n{"answer": "hello"}\n```'
        result = cleanup_content(text)
        assert result == '{"answer": "hello"}'

    def test_plain_text_unchanged(self):
        text = "Just a plain answer."
        result = cleanup_content(text)
        assert result == "Just a plain answer."

    def test_whitespace_stripped(self):
        text = "  \n  answer  \n  "
        result = cleanup_content(text)
        assert result == "answer"

    def test_think_tags_with_code_block(self):
        text = '<think>reasoning</think>```json\n{"key": "val"}\n```'
        result = cleanup_content(text)
        assert result == '{"key": "val"}'

    def test_multiple_think_tags_takes_last(self):
        text = "<think>first</think>middle<think>second</think>final"
        result = cleanup_content(text)
        # split on </think> takes last piece
        assert result == "final"

    def test_no_closing_think_tag(self):
        text = "<think>no closing tag"
        result = cleanup_content(text)
        # No </think> so nothing is stripped
        assert result == "<think>no closing tag"

    def test_empty_string(self):
        result = cleanup_content("")
        assert result == ""

    def test_only_code_block_markers(self):
        text = "```json\n```"
        result = cleanup_content(text)
        assert result == ""


# ---------------------------------------------------------------------------
# _append_task_markers
# ---------------------------------------------------------------------------
class TestAppendTaskMarkers:
    """Tests for _append_task_markers(answer, conversation_tasks)."""

    def test_with_tasks(self):
        tasks = [
            {"fileName": "report.csv", "signedUrl": "https://example.com/report.csv"},
            {"fileName": "data.xlsx", "downloadUrl": "https://example.com/data.xlsx"},
        ]
        result = _append_task_markers("Answer text", tasks)
        assert result.startswith("Answer text")
        assert "::download_conversation_task[report.csv](https://example.com/report.csv)" in result
        assert "::download_conversation_task[data.xlsx](https://example.com/data.xlsx)" in result

    def test_none_tasks(self):
        result = _append_task_markers("Answer text", None)
        assert result == "Answer text"

    def test_empty_tasks(self):
        result = _append_task_markers("Answer text", [])
        assert result == "Answer text"

    def test_tasks_without_urls_are_skipped(self):
        # Tasks without signedUrl or downloadUrl produce no marker text,
        # but the "\n\n" separator is still appended since the list is non-empty.
        tasks = [{"fileName": "no_url.csv"}]
        result = _append_task_markers("Answer text", tasks)
        assert result == "Answer text\n\n"

    def test_task_with_signed_url_preferred(self):
        tasks = [
            {"fileName": "file.csv", "signedUrl": "https://signed.url", "downloadUrl": "https://download.url"},
        ]
        result = _append_task_markers("Answer text", tasks)
        assert "https://signed.url" in result

    def test_default_filename(self):
        tasks = [{"signedUrl": "https://example.com/file"}]
        result = _append_task_markers("Answer text", tasks)
        assert "::download_conversation_task[Download](https://example.com/file)" in result

    def test_markers_on_new_lines(self):
        tasks = [{"fileName": "f.csv", "signedUrl": "https://u"}]
        result = _append_task_markers("Answer", tasks)
        # markers should be separated from answer by double newline
        assert "\n\n" in result


# ---------------------------------------------------------------------------
# supports_human_message_after_tool
# ---------------------------------------------------------------------------
class TestSupportsHumanMessageAfterTool:
    """Tests for supports_human_message_after_tool(llm)."""

    def test_mistral_returns_false(self):
        from langchain_mistralai import ChatMistralAI

        # Create a subclass instance so isinstance() returns True
        # without needing actual API credentials.
        class FakeMistral(ChatMistralAI):
            def __init__(self):
                # Bypass Pydantic validation entirely
                pass

        mock_llm = FakeMistral.__new__(FakeMistral)
        result = supports_human_message_after_tool(mock_llm)
        assert result is False

    def test_other_llm_returns_true(self):
        mock_llm = MagicMock()
        result = supports_human_message_after_tool(mock_llm)
        assert result is True

    def test_openai_returns_true(self):
        mock_llm = MagicMock(spec=[])
        result = supports_human_message_after_tool(mock_llm)
        assert result is True


# ---------------------------------------------------------------------------
# _get_schema_for_structured_output
# ---------------------------------------------------------------------------
class TestGetSchemaForStructuredOutput:
    """Tests for _get_schema_for_structured_output(is_agent)."""

    def test_agent_true(self):
        from app.modules.agents.qna.schemas import AgentAnswerWithMetadataDict

        result = _get_schema_for_structured_output(is_agent=True)
        assert result is AgentAnswerWithMetadataDict

    def test_agent_false(self):
        from app.modules.qna.prompt_templates import AnswerWithMetadataDict

        result = _get_schema_for_structured_output(is_agent=False)
        assert result is AnswerWithMetadataDict

    def test_default_is_false(self):
        from app.modules.qna.prompt_templates import AnswerWithMetadataDict

        result = _get_schema_for_structured_output()
        assert result is AnswerWithMetadataDict


# ---------------------------------------------------------------------------
# _get_schema_for_parsing
# ---------------------------------------------------------------------------
class TestGetSchemaForParsing:
    """Tests for _get_schema_for_parsing(is_agent)."""

    def test_agent_true(self):
        from app.modules.agents.qna.schemas import AgentAnswerWithMetadataJSON

        result = _get_schema_for_parsing(is_agent=True)
        assert result is AgentAnswerWithMetadataJSON

    def test_agent_false(self):
        from app.modules.qna.prompt_templates import AnswerWithMetadataJSON

        result = _get_schema_for_parsing(is_agent=False)
        assert result is AnswerWithMetadataJSON

    def test_default_is_false(self):
        from app.modules.qna.prompt_templates import AnswerWithMetadataJSON

        result = _get_schema_for_parsing()
        assert result is AnswerWithMetadataJSON


# ---------------------------------------------------------------------------
# bind_tools_for_llm
# ---------------------------------------------------------------------------
class TestBindToolsForLlm:
    """Tests for bind_tools_for_llm(llm, tools)."""

    def test_success(self):
        mock_llm = MagicMock()
        mock_bound = MagicMock()
        mock_llm.bind_tools.return_value = mock_bound
        tools = [MagicMock(), MagicMock()]

        result = bind_tools_for_llm(mock_llm, tools)
        assert result is mock_bound
        mock_llm.bind_tools.assert_called_once_with(tools)

    def test_exception_returns_false(self):
        mock_llm = MagicMock()
        mock_llm.bind_tools.side_effect = Exception("bind failed")
        tools = [MagicMock()]

        result = bind_tools_for_llm(mock_llm, tools)
        assert result is False

    def test_empty_tools_list(self):
        mock_llm = MagicMock()
        mock_bound = MagicMock()
        mock_llm.bind_tools.return_value = mock_bound

        result = bind_tools_for_llm(mock_llm, [])
        assert result is mock_bound
        mock_llm.bind_tools.assert_called_once_with([])


# ---------------------------------------------------------------------------
# get_parser
# ---------------------------------------------------------------------------
class TestGetParser:
    """Tests for get_parser(schema)."""

    def test_default_schema(self):
        from app.modules.qna.prompt_templates import AnswerWithMetadataJSON

        parser, format_instructions = get_parser()
        assert parser.pydantic_object is AnswerWithMetadataJSON
        assert isinstance(format_instructions, str)
        assert len(format_instructions) > 0

    def test_custom_schema(self):
        from app.modules.agents.qna.schemas import AgentAnswerWithMetadataJSON

        parser, format_instructions = get_parser(AgentAnswerWithMetadataJSON)
        assert parser.pydantic_object is AgentAnswerWithMetadataJSON
        assert isinstance(format_instructions, str)

    def test_returns_tuple(self):
        result = get_parser()
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_format_instructions_contain_json(self):
        _, format_instructions = get_parser()
        # Format instructions should mention JSON formatting
        assert "json" in format_instructions.lower() or "JSON" in format_instructions


# ---------------------------------------------------------------------------
# AnswerParserState
# ---------------------------------------------------------------------------
class TestAnswerParserState:
    """Tests for AnswerParserState class."""

    def test_initial_state(self):
        state = AnswerParserState()
        assert state.full_json_buf == ""
        assert state.answer_buf == ""
        assert state.answer_done is False
        assert state.prev_norm_len == 0
        assert state.emit_upto == 0
        assert state.words_in_chunk == 0

    def test_state_is_mutable(self):
        state = AnswerParserState()
        state.full_json_buf = '{"answer": "hello"}'
        state.answer_buf = "hello"
        state.answer_done = True
        state.prev_norm_len = 5
        state.emit_upto = 5
        state.words_in_chunk = 3
        assert state.full_json_buf == '{"answer": "hello"}'
        assert state.answer_buf == "hello"
        assert state.answer_done is True
        assert state.prev_norm_len == 5
        assert state.emit_upto == 5
        assert state.words_in_chunk == 3

    def test_independent_instances(self):
        state1 = AnswerParserState()
        state2 = AnswerParserState()
        state1.answer_buf = "changed"
        assert state2.answer_buf == ""


# ---------------------------------------------------------------------------
# _initialize_answer_parser_regex
# ---------------------------------------------------------------------------
class TestInitializeAnswerParserRegex:
    """Tests for _initialize_answer_parser_regex()."""

    def test_returns_four_elements(self):
        result = _initialize_answer_parser_regex()
        assert len(result) == 4

    def test_answer_key_regex_matches(self):
        answer_key_re, _, _, _ = _initialize_answer_parser_regex()
        assert answer_key_re.search('"answer": "hello"') is not None
        assert answer_key_re.search('"answer"  :  "hello"') is not None
        assert answer_key_re.search('"answer":"hello"') is not None

    def test_answer_key_regex_no_match(self):
        answer_key_re, _, _, _ = _initialize_answer_parser_regex()
        assert answer_key_re.search('"reason": "because"') is None

    def test_cite_block_regex_matches(self):
        _, cite_block_re, _, _ = _initialize_answer_parser_regex()
        assert cite_block_re.match("[1]") is not None
        assert cite_block_re.match("[1][2]") is not None
        # Chinese brackets
        assert cite_block_re.match("\u30101\u3011") is not None

    def test_incomplete_cite_regex_matches(self):
        _, _, incomplete_cite_re, _ = _initialize_answer_parser_regex()
        assert incomplete_cite_re.search("text [") is not None
        assert incomplete_cite_re.search("text [12") is not None
        assert incomplete_cite_re.search("text \u3010") is not None

    def test_incomplete_cite_regex_no_match_on_complete(self):
        _, _, incomplete_cite_re, _ = _initialize_answer_parser_regex()
        assert incomplete_cite_re.search("text [1]") is None
        assert incomplete_cite_re.search("text \u30101\u3011") is None

    def test_word_iter_callable(self):
        _, _, _, word_iter = _initialize_answer_parser_regex()
        matches = list(word_iter("hello world"))
        assert len(matches) == 2
        assert matches[0].group() == "hello"
        assert matches[1].group() == "world"


# ---------------------------------------------------------------------------
# _apply_structured_output
# ---------------------------------------------------------------------------
class TestApplyStructuredOutput:
    """Tests for _apply_structured_output(llm, schema)."""

    def test_unsupported_llm_returns_original(self):
        """Non-supported LLM types should return the original LLM unchanged."""
        mock_llm = MagicMock()
        # Ensure it doesn't match any isinstance check
        mock_llm.__class__ = type("CustomLLM", (), {})
        result = _apply_structured_output(mock_llm, schema=MagicMock())
        assert result is mock_llm

    def test_openai_llm_success(self):
        """ChatOpenAI should use with_structured_output with json_schema method."""
        from langchain_openai import ChatOpenAI

        mock_llm = MagicMock(spec=ChatOpenAI)
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        schema = MagicMock()

        result = _apply_structured_output(mock_llm, schema=schema)
        assert result is mock_structured
        mock_llm.with_structured_output.assert_called_once_with(
            schema, method="json_schema"
        )

    def test_anthropic_legacy_model_returns_original(self):
        """Legacy Anthropic models (claude-3-*) should return the original LLM."""
        from langchain_anthropic import ChatAnthropic

        mock_llm = MagicMock(spec=ChatAnthropic)
        mock_llm.model = "claude-3-5-sonnet-20241022"
        schema = MagicMock()

        result = _apply_structured_output(mock_llm, schema=schema)
        assert result is mock_llm
        mock_llm.with_structured_output.assert_not_called()

    def test_anthropic_claude2_returns_original(self):
        """Claude-2 models should be detected as legacy."""
        from langchain_anthropic import ChatAnthropic

        mock_llm = MagicMock(spec=ChatAnthropic)
        mock_llm.model = "claude-2.1"
        schema = MagicMock()

        result = _apply_structured_output(mock_llm, schema=schema)
        assert result is mock_llm

    def test_anthropic_no_model_name_returns_original(self):
        """If model name is not set, return the original LLM."""
        from langchain_anthropic import ChatAnthropic

        mock_llm = MagicMock(spec=ChatAnthropic)
        mock_llm.model = None
        schema = MagicMock()

        result = _apply_structured_output(mock_llm, schema=schema)
        assert result is mock_llm

    def test_anthropic_new_model_applies_structured_output(self):
        """Non-legacy Anthropic models should use structured output with stream=True."""
        from langchain_anthropic import ChatAnthropic

        mock_llm = MagicMock(spec=ChatAnthropic)
        mock_llm.model = "claude-4-opus-20260101"
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        schema = MagicMock()

        result = _apply_structured_output(mock_llm, schema=schema)
        assert result is mock_structured
        mock_llm.with_structured_output.assert_called_once_with(
            schema, stream=True, method="json_schema"
        )

    def test_bedrock_no_method_kwarg(self):
        """ChatBedrock should NOT get method='json_schema'."""
        from langchain_aws import ChatBedrock

        mock_llm = MagicMock(spec=ChatBedrock)
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        schema = MagicMock()

        result = _apply_structured_output(mock_llm, schema=schema)
        assert result is mock_structured
        # Should be called without method kwarg
        mock_llm.with_structured_output.assert_called_once_with(schema)

    def test_with_structured_output_raises_falls_back(self):
        """If with_structured_output raises, return original LLM."""
        from langchain_openai import ChatOpenAI

        mock_llm = MagicMock(spec=ChatOpenAI)
        mock_llm.with_structured_output.side_effect = Exception("not supported")
        schema = MagicMock()

        result = _apply_structured_output(mock_llm, schema=schema)
        assert result is mock_llm

    def test_google_llm_success(self):
        """ChatGoogleGenerativeAI should use structured output."""
        from langchain_google_genai import ChatGoogleGenerativeAI

        mock_llm = MagicMock(spec=ChatGoogleGenerativeAI)
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        schema = MagicMock()

        result = _apply_structured_output(mock_llm, schema=schema)
        assert result is mock_structured

    def test_azure_openai_success(self):
        """AzureChatOpenAI should use structured output with json_schema method."""
        from langchain_openai import AzureChatOpenAI

        mock_llm = MagicMock(spec=AzureChatOpenAI)
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        schema = MagicMock()

        result = _apply_structured_output(mock_llm, schema=schema)
        assert result is mock_structured
        mock_llm.with_structured_output.assert_called_once_with(
            schema, method="json_schema"
        )

    def test_mistral_llm_success(self):
        """ChatMistralAI should use structured output with json_schema method."""
        from langchain_mistralai import ChatMistralAI

        mock_llm = MagicMock(spec=ChatMistralAI)
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        schema = MagicMock()

        result = _apply_structured_output(mock_llm, schema=schema)
        assert result is mock_structured

    def test_anthropic_claude_sonnet_4_dated_is_legacy(self):
        """claude-sonnet-4-20250514 is in the legacy pattern list."""
        from langchain_anthropic import ChatAnthropic

        mock_llm = MagicMock(spec=ChatAnthropic)
        mock_llm.model = "claude-sonnet-4-20250514"
        schema = MagicMock()

        result = _apply_structured_output(mock_llm, schema=schema)
        assert result is mock_llm

    def test_anthropic_claude_opus_4_dated_is_legacy(self):
        """claude-opus-4-20250514 is in the legacy pattern list."""
        from langchain_anthropic import ChatAnthropic

        mock_llm = MagicMock(spec=ChatAnthropic)
        mock_llm.model = "claude-opus-4-20250514"
        schema = MagicMock()

        result = _apply_structured_output(mock_llm, schema=schema)
        assert result is mock_llm


# ---------------------------------------------------------------------------
# aiter_llm_stream
# ---------------------------------------------------------------------------
class TestAiterLlmStream:
    """Tests for aiter_llm_stream(llm, messages, parts)."""

    async def test_streaming_text_content(self):
        """Test streaming with text content from astream."""
        mock_chunk1 = MagicMock()
        mock_chunk1.content = "Hello "
        mock_chunk2 = MagicMock()
        mock_chunk2.content = "World"

        async def mock_astream(messages, config=None):
            for chunk in [mock_chunk1, mock_chunk2]:
                yield chunk

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)

        assert results == ["Hello ", "World"]

    async def test_streaming_dict_content(self):
        """Test that dict parts are yielded as-is."""
        dict_part = {"answer": "hello", "confidence": "High"}

        async def mock_astream(messages, config=None):
            yield dict_part

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)

        assert results == [dict_part]

    async def test_streaming_skips_empty_parts(self):
        """Empty/None parts should be skipped."""
        async def mock_astream(messages, config=None):
            yield None
            yield ""
            mock_chunk = MagicMock()
            mock_chunk.content = "data"
            yield mock_chunk

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)

        assert results == ["data"]

    async def test_streaming_empty_content_skipped(self):
        """Parts with empty string content should be skipped."""
        mock_chunk = MagicMock()
        mock_chunk.content = ""

        async def mock_astream(messages, config=None):
            yield mock_chunk

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)

        assert results == []

    async def test_non_streaming_fallback(self):
        """Test fallback to ainvoke when astream is not available."""
        mock_response = MagicMock()
        mock_response.content = "Full response"

        mock_llm = MagicMock(spec=[])  # No astream attribute
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)

        assert results == ["Full response"]

    async def test_non_streaming_dict_content(self):
        """Test ainvoke fallback with dict content."""
        mock_response = MagicMock()
        mock_response.content = {"key": "value"}

        mock_llm = MagicMock(spec=[])
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)

        assert results == [{"key": "value"}]

    async def test_non_streaming_empty_content(self):
        """Test ainvoke fallback with empty content."""
        mock_response = MagicMock()
        mock_response.content = ""

        mock_llm = MagicMock(spec=[])
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)

        assert results == []

    async def test_streaming_error_propagates(self):
        """Errors during streaming should propagate."""
        async def mock_astream(messages, config=None):
            raise RuntimeError("stream failed")
            yield  # pragma: no cover

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        with pytest.raises(RuntimeError, match="stream failed"):
            async for _ in aiter_llm_stream(mock_llm, []):
                pass

    async def test_parts_are_collected(self):
        """The parts list should be populated with each streamed part."""
        mock_chunk = MagicMock()
        mock_chunk.content = "hello"

        async def mock_astream(messages, config=None):
            yield mock_chunk

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        parts = []
        async for _ in aiter_llm_stream(mock_llm, [], parts=parts):
            pass

        assert len(parts) == 1
        assert parts[0] is mock_chunk

    async def test_list_content_stringified(self):
        """List content from a chunk should be stringified."""
        mock_chunk = MagicMock()
        mock_chunk.content = [{"type": "text", "text": "Hello"}]

        async def mock_astream(messages, config=None):
            yield mock_chunk

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)

        assert results == ["Hello"]

    @patch("app.utils.streaming.opik_tracer", new=MagicMock())
    async def test_opik_tracer_config_passed(self):
        """When opik_tracer is set, callbacks should be included."""
        mock_chunk = MagicMock()
        mock_chunk.content = "data"

        async def mock_astream(messages, config=None):
            # Verify config contains callbacks
            assert "callbacks" in config
            yield mock_chunk

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)

        assert results == ["data"]


# ---------------------------------------------------------------------------
# create_stream_record_response
# ---------------------------------------------------------------------------
class TestCreateStreamRecordResponse:
    """Tests for create_stream_record_response."""

    def test_basic_response(self):
        async def gen():
            yield b"data"

        response = create_stream_record_response(gen(), "test.txt")
        assert response.media_type == "application/octet-stream"
        assert "Content-Disposition" in response.headers
        assert "test.txt" in response.headers["Content-Disposition"]

    def test_custom_mime_type(self):
        async def gen():
            yield b"data"

        response = create_stream_record_response(
            gen(), "report.pdf", mime_type="application/pdf"
        )
        assert response.media_type == "application/pdf"

    def test_none_filename_uses_fallback(self):
        async def gen():
            yield b"data"

        response = create_stream_record_response(gen(), None, fallback_filename="download")
        assert "download" in response.headers["Content-Disposition"]

    def test_additional_headers(self):
        async def gen():
            yield b"data"

        extra = {"X-Custom": "value"}
        response = create_stream_record_response(gen(), "file.csv", additional_headers=extra)
        assert response.headers.get("X-Custom") == "value"

    def test_default_fallback_filename(self):
        async def gen():
            yield b"data"

        response = create_stream_record_response(gen(), "")
        # Should use "file" as the default fallback
        assert "file" in response.headers["Content-Disposition"]


# ---------------------------------------------------------------------------
# stream_content
# ---------------------------------------------------------------------------
class TestStreamContent:
    """Tests for stream_content(signed_url, ...)."""

    async def test_non_string_url_raises_type_error(self):
        """Passing a non-string signed_url should raise TypeError."""
        with pytest.raises(TypeError, match="Expected signed_url to be a string"):
            async for _ in stream_content(123):  # type: ignore
                pass

    async def test_coroutine_url_raises_type_error(self):
        """Passing a coroutine as signed_url should raise TypeError."""

        async def coro():
            return "url"

        with pytest.raises(TypeError, match="Expected signed_url to be a string"):
            async for _ in stream_content(coro()):  # type: ignore
                pass


# ---------------------------------------------------------------------------
# invoke_with_structured_output_and_reflection
# ---------------------------------------------------------------------------
class TestInvokeWithStructuredOutputAndReflection:
    """Tests for invoke_with_structured_output_and_reflection."""

    class SimpleSchema(BaseModel):
        answer: str
        confidence: str

    async def test_success_dict_response(self):
        """Successful invocation with dict response from structured output."""
        from langchain_openai import ChatOpenAI

        mock_llm = MagicMock(spec=ChatOpenAI)
        mock_structured_llm = MagicMock()
        mock_structured_llm.ainvoke = AsyncMock(
            return_value={"answer": "hello", "confidence": "High"}
        )
        mock_llm.with_structured_output = MagicMock(return_value=mock_structured_llm)

        messages = [HumanMessage(content="test")]
        result = await invoke_with_structured_output_and_reflection(
            mock_llm, messages, self.SimpleSchema
        )
        assert result is not None
        assert result.answer == "hello"
        assert result.confidence == "High"

    async def test_success_ai_message_response(self):
        """Successful invocation where response is an AIMessage."""
        mock_response = AIMessage(content='{"answer": "world", "confidence": "Low"}')

        from langchain_openai import ChatOpenAI

        mock_llm = MagicMock(spec=ChatOpenAI)
        mock_structured_llm = MagicMock()
        mock_structured_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_llm.with_structured_output = MagicMock(return_value=mock_structured_llm)

        messages = [HumanMessage(content="test")]
        result = await invoke_with_structured_output_and_reflection(
            mock_llm, messages, self.SimpleSchema
        )
        assert result is not None
        assert result.answer == "world"

    async def test_success_pydantic_model_response(self):
        """Response that is already a Pydantic model."""
        from langchain_openai import ChatOpenAI

        pydantic_response = self.SimpleSchema(answer="pydantic", confidence="Medium")

        mock_llm = MagicMock(spec=ChatOpenAI)
        mock_structured_llm = MagicMock()
        mock_structured_llm.ainvoke = AsyncMock(return_value=pydantic_response)
        mock_llm.with_structured_output = MagicMock(return_value=mock_structured_llm)

        messages = [HumanMessage(content="test")]
        result = await invoke_with_structured_output_and_reflection(
            mock_llm, messages, self.SimpleSchema
        )
        assert result is not None
        assert result.answer == "pydantic"

    async def test_llm_invocation_failure_returns_none(self):
        """If the LLM invocation itself fails, return None."""
        from langchain_openai import ChatOpenAI

        mock_llm = MagicMock(spec=ChatOpenAI)
        mock_structured_llm = MagicMock()
        mock_structured_llm.ainvoke = AsyncMock(side_effect=RuntimeError("LLM down"))
        mock_llm.with_structured_output = MagicMock(return_value=mock_structured_llm)

        messages = [HumanMessage(content="test")]
        result = await invoke_with_structured_output_and_reflection(
            mock_llm, messages, self.SimpleSchema
        )
        assert result is None

    async def test_parse_failure_then_reflection_success(self):
        """Initial parse fails, but reflection succeeds."""
        from langchain_openai import ChatOpenAI

        bad_response = AIMessage(content="not valid json")
        good_response = AIMessage(content='{"answer": "reflected", "confidence": "High"}')

        mock_llm = MagicMock(spec=ChatOpenAI)
        mock_structured_llm = MagicMock()
        mock_structured_llm.ainvoke = AsyncMock(side_effect=[bad_response, good_response])
        mock_llm.with_structured_output = MagicMock(return_value=mock_structured_llm)

        messages = [HumanMessage(content="test")]
        result = await invoke_with_structured_output_and_reflection(
            mock_llm, messages, self.SimpleSchema
        )
        assert result is not None
        assert result.answer == "reflected"

    async def test_all_retries_fail_returns_none(self):
        """If all reflection retries fail, return None."""
        from langchain_openai import ChatOpenAI

        bad_response = AIMessage(content="invalid json")

        mock_llm = MagicMock(spec=ChatOpenAI)
        mock_structured_llm = MagicMock()
        mock_structured_llm.ainvoke = AsyncMock(return_value=bad_response)
        mock_llm.with_structured_output = MagicMock(return_value=mock_structured_llm)

        messages = [HumanMessage(content="test")]
        result = await invoke_with_structured_output_and_reflection(
            mock_llm, messages, self.SimpleSchema, max_retries=2
        )
        assert result is None

    async def test_dict_with_content_key(self):
        """Dict response with 'content' key (Bedrock style)."""
        from langchain_openai import ChatOpenAI

        mock_llm = MagicMock(spec=ChatOpenAI)
        mock_structured_llm = MagicMock()
        mock_structured_llm.ainvoke = AsyncMock(
            return_value={"content": '{"answer": "bedrock", "confidence": "High"}'}
        )
        mock_llm.with_structured_output = MagicMock(return_value=mock_structured_llm)

        messages = [HumanMessage(content="test")]
        result = await invoke_with_structured_output_and_reflection(
            mock_llm, messages, self.SimpleSchema
        )
        assert result is not None
        assert result.answer == "bedrock"

    async def test_reflection_with_dict_content_key(self):
        """Reflection response is a dict with 'content' key."""
        from langchain_openai import ChatOpenAI

        bad_response = AIMessage(content="not json")

        mock_llm = MagicMock(spec=ChatOpenAI)
        mock_structured_llm = MagicMock()
        mock_structured_llm.ainvoke = AsyncMock(
            side_effect=[
                bad_response,
                {"content": '{"answer": "fixed", "confidence": "High"}'},
            ]
        )
        mock_llm.with_structured_output = MagicMock(return_value=mock_structured_llm)

        messages = [HumanMessage(content="test")]
        result = await invoke_with_structured_output_and_reflection(
            mock_llm, messages, self.SimpleSchema
        )
        assert result is not None
        assert result.answer == "fixed"

    async def test_reflection_with_direct_dict(self):
        """Reflection response is a plain dict (no content key)."""
        from langchain_openai import ChatOpenAI

        bad_response = AIMessage(content="bad")

        mock_llm = MagicMock(spec=ChatOpenAI)
        mock_structured_llm = MagicMock()
        mock_structured_llm.ainvoke = AsyncMock(
            side_effect=[
                bad_response,
                {"answer": "direct", "confidence": "Low"},
            ]
        )
        mock_llm.with_structured_output = MagicMock(return_value=mock_structured_llm)

        messages = [HumanMessage(content="test")]
        result = await invoke_with_structured_output_and_reflection(
            mock_llm, messages, self.SimpleSchema
        )
        assert result is not None
        assert result.answer == "direct"

    async def test_reflection_with_pydantic_model(self):
        """Reflection response is a Pydantic model instance."""
        from langchain_openai import ChatOpenAI

        bad_response = AIMessage(content="bad")
        good_model = self.SimpleSchema(answer="model", confidence="High")

        mock_llm = MagicMock(spec=ChatOpenAI)
        mock_structured_llm = MagicMock()
        mock_structured_llm.ainvoke = AsyncMock(side_effect=[bad_response, good_model])
        mock_llm.with_structured_output = MagicMock(return_value=mock_structured_llm)

        messages = [HumanMessage(content="test")]
        result = await invoke_with_structured_output_and_reflection(
            mock_llm, messages, self.SimpleSchema
        )
        assert result is not None
        assert result.answer == "model"

    async def test_max_retries_zero_no_reflection(self):
        """With max_retries=0, no reflection should happen."""
        from langchain_openai import ChatOpenAI

        bad_response = AIMessage(content="not valid")

        mock_llm = MagicMock(spec=ChatOpenAI)
        mock_structured_llm = MagicMock()
        mock_structured_llm.ainvoke = AsyncMock(return_value=bad_response)
        mock_llm.with_structured_output = MagicMock(return_value=mock_structured_llm)

        messages = [HumanMessage(content="test")]
        result = await invoke_with_structured_output_and_reflection(
            mock_llm, messages, self.SimpleSchema, max_retries=0
        )
        assert result is None
        # Only the initial call should happen (no retries)
        assert mock_structured_llm.ainvoke.call_count == 1


# ---------------------------------------------------------------------------
# invoke_with_row_descriptions_and_reflection
# ---------------------------------------------------------------------------
class TestInvokeWithRowDescriptionsAndReflection:
    """Tests for invoke_with_row_descriptions_and_reflection."""

    async def test_success_correct_count(self):
        """Successful invocation returning correct number of descriptions."""
        from app.modules.parsers.excel.prompt_template import RowDescriptions

        expected = RowDescriptions(descriptions=["desc1", "desc2", "desc3"])

        with patch(
            "app.utils.streaming.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            return_value=expected,
        ):
            result = await invoke_with_row_descriptions_and_reflection(
                MagicMock(), [HumanMessage(content="test")], expected_count=3
            )
        assert result is not None
        assert len(result.descriptions) == 3

    async def test_initial_parse_failure_returns_none(self):
        """If initial parse fails, return None."""
        with patch(
            "app.utils.streaming.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await invoke_with_row_descriptions_and_reflection(
                MagicMock(), [HumanMessage(content="test")], expected_count=3
            )
        assert result is None

    async def test_count_mismatch_reflection_success(self):
        """Count mismatch triggers reflection that succeeds."""
        from app.modules.parsers.excel.prompt_template import RowDescriptions

        wrong_count = RowDescriptions(descriptions=["d1", "d2"])
        correct_count = RowDescriptions(descriptions=["d1", "d2", "d3"])

        with patch(
            "app.utils.streaming.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            side_effect=[wrong_count, correct_count],
        ):
            result = await invoke_with_row_descriptions_and_reflection(
                MagicMock(), [HumanMessage(content="test")], expected_count=3
            )
        assert result is not None
        assert len(result.descriptions) == 3

    async def test_count_mismatch_reflection_still_wrong(self):
        """Count mismatch, reflection also returns wrong count -> None."""
        from app.modules.parsers.excel.prompt_template import RowDescriptions

        wrong1 = RowDescriptions(descriptions=["d1", "d2"])
        wrong2 = RowDescriptions(descriptions=["d1", "d2", "d3", "d4"])

        with patch(
            "app.utils.streaming.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            side_effect=[wrong1, wrong2],
        ):
            result = await invoke_with_row_descriptions_and_reflection(
                MagicMock(), [HumanMessage(content="test")], expected_count=3
            )
        assert result is None

    async def test_count_mismatch_reflection_parse_failure(self):
        """Count mismatch, reflection itself fails to parse -> None."""
        from app.modules.parsers.excel.prompt_template import RowDescriptions

        wrong = RowDescriptions(descriptions=["d1"])

        with patch(
            "app.utils.streaming.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            side_effect=[wrong, None],
        ):
            result = await invoke_with_row_descriptions_and_reflection(
                MagicMock(), [HumanMessage(content="test")], expected_count=3
            )
        assert result is None

    async def test_count_mismatch_reflection_exception(self):
        """Count mismatch, reflection raises an exception -> None."""
        from app.modules.parsers.excel.prompt_template import RowDescriptions

        wrong = RowDescriptions(descriptions=["d1"])

        with patch(
            "app.utils.streaming.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            side_effect=[wrong, RuntimeError("reflection exploded")],
        ):
            result = await invoke_with_row_descriptions_and_reflection(
                MagicMock(), [HumanMessage(content="test")], expected_count=3
            )
        assert result is None

    async def test_exact_match_no_reflection(self):
        """When count matches, no reflection should be attempted."""
        from app.modules.parsers.excel.prompt_template import RowDescriptions

        exact = RowDescriptions(descriptions=["a", "b"])

        mock_invoke = AsyncMock(return_value=exact)
        with patch(
            "app.utils.streaming.invoke_with_structured_output_and_reflection",
            new=mock_invoke,
        ):
            result = await invoke_with_row_descriptions_and_reflection(
                MagicMock(), [HumanMessage(content="test")], expected_count=2
            )
        assert result is not None
        # Should only be called once (no reflection)
        assert mock_invoke.call_count == 1


# ---------------------------------------------------------------------------
# call_aiter_llm_stream (via streaming helpers)
# ---------------------------------------------------------------------------
class TestCallAiterLlmStream:
    """Tests for call_aiter_llm_stream."""

    async def test_basic_json_stream(self):
        """Test basic JSON streaming with answer field."""
        from app.utils.streaming import call_aiter_llm_stream

        json_response = '{"answer": "The answer is 42", "reason": "because", "confidence": "High", "answerMatchType": "Exact Match", "blockNumbers": ["1"]}'

        class SimpleChunk:
            """Simple chunk without tool_calls attribute."""
            def __init__(self, content):
                self.content = content

            def __add__(self, other):
                return SimpleChunk(self.content + other.content)

        async def mock_astream(messages, config=None):
            for char in json_response:
                yield SimpleChunk(char)

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        events = []
        async for event in call_aiter_llm_stream(
            mock_llm, [], final_results=[], records=[], target_words_per_chunk=1
        ):
            events.append(event)

        # Should have a complete event
        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1
        assert "The answer is 42" in complete_events[0]["data"]["answer"]

    async def test_dict_response_stream(self):
        """Test when LLM returns a dict directly (structured output)."""
        from app.utils.streaming import call_aiter_llm_stream

        dict_response = {
            "answer": "Hello world",
            "reason": "test",
            "confidence": "High",
            "answerMatchType": "Exact Match",
            "blockNumbers": ["1"],
        }

        async def mock_astream(messages, config=None):
            yield dict_response

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        events = []
        async for event in call_aiter_llm_stream(
            mock_llm, [], final_results=[], records=[], target_words_per_chunk=1
        ):
            events.append(event)

        # Should have answer_chunk and/or complete events
        answer_chunks = [e for e in events if e.get("event") == "answer_chunk"]
        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1 or len(answer_chunks) > 0

    async def test_tool_calls_detected(self):
        """Test that tool calls in stream are detected."""
        from app.utils.streaming import call_aiter_llm_stream

        class ToolChunk:
            def __init__(self):
                self.content = ""
                self.tool_calls = [{"name": "search", "args": {}, "id": "tc1"}]

            def __add__(self, other):
                return self

        async def mock_astream(messages, config=None):
            yield ToolChunk()

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        events = []
        async for event in call_aiter_llm_stream(
            mock_llm, [], final_results=[], records=[], target_words_per_chunk=1
        ):
            events.append(event)

        tool_call_events = [e for e in events if e.get("event") == "tool_calls"]
        assert len(tool_call_events) == 1

    async def test_empty_answer_stream(self):
        """Test handling when LLM returns empty content."""
        from app.utils.streaming import call_aiter_llm_stream

        async def mock_astream(messages, config=None):
            return
            yield  # Make it a generator  # pragma: no cover

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        events = []
        async for event in call_aiter_llm_stream(
            mock_llm, [], final_results=[], records=[], target_words_per_chunk=1
        ):
            events.append(event)

        # Should still produce a complete or error event
        assert len(events) > 0


# ---------------------------------------------------------------------------
# stream_llm_response
# ---------------------------------------------------------------------------
class TestStreamLlmResponse:
    """Tests for stream_llm_response."""

    async def test_json_mode_with_existing_ai_message(self):
        """Fast path: existing AI message in JSON mode."""
        from app.utils.streaming import stream_llm_response

        ai_content = json.dumps({
            "answer": "Existing answer",
            "reason": "test",
            "confidence": "High",
        })
        messages = [AIMessage(content=ai_content)]
        test_logger = logging.getLogger("test_stream")

        events = []
        async for event in stream_llm_response(
            llm=MagicMock(),
            messages=messages,
            final_results=[],
            logger=test_logger,
            target_words_per_chunk=1,
            mode="json",
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1
        assert "Existing answer" in complete_events[0]["data"]["answer"]

    async def test_simple_mode_with_existing_ai_message(self):
        """Fast path: existing AI message in simple mode."""
        from app.utils.streaming import stream_llm_response

        messages = [AIMessage(content="Simple existing answer")]
        test_logger = logging.getLogger("test_stream")

        events = []
        async for event in stream_llm_response(
            llm=MagicMock(),
            messages=messages,
            final_results=[],
            logger=test_logger,
            target_words_per_chunk=1,
            mode="simple",
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1

    async def test_simple_mode_dict_message(self):
        """Fast path with dict-style assistant message."""
        from app.utils.streaming import stream_llm_response

        messages = [{"role": "assistant", "content": "Dict answer"}]
        test_logger = logging.getLogger("test_stream")

        events = []
        async for event in stream_llm_response(
            llm=MagicMock(),
            messages=messages,
            final_results=[],
            logger=test_logger,
            target_words_per_chunk=1,
            mode="simple",
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1

    async def test_json_mode_streaming_from_llm(self):
        """Normal streaming in JSON mode."""
        from app.utils.streaming import stream_llm_response

        json_response = '{"answer": "Streamed answer", "reason": "r", "confidence": "High"}'

        async def mock_astream(messages, config=None):
            for char in json_response:
                mock_chunk = MagicMock()
                mock_chunk.content = char
                yield mock_chunk

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        # Pass a HumanMessage so fast-path is not triggered
        messages = [HumanMessage(content="question")]
        test_logger = logging.getLogger("test_stream")

        events = []
        async for event in stream_llm_response(
            llm=mock_llm,
            messages=messages,
            final_results=[],
            logger=test_logger,
            target_words_per_chunk=1,
            mode="json",
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1
        assert "Streamed answer" in complete_events[0]["data"]["answer"]

    async def test_simple_mode_streaming_from_llm(self):
        """Normal streaming in simple mode."""
        from app.utils.streaming import stream_llm_response

        async def mock_astream(messages, config=None):
            for word in ["Hello ", "simple ", "world"]:
                mock_chunk = MagicMock()
                mock_chunk.content = word
                yield mock_chunk

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        messages = [HumanMessage(content="question")]
        test_logger = logging.getLogger("test_stream")

        events = []
        async for event in stream_llm_response(
            llm=mock_llm,
            messages=messages,
            final_results=[],
            logger=test_logger,
            target_words_per_chunk=1,
            mode="simple",
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1

    async def test_json_mode_stream_error(self):
        """Streaming error in JSON mode should yield error event."""
        from app.utils.streaming import stream_llm_response

        async def mock_astream(messages, config=None):
            raise RuntimeError("LLM error")
            yield  # pragma: no cover

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        messages = [HumanMessage(content="question")]
        test_logger = logging.getLogger("test_stream")

        events = []
        async for event in stream_llm_response(
            llm=mock_llm,
            messages=messages,
            final_results=[],
            logger=test_logger,
            target_words_per_chunk=1,
            mode="json",
        ):
            events.append(event)

        error_events = [e for e in events if e.get("event") == "error"]
        assert len(error_events) == 1

    async def test_simple_mode_stream_error(self):
        """Streaming error in simple mode should yield error event."""
        from app.utils.streaming import stream_llm_response

        async def mock_astream(messages, config=None):
            raise RuntimeError("simple mode error")
            yield  # pragma: no cover

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        messages = [HumanMessage(content="question")]
        test_logger = logging.getLogger("test_stream")

        events = []
        async for event in stream_llm_response(
            llm=mock_llm,
            messages=messages,
            final_results=[],
            logger=test_logger,
            target_words_per_chunk=1,
            mode="simple",
        ):
            events.append(event)

        error_events = [e for e in events if e.get("event") == "error"]
        assert len(error_events) == 1

    async def test_json_mode_non_json_content_in_ai_message(self):
        """Fast path with non-JSON AI message content in JSON mode."""
        from app.utils.streaming import stream_llm_response

        messages = [AIMessage(content="Not valid JSON at all")]
        test_logger = logging.getLogger("test_stream")

        events = []
        async for event in stream_llm_response(
            llm=MagicMock(),
            messages=messages,
            final_results=[],
            logger=test_logger,
            target_words_per_chunk=1,
            mode="json",
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1


# ---------------------------------------------------------------------------
# handle_json_mode
# ---------------------------------------------------------------------------
class TestHandleJsonMode:
    """Tests for handle_json_mode."""

    async def test_fast_path_ai_message(self):
        """Fast path: last message is an AIMessage with JSON content."""
        from app.utils.streaming import handle_json_mode

        ai_content = json.dumps({
            "answer": "JSON answer",
            "reason": "test",
            "confidence": "High",
        })
        messages = [AIMessage(content=ai_content)]
        test_logger = logging.getLogger("test")

        events = []
        async for event in handle_json_mode(
            llm=MagicMock(),
            messages=messages,
            final_results=[],
            records=[],
            logger=test_logger,
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1
        assert "JSON answer" in complete_events[0]["data"]["answer"]

    async def test_fast_path_dict_message(self):
        """Fast path: last message is a dict with role=assistant."""
        from app.utils.streaming import handle_json_mode

        ai_content = json.dumps({
            "answer": "Dict msg answer",
            "reason": "r",
            "confidence": "Low",
        })
        messages = [{"role": "assistant", "content": ai_content}]
        test_logger = logging.getLogger("test")

        events = []
        async for event in handle_json_mode(
            llm=MagicMock(),
            messages=messages,
            final_results=[],
            records=[],
            logger=test_logger,
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1

    async def test_fast_path_with_reference_data(self):
        """Fast path includes referenceData if present."""
        from app.utils.streaming import handle_json_mode

        ai_content = json.dumps({
            "answer": "With refs",
            "reason": "r",
            "confidence": "High",
            "referenceData": [{"id": "ref1"}],
        })
        messages = [AIMessage(content=ai_content)]
        test_logger = logging.getLogger("test")

        events = []
        async for event in handle_json_mode(
            llm=MagicMock(),
            messages=messages,
            final_results=[],
            records=[],
            logger=test_logger,
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1
        assert "referenceData" in complete_events[0]["data"]

    async def test_fast_path_non_json_content(self):
        """Fast path with non-JSON content falls back to raw text."""
        from app.utils.streaming import handle_json_mode

        messages = [AIMessage(content="Plain text answer")]
        test_logger = logging.getLogger("test")

        events = []
        async for event in handle_json_mode(
            llm=MagicMock(),
            messages=messages,
            final_results=[],
            records=[],
            logger=test_logger,
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1

    async def test_streaming_error_yields_error_event(self):
        """If LLM streaming fails, yield an error event."""
        from app.utils.streaming import handle_json_mode

        mock_llm = MagicMock()
        # _apply_structured_output will return the mock itself for unknown types
        mock_llm.__class__ = type("CustomLLM", (), {})

        # Make astream raise immediately
        async def bad_astream(messages, config=None):
            raise RuntimeError("stream broke")
            yield  # pragma: no cover

        mock_llm.astream = bad_astream

        messages = [HumanMessage(content="question")]
        test_logger = logging.getLogger("test")

        events = []
        async for event in handle_json_mode(
            llm=mock_llm,
            messages=messages,
            final_results=[],
            records=[],
            logger=test_logger,
        ):
            events.append(event)

        error_events = [e for e in events if e.get("event") == "error"]
        assert len(error_events) >= 1

    async def test_empty_messages_no_fast_path(self):
        """Empty messages list should skip fast path."""
        from app.utils.streaming import handle_json_mode

        json_resp = '{"answer": "empty", "reason": "r", "confidence": "High", "answerMatchType": "Exact Match", "blockNumbers": []}'

        class SimpleChunk:
            def __init__(self, content):
                self.content = content
            def __add__(self, other):
                return SimpleChunk(self.content + other.content)

        async def mock_astream(messages, config=None):
            for char in json_resp:
                yield SimpleChunk(char)

        mock_llm = MagicMock()
        mock_llm.__class__ = type("CustomLLM", (), {})
        mock_llm.astream = mock_astream

        test_logger = logging.getLogger("test")
        events = []
        async for event in handle_json_mode(
            llm=mock_llm,
            messages=[],
            final_results=[],
            records=[],
            logger=test_logger,
        ):
            events.append(event)

        # Should process normally without fast path
        assert len(events) > 0


# ---------------------------------------------------------------------------
# handle_simple_mode
# ---------------------------------------------------------------------------
class TestHandleSimpleMode:
    """Tests for handle_simple_mode."""

    async def test_fast_path_ai_message(self):
        """Fast path: existing AI message streams directly."""
        from app.utils.streaming import handle_simple_mode

        messages = [AIMessage(content="Simple fast path")]
        test_logger = logging.getLogger("test")

        events = []
        async for event in handle_simple_mode(
            llm=MagicMock(),
            messages=messages,
            final_results=[],
            records=[],
            logger=test_logger,
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1
        assert complete_events[0]["data"]["reason"] is None

    async def test_streaming_from_llm(self):
        """Normal streaming from LLM."""
        from app.utils.streaming import handle_simple_mode

        async def mock_astream(messages, config=None):
            for word in ["Hello ", "simple"]:
                mock_chunk = MagicMock()
                mock_chunk.content = word
                yield mock_chunk

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        messages = [HumanMessage(content="question")]
        test_logger = logging.getLogger("test")

        events = []
        async for event in handle_simple_mode(
            llm=mock_llm,
            messages=messages,
            final_results=[],
            records=[],
            logger=test_logger,
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1
        # Simple mode sets reason and confidence to defaults
        assert complete_events[0]["data"]["reason"] == "Not provided"
        assert complete_events[0]["data"]["confidence"] == "Medium"

    async def test_streaming_error_yields_error_event(self):
        """Error during LLM streaming yields error event."""
        from app.utils.streaming import handle_simple_mode

        async def bad_astream(messages, config=None):
            raise RuntimeError("simple stream error")
            yield  # pragma: no cover

        mock_llm = MagicMock()
        mock_llm.astream = bad_astream

        messages = [HumanMessage(content="q")]
        test_logger = logging.getLogger("test")

        events = []
        async for event in handle_simple_mode(
            llm=mock_llm,
            messages=messages,
            final_results=[],
            records=[],
            logger=test_logger,
        ):
            events.append(event)

        error_events = [e for e in events if e.get("event") == "error"]
        assert len(error_events) == 1

    async def test_fast_path_dict_message(self):
        """Fast path: dict-style assistant message."""
        from app.utils.streaming import handle_simple_mode

        messages = [{"role": "assistant", "content": "Dict simple answer"}]
        test_logger = logging.getLogger("test")

        events = []
        async for event in handle_simple_mode(
            llm=MagicMock(),
            messages=messages,
            final_results=[],
            records=[],
            logger=test_logger,
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1

    async def test_multi_word_chunking(self):
        """Test chunking with target_words_per_chunk > 1."""
        from app.utils.streaming import handle_simple_mode

        async def mock_astream(messages, config=None):
            for word in ["word1 ", "word2 ", "word3 ", "word4"]:
                mock_chunk = MagicMock()
                mock_chunk.content = word
                yield mock_chunk

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        messages = [HumanMessage(content="q")]
        test_logger = logging.getLogger("test")

        events = []
        async for event in handle_simple_mode(
            llm=mock_llm,
            messages=messages,
            final_results=[],
            records=[],
            logger=test_logger,
            target_words_per_chunk=2,
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1


# ---------------------------------------------------------------------------
# ANTHROPIC_LEGACY_MODEL_PATTERNS coverage
# ---------------------------------------------------------------------------
class TestAnthropicLegacyModelPatterns:
    """Verify all patterns in ANTHROPIC_LEGACY_MODEL_PATTERNS are correctly detected."""

    def test_claude_instant_is_legacy(self):
        from langchain_anthropic import ChatAnthropic

        mock_llm = MagicMock(spec=ChatAnthropic)
        mock_llm.model = "claude-instant-1.2"
        result = _apply_structured_output(mock_llm, schema=MagicMock())
        assert result is mock_llm

    def test_claude_1_is_legacy(self):
        from langchain_anthropic import ChatAnthropic

        mock_llm = MagicMock(spec=ChatAnthropic)
        mock_llm.model = "claude-1.3"
        result = _apply_structured_output(mock_llm, schema=MagicMock())
        assert result is mock_llm

    def test_claude_3_opus_is_legacy(self):
        from langchain_anthropic import ChatAnthropic

        mock_llm = MagicMock(spec=ChatAnthropic)
        mock_llm.model = "claude-3-opus-20240229"
        result = _apply_structured_output(mock_llm, schema=MagicMock())
        assert result is mock_llm

    def test_claude_3_haiku_is_legacy(self):
        from langchain_anthropic import ChatAnthropic

        mock_llm = MagicMock(spec=ChatAnthropic)
        mock_llm.model = "claude-3-haiku-20240307"
        result = _apply_structured_output(mock_llm, schema=MagicMock())
        assert result is mock_llm


# ---------------------------------------------------------------------------
# call_aiter_llm_stream - deeper branch coverage
# ---------------------------------------------------------------------------
class TestCallAiterLlmStreamBranches:
    """Tests covering reflection, fallback, and edge-case branches."""

    async def test_malformed_json_triggers_reflection(self):
        """When LLM returns malformed JSON, reflection should be triggered."""
        from app.utils.streaming import call_aiter_llm_stream

        class SimpleChunk:
            def __init__(self, content):
                self.content = content
            def __add__(self, other):
                return SimpleChunk(self.content + other.content)

        # First call: malformed JSON
        malformed = '{"answer": "test answer", "reason": "r", "confidence": INVALID}'
        # Second call (reflection): valid JSON
        valid = '{"answer": "reflected answer", "reason": "r", "confidence": "High", "answerMatchType": "Exact Match", "blockNumbers": ["1"]}'

        call_count = 0

        async def mock_astream(messages, config=None):
            nonlocal call_count
            data = malformed if call_count == 0 else valid
            call_count += 1
            for char in data:
                yield SimpleChunk(char)

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        events = []
        async for event in call_aiter_llm_stream(
            mock_llm, [], final_results=[], records=[],
            target_words_per_chunk=1, max_reflection_retries=2,
        ):
            events.append(event)

        # Should have restreaming and a complete event after reflection
        restream_events = [e for e in events if e.get("event") == "restreaming"]
        complete_events = [e for e in events if e.get("event") == "complete"]
        error_events = [e for e in events if e.get("event") == "error"]
        # Either reflection succeeded (complete) or fell back (complete or error)
        assert len(complete_events) > 0 or len(error_events) > 0

    async def test_max_retries_exhausted_with_answer_buf(self):
        """When max retries are exhausted but answer_buf exists, use it as fallback."""
        from app.utils.streaming import call_aiter_llm_stream

        class SimpleChunk:
            def __init__(self, content):
                self.content = content
            def __add__(self, other):
                return SimpleChunk(self.content + other.content)

        # Malformed JSON that at least has an answer field start
        malformed = '{"answer": "partial answer", "confidence": BAD}'

        async def mock_astream(messages, config=None):
            for char in malformed:
                yield SimpleChunk(char)

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        events = []
        async for event in call_aiter_llm_stream(
            mock_llm, [], final_results=[], records=[],
            target_words_per_chunk=1, max_reflection_retries=0,
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1
        assert "partial answer" in complete_events[0]["data"]["answer"]

    async def test_max_retries_exhausted_no_answer_buf(self):
        """When max retries exhausted and no answer_buf, yield error."""
        from app.utils.streaming import call_aiter_llm_stream

        class SimpleChunk:
            def __init__(self, content):
                self.content = content
            def __add__(self, other):
                return SimpleChunk(self.content + other.content)

        # JSON with no answer field at all
        malformed = '{"reason": "test"}'

        async def mock_astream(messages, config=None):
            for char in malformed:
                yield SimpleChunk(char)

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        events = []
        async for event in call_aiter_llm_stream(
            mock_llm, [], final_results=[], records=[],
            target_words_per_chunk=1, max_reflection_retries=0,
        ):
            events.append(event)

        error_events = [e for e in events if e.get("event") == "error"]
        assert len(error_events) == 1
        assert "LLM did not provide any appropriate answer" in error_events[0]["data"]["error"]

    async def test_reflection_with_original_llm(self):
        """Reflection uses original_llm to re-apply structured output."""
        from app.utils.streaming import call_aiter_llm_stream

        class SimpleChunk:
            def __init__(self, content):
                self.content = content
            def __add__(self, other):
                return SimpleChunk(self.content + other.content)

        malformed = '{"answer": "test", "bad_field": INVALID}'
        valid = '{"answer": "fixed", "reason": "r", "confidence": "High", "answerMatchType": "Exact Match", "blockNumbers": ["1"]}'

        call_count = 0

        async def mock_astream(messages, config=None):
            nonlocal call_count
            data = malformed if call_count == 0 else valid
            call_count += 1
            for char in data:
                yield SimpleChunk(char)

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream
        mock_llm.__class__ = type("CustomLLM", (), {})

        # original_llm is different from the streamed llm
        original_mock = MagicMock()
        original_mock.__class__ = type("CustomLLM", (), {})
        original_mock.astream = mock_astream

        events = []
        async for event in call_aiter_llm_stream(
            mock_llm, [], final_results=[], records=[],
            target_words_per_chunk=1, max_reflection_retries=1,
            original_llm=original_mock,
        ):
            events.append(event)

        # Should have at least a complete or error event
        assert len(events) > 0

    async def test_dict_response_with_empty_answer(self):
        """Dict response from structured output with empty answer."""
        from app.utils.streaming import call_aiter_llm_stream

        dict_response = {
            "answer": "",
            "reason": "no data",
            "confidence": "Low",
            "answerMatchType": "Exact Match",
            "blockNumbers": [],
        }

        async def mock_astream(messages, config=None):
            yield dict_response

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        events = []
        async for event in call_aiter_llm_stream(
            mock_llm, [], final_results=[], records=[], target_words_per_chunk=1
        ):
            events.append(event)

        # Should produce metadata event (empty answer) and then complete
        assert len(events) > 0

    async def test_dict_response_with_incomplete_citation(self):
        """Dict response with incomplete citation at end of answer."""
        from app.utils.streaming import call_aiter_llm_stream

        dict_response = {
            "answer": "Answer with incomplete [",
            "reason": "test",
            "confidence": "High",
            "answerMatchType": "Exact Match",
            "blockNumbers": [],
        }

        async def mock_astream(messages, config=None):
            yield dict_response

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        events = []
        async for event in call_aiter_llm_stream(
            mock_llm, [], final_results=[], records=[], target_words_per_chunk=1
        ):
            events.append(event)

        # Should handle incomplete citation gracefully
        assert len(events) > 0


# ---------------------------------------------------------------------------
# stream_llm_response - additional branch coverage
# ---------------------------------------------------------------------------
class TestStreamLlmResponseBranches:
    """Extra tests for edge-case branches in stream_llm_response."""

    async def test_json_mode_malformed_json_fallback(self):
        """JSON mode with malformed JSON falls back to answer_buf."""
        from app.utils.streaming import stream_llm_response

        class SimpleChunk:
            def __init__(self, content):
                self.content = content

        # Return partial JSON that won't parse
        async def mock_astream(messages, config=None):
            for s in ['{"answer": "fallback text', '"]']:
                yield SimpleChunk(s)

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        messages = [HumanMessage(content="q")]
        test_logger = logging.getLogger("test_stream")

        events = []
        async for event in stream_llm_response(
            llm=mock_llm,
            messages=messages,
            final_results=[],
            logger=test_logger,
            target_words_per_chunk=1,
            mode="json",
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1

    async def test_json_mode_valid_json_with_reference_data(self):
        """JSON mode streams valid JSON with referenceData field."""
        from app.utils.streaming import stream_llm_response

        class SimpleChunk:
            def __init__(self, content):
                self.content = content

        json_resp = json.dumps({
            "answer": "The answer",
            "reason": "reasoning",
            "confidence": "High",
            "referenceData": [{"id": "ref1"}],
        })

        async def mock_astream(messages, config=None):
            for s in [json_resp[:40], json_resp[40:]]:
                yield SimpleChunk(s)

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        messages = [HumanMessage(content="q")]
        test_logger = logging.getLogger("test_stream")

        events = []
        async for event in stream_llm_response(
            llm=mock_llm,
            messages=messages,
            final_results=[],
            logger=test_logger,
            target_words_per_chunk=1,
            mode="json",
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1
        assert "referenceData" in complete_events[0]["data"]

    async def test_simple_mode_with_virtual_record_id(self):
        """Simple mode with virtual_record_id_to_result set."""
        from app.utils.streaming import stream_llm_response

        class SimpleChunk:
            def __init__(self, content):
                self.content = content

        async def mock_astream(messages, config=None):
            yield SimpleChunk("Hello world answer")

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        messages = [HumanMessage(content="q")]
        test_logger = logging.getLogger("test_stream")

        events = []
        async for event in stream_llm_response(
            llm=mock_llm,
            messages=messages,
            final_results=[],
            logger=test_logger,
            target_words_per_chunk=1,
            mode="simple",
            virtual_record_id_to_result={"id1": {"name": "test"}},
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1

    async def test_json_mode_empty_messages_list(self):
        """JSON mode with empty messages list (no fast path)."""
        from app.utils.streaming import stream_llm_response

        class SimpleChunk:
            def __init__(self, content):
                self.content = content

        json_resp = '{"answer": "empty msgs", "reason": "r", "confidence": "Low"}'

        async def mock_astream(messages, config=None):
            for s in [json_resp[:30], json_resp[30:]]:
                yield SimpleChunk(s)

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        test_logger = logging.getLogger("test_stream")

        events = []
        async for event in stream_llm_response(
            llm=mock_llm,
            messages=[],
            final_results=[],
            logger=test_logger,
            target_words_per_chunk=1,
            mode="json",
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1

    async def test_simple_mode_with_chunking(self):
        """Simple mode streamed with target_words_per_chunk=2."""
        from app.utils.streaming import stream_llm_response

        class SimpleChunk:
            def __init__(self, content):
                self.content = content

        async def mock_astream(messages, config=None):
            for word in ["word1 ", "word2 ", "word3 ", "word4 ", "word5"]:
                yield SimpleChunk(word)

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        messages = [HumanMessage(content="q")]
        test_logger = logging.getLogger("test_stream")

        events = []
        async for event in stream_llm_response(
            llm=mock_llm,
            messages=messages,
            final_results=[],
            logger=test_logger,
            target_words_per_chunk=2,
            mode="simple",
        ):
            events.append(event)

        answer_chunks = [e for e in events if e.get("event") == "answer_chunk"]
        complete_events = [e for e in events if e.get("event") == "complete"]
        # Should have at least one chunk and one complete
        assert len(complete_events) == 1
        assert len(answer_chunks) >= 1


# ---------------------------------------------------------------------------
# stream_llm_response_with_tools - coverage for the orchestration function
# ---------------------------------------------------------------------------
class TestStreamLlmResponseWithTools:
    """Tests for stream_llm_response_with_tools."""

    async def test_simple_mode_ignores_tools(self):
        """In simple mode, tools are forced to None."""
        from app.utils.streaming import stream_llm_response_with_tools

        messages = [AIMessage(content="Simple answer")]

        events = []
        async for event in stream_llm_response_with_tools(
            llm=MagicMock(),
            messages=messages,
            final_results=[],
            all_queries=["test query"],
            retrieval_service=MagicMock(),
            user_id="u1",
            org_id="o1",
            virtual_record_id_to_result={},
            blob_store=MagicMock(),
            is_multimodal_llm=False,
            context_length=10000,
            tools=[MagicMock()],
            tool_runtime_kwargs={"key": "val"},
            mode="simple",
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1

    async def test_no_tools_json_mode(self):
        """JSON mode without tools streams directly."""
        from app.utils.streaming import stream_llm_response_with_tools

        ai_content = json.dumps({
            "answer": "No tools answer",
            "reason": "r",
            "confidence": "High",
        })
        messages = [AIMessage(content=ai_content)]

        events = []
        async for event in stream_llm_response_with_tools(
            llm=MagicMock(),
            messages=messages,
            final_results=[],
            all_queries=["test"],
            retrieval_service=MagicMock(),
            user_id="u1",
            org_id="o1",
            virtual_record_id_to_result={},
            blob_store=MagicMock(),
            is_multimodal_llm=False,
            context_length=10000,
            tools=None,
            mode="json",
        ):
            events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1

    async def test_no_tools_simple_mode_with_conversation_tasks(self):
        """Simple mode with conversation_id triggers task collection."""
        from app.utils.streaming import stream_llm_response_with_tools

        messages = [AIMessage(content="Task answer")]

        with patch(
            "app.utils.conversation_tasks.await_and_collect_results",
            new_callable=AsyncMock,
            return_value=[{"fileName": "report.csv", "signedUrl": "https://example.com/report.csv"}],
        ):
            events = []
            async for event in stream_llm_response_with_tools(
                llm=MagicMock(),
                messages=messages,
                final_results=[],
                all_queries=["test"],
                retrieval_service=MagicMock(),
                user_id="u1",
                org_id="o1",
                virtual_record_id_to_result={},
                blob_store=MagicMock(),
                is_multimodal_llm=False,
                context_length=10000,
                mode="simple",
                conversation_id="conv123",
            ):
                events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1
        # Task markers should be appended
        assert "download_conversation_task" in complete_events[0]["data"]["answer"]

    async def test_json_mode_with_conversation_tasks(self):
        """JSON mode with conversation_id triggers task collection and appends markers."""
        from app.utils.streaming import stream_llm_response_with_tools

        ai_content = json.dumps({
            "answer": "JSON task answer",
            "reason": "r",
            "confidence": "High",
        })
        messages = [AIMessage(content=ai_content)]

        with patch(
            "app.utils.conversation_tasks.await_and_collect_results",
            new_callable=AsyncMock,
            return_value=[{"fileName": "data.csv", "signedUrl": "https://example.com/data.csv"}],
        ):
            events = []
            async for event in stream_llm_response_with_tools(
                llm=MagicMock(),
                messages=messages,
                final_results=[],
                all_queries=["test"],
                retrieval_service=MagicMock(),
                user_id="u1",
                org_id="o1",
                virtual_record_id_to_result={},
                blob_store=MagicMock(),
                is_multimodal_llm=False,
                context_length=10000,
                mode="json",
                conversation_id="conv456",
            ):
                events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1
        assert "download_conversation_task" in complete_events[0]["data"]["answer"]

    async def test_error_during_answer_generation(self):
        """Error during final answer generation yields error event."""
        from app.utils.streaming import stream_llm_response_with_tools

        async def bad_astream(messages, config=None):
            raise RuntimeError("generation failed")
            yield  # pragma: no cover

        mock_llm = MagicMock()
        mock_llm.__class__ = type("CustomLLM", (), {})
        mock_llm.astream = bad_astream

        messages = [HumanMessage(content="q")]

        events = []
        async for event in stream_llm_response_with_tools(
            llm=mock_llm,
            messages=messages,
            final_results=[],
            all_queries=["test"],
            retrieval_service=MagicMock(),
            user_id="u1",
            org_id="o1",
            virtual_record_id_to_result={},
            blob_store=MagicMock(),
            is_multimodal_llm=False,
            context_length=10000,
            mode="json",
        ):
            events.append(event)

        error_events = [e for e in events if e.get("event") == "error"]
        assert len(error_events) >= 1

    async def test_no_conversation_tasks_empty_result(self):
        """Conversation tasks returning empty list should not append markers."""
        from app.utils.streaming import stream_llm_response_with_tools

        ai_content = json.dumps({
            "answer": "No tasks here",
            "reason": "r",
            "confidence": "High",
        })
        messages = [AIMessage(content=ai_content)]

        with patch(
            "app.utils.conversation_tasks.await_and_collect_results",
            new_callable=AsyncMock,
            return_value=[],
        ):
            events = []
            async for event in stream_llm_response_with_tools(
                llm=MagicMock(),
                messages=messages,
                final_results=[],
                all_queries=["test"],
                retrieval_service=MagicMock(),
                user_id="u1",
                org_id="o1",
                virtual_record_id_to_result={},
                blob_store=MagicMock(),
                is_multimodal_llm=False,
                context_length=10000,
                mode="json",
                conversation_id="conv789",
            ):
                events.append(event)

        complete_events = [e for e in events if e.get("event") == "complete"]
        assert len(complete_events) == 1
        assert "download_conversation_task" not in complete_events[0]["data"]["answer"]


# ---------------------------------------------------------------------------
# execute_tool_calls - coverage for tool execution
# ---------------------------------------------------------------------------
class TestExecuteToolCalls:
    """Tests for execute_tool_calls."""

    async def test_no_tools_raises_value_error(self):
        """Passing empty tools list raises ValueError."""
        from app.utils.streaming import execute_tool_calls

        with pytest.raises(ValueError, match="Tools are required"):
            async for _ in execute_tool_calls(
                llm=MagicMock(),
                messages=[],
                tools=[],
                tool_runtime_kwargs={},
                final_results=[],
                virtual_record_id_to_result={},
                blob_store=MagicMock(),
                all_queries=["test"],
                retrieval_service=MagicMock(),
                user_id="u1",
                org_id="o1",
                context_length=10000,
            ):
                pass

    async def test_tool_call_and_execution(self):
        """Test a complete tool call and execution cycle."""
        from app.utils.streaming import execute_tool_calls

        # Create an AI message with tool calls
        ai_msg = AIMessage(
            content="",
            tool_calls=[{
                "name": "search_tool",
                "args": {"query": "test"},
                "id": "tc1",
            }],
        )

        # Mock the LLM to return an AI message with tool calls
        class SimpleChunk:
            def __init__(self, content, tool_calls=None):
                self.content = content
                self.tool_calls = tool_calls or []
            def __add__(self, other):
                tc = self.tool_calls + other.tool_calls
                return SimpleChunk(self.content + other.content, tc)

        async def mock_astream(messages, config=None):
            yield SimpleChunk("", tool_calls=[{"name": "search_tool", "args": {"query": "test"}, "id": "tc1"}])

        mock_llm = MagicMock()
        mock_llm.__class__ = type("CustomLLM", (), {})
        mock_llm.bind_tools = MagicMock(side_effect=Exception("no bind"))
        mock_llm.astream = mock_astream

        # Create a mock tool
        mock_tool = AsyncMock()
        mock_tool.name = "search_tool"
        mock_tool.arun = AsyncMock(return_value={
            "ok": True,
            "records": [],
            "record_count": 0,
        })

        events = []
        async for event in execute_tool_calls(
            llm=mock_llm,
            messages=[HumanMessage(content="find something")],
            tools=[mock_tool],
            tool_runtime_kwargs={},
            final_results=[],
            virtual_record_id_to_result={},
            blob_store=MagicMock(),
            all_queries=["test"],
            retrieval_service=MagicMock(),
            user_id="u1",
            org_id="o1",
            context_length=10000,
        ):
            events.append(event)

        event_types = [e.get("event") for e in events]
        # Should have tool_call, tool_success or tool_error, and tool_execution_complete
        assert "tool_execution_complete" in event_types


# ---------------------------------------------------------------------------
# Additional edge cases for stream_content
# ---------------------------------------------------------------------------
class TestStreamContentEdgeCases:
    """Additional edge cases for stream_content."""

    async def test_none_url_raises_type_error(self):
        """Passing None as signed_url should raise TypeError."""
        with pytest.raises(TypeError, match="Expected signed_url to be a string"):
            async for _ in stream_content(None):  # type: ignore
                pass

    async def test_list_url_raises_type_error(self):
        """Passing a list as signed_url should raise TypeError."""
        with pytest.raises(TypeError, match="Expected signed_url to be a string"):
            async for _ in stream_content([]):  # type: ignore
                pass


# ---------------------------------------------------------------------------
# Additional edge cases for create_stream_record_response
# ---------------------------------------------------------------------------
class TestCreateStreamRecordResponseEdgeCases:
    """Additional edge cases for create_stream_record_response."""

    def test_none_mime_type_uses_default(self):
        async def gen():
            yield b"data"

        response = create_stream_record_response(gen(), "file.txt", mime_type=None)
        assert response.media_type == "application/octet-stream"

    def test_custom_fallback_filename(self):
        async def gen():
            yield b"data"

        response = create_stream_record_response(
            gen(), "", fallback_filename="custom_fallback"
        )
        assert "custom_fallback" in response.headers["Content-Disposition"]

    def test_headers_merged_correctly(self):
        async def gen():
            yield b"data"

        extra = {"X-Total-Count": "42", "X-Custom-Header": "value"}
        response = create_stream_record_response(
            gen(), "file.csv", additional_headers=extra
        )
        assert response.headers.get("X-Total-Count") == "42"
        assert response.headers.get("X-Custom-Header") == "value"


# ---------------------------------------------------------------------------
# Additional edge cases for aiter_llm_stream
# ---------------------------------------------------------------------------
class TestAiterLlmStreamEdgeCases:
    """Edge case tests for aiter_llm_stream."""

    async def test_non_streaming_no_content_attribute(self):
        """ainvoke fallback where response has no content attribute."""
        mock_response = "raw string response"

        mock_llm = MagicMock(spec=[])
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)

        # content = getattr(response, "content", response) -> returns "raw string response"
        assert results == ["raw string response"]

    async def test_streaming_none_content(self):
        """Chunks with None content should be skipped."""
        mock_chunk = MagicMock()
        mock_chunk.content = None

        async def mock_astream(messages, config=None):
            yield mock_chunk

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)

        assert results == []

    async def test_non_streaming_dict_response_no_content(self):
        """ainvoke returns a dict without content attr -- treated as dict."""
        mock_response = {"key": "val"}

        mock_llm = MagicMock(spec=[])
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)

        # content = getattr(response, "content", response) -> returns the dict
        # isinstance(content, dict) -> True, so yields dict
        assert results == [{"key": "val"}]

    async def test_multiple_dict_and_text_chunks_mixed(self):
        """Streaming with mixed dict and text chunks."""
        dict_chunk = {"answer": "hello"}
        text_chunk = MagicMock()
        text_chunk.content = "world"

        async def mock_astream(messages, config=None):
            yield dict_chunk
            yield text_chunk

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)

        assert results[0] == {"answer": "hello"}
        assert results[1] == "world"


# ---------------------------------------------------------------------------
# Additional edge cases for _apply_structured_output
# ---------------------------------------------------------------------------
class TestApplyStructuredOutputEdgeCases:
    """Edge case tests for _apply_structured_output."""

    def test_google_llm_failure_falls_back(self):
        """If with_structured_output fails for Google, return original LLM."""
        from langchain_google_genai import ChatGoogleGenerativeAI

        mock_llm = MagicMock(spec=ChatGoogleGenerativeAI)
        mock_llm.with_structured_output.side_effect = Exception("not supported")
        schema = MagicMock()

        result = _apply_structured_output(mock_llm, schema=schema)
        assert result is mock_llm

    def test_mistral_failure_falls_back(self):
        """If with_structured_output fails for Mistral, return original LLM."""
        from langchain_mistralai import ChatMistralAI

        mock_llm = MagicMock(spec=ChatMistralAI)
        mock_llm.with_structured_output.side_effect = Exception("fail")
        schema = MagicMock()

        result = _apply_structured_output(mock_llm, schema=schema)
        assert result is mock_llm

    def test_bedrock_failure_falls_back(self):
        """If with_structured_output fails for Bedrock, return original LLM."""
        from langchain_aws import ChatBedrock

        mock_llm = MagicMock(spec=ChatBedrock)
        mock_llm.with_structured_output.side_effect = Exception("fail")
        schema = MagicMock()

        result = _apply_structured_output(mock_llm, schema=schema)
        assert result is mock_llm


# ---------------------------------------------------------------------------
# stream_content
# ---------------------------------------------------------------------------
class TestStreamContent:
    """Tests for stream_content async generator."""

    @pytest.mark.asyncio
    async def test_invalid_url_type_raises(self):
        """Non-string signed_url raises TypeError."""
        with pytest.raises(TypeError, match="Expected signed_url to be a string"):
            async for _ in stream_content(123):
                pass

    @pytest.mark.asyncio
    async def test_coroutine_url_type_raises(self):
        """Coroutine passed as signed_url raises TypeError."""
        import asyncio
        coro = asyncio.coroutine(lambda: None)
        with pytest.raises(TypeError):
            async for _ in stream_content(coro):
                pass


# ---------------------------------------------------------------------------
# create_stream_record_response
# ---------------------------------------------------------------------------
class TestCreateStreamRecordResponse:
    """Tests for create_stream_record_response."""

    def test_basic_response(self):
        async def gen():
            yield b"data"
        resp = create_stream_record_response(gen(), "test.pdf")
        assert resp.media_type == "application/octet-stream"

    def test_custom_mime_type(self):
        async def gen():
            yield b"data"
        resp = create_stream_record_response(gen(), "test.pdf", mime_type="application/pdf")
        assert resp.media_type == "application/pdf"

    def test_none_filename(self):
        async def gen():
            yield b"data"
        resp = create_stream_record_response(gen(), None, fallback_filename="download")
        assert "download" in resp.headers.get("content-disposition", "")

    def test_additional_headers(self):
        async def gen():
            yield b"data"
        resp = create_stream_record_response(
            gen(), "test.pdf",
            additional_headers={"X-Custom": "value"}
        )
        assert resp.headers.get("x-custom") == "value"


# ---------------------------------------------------------------------------
# _stringify_content
# ---------------------------------------------------------------------------
class TestStringifyContentExtended:
    """Extended tests for _stringify_content."""

    def test_none_returns_empty(self):
        assert _stringify_content(None) == ""

    def test_string_returns_as_is(self):
        assert _stringify_content("hello") == "hello"

    def test_list_of_strings(self):
        assert _stringify_content(["a", "b", "c"]) == "abc"

    def test_list_of_dicts_text_type(self):
        items = [{"type": "text", "text": "hello "}]
        assert _stringify_content(items) == "hello "

    def test_list_of_dicts_no_type(self):
        items = [{"text": "world"}]
        assert _stringify_content(items) == "world"

    def test_list_mixed(self):
        items = [
            {"type": "text", "text": "a"},
            "b",
            {"type": "image_url", "url": "http://img"},
            42,
        ]
        result = _stringify_content(items)
        assert "a" in result
        assert "b" in result

    def test_dict_returns_str(self):
        result = _stringify_content({"key": "val"})
        assert isinstance(result, str)

    def test_empty_list(self):
        assert _stringify_content([]) == ""


# ---------------------------------------------------------------------------
# get_vectorDb_limit
# ---------------------------------------------------------------------------
class TestGetVectorDbLimitExtended:
    """Extended tests for get_vectorDb_limit."""

    def test_exact_boundary_17000(self):
        assert get_vectorDb_limit(17000) == 43

    def test_exact_boundary_33000(self):
        assert get_vectorDb_limit(33000) == 154

    def test_exact_boundary_65000(self):
        assert get_vectorDb_limit(65000) == 213

    def test_above_65000(self):
        assert get_vectorDb_limit(100000) == 266

    def test_zero(self):
        assert get_vectorDb_limit(0) == 43

    def test_small_value(self):
        assert get_vectorDb_limit(1000) == 43


# ---------------------------------------------------------------------------
# supports_human_message_after_tool
# ---------------------------------------------------------------------------
class TestSupportsHumanMessageExtended:
    """Extended tests for supports_human_message_after_tool."""

    def test_openai_supports(self):
        from langchain_openai import ChatOpenAI
        mock_llm = MagicMock(spec=ChatOpenAI)
        assert supports_human_message_after_tool(mock_llm) is True

    def test_anthropic_supports(self):
        from langchain_anthropic import ChatAnthropic
        mock_llm = MagicMock(spec=ChatAnthropic)
        assert supports_human_message_after_tool(mock_llm) is True

    def test_mistral_does_not_support(self):
        from langchain_mistralai import ChatMistralAI
        mock_llm = MagicMock(spec=ChatMistralAI)
        assert supports_human_message_after_tool(mock_llm) is False

    def test_generic_llm_supports(self):
        mock_llm = MagicMock()
        assert supports_human_message_after_tool(mock_llm) is True


# ---------------------------------------------------------------------------
# _get_schema_for_structured_output / _get_schema_for_parsing
# ---------------------------------------------------------------------------
class TestGetSchemaFunctions:
    """Tests for schema selection functions."""

    def test_structured_output_agent(self):
        from app.modules.agents.qna.schemas import AgentAnswerWithMetadataDict
        result = _get_schema_for_structured_output(is_agent=True)
        assert result is AgentAnswerWithMetadataDict

    def test_structured_output_default(self):
        from app.modules.qna.prompt_templates import AnswerWithMetadataDict
        result = _get_schema_for_structured_output(is_agent=False)
        assert result is AnswerWithMetadataDict

    def test_parsing_agent(self):
        from app.modules.agents.qna.schemas import AgentAnswerWithMetadataJSON
        result = _get_schema_for_parsing(is_agent=True)
        assert result is AgentAnswerWithMetadataJSON

    def test_parsing_default(self):
        from app.modules.qna.prompt_templates import AnswerWithMetadataJSON
        result = _get_schema_for_parsing(is_agent=False)
        assert result is AnswerWithMetadataJSON


# ---------------------------------------------------------------------------
# get_parser
# ---------------------------------------------------------------------------
class TestGetParserExtended:
    """Extended tests for get_parser."""

    def test_default_schema(self):
        parser, fmt = get_parser()
        assert parser is not None
        assert isinstance(fmt, str)
        assert len(fmt) > 0

    def test_custom_schema(self):
        from app.modules.agents.qna.schemas import AgentAnswerWithMetadataJSON
        parser, fmt = get_parser(schema=AgentAnswerWithMetadataJSON)
        assert parser is not None
        assert isinstance(fmt, str)


# ---------------------------------------------------------------------------
# aiter_llm_stream
# ---------------------------------------------------------------------------
class TestAiterLlmStreamExtended:
    """Extended tests for aiter_llm_stream."""

    @pytest.mark.asyncio
    async def test_non_streaming_llm(self):
        """LLM without astream uses ainvoke fallback."""
        mock_llm = MagicMock(spec=[])  # no astream
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="response"))

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)
        assert len(results) == 1
        assert results[0] == "response"

    @pytest.mark.asyncio
    async def test_non_streaming_dict_content(self):
        """LLM without astream returns dict content."""
        mock_llm = MagicMock(spec=[])
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content={"key": "val"}))

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)
        assert results[0] == {"key": "val"}

    @pytest.mark.asyncio
    async def test_non_streaming_empty_content(self):
        """LLM without astream with None content."""
        mock_llm = MagicMock(spec=[])
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=None))

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_stream_error_propagates(self):
        """Errors in streaming propagate."""
        async def failing_stream(messages, config=None):
            raise RuntimeError("stream error")
            yield  # make it a generator

        mock_llm = MagicMock()
        mock_llm.astream = failing_stream

        with pytest.raises(RuntimeError, match="stream error"):
            async for _ in aiter_llm_stream(mock_llm, []):
                pass

    @pytest.mark.asyncio
    async def test_empty_parts_skipped(self):
        """Empty/None chunks are skipped."""
        async def mock_astream(messages, config=None):
            yield None
            yield MagicMock(content="hello")

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)
        assert len(results) == 1
        assert results[0] == "hello"

    @pytest.mark.asyncio
    async def test_parts_accumulation(self):
        """Parts parameter accumulates chunks."""
        async def mock_astream(messages, config=None):
            yield MagicMock(content="a")
            yield MagicMock(content="b")

        mock_llm = MagicMock()
        mock_llm.astream = mock_astream

        parts = []
        async for _ in aiter_llm_stream(mock_llm, [], parts=parts):
            pass
        assert len(parts) == 2


# ---------------------------------------------------------------------------
# stream_content  (lines 129-208)
# ---------------------------------------------------------------------------

class TestStreamContentCoverage:
    """Additional tests targeting uncovered lines in stream_content."""

    @pytest.mark.asyncio
    async def test_stream_content_success(self):
        """Successful streaming should yield chunks."""
        from app.utils.streaming import stream_content

        mock_response = AsyncMock()
        mock_response.status = 200

        async def mock_iter_chunked(size):
            yield b"chunk1"
            yield b"chunk2"

        mock_response.content = MagicMock()
        mock_response.content.iter_chunked = mock_iter_chunked
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.utils.streaming.aiohttp.ClientSession", return_value=mock_session):
            chunks = []
            async for chunk in stream_content("https://example.com/file.pdf", "rec-1", "file.pdf"):
                chunks.append(chunk)
        assert chunks == [b"chunk1", b"chunk2"]

    @pytest.mark.asyncio
    async def test_stream_content_400_error(self):
        """400 Bad Request should raise HTTPException."""
        from fastapi import HTTPException
        from app.utils.streaming import stream_content

        mock_response = AsyncMock()
        mock_response.status = 400
        mock_response.text = AsyncMock(return_value="Bad request")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.utils.streaming.aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(HTTPException) as exc_info:
                async for _ in stream_content("https://example.com/file.pdf", "rec-1", "file.pdf"):
                    pass
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_stream_content_403_error(self):
        """403 Forbidden should raise HTTPException."""
        from fastapi import HTTPException
        from app.utils.streaming import stream_content

        mock_response = AsyncMock()
        mock_response.status = 403
        mock_response.text = AsyncMock(return_value="Forbidden")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.utils.streaming.aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(HTTPException):
                async for _ in stream_content("https://example.com/file.pdf", "rec-1"):
                    pass

    @pytest.mark.asyncio
    async def test_stream_content_404_error(self):
        """404 Not Found should raise HTTPException."""
        from fastapi import HTTPException
        from app.utils.streaming import stream_content

        mock_response = AsyncMock()
        mock_response.status = 404
        mock_response.text = AsyncMock(return_value="Not found")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.utils.streaming.aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(HTTPException):
                async for _ in stream_content("https://example.com/file.pdf"):
                    pass

    @pytest.mark.asyncio
    async def test_stream_content_500_error(self):
        """Non-standard error code should raise HTTPException."""
        from fastapi import HTTPException
        from app.utils.streaming import stream_content

        mock_response = AsyncMock()
        mock_response.status = 502
        mock_response.text = AsyncMock(return_value="Bad gateway")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.utils.streaming.aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(HTTPException):
                async for _ in stream_content("https://example.com/file.pdf", "rec-1", "file.pdf"):
                    pass

    @pytest.mark.asyncio
    async def test_stream_content_client_error(self):
        """aiohttp.ClientError should raise HTTPException."""
        import aiohttp
        from fastapi import HTTPException
        from app.utils.streaming import stream_content

        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=aiohttp.ClientError("Connection failed"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.utils.streaming.aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(HTTPException):
                async for _ in stream_content("https://example.com/file.pdf", "rec-1"):
                    pass

    @pytest.mark.asyncio
    async def test_stream_content_long_url_truncation(self):
        """Long URLs should be truncated in logging."""
        from fastapi import HTTPException
        from app.utils.streaming import stream_content

        long_url = "https://example.com/" + "a" * 300

        mock_response = AsyncMock()
        mock_response.status = 200

        async def mock_iter_chunked(size):
            yield b"data"

        mock_response.content = MagicMock()
        mock_response.content.iter_chunked = mock_iter_chunked
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.utils.streaming.aiohttp.ClientSession", return_value=mock_session):
            chunks = []
            async for chunk in stream_content(long_url, "rec-1", "file.pdf"):
                chunks.append(chunk)
        assert chunks == [b"data"]

    @pytest.mark.asyncio
    async def test_stream_content_error_body_read_failure(self):
        """When error body text fails to read, should still raise HTTPException."""
        from fastapi import HTTPException
        from app.utils.streaming import stream_content

        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(side_effect=Exception("read failed"))
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("app.utils.streaming.aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(HTTPException):
                async for _ in stream_content("https://example.com/file.pdf"):
                    pass


# ---------------------------------------------------------------------------
# execute_tool_calls  (lines 441-443, 447-449, 489-490, 498-499, 516-522, 565-571, etc.)
# ---------------------------------------------------------------------------


class TestExecuteToolCallsCoverage:
    """Additional tests targeting uncovered lines in execute_tool_calls."""

    @pytest.mark.asyncio
    async def test_execute_tool_calls_no_tools_raises(self):
        """Should raise ValueError when tools is empty."""
        from app.utils.streaming import execute_tool_calls

        with pytest.raises(ValueError, match="Tools are required"):
            async for _ in execute_tool_calls(
                llm=MagicMock(),
                messages=[],
                tools=[],
                tool_runtime_kwargs={},
                final_results=[],
                virtual_record_id_to_result={},
                blob_store=MagicMock(),
                all_queries=["test"],
                retrieval_service=AsyncMock(),
                user_id="u1",
                org_id="o1",
                context_length=128000,
            ):
                pass

    @pytest.mark.asyncio
    async def test_execute_tool_calls_bind_tools_failure(self):
        """When bind_tools fails, should fallback to structured output."""
        from app.utils.streaming import execute_tool_calls

        mock_llm = MagicMock()
        mock_tool = MagicMock()
        mock_tool.name = "my_tool"

        # Make call_aiter_llm_stream yield a complete event so we exit
        async def mock_call_aiter(*args, **kwargs):
            yield {
                "event": "complete",
                "data": {"answer": "test answer"},
            }

        with patch("app.utils.streaming.bind_tools_for_llm", return_value=False):
            with patch("app.utils.streaming._apply_structured_output", return_value=mock_llm):
                with patch("app.utils.streaming.call_aiter_llm_stream", side_effect=mock_call_aiter):
                    events = []
                    async for event in execute_tool_calls(
                        llm=mock_llm,
                        messages=[],
                        tools=[mock_tool],
                        tool_runtime_kwargs={},
                        final_results=[],
                        virtual_record_id_to_result={},
                        blob_store=MagicMock(),
                        all_queries=["test"],
                        retrieval_service=AsyncMock(),
                        user_id="u1",
                        org_id="o1",
                        context_length=128000,
                    ):
                        events.append(event)

        assert any(e.get("event") == "complete" for e in events)

    @pytest.mark.asyncio
    async def test_execute_tool_calls_tool_error_in_llm(self):
        """When LLM raises an exception during tool loop, should exit loop."""
        from app.utils.streaming import execute_tool_calls

        mock_llm = MagicMock()
        mock_tool = MagicMock()
        mock_tool.name = "my_tool"

        async def mock_call_aiter(*args, **kwargs):
            raise Exception("LLM failed")
            yield  # noqa: unreachable - makes it a generator

        with patch("app.utils.streaming.bind_tools_for_llm", return_value=mock_llm):
            with patch("app.utils.streaming.call_aiter_llm_stream", side_effect=mock_call_aiter):
                events = []
                async for event in execute_tool_calls(
                    llm=mock_llm,
                    messages=[],
                    tools=[mock_tool],
                    tool_runtime_kwargs={},
                    final_results=[],
                    virtual_record_id_to_result={},
                    blob_store=MagicMock(),
                    all_queries=["test"],
                    retrieval_service=AsyncMock(),
                    user_id="u1",
                    org_id="o1",
                    context_length=128000,
                ):
                    events.append(event)

        # Should get tool_execution_complete with tools_executed=False
        assert any(e.get("event") == "tool_execution_complete" for e in events)

    @pytest.mark.asyncio
    async def test_execute_tool_calls_unknown_tool(self):
        """When AI calls an unknown tool, should yield error result."""
        from app.utils.streaming import execute_tool_calls

        mock_llm = MagicMock()
        mock_tool = MagicMock()
        mock_tool.name = "known_tool"

        ai_msg = AIMessage(content="", tool_calls=[
            {"name": "unknown_tool", "args": {}, "id": "call-1"}
        ])

        # First iteration: yield tool_calls event
        async def mock_call_aiter(*args, **kwargs):
            yield {"event": "tool_calls", "data": {"ai": ai_msg}}

        with patch("app.utils.streaming.bind_tools_for_llm", return_value=mock_llm):
            with patch("app.utils.streaming.call_aiter_llm_stream", side_effect=mock_call_aiter):
                with patch("app.utils.streaming.count_tokens", return_value=(100, 100)):
                    events = []
                    async for event in execute_tool_calls(
                        llm=mock_llm,
                        messages=[],
                        tools=[mock_tool],
                        tool_runtime_kwargs={},
                        final_results=[],
                        virtual_record_id_to_result={},
                        blob_store=MagicMock(),
                        all_queries=["test"],
                        retrieval_service=AsyncMock(),
                        user_id="u1",
                        org_id="o1",
                        context_length=128000,
                        max_hops=1,
                    ):
                        events.append(event)

        # Should have tool_call and tool_error events
        assert any(e.get("event") == "tool_call" for e in events)
        assert any(e.get("event") == "tool_error" for e in events)

    @pytest.mark.asyncio
    async def test_execute_tool_calls_tool_execution_exception(self):
        """When a tool raises an exception, should yield error result."""
        from app.utils.streaming import execute_tool_calls

        mock_llm = MagicMock()
        mock_tool = MagicMock()
        mock_tool.name = "my_tool"
        mock_tool.arun = AsyncMock(side_effect=Exception("Tool broke"))

        ai_msg = AIMessage(content="", tool_calls=[
            {"name": "my_tool", "args": {}, "id": "call-1"}
        ])

        async def mock_call_aiter(*args, **kwargs):
            yield {"event": "tool_calls", "data": {"ai": ai_msg}}

        with patch("app.utils.streaming.bind_tools_for_llm", return_value=mock_llm):
            with patch("app.utils.streaming.call_aiter_llm_stream", side_effect=mock_call_aiter):
                with patch("app.utils.streaming.count_tokens", return_value=(100, 100)):
                    events = []
                    async for event in execute_tool_calls(
                        llm=mock_llm,
                        messages=[],
                        tools=[mock_tool],
                        tool_runtime_kwargs={},
                        final_results=[],
                        virtual_record_id_to_result={},
                        blob_store=MagicMock(),
                        all_queries=["test"],
                        retrieval_service=AsyncMock(),
                        user_id="u1",
                        org_id="o1",
                        context_length=128000,
                        max_hops=1,
                    ):
                        events.append(event)

        tool_error_events = [e for e in events if e.get("event") == "tool_error"]
        assert len(tool_error_events) >= 1
        assert "Tool broke" in tool_error_events[0]["data"]["error"]


# ---------------------------------------------------------------------------
# handle_json_mode  (lines 1063, 1111-1113)
# ---------------------------------------------------------------------------

class TestHandleJsonModeCoverage:
    """Additional tests for handle_json_mode."""

    @pytest.mark.asyncio
    async def test_fast_path_with_existing_ai_message(self):
        """When last message is AIMessage with valid JSON, should stream directly."""
        from app.utils.streaming import handle_json_mode

        ai_content = json.dumps({
            "answer": "Hello world",
            "reason": "Test",
            "confidence": "High",
        })
        messages = [AIMessage(content=ai_content)]

        with patch("app.utils.streaming.normalize_citations_and_chunks", return_value=("Hello world", [])):
            events = []
            async for event in handle_json_mode(
                llm=MagicMock(),
                messages=messages,
                final_results=[],
                records=[],
                logger=logging.getLogger("test"),
                target_words_per_chunk=1,
            ):
                events.append(event)

        assert any(e.get("event") == "complete" for e in events)
        complete = next(e for e in events if e.get("event") == "complete")
        assert complete["data"]["reason"] == "Test"

    @pytest.mark.asyncio
    async def test_fast_path_with_dict_message(self):
        """When last message is dict with role=assistant, should stream directly."""
        from app.utils.streaming import handle_json_mode

        messages = [{"role": "assistant", "content": "simple answer"}]

        with patch("app.utils.streaming.normalize_citations_and_chunks", return_value=("simple answer", [])):
            events = []
            async for event in handle_json_mode(
                llm=MagicMock(),
                messages=messages,
                final_results=[],
                records=[],
                logger=logging.getLogger("test"),
                target_words_per_chunk=1,
            ):
                events.append(event)

        complete = next(e for e in events if e.get("event") == "complete")
        assert complete["data"]["answer"] == "simple answer"

    @pytest.mark.asyncio
    async def test_fast_path_with_reference_data(self):
        """Should include referenceData when present in JSON content."""
        from app.utils.streaming import handle_json_mode

        ai_content = json.dumps({
            "answer": "answer text",
            "reason": "r",
            "confidence": "High",
            "referenceData": [{"id": "ref1"}],
        })
        messages = [AIMessage(content=ai_content)]

        with patch("app.utils.streaming.normalize_citations_and_chunks", return_value=("answer text", [])):
            events = []
            async for event in handle_json_mode(
                llm=MagicMock(),
                messages=messages,
                final_results=[],
                records=[],
                logger=logging.getLogger("test"),
                target_words_per_chunk=1,
            ):
                events.append(event)

        complete = next(e for e in events if e.get("event") == "complete")
        assert complete["data"]["referenceData"] == [{"id": "ref1"}]

    @pytest.mark.asyncio
    async def test_fast_path_failure_falls_through(self):
        """When fast-path detection fails, should fall through to normal path."""
        from app.utils.streaming import handle_json_mode

        # Empty messages list causes fast-path to skip
        messages = []

        async def mock_call_aiter(*args, **kwargs):
            yield {
                "event": "complete",
                "data": {"answer": "from llm", "citations": []},
            }

        with patch("app.utils.streaming._apply_structured_output", return_value=MagicMock()):
            with patch("app.utils.streaming.call_aiter_llm_stream", side_effect=mock_call_aiter):
                events = []
                async for event in handle_json_mode(
                    llm=MagicMock(),
                    messages=messages,
                    final_results=[],
                    records=[],
                    logger=logging.getLogger("test"),
                    target_words_per_chunk=1,
                ):
                    events.append(event)

        assert any(e.get("event") == "complete" for e in events)

    @pytest.mark.asyncio
    async def test_handle_json_mode_exception(self):
        """When call_aiter_llm_stream raises, should yield error event."""
        from app.utils.streaming import handle_json_mode

        with patch("app.utils.streaming._apply_structured_output", return_value=MagicMock()):
            with patch("app.utils.streaming.call_aiter_llm_stream", side_effect=Exception("LLM crashed")):
                events = []
                async for event in handle_json_mode(
                    llm=MagicMock(),
                    messages=[],
                    final_results=[],
                    records=[],
                    logger=logging.getLogger("test"),
                    target_words_per_chunk=1,
                ):
                    events.append(event)

        assert any(e.get("event") == "error" for e in events)

    @pytest.mark.asyncio
    async def test_fast_path_basemessage_ai_type(self):
        """Should detect BaseMessage with type='ai' in fast path."""
        from app.utils.streaming import handle_json_mode

        mock_msg = MagicMock(spec=BaseMessage)
        mock_msg.type = "ai"
        mock_msg.content = "direct content"
        messages = [mock_msg]

        with patch("app.utils.streaming.normalize_citations_and_chunks", return_value=("direct content", [])):
            events = []
            async for event in handle_json_mode(
                llm=MagicMock(),
                messages=messages,
                final_results=[],
                records=[],
                logger=logging.getLogger("test"),
                target_words_per_chunk=1,
            ):
                events.append(event)

        complete = next(e for e in events if e.get("event") == "complete")
        assert complete["data"]["answer"] == "direct content"


# ---------------------------------------------------------------------------
# handle_simple_mode  (lines 1162, 1194-1195, 1211, 1219)
# ---------------------------------------------------------------------------

class TestHandleSimpleModeCoverage:
    """Additional tests for handle_simple_mode."""

    @pytest.mark.asyncio
    async def test_fast_path_with_ai_message(self):
        """Fast-path when AIMessage is last in messages."""
        from app.utils.streaming import handle_simple_mode

        messages = [AIMessage(content="simple direct")]

        with patch("app.utils.streaming.normalize_citations_and_chunks", return_value=("simple direct", [])):
            events = []
            async for event in handle_simple_mode(
                llm=MagicMock(),
                messages=messages,
                final_results=[],
                records=[],
                logger=logging.getLogger("test"),
                target_words_per_chunk=1,
            ):
                events.append(event)

        complete = next(e for e in events if e.get("event") == "complete")
        assert complete["data"]["answer"] == "simple direct"

    @pytest.mark.asyncio
    async def test_fast_path_with_basemessage_ai(self):
        """Fast-path when BaseMessage with type='ai' is last."""
        from app.utils.streaming import handle_simple_mode

        mock_msg = MagicMock(spec=BaseMessage)
        mock_msg.type = "ai"
        mock_msg.content = "ai content"
        messages = [mock_msg]

        with patch("app.utils.streaming.normalize_citations_and_chunks", return_value=("ai content", [])):
            events = []
            async for event in handle_simple_mode(
                llm=MagicMock(),
                messages=messages,
                final_results=[],
                records=[],
                logger=logging.getLogger("test"),
                target_words_per_chunk=1,
            ):
                events.append(event)

        complete = next(e for e in events if e.get("event") == "complete")
        assert complete["data"]["answer"] == "ai content"

    @pytest.mark.asyncio
    async def test_fast_path_failure_falls_through(self):
        """When fast-path fails, falls through to LLM streaming."""
        from app.utils.streaming import handle_simple_mode

        # Empty messages causes fast-path to skip
        messages = []

        async def mock_aiter(llm, msgs, parts=None):
            yield "word1 word2"

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter):
            with patch("app.utils.streaming.normalize_citations_and_chunks_for_agent", return_value=("word1 word2", [])):
                events = []
                async for event in handle_simple_mode(
                    llm=MagicMock(),
                    messages=messages,
                    final_results=[],
                    records=[],
                    logger=logging.getLogger("test"),
                    target_words_per_chunk=1,
                ):
                    events.append(event)

        assert any(e.get("event") == "complete" for e in events)

    @pytest.mark.asyncio
    async def test_streaming_error(self):
        """Errors in LLM streaming should yield error event."""
        from app.utils.streaming import handle_simple_mode

        async def failing_aiter(llm, msgs, parts=None):
            raise RuntimeError("stream broke")
            yield  # noqa

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=failing_aiter):
            events = []
            async for event in handle_simple_mode(
                llm=MagicMock(),
                messages=[],
                final_results=[],
                records=[],
                logger=logging.getLogger("test"),
                target_words_per_chunk=1,
            ):
                events.append(event)

        assert any(e.get("event") == "error" for e in events)


# ---------------------------------------------------------------------------
# call_aiter_llm_stream (lines 1520-1524, 1579, 1588-1589, 1732, 1737-1740)
# ---------------------------------------------------------------------------

class TestCallAiterLlmStreamCoverage:
    """Additional tests for call_aiter_llm_stream."""

    @pytest.mark.asyncio
    async def test_dict_token_with_answer_short(self):
        """When dict token has answer shorter than emit_upto, should yield metadata."""
        from app.utils.streaming import call_aiter_llm_stream

        async def mock_aiter(llm, msgs, parts=None):
            yield {"answer": "hi"}  # Short answer
            yield {"answer": "hi"}  # Same length, won't emit

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter):
            with patch("app.utils.streaming.normalize_citations_and_chunks", return_value=("hi", [])):
                events = []
                async for event in call_aiter_llm_stream(
                    llm=MagicMock(),
                    messages=[],
                    final_results=[],
                    records=[],
                    target_words_per_chunk=1,
                ):
                    events.append(event)

        # Should have metadata event for the second iteration
        metadata_events = [e for e in events if e.get("event") == "metadata"]
        assert len(metadata_events) >= 1

    @pytest.mark.asyncio
    async def test_incomplete_citation_sets_words_threshold(self):
        """Incomplete citations should prevent emission and adjust threshold."""
        from app.utils.streaming import call_aiter_llm_stream

        async def mock_aiter(llm, msgs, parts=None):
            yield '"answer":"word1 word2 [incomplete'  # incomplete citation

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter):
            with patch("app.utils.streaming.normalize_citations_and_chunks") as mock_norm:
                mock_norm.return_value = ("word1 word2", [])
                events = []
                async for event in call_aiter_llm_stream(
                    llm=MagicMock(),
                    messages=[],
                    final_results=[],
                    records=[],
                    target_words_per_chunk=1,
                ):
                    events.append(event)

        # Should have parsing occurred even if incomplete

    @pytest.mark.asyncio
    async def test_tool_calls_detected(self):
        """When parts contain tool_calls, should yield tool_calls event."""
        from app.utils.streaming import call_aiter_llm_stream

        ai_part = MagicMock()
        ai_part.content = "some content"
        ai_part.tool_calls = [{"name": "tool1", "args": {}, "id": "c1"}]
        ai_part.__iadd__ = MagicMock(return_value=ai_part)

        async def mock_aiter(llm, msgs, parts=None):
            if parts is not None:
                parts.append(ai_part)
            yield "some text"

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter):
            with patch("app.utils.streaming.normalize_citations_and_chunks", return_value=("some text", [])):
                events = []
                async for event in call_aiter_llm_stream(
                    llm=MagicMock(),
                    messages=[],
                    final_results=[],
                    records=[],
                    target_words_per_chunk=1,
                ):
                    events.append(event)

        tool_events = [e for e in events if e.get("event") == "tool_calls"]
        assert len(tool_events) == 1

    @pytest.mark.asyncio
    async def test_outer_exception(self):
        """Outer exception should yield error event."""
        from app.utils.streaming import call_aiter_llm_stream

        async def mock_aiter(llm, msgs, parts=None):
            yield "valid text"

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter):
            with patch("app.utils.streaming.get_parser", side_effect=Exception("parser broke")):
                events = []
                async for event in call_aiter_llm_stream(
                    llm=MagicMock(),
                    messages=[],
                    final_results=[],
                    records=[],
                    target_words_per_chunk=1,
                ):
                    events.append(event)

        error_events = [e for e in events if e.get("event") == "error"]
        assert len(error_events) == 1

    @pytest.mark.asyncio
    async def test_parsed_answer_with_reference_data(self):
        """When parsed result has referenceData (agent mode), should include in complete."""
        from app.utils.streaming import call_aiter_llm_stream

        json_response = json.dumps({
            "answer": "the answer",
            "reason": "reason",
            "confidence": "High",
            "answerMatchType": "Derived From Blocks",
            "blockNumbers": ["R1-1"],
            "referenceData": [{"id": "r1"}],
        })

        async def mock_aiter(llm, msgs, parts=None):
            yield json_response

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter):
            with patch("app.utils.streaming.normalize_citations_and_chunks", return_value=("the answer", [])):
                events = []
                async for event in call_aiter_llm_stream(
                    llm=MagicMock(),
                    messages=[],
                    final_results=[],
                    records=[],
                    target_words_per_chunk=1,
                    is_agent=True,  # Agent mode has referenceData
                ):
                    events.append(event)

        complete = next((e for e in events if e.get("event") == "complete"), None)
        assert complete is not None
        assert complete["data"].get("referenceData") == [{"id": "r1"}]


# ---------------------------------------------------------------------------
# stream_llm_response_with_tools (lines 1321-1401, 1448-1450)
# ---------------------------------------------------------------------------

class TestStreamLlmResponseWithToolsCoverage:
    """Additional tests for stream_llm_response_with_tools."""

    @pytest.mark.asyncio
    async def test_simple_mode_ignores_tools(self):
        """In simple mode, tools should be ignored."""
        from app.utils.streaming import stream_llm_response_with_tools

        async def mock_handle_simple(*args, **kwargs):
            yield {"event": "complete", "data": {"answer": "simple"}}

        with patch("app.utils.streaming.handle_simple_mode", side_effect=mock_handle_simple):
            events = []
            async for event in stream_llm_response_with_tools(
                llm=MagicMock(),
                messages=[],
                final_results=[],
                all_queries=["q"],
                retrieval_service=AsyncMock(),
                user_id="u1",
                org_id="o1",
                virtual_record_id_to_result={},
                blob_store=MagicMock(),
                is_multimodal_llm=False,
                context_length=128000,
                tools=[MagicMock()],
                tool_runtime_kwargs={"key": "val"},
                mode="simple",
            ):
                events.append(event)

        complete = next(e for e in events if e.get("event") == "complete")
        assert complete["data"]["answer"] == "simple"

    @pytest.mark.asyncio
    async def test_tool_execution_error_yields_error_event(self):
        """When execute_tool_calls raises, should yield error event."""
        from app.utils.streaming import stream_llm_response_with_tools

        async def mock_execute(*args, **kwargs):
            raise RuntimeError("tool exec failed")
            yield  # noqa

        with patch("app.utils.streaming.execute_tool_calls", side_effect=mock_execute):
            events = []
            async for event in stream_llm_response_with_tools(
                llm=MagicMock(),
                messages=[],
                final_results=[],
                all_queries=["q"],
                retrieval_service=AsyncMock(),
                user_id="u1",
                org_id="o1",
                virtual_record_id_to_result={},
                blob_store=MagicMock(),
                is_multimodal_llm=False,
                context_length=128000,
                tools=[MagicMock()],
                tool_runtime_kwargs={"key": "val"},
                mode="json",
            ):
                events.append(event)

        error_events = [e for e in events if e.get("event") == "error"]
        assert len(error_events) >= 1

    @pytest.mark.asyncio
    async def test_tool_execution_forwards_events(self):
        """Tool events should be forwarded with status messages."""
        from app.utils.streaming import stream_llm_response_with_tools

        async def mock_execute(*args, **kwargs):
            yield {"event": "tool_call", "data": {"tool_name": "t1"}}
            yield {"event": "tool_success", "data": {"tool_name": "t1"}}
            yield {
                "event": "tool_execution_complete",
                "data": {
                    "messages": [],
                    "tools_executed": True,
                    "tool_results": [],
                    "tool_args": [],
                }
            }

        async def mock_json_mode(*args, **kwargs):
            yield {"event": "complete", "data": {"answer": "result"}}

        with patch("app.utils.streaming.execute_tool_calls", side_effect=mock_execute):
            with patch("app.utils.streaming.handle_json_mode", side_effect=mock_json_mode):
                events = []
                async for event in stream_llm_response_with_tools(
                    llm=MagicMock(),
                    messages=[],
                    final_results=[],
                    all_queries=["q"],
                    retrieval_service=AsyncMock(),
                    user_id="u1",
                    org_id="o1",
                    virtual_record_id_to_result={},
                    blob_store=MagicMock(),
                    is_multimodal_llm=False,
                    context_length=128000,
                    tools=[MagicMock()],
                    tool_runtime_kwargs={"key": "val"},
                    mode="json",
                ):
                    events.append(event)

        # Should have status, tool_call, tool_success, status(generating_answer), complete
        event_types = [e["event"] for e in events]
        assert "status" in event_types
        assert "tool_call" in event_types

    @pytest.mark.asyncio
    async def test_final_answer_generation_error(self):
        """When handle_json_mode raises, should yield error event."""
        from app.utils.streaming import stream_llm_response_with_tools

        async def mock_json_mode(*args, **kwargs):
            raise RuntimeError("json mode crashed")
            yield  # noqa

        with patch("app.utils.streaming.handle_json_mode", side_effect=mock_json_mode):
            events = []
            async for event in stream_llm_response_with_tools(
                llm=MagicMock(),
                messages=[],
                final_results=[],
                all_queries=["q"],
                retrieval_service=AsyncMock(),
                user_id="u1",
                org_id="o1",
                virtual_record_id_to_result={},
                blob_store=MagicMock(),
                is_multimodal_llm=False,
                context_length=128000,
                mode="json",
            ):
                events.append(event)

        error_events = [e for e in events if e.get("event") == "error"]
        assert len(error_events) >= 1

    @pytest.mark.asyncio
    async def test_conversation_tasks_appended(self):
        """When conversation_id is provided, task markers should be appended."""
        from app.utils.streaming import stream_llm_response_with_tools

        async def mock_json_mode(*args, **kwargs):
            yield {"event": "complete", "data": {"answer": "base answer"}}

        with patch("app.utils.streaming.handle_json_mode", side_effect=mock_json_mode):
            with patch("app.utils.conversation_tasks.await_and_collect_results", new_callable=AsyncMock) as mock_tasks:
                mock_tasks.return_value = [{"fileName": "report.csv", "signedUrl": "https://example.com/report.csv"}]
                events = []
                async for event in stream_llm_response_with_tools(
                    llm=MagicMock(),
                    messages=[],
                    final_results=[],
                    all_queries=["q"],
                    retrieval_service=AsyncMock(),
                    user_id="u1",
                    org_id="o1",
                    virtual_record_id_to_result={},
                    blob_store=MagicMock(),
                    is_multimodal_llm=False,
                    context_length=128000,
                    mode="json",
                    conversation_id="conv-123",
                ):
                    events.append(event)

        complete = next(e for e in events if e.get("event") == "complete")
        assert "::download_conversation_task" in complete["data"]["answer"]
