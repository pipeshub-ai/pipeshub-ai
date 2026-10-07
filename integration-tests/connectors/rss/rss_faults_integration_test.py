# pyright: ignore-file

"""
RSS Connector – Fault Recovery Integration Tests
================================================

The feed server misbehaves the way real ones do, through the ``web-fixtures``
fault modes, and the next sync runs with the resync API.

What the connector does today, and so what these hold it to:
  * A 429 or 503 on the feed or an article is retried inside the fetch,
    honouring Retry-After, and a dropped connection is retried the same way.
  * Any other error on the feed skips that feed for the sync. Nothing already
    indexed is removed, and the next sync picks up what was missed.
  * An article page that will not load falls back to the feed's own summary,
    so the item is still indexed; once the page loads again, the next sync
    re-indexes the item with the full article.

Faults are set only once indexing has finished with every item, because
indexing an item re-reads the feed and would otherwise take a fault meant for
the sync.

Test cases:
  TC-FAULT-001 — A rate-limited feed and a dropped article recover within the sync
  TC-FAULT-002 — A feed that errors is skipped without loss, then caught up
  TC-FAULT-003 — An item whose article is down is indexed from its summary, then repaired
"""

import logging
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from connectors.rss.conftest import (  # type: ignore[import-not-found]
    CHANGELOG_GUIDS,
    NEWS_FEED,
    NEWS_ITEMS,
    MAX_ARTICLES_PER_FEED,
    FeedItem,
    news_feed,
)
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import (
    wait_for_records_indexed,
    wait_for_sync_completion,
    wait_until_graph_condition,
)
from helper.storage_incremental import settle_record_baseline, wait_for_record_reindex
from helper.web_fixtures import WebFixtures, html_page
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("rss-faults-test")

FAULT_SYNC_TIMEOUT = 600
# The news feed keeps its newest items; each test puts its own item first.
KEPT_ITEMS = NEWS_ITEMS[: MAX_ARTICLES_PER_FEED - 1]


def _resync(pipeshub_client: PipeshubClient, connector_id: str) -> None:
    pipeshub_client.resync_connector(connector_id, full_sync=False)


def _serve_item(rss_fixtures: WebFixtures, item: FeedItem, marker: str) -> None:
    """Serve ``item``'s article and put it at the top of the news feed."""
    # The marker ends the page, so a copy cut off in transit lacks it.
    filler = "This paragraph pads the article so that half of it is a real cut. " * 20
    rss_fixtures.put(
        item.article_path, html_page(item.title, f"{filler} Marker {marker}."), "text/html; charset=utf-8"
    )
    rss_fixtures.put(NEWS_FEED, news_feed([item, *KEPT_ITEMS]), "application/rss+xml; charset=utf-8")


async def _settle(
    pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol, connector_id: str, guids: list[str]
) -> int:
    count = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
    await wait_for_records_indexed(graph_provider, connector_id, guids)
    return count


async def _streamed_text(
    pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol, connector_id: str, guid: str
) -> str:
    record = await graph_provider.get_record_by_external_id(connector_id, guid)
    assert record is not None, f"no record for feed item {guid}"
    return pipeshub_client.stream_record(record.id).content.decode("utf-8", errors="replace")


async def _wait_for_item(graph_provider: GraphProviderProtocol, connector_id: str, guid: str) -> None:
    async def _present() -> bool:
        return await graph_provider.get_record_by_external_id(connector_id, guid) is not None

    await wait_until_graph_condition(
        connector_id, check=_present, timeout=120, poll_interval=5, description=f"feed item {guid}"
    )


@pytest.mark.integration
@pytest.mark.rss
@pytest.mark.asyncio(loop_scope="session")
class TestRSSConnectorFaults:
    """A misbehaving feed server delays items; it never loses them."""

    @pytest.mark.order(1)
    async def test_tc_fault_001_rate_limited_feed_and_dropped_article_recover(
        self,
        rss_connector: dict[str, Any],
        rss_fixtures: WebFixtures,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FAULT-001: The sync that meets a passing fault still indexes the new item."""
        connector_id = rss_connector["connector_id"]
        item = FeedItem("fixture-news-fault-1", "Fixture News: Rate Limit Drill",
                        "feeds/news/rate-limit-drill.html", "Sat, 06 Sep 2025 09:00:00 GMT")
        known = [i.guid for i in NEWS_ITEMS[:MAX_ARTICLES_PER_FEED]] + CHANGELOG_GUIDS
        before_count = await _settle(pipeshub_client, graph_provider, connector_id, known)

        _serve_item(rss_fixtures, item, "news-rate-limit-drill")
        rss_fixtures.add_fault(NEWS_FEED, status=429, retry_after=2, times=2)
        rss_fixtures.add_fault(item.article_path, truncate=True, times=1)
        _resync(pipeshub_client, connector_id)
        await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=FAULT_SYNC_TIMEOUT)

        assert rss_fixtures.fault_hits(NEWS_FEED) == 2, "TC-FAULT-001: the sync did not get past the rate limit"
        assert rss_fixtures.fault_hits(item.article_path) == 1, "TC-FAULT-001: the article was never requested"
        await _wait_for_item(graph_provider, connector_id, item.guid)
        after_count = await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id)
        assert after_count == before_count + 1, (
            f"TC-FAULT-001: expected one new record, count went {before_count} -> {after_count}"
        )
        record = await graph_provider.get_record_by_external_id(connector_id, item.guid)
        assert record.size_in_bytes and record.size_in_bytes > 1000, (
            f"TC-FAULT-001: the item holds {record.size_in_bytes} bytes; the sync kept the "
            "summary, not the article it should have fetched again after the cut"
        )

    @pytest.mark.order(2)
    async def test_tc_fault_002_failed_feed_is_skipped_without_loss(
        self,
        rss_connector: dict[str, Any],
        rss_fixtures: WebFixtures,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FAULT-002: A feed error costs one sync's worth of news, and nothing else."""
        connector_id = rss_connector["connector_id"]
        item = FeedItem("fixture-news-fault-2", "Fixture News: Outage Postmortem",
                        "feeds/news/outage-postmortem.html", "Sun, 07 Sep 2025 09:00:00 GMT")
        existing = [
            r.get("externalRecordId") for r in await graph_provider.fetch_records_by_type(connector_id, "")
        ]
        before_count = await _settle(pipeshub_client, graph_provider, connector_id, existing)

        _serve_item(rss_fixtures, item, "news-outage-postmortem")
        # Held for the whole sync rather than one request, so it is the sync
        # that meets it and not an indexer re-reading the feed.
        rss_fixtures.add_fault(NEWS_FEED, status=500)
        _resync(pipeshub_client, connector_id)
        during_count = await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id)

        assert rss_fixtures.fault_hits(NEWS_FEED) >= 1, "TC-FAULT-002: the sync never read the feed"
        assert during_count == before_count, (
            f"TC-FAULT-002: a sync that could not read the feed moved the count {before_count} -> {during_count}"
        )
        for guid in existing:
            assert await graph_provider.get_record_by_external_id(connector_id, guid) is not None, (
                f"TC-FAULT-002: {guid} was removed while its feed was failing"
            )
        assert await graph_provider.get_record_by_external_id(connector_id, item.guid) is None, (
            "TC-FAULT-002: the new item was indexed although its feed answered 500"
        )

        rss_fixtures.remove_fault(NEWS_FEED)
        _resync(pipeshub_client, connector_id)
        await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id)
        await _wait_for_item(graph_provider, connector_id, item.guid)
        after_count = await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id)
        assert after_count == before_count + 1, (
            f"TC-FAULT-002: the sync after the outage should add one record, count went {before_count} -> {after_count}"
        )

    @pytest.mark.order(3)
    async def test_tc_fault_003_item_with_article_down_uses_its_summary_then_repairs(
        self,
        rss_connector: dict[str, Any],
        rss_fixtures: WebFixtures,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FAULT-003: A dead article degrades its item to the summary until the page is back."""
        connector_id = rss_connector["connector_id"]
        item = FeedItem("fixture-news-fault-3", "Fixture News: Article Outage",
                        "feeds/news/article-outage.html", "Mon, 08 Sep 2025 09:00:00 GMT")
        existing = [
            r.get("externalRecordId") for r in await graph_provider.fetch_records_by_type(connector_id, "")
        ]
        await _settle(pipeshub_client, graph_provider, connector_id, existing)

        _serve_item(rss_fixtures, item, "news-article-outage")
        rss_fixtures.add_fault(item.article_path, status=500)
        _resync(pipeshub_client, connector_id)
        await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id)
        await _wait_for_item(graph_provider, connector_id, item.guid)
        await wait_for_records_indexed(graph_provider, connector_id, [item.guid])

        degraded = await graph_provider.get_record_by_name(connector_id, item.title)
        assert degraded is not None, f"TC-FAULT-003: {item.title} is not in the graph"
        text = await _streamed_text(pipeshub_client, graph_provider, connector_id, item.guid)
        assert f"Summary for {item.title}" in text and "news-article-outage" not in text, (
            f"TC-FAULT-003: with its article down the item should carry the feed summary: {text[:300]!r}"
        )

        rss_fixtures.remove_fault(item.article_path)
        _resync(pipeshub_client, connector_id)
        await wait_for_record_reindex(
            graph_provider, connector_id, item.title, degraded.get("version"), timeout=FAULT_SYNC_TIMEOUT
        )
        text = await _streamed_text(pipeshub_client, graph_provider, connector_id, item.guid)
        assert "news-article-outage" in text, (
            f"TC-FAULT-003: the next sync did not bring the article back: {text[:300]!r}"
        )
