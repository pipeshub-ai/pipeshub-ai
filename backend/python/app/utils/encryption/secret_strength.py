"""Whether a secret is random enough to write under (SEC-D-05), shared by every component that takes one.

Stdlib only: the legacy Docker API proxy checks its caller credential with it and starts without the app's
dependencies. The cases both languages agree on are ``contracts/secret-key/strength-cases.json``.
"""

from __future__ import annotations

import base64
import binascii
import math
import re
from collections import Counter
from typing import Literal

MIN_SECRET_KEY_LENGTH = 32
# Shannon entropy of the characters themselves: random hex is about 3.8, random base64 about 5.
_MIN_BITS_PER_CHAR = 3.0
_PLACEHOLDERS = frozenset(
    {"yourrandomencryptionsecretkey", "changeme", "secret", "secretkey", "password", "pipeshub", "default",
     "example", "test", "admin"}
)
_PLACEHOLDER_STEMS = ("yourrandomencryptionsecretkey",)
WeakReason = Literal["whitespace", "placeholder", "short", "low_entropy", "pattern", "encoded_text"]
_REASON_TEXT: dict[str, str] = {
    "whitespace": "starts or ends with a space or line break",
    "placeholder": "is a published placeholder",
    "short": f"is shorter than {MIN_SECRET_KEY_LENGTH} characters",
    "low_entropy": "repeats too few characters to be random",
    "pattern": "is a repeated or sequential pattern",
    "encoded_text": "is readable text encoded as hex or base64, not random bytes",
}


def weak_secret_reason(secret: str) -> WeakReason | None:
    """Why ``secret`` cannot be a key anything is written under, or ``None``. Never logs or returns the value."""
    if secret != secret.strip():
        return "whitespace"
    folded = re.sub(r"[^a-z0-9]", "", secret.lower())
    if folded in _PLACEHOLDERS or any(stem in folded for stem in _PLACEHOLDER_STEMS):
        return "placeholder"
    if len(secret) < MIN_SECRET_KEY_LENGTH:
        return "short"
    if _bits_per_char(secret) < _MIN_BITS_PER_CHAR:
        return "low_entropy"
    if _periodic(secret) or _sequential(secret):
        return "pattern"
    decoded = _decoded(secret)
    if decoded is not None and _bits_per_char(decoded.decode("latin-1")) < _MIN_BITS_PER_CHAR:
        return "low_entropy"
    if decoded is not None and all(0x20 <= byte < 0x7F for byte in decoded):
        return "encoded_text"
    return None


def _bits_per_char(text: str) -> float:
    counts = Counter(text)
    total = len(text)
    return -sum(n / total * math.log2(n / total) for n in counts.values()) if total else 0.0


def _periodic(text: str) -> bool:
    return any(len(text) % size == 0 and text == text[:size] * (len(text) // size) for size in range(1, len(text) // 2 + 1))


def _sequential(text: str) -> bool:
    steps = [ord(b) - ord(a) for a, b in zip(text, text[1:], strict=False)]
    return sum(1 for step in steps if step in (1, -1)) * 2 > len(steps)


def _decoded(text: str) -> bytes | None:
    """The bytes ``text`` encodes as hex or base64 (16 or more), else ``None``."""
    try:
        if re.fullmatch(r"(?:[0-9a-fA-F]{2}){16,}", text):
            return binascii.unhexlify(text)
        if re.fullmatch(r"[A-Za-z0-9+/_-]{22,}={0,2}", text):
            padded = text.rstrip("=")
            raw = base64.b64decode(padded.replace("-", "+").replace("_", "/") + "=" * (-len(padded) % 4))
            return raw if len(raw) >= 16 else None
    except (binascii.Error, ValueError):
        return None
    return None


__all__ = ["MIN_SECRET_KEY_LENGTH", "WeakReason", "weak_secret_reason"]
