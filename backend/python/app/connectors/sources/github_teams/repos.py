"""
Code repository synchronisation for the GitHub Teams connector.

Shared verbatim by the personal connector (identical logic; only the caller —
``ProjectsSync``'s scope resolution — differs).

The flow, top to bottom, mirrors this file's layout:

1. ``run``          — decide: full sync, incremental, or skip (checkpoint = HEAD SHA).
2. Full sync        — ONE recursive Git Tree call -> folders -> files -> prune
                      deletions (BFS per-subtree fallback for giant repos).
3. Incremental      — ONE Compare Commits call -> classify -> reconcile
                      squash-renames -> deletes, renames, upserts -> folder cleanup.
4. Folder lifecycle — folder records synthesised from path strings (no API);
                      emptied folders removed bottom-up.
5. Record builders  — plain records handed to the data-entities processor,
                      which owns new-vs-update, versioning, and reindex decisions.
6. Content streaming— raw bytes at index time, with the 1-5MB blob fallback
                      and the oversized-413 guard.

Failure ladder: incremental failure -> full sync same run; any persist failure
-> checkpoint withheld -> redone next sync (all writes are idempotent upserts).
An untrusted delta (force-push, branch reset, GitHub's 300-file/250-commit
caps) is never applied — full sync re-baselines and pruning removes what the
delta could not see.

Every file in the tree gets a record — nothing is skipped. Files above
``CODE_FILE_MAX_SIZE_BYTES`` still get a record (visible, name-searchable) but
have content indexing switched off with a reason; the stream-time 413 guard
covers the incremental path, where Compare Commits carries no blob size.

External IDs are anchored on the stable numeric ``repo.id`` — never
``owner/repo`` or branch name — so renames/transfers never orphan a checkpoint
or a record's identity. Source timestamps live in ``timestamps.py``.
"""

from __future__ import annotations

import base64
import uuid
from collections import deque
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from app.sources.external.github.github_async import GhObject

from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
    get_mime_type_for_extension,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.sync_point.sync_point import generate_record_sync_point_key
from app.connectors.core.registry.filters import IndexingFilterKey
from app.models.entities import CodeFileRecord, FileRecord, Record, RecordGroupType, RecordType

from .constants import (
    CODE_FILE_MAX_SIZE_BYTES,
    COMPARE_COMMITS_FILES_LIMIT,
    COMPARE_COMMITS_TOTAL_LIMIT,
    GIT_TREE_TRUNCATION_ENTRY_HINT,
    PREVIEW_RENDERABLE_EXTENSIONS,
    REPO_DELETE_VALVE_MAX_FRACTION,
    REPO_DELETE_VALVE_MIN_ABSOLUTE,
)
from .models import GitHubLiterals, blob_external_id, path_from_external_id, tree_external_id
from .timestamps import TimestampBackfill

if TYPE_CHECKING:
    from app.connectors.sources.github_teams.connector import GitHubTeamsConnector


def _git_tree_entries(prefix: str, tree: Any) -> list[tuple[str, str, str, int | None]]:
    """``(full_path, type, sha, size)`` for one Git Tree response, rebased on ``prefix``.

    A recursive response already carries paths relative to the tree it was
    fetched from, so prefixing is all that is needed to make them repo-relative.
    """
    entries: list[tuple[str, str, str, int | None]] = []
    for el in tree.tree or []:
        if not el.path:
            continue
        full_path = f"{prefix}/{el.path}" if prefix else el.path
        entries.append((full_path, el.type, el.sha, getattr(el, "size", None)))
    return entries


class ReposSync:
    """Handles code repository (file/blob) synchronisation for ``GitHubTeamsConnector``."""

    def __init__(self, connector: "GitHubTeamsConnector") -> None:
        self.c = connector
        self.logger = connector.logger
        self.timestamps = TimestampBackfill(connector)

    # ------------------------------------------------------------------
    # 1. Entry point: dispatch + checkpoints
    # ------------------------------------------------------------------

    async def run(self, repo: GhObject) -> None:
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

    def _checkpoint_key(self, repo_id: int) -> str:
        return generate_record_sync_point_key(
            Connectors.GITHUB_TEAMS.value, f"{repo_id}-code-repository", ""
        )

    async def _get_checkpoint(self, repo_id: int) -> dict[str, Any] | None:
        try:
            return await self.c.record_sync_point.read_sync_point(self._checkpoint_key(repo_id))
        except Exception:
            return None

    async def _update_checkpoint(self, repo: GhObject, commit_sha: str, default_branch: str) -> None:
        await self.c.record_sync_point.update_sync_point(
            self._checkpoint_key(repo.id),
            {
                GitHubLiterals.LAST_COMMIT_SHA.value: commit_sha,
                GitHubLiterals.DEFAULT_BRANCH.value: default_branch,
                GitHubLiterals.FULL_NAME.value: repo.full_name,
            },
        )

    # ------------------------------------------------------------------
    # 2. Full sync: tree walk -> persist -> prune
    # ------------------------------------------------------------------

    async def _full_sync(self, repo: GhObject, head_sha: str) -> bool:
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
                "Git tree truncated for %s (>%s entries or >7MB); falling back to per-subtree walk",
                repo.full_name,
                f"{GIT_TREE_TRUNCATION_ENTRY_HINT:,}",
            )
            return await self._full_sync_untruncated(repo, head_sha)

        entries = [
            (el.path, el.type, el.sha, getattr(el, "size", None))
            for el in (tree.tree or [])
            if el.path
        ]
        persisted_ok, folders, blobs = await self._persist_tree_entries(repo, entries)
        self.logger.info(
            "Full code sync for %s: %s folder(s), %s file(s) persisted in 1 tree call",
            repo.full_name, folders, blobs,
        )
        if persisted_ok:
            await self._prune_deleted_paths(repo, {path for path, *_ in entries})
        return persisted_ok

    async def _full_sync_untruncated(self, repo: GhObject, head_sha: str) -> bool:
        """Subtree walk for a repo whose recursive Git Tree came back truncated.

        Each queued node is fetched *recursively*, so one call normally covers a
        whole subtree; only a node that itself comes back truncated is split into
        its immediate children and re-queued. A repo with a few dozen top-level
        directories therefore costs tens of calls rather than one per directory,
        and the recursion only deepens where the API limit is really hit.

        Entries are persisted per node as the walk proceeds rather than
        accumulated and written at the end — the same streaming shape the GitLab
        connector uses. Memory stays flat and records land continuously, so a
        walk that dies at 90% keeps the 90%. Out-of-order arrival is safe: a blob
        whose parent folder has not been written yet gets a placeholder parent
        from the data-entities processor, upgraded in place when the real folder
        record arrives.
        """
        c = self.c
        owner, name = repo.owner.login, repo.name
        all_ok = True
        walked_paths: set[str] = set()
        folders_walked = blobs_walked = tree_calls = 0

        # The root's recursive fetch already returned truncated — that is why
        # this path is running — so it is seeded as pre-split instead of being
        # re-fetched recursively just to be told the same thing again.
        queue: deque[tuple[str, str, bool]] = deque([("", head_sha, True)])
        while queue:
            prefix, sha, known_truncated = queue.popleft()
            entries: list[tuple[str, str, str, int | None]] | None = None
            children: list[tuple[str, str]] = []

            if not known_truncated:
                tree_calls += 1
                res = await c.runtime.ds_call(c.data_source.get_git_tree, owner, name, sha, True)
                if not res.success or res.data is None:
                    self.logger.warning(
                        "Recursive subtree fetch failed for %s at %r (sha=%s): %s",
                        repo.full_name, prefix, sha, res.error,
                    )
                    all_ok = False
                    continue
                if not getattr(res.data, "truncated", False):
                    entries = _git_tree_entries(prefix, res.data)

            if entries is None:
                # Subtree too large for one recursive call: take only this level
                # and walk each child directory on its own.
                tree_calls += 1
                res = await c.runtime.ds_call(c.data_source.get_git_tree, owner, name, sha, False)
                if not res.success or res.data is None:
                    self.logger.warning(
                        "Subtree fetch failed for %s at %r (sha=%s): %s",
                        repo.full_name, prefix, sha, res.error,
                    )
                    all_ok = False
                    continue
                if getattr(res.data, "truncated", False):
                    # One directory with more direct children than the API will
                    # return: there is no smaller unit left to split into, so the
                    # walk is knowingly incomplete. Fail the sync so pruning is
                    # skipped and the checkpoint withheld — silently accepting it
                    # would let the next prune delete files that still exist.
                    self.logger.error(
                        "Directory %r in %s exceeds the Git Tree limit even without "
                        "recursion; it cannot be split further, so this walk is "
                        "incomplete. Not pruning and not advancing the checkpoint.",
                        prefix or "<root>", repo.full_name,
                    )
                    all_ok = False
                    continue
                entries = _git_tree_entries(prefix, res.data)
                children = [(p, sha_) for p, t, sha_, _sz in entries if t == "tree"]

            if entries:
                ok, n_folders, n_blobs = await self._persist_tree_entries(repo, entries)
                all_ok = ok and all_ok
                folders_walked += n_folders
                blobs_walked += n_blobs
                walked_paths.update(path for path, *_ in entries)
                # One line per subtree rather than a fixed cadence: a subtree is
                # a whole recursive call, so the count is proportional to real
                # work (tens of lines, not thousands) and a stall is visible at
                # the path it stalled on.
            # Enqueue before logging so the reported queue depth includes the
            # children this node just produced.
            for child_path, child_sha in children:
                queue.append((child_path, child_sha, False))

            if entries:
                self.logger.info(
                    "Subtree walk %s: %s -> %s folder(s), %s file(s) "
                    "[total %s folder(s), %s file(s); %s subtree(s) queued]",
                    repo.full_name, prefix or "<root>", n_folders, n_blobs,
                    folders_walked, blobs_walked, len(queue),
                )

        self.logger.info(
            "Full code sync for %s: %s folder(s), %s file(s) persisted in %s tree call(s)%s",
            repo.full_name, folders_walked, blobs_walked, tree_calls,
            "" if all_ok else " (INCOMPLETE - checkpoint withheld)",
        )
        # Deliberately no pruning after a partial walk: anything "missing" may
        # simply not have been walked.
        if all_ok:
            await self._prune_deleted_paths(repo, walked_paths)
        return all_ok

    async def _persist_tree_entries(
        self, repo: GhObject, entries: list[tuple[str, str, str, int | None]]
    ) -> tuple[bool, int, int]:
        """Persist one batch of ``(path, type, sha, size)`` Git Tree entries.

        Folders go first, shallowest level first, so a parent is written before
        its children; blobs follow in processor-sized batches. Returns
        ``(ok, folders_persisted, blobs_persisted)`` — the caller owns the
        summary log, because the truncated path calls this once per subtree.
        """
        c = self.c
        folders = [(p, s) for p, t, s, _sz in entries if t == "tree"]
        blobs = [(p, s, sz) for p, t, s, sz in entries if t == "blob"]

        code_files_enabled = self._code_files_indexing_enabled()

        all_ok = True
        level_wise: dict[int, list[tuple[str, str]]] = {}
        for path, sha in folders:
            level_wise.setdefault(path.count("/"), []).append((path, sha))
        for _level, level_folders in sorted(level_wise.items()):
            records = [
                self._build_folder_record(repo, path, sha, code_files_enabled)
                for path, sha in level_folders
            ]
            all_ok = await self._process_records(records) and all_ok

        batch: list[Record] = []
        for path, sha, size in blobs:
            batch.append(self._build_code_file_record(repo, path, sha, code_files_enabled, size=size))
            if len(batch) >= c.batch_size * 4:
                all_ok = await self._process_records(batch) and all_ok
                batch = []
        if batch:
            all_ok = await self._process_records(batch) and all_ok

        return all_ok, len(folders), len(blobs)

    async def _prune_deleted_paths(self, repo: GhObject, walked_paths: set[str]) -> None:
        """Delete code records whose path is absent from a complete tree walk.

        Full sync is the recovery path for a diverged history, a Compare Commits
        overflow, and a default-branch change — exactly the cases where the
        incremental path never sees the deletions. Without this, upserts alone
        leave those records permanently.

        Only ever called with a complete, successfully-persisted walk; the same
        fraction valve as repo deletion guards against a truncated one.
        """
        c = self.c
        external_group_id = f"{repo.id}-code-repository"
        try:
            existing = await self._list_code_records_by_path(external_group_id)
        except Exception as e:
            self.logger.error("Could not list code records for pruning in %s: %s", repo.full_name, e, exc_info=True)
            return

        stale = {path: rec_id for path, rec_id in existing.items() if path not in walked_paths}
        if not stale:
            return
        if (
            len(stale) > REPO_DELETE_VALVE_MIN_ABSOLUTE
            and existing
            and len(stale) / len(existing) >= REPO_DELETE_VALVE_MAX_FRACTION
        ):
            self.logger.error(
                "Refusing to prune %s of %s code records (%.0f%%) in %s on a single walk — "
                "this looks like a truncated tree rather than real deletions.",
                len(stale), len(existing), len(stale) / len(existing) * 100, repo.full_name,
            )
            return

        self.logger.info("Pruning %s deleted code record(s) from %s", len(stale), repo.full_name)
        # Deepest-first so a stale folder is deleted only after its stale
        # children — the same bottom-up order _cleanup_emptied_folders uses.
        ordered_ids = [stale[p] for p in sorted(stale, key=lambda p: p.count("/"), reverse=True)]
        try:
            await c.data_entities_processor.on_records_deleted_cascade(ordered_ids, c.connector_id)
        except Exception as e:
            self.logger.error("Failed to prune deleted code records in %s: %s", repo.full_name, e, exc_info=True)

    async def _list_code_records_by_path(self, external_group_id: str) -> dict[str, str]:
        """``{repo_path: record_id}`` for every record under a code record group.

        Folders are ``FileRecord``s with no ``file_path`` attribute, so their
        path is recovered from the external id instead — keying on ``file_path``
        alone would leave pruning blind to folders.
        """
        c = self.c
        repo_id = int(external_group_id.split("-")[0])
        by_path: dict[str, str] = {}
        async with c.data_store_provider.transaction() as tx_store:
            rg = await tx_store.get_record_group_by_external_id(
                connector_id=c.connector_id, external_id=external_group_id,
            )
            if not rg:
                return by_path
            offset = 0
            page_size = 500
            while True:
                page = await tx_store.get_records_by_status(
                    org_id=c.data_entities_processor.org_id,
                    connector_id=c.connector_id,
                    status_filters=None,
                    limit=page_size,
                    offset=offset,
                    record_group_id=rg.id,
                )
                if not page:
                    break
                for rec in page:
                    path = getattr(rec, "file_path", None) or path_from_external_id(
                        repo_id, getattr(rec, "external_record_id", None) or ""
                    )
                    if path:
                        by_path[path] = rec.id
                if len(page) < page_size:
                    break
                offset += page_size
        return by_path

    # ------------------------------------------------------------------
    # 3. Incremental sync: compare -> classify -> reconcile -> apply
    # ------------------------------------------------------------------

    async def _incremental_sync(self, repo: GhObject, from_sha: str, to_sha: str) -> bool:
        """Compare-commits-based incremental sync. Returns False (triggering a
        full-sync fallback) on any untrusted-delta condition or API failure."""
        c = self.c
        owner, name = repo.owner.login, repo.name
        cmp_res = await c.runtime.ds_call(c.data_source.compare_commits, owner, name, from_sha, to_sha)
        if not cmp_res.success or cmp_res.data is None:
            self.logger.warning("compare_commits failed for %s: %s", repo.full_name, cmp_res.error)
            return False

        comparison = cmp_res.data
        status = getattr(comparison, "status", None)
        # Only "ahead" carries a trustworthy delta. "diverged" is a force-push;
        # "behind" is a branch reset to an ancestor, where compare returns an
        # EMPTY files list — applying it would "succeed", advance the checkpoint,
        # and leave every rolled-back file as a permanent record. Both need the
        # full-sync re-baseline, whose pruning removes what the delta never saw.
        # ("identical" can't reach here: equal SHAs short-circuit in run().)
        if status and status != "ahead":
            self.logger.warning(
                "compare_commits returned status=%s for %s (force-push/branch reset); falling back to full sync",
                status, repo.full_name,
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

        # Order is load-bearing: deletes before upserts (file->folder swaps),
        # folder cleanup last (folder->file swaps).
        all_ok = True
        if deletes:
            await self._delete_code_files_by_paths(repo, list(deletes.keys()))
        if renames:
            all_ok = await self._apply_code_renames(repo, renames) and all_ok
        upserts: dict[str, str] = {**adds, **modifies}
        if upserts:
            all_ok = await self._upsert_code_files(repo, upserts) and all_ok

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
        an add (new independent identity).
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
                if filename:
                    deletes[filename] = sha
                continue
            if status == "renamed":
                old_path = previous_filename or filename
                new_path = filename or previous_filename
                if not old_path or not new_path:
                    continue
                renames.append((old_path, new_path, sha))
                continue

            target = filename or previous_filename
            if not target:
                continue
            if status == "added" or status == "copied":
                adds[target] = sha
            else:  # "modified" | "changed"
                modifies[target] = sha

        return deletes, adds, modifies, renames

    async def _reconcile_sha_moves(
        self, repo: GhObject, deletes: dict[str, str], adds: dict[str, str]
    ) -> tuple[dict[str, str], dict[str, str], list[tuple[str, str, str]]]:
        """Promote delete+add pairs whose blob SHA matches into renames
        (squash-merge/rebase reports a rename as delete+add).

        The deleted path's "before" SHA comes from our own DB (the previously
        indexed record's ``external_revision_id`` / ``file_hash``) — Compare's
        ``removed`` entries do not reliably carry a usable SHA — while the added
        path's SHA comes directly off the compare response, so no extra API
        call is needed.
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
            old_external_id = blob_external_id(repo.id, old_path)
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

    async def _delete_code_files_by_paths(self, repo: GhObject, paths: list[str]) -> None:
        c = self.c
        for path in paths:
            external_id = blob_external_id(repo.id, path)
            record = await c.data_entities_processor.get_record_by_external_id(c.connector_id, external_id)
            if record:
                await c.data_entities_processor.on_record_deleted(record.id)

    async def _apply_code_renames(self, repo: GhObject, renames: list[tuple[str, str, str]]) -> bool:
        """Apply in-place renames via ``on_records_moved`` — reuses the existing
        DB vertex, so permission/parent edges survive and no reindex fires
        unless the blob SHA also changed."""
        if not renames:
            return True
        c = self.c
        new_paths = [new_p for _old, new_p, _sha in renames]
        folders_ok = await self._ensure_folder_records_for_paths(repo, new_paths)

        code_files_enabled = self._code_files_indexing_enabled()
        moves = [
            (blob_external_id(repo.id, old_path),
             self._build_code_file_record(repo, new_path, new_sha, code_files_enabled),
             [])
            for old_path, new_path, new_sha in renames
        ]
        try:
            await c.data_entities_processor.on_records_moved(moves)
        except Exception as e:
            # Caught here (rather than left to propagate) so a rename failure
            # is reported as all_ok=False, letting `run` fall back to a full
            # sync this cycle instead of raising past the checkpoint decision.
            self.logger.error("Failed to apply GitHub code renames for %s: %s", repo.full_name, e, exc_info=True)
            return False
        return folders_ok

    async def _upsert_code_files(self, repo: GhObject, path_to_sha: dict[str, str]) -> bool:
        """Upsert (add/modify) code file records for the given repo-relative paths."""
        if not path_to_sha:
            return True
        folders_ok = await self._ensure_folder_records_for_paths(repo, list(path_to_sha.keys()))
        code_files_enabled = self._code_files_indexing_enabled()
        # Exact per-file dates at sync time (~2 GraphQL queries per 100 files)
        # — the ONLY way a modified file's source_updated stays fresh: the
        # processor carries stored dates forward when the incoming record has
        # None, and the backfill's missing-only query never revisits an
        # already-dated record.
        dates = await self.timestamps.fetch_commit_dates(
            repo.owner.login, repo.name, list(path_to_sha.keys())
        )
        records: list[Record] = []
        for path, sha in path_to_sha.items():
            record = self._build_code_file_record(repo, path, sha, code_files_enabled)
            created_ms, updated_ms = dates.get(path, (None, None))
            if created_ms is not None or updated_ms is not None:
                record.source_created_at = created_ms
                record.source_updated_at = updated_ms
            records.append(record)
        return await self._process_records(records) and folders_ok

    # ------------------------------------------------------------------
    # 4. Folder lifecycle
    # ------------------------------------------------------------------

    async def _ensure_folder_records_for_paths(self, repo: GhObject, file_paths: list[str]) -> bool:
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
            return True

        code_files_enabled = self._code_files_indexing_enabled()
        records: list[Record] = []
        for prefix in sorted(prefixes, key=lambda p: p.count("/")):
            external_id = tree_external_id(repo.id, prefix)
            existing = await c.data_entities_processor.get_record_by_external_id(c.connector_id, external_id)
            if existing:
                continue
            records.append(self._build_folder_record(repo, prefix, sha=None, code_files_enabled=code_files_enabled))

        if records:
            return await self._process_records(records)
        return True

    async def _cleanup_emptied_folders(self, repo: GhObject, removed_paths: list[str]) -> None:
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
            external_id = tree_external_id(repo.id, dir_path)
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
    # 5. Record builders + persistence
    # ------------------------------------------------------------------

    def _build_folder_record(
        self, repo: GhObject, path: str, sha: str | None, code_files_enabled: bool
    ) -> FileRecord:
        """A folder record, stamped with the same indexing flag as the code files
        it contains — folders hold no content, so publishing indexing events for
        them when files are AUTO_INDEX_OFF would be pure waste."""
        c = self.c
        name = path.rsplit("/", 1)[-1]
        parent_path = path.rpartition("/")[0] if "/" in path else None
        external_id = tree_external_id(repo.id, path)
        parent_external_id = tree_external_id(repo.id, parent_path) if parent_path else None
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
        if not code_files_enabled:
            record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
        return record

    def _build_code_file_record(
        self,
        repo: GhObject,
        path: str,
        sha: str | None,
        code_files_enabled: bool,
        size: int | None = None,
    ) -> CodeFileRecord:
        """A code file record. Every file gets one — oversized files (size only
        known on the full-sync path; Compare Commits carries no blob size) keep
        theirs with content indexing switched off and a reason, so the file
        stays visible and name-searchable instead of silently not existing.
        The incremental path is covered by the 413 guard in
        ``fetch_code_file_content``."""
        c = self.c
        name = path.rsplit("/", 1)[-1]
        extension = name.rsplit(".", 1)[-1] if "." in name else ""
        mime_type = get_mime_type_for_extension(extension, fallback=MimeTypes.PLAIN_TEXT.value)
        external_id = blob_external_id(repo.id, path)
        parent_path = path.rpartition("/")[0] if "/" in path else None
        parent_external_id = tree_external_id(repo.id, parent_path) if parent_path else None
        record = CodeFileRecord(
            id=str(uuid.uuid4()), org_id=c.data_entities_processor.org_id, record_name=name,
            record_type=RecordType.CODE_FILE.value, connector_name=c.connector_name, connector_id=c.connector_id,
            external_record_id=external_id, version=0, origin=OriginTypes.CONNECTOR.value,
            record_group_type=RecordGroupType.PROJECT.value,
            external_record_group_id=f"{repo.id}-code-repository",
            mime_type=mime_type, external_revision_id=str(sha) if sha else "",
            # None, not "", for extensionless names (LICENSE, Dockerfile).
            extension=extension.lower() or None,
            preview_renderable=extension.lower() in PREVIEW_RENDERABLE_EXTENSIONS if extension else True,
            file_path=path, file_hash=sha,
            inherit_permissions=True, parent_external_record_id=parent_external_id,
            parent_record_type=(RecordType.FILE if parent_external_id else None),
            weburl=f"{repo.html_url}/blob/{repo.default_branch}/{path}",
        )
        if not code_files_enabled:
            record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
        if size is not None and size > CODE_FILE_MAX_SIZE_BYTES:
            record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
            record.reason = (
                f"File is {size} bytes, above the {CODE_FILE_MAX_SIZE_BYTES}-byte "
                "content-indexing limit; metadata synced, content not indexed"
            )
        return record

    async def _process_records(self, records: list[Record]) -> bool:
        """Persist a batch via the data-entities processor (which owns
        new-vs-update, versioning, and reindex decisions); returns False on
        failure so callers can withhold checkpoint advancement instead of
        silently losing the batch."""
        if not records:
            return True
        try:
            await self.c.data_entities_processor.on_new_records([(r, []) for r in records])
            return True
        except Exception as e:
            self.logger.error("Error persisting GitHub code repo records: %s", e, exc_info=True)
            return False

    def _code_files_indexing_enabled(self) -> bool:
        c = self.c
        if not c.indexing_filters:
            return True
        return c.indexing_filters.is_enabled(IndexingFilterKey.CODE_FILES)

    # ------------------------------------------------------------------
    # 6. Content streaming (index time)
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
        # Incrementally-added/modified files bypass the full-sync size stamp
        # (Compare Commits entries carry no blob size), so this is the only
        # remaining checkpoint before an oversized file's content would be
        # indexed. 413 is classified TERMINAL by the indexing consumer, so the
        # record fails once with a clear reason instead of a retry storm — the
        # record itself (name, path, weburl) stays synced and searchable.
        blob_size = getattr(content_file, "size", None)
        if blob_size is not None and blob_size > CODE_FILE_MAX_SIZE_BYTES:
            raise HTTPException(
                status_code=HttpStatusCode.PAYLOAD_TOO_LARGE.value,
                detail=(
                    f"File {file_path!r} in {repo.full_name} is {blob_size} bytes, above the "
                    f"{CODE_FILE_MAX_SIZE_BYTES}-byte content-indexing limit; "
                    "metadata synced, content not indexed"
                ),
            )
        try:
            decoded = content_file.decoded_content
            if decoded:
                return decoded
        except Exception as e:
            self.logger.warning(
                "Could not decode Contents API payload for %s in %s: %s", file_path, repo.full_name, e,
            )

        raw = getattr(content_file, "content", None)
        if raw:
            return base64.b64decode(raw)

        # The Contents API returns empty content for blobs over 1 MB while the
        # guard above admits up to CODE_FILE_MAX_SIZE_BYTES, so every 1-5 MB
        # file lands here. The Git Data API serves it by sha.
        if blob_size:
            return await self._fetch_blob_by_sha(repo, record, file_path, blob_size)
        return b""

    async def _fetch_blob_by_sha(
        self, repo: Any, record: CodeFileRecord, file_path: str, blob_size: int,
    ) -> bytes:
        c = self.c
        blob_sha = getattr(record, "file_hash", None)
        if not blob_sha:
            raise Exception(
                f"Contents API returned no content for {file_path!r} ({blob_size} bytes) in "
                f"{repo.full_name} and the record carries no blob sha to fall back on"
            )
        blob_res = await c.runtime.ds_call(c.data_source.get_git_blob, repo.owner.login, repo.name, blob_sha)
        if not blob_res.success or blob_res.data is None:
            raise Exception(
                f"Failed to fetch blob {blob_sha} for {file_path!r} in {repo.full_name}: {blob_res.error}"
            )
        blob_content = getattr(blob_res.data, "content", None)
        if not blob_content:
            raise Exception(f"Blob {blob_sha} for {file_path!r} in {repo.full_name} returned no content")
        if getattr(blob_res.data, "encoding", "base64") != "base64":
            return blob_content.encode(GitHubLiterals.UTF_8.value)
        return base64.b64decode(blob_content)
