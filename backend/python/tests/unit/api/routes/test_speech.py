"""Tests for app.api.routes.speech endpoints.

Exercises the direct function paths (bypassing the FastAPI middleware stack)
so we can verify the 409 fallback behaviour when TTS/STT are unconfigured
and the happy-path that streams adapter bytes back to the client.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


@pytest.fixture
def mock_config_service() -> MagicMock:
    return MagicMock(name="ConfigurationService")


class TestTranscribeRoute:
    @pytest.mark.asyncio
    async def test_returns_409_when_stt_unconfigured(
        self, mock_config_service: MagicMock
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
                )
        assert exc_info.value.status_code == 409
        assert "Speech-to-Text" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_returns_400_on_empty_payload(
        self, mock_config_service: MagicMock
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
                )
        assert exc_info.value.status_code == 400
        adapter.transcribe.assert_not_called()

    @pytest.mark.asyncio
    async def test_happy_path_returns_text_and_provider(
        self, mock_config_service: MagicMock
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


class TestSpeakRoute:
    @pytest.mark.asyncio
    async def test_returns_409_when_tts_unconfigured(
        self, mock_config_service: MagicMock
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
                )
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_returns_400_when_text_missing(
        self, mock_config_service: MagicMock
    ) -> None:
        from app.api.routes.speech import SpeakRequest, synthesize_speech

        with pytest.raises(HTTPException) as exc_info:
            await synthesize_speech(
                payload=SpeakRequest(text="   "),
                config_service=mock_config_service,
            )
        assert exc_info.value.status_code == 400


class TestCapabilitiesRoute:
    @pytest.mark.asyncio
    async def test_reports_none_when_unconfigured(
        self, mock_config_service: MagicMock
    ) -> None:
        from app.api.routes.speech import speech_capabilities

        with patch(
            "app.utils.llm.get_tts_config",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.utils.llm.get_stt_config",
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
            "app.utils.llm.get_tts_config",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.utils.llm.get_stt_config",
            new=AsyncMock(return_value=stt_cfg),
        ):
            result = await speech_capabilities(config_service=mock_config_service)
        assert result["tts"] is None
        assert result["stt"] == {
            "provider": "openAI",
            "model": "whisper-1",
            "friendlyName": "Whisper cloud",
        }
