"""Quantities, ages and percentages, converted to a canonical unit."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.modules.named_entities.domain.values import PercentValue, QuantityValue

# (dimension, si unit, multiplier to SI)
_UNITS: dict[str, tuple[str, str, Decimal]] = {
    "mm": ("length", "m", Decimal("0.001")),
    "cm": ("length", "m", Decimal("0.01")),
    "m": ("length", "m", Decimal("1")),
    "km": ("length", "m", Decimal("1000")),
    "ft": ("length", "m", Decimal("0.3048")),
    "mi": ("length", "m", Decimal("1609.344")),
    "g": ("weight", "kg", Decimal("0.001")),
    "kg": ("weight", "kg", Decimal("1")),
    "lb": ("weight", "kg", Decimal("0.45359237")),
    "lbs": ("weight", "kg", Decimal("0.45359237")),
    "kph": ("speed", "m/s", Decimal(1000) / Decimal(3600)),
    "km/h": ("speed", "m/s", Decimal(1000) / Decimal(3600)),
    "mph": ("speed", "m/s", Decimal("0.44704")),
    "c": ("temperature", "K", Decimal("1")),
    "celsius": ("temperature", "K", Decimal("1")),
    "f": ("temperature", "K", Decimal("1")),
    "fahrenheit": ("temperature", "K", Decimal("1")),
    # Decimal prefixes are SI (1 kB = 1000 B); the binary ones are explicit.
    "kb": ("data_size", "B", Decimal(10) ** 3),
    "mb": ("data_size", "B", Decimal(10) ** 6),
    "gb": ("data_size", "B", Decimal(10) ** 9),
    "tb": ("data_size", "B", Decimal(10) ** 12),
    "pb": ("data_size", "B", Decimal(10) ** 15),
    "kib": ("data_size", "B", Decimal(2) ** 10),
    "mib": ("data_size", "B", Decimal(2) ** 20),
    "gib": ("data_size", "B", Decimal(2) ** 30),
    "tib": ("data_size", "B", Decimal(2) ** 40),
}
# Thousands are grouped with commas only; a decimal comma ("1,5 %") is not read.
_N = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_TOKENS = re.compile(rf"^(?P<n>{_N})\s?(?P<scale>[kKmM]?)\s?tokens?$")
_TOKEN_SCALE = {"": Decimal(1), "k": Decimal(10) ** 3, "m": Decimal(10) ** 6}
_QTY = re.compile(
    rf"^(?P<n>{_N})(?P<gap>\s*)(?P<u>km/h|km|m|cm|mm|kg|g|lbs|lb|mi|ft|mph|kph|celsius|fahrenheit|c|f"
    r"|kib|mib|gib|tib|kb|mb|gb|tb|pb)$",
    re.IGNORECASE,
)
_AGE = re.compile(r"^(?P<n>\d{1,3})[\-\s]?years?[\-\s]?old$", re.IGNORECASE)
_PERCENT = re.compile(rf"^(?P<n>{_N})\s*(?P<u>%|bps|percent|per\s?cent|pct)$", re.IGNORECASE)


def normalize_quantity(surface: str) -> QuantityValue | None:
    text = (surface or "").strip().replace("°", "")
    tokens = _TOKENS.match(text)
    if tokens:
        count = Decimal(tokens.group("n").replace(",", "")) * _TOKEN_SCALE[tokens.group("scale").casefold()]
        count = count.quantize(Decimal(1)) if count == count.to_integral_value() else count
        return QuantityValue(value=count, unit="token", dimension="tokens", si_value=float(count), si_unit="token")
    match = _QTY.match(text)
    if not match:
        return None
    unit = match.group("u").casefold()
    if unit == "m" and not match.group("gap"):
        # "15m" is as often minutes or millions as metres; only "15 m" is read as length.
        return None
    spec = _UNITS.get(unit)
    if spec is None:
        return None
    try:
        value = Decimal(match.group("n").replace(",", ""))
    except InvalidOperation:
        return None
    dimension, si_unit, factor = spec
    if unit in {"c", "celsius"}:
        si = float(value + Decimal("273.15"))
    elif unit in {"f", "fahrenheit"}:
        si = float((value - Decimal("32")) * Decimal("5") / Decimal("9") + Decimal("273.15"))
    else:
        si = float(value * factor)
    return QuantityValue(value=value, unit=unit, dimension=dimension, si_value=si, si_unit=si_unit)


def normalize_age(surface: str) -> QuantityValue | None:
    match = _AGE.match((surface or "").strip())
    if not match:
        return None
    value = Decimal(match.group("n"))
    return QuantityValue(
        value=value, unit="year", dimension="age", si_value=float(value), si_unit="year",
    )


def normalize_percent(surface: str) -> PercentValue | None:
    text = (surface or "").strip()
    negative = text[:1] in "-−\u2012\u2013" or (text.startswith("(") and text.endswith(")"))
    match = _PERCENT.match(text.strip("()").lstrip("-−\u2012\u2013 "))
    if not match:
        return None
    number = Decimal(match.group("n").replace(",", "")) * (-1 if negative else 1)
    if match.group("u").casefold() == "bps":
        number = number / Decimal("10000")
    else:
        number = number / Decimal("100")
    return PercentValue(value=number)
