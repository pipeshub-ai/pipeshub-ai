"""PHP's parser facts: `use` imports, extends / implements / traits, the type table, `new X()`."""
from app.config.constants.arangodb import RecordRelations
from app.modules.parsers.code_parser.engine import parse_code

SRC = b"""<?php
namespace App\\Http\\Controllers;

use App\\Models\\User;
use Illuminate\\Http\\Request as Req;
use App\\Support\\{Helper, Other as O};
use function App\\Support\\helper_fn;

class UserController extends Controller implements HasName, \\App\\Contracts\\Thing
{
    use Loggable, Cacheable;
    private UserRepo $repo;
    private ?Cache $cache;
    private int $count;
    public function __construct(UserRepo $repo, private Mailer $mailer) { $this->repo = $repo; }
    public function show(Req $request, int $id, A|B $either): User
    {
        $user = new User();
        $user->save();
        $this->repo->find($id);
        self::make();
        parent::boot();
        view('users.show');
        return $user;
    }
}
interface HasName extends Base {}
"""


def facts(relation: str) -> list[tuple]:
    return [(f.to_name, f.qualified_prefix, f.local_name) for f in parse_code(SRC, "php").pending
            if getattr(f.relation, "value", f.relation) == relation]


def test_a_use_imports_the_item_from_the_file_declaring_its_qualified_name() -> None:
    assert facts(RecordRelations.IMPORTS_FROM.value) == [
        ("App\\Models\\User", None, None), ("Illuminate\\Http\\Request", None, None),
        ("App\\Support\\Helper", None, None), ("App\\Support\\Other", None, None),
        ("App\\Support\\helper_fn", None, None)]
    assert facts(RecordRelations.IMPORTS.value) == [
        ("User", "App\\Models\\User", None), ("Request", "Illuminate\\Http\\Request", "Req"),
        ("Helper", "App\\Support\\Helper", None), ("Other", "App\\Support\\Other", "O"),
        ("helper_fn", "App\\Support\\helper_fn", None)]


def test_extends_implements_and_trait_uses_are_heritage() -> None:
    assert facts(RecordRelations.INHERITS.value) == [
        ("Controller", None, None), ("Loggable", None, None), ("Cacheable", None, None), ("Base", None, None)]
    assert facts(RecordRelations.IMPLEMENTS.value) == [("HasName", None, None), ("Thing", "App\\Contracts", None)]


def test_the_type_table_reads_properties_parameters_and_new() -> None:
    table = parse_code(SRC, "php").type_table
    assert table == {"$this->repo": "UserRepo", "$this->cache": "Cache", "$repo": "UserRepo", "$mailer": "Mailer",
                     "$this->mailer": "Mailer", "$request": "Req", "$user": "User"}   # no builtin, no union


def test_new_is_a_call_to_the_class_and_receivers_keep_their_keyword() -> None:
    calls = [(f.to_name, f.receiver, f.receiver_type) for f in parse_code(SRC, "php").pending
             if getattr(f.relation, "value", f.relation) == RecordRelations.CALLS.value]
    assert ("User", None, None) in calls and ("view", None, None) in calls
    assert ("save", "$user", "User") in calls and ("find", "$this->repo", "UserRepo") in calls
    assert ("make", "self", None) in calls and ("boot", "parent", None) in calls
