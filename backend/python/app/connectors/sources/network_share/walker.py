"""Recursive share walk. Depends on INetworkShareDataSource, never on smbclient/pysmb."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.connectors.core.registry.filters import FilterCollection, IndexingFilterKey
from app.connectors.core.registry.folder_scope import FolderScope
from app.connectors.sources.network_share.entry import DirectoryEntry
from app.connectors.sources.network_share.errors import DirectoryListingError
from app.connectors.sources.network_share.filters import (
    date_bounds,
    passes_date_filters,
    passes_extension_filter,
)
from app.connectors.sources.network_share.pathing import (
    identity_rel_path,
    join_rel_path,
)
from app.connectors.sources.network_share.record_mapper import (
    MoveDecision,
    RecordMapper,
    SkipDecision,
    UpsertDecision,
    revision_id,
    usable_file_id,
)
from app.models.entities import FileRecord, Record
from app.models.permission import Permission

if TYPE_CHECKING:
    from datetime import datetime
    from logging import Logger

    from app.config.constants.arangodb import Connectors
    from app.connectors.sources.network_share.protocol import INetworkShareDataSource

ExistingLookup = Callable[[str], Awaitable[Record | None]]
FlushUpserts = Callable[[list[tuple[FileRecord, list[Permission]]]], Awaitable[None]]
FlushMoves = Callable[[list[tuple[str, FileRecord, list[Permission]]]], Awaitable[None]]
PermissionsFor = Callable[[FileRecord], list[Permission]]


@dataclass
class WalkResult:
    seen: set[str]
    complete: bool
    max_timestamp_ms: int = 0


@dataclass
class ShareWalker:
    data_source: INetworkShareDataSource
    mapper: RecordMapper
    logger: Logger
    connector_name: Connectors
    connector_id: str
    batch_size: int
    get_by_external_id: ExistingLookup
    get_by_revision: ExistingLookup
    flush_upserts: FlushUpserts
    flush_moves: FlushMoves
    permissions_for: PermissionsFor

    async def walk_share(
        self,
        share: str,
        sync_filters: FilterCollection,
        indexing_filters: FilterCollection,
    ) -> WalkResult:
        scope = FolderScope.from_filters(sync_filters)
        modified_after, modified_before, created_after, created_before = date_bounds(
            sync_filters
        )
        indexing_manual = not indexing_filters.is_enabled(
            IndexingFilterKey.FILES, default=True
        )
        seen: set[str] = {share}
        complete = True
        max_ts = 0
        upserts: list[tuple[FileRecord, list[Permission]]] = []
        moves: list[tuple[str, FileRecord, list[Permission]]] = []
        pending_moves: list[tuple[MoveDecision, DirectoryEntry, str, list[Permission]]] = []
        seen_file_ids: set[int] = set()
        walked_dirs: dict[int, tuple[str, datetime | None, datetime | None]] = {}
        chosen_folders = {p.rstrip("/") for p in scope.list_prefixes if p}

        async def flush() -> None:
            nonlocal upserts, moves
            if upserts:
                await self.flush_upserts(upserts)
                upserts = []
            if moves:
                await self.flush_moves(moves)
                moves = []

        async def second_path_of(
            entry: DirectoryEntry,
            ext_id: str,
            existing_by_id: Record | None,
            existing_by_revision: Record | None,
        ) -> str | None:
            # Samba follows a directory symlink on the server and lists it as a
            # plain folder with the target's file id. A folder has one path, so a
            # second path with that id is a link. The timestamps guard against two
            # filesystems under one share reusing an inode number.
            first = walked_dirs.get(entry.file_id)
            if first is not None:
                path, created, written = first
                if (created, written) == (entry.created_time, entry.last_write_time):
                    return path
                return None
            if existing_by_id is not None or existing_by_revision is None:
                return None
            known_id = existing_by_revision.external_record_id
            if not known_id or known_id == ext_id or not known_id.startswith(f"{share}/"):
                return None
            # A renamed folder has the same id at a new path too; its old path is gone.
            at_known = await self.data_source.stat(share, known_id[len(share) + 1 :])
            if at_known is not None and at_known.is_directory and at_known.file_id == entry.file_id:
                return known_id
            return None

        async def handle_entry(entry: DirectoryEntry, parent_dir: str) -> str | None:
            nonlocal max_ts
            if self.mapper.skip_reason(entry):
                return None
            server_path = join_rel_path(parent_dir, entry.name)
            if server_path is None or not server_path:
                return None
            nfc_path = identity_rel_path(server_path)
            in_scope = (
                scope.includes_folder(nfc_path)
                if entry.is_directory
                else scope.includes_file(nfc_path)
            )
            if not in_scope:
                return None
            if not passes_extension_filter(
                nfc_path, is_directory=entry.is_directory, sync_filters=sync_filters
            ):
                return None
            if not passes_date_filters(
                entry,
                modified_after,
                modified_before,
                created_after,
                created_before,
            ):
                return None

            ext_id = f"{share}/{nfc_path}"
            existing_by_id = await self.get_by_external_id(ext_id)
            existing_by_revision = None
            skip_revision_lookup = (
                existing_by_id is not None
                or not usable_file_id(entry.file_id)
                or entry.file_id in seen_file_ids
            )
            if not skip_revision_lookup:
                existing_by_revision = await self.get_by_revision(
                    revision_id(share, entry, nfc_path)
                )

            if entry.is_directory and usable_file_id(entry.file_id):
                same_as = await second_path_of(entry, ext_id, existing_by_id, existing_by_revision)
                if same_as is not None:
                    self.logger.warning(
                        "Not walking %s: it is the same folder as %s (a link on the server)",
                        ext_id,
                        same_as,
                    )
                    return None
                walked_dirs[entry.file_id] = (ext_id, entry.created_time, entry.last_write_time)

            seen.add(ext_id)
            if entry.last_write_time is not None:
                ts = int(entry.last_write_time.timestamp() * 1000)
                max_ts = max(max_ts, ts)

            decision = self.mapper.classify(
                entry=entry,
                share=share,
                parent_dir=parent_dir,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                existing_by_id=existing_by_id,
                existing_by_revision=existing_by_revision,
                seen_file_ids=seen_file_ids,
                indexing_manual=indexing_manual,
            )
            if usable_file_id(entry.file_id) and entry.file_id is not None:
                seen_file_ids.add(entry.file_id)

            if isinstance(decision, SkipDecision):
                return None
            perms = self.permissions_for(decision.record)
            if isinstance(decision, MoveDecision):
                pending_moves.append((decision, entry, parent_dir, perms))
            elif isinstance(decision, UpsertDecision):
                upserts.append((decision.record, perms))
                if len(upserts) >= self.batch_size:
                    await flush()
            return server_path if entry.is_directory else None

        async def traverse(directory_path: str, *, prefix: bool = False) -> None:
            nonlocal complete
            try:
                entries = await self.data_source.list_directory(share, directory_path)
            except FileNotFoundError:
                if directory_path in chosen_folders:
                    self.logger.warning(
                        "Folder %s/%s named in the folder filter does not exist",
                        share,
                        directory_path,
                    )
                    return
                complete = False
                self.logger.error(
                    "Failed to list %s/%s: not found", share, directory_path
                )
                return
            except (DirectoryListingError, OSError, ConnectionError) as exc:
                complete = False
                self.logger.error(
                    "Failed to list %s/%s: %s", share, directory_path, exc
                )
                return

            if prefix and directory_path:
                await self._materialize_ancestors(
                    share, directory_path, seen, handle_entry
                )

            for entry in entries:
                try:
                    child_dir = await handle_entry(entry, directory_path)
                    if (
                        child_dir is not None
                        and entry.is_directory
                        and not entry.is_symlink
                        and not entry.is_reparse
                    ):
                        await traverse(child_dir)
                except Exception:
                    complete = False
                    self.logger.exception(
                        "Error processing %s in %s/%s",
                        entry.name,
                        share,
                        directory_path,
                    )

        async def selected_prefix_is_walkable(directory_path: str) -> bool:
            # Listing starts at the chosen path, so a reparse prefix is invisible
            # to the child check. A failed stat must not prune.
            nonlocal complete
            parts = [part for part in directory_path.split("/") if part]
            for index in range(len(parts)):
                path = "/".join(parts[: index + 1])
                try:
                    info = await self.data_source.stat(share, path, follow=False)
                except Exception as exc:
                    complete = False
                    self.logger.error(
                        "Failed to stat %s/%s before walking a selected folder: %s",
                        share,
                        path,
                        exc,
                    )
                    return False
                if info is None:
                    return True
                if info.is_symlink or info.is_reparse:
                    self.logger.warning(
                        "Not walking %s/%s: the path is a reparse point",
                        share,
                        path,
                    )
                    return False
            return True

        for prefix in scope.list_prefixes:
            directory = prefix.rstrip("/")
            if directory and not await selected_prefix_is_walkable(directory):
                continue
            await traverse(directory, prefix=bool(prefix))

        # Settled only after the whole walk: the revision lookup returns one record
        # per file id, so for a hard link walked before its other path it hands
        # back the record of a link that is still on the share.
        for decision, entry, parent_dir, perms in pending_moves:
            if decision.old_external_id in seen:
                fallback = self.mapper.classify(
                    entry=entry,
                    share=share,
                    parent_dir=parent_dir,
                    connector_name=self.connector_name,
                    connector_id=self.connector_id,
                    existing_by_id=None,
                    existing_by_revision=None,
                    seen_file_ids=(),
                    indexing_manual=indexing_manual,
                )
                if isinstance(fallback, UpsertDecision):
                    upserts.append((fallback.record, perms))
            else:
                moves.append((decision.old_external_id, decision.record, perms))
            if len(upserts) >= self.batch_size or len(moves) >= self.batch_size:
                await flush()

        await flush()
        return WalkResult(seen=seen, complete=complete, max_timestamp_ms=max_ts)

    async def _materialize_ancestors(
        self,
        share: str,
        directory_path: str,
        seen: set[str],
        handle_entry: Callable[..., Awaitable[str | None]],
    ) -> None:
        parts = [p for p in directory_path.split("/") if p]
        for i, name in enumerate(parts):
            parent = "/".join(parts[:i])
            ancestor_path = "/".join(parts[: i + 1])
            seen.add(f"{share}/{ancestor_path}")
            synthetic = DirectoryEntry(
                name=name,
                is_directory=True,
                is_symlink=False,
                size=0,
                created_time=None,
                last_write_time=None,
                file_id=None,
            )
            await handle_entry(synthetic, parent)
