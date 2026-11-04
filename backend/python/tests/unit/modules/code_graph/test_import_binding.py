"""A call binds to what the file imported, never to another same-named definition (R-1)."""
import pytest

from app.config.constants.arangodb import RecordRelations

from .conftest import GROUP_ID, ORG_ID

CALLS = RecordRelations.CALLS.value


async def _build(graph) -> None:
    from app.modules.code_graph.edge_builder import build_code_graph_edges

    await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID,
                                 record_group_id=GROUP_ID)


def _targets(graph, relation: str, from_path: str) -> set[str]:
    out = set()
    for edge in graph.code_edges(relation):
        src = edge["_from"].split("/", 1)[1]
        src_doc = graph.blocks.get(src)
        src_path = graph.code_files.get(src_doc["recordId"]) if src_doc else graph.code_files.get(src)
        if src_path != from_path:
            continue
        _, _, ident = edge["_to"].partition("/")
        doc = graph.blocks.get(ident)
        out.add(f"{graph.code_files[doc['recordId']]}::{doc['qualifiedName']}" if doc
                else graph.code_files.get(ident))
    return out


async def _package(index_file, *dirs: str) -> None:
    for i, d in enumerate(dirs):
        await index_file(f"init{i}", f"{d}/__init__.py", b"", "python")


@pytest.mark.asyncio
async def test_a_stdlib_module_never_binds_to_a_repo_file_of_the_same_name(graph, index_file) -> None:
    await _package(index_file, "app", "app/telemetry")
    await index_file("log", "app/telemetry/logging.py", b"def getLogger(name):\n    return name\n", "python")
    await index_file("svc", "app/svc.py", b"import logging\n\nlogger = logging.getLogger(__name__)\n", "python")
    await _build(graph)

    assert _targets(graph, CALLS, "app/svc.py") == set()
    assert _targets(graph, RecordRelations.IMPORTS_FROM.value, "app/svc.py") == set()


@pytest.mark.asyncio
async def test_a_third_party_import_shadows_a_repo_definition(graph, index_file) -> None:
    await _package(index_file, "app", "app/legacy")
    await index_file("req", "app/legacy/requests.py", b"def get(url):\n    return url\n", "python")
    await index_file("hook", "app/hooks.py",
                     b"from requests import get\n\n\ndef pull(url):\n    return get(url)\n", "python")
    await _build(graph)

    assert _targets(graph, CALLS, "app/hooks.py") == set()
    assert _targets(graph, RecordRelations.IMPORTS.value, "app/hooks.py") == set()


@pytest.mark.asyncio
async def test_the_imported_definition_beats_a_same_directory_decoy(graph, index_file) -> None:
    await _package(index_file, "app", "app/shared", "app/payments")
    await index_file("val", "app/shared/validation.py", b"def validate(v, kind):\n    return v\n", "python")
    await index_file("dec", "app/payments/checks.py", b"def validate(v):\n    return v\n", "python")
    await index_file("auth", "app/payments/authorize.py",
                     b"from app.shared.validation import validate\n\n\n"
                     b"def authorize(p):\n    return validate(p, 'amount')\n", "python")
    await _build(graph)

    assert _targets(graph, CALLS, "app/payments/authorize.py") == {
        "app/shared/validation.py::function:validate"}


@pytest.mark.asyncio
async def test_an_imported_base_beats_a_same_directory_base(graph, index_file) -> None:
    await _package(index_file, "app", "app/shared", "app/catalog")
    await index_file("imp", "app/shared/importing.py", b"class Base:\n    pass\n", "python")
    await index_file("dec", "app/catalog/base.py", b"class Base:\n    pass\n", "python")
    await index_file("csv", "app/catalog/importers.py",
                     b"from app.shared.importing import Base\n\n\nclass CsvImporter(Base):\n    pass\n",
                     "python")
    await _build(graph)

    assert _targets(graph, RecordRelations.INHERITS.value, "app/catalog/importers.py") == {
        "app/shared/importing.py::class:Base"}


@pytest.mark.asyncio
async def test_a_local_function_is_not_lost_to_an_imported_module_of_the_same_name(graph, index_file) -> None:
    await _package(index_file, "app", "app/analytics")
    await index_file("jobs", "app/analytics/jobs.py", b"def run():\n    return 1\n", "python")
    await index_file("sched", "app/analytics/scheduler.py",
                     b"from app.analytics import jobs\n\n\ndef run():\n    return 0\n\n\n"
                     b"def tick():\n    return run(), jobs.run()\n", "python")
    await _build(graph)

    assert _targets(graph, CALLS, "app/analytics/scheduler.py") == {
        "app/analytics/scheduler.py::function:run", "app/analytics/jobs.py::function:run"}
    assert "app/analytics/jobs.py" in _targets(
        graph, RecordRelations.IMPORTS_FROM.value, "app/analytics/scheduler.py")


@pytest.mark.asyncio
async def test_aliased_imports_resolve_through_the_alias(graph, index_file) -> None:
    await _package(index_file, "app", "app/shared")
    await index_file("val", "app/shared/validation.py", b"def validate_email(v):\n    return v\n", "python")
    await index_file("other", "app/shared/other.py", b"def check_email(v):\n    return v\n", "python")
    await index_file("use", "app/use.py",
                     b"from app.shared import validation as v\n"
                     b"from app.shared.validation import validate_email as check_email\n\n\n"
                     b"def norm(a, b):\n    return v.validate_email(a), check_email(b)\n", "python")
    await _build(graph)

    assert _targets(graph, CALLS, "app/use.py") == {"app/shared/validation.py::function:validate_email"}


@pytest.mark.asyncio
async def test_a_typescript_package_import_does_not_bind_to_a_repo_symbol(graph, index_file) -> None:
    await index_file("deb", "src/lib/debounce.ts", b"export function debounce(f) { return f; }\n",
                     "typescript")
    await index_file("hook", "src/hooks/useSearch.ts",
                     b'import { debounce } from "lodash";\nexport function useSearch(f) { return debounce(f); }\n',
                     "typescript")
    await _build(graph)

    assert _targets(graph, CALLS, "src/hooks/useSearch.ts") == set()
    assert _targets(graph, RecordRelations.IMPORTS.value, "src/hooks/useSearch.ts") == set()


@pytest.mark.asyncio
async def test_a_root_path_alias_resolves_to_the_source_root(graph, index_file) -> None:
    await index_file("http", "web/src/lib/http.ts", b"export function getJson(u) { return u; }\n",
                     "typescript")
    await index_file("api", "web/src/features/orders/api.ts",
                     b'import { getJson as get } from "@/lib/http";\n'
                     b'export function fetchOrders() { return get("/orders"); }\n', "typescript")
    await _build(graph)

    assert _targets(graph, CALLS, "web/src/features/orders/api.ts") == {
        "web/src/lib/http.ts::function:getJson"}


@pytest.mark.asyncio
async def test_edges_record_the_rule_that_resolved_them(graph, index_file) -> None:
    await _package(index_file, "app")
    await index_file("a", "app/a.py", b"def helper():\n    return 1\n", "python")
    await index_file("b", "app/b.py",
                     b"from app.a import helper\n\n\ndef local():\n    return 2\n\n\n"
                     b"def run():\n    return helper(), local()\n", "python")
    await _build(graph)

    by_target = {
        graph.blocks[e["_to"].split("/", 1)[1]]["qualifiedName"]: e["resolution"]
        for e in graph.code_edges(CALLS)
    }
    assert by_target == {"function:helper": "import-binding", "function:local": "same-file"}
