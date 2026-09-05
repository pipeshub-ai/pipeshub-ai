# pyright: ignore-file

"""
RSS Connector – Integration Tests
=================================

Tests receive a fully set-up connector via the ``rss_connector`` fixture
(defined in conftest.py), which writes the feed, creates the connector and waits
for a full sync, then tears both down.

The feed is served by an nginx container in the integration stack. Pointing
these tests at a real feed would make them fail whenever that site changed or
went down, which is noise rather than signal about the connector.

Test cases:
  TC-SYNC-001  — Full sync turns every article into a record
  TC-INCR-001  — An article published after the first sync appears on the next one
  TC-DEDUP-001 — Re-syncing an unchanged feed does not duplicate its articles
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
from connectors.rss.rss_source_helper import (  # type: ignore[import-not-found]  # noqa: E402
    BASE_ARTICLES,
    RssSourceHelper,
)

logger = logging.getLogger("rss-lifecycle-test")


@pytest.mark.integration
@pytest.mark.rss
@pytest.mark.asyncio(loop_scope="session")
class TestRssConnector:
    """Lifecycle coverage for the RSS connector."""

    @pytest.mark.order(1)
    async def test_tc_sync_001_full_sync_graph_validation(
        self,
        rss_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: Every article in the feed becomes a record."""
        connector_id = rss_connector["connector_id"]
        seeded = rss_connector["seeded_titles"]
        full_count = rss_connector["full_sync_count"]

        await graph_provider.assert_min_records(connector_id, len(seeded))
        await graph_provider.assert_no_orphan_records(connector_id)
        await graph_provider.assert_record_paths_or_names_contain(connector_id, seeded)

        logger.info(
            "TC-SYNC-001 passed: %d records for articles %s (connector %s)",
            full_count,
            seeded,
            connector_id,
        )

    @pytest.mark.order(2)
    async def test_tc_incr_001_new_article_is_picked_up(
        self,
        rss_connector: Dict[str, Any],
        rss_source: RssSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-001: An article added to the feed appears on the next sync."""
        connector_id = rss_connector["connector_id"]

        before_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )

        new_title = "Incident review 2026-09"
        rss_source.write_feed(
            [(new_title, "What happened, and what changed afterwards."), *BASE_ARTICLES]
        )
        logger.info("Published new article %r to the feed", new_title)

        after_count = await sync_until_names_visible(
            pipeshub_client, graph_provider, connector_id, [new_title]
        )

        assert after_count > before_count, (
            f"TC-INCR-001: expected a new record for {new_title!r}; count stayed "
            f"at {after_count}"
        )
        await graph_provider.assert_record_paths_or_names_contain(
            connector_id, [new_title]
        )
        rss_connector["after_incr_count"] = after_count
        logger.info(
            "TC-INCR-001 passed: before=%d, after=%d, new=%r",
            before_count,
            after_count,
            new_title,
        )

    @pytest.mark.order(3)
    async def test_tc_dedup_001_resyncing_an_unchanged_feed_adds_nothing(
        self,
        rss_connector: Dict[str, Any],
        rss_source: RssSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DEDUP-001: Re-syncing an unchanged feed must not duplicate articles.

        A feed is re-fetched in full on every sync, so an article the connector
        has already seen arrives again each time. If it is not recognised by its
        guid, every sync adds another copy — the record count grows on a
        schedule with no user action, and the same article fills up search
        results. This is the failure mode most specific to feed connectors.
        """
        connector_id = rss_connector["connector_id"]

        before_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )
        titles_before = rss_source.article_titles()

        # Republish byte-for-byte the same articles. The helper derives each
        # guid from the title, so the feed is genuinely unchanged.
        rss_source.write_feed(
            [
                ("Incident review 2026-09", "What happened, and what changed afterwards."),
                *BASE_ARTICLES,
            ]
        )
        assert rss_source.article_titles() == titles_before, (
            "TC-DEDUP-001 requires the feed to be unchanged; the fixture rewrote "
            "different articles"
        )

        after_count = await sync_until_names_visible(
            pipeshub_client, graph_provider, connector_id, titles_before[:1]
        )

        assert after_count == before_count, (
            f"TC-DEDUP-001: record count grew from {before_count} to {after_count} "
            "after re-syncing an unchanged feed. Articles must be recognised by "
            "guid, not re-added on every fetch."
        )
        await graph_provider.assert_no_orphan_records(connector_id)
        logger.info(
            "TC-DEDUP-001 passed: unchanged feed re-synced, count stable at %d",
            after_count,
        )
