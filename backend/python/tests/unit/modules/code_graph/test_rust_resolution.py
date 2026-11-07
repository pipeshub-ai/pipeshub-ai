"""Rust resolution: `use` paths across crates and re-exports, impls and their type, bare calls."""
import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.family_rules import rules_for
from app.modules.code_graph.resolvers import RESOLVERS, rust

from .test_import_binding import _build, _targets

CALLS, IMPORTS, IMPORTS_FROM, IMPLEMENTS = (RecordRelations.CALLS.value, RecordRelations.IMPORTS.value,
                                            RecordRelations.IMPORTS_FROM.value, RecordRelations.IMPLEMENTS.value)


async def _repo(index_file, files: dict[str, bytes]) -> None:
    for i, (path, src) in enumerate(files.items()):
        await index_file(f"r{i}", path, src, "rust")


CRATE = {
    "client/src/lib.rs": b"pub mod model;\npub use model::*;\n",
    "client/src/model.rs": b"pub struct History {}\nimpl History {\n    pub fn new() -> Self { History {} }\n"
                           b"    pub fn push(&self) { self.store(); }\n    fn store(&self) {}\n}\n",
}


@pytest.mark.asyncio
async def test_a_use_through_a_re_export_reaches_the_definition_in_another_crate(graph, index_file) -> None:
    await _repo(index_file, {**CRATE, "app/src/main.rs": b"use client::History;\n\nfn main() {\n"
                                                         b"    let h = History::new();\n    h.push();\n}\n"})
    await _build(graph)
    assert {t.split("::")[0] for t in _targets(graph, IMPORTS, "app/src/main.rs")} == {"client/src/model.rs"}
    assert _targets(graph, IMPORTS_FROM, "app/src/main.rs") == {"client/src/model.rs"}
    calls = _targets(graph, CALLS, "app/src/main.rs")
    assert {"client/src/model.rs::method:History.new", "client/src/model.rs::method:History.push"} <= calls


@pytest.mark.asyncio
async def test_self_calls_in_an_impl_reach_the_type_s_methods(graph, index_file) -> None:
    await _repo(index_file, CRATE)
    await _build(graph)
    assert "client/src/model.rs::method:History.store" in _targets(graph, CALLS, "client/src/model.rs")


@pytest.mark.asyncio
async def test_a_module_import_and_an_external_one_give_no_import_edge(graph, index_file) -> None:
    await _repo(index_file, {**CRATE, "client/src/use.rs": b"use crate::model;\nuse serde::Serialize;\n"})
    await _build(graph)
    assert _targets(graph, IMPORTS_FROM, "client/src/use.rs") == set()
    assert _targets(graph, IMPORTS, "client/src/use.rs") == set()


@pytest.mark.asyncio
async def test_an_impl_in_another_file_is_its_type_s_heritage(graph, index_file) -> None:
    await _repo(index_file, {
        "core/src/lib.rs": b"pub mod store;\npub mod row;\n",
        "core/src/store.rs": b"pub trait Store { fn save(&self); }\n",
        "core/src/row.rs": b"pub struct Row {}\n",
        "core/src/impls.rs": b"use crate::store::Store;\nuse crate::row::Row;\n"
                             b"impl Store for Row { fn save(&self) {} }\nimpl Store for Vec<u8> { fn save(&self) {} }\n",
    })
    await _build(graph)
    assert _targets(graph, IMPLEMENTS, "core/src/row.rs") == {"core/src/store.rs::trait:Store"}
    assert _targets(graph, IMPLEMENTS, "core/src/impls.rs") == set()   # Vec is nobody's here


QUERY = {
    "core/src/lib.rs": b"pub mod query;\npub mod rows;\n",
    "core/src/query.rs": b"pub trait FromRow { fn from_row(); }\n",
}


@pytest.mark.asyncio
async def test_a_trait_qualified_by_another_crate_is_never_a_same_named_repo_trait(graph, index_file) -> None:
    await _repo(index_file, {**QUERY, "core/src/rows.rs": b"pub struct Row {}\n"
                                                          b"impl<'r> sqlx::FromRow<'r, SqliteRow> for Row {}\n"})
    await _build(graph)
    assert _targets(graph, IMPLEMENTS, "core/src/rows.rs") == set()


@pytest.mark.asyncio
async def test_a_trait_qualified_by_a_crate_path_binds_to_its_definition(graph, index_file) -> None:
    await _repo(index_file, {**QUERY, "core/src/rows.rs": b"pub struct Row {}\nimpl crate::query::FromRow for Row {}\n",
                             "other/src/lib.rs": b"pub trait FromRow {}\n"})   # a same-named trait elsewhere
    await _build(graph)
    assert _targets(graph, IMPLEMENTS, "core/src/rows.rs") == {"core/src/query.rs::trait:FromRow"}
    edge = next(e for e in graph.code_edges(IMPLEMENTS))
    assert edge["confidence"] == "EXTRACTED" and edge["resolution"] == "qualifier"


PRELUDE_REPO = {
    "app/src/lib.rs": b"pub mod action;\npub mod marker;\npub mod a;\n",
    "app/src/action.rs": b"pub enum Copy {}\npub enum Action { Copy, Paste }\n",   # same-named enum and variant
    "app/src/marker.rs": b"pub trait Copy {}\n",
}


@pytest.mark.parametrize(("header", "want"), [
    (b"use std::marker::Copy;\n", set()),                                     # std's, imported by name
    (b"use crate::action::Action::{Copy, Paste};\n", set()),                  # an enum variant, not a trait
    (b"", set()),                                                             # the prelude's
    (b"use crate::marker::Copy;\n", {"app/src/marker.rs::trait:Copy"}),       # a repo trait, imported
])
@pytest.mark.asyncio
async def test_a_prelude_trait_name_is_std_s_unless_a_repo_trait_is_bound(graph, index_file, header, want) -> None:
    await _repo(index_file, {**PRELUDE_REPO, "app/src/a.rs": header + b"pub struct A {}\nimpl Copy for A {}\n"})
    await _build(graph)
    assert _targets(graph, IMPLEMENTS, "app/src/a.rs") == want


@pytest.mark.asyncio
async def test_a_prelude_trait_name_the_file_defines_is_that_trait(graph, index_file) -> None:
    await _repo(index_file, {"x/src/lib.rs": b"pub trait Clone {}\npub struct A {}\nimpl Clone for A {}\n"})
    await _build(graph)
    assert _targets(graph, IMPLEMENTS, "x/src/lib.rs") == {"x/src/lib.rs::trait:Clone"}


@pytest.mark.asyncio
async def test_a_bare_call_is_a_function_never_a_method(graph, index_file) -> None:
    await _repo(index_file, {**CRATE, "client/src/run.rs": b"pub fn store() {}\nfn go() { store(); push(); }\n"})
    await _build(graph)
    assert _targets(graph, CALLS, "client/src/run.rs") == {"client/src/run.rs::function:store"}


def test_the_resolver_names_module_files_and_needs_an_item() -> None:
    files = {"a/src/lib.rs", "a/src/db/mod.rs", "a/src/db/query.rs", "b/src/main.rs"}
    assert rust.resolve("crate::db::query::Q", "a/src/lib.rs", files, None) == "a/src/db/query.rs"
    # db's parent is the crate root; without a build index nothing says whether lib.rs defines `query`
    assert rust.resolve("super::query::Q", "a/src/db/mod.rs", files, None) == "a/src/lib.rs"
    assert rust.resolve("self::query::Q", "a/src/db/mod.rs", files, None) == "a/src/db/query.rs"
    assert rust.resolve("super::Pool", "a/src/db/query.rs", files, None) == "a/src/db/mod.rs"
    assert rust.resolve("a::db::Pool", "b/src/main.rs", files, None) == "a/src/db/mod.rs"   # a crate by its folder
    assert rust.resolve("crate::db", "a/src/lib.rs", files, None) is None                  # a module, not an item
    assert rust.resolve("std::fmt::Display", "a/src/lib.rs", files, None) is None


def test_a_rust_file_keeps_its_imports_on_its_nodes() -> None:
    # Any Rust module can re-export with `pub use`; an incremental build follows those hops from the nodes alone.
    from app.modules.code_graph.block_projection import _is_package_init
    assert _is_package_init("a/src/time.rs") and _is_package_init("a/src/lib.rs") and _is_package_init("pkg/__init__.py")
    assert not _is_package_init("pkg/mod.py") and not _is_package_init(None)


TWO_CRATES = {
    "net/src/lib.rs": b"pub struct Client {}\nimpl Client { pub fn bind(&self) {} }\n",
    "common/src/lib.rs": b"pub mod settings;\npub trait Ext { fn go(self); }\npub trait FromSettings {}\n",
    "common/src/settings.rs": b"pub struct LogConfig {}\n",
}


@pytest.mark.asyncio
async def test_an_impl_on_a_type_imported_from_outside_the_repo_is_nobody_s(graph, index_file) -> None:
    # `use reqwest::Client` names reqwest's Client, not the repo's `net::Client`.
    await _repo(index_file, {**TWO_CRATES, "app/src/ext.rs": b"use reqwest::Client;\nuse common::Ext;\n"
                                                            b"impl Ext for Client { fn go(self) { self.bind(); } }\n"})
    await _build(graph)
    assert _targets(graph, IMPLEMENTS, "net/src/lib.rs") == set()
    assert _targets(graph, CALLS, "app/src/ext.rs") == set()


@pytest.mark.asyncio
async def test_an_impl_on_an_imported_type_is_that_type_s_not_a_nearer_namesake(graph, index_file) -> None:
    # the impl's own crate defines a LogConfig too; the `use` decides.
    await _repo(index_file, {**TWO_CRATES,
                             "app/src/lib.rs": b"pub mod settings;\npub mod logs;\n",
                             "app/src/settings.rs": b"pub struct LogConfig {}\n",
                             "app/src/logs.rs": b"use common::settings::LogConfig;\nuse common::FromSettings;\n"
                                                b"impl FromSettings for LogConfig {}\n"})
    await _build(graph)
    assert _targets(graph, IMPLEMENTS, "common/src/settings.rs") == {"common/src/lib.rs::trait:FromSettings"}
    assert _targets(graph, IMPLEMENTS, "app/src/settings.rs") == set()


@pytest.mark.asyncio
async def test_a_call_never_lands_on_an_associated_type_a_module_or_a_field(graph, index_file) -> None:
    # `Err(..)` and the variant `Content::Error(..)` are not the impls' `type Err` / `type Error`.
    await _repo(index_file, {
        "c/src/lib.rs": b"pub mod id;\npub mod content;\npub mod record;\n",
        "c/src/id.rs": b"pub struct Id(u8);\nimpl std::str::FromStr for Id {\n    type Err = String;\n"
                       b"    fn from_str(s: &str) -> Result<Self, Self::Err> { Err(String::new()) }\n}\n",
        "c/src/content.rs": b"pub enum Content { Error(String) }\nimpl TryFrom<u8> for Content {\n"
                            b"    type Error = String;\n"
                            b"    fn try_from(v: u8) -> Result<Self, Self::Error> { Ok(Content::Error(v.to_string())) }\n}\n",
        "c/src/record.rs": b"mod tag;\npub struct Record { pub tag: u8 }\n",
        "c/src/use.rs": b"use crate::id::Id;\nuse crate::content::Content;\nuse crate::record::Record;\n"
                        b"fn go() -> Result<Id, String> {\n    let c = Content::Error(String::new());\n"
                        b"    crate::record::tag();\n    let i = Id(1);\n    Err(String::new())\n}\n",
    })
    await _build(graph)
    calls = set().union(*(_targets(graph, CALLS, f"c/src/{f}.rs") for f in ("id", "content", "use")))
    assert not any(k in t for t in calls for k in ("type_alias:", "module:", "field:")), calls
    assert "c/src/id.rs::struct:Id" in _targets(graph, CALLS, "c/src/use.rs")   # a tuple struct still links


@pytest.mark.asyncio
async def test_an_item_import_scopes_only_that_item(graph, index_file) -> None:
    # `use crate::m::Item` does not put m's `helper` in scope.
    await _repo(index_file, {
        "k/src/lib.rs": b"pub mod m;\npub mod a;\n",
        "k/src/m.rs": b"pub struct Item {}\npub fn helper() {}\n",
        "k/src/a.rs": b"use crate::m::Item;\nfn go() { helper(); }\n",
        "k/src/b.rs": b"pub fn helper() {}\n",
    })
    await _build(graph)
    assert "k/src/m.rs::function:helper" not in _targets(graph, CALLS, "k/src/a.rs")


@pytest.mark.parametrize("use_line, call", [
    (b"use crate::m;", b"m::f();"),                       # crate::
    (b"use self::m;", b"m::f();"),                        # self::
], ids=["crate", "self"])
@pytest.mark.asyncio
async def test_a_module_path_call_reaches_the_module_s_function(graph, index_file, use_line, call) -> None:
    await _repo(index_file, {
        "app/src/main.rs": b"mod m;\n" + use_line + b"\nfn main() { " + call + b" }\n",
        "app/src/m/mod.rs": b"pub fn f() {}\n",
        "app/src/other.rs": b"pub fn f() {}\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "app/src/main.rs") == {"app/src/m/mod.rs::function:f"}


@pytest.mark.asyncio
async def test_a_path_into_another_repo_crate_reaches_its_function(graph, index_file) -> None:
    await _repo(index_file, {
        "app/src/main.rs": b"use other::db;\nfn main() { db::append(); }\n",
        "other/src/lib.rs": b"pub mod db;\n",
        "other/src/db/mod.rs": b"pub fn append() {}\n",
        "app/src/store/db.rs": b"pub fn append() {}\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "app/src/main.rs") == {"other/src/db/mod.rs::function:append"}


@pytest.mark.asyncio
async def test_a_type_a_use_binds_outside_the_repo_is_never_a_same_named_repo_type(graph, index_file) -> None:
    await _repo(index_file, {
        "app/src/main.rs": b"use reqwest::Client;\nfn main() { let c = Client::new(); c.send(); }\n",
        "app/src/client.rs": b"pub struct Client {}\nimpl Client {\n    pub fn new() -> Self { Client {} }\n"
                             b"    pub fn send(&self) {}\n}\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "app/src/main.rs") == set()


def test_the_rust_row_and_its_extension() -> None:
    rules = rules_for("rust")
    assert rules.bare_call_reaches_method == "never" and "Self" in rules.self_receivers
    assert not rules.bare_names_from_imported_modules
    assert rules.uncallable_kinds == {"module", "field", "type_alias"} and not rules_for("py").uncallable_kinds
    assert rules.enclosing_type is rust.impl_type and rules.out_of_line_methods == "repo"
    assert rules.qualified_path_separator == "::" and rules_for("java").qualified_path_separator is None
    assert RESOLVERS["rust"] is rust.resolve
