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
