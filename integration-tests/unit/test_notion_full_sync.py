"""The Notion ``full_sync`` helper must not report a failed full sync as done (no live services).

A full sync that fails still ends IDLE with steady record counts; the only trace it leaves is
``pendingFullSync`` on the app node, which the helper reads after each wait.
"""

from __future__ import annotations

from typing import Any

import pytest

from connectors.notion import notion_test_utils

pytestmark = pytest.mark.unit


class _Client:
    def __init__(self) -> None:
        self.resyncs = 0

    def resync_connector(self, connector_id: str, *, full_sync: bool) -> None:
        assert full_sync
        self.resyncs += 1


class _Graph:
    def __init__(self, pending_after_each_sync: list[bool | None]) -> None:
        self.pending = list(pending_after_each_sync)

    async def get_document(self, key: str, collection: str) -> dict[str, Any] | None:
        pending = self.pending.pop(0)
        return None if pending is None else {"pendingFullSync": pending}


@pytest.fixture(autouse=True)
def _instant_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    async def wait(*args: Any, **kwargs: Any) -> int:
        return 7

    monkeypatch.setattr(notion_test_utils, "wait_for_sync_completion", wait)


@pytest.mark.asyncio
@pytest.mark.parametrize("pending, resyncs", [([False], 1), ([True, False], 2)])
async def test_returns_once_a_full_sync_finishes(pending: list[bool], resyncs: int) -> None:
    client = _Client()
    assert await notion_test_utils.full_sync(client, _Graph(pending), "conn-1") == 7
    assert client.resyncs == resyncs


@pytest.mark.asyncio
async def test_fails_when_the_retry_fails_too() -> None:
    client = _Client()
    with pytest.raises(AssertionError, match="pendingFullSync"):
        await notion_test_utils.full_sync(client, _Graph([True, True]), "conn-1")
    assert client.resyncs == 2


@pytest.mark.asyncio
async def test_fails_when_the_app_node_is_missing() -> None:
    with pytest.raises(AssertionError, match="app node"):
        await notion_test_utils.full_sync(_Client(), _Graph([None]), "conn-1")
