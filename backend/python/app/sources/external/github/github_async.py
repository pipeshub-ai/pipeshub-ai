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
import re
from collections.abc import AsyncGenerator, Callable, Sequence
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from app.sources.client.github.github import GitHubResponse

logger = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"
_API_VERSION = "2022-11-28"
_ISSUE_DEPENDENCIES_API_VERSION = "2026-03-10"
_TIMEOUT_SECONDS = 30.0
_SERVER_PAGE_SIZE = 100
_MAX_PAGE_SIZE = 100
_DEFAULT_PAGE_SIZE = 10
_LINK_LAST_RE = re.compile(r'[?&]page=(\d+)[^>]*>;\s*rel="last"')

_TRUNCATION_MARKERS = ("diff too large", "binary file", "file is too large", "large diffs")


class GitHubHTTPError(Exception):
    """Non-2xx REST response; ``status`` mirrors PyGithub's GithubException."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(f"HTTP {status}: {message}")


def _parse_iso(value: str) -> datetime | str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        client = self._ensure_client()
        response = await client.request(
            method, path, params=params, json=json_body, headers=self._headers(headers),
        )
        if response.status_code >= 400:
            # Keep the body short: rate-limit/permission bodies are one-line
            # JSON messages; anything longer is noise in logs.
            raise GitHubHTTPError(response.status_code, response.text[:300])
        return response

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
    ) -> GitHubResponse:
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
            f"/repos/{owner}/{repo}/issues", params, per_page, page,
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

    async def get_img_bytes(self, image_url: str) -> GitHubResponse:
        headers = {"Authorization": f"Bearer {self._rest.token}", "Accept": "*/*"}
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=_TIMEOUT_SECONDS) as client:
                resp = await client.get(image_url, headers=headers)
                resp.raise_for_status()
                return GitHubResponse(success=True, data=resp.content)
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
        headers = {
            "Authorization": f"Bearer {self._rest.token}",
            "Accept": "application/vnd.github+json",
        }
        async with httpx.AsyncClient(follow_redirects=True, timeout=_TIMEOUT_SECONDS) as client:
            async with client.stream("GET", weburl, headers=headers) as response:
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
    ) -> GitHubResponse:
        """One page when page args are given, otherwise every page (100 at a time)."""
        try:
            if per_page is None and page is None:
                return GitHubResponse(
                    success=True, data=_wrap(await self._list_all(path, params)),
                )
            _per_page, _page = self._clamp_page_args(per_page, page)
            query = dict(params or {})
            query.update({"per_page": _per_page, "page": _page})
            batch = await self._rest.get_json(path, query)
            return GitHubResponse(
                success=True, data=_wrap(batch if isinstance(batch, list) else []),
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
        return GitHubResponse(
            success=False,
            error=str(e)[:500],
            status_code=getattr(e, "status", None),
            exception_type=type(e).__name__,
        )
