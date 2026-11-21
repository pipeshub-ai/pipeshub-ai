"""Split a rendered block into units whose edges are guaranteed word boundaries.

A browser only accepts a term that starts and ends on a word boundary
(UAX #29). For space-delimited scripts that holds at every whitespace edge, and
at a token edge once surrounding punctuation is trimmed. Scripts written
without spaces (CJK, Thai, ...) need a dictionary to find boundaries, which we
do not have, so for those a unit is a run of letters between punctuation, and
terms are never cut inside a unit.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.utils.text_fragments.models import TextFragmentConfig

_UNSPACED_RANGES: tuple[tuple[int, int], ...] = (
    (0x0E00, 0x0EFF),  # Thai, Lao
    (0x1000, 0x109F),  # Myanmar
    (0x1780, 0x17FF),  # Khmer
    (0x2E80, 0x9FFF),  # CJK radicals, kana, bopomofo, CJK ideographs
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0xFF66, 0xFF9F),  # halfwidth katakana
    (0x20000, 0x2FA1F),  # CJK extensions B and beyond
)


# A citation link is built for every block of a fetched page, megabytes of it; a per-character range check in Python
# made that the bulk of reading a large page.
_UNSPACED_CHAR = re.compile(
    "[" + "".join(f"{re.escape(chr(low))}-{re.escape(chr(high))}" for low, high in _UNSPACED_RANGES) + "]"
)


def is_unspaced_script(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _UNSPACED_RANGES)


def has_unspaced_script(text: str) -> bool:
    return _UNSPACED_CHAR.search(text) is not None


def is_word_char(char: str) -> bool:
    """True for characters that stay glued to a word under UAX #29.

    Underscore joins words (ExtendNumLet) and combining marks extend them, so
    trimming either off a term edge would leave the edge inside a word.
    """
    return char.isalnum() or char == "_" or unicodedata.category(char).startswith("M")


def _is_internal_apostrophe(token: str, index: int) -> bool:
    return (
        token[index] in ("'", "\u2019")
        and index > 0
        and index + 1 < len(token)
        and is_word_char(token[index - 1])
        and is_word_char(token[index + 1])
    )


@dataclass(frozen=True)
class Unit:
    start: int
    end: int
    unspaced: bool


@dataclass(frozen=True)
class SegmentedBlock:
    text: str
    units: tuple[Unit, ...]
    alnum_count: int

    def slice(self, first: int, last: int) -> str:
        """Text from unit `first` through unit `last` (inclusive), punctuation between kept."""
        return self.text[self.units[first].start:self.units[last].end]

    @property
    def unspaced_chars(self) -> int:
        return sum(unit.end - unit.start for unit in self.units if unit.unspaced)


class WordSegmenter:
    def __init__(self, config: TextFragmentConfig) -> None:
        self._config = config

    def segment(self, block: str) -> SegmentedBlock | None:
        units: list[Unit] = []
        position = 0
        for token in block.split(" "):
            token_start = position
            position += len(token) + 1
            if token:
                units.extend(self._units_of_token(token, token_start))
        if not units:
            return None
        alnum = sum(map(str.isalnum, block))
        return SegmentedBlock(text=block, units=tuple(units), alnum_count=alnum)

    def is_eligible(self, block: SegmentedBlock) -> bool:
        return block.alnum_count >= self._config.min_term_alnum_chars

    def head_span(self, block: SegmentedBlock) -> tuple[int, int]:
        """Unit indexes `(first, last)` of the leading term."""
        last = self._extent(block, range(len(block.units)))
        return 0, last

    def tail_span(self, block: SegmentedBlock) -> tuple[int, int]:
        """Unit indexes `(first, last)` of the trailing term."""
        count = len(block.units)
        first = self._extent(block, range(count - 1, -1, -1))
        return first, count - 1

    def is_exact_candidate(self, block: SegmentedBlock) -> bool:
        return (
            len(block.units) <= self._config.exact_max_words
            and len(block.text) <= self._config.exact_max_chars
            and block.unspaced_chars <= self._config.cjk_term_chars * 2
        )

    def _extent(self, block: SegmentedBlock, order: range) -> int:
        """Walk units in `order` until the word or unspaced-character budget is spent."""
        words = 0
        unspaced_chars = 0
        reached = order[0]
        for index in order:
            unit = block.units[index]
            reached = index
            words += 1
            if unit.unspaced:
                unspaced_chars += unit.end - unit.start
            if words >= self._config.range_term_words or unspaced_chars >= self._config.cjk_term_chars:
                break
        return reached

    @staticmethod
    def _units_of_token(token: str, offset: int) -> list[Unit]:
        if has_unspaced_script(token):
            return _runs_of_word_chars(token, offset)
        first = next((i for i, ch in enumerate(token) if is_word_char(ch)), None)
        if first is None:
            return []
        last = next(i for i in range(len(token) - 1, first - 1, -1) if is_word_char(token[i]))
        return [Unit(offset + first, offset + last + 1, unspaced=False)]


def _runs_of_word_chars(token: str, offset: int) -> list[Unit]:
    units: list[Unit] = []
    run_start: int | None = None
    for index, char in enumerate(token + "\0"):
        if index < len(token) and (
            is_word_char(char) or _is_internal_apostrophe(token, index)
        ):
            if run_start is None:
                run_start = index
            continue
        if run_start is not None:
            run = token[run_start:index]
            units.append(
                Unit(offset + run_start, offset + index, unspaced=has_unspaced_script(run))
            )
            run_start = None
    return units
