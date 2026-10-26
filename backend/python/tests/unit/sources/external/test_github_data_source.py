"""Unit tests for ``app.sources.external.github.github_.GitHubDataSource``.

Focuses on client binding and ``rebind_client`` -- the hook that keeps the
data source's SDK/token in sync after an OAuth token rotation. PyGithub binds
``Auth.Token`` at construction time, so ``GitHubClientViaToken.set_token``
rebuilds a brand new ``Github`` instance rather than mutating one in place;
without ``rebind_client`` the data source would keep serving requests with a
stale SDK instance and an expired token indefinitely.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from github import Github

from app.sources.external.github.github_ import GitHubDataSource


def _make_wrapper_client(sdk: Github, token: str = "test-token") -> MagicMock:
    wrapper = MagicMock()
    wrapper.get_sdk.return_value = sdk
    wrapper.get_token.return_value = token
    return wrapper


class TestGitHubDataSourceInit:
    def test_accepts_raw_sdk(self) -> None:
        sdk = MagicMock(spec=Github)
        ds = GitHubDataSource(sdk)
        assert ds._sdk is sdk

    def test_accepts_wrapper_client(self) -> None:
        sdk = MagicMock(spec=Github)
        wrapper = _make_wrapper_client(sdk, token="abc123")
        ds = GitHubDataSource(wrapper)
        assert ds._sdk is sdk
        assert ds.token == "abc123"

    def test_wrapper_missing_get_sdk_raises(self) -> None:
        wrapper = MagicMock(spec=[])
        with pytest.raises(TypeError, match="get_sdk"):
            GitHubDataSource(wrapper)

    def test_wrapper_get_sdk_wrong_type_raises(self) -> None:
        wrapper = MagicMock()
        wrapper.get_sdk.return_value = "not-a-github-sdk"
        with pytest.raises(TypeError, match="Github instance"):
            GitHubDataSource(wrapper)

    def test_wrapper_get_token_wrong_type_raises(self) -> None:
        sdk = MagicMock(spec=Github)
        wrapper = _make_wrapper_client(sdk)
        wrapper.get_token.return_value = 12345
        with pytest.raises(TypeError, match="get_token"):
            GitHubDataSource(wrapper)


class TestRebindClient:
    def test_rebind_updates_sdk_and_token(self) -> None:
        old_sdk = MagicMock(spec=Github)
        wrapper = _make_wrapper_client(old_sdk, token="old-token")
        ds = GitHubDataSource(wrapper)
        assert ds._sdk is old_sdk
        assert ds.token == "old-token"

        new_sdk = MagicMock(spec=Github)
        wrapper.get_sdk.return_value = new_sdk
        wrapper.get_token.return_value = "new-token"

        ds.rebind_client(wrapper)

        assert ds._sdk is new_sdk
        assert ds._sdk is not old_sdk
        assert ds.token == "new-token"

    def test_rebind_with_invalid_client_raises_and_leaves_state_untouched(self) -> None:
        sdk = MagicMock(spec=Github)
        wrapper = _make_wrapper_client(sdk, token="stays")
        ds = GitHubDataSource(wrapper)

        with pytest.raises(TypeError):
            ds.rebind_client(MagicMock(spec=[]))

        assert ds._sdk is sdk
        assert ds.token == "stays"


class TestRepoScopedReadsCostOneRequest:
    """Each read below used to fetch the repository first: two requests of the
    hourly rate limit per file streamed or subtree walked, where one does."""

    @pytest.fixture
    def requests(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        from github.Requester import Requester

        seen: list[str] = []

        def fake(_self: object, _verb: str, url: str, *_a: object, **_kw: object) -> tuple:
            seen.append(url)
            if "/git/blobs/" in url:
                return {}, {"sha": "b1", "size": 3, "content": "YWJj", "encoding": "base64"}
            if "/git/trees/" in url:
                return {}, {"sha": "t1", "tree": [], "truncated": False}
            if "/compare/" in url:
                return {}, {"status": "ahead", "files": [], "commits": []}
            if "/contents/" in url:
                return {}, {"type": "file", "path": "a.py", "content": "YWJj", "encoding": "base64"}
            return {}, {"full_name": "acme/widgets", "name": "widgets", "url": "https://api.github.com/repos/acme/widgets"}

        monkeypatch.setattr(Requester, "requestJsonAndCheck", fake)
        return seen

    @pytest.mark.parametrize(
        ("call", "endpoint"),
        [
            (lambda ds: ds.get_git_blob("acme", "widgets", "b1"), "/git/blobs/b1"),
            (lambda ds: ds.get_git_tree("acme", "widgets", "t1"), "/git/trees/t1"),
            (lambda ds: ds.compare_commits("acme", "widgets", "a", "b"), "/compare/a...b"),
            (lambda ds: ds.get_file_contents("acme", "widgets", "a.py", "main"), "/contents/a.py"),
        ],
    )
    def test_one_request(self, requests: list[str], call, endpoint: str) -> None:
        response = call(GitHubDataSource(Github()))

        assert response.success, response.error
        assert len(requests) == 1, requests
        assert endpoint in requests[0]

    def test_get_repo_still_confirms_the_repository_exists(self, requests: list[str]) -> None:
        assert GitHubDataSource(Github()).get_repo("acme", "widgets").success
        assert requests == ["/repos/acme/widgets"]


class TestRepoPathSafety:
    """owner/repo come from the model; a dot segment must not reach the HTTP layer,
    which would resolve it into another repository's path."""

    @pytest.mark.parametrize("owner,repo", [
        ("acme", ".."), ("..", "widgets"), ("acme", "widgets/../../other"), ("acme", "."), ("", "widgets"),
        ("acme", "widgets?x=1"),
    ])
    def test_unsafe_names_are_refused(self, owner: str, repo: str) -> None:
        sdk = MagicMock(spec=Github)
        ds = GitHubDataSource(sdk)
        with pytest.raises(ValueError):
            ds._repo(owner, repo)
        sdk.get_repo.assert_not_called()

    def test_ordinary_names_pass(self) -> None:
        sdk = MagicMock(spec=Github)
        ds = GitHubDataSource(sdk)
        ds._repo("acme-org", "my_repo.js")
        sdk.get_repo.assert_called_once_with("acme-org/my_repo.js", lazy=False)


def test_sdk_retries_only_reads() -> None:
    """A merge is a PUT: retried after a 502 that hid a successful merge, it fails as
    'not mergeable' and the user is told the merge failed."""
    from app.sources.client.github.github import _TRANSIENT_RETRY

    assert _TRANSIENT_RETRY.is_retry("GET", 502) is True
    assert _TRANSIENT_RETRY.is_retry("PUT", 502) is False
    assert _TRANSIENT_RETRY.is_retry("DELETE", 502) is False


class TestAgentToolsetReads:
    """Agent tool reads that misreported GitHub's state (2026-10-23 audit)."""

    def test_own_login_lists_private_repositories_too(self) -> None:
        """/users/{login}/repos is public-only even for the caller's own login."""
        sdk = MagicMock(spec=Github)
        me = MagicMock()
        me.login = "Ann"
        sdk.get_user.side_effect = lambda *a: me if not a else MagicMock()
        ds = GitHubDataSource(sdk)
        ds.list_user_repos("ann")
        me.get_repos.assert_called_once()
        sdk.get_user.assert_called_once_with()

    def test_another_login_is_read_as_that_user(self) -> None:
        sdk = MagicMock(spec=Github)
        me, other = MagicMock(), MagicMock()
        me.login = "ann"
        sdk.get_user.side_effect = lambda *a: other if a else me
        GitHubDataSource(sdk).list_user_repos("bob")
        other.get_repos.assert_called_once()
        me.get_repos.assert_not_called()

    def test_a_side_that_cannot_be_read_keeps_githubs_patch(self) -> None:
        """A failed or over-1 MB fetch is not an empty file: diffing it as one reports
        the whole file added or deleted."""
        ds = GitHubDataSource(MagicMock(spec=Github))
        ds._fetch_file_content_at_ref = MagicMock(side_effect=["old\n", None])
        pr = MagicMock()
        pr.base.sha, pr.head.sha = "b", "h"
        assert ds._generate_full_diff_for_file(
            owner="acme", repo="web", pr=pr, filename="a.py", status="modified",
            max_diff_lines=100, context_lines=2,
        ) is None

    def test_a_file_github_sends_without_content_is_unreadable(self) -> None:
        sdk = MagicMock(spec=Github)
        big = MagicMock(encoding="none", content="")
        sdk.get_repo.return_value.get_contents.return_value = big
        assert GitHubDataSource(sdk)._fetch_file_content_at_ref("acme", "web", "big.bin", "h") is None

    def test_merge_returns_its_result_and_passes_the_expected_head(self) -> None:
        sdk = MagicMock(spec=Github)
        pr = sdk.get_repo.return_value.get_pull.return_value
        pr.merge.return_value = MagicMock(merged=True, sha="abc123", message="Pull Request successfully merged")
        res = GitHubDataSource(sdk).merge_pull("acme", "web", 7, sha="head1")
        assert res.data == {"merged": True, "sha": "abc123", "message": "Pull Request successfully merged"}
        assert pr.merge.call_args.kwargs["sha"] == "head1"

    def test_nested_lists_are_capped(self) -> None:
        from app.sources.external.github.github_ import _MAX_NESTED_ITEMS

        sdk = MagicMock(spec=Github)
        pr = sdk.get_repo.return_value.get_pull.return_value
        pr.get_reviews.return_value = iter(range(_MAX_NESTED_ITEMS + 50))
        res = GitHubDataSource(sdk).get_pull_reviews("acme", "web", 7)
        assert len(res.data) == _MAX_NESTED_ITEMS and res.truncated is True
        pr.get_reviews.return_value = iter(range(_MAX_NESTED_ITEMS))
        res = GitHubDataSource(sdk).get_pull_reviews("acme", "web", 7)
        assert len(res.data) == _MAX_NESTED_ITEMS and res.truncated is False
