"""Unit tests for Vertex AI branches in app.utils.aimodels."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.utils.aimodels import (
    MAX_OUTPUT_TOKENS,
    EmbeddingProvider,
    LLMProvider,
    _create_vertex_credentials,
    get_embedding_model,
    get_generator_model,
)


def _minimal_service_account_dict() -> dict:
    return {
        "type": "service_account",
        "project_id": "test-project-123",
        "private_key_id": "keyid",
        "private_key": (
            "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7\n"
            "-----END PRIVATE KEY-----\n"
        ),
        "client_email": "vertex@test-project-123.iam.gserviceaccount.com",
        "client_id": "123456789",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }


class TestCreateVertexCredentials:
    def test_valid_json_returns_credentials(self):
        sa = _minimal_service_account_dict()
        payload = json.dumps(sa)
        mock_creds = MagicMock()
        with patch(
            "google.oauth2.service_account.Credentials.from_service_account_info",
            return_value=mock_creds,
        ) as mock_from_info:
            out = _create_vertex_credentials(payload)
        mock_from_info.assert_called_once()
        call_info = mock_from_info.call_args[0][0]
        assert call_info["project_id"] == "test-project-123"
        assert out is mock_creds

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _create_vertex_credentials("not json {")

    def test_incomplete_service_account_raises(self):
        bad = json.dumps({"type": "service_account", "project_id": "x"})
        with pytest.raises((ValueError, KeyError)):
            _create_vertex_credentials(bad)


class TestGetEmbeddingModelVertexAI:
    def _config(self, **overrides):
        base = {
            "configuration": {
                "model": "text-embedding-004",
                "project": "my-gcp-project",
                "location": "europe-west4",
                "serviceAccountJson": json.dumps(_minimal_service_account_dict()),
            },
            "isDefault": True,
        }
        base["configuration"].update(overrides)
        return base

    @patch("google.oauth2.service_account.Credentials.from_service_account_info")
    @patch("langchain_google_genai.GoogleGenerativeAIEmbeddings")
    def test_builds_vertex_embeddings(self, mock_emb_cls, _mock_from_info):
        mock_emb_cls.return_value = MagicMock()
        cfg = self._config()
        result = get_embedding_model(EmbeddingProvider.VERTEX_AI.value, cfg)
        mock_emb_cls.assert_called_once()
        kwargs = mock_emb_cls.call_args.kwargs
        assert kwargs["model"] == "text-embedding-004"
        assert kwargs["project"] == "my-gcp-project"
        assert kwargs["location"] == "europe-west4"
        assert kwargs["credentials"] is not None
        assert "output_dimensionality" not in kwargs
        assert result is mock_emb_cls.return_value

    @patch("google.oauth2.service_account.Credentials.from_service_account_info")
    @patch("langchain_google_genai.GoogleGenerativeAIEmbeddings")
    def test_default_location_when_empty(self, mock_emb_cls, _mock_from_info):
        mock_emb_cls.return_value = MagicMock()
        cfg = self._config(location="")
        get_embedding_model(EmbeddingProvider.VERTEX_AI.value, cfg)
        assert mock_emb_cls.call_args.kwargs["location"] == "us-central1"

    @patch("google.oauth2.service_account.Credentials.from_service_account_info")
    @patch("langchain_google_genai.GoogleGenerativeAIEmbeddings")
    def test_dimensions_forwarded(self, mock_emb_cls, _mock_from_info):
        mock_emb_cls.return_value = MagicMock()
        cfg = self._config(dimensions=768)
        get_embedding_model(EmbeddingProvider.VERTEX_AI.value, cfg)
        assert mock_emb_cls.call_args.kwargs["output_dimensionality"] == 768


class TestGetGeneratorModelVertexAI:
    def _config(self, **overrides):
        base = {
            "configuration": {
                "model": "gemini-2.5-flash",
                "project": "my-gcp-project",
                "location": "us-east1",
                "serviceAccountJson": json.dumps(_minimal_service_account_dict()),
                "temperature": 0.7,
            },
            "isDefault": True,
        }
        for k, v in overrides.items():
            if k in ("isReasoning",):
                base[k] = v
            else:
                base["configuration"][k] = v
        return base

    @patch("google.oauth2.service_account.Credentials.from_service_account_info")
    @patch("langchain_google_genai.ChatGoogleGenerativeAI")
    def test_builds_chat_vertex(self, mock_chat_cls, _mock_from_info):
        mock_chat_cls.return_value = MagicMock()
        cfg = self._config()
        result = get_generator_model(LLMProvider.VERTEX_AI.value, cfg)
        mock_chat_cls.assert_called_once()
        kwargs = mock_chat_cls.call_args.kwargs
        assert kwargs["model"] == "gemini-2.5-flash"
        assert kwargs["project"] == "my-gcp-project"
        assert kwargs["location"] == "us-east1"
        assert kwargs["temperature"] == 0.7
        assert kwargs["max_tokens"] == MAX_OUTPUT_TOKENS
        assert kwargs["max_retries"] == 2
        assert result is mock_chat_cls.return_value

    @patch("google.oauth2.service_account.Credentials.from_service_account_info")
    @patch("langchain_google_genai.ChatGoogleGenerativeAI")
    def test_model_name_does_not_affect_temperature(self, mock_chat_cls, _mock_from_info):
        """Gemini model id alone does not force reasoning temperature (use isReasoning or gpt-5 id)."""
        mock_chat_cls.return_value = MagicMock()
        cfg = self._config(model="gemini-2.5-flash", temperature=0.5)
        get_generator_model(LLMProvider.VERTEX_AI.value, cfg)
        assert mock_chat_cls.call_args.kwargs["temperature"] == 0.5

    @patch("google.oauth2.service_account.Credentials.from_service_account_info")
    @patch("langchain_google_genai.ChatGoogleGenerativeAI")
    def test_gpt5_model_id_forces_reasoning_temperature(self, mock_chat_cls, _mock_from_info):
        mock_chat_cls.return_value = MagicMock()
        cfg = self._config(model="gpt-5-chat", temperature=0.2)
        get_generator_model(LLMProvider.VERTEX_AI.value, cfg)
        assert mock_chat_cls.call_args.kwargs["temperature"] == 1

    @patch("google.oauth2.service_account.Credentials.from_service_account_info")
    @patch("langchain_google_genai.ChatGoogleGenerativeAI")
    def test_reasoning_flag_temperature(self, mock_chat_cls, _mock_from_info):
        mock_chat_cls.return_value = MagicMock()
        cfg = self._config(model="some-model", temperature=0.2)
        cfg["isReasoning"] = True
        get_generator_model(LLMProvider.VERTEX_AI.value, cfg)
        assert mock_chat_cls.call_args.kwargs["temperature"] == 1
