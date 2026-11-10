"""C++ calls through a class qualifier, a typed receiver, `this->` and an out-of-line method's class."""
import pytest

from app.config.constants.arangodb import RecordRelations

from .test_import_binding import _build, _targets

CALLS = RecordRelations.CALLS.value

ENV_HPP = b"namespace oatpp { namespace base {\nclass Environment {\n public:\n  static long tick();\n};\n}}\n"
ENV_CPP = b'#include "env.hpp"\nnamespace oatpp { namespace base {\nlong Environment::tick() { return 1; }\n}}\n'
CONN_HPP = (b"#include <memory>\nclass Conn {\n public:\n  void open();\n  static std::shared_ptr<Conn> createShared();\n};\n"
            b"class Other {\n public:\n  void open();\n};\n")


@pytest.mark.asyncio
async def test_a_class_qualified_call_reaches_the_out_of_line_method_in_another_folder(graph, c_repo) -> None:
    await c_repo({
        "include/oatpp/base/env.hpp": ENV_HPP,
        "src/oatpp/base/env.cpp": ENV_CPP.replace(b'"env.hpp"', b'"oatpp/base/env.hpp"'),
        "test/checker.cpp": b'#include "oatpp/base/env.hpp"\nlong elapsed() { return oatpp::base::Environment::tick(); }\n',
    })
    await _build(graph)
    assert _targets(graph, CALLS, "test/checker.cpp") == {"src/oatpp/base/env.cpp::method:oatpp.base.Environment.tick"}


@pytest.mark.asyncio
async def test_a_typed_receiver_a_smart_pointer_and_an_auto_factory_call_their_class(graph, c_repo) -> None:
    await c_repo({
        "net/conn.hpp": CONN_HPP,
        "net/conn.cpp": (b'#include "conn.hpp"\nvoid Conn::open() {}\nstd::shared_ptr<Conn> Conn::createShared() { return 0; }\n'
                         b"void Other::open() {}\n"),
        "app/main.cpp": (b'#include "net/conn.hpp"\nvoid a(Conn* c) { c->open(); }\n'
                         b"void b() { std::shared_ptr<Conn> s; s->open(); }\n"
                         b"void d() { auto x = Conn::createShared(); x->open(); }\n"),
    })
    await _build(graph)
    assert _targets(graph, CALLS, "app/main.cpp") == {"net/conn.cpp::method:Conn.open",
                                                      "net/conn.cpp::method:Conn.createShared"}


@pytest.mark.asyncio
async def test_this_and_a_bare_call_in_an_out_of_line_method_stay_in_its_class(graph, c_repo) -> None:
    await c_repo({
        "srv/server.hpp": b"class Server {\n public:\n  void run();\n  void stop();\n};\nvoid stop();\n",
        "srv/server.cpp": (b'#include "server.hpp"\nvoid Server::run() { this->stop(); stop(); }\n'
                           b"void Server::stop() {}\nvoid stop() {}\n"),
    })
    await _build(graph)
    assert _targets(graph, CALLS, "srv/server.cpp") == {"srv/server.cpp::method:Server.stop"}


@pytest.mark.asyncio
async def test_two_same_named_classes_each_keep_their_own_out_of_line_methods(graph, c_repo) -> None:
    foo_hpp = b"class Foo {\n public:\n  void go();\n  void helper();\n};\n"
    foo_cpp = b'#include "foo.hpp"\nvoid Foo::go() { helper(); this->helper(); }\nvoid Foo::helper() {}\n'
    await c_repo({"a/foo.hpp": foo_hpp, "a/foo.cpp": foo_cpp, "b/foo.hpp": foo_hpp, "b/foo.cpp": foo_cpp})
    await _build(graph)
    assert _targets(graph, CALLS, "a/foo.cpp") == {"a/foo.cpp::method:Foo.helper"}
    assert _targets(graph, CALLS, "b/foo.cpp") == {"b/foo.cpp::method:Foo.helper"}


@pytest.mark.asyncio
async def test_a_call_on_a_mixin_runs_the_base_method_with_a_body_not_the_pure_virtual(graph, c_repo) -> None:
    # The pure virtual has no body (_body_of is None), so the base walk's declaration flag passes over it.
    await c_repo({
        "m/thing.hpp": (b"class IThing {\n public:\n  virtual void m() = 0;\n};\n"
                        b"class ThingImpl {\n public:\n  void m() {}\n};\n"
                        b"class C : public IThing, public ThingImpl {};\n"),
        "m/use.cpp": b'#include "thing.hpp"\nvoid use(C& c) { c.m(); }\n',
    })
    await _build(graph)
    assert _targets(graph, CALLS, "m/use.cpp") == {"m/thing.hpp::method:ThingImpl.m"}



@pytest.mark.asyncio
async def test_a_method_a_class_inherits_is_found_on_its_base(graph, c_repo) -> None:
    await c_repo({
        "k/base.hpp": b"class Base {\n public:\n  void ping() {}\n};\nclass Derived : public Base {};\n",
        "k/use.cpp": b'#include "base.hpp"\nvoid use(Derived& d) { d.ping(); }\n',
    })
    await _build(graph)
    assert _targets(graph, CALLS, "k/use.cpp") == {"k/base.hpp::method:Base.ping"}
