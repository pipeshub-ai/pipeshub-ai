"""A package's re-exported names, imported on first use (PEP 562).

A package ``__init__`` that imports every submodule to re-export their names makes importing any one submodule load
them all: the sandbox broker, which needs a constant from ``harness_core.gateway`` and the worker credential from
``app.harness.journal_node``, held the whole tool gateway and the journal client (FastAPI included) at idle
(R25OPS-FU-1). A package keeps its names, ``from package import Name`` still works, and each submodule is imported
when one of its names is first read.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


def lazy_exports(package: str, exports: Mapping[str, str]) -> tuple[Callable[[str], object], Callable[[], list[str]]]:
    """``__getattr__`` and ``__dir__`` for ``package``: each name in ``exports`` is read from the submodule it maps to
    (relative to ``package``) the first time it is asked for, then kept in the package's namespace."""

    def __getattr__(name: str) -> object:
        submodule = exports.get(name)
        if submodule is None:
            raise AttributeError(f"module {package!r} has no attribute {name!r}")
        value = getattr(importlib.import_module(f"{package}.{submodule}"), name)
        setattr(importlib.import_module(package), name, value)
        return value

    def __dir__() -> list[str]:
        return sorted({*vars(importlib.import_module(package)), *exports})

    return __getattr__, __dir__


__all__ = ["lazy_exports"]
