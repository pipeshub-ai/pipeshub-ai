"""Assert that no personal value reached the logs (docs/logging.md, "Personal data").

A line carries a value in its message, its fields (bound or ``extra=``), its traceback or its
stack, so all four are searched, as every log format renders them. Matching ignores case:
code lowercases emails. Pass the email whole and the first and last names separately. Both
helpers fail when nothing was logged, so a test whose logger was silenced cannot pass.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.utils.logger import log_record_fields

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from unittest.mock import Mock

LOG_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal", "log"})


def _rendered(record: logging.LogRecord) -> str:
    parts = [record.getMessage(), *(f"{k}={v}" for k, v in log_record_fields(record).items())]
    if record.exc_info:
        parts.append(logging.Formatter().formatException(record.exc_info))
    if record.stack_info:
        parts.append(record.stack_info)
    return "\n".join(parts)


def _strings(value: Any, path: frozenset[int] = frozenset()) -> Iterator[str]:  # noqa: ANN401
    """Each string a logged value holds, unescaped: dict keys and values, and container items, at any depth.

    A container already on the current path (one that holds itself) is not entered again.
    """
    if isinstance(value, (dict, list, tuple, set, frozenset)):
        if id(value) in path:
            return
        path = path | {id(value)}
        items = (x for kv in value.items() for x in kv) if isinstance(value, dict) else iter(value)
        for v in items:
            yield from _strings(v, path)
    else:
        yield str(value)


def _assert_absent(lines: list[str], values: Sequence[str]) -> None:
    for value in values:
        hits = [line for line in lines if value.lower() in line.lower()]
        assert not hits, f"{value!r} was logged: {hits[0]!r}"


def assert_not_logged(records: Sequence[logging.LogRecord], *values: str) -> None:
    """None of ``values`` is in any of ``records`` (``caplog.records``, or a handler's list)."""
    assert records, "no log records were captured; check the logger name and level the test captures"
    _assert_absent([_rendered(r) for r in records], values)


def assert_not_logged_mock(logger: Mock, *values: str) -> None:
    """None of ``values`` is in any log-level call on a mock logger: its args and kwargs, ``extra=`` and an exception
    passed as an arg or as ``exc_info=exc`` included. A mock records no traceback for ``exc_info=True`` or
    ``logger.exception(...)``; test those with :func:`assert_not_logged` on a real logger."""
    calls = [c for c in logger.mock_calls if c[0].rsplit(".", 1)[-1] in LOG_METHODS]
    assert calls, "the mock logger got no log-level calls"
    # repr leads, for the failure message; the raw strings follow, as repr escapes quotes and backslashes.
    _assert_absent(["\n".join([repr(c), *_strings((c.args, c.kwargs))]) for c in calls], values)
