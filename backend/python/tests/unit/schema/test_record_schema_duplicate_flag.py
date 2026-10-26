"""The records collection's schema is strict; the reconcile flag must be declared."""

from app.schema.arango.documents import record_schema


def test_duplicate_reconcile_pending_is_a_declared_boolean() -> None:
    rule = record_schema["rule"]
    assert rule["additionalProperties"] is False
    assert rule["properties"]["duplicateReconcilePending"] == {"type": "boolean"}


def test_duplicate_reconcile_attempts_is_declared() -> None:
    """The retry sweep counts failed reconciles on the record (KG-51)."""
    rule = record_schema["rule"]
    assert rule["properties"]["duplicateReconcileAttempts"] == {"type": ["integer", "null"]}
    assert rule["properties"]["duplicateReconcileDueAt"] == {"type": ["number", "null"]}


def test_taxonomy_edges_declare_extracted_name() -> None:
    """The resolver writes extractedName on belongsTo* edges; under the strict
    basic edge schema ArangoDB rejected every one of them (errorNum 1620)."""
    from app.config.constants.arangodb import CollectionNames
    from app.schema.arango.edges import taxonomy_edge_schema
    from app.services.graph_db.arango.arango_http_provider import EDGE_COLLECTIONS

    rule = taxonomy_edge_schema["rule"]
    assert rule["additionalProperties"] is False
    assert rule["properties"]["extractedName"] == {"type": ["string", "null"]}
    schemas = dict(EDGE_COLLECTIONS)
    for collection in (
        CollectionNames.BELONGS_TO_CATEGORY.value,
        CollectionNames.BELONGS_TO_LANGUAGE.value,
        CollectionNames.BELONGS_TO_TOPIC.value,
    ):
        assert schemas[collection] is taxonomy_edge_schema, collection
