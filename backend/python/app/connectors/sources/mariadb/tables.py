"""MariaDB table ids and revisions.

A table's id is ``database.table`` written as a quote-aware qualified name, so
a name containing a dot cannot collide with another. Its revision is
``structure:content``: a hash of its columns, keys and foreign keys, then its
last-change marker. ``TABLE_ROWS`` is left out: InnoDB re-estimates it with no
change to the data.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from app.connectors.core.sql.qualified_names import (
    join_qualified_name,
    split_qualified_name,
)

if TYPE_CHECKING:
    from app.sources.external.mariadb.mariadb_ import (
        ColumnInfo,
        ForeignKeyInfo,
        TableStatsEntry,
    )


def table_fqn(database: str, table: str) -> str:
    return join_qualified_name((database, table))


def split_table_fqn(fqn: str) -> tuple[str, str]:
    """(database, table). Raises ValueError for an id this connector did not write."""
    database, table = split_qualified_name(fqn, 2)
    return database, table


def structure_hash(
    columns: list[ColumnInfo], primary_keys: list[str], foreign_keys: list[ForeignKeyInfo],
) -> str:
    payload = {
        "columns": [column.model_dump() for column in columns],
        "primary_keys": list(primary_keys),
        "foreign_keys": [fk.model_dump() for fk in foreign_keys],
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def content_marker(stat: TableStatsEntry | None) -> str:
    """When the rows last changed, as far as MariaDB says.

    InnoDB keeps ``UPDATE_TIME`` in memory: after a restart it is empty until
    the next write, and ``CREATE_TIME`` stands in. ``AUTO_INCREMENT`` catches
    inserts the time misses.
    """
    if stat is None:
        return ""
    return f"{stat.last_updated or stat.create_time or ''}|{stat.auto_increment or 0}"


def table_revision(structure: str, stat: TableStatsEntry | None, max_rows: int) -> str:
    """``structure:content:max_rows``: the row limit decides how many rows the indexed text holds."""
    return f"{structure}:{content_marker(stat)}:{max_rows}"
