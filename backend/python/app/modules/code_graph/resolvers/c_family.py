"""C and C++ includes: the repo file an `#include` names, and what a file sees through its includes.

The product has no compile flags, so an include is looked for the way a
compiler with the project's usual `-I` paths would find it: beside the including
file first, then any repo file whose path ends with the specifier, the nearest
one winning (most leading folders shared with the includer; a tie names none).

A file sees what its includes include, transitively (`include_scope`), as the
compiler does. A header's bodies live in its same-stem source (`x.h` -> `x.c`,
`x.cpp`, same folder), which no include names: `paired_sources` is that, as
INFERRED evidence.
"""
from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING

from app.modules.parsers.code_parser.languages.c_family import in_std

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.modules.code_graph.symbol_index import SymbolIndex

__all__ = ["in_repo", "include_scope", "owner_type", "paired_sources", "qualified_callees", "resolve", "same_unit",
           "scoped_types"]

_IMPORTS_FROM = "IMPORTS_FROM"
_HEADERS = (".h", ".hh", ".hpp", ".hxx")
_SOURCES = (".c", ".cc", ".cpp", ".cxx")


def _common_dirs(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a.split("/")[:-1], b.split("/")[:-1], strict=False):
        if x != y:
            break
        n += 1
    return n


def _by_basename(files: set[str], index: SymbolIndex | None) -> dict[str, list[str]]:
    # shortcut: keyed by the set's identity and size, which a build never mutates; key by content if one does.
    cache = index.__dict__.setdefault("_c_basenames", {}) if index is not None else {}
    key = (id(files), len(files))
    if key not in cache:
        names: dict[str, list[str]] = {}
        for path in files:
            names.setdefault(path.rsplit("/", 1)[-1], []).append(path)
        cache[key] = names
    return cache[key]


def resolve(specifier: str, from_file: str, files: set[str], index: SymbolIndex | None) -> str | None:
    """The repo file `#include "<specifier>"` (or `<specifier>`, kept in its brackets) in *from_file* names,
    or None (a system header). An angle include is a repo file only when its path places it
    (`<oatpp/core/Types.hpp>`), never beside the includer and never by a bare name (`<config.h>`); a quoted
    one is looked for beside the includer first, then by its path or name."""
    angle, spec = _form(specifier)
    if not spec:
        return None
    beside = posixpath.normpath(posixpath.join(posixpath.dirname(from_file), spec))
    if not angle and beside in files:
        return beside
    tail = posixpath.normpath(spec).lstrip("/")
    if tail.startswith("../") or (angle and "/" not in tail):
        return None  # a bare `<config.h>` is a system or vendored header, whatever the repo holds
    hits = [path for path in _by_basename(files, index).get(tail.rsplit("/", 1)[-1], ())
            if path == tail or path.endswith("/" + tail)]
    if len(hits) <= 1:
        return hits[0] if hits else None
    best = max(_common_dirs(path, from_file) for path in hits)
    nearest = [path for path in hits if _common_dirs(path, from_file) == best]
    return nearest[0] if len(nearest) == 1 else None


def in_repo(specifier: str, from_file: str, files: set[str], index: SymbolIndex | None) -> bool:
    """Whether a quoted include `resolve` cannot place still names a repo header (a tie between two, or a
    path the product's missing -I flags hide), so it is unresolved rather than an outside library. An angle
    include `resolve` cannot place by its path stays outside: a repo `config.h` must not capture a system
    or vendored `<config.h>`."""
    angle, spec = _form(specifier)
    tail = posixpath.normpath(spec).lstrip("/") if spec else ""
    return not angle and bool(tail) and bool(_by_basename(files, index).get(tail.rsplit("/", 1)[-1]))


def _form(specifier: str) -> tuple[bool, str]:
    """(an angle include, the path it names): `<a/x.h>` -> (True, "a/x.h"), `a/x.h` -> (False, "a/x.h")."""
    spec = specifier.replace("\\", "/").strip()
    if spec.startswith("<") and spec.endswith(">"):
        return True, spec[1:-1].strip()
    return False, spec


def _includes(index: SymbolIndex, file_path: str) -> list[str]:
    """The repo files *file_path* includes directly."""
    cache = index.__dict__.setdefault("_c_includes", {})
    if file_path not in cache:
        if "_c_files" not in index.__dict__:
            index.__dict__["_c_files"] = set(index.record_by_file)
        files = index.__dict__["_c_files"]
        record = index.record_by_file.get(file_path)
        found: dict[str, None] = {}
        for block_id in index.blocks_by_record.get(record, ()) if record else ():
            for fact in index.rows[block_id].pending_edges:
                if fact.get("relation") == _IMPORTS_FROM and fact.get("toName"):
                    target = resolve(fact["toName"], file_path, files, index)
                    if target and target != file_path:
                        found[target] = None
        cache[file_path] = list(found)
    return cache[file_path]


def include_scope(index: SymbolIndex, file_path: str) -> list[str]:
    """Every repo file *file_path* includes, directly or through another include, sorted."""
    cache = index.__dict__.setdefault("_c_scope", {})
    # shortcut: a file's whole include closure, unbounded; cap its size if a repo's headers make it huge.
    if file_path not in cache:
        seen: set[str] = set()
        todo = list(_includes(index, file_path))
        while todo:
            path = todo.pop()
            if path in seen or path == file_path:
                continue
            seen.add(path)
            todo.extend(_includes(index, path))
        cache[file_path] = sorted(seen)
    return cache[file_path]


def _units_of(index: SymbolIndex) -> dict[str, set[str]]:
    """source -> the translation units holding it, each named by its root: a source no repo file includes, with
    the sources it includes (so a unity build's `#include "a.c"` puts a.c in the includer's unit)."""
    cache = index.__dict__
    if "_c_units" not in cache:
        c_files = [p for p in index.record_by_file if p.endswith((*_SOURCES, *_HEADERS))]
        included = {target for p in c_files for target in _includes(index, p)}
        units: dict[str, set[str]] = {}
        for root in (p for p in c_files if p.endswith(_SOURCES) and p not in included):
            for path in (root, *include_scope(index, root)):
                if path.endswith(_SOURCES):
                    units.setdefault(path, set()).add(root)
        cache["_c_units"] = units
    return cache["_c_units"]


def _scope_set(index: SymbolIndex, file_path: str) -> set[str]:
    """include_scope as a set, cached: membership is tested per candidate."""
    cache = index.__dict__.setdefault("_c_scope_sets", {})
    if file_path not in cache:
        cache[file_path] = set(include_scope(index, file_path))
    return cache[file_path]


def same_unit(index: SymbolIndex, call_file: str, def_file: str) -> bool | None:
    """Whether *def_file*'s `static` functions are in *call_file*'s translation unit: its own file, a header, or
    a source some unit holds with the calling source (a unity build's sibling `.c`, or the `.c` that includes
    the caller). None (unknown) for a header that source includes: the header's code may mean the static there
    or an extern of the same name elsewhere, depending on what the includer defines above the include."""
    # shortcut: units are approximated from the include graph (no compile_commands), and a header's statics count
    # as in every unit, since which units include a header (beside another header, through an -I path resolve
    # cannot place) is unknown; a `static` prototype before a plain definition, or a macro storage class (zlib's
    # `local`), leaves the definition unmarked, as before. Read the build's units once the product has compile flags.
    if def_file == call_file or def_file.endswith(_HEADERS):
        return True
    if call_file.endswith(_HEADERS):
        return None if call_file in _scope_set(index, def_file) else False
    units = _units_of(index)
    return not units.get(call_file, set()).isdisjoint(units.get(def_file, ()))


def _visible(index: SymbolIndex, file_path: str) -> set[str]:
    """*file_path*, its includes and their same-stem sources: the files whose definitions it sees."""
    cache = index.c_visible
    if file_path not in cache:
        cache[file_path] = {file_path, *include_scope(index, file_path), *paired_sources(index, file_path)}
    return cache[file_path]


def scoped_types(index: SymbolIndex, file_path: str, name: str, qualifier: str | None = None) -> list[str]:
    """The types named *name* that *file_path* sees (itself, its includes, their sources). A C++ qualifier
    (`oatpp::base::Environment`, `ns.Inner`) keeps only the types whose qualified name ends with it, when
    that leaves any; several left are narrowed to the nearest path."""
    files = _visible(index, file_path)
    found = [cid for cid in index.candidates(name)
             if cid in index.type_blocks and index.rows[cid].file_path in files]
    if qualifier and len(found) > 1:
        tail = "." + qualifier.replace("::", ".").strip(".")
        named = [cid for cid in found if ("." + _path_of(index.rows[cid].qualified_name)).endswith(tail)]
        found = named or found
    if len(found) > 1:
        # `typedef oatpp::async::Action Action;` in a class names the class beside it, not a second type.
        defined = [cid for cid in found if index.rows[cid].kind != "type_alias"]
        found = defined if len(defined) == 1 else found
    if len(found) > 1:
        best = max(_common_dirs(index.rows[cid].file_path, file_path) for cid in found)
        found = [cid for cid in found if _common_dirs(index.rows[cid].file_path, file_path) == best]
    return found


def qualified_callees(index: SymbolIndex, file_path: str, name: str, qualifier: str) -> list[str]:
    """The functions and methods `oatpp::concurrency::f()` / `utils::String::compare()` can mean: those whose
    qualified name ends with the written path, when its last segment names a repo namespace or type (so
    `conn.f()` on an untyped `conn` is never read as a path); several are narrowed to those *file_path* sees.
    A path into `std::` is the standard library's, never a repo specialization (`std::numeric_limits<T>::max()`)."""
    path = [part for part in qualifier.replace("::", ".").split(".") if part]
    if not path or in_std(qualifier) or not any(index.rows[cid].kind == "module" or cid in index.type_blocks
                           for cid in index.candidates(path[-1])):
        return []
    tail = "." + ".".join((*path, name))
    named = [cid for cid in index.candidates(name) if ("." + _path_of(index.rows[cid].qualified_name)).endswith(tail)]
    # `ns::Conn(..)` constructs the class, which is where a constructor call points, never at `Conn::Conn`.
    found = [cid for cid in named if cid in index.callable_blocks and path[-1] != name] \
        or [cid for cid in named if cid in index.type_blocks]
    if len(found) > 1:
        files = _visible(index, file_path)
        found = [cid for cid in found if index.rows[cid].file_path in files] or found
    return found


def owner_type(index: SymbolIndex, block_id: str, _bindings: Mapping[str, object] | None = None) -> str | None:
    """The class an out-of-line C++ method belongs to (`void Foo::bar() {}` in foo.cpp, `class Foo` in
    foo.hpp): the type its qualified name's owner names, among the types its file sees, the nearer of
    two same-named classes. An #include binds no names, so the file's import bindings play no part."""
    row = index.rows.get(block_id)
    while row is not None and row.kind not in ("method", "constructor") and row.parent_block_id:
        row = index.rows.get(row.parent_block_id)  # a lambda inside the method: the method's class
    if row is None or row.kind not in ("method", "constructor") or not row.file_path:
        return None
    owner = _path_of(row.qualified_name).rsplit(".", 1)[0] if "." in _path_of(row.qualified_name) else ""
    if not owner:
        return None
    found = scoped_types(index, row.file_path, owner.rsplit(".", 1)[-1], owner)
    return found[0] if len(found) == 1 else None


def _path_of(qualified_name: str | None) -> str:
    """`method:ns.Foo.bar` -> `ns.Foo.bar`."""
    return (qualified_name or "").partition(":")[2]


def paired_sources(index: SymbolIndex, file_path: str) -> list[str]:
    """The same-stem sources of *file_path* and of every header it sees: where their bodies usually are."""
    cache = index.c_paired  # read per call, like include_scope
    if file_path not in cache:
        headers = [p for p in (file_path, *include_scope(index, file_path)) if p.endswith(_HEADERS)]
        pairs = {p.rsplit(".", 1)[0] + ext for p in headers for ext in _SOURCES}
        cache[file_path] = sorted(p for p in pairs & index.record_by_file.keys() if p != file_path)
    return cache[file_path]
