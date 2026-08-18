"""Parsing Service HTTP API routes.

Endpoints
---------
POST /api/v1/parse
    Parse a file into blocks (or a raw parsed document for Docling-backed
    providers, which defer block construction to the caller). File content is
    sent as multipart form-data (field: ``file``). Metadata fields are passed
    as form fields.

GET  /api/v1/parse/providers
    List registered providers per format key.

GET  /health
    Standard health probe (defined in parsing_main.py but kept here for ref).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile, status
from fastapi.responses import JSONResponse

from app.services.parsing.interface import (
    ParseError,
    ParseErrorCode,
    ParserProvider,
)
from app.services.parsing.registry import ParserRegistry
from app.services.resource_governor import (
    acquire_gate_with_backpressure,
    classify,
    gate_pool,
    parse_cost,
)
from app.utils.request_context import current_display_id
from app.utils.semaphore_logger import SemaphoreLogger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/parse", tags=["parsing"])

# How long a request will wait for a parsing slot before it's reported as
# "saturated" (still waiting) and, ultimately, before the gate gives up and
# returns 429 — see PARSE_GATE_TIMEOUT_SECONDS below.
PARSE_QUEUE_WAIT_WARN_SECONDS = 10.0
# Total time a request may wait for a free slot before the gate responds 429
# backpressure. Generous relative to the old 30s: the indexing consumer's
# record-level timeout is 1800s with auto-renewed leases, so a record that
# waits this long in the parsing queue and eventually gets a slot is far
# cheaper than one that gets shed and has to re-enter the whole pipeline.
PARSE_GATE_TIMEOUT_SECONDS = 120.0
# Suggested client backoff before its next backpressured attempt — see
# ParsingClient/base_client's Retry-After handling (services/base_client.py).
PARSE_BACKPRESSURE_RETRY_AFTER_SECONDS = 5
# Parses slower than this are logged as an outlier so pathological documents
# are identifiable without turning on debug logging. Large PDFs (OCR/VLM
# heavy documents) can legitimately take several minutes, so this is set
# well above typical large-document parse times, not just above the median.
SLOW_PARSE_WARN_SECONDS = 300.0


def _get_registry(request: Request) -> ParserRegistry:
    """Pull the ParserRegistry from FastAPI app state."""
    registry: ParserRegistry = request.app.state.parser_registry
    return registry


# ---------------------------------------------------------------------------
# POST /api/v1/parse
# ---------------------------------------------------------------------------


@router.post("", summary="Parse a file into a BlocksContainer")
async def parse_file(
    request: Request,
    file: Annotated[UploadFile, File(description="File to parse")],
    record_name: Annotated[str, Form(description="Human-readable filename")] = "",
    mime_type: Annotated[str, Form(description="MIME type of the file")] = "",
    extension: Annotated[str, Form(description="File extension (without dot)")] = "",
    org_id: Annotated[str | None, Form(description="Organisation ID")] = None,
    provider: Annotated[str | None, Form(description="Parser provider override")] = None,
    skip_table_enrichment: Annotated[bool, Form(description="Skip LLM table summaries")] = False,
) -> JSONResponse:
    """Parse *file* into blocks, or a raw parsed document for Docling-backed providers.

    The response body is ``ParseResponse`` JSON. Exactly one of ``block_container``
    or ``raw_document`` is populated - Docling-backed providers defer block
    construction (and any LLM calls) to the caller so this service stays stateless::

        {
          "success": true,
          "block_container": { ... } | null,
          "raw_document": "<serialized DoclingDocument>" | null,
          "provider_used": "docling",
          "error": null
        }
    """
    registry = _get_registry(request)

    # Resolve provider enum if supplied
    provider_enum: ParserProvider | None = None
    if provider:
        try:
            provider_enum = ParserProvider(provider)
        except ValueError:
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content={
                    "success": False,
                    "error": {
                        "code": ParseErrorCode.INVALID_INPUT.value,
                        "message": f"Unknown provider '{provider}'. Valid values: "
                        + ", ".join(p.value for p in ParserProvider),
                        "details": {},
                    },
                },
            )

    content = await file.read()
    if not record_name:
        record_name = file.filename or "unknown"
    if not mime_type and file.content_type:
        mime_type = file.content_type
    if not extension and "." in record_name:
        extension = record_name.rsplit(".", 1)[-1]

    config: dict = {"extension": extension}
    if skip_table_enrichment:
        config["skip_table_enrichment"] = True

    if provider_enum is None:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "success": False,
                "error": {
                    "code": ParseErrorCode.NO_PROVIDER_PROVIDED.value,
                    "message": "No provider provided",
                },
            })

    message_id = current_display_id()
    tier = classify(extension, mime_type)
    cost = parse_cost(tier, len(content))
    logger.info(
        "Received parse request: record='%s' format=%s provider=%s size_bytes=%d tier=%s cost=%d",
        record_name, extension or mime_type or "unknown", provider_enum.value, len(content),
        tier.value, cost,
    )

    governor = request.app.state.governor
    gate = governor.gate(gate_pool(tier))

    admitted = await acquire_gate_with_backpressure(
        gate, cost, tier, message_id,
        logger=logger,
        log_prefix="parsing",
        queue_wait_warn_seconds=PARSE_QUEUE_WAIT_WARN_SECONDS,
        gate_timeout_seconds=PARSE_GATE_TIMEOUT_SECONDS,
    )
    if not admitted:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(PARSE_BACKPRESSURE_RETRY_AFTER_SECONDS)},
            content={
                "success": False,
                "error": {
                    "code": ParseErrorCode.PARSE_BACKPRESSURE.value,
                    "message": "Parsing service is at capacity; retry later.",
                    "details": {"tier": tier.value, "limit": gate.limit},
                },
            },
        )

    parse_start = time.monotonic()
    try:
        try:
            parser = registry.resolve(mime_type, extension, provider_enum)
            result = await parser.parse(content, record_name, config)
        except ParseError as exc:
            parse_ms = (time.monotonic() - parse_start) * 1000
            logger.warning(
                "Parse failed: record='%s' provider=%s parse_ms=%.0f code=%s message=%s",
                record_name, provider_enum.value, parse_ms, exc.code.value, exc.message,
            )
            http_status = (
                status.HTTP_422_UNPROCESSABLE_ENTITY
                if exc.code in (ParseErrorCode.UNSUPPORTED_FORMAT, ParseErrorCode.INVALID_INPUT)
                else status.HTTP_500_INTERNAL_SERVER_ERROR
            )
            return JSONResponse(
                status_code=http_status,
                content={"success": False, "error": exc.to_dict()},
            )
        except Exception as exc:  # noqa: BLE001
            parse_ms = (time.monotonic() - parse_start) * 1000
            logger.exception(
                "Unexpected error parsing '%s' (provider=%s, parse_ms=%.0f)",
                record_name, provider_enum.value, parse_ms,
            )
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "success": False,
                    "error": {
                        "code": ParseErrorCode.PARSE_FAILED.value,
                        "message": str(exc),
                        "details": {},
                    },
                },
            )
    finally:
        gate.release(cost)
        SemaphoreLogger.log_semaphore_release(
            f"parsing:{tier.value}", message_id, gate.limit - gate.in_use, gate.limit
        )

    parse_ms = (time.monotonic() - parse_start) * 1000
    if parse_ms >= SLOW_PARSE_WARN_SECONDS * 1000:
        logger.warning(
            "Slow parse: record='%s' provider=%s parse_ms=%.0f",
            record_name, provider_enum.value, parse_ms,
        )
    blocks_count = len(result.block_container.blocks) if result.block_container is not None else 0
    logger.info(
        "Parse completed: record='%s' outcome=success provider_used=%s parse_ms=%.0f blocks=%d raw_document=%s",
        record_name,
        result.provider_used.value if result.provider_used is not None else "default",
        parse_ms,
        blocks_count,
        result.raw_document is not None,
    )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "success": True,
            "block_container": (
                json.loads(result.block_container.model_dump_json())
                if result.block_container is not None
                else None
            ),
            "raw_document": result.raw_document,
            "provider_used": (
                result.provider_used.value if result.provider_used is not None else None
            ),
            "metadata": result.metadata,
            "error": None,
        },
    )


# ---------------------------------------------------------------------------
# GET /api/v1/parse/providers
# ---------------------------------------------------------------------------


@router.get("/providers", summary="List available providers per format")
async def list_providers(request: Request) -> JSONResponse:
    """Return a dict mapping format keys to their available provider names."""
    registry = _get_registry(request)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=registry.list_all_formats(),
    )
