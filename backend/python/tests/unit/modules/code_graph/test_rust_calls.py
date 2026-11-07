"""Rust calls the builder resolves: module paths, receiver chains and let-bound receivers, trait
methods through impls and bounds, impls on types the repo does not define, macro arguments."""
import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.edge_builder import build_code_graph_edges

from .conftest import GROUP_ID, ORG_ID
from .test_import_binding import _build, _targets
from .test_rust_resolution import _repo

CALLS = RecordRelations.CALLS.value
LIB = {"app/src/lib.rs": b"pub mod search;\npub mod util;\npub mod run;\n"}
SEARCH = (b"pub struct SearcherBuilder { n: u8 }\npub struct Searcher { line_number: bool }\n"
          b"impl SearcherBuilder {\n    pub fn new() -> SearcherBuilder { todo!() }\n"
          b"    pub fn line_number(&mut self, yes: bool) -> &mut SearcherBuilder { self }\n"
          b"    pub fn build(&self) -> Result<Searcher, Error> { todo!() }\n}\n"
          b"impl Searcher {\n    pub fn line_number(&self) -> bool { self.line_number }\n"
          b"    pub fn search(&self) {}\n}\n")


async def _calls(graph, index_file, run: bytes, **more: bytes) -> set[str]:
    await _repo(index_file, {**LIB, "app/src/search.rs": SEARCH, "app/src/run.rs": run, **more})
    await _build(graph)
    return _targets(graph, CALLS, "app/src/run.rs")


@pytest.mark.asyncio
async def test_a_builder_chain_reaches_each_method_through_return_types(graph, index_file) -> None:
    run = (b"use crate::search::SearcherBuilder;\n"
           b"fn go() { SearcherBuilder::new().line_number(true).build().unwrap().search(); }\n")
    assert await _calls(graph, index_file, run) == {
        "app/src/search.rs::method:SearcherBuilder.new", "app/src/search.rs::method:SearcherBuilder.line_number",
        "app/src/search.rs::method:SearcherBuilder.build", "app/src/search.rs::method:Searcher.search"}


@pytest.mark.asyncio
async def test_a_let_bound_receiver_is_typed_by_what_it_was_bound_to(graph, index_file) -> None:
    run = (b"use crate::search::SearcherBuilder;\nfn go() {\n    let b = SearcherBuilder::new();\n"
           b"    let s = b.build()?;\n    s.search();\n}\n")
    assert "app/src/search.rs::method:Searcher.search" in await _calls(graph, index_file, run)


@pytest.mark.asyncio
async def test_a_getter_named_like_its_field_is_the_method_called(graph, index_file) -> None:
    run = b"use crate::search::Searcher;\nfn go(s: &Searcher) { s.line_number(); }\n"
    assert await _calls(graph, index_file, run) == {"app/src/search.rs::method:Searcher.line_number"}


@pytest.mark.asyncio
async def test_a_chain_whose_head_has_no_repo_type_reaches_nothing(graph, index_file) -> None:
    run = b"fn go() { reqwest::Client::new().search(); }\n"
    assert await _calls(graph, index_file, run) == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("run", [
    b"fn go() { crate::util::check(); }\n",
    b"use crate::util;\nfn go() { util::check(); }\n",
    b"mod tests {\n    use super::super::util;\n    fn t() { util::check(); }\n}\n",
])
async def test_a_module_path_call_reaches_the_module_s_function(graph, index_file, run) -> None:
    util = {"app/src/util.rs": b"pub fn check() {}\n", "app/src/other.rs": b"pub fn check() {}\n"}
    assert await _calls(graph, index_file, run, **util) == {"app/src/util.rs::function:check"}


@pytest.mark.asyncio
async def test_a_path_into_an_inline_module_reaches_its_function(graph, index_file) -> None:
    util = {"app/src/util.rs": b"pub mod testing {\n    pub fn synced() {}\n}\npub fn synced() {}\n"}
    run = b"mod convert {\n    pub fn usize() {}\n}\nfn go() { crate::util::testing::synced(); convert::usize(); }\n"
    assert await _calls(graph, index_file, run, **util) == {
        "app/src/util.rs::function:testing.synced", "app/src/run.rs::function:convert.usize"}


@pytest.mark.asyncio
async def test_an_outside_path_never_reaches_a_same_named_repo_function(graph, index_file) -> None:
    util = {"app/src/util.rs": b"pub fn take() {}\n"}
    assert await _calls(graph, index_file, b"fn go() { std::mem::take(); }\n", **util) == set()


@pytest.mark.asyncio
async def test_a_type_in_an_inline_module_has_its_own_methods(graph, index_file) -> None:
    nested = (b"mod tests {\n    struct Harness {}\n    impl Harness {\n        fn new() -> Self { todo!() }\n"
              b"        fn press(&self) {}\n    }\n    fn t() { let h = Harness::new(); h.press(); }\n}\n")
    top = {"app/src/util.rs": b"struct Harness {}\nimpl Harness {\n    fn new() -> Self { todo!() }\n}\n"}
    assert await _calls(graph, index_file, nested, **top) == {
        "app/src/run.rs::method:tests.Harness.new", "app/src/run.rs::method:tests.Harness.press"}


TRAITS = (b"pub trait Matcher {\n    fn find(&self);\n    fn is_match(&self) { self.find(); }\n}\n"
          b"pub struct Regex {}\nimpl Matcher for Regex {\n    fn find(&self) {}\n}\n")


@pytest.mark.asyncio
async def test_a_trait_s_default_method_is_reached_through_an_impl(graph, index_file) -> None:
    run = b"use crate::util::Regex;\nfn go(r: &Regex) { r.is_match(); r.find(); }\n"
    assert await _calls(graph, index_file, run, **{"app/src/util.rs": TRAITS}) == {
        "app/src/util.rs::method:Matcher.is_match", "app/src/util.rs::method:Regex.find"}


@pytest.mark.asyncio
@pytest.mark.parametrize("run", [
    b"use crate::util::Matcher;\nfn go<M: Matcher>(m: M) { m.find(); }\n",
    b"use crate::util::Matcher;\nfn go<M>(m: &M) where M: Matcher { m.find(); }\n",
    b"use crate::util::Matcher;\nfn go(m: &dyn Matcher) { m.find(); }\n",
    b"use crate::util::Matcher;\nstruct Core<M: Matcher> { m: M }\n"
    b"impl<M: Matcher> Core<M> { fn go(&self) { self.m.find(); } }\n",
])
async def test_a_call_on_a_bounded_type_parameter_or_trait_object_is_the_trait_s(graph, index_file, run) -> None:
    assert "app/src/util.rs::method:Matcher.find" in await _calls(graph, index_file, run, **{"app/src/util.rs": TRAITS})


EXT = {"app/src/util.rs": b"pub trait PathBufExt { fn joined(&self) -> PathBuf; fn push(&mut self); }\n"
                          b"impl PathBufExt for PathBuf {\n    fn joined(&self) -> PathBuf { todo!() }\n"
                          b"    fn push(&mut self) {}\n}\n"
                          b"pub struct Parsed {}\nimpl Parsed {\n    pub fn tokens(&self) {}\n}\n"}


@pytest.mark.asyncio
@pytest.mark.parametrize("use", [b"use crate::util::PathBufExt;\n", b"use crate::util::PathBufExt as _;\n"])
async def test_a_repo_trait_s_impl_on_an_outside_type_gives_it_methods_where_used(graph, index_file, use) -> None:
    run = b"use std::path::PathBuf;\n" + use + b"fn go(p: PathBuf) { p.joined().joined(); }\n"
    assert await _calls(graph, index_file, run, **EXT) == {"app/src/util.rs::method:PathBuf.joined"}


@pytest.mark.asyncio
async def test_a_method_of_a_trait_the_file_does_not_use_is_the_outside_type_s_own(graph, index_file) -> None:
    run = b"use std::path::PathBuf;\nfn go(p: &mut PathBuf) { p.push(); p.joined(); }\n"
    assert await _calls(graph, index_file, run, **EXT) == set()


@pytest.mark.asyncio
async def test_a_type_imported_under_another_name_types_its_receiver(graph, index_file) -> None:
    run = b"use crate::search::Searcher as Engine;\nfn go(e: &Engine) { e.search(); }\n"
    assert await _calls(graph, index_file, run) == {"app/src/search.rs::method:Searcher.search"}


@pytest.mark.asyncio
@pytest.mark.parametrize("use", [b"use app as engine;\n", b"use app::{self as engine};\n",
                                 b"use crate::search::{self as engine};\n"])
async def test_a_type_path_through_a_module_alias_types_its_receiver(graph, index_file, use) -> None:
    path = b"engine::search::Searcher" if b"app" in use else b"engine::Searcher"
    run = use + b"fn go(e: &" + path + b") { e.search(); }\n"
    # A second `Searcher` makes the bare name ambiguous: only the path through the alias places it.
    other = {"app/src/util.rs": b"pub struct Searcher {}\nimpl Searcher {\n    pub fn search(&self) {}\n}\n"}
    assert await _calls(graph, index_file, run, **other) == {"app/src/search.rs::method:Searcher.search"}


@pytest.mark.asyncio
async def test_calls_in_macro_arguments_are_edges(graph, index_file) -> None:
    run = (b"use crate::search::Searcher;\n"
           b'fn go(s: &Searcher) { assert!(s.line_number(), "{}", format!("{}", crate::util::check())); }\n')
    util = {"app/src/util.rs": b"pub fn check() {}\n"}
    assert await _calls(graph, index_file, run, **util) == {
        "app/src/search.rs::method:Searcher.line_number", "app/src/util.rs::function:check"}


@pytest.mark.asyncio
async def test_an_incremental_build_follows_a_renamed_method_in_a_chain(graph, index_file) -> None:
    run = b"use crate::search::SearcherBuilder;\nfn go() { SearcherBuilder::new().build().unwrap().search(); }\n"
    assert "app/src/search.rs::method:Searcher.search" in await _calls(graph, index_file, run)
    graph.changed_names.clear()
    await index_file("r1", "app/src/search.rs", SEARCH.replace(b"fn build(", b"fn finish("), "rust")
    await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                 touched_record_ids={"r1"}, facts_source=graph)
    incremental = _targets(graph, CALLS, "app/src/run.rs")
    assert incremental == {"app/src/search.rs::method:SearcherBuilder.new"}   # `build` is gone, and so is the chain
    await _build(graph)
    assert _targets(graph, CALLS, "app/src/run.rs") == incremental


@pytest.mark.asyncio
async def test_a_derived_default_is_a_value_of_its_type(graph, index_file) -> None:
    run = b"use crate::search::Searcher;\nfn go() { Searcher::default().search(); }\n"
    assert await _calls(graph, index_file, run) == {"app/src/search.rs::method:Searcher.search"}


@pytest.mark.asyncio
@pytest.mark.parametrize("util", [b"pub fn spawn() {}\n", b"pub fn other() {}\n"])   # named in the repo or not
@pytest.mark.parametrize("use", [b"use std::thread;\n", b"use std::{thread, io};\n", b"use std::thread::{self};\n"])
async def test_a_module_path_call_into_a_library_never_reaches_a_repo_function(graph, index_file, util,
                                                                              use) -> None:
    run = use + b"fn go() { thread::spawn(|| 1); }\n"
    await _calls(graph, index_file, run, **{"app/src/util.rs": util})
    assert _targets(graph, CALLS, "app/src/run.rs") == set()


CFG = {"app/src/util.rs": b"pub struct Colors {}\nimpl Colors { pub fn matched(&self) {} }\n"
                          b"pub struct Paint {}\nimpl Paint { pub fn matched(&self) {} }\n",
       "app/src/search.rs": b"use crate::util::Colors;\nuse crate::util::Paint;\n"
                            b"pub struct Config { pub colors: Colors }\n",
       "app/src/app.rs": b"use crate::search::Config;\npub struct App { pub config: Config }\n"}


@pytest.mark.asyncio
async def test_an_incremental_build_follows_a_field_whose_type_changed(graph, index_file) -> None:
    # run.rs has no edge into search.rs, where the field changes: only its chain through `colors` ties them.
    run = b"use crate::app::App;\nfn go(a: &App) { a.config.colors.matched(); }\n"
    lib = {"app/src/lib.rs": b"pub mod search;\npub mod util;\npub mod run;\npub mod app;\n"}
    assert await _calls(graph, index_file, run, **CFG, **lib) == {"app/src/util.rs::method:Colors.matched"}
    graph.changed_names.clear()
    await index_file("r1", "app/src/search.rs", CFG["app/src/search.rs"].replace(b"colors: Colors", b"colors: Paint"),
                     "rust")
    await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                 touched_record_ids={"r1"}, facts_source=graph)
    assert _targets(graph, CALLS, "app/src/run.rs") == {"app/src/util.rs::method:Paint.matched"}


STACKS = {"app/src/util.rs": b"pub trait Stack { fn push(&mut self); }\n"
                             b"impl Stack for Vec<u8> { fn push(&mut self) {} }\n"
                             b"pub trait Peek { fn peek(&self); }\nimpl Peek for Vec<u8> { fn peek(&self) {} }\n"}


@pytest.mark.asyncio
async def test_only_the_impl_s_own_trait_must_be_in_scope(graph, index_file) -> None:
    run = b"use crate::util::Peek;\nfn go(v: &mut Vec<u8>) { v.push(); v.peek(); }\n"
    assert await _calls(graph, index_file, run, **STACKS) == {"app/src/util.rs::method:Vec.peek"}


@pytest.mark.asyncio
async def test_an_outside_trait_s_method_on_an_outside_type_is_no_repo_call(graph, index_file) -> None:
    util = {"app/src/util.rs": b"impl AsRef<str> for Vec<u8> {\n    fn as_ref(&self) -> &str { todo!() }\n}\n"}
    run = b"fn go(v: &Vec<u8>) { v.as_ref(); }\n"
    assert await _calls(graph, index_file, run, **util) == set()


@pytest.mark.asyncio
async def test_an_impl_belongs_to_the_type_beside_it_whatever_a_use_binds(graph, index_file) -> None:
    case = (b"struct Case {}\nimpl Case {\n    fn db(&self) {}\n}\nmod inner {\n    use super::Case;\n}\n"
            b"fn go(c: &Case) { c.db(); }\n")
    await _repo(index_file, {**LIB, "app/src/search.rs": SEARCH, "app/tests/watch.rs": case,
                             "app/src/util.rs": b"pub struct Case {}\n"})
    await _build(graph)
    assert _targets(graph, CALLS, "app/tests/watch.rs") == {"app/tests/watch.rs::method:Case.db"}


WRAPPED = {"app/src/util.rs": b"pub struct Foo {}\nimpl Foo {\n    pub fn map(&self) {}\n    pub fn take(&self) {}\n"
                              b"    pub fn lock(&self) {}\n    pub fn ok(&self) {}\n"
                              b"    pub fn clone(&self) -> Foo { todo!() }\n}\n"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("body", "expected"), [
    (b"fn go(o: Option<Foo>) { o.map(|x| x); }", set()),   # Option's map
    (b"fn go(a: Arc<Foo>) { a.clone(); }", set()),         # Arc's clone
    (b"fn go(m: Mutex<Foo>) { m.lock(); }", set()),
    (b"fn go(r: Result<Foo, E>) { r.ok(); }", set()),
    (b"fn go(o: Option<Foo>) { o.as_ref().unwrap().take(); }", {"app/src/util.rs::method:Foo.take"}),
    (b"fn go(m: Mutex<Foo>) { m.lock().unwrap().take(); }", {"app/src/util.rs::method:Foo.take"}),
    (b"fn go(a: Arc<Foo>) { a.take(); }", {"app/src/util.rs::method:Foo.take"}),   # through the deref
    (b"fn go(r: Result<Foo, E>) -> Result<(), E> { r?.take(); Ok(()) }", {"app/src/util.rs::method:Foo.take"}),
])
async def test_a_wrapper_s_own_methods_are_not_the_value_s(graph, index_file, body, expected) -> None:
    run = b"use crate::util::Foo;\nuse std::sync::{Arc, Mutex};\n" + body + b"\n"
    assert await _calls(graph, index_file, run, **WRAPPED) == expected


@pytest.mark.asyncio
async def test_an_outside_type_path_is_no_same_named_repo_type(graph, index_file) -> None:
    util = {"app/src/util.rs": b"pub struct Client {}\nimpl Client {\n    pub fn get(&self) {}\n}\n"}
    run = b"fn go(c: &reqwest::Client) { c.get(); }\n"
    assert await _calls(graph, index_file, run, **util) == set()


@pytest.mark.asyncio
async def test_a_nested_fn_s_parameter_does_not_type_its_parent_s_variable(graph, index_file) -> None:
    run = (b"use crate::search::{Searcher, SearcherBuilder};\n"
           b"fn go() {\n    let s = SearcherBuilder::new();\n    fn inner(s: Searcher) {}\n"
           b"    s.line_number(true);\n}\n")
    assert "app/src/search.rs::method:SearcherBuilder.line_number" in await _calls(graph, index_file, run)


OUTSIDE_NAMES = {"app/src/util.rs": b"pub fn from_utf8() {}\npub fn stdout() {}\npub fn f() {}\npub fn sleep() {}\n"
                                    b"pub struct Nushell;\nimpl Nushell { pub fn generate(&self) {} }\n"}


@pytest.mark.asyncio
@pytest.mark.parametrize("run", [
    b"use std::str;\nfn go() { std::str::from_utf8(b\"x\"); }\n",
    b"use std::io::{self, Write};\nfn go() { std::io::stdout(); }\n",
    b"use some_crate::m;\nfn go() { some_crate::m::f(); }\n",
    b"fn go() { tokio::time::sleep(1); }\n",   # no `use` at all
    b"use clap_complete_nushell::Nushell;\nfn go() { Nushell.generate(1); }\n",   # a library's unit struct value
], ids=["std-path", "std-self", "crate-path", "no-use", "unit-struct"])
async def test_a_library_call_never_reaches_a_same_named_repo_definition(graph, index_file, run) -> None:
    assert await _calls(graph, index_file, run, **OUTSIDE_NAMES) == set()


@pytest.mark.asyncio
async def test_a_known_value_s_missing_method_is_not_a_same_named_variable_s(graph, index_file) -> None:
    # The field `f` is an Option: `take()` is Option's, whatever the file-wide table says of a name `f`.
    run = b"use crate::util::Foo;\nstruct S { f: Option<Foo> }\nimpl S { fn go(&mut self) { self.f.take(); } }\n"
    assert await _calls(graph, index_file, run, **WRAPPED) == set()


@pytest.mark.asyncio
async def test_a_parameter_of_an_unread_type_is_not_another_fn_s_variable(graph, index_file) -> None:
    run = (b"use crate::search::Searcher;\nfn other(s: &Searcher) {}\n"
           b"fn go(s: impl AsRef<str>) { s.search(); }\n")
    assert await _calls(graph, index_file, run) == set()


@pytest.mark.asyncio
async def test_a_result_alias_holds_its_value(graph, index_file) -> None:
    run = (b"use crate::util::Foo;\nfn mk() -> FormatResult<Foo> { todo!() }\n"
           b"fn go() -> FormatResult<()> { mk()?.take(); mk().take(); Ok(()) }\n")
    assert await _calls(graph, index_file, run, **WRAPPED) == {"app/src/run.rs::function:mk",
                                                              "app/src/util.rs::method:Foo.take"}
