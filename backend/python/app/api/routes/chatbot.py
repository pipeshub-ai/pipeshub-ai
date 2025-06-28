import asyncio
import json
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

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
from app.utils.citations import normalize_citations_and_chunks, process_citations
from app.utils.query_decompose import QueryDecompositionService
from app.utils.query_transform import (
    setup_followup_query_transformation,
)

router = APIRouter()


# Pydantic models
class ChatQuery(BaseModel):
    query: str
    limit: Optional[int] = 50
    previousConversations: List[Dict] = []
    filters: Optional[Dict[str, Any]] = None
    retrieval_mode: Optional[str] = "HYBRID"


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


def create_sse_event(event_type: str, data: Union[str, dict, list]) -> str:
    """Create Server-Sent Event format"""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

async def stream_llm_response(llm, messages, final_results) -> AsyncGenerator[Dict[str, Any], None]:
    """Stream LLM response and yield incremental JSON updates with normalized citations"""
    accumulated_content = ""
    target_words_per_chunk = 3  # Send 3 words at a time

    try:
        # First, accumulate the complete response
        if hasattr(llm, 'astream'):
            async for chunk in llm.astream(messages):
                if hasattr(chunk, 'content') and chunk.content:
                    accumulated_content += chunk.content
        else:
            # Fallback to non-streaming response
            response = await llm.ainvoke(messages)
            accumulated_content = response

        # Now process the complete response
        try:
            # Try to parse as JSON
            parsed_json = json.loads(accumulated_content)

            # Extract the answer field
            if "answer" in parsed_json:
                answer_text = parsed_json["answer"]

                # Normalize citations and get corresponding chunks
                normalized_answer, citations = normalize_citations_and_chunks(answer_text, final_results)

                # Stream the normalized answer text in chunks
                words = normalized_answer.split()
                for i in range(0, len(words), target_words_per_chunk):
                    chunk_words = words[i:i + target_words_per_chunk]
                    chunk_text = " ".join(chunk_words)

                    if chunk_text.strip():
                        yield {
                            "event": "answer_chunk",
                            "data": {
                                "chunk": chunk_text,
                                "accumulated": normalized_answer,  # Send normalized version
                                "citations": citations
                            }
                        }

                # Send completion event with normalized response
                yield {
                    "event": "complete",
                    "data": {
                        "answer": normalized_answer,
                        "citations": citations
                    }
                }
                return

        except json.JSONDecodeError:
            # Not valid JSON, process with original citations logic but normalize
            normalized_response, normalized_citations = normalize_citations_and_chunks(accumulated_content, final_results)

            # Create response similar to process_citations format
            final_response = {
                "answer": normalized_response,
                "citations": normalized_citations
            }

            yield {
                "event": "complete",
                "data": final_response
            }

    except Exception as e:
        yield {
            "event": "error",
            "data": {"error": f"Error in LLM streaming: {str(e)}"}
        }

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

    print(f"query_info: {query_info}")

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

            decomposition_service = QueryDecompositionService(llm, logger=logger)
            decomposition_result = await decomposition_service.decompose_query(query_info.query)
            decomposed_queries = decomposition_result["queries"]

            if not decomposed_queries:
                all_queries = [{"query": query_info.query}]
            else:
                all_queries = decomposed_queries

            yield create_sse_event("query_decomposed", {"queries": all_queries})

            # Separate the processing logic from the generator
            async def process_single_query(query: str, org_id: str, user_id: str) -> Dict[str, Any]:

                results = await retrieval_service.search_with_filters(
                    queries=[query],
                    org_id=org_id,
                    user_id=user_id,
                    limit=query_info.limit,
                    filter_groups=query_info.filters,
                    arango_service=arango_service,
                )

                return results

            # Execute all query processing in parallel
            org_id = request.state.user.get('orgId')
            user_id = request.state.user.get('userId')
            send_user_info = request.query_params.get('sendUserInfo', True)

            # Process queries and yield status updates
            yield create_sse_event("status", {"status": "parallel_processing", "message": f"Processing {len(all_queries)} queries in parallel..."})

            # Send individual query processing updates
            for i, query_dict in enumerate(all_queries):
                query = query_dict.get("query")
                yield create_sse_event("transformed_query", {"status": "transforming", "query": query, "index": i+1})

            # Process all queries
            tasks = [
                process_single_query(query_dict.get("query"), org_id, user_id)
                for query_dict in all_queries
            ]

            yield create_sse_event("status", {"status": "searching", "message": "Executing searches..."})
            all_results = await asyncio.gather(*tasks)

            yield create_sse_event("search_complete", {"results_count": sum(len(r.get("searchResults", [])) for r in all_results)})

            # Flatten and deduplicate results
            yield create_sse_event("status", {"status": "deduplicating", "message": "Deduplicating search results..."})

            flattened_results = []
            seen_ids = set()
            for result_set in all_results:
                status_code = result_set.get("status_code", 500)
                if status_code in [202, 500, 503]:
                    logger.warn(f"AI service returned an error status code: {status_code}", {
                        "status": result_set.get("status", "error"),
                        "message": result_set.get("message", "No results found")
                    })
                    yield create_sse_event("error", {
                        "status": result_set.get("status", "error"),
                        "message": result_set.get("message", "No results found")
                    })
                    return

                search_result_set = result_set.get("searchResults", [])
                for result in search_result_set:
                    result_id = result["metadata"].get("_id")
                    if result_id not in seen_ids:
                        seen_ids.add(result_id)
                        flattened_results.append(result)

            yield create_sse_event("results_ready", {"total_results": len(flattened_results)})

            # Re-rank results
            if len(flattened_results) > 1:
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

                if (org_info.get("accountType") == AccountType.ENTERPRISE.value or
                    org_info.get("accountType") == AccountType.BUSINESS.value):
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

        async def process_decomposed_query(query: str, org_id: str, user_id: str) -> Dict[str, Any]:

            results = await retrieval_service.search_with_filters(
                queries=[query],
                org_id=org_id,
                user_id=user_id,
                limit=query_info.limit,
                filter_groups=query_info.filters,
                arango_service=arango_service,
            )
            logger.info("Results from the AI service received")
            logger.debug(f"formatted_results: {results}")

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
