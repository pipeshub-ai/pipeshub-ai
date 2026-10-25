"""Tests for RecordRemover: removing what is gone from Linear together with what only
existed because of it, while sub-issues survive."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.connectors.sources.linear import record_removal
from app.connectors.sources.linear.record_removal import RecordRemover
from app.models.entities import RecordType

CONNECTOR_ID = "linear-conn-1"


def _record(record_id, external_id, record_type=RecordType.TICKET) -> SimpleNamespace:
    return SimpleNamespace(id=record_id, external_record_id=external_id, record_type=record_type)


def _make_remover(children=None, records=None) -> tuple:
    """``children`` maps a parent's external id to its child records; ``records`` maps
    an external id to the stored record."""
    children = children or {}
    records = records or {}
    processor = MagicMock()
    processor.get_record_by_external_id = AsyncMock(
        side_effect=lambda connector_id, external_id: records.get(external_id))
    processor.get_records_by_parent = AsyncMock(
        side_effect=lambda connector_id, external_id: list(children.get(external_id, [])))
    processor.on_records_deleted_cascade = AsyncMock()
    processor.on_records_detached_from_parent = AsyncMock()
    processor.get_records_in_record_group = AsyncMock(return_value=[])
    processor.on_record_group_deleted = AsyncMock()
    processor.on_user_group_deleted = AsyncMock()
    logger = MagicMock()
    return RecordRemover(processor, CONNECTOR_ID, logger), processor, logger


class TestFailedDelete:
    """The processor reports an ordinary delete failure in its result, without raising."""

    @pytest.mark.asyncio
    async def test_a_failed_delete_is_not_a_removal(self) -> None:
        issue = _record("node-issue", "issue-1")
        remover, processor, _ = _make_remover(records={"issue-1": issue})
        processor.on_records_deleted_cascade = AsyncMock(return_value={"success": False, "reason": "graph down"})

        with pytest.raises(RuntimeError, match="graph down"):
            await remover.remove("issue-1")

    @pytest.mark.asyncio
    async def test_a_failed_group_delete_stops_the_removal(self) -> None:
        remover, processor, _ = _make_remover()
        processor.get_records_in_record_group = AsyncMock(return_value=[_record("node-1", "issue-1")])
        processor.on_records_deleted_cascade = AsyncMock(return_value={"success": False, "reason": "locked"})

        with pytest.raises(RuntimeError, match="locked"):
            await remover.remove_group("team-1")
        processor.on_record_group_deleted.assert_not_awaited()


class TestRemove:
    @pytest.mark.asyncio
    async def test_unknown_record_removes_nothing(self) -> None:
        remover, processor, _ = _make_remover()

        assert await remover.remove("issue-unknown") is False

        processor.get_record_by_external_id.assert_awaited_once_with(CONNECTOR_ID, "issue-unknown")
        processor.get_records_by_parent.assert_not_awaited()
        processor.on_records_deleted_cascade.assert_not_awaited()
        processor.on_records_detached_from_parent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_record_without_children(self) -> None:
        issue = _record("node-issue", "issue-1")
        remover, processor, _ = _make_remover(records={"issue-1": issue})

        assert await remover.remove("issue-1") is True

        processor.on_records_deleted_cascade.assert_awaited_once_with(
            ["node-issue"], CONNECTOR_ID, cascade_children=False, include_trashed_roots=True)
        processor.on_records_detached_from_parent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_links_documents_and_files_go_with_the_issue(self) -> None:
        issue = _record("node-issue", "issue-1")
        link = _record("node-link", "att-1", RecordType.LINK)
        document = _record("node-doc", "doc-1", RecordType.WEBPAGE)
        file_record = _record("node-file", "https://uploads.linear.app/a.pdf#parent=issue-1", RecordType.FILE)
        remover, processor, _ = _make_remover(
            records={"issue-1": issue},
            children={"issue-1": [link, document, file_record]},
        )

        assert await remover.remove("issue-1") is True

        # One call, children before the parent, and the processor's own cascade off:
        # it would take the sub-issues too.
        processor.on_records_deleted_cascade.assert_awaited_once_with(
            ["node-link", "node-doc", "node-file", "node-issue"], CONNECTOR_ID, cascade_children=False, include_trashed_roots=True)

    @pytest.mark.asyncio
    async def test_sub_issues_survive_and_are_detached(self) -> None:
        issue = _record("node-issue", "issue-1")
        sub_issue_a = _record("node-sub-a", "issue-2")
        sub_issue_b = _record("node-sub-b", "issue-3")
        link = _record("node-link", "att-1", RecordType.LINK)
        remover, processor, _ = _make_remover(
            records={"issue-1": issue},
            children={
                "issue-1": [sub_issue_a, link, sub_issue_b],
                # A sub-issue has children of its own; none of them is touched.
                "issue-2": [_record("node-sub-link", "att-9", RecordType.LINK)],
            },
        )

        await remover.remove("issue-1")

        processor.on_records_detached_from_parent.assert_awaited_once_with(["node-sub-a", "node-sub-b"])
        deleted = processor.on_records_deleted_cascade.await_args.args[0]
        assert deleted == ["node-link", "node-issue"]
        assert "node-sub-a" not in deleted and "node-sub-b" not in deleted
        assert "node-sub-link" not in deleted
        # The sub-issues' own children were never even looked at.
        looked_up = [c.args[1] for c in processor.get_records_by_parent.await_args_list]
        assert "issue-2" not in looked_up and "issue-3" not in looked_up

    @pytest.mark.asyncio
    async def test_sub_issues_are_detached_before_the_parent_is_deleted(self) -> None:
        issue = _record("node-issue", "issue-1")
        sub_issue = _record("node-sub", "issue-2")
        remover, processor, _ = _make_remover(
            records={"issue-1": issue}, children={"issue-1": [sub_issue]})
        order = MagicMock()
        order.attach_mock(processor.on_records_detached_from_parent, "detach")
        order.attach_mock(processor.on_records_deleted_cascade, "delete")

        await remover.remove("issue-1")

        assert order.mock_calls == [
            call.detach(["node-sub"]),
            call.delete(["node-issue"], CONNECTOR_ID, cascade_children=False, include_trashed_roots=True),
        ]

    @pytest.mark.asyncio
    async def test_failed_detach_deletes_nothing(self) -> None:
        issue = _record("node-issue", "issue-1")
        sub_issue = _record("node-sub", "issue-2")
        remover, processor, _ = _make_remover(
            records={"issue-1": issue}, children={"issue-1": [sub_issue]})
        processor.on_records_detached_from_parent.side_effect = RuntimeError("not updated")

        with pytest.raises(RuntimeError, match="not updated"):
            await remover.remove("issue-1")

        processor.on_records_deleted_cascade.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_grandchildren_are_deleted_deepest_first(self) -> None:
        project = _record("node-project", "proj-1", RecordType.PROJECT)
        document = _record("node-doc", "doc-1", RecordType.WEBPAGE)
        link = _record("node-link", "link-1", RecordType.LINK)
        doc_file = _record("node-doc-file", "https://uploads.linear.app/a.pdf#parent=doc-1", RecordType.FILE)
        remover, processor, _ = _make_remover(
            records={"proj-1": project},
            children={"proj-1": [document, link], "doc-1": [doc_file]},
        )

        await remover.remove("proj-1")

        processor.on_records_deleted_cascade.assert_awaited_once_with(
            ["node-doc-file", "node-doc", "node-link", "node-project"], CONNECTOR_ID, cascade_children=False, include_trashed_roots=True)

    @pytest.mark.asyncio
    async def test_each_record_is_listed_once(self) -> None:
        issue = _record("node-issue", "issue-1")
        document = _record("node-doc", "doc-1", RecordType.WEBPAGE)
        shared = _record("node-file", "file-1", RecordType.FILE)
        remover, processor, _ = _make_remover(
            records={"issue-1": issue},
            # The same file is reported under the issue and under its document.
            children={"issue-1": [shared, document], "doc-1": [shared]},
        )

        await remover.remove("issue-1")

        deleted = processor.on_records_deleted_cascade.await_args.args[0]
        assert deleted == ["node-file", "node-doc", "node-issue"]


class TestRemoveRecord:
    @pytest.mark.asyncio
    async def test_record_in_hand_is_not_looked_up_again(self) -> None:
        issue = _record("node-issue", "issue-1")
        link = _record("node-link", "att-1", RecordType.LINK)
        remover, processor, _ = _make_remover(children={"issue-1": [link]})

        await remover.remove_record(issue)

        processor.get_record_by_external_id.assert_not_awaited()
        processor.on_records_deleted_cascade.assert_awaited_once_with(
            ["node-link", "node-issue"], CONNECTOR_ID, cascade_children=False, include_trashed_roots=True)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("external_id", [None, ""])
    async def test_record_without_an_external_id_has_no_children_to_find(self, external_id) -> None:
        remover, processor, _ = _make_remover()

        await remover.remove_record(_record("node-x", external_id, RecordType.FILE))

        processor.get_records_by_parent.assert_not_awaited()
        processor.on_records_deleted_cascade.assert_awaited_once_with(
            ["node-x"], CONNECTOR_ID, cascade_children=False, include_trashed_roots=True)

    @pytest.mark.asyncio
    async def test_child_without_an_external_id_is_still_deleted(self) -> None:
        issue = _record("node-issue", "issue-1")
        odd_child = _record("node-odd", None, RecordType.FILE)
        remover, processor, _ = _make_remover(children={"issue-1": [odd_child]})

        await remover.remove_record(issue)

        assert processor.on_records_deleted_cascade.await_args.args[0] == ["node-odd", "node-issue"]


class TestRemoveGroup:
    @pytest.mark.asyncio
    async def test_empty_team_still_loses_its_groups(self) -> None:
        remover, processor, _ = _make_remover()

        assert await remover.remove_group("team-1") == 0

        processor.on_records_deleted_cascade.assert_not_awaited()
        processor.on_record_group_deleted.assert_awaited_once_with("team-1", CONNECTOR_ID)
        processor.on_user_group_deleted.assert_awaited_once_with("team-1", CONNECTOR_ID)

    @pytest.mark.asyncio
    async def test_records_are_removed_before_the_groups(self) -> None:
        remover, processor, logger = _make_remover()
        processor.get_records_in_record_group.return_value = [
            _record("node-1", "issue-1"), _record("node-2", "att-1", RecordType.LINK)]
        order = MagicMock()
        order.attach_mock(processor.on_records_deleted_cascade, "delete")
        order.attach_mock(processor.on_record_group_deleted, "record_group")
        order.attach_mock(processor.on_user_group_deleted, "user_group")

        assert await remover.remove_group("team-1") == 2

        processor.get_records_in_record_group.assert_awaited_once_with(
            CONNECTOR_ID, "team-1", record_removal.GROUP_PAGE)
        assert order.mock_calls == [
            call.delete(["node-1", "node-2"], CONNECTOR_ID, cascade_children=False),
            call.record_group("team-1", CONNECTOR_ID),
            call.user_group("team-1", CONNECTOR_ID),
        ]
        logger.info.assert_called_once()

    @pytest.mark.asyncio
    async def test_large_team_is_removed_a_page_at_a_time(self) -> None:
        remover, processor, _ = _make_remover()
        processor.get_records_in_record_group.side_effect = [
            [_record("node-1", "a"), _record("node-2", "b")],
            [_record("node-3", "c"), _record("node-4", "d")],
            [_record("node-5", "e")],
        ]

        with patch.object(record_removal, "GROUP_PAGE", 2):
            removed = await remover.remove_group("team-1")

        assert removed == 5
        assert [c.args[0] for c in processor.on_records_deleted_cascade.await_args_list] == [
            ["node-1", "node-2"], ["node-3", "node-4"], ["node-5"],
        ]
        # A short page is the last one: no fourth read.
        assert processor.get_records_in_record_group.await_count == 3
        assert all(c.args == (CONNECTOR_ID, "team-1", 2)
                   for c in processor.get_records_in_record_group.await_args_list)

    @pytest.mark.asyncio
    async def test_full_last_page_is_followed_by_an_empty_read(self) -> None:
        remover, processor, _ = _make_remover()
        processor.get_records_in_record_group.side_effect = [
            [_record("node-1", "a"), _record("node-2", "b")],
            [],
        ]

        with patch.object(record_removal, "GROUP_PAGE", 2):
            removed = await remover.remove_group("team-1")

        assert removed == 2
        assert processor.get_records_in_record_group.await_count == 2
        processor.on_record_group_deleted.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failed_record_delete_keeps_the_groups(self) -> None:
        remover, processor, _ = _make_remover()
        processor.get_records_in_record_group.return_value = [_record("node-1", "issue-1")]
        processor.on_records_deleted_cascade.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError, match="db down"):
            await remover.remove_group("team-1")

        processor.on_record_group_deleted.assert_not_awaited()
        processor.on_user_group_deleted.assert_not_awaited()
