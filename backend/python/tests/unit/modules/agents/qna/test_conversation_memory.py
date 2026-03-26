"""Unit tests for app.modules.agents.qna.conversation_memory."""

import pytest

from app.modules.agents.qna.conversation_memory import (
    ConversationMemory,
    get_conversation_memory,
)


# ===================================================================
# extract_tool_context_from_history
# ===================================================================
class TestExtractToolContextFromHistory:
    def test_empty_history(self):
        result = ConversationMemory.extract_tool_context_from_history([])
        assert result["tool_summaries"] == []
        assert result["has_context"] is False
        assert result["user_intents"] == []

    def test_with_user_query_and_mentions(self):
        convos = [
            {"role": "user_query", "content": "Send a message to @john in #general"},
        ]
        result = ConversationMemory.extract_tool_context_from_history(convos)
        assert "@john" in result["entities"]["mentions"]
        assert "#general" in result["entities"]["channels"]
        assert result["user_intents"] == ["Send a message to @john in #general"]

    def test_with_bot_response_tool_indicators(self):
        convos = [
            {"role": "bot_response", "content": "I retrieved 5 messages from #general channel."},
        ]
        result = ConversationMemory.extract_tool_context_from_history(convos)
        assert len(result["tool_summaries"]) == 1
        assert result["tool_summaries"][0]["action"] == "fetch"
        assert result["tool_summaries"][0]["target"] == "messages"
        assert result["has_context"] is True

    def test_bot_response_with_channels_entity(self):
        convos = [
            {"role": "bot_response", "content": "Retrieved messages from #random"},
        ]
        result = ConversationMemory.extract_tool_context_from_history(convos)
        assert "#random" in result["entities"]["channels"]

    def test_bot_response_with_urls(self):
        convos = [
            {"role": "bot_response", "content": "Found data at https://example.com/api/data"},
        ]
        result = ConversationMemory.extract_tool_context_from_history(convos)
        assert any("example.com" in url for url in result["entities"]["urls"])

    def test_bot_response_with_ids(self):
        convos = [
            {"role": "bot_response", "content": "Retrieved ticket ABC1234567 from the system"},
        ]
        result = ConversationMemory.extract_tool_context_from_history(convos)
        assert "ABC1234567" in result["entities"]["ids"]

    def test_no_tool_indicators_in_response(self):
        convos = [
            {"role": "bot_response", "content": "Hello, how can I help you?"},
        ]
        result = ConversationMemory.extract_tool_context_from_history(convos)
        assert result["tool_summaries"] == []

    def test_multiple_conversations(self):
        convos = [
            {"role": "user_query", "content": "List channels"},
            {"role": "bot_response", "content": "Found 10 channels in the workspace"},
            {"role": "user_query", "content": "Send a message to #general"},
            {"role": "bot_response", "content": "I sent a message to #general"},
        ]
        result = ConversationMemory.extract_tool_context_from_history(convos)
        assert len(result["tool_summaries"]) == 2
        assert result["has_context"] is True
        assert len(result["user_intents"]) == 2

    def test_user_intents_limited_to_last_three(self):
        convos = [
            {"role": "user_query", "content": f"query {i}"} for i in range(10)
        ]
        result = ConversationMemory.extract_tool_context_from_history(convos)
        assert len(result["user_intents"]) == 3
        assert result["user_intents"][-1] == "query 9"

    def test_entities_deduplication(self):
        convos = [
            {"role": "user_query", "content": "@alice @alice @alice in #general #general"},
        ]
        result = ConversationMemory.extract_tool_context_from_history(convos)
        assert result["entities"]["mentions"].count("@alice") == 1
        assert result["entities"]["channels"].count("#general") == 1

    def test_entities_limited_to_five(self):
        mentions = " ".join([f"@user{i}" for i in range(20)])
        convos = [{"role": "user_query", "content": mentions}]
        result = ConversationMemory.extract_tool_context_from_history(convos)
        assert len(result["entities"]["mentions"]) <= 5

    def test_tool_summary_created_action(self):
        convos = [
            {"role": "bot_response", "content": "I created a new message in the channel"},
        ]
        result = ConversationMemory.extract_tool_context_from_history(convos)
        assert len(result["tool_summaries"]) == 1
        assert result["tool_summaries"][0]["action"] == "create/send"
        assert result["tool_summaries"][0]["target"] == "message"

    def test_tool_summary_sent_action(self):
        convos = [
            {"role": "bot_response", "content": "I sent a post to the team"},
        ]
        result = ConversationMemory.extract_tool_context_from_history(convos)
        assert len(result["tool_summaries"]) == 1
        assert result["tool_summaries"][0]["action"] == "create/send"

    def test_tool_summary_search_with_count(self):
        convos = [
            {"role": "bot_response", "content": "I found 42 results in the database"},
        ]
        result = ConversationMemory.extract_tool_context_from_history(convos)
        assert len(result["tool_summaries"]) == 1
        assert result["tool_summaries"][0]["action"] == "search"
        assert result["tool_summaries"][0]["result"] == "42 items"

    def test_tool_summary_fetch_channels(self):
        convos = [
            {"role": "bot_response", "content": "Fetched channels from Slack workspace"},
        ]
        result = ConversationMemory.extract_tool_context_from_history(convos)
        summary = result["tool_summaries"][0]
        assert summary["action"] == "fetch"
        assert summary["target"] == "channels"

    def test_tool_summary_fetch_users(self):
        convos = [
            {"role": "bot_response", "content": "Retrieved the users from the org"},
        ]
        result = ConversationMemory.extract_tool_context_from_history(convos)
        summary = result["tool_summaries"][0]
        assert summary["action"] == "fetch"
        assert summary["target"] == "users"

    def test_missing_content_treated_as_empty_string(self):
        convos = [{"role": "user_query"}]
        result = ConversationMemory.extract_tool_context_from_history(convos)
        assert result["user_intents"] == [""]


# ===================================================================
# _extract_tool_summary (tested indirectly above, add edge-case tests)
# ===================================================================
class TestExtractToolSummary:
    def test_returns_none_for_no_indicators(self):
        result = ConversationMemory._extract_tool_summary("Just a normal response with no action words")
        assert result is None

    def test_returns_none_when_indicator_present_but_no_pattern_match(self):
        # "completed" is an indicator but none of the pattern branches match
        result = ConversationMemory._extract_tool_summary("The operation completed successfully")
        assert result is None

    def test_posted_action(self):
        result = ConversationMemory._extract_tool_summary("I posted a new message to the channel")
        assert result is not None
        assert result["action"] == "create/send"
        assert result["target"] == "message"


# ===================================================================
# build_context_reminder
# ===================================================================
class TestBuildContextReminder:
    def test_empty_memory(self):
        result = ConversationMemory.build_context_reminder({"has_context": False})
        assert result == ""

    def test_with_tool_summaries(self):
        memory = {
            "has_context": True,
            "tool_summaries": [
                {"action": "fetch", "target": "messages", "data_available": True},
            ],
            "entities": {},
            "user_intents": [],
        }
        result = ConversationMemory.build_context_reminder(memory)
        assert "fetch messages" in result
        assert "data still available" in result

    def test_with_channels(self):
        memory = {
            "has_context": True,
            "tool_summaries": [],
            "entities": {"channels": ["#general", "#random"], "mentions": [], "urls": [], "ids": [], "users": []},
            "user_intents": [],
        }
        result = ConversationMemory.build_context_reminder(memory)
        assert "#general" in result

    def test_with_mentions(self):
        memory = {
            "has_context": True,
            "tool_summaries": [],
            "entities": {"channels": [], "mentions": ["@alice"], "urls": [], "ids": [], "users": []},
            "user_intents": [],
        }
        result = ConversationMemory.build_context_reminder(memory)
        assert "@alice" in result

    def test_with_user_intents(self):
        memory = {
            "has_context": True,
            "tool_summaries": [],
            "entities": {},
            "user_intents": ["Check recent messages"],
        }
        result = ConversationMemory.build_context_reminder(memory)
        assert "Recent topic" in result

    def test_returns_empty_when_no_reminders(self):
        memory = {
            "has_context": True,
            "tool_summaries": [],
            "entities": {},
            "user_intents": [],
        }
        result = ConversationMemory.build_context_reminder(memory)
        assert result == ""


# ===================================================================
# should_reuse_tool_results
# ===================================================================
class TestShouldReuseToolResults:
    def test_empty_previous_conversations(self):
        assert ConversationMemory.should_reuse_tool_results("yes", []) is False

    def test_follow_up_yes(self):
        convos = [{"role": "user_query", "content": "hi"}]
        assert ConversationMemory.should_reuse_tool_results("yes", convos) is True

    def test_follow_up_ok(self):
        convos = [{"role": "user_query", "content": "hi"}]
        assert ConversationMemory.should_reuse_tool_results("ok", convos) is True

    def test_follow_up_sure(self):
        convos = [{"role": "user_query", "content": "hi"}]
        assert ConversationMemory.should_reuse_tool_results("sure", convos) is True

    def test_follow_up_go_ahead(self):
        convos = [{"role": "user_query", "content": "hi"}]
        assert ConversationMemory.should_reuse_tool_results("go ahead", convos) is True

    def test_follow_up_do_it(self):
        convos = [{"role": "user_query", "content": "hi"}]
        assert ConversationMemory.should_reuse_tool_results("do it", convos) is True

    def test_follow_up_please(self):
        convos = [{"role": "user_query", "content": "hi"}]
        assert ConversationMemory.should_reuse_tool_results("please", convos) is True

    def test_follow_up_send_it(self):
        convos = [{"role": "user_query", "content": "hi"}]
        assert ConversationMemory.should_reuse_tool_results("send it now", convos) is True

    def test_follow_up_share_it(self):
        convos = [{"role": "user_query", "content": "hi"}]
        assert ConversationMemory.should_reuse_tool_results("share it", convos) is True

    def test_follow_up_create_it(self):
        convos = [{"role": "user_query", "content": "hi"}]
        assert ConversationMemory.should_reuse_tool_results("create it", convos) is True

    def test_short_query_with_pronoun(self):
        convos = [{"role": "user_query", "content": "hi"}]
        assert ConversationMemory.should_reuse_tool_results("post it", convos) is True

    def test_short_query_with_that(self):
        convos = [{"role": "user_query", "content": "hi"}]
        assert ConversationMemory.should_reuse_tool_results("do that", convos) is True

    def test_new_topic_long_query(self):
        convos = [{"role": "user_query", "content": "hi"}]
        query = "What is the latest financial report for Q3 2025 revenue?"
        assert ConversationMemory.should_reuse_tool_results(query, convos) is False

    def test_new_topic_no_pronouns_no_patterns(self):
        convos = [{"role": "user_query", "content": "hi"}]
        assert ConversationMemory.should_reuse_tool_results("different question", convos) is False

    def test_case_insensitive(self):
        convos = [{"role": "user_query", "content": "hi"}]
        assert ConversationMemory.should_reuse_tool_results("YES", convos) is True
        assert ConversationMemory.should_reuse_tool_results("Go Ahead", convos) is True

    def test_now_pattern(self):
        convos = [{"role": "user_query", "content": "hi"}]
        assert ConversationMemory.should_reuse_tool_results("now", convos) is True

    def test_post_it_pattern(self):
        convos = [{"role": "user_query", "content": "hi"}]
        assert ConversationMemory.should_reuse_tool_results("post it", convos) is True


# ===================================================================
# enrich_query_with_context
# ===================================================================
class TestEnrichQueryWithContext:
    def test_empty_previous_conversations(self):
        result = ConversationMemory.enrich_query_with_context("hello", [])
        assert result == "hello"

    def test_non_follow_up_returns_original_query(self):
        convos = [{"role": "user_query", "content": "previous question"}]
        long_query = "What is the annual revenue for fiscal year 2025?"
        result = ConversationMemory.enrich_query_with_context(long_query, convos)
        assert result == long_query

    def test_follow_up_is_enriched(self):
        convos = [
            {"role": "user_query", "content": "list channels"},
            {"role": "bot_response", "content": "I retrieved 5 channels from Slack"},
        ]
        result = ConversationMemory.enrich_query_with_context("yes", convos)
        assert "**Current Request**: yes" in result

    def test_follow_up_with_tool_data_available(self):
        convos = [
            {"role": "user_query", "content": "get messages"},
            {"role": "bot_response", "content": "I retrieved messages from #general channel"},
        ]
        result = ConversationMemory.enrich_query_with_context("send it", convos)
        assert "follow-up" in result.lower()

    def test_enrichment_includes_context_reminder(self):
        convos = [
            {"role": "user_query", "content": "fetch channels"},
            {"role": "bot_response", "content": "Retrieved 3 channels from Slack"},
        ]
        result = ConversationMemory.enrich_query_with_context("ok", convos)
        assert "Context" in result or "Current Request" in result


# ===================================================================
# get_conversation_memory
# ===================================================================
class TestGetConversationMemory:
    def test_returns_instance(self):
        mem = get_conversation_memory()
        assert isinstance(mem, ConversationMemory)
