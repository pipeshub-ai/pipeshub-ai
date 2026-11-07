"""What a language family's names can mean, as rules the edge builder reads.

The builder used to branch on family names at each decision (`family == "js"`,
`family in {"jvm", "csharp"}`). Those decisions live here as one table, so a
language is added by adding its row, and its hooks, not by editing the builder.
Every row reproduces the builder's behaviour from before the table existed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.modules.code_graph.resolvers import c_family, rust

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
    # namespace across files; what a C/C++ file's includes include).
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
    # Files a bare name likely reaches when neither the file, its imports nor its
    # implicit scope define it, as INFERRED evidence: a C header's same-stem source.
    inferred_scope: Callable[[SymbolIndex, str], Iterable[str]] | None = None
    # An out-of-line method is also matched under its type's namespaces (`method:oatpp.base.Environment.tick`
    # for `class:oatpp.base.Environment`), not only as `Type.method` (C/C++).
    out_of_line_under_namespaces: bool = False
    # The types a name (with an optional qualifier) can mean in a file, beyond its own and its
    # bindings: what a C++ file sees through its includes (`Environment::tick()`, `Conn* c; c->open()`).
    type_scope: Callable[[SymbolIndex, str, str, str | None], list[str]] | None = None
    # A file's IMPORTS_FROM facts stay on its nodes, as builds read them for files they are not re-resolving
    # (C/C++: a file sees what its includes include, transitively).
    resident_imports: bool = False
    # Files whose function-like macros a call sees besides those defined above it in its own file, so a call
    # through a wrapper macro reaches the one function it expands to (C/C++: what its includes include).
    macro_scope: Callable[[SymbolIndex, str], Iterable[str]] | None = None
    # The files that see these files' definitions through their includes, transitively, so a file whose include
    # list changed re-resolves all of them, not only its direct includers (C/C++).
    includers: Callable[[SymbolIndex, set[str]], set[str]] | None = None
    # (call file, defining file) -> whether a definition marked STATIC there (C/C++ internal linkage) is in
    # the call's translation unit; one that is not is never reached, by a name guess, a paired source or a
    # qualifier; one that may be (the hook says None) leaves the call with no edge. None: the family has no
    # internal linkage.
    same_unit: Callable[[SymbolIndex, str, str], bool | None] | None = None
    # Whether a bare receiver the calling file does not type may be a field of the enclosing class, typed by the
    # class's own declaration (`m_list.notify()` in foo.cpp, `CoroutineWaitList m_list;` in foo.hpp: C++).
    implicit_member_receivers: bool = False
    # (file, name, qualifier) -> the callables a written path (`ns::f()`, `ns::Cls::f()`) can mean, tried when the
    # qualifier names no type the file sees (C++: a free function in a namespace, a method defined out of line).
    qualified_callees: Callable[[SymbolIndex, str, str, str], list[str]] | None = None
    # Whether a receiver an initializer types (`auto& c = Cfg::get(x)`, kept as `=Cfg.get(x)`) or written as a chain
    # (`a.b()->c()`) is evaluated through the declared field and return types (C++; JS/TS have their own chain rule).
    evaluated_receivers: bool = False


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
    # C/C++: a bare call inside a method is first `this->name()`; an out-of-line method (`Foo::bar`) is
    # found anywhere in the repo and belongs to the class its owner names.
    "c": FamilyRules(bare_call_reaches_method="same_class", out_of_line_methods="repo",
                     out_of_line_under_namespaces=True, enclosing_type=c_family.owner_type,
                     implicit_scope=c_family.include_scope,
                     inferred_scope=c_family.paired_sources, type_scope=c_family.scoped_types,
                     resident_imports=True, macro_scope=c_family.include_scope, includers=c_family.includers,
                     same_unit=c_family.same_unit, implicit_member_receivers=True,
                     qualified_callees=c_family.qualified_callees, evaluated_receivers=True),
}


def rules_for(family: str | None) -> FamilyRules:
    """The family's rules; a family without a row gets the defaults."""
    return _RULES.get(family or "", _DEFAULT)
