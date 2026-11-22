"""The unit-test temp isolation in ``tests/unit/conftest.py`` leaves pytest's own directories alone."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def test_pytest_keeps_its_temp_dirs_outside_the_private_temp_dir(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, _private_temp_dir: str
) -> None:
    """The private dir is removed at the end of the run; pytest's base temp dir keeps
    the last runs' ``tmp_path`` directories for debugging failed tests, so it must not
    be inside it."""
    private = Path(_private_temp_dir).resolve()

    assert private not in tmp_path_factory.getbasetemp().resolve().parents
    assert private not in tmp_path.resolve().parents
    assert Path(tempfile.gettempdir()).resolve() == private
