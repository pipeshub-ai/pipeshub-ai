"""Rows that a subpackage of ``helper`` adds to a shared route table.

A subpackage holding a module named like the table (``helper/<pkg>/admin_route_table.py``)
extends it. Only packages that have that module are imported.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

_HELPER = Path(__file__).resolve().parent


def table_extensions(table: str) -> list[ModuleType]:
    """The ``helper.<pkg>.<table>`` modules that exist, in package-name order."""
    return [
        importlib.import_module(f"helper.{pkg.name}.{table}")
        for pkg in sorted(_HELPER.iterdir())
        if (pkg / "__init__.py").is_file() and (pkg / f"{table}.py").is_file()
    ]
