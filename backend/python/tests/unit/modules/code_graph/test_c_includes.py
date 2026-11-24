"""C/C++ `#include`s resolve to repo files, and calls see what a file's includes include (and their paired sources)."""
import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.block_projection import changed_includes
from app.modules.code_graph.module_refs import (
    includes_changed_ref,
    module_ref,
    module_refs_of_path,
)
from app.modules.code_graph.resolvers.c_family import resolve
from app.modules.parsers.code_parser.engine import parse_code

from .test_import_binding import _build, _targets

CALLS = RecordRelations.CALLS.value
IMPORTS_FROM = RecordRelations.IMPORTS_FROM.value
INHERITS = RecordRelations.INHERITS.value
FILES = {"src/net/conn.c", "src/net/conn.h", "src/util.h", "include/app/util.h", "lib/x/util.h", "lib/y/util.h"}


def test_an_include_is_looked_for_beside_the_includer_then_by_its_path_nearest_first() -> None:
    assert resolve("conn.h", "src/net/conn.c", FILES, None) == "src/net/conn.h"           # beside it
    assert resolve("../util.h", "src/net/conn.c", FILES, None) == "src/util.h"            # relative
    assert resolve("app/util.h", "src/net/conn.c", FILES, None) == "include/app/util.h"   # an -I folder
    assert resolve("util.h", "lib/x/a.c", FILES, None) == "lib/x/util.h"                  # the nearest
    assert resolve("util.h", "tools/a.c", FILES, None) is None                            # a tie names none
    assert resolve("stdio.h", "src/net/conn.c", FILES, None) is None                      # a system header


def test_an_angle_include_is_repo_code_only_when_its_path_places_it() -> None:
    files = {"src/config.h", "src/oatpp/core/Types.hpp", "src/main.c", "src/lib/x.c", "lib/conf/config.h"}
    # Quoted "config.h" with repo config.h files: placed by name, or, when two tie, unresolved.
    assert resolve("config.h", "src/main.c", files, None) == "src/config.h"
    assert resolve("config.h", "tools/x.c", files, None) is None
    # Angle <config.h> with a repo config.h: a system or vendored header, never the repo's.
    assert resolve("<config.h>", "src/main.c", files, None) is None
    # Angle <oatpp/core/Types.hpp>: its path places it in the repo.
    assert resolve("<oatpp/core/Types.hpp>", "src/main.c", files, None) == "src/oatpp/core/Types.hpp"
    # <stdio.h>: outside.
    assert resolve("<stdio.h>", "src/main.c", files, None) is None
    assert module_ref("<oatpp/core/Types.hpp>", "src/main.c") == "module:oatpp/core/Types"
    # The parser keeps the form: an angle include in its brackets, a quoted one bare.
    facts = parse_code(b'#include <oatpp/core/Types.hpp>\n#include "util.h"\n#include <stdio.h>\n', "c").pending
    assert [f.to_name for f in facts if str(getattr(f.relation, "value", f.relation)) == "IMPORTS_FROM"] == [
        "<oatpp/core/Types.hpp>", "util.h", "<stdio.h>"]


def test_an_include_names_a_file_for_incremental_builds_but_a_python_module_keeps_its_dots() -> None:
    assert module_ref("util.h", "src/main.c") == "module:util"
    assert module_ref("util.h", "src/main.c") in module_refs_of_path("src/util.c")   # the pair sees it too
    assert module_ref("pkg.c", "app/main.py") == "module:pkg/c"


@pytest.mark.asyncio
async def test_a_call_lands_on_the_body_in_the_included_headers_source(graph, c_repo) -> None:
    await c_repo({
        "src/net/conn.h": b"#ifndef CONN_H\n#define CONN_H\nint conn_open(int fd);\n#endif\n",
        "src/net/conn.c": b'#include "conn.h"\nint conn_open(int fd) { return fd; }\n',
        "src/server.c": b'#include "net/conn.h"\nint start(void) { return conn_open(3); }\n',
        # The same name in a file the server never includes: without the include, a tie.
        "tools/fake.c": b"int conn_open(int fd) { return 0; }\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "src/server.c") == {"src/net/conn.c::function:conn_open"}
    assert _targets(graph, IMPORTS_FROM, "src/server.c") == {"src/net/conn.h"}


@pytest.mark.asyncio
async def test_a_header_included_through_another_header_is_seen(graph, c_repo) -> None:
    await c_repo({
        "a/base.hpp": b"inline int base_id() { return 7; }\n",
        "a/mid.hpp": b'#include "base.hpp"\n',
        "b/main.cpp": b'#include "a/mid.hpp"\nint run() { return base_id(); }\n',
        "c/other.hpp": b"inline int base_id() { return 8; }\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "b/main.cpp") == {"a/base.hpp::function:base_id"}
    assert _targets(graph, IMPORTS_FROM, "b/main.cpp") == {"a/mid.hpp"}   # the direct include only


@pytest.mark.asyncio
async def test_a_cpp_base_class_is_inherited_without_its_template_arguments(graph, c_repo) -> None:
    await c_repo({
        "geo/shape.hpp": b"namespace geo {\nclass Shape {};\ntemplate <typename T> class Holder {};\n}\n",
        "geo/square.hpp": (b'#include "shape.hpp"\nnamespace geo {\n'
                           b"class Square : public Shape, private geo::Holder<int> {};\n}\n"),
    })
    await _build(graph)
    assert _targets(graph, INHERITS, "geo/square.hpp") == {"geo/shape.hpp::class:geo.Shape",
                                                           "geo/shape.hpp::class:geo.Holder"}


def test_only_a_changed_include_list_marks_a_c_file_for_its_transitive_includers() -> None:
    def rows(*includes: str) -> list[dict]:
        return [{"pendingEdges": [{"relation": IMPORTS_FROM, "toName": i} for i in includes]},
                {"pendingEdges": '[{"relation": "CALLS", "toName": "f"}]'}]   # Neo4j's JSON form
    mark = {includes_changed_ref("src/a.h")}
    assert changed_includes("src/a.h", rows("b.h"), rows("b.h")) == set()          # a body edit: no mark
    assert changed_includes("src/a.h", rows("b.h"), rows("b.h", "<c/d.h>")) == mark
    assert changed_includes("src/a.h", rows("b.h"), ()) == mark                    # emptied or deleted
    assert changed_includes("app/m.py", rows("b.h"), ()) == set()                  # only C/C++ keeps includes
