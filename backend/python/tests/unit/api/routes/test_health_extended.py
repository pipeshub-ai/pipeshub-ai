"""Extended tests for health.py covering uncovered lines:
- web_search_health_check (lines 106-184)
- perform_tts_health_check (lines 914-1014)
- perform_stt_health_check (lines 1040-1186)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import JSONResponse

from app.web.errors import (
    AuthFailed,
    Blocked,
    NotConfigured,
    QuotaExceeded,
    RateLimited,
    TimedOut,
    Unreachable,
    UpstreamError,
)
from app.web.fetch.http import FetchedBody

MODULE = "app.api.routes.health"


@pytest.fixture
def mock_request():
    req = MagicMock()
    app = MagicMock()
    container = MagicMock()
    container.logger.return_value = MagicMock()
    app.container = container
    req.app = app
    return req


# ============================================================================
# web_search_health_check
# ============================================================================


class TestWebSearchHealthCheck:
    """The check runs one search with the provider alone; its network answer is stubbed."""

    @staticmethod
    async def _check(mock_request, provider_config: dict, outcome: object = None) -> tuple[JSONResponse, AsyncMock]:
        from app.api.routes.health import web_search_health_check

        check = AsyncMock(side_effect=outcome) if isinstance(outcome, BaseException) else AsyncMock(return_value=None)
        with patch(f"{MODULE}.check_provider", check):
            resp = await web_search_health_check(mock_request, provider_config)
        return resp, check

    @pytest.mark.asyncio
    async def test_success(self, mock_request):
        resp, check = await self._check(mock_request, {"provider": "serper", "configuration": {"apiKey": "k"}})
        assert resp.status_code == 200
        assert "serper" in resp.body.decode()
        assert check.call_args.args[:2] == ("serper", {"apiKey": "k"})

    @pytest.mark.asyncio
    async def test_default_provider_is_duckduckgo(self, mock_request):
        resp, check = await self._check(mock_request, {"configuration": {}})
        assert resp.status_code == 200
        assert check.call_args.args[0] == "duckduckgo"

    @pytest.mark.asyncio
    async def test_unknown_provider_returns_400(self, mock_request):
        from app.api.routes.health import web_search_health_check

        resp = await web_search_health_check(mock_request, {"provider": "unknown_engine", "configuration": {}})
        assert resp.status_code == 400
        assert "isn't a web search provider" in resp.body.decode()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("error", "status", "text"),
        [
            (TimedOut("t"), 408, "didn't answer in time"),
            (NotConfigured("boom"), 400, "Serper needs an API key"),
            (NotConfigured("boom", reason="base_url"), 400, "needs the http(s) address"),
            (NotConfigured("boom", reason="searxng_json"), 400, "add json to search.formats"),
            (NotConfigured("boom", reason="server_version"), 400, "0.9.4 or later is required"),
            (AuthFailed("x", status=401), 400, "didn't accept the API key"),
            (RateLimited("x", status=429), 400, "limiting requests"),
            (QuotaExceeded("x", status=402), 400, "credits are used up"),
            (Blocked("x"), 400, "blocked the test search"),
            (UpstreamError("x", status=500), 400, "returned an error (500)"),
            (Unreachable("x"), 500, "Couldn't reach"),
            (RuntimeError("boom"), 500, "Couldn't reach"),
        ],
    )
    async def test_failures(self, mock_request, error, status, text):
        resp, _ = await self._check(mock_request, {"provider": "serper", "configuration": {"apiKey": "k"}}, error)
        assert resp.status_code == status
        body = resp.body.decode()
        assert text in body
        assert "boom" not in body  # exception text never reaches the response

    @pytest.mark.asyncio
    async def test_blocked_duckduckgo_is_not_reported_healthy(self, mock_request):
        """A blocked DuckDuckGo used to look healthy because it returned no results."""
        from app.api.routes.health import web_search_health_check

        blocked = FetchedBody("https://html.duckduckgo.com/html/", 202, "text/html", b"<div class=anomaly-modal></div>")
        with patch("app.web.search.duckduckgo_html.fetch_public", AsyncMock(return_value=blocked)):
            resp = await web_search_health_check(mock_request, {"provider": "duckduckgo", "configuration": {}})
        assert resp.status_code == 400
        assert "blocked" in resp.body.decode()


# ============================================================================
# perform_tts_health_check
# ============================================================================


class TestPerformTtsHealthCheck:
    def _cfg(self, provider: str, model: str = "tts-1", api_key: str = "sk-test") -> dict:
        return {
            "provider": provider,
            "configuration": {"model": model, "apiKey": api_key},
        }

    @pytest.mark.asyncio
    async def test_no_model_names_returns_500(self):
        logger = MagicMock()
        from app.api.routes.health import perform_tts_health_check

        resp = await perform_tts_health_check(
            {"provider": "openAI", "configuration": {"model": "  ", "apiKey": "sk"}},
            logger,
        )
        assert resp.status_code == 500
        body = resp.body.decode()
        assert "No valid model names" in body

    @pytest.mark.asyncio
    async def test_adapter_build_failure_returns_500(self):
        logger = MagicMock()
        with patch(f"{MODULE}.get_tts_model", side_effect=ValueError("bad cfg")):
            from app.api.routes.health import perform_tts_health_check

            resp = await perform_tts_health_check(self._cfg("openAI"), logger)
        assert resp.status_code == 500
        body = resp.body.decode()
        assert "with these speech model settings" in body

    @pytest.mark.asyncio
    async def test_openai_success(self):
        logger = MagicMock()
        mock_adapter = MagicMock()
        mock_client = AsyncMock()
        mock_client.models.list = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        with patch(f"{MODULE}.get_tts_model", return_value=mock_adapter), \
             patch("openai.AsyncOpenAI", return_value=mock_client), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[]):
            from app.api.routes.health import perform_tts_health_check

            resp = await perform_tts_health_check(self._cfg("openAI"), logger)
        assert resp.status_code == 200
        body = resp.body.decode()
        assert "healthy" in body

    @pytest.mark.asyncio
    async def test_openai_probe_failure_returns_500(self):
        logger = MagicMock()
        mock_adapter = MagicMock()
        with patch(f"{MODULE}.get_tts_model", return_value=mock_adapter), \
             patch("asyncio.wait_for", new_callable=AsyncMock, side_effect=RuntimeError("net error")):
            from app.api.routes.health import perform_tts_health_check

            resp = await perform_tts_health_check(self._cfg("openAI"), logger)
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_gemini_success(self):
        logger = MagicMock()
        mock_adapter = MagicMock()
        mock_client = MagicMock()
        mock_client.aio.models.get = AsyncMock(return_value=MagicMock())
        with patch(f"{MODULE}.get_tts_model", return_value=mock_adapter), \
             patch("google.genai.Client", return_value=mock_client) as client_cls:
            from app.api.routes.health import perform_tts_health_check

            resp = await perform_tts_health_check(self._cfg("gemini", model="gemini-tts"), logger)
        assert resp.status_code == 200
        body = resp.body.decode()
        assert "healthy" in body
        client_cls.assert_called_once_with(api_key="sk-test")
        mock_client.aio.models.get.assert_awaited_once_with(model="gemini-tts")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("code", "status", "error_code"),
        [(401, "UNAUTHENTICATED", "auth_error"), (404, "NOT_FOUND", "model_not_found")],
    )
    async def test_gemini_rejected_settings_fail_the_check(self, code, status, error_code):
        import json

        from google.genai import errors

        logger = MagicMock()
        mock_client = MagicMock()
        mock_client.aio.models.get = AsyncMock(
            side_effect=errors.ClientError(code, {"error": {"code": code, "message": "rejected", "status": status}})
        )
        with patch(f"{MODULE}.get_tts_model", return_value=MagicMock()), \
             patch("google.genai.Client", return_value=mock_client):
            from app.api.routes.health import perform_tts_health_check

            resp = await perform_tts_health_check(self._cfg("gemini", model="gemini-tts"), logger)
        assert resp.status_code == 400
        assert json.loads(resp.body)["details"]["error_code"] == error_code

    @pytest.mark.asyncio
    async def test_openrouter_success(self):
        logger = MagicMock()
        mock_adapter = MagicMock()

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch(f"{MODULE}.get_tts_model", return_value=mock_adapter), \
             patch("httpx.AsyncClient", return_value=mock_http):
            from app.api.routes.health import perform_tts_health_check

            resp = await perform_tts_health_check(
                self._cfg("openRouter", model="tts-model"), logger
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_openrouter_bad_key_is_a_settings_error(self):
        logger = MagicMock()
        mock_adapter = MagicMock()

        mock_response = MagicMock()
        mock_response.status_code = 401

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch(f"{MODULE}.get_tts_model", return_value=mock_adapter), \
             patch("httpx.AsyncClient", return_value=mock_http):
            from app.api.routes.health import perform_tts_health_check

            resp = await perform_tts_health_check(
                self._cfg("openRouter", model="tts-model"), logger
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_litellm_proxy_success(self):
        logger = MagicMock()
        mock_adapter = MagicMock()

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.get = AsyncMock(return_value=mock_response)

        cfg = {
            "provider": "litellmProxy",
            "configuration": {"model": "tts-1", "endpoint": "http://proxy:8000", "apiKey": "sk"},
        }
        with patch(f"{MODULE}.get_tts_model", return_value=mock_adapter), \
             patch("httpx.AsyncClient", return_value=mock_http):
            from app.api.routes.health import perform_tts_health_check

            resp = await perform_tts_health_check(cfg, logger)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_litellm_proxy_bad_status_returns_500(self):
        logger = MagicMock()
        mock_adapter = MagicMock()

        mock_response = MagicMock()
        mock_response.status_code = 503

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.get = AsyncMock(return_value=mock_response)

        cfg = {
            "provider": "litellmProxy",
            "configuration": {"model": "tts-1", "endpoint": "http://proxy:8000"},
        }
        with patch(f"{MODULE}.get_tts_model", return_value=mock_adapter), \
             patch("httpx.AsyncClient", return_value=mock_http):
            from app.api.routes.health import perform_tts_health_check

            resp = await perform_tts_health_check(cfg, logger)
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_unsupported_provider_returns_400(self):
        logger = MagicMock()
        mock_adapter = MagicMock()
        with patch(f"{MODULE}.get_tts_model", return_value=mock_adapter):
            from app.api.routes.health import perform_tts_health_check

            resp = await perform_tts_health_check(
                self._cfg("unknownTTSProvider"), logger
            )
        assert resp.status_code == 400
        body = resp.body.decode()
        assert "Unsupported TTS provider" in body

    @pytest.mark.asyncio
    async def test_generic_exception_returns_500(self):
        logger = MagicMock()
        mock_adapter = MagicMock()
        with patch(f"{MODULE}.get_tts_model", return_value=mock_adapter), \
             patch("asyncio.wait_for", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            from app.api.routes.health import perform_tts_health_check

            resp = await perform_tts_health_check(self._cfg("openAI"), logger)
        assert resp.status_code == 500
        body = resp.body.decode()
        assert "with these speech model settings" in body


# ============================================================================
# perform_stt_health_check
# ============================================================================


class TestPerformSttHealthCheck:
    def _cfg(self, provider: str, model: str = "whisper-1", api_key: str = "sk-test") -> dict:
        return {
            "provider": provider,
            "configuration": {"model": model, "apiKey": api_key},
        }

    @pytest.mark.asyncio
    async def test_no_model_names_returns_500(self):
        logger = MagicMock()
        from app.api.routes.health import perform_stt_health_check

        resp = await perform_stt_health_check(
            {"provider": "openAI", "configuration": {"model": "  ", "apiKey": "sk"}},
            logger,
        )
        assert resp.status_code == 500
        body = resp.body.decode()
        assert "No valid model names" in body

    @pytest.mark.asyncio
    async def test_adapter_build_failure_returns_500(self):
        logger = MagicMock()
        with patch(f"{MODULE}.get_stt_model", side_effect=ValueError("bad cfg")):
            from app.api.routes.health import perform_stt_health_check

            resp = await perform_stt_health_check(self._cfg("openAI"), logger)
        assert resp.status_code == 500
        body = resp.body.decode()
        assert "with these speech-to-text model settings" in body

    @pytest.mark.asyncio
    async def test_openai_success(self):
        logger = MagicMock()
        mock_adapter = MagicMock()
        mock_client = AsyncMock()
        mock_client.models.list = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        with patch(f"{MODULE}.get_stt_model", return_value=mock_adapter), \
             patch("openai.AsyncOpenAI", return_value=mock_client), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=[]):
            from app.api.routes.health import perform_stt_health_check

            resp = await perform_stt_health_check(self._cfg("openAI"), logger)
        assert resp.status_code == 200
        body = resp.body.decode()
        assert "healthy" in body

    @pytest.mark.asyncio
    async def test_openai_probe_failure_returns_500(self):
        logger = MagicMock()
        mock_adapter = MagicMock()
        with patch(f"{MODULE}.get_stt_model", return_value=mock_adapter), \
             patch("asyncio.wait_for", new_callable=AsyncMock, side_effect=RuntimeError("net error")):
            from app.api.routes.health import perform_stt_health_check

            resp = await perform_stt_health_check(self._cfg("openAI"), logger)
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_whisper_with_faster_whisper_installed(self):
        logger = MagicMock()
        mock_adapter = MagicMock()
        mock_spec = MagicMock()

        with patch(f"{MODULE}.get_stt_model", return_value=mock_adapter), \
             patch("importlib.util.find_spec", return_value=mock_spec):
            from app.api.routes.health import perform_stt_health_check

            resp = await perform_stt_health_check(self._cfg("whisper", model="base"), logger)
        assert resp.status_code == 200
        body = resp.body.decode()
        assert "healthy" in body

    @pytest.mark.asyncio
    async def test_whisper_without_faster_whisper_returns_500(self):
        logger = MagicMock()
        mock_adapter = MagicMock()

        with patch(f"{MODULE}.get_stt_model", return_value=mock_adapter), \
             patch("importlib.util.find_spec", return_value=None):
            from app.api.routes.health import perform_stt_health_check

            resp = await perform_stt_health_check(self._cfg("whisper", model="base"), logger)
        assert resp.status_code == 500
        body = resp.body.decode()
        assert "faster-whisper" in body

    @pytest.mark.asyncio
    async def test_whisper_probe_error_is_not_echoed(self):
        import json

        logger = MagicMock()
        error = RuntimeError("SENTINEL /opt/venv/lib/python3.12/site-packages unreadable")

        with patch(f"{MODULE}.get_stt_model", return_value=MagicMock()), \
             patch("importlib.util.find_spec", side_effect=error):
            from app.api.routes.health import perform_stt_health_check

            resp = await perform_stt_health_check(self._cfg("whisper", model="base"), logger)
        assert resp.status_code == 500
        body = json.loads(resp.body)
        assert body["status"] == "error"
        assert body["message"] == (
            "Couldn't check the local Whisper install. Reinstall the service's dependencies, then try again."
        )
        assert "SENTINEL" not in resp.body.decode()
        log_call = logger.error.call_args
        assert log_call.kwargs["exc_info"] is True
        assert error in log_call.args

    @pytest.mark.asyncio
    async def test_gemini_success(self):
        logger = MagicMock()
        mock_adapter = MagicMock()
        mock_client = MagicMock()
        mock_client.aio.models.get = AsyncMock(return_value=MagicMock())

        with patch(f"{MODULE}.get_stt_model", return_value=mock_adapter), \
             patch("google.genai.Client", return_value=mock_client), \
             patch("asyncio.wait_for", new_callable=AsyncMock, return_value=MagicMock()):
            from app.api.routes.health import perform_stt_health_check

            resp = await perform_stt_health_check(
                self._cfg("gemini", model="gemini-stt"), logger
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_wispr_with_ffmpeg_success(self):
        logger = MagicMock()
        mock_adapter = MagicMock()

        with patch(f"{MODULE}.get_stt_model", return_value=mock_adapter), \
             patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            from app.api.routes.health import perform_stt_health_check

            resp = await perform_stt_health_check(self._cfg("wispr", model="wispr-1"), logger)
        assert resp.status_code == 200
        body = resp.body.decode()
        assert "healthy" in body

    @pytest.mark.asyncio
    async def test_wispr_without_ffmpeg_returns_500(self):
        logger = MagicMock()
        mock_adapter = MagicMock()

        with patch(f"{MODULE}.get_stt_model", return_value=mock_adapter), \
             patch("shutil.which", return_value=None):
            from app.api.routes.health import perform_stt_health_check

            resp = await perform_stt_health_check(self._cfg("wispr", model="wispr-1"), logger)
        assert resp.status_code == 500
        body = resp.body.decode()
        assert "ffmpeg" in body

    @pytest.mark.asyncio
    async def test_openrouter_success(self):
        logger = MagicMock()
        mock_adapter = MagicMock()

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch(f"{MODULE}.get_stt_model", return_value=mock_adapter), \
             patch("httpx.AsyncClient", return_value=mock_http):
            from app.api.routes.health import perform_stt_health_check

            resp = await perform_stt_health_check(
                self._cfg("openRouter", model="stt-model"), logger
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_openrouter_bad_key_is_a_settings_error(self):
        logger = MagicMock()
        mock_adapter = MagicMock()

        mock_response = MagicMock()
        mock_response.status_code = 401

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch(f"{MODULE}.get_stt_model", return_value=mock_adapter), \
             patch("httpx.AsyncClient", return_value=mock_http):
            from app.api.routes.health import perform_stt_health_check

            resp = await perform_stt_health_check(
                self._cfg("openRouter", model="stt-model"), logger
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_litellm_proxy_success(self):
        logger = MagicMock()
        mock_adapter = MagicMock()

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.get = AsyncMock(return_value=mock_response)

        cfg = {
            "provider": "litellmProxy",
            "configuration": {"model": "whisper-1", "endpoint": "http://proxy:8000", "apiKey": "sk"},
        }
        with patch(f"{MODULE}.get_stt_model", return_value=mock_adapter), \
             patch("httpx.AsyncClient", return_value=mock_http):
            from app.api.routes.health import perform_stt_health_check

            resp = await perform_stt_health_check(cfg, logger)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_litellm_proxy_bad_status_returns_500(self):
        logger = MagicMock()
        mock_adapter = MagicMock()

        mock_response = MagicMock()
        mock_response.status_code = 503

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=None)
        mock_http.get = AsyncMock(return_value=mock_response)

        cfg = {
            "provider": "litellmProxy",
            "configuration": {"model": "whisper-1", "endpoint": "http://proxy:8000"},
        }
        with patch(f"{MODULE}.get_stt_model", return_value=mock_adapter), \
             patch("httpx.AsyncClient", return_value=mock_http):
            from app.api.routes.health import perform_stt_health_check

            resp = await perform_stt_health_check(cfg, logger)
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_unsupported_provider_returns_400(self):
        logger = MagicMock()
        mock_adapter = MagicMock()
        with patch(f"{MODULE}.get_stt_model", return_value=mock_adapter):
            from app.api.routes.health import perform_stt_health_check

            resp = await perform_stt_health_check(
                self._cfg("unknownSTTProvider"), logger
            )
        assert resp.status_code == 400
        body = resp.body.decode()
        assert "Unsupported STT provider" in body

    @pytest.mark.asyncio
    async def test_generic_exception_returns_500(self):
        logger = MagicMock()
        mock_adapter = MagicMock()
        with patch(f"{MODULE}.get_stt_model", return_value=mock_adapter), \
             patch("asyncio.wait_for", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            from app.api.routes.health import perform_stt_health_check

            resp = await perform_stt_health_check(self._cfg("openAI"), logger)
        assert resp.status_code == 500
        body = resp.body.decode()
        assert "with these speech-to-text model settings" in body


# ============================================================================
# health_check endpoint dispatching to tts and stt
# ============================================================================


class TestHealthCheckEndpointTtsStt:
    @pytest.fixture
    def mock_request(self):
        req = MagicMock()
        app = MagicMock()
        container = MagicMock()
        container.logger.return_value = MagicMock()
        app.container = container
        req.app = app
        return req

    @pytest.mark.asyncio
    async def test_tts_type_dispatches(self, mock_request):
        from fastapi.responses import JSONResponse

        config = {"provider": "openAI", "configuration": {"model": "tts-1", "apiKey": "k"}}
        with patch(
            f"{MODULE}.perform_tts_health_check",
            new_callable=AsyncMock,
            return_value=JSONResponse(status_code=200, content={"status": "healthy"}),
        ):
            from app.api.routes.health import health_check

            resp = await health_check(mock_request, "tts", config)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_stt_type_dispatches(self, mock_request):
        from fastapi.responses import JSONResponse

        config = {"provider": "openAI", "configuration": {"model": "whisper-1", "apiKey": "k"}}
        with patch(
            f"{MODULE}.perform_stt_health_check",
            new_callable=AsyncMock,
            return_value=JSONResponse(status_code=200, content={"status": "healthy"}),
        ):
            from app.api.routes.health import health_check

            resp = await health_check(mock_request, "stt", config)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_image_generation_type_dispatches(self, mock_request):
        from fastapi.responses import JSONResponse

        config = {"provider": "openAI", "configuration": {"model": "dall-e-3", "apiKey": "k"}}
        with patch(
            f"{MODULE}.perform_image_generation_health_check",
            new_callable=AsyncMock,
            return_value=JSONResponse(status_code=200, content={"status": "healthy"}),
        ):
            from app.api.routes.health import health_check

            resp = await health_check(mock_request, "imageGeneration", config)
        assert resp.status_code == 200
