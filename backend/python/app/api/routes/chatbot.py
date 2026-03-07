from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from jinja2 import Template
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel

from app.api.middlewares.auth import require_scopes
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import AccountType
from app.config.constants.service import OAuthScopes, config_node_constants
from app.containers.query import QueryAppContainer
from app.modules.qna.prompt_templates import (
    web_search_system_prompt,
    web_search_user_prompt,
)
from app.modules.reranker.reranker import RerankerService
from app.modules.retrieval.retrieval_service import RetrievalService
from app.modules.transformers.blob_storage import BlobStorage
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.aimodels import get_generator_model
from app.utils.cache_helpers import get_cached_user_info
from app.utils.chat_helpers import get_flattened_results, get_message_content
from app.utils.fetch_full_record import create_fetch_full_record_tool
from app.utils.fetch_url_tool import create_fetch_url_tool
from app.utils.query_transform import setup_followup_query_transformation
from app.utils.streaming import (
    create_sse_event,
    stream_llm_response_with_tools,
)
from app.utils.web_search_tool import create_web_search_tool

DEFAULT_CONTEXT_LENGTH = 128000
DEFAULT_WEB_SEARCH_INCLUDE_IMAGES = False
DEFAULT_WEB_SEARCH_MAX_IMAGES = 3
MAX_WEB_SEARCH_IMAGES = 500

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
    chatMode: Optional[str] = "internal_search"  # "analysis", "deep_research", "creative", "precise"
    mode: Optional[str] = "json"  # "json" for full metadata, "simple" for answer only


def normalize_web_search_image_settings(
    settings: Optional[Dict[str, Any]],
) -> Tuple[bool, int]:
    include_images = DEFAULT_WEB_SEARCH_INCLUDE_IMAGES
    max_images = DEFAULT_WEB_SEARCH_MAX_IMAGES

    if isinstance(settings, dict):
        raw_include_images = settings.get("includeImages")
        if isinstance(raw_include_images, bool):
            include_images = raw_include_images

        raw_max_images = settings.get("maxImages")
        parsed_max_images: Optional[int] = None
        if isinstance(raw_max_images, int) and not isinstance(raw_max_images, bool):
            parsed_max_images = raw_max_images
        elif isinstance(raw_max_images, str):
            try:
                parsed_max_images = int(raw_max_images)
            except ValueError:
                parsed_max_images = None

        if parsed_max_images is not None and 1 <= parsed_max_images <= MAX_WEB_SEARCH_IMAGES:
            max_images = parsed_max_images

    return include_images, max_images


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

async def get_llm_for_chat(config_service: ConfigurationService, model_key: str = None, model_name: str = None, chat_mode: str = "internal_search") -> Tuple[BaseChatModel, dict, dict]:
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


@router.post("/chat/stream", dependencies=[Depends(require_scopes(OAuthScopes.CONVERSATION_CHAT))])
@inject
async def askAIStream(
    request: Request,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
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
                llm, config, ai_models_config = await get_llm_for_chat(
                    config_service,
                    query_info.modelKey,
                    query_info.modelName,
                    query_info.chatMode
                )
                is_multimodal_llm = config.get("isMultimodal")
                context_length = config.get("contextLength") or DEFAULT_CONTEXT_LENGTH


                if llm is None:
                    raise ValueError("Failed to initialize LLM service. LLM configuration is missing.")



                if config.get("provider").lower() == "ollama":
                    query_info.mode = "simple"

                # Handle conversation history and query transformation
                if len(query_info.previousConversations) > 0 and query_info.chatMode != "web_search":
                    yield create_sse_event("status", {"status": "transforming", "message": "Understanding conversation context..."})
                    logger.info(
                        "Transforming follow-up query using conversation history",
                        extra={"num_turns": len(query_info.previousConversations)},
                    )
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


                all_queries = [query_info.query]

                org_id = request.state.user.get('orgId')
                user_id = request.state.user.get('userId')


                # ── Shared setup ─────────────────────────────────────────────
                blob_store = BlobStorage(
                    logger=logger, config_service=config_service, graph_provider=graph_provider
                )

                mode_config = get_model_config_for_mode(query_info.chatMode)
                if query_info.chatMode == "internal_search":
                    custom_system_prompt = ai_models_config.get("customSystemPromptInternal", "")
                elif query_info.chatMode == "web_search":
                    custom_system_prompt = ai_models_config.get("customSystemPromptWebSearch", "")
                else:
                    custom_system_prompt = ""
                if custom_system_prompt:
                    logger.debug(f"Custom system prompt for {query_info.chatMode}: {custom_system_prompt}")
                    mode_config["system_prompt"] = custom_system_prompt

                messages = [{"role": "system", "content": mode_config["system_prompt"]}]
                for conv in query_info.previousConversations:
                    if conv.get("role") == "user_query":
                        messages.append({"role": "user", "content": conv.get("content")})
                    elif conv.get("role") == "bot_response":
                        messages.append({"role": "assistant", "content": conv.get("content")})

                # ── Mode-specific branching ───────────────────────────────────
                if query_info.chatMode == "web_search":

                    logger.info("Searching the web...")
                    web_search_config = await config_service.get_config(
                        config_node_constants.WEB_SEARCH.value,
                        default={},
                        use_cache=False,
                    )
                    web_search_provider_config = None
                    include_images, max_images = normalize_web_search_image_settings(
                        web_search_config.get("settings") if isinstance(web_search_config, dict) else None
                    )
                    if web_search_config and web_search_config.get("providers"):
                        providers = web_search_config.get("providers", [])
                        default_provider = next(
                            (p for p in providers if p.get("isDefault")), None
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
                            logger.warning("Web search config present but no usable provider found")
                    else:
                        logger.warning("No web search config found; proceeding without a configured provider")

                    final_results = []
                    virtual_record_id_to_result = {}

                    messages.append({
                        "role": "user",
                        "content": Template(web_search_user_prompt).render(
                            query=query_info.query,
                            mode=query_info.mode,
                        ),
                    })

                    url_counter = {"count": 0}
                    tools = [
                        create_web_search_tool(url_counter, web_search_provider_config),
                        create_fetch_url_tool(
                            url_counter,
                            bool(is_multimodal_llm) and include_images,
                        ),
                    ]

                    tool_runtime_kwargs = {
                        "include_images": include_images,
                        "max_images": max_images,
                    }

                else:
                    # Full retrieval pipeline (internal_search + legacy modes).
                    # Only fetch_full_record tool — no web tools.

                    logger.info("Internal search: querying knowledge base")
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

                    logger.info(
                        "Knowledge base search completed"
                    )

                    if status_code in [202, 500, 503, 404]:
                        raise HTTPException(status_code=status_code, detail=result)

                    yield create_sse_event("status", {"status": "processing", "message": "Processing search results..."})


                    virtual_record_id_to_result = {}
                    flattened_results = await get_flattened_results(
                        search_results, blob_store, org_id, is_multimodal_llm, virtual_record_id_to_result, virtual_to_record_map, graph_provider=graph_provider
                    )

                    final_results = sorted(flattened_results, key=lambda x: (x['virtual_record_id'], x['block_index']))
                    logger.info("Results prepared for LLM context")

                    # Prepare user context
                    send_user_info = request.query_params.get('sendUserInfo', True)
                    user_data = ""

                    if send_user_info:
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



                    content = get_message_content(final_results, virtual_record_id_to_result, user_data, query_info.query, logger, query_info.mode)
                    messages.append({"role": "user", "content": content})
                    tools = [
                        create_fetch_full_record_tool(virtual_record_id_to_result),
                    ]
                    tool_runtime_kwargs = {
                        "blob_store": blob_store,
                        "graph_provider": graph_provider,
                        "org_id": org_id,
                    }

            except HTTPException as e:
                logger.error(f"HTTPException: {str(e)}", exc_info=True)
                result = e.detail
                yield create_sse_event("error", {
                    "status": result.get("status", "error"),
                    "message": result.get("message", "No results found")
                })
                return
            except Exception as e:
                logger.error(f"Setup/retrieval failed: {str(e)}", exc_info=True)
                yield create_sse_event("error", {"error": str(e)})
                return

            # Stream response with enhanced tool support using your existing implementation
            logger.debug("Starting LLM stream")
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

                logger.info("Chat stream completed successfully")
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



