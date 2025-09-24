from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain.chat_models.base import BaseChatModel
from pydantic import BaseModel

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import AccountType, CollectionNames
from app.config.constants.service import config_node_constants
from app.containers.query import QueryAppContainer
from app.modules.reranker.reranker import RerankerService
from app.modules.retrieval.retrieval_arango import ArangoService
from app.modules.retrieval.retrieval_service import RetrievalService
from app.modules.transformers.blob_storage import BlobStorage
from app.utils.aimodels import get_generator_model
from app.utils.chat_helpers import get_flattened_results, get_message_content
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
    # New fields for multi-model support
    modelKey: Optional[str] = None  # e.g., "uuid-of-the-model"
    modelName: Optional[str] = None  # e.g., "gpt-4o-mini", "claude-3-5-sonnet", "llama3.2"
    chatMode: Optional[str] = "standard"  # "quick", "analysis", "deep_research", "creative", "precise"


async def get_retrieval_service(request: Request) -> RetrievalService:
    container: QueryAppContainer = request.app.container
    retrieval_service = await container.retrieval_service()
    return retrieval_service


async def get_arango_service(request: Request) -> ArangoService:
    container: QueryAppContainer = request.app.container
    arango_service = await container.arango_service()
    return arango_service

async def get_config_service(request: Request) -> ConfigurationService:
    container: QueryAppContainer = request.app.container
    config_service = container.config_service()
    return config_service

async def get_reranker_service(request: Request) -> RerankerService:
    container: QueryAppContainer = request.app.container
    reranker_service = container.reranker_service()
    return reranker_service

def get_model_config_for_mode(chat_mode: str) -> Dict[str, Any]:
    """Get model configuration based on chat mode and user selection"""
    mode_configs = {
        "quick": {
            "temperature": 0.1,
            "max_tokens": 1000,
            "system_prompt": "You are a concise assistant. Provide brief, accurate answers."
        },
        "analysis": {
            "temperature": 0.3,
            "max_tokens": 2000,
            "system_prompt": "You are an analytical assistant. Provide detailed analysis with insights and patterns."
        },
        "deep_research": {
            "temperature": 0.2,
            "max_tokens": 4000,
            "system_prompt": "You are a research assistant. Provide comprehensive, well-sourced answers with detailed explanations."
        },
        "creative": {
            "temperature": 0.7,
            "max_tokens": 3000,
            "system_prompt": "You are a creative assistant. Provide innovative and imaginative responses while staying relevant."
        },
        "precise": {
            "temperature": 0.05,
            "max_tokens": 1500,
            "system_prompt": "You are a precise assistant. Provide accurate, factual answers with high attention to detail."
        },
        "standard": {
            "temperature": 0.2,
            "max_tokens": 2000,
            "system_prompt": "You are an enterprise questions answering expert"
        }
    }

    return mode_configs.get(chat_mode, mode_configs["standard"])

async def get_model_config(config_service: ConfigurationService, model_key: str) -> Dict[str, Any]:
    """Get model configuration based on user selection or fallback to default"""

    ai_models = await config_service.get_config(
            config_node_constants.AI_MODELS.value
        )
    llm_configs = ai_models["llm"]

    for config in llm_configs:
        target_model_key = config.get("modelKey")
        if target_model_key == model_key:
            return config

    new_ai_models = await config_service.get_config(
        config_node_constants.AI_MODELS.value,
        use_cache=False
    )

    llm_configs = new_ai_models["llm"]

    for config in llm_configs:
        target_model_key = config.get("modelKey")
        if target_model_key == model_key:
            return config

    if not llm_configs:
        raise ValueError("No LLM configurations found")

    # If user specified a model, try to find it
    return llm_configs

async def get_llm_for_chat(config_service: ConfigurationService, model_key: str = None, model_name: str = None, chat_mode: str = "standard") -> Tuple[BaseChatModel, dict]:
    """Get LLM instance based on user selection or fallback to default"""
    try:
        llm_config = await get_model_config(config_service, model_key)
        if not llm_config:
            raise ValueError("No LLM configurations found")

        # If user specified a model, try to find it
        if model_key and model_name:
            model_string = llm_config.get("configuration", {}).get("model")
            model_names = [name.strip() for name in model_string.split(",") if name.strip()]
            if (llm_config.get("modelKey") == model_key and model_name in model_names):
                model_provider = llm_config.get("provider")
                return get_generator_model(model_provider, llm_config, model_name),llm_config

        # If user specified only provider, find first matching model
        if model_key:
            model_string = llm_config.get("configuration", {}).get("model")
            model_names = [name.strip() for name in model_string.split(",") if name.strip()]
            default_model_name = model_names[0]
            model_provider = llm_config.get("provider")
            return get_generator_model(model_provider, llm_config, default_model_name),llm_config

        # Fallback to first available model
        if isinstance(llm_config, list):
            llm_config = llm_config[0]
        model_string = llm_config.get("configuration", {}).get("model")
        model_names = [name.strip() for name in model_string.split(",") if name.strip()]
        default_model_name = model_names[0]
        model_provider = llm_config.get("provider")
        llm = get_generator_model(model_provider, llm_config, default_model_name)
        return llm, llm_config
    except Exception as e:
            raise ValueError(f"Failed to initialize LLM: {str(e)}")

@router.post("/chat/stream")
@inject
async def askAIStream(
    request: Request,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    arango_service: ArangoService = Depends(get_arango_service),
    reranker_service: RerankerService = Depends(get_reranker_service),
    config_service: ConfigurationService = Depends(get_config_service),
) -> StreamingResponse:
    """Perform semantic search across documents with streaming events"""
    query_info = ChatQuery(**(await request.json()))
    async def generate_stream() -> AsyncGenerator[str, None]:
        try:
            container = request.app.container
            logger = container.logger()

            # logger.info(f"query_info: {query_info}")  # Consider enabling at debug level if needed
            # Send initial event
            yield create_sse_event("status", {"status": "started", "message": "Starting AI processing..."})

            # Get LLM based on user selection or fallback to default
            llm, config = await get_llm_for_chat(
                config_service,
                query_info.modelKey,
                query_info.modelName,
                query_info.chatMode
            )
            is_multimodal_llm = config.get("isMultimodal")

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

            # Query decomposition based on mode
            yield create_sse_event("status", {"status": "decomposing", "message": "Decomposing query..."})

            decomposed_queries = []

            # Skip decomposition for quick mode or if explicitly disabled
            if not query_info.quickMode and query_info.chatMode != "quick":
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
                )
            yield create_sse_event("search_complete", {"results_count": len(result.get("searchResults", []))})

            # Flatten and deduplicate results
            yield create_sse_event("status", {"status": "deduplicating", "message": "Deduplicating search results..."})

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
            blob_store = BlobStorage(logger=logger, config_service=config_service, arango_service=arango_service)
            virtual_record_id_to_result = {}
            flattened_results = []

            flattened_results = await get_flattened_results(result_set, blob_store, org_id, is_multimodal_llm,virtual_record_id_to_result)
            yield create_sse_event("results_ready", {"total_results": len(flattened_results)})

            # Re-rank results based on mode
            if len(flattened_results) > 1 and not query_info.quickMode and query_info.chatMode != "quick":
                yield create_sse_event("status", {"status": "reranking", "message": "Reranking results for better relevance..."})
                # final_results = flattened_results
                final_results = await reranker_service.rerank(
                        query=query_info.query,
                        documents=flattened_results,
                        top_k=query_info.limit,
                    )

            else:
                final_results = flattened_results


            final_results = sorted(final_results, key=lambda x: (x['virtual_record_id'], x['block_index']))
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

            # Get mode-specific configuration
            mode_config = get_model_config_for_mode(query_info.chatMode)

            # Prepare prompt with mode-specific system message

            messages = [
                {"role": "system", "content": mode_config["system_prompt"]}
            ]

            # Add conversation history
            for conversation in query_info.previousConversations:
                if conversation.get("role") == "user_query":
                    messages.append({"role": "user", "content": conversation.get("content")})
                elif conversation.get("role") == "bot_response":
                    messages.append({"role": "assistant", "content": conversation.get("content")})


            content = get_message_content(final_results, virtual_record_id_to_result, user_data, query_info.query, logger)
            messages.append({"role": "user", "content": content})


            yield create_sse_event("status", {"status": "generating", "message": "Generating AI response..."})

            # Stream LLM response with real-time answer updates
            async for stream_event in stream_llm_response(llm, messages, final_results,logger):
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
    config_service: ConfigurationService = Depends(get_config_service),
) -> JSONResponse:
    """Perform semantic search across documents"""
    try:
        container = request.app.container

        logger = container.logger()

        # Get LLM based on user selection or fallback to default
        llm,config = await get_llm_for_chat(
            config_service,
            query_info.modelKey,
            query_info.modelName,
            query_info.chatMode
        )
        is_multimodal_llm = config.get("isMultimodal")

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
        if not query_info.quickMode and query_info.chatMode != "quick":
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

        blob_store = BlobStorage(logger=logger, config_service=config_service, arango_service=arango_service)
        virtual_record_id_to_result = {}
        flattened_results = []
        flattened_results = await get_flattened_results(search_results, blob_store, org_id, is_multimodal_llm,virtual_record_id_to_result)

        # Re-rank the combined results with the original query for better relevance
        if len(flattened_results) > 1 and not query_info.quickMode and query_info.chatMode != "quick":
            final_results = await reranker_service.rerank(
                query=query_info.query,  # Use original query for final ranking
                documents=flattened_results,
                top_k=query_info.limit,
            )
        else:
            final_results = flattened_results



        final_results = sorted(final_results, key=lambda x: (x['virtual_record_id'], x['block_index']))

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

        # Get mode-specific configuration
        mode_config = get_model_config_for_mode(query_info.chatMode)

        messages = [
            {
                "role": "system",
                "content": mode_config["system_prompt"],
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
        content = get_message_content(final_results, virtual_record_id_to_result, user_data, query_info.query, logger)
        messages.append({"role": "user", "content": content})

        # Add current query with context
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
