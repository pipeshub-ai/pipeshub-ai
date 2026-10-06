"""
Agent API Routes
Handles agent instances, templates, chat, and permissions using graph-based architecture
"""

import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncGenerator, Mapping
from contextlib import aclosing
from logging import Logger
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.agents.agent_loop.cancellation.registry import RunOwner
from app.agents.agent_loop.cancellation.validation import validate_run_id
from app.agents.agent_loop.error_classification import classify_exception
from app.agents.agent_loop.protocol import resolve_protocol
from app.agents.agent_loop.protocol.stream_collector import collect_stream_outcome
from app.agents.agent_loop.stream_bridge import run_agent_loop_stream
from app.agents.chat_modes.custom_instructions import resolve_custom_instructions
from app.agents.chat_modes.policy import AgentCapabilities, resolve_agent_policy
from app.agents.registry.toolset_registry import ToolsetRegistry
from app.api.middlewares.auth import require_scopes, require_service_token
from app.api.routes.chatbot import (
    get_llm_for_chat,
    get_run_cancellation_registry,
    load_entity_vector_store,
    load_system_prompts,
)
from app.config.configuration_service import ConfigurationService
from app.config.constants.ai_models import (
    validate_reasoning_effort,
)
from app.config.constants.arangodb import CollectionNames, Connectors
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import OAuthScopes, TokenScopes, config_node_constants
from app.modules.agents import handles
from app.modules.agents.capability_summary import fetch_connector_configs
from app.modules.agents.collaboration import (
    CollaborationContext,
    MentionRef,
    PreviousConversationTurn,
    ResumeRequest,
    turns_to_dicts,
)
from app.modules.agents.handle_allocator import suggest as suggest_handle
from app.modules.agents.knowledge_scope import (
    NO_KB_SELECTED_FILTER,
    admit_caller_project_collections,
    resolve_agent_filters,
)
from app.modules.agents.qna.router import (
    RouteDecision,  # noqa: F401 - re-exported for backward-compat imports (see below)
)
from app.modules.agents.qna.router import (
    build_capability_context as _build_agent_capability_context,  # noqa: F401
)
from app.modules.agents.qna.router import (
    build_prior_routing_messages as _build_prior_routing_messages,  # noqa: F401
)
from app.modules.agents.readiness import (
    TOOLSET_CONFIG_MISSING_CODE,
    AgentReadiness,
    compute_agent_readiness,
)
from app.modules.agents.service.agent_service import AgentService
from app.modules.agents.service.builders import (  # noqa: F401 - moved; re-exported for existing importers
    _SUPPORTED_WEB_SEARCH_PROVIDERS,
    SPLIT_PATH_EXPECTED_PARTS,
    _create_knowledge_edges,
    _create_mcp_server_edges,
    _create_skill_edges,
    _create_toolset_edges,
    _finish_removing_old_knowledge,
    _format_web_search_for_response,
    _parse_default_reasoning_effort,
    _parse_knowledge_sources,
    _parse_mcp_servers,
    _parse_models,
    _parse_skills,
    _parse_toolsets,
    _parse_web_search,
    _remove_knowledge_nodes,
    sa_forces_org_sharing,
)
from app.modules.agents.service.errors import (
    AgentError,
    AgentNotFoundError,
    InvalidRequestError,
    PermissionDeniedError,
)
from app.modules.agents.service.models import (
    AgentActor,
    AgentOrigin,
    AgentPatch,
    AgentSpec,
    ChatProvenance,
)
from app.modules.transformers.blob_storage import (
    BlobStorage,  # noqa: F401 - re-exported, see above
)
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.telemetry.event_buffer import record_event
from app.telemetry.identity import domain_from_email
from app.utils.aimodels import model_default_reasoning_effort
from app.utils.attachment_utils import (
    resolve_attachments,  # noqa: F401 - re-exported, see above
)
from app.utils.llm import LLM_MISSING_FOR_CHAT
from app.utils.stage_timer import StageTimer
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.user_messages import action_failed

# ``services['logger']`` is bound inside each request, so a handler that failed
# before that needs a module logger with a name it cannot shadow.
_log = logging.getLogger(__name__)

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
    previousConversations: list[PreviousConversationTurn] = []
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
    # Author-set instructions from the Project this conversation is linked
    # to (Node `ProjectService.buildContext`). Additive — rendered as its
    # own prompt section, never merged into the agent's system_prompt/
    # instructions, so a real Agent Builder agent's identity is untouched.
    projectInstructions: str | None = Field(default=None, max_length=8000)
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
    # Stop Generation: client-generated UUID identifying this run, so a
    # later `POST /chat/cancel {runId}` (`chatbot.py` — one endpoint for
    # both assistant and agent runs) can target it.
    runId: str | None = None
    # The chat's ACL version from Node (always present on Node requests).
    # Keys the chat-content PDP allow cache; absent means "do not cache".
    aclVersion: int | None = None
    # Present only when Node sends a multi-participant chat; opaque
    # `participant_<n>` refs only, never user ids (extra='forbid').
    collaboration: CollaborationContext | None = None
    # Set by Node on a follow-up that answers an ask_user_question card.
    resume: ResumeRequest | None = None
    # Roster refs of who this message mentions; never ids (see collaboration/mentions.py).
    mentions: list[MentionRef] = []
    # Set by Node for a project-scoped chat (see `applyProjectScope`,
    # project-context.ts). Threaded into `filters["strictScope"]` below —
    # see `ChatQuery.strictScope` in chatbot.py for the full rationale.
    strictScope: bool = False

    _validate_reasoning_effort = field_validator("reasoningEffort")(validate_reasoning_effort)
    _validate_run_id = field_validator("runId")(validate_run_id)


# ============================================================================
# Custom Exceptions
# ============================================================================





class AgentTemplateNotFoundError(AgentError):
    """Agent template not found"""
    def __init__(self, template_id: str) -> None:
        super().__init__(
            detail=f"Agent template '{template_id}' not found or you don't have access to it",
            status_code=404
        )






class LLMInitializationError(AgentError):
    """LLM initialization failed"""
    def __init__(self) -> None:
        super().__init__(
            detail=LLM_MISSING_FOR_CHAT,
            status_code=500
        )

# ============================================================================
# Helper Functions
# ============================================================================

async def get_services(request: Request) -> dict[str, Any]:
    """Get all required services from container.

    Deliberately resolves no LLM: listing, reading and templating agents never
    use one, and requiring it here made every agent route a 500 until a model
    was configured. Routes that need a model resolve it themselves
    (get_llm_for_chat) and raise LLMInitializationError there.
    """
    container = request.app.container

    retrieval_service = await container.retrieval_service()
    graph_provider = await container.graph_provider()
    reranker_service = container.reranker_service()
    config_service = container.config_service()
    logger = container.logger()

    return {
        "retrieval_service": retrieval_service,
        "graph_provider": graph_provider,
        "reranker_service": reranker_service,
        "config_service": config_service,
        "logger": logger,
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


def _apply_user_context_gate(user_info: dict[str, Any], *, enabled: bool) -> None:
    """When disabled, omit name/email/org from the agent system prompt."""
    if not enabled:
        user_info["sendUserInfo"] = False



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
    caller_org_id: str,
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
    creator_org = str(creator_doc.get("orgId") or "").strip()
    caller_org = str(caller_org_id or "").strip()
    # This branch skips check_agent_permission; empty/mismatched org must not
    # let a caller run retrieval as another tenant's creator.
    if not creator_org or not caller_org or creator_org != caller_org:
        raise AgentNotFoundError(str(agent.get("_key") or creator_key))
    synthetic = {
        "userId": str(creator_user_id),
        "orgId": creator_org,
        "email": (creator_doc.get("email") or "").strip(),
    }
    return await _enrich_user_info(synthetic, creator_doc)


async def _assert_agent_belongs_to_caller_org(
    agent: dict[str, Any],
    graph_provider: IGraphDBProvider,
    caller_org_id: str,
    agent_id: str,
) -> None:
    """404 unless the agent's creator org matches the caller. Used before any
    exists-vs-type distinction so other orgs cannot probe agent keys.

    TODO: persist orgId on the agent instance and backfill existing docs.
    Creator org is the only proxy today; a deleted or moved creator makes
    the agent unreachable to its own org, and each SA chat pays a USERS lookup.
    """
    creator_key = agent.get("createdBy")
    caller_org = str(caller_org_id or "").strip()
    if not creator_key or not caller_org:
        raise AgentNotFoundError(agent_id)
    creator_doc = await graph_provider.get_document(
        str(creator_key), CollectionNames.USERS.value
    )
    creator_org = str((creator_doc or {}).get("orgId") or "").strip()
    if not creator_org or creator_org != caller_org:
        raise AgentNotFoundError(agent_id)


def _agent_service(services: dict[str, Any]) -> AgentService:
    return AgentService(
        graph=services["graph_provider"],
        config=services.get("config_service"),
        logger=services["logger"],
    )


async def _build_agent_actor(user_context: dict[str, Any], services: dict[str, Any]) -> AgentActor:
    user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])
    return AgentActor(
        user_key=user_doc["_key"],
        user_id=user_context["userId"],
        org_id=user_context["orgId"],
    )


def _validate_required_fields(data: dict[str, Any], required_fields: list[str]) -> None:
    """Validate required fields in request data"""
    for field in required_fields:
        if not data.get(field) or not str(data.get(field)).strip():
            raise InvalidRequestError(f"'{field}' is required")












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






async def _resolve_turn_filters(
    *,
    agent_id: str,
    agent_knowledge: list[dict[str, Any]],
    requested_filters: dict[str, Any] | None,
    graph_provider: IGraphDBProvider,
    caller_user_id: str,
    org_id: str,
    logger: Logger,
) -> dict[str, Any]:
    """This turn's source filters, never wider than the agent's knowledge.

    Ids outside it are dropped, except the caller's own project collection
    (see ``admit_caller_project_collections``).
    """
    scope = resolve_agent_filters(
        agent_knowledge,
        requested_filters,
        is_universal_agent=agent_id == "agentIdPlaceholder",
    )
    filters = scope.filters
    dropped_kbs = list(scope.dropped_kb_ids)
    if dropped_kbs:
        admitted = await admit_caller_project_collections(
            graph_provider,
            kb_ids=dropped_kbs,
            caller_user_id=caller_user_id,
            org_id=org_id,
            logger=logger,
        )
        filters["kb"] = [*filters["kb"], *admitted]
        dropped_kbs = [k for k in dropped_kbs if k not in admitted]
    # Info, not warning: a project chat on a saved agent routinely sends the
    # project's other sources, so a drop is normal traffic, not an attack signal.
    if scope.dropped_app_ids or dropped_kbs:
        logger.info(
            "Dropped sources outside the agent's knowledge: agent=%s org=%s caller=%s "
            "apps=%s kb=%s",
            agent_id, org_id, caller_user_id, list(scope.dropped_app_ids), dropped_kbs,
        )
    return filters


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


















def _stored_default_effort(llm_config: dict[str, Any], model_key: str, logger: Logger) -> dict[str, str]:
    try:
        effort = model_default_reasoning_effort(llm_config)
    except ValueError as e:
        logger.warning(f"Ignoring the stored default reasoning effort of model {model_key}: {e}")
        return {}
    return {"defaultReasoningEffort": effort} if effort else {}


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
                    **_stored_default_effort(matching_config, model_key, logger),
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
        parsed = json.loads(body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise InvalidRequestError(
            "Invalid JSON. Check that the request body is valid JSON and try again."
        ) from e
    if not isinstance(parsed, dict):
        raise InvalidRequestError("The request body must be a JSON object, such as {\"name\": \"My agent\"}.")
    return parsed


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
        _log.error(f"Error creating template: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=action_failed("create this template")) from e


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
        _log.error(f"Error getting templates: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=action_failed("load agent templates")) from e


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
        _log.error(f"Error getting template: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=action_failed("load this template")) from e


@router.post("/template/{template_id}/clone", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def clone_agent_template(request: Request, template_id: str) -> JSONResponse:
    """Clone an agent template"""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)
        graph_provider = services["graph_provider"]
        user_doc = await _get_user_document(user_context["userId"], graph_provider, services["logger"])

        # The provider copies any template by key, so the caller's access is checked here.
        if not await graph_provider.get_template(template_id, user_doc["_key"]):
            raise AgentTemplateNotFoundError(template_id)

        # A copy whose owner edge fails must not be left behind, unreachable by anyone.
        cloned_template_id = None
        transaction_id = await graph_provider.begin_transaction(
            read=[CollectionNames.AGENT_TEMPLATES.value],
            write=[CollectionNames.AGENT_TEMPLATES.value, CollectionNames.PERMISSION.value],
        )
        try:
            cloned_template_id = await graph_provider.clone_agent_template(template_id, transaction=transaction_id)
            if not cloned_template_id:
                raise HTTPException(status_code=500, detail="Failed to clone agent template")

            time = get_epoch_timestamp_in_ms()
            owner_access = {
                "_from": f"{CollectionNames.USERS.value}/{user_doc['_key']}",
                "_to": f"{CollectionNames.AGENT_TEMPLATES.value}/{cloned_template_id}",
                "role": "OWNER",
                "type": "USER",
                "createdAtTimestamp": time,
                "updatedAtTimestamp": time,
            }
            if not await graph_provider.batch_create_edges(
                [owner_access], CollectionNames.PERMISSION.value, transaction=transaction_id
            ):
                raise HTTPException(status_code=500, detail="Failed to create template access")
            await graph_provider.commit_transaction(transaction_id)
        except Exception:
            try:
                await graph_provider.rollback_transaction(transaction_id)
            except Exception as rollback_error:
                _log.error(f"Failed to roll back template copy: {rollback_error}")
            # Neo4j without explicit transactions commits each write, so rollback may leave it.
            if cloned_template_id:
                try:
                    if await graph_provider.get_document(cloned_template_id, CollectionNames.AGENT_TEMPLATES.value):
                        await graph_provider.delete_nodes([cloned_template_id], CollectionNames.AGENT_TEMPLATES.value)
                except Exception as cleanup_error:
                    _log.error(f"Failed to remove template copy {cloned_template_id}: {cleanup_error}")
            raise

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
        _log.error(f"Error cloning template: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=action_failed("copy this template")) from e


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
        _log.error(f"Error deleting template: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=action_failed("delete this template")) from e


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
        _log.error(f"Error updating template: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=action_failed("save this template")) from e


# ============================================================================
# Agent CRUD Endpoints
# ============================================================================

async def _create_from_body(
    request: Request,
    *,
    origin: AgentOrigin,
    provenance: ChatProvenance | None = None,
) -> JSONResponse:
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)

        body = _parse_request_body(await request.body())
        _validate_required_fields(body, ["name"])

        actor = await _build_agent_actor(user_context, services)
        try:
            spec = AgentSpec.model_validate(body)
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=action_failed("create this agent")) from e

        created = await _agent_service(services).create(actor, spec, origin=origin, provenance=provenance)

        status = "partial_success" if created.warnings else "success"
        message = (
            f"Agent created with warnings: {len(created.warnings)} attachment(s) failed"
            if created.warnings else "Agent created successfully"
        )
        return JSONResponse(
            status_code=200,
            content={
                "status": status,
                "message": message,
                "agent": created.agent,
                "warnings": created.warnings or None,
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        _log.error(f"Error creating agent: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=action_failed("create this agent")) from e


@router.post("/create", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def create_agent(request: Request) -> JSONResponse:
    """Create a new agent using graph-based architecture. `createdVia` and the source ids in the
    body are never read: only the from-chat route below can set them."""
    return await _create_from_body(request, origin="ui")


@router.post("/internal/create-from-chat")
async def create_agent_from_chat(
    request: Request,
    claims: Mapping[str, Any] = Depends(require_service_token(TokenScopes.AGENT_CREATE_FROM_CHAT)),
) -> JSONResponse:
    """Create an agent from a chat draft. Node mints the token after checking that the draft is
    the caller's, so the conversation and message ids come from the token, never the body."""
    conversation_id, message_id = claims.get("conversationId"), claims.get("messageId")
    if not isinstance(conversation_id, str) or not isinstance(message_id, str) or not conversation_id or not message_id:
        raise HTTPException(status_code=403, detail="Token is not bound to a chat draft")
    provenance = ChatProvenance(conversation_id=conversation_id, message_id=message_id)
    return await _create_from_body(request, origin="chat", provenance=provenance)


@router.get("/handle-availability", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_READ))])
async def check_agent_handle(request: Request, handle: str = Query(..., max_length=100)) -> JSONResponse:
    """Whether `handle` is free in the caller's org. Answers for every agent, including ones the caller cannot see."""
    services = await get_services(request)
    org_id = _get_user_context(request)["orgId"]
    requested = handle.strip().removeprefix("@")
    if not handles.is_valid_format(requested):
        return JSONResponse(content={"available": False, "reason": "invalid"})
    if handles.is_reserved(requested):
        return JSONResponse(content={"available": False, "reason": "reserved"})
    graph = services["graph_provider"]
    if await graph.get_agent_by_handle(org_id, requested) is None:
        return JSONResponse(content={"available": True})
    return JSONResponse(content={
        "available": False,
        "reason": "taken",
        "suggestion": await suggest_handle(graph, org_id, requested),
    })


@router.get(
    "/{agent_id}/internal/service-account",
    dependencies=[
        Depends(
            require_scopes(
                OAuthScopes.AGENT_READ,
                service_scopes=(TokenScopes.CONVERSATION_CREATE,),
            )
        )
    ],
)
async def get_agent_internal(request: Request, agent_id: str) -> JSONResponse:
    """
    Internal route: verify that an agent is a service account and return its
    data.  Called by the Node.js gateway after hydrating a Slack scoped token
    into a regular user JWT (the hydrated user is the org admin, who always has
    access to any org-shared agent).

    Returns 403 if the agent exists in the caller's org but is NOT a service
    account, 404 if the agent is missing or belongs to another org (same 404
    so other orgs cannot probe keys).  Service account agents are always
    org-wide by invariant, so the standard get_agent() permission check will
    pass for the hydrated admin user.
    """
    try:
        services = await get_services(request)

        agent = await services["graph_provider"].get_agent(agent_id)
        if not agent:
            raise AgentNotFoundError(agent_id)

        org_key = _get_user_context(request)["orgId"]
        await _assert_agent_belongs_to_caller_org(
            agent, services["graph_provider"], org_key, agent_id
        )

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
        _log.error("get_agent_internal failed: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=action_failed("load this agent")) from e


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
        _log.error("get_web_search_provider_usage failed: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=action_failed("check where this web search provider is used")) from e


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
        _log.error("get_model_usage failed: %s", e, exc_info=True)
        # Server-side failure (graph DB outage, etc.) — return 500 so callers
        # treat this as a transient backend error and fail-closed on deletion.
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail=action_failed("check where this model is used"),
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
        _log.error(f"Error getting agent: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=action_failed("load this agent")) from e


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
        _log.error(f"Error getting agents: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=action_failed("load your agents")) from e


@router.put("/{agent_id}", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def update_agent(request: Request, agent_id: str) -> JSONResponse:
    """Update an agent using graph-based architecture"""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)

        body = _parse_request_body(await request.body())
        actor = await _build_agent_actor(user_context, services)
        patch = AgentPatch.model_validate(body)

        result = await _agent_service(services).update(actor, agent_id, patch)
        return JSONResponse(status_code=200, content=result)
    except HTTPException:
        raise
    except Exception as e:
        _log.error(f"Error updating agent: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=action_failed("save this agent")) from e

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
        raise HTTPException(status_code=400, detail=action_failed("delete this agent")) from e


# ============================================================================
# Agent Chat Endpoints
# ============================================================================

@router.get(
    "/{agent_id}/readiness",
    response_model=AgentReadiness,
    response_model_by_alias=True,
    dependencies=[
        Depends(
            require_scopes(
                OAuthScopes.AGENT_EXECUTE,
                service_scopes=(TokenScopes.CONVERSATION_CREATE,),
            )
        )
    ],
)
async def get_agent_readiness(request: Request, agent_id: str) -> AgentReadiness:
    """Whether the caller has every toolset of this agent configured and authenticated."""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)
        org_key = user_context["orgId"]
        graph_provider = services["graph_provider"]

        agent = await graph_provider.get_agent(agent_id, org_key)
        if not agent:
            raise AgentNotFoundError(agent_id)
        if agent.get("isServiceAccount", False):
            await _assert_agent_belongs_to_caller_org(agent, graph_provider, org_key, agent_id)
        else:
            user_doc = await _get_user_document(user_context["userId"], graph_provider, services["logger"])
            if not await graph_provider.check_agent_permission(agent_id, user_doc["_key"], org_key):
                raise AgentNotFoundError(agent_id)

        config_service = services["config_service"]
        from app.services.featureflag.platform_settings import is_actions_enabled

        actions_enabled = await is_actions_enabled(config_service)
        return await compute_agent_readiness(
            agent,
            user_context,
            config_service=config_service,
            agent_id=agent_id,
            toolsets=agent.get("toolsets", []) if actions_enabled else [],
        )
    except HTTPException:
        raise
    except Exception as e:
        _log.error("get_agent_readiness failed: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=action_failed("check this agent's readiness")) from e


@router.post("/{agent_id}/chat", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_EXECUTE))])
async def chat(request: Request, agent_id: str) -> JSONResponse:
    """Chat with an agent (non-streaming).

    Runs the exact agent-loop pipeline `chat_stream()` does by invoking that
    route function and draining its `body_iterator` (see `stream_collector`),
    rather than duplicating its setup: that setup is too security-sensitive
    (credential lookup scoping — see its own comments) to risk drifting.

    Called by Node's `POST /api/v1/agents/:agentKey/conversations` and
    `POST /api/v1/agents/:agentKey/conversations/:id/messages`, which persist
    the returned `completion_data` via `saveCompleteConversation`.
    """
    request.state.chat_streaming = False
    streaming_response = await chat_stream(request, agent_id)
    if not isinstance(streaming_response, StreamingResponse):
        return streaming_response  # pragma: no cover - chat_stream() only returns StreamingResponse today

    outcome = await collect_stream_outcome(streaming_response.body_iterator, request.is_disconnected)
    return outcome.to_response()


@router.post(
    "/{agent_id}/chat/stream",
    dependencies=[
        Depends(
            require_scopes(
                OAuthScopes.AGENT_EXECUTE,
                service_scopes=(TokenScopes.CONVERSATION_CREATE,),
            )
        )
    ],
)
async def chat_stream(request: Request, agent_id: str) -> StreamingResponse:
    """Chat with an agent using streaming response"""
    timer = StageTimer()
    try:
        services = await get_services(request)
        logger = services["logger"]

        config_service = services["config_service"]
        graph_provider = services["graph_provider"]
        retrieval_service = services["retrieval_service"]
        reranker_service = services["reranker_service"]
        config_service = services["config_service"]
        # Optional, and resolved here rather than in get_services: it costs a
        # vector-DB round trip, which the list/read/template routes never need.
        entity_vector_store = await load_entity_vector_store(request.app.container, logger)
        user_context = _get_user_context(request)
        org_key = user_context["orgId"]

        timer.mark("services")
        body = _parse_request_body(await request.body())
        chat_query = ChatQuery(**body)
        protocol = _resolve_protocol(chat_query, request)
        logger.debug("chat_stream: resolved protocol=%s (body.protocol=%r, query=%r)",
                     protocol, chat_query.protocol, request.query_params.get("protocol"))

        cancellation_registry = await get_run_cancellation_registry(request)
        # A real HTTP 409 is only possible here, before `StreamingResponse`
        # is returned — once `_run()` starts, the response is already
        # committed to 200. See `RunCancellationRegistry.is_active`.
        if chat_query.runId and await cancellation_registry.is_active(chat_query.runId):
            raise HTTPException(
                status_code=409, detail=f"runId '{chat_query.runId}' is already active",
            )

        record_event("agent_run", {
            "orgId": user_context.get("orgId"),
            "userId": user_context.get("userId"),
            "email": user_context.get("email"),
            "domain": user_context.get("domain"),
            "has_tools": bool(chat_query.tools),
            "streaming": getattr(request.state, "chat_streaming", True),
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

        if agent_id == "agentIdPlaceholder":
            # Lazy: `app.api.routes.toolsets` imports back into this module.
            from app.agents.mcp.service import is_mcp_enabled
            from app.services.featureflag.platform_settings import (
                is_actions_enabled,
                is_user_context_enabled,
            )

            toolset_registry = getattr(request.app.state, "toolset_registry", None)
            # One wave: org lookup, the user document, and platform flags
            # are mutually independent. The flags are resolved here and threaded
            # through because they are deliberately uncached live reads (see
            # `is_actions_enabled`) that `get_assistant_agent` and the toolset/
            # MCP blocks below each used to read again.
            org_info, actions_enabled, mcp_enabled, user_doc, user_context_enabled = await asyncio.gather(
                _get_org_info(user_context, graph_provider, logger),
                is_actions_enabled(config_service),
                is_mcp_enabled(config_service),
                _get_user_document(user_context["userId"], graph_provider, logger),
                is_user_context_enabled(config_service),
            )
            # Built inside the stream below: this is the expensive half of the
            # request (toolset + MCP + knowledge fan-out) and nothing above it
            # needs the result, so it must not delay the response headers.
            agent = None
            prefetched_toolset_auth: dict[str, dict[str, Any]] = {}
            enriched_user_info = await _enrich_user_info(user_context, user_doc)
            _apply_user_context_gate(enriched_user_info, enabled=user_context_enabled)
            perm = {"can_edit": False, "can_share": False, "role": "viewer"}
            is_service_account = False

        else:
            actions_enabled = mcp_enabled = None
            prefetched_toolset_auth: dict[str, dict[str, Any]] = {}
            org_info, agent = await asyncio.gather(
                _get_org_info(user_context, graph_provider, logger),
                services["graph_provider"].get_agent(agent_id, org_key),
            )
            if not agent:
                raise AgentNotFoundError(agent_id)
            is_service_account = agent.get("isServiceAccount", False)

            if is_service_account:
                enriched_user_info = await _enrich_user_info_for_service_account_agent_chat(
                    agent, graph_provider, logger, org_key
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

            _apply_user_context_gate(
                enriched_user_info,
                enabled=bool(agent.get("sendUserContext", True)),
            )

        async def _run() -> AsyncGenerator[str, None]:
            """Everything below the authorization checks. Runs after the
            response headers are already on the wire, so the client sees the
            stream open immediately instead of waiting out the config fan-out.

            Failures in here surface as a terminal SSE error frame rather than
            an HTTP status — the status line is long gone by then. Genuine
            auth/not-found errors are raised before this, in `chat_stream`."""
            nonlocal agent, prefetched_toolset_auth, actions_enabled, mcp_enabled, is_service_account
            try:
                if agent is None:
                    agent, prefetched_toolset_auth = await get_assistant_agent(
                        user_context["userId"], org_key, config_service, graph_provider,
                        toolset_registry, logger,
                        actions_enabled=actions_enabled, mcp_enabled=mcp_enabled, user_doc=user_doc,
                    )
                agent.update(perm)
                timer.mark("authz+agent")

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
                # Independent of each other, and both sit before the first streamed byte.
                llm_result, system_prompts_config = await asyncio.gather(
                    get_llm_for_chat(
                        services["config_service"],
                        model_key,
                        model_name,
                        chat_query.chatMode,
                        reasoning_effort=effective_reasoning_effort,
                    ),
                    load_system_prompts(services["config_service"], logger),
                )
                timer.mark("llm_init")

                if not llm_result:
                    raise LLMInitializationError()

                llm = llm_result[0]
                llm_config = llm_result[1]
                is_multimodal_llm = llm_config.get("isMultimodal", False)

                # Get and filter toolsets — gated on the `ENABLE_ACTIONS` platform flag:
                # when disabled, this is forced empty so `PipesHubToolLoader` (which only
                # loads an external toolset when it appears in `context.agent_toolsets`)
                # loads none of them, regardless of what's attached.
                if actions_enabled is None:
                    from app.services.featureflag.platform_settings import (
                        is_actions_enabled,
                    )

                    actions_enabled = await is_actions_enabled(config_service)
                agent_toolsets = agent.get("toolsets", []) if actions_enabled else []

                # `agent_toolsets` was already resolved above behind the ENABLE_ACTIONS
                # gate — do not re-read it from the agent here, or the gate is lost.
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
                if mcp_enabled is None:
                    from app.agents.mcp.service import is_mcp_enabled

                    mcp_enabled = await is_mcp_enabled(config_service)
                agent_mcp_servers = agent.get("mcpServers", []) if mcp_enabled else []
                if chat_query.tools is not None:
                    from app.agents.mcp.service import (
                        match_enabled_tools_for_mcp_server,
                    )

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

                readiness = await compute_agent_readiness(
                    agent,
                    user_context,
                    config_service=config_service,
                    agent_id=agent_id,
                    toolsets=agent_toolsets,
                    prefetched_auth=prefetched_toolset_auth,
                )
                toolset_configs = readiness.toolset_configs  # SENSITIVE: Contains user/agent credentials

                # Hard-block if ANY toolset is either unconfigured or unauthenticated
                if not readiness.can_send:
                    logger.info(
                        f"Blocking agent {agent_id} execution "
                        f"({'service account' if is_service_account else f'user {executing_user_id!r}'}): "
                        f"missing={readiness.missing_toolsets} unauthenticated={readiness.unauthenticated_toolsets}"
                    )
                    yield _stream_error_frame(protocol, readiness.blocked_message, code=TOOLSET_CONFIG_MISSING_CODE)
                    return

                if readiness.configured_toolsets is not None:
                    agent_toolsets = readiness.configured_toolsets

                timer.mark("toolset_cfg")

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
                    import asyncio as _asyncio

                    from app.agents.mcp import service as mcp_service
                    from app.edition_config import get_mcp_instance_resolved

                    async def _fetch_mcp_server_config(
                        mcp_server: dict,
                    ) -> tuple[dict, dict[str, Any] | None, dict[str, Any] | None]:
                        """Return (mcp_server, instance_or_None, effective_auth) without raising."""
                        instance_id = mcp_server["instanceId"]
                        try:
                            instance = await get_mcp_instance_resolved(instance_id, services["config_service"])
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

                        yield _stream_error_frame(protocol, error_message, code="mcp_server_config_missing")
                        return

                    agent_mcp_servers = configured_mcp_servers

                timer.mark("mcp_cfg")

                agent_knowledge = agent.get("knowledge", [])
                filters = await _resolve_turn_filters(
                    agent_id=agent_id,
                    agent_knowledge=agent_knowledge,
                    requested_filters=chat_query.filters,
                    graph_provider=graph_provider,
                    caller_user_id=user_context.get("userId", ""),
                    org_id=org_key,
                    logger=logger,
                )

                # Apply NO_KB sentinel BEFORE filtering agent_knowledge. When kb is
                # explicitly [] (user deselected all KB sources at runtime), the sentinel
                # ensures filters["kb"] is non-empty so downstream code can distinguish
                # "nothing selected" from "key absent" without needing this function's
                # "keys present but empty → return []" semantics to propagate further.
                if not filters.get("kb") and agent_id != "agentIdPlaceholder":
                    filters["kb"] = [NO_KB_SELECTED_FILTER]

                # A project-scoped chat sets this so an empty effective
                # apps/kb selection stays empty at retrieval time instead of
                # `get_accessible_virtual_record_ids` falling back to
                # "search everything the user can access".
                if chat_query.strictScope:
                    filters["strictScope"] = True

                agent_knowledge = _filter_knowledge_by_enabled_sources(agent_knowledge, filters)

                logger.info(f"Filters: {filters}")

                _stream_conn_ids = [
                    k["connectorId"] for k in agent_knowledge
                    if isinstance(k, dict) and k.get("connectorId")
                ]
                web_search_provider = _parse_web_search(agent.get("webSearch"))
                if web_search_provider:
                    web_search_coro = _resolve_web_search_tool_config(
                        web_search_provider,
                        config_service,
                        logger,
                    )
                elif agent_id == "agentIdPlaceholder":
                    web_search_coro = _resolve_default_web_search_config(
                        config_service,
                        logger,
                    )
                else:
                    web_search_coro = None

                # Overlapped: the connector-config fan-out and the web-search lookup
                # touch different keys and neither feeds the other.
                connector_task = asyncio.ensure_future(
                    fetch_connector_configs(config_service, _stream_conn_ids)
                )
                web_search_tool_config = await web_search_coro if web_search_coro is not None else None
                connector_configs = await connector_task
                timer.mark("connector_cfg")
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
                    resolve_custom_instructions(system_prompts_config, resolve_agent_policy(caps))
                    if is_placeholder
                    else None
                )

                # Build query info
                query_info = {
                    "query": chat_query.query,
                    "limit": chat_query.limit,
                    "messages": [],
                    "previous_conversations": turns_to_dicts(chat_query.previousConversations),
                    "quickMode": chat_query.quickMode,
                    "chatMode": chat_query.chatMode,
                    "retrievalMode": chat_query.retrievalMode,
                    "filters": filters,
                    "systemPrompt": agent.get("systemPrompt"),
                    "instructions": agent.get("instructions"),
                    "custom_instructions": custom_instructions,
                    "projectInstructions": chat_query.projectInstructions,
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
                    "runId": chat_query.runId,
                    "aclVersion": chat_query.aclVersion,
                }
                if chat_query.collaboration is not None:
                    query_info["collaboration"] = chat_query.collaboration.model_dump()
                if chat_query.collaboration is not None and chat_query.mentions:
                    query_info["mentions"] = [m.model_dump() for m in chat_query.mentions]
                if chat_query.resume is not None:
                    query_info["resume"] = chat_query.resume.model_dump()

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
                    stage_timer=timer,
                    cancellation_registry=cancellation_registry,
                    # Service-account agents run retrieval as the agent
                    # creator (enriched_user_info.userId), but the RUN is
                    # owned by the authenticated caller — without this,
                    # cancel() compares the creator's userId against the
                    # caller's and returns 403.
                    cancellation_owner=(
                        RunOwner(
                            user_id=user_context.get("userId", ""),
                            org_id=user_context.get("orgId", ""),
                            conversation_id=chat_query.conversationId,
                        ) if is_service_account else None
                    ),
                    entity_vector_store=entity_vector_store,
                )

                # Close the bridge with this generator so its producer task is
                # cancelled on disconnect, not whenever the bridge is GC'd.
                async with aclosing(generator):
                    async for _evt in generator:
                        yield _evt
            except Exception as exc:
                logger.error(f"Error in chat_stream body: {exc}", exc_info=True)
                error_code, user_message = classify_exception(exc)
                yield _stream_error_frame(protocol, user_message, error_code)

        return StreamingResponse(
            _run(),
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
        _log.error(f"Error in chat_stream: {e}", exc_info=True)
        _, user_message = classify_exception(e)
        raise HTTPException(status_code=400, detail=user_message) from e

def _stream_error_frame(protocol: str, message: str, code: str = "stream_error") -> str:
    """Terminal SSE error frame, in whichever protocol the client asked for.

    Used once the response headers are already sent, where an HTTP status is no
    longer available to carry the failure.
    """
    if protocol == "agui":
        from app.agents.agent_loop.protocol.agui import AGUIEventType, frame

        evt = frame(AGUIEventType.RUN_ERROR, message=message, code=code)
        return f"event: {evt['event']}\ndata: {json.dumps(evt['data'])}\n\n"
    return f"event: error\ndata: {json.dumps({'message': message, 'type': code})}\n\n"


async def get_assistant_agent(
    user_id: str,
    org_id: str,
    config_service: ConfigurationService,
    graph_provider: IGraphDBProvider,
    toolset_registry: ToolsetRegistry,
    logger: Logger,
    *,
    actions_enabled: bool | None = None,
    mcp_enabled: bool | None = None,
    user_doc: dict[str, Any] | None = None,
) -> tuple[dict, dict[str, dict[str, Any]]]:
    """
    Get the assistant agent with all authenticated toolsets and accessible connectors.

    Args:
        user_id: User ID
        org_id: Organization ID
        config_service: Configuration service for etcd access
        graph_provider: Graph provider instance
        toolset_registry: Toolset registry instance
        logger: Logger instance
        actions_enabled / mcp_enabled: pre-resolved platform flags. These are
            deliberately uncached reads (see `is_actions_enabled`), so a caller
            that already resolved one passes it in rather than paying for a
            second live read of the same flag in the same request.
        user_doc: the caller's already-fetched user document, if any — avoids
            repeating `get_user_by_user_id` for the same user in one request.

    Returns:
        ``(agent, toolset_auth_by_instance_id)``. The second element is the
        credential blob per authenticated toolset instance, handed back so the
        chat handler can skip re-reading the identical etcd paths.
    """
    from app.agents.mcp.service import get_authenticated_mcp_servers, is_mcp_enabled
    from app.api.routes.toolsets import get_authenticated_toolsets, is_actions_enabled
    from app.edition_config import resolve_mcp_instances_with_inheritance

    toolset_auth_by_instance: dict[str, dict[str, Any]] = {}

    # Get authenticated toolsets using the helper method — skipped entirely when
    # Actions is disabled (mirrors the MCP gate below): the chat handler forces
    # `agent_toolsets` empty regardless, so this would just be a wasted etcd/graph
    # round-trip on every assistant chat.
    if actions_enabled is None:
        actions_enabled = await is_actions_enabled(config_service)
    if actions_enabled:
        try:
            authenticated_toolsets_list, toolset_auth_by_instance = await get_authenticated_toolsets(
                user_id=user_id,
                org_id=org_id,
                config_service=config_service,
                registry=toolset_registry,
            )
        except Exception as e:
            logger.error(f"Error fetching authenticated toolsets: {e}", exc_info=True)
            authenticated_toolsets_list = []
    else:
        authenticated_toolsets_list = []

    # Get authenticated MCP server instances — parallel to toolsets above, no
    # graph attachment required (see `get_authenticated_mcp_servers` docstring).
    # Skipped entirely when MCP is disabled — the chat handler forces
    # `agent_mcp_servers` empty regardless, so this would just be a wasted
    # etcd/graph round-trip on every assistant chat.
    if mcp_enabled is None:
        mcp_enabled = await is_mcp_enabled(config_service)
    if mcp_enabled:
        try:
            mcp_instances = await resolve_mcp_instances_with_inheritance(config_service)
            authenticated_mcp_servers_list = await get_authenticated_mcp_servers(
                owner_id=user_id,
                config_service=config_service,
                instances=mcp_instances,
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
        user = user_doc or await graph_provider.get_user_by_user_id(user_id=user_id)
        if not user:
            logger.error(f"User not found: {user_id}")
            return {}, toolset_auth_by_instance
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
    }, toolset_auth_by_instance
