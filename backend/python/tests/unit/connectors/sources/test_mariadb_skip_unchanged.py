"""A MariaDB sync re-indexes a table only when the content indexing receives changed.

These wire the connector to a real ``DataSourceEntitiesProcessor`` (only the graph
transaction and the broker are faked), so they assert the events that actually reach
``record-events`` after ``_process_record`` has applied its indexing-status rules.
"""

import copy
import logging
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.sources.mariadb.connector import MariaDBConnector, MariaDBTable
from app.models.entities import Record
from app.sources.client.mariadb.mariadb import MariaDBResponse

DB = "shop"


def _ok(data: Any) -> MariaDBResponse:
    return MariaDBResponse(success=True, data=data)


class FakeMariaDB:
    """Answers the MariaDBDataSource calls the connector makes, from in-memory tables."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, Any]] = {}
        self.broken: set[str] = set()

    def add(self, name: str, rows: list[dict[str, Any]], columns: list[str] = ("id", "name")) -> None:
        self.tables[name] = {
            "columns": [{"name": c, "data_type": "int" if c == "id" else "varchar"} for c in columns],
            "rows": rows,
            "foreign_keys": [],
            "ddl": f"CREATE TABLE `{name}` ({', '.join(columns)})",
        }

    async def list_tables(self, database=None):
        return _ok([{"name": n, "database": DB, "type": "BASE TABLE"} for n in sorted(self.tables)])

    async def get_table_info(self, table, database=None):
        if table not in self.tables:
            return MariaDBResponse(success=False, error="Table not found")
        return _ok({"name": table, "columns": self.tables[table]["columns"]})

    async def get_foreign_keys(self, table, database=None):
        return _ok(list(self.tables[table]["foreign_keys"]))

    async def get_primary_keys(self, table, database=None):
        return _ok([{"column_name": "id"}])

    async def fetch_table_rows(self, database, table, limit=None):
        if table in self.broken:
            raise RuntimeError("SELECT command denied")
        return [dict(r) for r in self.tables[table]["rows"][:limit]]

    async def get_table_ddl(self, table, database=None):
        return _ok({"ddl": self.tables[table]["ddl"]})

    async def get_table_stats(self, databases=None):
        return _ok([
            {"database_name": DB, "table_name": n, "n_live_tup": len(t["rows"])}
            for n, t in sorted(self.tables.items())
        ])


class Harness:
    def __init__(self) -> None:
        self.stored: dict[str, Record] = {}
        self.published: list[tuple[str, str]] = []
        self.source = FakeMariaDB()

        tx_store = AsyncMock()
        tx_store.get_record_by_external_id = AsyncMock(
            side_effect=lambda connector_id, external_id: copy.deepcopy(self.stored.get(external_id))
        )
        tx_store.get_record_by_key = AsyncMock(return_value=None)
        tx_store.get_record_group_by_external_id = AsyncMock(return_value=None)
        tx_store.delete_edges_by_relationship_types = AsyncMock(return_value=0)
        tx_store.get_records_by_record_type = AsyncMock(
            side_effect=lambda connector_id, record_type: [copy.deepcopy(r) for r in self.stored.values()]
        )

        async def _upsert(records):
            for r in records:
                self.stored[r.external_record_id] = copy.deepcopy(r)

        tx_store.batch_upsert_records = AsyncMock(side_effect=_upsert)

        @asynccontextmanager
        async def _transaction():
            yield tx_store

        store_provider = MagicMock()
        store_provider.graph_provider = None
        store_provider.transaction = _transaction
        store_provider.compare_and_set_indexing_status = AsyncMock(
            side_effect=lambda ids, expected, new_status: list(ids)
        )

        processor = DataSourceEntitiesProcessor(
            logging.getLogger("test.mariadb.unchanged"), store_provider, AsyncMock()
        )
        processor.org_id = "org-1"
        processor.messaging_producer = AsyncMock()

        async def _send_message(topic, message, key=None):
            self.published.append((message["eventType"], message["payload"]["recordId"]))
            return True

        async def _send_messages(topic, messages):
            for _key, message in messages:
                self.published.append((message["eventType"], message["payload"]["recordId"]))
            return [True] * len(messages)

        processor.messaging_producer.send_message = AsyncMock(side_effect=_send_message)
        processor.messaging_producer.send_messages = AsyncMock(side_effect=_send_messages)
        processor.on_record_deleted = AsyncMock(side_effect=self._delete)

        connector = MariaDBConnector(
            logger=logging.getLogger("test.mariadb.unchanged"),
            data_entities_processor=processor,
            data_store_provider=MagicMock(),
            config_service=MagicMock(),
            connector_id="conn-1",
        )
        connector.data_source = self.source
        connector.database_name = DB
        connector._create_app_users = AsyncMock()
        connector._ensure_database_record_groups = AsyncMock()
        connector.tables_sync_point = MagicMock()
        connector.tables_sync_point.update_sync_point = AsyncMock()
        self.connector = connector
        self.processor = processor

    async def _delete(self, record_id: str) -> bool:
        for fqn, r in list(self.stored.items()):
            if r.id == record_id:
                del self.stored[fqn]
        return False

    def index_everything(self) -> None:
        for r in self.stored.values():
            r.indexing_status = ProgressStatus.COMPLETED.value

    def record(self, table: str) -> Record:
        return self.stored[f"{DB}.{table}"]

    async def full_sync(self) -> list[tuple[str, str]]:
        self.published.clear()
        await self.connector._run_full_sync_internal()
        return list(self.published)


async def _indexed_harness() -> Harness:
    h = Harness()
    h.source.add("orders", [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])
    h.source.add("customers", [{"id": 1, "name": "ann"}])
    first = await h.full_sync()
    assert sorted(first) == sorted(
        [("newRecord", h.record("orders").id), ("newRecord", h.record("customers").id)]
    )
    h.index_everything()
    return h


class TestFullSyncSkipsUnchangedTables:

    @pytest.mark.asyncio
    async def test_unchanged_tables_are_not_queued_again(self):
        h = await _indexed_harness()
        assert await h.full_sync() == []
        assert h.record("orders").indexing_status == ProgressStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_changed_rows_are_queued_again(self):
        h = await _indexed_harness()
        h.source.tables["orders"]["rows"][0]["name"] = "changed"
        assert await h.full_sync() == [("newRecord", h.record("orders").id)]

    @pytest.mark.asyncio
    async def test_schema_change_is_queued_again(self):
        h = await _indexed_harness()
        h.source.tables["customers"]["columns"].append({"name": "email", "data_type": "varchar"})
        h.source.tables["customers"]["ddl"] = "CREATE TABLE `customers` (id, name, email)"
        assert await h.full_sync() == [("newRecord", h.record("customers").id)]

    @pytest.mark.asyncio
    async def test_record_saved_before_fingerprints_is_queued_once(self):
        h = await _indexed_harness()
        h.record("orders").external_revision_id = "1712345678901"
        assert await h.full_sync() == [("newRecord", h.record("orders").id)]
        h.index_everything()
        assert await h.full_sync() == []

    @pytest.mark.asyncio
    async def test_unreadable_table_is_queued_again(self):
        h = await _indexed_harness()
        h.source.broken.add("orders")
        assert await h.full_sync() == [("newRecord", h.record("orders").id)]

    @pytest.mark.asyncio
    async def test_unchanged_table_that_failed_indexing_is_retried(self):
        h = await _indexed_harness()
        h.record("customers").indexing_status = ProgressStatus.FAILED.value
        assert await h.full_sync() == [("newRecord", h.record("customers").id)]

    @pytest.mark.asyncio
    async def test_dropped_table_is_still_deleted(self):
        h = await _indexed_harness()
        dropped_id = h.record("customers").id
        del h.source.tables["customers"]
        assert await h.full_sync() == []
        h.processor.on_record_deleted.assert_awaited_once_with(dropped_id)
        assert f"{DB}.customers" not in h.stored

    @pytest.mark.asyncio
    async def test_resync_keeps_the_stored_record_id_and_version(self):
        h = await _indexed_harness()
        before = h.record("orders")
        await h.full_sync()
        after = h.record("orders")
        assert after.id == before.id
        assert after.version == before.version
        assert after.source_updated_at == before.source_updated_at


class TestIncrementalSyncSkipsUnchangedContent:

    @pytest.mark.asyncio
    async def test_stats_change_without_content_change_is_not_queued(self):
        h = await _indexed_harness()
        h.published.clear()
        await h.connector._sync_updated_tables(DB, [MariaDBTable(name="orders", database_name=DB)])
        assert h.published == []

    @pytest.mark.asyncio
    async def test_content_change_is_queued(self):
        h = await _indexed_harness()
        h.published.clear()
        h.source.tables["orders"]["rows"].append({"id": 3, "name": "c"})
        await h.connector._sync_updated_tables(DB, [MariaDBTable(name="orders", database_name=DB)])
        assert h.published == [("updateRecord", h.record("orders").id)]


class TestTableFingerprint:

    @pytest.mark.asyncio
    async def test_foreign_key_order_does_not_change_it(self):
        h = Harness()
        h.source.add("orders", [{"id": 1, "name": "a"}])
        fks = [
            {"constraint_name": "fk_a", "column_name": "a", "foreign_database": DB,
             "foreign_table_name": "x", "foreign_column_name": "id"},
            {"constraint_name": "fk_b", "column_name": "b", "foreign_database": DB,
             "foreign_table_name": "y", "foreign_column_name": "id"},
        ]
        h.source.tables["orders"]["foreign_keys"] = fks
        first = await h.connector._table_fingerprint(DB, "orders")
        h.source.tables["orders"]["foreign_keys"] = list(reversed(fks))
        assert await h.connector._table_fingerprint(DB, "orders") == first

    @pytest.mark.asyncio
    async def test_row_limit_change_changes_it(self):
        h = Harness()
        h.source.add("orders", [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])
        first = await h.connector._table_fingerprint(DB, "orders")
        h.connector._max_rows_per_table = lambda _filters: 1
        assert await h.connector._table_fingerprint(DB, "orders") != first

    @pytest.mark.asyncio
    async def test_none_when_a_part_cannot_be_read(self):
        h = Harness()
        h.source.add("orders", [{"id": 1, "name": "a"}])
        h.source.broken.add("orders")
        assert await h.connector._table_fingerprint(DB, "orders") is None
