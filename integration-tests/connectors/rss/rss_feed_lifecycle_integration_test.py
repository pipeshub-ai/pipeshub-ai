# pyright: ignore-file

"""
RSS Connector – Feed Lifecycle Integration Tests
================================================

What happens to synced items when feeds change under the connector or in its
settings, and when a user asks for a full sync or a reindex.

  * A feed lists only its latest entries, so an entry that leaves it keeps its
    record, on a full sync as on a scheduled one. A feed that cannot be read
    keeps its records too.
  * A feed taken out of the Feed URLs setting is removed with its items.
  * An item's guid need only be unique within its feed, so two feeds that use
    the same guid get two records.
  * Reindexing a feed re-reads its items, and none is left waiting.
  * Enabling the connector with a URL that is not a feed names that URL.
  * A paged feed (RFC 5005) is read past its first page, up to the limit.

Test cases:
  TC-FULL-001    — A full sync keeps items that left the feed and those of a failing feed
  TC-REINDEX-001 — Reindexing a feed re-reads every item of it
  TC-FEED-002    — A feed taken out of the settings is removed with its items
  TC-ID-001      — The same guid in two feeds makes two records
  TC-CONN-001    — A URL that is not a feed is named when the connector is enabled
  TC-PAGE-001    — A paged feed is read past its first page
"""

import logging
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from connectors.rss.conftest import (  # noqa: E402  # type: ignore[import-not-found]
    CHANGELOG_FEED,
    CHANGELOG_GUIDS,
    NEWS_FEED,
    NEWS_ITEMS,
    FeedItem,
    item_record_id,
    news_feed,
)
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import (  # noqa: E402
    wait_for_records_indexed,
    wait_for_sync_completion,
    wait_until_graph_condition,
)
from helper.storage_incremental import settle_record_baseline  # noqa: E402
from helper.web_fixtures import WebFixtures, html_page  # noqa: E402
from pipeshub_client import PipeshubClient, PipeshubClientError  # noqa: E402  # type: ignore[import-not-found]

logger = logging.getLogger("rss-feed-lifecycle-test")

ROADMAP, OFFICE_MOVE, SECURITY = NEWS_ITEMS[0], NEWS_ITEMS[1], NEWS_ITEMS[2]
RSS = "application/rss+xml; charset=utf-8"
HTML = "text/html; charset=utf-8"
# A second feed whose only item reuses the guid of the news feed's first item.
OTHER_FEED = "feeds/other.xml"
OTHER_ITEM = FeedItem(ROADMAP.guid, "Other Feed: Same Guid, Different Story", "feeds/other/story.html",
                      "Mon, 01 Sep 2025 10:00:00 GMT")


def _other_feed() -> str:
    return news_feed([OTHER_ITEM]).replace("Fixture Company News", "Fixture Other Feed")


async def _wait_gone(graph_provider: GraphProviderProtocol, connector_id: str, record_id: str) -> None:
    async def _gone() -> bool:
        return await graph_provider.get_record_by_external_id(connector_id, record_id) is None

    await wait_until_graph_condition(
        connector_id, check=_gone, timeout=180, poll_interval=5, description=f"removal of {record_id}"
    )


@pytest.mark.integration
@pytest.mark.rss
@pytest.mark.asyncio(loop_scope="session")
class TestRSSFeedLifecycle:
    """Feeds change; synced items follow the settings, not the feed's rolling window."""

    @pytest.mark.order(1)
    async def test_tc_full_001_full_sync_keeps_departed_items_and_failing_feeds(
        self,
        rss_connector: dict[str, Any],
        rss_fixtures: WebFixtures,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FULL-001: A full sync removes nothing a feed merely stopped listing or failed to serve."""
        connector_id = rss_connector["connector_id"]
        await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        before_scoped = await graph_provider.count_records(connector_id, scoped=True)

        rss_fixtures.put(NEWS_FEED, news_feed([ROADMAP, SECURITY]), RSS)
        rss_fixtures.add_fault(CHANGELOG_FEED, status=500)
        try:
            pipeshub_client.resync_connector(connector_id, full_sync=True)
            await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id)
            assert rss_fixtures.fault_hits(CHANGELOG_FEED) >= 1, "TC-FULL-001: the full sync never read the changelog"

            after_scoped = await graph_provider.count_records(connector_id, scoped=True)
            assert after_scoped == before_scoped, (
                f"TC-FULL-001: the full sync unlinked records ({before_scoped} -> {after_scoped} in a feed)"
            )
            for record_id in [OFFICE_MOVE.record_id, *CHANGELOG_GUIDS]:
                assert await graph_provider.get_record_by_external_id(connector_id, record_id) is not None, (
                    f"TC-FULL-001: {record_id} was removed by a full sync"
                )
        finally:
            rss_fixtures.remove_fault(CHANGELOG_FEED)

    @pytest.mark.order(2)
    async def test_tc_reindex_001_reindexing_a_feed_rereads_its_items(
        self,
        rss_connector: dict[str, Any],
        rss_fixtures: WebFixtures,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-REINDEX-001: A feed's reindex streams its items again and leaves none waiting."""
        connector_id = rss_connector["connector_id"]
        news_ids = [ROADMAP.record_id, SECURITY.record_id]
        await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        await wait_for_records_indexed(graph_provider, connector_id, news_ids)
        group = await graph_provider.get_record_group_by_external_id(
            connector_id, rss_fixtures.url_for_connector(NEWS_FEED)
        )
        assert group is not None, "TC-REINDEX-001: the news feed has no record group"

        # A delay-only fault answers normally and counts the requests for the page.
        rss_fixtures.add_fault(ROADMAP.article_path, delay=0.01)
        try:
            pipeshub_client.reindex_record_group(group.id, depth=0)

            async def _article_read() -> bool:
                return rss_fixtures.fault_hits(ROADMAP.article_path) >= 1

            await wait_until_graph_condition(
                connector_id, check=_article_read, timeout=180, poll_interval=5,
                description="the reindex to read the roadmap article",
            )
            statuses = await wait_for_records_indexed(graph_provider, connector_id, news_ids)
            assert set(statuses.values()) == {"COMPLETED"}, f"TC-REINDEX-001: items after reindex: {statuses}"
        finally:
            rss_fixtures.remove_fault(ROADMAP.article_path)

    @pytest.mark.order(3)
    async def test_tc_feed_002_and_id_001_settings_change_removes_a_feed_and_keeps_guids_apart(
        self,
        rss_connector: dict[str, Any],
        rss_fixtures: WebFixtures,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FEED-002 / TC-ID-001: Swap the changelog for a feed that reuses a news guid."""
        connector_id = rss_connector["connector_id"]
        await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        news_url = rss_fixtures.url_for_connector(NEWS_FEED)
        other_url = rss_fixtures.url_for_connector(OTHER_FEED)
        changelog_url = rss_fixtures.url_for_connector(CHANGELOG_FEED)

        rss_fixtures.put(OTHER_ITEM.article_path,
                         html_page(OTHER_ITEM.title, "Marker other-story: a different story."), HTML)
        rss_fixtures.put(OTHER_FEED, _other_feed(), RSS)
        pipeshub_client.update_connector_filters_sync_safe(
            connector_id, sync={"feed_urls": f"{news_url}\n{other_url}"}
        )
        # Re-enabling runs a sync of its own; a resync on top of it is refused (409).
        await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id)
        other_id = item_record_id(other_url, OTHER_ITEM.guid)

        async def _other_synced() -> bool:
            return await graph_provider.get_record_by_external_id(connector_id, other_id) is not None

        await wait_until_graph_condition(
            connector_id, check=_other_synced, timeout=180, poll_interval=5, description="the other feed's item"
        )
        for guid in CHANGELOG_GUIDS:
            await _wait_gone(graph_provider, connector_id, guid)
        assert await graph_provider.get_record_group_by_external_id(connector_id, changelog_url) is None, (
            "TC-FEED-002: the changelog feed's group is still there after it left the settings"
        )

        roadmap = await graph_provider.get_record_by_external_id(connector_id, ROADMAP.record_id)
        other = await graph_provider.get_record_by_external_id(connector_id, other_id)
        assert roadmap is not None and other is not None, (
            f"TC-ID-001: guid {ROADMAP.guid} in two feeds should give two records; got {roadmap!r} and {other!r}"
        )
        assert roadmap.id != other.id
        assert roadmap.record_name == ROADMAP.title, f"TC-ID-001: the news item became {roadmap.record_name!r}"
        assert other.record_name == OTHER_ITEM.title

    @pytest.mark.order(4)
    async def test_tc_conn_001_a_url_that_is_not_a_feed_is_named_on_enable(
        self,
        rss_connector: dict[str, Any],
        rss_fixtures: WebFixtures,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-CONN-001: Enabling with an article page in Feed URLs fails and says which URL."""
        connector_id = rss_connector["connector_id"]
        await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        news_url = rss_fixtures.url_for_connector(NEWS_FEED)
        not_a_feed = rss_fixtures.url_for_connector(ROADMAP.article_path)

        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.update_connector_filters_sync_config(
            connector_id, sync={"feed_urls": f"{news_url}\n{not_a_feed}"}
        )
        try:
            with pytest.raises(PipeshubClientError) as refused:
                pipeshub_client.toggle_sync(connector_id, enable=True)
            assert ROADMAP.article_path in str(refused.value), (
                f"TC-CONN-001: the error does not name the URL that is not a feed: {refused.value}"
            )
        finally:
            pipeshub_client.update_connector_filters_sync_config(connector_id, sync={"feed_urls": news_url})
            pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id)

    @pytest.mark.order(5)
    async def test_tc_page_001_a_paged_feed_is_read_past_its_first_page(
        self,
        rss_connector: dict[str, Any],
        rss_fixtures: WebFixtures,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-PAGE-001: rel="next" pages are followed up to the per-feed limit (3)."""
        connector_id = rss_connector["connector_id"]
        await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        news_url = rss_fixtures.url_for_connector(NEWS_FEED)
        paged_url = rss_fixtures.url_for_connector(PAGED_FEED)
        guids = [f"urn:fixture:paged:{n}" for n in range(1, 5)]
        for n in range(1, 5):
            rss_fixtures.put(f"feeds/paged/{n}.html", html_page(f"Paged item {n}", f"Marker paged-{n}."), HTML)
        rss_fixtures.put(PAGED_FEED, _paged_feed(guids[:2], "paged-2.xml", [1, 2]), RSS)
        rss_fixtures.put("feeds/paged-2.xml", _paged_feed(guids[2:], None, [3, 4]), RSS)

        pipeshub_client.update_connector_filters_sync_safe(
            connector_id, sync={"feed_urls": f"{news_url}\n{paged_url}"}
        )
        await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id)

        async def _third_synced() -> bool:
            return await graph_provider.get_record_by_external_id(connector_id, guids[2]) is not None

        await wait_until_graph_condition(
            connector_id, check=_third_synced, timeout=180, poll_interval=5,
            description="the first item of the feed's second page",
        )
        for guid in guids[:3]:
            assert await graph_provider.get_record_by_external_id(connector_id, guid) is not None, (
                f"TC-PAGE-001: {guid} was not synced"
            )
        assert await graph_provider.get_record_by_external_id(connector_id, guids[3]) is None, (
            "TC-PAGE-001: the item past the per-feed limit was synced"
        )


PAGED_FEED = "feeds/paged.xml"


def _paged_feed(guids: list[str], next_href: str | None, numbers: list[int]) -> str:
    """An RSS 2.0 page linking its next page the RFC 5005 way; ``{{BASE_URL}}`` is filled in by the service."""
    items = "".join(
        f'<item><guid isPermaLink="false">{guid}</guid><title>Paged item {n}</title>'
        f"<link>{{{{BASE_URL}}}}/feeds/paged/{n}.html</link><description>Summary {n}.</description></item>"
        for guid, n in zip(guids, numbers, strict=True)
    )
    nxt = f'<atom:link rel="next" href="{next_href}"/>' if next_href else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">'
        f"<channel><title>Fixture Paged Feed</title><link>{{{{BASE_URL}}}}/feeds/</link>{nxt}{items}</channel></rss>"
    )
