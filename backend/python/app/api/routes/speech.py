"""HTTP routes for server-side Text-to-Speech and Speech-to-Text.

These endpoints are used by the chat UI when an admin has configured a
TTS/STT provider under ``/services/aiModels``. When no provider is
configured, the client falls back to the browser's Web Speech API.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.middlewares.auth import require_scopes
from app.config.configuration_service import ConfigurationService
from app.config.constants.service import OAuthScopes
from app.containers.query import QueryAppContainer
from app.utils.aimodels import tts_format_mime
from app.utils.llm import get_stt_model_instance, get_tts_model_instance

router = APIRouter()


async def get_config_service(request: Request) -> ConfigurationService:
    container: QueryAppContainer = request.app.container
    return container.config_service()


class SpeakRequest(BaseModel):
    text: str
    voice: str | None = None
    format: str | None = None
    speed: float | None = None


# ---------------------------------------------------------------------------
# Speech-to-Text
# ---------------------------------------------------------------------------


@router.post(
    "/chat/transcribe",
    dependencies=[Depends(require_scopes(OAuthScopes.CONVERSATION_CHAT))],
)
async def transcribe_audio(
    file: UploadFile = File(...),
    language: str | None = Form(None),
    config_service: ConfigurationService = Depends(get_config_service),
) -> dict[str, Any]:
    """Transcribe an uploaded audio blob using the configured STT provider.

    Returns ``409`` if no STT provider is configured so the frontend can
    fall back to browser speech recognition.
    """
    instance = await get_stt_model_instance(config_service)
    if instance is None:
        raise HTTPException(
            status_code=409,
            detail="No Speech-to-Text provider configured",
        )
    adapter, config = instance

    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio payload")

    mime = file.content_type or "application/octet-stream"
    try:
        text = await adapter.transcribe(
            audio_bytes,
            mime=mime,
            filename=file.filename,
            language=language,
        )
    except RuntimeError as exc:
        # e.g. faster-whisper not installed for the 'whisper' provider.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - provider-specific errors
        raise HTTPException(
            status_code=502,
            detail=f"Transcription failed: {exc}",
        ) from exc

    return {
        "text": text,
        "provider": config.get("provider"),
        "model": adapter.model,
    }


# ---------------------------------------------------------------------------
# Text-to-Speech
# ---------------------------------------------------------------------------


@router.post(
    "/chat/speak",
    dependencies=[Depends(require_scopes(OAuthScopes.CONVERSATION_CHAT))],
)
async def synthesize_speech(
    payload: SpeakRequest,
    config_service: ConfigurationService = Depends(get_config_service),
) -> StreamingResponse:
    """Synthesize audio for ``payload.text`` using the configured TTS provider.

    Returns ``409`` if no TTS provider is configured.
    """
    if not payload.text or not payload.text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    instance = await get_tts_model_instance(config_service)
    if instance is None:
        raise HTTPException(
            status_code=409,
            detail="No Text-to-Speech provider configured",
        )
    adapter, _config = instance

    requested_format = (payload.format or adapter.default_format or "mp3").lower()

    try:
        audio_bytes = await adapter.synthesize(
            payload.text,
            voice=payload.voice,
            response_format=requested_format,
            speed=payload.speed or 1.0,
        )
    except Exception as exc:  # pragma: no cover - provider-specific errors
        raise HTTPException(
            status_code=502,
            detail=f"Speech synthesis failed: {exc}",
        ) from exc

    mime = tts_format_mime(requested_format)

    def _stream() -> Any:
        yield audio_bytes

    return StreamingResponse(
        _stream(),
        media_type=mime,
        headers={
            "Content-Length": str(len(audio_bytes)),
            "Cache-Control": "no-store",
            "X-TTS-Provider": adapter.provider,
            "X-TTS-Model": adapter.model,
        },
    )


# ---------------------------------------------------------------------------
# Capability discovery (used by the chat UI to choose server vs. browser)
# ---------------------------------------------------------------------------


@router.get(
    "/chat/speech/capabilities",
    dependencies=[Depends(require_scopes(OAuthScopes.CONVERSATION_CHAT))],
)
async def speech_capabilities(
    config_service: ConfigurationService = Depends(get_config_service),
) -> dict[str, Any]:
    """Report whether the server has TTS/STT providers configured.

    The chat UI calls this once on mount to decide between server-side and
    browser Web Speech APIs.
    """
    from app.utils.llm import get_stt_config, get_tts_config

    tts_cfg = await get_tts_config(config_service)
    stt_cfg = await get_stt_config(config_service)

    def _summary(cfg: dict | None) -> dict[str, Any] | None:
        if not cfg:
            return None
        configuration = cfg.get("configuration") or {}
        models = [
            m.strip()
            for m in str(configuration.get("model", "")).split(",")
            if m.strip()
        ]
        return {
            "provider": cfg.get("provider"),
            "model": models[0] if models else None,
            "friendlyName": configuration.get("modelFriendlyName"),
        }

    return {
        "tts": _summary(tts_cfg),
        "stt": _summary(stt_cfg),
    }
