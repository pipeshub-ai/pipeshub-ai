#!/usr/bin/env python3
"""
chunker.py — break source files into semantically meaningful blocks using tree-sitter.

Designed for RAG / embedding pipelines. Each block carries clean line ranges, attached
leading comments and decorators, ancestor (class) path metadata, and the raw text.

Supported languages: Python, JavaScript, JSX, TypeScript, TSX, Go, Java, Rust, C, C++.
To add a language: add an entry to LANG_CONFIG with a `resolve_*` function.

Block kinds emitted:
    function, method, class, interface, type_alias, enum, struct, trait, impl,
    namespace, module, imports, statement, comment

Usage:
    python chunker.py path/to/file.py
    python chunker.py path/to/file.py --pretty
    python chunker.py path/to/file.py --language python   # force a language
    python chunker.py path/to/file.py --no-text           # omit `text` from output
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

from tree_sitter import Language, Parser, Node

import tree_sitter_python
import tree_sitter_javascript
import tree_sitter_typescript
import tree_sitter_go
import tree_sitter_java
import tree_sitter_rust
import tree_sitter_c
import tree_sitter_cpp


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------

@dataclass
class Block:
    type: str                       # function | method | class | imports | statement | comment | ...
    name: Optional[str]             # identifier if applicable
    language: str
    start_line: int                 # 1-indexed inclusive
    end_line: int                   # 1-indexed inclusive
    start_byte: int                 # 0-indexed byte offset in source
    end_byte: int                   # 0-indexed byte offset in source (exclusive)
    path: list[dict] = field(default_factory=list)   # [{"type":..., "name":...}, ...] of ancestors
    parent: Optional[str] = None    # "Outer::Inner" path of parent block names
    text: str = ""


# ---------------------------------------------------------------------------
# Resolvers: per-language logic to turn a candidate node into (kind, name, inner_node).
# `inner_node` is the actual definition node (e.g. function_definition inside a
# decorated_definition wrapper) — used for descending into its body.
# ---------------------------------------------------------------------------

def _named(node: Node, field_name: str = "name") -> Optional[str]:
    n = node.child_by_field_name(field_name)
    if n is None:
        return None
    try:
        return n.text.decode("utf8", errors="replace")
    except Exception:
        return None


def resolve_python(node: Node, in_container: bool):
    inner = node
    if node.type == "decorated_definition":
        # Unwrap to find the actual function or class
        for c in node.named_children:
            if c.type in ("function_definition", "class_definition"):
                inner = c
                break
        else:
            return None
    if inner.type == "function_definition":
        kind = "method" if in_container else "function"
        return (kind, _named(inner), inner)
    if inner.type == "class_definition":
        return ("class", _named(inner), inner)
    return None


def resolve_javascript(node: Node, in_container: bool):
    inner = node
    # Unwrap export_statement to find the actual declaration
    if node.type == "export_statement":
        for c in node.named_children:
            if c.type in ("function_declaration", "generator_function_declaration",
                          "class_declaration", "lexical_declaration", "variable_declaration"):
                inner = c
                break
        else:
            return None

    if inner.type in ("function_declaration", "generator_function_declaration"):
        return ("method" if in_container else "function", _named(inner), inner)
    if inner.type == "class_declaration":
        return ("class", _named(inner), inner)
    if inner.type == "method_definition":
        return ("method", _named(inner), inner)

    # const foo = () => ... / const Foo = class { ... }
    if inner.type in ("lexical_declaration", "variable_declaration"):
        for declarator in inner.named_children:
            if declarator.type != "variable_declarator":
                continue
            value = declarator.child_by_field_name("value")
            if value is None:
                continue
            if value.type in ("arrow_function", "function_expression", "generator_function"):
                name = _named(declarator)
                return ("method" if in_container else "function", name, declarator)
            if value.type == "class":
                return ("class", _named(declarator), declarator)
        return None
    return None


def resolve_typescript(node: Node, in_container: bool):
    js = resolve_javascript(node, in_container)
    if js is not None:
        return js
    inner = node
    if node.type == "export_statement":
        for c in node.named_children:
            if c.type in ("interface_declaration", "type_alias_declaration",
                          "enum_declaration", "abstract_class_declaration"):
                inner = c
                break
    if inner.type == "interface_declaration":
        return ("interface", _named(inner), inner)
    if inner.type == "type_alias_declaration":
        return ("type_alias", _named(inner), inner)
    if inner.type == "enum_declaration":
        return ("enum", _named(inner), inner)
    if inner.type == "abstract_class_declaration":
        return ("class", _named(inner), inner)
    return None


def resolve_go(node: Node, in_container: bool):
    if node.type == "function_declaration":
        return ("function", _named(node), node)
    if node.type == "method_declaration":
        return ("method", _named(node), node)
    if node.type == "type_declaration":
        # type Foo struct { ... } / type Foo interface { ... }
        for spec in node.named_children:
            if spec.type == "type_spec":
                name = _named(spec)
                # Inspect the type field to label kind precisely
                t = spec.child_by_field_name("type")
                kind = "type"
                if t is not None:
                    if t.type == "struct_type":
                        kind = "struct"
                    elif t.type == "interface_type":
                        kind = "interface"
                    else:
                        kind = "type_alias"
                return (kind, name, node)
    return None


def resolve_java(node: Node, in_container: bool):
    if node.type == "class_declaration":
        return ("class", _named(node), node)
    if node.type == "interface_declaration":
        return ("interface", _named(node), node)
    if node.type == "enum_declaration":
        return ("enum", _named(node), node)
    if node.type == "record_declaration":
        return ("record", _named(node), node)
    if node.type == "method_declaration":
        return ("method", _named(node), node)
    if node.type == "constructor_declaration":
        return ("constructor", _named(node), node)
    return None


def resolve_rust(node: Node, in_container: bool):
    if node.type == "function_item":
        return ("method" if in_container else "function", _named(node), node)
    if node.type == "struct_item":
        return ("struct", _named(node), node)
    if node.type == "enum_item":
        return ("enum", _named(node), node)
    if node.type == "trait_item":
        return ("trait", _named(node), node)
    if node.type == "impl_item":
        # impl blocks don't have a `name` field; use the type they implement for
        type_node = node.child_by_field_name("type")
        name = type_node.text.decode("utf8", errors="replace") if type_node else None
        return ("impl", name, node)
    if node.type == "mod_item":
        return ("module", _named(node), node)
    if node.type == "const_item":
        return ("constant", _named(node), node)
    if node.type == "static_item":
        return ("static", _named(node), node)
    if node.type == "type_item":
        return ("type_alias", _named(node), node)
    return None


def resolve_c(node: Node, in_container: bool):
    if node.type == "function_definition":
        # name is nested inside the declarator
        declarator = node.child_by_field_name("declarator")
        name = _extract_c_function_name(declarator) if declarator else None
        return ("function", name, node)
    if node.type == "struct_specifier":
        return ("struct", _named(node), node)
    if node.type == "union_specifier":
        return ("union", _named(node), node)
    if node.type == "enum_specifier":
        return ("enum", _named(node), node)
    return None


def resolve_cpp(node: Node, in_container: bool):
    c_result = resolve_c(node, in_container)
    if c_result is not None:
        # In C++ a function_definition inside a class body is a method
        if c_result[0] == "function" and in_container:
            return ("method",) + c_result[1:]
        return c_result
    if node.type == "class_specifier":
        return ("class", _named(node), node)
    if node.type == "namespace_definition":
        return ("namespace", _named(node), node)
    if node.type == "template_declaration":
        # Unwrap and resolve the templated thing
        for c in node.named_children:
            inner = resolve_cpp(c, in_container)
            if inner is not None:
                return inner
    return None


def _extract_c_function_name(declarator: Node) -> Optional[str]:
    """Walk through C declarator nesting (pointer_declarator, function_declarator) to find the identifier."""
    n = declarator
    while n is not None:
        if n.type in ("identifier", "field_identifier", "qualified_identifier"):
            return n.text.decode("utf8", errors="replace")
        # Descend through common wrappers
        inner = n.child_by_field_name("declarator")
        if inner is None:
            # Try first named child as a fallback
            for c in n.named_children:
                if c.type in ("identifier", "field_identifier", "qualified_identifier"):
                    return c.text.decode("utf8", errors="replace")
            return None
        n = inner
    return None


# ---------------------------------------------------------------------------
# Language config
# ---------------------------------------------------------------------------

LANG_CONFIG = {
    "python": {
        "extensions": (".py", ".pyi"),
        "language": lambda: Language(tree_sitter_python.language()),
        "block_types": {"function_definition", "class_definition", "decorated_definition"},
        "import_types": {"import_statement", "import_from_statement", "future_import_statement"},
        "comment_types": {"comment"},
        "descend_kinds": {"class"},  # which resolved kinds should we recurse into
        "body_field": "body",
        "resolve": resolve_python,
    },
    "javascript": {
        "extensions": (".js", ".jsx", ".mjs", ".cjs"),
        "language": lambda: Language(tree_sitter_javascript.language()),
        "block_types": {"function_declaration", "generator_function_declaration",
                        "class_declaration", "method_definition", "lexical_declaration",
                        "variable_declaration", "export_statement"},
        "import_types": {"import_statement"},
        "comment_types": {"comment"},
        "descend_kinds": {"class"},
        "body_field": "body",
        "resolve": resolve_javascript,
    },
    "typescript": {
        "extensions": (".ts",),
        "language": lambda: Language(tree_sitter_typescript.language_typescript()),
        "block_types": {"function_declaration", "generator_function_declaration",
                        "class_declaration", "abstract_class_declaration", "method_definition",
                        "lexical_declaration", "variable_declaration", "export_statement",
                        "interface_declaration", "type_alias_declaration", "enum_declaration"},
        "import_types": {"import_statement"},
        "comment_types": {"comment"},
        "descend_kinds": {"class", "interface"},
        "body_field": "body",
        "resolve": resolve_typescript,
    },
    "tsx": {
        "extensions": (".tsx",),
        "language": lambda: Language(tree_sitter_typescript.language_tsx()),
        "block_types": {"function_declaration", "generator_function_declaration",
                        "class_declaration", "abstract_class_declaration", "method_definition",
                        "lexical_declaration", "variable_declaration", "export_statement",
                        "interface_declaration", "type_alias_declaration", "enum_declaration"},
        "import_types": {"import_statement"},
        "comment_types": {"comment"},
        "descend_kinds": {"class", "interface"},
        "body_field": "body",
        "resolve": resolve_typescript,
    },
    "go": {
        "extensions": (".go",),
        "language": lambda: Language(tree_sitter_go.language()),
        "block_types": {"function_declaration", "method_declaration", "type_declaration"},
        "import_types": {"import_declaration"},
        "comment_types": {"comment"},
        "descend_kinds": set(),  # Go has no nested defs to recurse into
        "body_field": "body",
        "resolve": resolve_go,
    },
    "java": {
        "extensions": (".java",),
        "language": lambda: Language(tree_sitter_java.language()),
        "block_types": {"class_declaration", "interface_declaration", "enum_declaration",
                        "record_declaration", "method_declaration", "constructor_declaration"},
        "import_types": {"import_declaration"},
        "comment_types": {"line_comment", "block_comment"},
        "descend_kinds": {"class", "interface", "enum", "record"},
        "body_field": "body",
        "resolve": resolve_java,
    },
    "rust": {
        "extensions": (".rs",),
        "language": lambda: Language(tree_sitter_rust.language()),
        "block_types": {"function_item", "struct_item", "enum_item", "trait_item",
                        "impl_item", "mod_item", "const_item", "static_item", "type_item"},
        "import_types": {"use_declaration"},
        "comment_types": {"line_comment", "block_comment"},
        "descend_kinds": {"impl", "trait", "module"},
        "body_field": "body",
        "resolve": resolve_rust,
    },
    "c": {
        "extensions": (".c", ".h"),
        "language": lambda: Language(tree_sitter_c.language()),
        "block_types": {"function_definition", "struct_specifier", "union_specifier",
                        "enum_specifier"},
        "import_types": {"preproc_include"},
        "comment_types": {"comment"},
        "descend_kinds": set(),
        "body_field": "body",
        "resolve": resolve_c,
    },
    "cpp": {
        "extensions": (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"),
        "language": lambda: Language(tree_sitter_cpp.language()),
        "block_types": {"function_definition", "struct_specifier", "union_specifier",
                        "enum_specifier", "class_specifier", "namespace_definition",
                        "template_declaration"},
        "import_types": {"preproc_include"},
        "comment_types": {"comment"},
        "descend_kinds": {"class", "struct", "namespace"},
        "body_field": "body",
        "resolve": resolve_cpp,
    },
}


def detect_language(path: str) -> Optional[str]:
    ext = os.path.splitext(path)[1].lower()
    for lang_name, cfg in LANG_CONFIG.items():
        if ext in cfg["extensions"]:
            return lang_name
    return None


# ---------------------------------------------------------------------------
# Block extraction
# ---------------------------------------------------------------------------

# How many blank lines may sit between a leading comment and the block it
# attaches to. Comments separated by more than this become standalone blocks.
COMMENT_ATTACH_MAX_GAP = 1


def _slice_text(source: bytes, start_byte: int, end_byte: int) -> str:
    return source[start_byte:end_byte].decode("utf8", errors="replace")


def _gather_leading_comments(node: Node, comment_types: set[str]) -> tuple[int, int]:
    """Walk backward across prev_named_sibling collecting attached comments.

    Returns (extended_start_byte, extended_start_row). Comments separated from
    the block by more than COMMENT_ATTACH_MAX_GAP blank lines are NOT included.
    """
    earliest_byte = node.start_byte
    earliest_row = node.start_point[0]
    sib = node.prev_named_sibling
    while sib is not None and sib.type in comment_types:
        gap = earliest_row - sib.end_point[0]
        if gap > COMMENT_ATTACH_MAX_GAP:
            break
        earliest_byte = sib.start_byte
        earliest_row = sib.start_point[0]
        sib = sib.prev_named_sibling
    return earliest_byte, earliest_row


def _body_node(inner: Node, body_field: str) -> Optional[Node]:
    """Find the body of a definition node so we can recurse into it for nested blocks."""
    body = inner.child_by_field_name(body_field)
    if body is not None:
        return body
    # Some grammars use different field names — try common alternatives
    for alt in ("declaration_list", "field_declaration_list", "class_body", "block"):
        for c in inner.named_children:
            if c.type == alt:
                return c
    return None


# ---------------------------------------------------------------------------
# Production safety limits
# ---------------------------------------------------------------------------

# Files larger than this are skipped; the caller will fall back to text/markdown parsing.
MAX_FILE_SIZE_BYTES: int = 5 * 1024 * 1024  # 5 MB

# cAST chunking budget: maximum non-whitespace characters per semantic block before
# the VectorStore applies a sliding-window split (measured in non-ws chars because
# code density varies enormously across languages — this normalises the budget).
MAX_BLOCK_NONWS_CHARS: int = 2000


def _count_nonws_chars(text: str) -> int:
    """Count non-whitespace characters — the size metric used by the cAST algorithm."""
    return sum(1 for c in text if not c.isspace())


def decode_source(raw: bytes) -> bytes:
    """Return *raw* as a UTF-8-clean byte string.

    Tries UTF-8 first (covers >99% of modern code), then latin-1 as a
    fallback — latin-1 decodes every byte, so it never raises, and the
    result is re-encoded to UTF-8 so tree-sitter always receives valid UTF-8.
    """
    try:
        raw.decode("utf-8")
        return raw          # already valid UTF-8, return as-is
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace").encode("utf-8", errors="replace")


def extract_blocks(source: bytes, language: str, merge_gap: int = 1) -> list[Block]:
    """Parse *source* and return a list of semantic blocks.

    Args:
        source: raw source bytes (any encoding; non-UTF-8 is normalised via
            :func:`decode_source` so tree-sitter always receives valid UTF-8).
        language: key into :data:`LANG_CONFIG` (e.g. ``"python"``).
        merge_gap: maximum line-gap that still counts as "adjacent" when merging
            small statement/comment blocks.  ``1`` = no blank line allowed;
            ``2`` = one blank line allowed; ``0`` = merge disabled.

    Returns ``[]`` if *source* exceeds :data:`MAX_FILE_SIZE_BYTES`.
    """
    if len(source) > MAX_FILE_SIZE_BYTES:
        return []

    # Normalise encoding before handing to tree-sitter
    source = decode_source(source)

    cfg = LANG_CONFIG[language]
    lang_obj = cfg["language"]()
    parser = Parser(lang_obj)
    tree = parser.parse(source)
    blocks: list[Block] = []
    _walk_container(tree.root_node, source, language, cfg, blocks, path=[], in_container=False)
    # Order by start_line so the output reads top-to-bottom. Standalone comments
    # are emitted in a second pass, so without this they'd land out of order.
    # Tie-break on len(path) so a class block precedes its own methods.
    blocks.sort(key=lambda b: (b.start_line, len(b.path)))
    if merge_gap >= 1:
        blocks = _merge_adjacent_small_blocks(blocks, source, max_gap=merge_gap)
    return blocks


# Which block kinds are considered "small chunks" eligible for merging into
# their adjacent neighbours. Functions, classes, etc. are NOT merged — each is
# a distinct semantic unit even when short.
MERGEABLE_KINDS = {"statement", "comment"}


def _merge_adjacent_small_blocks(blocks: list[Block], source: bytes,
                                  max_gap: int = 1) -> list[Block]:
    """Coalesce runs of adjacent statement/comment blocks at the same nesting level.

    Two consecutive blocks merge when:
        - both have a `type` in MERGEABLE_KINDS
        - they sit at the same `path` (same class/module/etc.)
        - (next.start_line - prev.end_line) <= max_gap
          i.e. max_gap=1 allows no blank lines between; max_gap=2 allows one.

    The merged block's type is "statement" if any input was a statement, else
    "comment". The merged text is sliced fresh from `source` so it includes any
    blank lines that originally separated the pieces.
    """
    if not blocks:
        return blocks
    out: list[Block] = [blocks[0]]
    for b in blocks[1:]:
        prev = out[-1]
        can_merge = (
            prev.type in MERGEABLE_KINDS
            and b.type in MERGEABLE_KINDS
            and prev.path == b.path
            and (b.start_line - prev.end_line) <= max_gap
        )
        if can_merge:
            merged_type = "statement" if "statement" in (prev.type, b.type) else "comment"
            out[-1] = Block(
                type=merged_type,
                name=None,
                language=prev.language,
                start_line=prev.start_line,
                end_line=b.end_line,
                start_byte=prev.start_byte,
                end_byte=b.end_byte,
                path=prev.path,
                parent=prev.parent,
                text=_slice_text(source, prev.start_byte, b.end_byte),
            )
        else:
            out.append(b)
    return out


def _walk_container(container: Node, source: bytes, language: str, cfg: dict,
                    out: list[Block], path: list[dict], in_container: bool) -> None:
    """Iterate the children of a container, emitting blocks and recursing into class bodies."""
    block_types = cfg["block_types"]
    import_types = cfg["import_types"]
    comment_types = cfg["comment_types"]
    descend_kinds = cfg["descend_kinds"]
    body_field = cfg["body_field"]
    resolve = cfg["resolve"]

    # State for merging consecutive imports
    import_run: list[Node] = []

    def flush_imports():
        if not import_run:
            return
        first = import_run[0]
        last = import_run[-1]
        ext_start_byte, ext_start_row = _gather_leading_comments(first, comment_types)
        out.append(Block(
            type="imports",
            name=None,
            language=language,
            start_line=ext_start_row + 1,
            end_line=last.end_point[0] + 1,
            start_byte=ext_start_byte,
            end_byte=last.end_byte,
            path=list(path),
            parent="::".join(p["name"] for p in path if p.get("name")) or None,
            text=_slice_text(source, ext_start_byte, last.end_byte),
        ))
        import_run.clear()

    for child in container.named_children:
        # Comments are absorbed via _gather_leading_comments. We only handle them
        # explicitly if they end up unattached — see below.
        if child.type in comment_types:
            continue

        if child.type in import_types:
            import_run.append(child)
            continue

        # Any non-import breaks the import run
        flush_imports()

        # Is this a block?
        resolved = resolve(child, in_container) if child.type in block_types else None

        if resolved is not None:
            kind, name, inner = resolved
            ext_start_byte, ext_start_row = _gather_leading_comments(child, comment_types)
            block = Block(
                type=kind,
                name=name,
                language=language,
                start_line=ext_start_row + 1,
                end_line=child.end_point[0] + 1,
                start_byte=ext_start_byte,
                end_byte=child.end_byte,
                path=list(path),
                parent="::".join(p["name"] for p in path if p.get("name")) or None,
                text=_slice_text(source, ext_start_byte, child.end_byte),
            )
            out.append(block)

            # Recurse into bodies of class-like containers to find nested blocks
            if kind in descend_kinds:
                body = _body_node(inner, body_field)
                if body is not None:
                    nested_path = path + [{"type": kind, "name": name}]
                    _walk_container(body, source, language, cfg, out,
                                    path=nested_path, in_container=True)
            continue

        # Not a block, not an import — emit as a standalone statement, but only
        # at the top level. Inside class bodies, non-method members are already
        # captured in the parent class block's text.
        if not in_container:
            ext_start_byte, ext_start_row = _gather_leading_comments(child, comment_types)
            out.append(Block(
                type="statement",
                name=None,
                language=language,
                start_line=ext_start_row + 1,
                end_line=child.end_point[0] + 1,
                start_byte=ext_start_byte,
                end_byte=child.end_byte,
                path=list(path),
                parent="::".join(p["name"] for p in path if p.get("name")) or None,
                text=_slice_text(source, ext_start_byte, child.end_byte),
            ))

    flush_imports()

    # Emit standalone comments that weren't attached to anything (only at top level)
    if not in_container:
        _emit_standalone_comments(container, source, language, cfg, out, path)


def _emit_standalone_comments(container: Node, source: bytes, language: str, cfg: dict,
                              out: list[Block], path: list[dict]) -> None:
    """Find comments that didn't get attached to a following block and emit them."""
    comment_types = cfg["comment_types"]
    # Collect (start_line, end_line) ranges of every emitted block at this level
    covered = set()
    for b in out:
        if b.path == path:
            for line in range(b.start_line, b.end_line + 1):
                covered.add(line)

    # Walk children and emit comments not yet covered
    current_run: list[Node] = []

    def flush_run():
        if not current_run:
            return
        first, last = current_run[0], current_run[-1]
        out.append(Block(
            type="comment",
            name=None,
            language=language,
            start_line=first.start_point[0] + 1,
            end_line=last.end_point[0] + 1,
            start_byte=first.start_byte,
            end_byte=last.end_byte,
            path=list(path),
            parent="::".join(p["name"] for p in path if p.get("name")) or None,
            text=_slice_text(source, first.start_byte, last.end_byte),
        ))
        current_run.clear()

    for child in container.named_children:
        if child.type in comment_types:
            line = child.start_point[0] + 1
            if line in covered:
                # Already part of an emitted block (attached to a function/class)
                continue
            current_run.append(child)
        else:
            flush_run()
    flush_run()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Chunk a source file into semantic blocks.")
    p.add_argument("path", help="Path to the source file")
    p.add_argument("--language", help="Override language auto-detection",
                   choices=sorted(LANG_CONFIG.keys()))
    p.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    p.add_argument("--no-text", action="store_true",
                   help="Omit the `text` field from each block (smaller output)")
    p.add_argument("--merge-gap", type=int, default=1, metavar="N",
                   help="Merge adjacent statement/comment blocks when separated "
                        "by ≤ N lines. 1 (default) = strictly consecutive; "
                        "2 = allow one blank line; 0 = disable merging.")
    args = p.parse_args(argv)

    language = args.language or detect_language(args.path)
    if language is None:
        print(f"error: could not detect language for {args.path!r}; pass --language",
              file=sys.stderr)
        return 2

    with open(args.path, "rb") as f:
        source = f.read()

    blocks = extract_blocks(source, language, merge_gap=args.merge_gap)

    serial = []
    for b in blocks:
        d = asdict(b)
        if args.no_text:
            d.pop("text", None)
        serial.append(d)

    indent = 2 if args.pretty else None
    print(json.dumps(serial, indent=indent, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())