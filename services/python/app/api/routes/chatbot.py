import asyncio
from jinja2 import Template 
from pydantic import BaseModel
from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Optional, Dict, Any, List
from app.setups.query_setup import AppContainer
from app.utils.logger import create_logger
from app.modules.retrieval.retrieval_service import RetrievalService
from app.modules.retrieval.retrieval_arango import ArangoService
from app.config.configuration_service import ConfigurationService
from app.modules.qna.prompt_templates import qna_prompt
from app.utils.citations import process_citations
from app.utils.query_transform import setup_query_transformation
from app.utils.query_decompose import QueryDecompositionService
from app.modules.reranker.reranker import RerankerService
from app.utils.llm import get_llm

logger = create_logger(__name__)

router = APIRouter()

# Pydantic models
class ChatQuery(BaseModel):
    query: str
    limit: Optional[int] = 20
    previousConversations: List[Dict] = []
    useDecomposition: bool = True
    filters: Optional[Dict[str, Any]] = None
    retrieval_mode: Optional[str] = "HYBRID"

async def get_retrieval_service(request: Request) -> RetrievalService:
    # Retrieve the container from the app (set in your lifespan)
    container: AppContainer = request.app.container
    # Await the async resource provider to get the actual service instance
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

async def get_reranker_service(request: Request) -> RerankerService:
    container: AppContainer = request.app.container
    reranker_service = container.reranker_service()
    return reranker_service
     

@router.post("/chat")
@inject
async def askAI(request: Request, query_info: ChatQuery, 
                retrieval_service: RetrievalService=Depends(get_retrieval_service),
                arango_service: ArangoService=Depends(get_arango_service),
                config_service: ConfigurationService=Depends(get_config_service),
                reranker_service: RerankerService=Depends(get_reranker_service)):
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
                
        print("useDecomposition", query_info.useDecomposition)
        if query_info.useDecomposition:
            print("calling query decomposition")
            decomposition_service = QueryDecompositionService(llm)
            decomposition_result = await decomposition_service.decompose_query(query_info.query)
            decomposed_queries = decomposition_result["queries"]
            
            print("decomposed_queries", decomposed_queries)
            if not decomposed_queries:
                all_queries = [{'query': query_info.query}]
            else:
                all_queries = decomposed_queries

        else:
            all_queries = [{'query': query_info.query}]
        
        async def process_decomposed_query(query: str, org_id: str, user_id: str):
            rewrite_chain, expansion_chain = setup_query_transformation(llm)
                    
            # Run query transformations in parallel
            rewritten_query, expanded_queries = await asyncio.gather(
                rewrite_chain.ainvoke(query),
                expansion_chain.ainvoke(query)
            )

            logger.info(f"Rewritten query: {rewritten_query}")
            logger.info(f"Expanded queries: {expanded_queries}")
            
            expanded_queries_list = [q.strip() for q in expanded_queries.split('\n') if q.strip()]

            queries = [rewritten_query.strip()] if rewritten_query.strip() else []
            queries.extend([q for q in expanded_queries_list if q not in queries])
            seen = set()
            unique_queries = []
            for q in queries:
                if q.lower() not in seen:
                    seen.add(q.lower())
                    unique_queries.append(q)

            results = await retrieval_service.search_with_filters(
                queries=unique_queries,
                org_id=org_id,
                user_id=user_id,
                limit=query_info.limit,
                filter_groups=query_info.filters,
                arango_service=arango_service
            )
            logger.info("Results from the AI service received")
            # Format conversation history
            print(results, "formatted_results")
            # Get raw search results
            search_results = results.get('searchResults', [])

            return search_results
                
        # Execute all query processing in parallel
        org_id = request.state.user.get('orgId')
        user_id = request.state.user.get('userId')
        tasks = [process_decomposed_query(query_dict.get('query'), org_id, user_id) for query_dict in all_queries]
        all_search_results = await asyncio.gather(*tasks)
        
        # Flatten and deduplicate results based on document ID or other unique identifier
        # This assumes each result has an 'id' field - adjust according to your data structure
        flattened_results = []
        seen_ids = set()
        print(all_search_results, "all search results")
        for result_set in all_search_results:
            for result in result_set:
                print()
                flattened_results.append(result)
                # result_id = result.get('_id')
                # if result_id not in seen_ids:
                #     seen_ids.add(result_id)
                #     flattened_results.append(result)
        
        # Re-rank the combined results with the original query for better relevance
        if len(flattened_results) > 1:
            final_results = await reranker_service.rerank(
                query=query_info.query,  # Use original query for final ranking
                documents=flattened_results,
                top_k=query_info.limit
            )
        else:
            final_results = flattened_results
        
        print(final_results, "final_results")
        # Prepare the template with the final results
        template = Template(qna_prompt)
        rendered_form = template.render(
            query=query_info.query, 
            rephrased_queries=[],  # This keeps all query results for reference
            chunks=final_results
        )
        
        # Prepare messages for LLM
        messages = [
            {"role": "system", "content": "You are a enterprise questions answering expert"}
        ]
        
        # Add conversation history
        for conversation in query_info.previousConversations:
            if conversation.get('role') == 'user_query':
                messages.append({"role": "user", "content": conversation.get('content')})
            elif conversation.get('role') == 'bot_response':
                messages.append({"role": "assistant", "content": conversation.get('content')})
        
        # Add current query with context
        messages.append({"role": "user", "content": rendered_form})
        
        # Make async LLM call
        response = await llm.ainvoke(messages)
        print(response, "llm response")
        # Process citations and return response
        return process_citations(response, final_results)
        
    except Exception as e:
        logger.error(f"Error in askAI: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))