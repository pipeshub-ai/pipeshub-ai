"""Python: whether an import the resolver cannot place still names repo code.

`ImportResolution` places Python imports itself; this answers only for the ones it cannot: a relative
import, or a dotted path whose first segment is one of the repo's top-level names: a folder or module at
the root or under `src/`, or an outermost package (the highest folder of an `__init__.py` chain, so
`app` of `backend/python/app/`). Such an import is unresolved, never an outside library, so no external
ref is written for it (`app.api.routes:toolsets._keep_stored_secrets`). A module deeper down
(`telemetry/logging.py`) is not a top-level name, so `import logging` stays the standard library's.
"""
from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.code_graph.symbol_index import SymbolIndex

__all__ = ["in_repo"]


def in_repo(specifier: str, from_file: str, files: set[str], index: SymbolIndex | None) -> bool:
    head = specifier.split(".", 1)[0]
    return specifier.startswith(".") or (bool(head) and head in _top_level(files, index))


def _top_level(files: set[str], index: SymbolIndex | None) -> frozenset[str]:
    # shortcut: keyed by the set's identity and size, which a build never mutates; key by content if one does.
    cache = index.__dict__.setdefault("_py_top_level", {}) if index is not None else {}
    key = (id(files), len(files))
    if key not in cache:
        names = set()
        for path in files:
            # Only Python makes a top-level name: `docker/run.sh` or `redis/healthcheck.ts` must not hide
            # `import docker` / `import redis`.
            if not path.endswith((".py", ".pyi")):
                continue
            parts = path.split("/")
            parts = parts[1:] if parts[0] == "src" and len(parts) > 1 else parts
            if len(parts) > 1:
                names.add(parts[0])
            else:
                names.add(path.rsplit(".", 1)[0])
            if path.endswith("/__init__.py"):
                package = posixpath.dirname(path)
                while f"{posixpath.dirname(package)}/__init__.py" in files:
                    package = posixpath.dirname(package)
                if not _inside_a_package(package, files):
                    names.add(posixpath.basename(package))
        cache[key] = frozenset(names)
    return cache[key]


def _inside_a_package(folder: str, files: set[str]) -> bool:
    """Whether a folder above *folder* is a package: `app/modules/parsers/json/` under `app/modules/` with no
    `parsers/__init__.py` between is a namespace gap inside the repo's own package, never a root, so `json` stays
    the standard library's; `backend/python/app/` has no package above it, so `app` is one."""
    parent = posixpath.dirname(folder)
    while parent:
        if f"{parent}/__init__.py" in files:
            return True
        parent = posixpath.dirname(parent)
    return False
