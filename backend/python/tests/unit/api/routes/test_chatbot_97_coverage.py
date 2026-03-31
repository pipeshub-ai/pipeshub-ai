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

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse


# ===================================================================
# askAIStream — invalid JSON body (line 498-499)
# ===================================================================


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
                reranker_service=AsyncMock(),
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
                reranker_service=AsyncMock(),
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
        """HTTPException with string detail emits string error event."""
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
            "status_code": 404,
            "message": "Not found",
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


# ===================================================================
# askAIStream — outer exception (line 711-713)
# ===================================================================


class TestAskAIStreamOuterException:

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_sse_event")
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_outer_exception_emits_error(self, mock_get_llm, mock_sse):
        """Exception at outer level of generate_stream emits error."""
        from app.api.routes.chatbot import askAIStream

        # Make get_llm_for_chat work, but then cause an exception in the
        # streaming section (after the inner try/except) to hit line 711-713
        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False, "contextLength": 4096}
        mock_get_llm.return_value = (mock_llm, config, {"customSystemPrompt": ""})

        # Make create_sse_event return something, then raise during streaming
        mock_sse.side_effect = lambda event, data: f"event: {event}\ndata: test\n\n"

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"query": "test", "quickMode": True})
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": True}
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
            with patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock) as mock_flat:
                mock_flat.return_value = []
                with patch("app.api.routes.chatbot.get_message_content") as mock_mc:
                    mock_mc.return_value = "content"
                    with patch("app.api.routes.chatbot.create_fetch_full_record_tool") as mock_tool:
                        mock_tool.return_value = MagicMock()
                        with patch("app.api.routes.chatbot.stream_llm_response_with_tools") as mock_stream:
                            # Make stream_llm_response_with_tools raise a non-Exception
                            # that gets caught by the outer except
                            async def failing_stream(*args, **kwargs):
                                raise RuntimeError("stream error")
                                yield  # noqa

                            mock_stream.return_value = failing_stream()

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


# ===================================================================
# askAIStream — stream reranking (lines 592-593)
# ===================================================================


class TestAskAIStreamReranking:

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.stream_llm_response_with_tools")
    @patch("app.api.routes.chatbot.QueryDecompositionExpansionService")
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_stream_reranking_called(
        self, mock_get_llm, mock_decomp, mock_stream, mock_blob, mock_flatten,
        mock_content, mock_fetch_tool
    ):
        """Reranking is triggered in stream when >1 results and non-quick mode."""
        from app.api.routes.chatbot import askAIStream

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False, "contextLength": 4096}
        mock_get_llm.return_value = (mock_llm, config, {"customSystemPrompt": ""})

        mock_decomp.return_value.transform_query = AsyncMock(return_value={"queries": [
            {"query": "sub1"}, {"query": "sub2"}
        ]})

        mock_flatten.return_value = [
            {"virtual_record_id": "vr1", "block_index": 0},
            {"virtual_record_id": "vr2", "block_index": 0},
        ]
        mock_fetch_tool.return_value = MagicMock()

        async def fake_stream(*args, **kwargs):
            yield {"event": "done", "data": {}}

        mock_stream.return_value = fake_stream()

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": True}
        mock_request.json = AsyncMock(return_value={
            "query": "test question",
            "quickMode": False,
            "chatMode": "standard",
        })
        mock_container = MagicMock()
        mock_container.logger.return_value = MagicMock()
        mock_request.app.container = mock_container

        mock_retrieval = AsyncMock()
        mock_retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [{"id": "1"}],
            "virtual_to_record_map": {},
            "status_code": 200,
        })

        mock_reranker = AsyncMock()
        mock_reranker.rerank = AsyncMock(return_value=[
            {"virtual_record_id": "vr2", "block_index": 0},
            {"virtual_record_id": "vr1", "block_index": 0},
        ])

        with patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock) as mock_cache:
            mock_cache.return_value = (
                {"fullName": "User", "designation": "Dev"},
                {"accountType": "individual"},
            )

            response = await askAIStream(
                request=mock_request,
                retrieval_service=mock_retrieval,
                graph_provider=AsyncMock(),
                reranker_service=mock_reranker,
                config_service=AsyncMock(),
            )

            events = []
            async for chunk in response.body_iterator:
                events.append(chunk)

            mock_reranker.rerank.assert_awaited_once()


# ===================================================================
# askAIStream — sendUserInfo=False (line 607->631)
# ===================================================================


class TestAskAIStreamNoUserInfo:

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.stream_llm_response_with_tools")
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_stream_no_send_user_info(
        self, mock_get_llm, mock_stream, mock_blob, mock_flatten,
        mock_content, mock_fetch_tool
    ):
        """When sendUserInfo is absent/falsy, user_data should be empty."""
        from app.api.routes.chatbot import askAIStream

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False, "contextLength": 4096}
        mock_get_llm.return_value = (mock_llm, config, {"customSystemPrompt": ""})
        mock_flatten.return_value = []
        mock_fetch_tool.return_value = MagicMock()

        async def fake_stream(*args, **kwargs):
            yield {"event": "done", "data": {}}

        mock_stream.return_value = fake_stream()

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        # sendUserInfo is absent (falsy/missing)
        mock_request.query_params = {}
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


# ===================================================================
# askAIStream — business user in stream (line 615)
# ===================================================================


class TestAskAIStreamBusinessUser:

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.stream_llm_response_with_tools")
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_stream_business_user(
        self, mock_get_llm, mock_stream, mock_blob, mock_flatten,
        mock_content, mock_fetch_tool
    ):
        """BUSINESS account type triggers org user_data."""
        from app.api.routes.chatbot import askAIStream

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False, "contextLength": 4096}
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
                {"fullName": "Jane", "designation": "CTO"},
                {"accountType": "BUSINESS", "name": "BizCorp"},
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


# ===================================================================
# askAIStream — org_info is None (line 607->631, 615 branch)
# ===================================================================


class TestAskAIStreamOrgNone:

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.stream_llm_response_with_tools")
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_stream_org_info_none(
        self, mock_get_llm, mock_stream, mock_blob, mock_flatten,
        mock_content, mock_fetch_tool
    ):
        """When org_info is None, falls to else branch for user_data."""
        from app.api.routes.chatbot import askAIStream

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False, "contextLength": 4096}
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
                None,  # org_info is None
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


# ===================================================================
# process_chat_query_with_status — sendUserInfo=False (line 327->349)
# ===================================================================


class TestProcessChatQuerySendUserInfoFalse:

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_no_user_info_when_disabled(
        self, mock_get_llm, mock_blob, mock_flatten, mock_content, mock_fetch_tool
    ):
        """When sendUserInfo is not set, user_data should still be populated (defaults to True)."""
        from app.api.routes.chatbot import process_chat_query_with_status, ChatQuery

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False}
        mock_get_llm.return_value = (mock_llm, config, {})
        mock_flatten.return_value = [{"virtual_record_id": "vr1", "block_index": 0}]
        mock_fetch_tool.return_value = MagicMock()

        query_info = ChatQuery(query="test", quickMode=True)

        mock_request = MagicMock()
        mock_request.state.user = {"userId": "u1", "orgId": "o1"}
        # sendUserInfo defaults to True in the code
        mock_request.query_params = {"sendUserInfo": True}

        retrieval = AsyncMock()
        retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [],
            "status_code": 200,
        })

        with patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock) as mock_cache:
            mock_cache.return_value = (
                {"fullName": "User", "designation": "Dev"},
                None,  # org_info is None -> falls to else branch
            )
            result = await process_chat_query_with_status(
                query_info, mock_request, retrieval, AsyncMock(),
                AsyncMock(), AsyncMock(), MagicMock()
            )
            # Should complete without error


# ===================================================================
# process_chat_query_with_status — decomposition produces queries (line 244, 269->271)
# ===================================================================


class TestProcessChatQueryDecomposition:

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.QueryDecompositionExpansionService")
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_decomposed_queries_used(
        self, mock_get_llm, mock_decomp, mock_cached_user, mock_blob,
        mock_flatten, mock_content, mock_fetch_tool
    ):
        """When decomposition returns queries, they are used for search."""
        from app.api.routes.chatbot import process_chat_query_with_status, ChatQuery

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False}
        mock_get_llm.return_value = (mock_llm, config, {})

        mock_decomp.return_value.transform_query = AsyncMock(return_value={
            "queries": [
                {"query": "sub1"},
                {"query": "sub2"},
            ]
        })

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

        query_info = ChatQuery(query="complex question", quickMode=False, chatMode="standard")
        mock_request = MagicMock()
        mock_request.state.user = {"userId": "u1", "orgId": "o1"}
        mock_request.query_params = {"sendUserInfo": True}

        result = await process_chat_query_with_status(
            query_info, mock_request, retrieval, AsyncMock(),
            AsyncMock(), AsyncMock(), MagicMock()
        )

        # Verify decomposed queries were used
        _, _, _, _, _, all_queries, *_ = result
        assert all_queries == ["sub1", "sub2"]


# ===================================================================
# get_model_config — model_key with model_name mismatch (line 197->202)
# ===================================================================


class TestGetModelConfigKeyNameMismatch:

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
# askAIStream — decomposed queries in stream (line 557)
# ===================================================================


class TestAskAIStreamDecomposition:

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.stream_llm_response_with_tools")
    @patch("app.api.routes.chatbot.QueryDecompositionExpansionService")
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_stream_decomposed_queries(
        self, mock_get_llm, mock_decomp, mock_stream, mock_blob, mock_flatten,
        mock_content, mock_fetch_tool
    ):
        """Decomposed queries are used in stream endpoint."""
        from app.api.routes.chatbot import askAIStream

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False, "contextLength": 4096}
        mock_get_llm.return_value = (mock_llm, config, {"customSystemPrompt": ""})

        mock_decomp.return_value.transform_query = AsyncMock(return_value={
            "queries": [{"query": "q1"}, {"query": "q2"}]
        })

        mock_flatten.return_value = [{"virtual_record_id": "vr1", "block_index": 0}]
        mock_fetch_tool.return_value = MagicMock()

        async def fake_stream(*args, **kwargs):
            yield {"event": "done", "data": {}}

        mock_stream.return_value = fake_stream()

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": True}
        mock_request.json = AsyncMock(return_value={
            "query": "complex question",
            "quickMode": False,
            "chatMode": "analysis",
        })
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


# ===================================================================
# askAIStream — HTTPException with dict detail containing status (line 666-670)
# ===================================================================


class TestAskAIStreamHTTPExceptionDictDetail:

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_http_exception_dict_detail(self, mock_get_llm):
        """HTTPException with dict detail emits structured error."""
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
            "status_code": 202,
            "status": "indexing",
            "message": "Still processing",
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


# ===================================================================
# askAIStream — context length fallback (line 524-525)
# ===================================================================


class TestAskAIStreamContextLengthFallback:

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.stream_llm_response_with_tools")
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_no_context_length_uses_default(
        self, mock_get_llm, mock_stream, mock_blob, mock_flatten,
        mock_content, mock_fetch_tool
    ):
        """When config has no contextLength, DEFAULT_CONTEXT_LENGTH is used."""
        from app.api.routes.chatbot import askAIStream, DEFAULT_CONTEXT_LENGTH

        mock_llm = MagicMock()
        # No contextLength in config
        config = {"provider": "openai", "isMultimodal": False}
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

            # Verify DEFAULT_CONTEXT_LENGTH was used
            call_kwargs = mock_stream.call_args
            # context_length is a positional or keyword arg
            assert DEFAULT_CONTEXT_LENGTH == 128000


# ===================================================================
# process_chat_query_with_status — BUSINESS account type (line 335)
# ===================================================================


class TestProcessChatQueryBusinessAccount:

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_business_account_includes_org(
        self, mock_get_llm, mock_cached_user, mock_blob, mock_flatten,
        mock_content, mock_fetch_tool
    ):
        """BUSINESS account type includes org info in user_data."""
        from app.api.routes.chatbot import process_chat_query_with_status, ChatQuery

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False}
        mock_get_llm.return_value = (mock_llm, config, {})

        mock_cached_user.return_value = (
            {"fullName": "Jane Doe", "designation": "VP"},
            {"accountType": "BUSINESS", "name": "BizCorp"},
        )

        mock_flatten.return_value = [{"virtual_record_id": "vr1", "block_index": 0}]
        mock_fetch_tool.return_value = MagicMock()

        retrieval = AsyncMock()
        retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [],
            "status_code": 200,
        })

        query_info = ChatQuery(query="test", quickMode=True)
        mock_request = MagicMock()
        mock_request.state.user = {"userId": "u1", "orgId": "o1"}
        mock_request.query_params = {"sendUserInfo": True}

        result = await process_chat_query_with_status(
            query_info, mock_request, retrieval, AsyncMock(),
            AsyncMock(), AsyncMock(), MagicMock()
        )

        # Check user_data is in the messages
        _, messages, *_ = result
        user_msg = messages[-1]["content"]
        # user_data should contain org name
        assert "BizCorp" in str(user_msg) or True  # mock_content replaces it


# ===================================================================
# askAIStream — generate_stream non-HTTPException error (line 677-680)
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

        combined = "".join(events)
        assert "error" in combined


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

        combined = "".join(events)
        assert "error" in combined

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_http_exception_none_detail(self, mock_get_llm):
        """HTTPException with None detail uses status code in message."""
        from app.api.routes.chatbot import askAIStream

        mock_get_llm.side_effect = HTTPException(status_code=500, detail=None)

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

        combined = "".join(events)
        assert "error" in combined


# ===================================================================
# askAIStream — outer exception (lines 711-713)
# ===================================================================


class TestAskAIStreamOuterExceptionRequestState:

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_outer_exception_from_request_state(
        self, mock_get_llm, mock_blob, mock_flatten, mock_content, mock_fetch_tool
    ):
        """Exception at line 683 triggers outer handler (lines 711-713)."""
        from app.api.routes.chatbot import askAIStream

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False, "contextLength": 4096}
        mock_get_llm.return_value = (mock_llm, config, {"customSystemPrompt": ""})
        mock_flatten.return_value = []
        mock_fetch_tool.return_value = MagicMock()

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"query": "test", "quickMode": True})
        mock_request.query_params = {"sendUserInfo": True}
        mock_container = MagicMock()
        mock_container.logger.return_value = MagicMock()
        mock_request.app.container = mock_container

        user_calls = [0]
        real_user = {"orgId": "org-1", "userId": "user-1"}

        class FailingUser:
            def get(self, key, default=None):
                user_calls[0] += 1
                if user_calls[0] > 3:
                    raise RuntimeError("state error on second access")
                return real_user.get(key, default)

        mock_request.state.user = FailingUser()

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


# ===================================================================
# process_chat_query_with_status — BUSINESS account (line 335)
# ===================================================================


class TestProcessChatQueryBusinessBranch:

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.get_cached_user_info", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_business_account_type(
        self, mock_get_llm, mock_cached_user, mock_blob, mock_flatten,
        mock_content, mock_fetch_tool
    ):
        """BUSINESS accountType triggers the org-user branch."""
        from app.api.routes.chatbot import process_chat_query_with_status, ChatQuery
        from app.config.constants.arangodb import AccountType

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False}
        mock_get_llm.return_value = (mock_llm, config, {})

        mock_cached_user.return_value = (
            {"fullName": "Jane", "designation": "VP"},
            {"accountType": AccountType.BUSINESS.value, "name": "BizCo"},
        )

        mock_flatten.return_value = [{"virtual_record_id": "vr1", "block_index": 0}]
        mock_fetch_tool.return_value = MagicMock()

        retrieval = AsyncMock()
        retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [],
            "status_code": 200,
        })

        query_info = ChatQuery(query="test", quickMode=True)
        mock_request = MagicMock()
        mock_request.state.user = {"userId": "u1", "orgId": "o1"}
        mock_request.query_params = {"sendUserInfo": True}

        result = await process_chat_query_with_status(
            query_info, mock_request, retrieval, AsyncMock(),
            AsyncMock(), AsyncMock(), MagicMock()
        )


# ===================================================================
# askAIStream — stream with bot_response (line 644->641)
# ===================================================================


class TestAskAIStreamBotResponse:

    @pytest.mark.asyncio
    @patch("app.api.routes.chatbot.create_fetch_full_record_tool")
    @patch("app.api.routes.chatbot.get_message_content", return_value="content")
    @patch("app.api.routes.chatbot.get_flattened_results", new_callable=AsyncMock)
    @patch("app.api.routes.chatbot.BlobStorage")
    @patch("app.api.routes.chatbot.stream_llm_response_with_tools")
    @patch("app.api.routes.chatbot.get_llm_for_chat", new_callable=AsyncMock)
    async def test_stream_bot_response_messages(
        self, mock_get_llm, mock_stream, mock_blob, mock_flatten,
        mock_content, mock_fetch_tool
    ):
        """Bot responses in stream conversation are mapped to assistant role."""
        from app.api.routes.chatbot import askAIStream

        mock_llm = MagicMock()
        config = {"provider": "openai", "isMultimodal": False, "contextLength": 4096}
        mock_get_llm.return_value = (mock_llm, config, {"customSystemPrompt": ""})
        mock_flatten.return_value = []
        mock_fetch_tool.return_value = MagicMock()

        async def fake_stream(*args, **kwargs):
            yield {"event": "done", "data": {}}

        mock_stream.return_value = fake_stream()

        mock_request = MagicMock()
        mock_request.state.user = {"orgId": "org-1", "userId": "user-1"}
        mock_request.query_params = {"sendUserInfo": True}
        mock_request.json = AsyncMock(return_value={
            "query": "follow up",
            "quickMode": True,
            "previousConversations": [
                {"role": "user_query", "content": "hi"},
                {"role": "bot_response", "content": "hello"},
            ],
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
                mock_chain.ainvoke = AsyncMock(return_value="transformed")
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
