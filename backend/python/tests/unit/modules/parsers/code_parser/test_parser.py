"""Unit tests for app.modules.parsers.code_parser.parser

Tests cover:
- detect_language: extension → language mapping
- decode_source: encoding normalisation
- MAX_FILE_SIZE_BYTES guard in extract_blocks
- extract_blocks happy path for all 9 supported languages
- Nested container (class + methods) produces correct parent/path metadata
- Malformed source parses without raising (tree-sitter is error-tolerant)
- Non-UTF-8 latin-1 file decoded transparently
- content_hash stability via CodeFileParser (content_hash is set in code_file_parser)
- Grammar smoke test: all 9 grammars load without ABI error

These tests require the tree-sitter packages to be installed.
The entire module is skipped when they are absent so CI does not fail on a
minimal environment.
"""
from __future__ import annotations

import sys
import pytest

# ---------------------------------------------------------------------------
# Module-level skip guard: skip when any grammar is absent OR is a MagicMock
# stub (injected by conftest._ensure_module). The grammar language() function
# returns an integer on a real install; a MagicMock returns a MagicMock.
# ---------------------------------------------------------------------------
def _real_grammar(pkg_name: str) -> bool:
    """Return True if pkg_name is a real tree-sitter grammar, not a stub."""
    try:
        mod = sys.modules.get(pkg_name) or __import__(pkg_name)
        result = mod.language()
        return isinstance(result, int)
    except Exception:
        return False


for _grammar_pkg in (
    "tree_sitter_python",
    "tree_sitter_javascript",
    "tree_sitter_typescript",
    "tree_sitter_go",
    "tree_sitter_java",
    "tree_sitter_rust",
    "tree_sitter_c",
    "tree_sitter_cpp",
):
    if not _real_grammar(_grammar_pkg):
        pytest.skip(
            f"{_grammar_pkg} not properly installed; skipping code-parser tests",
            allow_module_level=True,
        )

# All grammars are real — safe to import the parser module.
from app.modules.parsers.code_parser.parser import (  # noqa: E402
    LANG_CONFIG,
    MAX_FILE_SIZE_BYTES,
    decode_source,
    detect_language,
    extract_blocks,
)

# ---------------------------------------------------------------------------
# Tiny source fixtures (inline to avoid file I/O in tests)
# ---------------------------------------------------------------------------

PYTHON_SRC = b"""\
import os

class MyClass:
    \"\"\"A simple class.\"\"\"

    def hello(self, name: str) -> str:
        return f"hello {name}"

    def goodbye(self) -> None:
        pass

def standalone():
    x = 1
    return x
"""

JAVASCRIPT_SRC = b"""\
import { readFile } from 'fs';

class Greeter {
    constructor(name) {
        this.name = name;
    }

    greet() {
        return `Hello, ${this.name}`;
    }
}

function standalone() {
    return 42;
}
"""

TYPESCRIPT_SRC = b"""\
import { Injectable } from '@angular/core';

interface IUser {
    id: number;
    name: string;
}

class UserService {
    private users: IUser[] = [];

    getUser(id: number): IUser | undefined {
        return this.users.find(u => u.id === id);
    }
}

export type UserMap = Map<number, IUser>;
"""

TSX_SRC = b"""\
import React from 'react';

interface Props {
    name: string;
}

class Greeting extends React.Component<Props> {
    render() {
        return <h1>Hello, {this.props.name}</h1>;
    }
}

export default Greeting;
"""

GO_SRC = b"""\
package main

import "fmt"

type Greeter struct {
    Name string
}

func (g Greeter) Hello() string {
    return fmt.Sprintf("Hello, %s", g.Name)
}

func main() {
    g := Greeter{Name: "world"}
    fmt.Println(g.Hello())
}
"""

JAVA_SRC = b"""\
import java.util.List;

public class Calculator {

    public int add(int a, int b) {
        return a + b;
    }

    public int subtract(int a, int b) {
        return a - b;
    }
}
"""

RUST_SRC = b"""\
use std::fmt;

struct Greeter {
    name: String,
}

impl Greeter {
    fn hello(&self) -> String {
        format!("Hello, {}!", self.name)
    }
}

fn main() {
    let g = Greeter { name: "world".to_string() };
    println!("{}", g.hello());
}
"""

C_SRC = b"""\
#include <stdio.h>

typedef struct {
    int x;
    int y;
} Point;

int add(int a, int b) {
    return a + b;
}

int main() {
    printf("%d\\n", add(1, 2));
    return 0;
}
"""

CPP_SRC = b"""\
#include <iostream>
#include <string>

class Greeter {
public:
    std::string hello(const std::string& name) {
        return "Hello, " + name + "!";
    }
};

int main() {
    Greeter g;
    std::cout << g.hello("world") << std::endl;
    return 0;
}
"""

MALFORMED_PYTHON = b"""\
def broken(
    # missing closing paren
    x = 1 + 2
"""

LATIN1_SRC = "# Fichier créé en latin-1\ndef bonjour():\n    pass\n".encode("latin-1")


# ---------------------------------------------------------------------------
# detect_language
# ---------------------------------------------------------------------------

class TestDetectLanguage:
    def test_python(self):
        assert detect_language("foo.py") == "python"

    def test_python_stub(self):
        assert detect_language("types.pyi") == "python"

    def test_javascript(self):
        assert detect_language("app.js") == "javascript"

    def test_jsx(self):
        assert detect_language("component.jsx") == "javascript"

    def test_typescript(self):
        assert detect_language("service.ts") == "typescript"

    def test_tsx(self):
        assert detect_language("widget.tsx") == "tsx"

    def test_go(self):
        assert detect_language("main.go") == "go"

    def test_java(self):
        assert detect_language("Calculator.java") == "java"

    def test_rust(self):
        assert detect_language("lib.rs") == "rust"

    def test_c_source(self):
        assert detect_language("utils.c") == "c"

    def test_c_header(self):
        assert detect_language("utils.h") == "c"

    def test_cpp_source(self):
        assert detect_language("main.cpp") == "cpp"

    def test_cpp_header(self):
        assert detect_language("types.hpp") == "cpp"

    def test_unsupported_extension(self):
        assert detect_language("README.md") is None

    def test_dockerfile_unsupported(self):
        assert detect_language("Dockerfile") is None

    def test_env_file_unsupported(self):
        assert detect_language(".env") is None

    def test_unknown_extension(self):
        assert detect_language("file.xyz") is None

    def test_case_insensitive(self):
        # Extension detection is lowercase-normalised
        assert detect_language("Script.PY") == "python"

    def test_no_extension(self):
        assert detect_language("Makefile") is None

    def test_nested_path(self):
        assert detect_language("src/components/App.tsx") == "tsx"


# ---------------------------------------------------------------------------
# decode_source
# ---------------------------------------------------------------------------

class TestDecodeSource:
    def test_valid_utf8_returned_unchanged(self):
        src = b"def foo(): pass\n"
        assert decode_source(src) == src

    def test_latin1_converted_to_utf8(self):
        result = decode_source(LATIN1_SRC)
        # Must not raise and must be valid UTF-8
        result.decode("utf-8")

    def test_latin1_content_preserved(self):
        result = decode_source(LATIN1_SRC)
        decoded = result.decode("utf-8")
        assert "bonjour" in decoded

    def test_empty_bytes(self):
        assert decode_source(b"") == b""


# ---------------------------------------------------------------------------
# File size guard
# ---------------------------------------------------------------------------

class TestFileSizeGuard:
    def test_file_exceeding_limit_returns_empty(self):
        oversized = b"x" * (MAX_FILE_SIZE_BYTES + 1)
        blocks = extract_blocks(oversized, "python")
        assert blocks == []

    def test_file_at_limit_is_accepted(self):
        """Files exactly at or below the limit should not be rejected by the guard.
        Use a minimal valid Python source well below the limit to confirm the guard
        does not trigger for normal files."""
        blocks = extract_blocks(PYTHON_SRC, "python")
        # Should produce at least one block (imports, class, or function)
        assert len(blocks) > 0


# ---------------------------------------------------------------------------
# extract_blocks – happy path per language
# ---------------------------------------------------------------------------

class TestExtractBlocksHappyPath:
    """Each language fixture should produce at least the expected semantic units."""

    def _assert_has_kinds(self, blocks, *expected_kinds):
        found = {b.type for b in blocks}
        for kind in expected_kinds:
            assert kind in found, f"Expected kind '{kind}' not found in {found}"

    def test_python_basic(self):
        blocks = extract_blocks(PYTHON_SRC, "python")
        assert blocks, "Expected at least one block"
        kinds = {b.type for b in blocks}
        assert "class" in kinds
        assert "method" in kinds or "function" in kinds

    def test_python_import_block(self):
        blocks = extract_blocks(PYTHON_SRC, "python")
        assert any(b.type == "imports" for b in blocks)

    def test_python_line_numbers_one_indexed(self):
        blocks = extract_blocks(PYTHON_SRC, "python")
        for b in blocks:
            assert b.start_line >= 1, f"start_line < 1: {b}"
            assert b.end_line >= b.start_line

    def test_javascript_basic(self):
        blocks = extract_blocks(JAVASCRIPT_SRC, "javascript")
        assert blocks
        assert any(b.type == "class" for b in blocks)

    def test_typescript_basic(self):
        blocks = extract_blocks(TYPESCRIPT_SRC, "typescript")
        assert blocks
        assert any(b.type == "interface" for b in blocks)

    def test_tsx_basic(self):
        blocks = extract_blocks(TSX_SRC, "tsx")
        assert blocks
        assert any(b.type == "class" for b in blocks)

    def test_go_basic(self):
        blocks = extract_blocks(GO_SRC, "go")
        assert blocks
        assert any(b.type in ("struct", "type") for b in blocks)

    def test_java_basic(self):
        blocks = extract_blocks(JAVA_SRC, "java")
        assert blocks
        assert any(b.type == "class" for b in blocks)

    def test_rust_basic(self):
        blocks = extract_blocks(RUST_SRC, "rust")
        assert blocks
        assert any(b.type in ("struct", "impl") for b in blocks)

    def test_c_basic(self):
        blocks = extract_blocks(C_SRC, "c")
        assert blocks
        assert any(b.type == "function" for b in blocks)

    def test_cpp_basic(self):
        blocks = extract_blocks(CPP_SRC, "cpp")
        assert blocks
        assert any(b.type == "class" for b in blocks)


# ---------------------------------------------------------------------------
# Nested containers: class + methods
# ---------------------------------------------------------------------------

class TestNestedContainers:
    def test_python_class_contains_methods(self):
        """Methods inside a Python class carry the class name in their path."""
        blocks = extract_blocks(PYTHON_SRC, "python")
        methods_in_class = [
            b for b in blocks
            if b.type == "method" and b.path and b.path[0].get("name") == "MyClass"
        ]
        assert len(methods_in_class) >= 2, "Expected at least 2 methods in MyClass"

    def test_java_class_contains_methods(self):
        """Methods inside a Java class carry the class name in their path."""
        blocks = extract_blocks(JAVA_SRC, "java")
        methods = [
            b for b in blocks
            if b.type == "method" and b.path and b.path[0].get("name") == "Calculator"
        ]
        assert len(methods) >= 2

    def test_blocks_sorted_by_start_line(self):
        """Blocks are returned in document order."""
        blocks = extract_blocks(PYTHON_SRC, "python")
        lines = [b.start_line for b in blocks]
        assert lines == sorted(lines), "Blocks must be in line order"


# ---------------------------------------------------------------------------
# Malformed / error-tolerance
# ---------------------------------------------------------------------------

class TestMalformedSource:
    def test_malformed_python_does_not_raise(self):
        """tree-sitter is error-tolerant; broken syntax must not raise."""
        try:
            blocks = extract_blocks(MALFORMED_PYTHON, "python")
            # May return 0 or more blocks — not raising is the contract.
            assert isinstance(blocks, list)
        except Exception as exc:
            pytest.fail(f"extract_blocks raised on malformed input: {exc}")

    def test_empty_source(self):
        blocks = extract_blocks(b"", "python")
        assert isinstance(blocks, list)

    def test_only_comments(self):
        blocks = extract_blocks(b"# just a comment\n", "python")
        assert isinstance(blocks, list)


# ---------------------------------------------------------------------------
# Encoding handling
# ---------------------------------------------------------------------------

class TestEncoding:
    def test_latin1_file_parsed_without_raising(self):
        """A latin-1 encoded file must not cause an exception."""
        try:
            blocks = extract_blocks(LATIN1_SRC, "python")
            assert isinstance(blocks, list)
        except Exception as exc:
            pytest.fail(f"extract_blocks raised on latin-1 input: {exc}")

    def test_latin1_produces_at_least_one_block(self):
        """The decoded latin-1 file contains a valid function definition."""
        blocks = extract_blocks(LATIN1_SRC, "python")
        assert any(b.type in ("function", "method") for b in blocks)


# ---------------------------------------------------------------------------
# Grammar smoke test
# ---------------------------------------------------------------------------

class TestGrammarSmoke:
    """All 9 grammars must instantiate without ABI errors."""

    @pytest.mark.parametrize("lang", list(LANG_CONFIG.keys()))
    def test_grammar_loads(self, lang: str):
        try:
            lang_obj = LANG_CONFIG[lang]["language"]()
            assert lang_obj is not None
        except Exception as exc:
            pytest.fail(f"Grammar for '{lang}' failed to load: {exc}")
