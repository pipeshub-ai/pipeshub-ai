"""
Additional async coverage tests for app.modules.qna.response_prompt

Targets the multimodal/PDF branches in create_response_messages that require
blob_store and async calls.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.modules.qna.response_prompt import create_response_messages


def _run(coro):
    return asyncio.run(coro)


class TestCreateResponseMessagesMultimodal:
    """Tests covering the multimodal/blob_store branches."""

    def test_multimodal_with_image_attachments(self):
        state = {
            "query": "What is this?",
            "previous_conversations": [
                {
                    "role": "user_query",
                    "content": "Look at this image",
                    "attachments": [{"mimeType": "image/png", "url": "http://img.com/x.png"}],
                },
                {"role": "bot_response", "content": "I see a diagram."},
            ],
            "is_multimodal_llm": True,
            "blob_store": MagicMock(),
            "org_id": "org1",
        }
        with patch("app.utils.chat_helpers.build_multimodal_user_content", new_callable=AsyncMock) as mock_mc:
            mock_mc.return_value = [
                {"type": "text", "text": "Look at this image"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ]
            with patch("app.modules.agents.qna.conversation_memory.ConversationMemory") as MockMem:
                MockMem.should_reuse_tool_results.return_value = False
                msgs = _run(create_response_messages(state))
                assert isinstance(msgs[0], SystemMessage)
                # System message content should include image blocks
                sys_content = msgs[0].content
                assert isinstance(sys_content, list)
                has_image = any(
                    b.get("type") == "image_url"
                    for b in sys_content
                    if isinstance(b, dict)
                )
                assert has_image

    def test_pdf_attachment_in_history(self):
        mock_blob = MagicMock()
        mock_blob.get_record_from_storage = AsyncMock(return_value={
            "blocks": [{"content": "Policy document content"}],
            "record_name": "policy.pdf",
        })

        state = {
            "query": "Summarize the policy",
            "previous_conversations": [
                {
                    "role": "user_query",
                    "content": "Here is the policy",
                    "attachments": [{"mimeType": "application/pdf", "virtualRecordId": "vr1"}],
                },
                {"role": "bot_response", "content": "I see it."},
            ],
            "is_multimodal_llm": False,
            "blob_store": mock_blob,
            "org_id": "org1",
        }
        with patch("app.utils.chat_helpers.record_to_message_content") as mock_rtmc:
            from app.utils.chat_helpers import CitationRefMapper
            mock_rtmc.return_value = (
                [{"type": "text", "text": "PDF: Policy content here"}],
                CitationRefMapper(),
            )
            with patch("app.modules.agents.qna.conversation_memory.ConversationMemory") as MockMem:
                MockMem.should_reuse_tool_results.return_value = False
                msgs = _run(create_response_messages(state))
                # Should have messages including PDF content
                assert len(msgs) >= 3  # system + user + bot + current

    def test_no_previous_conversations(self):
        state = {
            "query": "Hello",
            "previous_conversations": [],
        }
        with patch("app.modules.agents.qna.conversation_memory.ConversationMemory") as MockMem:
            MockMem.should_reuse_tool_results.return_value = False
            msgs = _run(create_response_messages(state))
            assert isinstance(msgs[0], SystemMessage)
            assert isinstance(msgs[-1], HumanMessage)
            assert msgs[-1].content == "Hello"

    def test_qna_message_content_used_directly(self):
        state = {
            "query": "What is X?",
            "qna_message_content": "formatted retrieval content with refs",
            "previous_conversations": [],
        }
        with patch("app.modules.agents.qna.conversation_memory.ConversationMemory") as MockMem:
            MockMem.should_reuse_tool_results.return_value = False
            msgs = _run(create_response_messages(state))
            last = msgs[-1]
            assert isinstance(last, HumanMessage)
            assert last.content == "formatted retrieval content with refs"

    def test_bot_response_abbreviated_in_system(self):
        long_response = "A" * 500
        state = {
            "query": "Follow up",
            "previous_conversations": [
                {"role": "user_query", "content": "q1"},
                {"role": "bot_response", "content": long_response},
            ],
        }
        with patch("app.modules.agents.qna.conversation_memory.ConversationMemory") as MockMem:
            MockMem.should_reuse_tool_results.return_value = False
            msgs = _run(create_response_messages(state))
            sys_content = msgs[0].content
            # System message should contain abbreviated bot response
            text_blocks = [b.get("text", "") for b in sys_content if isinstance(b, dict)]
            full_text = " ".join(text_blocks)
            assert "..." in full_text

    def test_reference_data_from_history(self):
        state = {
            "query": "Tell me more about PA-1",
            "previous_conversations": [
                {"role": "user_query", "content": "Search jira"},
                {
                    "role": "bot_response",
                    "content": "Found PA-1",
                    "referenceData": [{"type": "jira_issue", "key": "PA-1", "app": "jira"}],
                },
            ],
        }
        with patch("app.modules.agents.qna.conversation_memory.ConversationMemory") as MockMem:
            MockMem.should_reuse_tool_results.return_value = False
            msgs = _run(create_response_messages(state))
            # The AI message should have reference data appended
            ai_msgs = [m for m in msgs if isinstance(m, AIMessage)]
            found_ref = any("PA-1" in m.content for m in ai_msgs)
            assert found_ref

    def test_knowledge_tool_result_json_reminder(self):
        state = {
            "query": "Search for X",
            "all_tool_results": [{"tool_name": "internal_knowledge_retrieval"}],
            "previous_conversations": [],
        }
        with patch("app.modules.agents.qna.conversation_memory.ConversationMemory") as MockMem:
            MockMem.should_reuse_tool_results.return_value = False
            msgs = _run(create_response_messages(state))
            last = msgs[-1]
            assert "JSON format" in last.content

    def test_final_results_trigger_json_reminder(self):
        state = {
            "query": "What is the policy?",
            "final_results": [{"data": "result"}],
            "previous_conversations": [],
        }
        with patch("app.modules.agents.qna.conversation_memory.ConversationMemory") as MockMem:
            MockMem.should_reuse_tool_results.return_value = False
            msgs = _run(create_response_messages(state))
            last = msgs[-1]
            assert "JSON format" in last.content

    def test_reuse_tool_results_enriches_query(self):
        state = {
            "query": "Tell me more",
            "previous_conversations": [
                {"role": "user_query", "content": "What is X?"},
                {"role": "bot_response", "content": "X is a thing."},
            ],
        }
        with patch("app.modules.agents.qna.conversation_memory.ConversationMemory") as MockMem:
            MockMem.should_reuse_tool_results.return_value = True
            MockMem.enrich_query_with_context.return_value = "Tell me more (context: X is a thing)"
            msgs = _run(create_response_messages(state))
            last = msgs[-1]
            assert "context: X is a thing" in last.content
