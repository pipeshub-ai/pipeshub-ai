"""Parsing source files into blocks and deferred edge facts."""
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


def test_only_a_top_level_container_becomes_a_group():
    # A nested class is a block like a method -- one span, addressable, with its
    # own members hung off it. Groups never nest.
    container = CodeFileParser().parse_to_blocks(NESTED_PY, "mod.py", "src/mod.py", "python")
    groups = {g.name: g for g in container.block_groups}
    assert set(groups) == {"Outer"}
    assert groups["Outer"].parent_index is None

    inner = _blocks_by_name(container)["Inner"]
    assert inner.code_metadata.kind == "class"
    assert inner.parent_index == groups["Outer"].index
    assert inner.parent_block_index is None


def test_a_nested_definition_hangs_off_the_block_enclosing_it():
    container = CodeFileParser().parse_to_blocks(NESTED_PY, "mod.py", "src/mod.py", "python")
    by_name = _blocks_by_name(container)
    run, inner = by_name["run"], by_name["Inner"]
    # The two parent fields are exclusive: a block naming a group as parent must
    # appear in that group's children, and run() sits inside Inner's bytes, so
    # listing it under Outer would stop Outer's children from tiling it.
    assert run.parent_block_index == inner.index
    assert run.parent_index is None
    assert run.code_metadata.qualified_name == "method:Outer.Inner.run"


def test_a_function_nested_in_a_function_is_a_block_of_its_own():
    src = b"def outer():\n    def inner():\n        return 1\n    return inner()\n"
    container = CodeFileParser().parse_to_blocks(src, "m.py", "src/m.py", "python")
    by_name = _blocks_by_name(container)
    assert container.block_groups == []
    assert by_name["inner"].parent_block_index == by_name["outer"].index
    assert by_name["inner"].code_metadata.qualified_name == "function:outer.inner"


def test_an_empty_class_is_still_a_group():
    # Nothing to tile, but it stays a group so members added later have a home
    # and the record keeps one node per top-level type.
    container = CodeFileParser().parse_to_blocks(
        b"class Foo:\n    pass\n", "m.py", "src/m.py", "python"
    )
    assert [g.name for g in container.block_groups] == ["Foo"]


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

    relations = {(e["relation"], e["toName"]) for e in imports.code_metadata.pending_edges}
    assert ("IMPORTS_FROM", ".helpers") in relations
    assert ("IMPORTS_FROM", "os") in relations
    assert ("IMPORTS", "parse") in relations
    # The innermost block containing the statement owns the edge, so these are
    # sourced from the imports block rather than from the file record.
    assert all(e["fromKind"] == "block" for e in imports.code_metadata.pending_edges)


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


def test_calls_attach_to_the_enclosing_symbol():
    container = CodeFileParser().parse_to_blocks(NESTED_PY, "mod.py", "src/mod.py", "python")
    run = _blocks_by_name(container)["run"]
    assert [e["toName"] for e in run.code_metadata.pending_edges] == ["helper"]


def test_builtin_method_calls_on_untyped_receivers_are_dropped():
    """`d.get()` / `s.lower()` can never resolve -- resolution needs a receiver
    type, and these receivers are language primitives. Recording them would only
    bloat a traversed node."""
    src = b'''
def go(payload, label):
    payload.get("k")
    label.lower()
    return custom(payload)
'''
    container = CodeFileParser().parse_to_blocks(src, "a.py", "src/a.py", "python")
    go = _blocks_by_name(container)["go"]
    assert [e["toName"] for e in go.code_metadata.pending_edges] == ["custom"]


def test_typed_receiver_keeps_a_builtin_named_method():
    # `client.get()` where client is a repo type must survive: it is resolvable.
    src = b'''
def go():
    client: Client = build()
    return client.get("/health")
'''
    container = CodeFileParser().parse_to_blocks(src, "a.py", "src/a.py", "python")
    go = _blocks_by_name(container)["go"]
    get = next(e for e in go.code_metadata.pending_edges if e["toName"] == "get")
    assert get["receiverType"] == "Client"


def test_builtins_do_not_become_edges():
    src = b"def go():\n    print(len([1]))\n    return custom()\n"
    container = CodeFileParser().parse_to_blocks(src, "a.py", "src/a.py", "python")
    go = _blocks_by_name(container)["go"]
    names = [e["toName"] for e in go.code_metadata.pending_edges]
    assert names == ["custom"]


def test_typescript_heritage_and_type_table():
    src = b'''
export class Service extends Base implements Runnable {
  run(id: string) {
    const r: Repo = this.repo;
    return r.find(id);
  }
}
'''
    container = CodeFileParser().parse_to_blocks(src, "svc.ts", "src/svc.ts", "typescript")
    service = _blocks_by_name(container)["Service"]
    relations = {(e["relation"], e["toName"]) for e in service.code_metadata.pending_edges}
    assert ("EXTENDS", "Base") in relations
    assert ("IMPLEMENTS", "Runnable") in relations

    run = _blocks_by_name(container)["run"]
    find = next(e for e in run.code_metadata.pending_edges if e["toName"] == "find")
    assert find["receiverType"] == "Repo"  # from `const r: Repo`


def test_pending_edges_are_capped():
    from app.modules.parsers.code_parser.code_file_parser import MAX_PENDING_EDGES_PER_BLOCK

    body = "\n".join(f"    call_{i}()" for i in range(MAX_PENDING_EDGES_PER_BLOCK + 100))
    src = f"def big():\n{body}\n".encode()
    container = CodeFileParser().parse_to_blocks(src, "big.py", "src/big.py", "python")
    big = _blocks_by_name(container)["big"]
    assert len(big.code_metadata.pending_edges) == MAX_PENDING_EDGES_PER_BLOCK
    assert big.code_metadata.pending_edges_truncated is True
