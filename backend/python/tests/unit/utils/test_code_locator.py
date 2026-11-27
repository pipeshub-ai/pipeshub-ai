import pytest
from app.utils.chat_helpers import block_qualified_name, format_code_locator


@pytest.mark.parametrize(
    "block, expected",
    [
        ({}, ""),
        ({"code_metadata": "pkg.fn"}, ""),
        ({"code_metadata": {}}, ""),
        ({"code_metadata": {"qualified_name": ""}}, ""),
        ({"code_metadata": {"qualified_name": "pkg.mod.fn"}}, "pkg.mod.fn"),
    ],
)
def test_block_qualified_name(block, expected):
    assert block_qualified_name(block) == expected


@pytest.mark.parametrize(
    "file_path, qualified_name, expected",
    [
        ("src/app.py", "pkg.mod.fn", "src/app.py#pkg.mod.fn"),
        ("src/app.py", "", "src/app.py"),
        ("", "pkg.mod.fn", "pkg.mod.fn"),
        ("", "", ""),
    ],
)
def test_format_code_locator(file_path, qualified_name, expected):
    assert format_code_locator(file_path, qualified_name) == expected
