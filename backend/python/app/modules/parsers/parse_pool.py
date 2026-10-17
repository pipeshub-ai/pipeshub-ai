"""Worker processes for CPU-bound parsing, so a large file cannot stall the event loop.

``asyncio.to_thread`` keeps the loop turning only while the work releases the
GIL. markdown-it, the tree-sitter walk and CSV table detection are Python and
hold it; ``csv.reader`` and ``json.loads`` are C and hold it for the whole
call. A 5 MB source file kept the loop from running for seven seconds, long
enough for the Neo4j, Redis and embedding calls sharing that loop to time out
and read as downstream failures.

The pool is a few *lanes*. A lane is one thread and the one worker process it
talks to, running one job at a time. Because a lane never holds two jobs, a
worker that dies (OOM-killed, segfault) is always the fault of the job it was
running: that job fails and the lane starts a fresh worker for the next one. A
shared ``ProcessPoolExecutor`` would fail every job in flight instead, and its
workers would each re-import the service (see ``parse_worker``).

Only a process that owns a ``ResourceGovernor`` gets a pool (see
``set_resource_governor``). Everywhere else, and for payloads too small to be
worth the hop, callers keep using threads.
"""
from __future__ import annotations

import asyncio
import atexit
import logging
import os
import pickle
import queue
import select
import subprocess
import sys
import threading
import time
from concurrent.futures import Future
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from app.exceptions.indexing_exceptions import IndexingError
from app.modules.parsers import parse_worker
from app.services.messaging.config import messaging_env
from app.services.parsing.interface import ParseError, ParseErrorCode
from app.utils.cpu_offload import DEFAULT_OFFLOAD_THRESHOLD_BYTES
from app.utils.env_config import env_int
from app.utils.user_errors import PARSE_WORKER_OUT_OF_MEMORY, PROCESSING_TIMED_OUT

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.services.resource_governor import ResourceGovernor

T = TypeVar("T")

__all__ = [
    "ParseWorkerError",
    "parse_pool_workers",
    "set_resource_governor",
    "should_offload",
    "shutdown_parse_pool",
    "submit",
]

_logger = logging.getLogger(__name__)

WORKERS_ENV = "PARSE_POOL_WORKERS"
# With the setting unset the pool takes half of the governor's CPU-bound parse
# ceiling, and never more than this.
DEFAULT_MAX_WORKERS = 4
# A worker with nothing to do for this long exits and hands its memory back.
IDLE_SECONDS = 60.0
WORKER_START_SECONDS = 120.0
# How often a waiting lane checks whether its job was abandoned.
_POLL_SECONDS = 0.25
_PYTHON_ROOT = str(Path(__file__).resolve().parents[3])


class ParseWorkerError(ParseError):
    """A parse worker died or overran. The message is the reason people see."""

    def __init__(self, message: str, cause: str) -> None:
        super().__init__(ParseErrorCode.PARSE_FAILED, message, {"parse_worker": cause})
        self.cause = cause


class ParseAbandonedError(IndexingError):
    """The caller stopped waiting, or the pool is shutting down.

    An ``IndexingError`` so that a record caught by a shutdown is retried
    rather than marked as a bad file.
    """


class _WorkerUnavailable(Exception):
    """A worker process could not be started at all."""


class _PoolClosed(Exception):
    pass


class _State:
    governor: ResourceGovernor | None = None
    pool: _ParsePool | None = None
    lock = threading.Lock()


def set_resource_governor(governor: ResourceGovernor | None) -> None:
    """Turn the pool on for this process, sized from *governor*.

    Called from the lifespan of the services that parse under admission
    control (indexing, parsing). A process that never calls it keeps parsing
    in threads, which is what the query service and the tests want.
    """
    _State.governor = governor


def parse_pool_workers() -> int:
    """How many worker processes this process may run. 0 means no pool.

    Never more than the governor's heavy-parse ceiling, which is its count of
    CPU-bound parses the cgroup can carry (after the embedding reservation and
    any ``MAX_CONCURRENT_PARSING`` cap).
    """
    governor = _State.governor
    # Workers are handed their pipes with ``pass_fds``, which is POSIX only.
    if governor is None or os.name != "posix":
        return 0
    cpu_bound_ceiling = max(1, governor.ceilings.heavy)
    configured = env_int(WORKERS_ENV, -1)
    if configured < 0:
        return max(1, min(DEFAULT_MAX_WORKERS, cpu_bound_ceiling // 2))
    return min(configured, cpu_bound_ceiling)


def should_offload(size: int) -> bool:
    """True when a payload of *size* should be parsed in a worker process."""
    return size >= DEFAULT_OFFLOAD_THRESHOLD_BYTES and parse_pool_workers() > 0


@dataclass
class _Job:
    fn: Callable[..., object]
    args: tuple
    label: str
    size: int
    future: Future = field(default_factory=Future)
    cancelled: threading.Event = field(default_factory=threading.Event)


class _Worker:
    """One parse worker process and the two pipes to it."""

    def __init__(self) -> None:
        request_r, request_w = os.pipe()
        reply_r, reply_w = os.pipe()
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (_PYTHON_ROOT, env.get("PYTHONPATH")) if p
        )
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-m", parse_worker.__name__, str(request_r), str(reply_w)],
                pass_fds=(request_r, reply_w),
                stdin=subprocess.DEVNULL,
                env=env,
            )
        except OSError as exc:
            for fd in (request_r, request_w, reply_r, reply_w):
                os.close(fd)
            raise _WorkerUnavailable(str(exc)) from exc
        os.close(request_r)
        os.close(reply_w)
        self._requests = os.fdopen(request_w, "wb")
        self._replies = os.fdopen(reply_r, "rb", buffering=0)
        # poll, not select: a busy service holds more than the 1024 descriptors
        # select can address, and these pipes are opened late.
        self._reply_poller = select.poll()
        self._reply_poller.register(self._replies, select.POLLIN)

    def send(self, obj: object) -> None:
        # Blocks until the worker has read it, which it is always doing here: a
        # worker is sent a job only while it waits for one.
        parse_worker.write_frame(self._requests, obj)

    def reply_ready(self, timeout: float) -> bool:
        """True once a reply (or the end of a dead worker's pipe) can be read."""
        return bool(self._reply_poller.poll(timeout * 1000))

    def receive(self) -> object:
        return pickle.loads(parse_worker.read_frame(self._replies))

    def alive(self) -> bool:
        return self.process.poll() is None

    def stop(self) -> None:
        with suppress(OSError):
            self.process.kill()
        for stream in (self._requests, self._replies):
            with suppress(OSError):
                stream.close()
        with suppress(subprocess.TimeoutExpired):
            self.process.wait(timeout=5)


class _Lane:
    """One thread and the worker it owns. Nothing else touches that worker."""

    def __init__(self, pool: _ParsePool) -> None:
        self._pool = pool
        self._worker: _Worker | None = None

    def serve(self) -> None:
        jobs = self._pool.jobs
        while True:
            try:
                job = jobs.get(timeout=IDLE_SECONDS)
            except queue.Empty:
                self._drop_worker()
                continue
            if job is None:
                self._drop_worker()
                return
            if not job.future.set_running_or_notify_cancel():
                continue
            try:
                result = self._run(job)
            except BaseException as exc:  # handed to the waiting caller
                job.future.set_exception(exc)
            else:
                job.future.set_result(result)

    def _drop_worker(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.stop()

    def _start_worker(self, job: _Job) -> _Worker:
        worker = _Worker()
        deadline = time.monotonic() + WORKER_START_SECONDS
        try:
            while not worker.reply_ready(_POLL_SECONDS):
                if self._abandoned(job):
                    raise ParseAbandonedError("parse abandoned before its worker started")
                if time.monotonic() >= deadline:
                    raise _WorkerUnavailable("the worker did not start in time")
            worker.receive()
        except (EOFError, OSError, pickle.UnpicklingError) as exc:
            worker.stop()
            raise _WorkerUnavailable(f"the worker exited while starting: {exc}") from exc
        except BaseException:
            worker.stop()
            raise
        self._worker = worker
        return worker

    def _abandoned(self, job: _Job) -> bool:
        return job.cancelled.is_set() or self._pool.closed.is_set()

    def _send(self, job: _Job) -> _Worker:
        """Hand the job to a live worker. A worker found dead here died idle,
        which is no job's fault, so it is replaced and the job sent again."""
        for attempt in (1, 2):
            worker = self._worker
            if worker is None or not worker.alive():
                self._drop_worker()
                worker = self._start_worker(job)
            try:
                worker.send((job.fn, job.args))
            except OSError:
                self._drop_worker()
                if attempt == 2:
                    raise
            else:
                return worker
        raise AssertionError("unreachable")

    def _run(self, job: _Job) -> object:
        if self._abandoned(job):
            raise ParseAbandonedError("parse abandoned before it started")
        try:
            worker = self._send(job)
        except _WorkerUnavailable as exc:
            _logger.error(
                "Could not start a parse worker (%s); parsing %s in a thread instead",
                exc, job.label,
            )
            return job.fn(*job.args)
        except OSError as exc:
            raise self._crashed(job) from exc

        timeout = messaging_env.record_processing_timeout
        deadline = time.monotonic() + timeout
        while not worker.reply_ready(_POLL_SECONDS):
            if self._abandoned(job):
                self._drop_worker()
                raise ParseAbandonedError("parse abandoned while it was running")
            if time.monotonic() >= deadline:
                self._drop_worker()
                _logger.warning(
                    "Parsing %s (%d bytes) was stopped after %.0fs "
                    "(RECORD_PROCESSING_TIMEOUT); its worker was replaced",
                    job.label, job.size, timeout,
                )
                raise ParseWorkerError(PROCESSING_TIMED_OUT, "timed_out")

        try:
            ok, payload, remote_traceback = worker.receive()
        except (EOFError, OSError, pickle.UnpicklingError) as exc:
            raise self._crashed(job) from exc
        if ok:
            return payload
        payload.add_note(f"Raised in a parse worker:\n{remote_traceback}")
        raise payload

    def _crashed(self, job: _Job) -> ParseWorkerError:
        self._drop_worker()
        _logger.warning(
            "The parse worker died while parsing %s (%d bytes), most likely out of memory. "
            "That record fails; a fresh worker takes the next file.",
            job.label, job.size,
        )
        governor = _State.governor
        if governor is not None:
            # Proof of memory exhaustion that the periodic sampler may not see
            # for several seconds, same as a dead Docling or rasterizer worker.
            governor.report_memory_incident("parse worker died (most likely OOM-killed)")
        return ParseWorkerError(PARSE_WORKER_OUT_OF_MEMORY, "crashed")


class _ParsePool:
    def __init__(self, workers: int) -> None:
        self.workers = workers
        self.jobs: queue.Queue[_Job | None] = queue.Queue()
        self.closed = threading.Event()
        self._submit_lock = threading.Lock()
        self._threads = [
            threading.Thread(target=_Lane(self).serve, name=f"parse-pool-{i}", daemon=True)
            for i in range(workers)
        ]
        for thread in self._threads:
            thread.start()

    async def submit(self, job: _Job) -> object:
        with self._submit_lock:
            if self.closed.is_set():
                raise _PoolClosed
            self.jobs.put(job)
        try:
            return await asyncio.wrap_future(job.future)
        except asyncio.CancelledError:
            # A job still queued is dropped by the cancel itself; this stops
            # one that is already running, by having its lane kill the worker.
            job.cancelled.set()
            raise

    def shutdown(self) -> None:
        with self._submit_lock:
            self.closed.set()
        for _ in self._threads:
            self.jobs.put(None)
        for thread in self._threads:
            thread.join(timeout=5)


def _pool() -> _ParsePool | None:
    workers = parse_pool_workers()
    if workers <= 0:
        return None
    with _State.lock:
        if _State.pool is None:
            _State.pool = _ParsePool(workers)
            _logger.info("Parse pool ready: up to %d worker process(es)", workers)
        return _State.pool


async def submit(fn: Callable[..., T], *args: object, label: str = "", size: int = 0) -> T:
    """Run ``fn(*args)`` in a worker process and return its result.

    *fn* must be a module-level function, and it, its arguments and its result
    must pickle. Callers check ``should_offload`` first; without a pool this
    runs in a thread, so it is always safe to call.

    Raises ``ParseWorkerError`` when the worker died or ran past
    ``RECORD_PROCESSING_TIMEOUT``. Only this job fails.
    """
    pool = _pool()
    if pool is not None:
        try:
            return await pool.submit(_Job(fn, args, label, size))
        except _PoolClosed:
            pass
    return await asyncio.to_thread(fn, *args)


def shutdown_parse_pool() -> bool:
    """Stop the workers, killing any job still running. True if a pool existed."""
    with _State.lock:
        pool, _State.pool = _State.pool, None
    if pool is None:
        return False
    pool.shutdown()
    return True


atexit.register(shutdown_parse_pool)
