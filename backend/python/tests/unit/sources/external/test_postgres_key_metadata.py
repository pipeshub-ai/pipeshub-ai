"""Tests for PostgreSQL column key metadata (PK / unique / composite flags)."""

from unittest.mock import AsyncMock

import pytest

from app.sources.external.postgres.postgres_ import (
    ColumnInfo,
    PostgreSQLDataSource,
    _TableKeyRow,
    apply_key_metadata_to_columns,
    primary_key_columns_from_key_defs,
)


def _columns(*names: str) -> list[ColumnInfo]:
    return [ColumnInfo(name=name) for name in names]


def _key(
    key_type: str,
    column_names: list[str],
) -> _TableKeyRow:
    return {
        "key_type": key_type,  # type: ignore[typeddict-item]
        "key_width": len(column_names),
        "column_names": column_names,
    }


class TestApplyKeyMetadataToColumns:

    def test_single_column_unique_index(self) -> None:
        cols = _columns("player_api_id", "player_name")
        apply_key_metadata_to_columns(cols, [_key("unique", ["player_api_id"])])
        assert cols[0].is_unique is True
        assert cols[0].is_primary_key is False
        assert cols[1].is_unique is False

    def test_single_column_primary_key(self) -> None:
        cols = _columns("id", "name")
        apply_key_metadata_to_columns(cols, [_key("primary", ["id"])])
        assert cols[0].is_primary_key is True
        assert cols[0].is_unique is True
        assert cols[0].is_part_of_composite_primary_key is False

    def test_composite_primary_key(self) -> None:
        cols = _columns("a", "b", "c")
        apply_key_metadata_to_columns(cols, [_key("primary", ["a", "b"])])
        assert cols[0].is_primary_key is True
        assert cols[0].is_unique is False
        assert cols[0].is_part_of_composite_primary_key is True
        assert cols[1].is_part_of_composite_primary_key is True
        assert cols[2].is_primary_key is False

    def test_composite_unique_constraint(self) -> None:
        cols = _columns("a", "b")
        apply_key_metadata_to_columns(cols, [_key("unique", ["a", "b"])])
        assert cols[0].is_unique is False
        assert cols[0].is_part_of_composite_unique is True
        assert cols[1].is_part_of_composite_unique is True

    def test_skips_partial_or_expression_keys_with_width_mismatch(self) -> None:
        cols = _columns("email")
        apply_key_metadata_to_columns(cols, [{
            "key_type": "unique",
            "key_width": 2,
            "column_names": ["email"],
        }])
        assert cols[0].is_unique is False

    def test_pk_and_separate_single_column_unique(self) -> None:
        cols = _columns("id", "player_api_id")
        apply_key_metadata_to_columns(cols, [
            _key("primary", ["id"]),
            _key("unique", ["player_api_id"]),
        ])
        assert cols[0].is_primary_key is True
        assert cols[0].is_unique is True
        assert cols[1].is_unique is True
        assert cols[1].is_primary_key is False


class TestPrimaryKeyColumnsFromKeyDefs:

    def test_composite_primary_key_order(self) -> None:
        keys = [
            _key("unique", ["player_api_id"]),
            _key("primary", ["a", "b"]),
        ]
        assert primary_key_columns_from_key_defs(keys) == ["a", "b"]


class TestSQLColumnRecordSchema:

    def test_to_arango_includes_composite_fields(self) -> None:
        from app.config.constants.arangodb import Connectors, OriginTypes
        from app.models.entities import RecordType, SQLColumnRecord

        record = SQLColumnRecord(
            id="col-1",
            org_id="org-1",
            record_name="player_api_id",
            record_type=RecordType.SQL_COLUMN,
            external_record_id="db.public.player.player_api_id",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.POSTGRESQL,
            connector_id="conn-1",
            is_unique=True,
            is_part_of_composite_unique=False,
        )
        doc = record.to_arango_record()
        assert doc["isUnique"] is True
        assert doc["isPartOfCompositePrimaryKey"] is False
        assert doc["isPartOfCompositeUnique"] is False


class TestFetchTableKeyDefinitions:

    @pytest.mark.asyncio
    async def test_parses_query_rows(self) -> None:
        client = type("Client", (), {})()
        client.execute_query = AsyncMock(return_value=[
            {
                "key_type": "unique",
                "key_width": 1,
                "column_names": ["player_api_id"],
            }
        ])
        ds = PostgreSQLDataSource(client)  # type: ignore[arg-type]
        keys = await ds._fetch_table_key_definitions("public", "player")
        assert keys == [{
            "key_type": "unique",
            "key_width": 1,
            "column_names": ["player_api_id"],
        }]
