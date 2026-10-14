"""Display names and the deterministic norm_key each kind is stored under."""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal

from app.modules.entity_resolution.normalizer import display_form, normalize_name
from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.domain.values import (
    ContactValue,
    DateRangeValue,
    DurationValue,
    MoneyValue,
    NameValue,
    PercentValue,
    QuantityValue,
    TypedValue,
)

# Legal forms across the jurisdictions customers write in. "Pty Ltd" and
# "GmbH & Co. KG" are several forms in a row, so they are stripped to a fixed point.
_LEGAL_SUFFIX = re.compile(
    r"\b(?:incorporated|inc|llc|l\.l\.c|ltd|limited|gmbh|corp|corporation|co|company"
    r"|ag|se|plc|llp|lp|s\.?a|n\.?v|b\.?v|pty|pvt|private|k\.?k|oyj?|ab"
    r"|s\.?p\.?a|sas|s\.?a\.?r\.?l|s\.?r\.?l|sdn|bhd|kg|kgaa)\b\.?$",
    re.IGNORECASE,
)
_TRAILING_CONNECTOR = re.compile(r"(?:\s*(?:&|\+|\band\b))+$", re.IGNORECASE)


_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'", "`": "'", "\u00b4": "'"})
_ARTICLES = frozenset({"the", "a", "an"})


def canonical_name(surface: str, *, organization: bool = False) -> str:
    # Typographic apostrophes are one character to a reader: "O’Neil" is "O'Neil".
    display = display_form(surface.translate(_APOSTROPHES))
    return _without_legal_form(display) if organization else display


def _without_legal_form(name: str) -> str:
    """Idempotent, so the display and the key (computed from the display) agree."""
    while True:
        # "Bain & Co" loses "Co" and must not keep the dangling "&".
        stripped = _TRAILING_CONNECTOR.sub("", _LEGAL_SUFFIX.sub("", name).rstrip(" ,.")).strip(" ,.")
        # "The Limited" is a name, not "The" plus a suffix, and "AG" alone is a name.
        if not stripped or stripped.casefold() in _ARTICLES or stripped == name:
            return name
        name = display_form(stripped)


def _number(value: Decimal | float) -> str:
    # One key per quantity: "$1,250" and "USD 1,250.00" are the same entity.
    exact = value if isinstance(value, Decimal) else Decimal(repr(float(value)))
    return format(exact.normalize(), "f")


def _significant(value: float) -> Decimal:
    # An SI value is the product of float arithmetic, so its last digits are noise
    # (and a different noise per platform); 12 digits keeps identity stable.
    return Decimal(f"{float(value):.12g}")


def name_value(surface: str, *, organization: bool = False) -> NameValue:
    return NameValue(canonical=canonical_name(surface, organization=organization))


# Neo4j caps an index key near 8 KB; a longer key could not be written at all.
_MAX_KEY_BYTES = 512


def norm_key_for(kind: EntityKind, display: str, value: TypedValue | None) -> str:
    key = _norm_key(kind, display, value)
    if len(key.encode()) <= _MAX_KEY_BYTES:
        return key
    scheme = key.split(":", 1)[0]
    return f"{scheme}:sha256:{hashlib.sha256(key.encode()).hexdigest()}"


def _norm_key(kind: EntityKind, display: str, value: TypedValue | None) -> str:
    if isinstance(value, DateRangeValue):
        if value.ambiguous or value.start_ms is None:
            return f"date:unresolved:{value.timex or normalize_name(display)}"
        return f"date:{value.granularity}:{value.start_ms}:{value.end_ms}"
    if isinstance(value, DurationValue):
        return f"duration:{value.iso}"
    if isinstance(value, MoneyValue):
        return f"money:{value.currency}:{_number(value.amount)}"
    if isinstance(value, PercentValue):
        return f"pct:{_number(value.value)}"
    if isinstance(value, QuantityValue):
        return f"qty:{value.dimension}:{_number(_significant(value.si_value))}"
    if isinstance(value, ContactValue):
        return f"{value.scheme}:{value.canonical}"
    key = normalize_name(canonical_name(display, organization=kind is EntityKind.ORGANIZATION))
    return f"name:{kind.value}:{key}"
