"""Graph read failures that carry the database host, the query and record content in their text, and the checks that
a handler logged one text-free line for them (docs/logging.md, "Search access reads").

``unmarked`` is an error no client stamped; ``marked_internal`` is one the graph client recorded as ``db.query.failed``
(classed, text hidden), which a ``%s`` in a log call still prints.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.utils.error_class import mark_error
from app.utils.logger import JsonFormatter, TextFormatter

if TYPE_CHECKING:
    from collections.abc import Sequence

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


def rendered(record: logging.LogRecord) -> str:
    """The record as both formats print it: message, fields and stack."""
    return TextFormatter().format(record) + "\n" + json.dumps(json.loads(JsonFormatter().format(record)))


def assert_text_free(records: Sequence[logging.LogRecord], *values: str) -> None:
    """No sentinel (nor any of ``values``) in any record, as either format renders it."""
    assert records, "nothing was logged; check the logger name and level"
    for record in records:
        text = rendered(record)
        for value in (*SENTINELS, *values):
            assert value not in text, f"{value!r} was logged: {text!r}"


def failure_line(records: Sequence[logging.LogRecord], event: str, func: str) -> logging.LogRecord:
    """The one line for the failure: it is the only record carrying an error, and the only WARNING-or-above record
    ``func`` (the handler) wrote."""
    of_error = [r for r in records if getattr(r, "error_type", None) or r.exc_info or r.exc_text]
    assert [r.getMessage() for r in of_error] == [event], [r.getMessage() for r in records]
    assert [r.getMessage() for r in records if r.funcName == func and r.levelno >= logging.WARNING] == [event]
    line = of_error[0]
    assert line.outcome == "failure"
    assert not hasattr(line, "error_message")
    return line
