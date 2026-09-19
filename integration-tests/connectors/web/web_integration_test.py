# pyright: ignore-file

"""
Web Connector – Integration Tests
=================================

Tests receive a connector that has crawled the ``web-fixtures`` site through the
``web_connector`` fixture (conftest.py). Pages are changed through the fixture
service's control API between syncs.

Later syncs are started with the resync API, the path a scheduled sync takes,
so they run on the connector instance that did the first crawl.

Test cases:
  TC-SYNC-001   — The crawl indexes the pages in scope and none outside it
  TC-STREAM-001 — A crawled page streams its stored content
  TC-UPDATE-001 — A page whose text changes is re-indexed in place
  TC-INCR-001   — A page linked after the first crawl is indexed on the next one

Not covered: a page that disappears from the site. The connector skips a 404
and keeps the record it already has, so there is no behaviour to assert yet.
"""

import logging
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from connectors.web.conftest import (  # type: ignore[import-not-found]
    EXCLUDED_PAGES,
    EXPECTED_PAGES,
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

logger = logging.getLogger("web-lifecycle-test")

GUIDE_NAME = "Fixture Getting Started Guide"
GUIDE_PATH = EXPECTED_PAGES[GUIDE_NAME]


def _resync(pipeshub_client: PipeshubClient, connector_id: str) -> None:
    pipeshub_client.resync_connector(connector_id, full_sync=False)


async def _streamed_text(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    external_id: str,
) -> str:
    record = await graph_provider.get_record_by_external_id(connector_id, external_id)
    assert record is not None, f"no record for {external_id}"
    return pipeshub_client.stream_record(record.id).content.decode("utf-8", errors="replace")


@pytest.mark.integration
@pytest.mark.web
@pytest.mark.asyncio(loop_scope="session")
class TestWebConnector:
    """Lifecycle coverage for the Web connector against a site it owns."""

    @pytest.mark.order(1)
    async def test_tc_sync_001_crawl_indexes_exactly_the_pages_in_scope(
        self,
        web_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: Depth and start-path limits decide what is indexed."""
        connector_id = web_connector["connector_id"]

        await graph_provider.assert_record_names_contain(connector_id, list(EXPECTED_PAGES))
        for name, reason in EXCLUDED_PAGES.items():
            assert await graph_provider.get_record_by_name(connector_id, name) is None, (
                f"TC-SYNC-001: {name!r} was indexed, but it is {reason}"
            )
        logger.info(
            "TC-SYNC-001 passed: %d records for %s (connector %s)",
            web_connector["full_sync_count"],
            web_connector["start_url"],
            connector_id,
        )

    @pytest.mark.order(2)
    async def test_tc_stream_001_page_streams_its_content(
        self,
        web_connector: dict[str, Any],
        web_fixtures: WebFixtures,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-STREAM-001: stream_record returns the page text that was crawled."""
        text = await _streamed_text(
            pipeshub_client,
            graph_provider,
            web_connector["connector_id"],
            web_fixtures.url_for_connector(GUIDE_PATH),
        )
        assert "guide-v1" in text, f"TC-STREAM-001: guide text missing from the stream: {text[:300]!r}"

    @pytest.mark.order(3)
    async def test_tc_update_001_changed_page_is_reindexed_in_place(
        self,
        web_connector: dict[str, Any],
        web_fixtures: WebFixtures,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-UPDATE-001: New text on a crawled page updates its record, not a new one."""
        connector_id = web_connector["connector_id"]
        before_count = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        before = await graph_provider.get_record_by_name(connector_id, GUIDE_NAME)
        assert before is not None, f"TC-UPDATE-001: {GUIDE_NAME} not in the graph before the change"

        web_fixtures.put(
            GUIDE_PATH,
            web_fixtures.get(GUIDE_PATH).replace("guide-v1", "guide-v2"),
            "text/html; charset=utf-8",
        )
        _resync(pipeshub_client, connector_id)

        after = await wait_for_record_reindex(
            graph_provider, connector_id, GUIDE_NAME, before.get("version")
        )
        assert after["version"] > before["version"], (
            f"TC-UPDATE-001: version went from {before['version']} to {after['version']}"
        )
        after_count = await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id)
        assert after_count == before_count, (
            f"TC-UPDATE-001: record count moved from {before_count} to {after_count}; "
            "a changed page must update its record, not add one"
        )
        text = await _streamed_text(
            pipeshub_client, graph_provider, connector_id, web_fixtures.url_for_connector(GUIDE_PATH)
        )
        assert "guide-v2" in text and "guide-v1" not in text, (
            f"TC-UPDATE-001: the stream still serves the old text: {text[:300]!r}"
        )

    @pytest.mark.order(4)
    async def test_tc_incr_001_newly_linked_page_is_indexed(
        self,
        web_connector: dict[str, Any],
        web_fixtures: WebFixtures,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-001: A page added to the site and linked from it appears after the next crawl."""
        connector_id = web_connector["connector_id"]
        new_name = "Fixture Changelog"
        before_count = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)

        web_fixtures.put(
            "site/docs/changelog.html",
            html_page(new_name, "Marker changelog-page: what changed in each release, newest first."),
            "text/html; charset=utf-8",
        )
        home = web_fixtures.get(EXPECTED_PAGES["Fixture Docs Home"])
        web_fixtures.put(
            "site/docs/index.html",
            home.replace("</ul>", '<li><a href="changelog.html">Changelog</a></li></ul>', 1),
            "text/html; charset=utf-8",
        )

        after_count = await sync_until_names_visible(
            pipeshub_client, graph_provider, connector_id, [new_name], restart_fn=_resync
        )
        assert after_count == before_count + 1, (
            f"TC-INCR-001: expected one new record, count went {before_count} -> {after_count}"
        )
