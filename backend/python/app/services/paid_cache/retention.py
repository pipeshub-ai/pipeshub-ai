"""Retention by reference: a record's store holds the two newest completed revisions' objects.

There is no access time and no idle sweep. When revision ``R_n`` completes, the sweep lists the record's
folder and deletes every object that neither of the two newest completed revisions' manifests lists, with the
older manifests. Never deleted, whatever the manifests say:

* an object listed by an open manifest less than ``OPEN_PROTECTS_MS`` old (an in-flight or retried run);
* an object the storage service wrote within ``GRACE_MS`` of the sweep's start (a put not yet flushed into an
  open manifest);
* the objects of a revision whose run failed (open manifest only), until two newer revisions have completed.

With no completed revision to read, nothing is deleted: the sweep cannot tell what is still wanted.
"""

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from app.services.paid_cache.cache import ResultStore
from app.services.paid_cache.run import OPEN_PROTECTS_MS

#: Completed revisions whose objects are kept (the newest and one before it).
KEEP_REVISIONS = 2
GRACE_MS = 3600 * 1000
_MANIFESTS = "manifests/"


@dataclass(frozen=True, slots=True)
class SweepResult:
    deleted: int
    kept: int
    skipped_reason: str | None = None


def _split(path: str) -> tuple[str, str, bool] | None:
    """``manifests/{rev}/{writer}[.open].json`` as (rev, writer, is_open)."""
    parts = path.split("/")
    if len(parts) != 3 or parts[0] != "manifests" or not parts[2].endswith(".json"):
        return None
    name = parts[2][: -len(".json")]
    is_open = name.endswith(".open")
    return parts[1], name[: -len(".open")] if is_open else name, is_open


async def _read(store: ResultStore, org_id: str, record_id: str, path: str) -> dict[str, Any] | None:
    raw = await store.get(org_id, record_id, path)
    if raw is None:
        return None
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _keys(body: dict[str, Any]) -> set[str]:
    keys = body.get("keys")
    return {k for k in keys if isinstance(k, str)} if isinstance(keys, list) else set()


async def sweep_record(
    store: ResultStore, org_id: str, record_id: str, *, now_ms: int | None = None,
    logger: logging.Logger | None = None,
) -> SweepResult:
    """Applies the retention rule once. Raises on a storage failure; the next completed revision retries it."""
    log = logger or logging.getLogger(__name__)
    sweep_start = now_ms if now_ms is not None else int(time.time() * 1000)
    async def snapshot() -> tuple[list[Any], list[str] | None]:
        """(files, the paths the rule would delete now), or (files, None) when there is no completed revision."""
        files = await store.list_files(org_id, record_id)
        manifests = [f for f in files if f.path.startswith(_MANIFESTS)]

        final_keys: dict[str, set[str]] = {}
        #: Per revision, when it completed: (the writer's ``completedMs``, its ``seq``). The storage write time is
        #: only the fallback for a manifest that has none: S3 and Azure report it in whole seconds.
        final_at: dict[str, tuple[int, int]] = {}
        open_started: dict[tuple[str, str], int] = {}
        open_keys: dict[tuple[str, str], set[str]] = {}
        #: The writers that completed per revision: one writer's completion is not another's.
        final_writers: dict[str, set[str]] = {}
        manifest_files: list[tuple[str, str, bool]] = []  # (path, rev, is_open)
        for m in manifests:
            parsed = _split(m.path)
            if parsed is None:
                continue
            rev, writer, is_open = parsed
            manifest_files.append((m.path, rev, is_open))
            body = await _read(store, org_id, record_id, m.path)
            if body is None:
                continue
            if is_open:
                started = body.get("startedMs")
                started = started if isinstance(started, int) else m.last_modified_ms
                open_started[rev, writer] = max(open_started.get((rev, writer), 0), started)
                open_keys.setdefault((rev, writer), set()).update(_keys(body))
            else:
                final_writers.setdefault(rev, set()).add(writer)
                final_keys.setdefault(rev, set()).update(_keys(body))
                completed, seq = body.get("completedMs"), body.get("seq")
                at = (
                    completed if isinstance(completed, int) and not isinstance(completed, bool) else m.last_modified_ms,
                    seq if isinstance(seq, int) and not isinstance(seq, bool) else 0,
                )
                final_at[rev] = max(final_at.get(rev, (0, 0)), at)
        if not final_keys:
            return files, None

        newest = sorted(final_at, key=lambda r: (final_at[r], r), reverse=True)
        kept_revs = set(newest[:KEEP_REVISIONS])
        protected: set[str] = set()
        for rev in kept_revs:
            protected.update(final_keys[rev])
        kept_manifest_files: set[str] = {path for path, rev, _ in manifest_files if rev in kept_revs}

        for (rev, writer), started in open_started.items():
            young = sweep_start - started < OPEN_PROTECTS_MS
            newer_completed = sum(1 for r in final_at if final_at[r][0] > started)
            unfinished = writer not in final_writers.get(rev, set())
            # A writer that never completed keeps its references while its revision is still retained (another
            # writer of the same revision may have completed), or while fewer than the kept number of newer
            # revisions completed.
            if young or (unfinished and (rev in kept_revs or newer_completed < KEEP_REVISIONS)):
                protected.update(open_keys.get((rev, writer), set()))
                kept_manifest_files.update(
                    path for path, r, is_open in manifest_files
                    if r == rev and is_open and _split(path) is not None and _split(path)[1] == writer  # type: ignore[index]
                )

        doomed = [
            f.path for f in files
            if f.path not in protected and f.path not in kept_manifest_files
            and f.last_modified_ms < sweep_start - GRACE_MS
        ]
        return files, doomed

    files, doomed = await snapshot()
    if doomed is None:
        return SweepResult(0, len(files), "no completed revision")
    if doomed:
        # A run that reused one of these objects after the listing above has noted it in its manifest by now: the
        # manifests are read again and only what both reads leave unprotected is deleted.
        _, again = await snapshot()
        still_unprotected = set(again or ())
        doomed = [path for path in doomed if path in still_unprotected]
    if doomed:
        await store.delete_files(org_id, record_id, doomed)
    log.debug("Paid-result sweep of record %s: %d deleted, %d kept", record_id, len(doomed), len(files) - len(doomed))
    return SweepResult(len(doomed), len(files) - len(doomed))
