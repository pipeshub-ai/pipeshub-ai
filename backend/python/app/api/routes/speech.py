"""HTTP routes for server-side Text-to-Speech and Speech-to-Text.

These endpoints are used by the chat UI when an admin has configured a
TTS/STT provider under ``/services/aiModels``. When no provider is
configured, the client falls back to the browser's Web Speech API.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.middlewares.auth import require_scopes
from app.config.configuration_service import ConfigurationService
from app.config.constants.service import OAuthScopes
from app.containers.query import QueryAppContainer
from app.utils.aimodels import tts_format_mime
from app.utils.llm import (
    get_stt_config,
    get_stt_model_instance,
    get_tts_config,
    get_tts_model_instance,
)

router = APIRouter()

# Matches OpenAI's audio.transcriptions.create ceiling and keeps self-hosted
# faster-whisper inside a sane single-request budget. Anything above this is
# almost certainly abusive and should be rejected before we buffer it.
MAX_STT_AUDIO_BYTES = 25 * 1024 * 1024

# OpenAI tts-1 / gpt-4o-mini-tts cap input at 4096 characters. We reject at
# the API boundary so a bad client can't run up the bill with huge prompts.
MAX_TTS_TEXT_CHARS = 4096

# Narrow allowlist of response formats we advertise in the UI. Anything else
# falls back to mp3 to keep Content-Type / adapter behaviour predictable.
_ALLOWED_TTS_FORMATS = {"mp3", "opus", "aac", "flac", "wav", "pcm"}

_MAX_TTS_SPEED = 4.0
_MIN_TTS_SPEED = 0.25


async def get_config_service(request: Request) -> ConfigurationService:
    container: QueryAppContainer = request.app.container
    return container.config_service()


async def get_logger(request: Request) -> Any:
    return request.app.container.logger()


class SpeakRequest(BaseModel):
    text: str = Field(..., description="UTF-8 text to synthesize.")
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
    logger: Any = Depends(get_logger),
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
    size = len(audio_bytes)
    if size == 0:
        raise HTTPException(status_code=400, detail="Empty audio payload")
    if size > MAX_STT_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Audio payload too large ({size} bytes). "
                f"Maximum is {MAX_STT_AUDIO_BYTES} bytes."
            ),
        )

    mime = file.content_type or "application/octet-stream"
    logger.info(
        "STT transcribe request: provider=%s model=%s size=%d mime=%s language=%s",
        config.get("provider"),
        adapter.model,
        size,
        mime,
        language,
    )
    try:
        text = await adapter.transcribe(
            audio_bytes,
            mime=mime,
            filename=file.filename,
            language=language,
        )
    except RuntimeError as exc:
        # Common operator misconfigurations:
        #   - 'whisper': faster-whisper missing (broken env).
        #   - 'wispr':   ffmpeg missing on the host, or transcode failure.
        logger.error("STT transcribe runtime error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        # Don't echo SDK error messages to the client — they often contain
        # URLs, request-ids, or other internal context. Log the full
        # details server-side and return a generic upstream failure.
        logger.error(
            "STT transcribe upstream failure: provider=%s model=%s: %s",
            config.get("provider"),
            adapter.model,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=502,
            detail="Transcription failed: upstream provider error",
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
    logger: Any = Depends(get_logger),
) -> Response:
    """Synthesize audio for ``payload.text`` using the configured TTS provider.

    Returns ``409`` if no TTS provider is configured.
    """
    if not payload.text or not payload.text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    if len(payload.text) > MAX_TTS_TEXT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Text too long ({len(payload.text)} characters). "
                f"Maximum is {MAX_TTS_TEXT_CHARS} characters."
            ),
        )

    instance = await get_tts_model_instance(config_service)
    if instance is None:
        raise HTTPException(
            status_code=409,
            detail="No Text-to-Speech provider configured",
        )
    adapter, _config = instance

    requested_format = (payload.format or adapter.default_format or "mp3").lower()
    if requested_format not in _ALLOWED_TTS_FORMATS:
        requested_format = "mp3"

    # Clamp speed to OpenAI's accepted range so a bad client can't trigger a
    # 400 from the provider (and to defend against odd float values).
    speed = payload.speed if payload.speed is not None else 1.0
    if speed < _MIN_TTS_SPEED:
        speed = _MIN_TTS_SPEED
    elif speed > _MAX_TTS_SPEED:
        speed = _MAX_TTS_SPEED

    logger.info(
        "TTS synthesize request: provider=%s model=%s chars=%d format=%s",
        adapter.provider,
        adapter.model,
        len(payload.text),
        requested_format,
    )

    try:
        audio_bytes = await adapter.synthesize(
            payload.text,
            voice=payload.voice,
            response_format=requested_format,
            speed=speed,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "TTS synthesize upstream failure: provider=%s model=%s: %s",
            adapter.provider,
            adapter.model,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=502,
            detail="Speech synthesis failed: upstream provider error",
        ) from exc

    mime = tts_format_mime(requested_format)

    # We already have the full payload in memory; using Response (instead of
    # StreamingResponse over a one-shot generator) gives us correct
    # Content-Length handling and avoids a needless event-loop hop.
    return Response(
        content=audio_bytes,
        media_type=mime,
        headers={
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
    browser Web Speech APIs. Secrets (API keys, etc.) are never returned —
    only the public provider/model summary.
    """
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
        default_model = models[0] if models else None
        return {
            "provider": cfg.get("provider"),
            # Active model the server will dispatch to — i.e. the default.
            # Kept for backwards compatibility with older chat clients.
            "model": default_model,
            # Explicit default fields the UI can surface without having to
            # re-derive them from the comma-separated list.
            "defaultModel": default_model,
            "models": models,
            # True when this config was picked because it is flagged
            # ``isDefault`` in ``aiModels``, False when we fell back to the
            # first configured entry.
            "isDefault": bool(cfg.get("isDefault")),
            "modelKey": cfg.get("modelKey"),
            "friendlyName": configuration.get("modelFriendlyName"),
        }

    return {
        "tts": _summary(tts_cfg),
        "stt": _summary(stt_cfg),
    }
