"""Box file and folder records carry who created, last modified and owns them."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.box.connector import BoxConnector
from app.models.entities import SourcePerson


@pytest.fixture()
def box_connector() -> BoxConnector:
    processor = MagicMock()
    processor.org_id = "org-box"
    processor.get_record_by_external_id = AsyncMock(return_value=None)
    with patch("app.connectors.sources.box.connector.BoxApp"):
        connector = BoxConnector(
            logger=logging.getLogger("test.box.people"),
            data_entities_processor=processor,
            data_store_provider=MagicMock(),
            config_service=AsyncMock(),
            connector_id="box-1",
            scope="personal",
            created_by="test-user-id",
        )
    connector.sync_filters = FilterCollection()
    connector._cached_date_filters = (None, None, None, None)
    connector._get_permissions = AsyncMock(return_value=[])
    return connector


# Shape of a folder-items entry with the people fields requested.
FILE_ENTRY = {
    "type": "file",
    "id": "1234567",
    "name": "plan.docx",
    "size": 2048,
    "created_at": "2026-01-01T10:00:00-08:00",
    "modified_at": "2026-02-01T10:00:00-08:00",
    "etag": "3",
    "path_collection": {"total_count": 1, "entries": [{"type": "folder", "id": "0", "name": "All Files"}]},
    "created_by": {"type": "user", "id": "111", "name": "Ann Author", "login": "Ann@Acme.com"},
    "modified_by": {"type": "user", "id": "222", "name": "Ed Editor", "login": "ed@acme.com"},
    "owned_by": {"type": "user", "id": "333", "name": "Olga Owner", "login": "olga@acme.com"},
}


async def test_file_entry_maps_creator_modifier_and_owner(box_connector) -> None:
    update = await box_connector._process_box_entry(FILE_ENTRY, "333", "olga@acme.com", "rg-1")

    record = update.record
    assert record.authored_by == SourcePerson(source_id="111", email="Ann@Acme.com", display_name="Ann Author")
    assert record.last_modified_by == SourcePerson(source_id="222", email="ed@acme.com", display_name="Ed Editor")
    assert record.owners == [SourcePerson(source_id="333", email="olga@acme.com", display_name="Olga Owner")]
    assert record.created_by is None


async def test_entry_without_people_fields_names_no_one(box_connector) -> None:
    entry = {k: v for k, v in FILE_ENTRY.items() if k not in ("created_by", "modified_by", "owned_by")}

    record = (await box_connector._process_box_entry(entry, "333", "olga@acme.com", "rg-1")).record

    assert record.authored_by is None
    assert record.last_modified_by is None
    assert record.owners == []


async def test_service_account_and_anonymous_users_are_marked(box_connector) -> None:
    entry = {
        **FILE_ENTRY,
        "created_by": {"type": "user", "id": "999", "name": "Sync App",
                       "login": "AutomationUser_123_abc@boxdevedition.com"},
        "modified_by": {"type": "user", "id": "", "name": "Anonymous User", "login": ""},
    }

    record = (await box_connector._process_box_entry(entry, "333", "olga@acme.com", "rg-1")).record

    assert record.authored_by is not None and record.authored_by.is_service_account
    assert record.last_modified_by is None


async def test_folder_listing_requests_people_fields(box_connector) -> None:
    data_source = MagicMock()
    data_source.folders_get_folder_items = AsyncMock(
        return_value=MagicMock(success=True, data={"entries": [], "total_count": 0}),
    )
    box_connector.data_source = data_source

    await box_connector._sync_folder_contents_recursively("333", "0", [])

    fields = data_source.folders_get_folder_items.call_args.kwargs["fields"].split(",")
    assert {"created_by", "modified_by", "owned_by"} <= set(fields)
