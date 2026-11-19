"""impact lists the files that import what it reaches.

An import fact starts at the importing file: at its ``imports`` block (``from_kind="block"``), or at the file's
record for a pinned fact (a JavaScript ``require``). The target is the imported symbol (IMPORTS), or a whole file
(IMPORTS_FROM). The walk follows blocks only, so a file that imports a symbol and never calls it was invisible.
Importers come only from reported symbols (the anchor and what the walk lists), never from a stepping stone,
are never walked further, sit after callers at the same hop, and are capped per symbol with the rest counted.
"""
import pytest

from app.agents.actions.code_graph.neighbours import impact_impl
from app.agents.actions.code_graph.results import without_marker

from .conftest import BLOCKS, CONN, ORG, RECORDS, USER, QueryGraphProvider, _block

pytestmark = pytest.mark.asyncio

DB, CALLER = "src/shared/db.py", "src/orders/service.py"


def graph(*, deny=(), roles=None) -> QueryGraphProvider:
    g = QueryGraphProvider(deny_records=deny, file_roles=roles)
    g.blocks.clear()
    g.code_files.clear()
    g.records.clear()
    g.edges = []
    add_file(g, "rec-db", DB, ("k_db", "class", "Database"), ("k_port", "method", "Port.save"))
    add_file(g, "rec-caller", CALLER, ("k_save", "method", "OrderService.save"), ("k_caller_imp", "imports", "@1"))
    g.edges += [edge("k_save", "k_db", "CALLS"), edge("k_caller_imp", "k_db", "IMPORTS")]
    return g


def add_file(g: QueryGraphProvider, rid: str, path: str, *symbols: tuple[str, str, str]) -> None:
    for key, kind, name in symbols:
        block = _block(key, rid, path, key, name, kind)
        block.pop("filePath")
        g.blocks[key] = block
    g.code_files[rid] = path
    g.records[rid] = {"_key": rid, "orgId": ORG, "connectorId": CONN, "virtualRecordId": f"v-{rid}"}


def edge(a: str, b: str, rel: str, *, from_record: bool = False, to_record: bool = False) -> dict:
    return {"_from": f"{RECORDS if from_record else BLOCKS}/{a}", "_to": f"{RECORDS if to_record else BLOCKS}/{b}",
            "relationshipType": rel}


def importer(g: QueryGraphProvider, n: int, path: str, target: str = "k_db", *, by_record: bool = False) -> str:
    rid = f"rec-imp{n}"
    if by_record:  # a pinned fact: the file's record imports the symbol
        add_file(g, rid, path)
        g.edges.append(edge(rid, target, "IMPORTS", from_record=True))
    else:
        add_file(g, rid, path, (f"k_imp{n}", "imports", f"@{n}"))
        g.edges.append(edge(f"k_imp{n}", target, "IMPORTS"))
    return rid


async def _impact(g: QueryGraphProvider, **kwargs) -> dict:
    kwargs.setdefault("qualified_name", "class:Database")
    kwargs.setdefault("file_path", DB)
    return without_marker(await impact_impl(graph_provider=g, connector_id=CONN, org_id=ORG, user_id=USER,
                                            **kwargs))


def _files(out: dict, key: str = "impacted_files") -> dict[str, dict]:
    return {f["file_path"]: f for f in out.get(key) or ()}


async def test_a_file_that_only_imports_the_symbol_is_listed() -> None:
    g = graph()
    importer(g, 1, "src/billing/repository.py")
    importer(g, 2, "web/require.js", by_record=True)
    out = await _impact(g)
    files = _files(out)
    for path in ("src/billing/repository.py", "web/require.js"):
        assert files[path] == {"file_path": path, "symbols": [], "imports": ["class:Database"], "nearest_hop": 1,
                               "via": "imports"}
    assert out["impacted_symbols"] == 1  # importers add files, not symbols


async def test_importers_sit_after_callers_at_the_same_hop() -> None:
    g = graph()
    importer(g, 1, "a/first.py")  # sorts before the caller by path
    out = await _impact(g)
    assert [f["file_path"] for f in out["impacted_files"]] == [CALLER, "a/first.py"]


async def test_a_file_that_calls_and_imports_is_listed_once_as_a_caller() -> None:
    files = _files(await _impact(graph()))
    assert files[CALLER]["symbols"] == ["method:OrderService.save"] and "via" not in files[CALLER]


async def test_an_importer_the_caller_cannot_read_is_never_named() -> None:
    g = graph(deny=["rec-imp1"])
    importer(g, 1, "src/secret/vault.py")
    out = await _impact(g)
    assert "vault" not in repr(out) and "importers_omitted" not in out


async def test_test_files_that_import_are_listed_with_the_tests() -> None:
    g = graph(roles={"rec-imp1": "test"})
    importer(g, 1, "tests/test_db.py")
    out = await _impact(g)
    assert "tests/test_db.py" not in _files(out)
    assert _files(out, "impacted_tests")["tests/test_db.py"]["via"] == "imports"
    shown = await _impact(g, include_tests=True)
    assert _files(shown)["tests/test_db.py"]["via"] == "imports"


async def test_importers_are_capped_per_symbol_nearest_path_first_and_the_rest_counted() -> None:
    g = graph()
    near = [importer(g, n, f"src/shared/near_{n:02d}.py") for n in range(10)]
    far = [importer(g, 100 + n, f"zzz/far_{n:02d}.py") for n in range(55)]
    out = await _impact(g)
    listed = {p for p, f in _files(out).items() if f.get("via") == "imports"}
    assert len(listed) == 50 and {f"src/shared/near_{n:02d}.py" for n in range(10)} <= listed
    assert out["importers_omitted"] == 15 and out["truncated"] is True
    assert "15" in out["note"] and "import" in out["note"]
    assert near and far


async def test_a_whole_file_impact_lists_the_files_that_import_the_file() -> None:
    g = graph()
    add_file(g, "rec-mod", "src/api/routes.py", ("k_mod_imp", "imports", "@m"))
    g.edges.append(edge("k_mod_imp", "rec-db", "IMPORTS_FROM", to_record=True))
    out = await _impact(g, qualified_name=None)
    assert _files(out)["src/api/routes.py"]["via"] == "imports"


async def test_importers_of_a_caller_are_one_hop_further() -> None:
    g = graph()
    importer(g, 1, "src/api/handlers.py", target="k_save")
    assert _files(await _impact(g))["src/api/handlers.py"]["nearest_hop"] == 2


async def test_importers_follow_edge_types() -> None:
    """IMPORTS (or IMPORTS_FROM) asks for the importer step; a CALLS-only impact lists callers alone."""
    g = graph()
    importer(g, 1, "src/billing/repository.py")
    calls = _files(await _impact(g, edge_types=["CALLS"]))
    assert set(calls) == {CALLER} and "via" not in calls[CALLER]
    only = await _impact(g, edge_types=["IMPORTS"])
    # no walk, so the file that also calls is listed through its import alone
    assert {p: f["via"] for p, f in _files(only).items()} == {"src/billing/repository.py": "imports",
                                                              CALLER: "imports"}
    assert only["impacted_symbols"] == 0
    assert "via" in _files(await _impact(g, edge_types=["CALLS", "IMPORTS_FROM"]))["src/billing/repository.py"]


async def test_an_empty_edge_types_list_means_all_relations_as_omitting_it_does() -> None:
    g = graph()
    importer(g, 1, "src/billing/repository.py")
    omitted, empty = await _impact(g), await _impact(g, edge_types=[])
    assert empty == omitted
    assert {f.get("via") for f in empty["impacted_files"]} == {None, "imports"}  # callers and importers both


async def test_impact_accepts_imports_but_still_refuses_unknown_edge_types() -> None:
    out = await _impact(graph(), edge_types=["IMPORTS", "CONTAINS"])
    assert "unknown edge_types ['CONTAINS']" in out["error"] and "IMPORTS" in out["error"]


async def test_importer_hops_never_pass_depth() -> None:
    """At depth 1 only the anchor's importers: a caller's importer would be at hop 2."""
    g = graph()
    importer(g, 1, "src/api/handlers.py", target="k_save")
    importer(g, 2, "src/billing/repository.py")
    files = _files(await _impact(g, depth=1))
    assert "src/api/handlers.py" not in files and files["src/billing/repository.py"]["nearest_hop"] == 1


async def test_a_stepping_stone_brings_no_importers() -> None:
    """A file importing the interface depends on the interface; its callers already come through the stone."""
    g = graph()
    add_file(g, "rec-impl", "src/shared/pg.py", ("k_impl", "method", "PgPort.save"))
    g.edges.append(edge("k_impl", "k_port", "OVERRIDES"))
    importer(g, 1, "src/web/uses_port.py", target="k_port")
    out = await _impact(g, file_path="src/shared/pg.py", qualified_name="method:PgPort.save")
    assert "src/web/uses_port.py" not in _files(out)
