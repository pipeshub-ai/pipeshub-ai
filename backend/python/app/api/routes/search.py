import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Optional

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.agents.actions.knowledge_graph.ops.outside_dates import (
    OUTSIDE_DATES_SEARCH_LIMIT,
    outside_dates_payload,
    pick_outside_dates,
    quietly,
    shown_ids,
    wants_outside_dates,
)
from app.agents.actions.knowledge_graph.ops.time_range import parse_time_range
from app.agents.actions.knowledge_graph.ops.timeline import (
    PERIOD_SEARCH_LIMIT,
    auto_timeline_field,
    timeline_search_response,
    timeline_summaries_enabled,
)
from app.api.connection_limit import (
    hide_record_parents_outside_connection,
    hide_record_source_paths,
)
from app.api.middlewares.auth import (
    connection_limit,
    deny_service_tokens,
    require_scopes,
)
from app.config.configuration_service import ConfigurationService
from app.config.constants.service import OAuthScopes
from app.edition_config import resolve_llm_for_search
from app.exceptions.fastapi_responses import Status
from app.modules.retrieval.filter_schema import validate_request_filters
from app.modules.retrieval.retrieval_service import RetrievalService
from app.modules.retrieval.selection_scope import within_connection
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.model_identity.boundary import principal_from_request_user
from app.services.model_identity.runtime import call_scope
from app.services.model_identity.workload import Workload
from app.telemetry.event_buffer import record_event
from app.telemetry.identity import domain_from_email
from app.utils.opik import get_llm_invoke_config
from app.utils.query_transform import setup_query_transformation
from app.utils.request_deadline import deadline_after, search_deadline_seconds
from app.utils.stage_timer import StageTimer, collect_spans, format_spans
from app.utils.user_errors import provider_error_code
from app.utils.user_messages import action_failed

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

    from langchain_core.runnables import Runnable

    from app.containers.query import QueryAppContainer

router = APIRouter()

# The rewrite and expansion are optional, and the LLM's own timeout is minutes long;
# past this the search goes on with what the user typed.
QUERY_TRANSFORM_TIMEOUT_SECONDS = 20.0


# Pydantic models
DEFAULT_SEARCH_RESULT_LIMIT = 10
MAX_SEARCH_RESULT_LIMIT = 100


class SearchQuery(BaseModel):
    query: str
    # Most passages (entries of ``searchResults``) the response holds, however
    # many queries and expansions ran. Same default and bounds as the public API.
    limit: Optional[int] = Field(DEFAULT_SEARCH_RESULT_LIMIT, ge=1, le=MAX_SEARCH_RESULT_LIMIT)
    filters: Optional[dict[str, Any]] = {}
    # Optional date bounds on the source's own created / last-modified time,
    # named as on the knowledge-graph navigate endpoint (createdAfter, modifiedAfter):
    # "YYYY-MM-DD" (a whole day, UTC) or an ISO 8601 datetime with an offset.
    # Both ends are inclusive.
    createdAfter: Optional[str] = None
    createdBefore: Optional[str] = None
    modifiedAfter: Optional[str] = None
    modifiedBefore: Optional[str] = None
    # False searches the query exactly as sent: no model rewrite and no
    # expanded variants. For callers that write their own search queries,
    # such as an agent. Left out, the rewrite and expansion run as before.
    rewriteQuery: bool = True
    # Set, the search runs period by period over the span the matching date
    # bounds give (createdAfter/createdBefore for "created"), as the
    # assistant's timeline tool does, and returns the matches in date order,
    # each labelled with its period. ``limit`` is then the matches per period.
    # Left out, bounds on one date over more than a month run as a timeline
    # on that date, and a plain ranked search otherwise. "off" is always the
    # plain ranked search.
    timeline: Literal["created", "modified", "off"] | None = None

    _validate_filters = field_validator("filters")(validate_request_filters)


DEFAULT_RETRIEVE_LIMIT = 8
MAX_RETRIEVE_LIMIT = 50
# With maxChunksPerRecord set, a page of `limit` passages is often only a couple
# of records (knowledge search keeps up to four each). Read this many times
# `limit`, bounded, so the cap can fill the page from later records.
RETRIEVE_CAP_OVERFETCH = 4
MAX_RETRIEVE_CANDIDATES = 200


def _retrieve_fetch_limit(limit: int, max_per_record: int | None) -> int:
    """How many passages to read for a page of ``limit`` chunks."""
    if max_per_record is None:
        return limit
    return min(limit * RETRIEVE_CAP_OVERFETCH, MAX_RETRIEVE_CANDIDATES)


class RetrieveQuery(BaseModel):
    """``POST /retrieve``: ranked chunks with citations for a developer's own RAG or agent."""

    query: str = Field(min_length=1)
    limit: int = Field(DEFAULT_RETRIEVE_LIMIT, ge=1, le=MAX_RETRIEVE_LIMIT)
    filters: dict[str, Any] | None = {}
    createdAfter: str | None = None
    createdBefore: str | None = None
    modifiedAfter: str | None = None
    modifiedBefore: str | None = None
    maxChunksPerRecord: int | None = Field(None, ge=1, le=MAX_RETRIEVE_LIMIT)
    # Off by default, unlike /search: a retrieval call is latency-bound, and the
    # caller usually wrote the query itself.
    rewriteQuery: bool = False

    _validate_filters = field_validator("filters")(validate_request_filters)


# The parser names the assistant tool's parameters; this route receives the
# camelCase fields of the request body.
_REQUEST_FIELD_NAMES = {
    "created_after": "createdAfter",
    "created_before": "createdBefore",
    "modified_after": "modifiedAfter",
    "modified_before": "modifiedBefore",
}
_LABEL = "|".join(_REQUEST_FIELD_NAMES)
# Only the parser's labels: the one that opens the message, and the second
# bound of "x must be on or before y". A rejected value quoted later in the
# message is left as the caller sent it.
_LEADING_LABEL = re.compile(rf"^({_LABEL})\b")
_RANGE_LABEL = re.compile(rf"^(\w+ must be on or before )({_LABEL})( — )")


def _with_request_field_names(message: str) -> str:
    message = _LEADING_LABEL.sub(lambda m: _REQUEST_FIELD_NAMES[m.group(1)], message)
    return _RANGE_LABEL.sub(
        lambda m: m.group(1) + _REQUEST_FIELD_NAMES[m.group(2)] + m.group(3), message
    )


# The request fields that bound a timeline's span, and the time-range keys
# they parse to, by the date the timeline places records by.
_TIMELINE_SPAN = {
    "created": ("createdAfter", "createdBefore", "source_created_after_ms", "source_created_before_ms"),
    "modified": ("modifiedAfter", "modifiedBefore", "source_updated_after_ms", "source_updated_before_ms"),
}


def _search_time_range(body: "SearchQuery | RetrieveQuery") -> dict[str, int] | None:
    """The request's date bounds as the retrieval service's ``time_range``.

    Parsed by the same function as the assistant's search tool and the
    navigate endpoint, so all three accept and refuse the same dates. A bad
    date is a 400 that says what is wrong.
    """
    time_range, error = parse_time_range(
        created_after=body.createdAfter,
        created_before=body.createdBefore,
        modified_after=body.modifiedAfter,
        modified_before=body.modifiedBefore,
    )
    if error is not None:
        message = json.loads(error).get("message", "Invalid date filter.")
        raise HTTPException(status_code=400, detail=_with_request_field_names(message))
    return time_range


class SimilarDocumentQuery(BaseModel):
    document_id: str
    limit: Optional[int] = 5
    filters: Optional[dict[str, Any]] = None


class SearchRequest(BaseModel):
    query: str
    topK: int = 20
    filtersV1: list[dict[str, list[str]]]


async def get_retrieval_service(request: Request) -> RetrievalService:
    container: QueryAppContainer = request.app.container
    return await container.retrieval_service()

async def get_graph_provider(request: Request) -> IGraphDBProvider:
    container: QueryAppContainer = request.app.container
    return await container.graph_provider()


async def get_config_service(request: Request) -> ConfigurationService:
    container: QueryAppContainer = request.app.container
    return container.config_service()


async def _transform_query(
    chain: "Runnable", query: str, step: str, logger: "Logger", config: dict | None = None
) -> str | None:
    """The model's output for one query-transformation step, or None if it failed.

    Retrieval needs only the embedding model, so a rewrite the LLM could not do
    (provider down, content filter, timeout) must not cost the whole search.
    """
    try:
        call = chain.ainvoke(query, config=config) if config else chain.ainvoke(query)
        return await asyncio.wait_for(call, timeout=QUERY_TRANSFORM_TIMEOUT_SECONDS)
    except HTTPException:
        raise
    except Exception as exc:
        # Provider errors can quote the prompt, so only the error's kind is logged.
        logger.warning(
            "Query %s failed (%s, %s); continuing the search without it",
            step,
            type(exc).__name__,
            provider_error_code(exc),
        )
        return None


def _queries_for_search(
    original: str, rewritten: str | None, expanded: str | None
) -> list[str]:
    """The queries to retrieve with: what the user typed, then the model's versions."""
    # The typed query is always searched. A rewrite can paraphrase away an
    # exact identifier (an invoice number, a ticket key, a made-up word), and
    # then no leg of the hybrid search matches the one file that holds it.
    queries: list[str] = []
    for candidate in [original, rewritten or "", *(expanded or "").split("\n")]:
        q = candidate.strip()
        if q and q not in queries:
            queries.append(q)
    return queries


def _typed_text_pattern(typed: str) -> re.Pattern[str] | None:
    words = typed.split()
    if not words:
        return None
    return re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, words)) + r"(?!\w)", re.IGNORECASE)


def _apply_result_limit(results: dict[str, Any], limit: int, typed: str = "") -> dict[str, Any]:
    """Cut ``searchResults`` to ``limit`` passages, best first, and drop what they no longer cite.

    Retrieval over several queries, and the expansion of a matched section into
    its passages, can return more than ``limit``; ranking is left as it is.
    A passage holding ``typed`` word for word is kept before one that does not.
    """
    hits = results.get("searchResults")
    if not isinstance(hits, list) or len(hits) <= limit:
        return results
    # Children of an expanded section share its score; a matched passage goes
    # before its own context, so the cut never keeps a neighbour over the match.
    def ranked(h: dict | None) -> tuple:
        return (-((h or {}).get("score") or 0), (h or {}).get("distanceFromMatch") or 0)

    hits = sorted(hits, key=ranked)
    pattern = _typed_text_pattern(typed)
    if pattern is not None:
        # Hybrid scores are RRF ranks within each rewritten query, so passages
        # that rank well for every rewrite outscore the one keyword hit for a
        # code or name the person typed, and the cut dropped exactly that file.
        def holds_typed(h: dict | None) -> bool:
            content = (h or {}).get("content")
            return isinstance(content, str) and pattern.search(content) is not None

        def section_of(h: dict | None) -> tuple:
            h = h or {}
            record_id = (h.get("metadata") or {}).get("recordId")
            group = h.get("block_group_index")
            # Equal RRF scores are common, so the score names a section only when
            # retrieval did not say which group a passage came from.
            return (record_id, "group", group) if group is not None else (record_id, "score", h.get("score"))

        typed_hits = {id(h) for h in hits if holds_typed(h)}
        # A neighbour holding the text brings its section's match with it, and
        # the stable sort keeps that match ahead of the neighbour.
        typed_sections = {
            section_of(h) for h in hits
            if id(h) in typed_hits and ((h or {}).get("distanceFromMatch") or 0) > 0
        }

        def promoted(h: dict | None) -> bool:
            return id(h) in typed_hits or (
                (h or {}).get("distanceFromMatch") == 0 and section_of(h) in typed_sections
            )

        chosen = sorted(hits, key=lambda h: not promoted(h))[:limit]
        chosen_ids = {id(h) for h in chosen}
        kept = [h for h in hits if id(h) in chosen_ids]
        dropped = [h for h in hits if id(h) not in chosen_ids]
    else:
        kept, dropped = hits[:limit], hits[limit:]

    def record_id(hit: dict | None) -> object:
        return ((hit or {}).get("metadata") or {}).get("recordId")

    kept_ids = {record_id(hit) for hit in kept}
    omitted = dict(results.get("omittedPassages") or {})
    for hit in dropped:
        if record_id(hit) in kept_ids:
            omitted[record_id(hit)] = omitted.get(record_id(hit), 0) + 1
    results["searchResults"] = kept
    if omitted:
        results["omittedPassages"] = {k: v for k, v in omitted.items() if k in kept_ids}
    if isinstance(results.get("records"), list):
        results["records"] = [r for r in results["records"] if (r or {}).get("_key") in kept_ids]
    if isinstance(results.get("virtual_to_record_map"), dict):
        results["virtual_to_record_map"] = {
            vid: r for vid, r in results["virtual_to_record_map"].items()
            if (r or {}).get("_key") in kept_ids
        }
    return results


async def _rewritten_queries(
    request: Request, retrieval_service: RetrievalService, query: str, logger: "Logger"
) -> list[str]:
    """The query rewritten and expanded by the model, for retrieval."""
    llm = await resolve_llm_for_search(request, retrieval_service)
    rewrite_chain, expansion_chain = setup_query_transformation(llm)

    # OpikTracer is invoke-config only (not a constructor callback); without
    # this, rewrite/expand LLM calls never appear in Opik.
    invoke_config = get_llm_invoke_config(llm)

    # Run query transformations in parallel
    rewritten_query, expanded_queries = await asyncio.gather(
        _transform_query(rewrite_chain, query, "rewrite", logger, invoke_config),
        _transform_query(expansion_chain, query, "expansion", logger, invoke_config),
    )

    queries = _queries_for_search(query, rewritten_query, expanded_queries)
    logger.debug(
        "Query transformed: rewritten_len=%d queries=%d",
        len(rewritten_query or ""), len(queries),
    )
    return queries


def _timeline_choice(requested: str | None, time_range: dict[str, int] | None) -> tuple[str | None, bool]:
    """The date the search runs as a timeline by, or None, and whether it
    was chosen here rather than by the caller."""
    if requested == "off" or not timeline_summaries_enabled():
        return None, False
    if requested:
        return requested, False
    date_field = auto_timeline_field(time_range)
    return date_field, date_field is not None


@dataclass(frozen=True)
class _TimelineSpan:
    date_field: str
    start_ms: int | None
    end_ms: int | None
    other_bounds: dict[str, int]
    """Bounds on the other date, kept on every search of the timeline."""


def _timeline_span(date_field: str, time_range: dict[str, int] | None) -> _TimelineSpan:
    """The span the request's bounds on ``date_field`` give, checked before
    any search runs. An inverted range was already refused by the parser."""
    after_field, _, after_key, before_key = _TIMELINE_SPAN[date_field]
    bounds = time_range or {}
    start_ms = bounds.get(after_key)
    # A timeline ends today at the latest, so its start cannot be later.
    if start_ms is not None and start_ms > time.time() * 1000:
        raise HTTPException(
            status_code=400,
            detail=f"{after_field} is in the future, so the timeline has no span to search.",
        )
    return _TimelineSpan(
        date_field=date_field,
        start_ms=start_ms,
        end_ms=bounds.get(before_key),
        other_bounds={k: v for k, v in bounds.items() if k not in (after_key, before_key)},
    )


async def _timeline_search(
    span: _TimelineSpan,
    period_limit: int,
    run_search: "Callable[[dict[str, int] | None, int | None], Awaitable[dict[str, Any]]]",
    logger: "Logger",
    *,
    automatic: bool,
) -> dict[str, Any]:
    """The timeline tool's period-by-period search, each search being
    ``run_search``: the plain search, with its permission check and filters."""

    async def search_span(bounds: dict[str, int] | None, limit: int) -> dict[str, Any]:
        return await run_search({**span.other_bounds, **(bounds or {})} or None, limit)

    results = await timeline_search_response(
        search_span,
        start_ms=span.start_ms,
        end_ms=span.end_ms,
        date_field=span.date_field,
        period_limit=period_limit,
        automatic=automatic,
        log=logger,
    )
    if results is None:
        raise HTTPException(status_code=500, detail=action_failed("run this search"))
    return results


# Said when a search is stopped at SEARCH_DEADLINE_SECONDS. A vector store that
# timed out answers SEARCH_INDEX_UNAVAILABLE before this, from retrieval. One
# fixed sentence: Node passes a 503's message on only when it is listed word for
# word in READER_WRITTEN_MESSAGES (backend/nodejs/apps/src/libs/errors/
# reader-friendly.ts), so the deadline itself is logged, not said.
SEARCH_TIMED_OUT_MESSAGE = (
    "This search took too long, so it was stopped and no results are shown. "
    "Try again, or narrow it with filters or a shorter date range."
)


def _timed_out_response(seconds: float) -> dict[str, Any]:
    return {
        "searchResults": [],
        "records": [],
        "status": Status.SEARCH_TIMED_OUT.value,
        "status_code": 503,
        "message": SEARCH_TIMED_OUT_MESSAGE,
    }


# A date-bounded search that found nothing in its dates answers 404 (no
# accessible records); with matches from outside them it answers 200.
_NOTHING_IN_DATES_STATUS = 404
_NOTHING_IN_DATES_MESSAGE = (
    "Nothing in your dates matched. The best matches written outside them are "
    "in outsideDates."
)


def _with_outside_dates(
    results: dict[str, Any], unbounded: Any, time_range: dict[str, int],  # noqa: ANN401
) -> dict[str, Any]:
    """``results`` with ``outsideDates``: the best matches of ``unbounded``,
    the same search without dates, that the bounds ``time_range`` left out
    (``ops/outside_dates``). Unchanged when there are none or either search
    failed."""
    status_code = results.get("status_code", 500)
    if status_code not in (200, _NOTHING_IN_DATES_STATUS):
        return results
    shown_virtual_ids, shown_record_ids = shown_ids(
        results.get("searchResults") or [], results.get("virtual_to_record_map"),
    )
    picked = pick_outside_dates(
        unbounded, time_range, shown_virtual_ids=shown_virtual_ids, shown_record_ids=shown_record_ids,
    )
    if not picked:
        return results
    payload = {**results, "outsideDates": outside_dates_payload(picked, unbounded)}
    if status_code == _NOTHING_IN_DATES_STATUS:
        payload.update(status="success", status_code=200, message=_NOTHING_IN_DATES_MESSAGE)
    return payload


async def _with_search_without_dates(
    searched: "Awaitable[dict[str, Any]]",
    run_search: "Callable[[dict[str, int] | None, int | None], Awaitable[dict[str, Any]]]",
    time_range: dict[str, int] | None,
    logger: "Logger",
) -> dict[str, Any]:
    """``searched``, a ranked or timeline search. With date bounds the same
    search runs once more without them, one ranked search alongside, and the
    best matches the dates left out are added (``outsideDates``); a failure of
    that search leaves the result as it would have been."""
    if not time_range or not wants_outside_dates(time_range):
        return await searched
    results, unbounded = await asyncio.gather(
        searched, quietly(run_search(None, OUTSIDE_DATES_SEARCH_LIMIT), logger),
    )
    return _with_outside_dates(results, unbounded, time_range)


@router.post("/search", dependencies=[Depends(require_scopes(OAuthScopes.SEMANTIC_WRITE))])
@inject
async def search(
    request: Request,
    body: SearchQuery,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
)-> JSONResponse :
    """Perform semantic search across documents"""
    container = request.app.container
    logger = container.logger()
    time_range = _search_time_range(body)
    timeline, automatic = _timeline_choice(body.timeline, time_range)
    span = _timeline_span(timeline, time_range) if timeline else None
    timer = StageTimer()
    limit = body.limit or DEFAULT_SEARCH_RESULT_LIMIT
    # Inside the MCP server's wait for an answer (PIPESHUB_MCP_TIMEOUT_MS), so a
    # slow search still answers: the vector search fits its budget inside it
    # (app/services/vector_db/search_retry.py), and the rest is stopped here.
    deadline = search_deadline_seconds()
    guard = asyncio.timeout(deadline)
    stages: dict[str, list[float]] = {}
    try:
        with collect_spans() as stages, deadline_after(deadline):
            async with guard, call_scope(
                principal_from_request_user(request.state.user),
                Workload.SEARCH,
                config_service=container.config_service(),
                container=container,
            ):
                if body.rewriteQuery:
                    queries = await _rewritten_queries(request, retrieval_service, body.query, logger)
                else:
                    queries = _queries_for_search(body.query, None, None)
                timer.mark("query_transform")
                filter_groups = within_connection(body.filters, connection_limit(request.state.user))

                async def run_search(bounds: dict[str, int] | None, limit: int | None) -> dict[str, Any]:
                    return await retrieval_service.search_with_filters(
                        queries=queries,
                        org_id=request.state.user.get("orgId"),
                        user_id=request.state.user.get("userId"),
                        limit=limit,
                        filter_groups=filter_groups,
                        knowledge_search=True,
                        time_range=bounds,
                    )

                if span is None:
                    searched = run_search(time_range, limit)
                else:
                    # ``limit`` is the matches per period when the caller set it. A
                    # caller who did not ask for a timeline meant it for the whole
                    # search, so it does not raise each period's above the default.
                    period_limit = body.limit if "limit" in body.model_fields_set and body.limit else PERIOD_SEARCH_LIMIT
                    if automatic:
                        period_limit = min(period_limit, PERIOD_SEARCH_LIMIT)
                    searched = _timeline_search(span, period_limit, run_search, logger, automatic=automatic)
                results = await _with_search_without_dates(searched, run_search, time_range, logger)
                if span is None:
                    # A timeline's ``limit`` is per period, so only a ranked search is cut.
                    results = _apply_result_limit(results, limit, body.query)
                await hide_record_parents_outside_connection(
                    request, graph_provider, request.state.user.get("orgId"), request.state.user.get("userId"), results,
                )
                hide_record_source_paths(request, results)
                timer.mark("retrieval")
                timer.emit(
                    logger, "search", detail=format_spans(stages),
                    rewrite="on" if body.rewriteQuery else "off", queries=len(queries),
                    timeline=timeline, timeline_auto=automatic or None,
                )
                custom_status_code = results.get("status_code", 500)
                logger.info(f"Custom status code: {custom_status_code}")

                _su_email = request.state.user.get("email")
                record_event("search_performed", {
                    "orgId": request.state.user.get("orgId"),
                    "userId": request.state.user.get("userId"),
                    "email": _su_email,
                    "domain": domain_from_email(_su_email),
                    "status_code": custom_status_code,
                    "num_queries": len(queries),
                    "search_type": "timeline" if timeline else "search",
                })

                return JSONResponse(status_code=custom_status_code, content=results)

    except HTTPException:
        raise
    except TimeoutError as e:
        if deadline is None or not guard.expired():
            # Not this route's deadline: a step's own timeout that got out.
            logger.error("Search failed: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=action_failed("run this search")) from e
        timer.mark("retrieval")
        timer.emit(logger, "search", detail=format_spans(stages), timed_out=f"{deadline:g}s")
        logger.warning("Search stopped at its %gs deadline", deadline)
        return JSONResponse(status_code=503, content=_timed_out_response(deadline))
    except Exception as e:
        logger.error("Search failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=action_failed("run this search")) from e


@router.get("/health", dependencies=[Depends(deny_service_tokens)])
async def health_check() -> dict[str, str]:
    """Health check endpoint"""
    return {"status": "healthy"}


_EMPTY_RETRIEVAL_STATUSES = {
    Status.EMPTY_RESPONSE.value,
    Status.ACCESSIBLE_RECORDS_NOT_FOUND.value,
    Status.VECTOR_DB_EMPTY.value,
}


def _retrieve_chunks(results: dict[str, Any], max_per_record: int | None) -> list[dict[str, Any]]:
    """The search response as a flat, ranked chunk list with a citation each."""
    records = {r.get("_key"): r for r in results.get("records") or [] if isinstance(r, dict)}
    per_record: dict[str, int] = {}
    chunks: list[dict[str, Any]] = []
    # Ordered by score only, an expanded section's passages share its score in
    # document order; the passage that matched (distanceFromMatch 0) goes first
    # so a per-record cap keeps it over its neighbours.
    hits = sorted(
        results.get("searchResults") or [],
        key=lambda h: (-(h.get("score") or 0.0), h.get("distanceFromMatch") or 0),
    )
    for hit in hits:
        md = hit.get("metadata") or {}
        record_id = md.get("recordId")
        if not record_id or not hit.get("content"):
            continue
        if max_per_record is not None and per_record.get(record_id, 0) >= max_per_record:
            continue
        per_record[record_id] = per_record.get(record_id, 0) + 1
        doc = records.get(record_id) or {}
        # Object-store records (S3, Azure Blob/Files, GCS) store a console URL
        # naming bucket and key; search links to the record page instead.
        hidden = bool(md.get("hideWeburl") or doc.get("hideWeburl"))
        record_page = f"/record/{record_id}"
        chunks.append({
            # Stable across calls for the same indexed block, so callers can dedupe and cache.
            "id": f"{hit.get('virtual_record_id') or md.get('virtualRecordId')}:{hit.get('block_index')}",
            "text": hit["content"],
            "score": hit.get("score"),
            "record": {
                "recordId": record_id,
                "name": doc.get("recordName") or md.get("recordName"),
                "connector": doc.get("connectorName") or md.get("connector"),
                "mimeType": md.get("mimeType"),
                "updatedAt": md.get("updatedAt"),
            },
            "citation": {
                # Built by app/utils/text_fragments; never rebuilt here.
                "url": record_page if hidden else md.get("webUrl"),
                "recordUrl": record_page if hidden else (doc.get("webUrl") or md.get("webUrl")),
                "pageNum": md.get("pageNum"),
                "blockIndex": hit.get("block_index"),
            },
        })
    return chunks


@router.post("/retrieve", dependencies=[Depends(require_scopes(OAuthScopes.SEMANTIC_WRITE))])
@inject
async def retrieve(
    request: Request,
    body: RetrieveQuery,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
) -> JSONResponse:
    """Permission-filtered chunks with citations. Unlike /search, nothing is
    stored: no search history and no citation documents."""
    container = request.app.container
    logger = container.logger()
    time_range = _search_time_range(body)
    user = request.state.user
    fetch_limit = _retrieve_fetch_limit(body.limit, body.maxChunksPerRecord)
    try:
        async with call_scope(
            principal_from_request_user(user),
            Workload.SEARCH,
            config_service=container.config_service(),
            container=container,
        ):
            if body.rewriteQuery:
                queries = await _rewritten_queries(request, retrieval_service, body.query, logger)
            else:
                queries = _queries_for_search(body.query, None, None)
            results = await retrieval_service.search_with_filters(
                queries=queries,
                org_id=user.get("orgId"),
                user_id=user.get("userId"),
                limit=fetch_limit,
                filter_groups=within_connection(body.filters, connection_limit(user)),
                knowledge_search=True,
                time_range=time_range,
            )
            if body.maxChunksPerRecord is None:
                results = _apply_result_limit(results, body.limit, body.query)
            # With a cap, nothing is cut here: _retrieve_chunks caps each record
            # over every candidate, then the page is cut to `limit`. Cutting first
            # lets one expanded section fill the window and leaves the page short.
            await hide_record_parents_outside_connection(
                request, graph_provider, user.get("orgId"), user.get("userId"), results,
            )
            hide_record_source_paths(request, results)
            status_code = results.get("status_code", 500)
            record_event("retrieve_performed", {
                "orgId": user.get("orgId"),
                "userId": user.get("userId"),
                "domain": domain_from_email(user.get("email")),
                "status_code": status_code,
                "num_queries": len(queries),
            })
            if not results.get("searchResults"):
                # Nothing matched, nothing accessible, nothing indexed yet: still an
                # answer. A failure (index or permission check down, bad filter) is not.
                if status_code == 200 or results.get("status") in _EMPTY_RETRIEVAL_STATUSES:
                    return JSONResponse(status_code=200, content={
                        "chunks": [],
                        # A source that did not run makes "nothing found" unreliable.
                        "incomplete": bool(results.get("searchIncomplete")),
                        "message": results.get("message"),
                    })
                return JSONResponse(status_code=status_code, content={"message": results.get("message")})
            return JSONResponse(status_code=200, content={
                "chunks": _retrieve_chunks(results, body.maxChunksPerRecord)[: body.limit],
                "incomplete": bool(results.get("searchIncomplete")),
            })
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Retrieve failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=action_failed("retrieve passages")) from e
