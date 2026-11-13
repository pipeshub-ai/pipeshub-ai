"""What a language family's names can mean, as rules the edge builder reads.

The builder used to branch on family names at each decision (`family == "js"`,
`family in {"jvm", "csharp"}`). Those decisions live here as one table, so a
language is added by adding its row, and its hooks, not by editing the builder.
Every row reproduces the builder's behaviour from before the table existed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.modules.code_graph.resolvers import c_family, csharp, php, rust

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
    # Whether a bare name an import binds to a module is that module, never a same-named definition.
    module_binding_is_the_module: bool = False
    # Whether that module may itself be what the name calls: a CommonJS module can be a function or class
    # (`module.exports = f`, then `const f = require("./f")`), so the name is its own top-level definition.
    module_may_be_callable: bool = False
    # Whether a top-level `field` (a JS/TS module-level value, `export const vec3 = makeNode()`) is no call
    # target by any rung (an import binding, `state.locator(x)`). A class field stays callable.
    top_level_fields_uncallable: bool = False
    # Whether the file's own top-level definition can take a name back from an import binding: Python binds
    # names in order and per function scope (a later `def time()`, an import local to another function). JS
    # hoists declarations and a `require` assignment rebinds them, so there the import binding stands.
    definition_rebinds_import: bool = False
    # Whether an import written inside a definition is still the file's edge: a Python function-local
    # `from m import f` binds `f` only in that function, but the file depends on m wherever the statement
    # sits, as with a module-level import.
    local_imports_are_the_files: bool = False
    # Whether `from m import *` brings only m's literal `__all__`, else its names without a leading `_`
    # (Python); elsewhere a star import's names are not filtered.
    star_import_obeys_all: bool = False
    # Whether a typed receiver's call may land on a field: TS/JS call function-valued fields; a Python
    # field (a dependency-injector provider) is no callee.
    typed_calls_reach_fields: bool = True
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
    # parser already chose between a local binding and the file-wide table's guess; Python's types a receiver
    # from the bindings that reach it (the file-wide table typed `user.save()` with another function's
    # `user = AnonymousUser()`).
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
    "py": FamilyRules(module_binding_is_the_module=True, definition_rebinds_import=True,
                      local_imports_are_the_files=True, star_import_obeys_all=True, typed_calls_reach_fields=False,
                      file_wide_receiver_types=False),
    "js": FamilyRules(lexical_module_scope=True, module_binding_is_the_module=True, module_may_be_callable=True,
                      top_level_fields_uncallable=True),
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
