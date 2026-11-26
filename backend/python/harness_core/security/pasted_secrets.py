"""Secrets a person pastes into a message (SEC-CR-07, G-37), found by the shared vector's pasted-input rules.

Node enforces the rules on every message before it is kept; the frontend runs them to warn before send. This is
the same detector for Python callers, held to the same vector examples and negatives, so the three cannot drift.
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter
from dataclasses import dataclass

from harness_core.contracts.vectors.token_patterns import (
    PASTED_INPUT_RULES,
    REDACT_ONLY_PATTERN_SOURCES,
    SECRET_MASK,
    VENDOR_PATTERN_SOURCES,
)


@dataclass(frozen=True, slots=True)
class PastedSecret:
    """Where a secret sits in a message; ``repr`` never holds the value."""

    kind: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _Shape:
    kind: str
    pattern: re.Pattern[str]
    group: int
    check_value: bool


def _flags(flags: str) -> int:
    return re.IGNORECASE if "i" in flags else 0


_RULES = PASTED_INPUT_RULES
_SHAPES: tuple[_Shape, ...] = (
    *(_Shape(s.kind, re.compile(s.source), 0, False) for s in (*VENDOR_PATTERN_SOURCES, *REDACT_ONLY_PATTERN_SOURCES)),
    *(
        _Shape(d["kind"], re.compile(d["source"]), int(d["secretGroup"]), bool(d["checkValue"]))
        for d in _RULES["detectors"]
    ),
    _Shape(
        _RULES["assigned"]["kind"],
        re.compile(_RULES["assigned"]["source"], _flags(_RULES["assigned"]["flags"])),
        int(_RULES["assigned"]["secretGroup"]),
        True,
    ),
)
_PLACEHOLDER = re.compile(_RULES["placeholders"], _flags(_RULES["placeholderFlags"]))
_IDENTIFIER_PATH = re.compile(_RULES["identifierPath"])
_CONTEXT = _RULES["contextual"]
_KEYWORD = re.compile(_CONTEXT["keyword"], _flags(_CONTEXT["keywordFlags"]))
_WORD = re.compile(_CONTEXT["word"])
_SEPARATORS = re.compile(_CONTEXT["separators"])
_TRAILING = re.compile(_CONTEXT["trailing"])
_HEX_ONLY = re.compile(r"^[0-9A-Fa-f-]+$")
_IDENTIFIER_SYMBOLS = frozenset("_-.")


def _classes(value: str) -> int:
    lower = upper = digit = symbol = False
    for ch in value:
        if "a" <= ch <= "z":
            lower = True
        elif "A" <= ch <= "Z":
            upper = True
        elif "0" <= ch <= "9":
            digit = True
        elif ch not in _IDENTIFIER_SYMBOLS:
            symbol = True
    return lower + upper + digit + symbol


def _plausible(value: str) -> bool:
    return (
        _PLACEHOLDER.search(value) is None
        and _IDENTIFIER_PATH.search(value) is None
        and _classes(value) >= int(_RULES["minClasses"])
    )


def _bits_per_char(value: str) -> float:
    n = len(value)
    return -sum(count / n * math.log2(count / n) for count in Counter(value).values())


def _contextual(text: str) -> list[PastedSecret]:
    found: list[PastedSecret] = []
    offset = 0
    for line in text.split("\n"):
        if _KEYWORD.search(line):
            cursor = 0
            for raw in _SEPARATORS.split(line):
                start = line.find(raw, cursor) if raw else cursor
                cursor = start + len(raw)
                word = _TRAILING.sub("", raw)
                if not word or "://" in word or _HEX_ONLY.match(word) or not _WORD.match(word):
                    continue
                if _plausible(word) and _bits_per_char(word) >= float(_CONTEXT["minBits"]):
                    found.append(PastedSecret(_CONTEXT["kind"], offset + start, offset + start + len(word)))
        offset += len(line) + 1
    return found


class PastedSecretScanTimeout(RuntimeError):
    """A scan ran past its caller's deadline (SEC-C-06). Every vector pattern is linear (the pattern-budget cohesion
    test holds them to it), so this is the net for a pattern that regresses, not a path real text takes."""


def _check(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise PastedSecretScanTimeout("the pasted-secret scan ran past its time budget")


def find_pasted_secrets(text: str, *, deadline: float | None = None) -> tuple[PastedSecret, ...]:
    """Every secret in ``text``, overlapping finds merged, in order of position.

    ``deadline``: a ``time.monotonic()`` past which the scan raises ``PastedSecretScanTimeout``."""
    found: list[PastedSecret] = []
    for shape in _SHAPES:
        _check(deadline)
        for match in shape.pattern.finditer(text):
            value = match.group(shape.group)
            if value and (not shape.check_value or _plausible(value)):
                found.append(PastedSecret(shape.kind, match.start(shape.group), match.end(shape.group)))
    _check(deadline)
    found.extend(_contextual(text))
    merged: list[PastedSecret] = []
    for item in sorted(found, key=lambda f: (f.start, -f.end)):
        if merged and item.start < merged[-1].end:
            last = merged[-1]
            if item.end > last.end:
                merged[-1] = PastedSecret(last.kind, last.start, item.end)
            continue
        merged.append(item)
    return tuple(merged)


def redact_pasted_secrets(
    text: str, mask: str = SECRET_MASK, *, deadline: float | None = None
) -> tuple[str, tuple[PastedSecret, ...]]:
    """``text`` with every pasted secret replaced by ``mask``, and what was found (positions in the original)."""
    found = find_pasted_secrets(text, deadline=deadline)
    if not found:
        return text, found
    parts: list[str] = []
    cursor = 0
    for item in found:
        parts.append(text[cursor : item.start])
        parts.append(mask)
        cursor = item.end
    parts.append(text[cursor:])
    return "".join(parts), found


# Past a cut, how much more is scanned so a secret that starts before it is still whole when matched: longer than
# any one secret shape (a 16384-bit RSA key block is about 12 KiB).
REDACT_MARGIN_CHARS = 64 * 1024


def redact_prefix(text: str, limit: int, mask: str = SECRET_MASK) -> tuple[str, bool]:
    """The first ``limit`` characters of ``text`` as shown after redaction, and whether anything was cut.

    Only ``limit`` plus a margin is scanned, so a megabyte that will be cut to a few KiB costs a few KiB of scan
    (SEC-C-06); the margin keeps a secret that straddles the cut whole, so its shown head is masked too."""
    window = text[: limit + REDACT_MARGIN_CHARS]
    redacted, _ = redact_pasted_secrets(window, mask)
    shown = redacted[:limit]
    return shown, len(shown) < len(redacted) or len(window) < len(text)


__all__ = [
    "REDACT_MARGIN_CHARS",
    "PastedSecret",
    "PastedSecretScanTimeout",
    "find_pasted_secrets",
    "redact_pasted_secrets",
    "redact_prefix",
]
