"""The entity kinds extraction knows about.

A new kind is added here and nowhere else. Recognizers, normalizers and the
graph writer all read ``ENTITY_TYPE_REGISTRY``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EntityCategory(str, Enum):
    PERSON = "Person"
    LOCATION = "Location"
    ORGANIZATION = "Organization"
    EVENT = "Event"
    PRODUCT = "Product"
    SKILL = "Skill"
    DATETIME = "DateTime"
    QUANTITY = "Quantity"
    EMAIL = "Email"
    URL = "URL"
    PHONE = "PhoneNumber"
    IP = "IpAddress"


class EntitySource(str, Enum):
    PATTERN = "pattern"
    VALUE = "value"
    SEMANTIC = "semantic"


class EntityKind(str, Enum):
    PERSON = "person"
    PERSON_TYPE = "person_type"
    ORGANIZATION = "organization"
    EVENT = "event"
    PRODUCT = "product"
    SKILL = "skill"
    LOCATION = "location"
    CITY = "city"
    STATE = "state"
    COUNTRY_REGION = "country_region"
    ADDRESS = "address"
    DATE = "date"
    DATE_RANGE = "date_range"
    DATE_TIME = "date_time"
    DURATION = "duration"
    CURRENCY = "currency"
    PERCENTAGE = "percentage"
    AGE = "age"
    DIMENSION = "dimension"
    EMAIL = "email"
    URL = "url"
    PHONE = "phone"
    IP = "ip"


@dataclass(frozen=True)
class EntityTypeSpec:
    kind: EntityKind
    category: EntityCategory
    tags: tuple[str, ...]
    source: EntitySource
    is_pii: bool
    embeddable: bool
    resolvable: bool
    default_enabled: bool = True
    # The value types a mention of this kind may carry; any other is not this kind.
    value_types: tuple[str, ...] = ()


def _spec(
    kind: EntityKind,
    category: EntityCategory,
    source: EntitySource,
    *,
    tags: tuple[str, ...] = (),
    is_pii: bool = False,
    embeddable: bool = False,
    resolvable: bool = False,
    default_enabled: bool = True,
    value_types: tuple[str, ...] = (),
) -> EntityTypeSpec:
    return EntityTypeSpec(
        kind=kind,
        category=category,
        tags=tags or (category.value,),
        source=source,
        is_pii=is_pii,
        embeddable=embeddable,
        resolvable=resolvable,
        default_enabled=default_enabled,
        value_types=value_types or _DEFAULT_VALUE_TYPES[source],
    )


_DEFAULT_VALUE_TYPES = {
    EntitySource.SEMANTIC: ("name",),
    EntitySource.PATTERN: ("contact",),
    EntitySource.VALUE: (),
}
_SEMANTIC = EntitySource.SEMANTIC
_VALUE = EntitySource.VALUE
_PATTERN = EntitySource.PATTERN

ENTITY_TYPE_REGISTRY: dict[EntityKind, EntityTypeSpec] = {
    EntityKind.PERSON: _spec(EntityKind.PERSON, EntityCategory.PERSON, _SEMANTIC, is_pii=True, embeddable=True, resolvable=True),
    EntityKind.PERSON_TYPE: _spec(EntityKind.PERSON_TYPE, EntityCategory.PERSON, _SEMANTIC, embeddable=True, resolvable=True),
    EntityKind.ORGANIZATION: _spec(EntityKind.ORGANIZATION, EntityCategory.ORGANIZATION, _SEMANTIC, embeddable=True, resolvable=True),
    EntityKind.EVENT: _spec(EntityKind.EVENT, EntityCategory.EVENT, _SEMANTIC, embeddable=True, resolvable=True),
    EntityKind.PRODUCT: _spec(EntityKind.PRODUCT, EntityCategory.PRODUCT, _SEMANTIC, embeddable=True, resolvable=True),
    EntityKind.SKILL: _spec(EntityKind.SKILL, EntityCategory.SKILL, _SEMANTIC, embeddable=True, resolvable=True),
    EntityKind.LOCATION: _spec(EntityKind.LOCATION, EntityCategory.LOCATION, _SEMANTIC, tags=("Location",), embeddable=True, resolvable=True),
    EntityKind.CITY: _spec(EntityKind.CITY, EntityCategory.LOCATION, _SEMANTIC, tags=("Location", "City"), embeddable=True, resolvable=True),
    EntityKind.STATE: _spec(EntityKind.STATE, EntityCategory.LOCATION, _SEMANTIC, tags=("Location", "State"), embeddable=True, resolvable=True),
    EntityKind.COUNTRY_REGION: _spec(EntityKind.COUNTRY_REGION, EntityCategory.LOCATION, _SEMANTIC, tags=("Location", "CountryRegion"), embeddable=True, resolvable=True),
    EntityKind.ADDRESS: _spec(EntityKind.ADDRESS, EntityCategory.LOCATION, _SEMANTIC, tags=("Location", "Address"), is_pii=True, embeddable=True, resolvable=True),
    EntityKind.DATE: _spec(EntityKind.DATE, EntityCategory.DATETIME, _VALUE, value_types=("date_range",)),
    EntityKind.DATE_RANGE: _spec(EntityKind.DATE_RANGE, EntityCategory.DATETIME, _VALUE, value_types=("date_range",)),
    EntityKind.DATE_TIME: _spec(EntityKind.DATE_TIME, EntityCategory.DATETIME, _VALUE, value_types=("date_range",)),
    EntityKind.DURATION: _spec(EntityKind.DURATION, EntityCategory.DATETIME, _VALUE, value_types=("duration",)),
    EntityKind.CURRENCY: _spec(EntityKind.CURRENCY, EntityCategory.QUANTITY, _VALUE, value_types=("money",)),
    EntityKind.PERCENTAGE: _spec(EntityKind.PERCENTAGE, EntityCategory.QUANTITY, _VALUE, value_types=("percent",)),
    EntityKind.AGE: _spec(EntityKind.AGE, EntityCategory.QUANTITY, _VALUE, value_types=("quantity",)),
    EntityKind.DIMENSION: _spec(EntityKind.DIMENSION, EntityCategory.QUANTITY, _VALUE, value_types=("quantity",)),
    EntityKind.EMAIL: _spec(EntityKind.EMAIL, EntityCategory.EMAIL, _PATTERN, is_pii=True),
    EntityKind.URL: _spec(EntityKind.URL, EntityCategory.URL, _PATTERN),
    EntityKind.PHONE: _spec(EntityKind.PHONE, EntityCategory.PHONE, _PATTERN, is_pii=True, default_enabled=False),
    EntityKind.IP: _spec(EntityKind.IP, EntityCategory.IP, _PATTERN, is_pii=True, default_enabled=False),
}

# Typed values (dates, amounts, quantities…) are stored per record, not as shared
# nodes: a value such as "2026" or "USD" would be a hub every record write locks.
VALUE_KINDS: frozenset[EntityKind] = frozenset(
    kind for kind, spec in ENTITY_TYPE_REGISTRY.items() if spec.source is EntitySource.VALUE
)

# Kinds whose entities get a vector point; PII is never embedded.
VECTOR_KINDS: frozenset[EntityKind] = frozenset(
    kind for kind, spec in ENTITY_TYPE_REGISTRY.items() if spec.embeddable and not spec.is_pii
)


def spec_for(kind: EntityKind | str) -> EntityTypeSpec | None:
    try:
        parsed = kind if isinstance(kind, EntityKind) else EntityKind(str(kind).casefold())
    except ValueError:
        return None
    return ENTITY_TYPE_REGISTRY.get(parsed)


def default_enabled_kinds() -> frozenset[EntityKind]:
    return frozenset(kind for kind, spec in ENTITY_TYPE_REGISTRY.items() if spec.default_enabled)


def semantic_kinds(enabled: frozenset[EntityKind]) -> frozenset[EntityKind]:
    return frozenset(
        kind for kind in enabled if ENTITY_TYPE_REGISTRY[kind].source is EntitySource.SEMANTIC
    )
