"""A project's `global using`s: resident on their node, and a change to them re-resolves the project.

Each case edits files, runs an incremental build and checks it equals a full build of the same tree.
"""
import pytest

from app.modules.code_graph.block_projection import (
    BlockProjectionContext,
    _folder_scopes,
    _resident_facts,
    changed_folder_scope,
    write_code_file_blocks_to_graph,
)
from app.modules.code_graph.edge_builder import EdgeBuildResult, build_code_graph_edges
from app.modules.parsers.code_parser import CodeFileParser

from .conftest import GROUP_ID, ORG_ID, FakeGraphProvider

EXT = b"namespace Api.Extensions;\npublic static class Ext {\n  public static void AddCatalog(this IServiceCollection s) {}\n}\n"
PROGRAM = b"namespace Web;\nclass P { void Go(IServiceCollection s) { s.AddCatalog(); } }\n"
GLOBALS = b"global using Api.Extensions;\n"
FILES = {"rec-ext": ("lib/Ext.cs", EXT), "rec-glob": ("src/Api/GlobalUsings.cs", GLOBALS),
         "rec-prog": ("src/Api/Web/Program.cs", PROGRAM)}


async def _build(graph: FakeGraphProvider, touched: set[str] | None = None) -> EdgeBuildResult:
    return await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                        touched_record_ids=touched, facts_source=graph)


async def _full(files: dict[str, tuple[str, bytes]]) -> FakeGraphProvider:
    fresh, parser = FakeGraphProvider(), CodeFileParser()
    for rid, (path, src) in files.items():
        container = parser.parse_to_blocks(src, path.rsplit("/", 1)[-1], path, "csharp")
        fresh.code_files[rid] = path
        fresh.blobs[rid] = {"block_containers": container.model_dump(mode="json", exclude_none=True)}
        await write_code_file_blocks_to_graph(
            graph_provider=fresh,
            context=BlockProjectionContext(org_id=ORG_ID, record_id=rid, record_group_id=GROUP_ID,
                                           connector_id="conn1", language="csharp", file_path=path),
            block_containers=container)
    await _build(fresh)
    return fresh


def _calls(graph: FakeGraphProvider) -> set[tuple[str, str]]:
    return {(e["_from"], e["_to"]) for e in graph.code_edges("CALLS")}


async def _index(index_file, files: dict[str, tuple[str, bytes]]) -> None:
    for rid, (path, src) in files.items():
        await index_file(rid, path, src, "csharp")


@pytest.mark.asyncio
async def test_an_unchanged_global_usings_file_still_scopes_an_incremental_build(graph, index_file) -> None:
    await _index(index_file, FILES)
    await _build(graph)
    edited = {**FILES, "rec-prog": ("src/Api/Web/Program.cs", PROGRAM + b"// edited\n")}
    await _index(index_file, {"rec-prog": edited["rec-prog"]})
    await _build(graph, {"rec-prog"})
    assert _calls(graph) == _calls(await _full(edited)) != set()


@pytest.mark.parametrize("globals_after", [GLOBALS, b"// no global usings left\n"], ids=["added", "removed"])
@pytest.mark.asyncio
async def test_a_changed_global_using_re_resolves_the_files_under_its_folder(graph, index_file, globals_after) -> None:
    before = {**FILES, "rec-glob": ("src/Api/GlobalUsings.cs",
                                    b"// none yet\n" if globals_after == GLOBALS else GLOBALS)}
    await _index(index_file, before)
    await _build(graph)
    after = {**FILES, "rec-glob": ("src/Api/GlobalUsings.cs", globals_after)}
    await _index(index_file, {"rec-glob": after["rec-glob"]})
    result = await _build(graph, {"rec-glob"})
    assert result.files_reresolved >= 2   # GlobalUsings.cs and Program.cs under its folder
    assert _calls(graph) == _calls(await _full(after))


@pytest.mark.asyncio
async def test_the_file_s_own_using_outranks_a_project_global_using(graph, index_file) -> None:
    # eShop's CreateOrderCommandHandler: `new Order(..)` is the aggregate its own using names.
    await _index(index_file, {
        "r-glob": ("src/Ordering/GlobalUsings.cs", b"global using Ordering.Queries;\nglobal using Ordering.Domain;\n"),
        "r-q": ("src/Ordering/Queries/Order.cs", b"namespace Ordering.Queries;\npublic record Order(int Id);\n"),
        "r-d": ("src/Ordering/Domain/Order.cs", b"namespace Ordering.Domain;\npublic class Order { }\n"),
        "r-h": ("src/Ordering/Commands/Handler.cs", b"namespace Ordering.Commands;\nusing Ordering.Domain;\n"
                                                    b"class H { void Go() { var o = new Order(); } }\n"),
        "r-o": ("src/Ordering/Commands/Other.cs", b"namespace Ordering.Commands;\n"
                                                  b"class O { void Go() { var o = new Order(); } }\n"),
    })
    await _build(graph)

    def targets(rid: str) -> set[str]:
        return {graph.code_files[graph.blocks[e["_to"].partition("/")[2]]["recordId"]]
                for e in graph.code_edges("CALLS") if graph.blocks.get(e["_from"].partition("/")[2], {}).get("recordId") == rid}
    assert targets("r-h") == {"src/Ordering/Domain/Order.cs"}
    assert targets("r-o") == set()   # both only through the globals: still ambiguous, skipped


def test_a_global_using_is_resident_and_a_cs_file_references_its_folders() -> None:
    facts = [{"relation": "IMPORTS_FROM", "toName": "A.B", "context": "global-using"},
             {"relation": "IMPORTS_FROM", "toName": "C"}]
    assert _resident_facts(facts) == facts[:1]
    assert _folder_scopes("src/Api/Web/P.cs") == {"folder-scope:", "folder-scope:src", "folder-scope:src/Api",
                                                  "folder-scope:src/Api/Web"}
    assert _folder_scopes("src/a.py") == set()
    old = [{"pendingEdges": facts[:1]}]
    assert changed_folder_scope("src/Api/GlobalUsings.cs", old, []) == {"folder-scope:src/Api"}
    assert changed_folder_scope("src/Api/GlobalUsings.cs", old, old) == set()


@pytest.mark.asyncio
async def test_a_deleted_global_usings_file_names_its_folder_scope(graph, index_file) -> None:
    from app.modules.code_graph.deleted_files import defined_names

    await _index(index_file, FILES)
    assert "folder-scope:src/Api" in await defined_names(graph, ORG_ID, "rec-glob")
    assert not any(n.startswith("folder-scope:") for n in await defined_names(graph, ORG_ID, "rec-prog"))
