"""A Python receiver's type comes from the bindings that reach it.

The type table was one per file, keyed by bare name, first binding wins: a test's local
`user = AnonymousUser()` typed every other `user`, and `self.user` too, since a member call's
receiver is its chain's last name. That was the largest class of django's INFERRED CALLS false
positives (`self.user.has_perm` -> AnonymousUser.has_perm, `runner.test_suite`, `finder.find`).
"""
import pytest

from app.config.constants.arangodb import RecordRelations

from .test_import_binding import _build, _targets

CALLS = RecordRelations.CALLS.value


pytestmark = pytest.mark.asyncio


MODELS = b'''
class User:
    def has_perm(self, perm):
        return True


class AnonymousUser:
    def has_perm(self, perm):
        return False


class Cache:
    def get_item(self, key):
        return key
'''

TESTS = b'''
from app.models import AnonymousUser, Cache, User

cache = Cache()


def anonymous_check():
    user = AnonymousUser()
    return user.has_perm("x")


def typed_param(user: User):
    return user.has_perm("x")


def untyped_param(user):
    return user.has_perm("x")


def module_binding():
    return cache.get_item("k")


class Holder:
    def __init__(self, member: User) -> None:
        self.member = member

    def check(self):
        return self.member.has_perm("x")


class Case:
    def setUp(self):
        self.user = User()

    def test_self_attribute(self):
        return self.user.has_perm("x")

'''


async def _calls(graph, index_file) -> dict[str, set[str]]:
    await index_file("recM", "app/models.py", MODELS, "python")
    await index_file("recT", "app/tests.py", TESTS, "python")
    await _build(graph)
    out: dict[str, set[str]] = {}
    for edge in graph.code_edges(CALLS):
        src = graph.blocks.get(edge["_from"].partition("/")[2]) or {}
        dst = graph.blocks.get(edge["_to"].partition("/")[2]) or {}
        out.setdefault(src.get("qualifiedName"), set()).add(dst.get("qualifiedName"))
    return out


async def test_a_local_types_only_its_own_function(graph, index_file) -> None:
    calls = await _calls(graph, index_file)
    assert "method:AnonymousUser.has_perm" in calls["function:anonymous_check"]
    assert calls["function:typed_param"] >= {"method:User.has_perm"}
    assert "method:AnonymousUser.has_perm" not in calls["function:typed_param"]
    assert "method:AnonymousUser.has_perm" not in calls.get("function:untyped_param", set())


CLASSES = b'''
import requests


class Foo:
    def ping(self):
        return 1


class Bar:
    def ping(self):
        return 2


class Client:
    def get(self):
        return 3


api = Client()


class A:
    def __init__(self):
        self.conn = Foo()

    def go(self):
        return self.conn.ping()


class B:
    def __init__(self):
        self.conn = Bar()

    def go(self):
        return self.conn.ping()


class C(A):
    def again(self):
        return self.conn.ping()


def shadowed(api):
    return api.get()


def module_level():
    return api.get()
'''


async def _edges_of(graph, index_file, src: bytes) -> dict[str, set[str]]:
    await index_file("recK", "app/classes.py", src, "python")
    await _build(graph)
    out: dict[str, set[str]] = {}
    for edge in graph.code_edges(CALLS):
        src_qn = (graph.blocks.get(edge["_from"].partition("/")[2]) or {}).get("qualifiedName")
        dst_qn = (graph.blocks.get(edge["_to"].partition("/")[2]) or {}).get("qualifiedName")
        out.setdefault(src_qn, set()).add(dst_qn)
    return out


async def test_a_self_attribute_is_typed_by_its_own_class_only(graph, index_file) -> None:
    """`self.conn = Foo()` in A and `self.conn = Bar()` in B: each class's `self.conn` is its own; a same-file
    subclass reads its base's."""
    calls = await _edges_of(graph, index_file, CLASSES)
    assert calls["method:A.go"] == {"method:Foo.ping"}
    assert calls["method:B.go"] == {"method:Bar.ping"}
    assert calls["method:C.again"] == {"method:Foo.ping"}


async def test_an_untyped_parameter_shadows_a_module_binding(graph, index_file) -> None:
    """`def shadowed(api): api.get()` calls the argument's `get`, not the module-level `api = Client()`. (A name
    that spells a class, `client`, is typed by that class whatever binds it.)"""
    calls = await _edges_of(graph, index_file, CLASSES)
    assert "method:Client.get" not in calls.get("function:shadowed", set())
    assert calls["function:module_level"] == {"method:Client.get"}


BASE_CONN = b'''
class Processor:
    def run(self):
        return 1


class Other:
    def run(self):
        return 2


class BaseConnector:
    def __init__(self, processor):
        self.processor = processor
'''

SUB_CONN = b'''
from app.base import BaseConnector, Other, Processor


class Jira(BaseConnector):
    def __init__(self, processor: Processor) -> None:
        super().__init__(processor)

    def sync(self):
        return self.processor.run()
'''

MIXED = b'''
from app.base import BaseConnector, Other, Processor


class Mixed(BaseConnector):
    def a(self, client: Processor):
        return client

    def b(self, client: Other):
        return client

    def sync(self):
        return self.client.run()
'''


async def test_an_inherited_attribute_takes_the_type_of_a_typed_parameter_of_its_name(graph, index_file) -> None:
    # pipeshub-py's connectors: `self.data_entities_processor.x()`, assigned in BaseConnector (another file),
    # typed by the subclass's own `__init__(self, data_entities_processor: DataSourceEntitiesProcessor)`.
    await index_file("recB", "app/base.py", BASE_CONN, "python")
    await index_file("recJ", "app/jira.py", SUB_CONN, "python")
    await index_file("recX", "app/mixed.py", MIXED, "python")
    await _build(graph)
    assert _targets(graph, CALLS, "app/jira.py") >= {"app/base.py::method:Processor.run"}
    # Two parameters named `client` with different types: no guess.
    assert not {"app/base.py::method:Processor.run", "app/base.py::method:Other.run"} & \
        _targets(graph, CALLS, "app/mixed.py")


def _confidence(graph, src_qn: str, dst_qn: str) -> str | None:
    for edge in graph.code_edges(CALLS):
        src = graph.blocks.get(edge["_from"].partition("/")[2]) or {}
        dst = graph.blocks.get(edge["_to"].partition("/")[2]) or {}
        if src.get("qualifiedName") == src_qn and dst.get("qualifiedName") == dst_qn:
            return edge["confidence"]
    return None


CONTAINER = b'''
from dependency_injector import containers, providers


class AppContainer(containers.DeclarativeContainer):
    config_service = providers.Singleton(dict)

    def wire(self):
        return 1


async def lifespan(app_container: AppContainer):
    app_container.config_service()
    return app_container.wire()
'''


async def test_a_typed_receiver_calls_no_field(graph, index_file) -> None:
    """`app_container.config_service()` calls the provider a field holds (dependency-injector), not a method of
    the container: no CALLS edge to the field (pipeshub-py: 16 false positives), while a real method still gets one."""
    await index_file("recC", "app/containers.py", CONTAINER, "python")
    await _build(graph)
    assert _targets(graph, CALLS, "app/containers.py") == {"app/containers.py::method:AppContainer.wire"}


HELPERS_MODELS = b'''
class Conn:
    def run(self):
        return 1


class Other:
    def run(self):
        return 2
'''


CONFTEST = b'''
import pytest

from app.conn import Conn


@pytest.fixture
def conn():
    return Conn()


@pytest.fixture
def streamed():
    conn = Conn()
    yield conn
'''

FIXTURE_TEST = b'''
import pytest

from app.conn import Other


@pytest.fixture
def other():
    return Other()


def test_conftest_fixture(conn):
    return conn.run()


def test_yield_fixture(streamed):
    return streamed.run()


def test_same_file_fixture(other):
    return other.run()


def helper(unknown):
    return unknown.run()
'''


async def test_a_parameter_takes_the_type_of_the_pytest_fixture_of_its_name(graph, index_file) -> None:
    """pytest hands a test parameter the fixture of its name, from the test file or the
    nearest conftest.py above it; a parameter no fixture is named after stays untyped."""
    await index_file("recM", "app/conn.py", HELPERS_MODELS, "python")
    await index_file("recC", "tests/conftest.py", CONFTEST, "python")
    await index_file("recF", "tests/unit/test_fixtures.py", FIXTURE_TEST, "python")
    await _build(graph)
    calls: dict[str, set[str]] = {}
    for edge in graph.code_edges(CALLS):
        src, dst = graph.blocks.get(edge["_from"].partition("/")[2]), graph.blocks.get(edge["_to"].partition("/")[2])
        if src and dst:
            calls.setdefault(src["qualifiedName"], set()).add(dst["qualifiedName"])
    assert calls["function:test_conftest_fixture"] == {"method:Conn.run"}
    assert calls["function:test_yield_fixture"] == {"method:Conn.run"}
    assert calls["function:test_same_file_fixture"] == {"method:Other.run"}
    assert not calls.get("function:helper")
    assert _confidence(graph, "function:test_conftest_fixture", "method:Conn.run") == "INFERRED"


SVC_MODELS = b'''
class ServiceA:
    def run(self):
        return 1


class ServiceB:
    def run(self):
        return 2
'''

CLASS_OVERRIDE = b'''
import pytest

from app.svc import ServiceA, ServiceB


@pytest.fixture
def svc():
    return ServiceA()


def test_module_level(svc):
    return svc.run()


class TestOverride:
    @pytest.fixture
    def svc(self):
        return ServiceB()

    def test_in_class(self, svc):
        return svc.run()
'''


async def test_a_test_class_fixture_overrides_the_module_fixture(graph, index_file) -> None:
    """pytest hands `TestOverride.test_in_class` the class's own `svc` (ServiceB), not the module's."""
    await index_file("pM", "app/svc.py", SVC_MODELS, "python")
    await index_file("pT", "tests/test_svc.py", CLASS_OVERRIDE, "python")
    await _build(graph)
    calls: dict[str, set[str]] = {}
    for edge in graph.code_edges(CALLS):
        src = (graph.blocks.get(edge["_from"].partition("/")[2]) or {}).get("qualifiedName")
        dst = (graph.blocks.get(edge["_to"].partition("/")[2]) or {}).get("qualifiedName")
        calls.setdefault(src, set()).add(dst)
    assert calls["function:test_module_level"] == {"method:ServiceA.run"}
    assert calls["method:TestOverride.test_in_class"] == {"method:ServiceB.run"}
