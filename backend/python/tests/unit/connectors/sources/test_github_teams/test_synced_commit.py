"""GitHub code sync stamps the repo's code group with the commit its index now reflects."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.connectors.sources.github_teams.repos import ReposSync
from tests.unit.connectors.sources.test_github_teams.conftest import (
    make_mock_connector,
    make_repo,
    ok_response,
)

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


def _sync(checkpoint: dict | None, *, synced: bool = True) -> tuple[ReposSync, AsyncMock]:
    c = make_mock_connector()
    c.runtime.ds_call = AsyncMock(return_value=ok_response(SimpleNamespace(commit=SimpleNamespace(sha="head-sha"))))
    c.record_sync_point.read_sync_point = AsyncMock(return_value=checkpoint)
    c.record_sync_point.update_sync_point = AsyncMock()
    sync = ReposSync(c)
    sync._full_sync = AsyncMock(return_value=synced)
    sync._incremental_sync = AsyncMock(return_value=synced)
    sync._load_linguist_exclusions = AsyncMock(return_value=None)
    stamp = AsyncMock(return_value=True)
    return sync, stamp


@pytest.mark.parametrize("checkpoint", [
    None,                                                              # first sync: full walk
    {"last_commit_sha": "old-sha", "default_branch": "main"},          # incremental
    {"last_commit_sha": "head-sha", "default_branch": "main"},         # unchanged: stamped without a resync
])
async def test_a_sync_that_reaches_the_head_stamps_it(monkeypatch, checkpoint) -> None:
    sync, stamp = _sync(checkpoint)
    monkeypatch.setattr("app.connectors.sources.github_teams.repos.record_synced_commit", stamp)

    assert await sync.run(make_repo(repo_id=1001, default_branch="main")) is True

    stamp.assert_awaited_once_with(sync.c.data_entities_processor, "github-conn-1",
                                   "1001-code-repository", "head-sha")


async def test_a_sync_that_did_not_finish_stamps_nothing(monkeypatch) -> None:
    """The checkpoint is not advanced, so neither is the commit the results report."""
    sync, stamp = _sync(None, synced=False)
    monkeypatch.setattr("app.connectors.sources.github_teams.repos.record_synced_commit", stamp)

    assert await sync.run(make_repo(default_branch="main")) is False

    stamp.assert_not_awaited()
