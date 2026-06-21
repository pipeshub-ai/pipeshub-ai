"""
call_extractor.py — deterministic, tree-sitter-based symbol and call-site extractor.

Produces two lists from a source file:
- ``definitions``: top-level and class-level symbols defined in the file
  (functions, methods, classes, interfaces, …).
- ``calls``: call sites — places where a symbol is called — annotated with
  the enclosing function/method name so callee resolution can be scoped.

Reuses ``LANG_CONFIG`` and the tree-sitter parsers from ``parser.py``.
Supported languages: Python + TypeScript / JavaScript / TSX (Phase 1).

Design:
- A single AST walk collects both definitions and call sites.
- When a definition node (function/class/…) is found, the symbol is recorded
  and the walk descends into its body only, tracking it as the new "caller"
  context for any call sites found inside.
- A call node (Python ``call``, JS/TS ``call_expression`` / ``new_expression``)
  is recorded with the innermost enclosing function or method name as ``caller``
  (empty string = module-level).
- Unsupported languages return empty ``CodeSymbols``.
- Malformed source that tree-sitter can't parse returns empty ``CodeSymbols``;
  no exception is raised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from tree_sitter import Node

from app.modules.parsers.code_parser.parser import (
    LANG_CONFIG,
    _body_node,
    _named,
    decode_source,
    MAX_FILE_SIZE_BYTES,
)


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass
class Definition:
    """A symbol (function, method, class, …) defined in the source file."""

    name: str        # Symbol name as it appears in source
    kind: str        # "function" | "method" | "class" | "interface" | …
    start_line: int  # 1-indexed


@dataclass
class CallSite:
    """A place in source code where a symbol is called."""

    callee: str  # Name of the called symbol (may be "module.func" or just "func")
    line: int    # 1-indexed source line
    caller: str  # Innermost enclosing function/method name; "" = module-level


@dataclass
class CodeSymbols:
    """All extracted symbols from a single source file."""

    definitions: list[Definition] = field(default_factory=list)
    calls: list[CallSite] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-language call-node detection
# ---------------------------------------------------------------------------

# Maps language name → set of AST node types that represent a function call
_CALL_NODE_TYPES: dict[str, set[str]] = {
    "python":     {"call"},
    "javascript": {"call_expression", "new_expression"},
    "typescript": {"call_expression", "new_expression"},
    "tsx":        {"call_expression", "new_expression"},
}


def _extract_callee(node: Node, language: str) -> Optional[str]:
    """
    If *node* is a call-site node, return the callee name; otherwise ``None``.

    For Python ``call``:  uses the ``function`` field.
    For JS/TS ``call_expression``:  uses the ``function`` field.
    For JS/TS ``new_expression``:  uses the ``constructor`` field.

    Both ``identifier`` (plain call) and ``attribute``/``member_expression``
    (method call) are handled; for the latter, returns the attribute name only
    (e.g., ``obj.foo()`` → ``"foo"``).
    """
    call_types = _CALL_NODE_TYPES.get(language)
    if not call_types or node.type not in call_types:
        return None

    # Which field name contains the function/constructor reference?
    if node.type == "new_expression":
        fn_field = "constructor"
    else:
        fn_field = "function"

    fn_node = node.child_by_field_name(fn_field)
    if fn_node is None:
        return None

    # Plain identifier: foo()
    if fn_node.type in ("identifier", "name"):
        try:
            return fn_node.text.decode("utf-8", errors="replace")
        except Exception:
            return None

    # Python attribute: obj.method()
    if fn_node.type == "attribute":
        attr = fn_node.child_by_field_name("attribute")
        if attr is not None:
            try:
                return attr.text.decode("utf-8", errors="replace")
            except Exception:
                return None

    # JS/TS member_expression: obj.method()
    if fn_node.type == "member_expression":
        prop = fn_node.child_by_field_name("property")
        if prop is not None:
            try:
                return prop.text.decode("utf-8", errors="replace")
            except Exception:
                return None

    # Chained / complex expression — fall back to the whole text (truncated)
    try:
        text = fn_node.text.decode("utf-8", errors="replace")
        # Keep only the last component after "." to get the direct symbol name
        return text.split(".")[-1].strip() or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# AST walker
# ---------------------------------------------------------------------------


def _walk(
    node: Node,
    language: str,
    cfg: dict,
    definitions: list[Definition],
    calls: list[CallSite],
    context: list[str],
    in_container: bool,
) -> None:
    """Recursively collect definitions and call sites from *node*.

    Args:
        node:          Current tree-sitter AST node.
        language:      Language key (e.g. ``"python"``).
        cfg:           Entry from ``LANG_CONFIG[language]``.
        definitions:   Output list for symbol definitions.
        calls:         Output list for call sites.
        context:       Stack of enclosing symbol names (innermost last).
        in_container:  True when inside a class/interface body.
    """
    # ── Is this a definition node? ──────────────────────────────────────────
    if node.type in cfg["block_types"]:
        resolved = cfg["resolve"](node, in_container)
        if resolved is not None:
            kind, name, inner = resolved
            if name:
                definitions.append(Definition(
                    name=name,
                    kind=kind,
                    start_line=node.start_point[0] + 1,
                ))
                body = _body_node(inner, cfg["body_field"])
                # When the body is not found via field name, fall back to
                # recursing into the inner node itself (handles arrow functions,
                # variable declarators with function values, etc.).
                recurse_target = body if body is not None else inner
                new_context = context + [name]
                new_in_container = kind in ("class", "interface", "impl", "trait", "struct")
                _walk(recurse_target, language, cfg, definitions, calls,
                      new_context, new_in_container)
            # Return regardless of whether we got a name — the outer
            # decorated_definition / export_statement should not be walked
            # again to avoid double-counting the wrapped function_definition.
            return

    # ── Is this a call site? ─────────────────────────────────────────────────
    callee = _extract_callee(node, language)
    if callee:
        caller = context[-1] if context else ""
        calls.append(CallSite(callee=callee, line=node.start_point[0] + 1, caller=caller))

    # ── Recurse into children ────────────────────────────────────────────────
    for child in node.named_children:
        _walk(child, language, cfg, definitions, calls, context, in_container)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_symbols(source: bytes, language: str) -> CodeSymbols:
    """Parse *source* and return all definitions + call sites.

    Args:
        source:   Raw source bytes (non-UTF-8 is normalised automatically).
        language: Language key matching ``LANG_CONFIG`` (e.g. ``"python"``).
                  Unsupported languages return empty ``CodeSymbols``.

    Returns:
        ``CodeSymbols`` with ``definitions`` and ``calls``.
        Never raises — malformed source returns empty ``CodeSymbols``.
    """
    if not source or language not in LANG_CONFIG:
        return CodeSymbols()

    if len(source) > MAX_FILE_SIZE_BYTES:
        return CodeSymbols()

    cfg = LANG_CONFIG[language]
    if language not in _CALL_NODE_TYPES:
        # Language not yet supported for call extraction — return defs only if
        # the parse infrastructure is there, skip calls.
        # For Phase 1, only Python + TS/JS/TSX are supported.
        return CodeSymbols()

    try:
        source = decode_source(source)
        from tree_sitter import Language as TSLanguage, Parser
        lang_obj = cfg["language"]()
        parser = Parser(lang_obj)
        tree = parser.parse(source)
    except Exception:
        return CodeSymbols()

    definitions: list[Definition] = []
    calls: list[CallSite] = []
    try:
        _walk(tree.root_node, language, cfg, definitions, calls, context=[], in_container=False)
    except Exception:
        return CodeSymbols()

    return CodeSymbols(definitions=definitions, calls=calls)
