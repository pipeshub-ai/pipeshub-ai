"""A MariaDB table as one sync saw it, for the next sync to compare against.

A table is followed across a rename by InnoDB's ``TABLE_ID`` when both syncs
could read it (it needs PROCESS); otherwise a table whose name vanished is
paired with one whose name appeared when their columns, keys and foreign keys
are the same (the fingerprint). ``CREATE_TIME`` is no help: a rename changes it.

The revision (``structure:content:max_rows``) is what phase 7 saved, so a state
from then still compares: it only lacks what renames need, until the table is
seen again.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from app.connectors.core.sql.catalog_diff import (
    TableChange,
    names_explained,
    ordinal_column_renames,
    ordinal_types_kept,
)
from app.connectors.sources.mariadb.tables import structure_hash, table_fqn

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Hashable, Mapping

    from app.sources.external.mariadb.mariadb_ import ForeignKeyInfo

SELF = "<self>"


def _digest(value: Any) -> str:  # noqa: ANN401
    return hashlib.md5(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class MariaDBTableState(BaseModel):
    # The revision the table's record was written with; "" for a table whose
    # record could not be removed (it is tried again).
    revision: str = ""
    # False when its column records hold no current profile: they are
    # rewritten next sync, without the table being re-indexed.
    profiled: bool = False
    # Profiles of this revision that failed in a row; at the cap the table
    # waits for its next change.
    profile_failures: int = 0
    # InnoDB's id: kept by RENAME, new after a rebuild or TRUNCATE. None when
    # it could not be read (no PROCESS, another engine, a partitioned table).
    table_id: int | None = None
    # [ordinal, name, COLUMN_TYPE] per column, in ordinal order.
    columns: list[tuple[int, str, str]] = Field(default_factory=list)
    # Types and key flags by ordinal, foreign keys by target id: no names.
    shape: str = ""
    # The structure hash without constraint names and with references to
    # itself as "<self>": the same for the table under any name.
    fingerprint: str = ""
    # Set when a run moved this table's records here and did not finish
    # writing it: the name they moved from.
    renamed_from: str = ""
    # The id of the record a run moved here, saved before the move.
    moved_record_id: str = ""

    @classmethod
    def of(
        cls,
        table: Any,  # noqa: ANN401 - MariaDBTable; importing it would be circular
        *,
        max_rows: int,
        table_id: int | None = None,
        id_of: Callable[[str], int | None] = lambda fqn: None,
    ) -> MariaDBTableState:
        """The state of a table read from the catalog.

        ``id_of`` gives a referenced table's id, so this table's shape stays
        when a table it references is renamed (InnoDB renames the reference).
        """
        columns = [column for column in table.columns if column.name]
        by_name = {column.name: position + 1 for position, column in enumerate(columns)}
        own_fqn = table.fqn

        def target(fk: ForeignKeyInfo) -> str:
            return table_fqn(fk.foreign_database or table.database_name, fk.foreign_table_name)

        def unnamed(fk: ForeignKeyInfo) -> ForeignKeyInfo:
            # A rename renames generated constraint names too (staff_ibfk_1 -> people_ibfk_1).
            update: dict[str, str] = {"constraint_name": ""}
            if target(fk) == own_fqn:
                update.update(foreign_database="", foreign_table_name=SELF)
            return fk.model_copy(update=update)

        return cls(
            revision=table.revision(max_rows),
            table_id=table_id,
            columns=[(by_name[c.name], c.name, c.column_type or c.data_type or "") for c in columns],
            shape=_digest([
                [(by_name[c.name], c.column_type or c.data_type, c.nullable, c.is_primary_key, c.is_unique,
                  c.is_part_of_composite_primary_key, c.is_part_of_composite_unique) for c in columns],
                # A column can carry several keys, to an id (int) or a name (str): sort by text.
                sorted((
                    (by_name.get(fk.column_name), SELF if target(fk) == own_fqn else (id_of(target(fk)) or target(fk)),
                     fk.foreign_column_name)
                    for fk in table.foreign_keys
                ), key=repr),
                [by_name.get(name) for name in table.primary_keys],
            ]),
            # A table whose columns could not be seen looks like every other such table.
            fingerprint=structure_hash(
                table.columns, table.primary_keys, [unnamed(fk) for fk in table.foreign_keys],
            ) if columns else "",
        )

    # The revision is ``structure:content:max_rows``, the structure an md5.

    @property
    def structure(self) -> str:
        return self.revision.partition(":")[0]

    @property
    def rows_marker(self) -> str:
        """What changes when the indexed rows do: the content marker and the row limit."""
        return self.revision.partition(":")[2]

    @property
    def names_by_ordinal(self) -> dict[int, str]:
        return {ordinal: name for ordinal, name, _ in self.columns}

    @property
    def types_by_ordinal(self) -> dict[int, str]:
        return {ordinal: data_type for ordinal, _, data_type in self.columns}

    def other_table_than(self, before: MariaDBTableState) -> bool:
        """Known to be another table: both ids read, and different."""
        return self.table_id is not None and before.table_id is not None and self.table_id != before.table_id

    def same_table_as(self, before: MariaDBTableState) -> bool:
        return ordinal_types_kept(before.types_by_ordinal, self.types_by_ordinal)

    def looks_like(self, before: MariaDBTableState) -> bool:
        """``same_table_as``, and most of the column names kept too."""
        before_names = set(before.names_by_ordinal.values())
        kept = before_names & set(self.names_by_ordinal.values())
        return self.same_table_as(before) and len(kept) * 2 > len(before_names)

    def change_since(self, before: MariaDBTableState, *, old_fqn: str, new_fqn: str) -> TableChange:
        same_table = not self.other_table_than(before)
        column_renames = ordinal_column_renames(
            before.types_by_ordinal, self.types_by_ordinal, before.names_by_ordinal, self.names_by_ordinal,
        ) if same_table and before.columns else {}
        rename_pending = bool(before.renamed_from) and old_fqn == new_fqn
        structure = (
            old_fqn != new_fqn
            or self.structure != before.structure
            or bool(column_renames)
            or rename_pending
        )
        names_only = (
            structure and same_table and bool(before.shape) and before.shape == self.shape
            and names_explained(before.names_by_ordinal, self.names_by_ordinal, column_renames)
        )
        data = self.rows_marker != before.rows_marker or self.other_table_than(before)
        return TableChange(
            previous_fqn=old_fqn if old_fqn != new_fqn else None,
            structure=structure,
            data=data,
            column_renames=column_renames,
            names_only=names_only,
            rename_pending=rename_pending,
        )


@dataclass(frozen=True)
class Keyed:
    """A table state with the key ``diff_catalog`` follows it by in this comparison."""

    state: MariaDBTableState
    key: tuple[str, Hashable] | None = None

    @property
    def identity(self) -> Hashable | None:
        return self.key

    def same_table_as(self, before: Keyed) -> bool:
        # A fingerprint match is the same columns and keys: nothing left to check.
        return (self.key is not None and self.key[0] == "fingerprint") or self.state.same_table_as(before.state)

    def change_since(self, before: Keyed, *, old_fqn: str, new_fqn: str) -> TableChange:
        change = self.state.change_since(before.state, old_fqn=old_fqn, new_fqn=new_fqn)
        if old_fqn != new_fqn and self.key is not None and self.key[0] == "fingerprint":
            # Maybe a lookalike (DROP plus CREATE of the same DDL): the record
            # and notes follow, and it is reported as restructured.
            return replace(change, names_only=False)
        return change

    def reanalyzed_since(self, before: Keyed) -> bool:
        return False


# Ids are not trusted in a comparison when most tables that kept their name
# have a new id that nothing explains: a dump and reload, a failover to a
# server built from a dump, or a mass TRUNCATE hands out new ids, and an old id
# may then name another table. A new id is explained when it was a stored
# table's that this one looks like (a swap, or a rename into a dropped
# table's name), or when the old id went on under another name to a table
# that looks like this one (a rotation). An old id on a table that does not
# look like its stored owner distrusts them all.
_MIN_IDS_CHANGED_TO_DISTRUST = 2


def ids_trusted(stored: Mapping[str, MariaDBTableState], current: Mapping[str, MariaDBTableState]) -> bool:
    stored_by_id = {state.table_id: state for state in stored.values() if state.table_id is not None}
    current_by_id = {state.table_id: state for state in current.values() if state.table_id is not None}
    pairs = [
        (stored[fqn], current[fqn]) for fqn in stored.keys() & current.keys()
        if stored[fqn].table_id is not None and current[fqn].table_id is not None
    ]
    unexplained = 0
    for before, after in pairs:
        if before.table_id == after.table_id:
            continue
        taken_from = stored_by_id.get(after.table_id)
        if taken_from is not None and not after.looks_like(taken_from):
            # InnoDB hands an id out once per server: an old id on another
            # table means the ids were handed out again.
            return False
        moved_on = current_by_id.get(before.table_id)
        if taken_from is None and not (moved_on is not None and moved_on.looks_like(before)):
            # Neither swapped in nor rotated out (RENAME t TO t_1 plus a new t).
            unexplained += 1
    return unexplained < _MIN_IDS_CHANGED_TO_DISTRUST or unexplained * 2 <= len(pairs)


def rename_keys(
    stored: Mapping[str, MariaDBTableState], current: Mapping[str, MariaDBTableState],
) -> tuple[dict[str, Keyed], dict[str, Keyed]]:
    """Key each table by what can follow it across a rename in this comparison.

    - A stored table is keyed by its id when the current read has that id,
      and a current table by its id when the stored state has it.
    - Otherwise a stored table whose name is gone, or a current table whose
      name is new, is keyed by its fingerprint, but only when its id is
      missing or the other side read no ids: ids read on both sides are never
      overruled by a shape (a rename plus a rebuild then reads as drop plus add).
    - The rest have no key and are matched by name: a table still under its
      name is never paired with another by its shape.

    Ids that are not trusted (``ids_trusted``) count as missing on both sides.
    """
    if not ids_trusted(stored, current):
        stored = {fqn: s.model_copy(update={"table_id": None}) for fqn, s in stored.items()}
        current = {fqn: s.model_copy(update={"table_id": None}) for fqn, s in current.items()}
    stored_ids = {s.table_id for s in stored.values() if s.table_id is not None}
    current_ids = {s.table_id for s in current.values() if s.table_id is not None}

    def key(fqn: str, state: MariaDBTableState, ids: set[int], others: Mapping[str, Any]) -> tuple | None:
        if state.table_id is not None and state.table_id in ids:
            return ("id", state.table_id)
        id_missing = state.table_id is None or not ids
        if id_missing and fqn not in others and state.fingerprint:
            return ("fingerprint", state.fingerprint)
        return None

    return (
        {fqn: Keyed(state, key(fqn, state, current_ids, current)) for fqn, state in stored.items()},
        {fqn: Keyed(state, key(fqn, state, stored_ids, stored)) for fqn, state in current.items()},
    )


def checkpoint_moves(
    stored: Mapping[str, MariaDBTableState],
    moved: Mapping[str, str],
    record_ids: Mapping[str, str],
    *,
    shape_paired: Collection[str] = (),
) -> dict[str, MariaDBTableState]:
    """The stored state with each table about to move under its new name.

    Every old entry is taken out before any is put back, so swaps and chains
    land where they go. A table paired by shape loses its stored shape, so the
    sync that finishes its move cannot read it as names-only.
    """
    states = dict(stored)
    entries = {old: states.pop(old) for old in moved if old in states}
    for old_fqn, new_fqn in moved.items():
        if old_fqn in entries:
            update: dict[str, Any] = {"renamed_from": old_fqn, "moved_record_id": record_ids.get(old_fqn, "")}
            if old_fqn in shape_paired:
                update["shape"] = ""
            states[new_fqn] = entries[old_fqn].model_copy(update=update)
    return states
