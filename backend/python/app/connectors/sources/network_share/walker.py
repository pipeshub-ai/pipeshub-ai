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
from app.models.entities import FileRecord
from app.models.permission import Permission

if TYPE_CHECKING:
    from logging import Logger

    from app.config.constants.arangodb import Connectors
    from app.connectors.sources.network_share.protocol import INetworkShareDataSource

ExistingLookup = Callable[[str], Awaitable[FileRecord | None]]
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
        seen_file_ids: set[int] = set()
        chosen_folders = {p.rstrip("/") for p in scope.list_prefixes if p}

        async def flush() -> None:
            nonlocal upserts, moves
            if upserts:
                await self.flush_upserts(upserts)
                upserts = []
            if moves:
                await self.flush_moves(moves)
                moves = []

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
            seen.add(ext_id)
            if entry.last_write_time is not None:
                ts = int(entry.last_write_time.timestamp() * 1000)
                max_ts = max(max_ts, ts)

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
                moves.append((decision.old_external_id, decision.record, perms))
                if len(moves) >= self.batch_size:
                    await flush()
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
                    if child_dir is not None and entry.is_directory and not entry.is_symlink:
                        await traverse(child_dir)
                except Exception:
                    complete = False
                    self.logger.exception(
                        "Error processing %s in %s/%s",
                        entry.name,
                        share,
                        directory_path,
                    )

        for prefix in scope.list_prefixes:
            await traverse(prefix.rstrip("/"), prefix=bool(prefix))

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
