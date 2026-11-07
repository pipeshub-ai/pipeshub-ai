"""Per-language parser handlers, merged into the engine's registries.

A language adds its import, heritage and type-table handlers here, keyed by the
handler names in `lang_config`, instead of editing `engine.py`. A handler has
the signature of the engine's own handlers for that kind.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.parsers.code_parser.languages import c_family, rust

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["CALLEE_HANDLERS", "HERITAGE_HANDLERS", "IMPORT_HANDLERS", "MACRO_ARGUMENT_HANDLERS", "MACRO_TABLE_HANDLERS",
           "MODIFIER_HANDLERS", "RECEIVER_TYPE_HANDLERS", "TYPE_TABLE_HANDLERS"]

IMPORT_HANDLERS: dict[str, Callable] = {"rust": rust.imports, "c_family": c_family.imports}
HERITAGE_HANDLERS: dict[str, Callable] = {"rust": rust.heritage, "cpp": c_family.heritage}
TYPE_TABLE_HANDLERS: dict[str, Callable] = {"rust": rust.type_table, "cpp": c_family.type_table}
# Keyed by language name: (call node, source, config) -> (name, is_member, receiver, qualifier), in place of
# the engine's generic callee reader.
CALLEE_HANDLERS: dict[str, Callable] = {"rust": rust.callee}
# Keyed by language name: (root, source, reparse, config) -> {macro invocation start byte: its arguments parsed as
# code}, whose calls the walker reads where the invocation's own tokens hold none.
MACRO_ARGUMENT_HANDLERS: dict[str, Callable] = {"rust": rust.macro_arguments}
# Keyed by language name: (call node, source, per-function cache, config, the file-wide table's type for the
# receiver) -> the receiver's type, or `=expr` for the builder to evaluate, in place of the engine's own lookup.
RECEIVER_TYPE_HANDLERS: dict[str, Callable] = {"rust": rust.receiver_type}
# (root, source, table, config) -> macros the table's caps dropped; the table is name -> [line, called names].
MACRO_TABLE_HANDLERS: dict[str, Callable] = {"c_family": c_family.macro_table}
# Keyed by language name: (definition node, source) -> the symbol's modifiers.
MODIFIER_HANDLERS: dict[str, Callable] = {"c": c_family.modifiers, "cpp": c_family.cpp_modifiers}
