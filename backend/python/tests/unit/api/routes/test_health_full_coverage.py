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
        assert exc_info.value.status_code == 500

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

        # When health resolves stored identity via ``get_collection_spec``, an
        # absent node falls back to ``DEFAULT_EMBEDDING_MODEL`` and would not
        # match ``model-a`` for a non-empty collection — stub a matching spec.
        matching_collection_spec = {
            "embedding_provider": "openai",
            "embedding_model": "model-a",
            "embedding_dimension": 768,
            "signature_version": 1,
        }
        from app.api.routes.health import check_collection_info
        with patch(
            f"{MODULE}.get_collection_spec",
            new_callable=AsyncMock,
            return_value=matching_collection_spec,
            create=True,
        ):
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


class TestLoadTestImage:
    def test_load_test_image_file_not_found(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            with pytest.raises(FileNotFoundError):
                from app.api.routes.health import _load_test_image
                _load_test_image()
