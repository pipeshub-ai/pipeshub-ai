"""Renders a large grant's toolset modules of ``pipeshub_tools`` on first import (C5.01).

Seeded as ``pipeshub_tools/_lazy.py`` when a run has more tools than the worker renders at a turn's start. The
package's ``__init__`` installs one finder for its own name; ``import pipeshub_tools.<toolset>`` (or the attribute)
reads the toolset's pinned schema files from the turn's tools folder (``_lazy.json`` names them) and runs the module
``_render`` writes from them, the worker's own renderer, so the module is the one a small grant would hold. Nothing
is written: the session folder is read-only, and the source lives in memory (``linecache`` for tracebacks).

Standard library only: it runs on the engine image's own CPython.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import json
import linecache
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast, override

if TYPE_CHECKING:
    from collections.abc import Sequence
    from importlib.machinery import ModuleSpec
    from types import ModuleType

INDEX_FILE = "_lazy.json"


class Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Finds and renders the toolset modules ``_lazy.json`` lists, for one package folder."""

    def __init__(self, package: str, folder: Path) -> None:
        self.package = package
        self.folder = folder
        index = cast("dict[str, Any]", json.loads((folder / INDEX_FILE).read_bytes()))
        self.modules = cast("dict[str, dict[str, Any]]", index["modules"])
        # <session>/sdk-<digest>/python/pipeshub_tools: the turn's tools folder is a sibling of sdk-<digest>.
        self.tools = (folder.parents[2] / str(index["tools"])).resolve()

    @override
    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        del path, target
        package, _, name = fullname.rpartition(".")
        if package != self.package or name not in self.modules:
            return None
        return importlib.util.spec_from_loader(fullname, self, origin=f"{self.folder / name}.py (rendered on import)")

    @override
    def create_module(self, spec: ModuleSpec) -> ModuleType | None:
        del spec
        return None

    @override
    def exec_module(self, module: ModuleType) -> None:
        name = module.__name__.rpartition(".")[2]
        entry = self.modules[name]
        render = importlib.import_module(f"{self.package}._render")
        tools = [(str(t[0]), str(t[1]), str(t[2])) for t in entry["tools"]]
        source = render.module_from_schemas(str(entry["toolset"]), tools, self._read)
        filename = f"<{module.__name__}>"
        linecache.cache[filename] = (len(source), None, source.splitlines(True), filename)
        exec(compile(source, filename, "exec"), module.__dict__)

    def _read(self, path: str) -> bytes:
        target = (self.tools / path).resolve()
        if not target.is_relative_to(self.tools):
            raise ImportError(f"{path} is outside this turn's tools folder")
        return target.read_bytes()


def install(package: str, init_file: str) -> Finder:
    """The package's finder, put first on ``sys.meta_path`` once (a package seeded again replaces it)."""
    folder = Path(init_file).resolve().parent
    for finder in list(sys.meta_path):
        if isinstance(finder, Finder) and finder.package == package:
            if finder.folder == folder:
                return finder
            sys.meta_path.remove(finder)
    finder = Finder(package, folder)
    sys.meta_path.insert(0, finder)
    return finder


def load(package: str, name: str) -> ModuleType:
    """``package.<name>``, rendered on first use."""
    return importlib.import_module(f"{package}.{name}")


__all__ = ["INDEX_FILE", "Finder", "install", "load"]
