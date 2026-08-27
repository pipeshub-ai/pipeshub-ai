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
import mimetypes
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

from app.config.constants.arangodb import (
    SUPPORTED_CODE_FILE_EXTENSIONS,
    CollectionNames,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.constants import (
    IconPaths,
)
from app.models.entities import CodeFileRecord, FileRecord, RecordGroupType, RecordType
from app.modules.parsers.code_parser.file_role import (
    classify_file_role,
    should_index_code_file,
)
from app.modules.parsers.code_parser.lang_config import detect_language

from .constants import (
    GITLAB_COMPARE_DIFF_LIMIT,
    PREVIEW_RENDERABLE_EXTENSIONS,
    _GITLAB_BACKFILL_CONCURRENCY,
    _GITLAB_TREE_PAGE_MAX_ATTEMPTS,
    _GITLAB_TREE_PAGE_RETRY_BACKOFF_SECONDS,
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
        """Full sync of default-branch folders and blobs via one paginated GraphQL walk.

        Each page's folders and blobs are persisted immediately so only ~2 pages
        of entries are in memory at any time (the page being processed plus the
        page being pre-fetched). When a blob arrives before its parent folder
        the data-entities processor auto-creates a placeholder folder record;
        the real folder record (from the same or a later page) upgrades it in
        place — no data is lost.

        Returns ``True`` when sync completed without API errors, ``False`` on
        recoverable error (caller must not advance checkpoint).
        """
        after_cursor = ""
        folders_walked = 0
        blobs_walked = 0
        any_data = False
        all_ok = True

        pending: asyncio.Task[tuple[str, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]] | None = (
            asyncio.create_task(self._fetch_entries_page(project_path, project_id, after_cursor))
        )
        try:
            while pending is not None:
                kind, page_trees, page_blobs, page_info = await pending
                pending = None
                if kind == "abort":
                    return False
                if kind == "empty":
                    break
                any_data = True
                continue_paging, next_cursor = _should_continue_repo_tree_pagination(page_info)
                if continue_paging and next_cursor == after_cursor:
                    self.logger.error(
                        "Repository walk for project %s stalled: cursor %r did not advance; "
                        "stopping without advancing the checkpoint.",
                        project_id, next_cursor,
                    )
                    return False
                after_cursor = next_cursor
                if continue_paging:
                    pending = asyncio.create_task(
                        self._fetch_entries_page(project_path, project_id, after_cursor)
                    )
                if page_trees:
                    all_ok = await self._persist_folder_records(page_trees, project_id) and all_ok
                    folders_walked += len(page_trees)
                if page_blobs:
                    all_ok = await self.build_code_file_records(
                        page_blobs, project_id, project_path
                    ) and all_ok
                    blobs_walked += len(page_blobs)
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                try:
                    await pending
                except (asyncio.CancelledError, Exception):
                    pass

        if not any_data:
            self.logger.info("No repository entries found for project %s", project_id)
            return True

        self.logger.info(
            "Full code sync for project %s: %s folder(s), %s file(s) walked%s",
            project_id, folders_walked, blobs_walked,
            "" if all_ok else " (INCOMPLETE - checkpoint withheld)",
        )
        return all_ok

    async def _persist_folder_records(
        self, tree_nodes: list[dict[str, Any]], project_id: int
    ) -> bool:
        """Persist folder records from a single page in one batch."""
        external_group_id = f"{project_id}-code-repository"
        code_files_enabled = self._code_files_indexing_enabled()

        updates: list[RecordUpdate] = []
        for item in tree_nodes:
            if item.get("type") != "tree":
                continue
            file_path = item.get("path") or ""
            file_name = item.get("name")
            external_record_id = item.get("webPath")
            if not external_record_id or not file_name:
                self.logger.warning("Skipping tree %s: missing webPath/name", file_path)
                continue
            parent_external_record_id = (
                external_record_id.rpartition("/")[0] if "/" in file_path else None
            )
            tree_record = self._build_folder_record(
                folder_name=str(file_name),
                external_record_id=external_record_id,
                weburl=item.get("webUrl"),
                folder_hash=item.get("sha"),
                external_group_id=external_group_id,
                parent_external_record_id=parent_external_record_id,
                code_files_enabled=code_files_enabled,
            )
            updates.append(RecordUpdate(
                record=tree_record, is_new=True, is_updated=False, is_deleted=False,
                metadata_changed=False, content_changed=False, permissions_changed=False,
                external_record_id=str(external_record_id), new_permissions=[], old_permissions=[],
            ))
        if updates:
            return await self._process_records(updates)
        return True

    async def _fetch_entries_page(
        self, project_path: str, project_id: int, after_cursor: str
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        """Fetch one ``paginatedTree`` page. Returns ``(kind, trees, blobs, page_info)``.

        ``kind`` is ``"data"``, ``"empty"`` (nothing left to walk — empty repo,
        archived, missing scope, or a page past the end) or ``"abort"`` (the
        caller must not advance the checkpoint).

        Transport failures are retried ``_GITLAB_TREE_PAGE_MAX_ATTEMPTS`` times
        with exponential backoff before aborting: one transiently slow page must
        not throw away an otherwise-successful walk of a huge repository. A
        GraphQL ``errors`` payload aborts immediately — that is a permissions or
        query-shape problem a retry cannot change.
        """
        last_error: str | None = None
        for attempt in range(1, _GITLAB_TREE_PAGE_MAX_ATTEMPTS + 1):
            if attempt > 1:
                delay = _GITLAB_TREE_PAGE_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 2))
                self.logger.warning(
                    "Retrying repository entries page for project %s "
                    "(attempt %s/%s, cursor=%r) in %.0fs: %s",
                    project_id, attempt, _GITLAB_TREE_PAGE_MAX_ATTEMPTS,
                    after_cursor, delay, last_error,
                )
                await asyncio.sleep(delay)
            kind, trees, blobs, page_info, last_error = await self._fetch_entries_page_once(
                project_path, project_id, after_cursor
            )
            if kind != "retry":
                return kind, trees, blobs, page_info

        self.logger.error(
            "Repository entries page failed after %s attempts for project %s "
            "(cursor=%r): %s",
            _GITLAB_TREE_PAGE_MAX_ATTEMPTS, project_id, after_cursor, last_error,
        )
        return "abort", [], [], {}

    async def _fetch_entries_page_once(
        self, project_path: str, project_id: int, after_cursor: str
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], str | None]:
        """One attempt at a ``paginatedTree`` page.

        Returns ``(kind, trees, blobs, page_info, error)`` where ``kind`` adds
        ``"retry"`` (transient transport failure) to the caller-facing kinds.
        """
        c = self.c
        empty: list[dict[str, Any]] = []
        try:
            res = await c.runtime.ds_call_async(
                c.data_source.get_repo_entries_g,
                project_id=project_path, ref="HEAD", after_cursor=after_cursor,
            )
        except Exception as e:
            return "retry", empty, empty, {}, f"{type(e).__name__}: {e}"
        if not res.success or not res.data:
            return "retry", empty, empty, {}, res.error or "empty response body"
        try:
            data: dict[str, Any] = json.loads(res.data)
        except json.JSONDecodeError as e:
            # A truncated body is a transport problem, not a semantic one.
            return "retry", empty, empty, {}, f"malformed JSON: {e}"
        if "errors" in data:
            self.logger.error(
                "GraphQL errors for project %s: %s", project_id, json.dumps(data["errors"])
            )
            return "abort", empty, empty, {}, None

        project = (data.get("data") or {}).get("project") or {}
        paginated_tree = (project.get("repository") or {}).get("paginatedTree") or {}
        if not paginated_tree:
            self.logger.info(
                "No repository tree for project %s (empty repo, missing scope, or archived)", project_id
            )
            return "empty", empty, empty, {}, None
        nodes = paginated_tree.get("nodes") or []
        page_info = paginated_tree.get("pageInfo") or {}
        if not nodes:
            return "empty", empty, empty, page_info, None
        entry: dict[str, Any] = nodes[0]
        trees = (entry.get("trees") or {}).get("nodes") or []
        blobs = (entry.get("blobs") or {}).get("nodes") or []
        return "data", trees, blobs, page_info, None

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
            web_path = _code_blob_web_path(project_path, new_path)
            weburl = f"{c._gitlab_base_url}{web_path}"
            parent_dir = new_path.rpartition("/")[0] if "/" in new_path else None
            parent_external_record_id = _code_tree_web_path(project_path, parent_dir) if parent_dir else None
            # Leave source timestamps unset — on_records_moved keeps the stored
            # Git timestamps rather than overwriting with sync time.
            new_record = self._build_blob_record(
                file_name=file_name,
                file_path=new_path,
                external_record_id=web_path,
                weburl=weburl,
                blob_sha=blob_sha,
                external_group_id=external_group_id,
                parent_external_record_id=parent_external_record_id,
                code_files_enabled=code_files_enabled,
            )
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
            all_ok = await self.build_code_file_records(nodes, project_id, project_path) and all_ok
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
        """Create missing folder records for parent directories of changed files.

        No API call is needed: a file at ``a/b/c.py`` implies ``a`` and ``a/b``
        exist in the tree, and the folder's name and web paths are derivable
        from the path string. Only the DB existence check needs a lookup. The
        previous version issued one ``list_repo_tree`` call per prefix purely to
        read back a name it already had.

        The folder's tree SHA is left unset for the same reason: it is not
        needed to address the record, and fetching it was the whole cost.
        """
        c = self.c
        prefixes: set[str] = set()
        for repo_path in file_paths:
            parts = repo_path.split("/")
            for i in range(1, len(parts)):
                prefixes.add("/".join(parts[:i]))
        if not prefixes:
            return

        external_group_id = f"{project_id}-code-repository"
        code_files_enabled = self._code_files_indexing_enabled()
        record_updates: list[RecordUpdate] = []

        for prefix in sorted(prefixes, key=lambda p: p.count("/")):
            web_path = _code_tree_web_path(project_path, prefix)
            existing = await c.data_entities_processor.get_record_by_external_id(
                c.connector_id, web_path
            )
            if existing:
                continue
            parent_prefix = prefix.rpartition("/")[0] if "/" in prefix else None
            tree_record = self._build_folder_record(
                folder_name=prefix.rsplit("/", 1)[-1],
                external_record_id=web_path,
                weburl=f"{c._gitlab_base_url}{web_path}",
                folder_hash=None,
                external_group_id=external_group_id,
                parent_external_record_id=(
                    _code_tree_web_path(project_path, parent_prefix) if parent_prefix else None
                ),
                code_files_enabled=code_files_enabled,
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
            await c.data_entities_processor.on_record_deleted(folder.id)
            deleted_count += 1
        if deleted_count:
            self.logger.info("Folder cleanup removed %d emptied folder record(s) for project %s", deleted_count, project_id)

    # ------------------------------------------------------------------
    # Code file record building
    # ------------------------------------------------------------------

    def _build_folder_record(
        self,
        *,
        folder_name: str,
        external_record_id: str,
        weburl: str | None,
        folder_hash: str | None,
        external_group_id: str,
        parent_external_record_id: str | None,
        code_files_enabled: bool,
    ) -> FileRecord:
        """Build the record for one repository folder.

        Stamped with the same indexing flag as the code files it contains:
        folders hold no content, so publishing indexing events for them while
        their files are AUTO_INDEX_OFF is pure waste — one Kafka message, one
        consumer dispatch and two status writes per folder, for a filter the
        user explicitly turned off.
        """
        c = self.c
        record = FileRecord(
            id=str(uuid.uuid4()),
            org_id=c.data_entities_processor.org_id,
            record_name=folder_name,
            record_type=RecordType.FILE.value,
            connector_name=c.connector_name,
            connector_id=c.connector_id,
            external_record_id=external_record_id,
            version=0,
            origin=OriginTypes.CONNECTOR.value,
            record_group_type=RecordGroupType.PROJECT.value,
            external_record_group_id=external_group_id,
            mime_type=MimeTypes.FOLDER.value,
            external_revision_id=str(folder_hash) if folder_hash else "",
            preview_renderable=False,
            parent_external_record_id=parent_external_record_id,
            parent_record_type=(RecordType.FILE if parent_external_record_id else None),
            is_file=False,
            inherit_permissions=True,
            weburl=weburl,
        )
        if not code_files_enabled:
            record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
        return record

    def _build_blob_record(
        self,
        *,
        file_name: str,
        file_path: str,
        external_record_id: str,
        weburl: str | None,
        blob_sha: str | None,
        external_group_id: str,
        parent_external_record_id: str | None,
        code_files_enabled: bool,
        source_created_at: int | None = None,
        source_updated_at: int | None = None,
    ) -> CodeFileRecord:
        """Build the record for one repository blob.

        Every blob is a CODE_FILE regardless of extension. What decides whether
        it indexes, and through which parser, is the mime type and the filename
        extension — not the record type (see the gate in
        ``services/messaging/kafka/handlers/record.py`` and the dispatch in
        ``events.py``). Typing non-source blobs as FILE instead would change the
        record's type collection on a rename across the source/non-source line,
        which leaves a second isOfType edge behind and no way to tell which one
        a read will pick.

        ``version`` is always 0 here; ``_process_record`` carries the stored
        version forward, where the existing record is already loaded.
        """
        c = self.c
        extension = _blob_extension(file_name)
        file_role = classify_file_role(file_path, file_name)
        record = CodeFileRecord(
            language=detect_language(file_name),
            file_role=file_role.value,
            id=str(uuid.uuid4()),
            org_id=c.data_entities_processor.org_id,
            record_name=file_name,
            record_type=RecordType.CODE_FILE.value,
            connector_name=c.connector_name,
            connector_id=c.connector_id,
            external_record_id=external_record_id,
            version=0,
            origin=OriginTypes.CONNECTOR.value,
            record_group_type=RecordGroupType.PROJECT.value,
            external_record_group_id=external_group_id,
            mime_type=_blob_mime_type(file_name, extension),
            extension=extension,
            external_revision_id=str(blob_sha) if blob_sha else "",
            preview_renderable=extension in PREVIEW_RENDERABLE_EXTENSIONS if extension else True,
            file_path=file_path,
            file_hash=blob_sha,
            inherit_permissions=True,
            parent_external_record_id=parent_external_record_id,
            parent_record_type=(RecordType.FILE if parent_external_record_id else None),
            weburl=weburl,
            source_created_at=source_created_at,
            source_updated_at=source_updated_at,
        )

        if not code_files_enabled:
            record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
        return record

    async def build_code_file_records(
        self, code_file_list: list[dict[str, Any]], project_id: int, project_path: str
    ) -> None:
        """Build and persist repository blob records from a blob list."""
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
            should_index, _role = should_index_code_file(file_path, file_name)
            if not should_index:
                files_skipped += 1
                continue
            if "/" in file_path:
                parent_blob_path = external_record_id.rpartition("/")[0]
                parent_external_record_id = parent_blob_path.replace("/-/blob/", "/-/tree/", 1)
            else:
                parent_external_record_id = None
            blob_record = self._build_blob_record(
                file_name=str(file_name),
                file_path=file_path,
                external_record_id=external_record_id,
                weburl=weburl,
                blob_sha=file_hash,
                external_group_id=external_group_id,
                parent_external_record_id=parent_external_record_id,
                code_files_enabled=code_files_enabled,
            )
            list_records_new.append(RecordUpdate(
                record=blob_record, is_new=True, is_updated=False, is_deleted=False,
                metadata_changed=False, content_changed=False, permissions_changed=False,
                external_record_id=external_record_id, new_permissions=[], old_permissions=[],
            ))

        if list_records_new:
            ok = await self._process_records(list_records_new)
            if files_skipped:
                self.logger.info("Processed %s code file records; %s skipped (non-indexable/missing metadata)", len(list_records_new), files_skipped)
            return ok
        return True

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
                file_path = await c.data_entities_processor.get_record_path(record.id)
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
        """Backfill timestamps for code files with null created/updated timestamps.

        Never fabricates Record objects from raw graph nodes — that drops
        type-document fields (file_path, extension, file_hash) and corrupts
        the stored record on write-back. Instead, only record IDs and paths
        are extracted from the node dicts, and timestamps are written via
        ``batch_update_nodes`` (a partial merge that touches only the named
        properties).
        """
        from app.utils.time_conversion import get_epoch_timestamp_in_ms

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

            path_to_record_ids: dict[str, list[str]] = {}
            for node in nodes:
                if node.get("isDeleted"):
                    continue
                record_id = node.get("id") or node.get("_key")
                external_id = node.get("externalRecordId") or ""
                if not record_id:
                    continue
                file_path = _repo_path_from_blob_web_url(external_id)
                if not file_path:
                    file_path = _repo_path_from_blob_web_url(node.get("webUrl"))
                if not file_path:
                    continue
                path_to_record_ids.setdefault(file_path, []).append(record_id)

            file_paths = list(path_to_record_ids.keys())
            if file_paths:
                self.logger.info(
                    "Timestamp backfill for project %s: %s file(s) pending.",
                    project_id, len(file_paths),
                )
            for offset in range(0, len(file_paths), batch_size):
                await c.runtime.refresh_token_if_needed()
                batch_paths = file_paths[offset:offset + batch_size]
                try:
                    timestamp_by_path = await self._fetch_code_file_timestamps_batch(project_id, batch_paths)
                except Exception as e:
                    self.logger.warning("Failed to fetch timestamps for project %s: %s", project_id, e)
                    continue
                patches: list[dict[str, Any]] = []
                for file_path in batch_paths:
                    created_ms, updated_ms = timestamp_by_path.get(file_path, (None, None))
                    if created_ms is None and updated_ms is None:
                        continue
                    for record_id in path_to_record_ids[file_path]:
                        patch: dict[str, Any] = {"id": record_id, "updatedAtTimestamp": get_epoch_timestamp_in_ms()}
                        if created_ms is not None:
                            patch["sourceCreatedAtTimestamp"] = created_ms
                        if updated_ms is not None:
                            patch["sourceLastModifiedTimestamp"] = updated_ms
                        patches.append(patch)
                if patches:
                    try:
                        async with c.data_store_provider.transaction() as tx_store:
                            await tx_store.batch_update_nodes(patches, CollectionNames.RECORDS.value)
                    except Exception as e:
                        self.logger.warning(
                            "Failed to patch %s timestamp(s) for project %s: %s",
                            len(patches), project_id, e,
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
        semaphore = asyncio.Semaphore(_GITLAB_BACKFILL_CONCURRENCY)
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

    async def _process_records(self, records: list[RecordUpdate]) -> bool:
        """Persist a batch of RecordUpdate objects; ``False`` if the write failed.

        Callers walking a repository must fold this into their return value: a
        swallowed failure that still advances the code-repo checkpoint sends the
        next run down the incremental path, so the records that were never
        written are never retried.
        """
        if not records:
            return True
        batch_sent = [(ru.record, ru.new_permissions) for ru in records]
        try:
            await self.c.data_entities_processor.on_new_records(batch_sent)
        except Exception as e:
            self.logger.error("Error persisting repo records: %s", e, exc_info=True)
            return False
        return True


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


def _should_continue_repo_tree_pagination(page_info: dict[str, Any]) -> tuple[bool, str]:
    """Whether to fetch another ``paginatedTree`` page, and the next cursor.

    ``hasNextPage`` is the only safe terminator. An earlier version also
    stopped when a page yielded no nodes of the type being collected, but
    ``paginatedTree`` returns folders and files interleaved in one stream: a
    directory holding 100+ files produces a page with zero folders, which is
    ordinary mid-walk output, not the end of the repository.
    """
    has_next = bool(page_info.get("hasNextPage"))
    end_cursor = page_info.get("endCursor") or ""
    if not has_next or not end_cursor:
        return False, ""
    return True, end_cursor


def _should_skip_dotfile_repo_path(repo_path: str) -> bool:
    """True when a repo path should not become a record at all.

    Dotfiles, plus dependency/build-output/cache trees and generated code. This
    runs off the git tree listing, so a rejected file is never fetched, never
    queued and never streamed — the whole cost is avoided, not just the parse.
    """
    basename = repo_path.rsplit("/", 1)[-1]
    if basename.startswith("."):
        return True
    should_index, _role = should_index_code_file(repo_path, basename)
    return not should_index


def _blob_extension(file_name: str) -> str | None:
    """Lower-cased filename extension, or None when the blob has none.

    Returns None rather than the whole name for extension-less blobs
    (``LICENSE``, ``Dockerfile``, ``Makefile``); ``name.split(".")[-1]`` would
    otherwise hand back "Dockerfile" as the extension.
    """
    base = file_name.rsplit("/", 1)[-1]
    if "." not in base:
        return None
    return base.rsplit(".", 1)[-1].lower()


def _blob_mime_type(file_name: str, extension: str | None) -> str:
    """Best-effort MIME type for a repository blob.

    Unrecognised extensions fall back to ``application/octet-stream`` rather than
    ``text/plain`` — a text default would let the indexer feed binaries (``.mp4``,
    ``.zip``) through the text parser. Known code extensions with no MIME of their
    own (``.css``, ``.lua``) still get ``text/plain``, which is what they are.
    """
    if extension is None:
        # Extension-less repo blobs are conventionally text (LICENSE, Dockerfile).
        return MimeTypes.PLAIN_TEXT.value

    named = MimeTypes.__members__.get(extension.upper())
    if named is not None:
        return named.value

    guessed, _ = mimetypes.guess_type(file_name)
    if guessed:
        try:
            return MimeTypes(guessed).value
        except ValueError:
            pass

    if extension in SUPPORTED_CODE_FILE_EXTENSIONS:
        return MimeTypes.PLAIN_TEXT.value
    return MimeTypes.BIN.value


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
