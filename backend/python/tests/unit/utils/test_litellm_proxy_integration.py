"""Integration tests for the LiteLLM Proxy provider in app.utils.aimodels.

All network calls are mocked — no running LiteLLM Proxy instance is required.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.aimodels import (
    EmbeddingProvider,
    ImageGenerationProvider,
    LLMProvider,
    STTProvider,
    TTSProvider,
    _OpenAIImageAdapter,
    _OpenAISTTAdapter,
    _OpenAITTSAdapter,
    get_embedding_model,
    get_generator_model,
    get_image_generation_model,
    get_stt_model,
    get_tts_model,
)

_ENDPOINT = "http://localhost:4000"
_API_KEY = "sk-litellm-test"


def _config(model: str, endpoint: str = _ENDPOINT, api_key: str = _API_KEY) -> dict:
    return {
        "configuration": {
            "model": model,
            "endpoint": endpoint,
            "apiKey": api_key,
        },
        "isDefault": True,
    }


# ---------------------------------------------------------------------------
# Enum values
# ---------------------------------------------------------------------------

class TestLiteLLMProxyEnums:
    def test_llm_provider_enum_value(self):
        assert LLMProvider.LITELLM_PROXY.value == "litellmProxy"

    def test_embedding_provider_enum_value(self):
        assert EmbeddingProvider.LITELLM_PROXY.value == "litellmProxy"

    def test_image_generation_provider_enum_value(self):
        assert ImageGenerationProvider.LITELLM_PROXY.value == "litellmProxy"

    def test_tts_provider_enum_value(self):
        assert TTSProvider.LITELLM_PROXY.value == "litellmProxy"

    def test_stt_provider_enum_value(self):
        assert STTProvider.LITELLM_PROXY.value == "litellmProxy"


# ---------------------------------------------------------------------------
# LLM dispatch
# ---------------------------------------------------------------------------

class TestLiteLLMProxyLLM:
    @patch("langchain_openai.ChatOpenAI")
    def test_dispatch_creates_chatopenai_with_user_endpoint(self, mock_cls):
        mock_cls.return_value = MagicMock()
        get_generator_model(LLMProvider.LITELLM_PROXY.value, _config("gpt-4o"))
        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["base_url"] == _ENDPOINT
        assert kwargs["model"] == "gpt-4o"
        assert kwargs["api_key"] == _API_KEY

    @patch("langchain_openai.ChatOpenAI")
    def test_default_temperature(self, mock_cls):
        mock_cls.return_value = MagicMock()
        get_generator_model(LLMProvider.LITELLM_PROXY.value, _config("gpt-4o"))
        assert mock_cls.call_args.kwargs["temperature"] == pytest.approx(0.2)

    @patch("langchain_openai.ChatOpenAI")
    def test_stream_usage_enabled(self, mock_cls):
        mock_cls.return_value = MagicMock()
        get_generator_model(LLMProvider.LITELLM_PROXY.value, _config("gpt-4o"))
        assert mock_cls.call_args.kwargs["stream_usage"] is True

    @patch("langchain_openai.ChatOpenAI")
    def test_custom_endpoint_forwarded(self, mock_cls):
        mock_cls.return_value = MagicMock()
        custom = "https://litellm.mycompany.com"
        get_generator_model(LLMProvider.LITELLM_PROXY.value, _config("claude-opus-4-5", endpoint=custom))
        assert mock_cls.call_args.kwargs["base_url"] == custom


# ---------------------------------------------------------------------------
# Embedding dispatch
# ---------------------------------------------------------------------------

class TestLiteLLMProxyEmbedding:
    @patch("langchain_openai.embeddings.OpenAIEmbeddings")
    def test_dispatch_creates_openai_embeddings_with_user_endpoint(self, mock_cls):
        mock_cls.return_value = MagicMock()
        get_embedding_model(EmbeddingProvider.LITELLM_PROXY.value, _config("text-embedding-3-small"))
        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["base_url"] == _ENDPOINT
        assert kwargs["model"] == "text-embedding-3-small"
        assert kwargs["api_key"] == _API_KEY

    @patch("langchain_openai.embeddings.OpenAIEmbeddings")
    def test_dimensions_forwarded(self, mock_cls):
        mock_cls.return_value = MagicMock()
        cfg = {
            "configuration": {
                "model": "text-embedding-3-small",
                "endpoint": _ENDPOINT,
                "apiKey": _API_KEY,
                "dimensions": 512,
            },
            "isDefault": True,
        }
        get_embedding_model(EmbeddingProvider.LITELLM_PROXY.value, cfg)
        assert mock_cls.call_args.kwargs["dimensions"] == 512


# ---------------------------------------------------------------------------
# TTS dispatch
# ---------------------------------------------------------------------------

class TestLiteLLMProxyTTS:
    def test_dispatch_returns_openai_tts_adapter_with_proxy_endpoint(self):
        adapter = get_tts_model(TTSProvider.LITELLM_PROXY.value, _config("tts-1"))
        assert isinstance(adapter, _OpenAITTSAdapter)
        assert adapter.provider == TTSProvider.LITELLM_PROXY.value
        assert adapter._base_url == _ENDPOINT

    def test_voice_forwarded(self):
        cfg = {
            "configuration": {
                "model": "tts-1",
                "endpoint": _ENDPOINT,
                "apiKey": _API_KEY,
                "voice": "nova",
            },
            "isDefault": True,
        }
        adapter = get_tts_model(TTSProvider.LITELLM_PROXY.value, cfg)
        assert adapter.default_voice == "nova"

    @pytest.mark.asyncio
    async def test_synthesize_passes_endpoint_to_async_openai(self):
        adapter = _OpenAITTSAdapter(
            model="tts-1",
            api_key=_API_KEY,
            base_url=_ENDPOINT,
            provider_override=TTSProvider.LITELLM_PROXY.value,
        )
        mock_response = SimpleNamespace(aread=AsyncMock(return_value=b"audio"))
        fake_client = SimpleNamespace(
            audio=SimpleNamespace(
                speech=SimpleNamespace(create=AsyncMock(return_value=mock_response))
            ),
            close=AsyncMock(),
        )

        captured_kwargs: dict = {}

        def capture_async_openai(**kwargs):
            captured_kwargs.update(kwargs)
            return fake_client

        with patch("openai.AsyncOpenAI", side_effect=capture_async_openai):
            result = await adapter.synthesize("hello")

        assert result == b"audio"
        assert captured_kwargs.get("base_url") == _ENDPOINT


# ---------------------------------------------------------------------------
# STT dispatch
# ---------------------------------------------------------------------------

class TestLiteLLMProxySTT:
    def test_dispatch_returns_openai_stt_adapter_with_proxy_endpoint(self):
        adapter = get_stt_model(STTProvider.LITELLM_PROXY.value, _config("whisper-1"))
        assert isinstance(adapter, _OpenAISTTAdapter)
        assert adapter.provider == STTProvider.LITELLM_PROXY.value
        assert adapter._base_url == _ENDPOINT

    @pytest.mark.asyncio
    async def test_transcribe_passes_endpoint_to_async_openai(self):
        adapter = _OpenAISTTAdapter(
            model="whisper-1",
            api_key=_API_KEY,
            base_url=_ENDPOINT,
            provider_override=STTProvider.LITELLM_PROXY.value,
        )

        mock_transcription = MagicMock()
        mock_transcription.text = "hello world"

        captured_kwargs: dict = {}

        def capture_async_openai(**kwargs):
            captured_kwargs.update(kwargs)
            fake_client = AsyncMock()
            fake_client.audio = AsyncMock()
            fake_client.audio.transcriptions = AsyncMock()
            fake_client.audio.transcriptions.create = AsyncMock(return_value=mock_transcription)
            fake_client.close = AsyncMock()
            return fake_client

        with patch("openai.AsyncOpenAI", side_effect=capture_async_openai):
            text = await adapter.transcribe(b"audio data", mime="audio/wav")

        assert text == "hello world"
        assert captured_kwargs.get("base_url") == _ENDPOINT


# ---------------------------------------------------------------------------
# Image generation dispatch
# ---------------------------------------------------------------------------

class TestLiteLLMProxyImage:
    def test_dispatch_returns_openai_image_adapter_with_proxy_endpoint(self):
        adapter = get_image_generation_model(
            ImageGenerationProvider.LITELLM_PROXY.value,
            _config("dall-e-3"),
        )
        assert isinstance(adapter, _OpenAIImageAdapter)
        assert adapter.provider == ImageGenerationProvider.LITELLM_PROXY.value
        assert adapter._base_url == _ENDPOINT
        assert adapter.model == "dall-e-3"

    @pytest.mark.asyncio
    async def test_generate_passes_endpoint_to_async_openai(self):
        import base64

        adapter = _OpenAIImageAdapter(
            model="dall-e-3",
            api_key=_API_KEY,
            base_url=_ENDPOINT,
            provider_override=ImageGenerationProvider.LITELLM_PROXY.value,
        )

        raw = b"\x89PNG..."
        b64_data = base64.b64encode(raw).decode("ascii")

        mock_item = MagicMock()
        mock_item.b64_json = b64_data
        mock_item.url = None

        mock_response = MagicMock()
        mock_response.data = [mock_item]

        captured_kwargs: dict = {}

        def capture_async_openai(**kwargs):
            captured_kwargs.update(kwargs)
            fake_client = AsyncMock()
            fake_client.images = AsyncMock()
            fake_client.images.generate = AsyncMock(return_value=mock_response)
            fake_client.close = AsyncMock()
            return fake_client

        with patch("openai.AsyncOpenAI", side_effect=capture_async_openai):
            images = await adapter.generate("a sunset")

        assert images == [raw]
        assert captured_kwargs.get("base_url") == _ENDPOINT
