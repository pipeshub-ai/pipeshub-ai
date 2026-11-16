"""Which parse pool a job runs in, and which permit it carries (``PARSE_ADMISSION=classes``).

* ``set_class`` / ``enter_permit`` / ``parse_scope`` carry the record's cost class and its ClassGate permit down to the parse pool through
  a context variable, the way the governor's ``admitted_in`` stamp travels. A pool lane that finds a permit
  adopts it and reserves nothing; a lane that finds none (a legacy or query caller) reserves one itself,
  classified small and counted as unscoped.
* ``route`` picks the pool: ``inspect`` for an inspection job, ``big`` for large and huge work, ``small``
  for everything else.
* ``WidthBudget``: the one count of live worker processes across the class pools. Pools are warm-up
  caches; the member budget decides how many parses run.

No I/O and no pool of its own: the processes live in ``app.modules.parsers.parse_pool_classes``.
"""
from __future__ import annotations

import contextlib
import contextvars
import threading
import time
from enum import StrEnum
from typing import TYPE_CHECKING

from app.capacity.classes import BIG_CLASSES, CostClass

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

__all__ = [
    "BorrowedPermit",
    "Route",
    "WidthBudget",
    "current_class",
    "current_est_seconds",
    "enter_permit",
    "lane_permit",
    "parse_scope",
    "route",
    "set_class",
    "unscoped_total",
]

_class_var: contextvars.ContextVar[CostClass | None] = contextvars.ContextVar("parse_class", default=None)
_est_var: contextvars.ContextVar[float | None] = contextvars.ContextVar("parse_est_seconds", default=None)
_permit_var: contextvars.ContextVar[object | None] = contextvars.ContextVar("parse_permit", default=None)
_unscoped = 0
_unscoped_lock = threading.Lock()


class Route(StrEnum):
    INSPECT = "inspect"
    SMALL = "small"
    BIG = "big"


class BorrowedPermit:
    """A permit its owner keeps: the lane sees it as given (so it reserves nothing) but never releases it.

    One record may submit several parse jobs under one admission; the first lane to finish must not return the
    tokens the others still run on. The admission that took the permit releases it, once.
    """

    def __init__(self, permit: object) -> None:
        self._permit = permit
        self.family = getattr(permit, "family", None)
        self.cpu = getattr(permit, "cpu", 1)
        self.mem_bytes = getattr(permit, "mem_bytes", 0)
        self.cls = getattr(permit, "cls", None)

    def adopt(self) -> BorrowedPermit:
        return self

    def release(self) -> None:
        """The owner releases."""


@contextlib.contextmanager
def parse_scope(cls: CostClass | None, permit: object | None = None) -> Iterator[None]:
    c = _class_var.set(cls)
    p = _permit_var.set(permit)
    try:
        yield
    finally:
        _reset(_permit_var, p)
        _reset(_class_var, c)


def enter_scope(cls: CostClass | None, permit: object | None = None) -> Callable[[], None]:
    """``parse_scope`` for an admission that is released later, from a callback: returns the undo."""
    c = _class_var.set(cls)
    p = _permit_var.set(permit)

    def undo() -> None:
        _reset(_permit_var, p)
        _reset(_class_var, c)

    return undo


def _reset(var: contextvars.ContextVar, token: contextvars.Token) -> None:
    try:
        var.reset(token)
    except (ValueError, LookupError):  # released from another context than the one that entered
        var.set(None)


def set_class(cls: CostClass | None, est_seconds: float | None = None) -> None:
    """The class of the record this task is processing, for everything downstream of its parse admission
    (the embedding request carries it as ``X-Parse-Class``), and its estimated parse seconds (the Docling
    call's deadline comes from them). It lives as long as the task's context."""
    _class_var.set(cls)
    _est_var.set(est_seconds)


def current_est_seconds() -> float | None:
    return _est_var.get()


def enter_permit(permit: object | None) -> Callable[[], None]:
    """Make *permit* the one a pool lane adopts; returns the undo (run when the admission is released)."""
    p = _permit_var.set(permit)
    return lambda: _reset(_permit_var, p)


def current_class() -> CostClass | None:
    return _class_var.get()


def lane_permit(kind: object | None) -> BorrowedPermit | None:
    """The permit a pool lane may adopt for a job of *kind*: the admission's, never for an inspection."""
    if kind is not None and str(getattr(kind, "value", kind)) == "inspect":
        return None
    permit = _permit_var.get()
    return None if permit is None else BorrowedPermit(permit)


def route(kind: object | None, cls: CostClass | None = None) -> Route:
    """The pool for a job. A job with no class is small and counted: something parsed outside any admission."""
    global _unscoped  # noqa: PLW0603
    if kind is not None and str(getattr(kind, "value", kind)) == "inspect":
        return Route.INSPECT
    if cls is None:
        with _unscoped_lock:
            _unscoped += 1
        return Route.SMALL
    return Route.BIG if cls in BIG_CLASSES else Route.SMALL


def unscoped_total() -> int:
    """``pipeshub_parse_admission_unscoped_total``: parse jobs that arrived with no class."""
    return _unscoped


class WidthBudget:
    """At most ``limit`` live worker processes across every class pool. Thread safe."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self.limit = limit
        self._live = 0
        self._cond = threading.Condition()
        self.peak = 0

    @property
    def live(self) -> int:
        return self._live

    def acquire(self, timeout: float, abandoned: Callable[[], bool] = lambda: False) -> bool:
        end = time.monotonic() + timeout
        with self._cond:
            while self._live >= self.limit:
                if abandoned():
                    return False
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(min(0.25, left))
            self._live += 1
            self.peak = max(self.peak, self._live)
            return True

    def release(self) -> None:
        with self._cond:
            self._live = max(0, self._live - 1)
            self._cond.notify()
