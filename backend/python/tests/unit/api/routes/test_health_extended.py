"""Extended tests for health.py covering uncovered lines:
- web_search_health_check (lines 106-184)
- perform_tts_health_check (lines 914-1014)
- perform_stt_health_check (lines 1040-1186)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

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
    @pytest.mark.asyncio
    async def test_success_duckduckgo(self, mock_request):
        mock_search = AsyncMock(return_value=[{"title": "result"}])
        with patch(
            f"{MODULE}._search_with_duckduckgo",
            create=True,
        ), patch(
            "app.utils.web_search_tool._search_with_duckduckgo",
            mock_search,
            create=True,
        ), patch(
            "asyncio.wait_for",
            new_callable=AsyncMock,
            return_value=[{"title": "result"}],
        ):
            from app.api.routes.health import web_search_health_check

            resp = await web_search_health_check(
                mock_request, {"provider": "duckduckgo", "configuration": {}}
            )
        assert resp.status_code == 200
        body = resp.body.decode()
        assert "healthy" in body
        assert "duckduckgo" in body

    @pytest.mark.asyncio
    async def test_unknown_provider_returns_400(self, mock_request):
        from app.api.routes.health import web_search_health_check

        resp = await web_search_health_check(
            mock_request, {"provider": "unknown_engine", "configuration": {}}
        )
        assert resp.status_code == 400
        body = resp.body.decode()
        assert "Unknown web search provider" in body

    @pytest.mark.asyncio
    async def test_timeout_returns_408(self, mock_request):
        with patch(
            "asyncio.wait_for",
            new_callable=AsyncMock,
            side_effect=asyncio.TimeoutError,
        ):
            from app.api.routes.health import web_search_health_check

            resp = await web_search_health_check(
                mock_request, {"provider": "serper", "configuration": {"apiKey": "k"}}
            )
        assert resp.status_code == 408
        body = resp.body.decode()
        assert "timed out" in body

    @pytest.mark.asyncio
    async def test_value_error_returns_400(self, mock_request):
        with patch(
            "asyncio.wait_for",
            new_callable=AsyncMock,
            side_effect=ValueError("Missing API key"),
        ):
            from app.api.routes.health import web_search_health_check

            resp = await web_search_health_check(
                mock_request, {"provider": "tavily", "configuration": {}}
            )
        assert resp.status_code == 400
        body = resp.body.decode()
        assert "Missing API key" in body

    @pytest.mark.asyncio
    async def test_http_status_401_returns_invalid_api_key(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.request = MagicMock()
        error = httpx.HTTPStatusError(
            "auth error", request=mock_response.request, response=mock_response
        )
        with patch(
            "asyncio.wait_for",
            new_callable=AsyncMock,
            side_effect=error,
        ):
            from app.api.routes.health import web_search_health_check

            resp = await web_search_health_check(
                mock_request, {"provider": "serper", "configuration": {"apiKey": "bad"}}
            )
        assert resp.status_code == 400
        body = resp.body.decode()
        assert "Invalid API key" in body

    @pytest.mark.asyncio
    async def test_http_status_403_returns_invalid_api_key(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.request = MagicMock()
        error = httpx.HTTPStatusError(
            "forbidden", request=mock_response.request, response=mock_response
        )
        with patch(
            "asyncio.wait_for",
            new_callable=AsyncMock,
            side_effect=error,
        ):
            from app.api.routes.health import web_search_health_check

            resp = await web_search_health_check(
                mock_request, {"provider": "exa", "configuration": {"apiKey": "bad"}}
            )
        assert resp.status_code == 400
        body = resp.body.decode()
        assert "Invalid API key" in body

    @pytest.mark.asyncio
    async def test_http_status_429_returns_rate_limit(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.request = MagicMock()
        error = httpx.HTTPStatusError(
            "rate limit", request=mock_response.request, response=mock_response
        )
        with patch(
            "asyncio.wait_for",
            new_callable=AsyncMock,
            side_effect=error,
        ):
            from app.api.routes.health import web_search_health_check

            resp = await web_search_health_check(
                mock_request, {"provider": "serper", "configuration": {"apiKey": "k"}}
            )
        assert resp.status_code == 400
        body = resp.body.decode()
        assert "Rate limit exceeded" in body

    @pytest.mark.asyncio
    async def test_http_status_500_returns_generic_http_message(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.request = MagicMock()
        error = httpx.HTTPStatusError(
            "server error", request=mock_response.request, response=mock_response
        )
        with patch(
            "asyncio.wait_for",
            new_callable=AsyncMock,
            side_effect=error,
        ):
            from app.api.routes.health import web_search_health_check

            resp = await web_search_health_check(
                mock_request, {"provider": "tavily", "configuration": {"apiKey": "k"}}
            )
        assert resp.status_code == 400
        body = resp.body.decode()
        assert "returned HTTP 500" in body

    @pytest.mark.asyncio
    async def test_generic_exception_returns_500(self, mock_request):
        with patch(
            "asyncio.wait_for",
            new_callable=AsyncMock,
            side_effect=RuntimeError("unexpected"),
        ):
            from app.api.routes.health import web_search_health_check

            resp = await web_search_health_check(
                mock_request, {"provider": "duckduckgo", "configuration": {}}
            )
        assert resp.status_code == 500
        body = resp.body.decode()
        assert "Web search health check failed" in body

    @pytest.mark.asyncio
    async def test_default_provider_is_duckduckgo(self, mock_request):
        with patch(
            "asyncio.wait_for",
            new_callable=AsyncMock,
            return_value=[],
        ):
            from app.api.routes.health import web_search_health_check

            resp = await web_search_health_check(
                mock_request, {"configuration": {}}
            )
        assert resp.status_code == 200
        body = resp.body.decode()
        assert "duckduckgo" in body


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
        assert "TTS health check failed" in body

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
        with patch(f"{MODULE}.get_tts_model", return_value=mock_adapter):
            from app.api.routes.health import perform_tts_health_check

            resp = await perform_tts_health_check(self._cfg("gemini", model="gemini-tts"), logger)
        assert resp.status_code == 200
        body = resp.body.decode()
        assert "healthy" in body

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
    async def test_openrouter_bad_key_returns_500(self):
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
        assert resp.status_code == 500

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
        assert "TTS health check failed" in body


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
        assert "STT health check failed" in body

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
    async def test_openrouter_bad_key_returns_500(self):
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
        assert resp.status_code == 500

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
        assert "STT health check failed" in body


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

        config = {"provider": "openAI", "configuration": {"model": "tts-1"}}
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

        config = {"provider": "openAI", "configuration": {"model": "whisper-1"}}
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

        config = {"provider": "openAI", "configuration": {"model": "dall-e-3"}}
        with patch(
            f"{MODULE}.perform_image_generation_health_check",
            new_callable=AsyncMock,
            return_value=JSONResponse(status_code=200, content={"status": "healthy"}),
        ):
            from app.api.routes.health import health_check

            resp = await health_check(mock_request, "imageGeneration", config)
        assert resp.status_code == 200
