"""H2 LC-1..LC-5 (P2.2 open item): with ``INSTANCE_TOOL_IDS`` on, a live record names the connector instance whose
credentials fetched it, not the one ``tool_to_toolset_map`` (keyed by type) names for the type.

Two Drive instances A and B: the map says A for ``drive.get_file_content``; B's action reads a file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.google.drive.drive import GoogleDrive
from app.agents.actions.microsoft.one_drive.one_drive import OneDrive
from app.agents.actions.microsoft.sharepoint.sharepoint import SharePoint
from app.agents.actions.util.live_record import (
    bind_toolset_instance,
    live_file_record,
    toolset_instance_of,
)
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.instance_creator import ToolInstanceCreator
from app.agents.tools.factories.registry import ClientFactoryRegistry
from app.config.constants.arangodb import Connectors
from tests.unit.agents.actions.test_live_records import _parser_capturing, _response

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.models.entities import FileRecord

MAP_SAYS = "instance-a"
OWN = "instance-b"


@pytest.fixture(params=[True, False], ids=["flag_on", "flag_off"])
def flag(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> bool:
    monkeypatch.setenv("INSTANCE_TOOL_IDS", "true" if request.param else "false")
    return bool(request.param)


def _state(tool: str, mapped: str | None = MAP_SAYS) -> dict[str, Any]:
    return {"config_service": MagicMock(), "org_id": "org-1", "tool_to_toolset_map": {tool: mapped} if mapped else {}}


async def _drive(mapped: str | None) -> tuple[Any, Callable[[], Awaitable[tuple[bool, str]]], str]:
    drive = GoogleDrive.__new__(GoogleDrive)
    drive.client = AsyncMock()
    drive.client.files_get = AsyncMock(
        return_value={"id": "file-1", "name": "notes.txt", "mimeType": "text/plain", "size": "5", "fileExtension": "txt"}
    )
    drive._files_get_media_bytes = AsyncMock(return_value=b"hello")
    drive.state = _state("drive.get_file_content", mapped)
    return drive, lambda: drive.get_file_content("file-1"), "app.agents.actions.google.drive.drive"


async def _onedrive(mapped: str | None) -> tuple[Any, Callable[[], Awaitable[tuple[bool, str]]], str]:
    onedrive = OneDrive.__new__(OneDrive)
    onedrive.client = AsyncMock()
    onedrive.client.drives_get_items = AsyncMock(
        return_value=_response({"name": "a.txt", "size": 5, "file": {"mimeType": "text/plain", "fileExtension": "txt"}})
    )
    onedrive.client.drives_items_get_content = AsyncMock(return_value=_response(b"hello"))
    onedrive.state = _state("onedrive.get_file_content", mapped)
    return onedrive, lambda: onedrive.get_file_content(drive_id="d", item_id="item-1"), "app.agents.actions.microsoft.one_drive.one_drive"


async def _sharepoint(mapped: str | None) -> tuple[Any, Callable[[], Awaitable[tuple[bool, str]]], str]:
    sharepoint = SharePoint.__new__(SharePoint)
    client = MagicMock()
    client.get_drive_item_metadata = AsyncMock(
        return_value=_response({"name": "doc.txt", "size": 5, "file": {"mimeType": "text/plain"}})
    )
    client.get_drive_item_content = AsyncMock(return_value=_response(b"hello"))
    sharepoint.client = client
    sharepoint.state = _state("sharepoint.get_file_content", mapped)
    call = lambda: sharepoint.get_file_content(site_id="s", drive_id="d", item_id="item-1")  # noqa: E731
    return sharepoint, call, "app.agents.actions.microsoft.sharepoint.sharepoint"


BUILDERS = {"drive": _drive, "onedrive": _onedrive, "sharepoint": _sharepoint}


async def _read(kind: str, *, mapped: str | None = MAP_SAYS, own: str | None = OWN) -> tuple[bool, list[FileRecord]]:
    action, call, module = await BUILDERS[kind](mapped)
    if own is not None:
        bind_toolset_instance(action, own)
    captured: list[FileRecord] = []
    with patch(f"{module}.FileContentParser", return_value=_parser_capturing(captured)):
        ok, _ = await call()
    return ok, captured


@pytest.mark.parametrize("kind", sorted(BUILDERS))
async def test_lc1_lc2_lc3_the_live_record_names_the_instance_that_read_it(kind: str, flag: bool) -> None:
    ok, [record] = await _read(kind)
    assert ok is True
    assert record.connector_id == (OWN if flag else MAP_SAYS)


async def test_lc5_drive_reads_when_only_its_own_instance_is_known(flag: bool) -> None:
    ok, captured = await _read("drive", mapped=None)
    assert ok is flag
    assert [r.connector_id for r in captured] == ([OWN] if flag else [])


def test_lc2_the_record_without_a_bound_instance_keeps_the_map_s(flag: bool) -> None:
    record = live_file_record(
        _state("drive.get_file_content"), tool="drive.get_file_content", connector=Connectors.GOOGLE_DRIVE,
        external_record_id="f", record_name="f.txt", mime_type=None, extension="txt",
    )
    assert record.connector_id == MAP_SAYS


class _Factory:
    async def create_client(self, config_service: Any, logger: Any, config: dict, state: dict) -> dict:  # noqa: ANN401
        return {"config": config}


class _Drive:
    def __init__(self, client: dict) -> None:
        self.client = client


async def test_lc4_the_creator_binds_the_instance_whose_credentials_built_the_action(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _Factory()  # one factory under both names, as Google registers it
    monkeypatch.setattr(ClientFactoryRegistry, "_factories", {"drive": factory, "googledrive": factory})
    monkeypatch.setattr(ClientFactoryRegistry, "_initialized", True)
    context = AgentContext(
        org_id="org-1", user_id="user-1", user_email="u@example.com", logger=MagicMock(),
        retrieval_service=MagicMock(config_service=MagicMock()),
        agent_toolsets=[{"name": "googledrive", "instanceId": MAP_SAYS}, {"name": "googledrive", "instanceId": OWN}],
        toolset_configs={MAP_SAYS: {"auth": {"a": 1}}, OWN: {"auth": {"b": 2}}},
    )
    context.tool_to_toolset_map = {"drive.get_file_content": MAP_SAYS}
    creator = ToolInstanceCreator(context)
    named = await creator.create_instance_async(_Drive, "drive", instance_id=OWN)
    by_tool = await creator.create_instance_async(_Drive, "drive", "drive.get_file_content")
    by_app = await creator.create_instance_async(_Drive, "drive")
    assert toolset_instance_of(named) == OWN
    assert toolset_instance_of(by_tool) == MAP_SAYS
    assert toolset_instance_of(by_app) == MAP_SAYS
    assert toolset_instance_of(object()) is None
