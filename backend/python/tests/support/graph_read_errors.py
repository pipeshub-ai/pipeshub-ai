"""Graph read failures that carry the database host, the query and record content in their text (docs/logging.md,
"Search access reads"). Tests assert, with ``tests/support/log_capture.py``, that none of ``SENTINELS`` is logged.

``unmarked`` is an error no client stamped; ``marked_internal`` is one the graph client recorded as ``db.query.failed``
(classed, text hidden), which a ``%s`` in a log call still prints.
"""

from __future__ import annotations

import logging

from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.utils.error_class import mark_error

HOST = "graph-db.internal.example:7687"
CONTENT = "Quarterly salary review"
GRAPH_KEY = "gk-7f3a9c"
DRIVER_TEXT = f"neo4j://{HOST} MATCH (u:User {{id: '{GRAPH_KEY}'}}) RETURN '{CONTENT}'"
SENTINELS = (HOST, CONTENT, GRAPH_KEY, "MATCH (u:User")


def unmarked() -> Exception:
    return ConnectionError(DRIVER_TEXT)


def marked_internal() -> Exception:
    exc = RuntimeError(DRIVER_TEXT)
    mark_error(exc, "internal", hide_text=True)
    return exc


# (error factory, error_class, level): an outage, and our own query bug as the graph client leaves it.
CASES = [(unmarked, "unavailable", logging.WARNING), (marked_internal, "internal", logging.ERROR)]


def wrapped(cause: Exception) -> PermissionVerificationUnavailableError:
    """As the providers raise it: the driver's text copied into the message, the driver error kept as the cause."""
    try:
        raise cause
    except Exception as exc:
        try:
            raise PermissionVerificationUnavailableError(str(exc)) from exc
        except PermissionVerificationUnavailableError as pvue:
            return pvue
