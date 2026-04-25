from collections.abc import AsyncGenerator
from typing import Any

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from jinja2 import Template
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel

from app.api.middlewares.auth import require_scopes
from app.config.configuration_service import ConfigurationService
from app.config.constants.service import OAuthScopes, config_node_constants
from app.containers.query import QueryAppContainer
from app.modules.qna.prompt_templates import (
    web_search_system_prompt,
    web_search_user_prompt,
)
from app.modules.retrieval.retrieval_service import RetrievalService
from app.modules.transformers.blob_storage import BlobStorage
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.aimodels import get_generator_model
from app.utils.cache_helpers import get_cached_user_info
from app.utils.chat_helpers import (
    CitationRefMapper,
    enrich_virtual_record_id_to_result_with_fk_children,
    get_flattened_results,
    get_message_content,
)
from app.utils.fetch_full_record import create_fetch_full_record_tool
from app.utils.execute_query import create_execute_query_tool, has_sql_connector_configured
from app.utils.query_decompose import QueryDecompositionExpansionService
from app.utils.fetch_url_tool import create_fetch_url_tool
from app.utils.query_transform import setup_followup_query_transformation
from app.utils.streaming import (
    create_sse_event,
    stream_llm_response_with_tools,
)
from app.utils.time_conversion import build_llm_time_context
from app.utils.web_search_tool import create_web_search_tool

DEFAULT_CONTEXT_LENGTH = 128000

router = APIRouter()

# Pydantic models
class ChatQuery(BaseModel):
    query: str
    limit: int | None = 50
    previousConversations: list[dict] = []
    filters: dict[str, Any] | None = None
    retrievalMode: str | None = "HYBRID"
    quickMode: bool | None = False
    # New fields for multi-model support
    modelKey: str | None = None  # e.g., "uuid-of-the-model"
    modelName: str | None = None  # e.g., "gpt-4o-mini", "claude-3-5-sonnet", "llama3.2"
    chatMode: str | None = "internal_search"  # "quick", "analysis", "deep_research", "creative", "precise"
    mode: str | None = "json"  # "json" for full metadata, "simple" for answer only
    timezone: str | None = None  # IANA timezone id from the client (e.g., "America/New_York")
    currentTime: str | None = None  # ISO 8601 datetime string from the client
    conversationId: str | None = None  # Passed by Node.js layer for background task tracking


# Dependency injection functions
async def get_retrieval_service(request: Request) -> RetrievalService:
    container: QueryAppContainer = request.app.container
    return await container.retrieval_service()


async def get_graph_provider(request: Request) -> IGraphDBProvider:
    """Get graph provider from app.state or container"""
    if hasattr(request.app.state, 'graph_provider'):
        return request.app.state.graph_provider
    container: QueryAppContainer = request.app.container
    return await container.graph_provider()


async def get_config_service(request: Request) -> ConfigurationService:
    container: QueryAppContainer = request.app.container
    return container.config_service()




async def _build_llm_user_context_string(
    graph_provider: IGraphDBProvider,
    user_id: str,
    org_id: str,
    send_user_info: Any,
) -> str:
    """Build user/org context for the chat LLM user message when sendUserInfo is enabled."""
    if not send_user_info:
        return ""
    user_info, org_info = await get_cached_user_info(graph_provider, user_id, org_id)
    user_info = user_info or {}
    org_name = (org_info or {}).get("name")
    if org_name:
        return (
            "I am the user of the organization. "
            f"My name is {user_info.get('fullName', 'a user')} "
            f"({user_info.get('designation', '')}) "
            f"from {org_name}. "
            "Please provide accurate and relevant information based on the available context."
        )
    return (
        "I am the user. "
        f"My name is {user_info.get('fullName', 'a user')} "
        f"({user_info.get('designation', '')}) "
        "Please provide accurate and relevant information based on the available context."
    )


def get_model_config_for_mode(chat_mode: str) -> dict[str, Any]:
    """Get model configuration based on chat mode and user selection"""
    mode_configs = {
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
        },
        "internal_search": {
            "temperature": 0.1,
            "max_tokens": 4096,
            "system_prompt": (
                "You are an assistant. Answer queries in a professional, enterprise-appropriate format. "
                "You MUST ONLY answer based on the provided internal knowledge base documents. "
                "Do NOT use your own training knowledge. "
                "If the answer is not present in the provided context blocks, respond with: "
                "'This information is not available in the internal knowledge base.'"
            )
        },
        "web_search": {
            "temperature": 0.1,
            "max_tokens": 4096,
            "system_prompt": web_search_system_prompt,
        }
    }
    return mode_configs.get(chat_mode, mode_configs["internal_search"])


_CITATION_SYSTEM_RULES = (
    "\n\n## Citation Rules\n"
    "When the user message contains context blocks with Citation IDs (e.g., ref1, ref2), follow these rules:\n"
    "- **Limit citations to the most relevant blocks.** Do NOT cite every sentence — only cite the most important, non-obvious, or specific factual claims.\n"
    "- Cite by embedding the block's Citation ID as a markdown link: [source](ref1).\n"
    "- Use EXACTLY the Citation ID shown in the context. Do NOT invent or modify Citation IDs.\n"
    "- Do NOT manually assign citation numbers — the system numbers them automatically.\n"
    "- If you cannot find the Citation ID for a fact, omit the citation rather than guessing.\n"
)


def _append_conversation_history(
    messages: list[dict[str, Any]],
    previous_conversations: list[dict],
) -> None:
    """Append prior user/assistant turns to the message list (mutates in place)."""
    for conversation in previous_conversations:
        if conversation.get("role") == "user_query":
            messages.append({"role": "user", "content": conversation.get("content")})
        elif conversation.get("role") == "bot_response":
            messages.append({"role": "assistant", "content": conversation.get("content")})


def _build_system_prompt(
    chat_mode: str,
    ai_models_config: dict[str, Any],
    current_time: str | None,
    timezone: str | None,
    custom_prompt_key: str = "customSystemPrompt",
    append_citation_rules: bool = False,
) -> str:
    """Build the system prompt with optional custom override, time context, and citation rules."""
    mode_config = get_model_config_for_mode(chat_mode)
    custom_system_prompt = ai_models_config.get(custom_prompt_key, "")
    if custom_system_prompt:
        mode_config["system_prompt"] = custom_system_prompt

    system_prompt = mode_config["system_prompt"]
    time_context = build_llm_time_context(
        current_time=current_time,
        time_zone=timezone,
    )
    if time_context:
        system_prompt += f"\n\n{time_context}"
    if append_citation_rules:
        system_prompt += _CITATION_SYSTEM_RULES

    return system_prompt


def _build_chat_llm_messages(
    query_info: ChatQuery,
    ai_models_config: dict[str, Any],
    final_results: list[dict[str, Any]],
    virtual_record_id_to_result: dict[str, Any],
    user_data: str,
    logger: Any,
    is_multimodal_llm: bool=False,
    has_sql_connector: bool=False,
) -> tuple[list[dict[str, Any]], CitationRefMapper]:
    """System prompt (with optional custom override), prior turns, then user message with retrieval context."""
    system_prompt = _build_system_prompt(
        chat_mode=query_info.chatMode,
        ai_models_config=ai_models_config,
        current_time=query_info.currentTime,
        timezone=query_info.timezone,
        append_citation_rules=bool(final_results),
    )
    if ai_models_config.get("customSystemPrompt"):
        logger.debug(f"Custom system prompt: {ai_models_config['customSystemPrompt']}")

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    _append_conversation_history(messages, query_info.previousConversations)

    content, ref_mapper = get_message_content(
        final_results, virtual_record_id_to_result, user_data, query_info.query, query_info.mode,is_multimodal_llm=is_multimodal_llm,from_tool=False, has_sql_connector=has_sql_connector
    )
    messages.append({"role": "user", "content": content})
    return messages, ref_mapper


def _build_web_search_messages(
    query_info: ChatQuery,
    ai_models_config: dict[str, Any],
    original_query: str,
) -> list[dict[str, Any]]:
    """Build LLM messages for web search mode."""
    system_prompt = _build_system_prompt(
        chat_mode="web_search",
        ai_models_config=ai_models_config,
        current_time=query_info.currentTime,
        timezone=query_info.timezone,
        custom_prompt_key="customSystemPromptWebSearch",
    )

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    _append_conversation_history(messages, query_info.previousConversations)

    messages.append({
        "role": "user",
        "content": Template(web_search_user_prompt).render(
            query=original_query,
        )
    })
    return messages


async def get_model_config(config_service: ConfigurationService, model_key: str | None = None, model_name: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Get model configuration based on user selection or fallback to default

    Returns:
        Tuple of (model_config, ai_models_config) where:
        - model_config: The specific LLM configuration for the selected model
        - ai_models_config: The full AI models configuration object
    """

    def _find_config_by_default(configs: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Find config marked as default"""
        return next((config for config in configs if config.get("isDefault", False)), None)

    def _find_config_by_model_name(configs: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
        """Find config by model name in configuration.model field"""
        for config in configs:
            model_string = config.get("configuration", {}).get("model", "")
            model_names = [n.strip() for n in model_string.split(",") if n.strip()]
            if name in model_names:
                return config
        return None

    def _find_config_by_key(configs: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
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

async def get_llm_for_chat(config_service: ConfigurationService, model_key: str = None, model_name: str = None, chat_mode: str = "internal_search") -> tuple[BaseChatModel, dict, dict]:
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

async def _iter_prepare_chat_queries_for_retrieval(
    llm: BaseChatModel,
    query_info: ChatQuery,
) -> AsyncGenerator[tuple[str, Any], None]:
    """Apply follow-up transformation from history and optional decomposition.

    Mutates ``query_info.query``. Yields ``("status", payload)`` for SSE status
    events, then a final ``("queries", list[str])``.
    """
    followup_query = query_info.query
    if len(query_info.previousConversations) > 0:
        yield (
            "status",
            {"status": "transforming", "message": "Understanding conversation context..."},
        )
        followup_query_transformation = setup_followup_query_transformation(llm)
        formatted_history = "\n".join(
            f"{'User' if conv.get('role') == 'user_query' else 'Assistant'}: {conv.get('content')}"
            for conv in query_info.previousConversations
        )
        followup_query = await followup_query_transformation.ainvoke(
            {"query": query_info.query, "previous_conversations": formatted_history}
        )

    all_queries = [followup_query]
    yield ("queries", all_queries)

#     return ai

async def _generate_internal_search_stream(
    request: Request,
    query_info: ChatQuery,
    retrieval_service: RetrievalService,
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
) -> AsyncGenerator[str, None]:
    """Stream generator for internal knowledge-base search mode."""
    try:
        container = request.app.container
        logger = container.logger()

        yield create_sse_event("status", {"status": "started", "message": "Processing your query..."})

        try:
            llm, config, ai_models_config = await get_llm_for_chat(
                config_service,
                query_info.modelKey,
                query_info.modelName,
                query_info.chatMode,
            )
            is_multimodal_llm = config.get("isMultimodal")
            context_length = config.get("contextLength") or DEFAULT_CONTEXT_LENGTH

            if llm is None:
                raise ValueError("Failed to initialize LLM service. LLM configuration is missing.")

            if config.get("provider").lower() == "ollama":
                query_info.mode = "no_tools"
            else:
                query_info.mode = "simple"

            all_queries: list[str] = []
            async for kind, payload in _iter_prepare_chat_queries_for_retrieval(
                llm, query_info
            ):
                if kind == "status":
                    yield create_sse_event("status", payload)
                else:
                    all_queries = payload
                    logger.debug(f"All queries: {all_queries}")

            org_id = request.state.user.get("orgId")
            user_id = request.state.user.get("userId")

            yield create_sse_event("status", {"status": "searching", "message": "Searching knowledge base..."})

            result = await retrieval_service.search_with_filters(
                queries=all_queries,
                org_id=org_id,
                user_id=user_id,
                limit=query_info.limit,
                filter_groups=query_info.filters,
            )

            search_results = result.get("searchResults", [])
            virtual_to_record_map = result.get("virtual_to_record_map", {})
            status_code = result.get("status_code", 500)

            if status_code in [202, 500, 503, 404]:
                raise HTTPException(status_code=status_code, detail=result)

            yield create_sse_event("status", {"status": "processing", "message": "Processing search results..."})

            blob_store = BlobStorage(logger=logger, config_service=config_service, graph_provider=graph_provider)

            virtual_record_id_to_result: dict[str, Any] = {}
            flattened_results = await get_flattened_results(
                search_results, blob_store, org_id, is_multimodal_llm,
                virtual_record_id_to_result, virtual_to_record_map,
                graph_provider=graph_provider,
            )
            await enrich_virtual_record_id_to_result_with_fk_children(
                    virtual_record_id_to_result, blob_store, org_id, graph_provider, flattened_results
                )

            final_results = sorted(flattened_results, key=lambda x: (x["virtual_record_id"], x["block_index"]))

            send_user_info = request.query_params.get("sendUserInfo", True)
            user_data = await _build_llm_user_context_string(
                graph_provider, user_id, org_id, send_user_info,
            )

            has_sql_connector = await has_sql_connector_configured(graph_provider, user_id, org_id)
            tools = []
            if has_sql_connector:
                tools.append(create_execute_query_tool(
                    config_service=config_service,
                    graph_provider=graph_provider,
                    org_id=org_id,
                    conversation_id=query_info.conversationId,
                    blob_store=blob_store,
                ))


            messages, ref_mapper = _build_chat_llm_messages(
                query_info,
                ai_models_config,
                final_results,
                virtual_record_id_to_result,
                user_data,
                logger,
                is_multimodal_llm=is_multimodal_llm,
                has_sql_connector=has_sql_connector,
            )

            fetch_tool = create_fetch_full_record_tool(virtual_record_id_to_result, org_id, graph_provider)
            tools.append(fetch_tool)
            tool_runtime_kwargs = {
                "blob_store": blob_store,
                "graph_provider": graph_provider,
                "org_id": org_id,
            }

        except HTTPException as e:
            logger.error(f"HTTPException: {str(e)}", exc_info=True)
            detail = e.detail
            if isinstance(detail, dict):
                yield create_sse_event("error", {
                    "status": detail.get("status", "error"),
                    "message": detail.get("message", "No results found"),
                })
            else:
                yield create_sse_event("error", {
                    "status": "error",
                    "message": str(detail) if detail else f"HTTP {e.status_code} error",
                })
            return
        except Exception as e:
            logger.error(f"Error processing internal search query: {str(e)}", exc_info=True)
            yield create_sse_event("error", {"error": str(e)})
            return

        try:
            async for stream_event in stream_llm_response_with_tools(
                llm=llm,
                messages=messages,
                final_results=final_results,
                all_queries=all_queries,
                retrieval_service=retrieval_service,
                user_id=user_id,
                org_id=org_id,
                virtual_record_id_to_result=virtual_record_id_to_result,
                blob_store=blob_store,
                is_multimodal_llm=is_multimodal_llm,
                context_length=context_length,
                tools=tools,
                tool_runtime_kwargs=tool_runtime_kwargs,
                target_words_per_chunk=1,
                mode=query_info.mode,
                ref_mapper=ref_mapper,
                max_hops=1,
                conversation_id=query_info.conversationId,
            ):
                yield create_sse_event(stream_event["event"], stream_event["data"])
        except Exception as stream_error:
            logger.error(f"Error during LLM streaming: {str(stream_error)}", exc_info=True)
            yield create_sse_event("error", {"error": f"Stream error: {str(stream_error)}"})

    except Exception as e:
        logger.error(f"Error in internal search stream: {str(e)}", exc_info=True)
        yield create_sse_event("error", {"error": str(e)})


async def _generate_web_search_stream(
    request: Request,
    query_info: ChatQuery,
    config_service: ConfigurationService,
) -> AsyncGenerator[str, None]:
    """Stream generator for web search mode."""
    try:
        container = request.app.container
        logger = container.logger()

        yield create_sse_event("status", {"status": "started", "message": "Processing your query..."})

        try:
            original_query = query_info.query

            llm, config, ai_models_config = await get_llm_for_chat(
                config_service,
                query_info.modelKey,
                query_info.modelName,
                query_info.chatMode,
            )
            is_multimodal_llm = config.get("isMultimodal")
            context_length = config.get("contextLength") or DEFAULT_CONTEXT_LENGTH

            if llm is None:
                raise ValueError("Failed to initialize LLM service. LLM configuration is missing.")

            if config.get("provider").lower() == "ollama":
                query_info.mode = "no_tools"
            else:
                query_info.mode = "simple"

            # Load web search provider configuration
            web_search_config = await config_service.get_config(
                config_node_constants.WEB_SEARCH.value,
                default={},
                use_cache=False,
            )
            web_search_provider_config = None
            if web_search_config and web_search_config.get("providers"):
                providers = web_search_config.get("providers", [])
                default_provider = next(
                    (p for p in providers if p.get("isDefault")), None,
                )
                if default_provider:
                    web_search_provider_config = {
                        "provider": default_provider.get("provider"),
                        "configuration": default_provider.get("configuration", {}),
                    }
                    logger.info(
                        "Web search provider selected",
                        extra={"provider": web_search_provider_config["provider"]},
                    )
            else:
                logger.warning("No web search config found; proceeding without a configured provider")

            # Build messages for web search
            messages = _build_web_search_messages(
                query_info=query_info,
                ai_models_config=ai_models_config,
                original_query=original_query,
            )

            # Prepare web search tools. Share a single CitationRefMapper across tools so
            # tiny web-ref URLs minted by one tool can be resolved by another (fetch_url
            # may receive a ref minted by web_search).
            ref_mapper = CitationRefMapper()
            tools = [
                create_web_search_tool(web_search_provider_config),
                create_fetch_url_tool(
                    ref_mapper=ref_mapper,
                ),
            ]
            tool_runtime_kwargs = {
                "config_service": config_service,
            }

        except Exception as e:
            logger.error(f"Error setting up web search: {str(e)}", exc_info=True)
            yield create_sse_event("error", {"error": str(e)})
            return

        org_id = request.state.user.get("orgId")
        user_id = request.state.user.get("userId")

        try:
            async for stream_event in stream_llm_response_with_tools(
                llm=llm,
                messages=messages,
                final_results=[],
                all_queries=[query_info.query],
                retrieval_service=None,
                user_id=user_id,
                org_id=org_id,
                virtual_record_id_to_result={},
                blob_store=None,
                is_multimodal_llm=is_multimodal_llm,
                context_length=context_length,
                tools=tools,
                tool_runtime_kwargs=tool_runtime_kwargs,
                target_words_per_chunk=1,
                mode=query_info.mode,
                ref_mapper=ref_mapper,
                chat_mode="web_search",
            ):
                yield create_sse_event(stream_event["event"], stream_event["data"])
        except Exception as stream_error:
            logger.error(f"Error during web search LLM streaming: {str(stream_error)}", exc_info=True)
            yield create_sse_event("error", {"error": f"Stream error: {str(stream_error)}"})

    except Exception as e:
        logger.error(f"Error in web search stream: {str(e)}", exc_info=True)
        yield create_sse_event("error", {"error": str(e)})


@router.post("/chat/stream", dependencies=[Depends(require_scopes(OAuthScopes.CONVERSATION_CHAT))])
@inject
async def askAIStream(
    request: Request,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    config_service: ConfigurationService = Depends(get_config_service),
) -> StreamingResponse:
    """Perform semantic search across documents with streaming events and tool support"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in request body")

    try:
        query_info = ChatQuery(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid request parameters: {str(e)}")

    if query_info.chatMode == "web_search":
        stream = _generate_web_search_stream(
            request=request,
            query_info=query_info,
            config_service=config_service,
        )
    else:
        stream = _generate_internal_search_stream(
            request=request,
            query_info=query_info,
            retrieval_service=retrieval_service,
            graph_provider=graph_provider,
            config_service=config_service,
        )

    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Cache-Control",
        },
    )
