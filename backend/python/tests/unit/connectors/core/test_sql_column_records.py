from dataclasses import dataclass
from types import SimpleNamespace

from app.config.constants.arangodb import Connectors
from app.connectors.core.sql.column_records import (
    build_column_records,
    column_revision,
    stored_stats_and_profile,
)
from app.connectors.core.sql.profiling import ColumnProfile
from app.models.entities import (
    ProgressStatus,
    RecordGroupType,
    RecordType,
    SQLTableRecord,
)


@dataclass(frozen=True)
class Column:
    name: str
    data_type: str | None = "TEXT"
    nullable: bool = True
    default: str | None = None
    is_primary_key: bool = False
    is_unique: bool = False
    is_part_of_composite_primary_key: bool = False
    is_part_of_composite_unique: bool = False


def table_record(revision: str | None = "struct:content") -> SQLTableRecord:
    return SQLTableRecord(
        id="table-1",
        record_name="ORDERS",
        record_type=RecordType.SQL_TABLE,
        record_group_type=RecordGroupType.SQL_NAMESPACE.value,
        external_record_group_id="DB.S",
        external_record_id="DB.S.ORDERS",
        external_revision_id=revision,
        connector_name=Connectors.SNOWFLAKE,
        connector_id="conn-1",
        mime_type="text/sql-table",
        origin="CONNECTOR",
        source_created_at=1,
        source_updated_at=2,
        version=3,
    )


def column_fqn(table_fqn: str, name: str) -> str:
    return f"{table_fqn}.{name}"


def build(columns, **kwargs) -> list:
    return build_column_records(table_record(kwargs.pop("revision", "struct:content")), columns,
                                column_fqn=column_fqn, **kwargs)


class TestBuildColumnRecords:
    def test_one_record_per_named_column_with_gapped_positions(self) -> None:
        records = build([Column("a"), Column(""), Column("a"), Column("b")])
        assert [(r.record_name, r.ordinal_position) for r in records] == [("a", 0), ("b", 3)]

    def test_identity_comes_from_the_table_record(self) -> None:
        (record,) = build([Column("ID")])
        assert record.external_record_id == record.fqn == "DB.S.ORDERS.ID"
        assert record.parent_external_record_id == record.table_fqn == "DB.S.ORDERS"
        assert (record.parent_node_id, record.parent_record_type) == ("table-1", RecordType.SQL_TABLE)
        assert (record.connector_name, record.connector_id) == (Connectors.SNOWFLAKE, "conn-1")
        assert (record.external_record_group_id, record.version) == ("DB.S", 3)
        assert record.is_dependent_node and record.is_internal
        assert record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    def test_flags_stats_and_profile_are_copied(self) -> None:
        column = Column("ID", "NUMBER", nullable=False, default="0", is_primary_key=True, is_unique=True)
        profile = ColumnProfile(top_values=["1"], top_value_frequencies=[1.0], min_value="1", max_value="9",
                                numeric_text=None, profiled_at=5)
        (record,) = build(
            [column],
            stats_by_name={"ID": {"distinct_count": 9, "null_count": 0, "cardinality_ratio": 1.0}},
            profiles={"ID": profile},
        )
        assert (record.data_type, record.nullable, record.default) == ("NUMBER", False, "0")
        assert (record.is_primary_key, record.is_unique) == (True, True)
        assert (record.distinct_count, record.null_count, record.cardinality_ratio) == (9, 0, 1.0)
        assert (record.top_values, record.min_value, record.max_value, record.profiled_at) == (["1"], "1", "9", 5)

    def test_keep_stored_keeps_the_saved_stats_and_profile(self) -> None:
        stored = SimpleNamespace(
            distinct_count=3, null_count=1, cardinality_ratio=0.3, profiled_at=7,
            top_values=["X"], top_value_frequencies=[1.0], min_value=None, max_value=None, numeric_text=False,
        )
        (record,) = build([Column("C")], stats_by_name={}, profiles={}, stored={"DB.S.ORDERS.C": stored},
                          keep_stored=True)
        assert (record.distinct_count, record.top_values, record.profiled_at) == (3, ["X"], 7)

    def test_without_keep_stored_the_saved_record_is_ignored(self) -> None:
        stored = SimpleNamespace(distinct_count=3, null_count=1, cardinality_ratio=0.3, profiled_at=7)
        (record,) = build([Column("C")], stored={"DB.S.ORDERS.C": stored})
        assert record.distinct_count is None and record.profiled_at is None

    def test_keep_stored_without_a_saved_record_uses_the_new_values(self) -> None:
        (record,) = build([Column("C")], stats_by_name={"C": {"distinct_count": 2}}, stored={}, keep_stored=True)
        assert record.distinct_count == 2

    def test_a_table_without_a_revision_gives_columns_a_digest(self) -> None:
        (record,) = build([Column("C")], revision=None, stats_by_name={"C": {"distinct_count": 2}})
        assert record.external_revision_id and not record.external_revision_id.startswith("None")


class TestValuesAllowed:
    def test_without_values_no_record_keeps_values_kept_or_fresh(self) -> None:
        profile = ColumnProfile(top_values=["1"], min_value="1", max_value="9", numeric_text=True, profiled_at=5)
        stored = SimpleNamespace(
            distinct_count=3, null_count=1, cardinality_ratio=0.3, profiled_at=7, top_values=["X"],
            top_value_frequencies=[1.0], min_value="a", max_value="b", numeric_text=False,
        )
        fresh, kept = build(
            [Column("A"), Column("B")], profiles={"A": profile}, stored={"DB.S.ORDERS.B": stored},
            keep_stored=True, values_allowed=False,
        )
        assert (fresh.top_values, fresh.min_value, fresh.numeric_text, fresh.profiled_at) == (None, None, True, 5)
        assert (kept.top_values, kept.max_value, kept.distinct_count, kept.profiled_at) == (None, None, 3, 7)


class TestLoadTypedColumns:
    async def test_typed_records_replace_base_ones(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from app.connectors.core.sql.column_records import load_typed_columns

        processor = MagicMock()
        typed = SimpleNamespace(id="c1", distinct_count=3)
        processor.get_typed_records_by_ids = AsyncMock(return_value={"c1": typed})
        assert await load_typed_columns(processor, {"x.c": SimpleNamespace(id="c1")}) == {"x.c": typed}
        assert await load_typed_columns(processor, {}) == {}
        processor.get_typed_records_by_ids.assert_awaited_once_with(["c1"], raise_on_error=True)

    async def test_a_missing_typed_record_raises(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        import pytest

        from app.connectors.core.sql.column_records import load_typed_columns

        processor = MagicMock()
        processor.get_typed_records_by_ids = AsyncMock(return_value={})
        with pytest.raises(LookupError):
            await load_typed_columns(processor, {"x.c": SimpleNamespace(id="c1")})


class TestColumnRevision:
    def test_no_facts_keeps_the_table_revision(self) -> None:
        assert column_revision("t", {}, None) == "t"
        assert column_revision(None, {}, None) is None

    def test_facts_extend_it(self) -> None:
        revision = column_revision("t", {"distinct_count": 3}, None)
        assert revision.startswith("t:") and revision != column_revision("t", {"distinct_count": 4}, None)

    def test_profiled_at_does_not_change_it(self) -> None:
        assert column_revision("t", {}, ColumnProfile(top_values=["a"], profiled_at=1)) == column_revision(
            "t", {}, ColumnProfile(top_values=["a"], profiled_at=2)
        )

    def test_without_a_table_revision_it_is_the_digest(self) -> None:
        revision = column_revision(None, {"distinct_count": 3}, None)
        assert revision and ":" not in revision


class TestStoredStatsAndProfile:
    def test_a_record_never_profiled_has_stats_but_no_profile(self) -> None:
        stats, profile = stored_stats_and_profile(SimpleNamespace(distinct_count=3, null_count=None,
                                                                  cardinality_ratio=None, profiled_at=None))
        assert stats == {"distinct_count": 3, "null_count": None, "cardinality_ratio": None}
        assert profile is None
