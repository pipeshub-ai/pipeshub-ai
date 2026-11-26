from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.failpoints import registry
from tests.support.journal_history import journal_history_check  # noqa: F401  (autouse: JRN-22)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _clean_failpoints() -> Iterator[None]:
    registry.reset()
    yield
    registry.reset()


@pytest.fixture
def failpoint_file(tmp_path: Path) -> Path:
    """Path a test writes arms to (``write_arms``) and hands to a child process."""
    return tmp_path / "failpoints.json"
