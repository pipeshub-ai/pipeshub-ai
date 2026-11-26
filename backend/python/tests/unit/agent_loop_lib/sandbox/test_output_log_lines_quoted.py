"""Names from a run's archive are quoted in the log, so a newline in a file
name cannot forge extra log lines."""

from __future__ import annotations

import io
import logging
import tarfile
from typing import TYPE_CHECKING

from app.agent_loop_lib.sandbox.coding.output_limits import (
    ExtractBudget,
    extract_tar_stream,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_an_oversized_member_with_a_newline_logs_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("SANDBOX_OUTPUT_MAX_FILE_BYTES", "4")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name="output/a\nCRITICAL forged line")
        info.size = 10
        tar.addfile(info, io.BytesIO(b"x" * 10))
    with caplog.at_level(logging.WARNING):
        extract_tar_stream([buf.getvalue()], "/output", str(tmp_path), ExtractBudget.for_output())
    lines = [r.getMessage() for r in caplog.records if "oversized" in r.getMessage()]
    assert lines
    assert all("\n" not in line for line in lines)
