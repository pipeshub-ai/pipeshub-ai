"""Keep a test's ``sys.modules`` changes from leaking into later tests. Loaded as a pytest plugin by tests/conftest.py.

``sys.modules`` is the process-wide import cache. A module a test drops is imported again by the next test that needs
it: its body runs twice (metrics register twice: DuplicateTimeseries) and two copies of its classes and ContextVars
coexist. A stub a test leaves in place is what every later import gets. Either way a later, unrelated test breaks,
depending on which tests shared its worker.

Two checks:
- ``patch.dict(sys.modules)`` that drops modules first imported inside it fails at its own ``with`` line.
  ``_patch_dict._unpatch_dict`` is CPython's restore step; reading it at import raises AttributeError if a Python
  upgrade renames it. If a Python ever stopped calling it, tests/unit/test_sys_modules_guard.py goes red: don't skip it.
- Every test (its body and function-scoped fixtures) and every test module's import must leave each importable module
  as it found it: not dropped, not stubbed, not replaced by a second copy. Otherwise the entries are put back and the
  test errors at teardown, or the file errors at collection.
"""

import importlib.machinery
import os
import sys
import types
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from unittest import mock

import pytest

_restore = mock._patch_dict._unpatch_dict
_MISSING = object()


class ModulesDroppedError(AssertionError):
    pass


class ModulesChangedError(AssertionError):
    pass


def _restore_and_check(patcher: mock._patch_dict) -> None:
    if patcher.in_dict is not sys.modules:
        _restore(patcher)
        return
    present = set(sys.modules)
    _restore(patcher)
    dropped = sorted(present - sys.modules.keys() - patcher.values.keys())
    if dropped:
        raise ModulesDroppedError(
            f"patch.dict(sys.modules) dropped {len(dropped)} module(s) first imported inside it: {', '.join(dropped[:10])}."
            " Import them before patching, or patch the attribute the code reads (patch('pkg.mod.name'))."
        )


def install() -> None:
    mock._patch_dict._unpatch_dict = _restore_and_check


def _is_package_dir(path: str) -> bool:
    # A regular package, or a namespace package: a directory holding modules (source, compiled or extension) or
    # subpackages, not just any folder.
    suffixes = tuple(importlib.machinery.all_suffixes())
    try:
        return any(
            entry.name.endswith(suffixes) or (entry.is_dir() and not entry.name.startswith((".", "__")))
            for entry in os.scandir(path)
        )
    except OSError:
        return False


def _importable(name: str) -> bool:
    """A later ``import name`` would load a real file: a module or package on the import path.

    A plain filesystem walk, so nothing is imported. It goes through a live real parent's ``__path__`` when there is one,
    and past parents that are missing or stubs. ``PathFinder`` can't do this: for a namespace package whose parent isn't
    imported yet (app.api, app.api.routes) it raises KeyError. Built-in, frozen and zip-imported modules count as not
    importable, so a change to them is not reported.
    """
    dirs = list(sys.path)
    parts = name.split(".")
    for depth, part in enumerate(parts, 1):
        live = sys.modules.get(".".join(parts[:depth]))
        if (
            depth < len(parts)
            and issubclass(type(live), types.ModuleType)  # a MagicMock(spec=pkg) parent has a __path__ that iterates empty
            and getattr(live, "__spec__", None) is not None
            and getattr(live, "__path__", None) is not None
        ):
            try:
                dirs = list(live.__path__)
                continue
            except (KeyError, AttributeError):  # a namespace package's __path__ reads its parent's, which the test
                pass  # may have removed, or replaced with a module that has no __path__
        packages = [os.path.join(d, part) for d in dirs if _is_package_dir(os.path.join(d, part))]
        if depth == len(parts):
            return bool(packages) or any(
                os.path.isfile(os.path.join(d, part + suffix)) for d in dirs for suffix in importlib.machinery.all_suffixes()
            )
        if not packages:
            return False
        dirs = packages
    return False


def _hazard(name: str, old: object, new: object) -> str | None:
    if new is _MISSING:
        return "dropped"
    # type(), not isinstance(): a MagicMock(spec=module) passes isinstance and carries a child mock as __spec__.
    if not issubclass(type(new), types.ModuleType) or (
        getattr(new, "__spec__", None) is None and getattr(new, "__file__", None) is None
    ):
        return "stub"
    if old is not _MISSING and getattr(new, "__name__", None) == name:
        return "copy"
    return None  # a first import, or a real module stored under another name (sys.modules["cchardet"] = chardet)


def _put_back(name: str, old: object, new: object) -> None:
    """Put ``sys.modules[name]`` back, and the parent's attribute too when an import bound it to the rejected entry."""
    if old is _MISSING:
        del sys.modules[name]
    else:
        sys.modules[name] = old
    parent_name, _, attribute = name.rpartition(".")
    parent = sys.modules.get(parent_name)
    if new is _MISSING or parent is None or getattr(parent, attribute, None) is not new:
        return
    if old is _MISSING:
        delattr(parent, attribute)
    else:
        setattr(parent, attribute, old)


@contextmanager
def sys_modules_unchanged(where: str) -> Iterator[None]:
    before = dict(sys.modules)
    yield
    if sys.modules == before:
        return
    changed = {}
    for name in before.keys() | sys.modules.keys():
        old, new = before.get(name, _MISSING), sys.modules.get(name, _MISSING)
        if old is not new and (hazard := _hazard(name, old, new)) and _importable(name):
            changed[name] = hazard
    if not changed:
        return
    # Put them back first, so the next test sees the original modules. Parents go first: a child's repair needs its
    # original parent back in sys.modules.
    for name in sorted(changed, key=lambda name: name.count(".")):
        _put_back(name, before.get(name, _MISSING), sys.modules.get(name, _MISSING))
    listed = ", ".join(f"{name} ({hazard})" for name, hazard in sorted(changed.items())[:10])
    raise ModulesChangedError(
        f"{where} left sys.modules changed for {len(changed)} importable module(s): {listed}. Later imports in this process"
        " would get the stub, or run the module again. Use monkeypatch.setitem/delitem(sys.modules, ...) or"
        " patch.dict(sys.modules, ...), or patch the attribute the code reads; don't stub an installed package."
    )


@pytest.fixture(autouse=True)
def _sys_modules_unchanged(request: pytest.FixtureRequest) -> Iterator[None]:
    # A plugin fixture sorts before conftest's autouse fixtures, so their monkeypatch is undone before this check.
    with sys_modules_unchanged(request.node.nodeid):
        yield


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector: pytest.Collector) -> Generator[None, pytest.CollectReport, pytest.CollectReport]:
    if not isinstance(collector, pytest.Module):
        return (yield)
    try:
        with sys_modules_unchanged(f"importing {collector.nodeid}"):
            report = yield
    except ModulesChangedError as error:  # raising from a hook wrapper is an INTERNALERROR; fail the file instead
        # An import that also raised keeps its traceback, so the developer still sees why the import stopped.
        longrepr = f"{report.longrepr}\n\n{error}" if report.failed else str(error)
        report.outcome, report.longrepr, report.result = "failed", longrepr, []
    return report


install()  # at plugin import, before any test module is collected
