"""A MariaDB sync re-indexes a table only when the content indexing receives changed.

These wire the connector to a real ``DataSourceEntitiesProcessor`` (only the graph
transaction and the broker are faked), so they assert the events that actually reach
``record-events`` after ``_process_record`` has applied its indexing-status rules.
"""

import copy
import logging
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.mariadb.connector import MariaDBConnector
from app.models.entities import Record, RecordType
from app.sources.client.mariadb.mariadb import MariaDBResponse
from app.sources.external.mariadb.mariadb_ import (
    CatalogTable,
    ColumnInfo,
    ForeignKeyInfo,
)

DB = "shop"


def _ok(data: Any) -> MariaDBResponse:  # noqa: ANN401
    return MariaDBResponse(success=True, data=data)


class FakeMariaDB:
    """Answers the MariaDBDataSource calls the connector makes, from in-memory tables."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, Any]] = {}
        self.broken: set[str] = set()
        self.row_orders: dict[str, Any] = {}
        self.next_id = 100

    def add(self, name: str, rows: list[dict[str, Any]], columns: tuple[str, ...] = ("id", "name")) -> None:
        self.next_id += 1
        self.tables[name] = {
            "columns": list(columns),
            "rows": rows,
            "foreign_keys": [],
            "ddl": f"CREATE TABLE `{name}` ({', '.join(columns)})",
            "updated": "2026-01-01T00:00:00",
            "created": "2025-01-01T00:00:00",
            "auto": 10,
            "id": self.next_id,
        }

    def write(self, name: str) -> None:
        """What MariaDB's catalog shows after a write to the table."""
        self.tables[name]["updated"] = f"2026-02-0{len(self.tables[name]['rows'])}T00:00:00"

    def restart(self) -> None:
        """InnoDB keeps UPDATE_TIME in memory: a restart empties it for every table."""
        for t in self.tables.values():
            t["updated"] = None

    def _columns(self, name: str) -> list[ColumnInfo]:
        return [
            ColumnInfo(name=c, data_type="int" if c == "id" else "varchar", nullable=c != "id",
                       is_primary_key=c == "id", is_unique=c == "id")
            for c in self.tables[name]["columns"]
        ]

    async def read_catalog(self, database: str) -> dict[str, CatalogTable]:
        return {
            name: CatalogTable(
                columns=self._columns(name), primary_keys=["id"],
                foreign_keys=[ForeignKeyInfo.model_validate(fk) for fk in t["foreign_keys"]],
            )
            for name, t in self.tables.items()
        }

    async def read_table_ids(self, database: str) -> dict[str, int]:
        return {name: t["id"] for name, t in self.tables.items()}

    async def get_table_stats(self, databases: Any = None) -> MariaDBResponse:  # noqa: ANN401
        return _ok([
            {"database_name": DB, "table_name": n, "n_live_tup": len(t["rows"]), "last_updated": t["updated"],
             "create_time": t["created"], "auto_increment": t["auto"]}
            for n, t in sorted(self.tables.items())
        ])

    async def get_table_info(self, table: str, database: str | None = None) -> MariaDBResponse:
        if table not in self.tables:
            return MariaDBResponse(success=False, error="Table not found")
        return _ok({"name": table, "columns": [c.model_dump() for c in self._columns(table)]})

    async def get_foreign_keys(self, table: str, database: str | None = None) -> MariaDBResponse:
        return _ok(list(self.tables[table]["foreign_keys"]))

    async def get_primary_keys(self, table: str, database: str | None = None) -> MariaDBResponse:
        return _ok([{"column_name": "id"}])

    async def fetch_table_rows(self, database: str, table: str, limit: int | None = None, order_by: Any = None,  # noqa: ANN401
                               **_kw: Any) -> list[dict[str, Any]]:  # noqa: ANN401
        self.row_orders[table] = order_by
        if table in self.broken:
            raise RuntimeError("SELECT command denied")
        return [dict(r) for r in self.tables[table]["rows"][:limit]]

    async def get_table_ddl(self, table: str, database: str | None = None) -> MariaDBResponse:
        return _ok({"ddl": self.tables[table]["ddl"]})


class SyncPoint:
    def __init__(self) -> None:
        self.data: dict[str, dict] = {}

    async def read_sync_point(self, key: str) -> dict:
        return self.data.get(key, {})

    async def update_sync_point(self, key: str, value: dict) -> None:
        self.data[key] = dict(value)


class Harness:
    def __init__(self) -> None:
        self.stored: dict[str, Record] = {}
        self.published: list[tuple[str, str]] = []
        self.source = FakeMariaDB()

        tx_store = AsyncMock()
        tx_store.get_record_by_external_id = AsyncMock(
            side_effect=lambda connector_id, external_id, **_: copy.deepcopy(self.stored.get(external_id))
        )
        tx_store.get_record_by_key = AsyncMock(return_value=None)
        tx_store.get_record_group_by_external_id = AsyncMock(return_value=None)
        tx_store.delete_edges_by_relationship_types = AsyncMock(return_value=0)
        tx_store.get_records_by_parent = AsyncMock(
            side_effect=lambda connector_id, parent, record_type=None, **_: [
                copy.deepcopy(r) for r in self.stored.values() if r.parent_external_record_id == parent
            ]
        )

        async def _upsert(records: list[Record]) -> None:
            for r in records:
                self.stored[r.external_record_id] = copy.deepcopy(r)

        tx_store.batch_upsert_records = AsyncMock(side_effect=_upsert)

        @asynccontextmanager
        async def _transaction(*, explicit: Any = None):  # noqa: ANN202, ANN401
            yield tx_store

        store_provider = MagicMock()
        store_provider.transaction = _transaction
        store_provider.graph_provider.get_typed_records_batch = AsyncMock(
            side_effect=lambda ids, **_: {r.id: copy.deepcopy(r) for r in self.stored.values() if r.id in ids}
        )
        store_provider.compare_and_set_indexing_status = AsyncMock(
            side_effect=lambda ids, expected, new_status: list(ids)
        )

        processor = DataSourceEntitiesProcessor(
            logging.getLogger("test.mariadb.unchanged"), store_provider, AsyncMock()
        )
        processor.org_id = "org-1"
        processor.messaging_producer = AsyncMock()

        async def _send_message(topic: str, message: dict, key: Any = None) -> bool:  # noqa: ANN401
            self._publish(message)
            return True

        async def _send_messages(topic: str, messages: list) -> list[bool]:
            for _key, message in messages:
                self._publish(message)
            return [True] * len(messages)

        processor.messaging_producer.send_message = AsyncMock(side_effect=_send_message)
        processor.messaging_producer.send_messages = AsyncMock(side_effect=_send_messages)
        processor.on_sql_records_deleted = AsyncMock(side_effect=self._delete)

        connector = MariaDBConnector(
            logger=logging.getLogger("test.mariadb.unchanged"),
            data_entities_processor=processor,
            data_store_provider=store_provider,
            config_service=MagicMock(),
            connector_id="conn-1",
        )
        connector.data_source = self.source
        connector.database_name = DB
        connector._create_app_users = AsyncMock()
        connector._ensure_database_record_groups = AsyncMock()
        connector.tables_sync_point = SyncPoint()
        self.connector = connector
        self.processor = processor

    def _publish(self, message: dict) -> None:
        # Column records are internal and never indexed; only tables are asserted on.
        record_id = message["payload"]["recordId"]
        if any(r.id == record_id and r.record_type == RecordType.SQL_TABLE for r in self.stored.values()):
            self.published.append((message["eventType"], record_id))

    async def _delete(self, record_ids: list[str], connector_id: str, **_kw: Any) -> list[str]:  # noqa: ANN401
        removed = []
        # A table takes its columns with it.
        tables = {r.external_record_id for r in self.stored.values() if r.id in record_ids}
        for fqn, r in list(self.stored.items()):
            if r.id in record_ids or r.parent_external_record_id in tables:
                del self.stored[fqn]
                removed.append(r.id)
        return removed

    def index_everything(self) -> None:
        for r in self.stored.values():
            if r.record_type == RecordType.SQL_TABLE:
                self.finish_indexing(r)

    @staticmethod
    def finish_indexing(r: Record) -> None:
        """Leave a stored record as a finished indexing run does."""
        r.indexing_status = ProgressStatus.COMPLETED.value
        r.parsing_status = ProgressStatus.COMPLETED.value
        r.extraction_status = ProgressStatus.COMPLETED.value
        r.md5_hash = f"md5-{r.external_record_id}"
        r.size_in_bytes = 2048
        r.storage_document_id = f"doc-{r.external_record_id}"

    def while_fingerprinting(self, table: str, change: Any) -> None:  # noqa: ANN401
        """Apply ``change`` to the stored record after the connector has read it, before the save."""
        fingerprint = self.connector._table_fingerprint

        async def _fingerprint(t: Any, max_rows: int) -> str | None:  # noqa: ANN401
            result = await fingerprint(t, max_rows)
            if t.name == table:
                change(self.record(table))
            return result

        self.connector._table_fingerprint = _fingerprint

    def record(self, table: str) -> Record:
        return self.stored[f"{DB}.{table}"]

    async def sync(self) -> list[tuple[str, str]]:
        self.published.clear()
        with patch(
            "app.connectors.sources.mariadb.connector.load_connector_filters",
            new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection()),
        ):
            await self.connector.run_sync()
        return list(self.published)


async def _indexed_harness() -> Harness:
    h = Harness()
    h.source.add("orders", [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])
    h.source.add("customers", [{"id": 1, "name": "ann"}])
    first = await h.sync()
    assert sorted(first) == sorted(
        [("newRecord", h.record("orders").id), ("newRecord", h.record("customers").id)]
    )
    h.index_everything()
    return h


class TestUnchangedTablesAreNotQueuedAgain:

    @pytest.mark.asyncio
    async def test_unchanged_tables_are_not_queued_again(self) -> None:
        h = await _indexed_harness()
        assert await h.sync() == []
        assert h.record("orders").indexing_status == ProgressStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_a_restart_alone_queues_nothing(self) -> None:
        # Every table's catalog markers move, and none of their rows did.
        h = await _indexed_harness()
        h.source.restart()
        assert await h.sync() == []
        assert h.record("orders").indexing_status == ProgressStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_unchanged_table_keeps_what_indexing_stored(self) -> None:
        h = await _indexed_harness()
        before = h.record("orders")
        h.source.restart()
        assert await h.sync() == []
        after = h.record("orders")
        assert after.parsing_status == ProgressStatus.COMPLETED.value
        assert after.extraction_status == ProgressStatus.COMPLETED.value
        assert after.md5_hash == before.md5_hash
        assert after.size_in_bytes == before.size_in_bytes
        assert after.storage_document_id == before.storage_document_id

    @pytest.mark.asyncio
    async def test_resync_keeps_the_stored_record_id_and_version(self) -> None:
        h = await _indexed_harness()
        before = h.record("orders")
        h.source.restart()
        await h.sync()
        after = h.record("orders")
        assert after.id == before.id
        assert after.version == before.version


class TestChangedTablesAreQueued:

    @pytest.mark.asyncio
    async def test_changed_rows_are_queued_again(self) -> None:
        h = await _indexed_harness()
        h.source.tables["orders"]["rows"][0]["name"] = "changed"
        h.source.write("orders")
        assert await h.sync() == [("updateRecord", h.record("orders").id)]

    @pytest.mark.asyncio
    async def test_insert_past_the_row_limit_is_not_queued(self) -> None:
        h = await _indexed_harness()
        h.connector._max_rows = lambda sync_filters=None: 2
        h.source.tables["orders"]["ddl"] = (
            "CREATE TABLE `orders` (`id` int NOT NULL AUTO_INCREMENT) ENGINE=InnoDB AUTO_INCREMENT=3"
        )
        h.source.write("orders")
        await h.sync()
        h.index_everything()
        h.source.tables["orders"]["rows"].append({"id": 3, "name": "c"})
        h.source.tables["orders"]["ddl"] = (
            "CREATE TABLE `orders` (`id` int NOT NULL AUTO_INCREMENT) ENGINE=InnoDB AUTO_INCREMENT=4"
        )
        h.source.tables["orders"]["auto"] = 11
        h.source.write("orders")
        assert await h.sync() == []

    @pytest.mark.asyncio
    async def test_schema_change_is_queued_again(self) -> None:
        h = await _indexed_harness()
        h.source.tables["customers"]["columns"].append("email")
        h.source.tables["customers"]["ddl"] = "CREATE TABLE `customers` (id, name, email)"
        assert await h.sync() == [("updateRecord", h.record("customers").id)]

    @pytest.mark.asyncio
    async def test_unreadable_rows_are_queued_again(self) -> None:
        # The catalog marker stands in for the content, so the table is not taken for unchanged.
        h = await _indexed_harness()
        h.source.broken.add("orders")
        h.source.write("orders")
        assert await h.sync() == [("updateRecord", h.record("orders").id)]

    @pytest.mark.asyncio
    async def test_a_changed_table_that_failed_indexing_is_retried(self) -> None:
        h = await _indexed_harness()
        h.record("customers").indexing_status = ProgressStatus.FAILED.value
        h.source.restart()
        assert await h.sync() == [("updateRecord", h.record("customers").id)]


class TestIndexingDuringTheSync:

    @pytest.mark.asyncio
    async def test_indexing_that_finishes_during_the_sync_is_kept(self) -> None:
        h = Harness()
        h.source.add("orders", [{"id": 1, "name": "a"}])
        await h.sync()
        h.record("orders").indexing_status = ProgressStatus.QUEUED.value
        h.while_fingerprinting("orders", h.finish_indexing)
        h.source.restart()

        await h.sync()
        after = h.record("orders")
        assert after.indexing_status == ProgressStatus.COMPLETED.value
        assert after.md5_hash == "md5-shop.orders"
        assert after.storage_document_id == "doc-shop.orders"


class TestDroppedTables:

    @pytest.mark.asyncio
    async def test_dropped_table_is_still_deleted(self) -> None:
        h = await _indexed_harness()
        dropped_id = h.record("customers").id
        del h.source.tables["customers"]
        assert await h.sync() == []
        assert dropped_id in h.processor.on_sql_records_deleted.await_args.args[0]
        assert f"{DB}.customers" not in h.stored
        assert not any(r.parent_external_record_id == f"{DB}.customers" for r in h.stored.values())


class TestTableFingerprint:

    @staticmethod
    async def _fingerprint(h: Harness, name: str = "orders", max_rows: int = 1000) -> str | None:
        tables, _ = await h.connector._load_tables(await h.connector._listed_tables())
        table = next(t for t in tables if t.name == name)
        return await h.connector._table_fingerprint(table, max_rows)

    @pytest.mark.asyncio
    async def test_foreign_key_order_does_not_change_it(self) -> None:
        h = Harness()
        h.source.add("orders", [{"id": 1, "name": "a"}])
        fks = [
            {"constraint_name": "fk_a", "column_name": "a", "foreign_database": DB,
             "foreign_table_name": "x", "foreign_column_name": "id"},
            {"constraint_name": "fk_b", "column_name": "b", "foreign_database": DB,
             "foreign_table_name": "y", "foreign_column_name": "id"},
        ]
        h.source.tables["orders"]["foreign_keys"] = fks
        first = await self._fingerprint(h)
        h.source.tables["orders"]["foreign_keys"] = list(reversed(fks))
        assert await self._fingerprint(h) == first

    @pytest.mark.asyncio
    async def test_row_limit_change_changes_it(self) -> None:
        h = Harness()
        h.source.add("orders", [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])
        assert await self._fingerprint(h, max_rows=1) != await self._fingerprint(h, max_rows=1000)

    @pytest.mark.asyncio
    async def test_the_auto_increment_counter_does_not_change_it(self) -> None:
        h = Harness()
        h.source.add("orders", [{"id": 1, "name": "a"}])
        h.source.tables["orders"]["ddl"] = "CREATE TABLE `orders` (`id` int) ENGINE=InnoDB AUTO_INCREMENT=2"
        first = await self._fingerprint(h)
        h.source.tables["orders"]["ddl"] = "CREATE TABLE `orders` (`id` int) ENGINE=InnoDB AUTO_INCREMENT=9"
        assert await self._fingerprint(h) == first

    @pytest.mark.asyncio
    async def test_rows_are_read_in_primary_key_order(self) -> None:
        h = Harness()
        h.source.add("orders", [{"id": 1, "name": "a"}])
        await self._fingerprint(h)
        assert h.source.row_orders["orders"] == ["id"]

    @pytest.mark.asyncio
    async def test_none_when_a_part_cannot_be_read(self) -> None:
        h = Harness()
        h.source.add("orders", [{"id": 1, "name": "a"}])
        h.source.broken.add("orders")
        assert await self._fingerprint(h) is None
