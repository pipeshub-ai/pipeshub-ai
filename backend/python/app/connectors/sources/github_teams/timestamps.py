"""
Source-timestamp backfill for GitHub code files and folders.

Git stores no per-file dates — a file's created/modified times exist only in
commit history. Fetching them is batched over GraphQL (~2 queries per 100
files, against GraphQL's own point budget), but even that is left out of the
sync's critical path:

- Incremental syncs stamp exact dates at sync time (deltas are small); that
  happens in ``repos.py`` using :meth:`TimestampBackfill.fetch_commit_dates`.
- The full-sync backlog is filled here, in a background task scheduled after
  each sync. It is naturally resumable: only records still missing both dates
  are queried, so cancellation (a new sync starting) just pauses progress.
- Folders get no API calls at all — git has no folder dates, so a folder's
  dates are derived from its files: created = earliest child created,
  updated = latest child updated.

Hard-won rule (three production bugs live in this file's history): NEVER
rebuild a record from a bare graph node. The node carries only base-record
fields; a record fabricated via ``from_arango_record({}, node)`` silently
loses type-document fields (``file_path`` -> the file pass no-ops,
``is_file`` -> folders flip into files on re-upsert). This module therefore
never builds record objects at all: it patches only the named properties via
``tx_store.batch_update_nodes`` (a partial merge on both providers), so
nothing outside the patch can be touched.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.sources.external.github.github_async import GhObject

from app.config.constants.arangodb import CollectionNames
from app.models.entities import RecordType
from app.utils.time_conversion import get_epoch_timestamp_in_ms

from .models import path_from_external_id

if TYPE_CHECKING:
    from app.connectors.sources.github_teams.connector import GitHubTeamsConnector

_BATCH_SIZE = 100
_FETCH_CONCURRENCY = 10
_GRAPHQL_ALIAS_BATCH = 100


class TimestampBackfill:
    """Post-sync backfill of ``source_created_at``/``source_updated_at``."""

    def __init__(self, connector: "GitHubTeamsConnector") -> None:
        self.c = connector
        self.logger = connector.logger
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Task lifecycle (called from the connector's run_sync)
    # ------------------------------------------------------------------

    async def cancel(self) -> None:
        """Stop the in-flight backfill task before starting a new sync."""
        task, self._task = self._task, None
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def schedule(self) -> None:
        """Schedule the backfill in the background after a sync completes."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._run(),
            name=f"github_code_file_ts_backfill_{self.c.connector_id}",
        )

    async def _run(self) -> None:
        c = self.c
        try:
            await c.runtime.refresh_token_if_needed()
            repos = await c.projects._resolve_repos_with_filters()
            for repo in repos:
                await self.backfill_repo(repo)
        except Exception as e:
            self.logger.error("Code file timestamp backfill failed for connector %s: %s", c.connector_id, e, exc_info=True)
        finally:
            self._task = None

    # ------------------------------------------------------------------
    # Per-repo passes
    # ------------------------------------------------------------------

    async def backfill_repo(self, repo: GhObject) -> None:
        """File pass (commit history) then folder pass (derived from files)."""
        try:
            external_group_id = f"{repo.id}-code-repository"
            await self._backfill_file_timestamps(repo, external_group_id)
            await self._backfill_folder_timestamps(repo, external_group_id)
        except Exception as e:
            self.logger.error("Code file timestamp backfill failed for repo %s: %s", repo.full_name, e, exc_info=True)

    async def _backfill_file_timestamps(self, repo: GhObject, external_group_id: str) -> None:
        """Fill dates for code files still missing them, from commit history."""
        c = self.c
        nodes = await self._query_group_nodes(
            external_group_id, RecordType.CODE_FILE.value, missing_dates_only=True,
        )
        # Paths come from the external id: the graph node has no type-doc
        # fields, so record.file_path is unavailable here (see module docstring).
        path_to_record_ids: dict[str, list[str]] = {}
        blob_prefix = f"/{repo.id}/blob/"
        for node in nodes:
            external_id = node.get("externalRecordId") or ""
            record_id = node.get("id") or node.get("_key")
            if not record_id or not external_id.startswith(blob_prefix):
                continue
            path = path_from_external_id(repo.id, external_id)
            if path:
                path_to_record_ids.setdefault(path, []).append(record_id)

        file_paths = list(path_to_record_ids.keys())
        owner, name = repo.owner.login, repo.name
        for offset in range(0, len(file_paths), _BATCH_SIZE):
            await c.runtime.refresh_token_if_needed()
            batch_paths = file_paths[offset:offset + _BATCH_SIZE]
            dates_by_path = await self.fetch_commit_dates(owner, name, batch_paths)
            patches = []
            for file_path in batch_paths:
                created_ms, updated_ms = dates_by_path.get(file_path, (None, None))
                if created_ms is None and updated_ms is None:
                    continue
                for record_id in path_to_record_ids[file_path]:
                    patches.append(_date_patch(record_id, created_ms, updated_ms))
            await self._apply_patches(patches, CollectionNames.RECORDS.value, context=repo.full_name)

    async def _backfill_folder_timestamps(self, repo: GhObject, external_group_id: str) -> None:
        """Derive folder dates from their children — zero API calls.

        Recomputes EVERY folder each pass: while the file backlog is still
        filling, aggregates are partial, and a folder stamped early must
        converge to the true min/max as more of its files gain dates. Folders
        whose stored values already match are skipped. ``isFile=False`` is
        re-asserted on every folder so any folder corrupted by the old
        fabricate-from-node code self-heals (see module docstring).
        """
        file_nodes = await self._query_group_nodes(external_group_id, RecordType.CODE_FILE.value)
        folder_nodes = await self._query_group_nodes(external_group_id, RecordType.FILE.value)

        file_dates = {
            path: (node.get("sourceCreatedAtTimestamp"), node.get("sourceLastModifiedTimestamp"))
            for node in file_nodes
            if (path := path_from_external_id(repo.id, node.get("externalRecordId") or ""))
        }
        folder_dates = aggregate_folder_timestamps(file_dates)

        tree_prefix = f"/{repo.id}/tree/"
        date_patches: list[dict[str, Any]] = []
        repair_patches: list[dict[str, Any]] = []
        for node in folder_nodes:
            external_id = node.get("externalRecordId") or ""
            record_id = node.get("id") or node.get("_key")
            if not record_id or not external_id.startswith(tree_prefix):
                continue
            repair_patches.append({"id": record_id, "isFile": False})
            folder_path = path_from_external_id(repo.id, external_id)
            dates = folder_dates.get(folder_path) if folder_path else None
            if dates is None:
                continue
            created_ms, updated_ms = dates
            needs_update = (
                (created_ms is not None and node.get("sourceCreatedAtTimestamp") != created_ms)
                or (updated_ms is not None and node.get("sourceLastModifiedTimestamp") != updated_ms)
            )
            if needs_update:
                date_patches.append(_date_patch(record_id, created_ms, updated_ms))

        await self._apply_patches(repair_patches, CollectionNames.FILES.value, context=repo.full_name)
        await self._apply_patches(date_patches, CollectionNames.RECORDS.value, context=repo.full_name)
        if date_patches:
            self.logger.info(
                "Folder timestamp backfill updated %s folder(s) for %s", len(date_patches), repo.full_name,
            )

    # ------------------------------------------------------------------
    # Shared building blocks
    # ------------------------------------------------------------------

    async def _query_group_nodes(
        self, external_group_id: str, record_type: str, *, missing_dates_only: bool = False
    ) -> list[dict]:
        """Non-deleted record nodes of one type in the code record group."""
        filters: dict[str, Any] = {
            "connectorId": self.c.connector_id,
            "recordType": record_type,
            "externalGroupId": external_group_id,
        }
        if missing_dates_only:
            filters["sourceCreatedAtTimestamp"] = None
            filters["sourceLastModifiedTimestamp"] = None
        async with self.c.data_store_provider.transaction() as tx_store:
            nodes = await tx_store.get_nodes_by_filters(
                collection=CollectionNames.RECORDS.value, filters=filters,
            )
        return [n for n in nodes if not n.get("isDeleted")]

    async def _apply_patches(self, patches: list[dict[str, Any]], collection: str, *, context: str) -> None:
        """Write property patches via ``batch_update_nodes`` (partial merge,
        never creates) — only the named keys can change, so nothing on the
        stored record is at risk."""
        if not patches:
            return
        try:
            async with self.c.data_store_provider.transaction() as tx_store:
                await tx_store.batch_update_nodes(patches, collection)
        except Exception as e:
            self.logger.warning(
                "Failed to patch %s node(s) in %s for %s: %s", len(patches), collection, context, e,
            )

    async def fetch_commit_dates(
        self, owner: str, name: str, paths: list[str]
    ) -> dict[str, tuple[int | None, int | None]]:
        """``{path: (created_ms, updated_ms)}`` from each path's first/last commit.

        Batched over GraphQL: ~2 queries per 100 paths (against GraphQL's own
        point budget, not the REST core quota) instead of 2 REST requests per
        path. Falls back to the per-path REST method for any chunk where
        GraphQL fails. Also used by the incremental sync to stamp exact dates
        on changed files at sync time.
        """
        results: dict[str, tuple[int | None, int | None]] = {}
        for offset in range(0, len(paths), _GRAPHQL_ALIAS_BATCH):
            chunk = paths[offset:offset + _GRAPHQL_ALIAS_BATCH]
            chunk_dates = await self._fetch_dates_graphql(owner, name, chunk)
            if chunk_dates is None:
                chunk_dates = await self._fetch_dates_rest(owner, name, chunk)
            results.update(chunk_dates)
        return results

    async def _fetch_dates_graphql(
        self, owner: str, name: str, paths: list[str]
    ) -> dict[str, tuple[int | None, int | None]] | None:
        """Batched dates for up to ``_GRAPHQL_ALIAS_BATCH`` paths; None = fall back.

        Pass 1: one aliased query per chunk — each path's newest commit date,
        ``totalCount`` and a history cursor. Single-commit paths finish here
        (created == updated). Pass 2: commit history only paginates forward,
        but its cursors are ``"<anchor-oid> <offset>"``, so the oldest commit
        is one more query away via a constructed cursor — no page walking.
        """
        c = self.c
        variables = {"owner": owner, "name": name}

        fields = " ".join(
            f"p{i}: history(first: 1, path: {json.dumps(path)}) "
            "{ totalCount nodes { authoredDate } pageInfo { endCursor } }"
            for i, path in enumerate(paths)
        )
        res = await c.runtime.ds_call(c.data_source.graphql_query, _history_query(fields), variables)
        target = _commit_target(res)
        if target is None:
            self.logger.warning(
                "GraphQL commit-date batch failed for %s/%s (%s); falling back to REST.",
                owner, name, getattr(res, "error", "no data"),
            )
            return None

        results: dict[str, tuple[int | None, int | None]] = {}
        pending_oldest: list[tuple[int, str, str]] = []
        for i, path in enumerate(paths):
            history = target.get(f"p{i}") or {}
            nodes = history.get("nodes") or []
            total = history.get("totalCount") or 0
            newest_ms = _iso_to_ms(nodes[0].get("authoredDate")) if nodes else None
            if not total or newest_ms is None:
                results[path] = (None, None)
                continue
            if total == 1:
                results[path] = (newest_ms, newest_ms)
                continue
            results[path] = (None, newest_ms)
            end_cursor = (history.get("pageInfo") or {}).get("endCursor") or ""
            anchor_oid = end_cursor.split(" ")[0]
            if anchor_oid:
                pending_oldest.append((i, path, f"{anchor_oid} {total - 2}"))

        if not pending_oldest:
            return results

        fields = " ".join(
            f"p{i}: history(first: 1, path: {json.dumps(path)}, after: {json.dumps(cursor)}) "
            "{ nodes { authoredDate } }"
            for i, path, cursor in pending_oldest
        )
        res = await c.runtime.ds_call(c.data_source.graphql_query, _history_query(fields), variables)
        target = _commit_target(res) or {}
        for i, path, _cursor in pending_oldest:
            nodes = (target.get(f"p{i}") or {}).get("nodes") or []
            oldest_ms = _iso_to_ms(nodes[0].get("authoredDate")) if nodes else None
            _created, updated_ms = results[path]
            # A created date should never be missing when the modified date is
            # known — the newest commit bounds it, so fall back to that.
            results[path] = (oldest_ms if oldest_ms is not None else updated_ms, updated_ms)
        return results

    async def _fetch_dates_rest(
        self, owner: str, name: str, paths: list[str]
    ) -> dict[str, tuple[int | None, int | None]]:
        """Per-path REST fallback: 2 requests per path against the core quota."""
        c = self.c
        semaphore = asyncio.Semaphore(_FETCH_CONCURRENCY)
        results: dict[str, tuple[int | None, int | None]] = {}

        async def fetch_one(path: str) -> None:
            async with semaphore:
                try:
                    res = await c.runtime.ds_call(
                        c.data_source.list_commits_first_and_last, owner, name, path,
                    )
                    if not res.success or not res.data:
                        results[path] = (None, None)
                        return
                    newest_commit, oldest_commit = res.data
                    if newest_commit is None:
                        results[path] = (None, None)
                        return
                    results[path] = (_commit_authored_ms(oldest_commit), _commit_authored_ms(newest_commit))
                except Exception as e:
                    self.logger.debug("Failed to fetch commit history for %s: %s", path, e)
                    results[path] = (None, None)

        await asyncio.gather(*(fetch_one(path) for path in paths))
        return results


def _date_patch(record_id: str, created_ms: int | None, updated_ms: int | None) -> dict[str, Any]:
    """A None value would erase the stored property (Neo4j ``SET n += null``
    deletes it), so absent dates are omitted rather than written."""
    patch: dict[str, Any] = {"id": record_id, "updatedAtTimestamp": get_epoch_timestamp_in_ms()}
    if created_ms is not None:
        patch["sourceCreatedAtTimestamp"] = created_ms
    if updated_ms is not None:
        patch["sourceLastModifiedTimestamp"] = updated_ms
    return patch


def aggregate_folder_timestamps(
    file_timestamps: dict[str, tuple[int | None, int | None]]
) -> dict[str, tuple[int | None, int | None]]:
    """Per-folder (created, updated) from child files: min created / max updated
    over every ancestor prefix of every file path. Files with no timestamps
    contribute nothing."""
    folder_ts: dict[str, tuple[int | None, int | None]] = {}
    for path, (created, updated) in file_timestamps.items():
        if created is None and updated is None:
            continue
        parts = path.split("/")
        for i in range(1, len(parts)):
            prefix = "/".join(parts[:i])
            prev_created, prev_updated = folder_ts.get(prefix, (None, None))
            folder_ts[prefix] = (
                min(created, prev_created) if created is not None and prev_created is not None
                else (created if created is not None else prev_created),
                max(updated, prev_updated) if updated is not None and prev_updated is not None
                else (updated if updated is not None else prev_updated),
            )
    return folder_ts


def _history_query(fields: str) -> str:
    return (
        "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) "
        "{ defaultBranchRef { target { ... on Commit { " + fields + " } } } } }"
    )


def _commit_target(res: Any) -> dict | None:
    """The Commit object holding the aliased history fields, or None on failure/malformed shape."""
    if not getattr(res, "success", False) or not isinstance(res.data, dict):
        return None
    repository = res.data.get("repository")
    branch = repository.get("defaultBranchRef") if isinstance(repository, dict) else None
    target = branch.get("target") if isinstance(branch, dict) else None
    return target if isinstance(target, dict) else None


def _iso_to_ms(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return round(parsed.timestamp() * 1000)


def _commit_authored_ms(commit: Any) -> int | None:
    """Author date (epoch ms) from a ``/commits`` payload object."""
    git_commit = getattr(commit, "commit", None)
    author = getattr(git_commit, "author", None) if git_commit else None
    date = getattr(author, "date", None) if author else None
    if date is None:
        return None
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return round(date.timestamp() * 1000)
