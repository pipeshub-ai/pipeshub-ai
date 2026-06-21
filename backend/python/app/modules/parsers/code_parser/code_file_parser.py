"""
CodeFileParser: converts a source-code file into a BlocksContainer for indexing.

Mapping from tree-sitter semantic blocks (parser.py) to the indexing block model:
  - Container kinds (class, impl, trait, interface, struct, namespace, module, enum)
    at the top level  → BlockGroup (GroupType.CODE, GroupSubType.CODE_CLASS)
  - All other blocks (function, method, imports, statement, comment, type_alias, …)
    → Block (BlockType.CODE)
  - Methods / nested blocks whose path leads back to a top-level container are
    attached to that container's BlockGroup via parent_index and BlockGroupChildren.

Phase 2.1/2.2 additions:
  - Signature extraction from the first non-decorator, non-blank code line.
  - Docstring extraction (Python triple-quotes, JSDoc /**, Rust ///).
  - Decorator extraction (Python @, Java/TS @annotation).
  - content_hash on every Block and BlockGroup for reconciliation support.
  - File-level summary Block (BlockType.RECORD_SUMMARY) listing all top-level symbols.
  - Identifier subtokenization stored alongside code text to improve BM25 sparse recall.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Optional

from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    CitationMetadata,
    CodeMetadata,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.models.blocks import BlockType as BT
from app.modules.parsers.code_parser.parser import (
    LANG_CONFIG,
    Block as ParsedBlock,
    detect_language,
    extract_blocks,
)

# Kinds that become BlockGroups (structural containers)
CONTAINER_KINDS: frozenset[str] = frozenset(
    {"class", "impl", "trait", "interface", "struct", "namespace", "module", "enum"}
)

# Kinds where signature/docstring extraction is meaningful
CALLABLE_KINDS: frozenset[str] = frozenset(
    {"function", "method", "constructor", "record"}
)

# ── Regex helpers for docstring / decorator extraction ───────────────────────

_PY_DOCSTRING_RE = re.compile(
    r'^\s*(?:def|class|async\s+def)[^\n]*\n\s*(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')',
    re.DOTALL,
)
_JSDOC_RE = re.compile(r'/\*\*(.*?)\*/', re.DOTALL)
_RUST_DOC_RE = re.compile(r'^(?:\s*///[^\n]*\n)+', re.MULTILINE)
_PY_DECORATOR_RE = re.compile(r'^\s*@(\w[\w.]*)', re.MULTILINE)
_JAVA_TS_DECORATOR_RE = re.compile(r'^\s*@(\w+)', re.MULTILINE)

# ── Identifier subtokenisation ───────────────────────────────────────────────
# Split camelCase / PascalCase / snake_case identifiers so BM25 can match
# natural-language tokens against code symbol names.
# e.g. "getUserById" → "get user by id getUserById"

_CAMEL_SPLIT_RE = re.compile(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')


def _subtokenise(text: str) -> str:
    """Return *text* followed by a space-separated list of sub-tokens extracted
    from identifiers.  The original text is preserved so exact-match queries
    still work; the sub-tokens improve natural-language recall."""
    tokens: list[str] = []
    for word in re.findall(r'[A-Za-z_][A-Za-z0-9_]*', text):
        parts = _CAMEL_SPLIT_RE.sub(' ', word).lower().split('_')
        for p in parts:
            p = p.strip()
            if p and p not in tokens:
                tokens.append(p)
    if not tokens:
        return text
    return text + "\n\n" + " ".join(tokens)


# ── Content hash helper ──────────────────────────────────────────────────────

def _content_hash(text: str) -> str:
    """Stable SHA-256 hex digest of the block text (used for reconciliation)."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


# ── Docstring / signature extraction ────────────────────────────────────────

def _extract_signature(text: str) -> str:
    """Return the first meaningful definition line from a code block.

    Skips decorator lines and blank lines; returns the first non-empty,
    non-decorator line (e.g. ``def foo(bar: int) -> str:``).
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("@"):
            return stripped[:300]
    return ""


def _extract_docstring(text: str, language: str) -> str:
    """Extract the first docstring / doc-comment from a code block, if any.

    Supports Python triple-quote strings, JSDoc ``/** … */`` comments, and
    Rust ``///`` doc-comments.  Returns at most 500 characters.
    """
    if language == "python":
        m = _PY_DOCSTRING_RE.search(text)
        if m:
            raw = (m.group(1) or m.group(2) or "").strip()
            return raw[:500]
    elif language in ("javascript", "typescript", "tsx", "java"):
        m = _JSDOC_RE.search(text)
        if m:
            raw = m.group(1).strip()
            # Strip leading * from each line
            cleaned = "\n".join(
                line.lstrip().lstrip("*").lstrip() for line in raw.splitlines()
            )
            return cleaned[:500]
    elif language == "rust":
        m = _RUST_DOC_RE.search(text)
        if m:
            raw = "\n".join(
                line.strip().lstrip("///").lstrip()
                for line in m.group(0).splitlines()
            )
            return raw.strip()[:500]
    return ""


def _extract_decorators(text: str, language: str) -> list[str]:
    """Return the list of decorator/annotation names found in the block."""
    if language == "python":
        return _PY_DECORATOR_RE.findall(text)
    if language in ("java", "typescript", "tsx", "javascript"):
        return _JAVA_TS_DECORATOR_RE.findall(text)
    return []


class CodeFileParser:
    """Parse a source-code file into a BlocksContainer.

    Only top-level containers (no ancestors in their ``path``) become
    BlockGroups.  Nested containers and all leaf blocks become Block objects.

    In addition to the structural mapping, the parser:
    - Sets ``content_hash`` on every Block and BlockGroup for reconciliation.
    - Populates ``CodeMetadata`` with ``language``, ``signature``,
      ``docstring``, and ``decorators`` for contextual embedding.
    - Appends a file-level summary Block (``BlockType.RECORD_SUMMARY``)
      listing all top-level symbols.
    - Subtokenises identifier names in the embedded text (stored in a
      separate ``subtokens`` key inside ``block.data``) to improve BM25
      sparse-vector recall for natural-language queries.
    """

    def parse(
        self,
        source: bytes,
        file_name: str,
        language: Optional[str] = None,
    ) -> BlocksContainer:
        if language is None:
            language = detect_language(file_name)
        if language is None or language not in LANG_CONFIG:
            return BlocksContainer(blocks=[], block_groups=[])

        parsed_blocks = extract_blocks(source, language)
        if not parsed_blocks:
            return BlocksContainer(blocks=[], block_groups=[])

        return self._build_container(parsed_blocks, language, file_name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_container(
        self,
        parsed_blocks: list[ParsedBlock],
        language: str,
        file_name: str,
    ) -> BlocksContainer:
        block_groups: list[BlockGroup] = []
        blocks: list[Block] = []

        # name → block-group index for top-level containers
        top_level_container_index: dict[str, int] = {}
        # Collect top-level symbol names for the file summary
        top_level_symbols: list[str] = []

        # ── Pass 1: create a BlockGroup for every top-level container ──
        for pb in parsed_blocks:
            if pb.type in CONTAINER_KINDS and not pb.path:
                bg_idx = len(block_groups)
                if pb.name:
                    top_level_container_index[pb.name] = bg_idx
                    top_level_symbols.append(f"{pb.type}:{pb.name}")

                docstring = _extract_docstring(pb.text, language)
                signature = _extract_signature(pb.text)
                decorators = _extract_decorators(pb.text, language)

                bg = BlockGroup(
                    index=bg_idx,
                    name=pb.name,
                    type=GroupType.CODE,
                    sub_type=GroupSubType.CODE_CLASS,
                    code_metadata=CodeMetadata(
                        language=language,
                        signature=signature or None,
                        docstring=docstring or None,
                        decorators=decorators or None,
                    ),
                    citation_metadata=CitationMetadata(line_number=pb.start_line),
                    content_hash=_content_hash(pb.text),
                    data={
                        "text": pb.text,
                        "subtokens": _subtokenise(pb.text),
                        "kind": pb.type,
                        "start_line": pb.start_line,
                        "end_line": pb.end_line,
                    },
                )
                block_groups.append(bg)

        # ── Pass 2: create a Block for every non-top-level-container block ──
        for pb in parsed_blocks:
            if pb.type in CONTAINER_KINDS and not pb.path:
                continue  # already handled as a BlockGroup

            parent_group_idx = self._resolve_parent_group(pb, top_level_container_index)

            # Collect top-level functions/imports for summary
            if not pb.path and pb.type in ("function", "imports") and pb.name:
                top_level_symbols.append(f"{pb.type}:{pb.name}")

            docstring = (
                _extract_docstring(pb.text, language) if pb.type in CALLABLE_KINDS else ""
            )
            signature = (
                _extract_signature(pb.text) if pb.type in CALLABLE_KINDS else ""
            )
            decorators = (
                _extract_decorators(pb.text, language) if pb.type in CALLABLE_KINDS else []
            )

            b_idx = len(blocks)
            blocks.append(
                Block(
                    index=b_idx,
                    type=BT.CODE,
                    name=pb.name,
                    format=DataFormat.CODE,
                    parent_index=parent_group_idx,
                    citation_metadata=CitationMetadata(line_number=pb.start_line),
                    code_metadata=CodeMetadata(
                        language=language,
                        signature=signature or None,
                        docstring=docstring or None,
                        decorators=decorators or None,
                    ),
                    content_hash=_content_hash(pb.text),
                    data={
                        "text": pb.text,
                        "subtokens": _subtokenise(pb.text),
                        "kind": pb.type,
                        "start_line": pb.start_line,
                        "end_line": pb.end_line,
                    },
                )
            )

            if parent_group_idx is not None:
                bg = block_groups[parent_group_idx]
                if bg.children is None:
                    bg.children = BlockGroupChildren()
                bg.children.add_block_index(b_idx)

        # ── Pass 3: file-level summary block ────────────────────────────
        # A synthetic RECORD_SUMMARY block listing all top-level symbols.
        # Stored with isRecordSummary metadata so the VectorStore embeds it
        # separately and `where is X defined?` queries surface the whole file.
        if top_level_symbols:
            file_base = os.path.basename(file_name)
            summary_lines = [f"File: {file_base}", f"Language: {language}", ""]
            for sym in top_level_symbols:
                kind_sym, _, name_sym = sym.partition(":")
                summary_lines.append(f"  {kind_sym}: {name_sym}")
            summary_text = "\n".join(summary_lines)

            summary_idx = len(blocks)
            blocks.append(
                Block(
                    index=summary_idx,
                    type=BT.RECORD_SUMMARY,
                    name=f"{file_base} symbols",
                    format=DataFormat.TXT,
                    content_hash=_content_hash(summary_text),
                    data={
                        "text": summary_text,
                        "kind": "file_summary",
                        "symbols": top_level_symbols,
                    },
                )
            )

        return BlocksContainer(blocks=blocks, block_groups=block_groups)

    @staticmethod
    def _resolve_parent_group(
        pb: ParsedBlock,
        top_level_container_index: dict[str, int],
    ) -> Optional[int]:
        """Return the BlockGroup index of the immediate top-level ancestor, if any."""
        if not pb.path:
            return None
        # pb.path is ordered outermost → innermost; the first entry is the
        # top-level container.
        outermost_name = pb.path[0].get("name")
        return top_level_container_index.get(outermost_name) if outermost_name else None
