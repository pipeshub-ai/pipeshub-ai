"""Unit tests for app.connectors.sources.web.fetch_strategy."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from app.connectors.sources.web.fetch_strategy import (
    FetchResponse,
    _BOT_DETECTION_CODES,
    _NON_RETRYABLE_CLIENT_ERRORS,
    _sync_cloudscraper_fetch,
    _sync_curl_cffi_fetch,
    _try_aiohttp,
    _try_cloudscraper,
    _try_curl_cffi,
    build_stealth_headers,
    fetch_url_with_fallback,
)


@pytest.fixture
def log():
    lg = logging.getLogger("test_fetch")
    lg.setLevel(logging.CRITICAL)
    return lg


# ============================================================================
# FetchResponse
# ============================================================================
class TestFetchResponse:
    def test_creation(self):
        resp = FetchResponse(
            status_code=200,
            content_bytes=b"hello",
            headers={"Content-Type": "text/html"},
            final_url="http://example.com",
            strategy="aiohttp",
        )
        assert resp.status_code == 200
        assert resp.content_bytes == b"hello"
        assert resp.strategy == "aiohttp"


# ============================================================================
# build_stealth_headers
# ============================================================================
class TestBuildStealthHeaders:
    def test_basic(self):
        headers = build_stealth_headers("https://example.com/page")
        assert "Accept" in headers
        assert headers["Referer"] == "https://example.com/"

    def test_custom_referer(self):
        headers = build_stealth_headers("https://example.com/page", referer="https://google.com/")
        assert headers["Referer"] == "https://google.com/"

    def test_extra_headers(self):
        headers = build_stealth_headers("https://example.com", extra={"X-Custom": "val"})
        assert headers["X-Custom"] == "val"

    def test_no_extra(self):
        headers = build_stealth_headers("https://example.com")
        assert "X-Custom" not in headers


# ============================================================================
# _try_aiohttp
# ============================================================================
class TestTryAiohttp:
    @pytest.mark.asyncio
    async def test_success(self, log):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=b"content")
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.url = "https://example.com"

        # aiohttp uses async context manager for session.get()
        mock_session = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_ctx)

        result = await _try_aiohttp(mock_session, "https://example.com", {}, 15, log)
        assert result is not None
        assert result.status_code == 200
        assert result.strategy == "aiohttp"

    @pytest.mark.asyncio
    async def test_timeout(self, log):
        mock_session = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_ctx)

        result = await _try_aiohttp(mock_session, "https://example.com", {}, 15, log)
        assert result is None

    @pytest.mark.asyncio
    async def test_client_error(self, log):
        mock_session = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("fail"))
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_ctx)

        result = await _try_aiohttp(mock_session, "https://example.com", {}, 15, log)
        assert result is None

    @pytest.mark.asyncio
    async def test_unexpected_error(self, log):
        mock_session = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("unexpected"))
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_ctx)

        result = await _try_aiohttp(mock_session, "https://example.com", {}, 15, log)
        assert result is None


# ============================================================================
# _sync_curl_cffi_fetch
# ============================================================================
class TestSyncCurlCffiFetch:
    def test_import_error(self, log):
        with patch.dict("sys.modules", {"curl_cffi": None, "curl_cffi.requests": None}):
            # Simulate ImportError by patching the import
            with patch("builtins.__import__", side_effect=ImportError("no curl_cffi")):
                result = _sync_curl_cffi_fetch("https://example.com", {}, 15, True, logger=log)
                # May return None if curl_cffi not available
                # The function handles ImportError internally

    def test_empty_pool(self, log):
        # profiles=[] is falsy, so `profiles or _CURL_PROFILES` falls back to
        # the module-level list.  We must also patch that list to be empty so
        # the "not pool" early-return is triggered and no real HTTP request is made.
        with patch("app.connectors.sources.web.fetch_strategy._CURL_PROFILES", []):
            result = _sync_curl_cffi_fetch("https://example.com", {}, 15, True, profiles=[], logger=log)
            assert result is None

    def test_with_profiles_all_fail(self, log):
        mock_session_cls = MagicMock()
        mock_session_cls.side_effect = Exception("TLS error")

        with patch("app.connectors.sources.web.fetch_strategy._CURL_PROFILES", ["chrome120"]):
            with patch("app.connectors.sources.web.fetch_strategy.Session", mock_session_cls, create=True):
                # This should handle exceptions gracefully
                pass


# ============================================================================
# _try_curl_cffi
# ============================================================================
class TestTryCurlCffi:
    @pytest.mark.asyncio
    async def test_returns_none_when_all_exhausted(self, log):
        with patch("app.connectors.sources.web.fetch_strategy._sync_curl_cffi_fetch", return_value=None):
            result = await _try_curl_cffi("https://example.com", {}, 15, True, log)
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_result(self, log):
        mock_result = FetchResponse(200, b"ok", {}, "https://example.com", "curl_cffi")
        with patch("app.connectors.sources.web.fetch_strategy._sync_curl_cffi_fetch", return_value=mock_result):
            result = await _try_curl_cffi("https://example.com", {}, 15, True, log)
            assert result is not None
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_handles_exception(self, log):
        with patch(
            "app.connectors.sources.web.fetch_strategy._sync_curl_cffi_fetch",
            side_effect=RuntimeError("unexpected"),
        ):
            # The function runs _sync_curl_cffi_fetch in an executor, so we need to
            # patch at the executor level
            with patch("asyncio.get_running_loop") as mock_loop:
                mock_loop.return_value.run_in_executor = AsyncMock(side_effect=RuntimeError("fail"))
                result = await _try_curl_cffi("https://example.com", {}, 15, True, log)
                assert result is None


# ============================================================================
# _sync_cloudscraper_fetch
# ============================================================================
class TestSyncCloudscraperFetch:
    def test_import_error(self, log):
        with patch.dict("sys.modules", {"cloudscraper": None}):
            with patch("builtins.__import__", side_effect=ImportError("no cloudscraper")):
                result = _sync_cloudscraper_fetch("https://example.com", {}, 15, log)
                # Handles ImportError internally

    def test_exception_returns_none(self, log):
        mock_cloudscraper = MagicMock()
        mock_scraper = MagicMock()
        mock_scraper.get.side_effect = Exception("fail")
        mock_cloudscraper.create_scraper.return_value = mock_scraper

        with patch.dict("sys.modules", {"cloudscraper": mock_cloudscraper}):
            result = _sync_cloudscraper_fetch("https://example.com", {}, 15, log)
            assert result is None


# ============================================================================
# _try_cloudscraper
# ============================================================================
class TestTryCloudscraper:
    @pytest.mark.asyncio
    async def test_returns_none_on_failure(self, log):
        with patch(
            "app.connectors.sources.web.fetch_strategy._sync_cloudscraper_fetch",
            return_value=None,
        ):
            result = await _try_cloudscraper("https://example.com", {}, 15, log)
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_result(self, log):
        mock_result = FetchResponse(200, b"ok", {}, "https://example.com", "cloudscraper")
        with patch(
            "app.connectors.sources.web.fetch_strategy._sync_cloudscraper_fetch",
            return_value=mock_result,
        ):
            result = await _try_cloudscraper("https://example.com", {}, 15, log)
            assert result is not None

    @pytest.mark.asyncio
    async def test_handles_exception(self, log):
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(side_effect=RuntimeError("fail"))
            result = await _try_cloudscraper("https://example.com", {}, 15, log)
            assert result is None


# ============================================================================
# fetch_url_with_fallback
# ============================================================================
class TestFetchUrlWithFallback:
    @pytest.mark.asyncio
    async def test_success_first_strategy(self, log):
        mock_session = AsyncMock()
        success_resp = FetchResponse(200, b"ok", {}, "https://example.com", "curl_cffi")
        with patch("app.connectors.sources.web.fetch_strategy._try_curl_cffi", return_value=success_resp):
            result = await fetch_url_with_fallback("https://example.com", mock_session, log)
            assert result is not None
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_non_retryable_404(self, log):
        mock_session = AsyncMock()
        not_found_resp = FetchResponse(404, b"", {}, "https://example.com", "curl_cffi")
        with patch("app.connectors.sources.web.fetch_strategy._try_curl_cffi", return_value=not_found_resp):
            result = await fetch_url_with_fallback("https://example.com", mock_session, log)
            assert result is not None
            assert result.status_code == 404

    @pytest.mark.asyncio
    async def test_server_error_stops(self, log):
        mock_session = AsyncMock()
        error_resp = FetchResponse(500, b"", {}, "https://example.com", "curl_cffi")
        with patch("app.connectors.sources.web.fetch_strategy._try_curl_cffi", return_value=error_resp):
            result = await fetch_url_with_fallback("https://example.com", mock_session, log)
            assert result is not None
            assert result.status_code == 500

    @pytest.mark.asyncio
    async def test_all_strategies_fail_returns_none(self, log):
        mock_session = AsyncMock()
        with patch("app.connectors.sources.web.fetch_strategy._try_curl_cffi", return_value=None):
            with patch("app.connectors.sources.web.fetch_strategy._try_cloudscraper", return_value=None):
                with patch("app.connectors.sources.web.fetch_strategy._try_aiohttp", return_value=None):
                    result = await fetch_url_with_fallback(
                        "https://example.com", mock_session, log,
                        max_retries_per_strategy=1,
                    )
                    assert result is None

    @pytest.mark.asyncio
    async def test_bot_detection_tries_next_strategy(self, log):
        mock_session = AsyncMock()
        bot_resp = FetchResponse(403, b"", {}, "https://example.com", "curl_cffi")
        success_resp = FetchResponse(200, b"ok", {}, "https://example.com", "cloudscraper")

        call_count = 0

        async def mock_curl(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return bot_resp

        with patch("app.connectors.sources.web.fetch_strategy._try_curl_cffi", side_effect=mock_curl):
            with patch("app.connectors.sources.web.fetch_strategy._try_cloudscraper", return_value=success_resp):
                result = await fetch_url_with_fallback(
                    "https://example.com", mock_session, log,
                    max_retries_per_strategy=1,
                )
                assert result is not None
                assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_unknown_4xx_stops(self, log):
        mock_session = AsyncMock()
        resp = FetchResponse(418, b"", {}, "https://example.com", "curl_cffi")
        with patch("app.connectors.sources.web.fetch_strategy._try_curl_cffi", return_value=resp):
            result = await fetch_url_with_fallback("https://example.com", mock_session, log)
            assert result is not None
            assert result.status_code == 418

    @pytest.mark.asyncio
    async def test_max_size_exceeded(self, log):
        mock_session = MagicMock()
        head_resp = MagicMock()
        head_resp.headers = {"Content-Length": str(100 * 1024 * 1024)}  # 100MB
        head_ctx = MagicMock()
        head_ctx.__aenter__ = AsyncMock(return_value=head_resp)
        head_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.head = MagicMock(return_value=head_ctx)

        result = await fetch_url_with_fallback(
            "https://example.com", mock_session, log,
            max_size_mb=10,
        )
        assert result is not None
        assert result.status_code == 413
        assert result.strategy == "size_guard"

    @pytest.mark.asyncio
    async def test_max_size_head_fails_proceeds(self, log):
        mock_session = MagicMock()
        head_ctx = MagicMock()
        head_ctx.__aenter__ = AsyncMock(side_effect=Exception("HEAD not supported"))
        head_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.head = MagicMock(return_value=head_ctx)

        success_resp = FetchResponse(200, b"ok", {}, "https://example.com", "curl_cffi")
        with patch("app.connectors.sources.web.fetch_strategy._try_curl_cffi", return_value=success_resp):
            result = await fetch_url_with_fallback(
                "https://example.com", mock_session, log,
                max_size_mb=10,
            )
            assert result is not None
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_max_size_no_content_length(self, log):
        mock_session = MagicMock()
        head_resp = MagicMock()
        head_resp.headers = {}  # No Content-Length
        head_ctx = MagicMock()
        head_ctx.__aenter__ = AsyncMock(return_value=head_resp)
        head_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.head = MagicMock(return_value=head_ctx)

        success_resp = FetchResponse(200, b"ok", {}, "https://example.com", "curl_cffi")
        with patch("app.connectors.sources.web.fetch_strategy._try_curl_cffi", return_value=success_resp):
            result = await fetch_url_with_fallback(
                "https://example.com", mock_session, log,
                max_size_mb=10,
            )
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_preferred_strategy_match(self, log):
        mock_session = AsyncMock()
        success_resp = FetchResponse(200, b"ok", {}, "https://example.com", "aiohttp")
        with patch("app.connectors.sources.web.fetch_strategy._try_aiohttp", return_value=success_resp):
            result = await fetch_url_with_fallback(
                "https://example.com", mock_session, log,
                preferred_strategy="aiohttp",
            )
            assert result is not None
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_preferred_strategy_no_match_falls_back(self, log):
        mock_session = AsyncMock()
        success_resp = FetchResponse(200, b"ok", {}, "https://example.com", "curl_cffi")
        with patch("app.connectors.sources.web.fetch_strategy._try_curl_cffi", return_value=success_resp):
            result = await fetch_url_with_fallback(
                "https://example.com", mock_session, log,
                preferred_strategy="nonexistent_strategy",
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_all_fail_with_bot_detection_returns_last(self, log):
        mock_session = AsyncMock()
        bot_resp = FetchResponse(403, b"blocked", {}, "https://example.com", "any")
        with patch("app.connectors.sources.web.fetch_strategy._try_curl_cffi", return_value=bot_resp):
            with patch("app.connectors.sources.web.fetch_strategy._try_cloudscraper", return_value=bot_resp):
                with patch("app.connectors.sources.web.fetch_strategy._try_aiohttp", return_value=bot_resp):
                    result = await fetch_url_with_fallback(
                        "https://example.com", mock_session, log,
                        max_retries_per_strategy=1,
                    )
                    assert result is not None
                    assert result.status_code == 403


# ============================================================================
# Constants
# ============================================================================
class TestConstants:
    def test_non_retryable_codes(self):
        assert 404 in _NON_RETRYABLE_CLIENT_ERRORS
        assert 405 in _NON_RETRYABLE_CLIENT_ERRORS
        assert 410 in _NON_RETRYABLE_CLIENT_ERRORS

    def test_bot_detection_codes(self):
        assert 403 in _BOT_DETECTION_CODES
        assert 999 in _BOT_DETECTION_CODES
        assert 520 in _BOT_DETECTION_CODES
