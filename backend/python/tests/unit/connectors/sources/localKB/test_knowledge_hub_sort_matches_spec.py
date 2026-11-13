"""The knowledge-hub listings document exactly the sorts the router accepts, and the
knowledge-base page sends only those."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from app.connectors.sources.localKB.api.knowledge_hub_models import SortField

SPEC = Path(__file__).resolve().parents[6] / "nodejs" / "apps" / "src" / "modules" / "api-docs" / "pipeshub-openapi.yaml"


@pytest.mark.parametrize("path", [
    "/knowledgeBase/knowledge-hub/nodes",
    "/knowledgeBase/knowledge-hub/nodes/{parentType}/{parentId}",
])
def test_the_sort_by_enum_is_the_api_sort_field_enum(path: str) -> None:
    operation = yaml.safe_load(SPEC.read_text(encoding="utf-8"))["paths"][path]["get"]
    sort_by = next(p for p in operation["parameters"] if p.get("name") == "sortBy")

    assert sort_by["schema"]["enum"] == [field.value for field in SortField]



FE_SORT_FIELDS = (
    Path(__file__).resolve().parents[7] / "frontend" / "app" / "(main)" / "knowledge-base" / "sort-fields.json"
)


@pytest.mark.parametrize("listing", ["collections", "allRecords"])
def test_every_sort_the_frontend_sends_is_an_api_sort_field(listing: str) -> None:
    """The page sends these as `sortBy` unchanged; anything else is answered 400.
    The frontend's own test keeps the file equal to the lists it sends."""
    fields = json.loads(FE_SORT_FIELDS.read_text(encoding="utf-8"))[listing]

    assert fields
    assert set(fields) <= {field.value for field in SortField}
