"""The assistant's knowledge-graph entity tools as API routes, for MCP clients.

Each route runs the same operation the in-product agent's tool runs
(``app.agents.actions.knowledge_graph.ops``), on a state built for the signed-in
caller (``knowledge_search._entity_state``): their access, and the limit of the
connected app making the request. Listings come back as the agent reads them,
in ``text``, without the Citation IDs only a chat answer can resolve.

What a deployment serves differs, and is decided here as the agent decides it:

- entity search and an entity's records need the org's entity store;
- the named-entity type, co-mentions and typed values need named-entity
  extraction (``HIGH_ACCURACY_MODE`` or the org's Labs flag);
- searching inside the records that mention names needs, besides both, the
  ``ENTITY_SEARCH_TOOL`` switch;
- a person's records need neither.

``GET /search/entities/capabilities`` reports which hold for the caller, so a
client lists only what it can call; every route still checks its own gate,
since a client may call with what an earlier listing showed.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.agents.actions.knowledge_graph.ops.co_mentions import (
    TOO_SLOW_MSG as CO_MENTIONS_TOO_SLOW_MSG,
)
from app.agents.actions.knowledge_graph.ops.co_mentions import (
    execute_find_co_mentioned_entities,
)
from app.agents.actions.knowledge_graph.ops.entity_discovery import (
    NAMED_ENTITIES_DISABLED_MSG,
)
from app.agents.actions.knowledge_graph.ops.entity_filters import (
    limited_scope_note,
    load_entity_access_context,
)
from app.agents.actions.knowledge_graph.ops.entity_records import (
    LOOKUP_FAILED_MSG,
    NAMED_ENTITY_TYPE,
    execute_find_records_by_entity,
)
from app.agents.actions.knowledge_graph.ops.entity_search import (
    DEADLINE_SECONDS,
    NO_NAMES_MSG,
    TOO_SLOW_MSG,
    _nothing_to_search,
    build_linker,
    render_preamble,
)
from app.agents.actions.knowledge_graph.ops.people import execute_find_records_by_person
from app.agents.actions.knowledge_graph.ops.values import (
    NAME_TOO_SLOW_MSG,
    execute_find_records_by_value,
)
from app.agents.agent_loop.entity_search_tool import (
    ENTITY_SEARCH_OFFERED_STATE_KEY,
    NAMED_ENTITIES_ENABLED_STATE_KEY,
)
from app.api.connection_limit import (
    hide_record_parents_outside_connection,
    hide_record_source_paths,
)
from app.api.middlewares.auth import connection_limit, require_scopes
from app.api.routes.knowledge_search import (
    SEARCH_SCOPES,
    _entity_state,
    _graph_provider,
    _retrieval_service,
    _user,
)
from app.api.routes.search import (
    _EMPTY_RETRIEVAL_STATUSES,
    _queries_for_search,
    _retrieve_chunks,
    _retrieve_fetch_limit,
)
from app.modules.named_entities.domain.config import load_named_entity_config
from app.modules.named_entities.linking.mentions import MAX_MENTIONS, parse_mentions
from app.modules.named_entities.linking.records import readable_records
from app.modules.retrieval.entity_permissions import (
    SEARCHABLE_ENTITY_TYPES,
    EntityAccessError,
)
from app.modules.retrieval.retrieval_service import (
    RetrievalService,  # noqa: TC001 - FastAPI reads the annotation
)
from app.modules.retrieval.selection_scope import within_connection
from app.services.graph_db.interface.graph_db_provider import (
    IGraphDBProvider,  # noqa: TC001 - FastAPI reads the annotation
)
from app.services.model_identity.boundary import principal_from_request_user
from app.services.model_identity.runtime import call_scope
from app.services.model_identity.workload import Workload
from app.utils.user_messages import action_failed

router = APIRouter(tags=["Search"])

NOT_OFFERED_MSG = "Searching inside the records that mention names is not enabled in this deployment."
ENTITY_STORE_MISSING_MSG = "Entity lookup is not available: nothing has been indexed into the entity store yet."
# What the operations answer when they could not run, as opposed to a request
# they refuse: a retry may succeed, so these are 503s, never 400s.
_RETRYABLE = frozenset({
    LOOKUP_FAILED_MSG,
    TOO_SLOW_MSG,
    CO_MENTIONS_TOO_SLOW_MSG,
    NAME_TOO_SLOW_MSG,
    "Knowledge graph tool state not initialized.",
    "Graph provider not available.",
})
DEFAULT_PASSAGES = 10
MAX_PASSAGES = 50


def capabilities_of(state: dict[str, Any]) -> dict[str, bool]:
    """What this caller can be served, from one state: see the module docstring."""
    store = bool(state.get("entity_vector_store"))
    named = bool(state.get(NAMED_ENTITIES_ENABLED_STATE_KEY))
    return {
        "entitySearch": store,
        "entityRecords": store,
        "namedEntities": named,
        "coMentions": named,
        "recordsByValue": named,
        "recordsByEntities": bool(state.get(ENTITY_SEARCH_OFFERED_STATE_KEY)),
        "recordsByPerson": True,
    }


def _text(result: tuple[bool, str]) -> JSONResponse:
    ok, text = result
    if ok:
        return JSONResponse(content={"text": text})
    raise HTTPException(status_code=503 if text in _RETRYABLE else 400, detail=text)


def _require(offered: bool, detail: str) -> None:  # noqa: FBT001 - a gate and why it is shut
    if not offered:
        raise HTTPException(status_code=404, detail=detail)


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------
class EntityCapabilities(BaseModel):
    entitySearch: bool
    entityRecords: bool
    namedEntities: bool
    coMentions: bool
    recordsByValue: bool
    recordsByEntities: bool
    recordsByPerson: bool


@router.get("/search/entities/capabilities", dependencies=[Depends(require_scopes(*SEARCH_SCOPES))])
async def entity_capabilities(
    request: Request,
    graph_provider: IGraphDBProvider = Depends(_graph_provider),
) -> EntityCapabilities:
    """Which entity operations this deployment, org and caller are served."""
    return EntityCapabilities(**capabilities_of(await _entity_state(request, graph_provider)))


# ---------------------------------------------------------------------------
# an entity's records
# ---------------------------------------------------------------------------
class EntityRecordsQuery(BaseModel):
    entityId: str = Field(..., min_length=1, max_length=256)
    entityType: str = Field(..., min_length=1, max_length=64)
    recordTypes: list[str] | None = Field(default=None, max_length=20)
    limit: int = Field(20, ge=1, le=50)
    cursor: str | None = Field(default=None, min_length=1, max_length=4096)


@router.post("/search/entities/records", dependencies=[Depends(require_scopes(*SEARCH_SCOPES))])
async def entity_records(
    request: Request,
    body: EntityRecordsQuery,
    graph_provider: IGraphDBProvider = Depends(_graph_provider),
) -> JSONResponse:
    """The readable records connected to one entity, newest first, in pages.
    An entity the caller cannot reach and one with no readable records answer alike."""
    state = await _entity_state(request, graph_provider)
    caps = capabilities_of(state)
    _require(caps["entityRecords"], ENTITY_STORE_MISSING_MSG)
    entity_type = body.entityType.strip().lower()
    if entity_type not in SEARCHABLE_ENTITY_TYPES:
        raise HTTPException(
            status_code=400, detail=f"Unsupported entityType; use one of {sorted(SEARCHABLE_ENTITY_TYPES)}.",
        )
    if entity_type == NAMED_ENTITY_TYPE and not caps["namedEntities"]:
        raise HTTPException(status_code=400, detail=NAMED_ENTITIES_DISABLED_MSG)
    return _text(await execute_find_records_by_entity(
        state, entity_id=body.entityId, entity_type=entity_type,
        record_types=body.recordTypes, limit=body.limit, cursor=body.cursor,
    ))


# ---------------------------------------------------------------------------
# co-mentioned entities
# ---------------------------------------------------------------------------
class CoMentionedQuery(BaseModel):
    entityIds: list[str] = Field(..., min_length=1, max_length=20)
    kinds: list[str] | None = Field(default=None, max_length=20)
    limit: int = Field(20, ge=1, le=50)


@router.post("/search/entities/co-mentioned", dependencies=[Depends(require_scopes(*SEARCH_SCOPES))])
async def co_mentioned_entities(
    request: Request,
    body: CoMentionedQuery,
    graph_provider: IGraphDBProvider = Depends(_graph_provider),
) -> JSONResponse:
    """The named entities mentioned in the readable records that mention one entity, most first."""
    state = await _entity_state(request, graph_provider)
    _require(capabilities_of(state)["coMentions"], NAMED_ENTITIES_DISABLED_MSG)
    return _text(await execute_find_co_mentioned_entities(
        state, entity_ids=body.entityIds, kinds=body.kinds, limit=body.limit,
    ))


# ---------------------------------------------------------------------------
# a person's records
# ---------------------------------------------------------------------------
class PersonRecordsQuery(BaseModel):
    person: str = Field(..., min_length=1, max_length=320)
    roles: list[str] | None = Field(default=None, max_length=20)
    recordTypes: list[str] | None = Field(default=None, max_length=20)
    limit: int = Field(20, ge=1, le=50)
    cursor: str | None = Field(default=None, min_length=1, max_length=4096)


@router.post("/search/records/by-person", dependencies=[Depends(require_scopes(*SEARCH_SCOPES))])
async def records_by_person(
    request: Request,
    body: PersonRecordsQuery,
    graph_provider: IGraphDBProvider = Depends(_graph_provider),
) -> JSONResponse:
    """The readable records the connectors link to a person by role, newest first.
    ``person`` is a full name, an email, or "me"."""
    state = await _entity_state(request, graph_provider)
    return _text(await execute_find_records_by_person(
        state, person=body.person, roles=body.roles, record_types=body.recordTypes,
        limit=body.limit, cursor=body.cursor,
    ))


# ---------------------------------------------------------------------------
# typed values
# ---------------------------------------------------------------------------
class ValueRecordsQuery(BaseModel):
    kinds: list[str] | None = Field(default=None, max_length=20)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    entityIds: list[str] | None = Field(default=None, max_length=20)
    dateFrom: str | None = Field(default=None, max_length=64)
    dateTo: str | None = Field(default=None, max_length=64)
    amountMin: float | None = None
    amountMax: float | None = None
    currency: str | None = Field(default=None, max_length=16)
    quantityMin: float | None = None
    quantityMax: float | None = None
    dimension: str | None = Field(default=None, max_length=64)
    percentMin: float | None = None
    percentMax: float | None = None
    recordTypes: list[str] | None = Field(default=None, max_length=20)
    sourceIds: list[str] | None = Field(default=None, max_length=50)
    limit: int = Field(20, ge=1, le=50)
    cursor: str | None = Field(default=None, min_length=1, max_length=4096)


@router.post("/search/records/by-value", dependencies=[Depends(require_scopes(*SEARCH_SCOPES))])
async def records_by_value(
    request: Request,
    body: ValueRecordsQuery,
    graph_provider: IGraphDBProvider = Depends(_graph_provider),
) -> JSONResponse:
    """The readable records that mention a typed value: a date range, an amount,
    a quantity or a percentage, however the record writes it."""
    state = await _entity_state(request, graph_provider)
    _require(capabilities_of(state)["recordsByValue"], NAMED_ENTITIES_DISABLED_MSG)
    # A name that is not an exact match goes through one model check: bound to the caller, as a chat turn is.
    async with call_scope(
        principal_from_request_user(request.state.user), Workload.SEARCH,
        config_service=state["config_service"], container=request.app.container,
    ):
        result = await execute_find_records_by_value(
            state,
            kinds=body.kinds, name=body.name, entity_ids=body.entityIds,
            date_from=body.dateFrom, date_to=body.dateTo,
            amount_min=body.amountMin, amount_max=body.amountMax, currency=body.currency,
            quantity_min=body.quantityMin, quantity_max=body.quantityMax, dimension=body.dimension,
            percent_min=body.percentMin, percent_max=body.percentMax,
            record_types=body.recordTypes, source_ids=body.sourceIds,
            limit=body.limit, cursor=body.cursor,
        )
    return _text(result)


# ---------------------------------------------------------------------------
# passages inside the records that mention names
# ---------------------------------------------------------------------------
class Mention(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    kind: str | None = Field(default=None, max_length=64)


class EntityRecordsSearchQuery(BaseModel):
    mentions: list[Mention] = Field(..., min_length=1, max_length=MAX_MENTIONS)
    match: Literal["any", "all"] = "any"
    query: str | None = Field(default=None, max_length=1000)
    limit: int = Field(DEFAULT_PASSAGES, ge=1, le=MAX_PASSAGES)
    maxChunksPerRecord: int | None = Field(None, ge=1, le=MAX_PASSAGES)


@router.post("/search/records/by-entities", dependencies=[Depends(require_scopes(*SEARCH_SCOPES))])
async def records_by_entities(
    request: Request,
    body: EntityRecordsSearchQuery,
    graph_provider: IGraphDBProvider = Depends(_graph_provider),
    retrieval_service: RetrievalService = Depends(_retrieval_service),
) -> JSONResponse:
    """Each name linked to the entities extracted from the documents (an exact
    name settles it; look-alikes go through one model check), then the best
    passages for ``query`` (the names when none is given) inside the readable
    records that mention them: any of them, or all of them for ``match="all"``.
    ``entities`` says how each name was resolved."""
    state = await _entity_state(request, graph_provider)
    _require(capabilities_of(state)["recordsByEntities"], NOT_OFFERED_MSG)
    parsed = parse_mentions([m.model_dump(exclude_none=True) for m in body.mentions])
    notes = list(parsed.notes)
    if not parsed.mentions:
        raise HTTPException(status_code=400, detail=" ".join([*notes, NO_NAMES_MSG]))
    match_all = body.match == "all" and len(parsed.mentions) > 1
    question = (body.query or "").strip()
    state["query"] = question
    user = request.state.user
    org_id, user_id = _user(request)
    container = request.app.container
    logger = container.logger()
    async with call_scope(
        principal_from_request_user(user), Workload.SEARCH,
        config_service=state["config_service"], container=container,
    ):
        started = time.monotonic()
        try:
            context, config = await asyncio.gather(
                load_entity_access_context(state), load_named_entity_config(state["config_service"]),
            )
            linker = build_linker(state, graph_provider, context, aliases=config.link_aliases)
            linking = await asyncio.wait_for(linker.link(parsed.mentions, question=question), timeout=DEADLINE_SECONDS)
            scope = await asyncio.wait_for(
                readable_records(graph_provider, context, linking.results, match_all=match_all),
                timeout=max(1.0, DEADLINE_SECONDS - (time.monotonic() - started)),
            )
        except TimeoutError as exc:
            raise HTTPException(status_code=503, detail=TOO_SLOW_MSG) from exc
        except EntityAccessError as exc:
            logger.warning("records by entities: access check failed: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail=LOOKUP_FAILED_MSG) from exc
        except Exception as exc:
            # A failed lookup must read as a failure, never as "no records".
            logger.exception("records by entities: lookup failed")
            raise HTTPException(status_code=503, detail=LOOKUP_FAILED_MSG) from exc

        preamble = render_preamble(linking, scope, match_all=match_all, notes=notes).strip()
        if not scope.virtual_record_ids:
            nothing = " ".join(filter(None, (_nothing_to_search(linking, match_all=match_all), limited_scope_note(context))))
            return JSONResponse(content={"entities": f"{preamble}\n\n{nothing}", "chunks": [], "incomplete": False})

        searched = question or " ".join(result.mention.name for result in linking.results)
        try:
            results = await retrieval_service.search_with_filters(
                queries=_queries_for_search(searched, None, None),
                org_id=org_id,
                user_id=user_id,
                limit=_retrieve_fetch_limit(body.limit, body.maxChunksPerRecord),
                filter_groups=within_connection({}, connection_limit(user)),
                virtual_record_ids_from_tool=list(scope.virtual_record_ids),
                knowledge_search=True,
            )
            await hide_record_parents_outside_connection(request, graph_provider, org_id, user_id, results)
            hide_record_source_paths(request, results)
        except Exception as exc:
            logger.error("records by entities: search failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=action_failed("search these records")) from exc

    status_code = results.get("status_code", 500)
    if not results.get("searchResults"):
        if status_code == 200 or results.get("status") in _EMPTY_RETRIEVAL_STATUSES:
            return JSONResponse(content={
                "entities": preamble, "chunks": [],
                "incomplete": bool(results.get("searchIncomplete")), "message": results.get("message"),
            })
        return JSONResponse(status_code=status_code, content={"message": results.get("message")})
    return JSONResponse(content={
        "entities": preamble,
        "chunks": _retrieve_chunks(results, body.maxChunksPerRecord)[: body.limit],
        "incomplete": bool(results.get("searchIncomplete")),
    })
