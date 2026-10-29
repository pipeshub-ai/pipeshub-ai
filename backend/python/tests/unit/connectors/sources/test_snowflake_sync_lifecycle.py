"""Snowflake sync lifecycle across syncs: renames, removals, unreadable scopes, saved state.

Runs the real connector over an in-memory graph and state store, so a test can
sync, change the catalog, and sync again, as production does.
"""

import itertools
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.registry.filters import Filter, FilterCollection
from app.connectors.sources.snowflake.connector import SnowflakeConnector
from app.connectors.sources.snowflake.data_fetcher import (
    ForeignKey,
    SnowflakeColumnInfo,
    SnowflakeDatabase,
    SnowflakeFile,
    SnowflakeHierarchy,
    SnowflakeSchema,
    SnowflakeStage,
    SnowflakeTable,
    SnowflakeView,
    schema_key,
)
from app.connectors.sources.snowflake.sync_state import SnowflakeStateStore
from app.models.entities import RecordType
from app.sources.external.snowflake.profiling import TableProfile

CONFIG = {"auth": {"accountIdentifier": "acct", "warehouse": "WH", "patToken": "pat"}}
_ids = itertools.count(1)


# --- an in-memory graph ----------------------------------------------------------


class Crash(BaseException):
    """The process dying: nothing in the connector catches it."""


class Graph:
    def __init__(self) -> None:
        self.records: dict[str, Any] = {}       # id -> record
        self.groups: dict[str, dict] = {}       # external id -> node
        self.deleted: list[str] = []
        self.renamed_events: list[list[str]] = []
        self.fail_delete: set[str] = set()     # external ids whose delete raises
        self.fail_rename_event = False
        self.crash_on_move = False


    # lookups
    def by_external(self, external_id: str) -> Any | None:  # noqa: ANN401
        return next((r for r in self.records.values() if r.external_record_id == external_id), None)

    def of_type(self, record_type: RecordType) -> list[Any]:
        return [r for r in self.records.values() if r.record_type == record_type]

    def children(self, parent: str) -> list[Any]:
        return [r for r in self.records.values() if getattr(r, "parent_external_record_id", None) == parent]

    # writes
    def save(self, record: Any) -> None:  # noqa: ANN401
        existing = self.by_external(record.external_record_id)
        if existing is not None:
            record.id = existing.id
            del self.records[existing.id]
        self.records[record.id] = record

    def delete_ids(self, ids: list[str]) -> list[str]:
        for record_id in ids:
            record = self.records.get(record_id)
            if record is not None and record.external_record_id in self.fail_delete:
                raise RuntimeError("graph refused")
        removed = []
        for record_id in ids:
            record = self.records.pop(record_id, None)
            if record is None:
                continue
            removed.append(record_id)
            if record.record_type == RecordType.SQL_TABLE:
                for child in self.children(record.external_record_id):
                    self.records.pop(child.id, None)
                    removed.append(child.id)
        self.deleted += removed
        return removed


class TxStore:
    def __init__(self, graph: Graph) -> None:
        self.graph = graph

    async def __aenter__(self) -> "TxStore":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get_record_by_external_id(self, connector_id, external_id, raise_on_error=False) -> object:
        return self.graph.by_external(external_id)

    async def get_records_by_parent(self, connector_id, parent, record_type=None) -> list:
        return self.graph.children(parent)

    async def batch_update_nodes(self, nodes, collection) -> bool:
        if self.graph.crash_on_move:
            raise Crash()
        for node in nodes:
            record = self.graph.records[node["id"]]
            record.external_record_id = node["externalRecordId"]
            if "externalParentId" in node:
                record.parent_external_record_id = node["externalParentId"]
                record.table_fqn = node["externalParentId"]
        return True

    async def get_nodes_by_filters(self, collection, filters) -> list:
        return [g for g in self.graph.groups.values() if g["groupType"] == filters["groupType"]]


class StateSyncPoint:
    def __init__(self) -> None:
        self.data: dict[str, dict] = {}
        self.fail_writes = False

    async def read_sync_point(self, key) -> dict:
        return self.data.get(key, {})

    async def update_sync_point(self, key, value) -> None:
        if self.fail_writes:
            raise RuntimeError("key-value store down")
        self.data[key] = dict(value)

    async def delete_sync_point(self, key) -> None:
        self.data.pop(key, None)


def processor(graph: Graph) -> MagicMock:
    p = MagicMock()
    p.org_id = "org-1"

    async def get_record_by_external_id(connector_id, external_record_id, raise_on_error=False) -> object:
        return graph.by_external(external_record_id)

    async def on_new_records(batch) -> None:
        for record, _ in batch:
            graph.save(record)

    async def on_record_group_deleted(external_id, connector_id) -> bool:
        # As the processor does: the records whose own group it is go with it.
        own = [r.id for r in graph.records.values() if getattr(r, "external_record_group_id", None) == external_id]
        graph.delete_ids(own)
        return graph.groups.pop(external_id, None) is not None

    async def on_new_record_groups(groups) -> None:
        for group, _ in groups:
            graph.groups[group.external_group_id] = {
                "externalGroupId": group.external_group_id,
                "groupType": group.group_type.value if hasattr(group.group_type, "value") else group.group_type,
                "id": f"g-{group.external_group_id}",
            }

    async def on_sql_records_renamed(ids, connector_id) -> None:
        if graph.fail_rename_event:
            raise RuntimeError("broker down")
        graph.renamed_events.append(list(ids))

    p.get_record_by_external_id = get_record_by_external_id
    p.on_new_records = on_new_records
    p.on_new_record_groups = on_new_record_groups
    p.on_record_group_deleted = on_record_group_deleted
    p.on_sql_record_groups_deleted = AsyncMock()
    p.on_sql_records_deleted = AsyncMock(side_effect=lambda ids, connector_id, table_record_id=None: graph.delete_ids(ids))
    p.on_sql_records_renamed = on_sql_records_renamed
    p.get_records_by_record_type = AsyncMock(side_effect=lambda connector_id, record_type: graph.of_type(record_type))
    p.ensure_team_app_edge = AsyncMock()
    p.sql_tables_to_relate_again = AsyncMock(return_value=set())
    p.on_sql_table_renamed = AsyncMock()
    return p


# --- a catalog ------------------------------------------------------------------


def table(name: str, *, schema: str = "S", database: str = "DB", created: str | None = None,
          columns=(("ID", "NUMBER(38,0)"), ("NAME", "TEXT")), altered: str = "2024-01-01T00:00:00+00:00",
          fks=(), read: bool = True) -> SnowflakeTable:
    t = SnowflakeTable(name, database, schema, row_count=0, bytes=0, table_type="BASE TABLE",
                       created_at=created or f"created-{database}.{schema}.{name}", last_altered=altered)
    t.columns = [SnowflakeColumnInfo(name=n, ordinal_position=i + 1, data_type=dt) for i, (n, dt) in enumerate(columns)]
    t.foreign_keys = [ForeignKey("FK", database, schema, name, src, database, schema, target, "ID") for src, target in fks]
    t.columns_read = t.keys_read = t.foreign_keys_read = read
    return t


def catalog(*tables: SnowflakeTable, views=(), stages=(), files=None, unreadable_schemas=(),
            unreadable_databases=(), unreadable_stages=(), databases=("DB",)) -> SnowflakeHierarchy:
    h = SnowflakeHierarchy(databases=[SnowflakeDatabase(d) for d in databases])
    schemas: dict[str, set[str]] = {d: set() for d in databases if d not in unreadable_databases}
    for t in tables:
        schemas.setdefault(t.database_name, set()).add(t.schema_name)
        h.tables.setdefault(schema_key(t.database_name, t.schema_name), []).append(t)
    for v in views:
        schemas.setdefault(v.database_name, set()).add(v.schema_name)
        h.views.setdefault(schema_key(v.database_name, v.schema_name), []).append(v)
    for st in stages:
        schemas.setdefault(st.database_name, set()).add(st.schema_name)
        h.stages.setdefault(schema_key(st.database_name, st.schema_name), []).append(st)
    for key in unreadable_schemas:
        db, schema = key.split(".")
        schemas.setdefault(db, set()).add(schema)
    for db, names in schemas.items():
        if db not in unreadable_databases:
            h.schemas[db] = [SnowflakeSchema(n, db) for n in sorted(names)]
    h.files = dict(files or {})
    h.unreadable_schemas = set(unreadable_schemas)
    h.unreadable_databases = set(unreadable_databases)
    h.unreadable_stages = set(unreadable_stages)
    return h


class World:
    def __init__(self) -> None:
        self.graph = Graph()
        self.sync_point = StateSyncPoint()
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=CONFIG)
        data_store = MagicMock()
        data_store.transaction = MagicMock(side_effect=lambda: TxStore(self.graph))
        self.connector = SnowflakeConnector(MagicMock(), processor(self.graph), data_store, config_service, "conn-1")
        self.connector.data_entities_processor.get_typed_records_by_ids = AsyncMock(
            side_effect=lambda ids, **_: {i: self.graph.records[i] for i in ids if i in self.graph.records}
        )
        self.connector.data_entities_processor.move_incoming_relations = AsyncMock()
        self.connector.account_identifier = "acct"
        self.connector._state_store = SnowflakeStateStore(self.sync_point)
        self.connector.data_fetcher = MagicMock()
        self.connector.profiler = MagicMock()
        self.connector.profiler.profile_table = AsyncMock(return_value=TableProfile())
        self.filters = FilterCollection()

    async def sync(self, hierarchy: SnowflakeHierarchy) -> object:
        self.connector.data_fetcher.fetch_all = AsyncMock(return_value=hierarchy)
        with patch(
            "app.connectors.sources.snowflake.connector.load_connector_filters",
            new_callable=AsyncMock, return_value=(self.filters, FilterCollection()),
        ), patch("app.connectors.sources.snowflake.connector.LOOKUP_ATTEMPTS", 1):
            await self.connector.run_sync()
        return self.connector.sync_outcome

    def record(self, fqn: str) -> object:
        return self.graph.by_external(fqn)

    def ids(self, record_type: RecordType) -> set[str]:
        return {r.external_record_id for r in self.graph.of_type(record_type)}


@pytest.fixture
def world() -> World:
    return World()


# --- tests ----------------------------------------------------------------------


class TestFirstSync:
    async def test_every_table_is_written_and_restructured_and_the_state_saved(self, world) -> None:
        outcome = await world.sync(catalog(table("A"), table("B")))
        assert world.ids(RecordType.SQL_TABLE) == {"DB.S.A", "DB.S.B"}
        assert world.ids(RecordType.SQL_COLUMN) == {"DB.S.A.ID", "DB.S.A.NAME", "DB.S.B.ID", "DB.S.B.NAME"}
        assert {world.graph.records[i].external_record_id for i in outcome.restructured} == {"DB.S.A", "DB.S.B"}
        index = world.sync_point.data["snowflake_state"]
        assert (index["account"], index["databases"]) == ("acct", ["DB"])

    async def test_without_a_state_records_nothing_lists_are_swept_but_placeholders_stay(self, world) -> None:
        await world.sync(catalog(table("A"), table("OLD")))
        world.sync_point.data.clear()  # an explicit full sync, or another account's state
        placeholder = SimpleNamespace(id="ph", external_record_id="DB.S.STANDIN", record_type=RecordType.SQL_TABLE,
                                      is_placeholder=True)
        world.graph.records["ph"] = placeholder
        await world.sync(catalog(table("A")))
        assert world.ids(RecordType.SQL_TABLE) == {"DB.S.A", "DB.S.STANDIN"}

    async def test_a_state_saved_for_another_account_is_not_used(self, world) -> None:
        await world.sync(catalog(table("A")))
        world.connector.account_identifier = "other"
        outcome = await world.sync(catalog(table("A")))
        # Nothing to compare with: every table counts as new again.
        assert len(outcome.restructured) == 1


class TestLaterSyncs:
    async def test_an_unchanged_catalog_restructures_nothing(self, world) -> None:
        await world.sync(catalog(table("A")))
        outcome = await world.sync(catalog(table("A")))
        assert outcome.restructured == set() and outcome.removed == set() and outcome.renamed == set()

    async def test_changed_rows_restructure_nothing(self, world) -> None:
        await world.sync(catalog(table("A")))
        outcome = await world.sync(catalog(table("A", altered="2024-06-01T00:00:00+00:00")))
        assert outcome.restructured == set()

    async def test_an_added_column_restructures_the_table(self, world) -> None:
        await world.sync(catalog(table("A")))
        a = world.record("DB.S.A").id
        outcome = await world.sync(catalog(table("A", columns=(("ID", "NUMBER(38,0)"), ("NAME", "TEXT"), ("X", "DATE")))))
        assert outcome.restructured == {a}

    async def test_a_table_renamed_out_of_a_renamed_schema_survives_a_failed_write(self, world) -> None:
        # ALTER SCHEMA S RENAME TO S2: the record moves, but keeps its old group
        # until it is written, and removing that group would take it along.
        await world.sync(catalog(table("A", created="c1")))
        table_id = world.record("DB.S.A").id
        world.connector._table_revision = MagicMock(side_effect=RuntimeError("boom"))
        await world.sync(catalog(table("A", schema="S2", created="c1")))
        assert world.record("DB.S2.A").id == table_id
        assert "DB.S" in world.graph.groups

        del world.connector._table_revision
        await world.sync(catalog(table("A", schema="S2", created="c1")))
        assert world.record("DB.S2.A").id == table_id
        assert "DB.S" not in world.graph.groups

    async def test_a_renamed_table_keeps_its_records(self, world) -> None:
        await world.sync(catalog(table("A", created="c1")))
        table_id = world.record("DB.S.A").id
        column_ids = {r.id for r in world.graph.children("DB.S.A")}
        outcome = await world.sync(catalog(table("B", created="c1")))
        assert world.record("DB.S.B").id == table_id
        assert {r.id for r in world.graph.children("DB.S.B")} == column_ids
        assert world.record("DB.S.A") is None
        assert outcome.renamed == {table_id} and outcome.relations_only == {table_id}
        assert outcome.restructured == set() and outcome.removed == set()
        assert world.graph.renamed_events == [[table_id]]

    async def test_a_renamed_column_keeps_its_record(self, world) -> None:
        await world.sync(catalog(table("A")))
        name_id = world.record("DB.S.A.NAME").id
        await world.sync(catalog(table("A", columns=(("ID", "NUMBER(38,0)"), ("LABEL", "TEXT")))))
        assert world.record("DB.S.A.LABEL").id == name_id
        assert world.record("DB.S.A.NAME") is None

    async def test_two_tables_that_swapped_names_keep_their_records(self, world) -> None:
        await world.sync(catalog(table("A", created="ca"), table("B", created="cb")))
        a, b = world.record("DB.S.A").id, world.record("DB.S.B").id
        await world.sync(catalog(table("A", created="cb"), table("B", created="ca")))
        assert (world.record("DB.S.B").id, world.record("DB.S.A").id) == (a, b)

    async def test_a_dropped_table_is_removed_with_its_columns(self, world) -> None:
        await world.sync(catalog(table("A"), table("B")))
        b = world.record("DB.S.B").id
        outcome = await world.sync(catalog(table("A")))
        assert world.ids(RecordType.SQL_TABLE) == {"DB.S.A"}
        assert "DB.S.B.ID" not in world.ids(RecordType.SQL_COLUMN)
        assert b in outcome.removed

    async def test_a_table_the_filter_now_excludes_is_removed(self, world) -> None:
        await world.sync(catalog(table("A"), table("B")))
        world.filters = FilterCollection(filters=[Filter(key="tables", value=["DB.S.B"], type="list",
                                                         operator="not_in")])
        await world.sync(catalog(table("A"), table("B")))
        assert world.ids(RecordType.SQL_TABLE) == {"DB.S.A"}


class TestFullSyncCoverage:
    """A full sync unlinks what it did not write, unless it says it skipped something."""

    async def test_a_sync_that_wrote_everything_is_complete(self, world) -> None:
        await world.sync(catalog(table("A")))
        assert world.connector.full_sync_coverage().complete

    async def test_an_unreadable_schema_or_table_makes_it_incomplete_until_read_again(self, world) -> None:
        await world.sync(catalog(table("A"), unreadable_schemas=["DB.T"]))
        assert not world.connector.full_sync_coverage().complete
        await world.sync(catalog(table("A", read=False)))
        assert not world.connector.full_sync_coverage().complete
        await world.sync(catalog(table("A")))
        assert world.connector.full_sync_coverage().complete

    async def test_a_table_whose_write_failed_makes_it_incomplete(self, world) -> None:
        identity = world.connector._record_identity

        async def failing(fqn: str) -> object:
            if fqn == "DB.S.B":
                raise RuntimeError("graph unavailable")
            return await identity(fqn)

        world.connector._record_identity = failing
        await world.sync(catalog(table("A"), table("B")))
        assert "DB.S.A" in world.ids(RecordType.SQL_TABLE)
        assert not world.connector.full_sync_coverage().complete


class TestUnreadable:
    async def test_nothing_under_an_unreadable_schema_is_removed_or_restructured(self, world) -> None:
        await world.sync(catalog(table("A"), table("B", schema="T")))
        outcome = await world.sync(catalog(table("A"), unreadable_schemas=["DB.T"]))
        assert world.ids(RecordType.SQL_TABLE) == {"DB.S.A", "DB.T.B"}
        assert outcome.removed == set()
        # And it is still known: once readable again, it is unchanged.
        outcome = await world.sync(catalog(table("A"), table("B", schema="T")))
        assert outcome.restructured == set()

    async def test_nothing_under_an_unreadable_database_is_removed(self, world) -> None:
        await world.sync(catalog(table("A"), table("B", database="D2"), databases=("DB", "D2")))
        await world.sync(catalog(table("A"), databases=("DB", "D2"), unreadable_databases=["D2"]))
        assert world.ids(RecordType.SQL_TABLE) == {"DB.S.A", "D2.S.B"}

    async def test_a_table_whose_foreign_keys_were_unread_is_left_alone(self, world) -> None:
        await world.sync(catalog(table("A")))
        before = world.record("DB.S.A")
        outcome = await world.sync(catalog(table("A", read=False)))
        assert world.record("DB.S.A") is before
        assert outcome.restructured == set() and outcome.removed == set()
        # Read again later and unchanged: still nothing to do.
        outcome = await world.sync(catalog(table("A")))
        assert outcome.restructured == set()

    async def test_a_rename_is_still_followed_after_a_sync_that_could_not_read_the_table(self, world) -> None:
        await world.sync(catalog(table("A", created="c1")))
        table_id = world.record("DB.S.A").id
        await world.sync(catalog(table("A", created="c1", read=False)))
        await world.sync(catalog(table("B", created="c1")))
        assert world.record("DB.S.B").id == table_id


class TestFailuresAreRetried:
    async def test_a_removal_that_failed_is_tried_again(self, world) -> None:
        await world.sync(catalog(table("A"), table("B")))
        world.graph.fail_delete = {"DB.S.B"}
        await world.sync(catalog(table("A")))
        assert "DB.S.B" in world.ids(RecordType.SQL_TABLE)
        world.graph.fail_delete = set()
        await world.sync(catalog(table("A")))
        assert world.ids(RecordType.SQL_TABLE) == {"DB.S.A"}

    async def test_a_table_whose_write_failed_is_seen_as_new_next_sync(self, world) -> None:
        await world.sync(catalog(table("A")))
        original = world.connector._column_records
        world.connector._column_records = AsyncMock(side_effect=RuntimeError("boom"))
        failed = await world.sync(catalog(table("A"), table("B")))
        # Not written, so not reported as restructured; the next sync finds it new.
        assert failed.restructured == set()
        world.connector._column_records = original
        outcome = await world.sync(catalog(table("A"), table("B")))
        assert world.record("DB.S.B").id in outcome.restructured

    async def test_a_restructured_table_whose_write_failed_is_not_handed_over(self, world) -> None:
        await world.sync(catalog(table("A")))
        a = world.record("DB.S.A").id
        wider = table("A", columns=(("ID", "NUMBER(38,0)"), ("NAME", "TEXT"), ("X", "DATE")))
        original = world.connector._column_records
        world.connector._column_records = AsyncMock(side_effect=RuntimeError("boom"))
        assert (await world.sync(catalog(wider))).restructured == set()
        world.connector._column_records = original
        assert (await world.sync(catalog(wider))).restructured == {a}

    async def test_a_rename_announcement_that_fails_means_extracting_again(self, world) -> None:
        await world.sync(catalog(table("A", created="c1")))
        table_id = world.record("DB.S.A").id
        world.graph.fail_rename_event = True
        outcome = await world.sync(catalog(table("B", created="c1")))
        assert table_id in outcome.restructured


class TestInterruptedRenames:
    """A rename never runs twice, and one cut off is finished, whatever stops the sync."""

    async def test_a_state_that_cannot_be_saved_moves_nothing_and_fails_the_sync(self, world) -> None:
        await world.sync(catalog(table("A", created="c1")))
        table_id = world.record("DB.S.A").id
        world.sync_point.fail_writes = True
        with pytest.raises(RuntimeError):
            await world.sync(catalog(table("B", created="c1")))
        assert world.record("DB.S.A").id == table_id and world.record("DB.S.B") is None
        world.sync_point.fail_writes = False
        await world.sync(catalog(table("B", created="c1")))
        assert world.record("DB.S.B").id == table_id and world.record("DB.S.A") is None

    @pytest.mark.parametrize("crash", ["before the move", "after the move"])
    async def test_a_rename_cut_off_is_finished_next_sync(self, world, crash) -> None:
        await world.sync(catalog(table("A", created="c1")))
        table_id = world.record("DB.S.A").id
        self._crash(world, crash)
        with pytest.raises(Crash):
            await world.sync(catalog(table("B", created="c1")))
        self._recover(world)
        await world.sync(catalog(table("B", created="c1")))
        assert world.record("DB.S.B").id == table_id and world.record("DB.S.A") is None
        await world.sync(catalog(table("B", created="c1")))
        assert world.record("DB.S.B").id == table_id

    @pytest.mark.parametrize("crash", ["before the move", "after the move"])
    async def test_a_swap_cut_off_is_finished_and_never_undone(self, world, crash) -> None:
        await world.sync(catalog(table("A", created="ca"), table("B", created="cb")))
        a, b = world.record("DB.S.A").id, world.record("DB.S.B").id
        self._crash(world, crash)
        with pytest.raises(Crash):
            await world.sync(catalog(table("A", created="cb"), table("B", created="ca")))
        self._recover(world)
        for _ in range(2):
            await world.sync(catalog(table("A", created="cb"), table("B", created="ca")))
            assert (world.record("DB.S.B").id, world.record("DB.S.A").id) == (a, b)

    async def test_a_move_whose_old_record_is_gone_is_given_up(self, world) -> None:
        await world.sync(catalog(table("A", created="c1")))
        world.graph.crash_on_move = True
        with pytest.raises(Crash):
            await world.sync(catalog(table("B", created="c1")))
        world.graph.crash_on_move = False
        world.graph.delete_ids([world.record("DB.S.A").id])
        await world.sync(catalog(table("B", created="c1")))
        assert world.record("DB.S.B") is not None

    async def test_a_table_renamed_into_a_name_that_could_not_be_read_keeps_its_record(self, world) -> None:
        await world.sync(catalog(table("A", created="c1")))
        table_id = world.record("DB.S.A").id
        outcome = await world.sync(catalog(table("B", created="c1", read=False)))
        assert world.record("DB.S.A").id == table_id and outcome.removed == set()
        await world.sync(catalog(table("B", created="c1")))
        assert world.record("DB.S.B").id == table_id

    @staticmethod
    def _crash(world, when: str) -> None:
        if when == "before the move":
            world.graph.crash_on_move = True
        else:
            world.connector._follow_renames = AsyncMock(side_effect=Crash())

    @staticmethod
    def _recover(world) -> None:
        world.graph.crash_on_move = False
        world.connector._follow_renames = SnowflakeConnector._follow_renames.__get__(world.connector)


class TestOtherObjects:
    async def test_stale_views_files_and_groups_are_removed_in_order(self, world) -> None:
        view = SnowflakeView("V", "DB", "S", definition="select 1", last_altered="t")
        stage = SnowflakeStage("ST", "DB", "S")
        f = SnowflakeFile("a.csv", "ST", "DB", "S", md5="m")
        await world.sync(catalog(table("A"), table("Z", schema="OLD"), views=[view], stages=[stage],
                                 files={"DB.S.ST": [f]}))
        assert world.ids(RecordType.SQL_VIEW) == {"DB.S.V"} and world.ids(RecordType.FILE) == {"DB.S.ST/a.csv"}
        await world.sync(catalog(table("A")))
        assert world.ids(RecordType.SQL_VIEW) == set() and world.ids(RecordType.FILE) == set()
        assert world.ids(RecordType.SQL_TABLE) == {"DB.S.A"}
        assert "DB.S.ST" not in world.graph.groups and "DB.OLD" not in world.graph.groups
        assert "DB.S" in world.graph.groups

    async def test_files_of_a_stage_that_could_not_be_listed_stay(self, world) -> None:
        stage = SnowflakeStage("ST", "DB", "S")
        f = SnowflakeFile("a.csv", "ST", "DB", "S", md5="m")
        await world.sync(catalog(table("A"), stages=[stage], files={"DB.S.ST": [f]}))
        await world.sync(catalog(table("A"), stages=[stage], unreadable_stages=["DB.S.ST"]))
        assert world.ids(RecordType.FILE) == {"DB.S.ST/a.csv"}
        assert "DB.S.ST" in world.graph.groups

    async def test_a_stage_whose_file_could_not_be_removed_keeps_its_group(self, world) -> None:
        stage = SnowflakeStage("ST", "DB", "S")
        f = SnowflakeFile("a.csv", "ST", "DB", "S", md5="m")
        await world.sync(catalog(table("A"), stages=[stage], files={"DB.S.ST": [f]}))
        world.graph.fail_delete = {"DB.S.ST/a.csv"}
        await world.sync(catalog(table("A")))
        assert "DB.S.ST" in world.graph.groups

    async def test_a_view_links_only_to_tables_the_sync_filters_keep(self, world) -> None:
        world.filters = FilterCollection(filters=[Filter(key="tables", value=["DB.S.HIDDEN"], type="list",
                                                         operator="not_in")])
        view = SnowflakeView("V", "DB", "S", definition="select * from a join hidden on 1=1", last_altered="t")
        await world.sync(catalog(table("A"), table("HIDDEN"), views=[view]))
        links = [r.external_record_id for r in world.record("DB.S.V").related_external_records]
        assert links == ["DB.S.A"]

    async def test_names_with_slashes(self, world) -> None:
        stage = SnowflakeStage("ST/X", "MY/DB", "S")
        f = SnowflakeFile("a.csv", "ST/X", "MY/DB", "S", md5="m")
        await world.sync(catalog(table("A", database="MY/DB"), stages=[stage], files={"MY/DB.S.ST/X": [f]},
                                 databases=("MY/DB",)))
        assert world.ids(RecordType.FILE) == {"MY/DB.S.ST/X/a.csv"}
        # The stage could not be listed: its file stays.
        await world.sync(catalog(table("A", database="MY/DB"), stages=[stage], unreadable_stages=["MY/DB.S.ST/X"],
                                 databases=("MY/DB",)))
        assert world.ids(RecordType.FILE) == {"MY/DB.S.ST/X/a.csv"}
        await world.sync(catalog(table("A", database="MY/DB"), databases=("MY/DB",)))
        assert world.ids(RecordType.FILE) == set() and "MY/DB.S.ST/X" not in world.graph.groups
        assert world.sync_point.data["snowflake_state"]["databases"] == ["MY/DB"]

    async def test_a_database_whose_group_could_not_be_removed_is_tried_again(self, world) -> None:
        await world.sync(catalog(table("A"), databases=("DB", "D2")))
        processor_ = world.connector.data_entities_processor
        original = processor_.on_record_group_deleted
        processor_.on_record_group_deleted = AsyncMock(return_value=False)
        await world.sync(catalog(table("A")))
        assert "D2" in world.sync_point.data["snowflake_state"]["databases"]
        processor_.on_record_group_deleted = original
        await world.sync(catalog(table("A")))
        assert "D2" not in world.graph.groups
        assert world.sync_point.data["snowflake_state"]["databases"] == ["DB"]

    async def test_a_group_already_gone_counts_as_removed(self, world) -> None:
        await world.sync(catalog(table("A"), databases=("DB", "D2")))
        del world.graph.groups["D2"]
        errors_before = world.connector.sync_stats.errors
        await world.sync(catalog(table("A")))
        assert world.connector.sync_stats.errors == errors_before
        assert world.sync_point.data["snowflake_state"]["databases"] == ["DB"]

    async def test_a_sweep_whose_removal_failed_keeps_the_view_for_next_sync(self, world) -> None:
        view = SnowflakeView("V", "DB", "S", definition="select 1", last_altered="t")
        await world.sync(catalog(table("A"), views=[view]))
        world.sync_point.data.clear()
        world.graph.fail_delete = {"DB.S.V"}
        await world.sync(catalog(table("A")))
        assert world.ids(RecordType.SQL_VIEW) == {"DB.S.V"}
        world.graph.fail_delete = set()
        await world.sync(catalog(table("A")))
        assert world.ids(RecordType.SQL_VIEW) == set()

    async def test_a_sweep_that_cannot_list_records_does_not_fail_the_sync(self, world) -> None:
        await world.sync(catalog(table("A")))
        world.sync_point.data.clear()
        world.connector.data_entities_processor.get_records_by_record_type = AsyncMock(side_effect=RuntimeError("x"))
        await world.sync(catalog(table("A")))
        assert "snowflake_state" in world.sync_point.data

    async def test_a_dropped_database_is_removed(self, world) -> None:
        await world.sync(catalog(table("A"), table("B", database="D2"), databases=("DB", "D2")))
        await world.sync(catalog(table("A")))
        assert world.ids(RecordType.SQL_TABLE) == {"DB.S.A"}
        assert "D2" not in world.graph.groups and world.sync_point.data["snowflake_state"]["databases"] == ["DB"]
        assert "snowflake_state:D2" not in world.sync_point.data
