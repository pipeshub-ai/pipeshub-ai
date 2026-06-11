"""
CodeFileParser: converts a source-code file into a BlocksContainer for indexing.

Mapping from tree-sitter semantic blocks (parser.py) to the indexing block model:
  - Container kinds (class, impl, trait, interface, struct, namespace, module, enum)
    at the top level  → BlockGroup (GroupType.CODE, GroupSubType.CODE_CLASS)
  - All other blocks (function, method, imports, statement, comment, type_alias, …)
    → Block (BlockType.CODE)
  - Methods / nested blocks whose path leads back to a top-level container are
    attached to that container's BlockGroup via parent_index and BlockGroupChildren.
"""
from __future__ import annotations

import os
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
from app.modules.parsers.code_parser.parser import (
    LANG_CONFIG,
    Block as ParsedBlock,
    detect_language,
    extract_blocks,
)

# Block kinds that are treated as "containers" → become BlockGroups
CONTAINER_KINDS: frozenset[str] = frozenset(
    {"class", "impl", "trait", "interface", "struct", "namespace", "module", "enum"}
)


class CodeFileParser:
    """Parse a source-code file into a BlocksContainer.

    Only top-level containers (no ancestors in their `path`) become BlockGroups.
    Nested containers and all leaf blocks become Block objects.
    """

    def parse(self, source: bytes, file_name: str, language: Optional[str] = None) -> BlocksContainer:
        if language is None:
            language = detect_language(file_name)
        if language is None or language not in LANG_CONFIG:
            return BlocksContainer(blocks=[], block_groups=[])

        parsed_blocks = extract_blocks(source, language)
        if not parsed_blocks:
            return BlocksContainer(blocks=[], block_groups=[])

        return self._build_container(parsed_blocks, language)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_container(self, parsed_blocks: list[ParsedBlock], language: str) -> BlocksContainer:
        block_groups: list[BlockGroup] = []
        blocks: list[Block] = []

        # name → block-group index for top-level containers
        top_level_container_index: dict[str, int] = {}

        # ── Pass 1: create a BlockGroup for every top-level container ──
        for pb in parsed_blocks:
            if pb.type in CONTAINER_KINDS and not pb.path:
                bg_idx = len(block_groups)
                if pb.name:
                    top_level_container_index[pb.name] = bg_idx
                block_groups.append(BlockGroup(
                    index=bg_idx,
                    name=pb.name,
                    type=GroupType.CODE,
                    sub_type=GroupSubType.CODE_CLASS,
                    code_metadata=CodeMetadata(language=language),
                    citation_metadata=CitationMetadata(line_number=pb.start_line),
                    data={
                        "text": pb.text,
                        "kind": pb.type,
                        "start_line": pb.start_line,
                        "end_line": pb.end_line,
                    },
                ))

        # ── Pass 2: create a Block for every non-top-level-container block ──
        for pb in parsed_blocks:
            if pb.type in CONTAINER_KINDS and not pb.path:
                continue  # already handled as a BlockGroup

            parent_group_idx = self._resolve_parent_group(pb, top_level_container_index)

            b_idx = len(blocks)
            blocks.append(Block(
                index=b_idx,
                type=BlockType.CODE,
                name=pb.name,
                format=DataFormat.CODE,
                parent_index=parent_group_idx,
                citation_metadata=CitationMetadata(line_number=pb.start_line),
                code_metadata=CodeMetadata(language=language),
                data={
                    "text": pb.text,
                    "kind": pb.type,
                    "start_line": pb.start_line,
                    "end_line": pb.end_line,
                },
            ))

            if parent_group_idx is not None:
                bg = block_groups[parent_group_idx]
                if bg.children is None:
                    bg.children = BlockGroupChildren()
                bg.children.add_block_index(b_idx)

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
