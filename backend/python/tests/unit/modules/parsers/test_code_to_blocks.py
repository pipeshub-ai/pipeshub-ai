"""Unit tests for tree-sitter code → BlocksContainer conversion."""

from __future__ import annotations

import pytest

from app.models.blocks import (
    BlockGroup,
    BlocksContainer,
    BlockSubType,
    BlockType,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.modules.parsers.code.code_parser import CodeParser
from app.modules.parsers.code.fallback import build_whole_file_container
from app.modules.parsers.code.registry import resolve_adapter
from app.modules.parsers.code.tree_to_blocks import CodeToBlocksConverter


@pytest.fixture
def parser() -> CodeParser:
    return CodeParser()


@pytest.fixture
def converter() -> CodeToBlocksConverter:
    return CodeToBlocksConverter()


def _child_block_indices(group: BlockGroup) -> list[int]:
    assert group.children is not None
    indices: list[int] = []
    for block_range in group.children.block_ranges:
        indices.extend(range(block_range.start, block_range.end + 1))
    return indices


def _child_group_indices(group: BlockGroup) -> list[int]:
    assert group.children is not None
    indices: list[int] = []
    for group_range in group.children.block_group_ranges:
        indices.extend(range(group_range.start, group_range.end + 1))
    return indices


def _groups_named(container: BlocksContainer, name: str) -> list[BlockGroup]:
    return [group for group in container.block_groups if group.name == name]


pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_python")
pytest.importorskip("tree_sitter_javascript")
pytest.importorskip("tree_sitter_typescript")


class TestPythonAdapter:
    def test_module_import_class_and_method(self, parser: CodeParser) -> None:
        source = (
            "import os\n\n"
            "class UserService:\n"
            "    def fetch(self):\n"
            "        return os.getcwd()\n"
        )
        container = parser.parse_to_blocks(source, "service.py")

        assert len(container.block_groups) >= 3
        file_root = container.block_groups[0]
        assert file_root.type == GroupType.TEXT_SECTION
        assert file_root.sub_type == GroupSubType.CONTENT
        assert file_root.name == "service.py"
        assert file_root.children is not None

        import_blocks = [
            block for block in container.blocks
            if isinstance(block.data, str) and block.data.startswith("import os")
        ]
        assert len(import_blocks) == 1
        assert import_blocks[0].sub_type == BlockSubType.CODE
        assert import_blocks[0].parent_index == file_root.index
        assert import_blocks[0].citation_metadata is not None
        assert import_blocks[0].citation_metadata.line_number == 1

        class_groups = _groups_named(container, "UserService")
        assert len(class_groups) == 1
        class_group = class_groups[0]
        assert class_group.parent_index == file_root.index

        method_groups = _groups_named(container, "fetch")
        assert len(method_groups) == 1
        method_group = method_groups[0]
        assert method_group.parent_index == class_group.index
        assert method_group.index in _child_group_indices(class_group)

        method_blocks = [
            container.blocks[index]
            for index in _child_block_indices(method_group)
        ]
        assert len(method_blocks) == 1
        assert "return os.getcwd()" in method_blocks[0].data

    def test_decorated_function_is_single_group(self, parser: CodeParser) -> None:
        source = (
            "@staticmethod\n"
            "def foo():\n"
            "    return 1\n"
        )
        container = parser.parse_to_blocks(source, "decorators.py")

        foo_groups = _groups_named(container, "foo")
        assert len(foo_groups) == 1
        foo_group = foo_groups[0]

        foo_blocks = [container.blocks[i] for i in _child_block_indices(foo_group)]
        assert len(foo_blocks) == 1
        assert "@staticmethod" in foo_blocks[0].data
        assert "def foo" in foo_blocks[0].data

    def test_empty_file(self, parser: CodeParser) -> None:
        container = parser.parse_to_blocks("   \n", "empty.py")
        assert container.blocks == []
        assert container.block_groups == []


class TestTypeScriptAdapter:
    def test_export_function(self, parser: CodeParser) -> None:
        ts_adapter = resolve_adapter("sample.ts")
        if ts_adapter is None:
            pytest.skip("typescript grammar not installed")

        source = (
            "export function greet(name: string): string {\n"
            "  return name;\n"
            "}\n"
        )
        container = parser.parse_to_blocks(source, "greet.ts")

        greet_groups = _groups_named(container, "greet")
        assert len(greet_groups) == 1
        greet_group = greet_groups[0]
        assert greet_group.type == GroupType.TEXT_SECTION

        greet_blocks = [container.blocks[i] for i in _child_block_indices(greet_group)]
        assert len(greet_blocks) == 1
        assert "return name" in greet_blocks[0].data
        assert greet_blocks[0].code_metadata is not None
        assert greet_blocks[0].code_metadata.language == "typescript"


class TestFallback:
    def test_unknown_extension_uses_whole_file_block(self, parser: CodeParser) -> None:
        source = "package main\n\nfunc main() {}\n"
        container = parser.parse_to_blocks(source, "main.go")

        assert len(container.block_groups) == 1
        assert len(container.blocks) == 1
        assert container.blocks[0].sub_type == BlockSubType.CODE
        assert container.blocks[0].data == source.strip()
        assert container.blocks[0].code_metadata is not None
        assert container.blocks[0].code_metadata.language == "go"

    def test_build_whole_file_container_empty(self) -> None:
        container = build_whole_file_container("", file_path="empty.go")
        assert container.blocks == []
        assert container.block_groups == []


class TestBlockMetadata:
    def test_content_hash_and_format(self, converter: CodeToBlocksConverter) -> None:
        adapter = resolve_adapter("sample.py")
        assert adapter is not None

        source = "import sys\n"
        container = converter.convert(source, file_path="sample.py", adapter=adapter)
        assert len(container.blocks) == 1
        block = container.blocks[0]
        assert block.format == DataFormat.CODE
        assert block.type == BlockType.TEXT
        assert block.content_hash is not None
        assert len(block.content_hash) == 64
