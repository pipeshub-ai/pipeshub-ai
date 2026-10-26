"""Which code files are worth an LLM classification (SC-10).

Every indexed code file got one LLM call for departments, topics and a
summary. For a test, or a file that defines next to nothing, that answer
adds nothing search or the agents use: the code tools read the symbols, and
the file's path already says what it is. Identical content is classified
once already (MD5 dedupe shares the result within an org).
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from app.models.blocks import BlockType
from app.modules.parsers.code_parser.file_role import FileRole, classify_file_role

if TYPE_CHECKING:
    from app.models.blocks import BlocksContainer

__all__ = ["code_classification_skip_reason"]

# Fewer named definitions than this and the file is a script, a config module
# or a re-export barrel.
_MIN_DEFINITIONS = 2
_SKIPPED_ROLES = frozenset({FileRole.TEST, FileRole.MIGRATION, FileRole.TYPE_DEFINITION})


def code_classification_skip_reason(file_path: str | None,
                                    container: BlocksContainer) -> str | None:
    """Why this code file gets no LLM classification, or None when it should."""
    if os.getenv("CODE_FILE_CLASSIFICATION", "true").strip().lower() in ("false", "0", "off"):
        return "CODE_FILE_CLASSIFICATION is off"
    if file_path:
        role = classify_file_role(file_path)
        if role in _SKIPPED_ROLES:
            return f"{role.value} file"
    definitions = sum(1 for block in container.blocks
                      if block.type == BlockType.CODE and getattr(block, "name", None))
    if definitions < _MIN_DEFINITIONS:
        return f"{definitions} definition(s)"
    return None
