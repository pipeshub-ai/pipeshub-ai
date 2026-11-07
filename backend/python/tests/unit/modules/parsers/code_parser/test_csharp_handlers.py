"""C#'s parser facts: `using` directives, base lists and the type table."""
from app.config.constants.arangodb import RecordRelations
from app.modules.parsers.code_parser.engine import parse_code

SRC = b"""global using System.Linq;
using System;
using static Shop.Core.Guard;
using Alias = Shop.Orders.Order;
namespace Shop.Orders;

public class Order : Base<int>, IRepo, Shop.Core.IThing {
  private readonly IRepo _repo;
  public ILogger Log { get; set; }
  public Order(IRepo repo, int n) : base(n) { _repo = repo; }
  public void Pay(Card card, string note) {
    var svc = new PaymentService();
    PaymentService other = Create();
    var count = 3;
    svc.Charge(card);
  }
  public void Twice(Card x) {}
  public void Again(Ticket x) {}
}
public interface IRepo : IBase { void Save(); }
public record Item(int Id) : Base(Id);
"""


def facts(relation: str) -> list[tuple]:
    parsed = parse_code(SRC, "csharp")
    return [(f.to_name, f.qualified_prefix, f.local_name) for f in parsed.pending
            if getattr(f.relation, "value", f.relation) == relation]


def test_a_using_names_its_namespace_or_type_and_binds_no_name() -> None:
    assert facts(RecordRelations.IMPORTS_FROM.value) == [
        ("System.Linq", None, None), ("System", None, None), ("Shop.Core.Guard", None, None),
        ("Shop.Orders.Order", None, None)]
    assert facts(RecordRelations.IMPORTS.value) == []   # nothing binds a local name


def test_every_base_in_a_base_list_is_inherited_without_its_type_arguments() -> None:
    assert facts(RecordRelations.INHERITS.value) == [
        ("Base", None, None), ("IRepo", None, None), ("IThing", "Shop.Core", None),
        ("IBase", None, None), ("Base", None, None)]   # Order's three, IRepo's, the record's primary-constructor base


def test_the_type_table_reads_fields_properties_parameters_and_locals() -> None:
    table = parse_code(SRC, "csharp").type_table
    assert {k: table.get(k) for k in ("_repo", "Log", "repo", "card", "svc", "other")} == {
        "_repo": "IRepo", "Log": "ILogger", "repo": "IRepo", "card": "Card", "svc": "PaymentService",
        "other": "PaymentService"}
    assert "x" not in table          # declared as Card and as Ticket: unknown
    assert "note" not in table and "n" not in table and "count" not in table   # built-in types and `var` of a literal


def test_a_call_on_a_typed_local_carries_its_type() -> None:
    call = next(f for f in parse_code(SRC, "csharp").pending if f.to_name == "Charge")
    assert call.is_member_call and call.receiver == "svc" and call.receiver_type == "PaymentService"


def test_a_property_is_a_field_and_a_local_function_part_of_its_method() -> None:
    src = (b"namespace A;\nclass C {\n  public int Total { get { return Sum(); } }\n"
           b"  void Run() { int Local() { return Helper(); } Local(); }\n}\n")
    symbols = {(s.kind, s.name) for s in parse_code(src, "csharp").symbols if s.name}
    assert ("field", "Total") in symbols and ("method", "Run") in symbols
    assert not any(name == "Local" for _, name in symbols)


def test_partial_types_and_extension_methods_carry_their_modifier() -> None:
    src = (b"namespace A;\npublic static partial class Ext {\n"
           b"  public static int Twice(this int n) => n * 2;\n  public static int Plain(int n, string s) => n;\n}\n"
           b"public class Whole { void This(Whole w) {} }\n")
    symbols = {s.name: s for s in parse_code(src, "csharp").symbols if s.name}
    assert {name: s.modifiers for name, s in symbols.items()} == {
        "A": (), "Ext": ("partial",), "Twice": ("extension", "this:int"), "Plain": (), "Whole": (), "This": ()}
    assert symbols["Ext"].type_param_count == 0


def test_a_generic_declaration_counts_its_type_parameters() -> None:
    src = b"namespace A;\npublic interface IHandler<in T> : IHandler { }\npublic interface IHandler { }\n" \
          b"public class Box<K, V> { public T Get<T>() => default; }\n"
    counts = [(s.name, s.type_param_count) for s in parse_code(src, "csharp").symbols if s.name]
    assert counts == [("A", 0), ("IHandler", 1), ("IHandler", 0), ("Box", 2), ("Get", 1)]


def test_a_method_without_a_body_is_a_declaration_in_every_language() -> None:
    cases = {
        "csharp": b"interface I { void M(); }\nabstract class A { public abstract void N(); public void O() {} }\n",
        "java": b"interface I { void m(); default void d() {} }\n",
        "typescript": b"abstract class A { abstract n(): void; o(): void {} }\n",
        "rust": b"trait T { fn a(&self); fn b(&self) {} }\n",
        "php": b"<?php\ninterface I { public function m(); }\nclass C { public function o() {} }\n",
    }
    for language, src in cases.items():
        declared = {s.name for s in parse_code(src, language).symbols if "declaration" in s.modifiers}
        bodied = {s.name for s in parse_code(src, language).symbols if s.kind == "method"} - declared
        assert declared == {"M", "N", "m", "n", "a"} & {s.name for s in parse_code(src, language).symbols}, language
        assert bodied == {"O", "d", "o", "b"} & {s.name for s in parse_code(src, language).symbols}, language


def test_a_dart_callable_whose_body_is_a_sibling_is_not_a_declaration() -> None:
    # Dart's `function_body` follows the signature as a sibling; the span claims it, so it is a body.
    src = b"class A {\n  void n() { print(1); }\n  int get g => 1;\n}\nvoid top() { print(2); }\nvoid ext();\n"
    declared = {s.name for s in parse_code(src, "dart").symbols if "declaration" in s.modifiers}
    assert declared == {"ext"}


def test_only_a_global_using_is_marked_global() -> None:
    contexts = [f.context for f in parse_code(SRC, "csharp").pending
                if getattr(f.relation, "value", f.relation) == RecordRelations.IMPORTS_FROM.value]
    assert contexts == ["global-using", None, None, None]
