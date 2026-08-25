"""CodeFileParser -- source bytes to a BlocksContainer.

Satisfies the ``IParser`` protocol.
"""
from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Any

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
from app.modules.parsers.code_parser.engine import parse_code
from app.modules.parsers.code_parser.lang_config import (
    SUPPORTED_CODE_EXTENSIONS,
    config_for_language,
    detect_language,
)
from app.services.parsing.interface import ParseResult, ParserProvider

if TYPE_CHECKING:
    from app.modules.parsers.code_parser.models import ParsedFile, ParsedSymbol

__all__ = ["CodeFileParser", "qualified_name_for"]

_MAX_SIGNATURE_CHARS = 300
_MAX_DOCSTRING_CHARS = 500

_PY_DOCSTRING_RE = re.compile(r'^\s*(?:[rubRUB]{0,2})("""|\'\'\')(.*?)\1', re.DOTALL)
# The body-opening colon in a def/class: terminal on its line (only
# optional whitespace or a comment may follow before the newline).
_PY_SIG_COLON_RE = re.compile(r":[^\S\n]*(?:#[^\n]*)?\n")
# Anchored: a doc comment introduces what follows it. An unanchored search
# picks up a `/** ... */` buried in a body, and grammars that nest a trailing
# comment inside the previous declaration (Groovy) would hand every member the
# next member's documentation.
_BLOCK_DOC_RE = re.compile(r"\A\s*/\*\*(.*?)\*/", re.DOTALL)
_SUBTOKEN_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|\d+")

# Lines that precede a signature rather than being one: comments, decorators,
# annotations and the attribute syntaxes of Rust, C# and Swift.
_NON_SIGNATURE_PREFIXES = ("@", "#", "//", "/*", "*", "--", "[")


def qualified_name_for(kind: str, name: str | None, parent_chain: tuple[str, ...] = (),
                       *, start_line: int | None = None, end_line: int | None = None) -> str:
    """``"{kind}:{dotted.scope}"`` -- the human-readable identity of a symbol.

    Unnamed spans are addressed by line range instead (``imports:L1-5``).
    """
    if name:
        return f"{kind}:{'.'.join([*parent_chain, name])}"
    if start_line is not None:
        span = f"L{start_line}" if end_line in (None, start_line) else f"L{start_line}-{end_line}"
        return f"{kind}:{span}"
    return f"{kind}:anonymous"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _subtokenise(text: str) -> str:
    """camelCase / snake_case splits, appended to improve BM25 recall."""
    tokens = {t.lower() for t in _SUBTOKEN_RE.findall(text) if len(t) > 2}
    return " ".join(sorted(tokens))


def _extract_signature(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(_NON_SIGNATURE_PREFIXES):
            continue
        return stripped[:_MAX_SIGNATURE_CHARS]
    return None


def _leading_line_comment(text: str, prefixes: tuple[str, ...]) -> str | None:
    """The run of comment lines a definition opens with, in `//`-style languages."""
    collected: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            if collected:
                break
            continue
        prefix = next((p for p in prefixes if stripped.startswith(p)), None)
        if prefix is None:
            break
        collected.append(stripped[len(prefix):].strip())
    return " ".join(p for p in collected if p).strip() or None


def _extract_docstring(text: str, language: str) -> str | None:
    cfg = config_for_language(language)
    style = cfg.docstring_style if cfg else "none"

    if style == "python":
        sig_end = _PY_SIG_COLON_RE.search(text)
        body = text[sig_end.end():] if sig_end else text
        match = _PY_DOCSTRING_RE.search(body)
        if match:
            return match.group(2).strip()[:_MAX_DOCSTRING_CHARS] or None
        return None

    if style == "block_comment":
        match = _BLOCK_DOC_RE.search(text)
        if match:
            cleaned = " ".join(
                line.strip().lstrip("*").strip() for line in match.group(1).splitlines()
            ).strip()
            return cleaned[:_MAX_DOCSTRING_CHARS] or None
        return None

    if style == "line_comment" and cfg:
        doc = _leading_line_comment(text, cfg.doc_line_prefixes)
        return doc[:_MAX_DOCSTRING_CHARS] if doc else None

    return None


class CodeFileParser:
    """Parses source files into structured code blocks."""

    def supported_formats(self) -> list[str]:
        return sorted(SUPPORTED_CODE_EXTENSIONS)

    async def parse(
        self,
        content: bytes,
        record_name: str,
        config: dict[str, Any] | None = None,
    ) -> ParseResult:
        cfg = config or {}
        file_path = cfg.get("file_path") or record_name
        language = cfg.get("language") or detect_language(record_name) or detect_language(file_path)
        container = self.parse_to_blocks(content, record_name, file_path, language)
        return ParseResult(
            block_container=container or BlocksContainer(),
            provider_used=ParserProvider.DEFAULT,
            metadata={"language": language, "file_path": file_path, "skipped": container is None},
        )

    def parse_to_blocks(
        self,
        content: bytes,
        record_name: str,
        file_path: str | None = None,
        language: str | None = None,
    ) -> BlocksContainer | None:
        """Return blocks, or ``None`` when the file was skipped (e.g. oversized).

        Callers receiving ``None`` should fall back to an alternative parser
        rather than treating the file as empty.
        """
        path = file_path or record_name
        lang = language or detect_language(record_name) or detect_language(path)
        if not lang:
            return BlocksContainer()

        parsed = parse_code(content, lang)
        if parsed.skipped_reason:
            return None
        if not parsed.symbols:
            return BlocksContainer()
        return self._to_container(parsed, path, record_name)

    # ------------------------------------------------------------------

    def _to_container(self, parsed: ParsedFile, file_path: str, record_name: str) -> BlocksContainer:
        symbols = parsed.symbols

        # Blocks and groups are numbered in separate spaces, so keep an
        # index -> (is_group, idx) map to wire parents and children.
        placement: dict[int, tuple[bool, int]] = {}
        groups: list[BlockGroup] = []
        blocks: list[Block] = []

        # Pass 1 -- every container that has members becomes a group, at any
        # depth. Groups nest through parent_index, so Outer.Inner keeps its level
        # instead of collapsing onto Outer.
        for i, sym in enumerate(symbols):
            if sym.is_container:
                placement[i] = (True, len(groups))
                groups.append(None)  # filled below, once parents are known

        for i, sym in enumerate(symbols):
            if i not in placement:
                continue
            _, gidx = placement[i]
            parent_group = self._nearest_group(sym.parent, symbols, placement)
            groups[gidx] = self._build_group(
                sym, gidx, parsed.language, parent_group
            )

        # Pass 2 -- everything else becomes a block parented to its nearest
        # enclosing group.
        for i, sym in enumerate(symbols):
            if i in placement:
                continue
            bidx = len(blocks)
            parent_group = self._nearest_group(sym.parent, symbols, placement)
            blocks.append(
                self._build_block(sym, bidx, parsed.language, parent_group)
            )
            placement[i] = (False, bidx)
            if parent_group is not None:
                groups[parent_group].children.add_block_index(bidx)

        for i in range(len(symbols)):
            is_group, gidx = placement[i]
            if not is_group:
                continue
            parent_group = groups[gidx].parent_index
            if parent_group is not None:
                groups[parent_group].children.add_block_group_index(gidx)

        blocks.append(
            self._build_summary_block(parsed, symbols, len(blocks), record_name, file_path)
        )

        return BlocksContainer(blocks=blocks, block_groups=groups)

    @staticmethod
    def _nearest_group(parent: int | None, symbols: list[ParsedSymbol],
                       placement: dict[int, tuple[bool, int]]) -> int | None:
        """Walk up to the closest ancestor that became a group.

        A function nested inside a method has no group of its own, so it attaches
        to the enclosing class.
        """
        while parent is not None:
            slot = placement.get(parent)
            if slot and slot[0]:
                return slot[1]
            parent = symbols[parent].parent
        return None

    def _build_group(self, sym: ParsedSymbol, index: int, language: str,
                     parent_group: int | None = None) -> BlockGroup:
        # A container keeps its whole body. Its children are subsets of it, which
        # costs nothing in vectors: vectorstore only embeds table/view groups, so
        # a code group's text never reaches Qdrant.
        return BlockGroup(
            index=index,
            name=sym.name,
            type=GroupType.CODE,
            sub_type=GroupSubType.CODE_CLASS,
            format=DataFormat.CODE,
            parent_index=parent_group,
            data={
                "text": sym.text,
                "kind": sym.kind,
                "start_line": sym.start_line,
                "end_line": sym.end_line,
            },
            content_hash=_content_hash(sym.text),
            children=BlockGroupChildren(),
            code_metadata=self._code_metadata(sym, language),
        )

    def _build_block(self, sym: ParsedSymbol, index: int,
                     language: str, parent_group: int | None) -> Block:
        text = sym.text
        return Block(
            index=index,
            type=BlockType.CODE,
            name=sym.name,
            format=DataFormat.CODE,
            parent_index=parent_group,
            citation_metadata=CitationMetadata(line_number=sym.start_line),
            content_hash=_content_hash(text),
            data={
                "text": text,
                "subtokens": _subtokenise(text),
                "kind": sym.kind,
                "start_line": sym.start_line,
                "end_line": sym.end_line,
            },
            code_metadata=self._code_metadata(sym, language),
        )

    def _code_metadata(self, sym: ParsedSymbol, language: str) -> CodeMetadata:
        return CodeMetadata(
            language=language,
            kind=sym.kind,
            signature=_extract_signature(sym.text) if sym.name else None,
            docstring=_extract_docstring(sym.text, language),
            decorators=list(sym.decorators) or None,
            qualified_name=qualified_name_for(
                sym.kind, sym.name, sym.parent_chain,
                start_line=sym.start_line, end_line=sym.end_line,
            ),
            start_line=sym.start_line,
            end_line=sym.end_line,
        )

    def _build_summary_block(self, parsed: ParsedFile, symbols: list[ParsedSymbol],
                             index: int, record_name: str, file_path: str) -> Block:
        top_level = [
            f"{s.kind}:{s.name}" for s in symbols if not s.parent_chain and s.name
        ]
        summary = f"{record_name} ({parsed.language}) — " + (
            ", ".join(top_level) if top_level else "no top-level symbols"
        )
        return Block(
            index=index,
            type=BlockType.RECORD_SUMMARY,
            name=record_name,
            format=DataFormat.TXT,
            content_hash=_content_hash(summary),
            data={
                "text": summary,
                "kind": "file_summary",
                "symbols": top_level,
                "file_path": file_path,
            },
            code_metadata=CodeMetadata(
                language=parsed.language,
                kind="file_summary",
            ),
        )
