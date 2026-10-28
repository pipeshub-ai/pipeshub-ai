"""Rasterising an SVG from a document in a process with a deadline and a memory cap.

cairosvg has no time or memory limit, and a few kilobytes of SVG can make it
spend minutes (references that each draw their target twice, thousands of
``<use>`` or ``<text>`` elements). A thread cannot be stopped, so the work runs
in a process that can be killed:

* ``render_async`` uses a parse-pool worker when the service has a pool
  (``parse_pool.submit`` kills and replaces the worker at the deadline), and
  otherwise a child process of its own;
* ``run_bounded`` is the child-process form, for synchronous callers.

Both end in ``render_in_worker`` under ``memory_cap``. A refusal, a deadline, a
crash or running out of memory raises ``DocumentProcessingError``; callers skip
the image and the document still indexes.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from app.exceptions.indexing_exceptions import DocumentProcessingError

if TYPE_CHECKING:
    from collections.abc import Iterator

_logger = logging.getLogger(__name__)

SVG_DEADLINE_SECONDS = 10.0
# What a rasterisation may add to the address space the process already has.
SVG_EXTRA_MEMORY_BYTES = 1024 * 1024 * 1024
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


def run_bounded(
    svg: str,
    width: int | None = None,
    height: int | None = None,
    scale: float = 1.0,
    background: str | None = None,
    timeout: float = SVG_DEADLINE_SECONDS,
) -> str:
    """Rasterise in a child process, killing it (and its group) at ``timeout``."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (_PYTHON_ROOT, env.get("PYTHONPATH")) if p)
    payload = json.dumps(_request(svg, width, height, scale, background)).encode()
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", _MODULE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        raise DocumentProcessingError("SVG renderer could not be started", details={"error": str(exc)}) from exc
    try:
        out, _ = process.communicate(payload, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill(process)
        process.communicate()
        _logger.warning("An SVG was not rasterised: it ran past %.0fs and was stopped", timeout)
        raise DocumentProcessingError("SVG rasterising timed out", details={"timeout_s": timeout}) from exc
    finally:
        if process.poll() is None:
            _kill(process)
    try:
        reply = json.loads(out)
    except ValueError as exc:
        _logger.warning("An SVG was not rasterised: the renderer died (exit %s)", process.returncode)
        raise DocumentProcessingError(
            "SVG rasterising failed", details={"exit_code": process.returncode}
        ) from exc
    if "png" not in reply:
        raise DocumentProcessingError(reply.get("error") or "SVG rasterising failed")
    return reply["png"]


def _kill(process: subprocess.Popen) -> None:
    with contextlib.suppress(OSError):
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover
            process.kill()


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
    try:
        request = json.loads(sys.stdin.buffer.read())
        reply = {"png": render_in_worker(request)}
    except MemoryError:
        reply = {"error": "SVG needs too much memory to rasterise"}
    except Exception as exc:
        reply = {"error": f"{type(exc).__name__}: {exc}"[:500]}
    sys.stdout.write(json.dumps(reply))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
