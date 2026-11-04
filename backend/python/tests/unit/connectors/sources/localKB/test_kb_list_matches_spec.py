"""GET /knowledgeBase documents every field the list returns, ``status`` among them."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.config.constants.arangodb import AppStatus
from app.connectors.sources.localKB.api.models import KnowledgeBaseResponse
from app.services.graph_db.entity_index_queries import APP_STATUS_DELETING

SPEC = Path(__file__).resolve().parents[6] / "nodejs" / "apps" / "src" / "modules" / "api-docs" / "pipeshub-openapi.yaml"


def test_the_list_item_schema_has_every_field_of_a_knowledge_base() -> None:
    schemas = yaml.safe_load(SPEC.read_text(encoding="utf-8"))["components"]["schemas"]
    item = schemas["GetAllKnowledgeBaseResponseSchema"]["properties"]["knowledgeBases"]["items"]

    assert set(item["properties"]) == set(KnowledgeBaseResponse.model_fields)
    assert item["properties"]["status"]["nullable"] is True


def test_the_status_enum_lists_every_status_a_knowledge_base_can_hold() -> None:
    """``status`` is the KB app node's own status, which the sync lifecycle sets too (IDLE after
    an upload), so every AppStatus is a value the list returns."""
    schemas = yaml.safe_load(SPEC.read_text(encoding="utf-8"))["components"]["schemas"]
    expected = {s.value for s in AppStatus} | {APP_STATUS_DELETING}
    item = schemas["GetAllKnowledgeBaseResponseSchema"]["properties"]["knowledgeBases"]["items"]

    assert set(item["properties"]["status"]["enum"]) == expected
    assert set(schemas["KnowledgeBase"]["properties"]["status"]["enum"]) == expected
    assert set(schemas["GetKnowledgeBaseById"]["properties"]["status"]["enum"]) == expected
