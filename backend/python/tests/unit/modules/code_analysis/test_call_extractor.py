"""Unit tests for app.modules.code_analysis.call_extractor.

Tests cover:
- Python: function definitions, class + method definitions, call sites with correct callee/line/caller
- TypeScript: function, class, method, new expression call sites
- Decorated functions (Python): not double-counted
- Module-level calls: caller == ""
- Method calls: callee = attribute name
- Malformed source: no exception, returns empty
- Unsupported language: returns empty CodeSymbols
- Empty source: returns empty CodeSymbols
- Source exceeding MAX_FILE_SIZE_BYTES: returns empty
"""
from __future__ import annotations

import pytest

from app.modules.code_analysis.call_extractor import (
    CallSite,
    CodeSymbols,
    Definition,
    extract_symbols,
)


# ===========================================================================
# Python
# ===========================================================================


class TestExtractSymbolsPython:
    def test_top_level_function_definition(self):
        src = b"def greet(name):\n    pass\n"
        syms = extract_symbols(src, "python")
        names = [d.name for d in syms.definitions]
        assert "greet" in names

    def test_function_definition_kind(self):
        src = b"def top():\n    pass\n"
        syms = extract_symbols(src, "python")
        defn = next(d for d in syms.definitions if d.name == "top")
        assert defn.kind == "function"

    def test_class_definition(self):
        src = b"class MyClass:\n    pass\n"
        syms = extract_symbols(src, "python")
        names = [d.name for d in syms.definitions]
        assert "MyClass" in names

    def test_class_kind(self):
        src = b"class MyClass:\n    pass\n"
        syms = extract_symbols(src, "python")
        defn = next(d for d in syms.definitions if d.name == "MyClass")
        assert defn.kind == "class"

    def test_method_inside_class(self):
        src = b"class Foo:\n    def bar(self):\n        pass\n"
        syms = extract_symbols(src, "python")
        names = [d.name for d in syms.definitions]
        assert "bar" in names

    def test_method_kind(self):
        src = b"class Foo:\n    def bar(self):\n        pass\n"
        syms = extract_symbols(src, "python")
        defn = next(d for d in syms.definitions if d.name == "bar")
        assert defn.kind == "method"

    def test_module_level_call_site(self):
        src = b"print('hello')\n"
        syms = extract_symbols(src, "python")
        callees = [c.callee for c in syms.calls]
        assert "print" in callees

    def test_module_level_caller_is_empty(self):
        src = b"some_func()\n"
        syms = extract_symbols(src, "python")
        call = next(c for c in syms.calls if c.callee == "some_func")
        assert call.caller == ""

    def test_call_inside_function_has_correct_caller(self):
        src = b"def my_fn():\n    helper()\n"
        syms = extract_symbols(src, "python")
        call = next(c for c in syms.calls if c.callee == "helper")
        assert call.caller == "my_fn"

    def test_method_call_returns_attribute_name(self):
        src = b"def fn():\n    obj.process()\n"
        syms = extract_symbols(src, "python")
        callees = [c.callee for c in syms.calls]
        assert "process" in callees

    def test_call_line_number(self):
        src = b"def fn():\n    foo()\n"
        syms = extract_symbols(src, "python")
        call = next(c for c in syms.calls if c.callee == "foo")
        assert call.line == 2

    def test_definition_start_line(self):
        src = b"def fn():\n    pass\n"
        syms = extract_symbols(src, "python")
        defn = next(d for d in syms.definitions if d.name == "fn")
        assert defn.start_line == 1

    def test_decorated_function_counted_once(self):
        src = b"@decorator\ndef decorated():\n    pass\n"
        syms = extract_symbols(src, "python")
        names = [d.name for d in syms.definitions]
        assert names.count("decorated") == 1

    def test_multiple_calls_in_function(self):
        src = b"def fn():\n    a()\n    b()\n    c()\n"
        syms = extract_symbols(src, "python")
        callees = [c.callee for c in syms.calls]
        assert set(callees) >= {"a", "b", "c"}

    def test_nested_method_call_context(self):
        src = (
            b"class MyClass:\n"
            b"    def run(self):\n"
            b"        helper()\n"
        )
        syms = extract_symbols(src, "python")
        call = next(c for c in syms.calls if c.callee == "helper")
        assert call.caller == "run"

    def test_empty_source_returns_empty(self):
        syms = extract_symbols(b"", "python")
        assert syms.definitions == []
        assert syms.calls == []

    def test_malformed_source_no_exception(self):
        malformed = b"def (:\n    !!!\n@@@"
        try:
            syms = extract_symbols(malformed, "python")
        except Exception:
            pytest.fail("extract_symbols raised on malformed source")
        # May return partial results or empty — must not raise
        assert isinstance(syms, CodeSymbols)

    def test_unsupported_language_returns_empty(self):
        src = b"some code"
        syms = extract_symbols(src, "ruby")
        assert syms.definitions == []
        assert syms.calls == []


# ===========================================================================
# TypeScript / JavaScript
# ===========================================================================


class TestExtractSymbolsTypeScript:
    def test_function_declaration_definition(self):
        src = b"function fetchData(url: string): void {\n    callApi(url);\n}\n"
        syms = extract_symbols(src, "typescript")
        names = [d.name for d in syms.definitions]
        assert "fetchData" in names

    def test_function_definition_kind(self):
        src = b"function myFunc() {}\n"
        syms = extract_symbols(src, "typescript")
        defn = next(d for d in syms.definitions if d.name == "myFunc")
        assert defn.kind == "function"

    def test_class_declaration(self):
        src = b"class DataService {\n    load() {}\n}\n"
        syms = extract_symbols(src, "typescript")
        names = [d.name for d in syms.definitions]
        assert "DataService" in names

    def test_method_definition(self):
        src = b"class Svc {\n    async load() {\n        fetchData('/api');\n    }\n}\n"
        syms = extract_symbols(src, "typescript")
        names = [d.name for d in syms.definitions]
        assert "load" in names

    def test_call_expression_in_function(self):
        src = b"function fn() {\n    callApi('/v1');\n}\n"
        syms = extract_symbols(src, "typescript")
        callees = [c.callee for c in syms.calls]
        assert "callApi" in callees

    def test_new_expression_captured(self):
        src = b"function setup() {\n    const x = new MyClass();\n}\n"
        syms = extract_symbols(src, "typescript")
        callees = [c.callee for c in syms.calls]
        assert "MyClass" in callees

    def test_method_call_callee_name(self):
        src = b"function fn() {\n    obj.process();\n}\n"
        syms = extract_symbols(src, "typescript")
        callees = [c.callee for c in syms.calls]
        assert "process" in callees

    def test_caller_attribution_in_method(self):
        src = b"class Svc {\n    run() {\n        helper();\n    }\n}\n"
        syms = extract_symbols(src, "typescript")
        call = next(c for c in syms.calls if c.callee == "helper")
        assert call.caller == "run"

    def test_empty_source_returns_empty(self):
        syms = extract_symbols(b"", "typescript")
        assert syms.definitions == []
        assert syms.calls == []


class TestExtractSymbolsJavaScript:
    def test_function_declaration(self):
        src = b"function greet() {\n    console.log('hi');\n}\n"
        syms = extract_symbols(src, "javascript")
        names = [d.name for d in syms.definitions]
        assert "greet" in names

    def test_method_call_log(self):
        src = b"function greet() {\n    console.log('hi');\n}\n"
        syms = extract_symbols(src, "javascript")
        callees = [c.callee for c in syms.calls]
        assert "log" in callees

    def test_caller_of_module_level_call(self):
        src = b"fetchData();\n"
        syms = extract_symbols(src, "javascript")
        call = next((c for c in syms.calls if c.callee == "fetchData"), None)
        if call:
            assert call.caller == ""


# ===========================================================================
# Edge cases
# ===========================================================================


class TestExtractSymbolsEdgeCases:
    def test_source_over_max_size_returns_empty(self):
        from app.modules.parsers.code_parser.parser import MAX_FILE_SIZE_BYTES

        big = b"x = 1\n" * (MAX_FILE_SIZE_BYTES // 6 + 1)
        syms = extract_symbols(big, "python")
        assert syms.definitions == []
        assert syms.calls == []

    def test_no_calls_returns_empty_calls_list(self):
        src = b"def fn():\n    pass\n"
        syms = extract_symbols(src, "python")
        assert syms.calls == []

    def test_no_definitions_returns_empty_definitions_list(self):
        src = b"foo()\n"
        syms = extract_symbols(src, "python")
        assert syms.definitions == []

    def test_tsx_treated_as_typescript(self):
        src = b"function App() {\n    return null;\n}\n"
        syms = extract_symbols(src, "tsx")
        names = [d.name for d in syms.definitions]
        assert "App" in names
