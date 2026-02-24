import json
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from app.api.middlewares.auth import require_scopes
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import AccountType
from app.config.constants.service import OAuthScopes, config_node_constants
from app.containers.query import QueryAppContainer
from app.modules.reranker.reranker import RerankerService
from app.modules.retrieval.retrieval_service import RetrievalService
from app.modules.transformers.blob_storage import BlobStorage
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.aimodels import get_generator_model
from app.utils.cache_helpers import get_cached_user_info
from app.utils.chat_helpers import (
    enrich_virtual_record_id_to_result_with_fk_children,
    get_flattened_results,
    get_message_content,
)
from app.utils.citations import process_citations
from app.utils.fetch_full_record import create_fetch_full_record_tool
from app.utils.execute_query import create_execute_query_tool
from app.utils.query_decompose import QueryDecompositionExpansionService
from app.utils.query_transform import setup_followup_query_transformation
from app.utils.streaming import (
    bind_tools_for_llm,
    create_sse_event,
    stream_llm_response_with_tools,
)

DEFAULT_CONTEXT_LENGTH = 128000

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


async def get_graph_provider(request: Request) -> IGraphDBProvider:
    """Get graph provider from app.state or container"""
    if hasattr(request.app.state, 'graph_provider'):
        return request.app.state.graph_provider
    container: QueryAppContainer = request.app.container
    return await container.graph_provider()


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


async def get_model_config(config_service: ConfigurationService, model_key: str | None = None, model_name: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Get model configuration based on user selection or fallback to default

    Returns:
        Tuple of (model_config, ai_models_config) where:
        - model_config: The specific LLM configuration for the selected model
        - ai_models_config: The full AI models configuration object
    """

    def _find_config_by_default(configs: List[Dict[str, Any]]) -> Dict[str, Any] | None:
        """Find config marked as default"""
        return next((config for config in configs if config.get("isDefault", False)), None)

    def _find_config_by_model_name(configs: List[Dict[str, Any]], name: str) -> Dict[str, Any] | None:
        """Find config by model name in configuration.model field"""
        for config in configs:
            model_string = config.get("configuration", {}).get("model", "")
            model_names = [n.strip() for n in model_string.split(",") if n.strip()]
            if name in model_names:
                return config
        return None

    def _find_config_by_key(configs: List[Dict[str, Any]], key: str) -> Dict[str, Any] | None:
        """Find config by modelKey"""
        return next((config for config in configs if config.get("modelKey") == key), None)

    # Get initial config
    ai_models = await config_service.get_config(config_node_constants.AI_MODELS.value)
    llm_configs = ai_models["llm"]

    # Search based on provided parameters
    if model_key is None and model_name is None:
        # Return default config
        if default_config := _find_config_by_default(llm_configs):
            return default_config, ai_models
    elif model_key is None and model_name is not None:
        # Search by model name
        if name_config := _find_config_by_model_name(llm_configs, model_name):
            return name_config, ai_models
    elif model_key is not None:
        # Search by model key
        if key_config := _find_config_by_key(llm_configs, model_key):
            return key_config, ai_models

    # Try fresh config if not found (only for model_key searches)
    if model_key is not None:
        new_ai_models = await config_service.get_config(
            config_node_constants.AI_MODELS.value,
            use_cache=False
        )
        llm_configs = new_ai_models["llm"]
        if key_config := _find_config_by_key(llm_configs, model_key):
            return key_config, new_ai_models

    if not llm_configs:
        raise ValueError("No LLM configurations found")

    return llm_configs, ai_models

async def get_llm_for_chat(config_service: ConfigurationService, model_key: str = None, model_name: str = None, chat_mode: str = "standard") -> Tuple[BaseChatModel, dict, dict]:
    """Get LLM instance based on user selection or fallback to default

    Returns:
        Tuple of (llm, model_config, ai_models_config) where:
        - llm: The initialized LLM instance
        - model_config: The specific LLM configuration for the selected model
        - ai_models_config: The full AI models configuration object
    """
    try:
        llm_config, ai_models_config = await get_model_config(config_service, model_key, model_name)
        if not llm_config:
            raise ValueError("No LLM configurations found")

        # Handle list of configs - extract first one if we got a list
        if isinstance(llm_config, list):
            llm_config = llm_config[0]

        # If user specified a model, try to find it
        if model_key and model_name:
            model_string = llm_config.get("configuration", {}).get("model")
            model_names = [name.strip() for name in model_string.split(",") if name.strip()]
            if (llm_config.get("modelKey") == model_key and model_name in model_names):
                model_provider = llm_config.get("provider")
                return get_generator_model(model_provider, llm_config, model_name), llm_config, ai_models_config

        # If user specified only provider, find first matching model
        if model_key:
            model_string = llm_config.get("configuration", {}).get("model")
            model_names = [name.strip() for name in model_string.split(",") if name.strip()]
            default_model_name = model_names[0]
            model_provider = llm_config.get("provider")
            return get_generator_model(model_provider, llm_config, default_model_name), llm_config, ai_models_config

        # Fallback to first available model
        model_string = llm_config.get("configuration", {}).get("model")
        model_names = [name.strip() for name in model_string.split(",") if name.strip()]
        default_model_name = model_names[0]
        model_provider = llm_config.get("provider")
        llm = get_generator_model(model_provider, llm_config, default_model_name)
        return llm, llm_config, ai_models_config
    except Exception as e:
        raise ValueError(f"Failed to initialize LLM: {str(e)}")


async def process_chat_query_with_status(
    query_info: ChatQuery,
    request: Request,
    retrieval_service: RetrievalService,
    graph_provider: IGraphDBProvider,
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
    llm, config, _ = await get_llm_for_chat(
        config_service,
        query_info.modelKey,
        query_info.modelName,
        query_info.chatMode
    )
    is_multimodal_llm = config.get("isMultimodal")

    if llm is None:
        raise ValueError("Failed to initialize LLM service. LLM configuration is missing.")

    provider = config.get("provider", "unknown")
    logger.info(f"LLM provider: {provider.lower()}")
    if provider.lower() == "ollama":
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
            await yield_status("status", {"status": "analyzing", "message": "Analyzing your query..."})
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

    if status_code in [202, 500, 503, 404]:
        raise HTTPException(status_code=status_code, detail=result)

    if yield_status:
        await yield_status("status", {"status": "processing", "message": "Processing search results..."})

    blob_store = BlobStorage(logger=logger, config_service=config_service, graph_provider=graph_provider)

    virtual_record_id_to_result = {}
    flattened_results = await get_flattened_results(
        search_results, blob_store, org_id, is_multimodal_llm, virtual_record_id_to_result
    )
    await enrich_virtual_record_id_to_result_with_fk_children(
        virtual_record_id_to_result, blob_store, org_id, graph_provider, flattened_results
    )

    fk_blocks_in_flattened = [r for r in flattened_results if (r.get("metadata") or {}).get("source") == "FK_ENRICHMENT"]
    logger.debug(
        "chatbot: after FK enrichment flattened_results=%d, FK_ENRICHMENT blocks=%d",
        len(flattened_results),
        len(fk_blocks_in_flattened),
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
        user_info, org_info = await get_cached_user_info(graph_provider, user_id, org_id)

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
        "graph_provider": graph_provider,
        "org_id": org_id,
    }

    return llm, messages, tools, tool_runtime_kwargs, final_results, all_queries, virtual_record_id_to_result, blob_store, is_multimodal_llm


async def process_chat_query(
    query_info: ChatQuery,
    request: Request,
    retrieval_service: RetrievalService,
    graph_provider: IGraphDBProvider,
    reranker_service: RerankerService,
    config_service: ConfigurationService,
    logger
) -> Tuple[BaseChatModel, List[dict], List[dict], dict, dict]:
    """Wrapper for non-streaming endpoint (without status updates)"""
    return await process_chat_query_with_status(
        query_info, request, retrieval_service, graph_provider,
        reranker_service, config_service, logger, yield_status=None
    )


async def resolve_tools_then_answer(llm, messages, tools, tool_runtime_kwargs, max_hops=4) -> AIMessage:
    """Handle tool calls for non-streaming responses with reflection for invalid tool calls"""

    llm_with_tools = bind_tools_for_llm(llm, tools)

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



@router.post("/chat/stream", dependencies=[Depends(require_scopes(OAuthScopes.CONVERSATION_CHAT))])
@inject
async def askAIStream(
    request: Request,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    reranker_service: RerankerService = Depends(get_reranker_service),
    config_service: ConfigurationService = Depends(get_config_service),
) -> StreamingResponse:
    """Perform semantic search across documents with streaming events and tool support"""
    query_info = ChatQuery(**(await request.json()))

    async def generate_stream() -> AsyncGenerator[str, None]:
        try:
            
            container = request.app.container
            logger = container.logger()
            logger.info("Processing query...")
            # Send initial status immediately upon connection
            yield create_sse_event("status", {"status": "started", "message": "Processing your query..."})
            logger.info("Processing query2...")
            # Process query inline with real-time status updates
            try:
                # Get LLM based on user selection or fallback to default
                llm, config, ai_models_config = await get_llm_for_chat(
                    config_service,
                    query_info.modelKey,
                    query_info.modelName,
                    query_info.chatMode
                )
                is_multimodal_llm = config.get("isMultimodal")
                context_length = config.get("contextLength") or DEFAULT_CONTEXT_LENGTH


                if llm is None :
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
                    yield create_sse_event("status", {"status": "analyzing", "message": "Analyzing your query..."})
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
                virtual_to_record_map = result.get("virtual_to_record_map", {})
                status_code = result.get("status_code", 500)

                if status_code in [202, 500, 503,404]:
                    raise HTTPException(status_code=status_code, detail=result)

                yield create_sse_event("status", {"status": "processing", "message": "Processing search results..."})

                blob_store = BlobStorage(logger=logger, config_service=config_service, graph_provider=graph_provider)

                virtual_record_id_to_result = {}
                flattened_results = await get_flattened_results(
                    search_results, blob_store, org_id, is_multimodal_llm, virtual_record_id_to_result, virtual_to_record_map, graph_provider=graph_provider
                )
                await enrich_virtual_record_id_to_result_with_fk_children(
                    virtual_record_id_to_result, blob_store, org_id, graph_provider, flattened_results
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
                    user_info, org_info = await get_cached_user_info(graph_provider, user_id, org_id)

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

                custom_system_prompt = ai_models_config.get("customSystemPrompt", "")
                if custom_system_prompt:
                    logger.debug(f"Custom system prompt: {custom_system_prompt}")
                    mode_config["system_prompt"] = custom_system_prompt

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
                #!!!
                fetch_tool = create_fetch_full_record_tool(
                    virtual_record_id_to_result, graph_provider, blob_store, org_id
                )
                # tools = [fetch_tool]
                execute_query_tool = create_execute_query_tool(
                    config_service=config_service,
                    graph_provider=graph_provider,
                    org_id=org_id,
                )
                tools = [fetch_tool, execute_query_tool]

                tool_runtime_kwargs = {
                    "blob_store": blob_store,
                    "graph_provider": graph_provider,
                    "org_id": org_id,
                }

                all_queries = all_queries

            except HTTPException as e:
                logger.error(f"Error in streaming AI: {str(e)}", exc_info=True)
                result = e.detail
                yield create_sse_event("error", {
                    "status": result.get("status", "error"),
                    "message": result.get("message", "No results found")
                })
                return
            except Exception as e:
                logger.error(f"Error in streaming AI: {str(e)}", exc_info=True)
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
                    context_length,
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


@router.post("/chat", dependencies=[Depends(require_scopes(OAuthScopes.CONVERSATION_CHAT))])
@inject
async def askAI(
    request: Request,
    query_info: ChatQuery,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    reranker_service: RerankerService = Depends(get_reranker_service),
    config_service: ConfigurationService = Depends(get_config_service),
) -> JSONResponse:
    """Perform semantic search across documents"""
    try:
        container = request.app.container
        logger = container.logger()

        # Process query using shared logic
        llm, messages, tools, tool_runtime_kwargs, final_results, all_queries, virtual_record_id_to_result, blob_store, is_multimodal_llm = await process_chat_query(
            query_info, request, retrieval_service, graph_provider, reranker_service, config_service, logger
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
