"""Canary scan (QA-14): planted secrets and private text, looked for on every surface a run writes to.

A *secret* (a ``pipeshub-canary-<uuid>`` a fake resolver issued, or a vendor-shaped token) may reach the effect
boundary and nothing else, so it is a leak on any surface. *Private* text (a person's document or message) belongs
on that person's own surfaces (their journal content, their stream) and is a leak on an operator's (logs, spans
without content capture, metrics, audit). Shapes come from the shared token vector, so a token the run made up
itself is found as surely as one the test planted.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import urllib.parse
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast, override

from pydantic import BaseModel

from harness_core.journal.inline import INLINE_REF_PREFIX
from harness_core.security.token_patterns import REDACTION_PATTERNS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
    from uuid import UUID

SECRET_CANARY_PREFIX = "pipeshub-canary-"
PRIVATE_CANARY_PREFIX = "pipeshub-private-"
_MAX_DEPTH = 40


def secret_canary(uuid4: Callable[[], UUID]) -> str:
    return f"{SECRET_CANARY_PREFIX}{uuid4().hex}"


def private_canary(uuid4: Callable[[], UUID]) -> str:
    return f"{PRIVATE_CANARY_PREFIX}{uuid4().hex}"


@dataclass(frozen=True, slots=True)
class Canaries:
    secrets: tuple[str, ...]
    private: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Surface:
    name: str
    value: object
    # The person's own view: their private text may be here, a secret still may not.
    owner_view: bool = False


@dataclass(frozen=True, slots=True)
class Leak:
    surface: str
    kind: str
    # Enough to find it, never the whole secret: a failing test's output is a log too.
    excerpt: str

    @override
    def __str__(self) -> str:
        return f"{self.surface}: {self.kind} ({self.excerpt}...)"


def strings_in(value: object) -> Iterator[str]:
    """Every string reachable from ``value``: containers, dataclasses, models, objects' attributes, bytes."""
    yield from _walk(value, {}, 0)


def _walk(value: object, seen: dict[int, object], depth: int) -> Iterator[str]:
    if depth > _MAX_DEPTH:
        return
    if isinstance(value, str):
        yield value
        if value.startswith(INLINE_REF_PREFIX):
            # Small payloads ride in their ref as base64: a scan of the ref text alone would never see them.
            yield _inline_text(value)
        return
    if isinstance(value, bytes | bytearray):
        yield bytes(value).decode("utf-8", "replace")
        return
    if value is None or isinstance(value, int | float | bool) or isinstance(value, type):
        return
    if id(value) in seen:
        return
    # Held, not just numbered: a model's dump is a temporary, and a freed one's id is reused by the next.
    seen[id(value)] = value
    if isinstance(value, Mapping):
        for key, item in cast("Mapping[object, object]", value).items():
            yield from _walk(key, seen, depth + 1)
            yield from _walk(item, seen, depth + 1)
    elif isinstance(value, Collection):
        # Any sized container (a deque of events, a set, a dict view); never a one-shot iterator.
        for item in cast("Collection[object]", value):
            yield from _walk(item, seen, depth + 1)
    elif isinstance(value, BaseModel):
        yield from _walk(value.model_dump(mode="python"), seen, depth + 1)
    elif dataclasses.is_dataclass(value):
        for f in dataclasses.fields(value):
            yield from _walk(getattr(value, f.name, None), seen, depth + 1)
    elif hasattr(value, "__dict__"):
        yield from _walk(vars(value), seen, depth + 1)
    else:
        slots = [s for cls in type(value).__mro__ for s in getattr(cls, "__slots__", ())]
        if slots:
            for name in slots:
                yield from _walk(getattr(value, name, None), seen, depth + 1)
        else:
            yield repr(value)


def _inline_text(ref: str) -> str:
    try:
        return base64.urlsafe_b64decode(ref.removeprefix(INLINE_REF_PREFIX)).decode("utf-8", "replace")
    except ValueError:
        return ""


def scan(surface: Surface, canaries: Canaries) -> list[Leak]:
    leaks: list[Leak] = []
    private = () if surface.owner_view else canaries.private
    for text in strings_in(surface.value):
        leaks.extend(Leak(surface.name, "secret", s[:12]) for s in canaries.secrets if s in text)
        leaks.extend(Leak(surface.name, "private", p[:12]) for p in private if p in text)
        for p in REDACTION_PATTERNS:
            leaks.extend(Leak(surface.name, p.kind, m.group(0)[:8]) for m in p.pattern.finditer(text))
    return leaks


def encoded_forms(secret: str) -> dict[str, str]:
    """``secret`` as a copy of it may be written: raw, base64 (standard and URL-safe, at each of the three byte
    offsets a longer encoded value can start it at), percent-encoded and hex. A base64 form keeps only the characters
    that do not depend on the bytes around it, so the secret is found inside a longer encoded string (an
    ``Authorization: Basic`` value, a JWT body, a config file's base64 blob)."""
    raw = secret.encode()
    forms = {"raw": secret, "url": urllib.parse.quote(secret, safe=""), "url_plus": urllib.parse.quote_plus(secret),
             "hex": binascii.hexlify(raw).decode(), "HEX": binascii.hexlify(raw).decode().upper()}
    for offset in range(3):
        encoded = base64.b64encode(b"\0" * offset + raw).decode().rstrip("=")
        # The first characters carry the padding bytes' bits, the last one the bits of whatever follows.
        start = (offset * 8 + 5) // 6
        stable = encoded[start:-1] if (offset + len(raw)) % 3 else encoded[start:]
        forms[f"base64.{offset}"] = stable
        forms[f"base64url.{offset}"] = stable.replace("+", "-").replace("/", "_")
    # A secret of safe characters percent-encodes to itself: one form is enough.
    return {kind: value for kind, value in forms.items() if kind == "raw" or value != secret}


def find_encoded(text: str, secrets: Iterable[str]) -> list[tuple[str, str]]:
    """``(secret, form)`` for each secret found in ``text`` in any of its ``encoded_forms``."""
    return [(s, kind) for s in secrets if s for kind, value in encoded_forms(s).items() if value and value in text]


def assert_no_canary(surfaces: Iterable[Surface], canaries: Canaries) -> None:
    leaks = [leak for surface in surfaces for leak in scan(surface, canaries)]
    assert not leaks, "canary found:\n" + "\n".join(sorted({str(leak) for leak in leaks}))


__all__ = [
    "PRIVATE_CANARY_PREFIX",
    "SECRET_CANARY_PREFIX",
    "Canaries",
    "Leak",
    "Surface",
    "assert_no_canary",
    "encoded_forms",
    "find_encoded",
    "private_canary",
    "scan",
    "secret_canary",
    "strings_in",
]
