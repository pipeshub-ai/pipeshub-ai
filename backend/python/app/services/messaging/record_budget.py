"""A record's processing budget, shared between the indexing consumer that arms it and the record handler that sizes it.

The consumer arms the budget before the handler runs, from the event's own ``extension`` and ``mimeType``. When the
event names no mime, the handler resolves the record's stored one, and the parsing client sizes its own budget from
that. ``resize_to`` grows the consumer's budget to the same figure, so the two cannot drift (a stored PDF sent with no
mime is not cut at ``RECORD_PROCESSING_TIMEOUT`` while the parsing service was told ``PDF_RECORD_PROCESSING_TIMEOUT``).
"""
from __future__ import annotations

import asyncio
import contextlib
from contextvars import ContextVar
from typing import TYPE_CHECKING

from app.services.messaging.config import messaging_env

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class RecordBudget:
    """The consumer's timeout for one record, the seconds it is sized to, and the loop that owns it."""

    def __init__(self, timeout: asyncio.Timeout, seconds: float) -> None:
        self.timeout = timeout
        self.seconds = seconds
        self._loop = asyncio.get_running_loop()

    def resize(self, seconds: float) -> None:
        """Grow the budget to *seconds* from the same start. It never shrinks: a deadline moved below the time already
        spent would cut the record at once. From another loop (PostgreSQL runs the handler on its worker loop) the move
        is posted to the owner's."""
        if seconds <= self.seconds:
            return
        try:
            same_loop = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            same_loop = False
        if same_loop:
            self._move(seconds)
            return
        with contextlib.suppress(RuntimeError):  # the owner's loop closed during shutdown: nothing is left to move
            self._loop.call_soon_threadsafe(self._move, seconds)

    # The deadline, as the owner's timeout has it. A handler on another loop (PostgreSQL runs it on its worker loop) is given this
    # object where the other consumers give the timeout itself: reads are plain, and a move is posted to the owner's loop, because
    # ``Timeout.reschedule`` only works from the loop that entered the timeout.
    def when(self) -> float | None:
        return self.timeout.when()

    def expired(self) -> bool:
        return self.timeout.expired()

    def reschedule(self, when: float | None) -> None:
        """Move the deadline to *when* (the owner loop's clock) on the owner's loop, whichever loop calls."""
        try:
            same_loop = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            same_loop = False
        if same_loop:
            self._reschedule(when)
            return
        with contextlib.suppress(RuntimeError):  # the owner's loop closed during shutdown: nothing is left to move
            self._loop.call_soon_threadsafe(self._reschedule, when)

    def _reschedule(self, when: float | None) -> None:
        try:
            self.timeout.reschedule(when)
        except RuntimeError:  # it fired or was left: there is nothing to move
            return

    def _move(self, seconds: float) -> None:
        when = self.timeout.when()
        # shortcut: a paused budget (when() None) is not moved; the handler never runs while the consumer waits for a
        # permit (the consumer pulls its events), revisit if a handler ever runs beside such a wait.
        if seconds <= self.seconds or when is None:
            return
        try:
            self.timeout.reschedule(when + seconds - self.seconds)
        except RuntimeError:  # it fired or was left: there is nothing to move
            return
        self.seconds = seconds


_current: ContextVar[RecordBudget | None] = ContextVar("record_budget", default=None)


@contextlib.asynccontextmanager
async def armed(timeout: asyncio.Timeout, seconds: float) -> AsyncIterator[RecordBudget]:
    """Make *timeout*, sized to *seconds*, the budget the record handler resizes while the block runs."""
    budget = RecordBudget(timeout, seconds)
    token = _current.set(budget)
    try:
        yield budget
    finally:
        _current.reset(token)


def current() -> RecordBudget | None:
    return _current.get()


def resize_to(extension: object, mime_type: object) -> None:
    """Grow the armed budget, if any, to what these labels are given (``record_processing_timeout_for``)."""
    budget = _current.get()
    if budget is not None:
        budget.resize(messaging_env.record_processing_timeout_for(extension, mime_type))
