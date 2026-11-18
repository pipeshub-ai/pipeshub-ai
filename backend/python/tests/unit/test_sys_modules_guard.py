"""`tests.support.sys_modules_guard`: a patch.dict(sys.modules) that drops a module fails at its own line.

These tests are also the canary for the private API the guard wraps: if a Python stopped calling
``_patch_dict._unpatch_dict`` on exit, the guard would go quiet and only these tests would notice. Don't skip them.
"""

from __future__ import annotations

import importlib
import sys
import uuid
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from tests.support.sys_modules_guard import ModulesDroppedError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

STUB = object()


@pytest.fixture
def fresh_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A never-imported package ``<name>`` with a submodule ``<name>.child``; whatever this test leaves imported is removed."""
    name = f"f59_probe_{uuid.uuid4().hex}"
    (tmp_path / name).mkdir()
    (tmp_path / name / "__init__.py").write_text("")
    (tmp_path / name / "child.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield name
    for module in (name, f"{name}.child"):
        sys.modules.pop(module, None)


def test_a_module_first_imported_inside_the_patch_fails_at_the_patch(fresh_package: str) -> None:
    with pytest.raises(ModulesDroppedError, match=fresh_package), patch.dict(sys.modules, {"f59_stub": STUB}):
        importlib.import_module(fresh_package)

    assert fresh_package not in sys.modules
    assert "f59_stub" not in sys.modules


def test_a_module_imported_before_the_patch_is_kept(fresh_package: str) -> None:
    module = importlib.import_module(fresh_package)

    with patch.dict(sys.modules, {"f59_stub": STUB}):
        assert importlib.import_module(fresh_package) is module

    assert sys.modules[fresh_package] is module


def test_stub_keys_are_restored_and_not_reported(fresh_package: str) -> None:
    module = importlib.import_module(fresh_package)

    with patch.dict(sys.modules, {"f59_stub": STUB, fresh_package: STUB}):
        assert sys.modules[fresh_package] is STUB

    assert "f59_stub" not in sys.modules
    assert sys.modules[fresh_package] is module


def test_the_string_target_is_checked(fresh_package: str) -> None:
    with pytest.raises(ModulesDroppedError, match=fresh_package), patch.dict("sys.modules", {"f59_stub": STUB}):
        importlib.import_module(fresh_package)


def test_the_decorator_form_is_checked(fresh_package: str) -> None:
    @patch.dict(sys.modules, {"f59_stub": STUB})
    def body() -> None:
        importlib.import_module(fresh_package)

    with pytest.raises(ModulesDroppedError, match=fresh_package):
        body()


async def test_the_async_decorator_form_is_checked(fresh_package: str) -> None:
    @patch.dict(sys.modules, {"f59_stub": STUB})
    async def body() -> None:
        importlib.import_module(fresh_package)

    with pytest.raises(ModulesDroppedError, match=fresh_package):
        await body()


def test_start_and_stop_are_checked(fresh_package: str) -> None:
    patcher = patch.dict(sys.modules, {"f59_stub": STUB})
    patcher.start()
    importlib.import_module(fresh_package)

    with pytest.raises(ModulesDroppedError, match=fresh_package):
        patcher.stop()


def test_a_drop_is_reported_even_when_the_body_raised(fresh_package: str) -> None:
    body_error = ValueError("the body's own failure")

    with pytest.raises(ModulesDroppedError) as caught, patch.dict(sys.modules, {"f59_stub": STUB}):
        importlib.import_module(fresh_package)
        raise body_error

    assert caught.value.__context__ is body_error


def test_the_error_lists_every_dropped_module(fresh_package: str) -> None:
    with pytest.raises(ModulesDroppedError) as caught, patch.dict(sys.modules, {"f59_stub": STUB}):
        importlib.import_module(f"{fresh_package}.child")

    assert f"dropped 2 module(s) first imported inside it: {fresh_package}, {fresh_package}.child." in str(caught.value)


def test_other_dicts_are_restored_as_before() -> None:
    target = {"kept": 1}

    with patch.dict(target, {"stub": 2}):
        target["added_inside"] = 3

    assert target == {"kept": 1}
