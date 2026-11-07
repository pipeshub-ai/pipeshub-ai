"""Import resolvers for the families `ImportResolution` has no branch for.

A family's resolver maps an import specifier, the importing file's path, the
repo's file paths and the build's symbol index (None outside a build) to the
imported file, or None. A family lands its import
handler, its resolver here and its `module_refs` extensions in one change: a
handler without a resolver binds every import to nothing and hides same-named
repo definitions.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.code_graph.symbol_index import SymbolIndex

__all__ = ["IN_REPO", "RESOLVERS", "Resolver"]

Resolver = Callable[[str, str, set[str], "SymbolIndex | None"], "str | None"]
# Same arguments: whether a specifier the resolver cannot place still names repo code (a Rust `crate::`
# path to a module, an item it cannot find), so it is unresolved rather than an outside library.
InRepo = Callable[[str, str, set[str], "SymbolIndex | None"], bool]


def _registry() -> tuple[dict[str, Resolver], dict[str, InRepo]]:
    from app.modules.code_graph.resolvers import c_family, rust

    return {"rust": rust.resolve, "c": c_family.resolve}, {"rust": rust.in_repo}


RESOLVERS, IN_REPO = _registry()
