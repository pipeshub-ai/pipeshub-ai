"""MariaDB table states: what follows a table across a rename, and how it changed."""

from app.connectors.core.sql.catalog_diff import CatalogDiff, diff_catalog
from app.connectors.sources.mariadb.catalog_state import (
    MariaDBTableState,
    checkpoint_moves,
    rename_keys,
)
from app.connectors.sources.mariadb.connector import MariaDBTable
from app.sources.external.mariadb.mariadb_ import (
    ColumnInfo,
    ForeignKeyInfo,
    TableStatsEntry,
)


def col(name, column_type="int(11)", **flags) -> ColumnInfo:
    return ColumnInfo(name=name, data_type=column_type.split("(")[0], column_type=column_type, **flags)


def table(name, columns=None, *, fks=(), updated="2026-01-01T10:00:00", db="shop") -> MariaDBTable:
    columns = columns if columns is not None else [col("id", is_primary_key=True, is_unique=True), col("name", "varchar(20)")]
    return MariaDBTable(
        name=name, database_name=db, columns=columns, primary_keys=["id"],
        foreign_keys=[ForeignKeyInfo(constraint_name=f"fk_{name}_{c}", column_name=c, foreign_database=db,
                                     foreign_table_name=t, foreign_column_name="id") for c, t in fks],
        stats=TableStatsEntry(database_name=db, table_name=name, last_updated=updated, auto_increment=5),
    )


def state(t: MariaDBTable, table_id=None, **kw) -> MariaDBTableState:
    return MariaDBTableState.of(t, max_rows=1000, table_id=table_id, **kw)


def diff(stored: dict, current: dict) -> CatalogDiff:
    return diff_catalog(*rename_keys(stored, current))


class TestState:
    def test_columns_by_ordinal_with_their_full_type(self) -> None:
        s = state(table("orders"))
        assert s.columns == [(1, "id", "int(11)"), (2, "name", "varchar(20)")]

    def test_the_shape_and_fingerprint_do_not_see_the_tables_name(self) -> None:
        assert state(table("orders")).fingerprint == state(table("purchases")).fingerprint
        assert state(table("orders")).shape == state(table("purchases")).shape

    def test_a_reference_to_itself_is_the_same_under_a_new_name(self) -> None:
        before = state(table("staff", fks=[("id", "staff")]))
        after = state(table("people", fks=[("id", "people")]))
        assert before.fingerprint == after.fingerprint and before.shape == after.shape

    def test_the_shape_follows_a_referenced_table_by_its_id(self) -> None:
        ids = {"shop.customers": 7, "shop.clients": 7}
        before = state(table("orders", fks=[("id", "customers")]), id_of=ids.get)
        after = state(table("orders", fks=[("id", "clients")]), id_of=ids.get)
        assert before.shape == after.shape and before.fingerprint != after.fingerprint

    def test_a_column_with_keys_to_an_id_a_name_and_itself_has_a_shape(self) -> None:
        t = table("orders", fks=[("id", "customers"), ("id", "orders")])
        t.foreign_keys.append(ForeignKeyInfo(constraint_name="fk_x", column_name="id", foreign_database="other",
                                             foreign_table_name="t", foreign_column_name="id"))
        t.foreign_keys.append(ForeignKeyInfo(constraint_name="fk_gone", column_name="gone", foreign_database="shop",
                                             foreign_table_name="customers", foreign_column_name="id"))
        assert state(t, id_of={"shop.customers": 7}.get).shape

    def test_a_phase_7_revision_is_read_back(self) -> None:
        legacy = MariaDBTableState(revision="abc123:2026-01-01T10:00:00|5:1000")
        assert (legacy.structure, legacy.rows_marker) == ("abc123", "2026-01-01T10:00:00|5:1000")
        assert (MariaDBTableState().structure, MariaDBTableState().rows_marker) == ("", "")

    def test_the_revision_parts_match_a_fresh_state(self) -> None:
        s = state(table("orders"))
        assert s.revision == f"{s.structure}:{s.rows_marker}" and s.rows_marker == "2026-01-01T10:00:00|5:1000"

    def test_a_table_whose_columns_cannot_be_seen_has_no_fingerprint(self) -> None:
        assert state(table("t", [])).fingerprint == ""


class TestChange:
    def test_a_pure_rename_changes_names_only(self) -> None:
        change = state(table("purchases"), 18).change_since(state(table("orders"), 18), old_fqn="shop.orders",
                                                             new_fqn="shop.purchases")
        assert change.structure and change.names_only and not change.data and change.previous_fqn == "shop.orders"

    def test_a_renamed_column_is_followed(self) -> None:
        before = state(table("t"))
        after = state(table("t", [col("id", is_primary_key=True, is_unique=True), col("label", "varchar(20)")]))
        change = after.change_since(before, old_fqn="shop.t", new_fqn="shop.t")
        assert change.column_renames == {"name": "label"} and change.names_only

    def test_a_dropped_and_added_column_of_the_same_type_is_no_rename(self) -> None:
        before = state(table("t", [col("id"), col("a"), col("b")]))
        after = state(table("t", [col("id"), col("b"), col("c")]))
        change = after.change_since(before, old_fqn="shop.t", new_fqn="shop.t")
        assert change.column_renames == {} and change.structure and not change.names_only

    def test_a_type_change_is_structure(self) -> None:
        before = state(table("t"))
        after = state(table("t", [col("id", is_primary_key=True, is_unique=True), col("name", "varchar(40)")]))
        change = after.change_since(before, old_fqn="shop.t", new_fqn="shop.t")
        assert change.structure and not change.names_only and change.column_renames == {}

    def test_new_rows_are_data(self) -> None:
        change = state(table("t", updated="2026-02-01")).change_since(state(table("t")), old_fqn="shop.t",
                                                                      new_fqn="shop.t")
        assert change.data and not change.structure

    def test_a_new_row_limit_is_data(self) -> None:
        before = MariaDBTableState.of(table("t"), max_rows=10)
        change = state(table("t")).change_since(before, old_fqn="shop.t", new_fqn="shop.t")
        assert change.data and not change.structure

    def test_a_new_id_under_the_same_name_is_data_and_renames_no_column(self) -> None:
        before = state(table("t"), 18)
        after = state(table("t", [col("id", is_primary_key=True, is_unique=True), col("label", "varchar(20)")]), 20)
        change = after.change_since(before, old_fqn="shop.t", new_fqn="shop.t")
        assert change.data and change.column_renames == {} and not change.names_only

    def test_without_ids_a_column_rename_is_still_followed(self) -> None:
        before = state(table("t"))
        after = state(table("t", [col("id", is_primary_key=True, is_unique=True), col("label", "varchar(20)")]))
        assert after.change_since(before, old_fqn="shop.t", new_fqn="shop.t").column_renames == {"name": "label"}

    def test_a_pending_rename_is_written_again(self) -> None:
        before = state(table("t")).model_copy(update={"renamed_from": "shop.old"})
        change = state(table("t")).change_since(before, old_fqn="shop.t", new_fqn="shop.t")
        assert change.structure and change.rename_pending

    def test_a_phase_7_state_compares_by_its_revision(self) -> None:
        now = state(table("t"))
        legacy = MariaDBTableState(revision=now.revision, profiled=True)
        change = now.change_since(legacy, old_fqn="shop.t", new_fqn="shop.t")
        assert not change.structure and not change.data


class TestRenameKeys:
    def test_ids_follow_a_rename_and_a_swap(self) -> None:
        d = diff({"shop.a": state(table("a"), 1), "shop.b": state(table("b", [col("x")]), 2)},
                 {"shop.a": state(table("a", [col("x")]), 2), "shop.b": state(table("b"), 1)})
        assert d.renamed == {"shop.a": "shop.b", "shop.b": "shop.a"}

    def test_without_ids_a_pure_rename_is_followed_by_fingerprint(self) -> None:
        d = diff({"shop.orders": state(table("orders"))}, {"shop.purchases": state(table("purchases"))})
        assert d.renamed == {"shop.orders": "shop.purchases"} and not d.added and not d.missing

    def test_a_fingerprint_two_tables_share_follows_nothing(self) -> None:
        d = diff({"shop.orders": state(table("orders"))},
                 {"shop.p1": state(table("p1")), "shop.p2": state(table("p2"))})
        assert d.renamed == {} and d.missing == ["shop.orders"]

    def test_a_table_still_under_its_name_is_never_paired_by_shape(self) -> None:
        stored = {"shop.a": state(table("a"))}
        current = {"shop.a": state(table("a", [col("other")])), "shop.b": state(table("b"))}
        d = diff(stored, current)
        assert d.renamed == {} and d.added == ["shop.b"]

    def test_ids_read_on_both_sides_are_not_overruled_by_a_fingerprint(self) -> None:
        d = diff({"shop.old": state(table("old"), 5)}, {"shop.new": state(table("new"), 9)})
        assert d.renamed == {} and d.missing == ["shop.old"] and d.added == ["shop.new"]

    def test_an_id_on_one_side_only_falls_back_to_the_fingerprint(self) -> None:
        d = diff({"shop.orders": state(table("orders"), 18)}, {"shop.purchases": state(table("purchases"))})
        assert d.renamed == {"shop.orders": "shop.purchases"}

    def test_a_rename_with_an_alter_is_followed_by_id_only(self) -> None:
        altered = [col("id", is_primary_key=True, is_unique=True), col("name", "varchar(20)"), col("note", "text")]
        stored = {"shop.orders": state(table("orders"), 18)}
        assert diff(stored, {"shop.purchases": state(table("purchases", altered), 18)}).renamed == {
            "shop.orders": "shop.purchases"}
        no_ids = {"shop.orders": state(table("orders"))}
        assert diff(no_ids, {"shop.purchases": state(table("purchases", altered))}).renamed == {}

    def test_a_referenced_tables_rename_leaves_the_referencing_table_names_only(self) -> None:
        ids = {"shop.customers": 7, "shop.clients": 7}
        stored = {"shop.orders": state(table("orders", fks=[("id", "customers")]), 1, id_of=ids.get),
                  "shop.customers": state(table("customers"), 7)}
        current = {"shop.orders": state(table("orders", fks=[("id", "clients")]), 1, id_of=ids.get),
                   "shop.clients": state(table("clients"), 7)}
        d = diff(stored, current)
        assert d.renamed == {"shop.customers": "shop.clients"}
        assert d.changed["shop.orders"].structure and d.changed["shop.orders"].names_only


class TestIdsAfterARenameIntoATakenName:
    def test_a_rename_into_a_dropped_name_with_a_new_table_under_the_old_one_is_trusted(self) -> None:
        a = table("a", [col("id"), col("title", "text")])
        stored = {"shop.a": state(a, 1), "shop.old": state(table("old", [col("id"), col("x", "date")]), 2)}
        current = {"shop.old": state(table("old", a.columns), 1), "shop.a": state(table("a", [col("other")]), 3)}
        assert diff(stored, current).renamed == {"shop.a": "shop.old"}


class TestCheckpoint:
    def test_moves_land_under_the_new_names_with_the_record_ids(self) -> None:
        a, b = MariaDBTableState(revision="a"), MariaDBTableState(revision="b")
        swapped = checkpoint_moves({"x": a, "y": b}, {"x": "y", "y": "x"}, {"x": "r1", "y": "r2"})
        assert swapped["y"].revision == "a" and swapped["y"].renamed_from == "x" and swapped["y"].moved_record_id == "r1"
        assert swapped["x"].revision == "b" and swapped["x"].moved_record_id == "r2"
        chained = checkpoint_moves({"x": a, "y": b}, {"y": "z", "x": "y"}, {"x": "r1", "y": "r2"})
        assert set(chained) == {"y", "z"} and chained["z"].revision == "b" and chained["y"].revision == "a"


class TestReviewFixes:
    def test_ids_renumbered_by_a_reload_follow_no_rename(self) -> None:
        # Every table kept its name; the ids were handed out again (dump and reload).
        def t(name) -> object:
            return table(name, [col("id"), col(f"{name}_name", "varchar(20)")])

        stored = {"shop.a": state(t("a"), 10), "shop.b": state(t("b"), 20), "shop.c": state(t("c"), 30)}
        current = {"shop.a": state(t("a"), 20), "shop.b": state(t("b"), 30), "shop.c": state(t("c"), 40)}
        d = diff(stored, current)
        assert d.renamed == {} and not d.added and not d.missing
        assert all(not change.data for change in d.changed.values())

    def test_a_two_table_reload_moves_nothing(self) -> None:
        a, b = table("a", [col("id"), col("title", "text")]), table("b", [col("id"), col("total", "decimal(9,2)")])
        stored = {"shop.a": state(a, 10), "shop.b": state(b, 20)}
        current = {"shop.a": state(a, 20), "shop.b": state(b, 21)}
        d = diff(stored, current)
        assert d.renamed == {} and not d.replaced and not d.added

    def test_one_truncate_does_not_distrust_the_ids(self) -> None:
        stored = {"shop.a": state(table("a"), 10), "shop.b": state(table("b"), 20), "shop.c": state(table("c"), 30),
                  "shop.d": state(table("d", [col("x")]), 40)}
        current = {"shop.a": state(table("a"), 11), "shop.b": state(table("b"), 20), "shop.c": state(table("c"), 30),
                   "shop.e": state(table("e", [col("x")]), 40)}
        d = diff(stored, current)
        assert d.renamed == {"shop.d": "shop.e"} and d.changed["shop.a"].data

    def test_a_lookalike_paired_by_fingerprint_is_not_names_only(self) -> None:
        d = diff({"shop.staging_09": state(table("staging_09"))}, {"shop.staging_10": state(table("staging_10"))})
        change = d.changed["shop.staging_10"]
        assert change.previous_fqn == "shop.staging_09" and change.structure and not change.names_only

    def test_tables_with_no_columns_are_never_paired(self) -> None:
        d = diff({"shop.a": state(table("a", []))}, {"shop.b": state(table("b", []))})
        assert d.renamed == {}
