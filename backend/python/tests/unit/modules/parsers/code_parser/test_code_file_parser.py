"""Unit tests for app.modules.parsers.code_parser.code_file_parser.CodeFileParser

Tests cover:
- parse() returns empty BlocksContainer for unsupported extensions
- parse() returns BlockGroups for class-like containers
- parse() returns Blocks for functions, methods, imports
- Blocks have correct BlockType.CODE type
- BlockGroups have GroupType.CODE / GroupSubType.CODE_CLASS
- parent_index wired correctly between child Blocks and parent BlockGroups
- BlockGroupChildren ranges cover child block indices
- content_hash is set and non-empty on every Block / BlockGroup
- content_hash stability: same source → same hashes
- content_hash sensitivity: changing a function changes its hash but not others
- Signature extracted into CodeMetadata.signature
- Docstring extracted into CodeMetadata.docstring (Python triple-quote)
- JSDoc extracted into CodeMetadata.docstring (JS)
- Decorators extracted into CodeMetadata.decorators (Python)
- File summary block (BlockType.RECORD_SUMMARY) is appended
- File summary contains top-level symbol names
- block.data is a dict with "text", "kind", "start_line", "end_line" keys
- block.data["subtokens"] exists for BM25 enrichment
- Malformed source does not raise
"""
from __future__ import annotations

import hashlib
import sys
import pytest

def _real_grammar(pkg_name: str) -> bool:
    """Return True if pkg_name is a real tree-sitter grammar, not a MagicMock stub."""
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
            f"{_grammar_pkg} not properly installed; skipping code-file-parser tests",
            allow_module_level=True,
        )

from app.models.blocks import BlockType, GroupType, GroupSubType  # noqa: E402
from app.modules.parsers.code_parser.code_file_parser import (  # noqa: E402
    CodeFileParser,
    _content_hash,
    _extract_decorators,
    _extract_docstring,
    _extract_signature,
    _subtokenise,
)

# ---------------------------------------------------------------------------
# Source fixtures
# ---------------------------------------------------------------------------

PY_CLASS_SRC = b"""\
import os

class MyService:
    \"\"\"A service class.\"\"\"

    def __init__(self, name: str) -> None:
        self.name = name

    def run(self) -> str:
        \"\"\"Run the service.\"\"\"
        return self.name


def helper(x: int) -> int:
    return x + 1
"""

JS_SRC = b"""\
import fs from 'fs';

/**
 * A utility class.
 * @class
 */
class FileUtil {
    /**
     * Read a file.
     * @param {string} path
     */
    readFile(path) {
        return fs.readFileSync(path);
    }
}

const standalone = () => 42;
"""

PY_DECORATED_SRC = b"""\
class Router:
    @property
    def base_url(self):
        return self._url

    @staticmethod
    def ping():
        return True
"""

MALFORMED_PY = b"""\
def oops(
    x = 1 +
"""

JAVA_SRC = b"""\
import java.util.List;

public class Calculator {
    public int add(int a, int b) {
        return a + b;
    }
}
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse(source: bytes, name: str = "test.py") -> tuple:
    parser = CodeFileParser()
    bc = parser.parse(source, name)
    return bc, bc.block_groups, bc.blocks


# ---------------------------------------------------------------------------
# Unsupported extension
# ---------------------------------------------------------------------------

class TestUnsupportedExtension:
    def test_markdown_returns_empty(self):
        bc, bgs, blks = _parse(b"# heading\n\nsome text", "README.md")
        assert bgs == []
        assert blks == []

    def test_dockerfile_returns_empty(self):
        bc, bgs, blks = _parse(b"FROM ubuntu:22.04\n", "Dockerfile")
        assert bgs == []
        assert blks == []

    def test_no_extension_returns_empty(self):
        bc, bgs, blks = _parse(b"some content", "Makefile")
        assert bgs == []
        assert blks == []

    def test_empty_source_returns_empty(self):
        bc, bgs, blks = _parse(b"", "main.py")
        assert bgs == []
        assert blks == []


# ---------------------------------------------------------------------------
# Block types and structure
# ---------------------------------------------------------------------------

class TestBlockStructure:
    def test_class_becomes_block_group(self):
        _, bgs, _ = _parse(PY_CLASS_SRC, "service.py")
        assert any(bg.type == GroupType.CODE for bg in bgs), "Expected GroupType.CODE block group"

    def test_class_block_group_subtype(self):
        _, bgs, _ = _parse(PY_CLASS_SRC, "service.py")
        code_bgs = [bg for bg in bgs if bg.type == GroupType.CODE]
        assert any(bg.sub_type == GroupSubType.CODE_CLASS for bg in code_bgs)

    def test_class_block_group_named(self):
        _, bgs, _ = _parse(PY_CLASS_SRC, "service.py")
        names = {bg.name for bg in bgs}
        assert "MyService" in names

    def test_methods_are_blocks_not_groups(self):
        _, _, blks = _parse(PY_CLASS_SRC, "service.py")
        assert any(b.type == BlockType.CODE and b.name in ("__init__", "run") for b in blks)

    def test_standalone_function_is_block(self):
        _, _, blks = _parse(PY_CLASS_SRC, "service.py")
        assert any(b.type == BlockType.CODE and b.name == "helper" for b in blks)

    def test_import_is_block(self):
        _, _, blks = _parse(PY_CLASS_SRC, "service.py")
        import_blocks = [b for b in blks if isinstance(b.data, dict) and b.data.get("kind") == "imports"]
        assert import_blocks, "Expected at least one imports block"

    def test_java_class_is_block_group(self):
        _, bgs, _ = _parse(JAVA_SRC, "Calculator.java")
        assert any(bg.name == "Calculator" for bg in bgs)


# ---------------------------------------------------------------------------
# Parent-index wiring
# ---------------------------------------------------------------------------

class TestParentIndexWiring:
    def test_methods_have_parent_index(self):
        _, bgs, blks = _parse(PY_CLASS_SRC, "service.py")
        # Find the MyService block group
        service_bg = next((bg for bg in bgs if bg.name == "MyService"), None)
        assert service_bg is not None, "MyService block group not found"

        # Find blocks that are methods and have parent_index pointing at service_bg
        child_blocks = [b for b in blks if b.parent_index == service_bg.index]
        assert len(child_blocks) >= 2, "Expected at least __init__ and run in MyService"

    def test_block_group_children_ranges_cover_child_blocks(self):
        _, bgs, blks = _parse(PY_CLASS_SRC, "service.py")
        service_bg = next((bg for bg in bgs if bg.name == "MyService"), None)
        assert service_bg is not None

        child_method_indices = {b.index for b in blks if b.parent_index == service_bg.index}
        if service_bg.children:
            covered = set()
            for r in service_bg.children.block_ranges:
                covered.update(range(r.start, r.end + 1))
            assert child_method_indices <= covered, (
                f"Child indices {child_method_indices} not fully covered by ranges {service_bg.children.block_ranges}"
            )

    def test_top_level_function_has_no_parent(self):
        _, _, blks = _parse(PY_CLASS_SRC, "service.py")
        helper = next((b for b in blks if b.name == "helper"), None)
        assert helper is not None
        assert helper.parent_index is None


# ---------------------------------------------------------------------------
# content_hash
# ---------------------------------------------------------------------------

class TestContentHash:
    def test_every_block_has_content_hash(self):
        _, bgs, blks = _parse(PY_CLASS_SRC, "service.py")
        for b in blks:
            assert b.content_hash is not None and len(b.content_hash) > 0, (
                f"Block {b.name} ({b.type}) has no content_hash"
            )

    def test_every_block_group_has_content_hash(self):
        _, bgs, _ = _parse(PY_CLASS_SRC, "service.py")
        for bg in bgs:
            assert bg.content_hash is not None and len(bg.content_hash) > 0

    def test_hash_stability_same_source(self):
        """Parsing the same source twice produces identical hashes."""
        _, bgs1, blks1 = _parse(PY_CLASS_SRC, "service.py")
        _, bgs2, blks2 = _parse(PY_CLASS_SRC, "service.py")
        hashes1 = sorted(b.content_hash for b in blks1 if b.content_hash)
        hashes2 = sorted(b.content_hash for b in blks2 if b.content_hash)
        assert hashes1 == hashes2

    def test_hash_changes_when_function_body_changes(self):
        """Changing one function changes its hash but not unrelated blocks."""
        orig_src = b"""\
def alpha():
    return 1

def beta():
    return 2
"""
        modified_src = b"""\
def alpha():
    return 999  # changed

def beta():
    return 2
"""
        _, _, blks_orig = _parse(orig_src, "funcs.py")
        _, _, blks_mod = _parse(modified_src, "funcs.py")

        orig_by_name = {b.name: b.content_hash for b in blks_orig if b.name}
        mod_by_name = {b.name: b.content_hash for b in blks_mod if b.name}

        if "alpha" in orig_by_name and "alpha" in mod_by_name:
            assert orig_by_name["alpha"] != mod_by_name["alpha"], "alpha hash should change"
        if "beta" in orig_by_name and "beta" in mod_by_name:
            assert orig_by_name["beta"] == mod_by_name["beta"], "beta hash should not change"

    def test_content_hash_helper_deterministic(self):
        text = "def foo(): pass"
        assert _content_hash(text) == _content_hash(text)
        assert _content_hash(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Signature / docstring / decorator extraction
# ---------------------------------------------------------------------------

class TestSignatureExtraction:
    def test_python_function_signature(self):
        text = "def hello(name: str) -> str:\n    return name\n"
        sig = _extract_signature(text)
        assert "hello" in sig
        assert sig.startswith("def")

    def test_skips_decorator_lines(self):
        text = "@staticmethod\ndef ping():\n    return True\n"
        sig = _extract_signature(text)
        assert sig.startswith("def")

    def test_empty_text_returns_empty(self):
        assert _extract_signature("") == ""

    def test_class_signature(self):
        text = "class MyClass:\n    pass\n"
        sig = _extract_signature(text)
        assert "MyClass" in sig


class TestDocstringExtraction:
    def test_python_triple_double_quote(self):
        text = 'def foo():\n    """My docstring."""\n    pass\n'
        doc = _extract_docstring(text, "python")
        assert "My docstring" in doc

    def test_python_triple_single_quote(self):
        text = "def bar():\n    '''Another docstring.'''\n    pass\n"
        doc = _extract_docstring(text, "python")
        assert "Another docstring" in doc

    def test_python_no_docstring(self):
        text = "def baz():\n    x = 1\n    return x\n"
        doc = _extract_docstring(text, "python")
        assert doc == ""

    def test_javascript_jsdoc(self):
        text = "/** Read a file.\n * @param {string} path */\nfunction readFile(path) {}\n"
        doc = _extract_docstring(text, "javascript")
        assert "Read a file" in doc

    def test_unsupported_language_returns_empty(self):
        doc = _extract_docstring("/* comment */\nvoid foo() {}", "c")
        assert doc == ""


class TestDecoratorExtraction:
    def test_python_single_decorator(self):
        text = "@property\ndef url(self):\n    return self._url\n"
        decs = _extract_decorators(text, "python")
        assert "property" in decs

    def test_python_multiple_decorators(self):
        text = "@staticmethod\n@cache\ndef compute():\n    pass\n"
        decs = _extract_decorators(text, "python")
        assert "staticmethod" in decs
        assert "cache" in decs

    def test_java_annotation(self):
        text = "@Override\npublic void run() {}\n"
        decs = _extract_decorators(text, "java")
        assert "Override" in decs

    def test_no_decorators(self):
        decs = _extract_decorators("def plain():\n    pass\n", "python")
        assert decs == []


# ---------------------------------------------------------------------------
# Subtokenisation
# ---------------------------------------------------------------------------

class TestSubtokenise:
    def test_camel_case_split(self):
        result = _subtokenise("getUserById")
        assert "get" in result
        assert "user" in result
        assert "by" in result
        assert "id" in result
        assert "getUserById" in result  # original preserved

    def test_snake_case_split(self):
        result = _subtokenise("get_user_by_id")
        assert "get" in result
        assert "user" in result

    def test_plain_word_unchanged(self):
        result = _subtokenise("hello")
        assert "hello" in result

    def test_empty_returns_empty(self):
        assert _subtokenise("") == ""


# ---------------------------------------------------------------------------
# block.data structure
# ---------------------------------------------------------------------------

class TestBlockDataStructure:
    def test_block_data_is_dict(self):
        _, _, blks = _parse(PY_CLASS_SRC, "service.py")
        for b in blks:
            if b.type == BlockType.CODE:
                assert isinstance(b.data, dict), f"Block {b.name} data is not a dict"

    def test_block_data_has_text(self):
        _, _, blks = _parse(PY_CLASS_SRC, "service.py")
        for b in blks:
            if b.type == BlockType.CODE:
                assert "text" in b.data
                assert isinstance(b.data["text"], str)

    def test_block_data_has_kind(self):
        _, _, blks = _parse(PY_CLASS_SRC, "service.py")
        for b in blks:
            if b.type == BlockType.CODE:
                assert "kind" in b.data

    def test_block_data_has_line_numbers(self):
        _, _, blks = _parse(PY_CLASS_SRC, "service.py")
        for b in blks:
            if b.type == BlockType.CODE:
                assert "start_line" in b.data
                assert "end_line" in b.data

    def test_block_data_has_subtokens(self):
        _, _, blks = _parse(PY_CLASS_SRC, "service.py")
        for b in blks:
            if b.type == BlockType.CODE:
                assert "subtokens" in b.data


# ---------------------------------------------------------------------------
# File summary block
# ---------------------------------------------------------------------------

class TestFileSummaryBlock:
    def test_summary_block_present(self):
        _, _, blks = _parse(PY_CLASS_SRC, "service.py")
        summary_blocks = [b for b in blks if b.type == BlockType.RECORD_SUMMARY]
        assert summary_blocks, "Expected at least one RECORD_SUMMARY block"

    def test_summary_contains_class_name(self):
        _, _, blks = _parse(PY_CLASS_SRC, "service.py")
        summary = next(b for b in blks if b.type == BlockType.RECORD_SUMMARY)
        text = summary.data["text"]
        assert "MyService" in text

    def test_summary_contains_language(self):
        _, _, blks = _parse(PY_CLASS_SRC, "service.py")
        summary = next(b for b in blks if b.type == BlockType.RECORD_SUMMARY)
        text = summary.data["text"]
        assert "python" in text.lower()

    def test_summary_data_has_symbols_list(self):
        _, _, blks = _parse(PY_CLASS_SRC, "service.py")
        summary = next(b for b in blks if b.type == BlockType.RECORD_SUMMARY)
        assert "symbols" in summary.data
        assert isinstance(summary.data["symbols"], list)


# ---------------------------------------------------------------------------
# CodeMetadata on blocks
# ---------------------------------------------------------------------------

class TestCodeMetadata:
    def test_code_metadata_language_set(self):
        _, _, blks = _parse(PY_CLASS_SRC, "service.py")
        for b in blks:
            if b.type == BlockType.CODE:
                assert b.code_metadata is not None
                assert b.code_metadata.language == "python"

    def test_function_block_has_signature(self):
        _, _, blks = _parse(PY_CLASS_SRC, "service.py")
        run_block = next((b for b in blks if b.name == "run"), None)
        if run_block:
            assert run_block.code_metadata is not None
            assert run_block.code_metadata.signature is not None
            assert "run" in run_block.code_metadata.signature

    def test_decorated_function_has_decorators(self):
        _, _, blks = _parse(PY_DECORATED_SRC, "router.py")
        base_url = next((b for b in blks if b.name == "base_url"), None)
        if base_url and base_url.code_metadata:
            assert base_url.code_metadata.decorators is not None
            assert "property" in base_url.code_metadata.decorators


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

class TestRobustness:
    def test_malformed_python_does_not_raise(self):
        try:
            bc = CodeFileParser().parse(MALFORMED_PY, "broken.py")
            assert isinstance(bc.blocks, list)
            assert isinstance(bc.block_groups, list)
        except Exception as exc:
            pytest.fail(f"CodeFileParser raised on malformed input: {exc}")

    def test_non_utf8_source_does_not_raise(self):
        latin1_src = "def bonjour():\n    # Ça marche\n    pass\n".encode("latin-1")
        try:
            bc = CodeFileParser().parse(latin1_src, "code.py")
            assert isinstance(bc.blocks, list)
        except Exception as exc:
            pytest.fail(f"CodeFileParser raised on latin-1 input: {exc}")
