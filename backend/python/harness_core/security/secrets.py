"""The exact secret values a call resolved, masked wherever that call's output can surface (SEC4).

The token vector masks credentials by shape and by key name; a client secret, a password or a custom header value
that a client echoes in an error message has neither. The effect boundary resolved that value itself, so it masks
it exactly: ``CredentialLeases`` adds each resolved record to its holder's ``ResolvedSecrets``, ``EffectRunner`` runs a call inside
``masking(source)``, and the shared redactor (``redact_tokens``) masks ``active_secrets()`` before the vector shapes.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, cast, override

from harness_core.contracts.vectors.token_patterns import (
    SECRET_KEY_NAME_FLAGS,
    SECRET_KEY_NAME_SOURCE,
    SECRET_MASK,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

# A key named like a secret holds one whatever its value looks like (a pasted password, a short key).
SECRET_KEY_NAME: re.Pattern[str] = re.compile(SECRET_KEY_NAME_SOURCE, re.IGNORECASE if "i" in SECRET_KEY_NAME_FLAGS else 0)

# Shorter values are words a record also holds as metadata ("Bearer", "OAUTH"): masking them exactly would mask
# ordinary text. The vector still masks a short value under a secret-named key.
MIN_SECRET_CHARS = 8


def _values_in(material: object, extra_keys: Collection[str], *, named: bool) -> Iterator[str]:
    if isinstance(material, str):
        if named and len(material) >= MIN_SECRET_CHARS:
            yield material
    elif isinstance(material, Mapping):
        for key, item in cast("Mapping[object, object]", material).items():
            key_named = isinstance(key, str) and (SECRET_KEY_NAME.search(key) is not None or key in extra_keys)
            # A record nested under a secret-named key ("credentials") keeps plain fields (an expiry, a scope).
            record = isinstance(item, Mapping)
            yield from _values_in(item, extra_keys, named=key_named and not record)
    elif isinstance(material, list | tuple):
        for item in cast("list[object] | tuple[object, ...]", material):
            yield from _values_in(item, extra_keys, named=named)


@dataclass(frozen=True, slots=True)
class SecretSet:
    """Values masked exactly; ``repr`` names their count, never a value."""

    _values: frozenset[str] = field(default=frozenset(), repr=False)
    _pattern: re.Pattern[str] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._values and self._pattern is None:
            # Longest first, so a value that contains another is masked whole.
            ordered = sorted(self._values, key=lambda value: (-len(value), value))
            object.__setattr__(self, "_pattern", re.compile("|".join(re.escape(value) for value in ordered)))

    @classmethod
    def of(cls, material: object, extra_keys: Collection[str] = ()) -> SecretSet:
        """The secrets in a resolved record: a bare string, and strings under a secret-named key or ``extra_keys``."""
        return cls(frozenset(_values_in(material, frozenset(extra_keys), named=True)))

    def __or__(self, other: SecretSet) -> SecretSet:
        if not other._values or other._values <= self._values:
            return self
        if not self._values:
            return other
        return SecretSet(self._values | other._values)

    def __len__(self) -> int:
        return len(self._values)

    def __bool__(self) -> bool:
        return bool(self._values)

    @override
    def __repr__(self) -> str:
        return f"SecretSet({len(self._values)} values)"

    __str__ = __repr__

    def resolved_secrets(self) -> SecretSet:
        return self

    def mask(self, text: str, replacement: str = SECRET_MASK) -> str:
        return text if self._pattern is None else self._pattern.sub(replacement, text)

    def mask_in(self, value: JsonValue) -> JsonValue:
        """``value`` with every string and object key masked, at any depth."""
        if self._pattern is None:
            return value
        if isinstance(value, str):
            return self.mask(value)
        if isinstance(value, Mapping):
            return {self.mask(key): self.mask_in(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.mask_in(item) for item in value]
        return value


EMPTY_SECRETS = SecretSet()


class CallSecrets(Protocol):
    """What resolved secrets for the call in flight; read when masking, so a value resolved mid-call counts."""

    def resolved_secrets(self) -> SecretSet: ...


NO_SECRETS: CallSecrets = EMPTY_SECRETS


class ResolvedSecrets:
    """The values one holder's resolutions returned (an attempt's ``ToolCredentials``), kept until ``clear``.

    Not dropped when a handle lapses: a client created from the record still holds the value and may still echo it.
    """

    def __init__(self, extra_keys: Collection[str] = ()) -> None:
        self._extra_keys = frozenset(extra_keys)
        self._secrets = EMPTY_SECRETS

    def add(self, material: object) -> None:
        self._secrets = self._secrets | SecretSet.of(material, self._extra_keys)

    def clear(self) -> None:
        self._secrets = EMPTY_SECRETS

    def resolved_secrets(self) -> SecretSet:
        return self._secrets

    @override
    def __repr__(self) -> str:
        return f"ResolvedSecrets({len(self._secrets)} values)"

_ACTIVE: ContextVar[CallSecrets | None] = ContextVar("harness_call_secrets", default=None)


@contextmanager
def masking(source: CallSecrets) -> Iterator[None]:
    """Run a call, and every task it starts, with ``source``'s values masked by the shared redactor."""
    token = _ACTIVE.set(source)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def active_secrets() -> SecretSet:
    source = _ACTIVE.get()
    return EMPTY_SECRETS if source is None else source.resolved_secrets()


__all__ = [
    "EMPTY_SECRETS",
    "MIN_SECRET_CHARS",
    "NO_SECRETS",
    "SECRET_KEY_NAME",
    "ResolvedSecrets",
    "SecretSet",
    "CallSecrets",
    "active_secrets",
    "masking",
]
