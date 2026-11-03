"""Exact-text (grep) search and knowledge-graph entities as API routes.

All reuse what the assistant's tools run, so access is decided in the same
places: grep through ``merge_pattern_match_results`` (the permission check
semantic search uses, after a storage grep that sees only readable records),
entities through ``entity_permissions``. No route has a way to see a record
the caller cannot read, and the caller is always the signed-in user: the
bodies carry no organisation or user id.
"""
from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import re
import shlex
import time
from typing import TYPE_CHECKING, Any, Literal

import regex
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

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
from app.core.org_context import (
    MissingOrgContext,
    SystemContextNotAllowed,
    current,
    spawn_in_request,
)
from app.models.entities import resolve_weburl
from app.models.source_dates import RecordDates
from app.modules.artifacts.reader import record_artifact_reader
from app.modules.retrieval.entity_permissions import (
    SEARCHABLE_ENTITY_TYPES,
    EntityAccessError,
    list_accessible_entity_records,
)
from app.modules.retrieval.filter_schema import validate_request_filters
from app.modules.retrieval.retrieval_service import (
    RetrievalService,  # noqa: TC001 - FastAPI reads the annotation
)
from app.services.graph_db.interface.graph_db_provider import (
    IGraphDBProvider,  # noqa: TC001 - FastAPI reads the annotation
)
from app.services.graph_db.taxonomy import (
    TAXONOMY_EDGE_COLLECTIONS,
    TAXONOMY_ENTITY_TYPE_BY_COLLECTION,
)
from app.utils.chat_helpers import resolve_frontend_url
from app.utils.grep_lines import (
    COMPRESSED_MARK,
    ere_prefilter,
    find_matches_bounded,
    literal_prefilter,
)
from app.utils.pattern_match import (
    PatternMatchCoverage,
    await_pattern_match,
    check_pattern_match_eligible,
    merge_pattern_match_results,
    render_pattern_match_coverage_note,
    run_pattern_match_with_llm_grep,
    validate_pattern_match_command,
)
from app.utils.user_messages import action_failed

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

router = APIRouter(tags=["Search"])
logger = logging.getLogger(__name__)

SEARCH_SCOPES = (OAuthScopes.SEMANTIC_READ, OAuthScopes.SEMANTIC_WRITE)
MAX_PATTERN_CHARS = 500
_BACKREFERENCE = re.compile(r"\\[1-9]")
# Records read for line matches (or labels) at once; each is a blob fetch.
_LOAD_CONCURRENCY = 8
# Readable records one app may give back (the request's ``limit`` cuts after).
MAX_RECORDS_PER_APP = 100
# Share of ``timeBudgetMs`` for finding records; the rest is for reading them.
_FIND_SHARE = 0.6


async def _retrieval_service(request: Request) -> RetrievalService:
    return await request.app.container.retrieval_service()


async def _graph_provider(request: Request) -> IGraphDBProvider:
    return await request.app.container.graph_provider()


def _user(request: Request) -> tuple[str, str]:
    """The caller, from the org context the auth middleware bound (never from the body or the claims read here)."""
    try:
        ctx = current()
    except (MissingOrgContext, SystemContextNotAllowed) as exc:
        raise HTTPException(status_code=401, detail="Sign in again; your session has no organisation or user.") from exc
    user_id = ctx.user_id or request.state.user.get("userId")
    if not ctx.org_id or not user_id:
        raise HTTPException(status_code=401, detail="Sign in again; your session has no organisation or user.")
    return ctx.org_id, user_id



# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------
class GrepQuery(BaseModel):
    pattern: str = Field(..., min_length=1, max_length=MAX_PATTERN_CHARS)
    # False: the pattern is literal text. True: an extended regular expression.
    regex: bool = False
    caseSensitive: bool = False
    # Same shape and meaning as search: connector ids in ``apps``, collection ids in ``kb``.
    filters: dict[str, Any] | None = None
    # Shell-style globs on the record name (and a code file's path); a record
    # matching any is kept. Applied after the access check.
    paths: list[str] | None = Field(default=None, max_length=20)
    limit: int = Field(20, ge=1, le=100)
    contextLines: int = Field(2, ge=0, le=10)
    maxMatchesPerRecord: int = Field(5, ge=1, le=50)
    timeBudgetMs: int = Field(8000, ge=500, le=30000)

    _validate_filters = field_validator("filters")(validate_request_filters)

    @field_validator("pattern")
    @classmethod
    def _single_line(cls, value: str) -> str:
        if "\n" in value or "\r" in value or "\x00" in value:
            raise ValueError("pattern must be one line")
        return value


def _grep_command(body: GrepQuery) -> tuple[str, regex.Pattern[str]]:
    """The grep command the pattern-match layer runs, and the compiled pattern
    that finds the matching lines. 400 for a pattern it will not run.

    Python's ``regex`` decides which lines match. Records are stored as one
    line of JSON, so grep only picks the records worth reading, by a fixed
    string of plain characters that every match must contain and the storage
    writer never escapes (see ``ere_prefilter``); a pattern with no such text
    picks every record.
    """
    if body.regex and _BACKREFERENCE.search(body.pattern):
        raise HTTPException(status_code=400, detail="Back-references are not supported; remove \\1 style groups.")
    ignore_case = not body.caseSensitive
    try:
        compiled = regex.compile(
            body.pattern if body.regex else regex.escape(body.pattern),
            regex.IGNORECASE if ignore_case else 0,
        )
        if body.regex:
            prefilter, ignore_case = ere_prefilter(body.pattern, ignore_case=ignore_case)
        else:
            prefilter = literal_prefilter(body.pattern, ignore_case=ignore_case)
    except regex.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid regular expression: {exc}") from exc
    # The prefilter has only characters that are plain in an extended regular
    # expression, so it can be joined with the mark of records stored
    # compressed (always read, as grep cannot see their text).
    command = (
        f"grep -rc{'i' if ignore_case else ''}{'E' if prefilter else 'F'} "
        f"-e {shlex.quote(f'{prefilter}|{COMPRESSED_MARK}' if prefilter else '')} ."
    )
    ok, error = validate_pattern_match_command(command)
    if not ok:
        raise HTTPException(status_code=400, detail=f"This pattern cannot be searched: {error}")
    return command, compiled


async def _reachable_scope(
    graph_provider: IGraphDBProvider, org_id: str, user_id: str, filters: dict[str, Any],
) -> dict[str, Any] | None:
    """*filters* with ``apps`` and ``kb`` cut to the connectors and collections
    the caller reaches, or None when the request named some and none are left.
    A connector the caller cannot reach is never grepped, so nothing about it
    (that it exists, how long it took) can show in the answer."""
    named = {k: list(filters[k]) for k in ("apps", "kb") if filters.get(k)}
    if not named:
        return filters
    reach = {a["_key"] for a in await graph_provider.get_org_apps(org_id, active_only=False) if a.get("_key")}
    containers = await graph_provider.get_accessible_containers(user_id=user_id, org_id=org_id)
    # Grants to single records name no connector, and a fallback names no
    # containers at all: there the org's apps are the bound, and the access
    # check on each record is what decides.
    if containers is not None and not containers.fallback_reason and not containers.direct_records:
        groups = containers.record_group_ids | containers.root_group_ids
        rows = await graph_provider.get_nodes_by_field_in(
            CollectionNames.RECORD_GROUPS.value, "id", sorted(groups), ["id", "connectorId"],
        ) if groups else []
        reach &= set(containers.app_ids) | {r["connectorId"] for r in rows or [] if r.get("connectorId")}
    kept = {k: [i for i in ids if i in reach] for k, ids in named.items()}
    if not any(kept.values()):
        return None
    return {**filters, **kept}


def _path_matches(globs: list[str] | None, record: dict[str, Any]) -> bool:
    if not globs:
        return True
    names = [record.get("recordName") or "", record.get("filePath") or "", record.get("path") or ""]
    return any(fnmatch.fnmatchcase(n.lower(), g.lower()) for g in globs for n in names if n)


async def _load_blocks(blob_store: Any, vrid: str, org_id: str) -> list[dict[str, Any]]:  # noqa: ANN401
    stored = await record_artifact_reader(blob_store).read_record(vrid, org_id)
    containers = (stored or {}).get("block_containers") or {}
    blocks = containers.get("blocks") if isinstance(containers, dict) else None
    return blocks if isinstance(blocks, list) else []


@router.post("/search/grep", dependencies=[Depends(require_scopes(*SEARCH_SCOPES))])
async def grep(
    request: Request,
    body: GrepQuery,
    retrieval_service: RetrievalService = Depends(_retrieval_service),
    graph_provider: IGraphDBProvider = Depends(_graph_provider),
) -> JSONResponse:
    """Find records whose text contains a literal string or a regular expression."""
    org_id, user_id = _user(request)
    container = request.app.container
    log = container.logger()
    config_service: ConfigurationService = container.config_service()
    command, compiled = _grep_command(body)
    if not await check_pattern_match_eligible(config_service, log):
        raise HTTPException(
            status_code=501,
            detail="Exact-text search needs local file storage. Use search for this deployment.",
        )
    # One deadline for the whole request: finding the records, then reading them.
    started = time.monotonic()
    owner = object()  # this request's share of the line-matching workers
    deadline = started + body.timeBudgetMs / 1000
    try:
        filters = await _reachable_scope(graph_provider, org_id, user_id, body.filters or {})
        found: dict[str, dict[str, Any]] = {}
        coverage = PatternMatchCoverage()
        entries: list[dict[str, Any]] = []
        by_vrid: dict[str, dict[str, Any]] = {}
        if filters is not None:
            task = spawn_in_request(run_pattern_match_with_llm_grep(
                query=body.pattern, config_service=config_service, org_id=org_id, user_id=user_id,
                graph_provider=graph_provider, filters=filters, logger_instance=log,
                found=found, coverage=coverage, retrieval_service=None, commands=[command],  # no word index: it misses substrings
                max_records_per_connector=MAX_RECORDS_PER_APP, readable_only=True,
            ))
            raw = await await_pattern_match(
                task, log, wait_budget=body.timeBudgetMs * _FIND_SHARE / 1000, found_so_far=found, coverage=coverage,
            )
            entries = await merge_pattern_match_results(
                raw_records=raw, virtual_record_id_to_result=by_vrid, user_id=user_id, org_id=org_id,
                blob_store=retrieval_service.blob_store, graph_provider=graph_provider,
                is_multimodal_llm=False, logger_instance=log, filters=filters or None,
                config_service=config_service, coverage=coverage,
                max_records=MAX_RECORDS_PER_APP if body.paths else body.limit,
            )
        kept = [(e["virtual_record_id"], by_vrid[e["virtual_record_id"]]) for e in entries]
        kept = [(v, r) for v, r in kept if _path_matches(body.paths, r)]
        if len(kept) > body.limit:
            coverage.records_truncated = True  # readable records only, so this says nothing about the rest
            kept = kept[: body.limit]
        frontend_url = await resolve_frontend_url(config_service)
        gate = asyncio.Semaphore(_LOAD_CONCURRENCY)

        async def one(vrid: str, graph_rec: dict[str, Any]) -> dict[str, Any] | None:
            async with gate:
                try:
                    blocks = await asyncio.wait_for(
                        _load_blocks(retrieval_service.blob_store, vrid, org_id),
                        timeout=max(deadline - time.monotonic(), 0.001),
                    )
                except Exception:
                    log.warning("grep: could not read record %s", graph_rec.get("_key"), exc_info=True)
                    coverage.stop("some records could not be read in time")
                    return None
                matches, total, language, complete = await find_matches_bounded(
                    blocks, compiled,
                    context_lines=body.contextLines, max_matches=body.maxMatchesPerRecord, deadline=deadline,
                    owner=owner,
                )
            if not complete:
                coverage.stop("some records were not read in full within the time limit")
            record_id = graph_rec.get("_key") or graph_rec.get("id")
            url = None if graph_rec.get("hideWeburl") else resolve_weburl(graph_rec.get("webUrl"), frontend_url)
            for m in matches:
                m["citation"] = {"recordId": record_id, "blockIndex": m.pop("blockIndex"), "line": m["line"]}
            item: dict[str, Any] = {
                "recordId": record_id,
                "recordName": graph_rec.get("recordName"),
                "recordType": graph_rec.get("recordType"),
                "app": graph_rec.get("connectorName") or graph_rec.get("appName"),
                "webUrl": url,
                "matchCount": total,
                "matches": matches,
            }
            if language:
                item["language"] = language
            if not total and graph_rec.get("_match_snippet"):
                # Matched in the stored file (title or metadata), not in a passage line.
                item["preview"] = graph_rec["_match_snippet"]
            return item

        records = [r for r in await asyncio.gather(*(one(v, r) for v, r in kept)) if r is not None]
        note = render_pattern_match_coverage_note(coverage).strip()
        return JSONResponse(content={
            "status": "success",
            "pattern": body.pattern,
            "regex": body.regex,
            "totalRecords": len(records),
            "records": records,
            "coverage": {"complete": coverage.complete, **({"note": note} if note else {})},
        })
    except HTTPException:
        raise
    except Exception as exc:
        log.error("grep failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=action_failed("run this search")) from exc


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

