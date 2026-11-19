"""GitLab code sync stamps the project's code group with the commit its index now reflects."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.connectors.sources.gitlab.repos import ReposSync

from .conftest import branch_res, make_mock_connector

pytestmark = pytest.mark.anyio

_PROJECT_ID = 42
_PROJECT_PATH = "ns/proj"


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


def _sync(checkpoint: tuple, *, synced: bool = True) -> ReposSync:
    c = make_mock_connector()
    c.runtime.ds_call = AsyncMock(return_value=branch_res("head-sha"))
    repos = ReposSync(c)
    repos._load_linguist_exclusions = AsyncMock(return_value=None)
    repos._get_code_repo_checkpoint = AsyncMock(return_value=checkpoint)
    repos._sync_repo_full = AsyncMock(return_value=synced)
    repos._sync_repo_incremental = AsyncMock(return_value=synced)
    return repos


@pytest.mark.parametrize("checkpoint", [
    (None, None, None),                          # first sync: full walk
    ("old-sha", _PROJECT_PATH, "main"),          # incremental
    ("head-sha", _PROJECT_PATH, "main"),         # unchanged: stamped without a resync
    ("head-sha", _PROJECT_PATH, "develop"),      # same head on a newly chosen branch
])
async def test_a_sync_that_reaches_the_head_stamps_it_once(monkeypatch, checkpoint) -> None:
    stamp = AsyncMock(return_value=True)
    monkeypatch.setattr("app.connectors.sources.gitlab.repos.record_synced_commit", stamp)
    repos = _sync(checkpoint)

    await repos.run(_PROJECT_ID, _PROJECT_PATH, "main")

    stamp.assert_awaited_once_with(repos.c.data_entities_processor, "gitlab-conn-1",
                                   f"{_PROJECT_ID}-code-repository", "head-sha")


async def test_a_sync_that_did_not_finish_stamps_nothing(monkeypatch) -> None:
    stamp = AsyncMock(return_value=True)
    monkeypatch.setattr("app.connectors.sources.gitlab.repos.record_synced_commit", stamp)

    await _sync((None, None, None), synced=False).run(_PROJECT_ID, _PROJECT_PATH, "main")

    stamp.assert_not_awaited()
