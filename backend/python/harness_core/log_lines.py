"""The kernel's one-line log events: ``tool_call.end``, ``llm_call.failed``, ``sub_agent.end``, ``middleware.failed``.

Their shape (field binding, error classes, redaction, the stack written once) belongs to the host:
PipesHub's is ``app.utils.logging_fields`` and ``app.utils.error_class`` (docs/logging.md), bound
through ``harness_core.bindings.KernelBindings.log_lines``. ``harness_core`` may not import the app,
so every kernel call goes through the functions below. Without a host they write plain standard
library lines under the same event names, never an argument, a result or an error's text.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Protocol

from harness_core import bindings

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from contextlib import AbstractContextManager

_RECORD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


class LogLines(Protocol):
    def log_event(self, logger: logging.Logger, event: str, /, *, level: int = logging.INFO, **fields: object) -> None: ...

    def log_failure(self, logger: logging.Logger, event: str, exc: BaseException, /, **fields: object) -> None: ...

    def log_fields(self, **ids: object) -> AbstractContextManager[None]: ...

    def current_log_fields(self) -> Mapping[str, object]: ...

    def classify_error(self, exc: BaseException) -> str: ...

    def level_for(self, error_class: str) -> int: ...

    def already_logged(self, exc: BaseException, level: int) -> bool: ...


class NeutralLogLines:
    """Standard library lines: the event as the message, enum and id fields as ``extra``."""

    def log_event(self, logger: logging.Logger, event: str, /, *, level: int = logging.INFO, **fields: object) -> None:
        if logger.isEnabledFor(level):
            extra = {k: v for k, v in fields.items() if v is not None and k not in _RECORD_ATTRS}
            logger.log(level, event, extra=extra, stacklevel=3)

    def log_failure(self, logger: logging.Logger, event: str, exc: BaseException, /, **fields: object) -> None:
        level = fields.pop("level", None)
        fields.pop("include_error_text", None)
        cls = fields.pop("error_class", None) or self.classify_error(exc)
        want = level if isinstance(level, int) else self.level_for(str(cls))
        if logger.isEnabledFor(want):
            extra = {"outcome": fields.get("outcome") or "failure", "error_class": cls, "error_type": type(exc).__name__}
            extra |= {k: v for k, v in fields.items() if v is not None and k not in _RECORD_ATTRS and k not in extra}
            logger.log(want, event, extra=extra, stacklevel=3)

    @contextlib.contextmanager
    def log_fields(self, **ids: object) -> Iterator[None]:
        yield

    def current_log_fields(self) -> Mapping[str, object]:
        return {}

    def classify_error(self, exc: BaseException) -> str:
        if isinstance(exc, TimeoutError):
            return "timeout"
        if isinstance(exc, asyncio.CancelledError):
            return "cancelled"
        return "internal"

    def level_for(self, error_class: str) -> int:
        return logging.ERROR if error_class == "internal" else logging.WARNING

    def already_logged(self, exc: BaseException, level: int) -> bool:
        return False


NEUTRAL = NeutralLogLines()


def _lines() -> LogLines:
    return bindings.current().log_lines or NEUTRAL


def log_event(logger: logging.Logger, event: str, /, *, level: int = logging.INFO, **fields: object) -> None:
    _lines().log_event(logger, event, level=level, **fields)


def log_failure(logger: logging.Logger, event: str, exc: BaseException, /, **fields: object) -> None:
    """The one failure line for ``exc``; never raises (it runs inside ``except`` blocks)."""
    with contextlib.suppress(Exception):
        _lines().log_failure(logger, event, exc, **fields)


def log_fields(**ids: object) -> AbstractContextManager[None]:
    return _lines().log_fields(**ids)


def current_log_fields() -> Mapping[str, object]:
    return _lines().current_log_fields()


def classify_error(exc: BaseException) -> str:
    return _lines().classify_error(exc)


def level_for(error_class: str) -> int:
    return _lines().level_for(error_class)


def already_logged(exc: BaseException, level: int) -> bool:
    return _lines().already_logged(exc, level)


__all__ = [
    "NEUTRAL",
    "LogLines",
    "NeutralLogLines",
    "already_logged",
    "classify_error",
    "current_log_fields",
    "level_for",
    "log_event",
    "log_failure",
    "log_fields",
]
