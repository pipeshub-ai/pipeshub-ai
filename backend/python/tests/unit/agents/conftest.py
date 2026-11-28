"""Tests here drive the agent loop's entry points directly, below the HTTP routes,
so the loop's own caller check is admitted for them (`tests/support/loop_caller.py`)."""

from __future__ import annotations

import pytest

from tests.support.loop_caller import admit_loop_callers


@pytest.fixture(autouse=True)
def _loop_caller_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    admit_loop_callers(monkeypatch)
