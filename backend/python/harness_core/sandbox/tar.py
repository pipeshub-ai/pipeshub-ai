"""Tar streams for ``SandboxHandle.put_tar`` / ``get_tar``."""

from __future__ import annotations

import io
import posixpath
import tarfile
from typing import TYPE_CHECKING

from harness_core.archive_limits import (
    EXPANDED_TOO_LARGE,
    ExpansionLimits,
    ExpansionMeter,
    ExpansionRefused,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Mapping

    from harness_core.sandbox.ports import SandboxHandle
    from harness_core.workspace.sync.archive import Owner

DIR_MODE = 0o755
# What an engine obeys: read-only files, in folders the engine shares with root (see ``config_tar``).
CONFIG_MODE = 0o444
# A program the folder holds (WP-70: the session's own ph), runnable and still read-only to the sandbox user.
EXEC_MODE = 0o555
SHARED_DIR_MODE = 0o1777
# The most ``read_tar_stream`` reads of a sandbox's folder (an engine's transcripts, a version file).
MAX_READ_BYTES = 1024**3


async def _one(payload: bytes) -> AsyncIterator[bytes]:
    yield payload


def tar_stream(files: Mapping[str, bytes], *, owner: Owner | None = None) -> AsyncIterator[bytes]:
    """A tar of relative paths → content (leading ``/`` stripped), in path order.

    With ``owner``, every entry and its parent folders (listed first) belong to it, so a process running
    as that user can write beside the files (an extractor creates missing folders as itself, root)."""
    buffer = io.BytesIO()
    names = sorted((name.lstrip("/"), content) for name, content in files.items())
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        if owner is not None:
            parents = {"/".join(parts[:i]) for parts in (n.split("/") for n, _ in names) for i in range(1, len(parts))}
            for folder in sorted(parents, key=lambda f: (f.count("/"), f)):
                info = tarfile.TarInfo(name=folder)
                info.type, info.mode, info.uid, info.gid = tarfile.DIRTYPE, DIR_MODE, owner.uid, owner.gid
                archive.addfile(info)
        for name, content in names:
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            if owner is not None:
                info.uid, info.gid = owner.uid, owner.gid
            archive.addfile(info, io.BytesIO(content))
    return _one(buffer.getvalue())


def config_tar(
    files: Mapping[str, bytes],
    *,
    homes: Iterable[str] = (),
    links: Mapping[str, str] | None = None,
    own_parents: bool = False,
    dirs: Iterable[str] = (),
    executables: Iterable[str] = (),
) -> AsyncIterator[bytes]:
    """A tar of configuration the sandbox user may read and never change (relative paths, ``/`` stripped).

    Files and links belong to root, files read-only. Each of ``homes`` is a folder the engine also writes its
    own state in: root's, world-writable with the sticky bit, so the user adds entries but cannot remove,
    rename or replace root's (as in ``/tmp``). Each of ``dirs`` is root's folder the user may enter and list.
    ``own_parents``: every other folder above a file, link or home is root's too (0755), even one the user made
    earlier (restored state), so none can be renamed away; an agent may also refuse a config whose folder another
    user can write (Gemini CLI's system settings). Each of ``executables`` (a path in ``files``) is runnable (0555)."""
    buffer = io.BytesIO()
    shared = {h.strip("/") for h in homes}
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        folders = {d.strip("/") for d in dirs}
        if own_parents:
            named = [*shared, *(n.lstrip("/") for n in files), *(n.lstrip("/") for n in links or {})]
            folders |= {"/".join(parts[:i]) for parts in (n.split("/") for n in named) for i in range(1, len(parts))}
        for folder in sorted(folders - shared, key=lambda f: (f.count("/"), f)):
            info = tarfile.TarInfo(name=folder)
            info.type, info.mode = tarfile.DIRTYPE, DIR_MODE
            archive.addfile(info)
        for home in sorted(shared, key=lambda h: (h.count("/"), h)):
            info = tarfile.TarInfo(name=home)
            info.type, info.mode = tarfile.DIRTYPE, SHARED_DIR_MODE
            archive.addfile(info)
        for name, target in sorted((name.lstrip("/"), target) for name, target in (links or {}).items()):
            info = tarfile.TarInfo(name=name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            archive.addfile(info)
        runnable = {e.lstrip("/") for e in executables}
        for name, content in sorted((name.lstrip("/"), content) for name, content in files.items()):
            info = tarfile.TarInfo(name=name)
            info.size, info.mode = len(content), EXEC_MODE if name in runnable else CONFIG_MODE
            archive.addfile(info, io.BytesIO(content))
    return _one(buffer.getvalue())


def _by_mount(paths: Iterable[str]) -> dict[str, list[str]]:
    by_root: dict[str, list[str]] = {}
    for path in paths:
        parts = posixpath.normpath(path).split("/")
        if not path.startswith("/") or len(parts) < 3 or ".." in path.split("/"):
            raise ValueError(f"not a path under a top-level directory: {path}")
        by_root.setdefault("/" + parts[1], []).append("/".join(parts[2:]))
    return by_root


async def put_config(
    sandbox: SandboxHandle,
    files: Mapping[str, bytes],
    *,
    homes: Iterable[str] = (),
    links: Mapping[str, str] | None = None,
    own_parents: bool = False,
) -> None:
    """Write what an engine obeys so the sandbox user cannot change it (``config_tar``), one ``put_tar`` per mount.

    Call it after anything the user owns under ``homes``: a folder entry in a later archive takes that
    archive's owner and mode. The mount's own root must not be the user's either (the broker makes scratch
    mounts root's with the sticky bit), or the user could rename a home away and make a new one."""
    links = dict(links or {})
    homes = tuple(homes)
    roots = _by_mount([*files, *homes, *links])
    for root in sorted(roots):
        prefix = root + "/"
        await sandbox.put_tar(
            root,
            config_tar(
                {p.removeprefix(prefix): c for p, c in files.items() if p.startswith(prefix)},
                homes=[h.removeprefix(prefix) for h in homes if h.startswith(prefix)],
                links={p.removeprefix(prefix): t for p, t in links.items() if p.startswith(prefix)},
                own_parents=own_parents,
            ),
        )


async def read_tar_stream(stream: AsyncIterator[bytes], *, limits: ExpansionLimits | None = None) -> dict[str, bytes]:
    """A sandbox's folder as path → content. The archive is the sandbox's to shape, so it is read as plain tar (a
    compressed one is refused, not inflated) and bounded in bytes, members and expansion (SEC-FS-03)."""
    bound = limits or ExpansionLimits.from_env(MAX_READ_BYTES)
    parts: list[bytes] = []
    size = 0
    async for chunk in stream:
        size += len(chunk)
        if size > bound.max_bytes:
            raise ExpansionRefused(f"the sandbox archive is over {bound.max_bytes} bytes", reason=EXPANDED_TOO_LARGE)
        parts.append(chunk)
    meter = ExpansionMeter(bound, consumed=size)
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(b"".join(parts)), mode="r:") as archive:
        for member in archive:
            meter.member()
            extracted = archive.extractfile(member) if member.isfile() else None
            if extracted is not None:
                content = extracted.read()
                meter.produce(len(content))
                out[member.name] = content
    return out


async def put_files(sandbox: SandboxHandle, files: Mapping[str, bytes], *, owner: Owner | None = None) -> None:
    """Write absolute paths → content, one ``put_tar`` per top-level directory.

    A sandbox's root is read-only and ``put_tar`` writes only into an existing
    writable mount, so each path's first segment must be a mount (``/workspace``,
    ``/state``); deeper directories are created by the archive.
    """
    by_root: dict[str, dict[str, bytes]] = {}
    for path, content in files.items():
        parts = posixpath.normpath(path).split("/")
        if not path.startswith("/") or len(parts) < 3 or ".." in parts:
            raise ValueError(f"not a file under a top-level directory: {path}")
        by_root.setdefault("/" + parts[1], {})["/".join(parts[2:])] = content
    for root, members in sorted(by_root.items()):
        await sandbox.put_tar(root, tar_stream(members, owner=owner))


__all__ = ["CONFIG_MODE", "EXEC_MODE", "SHARED_DIR_MODE", "config_tar", "put_config", "put_files", "read_tar_stream", "tar_stream"]
