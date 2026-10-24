"""What of a code block is embedded: filler dropped, nested code once, long symbols split (SC-9)."""
import asyncio
from types import SimpleNamespace

from app.modules.parsers.code_parser.code_file_parser import CodeFileParser
from app.modules.transformers import code_units as code_units_module
from app.modules.transformers.vectorstore import _SPARSE_EXTRA, _build_code_documents


def _blocks(source: str, name: str = "a.tsx") -> list:
    ext = name.rsplit(".", 1)[-1]
    result = asyncio.run(CodeFileParser().parse(source.encode(), name, {"extension": ext,
                                                                         "file_path": f"web/{name}"}))
    return [b for b in result.block_container.blocks if b.type.value == "code"]


def _docs(source: str, name: str = "a.tsx") -> list:
    return _build_code_documents(_blocks(source, name), "vr", "org", "web/" + name)


def test_separator_and_directive_spans_are_not_embedded() -> None:
    docs = _docs("'use client';\n;\nexport function Panel() { return renderPanelContents() }\n")
    assert [d.metadata["blockIndex"] for d in docs] == [1]


def test_a_nested_function_is_embedded_once() -> None:
    docs = _docs("export function outer() {\n  function innerHelper() {\n    return computeInnerValue()\n"
                 "  }\n  return innerHelper()\n}\n")
    outer = next(d for d in docs if "function:outer\n" in d.page_content)
    assert "computeInnerValue" not in outer.page_content
    assert "…" in outer.page_content and "return innerHelper()" in outer.page_content
    assert sum("computeInnerValue" in d.page_content for d in docs) == 1


def test_a_long_symbol_is_split_and_every_piece_names_it(monkeypatch) -> None:
    monkeypatch.setattr(code_units_module, "UNIT_TOKENS", 64)
    body = "\n".join(f"    total_{i} = compute_line_value({i}, factor)" for i in range(60))
    docs = _docs(f'def long_report(factor):\n    """Build the report."""\n{body}\n    return total_0\n',
                 "r.py")
    assert len(docs) > 3
    assert all("def long_report(factor)" in d.page_content for d in docs)
    assert all(d.metadata["isBlock"] is False for d in docs)
    joined = "".join(d.page_content for d in docs)
    assert all(f"total_{i} =" in joined for i in range(60)), "no line is lost"


def test_subtokens_reach_keyword_search_but_not_the_payload() -> None:
    docs = _docs("export function fetchUserProfile() { return loadRemoteRecord() }\n")
    assert "profile" in docs[0].metadata[_SPARSE_EXTRA]
    assert "profile" not in docs[0].page_content.split("\n", 1)[1].lower().split()


def test_a_named_tiny_symbol_is_still_embedded() -> None:
    block = SimpleNamespace(id="b", index=0, data={"text": "x = 1"},
                            code_metadata=SimpleNamespace(qualified_name="variable:x", kind="variable"))
    assert len(_build_code_documents([block], "vr", "org")) == 1
