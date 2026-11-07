"""The C++ facts and names behind call recall on oatpp, abseil and duckdb."""
from app.modules.parsers.code_parser.code_file_parser import qualified_name_for
from app.modules.parsers.code_parser.engine import parse_code
from app.modules.parsers.code_parser.models import (
    CONSTRUCT,
    DECLARATION,
    INITIALIZER,
    RETURNS,
    STATIC,
)


def _calls(source: bytes, language: str = "cpp") -> list[tuple]:
    return [(f.to_name, f.is_member_call, f.receiver, f.qualified_prefix, f.context)
            for f in parse_code(source, language).pending if f.relation == "CALLS"]


def _names(source: bytes) -> set[str]:
    return {qualified_name_for(s.kind, s.name, s.parent_chain) for s in parse_code(source, "cpp").symbols if s.name}


def test_a_template_call_names_its_function_and_drops_its_arguments_from_the_qualifier() -> None:
    src = (b"void g(Foo& o, Foo* p) {\n make_uniq<Bar>(1);\n std::make_shared<Bar>(2);\n o.Cast<Bar>();\n"
           b" p->template Get<int>();\n Box<int>::make();\n ns::Cast::Operation<int, float>(3);\n}\n")
    assert _calls(src) == [
        ("make_uniq", False, None, None, None), ("make_shared", True, "std", "std", None),
        ("Cast", True, "o", "o", None), ("Get", True, "p", "p", None), ("make", True, "Box", "Box", None),
        ("Operation", True, "Cast", "ns::Cast", None)]


def test_a_namespace_after_an_unexpanded_macro_line_keeps_its_name() -> None:
    # abseil: `ABSL_NAMESPACE_BEGIN` before `namespace x {` parses as a function named `namespace`.
    src = (b"namespace absl {\nABSL_NAMESPACE_BEGIN\nnamespace {\nvoid hidden() {}\n}\n"
           b"ABSL_NAMESPACE_BEGIN\nnamespace inner {\nvoid h() {}\n}\nABSL_NAMESPACE_END\n}\n")
    got = _names(src)
    assert {"function:absl.hidden", "function:absl.inner.h"} <= got, sorted(got)
    assert not any("namespace" in n for n in got), sorted(got)


def test_an_out_of_class_specialization_is_named_as_a_member_of_its_class() -> None:
    src = (b"namespace a {\nclass ApiClient { template<class T> struct TI {}; };\ntemplate<>\n"
           b"struct ApiClient::TI<int> {\n  static int toString(int x) { return x; }\n};\n}\n")
    assert "method:a.ApiClient.TI.toString" in _names(src)


def test_a_forward_declaration_is_a_declaration_and_a_definition_is_not() -> None:
    parsed = parse_code(b"class Conn;\nstruct S;\nclass Conn { int x; };\nenum class E : int;\n", "cpp")
    assert [(s.name, s.modifiers) for s in parsed.symbols if s.kind in ("class", "struct", "enum")] == [
        ("Conn", (DECLARATION,)), ("S", (DECLARATION,)), ("Conn", ()), ("E", (DECLARATION,))]


def test_fields_are_typed_under_their_class_and_a_method_prototype_is_no_field() -> None:
    src = (b"class Cond {\n  List m_list;\n  static Action make();\n  optional_ptr<Conn> m_c;\n"
           b"  std::shared_ptr<Conn> m_s;\n};\nstruct P { Conn* c; };\nConn* Pool::get(int i) { return 0; }\n"
           b"void f() { auto& cfg = ns::Config::Get(1); auto r = cfg->template root<int>()->child(); }\n")
    table = parse_code(src, "cpp").type_table
    assert table["Cond.m_list"] == "List" and table["P.c"] == "Conn"
    assert "Cond.make" not in table
    # optional_ptr's argument is kept for its operator->; a std smart pointer is already what it points to.
    assert (table["Cond.m_c"], table["Cond.m_c<>"], table["Cond.m_s"]) == ("optional_ptr", "Conn", "Conn")
    assert "Cond.m_s<>" not in table
    assert (table["cfg"], table["r"]) == ("=ns.Config.Get(1)", "=cfg.root<int>().child()")


def test_a_receiver_takes_its_own_functions_type_when_the_file_reuses_the_name() -> None:
    facts = parse_code(b"void f(A& x) { x.run(); }\nvoid g() { B x; x.stop(); }\n", "cpp").pending
    assert [(f.to_name, f.receiver_type) for f in facts if f.relation == "CALLS"] == [("run", "A"), ("stop", "B")]


def test_constructions_and_initializers_are_calls_with_their_context() -> None:
    src = (b"D::D(int x) : Base(x), m_x(f(x)), ns::Err(1) {}\n"
           b"void g() { Conn a(3); int n(4); Conn b = make(); auto* c = new ns::Conn(5); Conn d{6}; }\n")
    assert _calls(src) == [
        ("Base", False, None, None, INITIALIZER), ("m_x", False, None, None, INITIALIZER),
        ("f", False, None, None, None), ("Err", True, "ns", "ns", INITIALIZER),
        ("Conn", False, None, None, CONSTRUCT), ("make", False, None, None, None),
        ("Conn", True, "ns", "ns", CONSTRUCT)]


def test_an_in_class_constructors_initializers_are_its_calls() -> None:
    parsed = parse_code(b"class D : public Base {\n  D(int x) : Base(x), m_x(make(x)) {}\n};\n", "cpp")
    ctor = next(i for i, s in enumerate(parsed.symbols) if s.kind == "method")
    assert {(f.to_name, f.from_symbol) for f in parsed.pending if f.relation == "CALLS"} == {
        ("Base", ctor), ("m_x", ctor), ("make", ctor)}


def test_c_keeps_its_facts() -> None:
    # A C file is parsed by the C grammar: no constructions, no template arguments.
    assert _calls(b"void g(void) { struct conn c; init(&c); c.ops->open(); }\n", "c") == [
        ("init", False, None, None, None), ("open", True, "ops", "c.ops", None)]


def test_a_function_or_method_names_the_class_it_returns_but_never_a_template_parameter() -> None:
    src = (b"class Pool {\n public:\n  Conn* get();\n  Matcher& root() { return *m; }\n  Conn* m_c = nullptr;\n};\n"
           b"std::unique_ptr<Conn> Pool::take() { return 0; }\nstatic Conn* make() { return 0; }\n"
           b"template <class T> T& Holder<T>::get() { return v; }\n"
           b"template <class T> class H {\n  T& g() { return v; }\n};\nint n() { return 0; }\n")
    got = {(s.name, s.start_line): s.modifiers for s in parse_code(src, "cpp").symbols if s.kind in ("method", "function", "field")}
    assert got[("get", 3)] == (f"{RETURNS}Conn",) and got[("root", 4)] == (f"{RETURNS}Matcher",)
    assert got[("m_c", 5)] == () and got[("take", 7)] == (f"{RETURNS}Conn",)
    assert got[("make", 8)] == (STATIC, f"{RETURNS}Conn",)
    assert got[("get", 9)] == () and got[("g", 11)] == () and got[("n", 13)] == ()
    # C keeps its words: no RETURNS.
    c = parse_code(b"static conn_t *mk(void) { return 0; }\n", "c").symbols
    assert [s.modifiers for s in c if s.kind == "function"] == [(STATIC,)]
