"""Unit tests for app.connectors.sources.web.fetch_strategy."""

import asyncio
import logging
import socket
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from app.connectors.sources.web.fetch_strategy import (
    MAX_RATE_LIMIT_BACKOFF,
    FetchResponse,
    _BOT_DETECTION_CODES,
    _NON_RETRYABLE_CLIENT_ERRORS,
    build_stealth_headers,
    fetch_url_with_fallback,
)
from app.services.base_client import parse_retry_after


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Every host resolves to one public address, without real DNS."""
    answer = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.215.14", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", MagicMock(return_value=answer))


@pytest.fixture
def log():
    lg = logging.getLogger("test_fetch")
    lg.setLevel(logging.DEBUG)
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

    def test_all_fields_stored(self):
        resp = FetchResponse(
            status_code=403,
            content_bytes=b"blocked",
            headers={"X-Custom": "val"},
            final_url="http://example.com/redirected",
            strategy="cloudscraper",
        )
        assert resp.headers == {"X-Custom": "val"}
        assert resp.final_url == "http://example.com/redirected"


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

    def test_auto_referer_uses_scheme_and_netloc(self):
        headers = build_stealth_headers("http://sub.domain.org:8080/path?q=1")
        assert headers["Referer"] == "http://sub.domain.org:8080/"

    def test_extra_overrides_default_header(self):
        headers = build_stealth_headers(
            "https://example.com",
            extra={"Accept": "application/json"},
        )
        assert headers["Accept"] == "application/json"

    def test_contains_expected_security_headers(self):
        headers = build_stealth_headers("https://example.com")
        assert "Sec-Ch-Ua" in headers
        assert "Sec-Fetch-Dest" in headers
        assert "Upgrade-Insecure-Requests" in headers
        assert headers["Cache-Control"] == "no-cache"
        assert headers["Pragma"] == "no-cache"


# ============================================================================
# _get_supported_profiles
# ============================================================================
class TestGetSupportedProfiles:
    def test_import_error_returns_empty(self):
        from app.connectors.sources.web.fetch_strategy import _get_supported_profiles

        with patch.dict("sys.modules", {"curl_cffi": None, "curl_cffi.requests": None}):
            with patch("builtins.__import__", side_effect=ImportError("no curl_cffi")):
                result = _get_supported_profiles()
                assert result == []

    def test_with_some_valid_profiles(self):
        from app.connectors.sources.web.fetch_strategy import _get_supported_profiles

        mock_session = MagicMock()
        mock_session.close = MagicMock()

        call_count = 0

        def session_side_effect(impersonate=None):
            nonlocal call_count
            call_count += 1
            # Fail on even calls, succeed on odd
            if call_count % 2 == 0:
                raise Exception("unsupported profile")
            return mock_session

        mock_module = MagicMock()
        mock_module.Session = MagicMock(side_effect=session_side_effect)

        with patch.dict("sys.modules", {"curl_cffi": MagicMock(), "curl_cffi.requests": mock_module}):
            result = _get_supported_profiles()
            assert isinstance(result, list)


# ============================================================================
# fetch_url_with_fallback
# ============================================================================
class TestFetchUrlWithFallback:
    @pytest.mark.asyncio
    async def test_success_first_strategy(self, log):
        mock_session = AsyncMock()
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "curl_cffi"
        )
        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=success_resp,
        ):
            result = await fetch_url_with_fallback(
                "https://example.com", mock_session, log
            )
            assert result is not None
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_non_retryable_404(self, log):
        mock_session = AsyncMock()
        not_found_resp = FetchResponse(
            404, b"", {}, "https://example.com", "curl_cffi"
        )
        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=not_found_resp,
        ):
            result = await fetch_url_with_fallback(
                "https://example.com", mock_session, log
            )
            assert result is not None
            assert result.status_code == 404

    @pytest.mark.asyncio
    async def test_non_retryable_410(self, log):
        mock_session = AsyncMock()
        gone_resp = FetchResponse(410, b"", {}, "https://example.com", "curl_cffi")
        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=gone_resp,
        ):
            result = await fetch_url_with_fallback(
                "https://example.com", mock_session, log
            )
            assert result is not None
            assert result.status_code == 410

    @pytest.mark.asyncio
    async def test_non_retryable_405(self, log):
        mock_session = AsyncMock()
        resp = FetchResponse(405, b"", {}, "https://example.com", "curl_cffi")
        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=resp,
        ):
            result = await fetch_url_with_fallback(
                "https://example.com", mock_session, log
            )
            assert result is not None
            assert result.status_code == 405

    @pytest.mark.asyncio
    async def test_server_error_stops(self, log):
        mock_session = AsyncMock()
        error_resp = FetchResponse(
            500, b"", {}, "https://example.com", "curl_cffi"
        )
        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=error_resp,
        ):
            result = await fetch_url_with_fallback(
                "https://example.com", mock_session, log
            )
            assert result is not None
            assert result.status_code == 500

    @pytest.mark.asyncio
    async def test_server_error_502(self, log):
        mock_session = AsyncMock()
        error_resp = FetchResponse(
            502, b"", {}, "https://example.com", "curl_cffi"
        )
        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=error_resp,
        ):
            result = await fetch_url_with_fallback(
                "https://example.com", mock_session, log
            )
            assert result is not None
            assert result.status_code == 502

    @pytest.mark.asyncio
    async def test_all_strategies_fail_returns_none(self, log):
        mock_session = AsyncMock()
        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=None,
        ):
            with patch(
                "app.connectors.sources.web.fetch_strategy._hops_cloudscraper",
                return_value=None,
            ):
                with patch(
                    "app.connectors.sources.web.fetch_strategy._hops_aiohttp",
                    return_value=None,
                ):
                    result = await fetch_url_with_fallback(
                        "https://example.com",
                        mock_session,
                        log,
                        max_retries_per_strategy=1,
                    )
                    assert result is None

    @pytest.mark.asyncio
    async def test_bot_detection_tries_next_strategy(self, log):
        mock_session = AsyncMock()
        bot_resp = FetchResponse(
            403, b"", {}, "https://example.com", "curl_cffi"
        )
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "cloudscraper"
        )

        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=bot_resp,
        ):
            with patch(
                "app.connectors.sources.web.fetch_strategy._hops_cloudscraper",
                return_value=success_resp,
            ):
                result = await fetch_url_with_fallback(
                    "https://example.com",
                    mock_session,
                    log,
                    max_retries_per_strategy=1,
                )
                assert result is not None
                assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_bot_detection_999(self, log):
        """LinkedIn's 999 code is treated as bot detection."""
        mock_session = AsyncMock()
        bot_resp = FetchResponse(999, b"", {}, "https://example.com", "curl_cffi")
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "cloudscraper"
        )

        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=bot_resp,
        ):
            with patch(
                "app.connectors.sources.web.fetch_strategy._hops_cloudscraper",
                return_value=success_resp,
            ):
                result = await fetch_url_with_fallback(
                    "https://example.com",
                    mock_session,
                    log,
                    max_retries_per_strategy=1,
                )
                assert result is not None
                assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_bot_detection_cloudflare_520(self, log):
        """Cloudflare 520 is treated as bot detection."""
        mock_session = AsyncMock()
        bot_resp = FetchResponse(520, b"", {}, "https://example.com", "curl_cffi")
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "aiohttp"
        )

        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=bot_resp,
        ):
            with patch(
                "app.connectors.sources.web.fetch_strategy._hops_cloudscraper",
                return_value=bot_resp,
            ):
                with patch(
                    "app.connectors.sources.web.fetch_strategy._hops_aiohttp",
                    return_value=success_resp,
                ):
                    result = await fetch_url_with_fallback(
                        "https://example.com",
                        mock_session,
                        log,
                        max_retries_per_strategy=1,
                    )
                    assert result is not None
                    assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_unknown_4xx_stops(self, log):
        mock_session = AsyncMock()
        resp = FetchResponse(418, b"", {}, "https://example.com", "curl_cffi")
        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=resp,
        ):
            result = await fetch_url_with_fallback(
                "https://example.com", mock_session, log
            )
            assert result is not None
            assert result.status_code == 418

    @pytest.mark.asyncio
    async def test_unknown_4xx_401(self, log):
        mock_session = AsyncMock()
        resp = FetchResponse(401, b"", {}, "https://example.com", "curl_cffi")
        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=resp,
        ):
            result = await fetch_url_with_fallback(
                "https://example.com", mock_session, log
            )
            assert result is not None
            assert result.status_code == 401

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
            "https://example.com",
            mock_session,
            log,
            max_size_mb=10,
        )
        assert result is not None
        assert result.status_code == 413
        assert result.strategy == "size_guard"

    @pytest.mark.asyncio
    async def test_max_size_content_length_lowercase(self, log):
        """Content-Length header with lowercase key."""
        mock_session = MagicMock()
        head_resp = MagicMock()
        head_resp.headers = {"content-length": str(200 * 1024 * 1024)}
        head_ctx = MagicMock()
        head_ctx.__aenter__ = AsyncMock(return_value=head_resp)
        head_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.head = MagicMock(return_value=head_ctx)

        result = await fetch_url_with_fallback(
            "https://example.com",
            mock_session,
            log,
            max_size_mb=10,
        )
        assert result is not None
        assert result.status_code == 413

    @pytest.mark.asyncio
    async def test_max_size_within_limit_proceeds(self, log):
        """When Content-Length is within limit, proceed with normal fetch."""
        mock_session = MagicMock()
        head_resp = MagicMock()
        head_resp.headers = {"Content-Length": str(1 * 1024 * 1024)}  # 1MB
        head_ctx = MagicMock()
        head_ctx.__aenter__ = AsyncMock(return_value=head_resp)
        head_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.head = MagicMock(return_value=head_ctx)

        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "curl_cffi"
        )
        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=success_resp,
        ):
            result = await fetch_url_with_fallback(
                "https://example.com",
                mock_session,
                log,
                max_size_mb=10,
            )
            assert result is not None
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_max_size_head_fails_proceeds(self, log):
        mock_session = MagicMock()
        head_ctx = MagicMock()
        head_ctx.__aenter__ = AsyncMock(side_effect=Exception("HEAD not supported"))
        head_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.head = MagicMock(return_value=head_ctx)

        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "curl_cffi"
        )
        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=success_resp,
        ):
            result = await fetch_url_with_fallback(
                "https://example.com",
                mock_session,
                log,
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

        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "curl_cffi"
        )
        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=success_resp,
        ):
            result = await fetch_url_with_fallback(
                "https://example.com",
                mock_session,
                log,
                max_size_mb=10,
            )
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_preferred_strategy_match_aiohttp(self, log):
        mock_session = AsyncMock()
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "aiohttp"
        )
        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_aiohttp",
            return_value=success_resp,
        ):
            result = await fetch_url_with_fallback(
                "https://example.com",
                mock_session,
                log,
                preferred_strategy="aiohttp",
            )
            assert result is not None
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_preferred_strategy_match_curl_cffi(self, log):
        mock_session = AsyncMock()
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "curl_cffi(chrome120, h2=True)"
        )
        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=success_resp,
        ):
            result = await fetch_url_with_fallback(
                "https://example.com",
                mock_session,
                log,
                preferred_strategy="curl_cffi(chrome120, h2=True)",
            )
            assert result is not None
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_preferred_strategy_match_cloudscraper(self, log):
        mock_session = AsyncMock()
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "cloudscraper"
        )
        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_cloudscraper",
            return_value=success_resp,
        ):
            result = await fetch_url_with_fallback(
                "https://example.com",
                mock_session,
                log,
                preferred_strategy="cloudscraper",
            )
            assert result is not None
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_preferred_strategy_no_match_falls_back(self, log):
        mock_session = AsyncMock()
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "curl_cffi"
        )
        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=success_resp,
        ):
            result = await fetch_url_with_fallback(
                "https://example.com",
                mock_session,
                log,
                preferred_strategy="nonexistent_strategy",
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_all_fail_with_bot_detection_returns_last(self, log):
        mock_session = AsyncMock()
        bot_resp = FetchResponse(
            403, b"blocked", {}, "https://example.com", "any"
        )
        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=bot_resp,
        ):
            with patch(
                "app.connectors.sources.web.fetch_strategy._hops_cloudscraper",
                return_value=bot_resp,
            ):
                with patch(
                    "app.connectors.sources.web.fetch_strategy._hops_aiohttp",
                    return_value=bot_resp,
                ):
                    result = await fetch_url_with_fallback(
                        "https://example.com",
                        mock_session,
                        log,
                        max_retries_per_strategy=1,
                    )
                    assert result is not None
                    assert result.status_code == 403

    @pytest.mark.asyncio
    async def test_429_retry_with_retry_after_header(self, log):
        """429 with Retry-After header uses that value for delay."""
        mock_session = AsyncMock()
        rate_limited_resp = FetchResponse(
            429, b"", {"Retry-After": "1"}, "https://example.com", "curl_cffi"
        )
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "curl_cffi"
        )

        call_count = 0

        async def mock_curl(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return rate_limited_resp
            return success_resp

        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            side_effect=mock_curl,
        ):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await fetch_url_with_fallback(
                    "https://example.com",
                    mock_session,
                    log,
                    max_retries_per_strategy=1,
                )
                assert result is not None
                assert result.status_code == 200
                # Verify sleep was called with delay=1 (from Retry-After header)
                mock_sleep.assert_called_once_with(1.0)

    @pytest.mark.asyncio
    async def test_429_retry_with_invalid_retry_after(self, log):
        """429 with non-numeric Retry-After falls back to exponential backoff."""
        mock_session = AsyncMock()
        rate_limited_resp = FetchResponse(
            429, b"", {"Retry-After": "not-a-number"}, "https://example.com", "curl_cffi"
        )
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "curl_cffi"
        )

        call_count = 0

        async def mock_curl(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return rate_limited_resp
            return success_resp

        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            side_effect=mock_curl,
        ):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await fetch_url_with_fallback(
                    "https://example.com",
                    mock_session,
                    log,
                    max_retries_per_strategy=1,
                )
                assert result is not None
                assert result.status_code == 200
                # Should use exponential backoff: 2^(0+1) = 2
                mock_sleep.assert_called_once_with(2)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "header",
        ["0", "Wed, 21 Oct 2015 07:28:00 GMT"],
        ids=["zero-seconds", "date-already-past"],
    )
    async def test_429_retry_after_asking_for_no_wait_still_backs_off(self, log, header):
        """A Retry-After of 0, or a date already past, must not retry instantly."""
        mock_session = AsyncMock()
        rate_limited_resp = FetchResponse(
            429, b"", {"Retry-After": header}, "https://example.com", "curl_cffi"
        )
        success_resp = FetchResponse(200, b"ok", {}, "https://example.com", "curl_cffi")

        call_count = 0

        async def mock_curl(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return rate_limited_resp if call_count <= 1 else success_resp

        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            side_effect=mock_curl,
        ):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await fetch_url_with_fallback(
                    "https://example.com", mock_session, log, max_retries_per_strategy=1
                )
                assert result is not None
                assert result.status_code == 200
                # Exponential backoff, not the zero the header asked for.
                mock_sleep.assert_called_once_with(2)

    @pytest.mark.asyncio
    async def test_429_retry_after_as_a_future_date_is_honoured(self, log):
        """A date inside the cap is waited out, like a number of seconds would be."""
        mock_session = AsyncMock()
        when = datetime.now(timezone.utc) + timedelta(seconds=45)
        rate_limited_resp = FetchResponse(
            429, b"", {"Retry-After": format_datetime(when, usegmt=True)},
            "https://example.com", "curl_cffi",
        )
        success_resp = FetchResponse(200, b"ok", {}, "https://example.com", "curl_cffi")

        call_count = 0

        async def mock_curl(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return rate_limited_resp if call_count <= 1 else success_resp

        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            side_effect=mock_curl,
        ):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await fetch_url_with_fallback(
                    "https://example.com", mock_session, log, max_retries_per_strategy=1
                )
                assert result is not None
                slept = mock_sleep.call_args[0][0]
                assert 35 <= slept <= 50, f"waited {slept}s for a 45s date"

    @pytest.mark.asyncio
    async def test_429_retry_after_date_beyond_the_cap_is_handed_back(self, log):
        """A date further out than the cap returns to the caller instead of sleeping."""
        mock_session = AsyncMock()
        when = datetime.now(timezone.utc) + timedelta(hours=1)
        rate_limited_resp = FetchResponse(
            429, b"", {"Retry-After": format_datetime(when, usegmt=True)},
            "https://example.com", "curl_cffi",
        )

        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            new_callable=AsyncMock,
            return_value=rate_limited_resp,
        ):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await fetch_url_with_fallback(
                    "https://example.com", mock_session, log, max_retries_per_strategy=1
                )
                assert result is not None
                assert result.status_code == 429
                assert result.retry_after is not None
                assert result.retry_after > MAX_RATE_LIMIT_BACKOFF
                mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_429_retry_without_retry_after_header(self, log):
        """429 without Retry-After uses exponential backoff."""
        mock_session = AsyncMock()
        rate_limited_resp = FetchResponse(
            429, b"", {}, "https://example.com", "curl_cffi"
        )
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "curl_cffi"
        )

        call_count = 0

        async def mock_curl(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return rate_limited_resp
            return success_resp

        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            side_effect=mock_curl,
        ):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await fetch_url_with_fallback(
                    "https://example.com",
                    mock_session,
                    log,
                    max_retries_per_strategy=1,
                )
                assert result is not None
                assert result.status_code == 200
                # 2^(0+1) = 2
                mock_sleep.assert_called_once_with(2)

    @pytest.mark.asyncio
    async def test_429_retry_with_lowercase_retry_after(self, log):
        """429 with lowercase 'retry-after' header is also recognized."""
        mock_session = AsyncMock()
        rate_limited_resp = FetchResponse(
            429, b"", {"retry-after": "3"}, "https://example.com", "curl_cffi"
        )
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "curl_cffi"
        )

        call_count = 0

        async def mock_curl(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return rate_limited_resp
            return success_resp

        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            side_effect=mock_curl,
        ):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await fetch_url_with_fallback(
                    "https://example.com",
                    mock_session,
                    log,
                    max_retries_per_strategy=1,
                )
                assert result.status_code == 200
                mock_sleep.assert_called_once_with(3.0)

    @pytest.mark.asyncio
    async def test_429_exhausted_moves_to_next_strategy(self, log):
        """When 429 persists beyond max backoff, move to next strategy."""
        mock_session = AsyncMock()
        rate_limited_resp = FetchResponse(
            429, b"", {}, "https://example.com", "curl_cffi"
        )
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "cloudscraper"
        )

        with patch(
            "app.connectors.sources.web.fetch_strategy.MAX_RATE_LIMIT_BACKOFF",
            4,
        ):
            with patch(
                "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
                return_value=rate_limited_resp,
            ):
                with patch(
                    "app.connectors.sources.web.fetch_strategy._hops_cloudscraper",
                    return_value=success_resp,
                ):
                    with patch("asyncio.sleep", new_callable=AsyncMock):
                        result = await fetch_url_with_fallback(
                            "https://example.com",
                            mock_session,
                            log,
                            max_retries_per_strategy=1,
                        )
                        assert result is not None
                        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_429_exhausted_all_strategies_returns_last(self, log):
        """When 429 persists and all strategies exhausted, return last failed."""
        mock_session = AsyncMock()
        rate_limited_resp = FetchResponse(
            429, b"", {}, "https://example.com", "any"
        )

        with patch(
            "app.connectors.sources.web.fetch_strategy.MAX_RATE_LIMIT_BACKOFF",
            4,
        ):
            with patch(
                "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
                return_value=rate_limited_resp,
            ):
                with patch(
                    "app.connectors.sources.web.fetch_strategy._hops_cloudscraper",
                    return_value=rate_limited_resp,
                ):
                    with patch(
                        "app.connectors.sources.web.fetch_strategy._hops_aiohttp",
                        return_value=rate_limited_resp,
                    ):
                        with patch("asyncio.sleep", new_callable=AsyncMock):
                            result = await fetch_url_with_fallback(
                                "https://example.com",
                                mock_session,
                                log,
                                max_retries_per_strategy=1,
                            )
                            assert result is not None
                            assert result.status_code == 429

    @pytest.mark.asyncio
    async def test_bot_detection_retries_then_next_strategy(self, log):
        """403 retries within a strategy before moving to next."""
        mock_session = AsyncMock()
        bot_resp = FetchResponse(
            403, b"", {}, "https://example.com", "curl_cffi"
        )
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "cloudscraper"
        )

        curl_call_count = 0

        async def mock_curl(*args, **kwargs):
            nonlocal curl_call_count
            curl_call_count += 1
            return bot_resp

        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            side_effect=mock_curl,
        ):
            with patch(
                "app.connectors.sources.web.fetch_strategy._hops_cloudscraper",
                return_value=success_resp,
            ):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    result = await fetch_url_with_fallback(
                        "https://example.com",
                        mock_session,
                        log,
                        max_retries_per_strategy=3,
                    )
                    assert result is not None
                    assert result.status_code == 200
                    # 403 triggers break from 429 loop, then retries strategy
                    # With max_retries_per_strategy=3, curl should be called 3 times
                    assert curl_call_count == 3

    @pytest.mark.asyncio
    async def test_result_none_breaks_429_loop(self, log):
        """When strategy returns None, break 429 loop and try next attempt."""
        mock_session = AsyncMock()
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "cloudscraper"
        )

        curl_call_count = 0

        async def mock_curl(*args, **kwargs):
            nonlocal curl_call_count
            curl_call_count += 1
            return None  # Connection failed

        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            side_effect=mock_curl,
        ):
            with patch(
                "app.connectors.sources.web.fetch_strategy._hops_cloudscraper",
                return_value=success_resp,
            ):
                with patch(
                    "app.connectors.sources.web.fetch_strategy._hops_aiohttp",
                    return_value=None,
                ):
                    with patch("asyncio.sleep", new_callable=AsyncMock):
                        result = await fetch_url_with_fallback(
                            "https://example.com",
                            mock_session,
                            log,
                            max_retries_per_strategy=2,
                        )
                        assert result is not None
                        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_retry_backoff_between_attempts(self, log):
        """Bot detection triggers retry with backoff between attempts."""
        mock_session = AsyncMock()
        bot_resp = FetchResponse(
            403, b"", {}, "https://example.com", "curl_cffi"
        )
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "cloudscraper"
        )

        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=bot_resp,
        ):
            with patch(
                "app.connectors.sources.web.fetch_strategy._hops_cloudscraper",
                return_value=success_resp,
            ):
                with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                    with patch("random.uniform", return_value=0.25):
                        result = await fetch_url_with_fallback(
                            "https://example.com",
                            mock_session,
                            log,
                            max_retries_per_strategy=2,
                        )
                        assert result is not None
                        assert result.status_code == 200
                        # 403 is bot detection so each curl attempt also sleeps 2.0,
                        # and the second attempt (attempt=1) sleeps 1 + random(0, 0.5) = 1.25
                        assert mock_sleep.call_count == 3
                        mock_sleep.assert_any_call(2.0)
                        mock_sleep.assert_any_call(1.25)

    @pytest.mark.asyncio
    async def test_referer_and_extra_headers_passed(self, log):
        """referer and extra_headers are forwarded to build_stealth_headers."""
        mock_session = AsyncMock()
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "curl_cffi"
        )

        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=success_resp,
        ) as mock_curl:
            with patch(
                "app.connectors.sources.web.fetch_strategy.build_stealth_headers",
                wraps=build_stealth_headers,
            ) as mock_build:
                result = await fetch_url_with_fallback(
                    "https://example.com",
                    mock_session,
                    log,
                    referer="https://google.com",
                    extra_headers={"X-Test": "yes"},
                )
                assert result is not None
                mock_build.assert_called_once_with(
                    "https://example.com",
                    referer="https://google.com",
                    extra={"X-Test": "yes"},
                )

    @pytest.mark.asyncio
    async def test_no_max_size_skips_head_request(self, log):
        """When max_size_mb is None, no HEAD request is made."""
        mock_session = MagicMock()
        mock_session.head = MagicMock()

        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "curl_cffi"
        )
        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=success_resp,
        ):
            result = await fetch_url_with_fallback(
                "https://example.com",
                mock_session,
                log,
            )
            assert result is not None
            mock_session.head.assert_not_called()

    @pytest.mark.asyncio
    async def test_5xx_not_in_bot_detection_stops(self, log):
        """Server error (e.g. 500) that is NOT in _BOT_DETECTION_CODES stops immediately."""
        mock_session = AsyncMock()
        error_resp = FetchResponse(
            500, b"", {}, "https://example.com", "curl_cffi"
        )

        curl_call_count = 0

        async def mock_curl(*args, **kwargs):
            nonlocal curl_call_count
            curl_call_count += 1
            return error_resp

        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            side_effect=mock_curl,
        ):
            result = await fetch_url_with_fallback(
                "https://example.com",
                mock_session,
                log,
                max_retries_per_strategy=3,
            )
            assert result is not None
            assert result.status_code == 500
            # Should stop on first attempt
            assert curl_call_count == 1

    @pytest.mark.asyncio
    async def test_5xx_in_bot_detection_does_not_stop(self, log):
        """Cloudflare 5xx codes (520-530) that ARE in bot detection are retried."""
        mock_session = AsyncMock()
        cf_resp = FetchResponse(
            525, b"", {}, "https://example.com", "curl_cffi"
        )
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "cloudscraper"
        )

        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=cf_resp,
        ):
            with patch(
                "app.connectors.sources.web.fetch_strategy._hops_cloudscraper",
                return_value=success_resp,
            ):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    result = await fetch_url_with_fallback(
                        "https://example.com",
                        mock_session,
                        log,
                        max_retries_per_strategy=1,
                    )
                    assert result is not None
                    assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_success_on_second_strategy(self, log):
        """First strategy returns None, second succeeds."""
        mock_session = AsyncMock()
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "cloudscraper"
        )

        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=None,
        ):
            with patch(
                "app.connectors.sources.web.fetch_strategy._hops_cloudscraper",
                return_value=success_resp,
            ):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    result = await fetch_url_with_fallback(
                        "https://example.com",
                        mock_session,
                        log,
                        max_retries_per_strategy=1,
                    )
                    assert result is not None
                    assert result.status_code == 200
                    assert result.strategy == "cloudscraper"

    @pytest.mark.asyncio
    async def test_success_on_third_strategy_aiohttp(self, log):
        """First two strategies return None, aiohttp succeeds."""
        mock_session = AsyncMock()
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "aiohttp"
        )

        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=None,
        ):
            with patch(
                "app.connectors.sources.web.fetch_strategy._hops_cloudscraper",
                return_value=None,
            ):
                with patch(
                    "app.connectors.sources.web.fetch_strategy._hops_aiohttp",
                    return_value=success_resp,
                ):
                    with patch("asyncio.sleep", new_callable=AsyncMock):
                        result = await fetch_url_with_fallback(
                            "https://example.com",
                            mock_session,
                            log,
                            max_retries_per_strategy=1,
                        )
                        assert result is not None
                        assert result.status_code == 200
                        assert result.strategy == "aiohttp"

    @pytest.mark.asyncio
    async def test_multiple_429_retries_exponential_backoff(self, log):
        """Multiple 429 retries use increasing exponential backoff."""
        mock_session = AsyncMock()
        rate_limited_resp = FetchResponse(
            429, b"", {}, "https://example.com", "curl_cffi"
        )
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "curl_cffi"
        )

        call_count = 0

        async def mock_curl(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return rate_limited_resp
            return success_resp

        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            side_effect=mock_curl,
        ):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await fetch_url_with_fallback(
                    "https://example.com",
                    mock_session,
                    log,
                    max_retries_per_strategy=1,
                )
                assert result is not None
                assert result.status_code == 200
                # First 429: delay = 2^(0+1) = 2
                # Second 429: delay = 2^(1+1) = 4
                assert mock_sleep.call_count == 2
                mock_sleep.assert_any_call(2)
                mock_sleep.assert_any_call(4)

    @pytest.mark.asyncio
    async def test_custom_timeout(self, log):
        """Custom timeout is passed to strategies."""
        mock_session = AsyncMock()
        success_resp = FetchResponse(
            200, b"ok", {}, "https://example.com", "curl_cffi"
        )

        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=success_resp,
        ) as mock_curl:
            result = await fetch_url_with_fallback(
                "https://example.com",
                mock_session,
                log,
                timeout=30,
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_redirect_3xx_treated_as_success(self, log):
        """3xx status codes are treated as success (< 400)."""
        mock_session = AsyncMock()
        redirect_resp = FetchResponse(
            301, b"", {}, "https://example.com/new", "curl_cffi"
        )
        with patch(
            "app.connectors.sources.web.fetch_strategy._hops_curl_cffi",
            return_value=redirect_resp,
        ):
            result = await fetch_url_with_fallback(
                "https://example.com", mock_session, log
            )
            assert result is not None
            assert result.status_code == 301


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

    def test_bot_detection_codes_cloudflare_range(self):
        for code in range(520, 531):
            assert code in _BOT_DETECTION_CODES

    def test_non_retryable_not_overlapping_bot(self):
        """Non-retryable codes should not be in bot detection set."""
        assert _NON_RETRYABLE_CLIENT_ERRORS.isdisjoint(_BOT_DETECTION_CODES)


class TestParseRetryAfter:
    """The shared Retry-After parser, as the web fetch relies on it.

    Retry-After is either a number of seconds or an HTTP date (RFC 9110).
    """

    def test_seconds(self):
        assert parse_retry_after("120") == 120.0
        assert parse_retry_after("  30 ") == 30.0

    def test_negative_seconds_read_as_now(self):
        assert parse_retry_after("-5") == 0.0

    def test_http_date_in_the_future(self):
        when = datetime.now(timezone.utc) + timedelta(seconds=90)
        seconds = parse_retry_after(format_datetime(when, usegmt=True))
        assert seconds is not None
        assert 80 <= seconds <= 95

    def test_http_date_in_the_past_reads_as_now(self):
        when = datetime.now(timezone.utc) - timedelta(hours=1)
        assert parse_retry_after(format_datetime(when, usegmt=True)) == 0.0

    def test_a_long_http_date_is_over_the_cap(self):
        when = datetime.now(timezone.utc) + timedelta(hours=1)
        seconds = parse_retry_after(format_datetime(when, usegmt=True))
        assert seconds is not None and seconds > MAX_RATE_LIMIT_BACKOFF

    def test_missing_or_unreadable(self):
        assert parse_retry_after(None) is None
        assert parse_retry_after("") is None
        assert parse_retry_after("soon") is None
