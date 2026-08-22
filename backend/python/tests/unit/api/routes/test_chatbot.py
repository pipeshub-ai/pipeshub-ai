"""Tests for app.api.routes.chatbot helper functions and models."""
import asyncio
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
        assert q.chatMode == "internal_search"
        assert q.mode == "json"
        assert q.conversationId is None
        assert q.attachments == []

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
            conversationId="conv-456",
        )
        assert q.limit == 10
        assert q.quickMode is True
        assert q.chatMode == "analysis"
        assert q.mode == "simple"
        assert q.modelKey == "mk-123"
        assert q.modelName == "gpt-4o-mini"
        assert q.retrievalMode == "VECTOR"
        assert len(q.previousConversations) == 1
        assert q.conversationId == "conv-456"

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

    def test_attachments_on_query(self):
        from app.api.routes.chatbot import ChatQuery
        att = [{"virtualRecordId": "vr-1", "mimeType": "application/pdf"}]
        q = ChatQuery(query="q", attachments=att)
        assert q.attachments == att

    @pytest.mark.parametrize("effort", ["none", "low", "medium", "high", "max"])
    def test_reasoning_effort_accepts_valid_values(self, effort):
        from app.api.routes.chatbot import ChatQuery
        q = ChatQuery(query="q", reasoningEffort=effort)
        assert q.reasoningEffort == effort

    def test_reasoning_effort_defaults_to_none(self):
        from app.api.routes.chatbot import ChatQuery
        q = ChatQuery(query="q")
        assert q.reasoningEffort is None

    def test_reasoning_effort_rejects_invalid_value(self):
        from app.api.routes.chatbot import ChatQuery
        with pytest.raises(ValidationError, match="Invalid reasoningEffort"):
            ChatQuery(query="q", reasoningEffort="extreme")






# ---------------------------------------------------------------------------
# get_model_config_for_mode
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
    @patch("app.api.routes.chatbot.get_generator_model_async")
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
        mock_gen.assert_called_once_with("openai", config, "gpt-4o", None)

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_generator_model_async")
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
        mock_gen.assert_called_once_with("openai", config, "gpt-4o", None)

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_generator_model_async")
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
        mock_gen.assert_called_once_with("openai", config, "gpt-4o-mini", None)

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_generator_model_async")
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
        mock_gen.assert_called_once_with("openai", config, "gpt-4o", None)

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_generator_model_async")
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
        mock_gen.assert_called_once_with("openai", configs[0], "gpt-4o", None)

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_generator_model_async")
    @patch("app.api.routes.chatbot.get_model_config")
    async def test_reasoning_effort_forwarded_to_generator(self, mock_get_model_config, mock_gen):
        """reasoning_effort passed to get_llm_for_chat must reach get_generator_model_async
        as the 4th positional arg, regardless of model_key/model_name resolution path."""
        from app.api.routes.chatbot import get_llm_for_chat
        config = {
            "modelKey": "key-1",
            "configuration": {"model": "gpt-4o, gpt-4o-mini"},
            "provider": "openai",
        }
        mock_get_model_config.return_value = (config, {"llm": [config]})
        mock_gen.return_value = MagicMock()

        await get_llm_for_chat(AsyncMock(), reasoning_effort="high")
        mock_gen.assert_called_once_with("openai", config, "gpt-4o", "high")

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
    @patch("app.api.routes.chatbot.get_generator_model_async")
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



# ---------------------------------------------------------------------------
# DEFAULT_CONTEXT_LENGTH constant
# ---------------------------------------------------------------------------

class TestConstants:
    def test_default_context_length(self):
        from app.api.routes.chatbot import DEFAULT_CONTEXT_LENGTH
        assert DEFAULT_CONTEXT_LENGTH == 128000


# ---------------------------------------------------------------------------
# _build_llm_user_context_string
# ---------------------------------------------------------------------------


class TestAskAIStreamEndpoint:
    """Tests for the askAIStream endpoint."""

    @pytest.mark.asyncio
    async def test_invalid_json_body_raises_400(self):
        from fastapi import HTTPException

        from app.api.routes.chatbot import askAIStream

        request = MagicMock()
        request.json = AsyncMock(side_effect=Exception("bad json"))

        with pytest.raises(HTTPException) as exc_info:
            await askAIStream(request, AsyncMock(), AsyncMock(), AsyncMock())
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_params_raises_400(self):
        from fastapi import HTTPException

        from app.api.routes.chatbot import askAIStream

        request = MagicMock()
        # Missing required 'query' field
        request.json = AsyncMock(return_value={"limit": 10})

        with pytest.raises(HTTPException) as exc_info:
            await askAIStream(request, AsyncMock(), AsyncMock(), AsyncMock())
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_returns_streaming_response(self):
        from fastapi.responses import StreamingResponse

        from app.api.routes.chatbot import askAIStream

        request = MagicMock()
        request.json = AsyncMock(return_value={"query": "hello"})

        result = await askAIStream(request, AsyncMock(), AsyncMock(), AsyncMock())
        assert isinstance(result, StreamingResponse)
        assert result.media_type == "text/event-stream"


# ---------------------------------------------------------------------------
# Additional get_model_config coverage
# ---------------------------------------------------------------------------


class TestGetModelConfigAdditional:
    """Additional tests for get_model_config branches."""

    @pytest.mark.asyncio
    async def test_default_config(self):
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
        from app.api.routes.chatbot import get_model_config

        mock_cs = AsyncMock()
        mock_cs.get_config = AsyncMock(side_effect=[
            {"llm": [{"provider": "openai", "isDefault": False, "configuration": {"model": "gpt-4"}, "modelKey": "old-key"}]},
            {"llm": [{"provider": "openai", "isDefault": False, "configuration": {"model": "gpt-4"}, "modelKey": "new-key"}]},
        ])
        config, _ = await get_model_config(mock_cs, model_key="new-key", model_name=None)
        assert config["modelKey"] == "new-key"

    @pytest.mark.asyncio
    async def test_no_configs_raises(self):
        from app.api.routes.chatbot import get_model_config

        mock_cs = AsyncMock()
        mock_cs.get_config = AsyncMock(return_value={"llm": []})

        with pytest.raises(ValueError, match="No LLM configurations found"):
            await get_model_config(mock_cs, model_key=None, model_name=None)

    @pytest.mark.asyncio
    async def test_fallback_to_list(self):
        from app.api.routes.chatbot import get_model_config

        mock_cs = AsyncMock()
        configs = [
            {"provider": "openai", "isDefault": False, "configuration": {"model": "gpt-4"}, "modelKey": "k1"},
        ]
        mock_cs.get_config = AsyncMock(return_value={"llm": configs})

        result, _ = await get_model_config(mock_cs, model_key=None, model_name="nonexistent")
        assert result == configs


# ---------------------------------------------------------------------------
# Additional get_llm_for_chat coverage
# ---------------------------------------------------------------------------


class TestGetLlmForChatAdditional:
    """Additional tests for get_llm_for_chat."""

    @pytest.mark.asyncio
    async def test_with_model_key_and_name(self):
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

        with patch("app.api.routes.chatbot.get_generator_model_async") as mock_gen:
            mock_gen.return_value = MagicMock()
            llm, cfg, ai = await get_llm_for_chat(
                mock_cs, model_key="key-1", model_name="gpt-4o-mini"
            )
            mock_gen.assert_called_once_with("openai", cfg, "gpt-4o-mini", None)
        assert llm is mock_gen.return_value
        assert cfg["modelKey"] == "key-1"
        assert ai == mock_cs.get_config.return_value





# ---------------------------------------------------------------------------
# agentCapabilities field on ChatQuery
# ---------------------------------------------------------------------------


class TestChatQueryAgentCapabilities:
    """Verify the agentCapabilities field is accepted and defaults to None."""

    def test_defaults_to_none(self) -> None:
        from app.api.routes.chatbot import ChatQuery

        q = ChatQuery(query="hello")
        assert q.agentCapabilities is None

    def test_accepts_capabilities_dict(self) -> None:
        from app.api.routes.chatbot import ChatQuery

        q = ChatQuery(
            query="hello",
            agentCapabilities={"internalSearch": False, "webSearch": True},
        )
        assert q.agentCapabilities == {"internalSearch": False, "webSearch": True}

    def test_accepts_none_explicitly(self) -> None:
        from app.api.routes.chatbot import ChatQuery

        q = ChatQuery(query="hello", agentCapabilities=None)
        assert q.agentCapabilities is None

    def test_deep_search_key_preserved(self) -> None:
        from app.api.routes.chatbot import ChatQuery

        q = ChatQuery(
            query="hello",
            agentCapabilities={"internalSearch": True, "webSearch": True, "deepSearch": True},
        )
        assert q.agentCapabilities["deepSearch"] is True


# ---------------------------------------------------------------------------
# askAIStream endpoint (lines 496-702)
