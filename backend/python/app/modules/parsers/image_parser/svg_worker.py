"""Rasterising an SVG from a document in a process with a deadline and a memory cap.

cairosvg has no time or memory limit, and a few kilobytes of SVG can make it
spend minutes (references that each draw their target twice, thousands of
``<use>`` or ``<text>`` elements). A thread cannot be stopped, so the work runs
in a process that can be killed:

* ``render_async`` uses a parse-pool worker when the service has a pool
  (``parse_pool.submit`` kills and replaces the worker at the deadline), and
  otherwise a child process of its own;
* ``run_bounded`` is the child-process form, for synchronous callers. The
  child is a small process (``svg_core`` and cairosvg only) that stays up and
  renders one SVG after another, so only the first SVG pays its start-up; it is
  killed at the deadline of any one SVG and replaced, and closed when idle.

Both end in ``render_in_worker`` under ``memory_cap``. A refusal, a deadline, a
crash or running out of memory raises ``DocumentProcessingError``; callers skip
the image and the document still indexes.
"""
from __future__ import annotations

import asyncio
import atexit
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
from pathlib import Path
from typing import TYPE_CHECKING

from app.exceptions.indexing_exceptions import DocumentProcessingError

if TYPE_CHECKING:
    from collections.abc import Iterator

_logger = logging.getLogger(__name__)

SVG_DEADLINE_SECONDS = 10.0
# What a rasterisation may add to the address space the process already has.
SVG_EXTRA_MEMORY_BYTES = 1024 * 1024 * 1024
# A renderer is closed after this long unused; at most this many wait for a caller.
_IDLE_SECONDS = 30.0
_MAX_IDLE_RENDERERS = 4
# The first SVG a new renderer gets also waits for it to start.
_START_ALLOWANCE_SECONDS = 5.0
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


def render_in_worker(request: dict) -> str:
    """PNG base64 for ``request`` (the arguments of ``svg_base64_to_png_base64``).

    Module level and picklable, for ``parse_pool.submit``.
    """
    from app.modules.parsers.image_parser.image_parser import ImageParser

    with memory_cap():
        return ImageParser._svg_to_png_in_process(
            request["svg"],
            request.get("width"),
            request.get("height"),
            request.get("scale", 1.0),
            request.get("background"),
        )


def _request(svg: str, width: int | None, height: int | None, scale: float, background: str | None) -> dict:
    return {"svg": svg, "width": width, "height": height, "scale": scale, "background": background}


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


class _Renderer:
    """One renderer process. Used by one thread at a time."""

    def __init__(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(p for p in (_PYTHON_ROOT, env.get("PYTHONPATH")) if p)
        self.process = subprocess.Popen(
            [sys.executable, "-m", _MODULE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=os.name == "posix",
        )
        self.last_used = time.monotonic()

    def alive(self) -> bool:
        return self.process.poll() is None

    def render(self, request: dict, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        line = json.dumps(request).encode() + b"\n"
        try:
            self.process.stdin.write(line)
            self.process.stdin.flush()
        except OSError as exc:
            self.close()
            raise DocumentProcessingError("SVG renderer is not available") from exc
        # The first reply also covers the child's start-up.
        reply_line = _read_line(self.process.stdout.fileno(), deadline + _START_ALLOWANCE_SECONDS * (not self.used))
        self.used = True
        self.last_used = time.monotonic()
        if reply_line is None:
            timed_out = self.alive()
            self.close()
            if timed_out:
                _logger.warning("An SVG was not rasterised: it ran past %.0fs and was stopped", timeout)
                raise DocumentProcessingError("SVG rasterising timed out", details={"timeout_s": timeout})
            _logger.warning("An SVG was not rasterised: the renderer died")
            raise DocumentProcessingError("SVG rasterising failed")
        reply = json.loads(reply_line)
        if "png" not in reply:
            raise DocumentProcessingError(reply.get("error") or "SVG rasterising failed")
        return reply["png"]

    used = False

    def close(self) -> None:
        with contextlib.suppress(OSError):
            if os.name == "posix":
                os.killpg(self.process.pid, signal.SIGKILL)
            else:  # pragma: no cover
                self.process.kill()
        for stream in (self.process.stdin, self.process.stdout):
            with contextlib.suppress(OSError):
                stream.close()
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.process.wait(timeout=5)


class _Renderers:
    """A few idle renderers, handed out one per call and closed when idle."""

    def __init__(self) -> None:
        self._idle: list[_Renderer] = []
        self._lock = threading.Lock()
        self._reaper: threading.Thread | None = None

    def acquire(self) -> _Renderer:
        with self._lock:
            while self._idle:
                renderer = self._idle.pop()
                if renderer.alive():
                    return renderer
                renderer.close()
        try:
            return _Renderer()
        except OSError as exc:
            raise DocumentProcessingError("SVG renderer could not be started", details={"error": str(exc)}) from exc

    def release(self, renderer: _Renderer) -> None:
        if not renderer.alive():
            renderer.close()
            return
        with self._lock:
            if len(self._idle) >= _MAX_IDLE_RENDERERS:
                renderer.close()
                return
            self._idle.append(renderer)
            if self._reaper is None or not self._reaper.is_alive():
                self._reaper = threading.Thread(target=self._reap, name="svg-renderer-reaper", daemon=True)
                self._reaper.start()

    def _reap(self) -> None:
        while True:
            time.sleep(_IDLE_SECONDS / 4)
            with self._lock:
                now = time.monotonic()
                stale = [r for r in self._idle if now - r.last_used >= _IDLE_SECONDS]
                self._idle = [r for r in self._idle if r not in stale]
                done = not self._idle
            for renderer in stale:
                renderer.close()
            if done:
                return

    def close_all(self) -> None:
        with self._lock:
            idle, self._idle = self._idle, []
        for renderer in idle:
            renderer.close()


_renderers = _Renderers()
atexit.register(_renderers.close_all)


def run_bounded(
    svg: str,
    width: int | None = None,
    height: int | None = None,
    scale: float = 1.0,
    background: str | None = None,
    timeout: float = SVG_DEADLINE_SECONDS,
) -> str:
    """Rasterise in a renderer process, killing it at ``timeout`` (a new one takes the next SVG)."""
    renderer = _renderers.acquire()
    try:
        return renderer.render(_request(svg, width, height, scale, background), timeout)
    finally:
        _renderers.release(renderer)


async def render_async(svg: str) -> str:
    """Rasterise for async callers; see the module docstring."""
    from app.modules.parsers import parse_pool

    if parse_pool.parse_pool_workers() > 0:
        try:
            return await parse_pool.submit(
                render_in_worker,
                _request(svg, None, None, 1.0, None),
                label="svg",
                size=len(svg),
                timeout=SVG_DEADLINE_SECONDS,
            )
        except parse_pool.ParseWorkerError as exc:
            _logger.warning("An SVG was not rasterised: %s", exc.cause)
            raise DocumentProcessingError(
                "SVG rasterising failed or timed out", details={"cause": exc.cause}
            ) from exc
    return await asyncio.to_thread(run_bounded, svg)


def main() -> None:
    """The renderer process: one JSON request per line in, one JSON reply per line out."""
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
