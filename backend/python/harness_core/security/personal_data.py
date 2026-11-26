"""Personal-data shapes an org's policy redacts from what a tool returns (TG-12, OWD-9 ``allow_with_redaction``).

Credentials are ``token_patterns``' job; this adds what is personal rather than secret: e-mail addresses, phone
numbers, payment card numbers, US social security numbers and IBANs. Each shape is narrow on purpose: a redaction
that eats dates, ids or prices makes the result useless, so a card number must pass the Luhn check and a phone
number must be written as one (a leading ``+`` or separators between its groups).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from harness_core.security.token_patterns import SECRET_MASK, redact_tokens

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import JsonValue

_EMAIL: Final = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,24}\b")
_CARD: Final = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")
_SSN: Final = re.compile(r"(?<![\d-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\d-])")
_IBAN: Final = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")
# International (+CC ...) or grouped national numbers: 9 to 15 digits with separators between groups.
_PHONE: Final = re.compile(
    r"(?<![\w+])(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?|\d{2,4}[ .-])\d{2,4}[ .-]\d{2,4}(?:[ .-]\d{2,4})?(?![\w-])"
)
_PHONE_PLUS: Final = re.compile(r"(?<![\w+])\+\d{9,15}(?!\d)")


def _luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _digits(text: str) -> str:
    return "".join(c for c in text if c.isdigit())


def _card(match: re.Match[str]) -> bool:
    digits = _digits(match.group())
    return 13 <= len(digits) <= 19 and _luhn(digits)


def _phone(match: re.Match[str]) -> bool:
    return 9 <= len(_digits(match.group())) <= 15


@dataclass(frozen=True, slots=True)
class PersonalShape:
    kind: str
    pattern: re.Pattern[str]
    accept: Callable[[re.Match[str]], bool] | None = None


# Order matters: a card number is masked before the phone shape could take part of it.
PERSONAL_SHAPES: Final[tuple[PersonalShape, ...]] = (
    PersonalShape("email", _EMAIL),
    PersonalShape("iban", _IBAN),
    PersonalShape("payment_card", _CARD, _card),
    PersonalShape("ssn", _SSN),
    PersonalShape("phone", _PHONE_PLUS, _phone),
    PersonalShape("phone", _PHONE, _phone),
)


def mask_for(kind: str) -> str:
    return f"[redacted {kind.replace('_', ' ')}]"


def redact_personal(text: str, found: set[str] | None = None) -> str:
    """``text`` with credentials (``redact_tokens``) and every personal shape masked; ``found`` collects the kinds."""
    masked = redact_tokens(text, lambda _kind: _note(found, "secret", SECRET_MASK))
    for shape in PERSONAL_SHAPES:

        def replace(match: re.Match[str], shape: PersonalShape = shape) -> str:
            if shape.accept is not None and not shape.accept(match):
                return match.group()
            return _note(found, shape.kind, mask_for(shape.kind))

        masked = shape.pattern.sub(replace, masked)
    return masked


def _note(found: set[str] | None, kind: str, mask: str) -> str:
    if found is not None:
        found.add(kind)
    return mask


def redact_personal_in(value: JsonValue, found: set[str] | None = None) -> JsonValue:
    """``redact_personal`` over every string of a JSON value (object keys stay: they are the tool's field names)."""
    if isinstance(value, str):
        return redact_personal(value, found)
    if isinstance(value, Mapping):
        return {key: redact_personal_in(item, found) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_personal_in(item, found) for item in value]
    return value


__all__ = ["PERSONAL_SHAPES", "PersonalShape", "mask_for", "redact_personal", "redact_personal_in"]
