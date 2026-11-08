"""What a sync reads off two snapshots of a PostgreSQL catalog."""

import hashlib

import pytest

from app.connectors.sources.postgres.catalog_diff import (
    PostgresTableState,
    diff_catalog,
)


def _state(fqn: str = "shop.orders", **overrides) -> PostgresTableState:
    schema, table = fqn.split(".", 1)
    values = {
        "schema_name": schema,
        "table_name": table,
        "oid": 100,
        "storage_id": "100",
        "column_hash": "cols",
        "constraint_hash": "keys",
        "n_tup_ins": 5,
        "columns": {"1": "order_id", "2": "status"},
    }
    values.update(overrides)
    return PostgresTableState(**values)


class TestTables:
    def test_nothing_changed(self) -> None:
        diff = diff_catalog({"shop.orders": _state()}, {"shop.orders": _state()})

        assert not (diff.added or diff.missing or diff.changed or diff.reanalyzed)

    def test_a_table_seen_for_the_first_time_is_added(self) -> None:
        diff = diff_catalog({}, {"shop.orders": _state()})

        assert diff.added == ["shop.orders"]
        assert not diff.changed

    def test_a_table_no_longer_listed_is_missing(self) -> None:
        diff = diff_catalog({"shop.orders": _state()}, {})

        assert diff.missing == ["shop.orders"]

    def test_a_table_that_could_not_be_read_is_neither_missing_nor_changed(self) -> None:
        diff = diff_catalog({"shop.orders": _state()}, {}, unreadable={"shop.orders"})

        assert not (diff.missing or diff.changed)

    def test_a_renamed_table_is_one_change_not_a_drop_and_an_add(self) -> None:
        diff = diff_catalog(
            {"shop.orders": _state()},
            {"shop.purchases": _state("shop.purchases")},
        )

        assert not (diff.added or diff.missing)
        assert diff.renamed == {"shop.orders": "shop.purchases"}
        change = diff.changed["shop.purchases"]
        assert change.previous_fqn == "shop.orders"
        assert change.structure and not change.data

    def test_a_table_moved_to_another_schema_is_renamed(self) -> None:
        diff = diff_catalog(
            {"shop.orders": _state()},
            {"archive.orders": _state("archive.orders")},
        )

        assert diff.renamed == {"shop.orders": "archive.orders"}

    def test_a_new_table_with_another_oid_is_not_taken_for_a_rename(self) -> None:
        diff = diff_catalog(
            {"shop.orders": _state()},
            {"shop.purchases": _state("shop.purchases", oid=200)},
        )

        assert diff.added == ["shop.purchases"]
        assert diff.missing == ["shop.orders"]
        assert not diff.renamed

    def test_a_table_recreated_under_its_name_is_the_same_table_changed(self) -> None:
        """A full refresh drops and creates the table on every run."""
        diff = diff_catalog(
            {"shop.orders": _state()},
            {"shop.orders": _state(oid=200, storage_id="200", n_tup_ins=0)},
        )

        assert not (diff.added or diff.missing or diff.renamed)
        assert diff.changed["shop.orders"].data

    def test_a_reused_oid_with_none_of_the_columns_is_not_a_rename(self) -> None:
        """Within one database an oid comes back only after the counter wraps
        around; the new table's columns are not the old one's attnums."""
        diff = diff_catalog(
            {"shop.orders": _state()},
            {"hr.payslips": _state("hr.payslips", columns={"7": "payslip_id", "8": "amount"})},
        )

        assert not diff.renamed
        assert diff.added == ["hr.payslips"]
        assert diff.missing == ["shop.orders"]

    @pytest.mark.parametrize("columns", [
        ({"1": "id"}, {"1": "key"}),
        ({"1": "order_id", "2": "status"}, {"1": "purchase_id", "2": "state"}),
        ({"1": "a", "2": "b", "3": "c"}, {"1": "x", "2": "y", "3": "z", "4": "added"}),
    ])
    def test_a_table_renamed_with_every_column_is_one_rename(self, columns) -> None:
        before, after = columns
        diff = diff_catalog(
            {"shop.orders": _state(columns=before)},
            {"shop.purchases": _state("shop.purchases", columns=after)},
        )

        assert diff.renamed == {"shop.orders": "shop.purchases"}
        assert not (diff.added or diff.missing)
        assert diff.changed["shop.purchases"].column_renames == {
            name: after[attnum] for attnum, name in before.items()
        }

    def test_a_renamed_table_with_no_known_columns_is_not_followed(self) -> None:
        diff = diff_catalog(
            {"shop.orders": _state(columns={})},
            {"shop.purchases": _state("shop.purchases", columns={})},
        )

        assert not diff.renamed

    def test_states_saved_without_oids_never_match_as_renames(self) -> None:
        diff = diff_catalog(
            {"shop.orders": _state(oid=0)},
            {"shop.purchases": _state("shop.purchases", oid=0)},
        )

        assert diff.added == ["shop.purchases"]
        assert diff.missing == ["shop.orders"]


class TestNamesThatMoveBetweenTables:
    """A table is followed by its oid, also when its name is not free."""

    def test_two_tables_that_swapped_names_keep_their_records(self) -> None:
        diff = diff_catalog(
            {"shop.a": _state("shop.a", oid=1), "shop.b": _state("shop.b", oid=2)},
            {"shop.a": _state("shop.a", oid=2), "shop.b": _state("shop.b", oid=1)},
        )

        assert diff.renamed == {"shop.a": "shop.b", "shop.b": "shop.a"}
        assert not (diff.added or diff.missing or diff.replaced)

    def test_a_table_renamed_into_the_name_of_one_dropped_replaces_it(self) -> None:
        # DROP TABLE b; ALTER TABLE a RENAME TO b
        diff = diff_catalog(
            {"shop.a": _state("shop.a", oid=1), "shop.b": _state("shop.b", oid=2)},
            {"shop.b": _state("shop.b", oid=1)},
        )

        assert diff.renamed == {"shop.a": "shop.b"}
        assert diff.replaced == ["shop.b"]
        assert not (diff.added or diff.missing)

    def test_a_new_table_under_a_renamed_tables_old_name_is_added(self) -> None:
        # ALTER TABLE a RENAME TO a_old; CREATE TABLE a (...)
        diff = diff_catalog(
            {"shop.a": _state("shop.a", oid=1)},
            {"shop.a_old": _state("shop.a_old", oid=1), "shop.a": _state("shop.a", oid=3)},
        )

        assert diff.renamed == {"shop.a": "shop.a_old"}
        assert diff.added == ["shop.a"]
        assert not (diff.missing or diff.replaced)

    def test_a_chain_of_renames_is_followed(self) -> None:
        # ALTER TABLE b RENAME TO c; ALTER TABLE a RENAME TO b
        diff = diff_catalog(
            {"shop.a": _state("shop.a", oid=1), "shop.b": _state("shop.b", oid=2)},
            {"shop.b": _state("shop.b", oid=1), "shop.c": _state("shop.c", oid=2)},
        )

        assert diff.renamed == {"shop.a": "shop.b", "shop.b": "shop.c"}
        assert not (diff.added or diff.missing or diff.replaced)

    def test_a_table_rebuilt_in_place_is_still_matched_by_its_name(self) -> None:
        # Its old oid is nowhere: dropped and created again under the name.
        diff = diff_catalog(
            {"shop.a": _state("shop.a", oid=1)},
            {"shop.a": _state("shop.a", oid=5, storage_id="5")},
        )

        assert list(diff.changed) == ["shop.a"]
        assert diff.changed["shop.a"].previous_fqn is None
        assert not (diff.added or diff.missing or diff.replaced)

    def test_an_unreadable_table_is_never_taken_for_a_rename_source(self) -> None:
        diff = diff_catalog(
            {"shop.a": _state("shop.a", oid=1)},
            {"shop.b": _state("shop.b", oid=1)},
            unreadable={"shop.a"},
        )

        assert not diff.renamed
        assert diff.added == ["shop.b"]
        assert not diff.missing


class TestStructure:
    def test_changed_columns(self) -> None:
        diff = diff_catalog({"shop.orders": _state()}, {"shop.orders": _state(column_hash="other")})

        change = diff.changed["shop.orders"]
        assert change.structure and not change.data

    def test_a_constraint_added_without_touching_a_row(self) -> None:
        diff = diff_catalog({"shop.orders": _state()}, {"shop.orders": _state(constraint_hash="other")})

        change = diff.changed["shop.orders"]
        assert change.structure and not change.data

    def test_a_renamed_column_is_followed_by_its_attnum(self) -> None:
        diff = diff_catalog(
            {"shop.orders": _state()},
            {"shop.orders": _state(column_hash="other", columns={"1": "order_id", "2": "state"})},
        )

        assert diff.changed["shop.orders"].column_renames == {"status": "state"}

    def test_a_dropped_and_an_added_column_are_not_a_rename(self) -> None:
        diff = diff_catalog(
            {"shop.orders": _state()},
            {"shop.orders": _state(column_hash="other", columns={"1": "order_id", "3": "state"})},
        )

        assert diff.changed["shop.orders"].column_renames == {}

    def test_column_numbers_of_a_recreated_table_say_nothing(self) -> None:
        diff = diff_catalog(
            {"shop.orders": _state()},
            {"shop.orders": _state(oid=200, column_hash="other", columns={"1": "order_id", "2": "state"})},
        )

        assert diff.changed["shop.orders"].column_renames == {}

    def test_a_renamed_table_can_have_renamed_columns_too(self) -> None:
        diff = diff_catalog(
            {"shop.orders": _state()},
            {"shop.purchases": _state("shop.purchases", column_hash="other", columns={"1": "id", "2": "status"})},
        )

        assert diff.changed["shop.purchases"].column_renames == {"order_id": "id"}


class TestNamesOnly:
    """A structure that changed only in names: what was extracted about it still holds."""

    def test_a_renamed_table(self) -> None:
        diff = diff_catalog(
            {"shop.orders": _state(shape="s1")},
            {"shop.purchases": _state("shop.purchases", shape="s1")},
        )

        assert diff.changed["shop.purchases"].names_only

    def test_a_renamed_column(self) -> None:
        diff = diff_catalog(
            {"shop.orders": _state(shape="s1")},
            {"shop.orders": _state(column_hash="other", columns={"1": "id", "2": "status"}, shape="s1")},
        )

        change = diff.changed["shop.orders"]
        assert change.column_renames == {"order_id": "id"}
        assert change.structure and change.names_only

    def test_a_constraint_or_a_referenced_table_renamed(self) -> None:
        # Its definition names the constraint and the table it references.
        diff = diff_catalog(
            {"shop.orders": _state(shape="s1")},
            {"shop.orders": _state(constraint_hash="other", shape="s1")},
        )

        assert diff.changed["shop.orders"].names_only

    def test_a_renamed_table_whose_rows_changed_too(self) -> None:
        diff = diff_catalog(
            {"shop.orders": _state(shape="s1")},
            {"shop.purchases": _state("shop.purchases", shape="s1", n_tup_ins=9)},
        )

        change = diff.changed["shop.purchases"]
        assert change.names_only and change.data

    def test_a_renamed_table_whose_columns_changed_too_is_restructured(self) -> None:
        diff = diff_catalog(
            {"shop.orders": _state(shape="s1")},
            {"shop.purchases": _state("shop.purchases", column_hash="other", shape="s2")},
        )

        change = diff.changed["shop.purchases"]
        assert change.structure and not change.names_only

    def test_a_state_saved_without_a_shape_is_restructured(self) -> None:
        diff = diff_catalog(
            {"shop.orders": _state()},
            {"shop.purchases": _state("shop.purchases", shape="s1")},
        )

        assert not diff.changed["shop.purchases"].names_only

    def test_a_table_recreated_under_its_name_is_restructured(self) -> None:
        # Another table to the database, whatever its columns look like.
        diff = diff_catalog(
            {"shop.orders": _state(shape="s1")},
            {"shop.orders": _state(oid=200, constraint_hash="other", shape="s1")},
        )

        assert not diff.changed["shop.orders"].names_only

    def test_a_rename_whose_sync_failed_is_finished_next_time(self) -> None:
        # Saved under the new name with the old content, and where it came from.
        diff = diff_catalog(
            {"shop.purchases": _state("shop.purchases", shape="s1", renamed_from="shop.orders")},
            {"shop.purchases": _state("shop.purchases", shape="s1")},
        )

        change = diff.changed["shop.purchases"]
        assert change.rename_pending and change.structure and change.names_only
        # Its records moved already: nothing to move, nor to take for a rename.
        assert change.previous_fqn is None and diff.renamed == {}

    def test_rows_alone_are_not_a_change_of_names(self) -> None:
        diff = diff_catalog({"shop.orders": _state(shape="s1")}, {"shop.orders": _state(shape="s1", n_tup_ins=9)})

        assert not diff.changed["shop.orders"].names_only


class TestRows:
    def test_written_rows(self) -> None:
        diff = diff_catalog({"shop.orders": _state()}, {"shop.orders": _state(n_tup_ins=6)})

        change = diff.changed["shop.orders"]
        assert change.data and not change.structure

    def test_a_truncate_moves_no_counter_but_changes_the_storage(self) -> None:
        diff = diff_catalog({"shop.orders": _state()}, {"shop.orders": _state(storage_id="101")})

        assert diff.changed["shop.orders"].data

    def test_reset_statistics_count_as_changed_rows(self) -> None:
        diff = diff_catalog({"shop.orders": _state()}, {"shop.orders": _state(n_tup_ins=0)})

        assert diff.changed["shop.orders"].data

    def test_a_changed_row_limit_re_reads_the_rows(self) -> None:
        diff = diff_catalog({"shop.orders": _state(row_limit=1000)}, {"shop.orders": _state(row_limit=50)})

        change = diff.changed["shop.orders"]
        assert change.data and not change.structure

    def test_a_changed_sample_on_a_standby(self) -> None:
        diff = diff_catalog(
            {"shop.orders": _state(sample_hash="a")}, {"shop.orders": _state(sample_hash="b")},
        )

        assert diff.changed["shop.orders"].data

    def test_only_an_analyze_is_not_a_change(self) -> None:
        diff = diff_catalog({"shop.orders": _state()}, {"shop.orders": _state(analyze_count=1)})

        assert not diff.changed
        assert diff.reanalyzed == ["shop.orders"]

    def test_a_live_row_estimate_alone_is_nothing(self) -> None:
        diff = diff_catalog({"shop.orders": _state()}, {"shop.orders": _state(n_live_tup=99)})

        assert not (diff.changed or diff.reanalyzed)


class TestRevision:
    def test_a_rename_changes_the_revision_and_the_structure(self) -> None:
        before, after = _state(), _state("shop.purchases")

        assert before.revision() != after.revision()
        assert before.structure() != after.structure()

    def test_written_rows_change_the_revision_only(self) -> None:
        before, after = _state(), _state(n_tup_ins=6)

        assert before.revision() != after.revision()
        assert before.structure() == after.structure()

    def test_a_truncate_changes_the_revision_only(self) -> None:
        before, after = _state(), _state(storage_id="101")

        assert before.revision() != after.revision()
        assert before.structure() == after.structure()

    def test_the_row_limit_changes_the_revision_only(self) -> None:
        before, after = _state(row_limit=1000), _state(row_limit=50)

        assert before.revision() != after.revision()
        assert before.structure() == after.structure()

    def test_a_constraint_changes_both(self) -> None:
        before, after = _state(), _state(constraint_hash="other")

        assert before.revision() != after.revision()
        assert before.structure() != after.structure()

    def test_the_legacy_revision_is_the_one_version_3_wrote(self) -> None:
        state = _state(sample_hash="abc")

        assert state.legacy_revision() == hashlib.md5(b"cols:5:0:0:abc").hexdigest()

    def test_an_analyze_or_a_row_estimate_changes_neither(self) -> None:
        before, after = _state(), _state(analyze_count=3, n_live_tup=42)

        assert before.revision() == after.revision()
        assert before.structure() == after.structure()
