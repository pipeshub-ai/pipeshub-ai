"""C/C++ walker: definitions named the way a compiler-grade index names them.

Preprocessor branches (`#ifndef` include guards, `#if`/`#else`) and `extern "C"` hold definitions; the
walker treated each as one statements span and never looked inside, so an include-guarded header had no symbols.

A C++ out-of-line definition is named by its declarator however the return type wraps it (a reference
``const T& Foo::name()`` was ``function:T`` with its owner lost), and its owner is the class without template
arguments (``Box<T>::get`` is ``Box.get``).
"""
import pytest

from app.modules.parsers.code_parser.code_file_parser import qualified_name_for
from app.modules.parsers.code_parser.engine import parse_code


def names(source: str, language: str) -> set[str]:
    """Every named symbol as ``kind:Scope.name``."""
    return {qualified_name_for(s.kind, s.name, s.parent_chain)
            for s in parse_code(source.encode(), language).symbols if s.name}


HEADER = ("#ifndef SDS_H\n#define SDS_H\n#include <stddef.h>\n"
          "typedef struct sdshdr { int len; } sdshdr;\n"
          "size_t sdsprint(const char *s);\n"
          "static inline size_t sdslen(const char *s) { return 0; }\n"
          "#endif\n")


@pytest.mark.parametrize("language", ["c", "cpp"])   # PipesHub parses .h with the C++ grammar
def test_an_include_guarded_header_has_its_definitions(language: str) -> None:
    got = names(HEADER, language)
    assert "function:sdslen" in got and any(n.endswith(":sdshdr") for n in got), sorted(got)
    assert not any(n.endswith(":SDS_H") for n in got)          # the guard is no symbol


def test_extern_c_blocks_and_declarations_hold_definitions() -> None:
    got = names('extern "C" {\nint start(void) { return 0; }\nstruct cfg { int n; };\n}\n'
                'extern "C" int stop(void) { return 1; }\n', "cpp")
    assert {"function:start", "function:stop"} <= got and any(n.endswith(":cfg") for n in got), sorted(got)


def test_both_branches_of_an_if_else_are_definitions() -> None:
    syms = [s for s in parse_code(b"#if USE_TLS\nint conn(void) { return 1; }\n#elif USE_RDMA\n"
                                  b"int conn(void) { return 2; }\n#else\nint conn(void) { return 3; }\n#endif\n",
                                  "c").symbols if s.name == "conn"]
    assert len(syms) == 3 and [s.start_line for s in syms] == [2, 4, 6]


def test_nested_branches_and_branches_in_a_class_body() -> None:
    got = names("#ifdef A\n#ifdef B\nint deep(void) { return 0; }\n#endif\n#endif\n"
                "class Conn {\n public:\n#ifdef USE_TLS\n  void handshake() {}\n#endif\n  void close() {}\n};\n",
                "cpp")
    assert {"function:deep", "method:Conn.handshake", "method:Conn.close"} <= got, sorted(got)


def test_branches_below_a_nested_namespace_are_definitions() -> None:
    # A namespace or type inside a namespace is emitted through its parent's body, not tiled.
    got = names("namespace a {\nnamespace b {\n#if defined(WIN32)\nvoid C::m(int x) {\n  win();\n}\n#else\n"
                "void C::m(int x) {\n  posix();\n}\n#endif\n"
                "struct S {\n#ifdef FAST\n  void go() {}\n#endif\n};\n}\n}\n", "cpp")
    assert {"method:a.b.C.m", "method:a.b.S.go"} <= got, sorted(got)


def test_the_branch_text_is_still_tiled() -> None:
    parsed = parse_code(HEADER.encode(), "c")
    covered = sum(s.end_byte - s.start_byte for s in parsed.symbols if s.parent is None)
    assert covered == len(HEADER.encode())                      # no byte of the header is lost


@pytest.mark.parametrize("definition,expected", [
    ("const std::string& Foo::name() const { return n; }", "Foo.name"),       # reference return
    ("std::vector<int>&& Foo::take() { return v; }", "Foo.take"),              # rvalue reference return
    ("Foo& Foo::operator=(const Foo& o) { return *this; }", "Foo.operator="),  # a reference-returning operator
    ("int* Foo::raw() { return p; }", "Foo.raw"),                              # a pointer, as before
    ("std::string Foo::label() { return s; }", "Foo.label"),                   # a qualified return, as before
    ("template <typename T> T Box<T>::get() { return v; }", "Box.get"),        # template owner
    ("template <typename T> const T& Box<T>::ref() const { return v; }", "Box.ref"),
    ("template <typename K, typename V> V net::Map<K, V>::at(const K& k) { return d; }", "net.Map.at"),
    ("int Table<std::map<int, int>>::size() { return 0; }", "Table.size"),       # :: inside the arguments
])
def test_cpp_out_of_line_definitions_are_named_by_their_declarator(definition: str, expected: str) -> None:
    got = names(definition + "\n", "cpp")
    assert f"method:{expected}" in got, sorted(got)
    assert not any("<" in n for n in got), sorted(got)


def test_c_functions_returning_pointers_keep_their_names() -> None:
    got = names("const char **names(void) { return 0; }\nchar *dup(const char *s) { return 0; }\n"
                "int (*handler(int sig))(int) { return 0; }\n", "c")
    assert {"function:names", "function:dup", "function:handler"} <= got


def test_cpp_in_class_definitions_are_unchanged() -> None:
    got = names("class Foo {\n public:\n  const int& ref() const { return x; }\n  int x;\n};\n", "cpp")
    assert "method:Foo.ref" in got
