"""WP-26 GIT-FLOW (UI-4): the repository picker's listings, read with the person's own GitHub sign-in.

The listing reads GitHub (here a fake transport) with the token as a Bearer and shows names, visibility, default
branches and update times only; the routes answer 404 for a connection that is not a GitHub one the caller may use,
409 when they are not signed in, and 502 when GitHub refuses the sign-in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.agents.actions.github import repository_picker
from app.agents.actions.github.repository_picker import (
    GitHubListingFailed,
    github_api_base,
    list_branches,
    list_repositories,
    repository_name_ok,
)
from tests.support.bound_org import bind_test_org

if TYPE_CHECKING:
    from collections.abc import Iterator

TOKEN = "gho_fake0123456789"
# GitHub's own API, named so a GITHUB_API_URL left in the environment cannot change which links count.
GITHUB = "https://api.github.com"


def _repo(name: str, *, private: bool = False, pushed: str = "2026-10-30T10:00:00Z") -> dict[str, Any]:
    return {
        "full_name": name,
        "html_url": f"https://github.com/{name}",
        "private": private,
        "default_branch": "main",
        "pushed_at": pushed,
        "owner": {"login": name.split("/")[0]},
        "permissions": {"admin": True},
    }


REPOS = [_repo("acme/widgets", private=True), _repo("acme/widgets-docs"), _repo("octo-dev/sandbox")]


class FakeGitHub:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"message": "Bad credentials"})
        path = request.url.path.removeprefix("/api/v3")
        if path == "/user/repos":
            page = int(request.url.params.get("page", "1"))
            return httpx.Response(200, json=REPOS if page == 1 else [])
        if path == "/repos/acme/widgets":
            return httpx.Response(200, json=_repo("acme/widgets"))
        if path == "/repos/pipeshub-ai/pipeshub-ai":
            return httpx.Response(200, json=_repo("pipeshub-ai/pipeshub-ai"))
        if path == "/repos/acme/widgets/branches":
            return httpx.Response(200, json=[{"name": "release/1.x"}, {"name": "main"}])
        return httpx.Response(404, json={"message": "Not Found"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


class TestTheListings:
    async def test_rl_1_repositories_are_read_with_the_token_and_shown_by_name_only(self) -> None:
        github = FakeGitHub()
        listing = await list_repositories(TOKEN, api="https://api.example", transport=github.transport)
        assert listing.wire() == {
            "repositories": [
                {"fullName": "acme/widgets", "url": "https://github.com/acme/widgets", "private": True,
                 "defaultBranch": "main", "updatedAt": "2026-10-30T10:00:00Z"},
                {"fullName": "acme/widgets-docs", "url": "https://github.com/acme/widgets-docs", "private": False,
                 "defaultBranch": "main", "updatedAt": "2026-10-30T10:00:00Z"},
                {"fullName": "octo-dev/sandbox", "url": "https://github.com/octo-dev/sandbox", "private": False,
                 "defaultBranch": "main", "updatedAt": "2026-10-30T10:00:00Z"},
            ],
            "page": 1,
            "hasMore": False,
        }
        request = github.requests[0]
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.url.params["sort"] == "pushed"

    async def test_rl_2_a_search_matches_owner_and_name_in_any_case(self) -> None:
        listing = await list_repositories(TOKEN, search="WIDGETS", api="https://api.example", transport=FakeGitHub().transport)
        assert [r["fullName"] for r in listing.repositories] == ["acme/widgets", "acme/widgets-docs"]
        assert listing.has_more is False

    async def test_rl_3_branches_list_the_default_first_and_mark_it(self) -> None:
        branches = await list_branches(TOKEN, "acme/widgets", api="https://api.example", transport=FakeGitHub().transport)
        assert branches == [{"name": "main", "default": True}, {"name": "release/1.x", "default": False}]

    async def test_rl_4_a_refused_sign_in_and_an_unseen_repository_are_told_apart(self) -> None:
        github = FakeGitHub()
        github.status = 401
        with pytest.raises(GitHubListingFailed) as failed:
            await list_repositories(TOKEN, api="https://api.example", transport=github.transport)
        assert failed.value.status == 401
        with pytest.raises(repository_picker.RepositoryNotFound):
            await list_branches(TOKEN, "acme/hidden", api="https://api.example", transport=FakeGitHub().transport)

    @pytest.mark.parametrize(
        "pasted",
        [
            "https://github.com/pipeshub-ai/pipeshub-ai/",
            "https://github.com/pipeshub-ai/pipeshub-ai.git",
            "github.com/pipeshub-ai/pipeshub-ai/tree/main/backend",
            "git@github.com:pipeshub-ai/pipeshub-ai.git",
            "  pipeshub-ai/pipeshub-ai  ",
        ],
    )
    async def test_rl_6_a_pasted_link_finds_a_repository_the_sign_in_sees_but_is_not_a_member_of(self, pasted: str) -> None:
        github = FakeGitHub()
        listing = await list_repositories(TOKEN, search=pasted, api=GITHUB, transport=github.transport)
        assert [r["fullName"] for r in listing.repositories] == ["pipeshub-ai/pipeshub-ai"]
        assert listing.has_more is False
        assert github.requests[0].url.path.removeprefix("/api/v3") == "/repos/pipeshub-ai/pipeshub-ai"
        assert github.requests[0].headers["Authorization"] == f"Bearer {TOKEN}"

    async def test_rl_7_a_partial_owner_name_falls_back_to_the_name_search(self) -> None:
        listing = await list_repositories(TOKEN, search="https://github.com/acme/widg", api=GITHUB, transport=FakeGitHub().transport)
        assert [r["fullName"] for r in listing.repositories] == ["acme/widgets", "acme/widgets-docs"]

    async def test_rl_8_a_link_to_another_host_is_never_looked_up(self) -> None:
        github = FakeGitHub()
        listing = await list_repositories(TOKEN, search="https://gitlab.com/acme/widgets", api=GITHUB, transport=github.transport)
        assert listing.repositories == []
        assert all(r.url.path == "/user/repos" for r in github.requests)

    async def test_rl_9_an_enterprise_api_takes_links_on_its_own_host(self) -> None:
        github = FakeGitHub()
        listing = await list_repositories(
            TOKEN, search="https://ghe.example.com/pipeshub-ai/pipeshub-ai", api="https://ghe.example.com/api/v3",
            transport=github.transport,
        )
        assert [r["fullName"] for r in listing.repositories] == ["pipeshub-ai/pipeshub-ai"]

    def test_rl_5_names_and_the_api_base(self) -> None:
        assert repository_name_ok("acme/widgets") and repository_name_ok("octo-dev/sandbox.js")
        assert not any(repository_name_ok(n) for n in ("acme", "acme/../x", "a/b/c", "/x", "acme/ widgets"))
        assert github_api_base({}) == "https://api.github.com"
        assert github_api_base({"GITHUB_API_URL": "http://pxh-fake-saas:8090/api/v3/"}) == "http://pxh-fake-saas:8090/api/v3"


def _config(instance_type: str = "github", auth: dict[str, Any] | None = None) -> Any:  # noqa: ANN401
    config_service = AsyncMock()

    async def get_config(path: str, default: Any = None, use_cache: bool = True, **_: Any) -> Any:  # noqa: ANN401 - raise_on_error
        del use_cache
        if "toolset-instances" in path:
            return [{"_id": "gh-1", "orgId": "o1", "instanceName": "GitHub", "toolsetType": instance_type}]
        if path.endswith("/services/toolsets/gh-1/u1"):
            return auth
        return default

    config_service.get_config = get_config
    return config_service


SIGNED_IN = {"isAuthenticated": True, "credentials": {"access_token": TOKEN}}


class TestTheRoutes:
    async def test_rr_1_the_callers_own_token_lists_their_repositories(self) -> None:
        from app.api.routes.toolsets import list_github_repositories

        listed = AsyncMock(return_value=repository_picker.RepositoryListing([], 1, False))
        with (
            patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}),
            patch.object(repository_picker, "list_repositories", listed),
        ):
            answer = await list_github_repositories("gh-1", MagicMock(), search="wid", page=1, config_service=_config(auth=SIGNED_IN))
        assert answer == {"repositories": [], "page": 1, "hasMore": False}
        assert listed.await_args.args == (TOKEN,) and listed.await_args.kwargs == {"search": "wid", "page": 1}

    async def test_rr_2_not_github_404_not_signed_in_409_github_refuses_502(self) -> None:
        from app.api.routes.toolsets import (
            list_github_branches,
            list_github_repositories,
        )

        with patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}):
            with pytest.raises(HTTPException) as other:
                await list_github_repositories("gh-1", MagicMock(), search=None, page=1, config_service=_config("jira", SIGNED_IN))
            assert other.value.status_code == 404
            with pytest.raises(HTTPException) as unknown:
                await list_github_repositories("gh-9", MagicMock(), search=None, page=1, config_service=_config(auth=SIGNED_IN))
            assert unknown.value.status_code == 404
            with pytest.raises(HTTPException) as signed_out:
                await list_github_repositories(
                    "gh-1", MagicMock(), search=None, page=1, config_service=_config(auth={"isAuthenticated": False})
                )
            assert signed_out.value.status_code == 409
            with (
                patch.object(repository_picker, "list_branches", AsyncMock(side_effect=GitHubListingFailed(401))),
                pytest.raises(HTTPException) as refused,
            ):
                await list_github_branches("gh-1", MagicMock(), repository="acme/widgets", config_service=_config(auth=SIGNED_IN))
            assert refused.value.status_code == 502 and "Reconnect GitHub" in str(refused.value.detail)
            with pytest.raises(HTTPException) as malformed:
                await list_github_branches("gh-1", MagicMock(), repository="acme/../x", config_service=_config(auth=SIGNED_IN))
            assert malformed.value.status_code == 400


@pytest.fixture(autouse=True)
def _bound_org() -> Iterator[str]:
    """The auth middleware binds the verified token's org; these tests call the route functions directly."""
    yield from bind_test_org("o1")
