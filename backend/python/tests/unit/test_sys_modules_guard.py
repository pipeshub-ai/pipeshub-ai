"""`tests.support.sys_modules_guard`: sys.modules changes a test leaves behind fail that test, not a later one.

A patch.dict(sys.modules) that drops a module fails at its own line. These tests are also the canary for the private
API that check wraps: if a Python stopped calling ``_patch_dict._unpatch_dict`` on exit, it would go quiet and only
these tests would notice. Don't skip them.

The rest pins the snapshot rule: a dropped module, a stub or a second copy left for an importable module is put back and
reported, by the per-test fixture at teardown and by the collection hook for a test module's import.
"""

from __future__ import annotations

import importlib.machinery
import os
import subprocess
import sys
import textwrap
import types
import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from tests.support.sys_modules_guard import (
    ModulesChangedError,
    ModulesDroppedError,
    sys_modules_unchanged,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

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


# --- the snapshot rule: sys_modules_unchanged, shared by the per-test fixture and the collection hook ---


def test_a_dropped_module_is_reported_and_put_back(fresh_package: str) -> None:
    module = importlib.import_module(fresh_package)

    with pytest.raises(ModulesChangedError, match=rf"{fresh_package} \(dropped\)"), sys_modules_unchanged("t"):
        del sys.modules[fresh_package]

    assert sys.modules[fresh_package] is module


@pytest.mark.parametrize("make_stub", [types.ModuleType, lambda name: MagicMock()], ids=["module_type", "magic_mock"])
def test_a_stub_left_for_an_importable_module_is_reported_and_removed(fresh_package: str, make_stub) -> None:
    with pytest.raises(ModulesChangedError, match=rf"{fresh_package} \(stub\)"), sys_modules_unchanged("t"):
        sys.modules[fresh_package] = make_stub(fresh_package)

    assert fresh_package not in sys.modules


def test_a_spec_carrying_fake_is_a_stub_too(fresh_package: str) -> None:
    real = importlib.import_module(fresh_package)

    with pytest.raises(ModulesChangedError, match=rf"{fresh_package} \(stub\)"), sys_modules_unchanged("t"):
        sys.modules[fresh_package] = MagicMock(spec=real)

    assert sys.modules[fresh_package] is real


def test_a_second_copy_is_reported_and_the_first_put_back(fresh_package: str) -> None:
    first = importlib.import_module(fresh_package)

    with pytest.raises(ModulesChangedError, match=rf"{fresh_package} \(copy\)"), sys_modules_unchanged("t"):
        del sys.modules[fresh_package]
        assert importlib.import_module(fresh_package) is not first

    assert sys.modules[fresh_package] is first


def test_a_second_copy_of_a_submodule_is_unbound_from_its_parent_too(fresh_package: str) -> None:
    child = f"{fresh_package}.child"
    first = importlib.import_module(child)
    package = sys.modules[fresh_package]

    with pytest.raises(ModulesChangedError, match=rf"{child} \(copy\)"), sys_modules_unchanged("t"):
        del sys.modules[child]
        assert importlib.import_module(child) is not first  # the import also rebinds package.child

    assert sys.modules[child] is first
    assert package.child is first


def test_a_first_import_is_kept_and_not_reported(fresh_package: str) -> None:
    with sys_modules_unchanged("t"):
        module = importlib.import_module(fresh_package)

    assert sys.modules[fresh_package] is module


def test_a_module_imported_and_removed_inside_is_not_reported(fresh_package: str) -> None:
    with sys_modules_unchanged("t"):
        importlib.import_module(fresh_package)
        del sys.modules[fresh_package]

    assert fresh_package not in sys.modules


def test_a_stub_for_a_name_nothing_can_import_is_not_reported() -> None:
    name = f"f59b_missing_{uuid.uuid4().hex}"
    try:
        with sys_modules_unchanged("t"):
            sys.modules[name] = types.ModuleType(name)  # a shim for a package that isn't installed
    finally:
        del sys.modules[name]


def test_a_plain_folder_on_the_path_is_not_a_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    name = f"f59b_data_{uuid.uuid4().hex}"
    (tmp_path / name).mkdir()
    (tmp_path / name / "notes.txt").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setitem(sys.modules, name, None)  # removed again, whatever happens below

    with sys_modules_unchanged("t"):
        sys.modules[name] = types.ModuleType(name)


@pytest.mark.parametrize(
    "filename",
    ["mod.pyc", f"mod{importlib.machinery.EXTENSION_SUFFIXES[0]}"],
    ids=["sourceless", "extension"],
)
def test_a_namespace_folder_holding_only_a_compiled_module_is_a_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str
) -> None:
    top = f"f59b_compiled_{uuid.uuid4().hex}"
    (tmp_path / top).mkdir()
    (tmp_path / top / filename).write_bytes(b"")  # never imported: the walk only looks at names
    monkeypatch.syspath_prepend(str(tmp_path))
    name = f"{top}.mod"

    with pytest.raises(ModulesChangedError, match=rf"{name} \(stub\)"), sys_modules_unchanged("t"):
        sys.modules[name] = types.ModuleType(name)

    assert name not in sys.modules


def test_a_real_module_stored_under_another_name_is_not_reported(fresh_package: str) -> None:
    with sys_modules_unchanged("t"):
        sys.modules[fresh_package] = os  # an alias, like the app's sys.modules["cchardet"] = chardet

    assert sys.modules.pop(fresh_package) is os


def test_monkeypatch_setitem_and_delitem_are_not_reported(fresh_package: str) -> None:
    module = importlib.import_module(fresh_package)

    with sys_modules_unchanged("t"), pytest.MonkeyPatch.context() as m:
        m.setitem(sys.modules, f"{fresh_package}.child", types.ModuleType("stub"))
        m.delitem(sys.modules, fresh_package)

    assert sys.modules[fresh_package] is module


def test_a_stub_under_a_namespace_package_that_is_not_imported_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # app.api and app.api.routes are namespace packages: the toolsets stub hid behind them.
    top = f"f59b_ns_{uuid.uuid4().hex}"
    (tmp_path / top / "sub").mkdir(parents=True)
    (tmp_path / top / "sub" / "mod.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    name = f"{top}.sub.mod"

    with pytest.raises(ModulesChangedError, match=rf"{name} \(stub\)"), sys_modules_unchanged("t"):
        sys.modules[name] = types.ModuleType(name)

    assert name not in sys.modules and top not in sys.modules


def test_a_namespace_package_whose_parent_was_dropped_is_walked_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    top = f"f59b_regular_{uuid.uuid4().hex}"
    (tmp_path / top / "ns").mkdir(parents=True)  # top is a regular package, top.ns a namespace package inside it
    (tmp_path / top / "__init__.py").write_text("")
    (tmp_path / top / "ns" / "mod.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    name = f"{top}.ns.mod"
    module = importlib.import_module(name)
    for loaded in (top, f"{top}.ns", name):
        monkeypatch.setitem(sys.modules, loaded, sys.modules[loaded])  # removed again at teardown

    with pytest.raises(ModulesChangedError) as caught, sys_modules_unchanged("t"):
        del sys.modules[top]  # top.ns stays loaded; iterating its __path__ now raises KeyError
        sys.modules[name] = types.ModuleType(name)

    assert f"{top} (dropped)" in str(caught.value) and f"{name} (stub)" in str(caught.value)
    assert sys.modules[name] is module


def test_a_namespace_package_whose_parent_lost_its_path_is_walked_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    top = f"f59b_regular_{uuid.uuid4().hex}"
    (tmp_path / top / "ns").mkdir(parents=True)
    (tmp_path / top / "__init__.py").write_text("")
    (tmp_path / top / "ns" / "mod.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    name = f"{top}.ns.mod"
    module = importlib.import_module(name)
    package = sys.modules[top]
    for loaded in (top, f"{top}.ns", name):
        monkeypatch.setitem(sys.modules, loaded, sys.modules[loaded])  # removed again at teardown

    with pytest.raises(ModulesChangedError) as caught, sys_modules_unchanged("t"):
        sys.modules[top] = types.ModuleType(top)  # no __path__: iterating top.ns's __path__ raises AttributeError
        sys.modules[name] = types.ModuleType(name)

    assert f"{top} (stub)" in str(caught.value) and f"{name} (stub)" in str(caught.value)
    assert sys.modules[top] is package and sys.modules[name] is module


def test_a_stubbed_parent_is_put_back_before_its_rebound_child(fresh_package: str) -> None:
    child = f"{fresh_package}.child"
    first = importlib.import_module(child)
    package = sys.modules[fresh_package]

    with pytest.raises(ModulesChangedError), sys_modules_unchanged("t"):
        del sys.modules[child]
        importlib.import_module(child)  # rebinds package.child to the copy
        sys.modules[fresh_package] = MagicMock()

    assert sys.modules[fresh_package] is package and sys.modules[child] is first
    assert package.child is first


@pytest.mark.parametrize("spec_the_parent", [False, True], ids=["mock_parent", "spec_carrying_mock_parent"])
def test_a_stub_under_a_stubbed_parent_is_reported(fresh_package: str, spec_the_parent: bool) -> None:
    child = f"{fresh_package}.child"
    # A MagicMock(spec=<package>) has a __spec__ and a __path__ that iterates empty: the child must still be found.
    parent = MagicMock(spec=importlib.import_module(fresh_package)) if spec_the_parent else MagicMock()

    with pytest.raises(ModulesChangedError) as caught, sys_modules_unchanged("t"):
        sys.modules[fresh_package] = parent
        sys.modules[child] = MagicMock()

    assert f"2 importable module(s): {fresh_package} (stub), {child} (stub)." in str(caught.value)
    assert child not in sys.modules
    assert (fresh_package in sys.modules) is spec_the_parent  # put back as it was: imported only for the spec


# --- wiring: the fixture and the collection hook in a real pytest run (a subprocess, outside tests/ so no conftest) ---

_PYTHON_ROOT = Path(__file__).resolve().parents[2]


def _run_pytest(tmp_path: Path, test_source: str) -> subprocess.CompletedProcess[str]:
    package = "f59b_wired"
    (tmp_path / package).mkdir()
    (tmp_path / package / "__init__.py").write_text("")
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_wired.py").write_text(textwrap.dedent(test_source))
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.support.sys_modules_guard", "-p", "no:cacheprovider",
         "-p", "no:randomly", "-q", "-rfE", str(tmp_path / "test_wired.py")],
        capture_output=True, text=True, cwd=tmp_path, timeout=25, check=False,
        env={**os.environ, "PYTHONPATH": str(_PYTHON_ROOT)},
    )


def test_a_test_that_leaves_a_stub_errors_at_its_own_teardown_and_the_next_test_sees_the_real_module(
    tmp_path: Path,
) -> None:
    run = _run_pytest(tmp_path, """
        import sys, types

        def test_a_leaves_a_stub():
            sys.modules["f59b_wired"] = types.ModuleType("f59b_wired")

        def test_b_fails_and_leaves_a_stub():
            sys.modules["f59b_wired"] = types.ModuleType("f59b_wired")
            assert False, "the body's own failure"

        def test_c_sees_the_real_module():
            import f59b_wired
            assert f59b_wired.__file__
    """)

    assert run.returncode == 1, run.stdout[-3000:]
    assert "ERROR test_wired.py::test_a_leaves_a_stub" in run.stdout
    assert "FAILED test_wired.py::test_b_fails_and_leaves_a_stub" in run.stdout
    assert "ERROR test_wired.py::test_b_fails_and_leaves_a_stub" in run.stdout
    assert "f59b_wired (stub)" in run.stdout
    assert "test_c_sees_the_real_module" not in run.stdout.split("short test summary")[-1]


def test_a_test_module_that_stubs_at_import_is_a_collection_error(tmp_path: Path) -> None:
    run = _run_pytest(tmp_path, """
        import sys, types

        sys.modules["f59b_wired"] = types.ModuleType("f59b_wired")

        def test_never_runs():
            pass
    """)

    assert run.returncode == 2, run.stdout[-3000:]
    assert "ERROR collecting test_wired.py" in run.stdout
    assert "importing test_wired.py left sys.modules changed for 1 importable module(s): f59b_wired (stub)" in run.stdout


def test_a_test_module_that_stubs_and_then_raises_at_import_keeps_its_traceback(tmp_path: Path) -> None:
    run = _run_pytest(tmp_path, """
        import sys, types

        sys.modules["f59b_wired"] = types.ModuleType("f59b_wired")
        raise RuntimeError("the import's own failure")
    """)

    assert run.returncode == 2, run.stdout[-3000:]
    assert "RuntimeError: the import's own failure" in run.stdout
    assert "importing test_wired.py left sys.modules changed for 1 importable module(s): f59b_wired (stub)" in run.stdout


def test_stubs_that_are_put_back_pass(tmp_path: Path) -> None:
    run = _run_pytest(tmp_path, """
        import sys, types
        import pytest

        @pytest.fixture
        def function_stub():
            sys.modules["f59b_wired"] = types.ModuleType("f59b_wired")
            yield
            del sys.modules["f59b_wired"]

        @pytest.fixture(scope="module")
        def module_stub():
            sys.modules["f59b_wired.child"] = types.ModuleType("f59b_wired.child")
            yield
            del sys.modules["f59b_wired.child"]

        def test_function_fixture(function_stub, module_stub):
            assert isinstance(sys.modules["f59b_wired"], types.ModuleType)

        def test_monkeypatch(monkeypatch, module_stub):
            monkeypatch.setitem(sys.modules, "f59b_wired", types.ModuleType("f59b_wired"))
    """)

    assert run.returncode == 0, run.stdout[-3000:]
    assert "2 passed" in run.stdout
