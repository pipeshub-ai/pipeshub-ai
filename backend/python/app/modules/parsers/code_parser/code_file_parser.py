"""CodeFileParser -- source bytes to a BlocksContainer.

Satisfies the ``IParser`` protocol. Identity is the qualified name, which is
unique within a file; the repo-relative path still arrives through the ``config``
dict because it is stored on every block and used to address them.
"""
from __future__ import annotations

import asyncio
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
from app.modules.code_graph.version import PARSER_VERSION
from app.modules.parsers import parse_pool
from app.modules.parsers.code_parser.engine import parse_code
from app.modules.parsers.code_parser.lang_config import (
    SUPPORTED_CODE_EXTENSIONS,
    config_for_extension,
    config_for_language,
    detect_language,
)
from app.modules.parsers.code_parser.models import Relation
from app.services.parsing.interface import ParseResult, ParserProvider
from app.utils.env_config import env_int

if TYPE_CHECKING:
    from app.modules.parsers.code_parser.models import ParsedFile, ParsedSymbol

__all__ = ["CodeFileParser", "parse_code_to_blocks", "qualified_name_for"]

# A file past these is data or generator output that slipped past the name and
# content checks in ``routing``: every block is a graph node and an embedding,
# and a 2 MB file of one-line functions made 50,000 of each; the largest real
# file in PipesHub's own repo has about 3,200 spans. It is read as text instead.
# Repeated facts go first, which costs no edges (the edge builder keeps one per
# from, to and relation); the cap on distinct ones sits well above a large
# hand-written function's few hundred callees.
MAX_BLOCKS_PER_FILE = max(1, env_int("CODE_FILE_MAX_BLOCKS", 5000))
MAX_FACTS_PER_BLOCK = max(1, env_int("CODE_BLOCK_MAX_FACTS", 2000))

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
                       *, start_line: int | None = None, end_line: int | None = None,
                       content: str | None = None) -> str:
    """``"{kind}:{dotted.scope}"`` -- the human-readable identity of a symbol.

    An unnamed span is addressed by its nearest named scope and a hash of its
    whitespace-normalised text (``imports:@3f2a9c1d``, ``statements:Cart@…``):
    a line range changed whenever anything above it moved, so every edit
    re-keyed the file's imports and statements and churned their nodes (P-10).
    Without ``content``, by line range (``imports:L1-5``).
    """
    if name:
        return f"{kind}:{'.'.join([*parent_chain, name])}"
    if content is not None:
        digest = hashlib.sha1(" ".join(content.split()).encode("utf-8", errors="replace"),
                              usedforsecurity=False).hexdigest()[:8]
        return f"{kind}:{'.'.join(parent_chain)}@{digest}"
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


def parse_code_to_blocks(
    content: bytes,
    record_name: str,
    file_path: str | None = None,
    language: str | None = None,
) -> BlocksContainer | None:
    """``CodeFileParser.parse_to_blocks`` as a function a parse worker can be sent."""
    return CodeFileParser().parse_to_blocks(content, record_name, file_path, language)


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
        if not language:
            # The parsing service picked this parser by extension, and a name
            # like "build-script" carries none to detect a language from.
            extension_config = config_for_extension(cfg.get("extension") or "")
            language = extension_config.name if extension_config else None
        container = await self.parse_to_blocks_off_loop(content, record_name, file_path, language)
        return ParseResult(
            block_container=container or BlocksContainer(),
            provider_used=ParserProvider.DEFAULT,
            metadata={"language": language, "file_path": file_path, "skipped": container is None},
        )

    async def parse_to_blocks_off_loop(
        self,
        content: bytes,
        record_name: str,
        file_path: str | None = None,
        language: str | None = None,
    ) -> BlocksContainer | None:
        """``parse_to_blocks`` without holding the event loop.

        The tree-sitter walk is Python and holds the GIL, so a large file goes
        to a parse worker process and a small one to a thread.
        """
        if parse_pool.should_offload(len(content)):
            return await parse_pool.submit(
                parse_code_to_blocks, content, record_name, file_path, language,
                label=file_path or record_name, size=len(content),
            )
        return await asyncio.to_thread(
            self.parse_to_blocks, content, record_name, file_path, language
        )

    def parse_to_blocks(
        self,
        content: bytes,
        record_name: str,
        file_path: str | None = None,
        language: str | None = None,
    ) -> BlocksContainer | None:
        """Return blocks, or ``None`` when the file was skipped (oversized, or
        more than ``MAX_BLOCKS_PER_FILE`` definitions).

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
        if len(parsed.symbols) > MAX_BLOCKS_PER_FILE:
            return None
        return self._to_container(parsed, path, record_name)

    # ------------------------------------------------------------------

    def _qualified_names(self, symbols: list[ParsedSymbol]) -> list[str]:
        """Stable identity per symbol, unique within the file.

        A file may legitimately define the same name twice -- a conditional `def`
        in either branch of an `if`, or a redefinition -- and two blocks sharing
        an identity would hash to one `_key`, silently merging into a single node
        and taking every edge attached to either with it. Repeats are suffixed
        rather than allowed to collide.
        """
        used: dict[str, int] = {}
        out: list[str] = []
        for sym in symbols:
            qname = qualified_name_for(
                sym.kind, sym.name, sym.parent_chain,
                start_line=sym.start_line, end_line=sym.end_line, content=sym.text,
            )
            seen = used.get(qname, 0) + 1
            used[qname] = seen
            out.append(qname if seen == 1 else f"{qname}#{seen}")
        return out

    def _to_container(self, parsed: ParsedFile, file_path: str, record_name: str) -> BlocksContainer:
        symbols = parsed.symbols
        qualified_names = self._qualified_names(symbols)

        # Pending facts reference symbols by list index; blocks and groups are
        # numbered in separate spaces, so keep an index -> (is_group, idx) map.
        placement: dict[int, tuple[bool, int]] = {}
        groups: list[BlockGroup] = []
        blocks: list[Block] = []

        # Pass 1 -- every top-level container becomes a group. Anything nested
        # is a block, so groups never nest.
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
                sym, qualified_names[i], gidx, parsed.language, parent_group
            )

        # Pass 2 -- everything else becomes a block. A definition nested inside
        # another block hangs off that block; everything else off its group.
        for i, sym in enumerate(symbols):
            if i in placement:
                continue
            bidx = len(blocks)
            slot = placement.get(sym.parent) if sym.parent is not None else None
            parent_block = slot[1] if slot and not slot[0] else None
            # The two parents are exclusive. A block naming a group as parent
            # must appear in that group's children, and a nested block's bytes
            # are already inside its parent's, so listing it there would stop
            # the group's children from tiling it.
            parent_group = (
                None if parent_block is not None
                else self._nearest_group(sym.parent, symbols, placement)
            )
            blocks.append(
                self._build_block(sym, qualified_names[i], bidx, parsed.language,
                                  parent_group, parent_block)
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

        # Pass 3 -- attach pending facts to the block that owns them. Facts with
        # no owning symbol come back for the summary block.
        file_level = self._attach_pending(parsed, placement, blocks, groups)

        blocks.append(
            self._build_summary_block(
                parsed, symbols, len(blocks), record_name, file_path, file_level
            )
        )

        return BlocksContainer(blocks=blocks, block_groups=groups, code_facts_version=PARSER_VERSION)

    @staticmethod
    def _nearest_group(parent: int | None, symbols: list[ParsedSymbol],
                       placement: dict[int, tuple[bool, int]]) -> int | None:
        """Walk up to the closest ancestor that became a group.

        Only a top-level container is a group, so this resolves in one step for a
        member tiling one and returns None elsewhere. A definition nested inside a
        block is parented through ``parent_block_index`` instead.
        """
        while parent is not None:
            slot = placement.get(parent)
            if slot and slot[0]:
                return slot[1]
            parent = symbols[parent].parent
        return None

    def _build_group(self, sym: ParsedSymbol, qualified_name: str, index: int,
                     language: str, parent_group: int | None = None) -> BlockGroup:
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
            code_metadata=self._code_metadata(sym, qualified_name, language),
        )

    def _build_block(self, sym: ParsedSymbol, qualified_name: str, index: int,
                     language: str, parent_group: int | None,
                     parent_block: int | None = None) -> Block:
        text = sym.text
        return Block(
            index=index,
            type=BlockType.CODE,
            name=sym.name,
            format=DataFormat.CODE,
            parent_index=parent_group,
            parent_block_index=parent_block,
            citation_metadata=CitationMetadata(line_number=sym.start_line),
            content_hash=_content_hash(text),
            data={
                "text": text,
                "subtokens": _subtokenise(text),
                "kind": sym.kind,
                "start_line": sym.start_line,
                "end_line": sym.end_line,
            },
            code_metadata=self._code_metadata(sym, qualified_name, language),
        )

    def _code_metadata(self, sym: ParsedSymbol, qualified_name: str,
                       language: str) -> CodeMetadata:
        return CodeMetadata(
            language=language,
            kind=sym.kind,
            signature=_extract_signature(sym.text) if sym.name else None,
            docstring=_extract_docstring(sym.text, language),
            decorators=list(sym.decorators) or None,
            qualified_name=qualified_name,
            start_line=sym.start_line,
            end_line=sym.end_line,
        )

    def _attach_pending(self, parsed: ParsedFile, placement: dict[int, tuple[bool, int]],
                        blocks: list[Block], groups: list[BlockGroup]) -> list[dict]:
        """Bucket pending facts by owning block; return the file-level ones."""
        buckets: dict[tuple[bool, int] | None, list[dict]] = {}
        seen: dict[tuple[bool, int] | None, set[tuple]] = {}
        for fact in parsed.pending:
            key = placement.get(fact.from_symbol) if fact.from_symbol is not None else None
            facts = buckets.setdefault(key, [])
            if len(facts) >= MAX_FACTS_PER_BLOCK:
                continue
            fact_dict = fact.to_dict()
            # The same call on another line resolves to the same edge.
            identity = tuple(sorted((k, v) for k, v in fact_dict.items() if k != "line"))
            if identity in seen.setdefault(key, set()):
                continue
            seen[key].add(identity)
            facts.append(fact_dict)

        for key, facts in buckets.items():
            if key is None:
                continue
            is_group, idx = key
            target = groups[idx] if is_group else blocks[idx]
            if target.code_metadata is None:
                target.code_metadata = CodeMetadata()
            target.code_metadata.pending_edges = facts

        return buckets.get(None, [])

    def _build_summary_block(self, parsed: ParsedFile, symbols: list[ParsedSymbol],
                             index: int, record_name: str, file_path: str,
                             file_pending: list[dict]) -> Block:
        top_level = [
            f"{s.kind}:{s.name}" for s in symbols if not s.parent_chain and s.name
        ]
        summary = f"{record_name} ({parsed.language}) — " + (
            ", ".join(top_level) if top_level else "no top-level symbols"
        )
        facts = list(file_pending)
        # `export function f` is resolvable here: the symbol is in this file.
        facts.extend(
            {
                "relation": Relation.EXPORTS, "toName": sym.name,
                "line": sym.start_line, "fromKind": "record", "toKind": "block",
                "restrictToFile": True,
            }
            for sym in symbols if sym.is_exported and sym.name
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
                # The parse recovered past a syntax error here; symbols after it may be
                # missing or partial. Kept so tools can say so instead of looking complete (P-9).
                **({"parse_error_line": parsed.parse_error_line} if parsed.parse_error_line else {}),
            },
            code_metadata=CodeMetadata(
                language=parsed.language,
                kind="file_summary",
                type_table=parsed.type_table or None,
                pending_edges=facts or None,
            ),
        )
