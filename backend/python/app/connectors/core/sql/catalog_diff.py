"""What changed in a SQL catalog between two syncs, for any engine.

A sync saves one state per table; the next sync reads the catalog again and
``diff_catalog`` compares the two. Catalogs keep no history a read-only role
can see, so comparing snapshots is how a dropped, renamed or altered table is
found.

A table is followed across a rename by its engine's identity (PostgreSQL's
``pg_class.oid``, Snowflake's creation time), confirmed by the engine's
``same_table_as``, so a table is followed even when its old name now belongs
to another table, or two tables swapped names. A table whose identity is gone
but whose name is still there was dropped and created again under that name;
it is still that table to the people who query it, so its record and its
notes stay.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, TypeVar

if TYPE_CHECKING:
    from collections.abc import Hashable, Mapping


@dataclass(frozen=True)
class TableChange:
    """How one table that exists in both snapshots differs."""

    # Its name in the previous snapshot, when that was different.
    previous_fqn: str | None = None
    # Name, columns or constraints changed.
    structure: bool = False
    # Rows changed.
    data: bool = False
    # Previous column name -> current name.
    column_renames: dict[str, str] = field(default_factory=dict)
    # The structure changed only in names: of the table, its columns, its
    # constraints or a table it references. What was extracted about it
    # still holds, under the new names.
    names_only: bool = False
    # Its records moved to this name in a run whose sync of it then failed.
    rename_pending: bool = False


@dataclass
class CatalogDiff:
    added: list[str] = field(default_factory=list)
    # Stored tables the catalog no longer shows under any name. Candidates
    # only: the caller confirms each against a fresh table listing.
    missing: list[str] = field(default_factory=list)
    # Stored tables that are gone, whose name a renamed table took: dropped for
    # certain, and to be removed before that table moves into the name.
    replaced: list[str] = field(default_factory=list)
    # Tables to sync again, by their current name.
    changed: dict[str, TableChange] = field(default_factory=dict)
    # Only the engine's statistics moved: column profiles are stale, rows and
    # structure are not.
    reanalyzed: list[str] = field(default_factory=list)
    # Matched by name and the same in every way that counts.
    unchanged: list[str] = field(default_factory=list)

    @property
    def renamed(self) -> dict[str, str]:
        """Previous name -> current name."""
        return {
            change.previous_fqn: fqn
            for fqn, change in self.changed.items()
            if change.previous_fqn
        }


class CatalogTableState(Protocol):
    """What ``diff_catalog`` needs from an engine's saved table state."""

    @property
    def identity(self) -> Hashable | None:
        """What a rename leaves alone; None when the engine could not read it."""

    def same_table_as(self, before: CatalogTableState) -> bool:
        """Whether this state and ``before``, sharing an identity, are one table."""

    def change_since(self, before: CatalogTableState, *, old_fqn: str, new_fqn: str) -> TableChange:
        """How the table changed since ``before`` (its state under ``old_fqn``)."""

    def reanalyzed_since(self, before: CatalogTableState) -> bool:
        """Whether only the engine's statistics moved."""


State = TypeVar("State", bound=CatalogTableState)


def _unique_identities(states: Mapping[str, CatalogTableState]) -> dict[Hashable, str]:
    """Identity -> table, for identities exactly one table has.

    A shared identity (tables cloned together share a creation time) cannot
    say which table is which, so it matches no rename.
    """
    counts = Counter(state.identity for state in states.values() if state.identity)
    return {
        state.identity: fqn
        for fqn, state in states.items()
        if state.identity and counts[state.identity] == 1
    }


def diff_catalog(
    stored: Mapping[str, State],
    current: Mapping[str, State],
    unreadable: set[str] | None = None,
) -> CatalogDiff:
    """Compare the saved table states with the ones just read.

    ``unreadable`` are stored tables that could not be read this run:
    unknown, so neither missing nor changed. They must not be in ``current``.
    """
    unreadable = unreadable or set()
    diff = CatalogDiff()

    current_by_identity = _unique_identities(current)
    stored_identities = Counter(state.identity for state in stored.values() if state.identity)
    renamed: dict[str, str] = {}
    for old_fqn in sorted(stored.keys() - unreadable):
        before = stored[old_fqn]
        if not before.identity or stored_identities[before.identity] != 1:
            continue
        new_fqn = current_by_identity.get(before.identity)
        if new_fqn is not None and new_fqn != old_fqn and current[new_fqn].same_table_as(before):
            renamed[old_fqn] = new_fqn
    taken = set(renamed.values())

    unmatched = stored.keys() - renamed.keys() - unreadable
    same_name = (unmatched & current.keys()) - taken
    diff.replaced = sorted(unmatched & taken)
    diff.missing = sorted(unmatched - current.keys())
    # Includes a table under a renamed table's old name: another table, created since.
    diff.added = sorted(current.keys() - taken - same_name)

    pairs = [(fqn, fqn) for fqn in same_name]
    pairs.extend(renamed.items())
    for old_fqn, new_fqn in sorted(pairs, key=lambda pair: pair[1]):
        before, after = stored[old_fqn], current[new_fqn]
        change = after.change_since(before, old_fqn=old_fqn, new_fqn=new_fqn)
        if change.structure or change.data:
            diff.changed[new_fqn] = change
        elif after.reanalyzed_since(before):
            diff.reanalyzed.append(new_fqn)
        else:
            diff.unchanged.append(new_fqn)
    return diff


# Column rules for states that keep ``{ordinal: type}`` and ``{ordinal: name}``.
# Ordinals are renumbered when a column is dropped, so they are compared with
# their types, never alone.


def ordinal_types_kept(before: Mapping[int, str], after: Mapping[int, str]) -> bool:
    """One table under two names: half or more of the previous ordinals keep their type."""
    if not before:
        return False
    kept = sum(1 for ordinal, data_type in before.items() if after.get(ordinal) == data_type)
    return kept * 2 >= len(before)


def ordinal_column_renames(
    before_types: Mapping[int, str],
    after_types: Mapping[int, str],
    before_names: Mapping[int, str],
    after_names: Mapping[int, str],
) -> dict[str, str]:
    """Previous column name -> current, only when unambiguous.

    Every ordinal keeps its type, the old name is no longer a column and the
    new name was not one before: a drop plus an add, or two columns swapping
    names, is never a rename.
    """
    if before_types != after_types:
        return {}
    return {
        old_name: after_names[ordinal]
        for ordinal, old_name in before_names.items()
        if after_names[ordinal] != old_name
        and old_name not in after_names.values()
        and after_names[ordinal] not in before_names.values()
    }


def names_explained(
    before_names: Mapping[int, str], after_names: Mapping[int, str], renames: Mapping[str, str],
) -> bool:
    """Whether the name at every ordinal is the old one or its recognised rename."""
    return before_names.keys() == after_names.keys() and all(
        renames.get(name, name) == after_names[ordinal] for ordinal, name in before_names.items()
    )
