"""What changed in a PostgreSQL catalog between two syncs.

A sync saves one ``PostgresTableState`` per table; the next sync reads the
catalog again and ``diff_catalog`` compares the two. PostgreSQL keeps no
history of schema changes that a read-only role can see, so comparing snapshots
is how a dropped, renamed or altered table is found.

Tables are matched by ``pg_class.oid`` first, which a rename or a move to
another schema does not change, so a table is followed even when its old name
now belongs to another table, or two tables swapped names. A table whose oid
is gone but whose name is still there was dropped and created again under
that name (a full refresh); it is still that table to the people who query
it, so its record and its notes stay. A column is matched by
``attnum``.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, Field

from app.connectors.core.sql.catalog_diff import (  # noqa: F401 (re-exported)
    CatalogDiff,
    TableChange,
    diff_catalog,
)


class PostgresTableState(BaseModel):
    column_hash: str = ""
    # Primary, unique, foreign-key and check constraints, and unique indexes.
    # Adding or dropping one writes no row, so the counters below do not move.
    constraint_hash: str = ""
    n_tup_ins: int = 0
    n_tup_upd: int = 0
    n_tup_del: int = 0
    # An estimate that ANALYZE moves without any write, so revision() ignores it.
    n_live_tup: int = 0
    schema_name: str = ""
    table_name: str = ""
    # Hash of the sampled rows, set only on a standby, where replayed writes
    # leave the counters above unchanged.
    sample_hash: str = ""
    # ANALYZE runs. Left out of revision(): fresher pg_stats change the column
    # profiles, not the rows, so they must not re-index the table.
    analyze_count: int = 0
    # pg_class.oid: the same after a rename or a move to another schema.
    oid: int = 0
    # Changes when the table's rows are rewritten wholesale. TRUNCATE is the
    # case that matters: it moves none of the three counters.
    storage_id: str = ""
    # attnum -> column name. The keys are strings because the state is saved as JSON.
    columns: dict[str, str] = Field(default_factory=dict)
    # How many rows of the table are indexed (the "Max Rows Per Table" filter).
    row_limit: int = 0
    # Digest of the columns (by attnum) and keys with no name in them, so a
    # rename leaves it alone; empty when saved by a version without it.
    shape: str = ""
    # Set when a run moved this table's records (to this name, or its columns
    # to new names) and did not finish syncing it: the name they moved from,
    # the table's own for columns. The next run syncs it again and finishes
    # the rename, rather than moving the records a second time.
    renamed_from: str = ""

    def revision(self) -> str:
        """Changes whenever the table's indexed text would.

        The name is part of it: the text names the table and its columns, so a
        rename alone has to re-index it. So is the row limit: the text holds
        that many rows.
        """
        key = (
            f"{self.schema_name}.{self.table_name}:{self.column_hash}:{self.constraint_hash}:"
            f"{self.n_tup_ins}:{self.n_tup_upd}:{self.n_tup_del}:{self.storage_id}:{self.row_limit}"
        )
        if self.sample_hash:
            key += f":{self.sample_hash}"
        return hashlib.md5(key.encode()).hexdigest()

    def legacy_revision(self) -> str:
        """The revision versions before 4 wrote for this state, to recognise one."""
        key = f"{self.column_hash}:{self.n_tup_ins}:{self.n_tup_upd}:{self.n_tup_del}"
        if self.sample_hash:
            key += f":{self.sample_hash}"
        return hashlib.md5(key.encode()).hexdigest()

    def structure(self) -> str:
        """Changes when what the table *is* changes: its name, columns or keys.

        What is derived from a table describes this, not the rows, so it is
        worked out again only when this moves.
        """
        key = f"{self.schema_name}.{self.table_name}:{self.column_hash}:{self.constraint_hash}"
        return hashlib.md5(key.encode()).hexdigest()

    # The catalog-diff protocol (app.connectors.core.sql.catalog_diff).

    @property
    def identity(self) -> int | None:
        return self.oid or None

    def same_table_as(self, before: PostgresTableState) -> bool:
        return _same_table(before, self)

    def change_since(self, before: PostgresTableState, *, old_fqn: str, new_fqn: str) -> TableChange:
        return _table_change(old_fqn, new_fqn, before, self)

    def reanalyzed_since(self, before: PostgresTableState) -> bool:
        return self.analyze_count != before.analyze_count


def _same_table(before: PostgresTableState, after: PostgresTableState) -> bool:
    """Whether two states with one oid are one table under two names.

    A state is only compared within the database it was saved for (the
    connector checks the server and ``pg_database.oid``), where an oid names
    one table until the counter wraps around. A rename keeps its columns'
    attnums whatever they are called now, so most of those have to be there.
    """
    if not before.columns:
        return False
    kept = before.columns.keys() & after.columns.keys()
    return len(kept) * 2 >= len(before.columns)


def _table_change(
    old_fqn: str,
    new_fqn: str,
    before: PostgresTableState,
    after: PostgresTableState,
) -> TableChange:
    same_table = bool(before.oid) and before.oid == after.oid
    column_renames = _column_renames(before, after) if same_table else {}

    rename_pending = bool(before.renamed_from) and old_fqn == new_fqn
    structure = (
        old_fqn != new_fqn
        or after.column_hash != before.column_hash
        or after.constraint_hash != before.constraint_hash
        or bool(column_renames)
        or rename_pending
    )
    names_only = structure and same_table and bool(before.shape) and before.shape == after.shape
    # A counter that went down means the statistics were reset, which says
    # nothing about the rows; treated as changed rather than missed.
    data = (
        after.n_tup_ins != before.n_tup_ins
        or after.n_tup_upd != before.n_tup_upd
        or after.n_tup_del != before.n_tup_del
        or after.sample_hash != before.sample_hash
        or after.storage_id != before.storage_id
        or after.oid != before.oid
        or after.row_limit != before.row_limit
    )
    return TableChange(
        previous_fqn=old_fqn if old_fqn != new_fqn else None,
        structure=structure,
        data=data,
        column_renames=column_renames,
        names_only=names_only,
        rename_pending=rename_pending,
    )


def _column_renames(before: PostgresTableState, after: PostgresTableState) -> dict[str, str]:
    renames: dict[str, str] = {}
    for attnum, old_name in before.columns.items():
        new_name = after.columns.get(attnum)
        if new_name is not None and new_name != old_name:
            renames[old_name] = new_name
    return renames
