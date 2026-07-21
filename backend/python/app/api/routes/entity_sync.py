"""
Admin endpoint for entity vector store synchronisation.

POST /api/v1/admin/entity-sync/trigger
    Full sync: pulls all entities for the org from the graph DB and upserts them
    into the entity vector store.  Safe to re-run (idempotent due to UUID5 point IDs).

GET  /api/v1/admin/entity-sync/status
    Returns the current entity vector store collection stats for the org.
"""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.middlewares.auth import require_scopes
from app.config.constants.service import OAuthScopes
from app.models.entities import EntityRecord, EntityType

router = APIRouter(prefix="/api/v1/admin/entity-sync", tags=["Entity Sync Admin"])


async def _get_services(request: Request) -> dict[str, Any]:
    container = request.app.container
    logger = container.logger()

    graph_provider = None
    if hasattr(request.app.state, "graph_provider"):
        graph_provider = request.app.state.graph_provider
    if graph_provider is None and hasattr(container, "graph_provider"):
        graph_provider = await container.graph_provider()

    entity_vector_store = None
    if hasattr(container, "entity_vector_store"):
        try:
            entity_vector_store = await container.entity_vector_store()
        except Exception as exc:
            logger.warning("entity_vector_store not available: %s", exc)

    return {
        "logger": logger,
        "graph_provider": graph_provider,
        "entity_vector_store": entity_vector_store,
    }


@router.post(
    "/trigger",
    summary="Trigger full entity vector sync for the caller's organisation",
)
async def trigger_entity_sync(
    request: Request,
    entity_types: list[str] | None = None,
) -> JSONResponse:
    """Pull all entities from the graph DB and upsert them into the entity
    vector store.  Idempotent — safe to re-run as a repair operation.

    Query params:
      entity_types: optional comma-separated list to restrict sync
                    (e.g. category,topic,department).  Defaults to all types.
    """
    services = await _get_services(request)
    logger = services["logger"]
    graph_provider = services["graph_provider"]
    entity_vector_store = services["entity_vector_store"]

    user = getattr(request.state, "user", {})
    org_id = user.get("orgId", "")
    if not org_id:
        raise HTTPException(status_code=401, detail="Authenticated org_id required")

    if entity_vector_store is None:
        raise HTTPException(
            status_code=503,
            detail="Entity vector store is not available on this service instance.",
        )
    if graph_provider is None:
        raise HTTPException(status_code=503, detail="Graph provider not available.")

    # Validate entity_types param
    valid_types = {e.value for e in EntityType}
    requested_types: list[str] | None = None
    if entity_types:
        requested_types = []
        for t in entity_types:
            t = t.strip().lower()
            if t not in valid_types:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown entity_type '{t}'. Valid: {sorted(valid_types)}",
                )
            requested_types.append(t)

    logger.info(
        "Entity sync triggered | org=%s | entity_types=%s", org_id, requested_types
    )

    try:
        raw_entities = await graph_provider.get_entities_for_sync(
            org_id=org_id,
            entity_types=requested_types,
        )
    except Exception as exc:
        logger.error("get_entities_for_sync failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Graph query failed: {exc}") from exc

    if not raw_entities:
        return JSONResponse(
            content={
                "status": "success",
                "message": "No entities found in graph for this organisation.",
                "synced": 0,
            }
        )

    entity_records: list[EntityRecord] = []
    for raw in raw_entities:
        try:
            etype_val = (raw.get("entityType") or "").lower()
            if etype_val not in valid_types:
                continue
            entity_records.append(
                EntityRecord(
                    entity_id=str(raw["entityId"]),
                    entity_type=EntityType(etype_val),
                    name=str(raw.get("name") or ""),
                    org_id=org_id,
                    description=raw.get("description"),
                    aliases=raw.get("aliases") or [],
                    parent_entity_id=raw.get("parentEntityId"),
                    parent_entity_type=(
                        EntityType(raw["parentEntityType"].lower())
                        if raw.get("parentEntityType")
                        and raw["parentEntityType"].lower() in valid_types
                        else None
                    ),
                    extraction_sources=raw.get("extractionSources") or [],
                    source_connectors=raw.get("sourceConnectors") or [],
                )
            )
        except Exception as exc:
            logger.warning("Skipping malformed entity %s: %s", raw, exc)

    if not entity_records:
        return JSONResponse(
            content={
                "status": "success",
                "message": "No valid entity records to sync.",
                "synced": 0,
            }
        )

    try:
        await entity_vector_store.upsert_entities_batch(entity_records)
    except Exception as exc:
        logger.error("upsert_entities_batch failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Vector store upsert failed: {exc}"
        ) from exc

    logger.info(
        "Entity sync complete | org=%s | synced=%d", org_id, len(entity_records)
    )
    return JSONResponse(
        content={
            "status": "success",
            "message": f"Synced {len(entity_records)} entities to vector store.",
            "synced": len(entity_records),
        }
    )


@router.get(
    "/status",
    summary="Get entity vector store collection stats for the caller's organisation",
)
async def entity_sync_status(request: Request) -> JSONResponse:
    """Return collection info and per-org entity count."""
    services = await _get_services(request)
    logger = services["logger"]
    entity_vector_store = services["entity_vector_store"]

    user = getattr(request.state, "user", {})
    org_id = user.get("orgId", "")

    if entity_vector_store is None:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "message": "Entity vector store is not provisioned on this service.",
            },
        )

    try:
        await entity_vector_store._ensure_initialized()
        collection_info = await entity_vector_store.vector_db_service.get_collection_info(
            entity_vector_store.collection_name
        )
        info = {
            "collection": entity_vector_store.collection_name,
            "exists": collection_info.exists,
            "total_points": getattr(collection_info, "points_count", None),
            "dense_dimension": getattr(collection_info, "dense_dimension", None),
        }
    except Exception as exc:
        logger.warning("Could not fetch collection info: %s", exc)
        info = {"collection": entity_vector_store.collection_name, "error": str(exc)}

    return JSONResponse(content={"status": "success", "data": info})
