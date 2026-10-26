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
- GraphQL failures are told apart. A batch too heavy for GitHub to answer
  (a 5xx or a timeout, after ``ds_call``'s own retries) is split and asked
  again, and the smaller size is kept for the rest of the call. A rate limit
  stops the backfill run: the per-path REST fallback would spend two core
  requests per file from the quota content indexing waits on. Only a batch
  that still fails at ``_GRAPHQL_MIN_ALIAS_BATCH`` falls back to REST.

Hard-won rule (three production bugs live in this file's history): NEVER
rebuild a record from a bare graph node. The node carries only base-record
fields; a record fabricated via ``from_arango_record({}, node)`` silently
loses type-document fields (``file_path`` -> the file pass no-ops,
``is_file`` -> folders flip into files on re-upsert). This module therefore
never builds record objects at all: it patches only the named properties via
``data_entities_processor.batch_update_nodes`` (a partial merge on both providers), so
nothing outside the patch can be touched.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.sources.external.github.github_async import GhObject

from app.config.constants.arangodb import CollectionNames
from app.models.entities import RecordType
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.url_redaction import redact_urls_in_text

from .models import path_from_external_id

if TYPE_CHECKING:
    from app.connectors.sources.github_teams.connector import GitHubTeamsConnector

_BATCH_SIZE = 100
_FETCH_CONCURRENCY = 10
_GRAPHQL_ALIAS_BATCH = 100
# Smallest batch a too-heavy query is split down to before the paths in it
# fall back to REST (2 core requests each).
_GRAPHQL_MIN_ALIAS_BATCH = 10


@dataclass(frozen=True)
class _GraphqlFailure:
    """Why a GraphQL commit-date query got no data."""

    rate_limited: bool
    too_heavy: bool
    retry_after_seconds: int | None
    detail: str


class BackfillRateLimitedError(Exception):
    """GitHub rate-limited the backfill; the run stops and the next sync resumes it."""

    def __init__(self, failure: _GraphqlFailure) -> None:
        self.failure = failure
        super().__init__(failure.detail)


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
        except BackfillRateLimitedError as e:
            # Not an error: only records still missing both dates are asked
            # for, so the run after the next sync carries on from here.
            self.logger.info(
                "Code file timestamp backfill for connector %s paused: GitHub rate-limited it (%s); "
                "the next sync resumes it.",
                c.connector_id, _resume_hint(e.failure),
            )
        except Exception as e:
            self.logger.error("Code file timestamp backfill failed for connector %s: %s", c.connector_id, e, exc_info=True)
        finally:
            self._task = None

    # ------------------------------------------------------------------
    # Per-repo passes
    # ------------------------------------------------------------------

    async def backfill_repo(self, repo: GhObject) -> None:
        """File pass (commit history) then folder pass (derived from files)."""
        external_group_id = f"{repo.id}-code-repository"
        try:
            await self._backfill_file_timestamps(repo, external_group_id)
        except BackfillRateLimitedError:
            # Folders cost no API calls; stamp them from the files dated so
            # far, then stop the run (the same quota covers every repo).
            await self._backfill_folders_safely(repo, external_group_id)
            raise
        except Exception as e:
            self.logger.error("Code file timestamp backfill failed for repo %s: %s", repo.full_name, e, exc_info=True)
            return
        await self._backfill_folders_safely(repo, external_group_id)

    async def _backfill_folders_safely(self, repo: GhObject, external_group_id: str) -> None:
        try:
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
            dates_by_path, failure = await self._fetch_commit_dates(owner, name, batch_paths)
            patches = []
            for file_path in batch_paths:
                created_ms, updated_ms = dates_by_path.get(file_path, (None, None))
                if created_ms is None and updated_ms is None:
                    continue
                for record_id in path_to_record_ids[file_path]:
                    patches.append(_date_patch(record_id, created_ms, updated_ms))
            await self._apply_patches(patches, CollectionNames.RECORDS.value, context=repo.full_name)
            if failure is not None:
                raise BackfillRateLimitedError(failure)

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
        nodes = await self.c.data_entities_processor.get_nodes_by_filters(
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
            await self.c.data_entities_processor.batch_update_nodes(patches, collection)
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
        path. Also used by the incremental sync to stamp exact dates on changed
        files at sync time. A path GitHub's rate limit kept from being asked
        about is left out; the backfill dates it later.

        A path left out for any other reason (its oldest-commit query failed)
        is dated over REST here. The incremental sync keeps a file's stored
        dates when a path is missing, and the backfill only revisits records
        with no dates, so a changed file would keep its old modified date.
        """
        results, failure = await self._fetch_commit_dates(owner, name, paths)
        if failure is None:
            missing = [path for path in paths if path not in results]
            if missing:
                # A rate limit here leaves the rest missing; the stored dates stay.
                rest_dates, _limited = await self._fetch_dates_rest(owner, name, missing)
                results.update(rest_dates)
        return results

    async def _fetch_commit_dates(
        self, owner: str, name: str, paths: list[str]
    ) -> tuple[dict[str, tuple[int | None, int | None]], _GraphqlFailure | None]:
        """The dates, and the rate limit that stopped the fetch early, if any.

        A chunk GitHub could not answer in time is split in half and asked
        again, and the smaller size is kept for the chunks after it. A chunk
        that still fails at ``_GRAPHQL_MIN_ALIAS_BATCH``, or fails for any
        reason other than a rate limit or a timeout, falls back to REST. A rate
        limit stops here, without REST: those requests come from the core quota.
        """
        results: dict[str, tuple[int | None, int | None]] = {}
        size = _GRAPHQL_ALIAS_BATCH
        remaining = list(paths)
        while remaining:
            chunk, rest = remaining[:size], remaining[size:]
            chunk_dates, failure = await self._fetch_dates_graphql(owner, name, chunk)
            results.update(chunk_dates)
            if failure is None:
                remaining = rest
                continue
            if failure.rate_limited:
                self.logger.warning(
                    "GitHub rate-limited the commit-date query for %s/%s (%s); %s path(s) left undated "
                    "for now, without falling back to REST.",
                    owner, name, failure.detail, len(remaining) - len(chunk_dates),
                )
                return results, failure
            if failure.too_heavy and len(chunk) > _GRAPHQL_MIN_ALIAS_BATCH:
                size = max(_GRAPHQL_MIN_ALIAS_BATCH, len(chunk) // 2)
                self.logger.info(
                    "GraphQL commit-date batch of %s paths too heavy for %s/%s (%s); retrying in batches of %s.",
                    len(chunk), owner, name, failure.detail, size,
                )
                continue
            self.logger.warning(
                "GraphQL commit-date batch failed for %s/%s (%s); falling back to REST for %s path(s).",
                owner, name, failure.detail, len(chunk),
            )
            rest_dates, rest_failure = await self._fetch_dates_rest(owner, name, chunk)
            results.update(rest_dates)
            if rest_failure is not None:
                self.logger.warning(
                    "GitHub rate-limited the REST commit-date fallback for %s/%s (%s); stopping.",
                    owner, name, rest_failure.detail,
                )
                return results, rest_failure
            remaining = rest
        return results, None

    async def _fetch_dates_graphql(
        self, owner: str, name: str, paths: list[str]
    ) -> tuple[dict[str, tuple[int | None, int | None]], _GraphqlFailure | None]:
        """Batched dates for up to ``_GRAPHQL_ALIAS_BATCH`` paths.

        Pass 1: one aliased query per chunk — each path's newest commit date,
        ``totalCount`` and a history cursor. Single-commit paths finish here
        (created == updated). Pass 2: commit history only paginates forward,
        but its cursors are ``"<anchor-oid> <offset>"``, so the oldest commit
        is one more query away via a constructed cursor — no page walking.

        A failed pass 1 returns no dates and the failure. In pass 2, a query
        too heavy to answer is split like pass 1, and a path still without an
        oldest date takes its newest as created. A rate limit in pass 2 leaves
        the paths that needed it out, so they stay undated and are asked again.
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
            return {}, _classify_failure(res)

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

        oldest, failure = await self._fetch_oldest_dates(pending_oldest, variables)
        for i, path, _cursor in pending_oldest:
            if i not in oldest:
                # Its oldest commit was never read (a rate limit, or a query
                # that kept failing): left undated, so a later run asks again.
                results.pop(path, None)
                continue
            _created, updated_ms = results[path]
            # A created date should never be missing when the modified date is
            # known — the newest commit bounds it, so fall back to that.
            oldest_ms = oldest[i]
            results[path] = (oldest_ms if oldest_ms is not None else updated_ms, updated_ms)
        return results, failure

    async def _fetch_oldest_dates(
        self, pending: list[tuple[int, str, str]], variables: dict[str, str],
    ) -> tuple[dict[int, int | None], _GraphqlFailure | None]:
        """Pass 2: ``{alias index: oldest commit ms or None}``, and a rate limit that stopped it."""
        c = self.c
        found: dict[int, int | None] = {}
        size = max(1, len(pending))
        remaining = list(pending)
        while remaining:
            chunk, rest = remaining[:size], remaining[size:]
            fields = " ".join(
                f"p{i}: history(first: 1, path: {json.dumps(path)}, after: {json.dumps(cursor)}) "
                "{ nodes { authoredDate } }"
                for i, path, cursor in chunk
            )
            res = await c.runtime.ds_call(c.data_source.graphql_query, _history_query(fields), variables)
            target = _commit_target(res)
            if target is None:
                failure = _classify_failure(res)
                if failure.rate_limited:
                    return found, failure
                if failure.too_heavy and len(chunk) > _GRAPHQL_MIN_ALIAS_BATCH:
                    size = max(_GRAPHQL_MIN_ALIAS_BATCH, len(chunk) // 2)
                    continue
                # A failed read is not an empty one: these paths stay out of
                # `found`, so they stay undated and a later run asks again,
                # rather than taking their newest commit as created for good.
                self.logger.warning(
                    "Oldest-commit query for %s path(s) failed (%s); leaving them undated for now.",
                    len(chunk), failure.detail,
                )
                remaining = rest
                continue
            for i, _path, _cursor in chunk:
                nodes = (target.get(f"p{i}") or {}).get("nodes") or []
                found[i] = _iso_to_ms(nodes[0].get("authoredDate")) if nodes else None
            remaining = rest
        return found, None

    async def _fetch_dates_rest(
        self, owner: str, name: str, paths: list[str]
    ) -> tuple[dict[str, tuple[int | None, int | None]], _GraphqlFailure | None]:
        """Per-path REST fallback: 2 requests per path against the core quota.

        A rate-limited answer is not an empty history: that path is left out,
        the paths not yet asked about are skipped, and the rate limit is
        returned so the caller stops. (A secondary limit can leave the core
        quota above the connector's reserve, so nothing else would stop it.)
        """
        c = self.c
        semaphore = asyncio.Semaphore(_FETCH_CONCURRENCY)
        results: dict[str, tuple[int | None, int | None]] = {}
        limited: list[_GraphqlFailure] = []

        async def fetch_one(path: str) -> None:
            async with semaphore:
                if limited:
                    return
                try:
                    res = await c.runtime.ds_call(
                        c.data_source.list_commits_first_and_last, owner, name, path,
                    )
                    if not res.success:
                        failure = _classify_failure(res)
                        if failure.rate_limited:
                            limited.append(failure)
                            return
                        results[path] = (None, None)
                        return
                    if not res.data:
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
        return results, (limited[0] if limited else None)


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


# GitHub's GraphQL API reports an exhausted point budget with HTTP 200 and an
# error of this type, so the status code alone misses it.
_GRAPHQL_RATE_LIMITED = "RATE_LIMITED"
# GraphQL's own "query took too long" answer, also sent with HTTP 200.
# How GitHub words a query it could not finish, when the status alone doesn't
# say so: a timeout ("We couldn't respond to your request in time", also sent
# as 502/504) or a query over its resource limits ("resource limits were
# exceeded", which can come back with HTTP 200 and partial data). See
# docs.github.com/en/graphql/overview/rate-limits-and-query-limits-for-the-graphql-api.
_GRAPHQL_TIMEOUT_MARKERS = (
    "timeout", "timed out", "something went wrong while executing your query",
    "couldn't respond to your request in time", "resource limit", "resource_limit",
)


def _classify_failure(res: object) -> _GraphqlFailure:
    """Rate limit, too heavy (5xx or a timeout), or anything else."""
    status = getattr(res, "status_code", None)
    status = status if isinstance(status, int) else None
    error = getattr(res, "error", None)
    error = error if isinstance(error, str) else ""
    retry_after = getattr(res, "retry_after_seconds", None)
    retry_after = retry_after if isinstance(retry_after, int) else None
    exception_type = getattr(res, "exception_type", None)
    lowered = error.lower()
    rate_limited = (
        getattr(res, "is_rate_limited", False) is True
        or status == 429
        or _GRAPHQL_RATE_LIMITED in error
        or "rate limit" in lowered
    )
    too_heavy = not rate_limited and (
        (status is not None and status >= 500)
        or exception_type in ("TimeoutError", "ReadTimeout", "ReadError")
        or any(marker in lowered for marker in _GRAPHQL_TIMEOUT_MARKERS)
    )
    # Error text can quote a URL; it is logged, so it goes through redaction.
    detail = f"HTTP {status}" if status is not None else (redact_urls_in_text(error[:120]) or "no data")
    return _GraphqlFailure(rate_limited, too_heavy, retry_after, detail)


def _resume_hint(failure: _GraphqlFailure) -> str:
    if failure.retry_after_seconds is None:
        return failure.detail
    return f"{failure.detail}, resets in {failure.retry_after_seconds}s"


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
