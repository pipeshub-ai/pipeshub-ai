"""Moving the records of renamed tables to their new names, for any SQL engine.

Only the decisions live here; each connector reads and writes its own records.
Keeping a renamed table's record keeps what hangs off it: the notes people
wrote and anything else linked to the table and its columns.
"""

from collections.abc import Callable, Mapping
from typing import Any

from app.models.entities import Record

# old table id -> (its stored record, its stored column records)
Moves = dict[str, tuple[Record, list[Record]]]


def hold_renames(renamed: Mapping[str, str], moves: Moves, unmoved: set[str], blocked: set[str]) -> None:
    """Hold back every rename into a name that will not be free.

    A name is not free when it is ``blocked`` (a dropped table there could
    not be removed) or when the table under it could not move out. Holding
    one rename can block the next in a chain, so this repeats until stable.
    ``moves`` and ``unmoved`` are updated in place.
    """
    held = True
    while held:
        held = False
        for old_fqn, new_fqn in renamed.items():
            if new_fqn in unmoved:
                continue
            if new_fqn in blocked or (new_fqn in renamed and renamed[new_fqn] in unmoved):
                unmoved.add(new_fqn)
                moves.pop(old_fqn, None)
                held = True


def rename_updates(
    moves: Moves,
    renamed: Mapping[str, str],
    column_fqn: Callable[[str, str], str],
) -> list[dict[str, Any]]:
    """The node updates that move each table's record and its column records in one write.

    One write moves them all, so tables that swapped names, or a chain of
    renames, move together.
    """
    updates: list[dict[str, Any]] = []
    for old_fqn, (table, columns) in moves.items():
        new_fqn = renamed[old_fqn]
        updates.append({"id": table.id, "externalRecordId": new_fqn})
        updates.extend(
            {
                "id": column.id,
                "externalRecordId": column_fqn(new_fqn, column.record_name),
                "externalParentId": new_fqn,
            }
            for column in columns if column.id
        )
    return updates
