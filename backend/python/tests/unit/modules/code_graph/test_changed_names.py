"""What an incremental build needs that a file's current blocks no longer show (R-3, R-7).

A file that drops a definition, or loses all of them, has to tell the next
build which names went, and an importer has to say which module it imports,
so that the files depending on either are re-resolved.
"""
import pytest

from app.modules.code_graph.block_projection import (
    BlockProjectionContext,
    drop_code_file_blocks,
    write_code_file_blocks_to_graph,
)
from app.modules.code_graph.edge_builder import EdgeBuildResult, build_code_graph_edges
from app.modules.code_graph.module_refs import module_ref, module_refs_of_path
from app.modules.parsers.code_parser import CodeFileParser

from .conftest import GROUP_ID, ORG_ID, FakeGraphProvider


@pytest.mark.parametrize(("specifier", "importer", "expected"), [
    ("pkg.m28", "pkg/a.py", "module:pkg/m28"),
    (".m28", "backend/pkg/a.py", "module:backend/pkg/m28"),
    ("..core.base", "backend/pkg/sub/a.py", "module:backend/pkg/core/base"),
    (".", "backend/pkg/a.py", "module:backend/pkg"),
    ("./utils", "src/app/main.ts", "module:src/app/utils"),
    ("../lib/format.js", "src/app/main.ts", "module:src/lib/format"),
    ("@/lib/format", "frontend/src/app/x.tsx", "module:lib/format"),
    ("react", "src/a.ts", "module:react"),
    ("../../../outside", "a.ts", None),
    ("", "a.py", None),
])
def test_an_import_names_the_module_path(specifier: str, importer: str, expected: str | None) -> None:
    assert module_ref(specifier, importer) == expected


@pytest.mark.parametrize(("specifier", "importer", "module"), [
    ("app.cart.service", "backend/app/api.py", "backend/app/cart/service.py"),
    (".service", "backend/app/cart/api.py", "backend/app/cart/service.py"),
    ("./utils", "src/a.ts", "src/utils/index.ts"),
    ("@/lib/format", "frontend/src/x.tsx", "frontend/src/lib/format.ts"),
    ("pkg", "a.py", "pkg/__init__.py"),
])
def test_a_module_matches_the_imports_that_mean_it(specifier: str, importer: str, module: str) -> None:
    assert module_ref(specifier, importer) in module_refs_of_path(module)


def test_a_module_does_not_match_a_namesake_elsewhere() -> None:
    """Matching on the last segment alone sent every importer of any
    `service` module to be re-resolved when one was added."""
    assert module_ref("app.billing.service", "backend/app/api.py") not in \
        module_refs_of_path("backend/app/cart/service.py")


async def test_a_dropped_definition_is_remembered(graph, index_file) -> None:
    await index_file("rec-a", "pkg/a.py", b"def keep():\n    pass\n\n\ndef gone():\n    pass\n", "python")
    graph.changed_names.clear()
    await index_file("rec-a", "pkg/a.py", b"def keep():\n    pass\n", "python")
    assert graph.changed_names["rec-a"]["changedNames"] == ["gone"]


async def test_changed_names_accumulate_until_they_expire(graph, index_file) -> None:
    await index_file("rec-a", "pkg/a.py", b"def a():\n    pass\n\n\ndef b():\n    pass\n", "python")
    graph.changed_names.clear()
    await index_file("rec-a", "pkg/a.py", b"def a():\n    pass\n", "python")
    await index_file("rec-a", "pkg/a.py", b"x = 1\n", "python")
    assert graph.changed_names["rec-a"]["changedNames"] == ["a", "b"]
    graph.changed_names["rec-a"]["changedNamesAt"] = 0
    await index_file("rec-a", "pkg/a.py", b"def c():\n    pass\n", "python")
    await index_file("rec-a", "pkg/a.py", b"x = 2\n", "python")
    assert graph.changed_names["rec-a"]["changedNames"] == ["c"]


async def test_emptying_a_file_remembers_every_name_it_had(graph, index_file) -> None:
    await index_file("rec-a", "pkg/a.py", b"def a():\n    pass\n\n\ndef b():\n    pass\n", "python")
    await drop_code_file_blocks(graph, "rec-a")
    assert graph.changed_names["rec-a"]["changedNames"] == ["a", "b"]
    assert not [b for b in graph.blocks.values() if b.get("recordId") == "rec-a"]


async def test_an_importer_lists_the_module_it_imports(graph, index_file) -> None:
    await index_file("rec-a", "pkg/a.py", b"from .b import f\nfrom pkg import c\n\n\ndef g():\n    f()\n",
                     "python")
    names = graph.referenced_names["rec-a"]
    assert {"f", "module:pkg/b", "module:pkg/c"} <= set(names)


async def test_a_removed_definition_rebinds_its_callers_elsewhere(graph, index_file) -> None:
    """The caller never changed, and it imports nothing from the file that
    lost the definition, so name overlap with that file's remaining names
    could not find it."""
    await index_file("rec-lib", "pkg/lib.py", b"def run():\n    pass\n", "python")
    await index_file("rec-alt", "pkg/alt.py", b"def run():\n    pass\n\n\ndef other():\n    pass\n",
                     "python")
    await index_file("rec-app", "pkg/app.py", b"from pkg.alt import run\n\n\ndef main():\n    run()\n",
                     "python")
    await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                 facts_source=graph)
    await index_file("rec-alt", "pkg/alt.py", b"def other():\n    pass\n", "python")
    result = await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID,
                                          record_group_id=GROUP_ID,
                                          touched_record_ids={"rec-alt"}, facts_source=graph)
    assert result.files_reresolved >= 2


async def test_a_body_only_edit_records_no_names(graph, index_file) -> None:
    await index_file("rec-a", "pkg/a.py", b"def get():\n    return 1\n", "python")
    graph.changed_names.pop("rec-a", None)
    await index_file("rec-a", "pkg/a.py", b"def get():\n    return 2\n", "python")
    assert "rec-a" not in graph.changed_names


async def test_a_new_file_records_the_names_it_defines(graph, index_file) -> None:
    await index_file("rec-a", "pkg/a.py", b"def get():\n    pass\n", "python")
    assert graph.changed_names["rec-a"]["changedNames"] == ["get"]


@pytest.mark.parametrize(("before", "after"), [
    (b"class P:\n    def m(self):\n        pass\n\n\nclass Q:\n    pass\n",
     b"class P:\n    pass\n\n\nclass Q:\n    def m(self):\n        pass\n"),
    (b"def Thing():\n    pass\n", b"class Thing:\n    pass\n"),
])
async def test_a_moved_or_rekinded_definition_is_recorded(graph, index_file, before, after) -> None:
    """Same bare name, different definition: callers may bind elsewhere now."""
    await index_file("rec-a", "pkg/a.py", before, "python")
    graph.changed_names.pop("rec-a", None)
    await index_file("rec-a", "pkg/a.py", after, "python")
    assert set(graph.changed_names["rec-a"]["changedNames"]) & {"m", "Thing"}


async def _build(graph: FakeGraphProvider, touched: set[str] | None = None) -> EdgeBuildResult:
    return await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                        touched_record_ids=touched, facts_source=graph)


async def test_an_edit_does_not_reresolve_namesakes_elsewhere(graph, index_file) -> None:
    """Every file calling any `get` was re-resolved on each edit to a file
    defining one, which in a real repo is most of it (R-7)."""
    await index_file("rec-a", "pkg/a.py", b"def get():\n    return 1\n", "python")
    await index_file("rec-b", "pkg/b.py", b"def get():\n    return 1\n", "python")
    await index_file("rec-c", "pkg/c.py", b"from pkg.b import get\n\n\ndef run():\n    get()\n", "python")
    await _build(graph)
    graph.changed_names.clear()
    await index_file("rec-a", "pkg/a.py", b"def get():\n    return 2\n", "python")
    result = await _build(graph, {"rec-a"})
    assert result.files_reresolved == 1


async def test_a_method_moved_into_a_class_binds_its_callers_as_a_full_build_would(index_file, graph) -> None:
    lib_before = b"class P:\n    def m(self):\n        pass\n\n\nclass Q:\n    pass\n"
    lib_after = b"class P:\n    pass\n\n\nclass Q:\n    def m(self):\n        pass\n"
    app = b"from pkg.lib import Q\n\n\ndef run():\n    Q().m()\n"
    await index_file("rec-lib", "pkg/lib.py", lib_before, "python")
    await index_file("rec-app", "pkg/app.py", app, "python")
    await _build(graph)
    await index_file("rec-lib", "pkg/lib.py", lib_after, "python")
    await _build(graph, {"rec-lib"})

    fresh = FakeGraphProvider()
    parser = CodeFileParser()
    for rid, path, src in (("rec-lib", "pkg/lib.py", lib_after), ("rec-app", "pkg/app.py", app)):
        container = parser.parse_to_blocks(src, path.rsplit("/", 1)[-1], path, "python")
        fresh.code_files[rid] = path
        fresh.blobs[rid] = {"block_containers": container.model_dump(mode="json", exclude_none=True)}
        await write_code_file_blocks_to_graph(
            graph_provider=fresh,
            context=BlockProjectionContext(org_id=ORG_ID, record_id=rid, record_group_id=GROUP_ID,
                                           connector_id="conn1", language="python", file_path=path),
            block_containers=container)
    await _build(fresh)

    def edges(g: FakeGraphProvider) -> set[tuple[str, str, str]]:
        return {(e["_from"], e["_to"], e["relationshipType"]) for e in g.code_edges()}
    assert edges(graph) == edges(fresh)
    assert any(rel == "CALLS" for _, _, rel in edges(fresh))
