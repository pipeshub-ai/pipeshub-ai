"""Short forms of a name that one record also writes in full.

Inside one record, "Maya" next to "Maya Castellanos", "Lumora" next to
"Lumora Systems" or "NL" next to "Northwind Logistics" name the same entity, so the
short form's mentions join the full one instead of becoming a node of their own
(which across records turns into one node shared by every "Maya"). A short form
joins only when exactly one full form in the record fits it; any doubt keeps it
apart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.named_entities.domain.kinds import EntityKind

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.modules.named_entities.domain.models import NamedEntity

# Words that, after a short form, describe the same organization rather than a
# part of it: "Lumora" is "Lumora Systems", but "Google" is not "Google Cloud".
# Trade words that name the business, not a unit: "Pinecrest" is "Pinecrest Hospitality".
_ORGANIZATION_DESIGNATORS = frozenset({
    "holdings", "holding", "group", "systems", "system", "technologies", "technology", "solutions",
    "industries", "international", "global", "enterprises", "enterprise", "partners", "labs",
    "corporation", "company", "worldwide",
    "hospitality", "construction", "utilities", "farms", "foods", "authority", "aviation", "airlines",
    "lines", "transit", "transport", "collective", "property", "properties", "district", "manufacturing",
})
# Trade words that also name a parent's division or spin-off ("Siemens Energy", "GE Capital",
# "Uber Freight"): the short form joins only when the record names the full form first,
# which is how a company is introduced; a parent named first ("Siemens ... spun off
# Siemens Energy") stays apart.
_DIVISION_WORDS = frozenset({
    "energy", "services", "capital", "retail", "logistics", "freight", "consulting", "management",
    "communications", "telecom", "telecommunications", "health", "healthcare", "medical",
})
# A product named without its brand keeps at least this many words: "Edge Pro" is
# "Lumora Edge Pro", but "Teams" is too common to be "Microsoft Teams" alone.
_MIN_UNBRANDED_PRODUCT_WORDS = 2
_MIN_ACRONYM = 2
_MAX_ACRONYM = 6


def link_short_forms(entities: list[NamedEntity]) -> list[NamedEntity]:
    """``entities`` of one record with each unambiguous short form folded into
    its full form (mentions moved, short entity dropped). Order is kept."""
    folded: set[int] = set()
    # Initials first: once "Darshan G" has joined "Darshan Godase", a lone
    # "Darshan" has one full form left to join.
    passes = (
        (EntityKind.PERSON, _person_with_initial),
        (EntityKind.PERSON, _person_full_form),
        (EntityKind.ORGANIZATION, _organization_full_form),
        (EntityKind.PRODUCT, _product_full_form),
    )
    for kind, full_of in passes:
        # Longest first: "Lumora Systems" joins "Lumora Systems Group" before
        # "Lumora" is matched, so the result does not depend on input order.
        candidates = sorted(
            (e for e in entities if e.kind is kind and id(e) not in folded),
            key=lambda e: len(_tokens(e)), reverse=True,
        )
        for entity in candidates:
            target = full_of(entity, [c for c in candidates if c is not entity and id(c) not in folded])
            if target is not None:
                target.mentions.extend(entity.mentions)
                # Document order: the edge keeps only the first blocks a mention is in.
                target.mentions.sort(key=lambda m: (m.block_index, m.char_start))
                folded.add(id(entity))
    return [e for e in entities if id(e) not in folded]


def _first(entity: NamedEntity) -> tuple[int, int]:
    """Where the record first writes it: mentions are not in document order here."""
    return min(((m.block_index, m.char_start) for m in entity.mentions), default=(0, 0))


def _tokens(entity: NamedEntity) -> list[str]:
    return entity.display_name.casefold().split()


def _only(entities: list[NamedEntity], fits: Callable[[NamedEntity], bool]) -> NamedEntity | None:
    matches = [e for e in entities if fits(e)]
    return matches[0] if len(matches) == 1 else None


def _person_with_initial(entity: NamedEntity, others: list[NamedEntity]) -> NamedEntity | None:
    """"Darshan G" or "Darshan G." is the one "Darshan G…" the record names in full."""
    tokens = [t.rstrip(".") for t in _tokens(entity)]
    if len(tokens) != 2 or len(tokens[1]) != 1:
        return None
    first, initial = tokens
    return _only(others, lambda e: len(full := _tokens(e)) > 1 and full[0] == first
                 and len(full[-1]) > 1 and full[-1].startswith(initial))


def _person_full_form(entity: NamedEntity, others: list[NamedEntity]) -> NamedEntity | None:
    tokens = _tokens(entity)
    if len(tokens) != 1:
        return None
    word = tokens[0]
    return _only(others, lambda e: len(full := _tokens(e)) > 1 and word in (full[0], full[-1]))


def _organization_full_form(entity: NamedEntity, others: list[NamedEntity]) -> NamedEntity | None:
    tokens = _tokens(entity)
    if not tokens:
        return None

    def extends(full: NamedEntity) -> bool:
        words = _tokens(full)
        rest = words[len(tokens):]
        if len(words) <= len(tokens) or words[: len(tokens)] != tokens:
            return False
        if all(word in _ORGANIZATION_DESIGNATORS for word in rest):
            return True
        return all(word in _ORGANIZATION_DESIGNATORS or word in _DIVISION_WORDS for word in rest) and _first(full) < _first(entity)

    def abbreviates(full: NamedEntity) -> bool:
        # "P&G" is "Procter & Gamble": the ampersand is not an initial.
        words = [word for word in _tokens(full) if word[0].isalpha()]
        acronym = entity.display_name.replace("&", "")
        return (
            len(words) >= _MIN_ACRONYM
            and _MIN_ACRONYM <= len(acronym) <= _MAX_ACRONYM
            and acronym.isalpha()
            and acronym.isupper()
            and "".join(word[0] for word in words) == acronym.casefold()
        )

    # One target across both rules: "NL" beside "NL Systems" and "Northwind Logistics" stays apart.
    return _only(others, lambda full: extends(full) or abbreviates(full))


def _product_full_form(entity: NamedEntity, others: list[NamedEntity]) -> NamedEntity | None:
    """"Edge Pro" is the one "<brand> Edge Pro" the record names in full."""
    tokens = _tokens(entity)
    if len(tokens) < _MIN_UNBRANDED_PRODUCT_WORDS:
        return None
    return _only(others, lambda full: len(words := _tokens(full)) == len(tokens) + 1 and words[1:] == tokens)
