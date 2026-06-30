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

import json
import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.services.parsing.interface import (
    ParseError,
    ParseErrorCode,
    ParserProvider,
)
from app.services.parsing.registry import ParserRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/parse", tags=["parsing"])


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

    try:
        if provider_enum is not None:
            parser = registry.resolve(mime_type, extension, provider_enum)
            result = await parser.parse(content, record_name, config)
        else:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "success": False,
                    "error": {
                        "code": ParseErrorCode.NO_PROVIDER_PROVIDED.value,
                        "message": "No provider provided",
                    },
                })
    except ParseError as exc:
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
        logger.exception("Unexpected error parsing '%s'", record_name)
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
