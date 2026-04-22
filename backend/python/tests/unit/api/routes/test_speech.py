"""Tests for app.api.routes.speech endpoints.

Exercises the direct function paths (bypassing the FastAPI middleware stack)
so we can verify the 409 fallback behaviour when TTS/STT are unconfigured,
the 400/413 input validation, and the happy-path that returns adapter bytes
to the client.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import Response


@pytest.fixture
def mock_config_service() -> MagicMock:
    return MagicMock(name="ConfigurationService")


@pytest.fixture
def mock_logger() -> MagicMock:
    return MagicMock(name="Logger")


class TestTranscribeRoute:
    @pytest.mark.asyncio
    async def test_returns_409_when_stt_unconfigured(
        self, mock_config_service: MagicMock, mock_logger: MagicMock
    ) -> None:
        from app.api.routes.speech import transcribe_audio

        upload = MagicMock()
        upload.read = AsyncMock(return_value=b"audio")
        upload.content_type = "audio/webm"
        upload.filename = "speech.webm"

        with patch(
            "app.api.routes.speech.get_stt_model_instance",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await transcribe_audio(
                    file=upload,
                    language=None,
                    config_service=mock_config_service,
                    logger=mock_logger,
                )
        assert exc_info.value.status_code == 409
        assert "Speech-to-Text" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_returns_400_on_empty_payload(
        self, mock_config_service: MagicMock, mock_logger: MagicMock
    ) -> None:
        from app.api.routes.speech import transcribe_audio

        upload = MagicMock()
        upload.read = AsyncMock(return_value=b"")
        upload.content_type = "audio/webm"
        upload.filename = "speech.webm"

        adapter = MagicMock()
        adapter.transcribe = AsyncMock(return_value="should not be called")
        adapter.model = "whisper-1"

        with patch(
            "app.api.routes.speech.get_stt_model_instance",
            new=AsyncMock(return_value=(adapter, {"provider": "openAI"})),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await transcribe_audio(
                    file=upload,
                    language=None,
                    config_service=mock_config_service,
                    logger=mock_logger,
                )
        assert exc_info.value.status_code == 400
        adapter.transcribe.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_413_when_audio_too_large(
        self, mock_config_service: MagicMock, mock_logger: MagicMock
    ) -> None:
        from app.api.routes.speech import MAX_STT_AUDIO_BYTES, transcribe_audio

        upload = MagicMock()
        upload.read = AsyncMock(return_value=b"x" * (MAX_STT_AUDIO_BYTES + 1))
        upload.content_type = "audio/webm"
        upload.filename = "speech.webm"

        adapter = MagicMock()
        adapter.transcribe = AsyncMock(return_value="should not be called")
        adapter.model = "whisper-1"

        with patch(
            "app.api.routes.speech.get_stt_model_instance",
            new=AsyncMock(return_value=(adapter, {"provider": "openAI"})),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await transcribe_audio(
                    file=upload,
                    language=None,
                    config_service=mock_config_service,
                    logger=mock_logger,
                )
        assert exc_info.value.status_code == 413
        adapter.transcribe.assert_not_called()

    @pytest.mark.asyncio
    async def test_happy_path_returns_text_and_provider(
        self, mock_config_service: MagicMock, mock_logger: MagicMock
    ) -> None:
        from app.api.routes.speech import transcribe_audio

        upload = MagicMock()
        upload.read = AsyncMock(return_value=b"binary-audio")
        upload.content_type = "audio/webm"
        upload.filename = "speech.webm"

        adapter = MagicMock()
        adapter.transcribe = AsyncMock(return_value="hello world")
        adapter.model = "whisper-1"

        with patch(
            "app.api.routes.speech.get_stt_model_instance",
            new=AsyncMock(return_value=(adapter, {"provider": "openAI"})),
        ):
            result = await transcribe_audio(
                file=upload,
                language="en",
                config_service=mock_config_service,
                logger=mock_logger,
            )
        assert result == {
            "text": "hello world",
            "provider": "openAI",
            "model": "whisper-1",
        }
        adapter.transcribe.assert_awaited_once()
        call_kwargs = adapter.transcribe.await_args.kwargs
        assert call_kwargs["mime"] == "audio/webm"
        assert call_kwargs["filename"] == "speech.webm"
        assert call_kwargs["language"] == "en"

    @pytest.mark.asyncio
    async def test_upstream_error_is_not_echoed(
        self, mock_config_service: MagicMock, mock_logger: MagicMock
    ) -> None:
        """Internal provider messages must not leak in the HTTP response."""
        from app.api.routes.speech import transcribe_audio

        upload = MagicMock()
        upload.read = AsyncMock(return_value=b"binary-audio")
        upload.content_type = "audio/webm"
        upload.filename = "speech.webm"

        adapter = MagicMock()
        adapter.transcribe = AsyncMock(
            side_effect=Exception(
                "openai.com returned 401: Authorization header missing sk-proj-abc"
            )
        )
        adapter.model = "whisper-1"

        with patch(
            "app.api.routes.speech.get_stt_model_instance",
            new=AsyncMock(return_value=(adapter, {"provider": "openAI"})),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await transcribe_audio(
                    file=upload,
                    language=None,
                    config_service=mock_config_service,
                    logger=mock_logger,
                )
        assert exc_info.value.status_code == 502
        assert "sk-proj-abc" not in str(exc_info.value.detail)


class TestSpeakRoute:
    @pytest.mark.asyncio
    async def test_returns_409_when_tts_unconfigured(
        self, mock_config_service: MagicMock, mock_logger: MagicMock
    ) -> None:
        from app.api.routes.speech import SpeakRequest, synthesize_speech

        with patch(
            "app.api.routes.speech.get_tts_model_instance",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await synthesize_speech(
                    payload=SpeakRequest(text="hi"),
                    config_service=mock_config_service,
                    logger=mock_logger,
                )
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_returns_400_when_text_missing(
        self, mock_config_service: MagicMock, mock_logger: MagicMock
    ) -> None:
        from app.api.routes.speech import SpeakRequest, synthesize_speech

        with pytest.raises(HTTPException) as exc_info:
            await synthesize_speech(
                payload=SpeakRequest(text="   "),
                config_service=mock_config_service,
                logger=mock_logger,
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_returns_413_when_text_too_long(
        self, mock_config_service: MagicMock, mock_logger: MagicMock
    ) -> None:
        from app.api.routes.speech import (
            MAX_TTS_TEXT_CHARS,
            SpeakRequest,
            synthesize_speech,
        )

        with pytest.raises(HTTPException) as exc_info:
            await synthesize_speech(
                payload=SpeakRequest(text="a" * (MAX_TTS_TEXT_CHARS + 1)),
                config_service=mock_config_service,
                logger=mock_logger,
            )
        assert exc_info.value.status_code == 413

    @pytest.mark.asyncio
    async def test_happy_path_streams_audio_bytes(
        self, mock_config_service: MagicMock, mock_logger: MagicMock
    ) -> None:
        from app.api.routes.speech import SpeakRequest, synthesize_speech

        adapter = MagicMock()
        adapter.synthesize = AsyncMock(return_value=b"\x00\x01\x02audio")
        adapter.model = "tts-1"
        adapter.provider = "openAI"
        adapter.default_format = "mp3"

        with patch(
            "app.api.routes.speech.get_tts_model_instance",
            new=AsyncMock(return_value=(adapter, {"provider": "openAI"})),
        ):
            result = await synthesize_speech(
                payload=SpeakRequest(text="hi", format="mp3"),
                config_service=mock_config_service,
                logger=mock_logger,
            )
        assert isinstance(result, Response)
        assert result.media_type == "audio/mpeg"
        assert result.headers["X-TTS-Provider"] == "openAI"
        assert result.headers["X-TTS-Model"] == "tts-1"
        assert result.body == b"\x00\x01\x02audio"
        adapter.synthesize.assert_awaited_once()
        kwargs = adapter.synthesize.await_args.kwargs
        assert kwargs["response_format"] == "mp3"
        # Speed not supplied → defaults to 1.0.
        assert kwargs["speed"] == 1.0

    @pytest.mark.asyncio
    async def test_speed_is_clamped_to_valid_range(
        self, mock_config_service: MagicMock, mock_logger: MagicMock
    ) -> None:
        from app.api.routes.speech import SpeakRequest, synthesize_speech

        adapter = MagicMock()
        adapter.synthesize = AsyncMock(return_value=b"x")
        adapter.model = "tts-1"
        adapter.provider = "openAI"
        adapter.default_format = "mp3"

        with patch(
            "app.api.routes.speech.get_tts_model_instance",
            new=AsyncMock(return_value=(adapter, {"provider": "openAI"})),
        ):
            await synthesize_speech(
                payload=SpeakRequest(text="hi", speed=99.0),
                config_service=mock_config_service,
                logger=mock_logger,
            )
        assert adapter.synthesize.await_args.kwargs["speed"] == 4.0

    @pytest.mark.asyncio
    async def test_unknown_format_falls_back_to_mp3(
        self, mock_config_service: MagicMock, mock_logger: MagicMock
    ) -> None:
        from app.api.routes.speech import SpeakRequest, synthesize_speech

        adapter = MagicMock()
        adapter.synthesize = AsyncMock(return_value=b"x")
        adapter.model = "tts-1"
        adapter.provider = "openAI"
        adapter.default_format = "mp3"

        with patch(
            "app.api.routes.speech.get_tts_model_instance",
            new=AsyncMock(return_value=(adapter, {"provider": "openAI"})),
        ):
            result = await synthesize_speech(
                payload=SpeakRequest(text="hi", format="ogg_vorbis"),
                config_service=mock_config_service,
                logger=mock_logger,
            )
        assert result.media_type == "audio/mpeg"
        assert adapter.synthesize.await_args.kwargs["response_format"] == "mp3"


class TestCapabilitiesRoute:
    @pytest.mark.asyncio
    async def test_reports_none_when_unconfigured(
        self, mock_config_service: MagicMock
    ) -> None:
        from app.api.routes.speech import speech_capabilities

        with patch(
            "app.api.routes.speech.get_tts_config",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.api.routes.speech.get_stt_config",
            new=AsyncMock(return_value=None),
        ):
            result = await speech_capabilities(config_service=mock_config_service)
        assert result == {"tts": None, "stt": None}

    @pytest.mark.asyncio
    async def test_reports_summary_when_configured(
        self, mock_config_service: MagicMock
    ) -> None:
        from app.api.routes.speech import speech_capabilities

        stt_cfg = {
            "provider": "openAI",
            "configuration": {
                "model": "whisper-1",
                "modelFriendlyName": "Whisper cloud",
            },
        }
        with patch(
            "app.api.routes.speech.get_tts_config",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.api.routes.speech.get_stt_config",
            new=AsyncMock(return_value=stt_cfg),
        ):
            result = await speech_capabilities(config_service=mock_config_service)
        assert result["tts"] is None
        assert result["stt"] == {
            "provider": "openAI",
            "model": "whisper-1",
            "friendlyName": "Whisper cloud",
        }

    @pytest.mark.asyncio
    async def test_capabilities_does_not_leak_secrets(
        self, mock_config_service: MagicMock
    ) -> None:
        """Regression: the configured config contains apiKey, organizationId,
        etc. The capabilities summary must never surface those fields."""
        from app.api.routes.speech import speech_capabilities

        stt_cfg = {
            "provider": "openAI",
            "configuration": {
                "model": "whisper-1",
                "apiKey": "sk-super-secret",
                "organizationId": "org-xyz",
            },
        }
        with patch(
            "app.api.routes.speech.get_tts_config",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.api.routes.speech.get_stt_config",
            new=AsyncMock(return_value=stt_cfg),
        ):
            result = await speech_capabilities(config_service=mock_config_service)
        stt = result["stt"]
        assert stt is not None
        assert "apiKey" not in stt
        assert "organizationId" not in stt
        assert "sk-super-secret" not in str(result)
