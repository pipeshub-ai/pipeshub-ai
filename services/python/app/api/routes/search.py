import asyncio
from pydantic import BaseModel
from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Optional, Dict, Any, List
from app.setups.query_setup import AppContainer
from app.utils.logger import create_logger
from app.config.configuration_service import ConfigurationService
from app.modules.retrieval.retrieval_service import RetrievalService
from app.modules.retrieval.retrieval_arango import ArangoService
from app.utils.query_transform import setup_query_transformation
from app.utils.llm import get_llm

logger = create_logger(__name__)

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
    container: AppContainer = request.app.container
    retrieval_service = await container.retrieval_service()
    return retrieval_service

async def get_arango_service(request: Request) -> ArangoService:
    container: AppContainer = request.app.container
    arango_service = await container.arango_service()
    return arango_service

async def get_config_service(request: Request) -> ConfigurationService:    
    container: AppContainer = request.app.container
    config_service = container.config_service()
    return config_service

@router.post("/search")
@inject
async def search(request: Request, body: SearchQuery, 
                retrieval_service: RetrievalService = Depends(get_retrieval_service),
                config_service: ConfigurationService=Depends(get_config_service),
                arango_service: ArangoService = Depends(get_arango_service)):
    """Perform semantic search across documents"""
    try:       
        llm = retrieval_service.llm
        if llm is None:
            llm = await retrieval_service.get_llm()
            if llm is None:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to initialize LLM service. LLM configuration is missing."
                )
            
        # Setup query transformation
        rewrite_chain, expansion_chain = setup_query_transformation(llm)

        # Run query transformations in parallel
        print("body ", body)
        rewritten_query, expanded_queries = await asyncio.gather(
            rewrite_chain.ainvoke(body.query),
            expansion_chain.ainvoke(body.query)
        )
        
        logger.info(f"Rewritten query: {rewritten_query}")
        logger.info(f"Expanded queries: {expanded_queries}")
        
        expanded_queries_list = [q.strip() for q in expanded_queries.split('\n') if q.strip()]

        queries = [rewritten_query.strip()] if rewritten_query.strip() else []
        queries.extend([q for q in expanded_queries_list if q not in queries])

        results = await retrieval_service.search_with_filters(
            queries=queries,
            org_id=request.state.user.get('orgId'),
            user_id=request.state.user.get('userId'),
            limit=body.limit,
            filter_groups=body.filters,
            arango_service=arango_service
        )
        return results
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}

