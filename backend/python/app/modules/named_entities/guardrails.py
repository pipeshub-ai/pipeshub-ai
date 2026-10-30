"""Checks on extracted entities: caps applied after extraction, and the name guard
applied as each mention is staged. Rejected items are dropped and counted."""

from __future__ import annotations

import re

from app.modules.named_entities.domain.config import NamedEntityBudgets
from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.domain.models import NamedEntity


def apply_caps(
    entities: list[NamedEntity],
    budgets: NamedEntityBudgets,
    enabled: frozenset[EntityKind],
) -> tuple[list[NamedEntity], int]:
    dropped = 0
    kept: list[NamedEntity] = []
    per_block: dict[int, int] = {}
    for entity in entities:
        if entity.kind not in enabled:
            dropped += 1
            continue
        mentions = []
        for mention in entity.mentions:
            count = per_block.get(mention.block_index, 0)
            if count >= budgets.max_per_block:
                dropped += 1
                continue
            if len(mentions) >= budgets.max_mentions_per_entity:
                dropped += 1
                continue
            per_block[mention.block_index] = count + 1
            mentions.append(mention)
        if not mentions:
            continue
        if len(kept) >= budgets.max_entities:
            dropped += 1
            continue
        entity.mentions = mentions
        entity.display_name = entity.display_name[: budgets.max_text_chars]
        kept.append(entity)
    return kept, dropped


# Kinds that name one specific thing. A common noun ("purchase order"), a
# description ("the company") or an identifier is not such a name.
_PROPER_NAME_KINDS = frozenset({
    EntityKind.PERSON, EntityKind.ORGANIZATION, EntityKind.PRODUCT, EntityKind.EVENT,
    EntityKind.LOCATION, EntityKind.CITY, EntityKind.STATE, EntityKind.COUNTRY_REGION,
})
# Several lower-case words are a description for these kinds ("purchase order",
# "procurement team"); places written in lower case ("new york") are still places,
# and one lower-case word is still a name ("kubernetes", "npm").
_LOWERCASE_PHRASE_KINDS = frozenset({
    EntityKind.ORGANIZATION, EntityKind.PRODUCT, EntityKind.EVENT, EntityKind.LOCATION,
})
# Ticket keys and document numbers are never a person, company or place; a
# product or event may be numbered ("F-150", "COP-2025").
_IDENTIFIER_KINDS = frozenset({EntityKind.PERSON, EntityKind.ORGANIZATION, EntityKind.LOCATION})
_DETERMINERS = frozenset({
    "the", "a", "an", "our", "their", "this", "that", "these", "those", "my", "your", "his", "her", "its",
})
_MAX_NAME_TOKENS = 8
# A postal address runs longer than a name.
_MAX_ADDRESS_TOKENS = 24
_URL = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|www\.)", re.IGNORECASE)
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
# Two path separators, or one plus a lower-case file name: "backend/python/pyproject.toml",
# "src/app.py"; "React/Node.js" is two names, not a path.
_PATH = re.compile(r"^\S*[/\\]\S*[/\\]\S*$|^[^\sA-Z]*[/\\][^\sA-Z]*\.[a-z0-9]{1,5}$")
# A bare file name: one case throughout before a document or source extension
# ("REPORT.md", "issue_reconciler.py"). No .js/.ts: "Node.js", "three.js" are names.
_FILE_NAME = re.compile(
    r"^[\w.-]+\.(?:py|md|txt|csv|json|ya?ml|toml|ini|cfg|log|sql|sh|html?|xml|"
    r"pdf|docx?|xlsx?|pptx?|go|rs|java|rb|php)$",
)
# Ticket keys and document numbers end in three or more digits ("PA-2404",
# "LMS-MSA-2025-006"); model names such as "GPT-4" or "F-35" do not.
_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d{3,}$")


def _is_file_name(text: str) -> bool:
    base = text.rpartition(".")[0]
    return bool(_FILE_NAME.match(text)) and base in (base.lower(), base.upper())


def _is_lowercase(text: str) -> bool:
    # Scripts without case (CJK, Arabic, Devanagari) read as neither.
    return text == text.lower() and text != text.upper()


def name_rejection(kind: EntityKind, surface: str) -> str | None:
    """Why ``surface`` is not a name of ``kind``, or None when it may be.

    Conservative on purpose: it rejects only what is never a name of the kind,
    and leaves the judgement calls to the extractor's kind guide."""
    text = " ".join(surface.split())
    if not any(char.isalpha() for char in text):
        return "no_letters"
    tokens = text.split(" ")
    if len(tokens) > (_MAX_ADDRESS_TOKENS if kind is EntityKind.ADDRESS else _MAX_NAME_TOKENS):
        return "too_long"
    if _URL.match(text) or _EMAIL.match(text) or _UUID.match(text) or _PATH.match(text) or _is_file_name(text):
        return "identifier"
    if kind not in _PROPER_NAME_KINDS:
        return None
    if kind in _IDENTIFIER_KINDS and _IDENTIFIER.match(text):
        return "identifier"
    if len(tokens) > 1 and tokens[0].lower() in _DETERMINERS and _is_lowercase(" ".join(tokens[1:])):
        return "description"
    if len(tokens) > 1 and kind in _LOWERCASE_PHRASE_KINDS and _is_lowercase(text):
        return "description"
    return None
