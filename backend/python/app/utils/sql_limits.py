"""Bounds for SQL that an agent writes: rows read, time spent, and cell size shown."""

from typing import Any

# Rows read from a result. The clients fetch one more, so that a cut is seen as a cut.
SQL_MAX_ROWS = 1000
# Seconds the database may spend on one agent statement (each engine's own setting).
SQL_QUERY_TIMEOUT_S = 120
# Characters of one text cell shown to the model; the CSV keeps the whole value.
SQL_CELL_MAX_CHARS = 100

_TIMEOUT_MARKERS = (
    "statement timeout",
    "max_statement_time",
    "statement reached its statement or warehouse timeout",
    "cancelled by the client due to a timeout",
)
# Redshift reports its statement_timeout like any other cancel, so the note only says it is likely.
# MariaDB's KILL QUERY (the client's deadline) reads the same way.
_CANCEL_MARKERS = ("cancelled on user's request", "query execution was interrupted")


def cap_rows(rows: list[Any]) -> tuple[list[Any], bool]:
    """``rows`` cut to SQL_MAX_ROWS, and whether anything was left out."""
    return rows[:SQL_MAX_ROWS], len(rows) > SQL_MAX_ROWS


def clip_cells(row: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``row`` with long text values shortened."""
    return {
        k: v[: SQL_CELL_MAX_CHARS - 3] + "..." if isinstance(v, str) and len(v) > SQL_CELL_MAX_CHARS else v
        for k, v in row.items()
    }


def friendly_sql_error(message: str) -> str:
    """The database's timeout errors say what happened in engine terms; say it plainly."""
    lowered = message.lower()
    # A warehouse or account limit can be shorter than ours, so the duration is not asserted.
    if any(m in lowered for m in _TIMEOUT_MARKERS):
        cause = f"The query hit a time limit and was stopped (agent queries may run up to {SQL_QUERY_TIMEOUT_S}s)."
    elif any(m in lowered for m in _CANCEL_MARKERS):
        cause = f"The query was cancelled, most likely because it ran past the {SQL_QUERY_TIMEOUT_S}s limit."
    else:
        return message
    return f"{cause} Add a selective WHERE, aggregate, or select fewer columns. ({message})"


def truncation_note(max_rows: int = SQL_MAX_ROWS) -> str:
    return f"Result cut at {max_rows} rows; more rows exist. Narrow the query to see the rest."
