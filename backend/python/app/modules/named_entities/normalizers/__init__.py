"""Turn a surface form into a typed value. Failure returns None, never a guess."""

from __future__ import annotations

from app.modules.named_entities.domain.kinds import EntityKind, EntitySource, spec_for
from app.modules.named_entities.domain.values import TypedValue
from app.modules.named_entities.normalizers.contacts import normalize_contact
from app.modules.named_entities.normalizers.dates import (
    NormalizationContext,
    normalize_temporal,
)
from app.modules.named_entities.normalizers.money import normalize_money
from app.modules.named_entities.normalizers.names import name_value
from app.modules.named_entities.normalizers.quantities import (
    normalize_age,
    normalize_percent,
    normalize_quantity,
)

_TEMPORAL = {EntityKind.DATE, EntityKind.DATE_RANGE, EntityKind.DATE_TIME, EntityKind.DURATION}


def normalize_value(kind: EntityKind, surface: str, ctx: NormalizationContext) -> TypedValue | None:
    """The value stated by the grounded text. A model's hint never sets it: the
    text is the evidence, and a hint can name a value the document does not."""
    value = _parse(kind, (surface or "").strip(), ctx)
    spec = spec_for(kind)
    if value is None or spec is None or value.value_type not in spec.value_types:
        return None
    return value


def _parse(kind: EntityKind, surface: str, ctx: NormalizationContext) -> TypedValue | None:
    if kind in _TEMPORAL:
        return normalize_temporal(kind, surface, ctx)
    if kind is EntityKind.CURRENCY:
        return normalize_money(surface)
    if kind is EntityKind.PERCENTAGE:
        return normalize_percent(surface)
    if kind is EntityKind.AGE:
        return normalize_age(surface)
    if kind is EntityKind.DIMENSION:
        return normalize_quantity(surface)
    spec = spec_for(kind)
    if spec is not None and spec.source is EntitySource.PATTERN:
        return normalize_contact(kind, surface)
    if spec is not None and spec.source is EntitySource.SEMANTIC:
        return name_value(surface, organization=kind is EntityKind.ORGANIZATION)
    return None
