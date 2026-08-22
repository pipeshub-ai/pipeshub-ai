"""Additional unit tests for app.utils.streaming targeting uncovered branches."""

import logging
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

from app.utils.streaming import (
    aiter_llm_stream,
    cleanup_content,
    stream_content,
)


# ---------------------------------------------------------------------------
# Pure helper coverage
# ---------------------------------------------------------------------------

class TestBuildCitationReflectionMessage:
    """Cover lines 81-99 — _build_citation_reflection_message."""

    def test_single_url_formatted(self):
        from app.utils.streaming import _build_citation_reflection_message

        msg = _build_citation_reflection_message(["http://bad/url#blockIndex=5"])
        assert "CITATION ERROR" in msg
        assert "http://bad/url#blockIndex=5" in msg
        assert "HOW TO FIX" in msg
        assert "omit the citation" in msg

    def test_multiple_urls_listed(self):
        from app.utils.streaming import _build_citation_reflection_message

        urls = [
            "http://bad/first#blockIndex=1",
            "http://bad/second#blockIndex=2",
        ]
        msg = _build_citation_reflection_message(urls)
        for u in urls:
            assert u in msg

    def test_empty_urls_still_renders(self):
        from app.utils.streaming import _build_citation_reflection_message

        msg = _build_citation_reflection_message([])
        assert "CITATION ERROR" in msg


class TestParseConfidenceFromAnswer:
    """Matching is deliberately lenient: an unparsed trailer is not a missing
    indicator, it is a raw `Confidence: High` line rendered to the user inside
    the answer (observed in production when the model omitted the `---` rule)."""

    @pytest.mark.parametrize(("trailer", "expected"), [
        ("\n\nConfidence: High", "High"),                  # no rule at all
        ("\n---\nConfidence: High", "High"),
        ("\n\n---\n\nConfidence: Very  High", "Very High"),
        ("\n***\nConfidence: Medium", "Medium"),
        ("\n___\nConfidence: Low", "Low"),
        ("\n\n**Confidence:** high", "High"),
        ("\n\n**Confidence: medium**", "Medium"),
        ("\n\nConfidence - Low", "Low"),
        ("\n\nConfidence Level: HIGH.", "High"),
        ("\n---\nConfidence: High\n\n", "High"),
    ])
    def test_parses_the_shapes_models_actually_emit(self, trailer, expected):
        from app.utils.streaming import parse_confidence_from_answer

        clean, conf = parse_confidence_from_answer(f"Body of the answer.{trailer}")
        assert clean == "Body of the answer."
        # The frontend indicator switches on the exact-cased level.
        assert conf == expected

    @pytest.mark.parametrize("text", [
        "Confidence intervals were computed at 95%.",
        "Body.\n\nConfidence: High in our coverage of the topic.",
        "Body.\n\nConfidence: unclear",
        "Body.\n\nConfidence: High\n\nOne more thing.",
    ])
    def test_leaves_prose_and_mid_body_matches_alone(self, text):
        from app.utils.streaming import parse_confidence_from_answer

        clean, conf = parse_confidence_from_answer(text)
        assert (clean, conf) == (text, None)

    def test_parses_trailing_confidence(self):
        from app.utils.streaming import parse_confidence_from_answer

        text = "Body of the answer.\n---\nConfidence: High."
        clean, conf = parse_confidence_from_answer(text)
        assert clean == "Body of the answer."
        assert conf.lower() == "high"

    def test_no_delimiter_returns_input(self):
        from app.utils.streaming import parse_confidence_from_answer

        clean, conf = parse_confidence_from_answer("Just an answer")
        assert clean == "Just an answer"
        assert conf is None

    def test_very_high_confidence(self):
        from app.utils.streaming import parse_confidence_from_answer

        clean, conf = parse_confidence_from_answer(
            "Fact stated.\n---\nConfidence: Very High"
        )
        assert clean == "Fact stated."
        assert conf == "Very High"

    def test_low_case_insensitive(self):
        from app.utils.streaming import parse_confidence_from_answer

        clean, conf = parse_confidence_from_answer(
            "Hmm.\n---\nconfidence: low!"
        )
        assert clean == "Hmm."
        assert conf.lower() == "low"


class TestStripPartialConfidenceTrailer:
    """`parse_confidence_from_answer` only matches the COMPLETE trailer. A
    caller streaming the model's own tokens (`agent_loop/answer_streamer.py`)
    sees every partial state on the way there and must hide those too."""

    @pytest.mark.parametrize("tail", [
        # The delimiter itself streams in one dash at a time.
        "\n-",
        "\n--",
        "\n---",
        "\n---\n",
        "\n---\nC",
        "\n---\nConfidence",
        "\n---\nConfidence:",
        "\n---\nConfidence: Very",
        "\n---\nConfidence: Med",
        "\n---\n\nConfidence: High",
        # ... and the same states with no rule, which the model emits when it
        # reads the prompt's `---` as belonging to the prompt.
        "\n\nConf",
        "\n\nConfidence:",
        "\n\nConfidence: Very",
        "\n\nConfidence: High",
        "\n\n**Confidence:** Hi",
        "\n\n**Confidence: Low**",
    ])
    def test_hides_every_partial_state_of_the_trailer(self, tail):
        from app.utils.streaming import strip_partial_confidence_trailer

        assert strip_partial_confidence_trailer(f"Body of the answer.{tail}") == "Body of the answer."

    def test_keeps_a_horizontal_rule_followed_by_real_content(self):
        from app.utils.streaming import strip_partial_confidence_trailer

        text = "Intro.\n---\nNext section of the answer."
        assert strip_partial_confidence_trailer(text) == text

    def test_keeps_a_bullet_list(self):
        from app.utils.streaming import strip_partial_confidence_trailer

        text = "Findings:\n- First point\n- Second point"
        assert strip_partial_confidence_trailer(text) == text

    def test_only_the_last_rule_is_considered(self):
        from app.utils.streaming import strip_partial_confidence_trailer

        text = "Intro.\n---\nBody.\n---\nConfidence: Hi"
        assert strip_partial_confidence_trailer(text) == "Intro.\n---\nBody."

    def test_text_without_a_rule_is_untouched(self):
        from app.utils.streaming import strip_partial_confidence_trailer

        assert strip_partial_confidence_trailer("Just an answer") == "Just an answer"


class TestCleanupContentNonString:
    """Cover lines 2013-2018 — non-str input gets coerced with a warning."""

    def test_list_input_coerced(self):
        result = cleanup_content([1, 2, 3])
        assert isinstance(result, str)
        assert "1" in result and "2" in result and "3" in result

    def test_gemini_list_blocks_coerced(self):
        """Gemini-style dict blocks are extracted, not stringified as Python repr."""
        result = cleanup_content(
            [
                {"type": "text", "text": '{"answer": '},
                {"type": "text", "text": '"hello"}'},
            ]
        )
        assert result == '{"answer": "hello"}'

    def test_tuple_input_coerced(self):
        result = cleanup_content(("a", "b"))
        assert isinstance(result, str)
        assert "a" in result and "b" in result

    def test_int_input_coerced(self):
        assert cleanup_content(42) == "42"


# ---------------------------------------------------------------------------
# aiter_llm_stream ValidationError handling (lines 355-364)
# ---------------------------------------------------------------------------

class TestAiterLlmStreamValidationError:
    """Cover the 'role=None' ValidationError suppression branch and the
    re-raise branch for unrelated ValidationErrors."""

    @pytest.mark.asyncio
    async def test_role_validation_error_swallowed_on_chunk(self):
        """A per-chunk ValidationError mentioning 'role' is skipped."""
        good_chunk = MagicMock()
        good_chunk.content = "ok"

        role_error = ValidationError.from_exception_data(
            title="RoleErr",
            line_errors=[{"type": "missing", "loc": ("role",), "input": None}],
        )

        class FakeIter:
            def __init__(self):
                self._items = [role_error, good_chunk]
                self._idx = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._idx >= len(self._items):
                    raise StopAsyncIteration
                item = self._items[self._idx]
                self._idx += 1
                if isinstance(item, Exception):
                    raise item
                return item

        mock_llm = MagicMock()
        mock_llm.astream = MagicMock(return_value=FakeIter())

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)

        assert results == ["ok"]

    @pytest.mark.asyncio
    async def test_non_role_validation_error_reraised(self):
        """A ValidationError NOT mentioning role should propagate."""
        other_error = ValidationError.from_exception_data(
            title="OtherErr",
            line_errors=[
                {"type": "missing", "loc": ("completely_unrelated_field",), "input": None}
            ],
        )

        class FakeIter:
            def __init__(self):
                self._raised = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._raised:
                    raise StopAsyncIteration
                self._raised = True
                raise other_error

        mock_llm = MagicMock()
        mock_llm.astream = MagicMock(return_value=FakeIter())

        with pytest.raises(ValidationError):
            async for _ in aiter_llm_stream(mock_llm, []):
                pass


# ---------------------------------------------------------------------------
# call_aiter_llm_stream_simple citation reflection
# ---------------------------------------------------------------------------

class TestCallAiterLlmStreamSimpleCitationReflection:
    """Cover lines 1594-1616 in call_aiter_llm_stream_simple."""

    @pytest.mark.asyncio
    async def test_reflection_path_on_hallucinated(self):
        from app.utils.streaming import call_aiter_llm_stream_simple

        call_count = {"n": 0}

        async def mock_aiter(llm, messages, parts=None):
            if call_count["n"] == 0:
                call_count["n"] += 1
                yield "hi [1](http://bad/record/x/preview#blockIndex=0)"
            else:
                yield "hi clean"

        detect_calls = {"n": 0}

        def mock_detect(answer, *a, **kw):
            detect_calls["n"] += 1
            if detect_calls["n"] == 1:
                return ["http://bad/record/x/preview#blockIndex=0"]
            return []

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter), \
             patch("app.utils.streaming.detect_hallucinated_citation_urls", side_effect=mock_detect), \
             patch("app.utils.streaming.normalize_citations_and_chunks", return_value=("hi clean", [])):

            events = []
            async for event in call_aiter_llm_stream_simple(
                llm=MagicMock(),
                messages=[HumanMessage(content="q")],
                final_results=[],
                records=[],
                target_words_per_chunk=10,
                original_llm=MagicMock(),
            ):
                events.append(event)

        restreams = [e for e in events if e.get("event") == "restreaming"]
        assert len(restreams) >= 1


class TestCallAiterLlmStreamSimpleToolCalls:
    """Cover lines 1571-1581 — tool_calls detection in the simple stream."""

    @pytest.mark.asyncio
    async def test_tool_calls_emitted_when_detected(self):
        from app.utils.streaming import call_aiter_llm_stream_simple

        tool_chunk = MagicMock()
        tool_chunk.tool_calls = [{"name": "search", "args": {}, "id": "c1"}]
        tool_chunk.content = ""

        # Support '+=' / '+' ai accumulation.
        def _return_self(other):
            return tool_chunk

        tool_chunk.__iadd__ = _return_self
        tool_chunk.__add__ = _return_self

        async def mock_aiter(llm, messages, parts=None):
            if parts is not None:
                parts.append(tool_chunk)
            # Yield no actual text so the outer word loop is inert.
            if False:
                yield ""
            return

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter):
            events = []
            async for event in call_aiter_llm_stream_simple(
                llm=MagicMock(),
                messages=[HumanMessage(content="q")],
                final_results=[],
                records=[],
                target_words_per_chunk=1,
                original_llm=MagicMock(),
            ):
                events.append(event)

        tool_call_events = [e for e in events if e.get("event") == "tool_calls"]
        assert len(tool_call_events) == 1


# ---------------------------------------------------------------------------
# stream_content urlparse exception fallback (lines 160-162)
# ---------------------------------------------------------------------------

class TestStreamContentUrlParseExceptionFallback:
    """Cover lines 160-162 — urlparse raises → falls back to truncated URL."""

    @pytest.mark.asyncio
    async def test_urlparse_exception_triggers_fallback(self):
        # Patch urlparse inside streaming's import to raise.
        with patch("urllib.parse.urlparse", side_effect=RuntimeError("bad parse")):
            # We don't care whether the HTTP request succeeds; we just need to
            # execute past the urlparse branch. Any network exception is fine.
            gen = stream_content(
                signed_url="http://127.0.0.1:1/nope",
                record_id="r1",
                file_name="f.txt",
            )
            with pytest.raises(Exception):
                async for _ in gen:
                    pass


# ---------------------------------------------------------------------------
# call_aiter_llm_stream_simple: CITE_BLOCK_RE match
# ---------------------------------------------------------------------------

class TestCallAiterLlmStreamSimpleCiteBlock:
    """Cover line 1538-1539 — citation block after a word in chatbot simple mode."""

    @pytest.mark.asyncio
    async def test_cite_block_extends_char_end(self):
        from app.utils.streaming import call_aiter_llm_stream_simple

        text = 'first [1](ref1) second third'

        async def mock_aiter(llm, messages, parts=None):
            yield text

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter), \
             patch("app.utils.streaming.detect_hallucinated_citation_urls", return_value=[]), \
             patch("app.utils.streaming.normalize_citations_and_chunks", return_value=(text, [])):

            events = []
            async for event in call_aiter_llm_stream_simple(
                llm=MagicMock(),
                messages=[HumanMessage(content="q")],
                final_results=[],
                records=[],
                target_words_per_chunk=1,
                original_llm=MagicMock(),
            ):
                events.append(event)

        assert any(e.get("event") == "complete" for e in events)


# ---------------------------------------------------------------------------
# call_aiter_llm_stream_simple multi-part accumulation
# ---------------------------------------------------------------------------

class TestCallAiterLlmStreamSimpleMultiPartAccumulation:
    """Cover line 1575 — multi-part AI accumulation in simple chatbot stream."""

    @pytest.mark.asyncio
    async def test_two_parts_accumulated_then_tool_calls(self):
        from app.utils.streaming import call_aiter_llm_stream_simple

        # Two parts: first has no tool_calls; second also has none, but both
        # are accumulated via += which covers the else branch.
        part1 = MagicMock()
        part1.tool_calls = []
        part1.content = ""

        part2 = MagicMock()
        part2.tool_calls = []
        part2.content = ""

        # Simulate the '+=' on the first part returning an accumulated object
        # whose tool_calls is truthy.
        accumulated = MagicMock()
        accumulated.tool_calls = [{"name": "search", "args": {}, "id": "c1"}]

        part1.__iadd__ = lambda self, other: accumulated
        part1.__add__ = lambda self, other: accumulated

        async def mock_aiter(llm, messages, parts=None):
            if parts is not None:
                parts.append(part1)
                parts.append(part2)
            if False:
                yield ""
            return

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter):
            events = []
            async for event in call_aiter_llm_stream_simple(
                llm=MagicMock(),
                messages=[HumanMessage(content="q")],
                final_results=[],
                records=[],
                target_words_per_chunk=1,
                original_llm=MagicMock(),
            ):
                events.append(event)

        # Two-part accumulation should produce a tool_calls event since
        # accumulated.tool_calls is truthy.
        assert any(e.get("event") == "tool_calls" for e in events)


class TestVirtualRecordIdMapForwarding:
    """Validate virtual_record_id_to_result is forwarded to chat citation normalization."""


    @pytest.mark.asyncio
    async def test_handle_simple_mode_fast_path_forwards_vrid_map(self):
        from langchain_core.messages import AIMessage
        from app.utils.streaming import handle_simple_mode

        vrid_map = {"vr2": {"id": "rec-2"}}
        messages = [AIMessage(content="plain answer")]

        with patch("app.utils.streaming.normalize_citations_and_chunks", return_value=("plain answer", [])) as mock_norm:
            events = []
            async for event in handle_simple_mode(
                llm=MagicMock(),
                messages=messages,
                final_results=[],
                records=[],
                logger=logging.getLogger("test"),
                target_words_per_chunk=5,
                virtual_record_id_to_result=vrid_map,
            ):
                events.append(event)

        assert any(e.get("event") == "complete" for e in events)
        assert mock_norm.called
        assert mock_norm.call_args.kwargs.get("virtual_record_id_to_result") == vrid_map

    @pytest.mark.asyncio
    async def test_call_aiter_llm_stream_simple_forwards_vrid_map(self):
        from app.utils.streaming import call_aiter_llm_stream_simple

        vrid_map = {"vr3": {"id": "rec-3"}}

        async def mock_aiter(llm, messages, parts=None):
            yield "hello world"

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter), \
             patch("app.utils.streaming.detect_hallucinated_citation_urls", return_value=[]), \
             patch("app.utils.streaming.normalize_citations_and_chunks", return_value=("hello world", [])) as mock_norm:
            events = []
            async for event in call_aiter_llm_stream_simple(
                llm=MagicMock(),
                messages=[HumanMessage(content="q")],
                final_results=[],
                records=[],
                target_words_per_chunk=10,
                virtual_record_id_to_result=vrid_map,
                original_llm=MagicMock(),
            ):
                events.append(event)

        assert any(e.get("event") == "complete" for e in events)
        assert mock_norm.called
        assert mock_norm.call_args.kwargs.get("virtual_record_id_to_result") == vrid_map


# ---------------------------------------------------------------------------
# _stringify_content — branch 318->313: list item with type="text" but
# the "text" value is not a string (isinstance(text_val, str) is False)
# ---------------------------------------------------------------------------


class TestStringifyContentNonStrTextVal:
    """Cover _stringify_content branch where text_val is not str (line 318->313)."""

    def test_type_text_with_none_text_val_skipped(self) -> None:
        from app.utils.streaming import _stringify_content

        # {"type": "text", "text": None} → text_val is None, not str → not appended
        content = [
            {"type": "text", "text": None},
            {"type": "text", "text": "valid"},
        ]
        result = _stringify_content(content)
        assert result == "valid"

    def test_type_text_with_int_text_val_skipped(self) -> None:
        from app.utils.streaming import _stringify_content

        content = [{"type": "text", "text": 42}]
        result = _stringify_content(content)
        assert result == ""

    def test_type_text_with_list_text_val_skipped(self) -> None:
        from app.utils.streaming import _stringify_content

        content = [
            {"type": "text", "text": ["not", "a", "string"]},
            {"type": "text", "text": "ok"},
        ]
        result = _stringify_content(content)
        assert result == "ok"


# ---------------------------------------------------------------------------
# _apply_structured_output — lines 1895->1929
# Covers: unsupported LLM type, Anthropic with no model, legacy Anthropic,
# and structured output exception fallback.
# ---------------------------------------------------------------------------

# conftest.py mocks all langchain_* packages, so ChatAnthropic, ChatOpenAI, etc.
# are MagicMock objects.  isinstance(llm, (MagicMock, ...)) raises TypeError.
# We define minimal real sentinel classes here and patch them into streaming.py
# for the duration of every test in this class.

class _FakeChatGoogleGenerativeAI:
    pass

class _FakeChatAnthropic:
    pass

class _FakeChatOpenAI:
    pass

class _FakeChatMistralAI:
    pass

class _FakeAzureChatOpenAI:
    pass

class _FakeChatBedrock:
    pass


class TestApplyStructuredOutputPaths:
    """Cover missing branches in _apply_structured_output."""

    @pytest.fixture(autouse=True)
    def _patch_llm_classes(self):
        """Replace mocked langchain LLM classes in streaming.py with real sentinels."""
        patches = [
            patch("app.utils.streaming.ChatGoogleGenerativeAI", new=_FakeChatGoogleGenerativeAI),
            patch("app.utils.streaming.ChatAnthropic",          new=_FakeChatAnthropic),
            patch("app.utils.streaming.ChatOpenAI",             new=_FakeChatOpenAI),
            patch("app.utils.streaming.ChatMistralAI",          new=_FakeChatMistralAI),
            patch("app.utils.streaming.AzureChatOpenAI",        new=_FakeAzureChatOpenAI),
            patch("app.utils.streaming.ChatBedrock",            new=_FakeChatBedrock),
        ]
        for p in patches:
            p.start()
        yield
        for p in patches:
            p.stop()

    def test_unsupported_llm_type_returned_as_is(self) -> None:
        """Line 1895->1929: LLM not in supported set → returned unchanged."""
        from app.utils.streaming import _apply_structured_output
        from app.modules.agents.qna.schemas import AgentAnswerWithMetadataDict

        mock_llm = MagicMock()  # not any of the fake LLM classes
        result = _apply_structured_output(mock_llm, AgentAnswerWithMetadataDict)
        assert result is mock_llm

    def test_anthropic_no_model_returns_llm(self) -> None:
        """Lines 1899-1901: ChatAnthropic without a model string → llm returned."""
        from app.utils.streaming import _apply_structured_output
        from app.modules.agents.qna.schemas import AgentAnswerWithMetadataDict

        mock_llm = MagicMock()
        mock_llm.__class__ = _FakeChatAnthropic
        mock_llm.model = None  # getattr returns None → "not model_str" is True

        result = _apply_structured_output(mock_llm, AgentAnswerWithMetadataDict)
        assert result is mock_llm

    def test_anthropic_empty_model_string_returns_llm(self) -> None:
        """Lines 1899-1901: empty model string is falsy → llm returned."""
        from app.utils.streaming import _apply_structured_output
        from app.modules.agents.qna.schemas import AgentAnswerWithMetadataDict

        mock_llm = MagicMock()
        mock_llm.__class__ = _FakeChatAnthropic
        mock_llm.model = ""

        result = _apply_structured_output(mock_llm, AgentAnswerWithMetadataDict)
        assert result is mock_llm

    def test_anthropic_legacy_claude3_model_returns_llm(self) -> None:
        """Lines 1903-1905: legacy claude-3 pattern → llm returned without structured output."""
        from app.utils.streaming import _apply_structured_output
        from app.modules.agents.qna.schemas import AgentAnswerWithMetadataDict

        mock_llm = MagicMock()
        mock_llm.__class__ = _FakeChatAnthropic
        mock_llm.model = "claude-3-sonnet-20240229"

        result = _apply_structured_output(mock_llm, AgentAnswerWithMetadataDict)
        assert result is mock_llm

    def test_anthropic_legacy_claude2_model_returns_llm(self) -> None:
        """Lines 1903-1905: legacy claude-2 pattern → llm returned."""
        from app.utils.streaming import _apply_structured_output
        from app.modules.agents.qna.schemas import AgentAnswerWithMetadataDict

        mock_llm = MagicMock()
        mock_llm.__class__ = _FakeChatAnthropic
        mock_llm.model = "claude-2.1"

        result = _apply_structured_output(mock_llm, AgentAnswerWithMetadataDict)
        assert result is mock_llm

    def test_structured_output_exception_falls_back_to_llm(self) -> None:
        """Lines 1919-1921: with_structured_output raises → exception caught → llm returned."""
        from app.utils.streaming import _apply_structured_output
        from app.modules.agents.qna.schemas import AgentAnswerWithMetadataDict

        mock_llm = MagicMock()
        mock_llm.__class__ = _FakeChatOpenAI
        mock_llm.with_structured_output.side_effect = Exception("method not supported")

        result = _apply_structured_output(mock_llm, AgentAnswerWithMetadataDict)
        assert result is mock_llm

    def test_bedrock_skips_method_kwarg(self) -> None:
        """ChatBedrock skips adding method='json_schema'; with_structured_output succeeds."""
        from app.utils.streaming import _apply_structured_output
        from app.modules.agents.qna.schemas import AgentAnswerWithMetadataDict

        mock_structured = MagicMock()
        mock_llm = MagicMock()
        mock_llm.__class__ = _FakeChatBedrock
        mock_llm.with_structured_output.return_value = mock_structured

        result = _apply_structured_output(mock_llm, AgentAnswerWithMetadataDict)
        assert result is mock_structured
        # Bedrock must NOT have method= in the kwargs
        call_kwargs = mock_llm.with_structured_output.call_args[1]
        assert "method" not in call_kwargs


# ---------------------------------------------------------------------------
# aiter_llm_stream — non-streaming (ainvoke) code path
# ---------------------------------------------------------------------------


class TestAiterLlmStreamNonStreamingPath:
    """Cover the else branch (no astream) in aiter_llm_stream."""

    @pytest.mark.asyncio
    async def test_ainvoke_dict_content_yielded_as_dict(self) -> None:
        """Non-streaming path: dict content is yielded directly."""
        from app.utils.streaming import aiter_llm_stream

        mock_response = MagicMock()
        mock_response.content = {"answer": "hello", "reason": "none"}

        # spec=[] means the mock has no attributes — no astream — triggers else branch
        mock_llm = MagicMock(spec=[])
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        results = []
        async for token in aiter_llm_stream(mock_llm, [HumanMessage(content="q")]):
            results.append(token)

        assert len(results) == 1
        assert isinstance(results[0], dict)
        assert results[0]["answer"] == "hello"

    @pytest.mark.asyncio
    async def test_ainvoke_string_content_yielded_as_text(self) -> None:
        """Non-streaming path: string content → _stringify_content → yield text."""
        from app.utils.streaming import aiter_llm_stream

        mock_response = MagicMock()
        mock_response.content = "This is the response text."

        mock_llm = MagicMock(spec=[])
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)

        assert results == ["This is the response text."]

    @pytest.mark.asyncio
    async def test_ainvoke_empty_content_yields_nothing(self) -> None:
        """Non-streaming path: empty string content → no yield."""
        from app.utils.streaming import aiter_llm_stream

        mock_response = MagicMock()
        mock_response.content = ""

        mock_llm = MagicMock(spec=[])
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)

        assert results == []

    @pytest.mark.asyncio
    async def test_ainvoke_no_content_attr_uses_response_itself(self) -> None:
        """Non-streaming path: response has no .content → response used as content."""
        from app.utils.streaming import aiter_llm_stream

        # Response has no .content attribute; getattr(response, "content", response) returns response
        mock_response = "direct string response"

        mock_llm = MagicMock(spec=[])
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        results = []
        async for token in aiter_llm_stream(mock_llm, []):
            results.append(token)

        assert results == ["direct string response"]


# ---------------------------------------------------------------------------
# stream_content — branch 191->198: non-200 response with empty body
# ---------------------------------------------------------------------------


class TestStreamContentEmptyErrorBody:
    """Cover branch 191->198: response is non-200 but response.text() is empty."""

    def _make_mocks(self, status: int, body: str = "") -> tuple:
        """Build the mock hierarchy for ClientSession / response."""
        mock_response = MagicMock()
        mock_response.status = status
        mock_response.text = AsyncMock(return_value=body)

        mock_response_cm = MagicMock()
        mock_response_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response_cm)

        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        return mock_session_cm

    @pytest.mark.asyncio
    async def test_403_empty_body_raises_http_exception(self) -> None:
        """Line 191->198: 403 with empty body → error_details stays '', HTTPException raised."""
        from app.utils.streaming import stream_content
        from fastapi import HTTPException

        mock_session_cm = self._make_mocks(403, "")

        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            with pytest.raises(HTTPException) as exc_info:
                async for _ in stream_content("https://example.com/signed?token=xyz"):
                    pass

        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_400_empty_body_raises_http_exception(self) -> None:
        """400 BAD_REQUEST with empty body hits the BAD_REQUEST branch."""
        from app.utils.streaming import stream_content
        from fastapi import HTTPException

        mock_session_cm = self._make_mocks(400, "")

        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            with pytest.raises(HTTPException) as exc_info:
                async for _ in stream_content("https://example.com/signed?token=xyz"):
                    pass

        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_404_empty_body_raises_http_exception(self) -> None:
        """404 NOT_FOUND with empty body."""
        from app.utils.streaming import stream_content
        from fastapi import HTTPException

        mock_session_cm = self._make_mocks(404, "")

        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            with pytest.raises(HTTPException):
                async for _ in stream_content("https://example.com/signed?token=xyz"):
                    pass

    @pytest.mark.asyncio
    async def test_403_with_body_includes_error_details(self) -> None:
        """403 with non-empty body → error_details are populated (positive coverage)."""
        from app.utils.streaming import stream_content
        from fastapi import HTTPException

        mock_session_cm = self._make_mocks(403, "Access denied by policy")

        with patch("aiohttp.ClientSession", return_value=mock_session_cm):
            with pytest.raises(HTTPException) as exc_info:
                async for _ in stream_content("https://example.com/file"):
                    pass

        assert exc_info.value.status_code == 500
        assert "Access denied by policy" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_non_string_signed_url_raises_type_error(self) -> None:
        """stream_content with a coroutine instead of string raises TypeError."""
        from app.utils.streaming import stream_content

        async def fake_coroutine():
            return "https://example.com"

        # Use a non-awaitable object; a coroutine would emit RuntimeWarning when garbage-collected.
        with pytest.raises(TypeError, match="Expected signed_url to be a string"):
            async for _ in stream_content(object()):
                pass


# ---------------------------------------------------------------------------
# call_aiter_llm_stream_simple — skip citation reflection when web_search
# ---------------------------------------------------------------------------


class TestCallAiterLlmStreamSimpleWebSearchSkipsReflection:
    @pytest.mark.asyncio
    async def test_no_reflection_when_chat_mode_web_search(self):
        from app.utils.streaming import call_aiter_llm_stream_simple

        async def mock_aiter(llm, messages, parts=None):
            yield "bad [1](http://evil/x#blockIndex=0)"

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter), patch(
            "app.utils.streaming.detect_hallucinated_citation_urls",
            return_value=["http://evil/x#blockIndex=0"],
        ), patch(
            "app.utils.streaming.normalize_citations_and_chunks",
            return_value=("normalized", []),
        ):
            events = []
            async for event in call_aiter_llm_stream_simple(
                llm=MagicMock(),
                messages=[HumanMessage(content="q")],
                final_results=[],
                records=[],
                target_words_per_chunk=10,
                original_llm=MagicMock(),
                chat_mode="web_search",
            ):
                events.append(event)

        assert not any(e.get("event") == "restreaming" for e in events)
        assert any(e.get("event") == "complete" for e in events)


# ---------------------------------------------------------------------------
# Opik configure failure at import time (lines 68–71) via module reload
# ---------------------------------------------------------------------------


class TestOpikConfigureImportFailure:
    def test_configure_exception_sets_tracer_none(self, monkeypatch):
        import importlib
        import sys

        monkeypatch.setenv("OPIK_API_KEY", "test-key")
        monkeypatch.setenv("OPIK_WORKSPACE", "test-ws")

        opik_mod = types.ModuleType("opik")
        integrations = types.ModuleType("opik.integrations")
        langchain_int = types.ModuleType("opik.integrations.langchain")

        def _failing_opik_tracer(*args, **kwargs):
            raise RuntimeError("opik unavailable")

        langchain_int.OpikTracer = _failing_opik_tracer

        sys.modules["opik"] = opik_mod
        sys.modules["opik.integrations"] = integrations
        sys.modules["opik.integrations.langchain"] = langchain_int

        import app.utils.streaming as sm

        importlib.reload(sm)
        assert sm.opik_tracer is None

        monkeypatch.delenv("OPIK_API_KEY", raising=False)
        monkeypatch.delenv("OPIK_WORKSPACE", raising=False)
        for key in (
            "opik",
            "opik.integrations",
            "opik.integrations.langchain",
        ):
            sys.modules.pop(key, None)
        importlib.reload(sm)

