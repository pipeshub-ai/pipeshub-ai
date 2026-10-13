"""Connectors that delete records themselves honour ENABLE_SOFT_DELETE.

Jira Data Center and Linear remove records with ``delete_records_and_relations``
instead of going through the processor's delete. With the flag on they must send
exactly the set their hard delete removes to the trash, as one CONNECTOR batch
that walks nothing further; with it off they must hard-delete as before.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import DeleteSource
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.atlassian.jira_data_center.connector import (
    JiraDataCenterConnector,
)
from app.connectors.sources.linear.connector import LinearConnector
from app.models.entities import RecordType

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

JIRA_DC = "app.connectors.sources.atlassian.jira_data_center.connector"
LINEAR = "app.connectors.sources.linear.connector"


@dataclass
class _Rec:
    id: str
    external_record_id: str
    record_type: str = RecordType.FILE.value


@dataclass
class _Store:
    """Records keyed by external parent id; hard deletes remove them from view."""

    children: dict[str, list[_Rec]]
    by_external_id: dict[str, _Rec]
    by_issue_key: dict[str, _Rec] = field(default_factory=dict)
    hard_deleted: list[str] = field(default_factory=list)

    async def get_record_by_issue_key(self, connector_id: str, issue_key: str) -> _Rec | None:
        return self.by_issue_key.get(issue_key)

    async def get_record_by_external_id(self, connector_id: str, external_id: str) -> _Rec | None:
        return self.by_external_id.get(external_id)

    async def get_records_by_parent(
        self, connector_id: str, parent_external_record_id: str, record_type: str | None = None,
    ) -> list[_Rec]:
        return [
            r for r in self.children.get(parent_external_record_id, [])
            if r.id not in self.hard_deleted and (record_type is None or r.record_type == record_type)
        ]

    async def delete_records_and_relations(self, record_key: str, hard_delete: bool = False) -> None:
        self.hard_deleted.append(record_key)


class _Tx:
    def __init__(self, store: _Store) -> None:
        self.store = store

    async def __aenter__(self) -> _Store:
        return self.store

    async def __aexit__(self, *_: object) -> None:
        return None


def _wire(conn: JiraDataCenterConnector | LinearConnector, store: _Store) -> MagicMock:
    conn.data_store_provider = MagicMock()
    conn.data_store_provider.transaction = MagicMock(side_effect=lambda: _Tx(store))
    processor = MagicMock()
    processor.org_id = "org-1"
    processor.on_records_soft_deleted = AsyncMock(return_value={"success": True})
    conn.data_entities_processor = processor
    return processor


def _flag(module: str, on: bool) -> AbstractContextManager[AsyncMock]:
    return patch(f"{module}.is_soft_delete_enabled", AsyncMock(return_value=on))


def _trashed(processor: MagicMock) -> tuple[list[str], str, dict]:
    processor.on_records_soft_deleted.assert_awaited_once()
    call = processor.on_records_soft_deleted.await_args
    return call.args[0], call.args[1], call.kwargs


# ---------------------------------------------------------------------------
# Jira Data Center: the issue and its direct file children
# ---------------------------------------------------------------------------


def _jira_store() -> _Store:
    issue = _Rec("issue-1", "10004", RecordType.TICKET.value)
    return _Store(
        by_issue_key={"PA-5": issue},
        by_external_id={},
        children={
            "10004": [
                _Rec("file-1", "att-1"),
                _Rec("file-2", "att-2"),
                _Rec("subtask-1", "10011", RecordType.TICKET.value),
            ],
            "att-1": [_Rec("file-under-file", "att-1-1")],
        },
    )


async def _run_jira(on: bool) -> tuple[_Store, MagicMock]:
    cs = MagicMock()
    cs.get_config = AsyncMock()
    conn = JiraDataCenterConnector(MagicMock(), MagicMock(), MagicMock(), cs, "jdc-1", "team", "u1")
    store = _jira_store()
    processor = _wire(conn, store)
    datasource = MagicMock()
    datasource.get_issue_v2 = AsyncMock(return_value=MagicMock(status=HttpStatusCode.NOT_FOUND.value))
    with _flag(JIRA_DC, on), patch.object(conn, "_get_fresh_datasource", AsyncMock(return_value=datasource)):
        await conn._handle_deleted_issue("PA-5")
    return store, processor


class TestJiraDataCenterIssueDelete:
    async def test_flag_off_hard_deletes_as_before(self) -> None:
        store, processor = await _run_jira(on=False)
        assert sorted(store.hard_deleted) == ["file-1", "file-2", "issue-1"]
        processor.on_records_soft_deleted.assert_not_called()

    async def test_flag_on_trashes_exactly_what_the_hard_delete_removes(self) -> None:
        hard, _ = await _run_jira(on=False)
        store, processor = await _run_jira(on=True)

        ids, connector_id, kwargs = _trashed(processor)
        assert sorted(ids) == sorted(hard.hard_deleted)
        assert connector_id == "jdc-1"
        assert kwargs == {"delete_source": DeleteSource.CONNECTOR, "follow": ()}
        assert store.hard_deleted == []


# ---------------------------------------------------------------------------
# Linear: the record, its children and their children, nothing deeper
# ---------------------------------------------------------------------------


def _linear_store() -> _Store:
    return _Store(
        by_external_id={"issue-ext": _Rec("issue", "issue-ext", RecordType.TICKET.value)},
        children={
            "issue-ext": [_Rec("doc", "doc-ext", RecordType.WEBPAGE.value), _Rec("link", "link-ext")],
            "doc-ext": [_Rec("doc-file", "doc-file-ext")],
            "doc-file-ext": [_Rec("too-deep", "too-deep-ext")],
        },
    )


async def _run_linear(on: bool) -> tuple[_Store, MagicMock]:
    conn = LinearConnector(MagicMock(), MagicMock(), MagicMock(), AsyncMock(), "linear-1", "team", "u1")
    store = _linear_store()
    processor = _wire(conn, store)
    with _flag(LINEAR, on):
        await conn._mark_record_and_children_deleted(external_record_id="issue-ext", record_type="issue")
    return store, processor


class TestLinearDelete:
    async def test_flag_off_hard_deletes_as_before(self) -> None:
        store, processor = await _run_linear(on=False)
        assert sorted(store.hard_deleted) == ["doc", "doc-file", "issue", "link"]
        processor.on_records_soft_deleted.assert_not_called()

    async def test_flag_on_trashes_exactly_what_the_hard_delete_removes(self) -> None:
        hard, _ = await _run_linear(on=False)
        store, processor = await _run_linear(on=True)

        ids, connector_id, kwargs = _trashed(processor)
        assert sorted(ids) == sorted(hard.hard_deleted)
        assert len(ids) == len(set(ids))
        assert connector_id == "linear-1"
        assert kwargs == {"delete_source": DeleteSource.CONNECTOR, "follow": ()}
        assert store.hard_deleted == []

    @pytest.mark.parametrize("on", [True, False])
    async def test_a_record_that_is_not_stored_is_left_alone(self, on: bool) -> None:
        conn = LinearConnector(MagicMock(), MagicMock(), MagicMock(), AsyncMock(), "linear-1", "team", "u1")
        store = _Store(children={}, by_external_id={})
        processor = _wire(conn, store)
        with _flag(LINEAR, on):
            await conn._mark_record_and_children_deleted(external_record_id="gone", record_type="issue")
        processor.on_records_soft_deleted.assert_not_called()
        assert store.hard_deleted == []
