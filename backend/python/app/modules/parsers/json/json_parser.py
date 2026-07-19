"""JSON Parser.

Parses raw JSON bytes into a :class:`BlocksContainer` using schema-aware,
natural-language chunking:

- Flat key/value sections become one natural-language TEXT block
  ("key: value, key2: value2, ...").
- Nested objects become nested ``KEY_VALUE_AREA`` BlockGroups.
- Arrays of objects become ``TABLE`` BlockGroups with one ``TABLE_ROW`` block
  per item (reusing the same natural-language row convention as the
  CSV/Excel/SQL parsers so the indexing pipeline treats them identically).
- Arrays of scalars become one inline TEXT block.
- Long string values are pulled into their own TEXT block for proper
  sentence-level chunking instead of being buried in a flattened sentence.

No LLM is required — flattening is fully deterministic, matching the
SQL table parser's "no-LLM" design.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    CitationMetadata,
    DataFormat,
    GroupSubType,
    GroupType,
    TableMetadata,
)
from app.modules.parsers.json.structured_data_utils import (
    LONG_TEXT_THRESHOLD,
    MAX_FLATTEN_DEPTH,
    classify_value,
    flatten_leaf_dict,
    flatten_to_text,
    object_to_sentence,
    stringify_scalar_array,
)
from app.services.parsing.interface import ParseError, ParseErrorCode, ParseResult
from app.utils.logger import create_logger

logger = create_logger("json_parser")


class JSONParser:
    """Parser for JSON bytes -> BlocksContainer."""

    MAX_DEPTH = MAX_FLATTEN_DEPTH
    LONG_TEXT_THRESHOLD = LONG_TEXT_THRESHOLD

    def __init__(self) -> None:
        self._blocks: List[Block] = []
        self._groups: List[BlockGroup] = []
        self._format: DataFormat = DataFormat.JSON

    async def parse(
        self,
        content: bytes,
        record_name: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> ParseResult:
        if not content or not content.strip():
            raise ParseError(ParseErrorCode.EMPTY_CONTENT, "JSON content is empty")

        try:
            data = json.loads(content.decode("utf-8"))
        except Exception as e:
            raise ParseError(
                ParseErrorCode.PARSE_FAILED,
                f"Failed to parse JSON for '{record_name}': {e}",
                {"error": str(e)},
            )

        block_container = self.parse_data(data, record_name)
        return ParseResult(
            block_container=block_container,
            metadata={"record_name": record_name},
        )

    def supported_formats(self) -> List[str]:
        return ["json"]

    # ------------------------------------------------------------------
    # Public entry point shared with YAMLParser (operates on already
    # decoded Python data, so YAML can reuse this after yaml.safe_load).
    # ------------------------------------------------------------------

    def parse_data(
        self,
        data: Any,
        record_name: str,
        data_format: DataFormat = DataFormat.JSON,
    ) -> BlocksContainer:
        self._blocks = []
        self._groups = []
        self._format = data_format

        root_group = self._new_root_group(record_name)
        self._process_container(data, root_group, path="", depth=0)

        root_group.data = {"summary": self._build_root_summary(data, record_name)}
        root_group.content_hash = self._hash(self._safe_dumps(data))

        return BlocksContainer(blocks=self._blocks, block_groups=self._groups)

    # ------------------------------------------------------------------
    # Tree walker
    # ------------------------------------------------------------------

    def _process_container(
        self,
        value: Any,
        parent_group: BlockGroup,
        path: str,
        depth: int,
    ) -> None:
        """Dispatch *value* (the payload owned by *parent_group*) by shape."""
        if isinstance(value, dict):
            self._process_dict(value, parent_group, path, depth)
            return

        if isinstance(value, list):
            self._process_top_level_list(value, parent_group, path, depth)
            return

        # Bare scalar document (rare, e.g. a JSON file containing just `"hello"`)
        idx = self._add_text_block(str(value), parent_group, path, self._format)
        parent_group.children = BlockGroupChildren.from_indices([idx], [])

    def _process_top_level_list(
        self,
        items: list,
        parent_group: BlockGroup,
        path: str,
        depth: int,
    ) -> None:
        if not items:
            parent_group.children = BlockGroupChildren.from_indices([], [])
            return

        kind = classify_value(items)
        if kind == "object_array" and depth < self.MAX_DEPTH:
            table_group = self._build_table_group(
                items, name=parent_group.name or "items", parent_group=parent_group, path=path
            )
            parent_group.children = BlockGroupChildren.from_indices([], [table_group.index])
        elif kind == "scalar_array":
            idx = self._add_text_block(
                stringify_scalar_array(items), parent_group, path, self._format
            )
            parent_group.children = BlockGroupChildren.from_indices([idx], [])
        else:
            idx = self._add_text_block(
                self._safe_dumps(items), parent_group, path, DataFormat.JSON
            )
            parent_group.children = BlockGroupChildren.from_indices([idx], [])

    def _process_dict(
        self,
        obj: dict,
        parent_group: BlockGroup,
        path: str,
        depth: int,
    ) -> None:
        flat_fields: Dict[str, Any] = {}
        child_block_indices: List[int] = []
        child_group_indices: List[int] = []

        for key, value in obj.items():
            key_path = f"{path}.{key}" if path else str(key)
            kind = classify_value(value)

            if kind == "scalar":
                if isinstance(value, str) and len(value) > self.LONG_TEXT_THRESHOLD:
                    idx = self._add_text_block(value, parent_group, key_path, DataFormat.TXT)
                    child_block_indices.append(idx)
                else:
                    flat_fields[key_path] = value

            elif kind == "empty":
                flat_fields[key_path] = value

            elif kind == "scalar_array":
                flat_fields[key_path] = stringify_scalar_array(value)

            elif kind == "flat_object":
                flat_fields.update(flatten_leaf_dict(value, key_path))

            elif kind == "nested_object":
                if depth >= self.MAX_DEPTH:
                    flat_fields[key_path] = self._safe_dumps(value)
                else:
                    child_group = self._new_group(
                        GroupType.KEY_VALUE_AREA, name=str(key), parent_group=parent_group
                    )
                    self._process_dict(value, child_group, key_path, depth + 1)
                    child_group_indices.append(child_group.index)

            elif kind == "object_array":
                if depth >= self.MAX_DEPTH:
                    flat_fields[key_path] = self._safe_dumps(value)
                else:
                    table_group = self._build_table_group(
                        value, name=str(key), parent_group=parent_group, path=key_path
                    )
                    child_group_indices.append(table_group.index)

            else:  # mixed_array
                flat_fields[key_path] = self._safe_dumps(value)

        if flat_fields:
            idx = self._add_flat_fields_block(flat_fields, parent_group, path)
            child_block_indices.append(idx)

        parent_group.children = BlockGroupChildren.from_indices(
            child_block_indices, child_group_indices
        )

    # ------------------------------------------------------------------
    # Block / group builders
    # ------------------------------------------------------------------

    def _new_root_group(self, record_name: str) -> BlockGroup:
        group = BlockGroup(
            index=0,
            type=GroupType.KEY_VALUE_AREA,
            sub_type=GroupSubType.RECORD,
            name=record_name,
            format=self._format,
        )
        self._groups.append(group)
        return group

    def _new_group(
        self,
        group_type: GroupType,
        name: str,
        parent_group: BlockGroup,
        sub_type: Optional[GroupSubType] = None,
    ) -> BlockGroup:
        idx = len(self._groups)
        group = BlockGroup(
            index=idx,
            type=group_type,
            sub_type=sub_type,
            name=name,
            format=self._format,
            parent_index=parent_group.index,
        )
        self._groups.append(group)
        return group

    def _add_text_block(
        self,
        text: str,
        parent_group: BlockGroup,
        path: str,
        data_format: DataFormat,
    ) -> int:
        idx = len(self._blocks)
        block = Block(
            index=idx,
            type=BlockType.TEXT,
            format=data_format,
            data=text,
            parent_index=parent_group.index,
            content_hash=self._hash(text),
            citation_metadata=CitationMetadata(section_title=path or parent_group.name),
        )
        self._blocks.append(block)
        return idx

    def _add_flat_fields_block(
        self,
        flat_fields: Dict[str, Any],
        parent_group: BlockGroup,
        path: str,
    ) -> int:
        text = flatten_to_text(flat_fields)
        return self._add_text_block(text, parent_group, path, self._format)

    def _build_table_group(
        self,
        items: List[Any],
        name: str,
        parent_group: BlockGroup,
        path: str,
    ) -> BlockGroup:
        table_group = self._new_group(GroupType.TABLE, name=name, parent_group=parent_group)

        column_names: List[str] = []
        seen: set = set()
        row_indices: List[int] = []

        for item in items:
            if not isinstance(item, dict):
                # Stray non-dict entry in an otherwise homogeneous array — skip
                # rather than corrupt the table's row shape.
                continue

            flat_row = flatten_leaf_dict(item)
            for column in flat_row:
                if column not in seen:
                    seen.add(column)
                    column_names.append(column)

            row_text = object_to_sentence(item)
            idx = len(self._blocks)
            self._blocks.append(
                Block(
                    index=idx,
                    type=BlockType.TABLE_ROW,
                    format=DataFormat.JSON,
                    data={
                        "row_natural_language_text": row_text,
                        "row": self._safe_dumps(item),
                    },
                    parent_index=table_group.index,
                    content_hash=self._hash(row_text),
                    citation_metadata=CitationMetadata(section_title=path),
                )
            )
            row_indices.append(idx)

        table_group.table_metadata = TableMetadata(
            num_of_rows=len(row_indices),
            num_of_cols=len(column_names),
            has_header=True,
            column_names=column_names,
        )
        table_group.data = {
            "table_summary": (
                f"Array '{name}' with {len(row_indices)} item(s) and fields: "
                f"{', '.join(column_names) if column_names else 'none'}."
            ),
            "column_headers": column_names,
        }
        table_group.children = BlockGroupChildren.from_indices(row_indices, [])
        table_group.content_hash = self._hash(
            json.dumps({"name": name, "column_headers": column_names}, sort_keys=True)
        )
        return table_group

    # ------------------------------------------------------------------
    # Misc helpers
    # ------------------------------------------------------------------

    def _build_root_summary(self, data: Any, record_name: str) -> str:
        if isinstance(data, list):
            shape, count = "array", len(data)
        elif isinstance(data, dict):
            shape, count = "object", len(data)
        else:
            shape, count = "scalar", 1
        format_label = self._format.value.upper()
        return (
            f"{format_label} record '{record_name}' containing a top-level "
            f"{shape} with {count} entr{'y' if count == 1 else 'ies'}."
        )

    @staticmethod
    def _safe_dumps(value: Any) -> str:
        try:
            return json.dumps(value, default=str, ensure_ascii=False)
        except Exception:
            return str(value)

    @staticmethod
    def _hash(content: str) -> str:
        sha256_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        md5_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
        return f"{sha256_hash}:{md5_hash}"
