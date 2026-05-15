"""
Additional coverage tests for app.modules.agents.deep.context_manager

Targets functions and branches not covered by the main test file:
- _image_attachment_count
- _pdf_attachment_count
- _user_plain_summary_line
- ensure_blob_store
- build_conversation_messages (various branches)
- build_respond_conversation_context (various branches)
- compact_conversation_history_async
- _summarize_conversations_sync / _summarize_conversations_async
"""

import asyncio
import logging
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.modules.agents.deep.context_manager import (
    _format_reference_data,
    _image_attachment_count,
    _pdf_attachment_count,
    _user_plain_summary_line,
    build_conversation_messages,
    build_respond_conversation_context,
    compact_conversation_history,
    compact_conversation_history_async,
    ensure_blob_store,
    _summarize_conversations_sync,
    MAX_RECENT_PAIRS,
)

log = logging.getLogger("test_ctx_mgr_cov")
log.setLevel(logging.CRITICAL)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# _image_attachment_count
# ---------------------------------------------------------------------------

class TestImageAttachmentCount:
    def test_no_attachments_key(self):
        assert _image_attachment_count({}) == 0

    def test_none_attachments(self):
        assert _image_attachment_count({"attachments": None}) == 0

    def test_empty_list(self):
        assert _image_attachment_count({"attachments": []}) == 0

    def test_single_image(self):
        conv = {"attachments": [{"mimeType": "image/png"}]}
        assert _image_attachment_count(conv) == 1

    def test_multiple_images(self):
        conv = {"attachments": [
            {"mimeType": "image/png"},
            {"mimeType": "image/jpeg"},
            {"mimeType": "IMAGE/WEBP"},
        ]}
        assert _image_attachment_count(conv) == 3

    def test_non_image_attachments_excluded(self):
        conv = {"attachments": [
            {"mimeType": "application/pdf"},
            {"mimeType": "text/plain"},
            {"mimeType": "image/png"},
        ]}
        assert _image_attachment_count(conv) == 1

    def test_invalid_attachment_shape(self):
        conv = {"attachments": ["not a dict", None, {"mimeType": "image/png"}]}
        assert _image_attachment_count(conv) == 1

    def test_missing_mimetype(self):
        conv = {"attachments": [{"name": "file.png"}]}
        assert _image_attachment_count(conv) == 0


# ---------------------------------------------------------------------------
# _pdf_attachment_count
# ---------------------------------------------------------------------------

class TestPdfAttachmentCount:
    def test_no_attachments(self):
        assert _pdf_attachment_count({}) == 0

    def test_none_attachments(self):
        assert _pdf_attachment_count({"attachments": None}) == 0

    def test_single_pdf(self):
        conv = {"attachments": [{"mimeType": "application/pdf"}]}
        assert _pdf_attachment_count(conv) == 1

    def test_case_insensitive(self):
        conv = {"attachments": [{"mimeType": "Application/PDF"}]}
        assert _pdf_attachment_count(conv) == 1

    def test_non_pdf_excluded(self):
        conv = {"attachments": [
            {"mimeType": "image/png"},
            {"mimeType": "application/pdf"},
        ]}
        assert _pdf_attachment_count(conv) == 1

    def test_multiple_pdfs(self):
        conv = {"attachments": [
            {"mimeType": "application/pdf"},
            {"mimeType": "application/pdf"},
        ]}
        assert _pdf_attachment_count(conv) == 2


# ---------------------------------------------------------------------------
# _user_plain_summary_line
# ---------------------------------------------------------------------------

class TestUserPlainSummaryLine:
    def test_empty_content_no_attachments(self):
        assert _user_plain_summary_line({"content": ""}, 100) is None

    def test_none_content_no_attachments(self):
        assert _user_plain_summary_line({}, 100) is None

    def test_simple_text(self):
        conv = {"content": "Hello world"}
        result = _user_plain_summary_line(conv, 100)
        assert result == "User: Hello world"

    def test_truncates_long_text(self):
        conv = {"content": "A" * 500}
        result = _user_plain_summary_line(conv, 50)
        assert result.startswith("User: ")
        assert "..." in result
        assert len(result) < 500

    def test_image_attachment_only_no_text(self):
        conv = {"content": "", "attachments": [{"mimeType": "image/png"}]}
        result = _user_plain_summary_line(conv, 100)
        assert "1 image(s) attached" in result

    def test_pdf_attachment_only_no_text(self):
        conv = {"content": "", "attachments": [{"mimeType": "application/pdf"}]}
        result = _user_plain_summary_line(conv, 100)
        assert "1 PDF(s) attached" in result

    def test_both_images_and_pdfs_no_text(self):
        conv = {"content": "", "attachments": [
            {"mimeType": "image/png"},
            {"mimeType": "image/jpeg"},
            {"mimeType": "application/pdf"},
        ]}
        result = _user_plain_summary_line(conv, 100)
        assert "2 image(s)" in result
        assert "1 PDF(s)" in result

    def test_text_with_attachments(self):
        conv = {"content": "Check this", "attachments": [{"mimeType": "image/png"}]}
        result = _user_plain_summary_line(conv, 100)
        assert "Check this" in result
        assert "1 image(s)" in result
        assert "attached" in result

    def test_text_with_pdf_attachment(self):
        conv = {"content": "See doc", "attachments": [{"mimeType": "application/pdf"}]}
        result = _user_plain_summary_line(conv, 100)
        assert "See doc" in result
        assert "1 PDF(s)" in result


# ---------------------------------------------------------------------------
# ensure_blob_store
# ---------------------------------------------------------------------------

class TestEnsureBlobStore:
    def test_returns_existing_blob_store(self):
        mock_store = MagicMock()
        state = {"blob_store": mock_store}
        result = ensure_blob_store(state, log)
        assert result is mock_store

    def test_creates_blob_store_when_missing(self):
        state = {"blob_store": None, "config_service": MagicMock(), "graph_provider": MagicMock()}
        with patch("app.modules.transformers.blob_storage.BlobStorage") as MockBS:
            MockBS.return_value = MagicMock()
            result = ensure_blob_store(state, log)
            assert result is not None
            assert state["blob_store"] is not None

    def test_handles_exception_gracefully(self):
        state = {"config_service": None, "graph_provider": None}
        # When BlobStorage constructor raises, ensure_blob_store should not crash
        with patch(
            "app.modules.transformers.blob_storage.BlobStorage",
            side_effect=Exception("init fail"),
        ):
            result = ensure_blob_store(state, log)
            # Should return None on failure
            assert result is None


# ---------------------------------------------------------------------------
# _format_reference_data
# ---------------------------------------------------------------------------

class TestFormatReferenceData:
    def test_empty_list(self):
        assert _format_reference_data([]) == ""

    def test_single_item(self):
        data = [{"type": "jira_issue", "id": "123", "key": "PROJ-1"}]
        result = _format_reference_data(data)
        assert "type=jira_issue" in result
        assert "id=123" in result
        assert "key=PROJ-1" in result

    def test_multiple_items(self):
        data = [
            {"type": "page", "id": "1", "name": "Doc"},
            {"type": "issue", "id": "2", "key": "PA-2"},
        ]
        result = _format_reference_data(data)
        assert "type=page" in result
        assert "name=Doc" in result
        assert "key=PA-2" in result

    def test_caps_at_50(self):
        data = [{"type": "item", "id": str(i)} for i in range(100)]
        result = _format_reference_data(data)
        assert "id=49" in result
        assert "id=50" not in result

    def test_optional_fields_included(self):
        data = [{"type": "file", "url": "https://x.com", "title": "My File", "name": "file.pdf"}]
        result = _format_reference_data(data)
        assert "url=https://x.com" in result
        assert "title=My File" in result
        assert "name=file.pdf" in result


# ---------------------------------------------------------------------------
# build_conversation_messages
# ---------------------------------------------------------------------------

class TestBuildConversationMessages:
    def test_empty_conversations(self):
        result = _run(build_conversation_messages([], log))
        assert result == []

    def test_single_user_message(self):
        convs = [{"role": "user_query", "content": "Hello"}]
        result = _run(build_conversation_messages(convs, log))
        assert len(result) == 1
        assert isinstance(result[0], HumanMessage)
        assert result[0].content == "Hello"

    def test_user_and_bot_pair(self):
        convs = [
            {"role": "user_query", "content": "What is X?"},
            {"role": "bot_response", "content": "X is a thing."},
        ]
        result = _run(build_conversation_messages(convs, log))
        assert len(result) == 2
        assert isinstance(result[0], HumanMessage)
        assert isinstance(result[1], AIMessage)

    def test_sliding_window(self):
        convs = []
        for i in range(30):
            convs.append({"role": "user_query", "content": f"q{i}"})
            convs.append({"role": "bot_response", "content": f"a{i}"})
        result = _run(build_conversation_messages(convs, log, max_pairs=5))
        # Should keep only last 5 pairs = 10 messages
        assert len(result) == 10
        assert result[0].content == "q25"

    def test_empty_content_skipped(self):
        convs = [
            {"role": "user_query", "content": ""},
            {"role": "bot_response", "content": "answer"},
        ]
        result = _run(build_conversation_messages(convs, log))
        assert len(result) == 1
        assert isinstance(result[0], AIMessage)

    def test_reference_data_appended(self):
        convs = [
            {"role": "user_query", "content": "q"},
            {"role": "bot_response", "content": "a", "referenceData": [
                {"type": "issue", "key": "PA-1"}
            ]},
        ]
        result = _run(build_conversation_messages(
            convs, log, include_reference_data=True
        ))
        # Reference data should be appended to the last AI message
        ai_msg = [m for m in result if isinstance(m, AIMessage)]
        assert len(ai_msg) == 1
        assert "PA-1" in ai_msg[0].content

    def test_bot_response_without_user_pair(self):
        convs = [{"role": "bot_response", "content": "orphan response"}]
        result = _run(build_conversation_messages(convs, log))
        assert len(result) == 1
        assert isinstance(result[0], AIMessage)


# ---------------------------------------------------------------------------
# build_respond_conversation_context
# ---------------------------------------------------------------------------

class TestBuildRespondConversationContext:
    def test_empty_conversations_no_summary(self):
        result = _run(build_respond_conversation_context([], None, log))
        assert result == []

    def test_summary_only(self):
        result = _run(build_respond_conversation_context(
            [], "Previous: user asked about X", log
        ))
        assert len(result) == 1
        assert isinstance(result[0], HumanMessage)
        assert "Previous conversation context" in result[0].content

    def test_recent_messages_included(self):
        convs = [
            {"role": "user_query", "content": "Hello"},
            {"role": "bot_response", "content": "Hi"},
        ]
        result = _run(build_respond_conversation_context(convs, None, log))
        assert len(result) == 2
        assert isinstance(result[0], HumanMessage)
        assert isinstance(result[1], AIMessage)

    def test_long_bot_response_truncated(self):
        long_resp = "A" * 1000
        convs = [
            {"role": "user_query", "content": "q"},
            {"role": "bot_response", "content": long_resp},
        ]
        result = _run(build_respond_conversation_context(convs, None, log))
        ai_msg = [m for m in result if isinstance(m, AIMessage)]
        assert len(ai_msg) == 1
        assert len(ai_msg[0].content) < len(long_resp)
        assert "truncated" in ai_msg[0].content

    def test_summary_plus_recent(self):
        convs = [
            {"role": "user_query", "content": "recent q"},
            {"role": "bot_response", "content": "recent a"},
        ]
        result = _run(build_respond_conversation_context(
            convs, "Old summary text", log
        ))
        assert len(result) == 3  # summary + user + bot
        assert "Old summary text" in result[0].content

    def test_max_recent_pairs_limit(self):
        convs = []
        for i in range(20):
            convs.append({"role": "user_query", "content": f"q{i}"})
            convs.append({"role": "bot_response", "content": f"a{i}"})
        result = _run(build_respond_conversation_context(
            convs, None, log, max_recent_pairs=3
        ))
        # 3 pairs = 6 items from conversation, so at most 6 messages
        assert len(result) <= 6


# ---------------------------------------------------------------------------
# compact_conversation_history
# ---------------------------------------------------------------------------

class TestCompactConversationHistory:
    def test_empty_conversations(self):
        summary, recent = compact_conversation_history([], MagicMock(), log)
        assert summary is None
        assert recent == []

    def test_short_conversation_no_compaction(self):
        convs = [
            {"role": "user_query", "content": "q1"},
            {"role": "bot_response", "content": "a1"},
        ]
        summary, recent = compact_conversation_history(convs, MagicMock(), log)
        assert summary is None
        assert recent == convs

    def test_long_conversation_compacted(self):
        convs = []
        for i in range(20):
            convs.append({"role": "user_query", "content": f"question {i}"})
            convs.append({"role": "bot_response", "content": f"answer {i}"})
        summary, recent = compact_conversation_history(
            convs, MagicMock(), log, max_recent_pairs=5
        )
        assert summary is not None
        assert "Previous conversation summary" in summary
        assert len(recent) == 10  # 5 pairs * 2


# ---------------------------------------------------------------------------
# compact_conversation_history_async
# ---------------------------------------------------------------------------

class TestCompactConversationHistoryAsync:
    def test_empty_conversations(self):
        summary, recent = _run(compact_conversation_history_async(
            [], MagicMock(), log
        ))
        assert summary is None
        assert recent == []

    def test_short_conversation_no_compaction(self):
        convs = [
            {"role": "user_query", "content": "hi"},
            {"role": "bot_response", "content": "hello"},
        ]
        summary, recent = _run(compact_conversation_history_async(
            convs, MagicMock(), log
        ))
        assert summary is None
        assert recent == convs

    def test_long_conversation_async_compacted(self):
        convs = []
        for i in range(20):
            convs.append({"role": "user_query", "content": f"q{i}"})
            convs.append({"role": "bot_response", "content": f"a{i}"})

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Summary of older conversations."
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        summary, recent = _run(compact_conversation_history_async(
            convs, mock_llm, log, max_recent_pairs=5
        ))
        assert summary is not None
        assert len(recent) == 10


# ---------------------------------------------------------------------------
# _summarize_conversations_sync
# ---------------------------------------------------------------------------

class TestSummarizeConversationsSync:
    def test_basic_summary(self):
        convs = [
            {"role": "user_query", "content": "What is AI?"},
            {"role": "bot_response", "content": "AI is artificial intelligence."},
        ]
        result = _summarize_conversations_sync(convs, log)
        assert "Previous conversation summary" in result
        assert "What is AI?" in result
        assert "AI is artificial intelligence." in result

    def test_truncates_long_bot_response(self):
        convs = [
            {"role": "bot_response", "content": "X" * 500},
        ]
        result = _summarize_conversations_sync(convs, log)
        assert "..." in result

    def test_skips_empty_content(self):
        convs = [
            {"role": "user_query", "content": ""},
            {"role": "bot_response", "content": ""},
        ]
        result = _summarize_conversations_sync(convs, log)
        assert result == "Previous conversation summary:\n"

    def test_user_with_attachments(self):
        convs = [
            {"role": "user_query", "content": "", "attachments": [{"mimeType": "image/png"}]},
        ]
        result = _summarize_conversations_sync(convs, log)
        assert "image(s) attached" in result
