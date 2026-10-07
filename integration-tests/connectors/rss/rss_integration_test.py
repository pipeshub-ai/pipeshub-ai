# pyright: ignore-file

"""
RSS Connector – Integration Tests
=================================

Tests receive a connector that has read the ``web-fixtures`` feeds through the
``rss_connector`` fixture (conftest.py). Feeds and article pages are changed
through the fixture service's control API between syncs.

Later syncs are started with the resync API, the path a scheduled sync takes.
It runs on the connector instance that did the first sync, which is what
TC-UPDATE-001 depends on: an instance that remembered the entries of its last
run skipped all of them, so no change was ever indexed.

Test cases:
  TC-SYNC-001   — Both feeds are read, up to the per-feed article limit
  TC-STREAM-001 — An item streams the text of its article page
  TC-UPDATE-001 — An item whose article changes is re-indexed in place
  TC-FEED-001   — A new item is added; an item that leaves the feed is kept
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
    EXPECTED_GUIDS,
    NEWS_FEED,
    NEWS_ITEMS,
    PAST_LIMIT_GUID,
    FeedItem,
    news_feed,
)
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_for_sync_completion
from helper.storage_incremental import (
    settle_record_baseline,
    sync_until_names_visible,
    wait_for_record_reindex,
)
from helper.web_fixtures import WebFixtures, html_page
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("rss-lifecycle-test")

ROADMAP, OFFICE_MOVE = NEWS_ITEMS[0], NEWS_ITEMS[1]


def _resync(pipeshub_client: PipeshubClient, connector_id: str) -> None:
    pipeshub_client.resync_connector(connector_id, full_sync=False)


async def _streamed_text(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    guid: str,
) -> str:
    record = await graph_provider.get_record_by_external_id(connector_id, guid)
    assert record is not None, f"no record for feed item {guid}"
    return pipeshub_client.stream_record(record.id).content.decode("utf-8", errors="replace")


@pytest.mark.integration
@pytest.mark.rss
@pytest.mark.asyncio(loop_scope="session")
class TestRSSConnector:
    """Lifecycle coverage for the RSS connector against feeds it owns."""

    @pytest.mark.order(1)
    async def test_tc_sync_001_both_feeds_up_to_the_article_limit(
        self,
        rss_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: Every item within the limit, from RSS and Atom, is a record."""
        connector_id = rss_connector["connector_id"]

        for guid in EXPECTED_GUIDS:
            assert await graph_provider.get_record_by_external_id(connector_id, guid) is not None, (
                f"TC-SYNC-001: feed item {guid} was not synced"
            )
        assert await graph_provider.get_record_by_external_id(connector_id, PAST_LIMIT_GUID) is None, (
            f"TC-SYNC-001: {PAST_LIMIT_GUID} is past the per-feed limit but was synced"
        )
        logger.info(
            "TC-SYNC-001 passed: %d records from %s (connector %s)",
            rss_connector["full_sync_count"],
            rss_connector["feed_urls"],
            connector_id,
        )

    @pytest.mark.order(2)
    async def test_tc_stream_001_item_streams_its_article(
        self,
        rss_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-STREAM-001: With full content on, an item's text is its article page's."""
        text = await _streamed_text(
            pipeshub_client, graph_provider, rss_connector["connector_id"], ROADMAP.guid
        )
        assert "news-roadmap" in text, f"TC-STREAM-001: article text missing from the stream: {text[:300]!r}"

    @pytest.mark.order(3)
    async def test_tc_update_001_changed_article_is_reindexed_in_place(
        self,
        rss_connector: dict[str, Any],
        rss_fixtures: WebFixtures,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-UPDATE-001: An edited article updates its item's record on the next sync."""
        connector_id = rss_connector["connector_id"]
        before_count = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        before = await graph_provider.get_record_by_name(connector_id, ROADMAP.title)
        assert before is not None, f"TC-UPDATE-001: {ROADMAP.title} not in the graph before the change"

        rss_fixtures.put(
            ROADMAP.article_path,
            rss_fixtures.get(ROADMAP.article_path).replace("news-roadmap", "news-roadmap-revised"),
            "text/html; charset=utf-8",
        )
        _resync(pipeshub_client, connector_id)

        after = await wait_for_record_reindex(
            graph_provider, connector_id, ROADMAP.title, before.get("version")
        )
        assert after["version"] > before["version"], (
            f"TC-UPDATE-001: version went from {before['version']} to {after['version']}"
        )
        after_count = await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id)
        assert after_count == before_count, (
            f"TC-UPDATE-001: record count moved from {before_count} to {after_count}; "
            "an edited article must update its item, not add one"
        )
        text = await _streamed_text(pipeshub_client, graph_provider, connector_id, ROADMAP.guid)
        assert "news-roadmap-revised" in text, f"TC-UPDATE-001: stream has the old text: {text[:300]!r}"

    @pytest.mark.order(4)
    async def test_tc_feed_001_new_item_added_and_departed_item_kept(
        self,
        rss_connector: dict[str, Any],
        rss_fixtures: WebFixtures,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FEED-001: Feeds roll items off; a synced article outlives its feed entry.

        The connector keeps what it has indexed, so an item that drops out of the
        feed stays searchable, and streaming falls back to its article page.
        """
        connector_id = rss_connector["connector_id"]
        before_count = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)

        hackathon = FeedItem(
            "fixture-news-5", "Fixture News: Hackathon", "feeds/news/hackathon.html",
            "Fri, 05 Sep 2025 09:00:00 GMT",
        )
        rss_fixtures.put(
            hackathon.article_path,
            html_page(hackathon.title, "Marker news-hackathon: the hackathon runs for two days, with prizes for the best internal tool."),
            "text/html; charset=utf-8",
        )
        remaining = [hackathon] + [item for item in NEWS_ITEMS if item != OFFICE_MOVE]
        rss_fixtures.put(NEWS_FEED, news_feed(remaining), "application/rss+xml; charset=utf-8")

        after_count = await sync_until_names_visible(
            pipeshub_client, graph_provider, connector_id, [hackathon.title], restart_fn=_resync
        )
        assert after_count == before_count + 1, (
            f"TC-FEED-001: expected one new record, count went {before_count} -> {after_count}"
        )
        assert await graph_provider.get_record_by_external_id(connector_id, OFFICE_MOVE.guid) is not None, (
            f"TC-FEED-001: {OFFICE_MOVE.guid} was removed when it left the feed"
        )
        text = await _streamed_text(pipeshub_client, graph_provider, connector_id, OFFICE_MOVE.guid)
        assert "news-office" in text, (
            f"TC-FEED-001: an item no longer in the feed should stream its article: {text[:300]!r}"
        )
