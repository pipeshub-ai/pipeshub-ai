"""INDIRECT_CALL facts: a function named as a value, not called, may run later.

Value positions are call arguments, dict/list/object tables, assignment
right-hand sides and return values. The shadow guard: a name the enclosing
function binds itself (a parameter, a local, a loop or `except` target) is never
a reference to a module function, so it yields no fact.
"""
from __future__ import annotations

from app.modules.parsers.code_parser.engine import parse_code
from app.modules.parsers.code_parser.models import Relation


def _refs(source: str, language: str) -> dict[str | None, set[tuple[str, str]]]:
    """Owner block name -> {(target name, context)} of the file's INDIRECT_CALL facts."""
    parsed = parse_code(source.encode(), language)
    out: dict[str | None, set[tuple[str, str]]] = {}
    for fact in parsed.pending:
        if fact.relation == Relation.INDIRECT_CALL:
            owner = parsed.symbols[fact.from_symbol].name if fact.from_symbol is not None else None
            out.setdefault(owner, set()).add((fact.to_name, fact.context))
    return out


def _all(refs: dict) -> set[tuple[str, str]]:
    return set().union(*refs.values()) if refs else set()


def _calls(source: str, language: str) -> set[str]:
    return {f.to_name for f in parse_code(source.encode(), language).pending if f.relation == Relation.CALLS}


class TestPythonValuePositions:
    def test_a_function_passed_as_an_argument(self) -> None:
        refs = _refs("def settle(ref):\n    return submit(on_paid, ref)\n", "python")
        assert ("on_paid", "argument") in refs["settle"]

    def test_a_keyword_argument(self) -> None:
        refs = _refs("def start():\n    Thread(target=worker).start()\n", "python")
        assert ("worker", "argument") in refs["start"]

    def test_dict_values_and_list_elements(self) -> None:
        refs = _refs("def route(kind):\n    table = {'a': on_a}\n    chain = [on_b, on_c]\n", "python")
        assert {("on_a", "collection"), ("on_b", "collection"), ("on_c", "collection")} <= refs["route"]

    def test_an_assignment_right_hand_side(self) -> None:
        refs = _refs("def retry(ref):\n    handler = on_failed\n    return handler(ref)\n", "python")
        assert ("on_failed", "assignment") in refs["retry"]

    def test_an_attribute_assignment(self) -> None:
        refs = _refs("def wire(obj):\n    obj.handler = on_done\n", "python")
        assert ("on_done", "assignment") in refs["wire"]

    def test_a_returned_function(self) -> None:
        refs = _refs("def pick():\n    return on_e\n", "python")
        assert ("on_e", "return") in refs["pick"]

    def test_a_bound_method_on_self(self) -> None:
        src = "class A:\n    def run(self):\n        submit(self.on_x)\n    def on_x(self):\n        pass\n"
        facts = [f for f in parse_code(src.encode(), "python").pending if f.relation == Relation.INDIRECT_CALL]
        assert [(f.to_name, f.is_member_call, f.receiver) for f in facts] == [("on_x", True, "self")]

    def test_properties_and_their_setters_are_flagged_as_accessors(self) -> None:
        # The builder drops a member value landing on one: naming it reads it.
        src = ("class Response:\n    @property\n    def text(self):\n        return '{}'\n\n"
               "    @text.setter\n    def text(self, v):\n        pass\n\n"
               "    @functools.cached_property\n    def body(self):\n        return b''\n\n"
               "    @staticmethod\n    def handle(r):\n        return r\n")
        flagged = {(s.name, s.is_accessor) for s in parse_code(src.encode(), "python").symbols if s.kind == "method"}
        assert flagged == {("text", True), ("body", True), ("handle", False)}

    def test_a_member_of_another_object_is_not_a_value_reference(self) -> None:
        assert _all(_refs("def f(x):\n    submit(utils.parse, x.cb)\n", "python")) == set()

    def test_a_module_level_table(self) -> None:
        refs = _refs("COMMANDS = {'ship': on_ship}\n", "python")
        assert ("on_ship", "collection") in _all(refs)

    def test_the_callee_itself_is_a_call_not_a_value(self) -> None:
        src = "def settle(ref):\n    return submit(ref)\n"
        assert _all(_refs(src, "python")) == set()
        assert "submit" in _calls(src, "python")

    def test_builtins_and_literals_yield_nothing(self) -> None:
        assert _all(_refs("def f(xs):\n    return map(str, xs), [1, 'a', None]\n", "python")) == set()

    def test_a_base_class_list_is_not_an_argument(self) -> None:
        assert _all(_refs("class A(Base, metaclass=Meta):\n    pass\n", "python")) == set()


class TestPythonShadowGuard:
    def test_parameters_of_every_form(self) -> None:
        src = ("def f(a, b: int, c=1, *args, d: str = 'x', **kw):\n"
               "    return submit(a, b, c, args, d, kw)\n")
        assert _all(_refs(src, "python")) == set()

    def test_locals_loop_with_except_and_walrus_targets(self) -> None:
        src = ("def f(xs):\n"
               "    x, (y, z) = load()\n"
               "    for i in xs:\n"
               "        submit(i)\n"
               "    with open('p') as fh:\n"
               "        submit(fh)\n"
               "    try:\n"
               "        pass\n"
               "    except E as err:\n"
               "        submit(err)\n"
               "    if (n := 3):\n"
               "        submit(n)\n"
               "    return submit(x, y, z)\n")
        assert _all(_refs(src, "python")) == set()

    def test_comprehension_and_lambda_names(self) -> None:
        src = "def f(xs):\n    return [submit(w) for w in xs], (lambda q: submit(q))\n"
        assert _all(_refs(src, "python")) == set()

    def test_a_declared_global_is_the_modules_and_a_rebound_one_is_left_alone(self) -> None:
        # `global cb` names the module's cb; rebinding it here is an unclear scope.
        assert _all(_refs("def f():\n    global cb\n    submit(cb)\n", "python")) == {("cb", "argument")}
        rebound = _all(_refs("def f():\n    global cb\n    cb = other\n    submit(cb)\n", "python"))
        assert not {name for name, _ in rebound} & {"cb"}

    def test_a_class_body_reads_an_outer_name_until_it_binds_it(self) -> None:
        # The body runs top to bottom: `handler = handler` and `b = property(b)` read the outer name.
        src = ("class Storage:\n    handler = handler\n\n    def b(self):\n        return 1\n    b = property(b)\n"
               "    later = handler\n")
        refs = _all(_refs(src, "python"))
        assert {("handler", "assignment"), ("b", "argument")} <= refs
        assert sum(1 for name, _ in refs if name == "handler") == 1  # `later = handler` reads Storage.handler

    def test_an_outer_parameter_shadows_inside_a_closure(self) -> None:
        src = "def outer(cb):\n    def inner():\n        return submit(cb)\n    return inner\n"
        refs = _refs(src, "python")
        assert ("cb", "argument") not in _all(refs)
        # A nested def is a real function, so returning it is a reference.
        assert ("inner", "return") in refs["outer"]


class TestPythonTableDispatch:
    def test_subscripting_a_module_table_and_calling_it_reaches_every_value(self) -> None:
        src = ("def on_ship(ref):\n    return ref\n\n"
               "COMMANDS = {'ship': on_ship, 'skip': self_check}\n\n"
               "def run_command(name, ref):\n    return COMMANDS[name](ref)\n")
        refs = _refs(src, "python")
        assert {("on_ship", "dispatch"), ("self_check", "dispatch")} <= refs["run_command"]

    def test_a_local_shadowing_the_table_name_dispatches_nothing(self) -> None:
        src = ("COMMANDS = {'ship': on_ship}\n\n"
               "def run(COMMANDS, name):\n    return COMMANDS[name]()\n")
        assert ("on_ship", "dispatch") not in _all(_refs(src, "python"))

    def test_a_name_that_is_not_a_table_dispatches_nothing(self) -> None:
        src = "COMMANDS = make()\n\ndef run(name):\n    return COMMANDS[name]()\n"
        assert not any(ctx == "dispatch" for _, ctx in _all(_refs(src, "python")))


class TestTypeScriptValuePositions:
    def test_every_position(self) -> None:
        src = ("export function f(): void {\n"
               "  submit(onPaid, this.onY);\n"
               "  const t = { a: onA, onB, ...spread };\n"
               "  const l = [onC, onD];\n"
               "  const x = onX;\n"
               "  obj.cb = onF;\n"
               "  return onG;\n"
               "}\n")
        got = _refs(src, "typescript")["f"]
        assert {("onPaid", "argument"), ("onY", "argument"), ("onA", "collection"), ("onB", "collection"),
                ("onC", "collection"), ("onD", "collection"), ("onX", "assignment"), ("onF", "assignment"),
                ("onG", "return")} <= got
        assert not any(name == "spread" for name, _ in got)

    def test_get_and_set_accessors_are_flagged_and_methods_are_not(self) -> None:
        src = ("class History {\n  get isEmpty(): boolean { return true; }\n"
               "  static get count(): number { return 1; }\n  set size(v: number) {}\n  onChange(): void {}\n}\n")
        flagged = {(s.name, s.is_accessor) for s in parse_code(src.encode(), "typescript").symbols
                   if s.kind == "method"}
        assert flagged == {("isEmpty", True), ("count", True), ("size", True), ("onChange", False)}

    def test_an_event_subscription(self) -> None:
        refs = _refs("export function sub(bus: Emitter): void {\n  bus.on('ship', onShip);\n}\n", "typescript")
        assert ("onShip", "argument") in refs["sub"]

    def test_a_constructor_argument(self) -> None:
        refs = _refs("export function f(): void {\n  new Worker(handle);\n}\n", "typescript")
        assert ("handle", "argument") in refs["f"]

    def test_undefined_and_builtins_yield_nothing(self) -> None:
        src = "export function f(): void {\n  g(undefined, console, JSON);\n}\n"
        assert _all(_refs(src, "typescript")) == set()


class TestTypeScriptShadowGuard:
    def test_parameters_and_destructuring(self) -> None:
        src = ("function f(a: number, b = 1, { c, d: e }: T, [g, ...h]: U, ...rest: V) {\n"
               "  submit(a, b, c, e, g, h, rest);\n"
               "}\n")
        assert _all(_refs(src, "typescript")) == set()

    def test_locals_loops_and_catch(self) -> None:
        src = ("function f(xs: X[]) {\n"
               "  const x = load(), { y } = obj;\n"
               "  let z;\n"
               "  for (const i of xs) { submit(i); }\n"
               "  try {} catch (err) { submit(err); }\n"
               "  submit(x, y, z);\n"
               "}\n")
        assert all(name in ("load", "obj") for name, _ in _all(_refs(src, "typescript")))

    def test_an_arrow_callback_parameter_and_the_enclosing_one(self) -> None:
        src = ("class E {\n  emit(name: string, ref: string) {\n"
               "    return this.list.map((listener) => listener(ref)).filter((x) => keep(x, name));\n"
               "  }\n}\n")
        assert _all(_refs(src, "typescript")) == set()


class TestTypeScriptTableDispatch:
    def test_a_module_object_table(self) -> None:
        src = ("const ROUTES: Record<string, F> = { open: onOpen, close: onClose };\n"
               "export function dispatch(name: string, ref: string): string {\n"
               "  return ROUTES[name](ref);\n"
               "}\n")
        refs = _refs(src, "typescript")
        assert {("onOpen", "dispatch"), ("onClose", "dispatch")} <= refs["dispatch"]

    def test_an_exported_array_table(self) -> None:
        src = "export const STEPS = [stepA];\nexport function run(i: number) {\n  return STEPS[i]();\n}\n"
        assert ("stepA", "dispatch") in _refs(src, "typescript")["run"]


class TestJsxPropHandlers:
    """`onClick={handle}` hands a function to the component to run later."""

    SRC = ("export function Panel(props: P) {\n  const local = props.x;\n"
           "  return <Button onClick={handleClick} onHover={this.onHover} label={local}>{renderBody}</Button>;\n}\n")

    def test_an_attribute_value_is_a_prop_reference(self) -> None:
        got = _refs(self.SRC, "tsx")["Panel"]
        assert {("handleClick", "prop"), ("onHover", "prop")} <= got

    def test_a_local_or_a_child_expression_is_not(self) -> None:
        got = _all(_refs(self.SRC, "tsx"))
        assert not any(name in ("local", "renderBody") for name, _ in got)

    def test_other_families_and_non_jsx_values_keep_their_context(self) -> None:
        """Only a JSX attribute is a prop: a keyword argument or an object property is not."""
        assert _refs("def panel():\n    return button(on_click=handle)\n", "python")["panel"] == {
            ("handle", "argument")}
        got = _refs("export function f(): void {\n  render({ onClick: handle });\n}\n", "typescript")["f"]
        assert got and not any(context == "prop" for _, context in got)


def test_languages_without_value_references_emit_none() -> None:
    src = "class A {\n  void run() {\n    submit(handler);\n  }\n}\n"
    assert _all(_refs(src, "java")) == set()
