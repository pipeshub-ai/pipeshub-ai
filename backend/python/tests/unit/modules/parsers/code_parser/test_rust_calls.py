"""Rust call facts: the callee reader, calls inside macro arguments, return and field types, and the
receiver each method call is typed by (`T` or an `=chain` the edge builder evaluates)."""
import pytest

from app.modules.parsers.code_parser.engine import parse_code


def calls(src: bytes) -> list[tuple]:
    return [(f.to_name, f.is_member_call, f.receiver, f.qualified_prefix, f.receiver_type)
            for f in parse_code(src, "rust").pending if f.relation == "CALLS"]


@pytest.mark.parametrize(("body", "fact"), [
    (b"x.collect::<Vec<u8>>()", ("collect", True, "x", "x", None)),          # not a call of `x`
    (b"T::new::<u8>()", ("new", True, "T", "T", None)),                      # not a call of `T`
    (b"Foo::<u8>::new()", ("new", True, "Foo", "Foo::<u8>", None)),           # the type is Foo
    (b"helpers::check()", ("check", False, None, "helpers", None)),           # a module's function
    (b"crate::a::check()", ("check", False, None, "crate::a", None)),
])
def test_the_callee_names_what_is_called_and_a_module_path_is_no_receiver(body, fact) -> None:
    assert calls(b"fn f() { " + body + b"; }\n") == [fact]


def test_a_path_call_inside_an_inline_module_is_read_as_the_file_s_module_sees_it() -> None:
    src = b"mod tests {\n    fn t() { super::fixtures::session(); }\n}\nmod convert {}\nfn g() { convert::usize(); }\n"
    assert [(f[0], f[3]) for f in calls(src)] == [("session", "self::fixtures"), ("usize", "self::convert")]


def test_calls_in_an_expression_macro_s_arguments_are_calls() -> None:
    src = b'fn t() { assert_eq!(format!("{}", a.b()), c::d(vec![e()]), "x"); }\n'
    assert sorted(f[0] for f in calls(src)) == ["b", "d", "e"]


def test_an_item_macro_and_a_token_macro_hold_no_calls() -> None:
    src = (b"lazy_static! { static ref R: Regex = Regex::new(); }\nfn t() { quote! { f(x) }; }\n"
           b"fn u() { assert!(g()); }\n")
    assert [f[0] for f in calls(src)] == ["g"]   # only the expression macro's argument


def test_a_macro_of_unknown_syntax_holds_no_calls_but_a_known_one_does() -> None:
    src = (b"fn t() { foo!(fn x() { y(); }); my_dsl!(select g(1) from t); html! { <p>{ h() }</p> };\n"
           b'    log::info!("{}", i()); assert_eq!(j(), my_dsl!(k())); }\n')
    assert sorted(f[0] for f in calls(src)) == ["i", "j"]


def test_a_macro_s_calls_keep_their_lines() -> None:
    parsed = parse_code(b"fn t() {\n    assert!(\n        ok(1)\n    );\n}\n", "rust")
    assert [(f.to_name, f.line) for f in parsed.pending if f.relation == "CALLS"] == [("ok", 3)]


TYPES = b"""pub struct Checker<'a> { semantic: SemanticModel<'a>, locator: &'a Locator }
impl<'a> Checker<'a> {
    pub fn new() -> Result<Self, Error> { todo!() }
    pub fn semantic(&self) -> &SemanticModel<'a> { &self.semantic }
    pub fn rules(&self) -> impl Iterator<Item = Rule> { todo!() }
}
pub fn parse(src: &str) -> Box<Parsed> { todo!() }
"""


def test_return_and_field_types_are_keyed_by_their_owner() -> None:
    table = parse_code(TYPES, "rust").type_table
    assert {k: table.get(k) for k in ("Checker.new()", "Checker.semantic()", "parse()", "Checker.semantic",
                                      "Checker.locator", "Checker.rules()")} == {
        "Checker.new()": "?Checker", "Checker.semantic()": "SemanticModel", "parse()": "*Parsed",
        "Checker.semantic": "SemanticModel", "Checker.locator": "Locator", "Checker.rules()": None}


def test_a_type_parameter_or_trait_object_is_its_trait() -> None:
    src = (b"struct Core<'s, M: Matcher, S> where S: Sink + Send { matcher: &'s M, sink: S }\n"
           b"fn f<T: Clone + Printer>(p: T, q: impl Store, r: &dyn Sink, s: Vec<u8>) {}\n")
    table = parse_code(src, "rust").type_table
    assert {k: table.get(k) for k in ("Core.matcher", "Core.sink", "p", "q", "r", "s")} == {
        "Core.matcher": "Matcher", "Core.sink": "Sink", "p": "Printer", "q": "Store", "r": "Sink", "s": "Vec"}


def test_an_unbounded_type_parameter_types_nothing() -> None:
    table = parse_code(b"fn f<T, U: Printer>(x: T, y: U) {}\n", "rust").type_table
    assert "x" not in table and table.get("y") == "Printer"


@pytest.mark.parametrize(("body", "method", "receiver_type"), [
    (b"SearcherBuilder::new().line_number(true).build().search(x);", "search",
     "=SearcherBuilder::new().line_number().build()"),
    (b"let b = Builder::new(a, b); let s = b.build()?; s.search();", "search", "=Builder::new().build().unwrap()"),
    (b"let x = load(); let x = x.parse().await; x.run();", "run", "=load().parse()"),
    (b"if let Some(s) = self.store() { s.save(); }", "save", "=self.store().unwrap()"),
    (b"self.config().colors.matched();", "matched", "=self.config().colors"),
    (b"let c: Client = make(); c.get();", "get", "Client"),
    (b"Caller { own: 1 }.harness();", "harness", "Caller"),
])
def test_a_method_call_s_receiver_is_typed_by_its_chain_and_its_lets(body, method, receiver_type) -> None:
    found = [f[4] for f in calls(b"impl Engine { fn f(&self, a: u8) { " + body + b" } }\n") if f[0] == method]
    assert found == [receiver_type]


def test_a_bare_self_or_an_unbound_variable_is_left_to_the_builder() -> None:
    src = b"impl E { fn f(&self, v: u8) { self.go(); w.go(); self.cfg.go(); } }\n"
    assert [f[4] for f in calls(src)] == [None, None, "=self.cfg"]   # only a chain is written for the builder


def test_nested_type_arguments_leave_the_path() -> None:
    src = b"fn f() { Snippet::<Annotation<'_>>::source(s).path(p); }\n"
    assert [f[4] for f in calls(src) if f[0] == "path"] == ["=Snippet::source()"]


def test_a_wrapper_s_constructor_gives_its_variable_no_type() -> None:
    table = parse_code(b"fn f() { let store = Arc::new(Store::open()); let b = Builder::new(); }\n", "rust").type_table
    assert "store" not in table and table.get("b") == "Builder"
