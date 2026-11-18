"""Per-worker counts of code sandbox runs, for the load alerts.

Every query worker records how each counted run (``run_code``,
``install_packages``) got its slot: at once, after a wait, or refused after
``SANDBOX_SLOT_WAIT_S``, and why it was held back. The counts go to one small
JSON file per process, ``load/w-<pid>-<start_ns>.json`` in the code sandbox's
state directory, so the one worker that evaluates the alerts
(``app.services.sandbox_alerts``) can sum every worker of the container. A
worker that dies leaves its file with the counts of its last flush; a new one
writes a new file.

A wait or refusal counts toward the alerts ("qualifying") unless it was for
the org's own share of slots, and, for a memory reason, unless indexing had
not yet backed off when it ended: until then slowing indexing may still free
the memory. On a Docker-in-Docker sidecar indexing does not share the
sandboxes' memory, so memory waits count there at once.

Recording never fails a run: a directory that cannot be used turns the ledger
off with one warning.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.agent_loop_lib.sandbox.capacity import HoldReason, get_sandbox_capacity
from app.core.org_context import spawn_detached
from app.services.resource_governor.shared_slots import (
    ensure_private_directory,
    sandbox_state_directory,
)

__all__ = [
    "FLAG_BACKED_OFF",
    "FLAG_CPU",
    "FLAG_HELD",
    "FLAG_MEMORY",
    "FLAG_QUALIFYING",
    "FLAG_SLOTS",
    "LoadLedger",
    "MinuteBucket",
    "get_default_load_ledger",
    "reset_default_load_ledger",
]

logger = logging.getLogger(__name__)

FILE_VERSION = 1
TICK_S = 10
MINUTE_S = 60
# The longest alert window; anything older is pruned.
KEEP_S = 3600
FLUSH_EVERY_S = 2.0
_FLUSH_WARN_EVERY_S = 300.0

FLAG_QUALIFYING = 1
FLAG_HELD = 2
FLAG_SLOTS = 4
FLAG_MEMORY = 8
FLAG_CPU = 16
FLAG_BACKED_OFF = 32

_MEMORY_REASONS = frozenset({HoldReason.LIVE_MEMORY, HoldReason.HOST_MEMORY_FLOOR})
_REASON_FLAG = {
    HoldReason.SLOTS_FULL: FLAG_SLOTS,
    HoldReason.LIVE_MEMORY: FLAG_MEMORY,
    HoldReason.HOST_MEMORY_FLOOR: FLAG_MEMORY,
    HoldReason.LIVE_CPU: FLAG_CPU,
}
_REASON_KEY = {
    HoldReason.SLOTS_FULL: "slots",
    HoldReason.LIVE_MEMORY: "memory",
    HoldReason.HOST_MEMORY_FLOOR: "memory",
    HoldReason.LIVE_CPU: "cpu",
    HoldReason.ORG_SHARE: "orgShare",
}


@dataclass
class MinuteBucket:
    runs: int = 0
    waited: int = 0
    refused: int = 0
    waited_unqualified: int = 0
    refused_unqualified: int = 0
    cancelled_waiting: int = 0
    waited_by_reason: dict[str, int] = field(
        default_factory=lambda: {"slots": 0, "memory": 0, "cpu": 0, "orgShare": 0},
    )
    max_wait_s: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "runs": self.runs, "waited": self.waited, "refused": self.refused,
            "waitedUnqualified": self.waited_unqualified,
            "refusedUnqualified": self.refused_unqualified,
            "cancelledWaiting": self.cancelled_waiting,
            "waitedByReason": dict(self.waited_by_reason),
            "maxWaitS": round(self.max_wait_s, 1),
        }


def _indexing_backed_off(directory: Path | None) -> bool:
    from app.services.resource_governor.yield_signal import (
        indexing_backed_off,
        read_yield_state,
    )

    now = time.monotonic()
    return indexing_backed_off(read_yield_state(directory, now=now), now)


class LoadLedger:
    """An ``AcquireObserver`` that counts this process's runs and flushes
    them to its own file at most every ``FLUSH_EVERY_S``."""

    def __init__(
        self,
        state_directory: Path | None,
        *,
        mode: str,
        clock: Callable[[], float] = time.time,
        backed_off: Callable[[], bool] | None = None,
        flush_every_s: float = FLUSH_EVERY_S,
    ) -> None:
        self._state_directory = state_directory
        self._directory = None if state_directory is None else state_directory / "load"
        self._mode = mode
        self._clock = clock
        self._backed_off = backed_off or (lambda: _indexing_backed_off(state_directory))
        self._flush_every_s = flush_every_s
        self._pid = os.getpid()
        self._started_at = clock()
        self._name = f"w-{self._pid}-{time.time_ns()}.json"
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._minutes: dict[int, MinuteBucket] = {}
        self._ticks: dict[int, int] = {}
        self._disabled = self._directory is None
        self._dir_ready = False
        self._dirty = False
        self._pending = False
        self._last_flush_at = float("-inf")
        self._flush_warned_at = float("-inf")
        self._tasks: set[asyncio.Future[None]] = set()
        if self._disabled:
            logger.warning("sandbox load ledger: disabled (%s)", "no POSIX file locks")

    @property
    def path(self) -> Path | None:
        return None if self._directory is None else self._directory / self._name

    @property
    def enabled(self) -> bool:
        return not self._disabled

    def wait_start(self, reason: HoldReason) -> None:
        if self._disabled:
            return
        flags = FLAG_HELD | _REASON_FLAG.get(reason, 0)
        with self._lock:
            tick = int(self._clock() // TICK_S)
            self._ticks[tick] = self._ticks.get(tick, 0) | flags
            self._dirty = True
        self._schedule_flush()

    def wait_reason(self, reason: HoldReason) -> None:
        # The reason that counts is the last one, which ``outcome`` carries.
        return None

    def outcome(
        self, outcome: Literal["granted", "refused", "cancelled"],
        waited_s: float, reason: HoldReason | None,
    ) -> None:
        if self._disabled:
            return
        # Only the last KEEP_S of a wait is ever read; a non-finite value is a bug upstream.
        waited_s = min(float(waited_s), KEEP_S) if math.isfinite(waited_s) and waited_s > 0 else 0.0
        if reason is None:
            if outcome != "granted":
                return
            with self._lock:
                self._bucket(self._clock()).runs += 1
                self._dirty = True
            self._schedule_flush()
            return
        memory = reason in _MEMORY_REASONS
        backed_off = False
        if memory and self._mode != "helm_dind":
            try:
                backed_off = self._backed_off()
            except Exception:
                logger.debug("sandbox load ledger: back-off read failed", exc_info=True)
        qualifying = (
            outcome != "cancelled"
            and reason != HoldReason.ORG_SHARE
            and (not memory or self._mode == "helm_dind" or backed_off)
        )
        flags = FLAG_HELD
        if qualifying:
            flags |= FLAG_QUALIFYING | _REASON_FLAG.get(reason, 0)
            if backed_off:
                flags |= FLAG_BACKED_OFF
        with self._lock:
            now = self._clock()
            for tick in range(int((now - waited_s) // TICK_S), int(now // TICK_S) + 1):
                self._ticks[tick] = self._ticks.get(tick, 0) | flags
            bucket = self._bucket(now)
            if outcome == "cancelled":
                bucket.cancelled_waiting += 1
            else:
                bucket.runs += 1
                bucket.max_wait_s = max(bucket.max_wait_s, waited_s)
                refused = outcome == "refused"
                if qualifying:
                    bucket.waited += 1
                    bucket.refused += int(refused)
                    bucket.waited_by_reason[_REASON_KEY[reason]] += 1
                else:
                    bucket.waited_unqualified += 1
                    bucket.refused_unqualified += int(refused)
                    if reason == HoldReason.ORG_SHARE:
                        bucket.waited_by_reason["orgShare"] += 1
            self._dirty = True
        self._schedule_flush()

    def _bucket(self, now: float) -> MinuteBucket:
        minute = int(now // MINUTE_S)
        bucket = self._minutes.get(minute)
        if bucket is None:
            bucket = self._minutes[minute] = MinuteBucket()
        return bucket

    def _schedule_flush(self) -> None:
        with self._lock:
            if self._pending:
                return
            self._pending = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            with self._lock:
                self._pending = False
            self.flush()
            return
        delay = max(0.0, self._last_flush_at + self._flush_every_s - time.monotonic())
        loop.call_later(delay, self._start_flush, loop)

    def _start_flush(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            self._pending = False
        task = spawn_detached(asyncio.to_thread(self.flush), name="sandbox-load-ledger-flush")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def to_json(self, now: float | None = None) -> dict[str, Any]:
        """The file's content; prunes what is older than ``KEEP_S``."""
        now = self._clock() if now is None else now
        with self._lock:
            oldest = now - KEEP_S
            for minute in [m for m in self._minutes if (m + 1) * MINUTE_S < oldest]:
                del self._minutes[minute]
            for tick in [t for t in self._ticks if (t + 1) * TICK_S < oldest]:
                del self._ticks[tick]
            return {
                "v": FILE_VERSION, "pid": self._pid,
                "startedAt": round(self._started_at, 3), "updatedAt": round(now, 3),
                "host": _hostname(),
                "tickSeconds": TICK_S, "minuteSeconds": MINUTE_S,
                "minutes": {str(m): b.to_json() for m, b in sorted(self._minutes.items())},
                "ticks": {str(t): f for t, f in sorted(self._ticks.items())},
            }

    def flush(self) -> bool:
        """Write the file now (tmp file, then ``os.replace``, so a reader never
        sees a torn one). Blocking; returns whether it was written."""
        if self._disabled or self._directory is None:
            return False
        with self._write_lock:
            self._last_flush_at = time.monotonic()
            if not self._dir_ready:
                try:
                    if self._state_directory is not None:
                        ensure_private_directory(self._state_directory)
                    ensure_private_directory(self._directory)
                except OSError as exc:
                    self._disabled = True
                    logger.warning("sandbox load ledger: disabled (%s)", exc)
                    return False
                self._dir_ready = True
            with self._lock:
                self._dirty = False
            data = json.dumps(self.to_json(), separators=(",", ":")).encode()
            final = self._directory / self._name
            tmp = final.with_name(final.name + ".tmp")
            try:
                with contextlib.suppress(FileNotFoundError):
                    tmp.unlink()
                fd = os.open(
                    tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                )
                try:
                    written = os.write(fd, data)
                    if written != len(data):
                        # A short write (disk full): keep the last whole file.
                        raise OSError(f"short write ({written} of {len(data)} bytes)")
                finally:
                    os.close(fd)
                os.replace(tmp, final)
            except OSError as exc:
                with self._lock:
                    self._dirty = True
                if time.monotonic() - self._flush_warned_at >= _FLUSH_WARN_EVERY_S:
                    self._flush_warned_at = time.monotonic()
                    logger.warning("sandbox load ledger: flush failed: %s", exc)
                return False
            return True

    def close(self) -> None:
        """Final flush at shutdown, when anything is left unwritten."""
        with self._lock:
            dirty = self._dirty
        if dirty:
            self.flush()


_hostname_cache: str | None = None


def _hostname() -> str:
    global _hostname_cache
    if _hostname_cache is None:
        import socket

        _hostname_cache = socket.gethostname()
    return _hostname_cache


_default: LoadLedger | None = None
_default_lock = threading.Lock()


def get_default_load_ledger() -> LoadLedger:
    """This process's ledger, in the code sandbox's state directory."""
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = LoadLedger(sandbox_state_directory(), mode=get_sandbox_capacity().mode)
    return _default


def peek_default_load_ledger() -> LoadLedger | None:
    """The process's ledger if one was built; never builds it."""
    return _default


def reset_default_load_ledger() -> None:
    """Drop the process's ledger. For tests only."""
    global _default
    with _default_lock:
        _default = None
