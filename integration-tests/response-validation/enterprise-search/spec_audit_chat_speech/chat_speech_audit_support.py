"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/chat (speech)."""

from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import uuid
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Literal, Optional

import requests
from pymongo import MongoClient

from helper.config import MONGO_DB_NAME, MONGO_URI
from helper.http.api_client import APIClient
from helper.pipeshub_client import PipeshubClient
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
JSON_HEADERS = {"Content-Type": "application/json"}
MALFORMED_JSON_BODY = "{not json"

# A token of the suite's own OAuth client without conversation:chat.
NARROW_SCOPE = "org:read"

NO_TTS_PROVIDER = {"detail": "No Text-to-Speech provider configured"}
NO_STT_PROVIDER = {"detail": "No Speech-to-Text provider configured"}

# The fake provider below refuses any request whose body carries this marker.
PROVIDER_FAILURE_MARKER = "spec-audit-provider-failure"
FAKE_AUDIO = b"ID3spec-audit-audio"
FAKE_TRANSCRIPT = "spec audit transcript"
FAKE_PROVIDER = "litellmProxy"
FAKE_TTS_MODEL = "spec-audit-tts"
FAKE_STT_MODEL = "spec-audit-stt"

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


def request_with_headers(
    client: PipeshubClient, headers: dict[str, str], method: str, path: str, **kwargs: Any
) -> requests.Response:
    """Call a speech route with explicit headers; path is relative to /api/v1/chat."""
    kwargs.setdefault("timeout", client.timeout_seconds)
    return requests.request(
        method, f"{client.base_url}{CHAT_SPEECH_BASE}{path}", headers=headers, **kwargs
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


class _FakeOpenAIAudio(BaseHTTPRequestHandler):
    """The three calls a LiteLLM-proxy speech provider makes: health, speech, transcriptions."""

    def _reply(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._reply(status, json.dumps(payload).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        if self.path.rstrip("/").endswith("/health"):
            self._json(200, {"status": "healthy"})
        else:
            self._json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if PROVIDER_FAILURE_MARKER.encode() in body:
            # 400 rather than 5xx: the OpenAI SDK retries a 5xx, which only slows the test.
            self._json(400, {"error": {"message": "spec audit provider failure"}})
        elif self.path.endswith("/audio/speech"):
            self._reply(200, FAKE_AUDIO, "audio/mpeg")
        elif self.path.endswith("/audio/transcriptions"):
            self._json(200, {"text": FAKE_TRANSCRIPT})
        else:
            self._json(404, {"error": {"message": "not found"}})

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signature of the base class
        return


@contextmanager
def fake_openai_audio_server() -> Iterator[str]:
    """An OpenAI-compatible audio API on a free local port; yields its base URL.

    The query service runs on this machine, so it reaches the server on 127.0.0.1.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAIAudio)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()


def speech_provider_body(kind: SpeechKind, endpoint: str) -> dict[str, Any]:
    return {
        "modelType": kind,
        "provider": FAKE_PROVIDER,
        "configuration": {
            "endpoint": endpoint,
            "apiKey": "spec-audit-key",
            "model": FAKE_TTS_MODEL if kind == "tts" else FAKE_STT_MODEL,
            "modelFriendlyName": f"spec-audit {kind} {uuid.uuid4().hex[:6]}",
        },
        "isMultimodal": False,
        "isReasoning": False,
        "isDefault": True,
    }


def mint_narrow_scope_token(base_url: str, timeout: int = 60) -> str:
    """A client-credentials token of the suite's own OAuth client, limited to NARROW_SCOPE."""
    resp = requests.post(
        f"{base_url}/api/v1/oauth2/token",
        json={
            "grant_type": "client_credentials",
            "client_id": os.environ["CLIENT_ID"],
            "client_secret": os.environ["CLIENT_SECRET"],
            "scope": NARROW_SCOPE,
        },
        timeout=timeout,
    )
    assert resp.status_code == 200, f"minting a {NARROW_SCOPE} token: {resp.status_code} {resp.text[:300]}"
    granted = resp.json().get("scope")
    assert granted == NARROW_SCOPE, f"asked for {NARROW_SCOPE!r}, the token carries {granted!r}"
    return str(resp.json()["access_token"])


def forget_access_token(token: str) -> None:
    """Remove the stored row of an access token this suite minted (there is no delete API)."""
    client: MongoClient[dict[str, Any]] = MongoClient(MONGO_URI)
    try:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        client[MONGO_DB_NAME]["oauthAccessTokens"].delete_one({"tokenHash": token_hash})
    finally:
        client.close()
