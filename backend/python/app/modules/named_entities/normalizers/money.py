"""Money spans. A bare '$' is USD and flagged ambiguous. No FX conversion."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import NamedTuple

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
    # German: "2,5 Mio. €", "1 Mrd. EUR", "300 Tsd. CHF".
    "tsd": Decimal(10) ** 3,
    "mio": Decimal(10) ** 6,
    "mrd": Decimal(10) ** 9,
}
SCALE_WORDS = r"thousand|million|billion|trillion|lakhs?|lacs?|crores?|mio|mrd|tsd|bn|mn|mm|tn|cr|k|m|b"
# Codes read in lower case after a number ("1500 usd"); codes that are also words
# ("try", "pen", "rub", "top", "cad" drawings) are left out.
LOWER_CODES = r"usd|eur|gbp|inr|aud|chf|jpy|cny|sgd|hkd|nzd|sek|nok|dkk|zar|brl|mxn|aed"
_LOWER_CODE = re.compile(rf"\b(?P<code>{LOWER_CODES})\b")
# The abbreviation's dot goes with it: "2,5 Mrd. €" must not leave "2,5. €".
_SCALE = re.compile(rf"(?<=\d)\s?(?P<scale>{SCALE_WORDS})\.?(?=\s|$|[^\w]|[A-Z]{{3}}\b)", re.IGNORECASE)
# Dashes that are a minus before an amount or a range's "to" between two.
_DASHES = "-−‒–—"
# A minus sign, or accounting parentheses: "-$500", "−€20", "($1,200)".
_NEGATIVE = re.compile(rf"^\s*(?:[{_DASHES}]\s*(?P<rest>.+)|\((?P<paren>.+)\))\s*$", re.DOTALL)
# Beyond this an amount is an identifier or noise, and its float would be inf.
_MAX_DIGITS = 18
_VERSION = re.compile(r"v?\d+(?:\.\d+)+", re.IGNORECASE)
_SYMBOL = "|".join(re.escape(symbol) for symbol in sorted(SYMBOLS, key=len, reverse=True))
_NUM = r"\d[\d.,'’]*"
# Upper-case ISO codes, or the common lower-case ones: never a word such as "per".
_ANY_CODE = rf"(?:(?-i:{'|'.join(sorted(ISO_4217))})|{LOWER_CODES})\b"
_SCALE_WORD = rf"\s?(?:{SCALE_WORDS})\b\.?"
# A range is two bare amounts and nothing else: "$5-10M", "USD 5 to 10M", "500-1000 EUR".
# It needs the scale or the currency written after the high end, as the recognizer
# does, so "USD 555-1234" and "$500 - 20% discount" stay one amount.
_RANGE = re.compile(
    rf"^(?P<lo>(?:(?:{_SYMBOL}|{_ANY_CODE})\s?)?{_NUM}(?:{_SCALE_WORD})?)"
    rf"(?:\s?[{_DASHES}]\s?|\s+to\s+)"
    rf"(?P<hi>(?:{_SYMBOL}\s?)?{_NUM}(?P<scale>{_SCALE_WORD})?(?P<currency>\s?(?:{_ANY_CODE}|[€£₹$]|(?:dollar|euro|yen|rupee)s?\b))?)$",
    re.IGNORECASE,
)
_YEARS = re.compile(rf"^(?:19|20)\d\d\s?(?:[{_DASHES}]|\s+to\s+)\s?(?:19|20)\d\d\b(?!\s?(?:{SCALE_WORDS})\b)", re.IGNORECASE)
# A currency named in a header or label: "Price (USD): 1,250.00" as a row reads it.
_HEADER = re.compile(r"^\((?P<label>[^()]*)\)\s?:\s?(?P<rest>.+)$", re.DOTALL)
_HEADER_CURRENCY = re.compile(rf"^\s*(?:{_ANY_CODE}|{_SYMBOL})\s*$", re.IGNORECASE)
_CODE = re.compile(r"\b(?P<code>[A-Z]{3})\b")


def _currency(text: str, parsed: str | None) -> tuple[str, bool] | None:
    code = _CODE.search(text)
    if code and code.group("code") in ISO_4217:
        return code.group("code"), False
    lower = _LOWER_CODE.search(text)
    if lower:
        return lower.group("code").upper(), False
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

    # price_parser reads "1’250" as 1; the plain apostrophe it groups.
    price = Price.fromstring(text.replace("’", "'"))
    return price.amount, price.currency


class _End(NamedTuple):
    """One end of an amount: its value, its own scale (None without one), the
    currency price_parser read, and the text left once the scale is out."""

    amount: Decimal | None
    scale: Decimal | None
    parsed: str | None
    text: str


def _end(text: str) -> _End:
    if sum(ch.isdigit() for ch in text) > _MAX_DIGITS:
        return _End(None, None, None, text)
    scale = None
    scale_match = _SCALE.search(text)
    if scale_match:
        scale = _SCALES[scale_match.group("scale").casefold().rstrip("s")]
        text = text[: scale_match.start()] + text[scale_match.end():]
    amount, parsed = _amount(text)
    return _End(amount, scale, parsed, text)


def _unheadered(text: str) -> str | None:
    """"(USD): 500" read as "USD 500", its sign kept; None for a header that is
    not just a currency ("(USD m): 5" is in millions)."""
    header = _HEADER.match(text)
    if not header:
        return text
    if not _HEADER_CURRENCY.match(header.group("label")):
        return None
    rest = header.group("rest").strip()
    sign = ""
    negative = _NEGATIVE.match(rest)
    if negative:
        sign, rest = "-", (negative.group("rest") or negative.group("paren") or "").strip()
    return f"{sign}{header.group('label').strip()} {rest}"


def normalize_money(surface: str) -> MoneyValue | None:
    text = _unheadered((surface or "").strip())
    if text is None:
        return None
    sign = Decimal(1)
    negative = _NEGATIVE.match(text)
    if negative:
        sign = Decimal(-1)
        text = (negative.group("rest") or negative.group("paren") or "").strip()
    if not text or _VERSION.fullmatch(text):
        return None
    ends = _RANGE.match(text)
    if ends and not (ends.group("scale") or ends.group("currency")):
        ends = None
    if ends and _YEARS.match(text):
        # "The 2019-2021 USD figures" is a span of years, not an amount.
        return None
    low = _end(ends.group("lo") if ends else text)
    if low.amount is None:
        return None
    scale, parsed, rest = low.scale, low.parsed, low.text
    high = None
    if ends:
        top = _end(ends.group("hi"))
        if top.amount is None:
            return None
        # "$5M - €10M" is two amounts, not one range: both ends name a currency, and not the same.
        low_currency, high_currency = _currency(low.text, low.parsed), _currency(top.text, top.parsed)
        if low_currency and high_currency and low_currency[0] != high_currency[0]:
            return None
        # The low end shares a scale written once: "$5-10M" is 5M to 10M.
        scale = scale or top.scale
        high = abs(top.amount) * (top.scale or 1)
        parsed, rest = parsed or top.parsed, f"{rest} {top.text}"
    amount = abs(low.amount) * (scale or 1)
    if high is not None and amount > high:
        return None
    currency = _currency(rest, parsed)
    if currency is None:
        return None
    code, ambiguous = currency
    if sign < 0 and high is not None:
        # A loss of "$5-10M" runs from -10M to -5M.
        amount, high = -high, -amount
    else:
        amount = amount * sign
    return MoneyValue(
        amount=amount,
        amount_float=float(amount),
        amount_high=high,
        currency=code,
        currency_ambiguous=ambiguous,
    )
