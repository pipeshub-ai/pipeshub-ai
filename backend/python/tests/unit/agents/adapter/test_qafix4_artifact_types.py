"""QAFIX4 B3: a file ``run_code`` produces is registered with its real type, whatever the host's ``mimetypes`` table.

QA-FP12 task 3: the service image's Python has no Office types and no ``/etc/mime.types``, so ``deck.pptx`` was stored
as ``OTHER`` / ``application/octet-stream`` and the chat showed "Other" with a PDF badge. The type comes from the one
explicit extension table (``harness_core.mime``), else from the file's leading bytes.
"""

from __future__ import annotations

import io
import zipfile
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
from app.agent_loop_lib.sandbox.manager import SandboxManager, SandboxType
from app.agent_loop_lib.tools.base import ToolOutput
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.sandbox_bridge import coding_sandbox_artifact_bridge
from app.models.entities import ArtifactType
from harness_core.mime import mime_for
from tests.unit.agents.adapter.test_sandbox_bridge import (
    _FakeBackend,
    _make_context,
    _make_mock_registry,
    _noop_next,
)

PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


_ZIP_TIME = (2026, 1, 1, 0, 0, 0)


def _ooxml(part: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        # A fixed timestamp: the bytes are a test id, and xdist workers that collect a second apart must agree.
        z.writestr(zipfile.ZipInfo("[Content_Types].xml", date_time=_ZIP_TIME), "<Types/>")
        z.writestr(zipfile.ZipInfo(part, date_time=_ZIP_TIME), "<x/>")
    return buf.getvalue()


def _no_host_table() -> object:
    """The service image: ``mimetypes`` knows nothing about Office files."""
    return patch("mimetypes.guess_type", return_value=(None, None))


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("deck.pptx", b"", PPTX),
        ("brief.docx", b"", DOCX),
        ("totals.xlsx", b"", XLSX),
        ("report.PDF", b"", "application/pdf"),
        ("chart.png", b"", "image/png"),
        ("deck", _ooxml("ppt/presentation.xml"), PPTX),
        ("brief", _ooxml("word/document.xml"), DOCX),
        ("totals", _ooxml("xl/workbook.xml"), XLSX),
        ("bundle", _ooxml("data/a.txt"), "application/zip"),
        ("scan", b"%PDF-1.7\n...", "application/pdf"),
        ("logo", b"\x89PNG\r\n\x1a\n....", "image/png"),
        ("photo", b"\xff\xd8\xff\xe0....", "image/jpeg"),
        ("anim", b"GIF89a....", "image/gif"),
        ("pic", b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        ("blob", b"\x00\x01\x02", "application/octet-stream"),
    ],
)
def test_the_type_comes_from_the_extension_else_the_leading_bytes(name: str, content: bytes, expected: str) -> None:
    with _no_host_table():
        assert mime_for(name, content) == expected


async def test_a_pptx_from_run_code_is_registered_as_a_presentation(monkeypatch: pytest.MonkeyPatch) -> None:
    context = _make_context()
    registry = _make_mock_registry()
    monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
    manager = SandboxManager()
    files = {"output/deck.pptx": _ooxml("ppt/presentation.xml"), "output/summary": b"%PDF-1.7\n"}
    manager.register_backend_factory(SandboxType.CODING, lambda: _FakeBackend(files))
    sandbox_id, _ = await manager.get_or_create(SandboxType.CODING)
    ctx = ToolResultContext(
        tool_path="/toolsets/coding_sandbox/run_code",
        tool_use_id=uuid4(),
        tool_response=ToolOutput(success=True, data={"artifacts": sorted(files), "sandbox_id": sandbox_id}),
    )
    with _no_host_table(), patch("app.agents.agent_loop.sandbox_bridge.register_task"):
        await coding_sandbox_artifact_bridge(context, manager)(ctx, _noop_next)
    registered = {c.kwargs["name"]: (c.kwargs["artifact_type"], c.kwargs["mime_type"]) for c in registry.register_output.call_args_list}
    assert registered == {
        "deck.pptx": (ArtifactType.PRESENTATION, PPTX),
        "summary": (ArtifactType.DOCUMENT, "application/pdf"),
    }
