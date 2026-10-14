"""Candidate spans for dates, money, percents and quantities.

A regex only proposes a span. The normalizer decides whether it is real,
so 'v1.2' never becomes money.
"""

from __future__ import annotations

import re

from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.mentions import RawMention
from app.modules.named_entities.normalizers.money import ISO_4217, SCALE_WORDS
from app.modules.named_entities.text import TextUnit

_MONTH = (
    r"January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
_DATE = re.compile(
    rf"\b(?:Q[1-4]\s*(?:FY\s*)?\d{{2,4}}|FY\s?(?:\d{{4}}|\d{{2}})|(?:{_MONTH})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:(?:,\s*|\s+)\d{{4}})?|"
    rf"\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{_MONTH})\b(?:(?:,\s*|\s+)\d{{4}})?|"
    rf"(?:{_MONTH})\.?\s+\d{{4}}|\d{{4}}-\d{{2}}-\d{{2}}(?:[T ]\d{{2}}:\d{{2}}(?::\d{{2}})?(?:Z|[+-]\d{{2}}:?\d{{2}})?)?|"
    rf"\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{2,4}}|(?:next|last|this)\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|week|month|quarter|year)|"
    rf"\d+\s+(?:days?|weeks?|months?|years?|hours?|minutes?)|"
    # Short time units; bare "s" and "h" stay out ("1990s", "10s of thousands").
    rf"\d+(?:\.\d+)?\s?(?:ms|msecs?|milliseconds?|secs?|seconds?|mins?|hrs?))\b",
    re.IGNORECASE,
)
# Codes that are also common words in capitals ("TOP 10", "ALL 100 EMPLOYEES").
_WORD_CODES = frozenset({"ALL", "CUP", "SOS", "TOP"})
_CODES = "|".join(sorted(ISO_4217 - _WORD_CODES))
# Possessive groups and the lookbehinds keep the scan linear: a match starts only
# at the first digit of a run and never backtracks into it. Without them a long
# run of "123 456 ..." costs O(n^2), hours for a numeric CSV.
# A plain space groups thousands only where the currency follows ("1 250 €");
# "$5 100 times" is five dollars.
_NUMBER = r"(?>\d{1,3}(?:[,.\u00a0\u202f]\d{3})++(?:[.,]\d+)?+|\d++(?:[.,]\d++)?+)"
_SPACED_NUMBER = r"(?>\d{1,3}(?:[,.\u00a0\u202f ]\d{3})++(?:[.,]\d+)?+|\d++(?:[.,]\d++)?+)"
_SCALE = rf"(?:\s?(?i:{SCALE_WORDS})\b)?"
# "CAD 3D" and "$5MM" are not an amount followed by more letters.
_END = r"(?![A-Za-z\d])"
_MONEY = re.compile(
    rf"(?<![A-Za-z0-9.])(?:"
    rf"(?:US\$|R\$|C\$|A\$|HK\$|NZ\$|S\$|CN¥|RMB|Rs\.?|[$€£¥₹₩₽₺₪₫₱])\s?{_NUMBER}{_SCALE}{_END}"
    rf"|(?:{_CODES})\s?{_NUMBER}{_SCALE}{_END}"
    rf"|(?<![\d,][\s\u00a0])(?<!,){_SPACED_NUMBER}{_SCALE}\s?(?:(?:{_CODES})\b|[€£₹]|(?i:dollar|euro|yen|rupee)s?\b))",
)
# A grouped number is read whole, so "1,000%" is never "000%".
_GROUPED = r"(?>\d{1,3}(?:,\d{3})++|\d++)(?:\.\d++)?+"
# No \b after "%": it is not a word character, so "12.5% " has no boundary there.
_PERCENT = re.compile(rf"(?<![\d.,]){_GROUPED}\s?(?:%|(?:bps|percent|per\s?cent|pct)\b)", re.IGNORECASE)
# Bare "m" and "g" are lowercase only: "100M users" and "5G" are counts and networks.
_QUANTITY = re.compile(
    rf"(?<![\d.,]){_GROUPED}\s?(?:km/h|km|cm|mm|kg|lb|lbs|mi|ft|mph|kph|°C|°F|celsius|fahrenheit"
    r"|[kmgtp]i?b|(?-i:m|g))\b(?!/)"
    rf"|(?<![\d.,]){_GROUPED}\s?[kKmM]?\s?tokens?\b",
    re.IGNORECASE,
)
_AGE = re.compile(r"\b\d{1,3}[\-\s]?years?[\-\s]?old\b", re.IGNORECASE)


class ValueCandidateRecognizer:
    name = "value"

    def __init__(self, enabled: frozenset[EntityKind]) -> None:
        self._enabled = enabled

    def recognize(self, units: list[TextUnit]) -> list[RawMention]:
        found: list[RawMention] = []
        for unit in units:
            found.extend(self._scan(unit))
        return found

    def _add(
        self,
        out: list[RawMention],
        unit: TextUnit,
        kind: EntityKind,
        match: re.Match[str],
    ) -> None:
        if kind not in self._enabled:
            return
        start, end = match.span()
        if kind in _SIGNED:
            start, end = _with_sign(unit.text, start, end)
        out.append(
            RawMention(
                kind=kind,
                surface=unit.text[start:end],
                block_index=unit.block_index,
                block_id=unit.block_id,
                char_start=start,
                char_end=end,
                extractor="value",
                evidence_score=0.8,
            )
        )

    def _scan(self, unit: TextUnit) -> list[RawMention]:
        out: list[RawMention] = []
        date_kinds = {EntityKind.DATE, EntityKind.DATE_RANGE, EntityKind.DATE_TIME, EntityKind.DURATION}
        if date_kinds & self._enabled:
            for match in _DATE.finditer(unit.text):
                surface = match.group(0)
                kind = _date_kind(surface)
                self._add(out, unit, kind, match)
        if EntityKind.CURRENCY in self._enabled:
            for match in _MONEY.finditer(unit.text):
                self._add(out, unit, EntityKind.CURRENCY, match)
        if EntityKind.PERCENTAGE in self._enabled:
            for match in _PERCENT.finditer(unit.text):
                self._add(out, unit, EntityKind.PERCENTAGE, match)
        if EntityKind.DIMENSION in self._enabled:
            for match in _QUANTITY.finditer(unit.text):
                self._add(out, unit, EntityKind.DIMENSION, match)
        if EntityKind.AGE in self._enabled:
            for match in _AGE.finditer(unit.text):
                self._add(out, unit, EntityKind.AGE, match)
        return out


_SIGNED = frozenset({EntityKind.CURRENCY, EntityKind.PERCENTAGE})


def _with_sign(text: str, start: int, end: int) -> tuple[int, int]:
    """Take in a minus sign or accounting parentheses: a refund of "-$500" or a
    "($1,200)" loss is not the same amount as a $500 fee."""
    if start > 0 and end < len(text) and text[start - 1] == "(" and text[end] == ")":
        return start - 1, end + 1
    if start > 0 and text[start - 1] in "-−\u2012\u2013" and (start == 1 or not text[start - 2].isalnum()):
        return start - 1, end
    return start, end


def _date_kind(surface: str) -> EntityKind:
    lowered = surface.casefold()
    if re.search(r"\d+\s+(?:days?|weeks?|months?|years?|hours?|minutes?)", lowered) or re.fullmatch(
        r"\d+(?:\.\d+)?\s?(?:ms|msecs?|milliseconds?|secs?|seconds?|mins?|hrs?)", lowered,
    ):
        return EntityKind.DURATION
    if lowered.startswith(("q", "fy")) or " to " in lowered or "/" in lowered and "fy" in lowered:
        return EntityKind.DATE_RANGE
    if re.fullmatch(r"(?:next|last|this)\s+(?:week|month|quarter|year)", lowered):
        return EntityKind.DATE_RANGE
    if "t" in lowered and ":" in lowered:
        return EntityKind.DATE_TIME
    return EntityKind.DATE
