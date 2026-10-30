"""Behaviour of repeated Snowflake syncs: what each run queues for indexing, deletes and saves.

Every sync writes every listed object; one whose revision is unchanged is not
queued for indexing again, so "queued" is what a sync changed.

The catalog is read through the real SnowflakeDataFetcher and statement runner
over a fake SQL API, and the sync state goes through the real SyncPoint into a
store that behaves like Neo4j: it merges on write and refuses map-valued
properties.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.snowflake.connector import SnowflakeConnector
from app.connectors.sources.snowflake.data_fetcher import SnowflakeDataFetcher
from app.models.entities import RecordType
from app.sources.client.snowflake.snowflake import SnowflakeResponse
from app.sources.external.snowflake.profiling import TableProfile
from app.sources.external.snowflake.statements import SnowflakeStatementRunner
from tests.unit.connectors.sources.test_snowflake_sync_lifecycle import (
    Graph,
    TxStore,
    processor,
)

DB, SCHEMA, STAGE = "DB", "S", "STG"
T1, T2, V1 = f"{DB}.{SCHEMA}.T1", f"{DB}.{SCHEMA}.T2", f"{DB}.{SCHEMA}.V1"


def _ok(columns: list[str], rows: list[list[Any]], **meta: Any) -> SnowflakeResponse:  # noqa: ANN401
    body = {"resultSetMetaData": {"rowType": [{"name": c, "type": "text"} for c in columns], **meta}, "data": rows}
    return SnowflakeResponse(success=True, data=body, status_code=200)


def _refused() -> SnowflakeResponse:
    return SnowflakeResponse(success=False, data={"message": "refused"}, error="refused", status_code=422,
                             sql_state="42501")


class FakeSnowflake:
    """One database, one schema, one stage, as the SQL API answers; tests edit it between syncs."""

    def __init__(self) -> None:
        self.tables: dict[str, str] = {"T1": "2026-01-01T00:00:00Z", "T2": "2026-01-01T00:00:00Z"}  # LAST_ALTERED
        self.views: dict[str, str] = {"V1": "2026-01-01T00:00:00Z"}
        self.files: dict[str, str] = {"a.csv": "md5-a"}
        self.refused: set[str] = set()  # "tables", "views", "columns", "directory"
        # Stage files past this index come back in a second partition.
        self.files_in_first_partition: int | None = None
        self.refuse_second_partition = False
        self.cancelled: list[str] = []

    def _files(self) -> list[list[Any]]:
        return [[path, "1", "2026-01-01T00:00:00Z", md5, None] for path, md5 in self.files.items()]

    async def execute_sql(self, *, statement: str, **_: object) -> SnowflakeResponse:
        if statement.startswith("SHOW DATABASES"):
            return _ok(["name", "kind"], [[DB, "STANDARD"]])
        if "INFORMATION_SCHEMA.SCHEMATA" in statement:
            return _ok(["SCHEMA_NAME"], [[SCHEMA]])
        if "INFORMATION_SCHEMA.TABLES" in statement:
            if "tables" in self.refused:
                return _refused()
            return _ok(["TABLE_NAME", "TABLE_TYPE", "CREATED", "LAST_ALTERED"],
                       [[t, "BASE TABLE", f"created-{t}", altered] for t, altered in self.tables.items()])
        if "INFORMATION_SCHEMA.VIEWS" in statement:
            if "views" in self.refused:
                return _refused()
            return _ok(["TABLE_NAME", "VIEW_DEFINITION", "LAST_ALTERED"],
                       [[v, "SELECT 1", altered] for v, altered in self.views.items()])
        if "INFORMATION_SCHEMA.STAGES" in statement:
            return _ok(["STAGE_NAME", "STAGE_TYPE"], [[STAGE, "INTERNAL NAMED"]])
        if "INFORMATION_SCHEMA.COLUMNS" in statement:
            if "columns" in self.refused:
                return _refused()
            return _ok(["TABLE_NAME", "COLUMN_NAME", "ORDINAL_POSITION", "DATA_TYPE"],
                       [[t, "ID", "1", "NUMBER"] for t in self.tables])
        if statement.startswith("SELECT RELATIVE_PATH"):
            if "directory" in self.refused:
                return _refused()
            rows, split = self._files(), self.files_in_first_partition
            if split is None:
                return _ok(["RELATIVE_PATH", "SIZE", "LAST_MODIFIED", "MD5", "FILE_URL"], rows)
            response = _ok(["RELATIVE_PATH", "SIZE", "LAST_MODIFIED", "MD5", "FILE_URL"], rows[:split],
                           partitionInfo=[{"rowCount": split}, {"rowCount": len(rows) - split}])
            response.data["statementHandle"] = "h-files"
            return response
        return _ok([], [])  # SHOW PRIMARY / UNIQUE / IMPORTED KEYS: none

    async def get_statement_status(self, handle: str, partition: int | None = None) -> SnowflakeResponse:
        if self.refuse_second_partition:
            return _refused()
        return SnowflakeResponse(success=True, data={"data": self._files()[self.files_in_first_partition:]},
                                 status_code=200)

    async def cancel_statement(self, handle: str) -> SnowflakeResponse:
        self.cancelled.append(handle)
        return SnowflakeResponse(success=True, data={}, status_code=200)


class Neo4jLikeStore(TxStore):
    """The lifecycle test's graph, plus sync points as Neo4j keeps them: `SET n += $data`, primitives only."""

    def __init__(self, graph: Graph, nodes: dict[str, dict[str, Any]], flags: dict[str, bool]) -> None:
        super().__init__(graph)
        self.nodes = nodes
        self.flags = flags

    async def get_sync_point(self, key: str, *, raise_on_error: bool = False) -> dict | None:
        if self.flags["fail_reads"]:
            raise ConnectionError("graph store unavailable")
        node = self.nodes.get(key)
        return dict(node) if node else None

    async def update_sync_point(self, key: str, data: dict) -> None:
        if self.flags["fail_writes"]:
            raise ConnectionError("graph store unavailable")
        for name, value in data.items():
            if isinstance(value, dict) or (isinstance(value, list) and any(isinstance(v, (dict, list)) for v in value)):
                raise TypeError(f"Neo4j cannot store property {name!r} of type {type(value).__name__}")
        self.nodes.setdefault(key, {}).update(data)

    async def delete_sync_point(self, key: str) -> None:
        self.nodes.pop(key, None)


class World:
    def __init__(self) -> None:
        self.source = FakeSnowflake()
        self.graph = Graph()
        self.nodes: dict[str, dict[str, Any]] = {}
        self.flags = {"fail_reads": False, "fail_writes": False}
        data_store = MagicMock()
        data_store.transaction = MagicMock(side_effect=lambda: Neo4jLikeStore(self.graph, self.nodes, self.flags))
        data_store.graph_provider.get_typed_records_batch = AsyncMock(
            side_effect=lambda ids: {i: self.graph.records[i] for i in ids if i in self.graph.records}
        )
        self.processor = processor(self.graph)
        self.queued: list[tuple[str, str | None]] = []
        save = self.processor.on_new_records

        async def on_new_records(batch: list) -> None:
            # The processor queues a record for indexing when it is new or its revision moved.
            for record, _ in batch:
                stored = self.graph.by_external(record.external_record_id)
                if stored is None or stored.external_revision_id != record.external_revision_id:
                    self.queued.append((record.external_record_id, record.record_type))
            await save(batch)

        self.processor.on_new_records = on_new_records
        self.connector = SnowflakeConnector(MagicMock(), self.processor, data_store, MagicMock(), "conn-sf")
        self.connector.account_identifier = "acct"
        self.connector.data_fetcher = SnowflakeDataFetcher(SnowflakeStatementRunner(self.source, warehouse="WH"))
        self.connector.profiler = MagicMock()
        self.connector.profiler.profile_table = AsyncMock(return_value=TableProfile())

    async def sync(self, *, incremental: bool = False) -> None:
        self.queued.clear()
        self.graph.deleted.clear()
        with patch(
            "app.connectors.sources.snowflake.connector.load_connector_filters",
            new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection()),
        ):
            await (self.connector.run_incremental_sync() if incremental else self.connector.run_sync())

    def changed(self) -> list[str]:
        """Tables, views and files queued for indexing this sync (column records left out)."""
        return sorted(e for e, t in self.queued if t != RecordType.SQL_COLUMN)

    def deleted(self) -> list[str]:
        return sorted(self.graph.deleted)

    def has(self, external_id: str) -> bool:
        return self.graph.by_external(external_id) is not None

    def file_id(self, path: str) -> str:
        return next(r.external_record_id for r in self.graph.of_type(RecordType.FILE) if r.path == path)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    return World()


async def test_a_second_sync_queues_only_what_changed(world) -> None:
    await world.sync()
    file_a = world.file_id("a.csv")
    assert world.changed() == sorted([T1, T2, V1, file_a])

    world.source.tables["T1"] = "2026-02-01T00:00:00Z"
    world.source.files["a.csv"] = "md5-a2"
    await world.sync()

    assert world.changed() == sorted([T1, file_a])
    assert world.deleted() == []


async def test_nothing_changed_queues_nothing(world) -> None:
    await world.sync()
    await world.sync()
    assert world.changed() == []
    assert world.deleted() == []


async def test_objects_gone_from_snowflake_are_deleted(world) -> None:
    await world.sync()
    file_a = world.file_id("a.csv")
    del world.source.tables["T2"]
    del world.source.views["V1"]
    del world.source.files["a.csv"]

    await world.sync()

    assert not world.has(T2) and not world.has(V1) and not world.has(file_a)
    assert not world.has(f"{T2}.ID")
    assert world.has(T1)


async def test_a_failed_listing_is_neither_a_deletion_nor_unchanged(world) -> None:
    await world.sync()
    world.source.refused.add("tables")
    world.source.tables["T2"] = "2026-02-01T00:00:00Z"

    await world.sync()
    assert world.deleted() == []
    assert world.has(T2)

    world.source.refused.clear()
    await world.sync()
    assert world.changed() == [T2]


@pytest.mark.parametrize("refused", ["views", "columns"])
async def test_a_refused_read_holds_its_objects_and_catches_up_once_readable(world, refused) -> None:
    # A refused views listing leaves the whole schema unread; refused columns
    # leave each table unread. Either way nothing is deleted or frozen.
    await world.sync()
    world.source.refused.add(refused)
    world.source.tables["T1"] = "2026-02-01T00:00:00Z"
    world.source.files["a.csv"] = "md5-a2"

    await world.sync()
    assert T1 not in world.changed()
    assert world.deleted() == []
    assert world.has(V1)

    world.source.refused.clear()
    await world.sync()
    assert T1 in world.changed()
    await world.sync()
    assert world.changed() == []


async def test_a_failed_delete_is_retried_next_sync(world) -> None:
    await world.sync()
    del world.source.tables["T2"]
    world.graph.fail_delete = {T2}

    await world.sync()
    assert world.has(T2)

    world.graph.fail_delete = set()
    await world.sync()
    assert not world.has(T2)


async def test_a_failed_state_write_fails_the_sync_and_the_next_one_catches_up(world) -> None:
    await world.sync()
    world.source.tables["T1"] = "2026-02-01T00:00:00Z"
    del world.source.tables["T2"]
    world.flags["fail_writes"] = True

    with pytest.raises(RuntimeError, match="sync state"):
        await world.sync()
    # The records were written before the state: the change already reached indexing.
    assert world.changed() == [T1]

    world.flags["fail_writes"] = False
    await world.sync()
    assert world.changed() == []
    assert not world.has(T2)
    await world.sync()
    assert world.changed() == [] and world.deleted() == []


async def test_a_failed_state_read_is_not_taken_for_a_first_sync(world) -> None:
    await world.sync()
    world.flags["fail_reads"] = True

    with pytest.raises(ConnectionError):
        await world.sync()
    assert world.changed() == []
    assert world.deleted() == []


async def test_an_unreadable_saved_state_falls_back_to_a_full_sync(world) -> None:
    await world.sync()
    revisions = {r.external_record_id: r.external_revision_id for r in world.graph.records.values()}
    world.nodes[next(k for k in world.nodes if k.endswith(f"snowflake_state:{DB}"))]["state"] = "{not json"

    await world.sync()

    assert world.deleted() == []
    assert world.changed() == []
    assert {r.external_record_id: r.external_revision_id for r in world.graph.records.values()} == revisions


async def test_run_incremental_sync_takes_the_same_path(world) -> None:
    await world.sync()
    world.source.tables["T1"] = "2026-02-01T00:00:00Z"

    await world.sync(incremental=True)

    assert world.changed() == [T1]


async def test_stage_files_are_read_from_every_result_partition(world) -> None:
    world.source.files["b.csv"] = "md5-b"
    await world.sync()
    world.source.files_in_first_partition = 1
    world.source.files["b.csv"] = "md5-b2"

    await world.sync()

    assert world.deleted() == []
    assert world.changed() == [world.file_id("b.csv")]


@pytest.mark.parametrize("failure", ["second partition", "directory"])
async def test_files_of_a_stage_that_could_not_be_listed_stay(world, failure) -> None:
    world.source.files["b.csv"] = "md5-b"
    await world.sync()
    file_b = world.file_id("b.csv")
    if failure == "second partition":
        world.source.files_in_first_partition = 1
        world.source.refuse_second_partition = True
    else:
        world.source.refused.add("directory")
    del world.source.files["a.csv"]

    await world.sync()

    assert world.deleted() == []
    assert world.has(file_b)


async def test_the_saved_state_is_flat_enough_for_neo4j(world) -> None:
    await world.sync()
    # Neo4jLikeStore refused anything nested; every saved value is a primitive or a flat list.
    assert world.nodes
    assert all(not isinstance(v, dict) for node in world.nodes.values() for v in node.values())
