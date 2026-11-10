"""MariaDB columns as the shared column-record builder reads them, and row values as JSON."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.sources.external.mariadb.values import json_default

__all__ = ["ColumnFacts", "column_fqn", "decode_rows", "json_default", "sample_order"]

if TYPE_CHECKING:
    from app.sources.external.mariadb.mariadb_ import ColumnInfo


def column_fqn(table_fqn: str, column: str) -> str:
    """A column's id, as for every engine: the table's id, a dot, the column name as it is."""
    return f"{table_fqn}.{column}"


@dataclass(frozen=True)
class ColumnFacts:
    """A column as ``build_column_records`` reads it.

    ``data_type`` is the full ``COLUMN_TYPE`` (``int(11)``, ``varchar(64)``),
    and a default MariaDB reports as the text ``NULL`` is no default.
    ``ColumnInfo`` itself stays as the agent toolset and the structure hash
    know it.
    """
    name: str
    data_type: str | None
    nullable: bool
    default: str | None
    is_primary_key: bool
    is_unique: bool
    is_part_of_composite_primary_key: bool
    is_part_of_composite_unique: bool

    @classmethod
    def of(cls, column: ColumnInfo) -> ColumnFacts:
        default = column.default
        if isinstance(default, str) and default.upper() == "NULL":
            default = None
        return cls(
            name=column.name,
            data_type=column.column_type or column.data_type,
            nullable=bool(column.nullable),
            default=default,
            is_primary_key=column.is_primary_key,
            is_unique=column.is_unique,
            is_part_of_composite_primary_key=column.is_part_of_composite_primary_key,
            is_part_of_composite_unique=column.is_part_of_composite_unique,
        )


def decode_rows(rows: list[dict[str, Any]], columns: list[ColumnInfo]) -> list[dict[str, Any]]:
    """BIT values arrive as bytes; they are numbers."""
    bits = {c.name for c in columns if (c.data_type or "").lower() == "bit"}
    if not bits:
        return rows
    decoded = []
    for row in rows:
        values = dict(row)
        for name in bits & values.keys():
            if isinstance(values[name], (bytes, bytearray)):
                values[name] = int.from_bytes(values[name], "big")
        decoded.append(values)
    return decoded


# A sample is ordered by every column only while this cheap to sort.
ORDERED_READ_MAX_ROWS = 1_000_000


def sample_order(columns: list[ColumnInfo], primary_keys: list[str], row_estimate: int | None) -> list[str] | None:
    """The columns a sample is ordered by, so it is the same rows from one read to the next.

    The primary key; else a single-column unique key that cannot be NULL; else
    every column while the table is known to be small enough to sort; else
    none. An estimate of 0 is unknown: InnoDB can report it for a full table.
    """
    if primary_keys:
        return list(primary_keys)
    for column in columns:
        if column.is_unique and not column.nullable and column.name:
            return [column.name]
    names = [c.name for c in columns if c.name]
    if names and row_estimate and row_estimate <= ORDERED_READ_MAX_ROWS:
        return names
    return None
