from app.utils.text_fragments.models import TextDirective, TextFragmentConfig
from app.utils.text_fragments.segmentation import SegmentedBlock, WordSegmenter
from app.utils.text_fragments.strategies import (
    ExactMatchStrategy,
    HybridDirectiveStrategy,
    RangeStrategy,
)

segmenter = WordSegmenter(TextFragmentConfig())


def _blocks(*texts: str) -> list[SegmentedBlock]:
    segmented = [segmenter.segment(text) for text in texts]
    assert all(block is not None for block in segmented)
    return segmented  # type: ignore[return-value]


class TestExactMatchStrategy:
    strategy = ExactMatchStrategy(segmenter)

    def test_quotes_a_short_single_block(self) -> None:
        directive = self.strategy.build(_blocks("The e-mail, from Zürich!"))
        assert directive == TextDirective(start="The e-mail, from Zürich")

    def test_declines_multiple_blocks(self) -> None:
        assert self.strategy.build(_blocks("one two", "three four")) is None

    def test_declines_long_block(self) -> None:
        assert self.strategy.build(_blocks(" ".join(["word"] * 9))) is None

    def test_declines_empty_input(self) -> None:
        assert self.strategy.build([]) is None


class TestRangeStrategy:
    strategy = RangeStrategy(segmenter)

    def test_single_long_block_uses_head_and_tail(self) -> None:
        directive = self.strategy.build(_blocks("one two three four five six seven eight nine ten"))
        assert directive == TextDirective(start="one two three four", end="seven eight nine ten")

    def test_multiple_blocks_use_first_head_and_last_tail(self) -> None:
        directive = self.strategy.build(
            _blocks("alpha beta gamma delta epsilon", "middle block here", "uno dos tres cuatro cinco")
        )
        assert directive == TextDirective(start="alpha beta gamma delta", end="dos tres cuatro cinco")

    def test_overlapping_terms_in_one_block_fall_back_to_the_whole_block(self) -> None:
        directive = self.strategy.build(_blocks("one two three four five six"))
        assert directive == TextDirective(start="one two three four five six")

    def test_terms_never_cross_blocks(self) -> None:
        directive = self.strategy.build(_blocks("alpha beta", "gamma delta"))
        assert directive == TextDirective(start="alpha beta", end="gamma delta")

    def test_empty_input(self) -> None:
        assert self.strategy.build([]) is None


class TestHybridDirectiveStrategy:
    strategy = HybridDirectiveStrategy(segmenter)

    def test_prefers_exact_for_short_single_block(self) -> None:
        directive = self.strategy.build(_blocks("short quote here"))
        assert directive == TextDirective(start="short quote here")

    def test_falls_back_to_range_for_long_single_block(self) -> None:
        directive = self.strategy.build(_blocks(" ".join(f"w{i}x" for i in range(12))))
        assert directive is not None
        assert directive.end is not None
        assert directive.start == "w0x w1x w2x w3x"
        assert directive.end == "w8x w9x w10x w11x"

    def test_uses_range_for_multiple_blocks(self) -> None:
        directive = self.strategy.build(_blocks("first block", "second block"))
        assert directive == TextDirective(start="first block", end="second block")

    def test_strategies_are_injectable(self) -> None:
        class Never:
            def build(self, blocks) -> None:
                return None

        assert HybridDirectiveStrategy(segmenter, [Never()]).build(_blocks("some text")) is None
