"""
Additional async coverage tests for app.modules.agents.deep.context_manager

Targets async branches in:
- _summarize_conversations_async
- build_sub_agent_context (multimodal/PDF branches)
- build_conversation_messages (multimodal/PDF branches)
- build_respond_conversation_context (multimodal branches)
"""

import asyncio
import logging
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.modules.agents.deep.context_manager import (
    _summarize_conversations_async,
    build_conversation_messages,
    build_respond_conversation_context,
    build_sub_agent_context,
)

log = logging.getLogger("test_ctx_async")
log.setLevel(logging.CRITICAL)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# _summarize_conversations_async
# ---------------------------------------------------------------------------

class TestSummarizeConversationsAsync:
    def test_empty_conversations_returns_empty(self):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content=""))
        # When there are no meaningful turns, should return empty
        result = _run(_summarize_conversations_async(
            [{"role": "user_query", "content": ""}],
            mock_llm, log,
        ))
        assert result == ""

    def test_basic_text_summary(self):
        mock_response = MagicMock()
        mock_response.content = "User asked about vacation policy."
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        convs = [
            {"role": "user_query", "content": "What is the vacation policy?"},
            {"role": "bot_response", "content": "You get 20 days of PTO."},
        ]
        result = _run(_summarize_conversations_async(convs, mock_llm, log))
        assert "vacation policy" in result

    def test_llm_failure_falls_back_to_sync(self):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("LLM error"))

        convs = [
            {"role": "user_query", "content": "Hello"},
            {"role": "bot_response", "content": "Hi"},
        ]
        result = _run(_summarize_conversations_async(convs, mock_llm, log))
        assert "Previous conversation summary" in result

    def test_long_content_truncated(self):
        mock_response = MagicMock()
        mock_response.content = "Summarized."
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        convs = [
            {"role": "user_query", "content": "X" * 1000},
            {"role": "bot_response", "content": "Y" * 1000},
        ]
        result = _run(_summarize_conversations_async(convs, mock_llm, log))
        assert result == "Summarized."

    def test_multimodal_with_images(self):
        mock_response = MagicMock()
        mock_response.content = "User shared an image of a diagram."
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        convs = [
            {
                "role": "user_query",
                "content": "What is this?",
                "attachments": [{"mimeType": "image/png", "url": "http://img.com/a.png"}],
            },
        ]
        with patch("app.utils.chat_helpers.build_multimodal_user_content", new_callable=AsyncMock) as mock_mc:
            mock_mc.return_value = [
                {"type": "text", "text": "What is this?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ]
            result = _run(_summarize_conversations_async(
                convs, mock_llm, log,
                is_multimodal_llm=True,
                blob_store=MagicMock(),
                org_id="org1",
            ))
            assert "diagram" in result

    def test_pdf_attachments_resolved(self):
        mock_response = MagicMock()
        mock_response.content = "PDF about security policies."
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        mock_blob_store = MagicMock()
        mock_blob_store.get_record_from_storage = AsyncMock(return_value={"blocks": [{"content": "policy doc"}]})

        convs = [
            {
                "role": "user_query",
                "content": "Check this PDF",
                "attachments": [{"mimeType": "application/pdf", "virtualRecordId": "vr1"}],
            },
        ]
        with patch("app.utils.attachment_utils.resolve_pdf_blocks_simple", return_value=[{"type": "text", "text": "PDF content"}]):
            result = _run(_summarize_conversations_async(
                convs, mock_llm, log,
                blob_store=mock_blob_store,
                org_id="org1",
            ))
            assert "security" in result.lower() or "PDF" in result


# ---------------------------------------------------------------------------
# build_conversation_messages with multimodal
# ---------------------------------------------------------------------------

class TestBuildConversationMessagesMultimodal:
    def test_multimodal_images_included(self):
        convs = [
            {
                "role": "user_query",
                "content": "Look at this",
                "attachments": [{"mimeType": "image/png"}],
            },
            {"role": "bot_response", "content": "I see it."},
        ]
        with patch("app.utils.chat_helpers.build_multimodal_user_content", new_callable=AsyncMock) as mock_mc:
            mock_mc.return_value = [
                {"type": "text", "text": "Look at this"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ]
            result = _run(build_conversation_messages(
                convs, log,
                is_multimodal_llm=True,
                blob_store=MagicMock(),
                org_id="org1",
            ))
            assert len(result) == 2
            # User message should be multimodal content list
            assert isinstance(result[0].content, list)

    def test_pdf_attachments_resolved(self):
        mock_blob = MagicMock()
        mock_blob.get_record_from_storage = AsyncMock(return_value={"blocks": [{"content": "data"}]})

        convs = [
            {
                "role": "user_query",
                "content": "See doc",
                "attachments": [{"mimeType": "application/pdf", "virtualRecordId": "vr1"}],
            },
        ]
        with patch("app.utils.chat_helpers.record_to_message_content") as mock_rtmc:
            from app.utils.chat_helpers import CitationRefMapper
            mock_rtmc.return_value = ([{"type": "text", "text": "pdf content"}], CitationRefMapper())
            result = _run(build_conversation_messages(
                convs, log,
                blob_store=mock_blob,
                org_id="org1",
            ))
            assert len(result) == 1
            # Content should include PDF blocks
            content = result[0].content
            assert isinstance(content, list)


# ---------------------------------------------------------------------------
# build_respond_conversation_context with multimodal
# ---------------------------------------------------------------------------

class TestBuildRespondConversationContextMultimodal:
    def test_multimodal_images(self):
        convs = [
            {
                "role": "user_query",
                "content": "What is this diagram?",
                "attachments": [{"mimeType": "image/png"}],
            },
            {"role": "bot_response", "content": "It shows architecture."},
        ]
        with patch("app.utils.chat_helpers.build_multimodal_user_content", new_callable=AsyncMock) as mock_mc:
            mock_mc.return_value = [
                {"type": "text", "text": "What is this diagram?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
            ]
            result = _run(build_respond_conversation_context(
                convs, None, log,
                is_multimodal_llm=True,
                blob_store=MagicMock(),
                org_id="org1",
            ))
            # First message should be multimodal
            assert isinstance(result[0].content, list)

    def test_pdf_in_respond_context(self):
        mock_blob = MagicMock()
        mock_blob.get_record_from_storage = AsyncMock(return_value={"blocks": [{"content": "pdf"}]})

        convs = [
            {
                "role": "user_query",
                "content": "Check doc",
                "attachments": [{"mimeType": "application/pdf", "virtualRecordId": "vr1"}],
            },
        ]
        with patch("app.utils.chat_helpers.record_to_message_content") as mock_rtmc:
            from app.utils.chat_helpers import CitationRefMapper
            mock_rtmc.return_value = ([{"type": "text", "text": "pdf data"}], CitationRefMapper())
            result = _run(build_respond_conversation_context(
                convs, None, log,
                blob_store=mock_blob,
                org_id="org1",
            ))
            assert len(result) == 1
            assert isinstance(result[0].content, list)


# ---------------------------------------------------------------------------
# build_sub_agent_context
# ---------------------------------------------------------------------------

class TestBuildSubAgentContext:
    def test_basic_no_conversations(self):
        task = {"task_id": "t1", "depends_on": []}
        result = _run(build_sub_agent_context(
            query="What is X?",
            task=task,
            completed_tasks=[],
            conversation_summary=None,
            recent_conversations=[],
            log=log,
        ))
        texts = " ".join(b.get("text", "") for b in result if isinstance(b, dict))
        assert "What is X?" in texts

    def test_with_conversation_summary(self):
        task = {"task_id": "t1", "depends_on": []}
        result = _run(build_sub_agent_context(
            query="Follow up",
            task=task,
            completed_tasks=[],
            conversation_summary="Previously discussed vacation policy.",
            recent_conversations=[],
            log=log,
        ))
        texts = " ".join(b.get("text", "") for b in result if isinstance(b, dict))
        assert "vacation policy" in texts

    def test_with_dependency_results(self):
        task = {"task_id": "t2", "depends_on": ["t1"]}
        completed = [{
            "task_id": "t1",
            "status": "success",
            "result": {"response": "Found 5 tickets."},
        }]
        result = _run(build_sub_agent_context(
            query="Update those tickets",
            task=task,
            completed_tasks=completed,
            conversation_summary=None,
            recent_conversations=[],
            log=log,
        ))
        texts = " ".join(b.get("text", "") for b in result if isinstance(b, dict))
        assert "5 tickets" in texts

    def test_with_failed_dependency(self):
        task = {"task_id": "t2", "depends_on": ["t1"]}
        completed = [{
            "task_id": "t1",
            "status": "error",
            "error": "Timeout after 30s",
        }]
        result = _run(build_sub_agent_context(
            query="Get data",
            task=task,
            completed_tasks=completed,
            conversation_summary=None,
            recent_conversations=[],
            log=log,
        ))
        texts = " ".join(b.get("text", "") for b in result if isinstance(b, dict))
        assert "FAILED" in texts

    def test_with_recent_conversations(self):
        task = {"task_id": "t1", "depends_on": []}
        convs = [
            {"role": "user_query", "content": "What about the new policy?"},
            {"role": "bot_response", "content": "The new policy states 25 days PTO."},
        ]
        result = _run(build_sub_agent_context(
            query="Tell me more",
            task=task,
            completed_tasks=[],
            conversation_summary=None,
            recent_conversations=convs,
            log=log,
        ))
        texts = " ".join(b.get("text", "") for b in result if isinstance(b, dict))
        assert "new policy" in texts

    def test_with_multimodal_images(self):
        task = {"task_id": "t1", "depends_on": []}
        convs = [
            {
                "role": "user_query",
                "content": "See this",
                "attachments": [{"mimeType": "image/png"}],
            },
        ]
        with patch("app.utils.chat_helpers.build_multimodal_user_content", new_callable=AsyncMock) as mock_mc:
            mock_mc.return_value = [
                {"type": "text", "text": ""},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ]
            result = _run(build_sub_agent_context(
                query="Analyze",
                task=task,
                completed_tasks=[],
                conversation_summary=None,
                recent_conversations=convs,
                log=log,
                is_multimodal_llm=True,
                blob_store=MagicMock(),
                org_id="org1",
            ))
            # Should have image_url block
            has_image = any(
                b.get("type") == "image_url" for b in result if isinstance(b, dict)
            )
            assert has_image

    def test_with_pdf_attachments(self):
        task = {"task_id": "t1", "depends_on": []}
        mock_blob = MagicMock()
        mock_blob.get_record_from_storage = AsyncMock(return_value={"blocks": [{"content": "doc content"}]})
        convs = [
            {
                "role": "user_query",
                "content": "Check this",
                "attachments": [{"mimeType": "application/pdf", "virtualRecordId": "vr1"}],
            },
        ]
        with patch("app.utils.attachment_utils.resolve_pdf_blocks_simple", return_value=[{"type": "text", "text": "PDF data"}]):
            result = _run(build_sub_agent_context(
                query="Summarize",
                task=task,
                completed_tasks=[],
                conversation_summary=None,
                recent_conversations=convs,
                log=log,
                blob_store=mock_blob,
                org_id="org1",
            ))
            texts = " ".join(b.get("text", "") for b in result if isinstance(b, dict))
            assert "PDF" in texts
