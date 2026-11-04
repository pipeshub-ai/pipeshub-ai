# pyright: ignore-file

"""
Snowflake Connector – Integration Tests
=======================================

Tests receive a connector via the ``snowflake_connector`` fixture (conftest.py),
which seeds a schema of its own in a real Snowflake account, creates the
connector limited to it, and waits for the first sync.

Test cases:
  TC-SYNC-001   — First sync: tables, view, stages and a stage file, column records, foreign keys
  TC-STREAM-001 — A table streams its rows; a column streams live value counts,
                  none for a column that looks personal
  TC-INCR-001   — A table added after the first sync appears on the next one
  TC-ROWS-001   — Rows added to a table update its record, not duplicate it
  TC-RENAME-001 — A renamed table keeps its record, and the foreign key follows it
  TC-RENAME-002 — A renamed column keeps its record
  TC-DEL-001    — A dropped table's record and its column records are removed
  TC-FULL-001   — A full sync keeps every record it wrote
  TC-REVOKE-001 — A schema the connector's role loses is removed with its records
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

from connectors.snowflake.conftest import (  # type: ignore[import-not-found]
    BARE_STAGE,
    CUSTOMERS,
    DROPPED_TABLE,
    HIDDEN_TABLE,
    NEW_TABLE,
    NOTES,
    ORDERS,
    SEEDED_COLUMNS,
    SEEDED_TABLES,
    STAGE,
    VIEW,
)
from connectors.snowflake.snowflake_source_helper import (  # type: ignore[import-not-found]
    SnowflakeSourceHelper,
)
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_for_sync_completion
from helper.storage_incremental import (
    restart_sync,
    settle_record_baseline,
    sync_until_names_absent,
    sync_until_names_visible,
)
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("snowflake-lifecycle-test")


async def _column(graph_provider: GraphProviderProtocol, connector_id: str, external_id: str) -> dict[str, Any] | None:
    for record in await graph_provider.fetch_records_by_type(connector_id, "SQL_COLUMN"):
        if record.get("externalRecordId") == external_id:
            return record
    return None


async def _sync_once(pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol, connector_id: str) -> None:
    restart_sync(pipeshub_client, connector_id)
    await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=600)


@pytest.mark.integration
@pytest.mark.snowflake
@pytest.mark.asyncio(loop_scope="session")
class TestSnowflakeConnector:
    """Lifecycle coverage for the Snowflake connector on a live account."""

    @pytest.mark.order(1)
    async def test_tc_sync_001_first_sync(
        self,
        snowflake_connector: dict[str, Any],
        snowflake_source: SnowflakeSourceHelper,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: Tables, the view, the stage file, column records and foreign keys."""
        connector_id = snowflake_connector["connector_id"]
        names = set(await graph_provider.fetch_record_names(connector_id))
        missing = {*SEEDED_TABLES, HIDDEN_TABLE, VIEW, "orders.csv"} - names
        assert not missing, f"TC-SYNC-001: not in the graph after the first sync: {sorted(missing)}"

        columns = await graph_provider.count_records_by_type(connector_id, "SQL_COLUMN")
        # The hidden schema's table adds two more.
        assert columns == SEEDED_COLUMNS + 2, f"TC-SYNC-001: expected {SEEDED_COLUMNS + 2} column records, got {columns}"

        targets = await graph_provider.get_record_outgoing_relations(
            connector_id, snowflake_source.qualified(ORDERS), "FOREIGN_KEY",
        )
        assert snowflake_source.qualified(CUSTOMERS) in targets, (
            f"TC-SYNC-001: {ORDERS} should link to {CUSTOMERS} by its foreign key; links: {targets}"
        )
        groups = set(await graph_provider.fetch_record_group_names(connector_id))
        # A stage without a directory table has no files to list, but is still a group.
        missing_groups = {snowflake_connector["schema"], STAGE, BARE_STAGE} - groups
        assert not missing_groups, f"TC-SYNC-001: no group for {sorted(missing_groups)}; groups: {groups}"
        await graph_provider.assert_no_orphan_records(connector_id)
        logger.info("TC-SYNC-001 passed: %d records, %d columns", snowflake_connector["full_sync_count"], columns)

    @pytest.mark.order(2)
    async def test_tc_stream_001_table_and_column_streams(
        self,
        snowflake_connector: dict[str, Any],
        snowflake_source: SnowflakeSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-STREAM-001: A table streams its rows; a column streams its values, unless it looks personal."""
        connector_id = snowflake_connector["connector_id"]
        table = await graph_provider.get_record_by_name(connector_id, ORDERS)
        assert table is not None, f"TC-STREAM-001: {ORDERS} is not in the graph"
        body = pipeshub_client.stream_record(table["id"]).content
        data = json.loads(body)
        assert len(data.get("rows") or []) == 3, f"TC-STREAM-001: expected 3 rows of {ORDERS}: {str(data)[:500]}"

        status = await _column(graph_provider, connector_id, f"{snowflake_source.qualified(ORDERS)}.STATUS")
        assert status is not None, "TC-STREAM-001: ORDERS.STATUS has no column record"
        counts = json.loads(pipeshub_client.stream_record(status["id"]).content)
        assert {c["value"]: c["count"] for c in counts["value_counts"]} == {"OPEN": 2, "CLOSED": 1}, counts
        assert counts["value_counts_withheld"] is False
        assert counts["profile"]["distinct_count"] == 2, f"TC-STREAM-001: profile not stored: {counts['profile']}"

        email = await _column(graph_provider, connector_id, f"{snowflake_source.qualified(CUSTOMERS)}.EMAIL")
        assert email is not None, "TC-STREAM-001: CUSTOMERS.EMAIL has no column record"
        withheld = json.loads(pipeshub_client.stream_record(email["id"]).content)
        assert withheld["value_counts"] == [] and withheld["value_counts_withheld"] is True, withheld
        logger.info("TC-STREAM-001 passed")

    @pytest.mark.order(3)
    async def test_tc_incr_001_new_table_is_picked_up(
        self,
        snowflake_connector: dict[str, Any],
        snowflake_source: SnowflakeSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-001: A table created after the first sync appears on the next one, with its columns."""
        connector_id = snowflake_connector["connector_id"]
        before = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        await snowflake_source.run(
            f"CREATE TABLE {snowflake_source.qualified(NEW_TABLE)} (TITLE STRING, STEPS STRING)",
            f"INSERT INTO {snowflake_source.qualified(NEW_TABLE)} VALUES ('Restart', 'Drain, then restart')",
        )
        await snowflake_source.grant_reads()
        after = await sync_until_names_visible(pipeshub_client, graph_provider, connector_id, [NEW_TABLE])
        # The table and its two columns.
        assert after == before + 3, f"TC-INCR-001: expected {before + 3} records, got {after}"
        logger.info("TC-INCR-001 passed: %d -> %d", before, after)

    @pytest.mark.order(4)
    async def test_tc_rows_001_added_rows_update_the_record(
        self,
        snowflake_connector: dict[str, Any],
        snowflake_source: SnowflakeSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-ROWS-001: Rows added to a table update its record in place."""
        connector_id = snowflake_connector["connector_id"]
        before = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        record = await graph_provider.get_record_by_name(connector_id, ORDERS)
        await snowflake_source.run(f"INSERT INTO {snowflake_source.qualified(ORDERS)} VALUES (4, 3, 'OPEN', 1.00)")

        async def _updated() -> bool:
            current = await graph_provider.get_record_by_name(connector_id, ORDERS)
            return bool(current) and current.get("externalRevisionId") != record.get("externalRevisionId")

        for _ in range(3):
            await _sync_once(pipeshub_client, graph_provider, connector_id)
            if await _updated():
                break
        after_record = await graph_provider.get_record_by_name(connector_id, ORDERS)
        assert after_record["id"] == record["id"], "TC-ROWS-001: the table got a new record"
        assert after_record.get("externalRevisionId") != record.get("externalRevisionId"), (
            "TC-ROWS-001: the table's revision did not change after its rows did"
        )
        assert await graph_provider.count_records(connector_id) == before, "TC-ROWS-001: the record count moved"
        logger.info("TC-ROWS-001 passed")

    @pytest.mark.order(5)
    async def test_tc_rename_001_renamed_table_keeps_its_record(
        self,
        snowflake_connector: dict[str, Any],
        snowflake_source: SnowflakeSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-RENAME-001: A renamed table keeps its record id, and the foreign key follows it."""
        connector_id = snowflake_connector["connector_id"]
        await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        before = await graph_provider.get_record_by_name(connector_id, CUSTOMERS)
        await snowflake_source.run(
            f"ALTER TABLE {snowflake_source.qualified(CUSTOMERS)} RENAME TO {snowflake_source.qualified('CLIENTS')}"
        )
        await sync_until_names_visible(pipeshub_client, graph_provider, connector_id, ["CLIENTS"])
        after = await graph_provider.get_record_by_name(connector_id, "CLIENTS")
        assert after["id"] == before["id"], f"TC-RENAME-001: record id changed {before['id']} -> {after['id']}"
        assert await graph_provider.get_record_by_name(connector_id, CUSTOMERS) is None
        targets = await graph_provider.get_record_outgoing_relations(
            connector_id, snowflake_source.qualified(ORDERS), "FOREIGN_KEY",
        )
        assert targets == [snowflake_source.qualified("CLIENTS")], f"TC-RENAME-001: foreign key links: {targets}"
        logger.info("TC-RENAME-001 passed")

    @pytest.mark.order(6)
    async def test_tc_rename_002_renamed_column_keeps_its_record(
        self,
        snowflake_connector: dict[str, Any],
        snowflake_source: SnowflakeSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-RENAME-002: A renamed column keeps its record id."""
        connector_id = snowflake_connector["connector_id"]
        await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        notes = snowflake_source.qualified(NOTES)
        before = await _column(graph_provider, connector_id, f"{notes}.NOTE")
        assert before is not None, "TC-RENAME-002: NOTES.NOTE has no column record"
        await snowflake_source.run(f"ALTER TABLE {notes} RENAME COLUMN NOTE TO BODY")

        async def _renamed() -> bool:
            return await _column(graph_provider, connector_id, f"{notes}.BODY") is not None

        for _ in range(3):
            await _sync_once(pipeshub_client, graph_provider, connector_id)
            if await _renamed():
                break
        after = await _column(graph_provider, connector_id, f"{notes}.BODY")
        assert after is not None and after["id"] == before["id"], f"TC-RENAME-002: {before} -> {after}"
        assert await _column(graph_provider, connector_id, f"{notes}.NOTE") is None
        logger.info("TC-RENAME-002 passed")

    @pytest.mark.order(7)
    async def test_tc_del_001_dropped_table_is_removed(
        self,
        snowflake_connector: dict[str, Any],
        snowflake_source: SnowflakeSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DEL-001: A dropped table's record and its column record go, and nothing else."""
        connector_id = snowflake_connector["connector_id"]
        before = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        await snowflake_source.run(f"DROP TABLE {snowflake_source.qualified(DROPPED_TABLE)}")
        await sync_until_names_absent(pipeshub_client, graph_provider, connector_id, [DROPPED_TABLE])
        after = await graph_provider.count_records(connector_id)
        assert after == before - 2, f"TC-DEL-001: expected {before - 2} records, got {after}"
        logger.info("TC-DEL-001 passed")

    @pytest.mark.order(8)
    async def test_tc_full_001_full_sync_keeps_everything(
        self,
        snowflake_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FULL-001: A full sync unlinks only what it did not write, so it keeps every record."""
        connector_id = snowflake_connector["connector_id"]
        before = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        names_before = set(await graph_provider.fetch_record_names(connector_id))
        pipeshub_client.resync_connector(connector_id, full_sync=True, connector_name="Snowflake")
        await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=600)
        after = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        assert after == before, f"TC-FULL-001: record count moved from {before} to {after}"
        assert set(await graph_provider.fetch_record_names(connector_id)) == names_before
        await graph_provider.assert_no_orphan_records(connector_id)
        logger.info("TC-FULL-001 passed: %d records kept", after)

    @pytest.mark.order(9)
    async def test_tc_revoke_001_a_schema_the_role_loses_is_removed(
        self,
        snowflake_connector: dict[str, Any],
        snowflake_source: SnowflakeSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-REVOKE-001: A schema the connector's role loses is removed with its records.

        Snowflake lists only what the role may see, so a revoked schema is gone
        from the connector's view, as a dropped one is; its data must not stay
        searchable through the connector.
        """
        connector_id = snowflake_connector["connector_id"]
        before = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        await snowflake_source.revoke_schema(snowflake_connector["hidden_schema"])
        await sync_until_names_absent(pipeshub_client, graph_provider, connector_id, [HIDDEN_TABLE])
        after = await graph_provider.count_records(connector_id)
        # The table and its two columns.
        assert after == before - 3, f"TC-REVOKE-001: expected {before - 3} records, got {after}"
        groups = set(await graph_provider.fetch_record_group_names(connector_id))
        assert snowflake_connector["hidden_schema"] not in groups, f"TC-REVOKE-001: schema group kept: {groups}"
        logger.info("TC-REVOKE-001 passed")
