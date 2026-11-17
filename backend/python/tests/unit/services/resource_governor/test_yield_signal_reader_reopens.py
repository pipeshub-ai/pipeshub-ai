"""A process that only reads the back-off signal follows a replaced record."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from app.services.resource_governor import yield_signal
from app.services.resource_governor.yield_signal import YieldSignal

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_a_reader_sees_the_record_written_after_the_file_was_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(yield_signal, "REOPEN_CHECK_S", 0.0)
    tmp_path.chmod(0o700)
    reader = YieldSignal(tmp_path)
    assert reader.read_state().active is False
    os.unlink(tmp_path / "yield")
    os.unlink(tmp_path / "yield.lock")
    writer = YieldSignal(tmp_path)
    assert writer.refresh(reason="test", budget=1.0) is not None
    assert reader.read_state().active is True
    reader.close()
    writer.close()
