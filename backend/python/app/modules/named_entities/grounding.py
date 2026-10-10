"""Align a model's verbatim span to a block. Offsets come from this search."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from itertools import islice

from app.modules.named_entities.text import TextUnit

_WS = re.compile(r"\s+")
_WORD = re.compile(r"\S+")
FUZZY_MIN = 0.85
# A block can run to the record cap and grounding runs on the event loop, so the
# fuzzy scan covers the head the model is shown plus windows around a few literal
# hits of the surface's longest word, not the whole block.
_HEAD_CHARS = 3_000
_MAX_ANCHORS = 20
# Fuzzy matching costs tens of ms per item. A long surface is never a near-miss
# spelling, and a run that misses again and again is not helped by more scans.
_MAX_FUZZY_WORDS = 10
_FUZZY_BUDGET = 200
_NEAREST_BUDGET = 50


def _fold(text: str) -> str:
    return _WS.sub(" ", text).casefold().strip()


def _regions(text: str, surface: str) -> list[tuple[int, int]]:
    regions = [(0, min(len(text), _HEAD_CHARS))]
    anchor = max(surface.split(), key=len, default="")
    if len(text) <= _HEAD_CHARS or not anchor:
        return regions
    pad = 2 * len(surface)
    for match in islice(re.finditer(re.escape(anchor), text, re.IGNORECASE), _MAX_ANCHORS):
        regions.append((max(0, match.start() - pad), min(len(text), match.end() + pad)))
    return regions


def _words(text: str, lo: int, hi: int) -> list[tuple[int, int]]:
    """Word spans inside ``[lo, hi)``, without a word cut by either edge."""
    spans = [match.span() for match in _WORD.finditer(text, lo, hi)]
    if spans and lo > 0 and spans[0][0] == lo and not text[lo - 1].isspace():
        spans.pop(0)
    if spans and hi < len(text) and spans[-1][1] == hi and not text[hi].isspace():
        spans.pop()
    return spans


def _word_char(char: str) -> bool:
    # Scripts written without spaces (CJK, Thai) have no word boundary to check.
    return char.isalnum() and ord(char) < 0x2E80 and not 0x0E00 <= ord(char) <= 0x0EFF


def _whole(text: str, surface: str, flags: int) -> re.Match[str] | None:
    """The first match that is not part of a longer word: ``Ed`` is not inside
    ``Edward``, and ``$5`` is not inside ``$50``."""
    for match in re.finditer(re.escape(surface), text, flags):
        start, end = match.span()
        if start > 0 and _word_char(text[start - 1]) and _word_char(text[start]):
            continue
        if end < len(text) and _word_char(text[end]) and _word_char(text[end - 1]):
            continue
        return match
    return None


class Grounder:
    """Not thread-safe: one caller at a time (see ``ExtractionSession.staged``)."""

    def __init__(self, units: list[TextUnit]) -> None:
        self._by_index = {unit.block_index: unit for unit in units}
        self._fuzzy_left = _FUZZY_BUDGET
        self._nearest_left = _NEAREST_BUDGET

    def align(self, block_index: int, surface: str, *, fuzzy: bool = True) -> tuple[int, int, str, str] | None:
        """Return ``(char_start, char_end, matched_surface, grounding)`` or None.
        ``fuzzy=False`` for values: a near miss of ``$12,500`` is ``$12,600``."""
        unit = self._by_index.get(block_index)
        text = (surface or "").strip()
        if unit is None or not text or len(text) > 256:
            return None
        # Case-sensitive first. Not casefold: it can change the length ("ß" -> "ss"),
        # so its offsets would not index the original text.
        match = _whole(unit.text, text, 0) or _whole(unit.text, text, re.IGNORECASE)
        if match:
            return match.start(), match.end(), match.group(0), "exact"
        if not fuzzy or len(text.split()) > _MAX_FUZZY_WORDS or self._fuzzy_left <= 0:
            return None
        self._fuzzy_left -= 1
        return self._fuzzy(unit.text, text)

    def nearest(self, block_index: int, surface: str) -> str:
        unit = self._by_index.get(block_index)
        if unit is None or len(surface.split()) > _MAX_FUZZY_WORDS or self._nearest_left <= 0:
            return ""
        self._nearest_left -= 1
        needle = _fold(surface)
        if not needle:
            return ""
        best = ""
        best_ratio = 0.0
        width = max(1, len(needle.split()))
        for lo, hi in _regions(unit.text, surface):
            words = [unit.text[start:end] for start, end in _words(unit.text, lo, hi)]
            for size in (width, width + 1):
                for i in range(0, max(1, len(words) - size + 1)):
                    window = " ".join(words[i : i + size])
                    ratio = SequenceMatcher(None, needle, _fold(window)).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best = window
        return best if best_ratio >= 0.5 else ""

    def _fuzzy(self, text: str, surface: str) -> tuple[int, int, str, str] | None:
        needle = _fold(surface)
        if not needle:
            return None
        width = max(1, len(needle.split()))
        best: tuple[float, int, int] | None = None
        for lo, hi in _regions(text, surface):
            spans = _words(text, lo, hi)
            for size in (width, max(1, width - 1), width + 1):
                for i in range(0, len(spans) - size + 1):
                    start = spans[i][0]
                    end = spans[i + size - 1][1]
                    ratio = SequenceMatcher(None, needle, _fold(text[start:end])).ratio()
                    if best is None or ratio > best[0]:
                        best = (ratio, start, end)
        if best is None or best[0] < FUZZY_MIN:
            return None
        _, start, end = best
        return start, end, text[start:end], "fuzzy"
