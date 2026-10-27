"""GET /knowledgeBase documents every field the list returns, ``status`` among them."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.connectors.sources.localKB.api.models import KnowledgeBaseResponse

SPEC = Path(__file__).resolve().parents[6] / "nodejs" / "apps" / "src" / "modules" / "api-docs" / "pipeshub-openapi.yaml"


def test_the_list_item_schema_has_every_field_of_a_knowledge_base() -> None:
    schemas = yaml.safe_load(SPEC.read_text(encoding="utf-8"))["components"]["schemas"]
    item = schemas["GetAllKnowledgeBaseResponseSchema"]["properties"]["knowledgeBases"]["items"]

    assert set(item["properties"]) == set(KnowledgeBaseResponse.model_fields)
    assert item["properties"]["status"]["enum"] == ["DELETING"] and item["properties"]["status"]["nullable"] is True
