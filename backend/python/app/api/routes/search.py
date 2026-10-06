import asyncio
from typing import TYPE_CHECKING, Any, Optional

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.middlewares.auth import deny_service_tokens, require_scopes
from app.config.configuration_service import ConfigurationService
from app.config.constants.service import OAuthScopes
from app.edition_config import resolve_llm_for_search
from app.modules.retrieval.retrieval_service import RetrievalService
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.telemetry.event_buffer import record_event
from app.telemetry.identity import domain_from_email
from app.utils.query_transform import setup_query_transformation
from app.utils.user_errors import provider_error_code
from app.utils.user_messages import action_failed

if TYPE_CHECKING:
    from logging import Logger

    from langchain_core.runnables import Runnable

    from app.containers.query import QueryAppContainer

router = APIRouter()


# Pydantic models
class SearchQuery(BaseModel):
    query: str
    limit: Optional[int] = 5
    filters: Optional[dict[str, Any]] = {}


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
    chain: "Runnable", query: str, step: str, logger: "Logger"
) -> str | None:
    """The model's output for one query-transformation step, or None if it failed.

    Retrieval needs only the embedding model, so a rewrite the LLM could not do
    (provider down, content filter, timeout) must not cost the whole search.
    """
    try:
        return await chain.ainvoke(query)
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
    expanded_queries_list = [
        q.strip() for q in (expanded or "").split("\n") if q.strip()
    ]
    queries.extend([q for q in expanded_queries_list if q not in queries])
    if not queries and original:
        queries = [original]
    return queries


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
    try:
        llm = await resolve_llm_for_search(request, retrieval_service)

        # Extract KB IDs from filters if present
        updated_filters = body.filters

        # Setup query transformation
        rewrite_chain, expansion_chain = setup_query_transformation(llm)

        # Run query transformations in parallel
        rewritten_query, expanded_queries = await asyncio.gather(
            _transform_query(rewrite_chain, body.query, "rewrite", logger),
            _transform_query(expansion_chain, body.query, "expansion", logger),
        )

        logger.debug(f"Rewritten query: {rewritten_query}")
        logger.debug(f"Expanded queries: {expanded_queries}")

        queries = _queries_for_search(body.query, rewritten_query, expanded_queries)
        results = await retrieval_service.search_with_filters(
            queries=queries,
            org_id=request.state.user.get("orgId"),
            user_id=request.state.user.get("userId"),
            limit=body.limit,
            filter_groups=updated_filters,
            knowledge_search=True,
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
            "search_type": "search",
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
