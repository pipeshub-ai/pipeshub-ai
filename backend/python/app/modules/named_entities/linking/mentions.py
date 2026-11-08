"""The names an agent passes, checked and keyed the way indexing keyed them.

Tool arguments reach the tool unvalidated (``BoundMethodTool.validate`` is a
no-op), so everything here is lenient about shape and strict about meaning: a
mention is a non-empty name and an optional kind this module knows, and its
keys come from ``merge.entity_identity``, never a second normalizer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.modules.named_entities.domain.kinds import (
    ENTITY_TYPE_REGISTRY,
    VALUE_KINDS,
    EntityKind,
    EntitySource,
    interchangeable_kinds,
    kind_family,
)
from app.modules.named_entities.domain.values import ContactValue
from app.modules.named_entities.merge import EntityIdentity, entity_identity
from app.modules.named_entities.normalizers.dates import NormalizationContext
from app.modules.named_entities.recognizers.pattern import is_pattern_surface

MAX_MENTIONS = 5
MAX_NAME_CHARS = 200

# Kinds stored as shared nodes and reachable by name; phone and IP are off by
# default and too sensitive to look up by a guess.
NAME_KINDS: tuple[EntityKind, ...] = (
    *(kind for kind, spec in ENTITY_TYPE_REGISTRY.items() if spec.source is EntitySource.SEMANTIC),
    EntityKind.EMAIL,
    EntityKind.URL,
)
# What an agent may say a name is; an address is still tried when no kind is given.
MENTION_KIND_HINTS: tuple[str, ...] = tuple(kind.value for kind in NAME_KINDS if kind is not EntityKind.ADDRESS)
_CONTEXT = NormalizationContext(tz="UTC")


@dataclass(frozen=True)
class Mention:
    name: str
    kind_hint: EntityKind | None
    keys: tuple[tuple[EntityKind, str], ...]
    """``(kind, norm_key)`` for every kind the name may be stored under."""

    @property
    def kinds(self) -> tuple[EntityKind, ...]:
        return tuple(dict.fromkeys(kind for kind, _ in self.keys))

    def hinted(self, kind: str | EntityKind) -> bool:
        """Whether ``kind`` is what the agent asked for, or one it is filed under
        too (any kind, when it gave none)."""
        if self.kind_hint is None:
            return True
        return str(getattr(kind, "value", kind)) in {k.value for k in kind_family(self.kind_hint)}

    def settles(self, kind: str | EntityKind) -> bool:
        """Whether an exact name match under ``kind`` answers this mention without
        a check: the asked kind or one interchangeable with it (any, with no hint)."""
        if self.kind_hint is None:
            return True
        return str(getattr(kind, "value", kind)) in {k.value for k in interchangeable_kinds(self.kind_hint)}


@dataclass(frozen=True)
class ParsedMentions:
    mentions: list[Mention]
    notes: list[str]


def parse_mentions(raw: Any) -> ParsedMentions:  # noqa: ANN401 - tool arguments, any JSON shape
    """``[{name, kind?}]`` (a bare string is a name) into keyed mentions, with a
    note for each one dropped or changed and why."""
    items = raw if isinstance(raw, list) else ([raw] if raw else [])
    notes: list[str] = []
    mentions: list[Mention] = []
    seen: set[tuple[frozenset[tuple[EntityKind, str]], EntityKind | None]] = set()
    for item in items:
        name, kind_text = _fields(item)
        if not name:
            notes.append("A mention without a name was skipped.")
            continue
        name = name[:MAX_NAME_CHARS]
        hint = _kind(kind_text)
        if kind_text and hint is None:
            if _value_kind(kind_text):
                notes.append(
                    f"'{name}': {kind_text} values are not looked up by name; use "
                    "knowledgegraph__find_records_by_value for dates, amounts and other values."
                )
                continue
            notes.append(f"'{name}': unknown kind '{kind_text}' was ignored.")
        keys = tuple(
            (kind, identity.norm_key)
            for kind in (kind_family(hint) if hint else NAME_KINDS)
            if (identity := _identity(kind, name)) is not None
        )
        if not keys:
            notes.append(f"'{name}' is not a name that can be looked up.")
            continue
        # The hint stays part of it: related kinds share keys but not what settles a match.
        dedupe = (frozenset(keys), hint)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        if len(mentions) == MAX_MENTIONS:
            notes.append(f"Only the first {MAX_MENTIONS} names were looked up.")
            break
        mentions.append(Mention(name=name, kind_hint=hint, keys=keys))
    return ParsedMentions(mentions=mentions, notes=notes)


def _identity(kind: EntityKind, name: str) -> EntityIdentity | None:
    # Indexing stores a contact only where its recognizer found one; anything else
    # under an email or URL key is a lookup of a node that cannot exist.
    if ENTITY_TYPE_REGISTRY[kind].source is EntitySource.PATTERN and not is_pattern_surface(kind, name):
        return None
    identity = entity_identity(kind, name, _CONTEXT)
    if identity is None or (ENTITY_TYPE_REGISTRY[kind].source is EntitySource.PATTERN and not isinstance(identity.value, ContactValue)):
        return None
    return identity


def _fields(item: Any) -> tuple[str, str]:  # noqa: ANN401 - one tool argument
    if isinstance(item, str):
        return item.strip(), ""
    if isinstance(item, dict):
        name = item.get("name")
        kind = item.get("kind")
        return (name.strip() if isinstance(name, str) else ""), (kind.strip().casefold() if isinstance(kind, str) else "")
    return "", ""


def _kind(text: str) -> EntityKind | None:
    try:
        kind = EntityKind(text)
    except ValueError:
        return None
    return kind if kind in NAME_KINDS else None


def _value_kind(text: str) -> bool:
    return any(kind.value == text for kind in VALUE_KINDS)


