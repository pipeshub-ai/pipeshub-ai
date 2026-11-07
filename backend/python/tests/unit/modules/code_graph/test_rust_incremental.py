"""Rust incremental builds: an impl's heritage edge starts at its type, in another file, so re-resolving either
the impl's file or the type's file must leave the edges a full build writes."""
import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.edge_builder import build_code_graph_edges

from .conftest import GROUP_ID, ORG_ID
from .test_import_binding import _build, _targets
from .test_rust_resolution import _repo

IMPLEMENTS = RecordRelations.IMPLEMENTS.value
STORE = "core/src/store.rs::trait:Store"
FILES = {
    "core/src/lib.rs": b"pub mod store;\npub mod row;\npub mod impls;\n",
    "core/src/store.rs": b"pub trait Store { fn save(&self); }\n",
    "core/src/row.rs": b"pub struct Row {}\n",
    "core/src/impls.rs": b"use crate::store::Store;\nuse crate::row::Row;\nimpl Store for Row { fn save(&self) {} }\n",
}
# Through a glob: no IMPORTS edge ties the impl's file to the type's.
GLOB = {**FILES, "core/src/impls.rs": b"use crate::store::Store;\nuse crate::row::*;\nimpl Store for Row { fn save(&self) {} }\n"}


async def _incremental(graph, index_file, record_id: str, path: str, source: bytes) -> set[str]:
    graph.changed_names.clear()
    await index_file(record_id, path, source, "rust")
    await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                 touched_record_ids={record_id}, facts_source=graph)
    incremental = _targets(graph, IMPLEMENTS, "core/src/row.rs")
    await _build(graph)
    assert _targets(graph, IMPLEMENTS, "core/src/row.rs") == incremental   # what a full build writes
    return incremental


@pytest.mark.asyncio
@pytest.mark.parametrize("files", [FILES, GLOB], ids=["use", "glob"])
async def test_removing_an_impl_drops_its_type_s_heritage(graph, index_file, files) -> None:
    await _repo(index_file, files)
    await _build(graph)
    assert _targets(graph, IMPLEMENTS, "core/src/row.rs") == {STORE}
    assert await _incremental(graph, index_file, "r3", "core/src/impls.rs", b"use crate::store::Store;\n") == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("files", [FILES, GLOB], ids=["use", "glob"])
async def test_editing_the_type_s_file_keeps_heritage_from_an_impl_elsewhere(graph, index_file, files) -> None:
    await _repo(index_file, files)
    await _build(graph)
    edited = b"pub struct Row {}\npub fn helper() {}\n"
    assert await _incremental(graph, index_file, "r2", "core/src/row.rs", edited) == {STORE}


TYPES = 6


@pytest.mark.asyncio
async def test_a_one_file_change_reads_only_the_impls_of_the_types_it_uses(graph, index_file) -> None:
    files = {"app/src/lib.rs": b"".join(b"pub mod t%d;\npub mod i%d;\n" % (n, n) for n in range(TYPES)) + b"pub mod run;\n",
             **{f"app/src/t{n}.rs": b"pub struct T%d {}\n" % n for n in range(TYPES)},
             **{f"app/src/i{n}.rs": b"use crate::t%d::T%d;\nimpl T%d { pub fn go(&self) {} }\n" % (n, n, n)
                for n in range(TYPES)},
             "app/src/run.rs": b"use crate::t0::T0;\nfn f(t: &T0) { t.go(); }\n"}
    await _repo(index_file, files)
    await _build(graph)
    run_id = f"r{list(files).index('app/src/run.rs')}"
    graph.changed_names.clear()
    await index_file(run_id, "app/src/run.rs", b"use crate::t0::T0;\nfn f(t: &T0) { t.go(); t.go(); }\n", "rust")
    result = await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                          touched_record_ids={run_id}, facts_source=graph)
    assert result.files_reresolved == 2   # run.rs and i0.rs, whose `use` names T0's impl; not every impl's file
    calls = _targets(graph, RecordRelations.CALLS.value, "app/src/run.rs")
    assert calls == {"app/src/i0.rs::method:T0.go"}
    await _build(graph)
    assert _targets(graph, RecordRelations.CALLS.value, "app/src/run.rs") == calls


CHAIN = {
    "app/src/lib.rs": b"pub mod db;\npub mod builder;\npub mod other;\npub mod run;\n",
    "app/src/db.rs": b"use crate::builder::Builder;\npub struct Db {}\n"
                     b"impl Db {\n    pub fn builder() -> Builder { todo!() }\n    pub fn pool(&self) {}\n}\n",
    "app/src/builder.rs": b"use crate::db::Db;\npub struct Builder {}\n"
                          b"impl Builder {\n    pub fn open(&self) -> Result<Db, E> { todo!() }\n}\n",
    "app/src/other.rs": b"pub struct Db {}\nimpl Db {\n    pub fn pool(&self) {}\n}\n",
    "app/src/run.rs": b"use crate::db::Db as Common;\nfn go() { Common::builder().open().unwrap().pool(); }\n",
}


@pytest.mark.asyncio
async def test_a_file_read_for_its_types_can_still_be_bound_in_a_later_round(graph, index_file) -> None:
    # builder.rs is first read for its return types; only then does `open()`'s `Db` (one of two) need its `use`.
    await _repo(index_file, CHAIN)
    await _build(graph)
    calls = RecordRelations.CALLS.value
    full = _targets(graph, calls, "app/src/run.rs")
    assert "app/src/db.rs::method:Db.pool" in full
    graph.changed_names.clear()
    await index_file("r4", "app/src/run.rs", CHAIN["app/src/run.rs"] + b"fn more() {}\n", "rust")
    await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                 touched_record_ids={"r4"}, facts_source=graph)
    assert _targets(graph, calls, "app/src/run.rs") == full
