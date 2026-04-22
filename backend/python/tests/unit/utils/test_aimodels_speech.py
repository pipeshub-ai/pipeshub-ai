"""Unit tests for TTS/STT factories and adapters in ``app.utils.aimodels``.

Covers the provider → adapter class dispatch in
:func:`get_tts_model` / :func:`get_stt_model`, and the ``synthesize`` /
``transcribe`` implementations of the OpenAI adapters (the provider SDK is
mocked so no network calls are made).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.utils.aimodels import (
    STTAdapter,
    STTProvider,
    TTSAdapter,
    TTSProvider,
    _OpenAISTTAdapter,
    _OpenAITTSAdapter,
    _WhisperLocalSTTAdapter,
    get_stt_model,
    get_tts_model,
    tts_format_mime,
)


def _openai_tts_config(model: str = "tts-1") -> dict:
    return {
        "configuration": {"apiKey": "sk-test", "model": model, "voice": "nova"},
        "isDefault": True,
    }


def _openai_stt_config(model: str = "whisper-1") -> dict:
    return {
        "configuration": {"apiKey": "sk-test", "model": model},
        "isDefault": True,
    }


def _whisper_config(model: str = "base") -> dict:
    return {
        "configuration": {
            "model": model,
            "device": "cpu",
            "computeType": "int8",
        },
        "isDefault": True,
    }


class TestGetTTSModel:
    def test_openai_dispatch(self) -> None:
        adapter = get_tts_model("openAI", _openai_tts_config())
        assert isinstance(adapter, TTSAdapter)
        assert isinstance(adapter, _OpenAITTSAdapter)
        assert adapter.provider == TTSProvider.OPENAI.value
        assert adapter.model == "tts-1"
        assert adapter.default_voice == "nova"

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported TTS provider"):
            get_tts_model("bogus", _openai_tts_config())

    def test_missing_model_raises(self) -> None:
        with pytest.raises(ValueError, match="No TTS model configured"):
            get_tts_model(
                "openAI",
                {"configuration": {"apiKey": "k", "model": ""}, "isDefault": True},
            )


class TestGetSTTModel:
    def test_openai_dispatch(self) -> None:
        adapter = get_stt_model("openAI", _openai_stt_config())
        assert isinstance(adapter, STTAdapter)
        assert isinstance(adapter, _OpenAISTTAdapter)
        assert adapter.provider == STTProvider.OPENAI.value
        assert adapter.model == "whisper-1"

    def test_whisper_dispatch(self) -> None:
        adapter = get_stt_model("whisper", _whisper_config())
        assert isinstance(adapter, _WhisperLocalSTTAdapter)
        assert adapter.provider == STTProvider.WHISPER.value
        assert adapter.model == "base"

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported STT provider"):
            get_stt_model("bogus", _openai_stt_config())


class TestOpenAITTSAdapterSynthesize:
    @pytest.mark.asyncio
    async def test_synthesize_returns_bytes(self) -> None:
        adapter = _OpenAITTSAdapter(model="tts-1", api_key="sk-test")

        mock_response = SimpleNamespace(aread=AsyncMock(return_value=b"audio-bytes"))
        fake_client = SimpleNamespace(
            audio=SimpleNamespace(
                speech=SimpleNamespace(create=AsyncMock(return_value=mock_response))
            ),
            close=AsyncMock(),
        )

        with patch("openai.AsyncOpenAI", return_value=fake_client):
            out = await adapter.synthesize("hello world", voice="nova")

        assert out == b"audio-bytes"
        fake_client.audio.speech.create.assert_awaited_once()
        kwargs = fake_client.audio.speech.create.await_args.kwargs
        assert kwargs["model"] == "tts-1"
        assert kwargs["voice"] == "nova"
        assert kwargs["input"] == "hello world"
        assert kwargs["response_format"] == "mp3"
        fake_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_synthesize_falls_back_to_mp3_for_unknown_format(self) -> None:
        adapter = _OpenAITTSAdapter(model="tts-1", api_key="sk-test")

        mock_response = SimpleNamespace(aread=AsyncMock(return_value=b"x"))
        fake_client = SimpleNamespace(
            audio=SimpleNamespace(
                speech=SimpleNamespace(create=AsyncMock(return_value=mock_response))
            ),
            close=AsyncMock(),
        )

        with patch("openai.AsyncOpenAI", return_value=fake_client):
            await adapter.synthesize("hi", response_format="ogg_vorbis")

        assert (
            fake_client.audio.speech.create.await_args.kwargs["response_format"]
            == "mp3"
        )


class TestOpenAISTTAdapterTranscribe:
    @pytest.mark.asyncio
    async def test_transcribe_returns_text(self) -> None:
        adapter = _OpenAISTTAdapter(model="whisper-1", api_key="sk-test")

        response = SimpleNamespace(text="the quick brown fox")
        fake_client = SimpleNamespace(
            audio=SimpleNamespace(
                transcriptions=SimpleNamespace(create=AsyncMock(return_value=response))
            ),
            close=AsyncMock(),
        )

        with patch("openai.AsyncOpenAI", return_value=fake_client):
            out = await adapter.transcribe(
                b"binary-audio",
                mime="audio/webm",
                filename="clip.webm",
                language="en",
            )

        assert out == "the quick brown fox"
        call_kwargs = fake_client.audio.transcriptions.create.await_args.kwargs
        assert call_kwargs["model"] == "whisper-1"
        assert call_kwargs["language"] == "en"
        # FastAPI sends the uploaded file as a tuple — exact shape is
        # (filename, bytes, content-type).
        sent_file = call_kwargs["file"]
        assert sent_file[0] == "clip.webm"
        assert sent_file[1] == b"binary-audio"
        assert sent_file[2] == "audio/webm"
        fake_client.close.assert_awaited_once()


class TestFormatMime:
    @pytest.mark.parametrize(
        "fmt,expected",
        [
            ("mp3", "audio/mpeg"),
            ("wav", "audio/wav"),
            ("flac", "audio/flac"),
            ("pcm", "audio/pcm"),
            ("opus", "audio/ogg"),
            ("unknown", "application/octet-stream"),
        ],
    )
    def test_mime_mapping(self, fmt: str, expected: str) -> None:
        assert tts_format_mime(fmt) == expected
