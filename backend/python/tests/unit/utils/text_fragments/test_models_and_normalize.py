import pytest

from app.utils.text_fragments.models import (
    SourceFormat,
    TextDirective,
    TextFragmentConfig,
)
from app.utils.text_fragments.normalize import TextNormalizer


class TestSourceFormat:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("markdown", SourceFormat.MARKDOWN),
            ("MARKDOWN", SourceFormat.MARKDOWN),
            ("html", SourceFormat.HTML),
            ("txt", SourceFormat.PLAIN),
            ("utf8", SourceFormat.PLAIN),
            ("csv", SourceFormat.PLAIN),
            ("json", SourceFormat.PLAIN),
            ("diff", SourceFormat.PLAIN),
            ("code", SourceFormat.PLAIN),
            ("bin", None),
            (None, None),
            (3, None),
        ],
    )
    def test_from_data_format(self, value: object, expected: SourceFormat | None) -> None:
        assert SourceFormat.from_data_format(value) is expected

    def test_accepts_enum_members(self) -> None:
        from app.models.blocks import DataFormat

        assert SourceFormat.from_data_format(DataFormat.HTML) is SourceFormat.HTML
        assert SourceFormat.from_data_format(DataFormat.TXT) is SourceFormat.PLAIN


class TestTextDirective:
    def test_requires_start(self) -> None:
        with pytest.raises(ValueError):
            TextDirective(start="")

    @pytest.mark.parametrize("field", ["end", "prefix", "suffix"])
    def test_optional_terms_must_be_none_or_non_empty(self, field: str) -> None:
        with pytest.raises(ValueError):
            TextDirective(start="a", **{field: ""})


class TestTextFragmentConfig:
    def test_defaults_are_valid(self) -> None:
        config = TextFragmentConfig()
        assert config.exact_max_words == 8
        assert config.range_term_words == 4

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"exact_max_words": 0},
            {"range_term_words": 0},
            {"min_term_alnum_chars": 0},
            {"cjk_term_chars": 0},
        ],
    )
    def test_rejects_non_positive_limits(self, kwargs: dict[str, int]) -> None:
        with pytest.raises(ValueError):
            TextFragmentConfig(**kwargs)


class TestTextNormalizer:
    normalizer = TextNormalizer()

    def test_collapses_all_whitespace(self) -> None:
        assert self.normalizer.normalize("  a \n\t b\u00a0\u2009c  ") == "a b c"

    def test_applies_nfc(self) -> None:
        assert self.normalizer.normalize("e\u0301") == "\u00e9"

    @pytest.mark.parametrize("invisible", ["\u00ad", "\u200b", "\u200e", "\u200f", "\u2060", "\ufeff", "\u202a"])
    def test_removes_invisible_characters(self, invisible: str) -> None:
        assert self.normalizer.normalize(f"ab{invisible}cd") == "abcd"

    def test_keeps_joiners_used_by_shaping(self) -> None:
        assert self.normalizer.normalize("a\u200db") == "a\u200db"

    def test_empty(self) -> None:
        assert self.normalizer.normalize("   ") == ""
