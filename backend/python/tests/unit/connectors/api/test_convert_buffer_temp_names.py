"""convert_buffer_to_pdf_stream never writes its input outside its own temp directory,
whatever the record is called, and no router call hands a convert target to a
connector's stream_record. The converter is faked: the tests pin which path it is
given and what is left on disk.
"""

from __future__ import annotations

import ast
import inspect
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.connectors.api import router as router_module
from app.connectors.api.router import convert_buffer_to_pdf_stream

_ROUTER = "app.connectors.api.router"

# Names with no file name left once the directories are dropped: nothing sensible to convert.
NO_FILE_NAME = ["..", ".", "/", "", None, "a/..", "/..", "../.."]
RECORD_NAMES = [
    *NO_FILE_NAME, "../../x.pptx", "/etc/cron.d/x.pptx",
    "/usr/lib/python3.12/site-packages/x.pth", "x/../../y.pptx", "..\\..\\w.pptx", "deck.pptx/",
    ".libreoffice-profile", "-env:UserInstallation=file:///etc", "deck.pptx", "deck", "...",
]


@pytest.fixture
def nested_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Temp dirs are created two levels below tmp_path, so '../..' lands in tmp_path, where
    the test can see it."""
    base = tmp_path / "outer" / "work"
    base.mkdir(parents=True)
    monkeypatch.setattr(tempfile, "tempdir", str(base))
    return tmp_path


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


class TestConvertBufferToPdfStream:
    @pytest.mark.parametrize("extension", ["pptx", "ppt", "epub", None, "", "exe"])
    @pytest.mark.parametrize("name", RECORD_NAMES)
    async def test_the_input_file_never_lands_outside_the_temp_dir(
        self, nested_tmp: Path, name: str | None, extension: str | None
    ) -> None:
        seen: list[tuple[str, bool, bool]] = []

        async def fake_convert(file_path: str, temp_dir: str) -> str:
            inside = os.path.dirname(os.path.realpath(file_path)) == os.path.realpath(temp_dir)
            seen.append((file_path, os.path.isfile(file_path), inside))
            pdf = os.path.join(temp_dir, "out.pdf")
            Path(pdf).write_bytes(b"%PDF-fake")
            return pdf

        outcome: StreamingResponse | Exception
        with patch(f"{_ROUTER}.convert_to_pdf", side_effect=fake_convert):
            try:
                outcome = await convert_buffer_to_pdf_stream(b"bytes", name, extension)
            except Exception as exc:  # a name with no file name may fail, but must not escape
                outcome = exc

        assert _tree(nested_tmp) == ["outer", os.path.join("outer", "work")], "something was left or written outside"
        if isinstance(outcome, Exception):
            # An HTTPException is the route refusing a format on purpose (EPUB previews, where it does).
            refused = isinstance(outcome, HTTPException)
            assert refused or name in NO_FILE_NAME, f"a usable name failed to convert: {outcome!r}"
            assert seen == [], "the converter ran and then the call failed"
        else:
            assert isinstance(outcome, StreamingResponse)
            assert len(seen) == 1
            file_path, is_file, inside = seen[0]
            assert is_file and inside, file_path


def test_no_router_call_passes_convert_to_into_stream_record() -> None:
    """The Drive and Gmail connectors write a temp file named after the record only when
    stream_record is asked to convert, so the router must never ask."""
    tree = ast.parse(inspect.getsource(router_module))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "stream_record"
    ]
    assert calls
    for call in calls:
        assert len(call.args) <= 2, ast.dump(call)
        assert not [k.arg for k in call.keywords if k.arg in ("convertTo", "convert_to")]
        if len(call.args) == 2:
            second = call.args[1]
            assert isinstance(second, ast.Name) and second.id == "user_id", ast.dump(second)
