"""C# resolution: namespaces scope bare names, `base.` is the base class, `new X()` is the class."""
import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.family_rules import rules_for
from app.modules.code_graph.module_refs import module_refs_of_path
from app.modules.code_graph.resolvers.csharp import namespace_scope, partial_parts

from .test_import_binding import _build, _targets

CALLS, INHERITS, IMPLEMENTS = (RecordRelations.CALLS.value, RecordRelations.INHERITS.value,
                               RecordRelations.IMPLEMENTS.value)


async def _repo(index_file, files: dict[str, bytes]) -> None:
    for i, (path, src) in enumerate(files.items()):
        await index_file(f"r{i}", path, src, "csharp")


@pytest.mark.asyncio
async def test_a_bare_type_resolves_through_its_namespace_not_by_guess(graph, index_file) -> None:
    await _repo(index_file, {
        "src/Orders/Order.cs": b"namespace Shop.Orders;\npublic class Order { public void Pay() { var l = new Line(); } }\n",
        "src/Orders/Line.cs": b"namespace Shop.Orders;\npublic class Line {}\n",
        "src/Other/Line.cs": b"namespace Shop.Other;\npublic class Line {}\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "src/Orders/Order.cs") == {"src/Orders/Line.cs::class:Shop.Orders.Line"}
    assert next(e for e in graph.code_edges(CALLS))["confidence"] == "EXTRACTED"


@pytest.mark.asyncio
async def test_a_using_and_an_enclosing_namespace_put_types_in_scope(graph, index_file) -> None:
    await _repo(index_file, {
        "app/Billing/Invoice.cs": b"using Shop.Pay;\nnamespace Shop.Billing.Docs;\n"
                                  b"public class Invoice : Base { public void Go() { new Card(); } }\n",
        "app/Pay/Card.cs": b"namespace Shop.Pay;\npublic class Card {}\n",
        "app/Other/Card.cs": b"namespace Shop.Other;\npublic class Card {}\n",
        "app/Base.cs": b"namespace Shop;\npublic class Base {}\n",            # enclosing: Shop.Billing.Docs sees Shop
        "app/Elsewhere/Base.cs": b"namespace Shop.Elsewhere;\npublic class Base {}\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "app/Billing/Invoice.cs") == {"app/Pay/Card.cs::class:Shop.Pay.Card"}
    assert _targets(graph, INHERITS, "app/Billing/Invoice.cs") == {"app/Base.cs::class:Shop.Base"}


@pytest.mark.asyncio
async def test_an_unresolved_using_hides_no_repo_type(graph, index_file) -> None:
    # `using Microsoft.Extensions.Logging;` resolves to no file; a repo `Logger` is still found.
    await _repo(index_file, {
        "a/Svc.cs": b"using Microsoft.Extensions.Logging;\nnamespace App;\npublic class Svc { void Go() { new Logger(); } }\n",
        "a/Logger.cs": b"namespace App;\npublic class Logger {}\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "a/Svc.cs") == {"a/Logger.cs::class:App.Logger"}


@pytest.mark.asyncio
async def test_base_calls_the_base_class_and_an_interface_base_is_implemented(graph, index_file) -> None:
    await _repo(index_file, {
        "s/Base.cs": b"namespace S;\npublic class Base { public virtual void Save() {} }\n",
        "s/IRepo.cs": b"namespace S;\npublic interface IRepo { void Load(); }\n",
        "s/Repo.cs": b"namespace S;\npublic class Repo : Base, IRepo {\n"
                     b"  public override void Save() { base.Save(); }\n  public void Load() {}\n}\n",
    })
    await _build(graph)
    assert "s/Base.cs::method:S.Base.Save" in _targets(graph, CALLS, "s/Repo.cs")
    assert _targets(graph, INHERITS, "s/Repo.cs") == {"s/Base.cs::class:S.Base"}
    assert _targets(graph, IMPLEMENTS, "s/Repo.cs") == {"s/IRepo.cs::interface:S.IRepo"}


@pytest.mark.asyncio
async def test_base_never_reaches_an_interface_the_class_implements(graph, index_file) -> None:
    # The base class is outside the repo, the interface declares the method.
    await _repo(index_file, {
        "s/IUnit.cs": b"namespace S;\npublic interface IUnit { void Save(); }\n",
        "s/Ctx.cs": b"namespace S;\npublic class Ctx : ExternalDbContext, IUnit {\n"
                    b"  public void Save() { base.Save(); }\n}\n",
    })
    await _build(graph)
    assert "s/IUnit.cs::method:S.IUnit.Save" not in _targets(graph, CALLS, "s/Ctx.cs")


@pytest.mark.asyncio
async def test_new_x_names_the_class_even_beside_a_property_named_x(graph, index_file) -> None:
    await _repo(index_file, {
        "d/Order.cs": b"namespace D;\npublic class Order { public Address Address { get; set; } }\n",
        "d/Address.cs": b"namespace D;\npublic class Address {}\n",
        "d/Make.cs": b"namespace D;\npublic class Make { public void Go() { new Address(); } }\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "d/Make.cs") == {"d/Address.cs::class:D.Address"}


@pytest.mark.asyncio
async def test_a_typed_receiver_resolves_its_method(graph, index_file) -> None:
    await _repo(index_file, {
        "t/Pay.cs": b"namespace T;\npublic class PaymentService { public void Charge() {} }\n",
        "t/Use.cs": b"namespace T;\npublic class Use { void Go(PaymentService svc) { svc.Charge(); } }\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "t/Use.cs") == {"t/Pay.cs::method:T.PaymentService.Charge"}


@pytest.mark.asyncio
async def test_a_base_naming_one_of_a_generic_and_a_plain_type_of_one_name_resolves(graph, index_file) -> None:
    # eShop's IIntegrationEventHandler<T> and IIntegrationEventHandler share a file and a name; the key has one symbol.
    await _repo(index_file, {
        "src/Bus/IHandler.cs": b"namespace E;\npublic interface IHandler<in T> : IHandler { }\npublic interface IHandler { }\n",
        "src/App/OrderHandler.cs": b"using E;\nnamespace App;\npublic class OrderHandler(int x) : IHandler<int> { }\n",
    })
    await _build(graph)
    got = _targets(graph, IMPLEMENTS, "src/App/OrderHandler.cs") | _targets(graph, INHERITS, "src/App/OrderHandler.cs")
    assert {t.split("#")[0] for t in got} == {"src/Bus/IHandler.cs::interface:E.IHandler"}
    assert [_type_params(graph, t) for t in got] == [1]


def _type_params(graph, target: str) -> int | None:
    """The `typeParamCount` of a `_targets` endpoint (`path::kind:Name#n`)."""
    path, _, qualified = target.partition("::")
    rows = [b for b in graph.blocks.values()
            if graph.code_files.get(b.get("recordId")) == path and b.get("qualifiedName") == qualified]
    return rows[0].get("typeParamCount") if len(rows) == 1 else -1


@pytest.mark.asyncio
async def test_arity_picks_the_plain_type_too_and_never_guesses_when_it_does_not_separate(graph, index_file) -> None:
    await _repo(index_file, {
        "src/Bus/IHandler.cs": b"namespace E;\npublic interface IHandler<in T> : IHandler { }\npublic interface IHandler { }\n",
        "src/App/Plain.cs": b"using E;\nnamespace App;\npublic class Plain : IHandler { }\n",
        "src/App/Two.cs": b"using E;\nnamespace App;\npublic class Two : IHandler<int, int> { }\n",
    })
    await _build(graph)
    assert [_type_params(graph, t) for t in _targets(graph, IMPLEMENTS, "src/App/Plain.cs")] == [None]
    assert _targets(graph, IMPLEMENTS, "src/App/Two.cs") | _targets(graph, INHERITS, "src/App/Two.cs") == set()


EXTENSIONS = {
    "src/Api/Extensions/ServiceExtensions.cs": b"namespace Api.Extensions;\npublic static class ServiceExtensions {\n"
                                               b"  public static void AddCatalog(this IServiceCollection s) {}\n}\n",
    "src/Other/OtherExtensions.cs": b"namespace Other;\npublic static class OtherExtensions {\n"
                                    b"  public static void AddCatalog(this IServiceCollection s) {}\n}\n",
}


@pytest.mark.asyncio
async def test_an_extension_method_in_scope_is_called_on_any_receiver(graph, index_file) -> None:
    await _repo(index_file, {**EXTENSIONS, "src/Api/Program.cs": b"using Api.Extensions;\nnamespace Api;\n"
                                                                b"class P { void Go(IServiceCollection s) { s.AddCatalog(); } }\n"})
    await _build(graph)
    assert _targets(graph, CALLS, "src/Api/Program.cs") == {
        "src/Api/Extensions/ServiceExtensions.cs::method:Api.Extensions.ServiceExtensions.AddCatalog"}


@pytest.mark.asyncio
async def test_a_project_global_using_scopes_every_file_under_it(graph, index_file) -> None:
    await _repo(index_file, {**EXTENSIONS, "src/Api/GlobalUsings.cs": b"global using Api.Extensions;\n",
                             "src/Api/Web/Program.cs": b"namespace Web;\n"
                                                       b"class P { void Go(IServiceCollection s) { s.AddCatalog(); } }\n",
                             "src/Elsewhere/Program.cs": b"namespace Web;\n"
                                                        b"class Q { void Go(IServiceCollection s) { s.AddCatalog(); } }\n"})
    await _build(graph)
    assert _targets(graph, CALLS, "src/Api/Web/Program.cs") == {
        "src/Api/Extensions/ServiceExtensions.cs::method:Api.Extensions.ServiceExtensions.AddCatalog"}
    assert _targets(graph, CALLS, "src/Elsewhere/Program.cs") == set()   # another project, nothing in scope


@pytest.mark.asyncio
async def test_the_receiver_s_type_picks_among_extension_overloads_before_proximity(graph, index_file) -> None:
    await _repo(index_file, {
        "src/Api/Near.cs": b"namespace Api;\npublic static class Near {\n"
                           b"  public static void AddX(this IServiceCollection s) {}\n  public static void Run(this IBar b) {}\n}\n",
        "lib/Far/Far.cs": b"namespace Api;\npublic static class Far {\n"
                          b"  public static void AddX(this IHostBuilder h) {}\n  public static void Run(this IBaz b) {}\n}\n",
        "lib/Far/Foo.cs": b"namespace Api;\npublic interface IBaz {}\npublic class Foo : IBaz {}\n",
        "src/Api/Use.cs": b"namespace Api;\nclass U {\n  void Go(IHostBuilder h, Foo f) { h.AddX(); f.Run(); }\n}\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "src/Api/Use.cs") == {"lib/Far/Far.cs::method:Api.Far.AddX",   # exact `this` type
                                                       "lib/Far/Far.cs::method:Api.Far.Run"}    # a base of Foo


@pytest.mark.asyncio
async def test_a_method_on_the_receiver_s_type_wins_over_an_extension(graph, index_file) -> None:
    await _repo(index_file, {**EXTENSIONS,
                             "src/Api/Bag.cs": b"namespace Api;\npublic class Bag { public void AddCatalog() {} }\n",
                             "src/Api/Use.cs": b"using Api.Extensions;\nnamespace Api;\n"
                                               b"class U { void Go(Bag b) { b.AddCatalog(); } }\n"})
    await _build(graph)
    assert _targets(graph, CALLS, "src/Api/Use.cs") == {"src/Api/Bag.cs::method:Api.Bag.AddCatalog"}


@pytest.mark.asyncio
async def test_a_partial_class_reaches_the_methods_of_its_other_parts(graph, index_file) -> None:
    await _repo(index_file, {
        "a/Invoice.cs": b"namespace Acme.Billing;\npublic partial class Invoice {\n"
                        b"    void Run() { this.Total(); Tax(); }\n}\n",
        "b/Invoice.Calc.cs": b"namespace Acme.Billing;\npublic partial class Invoice {\n"
                             b"    public void Total() {}\n    public void Tax() {}\n}\n",
        "c/Invoice.cs": b"namespace Acme.Other;\npublic class Invoice { public void Total() {} public void Tax() {} }\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "a/Invoice.cs") == {"b/Invoice.Calc.cs::method:Acme.Billing.Invoice.Total",
                                                      "b/Invoice.Calc.cs::method:Acme.Billing.Invoice.Tax"}


@pytest.mark.asyncio
async def test_a_base_declared_on_one_part_is_the_base_of_every_part(graph, index_file) -> None:
    await _repo(index_file, {
        "s/Base.cs": b"namespace S;\npublic class Base { public virtual void Save() {} public void Load() {} }\n",
        "s/Repo.cs": b"namespace S;\npublic partial class Repo : Base {\n}\n",
        "s/Repo.Ops.cs": b"namespace S;\npublic partial class Repo {\n"
                         b"    public override void Save() { base.Save(); this.Load(); }\n}\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "s/Repo.Ops.cs") == {"s/Base.cs::method:S.Base.Save", "s/Base.cs::method:S.Base.Load"}


@pytest.mark.asyncio
async def test_a_same_named_type_that_is_not_partial_or_differs_in_arity_is_not_a_part(graph, index_file) -> None:
    await _repo(index_file, {
        "a/Invoice.cs": b"namespace Acme;\npublic partial class Invoice {\n    void Run() { this.Total(); this.Tax(); }\n}\n",
        # another project's non-partial type of the same qualified name
        "other/Invoice.cs": b"namespace Acme;\npublic class Invoice { public void Total() {} }\n",
        # a generic partial type of the same name is another type
        "g/Invoice.cs": b"namespace Acme;\npublic partial class Invoice<T> { public void Tax() {} }\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "a/Invoice.cs") == set()


def _type(ns: str) -> bytes:
    return f"namespace {ns} {{ public class T {{ }} }}\n".encode()


@pytest.mark.parametrize("homes, want", [
    (("X", "Y"), "X"),        # the inner declaration's using before the outer one's
    (("A", "Y"), "A"),        # the outer namespace's members before its using
    (("X", "A"), "X"),        # the inner using before the outer namespace's members
    (("Y", "Top"), "Y"),      # any namespace level before the compilation unit
], ids=["inner-using", "members-then-using", "inner-using-over-outer-members", "namespace-over-unit"])
@pytest.mark.asyncio
async def test_a_name_resolves_at_the_nearest_lookup_level(graph, index_file, homes, want) -> None:
    # `namespace A { using Y; namespace B { using X; .. } }`, `using Top;` at the compilation unit.
    await _repo(index_file, {
        **{f"lib/{ns}/T.cs": _type(ns) for ns in homes},
        "app/C.cs": b"using Top;\nnamespace A { using Y; namespace B { using X;\n"
                    b"  class C { void Go() { new T(); } } } }\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "app/C.cs") == {f"lib/{want}/T.cs::class:{want}.T"}


@pytest.mark.parametrize("homes, want", [
    (("A", "Z"), "A"),        # the enclosing block-form `A` before a compilation-unit using
    (("W", "A"), "W"),        # a using after `namespace A.B;` is inside A.B: before A's members
], ids=["outer-namespace-over-unit-using", "inner-using-of-file-scoped"])
@pytest.mark.asyncio
async def test_a_file_scoped_namespace_beside_a_block_form_enclosing_one(graph, index_file, homes, want) -> None:
    await _repo(index_file, {
        **{f"lib/{ns}/T.cs": _type(ns) for ns in homes},
        "app/C.cs": b"using Z;\nnamespace A.B;\nusing W;\nclass C { void Go() { new T(); } }\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "app/C.cs") == {f"lib/{want}/T.cs::class:{want}.T"}


@pytest.mark.asyncio
async def test_a_file_level_using_ranks_with_a_global_using(graph, index_file) -> None:
    # Both are compilation-unit level: neither wins, and two equally near candidates stay skipped.
    await _repo(index_file, {
        "src/Api/GlobalUsings.cs": b"global using G;\n",
        "src/Api/lib/G/T.cs": _type("G"), "src/Api/lib/Z/T.cs": _type("Z"),
        "src/Api/C.cs": b"using Z;\nnamespace A;\nclass C { void Go() { new T(); } }\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "src/Api/C.cs") == set()


def test_the_csharp_row_and_its_extensions() -> None:
    rules = rules_for("csharp")
    assert rules.bare_call_reaches_method == "same_class" and "base" in rules.super_receivers
    assert rules.implicit_scope is namespace_scope and rules.type_parts is partial_parts
    assert rules_for("jvm").type_parts is None
    assert "module:src/Orders/Order" in module_refs_of_path("src/Orders/Order.cs")


@pytest.mark.asyncio
async def test_the_base_chain_takes_the_in_scope_base_a_nearer_one_does_not_win(graph, index_file) -> None:
    # The same-folder Base is in another namespace: path proximity would pick it, C#'s scope does not.
    await _repo(index_file, {
        "src/Billing/Invoice.cs": b"namespace Shop.Billing;\n"
                                  b"public class Invoice : Base { public void Go() { base.Save(); Load(); } }\n",
        "src/Billing/Base.cs": b"namespace Shop.Legacy;\n"
                               b"public class Base { public void Save() {} public void Load() {} }\n",
        "core/Base.cs": b"namespace Shop.Billing;\npublic class Base { public void Save() {} public void Load() {} }\n",
    })
    await _build(graph)
    assert _targets(graph, INHERITS, "src/Billing/Invoice.cs") == {"core/Base.cs::class:Shop.Billing.Base"}
    assert _targets(graph, CALLS, "src/Billing/Invoice.cs") == {
        "core/Base.cs::method:Shop.Billing.Base.Save", "core/Base.cs::method:Shop.Billing.Base.Load"}
