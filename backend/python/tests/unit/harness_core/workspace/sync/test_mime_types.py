"""QAFIX2: a file an engine publishes is typed by one explicit table, not by whatever ``/etc/mime.types`` the
host has; Python's built-in table alone knows no Office type, so a published .pptx was typed "Other"."""

from __future__ import annotations

import mimetypes

import pytest

from harness_core.workspace.sync.tar_sync import (
    _guess_mime_type,  # pyright: ignore[reportPrivateUsage]
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("out/qa4-deck.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ("out/report.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("out/Sheet.XLSX", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("out/data.csv", "text/csv"),
        ("README.md", "text/markdown"),
        ("bin/tool", "application/octet-stream"),
    ],
)
def test_published_files_are_typed_without_a_system_mime_table(
    monkeypatch: pytest.MonkeyPatch, path: str, expected: str
) -> None:
    monkeypatch.setattr(mimetypes, "knownfiles", [])
    assert _guess_mime_type(path) == expected


def test_a_type_only_python_knows_still_resolves() -> None:
    assert _guess_mime_type("app.js") == "text/javascript"
