"""Rust: a `use` path to the module file that holds its item, and an impl's methods to their type.

Crates are found from the tree alone (no Cargo.toml is read): a folder holding
`src/lib.rs` or `src/main.rs` is a crate named after the folder (`atuin-client`
is `atuin_client`). A module is `src/a/b.rs` or `src/a/b/mod.rs`. A path starts
at `crate::`, `self::`, `super::`, a crate of the repo, or a child module of the
importing one; any other first segment (`std`, `serde`) is outside the repo.
The longest prefix of the rest that names a module file is the module; the
segments after it are items in that file. A re-exported item (`pub use` in
lib.rs) resolves to the re-exporting file, which binds nothing that hides it.
"""
from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING

from app.modules.code_graph.guards import path_proximity_winner

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.modules.code_graph.symbol_index import SymbolIndex

__all__ = ["impl_type", "in_repo", "resolve"]

_ROOT_FILES = ("src/lib.rs", "src/main.rs")


class _Crates:
    def __init__(self, files: set[str]) -> None:
        self.files = files
        self.roots: dict[str, str] = {}      # crate folder ("" at the repo root) -> its root file
        for path in sorted(files):
            for root in _ROOT_FILES:
                if path == root or path.endswith("/" + root):
                    folder = path[: -len(root)].rstrip("/")
                    self.roots.setdefault(folder, path)
        self.by_name = {posixpath.basename(folder).replace("-", "_"): folder for folder in self.roots if folder}
        # Read once per build: file -> its `use` bindings, its top-level names; (path, file) -> resolve()'s answer.
        self._uses: dict[str, dict[str, str]] = {}
        self._tops: dict[str, set[str]] = {}
        self.resolved: dict[tuple[str, str], str | None] = {}

    def crate_of(self, path: str) -> str | None:
        folders = [f for f in self.roots if not f or path.startswith(f + "/")]
        return max(folders, key=len) if folders else None

    def module_of(self, path: str, crate: str) -> list[str]:
        rel = path[len(crate) + 1:] if crate else path
        if not rel.startswith("src/") or not rel.endswith(".rs"):
            return []
        parts = rel[4:-3].split("/")
        if parts[-1] == "mod" or (len(parts) == 1 and parts[0] in ("lib", "main")):
            parts = parts[:-1]
        return parts

    def module_file(self, crate: str, module: list[str]) -> str | None:
        if not module:
            return self.roots.get(crate)
        base = f"{crate}/src/" if crate else "src/"
        for candidate in (base + "/".join(module) + ".rs", base + "/".join(module) + "/mod.rs"):
            if candidate in self.files:
                return candidate
        return None

    def use_path(self, index: SymbolIndex, path: str, name: str) -> str | None:
        """The full path a `use` in *path* binds *name* to (`use super::helpers;` -> `super::helpers`)."""
        if path not in self._uses:
            uses: dict[str, str] = {}
            record = index.record_by_file.get(path)
            for block_id in index.blocks_by_record.get(record, ()) if record else ():
                for fact in index.rows[block_id].pending_edges:
                    if fact.get("relation") == "IMPORTS" and fact.get("qualifiedPrefix"):
                        uses.setdefault(fact.get("localName") or fact.get("toName") or "", fact["qualifiedPrefix"])
            self._uses[path] = uses
        return self._uses[path].get(name)

    def top_names(self, index: SymbolIndex, path: str) -> set[str]:
        """The names *path* defines at its top level."""
        if path not in self._tops:
            record = index.record_by_file.get(path)
            self._tops[path] = {row.name for b in (index.blocks_by_record.get(record, ()) if record else ())
                                if (row := index.rows.get(b)) is not None and row.name and not row.parent_block_id}
        return self._tops[path]


def _crates(files: set[str], index: SymbolIndex | None) -> _Crates:
    # Kept on the build's index when there is one (an index is unhashable, so no weak map).
    if index is None:
        return _Crates(files)
    cached = index.__dict__.get("_rust_crates")
    if cached is None or cached.files is not files:
        cached = index.__dict__["_rust_crates"] = _Crates(files)
    return cached


def resolve(specifier: str, from_file: str, file_paths: set[str], index: SymbolIndex | None) -> str | None:
    """`crate::a::b::Item`, `super::x::Y`, `atuin_client::db::Db` -> the module file defining the item."""
    crates = _crates(file_paths, index)
    key = (specifier, from_file)
    if key not in crates.resolved:
        found, item = _locate(crates, specifier, from_file, index)
        if item is None:
            crates.resolved[key] = None   # `use a::dirs;` names a module, which the answer key gives no import
        elif index is None:
            crates.resolved[key] = found
        else:
            defining = _reexported(index, crates, found, item, 0)
            crates.resolved[key] = defining if _defines(index, crates, defining, item) else None
    return crates.resolved[key]


def in_repo(specifier: str, from_file: str, file_paths: set[str], index: SymbolIndex | None) -> bool:
    """Whether a path starts in the repo (`crate::`, `self::`, `super::`, a repo crate, a child module),
    placeable or not: such a path is never an outside library. Also a head named like a module file beside
    the importing file or under it: `mod common;` in an integration test (`tests/x.rs` is a crate root the
    tree rules do not see), or a sibling a `use super::*` brought in (`use events::Event` in fsm/tests.rs).

    shortcut: a sibling file named like an outside crate (`time.rs` beside a `use time::Duration`) counts
    as the repo's; read the file's `mod` and glob `use` lines if such refs go missing from searches.
    A module the importing file declares (`mod mock { .. }`, `#[path = "common/agents.rs"] mod agents;`)
    reaches here as `self::<head>`, which the parser writes for it (`_in_file_module`).
    """
    crates = _crates(file_paths, index)
    head = specifier.split("::")[0]
    # `crate::` and a repo crate's name are the repo's from any file, one outside every crate's tree too (ripgrep's
    # crates/core, whose main.rs is no `src/` root)
    if head in ("crate", "self", "super") or head in crates.by_name \
            or _start(crates, specifier, from_file, index) is not None:
        return True
    folder, stem = posixpath.split(from_file.removesuffix(".rs"))
    found = {f"{base}/{head}{tail}" for base in (folder, f"{folder}/{stem}") for tail in (".rs", "/mod.rs")}
    found.discard(from_file)   # caps/axum.rs's own `use axum::` is the axum crate
    return any(path in file_paths for path in found)


_MAX_USE_HOPS = 4


def _start(crates: _Crates, specifier: str, from_file: str, index: SymbolIndex | None = None,
           depth: int = 0) -> tuple[str, list[str], list[str]] | None:
    """(crate folder, module path, remaining segments) a path's head names, or None outside the repo. A head
    the file's own `use` binds (`use super::helpers;` then `helpers::f`) stands for the path it binds."""
    here = crates.crate_of(from_file)
    if here is None:
        return None
    segs = specifier.split("::")
    head, rest = segs[0], segs[1:]
    if head == "crate":
        return here, [], rest
    if head == "self":
        return here, crates.module_of(from_file, here), rest
    if head == "super":
        module = crates.module_of(from_file, here)[:-1]
        while rest and rest[0] == "super":
            module, rest = module[:-1], rest[1:]
        return here, module, rest
    if head in crates.by_name:
        return crates.by_name[head], [], rest
    if crates.module_file(here, [*crates.module_of(from_file, here), head]):
        return here, [*crates.module_of(from_file, here), head], rest
    bound = crates.use_path(index, from_file, head) if index is not None and depth < _MAX_USE_HOPS else None
    if bound and bound.split("::")[0] != head:
        return _start(crates, "::".join([bound, *rest]), from_file, index, depth + 1)
    return None


def _locate(crates: _Crates, specifier: str, from_file: str,
            index: SymbolIndex | None = None) -> tuple[str | None, str | None]:
    """(the module file a path's longest module prefix names, the item after it, if any)."""
    start = _start(crates, specifier, from_file, index)
    if start is None:
        return None, None
    crate, module, rest = start
    for k in range(len(rest), -1, -1):
        found = crates.module_file(crate, module + rest[:k])
        if found:
            return found, (rest[k] if k < len(rest) else None)
    return None, None


_MAX_REEXPORT_HOPS = 4


def _defines(index: SymbolIndex, crates: _Crates, path: str, name: str) -> bool:
    return name in crates.top_names(index, path)


def _reexported(index: SymbolIndex, crates: _Crates, path: str, item: str, depth: int) -> str:
    """The file defining *item*, following `pub use` hops (a Rust file's imports stay on its nodes, so a
    full and an incremental build follow the same hops); *path* itself when it defines *item* or no
    hop leads to a definition."""
    if _defines(index, crates, path, item) or depth >= _MAX_REEXPORT_HOPS:
        return path
    record = index.record_by_file.get(path)
    for block_id in index.blocks_by_record.get(record, ()) if record else ():
        for fact in index.rows[block_id].pending_edges:
            relation = fact.get("relation")
            if relation == "IMPORTS" and (fact.get("localName") or fact.get("toName")) == item:
                source, original = (_locate(crates, fact.get("qualifiedPrefix") or "", path, index)[0],
                                    fact.get("toName") or item)
            elif relation == "RE_EXPORTS":   # `pub use model::*;`
                source, original = _locate(crates, fact.get("toName") or "", path, index)[0], item
            else:
                continue
            if source and source != path:
                found = _reexported(index, crates, source, original, depth + 1)
                if _defines(index, crates, found, original):
                    return found
    return path


def impl_type(index: SymbolIndex, block_id: str, bindings: Mapping[str, object]) -> str | None:
    """The type an `impl` block's method belongs to: the type of that name in the impl's own module scope, else
    the one the file's `use` binds to that name (none when bound outside the repo, `use reqwest::Client`), else
    the type of that name, same file, else nearest."""
    row, seen = index.rows.get(block_id), set()
    while row is not None and row.kind != "impl" and row.parent_block_id and row.parent_block_id not in seen:
        seen.add(row.parent_block_id)
        row = index.rows.get(row.parent_block_id)
    if row is None or row.kind != "impl" or not row.name:
        return None
    # A type of that name in the impl's own module scope is what it names, whatever a `use` elsewhere in the file
    # binds (`mod tests { use super::TestCase; }` binds the file's name, unplaced when tests/ is no crate module).
    beside = [c for c in index.candidates(row.name)
              if c in index.type_blocks and index.file_by_block.get(c) == row.file_path
              and (own := index.rows.get(c)) is not None and own.parent_block_id == row.parent_block_id]
    if len(beside) == 1:
        return beside[0]
    bound = bindings.get(row.name)
    if bound is not None:
        return bound if bound in index.type_blocks else None
    types = [c for c in index.candidates(row.name)
             if c in index.type_blocks and (index.file_by_block.get(c) or "").endswith(".rs")]
    winners = path_proximity_winner(types, index.file_by_block, row.file_path)
    return winners[0] if len(winners) == 1 else None
