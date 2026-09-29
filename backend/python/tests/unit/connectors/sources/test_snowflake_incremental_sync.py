"""Behaviour of repeated Snowflake syncs: what each run upserts, deletes and saves.

The listing runs through the real SnowflakeDataFetcher over a fake Snowflake,
and the sync state goes through the real SyncPoint into a store that behaves
like Neo4j: it merges on write and refuses map-valued properties.
"""
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiolimiter import AsyncLimiter

from app.config.constants.arangodb import Connectors, OriginTypes
from app.connectors.core.base.sync_point.sync_point import SyncDataPointType, SyncPoint
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.snowflake.connector import SnowflakeConnector, SyncStats
from app.connectors.sources.snowflake.data_fetcher import SnowflakeDataFetcher
from app.models.entities import Record

DB, SCHEMA, STAGE = "DB", "S", "STG"


def _ok(data: object) -> SimpleNamespace:
    return SimpleNamespace(success=True, data=data, error=None, status_code=200, sql_state=None)


def _refused() -> SimpleNamespace:
    return SimpleNamespace(success=False, data=None, error="refused", status_code=422, sql_state="42501")


def _rows(columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {"resultSetMetaData": {"rowType": [{"name": c} for c in columns]}, "data": rows}


class FakeSnowflake:
    """One database, one schema, one stage; tests edit the dicts between syncs."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, Any]] = {
            "T1": {"rows": 10, "bytes": 100},
            "T2": {"rows": 5, "bytes": 50},
        }
        self.views: dict[str, str] = {"V1": "SELECT * FROM T1"}
        self.files: dict[str, str] = {"a.csv": "md5-a"}
        self.refuse_tables = False
        self.refuse_views = False
        self.refuse_columns = False
        self.refuse_ddl = False

    async def list_databases(self, **_: object) -> SimpleNamespace:
        return _ok([{"name": DB}])

    async def list_schemas(self, database: str, **_: object) -> SimpleNamespace:
        return _ok([{"name": SCHEMA}])

    async def list_tables(self, database: str, schema: str, **_: object) -> SimpleNamespace:
        if self.refuse_tables:
            return _refused()
        return _ok([{"name": n, **meta} for n, meta in self.tables.items()])

    async def list_views(self, database: str, schema: str, **_: object) -> SimpleNamespace:
        if self.refuse_views:
            return _refused()
        return _ok([{"name": n, "text": d} for n, d in self.views.items()])

    async def list_stages(self, database: str, schema: str, **_: object) -> SimpleNamespace:
        return _ok([{"name": STAGE, "type": "INTERNAL"}])

    async def list_stage_files(self, **_: object) -> SimpleNamespace:
        return _ok({"data": [[path, 1, "2026-01-01", md5] for path, md5 in self.files.items()]})

    async def execute_sql(self, statement: str, **_: object) -> SimpleNamespace:
        if "INFORMATION_SCHEMA.COLUMNS" in statement:
            if self.refuse_columns:
                return _refused()
            return _ok(_rows(
                ["TABLE_NAME", "COLUMN_NAME", "DATA_TYPE"],
                [[t, "ID", "NUMBER"] for t in self.tables],
            ))
        if "GET_DDL('VIEW'" in statement:
            if self.refuse_ddl:
                return _refused()
            name = statement.split("'")[3].split(".")[-1]
            return _ok(_rows(["DDL"], [[self.views.get(name, "")]]))
        return _ok(_rows([], []))


class Neo4jLikeSyncPointStore:
    """Sync points as Neo4j keeps them: `SET n += $data`, primitives only."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.fail_reads = False
        self.fail_writes = False

    async def get_sync_point(self, key: str, *, raise_on_error: bool = False) -> dict | None:
        if self.fail_reads:
            raise ConnectionError("graph store unavailable")
        node = self.nodes.get(key)
        return dict(node) if node else None

    async def update_sync_point(self, key: str, data: dict) -> None:
        if self.fail_writes:
            raise ConnectionError("graph store unavailable")
        for name, value in data.items():
            if isinstance(value, dict) or (
                isinstance(value, list) and any(isinstance(v, (dict, list)) for v in value)
            ):
                raise TypeError(f"Neo4j cannot store property {name!r} of type {type(value).__name__}")
        self.nodes.setdefault(key, {}).update(data)


class FakeProcessor:
    """Stores records by external id and queues them for indexing the way the real processor does."""

    def __init__(self) -> None:
        self.org_id = "org-1"
        self.records: dict[str, Record] = {}
        self.upserted: list[str] = []
        self.queued: list[str] = []
        self.deleted: list[str] = []
        self.fail_deletes: set[str] = set()
        self.on_new_record_groups = AsyncMock()
        self.on_new_app_users = AsyncMock()
        self.ensure_team_app_edge = AsyncMock()
        self.get_user_by_user_id = AsyncMock(return_value=None)

    async def on_new_records(self, batch: list[tuple[Record, list]]) -> None:
        for record, _ in batch:
            key = record.external_record_id
            existing = self.records.get(key)
            self.upserted.append(key)
            if existing is None or existing.external_revision_id != record.external_revision_id:
                self.queued.append(key)
            if existing is not None:
                record.id = existing.id
            self.records[key] = record

    async def get_record_by_external_id(self, connector_id: str, external_record_id: str) -> Record | None:
        stored = self.records.get(external_record_id)
        if stored is None:
            return None
        return Record(
            id=stored.id,
            record_name=stored.record_name,
            record_type=stored.record_type,
            external_record_id=stored.external_record_id,
            external_revision_id=stored.external_revision_id,
            version=stored.version,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SNOWFLAKE,
            connector_id=connector_id,
        )

    async def on_record_deleted(self, record_id: str) -> None:
        key = next(k for k, r in self.records.items() if r.id == record_id)
        if key in self.fail_deletes:
            raise ConnectionError("delete failed")
        del self.records[key]
        self.deleted.append(key)


class _DataStoreProvider:
    def __init__(self, store: Neo4jLikeSyncPointStore) -> None:
        self.store = store

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Neo4jLikeSyncPointStore]:
        yield self.store


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    source = FakeSnowflake()
    store = Neo4jLikeSyncPointStore()
    processor = FakeProcessor()

    connector = SnowflakeConnector.__new__(SnowflakeConnector)
    connector.logger = logging.getLogger("test.snowflake.incremental")
    connector.connector_id = "conn-sf"
    connector.connector_name = Connectors.SNOWFLAKE
    connector.scope = "TEAM"
    connector.created_by = None
    connector.warehouse = "WH"
    connector.rate_limiter = AsyncLimiter(1000, 1)
    connector.batch_size = 100
    connector.config_service = None
    connector.sync_filters = FilterCollection()
    connector.indexing_filters = FilterCollection()
    connector._record_id_cache = {}
    connector.sync_stats = SyncStats()
    connector._sync_state_key = "snowflake_sync_state"
    connector.data_entities_processor = processor
    connector.data_source = source
    connector.data_fetcher = SnowflakeDataFetcher(source, "WH")
    connector.record_sync_point = SyncPoint(
        connector_id="conn-sf",
        org_id="org-1",
        sync_data_point_type=SyncDataPointType.RECORDS,
        data_store_provider=_DataStoreProvider(store),
    )

    async def sync() -> None:
        processor.upserted.clear()
        processor.queued.clear()
        processor.deleted.clear()
        with patch(
            "app.connectors.sources.snowflake.connector.load_connector_filters",
            new=AsyncMock(return_value=(FilterCollection(), FilterCollection())),
        ):
            await connector.run_sync()

    return SimpleNamespace(connector=connector, source=source, store=store, processor=processor, sync=sync)


T1, T2, V1, FILE_A = f"{DB}.{SCHEMA}.T1", f"{DB}.{SCHEMA}.T2", f"{DB}.{SCHEMA}.V1", f"{DB}.{SCHEMA}.{STAGE}/a.csv"


@pytest.mark.asyncio
async def test_second_sync_upserts_and_requeues_only_what_changed(env) -> None:
    await env.sync()
    assert sorted(env.processor.upserted) == sorted([T1, T2, V1, FILE_A])

    env.source.tables["T1"] = {"rows": 11, "bytes": 110}
    env.source.files["a.csv"] = "md5-a2"
    await env.sync()

    assert sorted(env.processor.upserted) == sorted([T1, FILE_A])
    assert sorted(env.processor.queued) == sorted([T1, FILE_A])


@pytest.mark.asyncio
async def test_nothing_changed_upserts_nothing(env) -> None:
    await env.sync()
    await env.sync()
    assert env.processor.upserted == []
    assert env.processor.deleted == []


@pytest.mark.asyncio
async def test_object_gone_from_snowflake_is_deleted(env) -> None:
    await env.sync()
    del env.source.tables["T2"]
    del env.source.views["V1"]
    del env.source.files["a.csv"]

    await env.sync()

    assert sorted(env.processor.deleted) == sorted([T2, V1, FILE_A])
    assert set(env.processor.records) == {T1}


@pytest.mark.asyncio
async def test_failed_listing_is_neither_a_deletion_nor_unchanged(env) -> None:
    await env.sync()
    env.source.refuse_tables = True
    env.source.tables["T2"] = {"rows": 6, "bytes": 60}

    await env.sync()
    assert env.processor.deleted == []
    assert T2 in env.processor.records

    env.source.refuse_tables = False
    await env.sync()
    assert env.processor.upserted == [T2]


@pytest.mark.asyncio
async def test_failed_delete_is_retried_next_sync(env) -> None:
    await env.sync()
    del env.source.tables["T2"]
    env.processor.fail_deletes = {T2}

    await env.sync()
    assert T2 in env.processor.records

    env.processor.fail_deletes = set()
    await env.sync()
    assert env.processor.deleted == [T2]


@pytest.mark.asyncio
async def test_failed_state_write_leaves_the_change_for_next_sync(env) -> None:
    await env.sync()
    env.source.tables["T1"] = {"rows": 11, "bytes": 110}
    env.store.fail_writes = True

    with pytest.raises(ConnectionError):
        await env.sync()

    env.store.fail_writes = False
    await env.sync()
    assert env.processor.upserted == [T1]


@pytest.mark.asyncio
async def test_failed_state_read_is_not_taken_for_a_first_sync(env) -> None:
    await env.sync()
    env.store.fail_reads = True

    with pytest.raises(ConnectionError):
        await env.sync()
    assert env.processor.upserted == []


@pytest.mark.asyncio
async def test_unreadable_saved_state_falls_back_to_a_full_sync(env) -> None:
    await env.sync()
    key = next(iter(env.store.nodes))
    env.store.nodes[key]["objects"] = "{not json"

    await env.sync()

    assert sorted(env.processor.upserted) == sorted([T1, T2, V1, FILE_A])
    assert env.processor.queued == []
    assert env.processor.deleted == []


@pytest.mark.asyncio
async def test_run_incremental_sync_takes_the_same_path(env) -> None:
    await env.sync()
    env.source.tables["T1"] = {"rows": 11, "bytes": 110}
    env.processor.upserted.clear()

    with patch(
        "app.connectors.sources.snowflake.connector.load_connector_filters",
        new=AsyncMock(return_value=(FilterCollection(), FilterCollection())),
    ):
        await env.connector.run_incremental_sync()

    assert env.processor.upserted == [T1]


@pytest.mark.asyncio
@pytest.mark.parametrize("refused", ["refuse_views", "refuse_columns"])
async def test_a_failed_read_of_one_kind_does_not_freeze_the_others(env, refused) -> None:
    await env.sync()
    setattr(env.source, refused, True)
    env.source.tables["T1"] = {"rows": 11, "bytes": 110}
    env.source.files["a.csv"] = "md5-a2"
    del env.source.tables["T2"]

    await env.sync()

    assert sorted(env.processor.upserted) == sorted([T1, FILE_A])
    assert env.processor.deleted == [T2]
    assert V1 in env.processor.records

    setattr(env.source, refused, False)
    await env.sync()
    assert env.processor.upserted == []


def _saved_revisions(env, kind: str) -> dict[str, Any]:
    (node,) = env.store.nodes.values()
    return json.loads(node["objects"])[kind]


@pytest.mark.asyncio
async def test_failed_view_definition_keeps_the_old_revision_for_a_retry(env) -> None:
    await env.sync()
    before = _saved_revisions(env, "views")[V1]
    env.source.views["V1"] = "SELECT id FROM T1"
    env.source.refuse_ddl = True

    await env.sync()
    assert V1 not in env.processor.upserted
    assert _saved_revisions(env, "views")[V1] == before

    env.source.refuse_ddl = False
    await env.sync()
    assert env.processor.upserted == [V1]
    assert env.processor.records[V1].definition == "SELECT id FROM T1"
