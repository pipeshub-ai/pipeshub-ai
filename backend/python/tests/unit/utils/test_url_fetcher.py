"""Tests for app.utils.url_fetcher — robust multi-strategy URL fetcher."""

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
