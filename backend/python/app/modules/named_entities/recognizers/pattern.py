"""Pattern entities: email, URL, IP, phone. Cards, IBANs and national IDs are suppressed."""

from __future__ import annotations

import ipaddress
import re

from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.mentions import RawMention
from app.modules.named_entities.text import TextUnit

# The local part starts only at the start of its run and is possessive, so a long
# "a.a.a..." with no "@" is one linear attempt, not one per dot.
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]++@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)
_URL = re.compile(
    r"\bhttps?://[^\s<>\"']+",
    re.IGNORECASE,
)
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b")
_IPV6 = re.compile(r"\b(?:[A-Fa-f0-9]{0,4}:){2,7}[A-Fa-f0-9]{0,4}\b")
_PHONE = re.compile(
    r"(?<!\d)(?:\+\d{1,3}[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)\d{3}[\s.\-]?\d{4}(?!\d)"
)
# Luhn-shaped card numbers. Detected only so they are never stored.
_CARD = re.compile(r"(?<!\d)(?:\d[ \-]?){13,19}(?!\d)")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{1,4}){3,8}\b")
# National IDs: US SSN, UK NINO, Indian Aadhaar and PAN. Suppressed, never stored.
_NATIONAL_IDS = (
    re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
    re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2} ?\d{2} ?\d{2} ?\d{2} ?[A-D]\b"),
    re.compile(r"(?<!\d)[2-9]\d{3} \d{4} \d{4}(?!\d)"),
    re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
)

_TLD_OK = re.compile(r"\.[A-Za-z]{2,}$")


def _luhn_ok(digits: str) -> bool:
    if not digits.isdigit() or not (13 <= len(digits) <= 19):
        return False
    total = 0
    reverse = digits[::-1]
    for i, ch in enumerate(reverse):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _iban_ok(raw: str) -> bool:
    compact = raw.replace(" ", "")
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    try:
        return int("".join(str(int(ch, 36)) for ch in rearranged)) % 97 == 1
    except ValueError:
        return False


def _spans(pattern: re.Pattern[str], text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(0)) for m in pattern.finditer(text)]


def _suppressed_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for start, end, raw in _spans(_CARD, text):
        digits = re.sub(r"\D", "", raw)
        if _luhn_ok(digits):
            ranges.append((start, end))
    for start, end, raw in _spans(_IBAN, text):
        if _iban_ok(raw):
            ranges.append((start, end))
    for pattern in _NATIONAL_IDS:
        ranges.extend((start, end) for start, end, _raw in _spans(pattern, text))
    return ranges


def _overlaps(start: int, end: int, blocked: list[tuple[int, int]]) -> bool:
    return any(start < b_end and end > b_start for b_start, b_end in blocked)


class PatternRecognizer:
    name = "pattern"

    def __init__(self, enabled: frozenset[EntityKind]) -> None:
        self._enabled = enabled

    def recognize(self, units: list[TextUnit]) -> list[RawMention]:
        found: list[RawMention] = []
        for unit in units:
            blocked = _suppressed_ranges(unit.text)
            found.extend(self._scan(unit, blocked))
        return found

    def _scan(self, unit: TextUnit, blocked: list[tuple[int, int]]) -> list[RawMention]:
        out: list[RawMention] = []

        def add(kind: EntityKind, start: int, end: int, surface: str, score: float = 0.9) -> None:
            if kind not in self._enabled or _overlaps(start, end, blocked):
                return
            out.append(
                RawMention(
                    kind=kind,
                    surface=surface,
                    block_index=unit.block_index,
                    block_id=unit.block_id,
                    char_start=start,
                    char_end=end,
                    extractor="pattern",
                    evidence_score=score,
                )
            )

        if EntityKind.EMAIL in self._enabled:
            for start, end, surface in _spans(_EMAIL, unit.text):
                if _TLD_OK.search(surface):
                    add(EntityKind.EMAIL, start, end, surface, 0.95)
        if EntityKind.URL in self._enabled:
            for start, _end, surface in _spans(_URL, unit.text):
                trimmed = surface.rstrip(".,);")
                add(EntityKind.URL, start, start + len(trimmed), trimmed, 0.9)
        if EntityKind.IP in self._enabled:
            for start, end, surface in _spans(_IPV4, unit.text) + _spans(_IPV6, unit.text):
                try:
                    ipaddress.ip_interface(surface)
                except ValueError:
                    continue
                add(EntityKind.IP, start, end, surface, 0.95)
        if EntityKind.PHONE in self._enabled:
            for start, end, surface in _spans(_PHONE, unit.text):
                digits = re.sub(r"\D", "", surface)
                if 10 <= len(digits) <= 15:
                    add(EntityKind.PHONE, start, end, surface, 0.7)
        return out


def is_suppressed_secret(text: str) -> bool:
    """True when the span is a card, IBAN or national ID and must not be stored."""
    return bool(_suppressed_ranges(text or ""))
