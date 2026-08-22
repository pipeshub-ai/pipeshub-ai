"""Blocks must tile the file exactly.

Blocks are what get embedded and stored, so anything not in a block is content
the platform can never retrieve or show.
"""
import importlib
import pathlib

import pytest

from app.models.blocks import BlockType
from app.modules.parsers.code_parser import CodeFileParser, detect_language

REPO_ROOT = pathlib.Path(__file__).resolve().parents[7]


def reconstruct(container) -> str:
    """Concatenate the top-level blocks in source order.

    Top-level, not leaves: a container keeps its whole body, so its children are
    subsets of it and would double-count.
    """
    items = [
        (b.code_metadata.start_line, b.data["text"])
        for b in container.blocks
        if b.type is BlockType.CODE and b.parent_index is None
    ]
    items += [
        (g.code_metadata.start_line, g.data["text"])
        for g in container.block_groups
        if g.parent_index is None
    ]
    return "".join(text for _, text in sorted(items))


def parse(src: bytes, name: str, path: str | None = None):
    return CodeFileParser().parse_to_blocks(
        src, name, path or name, detect_language(name)
    )


@pytest.mark.parametrize(
    "name,src",
    [
        ("plain.py", b"import os\n\nCONST = 1\n\n\ndef go():\n    return CONST\n"),
        # No definitions at all -- this shape produced zero blocks before.
        ("script.py", b"import a\nx = a.run()\nprint(x)\n"),
        ("crlf.py", b"import os\r\n\r\nx = 1\r\n"),
        ("no_trailing_newline.py", b"import os\nx = 1"),
        ("only_comments.py", b"# just\n# comments\n"),
        ("empty_ish.py", b"\n\n\n"),
        ("docstring_only.py", b'"""Only a docstring."""\n'),
        ("nested.py", b"class A:\n    class B:\n        def c(self):\n            pass\n"),
        ("decorated.py", b'@app.route("/x")\ndef f():\n    return 1\n'),
        ("mod.ts", b'import {a} from "./m";\nexport const X = 1;\nexport class C { m() {} }\n'),
        ("comp.tsx", b'import React from "react";\nexport default function App() { return <div/>; }\n'),
        ("plain.js", b"const x = require('y');\nfunction go(){ return x; }\ngo();\n"),
        ("arrow.ts", b"export const handler = async () => { return 1; };\n"),
    ],
)
def test_blocks_tile_the_file_byte_exactly(name, src):
    container = parse(src, name)
    assert reconstruct(container).encode() == src


# One sample per grammar-backed language: imports, a container with members, a
# doc comment and a top-level definition, which between them exercise every
# branch of the tiler.
LANGUAGE_SAMPLES = {
    "s.c": b'#include <stdio.h>\n\n/** A point. */\ntypedef struct Point { int x; } Point;\n\nstatic int add(int a, int b) { return a + b; }\n',
    "s.cpp": b'#include <vector>\n\nnamespace ns {\nclass Widget : public Base {\npublic:\n    int size;\n    void draw() {}\n};\ntemplate<typename T> T identity(T v) { return v; }\n}\n',
    "s.h": b'#pragma once\nstruct Point { int x; };\nint add(int a, int b);\n',
    "s.cs": b'using System;\n\nnamespace App {\n    /// <summary>A repo.</summary>\n    [Serializable]\n    public class Repo : Base {\n        private int id;\n        public void Save() { Console.WriteLine(id); }\n    }\n}\n',
    "s.java": b'package com.example;\n\nimport java.util.List;\n\n/** Service. */\n@Service\npublic class FooService extends Base {\n    private int count;\n\n    /** Runs it. */\n    public void run(List<String> args) { count++; }\n}\n',
    "s.kt": b'package com.example\n\nimport kotlin.io.*\n\n/** A repo. */\nclass Repo(val id: Int) : Base() {\n    /** Saves it. */\n    fun save(): Int { return id }\n}\n\nobject Single { fun go() {} }\n',
    "s.kts": b'plugins { id("java") }\n\nfun configure(): Int = 1\n',
    "s.scala": b'package example\n\nimport scala.util.Try\n\n/** A repo. */\nclass Repo(id: Int) extends Base {\n  val limit = 10\n  def save(): Int = id\n}\n\ntrait Store { def get(): Int }\n',
    "s.groovy": b'package example\n\nimport java.util.List\n\nclass Repo extends Base {\n    int id\n\n    def save() { return id }\n}\n',
    "s.gradle": b'apply plugin: "java"\n\nclass Helper {\n    def run() { return 1 }\n}\n',
    "s.go": b'package main\n\nimport "fmt"\n\n// Repo stores records.\ntype Repo struct {\n\tID int\n}\n\n// Save persists it.\nfunc (r *Repo) Save() error {\n\tfmt.Println(r.ID)\n\treturn nil\n}\n\nfunc main() { fmt.Println("hi") }\n',
    "s.rs": b'use std::fmt;\n\n/// A repo handle.\n#[derive(Debug)]\npub struct Repo {\n    id: u32,\n}\n\nimpl Repo {\n    /// Save it.\n    pub fn save(&self) -> u32 { self.id }\n}\n\npub trait Store {\n    fn get(&self) -> u32;\n}\n',
    "s.rb": b'require "json"\n\n# A repo.\nmodule Store\n  class Repo < Base\n    LIMIT = 10\n\n    # Saves it.\n    def save\n      1\n    end\n\n    def self.build; end\n  end\nend\n',
    "s.php": b'<?php\nnamespace App;\n\nuse Foo\\Bar;\n\n/** A repo. */\nclass Repo extends Base {\n    private $id;\n\n    /** Saves it. */\n    public function save() { return $this->id; }\n}\n',
    "s.swift": b'import Foundation\n\n/// A repo.\nclass Repo: Base {\n    var id: Int = 0\n\n    init() {}\n\n    /// Saves it.\n    func save() -> Int { return id }\n}\n\nprotocol Store {\n    func get() -> Int\n}\n',
    "s.dart": b"import 'dart:io';\n\n/// A repo.\nclass Repo extends Base {\n  int id = 0;\n\n  /// Saves it.\n  void save() { print(id); }\n}\n\nint top(int a) => a;\n",
    "s.lua": b'local m = require("m")\n\n--- Adds numbers.\nlocal function helper(a, b)\n  return a + b\nend\n\nfunction M.run(x)\n  return helper(x, 1)\nend\n',
}


@pytest.mark.parametrize("name", sorted(LANGUAGE_SAMPLES))
def test_every_language_tiles_its_sample(name):
    src = LANGUAGE_SAMPLES[name]
    assert reconstruct(parse(src, name)).encode() == src


@pytest.mark.parametrize("name", sorted(LANGUAGE_SAMPLES))
def test_every_language_survives_a_missing_trailing_newline(name):
    src = LANGUAGE_SAMPLES[name].rstrip(b"\n")
    assert reconstruct(parse(src, name)).encode() == src


@pytest.mark.parametrize("name", sorted(LANGUAGE_SAMPLES))
def test_every_language_finds_at_least_one_named_definition(name):
    """Tiling alone is satisfied by one big statements block; this is what
    catches a language config whose node types are simply wrong."""
    container = parse(LANGUAGE_SAMPLES[name], name)
    named = [b.name for b in container.blocks
             if b.name and b.code_metadata and b.code_metadata.kind != "file_summary"]
    named += [g.name for g in container.block_groups if g.name]
    assert named, f"{name} produced no named definition"


@pytest.mark.parametrize("name", sorted(LANGUAGE_SAMPLES))
def test_qualified_names_are_unique_within_a_file(name):
    """Qualified names must be unique within a file so blocks don't collide."""
    container = parse(LANGUAGE_SAMPLES[name], name)
    names = [b.code_metadata.qualified_name for b in container.blocks if b.code_metadata]
    names += [g.code_metadata.qualified_name for g in container.block_groups if g.code_metadata]
    assert len(names) == len(set(names))


def test_every_configured_grammar_loads():
    """A grammar wheel compiled against a different tree-sitter ABI fails at
    Language() construction, not at install, so pin drift must fail here rather
    than on the first file of that language in production."""
    from tree_sitter import Language

    from app.modules.parsers.code_parser.lang_config import LANGUAGES

    for name, cfg in sorted(LANGUAGES.items()):
        module = importlib.import_module(cfg.ts_module)
        factory = getattr(module, cfg.ts_language_fn, None)
        assert factory is not None, f"{cfg.ts_module} has no {cfg.ts_language_fn}()"
        Language(factory())


def test_a_file_with_no_definitions_still_produces_blocks():
    src = b"import analysis\n\nresult = analysis.run()\n"
    container = parse(src, "script.py")
    kinds = [b.code_metadata.kind for b in container.blocks if b.code_metadata]
    assert "imports" in kinds
    assert "statements" in kinds
    assert reconstruct(container).encode() == src


def test_container_children_tile_the_container():
    src = b'''@dataclass
class Foo(Base):
    """Docs."""
    LIMIT = 10

    # explains bar
    def bar(self):
        return 1
'''
    container = parse(src, "m.py")
    group = next(g for g in container.block_groups if g.name == "Foo")
    children = sorted(
        (b for b in container.blocks if b.parent_index == group.index),
        key=lambda b: b.code_metadata.start_line,
    )
    assert "".join(b.data["text"] for b in children) == group.data["text"]


def test_class_header_holds_decorator_signature_and_docstring():
    src = b'@dataclass\nclass Foo(Base):\n    """Docs."""\n    LIMIT = 10\n'
    container = parse(src, "m.py")
    header = next(b for b in container.blocks if b.code_metadata.kind == "header")
    text = header.data["text"]
    assert "@dataclass" in text
    assert "class Foo(Base):" in text
    assert '"""Docs."""' in text
    # Attributes stay their own blocks so DEFINES still has something to target.
    assert any(b.code_metadata.kind == "field" and b.name == "LIMIT"
               for b in container.blocks)


def test_decorator_source_survives_including_its_arguments():
    src = b'@app.route("/x")\ndef f():\n    return 1\n'
    container = parse(src, "m.py")
    block = next(b for b in container.blocks if b.name == "f")
    assert '@app.route("/x")' in block.data["text"]
    assert any("/x" in d for d in (block.code_metadata.decorators or []))


class TestCommentDirection:
    """Comments document what follows them, whitespace trails what precedes it."""

    def test_comment_above_a_definition_is_absorbed_into_it(self):
        src = b"# explains go\ndef go():\n    return 1\n"
        container = parse(src, "m.py")
        block = next(b for b in container.blocks if b.name == "go")
        assert block.data["text"].startswith("# explains go")

    def test_detached_comment_becomes_its_own_block(self):
        src = b"# a floating note\n\n\n\ndef go():\n    return 1\n"
        container = parse(src, "m.py")
        kinds = [b.code_metadata.kind for b in container.blocks if b.code_metadata]
        assert "comment" in kinds

    def test_blank_lines_never_become_their_own_block(self):
        src = b"def a():\n    pass\n\n\n\ndef b():\n    pass\n"
        container = parse(src, "m.py")
        for block in container.blocks:
            if block.type is BlockType.CODE:
                assert block.data["text"].strip(), "whitespace-only block emitted"
        assert reconstruct(container).encode() == src


def test_qualified_names_survive_an_edit_above_them():
    """Named symbols keep the same qualified_name when lines shift."""
    base = b"import os\n\ndef go():\n    return 1\n"
    shifted = b"import os\nimport sys\n\ndef go():\n    return 1\n"

    def names(src):
        container = parse(src, "m.py", "src/m.py")
        return {
            b.code_metadata.kind: b.code_metadata.qualified_name
            for b in container.blocks
            if b.code_metadata and b.code_metadata.kind == "function"
        }

    before, after = names(base), names(shifted)
    assert before["function"] == after["function"]

    # The readable name does track the lines.
    container = parse(base, "m.py", "src/m.py")
    imports = next(b for b in container.blocks if b.code_metadata.kind == "imports")
    assert imports.code_metadata.qualified_name.startswith("imports:L1")


@pytest.mark.parametrize("suffix", [".py", ".ts", ".tsx", ".js"])
def test_tiles_real_repository_files(suffix):
    """The fixtures above are small and hand-made; real files are the actual bar."""
    root = REPO_ROOT / "backend" / "python" / "app"
    if suffix != ".py":
        root = REPO_ROOT / "frontend"
    if not root.exists():
        pytest.skip(f"no source tree for {suffix}")

    files = [
        p for p in root.rglob(f"*{suffix}")
        if not {"node_modules", "__pycache__", "dist", ".next"} & set(p.parts)
    ][:120]
    if not files:
        pytest.skip(f"no {suffix} files found")

    checked = 0
    for path in files:
        src = path.read_bytes()
        if not src.strip():
            continue
        container = parse(src, path.name, str(path))
        if container is None:
            continue  # oversized — known limitation
        assert container.blocks or container.block_groups, (
            f"non-empty {path} produced no blocks"
        )
        assert reconstruct(container).encode() == src, f"tiling broke on {path}"
        checked += 1
    assert checked > 0
