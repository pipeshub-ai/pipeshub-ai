# pyright: ignore-file

"""
Local FS Connector – Integration Tests
======================================

Tests receive a fully set-up connector via the ``localfs_connector`` fixture
(defined in conftest.py), which writes the files, creates the connector and
waits for a full sync, then tears both down.

This is the cheapest connector to cover: the "provider" is a directory. The
integration compose file bind-mounts one into the connector container, so the
test writes files on the host side and the connector reads the same bytes on the
container side.

Test cases:
  TC-SYNC-001   — Full sync picks up every file, including nested folders
  TC-INCR-001   — A file added after the first sync appears on the next one
  TC-UPDATE-001 — Editing a file updates its record rather than adding one
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
from connectors.localfs.localfs_source_helper import (  # type: ignore[import-not-found]  # noqa: E402
    LocalFsSourceHelper,
)

logger = logging.getLogger("localfs-lifecycle-test")


@pytest.mark.integration
@pytest.mark.localfs
@pytest.mark.asyncio(loop_scope="session")
class TestLocalFsConnector:
    """Lifecycle coverage for the Local FS connector."""

    @pytest.mark.order(1)
    async def test_tc_sync_001_full_sync_graph_validation(
        self,
        localfs_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: Every seeded file is in the graph, nested ones included.

        The seed puts files in two subfolders, so this also covers
        include_subfolders — a connector that only read the top level would
        find nothing and fail here.
        """
        connector_id = localfs_connector["connector_id"]
        seeded = localfs_connector["seeded_names"]
        full_count = localfs_connector["full_sync_count"]

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
        localfs_connector: Dict[str, Any],
        localfs_source: LocalFsSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-001: A file added after the first sync appears on the next one."""
        connector_id = localfs_connector["connector_id"]

        before_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )

        new_name = "runbook.txt"
        localfs_source.write_files(
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
        localfs_connector: Dict[str, Any],
        localfs_source: LocalFsSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-UPDATE-001: An edited file updates its record rather than adding one.

        A file whose contents change keeps its path, so re-indexing must update
        the existing record. A second record for the same path is a duplicate,
        which reaches users as the same document appearing twice in search.
        """
        connector_id = localfs_connector["connector_id"]
        rel_path = "handbook/onboarding.txt"
        file_name = Path(rel_path).name

        before_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )

        localfs_source.overwrite_file(
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
        await graph_provider.assert_no_orphan_records(connector_id)
        logger.info(
            "TC-UPDATE-001 passed: %s updated in place, count stable at %d",
            file_name,
            after_count,
        )
