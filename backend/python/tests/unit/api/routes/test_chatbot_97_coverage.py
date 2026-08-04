"""Additional tests for app.api.routes.chatbot targeting >97% coverage.

NOTE: Lines 711-713 (outer exception in generate_stream) are unreachable because
every code path between lines 682-709 is wrapped in inner try/except blocks that
catch all exceptions. The only way to trigger lines 711-713 is if request.state.user.get
raises, which is a dict access that shouldn't fail.

Targets uncovered lines/branches:
- 155->161: model_key=None, model_name=None branch in get_model_config
- 167->170: model_key not found after fresh fetch, empty configs
- 197->202: model_key+model_name where key matches but model not in list
- 244: decomposed_queries producing actual queries
- 269->271: decomposition queries used in search
- 311->313: reranking in process_chat_query_with_status
- 327->349: sendUserInfo=False or missing
- 335: org_info is None user_data
- 356->353: previousConversations with bot_response
- 498-499: invalid JSON body in askAIStream
- 503-504: invalid request params in askAIStream
- 592-593: stream reranking branch
- 607->631: stream sendUserInfo=False
- 615: enterprise user in stream
- 644->641: stream bot_response conversation
- 672: HTTPException with string detail in stream
- 711-713: outer exception in generate_stream
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


class TestAskAIStreamInvalidJSON:

    @pytest.mark.asyncio
    async def test_invalid_json_raises_400(self):
        """Invalid JSON body returns HTTPException 400."""
        from app.api.routes.chatbot import askAIStream

        mock_request = MagicMock()
        mock_request.json = AsyncMock(side_effect=Exception("invalid json"))

        with pytest.raises(HTTPException) as exc:
            await askAIStream(
                request=mock_request,
                retrieval_service=AsyncMock(),
                graph_provider=AsyncMock(),
                config_service=AsyncMock(),
            )
        assert exc.value.status_code == 400
        assert "Invalid JSON" in str(exc.value.detail)


# ===================================================================
# askAIStream — invalid request params (line 503-504)
# ===================================================================


class TestAskAIStreamInvalidParams:

    @pytest.mark.asyncio
    async def test_invalid_params_raises_400(self):
        """Invalid ChatQuery params returns HTTPException 400."""
        from app.api.routes.chatbot import askAIStream

        mock_request = MagicMock()
        # Return body missing 'query' field
        mock_request.json = AsyncMock(return_value={"limit": "not_an_int_required"})

        with pytest.raises(HTTPException) as exc:
            await askAIStream(
                request=mock_request,
                retrieval_service=AsyncMock(),
                graph_provider=AsyncMock(),
                config_service=AsyncMock(),
            )
        assert exc.value.status_code == 400
        assert "Invalid request parameters" in str(exc.value.detail)


# ===================================================================
# askAIStream — HTTPException with string detail (line 672)
# ===================================================================


class TestAskAIStreamHTTPExceptionStringDetail:

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_http_exception_string_detail(self, mock_get_llm):
        """HTTPException with string detail during LLM init emits an AG-UI RUN_ERROR event."""
        from app.api.routes.chatbot import askAIStream

        mock_get_llm.side_effect = HTTPException(status_code=404, detail="Not found")

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": True}
        mock_request.json = AsyncMock(
            return_value={"query": "test", "quickMode": True, "protocol": "agui"}
        )
        mock_container = MagicMock()
        mock_container.logger.return_value = MagicMock()
        mock_request.app.container = mock_container

        response = await askAIStream(
            request=mock_request,
            retrieval_service=AsyncMock(),
            graph_provider=AsyncMock(),
            config_service=AsyncMock(),
        )

        events = []
        async for chunk in response.body_iterator:
            events.append(chunk)

        combined = "".join(events)
        assert "RUN_ERROR" in combined
        assert "Not found" in combined
        assert "llm_initialization_failed" in combined


# ===================================================================
# askAIStream — outer exception (line 711-713)
# ===================================================================



    @pytest.mark.asyncio
    async def test_model_key_match_but_name_not_in_models(self):
        """When modelKey matches but modelName is not in config, falls through."""
        from app.api.routes.chatbot import get_model_config

        configs = [
            {
                "modelKey": "key-1",
                "configuration": {"model": "gpt-4o"},
                "provider": "openai",
                "isDefault": False,
            }
        ]

        mock_cs = AsyncMock()
        mock_cs.get_config = AsyncMock(return_value={"llm": configs})

        # model_key matches but model_name doesn't
        config, _ = await get_model_config(mock_cs, model_key="key-1", model_name="nonexistent")
        assert config["modelKey"] == "key-1"  # Still returns by key match


# ===================================================================
# get_model_config — empty configs after fresh fetch (line 167->170)
# ===================================================================


class TestGetModelConfigEmptyAfterFresh:

    @pytest.mark.asyncio
    async def test_empty_configs_after_refresh_raises(self):
        """When configs are empty even after refresh, raises ValueError."""
        from app.api.routes.chatbot import get_model_config

        mock_cs = AsyncMock()
        mock_cs.get_config = AsyncMock(side_effect=[
            {"llm": [{"modelKey": "old", "configuration": {"model": "m"}, "isDefault": False}]},
            {"llm": []},  # Fresh fetch returns empty
        ])

        # Will try fresh config when key not found, fresh returns empty
        with pytest.raises(ValueError, match="No LLM configurations found"):
            await get_model_config(mock_cs, model_key="missing-key")




# ===================================================================
# askAIStream — HTTPException with dict detail containing status (line 666-670)
# ===================================================================


class TestAskAIStreamHTTPExceptionDictDetail:

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_http_exception_dict_detail(self, mock_get_llm):
        """HTTPException with dict detail during LLM init emits an AG-UI RUN_ERROR event with the detail serialized in the message."""
        from app.api.routes.chatbot import askAIStream

        mock_get_llm.side_effect = HTTPException(
            status_code=202,
            detail={"status": "indexing", "message": "Still processing"},
        )

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": True}
        mock_request.json = AsyncMock(
            return_value={"query": "test", "quickMode": True, "protocol": "agui"}
        )
        mock_container = MagicMock()
        mock_container.logger.return_value = MagicMock()
        mock_request.app.container = mock_container

        response = await askAIStream(
            request=mock_request,
            retrieval_service=AsyncMock(),
            graph_provider=AsyncMock(),
            config_service=AsyncMock(),
        )

        events = []
        async for chunk in response.body_iterator:
            events.append(chunk)

        combined = "".join(events)
        assert "RUN_ERROR" in combined
        assert "indexing" in combined
        assert "llm_initialization_failed" in combined


# ===================================================================
# askAIStream — context length fallback (line 524-525)
# ===================================================================


class TestAskAIStreamGenericError:

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_generic_error_in_query_processing(self, mock_get_llm):
        """Non-HTTPException during query processing emits error."""
        from app.api.routes.chatbot import askAIStream

        mock_get_llm.side_effect = RuntimeError("unexpected crash")

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": True}
        mock_request.json = AsyncMock(return_value={"query": "test", "protocol": "agui"})
        mock_container = MagicMock()
        mock_container.logger.return_value = MagicMock()
        mock_request.app.container = mock_container

        response = await askAIStream(
            request=mock_request,
            retrieval_service=AsyncMock(),
            graph_provider=AsyncMock(),
            config_service=AsyncMock(),
        )

        events = []
        async for chunk in response.body_iterator:
            events.append(chunk)

        combined = "".join(events)
        assert "RUN_ERROR" in combined
        assert "unexpected crash" in combined
        assert "llm_initialization_failed" in combined


# ===================================================================
# askAIStream — HTTPException with string detail (line 672)
# ===================================================================


class TestAskAIStreamHTTPExceptionNonDictDetail:

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_http_exception_non_dict_detail_string(self, mock_get_llm):
        """HTTPException with string detail emits non-dict error."""
        from app.api.routes.chatbot import askAIStream

        mock_get_llm.side_effect = HTTPException(status_code=503, detail="Service unavailable string")

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": True}
        mock_request.json = AsyncMock(return_value={"query": "test", "protocol": "agui"})
        mock_container = MagicMock()
        mock_container.logger.return_value = MagicMock()
        mock_request.app.container = mock_container

        response = await askAIStream(
            request=mock_request,
            retrieval_service=AsyncMock(),
            graph_provider=AsyncMock(),
            config_service=AsyncMock(),
        )

        events = []
        async for chunk in response.body_iterator:
            events.append(chunk)

        combined = "".join(events)
        assert "RUN_ERROR" in combined
        assert "Service unavailable string" in combined
        assert "llm_initialization_failed" in combined

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_http_exception_none_detail(self, mock_get_llm):
        """HTTPException with None detail uses status code in message."""
        from app.api.routes.chatbot import askAIStream

        mock_get_llm.side_effect = HTTPException(status_code=500, detail=None)

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": True}
        mock_request.json = AsyncMock(return_value={"query": "test", "protocol": "agui"})
        mock_container = MagicMock()
        mock_container.logger.return_value = MagicMock()
        mock_request.app.container = mock_container

        response = await askAIStream(
            request=mock_request,
            retrieval_service=AsyncMock(),
            graph_provider=AsyncMock(),
            config_service=AsyncMock(),
        )

        events = []
        async for chunk in response.body_iterator:
            events.append(chunk)

        combined = "".join(events)
        assert "RUN_ERROR" in combined
        assert "500: Internal Server Error" in combined
        assert "llm_initialization_failed" in combined


# ===================================================================
# askAIStream — outer exception (lines 711-713)
# ===================================================================


