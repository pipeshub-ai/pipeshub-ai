# pyright: ignore-file

"""
Web Connector – Integration Tests
=================================

Tests receive a fully set-up connector via the ``web_connector`` fixture
(defined in conftest.py), which publishes the site, creates the connector and
waits for a full sync, then tears both down.

The site is served by an nginx container in the integration stack. Crawling a
real website would tie the suite to that site's uptime and wording, and would
send traffic to someone else's server on every CI run.

The fixture site is a deliberate link tree:

    index.html                     depth 0
      -> guides/setup.html         depth 1
      -> guides/billing.html       depth 1
           -> guides/deep.html     depth 2

Test cases:
  TC-SYNC-001  — A recursive crawl follows links and indexes the pages it reaches
  TC-DEPTH-001 — The depth limit is honoured: a page one hop too far is not indexed
  TC-UPDATE-001 — Re-crawling an edited page updates its record rather than adding one
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.storage_incremental import (  # noqa: E402
    settle_record_baseline,
    sync_until_names_visible,
)
from connectors.web.web_source_helper import (  # type: ignore[import-not-found]  # noqa: E402
    WebSourceHelper,
)

logger = logging.getLogger("web-lifecycle-test")


@pytest.mark.integration
@pytest.mark.web
@pytest.mark.asyncio(loop_scope="session")
class TestWebConnector:
    """Lifecycle coverage for the Web connector."""

    @pytest.mark.order(1)
    async def test_tc_sync_001_recursive_crawl_follows_links(
        self,
        web_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: The crawl reaches the landing page and the pages it links to.

        The two guides are only discoverable by following anchors from
        index.html, so a connector that fetched the start URL alone would find
        one page and fail here rather than passing on a single-page site.
        """
        connector_id = web_connector["connector_id"]
        expected = web_connector["expected_titles"]
        full_count = web_connector["full_sync_count"]

        await graph_provider.assert_min_records(connector_id, len(expected))
        await graph_provider.assert_no_orphan_records(connector_id)
        await graph_provider.assert_record_paths_or_names_contain(
            connector_id, expected
        )

        logger.info(
            "TC-SYNC-001 passed: %d records for pages %s (connector %s)",
            full_count,
            expected,
            connector_id,
        )

    @pytest.mark.order(2)
    async def test_tc_depth_001_pages_beyond_the_depth_limit_are_not_indexed(
        self,
        web_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DEPTH-001: A page one hop past the configured depth is not indexed.

        The connector is configured with depth 1. `guides/deep.html` is linked
        only from `guides/setup.html`, putting it two hops from the start URL.

        A depth limit that is not enforced is not a cosmetic bug: on a real site
        it is the difference between indexing a section and crawling the whole
        internet from one page.
        """
        connector_id = web_connector["connector_id"]
        beyond = web_connector["beyond_depth_titles"]

        records = await graph_provider.fetch_record_names(connector_id)
        leaked = [title for title in beyond if any(title in name for name in records)]

        assert not leaked, (
            f"TC-DEPTH-001: {leaked} was indexed despite being two hops from the "
            f"start URL with depth=1. Records found: {sorted(records)}"
        )
        logger.info(
            "TC-DEPTH-001 passed: %s correctly not indexed at depth 1", beyond
        )

    @pytest.mark.order(3)
    async def test_tc_update_001_editing_a_page_does_not_duplicate_it(
        self,
        web_connector: Dict[str, Any],
        web_source: WebSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-UPDATE-001: Re-crawling an edited page updates its record.

        A page keeps its URL when its content changes, so a re-crawl must update
        the existing record. A second record for the same URL is a duplicate,
        which reaches users as the same page appearing twice in search — and on
        a scheduled crawl it would accumulate on every run.
        """
        connector_id = web_connector["connector_id"]
        title = "Billing guide"

        before_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )

        web_source.write_page(
            "guides/billing.html",
            title,
            "Updated for 2026: invoices are issued on the first of the month.",
            [],
        )
        logger.info("Edited guides/billing.html")

        after_count = await sync_until_names_visible(
            pipeshub_client, graph_provider, connector_id, [title]
        )

        assert after_count == before_count, (
            f"TC-UPDATE-001: record count moved from {before_count} to "
            f"{after_count} after editing one page. A re-crawl must update the "
            "existing record, not add another for the same URL."
        )
        await graph_provider.assert_no_orphan_records(connector_id)
        logger.info(
            "TC-UPDATE-001 passed: %s updated in place, count stable at %d",
            title,
            after_count,
        )
