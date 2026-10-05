"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/chat (speech)."""

from __future__ import annotations

import io
import wave
from typing import Any, Literal, Optional

import requests

from helper.http.api_client import APIClient
from helper.second_user import SecondUser

CHAT_SPEECH_BASE = "/api/v1/chat"
CAPABILITIES_ROUTE = f"{CHAT_SPEECH_BASE}/speech/capabilities"
SPEAK_ROUTE = f"{CHAT_SPEECH_BASE}/speak"
TRANSCRIBE_ROUTE = f"{CHAT_SPEECH_BASE}/transcribe"

# Mirrors of the Python limits (app/api/routes/speech.py); the Node multer cap is the same 25 MiB.
MAX_TTS_TEXT_CHARS = 4096
MAX_STT_AUDIO_BYTES = 25 * 1024 * 1024

AUDIO_FIELD = "file"
INVALID_AUTH_HEADERS = {"Authorization": "Bearer not-a-jwt"}

SpeechKind = Literal["tts", "stt"]
SpeechCapabilities = dict[str, Optional[dict[str, Any]]]


class ChatSpeechClient(APIClient):
    """Client for the speech routes under /api/v1/chat, acting as the shared org admin."""

    BASE = CHAT_SPEECH_BASE

    def capabilities(self, *, auth: bool = True, **kwargs: Any) -> requests.Response:
        return self.get("/speech/capabilities", auth=auth, **kwargs)

    def speak(
        self, body: Any = None, *, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        """POST /speak with a JSON body; pass ``data=``/``headers=`` for a raw one."""
        if body is not None:
            kwargs["json"] = body
        return self.post("/speak", auth=auth, **kwargs)

    def transcribe(
        self,
        audio: Optional[bytes] = None,
        *,
        field: str = AUDIO_FIELD,
        filename: str = "audio.wav",
        mime: str = "audio/wav",
        language: Optional[str] = None,
        auth: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        """POST /transcribe as multipart; ``audio=None`` sends a form with no file part."""
        form: dict[str, Any] = {}
        if audio is not None:
            form[field] = (filename, audio, mime)
        if language is not None:
            form["language"] = (None, language)
        if not form:
            # requests only emits multipart when files is non-empty.
            form["note"] = (None, "no-file")
        return self.post("/transcribe", auth=auth, files=form, **kwargs)


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a speech route as the non-admin member; path is relative to /api/v1/chat."""
    kwargs.setdefault("timeout", user.timeout)
    headers = dict(user.headers)
    if kwargs.get("files"):
        # The member's default JSON Content-Type would hide the multipart boundary.
        headers.pop("Content-Type", None)
    headers.update(kwargs.pop("headers", None) or {})
    return requests.request(
        method, f"{user.base_url}{CHAT_SPEECH_BASE}{path}", headers=headers, **kwargs
    )


def provider_configured(capabilities: SpeechCapabilities, kind: SpeechKind) -> bool:
    """Whether the org has a server-side TTS or STT provider, per the capabilities body."""
    return bool(capabilities.get(kind))


def silent_wav(milliseconds: int = 100) -> bytes:
    """A small valid mono 16 kHz WAV of silence."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * (16 * milliseconds))
    return buffer.getvalue()
