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
import re
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

# GitHub silently caps listing pages at 100 regardless of what is requested.
_GITHUB_MAX_PER_PAGE = 100

# Attachment markup, matching what clean_github_content recognises.
_HTML_IMG_RE = re.compile(
    r"""<img\s+[^>]*?src=["'](.*?)["'][^>]*?/?>""", re.IGNORECASE | re.DOTALL,
)
_MD_IMAGE_RE = re.compile(r"!\[(.*?)\]\((.*?)\)")
_MD_LINK_RE = re.compile(r"\[(.*?)\]\((.*?)\)")
_ATTACHMENT_URL_PREFIX = "https://github.com/user-attachments/"


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

async def _walk_pages(
    rest: GitHubAsyncRESTClient,
    path: str,
    *,
    params: dict[str, Any],
    per_page: int,
    context: str,
) -> list[dict[str, Any]]:
    """Every page of a listing, not just the first.

    ``get_json`` returns only the body, so the ``Link: rel="next"`` header is not
    visible; a short page is the end-of-listing signal instead. Cleanup loops depend
    on this — reading page 1 alone would silently leave artifacts behind on any repo
    holding more than ``per_page`` issues.
    """
    # A short page is the end-of-listing signal, which is only meaningful when the
    # requested size is one GitHub will honour. Above 100 it silently returns 100 and
    # the first page would look "short", truncating the listing; at or below 0 the
    # comparison can never be true and the loop would never terminate.
    if per_page < 1:
        raise ValueError(f"per_page must be >= 1, got {per_page}")
    per_page = min(per_page, _GITHUB_MAX_PER_PAGE)

    collected: list[dict[str, Any]] = []
    page = 1
    while True:
        rows = await gh_call_with_retry(
            rest.get_json, path,
            params={**params, "per_page": per_page, "page": page},
            context=f"{context} (page {page})",
        ) or []
        collected.extend(rows)
        if len(rows) < per_page:
            return collected
        page += 1


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
    rows = await _walk_pages(
        rest, f"/repos/{owner}/{repo}/issues",
        params={"state": state}, per_page=per_page,
        context=f"list_issues {owner}/{repo}",
    )
    return [r for r in rows if "/pull/" not in (r.get("html_url") or "")]


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
    return await _walk_pages(
        rest, f"/repos/{owner}/{repo}/pulls",
        params={"state": state, "sort": "updated", "direction": "desc"},
        per_page=per_page, context=f"list_pulls {owner}/{repo}",
    )


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
    """A GitHub user-attachments URL that becomes a FileRecord, or None.

    Mirrors ``CommentsHelper.clean_github_content``'s order of operations: it strips
    the two *image* forms first — an HTML ``<img src=...>`` tag and a markdown
    ``![alt](url)`` — because both are inlined as base64 and deliberately produce no
    FileRecord. Only a plain markdown link ``[text](url)`` yields one.

    Handling the HTML form matters: GitHub's web UI pastes images as ``<img>`` tags,
    not markdown, so scanning for a leading ``!`` alone silently mistakes a pasted
    screenshot for an attachment.
    """
    stripped = _HTML_IMG_RE.sub("", body)
    stripped = _MD_IMAGE_RE.sub("", stripped)
    for _text, url in _MD_LINK_RE.findall(stripped):
        url = url.strip()
        if url.startswith(_ATTACHMENT_URL_PREFIX):
            return url
    return None


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


async def delete_issue_comment(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, comment_id: int
) -> None:
    """Remove a comment this run added to a long-lived fixture.

    The pinned incremental PR is never deleted, so anything a run adds to it would
    otherwise accumulate one item per run forever.
    """
    try:
        await gh_call_with_retry(
            rest.request, "DELETE", f"/repos/{owner}/{repo}/issues/comments/{comment_id}",
            context=f"delete_issue_comment {owner}/{repo}#{comment_id}",
        )
    except Exception as e:
        logger.warning("Could not delete comment %s on %s/%s: %s", comment_id, owner, repo, e)


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


async def delete_issue(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, number: int
) -> bool:
    """Hard-delete an artifact issue so the repo returns to its seeded state.

    REST has no issue-delete endpoint; the GraphQL ``deleteIssue`` mutation does the
    job and needs admin on the repo. It takes the issue's GraphQL node id rather than
    its number, so the issue is fetched first.

    Safe here even though the connector has no tombstone for a deleted issue: every
    mutation test owns a throw-away connector destroyed immediately after cleanup, so
    no later sync of that connector can observe the deletion, and the next run starts
    from a fresh checkpoint. Falls back to closing when the delete is refused, so an
    under-privileged token degrades instead of leaving the artifact behind entirely.
    """
    try:
        issue = await gh_call_with_retry(
            rest.get_json, f"/repos/{owner}/{repo}/issues/{number}",
            context=f"delete_issue lookup {owner}/{repo}#{number}",
        )
        node_id = (issue or {}).get("node_id")
        if not node_id:
            raise RuntimeError("issue payload carried no node_id")
        result = await gh_call_with_retry(
            rest.post_json, "/graphql",
            {
                "query": "mutation($id: ID!) { deleteIssue(input: {issueId: $id})"
                         " { clientMutationId } }",
                "variables": {"id": node_id},
            },
            context=f"deleteIssue {owner}/{repo}#{number}",
        )
        # GraphQL reports failures as HTTP 200 with an errors array.
        if isinstance(result, dict) and result.get("errors"):
            raise RuntimeError(str(result["errors"])[:200])
        return True
    except Exception as e:
        logger.warning(
            "Could not delete %s/%s#%s (%s); falling back to closing it",
            owner, repo, number, e,
        )
        try:
            await update_issue(rest, owner, repo, number, state="closed")
        except Exception as close_error:
            logger.warning(
                "Could not close %s/%s#%s either: %s", owner, repo, number, close_error,
            )
        return False



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
    """Close an artifact PR.

    GitHub exposes no way to DELETE a pull request: there is no REST endpoint and no
    ``deletePullRequest`` GraphQL mutation (only ``deletePullRequestReview`` and
    friends). Closing it and deleting its head branch is the most that can be
    reclaimed, so closed PRs are the one artifact that accumulates.
    """
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
    allow_paths: tuple[str, ...] = (),
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
        # ``allow_paths`` exists for the pinned PR fixture, whose single file lives on
        # its own branch and is rewritten (never accumulated) by each run.
        if not owns_path(change.path) and change.path not in allow_paths:
            raise AssertionError(
                f"refusing to commit {change.path!r}: outside this run's namespace "
                f"({GH_IT_PATH_ROOT}/{GH_IT_RUN_ID}/) and not in allow_paths. "
                "Concurrent runs share this branch."
            )

    # Concurrent runs share this branch. Retrying only the ref update would reuse a
    # parent that is no longer the tip, so the whole read-modify-write is repeated:
    # re-read the head, rebuild the tree on it, commit, update. GitHub rejects a
    # non-fast-forward ref update with 422, which gh_call_with_retry does not retry.
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return await _commit_once(rest, owner, repo, branch, changes, message)
        except GitHubHTTPError as e:
            if getattr(e, "status", None) != 422 or attempt == _MAX_ATTEMPTS - 1:
                raise
            delay = _BASE_DELAY_SEC * (2**attempt) * (0.5 + random.random())
            logger.warning(
                "commit to %s/%s@%s lost a race (HTTP 422); rebuilding on the new tip "
                "in %.1fs", owner, repo, branch, delay,
            )
            await asyncio.sleep(delay)
    raise AssertionError(f"commit_changes {owner}/{repo}@{branch}: exhausted retries")


async def _commit_once(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, branch: str,
    changes: list["FileChange"], message: str,
) -> str:
    """One read-modify-write attempt: head -> tree -> commit -> ref."""
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
        for issue in await list_issues(rest, owner, repo, state="all"):
            if _is_own_artifact(issue.get("title")):
                await delete_issue(rest, owner, repo, issue["number"])
    except Exception as e:
        logger.warning("TEARDOWN: could not close artifacts for run %s: %s", GH_IT_RUN_ID, e)


# Marker carried by every comment TC-INCR-PR-001 leaves on the pinned PR, so the
# sweep can recognise its own leftovers without touching a human's comment.
PINNED_PR_COMMENT_MARKER = "TC-INCR-PR-001 run"


async def sweep_pinned_pr_comments(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, number: int,
) -> None:
    """Delete IT comments stranded on the long-lived PR by runs that died.

    The test removes its own comment in ``finally``, but a run killed mid-flight (a
    dropped network, a cancelled CI job) never gets there. Because that PR is never
    deleted, those leftovers would otherwise accumulate one per crashed run forever.

    Age-gated like the artifact sweep so a concurrently running leg's comment is left
    alone, and matched on the marker so a human comment is never touched.
    """
    cutoff = time.time() - GH_IT_STALE_ARTIFACT_AGE_SEC
    try:
        comments = await gh_call_with_retry(
            rest.get_json, f"/repos/{owner}/{repo}/issues/{number}/comments",
            params={"per_page": 100},
            context=f"list pinned PR comments {owner}/{repo}#{number}",
        ) or []
    except Exception as e:
        logger.warning("SETUP: could not list comments on pinned PR #%s: %s", number, e)
        return

    for comment in comments:
        if PINNED_PR_COMMENT_MARKER not in (comment.get("body") or ""):
            continue
        created = _parse_iso8601(comment.get("created_at") or "")
        if created is None or created >= cutoff:
            continue
        await delete_issue_comment(rest, owner, repo, comment["id"])
        logger.info("SETUP: reaped stranded comment %s on pinned PR #%s",
                    comment["id"], number)


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
        for issue in await list_issues(rest, owner, repo, state="all"):
            if _is_stale_artifact(issue, cutoff):
                await delete_issue(rest, owner, repo, issue["number"])
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

    # Same non-fast-forward race as commit_changes: the branch is shared, so the tip
    # can move between reading it and updating the ref. Retry the whole sequence.
    for attempt in range(_MAX_ATTEMPTS):
        try:
            await _force_delete_once(rest, owner, repo, branch, paths)
            return
        except GitHubHTTPError as e:
            if getattr(e, "status", None) != 422 or attempt == _MAX_ATTEMPTS - 1:
                raise
            delay = _BASE_DELAY_SEC * (2**attempt) * (0.5 + random.random())
            logger.warning(
                "sweep commit to %s/%s@%s lost a race (HTTP 422); retrying in %.1fs",
                owner, repo, branch, delay,
            )
            await asyncio.sleep(delay)


async def _force_delete_once(
    rest: GitHubAsyncRESTClient, owner: str, repo: str, branch: str, paths: list[str],
) -> None:
    """One read-modify-write attempt for the namespace sweep."""
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
