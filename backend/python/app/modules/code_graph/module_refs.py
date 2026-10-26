"""Module references in a file's name list, so a build finds a module's importers.

An incremental build re-resolves the files that mention a name the changed
files define. A module is referred to by its path, not a name it defines, so a
file importing `pkg.m28` was never re-resolved when `pkg/m28.py` appeared, and
its import stayed unlinked until a full rebuild (R-3).

Importers record ``module:<path>`` (the specifier resolved against the
importing file where it is relative); a changed file matches every importer
whose path is a suffix of its own, which covers absolute imports from any
source root (`app.cart.service` and `backend/app/cart/service.py`) and path
aliases (`@/lib/x`). Pure functions.
"""
from __future__ import annotations

import posixpath

__all__ = ["MODULE_PREFIX", "module_ref", "module_refs_of_path"]

MODULE_PREFIX = "module:"

_CODE_EXTENSIONS = frozenset({"py", "pyi", "js", "jsx", "mjs", "cjs", "ts", "tsx", "mts", "cts"})
# A file named like this is imported by its directory (`./utils` is
# utils/index.ts, `pkg` is pkg/__init__.py).
_DIRECTORY_MODULE_STEMS = frozenset({"index", "__init__", "mod"})
_ALIAS_PREFIXES = ("@/", "~/", "#/")


def _strip_extension(path: str) -> str:
    head, _, last = path.rpartition("/")
    stem, dot, ext = last.rpartition(".")
    if dot and stem and ext in _CODE_EXTENSIONS:
        last = stem
    return f"{head}/{last}" if head else last


def module_ref(specifier: str, file_path: str | None = None) -> str | None:
    """``module:<path>`` for an import specifier, or None when it names nothing."""
    spec = specifier.replace("\\", "/").strip()
    if not spec:
        return None
    base = posixpath.dirname(file_path.replace("\\", "/")) if file_path else None
    if spec.startswith(("./", "../")):
        if base is None:
            path = spec.lstrip("./")
        else:
            path = posixpath.normpath(posixpath.join(base, spec))
    elif spec.startswith("."):
        dots = len(spec) - len(spec.lstrip("."))
        rest = spec[dots:].replace(".", "/")
        if base is None:
            path = rest
        else:
            for _ in range(dots - 1):
                base = posixpath.dirname(base)
            path = posixpath.join(base, rest) if rest else base
    elif "/" in spec:
        path = spec
        for prefix in _ALIAS_PREFIXES:
            if path.startswith(prefix):
                path = path[len(prefix):]
                break
    else:
        path = spec.replace(".", "/")
    path = _strip_extension(posixpath.normpath(path).lstrip("/"))
    if not path or path in (".", "..") or path.startswith("../"):
        return None
    return MODULE_PREFIX + path


def module_refs_of_path(file_path: str) -> set[str]:
    """Every ``module:`` reference that can mean this file: each suffix of its path."""
    parts = _strip_extension(file_path.replace("\\", "/").lstrip("/")).split("/")
    paths = [parts]
    if parts[-1] in _DIRECTORY_MODULE_STEMS and len(parts) > 1:
        paths.append(parts[:-1])
    return {
        MODULE_PREFIX + "/".join(p[i:])
        for p in paths for i in range(len(p)) if p[i:]
    }
