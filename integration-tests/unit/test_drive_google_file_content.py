"""The Drive Workspace fixtures' native Google file helper, with Drive stubbed.

A Google Doc created with no body indexes as EMPTY, so a test that waits for COMPLETED on it can never
pass. Given text, the helper must upload it for Drive to convert into the native type.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_UTILS = (
    Path(__file__).resolve().parents[1]
    / "connectors"
    / "google_drive_workspace"
    / "drive_workspace_test_utils.py"
)
_DOC = "application/vnd.google-apps.document"


@pytest.fixture(scope="module")
def utils() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_drive_workspace_utils", _UTILS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Drive:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.uploaded: list[dict[str, Any]] = []

    async def files_create(self, **kwargs: Any) -> dict[str, Any]:
        self.created.append(kwargs)
        return {"id": "empty-doc"}

    async def files_create_with_media(self, **kwargs: Any) -> dict[str, Any]:
        self.uploaded.append(kwargs)
        return {"id": "doc-with-text"}


def test_a_doc_with_content_is_uploaded_as_text_for_drive_to_convert(utils: ModuleType) -> None:
    drive = _Drive()
    file_id = asyncio.run(
        utils.create_drive_google_file(drive, "audit-doc", parent_id="drive-a", mime_type=_DOC, content="hello\n")
    )
    assert file_id == "doc-with-text"
    assert drive.created == []
    (upload,) = drive.uploaded
    assert upload["file_metadata"] == {"name": "audit-doc", "mimeType": _DOC, "parents": ["drive-a"]}
    assert upload["content"] == b"hello\n"
    assert upload["mime_type"] == "text/plain"
    assert upload["supportsAllDrives"] is True


def test_a_doc_without_content_stays_an_empty_stub(utils: ModuleType) -> None:
    drive = _Drive()
    file_id = asyncio.run(utils.create_drive_google_file(drive, "stub", parent_id="drive-a", mime_type=_DOC))
    assert file_id == "empty-doc"
    assert drive.uploaded == []
    assert drive.created[0]["body"] == {"name": "stub", "mimeType": _DOC, "parents": ["drive-a"]}
