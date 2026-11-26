"""Credential shapes that must never cross a gateway, as data in one place.

The tool gateway refuses a call whose arguments carry a ``forbidden`` one
(ARCHITECTURE §3.1 principle 3, §4.6.4: a sandbox must not exfiltrate a vendor
credential through a tool), the egress proxy refuses requests carrying one
(``FORBIDDEN_PATTERN_SOURCES``), and every redactor (the approval card, the
``interaction.explained`` reason, the replay scrubber) masks every shape here.
JWTs and private keys are redacted but not refused: documents and code an
engine legitimately handles contain them, and a refused call there costs more
than the tripwire gains. The generic ``Bearer`` shape is redacted only; the
scrubber's structural ``password=`` and URL heuristics stay with it alone.

A request's model context is the exception (WP-103): a forbidden shape in the
body of an engine's model call is replaced with ``HIDDEN_CREDENTIAL`` and the
call goes on (``hide_forbidden_in``), since public pages and repositories the
run read carry documentation examples of these shapes. The shape
still never leaves; a header, a URL and a tool call's arguments are refused.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.contracts.vectors.token_patterns import (
    REDACT_ONLY_PATTERN_SOURCES,
    SECRET_MASK,
    VENDOR_PATTERN_SOURCES,
)
from harness_core.security.secrets import SECRET_KEY_NAME, active_secrets

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import JsonValue


@dataclass(frozen=True, slots=True)
class TokenPattern:
    kind: str
    pattern: re.Pattern[str]
    # Refused at the gateways and the proxy, not only redacted.
    forbidden: bool = True


# The shapes live in ``contracts/harness/vectors/token-patterns.json``, generated here and into the frontend so the
# approval card masks exactly what the gateways refuse. Their token boundaries are ASCII only: Python's ``\b``
# treats non-ASCII letters as word characters, so a key glued to ``é`` went unseen by the gateways while the egress
# proxy, matching bytes, saw it. Order matters for redaction: the most specific shapes first, so a JWT inside a
# header is reported as a JWT.
VENDOR_TOKEN_PATTERNS: tuple[TokenPattern, ...] = tuple(
    TokenPattern(s.kind, re.compile(s.source), forbidden=s.forbidden) for s in VENDOR_PATTERN_SOURCES
)
# Generic shapes a redactor masks but nothing refuses or scans a workspace for: ordinary documentation carries them.
REDACT_ONLY_PATTERNS: tuple[TokenPattern, ...] = tuple(
    TokenPattern(s.kind, re.compile(s.source), forbidden=False) for s in REDACT_ONLY_PATTERN_SOURCES
)
REDACTION_PATTERNS: tuple[TokenPattern, ...] = VENDOR_TOKEN_PATTERNS + REDACT_ONLY_PATTERNS


# The kind a scrubber's ``mask`` is asked for when an exact resolved value is replaced.
RESOLVED_SECRET = "resolved_secret"

FORBIDDEN_TOKEN_PATTERNS: tuple[TokenPattern, ...] = tuple(p for p in VENDOR_TOKEN_PATTERNS if p.forbidden)
# For a host that matches bytes (the egress proxy compiles these over request bytes).
FORBIDDEN_PATTERN_SOURCES: tuple[str, ...] = tuple(p.pattern.pattern for p in FORBIDDEN_TOKEN_PATTERNS)


def redact_tokens(text: str, mask: Callable[[str], str] = lambda _kind: SECRET_MASK) -> str:
    """``text`` with every redaction shape replaced by ``mask(kind)``; a person sees ``SECRET_MASK``.

    Inside a call (``masking``), the values the effect boundary resolved for it go first, exactly (SEC4).
    """
    exact = active_secrets()
    if exact:
        text = exact.mask(text, mask(RESOLVED_SECRET))
    for p in REDACTION_PATTERNS:
        text = p.pattern.sub(lambda _m, kind=p.kind: mask(kind), text)
    return text


def redact_tokens_in(value: JsonValue) -> JsonValue:
    """``value`` with ``redact_tokens`` applied to every string and object key at any depth."""
    if isinstance(value, str):
        return redact_tokens(value)
    if isinstance(value, Mapping):
        return {redact_tokens(key): redact_tokens_in(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_tokens_in(item) for item in value]
    return value


def redact_secrets_in(value: JsonValue) -> JsonValue:
    """Arguments as another person sees them: a secret-named key's value masked whole, every string by shape."""
    if isinstance(value, Mapping):
        return {
            redact_tokens(key): SECRET_MASK if SECRET_KEY_NAME.search(key) else redact_secrets_in(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets_in(item) for item in value]
    return redact_tokens_in(value)


# WP-103: what an engine's model reads where its context held a forbidden credential shape. Plain ASCII without a
# quote or a backslash, so the egress proxy can put it in a JSON body's bytes and the body stays the same JSON.
HIDDEN_CREDENTIAL = "[credential hidden by PipesHub]"


def hide_forbidden(text: str) -> tuple[str, tuple[str, ...]]:
    """``text`` with every forbidden shape replaced by ``HIDDEN_CREDENTIAL``, and the kinds hidden, in pattern order."""
    hidden: list[str] = []
    for p in FORBIDDEN_TOKEN_PATTERNS:
        text, count = p.pattern.subn(HIDDEN_CREDENTIAL, text)
        if count:
            hidden.append(p.kind)
    return text, tuple(hidden)


def hide_forbidden_in(value: JsonValue) -> tuple[JsonValue, tuple[str, ...]]:
    """``value`` with ``hide_forbidden`` applied to every string and object key at any depth, and the kinds hidden,
    in pattern order, each once."""
    found: set[str] = set()

    def text(item: str) -> str:
        masked, kinds = hide_forbidden(item)
        found.update(kinds)
        return masked

    def walk(item: JsonValue) -> JsonValue:
        if isinstance(item, str):
            return text(item)
        if isinstance(item, Mapping):
            return {text(key): walk(nested) for key, nested in item.items()}
        if isinstance(item, list):
            return [walk(nested) for nested in item]
        return item

    masked = walk(value)
    return masked, tuple(p.kind for p in FORBIDDEN_TOKEN_PATTERNS if p.kind in found)


def find_forbidden(text: str) -> tuple[str, ...]:
    """The kinds of forbidden credential found in ``text``, in pattern order, each once."""
    return tuple(p.kind for p in FORBIDDEN_TOKEN_PATTERNS if p.pattern.search(text))


def find_forbidden_in(value: JsonValue) -> tuple[str, ...]:
    """The forbidden kinds found anywhere in a JSON value: object keys, strings, nested lists."""
    found: set[str] = set()
    stack: list[JsonValue] = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            found.update(find_forbidden(item))
        elif isinstance(item, Mapping):
            for key, nested in item.items():
                found.update(find_forbidden(key))
                stack.append(nested)
        elif isinstance(item, list):
            stack.extend(item)
    return tuple(p.kind for p in FORBIDDEN_TOKEN_PATTERNS if p.kind in found)


__all__ = [
    "FORBIDDEN_PATTERN_SOURCES",
    "FORBIDDEN_TOKEN_PATTERNS",
    "HIDDEN_CREDENTIAL",
    "REDACTION_PATTERNS",
    "REDACT_ONLY_PATTERNS",
    "RESOLVED_SECRET",
    "SECRET_KEY_NAME",
    "SECRET_MASK",
    "VENDOR_TOKEN_PATTERNS",
    "TokenPattern",
    "find_forbidden",
    "find_forbidden_in",
    "hide_forbidden",
    "hide_forbidden_in",
    "redact_secrets_in",
    "redact_tokens",
    "redact_tokens_in",
]
