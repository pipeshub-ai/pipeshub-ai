"""Check that graph entities match integration-test expectations using YAML rules.

Loads field definitions from ``integration-tests/validation/schemas/`` (same engine as
``response_validation``). Steps: (1) merge models to JSON, (2) validate both against the schema,
(3) compare each field for equality except names listed in the default skip set (and any extras
you pass for that entity kind).

For entities with well-known structural edges (records, record groups), the async
``assert_graph_entity_with_edges`` convenience function validates both fields AND
edges in a single call — deriving edge expectations automatically from the actual
entity's properties.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from helper.graph_provider import GraphProviderProtocol

# So ``response_validator`` imports work when this package is on PYTHONPATH.
_RV_HELPER = Path(__file__).resolve().parents[1] / "response-validation" / "helper"
if str(_RV_HELPER) not in sys.path:
    sys.path.insert(0, str(_RV_HELPER))

from response_validator import (  # noqa: E402
    ResponseSchema,
    assert_response_matches_schema,
    load_yaml_schema,
)
from validation.graph_edge_validator import (  # noqa: E402
    assert_graph_edges,
    build_record_edge_expectations,
    build_record_group_edge_expectations,
    build_user_edge_expectations,
)

_SCHEMA_DIR: Final = Path(__file__).resolve().parent / "schemas"

# Each value is YAML file(s); later files add fields (e.g. ticket_record = base record + ticket fields).
_ENTITY_SCHEMA_LAYERS: Final[dict[str, tuple[str, ...]]] = {
    "ticket_record": ("record.yaml", "ticket_record.yaml"),
    "file_record": ("record.yaml", "file_record.yaml"),
    "link_record": ("record.yaml", "link_record.yaml"),
    "webpage_record": ("record.yaml", "webpage_record.yaml"),
    "record_group": ("record_group.yaml",),
    "app_user_group": ("app_user_group.yaml",),
    "app_role": ("app_role.yaml",),
    "app_metadata": ("app_metadata.yaml",),
}

GraphEntityKind = Literal[  # ``entity`` values accepted by ``assert_graph_entity_matches``.
    "ticket_record",
    "file_record",
    "link_record",
    "webpage_record",
    "record_group",
    "app_user_group",
    "app_role",
    "app_metadata",
]

_DEFAULT_SKIP_COMPARE_BY_ENTITY: Final[dict[str, frozenset[str]]] = {
    # Things integration tests often cannot know ahead of time or that change after sync.
    # parent_record_type is used only at write-time for edge creation, not persisted on the record.
    "ticket_record": frozenset({"id", "org_id", "indexing_status", "record_group_id", "virtual_record_id", "parent_record_type"}),
    "file_record": frozenset({"id", "org_id", "indexing_status", "record_group_id", "virtual_record_id", "parent_record_type"}),
    "link_record": frozenset({"id", "org_id", "indexing_status", "record_group_id", "virtual_record_id", "parent_record_type"}),
    "webpage_record": frozenset({"id", "org_id", "indexing_status", "record_group_id", "virtual_record_id", "parent_record_type"}),
    "record_group": frozenset({"id", "org_id"}),
    "app_user_group": frozenset({"id", "org_id"}),
    "app_role": frozenset({"id", "org_id"}),
    "app_metadata": frozenset(),
}


def _field_values_equal(key: str, expected: object, actual: object) -> bool:
    """Return True if expected and actual match for this field (special case for empty semantic_metadata)."""
    if key == "semantic_metadata" and expected in (None, {}) and actual in (None, {}):
        return True
    return expected == actual


def _merge_schemas(base: ResponseSchema, layer: ResponseSchema) -> ResponseSchema:
    """Join two YAML schemas: all fields from ``base``, overwritten by ``layer``."""
    fields = dict(base.fields)
    fields.update(layer.fields)
    return ResponseSchema(
        name=f"{base.name}+{layer.name}",
        description="",
        fields=fields,
    )


def merged_graph_entity_schema(entity: GraphEntityKind) -> ResponseSchema:
    """Return the merged YAML schema for this ``entity`` (one or more YAML files from ``schemas/``)."""
    try:
        layers = _ENTITY_SCHEMA_LAYERS[entity]
    except KeyError as e:
        raise ValueError(f"unknown entity: {entity!r}") from e
    merged = load_yaml_schema(_SCHEMA_DIR / layers[0])
    for layer_name in layers[1:]:
        merged = _merge_schemas(merged, load_yaml_schema(_SCHEMA_DIR / layer_name))
    return merged


def assert_graph_entity_matches(
    expected: BaseModel,
    actual: BaseModel,
    *,
    entity: GraphEntityKind,
    skip_compare: frozenset[str] | None = None,
) -> None:
    """Assert ``expected`` and ``actual`` match for one graph entity kind.

    Runs ``model_dump(mode="json")`` on both, checks each passes the YAML schema for ``entity``,
    then requires every schema field to match except those in the default skip list for that kind
    plus any names in ``skip_compare`` (the two skip lists are combined).
    """
    schema = merged_graph_entity_schema(entity)
    exp = expected.model_dump(mode="json")
    act = actual.model_dump(mode="json")
    assert_response_matches_schema(exp, schema)
    assert_response_matches_schema(act, schema)

    skip = _DEFAULT_SKIP_COMPARE_BY_ENTITY[entity] | (skip_compare or frozenset())
    mismatches: list[str] = []
    for key in schema.fields:
        if key in skip:
            continue
        if not _field_values_equal(key, exp.get(key), act.get(key)):
            mismatches.append(
                f"  - {key!r}: expected {exp.get(key)!r}, actual {act.get(key)!r}",
            )
    if mismatches:
        joined = "\n".join(mismatches)
        raise AssertionError(
            f"Entity field mismatch for entity={entity!r} (schema {schema.name!r}):\n{joined}",
        )


# ---------------------------------------------------------------------------
# Combined entity + edge validation
# ---------------------------------------------------------------------------

_ENTITY_KINDS_WITH_RECORD_EDGES: Final[frozenset[str]] = frozenset({
    "ticket_record", "file_record", "link_record", "webpage_record",
})


async def assert_graph_entity_with_edges(
    expected: BaseModel,
    actual: BaseModel,
    *,
    entity: GraphEntityKind,
    connector_id: str,
    graph_provider: "GraphProviderProtocol",
    skip_compare: frozenset[str] | None = None,
    parent_relation_type: str | None = None,
) -> None:
    """Validate entity fields AND structural edges in one call.

    Performs ``assert_graph_entity_matches`` first (field comparison), then
    automatically builds and asserts the expected edges based on entity kind:

    - ``ticket_record`` / ``file_record`` → record edges (belongsTo, inheritPermissions,
      isOfType, parent recordRelations)
    - ``record_group`` → record group edges (belongsTo app, parent group hierarchy)
    - Other kinds → fields-only (no automatic edges)
    """
    assert_graph_entity_matches(
        expected, actual, entity=entity, skip_compare=skip_compare,
    )

    if entity in _ENTITY_KINDS_WITH_RECORD_EDGES:
        edges = build_record_edge_expectations(
            actual, connector_id, parent_relation_type=parent_relation_type,
        )
        await assert_graph_edges(graph_provider, edges)
    elif entity == "record_group":
        edges = build_record_group_edge_expectations(actual, connector_id)
        await assert_graph_edges(graph_provider, edges)


async def assert_user_app_edge(
    user_source_id: str,
    *,
    connector_id: str,
    graph_provider: "GraphProviderProtocol",
) -> None:
    """Assert the ``userAppRelation`` edge exists from a synced user to the app."""
    edges = build_user_edge_expectations(user_source_id, connector_id)
    await assert_graph_edges(graph_provider, edges)
