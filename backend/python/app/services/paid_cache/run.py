"""The ambient run of one record's paid work, and its retention manifest.

A writer (the parser, the stage that describes images) opens a run for ``(org, record, contentRev)``. Every
cache key the run reads or stores is listed in the run's manifest:

* ``manifests/{contentRev}/{writer}.open.json``: ``{startedMs, run, keys}``, written before the run's first
  object and rewritten every ``FLUSH_KEYS`` keys or ``FLUSH_SECONDS``. It protects an in-flight run's objects
  from the retention sweep for ``OPEN_PROTECTS_MS``.
* ``manifests/{contentRev}/{writer}.json``: ``{startedMs, completedMs, seq, run, keys}``, written when the run
  completes, replacing the open one. The sweep orders revisions by ``completedMs`` (the writer's own clock,
  with ``seq``, a counter of that writer process, for ties), never by the storage write time, which S3 and Azure
  report in whole seconds.

A run that fails keeps its open manifest, so a retry still finds its objects.
"""

import asyncio
import itertools
import json
import secrets
import time
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, Protocol

from app.services.paid_cache import locks as lock_limits

FLUSH_KEYS = 50
FLUSH_SECONDS = 30.0
#: How long an open manifest keeps its objects out of the sweep.
OPEN_PROTECTS_MS = 24 * 3600 * 1000
#: The manifest label of a run that does not know its revision.
NO_REV = "norev"
#: Completion order within one writer process, for manifests that complete in the same millisecond.
_SEQ = itertools.count(1)


def open_manifest_path(content_rev: str, writer: str) -> str:
    return f"manifests/{content_rev}/{writer}.open.json"


def final_manifest_path(content_rev: str, writer: str) -> str:
    return f"manifests/{content_rev}/{writer}.json"


class _ManifestStore(Protocol):
    async def replace(self, org_id: str, record_id: str, path: str, data: bytes) -> None: ...
    async def delete_files(self, org_id: str, record_id: str, paths: list[str]) -> None: ...


class RunScope:
    def __init__(
        self, org_id: str, record_id: str, content_rev: str | None, writer: str, *, refresh: bool = False,
        clock: Callable[[], float] = time.time, stage_deadline_s: float | None = None,
    ) -> None:
        self.org_id = org_id
        self.record_id = record_id
        self.content_rev = content_rev or NO_REV
        self.writer = writer
        #: ``refreshPaidOutputs``: lookups are bypassed, so every call is paid again and stored.
        self.refresh = refresh
        self.run_id = secrets.token_hex(8)
        #: Set by the in-process pipeline, whose run ends when the record's indexing completes.
        self.in_process = False
        self.finished = False
        #: The stage deadline this run was given (the record budget a caller sent); None: the process's own setting.
        self.stage_deadline_s = stage_deadline_s
        self._clock = clock
        self.started_ms = int(clock() * 1000)
        self._keys: list[str] = []
        self._seen: set[str] = set()
        self._opened = False
        self._unflushed = 0
        self._last_flush = clock()
        #: One open-manifest write at a time, so a later snapshot is never overtaken by an older one.
        self._write_lock = asyncio.Lock()

    def past_deadline(self) -> bool:
        """True once the stage has run longer than ``stage_deadline_s()``: nothing more is written for the record.

        Measured from the stage's start, not from the paid call's: the delayed re-purge after a delete ends a
        bounded time after that stage began, so a write later than this could land after the last purge.
        """
        limit = self.stage_deadline_s if self.stage_deadline_s is not None else lock_limits.stage_deadline_s()
        return self._clock() * 1000 - self.started_ms > limit * 1000

    def matches(self, org_id: str, record_id: str) -> bool:
        return self.org_id == org_id and self.record_id == record_id

    @property
    def keys(self) -> list[str]:
        return list(self._keys)

    def _body(self, *, final: bool = False) -> bytes:
        body: dict[str, Any] = {
            "rev": self.content_rev, "writer": self.writer, "run": self.run_id,
            "startedMs": self.started_ms, "keys": self._keys,
        }
        if final:
            body["completedMs"] = max(int(self._clock() * 1000), self.started_ms)
            body["seq"] = next(_SEQ)
        return json.dumps(body, separators=(",", ":")).encode()

    async def note(self, store: _ManifestStore, path: str, *, opens: bool) -> None:
        """Records a key the run used. Opens the manifest before the run's first object."""
        if path not in self._seen:
            self._seen.add(path)
            self._keys.append(path)
            self._unflushed += 1
        if self.past_deadline():
            return
        if not self._opened:
            await self._write_open(store)
        elif self._unflushed >= FLUSH_KEYS or self._clock() - self._last_flush >= FLUSH_SECONDS:
            await self._write_open(store)

    async def _write_open(self, store: _ManifestStore) -> None:
        async with self._write_lock:
            # The body is taken inside the lock: it lists every key noted by the time this write starts, and the
            # write that waited behind another one finds nothing left to write.
            if self.finished or self.past_deadline() or (self._opened and self._unflushed == 0):
                return
            if not self._opened:
                await self._adopt_failed_attempt(store)
            body, noted = self._body(), self._unflushed
            async with asyncio.timeout(lock_limits.WRITE_DEADLINE_S):
                await store.replace(
                    self.org_id, self.record_id, open_manifest_path(self.content_rev, self.writer), body,
                )
            self._opened = True
            self._unflushed = max(self._unflushed - noted, 0)
            self._last_flush = self._clock()

    async def _adopt_failed_attempt(self, store: _ManifestStore) -> None:
        """A retry replaces the open manifest of the attempt that failed: its keys stay listed.

        Both attempts work on the same revision and writer, so one path holds them; a retry that fails after noting
        fewer keys would otherwise unlist results the first attempt paid for, and a sweep could delete them.
        """
        reader = getattr(store, "get", None)
        if reader is None:
            return
        # An absent manifest is ``None`` and adds nothing. A failed or undecodable read raises out of the write, so
        # the manifest is not replaced with this attempt's keys alone and the earlier references stay in place.
        async with asyncio.timeout(lock_limits.WRITE_DEADLINE_S):
            raw = await reader(self.org_id, self.record_id, open_manifest_path(self.content_rev, self.writer))
        previous = json.loads(raw).get("keys", []) if raw else []
        for path in previous if isinstance(previous, list) else []:
            if isinstance(path, str) and path not in self._seen:
                self._seen.add(path)
                self._keys.append(path)
                self._unflushed += 1

    async def flush(self, store: _ManifestStore) -> None:
        if self._opened and self._unflushed:
            await self._write_open(store)

    async def finish(self, store: _ManifestStore) -> None:
        """The run completed: the final manifest replaces the open one."""
        async with self._write_lock:
            if self.finished or self.past_deadline() or (not self._keys and not self._opened):
                return
            async with asyncio.timeout(lock_limits.WRITE_DEADLINE_S):
                await store.replace(
                    self.org_id, self.record_id, final_manifest_path(self.content_rev, self.writer),
                    self._body(final=True),
                )
            self.finished = True
            if self._opened:
                async with asyncio.timeout(lock_limits.WRITE_DEADLINE_S):
                    await store.delete_files(
                        self.org_id, self.record_id, [open_manifest_path(self.content_rev, self.writer)],
                    )
                self._opened = False


_run: ContextVar[RunScope | None] = ContextVar("paid_cache_run", default=None)


def begin_run(
    org_id: str, record_id: str, content_rev: str | None, writer: str, *, refresh: bool = False,
    stage_deadline_s: float | None = None,
) -> RunScope:
    """Opens the ambient run for this task and the tasks it starts. Left set: the next run replaces it."""
    scope = RunScope(org_id, record_id, content_rev, writer, refresh=refresh, stage_deadline_s=stage_deadline_s)
    _run.set(scope)
    return scope


def current_run() -> RunScope | None:
    return _run.get()


def clear_run() -> None:
    _run.set(None)


def refresh_requested(payload: Any) -> bool:  # noqa: ANN401 - a JSON value, model response or caller callable of any shape
    """``refreshPaidOutputs`` on a reindex event (never a string: ``"false"`` must not refresh)."""
    return isinstance(payload, dict) and payload.get("refreshPaidOutputs") is True
