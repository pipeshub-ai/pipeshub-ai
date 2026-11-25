"""The store walk that keeps a stored folder Graph did not return through a full sync."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

from app.config.constants.arangodb import CollectionNames
from app.connectors.sources.microsoft.common.outlook_folders import (
    OutlookFolderInventoryMixin,
)
from app.services.graph_db.common.record_visibility import RecordVisibility

CONNECTOR_ID = "outlook-1"


def inventory(processor: MagicMock) -> OutlookFolderInventoryMixin:
    mixin = OutlookFolderInventoryMixin()
    mixin.connector_id = CONNECTOR_ID
    mixin.data_entities_processor = processor
    return mixin


def records(*ids: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(id=i) for i in ids]


async def test_the_walk_keeps_every_page_of_records_every_subfolder_and_the_mailbox() -> None:
    first_page = [f"r{i:04d}" for i in range(1000)]
    pages = {("root", None): records(*first_page), ("root", "r0999"): records("r1000"), ("c1", None): []}
    children = {"root": [{"externalGroupId": "c1"}, {"externalGroupId": None}], "c1": [{"externalGroupId": "root"}]}
    processor = MagicMock()
    processor.get_record_group_by_external_id = AsyncMock(
        return_value=SimpleNamespace(parent_external_group_id="mailbox:u"),
    )
    processor.note_kept_record_group = AsyncMock()
    processor.get_records_in_record_group = AsyncMock(
        side_effect=lambda connector_id, group_id, limit, after_key, **_: pages[(group_id, after_key)],
    )
    processor.get_nodes_by_filters = AsyncMock(
        side_effect=lambda collection, filters, **_: children[filters["parentExternalGroupId"]],
    )

    assert await inventory(processor)._keep_stored_folder_tree("root") is True

    assert processor.note_kept_record_group.await_args_list == [
        call(CONNECTOR_ID, "mailbox:u"), call(CONNECTOR_ID, "root"), call(CONNECTOR_ID, "c1"),
    ]
    kept = [i for c in processor.note_kept_records.call_args_list for i in c.args[1]]
    assert kept == [*first_page, "r1000"]
    record_reads = processor.get_records_in_record_group.await_args_list
    assert [(c.args[1], c.args[3]) for c in record_reads] == [("root", None), ("root", "r0999"), ("c1", None)]
    # Soft-deleted mail is stored mail too; the unlink must not take it.
    assert all(c.kwargs == {"visibility": RecordVisibility.ALL} for c in record_reads)
    assert processor.get_nodes_by_filters.await_args_list == [
        call(CollectionNames.RECORD_GROUPS.value, {"connectorId": CONNECTOR_ID, "parentExternalGroupId": group_id},
             return_fields=["externalGroupId"], raise_on_error=True)
        for group_id in ("root", "c1")
    ]


async def test_an_unstored_folder_is_not_walked() -> None:
    processor = MagicMock()
    processor.get_record_group_by_external_id = AsyncMock(return_value=None)
    processor.note_kept_record_group = AsyncMock()
    processor.get_nodes_by_filters = AsyncMock()

    assert await inventory(processor)._keep_stored_folder_tree("x") is False

    processor.note_kept_record_group.assert_not_awaited()
    processor.note_kept_records.assert_not_called()
    processor.get_nodes_by_filters.assert_not_awaited()
