"""A Snowflake table as one sync saw it, for the next sync to compare against.

The state is built from the catalog read the sync makes anyway. A table is
followed across a rename by its ``CREATED`` time, confirmed by its
columns: at least half of them keep their type at the same ordinal. A column
counts as renamed only when every ordinal keeps its type, the old name is gone
and the new name is new; Snowflake renumbers ordinals when a column is
dropped.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from app.connectors.core.sql.catalog_diff import (
    TableChange,
    names_explained,
    ordinal_column_renames,
    ordinal_types_kept,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.connectors.sources.snowflake.data_fetcher import SnowflakeTable


def _digest(value: Any) -> str:  # noqa: ANN401
    return hashlib.md5(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class SnowflakeTableState(BaseModel):
    database: str = ""
    schema_name: str = ""
    table_name: str = ""
    # Kept by RENAME (and a move to another schema); new for CREATE OR REPLACE.
    created: str = ""
    # Moves on every DML and DDL.
    last_altered: str = ""
    # Column names, types, nullability and defaults, by ordinal.
    column_hash: str = ""
    # Primary, unique and foreign keys, with their columns' names.
    constraint_hash: str = ""
    # [ordinal, name, type] per column, in ordinal order: a list rather than
    # maps keeps the saved state small for accounts with many tables.
    columns: list[tuple[int, str, str]] = Field(default_factory=list)
    # Column types by ordinal and key shapes by ordinal, with no name in either:
    # a rename leaves it alone.
    shape: str = ""
    # How many rows of the table are indexed (the "Max Rows Per Table" filter).
    row_limit: int = 0
    # Set when a run moved this table's records (to this name, or its columns
    # to new names) and did not finish syncing it: the name they moved from,
    # the table's own for columns. The next run syncs it again and finishes
    # the rename, rather than moving the records a second time.
    renamed_from: str = ""
    # The id of the record a run moved here. Saved before the move, so a run
    # cut off between the two finishes the move rather than deciding it again.
    moved_record_id: str = ""

    @classmethod
    def of(
        cls,
        table: SnowflakeTable,
        *,
        row_limit: int,
        identity_of: Callable[[str], str | None] = lambda fqn: None,
    ) -> SnowflakeTableState:
        """The state of a table whose columns and keys were read.

        ``identity_of`` gives a referenced table's ``CREATED`` by FQN, so the
        shape follows a referenced table across its rename, as PostgreSQL's
        does with the referenced oid.
        """
        ordinals = {
            str(column.ordinal_position if column.ordinal_position is not None else position + 1): column
            for position, column in enumerate(table.columns)
            if column.name
        }
        by_name = {column.name: ordinal for ordinal, column in ordinals.items()}

        def key_ordinals(columns: list[str]) -> list[str | None]:
            return [by_name.get(name) for name in columns]

        keys = [
            (column.name, column.is_primary_key, column.is_unique,
             column.is_part_of_composite_primary_key, column.is_part_of_composite_unique)
            for column in ordinals.values()
        ]
        foreign_keys = sorted(
            (fk.source_column, fk.target_fqn, fk.target_column, fk.constraint_name) for fk in table.foreign_keys
        )
        # The referenced column by name: its ordinal is not in this table's
        # catalog read, and a key repointed to another column must count.
        # A column the read did not return has no ordinal (None): sort by text.
        foreign_key_shape = sorted((
            (by_name.get(fk.source_column), identity_of(fk.target_fqn) or fk.target_fqn, fk.target_column)
            for fk in table.foreign_keys
        ), key=repr)
        return cls(
            database=table.database_name,
            schema_name=table.schema_name,
            table_name=table.name,
            created=table.created_at or "",
            last_altered=table.last_altered or "",
            column_hash=_digest([
                (ordinal, column.name, column.data_type, column.nullable, column.default)
                for ordinal, column in sorted(ordinals.items(), key=lambda item: int(item[0]))
            ]),
            constraint_hash=_digest([sorted(keys), foreign_keys, table.primary_keys]),
            columns=sorted(
                (int(ordinal), column.name, column.data_type or "") for ordinal, column in ordinals.items()
            ),
            shape=_digest([
                sorted((ordinal, column.data_type, column.nullable) for ordinal, column in ordinals.items()),
                sorted(
                    (ordinal, column.is_primary_key, column.is_unique,
                     column.is_part_of_composite_primary_key, column.is_part_of_composite_unique)
                    for ordinal, column in ordinals.items()
                ),
                foreign_key_shape,
                key_ordinals(table.primary_keys),
            ]),
            row_limit=row_limit,
        )

    @property
    def names_by_ordinal(self) -> dict[int, str]:
        return {ordinal: name for ordinal, name, _ in self.columns}

    @property
    def types_by_ordinal(self) -> dict[int, str]:
        return {ordinal: data_type for ordinal, _, data_type in self.columns}

    # The catalog-diff protocol (app.connectors.core.sql.catalog_diff).

    @property
    def identity(self) -> str | None:
        return self.created or None

    def same_table_as(self, before: SnowflakeTableState) -> bool:
        """One table under two names: half or more of its ordinals keep their type."""
        return ordinal_types_kept(before.types_by_ordinal, self.types_by_ordinal)

    def change_since(self, before: SnowflakeTableState, *, old_fqn: str, new_fqn: str) -> TableChange:
        same_table = bool(before.created) and before.created == self.created
        column_renames = self._column_renames(before) if same_table else {}
        rename_pending = bool(before.renamed_from) and old_fqn == new_fqn
        structure = (
            old_fqn != new_fqn
            or self.column_hash != before.column_hash
            or self.constraint_hash != before.constraint_hash
            or bool(column_renames)
            or rename_pending
        )
        # The shape has no names, so a dropped column plus an added one of the
        # same type, or two columns swapping names, keeps it: the name at every
        # ordinal must be the old one or its recognised rename.
        names_only = (
            structure and same_table and bool(before.shape) and before.shape == self.shape
            and names_explained(before.names_by_ordinal, self.names_by_ordinal, column_renames)
        )
        data = (
            self.last_altered != before.last_altered
            or self.created != before.created
            or self.row_limit != before.row_limit
        )
        return TableChange(
            previous_fqn=old_fqn if old_fqn != new_fqn else None,
            structure=structure,
            data=data,
            column_renames=column_renames,
            names_only=names_only,
            rename_pending=rename_pending,
        )

    def reanalyzed_since(self, before: SnowflakeTableState) -> bool:
        return False

    def _column_renames(self, before: SnowflakeTableState) -> dict[str, str]:
        return ordinal_column_renames(
            before.types_by_ordinal, self.types_by_ordinal, before.names_by_ordinal, self.names_by_ordinal,
        )
