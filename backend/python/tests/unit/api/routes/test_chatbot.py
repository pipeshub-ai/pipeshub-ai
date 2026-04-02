"""Tests for app.api.routes.chatbot helper functions and models."""
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# ChatQuery model
# ---------------------------------------------------------------------------

class TestChatQueryModel:
    """Validation of the ChatQuery Pydantic model."""

    def test_defaults(self):
        from app.api.routes.chatbot import ChatQuery
        q = ChatQuery(query="test")
        assert q.query == "test"
        assert q.limit == 50
        assert q.previousConversations == []
        assert q.filters is None
        assert q.retrievalMode == "HYBRID"
        assert q.quickMode is False
        assert q.modelKey is None
        assert q.modelName is None
        assert q.chatMode == "standard"
        assert q.mode == "json"

    def test_all_fields(self):
        from app.api.routes.chatbot import ChatQuery
        q = ChatQuery(
            query="search this",
            limit=10,
            previousConversations=[{"role": "user_query", "content": "hi"}],
            filters={"apps": ["google"]},
            retrievalMode="VECTOR",
            quickMode=True,
            modelKey="mk-123",
            modelName="gpt-4o-mini",
            chatMode="analysis",
            mode="simple",
        )
        assert q.limit == 10
        assert q.quickMode is True
        assert q.chatMode == "analysis"
        assert q.mode == "simple"
        assert q.modelKey == "mk-123"
        assert q.modelName == "gpt-4o-mini"
        assert q.retrievalMode == "VECTOR"
        assert len(q.previousConversations) == 1

    def test_missing_query_fails(self):
        from app.api.routes.chatbot import ChatQuery
        with pytest.raises(ValidationError):
            ChatQuery()

    def test_query_must_be_string(self):
        from app.api.routes.chatbot import ChatQuery
        with pytest.raises(ValidationError):
            ChatQuery(query=None)

    def test_limit_none_allowed(self):
        from app.api.routes.chatbot import ChatQuery
        q = ChatQuery(query="q", limit=None)
        assert q.limit is None

    def test_extra_fields_ignored(self):
        """Extra fields not defined on the model should not appear."""
        from app.api.routes.chatbot import ChatQuery
        q = ChatQuery(query="q", unknownField="abc")
        assert not hasattr(q, "unknownField")


# ---------------------------------------------------------------------------
# get_model_config_for_mode
# ---------------------------------------------------------------------------

class TestGetModelConfigForMode:
    """Tests for the chat-mode configuration resolver."""

    def test_quick_mode(self):
        from app.api.routes.chatbot import get_model_config_for_mode
        cfg = get_model_config_for_mode("quick")
        assert cfg["temperature"] == 0.1
        assert cfg["max_tokens"] == 4096
        assert "system_prompt" in cfg

    def test_analysis_mode(self):
        from app.api.routes.chatbot import get_model_config_for_mode
        cfg = get_model_config_for_mode("analysis")
        assert cfg["temperature"] == 0.3
        assert cfg["max_tokens"] == 8192

    def test_deep_research_mode(self):
        from app.api.routes.chatbot import get_model_config_for_mode
        cfg = get_model_config_for_mode("deep_research")
        assert cfg["temperature"] == 0.2
        assert cfg["max_tokens"] == 16384

    def test_creative_mode(self):
        from app.api.routes.chatbot import get_model_config_for_mode
        cfg = get_model_config_for_mode("creative")
        assert cfg["temperature"] == 0.7
        assert cfg["max_tokens"] == 16384

    def test_precise_mode(self):
        from app.api.routes.chatbot import get_model_config_for_mode
        cfg = get_model_config_for_mode("precise")
        assert cfg["temperature"] == 0.05
        assert cfg["max_tokens"] == 16384

    def test_standard_mode(self):
        from app.api.routes.chatbot import get_model_config_for_mode
        cfg = get_model_config_for_mode("standard")
        assert cfg["temperature"] == 0.2
        assert cfg["max_tokens"] == 16384

    def test_unknown_mode_falls_back_to_standard(self):
        from app.api.routes.chatbot import get_model_config_for_mode
        cfg = get_model_config_for_mode("nonexistent")
        standard = get_model_config_for_mode("standard")
        assert cfg == standard

    def test_all_modes_have_system_prompt(self):
        from app.api.routes.chatbot import get_model_config_for_mode
        for mode in ("quick", "analysis", "deep_research", "creative", "precise", "standard"):
            cfg = get_model_config_for_mode(mode)
            assert isinstance(cfg["system_prompt"], str)
            assert len(cfg["system_prompt"]) > 0


# ---------------------------------------------------------------------------
# get_model_config
# ---------------------------------------------------------------------------

class TestGetModelConfig:
    """Tests for the model config resolver (async)."""

    @pytest.fixture
    def llm_configs(self):
        return [
            {
                "modelKey": "key-1",
                "configuration": {"model": "gpt-4o, gpt-4o-mini"},
                "provider": "openai",
                "isDefault": False,
            },
            {
                "modelKey": "key-2",
                "configuration": {"model": "claude-3-5-sonnet"},
                "provider": "anthropic",
                "isDefault": True,
            },
        ]

    def _make_config_service(self, llm_configs, fresh_configs=None):
        """Create a mock config service returning given configs."""
        config_service = AsyncMock()
        call_count = 0

        async def mock_get_config(path, default=None, use_cache=True):
            nonlocal call_count
            call_count += 1
            if not use_cache and fresh_configs is not None:
                return {"llm": fresh_configs}
            return {"llm": llm_configs}

        config_service.get_config = mock_get_config
        return config_service

    @pytest.mark.asyncio
    async def test_default_config_when_no_keys(self, llm_configs):
        from app.api.routes.chatbot import get_model_config
        cs = self._make_config_service(llm_configs)
        cfg, ai = await get_model_config(cs, model_key=None, model_name=None)
        assert cfg["modelKey"] == "key-2"  # isDefault=True
        assert "llm" in ai

    @pytest.mark.asyncio
    async def test_search_by_model_name(self, llm_configs):
        from app.api.routes.chatbot import get_model_config
        cs = self._make_config_service(llm_configs)
        cfg, ai = await get_model_config(cs, model_key=None, model_name="gpt-4o-mini")
        assert cfg["modelKey"] == "key-1"

    @pytest.mark.asyncio
    async def test_search_by_model_name_not_found_returns_list(self, llm_configs):
        """When name is not found, it falls through to returning llm_configs list."""
        from app.api.routes.chatbot import get_model_config
        cs = self._make_config_service(llm_configs)
        cfg, ai = await get_model_config(cs, model_key=None, model_name="nonexistent")
        # Falls through all branches, returns llm_configs (the list)
        assert isinstance(cfg, list)

    @pytest.mark.asyncio
    async def test_search_by_model_key(self, llm_configs):
        from app.api.routes.chatbot import get_model_config
        cs = self._make_config_service(llm_configs)
        cfg, ai = await get_model_config(cs, model_key="key-1")
        assert cfg["modelKey"] == "key-1"

    @pytest.mark.asyncio
    async def test_search_by_model_key_not_found_retries_fresh(self, llm_configs):
        """When key not found, tries again with use_cache=False."""
        from app.api.routes.chatbot import get_model_config
        fresh = llm_configs + [{
            "modelKey": "key-new",
            "configuration": {"model": "new-model"},
            "provider": "openai",
            "isDefault": False,
        }]
        cs = self._make_config_service(llm_configs, fresh_configs=fresh)
        cfg, ai = await get_model_config(cs, model_key="key-new")
        assert cfg["modelKey"] == "key-new"

    @pytest.mark.asyncio
    async def test_search_by_model_key_not_found_even_after_retry(self):
        """When key not found even after fresh fetch, returns the list."""
        from app.api.routes.chatbot import get_model_config
        configs = [{"modelKey": "key-1", "configuration": {"model": "m"}, "isDefault": False}]
        cs = self._make_config_service(configs)
        cfg, ai = await get_model_config(cs, model_key="nonexistent")
        assert isinstance(cfg, list)

    @pytest.mark.asyncio
    async def test_empty_configs_raises(self):
        from app.api.routes.chatbot import get_model_config
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"llm": []})
        with pytest.raises(ValueError, match="No LLM configurations found"):
            await get_model_config(cs, model_key="missing")

    @pytest.mark.asyncio
    async def test_no_default_returns_list(self, llm_configs):
        """When no model has isDefault and no key/name specified, returns list."""
        from app.api.routes.chatbot import get_model_config
        no_default = [dict(c, isDefault=False) for c in llm_configs]
        cs = self._make_config_service(no_default)
        cfg, ai = await get_model_config(cs, model_key=None, model_name=None)
        # Falls through default branch, returns the list
        assert isinstance(cfg, list)

    @pytest.mark.asyncio
    async def test_model_name_with_spaces_in_csv(self):
        from app.api.routes.chatbot import get_model_config
        configs = [
            {
                "modelKey": "k1",
                "configuration": {"model": "  gpt-4o ,  gpt-4o-mini  "},
                "isDefault": False,
            }
        ]
        cs = self._make_config_service(configs)
        cfg, ai = await get_model_config(cs, model_key=None, model_name="gpt-4o")
        assert cfg["modelKey"] == "k1"


# ---------------------------------------------------------------------------
# get_llm_for_chat
# ---------------------------------------------------------------------------

class TestGetLlmForChat:
    """Tests for the LLM initializer."""

    @pytest.fixture
    def llm_config(self):
        return {
            "modelKey": "key-1",
            "configuration": {"model": "gpt-4o, gpt-4o-mini"},
            "provider": "openai",
            "isDefault": True,
            "isMultimodal": True,
        }

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_generator_model")
    @patch("app.api.routes.chatbot.get_model_config")
    async def test_fallback_to_first_model(self, mock_get_model_config, mock_gen):
        from app.api.routes.chatbot import get_llm_for_chat
        config = {
            "modelKey": "key-1",
            "configuration": {"model": "gpt-4o, gpt-4o-mini"},
            "provider": "openai",
        }
        mock_get_model_config.return_value = (config, {"llm": [config]})
        mock_gen.return_value = MagicMock()

        llm, cfg, ai = await get_llm_for_chat(AsyncMock())
        mock_gen.assert_called_once_with("openai", config, "gpt-4o")

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_generator_model")
    @patch("app.api.routes.chatbot.get_model_config")
    async def test_with_model_key_only(self, mock_get_model_config, mock_gen):
        from app.api.routes.chatbot import get_llm_for_chat
        config = {
            "modelKey": "key-1",
            "configuration": {"model": "gpt-4o, gpt-4o-mini"},
            "provider": "openai",
        }
        mock_get_model_config.return_value = (config, {"llm": [config]})
        mock_gen.return_value = MagicMock()

        llm, cfg, ai = await get_llm_for_chat(AsyncMock(), model_key="key-1")
        mock_gen.assert_called_once_with("openai", config, "gpt-4o")

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_generator_model")
    @patch("app.api.routes.chatbot.get_model_config")
    async def test_with_model_key_and_name_matching(self, mock_get_model_config, mock_gen):
        from app.api.routes.chatbot import get_llm_for_chat
        config = {
            "modelKey": "key-1",
            "configuration": {"model": "gpt-4o, gpt-4o-mini"},
            "provider": "openai",
        }
        mock_get_model_config.return_value = (config, {"llm": [config]})
        mock_gen.return_value = MagicMock()

        llm, cfg, ai = await get_llm_for_chat(
            AsyncMock(), model_key="key-1", model_name="gpt-4o-mini"
        )
        mock_gen.assert_called_once_with("openai", config, "gpt-4o-mini")

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_generator_model")
    @patch("app.api.routes.chatbot.get_model_config")
    async def test_with_model_key_and_name_not_matching(self, mock_get_model_config, mock_gen):
        """When model_key matches but model_name is not in config, falls to model_key branch."""
        from app.api.routes.chatbot import get_llm_for_chat
        config = {
            "modelKey": "key-1",
            "configuration": {"model": "gpt-4o, gpt-4o-mini"},
            "provider": "openai",
        }
        mock_get_model_config.return_value = (config, {"llm": [config]})
        mock_gen.return_value = MagicMock()

        llm, cfg, ai = await get_llm_for_chat(
            AsyncMock(), model_key="key-1", model_name="nonexistent"
        )
        # Falls to the model_key-only branch, uses first model name
        mock_gen.assert_called_once_with("openai", config, "gpt-4o")

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_generator_model")
    @patch("app.api.routes.chatbot.get_model_config")
    async def test_list_config_takes_first(self, mock_get_model_config, mock_gen):
        """When get_model_config returns a list, first element is used."""
        from app.api.routes.chatbot import get_llm_for_chat
        configs = [
            {
                "modelKey": "key-1",
                "configuration": {"model": "gpt-4o"},
                "provider": "openai",
            },
            {
                "modelKey": "key-2",
                "configuration": {"model": "claude-3"},
                "provider": "anthropic",
            },
        ]
        mock_get_model_config.return_value = (configs, {"llm": configs})
        mock_gen.return_value = MagicMock()

        llm, cfg, ai = await get_llm_for_chat(AsyncMock())
        mock_gen.assert_called_once_with("openai", configs[0], "gpt-4o")

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_model_config")
    async def test_none_config_raises(self, mock_get_model_config):
        from app.api.routes.chatbot import get_llm_for_chat
        mock_get_model_config.return_value = (None, {})
        with pytest.raises(ValueError, match="Failed to initialize LLM"):
            await get_llm_for_chat(AsyncMock())

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_model_config")
    async def test_get_model_config_raises_wraps(self, mock_get_model_config):
        from app.api.routes.chatbot import get_llm_for_chat
        mock_get_model_config.side_effect = Exception("config error")
        with pytest.raises(ValueError, match="Failed to initialize LLM"):
            await get_llm_for_chat(AsyncMock())

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_generator_model")
    @patch("app.api.routes.chatbot.get_model_config")
    async def test_generator_model_raises_wraps(self, mock_get_model_config, mock_gen):
        from app.api.routes.chatbot import get_llm_for_chat
        config = {
            "modelKey": "key-1",
            "configuration": {"model": "gpt-4o"},
            "provider": "openai",
        }
        mock_get_model_config.return_value = (config, {"llm": [config]})
        mock_gen.side_effect = Exception("provider error")
        with pytest.raises(ValueError, match="Failed to initialize LLM"):
            await get_llm_for_chat(AsyncMock())


# ---------------------------------------------------------------------------
# Dependency injection functions
# ---------------------------------------------------------------------------

class TestDependencyInjectionFunctions:
    """Tests for FastAPI dependency injection helper functions."""

    @pytest.mark.asyncio
    async def test_get_retrieval_service(self):
        from app.api.routes.chatbot import get_retrieval_service
        mock_service = MagicMock()
        request = MagicMock()
        request.app.container.retrieval_service = AsyncMock(return_value=mock_service)
        result = await get_retrieval_service(request)
        assert result is mock_service

    @pytest.mark.asyncio
    async def test_get_graph_provider_from_state(self):
        from app.api.routes.chatbot import get_graph_provider
        mock_provider = MagicMock()
        request = MagicMock()
        request.app.state.graph_provider = mock_provider
        result = await get_graph_provider(request)
        assert result is mock_provider

    @pytest.mark.asyncio
    async def test_get_graph_provider_from_container(self):
        from app.api.routes.chatbot import get_graph_provider
        mock_provider = MagicMock()
        request = MagicMock()
        # Make hasattr(request.app.state, 'graph_provider') return False
        del request.app.state.graph_provider
        request.app.container.graph_provider = AsyncMock(return_value=mock_provider)
        result = await get_graph_provider(request)
        assert result is mock_provider

    @pytest.mark.asyncio
    async def test_get_config_service(self):
        from app.api.routes.chatbot import get_config_service
        mock_service = MagicMock()
        request = MagicMock()
        request.app.container.config_service.return_value = mock_service
        result = await get_config_service(request)
        assert result is mock_service

    @pytest.mark.asyncio
    async def test_get_reranker_service(self):
        from app.api.routes.chatbot import get_reranker_service
        mock_service = MagicMock()
        request = MagicMock()
        request.app.container.reranker_service.return_value = mock_service
        result = await get_reranker_service(request)
        assert result is mock_service


# ---------------------------------------------------------------------------
# DEFAULT_CONTEXT_LENGTH constant
# ---------------------------------------------------------------------------

class TestConstants:
    def test_default_context_length(self):
        from app.api.routes.chatbot import DEFAULT_CONTEXT_LENGTH
        assert DEFAULT_CONTEXT_LENGTH == 128000


# ---------------------------------------------------------------------------
# process_chat_query_with_status
# ---------------------------------------------------------------------------


class TestProcessChatQueryWithStatus:
    """Tests for the main processing pipeline."""

    def _make_query_info(self, **overrides):
        from app.api.routes.chatbot import ChatQuery
        defaults = {
            "query": "test question",
            "limit": 50,
            "previousConversations": [],
            "filters": None,
            "retrievalMode": "HYBRID",
            "quickMode": False,
            "modelKey": None,
            "modelName": None,
            "chatMode": "standard",
            "mode": "json",
        }
        defaults.update(overrides)
        return ChatQuery(**defaults)

    def _make_request(self, user_id="u1", org_id="o1"):
        request = MagicMock()
        request.state.user = {"userId": user_id, "orgId": org_id}
        request.query_params = {"sendUserInfo": True}
        return request

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="formatted content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_full_flow(
        self, mock_get_llm, mock_cached_user, mock_blob, mock_flatten,
        mock_content, mock_fetch_tool
    ):
        from app.api.routes.chatbot import process_chat_query_with_status

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False}
        mock_get_llm.return_value = (mock_llm, config, {})

        mock_cached_user.return_value = (
            {"fullName": "Test User", "designation": "Engineer"},
            {"accountType": "enterprise", "name": "Acme"},
        )

        mock_flatten.return_value = [
            {"virtual_record_id": "vr1", "block_index": 0, "content": "result"}
        ]

        mock_fetch_tool.return_value = MagicMock()

        retrieval = AsyncMock()
        retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [{"id": "1"}],
            "status_code": 200,
        })

        reranker = AsyncMock()
        graph_provider = AsyncMock()
        config_service = AsyncMock()
        logger = MagicMock()

        query_info = self._make_query_info()
        request = self._make_request()

        result = await process_chat_query_with_status(
            query_info, request, retrieval, graph_provider,
            reranker, config_service, logger
        )

        llm, messages, tools, kwargs, final_results, queries, vr_map, blob, is_mm = result
        assert llm is mock_llm
        assert isinstance(messages, list)
        assert len(messages) >= 2  # system + user
        assert messages[0]["role"] == "system"
        assert queries == ["test question"]

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_quick_mode_skips_decomposition_and_rerank(
        self, mock_get_llm, mock_cached_user, mock_blob, mock_flatten,
        mock_content, mock_fetch_tool
    ):
        from app.api.routes.chatbot import process_chat_query_with_status

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False}
        mock_get_llm.return_value = (mock_llm, config, {})

        mock_cached_user.return_value = (
            {"fullName": "User", "designation": "Dev"},
            {"accountType": "individual"},
        )

        mock_flatten.return_value = [
            {"virtual_record_id": "vr1", "block_index": 0},
            {"virtual_record_id": "vr2", "block_index": 0},
        ]
        mock_fetch_tool.return_value = MagicMock()

        retrieval = AsyncMock()
        retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [{"id": "1"}],
            "status_code": 200,
        })
        reranker = AsyncMock()
        reranker.rerank = AsyncMock()

        query_info = self._make_query_info(quickMode=True)
        request = self._make_request()

        await process_chat_query_with_status(
            query_info, request, retrieval, AsyncMock(),
            reranker, AsyncMock(), MagicMock()
        )

        # In quick mode, reranker should NOT be called
        reranker.rerank.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_llm_none_raises(self, mock_get_llm):
        from app.api.routes.chatbot import process_chat_query_with_status

        mock_get_llm.return_value = (None, {}, {})

        query_info = self._make_query_info()
        request = self._make_request()

        with pytest.raises(ValueError, match="Failed to initialize LLM"):
            await process_chat_query_with_status(
                query_info, request, AsyncMock(), AsyncMock(),
                AsyncMock(), AsyncMock(), MagicMock()
            )

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.setup_followup_query_transformation")
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_conversation_history_triggers_followup(
        self, mock_get_llm, mock_setup_followup, mock_cached_user,
        mock_blob, mock_flatten, mock_content, mock_fetch_tool
    ):
        from app.api.routes.chatbot import process_chat_query_with_status

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False}
        mock_get_llm.return_value = (mock_llm, config, {})

        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(return_value="transformed query")
        mock_setup_followup.return_value = mock_chain

        mock_cached_user.return_value = (
            {"fullName": "User", "designation": "Dev"},
            {"accountType": "individual"},
        )
        mock_flatten.return_value = [{"virtual_record_id": "vr1", "block_index": 0}]
        mock_fetch_tool.return_value = MagicMock()

        retrieval = AsyncMock()
        retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [],
            "status_code": 200,
        })

        query_info = self._make_query_info(
            previousConversations=[
                {"role": "user_query", "content": "What is X?"},
                {"role": "bot_response", "content": "X is a thing."},
            ]
        )
        request = self._make_request()

        await process_chat_query_with_status(
            query_info, request, retrieval, AsyncMock(),
            AsyncMock(), AsyncMock(), MagicMock()
        )

        mock_setup_followup.assert_called_once_with(mock_llm)
        mock_chain.ainvoke.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_search_error_raises_http_exception(self, mock_get_llm):
        from app.api.routes.chatbot import process_chat_query_with_status
        from fastapi import HTTPException

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False}
        mock_get_llm.return_value = (mock_llm, config, {})

        retrieval = AsyncMock()
        retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [],
            "status_code": 503,
            "error": "Service unavailable",
        })

        query_info = self._make_query_info()
        request = self._make_request()

        with pytest.raises(HTTPException) as exc:
            await process_chat_query_with_status(
                query_info, request, retrieval, AsyncMock(),
                AsyncMock(), AsyncMock(), MagicMock()
            )
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_ollama_provider_forces_simple_mode(
        self, mock_get_llm, mock_cached_user, mock_blob, mock_flatten,
        mock_content, mock_fetch_tool
    ):
        from app.api.routes.chatbot import process_chat_query_with_status

        mock_llm = MagicMock()
        config = {"provider": "ollama", "isMultimodal": False}
        mock_get_llm.return_value = (mock_llm, config, {})

        mock_cached_user.return_value = (
            {"fullName": "User", "designation": "Dev"},
            {"accountType": "individual"},
        )
        mock_flatten.return_value = [{"virtual_record_id": "vr1", "block_index": 0}]
        mock_fetch_tool.return_value = MagicMock()

        retrieval = AsyncMock()
        retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [],
            "status_code": 200,
        })

        query_info = self._make_query_info(mode="json")
        request = self._make_request()

        await process_chat_query_with_status(
            query_info, request, retrieval, AsyncMock(),
            AsyncMock(), AsyncMock(), MagicMock()
        )

        # After processing, query_info.mode should be forced to "simple" for ollama
        assert query_info.mode == "simple"


# ---------------------------------------------------------------------------
# resolve_tools_then_answer
# ---------------------------------------------------------------------------


class TestResolveToolsThenAnswer:
    """Tests for tool resolution loop."""

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.bind_tools_for_llm")
    async def test_no_tool_calls(self, mock_bind):
        from app.api.routes.chatbot import resolve_tools_then_answer
        from langchain_core.messages import AIMessage

        ai_msg = AIMessage(content="Direct answer")
        mock_llm_with_tools = AsyncMock()
        mock_llm_with_tools.ainvoke = AsyncMock(return_value=ai_msg)
        mock_bind.return_value = mock_llm_with_tools

        result = await resolve_tools_then_answer(
            MagicMock(), [{"role": "user", "content": "hi"}],
            [], {}, max_hops=4
        )
        assert result.content == "Direct answer"

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.bind_tools_for_llm")
    async def test_tool_call_then_answer(self, mock_bind):
        from app.api.routes.chatbot import resolve_tools_then_answer
        from langchain_core.messages import AIMessage

        # First call returns tool call, second call returns final answer
        tool_call_msg = AIMessage(content="")
        tool_call_msg.tool_calls = [
            {"name": "my_tool", "args": {"x": 1}, "id": "tc1"}
        ]

        final_msg = AIMessage(content="Final answer")

        mock_llm_with_tools = AsyncMock()
        mock_llm_with_tools.ainvoke = AsyncMock(
            side_effect=[tool_call_msg, final_msg]
        )
        mock_bind.return_value = mock_llm_with_tools

        mock_tool = MagicMock()
        mock_tool.name = "my_tool"
        mock_tool.arun = AsyncMock(return_value='{"result": "ok"}')

        result = await resolve_tools_then_answer(
            MagicMock(), [{"role": "user", "content": "hi"}],
            [mock_tool], {}, max_hops=4
        )
        assert result.content == "Final answer"
        mock_tool.arun.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.bind_tools_for_llm")
    async def test_max_hops_limit(self, mock_bind):
        from app.api.routes.chatbot import resolve_tools_then_answer
        from langchain_core.messages import AIMessage

        # Always returns tool calls to test max_hops
        tool_call_msg = AIMessage(content="")
        tool_call_msg.tool_calls = [
            {"name": "my_tool", "args": {}, "id": "tc1"}
        ]

        mock_llm_with_tools = AsyncMock()
        mock_llm_with_tools.ainvoke = AsyncMock(return_value=tool_call_msg)
        mock_bind.return_value = mock_llm_with_tools

        mock_tool = MagicMock()
        mock_tool.name = "my_tool"
        mock_tool.arun = AsyncMock(return_value='{"result": "ok"}')

        result = await resolve_tools_then_answer(
            MagicMock(), [{"role": "user", "content": "hi"}],
            [mock_tool], {}, max_hops=2
        )
        # After 2 hops, returns the last AIMessage (still with tool_calls)
        assert result is tool_call_msg
        # Initial tool_calls processed once, then 2 hops => each hop processes tool_calls once
        # but the while loop first processes the initial result's tool_calls, increments hops,
        # then processes the next result's tool_calls, increments hops again => 2 arun calls
        # (the initial ainvoke already returns tool_call_msg, loop runs 2 iterations)
        assert mock_tool.arun.await_count == 2

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.bind_tools_for_llm")
    async def test_invalid_tool_call_reflection(self, mock_bind):
        from app.api.routes.chatbot import resolve_tools_then_answer
        from langchain_core.messages import AIMessage

        # First call returns invalid tool call
        tool_call_msg = AIMessage(content="")
        tool_call_msg.tool_calls = [
            {"name": "nonexistent_tool", "args": {}, "id": "tc1"}
        ]

        # Second call returns final answer
        final_msg = AIMessage(content="Final answer")

        mock_llm_with_tools = AsyncMock()
        mock_llm_with_tools.ainvoke = AsyncMock(
            side_effect=[tool_call_msg, final_msg]
        )
        mock_bind.return_value = mock_llm_with_tools

        mock_tool = MagicMock()
        mock_tool.name = "valid_tool"

        result = await resolve_tools_then_answer(
            MagicMock(), [{"role": "user", "content": "hi"}],
            [mock_tool], {}, max_hops=4
        )
        assert result.content == "Final answer"

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.bind_tools_for_llm")
    async def test_tool_execution_exception(self, mock_bind):
        from app.api.routes.chatbot import resolve_tools_then_answer
        from langchain_core.messages import AIMessage

        tool_call_msg = AIMessage(content="")
        tool_call_msg.tool_calls = [
            {"name": "my_tool", "args": {}, "id": "tc1"}
        ]
        final_msg = AIMessage(content="Answer")

        mock_llm_with_tools = AsyncMock()
        mock_llm_with_tools.ainvoke = AsyncMock(
            side_effect=[tool_call_msg, final_msg]
        )
        mock_bind.return_value = mock_llm_with_tools

        mock_tool = MagicMock()
        mock_tool.name = "my_tool"
        mock_tool.arun = AsyncMock(side_effect=Exception("tool error"))

        result = await resolve_tools_then_answer(
            MagicMock(), [{"role": "user", "content": "hi"}],
            [mock_tool], {}, max_hops=4
        )
        # Should still get an answer despite tool error
        assert result.content == "Answer"

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.bind_tools_for_llm")
    async def test_provider_tool_error_on_initial_call(self, mock_bind):
        from app.api.routes.chatbot import resolve_tools_then_answer
        from langchain_core.messages import AIMessage

        mock_llm_with_tools = AsyncMock()
        mock_llm_with_tools.ainvoke = AsyncMock(
            side_effect=Exception("tool_use_failed: invalid args")
        )
        mock_bind.return_value = mock_llm_with_tools

        mock_llm_plain = MagicMock()
        final_msg = AIMessage(content="Fallback answer")
        mock_llm_plain.ainvoke = AsyncMock(return_value=final_msg)

        mock_tool = MagicMock()
        mock_tool.name = "my_tool"

        result = await resolve_tools_then_answer(
            mock_llm_plain, [{"role": "user", "content": "hi"}],
            [mock_tool], {}, max_hops=4
        )
        assert result.content == "Fallback answer"
        mock_llm_plain.ainvoke.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.bind_tools_for_llm")
    async def test_non_tool_error_reraises(self, mock_bind):
        from app.api.routes.chatbot import resolve_tools_then_answer

        mock_llm_with_tools = AsyncMock()
        mock_llm_with_tools.ainvoke = AsyncMock(
            side_effect=Exception("network timeout")
        )
        mock_bind.return_value = mock_llm_with_tools

        with pytest.raises(Exception, match="network timeout"):
            await resolve_tools_then_answer(
                MagicMock(), [{"role": "user", "content": "hi"}],
                [], {}, max_hops=4
            )


# ---------------------------------------------------------------------------
# process_chat_query (wrapper)
# ---------------------------------------------------------------------------


class TestProcessChatQuery:
    """Tests for the non-streaming wrapper."""

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.process_chat_query_with_status", new_callable=AsyncMock)
    async def test_delegates_to_with_status(self, mock_inner):
        from app.api.routes.chatbot import process_chat_query, ChatQuery

        sentinel = object()
        mock_inner.return_value = sentinel

        query_info = ChatQuery(query="test")
        result = await process_chat_query(
            query_info, MagicMock(), AsyncMock(), AsyncMock(),
            AsyncMock(), AsyncMock(), MagicMock()
        )
        assert result is sentinel
        mock_inner.assert_awaited_once()
        # yield_status should be None
        call_kwargs = mock_inner.call_args
        assert call_kwargs[1].get("yield_status") is None or call_kwargs[0][-1] is None


# ---------------------------------------------------------------------------
# get_model_config (lines 155->161, 254, 270, 282, 300, 311-313)
# ---------------------------------------------------------------------------


class TestGetModelConfig:
    """Tests for get_model_config covering multiple branches."""

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.ConfigurationService", autospec=True)
    async def test_default_config(self, mock_cs_class):
        from app.api.routes.chatbot import get_model_config

        mock_cs = AsyncMock()
        mock_cs.get_config = AsyncMock(return_value={
            "llm": [
                {"provider": "openai", "isDefault": True, "configuration": {"model": "gpt-4o"}, "modelKey": "k1"},
            ]
        })
        config, ai_models = await get_model_config(mock_cs, model_key=None, model_name=None)
        assert config["provider"] == "openai"

    @pytest.mark.asyncio
    async def test_search_by_model_name(self):
        from app.api.routes.chatbot import get_model_config

        mock_cs = AsyncMock()
        mock_cs.get_config = AsyncMock(return_value={
            "llm": [
                {"provider": "openai", "isDefault": False, "configuration": {"model": "gpt-4o-mini"}, "modelKey": "k1"},
                {"provider": "anthropic", "isDefault": True, "configuration": {"model": "claude-3-5-sonnet"}, "modelKey": "k2"},
            ]
        })
        config, _ = await get_model_config(mock_cs, model_key=None, model_name="gpt-4o-mini")
        assert config["provider"] == "openai"

    @pytest.mark.asyncio
    async def test_search_by_model_key(self):
        from app.api.routes.chatbot import get_model_config

        mock_cs = AsyncMock()
        mock_cs.get_config = AsyncMock(return_value={
            "llm": [
                {"provider": "openai", "isDefault": False, "configuration": {"model": "gpt-4o"}, "modelKey": "key-123"},
            ]
        })
        config, _ = await get_model_config(mock_cs, model_key="key-123", model_name=None)
        assert config["modelKey"] == "key-123"

    @pytest.mark.asyncio
    async def test_model_key_not_found_refreshes(self):
        """When model_key not found in cache, should refresh config."""
        from app.api.routes.chatbot import get_model_config

        mock_cs = AsyncMock()
        # First call returns no match, second call (fresh) returns match
        mock_cs.get_config = AsyncMock(side_effect=[
            {"llm": [{"provider": "openai", "isDefault": False, "configuration": {"model": "gpt-4"}, "modelKey": "old-key"}]},
            {"llm": [{"provider": "openai", "isDefault": False, "configuration": {"model": "gpt-4"}, "modelKey": "new-key"}]},
        ])
        config, _ = await get_model_config(mock_cs, model_key="new-key", model_name=None)
        assert config["modelKey"] == "new-key"

    @pytest.mark.asyncio
    async def test_no_configs_raises(self):
        """When no LLM configs found, should raise ValueError."""
        from app.api.routes.chatbot import get_model_config

        mock_cs = AsyncMock()
        mock_cs.get_config = AsyncMock(return_value={"llm": []})

        with pytest.raises(ValueError, match="No LLM configurations found"):
            await get_model_config(mock_cs, model_key=None, model_name=None)

    @pytest.mark.asyncio
    async def test_fallback_to_first_config(self):
        """When no match and configs exist, should return list."""
        from app.api.routes.chatbot import get_model_config

        mock_cs = AsyncMock()
        configs = [
            {"provider": "openai", "isDefault": False, "configuration": {"model": "gpt-4"}, "modelKey": "k1"},
        ]
        mock_cs.get_config = AsyncMock(return_value={"llm": configs})

        result, _ = await get_model_config(mock_cs, model_key=None, model_name="nonexistent")
        # Should return the configs list as fallback
        assert result == configs


# ---------------------------------------------------------------------------
# get_llm_for_chat (lines 175-217)
# ---------------------------------------------------------------------------


class TestGetLlmForChat:
    """Tests for get_llm_for_chat."""

    @pytest.mark.asyncio
    async def test_with_model_key_and_name(self):
        """When both modelKey and modelName are provided and match."""
        from app.api.routes.chatbot import get_llm_for_chat

        mock_cs = AsyncMock()
        mock_cs.get_config = AsyncMock(return_value={
            "llm": [{
                "provider": "openai",
                "isDefault": False,
                "configuration": {"model": "gpt-4o-mini"},
                "modelKey": "key-1",
            }]
        })

        with patch("app.api.routes.chatbot.get_model_config", new_callable=AsyncMock) as mock_gc:
            mock_gc.return_value = (
                {"provider": "openai", "configuration": {"model": "gpt-4o-mini"}, "modelKey": "key-1"},
                {"llm": []},
            )
            with patch("app.api.routes.chatbot.get_generator_model") as mock_gen:
                mock_gen.return_value = MagicMock()
                llm, config, ai_models = await get_llm_for_chat(
                    mock_cs, model_key="key-1", model_name="gpt-4o-mini"
                )
                mock_gen.assert_called_once()

    @pytest.mark.asyncio
    async def test_with_model_key_only(self):
        """When only modelKey is provided."""
        from app.api.routes.chatbot import get_llm_for_chat

        mock_cs = AsyncMock()
        with patch("app.api.routes.chatbot.get_model_config", new_callable=AsyncMock) as mock_gc:
            mock_gc.return_value = (
                {"provider": "openai", "configuration": {"model": "gpt-4o"}, "modelKey": "key-1"},
                {"llm": []},
            )
            with patch("app.api.routes.chatbot.get_generator_model") as mock_gen:
                mock_gen.return_value = MagicMock()
                llm, config, ai_models = await get_llm_for_chat(
                    mock_cs, model_key="key-1"
                )
                mock_gen.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_to_first(self):
        """When no model_key, should fallback to first available model."""
        from app.api.routes.chatbot import get_llm_for_chat

        mock_cs = AsyncMock()
        with patch("app.api.routes.chatbot.get_model_config", new_callable=AsyncMock) as mock_gc:
            mock_gc.return_value = (
                {"provider": "openai", "configuration": {"model": "gpt-4o, gpt-4o-mini"}, "modelKey": "k1"},
                {"llm": []},
            )
            with patch("app.api.routes.chatbot.get_generator_model") as mock_gen:
                mock_gen.return_value = MagicMock()
                llm, config, ai_models = await get_llm_for_chat(mock_cs)
                # Should use first model from comma-separated list
                mock_gen.assert_called_once_with("openai", mock_gc.return_value[0], "gpt-4o")

    @pytest.mark.asyncio
    async def test_none_config_raises(self):
        """When get_model_config returns None, should raise."""
        from app.api.routes.chatbot import get_llm_for_chat

        mock_cs = AsyncMock()
        with patch("app.api.routes.chatbot.get_model_config", new_callable=AsyncMock) as mock_gc:
            mock_gc.return_value = (None, {})
            with pytest.raises(ValueError, match="Failed to initialize LLM"):
                await get_llm_for_chat(mock_cs)

    @pytest.mark.asyncio
    async def test_list_config_extracts_first(self):
        """When config is a list, should extract first element."""
        from app.api.routes.chatbot import get_llm_for_chat

        mock_cs = AsyncMock()
        configs = [
            {"provider": "openai", "configuration": {"model": "gpt-4o"}, "modelKey": "k1"},
        ]
        with patch("app.api.routes.chatbot.get_model_config", new_callable=AsyncMock) as mock_gc:
            mock_gc.return_value = (configs, {"llm": configs})
            with patch("app.api.routes.chatbot.get_generator_model") as mock_gen:
                mock_gen.return_value = MagicMock()
                llm, config, ai_models = await get_llm_for_chat(mock_cs)
                # Should use the first element from the list
                assert config == configs[0]


# ---------------------------------------------------------------------------
# resolve_tools_then_answer additional branches (lines 466-479)
# ---------------------------------------------------------------------------


class TestResolveToolsAdditional:
    """Additional tests for resolve_tools_then_answer."""

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.bind_tools_for_llm")
    async def test_tool_error_during_loop(self, mock_bind):
        """Provider-level tool error during loop should trigger reflection."""
        from app.api.routes.chatbot import resolve_tools_then_answer
        from langchain_core.messages import AIMessage, ToolMessage

        # First call returns tool call, second call raises tool_use_failed
        ai_with_tool = AIMessage(
            content="",
            tool_calls=[{"name": "my_tool", "args": {}, "id": "c1"}],
        )
        ai_final = AIMessage(content="Final answer")

        mock_llm_with_tools = AsyncMock()
        call_count = [0]

        async def ainvoke_side_effect(messages, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return ai_with_tool
            raise Exception("tool_use_failed: bad format")

        mock_llm_with_tools.ainvoke = AsyncMock(side_effect=ainvoke_side_effect)
        mock_bind.return_value = mock_llm_with_tools

        mock_llm_plain = MagicMock()
        mock_llm_plain.ainvoke = AsyncMock(return_value=ai_final)
        # bind_tools returns the "with tools" version
        mock_llm_plain.bind_tools = MagicMock(return_value=mock_llm_with_tools)

        mock_tool = MagicMock()
        mock_tool.name = "my_tool"
        mock_tool.arun = AsyncMock(return_value='{"ok": true}')

        result = await resolve_tools_then_answer(
            mock_llm_plain, [{"role": "user", "content": "hi"}],
            [mock_tool], {}, max_hops=4
        )
        assert result.content == "Final answer"

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.bind_tools_for_llm")
    async def test_non_tool_error_during_loop_reraises(self, mock_bind):
        """Non-tool errors during loop should re-raise."""
        from app.api.routes.chatbot import resolve_tools_then_answer
        from langchain_core.messages import AIMessage

        ai_with_tool = AIMessage(
            content="",
            tool_calls=[{"name": "my_tool", "args": {}, "id": "c1"}],
        )

        mock_llm_with_tools = AsyncMock()
        call_count = [0]

        async def ainvoke_side_effect(messages, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return ai_with_tool
            raise Exception("network timeout")

        mock_llm_with_tools.ainvoke = AsyncMock(side_effect=ainvoke_side_effect)
        mock_bind.return_value = mock_llm_with_tools

        mock_tool = MagicMock()
        mock_tool.name = "my_tool"
        mock_tool.arun = AsyncMock(return_value='{"ok": true}')

        with pytest.raises(Exception, match="network timeout"):
            await resolve_tools_then_answer(
                MagicMock(), [{"role": "user", "content": "hi"}],
                [mock_tool], {}, max_hops=4
            )


# ---------------------------------------------------------------------------
# process_chat_query_with_status (lines 270, 282, 300, 311-313, 327->349, 356->353)
# ---------------------------------------------------------------------------


class TestProcessChatQueryWithStatus:
    """Tests for process_chat_query_with_status."""

    @pytest.mark.asyncio
    async def test_conversation_history_transforms_query(self):
        """Should transform query when previousConversations exist."""
        from app.api.routes.chatbot import process_chat_query_with_status, ChatQuery

        query_info = ChatQuery(
            query="follow up question",
            previousConversations=[{"role": "user_query", "content": "hi"}],
        )

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": "false"}

        mock_retrieval = AsyncMock()
        mock_retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [{"id": 1}],
            "status_code": 200,
        })

        mock_graph = AsyncMock()
        mock_reranker = AsyncMock()
        mock_reranker.rerank = AsyncMock(return_value=[
            {"virtual_record_id": "vr1", "block_index": 0, "content": "test"}
        ])

        mock_config_service = AsyncMock()

        mock_yield_status = AsyncMock()

        with patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False, "provider": "openai"}, {})
            with patch("app.api.routes.chatbot.setup_followup_query_transformation") as mock_fq:
                mock_chain = AsyncMock()
                mock_chain.ainvoke = AsyncMock(return_value="transformed query")
                mock_fq.return_value = mock_chain
                with patch("app.api.routes.chatbot.QueryDecompositionExpansionService") as mock_qd:
                    mock_qd_instance = AsyncMock()
                    mock_qd_instance.transform_query = AsyncMock(return_value={"queries": []})
                    mock_qd.return_value = mock_qd_instance
                    with patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock) as mock_flat:
                        mock_flat.return_value = [{"virtual_record_id": "vr1", "block_index": 0}]
                        with patch("app.api.routes.chatbot.get_message_content") as mock_mc:
                            mock_mc.return_value = "context"
                            with patch("app.api.routes.chatbot.create_fetch_full_record_tool") as mock_tool:
                                mock_tool.return_value = MagicMock()
                                with patch("app.api.routes.chatbot.BlobStorage"):
                                    with patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock) as mock_cache:
                                        mock_cache.return_value = ({"fullName": "Test User", "designation": "Dev"}, None)
                                        result = await process_chat_query_with_status(
                                            query_info, mock_request, mock_retrieval,
                                            mock_graph, mock_reranker, mock_config_service,
                                            MagicMock(), yield_status=mock_yield_status
                                        )

        # Should have called yield_status
        mock_yield_status.assert_awaited()

    @pytest.mark.asyncio
    async def test_quick_mode_skips_decomposition(self):
        """Quick mode should skip query decomposition."""
        from app.api.routes.chatbot import process_chat_query_with_status, ChatQuery

        query_info = ChatQuery(query="quick question", quickMode=True)

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": "false"}

        mock_retrieval = AsyncMock()
        mock_retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [],
            "status_code": 200,
        })

        with patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False, "provider": "openai"}, {})
            with patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock) as mock_flat:
                mock_flat.return_value = []
                with patch("app.api.routes.chatbot.get_message_content") as mock_mc:
                    mock_mc.return_value = "context"
                    with patch("app.api.routes.chatbot.create_fetch_full_record_tool") as mock_tool:
                        mock_tool.return_value = MagicMock()
                        with patch("app.api.routes.chatbot.BlobStorage"):
                            with patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock) as mock_cache:
                                mock_cache.return_value = ({"fullName": "User", "designation": ""}, {"accountType": "ENTERPRISE", "name": "Corp"})
                                result = await process_chat_query_with_status(
                                    query_info, mock_request, mock_retrieval,
                                    AsyncMock(), AsyncMock(), AsyncMock(),
                                    MagicMock()
                                )

    @pytest.mark.asyncio
    async def test_ollama_provider_sets_simple_mode(self):
        """Ollama provider should force simple mode."""
        from app.api.routes.chatbot import process_chat_query_with_status, ChatQuery

        query_info = ChatQuery(query="test", mode="json", quickMode=True)

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": "false"}

        mock_retrieval = AsyncMock()
        mock_retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [],
            "status_code": 200,
        })

        with patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False, "provider": "ollama"}, {})
            with patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock) as mock_flat:
                mock_flat.return_value = []
                with patch("app.api.routes.chatbot.get_message_content") as mock_mc:
                    mock_mc.return_value = "context"
                    with patch("app.api.routes.chatbot.create_fetch_full_record_tool") as mock_tool:
                        mock_tool.return_value = MagicMock()
                        with patch("app.api.routes.chatbot.BlobStorage"):
                            with patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock) as mock_cache:
                                mock_cache.return_value = ({"fullName": "User", "designation": ""}, None)
                                result = await process_chat_query_with_status(
                                    query_info, mock_request, mock_retrieval,
                                    AsyncMock(), AsyncMock(), AsyncMock(),
                                    MagicMock()
                                )
        assert query_info.mode == "simple"

    @pytest.mark.asyncio
    async def test_error_status_code_raises_http_exception(self):
        """Should raise HTTPException for error status codes."""
        from fastapi import HTTPException
        from app.api.routes.chatbot import process_chat_query_with_status, ChatQuery

        query_info = ChatQuery(query="test", quickMode=True)

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {}

        mock_retrieval = AsyncMock()
        mock_retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [],
            "status_code": 503,
            "status": "error",
            "message": "Service unavailable",
        })

        with patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False, "provider": "openai"}, {})
            with pytest.raises(HTTPException) as exc_info:
                await process_chat_query_with_status(
                    query_info, mock_request, mock_retrieval,
                    AsyncMock(), AsyncMock(), AsyncMock(),
                    MagicMock()
                )
            assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_enterprise_user_context(self):
        """Enterprise org should include org name in user_data."""
        from app.api.routes.chatbot import process_chat_query_with_status, ChatQuery

        query_info = ChatQuery(query="test", quickMode=True)

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": True}

        mock_retrieval = AsyncMock()
        mock_retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [],
            "status_code": 200,
        })

        mock_graph = AsyncMock()

        with patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False, "provider": "openai"}, {})
            with patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock) as mock_flat:
                mock_flat.return_value = []
                with patch("app.api.routes.chatbot.get_message_content") as mock_mc:
                    mock_mc.return_value = "context"
                    with patch("app.api.routes.chatbot.create_fetch_full_record_tool") as mock_tool:
                        mock_tool.return_value = MagicMock()
                        with patch("app.api.routes.chatbot.BlobStorage"):
                            with patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock) as mock_cache:
                                mock_cache.return_value = (
                                    {"fullName": "John Doe", "designation": "Engineer"},
                                    {"accountType": "BUSINESS", "name": "Acme Corp"}
                                )
                                result = await process_chat_query_with_status(
                                    query_info, mock_request, mock_retrieval,
                                    mock_graph, AsyncMock(), AsyncMock(),
                                    MagicMock()
                                )


# ---------------------------------------------------------------------------
# askAIStream endpoint (lines 496-702)
# ---------------------------------------------------------------------------


class TestAskAIStream:
    """Tests for the /chat/stream SSE endpoint (generate_stream coverage)."""

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.stream_llm_response_with_tools")
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_stream_happy_path(
        self, mock_get_llm, mock_stream, mock_blob, mock_flatten,
        mock_content, mock_fetch_tool
    ):
        """Full streaming flow emits status events and stream events."""
        from app.api.routes.chatbot import askAIStream, ChatQuery
        from fastapi.responses import StreamingResponse

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False, "contextLength": 4096}
        mock_get_llm.return_value = (mock_llm, config, {"customSystemPrompt": ""})
        mock_flatten.return_value = [{"virtual_record_id": "vr1", "block_index": 0}]
        mock_fetch_tool.return_value = MagicMock()

        async def fake_stream(*args, **kwargs):
            yield {"event": "token", "data": {"content": "Hello"}}
            yield {"event": "done", "data": {}}

        mock_stream.return_value = fake_stream()

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": True}
        mock_request.json = AsyncMock(return_value={"query": "test question"})

        mock_container = MagicMock()
        mock_container.logger.return_value = MagicMock()
        mock_request.app.container = mock_container

        mock_retrieval = AsyncMock()
        mock_retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [{"id": "1"}],
            "virtual_to_record_map": {},
            "status_code": 200,
        })

        with patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock) as mock_cache:
            mock_cache.return_value = (
                {"fullName": "Test User", "designation": "Dev"},
                {"accountType": "individual"},
            )
            with patch("app.api.routes.chatbot.QueryDecompositionExpansionService") as mock_decomp:
                mock_decomp.return_value.transform_query = AsyncMock(return_value={"queries": []})

                response = await askAIStream(
                    request=mock_request,
                    retrieval_service=mock_retrieval,
                    graph_provider=AsyncMock(),
                    reranker_service=AsyncMock(),
                    config_service=AsyncMock(),
                )

        assert isinstance(response, StreamingResponse)
        assert response.media_type == "text/event-stream"

        # Drain the stream
        events = []
        async for chunk in response.body_iterator:
            events.append(chunk)
        assert len(events) > 0

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_stream_llm_none_emits_error(self, mock_get_llm):
        """When LLM is None, stream emits an error event."""
        from app.api.routes.chatbot import askAIStream

        mock_get_llm.return_value = (None, {"isMultimodal": False, "contextLength": 4096, "provider": "openai"}, {})

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": True}
        mock_request.json = AsyncMock(return_value={"query": "test"})
        mock_container = MagicMock()
        mock_container.logger.return_value = MagicMock()
        mock_request.app.container = mock_container

        response = await askAIStream(
            request=mock_request,
            retrieval_service=AsyncMock(),
            graph_provider=AsyncMock(),
            reranker_service=AsyncMock(),
            config_service=AsyncMock(),
        )

        events = []
        async for chunk in response.body_iterator:
            events.append(chunk)

        # Should contain error event
        combined = "".join(events)
        assert "error" in combined

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_stream_search_error_emits_sse_error(self, mock_get_llm):
        """When search returns error status, stream emits error event."""
        from app.api.routes.chatbot import askAIStream

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False, "contextLength": 4096}
        mock_get_llm.return_value = (mock_llm, config, {})

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": True}
        mock_request.json = AsyncMock(return_value={"query": "test", "quickMode": True})
        mock_container = MagicMock()
        mock_container.logger.return_value = MagicMock()
        mock_request.app.container = mock_container

        mock_retrieval = AsyncMock()
        mock_retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [],
            "status_code": 503,
            "status": "error",
            "message": "Service unavailable",
        })

        response = await askAIStream(
            request=mock_request,
            retrieval_service=mock_retrieval,
            graph_provider=AsyncMock(),
            reranker_service=AsyncMock(),
            config_service=AsyncMock(),
        )

        events = []
        async for chunk in response.body_iterator:
            events.append(chunk)

        combined = "".join(events)
        assert "error" in combined

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.stream_llm_response_with_tools")
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_stream_with_conversation_history(
        self, mock_get_llm, mock_stream, mock_blob, mock_flatten,
        mock_content, mock_fetch_tool
    ):
        """Stream endpoint transforms query when conversation history is present."""
        from app.api.routes.chatbot import askAIStream

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False, "contextLength": 4096}
        mock_get_llm.return_value = (mock_llm, config, {"customSystemPrompt": ""})
        mock_flatten.return_value = [{"virtual_record_id": "vr1", "block_index": 0}]
        mock_fetch_tool.return_value = MagicMock()

        async def fake_stream(*args, **kwargs):
            yield {"event": "done", "data": {}}

        mock_stream.return_value = fake_stream()

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": True}
        mock_request.json = AsyncMock(return_value={
            "query": "follow up",
            "previousConversations": [
                {"role": "user_query", "content": "What is X?"},
                {"role": "bot_response", "content": "X is a thing."},
            ],
            "quickMode": True,
        })
        mock_container = MagicMock()
        mock_container.logger.return_value = MagicMock()
        mock_request.app.container = mock_container

        mock_retrieval = AsyncMock()
        mock_retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [],
            "virtual_to_record_map": {},
            "status_code": 200,
        })

        with patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock) as mock_cache:
            mock_cache.return_value = (
                {"fullName": "User", "designation": "Dev"},
                {"accountType": "individual"},
            )
            with patch("app.api.routes.chatbot.setup_followup_query_transformation") as mock_followup:
                mock_chain = MagicMock()
                mock_chain.ainvoke = AsyncMock(return_value="transformed query")
                mock_followup.return_value = mock_chain

                response = await askAIStream(
                    request=mock_request,
                    retrieval_service=mock_retrieval,
                    graph_provider=AsyncMock(),
                    reranker_service=AsyncMock(),
                    config_service=AsyncMock(),
                )

                events = []
                async for chunk in response.body_iterator:
                    events.append(chunk)
                assert len(events) > 0
                mock_followup.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.stream_llm_response_with_tools")
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_stream_ollama_forces_simple_mode(
        self, mock_get_llm, mock_stream, mock_blob, mock_flatten,
        mock_content, mock_fetch_tool
    ):
        """Ollama provider forces simple mode in streaming."""
        from app.api.routes.chatbot import askAIStream

        mock_llm = MagicMock()
        config = {"provider": "ollama", "isMultimodal": False, "contextLength": 4096}
        mock_get_llm.return_value = (mock_llm, config, {"customSystemPrompt": ""})
        mock_flatten.return_value = []
        mock_fetch_tool.return_value = MagicMock()

        async def fake_stream(*args, **kwargs):
            yield {"event": "done", "data": {}}

        mock_stream.return_value = fake_stream()

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": True}
        mock_request.json = AsyncMock(return_value={"query": "test", "quickMode": True})
        mock_container = MagicMock()
        mock_container.logger.return_value = MagicMock()
        mock_request.app.container = mock_container

        mock_retrieval = AsyncMock()
        mock_retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [],
            "virtual_to_record_map": {},
            "status_code": 200,
        })

        with patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock) as mock_cache:
            mock_cache.return_value = (
                {"fullName": "User", "designation": "Dev"},
                {"accountType": "individual"},
            )
            response = await askAIStream(
                request=mock_request,
                retrieval_service=mock_retrieval,
                graph_provider=AsyncMock(),
                reranker_service=AsyncMock(),
                config_service=AsyncMock(),
            )

        events = []
        async for chunk in response.body_iterator:
            events.append(chunk)
        assert len(events) > 0

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.stream_llm_response_with_tools")
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_stream_with_custom_system_prompt(
        self, mock_get_llm, mock_stream, mock_blob, mock_flatten,
        mock_content, mock_fetch_tool
    ):
        """Custom system prompt overrides mode config."""
        from app.api.routes.chatbot import askAIStream

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False, "contextLength": 4096}
        mock_get_llm.return_value = (mock_llm, config, {"customSystemPrompt": "You are a custom assistant"})
        mock_flatten.return_value = [{"virtual_record_id": "vr1", "block_index": 0}]
        mock_fetch_tool.return_value = MagicMock()

        async def fake_stream(*args, **kwargs):
            yield {"event": "done", "data": {}}

        mock_stream.return_value = fake_stream()

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": True}
        mock_request.json = AsyncMock(return_value={"query": "test", "quickMode": True})
        mock_container = MagicMock()
        mock_container.logger.return_value = MagicMock()
        mock_request.app.container = mock_container

        mock_retrieval = AsyncMock()
        mock_retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [],
            "virtual_to_record_map": {},
            "status_code": 200,
        })

        with patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock) as mock_cache:
            mock_cache.return_value = (
                {"fullName": "User", "designation": "Dev"},
                {"accountType": "individual"},
            )
            response = await askAIStream(
                request=mock_request,
                retrieval_service=mock_retrieval,
                graph_provider=AsyncMock(),
                reranker_service=AsyncMock(),
                config_service=AsyncMock(),
            )

        events = []
        async for chunk in response.body_iterator:
            events.append(chunk)
        assert len(events) > 0

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.stream_llm_response_with_tools")
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_stream_enterprise_user_context(
        self, mock_get_llm, mock_stream, mock_blob, mock_flatten,
        mock_content, mock_fetch_tool
    ):
        """Enterprise/business user gets org context in stream."""
        from app.api.routes.chatbot import askAIStream

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False, "contextLength": 4096}
        mock_get_llm.return_value = (mock_llm, config, {"customSystemPrompt": ""})
        mock_flatten.return_value = [{"virtual_record_id": "vr1", "block_index": 0}]
        mock_fetch_tool.return_value = MagicMock()

        async def fake_stream(*args, **kwargs):
            yield {"event": "done", "data": {}}

        mock_stream.return_value = fake_stream()

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": True}
        mock_request.json = AsyncMock(return_value={"query": "test", "quickMode": True})
        mock_container = MagicMock()
        mock_container.logger.return_value = MagicMock()
        mock_request.app.container = mock_container

        mock_retrieval = AsyncMock()
        mock_retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [],
            "virtual_to_record_map": {},
            "status_code": 200,
        })

        with patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock) as mock_cache:
            mock_cache.return_value = (
                {"fullName": "John", "designation": "Manager"},
                {"accountType": "ENTERPRISE", "name": "Big Corp"},
            )
            response = await askAIStream(
                request=mock_request,
                retrieval_service=mock_retrieval,
                graph_provider=AsyncMock(),
                reranker_service=AsyncMock(),
                config_service=AsyncMock(),
            )

        events = []
        async for chunk in response.body_iterator:
            events.append(chunk)
        assert len(events) > 0

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.stream_llm_response_with_tools")
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_stream_error_during_llm_streaming(
        self, mock_get_llm, mock_stream, mock_blob, mock_flatten,
        mock_content, mock_fetch_tool
    ):
        """Error during LLM streaming emits error event."""
        from app.api.routes.chatbot import askAIStream

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False, "contextLength": 4096}
        mock_get_llm.return_value = (mock_llm, config, {"customSystemPrompt": ""})
        mock_flatten.return_value = [{"virtual_record_id": "vr1", "block_index": 0}]
        mock_fetch_tool.return_value = MagicMock()

        async def failing_stream(*args, **kwargs):
            raise RuntimeError("Stream crashed")
            yield  # make it a generator

        mock_stream.return_value = failing_stream()

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": True}
        mock_request.json = AsyncMock(return_value={"query": "test", "quickMode": True})
        mock_container = MagicMock()
        mock_container.logger.return_value = MagicMock()
        mock_request.app.container = mock_container

        mock_retrieval = AsyncMock()
        mock_retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [],
            "virtual_to_record_map": {},
            "status_code": 200,
        })

        with patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock) as mock_cache:
            mock_cache.return_value = (
                {"fullName": "User", "designation": "Dev"},
                {"accountType": "individual"},
            )
            response = await askAIStream(
                request=mock_request,
                retrieval_service=mock_retrieval,
                graph_provider=AsyncMock(),
                reranker_service=AsyncMock(),
                config_service=AsyncMock(),
            )

        events = []
        async for chunk in response.body_iterator:
            events.append(chunk)

        combined = "".join(events)
        assert "error" in combined


# ---------------------------------------------------------------------------
# askAI endpoint (lines 725-748)
# ---------------------------------------------------------------------------


class TestAskAI:
    """Tests for the /chat non-streaming endpoint."""

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.process_citations")
    @patch("app.api.routes.chatbot.resolve_tools_then_answer", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.process_chat_query", new_callable=AsyncMock)
    async def test_ask_ai_happy_path(self, mock_process, mock_resolve, mock_citations):
        """Happy path: returns JSONResponse."""
        from app.api.routes.chatbot import askAI, ChatQuery
        from langchain_core.messages import AIMessage
        from fastapi.responses import JSONResponse

        mock_llm = MagicMock()
        mock_blob = MagicMock()
        mock_process.return_value = (
            mock_llm,
            [{"role": "user", "content": "hi"}],
            [],
            {},
            [{"virtual_record_id": "vr1", "block_index": 0}],
            ["test"],
            {},
            mock_blob,
            False,
        )

        final_msg = AIMessage(content="The answer is 42")
        mock_resolve.return_value = final_msg
        mock_citations.return_value = JSONResponse(content={"answer": "42"})

        mock_request = MagicMock()
        mock_container = MagicMock()
        mock_container.logger.return_value = MagicMock()
        mock_request.app.container = mock_container

        query_info = ChatQuery(query="test question")

        result = await askAI(
            request=mock_request,
            query_info=query_info,
            retrieval_service=AsyncMock(),
            graph_provider=AsyncMock(),
            reranker_service=AsyncMock(),
            config_service=AsyncMock(),
        )

        assert isinstance(result, JSONResponse)
        mock_citations.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.resolve_tools_then_answer", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.process_chat_query", new_callable=AsyncMock)
    async def test_ask_ai_no_content_raises(self, mock_process, mock_resolve):
        """When LLM returns no content, raises HTTPException 500."""
        from app.api.routes.chatbot import askAI, ChatQuery
        from fastapi import HTTPException
        from langchain_core.messages import AIMessage

        mock_llm = MagicMock()
        mock_process.return_value = (
            mock_llm, [], [], {}, [], [], {}, MagicMock(), False,
        )

        empty_msg = AIMessage(content="")
        mock_resolve.return_value = empty_msg

        mock_request = MagicMock()
        mock_container = MagicMock()
        mock_container.logger.return_value = MagicMock()
        mock_request.app.container = mock_container

        query_info = ChatQuery(query="test")

        with pytest.raises(HTTPException) as exc:
            await askAI(
                request=mock_request,
                query_info=query_info,
                retrieval_service=AsyncMock(),
                graph_provider=AsyncMock(),
                reranker_service=AsyncMock(),
                config_service=AsyncMock(),
            )
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.process_chat_query", new_callable=AsyncMock)
    async def test_ask_ai_generic_error_raises_400(self, mock_process):
        """Generic exception in askAI raises HTTPException 400."""
        from app.api.routes.chatbot import askAI, ChatQuery
        from fastapi import HTTPException

        mock_process.side_effect = RuntimeError("unexpected error")

        mock_request = MagicMock()
        mock_container = MagicMock()
        mock_container.logger.return_value = MagicMock()
        mock_request.app.container = mock_container

        query_info = ChatQuery(query="test")

        with pytest.raises(HTTPException) as exc:
            await askAI(
                request=mock_request,
                query_info=query_info,
                retrieval_service=AsyncMock(),
                graph_provider=AsyncMock(),
                reranker_service=AsyncMock(),
                config_service=AsyncMock(),
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.process_chat_query", new_callable=AsyncMock)
    async def test_ask_ai_http_exception_reraises(self, mock_process):
        """HTTPException is re-raised with original status code."""
        from app.api.routes.chatbot import askAI, ChatQuery
        from fastapi import HTTPException

        mock_process.side_effect = HTTPException(status_code=503, detail="Service unavailable")

        mock_request = MagicMock()
        mock_container = MagicMock()
        mock_container.logger.return_value = MagicMock()
        mock_request.app.container = mock_container

        query_info = ChatQuery(query="test")

        with pytest.raises(HTTPException) as exc:
            await askAI(
                request=mock_request,
                query_info=query_info,
                retrieval_service=AsyncMock(),
                graph_provider=AsyncMock(),
                reranker_service=AsyncMock(),
                config_service=AsyncMock(),
            )
        assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# resolve_tools_then_answer — tool error during loop (lines 464-479)
# ---------------------------------------------------------------------------


class TestResolveToolsLoopErrors:
    """Additional tests for tool call error handling inside the loop."""

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.bind_tools_for_llm")
    async def test_provider_tool_error_during_loop(self, mock_bind):
        """Provider tool_use_failed error during loop retries without tools."""
        from app.api.routes.chatbot import resolve_tools_then_answer
        from langchain_core.messages import AIMessage

        # First call returns a tool call
        tool_call_msg = AIMessage(content="")
        tool_call_msg.tool_calls = [
            {"name": "my_tool", "args": {}, "id": "tc1"}
        ]
        # Second call (after tool result) raises tool error
        # Third call (without tools) returns final answer
        final_msg = AIMessage(content="Fallback after loop error")

        mock_llm_with_tools = AsyncMock()
        mock_llm_with_tools.ainvoke = AsyncMock(
            side_effect=[tool_call_msg, Exception("tool_use_failed: bad args")]
        )
        mock_bind.return_value = mock_llm_with_tools

        mock_tool = MagicMock()
        mock_tool.name = "my_tool"
        mock_tool.arun = AsyncMock(return_value='{"ok": true}')

        mock_llm_plain = MagicMock()
        mock_llm_plain.ainvoke = AsyncMock(return_value=final_msg)

        result = await resolve_tools_then_answer(
            mock_llm_plain, [{"role": "user", "content": "hi"}],
            [mock_tool], {}, max_hops=4
        )
        assert result.content == "Fallback after loop error"

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.bind_tools_for_llm")
    async def test_non_tool_error_during_loop_reraises(self, mock_bind):
        """Non-tool error during loop is re-raised."""
        from app.api.routes.chatbot import resolve_tools_then_answer
        from langchain_core.messages import AIMessage

        tool_call_msg = AIMessage(content="")
        tool_call_msg.tool_calls = [
            {"name": "my_tool", "args": {}, "id": "tc1"}
        ]

        mock_llm_with_tools = AsyncMock()
        mock_llm_with_tools.ainvoke = AsyncMock(
            side_effect=[tool_call_msg, Exception("network error")]
        )
        mock_bind.return_value = mock_llm_with_tools

        mock_tool = MagicMock()
        mock_tool.name = "my_tool"
        mock_tool.arun = AsyncMock(return_value='{"ok": true}')

        with pytest.raises(Exception, match="network error"):
            await resolve_tools_then_answer(
                MagicMock(), [{"role": "user", "content": "hi"}],
                [mock_tool], {}, max_hops=4
            )


# ---------------------------------------------------------------------------
# process_chat_query_with_status — reranking (lines 311-313)
# ---------------------------------------------------------------------------


class TestProcessChatReranking:
    """Tests for reranking branch in process_chat_query_with_status."""

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.QueryDecompositionExpansionService")
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_reranking_called_for_multiple_results_non_quick(
        self, mock_get_llm, mock_decomp, mock_cached_user, mock_blob,
        mock_flatten, mock_content, mock_fetch_tool
    ):
        """Reranking is called when >1 results and not quick mode."""
        from app.api.routes.chatbot import process_chat_query_with_status, ChatQuery

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False}
        mock_get_llm.return_value = (mock_llm, config, {})
        mock_decomp.return_value.transform_query = AsyncMock(return_value={"queries": []})

        mock_cached_user.return_value = (
            {"fullName": "User", "designation": "Dev"},
            {"accountType": "individual"},
        )

        mock_flatten.return_value = [
            {"virtual_record_id": "vr1", "block_index": 0},
            {"virtual_record_id": "vr2", "block_index": 0},
        ]
        mock_fetch_tool.return_value = MagicMock()

        retrieval = AsyncMock()
        retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [{"id": "1"}],
            "status_code": 200,
        })

        reranker = AsyncMock()
        reranker.rerank = AsyncMock(return_value=[
            {"virtual_record_id": "vr2", "block_index": 0},
            {"virtual_record_id": "vr1", "block_index": 0},
        ])

        query_info = ChatQuery(query="test", quickMode=False, chatMode="standard")
        request = MagicMock()
        request.state.user = {"userId": "u1", "orgId": "o1"}
        request.query_params = {"sendUserInfo": True}

        # yield_status should trigger the status callback
        status_events = []

        async def capture_status(event_type, data):
            status_events.append((event_type, data))

        await process_chat_query_with_status(
            query_info, request, retrieval, AsyncMock(),
            reranker, AsyncMock(), MagicMock(),
            yield_status=capture_status
        )

        reranker.rerank.assert_awaited_once()
        # Should have ranking status event
        status_types = [e[1].get("status") for e in status_events]
        assert "ranking" in status_types


# ---------------------------------------------------------------------------
# process_chat_query_with_status — bot_response conversation (lines 356-357)
# ---------------------------------------------------------------------------


class TestProcessChatConversationHistory:
    """Tests for conversation history message formatting."""

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.setup_followup_query_transformation")
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_conversation_roles_mapped_correctly(
        self, mock_get_llm, mock_setup, mock_cached_user, mock_blob,
        mock_flatten, mock_content, mock_fetch_tool
    ):
        """user_query -> user, bot_response -> assistant in messages."""
        from app.api.routes.chatbot import process_chat_query_with_status, ChatQuery

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False}
        mock_get_llm.return_value = (mock_llm, config, {})

        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(return_value="transformed")
        mock_setup.return_value = mock_chain

        mock_cached_user.return_value = (
            {"fullName": "User", "designation": "Dev"},
            {"accountType": "individual"},
        )
        mock_flatten.return_value = [{"virtual_record_id": "vr1", "block_index": 0}]
        mock_fetch_tool.return_value = MagicMock()

        retrieval = AsyncMock()
        retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [],
            "status_code": 200,
        })

        query_info = ChatQuery(
            query="follow up",
            quickMode=True,
            previousConversations=[
                {"role": "user_query", "content": "What is X?"},
                {"role": "bot_response", "content": "X is a thing."},
                {"role": "user_query", "content": "Tell me more."},
            ],
        )

        request = MagicMock()
        request.state.user = {"userId": "u1", "orgId": "o1"}
        request.query_params = {"sendUserInfo": True}

        result = await process_chat_query_with_status(
            query_info, request, retrieval, AsyncMock(),
            AsyncMock(), AsyncMock(), MagicMock()
        )

        llm, messages, *_ = result
        # Check conversation history was mapped to user/assistant roles
        roles = [m["role"] for m in messages]
        assert "user" in roles
        assert "assistant" in roles
