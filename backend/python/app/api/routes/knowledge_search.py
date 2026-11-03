"""Knowledge-graph entity search and lookup as API routes.

Both reuse what the assistant's tools run, so access is decided in the same
place: ``entity_permissions`` (search through ``execute_search_entities``,
neighbours through ``list_accessible_entity_records``). Neither route has a
way to see a record the caller cannot read, and the caller is always the signed-in
user: the bodies carry no organisation or user id.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.agents.actions.knowledge_graph.ops.entity_discovery import (
    _named_entities_enabled,
    execute_search_entities,
)
from app.agents.actions.knowledge_graph.ops.entity_filters import (
    load_entity_access_context,
)
from app.agents.actions.knowledge_graph.views import _compact_date
from app.api.middlewares.auth import require_scopes
from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import OAuthScopes
from app.models.entities import resolve_weburl
from app.models.source_dates import RecordDates
from app.modules.retrieval.entity_permissions import (
    SEARCHABLE_ENTITY_TYPES,
    EntityAccessError,
    list_accessible_entity_records,
)
from app.services.graph_db.interface.graph_db_provider import (
    IGraphDBProvider,  # noqa: TC001 - FastAPI reads the annotation
)
from app.services.graph_db.taxonomy import (
    TAXONOMY_EDGE_COLLECTIONS,
    TAXONOMY_ENTITY_TYPE_BY_COLLECTION,
)
from app.utils.chat_helpers import resolve_frontend_url

router = APIRouter(tags=["Search"])
logger = logging.getLogger(__name__)

SEARCH_SCOPES = (OAuthScopes.SEMANTIC_READ, OAuthScopes.SEMANTIC_WRITE)
# Records read for labels at once.
_LOAD_CONCURRENCY = 8


async def _graph_provider(request: Request) -> IGraphDBProvider:
    return await request.app.container.graph_provider()


def _user(request: Request) -> tuple[str, str]:
    user = request.state.user
    org_id, user_id = user.get("orgId"), user.get("userId")
    if not org_id or not user_id:
        raise HTTPException(status_code=401, detail="Sign in again; your session has no organisation or user.")
    return org_id, user_id


# ---------------------------------------------------------------------------
# entities
# ---------------------------------------------------------------------------
async def _entity_state(request: Request, graph_provider: IGraphDBProvider) -> dict[str, Any]:
    """The state the knowledge-graph tools read, for one request: no chat, no
    scope beyond the caller's own access."""
    from app.api.routes.chatbot import load_entity_vector_store

    org_id, user_id = _user(request)
    container = request.app.container
    return {
        "org_id": org_id,
        "user_id": user_id,
        "graph_provider": graph_provider,
        "config_service": container.config_service(),
        "entity_vector_store": await load_entity_vector_store(container, container.logger(), org_id),
        "filters": {},
    }


class EntitySearchQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    entityTypes: list[str] | None = Field(default=None, max_length=10)
    namedEntityKinds: list[str] | None = Field(default=None, max_length=10)
    limit: int = Field(10, ge=1, le=25)


@router.post("/search/entities", dependencies=[Depends(require_scopes(*SEARCH_SCOPES))])
async def search_entities(
    request: Request,
    body: EntitySearchQuery,
    graph_provider: IGraphDBProvider = Depends(_graph_provider),
) -> JSONResponse:
    """Find knowledge-graph entities (departments, categories, topics, languages,
    record groups, record titles, named entities) the caller can reach."""
    state = await _entity_state(request, graph_provider)
    ok, text = await execute_search_entities(
        state, body.query, body.entityTypes, body.limit, body.namedEntityKinds,
    )
    payload = json.loads(text)
    if not ok:
        # the producing module says whether the search could not run (503) or the request was wrong (400)
        raise HTTPException(
            status_code=503 if payload.get("retryable") else 400,
            detail=payload.get("message", "Entity search failed."),
        )
    return JSONResponse(content=payload)


class EntityNeighborsQuery(BaseModel):
    entityId: str = Field(..., min_length=1, max_length=256)
    entityType: Literal[
        "department", "category", "subcategory", "topic", "language", "record_group", "record", "named_entity",
    ]
    recordTypes: list[str] | None = Field(default=None, max_length=20)
    recordLimit: int = Field(20, ge=1, le=50)
    relatedLimit: int = Field(20, ge=1, le=50)
    cursor: str | None = Field(default=None, max_length=64)


async def _related_entities(
    graph_provider: IGraphDBProvider,
    record_keys: list[str],
    own_id: str,
    *,
    named_enabled: bool,
    limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Entities attached to the given (already access-checked) records, most
    shared first, and the relation types seen. Built from those records only,
    so a count never includes a record the caller cannot read."""
    seen: dict[str, dict[str, Any]] = {}
    relations: set[str] = set()

    def add(
        entity_id: str, entity_type: str, name: str, relation: str, record_id: str, kind: str | None = None,
    ) -> None:
        if not entity_id or entity_id == own_id:
            return
        entry = seen.setdefault(entity_id, {
            "entityId": entity_id, "entityType": entity_type, "name": name,
            "relation": relation, "_records": set(), **({"kind": kind} if kind else {}),
        })
        entry["_records"].add(record_id)
        relations.add(relation)

    try:
        rows = await graph_provider.get_record_taxonomy_links(record_keys, include_departments=True) or []
    except Exception as exc:  # whatever the graph database raises is "try again", never a 500
        logger.warning("entity neighbors: taxonomy lookup failed", exc_info=True)
        raise EntityAccessError("taxonomy lookup failed") from exc
    for row in rows:
        collection = row.get("collection")
        entity_type = TAXONOMY_ENTITY_TYPE_BY_COLLECTION.get(collection or "")
        if entity_type:
            relation = TAXONOMY_EDGE_COLLECTIONS.get(collection, CollectionNames.BELONGS_TO_DEPARTMENT.value)
            add(str(row.get("entityId") or ""), entity_type, str(row.get("name") or ""), relation, str(row.get("recordId")))
    if named_enabled:
        gate = asyncio.Semaphore(_LOAD_CONCURRENCY)

        async def mentions(key: str) -> tuple[str, list[dict]]:
            async with gate:
                return key, await graph_provider.get_named_entities_for_record(key) or []

        try:
            labelled = await asyncio.gather(*(mentions(k) for k in record_keys))
        except Exception as exc:  # as above
            logger.warning("entity neighbors: named-entity lookup failed", exc_info=True)
            raise EntityAccessError("named-entity lookup failed") from exc
        for key, nodes in labelled:
            for node in nodes:
                add(
                    str(node.get("_key") or node.get("id") or ""), "named_entity",
                    str(node.get("name") or ""), CollectionNames.MENTIONS_ENTITY.value, key, node.get("kind"),
                )
    ranked = sorted(seen.values(), key=lambda e: (-len(e["_records"]), e["name"].lower()))[:limit]
    return [
        {**{k: v for k, v in e.items() if k != "_records"}, "sharedRecords": len(e["_records"])} for e in ranked
    ], sorted(relations)


def _listing_date(row: dict[str, Any]) -> dict[str, str]:
    """The one date key the assistant's entity tools show for a record (modified, dated, fileModified or uploaded), as YYYY-MM-DD."""
    dated = RecordDates.from_graph(row).listing_date(row.get("updatedAtTimestamp"))
    day = _compact_date(dated[1]) if dated else None
    return {dated[0]: day} if dated and day else {}


@router.post("/search/entities/neighbors", dependencies=[Depends(require_scopes(*SEARCH_SCOPES))])
async def entity_neighbors(
    request: Request,
    body: EntityNeighborsQuery,
    graph_provider: IGraphDBProvider = Depends(_graph_provider),
) -> JSONResponse:
    """The accessible records connected to one entity, the entities those records
    share with it, and the relation types between them. An entity the caller
    cannot reach and an entity with no readable records answer alike."""
    state = await _entity_state(request, graph_provider)
    named_enabled = await _named_entities_enabled(state)
    if body.entityType == "named_entity" and not named_enabled:
        raise HTTPException(status_code=400, detail="Named-entity lookup is not enabled.")
    if body.entityType not in SEARCHABLE_ENTITY_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported entityType; use one of {sorted(SEARCHABLE_ENTITY_TYPES)}.")
    frontend_url = await resolve_frontend_url(state["config_service"])
    try:
        context = await load_entity_access_context(state)
        page = await list_accessible_entity_records(
            graph_provider, context, entity_id=body.entityId, entity_type=body.entityType,
            record_types=[t.strip().upper() for t in body.recordTypes or [] if t.strip()] or None,
            limit=body.recordLimit, cursor=body.cursor,
        )
        keys = [r["_key"] for r in page.records if r.get("_key")]
        related, relations = (
            await _related_entities(graph_provider, keys, body.entityId, named_enabled=named_enabled, limit=body.relatedLimit)
            if keys else ([], [])
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EntityAccessError as exc:
        logger.warning("entity neighbors failed", exc_info=True)
        raise HTTPException(status_code=503, detail="Entity lookup failed - try again.") from exc
    records = [{
        "recordId": r["_key"],
        "recordName": r.get("recordName"),
        "recordType": r.get("recordType"),
        "app": context.app_names.get(r.get("connectorId")),
        "webUrl": None if r.get("hideWeburl") else resolve_weburl(r.get("webUrl"), frontend_url),
        **_listing_date(r),
    } for r in page.records]
    return JSONResponse(content={
        "status": "success",
        "entityId": body.entityId,
        "entityType": body.entityType,
        "records": records,
        "relatedEntities": related,
        "relationTypes": relations,
        **({"nextCursor": page.next_cursor} if page.next_cursor else {}),
        **({"note": "This entity has more records than one listing can scan; these are the newest of a sample."} if page.capped else {}),
        **({"relatedNote": "Related entities are counted over the records listed here, not the whole entity."} if page.next_cursor else {}),
    })

