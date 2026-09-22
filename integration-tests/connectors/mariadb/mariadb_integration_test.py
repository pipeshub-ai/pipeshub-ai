# pyright: ignore-file

"""
MariaDB Connector – Integration Tests
=====================================

Tests receive a fully set-up connector via the ``mariadb_connector`` fixture
(defined in conftest.py), which creates the tables, creates the connector and
waits for a full sync, then tears both down.

MariaDB here is a connector *source* — a database to be indexed — that runs in
the integration stack, so this connector has live coverage without an external
database.

Test cases:
  TC-SYNC-001   — Full sync: every base table is a record, views are not
  TC-STREAM-001 — A table's record streams its columns, keys, rows and DDL
  TC-INCR-001   — A table added after the first sync appears on the next one
  TC-ROWS-001   — Updating a row in a table with no key re-indexes that table
  TC-DEL-001    — A dropped table's record is removed
  TC-FILTER-001 — A table the sync filter excludes is removed from the graph
"""

import json
import logging
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from connectors.mariadb.mariadb_seed import (  # type: ignore[import-not-found]
    CHILD_TABLE,
    DROPPED_TABLE,
    EXCLUDED_TABLE,
    KEYED_TABLE,
    KEYLESS_TABLE,
    NEW_TABLE,
    SEED_TABLES,
    VIEW,
)
from connectors.mariadb.mariadb_source_helper import (  # type: ignore[import-not-found]
    MariaDBSourceHelper,
)
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import (
    wait_for_sync_completion,
    wait_until_graph_condition,
)
from helper.storage_incremental import (
    DEFAULT_SYNC_TIMEOUT_SEC,
    restart_sync,
    settle_record_baseline,
    sync_until_names_absent,
    sync_until_names_visible,
    wait_for_record_reindex,
)
from pipeshub_client import (
    PipeshubClient,  # type: ignore[import-not-found]
)

logger = logging.getLogger("mariadb-lifecycle-test")


@pytest.mark.integration
@pytest.mark.mariadb
@pytest.mark.asyncio(loop_scope="session")
class TestMariaDBConnector:
    """Lifecycle coverage for the MariaDB connector."""

    @pytest.mark.order(1)
    async def test_tc_sync_001_full_sync_graph_validation(
        self,
        mariadb_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: Every seeded base table is in the graph; the view is not."""
        connector_id = mariadb_connector["connector_id"]
        seeded = mariadb_connector["seeded_tables"]

        await graph_provider.assert_min_records(connector_id, len(seeded))
        await graph_provider.assert_no_orphan_records(connector_id)
        await graph_provider.assert_record_names_contain(connector_id, seeded)
        await graph_provider.assert_record_not_exists(connector_id, VIEW)

        groups = await graph_provider.fetch_record_group_names(connector_id)
        assert mariadb_connector["database"] in groups, (
            f"TC-SYNC-001: expected a record group for database "
            f"{mariadb_connector['database']}; found {groups}"
        )
        logger.info(
            "TC-SYNC-001 passed: %d records for tables %s (connector %s)",
            mariadb_connector["full_sync_count"],
            seeded,
            connector_id,
        )

    @pytest.mark.order(2)
    async def test_tc_stream_001_table_record_streams_its_content(
        self,
        mariadb_connector: dict[str, Any],
        mariadb_source: MariaDBSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-STREAM-001: Streaming a table's record returns what indexing reads.

        Indexing builds the table's searchable text from this payload, so a
        missing column list, key or row here is missing from search.
        """
        connector_id = mariadb_connector["connector_id"]
        record = await graph_provider.get_record_by_external_id(
            connector_id, mariadb_source.fqn(KEYED_TABLE)
        )
        assert record is not None, f"TC-STREAM-001: {KEYED_TABLE} is not in the graph"

        response = pipeshub_client.stream_record(record.id)
        assert response.status_code == 200
        payload = json.loads(response.content)

        assert payload["table_name"] == KEYED_TABLE
        assert payload["database_name"] == mariadb_source.database
        assert payload["primary_keys"] == ["id"]
        assert [c["name"] for c in payload["columns"]] == ["id", "title", "body"]
        titles = sorted(row["title"] for row in payload["rows"])
        assert titles == sorted(title for title, _ in SEED_TABLES[KEYED_TABLE])
        assert "CREATE TABLE" in payload["ddl"]

        child = await graph_provider.get_record_by_external_id(
            connector_id, mariadb_source.fqn(CHILD_TABLE)
        )
        assert child is not None, f"TC-STREAM-001: {CHILD_TABLE} is not in the graph"
        child_payload = json.loads(pipeshub_client.stream_record(child.id).content)
        assert [
            (fk["column_name"], fk["foreign_table_name"], fk["foreign_column_name"])
            for fk in child_payload["foreign_keys"]
        ] == [("parent_id", KEYED_TABLE, "id")]
        logger.info("TC-STREAM-001 passed: %s and %s streamed", KEYED_TABLE, CHILD_TABLE)

    @pytest.mark.order(3)
    async def test_tc_incr_001_new_table_is_picked_up(
        self,
        mariadb_connector: dict[str, Any],
        mariadb_source: MariaDBSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-001: A table created after the first sync appears on the next one."""
        connector_id = mariadb_connector["connector_id"]
        before_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )

        mariadb_source.create_table_with_rows(
            NEW_TABLE,
            [
                ("Restart procedure", "Drain traffic, then restart the service."),
                ("Backup restore", "Restore from the most recent nightly snapshot."),
            ],
        )
        after_count = await sync_until_names_visible(
            pipeshub_client, graph_provider, connector_id, [NEW_TABLE]
        )

        assert after_count == before_count + 1, (
            f"TC-INCR-001: expected exactly one new record for {NEW_TABLE}; "
            f"count went from {before_count} to {after_count}"
        )
        logger.info("TC-INCR-001 passed: before=%d, after=%d", before_count, after_count)

    @pytest.mark.order(4)
    async def test_tc_rows_001_updating_a_row_reindexes_a_keyless_table(
        self,
        mariadb_connector: dict[str, Any],
        mariadb_source: MariaDBSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-ROWS-001: An in-place update to a table with no key is noticed.

        MariaDB has no per-table write counters. An update that inserts nothing
        leaves the row estimate and auto-increment alone, so the table's update
        time is the only signal, and a table with no key has no auto-increment
        to fall back on. The record must be re-indexed in place: a higher
        version, and no second record.
        """
        connector_id = mariadb_connector["connector_id"]
        table = KEYLESS_TABLE
        before_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )
        before_record = await graph_provider.get_record_by_name(connector_id, table)
        assert before_record is not None, f"TC-ROWS-001: {table} is not in the graph"
        before_version = before_record.get("version")

        title = SEED_TABLES[table][0][0]
        changed = mariadb_source.update_body(table, title, "Annual leave accrues weekly.")
        assert changed == 1, f"TC-ROWS-001: expected to update one row, updated {changed}"

        restart_sync(pipeshub_client, connector_id)
        after_record = await wait_for_record_reindex(
            graph_provider, connector_id, table, before_version
        )
        after_version = after_record.get("version")
        assert isinstance(before_version, int) and isinstance(after_version, int), (
            f"TC-ROWS-001: version should be an int on both reads, got "
            f"{before_version!r} then {after_version!r}"
        )
        assert after_version > before_version, (
            f"TC-ROWS-001: version went from {before_version} to {after_version}; "
            "it must increase when the table is re-indexed"
        )

        after_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )
        assert after_count == before_count, (
            f"TC-ROWS-001: record count moved from {before_count} to {after_count} "
            f"after updating a row in {table}; it must update the record, not add one"
        )
        logger.info(
            "TC-ROWS-001 passed: %s version %s -> %s, count stable at %d",
            table,
            before_version,
            after_version,
            after_count,
        )

    @pytest.mark.order(5)
    async def test_tc_del_001_dropped_table_is_removed(
        self,
        mariadb_connector: dict[str, Any],
        mariadb_source: MariaDBSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DEL-001: Dropping a table removes its record, and only its record."""
        connector_id = mariadb_connector["connector_id"]

        mariadb_source.create_table_with_rows(
            DROPPED_TABLE, [("Temporary", "Only here to be dropped.")]
        )
        with_table = await sync_until_names_visible(
            pipeshub_client, graph_provider, connector_id, [DROPPED_TABLE]
        )

        mariadb_source.drop_table(DROPPED_TABLE)
        await sync_until_names_absent(
            pipeshub_client, graph_provider, connector_id, [DROPPED_TABLE]
        )

        after_count = await settle_record_baseline(
            pipeshub_client, graph_provider, connector_id
        )
        assert after_count == with_table - 1, (
            f"TC-DEL-001: expected one record fewer after dropping {DROPPED_TABLE}; "
            f"count went from {with_table} to {after_count}"
        )
        await graph_provider.assert_record_names_contain(
            connector_id, mariadb_connector["seeded_tables"]
        )
        await graph_provider.assert_no_orphan_records(connector_id)
        logger.info("TC-DEL-001 passed: %s removed, count %d", DROPPED_TABLE, after_count)

    @pytest.mark.order(6)
    async def test_tc_filter_001_excluded_table_is_removed(
        self,
        mariadb_connector: dict[str, Any],
        mariadb_source: MariaDBSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FILTER-001: Excluding a table with the sync filter removes its record.

        Changing a sync filter forces a full sync. That sync must take out what
        the filter now leaves out, not only stop adding it: an excluded table
        that stays in the graph stays searchable.
        """
        connector_id = mariadb_connector["connector_id"]
        excluded = EXCLUDED_TABLE
        await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        assert await graph_provider.get_record_by_name(connector_id, excluded) is not None

        pipeshub_client.update_connector_filters_sync_safe(
            connector_id,
            filters={
                "sync": {
                    "values": {
                        "tables": {
                            "operator": "not_in",
                            "value": [mariadb_source.fqn(excluded)],
                            "type": "multiselect",
                        }
                    }
                }
            },
        )

        async def _excluded_gone() -> bool:
            return await graph_provider.get_record_by_name(connector_id, excluded) is None

        await wait_until_graph_condition(
            connector_id,
            check=_excluded_gone,
            timeout=DEFAULT_SYNC_TIMEOUT_SEC,
            poll_interval=10,
            description=f"removal of filtered-out {excluded}",
        )
        await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id, timeout=DEFAULT_SYNC_TIMEOUT_SEC
        )

        kept = [t for t in mariadb_connector["seeded_tables"] if t != excluded]
        await graph_provider.assert_record_names_contain(connector_id, kept)
        await graph_provider.assert_record_not_exists(connector_id, excluded)
        logger.info("TC-FILTER-001 passed: %s excluded and removed; %s kept", excluded, kept)
