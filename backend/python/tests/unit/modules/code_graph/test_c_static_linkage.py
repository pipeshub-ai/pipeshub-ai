"""A C/C++ `static` free function has internal linkage, so no call from another translation unit
reaches it (valkey: `getMonotonicUs()` through a function pointer was linked to an unrelated static)."""
import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.edge_builder import build_code_graph_edges
from app.modules.parsers.code_parser.engine import parse_code
from app.modules.parsers.code_parser.models import STATIC

from .conftest import GROUP_ID, ORG_ID
from .test_import_binding import _build, _targets

CALLS = RecordRelations.CALLS.value
LUA = b"static long mono(void) { return 1; }\nlong lua_tick(void) { return mono(); }\n"


def _modifiers(source: bytes, language: str) -> dict[str, tuple[str, ...]]:
    return {s.name: s.modifiers for s in parse_code(source, language).symbols if s.kind in ("function", "method")}


def test_the_parser_marks_a_static_free_function_but_never_a_class_member() -> None:
    src = (b"static int h(void) { return 1; }\nstatic inline int i(void) { return 2; }\nint pub(void) { return 0; }\n"
           b"namespace ns { static void f() {} }\ntemplate<class T> static T t() { return T(); }\n"
           b"struct A { static int make() { return 1; } template<class T> static T tm() { return T(); } };\n")
    assert _modifiers(src.split(b"namespace")[0], "c") == {"h": (STATIC,), "i": (STATIC,), "pub": ()}
    assert _modifiers(src, "cpp") == {"h": (STATIC,), "i": (STATIC,), "pub": (), "f": (STATIC,), "t": (STATIC,),
                                      "make": (), "tm": ()}
    # Another family's `static` is no linkage word: a static method is a member like any other.
    assert STATIC not in _modifiers(b"class A { static int f() { return 1; } }\n", "java")["f"]
    assert STATIC not in _modifiers(b"class A { static int F() { return 1; } }\n", "csharp")["F"]


@pytest.mark.asyncio
async def test_a_name_guess_from_another_file_never_reaches_a_static_but_its_own_file_does(graph, c_repo) -> None:
    await c_repo({"src/lua/fn.c": LUA, "src/server.c": b"long now(void) { return mono(); }\n"})
    await _build(graph)
    assert _targets(graph, CALLS, "src/server.c") == set()
    assert _targets(graph, CALLS, "src/lua/fn.c") == {"src/lua/fn.c::function:mono"}


@pytest.mark.asyncio
async def test_a_call_reaches_the_non_static_definition_when_one_exists(graph, c_repo) -> None:
    # Without the rule the static, nearer by path, wins.
    await c_repo({"src/lua/fn.c": LUA, "lib/mono.c": b"long mono(void) { return 2; }\n",
                  "src/lua/server.c": b"long now(void) { return mono(); }\n"})
    await _build(graph)
    assert _targets(graph, CALLS, "src/lua/server.c") == {"lib/mono.c::function:mono"}


@pytest.mark.asyncio
async def test_a_headers_paired_source_static_is_not_reached_from_another_unit(graph, c_repo) -> None:
    await c_repo({
        "src/x.h": b"long mono(void);\nint x_cb(void);\n",
        "src/x.c": b'#include "x.h"\n' + LUA + b"static int x_cb(void) { return 0; }\n",
        "src/y.c": b'#include "x.h"\nlong y(void) { register_cb(x_cb); return mono(); }\n',
    })
    await _build(graph)
    assert _targets(graph, CALLS, "src/y.c") == set()


@pytest.mark.asyncio
async def test_a_static_the_unit_includes_is_reached(graph, c_repo) -> None:
    await c_repo({
        "src/util.h": b"static inline int clamp(int v) { return v; }\n",
        "src/part.c": b"static int part_init(void) { return 3; }\n",
        "src/all.c": b'#include "util.h"\n#include "part.c"\nint run(void) { return clamp(part_init()); }\n',
    })
    await _build(graph)
    assert _targets(graph, CALLS, "src/all.c") == {"src/util.h::function:clamp", "src/part.c::function:part_init"}


@pytest.mark.asyncio
async def test_a_static_member_function_is_reached_from_another_unit(graph, c_repo) -> None:
    # A member's body in pool.cpp, outside b.cpp's unit: a member has external linkage, `static` or not.
    await c_repo({"lib/pool.hpp": b"struct Pool { static int make(); };\n",
                  "lib/pool.cpp": b'#include "pool.hpp"\nint Pool::make() { return 1; }\n',
                  "app/b.cpp": b'#include "lib/pool.hpp"\nint r() { return Pool::make(); }\n'})
    await _build(graph)
    assert _targets(graph, CALLS, "app/b.cpp") == {"lib/pool.cpp::method:Pool.make"}


@pytest.mark.asyncio
async def test_a_headers_static_is_still_name_guessed_from_a_file_that_does_not_include_it(graph, c_repo) -> None:
    # jemalloc: one header's inline calls another's `static inline`; a source includes both, in order.
    await c_repo({"inc/arena.h": b"static inline int arena_ind(void) { return 1; }\n",
                  "inc/choose.h": b"static inline int choose(void) { return arena_ind(); }\n"})
    await _build(graph)
    assert _targets(graph, CALLS, "inc/choose.h") == {"inc/arena.h::function:arena_ind"}


@pytest.mark.asyncio
async def test_a_unity_builds_sibling_source_reaches_the_static(graph, c_repo) -> None:
    await c_repo({"src/a.c": b"static int helper(void) { return 1; }\n",
                  "src/b.c": b"int use(void) { return helper(); }\n",
                  "src/unity.c": b'#include "a.c"\n#include "b.c"\n'})
    await _build(graph)
    assert _targets(graph, CALLS, "src/b.c") == {"src/a.c::function:helper"}


@pytest.mark.asyncio
async def test_an_included_source_reaches_its_includers_static(graph, c_repo) -> None:
    # raylib: rcore.c defines statics, then includes the platform source that calls them.
    await c_repo({"src/rcore.c": b'static int core_helper(void) { return 1; }\n#include "plat/desktop.c"\n',
                  "src/plat/desktop.c": b"int init_platform(void) { return core_helper(); }\n"})
    await _build(graph)
    assert _targets(graph, CALLS, "src/plat/desktop.c") == {"src/rcore.c::function:core_helper"}


@pytest.mark.parametrize(("before", "after"), [
    (b"static long mono(void) { return 2; }\n", b"long mono(void) { return 2; }\n"),
    (b"long mono(void) { return 2; }\n", b"static long mono(void) { return 2; }\n"),
], ids=["loses-static", "gains-static"])
@pytest.mark.asyncio
async def test_an_incremental_build_rebinds_callers_when_a_function_gains_or_loses_static(
        graph, index_file, before: bytes, after: bytes) -> None:
    await index_file("r0", "lib/mono.c", before, "c")
    await index_file("r1", "src/server.c", b"long now(void) { return mono(); }\n", "c")
    await _build(graph)
    was = _targets(graph, CALLS, "src/server.c")
    graph.changed_names.clear()  # names from earlier edits have expired
    await index_file("r0", "lib/mono.c", after, "c")
    await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                 touched_record_ids={"r0"}, facts_source=graph)
    incremental = _targets(graph, CALLS, "src/server.c")
    await _build(graph)
    assert incremental == _targets(graph, CALLS, "src/server.c") != was


@pytest.mark.asyncio
async def test_a_header_never_reaches_a_static_of_the_source_including_it(graph, c_repo) -> None:
    # valkey: monotonic.h's inline calls the extern pointer getMonotonicUs; function_lua.c includes monotonic.h
    # under one #if and defines its own static under the other.
    await c_repo({"src/mono.h": b"static inline long elapsed(void) { return mono(); }\n",
                  "src/lua/fn.c": b'#include "../mono.h"\n' + LUA})
    await _build(graph)
    assert _targets(graph, CALLS, "src/mono.h") == set()


@pytest.mark.asyncio
async def test_a_header_call_its_includers_static_may_mean_is_not_given_to_another_definition(
        graph, c_repo) -> None:
    # Ambiguous before the rule (two `mono`); the includer's static must not hand the call to other.c's.
    await c_repo({"src/m.h": b"static inline long elapsed(void) { return mono(); }\n",
                  "src/fn.c": b'#include "m.h"\n' + LUA,
                  "lib/other.c": b"long mono(void) { return 2; }\n"})
    await _build(graph)
    assert _targets(graph, CALLS, "src/m.h") == set()
