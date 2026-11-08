"""Primary and unique key flags on columns, from a table's key definitions.

A single-column primary key is also unique. A composite key marks each of its
columns as part of one and leaves them not unique on their own, since no one
of them identifies a row. Readers use these flags to tell keys from
attributes.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class KeyDefinition:
    kind: Literal["primary", "unique"]
    # In key order.
    columns: tuple[str, ...]


@dataclass
class KeyFlags:
    is_primary_key: bool = False
    is_unique: bool = False
    is_part_of_composite_primary_key: bool = False
    is_part_of_composite_unique: bool = False


def key_flags(column_names: Iterable[str], keys: Iterable[KeyDefinition]) -> dict[str, KeyFlags]:
    """Flags for every named column; a key column the table doesn't have is ignored."""
    flags = {name: KeyFlags() for name in column_names if name}
    for key in keys:
        if not key.columns:
            continue
        composite = len(key.columns) >= 2
        if key.kind == "primary":
            for name in key.columns:
                if name not in flags:
                    continue
                flags[name].is_primary_key = True
                if composite:
                    flags[name].is_part_of_composite_primary_key = True
                else:
                    flags[name].is_unique = True
        elif key.kind != "unique":
            continue
        elif composite:
            for name in key.columns:
                if name in flags:
                    flags[name].is_part_of_composite_unique = True
        elif key.columns[0] in flags:
            flags[key.columns[0]].is_unique = True
    return flags


def primary_key_columns(keys: Iterable[KeyDefinition]) -> list[str]:
    """The primary key's columns in key order; empty when there is none."""
    for key in keys:
        if key.kind == "primary" and key.columns:
            return list(key.columns)
    return []
