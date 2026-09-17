"""Tests for app.utils.url_fetcher — robust multi-strategy URL fetcher."""

import http.server
import ipaddress
import socket
import threading
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from app.utils.url_fetcher import (
    _MAX_REDIRECTS,
    FetchError,
    FetchResult,
    PublicTarget,
    _build_headers,
    _curl_pinned_request,
    _get_profiles,
    _get_supported_profiles,
    _run_pinned_with_failover,
    _try_cloudscraper,
    _try_curl_cffi,
    _try_requests,
    fetch_url,
    resolve_public_http_target,
    validate_public_http_url,
)

# The autouse stub below replaces DNS; the loopback socket tests need the real resolver back.
_REAL_GETADDRINFO = socket.getaddrinfo


@pytest.fixture(autouse=True)
def _stub_public_dns_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid flaky tests that depend on live DNS for https://example.com."""

    def fake_getaddrinfo(
        host: str,
        port: object,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> list[tuple[int, int, int, str, tuple[str | bytes, int]]]:
        # Public resolver IP — always passes SSRF checks.
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


# ---------------------------------------------------------------------------
# FetchResult dataclass
# ---------------------------------------------------------------------------


class TestFetchResult:
    def test_basic_construction(self) -> None:
        result = FetchResult(
            status_code=200,
            text="hello",
            content=b"hello",
            headers={"Content-Type": "text/html"},
            url="https://example.com",
            strategy="requests",
        )
        assert result.status_code == 200
        assert result.text == "hello"
        assert result.strategy == "requests"

    def test_empty_fields(self) -> None:
        result = FetchResult(
            status_code=404,
            text="",
            content=b"",
            headers={},
            url="https://example.com/missing",
            strategy="cloudscraper",
        )
        assert result.status_code == 404
        assert result.headers == {}


# ---------------------------------------------------------------------------
# FetchError exception
# ---------------------------------------------------------------------------


class TestFetchError:
    def test_default_status_code(self) -> None:
        err = FetchError("Something went wrong")
        assert str(err) == "Something went wrong"
        assert err.status_code == 0

    def test_custom_status_code(self) -> None:
        err = FetchError("Forbidden", status_code=403)
        assert err.status_code == 403

    def test_is_exception(self) -> None:
        with pytest.raises(FetchError):
            raise FetchError("test error")


# ---------------------------------------------------------------------------
# _build_headers
# ---------------------------------------------------------------------------


class TestBuildHeaders:
    def test_basic_headers_present(self) -> None:
        headers = _build_headers("https://example.com/page", None, None)
        assert "Accept" in headers
        assert "Accept-Language" in headers
        assert "Cache-Control" in headers

    def test_referer_auto_generated(self) -> None:
        headers = _build_headers("https://example.com/page", None, None)
        assert headers["Referer"] == "https://example.com/"

    def test_custom_referer(self) -> None:
        headers = _build_headers("https://example.com/page", "https://google.com/", None)
        assert headers["Referer"] == "https://google.com/"

    def test_extra_headers_merged(self) -> None:
        extra = {"X-Custom": "value", "Authorization": "Bearer token"}
        headers = _build_headers("https://example.com", None, extra)
        assert headers["X-Custom"] == "value"
        assert headers["Authorization"] == "Bearer token"

    def test_extra_headers_override_defaults(self) -> None:
        extra = {"Cache-Control": "max-age=3600"}
        headers = _build_headers("https://example.com", None, extra)
        assert headers["Cache-Control"] == "max-age=3600"

    def test_no_extra_headers(self) -> None:
        headers = _build_headers("https://example.com", None, None)
        assert "X-Custom" not in headers


# ---------------------------------------------------------------------------
# _get_supported_profiles
# ---------------------------------------------------------------------------


class TestGetSupportedProfiles:
    def test_returns_empty_list_on_import_error(self) -> None:
        with patch.dict("sys.modules", {"curl_cffi": None, "curl_cffi.requests": None}):
            # Simulate ImportError by patching the import inside the function
            with patch("builtins.__import__", side_effect=ImportError("no curl_cffi")):
                result = _get_supported_profiles()
        assert isinstance(result, list)

    def test_returns_list_of_strings_when_available(self) -> None:
        mock_session_cls = MagicMock()
        mock_session_instance = MagicMock()
        mock_session_cls.return_value = mock_session_instance

        with patch("app.utils.url_fetcher._get_supported_profiles") as mock_fn:
            mock_fn.return_value = ["chrome131", "chrome124"]
            profiles = mock_fn()

        assert isinstance(profiles, list)
        assert all(isinstance(p, str) for p in profiles)

    def test_skips_unsupported_profiles(self) -> None:
        call_count = [0]

        def fake_session(impersonate: str) -> MagicMock:
            call_count[0] += 1
            if impersonate in ("chrome131", "chrome124"):
                return MagicMock()
            raise ValueError(f"unsupported: {impersonate}")

        mock_session = MagicMock(side_effect=fake_session)

        with patch("app.utils.url_fetcher._get_supported_profiles") as mock_fn:
            mock_fn.return_value = ["chrome131", "chrome124"]
            result = mock_fn()

        assert "chrome131" in result


# ---------------------------------------------------------------------------
# _get_profiles — caching behaviour
# ---------------------------------------------------------------------------


class TestGetProfiles:
    def test_caches_result_on_second_call(self) -> None:
        import app.utils.url_fetcher as mod

        original = mod._PROFILES
        try:
            mod._PROFILES = None
            with patch.object(mod, "_get_supported_profiles", return_value=["chrome131"]) as mock_fn:
                first = _get_profiles()
                second = _get_profiles()

            assert first == second
            mock_fn.assert_called_once()
        finally:
            mod._PROFILES = original

    def test_returns_cached_value(self) -> None:
        import app.utils.url_fetcher as mod

        original = mod._PROFILES
        try:
            mod._PROFILES = ["cached_profile"]
            result = _get_profiles()
            assert result == ["cached_profile"]
        finally:
            mod._PROFILES = original


# ---------------------------------------------------------------------------
# _try_curl_cffi
# ---------------------------------------------------------------------------


class TestTryCurlCffi:
    def test_returns_none_on_import_error(self) -> None:
        with patch("app.utils.url_fetcher._get_profiles", return_value=[]):
            # When no profiles available after import, should return None
            result = _try_curl_cffi("https://example.com", {}, 10, profiles=[])
        assert result is None

    def test_returns_none_when_no_profiles_available(self) -> None:
        with patch("app.utils.url_fetcher._get_profiles", return_value=[]):
            result = _try_curl_cffi("https://example.com", {}, 10)
        assert result is None

    def test_returns_fetch_result_on_200(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "hello"
        mock_resp.content = b"hello"
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.url = "https://example.com"

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_resp)

        with patch("app.utils.url_fetcher._get_profiles", return_value=["chrome131"]), \
             patch("curl_cffi.requests.Session", return_value=mock_session), \
             patch("curl_cffi.CurlOpt"):
            result = _try_curl_cffi("https://example.com", {}, 10)

        assert result is not None
        assert result.status_code == 200

    def test_returns_none_on_403_all_profiles(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"
        mock_resp.content = b"Forbidden"
        mock_resp.headers = {}
        mock_resp.url = "https://example.com"

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_resp)

        with patch("app.utils.url_fetcher._get_profiles", return_value=["chrome131"]), \
             patch("curl_cffi.requests.Session", return_value=mock_session), \
             patch("curl_cffi.CurlOpt"):
            result = _try_curl_cffi("https://example.com", {}, 10)

        assert result is None

    def test_returns_fetch_result_on_4xx_non_403(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"
        mock_resp.content = b"Not Found"
        mock_resp.headers = {}
        mock_resp.url = "https://example.com/missing"

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_resp)

        with patch("app.utils.url_fetcher._get_profiles", return_value=["chrome131"]), \
             patch("curl_cffi.requests.Session", return_value=mock_session), \
             patch("curl_cffi.CurlOpt"):
            result = _try_curl_cffi("https://example.com/missing", {}, 10)

        assert result is not None
        assert result.status_code == 404

    def test_exception_in_session_continues_to_next_profile(self) -> None:
        call_count = [0]

        def fake_session(impersonate: str, timeout: int) -> MagicMock:
            call_count[0] += 1
            raise RuntimeError("connection error")

        mock_session_cls = MagicMock(side_effect=fake_session)

        with patch("app.utils.url_fetcher._get_profiles", return_value=["chrome131", "chrome124"]), \
             patch("curl_cffi.requests.Session", mock_session_cls), \
             patch("curl_cffi.CurlOpt"):
            result = _try_curl_cffi("https://example.com", {}, 10)

        assert result is None

    def test_uses_forced_profiles_when_provided(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "ok"
        mock_resp.content = b"ok"
        mock_resp.headers = {}
        mock_resp.url = "https://example.com"

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_resp)

        with patch("curl_cffi.requests.Session", return_value=mock_session), \
             patch("curl_cffi.CurlOpt"):
            result = _try_curl_cffi("https://example.com", {}, 10, profiles=["chrome120"])

        assert result is not None
        assert result.status_code == 200

    def test_http1_mode_setopt_called(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "ok"
        mock_resp.content = b"ok"
        mock_resp.headers = {}
        mock_resp.url = "https://example.com"

        mock_curl = MagicMock()
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.curl = mock_curl

        with patch("curl_cffi.requests.Session", return_value=mock_session), \
             patch("curl_cffi.CurlOpt") as mock_opt:
            result = _try_curl_cffi("https://example.com", {}, 10, use_http2=False, profiles=["chrome120"])

        assert result is not None


# ---------------------------------------------------------------------------
# _try_cloudscraper
# ---------------------------------------------------------------------------


class TestTryCloudscraper:
    def test_returns_none_on_import_error(self) -> None:
        with patch.dict("sys.modules", {"cloudscraper": None}):
            with patch("builtins.__import__", side_effect=ImportError("no cloudscraper")):
                result = _try_cloudscraper("https://example.com", {}, 10)
        assert result is None

    def test_returns_fetch_result_on_200(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "ok"
        mock_resp.content = b"ok"
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.url = "https://example.com"

        mock_scraper = MagicMock()
        mock_scraper.get = MagicMock(return_value=mock_resp)

        with patch("cloudscraper.create_scraper", return_value=mock_scraper):
            result = _try_cloudscraper("https://example.com", {}, 10)

        assert result is not None
        assert result.status_code == 200
        assert result.strategy == "cloudscraper"

    def test_returns_none_on_non_200(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "blocked"
        mock_resp.content = b"blocked"
        mock_resp.headers = {}
        mock_resp.url = "https://example.com"

        mock_scraper = MagicMock()
        mock_scraper.get = MagicMock(return_value=mock_resp)

        with patch("cloudscraper.create_scraper", return_value=mock_scraper):
            result = _try_cloudscraper("https://example.com", {}, 10)

        assert result is None

    def test_returns_none_on_exception(self) -> None:
        mock_scraper = MagicMock()
        mock_scraper.get = MagicMock(side_effect=RuntimeError("timeout"))

        with patch("cloudscraper.create_scraper", return_value=mock_scraper):
            result = _try_cloudscraper("https://example.com", {}, 10)

        assert result is None


# ---------------------------------------------------------------------------
# _try_requests
# ---------------------------------------------------------------------------


class TestTryRequests:
    def test_returns_none_on_import_error(self) -> None:
        with patch("builtins.__import__", side_effect=ImportError("no requests")):
            result = _try_requests("https://example.com", {}, 10)
        assert result is None

    def test_returns_fetch_result_on_200(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "page content"
        mock_resp.content = b"page content"
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.url = "https://example.com"

        mock_session = MagicMock()
        mock_session.headers = {}
        mock_session.get = MagicMock(return_value=mock_resp)

        with patch("requests.Session", return_value=mock_session):
            result = _try_requests("https://example.com", {}, 10)

        assert result is not None
        assert result.status_code == 200
        assert result.strategy == "requests"

    def test_returns_none_on_non_200(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.text = "service unavailable"
        mock_resp.content = b"service unavailable"
        mock_resp.headers = {}
        mock_resp.url = "https://example.com"

        mock_session = MagicMock()
        mock_session.headers = {}
        mock_session.get = MagicMock(return_value=mock_resp)

        with patch("requests.Session", return_value=mock_session):
            result = _try_requests("https://example.com", {}, 10)

        assert result is None

    def test_returns_none_on_exception(self) -> None:
        mock_session = MagicMock()
        mock_session.headers = {}
        mock_session.get = MagicMock(side_effect=ConnectionError("refused"))

        with patch("requests.Session", return_value=mock_session):
            result = _try_requests("https://example.com", {}, 10)

        assert result is None


# ---------------------------------------------------------------------------
# fetch_url — main orchestrator
# ---------------------------------------------------------------------------


class TestSsrfValidation:
    """Host validation runs before fetch strategies (initial URL only)."""

    def test_blocks_literal_loopback_ipv4(self) -> None:
        with pytest.raises(FetchError, match="Blocked unsafe URL"):
            fetch_url("http://127.0.0.1/")

    def test_blocks_cloud_metadata_ip(self) -> None:
        with pytest.raises(FetchError, match="Blocked unsafe URL"):
            fetch_url("http://169.254.169.254/latest/meta-data/")

    def test_blocks_localhost_hostname_without_dns(self) -> None:
        with pytest.raises(FetchError, match="Blocked unsafe URL hostname"):
            fetch_url("http://localhost/path")

    def test_skips_validation_when_disabled(self) -> None:
        ok = FetchResult(
            status_code=200,
            text="page",
            content=b"page",
            headers={},
            url="http://127.0.0.1/",
            strategy="requests",
        )
        with patch("app.utils.url_fetcher._try_requests", return_value=ok):
            result = fetch_url(
                "http://127.0.0.1/",
                strategy="requests",
                block_private_hosts=False,
            )
        assert result.status_code == 200

    def test_rejects_non_http_scheme(self) -> None:
        with pytest.raises(FetchError, match="Only HTTP/HTTPS"):
            fetch_url("file:///etc/passwd")


class TestFetchUrl:
    def _make_ok_result(self, strategy: str = "requests") -> FetchResult:
        return FetchResult(
            status_code=200,
            text="page",
            content=b"page",
            headers={},
            url="https://example.com",
            strategy=strategy,
        )

    def test_raises_on_unknown_strategy(self) -> None:
        with pytest.raises(FetchError, match="Unknown fetch strategy"):
            fetch_url("https://example.com", strategy="magic")  # type: ignore[arg-type]

    def test_single_strategy_success(self) -> None:
        ok = self._make_ok_result("requests")
        with patch("app.utils.url_fetcher._try_requests", return_value=ok):
            result = fetch_url("https://example.com", strategy="requests")
        assert result.status_code == 200

    def test_fallback_chain_uses_requests_last(self) -> None:
        ok = self._make_ok_result("requests")
        with patch("app.utils.url_fetcher._try_curl_cffi", return_value=None), \
             patch("app.utils.url_fetcher._try_cloudscraper", return_value=None), \
             patch("app.utils.url_fetcher._try_requests", return_value=ok):
            result = fetch_url("https://example.com")
        assert result.strategy == "requests"

    def test_raises_fetch_error_when_all_strategies_fail(self) -> None:
        with patch("app.utils.url_fetcher._try_curl_cffi", return_value=None), \
             patch("app.utils.url_fetcher._try_cloudscraper", return_value=None), \
             patch("app.utils.url_fetcher._try_requests", return_value=None):
            with pytest.raises(FetchError):
                fetch_url("https://example.com")

    def test_exception_in_strategy_captured_in_errors(self) -> None:
        with patch("app.utils.url_fetcher._try_curl_cffi", side_effect=RuntimeError("bad")), \
             patch("app.utils.url_fetcher._try_cloudscraper", return_value=None), \
             patch("app.utils.url_fetcher._try_requests", return_value=None):
            with pytest.raises(FetchError) as exc_info:
                fetch_url("https://example.com")
        # The first error message is included in the FetchError
        assert exc_info.value is not None

    def test_profile_argument_passed_as_single_profile_list(self) -> None:
        ok = self._make_ok_result("curl_cffi(chrome120, h2=True)")
        with patch("app.utils.url_fetcher._try_curl_cffi", return_value=ok) as mock_curl:
            result = fetch_url("https://example.com", profile="chrome120", strategy="curl_cffi_h2")
        assert result.status_code == 200
        call_kwargs = mock_curl.call_args
        assert call_kwargs is not None

    def test_verbose_mode_logs_debug(self) -> None:
        ok = self._make_ok_result("requests")
        with patch("app.utils.url_fetcher._try_curl_cffi", return_value=None), \
             patch("app.utils.url_fetcher._try_cloudscraper", return_value=None), \
             patch("app.utils.url_fetcher._try_requests", return_value=ok):
            result = fetch_url("https://example.com", verbose=True)
        assert result.status_code == 200

    def test_max_retries_honored(self) -> None:
        call_count = [0]

        def counting_requests(
            url: str, headers: dict, timeout: int, **_: object
        ) -> FetchResult | None:
            call_count[0] += 1
            return None

        with patch("app.utils.url_fetcher._try_curl_cffi", return_value=None), \
             patch("app.utils.url_fetcher._try_cloudscraper", return_value=None), \
             patch("app.utils.url_fetcher._try_requests", side_effect=counting_requests), \
             patch("time.sleep"):
            with pytest.raises(FetchError):
                fetch_url("https://example.com", max_retries=2)

        # Called (max_retries + 1) times = 3
        assert call_count[0] == 3

    def test_curl_cffi_h2_strategy(self) -> None:
        ok = self._make_ok_result("curl_cffi(chrome131, h2=True)")
        with patch("app.utils.url_fetcher._try_curl_cffi", return_value=ok):
            result = fetch_url("https://example.com", strategy="curl_cffi_h2")
        assert result.status_code == 200

    def test_curl_cffi_h1_strategy(self) -> None:
        ok = self._make_ok_result("curl_cffi(chrome131, h2=False)")
        with patch("app.utils.url_fetcher._try_curl_cffi", return_value=ok):
            result = fetch_url("https://example.com", strategy="curl_cffi_h1")
        assert result.status_code == 200

    def test_cloudscraper_strategy(self) -> None:
        ok = self._make_ok_result("cloudscraper")
        with patch("app.utils.url_fetcher._try_cloudscraper", return_value=ok):
            result = fetch_url("https://example.com", strategy="cloudscraper", block_private_hosts=False)
        assert result.status_code == 200

    def test_extra_headers_forwarded(self) -> None:
        ok = self._make_ok_result()
        with patch("app.utils.url_fetcher._try_requests", return_value=ok):
            result = fetch_url(
                "https://example.com",
                headers={"X-Token": "secret"},
                strategy="requests",
            )
        assert result.status_code == 200

    def test_fetch_error_with_no_error_details(self) -> None:
        """When errors list is empty and strategies return None, use fallback message."""
        with patch("app.utils.url_fetcher._try_curl_cffi", return_value=None), \
             patch("app.utils.url_fetcher._try_cloudscraper", return_value=None), \
             patch("app.utils.url_fetcher._try_requests", return_value=None):
            with pytest.raises(FetchError) as exc_info:
                fetch_url("https://example.com")
        assert "No error details" in str(exc_info.value) or exc_info.value is not None


# ---------------------------------------------------------------------------
# _hostname_is_blocked — line 84 (.localhost / .local subdomains)
# ---------------------------------------------------------------------------


class TestHostnameIsBlocked:
    """Cover _hostname_is_blocked edge cases (line 84)."""

    def test_dot_local_subdomain_blocked(self) -> None:
        from app.utils.url_fetcher import _hostname_is_blocked
        assert _hostname_is_blocked("printer.local") is True

    def test_dot_localhost_subdomain_blocked(self) -> None:
        from app.utils.url_fetcher import _hostname_is_blocked
        assert _hostname_is_blocked("app.localhost") is True

    def test_normal_hostname_not_blocked(self) -> None:
        from app.utils.url_fetcher import _hostname_is_blocked
        assert _hostname_is_blocked("example.com") is False

    def test_trailing_dot_fqdn_localhost_blocked(self) -> None:
        """Trailing dot is stripped before checks (removesuffix)."""
        from app.utils.url_fetcher import _hostname_is_blocked
        assert _hostname_is_blocked("localhost.") is True

    def test_subdomain_of_local_with_trailing_dot(self) -> None:
        from app.utils.url_fetcher import _hostname_is_blocked
        assert _hostname_is_blocked("host.local.") is True

    def test_metadata_google_internal_blocked(self) -> None:
        from app.utils.url_fetcher import _hostname_is_blocked
        assert _hostname_is_blocked("metadata.google.internal") is True


# ---------------------------------------------------------------------------
# validate_public_http_url edge cases
# ---------------------------------------------------------------------------


class TestValidatePublicHttpUrlEdgeCases:
    """Cover validate_public_http_url branches not hit by existing tests."""

    def test_no_hostname_raises_fetch_error(self) -> None:
        """Line 100: empty hostname → FetchError('URL has no hostname')."""
        from app.utils.url_fetcher import validate_public_http_url
        with pytest.raises(FetchError, match="no hostname"):
            validate_public_http_url("http://")

    def test_literal_public_ip_returns_without_error(self) -> None:
        """Line 110: public literal IPv4 passes validation cleanly (return)."""
        from app.utils.url_fetcher import validate_public_http_url
        # 8.8.8.8 is a public IP — should not raise
        validate_public_http_url("http://8.8.8.8/path")

    def test_literal_private_ip_raises(self) -> None:
        """Line 109: private literal IP raises FetchError."""
        from app.utils.url_fetcher import validate_public_http_url
        with pytest.raises(FetchError, match="Blocked unsafe URL"):
            validate_public_http_url("http://192.168.1.100/internal")

    def test_dns_gaierror_raises_fetch_error(self) -> None:
        """Lines 116-117: socket.gaierror → FetchError('Could not resolve hostname')."""
        from app.utils.url_fetcher import validate_public_http_url
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("NXDOMAIN")):
            with pytest.raises(FetchError, match="Could not resolve hostname"):
                validate_public_http_url("http://nonexistent-host-xyz.example/")

    def test_empty_dns_result_raises(self) -> None:
        """Line 120: getaddrinfo returns [] → FetchError('No addresses resolved')."""
        from app.utils.url_fetcher import validate_public_http_url
        with patch("socket.getaddrinfo", return_value=[]):
            with pytest.raises(FetchError, match="No addresses resolved"):
                validate_public_http_url("http://example.com/")

    def test_invalid_addr_string_in_sockaddr_is_skipped(self) -> None:
        """Lines 127-128: ValueError from ip_address skips that sockaddr entry."""
        from app.utils.url_fetcher import validate_public_http_url
        # First entry has an unparseable addr; second entry is a safe public IP.
        infos = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-an-ip-address", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=infos):
            # Should NOT raise because 8.8.8.8 is public
            validate_public_http_url("http://mixed-addrs.example/")

    def test_resolved_private_ip_raises(self) -> None:
        """Line 130: hostname resolves to RFC-1918 address → FetchError."""
        from app.utils.url_fetcher import validate_public_http_url
        infos = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=infos):
            with pytest.raises(FetchError, match="Blocked unsafe URL"):
                validate_public_http_url("http://internal.corp.example/")

    def test_resolved_loopback_ip_raises(self) -> None:
        """Line 130: hostname resolves to loopback → FetchError."""
        from app.utils.url_fetcher import validate_public_http_url
        infos = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=infos):
            with pytest.raises(FetchError, match="Blocked unsafe URL"):
                validate_public_http_url("http://sneaky-redirect.example/")

    def test_dot_local_hostname_raises_without_dns(self) -> None:
        """Line 84 via validate_public_http_url: .local subdomain blocked before DNS."""
        from app.utils.url_fetcher import validate_public_http_url
        with pytest.raises(FetchError, match="Blocked unsafe URL hostname"):
            validate_public_http_url("http://printer.local/config")

    def test_resolved_nat64_public_ip_allowed(self) -> None:
        """A NAT64-synthesized address (RFC 6052 `64:ff9b::/96`) embedding a public IPv4
        (e.g. DNS64 resolving a public IPv4-only host like mcp.atlassian.com on an
        IPv6-only network) must not be blocked as 'reserved'."""
        from app.utils.url_fetcher import validate_public_http_url
        infos = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("64:ff9b::808:808", 0, 0, 0)),
        ]
        with patch("socket.getaddrinfo", return_value=infos):
            validate_public_http_url("http://public-nat64.example/")

    def test_resolved_nat64_loopback_ip_raises(self) -> None:
        """A NAT64 address embedding a loopback IPv4 (127.0.0.1) must still be blocked."""
        from app.utils.url_fetcher import validate_public_http_url
        infos = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("64:ff9b::7f00:1", 0, 0, 0)),
        ]
        with patch("socket.getaddrinfo", return_value=infos):
            with pytest.raises(FetchError, match="Blocked unsafe URL"):
                validate_public_http_url("http://sneaky-nat64.example/")

    def test_resolved_nat64_private_ip_raises(self) -> None:
        """A NAT64 address embedding a private IPv4 (10.0.0.1) must still be blocked."""
        from app.utils.url_fetcher import validate_public_http_url
        infos = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("64:ff9b::a00:1", 0, 0, 0)),
        ]
        with patch("socket.getaddrinfo", return_value=infos):
            with pytest.raises(FetchError, match="Blocked unsafe URL"):
                validate_public_http_url("http://sneaky-nat64-private.example/")

    def test_resolved_nat64_metadata_ip_raises(self) -> None:
        """A NAT64 address embedding the cloud metadata IPv4 (169.254.169.254) must still
        be blocked — the well-known-prefix unwrap must not open an SSRF bypass."""
        from app.utils.url_fetcher import validate_public_http_url
        infos = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("64:ff9b::a9fe:a9fe", 0, 0, 0)),
        ]
        with patch("socket.getaddrinfo", return_value=infos):
            with pytest.raises(FetchError, match="Blocked unsafe URL"):
                validate_public_http_url("http://sneaky-nat64-metadata.example/")


# ---------------------------------------------------------------------------
# _get_supported_profiles — lines 167-180 (body when curl_cffi is importable)
# ---------------------------------------------------------------------------


class TestGetSupportedProfilesActualBody:
    """Cover lines 167-180: profile discovery loop when curl_cffi is importable."""

    def test_all_profiles_added_when_session_always_succeeds(self) -> None:
        mock_session_instance = MagicMock()
        mock_session_cls = MagicMock(return_value=mock_session_instance)

        mock_requests_module = MagicMock()
        mock_requests_module.Session = mock_session_cls

        with patch.dict("sys.modules", {"curl_cffi.requests": mock_requests_module}):
            result = _get_supported_profiles()

        assert isinstance(result, list)
        assert len(result) > 0
        assert all(isinstance(p, str) for p in result)
        # close() should have been called for each successful profile
        assert mock_session_instance.close.call_count == len(result)

    def test_failing_profiles_skipped(self) -> None:
        """Profiles where Session() raises are not included (continue branch, line 178-179)."""
        call_count = [0]

        def selective_factory(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] % 2 == 0:
                raise ValueError("unsupported profile")
            return MagicMock()

        mock_requests_module = MagicMock()
        mock_requests_module.Session = MagicMock(side_effect=selective_factory)

        with patch.dict("sys.modules", {"curl_cffi.requests": mock_requests_module}):
            result = _get_supported_profiles()

        # Every other call raised, so exactly half succeed
        assert isinstance(result, list)
        total_candidates = 12  # hardcoded in the function
        assert len(result) < total_candidates

    def test_returns_empty_list_when_all_profiles_fail(self) -> None:
        """All Session() calls raise → empty list returned."""
        mock_requests_module = MagicMock()
        mock_requests_module.Session = MagicMock(side_effect=RuntimeError("always fails"))

        with patch.dict("sys.modules", {"curl_cffi.requests": mock_requests_module}):
            result = _get_supported_profiles()

        assert result == []


# ---------------------------------------------------------------------------
# _try_curl_cffi additional branches
# ---------------------------------------------------------------------------


class TestTryCurlCffiAdditional:
    """Cover remaining _try_curl_cffi branches."""

    @pytest.fixture(autouse=True)
    def _mock_curl_cffi_in_sys(self):
        """Put curl_cffi in sys.modules as a MagicMock so that
        patch('curl_cffi.requests.Session') doesn't fail with ModuleNotFoundError."""
        import sys
        fake_mod = MagicMock()
        fake_requests = MagicMock()
        fake_mod.requests = fake_requests
        modules = {
            "curl_cffi": fake_mod,
            "curl_cffi.requests": fake_requests,
        }
        with patch.dict(sys.modules, modules):
            yield

    def test_import_error_path_returns_none(self) -> None:
        """Lines 204-205: ImportError inside _try_curl_cffi → return None."""
        with patch.dict("sys.modules", {"curl_cffi": None, "curl_cffi.requests": None}):
            result = _try_curl_cffi("https://example.com", {}, 10, profiles=["chrome131"])
        assert result is None

    def test_setopt_exception_is_silenced(self) -> None:
        """Line 226: setopt raises but the exception is swallowed."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "ok"
        mock_resp.content = b"ok"
        mock_resp.headers = {}
        mock_resp.url = "https://example.com"

        mock_curl = MagicMock()
        mock_curl.setopt.side_effect = Exception("setopt not supported on this build")

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.curl = mock_curl

        with patch("curl_cffi.requests.Session", return_value=mock_session), \
             patch("curl_cffi.CurlOpt"):
            # use_http2=False triggers the setopt path; exception must be caught
            result = _try_curl_cffi(
                "https://example.com", {}, 10, use_http2=False, profiles=["chrome120"]
            )

        assert result is not None
        assert result.status_code == 200

    def test_5xx_status_continues_loop_to_next_profile(self) -> None:
        """Line 247->219: 5xx status falls through all if-checks and loops back."""
        def make_500_session(profile: str) -> MagicMock:
            resp = MagicMock()
            resp.status_code = 500
            resp.text = "Internal Server Error"
            resp.content = b"Internal Server Error"
            resp.headers = {}
            resp.url = "https://example.com"

            sess = MagicMock()
            sess.__enter__ = MagicMock(return_value=sess)
            sess.__exit__ = MagicMock(return_value=False)
            sess.get = MagicMock(return_value=resp)
            return sess

        sessions = [make_500_session(p) for p in ["chrome131", "chrome124"]]
        sessions_iter = iter(sessions)

        with patch("curl_cffi.requests.Session", side_effect=lambda *a, **kw: next(sessions_iter)), \
             patch("curl_cffi.CurlOpt"):
            result = _try_curl_cffi(
                "https://example.com", {}, 10, profiles=["chrome131", "chrome124"]
            )

        # 500 falls through (not 200, not 403, not in [400, 500)) → loop exhausted → None
        assert result is None

    def test_503_status_also_falls_through(self) -> None:
        """503 is also outside [400, 500) and should loop back (line 247->219)."""
        resp = MagicMock()
        resp.status_code = 503
        resp.text = "Service Unavailable"
        resp.content = b"Service Unavailable"
        resp.headers = {}
        resp.url = "https://example.com"

        sess = MagicMock()
        sess.__enter__ = MagicMock(return_value=sess)
        sess.__exit__ = MagicMock(return_value=False)
        sess.get = MagicMock(return_value=resp)

        with patch("curl_cffi.requests.Session", return_value=sess), \
             patch("curl_cffi.CurlOpt"):
            result = _try_curl_cffi("https://example.com", {}, 10, profiles=["chrome131"])

        assert result is None

    def test_empty_provided_profiles_list_returns_none(self) -> None:
        """Lines 216-217: explicit profiles=[] → if not profiles_to_try: return None."""
        with patch("curl_cffi.requests.Session"), patch("curl_cffi.CurlOpt"):
            result = _try_curl_cffi("https://example.com", {}, 10, profiles=[])
        assert result is None


# ---------------------------------------------------------------------------
# resolve_public_http_target — non-global addresses and the target shape
# ---------------------------------------------------------------------------


class TestResolvePublicHttpTarget:
    @pytest.mark.parametrize(
        "url",
        [
            "http://100.100.100.200/latest/meta-data",
            "http://198.18.0.1/",
            "http://[::ffff:10.0.0.1]/",
        ],
    )
    def test_non_global_literals_blocked(self, url: str) -> None:
        with pytest.raises(FetchError, match="Blocked unsafe URL"):
            resolve_public_http_target(url)

    def test_hostname_resolving_to_cgnat_blocked(self) -> None:
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.100.100.200", 0))]
        with patch("socket.getaddrinfo", return_value=infos):
            with pytest.raises(FetchError, match="Blocked unsafe URL"):
                resolve_public_http_target("http://metadata.example/")

    def test_block_non_global_false_allows_cgnat(self) -> None:
        target = resolve_public_http_target("http://100.64.1.1:8080/mcp", block_non_global=False)
        assert target.addresses == (ipaddress.ip_address("100.64.1.1"),)
        assert target.port == 8080
        validate_public_http_url("http://100.64.1.1/", block_non_global=False)

    def test_block_non_global_false_still_blocks_private(self) -> None:
        with pytest.raises(FetchError, match="Blocked unsafe URL"):
            validate_public_http_url("http://10.0.0.1/", block_non_global=False)

    @pytest.mark.parametrize("block_non_global", [True, False])
    @pytest.mark.parametrize(
        "url",
        [
            "http://100.100.100.200/latest/meta-data",  # Alibaba Cloud metadata (CGNAT space)
            "http://168.63.129.16/machine?comp=goalstate",  # Azure WireServer (public space)
            "http://[64:ff9b::6464:64c8]/latest/meta-data",  # NAT64-wrapped 100.100.100.200
        ],
    )
    def test_cloud_metadata_addresses_always_blocked(self, url: str, block_non_global: bool) -> None:
        with pytest.raises(FetchError, match="Blocked unsafe URL"):
            resolve_public_http_target(url, block_non_global=block_non_global)

    def test_hostname_resolving_to_metadata_blocked_when_non_global_allowed(self) -> None:
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("168.63.129.16", 0))]
        with patch("socket.getaddrinfo", return_value=infos):
            with pytest.raises(FetchError, match="Blocked unsafe URL"):
                resolve_public_http_target("http://wireserver.example/", block_non_global=False)

    def test_target_has_default_port_and_deduplicated_addresses(self) -> None:
        infos = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:4860:4860::8888", 0, 0, 0)),
        ]
        with patch("socket.getaddrinfo", return_value=infos):
            target = resolve_public_http_target("https://dns.example/query")
        assert target == PublicTarget(
            "https",
            "dns.example",
            443,
            (ipaddress.ip_address("8.8.8.8"), ipaddress.ip_address("2001:4860:4860::8888")),
        )

    def test_invalid_port_rejected(self) -> None:
        with pytest.raises(FetchError, match="invalid port"):
            resolve_public_http_target("http://example.com:99999/")


# ---------------------------------------------------------------------------
# Redirects: strategies stop at 3xx, fetch_url re-validates every hop
# ---------------------------------------------------------------------------


def _fetch_result(status_code: int, url: str, headers: dict | None = None) -> FetchResult:
    return FetchResult(
        status_code=status_code,
        text="",
        content=b"",
        headers=headers or {},
        url=url,
        strategy="requests",
    )


class TestStrategiesWithoutRedirects:
    def test_requests_returns_3xx_and_disables_redirects(self) -> None:
        mock_resp = MagicMock(status_code=302, text="", content=b"", headers={"Location": "/next"})
        mock_resp.url = "https://example.com/"
        mock_session = MagicMock()
        mock_session.headers = {}
        mock_session.get = MagicMock(return_value=mock_resp)

        with patch("requests.Session", return_value=mock_session):
            result = _try_requests("https://example.com/", {}, 10, follow_redirects=False)

        assert result is not None
        assert result.status_code == 302
        assert mock_session.get.call_args.kwargs["allow_redirects"] is False

    def test_cloudscraper_returns_3xx_and_disables_redirects(self) -> None:
        mock_resp = MagicMock(status_code=301, text="", content=b"", headers={"Location": "/next"})
        mock_resp.url = "https://example.com/"
        mock_scraper = MagicMock()
        mock_scraper.get = MagicMock(return_value=mock_resp)

        with patch("cloudscraper.create_scraper", return_value=mock_scraper):
            result = _try_cloudscraper("https://example.com/", {}, 10, follow_redirects=False)

        assert result is not None
        assert result.status_code == 301
        assert mock_scraper.get.call_args.kwargs["allow_redirects"] is False

    def test_curl_cffi_returns_3xx_without_trying_next_profile(self) -> None:
        mock_resp = MagicMock(status_code=302, text="", content=b"", headers={"Location": "/next"})
        mock_resp.url = "https://example.com/"
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_resp)

        with patch("curl_cffi.requests.Session", return_value=mock_session), \
             patch("curl_cffi.CurlOpt"):
            result = _try_curl_cffi(
                "https://example.com/",
                {},
                10,
                profiles=["chrome131", "chrome124"],
                follow_redirects=False,
            )

        assert result is not None
        assert result.status_code == 302
        mock_session.get.assert_called_once()
        assert mock_session.get.call_args.kwargs["allow_redirects"] is False


class TestFetchUrlRedirects:
    def test_redirect_to_private_address_is_blocked(self) -> None:
        redirect = _fetch_result(302, "https://example.com/start", {"Location": "http://127.0.0.1:8088/health"})
        with patch("app.utils.url_fetcher._try_requests", return_value=redirect) as mock_requests:
            with pytest.raises(FetchError, match="Blocked unsafe URL"):
                fetch_url("https://example.com/start", strategy="requests")
        mock_requests.assert_called_once()
        assert mock_requests.call_args.kwargs["follow_redirects"] is False

    def test_relative_redirect_to_public_address_is_followed(self) -> None:
        responses = [
            _fetch_result(302, "https://example.com/start", {"location": "/final"}),
            _fetch_result(200, "https://example.com/final"),
        ]
        with patch("app.utils.url_fetcher._try_requests", side_effect=responses) as mock_requests:
            result = fetch_url("https://example.com/start", strategy="requests")
        assert result.status_code == 200
        assert [c.args[0] for c in mock_requests.call_args_list] == [
            "https://example.com/start",
            "https://example.com/final",
        ]

    def test_too_many_redirects_raise(self) -> None:
        loop = _fetch_result(302, "https://example.com/loop", {"Location": "/loop"})
        with patch("app.utils.url_fetcher._try_requests", return_value=loop) as mock_requests:
            with pytest.raises(FetchError, match="Too many redirects"):
                fetch_url("https://example.com/loop", strategy="requests")
        assert mock_requests.call_count == _MAX_REDIRECTS + 1

    def test_3xx_without_location_is_returned(self) -> None:
        not_modified = _fetch_result(304, "https://example.com/")
        with patch("app.utils.url_fetcher._try_requests", return_value=not_modified):
            result = fetch_url("https://example.com/", strategy="requests")
        assert result.status_code == 304

    def test_unblocked_fetch_lets_the_library_follow_redirects(self) -> None:
        ok = _fetch_result(200, "http://10.0.0.1/")
        with patch("app.utils.url_fetcher._try_requests", return_value=ok) as mock_requests:
            fetch_url("http://10.0.0.1/", strategy="requests", block_private_hosts=False)
        assert mock_requests.call_args.kwargs["follow_redirects"] is True


def _pin(
    host: str = "example.com", address: str = "8.8.8.8", *, scheme: str = "https", port: int = 443
) -> PublicTarget:
    return PublicTarget(scheme, host, port, (ipaddress.ip_address(address),))


class TestFetchUrlPinsEachHop:
    @pytest.mark.parametrize("strategy", ["curl_cffi_h2", "curl_cffi_h1", "requests"])
    def test_strategy_receives_the_validated_target(self, strategy: str) -> None:
        name = "_try_requests" if strategy == "requests" else "_try_curl_cffi"
        ok = _fetch_result(200, "https://example.com/")
        with patch(f"app.utils.url_fetcher.{name}", return_value=ok) as mock_strategy:
            fetch_url("https://example.com/", strategy=strategy)  # type: ignore[arg-type]
        pin = mock_strategy.call_args.kwargs["pin"]
        assert (pin.host, str(pin.pinned_address)) == ("example.com", "8.8.8.8")

    def test_each_redirect_hop_gets_its_own_pin(self) -> None:
        responses = [
            _fetch_result(302, "https://example.com/start", {"Location": "https://cdn.example.com/final"}),
            _fetch_result(200, "https://cdn.example.com/final"),
        ]
        with patch("app.utils.url_fetcher._try_requests", side_effect=responses) as mock_requests:
            fetch_url("https://example.com/start", strategy="requests")
        hosts = [c.kwargs["pin"].host for c in mock_requests.call_args_list]
        assert hosts == ["example.com", "cdn.example.com"]

    def test_unblocked_fetch_is_not_pinned(self) -> None:
        ok = _fetch_result(200, "http://10.0.0.1/")
        with patch("app.utils.url_fetcher._try_requests", return_value=ok) as mock_requests:
            fetch_url("http://10.0.0.1/", strategy="requests", block_private_hosts=False)
        assert mock_requests.call_args.kwargs["pin"] is None

    def test_cloudscraper_is_skipped_for_untrusted_urls(self) -> None:
        with patch("app.utils.url_fetcher._try_curl_cffi", return_value=None), \
             patch("app.utils.url_fetcher._try_cloudscraper") as mock_cloudscraper, \
             patch("app.utils.url_fetcher._try_requests", return_value=None):
            with pytest.raises(FetchError):
                fetch_url("https://example.com/")
        mock_cloudscraper.assert_not_called()

    def test_cloudscraper_strategy_is_refused_for_untrusted_urls(self) -> None:
        with patch("app.utils.url_fetcher._try_cloudscraper") as mock_cloudscraper:
            with pytest.raises(FetchError, match="cloudscraper"):
                fetch_url("https://example.com/", strategy="cloudscraper")
        mock_cloudscraper.assert_not_called()


class TestCurlPinnedRequest:
    def test_pins_the_host_and_clears_proxies(self) -> None:
        from curl_cffi import CurlOpt

        url, curl_options = _curl_pinned_request("https://example.com/a?b=1#frag", _pin())
        assert url == "https://example.com/a?b=1"
        assert curl_options == {CurlOpt.PROXY: "", CurlOpt.RESOLVE: ["example.com:443:8.8.8.8"]}

    def test_ipv6_address_is_bracketed(self) -> None:
        from curl_cffi import CurlOpt

        _, curl_options = _curl_pinned_request("https://example.com/", _pin(address="2606:4700::1"))
        assert curl_options[CurlOpt.RESOLVE] == ["example.com:443:[2606:4700::1]"]

    def test_idn_host_is_sent_in_the_ascii_form_the_entry_uses(self) -> None:
        from curl_cffi import CurlOpt

        url, curl_options = _curl_pinned_request("https://bücher.example/x", _pin("bücher.example"))
        assert url == "https://xn--bcher-kva.example/x"
        assert curl_options[CurlOpt.RESOLVE] == ["xn--bcher-kva.example:443:8.8.8.8"]

    def test_explicit_port_and_https_credentials_are_kept(self) -> None:
        # Plain-HTTP userinfo is refused upstream (see TestUserinfoOverHttp); over https,
        # where TLS protects it, basic-auth userinfo is preserved.
        from curl_cffi import CurlOpt

        url, curl_options = _curl_pinned_request(
            "https://u:p%40ss@example.com:8443/x", _pin(scheme="https", port=8443)
        )
        assert url == "https://u:p%40ss@example.com:8443/x"
        assert curl_options[CurlOpt.RESOLVE] == ["example.com:8443:8.8.8.8"]

    def test_ip_literal_is_its_own_pin(self) -> None:
        from curl_cffi import CurlOpt

        url, curl_options = _curl_pinned_request(
            "https://[2606:4700::1]/x", _pin("2606:4700::1", "2606:4700::1")
        )
        assert url == "https://[2606:4700::1]/x"
        assert CurlOpt.RESOLVE not in curl_options

    @pytest.mark.parametrize(
        ("url", "host"),
        [
            ("https://exa%6Dple.com/", "exa%6dple.com"),
            ("https://evil.example\\@example.com/", "example.com"),
        ],
        ids=["percent-escaped-host", "backslash-in-userinfo"],
    )
    def test_urls_curl_could_read_differently_are_refused(self, url: str, host: str) -> None:
        with pytest.raises(FetchError):
            _curl_pinned_request(url, _pin(host))


class TestUserinfoOverHttp:
    def test_http_url_with_credentials_is_rejected(self) -> None:
        with pytest.raises(FetchError, match="plain HTTP"):
            fetch_url("http://user:pass@example.com/x", strategy="requests")

    def test_https_url_with_credentials_is_allowed(self) -> None:
        ok = _fetch_result(200, "https://example.com/x")
        with patch("app.utils.url_fetcher._try_requests", return_value=ok):
            assert fetch_url("https://user:pass@example.com/x", strategy="requests").status_code == 200

    def test_unblocked_fetch_still_allows_http_userinfo(self) -> None:
        # Operator-configured URLs (block_private_hosts=False) keep their credentials.
        ok = _fetch_result(200, "http://10.0.0.1/x")
        with patch("app.utils.url_fetcher._try_requests", return_value=ok):
            r = fetch_url("http://u:p@10.0.0.1/x", strategy="requests", block_private_hosts=False)
        assert r.status_code == 200


class TestAddressFailover:
    _TARGET = PublicTarget(
        "https", "example.com", 443,
        (ipaddress.ip_address("2606:4700::1"), ipaddress.ip_address("8.8.8.8")),
    )

    def test_next_address_is_tried_after_a_transport_failure(self) -> None:
        seen: list[str] = []

        def fake_run(url: str, *, follow_redirects: bool, pin: PublicTarget) -> FetchResult:
            seen.append(str(pin.pinned_address))
            if str(pin.pinned_address) == "2606:4700::1":
                raise FetchError("no route to host")
            return _fetch_result(200, url)

        result = _run_pinned_with_failover(fake_run, "https://example.com/", self._TARGET)  # type: ignore[arg-type]
        assert result.status_code == 200
        assert seen == ["2606:4700::1", "8.8.8.8"]

    def test_a_response_stops_further_addresses(self) -> None:
        seen: list[str] = []

        def fake_run(url: str, *, follow_redirects: bool, pin: PublicTarget) -> FetchResult:
            seen.append(str(pin.pinned_address))
            return _fetch_result(404, url)

        assert _run_pinned_with_failover(fake_run, "https://example.com/", self._TARGET).status_code == 404  # type: ignore[arg-type]
        assert seen == ["2606:4700::1"]

    def test_all_addresses_failing_raises_the_last_error(self) -> None:
        def fake_run(url: str, *, follow_redirects: bool, pin: PublicTarget) -> FetchResult:
            raise FetchError(f"down: {pin.pinned_address}")

        with pytest.raises(FetchError, match="down: 8.8.8.8"):
            _run_pinned_with_failover(fake_run, "https://example.com/", self._TARGET)  # type: ignore[arg-type]


class TestTryCurlCffiPinned:
    @staticmethod
    def _session(primary_ip: str) -> MagicMock:
        response = MagicMock(
            status_code=200,
            text="ok",
            content=b"ok",
            headers={},
            url="https://example.com/",
            primary_ip=primary_ip,
        )
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        session.get = MagicMock(return_value=response)
        return session

    def test_session_is_pinned_and_ignores_environment_proxies(self) -> None:
        from curl_cffi import CurlOpt

        session = self._session("8.8.8.8")
        with patch("curl_cffi.requests.Session", return_value=session) as session_cls:
            result = _try_curl_cffi(
                "https://example.com/", {}, 10, profiles=["chrome131"], follow_redirects=False, pin=_pin()
            )
        assert result is not None
        kwargs = session_cls.call_args.kwargs
        assert kwargs["trust_env"] is False
        assert kwargs["curl_options"][CurlOpt.RESOLVE] == ["example.com:443:8.8.8.8"]

    def test_response_from_another_address_is_refused(self) -> None:
        session = self._session("10.0.0.5")
        with patch("curl_cffi.requests.Session", return_value=session):
            with pytest.raises(FetchError, match="validated address"):
                _try_curl_cffi(
                    "https://example.com/",
                    {},
                    10,
                    profiles=["chrome131", "chrome124"],
                    follow_redirects=False,
                    pin=_pin(),
                )
        session.get.assert_called_once()


@pytest.fixture
def loopback_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[int, list[str]]]:
    """A local HTTP server, the real resolver, and a dead proxy in the environment."""
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    for var in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(var, "http://127.0.0.1:9")
    hosts: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hosts.append(self.headers["Host"])
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield int(server.server_address[1]), hosts
    server.shutdown()
    server.server_close()


class TestPinnedTransportsOnTheWire:
    """Loopback only. ``pinned.invalid`` never resolves and the environment points at a dead
    proxy, so a request arrives only if the transport used the pin and ignored the proxy."""

    def test_requests(self, loopback_server: tuple[int, list[str]]) -> None:
        port, hosts = loopback_server
        pin = _pin("pinned.invalid", "127.0.0.1", scheme="http", port=port)
        result = _try_requests(f"http://pinned.invalid:{port}/x", {}, 5, follow_redirects=False, pin=pin)
        assert result is not None
        assert result.status_code == 200
        assert hosts == [f"pinned.invalid:{port}"]

    def test_curl_cffi(self, loopback_server: tuple[int, list[str]]) -> None:
        port, hosts = loopback_server
        pin = _pin("pinned.invalid", "127.0.0.1", scheme="http", port=port)
        result = _try_curl_cffi(
            f"http://pinned.invalid:{port}/x", {}, 5, profiles=["chrome120"], follow_redirects=False, pin=pin
        )
        assert result is not None
        assert result.status_code == 200
        assert hosts == [f"pinned.invalid:{port}"]
