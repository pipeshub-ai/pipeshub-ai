"""Resolve import specifiers to real files inside the repo.

Runs before every other resolution step: its output *is* the import-evidence
table that decides whether a call edge is trustworthy.

Only intra-repo targets resolve. A specifier that leaves the corpus (``react``,
``os``) has no file to point at, and is reported so the caller can materialise a
shared external node instead.
"""
from __future__ import annotations

import posixpath

__all__ = ["ImportResolution", "resolve_import_specifier"]

_JS_EXTENSIONS = (".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
_JS_INDEX_FILES = tuple(f"index{ext}" for ext in _JS_EXTENSIONS)
_PY_EXTENSIONS = (".py", ".pyi")

# Bounded so a cycle of barrel files cannot spin forever.
_MAX_BARREL_HOPS = 8


class ImportResolution:
    """Resolves specifiers against the set of file paths present in the repo."""

    def __init__(self, file_paths: set[str]) -> None:
        self._files = file_paths
        self._by_suffix: dict[str, list[str]] = {}
        for path in file_paths:
            base = path.rsplit("/", 1)[-1]
            self._by_suffix.setdefault(base, []).append(path)

    # -- python ---------------------------------------------------------

    def _resolve_python(self, specifier: str, from_file: str) -> str | None:
        if specifier.startswith("."):
            depth = len(specifier) - len(specifier.lstrip("."))
            remainder = specifier[depth:]
            base = posixpath.dirname(from_file)
            for _ in range(depth - 1):
                base = posixpath.dirname(base)
            rel = remainder.replace(".", "/")
            candidate = posixpath.normpath(posixpath.join(base, rel)) if rel else base
            return self._python_candidates(candidate)

        rel = specifier.replace(".", "/")
        direct = self._python_candidates(rel)
        if direct:
            return direct
        # Absolute imports are usually rooted at a package dir (src/, app/…),
        # so match on the tail of known paths.
        for suffix in (f"/{rel}.py", f"/{rel}/__init__.py"):
            matches = [p for p in self._files if p.endswith(suffix)]
            if len(matches) == 1:
                return matches[0]
        return None

    def _python_candidates(self, base: str) -> str | None:
        for ext in _PY_EXTENSIONS:
            candidate = f"{base}{ext}"
            if candidate in self._files:
                return candidate
        init = f"{base}/__init__.py"
        return init if init in self._files else None

    # -- javascript / typescript ----------------------------------------

    def _resolve_js(self, specifier: str, from_file: str) -> str | None:
        if not specifier.startswith("."):
            return None  # bare specifier: a package, not a repo file
        base = posixpath.normpath(posixpath.join(posixpath.dirname(from_file), specifier))
        if base in self._files:
            return base
        for ext in _JS_EXTENSIONS:
            candidate = f"{base}{ext}"
            if candidate in self._files:
                return candidate
        for index_file in _JS_INDEX_FILES:
            candidate = f"{base}/{index_file}"
            if candidate in self._files:
                return candidate
        return None

    # -- public ---------------------------------------------------------

    def resolve(self, specifier: str, from_file: str, family: str | None) -> str | None:
        if not specifier or not from_file:
            return None
        try:
            if family == "py":
                return self._resolve_python(specifier, from_file)
            return self._resolve_js(specifier, from_file)
        except (ValueError, IndexError):
            return None

    def walk_barrel_chain(self, target_file: str,
                          re_exports: dict[str, list[str]]) -> str:
        """Follow `index.ts` re-export hops to the defining module.

        `importer -> barrel -> real_module`: importing from a barrel should
        attribute the dependency to the module that actually defines the symbol.
        Bounded and cycle-safe.
        """
        seen = {target_file}
        current = target_file
        for _ in range(_MAX_BARREL_HOPS):
            onward = re_exports.get(current) or []
            if len(onward) != 1:
                return current
            nxt = onward[0]
            if nxt in seen:
                return current
            seen.add(nxt)
            current = nxt
        return current


def resolve_import_specifier(specifier: str, from_file: str, family: str | None,
                             file_paths: set[str]) -> str | None:
    return ImportResolution(file_paths).resolve(specifier, from_file, family)
