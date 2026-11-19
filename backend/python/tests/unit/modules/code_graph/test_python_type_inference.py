"""Python receivers typed by deeper static inference: unpacked tuples and loop variables, what a helper
returns, through other helpers and across imports, method return types and attribute chains. The parser
spells what it cannot name as a chain (`=wrap()`), the edge builder evaluates it in the files that define
each link."""
import pytest

from app.config.constants.arangodb import RecordRelations

from .test_import_binding import _build

CALLS = RecordRelations.CALLS.value


pytestmark = pytest.mark.asyncio


LIB = b'''
class Client:
    def send(self):
        return 1

    def close(self):
        return 2


class Emitter:
    def emit(self):
        return 3


class Person:
    def greet(self):
        return 4


class Manager:
    def disable(self):
        return 5


class View:
    def redirect_url(self):
        return 6


def make_client() -> Client:
    return Client()


def wrap():
    return make_client()


def build_client():
    client = Client()
    return client


class Factory:
    def client(self) -> Client:
        return Client()

    def again(self):
        return self

    @classmethod
    def create(cls):
        return cls()


class Response:
    def json(self):
        return 7


class Http:
    def execute(self) -> Response:
        return Response()


class Service:
    def __init__(self, factory: Factory):
        self.http = factory.client()
        self.registry = None

    def start(self):
        self.registry = Manager()

    @property
    def view(self) -> View:
        return View()


class Model:
    objects = Manager()


class Ghost:
    def make(self) -> Client:
        return Client()


class Mailer:
    # Shares Client's and Person's method names, so no call here is linked by a method name only one class has.
    def send(self):
        return 8

    def greet(self):
        return 9
'''

APP = b'''
from app.lib.missing import Ghost
from app.lib import Client, Emitter, Factory, Http, Manager, Model, Person, Service, View, build_client, wrap


class Hub:
    def __init__(self, emitters: list[Emitter]):
        self._emitters: list[Emitter] = list(emitters)

    def fan_out(self):
        for e in self._emitters:
            e.emit()


def by_key(people: dict[str, Person]):
    for _name, person in people.items():
        person.greet()


def by_index(people: list[Person]):
    return [p.greet() for p in people]


def untyped_loop(things):
    for t in things:
        t.greet()


def literal_pair():
    client, view = Client(), View()
    view.redirect_url()


async def _manager(r) -> tuple[Manager, dict]:
    return Manager(), {}


async def unpack_helper(r):
    manager, _ctx = await _manager(r)
    manager.disable()


def two_levels():
    w = wrap()
    w.send()


def call_receiver():
    wrap().close()
    View().redirect_url()


def local_helper():
    c = build_client()
    c.close()


def method_returns(factory: Factory, conn: Http):
    c = factory.client()
    c.send()
    conn.execute().json()
    Factory.create().again().client().close()


class Sub(Service):
    def run(self):
        self.http.send()
        self.registry.disable()
        self.view.redirect_url()
        Model.objects.disable()

    def unknown(self):
        self.missing.send()


def broken_import(client: Ghost):
    client.make().send()


def two_values(flag):
    x = wrap()
    if flag:
        x = build_client().close
    x.send()
'''


async def _calls(graph, index_file) -> dict[str, set[str]]:
    await index_file("recL", "app/lib.py", LIB, "python")
    await index_file("recA", "app/app.py", APP, "python")
    await _build(graph)
    out: dict[str, set[str]] = {}
    for edge in graph.code_edges(CALLS):
        src = graph.blocks.get(edge["_from"].partition("/")[2]) or {}
        dst = graph.blocks.get(edge["_to"].partition("/")[2]) or {}
        out.setdefault(src.get("qualifiedName"), set()).add(dst.get("qualifiedName"))
    return out


async def test_a_loop_variable_takes_the_annotated_containers_element(graph, index_file) -> None:
    calls = await _calls(graph, index_file)
    assert "method:Emitter.emit" in calls["method:Hub.fan_out"]
    assert "method:Person.greet" in calls["function:by_key"]
    assert "method:Person.greet" in calls["function:by_index"]
    assert "method:Person.greet" not in calls.get("function:untyped_loop", set())


async def test_a_method_return_type_types_the_value(graph, index_file) -> None:
    calls = await _calls(graph, index_file)
    # A name given two values, and an attribute no class gives a type, are not typed.
    assert "method:Client.send" not in calls.get("function:two_values", set())
    assert calls.get("method:Sub.unknown", set()) == set()
    assert {"method:Client.send", "method:Response.json", "method:Client.close"} <= calls["function:method_returns"]


async def test_a_type_imported_from_a_module_the_resolver_cannot_place_starts_no_chain(graph, index_file) -> None:
    # `from app.lib.missing import Ghost` fails at run time; the repo's one Ghost is no guess down a chain.
    calls = await _calls(graph, index_file)
    assert "method:Client.send" not in calls.get("function:broken_import", set())


LONG_CHAIN = ("class Client:\n    def send(self):\n        return 1\n\n\n"
              "class Http:\n    def client(self) -> Client:\n        return Client()\n\n\n"
              "def short(h: Http):\n    return h.client().send()\n\n\n"
              "def generated(h: Http):\n    return h." + ".".join(f"a{i}" for i in range(600)) + ".send()\n").encode()


async def test_a_generated_chain_hundreds_of_links_long_leaves_the_file_parsed(graph, index_file) -> None:
    """A 600-link `h.a0.a1...` chain is not spelled for the builder (it would exhaust the stack and lose the
    file); the file still parses and a short chain in it is still typed."""
    await index_file("recG", "app/gen.py", LONG_CHAIN, "python")
    await _build(graph)
    calls = {}
    for edge in graph.code_edges(CALLS):
        src = graph.blocks.get(edge["_from"].partition("/")[2]) or {}
        dst = graph.blocks.get(edge["_to"].partition("/")[2]) or {}
        calls.setdefault(src.get("qualifiedName"), set()).add(dst.get("qualifiedName"))
    assert "method:Client.send" in calls["function:short"]


def _hops(name: str, count: int) -> str:
    return (f"def {name}(h: Http):\n    a0 = h.client()\n"
            + "".join(f"    a{i} = a{i - 1}.nxt\n" for i in range(1, count + 1)) + f"    return a{count}.send()\n\n\n")


ASSIGNMENT_HOPS = ("class Client:\n    nxt: 'Client'\n\n    def send(self):\n        return 1\n\n\n"
                   "class Http:\n    def client(self) -> Client:\n        return Client()\n\n\n"
                   + _hops("few", 5) + _hops("generated", 300)).encode()


async def test_a_generated_run_of_assignments_hundreds_long_leaves_the_file_parsed(graph, index_file) -> None:
    """`a1 = a0.nxt` ... `a300 = a299.nxt`: each name read through its assignment recursed again with a fresh link
    count, so the run exhausted the stack and lost the file. The hops now share the chain's budget; a short run is
    still typed."""
    await index_file("recH", "app/hops.py", ASSIGNMENT_HOPS, "python")
    await _build(graph)
    calls = {}
    for edge in graph.code_edges(CALLS):
        src = graph.blocks.get(edge["_from"].partition("/")[2]) or {}
        dst = graph.blocks.get(edge["_to"].partition("/")[2]) or {}
        calls.setdefault(src.get("qualifiedName"), set()).add(dst.get("qualifiedName"))
    assert "method:Client.send" in calls["function:few"]
