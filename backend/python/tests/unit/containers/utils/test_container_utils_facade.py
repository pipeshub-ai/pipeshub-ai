"""ContainerUtils keeps every factory name and signature (T7, T8, T15).

The dependency_injector providers in each container bind these methods when the
container class is defined, so a renamed or re-ordered parameter breaks a service at
start-up. The heavy imports behind the factories were moved into the method bodies;
this pins the surface they present.
"""
import inspect

import pytest

from app.containers.utils.utils import ContainerUtils

SIGNATURES = {
    "get_vector_db_service": ("self", "config_service"),
    "create_graph_provider": ("self", "logger", "config_service", "accessible_records_cache"),
    "create_accessible_records_cache": ("self", "logger", "config_service"),
    "create_accessible_records_invalidator": ("self", "logger", "accessible_records_cache", "graph_provider"),
    "create_collection_registry": ("self", "logger", "config_service", "vector_db_service"),
    "create_indexing_pipeline": ("self", "logger", "config_service", "graph_provider", "vector_db_service", "collection_registry", "blob_storage"),
    "create_vector_store": ("self", "logger", "graph_provider", "config_service", "vector_db_service", "collection_registry"),
    "create_entity_vector_store": ("self", "logger", "config_service", "vector_db_service", "collection_name", "recreate_on_dimension_mismatch"),
    "create_entity_resolver": ("self", "logger", "config_service", "graph_provider", "entity_vector_store"),
    "create_connector_off_filter": ("self", "logger", "graph_provider"),
    "create_graphdb": ("self", "graph_provider", "logger"),
    "create_sink_orchestrator": ("self", "logger", "graphdb", "blob_storage", "vector_store", "graph_provider", "config_service", "entity_vector_store", "entity_resolver"),
    "create_document_extractor": ("self", "logger", "graph_provider", "config_service"),
    "create_blob_storage": ("self", "logger", "config_service", "graph_provider"),
    "create_parsers": ("self", "logger", "config_service"),
    "create_pipeline_runtime": ("self", "logger", "config_service", "graph_provider", "sink_orchestrator", "document_extractor", "extraction_client"),
    "create_processor": ("self", "logger", "config_service", "indexing_pipeline", "graph_provider", "parsers", "document_extractor", "sink_orchestrator", "pipeline_runtime"),
    "create_event_processor": ("self", "logger", "processor", "graph_provider", "config_service", "collection_registry", "parsing_client", "extraction_client", "sink_orchestrator", "pipeline_runtime"),
    "create_parsing_client": ("self", "config_service"),
    "create_extraction_client": ("self", "config_service"),
    "create_retrieval_service": ("self", "config_service", "logger", "vector_db_service", "graph_provider", "blob_store", "collection_registry"),
    "create_feature_flag_service": ("self", "config_service"),
}


def test_every_factory_keeps_its_name_and_parameters() -> None:
    public = {n for n, m in inspect.getmembers(ContainerUtils, inspect.isfunction) if not n.startswith("_")}
    assert public == set(SIGNATURES)
    for name, params in SIGNATURES.items():
        method = getattr(ContainerUtils, name)
        assert tuple(inspect.signature(method).parameters) == params, name
        assert inspect.iscoroutinefunction(method), name


@pytest.mark.parametrize("name", sorted(SIGNATURES))
def test_a_bound_factory_can_be_taken_per_container(name: str) -> None:
    # The containers do exactly this at class definition: ``container_utils.create_x``.
    assert callable(getattr(ContainerUtils(), name))


def test_the_record_converter_is_one_function_under_both_names() -> None:
    from app.events import processor
    from app.models import record_convert

    assert processor.convert_record_dict_to_record is record_convert.convert_record_dict_to_record


def test_the_record_converter_builds_the_same_record() -> None:
    from app.models.record_convert import convert_record_dict_to_record

    record = convert_record_dict_to_record({
        "_key": "r1", "orgId": "o1", "recordName": "a.md", "externalRecordId": "e1",
        "mimeType": "text/markdown", "connectorId": "c1", "connectorName": "NOT_A_CONNECTOR",
    })
    assert (record.id, record.org_id, record.record_name) == ("r1", "o1", "a.md")
    assert record.connector_name.value == "KB"
