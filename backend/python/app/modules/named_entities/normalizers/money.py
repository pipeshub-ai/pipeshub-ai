"""Money spans. A bare '$' is USD and flagged ambiguous. No FX conversion."""

from __future__ import annotations

import re
from decimal import Decimal

from app.modules.named_entities.domain.values import MoneyValue

# Active ISO-4217 codes. A code outside this set is not a currency, so
# "ABC 100" stays unrecognized instead of inventing a currency.
ISO_4217: frozenset[str] = frozenset({
    "AED", "AFN", "ALL", "AMD", "ANG", "AOA", "ARS", "AUD", "AWG", "AZN", "BAM", "BBD",
    "BDT", "BGN", "BHD", "BIF", "BMD", "BND", "BOB", "BRL", "BSD", "BTN", "BWP", "BYN",
    "BZD", "CAD", "CDF", "CHF", "CLP", "CNY", "COP", "CRC", "CUP", "CVE", "CZK", "DJF",
    "DKK", "DOP", "DZD", "EGP", "ERN", "ETB", "EUR", "FJD", "FKP", "GBP", "GEL", "GHS",
    "GIP", "GMD", "GNF", "GTQ", "GYD", "HKD", "HNL", "HTG", "HUF", "IDR", "ILS", "INR",
    "IQD", "IRR", "ISK", "JMD", "JOD", "JPY", "KES", "KGS", "KHR", "KMF", "KPW", "KRW",
    "KWD", "KYD", "KZT", "LAK", "LBP", "LKR", "LRD", "LSL", "LYD", "MAD", "MDL", "MGA",
    "MKD", "MMK", "MNT", "MOP", "MRU", "MUR", "MVR", "MWK", "MXN", "MYR", "MZN", "NAD",
    "NGN", "NIO", "NOK", "NPR", "NZD", "OMR", "PAB", "PEN", "PGK", "PHP", "PKR", "PLN",
    "PYG", "QAR", "RON", "RSD", "RUB", "RWF", "SAR", "SBD", "SCR", "SDG", "SEK", "SGD",
    "SHP", "SLE", "SOS", "SRD", "SSP", "STN", "SYP", "SZL", "THB", "TJS", "TMT", "TND",
    "TOP", "TRY", "TTD", "TWD", "TZS", "UAH", "UGX", "USD", "UYU", "UZS", "VES", "VND",
    "VUV", "WST", "XAF", "XCD", "XOF", "XPF", "YER", "ZAR", "ZMW", "ZWL",
})
# '$' and '¥' are shared by several currencies; the default is flagged ambiguous.
SYMBOLS: dict[str, tuple[str, bool]] = {
    "$": ("USD", True),
    "US$": ("USD", False),
    "€": ("EUR", False),
    "£": ("GBP", False),
    "¥": ("JPY", True),
    "₹": ("INR", False),
    "₩": ("KRW", False),
    "₽": ("RUB", False),
    "₺": ("TRY", False),
    "₪": ("ILS", False),
    "₫": ("VND", False),
    "₱": ("PHP", False),
    "R$": ("BRL", False),
    "C$": ("CAD", False),
    "A$": ("AUD", False),
    "HK$": ("HKD", False),
    "NZ$": ("NZD", False),
    "S$": ("SGD", False),
    "CN¥": ("CNY", False),
    "RMB": ("CNY", False),
    "Rs.": ("INR", True),
    "Rs": ("INR", True),
}
# "pounds" is left out: it is as often a weight as a currency.
WORDS: dict[str, tuple[str, bool]] = {
    "dollar": ("USD", True),
    "euro": ("EUR", False),
    "yen": ("JPY", False),
    "rupee": ("INR", True),
}
_WORD = re.compile(r"\b(?P<word>dollar|euro|yen|rupee)s?\b", re.IGNORECASE)
_SCALES = {
    "k": Decimal(10) ** 3,
    "thousand": Decimal(10) ** 3,
    "lakh": Decimal(10) ** 5,
    "lac": Decimal(10) ** 5,
    "m": Decimal(10) ** 6,
    "mm": Decimal(10) ** 6,
    "mn": Decimal(10) ** 6,
    "million": Decimal(10) ** 6,
    "cr": Decimal(10) ** 7,
    "crore": Decimal(10) ** 7,
    "b": Decimal(10) ** 9,
    "bn": Decimal(10) ** 9,
    "billion": Decimal(10) ** 9,
    "tn": Decimal(10) ** 12,
    "trillion": Decimal(10) ** 12,
}
SCALE_WORDS = r"thousand|million|billion|trillion|lakhs?|lacs?|crores?|bn|mn|mm|tn|cr|k|m|b"
_SCALE = re.compile(rf"(?<=\d)\s?(?P<scale>{SCALE_WORDS})(?=\s|$|[^\w]|[A-Z]{{3}}\b)", re.IGNORECASE)
# A minus sign, or accounting parentheses: "-$500", "−€20", "($1,200)".
_NEGATIVE = re.compile(r"^\s*(?:[-−‒–]\s*(?P<rest>.+)|\((?P<paren>.+)\))\s*$", re.DOTALL)
# Beyond this an amount is an identifier or noise, and its float would be inf.
_MAX_DIGITS = 18
_VERSION = re.compile(r"v?\d+(?:\.\d+)+", re.IGNORECASE)
_CODE = re.compile(r"\b(?P<code>[A-Z]{3})\b")


def _currency(text: str, parsed: str | None) -> tuple[str, bool] | None:
    code = _CODE.search(text)
    if code and code.group("code") in ISO_4217:
        return code.group("code"), False
    if parsed:
        upper = parsed.strip().upper()
        if upper in ISO_4217:
            return upper, False
        if parsed.strip() in SYMBOLS:
            return SYMBOLS[parsed.strip()]
    for symbol in sorted(SYMBOLS, key=len, reverse=True):
        if symbol in text:
            return SYMBOLS[symbol]
    word = _WORD.search(text)
    if word:
        return WORDS[word.group("word").casefold()]
    return None


def _amount(text: str) -> tuple[Decimal | None, str | None]:
    from price_parser import Price

    price = Price.fromstring(text)
    return price.amount, price.currency


def normalize_money(surface: str) -> MoneyValue | None:
    text = (surface or "").strip()
    sign = Decimal(1)
    negative = _NEGATIVE.match(text)
    if negative:
        sign = Decimal(-1)
        text = (negative.group("rest") or negative.group("paren") or "").strip()
    if not text or _VERSION.fullmatch(text) or sum(ch.isdigit() for ch in text) > _MAX_DIGITS:
        return None
    scale = Decimal("1")
    scale_match = _SCALE.search(text)
    if scale_match:
        scale = _SCALES[scale_match.group("scale").casefold().rstrip("s")]
        text = text[: scale_match.start()] + text[scale_match.end():]
    amount, parsed_currency = _amount(text)
    if amount is None:
        return None
    amount = abs(amount) * sign
    currency = _currency(text, parsed_currency)
    if currency is None:
        return None
    code, ambiguous = currency
    amount = amount * scale
    return MoneyValue(
        amount=amount,
        amount_float=float(amount),
        currency=code,
        currency_ambiguous=ambiguous,
    )
