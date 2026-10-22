from app.modules.parsers.code_parser.engine import parse_code
from app.modules.parsers.code_parser.models import Relation


def _calls_by_symbol(source: str) -> dict[str | None, set[str]]:
    parsed = parse_code(source.encode(), "typescript")
    calls: dict[str | None, set[str]] = {}
    for fact in parsed.pending:
        if fact.relation != Relation.CALLS:
            continue
        owner = parsed.symbols[fact.from_symbol].name if fact.from_symbol is not None else None
        calls.setdefault(owner, set()).add(fact.to_name)
    return calls


def test_an_expression_bodied_arrow_calls_its_body() -> None:
    calls = _calls_by_symbol("const load = (id) => fetchUser(id);\n")
    assert "fetchUser" in calls.get("load", set())


def test_a_method_named_like_a_builtin_global_is_still_a_call() -> None:
    calls = _calls_by_symbol(
        "class Svc {\n  run() { return this.fetch(1); }\n  fetch(x) { return x; }\n}\n"
    )
    assert "fetch" in calls.get("run", set())


def test_a_bare_builtin_global_is_still_dropped() -> None:
    calls = _calls_by_symbol("function run() { return fetch('/x'); }\n")
    assert "fetch" not in calls.get("run", set())


def test_calls_inside_a_local_export_are_collected() -> None:
    calls = _calls_by_symbol(
        "export const store = createStore(reducer);\n"
        "export default connect(mapState)(App);\n"
    )
    assert {"createStore", "connect"} <= set().union(*calls.values())
