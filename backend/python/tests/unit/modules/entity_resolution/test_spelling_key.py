"""Two names share a spelling key only when they differ in case,
spacing or punctuation. Combining marks and symbols change the word, so they
are part of the key."""
from __future__ import annotations

import pytest

from app.modules.entity_resolution.normalizer import spelling_key


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Bug-bash", "bug bash"),
        ("Release checklist.", "release checklist"),
        ("release_checklist", "Release Checklist"),
        ("ＡＢＣ", "abc"),  # full-width forms fold under NFKC
        ("Q&A", "QA"),
        ("(draft) plan", "draft plan"),
    ],
)
def test_same_words_share_a_key(a: str, b: str) -> None:
    assert spelling_key(a) == spelling_key(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("दिन", "दीन"),  # Devanagari: "day" vs "poor"; only the vowel sign differs
        ("बल", "बाल"),  # "strength" vs "hair"
        ("C++", "C#"),
        ("C++", "C"),
        ("C#", "C"),
        ("100%", "100"),
        ("كتب", "كتّب"),  # Arabic shadda changes the verb form
        ("Ελλάδα", "Ελλαδα"),  # Greek tonos is part of the spelling
        ("Security", "Security incident response"),
        ("x86", "x64"),
    ],
)
def test_different_words_do_not_share_a_key(a: str, b: str) -> None:
    assert spelling_key(a) != spelling_key(b)


def test_key_of_blank_is_empty() -> None:
    assert spelling_key("  -- ") == ""


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Python 3.11", "Python 311"),
        ("v1.0", "v10"),
        ("1/2", "12"),
        ("-5", "5"),
        ("A*", "A"),
        (".NET", "NET"),
        ("2024-25", "202425"),
    ],
)
def test_punctuation_that_changes_a_number_or_name_is_kept(a: str, b: str) -> None:
    assert spelling_key(a) != spelling_key(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Python 3.11", "python 3.11"),
        ("release-checklist v2", "Release Checklist v2"),
        ("ASP.NET", "asp.net"),
    ],
)
def test_case_and_word_separators_still_match_around_numbers(a: str, b: str) -> None:
    assert spelling_key(a) == spelling_key(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("(.NET)", "NET"),
        ("(-5)", "5"),
        ("[.NET]", "NET"),
        ('".NET" runtime', "NET runtime"),
        ("Runtime (.NET)", "Runtime NET"),
    ],
)
def test_leading_mark_after_an_opening_bracket_or_quote_is_kept(a: str, b: str) -> None:
    assert spelling_key(a) != spelling_key(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("(.NET)", ".NET"),
        ("(-5)", "-5"),
        ("[(.NET)]", ".NET"),
        ("Runtime (.NET)", "runtime .net"),
    ],
)
def test_bracketed_leading_mark_matches_the_unbracketed_form(a: str, b: str) -> None:
    assert spelling_key(a) == spelling_key(b)


def test_mark_inside_a_word_is_still_dropped() -> None:
    assert spelling_key("x(.net)") == spelling_key("xnet")
