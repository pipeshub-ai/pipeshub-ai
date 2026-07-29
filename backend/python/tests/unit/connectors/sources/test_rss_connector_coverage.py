"""Additional tests for RSSConnector targeting remaining uncovered lines.

Covers:
- _fetch_and_parse_feed (HTTP errors, parse warnings, timeouts, exceptions)
- _fetch_article_content (HTTP errors, content-type checks, timeouts, exceptions)
- _process_feed (batching logic, empty feeds, entry errors)
- stream_record (success, HTTP errors, non-HTML content, exceptions, no weburl)
- _process_entry (fetch_full_content=True with article fetch, HTML summary type)
"""

import asyncio
import hashlib
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.connectors.sources.rss.connector import RSSConnector
from app.connectors.sources.web.fetch_strategy import FetchResponse
from app.models.entities import FileRecord, RecordType, User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_connector():
    """Build an RSSConnector with all dependencies mocked."""
    logger = MagicMock()
    dep = MagicMock()
    dep.org_id = "org-1"
    dep.get_all_active_users = AsyncMock(return_value=[])
    _creator = User(
        email="user@test.com",
        org_id="org-1",
        source_user_id="test-user-id",
        full_name="Test User",
    )
    dep.get_user_by_user_id = AsyncMock(return_value=_creator)
    dep.on_new_app_users = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    ds_provider = MagicMock()
    config_service = AsyncMock()
    return RSSConnector(
        logger=logger,
        data_entities_processor=dep,
        data_store_provider=ds_provider,
        config_service=config_service,
        connector_id="rss-conn-1",
        scope="personal",
        created_by="test-user-id",
    )


def _make_mock_response(status=200, content=b"<html>body</html>", headers=None):
    """Build a mock aiohttp response."""
    resp = MagicMock()
    resp.status = status
    resp.read = AsyncMock(return_value=content)
    resp.headers = headers or {"Content-Type": "text/html"}
    return resp


def _make_async_context_manager(return_value):
    """Create an async context manager mock."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=return_value)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


def _make_session(response):
    """Create a mock session with get() returning a context manager."""
    session = MagicMock()
    session.get = MagicMock(return_value=_make_async_context_manager(response))
    return session


def _make_fetch_response(status=200, content=b"<html>body</html>", headers=None,
                         final_url="https://example.com/article"):
    """Build a FetchResponse as returned by fetch_url_with_fallback."""
    return FetchResponse(
        status_code=status,
        content_bytes=content,
        headers=headers or {"Content-Type": "text/html"},
        final_url=final_url,
        strategy="aiohttp",
    )


def _patch_fetch(**kwargs):
    """Patch the connector's fetch_url_with_fallback to return a FetchResponse."""
    return patch(
        "app.connectors.sources.rss.connector.fetch_url_with_fallback",
        new=AsyncMock(return_value=_make_fetch_response(**kwargs)),
    )


def _make_record(**overrides):
    """Build a minimal Record for testing."""
    defaults = {
        "id": "rec-1",
        "record_name": "Test Article",
        "record_type": RecordType.FILE,
        "external_record_id": "ext-1",
        "origin": OriginTypes.CONNECTOR,
        "connector_name": Connectors.RSS,
        "connector_id": "rss-conn-1",
        "version": 1,
        "is_file": True,
        "weburl": "https://example.com/article-1",
        "mime_type": "text/html",
    }
    defaults.update(overrides)
    return FileRecord(**defaults)


# ===================================================================
# _fetch_and_parse_feed
# ===================================================================

class TestFetchAndParseFeed:

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        conn = _make_connector()
        resp = _make_mock_response(status=404)
        conn.session = _make_session(resp)
        result = await conn._fetch_and_parse_feed("https://feed.com/rss")
        assert result is None

    @pytest.mark.asyncio
    async def test_success_returns_feed(self):
        conn = _make_connector()
        feed_xml = b"""<?xml version="1.0"?>
        <rss version="2.0">
            <channel>
                <title>Test Feed</title>
                <item>
                    <title>Article 1</title>
                    <link>https://example.com/1</link>
                </item>
            </channel>
        </rss>"""
        with _patch_fetch(status=200, content=feed_xml):
            result = await conn._fetch_and_parse_feed("https://feed.com/rss")
        assert result is not None
        assert len(result.entries) == 1

    @pytest.mark.asyncio
    async def test_bozo_feed_with_no_entries_returns_none(self):
        conn = _make_connector()
        # Return content that feedparser can parse but marks as bozo
        resp = _make_mock_response(status=200, content=b"not-valid-xml-at-all")
        conn.session = _make_session(resp)
        with patch("app.connectors.sources.rss.connector.feedparser") as mock_fp:
            mock_feed = MagicMock()
            mock_feed.bozo = True
            mock_feed.entries = []
            mock_feed.bozo_exception = Exception("parse error")
            mock_fp.parse.return_value = mock_feed
            result = await conn._fetch_and_parse_feed("https://feed.com/rss")
            assert result is None

    @pytest.mark.asyncio
    async def test_bozo_feed_with_entries_returns_feed(self):
        conn = _make_connector()
        with _patch_fetch(status=200, content=b"<xml>something</xml>"):
            with patch("app.connectors.sources.rss.connector.feedparser") as mock_fp:
                mock_feed = MagicMock()
                mock_feed.bozo = True
                mock_feed.entries = [{"title": "Article"}]
                mock_fp.parse.return_value = mock_feed
                result = await conn._fetch_and_parse_feed("https://feed.com/rss")
                assert result is not None

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        conn = _make_connector()
        session = MagicMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
        cm.__aexit__ = AsyncMock(return_value=None)
        session.get = MagicMock(return_value=cm)
        conn.session = session
        result = await conn._fetch_and_parse_feed("https://feed.com/rss")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        conn = _make_connector()
        session = MagicMock()
        session.get = MagicMock(side_effect=Exception("network error"))
        conn.session = session
        result = await conn._fetch_and_parse_feed("https://feed.com/rss")
        assert result is None


# ===================================================================
# _fetch_article_content
# ===================================================================

class TestFetchArticleContent:

    @pytest.mark.asyncio
    async def test_success_returns_text(self):
        conn = _make_connector()
        with _patch_fetch(status=200, content=b"<html><body>Hello world</body></html>",
                          headers={"Content-Type": "text/html; charset=utf-8"}):
            with patch.object(conn, "_extract_text_content", return_value="Hello world"):
                result = await conn._fetch_article_content("https://example.com/article")
                assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self):
        conn = _make_connector()
        resp = _make_mock_response(status=500)
        conn.session = _make_session(resp)
        result = await conn._fetch_article_content("https://example.com/article")
        assert result == ""

    @pytest.mark.asyncio
    async def test_non_html_content_type_returns_empty(self):
        conn = _make_connector()
        resp = _make_mock_response(status=200, content=b"binary data")
        resp.headers = {"Content-Type": "application/pdf"}
        conn.session = _make_session(resp)
        result = await conn._fetch_article_content("https://example.com/file.pdf")
        assert result == ""

    @pytest.mark.asyncio
    async def test_xml_content_type_succeeds(self):
        conn = _make_connector()
        with _patch_fetch(status=200, content=b"<xml>data</xml>",
                          headers={"Content-Type": "application/xml"}):
            with patch.object(conn, "_extract_text_content", return_value="data"):
                result = await conn._fetch_article_content("https://example.com/feed.xml")
                assert result == "data"

    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self):
        conn = _make_connector()
        session = MagicMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
        cm.__aexit__ = AsyncMock(return_value=None)
        session.get = MagicMock(return_value=cm)
        conn.session = session
        result = await conn._fetch_article_content("https://example.com/article")
        assert result == ""

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        conn = _make_connector()
        session = MagicMock()
        session.get = MagicMock(side_effect=Exception("connection error"))
        conn.session = session
        result = await conn._fetch_article_content("https://example.com/article")
        assert result == ""

    @pytest.mark.asyncio
    async def test_missing_content_type_header(self):
        conn = _make_connector()
        resp = _make_mock_response(status=200, content=b"<html>body</html>")
        mock_headers = MagicMock()
        mock_headers.get = MagicMock(return_value="")
        resp.headers = mock_headers
        conn.session = _make_session(resp)
        result = await conn._fetch_article_content("https://example.com/article")
        assert result == ""  # Empty content-type doesn't contain 'html' or 'xml'


# ===================================================================
# _process_feed
# ===================================================================

class TestProcessFeed:

    @pytest.mark.asyncio
    async def test_no_entries_returns_zero(self):
        conn = _make_connector()
        conn.session = MagicMock()
        with patch.object(conn, "create_record_group", new_callable=AsyncMock):
            with patch.object(conn, "_fetch_and_parse_feed", new_callable=AsyncMock, return_value=None):
                result = await conn._process_feed("https://feed.com/rss", [])
                assert result == 0

    @pytest.mark.asyncio
    async def test_empty_entries_returns_zero(self):
        conn = _make_connector()
        conn.session = MagicMock()
        mock_feed = MagicMock()
        mock_feed.entries = []
        mock_feed.feed = {"title": "Test Feed"}
        with patch.object(conn, "create_record_group", new_callable=AsyncMock):
            with patch.object(conn, "_fetch_and_parse_feed", new_callable=AsyncMock, return_value=mock_feed):
                result = await conn._process_feed("https://feed.com/rss", [])
                assert result == 0

    @pytest.mark.asyncio
    async def test_processes_entries_and_flushes_batch(self):
        conn = _make_connector()
        conn.batch_size = 2  # Small batch for testing
        conn.session = MagicMock()

        mock_feed = MagicMock()
        mock_feed.entries = [
            {"title": f"Article {i}", "link": f"https://example.com/{i}", "id": f"id-{i}"}
            for i in range(5)
        ]
        mock_feed.feed = {"title": "Test Feed"}

        mock_record = MagicMock()
        mock_perms = [MagicMock()]

        with patch.object(conn, "create_record_group", new_callable=AsyncMock):
            with patch.object(conn, "_fetch_and_parse_feed", new_callable=AsyncMock, return_value=mock_feed):
                with patch.object(conn, "_process_entry", new_callable=AsyncMock, return_value=(mock_record, mock_perms)):
                    result = await conn._process_feed("https://feed.com/rss", [])
                    assert result == 5
                    # Should have flushed: 2 batches of 2 + 1 final batch of 1
                    assert conn.data_entities_processor.on_new_records.await_count == 3

    @pytest.mark.asyncio
    async def test_entry_processing_error_continues(self):
        conn = _make_connector()
        conn.session = MagicMock()

        mock_feed = MagicMock()
        mock_feed.entries = [
            {"title": "Article 1", "link": "https://example.com/1", "id": "id-1"},
            {"title": "Article 2", "link": "https://example.com/2", "id": "id-2"},
        ]
        mock_feed.feed = {"title": "Test Feed"}

        mock_record = MagicMock()
        mock_perms = [MagicMock()]

        with patch.object(conn, "create_record_group", new_callable=AsyncMock):
            with patch.object(conn, "_fetch_and_parse_feed", new_callable=AsyncMock, return_value=mock_feed):
                with patch.object(
                    conn, "_process_entry", new_callable=AsyncMock,
                    side_effect=[Exception("parse error"), (mock_record, mock_perms)]
                ):
                    result = await conn._process_feed("https://feed.com/rss", [])
                    assert result == 1  # Only second entry succeeded

    @pytest.mark.asyncio
    async def test_process_entry_returns_none_skipped(self):
        conn = _make_connector()
        conn.session = MagicMock()

        mock_feed = MagicMock()
        mock_feed.entries = [{"title": "Article", "link": "https://ex.com/1", "id": "1"}]
        mock_feed.feed = {"title": "Feed"}

        with patch.object(conn, "create_record_group", new_callable=AsyncMock):
            with patch.object(conn, "_fetch_and_parse_feed", new_callable=AsyncMock, return_value=mock_feed):
                with patch.object(conn, "_process_entry", new_callable=AsyncMock, return_value=None):
                    result = await conn._process_feed("https://feed.com/rss", [])
                    assert result == 0

    @pytest.mark.asyncio
    async def test_max_articles_limit_applied(self):
        conn = _make_connector()
        conn.max_articles_per_feed = 2
        conn.session = MagicMock()

        mock_feed = MagicMock()
        mock_feed.entries = [
            {"title": f"Art {i}", "link": f"https://ex.com/{i}", "id": f"id-{i}"}
            for i in range(10)
        ]
        mock_feed.feed = {"title": "Feed"}

        mock_record = MagicMock()
        mock_perms = [MagicMock()]

        with patch.object(conn, "create_record_group", new_callable=AsyncMock):
            with patch.object(conn, "_fetch_and_parse_feed", new_callable=AsyncMock, return_value=mock_feed):
                with patch.object(conn, "_process_entry", new_callable=AsyncMock, return_value=(mock_record, mock_perms)):
                    result = await conn._process_feed("https://feed.com/rss", [])
                    assert result == 2  # Limited to max_articles_per_feed


# ===================================================================
# _process_entry - fetch_full_content path
# ===================================================================

class TestProcessEntryFetchFullContent:

    @pytest.mark.asyncio
    async def test_fetch_full_content_enabled_fetches_article(self):
        conn = _make_connector()
        conn.fetch_full_content = True
        entry = {
            "title": "Test",
            "link": "https://example.com/article-new",
            "id": "guid-new",
        }
        with patch.object(conn, "_fetch_article_content", new_callable=AsyncMock, return_value="Full article text"):
            result = await conn._process_entry(entry, "https://feed.com/rss")
            assert result is not None
            record, _ = result
            # Content should include "Full article text"
            assert record.size_in_bytes > 0

    @pytest.mark.asyncio
    async def test_fetch_full_content_falls_back_to_summary(self):
        conn = _make_connector()
        conn.fetch_full_content = True
        entry = {
            "title": "Test",
            "link": "https://example.com/article-fb",
            "id": "guid-fb",
            "summary": "Fallback summary text",
        }
        with patch.object(conn, "_fetch_article_content", new_callable=AsyncMock, return_value=""):
            result = await conn._process_entry(entry, "https://feed.com/rss")
            assert result is not None
            record, _ = result
            assert record.size_in_bytes > 0

    @pytest.mark.asyncio
    async def test_html_summary_with_xhtml_type(self):
        conn = _make_connector()
        conn.fetch_full_content = False
        entry = {
            "title": "Test",
            "link": "https://example.com/article-xhtml",
            "id": "guid-xhtml",
            "summary": "<p>XHTML summary</p>",
            "summary_detail": {"type": "application/xhtml+xml"},
        }
        with patch.object(conn, "_extract_text_content", return_value="XHTML summary"):
            result = await conn._process_entry(entry, "https://feed.com/rss")
            assert result is not None

    @pytest.mark.asyncio
    async def test_plain_text_summary_used_directly(self):
        conn = _make_connector()
        conn.fetch_full_content = False
        entry = {
            "title": "Test",
            "link": "https://example.com/article-plain",
            "id": "guid-plain",
            "summary": "Plain text summary",
            "summary_detail": {"type": "text/plain"},
        }
        result = await conn._process_entry(entry, "https://feed.com/rss")
        assert result is not None
        record, _ = result
        expected_bytes = "Plain text summary".encode("utf-8")
        assert record.size_in_bytes == len(expected_bytes)


# ===================================================================
# stream_record
# ===================================================================

class TestStreamRecord:

    @pytest.mark.asyncio
    async def test_no_weburl_no_feed_raises(self):
        # No article URL and no feed to reparse -> content unresolvable -> 502.
        conn = _make_connector()
        record = _make_record(weburl="")
        with pytest.raises(HTTPException):
            await conn.stream_record(record)

    @pytest.mark.asyncio
    async def test_unresolvable_content_raises(self):
        # No feed on the record, article crawl yields nothing -> 502.
        conn = _make_connector()
        record = _make_record()
        with patch.object(conn, "_fetch_article_content", new_callable=AsyncMock, return_value=""):
            with pytest.raises(HTTPException):
                await conn.stream_record(record)

    @pytest.mark.asyncio
    async def test_success_article_fallback_serves_plain_text(self):
        conn = _make_connector()
        record = _make_record()  # no external_record_group_id -> article crawl fallback
        with patch.object(conn, "_fetch_article_content", new_callable=AsyncMock, return_value="Hello world"):
            with patch("app.connectors.sources.rss.connector.create_stream_record_response") as mock_stream:
                mock_stream.return_value = MagicMock()
                result = await conn.stream_record(record)
                assert result is not None
                mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_success_resolves_from_feed_by_guid(self):
        conn = _make_connector()
        record = _make_record(
            external_record_id="guid-1",
            external_record_group_id="https://feed.com/rss",
        )
        fake_entry = {"id": "guid-1", "link": "https://example.com/article-1", "summary": "hi"}
        fake_feed = MagicMock()
        fake_feed.entries = [fake_entry]
        with patch.object(conn, "_fetch_and_parse_feed", new_callable=AsyncMock, return_value=fake_feed):
            with patch.object(conn, "_resolve_entry_text", new_callable=AsyncMock, return_value="Resolved text"):
                with patch("app.connectors.sources.rss.connector.create_stream_record_response") as mock_stream:
                    mock_stream.return_value = MagicMock()
                    result = await conn.stream_record(record)
                    assert result is not None
                    mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_success_uses_record_mime_type(self):
        conn = _make_connector()
        record = _make_record(mime_type="application/pdf")
        with patch.object(conn, "_fetch_article_content", new_callable=AsyncMock, return_value="data"):
            with patch("app.connectors.sources.rss.connector.create_stream_record_response") as mock_stream:
                mock_stream.return_value = MagicMock()
                await conn.stream_record(record)
                assert mock_stream.call_args[1]["mime_type"] == "application/pdf"

    @pytest.mark.asyncio
    async def test_success_no_mime_type_defaults_plain_text(self):
        conn = _make_connector()
        record = _make_record()
        record.mime_type = None  # Set to None after creation to bypass validation
        with patch.object(conn, "_fetch_article_content", new_callable=AsyncMock, return_value="data"):
            with patch("app.connectors.sources.rss.connector.create_stream_record_response") as mock_stream:
                mock_stream.return_value = MagicMock()
                await conn.stream_record(record)
                assert mock_stream.call_args[1]["mime_type"] == MimeTypes.PLAIN_TEXT.value

    @pytest.mark.asyncio
    async def test_exception_propagates(self):
        conn = _make_connector()
        record = _make_record()
        with patch.object(conn, "_fetch_article_content", new_callable=AsyncMock, side_effect=Exception("network error")):
            with pytest.raises(Exception):
                await conn.stream_record(record)


# ===================================================================
# _extract_text_content edge cases
# ===================================================================

class TestExtractTextContentEdgeCases:

    def test_exception_in_trafilatura_returns_empty(self):
        conn = _make_connector()
        with patch("app.connectors.sources.rss.connector.trafilatura") as mock_traf:
            mock_traf.extract.side_effect = Exception("parse error")
            result = conn._extract_text_content("<html>bad</html>")
            assert result == ""

    def test_bytes_with_bad_encoding(self):
        conn = _make_connector()
        with patch("app.connectors.sources.rss.connector.trafilatura") as mock_traf:
            mock_traf.extract.return_value = "text"
            # bytes with replacement characters
            result = conn._extract_text_content(b"\xff\xfe<html>test</html>")
            assert result == "text"


# ===================================================================
# run_sync
# ===================================================================

class TestRunSync:
    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector()
        conn.feed_urls = ["https://feed1.com/rss", "https://feed2.com/rss"]
        with patch.object(conn, "_process_feed", new_callable=AsyncMock, return_value=10):
            await conn.run_sync()
            assert conn.data_entities_processor.on_new_app_users.await_count == 1

    @pytest.mark.asyncio
    async def test_feed_error_continues(self):
        conn = _make_connector()
        conn.feed_urls = ["https://feed1.com/rss", "https://feed2.com/rss"]
        call_count = 0

        async def process_side_effect(url, users):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Feed 1 failed")
            return 5

        with patch.object(conn, "_process_feed", new_callable=AsyncMock, side_effect=process_side_effect):
            await conn.run_sync()
            # Both feeds should have been attempted

    @pytest.mark.asyncio
    async def test_sync_raises_on_general_error(self):
        conn = _make_connector()
        conn.feed_urls = ["https://feed.com/rss"]
        conn.data_entities_processor.get_user_by_user_id = AsyncMock(
            side_effect=Exception("db error")
        )
        with pytest.raises(Exception, match="db error"):
            await conn.run_sync()


# ===================================================================
# _parse_feed_urls
# ===================================================================

class TestParseFeedUrls:
    def test_comma_separated(self):
        conn = _make_connector()
        result = conn._parse_feed_urls("https://a.com/rss,https://b.com/rss")
        assert result == ["https://a.com/rss", "https://b.com/rss"]

    def test_newline_separated(self):
        conn = _make_connector()
        result = conn._parse_feed_urls("https://a.com/rss\nhttps://b.com/rss")
        assert result == ["https://a.com/rss", "https://b.com/rss"]

    def test_filters_invalid_urls(self):
        conn = _make_connector()
        result = conn._parse_feed_urls("https://valid.com/rss,not-a-url,ftp://bad.com")
        assert result == ["https://valid.com/rss"]

    def test_deduplication(self):
        conn = _make_connector()
        result = conn._parse_feed_urls("https://a.com/rss,https://a.com/rss")
        assert result == ["https://a.com/rss"]


# ===================================================================
# test_connection_and_access
# ===================================================================

class TestTestConnectionAndAccess:
    @pytest.mark.asyncio
    async def test_no_session(self):
        conn = _make_connector()
        conn.session = None
        conn.feed_urls = ["https://feed.com/rss"]
        result = await conn.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_no_feed_urls(self):
        conn = _make_connector()
        conn.feed_urls = []
        conn.session = MagicMock()
        result = await conn.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector()
        conn.feed_urls = ["https://feed.com/rss"]
        resp = _make_mock_response(status=200)
        conn.session = _make_session(resp)
        result = await conn.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_bad_status(self):
        conn = _make_connector()
        conn.feed_urls = ["https://feed.com/rss"]
        resp = _make_mock_response(status=404)
        conn.session = _make_session(resp)
        result = await conn.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self):
        conn = _make_connector()
        conn.feed_urls = ["https://feed.com/rss"]
        session = MagicMock()
        session.get = MagicMock(side_effect=Exception("network error"))
        conn.session = session
        result = await conn.test_connection_and_access()
        assert result is False


# ===================================================================
# get_app_users
# ===================================================================

class TestGetAppUsers:
    def test_converts_users(self):
        from app.models.entities import User
        conn = _make_connector()
        user = MagicMock(spec=User)
        user.source_user_id = "src1"
        user.id = "u1"
        user.email = "test@example.com"
        user.full_name = "Test User"
        user.is_active = True
        user.title = "Engineer"
        user.org_id = "org-1"
        result = conn.get_app_users([user])
        assert len(result) == 1
        assert result[0].email == "test@example.com"

    def test_skips_users_without_email(self):
        conn = _make_connector()
        user = MagicMock()
        user.email = None
        result = conn.get_app_users([user])
        assert len(result) == 0


# ===================================================================
# create_record_group
# ===================================================================

class TestCreateRecordGroup:
    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector()
        await conn.create_record_group("https://blog.example.com/rss")
        conn.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_propagates(self):
        conn = _make_connector()
        conn.data_entities_processor.on_new_record_groups = AsyncMock(side_effect=Exception("db error"))
        with pytest.raises(Exception, match="db error"):
            await conn.create_record_group("https://blog.example.com/rss")


# ===================================================================
# _process_entry
# ===================================================================

class TestProcessEntryExtended:
    @pytest.mark.asyncio
    async def test_no_link_returns_none(self):
        conn = _make_connector()
        entry = {"title": "Test", "id": "1"}  # no link
        result = await conn._process_entry(entry, "https://feed.com/rss")
        assert result is None

    @pytest.mark.asyncio
    async def test_duplicate_url_skipped(self):
        conn = _make_connector()
        conn.processed_urls = {"https://example.com/article-1"}
        entry = {"title": "Test", "link": "https://example.com/article-1", "id": "1"}
        result = await conn._process_entry(entry, "https://feed.com/rss")
        assert result is None

    @pytest.mark.asyncio
    async def test_content_from_entry_content(self):
        conn = _make_connector()
        entry = {
            "title": "Test",
            "link": "https://example.com/article",
            "id": "1",
            "content": [{"value": "<p>Full article content</p>"}],
        }
        result = await conn._process_entry(entry, "https://feed.com/rss")
        assert result is not None

    @pytest.mark.asyncio
    async def test_content_from_fetch_full(self):
        conn = _make_connector()
        conn.fetch_full_content = True
        entry = {
            "title": "Test",
            "link": "https://example.com/article",
            "id": "1",
            "summary": "",
        }
        with patch.object(conn, "_fetch_article_content", new_callable=AsyncMock, return_value="Full crawled content"):
            result = await conn._process_entry(entry, "https://feed.com/rss")
            assert result is not None

    @pytest.mark.asyncio
    async def test_content_from_html_summary(self):
        conn = _make_connector()
        conn.fetch_full_content = False
        entry = {
            "title": "Test",
            "link": "https://example.com/article",
            "id": "1",
            "summary": "<p>HTML summary</p>",
            "summary_detail": {"type": "text/html"},
        }
        with patch.object(conn, "_extract_text_content", return_value="HTML summary"):
            result = await conn._process_entry(entry, "https://feed.com/rss")
            assert result is not None

    @pytest.mark.asyncio
    async def test_content_from_plain_summary(self):
        conn = _make_connector()
        conn.fetch_full_content = False
        entry = {
            "title": "Test",
            "link": "https://example.com/article",
            "id": "1",
            "summary": "Plain text summary",
            "summary_detail": {"type": "text/plain"},
        }
        result = await conn._process_entry(entry, "https://feed.com/rss")
        assert result is not None

    @pytest.mark.asyncio
    async def test_fallback_to_title(self):
        conn = _make_connector()
        conn.fetch_full_content = False
        entry = {
            "title": "Only Title Here",
            "link": "https://example.com/article",
            "id": "1",
        }
        result = await conn._process_entry(entry, "https://feed.com/rss")
        assert result is not None
