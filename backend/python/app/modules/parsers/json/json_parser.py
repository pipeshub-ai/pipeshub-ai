"""Parser for JSON documents.

JSON is structured text rather than a spreadsheet or a rich document, so this
parser flattens arbitrary JSON into readable ``key.path: value`` TEXT blocks
(the same block shape the markdown parser emits). This keeps JSON records
searchable/indexable without depending on Docling or an LLM.
"""

import json
from typing import Any, List, Tuple, Union
from uuid import uuid4

from app.models.blocks import (
    Block,
    BlocksContainer,
    BlockSubType,
    BlockType,
    DataFormat,
)


class JSONParser:
    """Flatten arbitrary JSON into readable TEXT blocks (key path + value)."""

    def parse(self, content: Union[str, bytes]) -> BlocksContainer:
        """Parse JSON text/bytes into a BlocksContainer of TEXT blocks.

        Raises:
            json.JSONDecodeError: if the content is not valid JSON.
        """
        if isinstance(content, bytes):
            content = content.decode("utf-8")

        data = json.loads(content)

        blocks: List[Block] = []
        for path, value in self._flatten(data):
            text = f"{path}: {self._render(value)}" if path else self._render(value)
            blocks.append(
                Block(
                    id=str(uuid4()),
                    index=len(blocks),
                    type=BlockType.TEXT,
                    sub_type=BlockSubType.PARAGRAPH,
                    format=DataFormat.TXT,
                    data=text,
                    parent_index=None,
                )
            )

        return BlocksContainer(blocks=blocks, block_groups=[])

    @staticmethod
    def _render(value: Any) -> str:
        """Render a JSON leaf value as a human-readable string."""
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def _flatten(self, obj: Any, prefix: str = "") -> List[Tuple[str, Any]]:
        """Flatten nested JSON into (dotted_path, leaf_value) pairs.

        - dict  -> ``prefix.key``
        - list  -> ``prefix[i]``
        - empty containers keep a leaf so their presence is still indexed.
        """
        out: List[Tuple[str, Any]] = []

        if isinstance(obj, dict):
            if not obj:
                out.append((prefix, "{}"))
                return out
            for key, value in obj.items():
                child = f"{prefix}.{key}" if prefix else str(key)
                out.extend(self._flatten(value, child))
        elif isinstance(obj, list):
            if not obj:
                out.append((prefix, "[]"))
                return out
            for i, value in enumerate(obj):
                child = f"{prefix}[{i}]"
                out.extend(self._flatten(value, child))
        else:
            out.append((prefix, obj))

        return out
