import asyncio
from typing import Any, Dict, List, Optional

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config.configuration_service import ConfigurationService
from app.connectors.services.base_arango_service import BaseArangoService
from app.containers.query import QueryAppContainer
from app.modules.retrieval.retrieval_service import RetrievalService
from app.utils.query_transform import setup_query_transformation

router = APIRouter()


# Pydantic models
class SearchQuery(BaseModel):
    query: str
    limit: Optional[int] = 5
    filters: Optional[Dict[str, Any]] = {}


class SimilarDocumentQuery(BaseModel):
    document_id: str
    limit: Optional[int] = 5
    filters: Optional[Dict[str, Any]] = None


class SearchRequest(BaseModel):
    query: str
    topK: int = 20
    filtersV1: List[Dict[str, List[str]]]


async def get_retrieval_service(request: Request) -> RetrievalService:
    container: QueryAppContainer = request.app.container
    retrieval_service = await container.retrieval_service()
    return retrieval_service


async def get_arango_service(request: Request) -> BaseArangoService:
    container: QueryAppContainer = request.app.container
    arango_service = await container.arango_service()
    return arango_service


async def get_config_service(request: Request) -> ConfigurationService:
    container: QueryAppContainer = request.app.container
    config_service = container.config_service()
    return config_service


@router.post("/search")
@inject
async def search(
    request: Request,
    body: SearchQuery,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    arango_service: BaseArangoService = Depends(get_arango_service),
)-> JSONResponse :
    """Perform semantic search across documents"""
    try:
        container = request.app.container
        logger = container.logger()
        llm = retrieval_service.llm
        if llm is None:
            llm = await retrieval_service.get_llm_instance()
            if llm is None:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to initialize LLM service. LLM configuration is missing.",
                )

        # Extract KB IDs from filters if present
        kb_ids = body.filters.get('kb') if body.filters else None
        updated_filters = body.filters
        # Validate KB IDs if provided
        if kb_ids:
            logger.info(f"Search request with KB filtering: {kb_ids}")

            # Validate KB access
            kb_validation = await arango_service.validate_user_kb_access(
                user_id=request.state.user.get("userId"),
                org_id=request.state.user.get("orgId"),
                kb_ids=kb_ids
            )

            accessible_kbs = kb_validation.get("accessible", [])
            inaccessible_kbs = kb_validation.get("inaccessible", [])

            if not accessible_kbs:
                logger.warning(f"⚠️ User has no access to requested KBs: {kb_ids}")
                return JSONResponse(
                    status_code=403,
                    content={
                        "searchResults": [],
                        "records": [],
                        "status": "ACCESS_DENIED",
                        "status_code": 403,
                        "message": "You don't have access to any of the specified knowledge bases.",
                        "inaccessible_kbs": inaccessible_kbs
                    }
                )

            if inaccessible_kbs:
                logger.warning(f"⚠️ Some KBs are inaccessible: {inaccessible_kbs}")

            # Update filters with only accessible KBs
            updated_filters = body.filters.copy() if body.filters else {}
            updated_filters['kb'] = accessible_kbs
            logger.info(f"✅ Using accessible KBs for search: {accessible_kbs}")

        # Setup query transformation
        rewrite_chain, expansion_chain = setup_query_transformation(llm)

        # Run query transformations in parallel
        rewritten_query, expanded_queries = await asyncio.gather(
            rewrite_chain.ainvoke(body.query), expansion_chain.ainvoke(body.query)
        )

        logger.debug(f"Rewritten query: {rewritten_query}")
        logger.debug(f"Expanded queries: {expanded_queries}")

        expanded_queries_list = [
            q.strip() for q in expanded_queries.split("\n") if q.strip()
        ]

        queries = [rewritten_query.strip()] if rewritten_query.strip() else []
        queries.extend([q for q in expanded_queries_list if q not in queries])

        results = await retrieval_service.search_with_filters(
            queries=queries,
            org_id=request.state.user.get("orgId"),
            user_id=request.state.user.get("userId"),
            limit=body.limit,
            filter_groups=updated_filters,
            arango_service=arango_service,
            knowledge_search=True,
        )
        custom_status_code = results.get("status_code", 500)
        logger.info(f"Custom status code: {custom_status_code}")
        if kb_ids:
            results["kb_filtering"] = {
                "requested_kbs": kb_ids,
                "accessible_kbs": accessible_kbs,
                "inaccessible_kbs": inaccessible_kbs,
                "total_requested": len(kb_ids),
                "total_accessible": len(accessible_kbs)
            }

        logger.info(f"Results: {results}")

        return JSONResponse(status_code=custom_status_code, content=results)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health_check() -> Dict[str, str]:
    """Health check endpoint"""
    return {"status": "healthy"}
