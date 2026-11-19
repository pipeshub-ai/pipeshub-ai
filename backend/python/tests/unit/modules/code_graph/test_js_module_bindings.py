"""A JS name an import binds to a module is that module, never a same-named definition; a CommonJS module
bound by `require` may be the function it exports.
"""
import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.edge_builder import build_code_graph_edges

from .conftest import GROUP_ID, ORG_ID

CALLS = RecordRelations.CALLS.value


pytestmark = pytest.mark.asyncio


F_JS = b'''
function f(x) {
  return x;
}

module.exports = f;
'''

G_JS = b'''
var f = require("./f");

function f() {
  return 0;
}

function g() {
  return f(1);
}
'''


async def test_a_javascript_declaration_does_not_take_a_name_back_from_require(graph, index_file) -> None:
    """`function f(){}` hoists and `var f = require("./f")` then rebinds `f`, so g()'s `f(1)` is f.js's
    function: the own-definition rule is Python's."""
    await index_file("recF", "lib/f.js", F_JS, "javascript")
    await index_file("recG", "lib/g.js", G_JS, "javascript")
    await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID, record_group_id=GROUP_ID)
    targets = {(graph.code_files.get(graph.blocks.get(e["_to"].partition("/")[2], {}).get("recordId")),
                graph.blocks.get(e["_to"].partition("/")[2], {}).get("qualifiedName"))
               for e in graph.code_edges(CALLS)
               if graph.blocks.get(e["_from"].partition("/")[2], {}).get("qualifiedName") == "function:g"}
    assert targets == {("lib/f.js", "function:f")}, targets
