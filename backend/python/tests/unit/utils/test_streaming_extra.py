"""Additional unit tests for app.utils.streaming targeting uncovered branches."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage
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
    """Cover line 1091 — CONFIDENCE_DELIMITER_RE match returns trimmed answer."""

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


class TestCleanupContentNonString:
    """Cover lines 2013-2018 — non-str input gets coerced with a warning."""

    def test_list_input_coerced(self):
        result = cleanup_content([1, 2, 3])
        assert isinstance(result, str)
        assert "1" in result and "2" in result and "3" in result

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
# execute_tool_calls: HTTPException on retrieval status 500 (line 685)
# ---------------------------------------------------------------------------

class TestExecuteToolCallsStatusException:
    """Cover lines 685-692 — HTTPException raised when retrieval returns 500."""

    @pytest.mark.asyncio
    async def test_http_exception_on_status_500(self):
        from app.utils.streaming import execute_tool_calls

        mock_tool = MagicMock()
        mock_tool.name = "search"
        mock_tool.arun = AsyncMock(return_value={
            "ok": True,
            "records": [{"virtual_record_id": "vr1", "content": "x" * 100}],
        })

        ai_mock = MagicMock()
        ai_mock.content = ""
        ai_mock.tool_calls = [{"name": "search", "args": {"q": "hi"}, "id": "c1"}]

        async def mock_stream(*a, **kw):
            yield {"event": "tool_calls", "data": {"ai": ai_mock}}

        retrieval_service = MagicMock()
        retrieval_service.search_with_filters = AsyncMock(return_value={
            "searchResults": [],
            "status_code": 500,
            "status": "error",
            "message": "Internal error",
        })

        with patch("app.utils.streaming.call_aiter_llm_stream", side_effect=mock_stream), \
             patch("app.utils.streaming.bind_tools_for_llm", return_value=MagicMock()), \
             patch("app.utils.streaming.count_tokens", return_value=(200000, 200000)), \
             patch("app.utils.streaming.record_to_message_content", return_value=([], MagicMock())), \
             patch("app.utils.streaming.supports_human_message_after_tool", return_value=False):
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                async for _ in execute_tool_calls(
                    llm=MagicMock(),
                    messages=[],
                    tools=[mock_tool],
                    tool_runtime_kwargs={},
                    final_results=[],
                    virtual_record_id_to_result={},
                    blob_store=MagicMock(),
                    all_queries=["q"],
                    retrieval_service=retrieval_service,
                    user_id="u1",
                    org_id="o1",
                    context_length=1000,
                ):
                    pass

            assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# execute_tool_calls: fetch_full_record with not_available_ids
# ---------------------------------------------------------------------------

class TestExecuteToolCallsFetchFullRecord:
    """Cover lines 711-717 — fetch_full_record flattens contents and appends
    a 'not available' note for missing record ids."""

    @pytest.mark.asyncio
    async def test_fetch_full_record_not_available_note_appended(self):
        from app.utils.streaming import execute_tool_calls

        mock_tool = MagicMock()
        mock_tool.name = "fetch_full_record"
        mock_tool.arun = AsyncMock(return_value={
            "ok": True,
            "records": [{"virtual_record_id": "vr1"}],
            "not_available_ids": ["rec-missing-1"],
        })

        ai_tool_call = MagicMock()
        ai_tool_call.content = ""
        ai_tool_call.tool_calls = [
            {"name": "fetch_full_record", "args": {"id": "x"}, "id": "c1"},
        ]

        ai_done = MagicMock()
        ai_done.content = ""
        ai_done.tool_calls = []

        stream_state = {"count": 0}

        async def mock_stream(*a, **kw):
            if stream_state["count"] == 0:
                stream_state["count"] += 1
                yield {"event": "tool_calls", "data": {"ai": ai_tool_call}}
            else:
                yield {"event": "tool_calls", "data": {"ai": ai_done}}

        with patch("app.utils.streaming.call_aiter_llm_stream", side_effect=mock_stream), \
             patch("app.utils.streaming.bind_tools_for_llm", return_value=MagicMock()), \
             patch("app.utils.streaming.count_tokens", return_value=(100, 100)), \
             patch("app.utils.streaming.record_to_message_content", return_value=([{"type": "text", "text": "snippet"}], MagicMock())), \
             patch("app.utils.streaming.supports_human_message_after_tool", return_value=False):

            events = []
            async for event in execute_tool_calls(
                llm=MagicMock(),
                messages=[],
                tools=[mock_tool],
                tool_runtime_kwargs={},
                final_results=[],
                virtual_record_id_to_result={},
                blob_store=MagicMock(),
                all_queries=["q"],
                retrieval_service=AsyncMock(),
                user_id="u1",
                org_id="o1",
                context_length=128000,
            ):
                events.append(event)

        complete = next(
            (e for e in events if e.get("event") == "tool_execution_complete"),
            None,
        )
        assert complete is not None
        msgs = complete["data"]["messages"]
        tool_messages = [m for m in msgs if hasattr(m, "tool_call_id")]
        # At least one tool message should carry a list of content items, with
        # a "not available" note.
        assert any(
            isinstance(tm.content, list)
            and any("not available" in str(x) for x in tm.content)
            for tm in tool_messages
        )


# ---------------------------------------------------------------------------
# Citation reflection flows in stream_llm_response (agent JSON + simple)
# ---------------------------------------------------------------------------

class TestStreamLlmResponseCitationReflection:
    """Cover lines 864-882, 917-934, 1007-1024."""

    @pytest.mark.asyncio
    async def test_agent_json_reflection_on_hallucinated_urls(self):
        from app.utils.streaming import stream_llm_response

        call_count = {"n": 0}

        async def mock_aiter(llm, messages, *a, **kw):
            if call_count["n"] == 0:
                call_count["n"] += 1
                js = '{"answer":"see [1](http://bad.com/record/x/preview#blockIndex=1)"}'
                for ch in js:
                    yield ch
            else:
                for ch in '{"answer":"ok"}':
                    yield ch

        detect_calls = {"n": 0}

        def mock_detect(answer, *a, **kw):
            detect_calls["n"] += 1
            if detect_calls["n"] == 1:
                return ["http://bad.com/record/x/preview#blockIndex=1"]
            return []

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter), \
             patch("app.utils.streaming.detect_hallucinated_citation_urls", side_effect=mock_detect), \
             patch("app.utils.streaming.normalize_citations_and_chunks_for_agent", return_value=("ok", [])):

            events = []
            async for event in stream_llm_response(
                MagicMock(),
                [],
                [],
                logging.getLogger("test"),
                target_words_per_chunk=10,
            ):
                events.append(event)

        restreams = [e for e in events if e.get("event") == "restreaming"]
        assert len(restreams) >= 1
        statuses = [e for e in events if e.get("event") == "status"]
        assert any(s["data"].get("message", "").startswith("Verifying") for s in statuses)

    @pytest.mark.asyncio
    async def test_agent_json_fallback_citation_reflection(self):
        """Malformed JSON → fallback path → hallucinated URLs → reflection."""
        from app.utils.streaming import stream_llm_response

        call_count = {"n": 0}

        async def mock_aiter(llm, messages, *a, **kw):
            if call_count["n"] == 0:
                call_count["n"] += 1
                # Malformed — no closing brace. Also no "answer" key so
                # answer_buf stays empty; but json.loads still fails either
                # way, so the fallback path is exercised.
                text = 'totally [1](http://bad.com/record/x/preview#blockIndex=1) bad'
                for ch in text:
                    yield ch
            else:
                for ch in '{"answer":"clean"}':
                    yield ch

        detect_calls = {"n": 0}

        def mock_detect(answer, *a, **kw):
            detect_calls["n"] += 1
            if detect_calls["n"] == 1:
                return ["http://bad.com/record/x/preview#blockIndex=1"]
            return []

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter), \
             patch("app.utils.streaming.detect_hallucinated_citation_urls", side_effect=mock_detect), \
             patch("app.utils.streaming.normalize_citations_and_chunks_for_agent", return_value=("clean", [])):

            events = []
            async for event in stream_llm_response(
                MagicMock(),
                [],
                [],
                logging.getLogger("test"),
                target_words_per_chunk=10,
            ):
                events.append(event)

        restreams = [e for e in events if e.get("event") == "restreaming"]
        assert len(restreams) >= 1

    @pytest.mark.asyncio
    async def test_simple_mode_citation_reflection(self):
        from app.utils.streaming import stream_llm_response

        call_count = {"n": 0}

        async def mock_aiter(llm, messages, *a, **kw):
            if call_count["n"] == 0:
                call_count["n"] += 1
                yield "Hello [1](http://bad.com/record/x/preview#blockIndex=1)"
            else:
                yield "Hello clean"

        detect_calls = {"n": 0}

        def mock_detect(answer, *a, **kw):
            detect_calls["n"] += 1
            if detect_calls["n"] == 1:
                return ["http://bad.com/record/x/preview#blockIndex=1"]
            return []

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter), \
             patch("app.utils.streaming.detect_hallucinated_citation_urls", side_effect=mock_detect), \
             patch("app.utils.streaming.normalize_citations_and_chunks_for_agent", return_value=("Hello clean", [])):

            events = []
            async for event in stream_llm_response(
                MagicMock(),
                [],
                [],
                logging.getLogger("test"),
                target_words_per_chunk=10,
            ):
                events.append(event)

        restreams = [e for e in events if e.get("event") == "restreaming"]
        assert len(restreams) >= 1


# ---------------------------------------------------------------------------
# Chatbot JSON citation reflection — call_aiter_llm_stream
# ---------------------------------------------------------------------------

class TestCallAiterLlmStreamCitationReflection:
    """Cover lines 1866-1890 (fallback) and 1922-1946 (success) reflection paths."""

    @pytest.mark.asyncio
    async def test_json_success_reflection_on_hallucinated_urls(self):
        from app.utils.streaming import call_aiter_llm_stream

        call_count = {"n": 0}

        good_reason = "reason text"
        # Valid chatbot JSON schema requires answer, reason, confidence, answerMatchType.
        js_bad = (
            '{"answer":"text [1](http://bad/record/x/preview#blockIndex=0)",'
            '"reason":"' + good_reason + '",'
            '"confidence":"High",'
            '"answerMatchType":"Derived From Blocks"}'
        )
        js_good = (
            '{"answer":"ok",'
            '"reason":"' + good_reason + '",'
            '"confidence":"High",'
            '"answerMatchType":"Derived From Blocks"}'
        )

        async def mock_aiter(llm, messages, *a, **kw):
            if call_count["n"] == 0:
                call_count["n"] += 1
                yield js_bad
            else:
                yield js_good

        detect_calls = {"n": 0}

        def mock_detect(answer, *a, **kw):
            detect_calls["n"] += 1
            if detect_calls["n"] == 1:
                return ["http://bad/record/x/preview#blockIndex=0"]
            return []

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter), \
             patch("app.utils.streaming.detect_hallucinated_citation_urls", side_effect=mock_detect), \
             patch("app.utils.streaming.normalize_citations_and_chunks", return_value=("ok", [])):

            events = []
            async for event in call_aiter_llm_stream(
                llm=MagicMock(),
                messages=[],
                final_results=[],
                records=[],
                target_words_per_chunk=10,
                original_llm=MagicMock(),
            ):
                events.append(event)

        restreams = [e for e in events if e.get("event") == "restreaming"]
        assert len(restreams) >= 1
        complete = [e for e in events if e.get("event") == "complete"]
        assert len(complete) == 1

    @pytest.mark.asyncio
    async def test_json_fallback_reflection_on_hallucinated_urls(self):
        """Cover lines 1866-1890 — JSON parsing fails but answer_buf has URLs."""
        from app.utils.streaming import call_aiter_llm_stream

        call_count = {"n": 0}

        async def mock_aiter(llm, messages, *a, **kw):
            if call_count["n"] == 0:
                call_count["n"] += 1
                # Malformed JSON but answer_buf will contain the stream text
                # between the first two quotes after "answer":.
                yield '{"answer":"text [1](http://bad/record/x/preview#blockIndex=0)"'
            else:
                # Also malformed so parse fails again, then fallback hit.
                yield '{"answer":"clean"'

        detect_calls = {"n": 0}

        def mock_detect(answer, *a, **kw):
            detect_calls["n"] += 1
            if detect_calls["n"] == 1:
                return ["http://bad/record/x/preview#blockIndex=0"]
            return []

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter), \
             patch("app.utils.streaming.detect_hallucinated_citation_urls", side_effect=mock_detect), \
             patch("app.utils.streaming.normalize_citations_and_chunks", return_value=("clean", [])):

            events = []
            async for event in call_aiter_llm_stream(
                llm=MagicMock(),
                messages=[],
                final_results=[],
                records=[],
                target_words_per_chunk=10,
                max_reflection_retries=0,  # force straight to fallback
                original_llm=MagicMock(),
            ):
                events.append(event)

        restreams = [e for e in events if e.get("event") == "restreaming"]
        assert len(restreams) >= 1


# ---------------------------------------------------------------------------
# Chatbot simple mode citation reflection (call_aiter_llm_stream_simple)
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
# stream_llm_response: CITE_BLOCK_RE match extends char_end (lines 819, 972)
# ---------------------------------------------------------------------------

class TestStreamLlmResponseCiteBlockExtends:
    """Cover lines 818-819 (agent JSON) and 971-972 (agent simple)."""

    @pytest.mark.asyncio
    async def test_cite_block_extends_char_end_agent_json(self):
        from app.utils.streaming import stream_llm_response

        # CITE_BLOCK_RE matches "\s*[...](...)" after a word ends. So the
        # answer should be "word [x](y) rest". Yield the whole JSON in a
        # single token so that the word-loop sees the citation immediately
        # after the first word.
        answer_with_cite = 'first [1](ref1) second word tail'
        js = '{"answer":"' + answer_with_cite + '"}'

        async def mock_aiter(llm, messages, *a, **kw):
            yield js

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter), \
             patch("app.utils.streaming.detect_hallucinated_citation_urls", return_value=[]), \
             patch("app.utils.streaming.normalize_citations_and_chunks_for_agent", return_value=(answer_with_cite, [])):

            events = []
            async for event in stream_llm_response(
                MagicMock(),
                [],
                [],
                logging.getLogger("test"),
                target_words_per_chunk=1,
            ):
                events.append(event)

        assert any(e.get("event") == "complete" for e in events)

    @pytest.mark.asyncio
    async def test_cite_block_extends_char_end_agent_simple(self):
        from app.utils.streaming import stream_llm_response

        text = 'first [1](ref1) second third more'

        async def mock_aiter(llm, messages, *a, **kw):
            yield text

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter), \
             patch("app.utils.streaming.detect_hallucinated_citation_urls", return_value=[]), \
             patch("app.utils.streaming.normalize_citations_and_chunks_for_agent", return_value=(text, [])):

            events = []
            async for event in stream_llm_response(
                MagicMock(),
                [],
                [],
                logging.getLogger("test"),
                target_words_per_chunk=1,
            ):
                events.append(event)

        assert any(e.get("event") == "complete" for e in events)


# ---------------------------------------------------------------------------
# call_aiter_llm_stream_simple: CITE_BLOCK_RE match (line 1539)
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
# call_aiter_llm_stream incomplete citation break (lines 1735-1736, 1742-1743)
# ---------------------------------------------------------------------------

class TestCallAiterLlmStreamIncompleteCite:
    """Cover lines 1735-1736 (cite block match) and 1742-1743 (incomplete
    citation reset + break)."""

    @pytest.mark.asyncio
    async def test_incomplete_citation_breaks_and_resets(self):
        from app.utils.streaming import call_aiter_llm_stream

        async def mock_aiter(llm, messages, parts=None):
            # Token 1 ends mid-citation — INCOMPLETE_CITE_RE should match
            # "hello [R1" pattern when target_words_per_chunk=2 threshold hits.
            yield '"answer": "hello [R1'
            # Token 2 completes the citation + gives closing quote.
            yield '](ref1)","reason":"x","confidence":"High","answerMatchType":"Derived From Blocks"}'

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter), \
             patch("app.utils.streaming.normalize_citations_and_chunks", return_value=("hello [R1](ref1)", [])), \
             patch("app.utils.streaming.detect_hallucinated_citation_urls", return_value=[]):

            events = []
            async for event in call_aiter_llm_stream(
                llm=MagicMock(),
                messages=[],
                final_results=[],
                records=[],
                target_words_per_chunk=2,
            ):
                events.append(event)

        # Should complete normally — no uncaught errors.
        assert any(e.get("event") == "complete" for e in events)


class TestCallAiterLlmStreamReflectionNoOriginalLlm:
    """Cover lines 1880 and 1936 — retry_llm = llm else branch when
    original_llm is None."""

    @pytest.mark.asyncio
    async def test_json_success_reflection_without_original_llm(self):
        """Cover line 1936 — no original_llm ⇒ retry_llm defaults to llm."""
        from app.utils.streaming import call_aiter_llm_stream

        call_count = {"n": 0}

        js_bad = (
            '{"answer":"text [1](http://bad/record/x/preview#blockIndex=0)",'
            '"reason":"r","confidence":"High","answerMatchType":"Derived From Blocks"}'
        )
        js_good = (
            '{"answer":"ok","reason":"r","confidence":"High",'
            '"answerMatchType":"Derived From Blocks"}'
        )

        async def mock_aiter(llm, messages, *a, **kw):
            if call_count["n"] == 0:
                call_count["n"] += 1
                yield js_bad
            else:
                yield js_good

        detect_calls = {"n": 0}

        def mock_detect(answer, *a, **kw):
            detect_calls["n"] += 1
            if detect_calls["n"] == 1:
                return ["http://bad/record/x/preview#blockIndex=0"]
            return []

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter), \
             patch("app.utils.streaming.detect_hallucinated_citation_urls", side_effect=mock_detect), \
             patch("app.utils.streaming.normalize_citations_and_chunks", return_value=("ok", [])):

            events = []
            async for event in call_aiter_llm_stream(
                llm=MagicMock(),
                messages=[],
                final_results=[],
                records=[],
                target_words_per_chunk=10,
                original_llm=None,  # ← triggers retry_llm = llm branch
            ):
                events.append(event)

        assert any(e.get("event") == "restreaming" for e in events)
        assert any(e.get("event") == "complete" for e in events)

    @pytest.mark.asyncio
    async def test_json_fallback_reflection_without_original_llm(self):
        """Cover line 1880 — fallback path without original_llm."""
        from app.utils.streaming import call_aiter_llm_stream

        call_count = {"n": 0}

        async def mock_aiter(llm, messages, *a, **kw):
            if call_count["n"] == 0:
                call_count["n"] += 1
                # Malformed JSON so parsing fails; answer_buf will contain
                # the text between quotes.
                yield '{"answer":"text [1](http://bad/record/x/preview#blockIndex=0)"'
            else:
                yield '{"answer":"clean"'

        detect_calls = {"n": 0}

        def mock_detect(answer, *a, **kw):
            detect_calls["n"] += 1
            if detect_calls["n"] == 1:
                return ["http://bad/record/x/preview#blockIndex=0"]
            return []

        with patch("app.utils.streaming.aiter_llm_stream", side_effect=mock_aiter), \
             patch("app.utils.streaming.detect_hallucinated_citation_urls", side_effect=mock_detect), \
             patch("app.utils.streaming.normalize_citations_and_chunks", return_value=("clean", [])):

            events = []
            async for event in call_aiter_llm_stream(
                llm=MagicMock(),
                messages=[],
                final_results=[],
                records=[],
                target_words_per_chunk=10,
                max_reflection_retries=0,
                original_llm=None,  # ← triggers retry_llm = llm fallback branch
            ):
                events.append(event)

        restreams = [e for e in events if e.get("event") == "restreaming"]
        assert len(restreams) >= 1


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
    async def test_handle_json_mode_fast_path_forwards_vrid_map(self):
        from langchain_core.messages import AIMessage
        from app.utils.streaming import handle_json_mode

        vrid_map = {"vr1": {"id": "rec-1"}}
        messages = [AIMessage(content='{"answer":"hello","reason":"r","confidence":"High"}')]

        with patch("app.utils.streaming.normalize_citations_and_chunks", return_value=("hello", [])) as mock_norm:
            events = []
            async for event in handle_json_mode(
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
# execute_single_tool — lines 421-422, 437-440
# ---------------------------------------------------------------------------


class TestExecuteSingleToolBranches:
    """Cover execute_single_tool branches not hit by existing tests."""

    @pytest.mark.asyncio
    async def test_tool_name_not_in_valid_names_returns_error(self) -> None:
        """Lines 421-422: tool found but tool_name not in valid_tool_names → error dict."""
        from app.utils.streaming import execute_single_tool

        mock_tool = MagicMock()
        mock_tool.name = "search"

        result = await execute_single_tool(
            args={},
            tool=mock_tool,       # tool is not None
            tool_name="rogue",    # but "rogue" not in valid list
            call_id="c1",
            valid_tool_names=["search", "fetch"],
            tool_runtime_kwargs={},
        )

        assert result["ok"] is False
        assert "Invalid tool" in result["error"]
        assert result["tool_name"] == "rogue"
        assert result["call_id"] == "c1"

    @pytest.mark.asyncio
    async def test_tool_result_non_json_string_wrapped(self) -> None:
        """Lines 437-440: tool returns non-JSON string → wrapped in content dict."""
        from app.utils.streaming import execute_single_tool

        mock_tool = AsyncMock()
        mock_tool.name = "search"
        mock_tool.arun = AsyncMock(return_value="plain text result")

        result = await execute_single_tool(
            args={},
            tool=mock_tool,
            tool_name="search",
            call_id="c2",
            valid_tool_names=["search"],
            tool_runtime_kwargs={},
        )

        assert result["ok"] is True
        assert result["content"] == "plain text result"
        assert result["result_type"] == "content"
        assert result["tool_name"] == "search"
        assert result["call_id"] == "c2"

    @pytest.mark.asyncio
    async def test_tool_result_valid_json_string_parsed(self) -> None:
        """Lines 436-438: tool returns valid JSON string → parsed and enriched."""
        from app.utils.streaming import execute_single_tool

        mock_tool = AsyncMock()
        mock_tool.name = "search"
        mock_tool.arun = AsyncMock(return_value='{"ok": true, "records": []}')

        result = await execute_single_tool(
            args={},
            tool=mock_tool,
            tool_name="search",
            call_id="c3",
            valid_tool_names=["search"],
            tool_runtime_kwargs={},
        )

        assert result["ok"] is True
        assert "records" in result
        assert result["tool_name"] == "search"

    @pytest.mark.asyncio
    async def test_tool_arun_exception_returns_error_dict(self) -> None:
        """Lines 444-456: arun raises → error dict with ok=False."""
        from app.utils.streaming import execute_single_tool

        mock_tool = AsyncMock()
        mock_tool.name = "search"
        mock_tool.arun = AsyncMock(side_effect=RuntimeError("tool crashed"))

        result = await execute_single_tool(
            args={},
            tool=mock_tool,
            tool_name="search",
            call_id="c4",
            valid_tool_names=["search"],
            tool_runtime_kwargs={},
        )

        assert result["ok"] is False
        assert "tool crashed" in result["error"]
        assert result["tool_name"] == "search"
        assert result["call_id"] == "c4"

    @pytest.mark.asyncio
    async def test_unknown_tool_none_returns_error(self) -> None:
        """Lines 411-418: tool is None → error dict with 'Unknown tool'."""
        from app.utils.streaming import execute_single_tool

        result = await execute_single_tool(
            args={},
            tool=None,
            tool_name="missing_tool",
            call_id="c5",
            valid_tool_names=["search"],
            tool_runtime_kwargs={},
        )

        assert result["ok"] is False
        assert "Unknown tool" in result["error"]
        assert result["tool_name"] == "missing_tool"


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

        with pytest.raises(TypeError, match="Expected signed_url to be a string"):
            async for _ in stream_content(fake_coroutine()):
                pass
