"""
Agent API Routes
Handles agent instances, templates, chat, and permissions using graph-based architecture
"""

import json
import os
import uuid
from collections.abc import AsyncGenerator
from logging import Logger
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, field_validator

from app.agents.agent_loop.protocol import resolve_protocol
from app.agents.agent_loop.stream_bridge import run_agent_loop_stream
from app.agents.chat_modes.custom_instructions import resolve_custom_instructions
from app.agents.chat_modes.policy import AgentCapabilities, resolve_agent_policy
from app.agents.registry.toolset_registry import ToolsetRegistry
from app.api.middlewares.auth import authMiddleware, require_scopes
from app.api.routes.chatbot import get_llm_for_chat
from app.config.configuration_service import ConfigurationService
from app.config.constants.ai_models import REASONING_EFFORT_VALUES, validate_reasoning_effort
from app.config.constants.arangodb import CollectionNames, Connectors
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import OAuthScopes, config_node_constants
from app.modules.agents.capability_summary import fetch_connector_configs
from app.modules.agents.qna.chat_state import _extract_kb_app_ids
from app.modules.agents.qna.router import (
    RouteDecision,  # noqa: F401 - re-exported for backward-compat imports (see below)
)
from app.modules.agents.qna.router import (
    build_capability_context as _build_agent_capability_context,  # noqa: F401
)
from app.modules.agents.qna.router import (
    build_prior_routing_messages as _build_prior_routing_messages,  # noqa: F401
)
from app.modules.transformers.blob_storage import (
    BlobStorage,  # noqa: F401 - re-exported, see above
)
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.telemetry.event_buffer import record_event
from app.telemetry.identity import domain_from_email
from app.utils.attachment_utils import (
    resolve_attachments,  # noqa: F401 - re-exported, see above
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# `RouteDecision`/`_build_agent_capability_context`/`_build_prior_routing_messages`/
# `BlobStorage`/`resolve_attachments` moved to `app.modules.agents.qna.router`
# (Phase 7 of the agent-loop migration, so the new agent-loop auto-router
# shares one classification implementation with this route). Re-exported
# here, unused, purely so existing `from app.api.routes.agent import ...`
# call sites and test patches keep working — see Phase 9's test-migration
# plan for retiring these once the affected tests are updated to import
# from the new module directly.

router = APIRouter()


def _resolve_protocol(chat_query: "ChatQuery", request: Request) -> str:
    """Negotiate the SSE wire protocol for `chat_stream` — see
    `app.agents.agent_loop.protocol.resolve_protocol` (shared with
    `chatbot.py::askAIStream` so both `/chat/stream`-shaped routes
    negotiate identically)."""
    return resolve_protocol(chat_query.protocol, request)


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


def _parse_agent_capabilities(raw: dict[str, Any] | None) -> AgentCapabilities:
    """Parse the raw ``agentCapabilities`` dict into a typed dataclass.

    Unknown keys are silently ignored; missing booleans default to ``True``
    (capability enabled) so the absence of a field never disables tools.
    """
    if not raw or not isinstance(raw, dict):
        return AgentCapabilities()
    return AgentCapabilities(
        internal_search=bool(raw.get("internalSearch", True)),
        web_search=bool(raw.get("webSearch", True)),
        deep_search=bool(raw.get("deepSearch", False)),
    )

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
    # "none" | "low" | "medium" | "high" | "max" — forwarded to the LLM factory's
    # reasoning_effort param; absent/None means no explicit override (the LLM
    # factory applies DEFAULT_REASONING_EFFORT for reasoning-capable models).
    reasoningEffort: str | None = None
    timezone: str | None = None
    currentTime: str | None = None
    conversationId: str | None = None
    # End-user display name when JWT userId is synthetic (e.g. Slack) — see
    # _merge_end_user_into_service_account_user_info.
    callerDisplayName: str | None = None
    callerEmail: str | None = None
    attachments: list[dict[str, Any]] = []
    # AG-UI protocol negotiation (see the migration plan) — Node.js sets
    # AG-UI is the only supported SSE wire protocol. This field is
    # accepted but ignored — `resolve_protocol` always returns "agui".
    protocol: str | None = None
    # Per-request capability toggles — allow the user to narrow what the
    # agent uses for a single session. Capabilities only narrow, never
    # expand: an agent without web search configured stays without it even
    # if agentCapabilities.webSearch=True.
    agentCapabilities: dict[str, Any] | None = None
    # TEMPORARY token-savings experiment — see `RecordIdShortener` in
    # `utils/chat_helpers.py`. Opt-in and disabled by default: short "R<n>"
    # labels are only valid for the request that minted them, so callers
    # that rely on record ids surviving across turns should leave this off.
    enableRecordIdShortening: bool = False

    _validate_reasoning_effort = field_validator("reasoningEffort")(validate_reasoning_effort)


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
        "email": user.get("email"),
        "domain": domain_from_email(user.get("email")),
        "isServiceAccount": bool(user.get("isServiceAccount", False)),
        "sendUserInfo": request.query_params.get("sendUserInfo", True),
    }



def _merge_end_user_into_service_account_user_info(
    creator_enriched: dict[str, Any],
    caller_display_name: str | None,
    caller_email_override: str | None = None,
) -> dict[str, Any]:
    """Overlay end-user name/email for LLM context; keep creator userId/orgId for retrieval ACL.

    ``caller_*`` values are expected pre-validated (e.g. via ``ChatQuery``). Empty strings
    after strip are treated as absent.
    """
    out = creator_enriched.copy()
    caller_email = (caller_email_override or "").strip()
    caller_name = (caller_display_name or "").strip()
    if caller_email:
        out["userEmail"] = caller_email
        out["email"] = caller_email
    if caller_name:
        for k in ("fullName", "displayName", "firstName", "lastName", "name"):
            out.pop(k, None)
        out["fullName"] = caller_name
        out["displayName"] = caller_name

    return out


async def _resolve_service_account_caller_identity(
    enriched_user_info: dict[str, Any],
    chat_query: ChatQuery,
    user_context: dict[str, Any],
    graph_provider: IGraphDBProvider,
    logger: Logger,
) -> dict[str, Any]:
    """Resolve the actual caller's name/email for a service-account agent chat.

    Priority:
      1. Explicit callerDisplayName / callerEmail from the request (e.g. Slack sends these).
      2. Fall back to the requesting user's document (platform-UI users have a real userId
         in the JWT, so we can look them up).

    Retrieval ACL stays on the agent creator — only the LLM-visible name/email changes.
    """
    caller_name = chat_query.callerDisplayName
    caller_email = chat_query.callerEmail

    if not caller_name and not caller_email:
        requesting_user_id = user_context.get("userId")
        if requesting_user_id:
            try:
                requesting_user_doc = await _get_user_document(requesting_user_id, graph_provider, logger)
                if requesting_user_doc and isinstance(requesting_user_doc, dict):
                    raw_name = requesting_user_doc.get("fullName") or requesting_user_doc.get("displayName") or ""
                    raw_email = requesting_user_doc.get("email") or ""
                    caller_name = raw_name if isinstance(raw_name, str) else None
                    caller_email = raw_email if isinstance(raw_email, str) else None
            except Exception:
                logger.debug(
                    "Could not look up requesting user %s for service-account caller context"
                    " (expected for Slack/synthetic callers)",
                    requesting_user_id,
                )

    if caller_name or caller_email:
        return _merge_end_user_into_service_account_user_info(
            enriched_user_info, caller_name, caller_email,
        )
    return enriched_user_info


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
            "accountType": raw_account_type,
            "name": org_doc.get("name") or "",
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
    creator_user_id = creator_doc.get("userId")
    if not creator_user_id:
        logger.error(
            "Service account agent creator %s has no userId field",
            creator_key,
        )
        raise HTTPException(
            status_code=500,
            detail="Agent creator is missing userId; cannot resolve knowledge permissions.",
        )
    synthetic = {
        "userId": str(creator_user_id),
        "orgId": str(creator_doc.get("orgId") or "").strip(),
        "email": (creator_doc.get("email") or "").strip(),
    }
    return await _enrich_user_info(synthetic, creator_doc)


async def _load_service_account_agent_for_chat(
    agent_id: str,
    org_key: str,
    graph_provider: IGraphDBProvider,
    logger: Logger,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Fetch service-account agent, validate, and build creator-based user info for chat/stream."""
    agent = await graph_provider.get_agent(agent_id, org_key)
    if not agent or not agent.get("isServiceAccount"):
        raise AgentNotFoundError(agent_id)
    enriched_user_info = await _enrich_user_info_for_service_account_agent_chat(
        agent, graph_provider, logger
    )
    perm = {"can_edit": False, "can_share": False, "role": "viewer"}
    return agent, enriched_user_info, perm


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


def _parse_default_reasoning_effort(raw_value: Any) -> str | None:
    """Validate the agent-level reasoning effort default.

    Returns ``None`` for an absent/blank value (no default configured — the
    per-request `reasoningEffort`, or DEFAULT_REASONING_EFFORT if neither is
    set, applies). Raises for any non-empty value outside the platform enum.
    """
    if raw_value is None:
        return None
    value = str(raw_value).strip()
    if not value:
        return None
    try:
        validate_reasoning_effort(value)
    except ValueError as exc:
        raise InvalidRequestError(
            f"Invalid defaultReasoningEffort '{value}'. "
            f"Must be one of: {', '.join(sorted(REASONING_EFFORT_VALUES))}."
        ) from exc
    return value


_SUPPORTED_WEB_SEARCH_PROVIDERS = {"duckduckgo", "serper", "tavily", "exa"}


def _parse_web_search(raw_web_search: Any) -> str | None:
    """Normalize the agent-level web-search attachment to a provider string.

    Accepts either:
    - a dict like {"provider": "serper", ...}
    - a provider string like "serper"

    Returns the sanitized provider (lowercase), or None if invalid/missing.
    """
    if not raw_web_search:
        return None

    provider = ""
    if isinstance(raw_web_search, dict):
        provider = str(raw_web_search.get("provider", "")).strip().lower()
    elif isinstance(raw_web_search, str):
        provider = raw_web_search.strip().lower()

    if not provider or provider not in _SUPPORTED_WEB_SEARCH_PROVIDERS:
        return None
    return provider


def _format_web_search_for_response(raw_web_search: Any) -> dict[str, Any] | None:
    """Normalize webSearch payloads to an API-friendly object shape."""
    provider = _parse_web_search(raw_web_search)
    if not provider:
        return None

    formatted: dict[str, Any] = {"provider": provider}
    if isinstance(raw_web_search, dict):
        provider_key = str(raw_web_search.get("providerKey", "")).strip()
        provider_label = str(raw_web_search.get("providerLabel", "")).strip()
        if provider_key:
            formatted["providerKey"] = provider_key
        if provider_label:
            formatted["providerLabel"] = provider_label
    return formatted


def _is_web_search_enabled(selected_tools: list[str] | None) -> bool:
    """Whether web_search should remain enabled for this request.

    `selected_tools is None` means "all actions", so web_search stays enabled.
    When an explicit tools list is provided, require a web_search entry.
    """
    if selected_tools is None:
        return True

    for tool in selected_tools:
        tool_name = str(tool).strip().lower()
        if tool_name == "web_search" or tool_name.startswith("web_search."):
            return True
    return False


async def _resolve_default_web_search_config(
    config_service: ConfigurationService,
    logger: Logger,
) -> dict[str, Any] | None:
    """Auto-detect the default web search provider from org-level config.

    Used by the assistant agent (agentIdPlaceholder) which doesn't have an
    explicit webSearch attachment but should still offer the tool when the
    org has a provider configured.
    """
    try:
        web_search_config = await config_service.get_config(
            config_node_constants.WEB_SEARCH.value,
            default={},
            use_cache=False,
        )
    except Exception as e:
        logger.warning("Failed to load web search configuration for auto-detect: %s", e)
        return None

    providers = (
        web_search_config.get("providers", [])
        if isinstance(web_search_config, dict)
        else []
    )
    if not isinstance(providers, list):
        providers = []

    default_provider = next(
        (p for p in providers if isinstance(p, dict) and p.get("isDefault")),
        None,
    )

    # Whenever no provider carries isDefault=true -- whether the org has never
    # configured any provider (empty/absent `providers`) or has configured one
    # without marking it default -- the Node.js layer treats DuckDuckGo as the
    # active default (it clears all isDefault flags rather than inserting a
    # DuckDuckGo entry into the array; see `cm_controller.ts::getWebSearchProviders`).
    if not default_provider:
        logger.debug("No explicit default web search provider; falling back to duckduckgo")
        return {"provider": "duckduckgo", "configuration": {}}

    provider = str(default_provider.get("provider", "")).strip().lower()
    if not provider or provider not in _SUPPORTED_WEB_SEARCH_PROVIDERS:
        return None

    configuration = default_provider.get("configuration", {})
    if not isinstance(configuration, dict):
        configuration = {}

    return {"provider": provider, "configuration": configuration}


async def _resolve_web_search_tool_config(
    provider: str | None,
    config_service: ConfigurationService,
    logger: Logger,
) -> dict[str, Any] | None:
    """Resolve provider-specific config for the web_search tool at runtime."""
    if not provider:
        return None

    try:
        web_search_config = await config_service.get_config(
            config_node_constants.WEB_SEARCH.value,
            default={},
            use_cache=False,
        )
    except Exception as e:
        logger.warning(
            "Failed to load web search configuration for provider '%s': %s",
            provider,
            str(e),
        )
        return {"provider": provider, "configuration": {}}

    providers = (
        web_search_config.get("providers", [])
        if isinstance(web_search_config, dict)
        else []
    )
    if not isinstance(providers, list):
        providers = []

    selected_provider = next(
        (
            entry
            for entry in providers
            if isinstance(entry, dict)
            and str(entry.get("provider", "")).strip().lower() == provider
        ),
        None,
    )

    if not selected_provider:
        return {"provider": provider, "configuration": {}}

    configuration = selected_provider.get("configuration", {})
    if not isinstance(configuration, dict):
        configuration = {}

    return {"provider": provider, "configuration": configuration}


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
    Filter agent_knowledge to only include entries enabled via filters.

    KB collections and app connectors are both UUID-identified connectors
    now, but the two enabled-sets are still tracked in separate filter
    buckets upstream — a KB entry's connectorId is only ever placed in
    filters["kb"], NEVER filters["apps"] (see the `!= "KB"` exclusion a
    few lines above each call site). So each entry must be checked
    against the bucket matching ITS OWN type, not filters["apps"] alone —
    checking only "apps" silently dropped every KB entry whenever at
    least one app connector was also configured.

    When the caller explicitly supplies filter keys (even as empty lists),
    empty means "nothing enabled" — return []. Pass-through (return the
    full list unfiltered) only happens when NEITHER key is present at all.
    """
    apps_present = "apps" in filters
    kb_present = "kb" in filters

    if not apps_present and not kb_present:
        return agent_knowledge

    enabled_apps = set(filters.get("apps") or [])
    enabled_kb = {
        cid for cid in (filters.get("kb") or [])
        if cid and cid != NO_KB_SELECTED_FILTER
    }

    result: list[dict[str, Any]] = []
    for k in agent_knowledge:
        if not isinstance(k, dict):
            continue
        is_kb = (k.get("type") or "").strip().upper() == "KB"
        enabled_set = enabled_kb if is_kb else enabled_apps
        if k.get("connectorId", "") in enabled_set:
            result.append(k)
    return result


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

    # Batch create agent -> toolset edges. Re-raise — mirrors `_create_mcp_server_edges`,
    # since every caller already wraps this in a transaction rollback or an HTTPException.
    try:
        await graph_provider.batch_create_edges(agent_toolset_edges, CollectionNames.AGENT_HAS_TOOLSET.value)
    except Exception as e:
        logger.error(f"Failed to create agent-toolset edges: {e}")
        raise

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

    # Batch create all tool nodes. Must raise on failure: edge creation below points at
    # these node keys, and some graph providers create edges via MATCH/MERGE that succeeds
    # silently even when the referenced node was never created.
    if tool_nodes:
        try:
            result = await graph_provider.batch_upsert_nodes(tool_nodes, CollectionNames.AGENT_TOOLS.value)
            if not result:
                raise RuntimeError("Failed to create tool nodes")
        except Exception as e:
            logger.error(f"Failed to batch create tool nodes: {e}")
            raise

    # Batch create toolset -> tool edges. Re-raise for the same reason as above.
    if toolset_tool_edges:
        try:
            await graph_provider.batch_create_edges(toolset_tool_edges, CollectionNames.TOOLSET_HAS_TOOL.value)
        except Exception as e:
            logger.error(f"Failed to create toolset-tool edges: {e}")
            raise

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


def _parse_mcp_servers(raw_mcp_servers: list[Any]) -> dict[str, dict[str, Any]]:
    """Parse attached MCP server references with their tools.

    Unlike `_parse_toolsets` (keyed by name — built-in toolset types have a
    single instance system-wide), MCP servers are keyed by `instanceId`: an
    agent can attach several distinct instances, but never two instances of
    the same `typeId` — that's enforced here so `mcp_{server_type}_{tool}`
    tool names stay unique at chat time (see `get_authenticated_mcp_servers`
    in `app/agents/mcp/service.py`).
    """
    mcp_servers_with_tools: dict[str, dict[str, Any]] = {}
    seen_type_ids: dict[str, str] = {}

    if not raw_mcp_servers or not isinstance(raw_mcp_servers, list):
        return mcp_servers_with_tools

    for mcp_data in raw_mcp_servers:
        if not isinstance(mcp_data, dict):
            continue

        instance_id = str(mcp_data.get("instanceId", "")).strip()
        if not instance_id:
            continue

        name = str(mcp_data.get("name", "")).strip()
        if not name:
            continue

        type_id = mcp_data.get("typeId") or None
        if type_id:
            existing_instance_id = seen_type_ids.get(type_id)
            if existing_instance_id and existing_instance_id != instance_id:
                raise InvalidRequestError(
                    f"Cannot attach two MCP server instances of the same type ('{type_id}') to one agent."
                )
            seen_type_ids[type_id] = instance_id

        display_name = mcp_data.get("displayName") or name.replace("_", " ").title()
        tools_list = mcp_data.get("tools", [])

        if instance_id not in mcp_servers_with_tools:
            mcp_servers_with_tools[instance_id] = {
                "name": name,
                "displayName": display_name,
                "typeId": type_id,
                "tools": [],
            }

        for tool in tools_list:
            if isinstance(tool, dict):
                tool_name = tool.get("name", "")
                if tool_name:
                    mcp_servers_with_tools[instance_id]["tools"].append({
                        "name": tool_name,
                        "fullName": tool.get("fullName", f"{name}.{tool_name}"),
                        "description": tool.get("description", "")
                    })

    return mcp_servers_with_tools


async def _create_mcp_server_edges(
    agent_key: str,
    mcp_servers_with_tools: dict[str, dict[str, Any]],
    user_info: dict[str, Any],
    user_key: str,
    graph_provider: IGraphDBProvider,
    logger: Logger,
    transaction: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Create MCP server nodes and edges for an agent using batch operations.

    Mirrors `_create_toolset_edges`, keyed by `instanceId` instead of name.
    MCP server nodes never carry credentials — auth is resolved at chat time
    from `/services/mcp/credentials/{instanceId}/{ownerId}` (etcd), same as
    the attach-time Node/Python validation that already rejects secrets here.
    Accepts an optional `transaction` so callers can fold this into an
    existing agent-creation transaction (unlike the toolset equivalent,
    which create_agent re-implements inline for that reason).
    """
    created_mcp_servers: list[dict[str, Any]] = []
    failed_mcp_servers: list[dict[str, Any]] = []
    time = get_epoch_timestamp_in_ms()

    if not mcp_servers_with_tools:
        return created_mcp_servers, failed_mcp_servers

    # Prepare all MCP server nodes
    mcp_server_nodes = []
    mcp_server_mapping = {}  # Map instance_id to node key/tools

    for instance_id, mcp_data in mcp_servers_with_tools.items():
        mcp_server_key = str(uuid.uuid4())
        name = mcp_data["name"]
        display_name = mcp_data["displayName"]
        type_id = mcp_data.get("typeId")
        tools_list = mcp_data["tools"]

        mcp_server_node = {
            "_key": mcp_server_key,
            "instanceId": instance_id,
            "name": name,
            "displayName": display_name,
            "userId": user_info["userId"],
            "createdBy": user_key,
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time
        }
        if type_id:
            mcp_server_node["typeId"] = type_id

        mcp_server_nodes.append(mcp_server_node)
        mcp_server_mapping[instance_id] = {
            "key": mcp_server_key,
            "name": name,
            "displayName": display_name,
            "tools": tools_list
        }

    # Batch create all MCP server nodes
    try:
        result = await graph_provider.batch_upsert_nodes(
            mcp_server_nodes, CollectionNames.AGENT_MCP_SERVERS.value, transaction=transaction
        )
        if not result:
            return created_mcp_servers, [{"name": "all", "error": "Failed to create MCP server nodes"}]
    except Exception as e:
        logger.error(f"Failed to batch create MCP server nodes: {e}")
        return created_mcp_servers, [{"name": "all", "error": str(e)}]

    # Prepare agent -> mcpServer edges
    agent_mcp_server_edges = [
        {
            "_from": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
            "_to": f"{CollectionNames.AGENT_MCP_SERVERS.value}/{mcp_info['key']}",
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
        }
        for mcp_info in mcp_server_mapping.values()
    ]

    # Batch create agent -> mcpServer edges. Re-raise (rather than log-and-continue) — every
    # caller already wraps this in a transaction rollback or an HTTPException, so swallowing
    # here would otherwise let create/update report "success" with MCP server nodes that were
    # never actually linked to the agent.
    try:
        await graph_provider.batch_create_edges(
            agent_mcp_server_edges, CollectionNames.AGENT_HAS_MCP_SERVER.value, transaction=transaction
        )
    except Exception as e:
        logger.error(f"Failed to create agent-mcpServer edges: {e}")
        raise

    # Prepare all tool nodes and edges (tools live in the shared AGENT_TOOLS
    # collection, same as toolset tools)
    tool_nodes = []
    mcp_server_tool_edges = []
    tool_mapping = {}  # Map full_name to tool_key

    for mcp_info in mcp_server_mapping.values():
        for tool_data in mcp_info["tools"]:
            tool_name = tool_data["name"]
            full_name = tool_data["fullName"]
            description = tool_data["description"]

            tool_key = str(uuid.uuid4())

            tool_node = {
                "_key": tool_key,
                "name": tool_name,
                "fullName": full_name,
                "toolsetName": mcp_info["name"],
                "description": description,
                "createdBy": user_key,
                "createdAtTimestamp": time,
                "updatedAtTimestamp": time
            }

            tool_nodes.append(tool_node)
            tool_mapping[full_name] = {
                "key": tool_key,
                "name": tool_name,
                "mcpServer": mcp_info["name"]
            }

            # Prepare mcpServer -> tool edge
            mcp_server_tool_edges.append({
                "_from": f"{CollectionNames.AGENT_MCP_SERVERS.value}/{mcp_info['key']}",
                "_to": f"{CollectionNames.AGENT_TOOLS.value}/{tool_key}",
                "createdAtTimestamp": time,
                "updatedAtTimestamp": time,
            })

    # Batch create all tool nodes. Must raise on failure: edge creation below points at
    # these node keys, and some graph providers create edges via MATCH/MERGE that succeeds
    # silently even when the referenced node was never created.
    if tool_nodes:
        try:
            result = await graph_provider.batch_upsert_nodes(
                tool_nodes, CollectionNames.AGENT_TOOLS.value, transaction=transaction
            )
            if not result:
                raise RuntimeError("Failed to create MCP tool nodes")
        except Exception as e:
            logger.error(f"Failed to batch create MCP tool nodes: {e}")
            raise

    # Batch create mcpServer -> tool edges. Re-raise for the same reason as the
    # agent->mcpServer edges above — a swallowed failure here leaves tools listed in the
    # response with no MCP_SERVER_HAS_TOOL edge actually connecting them.
    if mcp_server_tool_edges:
        try:
            await graph_provider.batch_create_edges(
                mcp_server_tool_edges, CollectionNames.MCP_SERVER_HAS_TOOL.value, transaction=transaction
            )
        except Exception as e:
            logger.error(f"Failed to create mcpServer-tool edges: {e}")
            raise

    # Build response with created MCP servers and tools
    for mcp_info in mcp_server_mapping.values():
        created_tools = []
        for tool_data in mcp_info["tools"]:
            full_name = tool_data["fullName"]
            if full_name in tool_mapping:
                created_tools.append({
                    "name": tool_mapping[full_name]["name"],
                    "fullName": full_name,
                    "key": tool_mapping[full_name]["key"]
                })

        created_mcp_servers.append({
            "name": mcp_info["name"],
            "displayName": mcp_info["displayName"],
            "key": mcp_info["key"],
            "tools": created_tools
        })

    return created_mcp_servers, failed_mcp_servers


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


def _parse_skills(raw_skills: list[Any]) -> list[str]:
    """Parse the agent payload's `skills: [{name}] | [name, ...]` field into
    a de-duplicated, order-preserving list of skill names.

    Unlike `_parse_toolsets`/`_parse_knowledge_sources`, this never creates
    anything: skill NODES already exist in `agentSkills` (owned by the
    Skills management API — `api/routes/skills.py`), so agent create/update
    only ever links to a skill that's already there. `_create_skill_edges`
    below re-validates existence/ownership at write time regardless of
    what the client claims here.
    """
    names: list[str] = []
    seen: set[str] = set()
    if not raw_skills or not isinstance(raw_skills, list):
        return names
    for entry in raw_skills:
        name = entry.get("name") if isinstance(entry, dict) else entry if isinstance(entry, str) else None
        if not isinstance(name, str):
            continue
        name = name.strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


async def _create_skill_edges(
    agent_key: str,
    skill_names: list[str],
    org_id: str,
    user_key: str,
    graph_provider: IGraphDBProvider,
    logger: Logger,
    transaction: str | None = None,
) -> list[str]:
    """Create `agentHasSkill` edges from an agent to each assigned skill —
    mirrors `_create_toolset_edges`/`_create_knowledge_edges` in shape, but
    never creates a skill node: skills are owned by the Skills management
    API, this only links to ones that already exist.

    Defense in depth (mirrors `GraphSkillStore._is_visible`): a name is
    only linked when it resolves to an existing skill in this org AND
    (the acting user created it OR it's a `builtin`-sourced skill) — a
    user can only assign their own skills (or org-wide builtins) to an
    agent, never a co-worker's. Any name that doesn't resolve is logged
    and skipped rather than failing the whole create/update — a stale
    skill reference in the payload should never block agent creation.
    """
    if not skill_names:
        return []

    skills_collection = CollectionNames.AGENT_SKILLS.value
    time = get_epoch_timestamp_in_ms()
    edges: list[dict[str, Any]] = []
    linked_names: list[str] = []

    for name in skill_names:
        skill_key = f"{org_id}_{name}"
        skill_doc = await graph_provider.get_document(skill_key, skills_collection, transaction=transaction)
        if not skill_doc or skill_doc.get("orgId") != org_id:
            logger.warning(f"Skipping unknown skill '{name}' for agent {agent_key}")
            continue
        if skill_doc.get("source") != "builtin" and skill_doc.get("createdBy") != user_key:
            logger.warning(f"Skipping skill '{name}' not owned by user {user_key} for agent {agent_key}")
            continue
        edges.append({
            "_from": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
            "_to": f"{skills_collection}/{skill_key}",
            "skillName": name,
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
        })
        linked_names.append(name)

    if edges:
        await graph_provider.batch_create_edges(edges, CollectionNames.AGENT_HAS_SKILL.value, transaction=transaction)
    return linked_names


async def _enrich_agent_models(agent: dict[str, Any], config_service: ConfigurationService, logger: Logger) -> None:
    """Enrich agent models with full configurations from etcd.

    Agents may be created/updated with no models, in which case they fall
    back to the organization's default LLM at chat time (see
    `get_llm_for_chat`). `usesOrgDefault` surfaces that state to API
    consumers without persisting it as a separate field on the agent doc.
    """
    model_entries = agent.get("models", [])

    if not model_entries or not isinstance(model_entries, list):
        agent["models"] = []
        agent["usesOrgDefault"] = True
        return

    agent["usesOrgDefault"] = False

    try:
        ai_models = await config_service.get_config(config_node_constants.AI_MODELS.value, use_cache=False)
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


def _mark_deprecated_tools(agent: dict[str, Any], logger: Logger) -> None:
    """
    Annotate agent.toolsets[].tools[] with deprecated=True when the tool's
    fullName is no longer present in the in-memory tool registry
    (i.e. its @tool was removed from code since the agent was created/edited).
    Mutates `agent` in place.

    NOTE: The old global tools registry has been removed. This function is
    currently a no-op until a replacement registry is wired in.
    """
    return


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
        default_reasoning_effort = _parse_default_reasoning_effort(body.get("defaultReasoningEffort"))

        # Models are optional: an agent created without any models falls back
        # to the organization's default LLM at chat time (see get_llm_for_chat).
        # When models ARE specified, at least one must be a reasoning model so
        # reasoning-effort settings behave predictably.
        if model_entries and not has_reasoning_model:
            raise InvalidRequestError(
                "When models are specified, at least one reasoning model is required."
            )

        # Parse toolsets, knowledge, skills, and MCP servers BEFORE starting transaction
        toolsets_with_tools = _parse_toolsets(body.get("toolsets", []))
        mcp_servers_with_tools = _parse_mcp_servers(body.get("mcpServers", []))
        knowledge_sources = _parse_knowledge_sources(body.get("knowledge", []))
        skill_names = _parse_skills(body.get("skills", []))
        web_search_attachment = _parse_web_search(body.get("webSearch"))

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
            "systemPrompt": body.get("systemPrompt", "").strip() or "You are a workplace productivity assistant. Help users with their connected work tools.",
            "instructions": body.get("instructions", "").strip() or None,
            "models": model_entries,
            "tags": body.get("tags", []) or [],
            "webSearch": web_search_attachment,
            "defaultReasoningEffort": default_reasoning_effort,
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
        created_mcp_servers: list[dict[str, Any]] = []
        failed_mcp_servers: list[dict[str, Any]] = []
        created_knowledge = []
        linked_skills: list[str] = []

        try:
            # Start transaction for ALL agent creation operations
            graph_provider = services["graph_provider"]
            transaction_id = await graph_provider.begin_transaction(
                read=[CollectionNames.AGENT_SKILLS.value],
                write=[
                    CollectionNames.AGENT_INSTANCES.value,
                    CollectionNames.PERMISSION.value,
                    CollectionNames.AGENT_TOOLSETS.value,
                    CollectionNames.AGENT_TOOLS.value,
                    CollectionNames.AGENT_HAS_TOOLSET.value,
                    CollectionNames.TOOLSET_HAS_TOOL.value,
                    CollectionNames.AGENT_MCP_SERVERS.value,
                    CollectionNames.AGENT_HAS_MCP_SERVER.value,
                    CollectionNames.MCP_SERVER_HAS_TOOL.value,
                    CollectionNames.AGENT_KNOWLEDGE.value,
                    CollectionNames.AGENT_HAS_KNOWLEDGE.value,
                    CollectionNames.AGENT_HAS_SKILL.value,
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

            # Step 3.5: Create attached MCP servers and their tools (within same transaction)
            if mcp_servers_with_tools:
                created_mcp_servers, failed_mcp_servers = await _create_mcp_server_edges(
                    agent_key, mcp_servers_with_tools, user_context, user_key,
                    graph_provider, logger, transaction=transaction_id,
                )
                logger.debug(f"Created {len(created_mcp_servers)} MCP server(s) for agent: {agent_key}")

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

            # Step 5: Link assigned skills (within same transaction) — mirrors
            # AGENT_HAS_TOOLSET/AGENT_HAS_KNOWLEDGE above but never creates a
            # skill node, only edges to skills that already exist.
            if skill_names:
                linked_skills = await _create_skill_edges(
                    agent_key, skill_names, org_key, user_key, graph_provider, logger,
                    transaction=transaction_id,
                )
                logger.debug(f"Linked {len(linked_skills)} skill(s) for agent: {agent_key}")

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
            "mcpServers": created_mcp_servers,
            "knowledge": created_knowledge,
            "skills": [{"name": n} for n in linked_skills],
        }
        response_agent["webSearch"] = _format_web_search_for_response(
            response_agent.get("webSearch"),
        )
        response_agent["createdBy"] = user_context["userId"]

        all_failed = failed_toolsets + failed_mcp_servers
        status = "partial_success" if all_failed else "success"
        message = f"Agent created with warnings: {len(all_failed)} attachment(s) failed" if all_failed else "Agent created successfully"

        return JSONResponse(
            status_code=200,
            content={
                "status": status,
                "message": message,
                "agent": response_agent,
                "warnings": all_failed if all_failed else None,
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


@router.get("/web-search-usage/{provider}", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_READ))])
async def get_web_search_provider_usage(request: Request, provider: str) -> JSONResponse:
    """Return agents in the org that use a specific web search provider."""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)
        org_key = user_context["orgId"]

        provider = provider.strip().lower()
        if provider not in _SUPPORTED_WEB_SEARCH_PROVIDERS:
            return JSONResponse(
                status_code=200,
                content={"success": True, "agents": []},
            )

        agents = await services["graph_provider"].get_agents_by_web_search_provider(
            org_key, provider
        )

        return JSONResponse(
            status_code=200,
            content={"success": True, "agents": agents},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/model-usage/{model_key}", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_READ))])
async def get_model_usage(request: Request, model_key: str) -> JSONResponse:
    """Return agents in the org that use a specific AI model."""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)
        org_key = user_context["orgId"]

        model_key = model_key.strip()
        if not model_key:
            return JSONResponse(
                status_code=200,
                content={"success": True, "agents": []},
            )

        agents = await services["graph_provider"].get_agents_by_model_key(
            org_key, model_key
        )

        return JSONResponse(
            status_code=200,
            content={"success": True, "agents": agents},
        )
    except HTTPException:
        raise
    except Exception as e:
        # Server-side failure (graph DB outage, etc.) — return 500 so callers
        # treat this as a transient backend error and fail-closed on deletion.
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=f"Internal server error while checking model usage: {str(e)}",
        ) from e


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

        _mark_deprecated_tools(agent, services["logger"])

        # Enrich models with configurations
        await _enrich_agent_models(agent, services["config_service"], services["logger"])
        agent.pop("modelsEnriched", None)
        agent["webSearch"] = _format_web_search_for_response(agent.get("webSearch"))

        creator_key = agent.get("createdBy")
        if creator_key and creator_key != "system":
            creator_doc = await services["graph_provider"].get_document(
                str(creator_key), CollectionNames.USERS.value
            )
            if creator_doc and creator_doc.get("userId"):
                agent["createdBy"] = creator_doc["userId"]

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

        creator_keys = {
            str(agent["createdBy"])
            for agent in agents
            if isinstance(agent, dict)
            and agent.get("createdBy")
            and agent.get("createdBy") != "system"
        }
        creators_by_key: dict[str, dict] = {}
        if creator_keys:
            creator_docs = await services["graph_provider"].get_nodes_by_field_in(
                CollectionNames.USERS.value,
                "id",
                list(creator_keys),
                return_fields=["id", "userId"],
            )
            for doc in creator_docs or []:
                if doc.get("id") and doc.get("userId"):
                    creators_by_key[str(doc["id"])] = doc

        for agent in agents:
            if not isinstance(agent, dict):
                continue
            agent["webSearch"] = _format_web_search_for_response(agent.get("webSearch"))
            # Cheap derived flag (no etcd lookup needed here); full model
            # enrichment only happens on the single-agent GET endpoint.
            agent["usesOrgDefault"] = not agent.get("models")
            creator_key = agent.get("createdBy")
            if creator_key and creator_key != "system":
                creator_doc = creators_by_key.get(str(creator_key))
                if creator_doc and creator_doc.get("userId"):
                    agent["createdBy"] = creator_doc["userId"]

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

        # Validate models if provided in update body. An empty array is valid
        # and clears the agent's models, reverting it to the organization's
        # default LLM at chat time. When a non-empty array is provided, at
        # least one entry must be a reasoning model.
        if "models" in body:
            raw_models = body.get("models", [])
            model_entries, has_reasoning_model = _parse_models(raw_models, logger)

            if model_entries and not has_reasoning_model:
                raise InvalidRequestError(
                    "When models are specified, at least one reasoning model is required."
                )

        if "defaultReasoningEffort" in body:
            body["defaultReasoningEffort"] = _parse_default_reasoning_effort(
                body.get("defaultReasoningEffort")
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


        # Normalize webSearch attachment before persisting
        if "webSearch" in body:
            body["webSearch"] = _parse_web_search(body.get("webSearch"))

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

        # Update attached MCP servers if provided in request (even if empty array - means detach all)
        if "mcpServers" in body:
            # Parse first to validate (duplicate typeId) before deletion
            mcp_servers_with_tools = _parse_mcp_servers(body.get("mcpServers", []))

            graph_provider = services["graph_provider"]
            transaction_id = None
            try:
                transaction_id = await graph_provider.begin_transaction(
                    read=[],
                    write=[
                        CollectionNames.AGENT_HAS_MCP_SERVER.value,
                        CollectionNames.AGENT_MCP_SERVERS.value,
                        CollectionNames.MCP_SERVER_HAS_TOOL.value,
                        CollectionNames.AGENT_TOOLS.value
                    ]
                )
                logger.debug(f"Started transaction for MCP server update on agent {agent_id}")

                agent_full_id = f"{CollectionNames.AGENT_INSTANCES.value}/{agent_id}"

                # ========== PHASE 1: GATHER ALL INFORMATION (READ ONLY) ==========

                mcp_server_edges = await graph_provider.get_edges_from_node(
                    agent_full_id,
                    CollectionNames.AGENT_HAS_MCP_SERVER.value,
                    transaction=transaction_id
                )

                mcp_server_keys = []
                mcp_server_full_ids = []
                for edge in mcp_server_edges:
                    mcp_server_full_id = edge.get("_to")
                    if mcp_server_full_id:
                        mcp_server_full_ids.append(mcp_server_full_id)
                        parts = mcp_server_full_id.split("/", 1)
                        if len(parts) == SPLIT_PATH_EXPECTED_PARTS:
                            mcp_server_keys.append(parts[1])

                logger.debug(f"Found {len(mcp_server_keys)} MCP server(s) connected to agent {agent_id}")

                all_tool_keys = []
                all_tool_full_ids = []
                for mcp_server_full_id in mcp_server_full_ids:
                    tool_edges = await graph_provider.get_edges_from_node(
                        mcp_server_full_id,
                        CollectionNames.MCP_SERVER_HAS_TOOL.value,
                        transaction=transaction_id
                    )

                    for edge in tool_edges:
                        tool_full_id = edge.get("_to")
                        if tool_full_id:
                            all_tool_full_ids.append(tool_full_id)
                            parts = tool_full_id.split("/", 1)
                            if len(parts) == SPLIT_PATH_EXPECTED_PARTS:
                                all_tool_keys.append(parts[1])

                logger.debug(f"Found {len(all_tool_keys)} tool(s) connected to MCP servers")

                # ========== PHASE 2: DELETE FROM LEAVES TO ROOT ==========

                # Step 1: Delete mcpServer -> tool edges (MCP_SERVER_HAS_TOOL)
                total_tool_edges_deleted = 0
                for tool_full_id in all_tool_full_ids:
                    count = await graph_provider.delete_all_edges_for_node(
                        tool_full_id,
                        CollectionNames.MCP_SERVER_HAS_TOOL.value,
                        transaction=transaction_id
                    )
                    total_tool_edges_deleted += count

                logger.debug(f"Deleted {total_tool_edges_deleted} mcpServer->tool edge(s)")

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

                # Step 3: Delete agent -> mcpServer edges (AGENT_HAS_MCP_SERVER)
                total_mcp_server_edges_deleted = 0
                for mcp_server_full_id in mcp_server_full_ids:
                    count = await graph_provider.delete_all_edges_for_node(
                        mcp_server_full_id,
                        CollectionNames.AGENT_HAS_MCP_SERVER.value,
                        transaction=transaction_id
                    )
                    total_mcp_server_edges_deleted += count

                logger.debug(f"Deleted {total_mcp_server_edges_deleted} agent->mcpServer edge(s)")

                # Step 4: Delete mcpServer nodes (now safe, all their edges are gone)
                deleted_mcp_server_nodes = 0
                if mcp_server_keys:
                    result = await graph_provider.delete_nodes(
                        mcp_server_keys,
                        CollectionNames.AGENT_MCP_SERVERS.value,
                        transaction=transaction_id
                    )
                    deleted_mcp_server_nodes = len(mcp_server_keys) if result else 0
                    logger.debug(f"Deleted {deleted_mcp_server_nodes} MCP server node(s)")

                logger.info(
                    f"Deleted for agent {agent_id}: "
                    f"{deleted_tool_nodes} tool(s), {deleted_mcp_server_nodes} MCP server(s), "
                    f"{total_tool_edges_deleted + total_mcp_server_edges_deleted} edge(s) total"
                )

                # Commit transaction after deletion
                await graph_provider.commit_transaction(transaction_id)
                transaction_id = None
                logger.debug(f"Committed transaction for MCP server deletion on agent {agent_id}")

            except Exception as e:
                if transaction_id:
                    try:
                        await graph_provider.rollback_transaction(transaction_id)
                        logger.warning(f"Aborted transaction for MCP server update on agent {agent_id}")
                    except Exception as abort_error:
                        logger.error(f"Failed to abort transaction: {abort_error}")
                logger.error(f"Failed to delete MCP server nodes and edges for agent {agent_id}: {e}", exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to delete MCP server nodes and edges: {str(e)}"
                ) from e

            # Create new MCP server nodes, tool nodes, and edges only if there are servers to attach.
            # Runs in its own transaction (the delete transaction above is already committed) so a
            # failure partway through rolls back rather than leaving orphaned MCP server/tool nodes
            # with no AGENT_HAS_MCP_SERVER edge linking them to the agent.
            if mcp_servers_with_tools:
                create_transaction_id = None
                try:
                    create_transaction_id = await graph_provider.begin_transaction(
                        read=[],
                        write=[
                            CollectionNames.AGENT_HAS_MCP_SERVER.value,
                            CollectionNames.AGENT_MCP_SERVERS.value,
                            CollectionNames.MCP_SERVER_HAS_TOOL.value,
                            CollectionNames.AGENT_TOOLS.value
                        ]
                    )
                    created_mcp_servers, failed_mcp_servers = await _create_mcp_server_edges(
                        agent_id, mcp_servers_with_tools, user_context, user_key,
                        services["graph_provider"], logger, transaction=create_transaction_id
                    )
                    if failed_mcp_servers:
                        logger.warning(
                            f"Agent {agent_id}: {len(failed_mcp_servers)} MCP server(s) failed to create: {failed_mcp_servers}"
                        )
                    await graph_provider.commit_transaction(create_transaction_id)
                    create_transaction_id = None
                    logger.info(f"Created {len(created_mcp_servers)} MCP server(s) for agent {agent_id}")
                except Exception as e:
                    if create_transaction_id:
                        try:
                            await graph_provider.rollback_transaction(create_transaction_id)
                            logger.warning(f"Aborted transaction for MCP server creation on agent {agent_id}")
                        except Exception as abort_error:
                            logger.error(f"Failed to abort transaction: {abort_error}")
                    logger.error(
                        f"Failed to create MCP server edges for agent {agent_id} after deletion: {e}",
                        exc_info=True
                    )
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to create MCP server edges: {str(e)}"
                    ) from e
            else:
                logger.info(f"All MCP servers detached for agent {agent_id}")

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

        # Update skill assignments if provided in request (even if empty array - means unassign all).
        # Unlike toolsets/knowledge, this never deletes NODES — only this agent's
        # AGENT_HAS_SKILL edges — since skills are owned by the Skills
        # management API, not by whichever agent happens to reference them.
        if "skills" in body:
            skill_names = _parse_skills(body.get("skills", []))
            graph_provider = services["graph_provider"]
            agent_full_id = f"{CollectionNames.AGENT_INSTANCES.value}/{agent_id}"
            transaction_id = None
            try:
                transaction_id = await graph_provider.begin_transaction(
                    read=[CollectionNames.AGENT_SKILLS.value],
                    write=[CollectionNames.AGENT_HAS_SKILL.value],
                )
                deleted_skill_edges = await graph_provider.delete_all_edges_for_node(
                    agent_full_id, CollectionNames.AGENT_HAS_SKILL.value, transaction=transaction_id,
                )
                logger.debug(f"Removed {deleted_skill_edges} existing agent->skill edge(s) for agent {agent_id}")

                linked_skills = (
                    await _create_skill_edges(
                        agent_id, skill_names, org_key, user_key, graph_provider, logger,
                        transaction=transaction_id,
                    )
                    if skill_names else []
                )
                await graph_provider.commit_transaction(transaction_id)
                transaction_id = None
                logger.info(f"Linked {len(linked_skills)} skill(s) for agent {agent_id}")
            except Exception as e:
                if transaction_id:
                    try:
                        await graph_provider.rollback_transaction(transaction_id)
                    except Exception as abort_error:
                        logger.error(f"Failed to abort transaction: {abort_error}")
                logger.error(f"Failed to update skill assignments for agent {agent_id}: {e}", exc_info=True)
                raise HTTPException(
                    status_code=500, detail=f"Failed to update skill assignments: {str(e)}",
                ) from e

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

def _parse_sse_events(chunk: str) -> list[tuple[str, Any]]:
    """Parses one or more `event: X\\ndata: Y\\n\\n` frames out of a raw SSE
    text chunk. Tolerant of a chunk containing multiple frames or a partial
    trailing one (returns only whole frames found) -- `chat()` drains the
    WHOLE stream before deciding anything, so a frame boundary split across
    two `body_iterator` chunks is completed by the next chunk's data before
    any frame is parsed here, not lost."""
    events: list[tuple[str, Any]] = []
    for block in chunk.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_name = None
        data_line = None
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_line = line[len("data:"):].strip()
        if event_name is None or data_line is None:
            continue
        try:
            events.append((event_name, json.loads(data_line)))
        except json.JSONDecodeError:
            continue
    return events


@router.post("/{agent_id}/chat", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_EXECUTE))])
async def chat(request: Request, agent_id: str) -> JSONResponse:
    """Chat with an agent (non-streaming).

    Runs the exact same agent-loop pipeline `chat_stream()` does -- same
    setup (toolset config loading, permission checks, LLM resolution, all
    ~250 lines of it), same `run_agent_loop_stream()` call -- by invoking
    that route function directly and draining its `StreamingResponse.
    body_iterator` instead of streaming it to the client. This is
    deliberately NOT a second copy of that setup logic: LangGraph's own
    separate non-streaming code path (`_select_agent_graph_for_query()` +
    `graph.ainvoke()`) was removed with the rest of LangGraph, and
    `chat_stream()`'s setup is too security-sensitive (credential lookup
    scoping — see its own comments) to risk drifting via duplication.

    Node.js's `createAgentConversation` (`POST /api/v1/agents/:agentKey/
    conversations` -> `POST /api/v1/agent/{agent_id}/chat`) is this
    endpoint's one live caller (see Phase 0 audit) — it reads whichever of
    `completion_data`'s fields are present (`answer` required, everything
    else optional; see `buildAIResponseMessage` in
    `enterprise_search/utils/utils.ts`), so returning agent-loop's
    `completion_data` shape as-is (no `reason`/`answerMatchType` on the
    success path -- see `respond.py`) does not break it.
    """
    streaming_response = await chat_stream(request, agent_id)
    if not isinstance(streaming_response, StreamingResponse):
        return streaming_response  # pragma: no cover - chat_stream() only returns StreamingResponse today

    completion_data: dict[str, Any] | None = None
    error_payload: dict[str, Any] | None = None
    async for raw_chunk in streaming_response.body_iterator:
        text = raw_chunk.decode("utf-8") if isinstance(raw_chunk, bytes) else raw_chunk
        for event_name, data in _parse_sse_events(text):
            if event_name == "complete" and isinstance(data, dict):
                completion_data = data
            elif event_name == "error" and isinstance(data, dict):
                error_payload = data

    if error_payload is not None:
        return JSONResponse(
            status_code=error_payload.get("status_code", 400),
            content={
                "status": error_payload.get("status", "error"),
                "message": error_payload.get("message") or error_payload.get("error") or "An error occurred",
                "searchResults": [],
                "records": [],
            },
        )
    if completion_data is None:
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": "The agent did not produce a response.",
                "searchResults": [],
                "records": [],
            },
        )
    return JSONResponse(content=completion_data)


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
        protocol = _resolve_protocol(chat_query, request)
        logger.info("chat_stream: resolved protocol=%s (body.protocol=%r, query=%r)",
                     protocol, chat_query.protocol, request.query_params.get("protocol"))

        record_event("agent_run", {
            "orgId": user_context.get("orgId"),
            "userId": user_context.get("userId"),
            "email": user_context.get("email"),
            "domain": user_context.get("domain"),
            "has_tools": bool(chat_query.tools),
            "streaming": True,
        })

        # `chat_query.tools` is a FILTER over the agent's configured toolsets
        # (see the `None` "use every configured toolset" branch further
        # below), not a per-turn LLM-context budget — lazy tool disclosure
        # (`lazy_tools_wiring.py`, default ON) means the number of schemas
        # actually bound to the model no longer scales with this list's
        # size. This is now purely a request-size sanity bound, raised well
        # above any real explicit selection so it stops rejecting legitimate
        # "everything selected" requests exploded client-side into one
        # fullName per action (previously 128, which a handful of
        # multi-action toolsets already exceeded — see chat-input.tsx).
        _MAX_TOOLS = 1024
        if chat_query.tools is not None and len(chat_query.tools) > _MAX_TOOLS:
            raise HTTPException(
                status_code=400,
                detail=f"Too many actions: maximum {_MAX_TOOLS} actions are allowed per request.",
            )

        org_info = await _get_org_info(user_context, services["graph_provider"], logger)

        if agent_id == "agentIdPlaceholder":
            toolset_registry = getattr(request.app.state, "toolset_registry", None)
            agent = await get_assistant_agent(user_context["userId"], org_key, config_service, graph_provider, toolset_registry, logger)
            user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], logger)
            enriched_user_info = await _enrich_user_info(user_context, user_doc)
            perm = {"can_edit": False, "can_share": False, "role": "viewer"}
            is_service_account = False

        else:
            agent = await services["graph_provider"].get_agent(agent_id, org_key)
            if not agent:
                raise AgentNotFoundError(agent_id)
            is_service_account = agent.get("isServiceAccount", False)

            if is_service_account:
                enriched_user_info = await _enrich_user_info_for_service_account_agent_chat(
                    agent, graph_provider, logger
                )
                enriched_user_info = await _resolve_service_account_caller_identity(
                    enriched_user_info, chat_query, user_context, graph_provider, logger,
                )
                perm = {"can_edit": False, "can_share": False, "role": "viewer"}
                logger.debug(f"loaded service account agent. enriched_user_info: {enriched_user_info}")
            else:
                # Standard user path: look up the user document and verify permissions.
                user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], logger)
                enriched_user_info = await _enrich_user_info(user_context, user_doc)
                perm = await services["graph_provider"].check_agent_permission(agent_id, user_doc["_key"], org_key)
                if not perm:
                    raise AgentNotFoundError(agent_id)

        agent.update(perm)

        # Determine model key/name: prefer explicit query params, then agent's first model.
        # If neither is available, model_key/model_name stay None and
        # get_llm_for_chat() below resolves the organization's default LLM.
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
            else:
                logger.info(
                    f"Agent {agent_id} has no configured models; falling back to organization default LLM"
                )

        # Get LLM for chat. Explicit per-request effort wins; otherwise fall back
        # to the agent's configured default (if any).
        effective_reasoning_effort = chat_query.reasoningEffort or agent.get("defaultReasoningEffort")
        llm_result = (await get_llm_for_chat(
            services["config_service"],
            model_key,
            model_name,
            chat_query.chatMode,
            reasoning_effort=effective_reasoning_effort,
        ))

        if not llm_result:
            raise LLMInitializationError()

        llm = llm_result[0]
        llm_config = llm_result[1]
        ai_models_config = llm_result[2] if len(llm_result) > 2 else {}
        is_multimodal_llm = llm_config.get("isMultimodal", False)

        # Get and filter toolsets
        agent_toolsets = agent.get("toolsets", [])
        if chat_query.tools is not None:
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

        # Get and filter attached MCP servers — same `chat_query.tools` filter,
        # applied to MCP tool `fullName`s (mirrors the toolset filter above).
        # Runs for BOTH custom agents (`agent.mcpServers` from the graph) and the
        # assistant/placeholder agent (`agent.mcpServers` from
        # `get_authenticated_mcp_servers`, populated in `get_assistant_agent`).
        # Gated on the `ENABLE_MCP` platform flag: when disabled, this is forced
        # empty so the "LOAD MCP SERVER CONFIGS" block below becomes a no-op and
        # no MCP tool ever reaches the agent loop, regardless of what's attached.
        from app.agents.mcp.service import is_mcp_enabled

        agent_mcp_servers = (
            agent.get("mcpServers", [])
            if await is_mcp_enabled(config_service)
            else []
        )
        if chat_query.tools is not None:
            from app.agents.mcp.service import match_enabled_tools_for_mcp_server

            enabled_tools_set = set(chat_query.tools)
            filtered_mcp_servers = []
            for mcp_server in agent_mcp_servers:
                server_tools = mcp_server.get("tools")
                if server_tools is None:
                    # Assistant/placeholder path (`get_authenticated_mcp_servers`) never
                    # populates "tools" — match selected `mcp_{type}_*` names by type
                    # prefix instead of keeping every authenticated server (which would
                    # let live discovery expose tools the chat filter excluded).
                    matched_tools = match_enabled_tools_for_mcp_server(
                        mcp_server, enabled_tools_set,
                    )
                    if matched_tools:
                        mcp_server_copy = dict(mcp_server)
                        mcp_server_copy["tools"] = matched_tools
                        filtered_mcp_servers.append(mcp_server_copy)
                    continue
                mcp_server_copy = dict(mcp_server)
                filtered_tools = [
                    tool for tool in server_tools
                    if tool.get("fullName") in enabled_tools_set
                ]
                if filtered_tools:
                    mcp_server_copy["tools"] = filtered_tools
                    filtered_mcp_servers.append(mcp_server_copy)
            agent_mcp_servers = filtered_mcp_servers

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
                        f"This service account agent requires the following actions to be configured — "
                        f"{'; '.join(problem_parts)}. "
                        "Please configure the agent's action credentials in Agent Builder (key icon next to each action)."
                    )
                else:
                    error_message = (
                        f"This agent requires the following actions to be set up — "
                        f"{'; '.join(problem_parts)}. "
                        "Please connect your actions in Workspace → Actions before using this agent."
                    )
                logger.info(
                    f"Blocking agent {agent_id} execution "
                    f"({'service account' if is_service_account else f'user {executing_user_id!r}'}): "
                    f"action issue(s) — {'; '.join(problem_parts)}"
                )

                async def _toolset_config_error_stream() -> AsyncGenerator[str, None]:
                    if protocol == "agui":
                        from app.agents.agent_loop.protocol.agui import AGUIEventType, frame

                        evt = frame(AGUIEventType.RUN_ERROR, message=error_message, code="toolset_config_missing")
                        yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'])}\n\n"
                    else:
                        yield f"event: error\ndata: {json.dumps({'message': error_message, 'type': 'toolset_config_missing'})}\n\n"

                return StreamingResponse(_toolset_config_error_stream(), media_type="text/event-stream")

            agent_toolsets = configured_toolsets

        # ============================================================================
        # LOAD MCP SERVER CONFIGS (SECURITY-CRITICAL)
        # ============================================================================
        # Mirrors the toolset config loading immediately above: same
        # `credential_lookup_id` (agentKey for service accounts, executing user
        # otherwise), same hard-block-on-unauthenticated policy. Unlike toolset
        # configs, an MCP server also needs its org-level instance definition
        # (transport/url/authMode) — never stored on the graph node
        # (`_create_mcp_server_edges` avoids secrets there) — so each fetch is a
        # two-step: `get_instance` then `resolve_effective_user_auth`.
        # ============================================================================
        mcp_server_configs: dict[str, dict[str, Any]] = {}  # SENSITIVE: contains credentials

        named_mcp_servers = [m for m in agent_mcp_servers if m.get("instanceId")]
        if named_mcp_servers:
            import asyncio as _asyncio  # noqa: F401 — may not have run yet if named_toolsets was empty above

            from app.agents.mcp import service as mcp_service

            async def _fetch_mcp_server_config(
                mcp_server: dict,
            ) -> tuple[dict, dict[str, Any] | None, dict[str, Any] | None]:
                """Return (mcp_server, instance_or_None, effective_auth) without raising."""
                instance_id = mcp_server["instanceId"]
                try:
                    instance = await mcp_service.get_instance(org_key, instance_id, services["config_service"])
                    if not instance:
                        return mcp_server, None, None
                    effective_auth = await mcp_service.resolve_effective_user_auth(
                        instance, credential_lookup_id, services["config_service"],
                    )
                    return mcp_server, instance, effective_auth
                except Exception as exc:
                    logger.warning(f"Failed to load MCP server config for instance '{instance_id}': {exc}")
                    return mcp_server, None, None

            mcp_fetch_results = await _asyncio.gather(*[_fetch_mcp_server_config(m) for m in named_mcp_servers])

            configured_mcp_servers = []
            missing_mcp_server_display_names: list[str] = []          # instance no longer exists
            unauthenticated_mcp_server_display_names: list[str] = []  # instance exists, auth incomplete

            for mcp_server, instance, effective_auth in mcp_fetch_results:
                instance_id = mcp_server["instanceId"]
                display_name = mcp_server.get("displayName") or mcp_server.get("name") or instance_id

                if instance is None:
                    missing_mcp_server_display_names.append(display_name)
                    logger.warning(f"MCP server instance '{instance_id}' not found for agent {agent_id}.")
                    continue

                if mcp_service.is_effective_auth_authenticated(effective_auth):
                    # `ownerId` is `credential_lookup_id` (mirrors the toolset config
                    # loading above) — the agent-loop runtime's `MCPSessionManager`
                    # needs it to persist a refreshed OAuth token back to the SAME
                    # etcd credential record this was resolved from (see
                    # `app/agents/agent_loop/mcp_session.py`).
                    mcp_server_configs[instance_id] = {
                        "instance": instance, "auth": effective_auth or {}, "ownerId": credential_lookup_id,
                    }
                    configured_mcp_servers.append(mcp_server)
                else:
                    unauthenticated_mcp_server_display_names.append(display_name)
                    cred_owner = f"agent '{agent_id}'" if is_service_account else f"user '{executing_user_id}'"
                    logger.warning(
                        f"MCP server '{display_name}' (instance='{instance_id}') is not authenticated "
                        f"for {cred_owner}."
                    )

            if missing_mcp_server_display_names or unauthenticated_mcp_server_display_names:
                problem_parts = []
                if missing_mcp_server_display_names:
                    missing_list = ", ".join(f"'{n}'" for n in missing_mcp_server_display_names)
                    problem_parts.append(f"not found: {missing_list}")
                if unauthenticated_mcp_server_display_names:
                    unauth_list = ", ".join(f"'{n}'" for n in unauthenticated_mcp_server_display_names)
                    problem_parts.append(f"not authenticated: {unauth_list}")

                if is_service_account:
                    error_message = (
                        f"This service account agent requires the following MCP servers to be configured — "
                        f"{'; '.join(problem_parts)}. "
                        "Please configure the agent's MCP server credentials in Agent Builder."
                    )
                else:
                    error_message = (
                        f"This agent requires the following MCP servers to be set up — "
                        f"{'; '.join(problem_parts)}. "
                        "Please connect them in Workspace → MCP Servers before using this agent."
                    )
                logger.info(
                    f"Blocking agent {agent_id} execution "
                    f"({'service account' if is_service_account else f'user {executing_user_id!r}'}): "
                    f"MCP server issue(s) — {'; '.join(problem_parts)}"
                )

                async def _mcp_server_config_error_stream() -> AsyncGenerator[str, None]:
                    if protocol == "agui":
                        from app.agents.agent_loop.protocol.agui import AGUIEventType, frame

                        evt = frame(AGUIEventType.RUN_ERROR, message=error_message, code="mcp_server_config_missing")
                        yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'])}\n\n"
                    else:
                        yield f"event: error\ndata: {json.dumps({'message': error_message, 'type': 'mcp_server_config_missing'})}\n\n"

                return StreamingResponse(_mcp_server_config_error_stream(), media_type="text/event-stream")

            agent_mcp_servers = configured_mcp_servers

        # Build filters and knowledge from agent's knowledge sources
        agent_knowledge = agent.get("knowledge", [])
        filters = chat_query.filters.copy() if chat_query.filters else {}

        if not chat_query.filters:
            # No explicit filters supplied — derive everything from the agent's knowledge config.
            # Exclude KB-typed entries from apps: they go into filters["kb"] exclusively.
            knowledge_connector_ids = [
                k.get("connectorId") for k in agent_knowledge
                if isinstance(k, dict)
                and k.get("connectorId")
                and (k.get("type") or "").strip().upper() != "KB"
            ]
            kb_ids = _extract_kb_app_ids(agent_knowledge)

            filters = {
                "apps": knowledge_connector_ids,
                "kb": kb_ids,
            }
            logger.info(f"Filters: {filters}")
        else:
            # Explicit filters supplied — override individual keys where provided,
            # but fall back to agent's knowledge for keys that are absent.
            if "apps" not in chat_query.filters or chat_query.filters["apps"] is None:
                # Exclude KB-typed entries from apps — they belong in filters["kb"] only.
                knowledge_connector_ids = [
                    k.get("connectorId") for k in agent_knowledge
                    if isinstance(k, dict)
                    and k.get("connectorId")
                    and (k.get("type") or "").strip().upper() != "KB"
                ]
                filters["apps"] = knowledge_connector_ids

            if "kb" not in chat_query.filters or chat_query.filters["kb"] is None:
                filters["kb"] = _extract_kb_app_ids(agent_knowledge)
            logger.info(f"Filters: {filters}")

        # Apply NO_KB sentinel BEFORE filtering agent_knowledge. When kb is
        # explicitly [] (user deselected all KB sources at runtime), the sentinel
        # ensures filters["kb"] is non-empty so downstream code can distinguish
        # "nothing selected" from "key absent" without needing this function's
        # "keys present but empty → return []" semantics to propagate further.
        if not filters.get("kb") and agent_id != "agentIdPlaceholder":
            filters["kb"] = [NO_KB_SELECTED_FILTER]

        agent_knowledge = _filter_knowledge_by_enabled_sources(agent_knowledge, filters)

        logger.info(f"Filters: {filters}")

        _stream_conn_ids = [
            k["connectorId"] for k in agent_knowledge
            if isinstance(k, dict) and k.get("connectorId")
        ]
        connector_configs = await fetch_connector_configs(config_service, _stream_conn_ids)
        web_search_provider = _parse_web_search(agent.get("webSearch"))
        web_search_tool_config = None
        if web_search_provider:
            web_search_tool_config = await _resolve_web_search_tool_config(
                web_search_provider,
                config_service,
                logger,
            )
        elif agent_id == "agentIdPlaceholder":
            web_search_tool_config = await _resolve_default_web_search_config(
                config_service,
                logger,
            )
        if not _is_web_search_enabled(chat_query.tools):
            web_search_provider = None
            web_search_tool_config = None

        # Apply user-requested capability overrides (capabilities can only narrow,
        # never expand beyond what the agent is configured with).
        caps = _parse_agent_capabilities(chat_query.agentCapabilities)
        if not caps.web_search:
            web_search_provider = None
            web_search_tool_config = None
        if not caps.internal_search:
            filters = {"apps": [], "kb": [NO_KB_SELECTED_FILTER]}
            agent_knowledge = []

        # Universal Agent Mode (agentIdPlaceholder) is still Chat Assistant —
        # Node routes chatMode=agent here, not through /chat/stream. Inject the
        # org-level Agent custom instructions; real Agent Builder IDs skip this.
        is_placeholder = agent_id == "agentIdPlaceholder"
        custom_instructions = (
            resolve_custom_instructions(ai_models_config or {}, resolve_agent_policy(caps))
            if is_placeholder
            else None
        )

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
            "custom_instructions": custom_instructions,
            "timezone": chat_query.timezone,
            "currentTime": chat_query.currentTime,
            "toolsets": agent_toolsets,
            "mcpServers": agent_mcp_servers,
            "mcpServerConfigs": mcp_server_configs,
            "knowledge": agent_knowledge,
            "skills": [s["name"] for s in agent.get("skills", []) if isinstance(s, dict) and s.get("name")] or None,
            "connector_configs": connector_configs,
            "toolsetConfigs": toolset_configs,
            "conversationId": chat_query.conversationId,
            "is_service_account": is_service_account,
            "isPlaceholderAgent": is_placeholder,
            "modelName": model_name,
            "modelKey": model_key,
            "webSearch": web_search_provider,
            "webSearchConfig": web_search_tool_config,
            "attachments": chat_query.attachments,
            "enableRecordIdShortening": chat_query.enableRecordIdShortening,
        }

        client_name = request.headers.get("client-name")

        generator = run_agent_loop_stream(
            query_info,
            enriched_user_info,
            llm,
            logger,
            retrieval_service,
            graph_provider,
            reranker_service,
            config_service,
            org_info,
            model_name=model_name,
            model_key=model_key,
            is_multimodal_llm=is_multimodal_llm,
            client_name=client_name,
            protocol=protocol,
            llm_provider=llm_config.get("provider", ""),
            context_length=llm_config.get("contextLength"),
            is_reasoning_model=bool(llm_config.get("isReasoning", False)),
        )

        return StreamingResponse(
            generator,
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
    from app.agents.mcp.service import get_authenticated_mcp_servers, is_mcp_enabled
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

    # Get authenticated MCP server instances — parallel to toolsets above, no
    # graph attachment required (see `get_authenticated_mcp_servers` docstring).
    # Skipped entirely when MCP is disabled — the chat handler forces
    # `agent_mcp_servers` empty regardless, so this would just be a wasted
    # etcd/graph round-trip on every assistant chat.
    if await is_mcp_enabled(config_service):
        try:
            authenticated_mcp_servers_list = await get_authenticated_mcp_servers(
                owner_id=user_id,
                org_id=org_id,
                config_service=config_service,
            )
        except Exception as e:
            logger.error(f"Error fetching authenticated MCP servers: {e}", exc_info=True)
            authenticated_mcp_servers_list = []
    else:
        authenticated_mcp_servers_list = []

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
                    kn: dict[str, Any] = {
                        "connectorId": kb_id,
                        "name": title,
                        "displayName": title,
                        "type": Connectors.KNOWLEDGE_BASE.value,
                        "filters": {},
                        "filtersParsed": {
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
        "mcpServers": authenticated_mcp_servers_list,
        "knowledge": knowledge_sources,
    }
