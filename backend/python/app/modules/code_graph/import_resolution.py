"""Resolve import specifiers to real files inside the repo.

Runs before every other resolution step: its output *is* the import-evidence
table that decides whether a call edge is trustworthy.

Only intra-repo targets resolve. A specifier that leaves the corpus (``react``,
``os``) has no file to point at, and is reported so the caller can materialise a
shared external node instead.
"""
from __future__ import annotations

import posixpath
import sys
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["ImportResolution", "resolve_import_specifier"]

_JS_EXTENSIONS = (".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
_JS_INDEX_FILES = tuple(f"index{ext}" for ext in _JS_EXTENSIONS)
_PY_EXTENSIONS = (".py", ".pyi")
# TypeScript ESM (node16/nodenext/bundler) imports a source by its emitted name:
# `./foo.js` while the repo holds `foo.ts`.
_TS_SOURCE_FOR_EMITTED = {
    ".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",),
}

# `import logging` is the standard library even when the repo has a
# `telemetry/logging.py`; only a module at a source root could shadow it.
_STDLIB_MODULES = frozenset(getattr(sys, "stdlib_module_names", ()))

# tsconfig/vite path aliases for the source root. A scoped package is
# `@scope/name`, so `@/` and `~/` cannot be one.
_JS_ROOT_ALIASES = ("@/", "~/", "#/")

# Folders a JS/TS monorepo keeps its workspace packages in. `@scope/element`
# is resolved to `<root>/packages/element` only when exactly one such folder
# holds a source entry, so a real npm package never lands on a repo folder.
_JS_WORKSPACE_DIRS = frozenset({"packages", "apps", "libs", "modules"})
_JVM_SOURCE_EXTENSIONS = (".java", ".kt", ".scala", ".groovy")

# Bounded so a cycle of barrel files cannot spin forever.
_MAX_BARREL_HOPS = 8


class ImportResolution:
    """Resolves specifiers against the set of file paths present in the repo."""

    def __init__(self, file_paths: set[str]) -> None:
        self._files = file_paths
        # Every trailing run of a Python module's path segments -> its files, so
        # an absolute import looks itself up instead of scanning all files with
        # the same basename (every `__init__.py`, per unresolved dotted import,
        # was the quadratic part of a build).
        self._modules_by_suffix: dict[str, list[str]] = {}
        self._packages_by_suffix: dict[str, list[str]] = {}
        # JVM: `com/acme/Owner` (and every shorter tail) -> source files.
        self._jvm_by_suffix: dict[str, list[str]] = {}
        # JS workspace package name (its folder's name) -> package root folders.
        self._js_workspaces: dict[str, list[str]] = {}
        # Go: package folder -> its non-test files; folders holding a go.mod; and,
        # once learnt from the repo's own imports, each module root's import path.
        self._go_packages: dict[str, list[str]] = {}
        self._go_roots: list[str] = []
        self._go_modules: dict[str, str] = {}
        for path in file_paths:
            if path.endswith(".go"):
                if not path.endswith("_test.go"):
                    self._go_packages.setdefault(posixpath.dirname(path), []).append(path)
                continue
            if path == "go.mod" or path.endswith("/go.mod"):
                self._go_roots.append(posixpath.dirname(path))
                continue
            if path.endswith(_JVM_SOURCE_EXTENSIONS):
                parts = path.rsplit(".", 1)[0].split("/")
                for i in range(len(parts)):
                    self._jvm_by_suffix.setdefault("/".join(parts[i:]), []).append(path)
                continue
            segments = path.split("/")
            for i in range(len(segments) - 2):
                if segments[i] in _JS_WORKSPACE_DIRS:
                    root = "/".join(segments[: i + 2])
                    roots = self._js_workspaces.setdefault(segments[i + 1], [])
                    if root not in roots:
                        roots.append(root)
                    break
            if path == "__init__.py" or path.endswith("/__init__.py"):
                module, table = path[: -len("__init__.py")].rstrip("/"), self._packages_by_suffix
            elif path.endswith(".py"):
                module, table = path[:-3], self._modules_by_suffix
            else:
                continue
            parts = module.split("/") if module else []
            for i in range(1, len(parts)):
                table.setdefault("/".join(parts[i:]), []).append(path)

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
        if rel.split("/", 1)[0] in _STDLIB_MODULES:
            return None
        # Absolute imports are rooted at a source dir (src/, backend/…), so match
        # on the tail of known paths -- but only where the first segment is a
        # top-level package. `telemetry/logging.py` inside the `app` package is
        # `app.telemetry.logging`, never `logging`.
        for table, suffix in ((self._modules_by_suffix, f"/{rel}.py"),
                              (self._packages_by_suffix, f"/{rel}/__init__.py")):
            matches = [
                p for p in table.get(rel, ())
                if self._is_source_root(p[: -len(suffix)])
            ]
            if len(matches) == 1:
                return matches[0]
        return None

    def _is_source_root(self, directory: str) -> bool:
        return f"{directory}/__init__.py" not in self._files

    def _python_candidates(self, base: str) -> str | None:
        for ext in _PY_EXTENSIONS:
            candidate = f"{base}{ext}"
            if candidate in self._files:
                return candidate
        init = f"{base}/__init__.py"
        return init if init in self._files else None

    # -- javascript / typescript ----------------------------------------

    def _resolve_js(self, specifier: str, from_file: str) -> str | None:
        if specifier.startswith(_JS_ROOT_ALIASES):
            return self._resolve_js_alias(specifier[2:], from_file)
        if not specifier.startswith("."):
            # A bare specifier is a package; in a monorepo it can be one of the repo's own.
            return self._resolve_js_workspace(specifier) if specifier.startswith("@") else None
        base = posixpath.normpath(posixpath.join(posixpath.dirname(from_file), specifier))
        return self._js_file(base)

    def _resolve_js_workspace(self, specifier: str) -> str | None:
        """`@excalidraw/element[/sub]` -> that workspace package's entry or sub-path.

        tsconfig `paths` and package.json names are not read here (the build has
        file paths only), so the package is matched by folder name, and only when
        one workspace folder of that name holds the target.
        """
        parts = specifier.split("/")
        if len(parts) < 2:
            return None
        rest = "/".join(parts[2:])
        hits = set()
        for root in self._js_workspaces.get(parts[1], ()):
            for base in ((f"{root}/src/{rest}", f"{root}/{rest}") if rest
                         else (f"{root}/src", root)):
                hit = self._js_file(base)
                if hit:
                    hits.add(hit)
                    break
        return hits.pop() if len(hits) == 1 else None

    # -- jvm ------------------------------------------------------------

    def _resolve_jvm(self, specifier: str, from_file: str) -> str | None:
        """`com.acme.Owner` -> the source file whose path ends in com/acme/Owner.

        When copies of the package sit in several source trees (guava/ and
        android/guava/), the one sharing the importer's tree wins, as in a build.
        """
        matches = self._jvm_by_suffix.get(specifier.replace(".", "/"), ())
        if len(matches) <= 1:
            return matches[0] if matches else None
        shared = {m: len(posixpath.commonpath([m, from_file])) for m in matches}
        best = max(shared.values())
        closest = [m for m, n in shared.items() if n == best]
        return closest[0] if len(closest) == 1 else None

    def learn_go_modules(self, specifiers: Iterable[str]) -> None:
        """Each go.mod folder's module path, by majority over the repo's imports.

        The build sees file paths, not go.mod's text. A module path is the prefix
        that, cut from the repo's own imports, leaves a folder under that go.mod;
        an external import whose tail happens to name a repo folder
        (`k8s.io/api/core/v1` beside `kubernetes/typed/core/v1`) is outvoted.
        """
        votes: Counter[tuple[str, str]] = Counter()
        for specifier in set(specifiers):
            parts = specifier.split("/")
            # shortcut: the standard library is told apart by a dotless first
            # element; a dotless module path (`module myapp`) is not resolved.
            if "." not in parts[0]:
                continue
            for root in self._go_roots:
                for i in range(1, len(parts) + 1):
                    folder = posixpath.join(root, *parts[i:]) if i < len(parts) else root
                    if folder in self._go_packages:
                        votes[(root, "/".join(parts[:i]))] += 1
        best: dict[str, tuple[int, list[str]]] = {}
        for (root, module), n in votes.items():
            top, names = best.get(root, (0, []))
            if n > top:
                best[root] = (n, [module])
            elif n == top:
                names.append(module)
        self._go_modules = {root: names[0] for root, (_, names) in best.items() if len(names) == 1}

    def _resolve_go(self, specifier: str) -> str | None:
        """`k8s.io/client-go/tools/cache` -> a file of the repo's tools/cache package."""
        for root, module in self._go_modules.items():
            if specifier == module:
                folder = root
            elif specifier.startswith(module + "/"):
                folder = posixpath.join(root, specifier[len(module) + 1:])
            else:
                continue
            files = self._go_packages.get(folder)
            if files:
                return min(files)
        return None

    def go_package_files(self, file_path: str) -> list[str]:
        """The non-test files of the Go package *file_path* belongs to."""
        return self._go_packages.get(posixpath.dirname(file_path), [])

    def _resolve_js_alias(self, rest: str, from_file: str) -> str | None:
        """`@/lib/http` against the importing file's ancestors, then their `src/`.

        tsconfig is not read, so the alias root is taken to be the nearest
        ancestor (or its `src/`) that holds the target.
        """
        directory = posixpath.dirname(from_file)
        while True:
            for root in (directory, posixpath.join(directory, "src") if directory else "src"):
                hit = self._js_file(posixpath.normpath(posixpath.join(root, rest)) if root else rest)
                if hit:
                    return hit
            if not directory:
                return None
            directory = posixpath.dirname(directory)

    def _js_file(self, base: str) -> str | None:
        if base in self._files:
            return base
        stem, emitted_ext = posixpath.splitext(base)
        for ext in _TS_SOURCE_FOR_EMITTED.get(emitted_ext, ()):
            if f"{stem}{ext}" in self._files:
                return f"{stem}{ext}"
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
            if family in ("js", None):
                return self._resolve_js(specifier, from_file)
            if family == "jvm":
                return self._resolve_jvm(specifier, from_file)
            if family == "go":
                return self._resolve_go(specifier)
            # No resolver for this language's imports yet; JS rules would only
            # misread its specifiers.
            return None
        except (ValueError, IndexError):
            return None

    def walk_barrel_chain(self, target_file: str,
                          re_exports: dict[str, set[str]]) -> str:
        """Follow `index.ts` re-export hops to the defining module.

        `importer -> barrel -> real_module`: importing from a barrel should
        attribute the dependency to the module that actually defines the symbol.
        Bounded and cycle-safe.
        """
        seen = {target_file}
        current = target_file
        for _ in range(_MAX_BARREL_HOPS):
            onward = re_exports.get(current) or ()
            if len(onward) != 1:
                return current
            nxt = next(iter(onward))
            if nxt in seen:
                return current
            seen.add(nxt)
            current = nxt
        return current


def resolve_import_specifier(specifier: str, from_file: str, family: str | None,
                             file_paths: set[str]) -> str | None:
    return ImportResolution(file_paths).resolve(specifier, from_file, family)
