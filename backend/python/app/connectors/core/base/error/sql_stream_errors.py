"""Map SQL failures onto HTTP statuses for the record-streaming paths.

``to_stream_error`` classifies a failure by reading an HTTP status off the
exception, which works for REST sources and never for a database: PostgreSQL,
MariaDB and Snowflake report "permission denied" or "table does not exist" over
their own wire protocol, with no status to read. Without this translation a
revoked SELECT grant, a dropped table and a real internal fault all collapse
into the same opaque 500.

Two entry points, because the SQL connectors lose the exception at different
depths: ``to_sql_stream_error`` for a live driver exception, and
``to_sql_response_error`` for the ``*DataSource`` wrappers that already caught
one and kept only ``str(e)``.
"""

from fastapi import HTTPException

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.error.stream_errors import (
    map_source_status,
    not_found_at_source,
    to_stream_error,
)

# asyncpg exception classes. Matched by name across the MRO for the same reason
# stream_errors does it: importing the driver here would make error handling
# itself fail on an install without it.
_DENIED_EXCEPTIONS = frozenset({"InsufficientPrivilegeError", "InvalidPasswordError"})
_MISSING_EXCEPTIONS = frozenset(
    {"UndefinedTableError", "UndefinedObjectError", "InvalidSchemaNameError"}
)

# SQLSTATE is the portable signal both drivers expose. MariaDB folds access
# denial into the catch-all '42000' (which also covers syntax errors), so its
# denials are matched on errno instead.
_DENIED_SQLSTATES = frozenset({"42501", "28000", "28P01"})
_MISSING_SQLSTATES = frozenset({"42P01", "42S02", "3D000", "3F000", "42704"})
_DENIED_ERRNOS = frozenset({1044, 1045, 1142, 1143, 1227, 1370})
_MISSING_ERRNOS = frozenset({1049, 1146})

# "denied" alone covers PostgreSQL's "permission denied for table x" and
# MySQL/MariaDB's "SELECT command denied to user 'x'@'y' for table 'z'".
_DENIED_TEXT = ("denied", "insufficient privilege")
# Deliberately not the bare "not found": a transport message such as
# "host not found" would then be reported as a deleted table, which the
# indexing consumer treats as permanent.
_MISSING_TEXT = (
    "table not found",
    "does not exist",
    "doesn't exist",
    "unknown database",
    "unknown table",
)


def to_sql_stream_error(
    exc: BaseException, *, connector: str | None = None
) -> HTTPException:
    """Map a database driver exception onto a stream HTTPException."""
    if isinstance(exc, HTTPException):
        return exc

    sqlstate = getattr(exc, "sqlstate", None)
    errno = getattr(exc, "errno", None)
    names = {base.__name__ for base in type(exc).__mro__}

    if (
        names & _DENIED_EXCEPTIONS
        or sqlstate in _DENIED_SQLSTATES
        or errno in _DENIED_ERRNOS
    ):
        return map_source_status(HttpStatusCode.FORBIDDEN.value, connector=connector)
    if (
        names & _MISSING_EXCEPTIONS
        or sqlstate in _MISSING_SQLSTATES
        or errno in _MISSING_ERRNOS
    ):
        return not_found_at_source(connector)
    # TimeoutError is an OSError but not a ConnectionError, so it still reaches
    # to_stream_error below and becomes the 504 it deserves.
    if isinstance(exc, ConnectionError):
        return map_source_status(
            HttpStatusCode.SERVICE_UNAVAILABLE.value, connector=connector
        )

    return to_stream_error(exc, connector=connector)


def to_sql_response_error(
    error: str | None, *, connector: str | None = None
) -> HTTPException:
    """Map a ``*DataSource`` wrapper's ``error`` text onto a stream HTTPException.

    Text matching is the only signal left: those wrappers catch the driver
    exception and keep ``str(e)``, discarding the class, SQLSTATE and errno.
    """
    text = (error or "").lower()
    if any(marker in text for marker in _MISSING_TEXT):
        return not_found_at_source(connector)
    if any(marker in text for marker in _DENIED_TEXT):
        return map_source_status(HttpStatusCode.FORBIDDEN.value, connector=connector)
    return to_stream_error(
        RuntimeError(error or "SQL metadata fetch failed"), connector=connector
    )
