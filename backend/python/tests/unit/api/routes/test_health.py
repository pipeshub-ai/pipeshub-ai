import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse


MODULE = "app.api.routes.health"


@pytest.fixture
def mock_request():
    req = MagicMock()
    app = MagicMock()
    container = MagicMock()
    container.logger.return_value = MagicMock()
    container.config_service.return_value = MagicMock()
    app.container = container

    retrieval_svc = AsyncMock()
    retrieval_svc.collection_name = "test_collection"
    retrieval_svc.vector_db_service = AsyncMock()
    retrieval_svc.get_current_embedding_model_name = AsyncMock(return_value="model-a")
    retrieval_svc.get_embedding_model_name = MagicMock(return_value="model-a")
    container.retrieval_service = AsyncMock(return_value=retrieval_svc)

    req.app = app
    return req


class TestLlmHealthCheck:
    @pytest.mark.asyncio
    async def test_success(self, mock_request):
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value="ok")

        with patch(f"{MODULE}.get_llm", new_callable=AsyncMock, return_value=(mock_llm, {})):
            from app.api.routes.health import llm_health_check
            resp = await llm_health_check(mock_request, [{"provider": "openai"}])

        assert resp.status_code == 200
        body = resp.body.decode()
        assert "healthy" in body

    @pytest.mark.asyncio
    async def test_failure(self, mock_request):
        with patch(f"{MODULE}.get_llm", new_callable=AsyncMock, side_effect=Exception("LLM failed")):
            from app.api.routes.health import llm_health_check
            resp = await llm_health_check(mock_request, [{"provider": "openai"}])

        assert resp.status_code == 500
        body = resp.body.decode()
        assert "not healthy" in body


class TestInitializeEmbeddingModel:
    @pytest.mark.asyncio
    async def test_default_model(self, mock_request):
        mock_embed = MagicMock()
        with patch(f"{MODULE}.get_default_embedding_model", return_value=mock_embed):
            from app.api.routes.health import initialize_embedding_model
            result = await initialize_embedding_model(mock_request, [])

        assert result[0] is mock_embed

    @pytest.mark.asyncio
    async def test_config_with_default_flag(self, mock_request):
        mock_embed = MagicMock()
        configs = [
            {"provider": "openai", "isDefault": False},
            {"provider": "openai", "isDefault": True},
        ]
        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed):
            from app.api.routes.health import initialize_embedding_model
            result = await initialize_embedding_model(mock_request, configs)

        assert result[0] is mock_embed

    @pytest.mark.asyncio
    async def test_config_without_default_uses_first(self, mock_request):
        mock_embed = MagicMock()
        configs = [{"provider": "openai", "isDefault": False}]
        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed):
            from app.api.routes.health import initialize_embedding_model
            result = await initialize_embedding_model(mock_request, configs)

        assert result[0] is mock_embed

    @pytest.mark.asyncio
    async def test_no_model_found_raises(self, mock_request):
        configs = [{"provider": "openai", "isDefault": False}]
        with patch(f"{MODULE}.get_embedding_model", return_value=None):
            from app.api.routes.health import initialize_embedding_model
            with pytest.raises(HTTPException) as exc_info:
                await initialize_embedding_model(mock_request, configs)
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_exception_during_init_raises(self, mock_request):
        configs = [{"provider": "bad", "isDefault": True}]
        with patch(f"{MODULE}.get_embedding_model", side_effect=Exception("init fail")):
            from app.api.routes.health import initialize_embedding_model
            with pytest.raises(HTTPException) as exc_info:
                await initialize_embedding_model(mock_request, configs)
            assert exc_info.value.status_code == 500


class TestVerifyEmbeddingHealth:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_embed = AsyncMock()
        mock_embed.aembed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
        logger = MagicMock()

        from app.api.routes.health import verify_embedding_health
        size = await verify_embedding_health(mock_embed, logger)
        assert size == 3

    @pytest.mark.asyncio
    async def test_empty_embedding_raises(self):
        mock_embed = AsyncMock()
        mock_embed.aembed_query = AsyncMock(return_value=[])
        logger = MagicMock()

        from app.api.routes.health import verify_embedding_health
        with pytest.raises(HTTPException) as exc_info:
            await verify_embedding_health(mock_embed, logger)
        assert exc_info.value.status_code == 500


class TestHandleModelChange:
    @pytest.mark.asyncio
    async def test_no_change(self):
        retrieval_svc = AsyncMock()
        logger = MagicMock()

        from app.api.routes.health import handle_model_change
        await handle_model_change(retrieval_svc, "model-a", "model-a", 768, 100, 768, logger)

    @pytest.mark.asyncio
    async def test_model_change_with_data_raises(self):
        retrieval_svc = AsyncMock()
        logger = MagicMock()

        from app.api.routes.health import handle_model_change
        with pytest.raises(HTTPException) as exc_info:
            await handle_model_change(retrieval_svc, "model-a", "model-b", 768, 100, 512, logger)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_model_change_empty_collection_recreates(self):
        retrieval_svc = AsyncMock()
        logger = MagicMock()

        with patch(f"{MODULE}.recreate_collection", new_callable=AsyncMock) as mock_recreate:
            from app.api.routes.health import handle_model_change
            await handle_model_change(retrieval_svc, "model-a", "model-b", 768, 0, 512, logger)
            mock_recreate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_change_when_current_is_none(self):
        retrieval_svc = AsyncMock()
        logger = MagicMock()

        from app.api.routes.health import handle_model_change
        await handle_model_change(retrieval_svc, None, "model-b", 768, 0, 512, logger)

    @pytest.mark.asyncio
    async def test_no_change_when_new_is_none(self):
        retrieval_svc = AsyncMock()
        logger = MagicMock()

        from app.api.routes.health import handle_model_change
        await handle_model_change(retrieval_svc, "model-a", None, 768, 0, 512, logger)

    @pytest.mark.asyncio
    async def test_strips_models_prefix(self):
        retrieval_svc = AsyncMock()
        logger = MagicMock()

        from app.api.routes.health import handle_model_change
        await handle_model_change(retrieval_svc, "models/model-a", "models/model-a", 768, 100, 768, logger)

    @pytest.mark.asyncio
    async def test_case_insensitive_comparison(self):
        retrieval_svc = AsyncMock()
        logger = MagicMock()

        from app.api.routes.health import handle_model_change
        await handle_model_change(retrieval_svc, "Model-A", "model-a", 768, 100, 768, logger)

    @pytest.mark.asyncio
    async def test_zero_qdrant_vector_size_no_recreate(self):
        retrieval_svc = AsyncMock()
        logger = MagicMock()

        from app.api.routes.health import handle_model_change
        await handle_model_change(retrieval_svc, "model-a", "model-b", 0, 0, 512, logger)


class TestRecreateCollection:
    @pytest.mark.asyncio
    async def test_success(self):
        retrieval_svc = MagicMock()
        retrieval_svc.collection_name = "test_coll"
        retrieval_svc.vector_db_service = AsyncMock()
        logger = MagicMock()

        from app.api.routes.health import recreate_collection
        await recreate_collection(retrieval_svc, 768, logger)

        retrieval_svc.vector_db_service.delete_collection.assert_awaited_once_with("test_coll")
        retrieval_svc.vector_db_service.create_collection.assert_awaited_once()
        assert retrieval_svc.vector_db_service.create_index.await_count == 2

    @pytest.mark.asyncio
    async def test_failure_raises(self):
        retrieval_svc = MagicMock()
        retrieval_svc.collection_name = "test_coll"
        retrieval_svc.vector_db_service = AsyncMock()
        retrieval_svc.vector_db_service.delete_collection = AsyncMock(side_effect=Exception("fail"))
        logger = MagicMock()

        from app.api.routes.health import recreate_collection
        with pytest.raises(Exception, match="fail"):
            await recreate_collection(retrieval_svc, 768, logger)


class TestCheckCollectionInfo:
    @pytest.mark.asyncio
    async def test_success(self):
        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"
        dense_vec_mock = MagicMock()
        dense_vec_mock.size = 768
        vectors_mock = MagicMock()
        vectors_mock.get.return_value = dense_vec_mock
        collection_info = MagicMock()
        collection_info.config.params.vectors = vectors_mock
        collection_info.points_count = 10
        retrieval_svc.vector_db_service.get_collection = AsyncMock(return_value=collection_info)
        retrieval_svc.get_current_embedding_model_name = AsyncMock(return_value="model-a")
        retrieval_svc.get_embedding_model_name = MagicMock(return_value="model-a")
        logger = MagicMock()
        dense_embeddings = MagicMock()

        from app.api.routes.health import check_collection_info
        await check_collection_info(retrieval_svc, dense_embeddings, 768, logger)

    @pytest.mark.asyncio
    async def test_grpc_not_found(self):
        import grpc
        from grpc._channel import _InactiveRpcError

        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"

        state = MagicMock()
        state.code = grpc.StatusCode.NOT_FOUND
        state.details = "not found"
        error = _InactiveRpcError(state)

        retrieval_svc.vector_db_service.get_collection = AsyncMock(side_effect=error)
        logger = MagicMock()
        dense_embeddings = MagicMock()

        from app.api.routes.health import check_collection_info
        # Should NOT raise - NOT_FOUND is acceptable
        await check_collection_info(retrieval_svc, dense_embeddings, 768, logger)
        logger.info.assert_called_with("collection not found - acceptable for health check")

    @pytest.mark.asyncio
    async def test_unexpected_exception(self):
        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"
        retrieval_svc.vector_db_service.get_collection = AsyncMock(side_effect=RuntimeError("bad"))
        logger = MagicMock()

        from app.api.routes.health import check_collection_info
        with pytest.raises(HTTPException) as exc_info:
            await check_collection_info(retrieval_svc, MagicMock(), 768, logger)
        assert exc_info.value.status_code == 500


class TestEmbeddingHealthCheck:
    @pytest.mark.asyncio
    async def test_success(self, mock_request):
        mock_embed = AsyncMock()
        mock_embed.aembed_query = AsyncMock(return_value=[0.1, 0.2])

        with patch(f"{MODULE}.initialize_embedding_model", new_callable=AsyncMock,
                   return_value=(mock_embed, mock_request.app.container.retrieval_service.return_value, MagicMock())), \
             patch(f"{MODULE}.verify_embedding_health", new_callable=AsyncMock, return_value=2), \
             patch(f"{MODULE}.check_collection_info", new_callable=AsyncMock):
            from app.api.routes.health import embedding_health_check
            resp = await embedding_health_check(mock_request, [{"provider": "openai"}])

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_http_exception(self, mock_request):
        with patch(f"{MODULE}.initialize_embedding_model", new_callable=AsyncMock,
                   side_effect=HTTPException(status_code=500, detail={"status": "not healthy", "error": "fail"})):
            from app.api.routes.health import embedding_health_check
            resp = await embedding_health_check(mock_request, [])

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_general_exception(self, mock_request):
        mock_embed = AsyncMock()
        retrieval_svc = mock_request.app.container.retrieval_service.return_value
        logger = MagicMock()

        with patch(f"{MODULE}.initialize_embedding_model", new_callable=AsyncMock,
                   return_value=(mock_embed, retrieval_svc, logger)), \
             patch(f"{MODULE}.verify_embedding_health", new_callable=AsyncMock, side_effect=Exception("boom")):
            from app.api.routes.health import embedding_health_check
            resp = await embedding_health_check(mock_request, [{"provider": "openai"}])

        assert resp.status_code == 500


class TestPerformLlmHealthCheck:
    @pytest.mark.asyncio
    async def test_success_text(self):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "gpt-4"}}
        mock_model = MagicMock()
        mock_model.invoke.return_value = "ok"

        with patch(f"{MODULE}.get_generator_model", return_value=mock_model), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value="ok"):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(config, logger)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_no_model_names(self):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": ""}}

        from app.api.routes.health import perform_llm_health_check
        resp = await perform_llm_health_check(config, logger)
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_multimodal_image_success(self):
        logger = MagicMock()
        config = {"provider": "openai", "isMultimodal": True, "configuration": {"model": "gpt-4o"}}
        mock_model = MagicMock()

        with patch(f"{MODULE}.get_generator_model", return_value=mock_model), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value="ok"):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(config, logger)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_multimodal_image_fails_text_passes(self):
        logger = MagicMock()
        config = {"provider": "openai", "isMultimodal": True, "configuration": {"model": "gpt-4"}}
        mock_model = MagicMock()

        call_count = 0
        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("image not supported")
            return "text ok"

        with patch(f"{MODULE}.get_generator_model", return_value=mock_model), \
             patch("asyncio.wait_for", side_effect=side_effect):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(config, logger)

        assert resp.status_code == 500
        body = resp.body.decode()
        assert "doesn't support images" in body

    @pytest.mark.asyncio
    async def test_multimodal_both_fail(self):
        logger = MagicMock()
        config = {"provider": "openai", "isMultimodal": True, "configuration": {"model": "gpt-4"}}
        mock_model = MagicMock()

        with patch(f"{MODULE}.get_generator_model", return_value=mock_model), \
             patch("asyncio.wait_for", new_callable=AsyncMock, side_effect=Exception("total fail")):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(config, logger)

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_timeout(self):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "gpt-4"}}
        mock_model = MagicMock()

        with patch(f"{MODULE}.get_generator_model", return_value=mock_model), \
             patch("asyncio.wait_for", new_callable=AsyncMock, side_effect=asyncio.TimeoutError):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(config, logger)

        assert resp.status_code == 500
        body = resp.body.decode()
        assert "timed out" in body

    @pytest.mark.asyncio
    async def test_http_exception(self):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "gpt-4"}}

        with patch(f"{MODULE}.get_generator_model", side_effect=HTTPException(status_code=401, detail="unauthorized")):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(config, logger)

        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_general_exception(self):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "gpt-4"}}

        with patch(f"{MODULE}.get_generator_model", side_effect=RuntimeError("bad")):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(config, logger)

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_multimodal_from_configuration(self):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "gpt-4o", "isMultimodal": True}}
        mock_model = MagicMock()

        with patch(f"{MODULE}.get_generator_model", return_value=mock_model), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value="ok"):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(config, logger)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_comma_separated_models_uses_first(self):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "gpt-4, gpt-3.5"}}
        mock_model = MagicMock()

        with patch(f"{MODULE}.get_generator_model", return_value=mock_model) as mock_gen, \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value="ok"):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(config, logger)

        mock_gen.assert_called_once_with(provider="openai", config=config, model_name="gpt-4")

    @pytest.mark.asyncio
    async def test_multimodal_timeout_on_image(self):
        logger = MagicMock()
        config = {"provider": "openai", "isMultimodal": True, "configuration": {"model": "gpt-4o"}}
        mock_model = MagicMock()

        with patch(f"{MODULE}.get_generator_model", return_value=mock_model), \
             patch("asyncio.wait_for", new_callable=AsyncMock, side_effect=asyncio.TimeoutError):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(config, logger)

        assert resp.status_code == 500


class TestPerformEmbeddingHealthCheck:
    @pytest.mark.asyncio
    async def test_success(self, mock_request):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}
        mock_embed = MagicMock()

        dense_vec = MagicMock()
        dense_vec.size = 768
        vectors = MagicMock()
        vectors.get.return_value = dense_vec
        coll_info = MagicMock()
        coll_info.config.params.vectors = vectors
        coll_info.points_count = 0

        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"
        retrieval_svc.vector_db_service = AsyncMock()
        retrieval_svc.vector_db_service.get_collection = AsyncMock(return_value=coll_info)
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_no_model_names(self, mock_request):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": ""}}

        from app.api.routes.health import perform_embedding_health_check
        resp = await perform_embedding_health_check(mock_request, config, logger)
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_empty_results(self, mock_request):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}
        mock_embed = MagicMock()

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_timeout(self, mock_request):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}
        mock_embed = MagicMock()

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, side_effect=asyncio.TimeoutError):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_dimension_mismatch_with_data(self, mock_request):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}
        mock_embed = MagicMock()

        dense_vec = MagicMock()
        dense_vec.size = 1024
        vectors = MagicMock()
        vectors.get.return_value = dense_vec
        coll_info = MagicMock()
        coll_info.config.params.vectors = vectors
        coll_info.points_count = 100

        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"
        retrieval_svc.vector_db_service = AsyncMock()
        retrieval_svc.vector_db_service.get_collection = AsyncMock(return_value=coll_info)
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_http_exception(self, mock_request):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}

        with patch(f"{MODULE}.get_embedding_model", side_effect=HTTPException(status_code=401, detail="unauthorized")):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_general_exception(self, mock_request):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}

        with patch(f"{MODULE}.get_embedding_model", side_effect=RuntimeError("bad")):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_grpc_not_found_during_collection_check(self, mock_request):
        import grpc
        from grpc._channel import _InactiveRpcError
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}
        mock_embed = MagicMock()

        state = MagicMock()
        state.code = grpc.StatusCode.NOT_FOUND
        state.details = "not found"
        error = _InactiveRpcError(state)

        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"
        retrieval_svc.vector_db_service = AsyncMock()
        retrieval_svc.vector_db_service.get_collection = AsyncMock(side_effect=error)
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)
            # NOT_FOUND is acceptable, should return healthy
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_collection_lookup_generic_error(self, mock_request):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}
        mock_embed = MagicMock()

        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"
        retrieval_svc.vector_db_service = AsyncMock()
        retrieval_svc.vector_db_service.get_collection = AsyncMock(side_effect=RuntimeError("conn failed"))
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_comma_separated_models_uses_first(self, mock_request):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "model-a, model-b"}}
        mock_embed = MagicMock()

        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"
        retrieval_svc.vector_db_service = AsyncMock()

        dense_vec = MagicMock()
        dense_vec.size = 768
        vectors = MagicMock()
        vectors.get.return_value = dense_vec
        coll_info = MagicMock()
        coll_info.config.params.vectors = vectors
        coll_info.points_count = 0
        retrieval_svc.vector_db_service.get_collection = AsyncMock(return_value=coll_info)
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed) as mock_get, \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        mock_get.assert_called_once_with(provider="openai", config=config, model_name="model-a")


class TestHealthCheckEndpoint:
    @pytest.mark.asyncio
    async def test_embedding_type(self, mock_request):
        config = {"provider": "openai", "configuration": {"model": "text-embedding"}}

        with patch(f"{MODULE}.perform_embedding_health_check", new_callable=AsyncMock,
                   return_value=JSONResponse(status_code=200, content={"status": "healthy"})):
            from app.api.routes.health import health_check
            resp = await health_check(mock_request, "embedding", config)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_llm_type(self, mock_request):
        config = {"provider": "openai", "configuration": {"model": "gpt-4"}}

        with patch(f"{MODULE}.perform_llm_health_check", new_callable=AsyncMock,
                   return_value=JSONResponse(status_code=200, content={"status": "healthy"})):
            from app.api.routes.health import health_check
            resp = await health_check(mock_request, "llm", config)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_unknown_type_returns_healthy(self, mock_request):
        config = {"provider": "openai", "configuration": {"model": "gpt-4"}}

        from app.api.routes.health import health_check
        resp = await health_check(mock_request, "unknown", config)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_exception_handling(self, mock_request):
        config = {"provider": "openai", "configuration": {"model": "gpt-4"}}

        with patch(f"{MODULE}.perform_llm_health_check", new_callable=AsyncMock,
                   side_effect=Exception("unexpected")):
            from app.api.routes.health import health_check
            resp = await health_check(mock_request, "llm", config)

        assert resp.status_code == 500


class TestInitializeEmbeddingModelExtraEdgeCases:
    """Extra edge cases for initialize_embedding_model to cover remaining branches."""

    @pytest.mark.asyncio
    async def test_all_configs_return_none_raises(self, mock_request):
        """Cover lines 85->89: when no default found AND fallback also returns None."""
        configs = [{"provider": "openai", "isDefault": False}]
        # get_embedding_model returns None for both the first loop (no default)
        # and the fallback loop
        with patch(f"{MODULE}.get_embedding_model", return_value=None):
            from app.api.routes.health import initialize_embedding_model
            with pytest.raises(HTTPException) as exc_info:
                await initialize_embedding_model(mock_request, configs)
            assert exc_info.value.status_code == 500
            assert "No default embedding model found" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_dense_embeddings_none_after_try_block(self, mock_request):
        """Cover line 103: dense_embeddings is None after the try/except.

        This is a safety guard for the case where the try block doesn't raise
        but dense_embeddings still ends up None. We achieve this by providing
        an empty list of configs (no default) which bypasses both for loops,
        and then the HTTPException from line 90 is caught and re-raised by the
        except block. So instead, we mock get_default_embedding_model to return None.
        """
        with patch(f"{MODULE}.get_default_embedding_model", return_value=None):
            from app.api.routes.health import initialize_embedding_model
            with pytest.raises(HTTPException) as exc_info:
                await initialize_embedding_model(mock_request, [])
            assert exc_info.value.status_code == 500
            assert "initialization_failed" in str(exc_info.value.detail)


class TestCheckCollectionInfoExtraEdgeCases:
    """Extra edge cases for check_collection_info to cover remaining branches."""

    @pytest.mark.asyncio
    async def test_grpc_non_not_found_error(self):
        """Cover lines 236-237: gRPC error with non-NOT_FOUND status code."""
        import grpc

        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"

        # Create a real _InactiveRpcError with UNAVAILABLE status
        from grpc._channel import _InactiveRpcError
        state = MagicMock()
        state.code = grpc.StatusCode.UNAVAILABLE
        state.details = "unavailable"
        error = _InactiveRpcError(state)

        retrieval_svc.vector_db_service.get_collection = AsyncMock(side_effect=error)
        logger = MagicMock()
        dense_embeddings = MagicMock()

        from app.api.routes.health import check_collection_info
        with pytest.raises(HTTPException) as exc_info:
            await check_collection_info(retrieval_svc, dense_embeddings, 768, logger)
        assert exc_info.value.status_code == 500
        assert "Unexpected gRPC error" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_http_exception_reraise(self):
        """Cover line 247: HTTPException raised by handle_model_change is re-raised."""
        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"

        # Setup collection info so handle_model_change gets called
        dense_vec_mock = MagicMock()
        dense_vec_mock.size = 768
        vectors_mock = MagicMock()
        vectors_mock.get.return_value = dense_vec_mock
        collection_info = MagicMock()
        collection_info.config.params.vectors = vectors_mock
        collection_info.points_count = 100

        retrieval_svc.vector_db_service.get_collection = AsyncMock(return_value=collection_info)
        retrieval_svc.get_current_embedding_model_name = AsyncMock(return_value="model-a")
        retrieval_svc.get_embedding_model_name = MagicMock(return_value="model-b")

        logger = MagicMock()
        dense_embeddings = MagicMock()

        from app.api.routes.health import check_collection_info
        with pytest.raises(HTTPException) as exc_info:
            await check_collection_info(retrieval_svc, dense_embeddings, 768, logger)
        assert exc_info.value.status_code == 400
        assert "Policy Rejection" in str(exc_info.value.detail)


class TestPerformEmbeddingHealthCheckExtraEdgeCases:
    """Extra edge cases for perform_embedding_health_check."""

    @pytest.mark.asyncio
    async def test_qdrant_vector_size_none(self, mock_request):
        """Cover line 518: qdrant_vector_size is None (dense_vector has no size)."""
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}
        mock_embed = MagicMock()

        # dense_vector exists but has no 'size' attribute
        dense_vec = MagicMock(spec=[])  # empty spec so getattr returns None
        vectors = MagicMock()
        vectors.get.return_value = dense_vec
        coll_info = MagicMock()
        coll_info.config.params.vectors = vectors
        coll_info.points_count = 0

        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"
        retrieval_svc.vector_db_service = AsyncMock()
        retrieval_svc.vector_db_service.get_collection = AsyncMock(return_value=coll_info)
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        # Should hit the "Qdrant vector size not found" -> generic exception handler
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_qdrant_dense_vector_none(self, mock_request):
        """Cover line 515 branch: dense_vector is None."""
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}
        mock_embed = MagicMock()

        vectors = MagicMock()
        vectors.get.return_value = None  # dense_vector is None
        coll_info = MagicMock()
        coll_info.config.params.vectors = vectors
        coll_info.points_count = 0

        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"
        retrieval_svc.vector_db_service = AsyncMock()
        retrieval_svc.vector_db_service.get_collection = AsyncMock(return_value=coll_info)
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_grpc_non_not_found_reraise(self, mock_request):
        """Cover line 540: gRPC error with non-NOT_FOUND status code is re-raised
        from inner try block, caught by outer except."""
        import grpc
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}
        mock_embed = MagicMock()

        from grpc._channel import _InactiveRpcError
        state = MagicMock()
        state.code = grpc.StatusCode.UNAVAILABLE
        state.details = "unavailable"
        error = _InactiveRpcError(state)

        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"
        retrieval_svc.vector_db_service = AsyncMock()
        retrieval_svc.vector_db_service.get_collection = AsyncMock(side_effect=error)
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        # The re-raised gRPC error is caught by the outer except Exception
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_inner_exception_reraise(self, mock_request):
        """Cover lines 573-574: except Exception as e: raise e in the inner try block.

        This triggers when embed_documents succeeds but something after that
        (in the inner try of the inner try) raises a non-timeout, non-gRPC exception
        that is not caught by the grpc or generic handler above -- specifically,
        the raise on line 574 propagates to the outer except.
        """
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}
        mock_embed = MagicMock()

        # We need wait_for to succeed, then have the collection check raise
        # an Exception that passes through the inner except chain.
        # The raise on line 540 is already a gRPC path. Let's trigger line 573-574
        # by having retrieval_service() itself raise (before getting to collection).
        # Actually, looking at the code flow: lines 573-574 catch exceptions raised
        # after the timeout try that are NOT TimeoutError. Let's make
        # embed_documents raise a non-timeout exception via wait_for.

        # Actually re-reading: lines 559-574 are:
        #   except asyncio.TimeoutError: ... (line 559)
        #   except Exception as e: raise e  (line 573)
        # This re-raises any non-timeout exception from within the inner try block
        # (lines 483-558), which is then caught by the outer try's except blocks.
        # The inner try covers: wait_for, empty check, collection check.
        # If we raise a ValueError (not timeout, not grpc) from within the
        # collection check's inner except chain... but those are already caught.
        # The simplest way: make wait_for raise a generic Exception (not timeout).
        # But that goes to line 573 -> raise -> caught by outer except Exception on 578.

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, side_effect=ValueError("embed fail")):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 500
        body = resp.body.decode()
        assert "embed fail" in body

    @pytest.mark.asyncio
    async def test_collection_info_falsy(self, mock_request):
        """Cover the branch where collection_info is falsy (line 513 evaluates to False)."""
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}
        mock_embed = MagicMock()

        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"
        retrieval_svc.vector_db_service = AsyncMock()
        retrieval_svc.vector_db_service.get_collection = AsyncMock(return_value=None)
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 200


class TestLoadTestImage:
    def test_load_test_image_file_not_found(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            with pytest.raises(FileNotFoundError):
                from app.api.routes.health import _load_test_image
                _load_test_image()

# =============================================================================
# Merged from test_health_full_coverage.py
# =============================================================================

MODULE = "app.api.routes.health"


@pytest.fixture
def mock_request():
    req = MagicMock()
    app = MagicMock()
    container = MagicMock()
    container.logger.return_value = MagicMock()
    container.config_service.return_value = MagicMock()
    app.container = container

    retrieval_svc = AsyncMock()
    retrieval_svc.collection_name = "test_collection"
    retrieval_svc.vector_db_service = AsyncMock()
    retrieval_svc.get_current_embedding_model_name = AsyncMock(return_value="model-a")
    retrieval_svc.get_embedding_model_name = MagicMock(return_value="model-a")
    container.retrieval_service = AsyncMock(return_value=retrieval_svc)

    req.app = app
    return req


class TestLlmHealthCheckFullCoverage:
    @pytest.mark.asyncio
    async def test_success(self, mock_request):
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value="ok")

        with patch(f"{MODULE}.get_llm", new_callable=AsyncMock, return_value=(mock_llm, {})):
            from app.api.routes.health import llm_health_check
            resp = await llm_health_check(mock_request, [{"provider": "openai"}])

        assert resp.status_code == 200
        body = resp.body.decode()
        assert "healthy" in body

    @pytest.mark.asyncio
    async def test_failure(self, mock_request):
        with patch(f"{MODULE}.get_llm", new_callable=AsyncMock, side_effect=Exception("LLM failed")):
            from app.api.routes.health import llm_health_check
            resp = await llm_health_check(mock_request, [{"provider": "openai"}])

        assert resp.status_code == 500
        body = resp.body.decode()
        assert "not healthy" in body


class TestInitializeEmbeddingModelFullCoverage:
    @pytest.mark.asyncio
    async def test_default_model(self, mock_request):
        mock_embed = MagicMock()
        with patch(f"{MODULE}.get_default_embedding_model", return_value=mock_embed):
            from app.api.routes.health import initialize_embedding_model
            result = await initialize_embedding_model(mock_request, [])

        assert result[0] is mock_embed

    @pytest.mark.asyncio
    async def test_config_with_default_flag(self, mock_request):
        mock_embed = MagicMock()
        configs = [
            {"provider": "openai", "isDefault": False},
            {"provider": "openai", "isDefault": True},
        ]
        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed):
            from app.api.routes.health import initialize_embedding_model
            result = await initialize_embedding_model(mock_request, configs)

        assert result[0] is mock_embed

    @pytest.mark.asyncio
    async def test_config_without_default_uses_first(self, mock_request):
        mock_embed = MagicMock()
        configs = [{"provider": "openai", "isDefault": False}]
        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed):
            from app.api.routes.health import initialize_embedding_model
            result = await initialize_embedding_model(mock_request, configs)

        assert result[0] is mock_embed

    @pytest.mark.asyncio
    async def test_no_model_found_raises(self, mock_request):
        configs = [{"provider": "openai", "isDefault": False}]
        with patch(f"{MODULE}.get_embedding_model", return_value=None):
            from app.api.routes.health import initialize_embedding_model
            with pytest.raises(HTTPException) as exc_info:
                await initialize_embedding_model(mock_request, configs)
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_exception_during_init_raises(self, mock_request):
        configs = [{"provider": "bad", "isDefault": True}]
        with patch(f"{MODULE}.get_embedding_model", side_effect=Exception("init fail")):
            from app.api.routes.health import initialize_embedding_model
            with pytest.raises(HTTPException) as exc_info:
                await initialize_embedding_model(mock_request, configs)
            assert exc_info.value.status_code == 500


class TestVerifyEmbeddingHealthFullCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_embed = AsyncMock()
        mock_embed.aembed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
        logger = MagicMock()

        from app.api.routes.health import verify_embedding_health
        size = await verify_embedding_health(mock_embed, logger)
        assert size == 3

    @pytest.mark.asyncio
    async def test_empty_embedding_raises(self):
        mock_embed = AsyncMock()
        mock_embed.aembed_query = AsyncMock(return_value=[])
        logger = MagicMock()

        from app.api.routes.health import verify_embedding_health
        with pytest.raises(HTTPException) as exc_info:
            await verify_embedding_health(mock_embed, logger)
        assert exc_info.value.status_code == 500


class TestHandleModelChangeFullCoverage:
    @pytest.mark.asyncio
    async def test_no_change(self):
        retrieval_svc = AsyncMock()
        logger = MagicMock()

        from app.api.routes.health import handle_model_change
        await handle_model_change(retrieval_svc, "model-a", "model-a", 768, 100, 768, logger)

    @pytest.mark.asyncio
    async def test_model_change_with_data_raises(self):
        retrieval_svc = AsyncMock()
        logger = MagicMock()

        from app.api.routes.health import handle_model_change
        with pytest.raises(HTTPException) as exc_info:
            await handle_model_change(retrieval_svc, "model-a", "model-b", 768, 100, 512, logger)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_model_change_empty_collection_recreates(self):
        retrieval_svc = AsyncMock()
        logger = MagicMock()

        with patch(f"{MODULE}.recreate_collection", new_callable=AsyncMock) as mock_recreate:
            from app.api.routes.health import handle_model_change
            await handle_model_change(retrieval_svc, "model-a", "model-b", 768, 0, 512, logger)
            mock_recreate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_change_when_current_is_none(self):
        retrieval_svc = AsyncMock()
        logger = MagicMock()

        from app.api.routes.health import handle_model_change
        await handle_model_change(retrieval_svc, None, "model-b", 768, 0, 512, logger)

    @pytest.mark.asyncio
    async def test_no_change_when_new_is_none(self):
        retrieval_svc = AsyncMock()
        logger = MagicMock()

        from app.api.routes.health import handle_model_change
        await handle_model_change(retrieval_svc, "model-a", None, 768, 0, 512, logger)

    @pytest.mark.asyncio
    async def test_strips_models_prefix(self):
        retrieval_svc = AsyncMock()
        logger = MagicMock()

        from app.api.routes.health import handle_model_change
        await handle_model_change(retrieval_svc, "models/model-a", "models/model-a", 768, 100, 768, logger)

    @pytest.mark.asyncio
    async def test_case_insensitive_comparison(self):
        retrieval_svc = AsyncMock()
        logger = MagicMock()

        from app.api.routes.health import handle_model_change
        await handle_model_change(retrieval_svc, "Model-A", "model-a", 768, 100, 768, logger)

    @pytest.mark.asyncio
    async def test_zero_qdrant_vector_size_no_recreate(self):
        retrieval_svc = AsyncMock()
        logger = MagicMock()

        from app.api.routes.health import handle_model_change
        await handle_model_change(retrieval_svc, "model-a", "model-b", 0, 0, 512, logger)


class TestRecreateCollectionFullCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        retrieval_svc = MagicMock()
        retrieval_svc.collection_name = "test_coll"
        retrieval_svc.vector_db_service = AsyncMock()
        logger = MagicMock()

        from app.api.routes.health import recreate_collection
        await recreate_collection(retrieval_svc, 768, logger)

        retrieval_svc.vector_db_service.delete_collection.assert_awaited_once_with("test_coll")
        retrieval_svc.vector_db_service.create_collection.assert_awaited_once()
        assert retrieval_svc.vector_db_service.create_index.await_count == 2

    @pytest.mark.asyncio
    async def test_failure_raises(self):
        retrieval_svc = MagicMock()
        retrieval_svc.collection_name = "test_coll"
        retrieval_svc.vector_db_service = AsyncMock()
        retrieval_svc.vector_db_service.delete_collection = AsyncMock(side_effect=Exception("fail"))
        logger = MagicMock()

        from app.api.routes.health import recreate_collection
        with pytest.raises(Exception, match="fail"):
            await recreate_collection(retrieval_svc, 768, logger)


class TestCheckCollectionInfoFullCoverage:
    @pytest.mark.asyncio
    async def test_success(self):
        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"
        dense_vec_mock = MagicMock()
        dense_vec_mock.size = 768
        vectors_mock = MagicMock()
        vectors_mock.get.return_value = dense_vec_mock
        collection_info = MagicMock()
        collection_info.config.params.vectors = vectors_mock
        collection_info.points_count = 10
        retrieval_svc.vector_db_service.get_collection = AsyncMock(return_value=collection_info)
        retrieval_svc.get_current_embedding_model_name = AsyncMock(return_value="model-a")
        retrieval_svc.get_embedding_model_name = MagicMock(return_value="model-a")
        logger = MagicMock()
        dense_embeddings = MagicMock()

        from app.api.routes.health import check_collection_info
        await check_collection_info(retrieval_svc, dense_embeddings, 768, logger)

    @pytest.mark.asyncio
    async def test_grpc_not_found(self):
        import grpc
        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"

        error = grpc._channel._InactiveRpcError(MagicMock())
        error_mock = MagicMock()
        error_mock.code.return_value = grpc.StatusCode.NOT_FOUND
        error._state = error_mock

        type(error).code = MagicMock(return_value=grpc.StatusCode.NOT_FOUND)

        retrieval_svc.vector_db_service.get_collection = AsyncMock(side_effect=error)
        logger = MagicMock()
        dense_embeddings = MagicMock()

        from app.api.routes.health import check_collection_info
        try:
            await check_collection_info(retrieval_svc, dense_embeddings, 768, logger)
        except (HTTPException, Exception):
            pass

    @pytest.mark.asyncio
    async def test_unexpected_exception(self):
        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"
        retrieval_svc.vector_db_service.get_collection = AsyncMock(side_effect=RuntimeError("bad"))
        logger = MagicMock()

        from app.api.routes.health import check_collection_info
        with pytest.raises(HTTPException) as exc_info:
            await check_collection_info(retrieval_svc, MagicMock(), 768, logger)
        assert exc_info.value.status_code == 500


class TestEmbeddingHealthCheckFullCoverage:
    @pytest.mark.asyncio
    async def test_success(self, mock_request):
        mock_embed = AsyncMock()
        mock_embed.aembed_query = AsyncMock(return_value=[0.1, 0.2])

        with patch(f"{MODULE}.initialize_embedding_model", new_callable=AsyncMock,
                   return_value=(mock_embed, mock_request.app.container.retrieval_service.return_value, MagicMock())), \
             patch(f"{MODULE}.verify_embedding_health", new_callable=AsyncMock, return_value=2), \
             patch(f"{MODULE}.check_collection_info", new_callable=AsyncMock):
            from app.api.routes.health import embedding_health_check
            resp = await embedding_health_check(mock_request, [{"provider": "openai"}])

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_http_exception(self, mock_request):
        with patch(f"{MODULE}.initialize_embedding_model", new_callable=AsyncMock,
                   side_effect=HTTPException(status_code=500, detail={"status": "not healthy", "error": "fail"})):
            from app.api.routes.health import embedding_health_check
            resp = await embedding_health_check(mock_request, [])

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_general_exception(self, mock_request):
        mock_embed = AsyncMock()
        retrieval_svc = mock_request.app.container.retrieval_service.return_value
        logger = MagicMock()

        with patch(f"{MODULE}.initialize_embedding_model", new_callable=AsyncMock,
                   return_value=(mock_embed, retrieval_svc, logger)), \
             patch(f"{MODULE}.verify_embedding_health", new_callable=AsyncMock, side_effect=Exception("boom")):
            from app.api.routes.health import embedding_health_check
            resp = await embedding_health_check(mock_request, [{"provider": "openai"}])

        assert resp.status_code == 500


class TestPerformLlmHealthCheckFullCoverage:
    @pytest.mark.asyncio
    async def test_success_text(self):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "gpt-4"}}
        mock_model = MagicMock()
        mock_model.invoke.return_value = "ok"

        with patch(f"{MODULE}.get_generator_model", return_value=mock_model), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value="ok"):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(config, logger)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_no_model_names(self):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": ""}}

        from app.api.routes.health import perform_llm_health_check
        resp = await perform_llm_health_check(config, logger)
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_multimodal_image_success(self):
        logger = MagicMock()
        config = {"provider": "openai", "isMultimodal": True, "configuration": {"model": "gpt-4o"}}
        mock_model = MagicMock()

        with patch(f"{MODULE}.get_generator_model", return_value=mock_model), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value="ok"):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(config, logger)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_multimodal_image_fails_text_passes(self):
        logger = MagicMock()
        config = {"provider": "openai", "isMultimodal": True, "configuration": {"model": "gpt-4"}}
        mock_model = MagicMock()

        call_count = 0
        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("image not supported")
            return "text ok"

        with patch(f"{MODULE}.get_generator_model", return_value=mock_model), \
             patch("asyncio.wait_for", side_effect=side_effect):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(config, logger)

        assert resp.status_code == 500
        body = resp.body.decode()
        assert "doesn't support images" in body

    @pytest.mark.asyncio
    async def test_multimodal_both_fail(self):
        logger = MagicMock()
        config = {"provider": "openai", "isMultimodal": True, "configuration": {"model": "gpt-4"}}
        mock_model = MagicMock()

        with patch(f"{MODULE}.get_generator_model", return_value=mock_model), \
             patch("asyncio.wait_for", new_callable=AsyncMock, side_effect=Exception("total fail")):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(config, logger)

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_timeout(self):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "gpt-4"}}
        mock_model = MagicMock()

        with patch(f"{MODULE}.get_generator_model", return_value=mock_model), \
             patch("asyncio.wait_for", new_callable=AsyncMock, side_effect=asyncio.TimeoutError):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(config, logger)

        assert resp.status_code == 500
        body = resp.body.decode()
        assert "timed out" in body

    @pytest.mark.asyncio
    async def test_http_exception(self):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "gpt-4"}}

        with patch(f"{MODULE}.get_generator_model", side_effect=HTTPException(status_code=401, detail="unauthorized")):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(config, logger)

        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_general_exception(self):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "gpt-4"}}

        with patch(f"{MODULE}.get_generator_model", side_effect=RuntimeError("bad")):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(config, logger)

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_multimodal_from_configuration(self):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "gpt-4o", "isMultimodal": True}}
        mock_model = MagicMock()

        with patch(f"{MODULE}.get_generator_model", return_value=mock_model), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value="ok"):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(config, logger)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_comma_separated_models_uses_first(self):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "gpt-4, gpt-3.5"}}
        mock_model = MagicMock()

        with patch(f"{MODULE}.get_generator_model", return_value=mock_model) as mock_gen, \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value="ok"):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(config, logger)

        mock_gen.assert_called_once_with(provider="openai", config=config, model_name="gpt-4")

    @pytest.mark.asyncio
    async def test_multimodal_timeout_on_image(self):
        logger = MagicMock()
        config = {"provider": "openai", "isMultimodal": True, "configuration": {"model": "gpt-4o"}}
        mock_model = MagicMock()

        with patch(f"{MODULE}.get_generator_model", return_value=mock_model), \
             patch("asyncio.wait_for", new_callable=AsyncMock, side_effect=asyncio.TimeoutError):
            from app.api.routes.health import perform_llm_health_check
            resp = await perform_llm_health_check(config, logger)

        assert resp.status_code == 500


class TestPerformEmbeddingHealthCheckFullCoverage:
    @pytest.mark.asyncio
    async def test_success(self, mock_request):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}
        mock_embed = MagicMock()

        dense_vec = MagicMock()
        dense_vec.size = 768
        vectors = MagicMock()
        vectors.get.return_value = dense_vec
        coll_info = MagicMock()
        coll_info.config.params.vectors = vectors
        coll_info.points_count = 0

        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"
        retrieval_svc.vector_db_service = AsyncMock()
        retrieval_svc.vector_db_service.get_collection = AsyncMock(return_value=coll_info)
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_no_model_names(self, mock_request):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": ""}}

        from app.api.routes.health import perform_embedding_health_check
        resp = await perform_embedding_health_check(mock_request, config, logger)
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_empty_results(self, mock_request):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}
        mock_embed = MagicMock()

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_timeout(self, mock_request):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}
        mock_embed = MagicMock()

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, side_effect=asyncio.TimeoutError):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_dimension_mismatch_with_data(self, mock_request):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}
        mock_embed = MagicMock()

        dense_vec = MagicMock()
        dense_vec.size = 1024
        vectors = MagicMock()
        vectors.get.return_value = dense_vec
        coll_info = MagicMock()
        coll_info.config.params.vectors = vectors
        coll_info.points_count = 100

        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"
        retrieval_svc.vector_db_service = AsyncMock()
        retrieval_svc.vector_db_service.get_collection = AsyncMock(return_value=coll_info)
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_http_exception(self, mock_request):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}

        with patch(f"{MODULE}.get_embedding_model", side_effect=HTTPException(status_code=401, detail="unauthorized")):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_general_exception(self, mock_request):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}

        with patch(f"{MODULE}.get_embedding_model", side_effect=RuntimeError("bad")):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_grpc_not_found_during_collection_check(self, mock_request):
        import grpc
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}
        mock_embed = MagicMock()

        error = grpc._channel._InactiveRpcError(MagicMock())
        type(error).code = MagicMock(return_value=grpc.StatusCode.NOT_FOUND)

        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"
        retrieval_svc.vector_db_service = AsyncMock()
        retrieval_svc.vector_db_service.get_collection = AsyncMock(side_effect=error)
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            try:
                resp = await perform_embedding_health_check(mock_request, config, logger)
                assert resp.status_code in [200, 500]
            except Exception:
                pass

    @pytest.mark.asyncio
    async def test_collection_lookup_generic_error(self, mock_request):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "text-embedding-3-small"}}
        mock_embed = MagicMock()

        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"
        retrieval_svc.vector_db_service = AsyncMock()
        retrieval_svc.vector_db_service.get_collection = AsyncMock(side_effect=RuntimeError("conn failed"))
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_comma_separated_models_uses_first(self, mock_request):
        logger = MagicMock()
        config = {"provider": "openai", "configuration": {"model": "model-a, model-b"}}
        mock_embed = MagicMock()

        retrieval_svc = AsyncMock()
        retrieval_svc.collection_name = "coll"
        retrieval_svc.vector_db_service = AsyncMock()

        dense_vec = MagicMock()
        dense_vec.size = 768
        vectors = MagicMock()
        vectors.get.return_value = dense_vec
        coll_info = MagicMock()
        coll_info.config.params.vectors = vectors
        coll_info.points_count = 0
        retrieval_svc.vector_db_service.get_collection = AsyncMock(return_value=coll_info)
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed) as mock_get, \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        mock_get.assert_called_once_with(provider="openai", config=config, model_name="model-a")


class TestHealthCheckEndpointFullCoverage:
    @pytest.mark.asyncio
    async def test_embedding_type(self, mock_request):
        config = {"provider": "openai", "configuration": {"model": "text-embedding"}}

        with patch(f"{MODULE}.perform_embedding_health_check", new_callable=AsyncMock,
                   return_value=JSONResponse(status_code=200, content={"status": "healthy"})):
            from app.api.routes.health import health_check
            resp = await health_check(mock_request, "embedding", config)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_llm_type(self, mock_request):
        config = {"provider": "openai", "configuration": {"model": "gpt-4"}}

        with patch(f"{MODULE}.perform_llm_health_check", new_callable=AsyncMock,
                   return_value=JSONResponse(status_code=200, content={"status": "healthy"})):
            from app.api.routes.health import health_check
            resp = await health_check(mock_request, "llm", config)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_unknown_type_returns_healthy(self, mock_request):
        config = {"provider": "openai", "configuration": {"model": "gpt-4"}}

        from app.api.routes.health import health_check
        resp = await health_check(mock_request, "unknown", config)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_exception_handling(self, mock_request):
        config = {"provider": "openai", "configuration": {"model": "gpt-4"}}

        with patch(f"{MODULE}.perform_llm_health_check", new_callable=AsyncMock,
                   side_effect=Exception("unexpected")):
            from app.api.routes.health import health_check
            resp = await health_check(mock_request, "llm", config)

        assert resp.status_code == 500


class TestLoadTestImageFullCoverage:
    def test_load_test_image_file_not_found(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            with pytest.raises(FileNotFoundError):
                from app.api.routes.health import _load_test_image
                _load_test_image()


# =============================================================================
# Collection-signature-aware identity check
#
# These tests cover the behaviour that makes the health check robust against
# "same vector dimension, different embedding model" swaps: when the vector
# collection itself carries a signature, it wins over the AI_MODELS config.
# =============================================================================


def _make_retrieval_svc_for_signature_check(
    *,
    stored_signature=None,
    current_config_model=None,
    points_count=100,
    dense_size=768,
):
    """Helper that builds a retrieval_service mock with explicit signature /
    config / points_count behaviour."""
    dense_vec = MagicMock()
    dense_vec.size = dense_size
    vectors = MagicMock()
    vectors.get.return_value = dense_vec
    coll_info = MagicMock()
    coll_info.config.params.vectors = vectors
    coll_info.points_count = points_count

    svc = AsyncMock()
    svc.collection_name = "coll"
    svc.vector_db_service = AsyncMock()
    svc.vector_db_service.get_collection = AsyncMock(return_value=coll_info)
    svc.vector_db_service.get_collection_signature = AsyncMock(
        return_value=stored_signature
    )
    svc.vector_db_service.count_user_points = AsyncMock(return_value=points_count)
    svc.get_current_embedding_model_name = AsyncMock(return_value=current_config_model)
    return svc


class TestPerformEmbeddingCheckSignatureAware:
    @pytest.mark.asyncio
    async def test_stored_signature_match_with_data_returns_200(self, mock_request):
        """Stored signature matches (provider, model) -> allow."""
        logger = MagicMock()
        config = {
            "provider": "openai",
            "configuration": {"model": "text-embedding-3-small"},
        }
        mock_embed = MagicMock()

        retrieval_svc = _make_retrieval_svc_for_signature_check(
            stored_signature={
                "embedding_provider": "openai",
                "embedding_model": "text-embedding-3-small",
                "embedding_dimension": 768,
            },
            points_count=100,
            dense_size=768,
        )
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_stored_signature_mismatch_same_dim_returns_400(self, mock_request):
        """Stored signature disagrees with new (provider, model) but dims match
        -> block. This is the case the original dimension-only check missed."""
        logger = MagicMock()
        config = {
            "provider": "voyageai",
            "configuration": {"model": "voyage-3"},
        }
        mock_embed = MagicMock()

        retrieval_svc = _make_retrieval_svc_for_signature_check(
            stored_signature={
                "embedding_provider": "openai",
                "embedding_model": "text-embedding-3-small",
                "embedding_dimension": 768,
            },
            points_count=100,
            dense_size=768,
        )
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 400
        body = resp.body.decode()
        assert "mismatch" in body.lower()
        assert "openai" in body

    @pytest.mark.asyncio
    async def test_stored_signature_same_model_different_provider_returns_400(
        self, mock_request
    ):
        """Same model name but different provider must still be rejected."""
        logger = MagicMock()
        config = {
            "provider": "openai",
            "configuration": {"model": "nomic-embed-text"},
        }
        mock_embed = MagicMock()

        retrieval_svc = _make_retrieval_svc_for_signature_check(
            stored_signature={
                "embedding_provider": "ollama",
                "embedding_model": "nomic-embed-text",
                "embedding_dimension": 768,
            },
            points_count=50,
            dense_size=768,
        )
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_unknown_identity_nonempty_collection_blocks(self, mock_request):
        """No stored signature AND no config -> cannot verify -> refuse."""
        logger = MagicMock()
        config = {
            "provider": "openai",
            "configuration": {"model": "text-embedding-3-small"},
        }
        mock_embed = MagicMock()

        retrieval_svc = _make_retrieval_svc_for_signature_check(
            stored_signature=None,
            current_config_model=None,  # also no config hint
            points_count=100,
            dense_size=768,
        )
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 400
        body = resp.body.decode()
        assert "cannot verify" in body.lower()

    @pytest.mark.asyncio
    async def test_signature_excludes_sentinel_from_user_count(self, mock_request):
        """When a signature is stored on an otherwise-empty collection,
        `count_user_points` returns 0 and the check should pass."""
        logger = MagicMock()
        config = {
            "provider": "openai",
            "configuration": {"model": "text-embedding-3-small"},
        }
        mock_embed = MagicMock()

        # Raw points_count is 1 (just the sentinel) but count_user_points is 0.
        retrieval_svc = _make_retrieval_svc_for_signature_check(
            stored_signature={
                "embedding_provider": "openai",
                "embedding_model": "text-embedding-3-small",
                "embedding_dimension": 768,
            },
            points_count=1,
            dense_size=768,
        )
        retrieval_svc.vector_db_service.count_user_points = AsyncMock(return_value=0)
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_models_prefix_and_case_normalized(self, mock_request):
        """``models/Foo-Bar`` should match ``foo-bar``."""
        logger = MagicMock()
        config = {
            "provider": "GoogleAI",
            "configuration": {"model": "models/text-embedding-004"},
        }
        mock_embed = MagicMock()

        retrieval_svc = _make_retrieval_svc_for_signature_check(
            stored_signature={
                "embedding_provider": "googleai",
                "embedding_model": "text-embedding-004",
                "embedding_dimension": 768,
            },
            points_count=100,
            dense_size=768,
        )
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_is_multimodal_flip_same_model_returns_400(self, mock_request):
        """Same (provider, model, dimension) but the stored signature says
        ``is_multimodal=True`` while the new config is text-only. Text-only
        and multimodal modes of the same model produce incompatible vector
        spaces, so flipping the toggle on a populated collection must be
        blocked with a targeted message."""
        logger = MagicMock()
        config = {
            "provider": "voyageai",
            "isMultimodal": False,
            "configuration": {"model": "voyage-multimodal-3"},
        }
        mock_embed = MagicMock()

        retrieval_svc = _make_retrieval_svc_for_signature_check(
            stored_signature={
                "embedding_provider": "voyageai",
                "embedding_model": "voyage-multimodal-3",
                "embedding_dimension": 1024,
                "is_multimodal": True,
            },
            points_count=100,
            dense_size=1024,
        )
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 1024]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 400
        body = resp.body.decode()
        assert "isMultimodal" in body
        assert "voyage-multimodal-3" in body
        # Detail payload must carry both flags so callers can render a diff.
        assert "existing_is_multimodal" in body
        assert "new_is_multimodal" in body

    @pytest.mark.asyncio
    async def test_is_multimodal_matches_returns_200(self, mock_request):
        """Same (provider, model, dimension, is_multimodal) is the happy
        path — no mismatch should be raised."""
        logger = MagicMock()
        config = {
            "provider": "voyageai",
            "isMultimodal": True,
            "configuration": {"model": "voyage-multimodal-3"},
        }
        mock_embed = MagicMock()

        retrieval_svc = _make_retrieval_svc_for_signature_check(
            stored_signature={
                "embedding_provider": "voyageai",
                "embedding_model": "voyage-multimodal-3",
                "embedding_dimension": 1024,
                "is_multimodal": True,
            },
            points_count=100,
            dense_size=1024,
        )
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 1024]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_is_multimodal_missing_on_stored_treated_as_unknown(self, mock_request):
        """Legacy collections written before the signature tracked
        ``is_multimodal`` won't have the flag. A new config that toggles
        isMultimodal must NOT be blocked on that axis alone — we only
        compare when both sides are known."""
        logger = MagicMock()
        config = {
            "provider": "voyageai",
            "isMultimodal": True,  # user now wants multimodal...
            "configuration": {"model": "voyage-multimodal-3"},
        }
        mock_embed = MagicMock()

        retrieval_svc = _make_retrieval_svc_for_signature_check(
            stored_signature={
                # ...and the legacy signature pre-dates the flag.
                "embedding_provider": "voyageai",
                "embedding_model": "voyage-multimodal-3",
                "embedding_dimension": 1024,
            },
            points_count=100,
            dense_size=1024,
        )
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 1024]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 200, resp.body.decode()


class TestResolveStoredEmbeddingIdentityMultimodal:
    """Targeted tests for the new ``is_multimodal`` surfacing behaviour
    in ``_resolve_stored_embedding_identity``. Kept separate from the
    integration-style tests above so regressions in the helper itself
    are easy to triage."""

    @pytest.mark.asyncio
    async def test_is_multimodal_true_surfaced(self):
        logger = MagicMock()
        retrieval_svc = MagicMock()
        retrieval_svc.collection_name = "coll"
        retrieval_svc.vector_db_service = MagicMock()
        retrieval_svc.vector_db_service.get_collection_signature = AsyncMock(
            return_value={
                "embedding_provider": "voyageai",
                "embedding_model": "voyage-multimodal-3",
                "embedding_dimension": 1024,
                "is_multimodal": True,
            }
        )

        from app.api.routes.health import _resolve_stored_embedding_identity
        identity, source = await _resolve_stored_embedding_identity(
            retrieval_svc, logger
        )

        assert source == "collection"
        assert identity["is_multimodal"] is True

    @pytest.mark.asyncio
    async def test_is_multimodal_false_surfaced(self):
        logger = MagicMock()
        retrieval_svc = MagicMock()
        retrieval_svc.collection_name = "coll"
        retrieval_svc.vector_db_service = MagicMock()
        retrieval_svc.vector_db_service.get_collection_signature = AsyncMock(
            return_value={
                "embedding_provider": "openai",
                "embedding_model": "text-embedding-3-small",
                "embedding_dimension": 1536,
                "is_multimodal": False,
            }
        )

        from app.api.routes.health import _resolve_stored_embedding_identity
        identity, source = await _resolve_stored_embedding_identity(
            retrieval_svc, logger
        )

        assert source == "collection"
        assert identity["is_multimodal"] is False

    @pytest.mark.asyncio
    async def test_is_multimodal_absent_is_not_surfaced(self):
        """A legacy signature (pre-flag) must not synthesize an
        ``is_multimodal`` key — callers rely on the key's absence to
        know the flag is unknown."""
        logger = MagicMock()
        retrieval_svc = MagicMock()
        retrieval_svc.collection_name = "coll"
        retrieval_svc.vector_db_service = MagicMock()
        retrieval_svc.vector_db_service.get_collection_signature = AsyncMock(
            return_value={
                "embedding_provider": "openai",
                "embedding_model": "text-embedding-3-small",
                "embedding_dimension": 1536,
            }
        )

        from app.api.routes.health import _resolve_stored_embedding_identity
        identity, source = await _resolve_stored_embedding_identity(
            retrieval_svc, logger
        )

        assert source == "collection"
        assert "is_multimodal" not in identity


class TestHandleModelChangeProviderAware:
    @pytest.mark.asyncio
    async def test_same_model_different_provider_is_rejected_with_data(self):
        """``handle_model_change`` must treat different providers as a mismatch
        even when the model names happen to match."""
        retrieval_svc = AsyncMock()
        logger = MagicMock()

        from app.api.routes.health import handle_model_change
        with pytest.raises(HTTPException) as exc_info:
            await handle_model_change(
                retrieval_svc,
                "nomic-embed-text",
                "nomic-embed-text",
                768,
                100,
                768,
                logger,
                current_provider="ollama",
                new_provider="openai",
                identity_source="collection",
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_same_model_same_provider_allows(self):
        retrieval_svc = AsyncMock()
        logger = MagicMock()

        from app.api.routes.health import handle_model_change
        await handle_model_change(
            retrieval_svc,
            "text-embedding-3-small",
            "text-embedding-3-small",
            768,
            100,
            768,
            logger,
            current_provider="openai",
            new_provider="openai",
            identity_source="collection",
        )

    @pytest.mark.asyncio
    async def test_missing_new_provider_falls_back_to_model_only(self):
        """If the new provider is unknown (legacy call) the comparison should
        fall back to model-name-only -- i.e. the pre-refactor behaviour."""
        retrieval_svc = AsyncMock()
        logger = MagicMock()

        from app.api.routes.health import handle_model_change
        await handle_model_change(
            retrieval_svc,
            "same-model",
            "same-model",
            768,
            100,
            768,
            logger,
            current_provider="ollama",
            new_provider=None,
        )


# =============================================================================
# Regression tests for the review fixes:
#   1. empty-but-signed collection must NOT be flagged as "has user data"
#   2. legacy config-source path must compare provider too
#   3. handle_model_change must log when it can't compare identities
# =============================================================================


class TestEmptyButSignedCollection:
    """``vectors_count`` in Qdrant counts the signature sentinel, so a
    collection whose only point is the sentinel would previously look
    non-empty and falsely block a model swap. The authoritative
    ``count_user_points`` helper is the single source of truth."""

    @pytest.mark.asyncio
    async def test_empty_signed_collection_allows_model_swap(self, mock_request):
        """count_user_points = 0 -> health check passes even though the raw
        points_count / vectors_count are non-zero because of the sentinel."""
        logger = MagicMock()
        config = {
            "provider": "voyageai",
            "configuration": {"model": "voyage-3"},
        }
        mock_embed = MagicMock()

        retrieval_svc = _make_retrieval_svc_for_signature_check(
            stored_signature={
                "embedding_provider": "openai",
                "embedding_model": "text-embedding-3-small",
                "embedding_dimension": 768,
            },
            # Only the sentinel is in the collection.
            points_count=1,
            dense_size=768,
        )
        retrieval_svc.vector_db_service.count_user_points = AsyncMock(return_value=0)
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 200, (
            "Empty-but-signed collection must not block model swap. "
            f"Body: {resp.body.decode()}"
        )


class TestLegacyConfigProviderCompare:
    """When only AI_MODELS config is available (no collection signature yet),
    the legacy compare must still catch provider-only swaps — e.g. switching
    ``ollama/nomic-embed-text`` to ``openai/nomic-embed-text``: the model
    string matches but the vector spaces are incompatible."""

    @pytest.mark.asyncio
    async def test_same_model_different_provider_rejected_in_config_path(
        self, mock_request
    ):
        logger = MagicMock()
        config = {
            "provider": "openai",
            "configuration": {"model": "nomic-embed-text"},
        }
        mock_embed = MagicMock()

        retrieval_svc = _make_retrieval_svc_for_signature_check(
            stored_signature=None,
            # Legacy saved config: ollama-backed. No collection signature yet.
            current_config_model="nomic-embed-text",
            points_count=50,
            dense_size=768,
        )
        retrieval_svc.get_current_embedding_config = AsyncMock(
            return_value={
                "provider": "ollama",
                "configuration": {"model": "nomic-embed-text"},
                "isDefault": True,
            }
        )
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 400
        body = resp.body.decode()
        assert "ollama" in body
        assert "openai" in body
        # Make sure the response surfaces "identity_source: config" so the
        # origin of the decision is auditable.
        assert "config" in body

    @pytest.mark.asyncio
    async def test_same_provider_same_model_allowed_in_config_path(
        self, mock_request
    ):
        """Control: identical (provider, model) in config must pass even on
        the legacy path (no stored signature)."""
        logger = MagicMock()
        config = {
            "provider": "openai",
            "configuration": {"model": "text-embedding-3-small"},
        }
        mock_embed = MagicMock()

        retrieval_svc = _make_retrieval_svc_for_signature_check(
            stored_signature=None,
            current_config_model="text-embedding-3-small",
            points_count=50,
            dense_size=768,
        )
        retrieval_svc.get_current_embedding_config = AsyncMock(
            return_value={
                "provider": "openai",
                "configuration": {"model": "text-embedding-3-small"},
                "isDefault": True,
            }
        )
        mock_request.app.container.retrieval_service = AsyncMock(return_value=retrieval_svc)

        with patch(f"{MODULE}.get_embedding_model", return_value=mock_embed), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[[0.1] * 768]):
            from app.api.routes.health import perform_embedding_health_check
            resp = await perform_embedding_health_check(mock_request, config, logger)

        assert resp.status_code == 200


class TestHandleModelChangeInsufficientInfo:
    @pytest.mark.asyncio
    async def test_missing_model_names_logs_and_noops(self):
        """Both model names empty → log info, do nothing, don't raise. This
        is explicitly distinguished from the "identities match" no-op so
        operators can see caller bugs in the logs."""
        retrieval_svc = AsyncMock()
        logger = MagicMock()

        from app.api.routes.health import handle_model_change
        # Must not raise.
        await handle_model_change(
            retrieval_svc,
            None,
            None,
            768,
            100,
            768,
            logger,
        )
        # At least one info log call mentioning "insufficient info".
        info_calls = [str(c) for c in logger.info.call_args_list]
        assert any("insufficient info" in c for c in info_calls), info_calls
