"""The entity caps spend their budget on names first, then everything else in document
order; what is kept stays in document order."""

from __future__ import annotations

from app.modules.named_entities.domain.config import NamedEntityBudgets
from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.domain.models import Mention, NamedEntity
from app.modules.named_entities.guardrails import apply_caps

_ENABLED = frozenset({EntityKind.PERSON, EntityKind.CURRENCY, EntityKind.DATE, EntityKind.EMAIL})


def _entity(kind: EntityKind, name: str, *blocks: int) -> NamedEntity:
    extractor = "single_call" if kind is EntityKind.PERSON else "value"
    return NamedEntity(
        kind=kind, display_name=name, norm_key=name.lower(),
        mentions=[Mention(block_index=b, char_start=0, char_end=len(name), surface=name, extractor=extractor) for b in blocks],
    )


def test_a_table_of_amounts_keeps_the_names_the_model_found_after_it() -> None:
    # A 320-row sheet: the amounts come first in the extraction, the people last.
    amounts = [_entity(EntityKind.CURRENCY, f"USD {i}", i) for i in range(320)]
    people = [_entity(EntityKind.PERSON, name, 400 + i) for i, name in enumerate(["Ana Diaz", "Bo Li", "Cy Oke"])]

    kept, dropped = apply_caps(amounts + people, NamedEntityBudgets(max_entities=300), _ENABLED)

    assert [e.display_name for e in kept if e.kind is EntityKind.PERSON] == ["Ana Diaz", "Bo Li", "Cy Oke"]
    # Document order holds inside a group: the first amounts stay, the last ones go.
    assert [e.display_name for e in kept if e.kind is EntityKind.CURRENCY] == [f"USD {i}" for i in range(297)]
    assert dropped == 23


def test_the_per_block_cap_keeps_a_name_over_a_value_in_the_same_block() -> None:
    budgets = NamedEntityBudgets(max_per_block=2)
    entities = [_entity(EntityKind.CURRENCY, "USD 1", 0), _entity(EntityKind.CURRENCY, "USD 2", 0), _entity(EntityKind.PERSON, "Ana Diaz", 0)]

    kept, dropped = apply_caps(entities, budgets, _ENABLED)

    assert [e.display_name for e in kept] == ["USD 1", "Ana Diaz"] and dropped == 1


def test_values_after_the_names_share_the_cap_in_document_order() -> None:
    # A sheet with a date and an amount per row keeps both kinds, and the header date
    # (first in the document) is kept before any row.
    # Extraction order is longest span first, so every date comes before every amount.
    header = _entity(EntityKind.DATE, "2026-10-30", 0)
    dates = [_entity(EntityKind.DATE, f"2026-01-{i:03}", i) for i in range(1, 200)]
    amounts = [_entity(EntityKind.CURRENCY, f"USD {i}", i) for i in range(1, 200)]

    kept, _ = apply_caps([header, *dates, *amounts], NamedEntityBudgets(max_entities=300), _ENABLED)

    assert kept[0] is header
    # Row by row: the header, then a date and an amount from each row until the cap.
    assert sum(e.kind is EntityKind.DATE for e in kept) == 151
    assert sum(e.kind is EntityKind.CURRENCY for e in kept) == 149


def test_under_the_caps_nothing_is_dropped_and_a_disabled_kind_still_is() -> None:
    entities = [
        _entity(EntityKind.EMAIL, "a@b.co", 0),
        _entity(EntityKind.CURRENCY, "USD 1", 1),
        _entity(EntityKind.PERSON, "Ana Diaz", 2),
        _entity(EntityKind.PHONE, "+1 555 0100", 3),
    ]

    kept, dropped = apply_caps(entities, NamedEntityBudgets(), _ENABLED)

    assert {e.display_name for e in kept} == {"a@b.co", "USD 1", "Ana Diaz"}
    assert dropped == 1
