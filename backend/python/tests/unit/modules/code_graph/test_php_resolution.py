"""PHP resolution: `use` by qualified name, the file's namespace, `$this` / `self` / `parent`, bare calls."""
import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.family_rules import rules_for
from app.modules.code_graph.import_resolution import ImportResolution
from app.modules.code_graph.module_refs import module_refs_of_path
from app.modules.code_graph.resolvers import RESOLVERS, php

from .test_import_binding import _build, _targets

CALLS, IMPORTS, IMPORTS_FROM, INHERITS = (RecordRelations.CALLS.value, RecordRelations.IMPORTS.value,
                                          RecordRelations.IMPORTS_FROM.value, RecordRelations.INHERITS.value)


async def _repo(index_file, files: dict[str, bytes]) -> None:
    for i, (path, src) in enumerate(files.items()):
        await index_file(f"r{i}", path, src, "php")


MODELS = {
    "Http/Request.php": b"<?php\nnamespace App\\Http;\nclass Request { public function input() {} }\n",
    "Models/User.php": b"<?php\nnamespace App\\Models;\nclass User extends Model { public function save() {} }\n",
    "Models/Model.php": b"<?php\nnamespace App\\Models;\nclass Model { public function boot() {} }\n",
}


@pytest.mark.asyncio
async def test_a_use_resolves_by_qualified_name_and_a_vendor_name_never_lands_in_the_repo(graph, index_file) -> None:
    await _repo(index_file, {**MODELS, "Ctl/UserCtl.php": (
        b"<?php\nnamespace App\\Ctl;\nuse App\\Models\\User;\nuse Illuminate\\Http\\Request;\n"
        b"class UserCtl { public function show(User $u) { $u->save(); new Request(); } }\n")})
    await _build(graph)
    assert _targets(graph, IMPORTS, "Ctl/UserCtl.php") == {"Models/User.php::class:App.Models.User"}
    assert _targets(graph, IMPORTS_FROM, "Ctl/UserCtl.php") == {"Models/User.php"}
    # Illuminate\Http\Request is not app/Http/Request.php, by path or by name
    assert _targets(graph, CALLS, "Ctl/UserCtl.php") == {"Models/User.php::method:App.Models.User.save"}


@pytest.mark.asyncio
async def test_a_file_sees_its_own_namespace_without_a_use(graph, index_file) -> None:
    await _repo(index_file, {**MODELS, "Other/User.php": b"<?php\nnamespace App\\Other;\nclass User {}\n",
                             "Models/Make.php": b"<?php\nnamespace App\\Models;\nclass Make { function go() { new User(); } }\n"})
    await _build(graph)
    assert _targets(graph, CALLS, "Models/Make.php") == {"Models/User.php::class:App.Models.User"}
    assert _targets(graph, INHERITS, "Models/User.php") == {"Models/Model.php::class:App.Models.Model"}


@pytest.mark.asyncio
async def test_this_self_static_and_parent_name_the_class_and_its_base(graph, index_file) -> None:
    await _repo(index_file, {**MODELS, "Models/Admin.php": (
        b"<?php\nnamespace App\\Models;\nclass Admin extends User {\n"
        b"  public function go() { $this->save(); self::save(); static::save(); parent::boot(); }\n}\n")})
    await _build(graph)
    assert _targets(graph, CALLS, "Models/Admin.php") == {"Models/User.php::method:App.Models.User.save",
                                                          "Models/Model.php::method:App.Models.Model.boot"}


@pytest.mark.asyncio
async def test_a_trait_s_method_wins_over_an_interface_declaring_it(graph, index_file) -> None:
    # bookstack's Chapter: the interface comes first in the base list, the trait's body runs.
    await _repo(index_file, {
        "Ent/HasDescription.php": b"<?php\nnamespace App\\Ent;\ninterface HasDescription { public function info(); }\n",
        "Ent/Container.php": b"<?php\nnamespace App\\Ent;\ntrait Container { public function info() { return 1; } }\n",
        "Ent/Chapter.php": b"<?php\nnamespace App\\Ent;\nclass Chapter implements HasDescription {\n  use Container;\n}\n",
        "Ent/Show.php": b"<?php\nnamespace App\\Ent;\nclass Show { public function go(Chapter $c) { $c->info(); } }\n",
        "Ent/Only.php": b"<?php\nnamespace App\\Ent;\nclass Only { public function go(HasDescription $d) { $d->info(); } }\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "Ent/Show.php") == {"Ent/Container.php::method:App.Ent.Container.info"}
    # with no body on the chain, the interface's declaration is still the target
    assert _targets(graph, CALLS, "Ent/Only.php") == {"Ent/HasDescription.php::method:App.Ent.HasDescription.info"}


@pytest.mark.asyncio
async def test_a_bare_call_is_a_function_never_a_method(graph, index_file) -> None:
    await _repo(index_file, {**MODELS, "helpers.php": b"<?php\nfunction view($n) { return $n; }\n",
                             "Views/Page.php": b"<?php\nnamespace App\\Views;\nclass Page {\n"
                                               b"  public function view() {}\n  public function go() { view('x'); save(); }\n}\n"})
    await _build(graph)
    assert _targets(graph, CALLS, "Views/Page.php") == {"helpers.php::function:view"}


def test_the_resolver_needs_the_index_and_names_one_file() -> None:
    assert php.resolve("App\\Models\\User", "a.php", {"Models/User.php"}, None) is None
    assert RESOLVERS["php"] is php.resolve
    rules = rules_for("php")
    assert rules.bare_call_reaches_method == "never" and {"$this", "self", "static"} <= rules.self_receivers
    assert "parent" in rules.super_receivers and rules.implicit_scope is php.namespace_scope
    assert "module:Models/User" in module_refs_of_path("Models/User.php")
    assert ImportResolution({"a.php"}).resolve("App\\X", "a.php", "php") is None   # no index outside a build
