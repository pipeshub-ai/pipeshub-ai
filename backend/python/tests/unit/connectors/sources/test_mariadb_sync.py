"""MariaDB sync across syncs: stable records, revisions, failed tables, legacy state, renames.

Runs the real connector over an in-memory catalog, record store and sync point.
"""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import ProgressStatus
from app.connectors.core.registry.filters import Filter, FilterCollection
from app.connectors.core.sql.profiling import ColumnProfile
from app.connectors.sources.mariadb.connector import (
    MAX_COLUMN_VALUES_STREAMED,
    SYNC_STATE_KEY,
    MariaDBConnector,
)
from app.connectors.sources.mariadb.tables import split_table_fqn, table_fqn
from app.models.entities import Record, RecordType
from app.sources.client.mariadb.mariadb import MariaDBResponse
from app.sources.external.mariadb.mariadb_ import (
    CatalogTable,
    ColumnInfo,
    ForeignKeyInfo,
)
from app.sources.external.mariadb.profiling import TableProfile


def ok(data) -> MariaDBResponse:
    return MariaDBResponse(success=True, data=data)


def failed(error="denied") -> MariaDBResponse:
    return MariaDBResponse(success=False, error=error)


class Catalog:
    """Tables of one database, as information_schema describes them."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, Any]] = {}
        # A table listed but missing from the catalog read (renamed or dropped in between).
        self.unreadable: set[str] = set()
        self.catalog_fails = False
        # InnoDB ids by table name; readable only with PROCESS.
        self.ids: dict[str, int] = {}
        self.next_id = 100
        self.process = True

    def add(self, name, columns=("id", "name"), *, rows=1, updated="2026-01-01T00:00:00", fks=()) -> None:
        """Create (or alter in place) a table; a column is a name or (name, COLUMN_TYPE)."""
        self.tables[name] = {"columns": list(columns), "rows": rows, "updated": updated, "fks": list(fks),
                             "created": "2025-01-01T00:00:00", "auto": 10}
        if name not in self.ids:
            self.rebuild(name)

    def rebuild(self, name) -> None:
        """A new InnoDB id: a rebuild, TRUNCATE, or DROP plus CREATE."""
        self.next_id += 1
        self.ids[name] = self.next_id

    def rename(self, *pairs) -> None:
        """RENAME TABLE old TO new[, ...]: as InnoDB does, references and generated names follow."""
        for old, new in pairs:  # in order, as RENAME TABLE runs them
            self.tables[new], self.ids[new] = self.tables.pop(old), self.ids.pop(old)
            for t in self.tables.values():
                t["fks"] = [(col, new if target == old else target) for col, target in t["fks"]]

    def drop(self, name) -> None:
        del self.tables[name]
        self.ids.pop(name, None)

    @staticmethod
    def column(spec) -> tuple:
        return spec if isinstance(spec, tuple) else (spec, "int(11)")

    def source(self) -> MagicMock:
        ds = MagicMock()

        async def stats(databases) -> object:
            return ok([
                {"database_name": "shop", "table_name": name, "n_live_tup": t["rows"],
                 "last_updated": t["updated"], "create_time": t["created"], "auto_increment": t["auto"]}
                for name, t in self.tables.items()
            ])

        async def info(name, database) -> object:
            if name in self.unreadable:
                return failed()
            columns = [{"name": c, "data_type": t.split("(")[0], "nullable": False, "is_primary_key": c == "id",
                        "is_unique": c == "id"} for c, t in map(self.column, self.tables[name]["columns"])]
            return ok({"name": name, "columns": columns})

        async def fks(name, database) -> object:
            return ok([{"constraint_name": f"{name}_ibfk_{i}", "column_name": col, "foreign_database": "shop",
                        "foreign_table_name": target, "foreign_column_name": "id"}
                       for i, (col, target) in enumerate(self.tables[name]["fks"], 1)])

        async def pks(name, database) -> object:
            return ok([{"column_name": "id"}])

        async def read_catalog(database) -> dict:
            if self.catalog_fails:
                raise RuntimeError("denied")
            catalog = {}
            for name, t in self.tables.items():
                if name in self.unreadable:
                    continue
                columns = [ColumnInfo(name=c, data_type=ct.split("(")[0], column_type=ct, nullable=c != "id",
                                      is_primary_key=c == "id", is_unique=c == "id")
                           for c, ct in map(self.column, t["columns"])]
                fks = [ForeignKeyInfo(constraint_name=f"{name}_ibfk_{i}", column_name=col, foreign_database="shop",
                                      foreign_table_name=target, foreign_column_name="id")
                       for i, (col, target) in enumerate(t["fks"], 1)]
                catalog[name] = CatalogTable(columns=columns, primary_keys=["id"], foreign_keys=fks)
            return catalog

        async def read_table_ids(database) -> dict:
            if not self.process:
                raise RuntimeError("1227 Access denied; you need (at least one of) the PROCESS privilege(s)")
            return {name: i for name, i in self.ids.items() if name in self.tables}

        ds.read_catalog = AsyncMock(side_effect=read_catalog)
        ds.read_table_ids = AsyncMock(side_effect=read_table_ids)
        ds.get_table_stats = AsyncMock(side_effect=stats)
        ds.get_table_info = AsyncMock(side_effect=info)
        ds.get_foreign_keys = AsyncMock(side_effect=fks)
        ds.get_primary_keys = AsyncMock(side_effect=pks)
        return ds


class Store:
    """Records by external id, as the processor keeps them: a write with an unchanged revision is a no-op."""

    def __init__(self) -> None:
        self.records: dict[str, Any] = {}
        self.writes: list[tuple[str, str]] = []  # (kind, external id)
        self.deleted: list[str] = []
        self.fail_delete: set[str] = set()
        self.moves: list[list[str]] = []
        self.fail_moves = False
        self.crash_moves = False

    def processor(self) -> MagicMock:
        p = MagicMock()
        p.org_id = "org-1"

        async def get_record(connector_id, external_record_id, raise_on_error=False) -> object:
            return self.records.get(external_record_id)

        async def on_new_records(batch) -> None:
            for record, _ in batch:
                existing = self.records.get(record.external_record_id)
                if existing is not None:
                    record.id = existing.id  # the processor keeps a stored record's id
                kind = "new" if record.record_type == RecordType.SQL_TABLE else "column"
                self.writes.append((kind, record.external_record_id))
                self.records[record.external_record_id] = record

        async def on_update(record) -> None:
            self.writes.append(("update", record.external_record_id))
            self.records[record.external_record_id] = record

        async def by_type(connector_id, record_type) -> list:
            return [r for r in self.records.values() if r.record_type == record_type]

        async def sql_deleted(ids, connector_id, table_record_id=None) -> list:
            removed = []
            for record_id in ids:
                record = next(r for r in self.records.values() if r.id == record_id)
                if record.external_record_id in self.fail_delete:
                    raise RuntimeError("graph refused")
                del self.records[record.external_record_id]
                self.deleted.append(record.external_record_id)
                removed.append(record_id)
                # A table takes its columns with it.
                for child in [r for r in self.records.values()
                              if getattr(r, "parent_external_record_id", None) == record.external_record_id]:
                    del self.records[child.external_record_id]
                    self.deleted.append(child.external_record_id)
                    removed.append(child.id)
            return removed

        p.get_record_by_external_id = get_record
        p.on_new_records = on_new_records
        p.on_record_content_update = on_update
        p.get_records_by_record_type = by_type
        p.on_sql_records_deleted = sql_deleted
        p.on_new_record_groups = AsyncMock()
        p.on_sql_records_renamed = AsyncMock()
        p.get_all_active_users = AsyncMock(return_value=[])
        p.on_new_app_users = AsyncMock()
        return p


class SyncPoint:
    def __init__(self) -> None:
        self.data: dict[str, dict] = {}
        self.saves = 0

    async def read_sync_point(self, key) -> dict:
        return self.data.get(key, {})

    async def update_sync_point(self, key, value) -> None:
        self.saves += 1
        self.data[key] = dict(value)


class World:
    def __init__(self, *, scope="team", created_by=None) -> None:
        self.catalog = Catalog()
        self.store = Store()
        self.point = SyncPoint()
        self.connector = MariaDBConnector(
            logger=MagicMock(), data_entities_processor=self.store.processor(), data_store_provider=MagicMock(),
            config_service=MagicMock(), connector_id="c1", scope=scope, created_by=created_by,
        )
        self.connector.database_name = "shop"
        store = self.store

        class Tx:
            async def __aenter__(self) -> "Tx":
                return self

            async def __aexit__(self, *exc) -> bool:
                return False

            async def get_records_by_parent(self, connector_id, parent, record_type=None) -> list:
                return [r for r in store.records.values() if getattr(r, "parent_external_record_id", None) == parent]

            async def get_record_by_external_id(self, connector_id, external_id, raise_on_error=False) -> object:
                return store.records.get(external_id)

            async def batch_update_nodes(self, updates, collection) -> None:
                if store.crash_moves:
                    raise Crash()
                if store.fail_moves:
                    raise RuntimeError("graph refused the move")
                by_id = {r.id: r for r in store.records.values()}
                moved = [(by_id[u["id"]], u) for u in updates]
                for record, _ in moved:
                    del store.records[record.external_record_id]
                for record, update in moved:
                    record.external_record_id = update["externalRecordId"]
                    if "externalParentId" in update:
                        record.parent_external_record_id = update["externalParentId"]
                    store.records[record.external_record_id] = record
                store.moves.append([u["id"] for u in updates])

        provider = MagicMock()
        provider.transaction = MagicMock(side_effect=lambda: Tx())
        self.connector.data_store_provider = provider
        self.connector.data_entities_processor.get_typed_records_by_ids = AsyncMock(
            side_effect=lambda ids, **_: {r.id: r for r in store.records.values() if r.id in ids}
        )
        self.profiler = MagicMock()
        self.profiler.profile_table = AsyncMock(side_effect=self._profile)
        self.connector.profiler = self.profiler
        self.profile_fails: set[str] = set()
        self.profile_error: BaseException = TimeoutError("stalled")

        self.connector.tables_sync_point = self.point
        self.connector._create_app_users = AsyncMock()
        # (record id a relation points at, table record id it comes from)
        self.related_through: list[tuple[str, str]] = []
        self.refreshed = AsyncMock()
        self.relations_moved = AsyncMock()
        processor = self.connector.data_entities_processor
        processor.sql_tables_to_relate_again = AsyncMock(side_effect=self._related_through)
        processor.on_sql_table_renamed = self.refreshed
        processor.move_incoming_relations = self.relations_moved

    async def _profile(self, database, table, columns, **kwargs) -> TableProfile:
        if table in self.profile_fails:
            raise self.profile_error
        return TableProfile(row_count=3, stats={
            c.name: {"name": c.name, "ordinal_position": i, "distinct_count": 3, "null_count": 0,
                     "cardinality_ratio": 1.0}
            for i, c in enumerate(columns)
        }, profiles={c.name: ColumnProfile(profiled_at=1, top_values=["a"], top_value_frequencies=[0.5])
                     if kwargs.get("with_values") else ColumnProfile(profiled_at=1) for c in columns})

    async def sync(self, filters=None, indexing=None) -> None:
        self.connector.data_source = self.catalog.source()
        with patch(
            "app.connectors.sources.mariadb.connector.load_connector_filters",
            new_callable=AsyncMock, return_value=(filters or FilterCollection(), indexing or FilterCollection()),
        ), patch("app.connectors.sources.mariadb.connector.asyncio.sleep", new_callable=AsyncMock):
            await self.connector.run_sync()

    async def _related_through(self, table_ids, *, column_ids=None) -> set:
        return {source for target, source in self.related_through if target in (column_ids or table_ids or [])}

    @property
    def outcome(self) -> object:
        return self.connector.sync_outcome

    def record(self, name: str) -> Record | None:
        return self.store.records.get(table_fqn("shop", name))

    def state(self) -> dict:
        return json.loads(self.point.data[SYNC_STATE_KEY]["table_states"])


@pytest.fixture
def world() -> World:
    return World()


class TestRecords:
    async def test_a_first_sync_writes_complete_records(self, world) -> None:
        world.catalog.add("orders", fks=[("customer_id", "customers")], rows=42)
        world.catalog.add("customers")
        await world.sync()
        orders = world.record("orders")
        assert (orders.fqn, orders.database_name, orders.row_count, orders.version) == ("shop.orders", "shop", 42, 1)
        assert orders.related_external_records[0].external_record_id == "shop.customers"
        assert world.point.data[SYNC_STATE_KEY]["state_version"] == 3
        # Column records come with the table, with the shared id contract and COLUMN_TYPE.
        id_column = world.store.records["shop.orders.id"]
        assert id_column.record_type == RecordType.SQL_COLUMN and id_column.parent_external_record_id == "shop.orders"
        assert (id_column.data_type, id_column.is_primary_key, id_column.is_unique) == ("int(11)", True, True)
        assert world.store.records["shop.orders.name"].nullable

    async def test_a_resync_keeps_ids_links_and_created_time(self, world) -> None:
        world.connector._frontend_url = "https://app"
        world.catalog.add("orders")
        await world.sync()
        first = world.record("orders")
        world.point.data.clear()  # an explicit full sync
        await world.sync()
        again = world.record("orders")
        assert again.id == first.id and again.weburl == f"https://app/record/{first.id}"
        assert again.source_created_at == first.source_created_at and again.version == 0

    async def test_names_with_dots_get_quote_aware_ids(self, world) -> None:
        world.catalog.add("a.b", fks=[("ref_id", "c.d")])
        world.catalog.add("c.d")
        await world.sync()
        record = world.record("a.b")
        assert record.external_record_id == 'shop."a.b"'
        assert split_table_fqn(record.external_record_id) == ("shop", "a.b")
        assert record.related_external_records[0].external_record_id == 'shop."c.d"'


class TestChanges:
    async def test_an_unchanged_table_is_not_written_again(self, world) -> None:
        world.catalog.add("orders")
        await world.sync()
        world.store.writes.clear()
        await world.sync()
        assert world.store.writes == []

    async def test_a_row_estimate_change_alone_is_no_change(self, world) -> None:
        world.catalog.add("orders", rows=10)
        await world.sync()
        world.store.writes.clear()
        world.catalog.tables["orders"]["rows"] = 11
        await world.sync()
        assert world.store.writes == []

    async def test_new_data_or_new_columns_update_the_same_record(self, world) -> None:
        world.catalog.add("orders")
        await world.sync()
        first_id = world.record("orders").id
        world.catalog.tables["orders"]["updated"] = "2026-02-01T00:00:00"
        await world.sync()
        world.catalog.add("orders", columns=("id", "name", "total"), updated="2026-02-01T00:00:00")
        await world.sync()
        updates = [w for w in world.store.writes if w[0] == "update"]
        assert updates[-2:] == [("update", "shop.orders"), ("update", "shop.orders")]
        assert world.record("orders").id == first_id and world.record("orders").version == 0

    async def test_unreadable_rows_leave_the_catalog_marker_with_create_time_after_a_restart(self, world) -> None:
        world.catalog.add("orders", updated=None)
        await world.sync()
        revision = world.record("orders").external_revision_id
        assert revision.endswith(":2025-01-01T00:00:00|10:1000")

    async def test_a_new_table_is_written_and_a_dropped_one_removed(self, world) -> None:
        world.catalog.add("orders")
        world.catalog.add("old")
        await world.sync()
        del world.catalog.tables["old"]
        world.catalog.add("new")
        await world.sync()
        assert world.record("new") is not None and world.record("old") is None


class TestFailures:
    async def test_an_unread_catalog_fails_the_sync_and_changes_nothing(self, world) -> None:
        # An unread catalog could make a renamed table's old name look gone.
        world.catalog.add("orders")
        world.catalog.add("old")
        await world.sync()
        saved = dict(world.point.data[SYNC_STATE_KEY])
        before = world.record("orders")
        world.catalog.drop("old")
        world.catalog.tables["orders"]["updated"] = "2026-03-01T00:00:00"
        world.catalog.catalog_fails = True
        with pytest.raises(ConnectionError):
            await world.sync()
        assert world.record("orders") is before and world.record("old") is not None
        assert world.point.data[SYNC_STATE_KEY] == saved
        world.catalog.catalog_fails = False
        world.store.writes.clear()
        await world.sync()
        assert ("update", "shop.orders") in world.store.writes and world.record("old") is None

    async def test_without_a_state_an_unread_catalog_removes_nothing(self, world) -> None:
        world.catalog.add("orders")
        await world.sync()
        world.point.data.clear()  # an explicit full sync
        world.catalog.catalog_fails = True
        with pytest.raises(ConnectionError):
            await world.sync()
        assert world.record("orders") is not None and world.store.deleted == []

    async def test_a_failed_delete_is_tried_again(self, world) -> None:
        world.catalog.add("orders")
        world.catalog.add("old")
        await world.sync()
        del world.catalog.tables["old"]
        world.store.fail_delete = {"shop.old"}
        await world.sync()
        assert "shop.old" in world.state()
        world.store.fail_delete = set()
        await world.sync()
        assert world.record("old") is None and "shop.old" not in world.state()


class TestColumns:
    async def test_a_dropped_column_loses_its_record_and_an_added_one_gets_one(self, world) -> None:
        world.catalog.add("orders", columns=("id", "name", "note"))
        await world.sync()
        name_id = world.store.records["shop.orders.name"].id
        world.catalog.add("orders", columns=("id", "name", "total"), updated="2026-02-01T00:00:00")
        await world.sync()
        assert "shop.orders.note" not in world.store.records and "shop.orders.total" in world.store.records
        assert world.store.records["shop.orders.name"].id == name_id

    async def test_a_removed_table_takes_its_column_records_with_it(self, world) -> None:
        world.catalog.add("orders")
        world.catalog.add("old")
        await world.sync()
        del world.catalog.tables["old"]
        await world.sync()
        assert not [k for k in world.store.records if k.startswith("shop.old")]

    async def test_a_table_the_catalog_read_does_not_show_is_left_for_the_next_sync(self, world) -> None:
        # Listed, then renamed or dropped before the catalog read (S4).
        world.catalog.add("orders")
        await world.sync()
        saved = world.state()["shop.orders"]
        columns = {k for k in world.store.records if k.startswith("shop.orders.")}
        world.catalog.add("fresh")
        world.catalog.unreadable = {"orders", "fresh"}
        world.store.writes.clear()
        await world.sync()
        assert world.store.writes == [] and world.store.deleted == []
        assert world.state()["shop.orders"] == saved and "shop.fresh" not in world.state()
        assert {k for k in world.store.records if k.startswith("shop.orders.")} == columns
        world.catalog.unreadable = set()
        await world.sync()
        assert world.record("fresh") is not None

    async def test_a_state_from_phase_6_brings_every_table_its_columns(self, world) -> None:
        world.catalog.add("orders")
        world.point.data[SYNC_STATE_KEY] = {"state_version": 2, "table_states": "{}"}
        await world.sync()
        assert "shop.orders.id" in world.store.records

    async def test_a_max_rows_change_rewrites_the_table(self, world) -> None:
        from app.connectors.core.registry.filters import Filter

        world.catalog.add("orders")
        await world.sync()
        world.store.writes.clear()
        filters = FilterCollection(filters=[Filter(key="max_rows_per_table", value=50, type="number",
                                                   operator="less_than_or_equal")])
        world.connector.data_source = world.catalog.source()
        with patch(
            "app.connectors.sources.mariadb.connector.load_connector_filters",
            new_callable=AsyncMock, return_value=(filters, FilterCollection()),
        ):
            await world.connector.run_sync()
        assert ("update", "shop.orders") in world.store.writes
        assert world.record("orders").external_revision_id.endswith(":50")


class TestFilters:
    async def test_a_table_filter_saved_with_an_old_plain_id_still_matches(self, world) -> None:
        from app.connectors.core.registry.filters import Filter

        world.catalog.add("a.b")
        world.catalog.add("other")
        filters = FilterCollection(filters=[Filter(key="tables", value=["shop.a.b"], type="list", operator="in")])
        world.connector.data_source = world.catalog.source()
        with patch(
            "app.connectors.sources.mariadb.connector.load_connector_filters",
            new_callable=AsyncMock, return_value=(filters, FilterCollection()),
        ):
            await world.connector.run_sync()
        assert world.record("a.b") is not None and world.record("other") is None


class TestFullSync:
    async def test_an_unchanged_table_is_written_again_without_being_queued(self, world) -> None:
        # A full sync unlinks the records it did not write, which takes them out of search.
        world.catalog.add("orders")
        await world.sync()
        world.record("orders").indexing_status = ProgressStatus.COMPLETED.value
        del world.point.data[SYNC_STATE_KEY]  # what a full sync starts from
        world.store.writes.clear()
        await world.sync()
        assert ("new", "shop.orders") in world.store.writes
        assert ("update", "shop.orders") not in world.store.writes


class TestUpgrade:
    async def test_a_state_from_before_is_replaced_by_a_full_sync(self, world) -> None:
        world.catalog.add("orders")
        world.point.data[SYNC_STATE_KEY] = {"table_states": json.dumps({"shop.orders": {"column_hash": "x"}})}
        await world.sync()
        state = world.state()["shop.orders"]
        assert set(world.state()) == {"shop.orders"} and "column_hash" not in state
        assert (state["revision"], state["profiled"]) == (world.record("orders").external_revision_id, True)

    async def test_a_legacy_record_whose_id_no_longer_parses_is_removed(self, world) -> None:
        world.catalog.add("a.b")
        legacy = SimpleNamespace(id="r-legacy", external_record_id="shop.a.b", record_type=RecordType.SQL_TABLE)
        world.store.records["shop.a.b"] = legacy
        await world.sync()
        assert "shop.a.b" in world.store.deleted and world.record("a.b") is not None

    async def test_the_no_state_path_saves_once(self, world) -> None:
        world.catalog.add("orders")
        world.connector.data_source = world.catalog.source()
        with patch(
            "app.connectors.sources.mariadb.connector.load_connector_filters",
            new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection()),
        ):
            await world.connector.run_incremental_sync()
        assert world.point.saves == 1


class TestSharing:
    def test_a_personal_connector_keeps_its_owner(self) -> None:
        world = World(scope="personal", created_by="user-9")
        assert (world.connector.connector_scope, world.connector.created_by) == ("personal", "user-9")

    def test_both_scopes_are_offered(self) -> None:
        assert set(MariaDBConnector._connector_metadata["connectorScopes"]) >= {"personal", "team"}


class TestColumnStreaming:
    async def test_a_column_streams_its_definition_profile_and_value_counts(self, world) -> None:
        world.catalog.add("orders", columns=("id", "status"))
        await world.sync()
        column = world.store.records["shop.orders.status"]
        world.connector.data_source.column_value_counts = AsyncMock(return_value=[{"value": "paid", "count": 3}])
        response = await world.connector.stream_record(column)
        body = b"".join([chunk async for chunk in response.body_iterator])
        data = json.loads(body)
        assert (data["column_name"], data["table_name"], data["database_name"]) == ("status", "orders", "shop")
        assert data["value_counts"] == [{"value": "paid", "count": 3}]
        assert world.connector.data_source.column_value_counts.await_args.args[:3] == ("shop", "orders", "status")

    @staticmethod
    async def _stream(world, column: str) -> dict:
        response = await world.connector.stream_record(world.store.records[f"shop.orders.{column}"])
        return json.loads(b"".join([chunk async for chunk in response.body_iterator]))

    @pytest.mark.parametrize(("rows", "truncated"), [
        (MAX_COLUMN_VALUES_STREAMED, False),
        (MAX_COLUMN_VALUES_STREAMED + 1, True),
    ])
    async def test_truncated_only_when_a_value_was_cut_off(self, world, rows: int, truncated: bool) -> None:
        world.catalog.add("orders", columns=("id", "status"))
        await world.sync()
        world.connector.data_source.column_value_counts = AsyncMock(
            return_value=[{"value": str(i), "count": 1} for i in range(rows)],
        )
        data = await self._stream(world, "status")
        assert len(data["value_counts"]) == MAX_COLUMN_VALUES_STREAMED
        assert data["value_counts_truncated"] is truncated
        assert world.connector.data_source.column_value_counts.await_args.args[3] == MAX_COLUMN_VALUES_STREAMED + 1

    async def test_a_personal_column_streams_no_value_counts(self, world) -> None:
        world.catalog.add("orders", columns=("id", "email"))
        await world.sync()
        world.connector.data_source.column_value_counts = AsyncMock()
        data = await self._stream(world, "email")
        world.connector.data_source.column_value_counts.assert_not_awaited()
        assert (data["value_counts"], data["value_counts_withheld"]) == ([], True)

    async def test_a_failed_value_read_is_an_error_not_an_empty_column(self, world) -> None:
        world.catalog.add("orders", columns=("id", "status"))
        await world.sync()
        world.connector.data_source.column_value_counts = AsyncMock(side_effect=RuntimeError("1969"))
        with pytest.raises(HTTPException):
            await world.connector.stream_record(world.store.records["shop.orders.status"])


class TestProfiles:
    async def test_column_records_carry_the_profile(self, world) -> None:
        world.catalog.add("orders", columns=("id", "status"))
        await world.sync()
        status = world.store.records["shop.orders.status"]
        assert (status.distinct_count, status.null_count, status.top_values) == (3, 0, ["a"])
        kwargs = world.profiler.profile_table.await_args.kwargs
        assert kwargs["with_values"] is True and kwargs["row_estimate"] == 1

    async def test_a_failed_profile_keeps_stored_stats_and_is_retried_without_a_reindex(self, world) -> None:
        world.catalog.add("orders", columns=("id", "status"))
        await world.sync()
        world.catalog.tables["orders"]["updated"] = "2026-02-01T00:00:00"
        world.profile_fails = {"orders"}
        await world.sync()
        assert world.store.records["shop.orders.status"].distinct_count == 3  # kept
        assert world.state()["shop.orders"]["profiled"] is False
        world.profile_fails = set()
        world.store.writes.clear()
        await world.sync()
        assert ("update", "shop.orders") not in world.store.writes  # no re-index
        assert ("column", "shop.orders.status") in world.store.writes
        assert world.state()["shop.orders"]["profiled"] is True

    async def test_switching_indexing_off_strips_values_and_on_brings_them_back(self, world) -> None:
        from app.connectors.core.registry.filters import Filter

        world.catalog.add("orders", columns=("id", "status"))
        await world.sync()
        assert world.store.records["shop.orders.status"].top_values == ["a"]
        off = FilterCollection(filters=[Filter(key="index_stage_tables", value=False, type="boolean", operator="is")])

        async def sync_with(indexing) -> None:
            world.connector.data_source = world.catalog.source()
            with patch(
                "app.connectors.sources.mariadb.connector.load_connector_filters",
                new_callable=AsyncMock, return_value=(FilterCollection(), indexing),
            ):
                await world.connector.run_sync()

        world.store.writes.clear()
        await sync_with(off)
        assert not world.store.records["shop.orders.status"].top_values
        assert ("update", "shop.orders") not in world.store.writes
        await sync_with(FilterCollection())
        assert world.store.records["shop.orders.status"].top_values == ["a"]

    async def test_the_budget_stops_profiling_after_three_unavailable_in_a_row(self, world) -> None:
        for name in ("a", "b", "c", "d"):
            world.catalog.add(name)
        world.profile_fails = {"a", "b", "c", "d"}
        await world.sync()
        assert world.profiler.profile_table.await_count == 3


class StatementTimeout(Exception):
    errno = 1969


class TestProfileReview:
    async def test_statement_timeouts_on_big_tables_do_not_stop_the_rest(self, world) -> None:
        for name in ("a", "b", "c", "d"):
            world.catalog.add(name)
        world.profile_error = StatementTimeout("max_statement_time exceeded")
        world.profile_fails = {"a", "b", "c"}
        await world.sync()
        assert world.state()["shop.d"]["profiled"] is True

    async def test_a_table_that_never_profiles_stops_being_rescanned(self, world) -> None:
        world.catalog.add("orders")
        world.profile_error = StatementTimeout("max_statement_time exceeded")
        world.profile_fails = {"orders"}
        for _ in range(5):
            await world.sync()
        assert world.profiler.profile_table.await_count == 3  # MAX_PROFILE_ATTEMPTS
        assert world.state()["shop.orders"]["profiled"] is True

    async def test_values_switched_while_the_catalog_fails_are_stripped_later(self, world) -> None:
        from app.connectors.core.registry.filters import Filter

        world.catalog.add("orders", columns=("id", "status"))
        await world.sync()
        off = FilterCollection(filters=[Filter(key="index_stage_tables", value=False, type="boolean", operator="is")])

        async def sync_with(indexing) -> None:
            world.connector.data_source = world.catalog.source()
            with patch(
                "app.connectors.sources.mariadb.connector.load_connector_filters",
                new_callable=AsyncMock, return_value=(FilterCollection(), indexing),
            ):
                await world.connector.run_sync()

        world.catalog.catalog_fails = True
        with pytest.raises(ConnectionError):
            await sync_with(off)
        world.catalog.catalog_fails = False
        await sync_with(off)
        assert not world.store.records["shop.orders.status"].top_values

    async def test_stored_stats_that_cannot_be_read_hold_the_table_until_its_profile_is_taken(self, world) -> None:
        world.catalog.add("orders", columns=("id", "status"))
        await world.sync()
        processor = world.connector.data_entities_processor
        read = processor.get_typed_records_by_ids
        processor.get_typed_records_by_ids = AsyncMock(return_value={})
        world.profile_fails = {"orders"}  # stored stats are wanted
        world.catalog.add("orders", columns=("id", "status", "total"), updated="2026-02-01T00:00:00")
        await world.sync()
        # Not written without its stored stats: the next sync tries again.
        assert "shop.orders.total" not in world.store.records

        processor.get_typed_records_by_ids = read
        world.profile_fails = set()
        await world.sync()
        assert "shop.orders.total" in world.store.records

    async def test_columns_are_written_before_the_table_so_a_column_failure_does_not_republish_it(self, world) -> None:
        world.catalog.add("orders", columns=("id", "status", "note"))
        await world.sync()
        processor = world.connector.data_entities_processor
        processor.on_sql_records_deleted = AsyncMock(side_effect=RuntimeError("graph refused"))
        world.catalog.add("orders", columns=("id", "status"), updated="2026-02-01T00:00:00")
        world.store.writes.clear()
        for _ in range(3):
            await world.sync()
        assert [w for w in world.store.writes if w[0] == "update"] == []

    async def test_a_behind_table_whose_record_vanished_is_written_again(self, world) -> None:
        world.catalog.add("orders")
        world.profile_fails = {"orders"}
        await world.sync()
        world.profile_fails = set()
        del world.store.records["shop.orders"]
        await world.sync()
        assert "shop.orders" not in world.state()
        await world.sync()
        assert world.record("orders") is not None


class Crash(BaseException):
    """The process dies: nothing after it runs, no except clause catches it."""


def ids_of(world, prefix) -> dict:
    return {k: r.id for k, r in world.store.records.items() if k == prefix or k.startswith(prefix + ".")}


class TestRenames:
    async def test_a_renamed_table_keeps_its_records_and_is_reported_renamed(self, world) -> None:
        world.catalog.add("orders", columns=("id", ("status", "varchar(20)")))
        await world.sync()
        record_id = world.record("orders").id
        column_id = world.store.records["shop.orders.status"].id
        world.catalog.rename(("orders", "purchases"))
        world.store.writes.clear()
        await world.sync()
        assert world.record("purchases").id == record_id and world.record("orders") is None
        assert world.store.records["shop.purchases.status"].id == column_id
        assert world.store.deleted == []
        assert record_id in world.outcome.renamed and record_id in world.outcome.relations_only
        assert record_id not in world.outcome.restructured
        # The indexed text names the table: it is published again (S5).
        assert ("update", "shop.purchases") in world.store.writes
        assert set(world.state()) == {"shop.purchases"} and world.state()["shop.purchases"]["renamed_from"] == ""
        world.connector.data_entities_processor.on_sql_records_renamed.assert_awaited_once_with([record_id], "c1")
        world.refreshed.assert_awaited()

    async def test_without_process_a_pure_rename_is_followed_by_shape_and_described_again(self, world) -> None:
        world.catalog.process = False
        world.catalog.add("orders", fks=[("customer_id", "orders")])  # a self-reference, generated name
        await world.sync()
        record_id = world.record("orders").id
        world.catalog.rename(("orders", "purchases"))
        await world.sync()
        assert world.record("purchases").id == record_id and world.store.deleted == []
        # Maybe a lookalike: kept, but reported as restructured.
        assert record_id in world.outcome.restructured

    async def test_a_rename_with_an_alter_is_followed_by_id(self, world) -> None:
        world.catalog.add("orders")
        await world.sync()
        record_id = world.record("orders").id
        world.catalog.rename(("orders", "purchases"))
        world.catalog.tables["purchases"]["columns"].append(("note", "text"))
        await world.sync()
        assert world.record("purchases").id == record_id and "shop.purchases.note" in world.store.records
        assert record_id in world.outcome.restructured

    async def test_a_swap_keeps_each_record_with_its_table(self, world) -> None:
        world.catalog.add("a", columns=("id", ("x", "varchar(9)")))
        world.catalog.add("b", columns=("id", ("y", "date")))
        await world.sync()
        a_id, b_id = world.record("a").id, world.record("b").id
        world.catalog.rename(("a", "tmp"), ("b", "a"), ("tmp", "b"))
        await world.sync()
        assert (world.record("b").id, world.record("a").id) == (a_id, b_id)
        assert world.store.records["shop.b.x"].parent_external_record_id == "shop.b"
        assert world.store.deleted == [] and len(world.store.moves) == 1

    async def test_a_renamed_column_keeps_its_record(self, world) -> None:
        world.catalog.add("orders", columns=("id", ("status", "varchar(20)")))
        await world.sync()
        column_id = world.store.records["shop.orders.status"].id
        world.catalog.add("orders", columns=("id", ("state", "varchar(20)")))
        await world.sync()
        assert world.store.records["shop.orders.state"].id == column_id
        assert "shop.orders.status" not in world.store.records
        record_id = world.record("orders").id
        assert record_id in world.outcome.renamed and record_id in world.outcome.relations_only

    async def test_a_failed_lookup_of_a_renamed_table_does_not_stop_the_others(self, world) -> None:
        for name in ("a", "b"):
            world.catalog.add(name, columns=("id", ("status", "varchar(20)")))
        await world.sync()
        for name in ("a", "b"):
            world.catalog.add(name, columns=("id", ("state", "varchar(20)")))
        processor = world.connector.data_entities_processor
        lookup = processor.get_record_by_external_id

        async def failing(connector_id, external_record_id, raise_on_error=False) -> object:
            if external_record_id == "shop.a":
                raise RuntimeError("graph unavailable")
            return await lookup(connector_id, external_record_id, raise_on_error=raise_on_error)

        processor.get_record_by_external_id = failing
        await world.sync()
        assert "shop.a" in world.outcome.restructured_tables
        assert world.record("b").id in world.outcome.renamed

    async def test_a_rebuild_under_the_same_name_is_data_only(self, world) -> None:
        world.catalog.add("orders", columns=("id", ("status", "varchar(20)")))
        await world.sync()
        record_id = world.record("orders").id
        world.catalog.rebuild("orders")  # TRUNCATE or OPTIMIZE
        world.catalog.tables["orders"]["updated"] = "2026-05-01T00:00:00"
        await world.sync()
        assert world.record("orders").id == record_id and not world.outcome.restructured
        assert not world.outcome.renamed

    async def test_a_reload_that_renumbers_every_table_moves_nothing(self, world) -> None:
        for name in ("a", "b", "c"):
            world.catalog.add(name, columns=("id", (f"{name}_label", "varchar(9)")))
        await world.sync()
        ids = {name: world.record(name).id for name in ("a", "b", "c")}
        world.catalog.ids = {"a": world.catalog.ids["b"], "b": world.catalog.ids["c"], "c": 999}
        await world.sync()
        assert {name: world.record(name).id for name in ("a", "b", "c")} == ids
        assert world.store.moves == [] and world.store.deleted == []

    async def test_a_lookalike_created_after_a_drop_is_described_again(self, world) -> None:
        world.catalog.process = False
        world.catalog.add("staging_09")
        await world.sync()
        world.catalog.drop("staging_09")
        world.catalog.add("staging_10")
        await world.sync()
        record_id = world.record("staging_10").id
        assert record_id in world.outcome.restructured and record_id not in world.outcome.relations_only

    async def test_a_referenced_tables_rename_keeps_the_referencing_table_described(self, world) -> None:
        world.catalog.add("customers")
        world.catalog.add("orders", fks=[("customer_id", "customers")])
        await world.sync()
        orders_id = world.record("orders").id
        world.catalog.rename(("customers", "clients"))
        await world.sync()
        assert world.record("orders").related_external_records[0].external_record_id == "shop.clients"
        assert orders_id not in world.outcome.restructured


class TestReplacedAndHeld:
    async def test_a_rename_into_a_dropped_tables_name_removes_that_table_first(self, world) -> None:
        world.catalog.add("old", columns=("id", ("x", "date")))
        world.catalog.add("new_orders")
        await world.sync()
        old_id, moving_id = world.record("old").id, world.record("new_orders").id
        world.catalog.drop("old")
        world.catalog.rename(("new_orders", "old"))
        await world.sync()
        assert world.record("old").id == moving_id and old_id in world.outcome.removed

    async def test_a_rename_that_must_wait_holds_both_names(self, world) -> None:
        world.catalog.add("old", columns=("id", ("x", "date")))
        world.catalog.add("a")
        await world.sync()
        a_id = world.record("a").id
        world.catalog.drop("old")
        world.catalog.rename(("a", "old"))
        world.catalog.add("a", columns=("id", ("other", "text")))  # a new table under the old name
        world.store.fail_delete = {"shop.old"}
        await world.sync()
        # The renamed table's record waits under its old name; the new table does not take it (N2).
        assert world.record("a").id == a_id and world.store.records["shop.a"].record_name == "a"
        assert "shop.a.other" not in world.store.records
        world.store.fail_delete = set()
        await world.sync()
        assert world.record("old").id == a_id and world.record("a").id != a_id
        assert "shop.a.other" in world.store.records


class TestInterruptedMoves:
    async def test_a_move_cut_off_after_the_checkpoint_is_finished_once(self, world) -> None:
        world.catalog.add("orders")
        await world.sync()
        record_id = world.record("orders").id
        world.catalog.rename(("orders", "purchases"))
        world.store.crash_moves = True
        with pytest.raises(Crash):
            await world.sync()
        assert world.state()["shop.purchases"]["moved_record_id"] == record_id
        assert world.record("orders").id == record_id  # not moved yet
        world.store.crash_moves = False
        world.store.writes.clear()
        await world.sync()
        assert world.record("purchases").id == record_id and world.record("orders") is None
        assert world.store.deleted == [] and len(world.store.moves) == 1
        assert ("update", "shop.purchases") in world.store.writes
        assert world.state()["shop.purchases"]["renamed_from"] == ""

    async def test_a_move_cut_off_after_the_graph_write_is_not_done_again(self, world) -> None:
        world.catalog.add("orders")
        await world.sync()
        record_id = world.record("orders").id
        world.catalog.rename(("orders", "purchases"))
        world.connector._follow_renames = AsyncMock(side_effect=Crash())
        with pytest.raises(Crash):
            await world.sync()
        del world.connector._follow_renames
        await world.sync()
        assert world.record("purchases").id == record_id and len(world.store.moves) == 1

    async def test_a_checkpoint_that_cannot_be_saved_moves_nothing(self, world) -> None:
        world.catalog.add("orders")
        await world.sync()
        record_id = world.record("orders").id
        world.catalog.rename(("orders", "purchases"))
        real = world.point.update_sync_point
        world.point.update_sync_point = AsyncMock(side_effect=RuntimeError("kv store down"))
        with pytest.raises(RuntimeError):
            await world.sync()
        assert world.store.moves == [] and world.record("orders").id == record_id
        world.point.update_sync_point = real
        await world.sync()
        assert world.record("purchases").id == record_id

    async def test_a_graph_that_refuses_the_move_is_tried_next_sync(self, world) -> None:
        world.catalog.add("orders")
        await world.sync()
        record_id = world.record("orders").id
        world.catalog.rename(("orders", "purchases"))
        world.store.fail_moves = True
        await world.sync()
        assert world.record("orders").id == record_id and world.record("purchases") is None
        world.store.fail_moves = False
        await world.sync()
        assert world.record("purchases").id == record_id and world.store.deleted == []

    async def test_a_renamed_table_whose_write_fails_is_written_next_sync_without_moving_again(self, world) -> None:
        world.catalog.add("orders")
        await world.sync()
        record_id = world.record("orders").id
        world.catalog.rename(("orders", "purchases"))
        processor = world.connector.data_entities_processor
        real = processor.on_record_content_update
        processor.on_record_content_update = AsyncMock(side_effect=RuntimeError("graph refused"))
        await world.sync()
        assert world.state()["shop.purchases"]["renamed_from"] == "shop.orders"
        processor.on_record_content_update = real
        world.store.writes.clear()
        await world.sync()
        assert len(world.store.moves) == 1 and ("update", "shop.purchases") in world.store.writes
        assert world.state()["shop.purchases"]["renamed_from"] == ""
        assert world.record("purchases").id == record_id

    async def test_a_checkpoint_keeps_the_last_values_setting(self, world) -> None:
        # S1: the values switch must still be seen after a sync cut off past the checkpoint.
        from app.connectors.core.registry.filters import Filter

        world.catalog.add("orders", columns=("id", ("status", "varchar(9)")))
        world.catalog.add("other")
        await world.sync()
        off = FilterCollection(filters=[Filter(key="index_stage_tables", value=False, type="boolean", operator="is")])
        world.catalog.rename(("other", "renamed"))
        world.store.fail_moves = True  # the move fails after the checkpoint is saved
        save, checkpoints = world.connector._save_state, []

        async def save_checkpoints_only(states, *, checkpoint=False) -> bool:
            if not checkpoint:
                raise Crash()
            checkpoints.append(states)
            return await save(states, checkpoint=True)

        world.connector._save_state = save_checkpoints_only
        with pytest.raises(Crash):
            await world.sync(indexing=off)
        del world.connector._save_state
        assert checkpoints and world.point.data[SYNC_STATE_KEY]["table_states"] == json.dumps(
            {fqn: state.model_dump() for fqn, state in checkpoints[-1].items()})
        world.store.fail_moves = False
        assert world.point.data[SYNC_STATE_KEY]["values_allowed"] is True
        await world.sync(indexing=off)
        assert not world.store.records["shop.orders.status"].top_values

    async def test_a_final_save_that_fails_fails_the_sync(self, world) -> None:
        world.catalog.add("orders")
        await world.sync()
        world.catalog.tables["orders"]["updated"] = "2026-05-01T00:00:00"
        world.point.update_sync_point = AsyncMock(side_effect=RuntimeError("kv store down"))
        with pytest.raises(RuntimeError, match="sync state"):
            await world.sync()


class TestRemovalsAndOutcome:
    async def test_a_dropped_table_is_reported_with_the_tables_keyed_into_it(self, world) -> None:
        world.catalog.add("orders")
        world.catalog.add("old")
        await world.sync()
        old_ids = set(ids_of(world, "shop.old").values())
        world.related_through = [(world.record("old").id, "orders-record")]
        world.catalog.drop("old")
        await world.sync()
        assert old_ids <= world.outcome.removed and "orders-record" in world.outcome.relations_only

    async def test_a_dropped_column_is_reported_removed(self, world) -> None:
        world.catalog.add("orders", columns=("id", ("note", "text")))
        await world.sync()
        note_id = world.store.records["shop.orders.note"].id
        world.catalog.add("orders", columns=("id",))
        await world.sync()
        assert note_id in world.outcome.removed

    async def test_an_added_table_is_restructured_and_a_data_change_is_not(self, world) -> None:
        world.catalog.add("orders")
        await world.sync()
        world.catalog.add("customers")
        world.catalog.tables["orders"]["updated"] = "2026-05-01T00:00:00"
        await world.sync()
        assert world.outcome.restructured == {world.record("customers").id}

    async def test_the_outcome_is_reset_every_sync(self, world) -> None:
        world.catalog.add("orders")
        await world.sync()
        world.catalog.add("customers")
        await world.sync()
        assert world.outcome.restructured
        await world.sync()
        assert not world.outcome.restructured and not world.outcome.removed

    async def test_renames_that_cannot_be_announced_are_described_again(self, world) -> None:
        world.catalog.add("orders")
        await world.sync()
        world.connector.data_entities_processor.on_sql_records_renamed = AsyncMock(side_effect=RuntimeError("down"))
        world.catalog.rename(("orders", "purchases"))
        await world.sync()
        assert world.record("purchases").id in world.outcome.restructured

    async def test_a_table_excluded_by_a_filter_is_removed_through_the_diff(self, world) -> None:
        from app.connectors.core.registry.filters import Filter

        world.catalog.add("orders")
        world.catalog.add("audit")
        await world.sync()
        only_orders = FilterCollection(filters=[Filter(key="tables", value=["shop.orders"], type="list", operator="in")])
        await world.sync(filters=only_orders)
        assert world.record("audit") is None and set(world.state()) == {"shop.orders"}

    async def test_without_a_state_unlisted_tables_go_and_stand_ins_stay(self, world) -> None:
        world.catalog.add("orders")
        await world.sync()
        world.store.records["shop.ghost"] = SimpleNamespace(
            id="r-ghost", external_record_id="shop.ghost", record_type=RecordType.SQL_TABLE, is_placeholder=False)
        world.store.records["shop.stub"] = SimpleNamespace(
            id="r-stub", external_record_id="shop.stub", record_type=RecordType.SQL_TABLE, is_placeholder=True)
        world.point.data.clear()
        await world.sync()
        assert "shop.ghost" in world.store.deleted and "shop.stub" in world.store.records

    async def test_a_table_referencing_an_added_table_is_written_again(self, world) -> None:
        world.catalog.add("orders", fks=[("customer_id", "customers")])
        await world.sync()
        world.store.writes.clear()
        world.catalog.add("customers")
        await world.sync()
        assert ("update", "shop.orders") in world.store.writes
        assert world.record("orders").id not in world.outcome.restructured


class TestPhase7State:
    async def test_a_phase_7_state_rewrites_nothing_and_then_follows_renames(self, world) -> None:
        world.catalog.add("orders")
        await world.sync()
        record_id = world.record("orders").id
        states = world.state()
        legacy = {fqn: {k: v for k, v in s.items() if k in ("revision", "profiled", "profile_failures")}
                  for fqn, s in states.items()}
        world.point.data[SYNC_STATE_KEY]["table_states"] = json.dumps(legacy)
        world.store.writes.clear()
        await world.sync()
        assert world.store.writes == [] and not world.outcome.restructured
        assert world.state()["shop.orders"]["table_id"] is not None and world.state()["shop.orders"]["columns"]
        world.catalog.rename(("orders", "purchases"))
        await world.sync()
        assert world.record("purchases").id == record_id


_INDEXING_OFF = FilterCollection(filters=[Filter(key="index_stage_tables", value=False, type="boolean", operator="is")])


class TestImplementationReview:
    """Phase 8 implementation review findings, each failing before its fix."""


    async def test_renamed_table_unread_under_new_name_keeps_its_record(self, world) -> None:
        # S4 says an unread table is neither written nor removed. Its old name must not be removed either.
        world.catalog.add("orders", columns=("id", ("status", "varchar(20)")))
        world.catalog.add("other")
        await world.sync()
        record_id = world.record("orders").id
        world.catalog.rename(("orders", "purchases"))
        world.catalog.unreadable = {"purchases"}
        await world.sync()
        assert record_id not in world.outcome.removed, "renamed table's record removed while its new name was unread"
        world.catalog.unreadable = set()
        await world.sync()
        assert world.record("purchases").id == record_id


    async def test_values_switched_while_a_table_is_unread_are_stripped_later(self, world) -> None:
        world.catalog.add("orders", columns=("id", ("status", "varchar(9)")))
        world.catalog.add("other")
        await world.sync()
        assert world.store.records["shop.orders.status"].top_values
        world.catalog.unreadable = {"orders"}
        await world.sync(indexing=_INDEXING_OFF)
        world.catalog.unreadable = set()
        await world.sync(indexing=_INDEXING_OFF)
        await world.sync(indexing=_INDEXING_OFF)
        assert not world.store.records["shop.orders.status"].top_values, "row values kept with indexing off"


    async def test_reload_that_hands_a_dropped_tables_id_to_a_survivor_removes_nothing(self, world) -> None:
        world.catalog.add("a", columns=("id", ("title", "varchar(9)")))
        world.catalog.add("b", columns=("id", ("label", "varchar(9)")))
        await world.sync()
        a_id = world.record("a").id
        # b dropped; failover to a server rebuilt from a dump: a gets the id b had.
        world.catalog.drop("b")
        world.catalog.ids["a"] = 102
        assert world.state()["shop.b"]["table_id"] == 102
        await world.sync()
        assert world.record("a").id == a_id, "a's record replaced by b's"
        assert a_id not in world.outcome.removed


    async def test_rotating_two_tables_follows_both_renames(self, world) -> None:
        # RENAME TABLE logs TO logs_1, logs_new TO logs; same for events.
        world.catalog.add("logs", columns=("id", ("msg", "text")))
        world.catalog.add("events", columns=("id", ("kind", "varchar(9)")))
        await world.sync()
        logs_id, events_id = world.record("logs").id, world.record("events").id
        world.catalog.rename(("logs", "logs_1"), ("events", "events_1"))
        world.catalog.add("logs", columns=("id", ("msg", "text")))
        world.catalog.add("events", columns=("id", ("kind", "varchar(9)")))
        await world.sync()
        # With one table rotated the record follows the data to logs_1; with two it should too.
        assert world.record("logs_1").id == logs_id and world.record("events_1").id == events_id


    async def test_rotating_one_table_follows_the_rename(self, world) -> None:
        world.catalog.add("logs", columns=("id", ("msg", "text")))
        world.catalog.add("events", columns=("id", ("kind", "varchar(9)")))
        await world.sync()
        logs_id = world.record("logs").id
        world.catalog.rename(("logs", "logs_1"))
        world.catalog.add("logs", columns=("id", ("msg", "text")))
        await world.sync()
        assert world.record("logs_1").id == logs_id


    async def test_a_shape_paired_rename_whose_write_fails_is_still_extracted_again(self, world) -> None:
        # A fingerprint pair may be a lookalike; it is reported as restructured, even if the write waits a sync.
        world.catalog.process = False
        world.catalog.add("orders")
        await world.sync()
        record_id = world.record("orders").id
        world.catalog.rename(("orders", "purchases"))
        processor = world.connector.data_entities_processor
        real = processor.on_record_content_update
        processor.on_record_content_update = AsyncMock(side_effect=RuntimeError("graph refused"))
        await world.sync()
        processor.on_record_content_update = real
        await world.sync()
        assert world.record("purchases").id == record_id
        assert record_id in world.outcome.restructured and record_id not in world.outcome.relations_only


    async def test_a_sweep_that_fails_without_a_state_is_tried_again(self, world) -> None:
        world.catalog.add("orders")
        world.store.records["shop.ghost"] = SimpleNamespace(
            id="r-ghost", external_record_id="shop.ghost", record_type=RecordType.SQL_TABLE, is_placeholder=False)
        world.store.fail_delete = {"shop.ghost"}
        await world.sync()  # no state: the sweep fails on ghost
        world.store.fail_delete = set()
        await world.sync()
        await world.sync()
        assert "shop.ghost" not in world.store.records, "a record the sweep could not remove stays forever"


    async def test_crash_after_checkpoint_of_a_shape_paired_rename_still_extracts_again(self, world) -> None:
        world.catalog.process = False
        world.catalog.add("orders")
        await world.sync()
        record_id = world.record("orders").id
        world.catalog.rename(("orders", "purchases"))
        world.store.crash_moves = True
        with pytest.raises(Crash):
            await world.sync()
        world.store.crash_moves = False
        await world.sync()
        assert world.record("purchases").id == record_id
        assert record_id in world.outcome.restructured and record_id not in world.outcome.relations_only
