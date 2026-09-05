# pyright: ignore-file

"""
Nextcloud Connector – Integration Tests
=======================================

Tests receive a fully set-up connector via the ``nextcloud_connector`` fixture
(defined in conftest.py), which uploads the files over WebDAV, creates the
connector and waits for a full sync, then tears both down.

Nextcloud is self-hostable, so the integration stack runs one and the connector
syncs from it. It is also unusual among SaaS-shaped sources in authenticating
with a plain username and password, so the admin credentials the container is
started with are all the connector needs — there is no app password to mint
first.

Test cases:
  TC-SYNC-001   — Full sync picks up every file, including ones in subfolders
  TC-INCR-001   — A file added after the first sync appears on the next one
  TC-UPDATE-001 — Editing a file updates its record rather than adding one
"""

import logging
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from connectors.nextcloud.nextcloud_source_helper import (  # type: ignore[import-not-found]
    NextcloudSourceHelper,
)
from helper.graph_provider import GraphProviderProtocol
from helper.storage_incremental import (
    settle_record_baseline,
    sync_until_names_visible,
)
from pipeshub_client import (
    PipeshubClient,  # type: ignore[import-not-found]
)

logger = logging.getLogger("nextcloud-lifecycle-test")


@pytest.mark.integration
@pytest.mark.nextcloud
@pytest.mark.asyncio(loop_scope="session")
class TestNextcloudConnector:
    """Lifecycle coverage for the Nextcloud connector."""

    @pytest.mark.order(1)
    async def test_tc_sync_001_full_sync_graph_validation(
        self,
        nextcloud_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: Every seeded file is in the graph, nested ones included.

        The seed puts files in two subfolders, so a connector that listed only
        the top level of the share would find nothing and fail here rather than
        passing against a flat directory.
        """
        connector_id = nextcloud_connector["connector_id"]
        seeded = nextcloud_connector["seeded_names"]
        full_count = nextcloud_connector["full_sync_count"]

        await graph_provider.assert_min_records(connector_id, len(seeded))
        await graph_provider.assert_no_orphan_records(connector_id)
        await graph_provider.assert_record_paths_or_names_contain(connector_id, seeded)

        logger.info(
            "TC-SYNC-001 passed: %d records for files %s (connector %s)",
            full_count,
            seeded,
            connector_id,
        )

    @pytest.mark.order(2)
    async def test_tc_incr_001_new_file_is_picked_up(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-001: A file added after the first sync appears on the next one."""
        connector_id = nextcloud_connector["connector_id"]

        before_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )

        new_name = "runbook.txt"
        nextcloud_source.write_files(
            [(f"handbook/{new_name}", "Drain traffic, then restart the service.\n")]
        )
        logger.info("Wrote new file %s", new_name)

        after_count = await sync_until_names_visible(
            pipeshub_client, graph_provider, connector_id, [new_name]
        )

        assert after_count > before_count, (
            f"TC-INCR-001: expected a new record for {new_name}; count stayed "
            f"at {after_count}"
        )
        await graph_provider.assert_record_paths_or_names_contain(
            connector_id, [new_name]
        )
        logger.info(
            "TC-INCR-001 passed: before=%d, after=%d, new=%s",
            before_count,
            after_count,
            new_name,
        )

    @pytest.mark.order(3)
    async def test_tc_update_001_editing_a_file_does_not_duplicate_it(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-UPDATE-001: An edited file updates its record rather than adding one.

        A file whose contents change keeps its path, so re-indexing must update
        the existing record. A second record for the same path is a duplicate,
        which reaches users as the same document appearing twice in search.
        """
        connector_id = nextcloud_connector["connector_id"]
        rel_path = "handbook/onboarding.txt"
        file_name = Path(rel_path).name

        before_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )

        # The connector increments a record's version when it updates one, so
        # comparing it is what separates "re-indexed" from "left alone". A count
        # that holds steady proves only that nothing was duplicated — a
        # connector that ignored the change entirely would also pass that.
        before_record = await graph_provider.get_record_by_name(
            connector_id, file_name
        )
        assert before_record is not None, (
            f"TC-UPDATE-001: {file_name} is not in the graph before the change"
        )
        before_version = before_record.get("version")

        nextcloud_source.overwrite_file(
            rel_path, "Updated: how to set up a new workspace in 2026.\n"
        )
        logger.info("Overwrote %s", rel_path)

        after_count = await sync_until_names_visible(
            pipeshub_client, graph_provider, connector_id, [file_name]
        )

        assert after_count == before_count, (
            f"TC-UPDATE-001: record count moved from {before_count} to "
            f"{after_count} after editing one file. An edit must update the "
            "existing record, not create a second one."
        )
        after_record = await graph_provider.get_record_by_name(
            connector_id, file_name
        )
        assert after_record is not None, (
            f"TC-UPDATE-001: {file_name} disappeared from the graph after the change"
        )
        assert after_record.get("version") != before_version, (
            f"TC-UPDATE-001: version stayed at {before_version} after the content "
            "changed, so the record was never re-indexed. The count assertion "
            "above would have passed regardless."
        )
        await graph_provider.assert_no_orphan_records(connector_id)
        logger.info(
            "TC-UPDATE-001 passed: %s updated in place, count stable at %d",
            file_name,
            after_count,
        )

