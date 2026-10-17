"""Jira Data Center and Linear hand their deletes to the processor's cascade.

Neither connector deletes records itself or reads ``ENABLE_SOFT_DELETE``: both
name the records the source no longer has and call
``on_records_deleted_cascade``, which removes them or, with the flag on, moves
the same set to the trash. So the trash takes exactly what the hard delete
removes because one call serves both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.atlassian.jira_data_center.connector import (
    JiraDataCenterConnector,
)
from app.connectors.sources.linear.connector import LinearConnector
from app.models.entities import RecordType


@dataclass
class _Rec:
    id: str
    external_record_id: str
    record_type: str = RecordType.FILE.value


@dataclass
class _Store:
    """Records keyed by external parent id."""

    children: dict[str, list[_Rec]]
    by_external_id: dict[str, _Rec]
    by_issue_key: dict[str, _Rec] = field(default_factory=dict)
    hard_deleted: list[str] = field(default_factory=list)

    async def get_record_by_external_id(self, connector_id: str, external_id: str) -> _Rec | None:
        return self.by_external_id.get(external_id)

    async def get_records_by_parent(
        self, connector_id: str, parent_external_record_id: str, record_type: str | None = None,
    ) -> list[_Rec]:
        return [
            r for r in self.children.get(parent_external_record_id, [])
            if record_type is None or r.record_type == record_type
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
    processor.on_records_deleted_cascade = AsyncMock(return_value={"success": True, "deleted_records": []})
    processor.on_records_soft_deleted = AsyncMock(return_value={"success": True})
    processor.get_record_by_issue_key = AsyncMock(
        side_effect=lambda connector_id, issue_key: store.by_issue_key.get(issue_key)
    )
    conn.data_entities_processor = processor
    return processor


def _handed_over(processor: MagicMock) -> tuple[list[str], str, dict]:
    processor.on_records_deleted_cascade.assert_awaited_once()
    call = processor.on_records_deleted_cascade.await_args
    return call.args[0], call.args[1], call.kwargs


# A source delete keeps PARENT_CHILD children, and reaches a record already in the trash.
CASCADE = {"cascade_children": False, "include_trashed_roots": True}


# ---------------------------------------------------------------------------
# Jira Data Center: the issue; its attachments go with it through ATTACHMENT edges
# ---------------------------------------------------------------------------


def _jira_store() -> _Store:
    issue = _Rec("issue-1", "10004", RecordType.TICKET.value)
    return _Store(
        by_issue_key={"PA-5": issue},
        by_external_id={},
        children={
            "10004": [
                _Rec("file-1", "att-1"),
                _Rec("subtask-1", "10011", RecordType.TICKET.value),
            ],
        },
    )


async def _run_jira(issue_key: str = "PA-5") -> tuple[_Store, MagicMock]:
    cs = MagicMock()
    cs.get_config = AsyncMock()
    conn = JiraDataCenterConnector(MagicMock(), MagicMock(), MagicMock(), cs, "jdc-1", "team", "u1")
    store = _jira_store()
    processor = _wire(conn, store)
    datasource = MagicMock()
    datasource.get_issue_v2 = AsyncMock(return_value=MagicMock(status=HttpStatusCode.NOT_FOUND.value))
    with patch.object(conn, "_get_fresh_datasource", AsyncMock(return_value=datasource)):
        await conn._handle_deleted_issue(issue_key)
    return store, processor


class TestJiraDataCenterIssueDelete:
    async def test_the_issue_goes_to_the_processors_cascade(self) -> None:
        store, processor = await _run_jira()

        ids, connector_id, kwargs = _handed_over(processor)
        assert ids == ["issue-1"]
        assert connector_id == "jdc-1"
        assert kwargs == CASCADE
        assert store.hard_deleted == []
        processor.on_records_soft_deleted.assert_not_called()

    async def test_an_issue_that_is_not_stored_is_left_alone(self) -> None:
        store, processor = await _run_jira("PA-404")

        processor.on_records_deleted_cascade.assert_not_called()
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


async def _run_linear(store: _Store, external_record_id: str = "issue-ext") -> MagicMock:
    conn = LinearConnector(MagicMock(), MagicMock(), MagicMock(), AsyncMock(), "linear-1", "team", "u1")
    processor = _wire(conn, store)
    await conn._mark_record_and_children_deleted(external_record_id=external_record_id, record_type="issue")
    return processor


class TestLinearDelete:
    async def test_the_record_and_two_levels_below_go_to_the_processors_cascade(self) -> None:
        store = _linear_store()
        processor = await _run_linear(store)

        ids, connector_id, kwargs = _handed_over(processor)
        assert sorted(ids) == ["doc", "doc-file", "issue", "link"]
        assert len(ids) == len(set(ids))
        assert connector_id == "linear-1"
        assert kwargs == CASCADE
        assert store.hard_deleted == []
        processor.on_records_soft_deleted.assert_not_called()

    async def test_a_record_that_is_not_stored_is_left_alone(self) -> None:
        store = _Store(children={}, by_external_id={})
        processor = await _run_linear(store, "gone")

        processor.on_records_deleted_cascade.assert_not_called()
        assert store.hard_deleted == []
