"""Unit tests for the embedding server FastAPI app."""

import base64
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config.constants.ai_models import DEFAULT_EMBEDDING_MODEL
from app.embedding_main import (
    DownloadStatus,
    ModelManager,
    _compute_expected_total_bytes,
    _format_embedding_vector,
    _measure_blobs_dir_bytes,
    _normalize_input,
    app,
    model_manager,
    run,
)


async def _passthrough_to_thread(func, *args, **kwargs):
    """Drop-in replacement for asyncio.to_thread that runs func synchronously."""
    return func(*args, **kwargs)


@pytest.fixture
def client():
    mock_manager = MagicMock(spec=ModelManager)
    mock_manager.list_loaded_models.return_value = [DEFAULT_EMBEDDING_MODEL]
    mock_manager.encode = AsyncMock(return_value=[[0.1, 0.2, 0.3]])

    mock_manager.warmup = AsyncMock()
    with patch("app.embedding_main.model_manager", mock_manager):
        with TestClient(app) as test_client:
            yield test_client, mock_manager


class TestEmbeddingServerRoutes:
    def test_health(self, client):
        test_client, _ = client
        response = test_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "healthy"
        assert DEFAULT_EMBEDDING_MODEL in body["loaded_models"]

    def test_list_models(self, client):
        test_client, _ = client
        response = test_client.get("/v1/models")
        assert response.status_code == 200
        body = response.json()
        assert body["object"] == "list"
        assert body["data"][0]["id"] == DEFAULT_EMBEDDING_MODEL

    def test_create_embeddings_single_input(self, client):
        test_client, mock_manager = client
        response = test_client.post(
            "/v1/embeddings",
            json={"model": DEFAULT_EMBEDDING_MODEL, "input": "hello"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["object"] == "list"
        assert len(body["data"]) == 1
        assert body["data"][0]["embedding"] == [0.1, 0.2, 0.3]
        mock_manager.encode.assert_awaited_once_with(
            DEFAULT_EMBEDDING_MODEL, ["hello"], trust_remote_code=False
        )

    def test_create_embeddings_batch_input(self, client):
        test_client, mock_manager = client
        mock_manager.encode = AsyncMock(
            return_value=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        )
        response = test_client.post(
            "/v1/embeddings",
            json={
                "model": DEFAULT_EMBEDDING_MODEL,
                "input": ["hello", "world"],
            },
        )
        assert response.status_code == 200
        mock_manager.encode.assert_awaited_once_with(
            DEFAULT_EMBEDDING_MODEL, ["hello", "world"], trust_remote_code=False
        )

    def test_create_embeddings_empty_input_rejected(self, client):
        test_client, _ = client
        response = test_client.post(
            "/v1/embeddings",
            json={"model": DEFAULT_EMBEDDING_MODEL, "input": []},
        )
        assert response.status_code == 400

    def test_create_embeddings_base64_format(self, client):
        test_client, mock_manager = client
        mock_manager.encode = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
        response = test_client.post(
            "/v1/embeddings",
            json={
                "model": DEFAULT_EMBEDDING_MODEL,
                "input": "hello",
                "encoding_format": "base64",
            },
        )
        assert response.status_code == 200
        body = response.json()
        expected = base64.b64encode(
            struct.pack("<3f", 0.1, 0.2, 0.3)
        ).decode("ascii")
        assert body["data"][0]["embedding"] == expected
        assert isinstance(body["data"][0]["embedding"], str)

    def test_create_embeddings_passes_trust_remote_code(self, client):
        test_client, mock_manager = client
        with patch("app.embedding_main.ALLOW_REMOTE_CODE", True):
            response = test_client.post(
                "/v1/embeddings",
                json={
                    "model": "nomic-ai/nomic-embed-text-v2-moe",
                    "input": "hello",
                    "trust_remote_code": True,
                },
            )
        assert response.status_code == 200
        mock_manager.encode.assert_awaited_once_with(
            "nomic-ai/nomic-embed-text-v2-moe",
            ["hello"],
            trust_remote_code=True,
        )


class TestEmbeddingServerSecurityPolicy:
    def test_trust_remote_code_rejected_by_default(self, client):
        test_client, _ = client
        with patch("app.embedding_main.ALLOW_REMOTE_CODE", False):
            response = test_client.post(
                "/v1/embeddings",
                json={
                    "model": DEFAULT_EMBEDDING_MODEL,
                    "input": "hello",
                    "trust_remote_code": True,
                },
            )
        assert response.status_code == 403
        assert "trust_remote_code" in response.json()["detail"]

    def test_trust_remote_code_false_always_allowed(self, client):
        test_client, _ = client
        with patch("app.embedding_main.ALLOW_REMOTE_CODE", False):
            response = test_client.post(
                "/v1/embeddings",
                json={"model": DEFAULT_EMBEDDING_MODEL, "input": "hello"},
            )
        assert response.status_code == 200

    def test_model_not_in_allowlist_rejected(self, client):
        test_client, _ = client
        allowlist = frozenset([DEFAULT_EMBEDDING_MODEL])
        with patch("app.embedding_main.ALLOWED_MODELS", allowlist):
            response = test_client.post(
                "/v1/embeddings",
                json={"model": "some-other/model", "input": "hello"},
            )
        assert response.status_code == 403
        assert "some-other/model" in response.json()["detail"]

    def test_model_in_allowlist_passes(self, client):
        test_client, _ = client
        allowlist = frozenset([DEFAULT_EMBEDDING_MODEL])
        with patch("app.embedding_main.ALLOWED_MODELS", allowlist):
            response = test_client.post(
                "/v1/embeddings",
                json={"model": DEFAULT_EMBEDDING_MODEL, "input": "hello"},
            )
        assert response.status_code == 200

    def test_no_allowlist_permits_any_model(self, client):
        test_client, _ = client
        with patch("app.embedding_main.ALLOWED_MODELS", None):
            response = test_client.post(
                "/v1/embeddings",
                json={"model": "any/model-name", "input": "hello"},
            )
        assert response.status_code == 200


class TestEmbeddingServerBaseUrl:
    def test_embedding_server_base_url_appends_v1(self):
        from app.utils.embedding_server_client import _embedding_server_base_url

        with patch.dict("os.environ", {"EMBEDDING_SERVER_URL": "http://embed:8002"}):
            assert _embedding_server_base_url() == "http://embed:8002/v1"

    def test_embedding_server_base_url_preserves_existing_v1(self):
        from app.utils.embedding_server_client import _embedding_server_base_url

        with patch.dict(
            "os.environ", {"EMBEDDING_SERVER_URL": "http://embed:8002/v1"}
        ):
            assert _embedding_server_base_url() == "http://embed:8002/v1"


class TestEmbeddingHelpers:
    def test_normalize_input_string(self):
        assert _normalize_input("hello") == ["hello"]

    def test_normalize_input_list(self):
        assert _normalize_input(["a", "b"]) == ["a", "b"]

    def test_normalize_input_empty_list_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            _normalize_input([])
        assert exc_info.value.status_code == 400

    def test_format_embedding_vector_float(self):
        assert _format_embedding_vector([0.1, 0.2], "float") == [0.1, 0.2]

    def test_format_embedding_vector_base64(self):
        vector = [0.1, 0.2, 0.3]
        expected = base64.b64encode(struct.pack("<3f", *vector)).decode("ascii")
        assert _format_embedding_vector(vector, "base64") == expected

    def test_format_embedding_vector_unsupported_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            _format_embedding_vector([0.1], "json")
        assert exc_info.value.status_code == 400
        assert "Unsupported encoding_format" in exc_info.value.detail


class TestModelManager:
    def test_cache_key(self):
        assert (
            ModelManager._cache_key("model-a", trust_remote_code=False)
            == "model-a::trust_remote_code=False"
        )
        assert (
            ModelManager._cache_key("model-a", trust_remote_code=True)
            == "model-a::trust_remote_code=True"
        )

    def test_list_loaded_models_deduplicates_trust_remote_code_keys(self):
        manager = ModelManager()
        manager._models = {
            "model-a::trust_remote_code=False": MagicMock(),
            "model-a::trust_remote_code=True": MagicMock(),
            "model-b::trust_remote_code=False": MagicMock(),
        }
        assert sorted(manager.list_loaded_models()) == ["model-a", "model-b"]

    @pytest.mark.asyncio
    async def test_get_model_returns_cached_without_loading(self):
        manager = ModelManager()
        cached = MagicMock(name="cached-model")
        manager._models["cached::trust_remote_code=False"] = cached

        with patch("sentence_transformers.SentenceTransformer") as mock_st:
            result = await manager.get_model("cached")

        assert result is cached
        mock_st.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_model_loads_from_local_cache(self):
        manager = ModelManager(device="cpu")
        loaded = MagicMock(name="loaded-model")

        with patch(
            "sentence_transformers.SentenceTransformer", return_value=loaded
        ) as mock_st:
            with patch(
                "app.embedding_main.asyncio.to_thread",
                side_effect=_passthrough_to_thread,
            ):
                result = await manager.get_model("local-model")

        assert result is loaded
        mock_st.assert_called_once_with(
            "local-model", local_files_only=True, device="cpu"
        )

    @pytest.mark.asyncio
    async def test_get_model_downloads_when_not_in_local_cache(self):
        manager = ModelManager(device="cpu")
        loaded = MagicMock(name="downloaded-model")
        call_count = {"n": 0}

        def _fake_sentence_transformer(model_name, **kwargs):
            assert model_name == "remote-model"
            # Both the initial cache-only probe and the post-download reload
            # use local_files_only=True; the actual network fetch happens in
            # _download_with_progress, not via SentenceTransformer itself.
            assert kwargs.get("local_files_only") is True
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("not in cache")
            return loaded

        with patch(
            "sentence_transformers.SentenceTransformer",
            side_effect=_fake_sentence_transformer,
        ):
            with patch(
                "app.embedding_main._download_with_progress"
            ) as mock_download:
                with patch(
                    "app.embedding_main.asyncio.to_thread",
                    side_effect=_passthrough_to_thread,
                ):
                    result = await manager.get_model("remote-model")

        assert result is loaded
        mock_download.assert_called_once()
        assert mock_download.call_args[0][0] == "remote-model"
        assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_get_model_passes_trust_remote_code_to_loader(self):
        manager = ModelManager(device="cpu")
        loaded = MagicMock(name="trusted-model")

        with patch(
            "sentence_transformers.SentenceTransformer", return_value=loaded
        ) as mock_st:
            with patch(
                "app.embedding_main.asyncio.to_thread",
                side_effect=_passthrough_to_thread,
            ):
                result = await manager.get_model(
                    "trusted-model", trust_remote_code=True
                )

        assert result is loaded
        mock_st.assert_called_once_with(
            "trusted-model",
            local_files_only=True,
            device="cpu",
            trust_remote_code=True,
        )

    @pytest.mark.asyncio
    async def test_warmup_delegates_to_get_model(self):
        manager = ModelManager()
        with patch.object(
            manager, "get_model", new_callable=AsyncMock, return_value=MagicMock()
        ) as mock_get:
            await manager.warmup("warm-model")
        mock_get.assert_awaited_once_with("warm-model")

    @pytest.mark.asyncio
    async def test_encode_returns_tolist_from_model(self):
        manager = ModelManager(normalize_embeddings=True, max_concurrency=2)
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([[0.1, 0.2], [0.3, 0.4]])

        with patch.object(
            manager, "get_model", new_callable=AsyncMock, return_value=mock_model
        ):
            with patch(
                "app.embedding_main.asyncio.to_thread",
                side_effect=_passthrough_to_thread,
            ):
                result = await manager.encode(
                    "encode-model", ["hello", "world"], trust_remote_code=True
                )

        assert result == [[0.1, 0.2], [0.3, 0.4]]
        mock_model.encode.assert_called_once_with(
            ["hello", "world"],
            normalize_embeddings=True,
            convert_to_numpy=True,
        )


class TestEmbeddingServerEdgeCases:
    def test_health_starting_when_default_model_not_loaded(self):
        mock_manager = MagicMock(spec=ModelManager)
        mock_manager.list_loaded_models.return_value = []
        mock_manager.warmup = AsyncMock()

        with patch("app.embedding_main.model_manager", mock_manager):
            with TestClient(app) as test_client:
                response = test_client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "starting"

    def test_health_unhealthy_on_exception(self):
        mock_manager = MagicMock(spec=ModelManager)
        mock_manager.list_loaded_models.side_effect = RuntimeError("db down")
        mock_manager.warmup = AsyncMock()

        with patch("app.embedding_main.model_manager", mock_manager):
            with TestClient(app) as test_client:
                response = test_client.get("/health")

        assert response.status_code == 500
        body = response.json()
        assert body["status"] == "unhealthy"
        assert "db down" in body["error"]

    def test_list_models_fallback_when_none_loaded(self):
        mock_manager = MagicMock(spec=ModelManager)
        mock_manager.list_loaded_models.return_value = []
        mock_manager.warmup = AsyncMock()

        with patch("app.embedding_main.model_manager", mock_manager):
            with TestClient(app) as test_client:
                response = test_client.get("/v1/models")

        assert response.status_code == 200
        assert response.json()["data"][0]["id"] == DEFAULT_EMBEDDING_MODEL

    def test_create_embeddings_unsupported_encoding_format(self, client):
        test_client, _ = client
        response = test_client.post(
            "/v1/embeddings",
            json={
                "model": DEFAULT_EMBEDDING_MODEL,
                "input": "hello",
                "encoding_format": "json",
            },
        )
        assert response.status_code == 400
        assert "Unsupported encoding_format" in response.json()["detail"]

    def test_create_embeddings_encode_failure_returns_500(self, client):
        test_client, mock_manager = client
        mock_manager.encode = AsyncMock(side_effect=RuntimeError("encode failed"))

        response = test_client.post(
            "/v1/embeddings",
            json={"model": DEFAULT_EMBEDDING_MODEL, "input": "hello"},
        )

        assert response.status_code == 500
        assert "encode failed" in response.json()["detail"]

    def test_lifespan_continues_when_warmup_fails(self):
        mock_manager = MagicMock(spec=ModelManager)
        mock_manager.list_loaded_models.return_value = [DEFAULT_EMBEDDING_MODEL]
        mock_manager.warmup = AsyncMock(side_effect=RuntimeError("warmup failed"))

        with patch("app.embedding_main.model_manager", mock_manager):
            with TestClient(app) as test_client:
                response = test_client.get("/health")

        assert response.status_code == 200
        mock_manager.warmup.assert_awaited_once()


class TestEmbeddingServerRun:
    def test_run_calls_uvicorn_with_explicit_port(self):
        with patch("app.embedding_main.uvicorn.run") as mock_uvicorn_run:
            run(host="127.0.0.1", port=9999, reload=True)

        mock_uvicorn_run.assert_called_once_with(
            "app.embedding_main:app",
            host="127.0.0.1",
            port=9999,
            log_level="info",
            reload=True,
        )

    def test_run_resolves_port_from_env_when_not_provided(self):
        with patch.dict("os.environ", {"EMBEDDING_SERVER_PORT": "7777"}):
            with patch("app.embedding_main.uvicorn.run") as mock_uvicorn_run:
                run(port=None)

        assert mock_uvicorn_run.call_args.kwargs["port"] == 7777


class TestDownloadStatus:
    def test_to_dict_rounds_progress(self):
        status = DownloadStatus(
            status="downloading",
            progress=42.567,
            downloaded_bytes=10,
            total_bytes=100,
        )

        body = status.to_dict()

        assert body["status"] == "downloading"
        assert body["progress"] == 42.6
        assert body["downloaded_bytes"] == 10
        assert body["total_bytes"] == 100
        assert body["error"] is None
        assert "started_at" in body
        assert "updated_at" in body

    def test_defaults_to_checking_with_zero_progress(self):
        status = DownloadStatus()
        assert status.status == "checking"
        assert status.to_dict()["progress"] == 0.0


class TestMeasureBlobsDirBytes:
    def test_returns_zero_for_missing_directory(self, tmp_path):
        assert _measure_blobs_dir_bytes(str(tmp_path / "missing")) == 0

    def test_sums_file_sizes_including_incomplete_downloads(self, tmp_path):
        blobs_dir = tmp_path / "blobs"
        blobs_dir.mkdir()
        (blobs_dir / "abc123").write_bytes(b"x" * 100)
        (blobs_dir / "def456.incomplete").write_bytes(b"y" * 50)

        assert _measure_blobs_dir_bytes(str(blobs_dir)) == 150


class TestComputeExpectedTotalBytes:
    def test_returns_filtered_size_sum(self):
        import types

        siblings = [
            types.SimpleNamespace(rfilename="model.safetensors", size=1000),
            types.SimpleNamespace(rfilename="model.onnx", size=500),
            types.SimpleNamespace(rfilename="config.json", size=10),
        ]
        mock_api = MagicMock()
        mock_api.model_info.return_value = MagicMock(siblings=siblings)

        with patch("huggingface_hub.HfApi", return_value=mock_api):
            total = _compute_expected_total_bytes("org/model")

        # model.onnx is excluded by MODEL_DOWNLOAD_IGNORE_PATTERNS.
        assert total == 1010

    def test_returns_zero_when_hub_lookup_fails(self):
        with patch("huggingface_hub.HfApi", side_effect=RuntimeError("offline")):
            assert _compute_expected_total_bytes("org/model") == 0


class TestModelManagerDownloadLifecycle:
    def test_get_download_status_returns_none_when_unknown(self):
        manager = ModelManager()
        assert manager.get_download_status("unknown/model") is None

    def test_start_prepare_returns_ready_status_when_already_loaded(self):
        manager = ModelManager()
        manager._models["cached::trust_remote_code=False"] = MagicMock()

        status = manager.start_prepare("cached")

        assert status.status == "ready"
        assert status.progress == 100.0

    def test_start_prepare_reuses_status_for_in_progress_task(self):
        manager = ModelManager()
        fake_task = MagicMock()
        fake_task.done.return_value = False
        manager._prepare_tasks["model-x"] = fake_task

        status1 = manager.start_prepare("model-x")
        status2 = manager.start_prepare("model-x")

        assert status1 is status2
        # The in-progress task must not be replaced by a fresh one.
        assert manager._prepare_tasks["model-x"] is fake_task

    @pytest.mark.asyncio
    async def test_start_prepare_runs_get_model_in_background(self):
        manager = ModelManager()
        loaded = MagicMock()

        with patch.object(
            manager, "get_model", new_callable=AsyncMock, return_value=loaded
        ) as mock_get:
            status = manager.start_prepare("bg-model", trust_remote_code=True)
            assert status.status == "checking"
            await manager._prepare_tasks["bg-model"]

        mock_get.assert_awaited_once_with("bg-model", trust_remote_code=True)

    @pytest.mark.asyncio
    async def test_start_prepare_marks_status_failed_on_background_error(self):
        manager = ModelManager()

        with patch.object(
            manager,
            "get_model",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            status = manager.start_prepare("bad-model")
            await manager._prepare_tasks["bad-model"]

        assert status.status == "failed"
        assert status.error == "boom"
        assert "bad-model" not in manager._prepare_tasks


class TestPrepareModelEndpoint:
    def test_returns_202_with_status_snapshot(self, client):
        test_client, mock_manager = client
        mock_manager.start_prepare.return_value = DownloadStatus(
            status="downloading", progress=10.0
        )

        response = test_client.post(
            "/prepare-model", json={"model": DEFAULT_EMBEDDING_MODEL}
        )

        assert response.status_code == 202
        body = response.json()
        assert body["model"] == DEFAULT_EMBEDDING_MODEL
        assert body["status"] == "downloading"
        mock_manager.start_prepare.assert_called_once_with(
            DEFAULT_EMBEDDING_MODEL, trust_remote_code=False
        )

    def test_rejects_model_not_in_allowlist(self, client):
        test_client, mock_manager = client
        allowlist = frozenset([DEFAULT_EMBEDDING_MODEL])

        with patch("app.embedding_main.ALLOWED_MODELS", allowlist):
            response = test_client.post(
                "/prepare-model", json={"model": "some-other/model"}
            )

        assert response.status_code == 403
        mock_manager.start_prepare.assert_not_called()

    def test_rejects_trust_remote_code_when_disabled(self, client):
        test_client, mock_manager = client

        with patch("app.embedding_main.ALLOW_REMOTE_CODE", False):
            response = test_client.post(
                "/prepare-model",
                json={"model": DEFAULT_EMBEDDING_MODEL, "trust_remote_code": True},
            )

        assert response.status_code == 403
        assert "trust_remote_code" in response.json()["detail"]
        mock_manager.start_prepare.assert_not_called()

    def test_allows_trust_remote_code_when_enabled(self, client):
        test_client, mock_manager = client
        mock_manager.start_prepare.return_value = DownloadStatus(
            status="ready", progress=100.0
        )

        with patch("app.embedding_main.ALLOW_REMOTE_CODE", True):
            response = test_client.post(
                "/prepare-model",
                json={"model": DEFAULT_EMBEDDING_MODEL, "trust_remote_code": True},
            )

        assert response.status_code == 202
        mock_manager.start_prepare.assert_called_once_with(
            DEFAULT_EMBEDDING_MODEL, trust_remote_code=True
        )


class TestDownloadProgressEndpoint:
    def test_returns_status_when_known(self, client):
        test_client, mock_manager = client
        mock_manager.get_download_status.return_value = DownloadStatus(
            status="downloading",
            progress=55.0,
            downloaded_bytes=550,
            total_bytes=1000,
        )

        response = test_client.get(f"/download-progress/{DEFAULT_EMBEDDING_MODEL}")

        assert response.status_code == 200
        body = response.json()
        assert body["model"] == DEFAULT_EMBEDDING_MODEL
        assert body["status"] == "downloading"
        assert body["progress"] == 55.0
        assert body["downloaded_bytes"] == 550

    def test_returns_ready_when_status_unknown_but_model_loaded(self, client):
        test_client, mock_manager = client
        mock_manager.get_download_status.return_value = None
        mock_manager.is_ready.return_value = True

        response = test_client.get(f"/download-progress/{DEFAULT_EMBEDDING_MODEL}")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["progress"] == 100.0

    def test_returns_not_found_when_status_and_model_unknown(self, client):
        test_client, mock_manager = client
        mock_manager.get_download_status.return_value = None
        mock_manager.is_ready.return_value = False

        response = test_client.get(f"/download-progress/{DEFAULT_EMBEDDING_MODEL}")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "not_found"
        assert body["progress"] == 0.0

    def test_decodes_percent_encoded_model_name(self, client):
        test_client, mock_manager = client
        mock_manager.get_download_status.return_value = None
        mock_manager.is_ready.return_value = False

        response = test_client.get("/download-progress/BAAI%2Fbge-m3")

        assert response.status_code == 200
        assert response.json()["model"] == "BAAI/bge-m3"
        mock_manager.get_download_status.assert_called_once_with("BAAI/bge-m3")
