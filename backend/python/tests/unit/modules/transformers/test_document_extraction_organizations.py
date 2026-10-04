"""KG-13 slice 3b: the classification call also names the organisations a
document mentions, as one more field of the same structured output."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.middlewares.auth import authMiddleware
from app.api.routes.extraction import router as extraction_router
from app.modules.extraction.prompt_template import prompt_for_document_extraction
from app.modules.transformers.document_extraction import (
    MAX_EXTRACTED_ORGANIZATIONS,
    DocumentClassification,
    DocumentExtraction,
    SubCategories,
    semantic_metadata_from,
)


def _classification(**overrides: object) -> DocumentClassification:
    fields = {
        "departments": ["Sales"], "category": "Legal",
        "subcategories": SubCategories(level1="Contract", level2="", level3=""),
        "languages": ["English"], "topics": ["Renewal"], "summary": "A renewal.",
    }
    return DocumentClassification(**(fields | overrides))


def test_organizations_default_to_none_found() -> None:
    assert _classification().organizations == []


def test_organizations_are_cleaned_and_capped_not_rejected() -> None:
    """A long list must not fail validation: that would retry, then fall back
    to a summary-only result and lose every other field."""
    names = ["  Acme Corp ", "", "Globex", "acme corp"] + [f"Org {i}" for i in range(20)]
    orgs = _classification(organizations=names).organizations
    assert orgs[:2] == ["Acme Corp", "Globex"]
    assert len(orgs) == MAX_EXTRACTED_ORGANIZATIONS


def test_both_classify_paths_carry_organizations_into_semantic_metadata() -> None:
    meta = semantic_metadata_from(_classification(organizations=["Acme Corp"]))
    assert meta.organizations == ["Acme Corp"]
    assert (meta.categories, meta.sub_category_level_1, meta.topics) == (["Legal"], "Contract", ["Renewal"])


def test_an_object_without_organizations_maps_to_none() -> None:
    classification = MagicMock(spec=["departments", "languages", "topics", "summary", "category", "subcategories"])
    classification.configure_mock(departments=[], languages=[], topics=[], summary="s", category="c",
                                  subcategories=SimpleNamespace(level1="", level2="", level3=""))
    assert semantic_metadata_from(classification).organizations == []


def test_the_prompt_asks_for_organizations_and_says_what_is_not_one() -> None:
    prompt = prompt_for_document_extraction
    assert "**Organizations**" in prompt
    for excluded in ("people", "products", "the application"):
        assert excluded in prompt


async def test_in_process_extraction_writes_organizations() -> None:
    extraction = DocumentExtraction(MagicMock(), MagicMock(), MagicMock())
    extraction.process_document = AsyncMock(return_value=_classification(organizations=["Globex"]))
    record = MagicMock(org_id="o", record_name="r", record_type=SimpleNamespace(value="FILE"))
    await extraction.apply(SimpleNamespace(record=record))
    assert record.semantic_metadata.organizations == ["Globex"]


def test_the_extraction_service_returns_organizations() -> None:
    async def as_indexing(request: Request) -> Request:
        request.state.user = {"token_type": "scoped", "scopes": ["document:classify"], "orgId": "org-1"}
        return request

    service = MagicMock()
    service.classify = AsyncMock(return_value=_classification(organizations=["Initech"]))
    app = FastAPI()
    app.state.document_extraction = service
    app.include_router(extraction_router)
    app.dependency_overrides[authMiddleware] = as_indexing
    response = TestClient(app).post(
        "/api/v1/extract/classify",
        json={"block_container": {"blocks": [], "block_groups": []}, "org_id": "org-1"},
    )
    assert response.json()["classification"]["organizations"] == ["Initech"]
