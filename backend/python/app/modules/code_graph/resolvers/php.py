"""PHP: a `use` names a definition by its fully qualified name; a file sees its own namespace.

`use App\\Models\\User;` resolves to the file declaring `App.Models.User`, found
by the blocks' qualified names, never by a path guess: `Illuminate\\Http\\Request`
must not land on a repo's app/Http/Request.php. A name no repo file declares is
outside the repo, and the import binds it so (a vendor `Request` is not the
repo's). A bare name also sees the other files of the file's namespace; unlike
C#, PHP gives no enclosing namespace.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.code_graph.guards import path_proximity_winner
from app.modules.code_graph.resolvers.namespaces import namespaces

if TYPE_CHECKING:
    from app.modules.code_graph.symbol_index import SymbolIndex

__all__ = ["namespace_scope", "resolve"]


def resolve(specifier: str, from_file: str, file_paths: set[str], index: SymbolIndex | None) -> str | None:
    """`App\\Models\\User` -> the one file declaring it (the nearest, if a definition is repeated)."""
    if index is None:
        return None
    files = sorted(namespaces(index, ".php").declared.get(specifier.strip().lstrip("\\").replace("\\", "."), ()))
    winners = path_proximity_winner(files, {f: f for f in files}, from_file)
    return winners[0] if len(winners) == 1 else None


def namespace_scope(index: SymbolIndex, file_path: str) -> list[str]:
    """The other files of *file_path*'s namespace, sorted."""
    spaces = namespaces(index, ".php")
    if file_path not in spaces.scope:
        spaces.scope[file_path] = spaces.in_namespaces(set(spaces.of_file.get(file_path, ())), file_path)
    return spaces.scope[file_path]
