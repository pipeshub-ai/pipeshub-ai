"""fresh_copy builds a module again under the current env and leaves the shared module and sys.modules alone."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.support.fresh_module import fresh_copy


def _load_unregistered(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_copy_sees_the_current_env_and_the_shared_module_does_not_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "env_pick.py"
    path.write_text('import os\nVALUE = os.environ.get("FRESH_MODULE_TEST", "default")\n')
    monkeypatch.delenv("FRESH_MODULE_TEST", raising=False)
    shared = _load_unregistered(path, "env_pick_for_fresh_module_test")
    monkeypatch.setitem(sys.modules, shared.__name__, shared)
    modules_before = set(sys.modules)

    monkeypatch.setenv("FRESH_MODULE_TEST", "other")
    first = fresh_copy(shared)
    second = fresh_copy(shared)

    assert (first.VALUE, second.VALUE, shared.VALUE) == ("other", "other", "default")
    assert first is not shared and second is not first
    assert (first.__name__, first.__file__) == (shared.__name__, shared.__file__)
    assert set(sys.modules) == modules_before
    assert sys.modules[shared.__name__] is shared


def test_a_module_without_a_source_file_is_refused() -> None:
    with pytest.raises(AttributeError, match="__file__"):
        fresh_copy(sys)
