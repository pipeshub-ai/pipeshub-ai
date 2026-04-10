"""
Agent API Routes
Handles agent instances, templates, chat, and permissions using graph-based architecture
"""

import json
import os
import uuid
from collections.abc import AsyncGenerator
from logging import Logger
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel

from app.agents.registry.toolset_registry import ToolsetRegistry
from app.api.middlewares.auth import authMiddleware, require_scopes
from app.api.routes.chatbot import get_llm_for_chat
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames, Connectors
from app.config.constants.service import OAuthScopes, config_node_constants
from app.modules.agents.capability_summary import fetch_connector_configs
from app.utils.execute_query import has_sql_connector_configured
from app.modules.agents.deep.graph import deep_agent_graph
from app.modules.agents.deep.state import build_deep_agent_state
from app.modules.agents.qna.cache_manager import get_cache_manager
from app.modules.agents.qna.chat_state import build_initial_state
from app.modules.agents.qna.graph import agent_graph, modern_agent_graph
from app.modules.agents.qna.memory_optimizer import (
    auto_optimize_state,
    check_memory_health,
)
from app.modules.reranker.reranker import RerankerService
from app.modules.retrieval.retrieval_service import RetrievalService
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

router = APIRouter()

# Opik tracer initialization
_opik_tracer = None
_opik_api_key = os.getenv("OPIK_API_KEY")
_opik_workspace = os.getenv("OPIK_WORKSPACE")
if _opik_api_key and _opik_workspace:
    try:
        from opik.integrations.langchain import OpikTracer
        _opik_tracer = OpikTracer()
    except Exception:
        pass
# Constants
SPLIT_PATH_EXPECTED_PARTS = 2  # Expected parts when splitting path with "/" separator
NO_KB_SELECTED_FILTER = "NO_KB_SELECTED"

# ============================================================================
# Request Models
# ============================================================================

class ChatQuery(BaseModel):
    query: str
    limit: int | None = 50
    previousConversations: list[dict] = []
    quickMode: bool = False
    filters: dict[str, Any] | None = None
    retrievalMode: str | None = "HYBRID"
    systemPrompt: str | None = None
    instructions: str | None = None
    tools: list[str] | None = None
    chatMode: str | None = "auto"
    modelKey: str | None = None
    modelName: str | None = None
    timezone: str | None = None
    currentTime: str | None = None
    conversationId: str | None = None


class RouteDecision(BaseModel):
    """
    Routing decision with structured chain-of-thought reasoning.

    reasoning: structured analysis — sub-tasks identified, dependency chain,
               parameter availability, and justification for the chosen tier.
               Written BEFORE committing to a route (CoT reduces misroutes).
    route: the tier — type-safe, cannot produce an invalid value.
    """
    reasoning: str
    route: Literal["quick", "react", "deep"]

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
            detail="Agent not found or you don't have access to it",
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

class ReasoningModelRequiredError(AgentError):
    """Reasoning model required"""
    def __init__(self) -> None:
        super().__init__(
            detail="Reasoning model is required in agent mode. Please use a reasoning model.",
            status_code=400
        )

# ============================================================================
# Helper Functions
# ============================================================================

async def get_services(request: Request) -> dict[str, Any]:
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


def _get_user_context(request: Request) -> dict[str, Any]:
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
        "isServiceAccount": bool(user.get("isServiceAccount", False)),
        "sendUserInfo": request.query_params.get("sendUserInfo", True),
    }



async def _select_agent_graph_for_query(
    query_info: dict[str, Any],
    logger: Logger,
    llm: BaseChatModel,
) -> CompiledStateGraph:
    """
    Graph selection based on chatMode from the chat input:
    - quick: legacy agent graph (fast, no tool loops)
    - verification: modern ReAct agent graph (tool calling with reflection)
    - deep: deep agent graph (orchestrator + sub-agents)
    - auto: LLM router decides based on query complexity (default: quick)
    """
    chat_mode = (query_info.get("chatMode") or "auto").lower().strip()

    if chat_mode == "deep":
        logger.info("Agent graph route: deep | chatMode=deep")
        return deep_agent_graph

    if chat_mode == "verification":
        logger.info("Agent graph route: react | chatMode=verification")
        return modern_agent_graph

    if chat_mode == "auto":
        # Auto-detect: use LLM to pick the right graph
        return await _auto_select_graph(query_info, logger, llm)

    # Default: "auto" → LLM router decides
    logger.info("Agent graph route: legacy | chatMode=%s", chat_mode)
    return agent_graph


async def _auto_select_graph(
    query_info: dict[str, Any],
    logger: Logger,
    llm: BaseChatModel,
) -> CompiledStateGraph:
    """
    Auto-select graph using an LLM call to classify the query into one of
    three agent types: quick, react, or deep.
    Falls back to 'react' if parsing fails.
    """

    from langchain_core.messages import HumanMessage, SystemMessage

    user_query = query_info.get("query", "").strip()
    if not user_query:
        return modern_agent_graph

    capability_block, n_knowledge, indexed_connectors, kb_sources, tools_data = (
        _build_agent_capability_context(query_info)
    )
    context_block = _build_routing_context(query_info)

    structured_llm = llm.with_structured_output(RouteDecision)

    system_prompt = (
        "You are a routing agent. Classify the user request into exactly one "
        "execution tier: quick, react, or deep.\n\n"

        + capability_block
        + context_block
        + "## quick\n"
        "Every action and every parameter can be fully determined right now "
        "from the query and context, before anything runs. The request itself "
        "is the final action — retrieving, searching, displaying, or acting on "
        "something where the goal is the retrieval or action itself, not "
        "further processing of what comes back.\n\n"

        "CRITICAL: For a request to be 'quick', ALL of the following must be true:\n"
        "1. ALL required parameters for the final action are directly available "
        "from the query text, conversation context, or system constants — NO "
        "tool calls needed to obtain any parameter (IDs, keys, identifiers).\n"
        "2. The query contains exactly ONE distinct action or question. If the "
        "query asks about two or more separate topics, tasks, or actions "
        "(e.g., 'How do I do X and also Y?'), it is NOT quick.\n\n"

        "## react\n"
        "A fixed, predictable sequence of dependent steps where the chain "
        "length is deterministic before execution starts, but at least one "
        "step's parameters only become known from a prior step's result. The "
        "intent implies: get something first, then do something with it — "
        "where 'it' is one specific thing.\n\n"
        "Key indicator: If the final action requires a parameter (ID, key, "
        "identifier, or any structured value) that must be fetched/resolved "
        "through a tool call, this is react. The dependency chain is: "
        "resolve parameter → execute final action.\n\n"
        "Also use react when the query has multiple related sub-tasks that "
        "build on shared context.\n\n"
        "**react is the safe default when routing is unclear.**\n\n"

        "## deep\n"
        "Reserved for tasks react cannot handle. Only two cases qualify:\n"
        "(a) The intent requires getting a collection and then doing something "
        "to EVERY item in it — the number of items is unknown before the "
        "collection is retrieved. Wanting to SEE a collection is not this.\n"
        "(b) The intent requires gathering information from ≥2 fully "
        "independent sources and combining it into one unified answer.\n"
        f"Configuration check: {n_knowledge} source(s) configured — deep "
        f"is {'viable' if n_knowledge >= 2 else 'NOT viable (need ≥2)'}.\n\n"

        "## What counts as a known vs unknown parameter\n"
        "Known (does NOT require a prior tool call):\n"
        "  • Any search term, keyword, or topic that appears in the query text "
        "itself — the user's words ARE the search input.\n"
        "  • Any ID, name, key, or value explicitly stated in the query or "
        "conversation history.\n"
        "  • Which tool or knowledge source to use — this is an internal agent "
        "routing decision, NOT a parameter the query must supply.\n\n"
        "Unknown (DOES require a prior tool call):\n"
        "  • An ID, key, or identifier that is not present anywhere in the "
        "query or conversation and must be obtained from a tool's response "
        "before the final action can execute.\n\n"

        "## Decision\n"
        "Answer these in order. Stop at the first match.\n\n"

        "Q1: Is this a single question or action, AND are ALL required "
        "parameters known (per the definitions above) — with NO tool calls "
        "needed to obtain them? → **quick**\n\n"

        "Q2: Does the request require a fixed sequence where at least one "
        "parameter for the final action must come from a prior tool's result? "
        "→ **react**\n\n"

        "Q3: Does the request imply acting on every item in a collection "
        "whose size is only known at runtime, or combining ≥2 fully "
        f"independent sources ({n_knowledge} configured)? → **deep**\n\n"

        "Q4: Does the query contain multiple distinct sub-questions, topics, "
        "or actions? → NOT quick; use react (if topics are related or "
        "sequential) or deep (if fully independent and targeting different "
        "sources).\n\n"

        "Default → **react**\n\n"

        "For follow-ups ('yes', 'ok', 'do it', 'give all', 'show more', "
        "'proceed') — infer the full intent from the prior conversation "
        "above, then apply the decision tree to that inferred intent.\n\n"

        f"Query: {user_query}"
    )

    route_map = {
        "quick": agent_graph,
        "react": modern_agent_graph,
        "deep": deep_agent_graph,
    }

    try:
        invoke_config = {"callbacks": [_opik_tracer]} if _opik_tracer else {}

        decision: RouteDecision = await structured_llm.ainvoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content="Classify."),
            ],
            config=invoke_config,
        )

        route = decision.route
        logger.info(
            "Agent graph route: %s | (query=%s, reasoning=%s)",
            route,
            user_query[:80],
            decision.reasoning[:120],
        )
        return route_map[route]

    except Exception as e:
        logger.warning(
            "Agent graph route: react (fallback) | router failed: %s", e
        )
        return modern_agent_graph

def _build_routing_context(query_info: dict[str, Any]) -> str:
    """
    Compact prior conversation context for resolving follow-ups.
    Last 3 turns only. First line of bot responses only.
    """
    previous = query_info.get("previous_conversations", [])
    if not previous:
        return ""

    recent = previous[-6:]
    turns = []

    for conv in recent:
        role = conv.get("role", "")
        content = str(conv.get("content", "")).strip()

        if role == "user_query":
            turns.append(f"User: {content[:200]}")
        elif role == "bot_response":
            first_line = content.split("\n")[0][:150]
            turns.append(f"Assistant: {first_line}")

    if not turns:
        return ""

    return (
        "Prior conversation:\n"
        + "\n".join(turns)
        + "\n\n"
    )


def _build_agent_capability_context(
    query_info: dict[str, Any],
) -> tuple[str, int, list[dict], list[dict], list[dict]]:
    """
    Build a rich capability summary for the routing prompt.

    Prefers fully-labeled data when available (chat_stream path supplies
    query_info["knowledge"] and query_info["toolsets"]).  Falls back
    gracefully to filter counts + bare tool-name strings when only the
    lighter query_info structure is present (non-streaming chat / askAI).

    Returns:
        (capability_block, n_knowledge, indexed_connectors, kb_sources, tools_data)
        where tools_data is a list of {"full_name": str, "desc": str} dicts.
    """
    from app.modules.agents.capability_summary import (  # noqa: PLC0415 – lazy import kept for historical reasons
        classify_knowledge_sources,
        format_connector_filter_lines,
    )

    lines: list[str] = ["## Agent capabilities\n"]
    indexed_connectors: list[dict] = []
    kb_sources: list[dict] = []

    # ── Knowledge sources ─────────────────────────────────────────────────────
    agent_knowledge: list[dict] = query_info.get("knowledge") or []
    connector_cfgs = query_info.get("connector_configs") or {}

    if agent_knowledge:
        kb_sources, indexed_connectors = classify_knowledge_sources(
            agent_knowledge,
            connector_configs=connector_cfgs if isinstance(connector_cfgs, dict) else None,
        )
        n_knowledge = len(kb_sources) + len(indexed_connectors)
        lines.append(f"Knowledge sources ({n_knowledge} total):")
        for c in indexed_connectors:
            line = f"  • {c['label']} — app connector (type: {c['type_key']})"
            fls = format_connector_filter_lines(c.get("filters"))
            if fls:
                line += "; " + "; ".join(fls)
            lines.append(line)
        for k in kb_sources:
            cids = k.get("collection_ids", [])
            scope = f", {len(cids)} scoped collection(s)" if cids else ""
            lines.append(f"  • {k['label']} — knowledge base{scope}")
    else:
        # Fallback: derive counts from filters (NO_KB_SELECTED sentinel excluded)
        filters = query_info.get("filters") or {}
        n_connectors = len(filters.get("apps") or [])
        n_kb = len([
            x for x in (filters.get("kb") or [])
            if x and x != "NO_KB_SELECTED"
        ])
        n_knowledge = n_connectors + n_kb
        if n_knowledge:
            lines.append(
                f"Knowledge sources ({n_knowledge} total): "
                f"{n_connectors} connector(s), {n_kb} KB collection(s)"
            )
        else:
            lines.append("Knowledge sources: none configured")

    lines.append("")

    # ── Action tools ──────────────────────────────────────────────────────────
    # Prefer toolsets (rich: fullName + description per tool).
    # Fall back to the flat "tools" string list when toolsets are absent.
    tools_data: list[dict] = []  # {"full_name": str, "desc": str}

    toolsets: list[dict] = query_info.get("toolsets") or []
    if toolsets:
        for ts in toolsets:
            for tool in ts.get("tools", []):
                full_name = tool.get("fullName") or tool.get("name", "")
                if not full_name:
                    continue
                desc = (tool.get("description") or "").strip()
                tools_data.append({"full_name": full_name, "desc": desc})
    else:
        raw_tools: list = query_info.get("tools") or []
        for t in raw_tools:
            if isinstance(t, str) and t:
                tools_data.append({"full_name": t, "desc": ""})

    if tools_data:
        lines.append(f"Action tools ({len(tools_data)} total):")
        for td in tools_data:
            entry = f"  • {td['full_name']}"
            if td["desc"]:
                entry += f" — {td['desc'][:100]}"
            lines.append(entry)
    else:
        lines.append("Action tools: none configured")

    return "\n".join(lines) + "\n\n", n_knowledge, indexed_connectors, kb_sources, tools_data

async def _get_user_document(user_id: str, graph_provider: IGraphDBProvider, logger: Logger) -> dict[str, Any]:
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
        raise HTTPException(status_code=500, detail="Failed to retrieve user information") from e


async def _get_org_info(user_info: dict[str, Any], graph_provider: IGraphDBProvider, logger: Logger) -> dict[str, Any]:
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
        raise HTTPException(status_code=500, detail="Failed to retrieve organization information") from e


async def _enrich_user_info(user_info: dict[str, Any], user_doc: dict[str, Any]) -> dict[str, Any]:
    """Enrich user info with document data"""
    enriched = user_info.copy()
    enriched["userEmail"] = user_doc.get("email", "").strip()
    enriched["_key"] = user_doc.get("_key")

    # Add name fields if available
    for field in ["fullName", "firstName", "lastName", "displayName"]:
        if user_doc.get(field):
            enriched[field] = user_doc[field]

    return enriched


async def _enrich_user_info_for_service_account_agent_chat(
    agent: dict[str, Any],
    graph_provider: IGraphDBProvider,
    logger: Logger,
) -> dict[str, Any]:
    """
    Service-account agents are invoked with a synthetic JWT (e.g. Slack bot). Retrieval and
    permission checks must use the agent creator's real userId — the same identity whose
    knowledge access configured the agent — not the service principal.
    """
    creator_key = agent.get("createdBy")
    if not creator_key:
        raise HTTPException(
            status_code=500,
            detail="Service account agent is missing createdBy; cannot resolve knowledge permissions.",
        )
    creator_doc = await graph_provider.get_document(
        str(creator_key), CollectionNames.USERS.value
    )
    if not creator_doc:
        raise HTTPException(
            status_code=500,
            detail="Agent creator user not found; cannot resolve knowledge permissions.",
        )
    creator_user_id = creator_doc.get("userId") or creator_doc.get("user_id")
    if not creator_user_id or not str(creator_user_id).strip():
        logger.error(
            "Service account agent creator %s has no userId field",
            creator_key,
        )
        raise HTTPException(
            status_code=500,
            detail="Agent creator is missing userId; cannot resolve knowledge permissions.",
        )
    synthetic = {
        "userId": str(creator_user_id).strip(),
        "orgId": str(creator_doc.get("orgId") or "").strip(),
        "email": (creator_doc.get("email") or "").strip(),
    }
    return await _enrich_user_info(synthetic, creator_doc)


def _validate_required_fields(data: dict[str, Any], required_fields: list[str]) -> None:
    """Validate required fields in request data"""
    for field in required_fields:
        if not data.get(field) or not str(data.get(field)).strip():
            raise InvalidRequestError(f"'{field}' is required")


def _parse_models(raw_models: list[Any], logger: Logger) -> tuple[list[str], bool]:
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


def _parse_toolsets(raw_toolsets: list[Any]) -> dict[str, dict[str, Any]]:
    """Parse toolsets with their tools.

    The key of the returned dict is the toolset name (lowercase).
    Each value carries the parsed fields including optional instanceId.
    """
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
        # New field: admin-created instance UUID
        instance_id = toolset_data.get("instanceId", None)
        instance_name = toolset_data.get("instanceName", None)

        if toolset_name not in toolsets_with_tools:
            toolsets_with_tools[toolset_name] = {
                "displayName": display_name,
                "type": toolset_type,
                "tools": [],
                "instanceId": instance_id,
                "instanceName": instance_name,
            }
        elif instance_id and not toolsets_with_tools[toolset_name].get("instanceId"):
            # Update instanceId if not yet set
            toolsets_with_tools[toolset_name]["instanceId"] = instance_id
            toolsets_with_tools[toolset_name]["instanceName"] = instance_name

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


def _parse_knowledge_sources(raw_knowledge: list[Any]) -> dict[str, dict[str, Any]]:
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


def _filter_knowledge_by_enabled_sources(
    agent_knowledge: list[dict[str, Any]],
    filters: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Filter agent_knowledge to only include entries matching enabled filters.

    Keeps:
    - App connectors whose connectorId is in filters["apps"]
    - KB connectors whose recordGroups overlap with filters["kb"],
      or KB connectors with no recordGroups (unrestricted KB)
    """
    enabled_apps = set(filters.get("apps", []))
    enabled_kbs = set(filters.get("kb", []))

    if not enabled_apps and not enabled_kbs:
        return agent_knowledge

    filtered: list[dict[str, Any]] = []
    for k in agent_knowledge:
        if not isinstance(k, dict):
            continue

        connector_id = k.get("connectorId", "")

        # App connector — keep if in enabled apps
        if connector_id in enabled_apps:
            filtered.append(k)
            continue

        # KB connector — keep if its record groups overlap or it has none
        if connector_id.startswith("knowledgeBase_") and enabled_kbs:
            filters_data = k.get("filters", k.get("filtersParsed", {}))
            if isinstance(filters_data, str):
                try:
                    filters_data = json.loads(filters_data)
                except (json.JSONDecodeError, ValueError):
                    filters_data = {}

            record_groups = (
                filters_data.get("recordGroups", [])
                if isinstance(filters_data, dict) else []
            )

            if any(rg in enabled_kbs for rg in record_groups):
                filtered.append(k)

    return filtered


async def _create_toolset_edges(
    agent_key: str,
    toolsets_with_tools: dict[str, dict[str, Any]],
    user_info: dict[str, Any],
    user_key: str,
    graph_provider: IGraphDBProvider,
    logger: Logger
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
        instance_id = toolset_data.get("instanceId")
        instance_name = toolset_data.get("instanceName")

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

        # Store instanceId in ArangoDB when provided (admin-created instances)
        if instance_id:
            toolset_node["instanceId"] = instance_id
        if instance_name:
            toolset_node["instanceName"] = instance_name

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
    agent_toolset_edges = [
        {
            "_from": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
            "_to": f"{CollectionNames.AGENT_TOOLSETS.value}/{toolset_info['key']}",
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
        }
        for toolset_info in toolset_mapping.values()
    ]

    # Batch create agent -> toolset edges
    try:
        await graph_provider.batch_create_edges(agent_toolset_edges, CollectionNames.AGENT_HAS_TOOLSET.value)
    except Exception as e:
        logger.error(f"Failed to create agent-toolset edges: {e}")

    # Prepare all tool nodes and edges
    tool_nodes = []
    toolset_tool_edges = []
    tool_mapping = {}  # Map full_name to tool_key

    for toolset_info in toolset_mapping.values():
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
    for toolset_info in toolset_mapping.values():
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
    knowledge_sources: dict[str, dict[str, Any]],
    user_key: str,
    graph_provider: IGraphDBProvider,
    logger: Logger
) -> list[dict[str, Any]]:
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
    agent_knowledge_edges = [
        {
            "_from": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
            "_to": f"{CollectionNames.AGENT_KNOWLEDGE.value}/{knowledge_info['key']}",
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
        }
        for knowledge_info in knowledge_mapping.values()
    ]

    # Batch create agent -> knowledge edges
    try:
        await graph_provider.batch_create_edges(agent_knowledge_edges, CollectionNames.AGENT_HAS_KNOWLEDGE.value)
    except Exception as e:
        logger.error(f"Failed to create agent-knowledge edges: {e}")

    # Build response
    created_knowledge.extend(
        {
            "connectorId": connector_id,
            "key": knowledge_info["key"],
            "filters": knowledge_info["filters"],
        }
        for knowledge_info in knowledge_mapping.values()
    )

    return created_knowledge


async def _enrich_agent_models(agent: dict[str, Any], config_service: ConfigurationService, logger: Logger) -> None:
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


def _parse_request_body(body: bytes) -> dict[str, Any]:
    """Parse and validate JSON request body"""
    if not body:
        raise InvalidRequestError("Request body is required")

    try:
        return json.loads(body.decode('utf-8'))
    except json.JSONDecodeError as e:
        raise InvalidRequestError(f"Invalid JSON: {str(e)}") from e


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
        selected_graph = await _select_agent_graph_for_query(query_info.model_dump(), logger, services["llm"])

        has_sql_connector = await has_sql_connector_configured(
            graph_provider, enriched_user_info["userId"], enriched_user_info["orgId"]
        )
        if selected_graph == deep_agent_graph:
            initial_state = build_deep_agent_state(
                query_info.model_dump(),
                enriched_user_info,
                services["llm"],
                logger,
                retrieval_service,
                graph_provider,
                reranker_service,
                config_service,
                org_info,
                query_info.modelName,
                query_info.modelKey,
                has_sql_connector=has_sql_connector,
            )
        else:
            graph_type = "react" if selected_graph == modern_agent_graph else "legacy"
            initial_state = build_initial_state(
                query_info.model_dump(),
                enriched_user_info,
                services["llm"],
                logger,
                retrieval_service,
                graph_provider,
                reranker_service,
                config_service,
                query_info.modelName,
                query_info.modelKey,
                org_info,
                graph_type,
                has_sql_connector=has_sql_connector,
            )

        graph_to_use = selected_graph
        config = {"recursion_limit": 30}
        final_state = await graph_to_use.ainvoke(initial_state, config=config)
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
        raise HTTPException(status_code=400, detail=str(e)) from e


async def stream_response(
    query_info: dict[str, Any],
    user_info: dict[str, Any],
    llm: BaseChatModel,
    logger: Logger,
    retrieval_service: RetrievalService,
    graph_provider: IGraphDBProvider,
    reranker_service: RerankerService,
    config_service: ConfigurationService,
    org_info: dict[str, Any] = None,
    modelName: str = None,
    modelKey: str = None,
) -> AsyncGenerator[str, None]:
    """Stream agent response"""
    try:
        selected_graph = await _select_agent_graph_for_query(query_info, logger, llm)

        has_sql_connector = await has_sql_connector_configured(
            graph_provider, user_info["userId"], user_info["orgId"]
        )
        if selected_graph == deep_agent_graph:
            graph_type = "deep"
            initial_state = build_deep_agent_state(
                query_info,
                user_info,
                llm,
                logger,
                retrieval_service,
                graph_provider,
                reranker_service,
                config_service,
                org_info,
                modelName,
                modelKey,
                has_sql_connector=has_sql_connector,
            )
        else:
            graph_type = "react" if selected_graph == modern_agent_graph else "legacy"
            initial_state = build_initial_state(
                query_info,
                user_info,
                llm,
                logger,
                retrieval_service,
                graph_provider,
                reranker_service,
                config_service,
                modelName,
                modelKey,
                org_info,
                graph_type,
                has_sql_connector=has_sql_connector,
            )

        config = {"recursion_limit": 50}
        chunk_count = 0

        graph_to_use = selected_graph
        async for chunk in graph_to_use.astream(initial_state, config=config, stream_mode="custom"):
            chunk_count += 1
            if isinstance(chunk, dict) and "event" in chunk:
                event_type = chunk.get('event', 'unknown')
                data = chunk.get('data', {})
                yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
            else:
                logger.warning(f"Unexpected chunk format: {type(chunk)}")

        logger.info(f"Streaming completed. Total chunks: {chunk_count}")
    except Exception as e:
        logger.error(f"Error in stream_response: {e}", exc_info=True)
        yield f"event: error\ndata: {json.dumps({'message': str(e), 'type': 'stream_error'})}\n\n"


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
                query_info.model_dump(),
                enriched_user_info,
                llm,
                logger,
                retrieval_service,
                graph_provider,
                reranker_service,
                config_service,
                org_info,
                query_info.modelName,
                query_info.modelKey,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        services["logger"].error(f"Error in askAIStream: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e)) from e


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
        raise HTTPException(status_code=500, detail="Internal server error") from e


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
        raise HTTPException(status_code=400, detail=str(e)) from e


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
        raise HTTPException(status_code=400, detail=str(e)) from e


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
        raise HTTPException(status_code=400, detail=str(e)) from e


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
        raise HTTPException(status_code=400, detail=str(e)) from e


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
        raise HTTPException(status_code=400, detail=str(e)) from e


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
        raise HTTPException(status_code=400, detail=str(e)) from e


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
        toolsets_with_tools = _parse_toolsets(body.get("toolsets", []))
        knowledge_sources = _parse_knowledge_sources(body.get("knowledge", []))

        # Validate shareWithOrg + toolsets combination BEFORE starting transaction
        is_service_account = bool(body.get("isServiceAccount", False))
        # Service account agents must always be org-wide so internal calls can access them
        # without requiring individual user permission edges.
        share_with_org = True if is_service_account else bool(body.get("shareWithOrg", False))

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
            "isServiceAccount": is_service_account,
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
                    instance_id = toolset_data.get("instanceId")
                    instance_name = toolset_data.get("instanceName")

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

                    # Store instanceId in ArangoDB node when provided (admin-created instances)
                    if instance_id:
                        toolset_node["instanceId"] = instance_id
                    if instance_name:
                        toolset_node["instanceName"] = instance_name

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
                agent_toolset_edges = [
                    {
                        "_from": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
                        "_to": f"{CollectionNames.AGENT_TOOLSETS.value}/{toolset_info['key']}",
                        "createdAtTimestamp": time,
                        "updatedAtTimestamp": time,
                    }
                    for toolset_info in toolset_mapping.values()
                ]
                if agent_toolset_edges:
                    await graph_provider.batch_create_edges(agent_toolset_edges, CollectionNames.AGENT_HAS_TOOLSET.value, transaction=transaction_id)

                # Create tool nodes and edges
                tool_mapping = {}
                tool_nodes = []
                toolset_tool_edges = []

                for toolset_info in toolset_mapping.values():
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
                for toolset_info in toolset_mapping.values():
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
                agent_knowledge_edges = [
                    {
                        "_from": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
                        "_to": f"{CollectionNames.AGENT_KNOWLEDGE.value}/{knowledge_info['key']}",
                        "createdAtTimestamp": time,
                        "updatedAtTimestamp": time,
                    }
                    for knowledge_info in knowledge_mapping.values()
                ]
                if agent_knowledge_edges:
                    await graph_provider.batch_create_edges(agent_knowledge_edges, CollectionNames.AGENT_HAS_KNOWLEDGE.value, transaction=transaction_id)

                # Build response for created knowledge
                created_knowledge.extend(
                    {
                        "connectorId": connector_id,
                        "key": knowledge_info["key"],
                        "filters": knowledge_info["filters"],
                    }
                    for knowledge_info in knowledge_mapping.values()
                )

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
            ) from e

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
        raise HTTPException(status_code=400, detail=str(e)) from e

@router.get("/{agent_id}/internal/service-account", dependencies=[Depends(authMiddleware)])
async def get_agent_internal(request: Request, agent_id: str) -> JSONResponse:
    """
    Internal route: verify that an agent is a service account and return its
    data.  Called by the Node.js gateway after hydrating a Slack scoped token
    into a regular user JWT (the hydrated user is the org admin, who always has
    access to any org-shared agent).

    Returns 403 if the agent exists but is NOT a service account, 404 if not
    found.  Service account agents are always org-wide by invariant, so the
    standard get_agent() permission check will pass for the hydrated admin user.
    """
    try:
        services = await get_services(request)

        agent = await services["graph_provider"].get_agent(agent_id)
        if not agent:
            raise AgentNotFoundError(agent_id)

        # Guard: this internal route is exclusively for service account agents.
        if not agent.get("isServiceAccount"):
            raise HTTPException(
                status_code=403,
                detail="This endpoint is only accessible for service account agents.",
            )

        await _enrich_agent_models(agent, services["config_service"], services["logger"])
        agent.pop("modelsEnriched", None)
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent retrieved successfully",
                "isServiceAccount": True,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/{agent_id}", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_READ))])
async def get_agent(request: Request, agent_id: str) -> JSONResponse:
    """Get an agent by ID with enriched data"""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)
        org_key = user_context["orgId"]

        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])

        perm = await services["graph_provider"].check_agent_permission(agent_id, user_doc["_key"], org_key)
        if not perm:
            raise AgentNotFoundError(agent_id)

        agent = await services["graph_provider"].get_agent(agent_id, org_key)
        if not agent:
            raise AgentNotFoundError(agent_id)

        agent.update(perm)

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
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_READ))])
async def get_agents(
    request: Request,
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    limit: int = Query(20, ge=1, le=200, description="Items per page"),
    search: str | None = Query(None, description="Search by name/description/tags"),
    sort_by: str = Query("updatedAtTimestamp", description="Field to sort by"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$", description="Sort order"),
    is_deleted: bool = Query(False, alias="isDeleted", description="When true, return only soft-deleted agents",),
) -> JSONResponse:
    """Get all agents with pagination and search"""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)
        org_key = user_context["orgId"]

        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])
        user_key = user_doc["_key"]

        # Delegate pagination/search/sort to graph provider
        result = await services["graph_provider"].get_all_agents(
            user_key,
            org_key,
            page=page,
            limit=limit,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
            is_deleted=is_deleted,
        )

        # Providers return either a simple list (backward-compat) or a dict with agents and totalItems
        if isinstance(result, list):
            agents = result
            total_items = len(agents)
        else:
            agents = result.get("agents", [])
            total_items = int(result.get("totalItems", len(agents)))

        # Build pagination envelope
        current_page = page
        per_page = limit
        total_pages = (total_items + per_page - 1) // per_page if per_page > 0 else 0
        has_next = current_page < total_pages
        has_prev = current_page > 1

        # Avoid 404s; return empty list with valid pagination

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "agents": agents or [],
                "pagination": {
                    "currentPage": current_page,
                    "limit": per_page,
                    "totalItems": total_items,
                    "totalPages": total_pages,
                    "hasNext": has_next,
                    "hasPrev": has_prev,
                },
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        services["logger"].error(f"Error getting agents: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e)) from e


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

        # Check permissions first, then fetch full agent data
        perm = await services["graph_provider"].check_agent_permission(agent_id, user_key, org_key)
        if not perm:
            raise AgentNotFoundError(agent_id)

        if not perm.get("can_edit", False):
            raise PermissionDeniedError("edit this agent (only owner can edit)")

        agent = await services["graph_provider"].get_agent(agent_id, org_key)
        if not agent:
            raise AgentNotFoundError(agent_id)

        agent.update(perm)

        # Guard: once an agent is marked as a service account it cannot be downgraded.
        # Allowing the reverse would leave orphaned agent-scoped toolset credentials
        # (stored under /services/toolsets/{instanceId}/{agentKey}) with no clear owner
        # and would confuse the toolset-fetching logic on the frontend.
        if "isServiceAccount" in body:
            current_is_sa = bool(agent.get("isServiceAccount", False))
            requested_is_sa = bool(body.get("isServiceAccount", False))
            if current_is_sa and not requested_is_sa:
                raise InvalidRequestError(
                    "A service account agent cannot be converted back to a regular agent."
                )
            # When converting to a service account, ensure org-wide sharing is enabled.
            # Service account agents must always have an ORG permission edge so that
            # internal calls (e.g. from Slack) can access them via the org admin user.
            if requested_is_sa and not current_is_sa:
                body["shareWithOrg"] = True

        # Handle shareWithOrg flag changes
        if "shareWithOrg" in body:
            new_share_with_org = bool(body.get("shareWithOrg", False))
            current_share_with_org = bool(agent.get("shareWithOrg", False))

            if new_share_with_org and not current_share_with_org:
                # Turning ON org sharing: validate no toolsets exist or being added

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
                # Service account agents must always be org-shared — reject the request.
                if bool(agent.get("isServiceAccount", False)):
                    raise InvalidRequestError(
                        "Cannot disable org-wide sharing for a service account agent. "
                        "Service account agents must always be shared across the organisation."
                    )
                # Turning OFF org sharing: delete the org permission edge
                await services["graph_provider"].delete_edge(
                    from_id=org_key,
                    from_collection=CollectionNames.ORGS.value,
                    to_id=agent_id,
                    to_collection=CollectionNames.AGENT_INSTANCES.value,
                    collection=CollectionNames.PERMISSION.value
                )
                logger.info(f"Deleted org permission edge for agent {agent_id}")


        # Update agent document
        # Persist update (use original body to avoid changing storage format)
        result = await services["graph_provider"].update_agent(agent_id, body, user_key, org_key)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to update agent")

        # Update toolsets if provided in request (even if empty array - means delete all)
        if "toolsets" in body:
            # Parse toolsets first to validate before deletion
            toolsets_with_tools = _parse_toolsets(body.get("toolsets", []))

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
                ) from e

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
                    ) from e
            else:
                logger.info(f"All toolsets removed for agent {agent_id}")

        # Update knowledge if provided in request (even if empty array - means delete all)
        if "knowledge" in body:
            # Parse knowledge sources first to validate before deletion
            knowledge_sources = _parse_knowledge_sources(body.get("knowledge", []))

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
                ) from e

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
                    ) from e
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
        raise HTTPException(status_code=400, detail=str(e)) from e

@router.delete("/{agent_id}", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def delete_agent(request: Request, agent_id: str) -> JSONResponse:
    """Soft-delete an agent (tombstone) using a transaction to ensure atomicity."""
    txn_id = None
    services = None
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)
        org_key = user_context["orgId"]

        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])

        perm = await services["graph_provider"].check_agent_permission(agent_id, user_doc["_key"], org_key)
        if not perm:
            raise AgentNotFoundError(agent_id)

        if not perm.get("can_delete", False):
            raise PermissionDeniedError("delete this agent (only owner can delete)")

        agent = await services["graph_provider"].get_agent(agent_id, org_key)
        if not agent:
            raise AgentNotFoundError(agent_id)

        agent.update(perm)

        # Begin transaction for atomic deletion
        txn_id = await services["graph_provider"].begin_transaction(
            read=[
                CollectionNames.AGENT_INSTANCES.value,
                CollectionNames.AGENT_TOOLSETS.value,
                CollectionNames.AGENT_TOOLS.value,
                CollectionNames.AGENT_KNOWLEDGE.value,
            ],
            write=[
                CollectionNames.AGENT_INSTANCES.value,
                CollectionNames.AGENT_TOOLSETS.value,
                CollectionNames.AGENT_TOOLS.value,
                CollectionNames.AGENT_KNOWLEDGE.value,
                CollectionNames.AGENT_HAS_TOOLSET.value,
                CollectionNames.AGENT_HAS_KNOWLEDGE.value,
                CollectionNames.TOOLSET_HAS_TOOL.value,
                CollectionNames.PERMISSION.value,
            ],
        )
        services["logger"].debug(f"🔄 Started transaction {txn_id} for agent deletion")

        # Soft-delete: marks the agent instance deleted; related toolsets/tools/knowledge remain.
        result = await services["graph_provider"].delete_agent(
            agent_id, user_doc["_key"], org_key, transaction=txn_id
        )
        if not result:
            if txn_id is not None:
                await services["graph_provider"].rollback_transaction(txn_id)
            raise HTTPException(status_code=500, detail="Failed to delete agent")

        # Commit transaction on success
        await services["graph_provider"].commit_transaction(txn_id)
        services["logger"].info(f"✅ Successfully soft-deleted agent {agent_id} in transaction {txn_id}")

        # For service account agents, stop in-process toolset token refresh tasks only.
        # Credential paths under /services/toolsets/{instanceId}/{agentKey} stay in ETCD.
        if agent.get("isServiceAccount"):
            try:
                refresh_service = None
                try:
                    from app.connectors.core.base.token_service.startup_service import (
                        startup_service,
                    )
                    refresh_service = startup_service.get_toolset_token_refresh_service()
                except Exception:
                    pass
                if refresh_service:
                    config_service = services["config_service"]
                    all_keys = await config_service.list_keys_in_directory("/services/toolsets/")
                    for key in all_keys:
                        # Path format: /services/toolsets/{instanceId}/{ownerId}
                        parts = key.strip("/").split("/")
                        if len(parts) >= 4 and parts[3] == agent_id:
                            refresh_service.cancel_refresh_task(key)
                            services["logger"].info(
                                f"Cancelled toolset token refresh for service account agent path: {key}"
                            )
            except Exception as e:
                services["logger"].warning(
                    f"Failed to cancel toolset refresh tasks for deleted service account agent {agent_id}: {e}"
                )

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent deleted successfully",
                "deleted": {
                    "agents": 1,
                    "toolsets": 0,
                    "tools": 0,
                    "knowledge": 0,
                    "edges": 0,
                },
            },
        )
    except HTTPException:
        if txn_id is not None and services is not None:
            try:
                await services["graph_provider"].rollback_transaction(txn_id)
                services["logger"].debug(f"🔄 Rolled back transaction {txn_id} due to HTTPException")
            except Exception as rb_err:
                if services is not None:
                    services["logger"].warning(f"⚠️ Failed to rollback transaction {txn_id}: {rb_err}")
        raise
    except Exception as e:
        if txn_id is not None and services is not None:
            try:
                await services["graph_provider"].rollback_transaction(txn_id)
                services["logger"].debug(f"🔄 Rolled back transaction {txn_id} due to error")
            except Exception as rb_err:
                services["logger"].warning(f"⚠️ Failed to rollback transaction {txn_id}: {rb_err}")
        if services is not None:
            services["logger"].error(f"Error deleting agent: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e)) from e


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

        perm = await services["graph_provider"].check_agent_permission(agent_id, user_doc["_key"], org_key)
        if not perm:
            raise AgentNotFoundError(agent_id)

        if not perm.get("can_share", False):
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
        raise HTTPException(status_code=400, detail=str(e)) from e


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

        perm = await services["graph_provider"].check_agent_permission(agent_id, user_doc["_key"], org_key)
        if not perm:
            raise AgentNotFoundError(agent_id)

        if not perm.get("can_share", False):
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
        raise HTTPException(status_code=400, detail=str(e)) from e


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
        raise HTTPException(status_code=400, detail=str(e)) from e


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
        raise HTTPException(status_code=400, detail=str(e)) from e


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
        is_service_account = user_context.get("isServiceAccount", False)

        org_info = await _get_org_info(user_context, services["graph_provider"], logger)

        if is_service_account:
            agent = await services["graph_provider"].get_agent(agent_id, org_key)
            if not agent or not agent.get("isServiceAccount"):
                raise AgentNotFoundError(agent_id)
            enriched_user_info = await _enrich_user_info_for_service_account_agent_chat(
                agent, services["graph_provider"], logger
            )
            perm = {"can_edit": False, "can_share": False, "role": "viewer"}
        else:
            # Standard user path: look up the user document and verify permissions.
            user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], logger)
            enriched_user_info = await _enrich_user_info(user_context, user_doc)
            perm = await services["graph_provider"].check_agent_permission(agent_id, user_doc["_key"], org_key)
            if not perm:
                raise AgentNotFoundError(agent_id)
            agent = await services["graph_provider"].get_agent(agent_id, org_key)
            if not agent:
                raise AgentNotFoundError(agent_id)

        agent.update(perm)

        agent_knowledge = agent.get("knowledge", [])

        # Build filters from knowledge array (new format)
        filters = chat_query.filters.copy() if chat_query.filters else {}

        if not chat_query.filters:
            # Extract knowledge sources from agent's knowledge array
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

        _chat_conn_ids = [
            k["connectorId"] for k in agent_knowledge
            if isinstance(k, dict) and k.get("connectorId")
            and not str(k["connectorId"]).startswith("knowledgeBase_")
        ]
        connector_configs = await fetch_connector_configs(config_service, _chat_conn_ids)

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
            "knowledge": agent_knowledge,
            "connector_configs": connector_configs,
            "systemPrompt": agent.get("systemPrompt"),
            "instructions": agent.get("instructions"),
            "timezone": chat_query.timezone,
            "currentTime": chat_query.currentTime,
            "conversationId": chat_query.conversationId,
            "is_service_account": is_service_account,
        }
        selected_graph = await _select_agent_graph_for_query(query_info, logger, llm)

        has_sql_connector = await has_sql_connector_configured(
            graph_provider, enriched_user_info["userId"], enriched_user_info["orgId"]
        )
        if selected_graph == deep_agent_graph:
            initial_state = build_deep_agent_state(
                query_info,
                enriched_user_info,
                llm,
                logger,
                retrieval_service,
                graph_provider,
                reranker_service,
                config_service,
                org_info,
                chat_query.modelName,
                chat_query.modelKey,
                has_sql_connector=has_sql_connector,
            )
        else:
            graph_type = "react" if selected_graph == modern_agent_graph else "legacy"
            initial_state = build_initial_state(
                query_info,
                enriched_user_info,
                llm,
                logger,
                retrieval_service,
                graph_provider,
                reranker_service,
                config_service,
                chat_query.modelName,
                chat_query.modelKey,
                org_info,
                graph_type,
                has_sql_connector=has_sql_connector,
            )

        graph_to_use = selected_graph
        config = {"recursion_limit": 50}
        final_state = await graph_to_use.ainvoke(initial_state, config=config)

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
        raise HTTPException(status_code=400, detail=str(e)) from e


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
        is_service_account = user_context.get("isServiceAccount", False)

        body = _parse_request_body(await request.body())
        chat_query = ChatQuery(**body)

        org_info = await _get_org_info(user_context, services["graph_provider"], logger)

        if agent_id == "agentIdPlaceholder":
            toolset_registry = getattr(request.app.state, "toolset_registry", None)
            agent = await get_assistant_agent(user_context["userId"], org_key, config_service, graph_provider, toolset_registry, logger)
            user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], logger)
            enriched_user_info = await _enrich_user_info(user_context, user_doc)
            perm = {"can_edit": False, "can_share": False, "role": "viewer"}

        else:
            if is_service_account:
                agent = await services["graph_provider"].get_agent(agent_id, org_key)
                if not agent or not agent.get("isServiceAccount"):
                    raise AgentNotFoundError(agent_id)
                enriched_user_info = await _enrich_user_info_for_service_account_agent_chat(
                    agent, services["graph_provider"], logger
                )
                perm = {"can_edit": False, "can_share": False, "role": "viewer"}
            else:
                # Standard user path: look up the user document and verify permissions.
                user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], logger)
                enriched_user_info = await _enrich_user_info(user_context, user_doc)
                perm = await services["graph_provider"].check_agent_permission(agent_id, user_doc["_key"], org_key)
                if not perm:
                    raise AgentNotFoundError(agent_id)
                agent = await services["graph_provider"].get_agent(agent_id, org_key)
                if not agent:
                    raise AgentNotFoundError(agent_id)

        agent.update(perm)

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
        llm_result = (await get_llm_for_chat(
            services["config_service"],
            model_key,
            model_name,
            chat_query.chatMode
        ))

        if not llm_result:
            raise LLMInitializationError()

        llm = llm_result[0]
        llm_config = llm_result[1]

        if not llm_config.get("isReasoning", False):
            raise ReasoningModelRequiredError()

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

        # ============================================================================
        # LOAD TOOLSET CONFIGS (SECURITY-CRITICAL)
        # ============================================================================
        # For normal agents: load toolset configs using the EXECUTING user's ID.
        # This ensures that when a shared agent is executed, the credentials of the
        # user making the request are used — not the agent creator's credentials.
        #
        # For service account agents: load toolset configs using the AGENT KEY.
        # The agent has its own credentials stored at /services/toolsets/{instanceId}/{agentKey}
        # These credentials are shared across all users who use this agent.
        #
        # SECURITY MODEL:
        # 1. Toolset nodes in graph DB contain ONLY: instanceId, name, displayName, tools
        # 2. NO userId is stored in toolset nodes (prevents credential leakage)
        # 3. User credentials: /services/toolsets/{instanceId}/{userId}
        # 4. Agent credentials: /services/toolsets/{instanceId}/{agentKey}
        # 5. The lookup key comes from authenticated request context (user) or agent key
        # ============================================================================

        is_service_account = bool(agent.get("isServiceAccount", False))
        executing_user_id = user_context["userId"]
        # For service account agents, credentials are keyed by agentKey not userId
        credential_lookup_id = agent_id if is_service_account else executing_user_id
        toolset_configs: dict = {}  # SENSITIVE: Contains user/agent credentials

        # Filter to toolsets that actually have a name or instanceId before the concurrent fetch
        named_toolsets = [t for t in agent_toolsets if t.get("instanceId") or t.get("name")]

        if named_toolsets:
            import asyncio as _asyncio

            async def _fetch_toolset_config(toolset: dict) -> tuple[dict, Any]:
                """Return (toolset, config_or_None) without raising.

                Uses instanceId (admin-created instance) if available, otherwise falls
                back to the legacy toolset name for backward compatibility.
                For service account agents, uses agentKey as the credential owner.
                """
                instance_id = toolset.get("instanceId")
                toolset_name = toolset.get("name", "")
                lookup_key = instance_id
                try:
                    etcd_path = get_toolset_config_path(lookup_key, credential_lookup_id)
                    config = await services["config_service"].get_config(etcd_path)
                    return toolset, config
                except Exception as exc:
                    logger.warning(f"Failed to load config for toolset '{toolset_name}' (lookup_key='{lookup_key}'): {exc}")
                    return toolset, None

            # Fetch ALL toolset configs in parallel
            fetch_results = await _asyncio.gather(*[_fetch_toolset_config(t) for t in named_toolsets])

            configured_toolsets = []
            missing_toolset_display_names: list[str] = []        # no config found at all
            unauthenticated_toolset_display_names: list[str] = []  # config exists but OAuth not completed

            for toolset, config in fetch_results:
                instance_id = toolset.get("instanceId")
                toolset_name = toolset.get("name", "")
                lookup_key = instance_id
                display_name = toolset.get("instanceName") or toolset.get("displayName") or toolset_name.replace("_", " ").title()

                if config and config.get("isAuthenticated", False):
                    # Fully configured and authenticated — allow
                    # Use instanceId as the toolset_configs key so downstream code
                    # (_build_tool_to_toolset_map) can look it up correctly.
                    toolset_configs[lookup_key] = config
                    configured_toolsets.append(toolset)
                elif config:
                    # Config saved but authentication not completed (e.g. OAuth flow pending)
                    unauthenticated_toolset_display_names.append(display_name)
                    cred_owner = f"agent '{agent_id}'" if is_service_account else f"user '{executing_user_id}'"
                    logger.warning(
                        f"Toolset '{toolset_name}' (instance='{instance_id}') is configured but not "
                        f"authenticated for {cred_owner}. Auth flow needs to be completed."
                    )
                else:
                    # No config found at all
                    missing_toolset_display_names.append(display_name)
                    cred_owner = f"agent '{agent_id}'" if is_service_account else f"user '{executing_user_id}'"
                    logger.warning(
                        f"Toolset config not found for {cred_owner} / "
                        f"toolset '{toolset_name}' (instance='{instance_id}'). "
                        "Credentials need to be configured."
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

                if is_service_account:
                    error_message = (
                        f"This service account agent requires the following toolset(s) to be configured — "
                        f"{'; '.join(problem_parts)}. "
                        "Please configure the agent's toolset credentials in the Agent Builder → Manage Credentials."
                    )
                else:
                    error_message = (
                        f"This agent requires the following toolset(s) to be set up — "
                        f"{'; '.join(problem_parts)}. "
                        "Please connect your account(s) in Settings → Toolsets before using this agent."
                    )
                logger.info(
                    f"Blocking agent {agent_id} execution "
                    f"({'service account' if is_service_account else f'user {executing_user_id!r}'}): "
                    f"toolset issue(s) — {'; '.join(problem_parts)}"
                )

                async def _toolset_config_error_stream() -> AsyncGenerator[str, None]:
                    yield f"event: error\ndata: {json.dumps({'message': error_message, 'type': 'toolset_config_missing'})}\n\n"

                return StreamingResponse(_toolset_config_error_stream(), media_type="text/event-stream")

            agent_toolsets = configured_toolsets

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
                    # knowledgeBase_* connectors represent KB sources — they should NOT
                    # go into apps; their record groups are collected into kb instead.
                    if connector_id and not connector_id.startswith("knowledgeBase_"):
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
                    and not k.get("connectorId", "").startswith("knowledgeBase_")
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

        # Apply NO_KB sentinel BEFORE filtering agent_knowledge. If we filter first while
        # kb is still [], _filter_knowledge_by_enabled_sources early-returns the full list
        # (both enabled sets empty); injecting kb afterward left knowledge out of sync with filters.
        if not filters.get("kb") and agent_id != "agentIdPlaceholder":
            filters["kb"] = [NO_KB_SELECTED_FILTER]

        agent_knowledge = _filter_knowledge_by_enabled_sources(agent_knowledge, filters)

        logger.info(f"Filters: {filters}")

        _stream_conn_ids = [
            k["connectorId"] for k in agent_knowledge
            if isinstance(k, dict) and k.get("connectorId")
            and not str(k["connectorId"]).startswith("knowledgeBase_")
        ]
        connector_configs = await fetch_connector_configs(config_service, _stream_conn_ids)

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
            "connector_configs": connector_configs,
            "toolsetConfigs": toolset_configs,
            "conversationId": chat_query.conversationId,
            "is_service_account": is_service_account,
            "isPlaceholderAgent": agent_id == "agentIdPlaceholder",
            "modelName": model_name,
            "modelKey": model_key,
        }

        return StreamingResponse(
            stream_response(
                query_info,
                enriched_user_info,
                llm,
                logger,
                retrieval_service,
                graph_provider,
                reranker_service,
                config_service,
                org_info,
                modelName=model_name,
                modelKey=model_key,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in chat_stream: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e)) from e

async def get_assistant_agent(
    user_id: str,
    org_id: str,
    config_service: ConfigurationService,
    graph_provider: IGraphDBProvider,
    toolset_registry: ToolsetRegistry,
    logger: Logger,
) -> dict:
    """
    Get the assistant agent with all authenticated toolsets and accessible connectors.

    Args:
        user_id: User ID
        org_id: Organization ID
        config_service: Configuration service for etcd access
        graph_provider: Graph provider instance
        toolset_registry: Toolset registry instance
        logger: Logger instance

    Returns:
        Dictionary containing assistant agent configuration with toolsets and knowledge sources
    """
    from app.api.routes.toolsets import get_authenticated_toolsets

    # Get authenticated toolsets using the helper method
    try:
        authenticated_toolsets_list = await get_authenticated_toolsets(
            user_id=user_id,
            org_id=org_id,
            config_service=config_service,
            registry=toolset_registry,
        )
    except Exception as e:
        logger.error(f"Error fetching authenticated toolsets: {e}", exc_info=True)
        authenticated_toolsets_list = []

    # Get all accessible connectors for knowledge sources
    knowledge_sources = []

    try:
        # Get active connector instances accessible to the user
        user = await graph_provider.get_user_by_user_id(user_id=user_id)
        if not user:
            logger.error(f"User not found: {user_id}")
            return {}
        # Same `user_id` the graph expects as in kb_service (User id / document key).
        user_key = user.get("id") or user.get("_key")

        # One knowledge entry per accessible KB record group, matching normal agent shape.
        try:
            page_size = 500
            skip = 0
            while True:
                kbs, total, _ = await graph_provider.list_user_knowledge_bases(
                    user_id=user_key,
                    org_id=org_id,
                    skip=skip,
                    limit=page_size,
                )
                for kb in kbs:
                    kb_id = kb.get("id")
                    if not kb_id:
                        continue
                    title = (kb.get("name") or "").strip() or "Untitled"
                    one_group = {
                        "recordGroups": [kb_id],
                        "records": [],
                    }
                    kn: dict[str, Any] = {
                        "connectorId": f"knowledgeBase_{org_id}",
                        "name": title,
                        "displayName": title,
                        "type": Connectors.KNOWLEDGE_BASE.value,
                        "filters": one_group,
                        "filtersParsed": {
                            "recordGroups": [kb_id],
                            "records": [],
                        },
                    }
                    knowledge_sources.append(kn)
                if not kbs or skip + len(kbs) >= total:
                    break
                skip += page_size
        except Exception as e:
            logger.error(
                f"Error listing org knowledge bases for assistant: {e}", exc_info=True
            )

        connectors = await graph_provider.get_user_apps(
            user_id=user_key,
        )
        for connector in connectors:
            connector_id = connector.get("id", "") or connector.get("_key", "")
            connector_name = connector.get("name", "")
            connector_type = connector.get("type", "")

            if connector_type == Connectors.KNOWLEDGE_BASE.value:
                continue
            # Build knowledge source entry
            knowledge_entry = {
                "connectorId": connector_id,
                "name": connector_name,
                "displayName": connector_name,
                "type": connector_type,
                "filtersParsed": {
                    "recordGroups": [],
                    "records": []
                }
            }
            knowledge_sources.append(knowledge_entry)
    except Exception as e:
        logger.error(f"Error fetching knowledge sources: {e}", exc_info=True)
        knowledge_sources = []

    # Return assistant agent configuration
    return {
        "systemPrompt": "You are a helpful AI assistant with access to various tools and knowledge sources. Use them to help users accomplish their tasks efficiently.",
        "models": [],
        "startMessage": "Hello! I'm your AI assistant. I have access to your connected tools and knowledge bases. How can I help you today?",
        "name": "assistant",
        "description": "AI assistant with access to all your authenticated tools and knowledge sources",
        "isActive": True,
        "tags": ["assistant", "general-purpose"],
        "toolsets": authenticated_toolsets_list,
        "knowledge": knowledge_sources,
    }
