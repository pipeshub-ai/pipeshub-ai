"""A call never binds across a language boundary, whatever the caller's language (R-2)."""
import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.guards import lang_family

from .conftest import GROUP_ID, ORG_ID

CALLS = RecordRelations.CALLS.value


async def _build(graph) -> None:
    from app.modules.code_graph.edge_builder import build_code_graph_edges

    await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID,
                                 record_group_id=GROUP_ID)


def _call_targets(graph) -> set[str]:
    out = set()
    for edge in graph.code_edges(CALLS):
        doc = graph.blocks[edge["_to"].split("/", 1)[1]]
        out.add(f"{graph.code_files[doc['recordId']]}::{doc['qualifiedName']}")
    return out


@pytest.mark.parametrize(("path", "family"), [
    ("svc/main.go", "go"), ("pricing/src/lib.rs", "rust"), ("Pay.java", "jvm"), ("Notify.kt", "jvm"),
    ("img/resize.h", "c"), ("img/resize.cpp", "c"), ("Report.cs", "csharp"), ("admin/user.rb", "ruby"),
    ("hook.php", "php"), ("Cart.swift", "swift"), ("cart.dart", "dart"), ("plugin.lua", "lua"),
])
def test_every_parsed_language_has_a_family(path, family) -> None:
    assert lang_family(None, path) == family


def _add_call_fact(graph, record_id: str, qualified_name: str, callee: str) -> None:
    """Go, Java and Rust emit no call facts yet (P-3); inject the one the guard must judge."""
    for block in graph.blobs[record_id]["block_containers"]["blocks"]:
        meta = block.get("code_metadata") or {}
        if meta.get("qualified_name") == qualified_name:
            meta.setdefault("pending_edges", []).append(
                {"relation": CALLS, "toName": callee, "line": 3, "fromKind": "block", "toKind": "block"})
            return
    raise AssertionError(f"no block {qualified_name}")


GO_CALLER = b"package gw\n\nfunc Handle() int { return computeDiscount(1) }\n"


@pytest.mark.asyncio
async def test_a_go_call_does_not_bind_to_a_python_definition(graph, index_file) -> None:
    await index_file("py", "pricing/discount.py", b"def computeDiscount(x):\n    return x\n", "python")
    await index_file("caller", "gateway/server.go", GO_CALLER, "go")
    _add_call_fact(graph, "caller", "function:Handle", "computeDiscount")
    await _build(graph)

    assert _call_targets(graph) == set()


@pytest.mark.asyncio
async def test_a_call_within_one_language_still_resolves(graph, index_file) -> None:
    await index_file("py", "pricing/discount.py", b"def computeDiscount(x):\n    return x\n", "python")
    await index_file("go", "gateway/util.go",
                     b"package gw\n\nfunc computeDiscount(x int) int { return x }\n", "go")
    await index_file("caller", "gateway/server.go", GO_CALLER, "go")
    _add_call_fact(graph, "caller", "function:Handle", "computeDiscount")
    await _build(graph)

    assert _call_targets(graph) == {"gateway/util.go::function:computeDiscount"}
