"""C++ calls the graph missed on oatpp, abseil and duckdb: template calls, receivers a forward declaration made
ambiguous, fields declared in the class's header, namespace-qualified calls, and constructions."""
import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.edge_builder import build_code_graph_edges

from .conftest import GROUP_ID, ORG_ID
from .test_import_binding import _build, _targets

CALLS = RecordRelations.CALLS.value

LIST_HPP = b"namespace ns {\nclass List {\n public:\n  void notify();\n};\n}\n"
LIST_CPP = b'#include "list.hpp"\nnamespace ns {\nvoid List::notify() {}\n}\n'
COND_HPP = (b'#include "list.hpp"\nnamespace ns {\nclass Cond {\n public:\n  void wake();\n private:\n'
            b"  List m_list;\n};\n}\n")
COND_CPP = b'#include "cond.hpp"\nnamespace ns {\nvoid Cond::wake() { m_list.notify(); this->m_list.notify(); }\n}\n'


@pytest.mark.asyncio
async def test_a_template_call_names_the_function_not_its_type_argument(graph, c_repo) -> None:
    await c_repo({
        "h/helper.hpp": (b"template <class T> T* make_uniq(int n) { return new T(n); }\n"
                         b"class Expr {\n public:\n  template <class T> T& Cast() { return *(T*)this; }\n};\n"
                         b"class Leaf : public Expr {\n public:\n  Leaf(int);\n};\n"),
        "src/use.cpp": (b'#include "h/helper.hpp"\n'
                        b"void use(Expr& e) { make_uniq<Leaf>(1); e.Cast<Leaf>(); e.template Cast<Leaf>(); }\n"),
    })
    await _build(graph)
    assert _targets(graph, CALLS, "src/use.cpp") == {"h/helper.hpp::function:make_uniq", "h/helper.hpp::method:Expr.Cast"}


@pytest.mark.asyncio
async def test_a_forward_declaration_is_no_second_class_for_a_typed_receiver(graph, c_repo) -> None:
    await c_repo({
        "a/fwd.hpp": b"namespace ns {\nclass List;\nvoid take(List* l);\n}\n",
        "a/list.hpp": LIST_HPP, "a/list.cpp": LIST_CPP,
        "a/use.cpp": b'#include "fwd.hpp"\n#include "list.hpp"\nnamespace ns {\nvoid go(List& l) { l.notify(); }\n}\n',
    })
    await _build(graph)
    assert _targets(graph, CALLS, "a/use.cpp") == {"a/list.cpp::method:ns.List.notify"}


@pytest.mark.asyncio
async def test_a_field_receiver_is_typed_by_its_class_in_the_header(graph, c_repo) -> None:
    await c_repo({"a/list.hpp": LIST_HPP, "a/list.cpp": LIST_CPP, "a/cond.hpp": COND_HPP, "a/cond.cpp": COND_CPP})
    await _build(graph)
    assert _targets(graph, CALLS, "a/cond.cpp") == {"a/list.cpp::method:ns.List.notify"}


@pytest.mark.asyncio
async def test_a_field_of_a_base_types_the_receiver_but_a_local_of_another_function_does_not(graph, c_repo) -> None:
    await c_repo({
        "a/list.hpp": LIST_HPP, "a/list.cpp": LIST_CPP, "a/cond.hpp": COND_HPP,
        "a/other.hpp": b"class Other {\n public:\n  void notify();\n};\n",
        "a/sub.cpp": (b'#include "cond.hpp"\n#include "other.hpp"\nnamespace ns {\nclass Sub : public Cond {\n'
                      b" public:\n  void run();\n};\nvoid Sub::run() { m_list.notify(); }\n"
                      b"void free_fn() { m_list.notify(); }\n}\n"),
    })
    await _build(graph)
    # free_fn is in no class, so its `m_list` is no field.
    assert _targets(graph, CALLS, "a/sub.cpp") == {"a/list.cpp::method:ns.List.notify"}


@pytest.mark.asyncio
async def test_a_receiver_is_typed_by_its_own_function_when_the_file_reuses_its_name(graph, c_repo) -> None:
    await c_repo({
        "a/two.hpp": b"class A {\n public:\n  void run() {}\n};\nclass B {\n public:\n  void stop() {}\n};\n",
        "a/use.cpp": b'#include "two.hpp"\nvoid f(A& x) { x.run(); }\nvoid g() { B x; x.stop(); }\n',
    })
    await _build(graph)
    assert _targets(graph, CALLS, "a/use.cpp") == {"a/two.hpp::method:A.run", "a/two.hpp::method:B.stop"}


@pytest.mark.asyncio
async def test_a_namespace_qualified_call_reaches_the_function_defined_in_another_source(graph, c_repo) -> None:
    await c_repo({
        "c/thread.hpp": b"namespace oatpp { namespace concurrency {\nint cores();\n}}\n",
        "c/thread.cpp": b'#include "thread.hpp"\nnamespace oatpp { namespace concurrency {\nint cores() { return 4; }\n}}\n',
        "c/other.cpp": b"namespace other {\nint cores() { return 1; }\n}\n",
        "c/exec.cpp": (b'#include "thread.hpp"\nint workers() { return oatpp::concurrency::cores(); }\n'
                       b"int local(int conn) { return conn.cores(); }\n"),
    })
    await _build(graph)
    assert _targets(graph, CALLS, "c/exec.cpp") == {"c/thread.cpp::function:oatpp.concurrency.cores"}


@pytest.mark.asyncio
async def test_a_class_nested_typedef_of_the_same_name_does_not_hide_the_class(graph, c_repo) -> None:
    await c_repo({
        "co/action.hpp": (b"namespace as {\nclass Action {\n public:\n  static Action create();\n};\n"
                          b"class Coroutine {\n public:\n  typedef as::Action Action;\n  Action next();\n};\n}\n"),
        "co/action.cpp": (b'#include "action.hpp"\nnamespace as {\nAction Action::create() { return Action(); }\n'
                          b"Action Coroutine::next() { return Action::create(); }\n}\n"),
    })
    await _build(graph)
    assert "co/action.cpp::method:as.Action.create" in _targets(graph, CALLS, "co/action.cpp")


@pytest.mark.asyncio
async def test_constructions_call_the_class_and_a_member_initializer_calls_nothing(graph, c_repo) -> None:
    await c_repo({
        "k/base.hpp": (b"namespace db {\nclass Base {\n public:\n  Base(int);\n};\nclass Conn {\n public:\n"
                       b"  Conn(int);\n};\ntypedef Conn ConnAlias;\nint count(int);\n}\n"),
        "k/derived.cpp": (b'#include "base.hpp"\nnamespace db {\nclass Derived : public Base {\n public:\n'
                          b"  Derived(int x);\n private:\n  int count;\n};\n"
                          b"Derived::Derived(int x) : Base(x), count(0) {}\n"
                          b"void make() { Conn c(1); auto* p = new db::Conn(2); ConnAlias a(3); std::string s(4); }\n}\n"),
        "t/hygiene_test.cpp": b"namespace not_std {\nclass string {};\n}\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "k/derived.cpp") == {"k/base.hpp::class:db.Base", "k/base.hpp::class:db.Conn"}


@pytest.mark.asyncio
async def test_a_header_retyping_a_base_field_rebinds_its_callers_on_an_incremental_build(graph, index_file) -> None:
    # sub.cpp includes base.hpp only through cond.hpp, so no include of its own names the edited header.
    base = b'#include "list.hpp"\n#include "other.hpp"\nnamespace ns {\nclass Base {\n protected:\n  List m_x;\n};\n}\n'
    await index_file("r0", "a/list.hpp", LIST_HPP, "cpp")
    await index_file("r1", "a/list.cpp", LIST_CPP, "cpp")
    await index_file("r2", "a/other.hpp", b"namespace ns {\nclass Other {\n public:\n  void notify() {}\n};\n}\n", "cpp")
    await index_file("r3", "a/base.hpp", base, "cpp")
    await index_file("r4", "a/cond.hpp", b'#include "base.hpp"\nnamespace ns {\nclass Cond : public Base {\n public:\n'
                     b"  void wake();\n};\n}\n", "cpp")
    await index_file("r5", "a/sub.cpp", b'#include "cond.hpp"\nnamespace ns {\nvoid Cond::wake() { m_x.notify(); }\n}\n',
                     "cpp")
    await _build(graph)
    was = _targets(graph, CALLS, "a/sub.cpp")
    graph.changed_names.clear()
    await index_file("r3", "a/base.hpp", base.replace(b"List m_x", b"Other m_x"), "cpp")
    await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                 touched_record_ids={"r3"}, facts_source=graph)
    incremental = _targets(graph, CALLS, "a/sub.cpp")
    await _build(graph)
    assert incremental == _targets(graph, CALLS, "a/sub.cpp") == {"a/other.hpp::method:ns.Other.notify"} != was


@pytest.mark.asyncio
async def test_an_incremental_build_of_the_caller_alone_reads_the_headers_field_types(graph, index_file) -> None:
    for i, (path, src) in enumerate((("a/list.hpp", LIST_HPP), ("a/list.cpp", LIST_CPP), ("a/cond.hpp", COND_HPP),
                                     ("a/cond.cpp", COND_CPP))):
        await index_file(f"r{i}", path, src, "cpp")
    await _build(graph)
    graph.changed_names.clear()
    await index_file("r3", "a/cond.cpp", COND_CPP + b"// edited\n", "cpp")
    await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                 touched_record_ids={"r3"}, facts_source=graph)
    assert _targets(graph, CALLS, "a/cond.cpp") == {"a/list.cpp::method:ns.List.notify"}


@pytest.mark.asyncio
async def test_a_forward_declaration_given_a_body_rebinds_callers_on_an_incremental_build(graph, index_file) -> None:
    other = b"namespace ns {\nclass List {\n public:\n  int size() { return 0; }\n};\n}\n"
    await index_file("r0", "a/list.hpp", LIST_HPP, "cpp")
    await index_file("r1", "a/list.cpp", LIST_CPP, "cpp")
    await index_file("r2", "b/list.hpp", b"namespace ns {\nclass List;\n}\n", "cpp")
    # No include names b/list.hpp: only the changed name `List` re-resolves use.cpp.
    await index_file("r3", "c/use.cpp", b"namespace ns {\nvoid go(List& l) { l.notify(); }\n}\n", "cpp")
    await _build(graph)
    was = _targets(graph, CALLS, "c/use.cpp")
    graph.changed_names.clear()
    await index_file("r2", "b/list.hpp", other, "cpp")
    await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                 touched_record_ids={"r2"}, facts_source=graph)
    incremental = _targets(graph, CALLS, "c/use.cpp")
    await _build(graph)
    assert incremental == _targets(graph, CALLS, "c/use.cpp") != was


CFG_HPP = (b"namespace db {\nclass Matcher {\n public:\n  Matcher* child(int i);\n  void match();\n};\n"
           b"class Config {\n public:\n  static Config &Get(int ctx);\n  Matcher* root();\n  void set(int v);\n"
           b" private:\n  Matcher m_root;\n};\n}\n")
CFG_CPP = (b'#include "cfg.hpp"\nnamespace db {\nConfig &Config::Get(int ctx) { static Config c; return c; }\n'
           b"Matcher* Config::root() { return &m_root; }\nvoid Config::set(int v) { m_root.child(v)->match(); }\n"
           b"Matcher* Matcher::child(int i) { return this; }\nvoid Matcher::match() {}\n}\n")


@pytest.mark.asyncio
async def test_an_auto_from_a_call_and_a_chain_are_typed_by_declared_return_types(graph, c_repo) -> None:
    await c_repo({
        "q/cfg.hpp": CFG_HPP, "q/cfg.cpp": CFG_CPP,
        "q/use.cpp": (b'#include "cfg.hpp"\nnamespace db {\nvoid use(int ctx) {\n  auto &cfg = Config::Get(ctx);\n'
                      b"  cfg.set(1);\n  cfg.root()->child(2)->match();\n  auto m = cfg.root();\n  m->match();\n}\n}\n"),
    })
    await _build(graph)
    assert _targets(graph, CALLS, "q/use.cpp") == {
        "q/cfg.cpp::method:db.Config.Get", "q/cfg.cpp::method:db.Config.set", "q/cfg.cpp::method:db.Config.root",
        "q/cfg.cpp::method:db.Matcher.child", "q/cfg.cpp::method:db.Matcher.match"}
    # A field heads a chain in an out-of-line method of its class.
    assert "q/cfg.cpp::method:db.Matcher.match" in _targets(graph, CALLS, "q/cfg.cpp")


@pytest.mark.asyncio
async def test_an_unknown_return_type_types_nothing(graph, c_repo) -> None:
    await c_repo({
        "q/cfg.hpp": CFG_HPP, "q/cfg.cpp": CFG_CPP,
        "q/use.cpp": (b'#include "cfg.hpp"\nnamespace db {\nauto make() { return 1; }\n'
                      b"void use() { auto x = make(); x.match(); auto y = other::thing(); y.set(1); }\n}\n"),
    })
    await _build(graph)
    assert _targets(graph, CALLS, "q/use.cpp") == {"q/use.cpp::function:db.make"}


@pytest.mark.asyncio
async def test_a_call_through_a_classes_operator_arrow_reaches_its_template_argument(graph, c_repo) -> None:
    await c_repo({
        "p/ptr.hpp": (b"template <class T> class optional_ptr {\n public:\n  T* operator->() const { return p; }\n"
                      b"  bool is_set() const { return p; }\n  T* p;\n};\n"
                      b"template <class T> class box {\n public:\n  T v;\n};\n"
                      b"class Conn {\n public:\n  void open() {}\n};\n"
                      b"class Pool {\n public:\n  void drain();\n  optional_ptr<Conn> m_conn;\n};\n"),
        "p/use.cpp": (b'#include "ptr.hpp"\nvoid use(optional_ptr<Conn> c, box<Conn> b) { c->open(); c.is_set(); b.open(); }\n'
                      b"void Pool::drain() { m_conn->open(); }\n"),
    })
    await _build(graph)
    # `box` has no operator->, so `b.open()` reaches nothing.
    assert _targets(graph, CALLS, "p/use.cpp") == {"p/ptr.hpp::method:Conn.open", "p/ptr.hpp::method:optional_ptr.is_set"}


def _calls_by_caller(graph, path: str) -> dict[str, set[str]]:
    """CALLS from *path*'s blocks: caller qualified name -> callee `file::qualifiedName`."""
    out: dict[str, set[str]] = {}
    for edge in graph.code_edges(CALLS):
        src = graph.blocks.get(edge["_from"].split("/", 1)[1])
        dst = graph.blocks.get(edge["_to"].partition("/")[2])
        if src and dst and graph.code_files.get(src["recordId"]) == path:
            out.setdefault(src["qualifiedName"], set()).add(f"{graph.code_files[dst['recordId']]}::{dst['qualifiedName']}")
    return out


# Edge cases of the rules above.

@pytest.mark.asyncio
async def test_a_definition_after_its_forward_declaration_keeps_its_methods(graph, c_repo) -> None:
    # duckdb: `class SequenceCatalogEntry;` above the class in one header numbered the class `#2`, so its out-of-line
    # methods missed it and `GetInfo()` in Copy fell to the base's.
    await c_repo({
        "inc/entry.hpp": (b"namespace db {\nclass Entry {\n public:\n  virtual int GetInfo() const;\n};\n}\n"),
        "src/entry.cpp": b'#include "../inc/entry.hpp"\nnamespace db {\nint Entry::GetInfo() const { return 0; }\n}\n',
        "inc/seq.hpp": (b'#include "entry.hpp"\nnamespace db {\nclass Seq;\nstruct Holder {\n  Seq* e;\n};\n'
                        b"class Seq : public Entry {\n public:\n  int GetInfo() const override;\n  int Copy() const;\n};\n}\n"),
        "src/seq.cpp": (b'#include "../inc/seq.hpp"\nnamespace db {\nint Seq::Copy() const { return GetInfo(); }\n'
                        b"int Seq::GetInfo() const { return 1; }\n}\n"),
    })
    await _build(graph)
    assert _targets(graph, CALLS, "src/seq.cpp") == {"src/seq.cpp::method:db.Seq.GetInfo"}


@pytest.mark.asyncio
async def test_a_forward_declaration_stands_for_a_type_whose_definition_is_not_indexed(graph, c_repo) -> None:
    # abseil: `class ABSL_LOCKABLE ABSL_ATTRIBUTE_WARN_UNUSED Mutex {` misparses, so no class Mutex is defined.
    await c_repo({
        "m/fwd.hpp": b"namespace absl {\nclass Mutex;\nvoid lock(Mutex* mu);\n}\n",
        "m/mutex.cpp": b'#include "fwd.hpp"\nnamespace absl {\nvoid Mutex::Lock() {}\n}\n',
        "m/use.cpp": b'#include "fwd.hpp"\nnamespace absl {\nvoid use(Mutex* mu) { mu->Lock(); }\n}\n',
    })
    await _build(graph)
    assert _targets(graph, CALLS, "m/use.cpp") == {"m/mutex.cpp::method:absl.Mutex.Lock"}


@pytest.mark.asyncio
async def test_a_path_into_std_never_reaches_a_repo_specialization(graph, c_repo) -> None:
    await c_repo({
        "s/limits.hpp": (b"namespace std {\ntemplate <> class numeric_limits<my::Int> {\n public:\n"
                         b"  static my::Int max() { return 0; }\n};\ntemplate <> struct hash<my::Int> {};\n}\n"),
        "s/use.cpp": (b'#include "limits.hpp"\nint use() { return std::numeric_limits<int>::max(); }\n'
                      b"void h() { std::hash<int> x(1); }\n"),
    })
    await _build(graph)
    assert _targets(graph, CALLS, "s/use.cpp") == set()


@pytest.mark.asyncio
async def test_an_auto_elsewhere_in_the_file_leaves_a_declared_receiver_typed(graph, c_repo) -> None:
    await c_repo({
        "q/cfg.hpp": CFG_HPP, "q/cfg.cpp": CFG_CPP,
        "q/use.cpp": (b'#include "cfg.hpp"\nnamespace db {\nMatcher m;\nvoid a() { m.match(); }\n'
                      b"void b() { auto m = other(); }\n"
                      b"Matcher* engine() { return nullptr; }\nvoid c() { engine()->match(); }\n"
                      b"void d() { auto m = cond ? x : y; m.match(); }\n"
                      b"void e1() { auto& e = Config::Get(1); }\nvoid e2() { auto& e = cond ? p : q; e.set(2); }\n}\n"),
    })
    await _build(graph)
    # c(): a call heads the chain; d(): an initializer the builder cannot evaluate falls back to the file's one class.
    assert _targets(graph, CALLS, "q/use.cpp") == {"q/cfg.cpp::method:db.Matcher.match", "q/use.cpp::function:db.engine",
                                                   "q/cfg.cpp::method:db.Config.Get", "q/cfg.cpp::method:db.Config.set"}
    calls = _calls_by_caller(graph, "q/use.cpp")
    assert calls["function:db.c"] == {"q/use.cpp::function:db.engine", "q/cfg.cpp::method:db.Matcher.match"}
    assert calls["function:db.d"] == calls["function:db.a"] == {"q/cfg.cpp::method:db.Matcher.match"}
    assert "function:db.b" not in calls
    # e2(): its unreadable initializer falls back to the file's one readable one, e1's.
    assert calls["function:db.e2"] == {"q/cfg.cpp::method:db.Config.set"}


@pytest.mark.asyncio
async def test_a_chain_types_through_a_method_defined_in_an_unpaired_source_on_an_incremental_build(
        graph, index_file) -> None:
    hpp = (b"namespace db {\nclass Matcher {\n public:\n  void match();\n};\nclass Other {\n public:\n  void match();\n};\n"
           b"class Config {\n public:\n  Matcher* root();\n};\n}\n")
    impl = (b'#include "cfg.hpp"\nnamespace db {\nMatcher* Config::root() { return nullptr; }\n'
            b"void Matcher::match() {}\nvoid Other::match() {}\n}\n")
    use = b'#include "cfg.hpp"\nnamespace db {\nvoid use(Config& c) { c.root()->match(); }\n}\n'
    await index_file("r0", "a/cfg.hpp", hpp, "cpp")
    await index_file("r1", "a/impl_extra.cpp", impl, "cpp")  # no same-stem pair of cfg.hpp
    await index_file("r2", "b/use.cpp", use, "cpp")
    await _build(graph)
    full = _targets(graph, CALLS, "b/use.cpp")
    graph.changed_names.clear()
    await index_file("r2", "b/use.cpp", use + b"// edited\n", "cpp")
    await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                 touched_record_ids={"r2"}, facts_source=graph)
    assert _targets(graph, CALLS, "b/use.cpp") == full == {"a/impl_extra.cpp::method:db.Config.root",
                                                           "a/impl_extra.cpp::method:db.Matcher.match"}


@pytest.mark.asyncio
async def test_an_unresolved_qualified_base_initializer_is_not_the_class_itself(graph, c_repo) -> None:
    await c_repo({
        "i/mapper.cpp": (b"namespace json {\nclass ObjectMapper {\n public:\n  ObjectMapper();\n};\n"
                         b"ObjectMapper::ObjectMapper() : data::mapping::ObjectMapper(1) {}\n}\n"),
    })
    await _build(graph)
    assert _targets(graph, CALLS, "i/mapper.cpp") == set()


@pytest.mark.asyncio
async def test_a_local_auto_shadows_a_field_of_the_same_name(graph, c_repo) -> None:
    await c_repo({
        "a/s.hpp": b"class Child {\n public:\n  void visit() {}\n};\nclass Node {\n public:\n  void run();\n  Child child;\n};\n",
        "a/s.cpp": b'#include "s.hpp"\nvoid Node::run() { auto &child = *children[0]; child.visit(); }\n',
    })
    await _build(graph)
    assert _targets(graph, CALLS, "a/s.cpp") == set()


@pytest.mark.asyncio
async def test_a_return_type_naming_a_template_parameter_types_nothing(graph, c_repo) -> None:
    await c_repo({
        "a/t.hpp": (b"class T {\n public:\n  void boom() {}\n};\nclass Conn {\n public:\n  void open() {}\n};\n"
                    b"template <class T> class Holder {\n public:\n  T& get();\n};\n"),
        "a/t.cpp": b'#include "t.hpp"\nvoid u(Holder<Conn>& h) { h.get().boom(); }\n',
    })
    await _build(graph)
    assert "a/t.hpp::method:T.boom" not in _targets(graph, CALLS, "a/t.cpp")


@pytest.mark.asyncio
async def test_only_an_arrow_call_goes_through_operator_arrow(graph, c_repo) -> None:
    await c_repo({
        "p/ptr.hpp": (b"template <class T> class optional_ptr {\n public:\n  T* operator->() const { return p; }\n"
                      b"  T* p;\n};\nclass Conn {\n public:\n  void reset() {}\n};\n"),
        "p/use.cpp": b'#include "ptr.hpp"\nvoid use(optional_ptr<Conn> c) { c.reset(); }\n',
    })
    await _build(graph)
    assert _targets(graph, CALLS, "p/use.cpp") == set()


@pytest.mark.asyncio
async def test_a_chain_through_an_overload_takes_the_class_its_overloads_return(graph, c_repo) -> None:
    # duckdb: `void Copy(const Stats&)` is defined first, `Stats Copy() const` second (Copy#2).
    await c_repo({
        "s/stats.hpp": b"class Stats {\n public:\n  void Copy(const Stats& o);\n  Stats Copy() const;\n  void Set(int v);\n};\n",
        "s/stats.cpp": (b'#include "stats.hpp"\nvoid Stats::Copy(const Stats& o) {}\nStats Stats::Copy() const { return *this; }\n'
                        b"void Stats::Set(int v) {}\n"),
        "s/use.cpp": b'#include "stats.hpp"\nvoid use(Stats& s) { auto c = s.Copy(); c.Set(1); }\n',
    })
    await _build(graph)
    assert "s/stats.cpp::method:Stats.Set" in _targets(graph, CALLS, "s/use.cpp")


@pytest.mark.asyncio
async def test_a_chain_from_an_unreadable_local_takes_the_files_declaration_of_its_head(graph, c_repo) -> None:
    # duckdb tpch: `auto &append_info = info[PSUPP]; append_info.appender->EndRow();`, `append_info` a parameter elsewhere.
    await c_repo({
        "t/app.hpp": (b"class Appender {\n public:\n  void EndRow() {}\n};\n"
                      b"struct Info {\n  Appender* appender;\n};\n"),
        "t/gen.cpp": (b'#include "app.hpp"\nvoid one(Info& append_info) { append_info.appender->EndRow(); }\n'
                      b"void two(Info* info) { auto& append_info = info[3]; append_info.appender->EndRow(); }\n"),
    })
    await _build(graph)
    assert _calls_by_caller(graph, "t/gen.cpp") == {"function:one": {"t/app.hpp::method:Appender.EndRow"},
                                                    "function:two": {"t/app.hpp::method:Appender.EndRow"}}


@pytest.mark.asyncio
async def test_overloads_returning_different_classes_type_no_chain(graph, c_repo) -> None:
    # `A* get(int)` beside `B* get()`, as prototypes and as in-class definitions.
    await c_repo({
        "a/o.hpp": (b"class A {\n public:\n  void fa() {}\n  void both() {}\n};\n"
                    b"class B {\n public:\n  void fb() {}\n  void both() {}\n};\n"
                    b"class Box {\n public:\n  A* get(int);\n  B* get();\n};\n"),
        "a/o.cpp": b'#include "o.hpp"\nvoid u(Box& b) { b.get()->both(); b.get(1)->both(); }\n',
        "a/p.cpp": (b'#include "o.hpp"\nclass Box2 {\n public:\n  A* get(int) { return nullptr; }\n'
                    b"  B* get() { return nullptr; }\n};\nvoid w(Box2& b) { b.get()->both(); }\n"),
    })
    await _build(graph)
    assert not {"a/o.hpp::method:A.both", "a/o.hpp::method:B.both"} & _targets(graph, CALLS, "a/o.cpp")
    assert not {"a/o.hpp::method:A.both", "a/o.hpp::method:B.both"} & _targets(graph, CALLS, "a/p.cpp")
