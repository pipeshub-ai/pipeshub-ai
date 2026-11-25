"""One metered reader for every untrusted tar or zip the platform opens (SEC-C-05, SEC-C-07).

Limits hold as the bytes are read, never after: a member's declared size is checked from its header before any
of its data is inflated, the bytes actually produced are counted by an ``ExpansionMeter`` (total, ratio to the
compressed bytes consumed, member count), and a link, device or unsafe path is refused from its header. Callers
get each member as a stream to copy where they like (memory, a file), or the whole archive as a dict.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import stat
import tarfile
import zipfile
import zlib
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING, Final, Literal, Protocol, override

from harness_core.archive_limits import (
    DEFAULT_MAX_RATIO,
    DEFAULT_RATIO_FLOOR_BYTES,
    EXPANDED_TOO_LARGE,
    RATIO_TOO_HIGH,
    TOO_MANY_MEMBERS,
    ExpansionLimits,
    ExpansionMeter,
    ExpansionRefused,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
    from pathlib import Path

ENTRY_TOO_LARGE: Final = "entry_too_large"
UNSAFE_PATH: Final = "unsafe_path"
LINK_ENTRY: Final = "link_entry"
SPECIAL_ENTRY: Final = "special_entry"
ENCRYPTED: Final = "encrypted"
CORRUPT: Final = "corrupt"
NOT_AN_ARCHIVE: Final = "not_an_archive"

_CHUNK: Final = 64 * 1024
# A pax or GNU long-name header is metadata: a few KiB at most in any real archive. tarfile reads it whole into
# memory, so it is bounded from its own header before that read.
_MAX_META_BYTES: Final = 1024 * 1024
# What a tar stream carries beyond its members' data: a header block and up to a block of padding per member.
_TAR_OVERHEAD_PER_MEMBER: Final = 3 * tarfile.BLOCKSIZE
_ZIP_LOCAL_HEADER: Final = 30

ArchiveKind = Literal["tar", "zip"]


class _Reads(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


class ArchiveRefused(ExpansionRefused):
    """An archive was not read: past a limit, unsafe, or not an archive. ``str()`` is safe to show a person."""


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    max_total_bytes: int
    max_entry_bytes: int
    max_entries: int
    max_ratio: float = DEFAULT_MAX_RATIO
    ratio_floor_bytes: int = DEFAULT_RATIO_FLOOR_BYTES

    def __post_init__(self) -> None:
        if min(self.max_total_bytes, self.max_entry_bytes, self.max_entries) <= 0 or self.max_ratio <= 0:
            raise ValueError("archive limits must be positive")

    def expansion(self, max_bytes: int) -> ExpansionLimits:
        return ExpansionLimits(
            max_bytes=max_bytes,
            max_ratio=self.max_ratio,
            ratio_floor_bytes=self.ratio_floor_bytes,
            max_members=self.max_entries,
        )


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    """A member as its header declares it; ``path`` has passed the traversal check, without a trailing slash."""

    path: str
    size: int
    is_dir: bool


def safe_entry_path(name: str) -> str:
    """``name`` as a relative path, or ``ArchiveRefused``: no absolute path, drive, backslash, NUL, ``.``/``..``
    or empty segment, so writing it under a root can never land outside that root."""
    path = name[:-1] if name.endswith("/") else name
    segments = path.split("/")
    unsafe = (
        not path
        or "\\" in path
        or "\x00" in path
        or path.startswith("/")
        or (len(path) > 1 and path[1] == ":")
        or any(segment in ("", ".", "..") for segment in segments)
    )
    if unsafe:
        raise ArchiveRefused(f"the archive contains an unsafe path: {name!r}", reason=UNSAFE_PATH)
    return path


def _refuse_entry_size(path: str, size: int, limits: ArchiveLimits, declared_total: int) -> None:
    if size < 0 or size > limits.max_entry_bytes:
        raise ArchiveRefused(
            f"the archive member {path!r} is larger than {limits.max_entry_bytes} bytes", reason=ENTRY_TOO_LARGE
        )
    if declared_total > limits.max_total_bytes:
        raise ArchiveRefused(
            f"the archive expands past {limits.max_total_bytes} bytes", reason=EXPANDED_TOO_LARGE
        )


class _Counted(io.RawIOBase):
    """The compressed side: every byte read is consumed on the meter, so the ratio sees the real input."""

    def __init__(self, source: IO[bytes], meter: ExpansionMeter) -> None:
        super().__init__()
        self._source = source
        self._meter = meter

    @override
    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray | memoryview) -> int:  # type: ignore[override]
        chunk = self._source.read(len(buffer))
        self._meter.consume(len(chunk))
        buffer[: len(chunk)] = chunk
        return len(chunk)


class _Produced(io.RawIOBase):
    """The decompressed side: every byte handed on is produced on the meter, which refuses past a limit."""

    def __init__(self, source: _Reads, meter: ExpansionMeter, *, also_consumed: bool) -> None:
        super().__init__()
        self._source = source
        self._meter = meter
        self._also_consumed = also_consumed

    @override
    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray | memoryview) -> int:  # type: ignore[override]
        # Never more than a chunk per call: a decompressor asked for "all" would inflate the bomb before counting.
        chunk = self._source.read(min(len(buffer), _CHUNK))
        if self._also_consumed:
            self._meter.consume(len(chunk))
        self._meter.produce(len(chunk))
        buffer[: len(chunk)] = chunk
        return len(chunk)


_DAMAGE: Final = (tarfile.TarError, zipfile.BadZipFile, OSError, EOFError, lzma.LZMAError, zlib.error)


def _refusal(exc: Exception) -> ArchiveRefused:
    if isinstance(exc, ArchiveRefused):
        return exc
    if isinstance(exc, ExpansionRefused):
        return ArchiveRefused(str(exc), reason=exc.reason_code)
    return ArchiveRefused(f"the archive is damaged ({type(exc).__name__})", reason=CORRUPT)


class _Guarded(io.RawIOBase):
    """A member's stream as handed to a caller: any failure while reading it is one ``ArchiveRefused``."""

    def __init__(self, source: _Reads) -> None:
        super().__init__()
        self._source = source

    @override
    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray | memoryview) -> int:  # type: ignore[override]
        try:
            chunk = self._source.read(min(len(buffer), _CHUNK))
        except (ExpansionRefused, *_DAMAGE) as exc:
            raise _refusal(exc) from exc
        buffer[: len(chunk)] = chunk
        return len(chunk)


def _member_stream(source: _Reads) -> IO[bytes]:
    return io.BufferedReader(_Guarded(source), _CHUNK)


class _BoundedTarInfo(tarfile.TarInfo):
    def _proc_member(self, tarfile_: tarfile.TarFile) -> tarfile.TarInfo:  # type: ignore[override]
        meta = (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE, tarfile.GNUTYPE_LONGNAME,
                tarfile.GNUTYPE_LONGLINK)
        if self.type in meta and self.size > _MAX_META_BYTES:
            raise ArchiveRefused(
                f"the archive has a {self.size}-byte metadata header", reason=ENTRY_TOO_LARGE
            )
        return super()._proc_member(tarfile_)  # type: ignore[misc]


def _source_of(data: bytes | IO[bytes]) -> IO[bytes]:
    return io.BytesIO(data) if isinstance(data, (bytes, bytearray, memoryview)) else data


def _head(source: IO[bytes], size: int) -> bytes:
    start = source.tell()
    head = source.read(size)
    _ = source.seek(start)
    return head


_GZIP: Final = b"\x1f\x8b"
_BZIP2: Final = b"BZh"
_XZ: Final = b"\xfd7zXZ\x00"


def _plain_tar_header(block: bytes) -> bool:
    if len(block) < tarfile.BLOCKSIZE:
        return False
    try:
        _ = tarfile.TarInfo.frombuf(block[: tarfile.BLOCKSIZE], "utf-8", "surrogateescape")
    except tarfile.HeaderError:
        return False
    return True


def archive_kind(data: bytes | IO[bytes]) -> ArchiveKind | None:
    """What the bytes are, from their own signature, never a file name: a compressed or plain tar, or a zip."""
    source = _source_of(data)
    head = _head(source, tarfile.BLOCKSIZE)
    if head.startswith((_GZIP, _BZIP2, _XZ)) or _plain_tar_header(head):
        return "tar"
    start = source.tell()
    try:
        return "zip" if zipfile.is_zipfile(source) else None
    finally:
        _ = source.seek(start)


def _decompressed(source: IO[bytes], meter: ExpansionMeter) -> _Reads | None:
    head = _head(source, 6)
    counted = io.BufferedReader(_Counted(source, meter), _CHUNK)
    if head.startswith(_GZIP):
        return gzip.GzipFile(fileobj=counted, mode="rb")
    if head.startswith(_BZIP2):
        return bz2.BZ2File(counted)
    if head.startswith(_XZ):
        return lzma.LZMAFile(counted)
    return None


def _iter_tar(source: IO[bytes], limits: ArchiveLimits) -> Iterator[tuple[ArchiveEntry, IO[bytes] | None]]:
    stream_cap = limits.max_total_bytes + limits.max_entries * _TAR_OVERHEAD_PER_MEMBER + _MAX_META_BYTES
    meter = ExpansionMeter(limits.expansion(stream_cap))
    decompressor = _decompressed(source, meter)
    if decompressor is None:
        raw: IO[bytes] = _Produced(source, meter, also_consumed=True)  # type: ignore[assignment]
    else:
        raw = _Produced(decompressor, meter, also_consumed=False)  # type: ignore[assignment]
    declared_total = 0
    try:
        with tarfile.open(fileobj=raw, mode="r|", tarinfo=_BoundedTarInfo) as archive:
            while (member := archive.next()) is not None:
                meter.member()
                path = safe_entry_path(member.name)
                if member.issym() or member.islnk():
                    raise ArchiveRefused(f"the archive contains a link: {member.name!r}", reason=LINK_ENTRY)
                if member.isdir():
                    yield ArchiveEntry(path, 0, True), None
                    continue
                if not member.isreg() or member.issparse():
                    raise ArchiveRefused(
                        f"the archive contains a special file: {member.name!r}", reason=SPECIAL_ENTRY
                    )
                declared_total += member.size
                _refuse_entry_size(path, member.size, limits, declared_total)
                stream = archive.extractfile(member)
                yield ArchiveEntry(path, member.size, False), (
                    _member_stream(stream) if stream is not None else io.BytesIO()
                )
    except (ExpansionRefused, *_DAMAGE) as exc:
        raise _refusal(exc) from exc


def zip_declared_entries(data: bytes | IO[bytes]) -> int | None:
    """The member count a zip's end record declares, read before the central directory is parsed; ``None`` when
    there is no end record."""
    source = _source_of(data)
    start = source.tell()
    try:
        end = zipfile._EndRecData(source)  # type: ignore[attr-defined]
    except OSError:
        end = None
    finally:
        _ = source.seek(start)
    if not end:
        return None
    return int(end[zipfile._ECD_ENTRIES_TOTAL])  # type: ignore[attr-defined]


def open_zip(data: bytes | IO[bytes], limits: ArchiveLimits) -> zipfile.ZipFile:
    """A ``ZipFile`` whose central directory holds at most ``limits.max_entries`` members, checked from the end
    record before the directory is parsed (a small zip can declare millions of members)."""
    source = _source_of(data)
    declared = zip_declared_entries(source)
    if declared is None:
        raise ArchiveRefused("the file is not a zip archive", reason=NOT_AN_ARCHIVE)
    if declared > limits.max_entries:
        raise ArchiveRefused(f"the archive has over {limits.max_entries} members", reason=TOO_MANY_MEMBERS)
    try:
        archive = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise ArchiveRefused(f"the zip archive is damaged ({type(exc).__name__})", reason=CORRUPT) from exc
    if len(archive.infolist()) > limits.max_entries:
        archive.close()
        raise ArchiveRefused(f"the archive has over {limits.max_entries} members", reason=TOO_MANY_MEMBERS)
    return archive


def _zip_mode(info: zipfile.ZipInfo) -> int:
    return info.external_attr >> 16 if info.create_system == 3 else 0


def _check_zip_layout(infos: list[zipfile.ZipInfo]) -> None:
    """Members whose data overlaps another's are a non-recursive bomb (one kernel inflated by every member)."""
    ordered = sorted(infos, key=lambda info: info.header_offset)
    for current, following in zip(ordered, ordered[1:], strict=False):
        least_end = current.header_offset + _ZIP_LOCAL_HEADER + len(current.orig_filename) + current.compress_size
        if least_end > following.header_offset:
            raise ArchiveRefused("the zip archive has overlapping members", reason=CORRUPT)


class _ZipEntryReader(io.RawIOBase):
    def __init__(self, member: _Reads, meter: ExpansionMeter, entry: ArchiveEntry) -> None:
        super().__init__()
        self._member = member
        self._meter = meter
        self._entry = entry
        self._produced = 0

    @override
    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray | memoryview) -> int:  # type: ignore[override]
        chunk = self._member.read(min(len(buffer), _CHUNK))
        self._produced += len(chunk)
        if self._produced > self._entry.size:
            raise ArchiveRefused(
                f"the archive member {self._entry.path!r} expands past the size it declares", reason=CORRUPT
            )
        self._meter.produce(len(chunk))
        buffer[: len(chunk)] = chunk
        return len(chunk)


def _iter_zip(source: IO[bytes], limits: ArchiveLimits) -> Iterator[tuple[ArchiveEntry, IO[bytes] | None]]:
    archive = open_zip(source, limits)
    meter = ExpansionMeter(limits.expansion(limits.max_total_bytes))
    declared_total = 0
    try:
        with archive:
            infos = archive.infolist()
            _check_zip_layout(infos)
            for info in infos:
                meter.member()
                path = safe_entry_path(info.filename)
                mode = _zip_mode(info)
                if stat.S_ISLNK(mode):
                    raise ArchiveRefused(f"the archive contains a link: {info.filename!r}", reason=LINK_ENTRY)
                if info.is_dir():
                    yield ArchiveEntry(path, 0, True), None
                    continue
                if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                    raise ArchiveRefused(
                        f"the archive contains a special file: {info.filename!r}", reason=SPECIAL_ENTRY
                    )
                if info.flag_bits & 0x1:
                    raise ArchiveRefused("encrypted zip archives are not supported", reason=ENCRYPTED)
                declared_total += info.file_size
                _refuse_entry_size(path, info.file_size, limits, declared_total)
                if info.file_size > limits.ratio_floor_bytes and info.file_size > limits.max_ratio * max(
                    info.compress_size, 1
                ):
                    raise ArchiveRefused(
                        f"the archive expands more than {limits.max_ratio:g} times its size", reason=RATIO_TOO_HIGH
                    )
                meter.consume(info.compress_size)
                entry = ArchiveEntry(path, info.file_size, False)
                with archive.open(info) as member:
                    reader = _member_stream(_ZipEntryReader(member, meter, entry))
                    yield entry, reader
                    # Whatever the caller left unread is still inflated: the CRC is checked only at the end.
                    while reader.read(_CHUNK):
                        pass
    except (ExpansionRefused, *_DAMAGE) as exc:
        raise _refusal(exc) from exc
    except (NotImplementedError, RuntimeError) as exc:
        raise ArchiveRefused("the zip archive uses an unsupported compression or encryption", reason=CORRUPT) from exc


def iter_archive(
    data: bytes | IO[bytes], limits: ArchiveLimits, *, kind: ArchiveKind | None = None
) -> Iterator[tuple[ArchiveEntry, IO[bytes] | None]]:
    """Each member in order with a stream of its bytes (``None`` for a directory). A stream is valid only until the
    next member is asked for; reading it is metered, and a limit crossed raises ``ArchiveRefused`` mid-read."""
    source = _source_of(data)
    found = kind or archive_kind(source)
    if found == "tar":
        return _iter_tar(source, limits)
    if found == "zip":
        return _iter_zip(source, limits)
    raise ArchiveRefused("the file is not a zip or tar archive", reason=NOT_AN_ARCHIVE)


def read_archive(
    data: bytes | IO[bytes],
    limits: ArchiveLimits,
    *,
    kind: ArchiveKind | None = None,
    on_entry: Callable[[ArchiveEntry], None] | None = None,
) -> dict[str, bytes]:
    """Every file member's bytes by path, directories left out. ``on_entry`` sees each header before its data is
    read and may raise to refuse it."""
    files: dict[str, bytes] = {}
    for entry, stream in iter_archive(data, limits, kind=kind):
        if on_entry is not None:
            on_entry(entry)
        if stream is not None:
            files[entry.path] = stream.read()
    return files


def extract_archive(data: bytes | IO[bytes], limits: ArchiveLimits, root: Path, *, kind: ArchiveKind | None = None) -> list[str]:
    """Writes every member under ``root`` as it is read (never whole in memory); an existing path is refused."""
    written: list[str] = []
    for entry, stream in iter_archive(data, limits, kind=kind):
        target = root.joinpath(*entry.path.split("/"))
        if stream is None:
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as out:
            while chunk := stream.read(_CHUNK):
                _ = out.write(chunk)
        written.append(entry.path)
    return written


def spool_chunks(chunks: Iterable[bytes], out: IO[bytes], *, max_bytes: int) -> int:
    """Copies a stream of chunks into ``out`` (rewound after), refusing past ``max_bytes``: a sandbox fills what it
    hands back, so its archive is bounded before a byte of it is parsed."""
    size = 0
    for chunk in chunks:
        size += len(chunk)
        if size > max_bytes:
            raise ArchiveRefused(f"the archive is larger than {max_bytes} bytes", reason=EXPANDED_TOO_LARGE)
        _ = out.write(chunk)
    _ = out.seek(0)
    return size


def check_zip(data: bytes | IO[bytes], limits: ArchiveLimits) -> tuple[str, ...]:
    """Inflates every member under ``limits``, discarding the bytes, and checks each CRC; returns the member names.
    Raises ``ArchiveRefused`` for a damaged archive or one past a limit."""
    names: list[str] = []
    for entry, stream in iter_archive(data, limits, kind="zip"):
        names.append(entry.path + ("/" if entry.is_dir else ""))
        if stream is not None:
            while stream.read(_CHUNK):
                pass
    return tuple(names)


__all__ = [
    "CORRUPT",
    "ENCRYPTED",
    "ENTRY_TOO_LARGE",
    "LINK_ENTRY",
    "NOT_AN_ARCHIVE",
    "SPECIAL_ENTRY",
    "UNSAFE_PATH",
    "ArchiveEntry",
    "ArchiveKind",
    "ArchiveLimits",
    "ArchiveRefused",
    "archive_kind",
    "check_zip",
    "extract_archive",
    "iter_archive",
    "open_zip",
    "read_archive",
    "safe_entry_path",
    "spool_chunks",
    "zip_declared_entries",
]
