"""Streaming tar for the sync: a writer that never holds more than a window of files, a bounded reader.

The writer emits PAX headers and content chunks as objects arrive from the
store. The reader spools a sandbox's archive to a size-capped temporary file
(memory up to a threshold, then disk), validates every header before any byte
is used, and reads file contents in batches off the event loop.
"""

from __future__ import annotations

import asyncio
import posixpath
import stat
import tarfile
import tempfile
from collections import deque
from dataclasses import dataclass, field
from typing import IO, TYPE_CHECKING

from harness_core.archive_limits import ExpansionLimits, ExpansionMeter
from harness_core.workspace.sync.rules import member_path
from harness_core.workspace.sync.types import (
    EXPANSION_LIMIT,
    FILE_TOO_LARGE,
    HARD_LINK,
    SPECIAL_FILE,
    ArchiveMalformed,
    ArchiveTooLarge,
    TooManyEntries,
    UnsafeArchiveMember,
)
from harness_core.workspace.types import SYMLINK_MODE

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
    from types import TracebackType

    from harness_core.workspace.sync.types import SyncLimits
    from harness_core.workspace.types import WorkspaceFile

BLOCK = tarfile.BLOCKSIZE
RECORD = tarfile.RECORDSIZE
CHUNK = 64 * 1024
DIR_MODE = 0o755
_SPOOL_IN_MEMORY = 16 * 1024**2
_READ_BATCH_FILES = 256
_READ_BATCH_BYTES = 8 * 1024**2


@dataclass(frozen=True, slots=True)
class Owner:
    """The sandbox user, every engine image's ``USER`` and the one the broker runs each sandbox process as: the owner of
    what the sync writes, so that user can edit and delete it, and of a handed-over repository, so its git reads it."""

    uid: int = 1000
    gid: int = 1000


def _header(info: tarfile.TarInfo) -> bytes:
    return info.tobuf(format=tarfile.PAX_FORMAT, encoding="utf-8", errors="surrogateescape")


def _info(name: str, owner: Owner, mtime: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.uid, info.gid, info.mtime = owner.uid, owner.gid, mtime
    return info


def _parents(paths: Sequence[str]) -> list[str]:
    folders = {"/".join(parts[:i]) for parts in (p.split("/") for p in paths) for i in range(1, len(parts))}
    return sorted(folders, key=lambda folder: (folder.count("/"), folder))


async def write_tree(
    files: Sequence[WorkspaceFile],
    fetch: Callable[[WorkspaceFile], Awaitable[bytes]],
    *,
    owner: Owner,
    mtime: int,
    window: int = 8,
) -> AsyncIterator[bytes]:
    """A tar of ``files`` (and their parent folders) with contents from ``fetch``, ``window`` fetches ahead.

    Parents come first, so a folder is created with the owner's id instead of
    the extractor's. A link's content is its target.
    """
    for folder in _parents([entry.path for entry in files]):
        info = _info(folder, owner, mtime)
        info.type, info.mode = tarfile.DIRTYPE, DIR_MODE
        yield _header(info)
    pending: deque[asyncio.Task[bytes]] = deque()
    upcoming = iter(files)
    written = 0

    def refill() -> None:
        while len(pending) < window:
            entry = next(upcoming, None)
            if entry is None:
                return
            pending.append(asyncio.ensure_future(fetch(entry)))

    try:
        refill()
        for entry in files:
            content = await pending.popleft()
            refill()
            info = _info(entry.path, owner, mtime)
            if entry.is_symlink:
                info.type, info.mode = tarfile.SYMTYPE, 0o777
                info.linkname = content.decode("utf-8", "surrogateescape")
                block = _header(info)
                written += len(block)
                yield block
                continue
            info.size, info.mode = len(content), stat.S_IMODE(entry.mode)
            block = _header(info)
            written += len(block)
            yield block
            for start in range(0, len(content), CHUNK):
                yield content[start : start + CHUNK]
            padding = -len(content) % BLOCK
            written += len(content) + padding
            if padding:
                yield bytes(padding)
    finally:
        for task in pending:
            _ = task.cancel()
    end = 2 * BLOCK
    yield bytes(end + (-(written + end) % RECORD))


@dataclass(frozen=True, slots=True)
class ScannedFile:
    """A regular file or link found in an archive; a link's content is its target."""

    path: str
    raw: str
    mode: int
    size: int
    member: tarfile.TarInfo


@dataclass(slots=True)
class ArchiveScan:
    """What one folder of a sandbox holds, validated header by header; contents are read on demand."""

    files: dict[str, ScannedFile] = field(default_factory=dict[str, ScannedFile])
    links: dict[str, str] = field(default_factory=dict[str, str])
    folders: set[str] = field(default_factory=set[str])
    # canonical path, or the raw relative name when it has none -> reason
    skipped: dict[str, str] = field(default_factory=dict[str, str])
    # raw relative names of everything that is not a folder, kept or not
    present: set[str] = field(default_factory=set[str])


class ArchiveReader:
    """Spool, validate, then read: ``async with ArchiveReader(...) as reader`` and ``reader.scan``."""

    def __init__(self, stream: AsyncIterator[bytes], *, base: str, limits: SyncLimits) -> None:
        self._stream = stream
        self._base = base
        self._limits = limits
        self._spool: IO[bytes] | None = None
        self._archive: tarfile.TarFile | None = None
        self._meter: ExpansionMeter | None = None
        self.scan = ArchiveScan()

    async def __aenter__(self) -> ArchiveReader:
        spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL_IN_MEMORY)
        self._spool = spool
        size = 0
        async for chunk in self._stream:
            size += len(chunk)
            if size > self._limits.max_archive_bytes:
                raise ArchiveTooLarge(f"the sandbox archive is over {self._limits.max_archive_bytes} bytes")
            _ = await asyncio.to_thread(spool.write, chunk)
        await asyncio.to_thread(self._index, size)
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._archive is not None:
            self._archive.close()
        if self._spool is not None:
            self._spool.close()

    def _index(self, size: int) -> None:
        assert self._spool is not None
        _ = self._spool.seek(0)
        if size == 0:
            return
        # Every kept file is read whole, a hard link as its target again: what they add up to is bounded by the
        # sync's cap and by its ratio to the archive (SEC-FS-03). Contents are read exactly as the headers say.
        self._meter = ExpansionMeter(ExpansionLimits.from_env(self._limits.max_expanded_bytes), consumed=size)
        # Headers only: tarfile seeks past each member's declared size and refuses to read past
        # the end, so a size beyond the spooled archive is a ReadError before any content is read.
        try:
            archive = tarfile.open(fileobj=self._spool, mode="r:")
            self._archive = archive
            members: list[tarfile.TarInfo] = []
            while (member := archive.next()) is not None:
                members.append(member)
                if len(members) > self._limits.max_entries:
                    raise TooManyEntries(f"the sandbox archive has over {self._limits.max_entries} entries")
        except tarfile.TarError as exc:
            raise ArchiveMalformed(f"the sandbox archive cannot be read: {type(exc).__name__}") from exc
        for member in members:
            self._classify(member)

    def _classify(self, member: tarfile.TarInfo) -> None:
        place = member_path(member.name, self._base)
        if place.path is None and place.skip is None:
            return
        scan = self.scan
        if member.isdir():
            if place.path is not None:
                scan.folders.add(place.path)
            return
        scan.present.add(place.raw)
        if place.path is None:
            scan.skipped[place.raw] = place.skip or ""
            return
        path = place.path
        if member.issym():
            scan.links[path] = member.linkname
            scan.files[path] = ScannedFile(path, place.raw, SYMLINK_MODE, len(member.linkname), member)
        elif member.islnk():
            self._hard_link(path, place.raw, member)
        elif member.isreg():
            if member.size > self._limits.max_file_bytes:
                scan.skipped[path] = FILE_TOO_LARGE
            elif self._expands(path, member.size):
                # Set-uid, set-gid and sticky bits are dropped: a workspace never carries them.
                mode = stat.S_IFREG | (member.mode & 0o777)
                scan.files[path] = ScannedFile(path, place.raw, mode, member.size, member)
        else:
            scan.skipped[path] = SPECIAL_FILE

    def _hard_link(self, path: str, raw: str, member: tarfile.TarInfo) -> None:
        try:
            target = member_path(member.linkname, self._base).path
        except UnsafeArchiveMember:
            # The member's own name was safe; only where it points is not.
            target = None
        source = self.scan.files.get(target) if target is not None else None
        if source is None or source.mode == SYMLINK_MODE:
            self.scan.skipped[path] = HARD_LINK
            return
        if self._expands(path, source.size):
            self.scan.files[path] = ScannedFile(path, raw, source.mode, source.size, source.member)

    def _expands(self, path: str, size: int) -> bool:
        """Whether ``size`` more bytes fit the expansion budget (counted when they do); a file past it is skipped,
        so the collect keeps what fits and names the rest."""
        meter = self._meter
        if meter is None or meter.fits(size):
            if meter is not None:
                meter.produce(size)
            return True
        self.scan.skipped[path] = EXPANSION_LIMIT
        return False

    async def contents(self, paths: Sequence[str]) -> AsyncIterator[tuple[ScannedFile, bytes]]:
        """Each scanned file's bytes (a link's target), read in bounded batches off the event loop."""
        queue = [self.scan.files[path] for path in paths]
        start = 0
        while start < len(queue):
            end, budget = start, 0
            while end < len(queue) and end - start < _READ_BATCH_FILES and budget < _READ_BATCH_BYTES:
                budget += queue[end].size
                end += 1
            batch = queue[start:end]
            for entry, data in zip(batch, await asyncio.to_thread(self._read, batch), strict=True):
                yield entry, data
            start = end

    def _read(self, batch: Sequence[ScannedFile]) -> list[bytes]:
        out: list[bytes] = []
        for entry in batch:
            if entry.mode == SYMLINK_MODE:
                out.append(entry.member.linkname.encode("utf-8", "surrogateescape"))
                continue
            assert self._archive is not None
            extracted = self._archive.extractfile(entry.member)
            data = extracted.read() if extracted is not None else b""
            if len(data) != entry.size:
                raise ArchiveMalformed("an archive member is shorter than its header says")
            out.append(data)
        return out


def archive_base(root: str) -> str:
    """The name ``get_tar(root)`` prefixes members with: the root's last segment."""
    return posixpath.basename(root.rstrip("/"))


__all__ = ["ArchiveReader", "ArchiveScan", "Owner", "ScannedFile", "archive_base", "write_tree"]
