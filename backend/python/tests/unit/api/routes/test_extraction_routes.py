"""Tests for the POST /api/v1/extract/classify and /entities endpoints."""
from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from httpx import Response

from app.api.middlewares.auth import authMiddleware
from app.api.routes.extraction import router as extraction_router
from app.models.blocks import SemanticMetadata
from app.modules.transformers.document_extraction import (
    DocumentClassification,
    ExtractionLLMError,
    SubCategories,
    semantic_metadata_from_classification,
)
from app.utils.llm import LLMNotConfiguredError, LLMUnavailableError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _authenticated_as_indexing(request: Request) -> Request:
    request.state.user = {
        "token_type": "scoped",
        "scopes": ["document:classify"],
        "orgId": "org-123",
    }
    return request


def _build_app(document_extraction) -> FastAPI:
    app = FastAPI()
    app.state.document_extraction = document_extraction
    app.include_router(extraction_router)
    app.dependency_overrides[authMiddleware] = _authenticated_as_indexing
    return app


def _empty_bc_dict() -> dict:
    return {"blocks": [], "block_groups": []}


def _semantic_metadata_dict() -> dict:
    return {
        "departments": ["Engineering"],
        "languages": ["English"],
        "topics": ["Testing"],
        "summary": "A test document.",
        "categories": ["Technical"],
        "sub_category_level_1": "Software",
        "sub_category_level_2": "Python",
        "sub_category_level_3": "Testing",
    }


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_classify_success() -> None:
    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(
        return_value=SemanticMetadata(
            departments=["Engineering"],
            languages=["English"],
            topics=["Testing"],
            summary="A test doc.",
            categories=["Technical"],
            sub_category_level_1="Software",
            sub_category_level_2="Python",
            sub_category_level_3="Testing",
        )
    )

    app = _build_app(mock_extraction)
    client = TestClient(app)

    response = client.post(
        "/api/v1/extract/classify",
        json={
            "block_container": _empty_bc_dict(),
            "org_id": "org-123",
            "departments": ["Engineering", "Finance"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["classification"] is not None
    mock_extraction.classify.assert_awaited_once()


def test_classify_empty_document_returns_none_classification() -> None:
    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(return_value=None)

    app = _build_app(mock_extraction)
    client = TestClient(app)

    response = client.post(
        "/api/v1/extract/classify",
        json={"block_container": _empty_bc_dict(), "org_id": "org-123"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["classification"] is None


def test_classify_uses_provided_departments() -> None:
    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(return_value=None)

    app = _build_app(mock_extraction)
    client = TestClient(app)

    client.post(
        "/api/v1/extract/classify",
        json={
            "block_container": _empty_bc_dict(),
            "org_id": "org-123",
            "departments": ["Engineering", "Finance"],
        },
    )

    call_kwargs = mock_extraction.classify.call_args[1]
    assert call_kwargs["departments"] == ["Engineering", "Finance"]


def test_classify_forwards_record_name_and_type() -> None:
    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(return_value=None)

    app = _build_app(mock_extraction)
    client = TestClient(app)

    client.post(
        "/api/v1/extract/classify",
        json={
            "block_container": _empty_bc_dict(),
            "org_id": "org-123",
            "record_name": "Q3 Board Deck.pdf",
            "record_type": "FILE",
        },
    )

    call_kwargs = mock_extraction.classify.call_args[1]
    assert call_kwargs["record_name"] == "Q3 Board Deck.pdf"
    assert call_kwargs["record_type"] == "FILE"


def test_classify_record_name_and_type_default_to_empty() -> None:
    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(return_value=None)

    app = _build_app(mock_extraction)
    client = TestClient(app)

    client.post(
        "/api/v1/extract/classify",
        json={"block_container": _empty_bc_dict(), "org_id": "org-123"},
    )

    call_kwargs = mock_extraction.classify.call_args[1]
    assert call_kwargs["record_name"] == ""
    assert call_kwargs["record_type"] == ""


def test_classify_reports_no_language_for_a_record_in_code_and_formats_only() -> None:
    classification = DocumentClassification(
        departments=["Engineering"], category="Technical",
        subcategories=SubCategories(level1="Software", level2="Python", level3="Testing"),
        languages=["Python", "YAML"], topics=["Testing"], summary="A test doc.",
    )
    mock_extraction = MagicMock()
    # DocumentExtraction.classify answers with the stored shape.
    mock_extraction.classify = AsyncMock(return_value=semantic_metadata_from_classification(classification))
    block_container = {
        "blocks": [{
            "index": 0, "type": "text", "format": "txt",
            "data": (
                "apiVersion: v1, kind: Service, metadata.name: payments, metadata.namespace: prod, "
                "metadata.labels.app: payments, spec.selector.app: payments, spec.type: ClusterIP"
            ),
        }],
        "block_groups": [],
    }

    response = TestClient(_build_app(mock_extraction)).post(
        "/api/v1/extract/classify",
        json={"block_container": block_container, "org_id": "org-123"},
    )

    assert response.status_code == 200
    assert response.json()["classification"]["languages"] == []


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_classify_llm_failure_returns_500() -> None:
    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(side_effect=RuntimeError("LLM timeout"))

    app = _build_app(mock_extraction)
    client = TestClient(app)

    response = client.post(
        "/api/v1/extract/classify",
        json={"block_container": _empty_bc_dict(), "org_id": "org-123"},
    )

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert "LLM timeout" in body["error"]


def test_classify_refusal_is_not_answered_as_retryable() -> None:
    """A 5xx makes the extraction client send the whole document again."""
    from app.exceptions.non_retryable import NonRetryableError
    from app.services.base_client import TRANSIENT_STATUS_CODES

    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(side_effect=NonRetryableError("over budget"))

    response = TestClient(_build_app(mock_extraction)).post(
        "/api/v1/extract/classify",
        json={"block_container": _empty_bc_dict(), "org_id": "org-123"},
    )

    assert response.status_code == 422
    assert response.status_code not in TRANSIENT_STATUS_CODES
    assert response.json() == {
        "success": False, "classification": None, "error": "over budget", "error_code": None,
    }


def test_classify_invalid_block_container_returns_422() -> None:
    mock_extraction = MagicMock()

    app = _build_app(mock_extraction)
    client = TestClient(app)

    response = client.post(
        "/api/v1/extract/classify",
        json={"block_container": "not-a-dict", "org_id": "org-123"},
    )

    assert response.status_code in {422, 500}


# ---------------------------------------------------------------------------
# Service-token enforcement (real signed tokens, real auth middleware)
# ---------------------------------------------------------------------------

JWT_SECRET = "session-secret-for-tests"
SCOPED_SECRET = "scoped-secret-for-tests"


class _SecretsConfigService:
    async def get_config(self, key, **kwargs):
        return {"jwtSecret": JWT_SECRET, "scopedJwtSecret": SCOPED_SECRET}


def _service_token(scope: str, **claims) -> str:
    now = int(time.time())
    return jwt.encode(
        {"iat": now, "exp": now + 3600, "scopes": [scope], **claims},
        SCOPED_SECRET,
        algorithm="HS256",
    )


def _real_auth_client(document_extraction: MagicMock) -> TestClient:
    app = _build_app(document_extraction)
    app.dependency_overrides.clear()
    app.container = SimpleNamespace(
        logger=lambda: logging.getLogger("test.extraction_routes.auth"),
        config_service=_SecretsConfigService,
    )
    return TestClient(app)


def _classifier() -> MagicMock:
    document_extraction = MagicMock()
    document_extraction.classify = AsyncMock(return_value=None)
    return document_extraction


def _post_classify(client: TestClient, token: str | None, org_id: str | None = None) -> Response:
    body: dict = {"block_container": _empty_bc_dict()}
    if org_id is not None:
        body["org_id"] = org_id
    return client.post(
        "/api/v1/extract/classify",
        json=body,
        headers={"Authorization": f"Bearer {token}"} if token else {},
    )


def test_classify_without_token_is_401_and_does_not_classify() -> None:
    document_extraction = _classifier()

    response = _post_classify(_real_auth_client(document_extraction), token=None, org_id="org-123")

    assert response.status_code == 401
    document_extraction.classify.assert_not_awaited()


def test_classify_with_parse_scope_is_403() -> None:
    document_extraction = _classifier()
    token = _service_token("document:parse", orgId="org-123")

    response = _post_classify(_real_auth_client(document_extraction), token, org_id="org-123")

    assert response.status_code == 403
    document_extraction.classify.assert_not_awaited()


def test_classify_uses_token_org_when_body_org_absent() -> None:
    document_extraction = _classifier()
    token = _service_token("document:classify", orgId="org-123")

    response = _post_classify(_real_auth_client(document_extraction), token)

    assert response.status_code == 200
    assert document_extraction.classify.call_args[1]["org_id"] == "org-123"


def test_classify_body_org_mismatch_is_403_and_does_not_classify() -> None:
    document_extraction = _classifier()
    token = _service_token("document:classify", orgId="org-123")

    response = _post_classify(_real_auth_client(document_extraction), token, org_id="org-other")

    assert response.status_code == 403
    assert response.json()["detail"] == "org_id does not match the service token"
    document_extraction.classify.assert_not_awaited()


def test_classify_token_without_org_is_401() -> None:
    document_extraction = _classifier()
    token = _service_token("document:classify")

    response = _post_classify(_real_auth_client(document_extraction), token, org_id="org-123")

    assert response.status_code == 401
    assert response.json()["detail"] == "Token missing orgId"
    document_extraction.classify.assert_not_awaited()


# ---------------------------------------------------------------------------
# POST /api/v1/extract/entities
# ---------------------------------------------------------------------------


@pytest.fixture
def entities_app(monkeypatch: pytest.MonkeyPatch):
    from app.modules.named_entities import extractor

    llm = AsyncMock(return_value=(None, None, "fake", "fake-model"))
    extract = AsyncMock(return_value=MagicMock(model_dump=MagicMock(return_value={"entities": []})))
    monkeypatch.setattr(extractor, "indexing_llm", llm)
    monkeypatch.setattr(extractor, "NamedEntityExtractor", MagicMock(return_value=MagicMock(extract=extract)))
    app = _build_app(MagicMock())
    app.container = SimpleNamespace(config_service=lambda: None)
    app.state.entity_extraction_semaphore = asyncio.Semaphore(1)
    return app, llm, extract


def _post_entities(app: FastAPI, **body) -> Response:
    return TestClient(app).post(
        "/api/v1/extract/entities", json={"block_container": _empty_bc_dict(), **body},
    )


def test_entities_success_releases_the_slot(entities_app) -> None:
    app, _, extract = entities_app

    response = _post_entities(app, budgets={"max_turns": 3})

    assert response.status_code == 200
    assert extract.await_args.args[0].budgets.max_turns == 3
    assert not app.state.entity_extraction_semaphore.locked()


def test_entities_model_calls_run_as_the_caller_named_in_the_service_token(entities_app) -> None:
    from app.services.model_identity.call_context import peek_call_context

    app, llm, extract = entities_app
    seen = {}

    async def as_indexing_with_caller(request: Request) -> Request:
        request.state.user = {
            "token_type": "scoped",
            "scopes": ["document:classify"],
            "orgId": "org-123",
            "pcp": {"v": 1, "kind": "service", "sub": "indexing", "org": "org-123", "wl": "indexing"},
        }
        return request

    async def load_model(*_a: object, **_k: object) -> tuple[None, None, str, str]:
        seen["load"] = peek_call_context()
        return None, None, "fake", "fake-model"

    async def run_extract(*_a: object, **_k: object) -> MagicMock:
        seen["extract"] = peek_call_context()
        return MagicMock(model_dump=MagicMock(return_value={"entities": []}))

    llm.side_effect = load_model
    extract.side_effect = run_extract
    app.dependency_overrides[authMiddleware] = as_indexing_with_caller

    assert _post_entities(app).status_code == 200

    for step in ("load", "extract"):
        assert seen[step] is not None, f"the {step} step ran with no caller bound"
        assert seen[step].principal.service_name == "indexing"
        assert seen[step].principal.org_id == "org-123"


def test_entities_failure_releases_the_slot(entities_app) -> None:
    app, _, extract = entities_app
    extract.side_effect = RuntimeError("extraction broke")

    response = _post_entities(app)

    assert response.status_code == 500
    assert response.json()["error"] == "RuntimeError"
    assert not app.state.entity_extraction_semaphore.locked()


def test_entities_without_a_model_still_extracts_deterministically(entities_app) -> None:
    """The benchmark finding: a missing indexing model lost even the regex entities."""
    from app.utils.llm import LLMNotConfiguredError

    app, llm, extract = entities_app
    llm.side_effect = LLMNotConfiguredError()

    response = _post_entities(app)

    assert response.status_code == 200
    request = extract.await_args.args[0]
    assert (request.llm, request.transport) == (None, None)
    assert not app.state.entity_extraction_semaphore.locked()


def test_entities_when_busy_is_a_429_the_caller_treats_as_backpressure(entities_app, monkeypatch) -> None:
    from app.api.routes import extraction as route
    from app.services.base_client import parse_retry_after

    monkeypatch.setattr(route, "ENTITY_SLOT_WAIT_SECONDS", 0.01)
    app, llm, _ = entities_app
    app.state.entity_extraction_semaphore = asyncio.Semaphore(0)

    response = _post_entities(app)

    assert response.status_code == 429
    assert parse_retry_after(response.headers.get("Retry-After")) == route.ENTITY_BACKPRESSURE_RETRY_AFTER_SECONDS
    llm.assert_not_awaited()


def test_entities_waits_for_a_slot_freed_while_it_waits(entities_app) -> None:
    app, _, _ = entities_app
    semaphore = asyncio.Semaphore(0)
    app.state.entity_extraction_semaphore = semaphore

    async def free_soon() -> None:
        await asyncio.sleep(0.05)
        semaphore.release()

    original = semaphore.acquire

    async def acquire() -> bool:
        asyncio.get_running_loop().create_task(free_soon())
        return await original()

    semaphore.acquire = acquire  # type: ignore[method-assign]
    response = _post_entities(app)

    assert response.status_code == 200


def test_entities_invalid_budgets_is_422_and_takes_no_slot(entities_app) -> None:
    app, llm, _ = entities_app

    response = _post_entities(app, budgets={"max_turns": "lots"})

    assert response.status_code == 422
    llm.assert_not_awaited()
    assert not app.state.entity_extraction_semaphore.locked()


def test_classify_without_a_configured_llm_returns_422_with_error_code() -> None:
    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(
        side_effect=LLMNotConfiguredError("No LLM is configured for this organization")
    )
    client = TestClient(_build_app(mock_extraction))

    response = client.post(
        "/api/v1/extract/classify",
        json={"block_container": _empty_bc_dict(), "org_id": "org-123"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "LLM_NOT_CONFIGURED"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (LLMUnavailableError("connection refused"), "LLM_UNAVAILABLE"),
        (ExtractionLLMError("no usable output"), "LLM_FAILED"),
    ],
)
def test_model_failures_answer_424_with_a_code(error: Exception, code: str) -> None:
    """A dependency failed, not this service: a 5xx would be retried and trip the caller's breaker."""
    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(side_effect=error)
    client = TestClient(_build_app(mock_extraction))

    response = client.post(
        "/api/v1/extract/classify",
        json={"block_container": _empty_bc_dict(), "org_id": "org-123"},
    )

    assert response.status_code == 424
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == code


def test_an_open_circuit_tells_the_caller_how_long_to_wait() -> None:
    from app.services.llm_gateway.gateway import ProviderUnavailableError

    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(side_effect=ProviderUnavailableError("down", provider="p", retry_after=42.4))
    response = TestClient(_build_app(mock_extraction)).post(
        "/api/v1/extract/classify", json={"block_container": _empty_bc_dict(), "org_id": "org-123"}
    )
    assert response.status_code == 424
    assert response.headers["Retry-After"] == "43"


def test_an_outage_before_the_circuit_opens_names_no_wait() -> None:
    mock_extraction = MagicMock()
    mock_extraction.classify = AsyncMock(side_effect=LLMUnavailableError("down"))
    response = TestClient(_build_app(mock_extraction)).post(
        "/api/v1/extract/classify", json={"block_container": _empty_bc_dict(), "org_id": "org-123"}
    )
    assert response.status_code == 424
    assert "Retry-After" not in response.headers
