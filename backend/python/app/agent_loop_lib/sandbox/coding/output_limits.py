"""Byte caps on what a sandbox run hands back to the app.

A run's stdout and stderr, and the files it leaves in its output and
working directories, are written by generated code, so their size is chosen
by that code. Without a cap, one ``print`` loop or one sparse
``truncate -s 50G`` file in ``$OUTPUT_DIR`` becomes gigabytes of app memory
and app disk. Everything here reads incrementally and keeps at most a fixed
number of bytes, whatever the source sends.

Settings (bytes unless noted; an unset, blank or invalid value uses the
default):

``SANDBOX_STDIO_HEAD_BYTES`` (196608) / ``SANDBOX_STDIO_TAIL_BYTES`` (65536)
    How much of the start and the end of stdout and of stderr is kept. The
    middle is replaced by one ``[... N bytes truncated ...]`` line.
``SANDBOX_OUTPUT_MAX_FILE_BYTES`` (26214400, the artifact size limit)
    Largest single file collected from a run's output and working
    directories. Larger files are left out with a note to the model.
``SANDBOX_OUTPUT_MAX_TOTAL_BYTES`` (104857600)
    Most bytes collected from one run, all files together. Collection stops
    there with a note.
``SANDBOX_OUTPUT_MAX_FILES`` (2000)
    Most archive entries read from one run.
``SANDBOX_DEPS_MAX_TOTAL_BYTES`` (2147483648)
    Most bytes taken back from one package install (``pip --target`` /
    ``node_modules``), and the largest single file in it.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
import tarfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ExtractBudget",
    "ExtractLimits",
    "HeadTail",
    "cap_text",
    "extract_tar_stream",
    "read_capped_stream",
    "read_stdio_capped",
    "stdio_limits",
]

logger = logging.getLogger(__name__)

ENV_STDIO_HEAD_BYTES = "SANDBOX_STDIO_HEAD_BYTES"
ENV_STDIO_TAIL_BYTES = "SANDBOX_STDIO_TAIL_BYTES"
ENV_OUTPUT_MAX_FILE_BYTES = "SANDBOX_OUTPUT_MAX_FILE_BYTES"
ENV_OUTPUT_MAX_TOTAL_BYTES = "SANDBOX_OUTPUT_MAX_TOTAL_BYTES"
ENV_OUTPUT_MAX_FILES = "SANDBOX_OUTPUT_MAX_FILES"
ENV_DEPS_MAX_TOTAL_BYTES = "SANDBOX_DEPS_MAX_TOTAL_BYTES"

DEFAULT_STDIO_HEAD_BYTES = 192 * 1024
DEFAULT_STDIO_TAIL_BYTES = 64 * 1024
DEFAULT_OUTPUT_MAX_FILE_BYTES = 25 * 1024 * 1024
DEFAULT_OUTPUT_MAX_TOTAL_BYTES = 100 * 1024 * 1024
DEFAULT_OUTPUT_MAX_FILES = 2000
DEFAULT_DEPS_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
_DEPS_MAX_FILES = 200_000

# Bytes of tar headers and padding allowed per entry on top of the data
# budget, so a stream of headers alone cannot run forever either.
_HEADER_ALLOWANCE = 2048
_READ_CHUNK = 64 * 1024


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("Ignoring %s=%r: use a whole number of bytes; using %d", name, raw, default)
        return default
    if value < minimum:
        logger.warning("Ignoring %s=%r: below %d; using %d", name, raw, minimum, default)
        return default
    return value


def _human(n: int) -> str:
    size = float(n)
    for unit in ("bytes", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{int(size)} bytes" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} bytes"


# ---------------------------------------------------------------------------
# stdout / stderr
# ---------------------------------------------------------------------------

def stdio_limits() -> tuple[int, int]:
    """``(head, tail)`` bytes kept of each of stdout and stderr."""
    return (
        _env_int(ENV_STDIO_HEAD_BYTES, DEFAULT_STDIO_HEAD_BYTES),
        _env_int(ENV_STDIO_TAIL_BYTES, DEFAULT_STDIO_TAIL_BYTES),
    )


class HeadTail:
    """Keeps the first ``head`` and the last ``tail`` bytes of a stream fed
    in chunks, and counts the rest. Memory stays at ``head + tail`` plus one
    chunk, however much is fed."""

    def __init__(self, head: int | None = None, tail: int | None = None) -> None:
        default_head, default_tail = stdio_limits() if head is None or tail is None else (head, tail)
        self._head_max = head if head is not None else default_head
        self._tail_max = tail if tail is not None else default_tail
        self._head = bytearray()
        self._tail = bytearray()
        self.total = 0

    def feed(self, data: bytes | bytearray | memoryview) -> None:
        if not data:
            return
        view = memoryview(data)
        self.total += len(view)
        room = self._head_max - len(self._head)
        if room > 0:
            self._head += view[:room]
            view = view[room:]
        if not len(view) or self._tail_max <= 0:
            return
        if len(view) >= self._tail_max:
            self._tail = bytearray(view[len(view) - self._tail_max:])
            return
        self._tail += view
        excess = len(self._tail) - self._tail_max
        if excess > 0:
            del self._tail[:excess]

    @property
    def dropped(self) -> int:
        return self.total - len(self._head) - len(self._tail)

    @property
    def truncated(self) -> bool:
        return self.dropped > 0

    def text(self) -> str:
        if not self.truncated:
            return bytes(self._head + self._tail).decode(errors="replace")
        return (
            bytes(self._head).decode(errors="replace")
            + f"\n[... {self.dropped} bytes truncated ...]\n"
            + bytes(self._tail).decode(errors="replace")
        )


def cap_text(text: str | None) -> str:
    """``text`` cut to the stdio head and tail, for output that already
    arrived as one string (a provider SDK that buffers it)."""
    if not text:
        return text or ""
    head, tail = stdio_limits()
    if len(text) <= head + tail:
        # A character is at least one byte, so this cannot be over the cap
        # in bytes by more than the encoding expands it; check exactly.
        encoded = text.encode(errors="replace")
        if len(encoded) <= head + tail:
            return text
    else:
        encoded = text.encode(errors="replace")
    buf = HeadTail(head, tail)
    buf.feed(encoded)
    return buf.text()


def read_capped_stream(chunks: Any) -> str:
    """Drain a docker-py log stream (bytes, or an iterator of bytes chunks)
    keeping only the stdio head and tail."""
    buf = HeadTail()
    if isinstance(chunks, (bytes, bytearray)):
        buf.feed(chunks)
        return buf.text()
    if isinstance(chunks, str):
        return cap_text(chunks)
    try:
        iterator = iter(chunks)
    except TypeError:
        return ""
    for chunk in iterator:
        if isinstance(chunk, str):
            chunk = chunk.encode(errors="replace")
        if isinstance(chunk, (bytes, bytearray, memoryview)):
            buf.feed(chunk)
    return buf.text()


async def _drain(reader: asyncio.StreamReader | None, buf: HeadTail) -> None:
    if reader is None:
        return
    while True:
        chunk = await reader.read(_READ_CHUNK)
        if not chunk:
            return
        buf.feed(chunk)


async def read_stdio_capped(proc: asyncio.subprocess.Process) -> tuple[str, str]:
    """`proc.communicate()` for a process started with piped stdout and
    stderr, keeping only the stdio head and tail of each. Waits for both
    pipes to close and for the process to exit."""
    out, err = HeadTail(), HeadTail()
    await asyncio.gather(_drain(proc.stdout, out), _drain(proc.stderr, err))
    await proc.wait()
    return out.text(), err.text()


# ---------------------------------------------------------------------------
# Files collected from a run (tar archives from the container)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExtractLimits:
    max_file_bytes: int
    max_total_bytes: int
    max_files: int

    @classmethod
    def for_output(cls) -> ExtractLimits:
        """Limits for what a run leaves in ``/output`` and ``/src``."""
        return cls(
            max_file_bytes=_env_int(ENV_OUTPUT_MAX_FILE_BYTES, DEFAULT_OUTPUT_MAX_FILE_BYTES, minimum=1),
            max_total_bytes=_env_int(ENV_OUTPUT_MAX_TOTAL_BYTES, DEFAULT_OUTPUT_MAX_TOTAL_BYTES, minimum=1),
            max_files=_env_int(ENV_OUTPUT_MAX_FILES, DEFAULT_OUTPUT_MAX_FILES, minimum=1),
        )

    @classmethod
    def for_dependencies(cls) -> ExtractLimits:
        """Limits for an install's ``/deps`` or ``node_modules``."""
        total = _env_int(ENV_DEPS_MAX_TOTAL_BYTES, DEFAULT_DEPS_MAX_TOTAL_BYTES, minimum=1)
        return cls(max_file_bytes=total, max_total_bytes=total, max_files=_DEPS_MAX_FILES)


@dataclass
class ExtractBudget:
    """What one run may still collect, shared by every archive of the run.

    ``stream_limit`` bounds the bytes read off the socket, including the
    data of files that were skipped for being too large: a skipped file
    still has to be read past, and a 50 GB file must not be."""

    limits: ExtractLimits
    extracted_bytes: int = 0
    streamed_bytes: int = 0
    entries: int = 0
    skipped: list[str] = field(default_factory=list)
    stopped_reason: str | None = None

    @classmethod
    def for_output(cls) -> ExtractBudget:
        return cls(ExtractLimits.for_output())

    @classmethod
    def for_dependencies(cls) -> ExtractBudget:
        return cls(ExtractLimits.for_dependencies())

    @property
    def stream_limit(self) -> int:
        return 2 * self.limits.max_total_bytes

    @property
    def stopped(self) -> bool:
        return self.stopped_reason is not None

    def note(self) -> str:
        """One message for the model, or "" when nothing was left out."""
        if not self.skipped and not self.stopped:
            return ""
        parts = []
        if self.skipped:
            shown = ", ".join(self.skipped[:10])
            more = f" and {len(self.skipped) - 10} more" if len(self.skipped) > 10 else ""
            parts.append(
                f"these files were too large to collect (over {_human(self.limits.max_file_bytes)} "
                f"each) and are not available: {shown}{more}"
            )
        if self.stopped_reason:
            parts.append(self.stopped_reason)
        return (
            "[sandbox] Not all output was collected: " + "; ".join(parts)
            + ". Write smaller files (split, compress, or keep only what is needed).\n"
        )


class _ArchiveTooLarge(Exception):
    pass


class _ChunkReader(io.RawIOBase):
    """A read-only file over an iterator of byte chunks, refusing to read
    past ``limit`` bytes in total. Never holds more than one chunk."""

    def __init__(self, chunks: Iterable[bytes], limit: int) -> None:
        super().__init__()
        self._it: Iterator[bytes] = iter(chunks)
        self._view = memoryview(b"")
        self._limit = limit
        self.consumed = 0

    def readable(self) -> bool:
        return True

    def readinto(self, b: Any) -> int:
        while not len(self._view):
            try:
                chunk = next(self._it)
            except StopIteration:
                return 0
            if chunk:
                self._view = memoryview(chunk)
        n = min(len(b), len(self._view))
        if self.consumed + n > self._limit:
            raise _ArchiveTooLarge
        b[:n] = self._view[:n]
        self._view = self._view[n:]
        self.consumed += n
        return n


_warned_tar_filter = False


def _warn_if_tar_filter_unpatched() -> None:
    """The ``data`` extraction filter had path bypasses fixed in 3.12.11 /
    3.13.4 (CVE-2025-4517 and related). Extraction leans on it, so say so
    once when this interpreter predates the fix."""
    global _warned_tar_filter
    if _warned_tar_filter:
        return
    _warned_tar_filter = True
    v = sys.version_info
    fixed = {(3, 12): 11, (3, 13): 4, (3, 11): 13, (3, 10): 18, (3, 9): 23}
    patch = fixed.get((v.major, v.minor))
    if patch is not None and v.micro < patch:
        logger.error(
            "Python %d.%d.%d has known bypasses of tarfile's 'data' filter, which sandbox "
            "output extraction relies on; upgrade to %d.%d.%d or later",
            v.major, v.minor, v.micro, v.major, v.minor, patch,
        )


def extract_tar_stream(
    chunks: Iterable[bytes],
    container_path: str,
    local_dir: str,
    budget: ExtractBudget,
    *,
    allow_symlinks: bool = False,
) -> None:
    """Extract the tar stream ``chunks`` (``get_archive`` of
    ``container_path``) into ``local_dir`` within ``budget``.

    Reads the stream as it arrives: nothing is spooled, at most one member
    is being written at a time, and at most ``budget.stream_limit`` data
    bytes (plus header allowance) are read off the socket.

    - Only regular files are extracted, plus symlinks when
      ``allow_symlinks``; hardlinks, devices and FIFOs are skipped.
    - A file larger than the per-file limit is skipped by name; once the
      total or the entry count is reached, collection stops.
    - Sizes are the sizes in the tar header, which for a sparse file is its
      full apparent size, so a sparse file cannot expand past the limits.
    - Every member is sanitised by ``filter="data"``, and one whose own
      path would land outside ``local_dir`` is skipped.
    """
    _warn_if_tar_filter_unpatched()
    limits = budget.limits
    if budget.stopped:
        return
    hard_limit = (
        max(0, budget.stream_limit - budget.streamed_bytes)
        + (limits.max_files + 1) * _HEADER_ALLOWANCE + 1024 * 1024
    )
    reader = _ChunkReader(chunks, hard_limit)
    resolved_root = os.path.realpath(local_dir)
    prefix = os.path.basename(container_path.rstrip("/")) + "/"
    current_target: str | None = None
    try:
        with tarfile.open(fileobj=reader, mode="r|*") as tar:
            for member in tar:
                current_target = None
                if member.isdir():
                    continue
                budget.entries += 1
                if budget.entries > limits.max_files:
                    budget.stopped_reason = (
                        f"collection stopped after {limits.max_files} files from {container_path}"
                    )
                    return
                is_link = member.issym() or member.islnk()
                if not member.isfile() and not (allow_symlinks and is_link):
                    logger.warning(
                        "Skipping non-regular tar member %r (type %r) from %s",
                        member.name, member.type, container_path,
                    )
                    continue
                if member.name.startswith(prefix):
                    member.name = member.name[len(prefix):]
                if not member.name:
                    continue
                target = os.path.realpath(os.path.join(local_dir, member.name))
                if not target.startswith(resolved_root + os.sep) and target != resolved_root:
                    logger.warning(
                        "Skipping tar member with path traversal: %s -> %s",
                        member.name, target,
                    )
                    continue
                size = member.size if member.isfile() else 0
                shown = f"{container_path.rstrip('/')}/{member.name} ({_human(size)})"
                if size > limits.max_file_bytes:
                    if budget.streamed_bytes + size > budget.stream_limit:
                        budget.stopped_reason = (
                            f"collection stopped at {shown}, which is over the "
                            f"{_human(limits.max_file_bytes)} per-file limit, so it and any later "
                            "files were not collected"
                        )
                        logger.warning("sandbox output: stopped at oversized member %s", shown)
                        return
                    budget.streamed_bytes += size
                    budget.skipped.append(shown)
                    logger.warning("sandbox output: skipped oversized member %s", shown)
                    continue
                if budget.extracted_bytes + size > limits.max_total_bytes:
                    budget.stopped_reason = (
                        f"collection stopped at {shown} because the run's output reached "
                        f"the {_human(limits.max_total_bytes)} total limit"
                    )
                    logger.warning("sandbox output: total limit reached at %s", shown)
                    return
                current_target = target
                try:
                    # `data` filter rejects a link whose target escapes the
                    # destination; skip that member rather than abort the run.
                    tar.extract(member, local_dir, filter="data")
                except _ArchiveTooLarge:
                    raise
                except Exception as exc:
                    logger.warning(
                        "Skipping unsafe tar member %r from %s: %s",
                        member.name, container_path, exc,
                    )
                    continue
                current_target = None
                budget.extracted_bytes += size
                budget.streamed_bytes += size
    except _ArchiveTooLarge:
        budget.stopped_reason = (
            f"collection stopped because the archive of {container_path} went past "
            f"the {_human(budget.stream_limit)} read limit"
        )
        logger.warning("sandbox output: archive of %s went past the read limit", container_path)
        if current_target and os.path.lexists(current_target) and not os.path.isdir(current_target):
            try:
                os.unlink(current_target)
            except OSError:
                pass
    finally:
        close = getattr(chunks, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
