"""Normalizer: the comparison key two spellings are matched on."""

import pytest

from app.modules.entity_resolution.normalizer import (
    MAX_NAME_LENGTH,
    MIN_NAME_LENGTH,
    display_form,
    is_acceptable_name,
    normalize_name,
    spelling_key,
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


    def test_zero_width_characters_are_noise(self) -> None:
        assert normalize_name("Foo​") == normalize_name("Foo") == "foo"
        assert normalize_name("﻿Road‍map") == "roadmap"


class TestDisplayForm:
    def test_keeps_casing_and_strips_noise(self) -> None:
        assert display_form('  "Bug Bash Testing." ') == "Bug Bash Testing"

    def test_display_and_normalized_agree(self) -> None:
        raw = "  Release Checklist!  "
        assert normalize_name(display_form(raw)) == normalize_name(raw)

    @pytest.mark.parametrize(
        "raw",
        [
            'Project "Phoenix".',
            "'Foo'.",
            "\"'Nested'\"!",
            ' " spaced " . ',
            "“Curly”?",
            "«Guillemets».",
            '"Foo',
            'ab"',
            "Q&A.",
        ],
    )
    def test_a_name_keys_the_same_as_its_display_form(self, raw) -> None:
        """The resolver keys on the raw name and the graph transformer looks up
        the display form; if they differ the name misses its node."""
        assert normalize_name(display_form(raw)) == normalize_name(raw)
        assert display_form(display_form(raw)) == display_form(raw)

    def test_a_quoted_word_inside_a_name_keeps_both_quotes(self) -> None:
        assert display_form('Project "Phoenix".') == 'Project "Phoenix"'


class TestSpellingKey:
    def test_presentation_only_differences_share_a_key(self) -> None:
        assert spelling_key("release-checklist V2") == spelling_key("Release Checklist v2")

    def test_added_or_dropped_words_do_not(self) -> None:
        assert spelling_key("release-checklist v2 (draft)") != spelling_key("Release Checklist v2")
        assert spelling_key("The release checklist") != spelling_key("Release checklist")


class TestAcceptableName:
    def test_bounds(self) -> None:
        assert MIN_NAME_LENGTH == 2
        assert MAX_NAME_LENGTH == 100
        assert not is_acceptable_name("")
        assert not is_acceptable_name("a")
        assert is_acceptable_name("ab")
        assert is_acceptable_name("x" * MAX_NAME_LENGTH)
        assert not is_acceptable_name("x" * (MAX_NAME_LENGTH + 1))
