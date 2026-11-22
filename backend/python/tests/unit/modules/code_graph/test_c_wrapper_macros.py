"""A C/C++ call through a function-like wrapper macro reaches the one repo function the macro expands to."""
import json
from collections.abc import Awaitable, Callable

import pytest

from app.config.constants.arangodb import CollectionNames, RecordRelations
from app.modules.code_graph.edge_builder import (
    RESOLUTION_WRAPPER,
    build_code_graph_edges,
)
from app.modules.code_graph.symbol_index import BlockRow
from app.modules.parsers.code_parser.engine import parse_code
from app.schema.arango.documents import block_schema
from app.schema.node_schema_registry import JSON_ENCODED_FIELDS
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

from .conftest import GROUP_ID, ORG_ID

CALLS = RecordRelations.CALLS.value
LOG_C = b'#include "log.h"\nvoid _serverLog(int l, const char *f, ...) { }\nint other(int x) { return x; }\n'
SERVER_LOG = "src/log.c::function:_serverLog"


@pytest.fixture
def c_repo(graph, index_file) -> Callable[[dict[str, bytes]], Awaitable[object]]:
    """Index the files, build, and return the build result."""
    async def _repo(files: dict[str, bytes]) -> object:
        for i, (path, source) in enumerate(files.items()):
            await index_file(f"r{i}", path, source, "cpp" if path.endswith((".cpp", ".hpp")) else "c")
        return await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID,
                                            record_group_id=GROUP_ID)
    return _repo


def _calls(graph, from_path: str) -> set[tuple[str, str, str | None, str | None]]:
    """(caller, callee, resolution, confidence) of the CALLS edges from *from_path*'s blocks."""
    out = set()
    for edge in graph.code_edges(CALLS):
        src = graph.blocks.get(edge["_from"].partition("/")[2])
        if src and graph.code_files.get(src["recordId"]) == from_path:
            doc = graph.blocks[edge["_to"].partition("/")[2]]
            out.add((src["qualifiedName"], f"{graph.code_files[doc['recordId']]}::{doc['qualifiedName']}",
                     edge.get("resolution"), edge.get("confidence")))
    return out


def _wrapper(callee: str, caller: str = "function:run") -> tuple[str, str, str, str]:
    return caller, callee, RESOLUTION_WRAPPER, "INFERRED"


async def _wrapped(graph, c_repo, header: bytes, call: str) -> tuple[set, object]:
    """log.h = *header*, log.c, and main.c's run() making *call*: run's CALLS and the build result."""
    result = await c_repo({"src/log.h": header, "src/log.c": LOG_C,
                           "src/main.c": f'#include "log.h"\nint run(void) {{ {call}; return 0; }}\n'.encode()})
    return _calls(graph, "src/main.c"), result


@pytest.mark.asyncio
async def test_a_single_level_wrapper_reaches_the_function_it_expands_to(graph, c_repo) -> None:
    calls, result = await _wrapped(graph, c_repo, b"#define LOG(f) _serverLog(0, f)\n", 'LOG("x")')
    assert calls == {_wrapper(SERVER_LOG)} and result.wrappers_unresolved == 0


@pytest.mark.asyncio
async def test_a_written_call_to_the_wrapped_function_keeps_its_own_edge(graph, c_repo) -> None:
    calls, _ = await _wrapped(graph, c_repo, b"#define LOG(f) _serverLog(0, f)\n", 'LOG("x"); _serverLog(1, "y")')
    assert {(callee, res) for _, callee, res, _ in calls} == {(SERVER_LOG, "module-import")}


@pytest.mark.asyncio
async def test_a_chain_of_wrappers_is_followed(graph, c_repo) -> None:
    header = b"#define LOG(f) _serverLog(0, f)\n#define WARN(f) LOG(f)\n#define ERR(f) WARN(f)\n"
    calls, _ = await _wrapped(graph, c_repo, header, 'ERR("x")')
    assert calls == {_wrapper(SERVER_LOG)}


@pytest.mark.asyncio
async def test_a_variadic_wrapper_leaves_out_builtins_keywords_members_and_its_parameters(graph, c_repo) -> None:
    header = (b"#define likely(x) __builtin_expect(!!(x), 1)\n"
              b"#define serverLog(level, fn, ...) do { if (likely(level)) _serverLog(level, __VA_ARGS__); \\\n"
              b"    fprintf(stderr, \"x\"); fn(level); (level)->cb(1); } while (0)\n")
    calls, _ = await _wrapped(graph, c_repo, header, 'serverLog(1, other, "x %d", 2)')
    assert calls == {_wrapper(SERVER_LOG)}


@pytest.mark.asyncio
async def test_a_macro_reaching_two_functions_makes_no_edge(graph, c_repo) -> None:
    calls, result = await _wrapped(graph, c_repo, b"#define BOTH(x) (_serverLog(x, 0), other(x))\n", "BOTH(1)")
    assert calls == set() and result.wrappers_unresolved == 1


@pytest.mark.asyncio
async def test_a_cycle_expands_each_macro_once(graph, c_repo) -> None:
    calls, result = await _wrapped(graph, c_repo, b"#define PING(x) PONG(x)\n#define PONG(x) PING(x)\n", "PING(1)")
    assert calls == set() and result.wrappers_unresolved == 1


@pytest.mark.asyncio
async def test_a_macro_naming_itself_calls_the_function_of_that_name(graph, c_repo) -> None:
    # The preprocessor leaves a name it is expanding alone: `other((x) + 1)` calls the function.
    calls, _ = await _wrapped(graph, c_repo, b"#define other(x) other((x) + 1)\n", "other(1)")
    assert calls == {_wrapper("src/log.c::function:other")}


@pytest.mark.parametrize(("depth", "reached"), [(8, True), (9, False)])
@pytest.mark.asyncio
async def test_nesting_past_the_depth_bound_makes_no_edge(graph, c_repo, depth: int, reached: bool) -> None:
    lines = [b"#define M1(x) _serverLog(x, 0)\n"] + [f"#define M{i}(x) M{i - 1}(x)\n".encode()
                                                    for i in range(2, depth + 1)]
    calls, result = await _wrapped(graph, c_repo, b"".join(lines), f"M{depth}(1)")
    assert calls == ({_wrapper(SERVER_LOG)} if reached else set())
    assert result.wrappers_unresolved == (0 if reached else 1)


@pytest.mark.asyncio
async def test_a_function_the_calling_file_defines_shadows_a_macro_of_its_name(graph, c_repo) -> None:
    await c_repo({"src/log.h": b"#define LOG(f) _serverLog(0, f)\n", "src/log.c": LOG_C,
                  "src/main.c": b'#include "log.h"\n#undef LOG\nint LOG(const char *f) { return 0; }\n'
                                b'int run(void) { return LOG("x"); }\n'})
    assert _calls(graph, "src/main.c") == {("function:run", "src/main.c::function:LOG", "same-file", "EXTRACTED")}


@pytest.mark.asyncio
async def test_a_macro_from_a_header_included_through_another_is_seen(graph, c_repo) -> None:
    await c_repo({"src/log.h": b"void _serverLog(int l, const char *f, ...);\n", "src/log.c": LOG_C,
                  "src/macros.h": b"#define LOG(f) _serverLog(0, f)\n",
                  "src/all.h": b'#include "macros.h"\n#include "log.h"\n',
                  "src/main.c": b'#include "all.h"\nint run(void) { LOG("x"); return 0; }\n',
                  "src/elsewhere.c": b'int run(void) { LOG("x"); return 0; }\n'})
    assert _calls(graph, "src/main.c") == {_wrapper(SERVER_LOG)}
    assert _calls(graph, "src/elsewhere.c") == set()   # it includes no LOG macro, and no LOG function exists


@pytest.mark.asyncio
async def test_a_macro_in_the_calling_file_counts_only_below_its_definition(graph, c_repo) -> None:
    await c_repo({"src/log.c": LOG_C, "src/main.c": b"int early(void) { return LOG(1); }\n"
                                                    b"#define LOG(x) other(x)\nint late(void) { return LOG(2); }\n"})
    assert _calls(graph, "src/main.c") == {_wrapper("src/log.c::function:other", "function:late")}


@pytest.mark.asyncio
async def test_other_languages_never_expand_macros(graph, index_file) -> None:
    await index_file("p", "pkg/a.py", b"def LOG(x):\n    return x\n\n\ndef run():\n    return LOG(1)\n", "python")
    await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID, record_group_id=GROUP_ID)
    assert {e.get("resolution") for e in graph.code_edges(CALLS)} == {"same-file"}
    assert all(doc.get("macroTable") is None for doc in graph.blocks.values())


def test_the_parser_keeps_function_like_macros_that_call_something() -> None:
    parsed = parse_code(b"#define K 3\n#define ID(x) (x)\n#ifdef A\n#define W(n) one(n)\n#define V(n) same(n)\n"
                        b"#define E(p) ((void)0)\n#else\n#define W(n) two(n)\n#define V(n) same(n)\n"
                        b"#define E(p) real(p)\n#endif\n#define M(p, f) do { p->m(1); s.n(2); f(p); ns::q(3); "
                        b"if (sizeof(p)) three(p); } while (0)\n", "c")
    # W and E differ between the branches, so which body compiles is unknown; V is the same in both.
    assert parsed.macro_table == {"V": [5, ["same"]], "M": [12, ["three"]]}
    assert parsed.macros_dropped == 0


def test_the_parser_caps_the_table_and_counts_what_it_drops() -> None:
    wide = b"#define WIDE(x) " + b" ".join(f"g{i}(x);".encode() for i in range(12)) + b"\n"
    # Each entry is about 100 bytes of JSON: about 40 fit in the 4 KB the table may take.
    many = b"".join(f"#define W{i:03}_{'w' * 40}(x) f{i:03}_{'f' * 40}(x)\n".encode() for i in range(100))
    parsed = parse_code(wide + many, "c")
    assert parsed.macro_table["WIDE"] == [1, []]   # over 8 names: still a macro, reaching no one function
    assert 4000 < len(json.dumps(parsed.macro_table)) <= 4096
    kept_with_callees = len(parsed.macro_table) - 1
    assert kept_with_callees + parsed.macros_dropped == 101 and parsed.macros_dropped > 50
    assert "W000_" + "w" * 40 in parsed.macro_table   # file order: the first ones are kept


def test_literals_and_comments_in_a_macro_body_call_nothing() -> None:
    parsed = parse_code(b'#define LOG(f) do { printf("debug(): %s", f); /* trace(f) */ putc(\'(\', out); '
                        b'real(f); } while (0) // later(f)\n', "c")
    assert parsed.macro_table == {"LOG": [1, ["printf", "putc", "real"]]}


@pytest.mark.parametrize(("source", "callees"), [
    # CRLF: a continuation is `\` CR LF.
    (b"#define C(x) do { \\\r\n  foo(x); \\\r\n  bar(x); } while (0)\r\nint z;\r\n", {"C": ["foo", "bar"]}),
    (b"#define L(x) do { if (!enabled()) break; \\\r\n  real_log(x); } while (0)\r\n",
     {"L": ["enabled", "real_log"]}),
    # A comment running past the directive's line is still a comment: `/* */` across lines, `//` past a splice.
    (b"#define G(x) foo(x) /* call bar(x) when\n done */\nint z;\n", {"G": ["foo"]}),
    (b"#define H(x) foo(x) // see \\\n bar(x)\nint z;\n", {"H": ["foo"]}),
])
def test_the_body_runs_to_the_end_of_its_logical_line(source: bytes, callees: dict) -> None:
    assert {name: entry[1] for name, entry in parse_code(source, "c").macro_table.items()} == callees


def test_a_macro_only_a_non_linux_platform_compiles_is_left_out() -> None:
    parsed = parse_code(b"#ifndef _WIN32\n#define A(x) a(x)\n#else\n#define connect(s) win32_connect(s)\n#endif\n"
                        b"#ifdef __APPLE__\n#define M(x) mac(x)\n#endif\n"
                        b"#if defined(__linux__)\n#define LX(x) lx(x)\n#elif !defined(_MSC_VER)\n#define D(x) d(x)\n"
                        b"#else\n#define E(x) e(x)\n#endif\n"
                        b"#ifndef GUARD_H\n#define GUARD_H\n#define G(x) g(x)\n#endif\n", "c")
    assert {name: entry[1] for name, entry in parsed.macro_table.items()} == {
        "A": ["a"], "LX": ["lx"], "D": ["d"], "G": ["g"]}


@pytest.mark.asyncio
async def test_a_macro_calling_more_names_than_kept_makes_no_edge(graph, c_repo) -> None:
    # Cut to 8 names, the list would reach _serverLog alone; the body also calls `other`.
    body = " ".join(f"g{i}(x);" for i in range(8)) + " _serverLog(x, 0); other(x);"
    calls, result = await _wrapped(graph, c_repo, f"#define WIDE(x) do {{ {body} }} while (0)\n".encode(), "WIDE(1)")
    assert calls == set() and result.wrappers_unresolved == 1


@pytest.mark.parametrize("stored", [None, "not json", "[1, 2]", {"LOG": "bad"}, {"LOG": [1]}, {"LOG": [1, "x"]}])
@pytest.mark.asyncio
async def test_a_missing_or_malformed_macro_table_reads_as_no_macros(graph, index_file, stored) -> None:
    files = {"src/log.h": b"#define LOG(f) _serverLog(0, f)\n", "src/log.c": LOG_C,
             "src/main.c": b'#include "log.h"\nint run(void) { LOG("x"); return 0; }\n'}
    for i, (path, source) in enumerate(files.items()):
        await index_file(f"r{i}", path, source, "c")
    for doc in graph.blocks.values():
        if doc.get("macroTable"):
            doc["macroTable"] = stored
    result = await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID,
                                          record_group_id=GROUP_ID)
    assert _calls(graph, "src/main.c") == set() and result.wrappers_unresolved == 0
    assert BlockRow.from_doc({"macroTable": stored}, {}).macro_table == (stored if isinstance(stored, dict) else {})


def test_both_backends_store_the_table_as_it_was_written() -> None:
    """No database runs in unit tests, so the conversions each backend applies on write and read are checked."""
    table = {"LOG": [1, ["_serverLog"]]}
    # Neo4j has no map property: written as JSON, decoded on read since the block schema declares an object.
    assert "macroTable" in JSON_ENCODED_FIELDS[CollectionNames.BLOCKS.value]
    written = Neo4jProvider._arango_to_neo4j_node(None, {"_key": "b1", "macroTable": table},  # type: ignore[arg-type]
                                                 CollectionNames.BLOCKS.value)
    assert isinstance(written["macroTable"], str)
    assert Neo4jProvider._decode_json_fields(dict(written), CollectionNames.BLOCKS.value)["macroTable"] == table
    # Arango validates against the block schema: an object or null, and the schema stays open, so a reader
    # from before this field accepts documents carrying it.
    assert block_schema["rule"]["properties"]["macroTable"] == {"type": ["object", "null"]}
    assert "additionalProperties" not in block_schema["rule"]
