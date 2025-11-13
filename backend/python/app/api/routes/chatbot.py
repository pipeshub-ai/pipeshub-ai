import json
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain.chat_models.base import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import AccountType
from app.config.constants.service import config_node_constants
from app.connectors.services.base_arango_service import BaseArangoService
from app.containers.query import QueryAppContainer
from app.modules.reranker.reranker import RerankerService
from app.modules.retrieval.retrieval_service import RetrievalService
from app.modules.transformers.blob_storage import BlobStorage
from app.utils.aimodels import get_generator_model
from app.utils.cache_helpers import get_cached_user_info
from app.utils.chat_helpers import get_flattened_results, get_message_content
from app.utils.citations import process_citations
from app.utils.fetch_full_record import create_fetch_full_record_tool
from app.utils.query_decompose import QueryDecompositionExpansionService
from app.utils.query_transform import setup_followup_query_transformation
from app.utils.streaming import create_sse_event, stream_llm_response_with_tools

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
    mode: Optional[str] = "json"  # "json" for full metadata, "simple" for answer only


# Dependency injection functions
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


async def get_reranker_service(request: Request) -> RerankerService:
    container: QueryAppContainer = request.app.container
    reranker_service = container.reranker_service()
    return reranker_service


def get_model_config_for_mode(chat_mode: str) -> Dict[str, Any]:
    """Get model configuration based on chat mode and user selection"""
    mode_configs = {
        "quick": {
            "temperature": 0.1,
            "max_tokens": 4096,
            "system_prompt": "You are an assistant. Answer queries in a professional, enterprise-appropriate format."
        },
        "analysis": {
            "temperature": 0.3,
            "max_tokens": 8192,
            "system_prompt": "You are an analytical assistant. Provide detailed analysis with insights and patterns."
        },
        "deep_research": {
            "temperature": 0.2,
            "max_tokens": 16384,
            "system_prompt": "You are a research assistant. Provide comprehensive, well-sourced answers with detailed explanations."
        },
        "creative": {
            "temperature": 0.7,
            "max_tokens": 16384,
            "system_prompt": "You are a creative assistant. Provide innovative and imaginative responses while staying relevant."
        },
        "precise": {
            "temperature": 0.05,
            "max_tokens": 16384,
            "system_prompt": "You are a precise assistant. Provide accurate, factual answers with high attention to detail."
        },
        "standard": {
            "temperature": 0.2,
            "max_tokens": 16384,
            "system_prompt": "You are an enterprise questions answering expert"
        }
    }
    return mode_configs.get(chat_mode, mode_configs["standard"])


async def get_model_config(config_service: ConfigurationService, model_key: str) -> Dict[str, Any]:
    """Get model configuration based on user selection or fallback to default"""
    ai_models = await config_service.get_config(config_node_constants.AI_MODELS.value)
    llm_configs = ai_models["llm"]

    for config in llm_configs:
        target_model_key = config.get("modelKey")
        if target_model_key == model_key:
            return config

    # Try fresh config if not found
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
                return get_generator_model(model_provider, llm_config, model_name), llm_config

        # If user specified only provider, find first matching model
        if model_key:
            model_string = llm_config.get("configuration", {}).get("model")
            model_names = [name.strip() for name in model_string.split(",") if name.strip()]
            default_model_name = model_names[0]
            model_provider = llm_config.get("provider")
            return get_generator_model(model_provider, llm_config, default_model_name), llm_config

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


async def process_chat_query_with_status(
    query_info: ChatQuery,
    request: Request,
    retrieval_service: RetrievalService,
    arango_service: BaseArangoService,
    reranker_service: RerankerService,
    config_service: ConfigurationService,
    logger,
    yield_status=None
) -> Tuple[BaseChatModel, List[dict], List[dict], dict, dict, List[dict], List[dict], BlobStorage, bool]:
    """
    Process chat query with optional status updates.
    If yield_status is provided, it should be an async function that accepts (event_type, data).
    """
    # Get LLM based on user selection or fallback to default
    llm, config = await get_llm_for_chat(
        config_service,
        query_info.modelKey,
        query_info.modelName,
        query_info.chatMode
    )
    is_multimodal_llm = config.get("isMultimodal")

    if llm is None:
        raise ValueError("Failed to initialize LLM service. LLM configuration is missing.")

    logger.info(f"LLM provider: {llm.provider.lower()}")
    if config.get("provider").lower() == "ollama":
        query_info.mode = "simple"

    # Handle conversation history and query transformation
    if len(query_info.previousConversations) > 0:
        if yield_status:
            await yield_status("status", {"status": "transforming", "message": "Understanding conversation context..."})
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

    # Query decomposition based on mode
    decomposed_queries = []
    if not query_info.quickMode and query_info.chatMode != "quick":
        if yield_status:
            await yield_status("status", {"status": "analyzing", "message": "Analyzing your question..."})
        decomposition_service = QueryDecompositionExpansionService(llm, logger=logger)
        decomposition_result = await decomposition_service.transform_query(query_info.query)
        decomposed_queries = decomposition_result["queries"]

    all_queries = [query_info.query] if not decomposed_queries else [query.get("query") for query in decomposed_queries]

    # Execute search
    org_id = request.state.user.get('orgId')
    user_id = request.state.user.get('userId')

    if yield_status:
        await yield_status("status", {"status": "searching", "message": "Searching knowledge base..."})

    result = await retrieval_service.search_with_filters(
        queries=all_queries,
        org_id=org_id,
        user_id=user_id,
        limit=query_info.limit,
        filter_groups=query_info.filters,
    )

    # Process search results
    search_results = result.get("searchResults", [])
    status_code = result.get("status_code", 500)

    if status_code in [202, 500, 503]:
        raise HTTPException(status_code=status_code, detail=result)

    if yield_status:
        await yield_status("status", {"status": "processing", "message": "Processing search results..."})

    blob_store = BlobStorage(logger=logger, config_service=config_service, arango_service=arango_service)

    virtual_record_id_to_result = {}
    flattened_results = await get_flattened_results(
        search_results, blob_store, org_id, is_multimodal_llm, virtual_record_id_to_result
    )

    # Re-rank results
    if len(flattened_results) > 1 and not query_info.quickMode and query_info.chatMode != "quick":
        if yield_status:
            await yield_status("status", {"status": "ranking", "message": "Ranking relevant information..."})
        final_results = await reranker_service.rerank(
            query=query_info.query,
            documents=flattened_results,
            top_k=query_info.limit,
        )
    else:
        final_results = flattened_results

    final_results = sorted(final_results, key=lambda x: (x['virtual_record_id'], x['block_index']))

    # Prepare user context
    send_user_info = request.query_params.get('sendUserInfo', True)
    user_data = ""

    if send_user_info:
        # Use cached user/org info for better performance (saves 0.5-1s per request)
        user_info, org_info = await get_cached_user_info(arango_service, user_id, org_id)

        if (org_info is not None and (
            org_info.get("accountType") == AccountType.ENTERPRISE.value
            or org_info.get("accountType") == AccountType.BUSINESS.value
        )):
            user_data = (
                "I am the user of the organization. "
                f"User's full name is {user_info.get('fullName', 'a user')} "
                f"User's designation is {user_info.get('designation', 'unknown')} "
                f"and part of the organization {org_info.get('name', 'the organization')}. "
            )
        else:
            user_data = (
                "I am the user. "
                f"User's full name is {user_info.get('fullName', 'a user')} "
                f"User's designation is {user_info.get('designation', 'unknown')} "
            )

    # Prepare messages
    mode_config = get_model_config_for_mode(query_info.chatMode)
    messages = [{"role": "system", "content": mode_config["system_prompt"]}]

    # Add conversation history
    for conversation in query_info.previousConversations:
        if conversation.get("role") == "user_query":
            messages.append({"role": "user", "content": conversation.get("content")})
        elif conversation.get("role") == "bot_response":
            messages.append({"role": "assistant", "content": conversation.get("content")})

    # Always add the current query with retrieved context as the final user message
    content = get_message_content(final_results, virtual_record_id_to_result, user_data, query_info.query, logger, query_info.mode)
    messages.append({"role": "user", "content": content})

    # Prepare tools
    fetch_tool = create_fetch_full_record_tool(virtual_record_id_to_result)
    tools = [fetch_tool]

    tool_runtime_kwargs = {
        "blob_store": blob_store,
        "arango_service": arango_service,
        "org_id": org_id,
    }

    return llm, messages, tools, tool_runtime_kwargs, final_results, all_queries, virtual_record_id_to_result, blob_store, is_multimodal_llm


async def process_chat_query(
    query_info: ChatQuery,
    request: Request,
    retrieval_service: RetrievalService,
    arango_service: BaseArangoService,
    reranker_service: RerankerService,
    config_service: ConfigurationService,
    logger
) -> Tuple[BaseChatModel, List[dict], List[dict], dict, dict]:
    """Wrapper for non-streaming endpoint (without status updates)"""
    return await process_chat_query_with_status(
        query_info, request, retrieval_service, arango_service,
        reranker_service, config_service, logger, yield_status=None
    )


async def resolve_tools_then_answer(llm, messages, tools, tool_runtime_kwargs, max_hops=4) -> AIMessage:
    """Handle tool calls for non-streaming responses with reflection for invalid tool calls"""

    llm_with_tools = llm.bind_tools(tools)

    # Initial call with provider-level error handling
    try:
        ai: AIMessage = await llm_with_tools.ainvoke(messages)
    except Exception as e:
        error_str = str(e).lower()
        # Check if this is a tool-related error from the provider
        if any(keyword in error_str for keyword in ['tool_use_failed', 'tool use failed', 'failed to call a function', 'invalid tool', 'function call failed']):
            valid_tool_names = [t.name for t in tools]
            reflection_content = (
                f"Error: The AI provider rejected the function call. This usually means:\n"
                f"1. Invalid arguments were provided to the tool\n"
                f"2. A non-existent tool was called\n"
                f"3. The function call format was incorrect\n\n"
                f"Available tools: {', '.join(valid_tool_names)}\n\n"
                f"Please provide your final answer directly as a JSON object with this structure:\n"
                f'{{"answer": "your answer here", "reason": "reasoning", "confidence": "High/Medium/Low", '
                f'"answerMatchType": "Derived From Blocks/Exact Match/etc", "blockNumbers": [list of block numbers]}}.\n\n'
                f"Do NOT attempt to call any tools. Provide your answer based on the blocks already provided in the context."
            )
            messages.append(HumanMessage(content=reflection_content))
            # Retry without tools binding
            ai: AIMessage = await llm.ainvoke(messages)
            return ai
        else:
            raise

    hops = 0
    while isinstance(ai, AIMessage) and getattr(ai, "tool_calls", None) and hops < max_hops:
        tool_msgs = []
        valid_tool_names = [t.name for t in tools]

        for call in ai.tool_calls:
            name = call["name"]
            args = call.get("args", {}) or {}
            call_id = call.get("id")

            tool = next((t for t in tools if t.name == name), None)
            if tool is None:
                # Use reflection to guide the LLM when it makes invalid tool calls
                reflection_message = (
                    f"Error: Tool '{name}' is not a valid tool. "
                    f"Available tools are: {', '.join(valid_tool_names)}. "
                    "Please provide your final answer directly as a JSON object with the following structure: "
                    '{"answer": "your answer here", "reason": "reasoning", "confidence": "High/Medium/Low", '
                    '"answerMatchType": "Derived From Blocks/Exact Match/etc", "blockNumbers": [list of block numbers]}. '
                    "Do NOT wrap your response in any tool call."
                )
                tool_msgs.append(
                    ToolMessage(
                        content=reflection_message,
                        tool_call_id=call_id,
                    )
                )
                continue

            try:
                tool_result = await tool.arun(args, **tool_runtime_kwargs)
            except Exception as e:
                tool_result = json.dumps({"ok": False, "error": str(e)})

            tool_msgs.append(ToolMessage(content=tool_result, tool_call_id=call_id))

        # feed back tool results
        messages.append(ai)
        messages.extend(tool_msgs)

        # ask model again (now with tool outputs) with error handling
        try:
            ai = await llm_with_tools.ainvoke(messages)
        except Exception as e:
            error_str = str(e).lower()
            if any(keyword in error_str for keyword in ['tool_use_failed', 'tool use failed', 'failed to call a function', 'invalid tool', 'function call failed']):
                reflection_content = (
                    "Error: The AI provider rejected the function call. "
                    "Please provide your final answer directly as a JSON object without using any tools. "
                    "Use only the information from the blocks already provided in the context."
                )
                messages.append(HumanMessage(content=reflection_content))
                # Retry without tools binding
                ai = await llm.ainvoke(messages)
                return ai
            else:
                raise
        hops += 1

    return ai



@router.post("/chat/stream")
@inject
async def askAIStream(
    request: Request,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    arango_service: BaseArangoService = Depends(get_arango_service),
    reranker_service: RerankerService = Depends(get_reranker_service),
    config_service: ConfigurationService = Depends(get_config_service),
) -> StreamingResponse:
    """Perform semantic search across documents with streaming events and tool support"""
    query_info = ChatQuery(**(await request.json()))

    async def generate_stream() -> AsyncGenerator[str, None]:
        try:
            container = request.app.container
            logger = container.logger()

            # Send initial status immediately upon connection
            yield create_sse_event("status", {"status": "started", "message": "Processing your query..."})

            # Process query inline with real-time status updates
            try:
                # Get LLM based on user selection or fallback to default
                llm, config = await get_llm_for_chat(
                    config_service,
                    query_info.modelKey,
                    query_info.modelName,
                    query_info.chatMode
                )
                is_multimodal_llm = config.get("isMultimodal")

                if llm is None:
                    raise ValueError("Failed to initialize LLM service. LLM configuration is missing.")

                if config.get("provider").lower() == "ollama":
                    query_info.mode = "simple"

                # Handle conversation history and query transformation
                if len(query_info.previousConversations) > 0:
                    yield create_sse_event("status", {"status": "transforming", "message": "Understanding conversation context..."})
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

                # Query decomposition based on mode
                decomposed_queries = []
                if not query_info.quickMode and query_info.chatMode != "quick":
                    yield create_sse_event("status", {"status": "analyzing", "message": "Analyzing your question..."})
                    decomposition_service = QueryDecompositionExpansionService(llm, logger=logger)
                    decomposition_result = await decomposition_service.transform_query(query_info.query)
                    decomposed_queries = decomposition_result["queries"]

                all_queries = [query_info.query] if not decomposed_queries else [query.get("query") for query in decomposed_queries]

                # Execute search
                org_id = request.state.user.get('orgId')
                user_id = request.state.user.get('userId')

                yield create_sse_event("status", {"status": "searching", "message": "Searching knowledge base..."})

                result = await retrieval_service.search_with_filters(
                    queries=all_queries,
                    org_id=org_id,
                    user_id=user_id,
                    limit=query_info.limit,
                    filter_groups=query_info.filters,
                )

                # Process search results
                search_results = result.get("searchResults", [])
                status_code = result.get("status_code", 500)

                if status_code in [202, 500, 503]:
                    raise HTTPException(status_code=status_code, detail=result)

                yield create_sse_event("status", {"status": "processing", "message": "Processing search results..."})

                blob_store = BlobStorage(logger=logger, config_service=config_service, arango_service=arango_service)

                virtual_record_id_to_result = {}
                flattened_results = await get_flattened_results(
                    search_results, blob_store, org_id, is_multimodal_llm, virtual_record_id_to_result
                )

                # Re-rank results
                if len(flattened_results) > 1 and not query_info.quickMode and query_info.chatMode != "quick":
                    yield create_sse_event("status", {"status": "ranking", "message": "Ranking relevant information..."})
                    final_results = await reranker_service.rerank(
                        query=query_info.query,
                        documents=flattened_results,
                        top_k=query_info.limit,
                    )
                else:
                    final_results = flattened_results

                final_results = sorted(final_results, key=lambda x: (x['virtual_record_id'], x['block_index']))

                # Prepare user context
                send_user_info = request.query_params.get('sendUserInfo', True)
                user_data = ""

                if send_user_info:
                    from app.utils.cache_helpers import get_cached_user_info
                    user_info, org_info = await get_cached_user_info(arango_service, user_id, org_id)

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

                # Prepare messages
                mode_config = get_model_config_for_mode(query_info.chatMode)
                messages = [{"role": "system", "content": mode_config["system_prompt"]}]

                # Add conversation history
                for conversation in query_info.previousConversations:
                    if conversation.get("role") == "user_query":
                        messages.append({"role": "user", "content": conversation.get("content")})
                    elif conversation.get("role") == "bot_response":
                        messages.append({"role": "assistant", "content": conversation.get("content")})

                # Always add the current query with retrieved context as the final user message
                content = get_message_content(final_results, virtual_record_id_to_result, user_data, query_info.query, logger, query_info.mode)
                messages.append({"role": "user", "content": content})

                # Prepare tools
                fetch_tool = create_fetch_full_record_tool(virtual_record_id_to_result)
                tools = [fetch_tool]

                tool_runtime_kwargs = {
                    "blob_store": blob_store,
                    "arango_service": arango_service,
                    "org_id": org_id,
                }

                all_queries = all_queries

            except HTTPException as e:
                result = e.detail
                yield create_sse_event("error", {
                    "status": result.get("status", "error"),
                    "message": result.get("message", "No results found")
                })
                return
            except Exception as e:
                yield create_sse_event("error", {"error": str(e)})
                return

            # Stream response with enhanced tool support using your existing implementation
            org_id = request.state.user.get('orgId')
            user_id = request.state.user.get('userId')

            try:
                async for stream_event in stream_llm_response_with_tools(
                    llm,
                    messages,
                    final_results,
                    all_queries,
                    retrieval_service,
                    user_id,
                    org_id,
                    virtual_record_id_to_result,
                    blob_store,
                    is_multimodal_llm,
                    tools=tools,
                    tool_runtime_kwargs=tool_runtime_kwargs,
                    target_words_per_chunk=1,
                    mode=query_info.mode,
                ):
                    event_type = stream_event["event"]
                    event_data = stream_event["data"]
                    yield create_sse_event(event_type, event_data)
            except Exception as stream_error:
                logger.error(f"Error during LLM streaming: {str(stream_error)}", exc_info=True)
                yield create_sse_event("error", {"error": f"Stream error: {str(stream_error)}"})

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
    arango_service: BaseArangoService = Depends(get_arango_service),
    reranker_service: RerankerService = Depends(get_reranker_service),
    config_service: ConfigurationService = Depends(get_config_service),
) -> JSONResponse:
    """Perform semantic search across documents"""
    try:
        container = request.app.container
        logger = container.logger()

        # Process query using shared logic
        llm, messages, tools, tool_runtime_kwargs, final_results, all_queries, virtual_record_id_to_result, blob_store, is_multimodal_llm = await process_chat_query(
            query_info, request, retrieval_service, arango_service, reranker_service, config_service, logger
        )

        # Make async LLM call with tools
        final_ai_msg = await resolve_tools_then_answer(llm, messages, tools, tool_runtime_kwargs, max_hops=4)

        # Guard: ensure we have content
        if not getattr(final_ai_msg, "content", None):
            raise HTTPException(status_code=500, detail="Model returned no final content after tool calls")

        return process_citations(final_ai_msg, final_results, records=[], from_agent=False)

    except HTTPException as he:
        # Re-raise HTTP exceptions with their original status codes
        raise he
    except Exception as e:
        logger.error(f"Error in askAI: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
