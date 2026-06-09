"""Declarative graph-edge validation for integration tests (step 3).

Builds on step 1-2 entity validation: after a node is validated, callers produce
``EdgeExpectation`` objects (via profile builders) and feed them to
``assert_graph_edges`` which resolves endpoints, fetches edges, validates
cardinality, YAML shape, and payload values.

All edge collection names use Arango conventions (camelCase).  The Neo4j
provider translates internally.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

if TYPE_CHECKING:
    from helper.graph_provider import GraphProviderProtocol

_RV_HELPER = Path(__file__).resolve().parents[1] / "response-validation" / "helper"
if str(_RV_HELPER) not in sys.path:
    sys.path.insert(0, str(_RV_HELPER))

from response_validator import (  # noqa: E402
    assert_response_matches_schema,
    load_yaml_schema,
)

logger = logging.getLogger("test-edge-validator")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EDGE_SCHEMA_DIR: Final = Path(__file__).resolve().parent / "schemas" / "edges"

_NODE_KIND_TO_COLLECTION: Final[dict[str, str]] = {
    "user": "users",
    "group": "groups",
    "role": "roles",
    "org": "organizations",
    "record": "records",
    "record_group": "recordGroups",
    "app": "apps",
}

_EDGE_COLLECTION_TO_YAML: Final[dict[str, str]] = {
    "permission": "permission.yaml",
    "belongsTo": "belongs_to.yaml",
    "inheritPermissions": "inherit_permissions.yaml",
    "recordRelations": "record_relations.yaml",
    "isOfType": "is_of_type.yaml",
    "userAppRelation": "user_app_relation.yaml",
    "entityRelations": "entity_relations.yaml",
}

_EDGE_DEFAULT_SKIP: Final[frozenset[str]] = frozenset({
    "createdAtTimestamp",
    "updatedAtTimestamp",
})

_ARANGO_SYSTEM_KEYS: Final[frozenset[str]] = frozenset({
    "_key", "_id", "_rev", "_from", "_to",
})

NodeKind = Literal[
    "user", "group", "role", "org", "record", "record_group", "app",
]

Cardinality = Literal["exactly_one", "at_least_one", "none"]

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeRef:
    """Identifies a graph vertex for edge lookups."""

    kind: NodeKind | None = None
    collection: str | None = None
    connector_id: str | None = None
    external_id: str | None = None
    internal_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind is None and self.collection is None:
            raise ValueError("NodeRef requires either kind or collection")


@dataclass(frozen=True)
class EdgeExpectation:
    """One directed edge to find and validate."""

    collection: str
    from_ref: NodeRef
    to_ref: NodeRef
    cardinality: Cardinality = "exactly_one"
    expected_payload: dict[str, Any] | None = None
    skip_compare: frozenset[str] = field(default_factory=lambda: _EDGE_DEFAULT_SKIP)
    label: str = ""


@dataclass(frozen=True)
class PermissionExpectation:
    """Input for ``build_permission_expectations``."""

    from_kind: NodeKind
    from_external_id: str
    to_kind: NodeKind
    to_external_id: str
    connector_id: str
    entity_type: str
    role: str


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_edge_doc(raw: dict[str, Any]) -> dict[str, Any]:
    """Strip Arango system fields so the dict is ready for YAML validation."""
    return {k: v for k, v in raw.items() if k not in _ARANGO_SYSTEM_KEYS}


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


async def _resolve_node_ref(
    graph: GraphProviderProtocol,
    ref: NodeRef,
    cache: dict[tuple[str | None, str | None, str | None], tuple[str, str]],
) -> tuple[str, str]:
    """Return ``(collection, internal_key)`` for *ref*, using *cache* to avoid repeated lookups."""
    cache_key = (ref.kind, ref.external_id, ref.connector_id)
    if cache_key in cache:
        return cache[cache_key]

    collection = ref.collection or (_NODE_KIND_TO_COLLECTION.get(ref.kind) if ref.kind else None)
    if collection is None:
        raise ValueError(f"Cannot derive collection from NodeRef: {ref}")

    if ref.internal_id is not None:
        result = (collection, ref.internal_id)
        cache[cache_key] = result
        return result

    if ref.external_id is None:
        raise ValueError(f"NodeRef has neither internal_id nor external_id: {ref}")

    cid = ref.connector_id
    if cid is None:
        raise ValueError(f"external_id lookup requires connector_id: {ref}")

    internal_id: str | None = None
    if ref.kind == "record":
        entity = await graph.get_record_by_external_id(cid, ref.external_id)
        internal_id = entity.id if entity else None
    elif ref.kind == "record_group":
        entity = await graph.get_record_group_by_external_id(cid, ref.external_id)
        internal_id = entity.id if entity else None
    elif ref.kind == "user":
        entity = await graph.get_user_by_source_id(ref.external_id, cid)
        internal_id = entity.id if entity else None
    elif ref.kind == "group":
        entity = await graph.get_user_group_by_external_id(cid, ref.external_id)
        internal_id = entity.id if entity else None
    elif ref.kind == "role":
        entity = await graph.get_app_role_by_external_id(cid, ref.external_id)
        internal_id = entity.id if entity else None
    elif ref.kind == "app":
        internal_id = cid
    elif ref.kind == "org":
        raise ValueError("org NodeRef must set internal_id directly")
    else:
        raise ValueError(f"Unsupported kind for external lookup: {ref.kind!r}")

    if internal_id is None:
        raise LookupError(
            f"Could not resolve NodeRef to internal id: kind={ref.kind!r}, "
            f"external_id={ref.external_id!r}, connector_id={cid!r}"
        )

    result = (collection, internal_id)
    cache[cache_key] = result
    return result


# ---------------------------------------------------------------------------
# Core assertion
# ---------------------------------------------------------------------------


async def assert_graph_edges(
    graph: GraphProviderProtocol,
    expectations: list[EdgeExpectation],
) -> None:
    """Validate every ``EdgeExpectation`` against the live graph."""
    cache: dict[tuple[str | None, str | None, str | None], tuple[str, str]] = {}
    errors: list[str] = []

    for exp in expectations:
        tag = exp.label or f"{exp.collection} {exp.from_ref.kind}->{exp.to_ref.kind}"
        try:
            from_coll, from_key = await _resolve_node_ref(graph, exp.from_ref, cache)
            to_coll, to_key = await _resolve_node_ref(graph, exp.to_ref, cache)
        except (LookupError, ValueError) as exc:
            errors.append(f"[{tag}] resolve failed: {exc}")
            continue

        raw_edges = await graph.find_edges_between(
            from_coll, from_key, to_coll, to_key, exp.collection,
        )
        count = len(raw_edges)

        if exp.cardinality == "exactly_one" and count != 1:
            errors.append(
                f"[{tag}] expected exactly 1 edge in {exp.collection!r} "
                f"from {from_coll}/{from_key} -> {to_coll}/{to_key}, found {count}"
            )
            continue
        if exp.cardinality == "at_least_one" and count < 1:
            errors.append(
                f"[{tag}] expected at least 1 edge in {exp.collection!r} "
                f"from {from_coll}/{from_key} -> {to_coll}/{to_key}, found 0"
            )
            continue
        if exp.cardinality == "none":
            if count != 0:
                errors.append(
                    f"[{tag}] expected NO edge in {exp.collection!r} "
                    f"from {from_coll}/{from_key} -> {to_coll}/{to_key}, found {count}"
                )
            continue

        edge = normalize_edge_doc(raw_edges[0])

        yaml_file = _EDGE_COLLECTION_TO_YAML.get(exp.collection)
        if yaml_file:
            schema_path = _EDGE_SCHEMA_DIR / yaml_file
            if schema_path.exists():
                schema = load_yaml_schema(schema_path)
                try:
                    assert_response_matches_schema(edge, schema)
                except AssertionError as exc:
                    errors.append(f"[{tag}] edge YAML validation failed: {exc}")
                    continue

        if exp.expected_payload:
            skip = exp.skip_compare
            for key, expected_val in exp.expected_payload.items():
                if key in skip:
                    continue
                actual_val = edge.get(key)
                if expected_val != actual_val:
                    errors.append(
                        f"[{tag}] payload mismatch on {key!r}: "
                        f"expected {expected_val!r}, actual {actual_val!r}"
                    )

    if errors:
        joined = "\n".join(f"  - {e}" for e in errors)
        raise AssertionError(f"Edge validation failures ({len(errors)}):\n{joined}")


# ---------------------------------------------------------------------------
# Profile builders (Phase 2 — structural)
# ---------------------------------------------------------------------------


def build_record_edge_expectations(
    record: Any,
    connector_id: str,
    *,
    parent_relation_type: str | None = None,
) -> list[EdgeExpectation]:
    """Build structural edge expectations from a validated Record entity.

    ``record`` should be a ``Record`` (or subclass) with fields populated from
    the graph (the *actual* entity from step 2).
    """
    from app.config.constants.arangodb import RECORD_TYPE_COLLECTION_MAPPING

    exps: list[EdgeExpectation] = []
    rec_ref = NodeRef(kind="record", internal_id=record.id, connector_id=connector_id)

    if record.external_record_group_id:
        rg_ref = NodeRef(
            kind="record_group",
            external_id=record.external_record_group_id,
            connector_id=connector_id,
        )
        exps.append(EdgeExpectation(
            collection="belongsTo",
            from_ref=rec_ref,
            to_ref=rg_ref,
            label="record belongsTo recordGroup",
        ))

        if record.inherit_permissions:
            exps.append(EdgeExpectation(
                collection="inheritPermissions",
                from_ref=rec_ref,
                to_ref=rg_ref,
                label="record inheritPermissions recordGroup",
            ))
        else:
            exps.append(EdgeExpectation(
                collection="inheritPermissions",
                from_ref=rec_ref,
                to_ref=rg_ref,
                cardinality="none",
                label="record NOT inheritPermissions (inherit=False)",
            ))

    rt_value = record.record_type.value if hasattr(record.record_type, "value") else str(record.record_type)
    typed_collection = RECORD_TYPE_COLLECTION_MAPPING.get(rt_value)
    if typed_collection:
        typed_ref = NodeRef(kind=None, collection=typed_collection, internal_id=record.id)
        exps.append(EdgeExpectation(
            collection="isOfType",
            from_ref=rec_ref,
            to_ref=typed_ref,
            label=f"record isOfType {typed_collection}",
        ))

    if record.parent_external_record_id:
        parent_ref = NodeRef(
            kind="record",
            external_id=record.parent_external_record_id,
            connector_id=connector_id,
        )
        rt_file = rt_value == "FILE"
        is_attachment = rt_file and getattr(record, "is_dependent_node", False)

        if parent_relation_type:
            rel_type = parent_relation_type
        elif is_attachment:
            rel_type = "ATTACHMENT"
        else:
            rel_type = "PARENT_CHILD"

        exps.append(EdgeExpectation(
            collection="recordRelations",
            from_ref=parent_ref,
            to_ref=rec_ref,
            cardinality="at_least_one",
            expected_payload={"relationshipType": rel_type},
            label=f"parent recordRelations ({rel_type}) -> record",
        ))

    return exps


def build_record_group_edge_expectations(
    rg: Any,
    connector_id: str,
) -> list[EdgeExpectation]:
    """Build structural edge expectations from a validated RecordGroup entity."""
    exps: list[EdgeExpectation] = []
    rg_ref = NodeRef(kind="record_group", internal_id=rg.id, connector_id=connector_id)
    app_ref = NodeRef(kind="app", internal_id=connector_id)

    exps.append(EdgeExpectation(
        collection="belongsTo",
        from_ref=rg_ref,
        to_ref=app_ref,
        label="recordGroup belongsTo app",
    ))

    if rg.parent_external_group_id:
        parent_rg_ref = NodeRef(
            kind="record_group",
            external_id=rg.parent_external_group_id,
            connector_id=connector_id,
        )
        exps.append(EdgeExpectation(
            collection="belongsTo",
            from_ref=rg_ref,
            to_ref=parent_rg_ref,
            label="recordGroup belongsTo parentRecordGroup",
        ))

        if getattr(rg, "inherit_permissions", False):
            exps.append(EdgeExpectation(
                collection="inheritPermissions",
                from_ref=rg_ref,
                to_ref=parent_rg_ref,
                label="recordGroup inheritPermissions parentRecordGroup",
            ))

    return exps


def build_user_edge_expectations(
    user_source_id: str,
    connector_id: str,
) -> list[EdgeExpectation]:
    """Build structural edge expectations for a synced user."""
    user_ref = NodeRef(
        kind="user",
        external_id=user_source_id,
        connector_id=connector_id,
    )
    app_ref = NodeRef(kind="app", internal_id=connector_id)

    return [
        EdgeExpectation(
            collection="userAppRelation",
            from_ref=user_ref,
            to_ref=app_ref,
            label="user userAppRelation app",
        ),
    ]


# ---------------------------------------------------------------------------
# Profile builder (Phase 3 — permissions)
# ---------------------------------------------------------------------------


def build_permission_expectations(
    perms: list[PermissionExpectation],
) -> list[EdgeExpectation]:
    """Convert a list of permission expectations into ``EdgeExpectation`` objects."""
    exps: list[EdgeExpectation] = []
    for p in perms:
        from_ref = NodeRef(
            kind=p.from_kind,
            external_id=p.from_external_id,
            connector_id=p.connector_id,
        )
        to_ref = NodeRef(
            kind=p.to_kind,
            external_id=p.to_external_id,
            connector_id=p.connector_id,
        )
        exps.append(EdgeExpectation(
            collection="permission",
            from_ref=from_ref,
            to_ref=to_ref,
            cardinality="at_least_one",
            expected_payload={"type": p.entity_type, "role": p.role},
            label=f"permission {p.from_kind}->{p.to_kind} ({p.entity_type}/{p.role})",
        ))
    return exps
