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
