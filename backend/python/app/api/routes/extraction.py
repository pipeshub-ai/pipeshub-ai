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
import math
from collections.abc import (
    Mapping,  # noqa: TC003  # FastAPI resolves route annotations at runtime
)
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.middlewares.auth import (
    authMiddleware,
    require_service_token,
    service_token_org,
)
from app.config.constants.service import TokenScopes
from app.exceptions.non_retryable import NonRetryableError
from app.models.blocks import BlocksContainer  # noqa: TC001  # pydantic field type
from app.modules.named_entities.domain.config import (
    NamedEntityBudgets,  # noqa: TC001  # pydantic field type
)
from app.modules.named_entities.domain.kinds import EntityKind, default_enabled_kinds
from app.modules.transformers.document_extraction import (
    ExtractionLLMError,
    extraction_error_class,
)
from app.services.llm_gateway import remote_usage
from app.services.llm_gateway.gateway import JobTrace, llm_job
from app.services.model_identity.propagation import scope_from_claims
from app.services.model_identity.workload import Workload
from app.utils.env_utils import env_positive_int
from app.utils.error_class import log_failure
from app.utils.llm import LLMNotConfiguredError, LLMUnavailableError

logger = logging.getLogger(__name__)

require_classify_token = require_service_token(TokenScopes.DOCUMENT_CLASSIFY)

# A block container for a very large document is tens of MB of JSON. The cap
# is above that and below what would exhaust the process when parsed.
MAX_CLASSIFY_REQUEST_BYTES = env_positive_int("EXTRACTION_MAX_REQUEST_BYTES", 128 * 1024 * 1024)
CLASSIFY_PATH = "/api/v1/extract/classify"

# On the router, not the app, so the checks hold wherever this router is included.
router = APIRouter(
    prefix="/api/v1/extract",
    tags=["extraction"],
    dependencies=[Depends(authMiddleware), Depends(require_classify_token)],
)


def _config_service(request: Request) -> Any:  # noqa: ANN401
    container = getattr(request.app, "container", None)
    return container.config_service() if container is not None else None


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ClassifyRequest(BaseModel):
    block_container: BlocksContainer
    org_id: str | None = None
    departments: list[str] = []
    record_name: str = ""
    record_type: str = ""
    # The caller's stage job (its state key). The model calls are admitted as that job, so the half-open trials of an
    # open breaker rotate over the waiting jobs instead of one job carrying them all.
    job_key: str | None = Field(default=None, max_length=256)
    # The failed sends the caller's job has left (its pause budget); this service sends no more than that.
    send_allowance: int | None = Field(default=None, ge=0)
    # The caller's name for this request, the same across its transport retries: a second delivery spends nothing.
    request_id: str | None = Field(default=None, max_length=64)


class ClassifyResponse(BaseModel):
    success: bool
    classification: dict | None = None
    error: str | None = None
    error_code: str | None = None
    # What the model calls of this request cost the caller's job (``JobTrace.usage``), whatever the outcome.
    usage: dict | None = None


class UnavailableResponse(ClassifyResponse):
    """The 424 body of a provider outage: whether a request reached the model provider (an open circuit is a
    refusal, not a send), and whether it was a half-open trial or a charged failure. A caller reads a missing
    ``sent`` as True, so an older service never gives a paused job free sends."""

    sent: bool | None = None
    requests: int | None = None
    pk: str | None = None
    size_class: str | None = None
    provider: str | None = None
    trial: bool | None = None
    chargeable: bool | None = None
    charged: int | None = None


def _unavailable_response(exc: LLMUnavailableError, usage: dict | None) -> JSONResponse:
    """424, not 5xx: this service is up and its model provider is not. A 5xx would be retried and counted by the
    caller's breaker, whose health probe cannot see the provider; the code tells the caller to pause the work instead."""
    retry_after = getattr(exc, "retry_after", None)
    return JSONResponse(
        status_code=status.HTTP_424_FAILED_DEPENDENCY,
        # Set while the provider's circuit is open: how long the caller's job should wait.
        headers={"Retry-After": str(math.ceil(retry_after))} if retry_after else None,
        content=UnavailableResponse(
            success=False,
            error=str(exc),
            error_code=LLMUnavailableError.code,
            usage=usage,
            sent=getattr(exc, "sent", True),
            requests=getattr(exc, "requests", None),
            pk=getattr(exc, "pk", None),
            size_class=getattr(exc, "size_class", None),
            provider=getattr(exc, "provider", None),
            trial=getattr(exc, "trial", False),
            chargeable=getattr(exc, "chargeable", False),
            charged=getattr(exc, "charged", None),
        ).model_dump(),
    )


REPLAYED = "REPLAYED"


async def _refuse_replay(job_key: str | None, request_id: str | None) -> JSONResponse | None:
    """A 409 for a request this service was already given (its reply was lost and the caller's client sent it again),
    with what the first delivery spent when it has finished; None for a first delivery. Claimed only once the request
    is about to spend (after any slot wait), so a busy 429 the caller retries is not a replay. When the record of
    deliveries cannot be read the request is refused as a provider outage that sent nothing: spending blind is what
    the record exists to prevent."""
    try:
        claimed = await remote_usage.claim(job_key or "", request_id or "")
    except Exception as exc:
        return _unavailable_response(
            LLMUnavailableError(f"the record of deliveries cannot be read: {type(exc).__name__}", sent=False), None
        )
    if claimed is True:
        return None
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"success": False, "error": "this request was already delivered", "error_code": REPLAYED,
                 "usage": claimed if isinstance(claimed, dict) else None},
    )


def _usage_of(trace: JobTrace | None) -> dict | None:
    """What the request spent for the caller's job, on every answer: a caller that retries a refused or failed
    request must count the sends behind it."""
    return trace.usage() if trace is not None else None


async def _record_usage(job_key: str | None, request_id: str | None, trace: JobTrace | None) -> None:
    """Recorded whatever happens, so a caller whose reply is lost learns what this request spent."""
    await remote_usage.record(job_key or "", request_id or "", trace.usage() if trace is not None else None)


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

    ``record_type`` (``CODE_FILE``) selects the code prompt, which returns a
    different field set -- hence the shared mapper rather than an inline
    SemanticMetadata construction here.

    ``departments`` should be pre-fetched by the caller (e.g. from the graph
    DB) to avoid introducing a graph connection dependency here.
    """
    org_id = service_token_org(claims, body.org_id)
    document_extraction = request.app.state.document_extraction
    trace: JobTrace | None = None
    replay = await _refuse_replay(body.job_key, body.request_id)
    if replay is not None:
        return replay
    try:

        try:
            async with scope_from_claims(
                claims,
                Workload.EXTRACTION,
                fallback_service="extraction",
                config_service=_config_service(request),
            ):
                try:
                    with llm_job(body.job_key or "", allowance=body.send_allowance) as trace:
                        metadata = await document_extraction.classify(
                            blocks=body.block_container.blocks,
                            org_id=org_id,
                            departments=body.departments or None,
                            record_name=body.record_name,
                            record_type=body.record_type,
                            block_groups=body.block_container.block_groups,
                        )
                except (LLMUnavailableError, ExtractionLLMError) as exc:
                    # Logged here, while the call scope still binds the org; the handlers below answer. A refusal by
                    # an open breaker sent nothing: every paused job meets it on every wake, so it is not news.
                    log_failure(
                        logger, "document_classify.failed", exc, include_error_text=False,
                        error_class=extraction_error_class(exc),
                        level=None if getattr(exc, "sent", True) else logging.DEBUG,
                    )
                    raise
        except LLMNotConfiguredError as exc:
            # 4xx: a configuration state, not an outage — the client must neither retry it
            # nor count it against its circuit breaker.
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content=ClassifyResponse(
                    success=False, error=str(exc), error_code=exc.code, usage=_usage_of(trace)
                ).model_dump(),
            )
        except LLMUnavailableError as exc:
            return _unavailable_response(exc, trace.usage() if trace is not None else None)
        except ExtractionLLMError as exc:
            # The model answered but produced nothing usable for this document: retried a few times.
            return JSONResponse(
                status_code=status.HTTP_424_FAILED_DEPENDENCY,
                content=ClassifyResponse(
                    success=False, error=str(exc), error_code=ExtractionLLMError.code, usage=_usage_of(trace)
                ).model_dump(),
            )
        except NonRetryableError as exc:
            # Not a 5xx: the client resends those, and this would refuse the
            # whole document again.
            logger.warning("Classification for org '%s' was refused: %s", org_id, exc)
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content=ClassifyResponse(success=False, error=str(exc), usage=_usage_of(trace)).model_dump(),
            )
        except Exception as exc:
            logger.exception(
                "Unexpected error during classification for org '%s'", org_id
            )
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content=ClassifyResponse(success=False, error=str(exc), usage=_usage_of(trace)).model_dump(),
            )

        usage = trace.usage() if trace is not None else None
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=ClassifyResponse(
                success=True, classification=metadata.model_dump() if metadata is not None else None, usage=usage
            ).model_dump(),
        )
    finally:
        await _record_usage(body.job_key, body.request_id, trace)


class ExtractEntitiesRequest(BaseModel):
    block_container: BlocksContainer
    org_id: str | None = None
    record_name: str = ""
    record_type: str = ""
    reference_time_ms: int | None = None
    tz: str = "UTC"
    enabled_kinds: list[str] = []
    budgets: NamedEntityBudgets | None = None
    # As for classify: the caller's stage job, the failed sends it has left, and its name for this request.
    job_key: str | None = Field(default=None, max_length=256)
    send_allowance: int | None = Field(default=None, ge=0)
    request_id: str | None = Field(default=None, max_length=64)


# How long a request waits for an extraction slot before it is shed with a 429.
ENTITY_SLOT_WAIT_SECONDS = 10.0
ENTITY_BACKPRESSURE_RETRY_AFTER_SECONDS = 5

class ExtractEntitiesResponse(BaseModel):
    success: bool
    extraction: dict | None = None
    error: str | None = None
    usage: dict | None = None


@router.post("/entities", response_model=ExtractEntitiesResponse, summary="Typed named-entity extraction")
async def extract_entities(
    request: Request,
    body: ExtractEntitiesRequest,
    claims: Mapping[str, Any] = Depends(require_classify_token),
) -> JSONResponse:
    """Extract typed entities. The model is loaded per request, not at startup."""
    org_id = service_token_org(claims, body.org_id)
    trace: JobTrace | None = None
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
    claimed = False
    try:
        replay = await _refuse_replay(body.job_key, body.request_id)
        if replay is not None:
            return replay
        claimed = True
        from app.modules.named_entities.extractor import (
            NamedEntityExtractor,
            NamedEntityRequest,
            indexing_llm_or_none,
        )

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
        async with scope_from_claims(
            claims,
            Workload.EXTRACTION,
            fallback_service="extraction",
            config_service=config_service,
        ):
            with llm_job(body.job_key or "", allowance=body.send_allowance) as trace:
                llm, transport, provider, model, enabled = await indexing_llm_or_none(
                    config_service,
                    enabled if enabled is not None else default_enabled_kinds(),
                    org_id=org_id,
                    shard_key=body.record_name,
                )
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
    except LLMUnavailableError as exc:
        logger.log(
            logging.WARNING if getattr(exc, "sent", True) else logging.DEBUG,
            "Model provider unavailable while extracting entities for org '%s': %s", org_id, type(exc).__name__,
        )
        return _unavailable_response(exc, trace.usage() if trace is not None else None)
    except Exception as exc:
        # The exception text can quote document content; report the type only.
        logger.warning("Named-entity extraction failed for org '%s': %s", org_id, type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ExtractEntitiesResponse(
                success=False, error=type(exc).__name__, usage=trace.usage() if trace is not None else None
            ).model_dump(),
        )
    finally:
        if semaphore is not None:
            semaphore.release()
        if claimed:
            await _record_usage(body.job_key, body.request_id, trace)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=ExtractEntitiesResponse(
            success=True,
            extraction=extraction.model_dump(mode="json"),
            usage=trace.usage() if trace is not None else None,
        ).model_dump(),
    )
