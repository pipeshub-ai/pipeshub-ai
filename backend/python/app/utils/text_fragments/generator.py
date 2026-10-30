from __future__ import annotations

from functools import cache, partial
from typing import TYPE_CHECKING

from app.utils.logger import create_logger
from app.utils.text_fragments.extractors import ExtractorRegistry
from app.utils.text_fragments.models import (
    SourceFormat,
    TextDirective,
    TextFragmentConfig,
)
from app.utils.text_fragments.normalize import TextNormalizer
from app.utils.text_fragments.segmentation import SegmentedBlock, WordSegmenter
from app.utils.text_fragments.strategies import (
    DirectiveStrategy,
    HybridDirectiveStrategy,
)
from app.utils.text_fragments.url import append_directive, has_fragment_directive

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = create_logger(__name__)


class TextFragmentGenerator:
    """Builds `#:~:text=` URLs from stored snippet text.

    Pipeline: extract rendered blocks -> normalize -> segment -> pick terms.
    Every collaborator is injectable; the defaults implement the policy
    described in docs/text-fragments.md.
    """

    def __init__(
        self,
        config: TextFragmentConfig | None = None,
        registry: ExtractorRegistry | None = None,
        normalizer: TextNormalizer | None = None,
        segmenter: WordSegmenter | None = None,
        strategy: DirectiveStrategy | None = None,
    ) -> None:
        self._config = config or TextFragmentConfig()
        self._registry = registry or ExtractorRegistry.with_defaults(self._config.default_format)
        self._normalizer = normalizer or TextNormalizer()
        self._segmenter = segmenter or WordSegmenter(self._config)
        self._strategy = strategy or HybridDirectiveStrategy(self._segmenter)

    def rendered_blocks(self, snippet: str, source_format: SourceFormat | None = None) -> list[str]:
        """The normalized blocks of text `snippet` renders to."""
        raw_blocks = self._registry.get(source_format).extract(snippet)
        normalized = (self._normalizer.normalize(block) for block in raw_blocks)
        return [block for block in normalized if block]

    def build_directive(
        self, snippet: str, source_format: SourceFormat | None = None
    ) -> TextDirective | None:
        groups = self._block_groups(snippet, source_format)
        blocks_of = cache(lambda group: groups[group]())
        forward = (
            (group, index, raw) for group in range(len(groups)) for index, raw in enumerate(blocks_of(group))
        )
        first = self._first_eligible(forward)
        if first is None:
            return None
        # Strategies read only the first and last eligible block, so the blocks
        # between them are never parsed, normalized or segmented.
        backward = (
            (group, index, raw)
            for group in range(len(groups) - 1, first[0] - 1, -1)
            for index, raw in reversed(list(enumerate(blocks_of(group))))
        )
        last = self._first_eligible(backward)
        picked = [first[2]] if last[:2] == first[:2] else [first[2], last[2]]
        segmented: list[SegmentedBlock] = [self._segmenter.segment(block) for block in picked]
        return self._strategy.build(segmented)

    def _block_groups(self, snippet: str, source_format: SourceFormat | None) -> list[Callable[[], list[str]]]:
        """Raw blocks in document order, as callables an extractor can evaluate lazily."""
        extractor = self._registry.get(source_format)
        extract_groups = getattr(extractor, "extract_groups", None)
        return extract_groups(snippet) if extract_groups else [partial(extractor.extract, snippet)]

    def _first_eligible(self, blocks: Iterator[tuple[int, int, str]]) -> tuple[int, int, str] | None:
        """Position and normalized text of the first eligible block in `blocks`."""
        for group, index, raw in blocks:
            block = self._normalizer.normalize(raw)
            if self._segmenter.is_eligible_text(block):
                return group, index, block
        return None

    def build_url(
        self, base_url: str, snippet: str, source_format: SourceFormat | None = None
    ) -> str:
        """`base_url` with a text directive for `snippet`, or `base_url` unchanged.

        Never raises: a citation link without a highlight is still a working link.
        """
        if not isinstance(base_url, str) or not isinstance(snippet, str):
            return base_url
        if not base_url or not snippet.strip() or has_fragment_directive(base_url):
            return base_url
        try:
            directive = self.build_directive(snippet, source_format)
            if directive is None:
                logger.debug("No text directive could be built for %s", base_url)
                return base_url
            return append_directive(base_url, directive)
        except Exception:
            logger.warning("Text fragment generation failed for %s", base_url, exc_info=True)
            return base_url
