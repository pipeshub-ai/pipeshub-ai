"""PostgreSQL's wrappers over the shared SQL helpers keep their old behaviour."""

import pytest

import app.connectors.core.sql.profiling as profiling
import app.connectors.sources.postgres.connector as pg_connector
import app.sources.external.postgres.postgres_ as pg
from app.connectors.core.registry.filters import FilterCollection
from app.sources.external.postgres.postgres_ import (
    ColumnInfo,
    PgColumnStatistics,
    estimate_distinct_and_ratio,
    top_values_eligible,
)


@pytest.mark.parametrize(
    "name",
    ["ColumnProfile", "TOP_VALUES_LIMIT", "TOP_VALUE_MAX_CHARS", "looks_personal", "looks_like_contact_value",
     "screen_top_values", "top_values_from_counts", "parse_table_column_stats_row"],
)
def test_moved_names_are_still_importable_from_postgres(name) -> None:
    assert getattr(pg, name) is getattr(profiling, name)


def test_a_short_primary_key_is_no_primary_key() -> None:
    keys = [
        {"key_type": "primary", "key_width": 2, "column_names": ["a"]},
        {"key_type": "primary", "key_width": 1, "column_names": ["b"]},
    ]
    assert pg.primary_key_columns_from_key_defs(keys[:1]) == []
    assert pg.primary_key_columns_from_key_defs(keys) == ["b"]


def test_build_column_records_keeps_stored_profiles_under_the_connector_ids() -> None:
    from types import SimpleNamespace

    from app.config.constants.arangodb import Connectors
    from app.models.entities import RecordGroupType, RecordType, SQLTableRecord

    connector = pg_connector.PostgreSQLConnector.__new__(pg_connector.PostgreSQLConnector)
    connector.database_name = "shop"
    connector.indexing_filters = FilterCollection()
    table_record = SQLTableRecord(
        id="t1", record_name="orders", record_type=RecordType.SQL_TABLE,
        record_group_type=RecordGroupType.SQL_NAMESPACE.value, external_record_group_id="public",
        external_record_id="public.orders", external_revision_id="r", connector_name=Connectors.POSTGRESQL,
        connector_id="c1", mime_type="text/sql-table", origin="CONNECTOR", version=1,
    )
    table = pg_connector.PostgresTable(
        name="orders", schema_name="public", columns=[ColumnInfo(name="status", data_type="text")],
        profiling_failed=True,
    )
    stored = SimpleNamespace(distinct_count=4, null_count=0, cardinality_ratio=0.1, profiled_at=9,
                             top_values=["OPEN"], top_value_frequencies=[1.0], min_value=None,
                             max_value=None, numeric_text=False)
    fqn = connector._column_fqn("public.orders", "status")
    (record,) = connector._build_column_records(table_record, table, {fqn: stored})
    assert record.external_record_id == "shop.public.orders.status"
    assert (record.distinct_count, record.top_values) == (4, ["OPEN"])


async def test_a_failed_profile_keeps_the_typed_stored_columns_not_the_base_ones() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    import pytest

    connector = pg_connector.PostgreSQLConnector.__new__(pg_connector.PostgreSQLConnector)
    connector.logger = MagicMock()
    connector.data_entities_processor = MagicMock()
    typed = SimpleNamespace(id="c1", distinct_count=4)
    connector.data_entities_processor.get_typed_records_by_ids = AsyncMock(return_value={"c1": typed})
    base = SimpleNamespace(id="c1")
    assert await connector._typed_columns({"shop.public.orders.status": base}) == {
        "shop.public.orders.status": typed,
    }
    # Not the base records, which would write the columns back without their stats.
    connector.data_entities_processor.get_typed_records_by_ids = AsyncMock(return_value={})
    with pytest.raises(LookupError):
        await connector._typed_columns({"shop.public.orders.status": base})


def test_indexing_off_keeps_no_stored_values_on_a_failed_profile() -> None:
    from types import SimpleNamespace

    from app.config.constants.arangodb import Connectors
    from app.connectors.core.registry.filters import Filter
    from app.models.entities import RecordGroupType, RecordType, SQLTableRecord

    connector = pg_connector.PostgreSQLConnector.__new__(pg_connector.PostgreSQLConnector)
    connector.database_name = "shop"
    connector.indexing_filters = FilterCollection(filters=[
        Filter(key="index_stage_tables", value=False, type="boolean", operator="is"),
    ])
    table_record = SQLTableRecord(
        id="t1", record_name="orders", record_type=RecordType.SQL_TABLE,
        record_group_type=RecordGroupType.SQL_NAMESPACE.value, external_record_group_id="public",
        external_record_id="public.orders", external_revision_id="r", connector_name=Connectors.POSTGRESQL,
        connector_id="c1", mime_type="text/sql-table", origin="CONNECTOR", version=1,
    )
    table = pg_connector.PostgresTable(
        name="orders", schema_name="public", columns=[ColumnInfo(name="status", data_type="text")],
        profiling_failed=True,
    )
    stored = SimpleNamespace(distinct_count=4, null_count=0, cardinality_ratio=0.1, profiled_at=9,
                             top_values=["OPEN"], top_value_frequencies=[1.0], min_value="a",
                             max_value="z", numeric_text=False)
    (record,) = connector._build_column_records(table_record, table, {"shop.public.orders.status": stored})
    assert record.distinct_count == 4
    assert (record.top_values, record.min_value, record.max_value) == (None, None, None)


class TestEstimateDistinctAndRatio:
    def test_partial_exact_stats_fall_back_to_pg_stats(self) -> None:
        pg_stats = PgColumnStatistics(column_name="c", n_distinct=4, null_frac=0.5)
        assert estimate_distinct_and_ratio({"distinct_count": 9, "null_count": None}, pg_stats, 100) == (4, 0.08)

    def test_exact_stats_win_when_complete(self) -> None:
        pg_stats = PgColumnStatistics(column_name="c", n_distinct=4, null_frac=0.5)
        assert estimate_distinct_and_ratio({"distinct_count": 9, "null_count": 10}, pg_stats, 100) == (9.0, 0.1)

    def test_no_rows_gives_nothing_even_with_exact_stats(self) -> None:
        assert estimate_distinct_and_ratio({"distinct_count": 9, "null_count": 0}, None, 0) == (None, None)


@pytest.mark.parametrize(
    "column",
    [
        ColumnInfo(name="status", data_type="text"),
        ColumnInfo(name="id", data_type="integer", is_primary_key=True, is_unique=True),
        ColumnInfo(name="a", data_type="integer", is_primary_key=True, is_part_of_composite_primary_key=True),
        ColumnInfo(name="payload", data_type="jsonb"),
        ColumnInfo(name="email", data_type="text"),
    ],
)
def test_top_values_eligible_matches_the_shared_rule(column) -> None:
    expected = profiling.vocabulary_eligible(
        name=column.name,
        is_unique=column.is_unique,
        is_primary_key=column.is_primary_key,
        is_part_of_composite_primary_key=column.is_part_of_composite_primary_key,
        excluded_type=column.data_type in ("jsonb",),
        distinct_count=5,
        non_null_ratio=0.01,
    )
    assert top_values_eligible(column, 5, 0.01) is expected


def test_split_table_fqn_keeps_its_error_message() -> None:
    with pytest.raises(ValueError, match="Not a table id"):
        pg_connector.split_table_fqn("a.b.c")
