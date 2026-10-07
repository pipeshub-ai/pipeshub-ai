"""Choose the directive terms for a set of rendered blocks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from app.utils.text_fragments.models import TextDirective

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.utils.text_fragments.segmentation import SegmentedBlock, WordSegmenter


class DirectiveStrategy(Protocol):
    def build(self, blocks: Sequence[SegmentedBlock]) -> TextDirective | None:
        """Build a directive from eligible, non-empty blocks; None if this strategy does not apply."""
        ...


class ExactMatchStrategy:
    """Quote a single short block in full.

    Preferred by the spec because the URL still says what was being looked for
    if the page changes. Only applies to one block: a term cannot cross blocks.
    """

    def __init__(self, segmenter: WordSegmenter) -> None:
        self._segmenter = segmenter

    def build(self, blocks: Sequence[SegmentedBlock]) -> TextDirective | None:
        if len(blocks) != 1 or not self._segmenter.is_exact_candidate(blocks[0]):
            return None
        block = blocks[0]
        return TextDirective(start=block.slice(0, len(block.units) - 1))


class RangeStrategy:
    """Quote the opening words of the first block and the closing words of the last.

    Each term stays inside its own block, as a browser requires. When the
    snippet is one block too short to hold two disjoint terms, the whole block
    is quoted instead of two overlapping terms the browser could not match.
    """

    def __init__(self, segmenter: WordSegmenter) -> None:
        self._segmenter = segmenter

    def build(self, blocks: Sequence[SegmentedBlock]) -> TextDirective | None:
        if not blocks:
            return None
        first, last = blocks[0], blocks[-1]
        head_from, head_to = self._segmenter.head_span(first)
        tail_from, tail_to = self._segmenter.tail_span(last)

        if first is last and head_to >= tail_from:
            return TextDirective(start=first.slice(0, len(first.units) - 1))

        return TextDirective(
            start=first.slice(head_from, head_to),
            end=last.slice(tail_from, tail_to),
        )


class HybridDirectiveStrategy:
    """Exact match when it fits, otherwise a range."""

    def __init__(
        self,
        segmenter: WordSegmenter,
        strategies: Sequence[DirectiveStrategy] | None = None,
    ) -> None:
        self._strategies: tuple[DirectiveStrategy, ...] = tuple(
            strategies or (ExactMatchStrategy(segmenter), RangeStrategy(segmenter))
        )

    def build(self, blocks: Sequence[SegmentedBlock]) -> TextDirective | None:
        for strategy in self._strategies:
            directive = strategy.build(blocks)
            if directive is not None:
                return directive
        return None
