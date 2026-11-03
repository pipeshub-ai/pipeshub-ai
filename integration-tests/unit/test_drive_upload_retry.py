"""The Drive Workspace fixtures' upload retry, with Drive stubbed.

A converting upload now and then gets no response, after Drive either created
the file or never finished it. The retry must reuse a file that landed rather
than upload a second copy, and must give up after its last attempt.
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


@pytest.fixture(scope="module")
def utils() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_drive_workspace_utils", _UTILS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Drive:
    """files.list answers with whatever the parent holds; queries are recorded."""

    def __init__(self, children: list[dict[str, Any]] | None = None) -> None:
        self.children = children or []
        self.queries: list[str] = []

    async def files_list(self, **kwargs: Any) -> dict[str, Any]:
        self.queries.append(kwargs["q"])
        return {"files": self.children}


def _upload(outcomes: list[Any]) -> tuple[Any, list[int]]:
    calls: list[int] = []

    async def upload() -> dict[str, Any]:
        calls.append(1)
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return upload, calls


def _run(utils: ModuleType, drive: _Drive, upload: Any, name: str = "Gantt chart.xlsx") -> Any:
    return asyncio.run(utils._create_upload_once(drive, "parent-1", name, upload))


def test_a_timed_out_upload_that_landed_is_reused_not_uploaded_again(utils: ModuleType) -> None:
    drive = _Drive([{"id": "landed", "name": "Gantt chart", "mimeType": "sheet"}])
    upload, calls = _upload([TimeoutError("read timed out")])

    assert _run(utils, drive, upload)["id"] == "landed"
    assert len(calls) == 1


def test_a_timed_out_upload_that_never_landed_is_retried(utils: ModuleType) -> None:
    upload, calls = _upload([TimeoutError(), {"id": "second-try"}])

    assert _run(utils, _Drive(), upload)["id"] == "second-try"
    assert len(calls) == 2


def test_the_last_timeout_is_raised(utils: ModuleType) -> None:
    upload, calls = _upload([TimeoutError()] * utils.DRIVE_UPLOAD_ATTEMPTS)

    with pytest.raises(TimeoutError):
        _run(utils, _Drive(), upload)
    assert len(calls) == utils.DRIVE_UPLOAD_ATTEMPTS


def test_other_errors_are_not_retried(utils: ModuleType) -> None:
    upload, calls = _upload([RuntimeError("403 forbidden")])

    with pytest.raises(RuntimeError):
        _run(utils, _Drive(), upload)
    assert len(calls) == 1


def test_the_lookup_matches_the_converted_name_and_escapes_quotes(utils: ModuleType) -> None:
    drive = _Drive()
    upload, _ = _upload([TimeoutError(), {"id": "ok"}])

    _run(utils, drive, upload, name="It's a plan.docx")

    query = drive.queries[0]
    assert "'parent-1' in parents" in query
    assert "name = 'It\\'s a plan.docx'" in query
    assert "name = 'It\\'s a plan'" in query
    assert "trashed = false" in query
