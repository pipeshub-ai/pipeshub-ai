"""Integration tests for the OpenRouter provider in app.utils.aimodels.

All network calls are mocked — no real API key is required.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.ai_models import OPENROUTER_BASE_URL
from app.utils.aimodels import (
    EmbeddingProvider,
    ImageGenerationProvider,
    LLMProvider,
    STTProvider,
    TTSProvider,
    _OpenAITTSAdapter,
    _OpenRouterImageAdapter,
    _OpenRouterSTTAdapter,
    _openrouter_stt_format,
    _size_to_openrouter_aspect_ratio,
    get_embedding_model,
    get_generator_model,
    get_image_generation_model,
    get_stt_model,
    get_tts_model,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _config(model: str, api_key: str = "sk-or-test") -> dict:
    return {
        "configuration": {"model": model, "apiKey": api_key},
        "isDefault": True,
    }


# ---------------------------------------------------------------------------
# LLM dispatch
# ---------------------------------------------------------------------------

class TestOpenRouterLLM:
    @patch("langchain_openai.ChatOpenAI")
    def test_dispatch_creates_chatopenai_with_openrouter_base_url(self, mock_cls):
        mock_cls.return_value = MagicMock()
        get_generator_model(LLMProvider.OPENROUTER.value, _config("anthropic/claude-sonnet-4"))
        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["base_url"] == OPENROUTER_BASE_URL
        assert kwargs["model"] == "anthropic/claude-sonnet-4"
        assert kwargs["api_key"] == "sk-or-test"

    @patch("langchain_openai.ChatOpenAI")
    def test_normal_model_uses_default_temperature(self, mock_cls):
        mock_cls.return_value = MagicMock()
        get_generator_model(LLMProvider.OPENROUTER.value, _config("openai/gpt-4o"))
        assert mock_cls.call_args.kwargs["temperature"] == pytest.approx(0.2)

    @patch("langchain_openai.ChatOpenAI")
    def test_stream_usage_enabled(self, mock_cls):
        mock_cls.return_value = MagicMock()
        get_generator_model(LLMProvider.OPENROUTER.value, _config("openai/gpt-4o"))
        assert mock_cls.call_args.kwargs["stream_usage"] is True


# ---------------------------------------------------------------------------
# Embedding dispatch
# ---------------------------------------------------------------------------

class TestOpenRouterEmbedding:
    @patch("langchain_openai.embeddings.OpenAIEmbeddings")
    def test_dispatch_creates_openai_embeddings_with_openrouter_base_url(self, mock_cls):
        mock_cls.return_value = MagicMock()
        get_embedding_model(EmbeddingProvider.OPENROUTER.value, _config("openai/text-embedding-3-small"))
        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["base_url"] == OPENROUTER_BASE_URL
        assert kwargs["model"] == "openai/text-embedding-3-small"

    @patch("langchain_openai.embeddings.OpenAIEmbeddings")
    def test_dimensions_forwarded(self, mock_cls):
        mock_cls.return_value = MagicMock()
        cfg = {
            "configuration": {
                "model": "openai/text-embedding-3-small",
                "apiKey": "sk-or-test",
                "dimensions": 512,
            },
            "isDefault": True,
        }
        get_embedding_model(EmbeddingProvider.OPENROUTER.value, cfg)
        assert mock_cls.call_args.kwargs["dimensions"] == 512


# ---------------------------------------------------------------------------
# TTS dispatch
# ---------------------------------------------------------------------------

class TestOpenRouterTTS:
    def test_dispatch_returns_openai_tts_adapter_with_openrouter_base_url(self):
        adapter = get_tts_model(TTSProvider.OPENROUTER.value, _config("openai/gpt-4o-mini-tts-2025-12-15"))
        assert isinstance(adapter, _OpenAITTSAdapter)
        assert adapter.provider == TTSProvider.OPENROUTER.value
        assert adapter._base_url == OPENROUTER_BASE_URL

    def test_voice_forwarded(self):
        cfg = {
            "configuration": {
                "model": "openai/gpt-4o-mini-tts-2025-12-15",
                "apiKey": "sk-or-test",
                "voice": "nova",
            },
            "isDefault": True,
        }
        adapter = get_tts_model(TTSProvider.OPENROUTER.value, cfg)
        assert adapter.default_voice == "nova"

    @pytest.mark.asyncio
    async def test_synthesize_passes_openrouter_base_url_to_async_openai(self):
        adapter = _OpenAITTSAdapter(
            model="openai/gpt-4o-mini-tts-2025-12-15",
            api_key="sk-or-test",
            base_url=OPENROUTER_BASE_URL,
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
        assert captured_kwargs.get("base_url") == OPENROUTER_BASE_URL


# ---------------------------------------------------------------------------
# STT dispatch
# ---------------------------------------------------------------------------

class TestOpenRouterSTT:
    def test_dispatch_returns_openrouter_stt_adapter(self):
        adapter = get_stt_model(STTProvider.OPENROUTER.value, _config("openai/whisper-1"))
        assert isinstance(adapter, _OpenRouterSTTAdapter)
        assert adapter.provider == STTProvider.OPENROUTER.value
        assert adapter.model == "openai/whisper-1"

    @pytest.mark.asyncio
    async def test_transcribe_posts_json_with_base64_input_audio(self):
        adapter = _OpenRouterSTTAdapter(model="openai/whisper-1", api_key="sk-or-test")
        raw_audio = b"\x00\x01\x02\x03"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"text": "hello world"}

        posted_body: dict = {}

        async def fake_post(url, headers=None, json=None, **kwargs):
            posted_body.update(json or {})
            return mock_response

        fake_client = AsyncMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=None)
        fake_client.post = fake_post

        with patch("httpx.AsyncClient", return_value=fake_client):
            text = await adapter.transcribe(raw_audio, mime="audio/wav")

        assert text == "hello world"
        assert posted_body["model"] == "openai/whisper-1"
        assert "input_audio" in posted_body
        assert posted_body["input_audio"]["format"] == "wav"
        assert posted_body["input_audio"]["data"] == base64.b64encode(raw_audio).decode("ascii")

    @pytest.mark.asyncio
    async def test_transcribe_includes_language_when_provided(self):
        adapter = _OpenRouterSTTAdapter(model="openai/whisper-1", api_key="sk-or-test")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"text": "bonjour"}

        posted_body: dict = {}

        async def fake_post(url, headers=None, json=None, **kwargs):
            posted_body.update(json or {})
            return mock_response

        fake_client = AsyncMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=None)
        fake_client.post = fake_post

        with patch("httpx.AsyncClient", return_value=fake_client):
            await adapter.transcribe(b"audio", mime="audio/mp3", language="fr")

        assert posted_body.get("language") == "fr"

    @pytest.mark.asyncio
    async def test_transcribe_raises_on_http_error(self):
        adapter = _OpenRouterSTTAdapter(model="openai/whisper-1", api_key="sk-or-test")

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_response.json.return_value = {"error": "Invalid API key"}

        async def fake_post(url, **kwargs):
            return mock_response

        fake_client = AsyncMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=None)
        fake_client.post = fake_post

        with patch("httpx.AsyncClient", return_value=fake_client):
            with pytest.raises(RuntimeError, match="OpenRouter STT transcription failed"):
                await adapter.transcribe(b"audio")


# ---------------------------------------------------------------------------
# STT format helper
# ---------------------------------------------------------------------------

class TestOpenRouterSttFormat:
    def test_webm_mime(self):
        assert _openrouter_stt_format("audio/webm") == "webm"

    def test_wav_mime(self):
        assert _openrouter_stt_format("audio/wav") == "wav"

    def test_mp3_mime(self):
        assert _openrouter_stt_format("audio/mpeg") == "mp3"

    def test_octet_stream_with_wav_filename(self):
        assert _openrouter_stt_format("application/octet-stream", "recording.wav") == "wav"

    def test_octet_stream_without_filename_defaults_to_webm(self):
        assert _openrouter_stt_format("application/octet-stream") == "webm"

    def test_empty_mime_defaults_to_webm(self):
        assert _openrouter_stt_format("") == "webm"


# ---------------------------------------------------------------------------
# Image generation dispatch
# ---------------------------------------------------------------------------

class TestOpenRouterImage:
    def test_dispatch_returns_openrouter_image_adapter(self):
        adapter = get_image_generation_model(
            ImageGenerationProvider.OPENROUTER.value,
            _config("bytedance-seed/seedream-4.5"),
        )
        assert isinstance(adapter, _OpenRouterImageAdapter)
        assert adapter.provider == ImageGenerationProvider.OPENROUTER.value
        assert adapter.model == "bytedance-seed/seedream-4.5"

    @pytest.mark.asyncio
    async def test_generate_posts_to_images_endpoint_with_aspect_ratio(self):
        adapter = _OpenRouterImageAdapter(
            model="bytedance-seed/seedream-4.5",
            api_key="sk-or-test",
        )
        raw = b"\x89PNG..."
        b64_data = base64.b64encode(raw).decode("ascii")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"b64_json": b64_data}]}

        posted_url: list[str] = []
        posted_body: dict = {}

        async def fake_post(url, headers=None, json=None, **kwargs):
            posted_url.append(url)
            posted_body.update(json or {})
            return mock_response

        fake_client = AsyncMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=None)
        fake_client.post = fake_post

        with patch("httpx.AsyncClient", return_value=fake_client):
            images = await adapter.generate("a red panda", size="1024x1024", n=1)

        assert images == [raw]
        assert posted_url[0] == f"{OPENROUTER_BASE_URL}/images"
        assert posted_body["model"] == "bytedance-seed/seedream-4.5"
        assert posted_body["aspect_ratio"] == "1:1"
        assert posted_body["n"] == 1

    @pytest.mark.asyncio
    async def test_generate_raises_on_http_error(self):
        adapter = _OpenRouterImageAdapter(model="some/model", api_key="sk-or-test")

        mock_response = MagicMock()
        mock_response.status_code = 402
        mock_response.text = "Payment required"
        mock_response.json.return_value = {"error": "Insufficient credits"}

        async def fake_post(url, **kwargs):
            return mock_response

        fake_client = AsyncMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=None)
        fake_client.post = fake_post

        with patch("httpx.AsyncClient", return_value=fake_client):
            with pytest.raises(RuntimeError, match="OpenRouter image generation failed"):
                await adapter.generate("prompt")


# ---------------------------------------------------------------------------
# Aspect ratio helper
# ---------------------------------------------------------------------------

class TestSizeToOpenRouterAspectRatio:
    def test_square(self):
        assert _size_to_openrouter_aspect_ratio("1024x1024") == "1:1"

    def test_portrait(self):
        assert _size_to_openrouter_aspect_ratio("1024x1792") == "9:16"

    def test_landscape(self):
        assert _size_to_openrouter_aspect_ratio("1792x1024") == "16:9"

    def test_unknown_defaults_to_square(self):
        assert _size_to_openrouter_aspect_ratio("512x512") == "1:1"
