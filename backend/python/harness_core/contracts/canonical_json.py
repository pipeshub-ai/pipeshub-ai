"""RFC 8785 JSON Canonicalization Scheme (JCS) and digests over canonical bytes.

Must produce the same bytes as ``agent-harness/contracts/canonical-json.ts``; the shared
golden vectors live in ``contracts/harness/vectors/canonical-json.json``.

JCS does not normalize Unicode: NFC and NFD spellings of the same text are different
strings and produce different bytes and digests. Normalize before hashing if that matters.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import cast

__all__ = [
    "RESERVED_KEY",
    "CanonicalJsonError",
    "assert_no_reserved_keys",
    "canonical_digest",
    "canonical_json",
    "canonical_sha256",
]

_ESCAPES = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_NEEDS_ESCAPE = re.compile(r'["\\\x00-\x1f]')
_SURROGATE = re.compile(r"[\ud800-\udfff]")
_MAX_SAFE = 2**53
# JavaScript object assignment turns a "__proto__" member into a prototype change, so the TS
# side would silently drop a key Python keeps. The contract forbids the key in both languages.
RESERVED_KEY = "__proto__"


class CanonicalJsonError(ValueError):
    """The value has no RFC 8785 representation."""


def canonical_json(value: object) -> bytes:
    """UTF-8 bytes of the RFC 8785 canonical form of a JSON value.

    Accepts ``None``, ``bool``, ``int``, ``float``, ``str``, sequences (not ``str``/``bytes``)
    and mappings with ``str`` keys. Integers beyond 2**53 must be exactly representable as an
    IEEE-754 double, since every JSON number is one under JCS.
    """
    out: list[str] = []
    _write(value, out)
    return "".join(out).encode("utf-8")


def canonical_sha256(value: object) -> str:
    """Hex SHA-256 over the canonical bytes."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def canonical_digest(value: object) -> str:
    """``sha256:<hex>`` digest used by argsDigest, specDigest and descriptor digests."""
    return "sha256:" + canonical_sha256(value)


def assert_no_reserved_keys(value: object) -> None:
    """Raise ``CanonicalJsonError`` if any mapping nested in ``value`` has a ``"__proto__"`` key."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, Mapping):
            mapping = cast("Mapping[object, object]", item)
            if RESERVED_KEY in mapping:
                raise CanonicalJsonError(f"object key {RESERVED_KEY} is reserved")
            stack.extend(mapping.values())
        elif isinstance(item, list | tuple):
            stack.extend(cast("Sequence[object]", item))


def _write(value: object, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        out.append(_format_int(value))
    elif isinstance(value, float):
        out.append(_format_float(value))
    elif isinstance(value, str):
        out.append(_format_string(value))
    elif isinstance(value, Mapping):
        _write_object(cast("Mapping[object, object]", value), out)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        out.append("[")
        for i, item in enumerate(cast("Sequence[object]", value)):
            if i:
                out.append(",")
            _write(item, out)
        out.append("]")
    else:
        raise CanonicalJsonError(f"{type(value).__name__} is not a JSON value")


def _write_object(value: Mapping[object, object], out: list[str]) -> None:
    keys: list[str] = []
    for key in value:
        if not isinstance(key, str):
            raise CanonicalJsonError(f"object key {key!r} is not a string")
        if key == RESERVED_KEY:
            raise CanonicalJsonError(f"object key {RESERVED_KEY} is reserved")
        keys.append(key)
    # UTF-16 code unit order; big-endian UTF-16 bytes compare the same way.
    keys.sort(key=_utf16_key)
    out.append("{")
    for i, key in enumerate(keys):
        if i:
            out.append(",")
        out.append(_format_string(key))
        out.append(":")
        _write(value[key], out)
    out.append("}")


def _utf16_key(key: str) -> bytes:
    if _SURROGATE.search(key):
        raise CanonicalJsonError("lone surrogate in object key")
    return key.encode("utf-16-be")


def _format_string(value: str) -> str:
    if _SURROGATE.search(value):
        raise CanonicalJsonError("lone surrogate in string")
    return '"' + _NEEDS_ESCAPE.sub(_escape, value) + '"'


def _escape(match: re.Match[str]) -> str:
    ch = match.group()
    return _ESCAPES.get(ch) or f"\\u{ord(ch):04x}"


def _format_int(value: int) -> str:
    if -_MAX_SAFE <= value <= _MAX_SAFE:
        return _format_float(float(value))
    as_float = float(value) if abs(value).bit_length() <= 1024 else math.inf
    if as_float != value:
        raise CanonicalJsonError(f"integer {value} is not exactly representable as an IEEE-754 double")
    return _format_float(as_float)


def _format_float(value: float) -> str:
    """ECMAScript Number::toString, which RFC 8785 section 3.2.2.3 mandates."""
    if math.isnan(value) or math.isinf(value):
        raise CanonicalJsonError("NaN and Infinity are not JSON numbers")
    if value == 0:
        return "0"
    if value < 0:
        return "-" + _format_float(-value)
    # repr() is the shortest round-tripping decimal, the same digits ECMAScript picks.
    _sign, digit_tuple, exponent = Decimal(repr(value)).normalize().as_tuple()
    if not isinstance(exponent, int):
        raise CanonicalJsonError(f"{value!r} is not finite")
    digits = "".join(map(str, digit_tuple))
    k = len(digits)
    n = k + exponent
    if k <= n <= 21:
        return digits + "0" * (n - k)
    if 0 < n <= 21:
        return digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return "0." + "0" * -n + digits
    e = n - 1
    mantissa = digits[0] + ("." + digits[1:] if k > 1 else "")
    return f"{mantissa}e{'+' if e >= 0 else '-'}{abs(e)}"
