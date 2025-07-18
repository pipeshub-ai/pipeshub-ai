from typing import Any, AsyncGenerator, Dict, List, Optional

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
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
from app.utils.query_decompose import QueryDecompositionExpansionService
from app.utils.query_transform import (
    setup_followup_query_transformation,
)
from app.utils.streaming import create_sse_event, stream_llm_response

router = APIRouter()


# Pydantic models
class ChatQuery(BaseModel):
    query: str
    limit: Optional[int] = 50
    previousConversations: List[Dict] = []
    filters: Optional[Dict[str, Any]] = None
    retrievalMode: Optional[str] = "HYBRID"
    quickMode: Optional[bool] = False


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


async def get_reranker_service(request: Request) -> RerankerService:
    container: AppContainer = request.app.container
    reranker_service = container.reranker_service()
    return reranker_service




@router.post("/chat/stream")
@inject
async def askAIStream(
    request: Request,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    arango_service: ArangoService = Depends(get_arango_service),
    reranker_service: RerankerService = Depends(get_reranker_service),
) -> StreamingResponse:
    """Perform semantic search across documents with streaming events"""
    query_info = ChatQuery(**(await request.json()))

    async def generate_stream() -> AsyncGenerator[str, None]:
        try:
            container = request.app.container
            logger = container.logger()

            # Send initial event
            yield create_sse_event("status", {"status": "started", "message": "Starting AI processing..."})

            llm = retrieval_service.llm
            if llm is None:
                llm = await retrieval_service.get_llm_instance()
                if llm is None:
                    yield create_sse_event("error", {"error": "Failed to initialize LLM service"})
                    return

            # Send LLM initialized event
            yield create_sse_event("status", {"status": "llm_ready", "message": "LLM service initialized"})

            if len(query_info.previousConversations) > 0:
                yield create_sse_event("status", {"status": "processing", "message": "Processing conversation history..."})

                followup_query_transformation = setup_followup_query_transformation(llm)
                formatted_history = "\n".join(
                    f"{'User' if conv.get('role') == 'user_query' else 'Assistant'}: {conv.get('content')}"
                    for conv in query_info.previousConversations
                )

                followup_query = await followup_query_transformation.ainvoke({
                    "query": query_info.query,
                    "previous_conversations": formatted_history
                })
                query_info.query = followup_query

                yield create_sse_event("query_transformed", {"original_query": query_info.query, "transformed_query": followup_query})

            # Query decomposition
            yield create_sse_event("status", {"status": "decomposing", "message": "Decomposing query..."})

            decomposed_queries = []

            if not query_info.quickMode:
                decomposition_service = QueryDecompositionExpansionService(llm, logger=logger)
                decomposition_result = await decomposition_service.transform_query(query_info.query)
                decomposed_queries = decomposition_result["queries"]

            if not decomposed_queries:
                all_queries = [query_info.query]
            else:
                all_queries = [query.get("query") for query in decomposed_queries]

            yield create_sse_event("query_decomposed", {"queries": all_queries})

            # Execute all query processing in parallel
            org_id = request.state.user.get('orgId')
            user_id = request.state.user.get('userId')
            send_user_info = request.query_params.get('sendUserInfo', True)

            # Process queries and yield status updates
            yield create_sse_event("status", {"status": "parallel_processing", "message": f"Processing {len(all_queries)} queries in parallel..."})

            # Send individual query processing updates
            for i, query in enumerate(all_queries):
                yield create_sse_event("transformed_query", {"status": "transforming", "query": query, "index": i+1})

            yield create_sse_event("status", {"status": "searching", "message": "Executing searches..."})
            result = await retrieval_service.search_with_filters(
                    queries=all_queries,
                    org_id=org_id,
                    user_id=user_id,
                    limit=query_info.limit,
                    filter_groups=query_info.filters,
                    arango_service=arango_service,
                )

            yield create_sse_event("search_complete", {"results_count": len(result.get("searchResults", []))})

            # Flatten and deduplicate results
            yield create_sse_event("status", {"status": "deduplicating", "message": "Deduplicating search results..."})

            flattened_results = []
            seen_ids = set()
            result_set = result.get("searchResults", [])
            status_code = result.get("status_code", 500)
            if status_code in [202, 500, 503]:
                logger.warn(f"AI service returned an error status code: {status_code}", {
                    "status": result.get("status", "error"),
                    "message": result.get("message", "No results found")
                })
                yield create_sse_event("error", {
                    "status": result.get("status", "error"),
                    "message": result.get("message", "No results found")
                })
                return

            for result in result_set:
                result_id = result["metadata"].get("_id")
                if result_id not in seen_ids:
                    seen_ids.add(result_id)
                    flattened_results.append(result)

            yield create_sse_event("results_ready", {"total_results": len(flattened_results)})

            # Re-rank results
            if len(flattened_results) > 1 and not query_info.quickMode:
                yield create_sse_event("status", {"status": "reranking", "message": "Reranking results for better relevance..."})
                final_results = await reranker_service.rerank(
                    query=query_info.query,
                    documents=flattened_results,
                    top_k=query_info.limit,
                )
            else:
                final_results = flattened_results

            # Prepare user context
            if send_user_info:
                yield create_sse_event("status", {"status": "preparing_context", "message": "Preparing user context..."})

                user_info = await arango_service.get_user_by_user_id(user_id)
                org_info = await arango_service.get_document(org_id, CollectionNames.ORGS.value)

                if (org_info is not None and (
                    org_info.get("accountType") == AccountType.ENTERPRISE.value
                    or org_info.get("accountType") == AccountType.BUSINESS.value
                )):
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

            # Prepare prompt
            template = Template(qna_prompt)
            rendered_form = template.render(
                user_data=user_data,
                query=query_info.query,
                rephrased_queries=[],
                chunks=final_results,
            )

            messages = [
                {"role": "system", "content": "You are a enterprise questions answering expert"}
            ]

            # Add conversation history
            for conversation in query_info.previousConversations:
                if conversation.get("role") == "user_query":
                    messages.append({"role": "user", "content": conversation.get("content")})
                elif conversation.get("role") == "bot_response":
                    messages.append({"role": "assistant", "content": conversation.get("content")})

            messages.append({"role": "user", "content": rendered_form})

            yield create_sse_event("status", {"status": "generating", "message": "Generating AI response..."})

            # Stream LLM response with real-time answer updates
            async for stream_event in stream_llm_response(llm, messages, final_results):
                event_type = stream_event["event"]
                event_data = stream_event["data"]
                yield create_sse_event(event_type, event_data)

        except Exception as e:
            logger.error(f"Error in streaming AI: {str(e)}", exc_info=True)
            yield create_sse_event("error", {"error": str(e)})

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Cache-Control"
        }
    )


@router.post("/chat")
@inject
async def askAI(
    request: Request,
    query_info: ChatQuery,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    arango_service: ArangoService = Depends(get_arango_service),
    reranker_service: RerankerService = Depends(get_reranker_service),
) -> JSONResponse:
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

        if len(query_info.previousConversations) > 0:
            followup_query_transformation = setup_followup_query_transformation(llm)

            # Format conversation history for the prompt
            formatted_history = "\n".join(
                f"{'User' if conv.get('role') == 'user_query' else 'Assistant'}: {conv.get('content')}"
                for conv in query_info.previousConversations
            )
            logger.debug(f"formatted_history {formatted_history}")

            followup_query = await followup_query_transformation.ainvoke({
                "query": query_info.query,
                "previous_conversations": formatted_history
            })
            query_info.query = followup_query

        logger.debug(f"query_info.query {query_info.query}")

        decomposed_queries = []
        if not query_info.quickMode:
            decomposition_service = QueryDecompositionExpansionService(llm, logger=logger)
            decomposition_result = await decomposition_service.transform_query(
                query_info.query
            )
            decomposed_queries = decomposition_result["queries"]

        logger.debug(f"decomposed_queries {decomposed_queries}")
        if not decomposed_queries:
            all_queries = [query_info.query]
        else:
            all_queries = [query.get("query") for query in decomposed_queries]


        # Execute all query processing in parallel
        org_id = request.state.user.get('orgId')
        user_id = request.state.user.get('userId')
        send_user_info = request.query_params.get('sendUserInfo', True)

        result = await retrieval_service.search_with_filters(
                queries=all_queries,
                org_id=org_id,
                user_id=user_id,
                limit=query_info.limit,
                filter_groups=query_info.filters,
                arango_service=arango_service,
            )

        # Flatten and deduplicate results based on document ID or other unique identifier
        flattened_results = []
        seen_ids = set()
        search_results = result.get("searchResults", [])
        status_code = result.get("status_code", 500)

        if status_code in [202, 500, 503]:
            return JSONResponse(
                status_code=status_code,
                content={
                    "status": result.get("status", "error"),
                    "message": result.get("message", "No results found"),
                    "searchResults": [],
                    "records": []
                }
            )

        for result in search_results:
            result_id = result["metadata"].get("_id")
            if result_id not in seen_ids:
                seen_ids.add(result_id)
                flattened_results.append(result)

        # Re-rank the combined results with the original query for better relevance
        if len(flattened_results) > 1 and not query_info.quickMode:
            final_results = await reranker_service.rerank(
                query=query_info.query,  # Use original query for final ranking
                documents=flattened_results,
                top_k=query_info.limit,
            )
        else:
            final_results = flattened_results

        # Prepare the template with the final results
        if send_user_info:
            user_info = await arango_service.get_user_by_user_id(user_id)
            org_info = await arango_service.get_document(
                org_id, CollectionNames.ORGS.value
            )
            if (
                org_info is not None and (
                    org_info.get("accountType") == AccountType.ENTERPRISE.value
                    or org_info.get("accountType") == AccountType.BUSINESS.value
                )
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
        # Make async LLM call
        response = await llm.ainvoke(messages)
        # Process citations and return response
        return process_citations(response, final_results)

    except HTTPException as he:
        # Re-raise HTTP exceptions with their original status codes
        raise he
    except Exception as e:
        logger.error(f"Error in askAI: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
