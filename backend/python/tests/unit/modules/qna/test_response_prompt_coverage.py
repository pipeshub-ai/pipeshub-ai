"""
Additional coverage tests for app.modules.qna.response_prompt

Targets uncovered branches in build_response_prompt and related helpers.
"""

import pytest
from unittest.mock import MagicMock, patch

from app.modules.qna.response_prompt import (
    build_conversation_history_context,
    build_direct_answer_time_context,
    build_response_prompt,
    build_user_context,
    response_system_prompt,
    _CONV_HISTORY_SENTINEL,
    CONTENT_PREVIEW_LENGTH,
    CONVERSATION_PREVIEW_LENGTH,
)


# ---------------------------------------------------------------------------
# response_system_prompt (module-level string)
# ---------------------------------------------------------------------------

class TestResponseSystemPrompt:
    def test_is_string(self):
        assert isinstance(response_system_prompt, str)
        assert len(response_system_prompt) > 500

    def test_contains_structural_sections(self):
        # The module replaces placeholders at import time, so check for section markers
        assert "internal_knowledge_context" in response_system_prompt
        assert "user_context" in response_system_prompt
        assert "conversation_history" in response_system_prompt

    def test_contains_reference_data_rules(self):
        # The module replaces __REFERENCE_DATA_FIELD_RULES_TABLE__ at import time
        assert "__REFERENCE_DATA_FIELD_RULES_TABLE__" not in response_system_prompt

    def test_contains_citation_instructions(self):
        assert "Citation ID" in response_system_prompt or "citation" in response_system_prompt.lower()


# ---------------------------------------------------------------------------
# build_direct_answer_time_context
# ---------------------------------------------------------------------------

class TestBuildDirectAnswerTimeContext:
    def test_with_timezone(self):
        state = {"current_time": "2026-01-15T10:30:00Z", "timezone": "America/New_York"}
        result = build_direct_answer_time_context(state)
        assert "America/New_York" in result

    def test_without_timezone(self):
        state = {"current_time": "2026-01-15T10:30:00Z"}
        result = build_direct_answer_time_context(state)
        assert isinstance(result, str)

    def test_empty_state(self):
        result = build_direct_answer_time_context({})
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# build_response_prompt — additional branches
# ---------------------------------------------------------------------------

class TestBuildResponsePromptExtended:
    def test_web_search_config_with_qna_content(self):
        state = {
            "qna_message_content": "context",
            "query": "test",
            "web_search_config": {"provider": "tavily"},
        }
        prompt = build_response_prompt(state)
        assert "web search" in prompt.lower()

    def test_web_search_config_with_final_results(self):
        state = {
            "final_results": [{"a": 1}],
            "query": "test",
            "web_search_config": {"provider": "tavily"},
        }
        prompt = build_response_prompt(state)
        assert "web search" in prompt.lower()

    def test_web_search_config_no_results(self):
        state = {
            "query": "test",
            "web_search_config": {"provider": "tavily"},
        }
        prompt = build_response_prompt(state)
        assert "web search" in prompt.lower()
        assert "No internal knowledge sources" in prompt

    def test_no_web_search_no_results(self):
        state = {"query": "test"}
        prompt = build_response_prompt(state)
        assert "information is unavailable" in prompt

    def test_conversation_sentinel_present(self):
        state = {"query": "test", "previous_conversations": []}
        prompt = build_response_prompt(state, use_conversation_sentinel=True)
        assert _CONV_HISTORY_SENTINEL in prompt

    def test_current_time_used_in_prompt(self):
        state = {"query": "test", "current_time": "2026-05-14T12:00:00Z"}
        prompt = build_response_prompt(state)
        assert "2026-05-14" in prompt

    def test_instructions_prepended(self):
        state = {"query": "test", "instructions": "Always respond in bullet points."}
        prompt = build_response_prompt(state)
        assert "## Agent Instructions" in prompt
        assert "Always respond in bullet points." in prompt

    def test_custom_system_prompt_prepended(self):
        state = {"query": "test", "system_prompt": "You are a legal expert assistant."}
        prompt = build_response_prompt(state)
        assert prompt.startswith("You are a legal expert assistant.")

    def test_default_system_prompt_not_prepended(self):
        state = {
            "query": "test",
            "system_prompt": "You are an enterprise questions answering expert",
        }
        prompt = build_response_prompt(state)
        # Default prompt should not be double-prepended
        assert not prompt.startswith(
            "You are an enterprise questions answering expert\n\nYou are an enterprise"
        )

    def test_timezone_appended_to_prompt(self):
        state = {
            "query": "test",
            "current_time": "2026-01-01T10:00:00Z",
            "timezone": "Asia/Kolkata",
        }
        prompt = build_response_prompt(state)
        assert "Asia/Kolkata" in prompt

    def test_full_user_and_org_context(self):
        state = {
            "query": "test",
            "user_info": {"userEmail": "alice@corp.com", "fullName": "Alice Smith"},
            "org_info": {"name": "Corp Inc", "accountType": "enterprise"},
        }
        prompt = build_response_prompt(state)
        assert "alice@corp.com" in prompt
        assert "Alice Smith" in prompt
        assert "Corp Inc" in prompt


# ---------------------------------------------------------------------------
# build_user_context — extra edge cases
# ---------------------------------------------------------------------------

class TestBuildUserContextExtended:
    def test_empty_dicts(self):
        result = build_user_context({}, {})
        assert "No user context available" in result

    def test_only_email(self):
        result = build_user_context({"userEmail": "a@b.com"}, {"name": "X"})
        assert "a@b.com" in result
        assert "X" in result

    def test_only_org_name(self):
        result = build_user_context({"fullName": "Bob"}, {"name": "Acme"})
        assert "Bob" in result
        assert "Acme" in result


# ---------------------------------------------------------------------------
# build_conversation_history_context — extra edge cases
# ---------------------------------------------------------------------------

class TestBuildConversationHistoryContextExtended:
    def test_unknown_role_ignored(self):
        convs = [{"role": "unknown", "content": "data"}]
        result = build_conversation_history_context(convs)
        assert "data" not in result

    def test_only_user_queries(self):
        convs = [
            {"role": "user_query", "content": "q1"},
            {"role": "user_query", "content": "q2"},
        ]
        result = build_conversation_history_context(convs)
        assert "q1" in result
        assert "q2" in result

    def test_short_bot_response_not_truncated(self):
        convs = [{"role": "bot_response", "content": "Short."}]
        result = build_conversation_history_context(convs)
        assert "Short." in result
        assert "..." not in result
