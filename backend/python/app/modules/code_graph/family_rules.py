"""What a language family's names can mean, as rules the edge builder reads.

The builder used to branch on family names at each decision (`family == "js"`,
`family in {"jvm", "csharp"}`). Those decisions live here as one table, so a
language is added by adding its row, and its hooks, not by editing the builder.
Every row reproduces the builder's behaviour from before the table existed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.modules.code_graph.resolvers import rust

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from app.modules.code_graph.symbol_index import SymbolIndex

__all__ = ["FamilyRules", "rules_for"]


@dataclass(frozen=True)
class FamilyRules:
    # "never": a method is only ever reached through a receiver (Go's `x.m()`), at
    # every resolution step. "same_class": a bare call inside a method is first
    # `this.name()`, the class's own or an inherited method (Java, C#). "any": no
    # rule, so a bare name may match a method like any other definition.
    bare_call_reaches_method: Literal["never", "same_class", "any"] = "any"
    # ES modules: a bare name is a lexical binding, so it is never a method of its
    # own file and never another module's definition without an import (no name
    # guess). Imported modules' definitions are still candidates.
    lexical_module_scope: bool = False
    # Whether a bare name may be a definition of a module the file imports. Go's
    # never is: an imported package's names take `pkg.`.
    bare_names_from_imported_modules: bool = True
    self_receivers: frozenset[str] = frozenset({"self", "this"})
    super_receivers: frozenset[str] = frozenset({"super()", "super"})
    # Where a method declared outside its type's body is looked for: the type's
    # folder (a Go package), or anywhere in the repo, the same folder first, then
    # the nearest path, only when that leaves one.
    out_of_line_methods: Literal["same_folder", "repo"] = "same_folder"
    # The type a block belongs to when no enclosing block is a type (a Rust impl
    # names its type rather than containing it). Called with the block's file's import bindings
    # (name -> block, or the outside marker).
    enclosing_type: Callable[[SymbolIndex, str, Mapping[str, str]], str | None] | None = None
    # Files whose definitions a bare name sees without an import (one C# or PHP
    # namespace across files).
    implicit_scope: Callable[[SymbolIndex, str], Iterable[str]] | None = None
    # How a path joins its segments when a qualified name (`a::b::Trait`, `helpers::f()`) is a full path the
    # family's import resolver places (Rust "::"): one it cannot place is outside the repo, never a same-named
    # repo definition, and a name a `use` binds outside the repo is no repo type. None: no such rule.
    qualified_path_separator: str | None = None
    # A block of kind `impl` names an existing type rather than defining one (Rust): never what an import of
    # that name imports, and its methods and implemented traits are that type's own.
    impl_blocks_name_their_type: bool = False
    # Block kinds no call can run, so a call resolved to one is dropped (Rust: `mod tag`, a field or
    # const, an associated `type Err`).
    uncallable_kinds: frozenset[str] = frozenset()
    # Whether a receiver typed `=head.field.method()` (Rust's parser writes one for a chained or let-bound
    # receiver) is evaluated: the head's type, then each field's declared type and each method's return type.
    typed_receiver_chains: bool = False
    # Whether a receiver its parser left untyped takes a type from the file's table by bare name. Rust's
    # parser already chose between a local binding and the file-wide table's guess.
    file_wide_receiver_types: bool = True


_DEFAULT = FamilyRules()
_RULES: dict[str, FamilyRules] = {
    "js": FamilyRules(lexical_module_scope=True),
    "go": FamilyRules(bare_call_reaches_method="never", bare_names_from_imported_modules=False),
    "jvm": FamilyRules(bare_call_reaches_method="same_class"),
    "csharp": FamilyRules(bare_call_reaches_method="same_class"),
    # Rust: a bare call is a function, never a method (Go's rule); `use m::Item` scopes only Item, never the
    # rest of m (Go's rule again). An impl's methods belong to the type it names, wherever in the crate it is
    # written.
    "rust": FamilyRules(bare_call_reaches_method="never", bare_names_from_imported_modules=False,
                        self_receivers=frozenset({"self", "this", "Self"}),
                        out_of_line_methods="repo", enclosing_type=rust.impl_type, qualified_path_separator="::",
                        impl_blocks_name_their_type=True, typed_receiver_chains=True,
                        file_wide_receiver_types=False,
                        uncallable_kinds=frozenset({"module", "field", "type_alias"})),
}


def rules_for(family: str | None) -> FamilyRules:
    """The family's rules; a family without a row gets the defaults."""
    return _RULES.get(family or "", _DEFAULT)
