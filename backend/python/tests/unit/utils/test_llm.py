"""Tests for app.utils.llm module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.llm import (
    get_embedding_model_config,
    get_image_generation_config,
    get_llm,
    get_stt_config,
    get_stt_model_instance,
    get_tts_config,
    get_tts_model_instance,
)


@pytest.fixture
def mock_config_service():
    service = MagicMock()
    service.get_config = AsyncMock()
    return service


class TestGetLlm:
    """Tests for get_llm function."""

    @pytest.mark.asyncio
    async def test_returns_default_llm(self, mock_config_service):
        """When a config has isDefault=True, use it."""
        mock_config_service.get_config.return_value = {
            "llm": [
                {"provider": "openAI", "isDefault": True, "model": "gpt-4"},
            ]
        }
        mock_llm = MagicMock()

        with patch("app.utils.llm.get_generator_model", return_value=mock_llm):
            llm, config = await get_llm(mock_config_service)

        assert llm is mock_llm
        assert config["provider"] == "openAI"
        assert config["isDefault"] is True

    @pytest.mark.asyncio
    async def test_falls_back_to_first_working_llm(self, mock_config_service):
        """When no isDefault, iterate and return first working LLM."""
        mock_config_service.get_config.return_value = {
            "llm": [
                {"provider": "anthropic", "model": "claude-3"},
                {"provider": "openAI", "model": "gpt-4"},
            ]
        }
        mock_llm = MagicMock()

        with patch("app.utils.llm.get_generator_model", side_effect=[None, mock_llm]):
            llm, config = await get_llm(mock_config_service)

        assert llm is mock_llm
        assert config["provider"] == "openAI"

    @pytest.mark.asyncio
    async def test_raises_when_no_llm_found(self, mock_config_service):
        """Raises ValueError if no LLM could be created."""
        mock_config_service.get_config.return_value = {
            "llm": [
                {"provider": "openAI", "model": "gpt-4"},
            ]
        }

        with patch("app.utils.llm.get_generator_model", return_value=None):
            with pytest.raises(ValueError, match="No LLM found"):
                await get_llm(mock_config_service)

    @pytest.mark.asyncio
    async def test_raises_when_no_llm_configs(self, mock_config_service):
        """Raises ValueError when llm configs list is empty."""
        mock_config_service.get_config.return_value = {
            "llm": []
        }

        with pytest.raises(ValueError, match="No LLM configurations found"):
            await get_llm(mock_config_service)

    @pytest.mark.asyncio
    async def test_raises_when_llm_configs_is_none(self, mock_config_service):
        """Raises ValueError when llm configs is None."""
        mock_config_service.get_config.return_value = {
            "llm": None
        }

        with pytest.raises(ValueError, match="No LLM configurations found"):
            await get_llm(mock_config_service)

    @pytest.mark.asyncio
    async def test_uses_provided_llm_configs(self, mock_config_service):
        """When llm_configs is provided, skips fetching from config_service."""
        llm_configs = [{"provider": "anthropic", "isDefault": True, "model": "claude-3"}]
        mock_llm = MagicMock()

        with patch("app.utils.llm.get_generator_model", return_value=mock_llm):
            llm, config = await get_llm(mock_config_service, llm_configs=llm_configs)

        assert llm is mock_llm
        assert config["provider"] == "anthropic"
        mock_config_service.get_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_default_preferred_over_non_default(self, mock_config_service):
        """Default LLM is tried first before non-default."""
        mock_config_service.get_config.return_value = {
            "llm": [
                {"provider": "openAI", "isDefault": False, "model": "gpt-3.5"},
                {"provider": "anthropic", "isDefault": True, "model": "claude-3"},
            ]
        }
        mock_llm_default = MagicMock()

        with patch("app.utils.llm.get_generator_model", return_value=mock_llm_default):
            llm, config = await get_llm(mock_config_service)

        assert config["isDefault"] is True
        assert config["provider"] == "anthropic"

    @pytest.mark.asyncio
    async def test_default_llm_returns_none_falls_through(self, mock_config_service):
        """When default LLM returns None, fall through to non-default loop."""
        mock_config_service.get_config.return_value = {
            "llm": [
                {"provider": "anthropic", "isDefault": True, "model": "claude-3"},
                {"provider": "openAI", "isDefault": False, "model": "gpt-4"},
            ]
        }
        mock_llm = MagicMock()

        # First call (default) returns None, second (fallback loop, anthropic) returns None,
        # third (fallback loop, openAI) returns the mock
        with patch("app.utils.llm.get_generator_model", side_effect=[None, None, mock_llm]):
            llm, config = await get_llm(mock_config_service)

        assert llm is mock_llm
        assert config["provider"] == "openAI"


class TestGetEmbeddingModelConfig:
    """Tests for get_embedding_model_config function."""

    @pytest.mark.asyncio
    async def test_returns_first_embedding_config(self, mock_config_service):
        mock_config_service.get_config.return_value = {
            "embedding": [
                {"provider": "openAI", "model": "text-embedding-3-small"},
                {"provider": "cohere", "model": "embed-v3"},
            ]
        }

        result = await get_embedding_model_config(mock_config_service)
        assert result["provider"] == "openAI"
        assert result["model"] == "text-embedding-3-small"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_embedding_configs(self, mock_config_service):
        mock_config_service.get_config.return_value = {
            "embedding": []
        }

        result = await get_embedding_model_config(mock_config_service)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_embedding_configs_is_none(self, mock_config_service):
        mock_config_service.get_config.return_value = {
            "embedding": None
        }

        result = await get_embedding_model_config(mock_config_service)
        assert result is None

    @pytest.mark.asyncio
    async def test_raises_on_config_service_error(self, mock_config_service):
        mock_config_service.get_config.side_effect = ConnectionError("etcd down")

        with pytest.raises(ConnectionError, match="etcd down"):
            await get_embedding_model_config(mock_config_service)

    @pytest.mark.asyncio
    async def test_raises_on_missing_embedding_key(self, mock_config_service):
        mock_config_service.get_config.return_value = {
            "llm": [{"provider": "openAI"}]
        }

        with pytest.raises(KeyError):
            await get_embedding_model_config(mock_config_service)


class TestGetImageGenerationConfig:
    """Tests for get_image_generation_config function."""

    @pytest.mark.asyncio
    async def test_returns_default_image_config(self, mock_config_service):
        """Prefers isDefault=True over first entry."""
        mock_config_service.get_config.return_value = {
            "imageGeneration": [
                {"provider": "openAI", "model": "dall-e-3"},
                {"provider": "stability", "model": "sdxl", "isDefault": True},
            ]
        }

        result = await get_image_generation_config(mock_config_service)
        assert result is not None
        assert result["provider"] == "stability"
        assert result["isDefault"] is True

    @pytest.mark.asyncio
    async def test_falls_back_to_first_when_no_default(self, mock_config_service):
        mock_config_service.get_config.return_value = {
            "imageGeneration": [
                {"provider": "openAI", "model": "dall-e-3"},
                {"provider": "stability", "model": "sdxl"},
            ]
        }

        result = await get_image_generation_config(mock_config_service)
        assert result is not None
        assert result["provider"] == "openAI"

    @pytest.mark.asyncio
    async def test_returns_none_when_empty(self, mock_config_service):
        mock_config_service.get_config.return_value = {"imageGeneration": []}
        result = await get_image_generation_config(mock_config_service)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_missing_key(self, mock_config_service):
        mock_config_service.get_config.return_value = {"llm": []}
        result = await get_image_generation_config(mock_config_service)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_bucket_is_none(self, mock_config_service):
        mock_config_service.get_config.return_value = {"imageGeneration": None}
        result = await get_image_generation_config(mock_config_service)
        assert result is None


class TestGetTtsConfig:
    """Tests for get_tts_config and _get_speech_config."""

    @pytest.mark.asyncio
    async def test_returns_default_tts(self, mock_config_service):
        mock_config_service.get_config.return_value = {
            "tts": [
                {"provider": "openAI", "model": "tts-1"},
                {"provider": "eleven", "model": "v2", "isDefault": True},
            ]
        }
        result = await get_tts_config(mock_config_service)
        assert result is not None
        assert result["provider"] == "eleven"

    @pytest.mark.asyncio
    async def test_falls_back_to_first_tts(self, mock_config_service):
        mock_config_service.get_config.return_value = {
            "tts": [{"provider": "openAI", "model": "tts-1"}]
        }
        result = await get_tts_config(mock_config_service)
        assert result is not None
        assert result["provider"] == "openAI"

    @pytest.mark.asyncio
    async def test_returns_none_when_ai_models_missing(self, mock_config_service):
        """Brand-new install: entire aiModels blob is empty/falsy."""
        mock_config_service.get_config.return_value = {}
        result = await get_tts_config(mock_config_service)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_tts_bucket_empty(self, mock_config_service):
        mock_config_service.get_config.return_value = {"tts": []}
        result = await get_tts_config(mock_config_service)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_tts_bucket_is_none(self, mock_config_service):
        mock_config_service.get_config.return_value = {"tts": None}
        result = await get_tts_config(mock_config_service)
        assert result is None


class TestGetSttConfig:
    """Tests for get_stt_config."""

    @pytest.mark.asyncio
    async def test_returns_default_stt(self, mock_config_service):
        mock_config_service.get_config.return_value = {
            "stt": [
                {"provider": "openAI", "model": "whisper-1"},
                {"provider": "deepgram", "model": "nova-2", "isDefault": True},
            ]
        }
        result = await get_stt_config(mock_config_service)
        assert result is not None
        assert result["provider"] == "deepgram"

    @pytest.mark.asyncio
    async def test_falls_back_to_first_stt(self, mock_config_service):
        mock_config_service.get_config.return_value = {
            "stt": [{"provider": "openAI", "model": "whisper-1"}]
        }
        result = await get_stt_config(mock_config_service)
        assert result is not None
        assert result["provider"] == "openAI"

    @pytest.mark.asyncio
    async def test_returns_none_when_empty(self, mock_config_service):
        mock_config_service.get_config.return_value = {"stt": []}
        result = await get_stt_config(mock_config_service)
        assert result is None


class TestGetTtsModelInstance:
    """Tests for get_tts_model_instance."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_config(self, mock_config_service):
        mock_config_service.get_config.return_value = {"tts": []}
        result = await get_tts_model_instance(mock_config_service)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_model_and_config(self, mock_config_service):
        mock_config_service.get_config.return_value = {
            "tts": [{"provider": "openAI", "isDefault": True, "model": "tts-1"}]
        }
        mock_model = MagicMock()
        with patch("app.utils.llm.get_tts_model", return_value=mock_model) as mock_get:
            result = await get_tts_model_instance(mock_config_service)

        assert result is not None
        model, config = result
        assert model is mock_model
        assert config["provider"] == "openAI"
        mock_get.assert_called_once_with("openAI", config)


class TestGetSttModelInstance:
    """Tests for get_stt_model_instance."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_config(self, mock_config_service):
        """When there is no STT config, returns None without calling get_stt_model."""
        mock_config_service.get_config.return_value = {}
        with patch("app.utils.llm.get_stt_model") as mock_get:
            result = await get_stt_model_instance(mock_config_service)
        assert result is None
        mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_model_and_config(self, mock_config_service):
        mock_config_service.get_config.return_value = {
            "stt": [{"provider": "deepgram", "model": "nova-2"}]
        }
        mock_model = MagicMock()
        with patch("app.utils.llm.get_stt_model", return_value=mock_model) as mock_get:
            result = await get_stt_model_instance(mock_config_service)

        assert result is not None
        model, config = result
        assert model is mock_model
        assert config["provider"] == "deepgram"
        mock_get.assert_called_once_with("deepgram", config)
