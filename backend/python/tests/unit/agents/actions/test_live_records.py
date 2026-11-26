"""Tools that read a file straight from a connector mark it live (P1b C-18).

Drive, SharePoint and OneDrive build a temporary `FileRecord` so the file
renders like an indexed one. It is never saved, so its id must never look
like a record id, and the record must say where it came from: the connector
kind, the instance that fetched it and the item's external id.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.google.drive.drive import GoogleDrive
from app.agents.actions.microsoft.one_drive.one_drive import OneDrive
from app.agents.actions.microsoft.sharepoint.sharepoint import SharePoint
from app.agents.actions.util.live_record import LiveCitations, live_file_record
from app.config.constants.arangodb import Connectors, OriginTypes
from app.core import org_context
from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat
from app.models.citation_source import (
    LIVE_RECORD_ID_PREFIX,
    is_live_record_id,
    live_record_id,
)
from app.models.entities import FileRecord, LlmTextContent
from tests.support.bound_org import bind_test_org

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _bound_org() -> Iterator[str]:
    """A live record names the org its run or request bound (WP-118); these tool calls bind the state's org."""
    yield from bind_test_org("org-1")


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


class TestLiveRecordId:
    def test_never_looks_like_a_record_id(self) -> None:
        ids = {live_record_id() for _ in range(50)}
        assert len(ids) == 50
        for value in ids:
            assert value.startswith(LIVE_RECORD_ID_PREFIX)
            assert not _is_uuid(value)
            assert is_live_record_id(value)

    @pytest.mark.parametrize("value", ["0b6f4c1e-2d7a-4e39-9c55-0d1f6a7b8c9d", "", None, 7, "LIVE:x", "record-live:x"])
    def test_anything_else_is_not_live(self, value: object) -> None:
        assert not is_live_record_id(value)


class TestLiveFileRecord:
    def test_carries_its_provenance_and_the_run_org(self) -> None:
        state = {"org_id": "org-1", "tool_to_toolset_map": {"drive.get_file_content": "toolset-9"}}
        record = live_file_record(
            state,
            tool="drive.get_file_content",
            connector=Connectors.GOOGLE_DRIVE,
            external_record_id="file-1",
            record_name="plan.pdf",
            mime_type="application/pdf",
            extension="pdf",
        )
        assert isinstance(record, FileRecord)
        assert is_live_record_id(record.id)
        assert record.org_id == "org-1"
        assert record.connector_name == Connectors.GOOGLE_DRIVE
        assert record.connector_id == "toolset-9"
        assert record.external_record_id == "file-1"
        assert record.origin == OriginTypes.CONNECTOR
        assert record.preview_renderable is False
        assert record.mime_type == "application/pdf"

    def test_without_state_it_still_never_names_a_record(self) -> None:
        unbound = org_context.push(None)
        try:
            record = live_file_record(
                None,
                tool="onedrive.get_file_content",
                connector=Connectors.ONEDRIVE,
                external_record_id="item-1",
                record_name="a.txt",
                mime_type=None,
                extension="txt",
            )
        finally:
            org_context.pop(unbound)
        assert is_live_record_id(record.id)
        assert record.connector_id == ""
        assert record.org_id == "", "no org is bound, and none is read off a state"
        assert record.mime_type == "application/octet-stream"


def _parser_capturing(captured: list[FileRecord]) -> MagicMock:
    """Parses to one text block, then renders and registers it as the real parser does."""

    async def parse(record: FileRecord, *_: object, citations: LiveCitations) -> tuple[bool, list[LlmTextContent]]:
        captured.append(record)
        record.block_containers = BlocksContainer(
            blocks=[Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="hello")], block_groups=[]
        )
        content = citations.render(record)
        citations.register(record)
        return True, content

    parser = MagicMock()
    parser.parse = AsyncMock(side_effect=parse)
    return parser


def _assert_citable_in(state: dict[str, Any], record: FileRecord, result: str) -> None:
    """The tool's result cites the file by reference, and the run can resolve it."""
    assert "[ref1] hello" in result
    [registered] = state["tool_records"]
    assert registered["id"] == record.id
    assert state["citation_ref_mapper"].ref_to_url["ref1"].startswith(f"/record/{record.id}/")


def _response(data: object) -> MagicMock:
    resp = MagicMock()
    resp.success = True
    resp.data = data
    resp.error = None
    resp.message = None
    return resp


@pytest.mark.asyncio
async def test_drive_marks_the_file_it_reads_live() -> None:
    drive = GoogleDrive.__new__(GoogleDrive)
    drive.client = AsyncMock()
    drive.client.files_get = AsyncMock(
        return_value={"id": "file-1", "name": "notes.txt", "mimeType": "text/plain", "size": "5", "fileExtension": "txt"}
    )
    drive._files_get_media_bytes = AsyncMock(return_value=b"hello")
    drive.state = {
        "config_service": MagicMock(),
        "org_id": "org-1",
        "tool_to_toolset_map": {"drive.get_file_content": "drive-toolset"},
    }
    captured: list[FileRecord] = []
    with patch("app.agents.actions.google.drive.drive.FileContentParser", return_value=_parser_capturing(captured)):
        ok, result = await drive.get_file_content("file-1")
    assert ok is True
    [record] = captured
    _assert_citable_in(drive.state, record, result)
    assert is_live_record_id(record.id)
    assert (record.connector_name, record.connector_id, record.external_record_id, record.org_id) == (
        Connectors.GOOGLE_DRIVE,
        "drive-toolset",
        "file-1",
        "org-1",
    )


@pytest.mark.asyncio
async def test_sharepoint_marks_the_file_it_reads_live() -> None:
    sharepoint = SharePoint.__new__(SharePoint)
    client = MagicMock()
    client.get_drive_item_metadata = AsyncMock(
        return_value=_response({"name": "doc.txt", "size": 5, "file": {"mimeType": "text/plain"}})
    )
    client.get_drive_item_content = AsyncMock(return_value=_response(b"hello"))
    sharepoint.client = client
    sharepoint.state = {
        "config_service": MagicMock(),
        "org_id": "org-1",
        "tool_to_toolset_map": {"sharepoint.get_file_content": "sp-toolset"},
    }
    captured: list[FileRecord] = []
    with patch(
        "app.agents.actions.microsoft.sharepoint.sharepoint.FileContentParser",
        return_value=_parser_capturing(captured),
    ):
        ok, result = await sharepoint.get_file_content(site_id="site-1", drive_id="drive-1", item_id="item-1")
    assert ok is True
    [record] = captured
    _assert_citable_in(sharepoint.state, record, result)
    assert is_live_record_id(record.id)
    # The drive id is not a connector instance: C-18 needs the instance and the item.
    assert (record.connector_name, record.connector_id, record.external_record_id, record.org_id) == (
        Connectors.SHAREPOINT_ONLINE,
        "sp-toolset",
        "item-1",
        "org-1",
    )


@pytest.mark.asyncio
async def test_onedrive_marks_the_file_it_reads_live() -> None:
    onedrive = OneDrive.__new__(OneDrive)
    onedrive.client = AsyncMock()
    onedrive.client.drives_get_items = AsyncMock(
        return_value=_response({"name": "a.txt", "size": 5, "file": {"mimeType": "text/plain", "fileExtension": "txt"}})
    )
    onedrive.client.drives_items_get_content = AsyncMock(return_value=_response(b"hello"))
    onedrive.state = {
        "config_service": MagicMock(),
        "org_id": "org-1",
        "tool_to_toolset_map": {"onedrive.get_file_content": "od-toolset"},
    }
    captured: list[FileRecord] = []
    with patch(
        "app.agents.actions.microsoft.one_drive.one_drive.FileContentParser",
        return_value=_parser_capturing(captured),
    ):
        ok, result = await onedrive.get_file_content(drive_id="drive-1", item_id="item-1")
    assert ok is True
    [record] = captured
    _assert_citable_in(onedrive.state, record, result)
    assert is_live_record_id(record.id)
    assert (record.connector_name, record.connector_id, record.external_record_id, record.org_id) == (
        Connectors.ONEDRIVE,
        "od-toolset",
        "item-1",
        "org-1",
    )
