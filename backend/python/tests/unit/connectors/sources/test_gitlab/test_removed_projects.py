"""Unit tests for RemovedProjects and the sync-incomplete rule."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.gitlab.connector import (
    GitLabConnector,
    GitLabSyncIncompleteError,
)
from app.connectors.sources.gitlab.projects import member_has_access
from app.connectors.sources.gitlab.removed_projects import RemovedProjects

from .conftest import make_mock_connector

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _connector(stored_groups: list[dict]) -> MagicMock:
    c = make_mock_connector()
    tx = MagicMock(get_nodes_by_filters=AsyncMock(return_value=stored_groups))

    @asynccontextmanager
    async def transaction() -> AsyncIterator[MagicMock]:
        yield tx

    c.data_store_provider = MagicMock(transaction=transaction)
    c.record_sync_point.read_sync_point = AsyncMock(return_value={})
    c.record_sync_point.delete_sync_point = AsyncMock()
    c.data_entities_processor.get_record_group_by_external_id = AsyncMock(return_value=SimpleNamespace(id="g"))
    c.data_entities_processor.on_record_group_deleted = AsyncMock(return_value=True)
    c.runtime.ds_call = AsyncMock(return_value=SimpleNamespace(success=False, data=None, status_code=404, error="404"))
    return c


async def test_a_remembered_project_is_removed_with_its_checkpoints() -> None:
    c = _connector([])
    c.record_sync_point.read_sync_point = AsyncMock(return_value={"project_id": 7})
    assert await RemovedProjects(c).moved_or_remove("acme/web") is None
    c.data_entities_processor.on_record_group_deleted.assert_awaited_once_with("7", c.connector_id)
    assert c.record_sync_point.delete_sync_point.await_count >= 4


async def test_a_project_renamed_or_transferred_is_found_by_its_id_and_kept() -> None:
    c = _connector([])
    c.record_sync_point.read_sync_point = AsyncMock(return_value={"project_id": 7})
    moved = SimpleNamespace(id=7, path_with_namespace="acme/web-renamed")
    c.runtime.ds_call = AsyncMock(return_value=SimpleNamespace(success=True, data=moved, status_code=None, error=None))
    assert await RemovedProjects(c).moved_or_remove("acme/web") is moved
    assert c.runtime.ds_call.await_args.args[1] == 7
    c.data_entities_processor.on_record_group_deleted.assert_not_called()
    c.record_sync_point.delete_sync_point.assert_not_called()


async def test_an_id_lookup_that_fails_removes_nothing_and_fails_the_sync() -> None:
    c = _connector([])
    c.record_sync_point.read_sync_point = AsyncMock(return_value={"project_id": 7})
    c.runtime.ds_call = AsyncMock(return_value=SimpleNamespace(success=False, data=None, status_code=503, error="503"))
    assert await RemovedProjects(c).moved_or_remove("acme/web") is None
    c.data_entities_processor.on_record_group_deleted.assert_not_called()
    c.note_failed_read.assert_called_once()


async def test_a_project_synced_before_the_path_was_remembered_is_found_by_its_group_name() -> None:
    c = _connector([{"externalGroupId": "7", "name": "acme/web"}])
    await RemovedProjects(c).moved_or_remove("acme/web")
    c.data_entities_processor.on_record_group_deleted.assert_awaited_once_with("7", c.connector_id)


async def test_an_unknown_project_removes_nothing() -> None:
    c = _connector([])
    await RemovedProjects(c).moved_or_remove("acme/web")
    c.data_entities_processor.on_record_group_deleted.assert_not_called()
    c.runtime.ds_call.assert_not_called()


@pytest.mark.parametrize(("state", "membership", "allowed"), [
    ("active", "active", True), ("blocked", "active", False), ("deactivated", "active", False),
    ("banned", "active", False), ("active", "awaiting", False), (None, None, True),
])
def test_member_has_access(state, membership, allowed) -> None:
    assert member_has_access(SimpleNamespace(state=state, membership_state=membership)) is allowed


@pytest.mark.parametrize(("status", "partial", "noted"), [
    (403, False, False), (404, False, False), (403, True, True), (500, False, True), (None, False, True),
])
def test_only_a_read_without_a_definite_answer_fails_the_sync(status, partial, noted) -> None:
    connector = MagicMock()
    response = SimpleNamespace(status_code=status, error="x", data=[1] if partial else None)
    GitLabConnector.note_failed_read(connector, "issues of project 1", response)
    assert connector.note_sync_problem.called is noted


def test_a_sync_with_problems_raises_after_the_rest_ran() -> None:
    connector = MagicMock(sync_problems=["issues of project 1: 502"])
    with pytest.raises(GitLabSyncIncompleteError, match="issues of project 1"):
        GitLabConnector.raise_if_sync_incomplete(connector)
    GitLabConnector.raise_if_sync_incomplete(MagicMock(sync_problems=[]))
