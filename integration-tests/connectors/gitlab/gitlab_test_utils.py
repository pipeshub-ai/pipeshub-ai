# pyright: ignore-file

"""GitLab API helpers for the GitLab connector integration tests.

Two halves.

*Reads* — discovery against the live group. Fixture shapes (reference issue,
incident, task, attachment, merged MR, nested code path) are discovered rather than
pinned, so a re-provisioned fixture needs no code change.

*Writes* — everything the incremental cases need. The connector's own data source
wraps the python-gitlab SDK rather than a thin REST client, so these talk to the
REST API directly over httpx with the same ``PRIVATE-TOKEN`` header.

Code mutations use ``POST /repository/commits`` with an ``actions`` array, which
applies every file change in ONE commit. That matters for renames: a single commit
carrying a ``move`` action is what makes ``compare`` report a rename, rather than
the delete+add pair two commits would produce.
"""

import asyncio
import logging
import os
import random
import re
import time
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Optional

import httpx

from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import wait_for_sync_completion  # type: ignore[import-not-found]
from helper.oauth_token_helper import inject_access_token  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from connectors.gitlab.constants import (  # type: ignore[import-not-found]
    GL_ACCESS_GUEST,
    GL_IT_ARTIFACT_RE,
    GL_IT_PATH_ROOT,
    GL_IT_RUN_ID,
    GL_IT_STALE_ARTIFACT_AGE_SEC,
    GL_SYNC_WAIT_SEC,
    PINNED_MR_COMMENT_MARKER,
    owns_path,
)

logger = logging.getLogger("gitlab-test-utils")

# 429 is a secondary rate limit; 5xx are transient. 403 is NOT retried — GitLab uses
# it for genuine permission denials and retrying only burns budget.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BASE_DELAY_SEC = 1.0
_GITLAB_MAX_PER_PAGE = 100

# Attachment markup the connector recognises (constants.py: UPLOAD_PATTERN). The
# image forms are stripped first because images are inlined and produce no record.
_MD_IMAGE_RE = re.compile(r"!\[(.*?)\]\((.*?)\)")
_MD_LINK_RE = re.compile(r"\[(.*?)\]\((.*?)\)")
_UPLOAD_HREF_RE = re.compile(r"^/uploads/[a-f0-9]{32}/[^)\s]+$")


def enc(path: str) -> str:
    """URL-encode a project or group path for use as an ``:id`` path segment."""
    return urllib.parse.quote(str(path), safe="")


class GitLabRestClient:
    """Thin httpx wrapper over the GitLab REST API."""

    def __init__(self, token: str, instance_url: str) -> None:
        self._token = token
        self._base = instance_url.rstrip("/") + "/api/v4"
        self._client: httpx.AsyncClient | None = None

    def _ensure(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._base, timeout=60.0)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def request(
        self, method: str, path: str, *, params: dict | None = None,
        json_body: Any = None, files: Any = None,
    ) -> httpx.Response:
        response = await self._ensure().request(
            method, path, params=params, json=json_body, files=files,
            headers={"PRIVATE-TOKEN": self._token},
        )
        if response.status_code >= 400:
            raise GitLabHTTPError(response.status_code, response.text[:300])
        return response

    async def get_json(self, path: str, params: dict | None = None) -> Any:
        return (await self.request("GET", path, params=params)).json()

    async def post_json(self, path: str, body: Any) -> Any:
        return (await self.request("POST", path, json_body=body)).json()


class GitLabHTTPError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(f"HTTP {status}: {message}")


async def gl_call(
    fn: Callable[..., Any], *args: Any,
    context: str = "gitlab call", retry_server_errors: bool = True, **kwargs: Any,
) -> Any:
    """Await ``fn`` with bounded retries on transient GitLab failures.

    ``retry_server_errors=False`` for non-idempotent creates: a retried 5xx that
    actually committed would leave a duplicate issue or MR on the shared project.
    """
    last: Optional[Exception] = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return await fn(*args, **kwargs)
        except GitLabHTTPError as e:
            retryable = e.status in _RETRYABLE_STATUSES and (
                retry_server_errors or e.status == 429
            )
            if not retryable or attempt == _MAX_ATTEMPTS - 1:
                raise
            last = e
            delay = _BASE_DELAY_SEC * (2**attempt) * (0.5 + random.random())
            logger.warning("%s: HTTP %s (attempt %s/%s); retrying in %.1fs",
                           context, e.status, attempt + 1, _MAX_ATTEMPTS, delay)
            await asyncio.sleep(delay)
    raise AssertionError(f"{context}: exhausted retries") from last


async def walk_pages(
    rest: GitLabRestClient, path: str, *, params: dict | None = None,
    per_page: int = _GITLAB_MAX_PER_PAGE, context: str = "listing",
) -> list[dict[str, Any]]:
    """Every page of a listing, not just the first.

    A short page is the end-of-listing signal, which is only meaningful when the
    requested size is one GitLab will honour — it caps ``per_page`` at 100, so a
    larger request would make page one look short and truncate the listing.
    """
    if per_page < 1:
        raise ValueError(f"per_page must be >= 1, got {per_page}")
    per_page = min(per_page, _GITLAB_MAX_PER_PAGE)
    collected: list[dict[str, Any]] = []
    page = 1
    while True:
        rows = await gl_call(
            rest.get_json, path,
            params={**(params or {}), "per_page": per_page, "page": page},
            context=f"{context} (page {page})",
        ) or []
        collected.extend(rows)
        if len(rows) < per_page:
            return collected
        page += 1


# ---------------------------------------------------------------------------
# Reads / discovery
# ---------------------------------------------------------------------------

async def get_project(rest: GitLabRestClient, project: str) -> dict[str, Any]:
    return await gl_call(rest.get_json, f"/projects/{enc(project)}",
                         context=f"get_project {project}")


async def list_issues(rest: GitLabRestClient, project: str, *, state: str = "all") -> list[dict]:
    return await walk_pages(rest, f"/projects/{enc(project)}/issues",
                            params={"state": state}, context=f"list_issues {project}")


async def get_issue(rest: GitLabRestClient, project: str, iid: int) -> dict[str, Any]:
    return await gl_call(rest.get_json, f"/projects/{enc(project)}/issues/{iid}",
                         context=f"get_issue {project}#{iid}")


async def list_merge_requests(rest: GitLabRestClient, project: str, *, state: str = "all") -> list[dict]:
    return await walk_pages(rest, f"/projects/{enc(project)}/merge_requests",
                            params={"state": state}, context=f"list_mrs {project}")


async def get_merge_request(rest: GitLabRestClient, project: str, iid: int) -> dict[str, Any]:
    return await gl_call(rest.get_json, f"/projects/{enc(project)}/merge_requests/{iid}",
                         context=f"get_mr {project}!{iid}")


async def list_project_members(rest: GitLabRestClient, project: str) -> list[dict]:
    """Effective member list, including inherited group members.

    An EMPTY list here is the trap this suite guards against: GitLab returns
    ``200 OK`` with ``[]`` — not 403 — when the token's role is too low to read
    members on a private project, and the connector then silently falls back to
    creator-only permissions.
    """
    return await walk_pages(rest, f"/projects/{enc(project)}/members/all",
                            context=f"list_members {project}")


async def list_group_members(rest: GitLabRestClient, group: str) -> list[dict]:
    return await walk_pages(rest, f"/groups/{enc(group)}/members/all",
                            context=f"list_group_members {group}")


async def get_user(rest: GitLabRestClient, user_id: int) -> dict[str, Any]:
    """``GET /users/:id`` — the only source of ``public_email``; the members API
    does not include it."""
    return await gl_call(rest.get_json, f"/users/{user_id}", context=f"get_user {user_id}")


async def get_group(rest: GitLabRestClient, group: str) -> dict[str, Any]:
    """``GET /groups/:path`` — the source of a group record group's name and web_url."""
    return await gl_call(rest.get_json, f"/groups/{enc(group)}",
                         context=f"get_group {group}")


async def branch_head_sha(rest: GitLabRestClient, project: str, branch: str) -> str:
    """HEAD commit sha of ``branch`` — what the code-repository checkpoint stores."""
    data = await gl_call(rest.get_json,
                         f"/projects/{enc(project)}/repository/branches/{branch}",
                         context=f"get_branch {project}@{branch}")
    return str((data.get("commit") or {}).get("id") or "")


async def get_tree(rest: GitLabRestClient, project: str, ref: str = "main") -> list[dict]:
    return await walk_pages(
        rest, f"/projects/{enc(project)}/repository/tree",
        params={"recursive": "true", "ref": ref}, context=f"get_tree {project}",
    )


async def list_notes(rest: GitLabRestClient, project: str, kind: str, iid: int) -> list[dict]:
    """User notes only — GitLab mixes system notes ("changed the description") into
    the same listing, and those are not comments."""
    rows = await walk_pages(rest, f"/projects/{enc(project)}/{kind}/{iid}/notes",
                            context=f"list_notes {project} {kind}#{iid}")
    return [n for n in rows if not n.get("system")]


def blob_paths(tree: list[dict[str, Any]]) -> list[str]:
    return [e["path"] for e in tree if e.get("type") == "blob" and e.get("path")]


def tree_dirs(tree: list[dict[str, Any]]) -> set[str]:
    """Distinct directories, derived from blob paths.

    The connector builds folder records from the parent prefixes of the files it
    syncs, so deriving the expected set the same way keeps the counts comparable.
    """
    dirs: set[str] = set()
    for path in blob_paths(tree):
        parts = path.split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
    return dirs


def dedupe_members(members: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Members keyed by id, keeping the highest access level.

    ``/members/all`` returns a row per source, so a user who is both a direct project
    member and an inherited group member appears twice. The connector collapses those
    keeping the highest level, and the ACL assertions have to compare against the same
    collapsed set.
    """
    best: dict[int, dict[str, Any]] = {}
    for member in members:
        member_id = member.get("id")
        if member_id is None:
            continue
        current = best.get(member_id)
        if current is None or (member.get("access_level") or 0) > (current.get("access_level") or 0):
            best[member_id] = member
    return best


def child_groups_for_level(access_level: int) -> set[str]:
    """Which child record groups a member at ``access_level`` is granted.

    The project group itself is deliberately absent: the connector grants it to every
    member *before* it looks at the level, so even a level-0 or Minimal (5) member
    holds a project-level grant. Only the children are gated.

    Guest (10) gets work items but not code or merge requests. From Planner (15) up
    the member gets all three — which is why the bound is ``>= 15`` and not ``>= 20``:
    Planner, a role that cannot read code on GitLab, is granted the code repository
    here.
    """
    if access_level == GL_ACCESS_GUEST:
        return {"work-items"}
    if access_level >= 15:
        return {"work-items", "merge-requests", "code-repository"}
    return set()


def syncable_blob_paths(tree: list[dict[str, Any]]) -> list[str]:
    """Blob paths the connector will actually turn into records.

    Two filters sit between the tree listing and a record: dotfiles are dropped by
    name, and ``should_index_code_file`` drops ignored paths and skipped roles
    (lockfiles, vendored trees). Counting raw blobs instead would over-count.
    """
    from app.modules.parsers.code_parser.file_role import (  # noqa: PLC0415
        should_index_code_file,
    )

    keep: list[str] = []
    for path in blob_paths(tree):
        name = path.rsplit("/", 1)[-1]
        if name.startswith("."):
            continue
        should_index, _role = should_index_code_file(path, name)
        if should_index:
            keep.append(path)
    return keep


def find_code_record(
    records: list[dict[str, Any]], repo_path: str,
) -> Optional[dict[str, Any]]:
    """The graph record for one repo path.

    Matched on the tail of ``externalRecordId`` rather than rebuilt: the id is a
    GraphQL ``webPath`` whose ref segment is GitLab's to choose, and a test that
    guessed the ref would fail for a reason that has nothing to do with the
    connector.
    """
    suffix = f"/{repo_path}"
    for record in records:
        external_id = str(record.get("externalRecordId") or "")
        if "/-/blob/" in external_id and external_id.endswith(suffix):
            return record
    return None


def deepest_blob_path(tree: list[dict[str, Any]], *, min_depth: int = 3) -> Optional[str]:
    candidates = [p for p in blob_paths(tree) if p.count("/") >= min_depth - 1]
    return max(candidates, key=lambda p: p.count("/")) if candidates else None


def extensionless_blob_path(tree: list[dict[str, Any]]) -> Optional[str]:
    for path in sorted(blob_paths(tree)):
        if "." not in path.rsplit("/", 1)[-1]:
            return path
    return None


def discover_reference_issue(issues: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """A plain issue for the TICKET property assertions.

    Prefers one that actually carries labels and an assignee so the comparison
    covers those fields rather than a row of Nones.
    """
    if not issues:
        return None
    return max(issues, key=lambda i: (bool(i.get("labels")), bool(i.get("assignees"))))


def discover_issue_of_type(issues: list[dict[str, Any]], issue_type: str) -> Optional[dict]:
    """First issue whose GitLab ``issue_type`` matches (incident / task)."""
    for issue in issues:
        if (issue.get("issue_type") or "").lower() == issue_type.lower():
            return issue
    return None


def discover_merged_mr(mrs: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    for mr in mrs:
        if mr.get("state") == "merged":
            return mr
    return None


def first_attachment_href(body: str) -> Optional[str]:
    """A GitLab upload link that becomes a FileRecord, or None.

    Mirrors the connector's ordering: image markdown is stripped first, because an
    image is inlined into the blocks and deliberately produces no record. Only a
    plain ``[text](/uploads/<32-hex>/name)`` link yields one.
    """
    stripped = _MD_IMAGE_RE.sub("", body or "")
    for _text, href in _MD_LINK_RE.findall(stripped):
        href = href.strip()
        if _UPLOAD_HREF_RE.match(href):
            return href
    return None


async def discover_attachment(
    rest: GitLabRestClient, project: str, items: list[dict[str, Any]], kind: str,
) -> Optional[tuple[dict[str, Any], str]]:
    """First (item, upload href) among issues or MRs, checking body then comments."""
    for item in items:
        bodies = [item.get("description") or ""]
        bodies += [n.get("body") or "" for n in await list_notes(rest, project, kind, item["iid"])]
        for body in bodies:
            href = first_attachment_href(body)
            if href:
                return item, href
    return None


# ---------------------------------------------------------------------------
# Writes — issues, merge requests, comments
# ---------------------------------------------------------------------------

async def create_issue(rest: GitLabRestClient, project: str, *, title: str,
                       description: str = "", **kw: Any) -> dict[str, Any]:
    return await gl_call(
        rest.post_json, f"/projects/{enc(project)}/issues",
        {"title": title, "description": description, **kw},
        context=f"create_issue {project}", retry_server_errors=False,
    )


async def update_issue(rest: GitLabRestClient, project: str, iid: int, **fields: Any) -> dict:
    resp = await gl_call(rest.request, "PUT", f"/projects/{enc(project)}/issues/{iid}",
                         json_body=fields, context=f"update_issue {project}#{iid}")
    return resp.json()


async def delete_issue(rest: GitLabRestClient, project: str, iid: int) -> bool:
    """Hard-delete an artifact issue so the project returns to its seeded state.

    GitLab exposes a real DELETE for issues (Owner-only), unlike GitHub where it
    needs a GraphQL mutation. Safe here because every mutation test owns a
    throw-away connector destroyed immediately after, so no later sync of that
    connector can observe the deletion.
    """
    try:
        await gl_call(rest.request, "DELETE", f"/projects/{enc(project)}/issues/{iid}",
                      context=f"delete_issue {project}#{iid}")
        return True
    except Exception as e:
        logger.warning("Could not delete %s#%s (%s); closing instead", project, iid, e)
        try:
            await update_issue(rest, project, iid, state_event="close")
        except Exception as close_error:
            logger.warning("Could not close %s#%s either: %s", project, iid, close_error)
        return False


async def add_note(rest: GitLabRestClient, project: str, kind: str, iid: int, body: str) -> dict:
    return await gl_call(
        rest.post_json, f"/projects/{enc(project)}/{kind}/{iid}/notes", {"body": body},
        context=f"add_note {project} {kind}#{iid}", retry_server_errors=False,
    )


async def delete_note(rest: GitLabRestClient, project: str, kind: str, iid: int, note_id: int) -> None:
    """Remove a comment this run added to a long-lived fixture, so it cannot
    accumulate one per run."""
    try:
        await gl_call(rest.request, "DELETE",
                      f"/projects/{enc(project)}/{kind}/{iid}/notes/{note_id}",
                      context=f"delete_note {project} {kind}#{iid}")
    except Exception as e:
        logger.warning("Could not delete note %s on %s %s#%s: %s", note_id, project, kind, iid, e)


async def create_merge_request(rest: GitLabRestClient, project: str, *, title: str,
                               source_branch: str, target_branch: str = "main",
                               description: str = "") -> dict[str, Any]:
    return await gl_call(
        rest.post_json, f"/projects/{enc(project)}/merge_requests",
        {"title": title, "source_branch": source_branch,
         "target_branch": target_branch, "description": description},
        context=f"create_mr {project}", retry_server_errors=False,
    )


async def update_merge_request(rest: GitLabRestClient, project: str, iid: int, **fields: Any) -> dict:
    resp = await gl_call(rest.request, "PUT",
                         f"/projects/{enc(project)}/merge_requests/{iid}",
                         json_body=fields, context=f"update_mr {project}!{iid}")
    return resp.json()


async def ensure_branch(rest: GitLabRestClient, project: str, branch: str, ref: str = "main") -> None:
    try:
        await gl_call(rest.get_json, f"/projects/{enc(project)}/repository/branches/{branch}",
                      context=f"get_branch {branch}")
    except GitLabHTTPError:
        await gl_call(rest.post_json, f"/projects/{enc(project)}/repository/branches",
                      {"branch": branch, "ref": ref}, context=f"create_branch {branch}",
                      retry_server_errors=False)


async def commit_actions(
    rest: GitLabRestClient, project: str, branch: str, message: str,
    actions: list[dict[str, Any]], *, allow_paths: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Apply every action as ONE commit; returns the commit.

    A single commit is what makes a rename legible — git records the move in one
    tree transition, so ``compare`` reports it as a rename with the previous path
    rather than as a delete plus an add.

    Guard: every mutated path must be inside this run's namespace. Concurrent runs
    share this branch, and touching a path outside it would corrupt another run.
    """
    for action in actions:
        for key in ("file_path", "previous_path"):
            path = action.get(key)
            if path and not owns_path(path) and path not in allow_paths:
                raise AssertionError(
                    f"refusing to commit {path!r}: outside this run's namespace "
                    f"({GL_IT_PATH_ROOT}/{GL_IT_RUN_ID}/) and not in allow_paths"
                )
    return await gl_call(
        rest.post_json, f"/projects/{enc(project)}/repository/commits",
        {"branch": branch, "commit_message": message, "actions": actions},
        context=f"commit {project}@{branch}", retry_server_errors=False,
    )


async def blob_sha_for_path(rest: GitLabRestClient, project: str, path: str,
                            ref: str = "main") -> Optional[str]:
    """Current blob id of ``path`` — what the connector stores as ``file_hash``."""
    for entry in await get_tree(rest, project, ref):
        if entry.get("path") == path and entry.get("type") == "blob":
            return entry.get("id")
    return None


# ---------------------------------------------------------------------------
# Artifact hygiene
# ---------------------------------------------------------------------------

def _is_own_artifact(title: Optional[str]) -> bool:
    return bool(title) and bool(GL_IT_ARTIFACT_RE.match(title)) and GL_IT_RUN_ID in title


def _parse_iso8601(value: str) -> Optional[float]:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def _is_stale_artifact(item: dict[str, Any], cutoff: float) -> bool:
    title = item.get("title") or ""
    if not GL_IT_ARTIFACT_RE.match(title) or GL_IT_RUN_ID in title:
        return False
    created = _parse_iso8601(item.get("created_at") or "")
    return created is not None and created < cutoff


async def reap_own_artifacts(rest: GitLabRestClient, project: str, branch: str) -> None:
    """Remove everything this run still owns. Best-effort — a cleanup failure must
    never fail an otherwise passing suite."""
    try:
        tree = await get_tree(rest, project, branch)
        mine = [p for p in blob_paths(tree) if owns_path(p)]
        if mine:
            await commit_actions(
                rest, project, branch,
                f"IT cleanup: remove {GL_IT_PATH_ROOT}/{GL_IT_RUN_ID}",
                [{"action": "delete", "file_path": p} for p in mine],
            )
    except Exception as e:
        logger.warning("TEARDOWN: could not clean code namespace for %s: %s", GL_IT_RUN_ID, e)

    try:
        for issue in await list_issues(rest, project):
            if _is_own_artifact(issue.get("title")):
                await delete_issue(rest, project, issue["iid"])
        for mr in await list_merge_requests(rest, project, state="opened"):
            if _is_own_artifact(mr.get("title")):
                await update_merge_request(rest, project, mr["iid"], state_event="close")
    except Exception as e:
        logger.warning("TEARDOWN: could not reap artifacts for %s: %s", GL_IT_RUN_ID, e)


async def sweep_stale_artifacts(rest: GitLabRestClient, project: str, branch: str) -> None:
    """Reclaim artifacts left by runs that died before their cleanup ran."""
    cutoff = time.time() - GL_IT_STALE_ARTIFACT_AGE_SEC
    try:
        for issue in await list_issues(rest, project):
            if _is_stale_artifact(issue, cutoff):
                await delete_issue(rest, project, issue["iid"])
        for mr in await list_merge_requests(rest, project, state="opened"):
            if _is_stale_artifact(mr, cutoff):
                await update_merge_request(rest, project, mr["iid"], state_event="close")
    except Exception as e:
        logger.warning("SETUP: stale issue/MR sweep failed (continuing): %s", e)

    try:
        tree = await get_tree(rest, project, branch)
        prefix = f"{GL_IT_PATH_ROOT}/"
        stale = [
            p for p in blob_paths(tree)
            if p.startswith(prefix) and not owns_path(p)
        ]
        if stale:
            logger.info("SETUP: sweeping %s leaked code path(s)", len(stale))
            await gl_call(
                rest.post_json, f"/projects/{enc(project)}/repository/commits",
                {"branch": branch,
                 "commit_message": "IT cleanup: reap leaked integration-test namespaces",
                 "actions": [{"action": "delete", "file_path": p} for p in stale]},
                context="sweep delete", retry_server_errors=False,
            )
    except Exception as e:
        logger.warning("SETUP: stale code sweep failed (continuing): %s", e)


async def sweep_pinned_mr_comments(rest: GitLabRestClient, project: str, iid: int) -> None:
    """Delete IT comments stranded on the long-lived MR by runs that died.

    That MR is never deleted, so leftovers would otherwise accumulate one per
    crashed run. Age-gated so a concurrently running leg's comment is left alone,
    and marker-matched so a human comment is never touched.
    """
    cutoff = time.time() - GL_IT_STALE_ARTIFACT_AGE_SEC
    try:
        for note in await list_notes(rest, project, "merge_requests", iid):
            if PINNED_MR_COMMENT_MARKER not in (note.get("body") or ""):
                continue
            created = _parse_iso8601(note.get("created_at") or "")
            if created is not None and created < cutoff:
                await delete_note(rest, project, "merge_requests", iid, note["id"])
                logger.info("SETUP: reaped stranded comment %s on pinned MR !%s",
                            note["id"], iid)
    except Exception as e:
        logger.warning("SETUP: could not sweep pinned MR comments: %s", e)


# ---------------------------------------------------------------------------
# PipesHub connector lifecycle
# ---------------------------------------------------------------------------

def sync_filters(**values: Any) -> dict[str, Any]:
    """Wrap filter fields into ``config.filters.sync.values``.

    The endpoint stores the request ``filters`` verbatim, so the payload must
    already carry the nesting — a flat ``{"project_ids": ...}`` is written to the
    wrong path and silently ignored, syncing everything instead of failing.
    """
    return {"sync": {"values": values}}


def list_filter(operator: str, values: list[str]) -> dict[str, Any]:
    return {"operator": operator, "type": "list", "value": values}


def bool_filter(value: bool) -> dict[str, Any]:
    """A boolean filter field.

    ``operator`` and ``type`` are both mandatory: ``FilterCollection.from_dict``
    *skips* any entry missing either one and only logs a warning, so a bare
    ``{"value": False}`` leaves the filter absent and ``is_enabled`` falls back to its
    default of True — the filter silently does nothing.
    """
    return {"operator": "is", "type": "boolean", "value": value}


def indexing_filters(**values: Any) -> dict[str, Any]:
    """Wrap filter fields into ``config.filters.indexing.values``."""
    return {"indexing": {"values": values}}


def create_gitlab_connector(
    pipeshub_client: PipeshubClient, *, token: str, name: str,
    instance_url: str, filters: Optional[dict[str, Any]] = None,
) -> str:
    """Register a GitLab connector and authenticate it. Returns connector_id.

    ``config`` must be non-empty: the create route only persists the ``auth`` block
    (carrying ``authType: OAUTH``) when a config was supplied, and
    ``build_from_services`` raises without it.
    """
    config: dict[str, Any] = {"auth": {"instanceUrl": instance_url}}
    if filters:
        config["filters"] = filters
    instance = pipeshub_client.create_connector(
        connector_type="GitLab",   # exact registry name; lookup is not normalised
        instance_name=name, scope="team", config=config, auth_type="OAUTH",
    )
    assert instance.connector_id, "Connector must have a valid ID"
    inject_access_token(instance.connector_id, token)
    return instance.connector_id


async def teardown_connector(
    pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol, connector_id: str,
) -> None:
    pipeshub_client.toggle_sync(connector_id, enable=False)
    pipeshub_client.delete_connector(connector_id)
    pipeshub_client.wait(25)
    await graph_provider.assert_all_records_cleaned(
        connector_id, timeout=int(os.getenv("INTEGRATION_GRAPH_CLEANUP_TIMEOUT", "300")),
    )


@asynccontextmanager
async def dedicated_connector(
    pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol, *,
    token: str, name: str, instance_url: str, filters: dict[str, Any],
    min_records: int | None = None, timeout: int = GL_SYNC_WAIT_SEC,
) -> AsyncIterator[str]:
    """A throw-away connector for one mutation or filter test."""
    connector_id = create_gitlab_connector(
        pipeshub_client, token=token, name=name,
        instance_url=instance_url, filters=filters,
    )
    try:
        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id,
            min_records=min_records, timeout=timeout,
        )
        yield connector_id
    finally:
        # Log, never raise: this runs in a ``finally``, so an exception here would
        # replace the real assertion error from the test body.
        try:
            await teardown_connector(pipeshub_client, graph_provider, connector_id)
        except Exception as e:
            logger.error("dedicated connector %s (%s) cleanup leaked: %s", name, connector_id, e)


async def resolve_app_user_emails(
    graph_provider: GraphProviderProtocol, connector_id: str, source_ids: list[str],
) -> dict[str, str]:
    """GitLab numeric user id -> the email the connector bound it to.

    GitLab hides emails by default (the connector reads ``public_email``), so which
    principals resolved is not predictable from the source side; reading the map
    back from the AppUser nodes is the only option.
    """
    resolved: dict[str, str] = {}
    for source_id in source_ids:
        user = await graph_provider.get_user_by_source_id(
            source_user_id=str(source_id), connector_id=connector_id,
        )
        if user is not None and getattr(user, "email", None):
            resolved[str(source_id)] = user.email
    return resolved
