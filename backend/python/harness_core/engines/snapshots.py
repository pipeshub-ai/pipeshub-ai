"""Encrypted sandbox snapshots (P5.9b): the sandbox's caches and tool state, kept between attempts and runs.

A snapshot is a tar of a fixed list of paths outside the workspace, ``/state`` and ``/skills`` (an engine's
``HOME`` caches), taken at autosave points and at turn end and restored before the engine opens. It never
holds a credential: every snapshot is scanned before it is stored, and one hit refuses it whole.

Capture and restore run ``tar`` inside the sandbox through ``SandboxHandle.exec``: ``HOME`` is the sandbox's
tmpfs, which a container runtime's archive API does not see, and ``exec`` is what every broker offers. The
archive comes from an untrusted sandbox, so it is parsed here, kept to regular files and folders under the
policy's paths, and re-written before it goes back into a sandbox.

Stored in the session's object scope through ``ObjectStoreSandboxSnapshots``: sealed with the session's data
key where the object store seals (PipesHub), so erasing the session erases its snapshots, and wrapped in an
envelope naming the session, so no store ever hands one session another's snapshot.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import posixpath
import re
import signal
import tarfile
import zlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from harness_core.archive_limits import (
    ExpansionLimits,
    ExpansionMeter,
    ExpansionRefused,
    inflate,
)
from harness_core.engines.errors import SnapshotRefused, SnapshotUnavailable
from harness_core.sandbox.errors import SandboxNotFound
from harness_core.security.token_patterns import VENDOR_TOKEN_PATTERNS
from harness_core.workspace.types import ObjectUnreadable, WorkspaceKeyDestroyed

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterable

    from harness_core.engines.ports import SandboxSnapshotStore
    from harness_core.logs import HarnessLog
    from harness_core.sandbox.ports import ProcessHandle, SandboxHandle
    from harness_core.workspace.ports import BinaryObjectStore
    from harness_core.workspace.types import ObjectRef, ObjectScope

SNAPSHOT_SECRET = "snapshot_secret"
SNAPSHOT_TOO_LARGE = "snapshot_too_large"
SNAPSHOT_UNSAFE = "snapshot_unsafe"
SNAPSHOT_SANDBOX_GONE = "snapshot_sandbox_gone"
SNAPSHOT_FAILED = "snapshot_failed"
SNAPSHOT_MISSING = "snapshot_missing"
SNAPSHOT_CORRUPT = "snapshot_corrupt"
SNAPSHOT_FOREIGN = "snapshot_foreign"
# A literal value of the run's (a token, a placeholder) found in a snapshot.
RUN_SECRET = "run_secret"

DEFAULT_MAX_BYTES = 64 * 1024 * 1024
# Shorter literals match ordinary bytes by chance; real tokens and placeholders are far longer.
MIN_SECRET_LENGTH = 8
CAPTURE_TIMEOUT_S = 120.0
_ENVELOPE_MAGIC = b"PXSNAP1\n"
# A decompressed body above this is not one of ours (the capture cap is far below it).
_MAX_BODY_BYTES = 1024**3
_PATH = re.compile(r"^(/[A-Za-z0-9._-]+)+$")
# Paths are validated constants passed as positional arguments, never spliced into the script, so word
# splitting on the found list is safe; a missing path is skipped, and nothing found prints nothing.
CAPTURE_SCRIPT = (
    'cd / || exit 70; found=""; '
    'for p in "$@"; do if [ -e "$p" ]; then found="$found $p"; fi; done; '
    '[ -n "$found" ] || exit 0; '
    "exec tar -c -f - $found"
)
CAPTURE_ARGV0 = "pxh-snapshot"
RESTORE_ARGV = ("tar", "-x", "-f", "-", "-C", "/")

SnapshotReason = Literal["autosave", "turn_end"]


@dataclass(frozen=True, slots=True)
class SnapshotPolicy:
    """What a snapshot may hold: absolute paths, each kept with everything under it, up to ``max_bytes`` of tar."""

    paths: tuple[str, ...]
    max_bytes: int = DEFAULT_MAX_BYTES

    def __post_init__(self) -> None:
        if self.max_bytes <= 0:
            raise ValueError("a snapshot's size cap must be positive")
        if not self.paths:
            raise ValueError("a snapshot policy names at least one path")
        for path in self.paths:
            if not _PATH.match(path) or path.count("/") < 2 or posixpath.normpath(path) != path:
                raise ValueError(f"snapshot path {path!r} is not a normalized absolute path below a top folder")
            if any(part in (".", "..") for part in path.split("/")):
                raise ValueError(f"snapshot path {path!r} has a relative segment")
        if len(set(self.paths)) != len(self.paths):
            raise ValueError("snapshot paths repeat")
        for a in self.paths:
            for b in self.paths:
                if a != b and _under(a, b):
                    raise ValueError(f"snapshot path {a!r} is inside {b!r}")

    def check_reserved(self, reserved: Iterable[str]) -> None:
        """``ValueError`` when a path is, holds or sits inside one of ``reserved`` (the workspace, engine state)."""
        for raw in reserved:
            root = posixpath.normpath(raw)
            for path in self.paths:
                if _under(path, root) or _under(root, path):
                    raise ValueError(f"snapshot path {path!r} overlaps {root!r}")

    @property
    def members(self) -> tuple[str, ...]:
        """The paths as tar member names (no leading ``/``)."""
        return tuple(p.lstrip("/") for p in self.paths)

    def admits(self, name: str) -> bool:
        return any(name == m or name.startswith(m + "/") for m in self.members)


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


@dataclass(frozen=True, slots=True)
class SnapshotEntry:
    name: str
    mode: int
    mtime: int
    # ``None`` for a folder.
    data: bytes | None


@dataclass(frozen=True, slots=True)
class SnapshotArchive:
    """A checked snapshot: regular files and folders under the policy's paths, in the order captured."""

    entries: tuple[SnapshotEntry, ...]
    # Links, devices and FIFOs the capture held: never kept (a link could point a restore elsewhere).
    skipped: int = 0

    @property
    def size(self) -> int:
        return sum(len(e.data) for e in self.entries if e.data is not None)

    @property
    def files(self) -> int:
        return sum(1 for e in self.entries if e.data is not None)

    def to_tar(self) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for entry in self.entries:
                info = tarfile.TarInfo(entry.name)
                info.mode, info.mtime = entry.mode, entry.mtime
                if entry.data is None:
                    # A folder's mtime moves whenever a file inside is written, the restore's own included.
                    info.type, info.mtime = tarfile.DIRTYPE, 0
                    archive.addfile(info)
                else:
                    info.size = len(entry.data)
                    archive.addfile(info, io.BytesIO(entry.data))
        return buffer.getvalue()


def read_archive(data: bytes, policy: SnapshotPolicy) -> SnapshotArchive:
    """The archive's files and folders, refused when a member escapes the policy, the files exceed its cap, or
    the archive has more members or expands further than the archive limits allow (SEC-FS-03)."""
    entries: list[SnapshotEntry] = []
    skipped = 0
    total = 0
    meter = ExpansionMeter(ExpansionLimits.from_env(policy.max_bytes), consumed=len(data))
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            for member in archive:
                meter.member()
                name = member.name.removeprefix("./").rstrip("/")
                if (
                    not name
                    or name.startswith("/")
                    or any(part in ("", ".", "..") for part in name.split("/"))
                    or not policy.admits(name)
                ):
                    raise SnapshotRefused("a snapshot member is outside its paths", reason=SNAPSHOT_UNSAFE)
                if member.isdir():
                    entries.append(SnapshotEntry(name, member.mode & 0o7777, int(member.mtime), None))
                    continue
                if not member.isreg():
                    skipped += 1
                    continue
                total += member.size
                if total > policy.max_bytes:
                    raise SnapshotRefused("the snapshot is over its size cap", reason=SNAPSHOT_TOO_LARGE)
                extracted = archive.extractfile(member)
                content = extracted.read() if extracted is not None else b""
                # A sparse member's holes read as zeros the archive never held: count what was read.
                meter.produce(len(content))
                entries.append(SnapshotEntry(name, member.mode & 0o7777, int(member.mtime), content))
    except ExpansionRefused as exc:
        raise SnapshotRefused(f"the snapshot is refused: {exc}", reason=SNAPSHOT_TOO_LARGE) from exc
    except tarfile.TarError as exc:
        raise SnapshotRefused("the snapshot is not a readable archive", reason=SNAPSHOT_FAILED) from exc
    return SnapshotArchive(tuple(entries), skipped)


def secret_kinds(archive: SnapshotArchive, secrets: Iterable[str] = ()) -> tuple[str, ...]:
    """The credential kinds a snapshot holds, in a member's name or bytes: the shared pattern list (every kind,
    the redact-only ones too) and ``RUN_SECRET`` for any of ``secrets`` (the run's tokens and placeholders)."""
    literals = [s.encode() for s in secrets if len(s) >= MIN_SECRET_LENGTH]
    found: set[str] = set()
    for entry in archive.entries:
        raw = entry.name.encode() + b"\n" + (entry.data or b"")
        # Each non-ASCII byte becomes U+FFFD, which is not a word character: a token right after a byte that
        # latin-1 would read as a letter still starts at a word boundary, so the patterns' ``\b`` holds.
        text = raw.decode("ascii", errors="replace")
        found.update(p.kind for p in VENDOR_TOKEN_PATTERNS if p.pattern.search(text))
        if any(literal in raw for literal in literals):
            found.add(RUN_SECRET)
    order = [p.kind for p in VENDOR_TOKEN_PATTERNS] + [RUN_SECRET]
    return tuple(kind for kind in order if kind in found)


def pack(scope: ObjectScope, archive: bytes) -> bytes:
    header = json.dumps({"tenant": scope.tenant_id, "partition": scope.partition}, sort_keys=True).encode()
    return _ENVELOPE_MAGIC + header + b"\n" + zlib.compress(archive, 6)


def unpack(scope: ObjectScope, payload: bytes) -> bytes:
    if not payload.startswith(_ENVELOPE_MAGIC):
        raise SnapshotUnavailable("not a sandbox snapshot", reason=SNAPSHOT_CORRUPT)
    header, sep, body = payload[len(_ENVELOPE_MAGIC) :].partition(b"\n")
    try:
        named = json.loads(header) if sep else None
    except ValueError:
        named = None
    if not isinstance(named, dict):
        raise SnapshotUnavailable("not a sandbox snapshot", reason=SNAPSHOT_CORRUPT)
    header_of = cast("dict[str, object]", named)
    if header_of.get("tenant") != scope.tenant_id or header_of.get("partition") != scope.partition:
        raise SnapshotUnavailable("the snapshot belongs to another session", reason=SNAPSHOT_FOREIGN)
    try:
        archive, inflater = inflate(body, ExpansionLimits.from_env(_MAX_BODY_BYTES))
    except zlib.error as exc:
        raise SnapshotUnavailable("the snapshot does not inflate", reason=SNAPSHOT_CORRUPT) from exc
    except ExpansionRefused as exc:
        raise SnapshotUnavailable(f"the snapshot is refused: {exc}", reason=SNAPSHOT_CORRUPT) from exc
    if inflater.unused_data or not inflater.eof:
        raise SnapshotUnavailable("the snapshot is truncated or too large", reason=SNAPSHOT_CORRUPT)
    return archive


class ObjectStoreSandboxSnapshots:
    """``SandboxSnapshotStore`` over a ``BinaryObjectStore`` (sealed with the session's key in PipesHub)."""

    def __init__(self, objects: BinaryObjectStore) -> None:
        self._objects = objects

    async def save(self, scope: ObjectScope, archive: bytes) -> ObjectRef:
        return await self._objects.put(scope, pack(scope, archive))

    async def load(self, scope: ObjectScope, ref: ObjectRef) -> bytes:
        try:
            payload = await self._objects.get(scope, ref)
        except (WorkspaceKeyDestroyed, FileNotFoundError, LookupError) as exc:
            raise SnapshotUnavailable("the snapshot is gone", reason=SNAPSHOT_MISSING) from exc
        except (ObjectUnreadable, ValueError) as exc:
            raise SnapshotUnavailable("the snapshot does not verify", reason=SNAPSHOT_CORRUPT) from exc
        return unpack(scope, payload)

    async def content_hash(self, scope: ObjectScope, archive: bytes) -> str:
        """The ref's ``content_hash`` a ``save`` of ``archive`` would carry, storing nothing."""
        return await self._objects.content_hash(scope, pack(scope, archive))


async def _drain(stream: AsyncIterator[bytes]) -> None:
    async for _ in stream:
        pass


async def _gone_or(sandbox: SandboxHandle, reason: str, message: str) -> SnapshotRefused:
    """A failed ``tar`` in a sandbox that no longer runs is a gone sandbox, never a snapshot (E1b F4)."""
    try:
        probe = await sandbox.exec(["sh", "-c", "exit 0"], env={})
        await asyncio.gather(_drain(probe.stdout), _drain(probe.stderr))
        if await probe.wait(timeout_s=CAPTURE_TIMEOUT_S) != 0:
            return SnapshotRefused("the sandbox stopped answering", reason=SNAPSHOT_SANDBOX_GONE)
    except SandboxNotFound:
        return SnapshotRefused("the sandbox is gone", reason=SNAPSHOT_SANDBOX_GONE)
    return SnapshotRefused(message, reason=reason)


async def capture(sandbox: SandboxHandle, policy: SnapshotPolicy) -> bytes | None:
    """The tar ``policy`` admits, read from inside ``sandbox``; ``None`` when none of its paths exist."""
    try:
        process = await sandbox.exec(["sh", "-c", CAPTURE_SCRIPT, CAPTURE_ARGV0, *policy.members], env={})
    except SandboxNotFound as exc:
        raise SnapshotRefused("the sandbox is gone", reason=SNAPSHOT_SANDBOX_GONE) from exc
    chunks: list[bytes] = []
    over = await _read_capped(process, chunks, policy.max_bytes)
    if over:
        with contextlib.suppress(Exception):
            await process.signal(signal.SIGKILL)
        with contextlib.suppress(Exception):
            _ = await process.wait(timeout_s=CAPTURE_TIMEOUT_S)
        raise SnapshotRefused("the snapshot is over its size cap", reason=SNAPSHOT_TOO_LARGE)
    try:
        code = await process.wait(timeout_s=CAPTURE_TIMEOUT_S)
    except SandboxNotFound as exc:
        raise SnapshotRefused("the sandbox is gone", reason=SNAPSHOT_SANDBOX_GONE) from exc
    if code != 0:
        raise await _gone_or(sandbox, SNAPSHOT_FAILED, f"the snapshot's tar exited {code}")
    data = b"".join(chunks)
    return data or None


async def _read_capped(process: ProcessHandle, chunks: list[bytes], max_bytes: int) -> bool:
    """Read stdout into ``chunks`` (draining stderr alongside); true once it passes ``max_bytes`` of tar."""
    stderr = asyncio.ensure_future(_drain(process.stderr))
    size = 0
    try:
        async for chunk in process.stdout:
            size += len(chunk)
            if size > max_bytes + _TAR_SLACK:
                return True
            chunks.append(chunk)
        return False
    finally:
        if not stderr.done():
            _ = stderr.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await stderr


# A tar of ``max_bytes`` of files also carries headers and padding; the file total is checked exactly on read.
_TAR_SLACK = 1024 * 1024


async def restore(sandbox: SandboxHandle, archive: SnapshotArchive) -> None:
    """Extract a checked archive at ``/`` inside ``sandbox`` (its members are under the policy's paths only)."""
    try:
        process = await sandbox.exec(list(RESTORE_ARGV), env={}, stdin=True)
    except SandboxNotFound as exc:
        raise SnapshotRefused("the sandbox is gone", reason=SNAPSHOT_SANDBOX_GONE) from exc
    payload = archive.to_tar()
    drained = asyncio.gather(_drain(process.stdout), _drain(process.stderr))
    try:
        for start in range(0, len(payload), 64 * 1024):
            await process.write(payload[start : start + 64 * 1024])
        await process.close_stdin()
    finally:
        await drained
    code = await process.wait(timeout_s=CAPTURE_TIMEOUT_S)
    if code != 0:
        raise await _gone_or(sandbox, SNAPSHOT_FAILED, f"the snapshot's restore exited {code}")


@dataclass
class SandboxSnapshots:
    """One turn's snapshots: restore the one it starts from, take one at each autosave point and at turn end.

    Nothing here fails a turn: a snapshot is an optimisation, so a refusal or an unreadable one is logged once
    at warning (reason code and counts only, never a path, a name or content) and the turn goes on.
    """

    store: SandboxSnapshotStore
    policy: SnapshotPolicy
    scope: ObjectScope
    # The values no snapshot may hold, read at each capture (a token may be renewed during the turn).
    secrets: Callable[[], tuple[str, ...]]
    log: HarnessLog
    # The snapshot the sandbox holds now (restored or last taken): an equal capture is not written again.
    last: ObjectRef | None = None

    async def restore_into(self, sandbox: SandboxHandle, ref: ObjectRef) -> bool:
        try:
            archive = read_archive(await self.store.load(self.scope, ref), self.policy)
            await restore(sandbox, archive)
        except (SnapshotUnavailable, SnapshotRefused) as unusable:
            self.log.warning("engine.snapshot.unusable", reasonCode=unusable.reason_code)
            return False
        self.last = ref
        self.log.info("engine.snapshot.restored", bytes=archive.size, files=archive.files)
        return True

    async def take(self, sandbox: SandboxHandle) -> ObjectRef | None:
        """The stored snapshot when one was written now; ``None`` when it equals the last one or was refused."""
        try:
            data = await capture(sandbox, self.policy)
            if data is None:
                return None
            archive = read_archive(data, self.policy)
            kinds = secret_kinds(archive, self.secrets())
            if kinds:
                self.log.warning("engine.snapshot.refused", reasonCode=SNAPSHOT_SECRET, kinds=list(kinds))
                return None
            tar = archive.to_tar()
            if self.last is not None and await self.store.content_hash(self.scope, tar) == self.last.content_hash:
                return None
            ref = await self.store.save(self.scope, tar)
        except SnapshotRefused as refused:
            self.log.warning("engine.snapshot.refused", reasonCode=refused.reason_code)
            return None
        self.last = ref
        self.log.info("engine.snapshot.saved", bytes=archive.size, files=archive.files, skipped=archive.skipped)
        return ref


__all__ = [
    "CAPTURE_ARGV0",
    "CAPTURE_SCRIPT",
    "DEFAULT_MAX_BYTES",
    "RESTORE_ARGV",
    "RUN_SECRET",
    "SNAPSHOT_CORRUPT",
    "SNAPSHOT_FAILED",
    "SNAPSHOT_FOREIGN",
    "SNAPSHOT_MISSING",
    "SNAPSHOT_SANDBOX_GONE",
    "SNAPSHOT_SECRET",
    "SNAPSHOT_TOO_LARGE",
    "SNAPSHOT_UNSAFE",
    "ObjectStoreSandboxSnapshots",
    "SandboxSnapshots",
    "SnapshotArchive",
    "SnapshotEntry",
    "SnapshotPolicy",
    "SnapshotReason",
    "capture",
    "pack",
    "read_archive",
    "restore",
    "secret_kinds",
    "unpack",
]
