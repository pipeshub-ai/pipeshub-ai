"""The shared catalog diff, driven through its protocol with a minimal state."""

from dataclasses import dataclass, field

from app.connectors.core.sql.catalog_diff import TableChange, diff_catalog


@dataclass(frozen=True)
class State:
    identity: str | None
    rows: int = 0
    columns: tuple[str, ...] = ("id",)
    stats: int = 0
    same: bool = True
    renamed_from: str = ""
    extra: dict = field(default_factory=dict)

    def same_table_as(self, before: "State") -> bool:
        return self.same

    def change_since(self, before: "State", *, old_fqn: str, new_fqn: str) -> TableChange:
        structure = old_fqn != new_fqn or self.columns != before.columns
        return TableChange(
            previous_fqn=old_fqn if old_fqn != new_fqn else None,
            structure=structure,
            data=self.rows != before.rows,
            names_only=structure and self.columns == before.columns,
        )

    def reanalyzed_since(self, before: "State") -> bool:
        return self.stats != before.stats


def test_added_missing_unchanged_changed_reanalyzed() -> None:
    stored = {"a": State("1"), "b": State("2"), "c": State("3"), "d": State("4")}
    current = {"a": State("1"), "b": State("2", rows=5), "c": State("3", stats=1), "e": State("5")}
    diff = diff_catalog(stored, current)
    assert diff.added == ["e"]
    assert diff.missing == ["d"]
    assert diff.unchanged == ["a"]
    assert list(diff.changed) == ["b"] and diff.changed["b"].data
    assert diff.reanalyzed == ["c"]


def test_rename_follows_the_identity() -> None:
    diff = diff_catalog({"old": State("1")}, {"new": State("1")})
    assert diff.renamed == {"old": "new"}
    assert diff.changed["new"].names_only
    assert diff.added == diff.missing == []


def test_swap_of_two_names() -> None:
    diff = diff_catalog({"a": State("1"), "b": State("2")}, {"a": State("2"), "b": State("1")})
    assert diff.renamed == {"a": "b", "b": "a"}


def test_rename_into_a_dropped_tables_name_replaces_it() -> None:
    diff = diff_catalog({"old": State("1"), "new": State("2")}, {"new": State("1")})
    assert diff.renamed == {"old": "new"}
    assert diff.replaced == ["new"]


def test_same_identity_but_not_the_same_table_is_dropped_and_added() -> None:
    diff = diff_catalog({"old": State("1")}, {"new": State("1", same=False)})
    assert diff.renamed == {}
    assert (diff.missing, diff.added) == (["old"], ["new"])


def test_recreated_under_the_same_name_stays_that_table() -> None:
    diff = diff_catalog({"t": State("1")}, {"t": State("9", rows=1)})
    assert list(diff.changed) == ["t"] and diff.added == []


def test_an_identity_two_current_tables_share_matches_no_rename() -> None:
    # A schema clone gives every table the same creation time.
    stored = {"old": State("clone-time")}
    current = {"x": State("clone-time"), "y": State("clone-time")}
    diff = diff_catalog(stored, current)
    assert diff.renamed == {}
    assert diff.missing == ["old"] and diff.added == ["x", "y"]


def test_an_identity_two_stored_tables_share_matches_no_rename() -> None:
    stored = {"a": State("t"), "b": State("t")}
    diff = diff_catalog(stored, {"c": State("t")})
    assert diff.renamed == {}


def test_no_identity_matches_by_name_only() -> None:
    diff = diff_catalog({"old": State(None)}, {"new": State(None)})
    assert diff.renamed == {} and (diff.missing, diff.added) == (["old"], ["new"])


def test_unreadable_tables_are_neither_missing_nor_changed() -> None:
    diff = diff_catalog({"a": State("1"), "b": State("2")}, {}, unreadable={"a", "b"})
    assert diff.missing == [] and diff.changed == {} and diff.unchanged == []
    assert diff.added == []
