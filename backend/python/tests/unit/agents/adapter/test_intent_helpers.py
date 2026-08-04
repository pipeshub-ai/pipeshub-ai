"""Tests for intent.py helper functions: ``_extract_whole_document_and_clean``,
``_extract_route_and_clean``, ``_decision_from_text``, ``_build_intent_prompt``,
and the async ``parse_intent_and_route`` / ``parse_intent`` entry points."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.agent_loop.intent import (
    IntentRouteDecision,
    _build_intent_prompt,
    _decision_from_text,
    _extract_route_and_clean,
    _extract_whole_document_and_clean,
    _fallback_decision,
    _parse_clarify_block,
    parse_intent,
    parse_intent_and_route,
)


class TestExtractWholeDocumentAndClean:
    def test_present_yes(self) -> None:
        text = "Some analysis\nWHOLE_DOCUMENT: yes\nMore text"
        value, cleaned = _extract_whole_document_and_clean(text)
        assert value == "yes"
        assert "WHOLE_DOCUMENT" not in cleaned
        assert "Some analysis" in cleaned
        assert "More text" in cleaned

    def test_present_no(self) -> None:
        text = "Analysis\nWHOLE_DOCUMENT: no"
        value, cleaned = _extract_whole_document_and_clean(text)
        assert value == "no"

    def test_absent(self) -> None:
        text = "No markers here"
        value, cleaned = _extract_whole_document_and_clean(text)
        assert value is None
        assert cleaned == text

    def test_case_insensitive(self) -> None:
        text = "whole_document: Yes"
        value, cleaned = _extract_whole_document_and_clean(text)
        assert value == "yes"


class TestExtractRouteAndClean:
    def test_standalone_line_marker(self) -> None:
        text = "Detailed analysis\nquick\nMore text"
        route, cleaned = _extract_route_and_clean(text)
        assert route == "quick"
        assert "quick" not in cleaned
        assert "Detailed analysis" in cleaned

    def test_backtick_wrapped(self) -> None:
        text = "Analysis\n`deep`\nEnd"
        route, cleaned = _extract_route_and_clean(text)
        assert route == "deep"

    def test_word_fallback_in_prose(self) -> None:
        text = "This is a react-based approach"
        route, cleaned = _extract_route_and_clean(text)
        assert route == "react"
        assert cleaned == text

    def test_no_route(self) -> None:
        text = "No tier word here"
        route, cleaned = _extract_route_and_clean(text)
        assert route is None
        assert cleaned == text

    def test_case_insensitive(self) -> None:
        text = "DEEP"
        route, cleaned = _extract_route_and_clean(text)
        assert route == "deep"


class TestDecisionFromText:
    def test_no_routing(self) -> None:
        decision = _decision_from_text("some text", include_routing=False)
        assert decision.rewritten_query == "some text"
        assert decision.route is None

    def test_with_routing_extracts_route(self) -> None:
        text = "Analysis of the request\nreact\nWHOLE_DOCUMENT: yes"
        decision = _decision_from_text(text, include_routing=True)
        assert decision.route == "react"
        assert decision.whole_document is True
        assert "WHOLE_DOCUMENT" not in decision.rewritten_query
        assert "react" not in decision.rewritten_query

    def test_clarify_block_populates_questions(self) -> None:
        text = (
            '```clarify\n'
            '{"user_intent": "unclear", "questions": ['
            '{"question": "What?", "multiSelect": false, '
            '"options": [{"label": "A"}, {"label": "B"}, {"label": "C"}]}'
            ']}\n```'
        )
        decision = _decision_from_text(text, include_routing=True)
        assert len(decision.clarifying_questions) == 1
        assert decision.rewritten_query == "unclear"
        assert decision.route is None

    def test_clarify_block_without_user_intent(self) -> None:
        text = (
            '```clarify\n'
            '{"questions": ['
            '{"question": "What?", "multiSelect": false, '
            '"options": [{"label": "A"}, {"label": "B"}, {"label": "C"}]}'
            ']}\n```'
        )
        decision = _decision_from_text(text, include_routing=True)
        assert len(decision.clarifying_questions) == 1
        assert decision.rewritten_query == ""

    def test_whole_document_no_when_absent(self) -> None:
        text = "Analysis\nquick"
        decision = _decision_from_text(text, include_routing=True)
        assert decision.whole_document is False


class TestFallbackDecision:
    def test_with_routing(self) -> None:
        decision = _fallback_decision("hello", include_routing=True, reason="test")
        assert decision.rewritten_query == "hello"
        assert decision.route == "react"
        assert decision.reasoning == "test"

    def test_without_routing(self) -> None:
        decision = _fallback_decision("hello", include_routing=False, reason="test")
        assert decision.route is None


class TestBuildIntentPrompt:
    def test_no_routing_excludes_tier_rubric(self) -> None:
        prompt = _build_intent_prompt(False, "", "", 0)
        assert "quick" not in prompt.lower() or "## quick" not in prompt
        assert "WHOLE_DOCUMENT" not in prompt

    def test_with_routing_includes_tier_rubric(self) -> None:
        prompt = _build_intent_prompt(True, "cap block", "sql override", 5)
        assert "WHOLE_DOCUMENT" in prompt
        assert "quick" in prompt.lower()

    def test_clarify_instructions_always_present(self) -> None:
        no_route = _build_intent_prompt(False, "", "", 0)
        with_route = _build_intent_prompt(True, "", "", 0)
        assert "clarify" in no_route.lower()
        assert "clarify" in with_route.lower()


class TestParseClarifyBlock:
    def test_valid_block(self) -> None:
        text = (
            '```clarify\n'
            '{"user_intent": "x", "questions": ['
            '{"question": "Q?", "multiSelect": false, '
            '"options": [{"label": "A"}, {"label": "B"}, {"label": "C"}]}'
            ']}\n```'
        )
        result = _parse_clarify_block(text)
        assert result is not None
        assert len(result) == 1

    def test_no_block(self) -> None:
        assert _parse_clarify_block("no clarify block") is None

    def test_invalid_json(self) -> None:
        assert _parse_clarify_block("```clarify\nnot json\n```") is None

    def test_no_questions_key(self) -> None:
        assert _parse_clarify_block('```clarify\n{"user_intent": "x"}\n```') is None

    def test_empty_questions(self) -> None:
        assert _parse_clarify_block('```clarify\n{"questions": []}\n```') is None


class TestDecisionFromTextClarifyJsonError:
    """Covers the except (ValueError, AttributeError, TypeError) branch at line 273."""

    def test_clarify_block_with_invalid_json_for_user_intent(self) -> None:
        from app.agents.actions.internal_tools.intrim_tools import AskUserQuestionItemInput

        text = "```clarify\nnot valid json at all\n```"
        fake_question = AskUserQuestionItemInput(
            question="What do you mean?",
            multiSelect=False,
            options=[{"label": "A"}, {"label": "B"}, {"label": "C"}],
        )
        with patch(
            "app.agents.agent_loop.intent._parse_clarify_block",
            return_value=[fake_question],
        ):
            decision = _decision_from_text(text, include_routing=True)
        assert len(decision.clarifying_questions) == 1
        assert decision.rewritten_query == ""
        assert decision.route is None


# ---------------------------------------------------------------------------
# parse_intent_and_route / parse_intent  (lines 332-437)
# ---------------------------------------------------------------------------

def _make_patches():
    """Shared mock setup for parse_intent_and_route tests."""
    return {
        "build_cap": patch(
            "app.agents.agent_loop.intent.build_capability_context",
            return_value=("cap", 1, [], []),
        ),
        "build_sql": patch(
            "app.agents.agent_loop.intent.build_sql_verify_override",
            return_value="",
        ),
        "build_prior": patch(
            "app.agents.agent_loop.intent.build_prior_routing_messages",
            new_callable=AsyncMock,
            return_value=[],
        ),
        "convert_msg": patch(
            "app.agents.agent_loop.intent.convert_message_from_langchain",
            side_effect=lambda m: m,
        ),
        "is_opik": patch(
            "app.agents.agent_loop.intent.is_opik_configured",
            return_value=False,
        ),
        "build_runtime": patch(
            "app.agents.agent_loop.intent.build_task_complete_runtime",
            return_value=MagicMock(),
        ),
        "run_single": patch(
            "app.agents.agent_loop.intent.run_text_single_shot",
            new_callable=AsyncMock,
            return_value="rewritten query text\nreact",
        ),
        "wrap_enabled": patch(
            "app.agents.agent_loop.intent.wrap_if_enabled",
            side_effect=lambda t, **kw: t,
        ),
        "lc_transport": patch(
            "app.agents.agent_loop.intent.LangChainTransport",
            return_value=MagicMock(),
        ),
    }


class TestParseIntentAndRouteEmptyQuery:
    @pytest.mark.asyncio
    async def test_empty_query_returns_fallback(self) -> None:
        result = await parse_intent_and_route(
            {"query": ""},
            logging.getLogger("test"),
            MagicMock(),
            include_routing=True,
        )
        assert result.reasoning == "empty query"
        assert result.route == "react"

    @pytest.mark.asyncio
    async def test_whitespace_query_returns_fallback(self) -> None:
        result = await parse_intent_and_route(
            {"query": "   "},
            logging.getLogger("test"),
            MagicMock(),
            include_routing=False,
        )
        assert result.reasoning == "empty query"
        assert result.route is None

    @pytest.mark.asyncio
    async def test_missing_query_key_returns_fallback(self) -> None:
        result = await parse_intent_and_route(
            {},
            logging.getLogger("test"),
            MagicMock(),
            include_routing=True,
        )
        assert result.reasoning == "empty query"


class TestParseIntentAndRouteHappyPath:
    @pytest.mark.asyncio
    async def test_happy_path_with_routing(self) -> None:
        patches = _make_patches()
        managers = {k: p.start() for k, p in patches.items()}
        try:
            result = await parse_intent_and_route(
                {"query": "find the Q3 report"},
                logging.getLogger("test"),
                MagicMock(),
                include_routing=True,
                opik_active=False,
                opik_project_name="test",
            )
            assert result.route == "react"
            assert "rewritten query text" in result.rewritten_query
            managers["run_single"].assert_awaited_once()
        finally:
            for p in patches.values():
                p.stop()

    @pytest.mark.asyncio
    async def test_happy_path_without_routing(self) -> None:
        patches = _make_patches()
        managers = {k: p.start() for k, p in patches.items()}
        try:
            result = await parse_intent_and_route(
                {"query": "find the Q3 report"},
                logging.getLogger("test"),
                MagicMock(),
                include_routing=False,
                opik_active=False,
                opik_project_name="test",
            )
            assert result.route is None
            assert result.rewritten_query.strip() != ""
        finally:
            for p in patches.values():
                p.stop()

    @pytest.mark.asyncio
    async def test_empty_rewritten_query_falls_back_to_user_query(self) -> None:
        patches = _make_patches()
        managers = {k: p.start() for k, p in patches.items()}
        managers["run_single"].return_value = "   "
        try:
            result = await parse_intent_and_route(
                {"query": "original query"},
                logging.getLogger("test"),
                MagicMock(),
                include_routing=False,
                opik_active=False,
                opik_project_name="test",
            )
            assert result.rewritten_query == "original query"
        finally:
            for p in patches.values():
                p.stop()


class TestParseIntentAndRouteErrorPaths:
    @pytest.mark.asyncio
    async def test_structured_single_shot_error_returns_fallback(self) -> None:
        from app.agent_loop_lib.agent.single_shot_runner import StructuredSingleShotError

        patches = _make_patches()
        managers = {k: p.start() for k, p in patches.items()}
        managers["run_single"].side_effect = StructuredSingleShotError("boom")
        try:
            result = await parse_intent_and_route(
                {"query": "test query"},
                logging.getLogger("test"),
                MagicMock(),
                include_routing=True,
                opik_active=False,
                opik_project_name="test",
            )
            assert result.route == "react"
            assert "intent parse failed" in result.reasoning
        finally:
            for p in patches.values():
                p.stop()

    @pytest.mark.asyncio
    async def test_generic_exception_returns_fallback(self) -> None:
        patches = _make_patches()
        managers = {k: p.start() for k, p in patches.items()}
        managers["run_single"].side_effect = RuntimeError("unexpected")
        try:
            result = await parse_intent_and_route(
                {"query": "test query"},
                logging.getLogger("test"),
                MagicMock(),
                include_routing=True,
                opik_active=False,
                opik_project_name="test",
            )
            assert result.route == "react"
            assert "intent parse failed" in result.reasoning
        finally:
            for p in patches.values():
                p.stop()


class TestParseIntentAndRouteBlobStore:
    @pytest.mark.asyncio
    async def test_blob_store_creation_failure_continues(self) -> None:
        patches = _make_patches()
        managers = {k: p.start() for k, p in patches.items()}
        try:
            with patch(
                "app.modules.transformers.blob_storage.BlobStorage",
                side_effect=RuntimeError("no blob"),
            ):
                result = await parse_intent_and_route(
                    {"query": "test query"},
                    logging.getLogger("test"),
                    MagicMock(),
                    include_routing=False,
                    config_service=MagicMock(),
                    graph_provider=MagicMock(),
                    opik_active=False,
                    opik_project_name="test",
                )
            assert result.rewritten_query.strip() != ""
        finally:
            for p in patches.values():
                p.stop()

    @pytest.mark.asyncio
    async def test_attachments_resolved_when_blob_store_available(self) -> None:
        patches = _make_patches()
        managers = {k: p.start() for k, p in patches.items()}
        try:
            fake_blob = MagicMock()
            with patch(
                "app.modules.transformers.blob_storage.BlobStorage",
                return_value=fake_blob,
            ), patch(
                "app.utils.attachment_utils.resolve_attachments",
                new_callable=AsyncMock,
                return_value=[{"type": "text", "text": "attachment content"}],
            ) as mock_resolve:
                result = await parse_intent_and_route(
                    {"query": "check this file", "attachments": [{"id": "a1"}]},
                    logging.getLogger("test"),
                    MagicMock(),
                    include_routing=False,
                    config_service=MagicMock(),
                    graph_provider=MagicMock(),
                    opik_active=False,
                    opik_project_name="test",
                )
            mock_resolve.assert_awaited_once()
            assert result.rewritten_query.strip() != ""
        finally:
            for p in patches.values():
                p.stop()

    @pytest.mark.asyncio
    async def test_attachment_resolution_failure_continues(self) -> None:
        patches = _make_patches()
        managers = {k: p.start() for k, p in patches.items()}
        try:
            fake_blob = MagicMock()
            with patch(
                "app.modules.transformers.blob_storage.BlobStorage",
                return_value=fake_blob,
            ), patch(
                "app.utils.attachment_utils.resolve_attachments",
                new_callable=AsyncMock,
                side_effect=RuntimeError("resolve failed"),
            ):
                result = await parse_intent_and_route(
                    {"query": "check this file", "attachments": [{"id": "a1"}]},
                    logging.getLogger("test"),
                    MagicMock(),
                    include_routing=False,
                    config_service=MagicMock(),
                    graph_provider=MagicMock(),
                    opik_active=False,
                    opik_project_name="test",
                )
            assert result.rewritten_query.strip() != ""
        finally:
            for p in patches.values():
                p.stop()


class TestParseIntentAndRouteTransportRegistry:
    @pytest.mark.asyncio
    async def test_builds_transport_registry_when_none(self) -> None:
        patches = _make_patches()
        managers = {k: p.start() for k, p in patches.items()}
        try:
            result = await parse_intent_and_route(
                {"query": "test query"},
                logging.getLogger("test"),
                MagicMock(),
                include_routing=True,
                transport_registry=None,
                opik_active=False,
                opik_project_name="test",
            )
            assert result.rewritten_query.strip() != ""
        finally:
            for p in patches.values():
                p.stop()

    @pytest.mark.asyncio
    async def test_builds_transport_when_registry_lacks_langchain(self) -> None:
        patches = _make_patches()
        managers = {k: p.start() for k, p in patches.items()}
        try:
            mock_registry = MagicMock()
            mock_registry.has.return_value = False
            result = await parse_intent_and_route(
                {"query": "test query"},
                logging.getLogger("test"),
                MagicMock(),
                include_routing=True,
                transport_registry=mock_registry,
                opik_active=False,
                opik_project_name="test",
            )
            mock_registry.register.assert_called_once()
            assert result.rewritten_query.strip() != ""
        finally:
            for p in patches.values():
                p.stop()

    @pytest.mark.asyncio
    async def test_reuses_transport_when_registry_has_langchain(self) -> None:
        patches = _make_patches()
        managers = {k: p.start() for k, p in patches.items()}
        try:
            mock_registry = MagicMock()
            mock_registry.has.return_value = True
            result = await parse_intent_and_route(
                {"query": "test query"},
                logging.getLogger("test"),
                MagicMock(),
                include_routing=True,
                transport_registry=mock_registry,
                opik_active=False,
                opik_project_name="test",
            )
            mock_registry.register.assert_not_called()
        finally:
            for p in patches.values():
                p.stop()


class TestParseIntentAndRouteOpikDefaults:
    @pytest.mark.asyncio
    async def test_opik_defaults_resolved_when_none(self) -> None:
        patches = _make_patches()
        managers = {k: p.start() for k, p in patches.items()}
        try:
            with patch.dict("os.environ", {"OPIK_PROJECT_NAME": "my-proj"}):
                result = await parse_intent_and_route(
                    {"query": "test query"},
                    logging.getLogger("test"),
                    MagicMock(),
                    include_routing=False,
                    opik_active=None,
                    opik_project_name=None,
                )
            managers["is_opik"].assert_called_once()
            assert result.rewritten_query.strip() != ""
        finally:
            for p in patches.values():
                p.stop()


class TestParseIntent:
    @pytest.mark.asyncio
    async def test_delegates_with_include_routing_false(self) -> None:
        patches = _make_patches()
        managers = {k: p.start() for k, p in patches.items()}
        try:
            result = await parse_intent(
                {"query": "summarize the document"},
                logging.getLogger("test"),
                MagicMock(),
                opik_active=False,
                opik_project_name="test",
            )
            assert result.route is None
            assert result.rewritten_query.strip() != ""
        finally:
            for p in patches.values():
                p.stop()

    @pytest.mark.asyncio
    async def test_empty_query_returns_fallback(self) -> None:
        result = await parse_intent(
            {"query": ""},
            logging.getLogger("test"),
            MagicMock(),
        )
        assert result.reasoning == "empty query"
        assert result.route is None
