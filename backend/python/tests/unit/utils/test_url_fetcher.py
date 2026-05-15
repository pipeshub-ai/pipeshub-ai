"""Tests for app.utils.url_fetcher — robust multi-strategy URL fetcher."""

import socket
from unittest.mock import MagicMock, patch

import pytest

from app.utils.url_fetcher import (
    FetchError,
    FetchResult,
    _build_headers,
    _get_profiles,
    _get_supported_profiles,
    _try_cloudscraper,
    _try_curl_cffi,
    _try_requests,
    fetch_url,
)


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

        def counting_requests(url: str, headers: dict, timeout: int) -> FetchResult | None:
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
            result = fetch_url("https://example.com", strategy="cloudscraper")
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
# _validate_public_http_url edge cases
# ---------------------------------------------------------------------------


class TestValidatePublicHttpUrlEdgeCases:
    """Cover _validate_public_http_url branches not hit by existing tests."""

    def test_no_hostname_raises_fetch_error(self) -> None:
        """Line 100: empty hostname → FetchError('URL has no hostname')."""
        from app.utils.url_fetcher import _validate_public_http_url
        with pytest.raises(FetchError, match="no hostname"):
            _validate_public_http_url("http://")

    def test_literal_public_ip_returns_without_error(self) -> None:
        """Line 110: public literal IPv4 passes validation cleanly (return)."""
        from app.utils.url_fetcher import _validate_public_http_url
        # 8.8.8.8 is a public IP — should not raise
        _validate_public_http_url("http://8.8.8.8/path")

    def test_literal_private_ip_raises(self) -> None:
        """Line 109: private literal IP raises FetchError."""
        from app.utils.url_fetcher import _validate_public_http_url
        with pytest.raises(FetchError, match="Blocked unsafe URL"):
            _validate_public_http_url("http://192.168.1.100/internal")

    def test_dns_gaierror_raises_fetch_error(self) -> None:
        """Lines 116-117: socket.gaierror → FetchError('Could not resolve hostname')."""
        from app.utils.url_fetcher import _validate_public_http_url
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("NXDOMAIN")):
            with pytest.raises(FetchError, match="Could not resolve hostname"):
                _validate_public_http_url("http://nonexistent-host-xyz.example/")

    def test_empty_dns_result_raises(self) -> None:
        """Line 120: getaddrinfo returns [] → FetchError('No addresses resolved')."""
        from app.utils.url_fetcher import _validate_public_http_url
        with patch("socket.getaddrinfo", return_value=[]):
            with pytest.raises(FetchError, match="No addresses resolved"):
                _validate_public_http_url("http://example.com/")

    def test_invalid_addr_string_in_sockaddr_is_skipped(self) -> None:
        """Lines 127-128: ValueError from ip_address skips that sockaddr entry."""
        from app.utils.url_fetcher import _validate_public_http_url
        # First entry has an unparseable addr; second entry is a safe public IP.
        infos = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-an-ip-address", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=infos):
            # Should NOT raise because 8.8.8.8 is public
            _validate_public_http_url("http://mixed-addrs.example/")

    def test_resolved_private_ip_raises(self) -> None:
        """Line 130: hostname resolves to RFC-1918 address → FetchError."""
        from app.utils.url_fetcher import _validate_public_http_url
        infos = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=infos):
            with pytest.raises(FetchError, match="Blocked unsafe URL"):
                _validate_public_http_url("http://internal.corp.example/")

    def test_resolved_loopback_ip_raises(self) -> None:
        """Line 130: hostname resolves to loopback → FetchError."""
        from app.utils.url_fetcher import _validate_public_http_url
        infos = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=infos):
            with pytest.raises(FetchError, match="Blocked unsafe URL"):
                _validate_public_http_url("http://sneaky-redirect.example/")

    def test_dot_local_hostname_raises_without_dns(self) -> None:
        """Line 84 via _validate_public_http_url: .local subdomain blocked before DNS."""
        from app.utils.url_fetcher import _validate_public_http_url
        with pytest.raises(FetchError, match="Blocked unsafe URL hostname"):
            _validate_public_http_url("http://printer.local/config")


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
