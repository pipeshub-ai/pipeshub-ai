# pyright: ignore-file

"""
PostgreSQL Connector – Integration Tests
========================================

Tests receive a fully set-up connector via the ``postgres_connector`` fixture
(defined in conftest.py), which creates the tables, creates the connector and
waits for a full sync, then tears both down.

PostgreSQL here is a connector *source* — a database to be indexed — not a store
the platform uses. Like MinIO, it runs in the integration stack, so this
connector has live coverage without an external database.

Test cases:
  TC-SYNC-001 — Full sync + graph validation
  TC-INCR-001 — A table added after the first sync appears on the next one
  TC-ROWS-001 — Adding rows to an existing table does not duplicate its record
"""

import logging
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from connectors.postgres.postgres_source_helper import (  # type: ignore[import-not-found]
    PostgresSourceHelper,
)
from helper.graph_provider import GraphProviderProtocol
from helper.storage_incremental import (
    settle_record_baseline,
    sync_until_names_visible,
)
from pipeshub_client import (
    PipeshubClient,  # type: ignore[import-not-found]
)

logger = logging.getLogger("postgres-lifecycle-test")


@pytest.mark.integration
@pytest.mark.postgres
@pytest.mark.asyncio(loop_scope="session")
class TestPostgresConnector:
    """Lifecycle coverage for the PostgreSQL connector."""

    @pytest.mark.order(1)
    async def test_tc_sync_001_full_sync_graph_validation(
        self,
        postgres_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: Every seeded table is in the graph after a full sync."""
        connector_id = postgres_connector["connector_id"]
        seeded = postgres_connector["seeded_tables"]
        full_count = postgres_connector["full_sync_count"]

        await graph_provider.assert_min_records(connector_id, len(seeded))
        await graph_provider.assert_no_orphan_records(connector_id)
        await graph_provider.assert_record_paths_or_names_contain(connector_id, seeded)

        logger.info(
            "TC-SYNC-001 passed: %d records for tables %s (connector %s)",
            full_count,
            seeded,
            connector_id,
        )

    @pytest.mark.order(2)
    async def test_tc_incr_001_new_table_is_picked_up(
        self,
        postgres_connector: dict[str, Any],
        postgres_source: PostgresSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-001: A table created after the first sync appears on the next one."""
        connector_id = postgres_connector["connector_id"]

        before_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )

        new_table = "runbooks"
        postgres_source.create_table_with_rows(
            new_table,
            [
                ("Restart procedure", "Drain traffic, then restart the service."),
                ("Backup restore", "Restore from the most recent nightly snapshot."),
            ],
        )
        logger.info("Created new table %s for incremental sync", new_table)

        after_count = await sync_until_names_visible(
            pipeshub_client, graph_provider, connector_id, [new_table]
        )

        assert after_count > before_count, (
            f"TC-INCR-001: expected a new record for table {new_table}; count "
            f"stayed at {after_count}"
        )
        await graph_provider.assert_record_paths_or_names_contain(
            connector_id, [new_table]
        )
        postgres_connector["incr_table"] = new_table
        logger.info(
            "TC-INCR-001 passed: before=%d, after=%d, new table=%s",
            before_count,
            after_count,
            new_table,
        )

    @pytest.mark.order(3)
    async def test_tc_rows_001_adding_rows_does_not_duplicate_the_table_record(
        self,
        postgres_connector: dict[str, Any],
        postgres_source: PostgresSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-ROWS-001: Rows added to a synced table update it, not duplicate it.

        The connector syncs one record per table. Re-indexing a table whose rows
        changed must update that record; a second record for the same table is
        the duplicate this guards against.
        """
        connector_id = postgres_connector["connector_id"]
        table = "articles"

        before_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )

        # The connector increments a record's version when it updates one, so
        # comparing it is what separates "re-indexed" from "left alone". A count
        # that holds steady proves only that nothing was duplicated — a
        # connector that ignored the change entirely would also pass that.
        before_record = await graph_provider.get_record_by_name(
            connector_id, table
        )
        assert before_record is not None, (
            f"TC-ROWS-001: {table} is not in the graph before the change"
        )
        before_version = before_record.get("version")
        rows_before = postgres_source.row_count(table)

        postgres_source.insert_rows(
            table, [("Escalation paths", "Who to page, and when.")]
        )
        assert postgres_source.row_count(table) == rows_before + 1

        after_count = await sync_until_names_visible(
            pipeshub_client, graph_provider, connector_id, [table]
        )

        assert after_count == before_count, (
            f"TC-ROWS-001: record count moved from {before_count} to {after_count} "
            f"after adding a row to {table}. Rows changing must update the "
            "table's record, not create another one."
        )
        after_record = await graph_provider.get_record_by_name(
            connector_id, table
        )
        assert after_record is not None, (
            f"TC-ROWS-001: {table} disappeared from the graph after the change"
        )
        assert after_record.get("version") != before_version, (
            f"TC-ROWS-001: version stayed at {before_version} after the content "
            "changed, so the record was never re-indexed. The count assertion "
            "above would have passed regardless."
        )
        await graph_provider.assert_no_orphan_records(connector_id)
        logger.info(
            "TC-ROWS-001 passed: %s updated in place, count stable at %d",
            table,
            after_count,
        )

