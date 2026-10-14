import pytest

from app.utils.text_fragments.models import TextFragmentConfig
from app.utils.text_fragments.segmentation import (
    SegmentedBlock,
    WordSegmenter,
    is_unspaced_script,
    is_word_char,
)

segmenter = WordSegmenter(TextFragmentConfig())


def _segment(text: str) -> SegmentedBlock:
    block = segmenter.segment(text)
    assert block is not None
    return block


class TestCharacterClasses:
    @pytest.mark.parametrize("char", ["a", "Z", "7", "é", "_", "\u0301", "日"])
    def test_word_chars(self, char: str) -> None:
        assert is_word_char(char)

    @pytest.mark.parametrize("char", [" ", "-", ",", ".", "(", "*", "$", "\u2014"])
    def test_non_word_chars(self, char: str) -> None:
        assert not is_word_char(char)

    @pytest.mark.parametrize("char", ["日", "本", "ひ", "カ", "ก", "ກ", "မ", "ក"])
    def test_unspaced_scripts(self, char: str) -> None:
        assert is_unspaced_script(char)

    @pytest.mark.parametrize("char", ["a", "é", "я", "ا", "한"])
    def test_spaced_scripts(self, char: str) -> None:
        assert not is_unspaced_script(char)


class TestSegment:
    def test_trims_surrounding_punctuation_from_each_token(self) -> None:
        block = _segment("(hello), \"world\"! -- end.")
        assert [block.text[u.start:u.end] for u in block.units] == ["hello", "world", "end"]

    def test_internal_punctuation_stays_inside_a_unit(self) -> None:
        block = _segment("the e-mail from a.b.c costs $5.99")
        assert [block.text[u.start:u.end] for u in block.units] == [
            "the",
            "e-mail",
            "from",
            "a.b.c",
            "costs",
            "5.99",
        ]

    def test_punctuation_only_tokens_are_dropped(self) -> None:
        block = _segment("a - b")
        assert [block.text[u.start:u.end] for u in block.units] == ["a", "b"]

    def test_leading_symbols_do_not_drop_words(self) -> None:
        block = _segment("**Q3** — results")
        assert [block.text[u.start:u.end] for u in block.units] == ["Q3", "results"]

    def test_unspaced_token_splits_on_punctuation_runs(self) -> None:
        block = _segment("日本語、テスト")
        assert [block.text[u.start:u.end] for u in block.units] == ["日本語", "テスト"]
        assert all(u.unspaced for u in block.units)
        assert block.unspaced_chars == 6

    def test_mixed_script_token_marks_only_unspaced_runs(self) -> None:
        block = _segment("GPT日本語")
        assert any(u.unspaced for u in block.units)

    def test_returns_none_without_words(self) -> None:
        assert segmenter.segment("") is None
        assert segmenter.segment("--- ... ***") is None

    def test_slice_keeps_punctuation_between_units(self) -> None:
        block = _segment("alpha, beta - gamma")
        assert block.slice(0, 2) == "alpha, beta - gamma"
        assert block.slice(1, 1) == "beta"

    def test_alnum_count(self) -> None:
        assert _segment("a-b c!").alnum_count == 3


class TestEligibility:
    def test_requires_enough_alphanumerics(self) -> None:
        assert not segmenter.is_eligible(_segment("a b"))
        assert segmenter.is_eligible(_segment("a bc"))


class TestSpans:
    def test_head_and_tail_are_limited_to_term_word_count(self) -> None:
        block = _segment("one two three four five six seven")
        assert segmenter.head_span(block) == (0, 3)
        assert segmenter.tail_span(block) == (3, 6)

    def test_short_block_spans_everything(self) -> None:
        block = _segment("one two")
        assert segmenter.head_span(block) == (0, 1)
        assert segmenter.tail_span(block) == (0, 1)

    def test_unspaced_budget_limits_term_length(self) -> None:
        block = _segment("あいうえお、かきくけこ、さしすせそ")
        first, last = segmenter.head_span(block)
        assert (first, last) == (0, 1)
        first, last = segmenter.tail_span(block)
        assert (first, last) == (1, 2)


class TestExactCandidate:
    def test_short_block_is_a_candidate(self) -> None:
        assert segmenter.is_exact_candidate(_segment("one two three"))

    def test_too_many_words(self) -> None:
        assert not segmenter.is_exact_candidate(_segment(" ".join(["word"] * 9)))
        assert segmenter.is_exact_candidate(_segment(" ".join(["word"] * 8)))

    def test_too_many_characters(self) -> None:
        assert not segmenter.is_exact_candidate(_segment("x" * 301))

    def test_too_many_unspaced_characters(self) -> None:
        assert not segmenter.is_exact_candidate(_segment("あ" * 21))
        assert segmenter.is_exact_candidate(_segment("あ" * 20))
