"""Rows a downstream build of the product adds to the suite's tables.

A build that serves more admin routes, or writes more edge fields, than this
repository has needs rows that cannot live here. It puts them in a
``suite_extensions`` directory one level below ``integration-tests``
(``integration-tests/<name>/suite_extensions/``), and each table merges what it
finds there:

* ``admin_route_table.py`` may define ``NODE_ADMIN_ROUTES``,
  ``PYTHON_ADMIN_ROUTES``, ``ADMIN_BODIES``, ``PYTHON_CONDITIONAL_ADMIN`` and
  ``SWITCHED_OFF``;
* ``admin_route_conditional_table.py`` may define ``CONDITIONAL_ROUTES`` and
  ``WORLD_GROUPS``;
* ``admin_route_conditional_world.py`` may define ``BUILDERS``: a group name of
  its ``WORLD_GROUPS`` -> a function that takes the ``World`` and makes it;
* ``schemas/edges/<file>.yaml`` adds fields to the edge schema of that name
  (``validation/graph_edge_validator.py``).

An extension only adds. A row, key or group that is already there is an error,
not an override, so an extension cannot drop or loosen a check made here.
Without an extension directory, every table is used as it is written.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable, Hashable, Iterable, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, TypeVar

ROOT = Path(__file__).resolve().parents[1]
DIR_NAME = "suite_extensions"

T = TypeVar("T")


def extension_dirs(root: Path = ROOT) -> list[Path]:
    return sorted(path for path in root.glob(f"*/{DIR_NAME}") if path.is_dir())


def modules(name: str, root: Path = ROOT) -> list[ModuleType]:
    """The extension modules named ``<name>.py``, imported once each."""
    found: list[ModuleType] = []
    for directory in extension_dirs(root):
        path = directory / f"{name}.py"
        if not path.is_file():
            continue
        module_name = f"{DIR_NAME}.{directory.parent.name}.{name}"
        module = sys.modules.get(module_name)
        if module is None or getattr(module, "__file__", None) != str(path):
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot import {path}")
            module = importlib.util.module_from_spec(spec)
            # Registered first, so dataclasses and imports inside it resolve.
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        found.append(module)
    return found


def add_rows(
    base: Iterable[T], name: str, extensions: Iterable[ModuleType], key: Callable[[T], Hashable],
) -> tuple[T, ...]:
    """``base`` followed by every extension's ``name`` rows; a key seen twice is an error."""
    rows = list(base)
    seen = {key(row) for row in rows}
    for module in extensions:
        for row in getattr(module, name, ()):
            row_key = key(row)
            if row_key in seen:
                raise ValueError(f"{module.__file__} adds a {name} row for {row_key!r}, which is already there")
            seen.add(row_key)
            rows.append(row)
    return tuple(rows)


def add_items(
    base: Mapping[Hashable, Any], name: str, extensions: Iterable[ModuleType],
) -> dict[Hashable, Any]:
    """``base`` with every extension's ``name`` entries added; a key seen twice is an error."""
    merged = dict(base)
    for module in extensions:
        for item_key, value in dict(getattr(module, name, {})).items():
            if item_key in merged:
                raise ValueError(f"{module.__file__} adds {name}[{item_key!r}], which is already there")
            merged[item_key] = value
    return merged
