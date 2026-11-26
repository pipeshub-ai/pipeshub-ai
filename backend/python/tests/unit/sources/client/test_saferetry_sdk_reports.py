"""SR-13: the remaining send paths SR-12 found report a possibly-sent failure.

Outlook's attachment chunks go straight to Graph's upload URL with aiohttp; an MCP tool call goes through the
session's ``call_tool_in_session``; Box runs every SDK call on an executor thread (SR-O1: its toolset is not
enabled, but its datasource must not lose the tool call's tracker when it is).
"""

from __future__ import annotations

from contextlib import suppress
from types import SimpleNamespace
from typing import Any

import pytest
from mcp import MCPError

from app.agents.actions.microsoft.outlook.attachments import OutlookAttachmentUploader
from app.agents.mcp.client import MCPClientManager, _Session
from app.agents.mcp.models import MCPAuthMode, MCPServerConfig, MCPTransport
from app.sources.external.box.box import BoxDataSource
from harness_core.effects.ambiguity import note_ambiguous_send, track_sends
from tests.unit.sources.client.saferetry_fakes import DROP, Step, http_fake


@pytest.mark.parametrize(("step", "reported"), [(DROP, True), (503, True), (200, False)], ids=["lost", "503", "ok"])
async def test_sr_13_an_outlook_attachment_chunk_reports_a_possible_send(step: Step, reported: bool) -> None:
    with http_fake(id="att1") as fake:
        fake.plan = [step]
        # The uploader turns a raised failure into a failed result; only the report matters here.
        with track_sends() as sends, suppress(Exception):
            _ = await OutlookAttachmentUploader(client=None)._put_chunks(  # pyright: ignore[reportPrivateUsage]
                f"http://{fake.address}/upload", b"chunk", 5
            )
    assert fake.writes() == [("PUT", "/upload")]
    assert sends.ambiguous is reported


class _Client:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def call_tool(self, name: str, arguments: dict[str, Any], **_kwargs: Any) -> object:  # noqa: ANN401
        raise self._error


@pytest.mark.parametrize(
    ("error", "reported"),
    [(ConnectionResetError(), True), (TimeoutError(), True), (MCPError(-32602, "bad input"), False)],
    ids=["reset", "timeout", "tool-error"],
)
async def test_sr_13_an_mcp_call_that_failed_after_it_was_sent_is_reported(error: Exception, reported: bool) -> None:
    config = MCPServerConfig(
        id="inst-sr13", org_id="org-1", created_by="u1", name="SR13", type_id="custom",
        transport=MCPTransport.STREAMABLE_HTTP, url="http://93.184.216.34/mcp", auth_mode=MCPAuthMode.NONE,
        created_at=0, updated_at=0,
    )
    manager = MCPClientManager(config)
    manager._session = _Session(  # pyright: ignore[reportPrivateUsage]
        runner=SimpleNamespace(client=_Client(error), running=True), stderr_path=None,  # type: ignore[arg-type]
    )
    with track_sends() as sends, pytest.raises(Exception):  # noqa: B017 - the report is what is checked
        await manager.call_tool_in_session("create_page", {})
    assert sends.ambiguous is reported


async def test_sr_13_a_box_call_keeps_the_tool_calls_tracker_on_its_thread() -> None:
    source = BoxDataSource(SimpleNamespace())  # type: ignore[arg-type]
    source._client = SimpleNamespace(  # pyright: ignore[reportPrivateUsage]
        files=SimpleNamespace(delete_file_by_id=lambda _file_id: note_ambiguous_send("ReadTimeout"))
    )
    with track_sends() as sends:
        _ = await source.files_delete_file_by_id("f1")
    assert sends.ambiguous
