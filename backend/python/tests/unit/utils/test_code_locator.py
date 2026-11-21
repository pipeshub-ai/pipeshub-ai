"""Unit tests for the code-block locator helpers in ``app.utils.chat_helpers``.

``block_qualified_name`` and ``format_code_locator`` are how a code block is
named when it is shown to a model: the render path calls
``format_code_locator(record_file_path, block_qualified_name(block))``, so the
locator is ``path#qualified_name``, or whichever side is present when the
other is empty. No test called either function before, so a change that
dropped the ``#`` or treated a non-dict ``code_metadata`` as a name would not
have failed CI. These tests pin that behavior.
"""

from __future__ import annotations

import pytest

from app.utils.chat_helpers import block_qualified_name, format_code_locator


class TestBlockQualifiedName:
    """``block_qualified_name`` reads ``block["code_metadata"]["qualified_name"]``."""

    @pytest.mark.parametrize(
        ("block", "expected"),
        [
            # No ``code_metadata`` key at all.
            ({}, ""),
            # ``code_metadata`` present but not a dict: string, None, list.
            ({"code_metadata": "pkg.fn"}, ""),
            ({"code_metadata": None}, ""),
            ({"code_metadata": ["pkg.fn"]}, ""),
            # A dict without ``qualified_name``, or with a falsy one.
            ({"code_metadata": {}}, ""),
            ({"code_metadata": {"qualified_name": ""}}, ""),
            ({"code_metadata": {"qualified_name": None}}, ""),
            # Other block keys are irrelevant without ``code_metadata``.
            ({"block_type": "code", "data": {"text": "print(1)"}}, ""),
            # The happy paths: a dotted qualified name and a bare one.
            ({"code_metadata": {"qualified_name": "pkg.mod.fn"}}, "pkg.mod.fn"),
            ({"code_metadata": {"qualified_name": "main"}}, "main"),
            # Sibling metadata keys are ignored; only ``qualified_name`` is read.
            (
                {
                    "code_metadata": {
                        "qualified_name": "pkg.fn",
                        "line_start": 3,
                        "line_end": 9,
                    }
                },
                "pkg.fn",
            ),
        ],
    )
    def test_reads_qualified_name_from_code_metadata(
        self, block: dict, expected: str
    ) -> None:
        assert block_qualified_name(block) == expected

    def test_result_is_always_a_string(self) -> None:
        blocks = ({}, {"code_metadata": "pkg.fn"}, {"code_metadata": {}})
        for block in blocks:
            assert isinstance(block_qualified_name(block), str)


class TestFormatCodeLocator:
    """``format_code_locator`` joins path and name with ``#``."""

    @pytest.mark.parametrize(
        ("file_path", "qualified_name", "expected"),
        [
            # Both sides present: joined with exactly one ``#``.
            ("src/app.py", "pkg.mod.fn", "src/app.py#pkg.mod.fn"),
            ("src/app.py", "main", "src/app.py#main"),
            # A path that already carries a line number is joined as-is;
            # the ``#`` still separates path from name.
            ("src/app.py:42", "pkg.mod.fn", "src/app.py:42#pkg.mod.fn"),
            # A Windows-style path and a dotted class-member name.
            ("C:\\repo\\pkg\\mod.py", "Cls.method", "C:\\repo\\pkg\\mod.py#Cls.method"),
            # Whichever side is present when the other is empty.
            ("src/app.py", "", "src/app.py"),
            ("", "pkg.mod.fn", "pkg.mod.fn"),
            ("", "", ""),
        ],
    )
    def test_locator_joining(
        self, file_path: str, qualified_name: str, expected: str
    ) -> None:
        result = format_code_locator(file_path, qualified_name)
        assert result == expected
        assert isinstance(result, str)

    def test_both_present_uses_exactly_one_hash(self) -> None:
        result = format_code_locator("src/app.py", "pkg.mod.fn")
        assert result.count("#") == 1


class TestCodeBlockRenderComposition:
    """The render path composes the two helpers for each code block."""

    def test_block_with_qualified_name_renders_path_hash_name(self) -> None:
        block = {
            "block_type": "code",
            "code_metadata": {"qualified_name": "pkg.mod.fn"},
        }
        locator = format_code_locator("src/app.py", block_qualified_name(block))
        assert locator == "src/app.py#pkg.mod.fn"

    def test_block_without_metadata_renders_path_only(self) -> None:
        block = {"block_type": "code", "data": {"text": "print(1)"}}
        locator = format_code_locator("src/app.py", block_qualified_name(block))
        assert locator == "src/app.py"

    def test_block_with_non_dict_metadata_and_no_path_renders_empty(self) -> None:
        block = {"code_metadata": "pkg.fn"}
        locator = format_code_locator("", block_qualified_name(block))
        assert locator == ""
