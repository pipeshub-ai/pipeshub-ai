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


class TestSearchWrappersBoundToFirstPage:
    """Every caller of these three wrappers only needs the first usable hit, so
    they must fetch a single Search API page (``get_page(0)``) rather than
    materializing the full ``PaginatedList`` -- which would issue one HTTP
    request per page and blow through GitHub's 30 req/min Search budget."""

    @pytest.fixture()
    def ds(self) -> GitHubDataSource:
        return GitHubDataSource(MagicMock(spec=Github))

    def test_search_users_by_email_fetches_only_first_page(self, ds: GitHubDataSource) -> None:
        paginated = MagicMock()
        paginated.get_page.return_value = ["user1", "user2"]
        ds._sdk.search_users.return_value = paginated

        res = ds.search_users_by_email("jane@example.com")

        paginated.get_page.assert_called_once_with(0)
        paginated.__iter__.assert_not_called()
        assert res.success is True
        assert res.data == ["user1", "user2"]

    def test_search_users_by_email_caps_page_size(self, ds: GitHubDataSource) -> None:
        paginated = MagicMock()
        paginated.get_page.return_value = [f"user{i}" for i in range(50)]
        ds._sdk.search_users.return_value = paginated

        res = ds.search_users_by_email("jane@example.com")

        assert len(res.data) == GitHubDataSource._SEARCH_FIRST_PAGE_SIZE

    def test_search_commits_by_author_email_fetches_only_first_page(self, ds: GitHubDataSource) -> None:
        paginated = MagicMock()
        paginated.get_page.return_value = ["commit1"]
        ds._sdk.search_commits.return_value = paginated

        res = ds.search_commits_by_author_email("jane@example.com", org="acme")

        paginated.get_page.assert_called_once_with(0)
        paginated.__iter__.assert_not_called()
        assert res.success is True
        assert res.data == ["commit1"]

    def test_search_commits_by_author_login_fetches_only_first_page(self, ds: GitHubDataSource) -> None:
        paginated = MagicMock()
        paginated.get_page.return_value = ["commit1"]
        ds._sdk.search_commits.return_value = paginated

        res = ds.search_commits_by_author_login("octocat", org="acme")

        paginated.get_page.assert_called_once_with(0)
        paginated.__iter__.assert_not_called()
        assert res.success is True
        assert res.data == ["commit1"]

    def test_search_error_returns_failed_response(self, ds: GitHubDataSource) -> None:
        ds._sdk.search_users.side_effect = RuntimeError("rate limited")

        res = ds.search_users_by_email("jane@example.com")

        assert res.success is False
        assert "rate limited" in res.error
