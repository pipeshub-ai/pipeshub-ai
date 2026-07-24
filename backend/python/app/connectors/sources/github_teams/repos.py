"""
Code repository synchronisation for the GitHub Teams connector.

Shared verbatim by the personal connector (identical logic; only the caller —
``ProjectsSync``'s scope resolution — differs).

Responsibilities:
- ``run``: dispatch between full and incremental sync, manage checkpoints.
- ``_full_sync``: single recursive Git Tree API call; falls back to a
  per-subtree walk when GitHub truncates the response.
- ``_incremental_sync``: Compare Commits API, with overflow guards and blob-SHA
  reconciliation to promote delete+add pairs (squash/rebase) into renames.
- Folder record creation/cleanup and code file content streaming.
- Post-sync commit-history timestamp backfill (source_created_at/updated_at).

External IDs are anchored on the stable numeric ``repo.id`` — never
``owner/repo`` or branch name — so renames/transfers never orphan a checkpoint
or a record's identity. See ``models.py`` docstring / connector plan for the
full rationale.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from github.Repository import Repository  # type: ignore

from app.config.constants.arangodb import CollectionNames, Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.connectors.core.base.sync_point.sync_point import generate_record_sync_point_key
from app.connectors.core.registry.filters import IndexingFilterKey
from app.models.entities import CodeFileRecord, FileRecord, RecordGroupType, RecordType

from .constants import (
    CODE_FILE_MAX_SIZE_BYTES,
    COMPARE_COMMITS_FILES_LIMIT,
    COMPARE_COMMITS_TOTAL_LIMIT,
    DOTFILE_PREFIX,
    PREVIEW_RENDERABLE_EXTENSIONS,
)
from .models import GitHubLiterals, RecordUpdate

if TYPE_CHECKING:
    from app.connectors.sources.github_teams.connector import GitHubTeamsConnector


def _is_dotfile_path(repo_path: str) -> bool:
    """True when the basename of a repo-relative path starts with '.'."""
    basename = repo_path.rsplit("/", 1)[-1]
    return basename.startswith(DOTFILE_PREFIX)


def _blob_external_id(repo_id: int, repo_path: str) -> str:
    """Stable, rename-proof identity for a code file: anchored on repo.id, not owner/repo."""
    return f"/{repo_id}/blob/{repo_path}"


def _tree_external_id(repo_id: int, repo_path: str) -> str:
    """Stable, rename-proof identity for a folder: anchored on repo.id, not owner/repo."""
    return f"/{repo_id}/tree/{repo_path}"


class ReposSync:
    """Handles code repository (file/blob) synchronisation for ``GitHubTeamsConnector``."""

    def __init__(self, connector: "GitHubTeamsConnector") -> None:
        self.c = connector
        self.logger = connector.logger

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self, repo: Repository) -> None:
        """Sync a repo's code: incremental when a checkpoint exists and the
        default branch hasn't changed, full sync (re-baseline) otherwise."""
        c = self.c
        owner, name = repo.owner.login, repo.name
        default_branch = repo.default_branch
        if not default_branch:
            self.logger.warning("Repo %s has no default branch (empty repo?); skipping code sync", repo.full_name)
            return

        branch_res = await c.runtime.ds_call(c.data_source.get_branch, owner, name, default_branch)
        if not branch_res.success or not branch_res.data:
            self.logger.error(
                "Failed to fetch branch %s for %s: %s", default_branch, repo.full_name, branch_res.error
            )
            return
        commit = getattr(branch_res.data, "commit", None)
        current_sha = getattr(commit, "sha", None) if commit else None
        if not current_sha:
            self.logger.error("No HEAD commit SHA on branch %s for %s", default_branch, repo.full_name)
            return

        checkpoint = await self._get_checkpoint(repo.id)
        last_sha = checkpoint.get(GitHubLiterals.LAST_COMMIT_SHA.value) if checkpoint else None
        last_branch = checkpoint.get(GitHubLiterals.DEFAULT_BRANCH.value) if checkpoint else None

        if last_sha is None:
            self.logger.info("No code checkpoint for %s; running full sync", repo.full_name)
            ok = await self._full_sync(repo, current_sha)
            if ok:
                await self._update_checkpoint(repo, current_sha, default_branch)
            else:
                self.logger.warning("Full code sync for %s completed with errors; checkpoint not advanced", repo.full_name)
            return

        if last_branch and last_branch != default_branch:
            self.logger.info(
                "Default branch changed for %s (%s -> %s); re-baselining with full sync",
                repo.full_name, last_branch, default_branch,
            )
            ok = await self._full_sync(repo, current_sha)
            if ok:
                await self._update_checkpoint(repo, current_sha, default_branch)
            return

        if last_sha == current_sha:
            self.logger.debug("Code repo unchanged for %s (HEAD %s); skipping", repo.full_name, current_sha[:8])
            return

        ok = await self._incremental_sync(repo, last_sha, current_sha)
        if ok:
            await self._update_checkpoint(repo, current_sha, default_branch)
            return

        self.logger.warning("Incremental code sync failed for %s; falling back to full sync", repo.full_name)
        ok = await self._full_sync(repo, current_sha)
        if ok:
            await self._update_checkpoint(repo, current_sha, default_branch)
        else:
            self.logger.warning("Full sync fallback for %s completed with errors; checkpoint not advanced", repo.full_name)

    # ------------------------------------------------------------------
    # Checkpoints
    # ------------------------------------------------------------------

    def _checkpoint_key(self, repo_id: int) -> str:
        return generate_record_sync_point_key(
            Connectors.GITHUB_TEAMS.value, f"{repo_id}-code-repository", ""
        )

    async def _get_checkpoint(self, repo_id: int) -> dict[str, Any] | None:
        try:
            return await self.c.record_sync_point.read_sync_point(self._checkpoint_key(repo_id))
        except Exception:
            return None

    async def _update_checkpoint(self, repo: Repository, commit_sha: str, default_branch: str) -> None:
        await self.c.record_sync_point.update_sync_point(
            self._checkpoint_key(repo.id),
            {
                GitHubLiterals.LAST_COMMIT_SHA.value: commit_sha,
                GitHubLiterals.DEFAULT_BRANCH.value: default_branch,
                GitHubLiterals.FULL_NAME.value: repo.full_name,
            },
        )

    # ------------------------------------------------------------------
    # Full sync
    # ------------------------------------------------------------------

    async def _full_sync(self, repo: Repository, head_sha: str) -> bool:
        """Full sync via a single recursive Git Tree call; falls back to a
        per-subtree walk when GitHub truncates the response."""
        c = self.c
        owner, name = repo.owner.login, repo.name
        tree_res = await c.runtime.ds_call(c.data_source.get_git_tree, owner, name, head_sha, True)
        if not tree_res.success or tree_res.data is None:
            self.logger.error("get_git_tree failed for %s: %s", repo.full_name, tree_res.error)
            return False

        tree = tree_res.data
        if getattr(tree, "truncated", False):
            self.logger.warning(
                "Git tree truncated for %s (>100k entries or >7MB); falling back to per-subtree walk",
                repo.full_name,
            )
            return await self._full_sync_untruncated(repo, head_sha)

        entries = [
            (el.path, el.type, el.sha, getattr(el, "size", None))
            for el in (tree.tree or [])
            if el.path
        ]
        return await self._persist_tree_entries(repo, entries)

    async def _full_sync_untruncated(self, repo: Repository, head_sha: str) -> bool:
        """Per-subtree walk (non-recursive Git Tree calls) for repos too large for
        a single recursive call. BFS from the root tree SHA."""
        c = self.c
        owner, name = repo.owner.login, repo.name
        entries: list[tuple[str, str, str, int | None]] = []
        all_ok = True
        queue: deque[tuple[str, str]] = deque([("", head_sha)])
        while queue:
            prefix, sha = queue.popleft()
            res = await c.runtime.ds_call(c.data_source.get_git_tree, owner, name, sha, False)
            if not res.success or res.data is None:
                self.logger.warning("Subtree fetch failed for %s at %r (sha=%s): %s", repo.full_name, prefix, sha, res.error)
                all_ok = False
                continue
            for el in res.data.tree or []:
                if not el.path:
                    continue
                full_path = f"{prefix}/{el.path}" if prefix else el.path
                entries.append((full_path, el.type, el.sha, getattr(el, "size", None)))
                if el.type == "tree":
                    queue.append((full_path, el.sha))

        persisted_ok = await self._persist_tree_entries(repo, entries)
        return persisted_ok and all_ok

    async def _persist_tree_entries(
        self, repo: Repository, entries: list[tuple[str, str, str, int | None]]
    ) -> bool:
        """Persist a flat list of ``(path, type, sha, size)`` Git Tree entries:
        folders top-down (parents before children), then blobs."""
        c = self.c
        folders = [(p, s) for p, t, s, _sz in entries if t == "tree"]
        blobs = [(p, s, sz) for p, t, s, sz in entries if t == "blob"]

        level_wise: dict[int, list[tuple[str, str]]] = {}
        for path, sha in folders:
            level_wise.setdefault(path.count("/"), []).append((path, sha))
        for _level, files in sorted(level_wise.items()):
            record_updates = [self._build_folder_record(repo, path, sha) for path, sha in files]
            await self._process_records(record_updates)

        code_files_enabled = self._code_files_indexing_enabled()
        batch: list[RecordUpdate] = []
        skipped = 0
        for path, sha, size in blobs:
            record_update = self._build_code_file_record_update(repo, path, sha, size, code_files_enabled)
            if record_update is None:
                skipped += 1
                continue
            batch.append(record_update)
            if len(batch) >= c.batch_size * 4:
                await self._process_records(batch)
                batch = []
        if batch:
            await self._process_records(batch)

        self.logger.info(
            "Full code sync for %s: %s folder(s), %s file(s) persisted, %s skipped",
            repo.full_name, len(folders), len(blobs) - skipped, skipped,
        )
        return True

    # ------------------------------------------------------------------
    # Incremental sync
    # ------------------------------------------------------------------

    async def _incremental_sync(self, repo: Repository, from_sha: str, to_sha: str) -> bool:
        """Compare-commits-based incremental sync. Returns False (triggering a
        full-sync fallback) on any overflow condition or API failure."""
        c = self.c
        owner, name = repo.owner.login, repo.name
        cmp_res = await c.runtime.ds_call(c.data_source.compare_commits, owner, name, from_sha, to_sha)
        if not cmp_res.success or cmp_res.data is None:
            self.logger.warning("compare_commits failed for %s: %s", repo.full_name, cmp_res.error)
            return False

        comparison = cmp_res.data
        status = getattr(comparison, "status", None)
        if status == "diverged":
            self.logger.warning(
                "compare_commits returned status=diverged for %s (force-push/history rewrite); falling back to full sync",
                repo.full_name,
            )
            return False

        files = list(getattr(comparison, "files", None) or [])
        total_commits = getattr(comparison, "total_commits", 0) or 0
        if len(files) >= COMPARE_COMMITS_FILES_LIMIT:
            self.logger.warning(
                "compare_commits files list capped (%s) for %s; some changes may be missing — falling back to full sync",
                len(files), repo.full_name,
            )
            return False
        if total_commits >= COMPARE_COMMITS_TOTAL_LIMIT:
            self.logger.warning(
                "compare_commits total_commits=%s (>=%s) for %s; falling back to full sync",
                total_commits, COMPARE_COMMITS_TOTAL_LIMIT, repo.full_name,
            )
            return False

        deletes, adds, modifies, renames = self._classify_compare_files(files)
        self.logger.info(
            "Incremental code sync for %s: %s deletes, %s adds, %s modifies, %s renames (before SHA reconcile)",
            repo.full_name, len(deletes), len(adds), len(modifies), len(renames),
        )

        deletes, adds, extra_renames = await self._reconcile_sha_moves(repo, deletes, adds)
        renames = renames + extra_renames
        if extra_renames:
            self.logger.info(
                "SHA reconcile promoted %s delete+add pair(s) to rename for %s", len(extra_renames), repo.full_name
            )

        all_ok = True
        if deletes:
            await self._delete_code_files_by_paths(repo, list(deletes.keys()))
        if renames:
            ok = await self._apply_code_renames(repo, renames)
            all_ok = all_ok and ok
        upserts: dict[str, str] = {**adds, **modifies}
        if upserts:
            ok = await self._upsert_code_files(repo, upserts)
            all_ok = all_ok and ok

        removed_paths = list(deletes.keys()) + [old for old, _new, _sha in renames]
        if removed_paths:
            await self._cleanup_emptied_folders(repo, removed_paths)

        if not all_ok:
            self.logger.warning(
                "Incremental sync for %s completed with partial failures; checkpoint not advanced", repo.full_name
            )
        return all_ok

    def _classify_compare_files(
        self, files: list[Any]
    ) -> tuple[dict[str, str], dict[str, str], dict[str, str], list[tuple[str, str, str]]]:
        """Classify Compare Commits ``files[]`` entries.

        Returns ``(deletes, adds, modifies, renames)`` where deletes/adds/modifies
        are ``{path: sha}`` (sha is ``None``/empty for deletes) and renames are
        ``(previous_filename, filename, sha)`` tuples. ``copied`` is treated as
        an add (new independent identity); dotfiles are dropped entirely.
        """
        deletes: dict[str, str] = {}
        adds: dict[str, str] = {}
        modifies: dict[str, str] = {}
        renames: list[tuple[str, str, str]] = []

        for f in files:
            status = getattr(f, "status", "")
            filename = getattr(f, "filename", None)
            previous_filename = getattr(f, "previous_filename", None)
            sha = getattr(f, "sha", "") or ""

            if status == "removed":
                if filename and not _is_dotfile_path(filename):
                    deletes[filename] = sha
                continue
            if status == "renamed":
                old_path = previous_filename or filename
                new_path = filename or previous_filename
                if not old_path or not new_path:
                    continue
                if _is_dotfile_path(new_path):
                    deletes[old_path] = ""
                    continue
                if _is_dotfile_path(old_path):
                    adds[new_path] = sha
                    continue
                renames.append((old_path, new_path, sha))
                continue

            target = filename or previous_filename
            if not target or _is_dotfile_path(target):
                continue
            if status == "added" or status == "copied":
                adds[target] = sha
            else:  # "modified" | "changed"
                modifies[target] = sha

        return deletes, adds, modifies, renames

    # ------------------------------------------------------------------
    # SHA reconciliation (squash/rebase: delete+add sharing a blob SHA -> rename)
    # ------------------------------------------------------------------

    async def _reconcile_sha_moves(
        self, repo: Repository, deletes: dict[str, str], adds: dict[str, str]
    ) -> tuple[dict[str, str], dict[str, str], list[tuple[str, str, str]]]:
        """Promote delete+add pairs whose blob SHA matches into renames.

        The deleted path's "before" SHA comes from our own DB (the previously
        indexed record's ``external_revision_id`` / ``file_hash``) — Compare's
        ``removed`` entries do not reliably carry a usable SHA — while the added
        path's SHA comes directly off the compare response, so no extra API
        call is needed (unlike GitLab, whose diff payload carries no blob SHA
        at all).
        """
        if not deletes or not adds:
            return deletes, adds, []
        c = self.c
        sha_to_new_path: dict[str, str] = {}
        for new_path, sha in adds.items():
            if sha:
                sha_to_new_path.setdefault(sha, new_path)

        extra_renames: list[tuple[str, str, str]] = []
        promoted_deletes: set[str] = set()
        promoted_adds: set[str] = set()
        for old_path in deletes:
            old_external_id = _blob_external_id(repo.id, old_path)
            existing = await c.data_entities_processor.get_record_by_external_id(c.connector_id, old_external_id)
            if existing is None:
                continue
            stored_sha = getattr(existing, "external_revision_id", None) or getattr(existing, "file_hash", None)
            if not stored_sha:
                continue
            new_path = sha_to_new_path.get(stored_sha)
            if new_path is None or new_path in promoted_adds:
                continue
            extra_renames.append((old_path, new_path, stored_sha))
            promoted_deletes.add(old_path)
            promoted_adds.add(new_path)

        remaining_deletes = {p: s for p, s in deletes.items() if p not in promoted_deletes}
        remaining_adds = {p: s for p, s in adds.items() if p not in promoted_adds}
        return remaining_deletes, remaining_adds, extra_renames

    # ------------------------------------------------------------------
    # Rename / upsert / delete
    # ------------------------------------------------------------------

    async def _apply_code_renames(self, repo: Repository, renames: list[tuple[str, str, str]]) -> bool:
        """Apply in-place renames via ``on_records_moved`` — reuses the existing
        DB vertex, so permission/parent edges survive and no reindex fires
        unless the blob SHA also changed."""
        if not renames:
            return True
        c = self.c
        new_paths = [new_p for _old, new_p, _sha in renames]
        await self._ensure_folder_records_for_paths(repo, new_paths)

        code_files_enabled = self._code_files_indexing_enabled()
        moves: list[tuple[str, Any, list[Any]]] = []
        for old_path, new_path, new_sha in renames:
            if _is_dotfile_path(new_path):
                await self._delete_code_files_by_paths(repo, [old_path])
                continue
            new_record = self._build_code_file_record(repo, new_path, new_sha, code_files_enabled)
            old_external_id = _blob_external_id(repo.id, old_path)
            moves.append((old_external_id, new_record, []))

        if moves:
            await c.data_entities_processor.on_records_moved(moves)
        return True

    async def _upsert_code_files(self, repo: Repository, path_to_sha: dict[str, str]) -> bool:
        """Upsert (add/modify) code file records for the given repo-relative paths."""
        if not path_to_sha:
            return True
        await self._ensure_folder_records_for_paths(repo, list(path_to_sha.keys()))
        code_files_enabled = self._code_files_indexing_enabled()
        record_updates: list[RecordUpdate] = []
        for path, sha in path_to_sha.items():
            record_update = self._build_code_file_record_update(repo, path, sha, None, code_files_enabled)
            if record_update is not None:
                record_updates.append(record_update)
        if record_updates:
            await self._process_records(record_updates)
        return True

    async def _delete_code_files_by_paths(self, repo: Repository, paths: list[str]) -> None:
        c = self.c
        for path in paths:
            external_id = _blob_external_id(repo.id, path)
            record = await c.data_entities_processor.get_record_by_external_id(c.connector_id, external_id)
            if record:
                await c.data_entities_processor.on_record_deleted(record.id)

    # ------------------------------------------------------------------
    # Folder management
    # ------------------------------------------------------------------

    async def _ensure_folder_records_for_paths(self, repo: Repository, file_paths: list[str]) -> None:
        """Create missing folder records for the parent-directory chain of changed files.

        No API call is needed: a file existing at ``a/b/c.py`` implies ``a`` and
        ``a/b`` exist in the tree, so folder records can be synthesised purely
        from the path string. Only the DB existence check requires a lookup.
        """
        c = self.c
        prefixes: set[str] = set()
        for path in file_paths:
            parts = path.split("/")
            for i in range(1, len(parts)):
                prefixes.add("/".join(parts[:i]))
        if not prefixes:
            return

        record_updates: list[RecordUpdate] = []
        for prefix in sorted(prefixes, key=lambda p: p.count("/")):
            external_id = _tree_external_id(repo.id, prefix)
            existing = await c.data_entities_processor.get_record_by_external_id(c.connector_id, external_id)
            if existing:
                continue
            record_updates.append(self._build_folder_record(repo, prefix, sha=None))

        if record_updates:
            await self._process_records(record_updates)

    async def _cleanup_emptied_folders(self, repo: Repository, removed_paths: list[str]) -> None:
        """Delete folder records that became empty after deletes/renames (bottom-up)."""
        c = self.c
        candidate_dirs: set[str] = set()
        for path in removed_paths:
            parts = path.split("/")
            for i in range(1, len(parts)):
                candidate_dirs.add("/".join(parts[:i]))
        if not candidate_dirs:
            return

        deleted_count = 0
        for dir_path in sorted(candidate_dirs, key=lambda p: p.count("/"), reverse=True):
            external_id = _tree_external_id(repo.id, dir_path)
            folder = await c.data_entities_processor.get_record_by_external_id(c.connector_id, external_id)
            if folder is None:
                continue
            children = await c.data_entities_processor.get_records_by_parent(c.connector_id, external_id)
            if children:
                continue
            await c.data_entities_processor.on_record_deleted(folder.id)
            deleted_count += 1
        if deleted_count:
            self.logger.info("Folder cleanup removed %s emptied folder record(s) for %s", deleted_count, repo.full_name)

    # ------------------------------------------------------------------
    # Record building
    # ------------------------------------------------------------------

    def _build_folder_record(self, repo: Repository, path: str, sha: str | None) -> RecordUpdate:
        c = self.c
        name = path.rsplit("/", 1)[-1]
        parent_path = path.rpartition("/")[0] if "/" in path else None
        external_id = _tree_external_id(repo.id, path)
        parent_external_id = _tree_external_id(repo.id, parent_path) if parent_path else None
        weburl = f"{repo.html_url}/tree/{repo.default_branch}/{path}"
        record = FileRecord(
            id=str(uuid.uuid4()), org_id=c.data_entities_processor.org_id, record_name=name,
            record_type=RecordType.FILE.value, connector_name=c.connector_name, connector_id=c.connector_id,
            external_record_id=external_id, version=0, origin=OriginTypes.CONNECTOR.value,
            record_group_type=RecordGroupType.PROJECT.value,
            external_record_group_id=f"{repo.id}-code-repository",
            mime_type=MimeTypes.FOLDER.value, external_revision_id=str(sha) if sha else "",
            preview_renderable=False, parent_external_record_id=parent_external_id,
            parent_record_type=(RecordType.FILE if parent_external_id else None),
            is_file=False, inherit_permissions=True, weburl=weburl,
        )
        return RecordUpdate(
            record=record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            old_permissions=[], new_permissions=[], external_record_id=external_id,
        )

    def _build_code_file_record(self, repo: Repository, path: str, sha: str | None, code_files_enabled: bool) -> CodeFileRecord:
        c = self.c
        name = path.rsplit("/", 1)[-1]
        extension = name.rsplit(".", 1)[-1] if "." in name else ""
        mime_type = getattr(MimeTypes, extension.upper(), MimeTypes.PLAIN_TEXT).value if extension else MimeTypes.PLAIN_TEXT.value
        preview_renderable = extension.lower() in PREVIEW_RENDERABLE_EXTENSIONS
        external_id = _blob_external_id(repo.id, path)
        parent_path = path.rpartition("/")[0] if "/" in path else None
        parent_external_id = _tree_external_id(repo.id, parent_path) if parent_path else None
        weburl = f"{repo.html_url}/blob/{repo.default_branch}/{path}"
        record = CodeFileRecord(
            id=str(uuid.uuid4()), org_id=c.data_entities_processor.org_id, record_name=name,
            record_type=RecordType.CODE_FILE.value, connector_name=c.connector_name, connector_id=c.connector_id,
            external_record_id=external_id, version=0, origin=OriginTypes.CONNECTOR.value,
            record_group_type=RecordGroupType.PROJECT.value,
            external_record_group_id=f"{repo.id}-code-repository",
            mime_type=mime_type, external_revision_id=str(sha) if sha else "",
            preview_renderable=preview_renderable, file_path=path, file_hash=sha,
            inherit_permissions=True, parent_external_record_id=parent_external_id,
            parent_record_type=(RecordType.FILE if parent_external_id else None),
            weburl=weburl,
        )
        if not code_files_enabled:
            record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
        return record

    def _build_code_file_record_update(
        self, repo: Repository, path: str, sha: str | None, size: int | None, code_files_enabled: bool
    ) -> RecordUpdate | None:
        if _is_dotfile_path(path):
            return None
        if size is not None and size > CODE_FILE_MAX_SIZE_BYTES:
            self.logger.debug("Skipping oversized blob %r (%s bytes) in %s", path, size, repo.full_name)
            return None
        record = self._build_code_file_record(repo, path, sha, code_files_enabled)
        return RecordUpdate(
            record=record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            old_permissions=[], new_permissions=[], external_record_id=record.external_record_id,
        )

    async def _process_records(self, records: list[RecordUpdate]) -> None:
        if not records:
            return
        batch_sent = [(ru.record, ru.new_permissions) for ru in records]
        try:
            await self.c.data_entities_processor.on_new_records(batch_sent)
        except Exception as e:
            self.logger.error("Error persisting GitHub code repo records: %s", e, exc_info=True)

    # ------------------------------------------------------------------
    # Content streaming
    # ------------------------------------------------------------------

    async def fetch_code_file_content(self, record: CodeFileRecord) -> bytes:
        """Fetch raw code file content at stream time (no blocks — raw bytes)."""
        c = self.c
        external_group_id = getattr(record, "external_record_group_id", None)
        if not external_group_id:
            raise Exception(f"Repository id not found on record {record.id}")
        repo_id = int(external_group_id.split("-")[0])
        file_path = record.file_path
        if not file_path:
            raise Exception(f"Cannot resolve repo path for record {record.id}")

        repo_res = await c.runtime.ds_call(c.data_source.get_repo_by_id, repo_id)
        if not repo_res.success or not repo_res.data:
            raise Exception(f"Failed to resolve repo id={repo_id} for record {record.id}: {repo_res.error}")
        repo = repo_res.data

        content_res = await c.runtime.ds_call(
            c.data_source.get_file_contents, repo.owner.login, repo.name, file_path, repo.default_branch,
        )
        if not content_res.success or content_res.data is None:
            raise Exception(f"Failed to fetch content for {file_path} in {repo.full_name}: {content_res.error}")
        content_file = content_res.data
        decoded = getattr(content_file, "decoded_content", None)
        if decoded is not None:
            return decoded
        raw = getattr(content_file, "content", None)
        if raw:
            import base64
            return base64.b64decode(raw)
        return b""

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
        """Schedule the post-sync commit-history timestamp backfill in the background."""
        c = self.c
        existing = c._code_file_timestamp_backfill_task
        if existing is not None and not existing.done():
            return
        c._code_file_timestamp_backfill_task = asyncio.create_task(
            self._backfill_code_file_timestamps_after_sync(),
            name=f"github_teams_code_file_ts_backfill_{c.connector_id}",
        )

    async def _backfill_code_file_timestamps_after_sync(self) -> None:
        c = self.c
        try:
            await c.runtime.refresh_token_if_needed()
            repos = await c.projects._resolve_repos_with_filters()
            for repo in repos:
                await self._run_code_file_timestamp_backfill(repo)
        except Exception as e:
            self.logger.error("Code file timestamp backfill failed for connector %s: %s", c.connector_id, e, exc_info=True)
        finally:
            c._code_file_timestamp_backfill_task = None

    async def _run_code_file_timestamp_backfill(self, repo: Repository) -> None:
        """Backfill source_created_at/source_updated_at for code files still missing them."""
        c = self.c
        batch_size = 100
        try:
            await c.runtime.refresh_token_if_needed()
            external_group_id = f"{repo.id}-code-repository"
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
                if code_file.file_path:
                    path_to_records.setdefault(code_file.file_path, []).append(code_file)

            file_paths = list(path_to_records.keys())
            owner, name = repo.owner.login, repo.name
            for offset in range(0, len(file_paths), batch_size):
                await c.runtime.refresh_token_if_needed()
                batch_paths = file_paths[offset:offset + batch_size]
                timestamp_by_path = await self._fetch_timestamps_batch(owner, name, batch_paths)
                for file_path in batch_paths:
                    created_ms, updated_ms = timestamp_by_path.get(file_path, (None, None))
                    if created_ms is None and updated_ms is None:
                        continue
                    for record in path_to_records[file_path]:
                        try:
                            updated_record = record.model_copy(
                                update={"source_created_at": created_ms, "source_updated_at": updated_ms}
                            )
                            await c.data_entities_processor.on_record_metadata_update(updated_record)
                        except Exception as e:
                            self.logger.warning("Failed to update timestamps for record %s path %s: %s", record.id, file_path, e)
        except Exception as e:
            self.logger.error("Code file timestamp backfill failed for repo %s: %s", repo.full_name, e, exc_info=True)

    async def _fetch_timestamps_batch(
        self, owner: str, name: str, paths: list[str]
    ) -> dict[str, tuple[int | None, int | None]]:
        c = self.c
        semaphore = asyncio.Semaphore(10)
        results: dict[str, tuple[int | None, int | None]] = {}

        async def fetch_one(path: str) -> None:
            async with semaphore:
                try:
                    # GitHub's commits API has no "oldest/newest for this path"
                    # aggregate (unlike GitLab) — list_commits(path=...) walks the
                    # entire path history. Acceptable here since this is a
                    # best-effort, semaphore-bounded background enrichment, not
                    # on the sync critical path.
                    res = await c.runtime.ds_call(c.data_source.list_commits, owner, name, None, path)
                    commits = res.data if res.success else None
                    if not commits:
                        results[path] = (None, None)
                        return
                    newest = _commit_authored_ms(commits[0])
                    oldest = _commit_authored_ms(commits[-1])
                    results[path] = (oldest, newest)
                except Exception as e:
                    self.logger.debug("Failed to fetch commit history for %s: %s", path, e)
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
        return c.indexing_filters.is_enabled(IndexingFilterKey.CODE_FILES)


def _commit_authored_ms(commit: Any) -> int | None:
    """Extract the commit author date (epoch ms) from a PyGithub ``Commit``."""
    git_commit = getattr(commit, "commit", None)
    author = getattr(git_commit, "author", None) if git_commit else None
    date = getattr(author, "date", None) if author else None
    if date is None:
        return None
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return round(date.timestamp() * 1000)
