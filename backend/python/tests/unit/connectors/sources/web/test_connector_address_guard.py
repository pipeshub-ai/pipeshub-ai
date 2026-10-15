"""The web connector's side of the address guard: a refused page is not retried in the
browser, the browser is never handed an address the guard refuses, a refused page is shown
with its reason, and the storage call keeps its own session because the crawl session
refuses internal addresses."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, ProgressStatus
from app.connectors.sources.web import connector as web_connector
from app.connectors.sources.web.connector import (
    MAX_RETRIES,
    UNSAFE_ADDRESS_REASON,
    RetryUrl,
    WebConnector,
)
from app.connectors.sources.web.fetch_strategy import (
    FetchResponse,
    too_many_redirects_response,
    unsafe_address_response,
)


def _response(status_code: int) -> FetchResponse:
    return FetchResponse(
        status_code=status_code,
        content_bytes=b"",
        headers={},
        final_url="https://site.example/",
        strategy="aiohttp",
    )


class TestBrowserFallback:
    @pytest.mark.parametrize(
        "response",
        [unsafe_address_response("http://10.0.0.5/"), too_many_redirects_response("https://site.example/")],
        ids=["unsafe-address", "too-many-redirects"],
    )
    def test_a_refused_page_is_not_retried_in_the_browser(self, response: FetchResponse) -> None:
        assert WebConnector._should_try_crawl4ai_fallback(MagicMock(), response) is False

    @pytest.mark.parametrize(("response", "expected"), [(None, True), (_response(403), True), (_response(503), True), (_response(404), False), (_response(413), False), (_response(200), False)])
    def test_other_outcomes_keep_their_fallback_rule(self, response: FetchResponse | None, expected: bool) -> None:
        assert WebConnector._should_try_crawl4ai_fallback(MagicMock(), response) is expected


# Literal addresses and non-http schemes: the real guard refuses these without a DNS lookup.
UNSAFE_URLS = [
    "file:///etc/hostname",
    "http://127.0.0.1:8080/admin",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://10.0.0.5/",
    "ftp://files.example/a.txt",
]


def _batch_connector() -> MagicMock:
    """A connector whose pages are all plain pages that redirect nowhere."""
    connector = MagicMock()
    connector._is_document_url = lambda url: False
    connector._landing_refused_before_browser = AsyncMock(return_value=None)
    connector._refused_before_browser = lambda url: WebConnector._refused_before_browser(connector, url)
    connector._browser_got_no_answer = lambda result: False

    async def unchanged(response: object, url: str, *, no_answer: bool) -> object:
        return response

    connector._fetch_document_behind_render = unchanged
    return connector


class TestBrowserHandOff:
    """The browser opens file: URLs itself and only its http(s) connections pass the guard
    proxy, so the connector checks the address before handing one over."""

    @pytest.mark.parametrize("url", UNSAFE_URLS)
    async def test_a_single_fetch_never_hands_an_unsafe_url_to_the_browser(self, url: str) -> None:
        connector = MagicMock()
        connector.crawl4ai_fetcher.fetch = AsyncMock()

        response = await WebConnector._headless_fetch(connector, url)

        connector.crawl4ai_fetcher.fetch.assert_not_awaited()
        assert response.headers["X-Fetch-Skip-Reason"] == "unsafe_address"
        assert response.success is False

    async def test_a_batch_renders_only_the_safe_urls_and_keeps_their_order(self) -> None:
        urls = ["https://site.example/a", "file:///etc/hostname", "https://site.example/b", "http://127.0.0.1/"]
        rendered = [MagicMock(name="a"), MagicMock(name="b")]
        connector = _batch_connector()
        connector.crawl4ai_fetcher.fetch_many = AsyncMock(return_value=rendered)
        connector._crawl4ai_result_to_response = lambda result, url: (result, url)

        async def only_site_example_is_safe(url: str) -> bool:
            return not url.startswith("https://site.example/")

        with patch.object(web_connector, "is_unsafe_url", only_site_example_is_safe):
            responses = await WebConnector._headless_fetch_many(connector, urls)

        connector.crawl4ai_fetcher.fetch_many.assert_awaited_once_with([urls[0], urls[2]])
        assert responses[0] == (rendered[0], urls[0])
        assert responses[2] == (rendered[1], urls[2])
        assert [responses[i].headers["X-Fetch-Skip-Reason"] for i in (1, 3)] == ["unsafe_address"] * 2
        assert [responses[i].final_url for i in (1, 3)] == [urls[1], urls[3]]

    async def test_a_batch_of_only_unsafe_urls_does_not_call_the_browser(self) -> None:
        connector = _batch_connector()
        connector.crawl4ai_fetcher.fetch_many = AsyncMock()

        responses = await WebConnector._headless_fetch_many(connector, UNSAFE_URLS)

        connector.crawl4ai_fetcher.fetch_many.assert_not_awaited()
        assert [r.headers["X-Fetch-Skip-Reason"] for r in responses] == ["unsafe_address"] * len(UNSAFE_URLS)

    @pytest.mark.parametrize("url", UNSAFE_URLS)
    async def test_a_fetch_that_skips_the_redirect_walk_is_still_checked(self, url: str) -> None:
        """A stored record opened on demand, or a retry, goes straight to the browser."""
        connector = MagicMock()
        connector.crawl4ai_fetcher.fetch = AsyncMock()

        response = await WebConnector._headless_fetch(connector, url, walk_first=False)

        connector.crawl4ai_fetcher.fetch.assert_not_awaited()
        assert response.headers["X-Fetch-Skip-Reason"] == "unsafe_address"

    @pytest.mark.parametrize("url", UNSAFE_URLS)
    async def test_the_rendering_probe_starts_no_browser_for_an_unsafe_url(self, url: str) -> None:
        with patch.object(web_connector, "Crawl4AIFetcher") as browser:
            needs_browser = await WebConnector._detect_csr(MagicMock(), url)

        browser.assert_not_called()
        assert needs_browser is False


class TestRefusedPageIsShown:
    @staticmethod
    def _connector() -> MagicMock:
        connector = MagicMock()
        connector.retry_urls = {}
        connector.base_domain = None
        connector.url_should_contain = []
        connector._normalize_url = lambda url: url
        connector._off_site = lambda url: False
        connector._excluded_by_url_should_contain = lambda url: False
        connector._record_final_failure = lambda *args, **kwargs: WebConnector._record_final_failure(
            connector, *args, **kwargs
        )
        return connector

    async def test_a_refused_address_is_kept_as_a_failed_page_with_the_reason(self) -> None:
        connector = self._connector()
        url = "http://10.0.0.5/wiki"

        result = await WebConnector._validate_fetch_result(connector, url, 1, None, unsafe_address_response(url))

        assert result is None
        entry = connector.retry_urls[url]
        assert entry.reason == UNSAFE_ADDRESS_REASON
        assert entry.retries == MAX_RETRIES, "a refused address must not be fetched again in this sync"

    async def test_other_final_errors_carry_no_refusal_reason(self) -> None:
        connector = self._connector()

        result = await WebConnector._validate_fetch_result(
            connector, "https://site.example/gone", 1, None, _response(404)
        )

        assert result is None
        (entry,) = connector.retry_urls.values()
        assert entry.reason is None
        assert entry.status_code == 404

    async def test_the_failed_page_shows_the_refusal_reason_not_the_status_wording(self) -> None:
        refused, busy = "http://10.0.0.5/wiki", "https://site.example/busy"
        connector = MagicMock()
        connector.data_entities_processor.org_id = "org-1"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        connector._ensure_parent_records_exist = AsyncMock()
        connector._extract_title_from_url = lambda url: "page"
        connector._get_parent_url = lambda url: None
        connector._normalize_url = lambda url: url
        connector._ensure_trailing_slash = lambda url: url
        connector.connector_name = Connectors.WEB
        connector.connector_id = "connector-1"
        connector.url = refused
        connector.retry_urls = {
            refused: RetryUrl(
                url=refused, status="PENDING", status_code=400, retries=MAX_RETRIES,
                last_attempted=0, reason=UNSAFE_ADDRESS_REASON,
            ),
            busy: RetryUrl(url=busy, status="PENDING", status_code=503, retries=MAX_RETRIES, last_attempted=0),
        }
        connector._create_failed_placeholder_record = (
            lambda url, status_code, reason=None, site_url=None: WebConnector._create_failed_placeholder_record(
                connector, url, status_code, reason, site_url
            )
        )

        updates = [update async for update in WebConnector._retry_urls_generator(connector)]

        reasons = {update.record.weburl: update.record.reason for update in updates}
        assert reasons[refused] == UNSAFE_ADDRESS_REASON
        assert "503" in reasons[busy]
        assert {update.record.indexing_status for update in updates} == {ProgressStatus.FAILED.value}


class TestStorageSignedUrl:
    async def test_the_crawl_session_is_not_used_for_the_storage_call(self) -> None:
        crawl_session = MagicMock()
        crawl_session.get.side_effect = AssertionError("storage call went through the crawl session")
        connector = MagicMock()
        connector.session = crawl_session
        connector._get_storage_url = AsyncMock(return_value="http://storage.example.com:3000")
        connector._get_storage_token = AsyncMock(return_value="token")

        storage_response = MagicMock(status=200, headers={"Content-Type": "application/json"})
        storage_response.json = AsyncMock(return_value={"signedUrl": "https://signed.example/doc"})
        own_session = MagicMock()
        own_session.get.return_value.__aenter__ = AsyncMock(return_value=storage_response)
        own_session.get.return_value.__aexit__ = AsyncMock(return_value=False)
        own_session.close = AsyncMock()

        record = MagicMock(storage_document_id="doc-1")
        with patch.object(web_connector.aiohttp, "ClientSession", return_value=own_session) as session_factory:
            signed_url = await WebConnector.get_signed_url(connector, record)

        assert signed_url == "https://signed.example/doc"
        session_factory.assert_called_once()
        own_session.close.assert_awaited_once()
