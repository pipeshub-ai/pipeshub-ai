"""Extraction Service HTTP API routes.

Endpoints
---------
POST /api/v1/extract/classify
    Run LLM document classification on a BlocksContainer.

GET  /health
    Standard health probe (defined in extraction_main.py).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.models.blocks import BlocksContainer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/extract", tags=["extraction"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ClassifyRequest(BaseModel):
    block_container: BlocksContainer
    org_id: str
    departments: list[str] = []


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
async def classify(request: Request, body: ClassifyRequest) -> JSONResponse:
    """Classify a document (departments, topics, summary, sentiment).

    ``departments`` should be pre-fetched by the caller (e.g. from the graph
    DB) to avoid introducing a graph connection dependency here.
    """
    document_extraction = request.app.state.document_extraction

    try:
        classification = await document_extraction.classify(
            blocks=body.block_container.blocks,
            org_id=body.org_id,
            departments=body.departments or None,
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
            "Unexpected error during classification for org '%s'", body.org_id
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
