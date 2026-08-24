"""Cross-file edge resolution.

Every ambiguity must resolve to emitting nothing. Skipping a guard produces god
nodes -- one popular name absorbing hundreds of false edges -- which degrades
the graph far more than the missing edges cost.
"""
import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.edge_builder import build_code_graph_edges

from .conftest import GROUP_ID, ORG_ID

HELPERS = b'''
def parse(raw):
    return raw

class Loader:
    def load(self, path):
        return parse(path)
'''

CLIENT = b'''
from .helpers import parse, Loader

class Client:
    def fetch(self, url):
        loader = Loader()
        return loader.load(parse(url))
'''


async def _build(graph, touched=None, dry_run=False):
    return await build_code_graph_edges(
        graph_provider=graph,
        org_id=ORG_ID,
        record_group_id=GROUP_ID,
        touched_record_ids=touched,
        dry_run=dry_run,
    )


@pytest.mark.asyncio
async def test_cross_file_call_resolves_with_import_evidence(graph, index_file):
    await index_file("recH", "src/helpers.py", HELPERS, "python")
    await index_file("recC", "src/client.py", CLIENT, "python")
    await _build(graph)

    assert "function:parse" in graph.edge_targets(RecordRelations.CALLS.value)
    calls = graph.code_edges(RecordRelations.CALLS.value)
    parse_edge = next(
        e for e in calls
        if graph.blocks[e["_to"].split("/")[1]].get("qualifiedName") == "function:parse"
    )
    assert parse_edge["confidence"] == "EXTRACTED"


@pytest.mark.asyncio
async def test_member_call_resolves_through_the_type_table(graph, index_file):
    await index_file("recH", "src/helpers.py", HELPERS, "python")
    await index_file("recC", "src/client.py", CLIENT, "python")
    await _build(graph)
    # loader = Loader() -> loader.load() binds to Loader.load
    assert "method:Loader.load" in graph.edge_targets(RecordRelations.CALLS.value)


@pytest.mark.asyncio
async def test_imports_are_sourced_from_the_imports_block(graph, index_file):
    """The innermost block containing the statement owns the edge.

    Sourcing this from the file record would attribute the dependency to the
    whole file rather than to the import statement that creates it.
    """
    await index_file("recH", "src/helpers.py", HELPERS, "python")
    await index_file("recC", "src/client.py", CLIENT, "python")
    await _build(graph)

    imports_from = graph.code_edges(RecordRelations.IMPORTS_FROM.value)
    assert len(imports_from) == 1
    edge = imports_from[0]

    source = graph.blocks[edge["_from"].split("/")[1]]
    assert source["kind"] == "imports"
    assert source["recordId"] == "recC"
    # The target is still the imported file itself.
    assert edge["_to"] == "records/recH"


@pytest.mark.asyncio
async def test_ambiguous_name_emits_no_edge(graph, index_file):
    # Two files define handle(); a third calls it with no import to disambiguate.
    await index_file("recA", "src/a.py", b"def handle(x):\n    return x\n", "python")
    await index_file("recB", "src/b.py", b"def handle(x):\n    return x\n", "python")
    await index_file("recC", "src/c.py", b"def go():\n    return handle(1)\n", "python")

    result = await _build(graph)
    assert graph.edge_targets(RecordRelations.CALLS.value) == []
    assert result.ambiguous_skipped >= 1


@pytest.mark.asyncio
async def test_cross_language_calls_do_not_bind(graph, index_file):
    await index_file("recPy", "src/a.py", b"def validate(x):\n    return x\n", "python")
    await index_file(
        "recTs", "src/b.ts", b"export function go(){ return validate(1); }\n", "typescript"
    )
    await _build(graph)
    # A TypeScript validate() must never bind to the Python definition.
    assert graph.edge_targets(RecordRelations.CALLS.value) == []


@pytest.mark.asyncio
async def test_package_import_does_not_bind_to_a_same_named_repo_symbol(graph, index_file):
    """`import { Box } from "@mui/material"` names a package, not a repo file.

    With no file to point at, the name alone is all that is left, and the one
    repo block called `Box` used to win it -- which is how the module graph came
    to claim that the frontend imports `backend/python/.../box.py`.
    """
    await index_file("recPy", "backend/box.py", b"class Box:\n    pass\n", "python")
    await index_file(
        "recTs", "frontend/panel.tsx",
        b'import { Box } from "@mui/material";\nexport const P = () => Box;\n',
        "tsx",
    )
    await _build(graph)

    imports = graph.code_edges(RecordRelations.IMPORTS.value)
    assert [graph.blocks[e["_to"].split("/")[1]].get("filePath") for e in imports] == []


@pytest.mark.asyncio
async def test_js_call_without_import_is_dropped(graph, index_file):
    # ES modules have no implicit cross-module scope.
    await index_file("recA", "src/a.ts", b"export function helper(){ return 1; }\n", "typescript")
    await index_file("recB", "src/b.ts", b"export function use(){ return helper(); }\n", "typescript")
    await _build(graph)
    assert graph.edge_targets(RecordRelations.CALLS.value) == []


@pytest.mark.asyncio
async def test_barrel_chain_resolves_to_the_defining_module(graph, index_file):
    await index_file("recRepo", "src/repo.ts", b"export class Repo { find(){ return 1; } }\n", "typescript")
    await index_file("recIndex", "src/index.ts", b'export { Repo } from "./repo";\n', "typescript")
    await index_file(
        "recSvc", "src/svc.ts",
        b'import { Repo } from "./index";\nexport class S { go(){ const r: Repo = x; return r.find(); } }\n',
        "typescript",
    )
    await _build(graph)

    imports = graph.code_edges(RecordRelations.IMPORTS.value)
    targets = {graph.blocks[e["_to"].split("/")[1]].get("filePath") for e in imports}
    assert "src/repo.ts" in targets  # walked through index.ts, not stopped at it


@pytest.mark.asyncio
async def test_module_qualified_call_resolves(graph, index_file):
    """`import analysis; analysis.analyze_game()`.

    The receiver is a module, so the type table can never carry it -- this has
    to resolve through import resolution instead.
    """
    await index_file("recA", "analysis.py", b"def analyze_game(x):\n    return x\n", "python")
    await index_file(
        "recR", "runner.py",
        b"import analysis\n\ndef main():\n    return analysis.analyze_game(1)\n",
        "python",
    )
    await _build(graph)
    assert "function:analyze_game" in graph.edge_targets(RecordRelations.CALLS.value)


@pytest.mark.asyncio
async def test_module_qualified_call_to_unknown_module_emits_nothing(graph, index_file):
    # chess.engine is third-party: no file in the repo, so no edge.
    await index_file("recA", "analysis.py", b"def popen_uci(x):\n    return x\n", "python")
    await index_file(
        "recR", "runner.py",
        b"import chess.engine\n\ndef main():\n    return chess.engine.SimpleEngine.popen_uci('x')\n",
        "python",
    )
    await _build(graph)
    assert graph.edge_targets(RecordRelations.CALLS.value) == []


@pytest.mark.asyncio
async def test_inheritance_resolves_across_files(graph, index_file):
    await index_file("recBase", "src/base.py", b"class Base:\n    pass\n", "python")
    await index_file("recSub", "src/sub.py", b"from .base import Base\n\nclass Sub(Base):\n    pass\n", "python")
    await _build(graph)
    assert "class:Base" in graph.edge_targets(RecordRelations.INHERITS.value)


@pytest.mark.asyncio
async def test_non_test_definition_wins_the_tie(graph, index_file):
    await index_file("recSrc", "src/impl.py", b"def shared():\n    return 1\n", "python")
    await index_file("recTest", "tests/impl.py", b"def shared():\n    return 2\n", "python")
    await index_file("recUse", "src/use.py", b"def go():\n    return shared()\n", "python")
    await _build(graph)

    calls = graph.code_edges(RecordRelations.CALLS.value)
    assert len(calls) == 1
    assert graph.blocks[calls[0]["_to"].split("/")[1]]["filePath"] == "src/impl.py"


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(graph, index_file):
    await index_file("recH", "src/helpers.py", HELPERS, "python")
    await index_file("recC", "src/client.py", CLIENT, "python")
    result = await _build(graph, dry_run=True)

    assert result.edges_written > 0
    assert graph.code_edges() == []


@pytest.mark.asyncio
async def test_rebuild_is_idempotent(graph, index_file):
    await index_file("recH", "src/helpers.py", HELPERS, "python")
    await index_file("recC", "src/client.py", CLIENT, "python")
    first = await _build(graph)
    snapshot = sorted((e["_from"], e["_to"], e["relationshipType"]) for e in graph.code_edges())

    second = await _build(graph)
    assert sorted((e["_from"], e["_to"], e["relationshipType"]) for e in graph.code_edges()) == snapshot
    assert second.edges_by_type == first.edges_by_type


class TestIncrementalInvalidation:
    """A change in B invalidates edges belonging to unchanged A.

    None of these show up in A's own timestamp, so re-resolving only the touched
    file leaves the graph wrong.
    """

    CALLER = b"from .b import foo\n\ndef caller():\n    return foo(1)\n"
    B_WITH = b"def foo(x):\n    return x\n"
    B_WITHOUT = b"def other(x):\n    return x\n"
    B_RENAMED = b"def bar(x):\n    return x\n"

    async def _setup(self, index_file, graph):
        await index_file("recA", "src/a.py", self.CALLER, "python")
        await index_file("recB", "src/b.py", self.B_WITH, "python")
        await build_code_graph_edges(
            graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID
        )
        assert graph.edge_targets(RecordRelations.CALLS.value) == ["function:foo"]

    @pytest.mark.asyncio
    async def test_target_deleted_drops_the_edge(self, graph, index_file):
        await self._setup(index_file, graph)
        await index_file("recB", "src/b.py", self.B_WITHOUT, "python")
        await build_code_graph_edges(
            graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
            touched_record_ids={"recB"},
        )
        # A never changed, but its edge now points at nothing.
        assert graph.edge_targets(RecordRelations.CALLS.value) == []

    @pytest.mark.asyncio
    async def test_target_added_creates_the_edge(self, graph, index_file):
        await self._setup(index_file, graph)
        await index_file("recB", "src/b.py", self.B_WITHOUT, "python")
        await build_code_graph_edges(
            graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
            touched_record_ids={"recB"},
        )
        await index_file("recB", "src/b.py", self.B_WITH, "python")
        await build_code_graph_edges(
            graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
            touched_record_ids={"recB"},
        )
        assert graph.edge_targets(RecordRelations.CALLS.value) == ["function:foo"]

    @pytest.mark.asyncio
    async def test_target_renamed_drops_the_edge(self, graph, index_file):
        await self._setup(index_file, graph)
        await index_file("recB", "src/b.py", self.B_RENAMED, "python")
        await build_code_graph_edges(
            graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
            touched_record_ids={"recB"},
        )
        assert graph.edge_targets(RecordRelations.CALLS.value) == []

    @pytest.mark.asyncio
    async def test_untouched_files_keep_their_edges(self, graph, index_file):
        await self._setup(index_file, graph)
        await index_file("recC", "src/c.py", b"def unrelated():\n    return 1\n", "python")
        await build_code_graph_edges(
            graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
            touched_record_ids={"recC"},
        )
        # An unscoped delete would have wiped this while not rebuilding it.
        assert graph.edge_targets(RecordRelations.CALLS.value) == ["function:foo"]
