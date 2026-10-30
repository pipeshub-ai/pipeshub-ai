"""Candidate spans for dates, money, percents and quantities.

A regex only proposes a span. The normalizer decides whether it is real,
so 'v1.2' never becomes money.
"""

from __future__ import annotations

import re

from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.mentions import RawMention
from app.modules.named_entities.normalizers.dates import ORDINAL as _ORDINAL
from app.modules.named_entities.normalizers.money import ISO_4217, SCALE_WORDS
from app.modules.named_entities.text import TextUnit

_MONTH = (
    r"January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
_RANGE_MONTH = rf"(?:{_MONTH})\b\.?"
_RANGE_YEAR = r"(?:,\s*|\s+)\d{4}"
_ISO = r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?"
_TO = r"(?:\s{0,2}[–—‒-]\s{0,2}|\s{1,3}(?:to|through|thru|until|till)\s{1,3})"
# A day that ends a range is not the hour of "Sep 9 – 11:00" or "May 3 – 5 PM", or
# the count of "20-30 minutes".
_LAST_DAY = (
    rf"\d{{1,2}}{_ORDINAL}(?![:.]\d|\s*(?:[ap]\.?m\b\.?|o'?clock\b)"
    rf"|\s+(?:days?|weeks?|months?|years?|hours?|minutes?|mins?|hrs?)\b)"
)
# Tried first, so a range is one span and not its last end alone ("11 September 2025"
# of "9–11 September 2025"). The normalizer fills in what one end leaves out.
_DATE_RANGE = (
    rf"{_ISO}(?:\s{{0,2}}/\s{{0,2}}|{_TO}){_ISO}(?![:.]\d)"
    rf"|{_RANGE_MONTH}\s+\d{{1,2}}{_ORDINAL}(?:{_RANGE_YEAR})?{_TO}(?:{_RANGE_MONTH}\s+)?{_LAST_DAY}(?:{_RANGE_YEAR})?"
    rf"|\d{{1,2}}{_ORDINAL}(?:\s+(?:of\s+)?{_RANGE_MONTH}(?:{_RANGE_YEAR})?)?{_TO}\d{{1,2}}{_ORDINAL}\s+(?:of\s+)?{_RANGE_MONTH}(?:{_RANGE_YEAR})?"
    rf"|{_RANGE_MONTH}(?:\s+\d{{4}})?{_TO}{_RANGE_MONTH}\s+\d{{4}}"
)
_ONE_DATE = (
    rf"Q[1-4]\s*(?:FY\s*)?\d{{2,4}}|FY\s?(?:\d{{4}}|\d{{2}})|(?:{_MONTH})\.?\s+\d{{1,2}}{_ORDINAL}(?:(?:,\s*|\s+)\d{{4}})?|"
    rf"\d{{1,2}}{_ORDINAL}\s+(?:of\s+)?(?:{_MONTH})\b(?:(?:,\s*|\s+)\d{{4}})?|"
    rf"(?:{_MONTH})\.?\s+\d{{4}}|{_ISO}|"
    rf"\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{2,4}}|(?:next|last|this)\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|week|month|quarter|year)|"
    rf"\d+\s+(?:days?|weeks?|months?|years?|hours?|minutes?)|"
    # Short time units; bare "s" and "h" stay out ("1990s", "10s of thousands").
    rf"\d+(?:\.\d+)?\s?(?:ms|msecs?|milliseconds?|secs?|seconds?|mins?|hrs?)"
)
_SINGLE_DATE = re.compile(rf"\b(?:{_ONE_DATE})\b", re.IGNORECASE)
_DATE = re.compile(rf"\b(?:(?P<range>{_DATE_RANGE})|{_ONE_DATE})\b", re.IGNORECASE)
# Codes that are also common words in capitals ("TOP 10", "ALL 100 EMPLOYEES").
_WORD_CODES = frozenset({"ALL", "CUP", "SOS", "TOP"})
_CODES = "|".join(sorted(ISO_4217 - _WORD_CODES))
# Possessive groups and the lookbehinds keep the scan linear: a match starts only
# at the first digit of a run and never backtracks into it. Without them a long
# run of "123 456 ..." costs O(n^2), hours for a numeric CSV.
# A plain space groups thousands only where the currency follows ("1 250 €");
# "$5 100 times" is five dollars.
# Indian grouping ("2,50,000", "12,34,56,789"), tried first: the thousands
# alternative stops at ",50," and the plain one then reads "2,50" as 2.50. Each
# repeat ends in its comma so it cannot eat the last group.
_INDIAN_NUMBER = r"\d{1,2},(?:\d{2},)++\d{3}(?!,\d)(?:\.\d{1,2})?+"
_NUMBER = rf"(?>{_INDIAN_NUMBER}|\d{{1,3}}(?:[,.\u00a0\u202f]\d{{3}})++(?:[.,]\d+)?+|\d++(?:[.,]\d++)?+)"
_SPACED_NUMBER = rf"(?>{_INDIAN_NUMBER}|\d{{1,3}}(?:[,.\u00a0\u202f ]\d{{3}})++(?:[.,]\d+)?+|\d++(?:[.,]\d++)?+)"
_SCALE = rf"(?:\s?(?i:{SCALE_WORDS})\b)?"
# "CAD 3D" and "$5MM" are not an amount followed by more letters, and a number
# cut short of its next group ("₹2,50" of "₹2,50,000,000") is no amount at all.
_END = r"(?![A-Za-z\d]|[.,]\d)"
_MONEY = re.compile(
    rf"(?<![A-Za-z0-9.])(?:"
    rf"(?:US\$|R\$|C\$|A\$|HK\$|NZ\$|S\$|CN¥|RMB|Rs\.?|[$€£¥₹₩₽₺₪₫₱])\s?{_NUMBER}{_SCALE}{_END}"
    rf"|(?:{_CODES})\s?{_NUMBER}{_SCALE}{_END}"
    rf"|(?<![\d,][\s\u00a0])(?<!,){_SPACED_NUMBER}{_SCALE}\s?(?:(?:{_CODES})\b|[€£₹](?!\d)|(?i:dollar|euro|yen|rupee)s?\b))",
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
        if kind in _SIGNED or (kind is EntityKind.DIMENSION and _TEMPERATURE.search(match.group(0))):
            start, end = _with_sign(unit.text, start, end, kind)
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
                if not match.group("range"):
                    self._add(out, unit, _date_kind(match.group(0)), match)
                    continue
                self._add(out, unit, EntityKind.DATE_RANGE, match)
                # A range that does not read ("3-1 May 2025", a score) is dropped
                # later; the dates inside it still count.
                for inner in _SINGLE_DATE.finditer(unit.text, match.start(), match.end()):
                    self._add(out, unit, _date_kind(inner.group(0)), inner)
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
# Only temperatures go below zero; a minus before "5 kg" is a dash.
_TEMPERATURE = re.compile(r"°[CF]|celsius|fahrenheit", re.IGNORECASE)


def _with_sign(text: str, start: int, end: int, kind: EntityKind) -> tuple[int, int]:
    """Take in a minus sign or accounting parentheses: a refund of "-$500" or a
    "($1,200)" loss is not the same amount as a $500 fee. A percentage in
    parentheses is negative only standing alone on its line or in a table cell
    ("(3.2%)"); next to text it is an aside ("Sales tax (CA) (8.25%)", "(8.25%) sales
    tax"). A temperature takes a minus sign only, and a doubled one ("--25 °C") is no sign."""
    if (
        kind is not EntityKind.DIMENSION
        and start > 0 and end < len(text) and text[start - 1] == "(" and text[end] == ")"
        and (kind is not EntityKind.PERCENTAGE or _stands_alone(text, start - 1, end + 1))
    ):
        return start - 1, end + 1
    if start > 0 and text[start - 1] in _MINUS and (
        # "--25" is no sign; "-10–-40" is a range dash before a minus.
        start == 1 or not (text[start - 2].isalnum() or text[start - 2] == text[start - 1])
    ):
        return start - 1, end
    return start, end


_MINUS = "-−\u2012\u2013"
# Cell padding: plain and nonbreaking spaces (tables pasted from documents carry both).
_PADDING = " \u00a0"


def _stands_alone(text: str, start: int, end: int) -> bool:
    """Whether ``text[start:end]`` is alone on its line or in its table cell:
    nothing but padding between it and the line's ends or a cell separator."""
    before = text[:start].rpartition("\n")[2].rstrip(_PADDING)
    after = text[end:].partition("\n")[0].lstrip(_PADDING)
    return (before == "" or before.endswith(("|", "\t"))) and (after == "" or after.startswith(("|", "\t")))


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
