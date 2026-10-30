"""SnowflakeConnector: construction, init, sync, streaming and filters."""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, RecordRelations
from app.connectors.core.base.error.stream_errors import map_source_status
from app.connectors.core.factory.connector_factory import ConnectorFactory
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    IndexingFilterKey,
)
from app.connectors.core.sql.key_flags import KeyDefinition
from app.connectors.core.sql.profiling import ColumnProfile
from app.connectors.sources.snowflake.connector import (
    MAX_COLUMN_VALUES_STREAMED,
    MAX_TABLE_DOCUMENT_BYTES,
    ORDERED_READ_MAX_ROWS,
    SnowflakeConnector,
    parse_view_sources,
    resolve_source_table,
    to_snowflake_stream_error,
)
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
)
from app.models.entities import ProgressStatus, RecordType
from app.models.permission import EntityType, PermissionType
from app.sources.external.snowflake.profiling import PROFILE_MAX_BYTES, TableProfile
from app.sources.external.snowflake.statements import (
    SnowflakeFetchError,
    SnowflakeResult,
)

STORED_CONFIG = {
    "auth": {"accountIdentifier": "acct", "warehouse": "WH", "patToken": "pat"},
    "scope": ConnectorScope.PERSONAL.value,
    "created_by": "user-9",
}


def processor() -> MagicMock:
    p = MagicMock()
    p.org_id = "org-1"
    p.get_record_by_external_id = AsyncMock(return_value=None)
    p.on_new_records = AsyncMock()
    p.on_new_record_groups = AsyncMock()
    p.ensure_team_app_edge = AsyncMock()
    p.get_user_by_user_id = AsyncMock()
    p.on_new_app_users = AsyncMock()
    p.reindex_existing_records = AsyncMock()
    return p


def make_connector(**kwargs: Any) -> SnowflakeConnector:  # noqa: ANN401
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value=STORED_CONFIG)
    return SnowflakeConnector(
        MagicMock(), processor(), MagicMock(), config_service, "conn-1", **kwargs,
    )


def filters(*items: Filter) -> FilterCollection:
    return FilterCollection(filters=list(items))


def multiselect(key: str, values: list[str], operator: str = "in") -> Filter:
    return Filter(key=key, value=values, type="list", operator=operator)


def toggle(key: str, value: bool) -> Filter:
    return Filter(key=key, value=value, type="boolean", operator="is")


def ready_connector(**kwargs: Any) -> SnowflakeConnector:  # noqa: ANN401
    connector = make_connector(**kwargs)
    connector.data_fetcher = MagicMock()
    connector.runner = MagicMock()
    connector.runner.run = AsyncMock(return_value=SnowflakeResult(columns=[], rows=[]))
    # No saved state; nothing stored to sweep.
    connector._state_store = MagicMock()
    connector._state_store.load = AsyncMock(return_value=None)
    connector._state_store.save = AsyncMock()
    connector.data_entities_processor.get_records_by_record_type = AsyncMock(return_value=[])
    connector._stored_groups = AsyncMock(return_value={})
    connector._stored_columns = AsyncMock(return_value={})
    return connector


def listed(*args: Any, **kwargs: Any) -> SnowflakeTable:  # noqa: ANN401
    """A table whose columns, keys and foreign keys were read."""
    table = SnowflakeTable(*args, **kwargs)
    table.columns_read = table.keys_read = table.foreign_keys_read = True
    return table


async def collect(generator) -> list:
    return [item async for item in generator]


# ---------------------------------------------------------------------------
# Construction and registration
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_constructs_without_patching_the_base_class(self) -> None:
        connector = make_connector(scope=ConnectorScope.PERSONAL.value, created_by="u1")
        assert connector.scope == ConnectorScope.PERSONAL.value
        assert connector.created_by == "u1"
        assert connector.connector_name == Connectors.SNOWFLAKE

    def test_defaults_to_team_scope(self) -> None:
        assert make_connector().scope == ConnectorScope.TEAM.value

    async def test_create_connector_forwards_scope_and_creator(self) -> None:
        connector = await SnowflakeConnector.create_connector(
            logger=MagicMock(), data_store_provider=MagicMock(), config_service=MagicMock(),
            connector_id="c", data_entities_processor=processor(),
            scope=ConnectorScope.PERSONAL.value, created_by="u1",
        )
        assert (connector.scope, connector.created_by) == (ConnectorScope.PERSONAL.value, "u1")

    def test_registered_in_the_factory(self) -> None:
        assert ConnectorFactory.get_connector_class("Snowflake") is SnowflakeConnector

    async def test_the_factory_builds_one(self) -> None:
        processor_cls = MagicMock(return_value=processor())
        processor_cls.return_value.initialize = AsyncMock()
        connector = await ConnectorFactory.create_connector(
            "snowflake", MagicMock(), MagicMock(), MagicMock(), "conn-1",
            scope=ConnectorScope.TEAM.value, created_by="u1",
            data_entities_processor_cls=processor_cls,
        )
        assert isinstance(connector, SnowflakeConnector)

    def test_indexing_filters_use_the_keys_the_code_reads(self) -> None:
        fields = SnowflakeConnector._connector_metadata["config"]["filters"]["indexing"]["schema"]["fields"]
        names = {field["name"] for field in fields}
        assert {
            IndexingFilterKey.TABLES.value, IndexingFilterKey.VIEWS.value, IndexingFilterKey.STAGE_FILES.value,
        } <= names

    def test_the_max_rows_filter_is_offered(self) -> None:
        fields = SnowflakeConnector._connector_metadata["config"]["filters"]["sync"]["schema"]["fields"]
        assert IndexingFilterKey.MAX_ROWS_PER_TABLE.value in {field["name"] for field in fields}

    def test_pat_then_key_pair_auth_is_offered(self) -> None:
        # PAT first: the default form, and what saved connectors carry.
        assert SnowflakeConnector._connector_metadata["supportedAuthTypes"] == ["ACCESS_KEY", "KEY_PAIR"]

    def test_both_forms_take_an_optional_role_and_the_key_is_a_secret_textarea(self) -> None:
        import json

        metadata = json.dumps(SnowflakeConnector._connector_metadata, default=str)
        assert "privateKey" in metadata and "TEXTAREA" in metadata and '"role"' in metadata

    def test_the_removed_cdc_and_checkpoint_code_is_gone(self) -> None:
        for name in (
            "_ensure_stream_exists", "_check_stream_has_changes", "_consume_stream_changes",
            "_save_checkpoint", "_load_checkpoint", "_run_incremental_sync_internal",
            "_process_deletions_batch", "_check_record_at_source", "_fetch_table_metadata",
        ):
            assert not hasattr(SnowflakeConnector, name), name


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


class TestInit:
    async def test_builds_the_client_runner_and_fetcher(self) -> None:
        connector = make_connector()
        with patch(
            "app.connectors.sources.snowflake.connector.load_connector_filters",
            new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection()),
        ):
            assert await connector.init() is True
        assert connector.warehouse == "WH"
        assert connector.runner.warehouse == "WH"
        assert connector.data_fetcher.runner is connector.runner
        assert connector.data_source.base_url == "https://acct.snowflakecomputing.com/api/v2"

    async def test_scope_and_creator_come_from_the_stored_config(self) -> None:
        connector = make_connector(scope=ConnectorScope.TEAM.value, created_by="ctor")
        with patch(
            "app.connectors.sources.snowflake.connector.load_connector_filters",
            new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection()),
        ):
            await connector.init()
        assert (connector.scope, connector.created_by) == (ConnectorScope.PERSONAL.value, "user-9")

    async def test_the_role_runs_every_statement(self) -> None:
        connector = make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"accountIdentifier": "acct", "warehouse": "WH", "patToken": "pat", "role": "LOADER"},
        })
        with patch(
            "app.connectors.sources.snowflake.connector.load_connector_filters",
            new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection()),
        ):
            assert await connector.init() is True
        assert connector.runner.role == "LOADER"

    async def test_a_bad_private_key_fails_init_without_logging_it(self, caplog) -> None:
        connector = make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "KEY_PAIR", "accountIdentifier": "acct", "warehouse": "WH", "user": "svc",
                     "privateKey": "-----BEGIN PRIVATE KEY-----\nnot-a-key\n-----END PRIVATE KEY-----"},
        })
        assert await connector.init() is False
        assert "not-a-key" not in caplog.text

    async def test_a_missing_warehouse_fails_init(self) -> None:
        connector = make_connector()
        connector.config_service.get_config = AsyncMock(
            return_value={"auth": {"accountIdentifier": "acct", "patToken": "pat"}}
        )
        assert await connector.init() is False
        assert connector.runner is None

    async def test_no_config_fails_init(self) -> None:
        connector = make_connector()
        connector.config_service.get_config = AsyncMock(return_value=None)
        assert await connector.init() is False


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class TestTableRecords:
    async def test_a_new_table_gets_a_new_id_and_its_metadata(self) -> None:
        connector = ready_connector()
        connector._frontend_url = "https://app"
        table = listed("ORDERS", "DB", "S", row_count=5, bytes=100, foreign_keys=[
            ForeignKey("FK", "DB", "S", "ORDERS", "CID", "OTHER", "CRM", "CUSTOMERS", "ID"),
        ])
        ((record, perms),) = await collect(connector._process_tables_generator("DB", "S", [table]))
        assert perms == []
        assert record.external_record_id == "DB.S.ORDERS"
        assert record.external_record_group_id == "DB.S"
        assert (record.database_name, record.schema_name, record.row_count) == ("DB", "S", 5)
        assert record.weburl == f"https://app/record/{record.id}"
        (fk,) = record.related_external_records
        assert fk.relation_type == RecordRelations.FOREIGN_KEY
        assert fk.external_record_id == "OTHER.CRM.CUSTOMERS"
        assert (fk.source_column, fk.target_column, fk.child_table_name) == ("CID", "ID", "DB.S.ORDERS")

    async def test_revision_follows_last_altered(self) -> None:
        connector = ready_connector()
        table = listed(
            "T", "DB", "S", created_at="2024-01-01T00:00:00+00:00", last_altered="2024-03-01T12:00:00+00:00",
        )
        ((record, _),) = await collect(connector._process_tables_generator("DB", "S", [table]))
        # The row limit is part of it: changing it changes the indexed text.
        assert record.external_revision_id == "2024-03-01T12:00:00+00:00:1000"
        assert record.source_created_at == 1704067200000
        assert record.source_updated_at == 1709294400000

    async def test_a_table_without_catalog_times_uses_now(self) -> None:
        connector = ready_connector()
        ((record, _),) = await collect(
            connector._process_tables_generator("DB", "S", [listed("T", "DB", "S")])
        )
        assert record.external_revision_id is None
        assert record.source_updated_at > 1704067200000

    async def test_a_stored_table_keeps_its_id_and_weburl(self) -> None:
        connector = ready_connector()
        connector._frontend_url = "https://app"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=SimpleNamespace(id="stored-id", source_created_at=111)
        )
        ((record, _),) = await collect(
            connector._process_tables_generator("DB", "S", [listed("T", "DB", "S")])
        )
        assert record.id == "stored-id"
        assert record.weburl == "https://app/record/stored-id"
        assert record.source_created_at == 111

    async def test_a_dotted_name_gets_a_quoted_id(self) -> None:
        connector = ready_connector()
        ((record, _),) = await collect(
            connector._process_tables_generator("DB", "my.schema", [listed("T", "DB", "my.schema")])
        )
        assert record.external_record_id == 'DB."my.schema".T'

    @pytest.mark.parametrize(
        ("key", "build"),
        [
            (IndexingFilterKey.TABLES.value, lambda c: c._process_tables_generator(
                "DB", "S", [listed("T", "DB", "S")])),
            (IndexingFilterKey.VIEWS.value, lambda c: c._process_views_generator(
                "DB", "S", [SnowflakeView("V", "DB", "S", definition="select 1")])),
            (IndexingFilterKey.STAGE_FILES.value, lambda c: c._process_stage_files_generator(
                "DB.S.ST", [SnowflakeFile("a.csv", "ST", "DB", "S")])),
        ],
    )
    async def test_indexing_toggle_off_marks_records_auto_index_off(self, key, build) -> None:
        connector = ready_connector()
        connector.indexing_filters = filters(toggle(key, False))
        ((record, _),) = await collect(build(connector))
        assert record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    async def test_indexing_toggle_on_leaves_status_alone(self) -> None:
        connector = ready_connector()
        connector.indexing_filters = filters(toggle(IndexingFilterKey.TABLES.value, True))
        ((record, _),) = await collect(
            connector._process_tables_generator("DB", "S", [listed("T", "DB", "S")])
        )
        assert record.indexing_status != ProgressStatus.AUTO_INDEX_OFF.value


class TestViewRecords:
    async def test_sources_come_from_the_catalog_definition(self) -> None:
        connector = ready_connector()
        view = SnowflakeView(
            "V", "DB", "S",
            definition='create view v as select * from orders o join "Crm"."Customers" c on o.id = c.id',
        )
        known = {"DB.S.ORDERS": RecordType.SQL_TABLE, "DB.Crm.Customers": RecordType.SQL_VIEW}
        ((record, _),) = await collect(connector._process_views_generator("DB", "S", [view], known))
        assert record.definition == view.definition
        assert record.source_tables == ["DB.S.ORDERS", "DB.Crm.Customers"]
        links = [(r.external_record_id, r.record_type, r.relation_type) for r in record.related_external_records]
        assert links == [
            ("DB.S.ORDERS", RecordType.SQL_TABLE, RecordRelations.DEPENDS_ON),
            ("DB.Crm.Customers", RecordType.SQL_VIEW, RecordRelations.DEPENDS_ON),
        ]
        connector.data_fetcher.get_ddl.assert_not_called()

    async def test_a_source_this_sync_did_not_read_gets_no_link(self) -> None:
        connector = ready_connector()
        view = SnowflakeView("V", "DB", "S", definition="select * from orders join elsewhere.t on 1=1")
        ((record, _),) = await collect(
            connector._process_views_generator("DB", "S", [view], {"DB.S.ORDERS": RecordType.SQL_TABLE})
        )
        assert [r.external_record_id for r in record.related_external_records] == ["DB.S.ORDERS"]
        assert record.source_tables == ["DB.S.ORDERS", "DB.ELSEWHERE.T"]

    async def test_revision_and_times_come_from_the_catalog(self) -> None:
        connector = ready_connector()
        view = SnowflakeView(
            "V", "DB", "S", definition="select 1",
            created_at="2024-01-01T00:00:00+00:00", last_altered="2024-02-01T00:00:00+00:00",
        )
        ((record, _),) = await collect(connector._process_views_generator("DB", "S", [view]))
        assert record.external_revision_id == "2024-02-01T00:00:00+00:00"
        assert record.source_created_at == 1704067200000
        assert record.source_updated_at == 1706745600000


class TestFileRecords:
    async def test_folders_are_skipped_and_files_keep_their_path(self) -> None:
        connector = ready_connector()
        files = [
            SnowflakeFile("dir/", "ST", "DB", "S"),
            SnowflakeFile("dir/report.pdf", "ST", "DB", "S", size=9, md5="m1"),
        ]
        ((record, _),) = await collect(connector._process_stage_files_generator("DB.S.ST", files))
        assert record.external_record_id == "DB.S.ST/dir/report.pdf"
        assert (record.path, record.record_name, record.etag) == ("dir/report.pdf", "report.pdf", "m1")
        assert record.external_revision_id == "m1"


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


def hierarchy() -> SnowflakeHierarchy:
    return SnowflakeHierarchy(
        databases=[SnowflakeDatabase("DB")],
        schemas={"DB": [SnowflakeSchema("S", "DB")]},
        tables={"DB.S": [listed("A", "DB", "S"), listed("x.y", "DB", "S")]},
        views={"DB.S": [SnowflakeView("V", "DB", "S", definition="select 1")]},
        stages={"DB.S": [SnowflakeStage("ST", "DB", "S")]},
        files={"DB.S.ST": [SnowflakeFile("a.csv", "ST", "DB", "S"), SnowflakeFile("b.csv", "ST", "DB", "S")]},
        errors=2,
    )


class TestSync:
    async def test_run_sync_saves_every_object_and_counts_errors(self) -> None:
        connector = ready_connector()
        connector.data_fetcher.fetch_all = AsyncMock(return_value=hierarchy())
        with patch(
            "app.connectors.sources.snowflake.connector.load_connector_filters",
            new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection()),
        ):
            await connector.run_sync()
        saved = [r for call in connector.data_entities_processor.on_new_records.await_args_list for r, _ in call.args[0]]
        assert sorted(r.external_record_id for r in saved) == sorted(
            ["DB.S.A", 'DB.S."x.y"', "DB.S.V", "DB.S.ST/a.csv", "DB.S.ST/b.csv"]
        )
        stats = connector.sync_stats
        assert (stats.tables_new, stats.views_new, stats.files_new, stats.errors) == (2, 1, 2, 2)

    async def test_run_incremental_sync_runs_a_full_sync(self) -> None:
        connector = ready_connector()
        connector.run_sync = AsyncMock()
        await connector.run_incremental_sync()
        connector.run_sync.assert_awaited_once()

    async def test_run_sync_without_init_raises(self) -> None:
        connector = make_connector()
        with pytest.raises(ConnectionError):
            await connector.run_sync()

    async def test_object_filters_match_quoted_ids(self) -> None:
        connector = ready_connector()
        connector.sync_filters = filters(
            multiselect("tables", ['DB.S."x.y"']),
            multiselect("files", ["DB.S.ST/a.csv"], operator="not_in"),
        )
        connector.data_fetcher.fetch_all = AsyncMock(return_value=hierarchy())
        await connector._sync_catalog()
        saved = {r.external_record_id for call in connector.data_entities_processor.on_new_records.await_args_list for r, _ in call.args[0]}
        assert 'DB.S."x.y"' in saved and "DB.S.A" not in saved
        assert "DB.S.ST/b.csv" in saved and "DB.S.ST/a.csv" not in saved

    async def test_database_and_schema_filters_reach_the_fetcher_as_predicates(self) -> None:
        connector = ready_connector()
        connector.sync_filters = filters(
            multiselect("databases", ["DB"]),
            multiselect("schemas", ["DB.SECRET"], operator="not_in"),
        )
        connector.data_fetcher.fetch_all = AsyncMock(return_value=SnowflakeHierarchy())
        await connector._sync_catalog()
        kwargs = connector.data_fetcher.fetch_all.await_args.kwargs
        assert kwargs["include_database"]("DB") and not kwargs["include_database"]("OTHER")
        assert kwargs["include_schema"]("DB.PUBLIC") and not kwargs["include_schema"]("DB.SECRET")

    async def test_team_scope_creates_the_team_edge(self) -> None:
        connector = ready_connector(scope=ConnectorScope.TEAM.value)
        await connector._ensure_scope_app_edges()
        connector.data_entities_processor.ensure_team_app_edge.assert_awaited_once_with("conn-1")

    async def test_personal_scope_creates_the_creators_edge(self) -> None:
        connector = ready_connector(scope=ConnectorScope.PERSONAL.value, created_by="u1")
        connector.data_entities_processor.get_user_by_user_id = AsyncMock(return_value=SimpleNamespace(
            source_user_id="s", id="u1", email="u@x.io", org_id="org-1", full_name="U", is_active=True, title=None,
        ))
        await connector._ensure_scope_app_edges()
        connector.data_entities_processor.on_new_app_users.assert_awaited_once()

    async def test_personal_scope_without_creator_only_warns(self) -> None:
        connector = ready_connector(scope=ConnectorScope.PERSONAL.value, created_by=None)
        await connector._ensure_scope_app_edges()
        connector.data_entities_processor.on_new_app_users.assert_not_awaited()

    async def test_a_team_connector_is_owned_by_the_org(self) -> None:
        (permission,) = await ready_connector()._get_permissions()
        assert (permission.type, permission.entity_type) == (PermissionType.OWNER, EntityType.ORG)

    async def test_a_personal_connector_is_owned_by_its_creator_only(self) -> None:
        connector = ready_connector(scope=ConnectorScope.PERSONAL.value, created_by="u1")
        connector.creator_email = "owner@example.com"
        (permission,) = await connector._get_permissions()
        assert (permission.entity_type, permission.email, permission.external_id) == (
            EntityType.USER, "owner@example.com", "u1")


# ---------------------------------------------------------------------------
# Column records and profiling
# ---------------------------------------------------------------------------


def profiled_table(**extra: Any) -> SnowflakeTable:  # noqa: ANN401
    fields = {"row_count": 100, "bytes": 4096, "table_type": "BASE TABLE", "last_altered": "2024-03-01T00:00:00+00:00"}
    fields.update(extra)
    table = listed("ORDERS", "DB", "S", **fields)
    table.columns = [
        SnowflakeColumnInfo(name="ID", ordinal_position=1, data_type="NUMBER", nullable=False,
                            is_primary_key=True, is_unique=True),
        SnowflakeColumnInfo(name="STATUS", ordinal_position=2, data_type="TEXT"),
    ]
    table.columns_read = table.keys_read = table.foreign_keys_read = True
    return table


# 2024-03-01T01:00:00Z: an hour after profiled_table()'s LAST_ALTERED.
PROFILED_AFTER_CHANGE = 1709254800000


def stored_column(name: str, **extra: Any) -> SimpleNamespace:  # noqa: ANN401
    fields = {
        "id": f"stored-{name}", "external_record_id": f"DB.S.ORDERS.{name}", "distinct_count": 7, "null_count": 0,
        "cardinality_ratio": 0.07, "profiled_at": PROFILED_AFTER_CHANGE, "top_values": None, "top_value_frequencies": None,
        "min_value": None, "max_value": None, "numeric_text": None,
    }
    fields.update(extra)
    return SimpleNamespace(**fields)


def profiling_connector(stored: dict | None = None, existing_revision: str | None = None) -> SnowflakeConnector:
    connector = ready_connector()
    connector._stored_columns = AsyncMock(return_value=stored or {})
    if existing_revision is not None:
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=SimpleNamespace(
            id="table-1", source_created_at=1, external_revision_id=existing_revision,
        ))
    connector.profiler = MagicMock()
    connector.profiler.profile_table = AsyncMock(return_value=TableProfile(
        row_count=100,
        stats={"ID": {"name": "ID", "ordinal_position": 0, "distinct_count": 100, "null_count": 0,
                      "cardinality_ratio": 1.0},
               "STATUS": {"name": "STATUS", "ordinal_position": 1, "distinct_count": 3, "null_count": 0,
                          "cardinality_ratio": 0.03}},
        profiles={"STATUS": ColumnProfile(top_values=["OPEN"], top_value_frequencies=[1.0], profiled_at=9)},
    ))
    return connector


async def records_of(connector, *tables) -> list:
    return [r for r, _ in await collect(connector._process_tables_generator("DB", "S", list(tables)))]


class TestColumnRecords:
    async def test_a_table_yields_its_record_then_its_column_records(self) -> None:
        connector = profiling_connector()
        table_record, *columns = await records_of(connector, profiled_table())
        assert table_record.fqn == table_record.external_record_id == "DB.S.ORDERS"
        assert [(c.external_record_id, c.ordinal_position) for c in columns] == [
            ("DB.S.ORDERS.ID", 0), ("DB.S.ORDERS.STATUS", 1),
        ]
        id_column, status = columns
        assert (id_column.is_primary_key, id_column.distinct_count) == (True, 100)
        assert (status.top_values, status.profiled_at) == (["OPEN"], 9)
        assert all(c.parent_node_id == table_record.id for c in columns)

    async def test_a_dotted_table_builds_its_column_ids_from_its_fqn(self) -> None:
        connector = profiling_connector()
        table = profiled_table()
        table.schema_name = "a.b"
        table_record, *columns = await records_of(connector, table)
        assert table_record.fqn == 'DB."a.b".ORDERS'
        assert [c.external_record_id for c in columns] == ['DB."a.b".ORDERS.ID', 'DB."a.b".ORDERS.STATUS']

    @pytest.mark.parametrize(("columns_read", "keys_read"), [(False, True), (True, False)])
    async def test_unread_columns_or_keys_touch_no_column_record(self, columns_read, keys_read) -> None:
        connector = profiling_connector(stored={"DB.S.ORDERS.GONE": stored_column("GONE")})
        table = profiled_table()
        table.columns_read, table.keys_read = columns_read, keys_read
        records = await records_of(connector, table)
        # Not written at all: without its columns or keys it would lose them.
        assert records == []
        assert connector._dropped_columns == []
        connector.profiler.profile_table.assert_not_awaited()

    async def test_an_unreadable_stored_column_lookup_touches_no_column_record(self) -> None:
        connector = profiling_connector()
        connector._stored_columns = AsyncMock(side_effect=RuntimeError("graph down"))
        records = await records_of(connector, profiled_table())
        assert len(records) == 1 and connector._dropped_columns == []

    async def test_dropped_columns_are_removed_after_the_tables_are_saved(self) -> None:
        connector = profiling_connector(stored={"DB.S.ORDERS.GONE": stored_column("GONE")})
        connector.data_entities_processor.on_sql_records_deleted = AsyncMock(return_value=["stored-GONE"])
        await connector._sync_tables("DB", "S", [profiled_table()])
        connector.data_entities_processor.on_sql_records_deleted.assert_awaited_once()
        args, kwargs = connector.data_entities_processor.on_sql_records_deleted.await_args
        assert args == (["stored-GONE"], "conn-1")
        assert kwargs["table_record_id"]
        assert connector._dropped_columns == []

    async def test_a_failed_removal_is_counted_not_raised(self) -> None:
        connector = profiling_connector(stored={"DB.S.ORDERS.GONE": stored_column("GONE")})
        connector.data_entities_processor.on_sql_records_deleted = AsyncMock(side_effect=RuntimeError("no"))
        await connector._sync_tables("DB", "S", [profiled_table()])
        assert connector.sync_stats.errors == 1


class TestStoredColumns:
    @staticmethod
    def connector_with(base_records, typed) -> SnowflakeConnector:
        connector = ready_connector()
        del connector._stored_columns  # the real lookup
        tx_store = MagicMock()
        tx_store.get_records_by_parent = AsyncMock(return_value=base_records)
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=tx_store)
        context.__aexit__ = AsyncMock(return_value=False)
        connector.data_store_provider.transaction = MagicMock(return_value=context)
        connector.data_entities_processor.get_typed_records_by_ids = AsyncMock(return_value=typed)
        return connector

    async def test_stored_columns_are_loaded_typed_with_their_profiles(self) -> None:
        base = SimpleNamespace(id="c1", external_record_id="DB.S.T.A")
        typed = SimpleNamespace(id="c1", external_record_id="DB.S.T.A", profiled_at=5, distinct_count=3)
        connector = self.connector_with([base], {"c1": typed})
        stored = await connector._stored_columns("DB.S.T")
        assert stored == {"DB.S.T.A": typed}
        connector.data_entities_processor.get_typed_records_by_ids.assert_awaited_once_with(
            ["c1"], raise_on_error=True,
        )

    async def test_a_column_that_cannot_be_loaded_typed_fails_the_lookup(self) -> None:
        base = [SimpleNamespace(id="c1", external_record_id="DB.S.T.A"),
                SimpleNamespace(id="c2", external_record_id="DB.S.T.B")]
        connector = self.connector_with(base, {"c1": SimpleNamespace(id="c1")})
        with pytest.raises(LookupError):
            await connector._stored_columns("DB.S.T")

    async def test_a_failed_typed_lookup_leaves_the_table_columns_alone(self) -> None:
        base = [SimpleNamespace(id="c1", external_record_id="DB.S.ORDERS.GONE")]
        connector = self.connector_with(base, {})
        connector.profiler = MagicMock()
        connector.profiler.profile_table = AsyncMock()
        records = await records_of(connector, profiled_table())
        assert len(records) == 1 and connector._dropped_columns == []
        connector.profiler.profile_table.assert_not_awaited()


class TestProfilingDecisions:
    async def test_an_unchanged_table_is_not_scanned_and_keeps_its_profile(self) -> None:
        stored = {f"DB.S.ORDERS.{n}": stored_column(n) for n in ("ID", "STATUS")}
        connector = profiling_connector(stored=stored, existing_revision="2024-03-01T00:00:00+00:00:1000")
        _, id_column, status = await records_of(connector, profiled_table())
        connector.profiler.profile_table.assert_not_awaited()
        assert (id_column.distinct_count, status.profiled_at) == (7, PROFILED_AFTER_CHANGE)

    async def test_a_table_changed_since_its_profile_is_scanned(self) -> None:
        stored = {f"DB.S.ORDERS.{n}": stored_column(n) for n in ("ID", "STATUS")}
        connector = profiling_connector(stored=stored)
        await records_of(connector, profiled_table(last_altered="2024-03-02T00:00:00+00:00"))
        connector.profiler.profile_table.assert_awaited_once()

    async def test_a_profile_taken_moments_after_a_change_is_not_trusted(self) -> None:
        # Within the freshness margin a change that landed during the scan could be missed.
        stored = {f"DB.S.ORDERS.{n}": stored_column(n, profiled_at=1709251260000) for n in ("ID", "STATUS")}
        connector = profiling_connector(stored=stored)
        await records_of(connector, profiled_table())
        connector.profiler.profile_table.assert_awaited_once()

    async def test_a_scan_that_failed_last_sync_is_retried_although_nothing_changed(self) -> None:
        # The failed scan kept the profile taken before the change.
        stored = {f"DB.S.ORDERS.{n}": stored_column(n, profiled_at=1700000000000) for n in ("ID", "STATUS")}
        connector = profiling_connector(stored=stored)
        await records_of(connector, profiled_table())
        connector.profiler.profile_table.assert_awaited_once()

    async def test_never_profiled_columns_are_scanned(self) -> None:
        stored = {f"DB.S.ORDERS.{n}": stored_column(n, profiled_at=None) for n in ("ID", "STATUS")}
        connector = profiling_connector(stored=stored)
        await records_of(connector, profiled_table())
        connector.profiler.profile_table.assert_awaited_once()

    async def test_a_new_column_means_a_scan(self) -> None:
        stored = {"DB.S.ORDERS.ID": stored_column("ID")}
        connector = profiling_connector(stored=stored, existing_revision="2024-03-01T00:00:00+00:00:1000")
        await records_of(connector, profiled_table())
        connector.profiler.profile_table.assert_awaited_once()

    async def test_indexing_off_never_writes_values_even_when_keeping_the_stored_profile(self) -> None:
        stored = {
            f"DB.S.ORDERS.{n}": stored_column(n, top_values=["OPEN"], top_value_frequencies=[1.0],
                                              min_value="1", max_value="9", numeric_text=False)
            for n in ("ID", "STATUS")
        }
        connector = profiling_connector(stored=stored)
        connector.indexing_filters = filters(toggle(IndexingFilterKey.TABLES.value, False))
        _, id_column, status = await records_of(connector, profiled_table(bytes=PROFILE_MAX_BYTES + 1))
        connector.profiler.profile_table.assert_not_awaited()
        for column in (id_column, status):
            assert (column.top_values, column.min_value, column.max_value) == (None, None, None)
            assert column.distinct_count == 7 and column.numeric_text is False

    async def test_indexing_turned_off_clears_stored_values_with_a_scan(self) -> None:
        stored = {f"DB.S.ORDERS.{n}": stored_column(n, top_values=["OPEN"]) for n in ("ID", "STATUS")}
        connector = profiling_connector(stored=stored, existing_revision="2024-03-01T00:00:00+00:00:1000")
        connector.indexing_filters = filters(toggle(IndexingFilterKey.TABLES.value, False))
        await records_of(connector, profiled_table())
        assert connector.profiler.profile_table.await_args.kwargs["with_values"] is False

    async def test_an_empty_table_gets_empty_profiles_without_a_scan(self) -> None:
        connector = profiling_connector()
        _, id_column, status = await records_of(connector, profiled_table(row_count=0))
        connector.profiler.profile_table.assert_not_awaited()
        assert (id_column.distinct_count, id_column.null_count) == (0, 0)
        assert status.profiled_at is not None and status.top_values is None

    @pytest.mark.parametrize("extra", [
        {"row_count": None}, {"table_type": "EXTERNAL TABLE"}, {"bytes": PROFILE_MAX_BYTES + 1},
    ])
    async def test_unknown_external_or_huge_tables_keep_their_stored_profile(self, extra) -> None:
        stored = {"DB.S.ORDERS.STATUS": stored_column("STATUS", top_values=["X"], top_value_frequencies=[1.0])}
        connector = profiling_connector(stored=stored)
        _, _, status = await records_of(connector, profiled_table(**extra))
        connector.profiler.profile_table.assert_not_awaited()
        assert status.top_values == ["X"]

    async def test_a_failed_scan_keeps_the_stored_profile(self) -> None:
        stored = {"DB.S.ORDERS.STATUS": stored_column("STATUS", top_values=["X"], top_value_frequencies=[1.0])}
        connector = profiling_connector(stored=stored)
        connector.profiler.profile_table = AsyncMock(side_effect=SnowflakeFetchError("no", sqlstate="42501"))
        _, _, status = await records_of(connector, profiled_table())
        assert status.top_values == ["X"]
        assert connector.sync_stats.errors == 1

    async def test_timeouts_do_not_stop_profiling(self) -> None:
        connector = profiling_connector()
        connector.profiler.profile_table = AsyncMock(
            side_effect=SnowflakeFetchError("The Snowflake statement timed out", sqlstate="57014")
        )
        tables = [profiled_table() for _ in range(5)]
        for i, table in enumerate(tables):
            table.name = f"T{i}"
        await records_of(connector, *tables)
        assert connector.profiler.profile_table.await_count == 5

    async def test_a_suspended_warehouse_stops_profiling_for_the_sync(self) -> None:
        connector = profiling_connector()
        connector.profiler.profile_table = AsyncMock(
            side_effect=SnowflakeFetchError("Warehouse suspended", sqlstate="57P03")
        )
        tables = [profiled_table() for _ in range(5)]
        for i, table in enumerate(tables):
            table.name = f"T{i}"
        await records_of(connector, *tables)
        assert connector.profiler.profile_table.await_count == 3

    async def test_only_filtered_tables_are_profiled(self) -> None:
        connector = profiling_connector()
        connector.sync_filters = filters(multiselect("tables", ["DB.S.KEEP"]))
        keep, skip = profiled_table(), profiled_table()
        keep.name, skip.name = "KEEP", "SKIP"
        hierarchy = SnowflakeHierarchy(tables={"DB.S": [keep, skip]})
        await connector._sync_schema_objects(hierarchy, "DB", "S")
        (call,) = connector.profiler.profile_table.await_args_list
        assert call.args[2] == "KEEP"


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


async def body_of(response) -> dict:
    chunks = [chunk async for chunk in response.body_iterator]
    return json.loads(b"".join(chunks))


def record(record_type: RecordType, external_id: str, **extra: Any) -> SimpleNamespace:  # noqa: ANN401
    return SimpleNamespace(
        id="r1", record_type=record_type, external_record_id=external_id,
        record_name=extra.pop("record_name", "name"), mime_type="text/csv", **extra,
    )


class TestStreamTable:
    @staticmethod
    def table_connector(ddl: Any = "create table t") -> SnowflakeConnector:  # noqa: ANN401
        connector = ready_connector()
        fetcher = connector.data_fetcher
        fetcher.columns_by_table = AsyncMock(return_value={"x.y": [
            SnowflakeColumnInfo(name="ID", ordinal_position=1, data_type="NUMBER", nullable=False),
            SnowflakeColumnInfo(name="CID", ordinal_position=2, data_type="NUMBER"),
        ]})
        fetcher.key_definitions = AsyncMock(return_value={"x.y": [KeyDefinition("primary", ("ID",))]})
        fetcher.foreign_keys = AsyncMock(return_value=[
            ForeignKey("FK", "DB", "S", "x.y", "CID", "DB", "CRM", "CUSTOMERS", "ID"),
        ])
        fetcher.sample_rows = AsyncMock(return_value=(["ID", "CID"], [[1, 7], [2, None]]))
        if isinstance(ddl, Exception):
            fetcher.get_table_ddl = AsyncMock(side_effect=ddl)
        else:
            fetcher.get_table_ddl = AsyncMock(return_value=ddl)
        return connector

    async def stream(self, connector, row_count: int | None = 10, max_rows: int | None = None) -> object:
        sync_filters = filters(Filter(key="max_rows_per_table", value=max_rows, type="number", operator="less_than_or_equal")) \
            if max_rows is not None else FilterCollection()
        with patch(
            "app.connectors.sources.snowflake.connector.load_connector_filters",
            new_callable=AsyncMock, return_value=(sync_filters, FilterCollection()),
        ):
            response = await connector.stream_record(record(RecordType.SQL_TABLE, 'DB.S."x.y"', row_count=row_count))
        return await body_of(response)

    async def test_a_dotted_table_is_split_and_its_sample_ordered_by_key(self) -> None:
        connector = self.table_connector()
        data = await self.stream(connector)
        connector.data_fetcher.columns_by_table.assert_awaited_once_with("DB", "S", "x.y")
        kwargs = connector.data_fetcher.sample_rows.await_args.kwargs
        assert connector.data_fetcher.sample_rows.await_args.args == ("DB", "S", "x.y")
        assert kwargs == {"limit": 1000, "order_by": ["ID"], "max_bytes": MAX_TABLE_DOCUMENT_BYTES}
        assert (data["table_name"], data["schema_name"], data["database_name"]) == ("x.y", "S", "DB")
        assert data["rows"] == [{"ID": 1, "CID": 7}, {"ID": 2, "CID": None}]
        assert data["primary_keys"] == ["ID"]
        assert data["ddl"] == "create table t"

    async def test_columns_carry_their_key_flags(self) -> None:
        data = await self.stream(self.table_connector())
        id_column, cid_column = data["columns"]
        assert (id_column["name"], id_column["is_primary_key"], id_column["is_unique"]) == ("ID", True, True)
        assert cid_column["is_primary_key"] is False

    async def test_the_max_rows_filter_limits_the_sample(self) -> None:
        connector = self.table_connector()
        await self.stream(connector, max_rows=50_000)
        assert connector.data_fetcher.sample_rows.await_args.kwargs["limit"] == 10_000

    @pytest.mark.parametrize("row_count", [ORDERED_READ_MAX_ROWS + 1, None])
    async def test_a_large_or_unknown_table_is_sampled_unordered(self, row_count) -> None:
        connector = self.table_connector()
        await self.stream(connector, row_count=row_count)
        assert connector.data_fetcher.sample_rows.await_args.kwargs["order_by"] is None

    async def test_a_table_without_a_key_is_ordered_by_row_hash(self) -> None:
        connector = self.table_connector()
        connector.data_fetcher.key_definitions = AsyncMock(return_value={})
        await self.stream(connector)
        assert connector.data_fetcher.sample_rows.await_args.kwargs["order_by"] == []

    async def test_foreign_keys_use_the_parser_field_names(self) -> None:
        from app.modules.parsers.sql.sql_table_parser import SQLTableInput

        parsed = SQLTableInput.model_validate(await self.stream(self.table_connector()))
        (fk,) = parsed.foreign_keys
        assert (fk.column_name, fk.foreign_table_name, fk.foreign_column_name) == ("CID", "CUSTOMERS", "ID")
        assert fk.reference_target == "CRM.CUSTOMERS"
        assert [c.name for c in parsed.columns] == ["ID", "CID"]

    async def test_a_refused_ddl_is_tolerated_when_columns_are_known(self) -> None:
        connector = self.table_connector(ddl=SnowflakeFetchError("no", sqlstate="42501"))
        assert (await self.stream(connector))["ddl"] is None

    async def test_a_refused_ddl_with_nothing_else_raises(self) -> None:
        connector = self.table_connector(ddl=SnowflakeFetchError("no", sqlstate="42501"))
        connector.data_fetcher.columns_by_table = AsyncMock(return_value={})
        connector.data_fetcher.sample_rows = AsyncMock(return_value=([], []))
        with pytest.raises(HTTPException):
            await self.stream(connector)

    async def test_a_malformed_id_is_a_500(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            await ready_connector().stream_record(record(RecordType.SQL_TABLE, "DB.only"))
        assert excinfo.value.status_code == 500

    async def test_unsupported_record_type_is_a_400(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            await ready_connector().stream_record(record(RecordType.WEBPAGE, "x"))
        assert excinfo.value.status_code == 400

    async def test_not_initialised_is_reported(self) -> None:
        with pytest.raises(HTTPException):
            await make_connector().stream_record(record(RecordType.SQL_TABLE, "DB.S.T"))


class TestStreamColumn:
    @staticmethod
    def column_record(**extra: Any) -> SimpleNamespace:  # noqa: ANN401
        fields = {
            "table_fqn": 'DB.S."x.y"', "parent_external_record_id": 'DB.S."x.y"', "record_name": "STATUS",
            "data_type": "TEXT", "nullable": True, "is_primary_key": False, "is_unique": False, "default": None,
            "ordinal_position": 1, "distinct_count": 3, "null_count": 0, "cardinality_ratio": 0.03,
            "min_value": None, "max_value": None, "numeric_text": False, "profiled_at": 5,
            "top_values": ["OPEN", "CLOSED"], "top_value_frequencies": [0.6, 0.4],
        }
        fields.update(extra)
        return record(RecordType.SQL_COLUMN, 'DB.S."x.y".STATUS', **fields)

    @staticmethod
    def column_connector(row_count: int | None) -> SnowflakeConnector:
        connector = ready_connector()
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=SimpleNamespace(row_count=row_count)
        )
        connector.data_fetcher.column_value_counts = AsyncMock(return_value=[{"value": "OPEN", "count": 60}])
        return connector

    async def test_streams_the_profile_and_exact_counts_for_a_small_table(self) -> None:
        connector = self.column_connector(row_count=100)
        data = await body_of(await connector.stream_record(self.column_record()))
        connector.data_fetcher.column_value_counts.assert_awaited_once_with(
            "DB", "S", "x.y", "STATUS", limit=MAX_COLUMN_VALUES_STREAMED + 1, approximate=False,
        )
        assert data["value_counts"] == [{"value": "OPEN", "count": 60}]
        assert data["value_counts_approximate"] is False and data["value_counts_truncated"] is False
        assert data["profile"]["top_values"] == [
            {"value": "OPEN", "frequency": 0.6}, {"value": "CLOSED", "frequency": 0.4},
        ]
        assert (data["table_name"], data["database_name"]) == ("x.y", "DB")

    @pytest.mark.parametrize("row_count", [ORDERED_READ_MAX_ROWS + 1, None])
    async def test_a_large_or_unknown_table_gets_approximate_counts(self, row_count) -> None:
        connector = self.column_connector(row_count=row_count)
        data = await body_of(await connector.stream_record(self.column_record()))
        assert connector.data_fetcher.column_value_counts.await_args.kwargs["approximate"] is True
        assert data["value_counts_approximate"] is True

    @pytest.mark.parametrize(("read", "truncated"), [
        (MAX_COLUMN_VALUES_STREAMED, False), (MAX_COLUMN_VALUES_STREAMED + 1, True),
    ])
    async def test_counts_are_truncated_only_when_a_value_was_cut_off(self, read, truncated) -> None:
        connector = self.column_connector(row_count=100)
        connector.data_fetcher.column_value_counts = AsyncMock(
            return_value=[{"value": str(i), "count": 1} for i in range(read)]
        )
        data = await body_of(await connector.stream_record(self.column_record()))
        assert data["value_counts_truncated"] is truncated
        assert len(data["value_counts"]) == MAX_COLUMN_VALUES_STREAMED

    async def test_a_column_that_looks_personal_streams_no_counts(self) -> None:
        connector = self.column_connector(row_count=100)
        data = await body_of(await connector.stream_record(self.column_record(record_name="EMAIL")))
        connector.data_fetcher.column_value_counts.assert_not_awaited()
        assert data["value_counts"] == [] and data["value_counts_withheld"] is True

    async def test_an_uncountable_type_is_a_400(self) -> None:
        connector = self.column_connector(row_count=100)
        with pytest.raises(HTTPException) as excinfo:
            await connector.stream_record(self.column_record(data_type="VARIANT"))
        assert excinfo.value.status_code == 400
        connector.data_fetcher.column_value_counts.assert_not_awaited()

    async def test_a_refused_count_is_mapped(self) -> None:
        connector = self.column_connector(row_count=100)
        connector.data_fetcher.column_value_counts = AsyncMock(
            side_effect=SnowflakeFetchError("timed out", sqlstate="57014")
        )
        with pytest.raises(HTTPException) as excinfo:
            await connector.stream_record(self.column_record())
        assert excinfo.value.status_code != 500


class TestStreamView:
    @staticmethod
    def view_connector(view: SnowflakeView | None, ddl: str | None = None) -> SnowflakeConnector:
        connector = ready_connector()
        connector.data_fetcher.get_view = AsyncMock(return_value=view)
        connector.data_fetcher.get_ddl = AsyncMock(return_value=ddl)
        connector.data_fetcher.get_table_ddl = AsyncMock(return_value="create table orders")
        return connector

    async def test_streams_the_catalog_definition_and_source_ddls(self) -> None:
        view = SnowflakeView("V", "DB", "S", definition="select * from orders", comment="c")
        connector = self.view_connector(view)
        data = await body_of(await connector.stream_record(record(RecordType.SQL_VIEW, "DB.S.V")))
        assert data["definition"] == "select * from orders"
        assert data["source_tables"] == ["DB.S.ORDERS"]
        assert data["source_table_ddls"] == {"DB.S.ORDERS": "create table orders"}
        connector.data_fetcher.get_ddl.assert_not_awaited()

    async def test_falls_back_to_get_ddl(self) -> None:
        view = SnowflakeView("V", "DB", "S", definition=None)
        connector = self.view_connector(view, ddl="create view v as select 1")
        data = await body_of(await connector.stream_record(record(RecordType.SQL_VIEW, "DB.S.V")))
        assert data["definition"] == "create view v as select 1"

    async def test_a_secure_view_without_a_body_is_forbidden(self) -> None:
        connector = self.view_connector(SnowflakeView("V", "DB", "S", is_secure=True))
        with pytest.raises(HTTPException) as excinfo:
            await connector.stream_record(record(RecordType.SQL_VIEW, "DB.S.V"))
        assert excinfo.value.status_code == 403

    async def test_a_missing_view_is_not_found(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            await self.view_connector(None).stream_record(record(RecordType.SQL_VIEW, "DB.S.V"))
        assert excinfo.value.status_code == 404


class TestStreamFile:
    async def test_streams_through_a_presigned_url(self) -> None:
        connector = ready_connector()
        connector.data_fetcher.presigned_url = AsyncMock(return_value="https://signed")
        file = record(
            RecordType.FILE, "DB.S.ST/dir/a.csv", external_record_group_id="DB.S.ST", path="dir/a.csv",
            record_name="a.csv",
        )
        with patch("app.connectors.sources.snowflake.connector.stream_content", return_value=iter([])) as stream:
            await connector.stream_record(file)
        connector.data_fetcher.presigned_url.assert_awaited_once_with("DB", "S", "ST", "dir/a.csv")
        assert stream.call_args.args[0] == "https://signed"

    async def test_no_url_is_not_found(self) -> None:
        connector = ready_connector()
        connector.data_fetcher.presigned_url = AsyncMock(return_value=None)
        file = record(RecordType.FILE, "DB.S.ST/a.csv", external_record_group_id="DB.S.ST", path="a.csv")
        with pytest.raises(HTTPException) as excinfo:
            await connector.stream_record(file)
        assert excinfo.value.status_code == 404


class TestStreamErrors:
    def test_a_client_timeout_is_a_try_again_later_error(self) -> None:
        error = SnowflakeFetchError("The Snowflake statement timed out", sqlstate="57014")
        mapped = to_snowflake_stream_error(error, connector="Snowflake")
        assert mapped.status_code == map_source_status(503, connector="Snowflake").status_code
        assert "try again" in mapped.detail

    async def test_a_refused_statement_is_mapped_not_leaked(self) -> None:
        connector = ready_connector()
        connector.data_fetcher.presigned_url = AsyncMock(
            side_effect=SnowflakeFetchError("acct.snowflakecomputing.com says no", sqlstate="42501")
        )
        file = record(RecordType.FILE, "DB.S.ST/a.csv", external_record_group_id="DB.S.ST", path="a.csv")
        with pytest.raises(HTTPException) as excinfo:
            await connector.stream_record(file)
        assert "snowflakecomputing" not in str(excinfo.value.detail)


# ---------------------------------------------------------------------------
# Filter options
# ---------------------------------------------------------------------------


class TestFilterOptions:
    @staticmethod
    def options_connector() -> SnowflakeConnector:
        connector = ready_connector()
        connector.data_fetcher.list_databases = AsyncMock(return_value=[
            SnowflakeDatabase("DB"), SnowflakeDatabase("OTHER"),
        ])
        connector.data_fetcher.object_names = AsyncMock(side_effect=lambda db, kind: [
            f"{db}.S.{kind[:-1].upper()}1", f'{db}."a.b".{kind[:-1].upper()}2',
        ])
        return connector

    async def test_view_options_follow_the_configured_databases(self) -> None:
        connector = self.options_connector()
        connector.sync_filters = filters(multiselect("databases", ["OTHER"]))
        response = await connector.get_filter_options("views")
        assert [o.id for o in response.options] == ["OTHER.S.VIEW1", 'OTHER."a.b".VIEW2']
        assert [c.args for c in connector.data_fetcher.object_names.await_args_list] == [("OTHER", "views")]

    async def test_schema_filter_applies_to_dotted_schemas(self) -> None:
        connector = self.options_connector()
        connector.sync_filters = filters(
            multiselect("databases", ["DB"]), multiselect("schemas", ['DB."a.b"']),
        )
        response = await connector.get_filter_options("tables")
        assert [o.id for o in response.options] == ['DB."a.b".TABLE2']

    async def test_paging_and_search(self) -> None:
        connector = self.options_connector()
        page1 = await connector.get_filter_options("stages", page=1, limit=3)
        assert len(page1.options) == 3 and page1.has_more and page1.cursor == "2"
        page2 = await connector.get_filter_options("stages", cursor="2", limit=3)
        assert len(page2.options) == 1 and not page2.has_more
        found = await connector.get_filter_options("stages", search="other")
        assert all("OTHER" in o.id for o in found.options) and len(found.options) == 2

    async def test_one_unreadable_database_leaves_the_others(self) -> None:
        connector = self.options_connector()

        async def names(db, kind) -> list[str]:
            if db == "DB":
                raise SnowflakeFetchError("Information schema query returned too much data")
            return [f"{db}.S.T1"]

        connector.data_fetcher.object_names = AsyncMock(side_effect=names)
        response = await connector.get_filter_options("tables")
        assert response.success is True
        assert [o.id for o in response.options] == ["OTHER.S.T1"]

    async def test_database_options(self) -> None:
        response = await self.options_connector().get_filter_options("databases")
        assert [o.id for o in response.options] == ["DB", "OTHER"]

    async def test_schema_options(self) -> None:
        connector = self.options_connector()
        connector.data_fetcher.fetch_all = AsyncMock(return_value=SnowflakeHierarchy(
            databases=[SnowflakeDatabase("DB")], schemas={"DB": [SnowflakeSchema("S", "DB")]},
        ))
        response = await connector.get_filter_options("schemas")
        assert [o.id for o in response.options] == ["DB.S"]
        assert connector.data_fetcher.fetch_all.await_args.kwargs["include_objects"] is False

    async def test_file_options_skip_folders_and_other_stages(self) -> None:
        connector = self.options_connector()
        connector.sync_filters = filters(multiselect("stages", ["DB.S.ST"]))
        connector.data_fetcher.fetch_all = AsyncMock(return_value=SnowflakeHierarchy(files={
            "DB.S.ST": [SnowflakeFile("d/", "ST", "DB", "S"), SnowflakeFile("d/a.csv", "ST", "DB", "S")],
            "DB.S.OTHER": [SnowflakeFile("b.csv", "OTHER", "DB", "S")],
        }))
        response = await connector.get_filter_options("files")
        assert [o.id for o in response.options] == ["DB.S.ST/d/a.csv"]

    async def test_unknown_key(self) -> None:
        response = await self.options_connector().get_filter_options("nope")
        assert response.success is False and "Unknown filter key" in response.message

    async def test_not_initialised(self) -> None:
        response = await make_connector().get_filter_options("tables")
        assert response.success is False

    async def test_a_failure_is_reported(self) -> None:
        connector = self.options_connector()
        connector.data_fetcher.list_databases = AsyncMock(side_effect=SnowflakeFetchError("no"))
        response = await connector.get_filter_options("databases")
        assert response.success is False


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_cleanup_closes_the_http_client(self) -> None:
        connector = ready_connector()
        connector.data_source = MagicMock()
        connector.data_source.http.close = AsyncMock()
        http = connector.data_source.http
        await connector.cleanup()
        http.close.assert_awaited_once()
        assert connector.data_source is None and connector.runner is None

    async def test_reindex_publishes_every_record(self) -> None:
        connector = ready_connector()
        records = [MagicMock(), MagicMock()]
        await connector.reindex_records(records)
        connector.data_entities_processor.reindex_existing_records.assert_awaited_once_with(records)

    async def test_reindex_without_init_raises(self) -> None:
        with pytest.raises(Exception, match="not initialized"):
            await make_connector().reindex_records([MagicMock()])

    async def test_connection_test(self) -> None:
        connector = ready_connector()
        assert await connector.test_connection_and_access() is True
        connector.runner.run = AsyncMock(side_effect=SnowflakeFetchError("no"))
        assert await connector.test_connection_and_access() is False

    def test_webhooks_are_not_supported(self) -> None:
        with pytest.raises(NotImplementedError):
            ready_connector().handle_webhook_notification({})

    def test_app_users_skip_missing_email(self) -> None:
        users = [
            SimpleNamespace(source_user_id=None, id="u1", email="a@x.io", org_id=None,
                            full_name=None, is_active=None, title=None),
            SimpleNamespace(source_user_id=None, id="u2", email=None, org_id=None,
                            full_name=None, is_active=None, title=None),
        ]
        (app_user,) = ready_connector().get_app_users(users)
        assert (app_user.email, app_user.org_id, app_user.is_active) == ("a@x.io", "org-1", True)


# ---------------------------------------------------------------------------
# View source parsing
# ---------------------------------------------------------------------------


class TestParseViewSources:
    @pytest.mark.parametrize(
        ("sql", "expected"),
        [
            ("select * from orders", [("ORDERS",)]),
            ("select * from sales.orders o join crm.customers c on 1=1", [("SALES", "ORDERS"), ("CRM", "CUSTOMERS")]),
            ('select * from "My DB"."Raw"."Orders"', [("My DB", "Raw", "Orders")]),
            ('select * from "we""ird"', [('we"ird',)]),
            ("with recent as (select * from orders) select * from recent", [("ORDERS",)]),
            ("with a as (select 1), b as (select * from t) select * from a join b on 1=1", [("T",)]),
            ("select * from table(flatten(input => x))", []),
            ("select * from db.sch.my_udtf(1)", []),
            ("select * from tabled", [("TABLED",)]),
            ("select * from (select * from inner_t)", [("INNER_T",)]),
            ("select * from orders join orders on 1=1", [("ORDERS",)]),
            ("select extract(year from created_at), trim(both from name) from t", [("T",)]),
            ("select substring(x from 2 for 3), position('a' in y) from t", [("T",)]),
            ("select * from t where a is distinct from b", [("T",)]),
            ("select 'text from foo' as x from t", [("T",)]),
            ("select $$ from bar $$ from t", [("T",)]),
            ("select 1 from t -- from commented\n", [("T",)]),
            ("select 1 /* join hidden */ from t", [("T",)]),
            ('select * from "weird from".t', [("weird from", "T")]),
            ("select 'it''s from x' from t", [("T",)]),
            ("", []),
            (None, []),
        ],
    )
    def test_parse(self, sql, expected) -> None:
        assert parse_view_sources(sql) == expected

    @pytest.mark.parametrize(
        ("parts", "expected"),
        [
            (("T",), ("DB", "S", "T")),
            (("X", "T"), ("DB", "X", "T")),
            (("D", "X", "T"), ("D", "X", "T")),
        ],
    )
    def test_resolve(self, parts, expected) -> None:
        assert resolve_source_table(parts, "DB", "S") == expected
