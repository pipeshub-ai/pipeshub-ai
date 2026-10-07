"""Languages resolve through the ISO table, never through the vector or model tiers."""

import pytest

from app.modules.entity_resolution.languages import canonical_language


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("english", "English"),
        ("English", "English"),
        ("en", "English"),
        ("EN-US", "English"),
        ("en_GB", "English"),
        ("English (US)", "English"),
        ("Français", "French"),
        ("francais", "French"),
        ("fr", "French"),
        ("Deutsch", "German"),
        ("zh-Hans", "Chinese"),
        ("日本語", "Japanese"),
        ("हिन्दी", "Hindi"),
        ("pt-br", "Portuguese"),
    ],
)
def test_known_values_map_to_canonical(raw, expected) -> None:
    assert canonical_language(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "Klingon", "xx-YY", "123"])
def test_unknown_values_return_none(raw) -> None:
    assert canonical_language(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"), [("Welsh", "Welsh"), ("cy", "Welsh"), ("isl", "Icelandic"), ("Slovene", "Slovenian")],
)
def test_natural_languages_outside_the_table_resolve_through_lingua(raw, expected) -> None:
    assert canonical_language(raw) == expected


@pytest.mark.parametrize("raw", ["Python", "Go", "TypeScript", "JSON", "YAML", "Bash", "American Sign Language"])
def test_code_and_data_formats_are_not_languages(raw) -> None:
    assert canonical_language(raw) is None
