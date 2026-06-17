"""
Repository synchronisation for the GitLab connector.

Responsibilities:
- ``_sync_repo_main``: dispatch between full and incremental sync, manage checkpoints.
- ``_sync_repo_full``: GraphQL-paginated full sync of folders and code blobs.
- ``_sync_repo_incremental``: compare-commits-based incremental sync.
- Blob SHA reconciliation (promote delete+add pairs to renames).
- In-place code file rename handling.
- Folder record creation and cleanup.
- Code file content streaming (``_fetch_code_file_content``).
- Timestamp backfill background task.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

from app.config.constants.arangodb import (
    CollectionNames,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.constants import (
    IconPaths,
)
from app.models.entities import CodeFileRecord, FileRecord, Record, RecordGroupType, RecordType

from .constants import (
    GITLAB_COMPARE_DIFF_LIMIT,
    PREVIEW_RENDERABLE_EXTENSIONS,
)
from .models import GitlabLiterals, RecordUpdate

if TYPE_CHECKING:
    from app.connectors.sources.gitlab.connector import GitLabConnector


class ReposSync:
    """Handles repository (code-file) synchronisation for ``GitLabConnector``."""

    def __init__(self, connector: "GitLabConnector") -> None:
        self.c = connector
        self.logger = connector.logger

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(
        self, project_id: int, project_path: str, default_branch: str
    ) -> None:
        """Sync code repository using incremental compare when a checkpoint exists,
        falling back to a full sync when the compare fails."""
        c = self.c
        branch_res = await c.runtime.ds_call(
            c.data_source.get_branch, project_id=project_id, branch=default_branch,
        )
        if not branch_res.success or not branch_res.data:
            self.logger.error(
                "Failed to fetch branch %s for project %s (%s): %s",
                default_branch, project_id, project_path, branch_res.error,
            )
            return

        current_sha = _branch_head_commit_sha(branch_res.data)
        if not current_sha:
            self.logger.error(
                "No commit SHA on branch %s for project %s (%s)",
                default_branch, project_id, project_path,
            )
            return

        last_sha = await self._get_code_repo_checkpoint(project_id)
        if last_sha is None:
            self.logger.info("No code-repo checkpoint for project %s; running full sync", project_id)
            full_ok = await self._sync_repo_full(project_id, project_path)
            if full_ok:
                await self._update_code_repo_checkpoint(project_id, current_sha)
            else:
                self.logger.warning(
                    "Full sync for project %s completed with errors; "
                    "checkpoint not advanced so the next run will retry",
                    project_id,
                )
            return

        if last_sha == current_sha:
            self.logger.info(
                "Code repo unchanged for project %s (HEAD %s); skipping",
                project_id, current_sha[:8],
            )
            return

        incremental_ok = await self._sync_repo_incremental(project_id, project_path, last_sha, current_sha)
        if incremental_ok:
            await self._update_code_repo_checkpoint(project_id, current_sha)
            return

        self.logger.warning("Incremental code sync failed for project %s; falling back to full sync", project_id)
        full_ok = await self._sync_repo_full(project_id, project_path)
        if full_ok:
            await self._update_code_repo_checkpoint(project_id, current_sha)
        else:
            self.logger.warning(
                "Full sync fallback for project %s completed with errors; "
                "checkpoint not advanced so the next run will retry",
                project_id,
            )

    # ------------------------------------------------------------------
    # Checkpoints
    # ------------------------------------------------------------------

    async def _get_code_repo_checkpoint(self, project_id: int) -> str | None:
        """Return the last synced HEAD commit SHA for a project's code repository."""
        from app.config.constants.arangodb import Connectors
        from app.connectors.core.base.sync_point.sync_point import generate_record_sync_point_key
        try:
            group_project_id = f"{project_id}-code-repository"
            key = generate_record_sync_point_key(Connectors.GITLAB.value, group_project_id, "")
            data = await self.c.record_sync_point.read_sync_point(key)
            if not data:
                return None
            sha = data.get(GitlabLiterals.LAST_COMMIT_SHA.value)
            return str(sha) if sha else None
        except Exception:
            return None

    async def _update_code_repo_checkpoint(self, project_id: int, commit_sha: str) -> None:
        """Persist the HEAD commit SHA after a successful code repository sync."""
        from app.config.constants.arangodb import Connectors
        from app.connectors.core.base.sync_point.sync_point import generate_record_sync_point_key
        key = generate_record_sync_point_key(
            Connectors.GITLAB.value, f"{project_id}-code-repository", ""
        )
        await self.c.record_sync_point.update_sync_point(
            key, {GitlabLiterals.LAST_COMMIT_SHA.value: commit_sha}
        )

    # ------------------------------------------------------------------
    # Full sync
    # ------------------------------------------------------------------

    async def _sync_repo_full(self, project_id: int, project_path: str) -> bool:
        """Full sync of default-branch folders and blobs via paginated GraphQL.

        Returns ``True`` when sync completed without API errors, ``False`` on
        recoverable error (caller must not advance checkpoint).
        """
        c = self.c
        # Phase 1: collect all folder (tree) nodes
        tree_list: list[dict[str, Any]] = []
        after_cursor = ""
        while True:
            try:
                tree_res = await c.runtime.ds_call_async(
                    c.data_source.get_repo_tree_g, project_id=project_path, ref="HEAD", after_cursor=after_cursor,
                )
            except Exception as e:
                self.logger.error("Error fetching tree for project %s: %s", project_id, e)
                return False
            if not tree_res.data:
                self.logger.info("No tree found for project %s", project_id)
                return True
            data: dict[str, Any] = json.loads(tree_res.data)
            project = (data.get("data") or {}).get("project") or {}
            repository = project.get("repository") or {}
            paginated_tree = repository.get("paginatedTree") or {}
            if not paginated_tree:
                self.logger.info(
                    "No repository tree for project %s (empty repo, missing scope, or archived); "
                    "skipping code sync", project_id,
                )
                return True
            project_nodes = paginated_tree.get("nodes") or []
            page_info = paginated_tree.get("pageInfo") or {}
            if not project_nodes:
                if tree_list:
                    break
                self.logger.info("No project nodes found for project %s", project_id)
                return True
            t_nodes: dict[str, Any] = project_nodes[0]
            file_path_nodes: list[dict[str, Any]] = (t_nodes.get("trees") or {}).get("nodes") or []
            tree_list.extend(file_path_nodes)
            continue_paging, after_cursor = _should_continue_repo_tree_pagination(
                len(file_path_nodes), len(tree_list), page_info
            )
            if not continue_paging:
                break

        # Phase 2: persist folder records top-down (parents before children)
        external_group_id = f"{project_id}-code-repository"
        level_wise_files: dict[int, list[dict[str, Any]]] = {}
        for item in tree_list:
            if item.get("type") != "tree":
                continue
            level_wise_files.setdefault((item.get("path") or "").count("/"), []).append(item)

        for _level, files in sorted(level_wise_files.items()):
            list_records_new: list[RecordUpdate] = []
            for file in files:
                file_path = file.get("path") or ""
                file_name = file.get("name")
                file_hash = file.get("sha")
                external_record_id = file.get("webPath")
                weburl = file.get("webUrl")
                if not external_record_id or not file_name:
                    self.logger.warning("Skipping tree %s: missing webPath/name", file_path)
                    continue
                parent_external_record_id = (
                    external_record_id.rpartition("/")[0] if "/" in file_path else None
                )
                tree_record = FileRecord(
                    id=str(uuid.uuid4()),
                    org_id=c.data_entities_processor.org_id,
                    record_name=str(file_name),
                    record_type=RecordType.FILE.value,
                    connector_name=c.connector_name,
                    connector_id=c.connector_id,
                    external_record_id=external_record_id,
                    version=0,
                    origin=OriginTypes.CONNECTOR.value,
                    record_group_type=RecordGroupType.PROJECT.value,
                    external_record_group_id=external_group_id,
                    mime_type=MimeTypes.FOLDER.value,
                    external_revision_id=str(file_hash),
                    preview_renderable=False,
                    parent_external_record_id=parent_external_record_id,
                    parent_record_type=(RecordType.FILE if parent_external_record_id else None),
                    is_file=False,
                    inherit_permissions=True,
                    weburl=weburl,
                )
                list_records_new.append(RecordUpdate(
                    record=tree_record, is_new=True, is_updated=False, is_deleted=False,
                    metadata_changed=False, content_changed=False, permissions_changed=False,
                    external_record_id=str(external_record_id), new_permissions=[], old_permissions=[],
                ))
            if list_records_new:
                await self._process_records(list_records_new)

        # Phase 3: depth-1 pipelining — fetch blob page N+1 while processing page N
        after_cursor = ""
        blobs_processed = 0
        pending: asyncio.Task[tuple[str, list[dict[str, Any]], dict[str, Any]]] | None = (
            asyncio.create_task(self._fetch_blob_page(project_path, project_id, after_cursor))
        )
        try:
            while pending is not None:
                kind, file_path_nodes, page_info = await pending
                pending = None
                if kind == "abort":
                    return False
                if kind == "no_nodes":
                    if blobs_processed == 0:
                        self.logger.info("No project nodes found for project %s", project_id)
                    break
                continue_paging, after_cursor = _should_continue_repo_tree_pagination(
                    len(file_path_nodes), blobs_processed + len(file_path_nodes), page_info,
                )
                if continue_paging:
                    pending = asyncio.create_task(
                        self._fetch_blob_page(project_path, project_id, after_cursor)
                    )
                if file_path_nodes:
                    await self.build_code_file_records(file_path_nodes, project_id, project_path)
                    blobs_processed += len(file_path_nodes)
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                try:
                    await pending
                except (asyncio.CancelledError, Exception):
                    pass

        return True

    async def _fetch_blob_page(
        self, project_path: str, project_id: int, after_cursor: str
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        """Fetch a single ``paginatedTree`` blob page. Returns ``(kind, nodes, page_info)``."""
        c = self.c
        empty: list[dict[str, Any]] = []
        try:
            tree_res = await c.runtime.ds_call_async(
                c.data_source.get_file_tree_g, project_id=project_path, ref="HEAD", after_cursor=after_cursor,
            )
        except Exception as e:
            self.logger.error("Error fetching file tree for project %s: %s", project_id, e)
            return "abort", empty, {}
        if not tree_res.success or not tree_res.data:
            self.logger.error("Error fetching file tree for project %s: %s", project_id, tree_res.error)
            return "abort", empty, {}
        try:
            data: dict[str, Any] = json.loads(tree_res.data)
        except json.JSONDecodeError as e:
            self.logger.error("Failed to parse file tree JSON for project %s: %s", project_id, e)
            return "abort", empty, {}
        if "errors" in data:
            self.logger.error("GraphQL errors for project %s: %s", project_id, json.dumps(data["errors"]))
            return "abort", empty, {}
        project = (data.get("data") or {}).get("project") or {}
        repository = project.get("repository") or {}
        paginated_tree = repository.get("paginatedTree") or {}
        if not paginated_tree:
            self.logger.info(
                "No repository tree for project %s (empty repo, missing scope, or archived)", project_id
            )
            return "abort", empty, {}
        project_nodes = paginated_tree.get("nodes") or []
        page_info = paginated_tree.get("pageInfo") or {}
        if not project_nodes:
            return "no_nodes", empty, page_info
        t_nodes: dict[str, Any] = project_nodes[0]
        file_path_nodes: list[dict[str, Any]] = (t_nodes.get("blobs") or {}).get("nodes") or []
        return "data", file_path_nodes, page_info

    # ------------------------------------------------------------------
    # Incremental sync
    # ------------------------------------------------------------------

    async def _sync_repo_incremental(
        self, project_id: int, project_path: str, from_sha: str, to_sha: str
    ) -> bool:
        """Incremental sync using compare-commits; returns False on failure."""
        c = self.c
        compare_res = await c.runtime.ds_call(
            c.data_source.compare_commits,
            project_id=project_id, from_sha=from_sha, to_sha=to_sha, straight=True,
        )
        if not compare_res.success or compare_res.data is None:
            self.logger.warning("compare_commits failed for project %s: %s", project_id, compare_res.error)
            return False

        compare_data = compare_res.data
        if isinstance(compare_data, dict):
            diffs = compare_data.get("diffs") or []
            overflow = compare_data.get("overflow")
        else:
            diffs = getattr(compare_data, "diffs", None) or []
            overflow = getattr(compare_data, "overflow", None)

        if overflow:
            self.logger.warning(
                "compare_commits returned overflow=true for project %s (%s diffs); falling back to full sync",
                project_id, len(diffs),
            )
            return False
        if len(diffs) >= GITLAB_COMPARE_DIFF_LIMIT:
            self.logger.warning("Too many diffs (%s) for project %s; falling back to full sync", len(diffs), project_id)
            return False

        deletes, adds, modifies, renames = _classify_compare_diffs(diffs)
        self.logger.info(
            "Incremental code sync for project %s: %s deletes, %s adds, %s modifies, %s renames (before SHA reconcile)",
            project_id, len(deletes), len(adds), len(modifies), len(renames),
        )

        deletes, adds, extra_renames = await self._reconcile_sha_moves(project_id, project_path, deletes, adds, ref=to_sha)
        renames = renames + extra_renames
        if extra_renames:
            self.logger.info("SHA reconcile promoted %s delete+add pair(s) to rename for project %s", len(extra_renames), project_id)

        self.logger.info(
            "Incremental code sync for project %s (final): %s deletes, %s adds, %s modifies, %s renames",
            project_id, len(deletes), len(adds), len(modifies), len(renames),
        )

        all_ok = True
        if deletes:
            await self._delete_code_files_by_paths(project_id, project_path, deletes)
        if renames:
            rename_ok = await self._apply_code_renames(project_id, project_path, renames, ref=to_sha)
            all_ok = all_ok and rename_ok
        upsert_paths = list(dict.fromkeys(adds + modifies))
        if upsert_paths:
            upsert_ok = await self._upsert_code_files_by_paths(project_id, project_path, upsert_paths, ref=to_sha)
            all_ok = all_ok and upsert_ok

        removed_paths = deletes + [old_p for old_p, _ in renames]
        if removed_paths:
            await self._cleanup_emptied_folders(project_id, project_path, removed_paths)

        if not all_ok:
            self.logger.warning(
                "Incremental sync for project %s completed with partial failures; checkpoint not advanced",
                project_id,
            )
        return all_ok

    # ------------------------------------------------------------------
    # SHA reconciliation
    # ------------------------------------------------------------------

    async def _reconcile_sha_moves(
        self, project_id: int, project_path: str, deletes: list[str], adds: list[str], ref: str = "HEAD",
    ) -> tuple[list[str], list[str], list[tuple[str, str]]]:
        """Promote delete+add pairs sharing the same blob SHA into renames."""
        if not deletes or not adds:
            return deletes, adds, []
        added_sha_map, _ = await self._resolve_blob_sha_by_path(project_id, adds, ref=ref)
        sha_to_new_path: dict[str, str] = {}
        for new_path, sha in added_sha_map.items():
            sha_to_new_path.setdefault(sha, new_path)
        extra_renames: list[tuple[str, str]] = []
        promoted_deletes: set[str] = set()
        promoted_adds: set[str] = set()
        for old_path in deletes:
            old_external_id = _code_blob_web_path(project_path, old_path)
            existing = await self.c.data_entities_processor.get_record_by_external_id(self.c.connector_id, old_external_id)
            if existing is None:
                continue
            stored_sha = getattr(existing, "external_revision_id", None) or ""
            if not stored_sha:
                continue
            new_path = sha_to_new_path.get(stored_sha)
            if new_path is None or new_path in promoted_adds:
                continue
            extra_renames.append((old_path, new_path))
            promoted_deletes.add(old_path)
            promoted_adds.add(new_path)
        return (
            [p for p in deletes if p not in promoted_deletes],
            [p for p in adds if p not in promoted_adds],
            extra_renames,
        )

    async def _resolve_blob_sha_by_path(
        self, project_id: int, repo_paths: list[str], ref: str = "HEAD",
    ) -> tuple[dict[str, str], bool]:
        """Fetch blob SHAs for a list of repo-relative paths via list_repo_tree."""
        by_parent: dict[str | None, list[str]] = {}
        for repo_path in repo_paths:
            parent = repo_path.rpartition("/")[0] if "/" in repo_path else None
            by_parent.setdefault(parent, []).append(repo_path)
        sha_map: dict[str, str] = {}
        all_ok = True
        for parent, child_paths in by_parent.items():
            tree_res = await self.c.runtime.paged_list(
                self.c.data_source.list_repo_tree,
                project_id=project_id, ref=ref, path=parent, recursive=False,
                progress_label=f"resolve-sha tree {parent or '/'} project {project_id}",
            )
            if not tree_res.success or not tree_res.data:
                all_ok = False
                continue
            for entry in tree_res.data:
                entry_path = entry.get("path") if isinstance(entry, dict) else getattr(entry, "path", None)
                entry_type = entry.get("type") if isinstance(entry, dict) else getattr(entry, "type", None)
                blob_sha = entry.get("id") if isinstance(entry, dict) else getattr(entry, "id", None)
                if entry_path and entry_type == "blob" and blob_sha and str(entry_path) in child_paths:
                    sha_map[str(entry_path)] = str(blob_sha)
        return sha_map, all_ok

    # ------------------------------------------------------------------
    # Rename / upsert / delete
    # ------------------------------------------------------------------

    async def _apply_code_renames(
        self, project_id: int, project_path: str, renames: list[tuple[str, str]], ref: str = "HEAD",
    ) -> bool:
        """Apply in-place rename for a list of ``(old_path, new_path)`` pairs."""
        if not renames:
            return True
        c = self.c
        new_paths = [new_p for _, new_p in renames]
        await self._ensure_folder_records_for_paths(project_id, project_path, new_paths, ref=ref)

        all_ok = True
        by_parent: dict[str | None, list[str]] = {}
        for repo_path in new_paths:
            parent = repo_path.rpartition("/")[0] if "/" in repo_path else None
            by_parent.setdefault(parent, []).append(repo_path)

        new_sha_map: dict[str, str] = {}
        name_map: dict[str, str] = {}
        for parent, child_paths in by_parent.items():
            tree_res = await c.runtime.paged_list(
                c.data_source.list_repo_tree, project_id=project_id, ref=ref, path=parent, recursive=False,
                progress_label=f"rename tree {parent or '/'} project {project_id}",
            )
            if not tree_res.success or not tree_res.data:
                all_ok = False
                continue
            for entry in tree_res.data:
                entry_path = entry.get("path") if isinstance(entry, dict) else getattr(entry, "path", None)
                entry_type = entry.get("type") if isinstance(entry, dict) else getattr(entry, "type", None)
                entry_name = entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", None)
                blob_sha_e = entry.get("id") if isinstance(entry, dict) else getattr(entry, "id", None)
                if entry_path and entry_type == "blob" and str(entry_path) in child_paths:
                    if blob_sha_e:
                        new_sha_map[str(entry_path)] = str(blob_sha_e)
                    if entry_name:
                        name_map[str(entry_path)] = str(entry_name)

        external_group_id = f"{project_id}-code-repository"
        code_files_enabled = self._code_files_indexing_enabled()
        from app.utils.time_conversion import get_epoch_timestamp_in_ms
        current_timestamp = get_epoch_timestamp_in_ms()

        moves: list[tuple[str, Any, list[Any]]] = []
        for old_path, new_path in renames:
            blob_sha = new_sha_map.get(new_path)
            file_name = name_map.get(new_path)
            if not file_name:
                self.logger.warning("Could not resolve name for rename target %r in project %s; falling back to delete+add", new_path, project_id)
                await self._delete_code_files_by_paths(project_id, project_path, [old_path])
                await self._upsert_code_files_by_paths(project_id, project_path, [new_path], ref=ref)
                continue
            if _should_skip_dotfile_repo_path(new_path):
                await self._delete_code_files_by_paths(project_id, project_path, [old_path])
                continue
            file_extension = file_name.split(".")[-1]
            file_mime = getattr(MimeTypes, file_extension.upper(), MimeTypes.PLAIN_TEXT).value
            preview_renderable = file_extension.lower() in PREVIEW_RENDERABLE_EXTENSIONS
            web_path = _code_blob_web_path(project_path, new_path)
            weburl = f"{c._gitlab_base_url}{web_path}"
            parent_dir = new_path.rpartition("/")[0] if "/" in new_path else None
            parent_external_record_id = _code_tree_web_path(project_path, parent_dir) if parent_dir else None
            new_record = CodeFileRecord(
                id=str(uuid.uuid4()), org_id=c.data_entities_processor.org_id, record_name=file_name,
                record_type=RecordType.CODE_FILE.value, connector_name=c.connector_name, connector_id=c.connector_id,
                external_record_id=web_path, version=0, origin=OriginTypes.CONNECTOR.value,
                record_group_type=RecordGroupType.PROJECT.value, external_record_group_id=external_group_id,
                mime_type=file_mime, external_revision_id=str(blob_sha) if blob_sha else "",
                preview_renderable=preview_renderable, file_path=new_path, file_hash=blob_sha,
                inherit_permissions=True, parent_external_record_id=parent_external_record_id,
                parent_record_type=(RecordType.FILE if parent_external_record_id else None),
                weburl=weburl, source_created_at=current_timestamp, source_updated_at=current_timestamp,
            )
            if not code_files_enabled:
                new_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
            old_external_id = _code_blob_web_path(project_path, old_path)
            moves.append((old_external_id, new_record, []))

        if moves:
            await c.data_entities_processor.on_records_moved(moves)

        return all_ok

    async def _upsert_code_files_by_paths(
        self, project_id: int, project_path: str, paths: list[str], ref: str = "HEAD"
    ) -> bool:
        """Upsert code file records for given repo-relative paths."""
        c = self.c
        unique_paths = list(dict.fromkeys(paths))
        if not unique_paths:
            return True
        await self._ensure_folder_records_for_paths(project_id, project_path, unique_paths, ref=ref)
        all_ok = True
        nodes: list[dict[str, Any]] = []
        by_parent: dict[str | None, list[str]] = {}
        for repo_path in unique_paths:
            parent = repo_path.rpartition("/")[0] if "/" in repo_path else None
            by_parent.setdefault(parent, []).append(repo_path)

        for parent, child_paths in by_parent.items():
            tree_res = await c.runtime.paged_list(
                c.data_source.list_repo_tree, project_id=project_id, ref=ref, path=parent, recursive=False,
                progress_label=f"upsert tree {parent or '/'} project {project_id}",
            )
            if not tree_res.success or not tree_res.data:
                self.logger.warning("list_repo_tree failed for project %s path %r: %s", project_id, parent, tree_res.error)
                all_ok = False
                continue
            entries_by_path: dict[str, Any] = {
                str(e.get("path") if isinstance(e, dict) else getattr(e, "path", None)): e
                for e in tree_res.data
                if (e.get("path") if isinstance(e, dict) else getattr(e, "path", None))
            }
            for repo_path in child_paths:
                entry = entries_by_path.get(repo_path)
                if entry is None:
                    self.logger.warning("Blob %r not found under parent %r for project %s", repo_path, parent, project_id)
                    all_ok = False
                    continue
                entry_type = entry.get("type") if isinstance(entry, dict) else getattr(entry, "type", None)
                if entry_type != "blob":
                    continue
                blob_sha = entry.get("id") if isinstance(entry, dict) else getattr(entry, "id", None)
                name = entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", None)
                if not blob_sha or not name:
                    continue
                web_path = _code_blob_web_path(project_path, repo_path)
                nodes.append({"path": repo_path, "name": name, "sha": str(blob_sha), "type": "blob",
                               "webPath": web_path, "webUrl": f"{c._gitlab_base_url}{web_path}"})

        if nodes:
            await self.build_code_file_records(nodes, project_id, project_path)
        return all_ok

    async def _delete_code_files_by_paths(
        self, project_id: int, project_path: str, paths: list[str]
    ) -> None:
        """Delete code file records by repo-relative paths."""
        c = self.c
        for repo_path in paths:
            external_id = _code_blob_web_path(project_path, repo_path)
            record = await c.data_entities_processor.get_record_by_external_id(c.connector_id, external_id)
            if record:
                await c.data_entities_processor.on_record_deleted(record.id)

    # ------------------------------------------------------------------
    # Folder management
    # ------------------------------------------------------------------

    async def _ensure_folder_records_for_paths(
        self, project_id: int, project_path: str, file_paths: list[str], ref: str = "HEAD"
    ) -> None:
        """Create missing folder records for parent directories of changed files."""
        c = self.c
        prefixes: set[str] = set()
        for repo_path in file_paths:
            parts = repo_path.split("/")
            for i in range(1, len(parts)):
                prefixes.add("/".join(parts[:i]))
        if not prefixes:
            return
        external_group_id = f"{project_id}-code-repository"
        record_updates: list[RecordUpdate] = []

        for prefix in sorted(prefixes, key=lambda p: p.count("/")):
            tree_external_id = _code_tree_web_path(project_path, prefix)
            existing = await c.data_entities_processor.get_record_by_external_id(c.connector_id, tree_external_id)
            if existing:
                continue
            parent_prefix = prefix.rpartition("/")[0] if "/" in prefix else None
            tree_res = await c.runtime.paged_list(
                c.data_source.list_repo_tree, project_id=project_id, ref=ref, path=parent_prefix, recursive=False,
                progress_label=f"folder tree {parent_prefix or '/'} project {project_id}",
            )
            if not tree_res.success or not tree_res.data:
                self.logger.warning("Could not list repo tree for folder %r in project %s", prefix, project_id)
                continue
            folder_entry = None
            for entry in tree_res.data:
                entry_path = entry.get("path") if isinstance(entry, dict) else getattr(entry, "path", None)
                entry_type = entry.get("type") if isinstance(entry, dict) else getattr(entry, "type", None)
                if entry_path == prefix and entry_type == "tree":
                    folder_entry = entry
                    break
            if folder_entry is None:
                continue
            folder_name = folder_entry.get("name") if isinstance(folder_entry, dict) else getattr(folder_entry, "name", None)
            folder_hash = folder_entry.get("id") if isinstance(folder_entry, dict) else getattr(folder_entry, "id", None)
            if not folder_name:
                continue
            web_path = _code_tree_web_path(project_path, prefix)
            weburl = f"{c._gitlab_base_url}{web_path}"
            parent_external_record_id = _code_tree_web_path(project_path, parent_prefix) if parent_prefix else None
            tree_record = FileRecord(
                id=str(uuid.uuid4()), org_id=c.data_entities_processor.org_id, record_name=str(folder_name),
                record_type=RecordType.FILE.value, connector_name=c.connector_name, connector_id=c.connector_id,
                external_record_id=web_path, version=0, origin=OriginTypes.CONNECTOR.value,
                record_group_type=RecordGroupType.PROJECT.value, external_record_group_id=external_group_id,
                mime_type=MimeTypes.FOLDER.value, external_revision_id=str(folder_hash) if folder_hash else "",
                preview_renderable=False, parent_external_record_id=parent_external_record_id,
                parent_record_type=(RecordType.FILE if parent_external_record_id else None),
                is_file=False, inherit_permissions=True, weburl=weburl,
            )
            record_updates.append(RecordUpdate(
                record=tree_record, is_new=True, is_updated=False, is_deleted=False,
                metadata_changed=False, content_changed=False, permissions_changed=False,
                external_record_id=web_path, new_permissions=[], old_permissions=[],
            ))

        if record_updates:
            await self._process_records(record_updates)

    async def _cleanup_emptied_folders(
        self, project_id: int, project_path: str, removed_paths: list[str]
    ) -> None:
        """Delete folder records that became empty after file deletes/renames."""
        c = self.c
        candidate_dirs: set[str] = set()
        for repo_path in removed_paths:
            parts = repo_path.split("/")
            for i in range(1, len(parts)):
                candidate_dirs.add("/".join(parts[:i]))
        if not candidate_dirs:
            return
        sorted_dirs = sorted(candidate_dirs, key=lambda p: p.count("/"), reverse=True)
        deleted_count = 0
        for dir_prefix in sorted_dirs:
            tree_external_id = _code_tree_web_path(project_path, dir_prefix)
            folder = await c.data_entities_processor.get_record_by_external_id(c.connector_id, tree_external_id)
            if folder is None:
                continue
            children = await c.data_entities_processor.get_records_by_parent(c.connector_id, tree_external_id)
            if children:
                self.logger.debug("Folder %r still has %d child(ren) in project %s; keeping", dir_prefix, len(children), project_id)
                continue
            self.logger.info("Deleting emptied folder record %r (id=%s) for project %s", dir_prefix, folder.id, project_id)
            await c.data_entities_processor.on_folder_deleted(folder.id)
            deleted_count += 1
        if deleted_count:
            self.logger.info("Folder cleanup removed %d emptied folder record(s) for project %s", deleted_count, project_id)

    # ------------------------------------------------------------------
    # Code file record building
    # ------------------------------------------------------------------

    async def build_code_file_records(
        self, code_file_list: list[dict[str, Any]], project_id: int, project_path: str
    ) -> None:
        """Build and persist code file records from a blob list."""
        c = self.c
        list_records_new: list[RecordUpdate] = []
        files_skipped = 0
        external_group_id = f"{project_id}-code-repository"
        code_files_enabled = self._code_files_indexing_enabled()

        for file in code_file_list:
            file_path = file.get("path") or ""
            file_name = file.get("name")
            file_hash = file.get("sha")
            external_record_id = file.get("webPath")
            weburl = file.get("webUrl")
            if not external_record_id or not file_name:
                files_skipped += 1
                self.logger.warning("Skipping blob %s: missing webPath/name", file_path)
                continue
            if file_name.startswith("."):
                files_skipped += 1
                continue
            file_extension = file_name.split(".")[-1]
            file_mime = getattr(MimeTypes, file_extension.upper(), MimeTypes.PLAIN_TEXT).value
            preview_renderable = file_extension.lower() in PREVIEW_RENDERABLE_EXTENSIONS
            if "/" in file_path:
                parent_blob_path = external_record_id.rpartition("/")[0]
                parent_external_record_id = parent_blob_path.replace("/-/blob/", "/-/tree/", 1)
            else:
                parent_external_record_id = None
            code_file_record = CodeFileRecord(
                id=str(uuid.uuid4()), org_id=c.data_entities_processor.org_id, record_name=str(file_name),
                record_type=RecordType.CODE_FILE.value, connector_name=c.connector_name, connector_id=c.connector_id,
                external_record_id=external_record_id, version=0, origin=OriginTypes.CONNECTOR.value,
                record_group_type=RecordGroupType.PROJECT.value, external_record_group_id=external_group_id,
                mime_type=file_mime, external_revision_id=str(file_hash), preview_renderable=preview_renderable,
                file_path=file_path, file_hash=file_hash, inherit_permissions=True,
                parent_external_record_id=parent_external_record_id,
                parent_record_type=(RecordType.FILE if parent_external_record_id else None),
                weburl=weburl, source_created_at=None, source_updated_at=None,
            )
            if not code_files_enabled:
                code_file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
            list_records_new.append(RecordUpdate(
                record=code_file_record, is_new=True, is_updated=False, is_deleted=False,
                metadata_changed=False, content_changed=False, permissions_changed=False,
                external_record_id=external_record_id, new_permissions=[], old_permissions=[],
            ))

        if list_records_new:
            await self._process_records(list_records_new)
            if files_skipped:
                self.logger.info("Processed %s code file records; %s skipped (dotfiles/missing metadata)", len(list_records_new), files_skipped)

    # ------------------------------------------------------------------
    # Content streaming
    # ------------------------------------------------------------------

    async def _fetch_code_file_content(
        self, record: CodeFileRecord
    ) -> AsyncGenerator[bytes, None]:
        """Stream code file content from GitLab."""
        c = self.c
        try:
            file_path = (
                _repo_path_from_blob_web_url(record.weburl)
                or record.file_path
            )
            if not file_path:
                async with c.data_store_provider.transaction() as tx_store:
                    file_path = await tx_store.get_record_path(record.id)
            if not file_path:
                raise ValueError(
                    f"Cannot resolve repo path for record {record.id}: "
                    f"weburl={record.weburl!r}, file_path={record.file_path!r}"
                )
            external_group_id = getattr(record, "external_record_group_id", None)
            if not external_group_id:
                raise ValueError("Project id not found.")
            project_id = external_group_id.split("-")[0]
            file_res = await c.runtime.ds_call(c.data_source.get_file_content, project_id=project_id, file_path=file_path)
            if not file_res.success:
                raise Exception(f"Error fetching file content for project {project_id} path {file_path}: {file_res.error}")
            file_data = file_res.data
            if not file_data:
                raise Exception(f"No file content returned by GitLab for project {project_id} path {file_path}")
            content_b64 = getattr(file_data, "content", None)
            if content_b64 is None:
                yield b""
                return
            decoded_bytes = await asyncio.to_thread(base64.b64decode, content_b64)
            yield decoded_bytes
        except Exception as e:
            raise Exception(f"Error fetching code content for record {record.id}: {e}") from e

    # ------------------------------------------------------------------
    # Timestamp backfill
    # ------------------------------------------------------------------

    async def cancel_timestamp_backfill(self) -> None:
        """Stop the in-flight backfill task before starting a new sync."""
        task = self.c._code_file_timestamp_backfill_task
        self.c._code_file_timestamp_backfill_task = None
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def schedule_timestamp_backfill(self) -> None:
        """Schedule the post-sync commit-history timestamp backfill."""
        c = self.c
        existing = c._code_file_timestamp_backfill_task
        if existing is not None and not existing.done():
            return
        c._code_file_timestamp_backfill_task = asyncio.create_task(
            self._backfill_code_file_timestamps_after_sync(),
            name=f"gitlab_code_file_ts_backfill_{c.connector_id}",
        )

    async def _backfill_code_file_timestamps_after_sync(self) -> None:
        """Background task: backfill commit-history timestamps for all synced projects."""
        c = self.c
        try:
            await c.runtime.refresh_token_if_needed()
            projects = await c.projects._resolve_projects_with_filters()
            for project in projects:
                await self._run_code_file_timestamp_backfill(project.id)
        except Exception as e:
            self.logger.error(
                "Code file timestamp backfill failed for connector %s: %s",
                c.connector_id, e, exc_info=True,
            )
        finally:
            c._code_file_timestamp_backfill_task = None

    async def _run_code_file_timestamp_backfill(self, project_id: int) -> None:
        """Backfill timestamps for code files with null created/updated timestamps."""
        c = self.c
        batch_size = 100
        try:
            await c.runtime.refresh_token_if_needed()
            external_group_id = f"{project_id}-code-repository"
            async with c.data_store_provider.transaction() as tx_store:
                nodes = await tx_store.get_nodes_by_filters(
                    collection=CollectionNames.RECORDS.value,
                    filters={
                        "connectorId": c.connector_id, "recordType": RecordType.CODE_FILE.value,
                        "externalGroupId": external_group_id,
                        "sourceCreatedAtTimestamp": None, "sourceLastModifiedTimestamp": None,
                    },
                )
            path_to_records: dict[str, list[CodeFileRecord]] = {}
            for node in nodes:
                if node.get("isDeleted"):
                    continue
                code_file = CodeFileRecord.from_arango_record({}, node)
                file_path = code_file.file_path or _repo_path_from_blob_web_url(code_file.weburl)
                if not file_path:
                    continue
                path_to_records.setdefault(file_path, []).append(code_file)

            file_paths = list(path_to_records.keys())
            for offset in range(0, len(file_paths), batch_size):
                await c.runtime.refresh_token_if_needed()
                batch_paths = file_paths[offset:offset + batch_size]
                try:
                    timestamp_by_path = await self._fetch_code_file_timestamps_batch(project_id, batch_paths)
                except Exception as e:
                    self.logger.warning("Failed to fetch timestamps for project %s: %s", project_id, e)
                    continue
                for file_path in batch_paths:
                    created_ms, updated_ms = timestamp_by_path.get(file_path, (None, None))
                    if created_ms is None and updated_ms is None:
                        continue
                    for record in path_to_records[file_path]:
                        if record.source_created_at == created_ms and record.source_updated_at == updated_ms:
                            continue
                        try:
                            updated_record = record.model_copy(
                                update={"source_created_at": created_ms, "source_updated_at": updated_ms}
                            )
                            await c.data_entities_processor.on_record_metadata_update(updated_record)
                        except Exception as e:
                            self.logger.warning(
                                "Failed to update timestamps for record %s path %s: %s",
                                record.id, file_path, e,
                            )
        except Exception as e:
            self.logger.error(
                "Code file timestamp backfill failed for project %s: %s", project_id, e, exc_info=True,
            )

    async def _code_file_source_timestamps(self, project_id: int, file_path: str, ref: str = "HEAD") -> tuple[int | None, int | None]:
        """Fetch created/updated timestamps from GitLab commit history for one path."""
        c = self.c
        if not file_path:
            return (None, None)
        res = await c.runtime.ds_call(c.data_source.list_commits_for_path, project_id=project_id, path=file_path, ref_name=ref)
        if not res.success or not res.data:
            return (None, None)
        data = res.data
        created_ms = _gitlab_timestamp_to_ms(data.get("oldest_committed_date"))
        updated_ms = _gitlab_timestamp_to_ms(data.get("newest_committed_date"))
        return (created_ms, updated_ms)

    async def _fetch_code_file_timestamps_batch(self, project_id: int, paths: list[str]) -> dict[str, tuple[int | None, int | None]]:
        """Fetch commit-history timestamps for many paths (bounded concurrency)."""
        semaphore = asyncio.Semaphore(10)
        results: dict[str, tuple[int | None, int | None]] = {}

        async def fetch_one(path: str) -> None:
            async with semaphore:
                try:
                    results[path] = await self._code_file_source_timestamps(project_id, path)
                except Exception as e:
                    self.logger.warning("Failed to fetch timestamps for %s in project %s: %s", path, project_id, e)
                    results[path] = (None, None)

        await asyncio.gather(*(fetch_one(path) for path in paths))
        return results

    # ------------------------------------------------------------------
    # Indexing flag
    # ------------------------------------------------------------------

    def _code_files_indexing_enabled(self) -> bool:
        c = self.c
        if not c.indexing_filters:
            return True
        from app.connectors.core.registry.filters import IndexingFilterKey
        return c.indexing_filters.is_enabled(IndexingFilterKey.CODE_FILES)

    # ------------------------------------------------------------------
    # Record persistence helper
    # ------------------------------------------------------------------

    async def _process_records(self, records: list[RecordUpdate]) -> None:
        """Persist a batch of RecordUpdate objects."""
        if not records:
            return
        batch_sent = [(ru.record, ru.new_permissions) for ru in records]
        try:
            await self.c.data_entities_processor.on_new_records(batch_sent)
        except Exception as e:
            self.logger.error("Error persisting repo records: %s", e, exc_info=True)


# ------------------------------------------------------------------
# Module-level static helpers
# ------------------------------------------------------------------

def _branch_head_commit_sha(branch_data: Any) -> str | None:
    """Extract commit id from a python-gitlab branch or REST dict."""
    commit = getattr(branch_data, "commit", None)
    if commit is None and isinstance(branch_data, dict):
        commit = branch_data.get("commit")
    if commit is None:
        return None
    if isinstance(commit, dict):
        sha = commit.get("id")
        return str(sha) if sha else None
    sha = getattr(commit, "id", None)
    return str(sha) if sha else None


def _should_continue_repo_tree_pagination(
    raw_nodes_fetched: int, total_collected: int, page_info: dict[str, Any]
) -> tuple[bool, str]:
    """Whether to fetch another ``paginatedTree`` page and the next cursor."""
    has_next = bool(page_info.get("hasNextPage"))
    end_cursor = page_info.get("endCursor") or ""
    if not has_next or not end_cursor:
        return False, ""
    if raw_nodes_fetched == 0 and total_collected > 0:
        return False, ""
    return True, end_cursor


def _should_skip_dotfile_repo_path(repo_path: str) -> bool:
    """True when the basename of a repo path starts with '.'."""
    basename = repo_path.rsplit("/", 1)[-1]
    return basename.startswith(".")


def _code_blob_web_path(project_path: str, repo_path: str, ref: str = "HEAD") -> str:
    return f"/{project_path}/-/blob/{ref}/{repo_path}"


def _code_tree_web_path(project_path: str, repo_path: str, ref: str = "HEAD") -> str:
    return f"/{project_path}/-/tree/{ref}/{repo_path}"


def _repo_path_from_blob_web_url(web_url: str | None) -> str | None:
    """Extract repo-relative path from a GitLab blob webUrl."""
    if not web_url:
        return None
    marker = "/-/blob/"
    idx = web_url.find(marker)
    if idx < 0:
        return None
    after = web_url[idx + len(marker):]
    ref_sep = after.find("/")
    if ref_sep < 0:
        return None
    return unquote(after[ref_sep + 1:])


def _classify_compare_diffs(
    diffs: list[Any],
) -> tuple[list[str], list[str], list[str], list[tuple[str, str]]]:
    """Classify compare-API diffs into (deletes, adds, modifies, renames)."""
    deletes: list[str] = []
    adds: list[str] = []
    modifies: list[str] = []
    renames: list[tuple[str, str]] = []
    seen_delete: set[str] = set()
    seen_add: set[str] = set()
    seen_modify: set[str] = set()
    seen_rename_old: set[str] = set()

    def _add_delete(path: str) -> None:
        if path and path not in seen_delete and not _should_skip_dotfile_repo_path(path):
            deletes.append(path)
            seen_delete.add(path)

    def _add_add(path: str) -> None:
        if path and path not in seen_add and not _should_skip_dotfile_repo_path(path):
            adds.append(path)
            seen_add.add(path)

    def _add_modify(path: str) -> None:
        if path and path not in seen_modify and not _should_skip_dotfile_repo_path(path):
            modifies.append(path)
            seen_modify.add(path)

    for diff in diffs:
        old_path = (diff.get("old_path") if isinstance(diff, dict) else getattr(diff, "old_path", None)) or ""
        new_path = (diff.get("new_path") if isinstance(diff, dict) else getattr(diff, "new_path", None)) or ""
        is_deleted = bool(diff.get("deleted_file") if isinstance(diff, dict) else getattr(diff, "deleted_file", False))
        is_renamed = bool(diff.get("renamed_file") if isinstance(diff, dict) else getattr(diff, "renamed_file", False))
        is_new = bool(diff.get("new_file") if isinstance(diff, dict) else getattr(diff, "new_file", False))

        if is_deleted:
            _add_delete(old_path)
            continue
        if is_renamed:
            effective_old = old_path
            effective_new = new_path or old_path
            if _should_skip_dotfile_repo_path(effective_new):
                _add_delete(effective_old)
                continue
            if _should_skip_dotfile_repo_path(effective_old):
                _add_add(effective_new)
                continue
            if effective_old and effective_old not in seen_rename_old:
                renames.append((effective_old, effective_new))
                seen_rename_old.add(effective_old)
            continue
        target = new_path or old_path
        if is_new:
            _add_add(target)
        else:
            _add_modify(target)

    return deletes, adds, modifies, renames


def _gitlab_timestamp_to_ms(value: Any) -> int | None:
    """Normalise GitLab commit date strings or datetimes to epoch ms."""
    from app.utils.time_conversion import parse_timestamp
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp() * 1000)
    if isinstance(value, str) and value.strip():
        try:
            return parse_timestamp(value)
        except (ValueError, TypeError):
            return None
    return None
