"""Normalizer: the comparison key two spellings are matched on."""

import pytest

from app.modules.entity_resolution.normalizer import (
    MAX_NAME_LENGTH,
    MIN_NAME_LENGTH,
    display_form,
    is_acceptable_name,
    normalize_name,
)


class TestNormalizeName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("  BUG BASH TESTING ", "bug bash testing"),
            ("Bug\tbash\n testing", "bug bash testing"),
            ("bug    bash  testing", "bug bash testing"),
            ("Ｑ３ roadmap", "q3 roadmap"),
            ("café", "café"),
            ("café", "café"),
            ('"Quality Assurance"', "quality assurance"),
            ("'Legal'", "legal"),
            ("Release checklist.", "release checklist"),
            ("Why testing?", "why testing"),
            ("C++", "c++"),
            ("e-commerce", "e-commerce"),
            ("Q&A", "q&a"),
            ("Straße", "strasse"),
        ],
    )
    def test_normalizes(self, raw, expected) -> None:
        assert normalize_name(raw) == expected

    def test_idempotent(self) -> None:
        once = normalize_name("  “Bug Bash Testing.” ")
        assert normalize_name(once) == once

    @pytest.mark.parametrize("raw", ["", "   ", "\t\n", None])
    def test_empty_inputs(self, raw) -> None:
        assert normalize_name(raw) == ""


class TestDisplayForm:
    def test_keeps_casing_and_strips_noise(self) -> None:
        assert display_form('  "Bug Bash Testing." ') == "Bug Bash Testing"

    def test_display_and_normalized_agree(self) -> None:
        raw = "  Release Checklist!  "
        assert normalize_name(display_form(raw)) == normalize_name(raw)


class TestAcceptableName:
    def test_bounds(self) -> None:
        assert MIN_NAME_LENGTH == 2
        assert MAX_NAME_LENGTH == 100
        assert not is_acceptable_name("")
        assert not is_acceptable_name("a")
        assert is_acceptable_name("ab")
        assert is_acceptable_name("x" * MAX_NAME_LENGTH)
        assert not is_acceptable_name("x" * (MAX_NAME_LENGTH + 1))
