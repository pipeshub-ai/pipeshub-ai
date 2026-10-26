"""Async REST data source for the GitHub *connector*.

Every method is a thin ``async def`` over one GitHub REST (or GraphQL)
endpoint via httpx — the same shape as the Jira/Confluence data sources.
No PyGithub, no executor threads, no lazy objects: returned ``GhObject``
wrappers hold the complete raw JSON, so reading an attribute can never
trigger a hidden HTTP request.

The toolset (``app/agents/actions/github``) keeps using the PyGithub-based
``github_.py``; this module serves the connector only.
"""

from __future__ import annotations

import base64
import difflib
import logging
import math
import os
import re
import time
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, quote, urlsplit

import httpx

from app.config.constants.http_status_code import HttpStatusCode
from app.sources.client.github.github import (
    GitHubResponse,
    github_retry_after_seconds,
    is_github_rate_limit,
)

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping

logger = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"
_API_VERSION = "2022-11-28"
_ISSUE_DEPENDENCIES_API_VERSION = "2026-03-10"
_TIMEOUT_SECONDS = 30.0
_SERVER_PAGE_SIZE = 100
_MAX_PAGE_SIZE = 100
_DEFAULT_PAGE_SIZE = 10
_LINK_LAST_RE = re.compile(r'[?&]page=(\d+)[^>]*>;\s*rel="last"')
_LINK_NEXT_RE = re.compile(r'<([^>]*)>;\s*rel="next"')

_TRUNCATION_MARKERS = ("diff too large", "binary file", "file is too large", "large diffs")

# A personal access token's 5,000/hr budget is the user's own: the gh CLI and
# every other tool on that account draw from it too. Below this share of an
# hourly pool PipesHub stops sending until the window resets, instead of
# draining it to zero. The per-minute Search pool is paced in the connector.
_RESERVED_RESOURCES = frozenset({"core", "graphql"})
_DEFAULT_RESERVE_PERCENT = 10
_RESERVE_PERCENT_ENV = "GITHUB_RATE_LIMIT_RESERVE_PERCENT"

# github.com/user-attachments is the web host, not the API: it answers an API token
# with 404 (verified against github.com, public repository included) and an
# anonymous request with a redirect to signed storage. The token is therefore never
# sent there; it would only make every download fail, and it has no business on a
# host other than the API.
_ATTACHMENT_HEADERS = {"Accept": "*/*"}

# Set while a record is streamed to a person who opened it: one request they
# asked for, which the reserve would refuse although GitHub would grant it.
# Those calls stop only once the pool is actually spent. Streaming to indexing
# is not exempt; it waits out the reset instead (StreamingHelper.stream_record).
_reserve_exempt: ContextVar[bool] = ContextVar("github_rate_limit_reserve_exempt", default=False)


@contextmanager
def rate_limit_reserve_exempt() -> Generator[None, None, None]:
    token = _reserve_exempt.set(True)
    try:
        yield
    finally:
        _reserve_exempt.reset(token)


class GitHubHTTPError(Exception):
    """Non-2xx REST response; ``status`` mirrors PyGithub's GithubException.

    ``headers`` and ``api_message`` are kept because a 403 is only told apart
    from a rate limit by GitHub's rate-limit headers and message.
    """

    def __init__(
        self,
        status: int,
        message: str,
        *,
        headers: Mapping[str, str] | None = None,
        api_message: str | None = None,
    ) -> None:
        self.status = status
        self.headers = dict(headers or {})
        self.api_message = api_message
        super().__init__(f"HTTP {status}: {message}")

    @property
    def rate_limited(self) -> bool:
        return is_github_rate_limit(self.status, self.headers, self.api_message)


class GitHubRateLimitReserveError(GitHubHTTPError):
    """Raised before a request is sent, so PipesHub leaves part of the
    user's hourly budget for their other tools on the same account."""

    def __init__(self, resource: str, remaining: int, limit: int, wait_seconds: int) -> None:
        reason = "reserve reached" if remaining > 0 else "exhausted"
        super().__init__(
            HttpStatusCode.TOO_MANY_REQUESTS.value,
            f"PipesHub paused GitHub {resource} requests with {remaining} of {limit} left "
            f"until the limit resets in {wait_seconds}s",
            headers={"retry-after": str(wait_seconds)},
            api_message=f"GitHub {resource} rate limit {reason}",
        )


def _resource_for(path: str) -> str:
    """The ``X-RateLimit-Resource`` pool GitHub will charge ``path`` to."""
    if path == "/graphql":
        return "graphql"
    if path.startswith("/search/code"):
        return "code_search"
    if path.startswith("/search/"):
        return "search"
    return "core"


def _api_message(response: httpx.Response) -> str | None:
    try:
        payload: object = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    message = cast("dict[str, object]", payload).get("message")
    return message if isinstance(message, str) else None


def _resolve_reserve_percent() -> int:
    raw = os.environ.get(_RESERVE_PERCENT_ENV)
    if not raw:
        return _DEFAULT_RESERVE_PERCENT
    try:
        value = int(raw)
        if not 0 <= value < 100:
            raise ValueError("must be 0-99")
        return value
    except ValueError:
        logger.warning(
            "Ignoring invalid %s=%r; using default %s%%",
            _RESERVE_PERCENT_ENV, raw, _DEFAULT_RESERVE_PERCENT,
        )
        return _DEFAULT_RESERVE_PERCENT


def _parse_iso(value: str) -> datetime | str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_cursor(link_header: str) -> str | None:
    """The ``after`` cursor of a ``Link: rel="next"`` URL, or None.

    Only the cursor is taken, never the URL itself, so the next request still
    goes to the configured API host.
    """
    match = _LINK_NEXT_RE.search(link_header or "")
    if not match:
        return None
    values = parse_qs(urlsplit(match.group(1)).query).get("after")
    return values[0] if values else None


def _wrap(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return GhObject(value)
    if isinstance(value, list):
        return [_wrap(v) for v in value]
    if isinstance(value, str) and key and (key.endswith("_at") or key == "date"):
        return _parse_iso(value)
    return value


class GhObject:
    """Attribute-style view over one REST JSON object.

    The payload is always complete — reading an attribute can never trigger
    an HTTP request. ``_rawData`` serves ``listing_payload``.
    """

    __slots__ = ("_data",)

    def __init__(self, data: dict) -> None:
        self._data = data

    @property
    def _rawData(self) -> dict:  # noqa: N802 - matches PyGithub's attribute
        return self._data

    @property
    def raw_data(self) -> dict:
        return self._data

    @property
    def decoded_content(self) -> bytes:
        """Base64-decoded body for contents/blob payloads (PyGithub parity)."""
        return base64.b64decode(self._data.get("content") or "")

    def __getattr__(self, name: str) -> Any:
        try:
            value = self._data[name]
        except KeyError:
            raise AttributeError(name) from None
        return _wrap(value, key=name)

    def __repr__(self) -> str:
        keys = ", ".join(list(self._data)[:6])
        return f"GhObject({keys})"


class GitHubAsyncRESTClient:
    """Thin httpx wrapper: live token, GitHub media headers, JSON in/out.

    The token is read per request from ``token_provider``, so a platform
    token rotation is picked up immediately — no client rebuild, no rebind.
    """

    def __init__(
        self,
        token_provider: Callable[[], str],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._reserve_percent = _resolve_reserve_percent()
        # resource -> (limit, lowest remaining seen, reset epoch) for the
        # window that is running.
        self._budgets: dict[str, tuple[int, int, int]] = {}

    @property
    def token(self) -> str:
        return self._token_provider()

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=_API_BASE,
                timeout=_TIMEOUT_SECONDS,
                follow_redirects=True,
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
        }
        if extra:
            headers.update(extra)
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        self._check_reserve(path)
        client = self._ensure_client()
        response = await client.request(
            method, path, params=params, json=json_body, headers=self._headers(headers),
        )
        self._record_budget(response.headers)
        if response.status_code >= 400:
            # Keep the body short: rate-limit/permission bodies are one-line
            # JSON messages; anything longer is noise in logs.
            raise GitHubHTTPError(
                response.status_code,
                response.text[:300],
                headers=response.headers,
                api_message=_api_message(response),
            )
        return response

    def _record_budget(self, headers: Mapping[str, str]) -> None:
        """Keep the lowest remaining count seen in the running window.

        GitHub serves requests from several regions, so a response in the same
        window can report more remaining than an earlier one, and overlapping
        calls finish out of order. Taking the latest value would reopen a pool
        the reserve had closed. Only a window whose reset has passed gives way
        to the next one; a response from an older window is ignored.
        """
        resource = headers.get("x-ratelimit-resource")
        limit = headers.get("x-ratelimit-limit", "")
        remaining = headers.get("x-ratelimit-remaining", "")
        reset = headers.get("x-ratelimit-reset", "")
        if not (resource and limit.isdigit() and remaining.isdigit() and reset.isdigit()):
            return
        seen = (int(limit), int(remaining), int(reset))
        current = self._budgets.get(resource)
        if current is None:
            self._budgets[resource] = seen
            return
        current_limit, current_remaining, current_reset = current
        if seen[2] < current_reset:
            return
        if time.time() < current_reset:
            self._budgets[resource] = (current_limit, min(current_remaining, seen[1]), current_reset)
        else:
            self._budgets[resource] = seen

    def _check_reserve(self, path: str) -> None:
        """Refuse to spend the last ``_reserve_percent`` of an hourly pool.

        Under ``rate_limit_reserve_exempt``, or with the reserve set to 0, only
        a spent pool is refused: GitHub would reject that request anyway.
        """
        exempt = _reserve_exempt.get()
        resource = _resource_for(path)
        budget = self._budgets.get(resource) if resource in _RESERVED_RESOURCES else None
        if budget is None:
            return
        limit, remaining, reset = budget
        wait_seconds = reset - int(time.time())
        floor = 0 if exempt else math.ceil(limit * self._reserve_percent / 100)
        if wait_seconds <= 0 or remaining > floor:
            return
        raise GitHubRateLimitReserveError(resource, remaining, limit, wait_seconds)

    async def get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return (await self.request("GET", path, params=params, headers=headers)).json()

    async def post_json(self, path: str, json_body: dict[str, Any]) -> Any:
        return (await self.request("POST", path, json_body=json_body)).json()


class GitHubAsyncDataSource:
    """The connector's GitHub API surface — async, endpoint-per-method."""

    def __init__(
        self,
        external_client: object,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._rest = GitHubAsyncRESTClient(
            lambda: external_client.get_client().get_token(),  # type: ignore[attr-defined]
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._rest.aclose()

    # ------------------------------------------------------------------
    # Repositories & discovery
    # ------------------------------------------------------------------

    async def get_repo(self, owner: str, repo: str) -> GitHubResponse:
        return await self._get_wrapped(f"/repos/{owner}/{repo}")

    async def get_repo_by_id(self, repo_id: int) -> GitHubResponse:
        """Resolve a repository from its stable numeric ID (survives rename/transfer)."""
        return await self._get_wrapped(f"/repositories/{repo_id}")

    async def list_org_repos(
        self,
        org: str,
        type: str = "all",  # noqa: A002 - keeps the PyGithub-era signature
        per_page: int | None = None,
        page: int | None = None,
        sort: str | None = None,
    ) -> GitHubResponse:
        """One page when page args are given, the full listing otherwise.

        ``sort`` (created/updated/pushed/full_name) makes GitHub's page order
        deterministic — required when pages from several orgs are merged and
        re-sliced locally (the repo picker); omit for full walks.
        """
        params: dict[str, str] = {"type": type}
        if sort:
            params["sort"] = sort
        return await self._listing(f"/orgs/{org}/repos", params, per_page, page)

    async def list_user_repos(
        self,
        user: str | None = None,
        type: str = "owner",  # noqa: A002
        per_page: int | None = None,
        page: int | None = None,
        sort: str | None = None,
    ) -> GitHubResponse:
        """Authenticated listing when ``user`` is omitted; a named user's
        public repos otherwise (matching the PyGithub-era behavior)."""
        path = f"/users/{user}/repos" if user else "/user/repos"
        params: dict[str, str] = {"type": type}
        if sort:
            params["sort"] = sort
        return await self._listing(path, params, per_page, page)

    async def list_user_orgs(self) -> GitHubResponse:
        """Orgs of the token-owning user; requires the ``read:org`` scope."""
        return await self._listing("/user/orgs", None, None, None)

    async def search_repositories(
        self, query: str, per_page: int | None = None, page: int | None = None,
    ) -> GitHubResponse:
        """Search repositories. Default 10 per page, max 100."""
        try:
            _per_page, _page = self._clamp_page_args(per_page, page)
            payload = await self._rest.get_json(
                "/search/repositories",
                {"q": query, "per_page": _per_page, "page": _page},
            )
            return GitHubResponse(success=True, data=_wrap(payload.get("items") or []))
        except Exception as e:
            return self._err(e)

    async def get_branch(self, owner: str, repo: str, branch: str) -> GitHubResponse:
        return await self._get_wrapped(
            f"/repos/{owner}/{repo}/branches/{quote(branch, safe='')}"
        )

    # ------------------------------------------------------------------
    # Code indexing (Git Data / Compare)
    # ------------------------------------------------------------------

    async def get_git_tree(
        self, owner: str, repo: str, tree_sha: str, recursive: bool = False
    ) -> GitHubResponse:
        """``recursive=True`` returns the full flat tree in one call (subject to
        GitHub's 100,000-entry / 7MB truncation limit — check ``.truncated``)."""
        params = {"recursive": "1"} if recursive else None
        return await self._get_wrapped(
            f"/repos/{owner}/{repo}/git/trees/{tree_sha}", params=params,
        )

    async def get_git_blob(self, owner: str, repo: str, sha: str) -> GitHubResponse:
        """Git Data API blob — serves content the Contents API caps at 1 MB."""
        return await self._get_wrapped(f"/repos/{owner}/{repo}/git/blobs/{sha}")

    async def get_file_contents(
        self, owner: str, repo: str, path: str, ref: str | None = None,
    ) -> GitHubResponse:
        """Contents payload (base64 body); a directory path yields a list."""
        params = {"ref": ref} if ref else None
        return await self._get_wrapped(
            f"/repos/{owner}/{repo}/contents/{quote(path, safe='/')}", params=params,
        )

    async def compare_commits(
        self, owner: str, repo: str, base: str, head: str
    ) -> GitHubResponse:
        """``.files`` is capped at 300 entries and ``.status`` may be 'diverged'
        on a force-push — callers must fall back to a full sync either way."""
        basehead = f"{quote(base, safe='')}...{quote(head, safe='')}"
        return await self._get_wrapped(f"/repos/{owner}/{repo}/compare/{basehead}")

    async def list_commits_first_and_last(
        self, owner: str, repo: str, path: str | None = None,
    ) -> GitHubResponse:
        """``(newest, oldest)`` commit for a path in exactly 2 requests.

        ``per_page=1`` makes the ``Link: rel="last"`` page number equal the
        total commit count, so the oldest commit is one direct page fetch —
        no history walking.
        """
        try:
            params: dict[str, Any] = {"per_page": 1}
            if path:
                params["path"] = path
            response = await self._rest.request(
                "GET", f"/repos/{owner}/{repo}/commits", params=params,
            )
            newest_page = response.json()
            if not newest_page:
                return GitHubResponse(success=True, data=(None, None))
            newest = _wrap(newest_page[0])
            match = _LINK_LAST_RE.search(response.headers.get("link", ""))
            if not match:
                return GitHubResponse(success=True, data=(newest, newest))
            params["page"] = int(match.group(1))
            oldest_page = await self._rest.get_json(
                f"/repos/{owner}/{repo}/commits", params=params,
            )
            oldest = _wrap(oldest_page[0]) if oldest_page else newest
            return GitHubResponse(success=True, data=(newest, oldest))
        except Exception as e:
            return self._err(e)

    # ------------------------------------------------------------------
    # Issues
    # ------------------------------------------------------------------

    async def list_issues(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        labels: Sequence[str] | None = None,
        assignee: str | None = None,
        since: datetime | None = None,
        per_page: int | None = None,
        page: int | None = None,
        sort: str | None = None,
        direction: str | None = None,
        after: str | None = None,
    ) -> GitHubResponse:
        """One page of a repository's issues (pull requests included, as GitHub lists them).

        GitHub refuses ``page`` numbers past 10,000 items here (422, "use cursor
        based pagination"). Pass the previous page's ``next_cursor`` as ``after``
        to read further; ``page`` is then not sent.
        """
        params: dict[str, Any] = {"state": state}
        if labels:
            params["labels"] = ",".join(labels)
        if assignee:
            params["assignee"] = assignee
        if since:
            params["since"] = _iso(since)
        if sort:
            params["sort"] = sort
        if direction:
            params["direction"] = direction
        return await self._listing(
            f"/repos/{owner}/{repo}/issues", params, per_page, page, after=after,
        )

    async def get_issue(self, owner: str, repo: str, number: int) -> GitHubResponse:
        return await self._get_wrapped(f"/repos/{owner}/{repo}/issues/{number}")

    async def list_issue_comments(
        self, owner: str, repo: str, number: int, since: datetime | None = None,
    ) -> GitHubResponse:
        params = {"since": _iso(since)} if since else None
        return await self._listing(
            f"/repos/{owner}/{repo}/issues/{number}/comments", params, None, None,
        )

    async def list_issue_blocking(
        self, owner: str, repo: str, number: int
    ) -> GitHubResponse:
        """Issues the given issue blocks — raw dicts, matching the old surface."""
        try:
            data = await self._rest.get_json(
                f"/repos/{owner}/{repo}/issues/{number}/dependencies/blocking",
                headers={"X-GitHub-Api-Version": _ISSUE_DEPENDENCIES_API_VERSION},
            )
            return GitHubResponse(success=True, data=data or [])
        except Exception as e:
            return self._err(e)

    # ------------------------------------------------------------------
    # Pull requests
    # ------------------------------------------------------------------

    async def list_pulls(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        head: str | None = None,
        base: str | None = None,
        sort: str | None = None,
        direction: str | None = None,
        per_page: int | None = None,
        page: int | None = None,
    ) -> GitHubResponse:
        """This endpoint has no ``since`` param, so incremental sync pages
        ``sort='updated', direction='desc'`` and stops at the checkpoint."""
        params: dict[str, Any] = {"state": state}
        for key, value in (("head", head), ("base", base), ("sort", sort), ("direction", direction)):
            if value:
                params[key] = value
        return await self._listing(f"/repos/{owner}/{repo}/pulls", params, per_page, page)

    async def get_pull(self, owner: str, repo: str, number: int) -> GitHubResponse:
        return await self._get_wrapped(f"/repos/{owner}/{repo}/pulls/{number}")

    async def get_pull_commits(self, owner: str, repo: str, number: int) -> GitHubResponse:
        return await self._listing(
            f"/repos/{owner}/{repo}/pulls/{number}/commits", None, None, None,
        )

    async def get_pull_reviews(self, owner: str, repo: str, number: int) -> GitHubResponse:
        return await self._listing(
            f"/repos/{owner}/{repo}/pulls/{number}/reviews", None, None, None,
        )

    async def get_pull_review_comments(
        self, owner: str, repo: str, number: int
    ) -> GitHubResponse:
        return await self._listing(
            f"/repos/{owner}/{repo}/pulls/{number}/comments", None, None, None,
        )

    async def get_pull_file_changes(
        self,
        owner: str,
        repo: str,
        number: int,
        fetch_full_content: bool = True,
        max_changes_per_file: int = 10000,
        max_diff_lines: int = 10000,
        context_lines: int = 2,
    ) -> GitHubResponse:
        """PR file changes with complete diffs and safety limits.

        GitHub truncates large patches out of the listing; for those, the full
        unified diff is regenerated from the base/head file contents. Files
        over ``max_changes_per_file`` total changes are kept but their patch is
        replaced with an explanatory note; regenerated diffs are cut at
        ``max_diff_lines``.
        """
        try:
            raw_files = await self._list_all(f"/repos/{owner}/{repo}/pulls/{number}/files")
            if not fetch_full_content:
                return GitHubResponse(success=True, data=_wrap(raw_files))

            pr_shas: tuple[str, str] | None = None
            enhanced: list[GhObject] = []
            for raw in raw_files:
                filename = raw.get("filename", "")
                status = raw.get("status", "")
                patch = raw.get("patch") or ""
                additions = raw.get("additions") or 0
                deletions = raw.get("deletions") or 0
                total_changes = additions + deletions

                if total_changes > max_changes_per_file:
                    data = dict(raw)
                    data["patch"] = (
                        f"[SKIPPED: File has {total_changes:,} total changes "
                        f"(+{additions:,} -{deletions:,}), exceeding safety limit of "
                        f"{max_changes_per_file:,}. This is likely a complete rewrite, "
                        f"generated file, or vendor dependency. Manual review recommended.]"
                    )
                    data["_skipped_large_file"] = True
                    data["_skip_reason"] = "excessive_changes"
                    enhanced.append(GhObject(data))
                    continue

                is_truncated = (
                    (total_changes > 0 and not patch)
                    or any(marker in patch.lower() for marker in _TRUNCATION_MARKERS)
                    or (total_changes > 1000 and len(patch) < 500)
                )
                if not is_truncated or status in ("removed", "renamed"):
                    enhanced.append(GhObject(raw))
                    continue

                if pr_shas is None:
                    pr = await self._rest.get_json(f"/repos/{owner}/{repo}/pulls/{number}")
                    pr_shas = (
                        (pr.get("base") or {}).get("sha") or "",
                        (pr.get("head") or {}).get("sha") or "",
                    )
                full_diff = await self._generate_full_diff_for_file(
                    owner, repo, pr_shas[0], pr_shas[1], filename, status,
                    max_diff_lines, context_lines,
                )
                if full_diff:
                    data = dict(raw)
                    data["patch"] = full_diff
                    data["_full_content_fetched"] = True
                    if "[TRUNCATED]" in full_diff:
                        data["_diff_truncated"] = True
                    enhanced.append(GhObject(data))
                else:
                    enhanced.append(GhObject(raw))
            return GitHubResponse(success=True, data=enhanced)
        except Exception as e:
            return self._err(e)

    async def _generate_full_diff_for_file(
        self,
        owner: str,
        repo: str,
        base_sha: str,
        head_sha: str,
        filename: str,
        status: str,
        max_diff_lines: int,
        context_lines: int,
    ) -> str | None:
        if not base_sha or not head_sha:
            return None
        try:
            base_content = "" if status == "added" else await self._text_content_at_ref(
                owner, repo, filename, base_sha,
            )
            head_content = "" if status == "removed" else await self._text_content_at_ref(
                owner, repo, filename, head_sha,
            )
            diff_iterator = difflib.unified_diff(
                base_content.splitlines(keepends=True) if base_content else [],
                head_content.splitlines(keepends=True) if head_content else [],
                fromfile=f"a/{filename}",
                tofile=f"b/{filename}",
                lineterm="",
                n=context_lines,
            )
            diff_lines: list[str] = []
            for i, line in enumerate(diff_iterator):
                if i >= max_diff_lines:
                    remaining = sum(1 for _ in diff_iterator)
                    diff_lines.append(
                        f"\n... [TRUNCATED: {remaining} more lines omitted to prevent "
                        f"context overflow. This diff exceeds {max_diff_lines} lines. "
                        f"Consider reviewing the file directly on GitHub.] ...\n"
                    )
                    break
                diff_lines.append(line)
            return "".join(diff_lines)
        except Exception as e:
            logger.debug("Could not generate full diff for %s: %s", filename, e)
            return None

    async def _text_content_at_ref(self, owner: str, repo: str, path: str, ref: str) -> str:
        try:
            payload = await self._rest.get_json(
                f"/repos/{owner}/{repo}/contents/{quote(path, safe='/')}", {"ref": ref},
            )
            if isinstance(payload, list):  # directory
                return ""
            return base64.b64decode(payload.get("content") or "").decode(
                "utf-8", errors="replace",
            )
        except Exception as e:
            logger.debug("Could not fetch %s at %s: %s", path, ref, e)
            return ""

    # ------------------------------------------------------------------
    # Users / orgs / teams / permissions
    # ------------------------------------------------------------------

    async def get_authenticated(self) -> GitHubResponse:
        """Authenticated user — always a complete payload (never lazy)."""
        return await self._get_wrapped("/user")

    async def get_user(self, login: str) -> GitHubResponse:
        """Full public profile (name/email included when public)."""
        return await self._get_wrapped(f"/users/{quote(login, safe='')}")

    async def list_org_members(self, org: str, role: str = "all") -> GitHubResponse:
        return await self._listing(f"/orgs/{org}/members", {"role": role}, None, None)

    async def list_org_outside_collaborators(self, org: str) -> GitHubResponse:
        return await self._listing(f"/orgs/{org}/outside_collaborators", None, None, None)

    async def list_team_members(self, org: str, team_slug: str) -> GitHubResponse:
        return await self._listing(
            f"/orgs/{org}/teams/{quote(team_slug, safe='')}/members", None, None, None,
        )

    async def list_repo_teams(self, owner: str, repo: str) -> GitHubResponse:
        """Each team's ``.permission`` reflects its role on *this* repo."""
        return await self._listing(f"/repos/{owner}/{repo}/teams", None, None, None)

    async def list_collaborators(
        self, owner: str, repo: str, affiliation: str | None = None
    ) -> GitHubResponse:
        """Each user's ``.permissions`` booleans reflect its role on *this* repo."""
        params = {"affiliation": affiliation} if affiliation else None
        return await self._listing(
            f"/repos/{owner}/{repo}/collaborators", params, None, None,
        )

    # ------------------------------------------------------------------
    # Rate limit & GraphQL
    # ------------------------------------------------------------------

    async def get_rate_limit(self) -> GitHubResponse:
        """Free endpoint — does not consume quota. ``rate.reset`` is epoch seconds."""
        return await self._get_wrapped("/rate_limit")

    async def graphql_query(
        self, query: str, variables: dict | None = None
    ) -> GitHubResponse:
        """GraphQL — its own point budget, separate from the REST core quota.
        Returns the raw ``data`` dict (callers parse it themselves)."""
        try:
            payload = await self._rest.post_json(
                "/graphql", {"query": query, "variables": variables or {}},
            )
            if payload.get("errors"):
                return GitHubResponse(success=False, error=str(payload["errors"])[:1000])
            return GitHubResponse(success=True, data=payload.get("data") or {})
        except Exception as e:
            return self._err(e)

    # ------------------------------------------------------------------
    # Attachment / image streaming (direct downloads, not the REST API)
    # ------------------------------------------------------------------

    async def get_img_bytes(self, image_url: str, max_bytes: int | None = None) -> GitHubResponse:
        """Download an image, refusing (success=False) one over ``max_bytes``.

        Streamed, so an oversized body is abandoned at the limit instead of being
        read whole into memory first: any upload can be embedded with image
        syntax, a 100 MB video included.

        Sent without the token: see ``_ATTACHMENT_HEADERS``.
        """
        headers = dict(_ATTACHMENT_HEADERS)
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=_TIMEOUT_SECONDS) as client:
                async with client.stream("GET", image_url, headers=headers) as resp:
                    resp.raise_for_status()
                    declared = resp.headers.get("Content-Length")
                    if max_bytes is not None and declared and declared.isdigit() and int(declared) > max_bytes:
                        return GitHubResponse(success=False, error=f"Image at {image_url} is over {max_bytes} bytes")
                    body = bytearray()
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        body.extend(chunk)
                        if max_bytes is not None and len(body) > max_bytes:
                            return GitHubResponse(success=False, error=f"Image at {image_url} is over {max_bytes} bytes")
                    return GitHubResponse(success=True, data=bytes(body))
        except httpx.HTTPStatusError as e:
            return GitHubResponse(
                success=False,
                error=f"HTTP {e.response.status_code} fetching image from {image_url}",
                status_code=e.response.status_code,
                exception_type=type(e).__name__,
            )
        except Exception as e:
            return GitHubResponse(
                success=False,
                error=f"Error fetching image from {image_url}: {str(e)}",
                exception_type=type(e).__name__,
            )

    async def get_attachment_files_content(
        self, weburl: str, max_bytes: int | None = None,
    ) -> AsyncGenerator[bytes, None]:
        """Stream raw bytes for an attachment from its web URL.

        Yields 64 KB chunks so a large attachment never buffers whole in memory
        (GitHub permits uploads up to 25 MB). Raises ``httpx.HTTPStatusError``
        on non-2xx rather than returning a response envelope, so the caller's
        eager-first-chunk wrapper can surface it as a clean HTTP error.
        """
        async with httpx.AsyncClient(follow_redirects=True, timeout=_TIMEOUT_SECONDS) as client:
            async with client.stream("GET", weburl, headers=dict(_ATTACHMENT_HEADERS)) as response:
                response.raise_for_status()
                if max_bytes is not None:
                    declared = response.headers.get("Content-Length")
                    if declared and int(declared) > max_bytes:
                        raise ValueError(
                            f"Attachment at {weburl} is {declared} bytes, over the {max_bytes}-byte limit"
                        )
                streamed = 0
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    streamed += len(chunk)
                    if max_bytes is not None and streamed > max_bytes:
                        raise ValueError(
                            f"Attachment at {weburl} exceeded the {max_bytes}-byte limit while streaming"
                        )
                    yield chunk

    # ------------------------------------------------------------------
    # Shared plumbing
    # ------------------------------------------------------------------

    async def _get_wrapped(
        self, path: str, params: dict[str, Any] | None = None
    ) -> GitHubResponse:
        try:
            return GitHubResponse(
                success=True, data=_wrap(await self._rest.get_json(path, params)),
            )
        except Exception as e:
            return self._err(e)

    async def _listing(
        self,
        path: str,
        params: dict[str, Any] | None,
        per_page: int | None,
        page: int | None,
        *,
        after: str | None = None,
    ) -> GitHubResponse:
        """One page when page args are given, otherwise every page (100 at a time).

        A single page also carries ``next_cursor``, the ``after`` cursor GitHub
        offers in its ``Link`` header (None when it offers none). Given ``after``,
        the page is addressed by that cursor instead of by number.
        """
        try:
            if per_page is None and page is None and after is None:
                return GitHubResponse(
                    success=True, data=_wrap(await self._list_all(path, params)),
                )
            _per_page, _page = self._clamp_page_args(per_page, page)
            query = dict(params or {})
            query["per_page"] = _per_page
            if after is not None:
                query["after"] = after
            else:
                query["page"] = _page
            response = await self._rest.request("GET", path, params=query)
            batch = response.json()
            return GitHubResponse(
                success=True, data=_wrap(batch if isinstance(batch, list) else []),
                next_cursor=_next_cursor(response.headers.get("link", "")),
            )
        except Exception as e:
            return self._err(e)

    async def _list_all(
        self, path: str, params: dict[str, Any] | None = None
    ) -> list[dict]:
        items: list[dict] = []
        page = 1
        while True:
            query = dict(params or {})
            query.update({"per_page": _SERVER_PAGE_SIZE, "page": page})
            batch = await self._rest.get_json(path, query)
            if not isinstance(batch, list) or not batch:
                break
            items.extend(batch)
            if len(batch) < _SERVER_PAGE_SIZE:
                break
            page += 1
        return items

    @staticmethod
    def _clamp_page_args(per_page: int | None, page: int | None) -> tuple[int, int]:
        return (
            _DEFAULT_PAGE_SIZE if per_page is None else min(_MAX_PAGE_SIZE, max(1, per_page)),
            1 if page is None else max(1, page),
        )

    @staticmethod
    def _err(e: Exception) -> GitHubResponse:
        status = getattr(e, "status", None)
        rate_limited = isinstance(e, GitHubHTTPError) and e.rate_limited
        return GitHubResponse(
            success=False,
            error=str(e)[:500],
            # A rate-limited 403 is reported as the 429 it is: every caller
            # (stream status mapping, structural-denial checks) reads 403 as
            # "not allowed", which a rate limit is not.
            status_code=HttpStatusCode.TOO_MANY_REQUESTS.value if rate_limited else status,
            exception_type=type(e).__name__,
            api_message=getattr(e, "api_message", None),
            retry_after_seconds=github_retry_after_seconds(getattr(e, "headers", None)),
        )
