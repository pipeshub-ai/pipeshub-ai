"""HTTP glue for the push routes: shared runtime, error bodies and the schema routes' mapping."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models.record_metadata import MetadataSchema, PropertyDefinition
from app.modules.record_metadata.schemas import (
    MAX_SCHEMA_VERSIONS,
    MetadataSchemaError,
    MetadataSchemaRegistry,
)

from .admission import Admission
from .contract import PushRefusal
from .spool import Spool, default_root, sweep
from .worker import PushWorker

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

_CODES = {
    400: "HTTP_BAD_REQUEST", 403: "HTTP_FORBIDDEN", 404: "HTTP_NOT_FOUND", 408: "HTTP_REQUEST_TIMEOUT",
    409: "HTTP_CONFLICT", 413: "HTTP_PAYLOAD_TOO_LARGE", 415: "HTTP_UNSUPPORTED_MEDIA_TYPE",
    422: "HTTP_UNPROCESSABLE_ENTITY", 429: "HTTP_TOO_MANY_REQUESTS", 500: "HTTP_INTERNAL_SERVER_ERROR",
    503: "HTTP_SERVICE_UNAVAILABLE",
}


@dataclass
class PushRuntime:
    worker: PushWorker
    spool: Spool
    admission: Admission


def runtime_for(app: FastAPI) -> PushRuntime:
    """One worker, spool and admission per connectors process, made on first use."""
    runtime = getattr(app.state, "kb_push_runtime", None)
    if runtime is None:
        sweep(default_root())
        runtime = PushRuntime(PushWorker(), Spool(), Admission())
        app.state.kb_push_runtime = runtime
    return runtime


def error_body(r: PushRefusal, request_id: str | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": _CODES.get(r.status, "HTTP_ERROR"), "reason": r.reason, "message": r.message}
    if r.location:
        error["location"] = r.location
    prop = r.extra.get("property") or (r.location if r.reason.startswith("PROPERTY_") else None)
    if prop:
        error["property"] = prop
    if r.issues:
        error["issues"] = r.issues[:50]
    for field in ("currentVersion", "limitBytes", "limit", "predictedBytes", "budgetBytes", "hint"):
        if field in r.extra:
            error[field] = r.extra[field]
    if request_id:
        error["requestId"] = request_id
    return {"error": error}


def error_headers(r: PushRefusal) -> dict[str, str]:
    retry = r.extra.get("retryAfter")
    return {"Retry-After": str(retry)} if retry else {}


# ---- schema routes ----------------------------------------------------------------------------

class PropertyIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    type: str
    displayLabel: str
    filterable: bool = False
    facetable: bool = False
    searchable: bool = False
    retrievable: bool = True
    enumValues: list[str] = Field(default_factory=list, max_length=256)
    semanticLabel: str | None = None


class SchemaIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    properties: list[PropertyIn] = Field(max_length=256)


def definitions_from(body: SchemaIn) -> list[PropertyDefinition]:
    out = []
    for i, p in enumerate(body.properties):
        data: dict[str, Any] = {
            "name": p.name, "type": p.type, "display_label": p.displayLabel, "searchable": p.searchable,
            "retrievable": p.retrievable, "enum_values": tuple(p.enumValues), "semantic_label": p.semanticLabel,
            "facetable": p.facetable,
        }
        if "filterable" in p.model_fields_set or not p.facetable:
            data["filterable"] = p.filterable
        try:
            out.append(PropertyDefinition.model_validate(data))
        except ValidationError as e:
            raise PushRefusal(422, "PROPERTY_DEFINITION_INVALID", f"property {p.name!r}: {e.errors()[0]['msg']}",
                              f"properties[{i}]", extra={"property": p.name}) from None
    return out


def schema_json(schema: MetadataSchema, *, created: bool | None = None) -> dict[str, Any]:
    props = []
    for p in schema.properties:
        d = p.model_dump(mode="json")
        props.append({
            "name": d["name"], "type": d["type"], "displayLabel": d["display_label"], "filterable": d["filterable"],
            "facetable": d["facetable"], "searchable": d["searchable"], "retrievable": d["retrievable"],
            "enumValues": d["enum_values"], "semanticLabel": d["semantic_label"],
        })
    out: dict[str, Any] = {"schemaId": schema.schema_id, "version": int(schema.schema_id.rsplit(":", 1)[1]), "properties": props}
    if created is not None:
        out["created"] = created
    return out


async def put_schema(registry: MetadataSchemaRegistry, org_id: str, kb_id: str, body: SchemaIn) -> dict[str, Any]:
    definitions = definitions_from(body)
    try:
        last, _ = await registry._scan(org_id, kb_id)
        schema = await registry.register(org_id, kb_id, definitions)
    except MetadataSchemaError as e:
        if f"{MAX_SCHEMA_VERSIONS} schema versions" in str(e):
            raise PushRefusal(409, "SCHEMA_VERSION_LIMIT", "This knowledge base has reached its schema version limit.") from None
        logger.error("schema registry error: %s", e)
        raise PushRefusal(500, "INTERNAL_ERROR", "The schema could not be stored.") from None
    return schema_json(schema, created=int(schema.schema_id.rsplit(":", 1)[1]) > last)


async def get_schema(registry: MetadataSchemaRegistry, org_id: str, kb_id: str, version: int | None) -> dict[str, Any]:
    try:
        schema = await registry.get(org_id, kb_id, version)
    except MetadataSchemaError:
        raise PushRefusal(500, "INTERNAL_ERROR", "The schema could not be read.") from None
    if schema is None:
        raise PushRefusal(404, "SCHEMA_NOT_FOUND", "This knowledge base has no such schema.")
    return schema_json(schema)
