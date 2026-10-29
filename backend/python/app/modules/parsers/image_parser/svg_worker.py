"""Rasterising an SVG from a document in a process with a deadline and a memory cap.

cairosvg has no time or memory limit, and a few kilobytes of SVG can make it
spend minutes (references that each draw their target twice, thousands of
``<use>`` or ``<text>`` elements). A thread cannot be stopped, so the work runs
in a process that can be killed:

* ``run_bounded`` renders in a child process; ``render_async`` runs it off
  the event loop. Not the shared parse pool: there an SVG could queue behind
  heavy parses and wait for a cold worker before any deadline started. The
  child is a small process (``svg_core`` and cairosvg only) that stays up and
  renders one SVG after another, so only the first SVG pays its start-up. The
  deadline covers the whole request (waiting for a renderer, its start-up, the
  write and the render). A renderer gets only an allowlisted environment, dies
  with its parent (PR_SET_PDEATHSIG), has a CPU limit, is killed at the deadline
  and replaced by a warm spare, and is closed when idle. At most four exist.

The renderer runs under ``memory_cap``. A refusal, a deadline, a
crash or running out of memory raises ``DocumentProcessingError``; callers skip
the image and the document still indexes.
"""
from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import contextlib
import json
import logging
import os
import select
import signal
import subprocess
import sys
import threading
import time
import weakref
from pathlib import Path
from typing import TYPE_CHECKING

from app.exceptions.indexing_exceptions import DocumentProcessingError

if TYPE_CHECKING:
    from collections.abc import Iterator

_logger = logging.getLogger(__name__)

SVG_DEADLINE_SECONDS = 10.0
# What a rasterisation may add to the address space the process already has.
SVG_EXTRA_MEMORY_BYTES = 1024 * 1024 * 1024
# A renderer is closed after this long unused. At most this many exist in all
# (busy and idle); a caller that finds them all busy waits for one until its
# deadline. Worst-case memory is therefore this many times SVG_EXTRA_MEMORY_BYTES.
_IDLE_SECONDS = 30.0
_MAX_RENDERERS = 4
# How often an async caller checks for a free renderer slot while it waits.
_ADMISSION_POLL_SECONDS = 0.02
# How long close() waits for a killed renderer before reaping it in the background.
_REAP_WAIT_SECONDS = 0.2
_PYTHON_ROOT = str(Path(__file__).resolve().parents[4])
_MODULE = "app.modules.parsers.image_parser.svg_worker"


@contextlib.contextmanager
def memory_cap(extra_bytes: int = SVG_EXTRA_MEMORY_BYTES) -> Iterator[None]:
    """Limit the address space to what is in use now plus ``extra_bytes``.

    Soft limit only, restored afterwards, so a long-lived pool worker is not
    left capped. Where the size in use cannot be read, no cap is set.
    """
    try:
        import resource

        with open("/proc/self/statm") as statm:
            in_use = int(statm.read().split()[0]) * resource.getpagesize()
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    except (ImportError, OSError, ValueError):
        yield
        return
    cap = in_use + extra_bytes
    if hard != resource.RLIM_INFINITY:
        cap = min(cap, hard)
    if soft != resource.RLIM_INFINITY:
        cap = min(cap, soft)
    resource.setrlimit(resource.RLIMIT_AS, (cap, hard))
    try:
        yield
    finally:
        resource.setrlimit(resource.RLIMIT_AS, (soft, hard))


def _request(svg: str, width: int | None, height: int | None, scale: float, background: str | None) -> dict:
    return {"svg": svg, "width": width, "height": height, "scale": scale, "background": background}


# The renderer parses hostile input, so it gets only what Python, cairo and
# fontconfig need, never the service's secrets (database URIs, API keys).
_ENV_ALLOWLIST = frozenset({
    "PATH", "HOME", "TMPDIR", "TZ", "LANG", "LANGUAGE", "LD_LIBRARY_PATH",
    "FONTCONFIG_FILE", "FONTCONFIG_PATH", "XDG_CACHE_HOME", "XDG_CONFIG_HOME",
    "XDG_DATA_HOME", "XDG_DATA_DIRS",
    # The locale categories by name: a prefix would also pass LC_SECRET_TOKEN.
    "LC_ALL", "LC_CTYPE", "LC_COLLATE", "LC_MESSAGES", "LC_MONETARY", "LC_NUMERIC",
    "LC_TIME", "LC_ADDRESS", "LC_IDENTIFICATION", "LC_MEASUREMENT", "LC_NAME",
    "LC_PAPER", "LC_TELEPHONE",
})
# `-I` (isolated): no PYTHON* variables, no working directory on sys.path.
_BOOT = "import runpy, sys; sys.path.insert(0, sys.argv[1]); runpy.run_module(sys.argv[3], run_name='__main__')"
# CPU time a renderer may spend on one SVG beyond the wall-clock deadline.
_CPU_SLACK_SECONDS = 2


def _renderer_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key in _ENV_ALLOWLIST
    }


def _read_line(fd: int, deadline: float) -> bytes | None:
    """One line from ``fd`` before ``deadline`` (monotonic), or None on timeout or EOF."""
    chunks: list[bytes] = []
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not poller.poll(remaining * 1000):
            return None
        data = os.read(fd, 1 << 20)
        if not data:
            return None
        chunks.append(data)
        if data.endswith(b"\n"):
            return b"".join(chunks)


def _write_all(fd: int, data: bytes, deadline: float) -> bool:
    """Write ``data`` to a non-blocking ``fd`` before ``deadline``; False if it did not fit."""
    view = memoryview(data)
    poller = select.poll()
    poller.register(fd, select.POLLOUT)
    while view:
        try:
            view = view[os.write(fd, view) :]
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not poller.poll(remaining * 1000):
                return False
    return True


class _Spawner:
    """Starts renderers from one thread that lives as long as the process.

    ``PR_SET_PDEATHSIG`` fires when the *thread* that started a child exits, so
    a child started from a short-lived thread would be killed with it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None

    def start(self) -> subprocess.Popen:
        with self._lock:
            if self._executor is None:
                self._executor = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="svg-spawn")
            executor = self._executor
        return executor.submit(self._popen).result()

    @staticmethod
    def _popen() -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, "-I", "-c", _BOOT, _PYTHON_ROOT, str(os.getpid()), _MODULE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_renderer_environment(),
            cwd="/" if os.name == "posix" else None,
            bufsize=0,
            start_new_session=os.name == "posix",
        )

    def reset(self) -> None:
        self._lock = threading.Lock()
        self._executor = None


class _Renderer:
    """One renderer process. Used by one thread at a time."""

    def __init__(self, process: subprocess.Popen) -> None:
        self.process = process
        self.last_used = time.monotonic()
        self._stdin_fd = process.stdin.fileno()
        os.set_blocking(self._stdin_fd, False)

    def alive(self) -> bool:
        return self.process.poll() is None

    def render(self, request: dict, timeout: float, deadline: float) -> str:
        """The PNG for ``request``. Anything that goes wrong kills the renderer.

        ``deadline`` (monotonic) covers writing the request, a start-up still in
        progress, and the render itself.
        """
        line = json.dumps({**request, "cpu": timeout + _CPU_SLACK_SECONDS}).encode() + b"\n"
        try:
            if not _write_all(self._stdin_fd, line, deadline):
                return self._fail("timed out", timeout)
            reply_line = _read_line(self.process.stdout.fileno(), deadline)
        except OSError:
            return self._fail("failed", timeout)
        self.last_used = time.monotonic()
        if reply_line is None:
            return self._fail("timed out" if self.alive() else "failed", timeout)
        try:
            reply = json.loads(reply_line)
            if not isinstance(reply, dict):
                raise TypeError("reply is not an object")
            png, error = reply.get("png"), reply.get("error")
            if png is not None and not isinstance(png, str):
                raise TypeError("png is not a string")
        except (ValueError, TypeError, RecursionError):
            _logger.warning("An SVG was not rasterised: the renderer sent an unreadable reply")
            self.close()
            raise DocumentProcessingError("SVG renderer sent an unreadable reply") from None
        if png is None:
            # A refusal the renderer reported itself; it is still healthy.
            raise DocumentProcessingError(error if isinstance(error, str) and error else "SVG rasterising failed")
        return png

    def _fail(self, what: str, timeout: float) -> str:
        self.close()
        if what == "timed out":
            _logger.warning("An SVG was not rasterised: it ran past %.0fs and was stopped", timeout)
            raise DocumentProcessingError("SVG rasterising timed out", details={"timeout_s": timeout})
        _logger.warning("An SVG was not rasterised: the renderer died")
        raise DocumentProcessingError("SVG rasterising failed")

    _closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(OSError):
            if os.name == "posix":
                os.killpg(self.process.pid, signal.SIGKILL)
            else:  # pragma: no cover
                self.process.kill()
        self.release_pipes()
        # A killed process is normally gone at once; one stuck in the kernel must
        # not hold the caller (and its waiter thread) past the deadline, so it is
        # reaped in the background.
        try:
            self.process.wait(timeout=_REAP_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            threading.Thread(target=self.process.wait, name="svg-reaper", daemon=True).start()

    def release_pipes(self) -> None:
        for stream in (self.process.stdin, self.process.stdout):
            with contextlib.suppress(OSError):
                stream.close()


class _Renderers:
    """Every renderer of this process, busy or idle, at most ``_MAX_RENDERERS``."""

    def __init__(self) -> None:
        self._idle: list[_Renderer] = []
        self._all: set[_Renderer] = set()
        self._cond = threading.Condition(threading.Lock())
        self._reaper: threading.Thread | None = None
        self._spawner = _spawner
        _pools.add(self)

    def acquire(self, deadline: float) -> _Renderer:
        """An idle renderer, a new one, or (all busy) a wait for one until ``deadline``."""
        with self._cond:
            while True:
                while self._idle:
                    renderer = self._idle.pop()
                    if renderer.alive():
                        return renderer
                    self._forget(renderer)
                if len(self._all) < _MAX_RENDERERS:
                    return self._start_locked()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _logger.warning("An SVG was not rasterised: every renderer is busy")
                    raise DocumentProcessingError("SVG renderers are busy")
                self._cond.wait(remaining)

    def _start_locked(self) -> _Renderer:
        try:
            renderer = _Renderer(self._spawner.start())
        except OSError as exc:
            raise DocumentProcessingError("SVG renderer could not be started", details={"error": str(exc)}) from exc
        self._all.add(renderer)
        return renderer

    def _forget(self, renderer: _Renderer) -> None:
        renderer.close()
        self._all.discard(renderer)
        self._cond.notify()

    def release(self, renderer: _Renderer, *, healthy: bool = True) -> None:
        with self._cond:
            if not healthy or not renderer.alive():
                self._forget(renderer)
                # Start the replacement now, so the next SVG finds a renderer
                # that is already importing cairosvg.
                if len(self._all) < _MAX_RENDERERS and not self._idle:
                    with contextlib.suppress(DocumentProcessingError):
                        self._idle.append(self._start_locked())
                        self._ensure_reaper()
                return
            self._idle.append(renderer)
            self._cond.notify()
            self._ensure_reaper()

    def _ensure_reaper(self) -> None:
        if self._reaper is None or not self._reaper.is_alive():
            self._reaper = threading.Thread(target=self._reap, name="svg-renderer-reaper", daemon=True)
            self._reaper.start()

    def _reap(self) -> None:
        while True:
            time.sleep(_IDLE_SECONDS / 4)
            with self._cond:
                now = time.monotonic()
                for renderer in [r for r in self._idle if now - r.last_used >= _IDLE_SECONDS]:
                    self._idle.remove(renderer)
                    self._forget(renderer)
                if not self._idle:
                    return

    def close_all(self) -> None:
        """Kill every renderer, the ones in use as well."""
        with self._cond:
            everyone, self._all, self._idle = list(self._all), set(), []
            self._cond.notify_all()
        for renderer in everyone:
            renderer.close()

    def after_fork_in_child(self) -> None:
        """Forget the renderers of the parent without touching them.

        They belong to the parent: killing them, or even asking whether they are
        alive (the child cannot wait on them), would break the parent. Only this
        process's copies of their pipes are closed.
        """
        for renderer in self._all:
            renderer.release_pipes()
        self._all = set()
        self._idle = []
        self._cond = threading.Condition(threading.Lock())
        self._reaper = None


_spawner = _Spawner()
_pools: weakref.WeakSet[_Renderers] = weakref.WeakSet()
_renderers = _Renderers()


@atexit.register
def _close_every_renderer() -> None:
    for pool in list(_pools):
        pool.close_all()


# Threads that wait on renderers for async callers. One per renderer, so a
# waiter never holds a thread of the loop's default executor (which every
# asyncio.to_thread shares) while all renderers are busy.
class _WaiterThreads:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pool: concurrent.futures.ThreadPoolExecutor | None = None
        # Admission: a call holds one from before it is submitted until its job
        # ends, so the pool never queues work (or the SVG it carries).
        self.slots = threading.Semaphore(_MAX_RENDERERS)

    def get(self) -> concurrent.futures.ThreadPoolExecutor:
        with self._lock:
            if self._pool is None:
                self._pool = concurrent.futures.ThreadPoolExecutor(
                    max_workers=_MAX_RENDERERS, thread_name_prefix="svg-render"
                )
            return self._pool

    def forget(self) -> None:
        # After fork: the parent's threads do not exist here, and its lock may be held.
        self._lock = threading.Lock()
        self._pool = None
        self.slots = threading.Semaphore(_MAX_RENDERERS)


_waiters = _WaiterThreads()


def _after_fork_in_child() -> None:
    _waiters.forget()
    _spawner.reset()
    for pool in list(_pools):
        pool.after_fork_in_child()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_in_child)


def run_bounded(
    svg: str,
    width: int | None = None,
    height: int | None = None,
    scale: float = 1.0,
    background: str | None = None,
    timeout: float = SVG_DEADLINE_SECONDS,
    deadline: float | None = None,
) -> str:
    """Rasterise in a renderer process within ``timeout`` seconds in all.

    The deadline covers waiting for a free renderer, a renderer that is still
    starting, and the render. A renderer that overruns is killed and a new one
    is started at once for the next SVG. Worst-case memory is
    ``_MAX_RENDERERS`` times ``SVG_EXTRA_MEMORY_BYTES`` plus their baselines.

    ``deadline`` (monotonic), when given, replaces ``timeout``: a caller that
    queued the call first passes the deadline it set before queueing.
    """
    if deadline is None:
        deadline = time.monotonic() + timeout
    timeout = deadline - time.monotonic()
    if timeout <= 0:
        raise DocumentProcessingError("SVG rasterising timed out before it started")
    pool = _renderers
    renderer = pool.acquire(deadline)
    healthy = False
    try:
        result = renderer.render(_request(svg, width, height, scale, background), timeout, deadline)
        healthy = True
        return result
    except DocumentProcessingError:
        # A refusal reported by a healthy renderer keeps it; every failure that
        # closed it is seen as not alive.
        healthy = renderer.alive()
        raise
    finally:
        pool.release(renderer, healthy=healthy)


async def render_async(svg: str) -> str:
    """Rasterise for async callers; see the module docstring."""
    deadline = time.monotonic() + SVG_DEADLINE_SECONDS
    waiters = _waiters
    while not waiters.slots.acquire(blocking=False):
        if time.monotonic() >= deadline:
            raise DocumentProcessingError("SVG rasterising timed out waiting for a renderer")
        await asyncio.sleep(_ADMISSION_POLL_SECONDS)
    # A slot freed after the deadline but before this poll must not admit a job
    # that is already late.
    if time.monotonic() >= deadline:
        waiters.slots.release()
        raise DocumentProcessingError("SVG rasterising timed out waiting for a renderer")

    def job() -> str:
        try:
            return run_bounded(svg, deadline=deadline)
        finally:
            waiters.slots.release()

    try:
        future = waiters.get().submit(job)
    except BaseException:
        waiters.slots.release()
        raise
    # A job cancelled before it starts never reaches its finally.
    future.add_done_callback(lambda f: f.cancelled() and waiters.slots.release())
    return await asyncio.wrap_future(future)


def _die_with_parent(parent_pid: int) -> None:
    """Exit when the parent does (``PR_SET_PDEATHSIG``), and at once if it is already gone."""
    if os.name != "posix" or not sys.platform.startswith("linux"):
        return
    import ctypes

    ctypes.CDLL(None, use_errno=True).prctl(1, int(signal.SIGKILL), 0, 0, 0)  # PR_SET_PDEATHSIG
    if os.getppid() != parent_pid:
        os._exit(0)


def _limit_cpu(seconds: float) -> None:
    """Make the kernel stop this process after ``seconds`` more CPU time (SIGXCPU)."""
    try:
        import resource
    except ImportError:  # pragma: no cover
        return
    used = time.process_time()
    resource.setrlimit(resource.RLIMIT_CPU, (int(used + seconds) + 1, resource.RLIM_INFINITY))


def main() -> None:
    """The renderer process: one JSON request per line in, one JSON reply per line out."""
    if len(sys.argv) > 2:
        _die_with_parent(int(sys.argv[2]))
    # A crash while parsing hostile input must not write its memory to a core file.
    import resource

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    from cairosvg import svg2png

    from app.modules.parsers.image_parser import svg_core

    # Replies go to the original stdout; anything else that prints goes to stderr.
    replies = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    # The cap is for the life of the process: it only ever renders.
    cap = memory_cap()
    cap.__enter__()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            _limit_cpu(float(request.get("cpu", SVG_DEADLINE_SECONDS + _CPU_SLACK_SECONDS)))
            png = svg_core.convert(
                request["svg"],
                request.get("width"),
                request.get("height"),
                request.get("scale", 1.0),
                request.get("background"),
                svg2png=svg2png,
            )
            reply = {"png": png}
        except MemoryError:
            reply = {"error": "SVG needs too much memory to rasterise"}
        except Exception as exc:
            reply = {"error": f"{type(exc).__name__}: {exc}"[:500]}
        replies.write(json.dumps(reply) + "\n")
        replies.flush()


if __name__ == "__main__":
    main()
