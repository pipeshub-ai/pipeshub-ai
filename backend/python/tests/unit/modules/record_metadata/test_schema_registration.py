"""Registration rules: a name keeps its type; facetable needs a closed or per-item type and a visible value."""

import pytest

from app.models.record_metadata import MetadataSchema, PropertyDefinition, PropertyType
from app.modules.record_metadata.schemas import SchemaRuleError, check_new_schema

from .projection_world import defs, registry

pytestmark = pytest.mark.unit


async def _versions(*versions: list[PropertyDefinition]) -> tuple[MetadataSchema, ...]:
    reg, _ = registry()
    for properties in versions:
        await reg.register("org-1", "kb-1", properties)
    return await reg.versions("org-1", "kb-1")


async def test_versions_are_listed_oldest_first() -> None:
    found = await _versions(defs(("a", PropertyType.STRING)), defs(("b", PropertyType.STRING)))
    assert [v.schema_id.rsplit(":", 1)[1] for v in found] == ["1", "2"]


async def test_type_is_immutable() -> None:
    previous = await _versions(defs(("score", PropertyType.STRING)))
    with pytest.raises(SchemaRuleError) as err:
        check_new_schema(previous, defs(("score", PropertyType.INT)))
    assert err.value.code == "PROPERTY_TYPE_IMMUTABLE" and err.value.property == "score"


async def test_a_type_declared_in_any_earlier_version_binds() -> None:
    previous = await _versions(defs(("score", PropertyType.STRING)), defs(("other", PropertyType.STRING)))
    with pytest.raises(SchemaRuleError):
        check_new_schema(previous, defs(("score", PropertyType.FLOAT)))


@pytest.mark.parametrize("ptype", [PropertyType.STRING, PropertyType.INT, PropertyType.FLOAT, PropertyType.DATETIME])
def test_facetable_is_for_enum_bool_and_string_lists_only(ptype: PropertyType) -> None:
    with pytest.raises(SchemaRuleError) as err:
        check_new_schema([], defs(("p", ptype), facetable=True))
    assert err.value.code == "PROPERTY_FACET_TYPE"


@pytest.mark.parametrize("ptype", [PropertyType.ENUM, PropertyType.BOOL, PropertyType.STRING_LIST])
def test_facetable_types_are_accepted(ptype: PropertyType) -> None:
    check_new_schema([], defs(("p", ptype), facetable=True))


def test_facetable_needs_retrievable() -> None:
    with pytest.raises(SchemaRuleError) as err:
        check_new_schema([], defs(("p", PropertyType.BOOL), facetable=True, retrievable=False))
    assert err.value.code == "PROPERTY_FACET_RETRIEVABLE"


async def test_an_already_registered_schema_is_tolerated_and_registering_it_again_is_a_lookup() -> None:
    """P3-08 accepted a facetable STRING and a type change. Those versions stay readable, and putting the
    same set again (a rollback, a second worker) is not refused by rules that came later."""
    legacy_a = defs(("score", PropertyType.STRING), facetable=True)
    legacy_b = defs(("score", PropertyType.INT))
    previous = await _versions(legacy_a, legacy_b)
    assert len(previous) == 2
    check_new_schema(previous, legacy_a)
    check_new_schema(previous, legacy_b)


async def test_a_new_property_beside_a_tolerated_one_is_checked() -> None:
    previous = await _versions(defs(("score", PropertyType.STRING), facetable=True))
    with pytest.raises(SchemaRuleError):
        check_new_schema(previous, defs(("score", PropertyType.STRING), facetable=True) + defs(("x", PropertyType.INT), facetable=True))
