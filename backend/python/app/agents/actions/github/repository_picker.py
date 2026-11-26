"""What the repository picker lists (GIT-FLOW, WP-26): the repositories a person's own GitHub sign-in can see, and
one repository's branches.

Read with the person's toolset token in the connectors service, which holds it; only names, visibility, default
branches and update times leave it. ``GITHUB_API_URL`` names another GitHub API (a GitHub Enterprise Server's
``https://ghe.example.com/api/v3``, or the e2e lanes' fake); unset, GitHub's own.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote, urlsplit

import httpx

if TYPE_CHECKING:
    from collections.abc import Mapping

GITHUB_API_ENV = "GITHUB_API_URL"
DEFAULT_GITHUB_API = "https://api.github.com"
# One page of the picker; GitHub's own page cap is 100.
PAGE_SIZE = 30
_GITHUB_PAGE = 100
# A search reads this many of GitHub's pages at most: GitHub has no name filter on /user/repos.
_SEARCH_PAGES = 5
_TIMEOUT_S = 15.0
_REPOSITORY = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,99}/[A-Za-z0-9_][A-Za-z0-9._-]{0,99}")
_SCP_LINK = re.compile(r"git@([^:/\s]+):(.+)")


class GitHubListingFailed(Exception):
    """GitHub could not be reached, or refused the sign-in (``status`` is its answer; 0: no answer)."""

    def __init__(self, status: int) -> None:
        super().__init__(f"GitHub answered {status}" if status else "GitHub could not be reached")
        self.status = status


class RepositoryNotFound(Exception):
    """The sign-in cannot see that repository."""


@dataclass(frozen=True, slots=True)
class RepositoryListing:
    repositories: list[dict[str, Any]]
    page: int
    has_more: bool

    def wire(self) -> dict[str, Any]:
        return {"repositories": self.repositories, "page": self.page, "hasMore": self.has_more}


def github_api_base(env: Mapping[str, str] | None = None) -> str:
    """The GitHub API this deployment talks to."""
    value = (env if env is not None else os.environ).get(GITHUB_API_ENV, "").strip()
    return (value or DEFAULT_GITHUB_API).rstrip("/")


def repository_name_ok(repository: str) -> bool:
    """``owner/name``, each a plain GitHub name."""
    return _REPOSITORY.fullmatch(repository) is not None and ".." not in repository


def _web_host(api: str) -> str:
    """The host people's links name: github.com for GitHub's own API, the API's host for an Enterprise Server."""
    host = (urlsplit(api).hostname or "").lower()
    return "github.com" if host == "api.github.com" else host


def _searched_name(search: str, api: str) -> str | None:
    """``owner/name`` (maybe partial) from a pasted link or name; None for a link to another host or plain words.

    People paste the repository's address from the browser or a clone command, while the listing matches
    ``owner/name``, so the two never met.
    """
    text = search.strip()
    scp = _SCP_LINK.fullmatch(text)
    if scp:
        host, path = scp.group(1), scp.group(2)
    elif "://" in text or text.lower().startswith(_web_host(api) + "/"):
        parts = urlsplit(text if "://" in text else "https://" + text)
        host, path = parts.hostname or "", parts.path
    elif "/" in text:
        return text.strip("/").lower() or None
    else:
        return None
    if host.lower() != _web_host(api):
        return ""
    segments = [p for p in path.split("/") if p][:2]
    if not segments:
        return ""
    if len(segments) == 2:
        segments[1] = segments[1].removesuffix(".git")
    return "/".join(segments).lower()


def _shown(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    name, url, branch = raw.get("full_name"), raw.get("html_url"), raw.get("default_branch")
    if not isinstance(name, str) or not isinstance(url, str) or not isinstance(branch, str):
        return None
    updated = raw.get("pushed_at") or raw.get("updated_at")
    return {
        "fullName": name,
        "url": url,
        "private": raw.get("private") is True,
        "defaultBranch": branch,
        "updatedAt": updated if isinstance(updated, str) else None,
    }


async def _get(client: httpx.AsyncClient, path: str, params: dict[str, str] | None = None) -> Any:  # noqa: ANN401
    try:
        response = await client.get(path, params=params)
    except httpx.HTTPError:
        raise GitHubListingFailed(0) from None
    if response.status_code == httpx.codes.NOT_FOUND:
        raise RepositoryNotFound
    if response.status_code != httpx.codes.OK:
        raise GitHubListingFailed(response.status_code)
    return response.json()


def _client(token: str, api: str, transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=api,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=_TIMEOUT_S,
        transport=transport,
        follow_redirects=False,
    )


async def list_repositories(
    token: str,
    *,
    search: str | None = None,
    page: int = 1,
    api: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> RepositoryListing:
    """The repositories the sign-in sees, most recently pushed first; ``search`` matches owner/name (any case).

    A search naming one repository (``owner/name`` or a pasted link) first asks GitHub for it directly: a public
    repository, or one of an organization the person is not a member of, is visible to the sign-in but never
    in ``/user/repos``.
    """
    base = api or github_api_base()
    wanted = (search or "").strip().lower()
    named = _searched_name(search or "", base)
    if named == "":
        return RepositoryListing([], page, False)
    if named is not None:
        wanted = named
    async with _client(token, base, transport) as client:
        if named and page == 1 and repository_name_ok(named):
            try:
                direct = _shown(await _get(client, "/repos/" + "/".join(quote(p, safe="") for p in named.split("/"))))
            except RepositoryNotFound:
                direct = None
            if direct:
                return RepositoryListing([direct], 1, False)
        seen: list[dict[str, Any]] = []
        exhausted = False
        pages = _SEARCH_PAGES if wanted else 1
        start = 1 if wanted else page
        for github_page in range(start, start + pages):
            raw = await _get(
                client,
                "/user/repos",
                {
                    "sort": "pushed",
                    "affiliation": "owner,collaborator,organization_member",
                    "per_page": str(_GITHUB_PAGE if wanted else PAGE_SIZE),
                    "page": str(github_page),
                },
            )
            items = [r for r in (_shown(i) for i in cast("list[Mapping[str, Any]]", raw if isinstance(raw, list) else [])) if r]
            seen.extend(r for r in items if not wanted or wanted in r["fullName"].lower())
            if len(items) < (_GITHUB_PAGE if wanted else PAGE_SIZE):
                exhausted = True
                break
    if not wanted:
        return RepositoryListing(seen, page, not exhausted)
    begin = (page - 1) * PAGE_SIZE
    return RepositoryListing(seen[begin : begin + PAGE_SIZE], page, len(seen) > begin + PAGE_SIZE or not exhausted)


async def list_branches(
    token: str,
    repository: str,
    *,
    api: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[dict[str, Any]]:
    """The repository's branches, its default branch first and marked."""
    if not repository_name_ok(repository):
        raise RepositoryNotFound
    path = "/repos/" + "/".join(quote(part, safe="") for part in repository.split("/"))
    async with _client(token, api or github_api_base(), transport) as client:
        meta = await _get(client, path)
        default = meta.get("default_branch") if isinstance(meta, dict) else None
        raw = await _get(client, f"{path}/branches", {"per_page": str(_GITHUB_PAGE)})
    names = [b.get("name") for b in raw if isinstance(b, dict)] if isinstance(raw, list) else []
    branches = [n for n in names if isinstance(n, str) and n]
    if isinstance(default, str) and default not in branches:
        branches.append(default)
    ordered = sorted(branches, key=lambda n: (n != default, n))
    return [{"name": n, "default": n == default} for n in ordered]


__all__ = [
    "DEFAULT_GITHUB_API",
    "GITHUB_API_ENV",
    "PAGE_SIZE",
    "GitHubListingFailed",
    "RepositoryListing",
    "RepositoryNotFound",
    "github_api_base",
    "list_branches",
    "list_repositories",
    "repository_name_ok",
]
