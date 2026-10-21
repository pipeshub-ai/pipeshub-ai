import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Optional

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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
from app.api.middlewares.auth import deny_service_tokens, require_scopes
from app.config.configuration_service import ConfigurationService
from app.config.constants.service import OAuthScopes
from app.edition_config import resolve_llm_for_search
from app.modules.retrieval.retrieval_service import RetrievalService
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.telemetry.event_buffer import record_event
from app.telemetry.identity import domain_from_email
from app.utils.opik import get_llm_invoke_config
from app.utils.query_transform import setup_query_transformation
from app.utils.stage_timer import StageTimer
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
class SearchQuery(BaseModel):
    query: str
    limit: Optional[int] = 5
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


def _search_time_range(body: SearchQuery) -> dict[str, int] | None:
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
    """The queries to retrieve with; what the user typed covers a failed step."""
    original = original.strip()
    # A rewrite that failed is replaced by the original; one that came back
    # blank is not, so the expansions stand alone as they always have.
    first = original if rewritten is None else rewritten.strip()
    queries = [first] if first else []
    for line in (expanded or "").split("\n"):
        q = line.strip()
        if q and q not in queries:
            queries.append(q)
    if not queries and original:
        queries = [original]
    return queries


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

    logger.debug(f"Rewritten query: {rewritten_query}")
    logger.debug(f"Expanded queries: {expanded_queries}")

    return _queries_for_search(query, rewritten_query, expanded_queries)


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
    try:
        if body.rewriteQuery:
            queries = await _rewritten_queries(request, retrieval_service, body.query, logger)
        else:
            queries = _queries_for_search(body.query, None, None)
        timer.mark("query_transform")

        async def run_search(bounds: dict[str, int] | None, limit: int | None) -> dict[str, Any]:
            return await retrieval_service.search_with_filters(
                queries=queries,
                org_id=request.state.user.get("orgId"),
                user_id=request.state.user.get("userId"),
                limit=limit,
                filter_groups=body.filters,
                knowledge_search=True,
                time_range=bounds,
            )

        if span is None:
            searched = run_search(time_range, body.limit)
        else:
            # ``limit`` is the matches per period when the caller set it. A
            # caller who did not ask for a timeline meant it for the whole
            # search, so it does not raise each period's above the default.
            period_limit = body.limit if "limit" in body.model_fields_set and body.limit else PERIOD_SEARCH_LIMIT
            if automatic:
                period_limit = min(period_limit, PERIOD_SEARCH_LIMIT)
            searched = _timeline_search(span, period_limit, run_search, logger, automatic=automatic)
        results = await _with_search_without_dates(searched, run_search, time_range, logger)
        timer.mark("retrieval")
        timer.emit(
            logger, "search",
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
    except Exception as e:
        logger.error("Search failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=action_failed("run this search")) from e


@router.get("/health", dependencies=[Depends(deny_service_tokens)])
async def health_check() -> dict[str, str]:
    """Health check endpoint"""
    return {"status": "healthy"}
