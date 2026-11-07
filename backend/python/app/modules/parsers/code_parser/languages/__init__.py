"""Per-language parser handlers, merged into the engine's registries.

A language adds its import, heritage and type-table handlers here, keyed by the
handler names in `lang_config`, instead of editing `engine.py`. A handler has
the signature of the engine's own handlers for that kind; a name may not reuse
a core one.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.parsers.code_parser.languages import csharp, php

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["HERITAGE_HANDLERS", "IMPORT_HANDLERS", "MODIFIER_HANDLERS", "TYPE_TABLE_HANDLERS"]

IMPORT_HANDLERS: dict[str, Callable] = {"csharp": csharp.imports, "php": php.imports}
HERITAGE_HANDLERS: dict[str, Callable] = {"csharp": csharp.heritage, "php": php.heritage}
TYPE_TABLE_HANDLERS: dict[str, Callable] = {"csharp": csharp.type_table, "php": php.type_table}
# Keyed by language name: (definition node, source) -> the symbol's modifiers.
MODIFIER_HANDLERS: dict[str, Callable] = {"csharp": csharp.modifiers}
