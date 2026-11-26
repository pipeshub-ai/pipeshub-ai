"""``TarWorkspaceSync``: a revision into a sandbox folder and the folder's changes back, by content hash.

Both directions start with a scan of the folder (``get_tar``, hashed with the
object store's own ``content_hash``), so materialize writes only what differs
and collect uploads only bytes the workspace does not already hold. The same
reconciler serves every mount strategy (``strategies.py``): what differs is
whether the folder starts empty (scratch) or holds the session's last state
(persistent).
"""

from __future__ import annotations

import asyncio
import dataclasses
import mimetypes
import posixpath
import secrets
from collections import Counter
from typing import TYPE_CHECKING

from harness_core.clock import SystemClock
from harness_core.logs import HarnessLog
from harness_core.mime import EXTENSION_TO_MIME
from harness_core.sandbox.streams import collect
from harness_core.sandbox.workspace_fs import WorkspacePathRefused, move_into_workspace
from harness_core.workspace.changes import ChangeSet
from harness_core.workspace.sync.archive import (
    ArchiveReader,
    Owner,
    archive_base,
    write_tree,
)
from harness_core.workspace.sync.rules import (
    engine_config_root,
    is_engine_config,
    link_escapes,
    plan_tree,
    under_any,
)
from harness_core.workspace.sync.storage_classes import (
    MAX_CITING_BYTES,
    STORAGE_RECORD_PATH,
    StorageClassRegistry,
    decode_record,
    encode_record,
    is_storage_record,
    merged_references,
)
from harness_core.workspace.sync.types import (
    ENGINE_CONFIG,
    ON_FIRST_READ,
    REFERENCE_COPY,
    SCRATCH_FILE,
    STORAGE_RECORD,
    SYMLINK_ESCAPES,
    WORKSPACE_FULL,
    Collected,
    SandboxRemoveFailed,
    SyncLimits,
    SyncState,
    WorkspaceSyncRefused,
)
from harness_core.workspace.types import (
    INPUTS_DIR,
    SYMLINK_MODE,
    ObjectRef,
    WorkspaceFile,
)

# A staged file's name directly under the root, there only between ``put_tar`` and the move.
STAGING_PREFIX = ".pipeshub-staging-"

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence

    from harness_core.clock import Clock
    from harness_core.sandbox.ports import SandboxHandle
    from harness_core.workspace.ports import BinaryObjectStore
    from harness_core.workspace.sync.archive import ArchiveScan
    from harness_core.workspace.sync.storage_classes import Reference, StorageMap
    from harness_core.workspace.types import ObjectScope, WorkspaceManifest

LOGGER = "harness_core.workspace.sync"
LINK_MIME_TYPE = "inode/symlink"
_DEFAULT_MIME_TYPE = "application/octet-stream"
# Python's built-in table only (no system mime.types), so a guess is the same on every host.
_MIME_TYPES = mimetypes.MimeTypes()
_REMOVE_ARGV_BYTES = 64 * 1024
# A pending() entry stores nothing; it only has to count as a change.
_UNSTORED = "unstored"


def rm_rf(paths: Sequence[str]) -> list[str]:
    return ["rm", "-rf", "--", *paths]


def _guess_mime_type(path: str) -> str:
    known = EXTENSION_TO_MIME.get(posixpath.splitext(path)[1].lower())
    guessed, _ = (known, None) if known else _MIME_TYPES.guess_type(path, strict=True)
    return guessed or _DEFAULT_MIME_TYPE



def _is_input(entry: WorkspaceFile) -> bool:
    return entry.path.startswith(f"{INPUTS_DIR}/") and not entry.is_symlink

def _check_root(root: str) -> str:
    if not root.startswith("/") or posixpath.normpath(root) != root or root == "/":
        raise ValueError("the sync root must be a normalised absolute folder other than /")
    return root


class TarWorkspaceSync:
    """``WorkspaceSync`` over a broker's archive API; objects come from and go to one scope's store.

    ``objects`` is the store behind the workspace (a ``SealedObjectStore`` in
    production, so hashes and dedupe are per session). ``owner`` owns what is
    written, so the sandbox's user can edit and delete it. Deletions run
    ``remove_argv(paths)`` in the sandbox, because an archive cannot delete.
    ``storage`` says which of the folder's paths are the person's (ADR-RUN-01 D2):
    only those are collected; references and scratch stay in the sandbox.
    """

    def __init__(
        self,
        *,
        objects: BinaryObjectStore,
        scope: ObjectScope,
        limits: SyncLimits | None = None,
        owner: Owner | None = None,
        clock: Clock | None = None,
        remove_argv: Callable[[Sequence[str]], list[str]] = rm_rf,
        fetch_window: int = 8,
        log: HarnessLog | None = None,
        storage: StorageClassRegistry | None = None,
    ) -> None:
        self._objects = objects
        self._scope = scope
        self._limits = limits or SyncLimits()
        self._owner = owner or Owner()
        self._clock = clock or SystemClock()
        self._remove_argv = remove_argv
        self._window = fetch_window
        self._log = log or HarnessLog.named(LOGGER)
        self._storage = storage or StorageClassRegistry.default()

    async def materialize(
        self,
        sandbox: SandboxHandle,
        manifest: WorkspaceManifest,
        *,
        root: str,
        defer: Collection[str] = (),
        keep_scratch: bool = False,
    ) -> SyncState:
        root = _check_root(root)
        log = self._log.bind(sandboxId=sandbox.id, workspaceId=manifest.workspace_id, revision=manifest.revision)
        try:
            return await self._materialize(sandbox, manifest, root, log, frozenset(defer), keep_scratch=keep_scratch)
        except WorkspaceSyncRefused as exc:
            log.warning("workspace_sync.refused", reason=exc.reason_code, operation="materialize")
            raise

    async def stage(self, sandbox: SandboxHandle, state: SyncState, path: str) -> SyncState:
        """Writes ``path``, a file ``materialize`` deferred (``ON_FIRST_READ``), as the manifest holds it; the state
        no longer hides it, and since the sandbox now holds the same bytes, the next collect finds no change."""
        if state.hidden.get(path) != ON_FIRST_READ:
            raise KeyError(path)
        entry = state.manifest.files[path]

        async def fetch(item: WorkspaceFile) -> bytes:
            return await self._objects.get(self._scope, item.content)

        await self._place(sandbox, state.root, entry, fetch)
        self._log.info("workspace_sync.staged", sandboxId=sandbox.id, bytes=entry.content.size_bytes)
        hidden = {p: why for p, why in state.hidden.items() if p != path}
        return SyncState(state.sandbox_id, state.root, state.manifest, hidden, state.references, state.vouched)

    async def collect(self, sandbox: SandboxHandle, state: SyncState) -> Collected:
        return await self._collect_logged(sandbox, state, upload=True)

    async def pending(self, sandbox: SandboxHandle, state: SyncState) -> int:
        """How many paths a ``collect`` now would change; a full scan, nothing uploaded."""
        return len((await self._collect_logged(sandbox, state, upload=False)).changes.paths)

    async def _materialize(
        self,
        sandbox: SandboxHandle,
        manifest: WorkspaceManifest,
        root: str,
        log: HarnessLog,
        defer: frozenset[str],
        *,
        keep_scratch: bool,
    ) -> SyncState:
        targets = {
            path: await self._objects.get(self._scope, entry.content)
            for path, entry in manifest.files.items()
            if entry.is_symlink
        }
        recorded = await self._recorded(manifest)
        record = [path for path in manifest.files if is_storage_record(path)]
        shown = {path: entry for path, entry in manifest.files.items() if not is_storage_record(path)}
        plan = plan_tree(shown, {p: t.decode("utf-8", "surrogateescape") for p, t in targets.items() if p in shown})
        visible = [manifest.files[path] for path in sorted(plan.visible)]
        scan, hashes, storage = await self._observe(sandbox, root, manifest)
        writes = [entry for entry in visible if not _matches(scan, hashes, entry)]
        # A deferred file the sandbox already holds as is (staged earlier, a kept sandbox) stays; any other waits for
        # its first read, and a stale copy goes.
        held_back = {entry.path for entry in writes if entry.path in defer and not entry.is_symlink}
        writes = [entry for entry in writes if entry.path not in held_back]
        hidden = {**plan.hidden, **dict.fromkeys(held_back, ON_FIRST_READ), **dict.fromkeys(record, STORAGE_RECORD)}
        # References stay where the engine works (WP-82); scratch only within the run, on a refresh mid-turn.
        keep: frozenset[str] = storage.reference_folders | (storage.scratch if keep_scratch else frozenset[str]())
        removals = _removals(scan, plan.visible - held_back, [entry.path for entry in writes], keep=keep)
        if removals:
            await self._remove(sandbox, root, removals)

        async def fetch(entry: WorkspaceFile) -> bytes:
            link = targets.get(entry.path)
            return link if link is not None else await self._objects.get(self._scope, entry.content)

        # The person's inputs land by the move, as on a first read: in a kept sandbox the engine's processes may still
        # run and turn ``in`` into a link while the archive streams (round 14, R14-TS).
        inputs = [entry for entry in writes if _is_input(entry)]
        writes = [entry for entry in writes if not _is_input(entry)]
        if writes:
            mtime = int(self._clock.now().timestamp())
            await sandbox.put_tar(root, write_tree(writes, fetch, owner=self._owner, mtime=mtime, window=self._window))
        for entry in inputs:
            try:
                await self._place(sandbox, root, entry, fetch)
            except WorkspacePathRefused as refused:
                # Not placed: it waits for its first read, when ``ph inputs get`` says why if ``in`` is still a link.
                log.warning("workspace_sync.input_refused", reason=refused.reason_code)
                hidden[entry.path] = ON_FIRST_READ
        log.info(
            "workspace_sync.materialized",
            files=len(visible),
            written=len(writes) + len(inputs),
            removed=len(removals),
            hidden=len(plan.hidden),
            deferred=len(held_back),
            bytes=sum(entry.content.size_bytes for entry in [*writes, *inputs]),
            references=len(storage.references),
        )
        return SyncState(sandbox.id, root, manifest, hidden, recorded)

    async def _place(
        self,
        sandbox: SandboxHandle,
        root: str,
        entry: WorkspaceFile,
        fetch: Callable[[WorkspaceFile], Awaitable[bytes]],
    ) -> None:
        """Writes ``entry`` as the sandbox user would (SEC-FS-02): root's ``put_tar`` writes one file under a fresh,
        unguessable name directly under the root (no folder a running engine could see before the bytes and swap),
        then the sandbox user moves it into place. ``WorkspacePathRefused`` when its folder leads out of the root."""
        staged = f"{STAGING_PREFIX}{secrets.token_hex(16)}"
        mtime = int(self._clock.now().timestamp())
        await sandbox.put_tar(
            root, write_tree([dataclasses.replace(entry, path=staged)], fetch, owner=self._owner, mtime=mtime, window=1)
        )
        await move_into_workspace(sandbox, root, f"{root}/{staged}", f"{root}/{entry.path}")

    async def _recorded(self, manifest: WorkspaceManifest) -> tuple[Reference, ...]:
        """The references the revision records (``STORAGE_RECORD_PATH``)."""
        entry = manifest.files.get(STORAGE_RECORD_PATH)
        if entry is None or entry.is_symlink:
            return ()
        return decode_record(await self._objects.get(self._scope, entry.content))

    async def _observe(
        self, sandbox: SandboxHandle, root: str, manifest: WorkspaceManifest
    ) -> tuple[ArchiveScan, dict[str, str], StorageMap]:
        """The folder's scan, the hashes of its files, and its references and scratch (never hashed: they are kept
        as they are)."""
        hashes: dict[str, str] = {}
        async with ArchiveReader(await sandbox.get_tar(root), base=archive_base(root), limits=self._limits) as reader:
            linked, links = await self._linked(sandbox, reader)
            storage = self._storage.storage_map(
                reader.scan.files, [*await self._references(reader), *linked], durable=manifest.files, linked=links
            )
            paths = sorted(
                path
                for path in reader.scan.files
                if not under_any(path, storage.reference_folders) and path not in storage.scratch
            )
            async for found, data in reader.contents(paths):
                hashes[found.path] = await self._objects.content_hash(self._scope, data)
            return reader.scan, hashes, storage

    async def _references(self, reader: ArchiveReader) -> list[Reference]:
        """The references the producers' marks in the archive register (``ph git clone``'s, WP-82)."""
        files = {path: found.size for path, found in reader.scan.files.items() if found.mode != SYMLINK_MODE}
        marks = self._storage.marker_files(files)
        found: list[Reference] = []
        if marks:
            async for item, data in reader.contents(marks):
                reference = self._storage.reference_of(item.path, data)
                if reference is not None:
                    found.append(reference)
        return found

    async def _linked(self, sandbox: SandboxHandle, reader: ArchiveReader) -> tuple[list[Reference], frozenset[str]]:
        """The references the producers' links in the archive register (ADR-RUN-02: ``ph git clone``'s link to its
        checkout on the references mount), each read from its checkout in the sandbox; and every such link, read or
        not. The archive never holds a checkout, so none is streamed or counted."""
        claimed = self._storage.linked(reader.scan.links)
        found: list[Reference] = []
        for path, (rule, folder) in sorted(claimed.items()):
            data = await self._read_small(sandbox, f"{folder}/{rule.file}", rule.max_bytes)
            reference = rule.read(path, data) if data is not None else None
            if reference is not None:
                found.append(reference)
        return found, frozenset(claimed)

    async def _read_small(self, sandbox: SandboxHandle, path: str, max_bytes: int) -> bytes | None:
        """A small file in the sandbox, read as its user; ``None`` when it is missing, unreadable or larger."""
        try:
            process = await sandbox.exec(["head", "-c", str(max_bytes + 1), "--", path], env={})
            out, _, code = await asyncio.gather(collect(process.stdout), collect(process.stderr), process.wait())
        except Exception as exc:  # the checkout cannot be read: its link is kept, nothing is recorded
            self._log.warning("workspace_sync.reference_unread", errorType=type(exc).__name__)
            return None
        return out if code == 0 and len(out) <= max_bytes else None

    async def _citing(self, reader: ArchiveReader, paths: Collection[str]) -> list[str]:
        """The texts of the folder's deliverables, at most ``MAX_CITING_BYTES`` of them: a scratch file one of them
        names is the person's."""
        files = reader.scan.files
        chosen: list[str] = []
        total = 0
        for path in sorted(p for p in paths if self._storage.is_deliverable(p) and files[p].mode != SYMLINK_MODE):
            total += files[path].size
            if total > MAX_CITING_BYTES:
                break
            chosen.append(path)
        texts: list[str] = []
        if chosen:
            async for _, data in reader.contents(chosen):
                texts.append(data.decode("utf-8", "replace"))
        return texts

    async def _collect_logged(self, sandbox: SandboxHandle, state: SyncState, *, upload: bool) -> Collected:
        log = self._log.bind(sandboxId=sandbox.id, workspaceId=state.workspace_id, baseRevision=state.revision)
        try:
            collected = await self._collect(sandbox, state, upload=upload)
        except WorkspaceSyncRefused as exc:
            log.warning("workspace_sync.refused", reason=exc.reason_code, operation="collect")
            raise
        for reason, count in sorted(Counter(collected.skipped.values()).items()):
            log.warning("workspace_sync.skipped", reason=reason, count=count)
        log.info(
            "workspace_sync.collected" if upload else "workspace_sync.pending",
            upserts=len(collected.changes.upserts),
            deletes=len(collected.changes.deletes),
            uploaded=collected.uploaded,
            skipped=len(collected.skipped),
        )
        return collected

    async def _collect(self, sandbox: SandboxHandle, state: SyncState, *, upload: bool) -> Collected:
        base = state.manifest.files
        known: dict[tuple[str, int], ObjectRef] = {
            (entry.content.content_hash, entry.content.size_bytes): entry.content for entry in base.values()
        }
        upserts: dict[str, WorkspaceFile] = {}
        uploaded = 0
        reader = ArchiveReader(await sandbox.get_tar(state.root), base=archive_base(state.root), limits=self._limits)
        async with reader:
            scan = reader.scan
            skipped = dict(scan.skipped)
            skipped.update({path: ENGINE_CONFIG for path in scan.files if is_engine_config(path)})
            # The record is the harness's: a copy the engine wrote is never read.
            skipped.update({path: STORAGE_RECORD for path in scan.files if path not in skipped and is_storage_record(path)})
            storage = await self._storage_map(sandbox, reader, base, skipped)
            skipped.update(
                {
                    path: REFERENCE_COPY
                    for path in (*scan.files, *storage.linked)
                    if path not in skipped and under_any(path, storage.reference_folders)
                }
            )
            skipped.update(dict.fromkeys(sorted(storage.scratch - skipped.keys()), SCRATCH_FILE))
            skipped.update(
                {
                    path: SYMLINK_ESCAPES
                    for path, target in scan.links.items()
                    if path not in skipped and link_escapes(path, target, scan.links)
                }
            )
            present = frozenset(scan.files) | frozenset(skipped)
            deletes = frozenset(
                path for path in base if path not in present and path not in state.hidden and not is_storage_record(path)
            )
            references = merged_references(
                state.references, (ref for ref in storage.references if ref.identity in state.vouched)
            )
            record = encode_record(references) if references else None
            current = base.get(STORAGE_RECORD_PATH)
            record_hash = await self._objects.content_hash(self._scope, record) if record is not None else None
            if current is not None and record_hash == current.content.content_hash:
                record = None
            new_record = record is not None and current is None
            skipped.update(
                over_cap(scan.files.keys() - skipped.keys(), base, deletes, self._limits, extra=int(new_record))
            )
            async for found, data in reader.contents(sorted(p for p in scan.files if p not in skipped)):
                content_hash = await self._objects.content_hash(self._scope, data)
                current = base.get(found.path)
                key = (content_hash, len(data))
                if (
                    current is not None
                    and (current.content.content_hash, current.content.size_bytes) == key
                    and current.mode == found.mode
                ):
                    continue
                ref = known.get(key)
                if ref is None and upload:
                    ref = known[key] = await self._objects.put(self._scope, data)
                    uploaded += 1
                ref = ref or ObjectRef(_UNSTORED, content_hash, len(data))
                upserts[found.path] = WorkspaceFile(found.path, ref, _mime_type(found.path, found.mode, current), found.mode)
        if record is not None and record_hash is not None:
            stored = await self._objects.put(self._scope, record) if upload else None
            uploaded += int(stored is not None)
            upserts[STORAGE_RECORD_PATH] = WorkspaceFile(
                STORAGE_RECORD_PATH, stored or ObjectRef(_UNSTORED, record_hash, len(record)), "application/json"
            )
        return Collected(ChangeSet(upserts, deletes), skipped, uploaded, references)

    async def _storage_map(
        self,
        sandbox: SandboxHandle,
        reader: ArchiveReader,
        base: Mapping[str, WorkspaceFile],
        skipped: Mapping[str, str],
    ) -> StorageMap:
        """The folder's references and scratch; the deliverables are read only when some file would be scratch."""
        candidates = [path for path in reader.scan.files if path not in skipped]
        linked, links = await self._linked(sandbox, reader)
        found = [*await self._references(reader), *linked]
        storage = self._storage.storage_map(candidates, found, durable=base, linked=links)
        if not storage.scratch:
            return storage
        citing = await self._citing(reader, candidates)
        return self._storage.storage_map(candidates, found, durable=base, citing=citing, linked=links)

    async def _remove(self, sandbox: SandboxHandle, root: str, removals: Sequence[str]) -> None:
        batch: list[str] = []
        size = 0
        for rel in removals:
            path = f"{root}/{rel}"
            if batch and size + len(path) > _REMOVE_ARGV_BYTES:
                await self._remove_batch(sandbox, batch)
                batch, size = [], 0
            batch.append(path)
            size += len(path) + 1
        if batch:
            await self._remove_batch(sandbox, batch)

    async def _remove_batch(self, sandbox: SandboxHandle, paths: Sequence[str]) -> None:
        process = await sandbox.exec(self._remove_argv(paths), env={})
        _, _, code = await asyncio.gather(collect(process.stdout), collect(process.stderr), process.wait())
        if code != 0:
            raise SandboxRemoveFailed(f"removing {len(paths)} path(s) from the sandbox exited {code}")


def _matches(scan: ArchiveScan, hashes: dict[str, str], entry: WorkspaceFile) -> bool:
    found = scan.files.get(entry.path)
    return (
        found is not None
        and found.mode == entry.mode
        and found.size == entry.content.size_bytes
        and hashes.get(entry.path) == entry.content.content_hash
    )


def _mime_type(path: str, mode: int, current: WorkspaceFile | None) -> str:
    if mode == SYMLINK_MODE:
        return LINK_MIME_TYPE
    if current is not None and not current.is_symlink:
        return current.mime_type
    return _guess_mime_type(path)


def _removals(
    scan: ArchiveScan, visible: frozenset[str], writes: Sequence[str], *, keep: frozenset[str] = frozenset()
) -> list[str]:
    """Raw names to remove before writing, outermost first.

    Whatever is not a visible path goes, as do folders a file replaces and
    special files a file replaces. Engine configuration goes as a whole folder,
    so nothing of it survives in a persistent mount, empty folders included.
    A reference folder or scratch file in ``keep`` stays as the engine left it
    (WP-82, ADR-RUN-01 D2), but for engine configuration inside it.
    """
    removals: set[str] = set()
    for raw in scan.present:
        if is_engine_config(raw):
            removals.add(engine_config_root(raw))
        elif raw not in visible and not under_any(raw, keep):
            removals.add(raw)
    removals.update(engine_config_root(folder) for folder in scan.folders if is_engine_config(folder))
    removals.update(path for path in writes if path in scan.folders or path in scan.skipped)
    return sorted(path for path in removals if not _has_ancestor_in(path, removals))


def over_cap(
    candidates: Collection[str],
    base: Mapping[str, WorkspaceFile],
    deletes: Collection[str],
    limits: SyncLimits,
    *,
    extra: int = 0,
) -> dict[str, str]:
    """The new files past the store's file cap, each ``WORKSPACE_FULL``: a revision never holds more than
    ``limits.max_workspace_files`` (Node refuses such a commit whole). The files nearest the root are kept, so the
    outputs folder's deliverables and the engine's notes go in before a deep tree such as ``node_modules``. ``extra``:
    files the change set adds besides the candidates (the references record)."""
    new = [path for path in candidates if path not in base]
    room = limits.max_workspace_files - (len(base) - len(deletes)) - extra
    if len(new) <= room:
        return {}
    new.sort(key=lambda path: (path.count("/"), path))
    return dict.fromkeys(new[max(0, room) :], WORKSPACE_FULL)


def _has_ancestor_in(path: str, paths: set[str]) -> bool:
    parts = path.split("/")
    return any("/".join(parts[:i]) in paths for i in range(1, len(parts)))


__all__ = ["LINK_MIME_TYPE", "LOGGER", "TarWorkspaceSync", "over_cap", "rm_rf"]
