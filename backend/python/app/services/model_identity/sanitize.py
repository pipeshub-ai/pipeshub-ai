"""Makes values safe to put in an HTTP header, a tag or a claim.

Identity values come from an IdP or from a user-editable profile, so they are
untrusted text on their way into a header. Everything here is conservative: a
value is cleaned, never rejected, so the request still goes out.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

MAX_VALUE_LENGTH = 256
MAX_HEADER_LENGTH = 4096

# RFC 7230 `token`.
_TOKEN_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

# Printable ASCII only: control characters (CR and LF in particular) and
# anything a gateway might read as a different encoding are dropped.
_UNSAFE_CHARS = re.compile(r"[^\x20-\x7e]")

# Headers a forwarding template must never set: they change the framing or the
# destination of the request, or carry the credential.
FORBIDDEN_HEADER_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "host",
        "content-length",
        "content-type",
        "content-encoding",
        "transfer-encoding",
        "connection",
        "upgrade",
        "te",
        "trailer",
        "cookie",
        "set-cookie",
        "x-api-key",
        "api-key",
    }
)


def is_valid_header_name(name: str) -> bool:
    return bool(name) and len(name) <= 128 and _TOKEN_RE.match(name) is not None


def is_forbidden_header_name(name: str) -> bool:
    return name.strip().lower() in FORBIDDEN_HEADER_NAMES


def clean_text(value: object, *, max_length: int = MAX_VALUE_LENGTH) -> str:
    """Printable ASCII, trimmed and capped."""
    if value is None:
        return ""
    text = _UNSAFE_CHARS.sub("", str(value)).strip()
    return text[:max_length]


def clean_tag_value(value: object, *, max_length: int = MAX_VALUE_LENGTH) -> str:
    """``clean_text`` with the comma escaped: tags are a comma-separated list."""
    return clean_text(value, max_length=max_length).replace(",", "%2C")


def clean_header_value(value: object, *, max_length: int = MAX_HEADER_LENGTH) -> str:
    return clean_text(value, max_length=max_length)


def join_capped(values: Iterable[str], *, max_length: int = MAX_HEADER_LENGTH) -> str:
    """Comma-join ``values`` and stop before the header would exceed ``max_length``."""
    out: list[str] = []
    size = 0
    for value in values:
        extra = len(value) + (1 if out else 0)
        if size + extra > max_length:
            break
        out.append(value)
        size += extra
    return ",".join(out)


def optional_clean(value: object | None) -> str | None:
    cleaned = clean_text(value) if value is not None else ""
    return cleaned or None
