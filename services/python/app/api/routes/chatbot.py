import asyncio
from typing import Any, Dict, List, Optional

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from jinja2 import Template
from pydantic import BaseModel

from app.config.configuration_service import ConfigurationService
from app.config.utils.named_constants.arangodb_constants import (
    AccountType,
    CollectionNames,
)
from app.modules.qna.prompt_templates import qna_prompt
from app.modules.reranker.reranker import RerankerService
from app.modules.retrieval.retrieval_arango import ArangoService
from app.modules.retrieval.retrieval_service import RetrievalService
from app.setups.query_setup import AppContainer
from app.utils.citations import process_citations
from app.utils.query_decompose import QueryDecompositionService
from app.utils.query_transform import setup_query_transformation

router = APIRouter()


# Pydantic models
class ChatQuery(BaseModel):
    query: str
    limit: Optional[int] = 50
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
async def askAI(
    request: Request,
    query_info: ChatQuery,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    arango_service: ArangoService = Depends(get_arango_service),
    reranker_service: RerankerService = Depends(get_reranker_service),
):
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

        logger.debug(f"useDecomposition {query_info.useDecomposition}")
        if query_info.useDecomposition:
            decomposition_service = QueryDecompositionService(llm, logger=logger)
            decomposition_result = await decomposition_service.decompose_query(
                query_info.query
            )
            decomposed_queries = decomposition_result["queries"]

            logger.debug(f"decomposed_queries {decomposed_queries}")
            if not decomposed_queries:
                all_queries = [{"query": query_info.query}]
            else:
                all_queries = decomposed_queries

        else:
            all_queries = [{"query": query_info.query}]

        async def process_decomposed_query(query: str, org_id: str, user_id: str):
            rewrite_chain, expansion_chain = setup_query_transformation(llm)

            # Run query transformations in parallel
            rewritten_query, expanded_queries = await asyncio.gather(
                rewrite_chain.ainvoke(query), expansion_chain.ainvoke(query)
            )

            logger.debug(f"Rewritten query: {rewritten_query}")
            logger.debug(f"Expanded queries: {expanded_queries}")

            expanded_queries_list = [
                q.strip() for q in expanded_queries.split("\n") if q.strip()
            ]

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
                arango_service=arango_service,
            )
            logger.info("Results from the AI service received")
            # Format conversation history
            logger.debug(f"formatted_results: {results}")
            # Get raw search results
            # search_results = results.get("searchResults", [])

            return results

        # Execute all query processing in parallel
        org_id = request.state.user.get('orgId')
        user_id = request.state.user.get('userId')
        send_user_info = request.query_params.get('sendUserInfo', True)

        tasks = [
            process_decomposed_query(query_dict.get("query"), org_id, user_id)
            for query_dict in all_queries
        ]
        all_results = await asyncio.gather(*tasks)

        # Flatten and deduplicate results based on document ID or other unique identifier
        # This assumes each result has an 'id' field - adjust according to your data structure
        flattened_results = []
        seen_ids = set()
        for result_set in all_results:
            status_code = result_set.get("status_code", 500)

            if status_code in [202, 500, 503]:
                return JSONResponse(
                    status_code=status_code,
                    content={
                        "status": result_set.get("status", "error"),
                        "message": result_set.get("message", "No results found"),
                        "searchResults": [],
                        "records": []
                    }
                )

            search_result_set = result_set.get("searchResults", [])
            for result in search_result_set:
                logger.debug("==================")
                logger.debug("==================")
                logger.debug(f"result: {result}")
                logger.debug("==================")
                logger.debug("==================")
                result_id = result["metadata"].get("_id")
                if result_id not in seen_ids:
                    seen_ids.add(result_id)
                    flattened_results.append(result)


        # Re-rank the combined results with the original query for better relevance
        if len(flattened_results) > 1:
            final_results = await reranker_service.rerank(
                query=query_info.query,  # Use original query for final ranking
                documents=flattened_results,
                top_k=query_info.limit,
            )
        else:
            final_results = flattened_results

        logger.debug(f"final_results: {final_results}")
        # Prepare the template with the final results
        if send_user_info:
            user_info = await arango_service.get_user_by_user_id(user_id)
            org_info = await arango_service.get_document(
                org_id, CollectionNames.ORGS.value
            )
            if (
                org_info.get("accountType") == AccountType.ENTERPRISE.value
                or org_info.get("accountType") == AccountType.BUSINESS.value
            ):
                user_data = (
                    "I am the user of the organization. "
                    f"My name is {user_info.get('fullName', 'a user')} "
                    f"({user_info.get('designation', '')}) "
                    f"from {org_info.get('name', 'the organization')}. "
                    "Please provide accurate and relevant information based on the available context."
                )
            else:
                user_data = (
                    "I am the user. "
                    f"My name is {user_info.get('fullName', 'a user')} "
                    f"({user_info.get('designation', '')}) "
                    "Please provide accurate and relevant information based on the available context."
                )
        else:
            user_data = ""

        template = Template(qna_prompt)
        rendered_form = template.render(
            user_data=user_data,
            query=query_info.query,
            rephrased_queries=[],  # This keeps all query results for reference
            chunks=final_results,
        )

        messages = [
            {
                "role": "system",
                "content": "You are a enterprise questions answering expert",
            }
        ]

        # Add conversation history
        for conversation in query_info.previousConversations:
            if conversation.get("role") == "user_query":
                messages.append(
                    {"role": "user", "content": conversation.get("content")}
                )
            elif conversation.get("role") == "bot_response":
                messages.append(
                    {"role": "assistant", "content": conversation.get("content")}
                )

        # Add current query with context
        messages.append({"role": "user", "content": rendered_form})
        logger.debug(f"Messages to LLM {messages}")
        # Make async LLM call
        response = await llm.ainvoke(messages)
        logger.debug(f"llm response: {response}")
        # Process citations and return response
        return process_citations(response, final_results)

    except HTTPException as he:
        # Re-raise HTTP exceptions with their original status codes
        raise he
    except Exception as e:
        logger.error(f"Error in askAI: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
