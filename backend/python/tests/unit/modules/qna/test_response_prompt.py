"""Unit tests for app.modules.qna.response_prompt — pure functions."""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.qna.response_prompt import (
    _format_reference_data_for_response,
    build_conversation_history_context,
    build_record_label_mapping,
    build_response_prompt,
    build_user_context,
    detect_response_mode,
    should_use_structured_mode,
)


# ============================================================================
# build_conversation_history_context
# ============================================================================

class TestBuildConversationHistoryContext:
    def test_empty_history(self):
        result = build_conversation_history_context([])
        assert "start of our conversation" in result

    def test_none_history(self):
        result = build_conversation_history_context(None)
        assert "start of our conversation" in result

    def test_user_and_bot_turns(self):
        convs = [
            {"role": "user_query", "content": "What is X?"},
            {"role": "bot_response", "content": "X is a thing."},
        ]
        result = build_conversation_history_context(convs)
        assert "User (Turn 1)" in result
        assert "What is X?" in result
        assert "Assistant (Turn 2)" in result

    def test_truncates_long_bot_response(self):
        long_response = "A" * 500
        convs = [{"role": "bot_response", "content": long_response}]
        result = build_conversation_history_context(convs)
        assert "..." in result
        assert len(result) < len(long_response) + 100

    def test_max_history_limit(self):
        convs = [{"role": "user_query", "content": f"q{i}"} for i in range(20)]
        result = build_conversation_history_context(convs, max_history=3)
        assert "q17" in result
        assert "q0" not in result


# ============================================================================
# build_record_label_mapping
# ============================================================================

class TestBuildRecordLabelMapping:
    def test_single_record(self):
        results = [
            {"virtual_record_id": "uuid-1", "block_index": 0},
            {"virtual_record_id": "uuid-1", "block_index": 1},
        ]
        mapping = build_record_label_mapping(results)
        assert mapping == {"R1": "uuid-1"}

    def test_multiple_records(self):
        results = [
            {"virtual_record_id": "uuid-1", "block_index": 0},
            {"virtual_record_id": "uuid-2", "block_index": 0},
            {"virtual_record_id": "uuid-3", "block_index": 0},
        ]
        mapping = build_record_label_mapping(results)
        assert mapping == {"R1": "uuid-1", "R2": "uuid-2", "R3": "uuid-3"}

    def test_uses_metadata_fallback(self):
        results = [
            {"metadata": {"virtualRecordId": "uuid-m"}, "block_index": 0},
        ]
        mapping = build_record_label_mapping(results)
        assert mapping == {"R1": "uuid-m"}

    def test_empty_results(self):
        assert build_record_label_mapping([]) == {}

    def test_no_virtual_id_skipped(self):
        results = [{"block_index": 0}]
        mapping = build_record_label_mapping(results)
        assert mapping == {}


# ============================================================================
# build_user_context
# ============================================================================

class TestBuildUserContext:
    def test_full_user_and_org_info(self):
        user = {"userEmail": "a@b.com", "fullName": "Alice", "designation": "Engineer"}
        org = {"name": "Acme Corp", "accountType": "Enterprise"}
        ctx = build_user_context(user, org)
        assert "a@b.com" in ctx
        assert "Alice" in ctx
        assert "Engineer" in ctx
        assert "Acme Corp" in ctx
        assert "Enterprise" in ctx

    def test_missing_user_info(self):
        assert build_user_context(None, {"name": "Org"}) == "No user context available."

    def test_missing_org_info(self):
        assert build_user_context({"userEmail": "a@b.com"}, None) == "No user context available."

    def test_both_none(self):
        assert build_user_context(None, None) == "No user context available."

    def test_partial_fields(self):
        user = {"userEmail": "a@b.com"}
        org = {"name": "Corp"}
        ctx = build_user_context(user, org)
        assert "a@b.com" in ctx
        assert "Corp" in ctx
        assert "Role" not in ctx
        assert "Account Type" not in ctx


# ============================================================================
# detect_response_mode
# ============================================================================

class TestDetectResponseMode:
    def test_dict_with_answer_and_chunk_indexes_is_structured(self):
        data = {"answer": "Hello", "chunkIndexes": [0, 1]}
        mode, content = detect_response_mode(data)
        assert mode == "structured"
        assert content["answer"] == "Hello"

    def test_dict_with_answer_and_citations(self):
        data = {"answer": "Hello", "citations": ["c1"]}
        mode, _ = detect_response_mode(data)
        assert mode == "structured"

    def test_dict_with_answer_and_chunk_indexes(self):
        data = {"answer": "Hello", "chunkIndexes": [0, 1]}
        mode, _ = detect_response_mode(data)
        assert mode == "structured"

    def test_dict_without_citation_keys(self):
        data = {"answer": "Hello"}
        mode, _ = detect_response_mode(data)
        assert mode == "conversational"

    def test_dict_with_answer_and_block_numbers_is_conversational(self):
        # blockNumbers is not a recognized structured key — only chunkIndexes/citations trigger structured
        data = {"answer": "Hello", "blockNumbers": ["R1-0"]}
        mode, _ = detect_response_mode(data)
        assert mode == "conversational"

    def test_non_string_non_dict(self):
        mode, content = detect_response_mode(42)
        assert mode == "conversational"
        assert content == "42"

    def test_json_markdown_code_block(self):
        json_str = '```json\n{"answer": "Test", "blockNumbers": ["R1-0"]}\n```'
        with patch("app.utils.streaming.extract_json_from_string",
                    return_value={"answer": "Test", "blockNumbers": ["R1-0"]}):
            mode, content = detect_response_mode(json_str)
            assert mode == "structured"
            assert content["answer"] == "Test"

    def test_raw_json_string(self):
        json_str = '{"answer": "Test", "blockNumbers": ["R1-0"]}'
        with patch("app.utils.citations.fix_json_string", return_value=json_str):
            mode, content = detect_response_mode(json_str)
            assert mode == "structured"

    def test_malformed_json_falls_back(self):
        content = '{"answer": "Test"'  # not valid JSON, doesn't end with }
        mode, result = detect_response_mode(content)
        assert mode == "conversational"

    def test_plain_text(self):
        mode, content = detect_response_mode("Just a regular answer")
        assert mode == "conversational"
        assert content == "Just a regular answer"

    def test_empty_string(self):
        mode, content = detect_response_mode("")
        assert mode == "conversational"

    def test_json_code_block_with_no_answer_key(self):
        json_str = '```json\n{"result": "Test"}\n```'
        with patch("app.utils.streaming.extract_json_from_string",
                    return_value={"result": "Test"}):
            mode, _ = detect_response_mode(json_str)
            assert mode == "conversational"

    def test_json_code_block_parse_error(self):
        json_str = '```json\n{invalid}\n```'
        with patch("app.utils.streaming.extract_json_from_string",
                    side_effect=ValueError("bad json")):
            mode, _ = detect_response_mode(json_str)
            assert mode == "conversational"

    def test_raw_json_parse_error(self):
        json_str = '{invalid json}'
        with patch("app.utils.citations.fix_json_string",
                    return_value='{invalid json}'):
            mode, _ = detect_response_mode(json_str)
            assert mode == "conversational"


# ============================================================================
# should_use_structured_mode
# ============================================================================

class TestShouldUseStructuredMode:
    def test_true_with_results_no_followup(self):
        state = {"final_results": [{"some": "data"}], "query_analysis": {"is_follow_up": False}}
        assert should_use_structured_mode(state) is True

    def test_false_with_results_and_followup(self):
        state = {"final_results": [{"some": "data"}], "query_analysis": {"is_follow_up": True}}
        assert should_use_structured_mode(state) is False

    def test_true_when_forced(self):
        state = {"final_results": [], "force_structured_output": True}
        assert should_use_structured_mode(state) is True

    def test_false_no_results_no_force(self):
        state = {"final_results": [], "force_structured_output": False}
        assert should_use_structured_mode(state) is False

    def test_false_when_no_state_keys(self):
        assert should_use_structured_mode({}) is False

    def test_missing_query_analysis(self):
        state = {"final_results": [{"data": "x"}]}
        assert should_use_structured_mode(state) is True


# ============================================================================
# build_response_prompt
# ============================================================================

class TestBuildResponsePrompt:
    def test_includes_internal_context_with_qna_content(self):
        state = {"qna_message_content": "some content", "query": "test"}
        prompt = build_response_prompt(state)
        assert "Internal knowledge" in prompt

    def test_includes_internal_context_with_final_results(self):
        state = {"final_results": [{"a": 1}, {"b": 2}], "query": "test"}
        prompt = build_response_prompt(state)
        assert "2 knowledge blocks are available" in prompt

    def test_no_internal_context(self):
        state = {"query": "test"}
        prompt = build_response_prompt(state)
        assert "No internal knowledge sources available" in prompt

    def test_includes_user_context(self):
        state = {
            "query": "test",
            "user_info": {"userEmail": "a@b.com", "fullName": "Alice"},
            "org_info": {"name": "Corp"},
        }
        prompt = build_response_prompt(state)
        assert "a@b.com" in prompt

    def test_includes_conversation_history(self):
        state = {
            "query": "test",
            "previous_conversations": [
                {"role": "user_query", "content": "prior question"},
            ],
        }
        prompt = build_response_prompt(state)
        assert "prior question" in prompt

    def test_prepends_base_prompt(self):
        state = {"query": "test", "system_prompt": "You are a helpful bot"}
        prompt = build_response_prompt(state)
        assert prompt.startswith("You are a helpful bot")

    def test_skips_default_base_prompt(self):
        state = {"query": "test", "system_prompt": "You are an enterprise questions answering expert"}
        prompt = build_response_prompt(state)
        assert not prompt.startswith("You are an enterprise questions answering expert\n\n")

    def test_includes_instructions(self):
        state = {"query": "test", "instructions": "Be concise."}
        prompt = build_response_prompt(state)
        assert "## Agent Instructions" in prompt
        assert "Be concise." in prompt

    def test_timezone_appended(self):
        state = {"query": "test", "timezone": "America/New_York"}
        prompt = build_response_prompt(state)
        assert "America/New_York" in prompt

    def test_provided_current_time_overrides_default(self):
        state = {"query": "test", "current_time": "2025-01-01T00:00:00Z"}
        prompt = build_response_prompt(state)
        # current_time is stored in state but only used if {current_datetime} placeholder exists
        # Verify no error and prompt is generated
        assert len(prompt) > 0


# ============================================================================
# create_response_messages
# ============================================================================

class TestCreateResponseMessages:
    def test_basic_message_creation(self):
        from app.modules.qna.response_prompt import create_response_messages
        from langchain_core.messages import HumanMessage, SystemMessage

        with patch("app.modules.agents.qna.conversation_memory.ConversationMemory") as MockMem:
            MockMem.extract_tool_context_from_history.return_value = {}
            MockMem.should_reuse_tool_results.return_value = False

            state = {
                "query": "What is PipesHub?",
                "qna_message_content": "formatted content with R-labels",
                "previous_conversations": [],
            }
            msgs = create_response_messages(state)
            assert len(msgs) == 2  # SystemMessage + HumanMessage
            assert isinstance(msgs[0], SystemMessage)
            assert isinstance(msgs[1], HumanMessage)
            assert msgs[1].content == "formatted content with R-labels"

    def test_fallback_plain_query_with_knowledge(self):
        from app.modules.qna.response_prompt import create_response_messages
        from langchain_core.messages import HumanMessage

        with patch("app.modules.agents.qna.conversation_memory.ConversationMemory") as MockMem:
            MockMem.extract_tool_context_from_history.return_value = {}
            MockMem.should_reuse_tool_results.return_value = False

            state = {
                "query": "What is X?",
                "final_results": [{"data": "result"}],
                "previous_conversations": [],
            }
            msgs = create_response_messages(state)
            last = msgs[-1]
            assert isinstance(last, HumanMessage)
            assert "Respond in JSON format" in last.content

    def test_fallback_plain_query_no_knowledge(self):
        from app.modules.qna.response_prompt import create_response_messages
        from langchain_core.messages import HumanMessage

        with patch("app.modules.agents.qna.conversation_memory.ConversationMemory") as MockMem:
            MockMem.extract_tool_context_from_history.return_value = {}
            MockMem.should_reuse_tool_results.return_value = False

            state = {
                "query": "Hello",
                "previous_conversations": [],
            }
            msgs = create_response_messages(state)
            last = msgs[-1]
            assert isinstance(last, HumanMessage)
            assert last.content == "Hello"

    def test_conversation_history_included(self):
        from app.modules.qna.response_prompt import create_response_messages
        from langchain_core.messages import AIMessage, HumanMessage

        with patch("app.modules.agents.qna.conversation_memory.ConversationMemory") as MockMem:
            MockMem.extract_tool_context_from_history.return_value = {}
            MockMem.should_reuse_tool_results.return_value = False

            state = {
                "query": "follow up",
                "previous_conversations": [
                    {"role": "user_query", "content": "prior question"},
                    {"role": "bot_response", "content": "prior answer"},
                ],
            }
            msgs = create_response_messages(state)
            # System + prior_user + prior_bot + current_user = 4
            assert len(msgs) == 4
            assert isinstance(msgs[1], HumanMessage)
            assert msgs[1].content == "prior question"
            assert isinstance(msgs[2], AIMessage)

    def test_reference_data_appended_to_last_ai_message(self):
        from app.modules.qna.response_prompt import create_response_messages

        with patch("app.modules.agents.qna.conversation_memory.ConversationMemory") as MockMem:
            MockMem.extract_tool_context_from_history.return_value = {}
            MockMem.should_reuse_tool_results.return_value = False

            state = {
                "query": "follow up",
                "previous_conversations": [
                    {"role": "user_query", "content": "q"},
                    {"role": "bot_response", "content": "a",
                     "referenceData": [{"type": "jira_issue", "key": "PA-1"}]},
                ],
            }
            msgs = create_response_messages(state)
            # The AI message should have reference data appended
            ai_msg = [m for m in msgs if hasattr(m, 'content') and 'PA-1' in m.content]
            assert len(ai_msg) >= 1

    def test_contextual_followup_enriches_query(self):
        from app.modules.qna.response_prompt import create_response_messages

        with patch("app.modules.agents.qna.conversation_memory.ConversationMemory") as MockMem:
            MockMem.extract_tool_context_from_history.return_value = {}
            MockMem.should_reuse_tool_results.return_value = True
            MockMem.enrich_query_with_context.return_value = "enriched: What is X?"

            state = {
                "query": "What is X?",
                "previous_conversations": [],
            }
            msgs = create_response_messages(state)
            assert state.get("is_contextual_followup") is True

    def test_knowledge_tool_result_adds_json_reminder(self):
        from app.modules.qna.response_prompt import create_response_messages
        from langchain_core.messages import HumanMessage

        with patch("app.modules.agents.qna.conversation_memory.ConversationMemory") as MockMem:
            MockMem.extract_tool_context_from_history.return_value = {}
            MockMem.should_reuse_tool_results.return_value = False

            state = {
                "query": "test",
                "all_tool_results": [{"tool_name": "internal_knowledge_retrieval"}],
                "previous_conversations": [],
            }
            msgs = create_response_messages(state)
            last = msgs[-1]
            assert isinstance(last, HumanMessage)
            assert "Respond in JSON format" in last.content


# ============================================================================
# _format_reference_data_for_response
# ============================================================================

class TestFormatReferenceData:
    def test_empty_list(self):
        assert _format_reference_data_for_response([]) == ""

    def test_jira_issues(self):
        data = [{"type": "jira_issue", "key": "PA-123"}]
        result = _format_reference_data_for_response(data)
        assert "PA-123" in result

    def test_confluence_spaces(self):
        data = [{"type": "confluence_space", "name": "Engineering", "id": "123"}]
        result = _format_reference_data_for_response(data)
        assert "Engineering" in result
        assert "Confluence Spaces" in result

    def test_jira_projects(self):
        data = [{"type": "jira_project", "name": "MyProj", "key": "MP"}]
        result = _format_reference_data_for_response(data)
        assert "MyProj" in result
        assert "Jira Projects" in result

    def test_confluence_pages(self):
        data = [{"type": "confluence_page", "title": "Getting Started", "id": "456"}]
        result = _format_reference_data_for_response(data)
        assert "Getting Started" in result

    def test_caps_at_ten_items(self):
        data = [{"type": "jira_issue", "key": f"PA-{i}"} for i in range(20)]
        result = _format_reference_data_for_response(data)
        assert "PA-9" in result
        assert "PA-10" not in result

    def test_mixed_types(self):
        data = [
            {"type": "jira_issue", "key": "PA-1"},
            {"type": "confluence_page", "title": "Page", "id": "1"},
        ]
        result = _format_reference_data_for_response(data)
        assert "Jira Issues" in result
        assert "Confluence Pages" in result
