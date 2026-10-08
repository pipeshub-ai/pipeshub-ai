"""Extraction Service HTTP API routes.

Endpoints
---------
POST /api/v1/extract/classify
    Run LLM document classification on a BlocksContainer.

GET  /health
    Standard health probe (defined in extraction_main.py).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.middlewares.auth import (
    authMiddleware,
    require_service_token,
    service_token_org,
)
from app.config.constants.service import TokenScopes
from app.models.blocks import BlocksContainer
from app.modules.named_entities.domain.config import NamedEntityBudgets
from app.modules.named_entities.domain.kinds import EntityKind

logger = logging.getLogger(__name__)

require_classify_token = require_service_token(TokenScopes.DOCUMENT_CLASSIFY)

# On the router, not the app, so the checks hold wherever this router is included.
router = APIRouter(
    prefix="/api/v1/extract",
    tags=["extraction"],
    dependencies=[Depends(authMiddleware), Depends(require_classify_token)],
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ClassifyRequest(BaseModel):
    block_container: BlocksContainer
    org_id: str | None = None
    departments: list[str] = []
    record_name: str = ""
    record_type: str = ""


class ClassifyResponse(BaseModel):
    success: bool
    classification: dict | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# POST /api/v1/extract/classify
# ---------------------------------------------------------------------------


@router.post(
    "/classify",
    response_model=ClassifyResponse,
    summary="LLM document classification",
)
async def classify(
    request: Request,
    body: ClassifyRequest,
    claims: Mapping[str, Any] = Depends(require_classify_token),
) -> JSONResponse:
    """Classify a document (departments, categories, topics, summary).

    ``departments`` should be pre-fetched by the caller (e.g. from the graph
    DB) to avoid introducing a graph connection dependency here.
    """
    org_id = service_token_org(claims, body.org_id)
    document_extraction = request.app.state.document_extraction

    try:
        classification = await document_extraction.classify(
            blocks=body.block_container.blocks,
            org_id=org_id,
            departments=body.departments or None,
            record_name=body.record_name,
            record_type=body.record_type,
        )
        if classification is None:
            metadata = None
        else:
            from app.models.blocks import SemanticMetadata  # noqa: PLC0415
            metadata = SemanticMetadata(
                departments=classification.departments,
                languages=classification.languages,
                topics=classification.topics,
                summary=classification.summary,
                categories=[classification.category],
                sub_category_level_1=classification.subcategories.level1,
                sub_category_level_2=classification.subcategories.level2,
                sub_category_level_3=classification.subcategories.level3,
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Unexpected error during classification for org '%s'", org_id
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ClassifyResponse(success=False, error=str(exc)).model_dump(),
        )

    if metadata is None:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=ClassifyResponse(success=True, classification=None).model_dump(),
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=ClassifyResponse(
            success=True, classification=metadata.model_dump()
        ).model_dump(),
    )


class ExtractEntitiesRequest(BaseModel):
    block_container: BlocksContainer
    org_id: str | None = None
    record_name: str = ""
    record_type: str = ""
    reference_time_ms: int | None = None
    tz: str = "UTC"
    enabled_kinds: list[str] = []
    budgets: NamedEntityBudgets | None = None


# How long a request waits for an extraction slot before it is shed with a 429.
ENTITY_SLOT_WAIT_SECONDS = 10.0
ENTITY_BACKPRESSURE_RETRY_AFTER_SECONDS = 5

class ExtractEntitiesResponse(BaseModel):
    success: bool
    extraction: dict | None = None
    error: str | None = None


@router.post("/entities", response_model=ExtractEntitiesResponse, summary="Typed named-entity extraction")
async def extract_entities(
    request: Request,
    body: ExtractEntitiesRequest,
    claims: Mapping[str, Any] = Depends(require_classify_token),
) -> JSONResponse:
    """Extract typed entities. The model is loaded per request, not at startup."""
    org_id = service_token_org(claims, body.org_id)
    semaphore = getattr(request.app.state, "entity_extraction_semaphore", None)
    if semaphore is not None:
        try:
            await asyncio.wait_for(semaphore.acquire(), ENTITY_SLOT_WAIT_SECONDS)
        except TimeoutError:
            # Retry-After makes this backpressure for the caller: it retries on its own
            # budget and pauses its consumer, and a busy service never trips the breaker
            # that classification shares.
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Retry-After": str(ENTITY_BACKPRESSURE_RETRY_AFTER_SECONDS)},
                content=ExtractEntitiesResponse(success=False, error="entity extraction is busy").model_dump(),
            )
    try:
        from app.modules.named_entities.extractor import NamedEntityExtractor, NamedEntityRequest, indexing_llm

        enabled = None
        if body.enabled_kinds:
            parsed = set()
            for raw in body.enabled_kinds:
                try:
                    parsed.add(EntityKind(raw))
                except ValueError:
                    continue
            enabled = frozenset(parsed)
        config_service = request.app.container.config_service()
        llm = transport = None
        provider = model = "indexing"
        try:
            llm, transport, provider, model = await indexing_llm(config_service)
        except Exception as exc:
            # As in-process extraction does: without a model the deterministic
            # entities still count, and the record is PARTIAL, not failed.
            logger.warning("Named-entity model unavailable for org '%s': %s", org_id, type(exc).__name__)
        extraction = await NamedEntityExtractor().extract(
            NamedEntityRequest(
                blocks=body.block_container.blocks,
                org_id=org_id,
                record_name=body.record_name,
                record_type=body.record_type,
                reference_time_ms=body.reference_time_ms,
                tz=body.tz,
                enabled=enabled,
                budgets=body.budgets,
                llm=llm,
                transport=transport,
                provider_name=provider,
                model_name=model,
            )
        )
    except Exception as exc:
        # The exception text can quote document content; report the type only.
        logger.warning("Named-entity extraction failed for org '%s': %s", org_id, type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ExtractEntitiesResponse(success=False, error=type(exc).__name__).model_dump(),
        )
    finally:
        if semaphore is not None:
            semaphore.release()
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=ExtractEntitiesResponse(success=True, extraction=extraction.model_dump(mode="json")).model_dump(),
    )
