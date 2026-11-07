"""Per-language parser handlers, merged into the engine's registries.

A language adds its import, heritage and type-table handlers here, keyed by the
handler names in `lang_config`, instead of editing `engine.py`. A handler has
the signature of the engine's own handlers for that kind.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.parsers.code_parser.languages import c_family

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["HERITAGE_HANDLERS", "IMPORT_HANDLERS", "MACRO_TABLE_HANDLERS", "MODIFIER_HANDLERS", "TYPE_TABLE_HANDLERS"]

IMPORT_HANDLERS: dict[str, Callable] = {"c_family": c_family.imports}
HERITAGE_HANDLERS: dict[str, Callable] = {"cpp": c_family.heritage}
TYPE_TABLE_HANDLERS: dict[str, Callable] = {"cpp": c_family.type_table}
# (root, source, table, config) -> macros the table's caps dropped; the table is name -> [line, called names].
MACRO_TABLE_HANDLERS: dict[str, Callable] = {"c_family": c_family.macro_table}
# Keyed by language name: (definition node, source) -> the symbol's modifiers.
MODIFIER_HANDLERS: dict[str, Callable] = {"c": c_family.modifiers, "cpp": c_family.cpp_modifiers}
