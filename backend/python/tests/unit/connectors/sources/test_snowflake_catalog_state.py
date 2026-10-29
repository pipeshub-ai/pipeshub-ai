"""SnowflakeTableState: what a sync saves, and how two states compare."""

from app.connectors.core.sql.catalog_diff import diff_catalog
from app.connectors.sources.snowflake.catalog_state import SnowflakeTableState
from app.connectors.sources.snowflake.data_fetcher import (
    ForeignKey,
    SnowflakeColumnInfo,
    SnowflakeTable,
)


def table(
    name: str = "ORDERS",
    *,
    columns: list[tuple[str, str]] = (("ID", "NUMBER(38,0)"), ("STATUS", "TEXT"), ("CID", "NUMBER(38,0)")),
    created: str = "2024-01-01T00:00:00+00:00",
    last_altered: str = "2024-02-01T00:00:00+00:00",
    primary: tuple[str, ...] = ("ID",),
    target: str = "CUSTOMERS",
    schema: str = "S",
) -> SnowflakeTable:
    t = SnowflakeTable(name, "DB", schema, created_at=created, last_altered=last_altered)
    t.columns = [
        SnowflakeColumnInfo(name=n, ordinal_position=i + 1, data_type=dt,
                            is_primary_key=n in primary, is_unique=len(primary) == 1 and n in primary)
        for i, (n, dt) in enumerate(columns)
    ]
    t.primary_keys = list(primary)
    t.foreign_keys = [ForeignKey("FK", "DB", schema, name, "CID", "DB", "S", target, "ID")]
    return t


def state(t: SnowflakeTable, row_limit: int = 1000, **kwargs) -> SnowflakeTableState:
    return SnowflakeTableState.of(t, row_limit=row_limit, **kwargs)


def change(before: SnowflakeTable, after: SnowflakeTable, *, old="DB.S.ORDERS", new="DB.S.ORDERS", **kwargs) -> object:
    return state(after, **kwargs).change_since(state(before, **kwargs), old_fqn=old, new_fqn=new)


class TestBuild:
    def test_keys_on_a_column_the_read_did_not_return_still_have_a_shape(self) -> None:
        t = table()
        t.foreign_keys.append(ForeignKey("FK2", "DB", "S", "ORDERS", "GONE", "DB", "S", "OTHER", "ID"))
        assert state(t).shape
    def test_ordinals_key_names_and_types(self) -> None:
        s = state(table())
        assert s.columns == [(1, "ID", "NUMBER(38,0)"), (2, "STATUS", "TEXT"), (3, "CID", "NUMBER(38,0)")]
        assert s.names_by_ordinal == {1: "ID", 2: "STATUS", 3: "CID"}
        assert s.identity == "2024-01-01T00:00:00+00:00"
        assert (s.database, s.schema_name, s.table_name, s.row_limit) == ("DB", "S", "ORDERS", 1000)

    def test_hashes_are_stable(self) -> None:
        assert state(table()) == state(table())

    def test_round_trips_as_json(self) -> None:
        s = state(table())
        assert SnowflakeTableState.model_validate_json(s.model_dump_json()) == s

    def test_no_created_time_means_no_identity(self) -> None:
        assert state(table(created="")).identity is None


class TestSameTable:
    def test_half_the_ordinals_keeping_their_type_is_the_same_table(self) -> None:
        before = state(table())
        after = state(table(columns=[("ID", "NUMBER(38,0)"), ("STATUS", "TEXT"), ("X", "DATE")]))
        assert after.same_table_as(before)

    def test_fewer_is_not(self) -> None:
        before = state(table())
        after = state(table(columns=[("A", "DATE"), ("B", "DATE"), ("C", "DATE")]))
        assert not after.same_table_as(before)


class TestChange:
    def test_unchanged(self) -> None:
        c = change(table(), table())
        assert not (c.structure or c.data)

    def test_rows_changed(self) -> None:
        c = change(table(), table(last_altered="2024-03-01T00:00:00+00:00"))
        assert c.data and not c.structure

    def test_row_limit_changed(self) -> None:
        c = state(table(), row_limit=10).change_since(state(table()), old_fqn="t", new_fqn="t")
        assert c.data

    def test_table_renamed_is_names_only(self) -> None:
        c = change(table(), table("ORDERS2"), new="DB.S.ORDERS2")
        assert (c.previous_fqn, c.structure, c.names_only) == ("DB.S.ORDERS", True, True)

    def test_column_renamed_at_its_ordinal(self) -> None:
        renamed = table(columns=[("ID", "NUMBER(38,0)"), ("STATE", "TEXT"), ("CID", "NUMBER(38,0)")])
        c = change(table(), renamed)
        assert c.column_renames == {"STATUS": "STATE"}
        assert c.structure and c.names_only

    def test_a_drop_plus_add_is_no_rename(self) -> None:
        # Snowflake renumbers ordinals after a drop: STATUS gone, CID moves to 2, NOTE added at 3.
        after = table(columns=[("ID", "NUMBER(38,0)"), ("CID", "NUMBER(38,0)"), ("NOTE", "TEXT")])
        c = change(table(), after)
        assert c.column_renames == {}
        assert c.structure and not c.names_only

    def test_dropping_one_column_and_adding_one_of_the_same_type_is_no_rename(self) -> None:
        # (A, B, C) all TEXT; drop A, add D: Snowflake renumbers to (B, C, D).
        before = table(columns=[("A", "TEXT"), ("B", "TEXT"), ("C", "TEXT")], primary=())
        after = table(columns=[("B", "TEXT"), ("C", "TEXT"), ("D", "TEXT")], primary=())
        c = change(before, after)
        assert c.column_renames == {}
        assert c.structure and not c.names_only

    def test_swapped_column_names_are_not_followed(self) -> None:
        before = table(columns=[("A", "TEXT"), ("B", "TEXT")], primary=())
        after = table(columns=[("B", "TEXT"), ("A", "TEXT")], primary=())
        c = change(before, after)
        assert c.column_renames == {}
        # Each column's notes now describe the other one.
        assert c.structure and not c.names_only

    def test_moved_record_id_round_trips(self) -> None:
        s = state(table()).model_copy(update={"renamed_from": "DB.S.OLD", "moved_record_id": "r1"})
        assert SnowflakeTableState.model_validate_json(s.model_dump_json()) == s

    def test_a_type_change_is_structure(self) -> None:
        after = table(columns=[("ID", "NUMBER(38,0)"), ("STATUS", "VARCHAR(10)"), ("CID", "NUMBER(38,0)")])
        c = change(table(), after)
        assert c.structure and not c.names_only

    def test_a_new_key_is_structure(self) -> None:
        c = change(table(), table(primary=("ID", "STATUS")))
        assert c.structure and not c.names_only

    def test_create_or_replace_is_new_data(self) -> None:
        c = change(table(), table(created="2024-05-01T00:00:00+00:00"))
        assert c.data

    def test_a_referenced_tables_rename_is_names_only(self) -> None:
        identities = {"DB.S.CUSTOMERS": "c-created", "DB.S.CLIENTS": "c-created"}
        c = change(table(), table(target="CLIENTS"), identity_of=identities.get)
        assert c.structure and c.names_only

    def test_a_pending_rename_is_finished(self) -> None:
        before = state(table()).model_copy(update={"renamed_from": "DB.S.OLD"})
        c = state(table()).change_since(before, old_fqn="DB.S.ORDERS", new_fqn="DB.S.ORDERS")
        assert c.rename_pending and c.structure


class TestWithTheDiff:
    def test_a_rename_into_another_schema_is_followed(self) -> None:
        stored = {"DB.S.ORDERS": state(table())}
        current = {"DB.T.ORDERS": state(table(schema="T"))}
        diff = diff_catalog(stored, current)
        assert diff.renamed == {"DB.S.ORDERS": "DB.T.ORDERS"}

    def test_tables_cloned_together_are_matched_by_name_only(self) -> None:
        stored = {"DB.S.A": state(table("A", created="clone")), "DB.S.B": state(table("B", created="clone"))}
        current = {"DB.S.A": state(table("A", created="clone")), "DB.S.C": state(table("C", created="clone"))}
        diff = diff_catalog(stored, current)
        assert diff.renamed == {}
        assert (diff.missing, diff.added) == (["DB.S.B"], ["DB.S.C"])
