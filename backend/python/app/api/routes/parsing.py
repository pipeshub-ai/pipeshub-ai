"""Parsing Service HTTP API routes.

Endpoints
---------
POST /api/v1/parse
    Parse a file into a BlocksContainer.  File content is sent as multipart
    form-data (field: ``file``).  Metadata fields are passed as form fields.

GET  /api/v1/parse/providers
    List registered providers per format key.

GET  /health
    Standard health probe (defined in parsing_main.py but kept here for ref).
"""
from __future__ import annotations

import asyncio
import contextlib
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
from app.utils.request_context import current_display_id
from app.utils.semaphore_logger import SemaphoreLogger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/parse", tags=["parsing"])

# How long a request will wait for a parsing slot before it's reported as
# "saturated" (still waiting) and, ultimately, before the gate gives up and
# returns 503 — see PARSE_GATE_TIMEOUT_SECONDS below.
PARSE_QUEUE_WAIT_WARN_SECONDS = 10.0
# Total time a request may wait for a free slot before the gate responds 503
# (retryable — ParsingClient backs off and retries, see services/parsing/client.py).
PARSE_GATE_TIMEOUT_SECONDS = 30.0
# Parses slower than this are logged as an outlier so pathological documents
# are identifiable without turning on debug logging. Large PDFs (OCR/VLM
# heavy documents) can legitimately take several minutes, so this is set
# well above typical large-document parse times, not just above the median.
SLOW_PARSE_WARN_SECONDS = 300.0


def _get_registry(request: Request) -> ParserRegistry:
    """Pull the ParserRegistry from FastAPI app state."""
    registry: ParserRegistry = request.app.state.parser_registry
    return registry


async def _acquire_parse_slot(
    semaphore: asyncio.Semaphore, max_slots: int, message_id: str
) -> bool:
    """Acquire a parsing slot, logging when the wait indicates saturation.

    Returns True once a slot is acquired. Returns False if
    ``PARSE_GATE_TIMEOUT_SECONDS`` elapses with no free slot — the caller
    should respond 503 so the client's own retry/backoff takes over instead
    of queuing indefinitely.
    """
    available = getattr(semaphore, "_value", -1)
    SemaphoreLogger.log_semaphore_acquire_attempt(
        "parsing", message_id, available, max_slots, 0, 0
    )
    start = time.monotonic()
    # A bare `wait_for(semaphore.acquire(), ...)` cancels the acquire on
    # timeout; retrying would create a *new* acquire() call that re-joins the
    # semaphore's internal waiter queue at the back, so a request already
    # waiting could lose its place — and be starved — every time it logs the
    # saturation warning below. Run acquire() as a single Task instead and
    # shield it from the first (warn-only) timeout so it keeps its queue
    # position; only cancel it if we're giving up for good.
    acquire_task = asyncio.ensure_future(semaphore.acquire())
    try:
        await asyncio.wait_for(asyncio.shield(acquire_task), timeout=PARSE_QUEUE_WAIT_WARN_SECONDS)
    except asyncio.TimeoutError:
        logger.warning(
            "Parsing saturated: request waited >%.0fs for a slot "
            "(max_concurrent_parsing=%d); still waiting up to %.0fs total",
            PARSE_QUEUE_WAIT_WARN_SECONDS, max_slots, PARSE_GATE_TIMEOUT_SECONDS,
        )
        remaining = max(PARSE_GATE_TIMEOUT_SECONDS - PARSE_QUEUE_WAIT_WARN_SECONDS, 0.0)
        try:
            await asyncio.wait_for(acquire_task, timeout=remaining)
        except asyncio.TimeoutError:
            acquire_task.cancel()
            # Await the cancellation so the task can't be garbage-collected
            # while still pending (which asyncio logs as an error).
            with contextlib.suppress(asyncio.CancelledError):
                await acquire_task
            logger.warning(
                "Parsing saturated: gate timeout after %.0fs (max_concurrent_parsing=%d); "
                "returning 503 to let the caller retry/back off",
                PARSE_GATE_TIMEOUT_SECONDS, max_slots,
            )
            SemaphoreLogger.log_message_error(message_id, "parse gate timeout — 503")
            return False

    wait_ms = (time.monotonic() - start) * 1000
    SemaphoreLogger.log_semaphore_acquired(
        message_id, getattr(semaphore, "_value", -1), max_slots, 0, 0, wait_time_ms=wait_ms
    )
    if wait_ms >= 1000:
        logger.info(
            "Parse slot acquired after %.0fms queue wait (max_concurrent_parsing=%d)",
            wait_ms, max_slots,
        )
    return True


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
    """Parse *file* into a ``BlocksContainer``.

    The response body is ``ParseResponse`` JSON::

        {
          "success": true,
          "block_container": { ... },
          "provider_used": "docling_service",
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
    logger.info(
        "Accepted parse request: record='%s' format=%s provider=%s size_bytes=%d",
        record_name, extension or mime_type or "unknown", provider_enum.value, len(content),
    )

    semaphore: asyncio.Semaphore = request.app.state.parse_semaphore
    max_slots: int = request.app.state.max_concurrent_parsing

    if not await _acquire_parse_slot(semaphore, max_slots, message_id):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "5"},
            content={
                "success": False,
                "error": {
                    "code": ParseErrorCode.PARSE_FAILED.value,
                    "message": "Parsing service is at capacity; retry later.",
                    "details": {"max_concurrent_parsing": max_slots},
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
        semaphore.release()
        SemaphoreLogger.log_semaphore_release(
            "parsing", message_id, getattr(semaphore, "_value", -1), max_slots
        )

    parse_ms = (time.monotonic() - parse_start) * 1000
    if parse_ms >= SLOW_PARSE_WARN_SECONDS * 1000:
        logger.warning(
            "Slow parse: record='%s' provider=%s parse_ms=%.0f",
            record_name, provider_enum.value, parse_ms,
        )
    logger.info(
        "Parse completed: record='%s' outcome=success provider_used=%s parse_ms=%.0f blocks=%d",
        record_name,
        result.provider_used.value if result.provider_used is not None else "default",
        parse_ms,
        len(result.block_container.blocks),
    )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "success": True,
            "block_container": json.loads(result.block_container.model_dump_json()),
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
