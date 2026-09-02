# pyright: ignore-file

"""GitHub API helpers for the GitHub Teams integration tests.

Two halves:

*Reads* — discovery against the live org. The suite deliberately discovers its
fixture shapes (multi-assignee issue, sub-issue pair, blocking pair, merged PR,
nested code path) instead of pinning issue numbers, so a re-provisioned fixture org
needs no code change. Only the two frozen blocks snapshots are pinned.

*Writes* — everything the incremental cases need. ``GitHubAsyncDataSource`` is
read-only (34 methods, all GET), so both halves go through ``GitHubAsyncRESTClient``,
the raw client the data source itself wraps: same auth header, same media type, same
API version, and raw JSON rather than attribute-wrapped objects.

Code-file mutations use the **Git Data API**, not the Contents API. Contents writes
one file per commit and expresses a rename as delete-then-add across two commits;
the Git Data API builds a single tree and a single commit, so GitHub's compare
reports a genuine ``renamed`` entry with ``previous_filename`` and the connector's
``_classify_compare_files`` sees the real rename path rather than the
``_reconcile_sha_moves`` fallback.
"""

import asyncio
import base64
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Optional

from app.sources.external.github.github_async import (  # type: ignore[import-not-found]
    GitHubAsyncRESTClient,
    GitHubHTTPError,
)

from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import wait_for_sync_completion  # type: ignore[import-not-found]
from helper.oauth_token_helper import inject_access_token  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from connectors.github_teams.constants import (  # type: ignore[import-not-found]
    GH_IT_ARTIFACT_RE,
    GH_IT_PATH_ROOT,
    GH_IT_RUN_ID,
    GH_IT_STALE_ARTIFACT_AGE_SEC,
    GH_SYNC_WAIT_SEC,
    owns_path,
)

logger = logging.getLogger("github-teams-test-utils")

# GitHub returns 429 for secondary rate limits and 5xx for transient faults. 403 is
# NOT retried: GitHub overloads it for both primary rate limiting and genuine
# permission denials, and retrying the latter just burns the shared hourly budget.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BASE_DELAY_SEC = 1.0

_BLOB_MODE = "100644"


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

def build_rest_client(token: str) -> GitHubAsyncRESTClient:
    """Raw REST client for writes (and for reads we want as plain JSON)."""
    return GitHubAsyncRESTClient(lambda: token)


async def gh_call_with_retry(
    fn: Callable[..., Any],
    *args: Any,
    context: str = "github call",
    retry_server_errors: bool = True,
    **kwargs: Any,
) -> Any:
    """Await ``fn`` with bounded retries on transient GitHub failures.

    ``retry_server_errors=False`` for non-idempotent creates: a 5xx that actually
    committed would otherwise leave a duplicate issue/PR behind on the shared org.
    """
    last_error: Optional[Exception] = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return await fn(*args, **kwargs)
        except GitHubHTTPError as e:
            status = getattr(e, "status", None)
            retryable = status in _RETRYABLE_STATUSES and (
                retry_server_errors or status == 429
            )
            if not retryable or attempt == _MAX_ATTEMPTS - 1:
                raise
            last_error = e
            delay = _BASE_DELAY_SEC * (2**attempt) * (0.5 + random.random())
            logger.warning(
                "%s: HTTP %s (attempt %s/%s); retrying in %.1fs",
                context, status, attempt + 1, _MAX_ATTEMPTS, delay,
            )
            await asyncio.sleep(delay)
    raise AssertionError(f"{context}: exhausted retries") from last_error


# ---------------------------------------------------------------------------
# Reads / discovery
# ---------------------------------------------------------------------------

async def get_repo(rest: GitHubAsyncRESTClient, owner: str, repo: str) -> dict[str, Any]:
    return await gh_call_with_retry(
        rest.get_json, f"/repos/{owner}/{repo}", context=f"get_repo {owner}/{repo}",
    )


async def list_issues(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, *, state: str = "all", per_page: int = 100,
) -> list[dict[str, Any]]:
    """Issues only — GitHub's /issues listing includes PR stubs, which we drop the
    same way the connector does (``issues.py``: skip anything whose html_url
    contains ``/pull/``)."""
    rows = await gh_call_with_retry(
        rest.get_json, f"/repos/{owner}/{repo}/issues",
        params={"state": state, "per_page": per_page},
        context=f"list_issues {owner}/{repo}",
    )
    return [r for r in rows or [] if "/pull/" not in (r.get("html_url") or "")]


async def get_issue(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, number: int
) -> dict[str, Any]:
    return await gh_call_with_retry(
        rest.get_json, f"/repos/{owner}/{repo}/issues/{number}",
        context=f"get_issue {owner}/{repo}#{number}",
    )


async def list_pulls(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, *, state: str = "all", per_page: int = 100,
) -> list[dict[str, Any]]:
    return await gh_call_with_retry(
        rest.get_json, f"/repos/{owner}/{repo}/pulls",
        params={"state": state, "per_page": per_page, "sort": "updated", "direction": "desc"},
        context=f"list_pulls {owner}/{repo}",
    ) or []


async def get_pull(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, number: int
) -> dict[str, Any]:
    return await gh_call_with_retry(
        rest.get_json, f"/repos/{owner}/{repo}/pulls/{number}",
        context=f"get_pull {owner}/{repo}#{number}",
    )


async def list_collaborators(
    rest: GitHubAsyncRESTClient, owner: str, repo: str
) -> list[dict[str, Any]]:
    """Effective access list. Requires push on the repo — a 403 here means the IT
    token is under-privileged and every permission assertion would be testing the
    visibility-floor fallback instead of the real ACL."""
    return await gh_call_with_retry(
        rest.get_json, f"/repos/{owner}/{repo}/collaborators",
        params={"affiliation": "all", "per_page": 100},
        context=f"list_collaborators {owner}/{repo}",
    ) or []


async def list_org_members(rest: GitHubAsyncRESTClient, org: str) -> list[dict[str, Any]]:
    return await gh_call_with_retry(
        rest.get_json, f"/orgs/{org}/members", params={"per_page": 100},
        context=f"list_org_members {org}",
    ) or []


async def get_tree(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, ref: str
) -> list[dict[str, Any]]:
    """Recursive tree of ``ref``. Returns the raw entries (blobs and trees)."""
    payload = await gh_call_with_retry(
        rest.get_json, f"/repos/{owner}/{repo}/git/trees/{ref}",
        params={"recursive": "1"}, context=f"get_tree {owner}/{repo}@{ref}",
    )
    return (payload or {}).get("tree") or []


def blob_paths(tree: list[dict[str, Any]]) -> list[str]:
    return [e["path"] for e in tree if e.get("type") == "blob" and e.get("path")]


def tree_dirs(tree: list[dict[str, Any]]) -> set[str]:
    """Distinct directories, derived from blob paths rather than from ``type == tree``.

    The connector builds folder records from the parent prefixes of the files it
    syncs, so an empty directory (which git cannot represent anyway) is not a folder
    it would ever create. Deriving the expected set the same way keeps the counts
    comparable.
    """
    dirs: set[str] = set()
    for path in blob_paths(tree):
        parts = path.split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
    return dirs


def discover_reference_issue(issues: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """A *plain* issue for the TICKET property assertions.

    Must have neither a sub-issue parent nor outgoing blocking dependencies: the
    expected record is built from the listing payload alone, so an issue carrying a
    parent reference or related records would compare against fields the builder was
    never told about. Picking ``issues[0]`` would silently land on one of those as
    soon as the fixture repo grows.

    Prefers an issue that actually has labels and an assignee, so the comparison
    covers those fields rather than a row of Nones.
    """
    def plain(issue: dict[str, Any]) -> bool:
        summary = issue.get("issue_dependencies_summary")
        blocking = isinstance(summary, dict) and summary.get("blocking")
        return not issue.get("parent_issue_url") and not blocking

    candidates = [i for i in issues if plain(i)]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda i: (bool(i.get("labels")), bool(i.get("assignees"))),
    )


def discover_multi_assignee_issue(issues: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """An issue with 2+ assignees — the paired-field / single-ASSIGNED_TO-edge case."""
    for issue in issues:
        if len(issue.get("assignees") or []) >= 2:
            return issue
    return None


def discover_subissue_pair(
    issues: list[dict[str, Any]],
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """(parent, child) for the first issue carrying ``parent_issue_url``.

    The field is inlined on the listing payload, which is exactly where the
    connector reads it from — no extra call, and no dependence on the sub-issues
    endpoints being enabled for the token.
    """
    by_number = {i.get("number"): i for i in issues}
    for issue in issues:
        parent_url = issue.get("parent_issue_url")
        if not parent_url:
            continue
        try:
            parent_number = int(str(parent_url).rstrip("/").rsplit("/", 1)[-1])
        except (TypeError, ValueError):
            continue
        parent = by_number.get(parent_number)
        if parent is not None:
            return parent, issue
    return None, None


def discover_blocking_issue(issues: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """An issue that BLOCKS another. Only the blocking side is modelled by the
    connector (one edge per user-visible link), so the blocked side is not searched."""
    for issue in issues:
        summary = issue.get("issue_dependencies_summary")
        if isinstance(summary, dict) and summary.get("blocking"):
            return issue
    return None


def discover_merged_pr(pulls: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """A merged PR. Status is derived from ``merged_at`` by the connector, never
    from ``.merged``, so that is what we select on."""
    for pr in pulls:
        if pr.get("merged_at"):
            return pr
    return None


def deepest_blob_path(tree: list[dict[str, Any]], *, min_depth: int = 3) -> Optional[str]:
    """Deepest file path, for the folder-hierarchy chain assertion."""
    candidates = [p for p in blob_paths(tree) if p.count("/") >= min_depth - 1]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.count("/"))


def extensionless_blob_path(tree: list[dict[str, Any]]) -> Optional[str]:
    """A root-level file with no dot (LICENSE, Dockerfile) — ``extension`` must be
    None rather than an empty string."""
    for path in sorted(blob_paths(tree)):
        if "." not in path.rsplit("/", 1)[-1]:
            return path
    return None


async def discover_non_image_attachment_issue(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, issues: list[dict[str, Any]],
) -> Optional[tuple[dict[str, Any], str]]:
    """First (issue, attachment_url) whose body or comments carry a NON-image
    attachment.

    Images are inlined as base64 by the connector and produce no FileRecord
    (``_attachment_file_update`` returns None for type == "image"), so an
    image-only issue is useless for the attachment assertion.
    """
    for issue in issues:
        bodies = [issue.get("body") or ""]
        if (issue.get("comments") or 0) > 0:
            comments = await gh_call_with_retry(
                rest.get_json,
                f"/repos/{owner}/{repo}/issues/{issue['number']}/comments",
                params={"per_page": 100},
                context=f"list_issue_comments {owner}/{repo}#{issue['number']}",
            )
            bodies.extend((c.get("body") or "") for c in comments or [])
        for body in bodies:
            url = _first_non_image_attachment_url(body)
            if url:
                return issue, url
    return None


def _first_non_image_attachment_url(body: str) -> Optional[str]:
    """A GitHub user-attachments URL that is not rendered as an image.

    Markdown image syntax is ``![alt](url)``; a plain link is ``[text](url)``. The
    leading ``!`` is the whole distinction, so scan for links whose preceding
    character is not ``!``.
    """
    marker = "https://github.com/user-attachments/"
    idx = 0
    while True:
        found = body.find(marker, idx)
        if found == -1:
            return None
        idx = found + len(marker)
        open_paren = body.rfind("(", 0, found)
        if open_paren == -1:
            continue
        close_bracket = body.rfind("]", 0, open_paren)
        if close_bracket > 0 and body[close_bracket - 1] == "!":
            continue  # image — inlined, no FileRecord
        end = body.find(")", found)
        if end != -1:
            return body[found:end].strip()


# ---------------------------------------------------------------------------
# Writes — issues and pull requests
# ---------------------------------------------------------------------------

async def create_issue(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, *, title: str, body: str = "",
) -> dict[str, Any]:
    return await gh_call_with_retry(
        rest.post_json, f"/repos/{owner}/{repo}/issues", {"title": title, "body": body},
        context=f"create_issue {owner}/{repo}",
        retry_server_errors=False,  # non-idempotent: a retried 5xx could duplicate
    )


async def update_issue(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, number: int, **fields: Any,
) -> dict[str, Any]:
    resp = await gh_call_with_retry(
        rest.request, "PATCH", f"/repos/{owner}/{repo}/issues/{number}",
        json_body=fields, context=f"update_issue {owner}/{repo}#{number}",
    )
    return resp.json()


async def add_comment(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, number: int, body: str,
) -> dict[str, Any]:
    return await gh_call_with_retry(
        rest.post_json, f"/repos/{owner}/{repo}/issues/{number}/comments", {"body": body},
        context=f"add_comment {owner}/{repo}#{number}", retry_server_errors=False,
    )


async def add_sub_issue(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, parent_number: int, child_id: int,
) -> None:
    """Link an existing issue as a sub-issue. ``child_id`` is the issue's numeric
    **id**, not its number — the sub-issues endpoint takes the former."""
    await gh_call_with_retry(
        rest.request, "POST", f"/repos/{owner}/{repo}/issues/{parent_number}/sub_issues",
        json_body={"sub_issue_id": child_id},
        headers={"X-GitHub-Api-Version": "2026-03-10"},
        context=f"add_sub_issue {owner}/{repo}#{parent_number}", retry_server_errors=False,
    )


async def close_issue(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, number: int
) -> None:
    """Close an artifact issue.

    GitHub has no ordinary delete for issues, and the connector has no tombstone
    for one either (a deleted issue leaves a ghost ticket — known gap). Closing is
    the honest cleanup: the artifact stops being active without pretending the
    record can be reaped.
    """
    try:
        await update_issue(rest, owner, repo, number, state="closed")
    except Exception as e:
        logger.warning("Could not close artifact issue %s/%s#%s: %s", owner, repo, number, e)


async def create_pull(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, *,
    title: str, head: str, base: str, body: str = "",
) -> dict[str, Any]:
    return await gh_call_with_retry(
        rest.post_json, f"/repos/{owner}/{repo}/pulls",
        {"title": title, "head": head, "base": base, "body": body},
        context=f"create_pull {owner}/{repo}", retry_server_errors=False,
    )


async def update_pull(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, number: int, **fields: Any,
) -> dict[str, Any]:
    resp = await gh_call_with_retry(
        rest.request, "PATCH", f"/repos/{owner}/{repo}/pulls/{number}",
        json_body=fields, context=f"update_pull {owner}/{repo}#{number}",
    )
    return resp.json()


async def close_pull(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, number: int
) -> None:
    try:
        await update_pull(rest, owner, repo, number, state="closed")
    except Exception as e:
        logger.warning("Could not close artifact PR %s/%s#%s: %s", owner, repo, number, e)


# ---------------------------------------------------------------------------
# Writes — code files via the Git Data API
# ---------------------------------------------------------------------------

class FileChange:
    """One entry in a tree write.

    ``content is None`` deletes the path (the Git Trees API takes ``sha: null`` to
    remove an entry from the base tree).
    """

    __slots__ = ("path", "content")

    def __init__(self, path: str, content: Optional[str]) -> None:
        self.path = path
        self.content = content

    @classmethod
    def upsert(cls, path: str, content: str) -> "FileChange":
        return cls(path, content)

    @classmethod
    def delete(cls, path: str) -> "FileChange":
        return cls(path, None)


async def get_branch_head(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, branch: str
) -> str:
    payload = await gh_call_with_retry(
        rest.get_json, f"/repos/{owner}/{repo}/git/ref/heads/{branch}",
        context=f"get_ref {owner}/{repo}@{branch}",
    )
    return payload["object"]["sha"]


async def read_file(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, path: str, ref: str
) -> str:
    payload = await gh_call_with_retry(
        rest.get_json, f"/repos/{owner}/{repo}/contents/{path}", params={"ref": ref},
        context=f"read_file {owner}/{repo}:{path}",
    )
    return base64.b64decode(payload.get("content") or "").decode("utf-8")


async def commit_changes(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, branch: str,
    changes: list[FileChange], message: str,
) -> str:
    """Apply every change as ONE commit on ``branch``; returns the new commit sha.

    A single commit is what makes a rename legible: git records the move in one
    tree transition, so ``GET /compare`` reports ``status == "renamed"`` with
    ``previous_filename`` set. Splitting the same edit across two Contents-API
    commits would surface as removed + added and exercise a different code path.

    Guard: every mutated path must be inside this run's ``it/<run_id>/`` namespace.
    Concurrent runs share this branch, and touching a path outside the namespace
    would corrupt another run's fixtures.
    """
    for change in changes:
        if not owns_path(change.path):
            raise AssertionError(
                f"refusing to commit {change.path!r}: outside this run's namespace "
                f"({GH_IT_PATH_ROOT}/{GH_IT_RUN_ID}/). Concurrent runs share this branch."
            )

    base_commit_sha = await get_branch_head(rest, owner, repo, branch)
    base_commit = await gh_call_with_retry(
        rest.get_json, f"/repos/{owner}/{repo}/git/commits/{base_commit_sha}",
        context=f"get_commit {base_commit_sha[:7]}",
    )
    base_tree_sha = base_commit["tree"]["sha"]

    tree_entries: list[dict[str, Any]] = []
    for change in changes:
        entry: dict[str, Any] = {"path": change.path, "mode": _BLOB_MODE, "type": "blob"}
        if change.content is None:
            entry["sha"] = None
        else:
            entry["content"] = change.content
        tree_entries.append(entry)

    tree = await gh_call_with_retry(
        rest.post_json, f"/repos/{owner}/{repo}/git/trees",
        {"base_tree": base_tree_sha, "tree": tree_entries},
        context=f"create_tree {owner}/{repo}", retry_server_errors=False,
    )
    commit = await gh_call_with_retry(
        rest.post_json, f"/repos/{owner}/{repo}/git/commits",
        {"message": message, "tree": tree["sha"], "parents": [base_commit_sha]},
        context=f"create_commit {owner}/{repo}", retry_server_errors=False,
    )
    new_sha = commit["sha"]
    await gh_call_with_retry(
        rest.request, "PATCH", f"/repos/{owner}/{repo}/git/refs/heads/{branch}",
        json_body={"sha": new_sha}, context=f"update_ref {owner}/{repo}@{branch}",
    )
    logger.info("Committed %s change(s) to %s/%s@%s: %s",
                len(changes), owner, repo, branch, new_sha[:7])
    return new_sha


async def blob_sha_for_path(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, path: str, ref: str
) -> Optional[str]:
    """Current blob sha of ``path`` — the value the connector stores as
    ``file_hash`` / ``external_revision_id``."""
    tree = await get_tree(rest, owner, repo, ref)
    for entry in tree:
        if entry.get("path") == path and entry.get("type") == "blob":
            return entry.get("sha")
    return None


# ---------------------------------------------------------------------------
# Artifact hygiene
# ---------------------------------------------------------------------------

async def reap_own_artifacts(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, branch: str,
) -> None:
    """Remove everything this run still owns: its code namespace, and its open
    issues/PRs. Best-effort — a cleanup failure must never fail a passing suite."""
    try:
        tree = await get_tree(rest, owner, repo, branch)
        mine = [p for p in blob_paths(tree) if owns_path(p)]
        if mine:
            await commit_changes(
                rest, owner, repo, branch,
                [FileChange.delete(p) for p in mine],
                message=f"IT cleanup: remove {GH_IT_PATH_ROOT}/{GH_IT_RUN_ID}",
            )
    except Exception as e:
        logger.warning("TEARDOWN: could not clean code namespace for run %s: %s", GH_IT_RUN_ID, e)

    try:
        for pr in await list_pulls(rest, owner, repo, state="open"):
            if _is_own_artifact(pr.get("title")):
                await close_pull(rest, owner, repo, pr["number"])
        for issue in await list_issues(rest, owner, repo, state="open"):
            if _is_own_artifact(issue.get("title")):
                await close_issue(rest, owner, repo, issue["number"])
    except Exception as e:
        logger.warning("TEARDOWN: could not close artifacts for run %s: %s", GH_IT_RUN_ID, e)


async def sweep_stale_artifacts(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, branch: str,
) -> None:
    """Clean up artifacts leaked by runs that no longer exist.

    A cancelled CI job SIGTERMs pytest before any ``finally``, stranding its
    namespace and issues. Only artifacts matching the strict run-id shape AND older
    than the stale gate are touched, so a run still asserting on its own fixtures is
    never disturbed.
    """
    cutoff = time.time() - GH_IT_STALE_ARTIFACT_AGE_SEC

    try:
        stale_dirs = await _stale_namespace_paths(rest, owner, repo, branch, cutoff)
        if stale_dirs:
            logger.info("SETUP: sweeping %s leaked code path(s)", len(stale_dirs))
            await _force_commit_deletes(rest, owner, repo, branch, stale_dirs)
    except Exception as e:
        logger.warning("SETUP: stale code sweep failed (continuing): %s", e)

    try:
        for issue in await list_issues(rest, owner, repo, state="open"):
            if _is_stale_artifact(issue, cutoff):
                await close_issue(rest, owner, repo, issue["number"])
        for pr in await list_pulls(rest, owner, repo, state="open"):
            if _is_stale_artifact(pr, cutoff):
                await close_pull(rest, owner, repo, pr["number"])
    except Exception as e:
        logger.warning("SETUP: stale issue/PR sweep failed (continuing): %s", e)


def _is_own_artifact(title: Optional[str]) -> bool:
    return bool(title) and bool(GH_IT_ARTIFACT_RE.match(title)) and GH_IT_RUN_ID in title


def _is_stale_artifact(item: dict[str, Any], cutoff: float) -> bool:
    title = item.get("title") or ""
    if not GH_IT_ARTIFACT_RE.match(title):
        return False  # not ours — never touch
    if GH_IT_RUN_ID in title:
        return False  # this run's own, still in use
    created = item.get("created_at")
    if not created:
        return False
    parsed = _parse_iso8601(created)
    return parsed is not None and parsed < cutoff


async def _stale_namespace_paths(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, branch: str, cutoff: float,
) -> list[str]:
    """Paths under ``it/<other_run_id>/`` whose owning run is long gone.

    Age comes from the last commit that touched the directory, so a namespace being
    actively written by a live run is never selected.
    """
    tree = await get_tree(rest, owner, repo, branch)
    prefix = f"{GH_IT_PATH_ROOT}/"
    by_run: dict[str, list[str]] = {}
    for path in blob_paths(tree):
        if not path.startswith(prefix) or owns_path(path):
            continue
        parts = path.split("/")
        if len(parts) < 3:
            continue
        by_run.setdefault(parts[1], []).append(path)

    stale: list[str] = []
    for run_id, paths in by_run.items():
        commits = await gh_call_with_retry(
            rest.get_json, f"/repos/{owner}/{repo}/commits",
            params={"path": f"{prefix}{run_id}", "per_page": 1},
            context=f"last commit for {prefix}{run_id}",
        )
        if not commits:
            continue
        committed = (commits[0].get("commit") or {}).get("committer", {}).get("date")
        parsed = _parse_iso8601(committed) if committed else None
        if parsed is not None and parsed < cutoff:
            stale.extend(paths)
    return stale


async def _force_commit_deletes(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, branch: str, paths: list[str],
) -> None:
    """Delete paths that belong to *other* runs' namespaces.

    ``commit_changes`` deliberately refuses paths outside this run's namespace, which
    is exactly the guard we must step around to reap a leak — so the sweep builds its
    own tree write. Still bounded to ``it/<run_id>/`` prefixes by the caller.
    """
    prefix = f"{GH_IT_PATH_ROOT}/"
    assert all(p.startswith(prefix) for p in paths), "sweep confined to the IT namespace"

    base_commit_sha = await get_branch_head(rest, owner, repo, branch)
    base_commit = await gh_call_with_retry(
        rest.get_json, f"/repos/{owner}/{repo}/git/commits/{base_commit_sha}",
        context="sweep get_commit",
    )
    tree = await gh_call_with_retry(
        rest.post_json, f"/repos/{owner}/{repo}/git/trees",
        {
            "base_tree": base_commit["tree"]["sha"],
            "tree": [
                {"path": p, "mode": _BLOB_MODE, "type": "blob", "sha": None} for p in paths
            ],
        },
        context="sweep create_tree", retry_server_errors=False,
    )
    commit = await gh_call_with_retry(
        rest.post_json, f"/repos/{owner}/{repo}/git/commits",
        {
            "message": "IT cleanup: reap leaked integration-test namespaces",
            "tree": tree["sha"],
            "parents": [base_commit_sha],
        },
        context="sweep create_commit", retry_server_errors=False,
    )
    await gh_call_with_retry(
        rest.request, "PATCH", f"/repos/{owner}/{repo}/git/refs/heads/{branch}",
        json_body={"sha": commit["sha"]}, context="sweep update_ref",
    )


def _parse_iso8601(value: str) -> Optional[float]:
    """GitHub timestamps are ``2026-09-01T12:34:56Z``; return epoch seconds."""
    from datetime import datetime, timezone  # noqa: PLC0415 — keep module import light

    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


# ---------------------------------------------------------------------------
# PipesHub connector lifecycle
# ---------------------------------------------------------------------------
#
# These live here rather than in ``conftest.py`` so the test module can import them
# without importing the conftest: pytest loads conftest under its own module name, and
# importing it a second time by path would create a duplicate module object.

def sync_filters(**values: Any) -> dict[str, Any]:
    """Wrap filter fields into the connector's ``config.filters.sync.values`` shape.

    The connector reads ``config.filters.sync.values.<key>``; the filters-sync endpoint
    stores the request ``filters`` verbatim, so the payload must already carry the
    ``sync.values`` nesting — a flat ``{"repo_ids": ...}`` is written to the wrong path
    and silently ignored, which syncs *everything* instead of failing.
    """
    return {"sync": {"values": values}}


def list_filter(operator: str, values: list[str]) -> dict[str, Any]:
    return {"operator": operator, "type": "list", "value": values}


def create_github_connector(
    pipeshub_client: PipeshubClient,
    *,
    token: str,
    name: str,
    filters: Optional[dict[str, Any]] = None,
) -> str:
    """Register a GitHub Teams connector and authenticate it. Returns connector_id.

    ``config`` must be non-empty: the create route only persists the ``auth`` block
    (which carries ``authType: OAUTH``) when the request contained a config, and
    ``build_from_services`` raises "Auth configuration not found" without it.
    """
    config: dict[str, Any] = {"auth": {}}
    if filters:
        config["filters"] = filters

    instance = pipeshub_client.create_connector(
        # Exact string: the registry lookup is a plain dict hit with no normalization,
        # so "Github Teams" or "githubteams" would 404.
        connector_type="GitHub Teams",
        instance_name=name,
        scope="team",
        config=config,
        auth_type="OAUTH",
    )
    assert instance.connector_id, "Connector must have a valid ID"
    inject_access_token(instance.connector_id, token)
    return instance.connector_id


async def teardown_connector(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
) -> None:
    """Disable, delete, and wait for the graph to drain."""
    pipeshub_client.toggle_sync(connector_id, enable=False)
    pipeshub_client.delete_connector(connector_id)
    pipeshub_client.wait(25)
    await graph_provider.assert_all_records_cleaned(
        connector_id,
        timeout=int(os.getenv("INTEGRATION_GRAPH_CLEANUP_TIMEOUT", "300")),
    )


@asynccontextmanager
async def dedicated_connector(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    *,
    token: str,
    name: str,
    filters: dict[str, Any],
    min_records: int | None = None,
    timeout: int = GH_SYNC_WAIT_SEC,
) -> AsyncIterator[str]:
    """A throw-away connector for one mutation test.

    Every mutation case gets its own connector scoped to the mutation repo, so the
    shared fixture connector and the read-only repos are untouched and nothing another
    concurrent run does can reach an assertion here. Assertions inside must still be by
    external id — the mutation repo itself is shared.
    """
    connector_id = create_github_connector(
        pipeshub_client, token=token, name=name, filters=filters,
    )
    try:
        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id,
            min_records=min_records, timeout=timeout,
        )
        yield connector_id
    finally:
        # Log, never raise. This runs in a ``finally``, so an exception here would
        # replace the real assertion error from the test body with a cleanup error and
        # hide what actually broke. A connector that leaks is still reported, and the
        # module fixture's own teardown asserts the graph drained.
        try:
            await teardown_connector(pipeshub_client, graph_provider, connector_id)
        except Exception as e:
            logger.error(
                "dedicated connector %s (%s) cleanup leaked: %s", name, connector_id, e,
            )


async def resolve_app_user_emails(
    graph_provider: GraphProviderProtocol, connector_id: str, source_ids: list[str],
) -> dict[str, str]:
    """GitHub numeric id -> the email the connector bound it to.

    GitHub exposes logins, never addresses, so there is no source-side way to predict
    which principals resolved to a PipesHub identity — the connector's own multi-phase
    resolution decides that. Reading the resolved map back from the AppUser nodes is the
    only option; the record assertions that consume it stay source-derived, since this
    only answers "which of these people has an identity at all".
    """
    resolved: dict[str, str] = {}
    for source_id in source_ids:
        user = await graph_provider.get_user_by_source_id(
            source_user_id=str(source_id), connector_id=connector_id,
        )
        if user is not None and getattr(user, "email", None):
            resolved[str(source_id)] = user.email
    return resolved
