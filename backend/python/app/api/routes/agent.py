"""
Agent API Routes
Handles agent instances, templates, chat, and permissions using graph-based architecture
"""

import json
import uuid
from logging import Logger
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel

from app.api.middlewares.auth import require_scopes
from app.api.routes.chatbot import get_llm_for_chat
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import OAuthScopes, config_node_constants
from app.modules.agents.qna.cache_manager import get_cache_manager
from app.modules.agents.qna.chat_state import build_initial_state
from app.modules.agents.qna.graph import agent_graph
from app.modules.agents.qna.memory_optimizer import (
    auto_optimize_state,
    check_memory_health,
)
from app.modules.reranker.reranker import RerankerService
from app.modules.retrieval.retrieval_service import RetrievalService
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

router = APIRouter()

# Constants
SPLIT_PATH_EXPECTED_PARTS = 2  # Expected parts when splitting path with "/" separator


# ============================================================================
# Request Models
# ============================================================================

class ChatQuery(BaseModel):
    query: str
    limit: Optional[int] = 50
    previousConversations: List[Dict] = []
    quickMode: bool = False
    filters: Optional[Dict[str, Any]] = None
    retrievalMode: Optional[str] = "HYBRID"
    systemPrompt: Optional[str] = None
    instructions: Optional[str] = None
    tools: Optional[List[str]] = None
    chatMode: Optional[str] = "quick"
    modelKey: Optional[str] = None
    modelName: Optional[str] = None
    timezone: Optional[str] = None
    currentTime: Optional[str] = None


# ============================================================================
# Custom Exceptions
# ============================================================================

class AgentError(HTTPException):
    """Base exception for agent operations"""
    def __init__(self, detail: str, status_code: int = 500) -> None:
        super().__init__(status_code=status_code, detail=detail)


class AgentNotFoundError(AgentError):
    """Agent not found"""
    def __init__(self, agent_id: str) -> None:
        super().__init__(
            detail=f"Agent '{agent_id}' not found or you don't have access to it",
            status_code=404
        )


class AgentTemplateNotFoundError(AgentError):
    """Agent template not found"""
    def __init__(self, template_id: str) -> None:
        super().__init__(
            detail=f"Agent template '{template_id}' not found or you don't have access to it",
            status_code=404
        )


class PermissionDeniedError(AgentError):
    """Permission denied"""
    def __init__(self, action: str) -> None:
        super().__init__(
            detail=f"You don't have permission to {action}",
            status_code=403
        )


class InvalidRequestError(AgentError):
    """Invalid request data"""
    def __init__(self, message: str) -> None:
        super().__init__(
            detail=f"Invalid request: {message}",
            status_code=400
        )


class LLMInitializationError(AgentError):
    """LLM initialization failed"""
    def __init__(self) -> None:
        super().__init__(
            detail="Failed to initialize LLM service. LLM configuration is missing.",
            status_code=500
        )


# ============================================================================
# Helper Functions
# ============================================================================

async def get_services(request: Request) -> Dict[str, Any]:
    """Get all required services from container"""
    container = request.app.container

    retrieval_service = await container.retrieval_service()
    graph_provider = await container.graph_provider()
    reranker_service = container.reranker_service()
    config_service = container.config_service()
    logger = container.logger()

    # Get and verify LLM
    llm = retrieval_service.llm
    if llm is None:
        llm = await retrieval_service.get_llm_instance()
        if llm is None:
            raise LLMInitializationError()

    return {
        "retrieval_service": retrieval_service,
        "graph_provider": graph_provider,
        "reranker_service": reranker_service,
        "config_service": config_service,
        "logger": logger,
        "llm": llm,
    }


def _get_user_context(request: Request) -> Dict[str, Any]:
    """Extract user context from request"""
    user = getattr(request.state, "user", {})
    user_id = user.get("userId")
    org_id = user.get("orgId")

    if not user_id or not org_id:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Please provide valid credentials."
        )

    return {
        "userId": user_id,
        "orgId": org_id,
        "sendUserInfo": request.query_params.get("sendUserInfo", True),
    }


async def _get_user_document(user_id: str, graph_provider: IGraphDBProvider, logger: Logger) -> Dict[str, Any]:
    """Get user document with validation"""
    try:
        user = await graph_provider.get_user_by_user_id(user_id)
        if not user or not isinstance(user, dict):
            raise HTTPException(status_code=404, detail="User not found")

        # Validate required fields
        if not user.get("email", "").strip():
            raise HTTPException(status_code=400, detail="User email is missing")

        return user
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching user document: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve user information")


async def _get_org_info(user_info: Dict[str, Any], graph_provider: IGraphDBProvider, logger: Logger) -> Dict[str, Any]:
    """Get organization information with validation"""
    try:
        org_doc = await graph_provider.get_document(user_info["orgId"], CollectionNames.ORGS.value)
        if not org_doc or not isinstance(org_doc, dict):
            raise HTTPException(status_code=404, detail="Organization not found")

        # Validate account type
        raw_account_type = str(org_doc.get("accountType", "")).lower()
        if raw_account_type not in ["enterprise", "individual"]:
            raise HTTPException(status_code=400, detail="Invalid organization account type")

        return {
            "orgId": user_info["orgId"],
            "accountType": raw_account_type
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching organization info: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve organization information")


async def _enrich_user_info(user_info: Dict[str, Any], user_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Enrich user info with document data"""
    enriched = user_info.copy()
    enriched["userEmail"] = user_doc.get("email", "").strip()
    enriched["_key"] = user_doc.get("_key")

    # Add name fields if available
    for field in ["fullName", "firstName", "lastName", "displayName"]:
        if user_doc.get(field):
            enriched[field] = user_doc[field]

    return enriched


def _validate_required_fields(data: Dict[str, Any], required_fields: List[str]) -> None:
    """Validate required fields in request data"""
    for field in required_fields:
        if not data.get(field) or not str(data.get(field)).strip():
            raise InvalidRequestError(f"'{field}' is required")


def _parse_models(raw_models: List[Any], logger: Logger) -> tuple[List[str], bool]:
    """Parse and validate model entries"""
    model_entries = []
    has_reasoning_model = False

    if not raw_models or not isinstance(raw_models, list):
        return model_entries, has_reasoning_model

    for model in raw_models:
        if isinstance(model, dict):
            model_key = model.get("modelKey")
            model_name = model.get("modelName", "")

            if model_key:
                entry = f"{model_key}_{model_name}" if model_name else model_key
                model_entries.append(entry)

                if model.get("isReasoning", False):
                    has_reasoning_model = True
        elif isinstance(model, str):
            model_entries.append(model)

    return model_entries, has_reasoning_model


def _parse_toolsets(raw_toolsets: List[Any], logger: Logger) -> Dict[str, Dict[str, Any]]:
    """Parse toolsets with their tools"""
    toolsets_with_tools = {}

    if not raw_toolsets or not isinstance(raw_toolsets, list):
        return toolsets_with_tools

    for toolset_data in raw_toolsets:
        if not isinstance(toolset_data, dict):
            continue

        toolset_name = toolset_data.get("name", "").lower().strip()
        if not toolset_name:
            continue

        display_name = toolset_data.get("displayName", toolset_name.replace("_", " ").title())
        toolset_type = toolset_data.get("type", "app")
        tools_list = toolset_data.get("tools", [])

        if toolset_name not in toolsets_with_tools:
            toolsets_with_tools[toolset_name] = {
                "displayName": display_name,
                "type": toolset_type,
                "tools": []
            }

        for tool in tools_list:
            if isinstance(tool, dict):
                tool_name = tool.get("name", "")
                if tool_name:
                    toolsets_with_tools[toolset_name]["tools"].append({
                        "name": tool_name,
                        "fullName": tool.get("fullName", f"{toolset_name}.{tool_name}"),
                        "description": tool.get("description", "")
                    })

    return toolsets_with_tools


def _parse_knowledge_sources(raw_knowledge: List[Any], logger: Logger) -> Dict[str, Dict[str, Any]]:
    """Parse knowledge sources"""
    knowledge_sources = {}

    if not raw_knowledge or not isinstance(raw_knowledge, list):
        return knowledge_sources

    for knowledge_data in raw_knowledge:
        if not isinstance(knowledge_data, dict):
            continue

        connector_id = knowledge_data.get("connectorId", "").strip()
        if not connector_id:
            continue

        filters = knowledge_data.get("filters", {})
        if isinstance(filters, str):
            try:
                filters = json.loads(filters)
            except json.JSONDecodeError:
                filters = {}

        knowledge_sources[connector_id] = {
            "connectorId": connector_id,
            "filters": filters
        }

    return knowledge_sources


async def _create_toolset_edges(
    agent_key: str,
    toolsets_with_tools: Dict[str, Dict[str, Any]],
    user_info: Dict[str, Any],
    user_key: str,
    graph_provider: IGraphDBProvider,
    logger: Logger
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Create toolset nodes and edges for agent using batch operations"""
    from app.agents.constants.toolset_constants import normalize_app_name

    created_toolsets = []
    failed_toolsets = []
    time = get_epoch_timestamp_in_ms()

    if not toolsets_with_tools:
        return created_toolsets, failed_toolsets

    # Prepare all toolset nodes
    toolset_nodes = []
    toolset_mapping = {}  # Map toolset_name to toolset_key

    for toolset_name, toolset_data in toolsets_with_tools.items():
        toolset_key = str(uuid.uuid4())
        display_name = toolset_data["displayName"]
        toolset_type = toolset_data["type"]
        tools_list = toolset_data["tools"]

        toolset_node = {
            "_key": toolset_key,
            "name": normalize_app_name(toolset_name),
            "displayName": display_name,
            "type": toolset_type,
            "userId": user_info["userId"],
            "createdBy": user_key,
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time
        }

        toolset_nodes.append(toolset_node)
        toolset_mapping[toolset_name] = {
            "key": toolset_key,
            "displayName": display_name,
            "tools": tools_list
        }

    # Batch create all toolset nodes
    try:
        result = await graph_provider.batch_upsert_nodes(toolset_nodes, CollectionNames.AGENT_TOOLSETS.value)
        if not result:
            return created_toolsets, [{"name": "all", "error": "Failed to create toolset nodes"}]
    except Exception as e:
        logger.error(f"Failed to batch create toolset nodes: {e}")
        return created_toolsets, [{"name": "all", "error": str(e)}]

    # Prepare agent -> toolset edges
    agent_toolset_edges = []
    for toolset_name, toolset_info in toolset_mapping.items():
        agent_toolset_edges.append({
            "_from": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
            "_to": f"{CollectionNames.AGENT_TOOLSETS.value}/{toolset_info['key']}",
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
        })

    # Batch create agent -> toolset edges
    try:
        await graph_provider.batch_create_edges(agent_toolset_edges, CollectionNames.AGENT_HAS_TOOLSET.value)
    except Exception as e:
        logger.error(f"Failed to create agent-toolset edges: {e}")

    # Prepare all tool nodes and edges
    tool_nodes = []
    toolset_tool_edges = []
    tool_mapping = {}  # Map full_name to tool_key

    for toolset_name, toolset_info in toolset_mapping.items():
        for tool_data in toolset_info["tools"]:
            tool_name = tool_data["name"]
            full_name = tool_data["fullName"]
            description = tool_data["description"]

            tool_key = str(uuid.uuid4())

            tool_node = {
                "_key": tool_key,
                "name": tool_name,
                "fullName": full_name,
                "toolsetName": toolset_name,
                "description": description,
                "createdBy": user_key,
                "createdAtTimestamp": time,
                "updatedAtTimestamp": time
            }

            tool_nodes.append(tool_node)
            tool_mapping[full_name] = {
                "key": tool_key,
                "name": tool_name,
                "toolset": toolset_name
            }

            # Prepare toolset -> tool edge
            toolset_tool_edges.append({
                "_from": f"{CollectionNames.AGENT_TOOLSETS.value}/{toolset_info['key']}",
                "_to": f"{CollectionNames.AGENT_TOOLS.value}/{tool_key}",
                "createdAtTimestamp": time,
                "updatedAtTimestamp": time,
            })

    # Batch create all tool nodes
    if tool_nodes:
        try:
            result = await graph_provider.batch_upsert_nodes(tool_nodes, CollectionNames.AGENT_TOOLS.value)
            if not result:
                logger.warning("Failed to create tool nodes")
        except Exception as e:
            logger.error(f"Failed to batch create tool nodes: {e}")

    # Batch create toolset -> tool edges
    if toolset_tool_edges:
        try:
            await graph_provider.batch_create_edges(toolset_tool_edges, CollectionNames.TOOLSET_HAS_TOOL.value)
        except Exception as e:
            logger.error(f"Failed to create toolset-tool edges: {e}")

    # Build response with created toolsets and tools
    for toolset_name, toolset_info in toolset_mapping.items():
        created_tools = []
        for tool_data in toolset_info["tools"]:
            full_name = tool_data["fullName"]
            if full_name in tool_mapping:
                created_tools.append({
                    "name": tool_mapping[full_name]["name"],
                    "fullName": full_name,
                    "key": tool_mapping[full_name]["key"]
                })

        created_toolsets.append({
            "name": toolset_name,
            "displayName": toolset_info["displayName"],
            "key": toolset_info["key"],
            "tools": created_tools
        })

    return created_toolsets, failed_toolsets


async def _create_knowledge_edges(
    agent_key: str,
    knowledge_sources: Dict[str, Dict[str, Any]],
    user_key: str,
    graph_provider: IGraphDBProvider,
    logger: Logger
) -> List[Dict[str, Any]]:
    """Create knowledge nodes and edges for agent using batch operations"""
    created_knowledge = []
    time = get_epoch_timestamp_in_ms()

    if not knowledge_sources:
        return created_knowledge

    # Prepare all knowledge nodes
    knowledge_nodes = []
    knowledge_mapping = {}

    for connector_id, knowledge_data in knowledge_sources.items():
        knowledge_key = str(uuid.uuid4())
        filters = knowledge_data["filters"]

        # Schema expects filters as a stringified JSON, not a dict
        filters_str = json.dumps(filters) if isinstance(filters, dict) else str(filters)

        knowledge_node = {
            "_key": knowledge_key,
            "connectorId": connector_id,
            "filters": filters_str,
            "createdBy": user_key,
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time
        }

        knowledge_nodes.append(knowledge_node)
        knowledge_mapping[connector_id] = {
            "key": knowledge_key,
            "filters": filters
        }

    # Batch create all knowledge nodes
    try:
        result = await graph_provider.batch_upsert_nodes(knowledge_nodes, CollectionNames.AGENT_KNOWLEDGE.value)
        if not result:
            logger.warning("Failed to create knowledge nodes")
            return created_knowledge
    except Exception as e:
        logger.error(f"Failed to batch create knowledge nodes: {e}")
        return created_knowledge

    # Prepare agent -> knowledge edges
    agent_knowledge_edges = []
    for connector_id, knowledge_info in knowledge_mapping.items():
        agent_knowledge_edges.append({
            "_from": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
            "_to": f"{CollectionNames.AGENT_KNOWLEDGE.value}/{knowledge_info['key']}",
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
        })

    # Batch create agent -> knowledge edges
    try:
        await graph_provider.batch_create_edges(agent_knowledge_edges, CollectionNames.AGENT_HAS_KNOWLEDGE.value)
    except Exception as e:
        logger.error(f"Failed to create agent-knowledge edges: {e}")

    # Build response
    for connector_id, knowledge_info in knowledge_mapping.items():
        created_knowledge.append({
            "connectorId": connector_id,
            "key": knowledge_info["key"],
            "filters": knowledge_info["filters"]
        })

    return created_knowledge


async def _enrich_agent_models(agent: Dict[str, Any], config_service: ConfigurationService, logger: Logger) -> None:
    """Enrich agent models with full configurations from etcd"""
    model_entries = agent.get("models", [])

    if not model_entries or not isinstance(model_entries, list):
        return

    try:
        ai_models = await config_service.get_config(config_node_constants.AI_MODELS.value, use_cache=True)
        llm_configs = ai_models.get("llm", []) if ai_models else []

        enriched_models = []
        for model_entry in model_entries:
            # Parse "modelKey_modelName" format
            if isinstance(model_entry, str) and "_" in model_entry:
                parts = model_entry.split("_", 1)
                model_key = parts[0]
                model_name = parts[1] if len(parts) > 1 else model_key
            else:
                model_key = model_entry
                model_name = None

            # Find matching config
            matching_config = next(
                (cfg for cfg in llm_configs if cfg.get("modelKey") == model_key),
                None
            )

            if matching_config:
                if not model_name:
                    config_data = matching_config.get("configuration", {})
                    raw_model_name = config_data.get("model", matching_config.get("modelName", model_key))
                    # Handle comma-separated model names
                    if isinstance(raw_model_name, str) and "," in raw_model_name:
                        model_name = raw_model_name.split(",")[0].strip()
                    else:
                        model_name = raw_model_name

                enriched_models.append({
                    "modelKey": model_key,
                    "modelName": model_name,
                    "provider": matching_config.get("provider", ""),
                    "isReasoning": matching_config.get("isReasoning", False),
                    "isMultimodal": matching_config.get("isMultimodal", False),
                    "isDefault": matching_config.get("isDefault", False),
                    "modelType": "llm",
                    "modelFriendlyName": matching_config.get("modelFriendlyName", model_name),
                })
            else:
                logger.warning(f"Model key {model_key} not found in LLM configs")
                enriched_models.append({
                    "modelKey": model_key,
                    "modelName": model_name or model_key,
                    "provider": "unknown",
                    "isReasoning": False,
                    "isMultimodal": False,
                    "isDefault": False,
                    "modelType": "llm",
                    "modelFriendlyName": model_name or model_key,
                })

        agent["models"] = enriched_models
    except Exception as e:
        logger.warning(f"Failed to enrich models: {e}")


def _parse_request_body(body: bytes) -> Dict[str, Any]:
    """Parse and validate JSON request body"""
    if not body:
        raise InvalidRequestError("Request body is required")

    try:
        return json.loads(body.decode('utf-8'))
    except json.JSONDecodeError as e:
        raise InvalidRequestError(f"Invalid JSON: {str(e)}")


# ============================================================================
# Chat Endpoints
# ============================================================================

@router.post("/agent-chat", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_EXECUTE))])
async def askAI(request: Request, query_info: ChatQuery) -> JSONResponse:
    """Process chat query using LangGraph agent with optimizations"""
    try:
        import time
        start_time = time.time()

        services = await get_services(request)
        logger = services["logger"]
        graph_provider = services["graph_provider"]
        reranker_service = services["reranker_service"]
        retrieval_service = services["retrieval_service"]
        config_service = services["config_service"]
        user_context = _get_user_context(request)

        # Check cache first
        cache = get_cache_manager()
        cache_context = {
            "has_internal_data": query_info.filters is not None,
            "tools": query_info.tools
        }
        cached_response = cache.get_llm_response(query_info.query, cache_context)
        if cached_response:
            logger.info(f"⚡ Cache hit! Query resolved in {(time.time() - start_time) * 1000:.0f}ms")
            return JSONResponse(content=cached_response)

        # Get user and org info
        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], logger)
        enriched_user_info = await _enrich_user_info(user_context, user_doc)
        org_info = await _get_org_info(user_context, services["graph_provider"], logger)

        # Build and execute graph
        initial_state = build_initial_state(
            query_info.model_dump(),
            enriched_user_info,
            services["llm"],
            logger,
            retrieval_service,
            graph_provider,
            reranker_service,
            config_service,
            org_info,
        )

        config = {"recursion_limit": 30}
        final_state = await agent_graph.ainvoke(initial_state, config=config)
        final_state = auto_optimize_state(final_state, logger)

        # Check memory health
        memory_health = check_memory_health(final_state, logger)
        if memory_health["status"] != "healthy":
            logger.warning(f"⚠️ Memory: {memory_health['memory_info']['total_mb']:.2f} MB")

        # Handle errors
        if final_state.get("error"):
            error = final_state["error"]
            return JSONResponse(
                status_code=error.get("status_code", 500),
                content={
                    "status": error.get("status", "error"),
                    "message": error.get("message", "An error occurred"),
                    "searchResults": [],
                    "records": [],
                }
            )

        # Get response and cache it
        response_data = final_state.get("completion_data", final_state.get("response"))

        if isinstance(response_data, JSONResponse):
            response_content = response_data.body.decode() if hasattr(response_data, 'body') else None
            if response_content:
                try:
                    response_dict = json.loads(response_content)
                    cache.set_llm_response(query_info.query, response_dict, cache_context)
                except Exception:
                    pass
        elif isinstance(response_data, dict):
            cache.set_llm_response(query_info.query, response_data, cache_context)

        total_time = (time.time() - start_time) * 1000
        logger.info(f"✅ Query completed in {total_time:.0f}ms")

        # Add performance metadata if available
        if "_performance_tracker" in final_state and isinstance(response_data, dict):
            response_data["_performance"] = final_state.get("performance_summary", {})

        return response_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in askAI: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


async def stream_response(
    query_info: Dict[str, Any],
    user_info: Dict[str, Any],
    llm: BaseChatModel,
    logger: Logger,
    retrieval_service: RetrievalService,
    graph_provider,
    reranker_service: RerankerService,
    config_service: ConfigurationService,
    org_info: Dict[str, Any] = None,
) -> AsyncGenerator[str, None]:
    """Stream agent response"""
    initial_state = build_initial_state(
        query_info,
        user_info,
        llm,
        logger,
        retrieval_service,
        graph_provider,
        reranker_service,
        config_service,
        org_info,
    )

    config = {"recursion_limit": 30}
    chunk_count = 0

    async for chunk in agent_graph.astream(initial_state, config=config, stream_mode="custom"):
        chunk_count += 1
        if isinstance(chunk, dict) and "event" in chunk:
            event_type = chunk.get('event', 'unknown')
            data = chunk.get('data', {})
            yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        else:
            logger.warning(f"Unexpected chunk format: {type(chunk)}")

    logger.info(f"Streaming completed. Total chunks: {chunk_count}")


@router.post("/agent-chat-stream", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_EXECUTE))])
async def askAIStream(request: Request, query_info: ChatQuery) -> StreamingResponse:
    """Process chat query with streaming"""
    try:
        services = await get_services(request)
        logger = services["logger"]
        graph_provider = services["graph_provider"]
        reranker_service = services["reranker_service"]
        retrieval_service = services["retrieval_service"]
        config_service = services["config_service"]
        llm = services["llm"]
        user_context = _get_user_context(request)

        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])
        enriched_user_info = await _enrich_user_info(user_context, user_doc)
        org_info = await _get_org_info(user_context, services["graph_provider"], services["logger"])

        return StreamingResponse(
            stream_response(
                query_info.model_dump(), enriched_user_info, llm, logger, retrieval_service, graph_provider, reranker_service, config_service, org_info
            ),
            media_type="text/event-stream",
        )
    except HTTPException:
        raise
    except Exception as e:
        services["logger"].error(f"Error in askAIStream: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================
# Agent Template Endpoints
# ============================================================================

@router.post("/template/create", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def create_agent_template(request: Request) -> JSONResponse:
    """Create a new agent template"""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)

        body = _parse_request_body(await request.body())
        _validate_required_fields(body, ["name", "description", "systemPrompt"])

        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])
        time = get_epoch_timestamp_in_ms()
        template_key = str(uuid.uuid4())

        template = {
            "_key": template_key,
            "name": body["name"].strip(),
            "description": body["description"].strip(),
            "startMessage": body.get("startMessage", "").strip() or "Hello! How can I help you today?",
            "systemPrompt": body["systemPrompt"].strip(),
            "tools": body.get("tools", []),
            "models": body.get("models", []),
            "memory": body.get("memory", {"type": []}),
            "tags": body.get("tags", []),
            "orgId": user_context["orgId"],
            "isActive": True,
            "createdBy": user_doc["_key"],
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
            "isDeleted": body.get("isDeleted", False),
        }

        user_template_access = {
            "_from": f"{CollectionNames.USERS.value}/{user_doc['_key']}",
            "_to": f"{CollectionNames.AGENT_TEMPLATES.value}/{template_key}",
            "role": "OWNER",
            "type": "USER",
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
        }

        result = await services["graph_provider"].batch_upsert_nodes([template], CollectionNames.AGENT_TEMPLATES.value)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to create agent template")

        result = await services["graph_provider"].batch_create_edges([user_template_access], CollectionNames.PERMISSION.value)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to create template access")

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent template created successfully",
                "template": template,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        services["logger"].error(f"Error creating template: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/template/list", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_READ))])
async def get_agent_templates(request: Request) -> JSONResponse:
    """Get all agent templates"""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)

        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])
        templates = await services["graph_provider"].get_all_agent_templates(user_doc["_key"])

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent templates retrieved successfully",
                "templates": templates or [],
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        services["logger"].error(f"Error getting templates: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/template/{template_id}", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_READ))])
async def get_agent_template(request: Request, template_id: str) -> JSONResponse:
    """Get an agent template by ID"""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)

        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])
        template = await services["graph_provider"].get_template(template_id, user_doc["_key"])

        if not template:
            raise AgentTemplateNotFoundError(template_id)

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent template retrieved successfully",
                "template": template,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        services["logger"].error(f"Error getting template: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/share-template/{template_id}", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def share_agent_template(request: Request, template_id: str) -> JSONResponse:
    """Share an agent template"""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)

        body = _parse_request_body(await request.body())
        user_ids = body.get("userIds", [])
        team_ids = body.get("teamIds", [])

        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])
        template = await services["graph_provider"].get_template(template_id, user_doc["_key"])

        if not template:
            raise AgentTemplateNotFoundError(template_id)

        result = await services["graph_provider"].share_agent_template(template_id, user_doc["_key"], user_ids, team_ids)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to share agent template")

        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "Agent template shared successfully"}
        )
    except HTTPException:
        raise
    except Exception as e:
        services["logger"].error(f"Error sharing template: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/template/{template_id}/clone", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def clone_agent_template(request: Request, template_id: str) -> JSONResponse:
    """Clone an agent template"""
    try:
        services = await get_services(request)
        cloned_template_id = await services["graph_provider"].clone_agent_template(template_id)

        if not cloned_template_id:
            raise HTTPException(status_code=500, detail="Failed to clone agent template")

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent template cloned successfully",
                "templateId": cloned_template_id,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        services["logger"].error(f"Error cloning template: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/template/{template_id}", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def delete_agent_template(request: Request, template_id: str) -> JSONResponse:
    """Delete an agent template"""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)

        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])
        result = await services["graph_provider"].delete_agent_template(template_id, user_doc["_key"])

        if not result:
            raise HTTPException(status_code=500, detail="Failed to delete agent template")

        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "Agent template deleted successfully"}
        )
    except HTTPException:
        raise
    except Exception as e:
        services["logger"].error(f"Error deleting template: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/template/{template_id}", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def update_agent_template(request: Request, template_id: str) -> JSONResponse:
    """Update an agent template"""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)

        body = _parse_request_body(await request.body())
        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])

        result = await services["graph_provider"].update_agent_template(template_id, body, user_doc["_key"])
        if not result:
            raise HTTPException(status_code=500, detail="Failed to update agent template")

        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "Agent template updated successfully"}
        )
    except HTTPException:
        raise
    except Exception as e:
        services["logger"].error(f"Error updating template: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================
# Agent CRUD Endpoints
# ============================================================================

@router.post("/create", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def create_agent(request: Request) -> JSONResponse:
    """Create a new agent using graph-based architecture"""
    try:
        services = await get_services(request)
        logger = services["logger"]
        user_context = _get_user_context(request)

        body = _parse_request_body(await request.body())
        _validate_required_fields(body, ["name"])

        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], logger)
        user_key = user_doc["_key"]
        org_key = user_context["orgId"]
        time = get_epoch_timestamp_in_ms()

        # Parse and validate models
        raw_models = body.get("models", [])
        model_entries, has_reasoning_model = _parse_models(raw_models, logger)

        if not model_entries:
            raise InvalidRequestError(
                "At least one AI model is required. Please add a model to your configuration."
            )

        if not has_reasoning_model:
            raise InvalidRequestError(
                "At least one reasoning model is required. Please add a reasoning model to your configuration."
            )

        # Parse toolsets and knowledge BEFORE starting transaction
        toolsets_with_tools = _parse_toolsets(body.get("toolsets", []), logger)
        knowledge_sources = _parse_knowledge_sources(body.get("knowledge", []), logger)

        # Validate shareWithOrg + toolsets combination BEFORE starting transaction
        share_with_org = body.get("shareWithOrg", False)
        if share_with_org and toolsets_with_tools:
            raise InvalidRequestError(
                "Agents shared with the organization cannot have toolsets. "
                "Toolsets require personal authentication and cannot be shared org-wide. "
                "Please remove all toolsets before enabling organization sharing."
            )

        # Create agent document
        agent_key = str(uuid.uuid4())
        agent = {
            "_key": agent_key,
            "name": body["name"].strip(),
            "description": body.get("description", "").strip() or "AI agent for task automation",
            "startMessage": body.get("startMessage", "").strip() or "Hello! How can I help you today?",
            "systemPrompt": body.get("systemPrompt", "").strip() or "You are a helpful assistant.",
            "instructions": body.get("instructions", "").strip() or None,
            "models": model_entries,
            "tags": body.get("tags", []) or [],
            "isActive": True,
            "createdBy": user_key,
            "updatedBy": None,
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
            "isDeleted": False,
        }

        # Wrap ALL creation operations in a single transaction
        created_toolsets = []
        failed_toolsets = []
        created_knowledge = []

        try:
            # Start transaction for ALL agent creation operations
            graph_provider = services["graph_provider"]
            transaction_id = await graph_provider.begin_transaction(
                read=[],
                write=[
                    CollectionNames.AGENT_INSTANCES.value,
                    CollectionNames.PERMISSION.value,
                    CollectionNames.AGENT_TOOLSETS.value,
                    CollectionNames.AGENT_TOOLS.value,
                    CollectionNames.AGENT_HAS_TOOLSET.value,
                    CollectionNames.TOOLSET_HAS_TOOL.value,
                    CollectionNames.AGENT_KNOWLEDGE.value,
                    CollectionNames.AGENT_HAS_KNOWLEDGE.value,
                ]
            )
            logger.debug(f"Started transaction for agent creation: {agent_key}")

            # Step 1: Create agent node
            await graph_provider.batch_upsert_nodes([agent], CollectionNames.AGENT_INSTANCES.value, transaction=transaction_id)
            logger.debug(f"Created agent node: {agent_key}")

            # Step 2: Create permission edge(s)
            # share_with_org already validated above before starting transaction
            user_permission_edge = {
                "_from": f"{CollectionNames.USERS.value}/{user_key}",
                "_to": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
                "role": "OWNER",
                "type": "USER",
                "createdAtTimestamp": time,
                "updatedAtTimestamp": time,
            }
            permission_edges = [user_permission_edge]

            # Only create org permission edge if shareWithOrg is explicitly set to True
            if share_with_org:
                org_permission_edge = {
                    "_from": f"{CollectionNames.ORGS.value}/{org_key}",
                    "_to": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
                    "role": "READER",
                    "type": "ORG",
                    "createdAtTimestamp": time,
                    "updatedAtTimestamp": time,
                }
                permission_edges.append(org_permission_edge)

            await graph_provider.batch_create_edges(permission_edges, CollectionNames.PERMISSION.value, transaction=transaction_id)
            logger.debug(f"Created permission edge(s) for agent: {agent_key} (shareWithOrg={share_with_org})")

            # Step 3: Create toolsets and tools (within same transaction)
            if toolsets_with_tools:
                toolset_mapping = {}
                toolset_nodes = []

                # Prepare toolset nodes
                for toolset_name, toolset_data in toolsets_with_tools.items():
                    from app.agents.constants.toolset_constants import (
                        normalize_app_name,
                    )

                    toolset_key = str(uuid.uuid4())
                    display_name = toolset_data["displayName"]
                    toolset_type = toolset_data["type"]
                    tools_list = toolset_data["tools"]

                    toolset_node = {
                        "_key": toolset_key,
                        "name": normalize_app_name(toolset_name),
                        "displayName": display_name,
                        "type": toolset_type,
                        "userId": user_context["userId"],
                        "createdBy": user_key,
                        "createdAtTimestamp": time,
                        "updatedAtTimestamp": time
                    }

                    toolset_nodes.append(toolset_node)
                    toolset_mapping[toolset_name] = {
                        "key": toolset_key,
                        "displayName": display_name,
                        "tools": tools_list
                    }

                # Batch create toolset nodes
                if toolset_nodes:
                    await graph_provider.batch_upsert_nodes(toolset_nodes, CollectionNames.AGENT_TOOLSETS.value, transaction=transaction_id)

                # Create agent -> toolset edges
                agent_toolset_edges = []
                for toolset_name, toolset_info in toolset_mapping.items():
                    agent_toolset_edges.append({
                        "_from": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
                        "_to": f"{CollectionNames.AGENT_TOOLSETS.value}/{toolset_info['key']}",
                        "createdAtTimestamp": time,
                        "updatedAtTimestamp": time,
                    })
                if agent_toolset_edges:
                    await graph_provider.batch_create_edges(agent_toolset_edges, CollectionNames.AGENT_HAS_TOOLSET.value, transaction=transaction_id)

                # Create tool nodes and edges
                tool_mapping = {}
                tool_nodes = []
                toolset_tool_edges = []

                for toolset_name, toolset_info in toolset_mapping.items():
                    for tool_data in toolset_info["tools"]:
                        tool_name = tool_data["name"]
                        full_name = tool_data["fullName"]
                        description = tool_data.get("description", "")
                        tool_key = str(uuid.uuid4())

                        tool_node = {
                            "_key": tool_key,
                            "name": tool_name,
                            "fullName": full_name,
                            "toolsetName": toolset_name,
                            "description": description,
                            "createdBy": user_key,
                            "createdAtTimestamp": time,
                            "updatedAtTimestamp": time
                        }
                        tool_nodes.append(tool_node)

                        tool_mapping[full_name] = {
                            "key": tool_key,
                            "name": tool_name,
                            "toolset": toolset_name
                        }

                        # Create toolset -> tool edge
                        toolset_tool_edges.append({
                            "_from": f"{CollectionNames.AGENT_TOOLSETS.value}/{toolset_info['key']}",
                            "_to": f"{CollectionNames.AGENT_TOOLS.value}/{tool_key}",
                            "createdAtTimestamp": time,
                            "updatedAtTimestamp": time,
                        })

                # Batch create tool nodes
                if tool_nodes:
                    await graph_provider.batch_upsert_nodes(tool_nodes, CollectionNames.AGENT_TOOLS.value, transaction=transaction_id)

                # Batch create toolset -> tool edges
                if toolset_tool_edges:
                    await graph_provider.batch_create_edges(toolset_tool_edges, CollectionNames.TOOLSET_HAS_TOOL.value, transaction=transaction_id)

                # Build response for created toolsets
                for toolset_name, toolset_info in toolset_mapping.items():
                    created_tools = []
                    for tool_data in toolset_info["tools"]:
                        full_name = tool_data["fullName"]
                        if full_name in tool_mapping:
                            created_tools.append({
                                "name": tool_mapping[full_name]["name"],
                                "fullName": full_name,
                                "key": tool_mapping[full_name]["key"]
                            })

                    created_toolsets.append({
                        "name": toolset_name,
                        "displayName": toolset_info["displayName"],
                        "key": toolset_info["key"],
                        "tools": created_tools
                    })

                logger.debug(f"Created {len(created_toolsets)} toolset(s) for agent: {agent_key}")

            # Step 4: Create knowledge sources (within same transaction)
            if knowledge_sources:
                knowledge_mapping = {}
                knowledge_nodes = []

                # Prepare knowledge nodes
                for connector_id, knowledge_data in knowledge_sources.items():
                    knowledge_key = str(uuid.uuid4())
                    filters = knowledge_data["filters"]

                    # Schema expects filters as stringified JSON
                    filters_str = json.dumps(filters) if isinstance(filters, dict) else str(filters)

                    knowledge_node = {
                        "_key": knowledge_key,
                        "connectorId": connector_id,
                        "filters": filters_str,
                        "createdBy": user_key,
                        "createdAtTimestamp": time,
                        "updatedAtTimestamp": time
                    }
                    knowledge_nodes.append(knowledge_node)

                    knowledge_mapping[connector_id] = {
                        "key": knowledge_key,
                        "filters": filters
                    }

                # Batch create knowledge nodes
                if knowledge_nodes:
                    await graph_provider.batch_upsert_nodes(knowledge_nodes, CollectionNames.AGENT_KNOWLEDGE.value, transaction=transaction_id)

                # Create agent -> knowledge edges
                agent_knowledge_edges = []
                for connector_id, knowledge_info in knowledge_mapping.items():
                    agent_knowledge_edges.append({
                        "_from": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
                        "_to": f"{CollectionNames.AGENT_KNOWLEDGE.value}/{knowledge_info['key']}",
                        "createdAtTimestamp": time,
                        "updatedAtTimestamp": time,
                    })
                if agent_knowledge_edges:
                    await graph_provider.batch_create_edges(agent_knowledge_edges, CollectionNames.AGENT_HAS_KNOWLEDGE.value, transaction=transaction_id)

                # Build response for created knowledge
                for connector_id, knowledge_info in knowledge_mapping.items():
                    created_knowledge.append({
                        "connectorId": connector_id,
                        "key": knowledge_info["key"],
                        "filters": knowledge_info["filters"]
                    })

                logger.debug(f"Created {len(created_knowledge)} knowledge source(s) for agent: {agent_key}")

            # Commit transaction - ALL or NOTHING
            await graph_provider.commit_transaction(transaction_id)
            transaction_id = None
            logger.info(f"✅ Successfully created agent {agent_key} with all components")

        except Exception as e:
            # Rollback on ANY error - ensures no partial state
            if transaction_id:
                try:
                    await graph_provider.rollback_transaction(transaction_id)
                    logger.warning(f"Rolled back agent creation transaction for {agent_key}")
                except Exception as abort_error:
                    logger.error(f"Failed to abort transaction: {abort_error}")

            logger.error(f"Failed to create agent {agent_key}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create agent: {str(e)}"
            )

        # Build response
        response_agent = {
            **agent,
            "toolsets": created_toolsets,
            "knowledge": created_knowledge,
        }

        status = "partial_success" if failed_toolsets else "success"
        message = f"Agent created with warnings: {len(failed_toolsets)} toolset(s) failed" if failed_toolsets else "Agent created successfully"

        return JSONResponse(
            status_code=200,
            content={
                "status": status,
                "message": message,
                "agent": response_agent,
                "warnings": failed_toolsets if failed_toolsets else None,
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating agent: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/{agent_id}", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_READ))])
async def get_agent(request: Request, agent_id: str) -> JSONResponse:
    """Get an agent by ID with enriched data"""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)
        org_key = user_context["orgId"]

        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])
        agent = await services["graph_provider"].get_agent(agent_id, user_doc["_key"], org_key)

        if not agent:
            raise AgentNotFoundError(agent_id)

        # Enrich models with configurations
        await _enrich_agent_models(agent, services["config_service"], services["logger"])
        agent.pop("modelsEnriched", None)

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent retrieved successfully",
                "agent": agent,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        services["logger"].error(f"Error getting agent: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_READ))])
async def get_agents(request: Request) -> JSONResponse:
    """Get all agents"""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)
        org_key = user_context["orgId"]

        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])
        agents = await services["graph_provider"].get_all_agents(user_doc["_key"], org_key)

        if not agents:
            raise HTTPException(status_code=404, detail="No agents found")

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agents retrieved successfully",
                "agents": agents,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        services["logger"].error(f"Error getting agents: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{agent_id}", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def update_agent(request: Request, agent_id: str) -> JSONResponse:
    """Update an agent using graph-based architecture"""
    try:
        services = await get_services(request)
        logger = services["logger"]
        user_context = _get_user_context(request)

        body = _parse_request_body(await request.body())
        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], logger)
        user_key = user_doc["_key"]
        org_key = user_context["orgId"]

        # Validate models if provided in update body
        if "models" in body:
            raw_models = body.get("models", [])
            model_entries, has_reasoning_model = _parse_models(raw_models, logger)

            if not model_entries:
                raise InvalidRequestError(
                    "At least one AI model is required. Please add a model to your configuration."
                )

            if not has_reasoning_model:
                raise InvalidRequestError(
                    "At least one reasoning model is required. Please add a reasoning model to your configuration."
                )

        # Check permissions
        agent = await services["graph_provider"].get_agent(agent_id, user_key, org_key)
        if not agent:
            raise AgentNotFoundError(agent_id)

        if not agent.get("can_edit", False):
            raise PermissionDeniedError("edit this agent (only owner can edit)")

        # Handle shareWithOrg flag changes
        if "shareWithOrg" in body:
            new_share_with_org = bool(body.get("shareWithOrg", False))
            current_share_with_org = bool(agent.get("shareWithOrg", False))

            if new_share_with_org:
                # Turning ON org sharing: validate no toolsets exist or being added
                has_existing_toolsets = bool(agent.get("toolsets", []))
                has_new_toolsets = bool(body.get("toolsets", None) is not None and body.get("toolsets", []))
                if has_existing_toolsets or has_new_toolsets:
                    raise InvalidRequestError(
                        "Agents shared with the organization cannot have toolsets. "
                        "Toolsets require personal authentication and cannot be shared org-wide. "
                        "Please remove all toolsets before enabling organization sharing."
                    )
                # Create the org permission edge
                time = get_epoch_timestamp_in_ms()
                org_permission_edge = {
                    "_from": f"{CollectionNames.ORGS.value}/{org_key}",
                    "_to": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_id}",
                    "role": "READER",
                    "type": "ORG",
                    "createdAtTimestamp": time,
                    "updatedAtTimestamp": time,
                }
                await services["graph_provider"].batch_create_edges(
                    [org_permission_edge], CollectionNames.PERMISSION.value
                )
                logger.info(f"Created org permission edge for agent {agent_id}")

            elif not new_share_with_org and current_share_with_org:
                # Turning OFF org sharing: delete the org permission edge using the standard delete_edge method
                await services["graph_provider"].delete_edge(
                    from_id=org_key,
                    from_collection=CollectionNames.ORGS.value,
                    to_id=agent_id,
                    to_collection=CollectionNames.AGENT_INSTANCES.value,
                    collection=CollectionNames.PERMISSION.value
                )
                logger.info(f"Deleted org permission edge for agent {agent_id}")

        # Also validate: if adding toolsets to an org-shared agent, block it
        if "toolsets" in body and body.get("toolsets"):
            is_org_shared = bool(body.get("shareWithOrg", agent.get("shareWithOrg", False)))
            if is_org_shared:
                raise InvalidRequestError(
                    "Cannot add toolsets to an agent that is shared with the organization. "
                    "Disable organization sharing first, or remove the toolsets."
                )

        # Update agent document
        result = await services["graph_provider"].update_agent(agent_id, body, user_key, org_key)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to update agent")

        # Update toolsets if provided in request (even if empty array - means delete all)
        if "toolsets" in body:
            # Parse toolsets first to validate before deletion
            toolsets_with_tools = _parse_toolsets(body.get("toolsets", []), logger)

            # Use transaction for atomic delete-then-create operation
            graph_provider = services["graph_provider"]
            transaction_id = None
            try:
                # Start transaction for atomic operations
                transaction_id = await graph_provider.begin_transaction(
                    read=[],
                    write=[
                        CollectionNames.AGENT_HAS_TOOLSET.value,
                        CollectionNames.AGENT_TOOLSETS.value,
                        CollectionNames.TOOLSET_HAS_TOOL.value,
                        CollectionNames.AGENT_TOOLS.value
                    ]
                )
                logger.debug(f"Started transaction for toolset update on agent {agent_id}")

                agent_full_id = f"{CollectionNames.AGENT_INSTANCES.value}/{agent_id}"

                # ========== PHASE 1: GATHER ALL INFORMATION (READ ONLY) ==========

                # Get all toolset edges from agent
                toolset_edges = await graph_provider.get_edges_from_node(
                    agent_full_id,
                    CollectionNames.AGENT_HAS_TOOLSET.value,
                    transaction=transaction_id
                )

                # Extract toolset keys and full IDs
                toolset_keys = []
                toolset_full_ids = []
                for edge in toolset_edges:
                    toolset_full_id = edge.get("_to")
                    if toolset_full_id:
                        toolset_full_ids.append(toolset_full_id)
                        parts = toolset_full_id.split("/", 1)
                        if len(parts) == SPLIT_PATH_EXPECTED_PARTS:
                            toolset_keys.append(parts[1])

                logger.debug(f"Found {len(toolset_keys)} toolset(s) connected to agent {agent_id}")

                # Get all tool edges for each toolset
                all_tool_keys = []
                all_tool_full_ids = []
                for toolset_full_id in toolset_full_ids:
                    tool_edges = await graph_provider.get_edges_from_node(
                        toolset_full_id,
                        CollectionNames.TOOLSET_HAS_TOOL.value,
                        transaction=transaction_id
                    )

                    for edge in tool_edges:
                        tool_full_id = edge.get("_to")
                        if tool_full_id:
                            all_tool_full_ids.append(tool_full_id)
                            parts = tool_full_id.split("/", 1)
                            if len(parts) == SPLIT_PATH_EXPECTED_PARTS:
                                all_tool_keys.append(parts[1])

                logger.debug(f"Found {len(all_tool_keys)} tool(s) connected to toolsets")

                # ========== PHASE 2: DELETE FROM LEAVES TO ROOT ==========

                # Step 1: Delete toolset -> tool edges (TOOLSET_HAS_TOOL)
                # This must be done first before deleting tool nodes
                total_tool_edges_deleted = 0
                for tool_full_id in all_tool_full_ids:
                    count = await graph_provider.delete_all_edges_for_node(
                        tool_full_id,
                        CollectionNames.TOOLSET_HAS_TOOL.value,
                        transaction=transaction_id
                    )
                    total_tool_edges_deleted += count

                logger.debug(f"Deleted {total_tool_edges_deleted} toolset->tool edge(s)")

                # Step 2: Delete tool nodes (now safe, all their edges are gone)
                deleted_tool_nodes = 0
                if all_tool_keys:
                    result = await graph_provider.delete_nodes(
                        all_tool_keys,
                        CollectionNames.AGENT_TOOLS.value,
                        transaction=transaction_id
                    )
                    deleted_tool_nodes = len(all_tool_keys) if result else 0
                    logger.debug(f"Deleted {deleted_tool_nodes} tool node(s)")

                # Step 3: Delete agent -> toolset edges (AGENT_HAS_TOOLSET)
                # Note: We don't check TOOLSET_HAS_TOOL again - those edges were deleted in Step 1
                total_toolset_edges_deleted = 0
                for toolset_full_id in toolset_full_ids:
                    count = await graph_provider.delete_all_edges_for_node(
                        toolset_full_id,
                        CollectionNames.AGENT_HAS_TOOLSET.value,
                        transaction=transaction_id
                    )
                    total_toolset_edges_deleted += count

                logger.debug(f"Deleted {total_toolset_edges_deleted} agent->toolset edge(s)")

                # Step 4: Delete toolset nodes (now safe, all their edges are gone)
                deleted_toolset_nodes = 0
                if toolset_keys:
                    result = await graph_provider.delete_nodes(
                        toolset_keys,
                        CollectionNames.AGENT_TOOLSETS.value,
                        transaction=transaction_id
                    )
                    deleted_toolset_nodes = len(toolset_keys) if result else 0
                    logger.debug(f"Deleted {deleted_toolset_nodes} toolset node(s)")

                logger.info(
                    f"Deleted for agent {agent_id}: "
                    f"{deleted_tool_nodes} tool(s), {deleted_toolset_nodes} toolset(s), "
                    f"{total_tool_edges_deleted + total_toolset_edges_deleted} edge(s) total"
                )

                # Commit transaction after deletion
                await graph_provider.commit_transaction(transaction_id)
                transaction_id = None
                logger.debug(f"Committed transaction for toolset deletion on agent {agent_id}")

            except Exception as e:
                if transaction_id:
                    try:
                        await graph_provider.rollback_transaction(transaction_id)
                        logger.warning(f"Aborted transaction for toolset update on agent {agent_id}")
                    except Exception as abort_error:
                        logger.error(f"Failed to abort transaction: {abort_error}")
                logger.error(f"Failed to delete toolset nodes and edges for agent {agent_id}: {e}", exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to delete toolset nodes and edges: {str(e)}"
                )

            # Create new toolset nodes, tool nodes, and edges only if there are toolsets to create
            if toolsets_with_tools:
                try:
                    created_toolsets, failed_toolsets = await _create_toolset_edges(
                        agent_id, toolsets_with_tools, user_context, user_key,
                        services["graph_provider"], logger
                    )
                    if failed_toolsets:
                        logger.warning(
                            f"Agent {agent_id}: {len(failed_toolsets)} toolset(s) failed to create: {failed_toolsets}"
                        )
                    logger.info(f"Created {len(created_toolsets)} toolset(s) for agent {agent_id}")
                except Exception as e:
                    logger.error(
                        f"Failed to create toolset edges for agent {agent_id} after deletion: {e}",
                        exc_info=True
                    )
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to create toolset edges: {str(e)}"
                    )
            else:
                logger.info(f"All toolsets removed for agent {agent_id}")

        # Update knowledge if provided in request (even if empty array - means delete all)
        if "knowledge" in body:
            # Parse knowledge sources first to validate before deletion
            knowledge_sources = _parse_knowledge_sources(body.get("knowledge", []), logger)

            # Use transaction for atomic delete-then-create operation
            graph_provider = services["graph_provider"]
            transaction_id = None
            try:
                # Start transaction for atomic operations
                transaction_id = await graph_provider.begin_transaction(
                    read=[],
                    write=[
                        CollectionNames.AGENT_HAS_KNOWLEDGE.value,
                        CollectionNames.AGENT_KNOWLEDGE.value
                    ]
                )
                logger.debug(f"Started transaction for knowledge update on agent {agent_id}")

                agent_full_id = f"{CollectionNames.AGENT_INSTANCES.value}/{agent_id}"

                # ========== PHASE 1: GATHER ALL INFORMATION (READ ONLY) ==========

                # Get all knowledge edges from agent
                knowledge_edges = await graph_provider.get_edges_from_node(
                    agent_full_id,
                    CollectionNames.AGENT_HAS_KNOWLEDGE.value,
                    transaction=transaction_id
                )

                # Extract knowledge keys and full IDs
                knowledge_keys = []
                knowledge_full_ids = []
                for edge in knowledge_edges:
                    knowledge_full_id = edge.get("_to")
                    if knowledge_full_id:
                        knowledge_full_ids.append(knowledge_full_id)
                        parts = knowledge_full_id.split("/", 1)
                        if len(parts) == SPLIT_PATH_EXPECTED_PARTS:
                            knowledge_keys.append(parts[1])

                logger.debug(f"Found {len(knowledge_keys)} knowledge node(s) connected to agent {agent_id}")

                # ========== PHASE 2: DELETE EDGES THEN NODES ==========

                # Step 1: Delete agent -> knowledge edges
                total_knowledge_edges_deleted = 0
                for knowledge_full_id in knowledge_full_ids:
                    count = await graph_provider.delete_all_edges_for_node(
                        knowledge_full_id,
                        CollectionNames.AGENT_HAS_KNOWLEDGE.value,
                        transaction=transaction_id
                    )
                    total_knowledge_edges_deleted += count

                logger.debug(f"Deleted {total_knowledge_edges_deleted} agent->knowledge edge(s)")

                # Step 2: Delete knowledge nodes (now safe, all their edges are gone)
                deleted_knowledge_nodes = 0
                if knowledge_keys:
                    result = await graph_provider.delete_nodes(
                        knowledge_keys,
                        CollectionNames.AGENT_KNOWLEDGE.value,
                        transaction=transaction_id
                    )
                    deleted_knowledge_nodes = len(knowledge_keys) if result else 0
                    logger.debug(f"Deleted {deleted_knowledge_nodes} knowledge node(s)")

                logger.info(
                    f"Deleted for agent {agent_id}: "
                    f"{deleted_knowledge_nodes} knowledge node(s), {total_knowledge_edges_deleted} edge(s)"
                )

                # Commit transaction after deletion
                await graph_provider.commit_transaction(transaction_id)
                transaction_id = None
                logger.debug(f"Committed transaction for knowledge deletion on agent {agent_id}")

            except Exception as e:
                if transaction_id:
                    try:
                        await graph_provider.rollback_transaction(transaction_id)
                        logger.warning(f"Aborted transaction for knowledge update on agent {agent_id}")
                    except Exception as abort_error:
                        logger.error(f"Failed to abort transaction: {abort_error}")
                logger.error(f"Failed to delete knowledge nodes and edges for agent {agent_id}: {e}", exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to delete knowledge nodes and edges: {str(e)}"
                )

            # Create new knowledge nodes and edges only if there are knowledge sources to create
            if knowledge_sources:
                try:
                    created_knowledge = await _create_knowledge_edges(
                        agent_id, knowledge_sources, user_key, services["graph_provider"], logger
                    )
                    logger.info(f"Created {len(created_knowledge)} knowledge source(s) for agent {agent_id}")
                except Exception as e:
                    logger.error(
                        f"Failed to create knowledge edges for agent {agent_id} after deletion: {e}",
                        exc_info=True
                    )
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to create knowledge edges: {str(e)}"
                    )
            else:
                logger.info(f"All knowledge sources removed for agent {agent_id}")

        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "Agent updated successfully"}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating agent: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/{agent_id}", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def delete_agent(request: Request, agent_id: str) -> JSONResponse:
    """Delete an agent"""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)
        org_key = user_context["orgId"]

        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])
        agent = await services["graph_provider"].get_agent(agent_id, user_doc["_key"], org_key)

        if not agent:
            raise AgentNotFoundError(agent_id)

        if not agent.get("can_delete", False):
            raise PermissionDeniedError("delete this agent (only owner can delete)")

        result = await services["graph_provider"].delete_agent(agent_id, user_doc["_key"], org_key)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to delete agent")

        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "Agent deleted successfully"}
        )
    except HTTPException:
        raise
    except Exception as e:
        services["logger"].error(f"Error deleting agent: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================
# Agent Sharing & Permissions
# ============================================================================

@router.post("/{agent_id}/share", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def share_agent(request: Request, agent_id: str) -> JSONResponse:
    """Share an agent"""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)
        org_key = user_context["orgId"]

        body = _parse_request_body(await request.body())
        user_ids = body.get("userIds", [])
        team_ids = body.get("teamIds", [])

        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])
        agent = await services["graph_provider"].get_agent(agent_id, user_doc["_key"], org_key)

        if not agent:
            raise AgentNotFoundError(agent_id)

        if not agent.get("can_share", False):
            raise PermissionDeniedError("share this agent")

        result = await services["graph_provider"].share_agent(agent_id, user_doc["_key"], org_key, user_ids, team_ids)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to share agent")

        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "Agent shared successfully"}
        )
    except HTTPException:
        raise
    except Exception as e:
        services["logger"].error(f"Error sharing agent: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{agent_id}/unshare", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def unshare_agent(request: Request, agent_id: str) -> JSONResponse:
    """Unshare an agent"""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)
        org_key = user_context["orgId"]

        body = _parse_request_body(await request.body())
        user_ids = body.get("userIds", [])
        team_ids = body.get("teamIds", [])

        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])
        agent = await services["graph_provider"].get_agent(agent_id, user_doc["_key"], org_key)

        if not agent:
            raise AgentNotFoundError(agent_id)

        if not agent.get("can_share", False):
            raise PermissionDeniedError("unshare this agent")

        result = await services["graph_provider"].unshare_agent(agent_id, user_doc["_key"], org_key, user_ids, team_ids)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to unshare agent")

        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "Agent unshared successfully"}
        )
    except HTTPException:
        raise
    except Exception as e:
        services["logger"].error(f"Error unsharing agent: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{agent_id}/permissions", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_READ))])
async def get_agent_permissions(request: Request, agent_id: str) -> JSONResponse:
    """Get all permissions for an agent"""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)
        org_key = user_context["orgId"]

        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])
        permissions = await services["graph_provider"].get_agent_permissions(agent_id, user_doc["_key"], org_key)

        # if permissions is None:
            # raise PermissionDeniedError("view permissions for this agent")

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent permissions retrieved successfully",
                "permissions": permissions,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        services["logger"].error(f"Error getting permissions: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{agent_id}/permissions", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def update_agent_permission(request: Request, agent_id: str) -> JSONResponse:
    """Update permission role for a user on an agent"""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)
        org_key = user_context["orgId"]

        body = _parse_request_body(await request.body())
        user_ids = body.get("userIds", [])
        team_ids = body.get("teamIds", [])
        role = body.get("role")

        if not role:
            raise InvalidRequestError("Role is required")

        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])
        result = await services["graph_provider"].update_agent_permission(agent_id, user_doc["_key"], org_key, user_ids, team_ids, role)

        if not result:
            raise HTTPException(status_code=500, detail="Failed to update agent permission")

        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "Agent permission updated successfully"}
        )
    except HTTPException:
        raise
    except Exception as e:
        services["logger"].error(f"Error updating permission: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================
# Agent Chat Endpoints
# ============================================================================

@router.post("/{agent_id}/chat", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_EXECUTE))])
async def chat(request: Request, agent_id: str, chat_query: ChatQuery) -> JSONResponse:
    """Chat with an agent"""
    try:
        services = await get_services(request)
        logger = services["logger"]
        graph_provider = services["graph_provider"]
        retrieval_service = services["retrieval_service"]
        llm = services["llm"]
        reranker_service = services["reranker_service"]
        config_service = services["config_service"]
        user_context = _get_user_context(request)
        org_key = user_context["orgId"]


        # Get user and org info
        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], logger)
        enriched_user_info = await _enrich_user_info(user_context, user_doc)
        org_info = await _get_org_info(user_context, services["graph_provider"], logger)

        # Get agent
        agent = await services["graph_provider"].get_agent(agent_id, user_doc["_key"], org_key)
        if not agent:
            raise AgentNotFoundError(agent_id)

        # Build filters from knowledge array (new format)
        filters = chat_query.filters.copy() if chat_query.filters else {}

        if not chat_query.filters:
            # Extract knowledge sources from agent's knowledge array
            agent_knowledge = agent.get("knowledge", [])
            knowledge_connector_ids = []
            kb_record_groups = []

            for k in agent_knowledge:
                if isinstance(k, dict):
                    connector_id = k.get("connectorId")
                    if connector_id:
                        knowledge_connector_ids.append(connector_id)

                    # Extract KB record groups from filters
                    filters_data = k.get("filters", {})
                    if isinstance(filters_data, str):
                        try:
                            filters_data = json.loads(filters_data)
                        except json.JSONDecodeError:
                            filters_data = {}

                    record_groups = filters_data.get("recordGroups", [])
                    if record_groups:
                        # Check if this is a KB connector (connectorName == "KB")
                        # For KBs, the recordGroups contain the KB IDs
                        kb_record_groups.extend(record_groups)

            filters = {
                "apps": knowledge_connector_ids,
                "kb": kb_record_groups,
                "vectorDBs": agent.get("vectorDBs", []),
                "connectors": agent.get("connectors", [])
            }

        # Override with chat query filters if provided
        if chat_query.filters:
            for key in ["apps", "kb", "vectorDBs"]:
                if chat_query.filters.get(key) is not None:
                    filters[key] = chat_query.filters[key]

        if agent.get("connectors"):
            filters["connectors"] = agent.get("connectors", [])

        # Build query info
        query_info = {
            "query": chat_query.query,
            "limit": chat_query.limit,
            "messages": [],
            "previous_conversations": chat_query.previousConversations,
            "quickMode": chat_query.quickMode,
            "chatMode": chat_query.chatMode,
            "retrievalMode": chat_query.retrievalMode,
            "filters": filters,
            "tools": chat_query.tools if chat_query.tools is not None else agent.get("tools"),
            "systemPrompt": agent.get("systemPrompt"),
            "instructions": agent.get("instructions"),
            "timezone": chat_query.timezone,
            "currentTime": chat_query.currentTime,
        }

        # Execute graph
        initial_state = build_initial_state(
            query_info,
            enriched_user_info,
            llm,
            logger,
            retrieval_service,
            graph_provider,
            reranker_service,
            config_service,
            org_info,
        )

        config = {"recursion_limit": 50}
        final_state = await agent_graph.ainvoke(initial_state, config=config)

        # Handle errors
        if final_state.get("error"):
            error = final_state["error"]
            return JSONResponse(
                status_code=error.get("status_code", 500),
                content={
                    "status": error.get("status", "error"),
                    "message": error.get("message", "An error occurred"),
                    "searchResults": [],
                    "records": [],
                }
            )

        return final_state.get("completion_data", final_state["response"])

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in chat: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{agent_id}/chat/stream", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_EXECUTE))])
async def chat_stream(request: Request, agent_id: str) -> StreamingResponse:
    """Chat with an agent using streaming response"""
    try:
        from app.agents.constants.toolset_constants import get_toolset_config_path

        services = await get_services(request)
        logger = services["logger"]
        config_service = services["config_service"]
        graph_provider = services["graph_provider"]
        retrieval_service = services["retrieval_service"]
        # llm = services["llm"]
        reranker_service = services["reranker_service"]
        config_service = services["config_service"]
        user_context = _get_user_context(request)
        org_key = user_context["orgId"]

        body = _parse_request_body(await request.body())
        chat_query = ChatQuery(**body)

        # Get user and org info first (needed to fetch agent)
        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], logger)
        enriched_user_info = await _enrich_user_info(user_context, user_doc)
        org_info = await _get_org_info(user_context, services["graph_provider"], logger)

        # Get agent before LLM init so we can fall back to its model config
        agent = await services["graph_provider"].get_agent(agent_id, user_doc["_key"], org_key)
        if not agent:
            raise AgentNotFoundError(agent_id)

        # Determine model key/name: prefer explicit query params, then agent's first model
        model_key = chat_query.modelKey
        model_name = chat_query.modelName
        if not model_key and not model_name:
            agent_models = agent.get("models", [])
            if agent_models:
                first_model = agent_models[0]
                if isinstance(first_model, str) and "_" in first_model:
                    parts = first_model.split("_", 1)
                    model_key = parts[0]
                    model_name = parts[1] if len(parts) > 1 else None
                elif isinstance(first_model, str):
                    model_key = first_model
                elif isinstance(first_model, dict):
                    model_key = first_model.get("modelKey")
                    model_name = first_model.get("modelName")
            if model_key:
                logger.info(f"Using agent's first model for LLM: modelKey={model_key}, modelName={model_name}")

        # Get LLM for chat
        llm = (await get_llm_for_chat(
            services["config_service"],
            model_key,
            model_name,
            chat_query.chatMode
        ))[0]

        if not llm:
            raise LLMInitializationError()

        # Get and filter toolsets
        agent_toolsets = agent.get("toolsets", [])
        if chat_query.tools:
            enabled_tools_set = set(chat_query.tools)
            filtered_toolsets = []
            for toolset in agent_toolsets:
                toolset_copy = dict(toolset)
                filtered_tools = [
                    tool for tool in toolset.get("tools", [])
                    if tool.get("fullName") in enabled_tools_set
                ]
                if filtered_tools:
                    toolset_copy["tools"] = filtered_tools
                    filtered_toolsets.append(toolset_copy)
            agent_toolsets = filtered_toolsets

        # Load toolset configs from etcd using the EXECUTING user's ID, not the owner's.
        # This ensures that when a shared agent is executed, the credentials of the
        # user making the request are used — not the agent creator's credentials.
        # All lookups are done concurrently to minimise latency.
        from app.agents.constants.toolset_constants import normalize_app_name

        executing_user_id = user_context["userId"]
        toolset_configs: dict = {}

        # Filter to toolsets that actually have a name before the concurrent fetch
        named_toolsets = [t for t in agent_toolsets if t.get("name")]

        if named_toolsets:
            import asyncio as _asyncio

            async def _fetch_toolset_config(toolset: dict):
                """Return (toolset, config_or_None) without raising."""
                toolset_name = toolset["name"]
                try:
                    etcd_path = get_toolset_config_path(executing_user_id, toolset_name)
                    config = await services["config_service"].get_config(etcd_path)
                    return toolset, config
                except Exception as exc:
                    logger.warning(f"Failed to load config for toolset '{toolset_name}': {exc}")
                    return toolset, None

            # Fetch ALL toolset configs in parallel
            fetch_results = await _asyncio.gather(*[_fetch_toolset_config(t) for t in named_toolsets])

            configured_toolsets = []
            missing_toolset_display_names: list[str] = []        # no config found at all
            unauthenticated_toolset_display_names: list[str] = []  # config exists but OAuth not completed

            for toolset, config in fetch_results:
                toolset_name = toolset["name"]
                normalized_name = normalize_app_name(toolset_name)
                display_name = toolset.get("displayName") or toolset_name.replace("_", " ").title()

                if config and config.get("isAuthenticated", False):
                    # Fully configured and authenticated — allow
                    synthetic_id = f"{executing_user_id}_{normalized_name}"
                    toolset_configs[synthetic_id] = config
                    configured_toolsets.append(toolset)
                elif config:
                    # Config saved but authentication not completed (e.g. OAuth flow pending)
                    unauthenticated_toolset_display_names.append(display_name)
                    logger.warning(
                        f"Toolset '{toolset_name}' is configured but not authenticated for user "
                        f"'{executing_user_id}'. User needs to complete the authentication flow."
                    )
                else:
                    # No config found at all
                    missing_toolset_display_names.append(display_name)
                    logger.warning(
                        f"Toolset config not found for user '{executing_user_id}' / toolset '{toolset_name}'. "
                        "User needs to configure this integration."
                    )

            # Hard-block if ANY toolset is either unconfigured or unauthenticated
            if missing_toolset_display_names or unauthenticated_toolset_display_names:
                problem_parts = []
                if missing_toolset_display_names:
                    missing_list = ", ".join(f"'{n}'" for n in missing_toolset_display_names)
                    problem_parts.append(f"not configured: {missing_list}")
                if unauthenticated_toolset_display_names:
                    unauth_list = ", ".join(f"'{n}'" for n in unauthenticated_toolset_display_names)
                    problem_parts.append(f"not authenticated: {unauth_list}")

                error_message = (
                    f"This agent requires the following toolset(s) to be set up — "
                    f"{'; '.join(problem_parts)}. "
                    "Please connect your account(s) in Settings → Toolsets before using this agent."
                )
                logger.info(
                    f"Blocking agent {agent_id} execution for user '{executing_user_id}': "
                    f"toolset issue(s) — {'; '.join(problem_parts)}"
                )

                async def _toolset_config_error_stream() -> AsyncGenerator[str, None]:
                    yield f"event: error\ndata: {json.dumps({'message': error_message, 'type': 'toolset_config_missing'})}\n\n"

                return StreamingResponse(_toolset_config_error_stream(), media_type="text/event-stream")

            agent_toolsets = configured_toolsets

        # Override the stored userId on each toolset with the executing user's ID so that
        # _build_tool_to_toolset_map (in chat_state.py) constructs synthetic IDs that
        # match the keys we just stored in toolset_configs above.
        for toolset in agent_toolsets:
            toolset["userId"] = executing_user_id

        # Build filters and knowledge from agent's knowledge sources
        agent_knowledge = agent.get("knowledge", [])
        filters = chat_query.filters.copy() if chat_query.filters else {}

        if not chat_query.filters:
            # No explicit filters supplied — derive everything from the agent's knowledge config
            knowledge_connector_ids = []
            kb_record_groups = []

            for k in agent_knowledge:
                if isinstance(k, dict):
                    connector_id = k.get("connectorId")
                    if connector_id:
                        knowledge_connector_ids.append(connector_id)

                    # Parse nested filters (stored as JSON string or dict)
                    filters_data = k.get("filters", {})
                    if isinstance(filters_data, str):
                        try:
                            filters_data = json.loads(filters_data)
                        except json.JSONDecodeError:
                            filters_data = {}

                    record_groups = filters_data.get("recordGroups", [])
                    if record_groups:
                        kb_record_groups.extend(record_groups)

            filters = {
                "apps": knowledge_connector_ids,
                "kb": kb_record_groups,
            }
            logger.info(f"Filters: {filters}")
        else:
            # Explicit filters supplied — override individual keys where provided,
            # but fall back to agent's knowledge for keys that are absent.
            if "apps" not in chat_query.filters or chat_query.filters["apps"] is None:
                knowledge_connector_ids = [
                    k.get("connectorId") for k in agent_knowledge
                    if isinstance(k, dict) and k.get("connectorId")
                ]
                filters["apps"] = knowledge_connector_ids

            if "kb" not in chat_query.filters or chat_query.filters["kb"] is None:
                kb_record_groups = []
                for k in agent_knowledge:
                    if isinstance(k, dict):
                        filters_data = k.get("filters", {})
                        if isinstance(filters_data, str):
                            try:
                                filters_data = json.loads(filters_data)
                            except json.JSONDecodeError:
                                filters_data = {}
                        record_groups = filters_data.get("recordGroups", [])
                        if record_groups:
                            kb_record_groups.extend(record_groups)
                filters["kb"] = kb_record_groups
            logger.info(f"Filters: {filters}")
            
        # Narrow agent_knowledge to only the connectors present in filters["apps"]
        # so the query_info knowledge list stays consistent with the resolved filters.
        enabled_apps_set = set(filters.get("apps", []))
        if enabled_apps_set:
            agent_knowledge = [
                k for k in agent_knowledge
                if isinstance(k, dict) and k.get("connectorId") in enabled_apps_set
            ]

        logger.info(f"Filters: {filters}")

        # Build query info
        query_info = {
            "query": chat_query.query,
            "limit": chat_query.limit,
            "messages": [],
            "previous_conversations": chat_query.previousConversations,
            "quickMode": chat_query.quickMode,
            "chatMode": chat_query.chatMode,
            "retrievalMode": chat_query.retrievalMode,
            "filters": filters,
            "systemPrompt": agent.get("systemPrompt"),
            "instructions": agent.get("instructions"),
            "timezone": chat_query.timezone,
            "currentTime": chat_query.currentTime,
            "toolsets": agent_toolsets,
            "knowledge": agent_knowledge,
            "toolsetConfigs": toolset_configs,
        }

        return StreamingResponse(
            stream_response(
                query_info, enriched_user_info, llm, logger, retrieval_service, graph_provider, reranker_service, config_service, org_info
            ),
            media_type="text/event-stream",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in chat_stream: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
