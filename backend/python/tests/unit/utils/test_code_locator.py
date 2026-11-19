"""Unit tests for chat_helpers.format_code_locator and block_qualified_name."""

from __future__ import annotations

import pytest

from app.utils import chat_helpers as ch

# ===================================================================
# block_qualified_name
# ===================================================================


@pytest.mark.parametrize(
    "block, expected",
    [
        ({"type": "code", "code_metadata": {"qualified_name": "Foo.bar.baz"}}, "Foo.bar.baz"),
        ({"type": "code", "code_metadata": {"qualified_name": None}}, ""),
        ({"type": "code", "code_metadata": {"qualified_name": ""}}, ""),
        ({"type": "code", "code_metadata": {"other_field": "value"}}, ""),
        ({"type": "code", "code_metadata": None}, ""),
        ({"type": "code", "code_metadata": "not a dict"}, ""),
        ({"type": "code", "code_metadata": [{"qualified_name": "Foo"}]}, ""),
        ({"type": "code", "data": "some code"}, ""),
    ],
)
def test_block_qualified_name(block: dict, expected: str) -> None:
    assert ch.block_qualified_name(block) == expected


# ===================================================================
# format_code_locator
# ===================================================================


@pytest.mark.parametrize(
    "file_path, qualified_name, expected",
    [
        ("app/x.py", "Foo.bar", "app/x.py#Foo.bar"),
        ("app/x.py", "", "app/x.py"),
        ("", "Foo.bar", "Foo.bar"),
        ("", "", ""),
        ("src/components/Button.tsx", "MyComponent.render", "src/components/Button.tsx#MyComponent.render"),
        ("app/utils/helpers.py", "utils.parse_config", "app/utils/helpers.py#utils.parse_config"),
        ("src/level1/level2/module.py", "level1.level2.module.Class.method", "src/level1/level2/module.py#level1.level2.module.Class.method"),
        ("file.py", "x", "file.py#x"),
        ("module.py", "__init__", "module.py#__init__"),
        ("/path with spaces/file.py", "MyClass", "/path with spaces/file.py#MyClass"),
        ("file.py", "module.Class<T>", "file.py#module.Class<T>"),
    ],
)
def test_format_code_locator(file_path: str, qualified_name: str, expected: str) -> None:
    assert ch.format_code_locator(file_path, qualified_name) == expected
