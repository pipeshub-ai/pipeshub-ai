"""Per-language parser handlers, merged into the engine's registries.

A language adds its import, heritage and type-table handlers here, keyed by the
handler names in `lang_config`, instead of editing `engine.py`. A handler has
the signature of the engine's own handlers for that kind.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["HERITAGE_HANDLERS", "IMPORT_HANDLERS", "TYPE_TABLE_HANDLERS"]

IMPORT_HANDLERS: dict[str, Callable] = {}
HERITAGE_HANDLERS: dict[str, Callable] = {}
TYPE_TABLE_HANDLERS: dict[str, Callable] = {}
