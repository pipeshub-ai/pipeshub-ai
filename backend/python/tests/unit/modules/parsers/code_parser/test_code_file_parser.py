"""Parsing source files into blocks."""
import pytest

from app.modules.parsers.code_parser import CodeFileParser
from app.modules.parsers.code_parser.engine import parse_code

NESTED_PY = b'''
class Outer:
    class Inner:
        def run(self):
            return helper()

    def top(self):
        return 1
'''


def _blocks_by_name(container):
    out = {}
    for block in container.blocks:
        if block.name:
            out[block.name] = block
    for group in container.block_groups:
        if group.name:
            out[group.name] = group
    return out


def test_nested_class_keeps_the_full_parent_chain():
    # Truncating the chain to one level yields method:Outer.run, which collides
    # with a genuine Outer.run and silently merges two distinct symbols.
    container = CodeFileParser().parse_to_blocks(
        NESTED_PY, "mod.py", "src/mod.py", "python"
    )
    run = _blocks_by_name(container)["run"]
    assert run.code_metadata.qualified_name == "method:Outer.Inner.run"


def test_parsing_is_deterministic():
    first = CodeFileParser().parse_to_blocks(NESTED_PY, "mod.py", "src/mod.py", "python")
    second = CodeFileParser().parse_to_blocks(NESTED_PY, "mod.py", "src/mod.py", "python")
    names = lambda c: [b.code_metadata.qualified_name for b in c.blocks if b.code_metadata]
    hashes = lambda c: [b.content_hash for b in c.blocks]
    assert names(first) == names(second)
    assert hashes(first) == hashes(second)


def test_every_container_with_members_becomes_a_group():
    # Nested containers get their own group too, so Outer.Inner keeps its level
    # instead of collapsing its methods onto Outer.
    container = CodeFileParser().parse_to_blocks(NESTED_PY, "mod.py", "src/mod.py", "python")
    by_name = {g.name: g for g in container.block_groups}
    assert set(by_name) == {"Outer", "Inner"}
    assert by_name["Outer"].parent_index is None
    assert by_name["Inner"].parent_index == by_name["Outer"].index
    assert by_name["Outer"].children.block_group_ranges  # Inner is linked as a child
    assert by_name["Inner"].children.block_ranges        # run() is attached to Inner


def test_local_variables_are_not_class_fields():
    src = b'''
class A:
    LIMIT = 5
    def go(self):
        local = 1
        return local
'''
    container = CodeFileParser().parse_to_blocks(src, "a.py", "src/a.py", "python")
    kinds = {b.name: b.code_metadata.kind for b in container.blocks if b.code_metadata}
    assert kinds.get("LIMIT") == "field"
    assert "local" not in kinds


def test_imports_get_their_own_block():
    src = b"from .helpers import parse\nimport os\n"
    container = CodeFileParser().parse_to_blocks(src, "a.py", "src/a.py", "python")

    imports = next(b for b in container.blocks
                   if b.code_metadata and b.code_metadata.kind == "imports")
    # Import statements are real content and must survive as block text; the
    # run of them coalesces into one block rather than one block per statement.
    assert imports.data["text"] == src.decode()


def test_unknown_language_returns_empty_container():
    container = CodeFileParser().parse_to_blocks(b"SELECT 1", "q.sql", "db/q.sql", None)
    assert container.blocks == []
    assert container.block_groups == []


def test_broken_syntax_still_yields_symbols():
    src = b"def ok():\n    return 1\n\ndef broken(:\n"
    parsed = parse_code(src, "python")
    assert any(s.name == "ok" for s in parsed.symbols)
    assert parsed.parse_error_line is not None


@pytest.mark.asyncio
async def test_iparser_contract():
    parser = CodeFileParser()
    result = await parser.parse(
        b"def go():\n    pass\n", "a.py", {"file_path": "src/a.py", "language": "python"}
    )
    assert result.block_container.blocks
    assert result.metadata["language"] == "python"
    assert "py" in parser.supported_formats()


def test_docstring_extraction_with_annotated_signature():
    """Annotations with colons must not prevent docstring extraction."""
    src = b'def process(data: dict[str, Any], count: int = 0) -> list[str]:\n    """Transform data into strings."""\n    return []\n'
    container = CodeFileParser().parse_to_blocks(src, "m.py", "src/m.py", "python")
    func = next(b for b in container.blocks if b.name == "process")
    assert func.code_metadata.docstring == "Transform data into strings."
