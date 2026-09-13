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
import math

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.models.blocks import BlocksContainer
from app.modules.transformers.document_extraction import ExtractionLLMError
from app.utils.llm import LLMNotConfiguredError, LLMUnavailableError

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
    error_code: str | None = None


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
        metadata = await document_extraction.classify(
            blocks=body.block_container.blocks,
            org_id=body.org_id,
            departments=body.departments or None,
        )
    except LLMNotConfiguredError as exc:
        # 4xx: a configuration state, not an outage — the client must neither retry it
        # nor count it against its circuit breaker.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ClassifyResponse(
                success=False, error=str(exc), error_code=LLMNotConfiguredError.code
            ).model_dump(),
        )
    except LLMUnavailableError as exc:
        # 424, not 5xx: this service is up and its model provider is not. A 5xx would be
        # retried and counted by the caller's breaker, whose health probe cannot see the
        # provider; the code tells the caller to pause the work instead.
        logger.warning("Model provider unavailable while classifying for org '%s': %s", body.org_id, exc)
        retry_after = getattr(exc, "retry_after", None)
        return JSONResponse(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            # Set while the provider's circuit is open: how long the caller's job should wait.
            headers={"Retry-After": str(math.ceil(retry_after))} if retry_after else None,
            content=ClassifyResponse(
                success=False, error=str(exc), error_code=LLMUnavailableError.code
            ).model_dump(),
        )
    except ExtractionLLMError as exc:
        # The model answered but produced nothing usable for this document: retried a few times.
        logger.warning("LLM failed to classify a document for org '%s': %s", body.org_id, exc)
        return JSONResponse(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            content=ClassifyResponse(
                success=False, error=str(exc), error_code=ExtractionLLMError.code
            ).model_dump(),
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
