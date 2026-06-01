"""Unit tests for the embedding server FastAPI app."""

import base64
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config.constants.ai_models import DEFAULT_EMBEDDING_MODEL
from app.embedding_main import ModelManager, app, model_manager


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
            struct.pack("3f", 0.1, 0.2, 0.3)
        ).decode("ascii")
        assert body["data"][0]["embedding"] == expected
        assert isinstance(body["data"][0]["embedding"], str)

    def test_create_embeddings_passes_trust_remote_code(self, client):
        test_client, mock_manager = client
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
