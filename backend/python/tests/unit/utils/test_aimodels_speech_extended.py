"""Extended tests for the Gemini/Wispr/Whisper TTS and STT code paths in
``app.utils.aimodels``.

These tests stay hermetic: no real network, no ffmpeg on PATH, and no real
provider SDKs. ``httpx.AsyncClient`` is patched with a simple async
context-manager helper, ``asyncio.create_subprocess_exec`` is patched at the
boundary of the ffmpeg shellouts, and ``faster_whisper`` is injected into
``sys.modules`` so the optional import resolves deterministically.
"""

from __future__ import annotations

import base64
import builtins
import struct
import sys
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils import aimodels
from app.utils.aimodels import (
    _GEMINI_TTS_DEFAULT_VOICE,
    _WISPR_DEFAULT_ENDPOINT,
    _WISPR_MAX_WAV_BYTES,
    STTProvider,
    TTSProvider,
    _GeminiSTTAdapter,
    _GeminiTTSAdapter,
    _gemini_stt_mime,
    _OpenAISTTAdapter,
    _OpenAITTSAdapter,
    _reencode_pcm_via_ffmpeg,
    _stt_filename_for_mime,
    _transcode_to_wispr_wav,
    _WhisperLocalSTTAdapter,
    _WisprFlowSTTAdapter,
    _wrap_pcm_as_wav,
    get_stt_model,
    get_tts_model,
)


# ---------------------------------------------------------------------------
# httpx helpers
# ---------------------------------------------------------------------------


class _FakeAsyncClient:
    """Minimal stand-in for ``httpx.AsyncClient`` as used inside aimodels.

    The real call sites all look like::

        async with httpx.AsyncClient(timeout=...) as client:
            response = await client.post(url, headers=..., json=...)

    So we just need an async context manager whose ``post`` returns a
    caller-controlled response object.
    """

    _last_instance: "_FakeAsyncClient | None" = None

    def __init__(self, response: Any) -> None:
        self._response = response
        self.calls: list[Dict[str, Any]] = []
        _FakeAsyncClient._last_instance = self

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, headers=None, json=None, **_: Any):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self._response


def _fake_client_factory(response: Any):
    """Build a patchable ``httpx.AsyncClient`` replacement.

    Returns a ``(factory, holder)`` pair. ``factory`` is passed directly to
    ``patch("httpx.AsyncClient", ...)``; ``holder`` is a zero-arg callable
    returning the last constructed ``_FakeAsyncClient`` so tests can inspect
    the exact arguments passed to ``.post()``.
    """

    def factory(*_args: Any, **_kwargs: Any) -> _FakeAsyncClient:
        return _FakeAsyncClient(response)

    return factory, lambda: _FakeAsyncClient._last_instance


def _http_response(
    *,
    status_code: int = 200,
    json_payload: Any = None,
    json_raises: bool = False,
    text: str = "",
) -> SimpleNamespace:
    if json_raises:
        def _raise() -> Any:
            raise ValueError("not json")

        json_callable = _raise
    else:
        def _json() -> Any:
            return json_payload

        json_callable = _json
    return SimpleNamespace(
        status_code=status_code,
        text=text,
        json=json_callable,
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestWrapPcmAsWav:
    def test_wav_container_round_trip(self) -> None:
        # 4 samples of signed-16 PCM (8 bytes payload).
        pcm = struct.pack("<4h", 0, 1, -1, 2)
        wav = _wrap_pcm_as_wav(pcm)
        assert wav[:4] == b"RIFF"
        assert wav[8:12] == b"WAVE"
        # Payload must still be present verbatim at the end.
        assert wav.endswith(pcm)

    def test_wav_container_accepts_empty_pcm(self) -> None:
        wav = _wrap_pcm_as_wav(b"")
        assert wav.startswith(b"RIFF")
        assert b"WAVE" in wav


class TestSttFilenameForMime:
    @pytest.mark.parametrize(
        "mime,expected",
        [
            ("audio/webm", "audio.webm"),
            ("audio/ogg", "audio.ogg"),
            ("audio/mp4", "audio.mp4"),
            ("audio/m4a", "audio.m4a"),
            ("audio/mpeg", "audio.mp3"),
            ("audio/wav", "audio.wav"),
            ("audio/x-wav", "audio.wav"),
            ("audio/flac", "audio.flac"),
            ("AUDIO/WEBM", "audio.webm"),
        ],
    )
    def test_mime_mapping(self, mime: str, expected: str) -> None:
        assert _stt_filename_for_mime(mime) == expected

    def test_fallback_overrides_mapping(self) -> None:
        assert _stt_filename_for_mime("audio/webm", fallback="custom.bin") == "custom.bin"

    def test_unknown_mime_defaults_to_webm(self) -> None:
        assert _stt_filename_for_mime("audio/made-up") == "audio.webm"


class TestGeminiSttMime:
    def test_empty_returns_default(self) -> None:
        assert _gemini_stt_mime("") == "audio/mp3"

    def test_strips_codec_params(self) -> None:
        assert _gemini_stt_mime("audio/webm;codecs=opus") == "audio/webm"

    def test_unknown_passes_through_lowered(self) -> None:
        assert _gemini_stt_mime("AUDIO/Weird") == "audio/weird"

    def test_known_mapping(self) -> None:
        assert _gemini_stt_mime("audio/m4a") == "audio/mp4"
        assert _gemini_stt_mime("audio/mpeg") == "audio/mp3"

    def test_octet_stream_uses_filename_extension(self) -> None:
        assert _gemini_stt_mime("application/octet-stream", "rec.webm") == "audio/webm"
        assert _gemini_stt_mime("binary/octet-stream", "clip.mp3") == "audio/mp3"

    def test_octet_stream_without_filename_defaults_webm(self) -> None:
        assert _gemini_stt_mime("application/octet-stream") == "audio/webm"


# ---------------------------------------------------------------------------
# Adapter __init__ defaults
# ---------------------------------------------------------------------------


class TestAdapterInitDefaults:
    def test_gemini_tts_defaults(self) -> None:
        adapter = _GeminiTTSAdapter(
            model="gemini-2.5-flash-preview-tts",
            api_key="gk-test",
        )
        assert adapter.provider == TTSProvider.GEMINI.value
        assert adapter.model == "gemini-2.5-flash-preview-tts"
        assert adapter.default_voice == _GEMINI_TTS_DEFAULT_VOICE
        assert adapter.default_format == "wav"
        assert adapter._endpoint == "https://generativelanguage.googleapis.com"

    def test_gemini_tts_custom_format_is_lowered(self) -> None:
        adapter = _GeminiTTSAdapter(
            model="m",
            api_key="k",
            default_format="MP3",
        )
        assert adapter.default_format == "mp3"

    def test_wispr_defaults(self) -> None:
        adapter = _WisprFlowSTTAdapter(
            model="flow",
            api_key="wk",
        )
        assert adapter.provider == STTProvider.WISPR.value
        assert adapter._endpoint == _WISPR_DEFAULT_ENDPOINT
        assert adapter._default_language is None
        assert adapter._default_app_type == "ai"

    def test_wispr_custom_endpoint_trailing_slash(self) -> None:
        adapter = _WisprFlowSTTAdapter(
            model="flow",
            api_key="wk",
            endpoint="https://example.test/",
            default_language="en",
            default_app_type="custom",
        )
        assert adapter._endpoint == "https://example.test"
        assert adapter._default_language == "en"
        assert adapter._default_app_type == "custom"

    def test_gemini_stt_trailing_slash_stripped(self) -> None:
        adapter = _GeminiSTTAdapter(
            model="gemini-1.5-flash",
            api_key="gk",
            endpoint="https://example.test/",
        )
        assert adapter.provider == STTProvider.GEMINI.value
        assert adapter._endpoint == "https://example.test"


# ---------------------------------------------------------------------------
# Factory dispatch and error paths
# ---------------------------------------------------------------------------


class TestGetTTSModelExtended:
    def test_gemini_dispatch(self) -> None:
        config = {
            "configuration": {
                "apiKey": "gk-test",
                "model": "gemini-2.5-flash-preview-tts",
                "voice": "Puck",
                "responseFormat": "wav",
                "endpoint": "https://g.example/",
            },
            "isDefault": True,
        }
        adapter = get_tts_model("gemini", config)
        assert isinstance(adapter, _GeminiTTSAdapter)
        assert adapter.model == "gemini-2.5-flash-preview-tts"
        assert adapter.default_voice == "Puck"
        assert adapter.default_format == "wav"
        assert adapter._endpoint == "https://g.example"

    def test_openai_model_name_not_in_list_raises(self) -> None:
        config = {
            "configuration": {
                "apiKey": "sk",
                "model": "tts-1, tts-1-hd",
            },
            "isDefault": False,
        }
        with pytest.raises(ValueError, match="not found in"):
            get_tts_model("openAI", config, model_name="nope")

    def test_openai_model_name_not_default_list_ok(self) -> None:
        config = {
            "configuration": {
                "apiKey": "sk",
                "model": "tts-1, tts-1-hd",
            },
            "isDefault": False,
        }
        adapter = get_tts_model("openAI", config, model_name="tts-1-hd")
        assert isinstance(adapter, _OpenAITTSAdapter)
        assert adapter.model == "tts-1-hd"


class TestGetSTTModelExtended:
    def test_wispr_dispatch(self) -> None:
        config = {
            "configuration": {
                "apiKey": "wk",
                "model": "wispr-flow",
                "endpoint": "https://w.example/",
                "language": "en",
                "appType": "custom",
            },
            "isDefault": True,
        }
        adapter = get_stt_model("wispr", config)
        assert isinstance(adapter, _WisprFlowSTTAdapter)
        assert adapter._endpoint == "https://w.example"
        assert adapter._default_app_type == "custom"

    def test_gemini_dispatch(self) -> None:
        config = {
            "configuration": {
                "apiKey": "gk",
                "model": "gemini-1.5-flash",
                "endpoint": None,
            },
            "isDefault": True,
        }
        adapter = get_stt_model("gemini", config)
        assert isinstance(adapter, _GeminiSTTAdapter)
        assert adapter.model == "gemini-1.5-flash"

    def test_empty_model_raises(self) -> None:
        config = {
            "configuration": {"apiKey": "sk", "model": ""},
            "isDefault": True,
        }
        with pytest.raises(ValueError, match="No STT model configured"):
            get_stt_model("openAI", config)

    def test_model_name_not_in_list_raises(self) -> None:
        config = {
            "configuration": {"apiKey": "sk", "model": "whisper-1, whisper-2"},
            "isDefault": False,
        }
        with pytest.raises(ValueError, match="not found in"):
            get_stt_model("openAI", config, model_name="nope")

    def test_openai_stt_uses_named_model_in_list(self) -> None:
        config = {
            "configuration": {"apiKey": "sk", "model": "whisper-1, whisper-2"},
            "isDefault": False,
        }
        adapter = get_stt_model("openAI", config, model_name="whisper-2")
        assert isinstance(adapter, _OpenAISTTAdapter)
        assert adapter.model == "whisper-2"


# ---------------------------------------------------------------------------
# _GeminiTTSAdapter.synthesize
# ---------------------------------------------------------------------------


def _gemini_tts_success_response(inline_b64: str) -> SimpleNamespace:
    return _http_response(
        status_code=200,
        json_payload={
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"inlineData": {"data": inline_b64}},
                        ]
                    }
                }
            ]
        },
    )


class TestGeminiTTSSynthesize:
    @pytest.mark.asyncio
    async def test_wav_wraps_pcm(self) -> None:
        pcm = b"\x01\x00\x02\x00\x03\x00\x04\x00"
        response = _gemini_tts_success_response(base64.b64encode(pcm).decode("ascii"))
        factory, holder = _fake_client_factory(response)

        adapter = _GeminiTTSAdapter(model="m", api_key="k")
        with patch("httpx.AsyncClient", side_effect=factory):
            out = await adapter.synthesize("hello", response_format="wav", voice="Puck")

        assert out.startswith(b"RIFF")
        assert b"WAVE" in out
        assert out.endswith(pcm)

        sent = holder()
        assert sent is not None
        assert sent.calls[0]["url"].endswith(":generateContent")
        assert sent.calls[0]["headers"]["x-goog-api-key"] == "k"
        body = sent.calls[0]["json"]
        assert body["contents"][0]["parts"][0]["text"] == "hello"
        voice_cfg = body["generationConfig"]["speechConfig"]["voiceConfig"]
        assert voice_cfg["prebuiltVoiceConfig"]["voiceName"] == "Puck"

    @pytest.mark.asyncio
    async def test_pcm_returns_raw_bytes(self) -> None:
        pcm = b"\xaa" * 16
        response = _gemini_tts_success_response(base64.b64encode(pcm).decode("ascii"))
        factory, _ = _fake_client_factory(response)

        adapter = _GeminiTTSAdapter(model="m", api_key="k")
        with patch("httpx.AsyncClient", side_effect=factory):
            out = await adapter.synthesize("hi", response_format="pcm")
        assert out == pcm

    @pytest.mark.asyncio
    async def test_unknown_format_falls_back_to_wav(self) -> None:
        pcm = b"\x10\x00\x20\x00"
        response = _gemini_tts_success_response(base64.b64encode(pcm).decode("ascii"))
        factory, _ = _fake_client_factory(response)

        adapter = _GeminiTTSAdapter(model="m", api_key="k")
        with patch("httpx.AsyncClient", side_effect=factory):
            out = await adapter.synthesize("hi", response_format="bogus")
        assert out.startswith(b"RIFF")

    @pytest.mark.asyncio
    async def test_non_200_surfaces_error_message(self) -> None:
        response = _http_response(
            status_code=429,
            json_payload={"error": {"message": "quota exceeded"}},
            text="quota exceeded body",
        )
        factory, _ = _fake_client_factory(response)
        adapter = _GeminiTTSAdapter(model="m", api_key="k")
        with patch("httpx.AsyncClient", side_effect=factory):
            with pytest.raises(RuntimeError, match="quota exceeded"):
                await adapter.synthesize("x")

    @pytest.mark.asyncio
    async def test_missing_inline_data_raises(self) -> None:
        response = _http_response(
            status_code=200,
            json_payload={"candidates": [{"content": {"parts": [{"text": "no audio"}]}}]},
        )
        factory, _ = _fake_client_factory(response)
        adapter = _GeminiTTSAdapter(model="m", api_key="k")
        with patch("httpx.AsyncClient", side_effect=factory):
            with pytest.raises(RuntimeError, match="inline audio data"):
                await adapter.synthesize("x")

    @pytest.mark.asyncio
    async def test_non_json_body_raises(self) -> None:
        response = _http_response(status_code=200, json_raises=True, text="<html/>")
        factory, _ = _fake_client_factory(response)
        adapter = _GeminiTTSAdapter(model="m", api_key="k")
        with patch("httpx.AsyncClient", side_effect=factory):
            with pytest.raises(RuntimeError, match="non-JSON response"):
                await adapter.synthesize("x")

    @pytest.mark.asyncio
    async def test_mp3_reencodes_via_ffmpeg(self) -> None:
        pcm = b"\x01\x00\x02\x00\x03\x00"
        response = _gemini_tts_success_response(base64.b64encode(pcm).decode("ascii"))
        factory, _ = _fake_client_factory(response)

        fake_process = SimpleNamespace(
            communicate=AsyncMock(return_value=(b"mp3-bytes", b"")),
            returncode=0,
        )
        adapter = _GeminiTTSAdapter(model="m", api_key="k")
        with patch("httpx.AsyncClient", side_effect=factory), patch(
            "asyncio.create_subprocess_exec", AsyncMock(return_value=fake_process)
        ):
            out = await adapter.synthesize("hi", response_format="mp3")

        assert out == b"mp3-bytes"
        fake_process.communicate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ffmpeg_missing_raises(self) -> None:
        pcm = b"\x01\x00"
        response = _gemini_tts_success_response(base64.b64encode(pcm).decode("ascii"))
        factory, _ = _fake_client_factory(response)

        adapter = _GeminiTTSAdapter(model="m", api_key="k")
        with patch("httpx.AsyncClient", side_effect=factory), patch(
            "asyncio.create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError())
        ):
            with pytest.raises(RuntimeError, match="requires ffmpeg"):
                await adapter.synthesize("hi", response_format="mp3")


class TestReencodePcmViaFfmpeg:
    @pytest.mark.asyncio
    async def test_ffmpeg_nonzero_exit_raises(self) -> None:
        fake_process = SimpleNamespace(
            communicate=AsyncMock(return_value=(b"", b"boom")),
            returncode=1,
        )
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_process)):
            with pytest.raises(RuntimeError, match="ffmpeg failed to encode"):
                await _reencode_pcm_via_ffmpeg(b"pcm", target_format="mp3")

    @pytest.mark.asyncio
    async def test_ffmpeg_empty_output_raises(self) -> None:
        fake_process = SimpleNamespace(
            communicate=AsyncMock(return_value=(b"", b"")),
            returncode=0,
        )
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_process)):
            with pytest.raises(RuntimeError, match="produced no output"):
                await _reencode_pcm_via_ffmpeg(b"pcm", target_format="mp3")


# ---------------------------------------------------------------------------
# _GeminiSTTAdapter.transcribe
# ---------------------------------------------------------------------------


class TestGeminiSTTTranscribe:
    @pytest.mark.asyncio
    async def test_success_concatenates_text_parts(self) -> None:
        response = _http_response(
            status_code=200,
            json_payload={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "hello "},
                                {"text": "world"},
                                {"inlineData": {"data": "xxx"}},
                            ]
                        }
                    }
                ]
            },
        )
        factory, holder = _fake_client_factory(response)
        adapter = _GeminiSTTAdapter(model="gemini-1.5-flash", api_key="gk")
        with patch("httpx.AsyncClient", side_effect=factory):
            out = await adapter.transcribe(
                b"audio-bytes", mime="audio/webm;codecs=opus", language="fr"
            )

        assert out == "hello world"
        body = holder().calls[0]["json"]
        # Language hint must be appended to the prompt.
        assert "fr" in body["contents"][0]["parts"][0]["text"]
        # Mime codec param is stripped.
        assert body["contents"][0]["parts"][1]["inlineData"]["mimeType"] == "audio/webm"

    @pytest.mark.asyncio
    async def test_octet_stream_filename_sets_inline_mime(self) -> None:
        response = _http_response(
            status_code=200,
            json_payload={
                "candidates": [{"content": {"parts": [{"text": "hi"}]}}],
            },
        )
        factory, holder = _fake_client_factory(response)
        adapter = _GeminiSTTAdapter(model="m", api_key="k")
        with patch("httpx.AsyncClient", side_effect=factory):
            await adapter.transcribe(
                b"a",
                mime="application/octet-stream",
                filename="recording.webm",
            )
        body = holder().calls[0]["json"]
        assert body["contents"][0]["parts"][1]["inlineData"]["mimeType"] == "audio/webm"

    @pytest.mark.asyncio
    async def test_no_candidates_returns_empty_string(self) -> None:
        response = _http_response(status_code=200, json_payload={"candidates": []})
        factory, _ = _fake_client_factory(response)
        adapter = _GeminiSTTAdapter(model="m", api_key="k")
        with patch("httpx.AsyncClient", side_effect=factory):
            assert await adapter.transcribe(b"x") == ""

    @pytest.mark.asyncio
    async def test_payload_not_dict_returns_empty_string(self) -> None:
        response = _http_response(status_code=200, json_payload=["not", "a", "dict"])
        factory, _ = _fake_client_factory(response)
        adapter = _GeminiSTTAdapter(model="m", api_key="k")
        with patch("httpx.AsyncClient", side_effect=factory):
            assert await adapter.transcribe(b"x") == ""

    @pytest.mark.asyncio
    async def test_non_200_surfaces_error(self) -> None:
        response = _http_response(
            status_code=500,
            json_payload={"error": {"message": "server exploded"}},
            text="server exploded",
        )
        factory, _ = _fake_client_factory(response)
        adapter = _GeminiSTTAdapter(model="m", api_key="k")
        with patch("httpx.AsyncClient", side_effect=factory):
            with pytest.raises(RuntimeError, match="server exploded"):
                await adapter.transcribe(b"x")

    @pytest.mark.asyncio
    async def test_non_json_body_raises(self) -> None:
        response = _http_response(status_code=200, json_raises=True, text="<html/>")
        factory, _ = _fake_client_factory(response)
        adapter = _GeminiSTTAdapter(model="m", api_key="k")
        with patch("httpx.AsyncClient", side_effect=factory):
            with pytest.raises(RuntimeError, match="non-JSON response"):
                await adapter.transcribe(b"x")


# ---------------------------------------------------------------------------
# _WisprFlowSTTAdapter.transcribe + _transcode_to_wispr_wav
# ---------------------------------------------------------------------------


class TestWisprFlowSTT:
    @pytest.mark.asyncio
    async def test_success_returns_transcript(self) -> None:
        response = _http_response(status_code=200, json_payload={"text": "hi there"})
        factory, holder = _fake_client_factory(response)
        fake_wav = b"RIFFxxxxWAVExxxx"
        adapter = _WisprFlowSTTAdapter(
            model="flow", api_key="wk", default_language="en"
        )

        with patch(
            "app.utils.aimodels._transcode_to_wispr_wav",
            new=AsyncMock(return_value=fake_wav),
        ), patch("httpx.AsyncClient", side_effect=factory):
            out = await adapter.transcribe(b"raw-audio", mime="audio/webm")

        assert out == "hi there"
        body = holder().calls[0]["json"]
        assert body["audio"] == base64.b64encode(fake_wav).decode("ascii")
        assert body["properties"]["language"] == "en"
        assert body["properties"]["app_type"] == "ai"

    @pytest.mark.asyncio
    async def test_oversized_wav_raises(self) -> None:
        oversized = b"\x00" * (_WISPR_MAX_WAV_BYTES + 1)
        adapter = _WisprFlowSTTAdapter(model="flow", api_key="wk")
        with patch(
            "app.utils.aimodels._transcode_to_wispr_wav",
            new=AsyncMock(return_value=oversized),
        ):
            with pytest.raises(RuntimeError, match="exceeds"):
                await adapter.transcribe(b"x")

    @pytest.mark.asyncio
    async def test_http_error_with_detail_message(self) -> None:
        response = _http_response(
            status_code=401,
            json_payload={"detail": "bad key"},
            text="bad key body",
        )
        factory, _ = _fake_client_factory(response)
        adapter = _WisprFlowSTTAdapter(model="flow", api_key="wk")
        with patch(
            "app.utils.aimodels._transcode_to_wispr_wav",
            new=AsyncMock(return_value=b"RIFFxxxx"),
        ), patch("httpx.AsyncClient", side_effect=factory):
            with pytest.raises(RuntimeError, match="bad key"):
                await adapter.transcribe(b"x")

    @pytest.mark.asyncio
    async def test_non_dict_response_returns_empty_string(self) -> None:
        response = _http_response(status_code=200, json_payload=["not", "a", "dict"])
        factory, _ = _fake_client_factory(response)
        adapter = _WisprFlowSTTAdapter(model="flow", api_key="wk")
        with patch(
            "app.utils.aimodels._transcode_to_wispr_wav",
            new=AsyncMock(return_value=b"RIFFxxxx"),
        ), patch("httpx.AsyncClient", side_effect=factory):
            assert await adapter.transcribe(b"x") == ""

    @pytest.mark.asyncio
    async def test_non_json_response_raises(self) -> None:
        response = _http_response(status_code=200, json_raises=True, text="<html/>")
        factory, _ = _fake_client_factory(response)
        adapter = _WisprFlowSTTAdapter(model="flow", api_key="wk")
        with patch(
            "app.utils.aimodels._transcode_to_wispr_wav",
            new=AsyncMock(return_value=b"RIFFxxxx"),
        ), patch("httpx.AsyncClient", side_effect=factory):
            with pytest.raises(RuntimeError, match="non-JSON response"):
                await adapter.transcribe(b"x")


class TestTranscodeToWisprWav:
    @pytest.mark.asyncio
    async def test_ffmpeg_not_found_raises(self) -> None:
        with patch(
            "asyncio.create_subprocess_exec",
            AsyncMock(side_effect=FileNotFoundError()),
        ):
            with pytest.raises(RuntimeError, match="requires ffmpeg"):
                await _transcode_to_wispr_wav(b"audio")

    @pytest.mark.asyncio
    async def test_ffmpeg_nonzero_exit_raises(self) -> None:
        fake_process = SimpleNamespace(
            communicate=AsyncMock(return_value=(b"", b"boom")),
            returncode=1,
        )
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_process)):
            with pytest.raises(RuntimeError, match="ffmpeg failed to transcode"):
                await _transcode_to_wispr_wav(b"audio")

    @pytest.mark.asyncio
    async def test_ffmpeg_empty_output_raises(self) -> None:
        fake_process = SimpleNamespace(
            communicate=AsyncMock(return_value=(b"", b"")),
            returncode=0,
        )
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_process)):
            with pytest.raises(RuntimeError, match="produced no output"):
                await _transcode_to_wispr_wav(b"audio")

    @pytest.mark.asyncio
    async def test_ffmpeg_success_returns_stdout(self) -> None:
        fake_process = SimpleNamespace(
            communicate=AsyncMock(return_value=(b"RIFFxxxxWAVE", b"")),
            returncode=0,
        )
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_process)):
            out = await _transcode_to_wispr_wav(b"audio")
        assert out == b"RIFFxxxxWAVE"


# ---------------------------------------------------------------------------
# _WhisperLocalSTTAdapter
# ---------------------------------------------------------------------------


@pytest.fixture
def _clear_whisper_cache():
    aimodels._WHISPER_MODEL_CACHE.clear()
    yield
    aimodels._WHISPER_MODEL_CACHE.clear()


def _install_fake_faster_whisper(calls: list[int]) -> MagicMock:
    """Inject a fake ``faster_whisper`` module into ``sys.modules``.

    ``calls`` is mutated so tests can assert that ``WhisperModel`` was only
    constructed the expected number of times.
    """

    fake_segments = [SimpleNamespace(text="foo "), SimpleNamespace(text="bar")]
    model_instance = MagicMock()
    model_instance.transcribe = MagicMock(return_value=(iter(fake_segments), SimpleNamespace()))

    def _ctor(*args: Any, **kwargs: Any) -> MagicMock:
        calls.append(1)
        return model_instance

    fake_module = MagicMock()
    fake_module.WhisperModel = _ctor
    sys.modules["faster_whisper"] = fake_module
    return model_instance


class TestWhisperLocalSTT:
    @pytest.mark.asyncio
    async def test_transcribe_concatenates_segments(
        self, _clear_whisper_cache
    ) -> None:
        calls: list[int] = []
        _install_fake_faster_whisper(calls)
        try:
            adapter = _WhisperLocalSTTAdapter(
                model="base", device="cpu", compute_type="int8"
            )
            out = await adapter.transcribe(b"audio", language="en")
        finally:
            sys.modules.pop("faster_whisper", None)

        assert out == "foo bar"
        assert calls == [1]

    @pytest.mark.asyncio
    async def test_model_cache_reused_across_calls(
        self, _clear_whisper_cache
    ) -> None:
        calls: list[int] = []
        _install_fake_faster_whisper(calls)
        try:
            adapter = _WhisperLocalSTTAdapter(
                model="base", device="cpu", compute_type="int8"
            )
            await adapter.transcribe(b"a")
            # Rebuild iterator for the second call — the MagicMock's
            # ``transcribe`` returns a fresh iterator each time because we
            # wrap it below.
            fake_segments = [SimpleNamespace(text="foo "), SimpleNamespace(text="bar")]
            cached_model = aimodels._WHISPER_MODEL_CACHE[
                ("base", "cpu", "int8", None)
            ]
            cached_model.transcribe = MagicMock(
                return_value=(iter(fake_segments), SimpleNamespace())
            )
            await adapter.transcribe(b"b")
        finally:
            sys.modules.pop("faster_whisper", None)

        # Only the first call constructed a WhisperModel; the second hit the
        # process-level cache.
        assert calls == [1]

    @pytest.mark.asyncio
    async def test_missing_faster_whisper_raises(
        self, _clear_whisper_cache
    ) -> None:
        sys.modules.pop("faster_whisper", None)
        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "faster_whisper":
                raise ImportError("no faster_whisper")
            return real_import(name, globals, locals, fromlist, level)

        adapter = _WhisperLocalSTTAdapter(
            model="base", device="cpu", compute_type="int8"
        )
        with patch("builtins.__import__", side_effect=fake_import):
            with pytest.raises(RuntimeError, match="faster-whisper"):
                await adapter.transcribe(b"audio")
