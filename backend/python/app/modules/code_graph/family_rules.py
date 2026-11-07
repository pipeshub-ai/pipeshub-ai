"""What a language family's names can mean, as rules the edge builder reads.

The builder used to branch on family names at each decision (`family == "js"`,
`family in {"jvm", "csharp"}`). Those decisions live here as one table, so a
language is added by adding its row, and its hooks, not by editing the builder.
Every row reproduces the builder's behaviour from before the table existed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.modules.code_graph.resolvers import csharp, php

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

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
    # names its type rather than containing it).
    enclosing_type: Callable[[SymbolIndex, str], str | None] | None = None
    # Files whose definitions a bare name sees without an import (one C# or PHP
    # namespace across files).
    implicit_scope: Callable[[SymbolIndex, str], Iterable[str]] | None = None
    # implicit_scope's files in the language's lookup order, nearest first (C#: innermost namespace's
    # members, its usings, outward, then the compilation unit): among several candidates, the nearest win.
    scope_levels: Callable[[SymbolIndex, str], list[set[str]]] | None = None
    # (file, method name) -> methods callable on any receiver that file sees (C# extension methods),
    # tried when the receiver's own type has no such method.
    extension_methods: Callable[[SymbolIndex, str, str], list[str]] | None = None
    # A type's other declarations, whose members and bases are its own (a C# `partial` class across files).
    type_parts: Callable[[SymbolIndex, str], list[str]] | None = None
    # A type reference's type-argument count picks among same-named types (C# `IHandler<T>` and `IHandler`
    # are two types; the parser strips arity from both names).
    disambiguate_repeats_by_arity: bool = False


_DEFAULT = FamilyRules()
_RULES: dict[str, FamilyRules] = {
    "js": FamilyRules(lexical_module_scope=True),
    "go": FamilyRules(bare_call_reaches_method="never", bare_names_from_imported_modules=False),
    "jvm": FamilyRules(bare_call_reaches_method="same_class"),
    "csharp": FamilyRules(bare_call_reaches_method="same_class",
                          super_receivers=frozenset({"super()", "super", "base"}),
                          implicit_scope=csharp.namespace_scope, scope_levels=csharp.scope_levels,
                          extension_methods=csharp.extension_methods,
                          type_parts=csharp.partial_parts, disambiguate_repeats_by_arity=True),
    # PHP: a bare `view()` is a function, never a method (Go's rule), and stays name-guessed as today.
    "php": FamilyRules(bare_call_reaches_method="never",
                       self_receivers=frozenset({"self", "this", "$this", "static"}),
                       super_receivers=frozenset({"super()", "super", "parent"}),
                       implicit_scope=php.namespace_scope),
}


def rules_for(family: str | None) -> FamilyRules:
    """The family's rules; a family without a row gets the defaults."""
    return _RULES.get(family or "", _DEFAULT)
