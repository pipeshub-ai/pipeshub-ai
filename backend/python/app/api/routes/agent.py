"""
Agent API Routes
Handles agent instances, templates, chat, and permissions using graph-based architecture
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator, Mapping
from contextlib import aclosing
from logging import Logger
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.agents.agent_environment import AgentEnvironmentRefused, agent_environment
from app.agents.agent_gates import (
    GATED_AGENT_NEEDS_HARNESS,
    AgentGatesRefused,
    agent_gates,
    stored_agent_gates,
)
from app.agents.agent_loop.cancellation.registry import RunOwner
from app.agents.agent_loop.cancellation.validation import validate_run_id
from app.agents.agent_loop.error_classification import classify_exception
from app.agents.agent_loop.protocol import resolve_protocol
from app.agents.agent_loop.protocol.stream_collector import collect_stream_outcome
from app.agents.agent_loop.stream_bridge import run_agent_loop_stream
from app.agents.agent_loop.turn_log import log_turn_end
from app.agents.capability_sync import CapabilitySync
from app.agents.chat_modes.custom_instructions import resolve_custom_instructions
from app.agents.chat_modes.policy import AgentCapabilities, resolve_agent_policy
from app.agents.engine_settings import (
    AgentEngineSettings,
    EngineSettingsRefused,
    agent_engine_settings,
)
from app.agents.openapi.service import connected_tool_refs
from app.agents.openapi.store import ApiToolsetStore
from app.agents.portable import (
    ImportCatalog,
    ImportPlan,
    check_drafted_tools,
    excluded_of,
    plan_import,
    portable_agent,
    unlinked_skills,
)
from app.agents.registry.toolset_registry import ToolsetRegistry
from app.agents.role_models import RoleModel, RoleModelsRefused, agent_role_models
from app.agents.run_definition import ASSISTANT_AGENT_KEY, AgentRunDefinition
from app.agents.versions import (
    AgentVersion,
    AgentVersionConflict,
    AgentVersionStore,
    VersionSource,
    agent_definition,
    definition_digest,
    pinned_agent,
)
from app.api.middlewares.auth import (
    connection_limit,
    is_request_admin,
    require_scopes,
    require_service_token,
)
from app.api.middlewares.caller_role import CallerRoleStatus, fetch_caller_role
from app.api.middlewares.request_log import (
    add_request_log_fields,
    bind_request_log_fields,
    mark_request_failure,
    path_log_fields,
)
from app.api.middlewares.token_policy import is_service_token
from app.api.routes.chatbot import (
    get_graph_provider,
    get_llm_for_chat,
    get_run_cancellation_registry,
    load_entity_vector_store,
    load_system_prompts,
    resolved_turn_or_401,
)
from app.api.routes.control_plane_directory import directory_events, owner_team_from
from app.config.ai_models.display import model_display_name
from app.config.configuration_service import ConfigurationService
from app.config.constants.ai_models import (
    validate_reasoning_effort,
)
from app.config.constants.arangodb import CollectionNames, Connectors
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import OAuthScopes, TokenScopes, config_node_constants
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.control_plane.directory.owner_team import OwnerTeamRefused
from app.core.org_context import bound_org_id, current_org_id
from app.harness.agent_deletion import agent_deletion_state
from app.harness.agent_names import MAX_AGENT_KEYS, agent_names, latest_versions
from app.harness.credentials.sources import configured_llm_entries
from app.harness.knowledge.caller import UNRESOLVED_CALLER
from app.harness.legacy.resolved_request import (
    bind_resolved_stages,
    bound_installations,
    release_resolved_stages,
    with_resolved_stages,
)
from app.harness.run_policy.select import engine_profiles
from app.modules.agents.caller_policy import get_agent_caller_policy, guard_agent_route
from app.modules.agents.capability_summary import (
    PARTLY_SHARED_KEY,
    fetch_connector_configs,
)
from app.modules.agents.knowledge_scope import (
    NO_KB_SELECTED_FILTER,
    admit_caller_project_collections,
    project_files_knowledge,
    resolve_agent_filters,
    service_account_knowledge,
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
from app.modules.retrieval.filter_schema import validate_request_filters
from app.modules.retrieval.selection_scope import (
    CONNECTION_LIMIT_TOO_LARGE_MESSAGE,
    SelectionNotReadyError,
    SelectionTooLargeError,
    connection_coverage,
    has_selection,
    prepare_turn_scope,
    selection_error,
    within_connection,
)
from app.modules.transformers.blob_storage import (
    BlobStorage,  # noqa: F401 - re-exported, see above
)
from app.schema.node_validator import SchemaValidationError
from app.services.featureflag.platform_settings import is_agent_harness_on
from app.services.graph_db.common.utils import (
    AGENT_ENGINE_FIELDS,
    AGENT_ENGINE_PROFILE_FIELD,
    AGENT_ENVIRONMENT_FIELD,
    AGENT_GATES_FIELD,
    AGENT_MODEL_ACCOUNT_FIELD,
    AGENT_OWNER_TEAM_FIELD,
    AGENT_ROLE_MODELS_FIELD,
    decode_agent_json_fields,
    encode_agent_json_fields,
)
from app.services.graph_db.interface.graph_db_provider import (
    SELECTION_APPS_FILTER_KEY,
    IGraphDBProvider,
)
from app.services.model_identity.boundary import principal_from_request_user
from app.services.model_identity.runtime import assert_identity_allowed, call_scope
from app.services.model_identity.workload import Workload
from app.telemetry.event_buffer import record_event
from app.telemetry.identity import domain_from_email
from app.telemetry.modules.schema_metrics import record_schema_rejection
from app.utils import error_class
from app.utils.aimodels import model_default_reasoning_effort
from app.utils.attachment_utils import (
    keep_accessible_attachments,
    resolve_attachments,  # noqa: F401 - re-exported, see above
)
from app.utils.llm import LLM_MISSING_FOR_CHAT
from app.utils.logging_fields import log_event
from app.utils.stage_timer import StageTimer
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.user_lookup import get_caller_user, get_org_or_503, get_user_or_503
from app.utils.user_messages import action_failed
from app.web.config import resolve_web_search

if TYPE_CHECKING:
    pass

from app.agents.agent_loop.tool_approvals import agent_credentials_admin_only
from app.modules.agents.chat_extension import get_chat_extension
from app.modules.agents.context.named_sources import picked_source_ids
from app.modules.agents.context.told_files import picked_file_ids
from app.modules.agents.readiness import (
    AgentReadiness,
    compute_agent_readiness,
)
from app.modules.agents.service.agent_service import AgentService, AgentWriteHooks
from app.modules.agents.service.builders import (  # noqa: F401 - moved; re-exported for existing importers
    _SUPPORTED_WEB_SEARCH_PROVIDERS,
    MAX_MCP_SERVERS_PER_AGENT,
    MAX_TOOLS_PER_MCP_SERVER,
    SPLIT_PATH_EXPECTED_PARTS,
    _assert_mcp_servers_exist,
    _assert_no_personal_mcp_servers,
    _assert_no_personal_toolsets,
    _assert_service_account_knowledge,
    _assert_toolsets_visible,
    _attached_personal_instances,
    _attached_personal_toolsets,
    _bind_mcp_attachments_to_instances,
    _checked_api_toolset_tools,
    _checked_service_account_binding,
    _cleanup_failed,
    _create_knowledge_edges,
    _create_mcp_server_edges,
    _create_skill_edges,
    _create_toolset_edges,
    _finish_removing_old_knowledge,
    _format_web_search_for_response,
    _is_live_identity,
    _mcp_servers_for_chat,
    _names_an_instance,
    _parse_default_reasoning_effort,
    _parse_knowledge_sources,
    _parse_mcp_servers,
    _parse_models,
    _parse_skills,
    _parse_toolsets,
    _parse_web_search,
    _read_agent_mcp_attachments,
    _refuse_limit_over_the_cap,
    _remove_knowledge_nodes,
    _remove_mcp_servers,
    _resolve_mcp_attachments,
    _resolve_service_account_binding,
    _rollback_failed,
    _toolset_attachments_unreadable,
    _validate_required_fields,
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
    ChatProvenance,
)

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

# The CRUD routes' ids on every line of the request, its own line included. Per route: the chat routes share this
# router, and the assistant's chat names `agentIdPlaceholder`.
_AGENT_ID_LOG = Depends(path_log_fields(agent_id="agent_id"))
_TEMPLATE_ID_LOG = Depends(path_log_fields(template_id="template_id"))


def _resolve_protocol(chat_query: "ChatQuery", request: Request) -> str:
    """Negotiate the SSE wire protocol for `chat_stream` — see
    `app.agents.agent_loop.protocol.resolve_protocol` (shared with
    `chatbot.py::askAIStream` so both `/chat/stream`-shaped routes
    negotiate identically)."""
    return resolve_protocol(chat_query.protocol, request)


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
    previousConversations: list[dict] = []
    # What the conversation so far read (FG2): its messages' labels joined by Node; absent, nobody vouches for it.
    historyLabel: dict[str, Any] | None = None
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
    # The project those instructions came from (Node `applyProjectScope`): an
    # answer given them is labelled with it, so only people who can open the
    # project read that answer.
    projectId: str | None = None
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
    # Opt-in measurement surface for evaluation clients: streams CUSTOM
    # `retrieval_context` frames naming the records and blocks that reached
    # the model, plus a `run_usage` frame. No effect on the answer.
    includeRetrievalContext: bool = False
    # "short": a search waits at most `KEYWORD_SEARCH_SHORT_WAIT_SECS` for the
    # keyword (grep) pattern match once its semantic results are in, and keeps
    # what it found by then. For callers that would rather answer sooner, such
    # as agents calling chat over MCP. Ignored under HIGH_ACCURACY_MODE.
    # Absent or "full": waits for all of it, as before.
    keywordSearchBudget: Literal["short", "full"] | None = None
    # Stop Generation: client-generated UUID identifying this run, so a
    # later `POST /chat/cancel {runId}` (`chatbot.py` — one endpoint for
    # both assistant and agent runs) can target it.
    runId: str | None = None
    # The chat's ACL version from Node (always present on Node requests).
    # Keys the chat-content access cache; absent means "do not cache".
    aclVersion: int | None = None
    # Set by Node for a project-scoped chat (see `applyProjectScope`,
    # project-context.ts). Threaded into `filters["strictScope"]` below —
    # see `ChatQuery.strictScope` in chatbot.py for the full rationale.
    strictScope: bool = False
    # The person's answer to an approval card (`tool_approvals`): `{approvalId, decision, remember}`.
    toolApproval: dict[str, Any] | None = None
    # Set by Node for a project-scoped chat whose turn selects below app level:
    # the project's own sources (`{"apps": [...]}`), which the selected record
    # groups, folders and records must lie in. Node builds it from the project and
    # never copies it from the client's request. It only narrows the turn: a body
    # that names other sources, or leaves it out, reaches no record beyond the
    # identity the turn retrieves as, as in a chat outside any project. That is the
    # caller, except for a service-account agent, which retrieves as its creator
    # or bound account.
    allowedFilters: dict[str, Any] | None = None

    _validate_reasoning_effort = field_validator("reasoningEffort")(validate_reasoning_effort)
    _validate_run_id = field_validator("runId")(validate_run_id)
    _validate_filters = field_validator("filters")(validate_request_filters)


# ============================================================================
# Custom Exceptions
# ============================================================================
class AgentTemplateNotFoundError(AgentError):
    """Agent template not found"""
    def __init__(self, template_id: str) -> None:
        # The argument stays for the callers; the detail leaves it out, as it may match nothing.
        super().__init__(
            detail="Agent template not found or you don't have access to it",
            status_code=404
        )


class LLMInitializationError(AgentError):
    """LLM initialization failed"""
    def __init__(self) -> None:
        super().__init__(
            detail=LLM_MISSING_FOR_CHAT,
            status_code=500
        )


# The documents a request's own values are written into: a schema rejection there is the caller's mistake. Anywhere
# else (permission edges, toolset and tool nodes) it is a document we built, so our own drift.
_CALLER_DOCUMENTS = frozenset({CollectionNames.AGENT_INSTANCES.value, CollectionNames.AGENT_TEMPLATES.value})


def _route_failure(request: Request, e: BaseException, action: str, *, writes_body: bool = False) -> HTTPException:
    """The HTTPException a route raises ``from e`` when it fails: 400 when the store refused the caller's values (a
    schema rejection of an agent or template document on a route that ``writes_body`` into it, or a cause classed
    ``bad_request``, such as a database data error), else 500. A clone writes only what we stored, so its rejection
    is our drift. The request's line classes it by ``e`` and keeps the store's text off it. A JSON decode error or
    an upstream 4xx in the chain would also be a 400; none reaches these routes (the body is parsed by
    ``_parse_request_body``). Every schema rejection is counted: one of a field we compute is our drift."""
    rejection = error_class.chain_link(e, SchemaValidationError)
    if rejection is not None:
        record_schema_rejection(rejection.collection)
        refused = writes_body and rejection.collection in _CALLER_DOCUMENTS
    else:
        refused = error_class.classify_error(e) == "bad_request"
    http_exc = HTTPException(status_code=400 if refused else 500, detail=action_failed(action))
    mark_request_failure(request, http_exc, reason="schema_rejected" if rejection is not None else None,
                         include_error_text=False)
    return http_exc

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
    config_service = container.config_service()
    logger = container.logger()

    return {
        "retrieval_service": retrieval_service,
        "graph_provider": graph_provider,
        "config_service": config_service,
        "logger": logger,
        "capability_sync": CapabilitySync(config_service),
    }


async def _sync_agent(services: dict[str, Any], org_key: str, agent_key: str) -> None:
    """CPD-7's dual write after an agent's edges changed; a services dict without the notifier syncs nothing."""
    sync = services.get("capability_sync")
    if isinstance(sync, CapabilitySync):
        await sync.agent(org_key, agent_key)


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
        **get_agent_caller_policy().caller_fields(user),
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

    Retrieval ACL stays on the agent creator — only the LLM-visible name/email changes. The requesting user is
    kept as ``callerUserId``: with knowledge publications on it bounds the creator's scope (D21, P2.13).
    """
    requesting_user = user_context.get("userId")
    enriched_user_info["callerUserId"] = (
        requesting_user if isinstance(requesting_user, str) and requesting_user else UNRESOLVED_CALLER
    )
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


async def _service_account_artifact_owner(
    user_context: dict[str, Any], graph_provider: IGraphDBProvider, org_id: str,
) -> str | None:
    """The caller who owns what a service-account agent makes in their chat.

    The agent runs as its creator, but the conversation is the caller's, and so
    are the files made in it. Only a signed-in user of the agent's org can own
    them: a service JWT (the Slack bot's) has no user to grant them to, so
    they stay the creator's, as do those of a caller who is not found.
    """
    if user_context.get("isServiceAccount"):
        return None
    caller_id = user_context.get("userId")
    caller = await get_user_or_503(graph_provider, caller_id)
    if not caller or str(caller.get("orgId") or "") != str(org_id or ""):
        return None
    return caller_id


async def _get_user_document(user_id: str, graph_provider: IGraphDBProvider, logger: Logger) -> dict[str, Any]:
    """Get user document with validation"""
    try:
        user = await get_user_or_503(graph_provider, user_id)
        if not user or not isinstance(user, dict):
            raise HTTPException(status_code=404, detail="User not found")

        # Validate required fields
        if not user.get("email", "").strip():
            raise HTTPException(status_code=400, detail="User email is missing")

        return user
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to retrieve user information") from e


async def _get_org_info(user_info: dict[str, Any], graph_provider: IGraphDBProvider, logger: Logger) -> dict[str, Any]:
    """Get organization information with validation"""
    try:
        org_doc = await get_org_or_503(graph_provider, user_info["orgId"])
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


async def _owner_user_id(agent: dict[str, Any], graph_provider: IGraphDBProvider) -> str | None:
    """The agent creator's user id (``createdBy`` is a graph key); None when it cannot be read."""
    creator_key = agent.get("createdBy")
    if not creator_key:
        return None
    creator = await graph_provider.get_document(str(creator_key), CollectionNames.USERS.value)
    user_id = (creator or {}).get("userId")
    return user_id if isinstance(user_id, str) and user_id else None


async def _only_an_administrator_may_bind(
    request: Request,
    config_service: ConfigurationService,
    detail: str = "Only an administrator can choose the service account an agent searches as.",
) -> None:
    """Refuse a change of binding from anyone who does not administer the org.

    Binding is how an administrator decides what a shared agent may read: the
    account's reach is whatever they granted it, usually by putting it in
    groups. Without this check any signed-in member could point an agent they
    own at any service account in the organisation and ask questions with that
    account's reach, and because a service agent is shared with everyone
    (`sa_forces_org_sharing`), so could every colleague after them. The screen
    that lists these accounts and the one that puts them in groups are both
    administrator-only already; this is the same rule on the route that spends
    what those screens grant.

    The role is the live one Node reports for the caller's own credentials,
    never a flag read off the request. An unconfirmed role is a 503 rather than
    a refusal: "you are not an administrator" is the wrong thing to tell an
    administrator whose edit failed because a lookup timed out.
    """
    role = await fetch_caller_role(request, config_service)
    if role.status is not CallerRoleStatus.VALID:
        raise HTTPException(
            status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
            detail=(
                "We couldn't confirm your role just now, so nothing was saved. "
                "Please try again."
            ),
        )
    if not role.is_admin:
        raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail=detail)


async def _enrich_user_info_for_service_account_agent_chat(
    agent: dict[str, Any],
    graph_provider: IGraphDBProvider,
    logger: Logger,
    caller_org_id: str,
) -> dict[str, Any]:
    """
    The identity a service agent searches knowledge as.

    A service agent is invoked with a synthetic JWT (e.g. the Slack bot), which
    is not a principal the permission graph knows, so retrieval has to run as
    someone. Which someone is what this decides.

    A bound service account, when the agent names one. Its reach is whatever an
    administrator has shared with it, usually through group membership, so what
    the agent can see is something granted rather than inherited.

    Otherwise the agent's creator, which is where every agent made before
    binding existed still stands. A service agent is shared with the whole
    organisation, so what it may search is limited to what is shared with the
    whole organisation: the org's team connectors, never its creator's personal
    ones, and collections only when it is bound (``service_account_knowledge``,
    applied on every run and when the agent is saved).

    Either way the identity must belong to the caller's own organisation. This
    path skips check_agent_permission, so that is checked here instead.
    """
    binding_user_id = agent.get("serviceAccountUserId")
    bound = bool(binding_user_id)
    what = "service account" if bound else "creator"
    identity_key = binding_user_id or agent.get("createdBy")

    if not identity_key:
        raise HTTPException(
            status_code=500,
            detail="Service account agent is missing createdBy; cannot resolve knowledge permissions.",
        )
    # A binding stores the account's `userId`, which is what the
    # service-accounts API speaks; `createdBy` stores the graph node's own key.
    # Two different identifiers, so two different lookups.
    identity_doc = (
        await graph_provider.get_user_by_user_id(str(identity_key), raise_on_error=True)
        if bound
        else await graph_provider.get_document(
            str(identity_key), CollectionNames.USERS.value
        )
    )
    if not identity_doc:
        raise HTTPException(
            status_code=500,
            detail=f"Agent {what} user not found; cannot resolve knowledge permissions.",
        )
    identity_user_id = identity_doc.get("userId")
    if not identity_user_id:
        logger.error(
            "Service account agent %s %s %s has no userId field",
            agent.get("_key"),
            what,
            identity_key,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Agent {what} is missing userId; cannot resolve knowledge permissions.",
        )
    identity_org = str(identity_doc.get("orgId") or "").strip()
    caller_org = str(caller_org_id or "").strip()
    # Empty or mismatched org must not let a caller run retrieval as another
    # tenant's identity. A binding is validated when it is set, but an account
    # can be moved or removed afterwards, so it is checked again on every run.
    if not identity_org or not caller_org or identity_org != caller_org:
        raise AgentNotFoundError(str(agent.get("_key") or identity_key))
    # A binding that is no longer a service account is refused rather than
    # quietly used: whatever it has become, it was not what an administrator
    # chose. Falling back to the creator here would be worse — it would widen
    # the agent's reach at the moment the narrower identity stopped working.
    if bound and identity_doc.get("kind") != "service":
        logger.error(
            "Agent %s is bound to %s, which is no longer a service account",
            agent.get("_key"),
            identity_key,
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "This agent is bound to something that is no longer a service "
                "account. An administrator needs to point it at one again."
            ),
        )
    # Checked on every run, not only when the binding was set: an account can
    # be removed or switched off long after an administrator bound it, and
    # neither reaches here any other way. Falling back to the creator would be
    # worse for the same reason as above — it widens the agent's reach at the
    # moment the narrower identity stopped being allowed.
    if bound and not _is_live_identity(identity_doc):
        # Deleted wins when both are set. Soft-delete only merges `isActive`
        # and leaves `isDisabled` as it found it, so an account switched off
        # and then removed still carries the flag — and telling an
        # administrator to switch it back on would send them after something
        # that cannot work, since the account is gone from the list too.
        switched_off = (
            identity_doc.get("isDisabled") is True
            and identity_doc.get("isActive") is not False
        )
        logger.error(
            "Agent %s is bound to %s, which has been %s",
            agent.get("_key"),
            identity_key,
            "switched off" if switched_off else "deleted",
        )
        raise HTTPException(
            status_code=500,
            detail=(
                # Said separately, because what an administrator should do
                # differs: a switched-off account can be switched back on and
                # this agent works again, a deleted one cannot.
                "This agent is bound to a service account that has been "
                "switched off. Switch it back on, or point the agent at "
                "another one."
                if switched_off
                else "This agent is bound to a service account that has been "
                "deleted. An administrator needs to point it at another one."
            ),
        )
    synthetic = {
        "userId": str(identity_user_id),
        "orgId": identity_org,
        "email": (identity_doc.get("email") or "").strip(),
    }
    return await _enrich_user_info(synthetic, identity_doc)


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


def _agent_service(
    services: dict[str, Any], request: Request | None = None, *, hooks: AgentWriteHooks | None = None,
) -> AgentService:
    """`request` lets the caller bind a service account, once its live role says it administers
    the org; without it no binding can change. `hooks`: what a save of this API checks and adds."""
    return get_chat_extension().agent_service_class()(
        graph=services["graph_provider"],
        config=services.get("config_service"),
        logger=services["logger"],
        admin_check=(
            None if request is None
            # Built lazily: the gate runs only when a binding is actually being set.
            else lambda **kwargs: _only_an_administrator_may_bind(request, services["config_service"], **kwargs)
        ),
        hooks=hooks,
        bind_log_fields=None if request is None else lambda **fields: bind_request_log_fields(request, **fields),
        add_log_fields=None if request is None else lambda **fields: add_request_log_fields(request, **fields),
    )


async def _build_agent_actor(
    user_context: dict[str, Any], services: dict[str, Any], *, is_admin: bool = False,
) -> AgentActor:
    user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])
    return AgentActor(
        user_key=user_doc["_key"],
        user_id=user_context["userId"],
        org_id=user_context["orgId"],
        is_admin=is_admin,
    )


# The fields of an agent edit that change what it can act on.
_TOOL_FIELDS = ("toolsets", "mcpServers", "apiToolsetTools")


async def _administers_for_a_tool_edit(request: Request, services: dict[str, Any], patch: AgentPatch) -> bool:
    """Whether the caller administers the org, asked only of an edit that names an agent's tools in a
    build where only an admin changes a service agent's: the live role Node reports, never a flag on the
    request. A role that can't be confirmed counts as not an admin, so such an edit is refused."""
    if not agent_credentials_admin_only() or not any(f in patch.to_update_body() for f in _TOOL_FIELDS):
        return False
    try:
        role = await fetch_caller_role(request, services.get("config_service"))
    except Exception as e:
        _log.warning("agent.update: the caller's role could not be read: %s", type(e).__name__)
        return False
    return role.status is CallerRoleStatus.VALID and role.is_admin


async def _agent_engine_settings(
    request: Request,
    services: dict[str, Any],
    org_key: str,
    agent_key: str,
    body: dict[str, Any],
    stored: dict[str, Any] | None,
) -> AgentEngineSettings | None:
    """The engine settings this write stores, checked under the org's engine ceiling (AG1); only an org
    admin turns an agent into an engine agent (P2.11e). The ceiling is the engine-profile document's while it is
    the record, else the org's live layer, which this process shares with its inline claimer."""
    from app.api.routes.harness import inline_claimer

    try:
        return await agent_engine_settings(
            org_key,
            agent_key,
            body,
            stored,
            lambda: engine_profiles(services["config_service"], lambda: inline_claimer(request).live_layer()),
            caller_is_admin=is_request_admin(request),
        )
    except EngineSettingsRefused as refused:
        raise AgentError(detail=f"{refused} [{refused.code}]", status_code=refused.status) from None


async def _agent_role_models(
    services: dict[str, Any], agent_key: str, body: dict[str, Any]
) -> dict[str, RoleModel] | None:
    """The role models this write stores (MA1), each a configured LLM; ``None``: the write does not name them."""
    try:
        return await agent_role_models(
            agent_key, body, lambda: configured_llm_entries(services["config_service"], fresh=True)
        )
    except RoleModelsRefused as refused:
        raise AgentError(detail=f"Invalid request: {refused} [{refused.code}]", status_code=400) from None


def _agent_gates(body: dict[str, Any]) -> dict[str, Any] | None:
    """The run gates this write stores (WP-31 D); ``None``: the write does not name them; ``{}``: it clears them."""
    try:
        return agent_gates(body)
    except AgentGatesRefused as refused:
        raise AgentError(detail=f"Invalid request: {refused} [{refused.code}]", status_code=400) from None


def _agent_environment(body: dict[str, Any]) -> dict[str, Any] | None:
    """The environment this write stores (C20.18); ``None``: the write does not name it; ``{}``: it clears it."""
    try:
        return agent_environment(body)
    except AgentEnvironmentRefused as refused:
        raise AgentError(detail=f"Invalid request: {refused} [{refused.code}]", status_code=400) from None


def _is_web_search_enabled(selected_tools: list[str] | None, *, is_universal_agent: bool = False) -> bool:
    """Whether web_search should remain enabled for this request.

    `selected_tools is None` means "all actions", so web_search stays enabled.
    When an explicit tools list is provided, require a web_search entry.

    The universal agent's tool pickers (composer and project) never list web search; its
    Web Search switch (`agentCapabilities.webSearch`) is the control. A narrowed or
    project-scoped tool list there must not switch web search off behind that switch.
    """
    if selected_tools is None or is_universal_agent:
        return True

    for tool in selected_tools:
        tool_name = str(tool).strip().lower()
        if tool_name == "web_search" or tool_name.startswith("web_search."):
            return True
    return False


def _project_sources(allowed_filters: dict[str, Any] | None) -> dict[str, list[str]] | None:
    """The project's sources, which Node sends with a turn that selects below
    app level; None for a turn outside a project."""
    if not isinstance(allowed_filters, dict):
        return None
    return {
        key: [i for i in allowed_filters.get(key) or [] if isinstance(i, str)]
        if isinstance(allowed_filters.get(key), list) else []
        for key in ("apps", "recordGroups", "records")
    }


async def _resolve_turn_filters(
    *,
    agent_id: str,
    agent_knowledge: list[dict[str, Any]],
    requested_filters: dict[str, Any] | None,
    graph_provider: IGraphDBProvider,
    caller_user_id: str,
    org_id: str,
    logger: Logger,
    project_sources: dict[str, list[str]] | None = None,
    connection_sources: dict[str, list[str]] | None = None,
    retrieval_user_id: str | None = None,
    searches_knowledge: bool = True,
    admit_project_collections: bool = True,
) -> dict[str, Any]:
    """This turn's source filters, never wider than the agent's knowledge.

    Ids outside it are dropped, except the caller's own project collection
    (see ``admit_caller_project_collections``) when ``admit_project_collections``:
    an unbound service-account agent searches no collection. A selection below app level
    is resolved once here, as the identity the search runs as, and bounded by
    a saved agent's sources and by the project's.

    Raises:
        SelectionTooLargeError, SelectionNotReadyError: the selection cannot
            be searched as asked.
    """
    is_universal_agent = agent_id == "agentIdPlaceholder"
    scope = resolve_agent_filters(
        agent_knowledge,
        requested_filters,
        is_universal_agent=is_universal_agent,
    )
    filters = scope.filters
    dropped_kbs = list(scope.dropped_kb_ids)
    if dropped_kbs and admit_project_collections:
        admitted = await admit_caller_project_collections(
            graph_provider,
            kb_ids=dropped_kbs,
            caller_user_id=caller_user_id,
            org_id=org_id,
            logger=logger,
        )
        filters["kb"] = [*filters["kb"], *admitted]
        dropped_kbs = [k for k in dropped_kbs if k not in admitted]
    if scope.unreadable_source_ids:
        logger.warning(
            "Knowledge sources of agent %s (org %s) have a stored limit that cannot be read "
            "and are not searched: %s",
            agent_id, org_id, list(scope.unreadable_source_ids),
        )
    # Info, not warning: a project chat on a saved agent routinely sends the
    # project's other sources, so a drop is normal traffic, not an attack signal.
    if scope.dropped_app_ids or dropped_kbs:
        logger.info(
            "Dropped sources outside the agent's knowledge: agent=%s org=%s caller=%s "
            "apps=%s kb=%s",
            agent_id, org_id, caller_user_id, list(scope.dropped_app_ids), dropped_kbs,
        )
    # A turn with internal search switched off never uses the selection, so
    # one that cannot be searched must not fail it. A saved agent is bound to
    # its sources even without a selection, and a connected app to its limit.
    if searches_knowledge and (
        has_selection(filters) or connection_sources is not None or not is_universal_agent
    ):
        agent_sources = None
        if not is_universal_agent:
            # The caller's project collection was admitted above.
            agent_sources = {**scope.sources, "apps": [*scope.sources["apps"], *filters["kb"]]}
        filters = await prepare_turn_scope(
            graph_provider, retrieval_user_id or caller_user_id, org_id, filters,
            agent_sources=agent_sources, project_sources=project_sources,
            connection_sources=connection_sources,
        )
    elif connection_sources is not None:
        filters = within_connection(filters, connection_sources)
    return filters


async def _knowledge_within_connection(
    graph_provider: IGraphDBProvider,
    user_id: str,
    org_id: str,
    agent_knowledge: list[dict[str, Any]],
    connection_sources: dict[str, list[str]] | None,
) -> list[dict[str, Any]]:
    """``agent_knowledge`` as a connected app limited to ``connection_sources``
    may see it: a source the limit names whole as it is, one it reaches only
    through some of its folders or files marked as partly shared, and every
    other source left out. Unchanged without a limit.

    Raises:
        SelectionTooLargeError, PermissionVerificationUnavailableError: see
            ``resolve_selection_scope``.
    """
    if connection_sources is None:
        return agent_knowledge
    whole, partial = await connection_coverage(graph_provider, user_id, org_id, connection_sources)
    kept: list[dict[str, Any]] = []
    for k in agent_knowledge:
        connector_id = k.get("connectorId") if isinstance(k, dict) else None
        if connector_id in whole:
            kept.append(k)
        elif connector_id in partial:
            kept.append({**k, PARTLY_SHARED_KEY: True})
    return kept


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

    # A selection below app level keeps the sources it touches enabled: the
    # tools that work per app (the source catalog, browsing) read this list.
    touched = set(filters.get(SELECTION_APPS_FILTER_KEY) or [])
    enabled_apps = set(filters.get("apps") or []) | touched
    enabled_kb = {
        cid for cid in (filters.get("kb") or [])
        if cid and cid != NO_KB_SELECTED_FILTER
    } | touched

    result: list[dict[str, Any]] = []
    for k in agent_knowledge:
        if not isinstance(k, dict):
            continue
        is_kb = (k.get("type") or "").strip().upper() == "KB"
        enabled_set = enabled_kb if is_kb else enabled_apps
        if k.get("connectorId", "") in enabled_set:
            result.append(k)
    return result


async def _refuse_unapproved_mcp_tools(services: dict[str, Any], mcp_servers_with_tools: dict[str, dict[str, Any]]) -> None:
    """With `MCP_HUB` on, an agent is saved only with the MCP tools the build's admission policy lets it attach
    (D-MCP-5; with no admin approval of MCP tools, every tool): a run pinned on a tool the policy holds could never
    be built, so the attachment is refused here, naming the tools, instead of at the first chat."""
    from app.harness.mcp_hub.settings import hub_enabled
    from app.harness.mcp_hub.tool_admission import mcp_tool_admission
    from harness_core.mcp_hub.errors import ManifestUnavailable, ToolUnapproved

    if not mcp_servers_with_tools or not hub_enabled():
        return
    config_service = services["config_service"]
    admission = mcp_tool_admission()
    if not await admission.requires_admin_approval(bound_org_id() or "", config_service):
        return
    refused: dict[str, list[str]] = {}
    try:
        for instance_id, server in mcp_servers_with_tools.items():
            unapproved = await admission.unapproved(config_service, instance_id, [t["name"] for t in server["tools"]])
            if unapproved:
                refused[instance_id] = unapproved
    except ManifestUnavailable as e:
        raise HTTPException(
            status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
            detail={"reasonCode": e.reason_code, "message": "The approved MCP tools could not be read. Try again in a moment."},
        ) from e
    if refused:
        named = "; ".join(
            f"{mcp_servers_with_tools[i]['displayName']}: {', '.join(tools)}" for i, tools in sorted(refused.items())
        )
        raise HTTPException(
            status_code=HttpStatusCode.BAD_REQUEST.value,
            detail={
                "reasonCode": ToolUnapproved.reason_code,
                "message": f"These MCP tools are not approved yet and cannot be attached: {named}.",
                "tools": refused,
            },
        )


def _stored_default_effort(llm_config: dict[str, Any], model_key: str, logger: Logger) -> dict[str, str]:
    try:
        effort = model_default_reasoning_effort(llm_config)
    except ValueError as e:
        log_event(logger, "agent.model.default_effort_ignored", model_key=model_key, error_type=type(e).__name__)
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
                    "modelFriendlyName": model_display_name(matching_config, model_name),
                    **_stored_default_effort(matching_config, model_key, logger),
                })
            else:
                log_event(logger, "agent.model.unknown", level=logging.DEBUG, model_key=model_key)
                enriched_models.append({
                    "modelKey": model_key,
                    "modelName": model_name or model_key,
                    "provider": "unknown",
                    "isReasoning": False,
                    "isMultimodal": False,
                    "isDefault": False,
                    "modelType": "llm",
                    "modelFriendlyName": model_display_name(None, model_name),
                })

        agent["models"] = enriched_models
    except Exception as e:
        error_class.log_failure(logger, "agent.route.failed", e, op="enrich_models", include_error_text=False)
# The base implementations, under names nothing rebinds. An edition may replace
# get_services, _enrich_agent_models and sa_forces_org_sharing when it loads
# (handlers look them up as module globals at call time); tests of the base
# behaviour pin these back.
_base_get_services = get_services
_base_enrich_agent_models = _enrich_agent_models
_base_sa_forces_org_sharing = sa_forces_org_sharing


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


async def agent_for_chat(
    agent: dict[str, Any],
    graph_provider: IGraphDBProvider,
    org_key: str,
    agent_id: str,
    config_service: ConfigurationService | None,
    *,
    resolved: bool = False,
) -> dict[str, Any]:
    """The definition an agent chat runs: the latest published version (OWD-14), not an unpublished draft. Where the
    agent harness is off for the org it is the saved agent, as a release without versions ran (ADR-OPS-08). Node
    resolves a turn (``X-PipesHub-Resolved``) only for an org with the harness on, so a resolved turn reads no
    setting for it."""
    if not resolved and not await is_agent_harness_on(config_service):
        return agent
    return pinned_agent(agent, await AgentVersionStore(graph_provider).current(org_key, agent_id, agent))


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
        _validate_required_fields(body, ["name", "description", "systemPrompt"], ("startMessage",))

        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])
        time = get_epoch_timestamp_in_ms()
        template_key = str(uuid.uuid4())
        bind_request_log_fields(request, template_id=template_key)

        template = {
            "_key": template_key,
            "name": body["name"].strip(),
            "description": body["description"].strip(),
            "startMessage": (body.get("startMessage") or "").strip() or "Hello! How can I help you today?",
            "systemPrompt": body["systemPrompt"].strip(),
            "tools": body.get("tools", []),
            "models": body.get("models", []),
            "memory": body["memory"] if body.get("memory") is not None else {"type": []},
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

        try:
            result = await services["graph_provider"].batch_upsert_nodes([template], CollectionNames.AGENT_TEMPLATES.value)
        except SchemaValidationError as e:
            # The body carried a value the template schema refuses: the caller's mistake, and retrying cannot help.
            # Only the field path goes back; the collection and schema detail stay out of the answer.
            record_schema_rejection(e.collection)
            err = e.original_error
            path = ".".join(str(p) for p in err.path) if err is not None and getattr(err, "path", None) else ""
            rejected = HTTPException(status_code=400, detail=f"Invalid agent template: {path}" if path else "Invalid agent template")
            mark_request_failure(request, rejected, reason="schema_rejected")
            raise rejected from e
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
        raise _route_failure(request, e, "create this template", writes_body=True) from e


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
        raise _route_failure(request, e, "load agent templates") from e


@router.get("/template/{template_id}", dependencies=[_TEMPLATE_ID_LOG, Depends(require_scopes(OAuthScopes.AGENT_READ))])
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
        raise _route_failure(request, e, "load this template") from e


@router.post("/template/{template_id}/clone", dependencies=[_TEMPLATE_ID_LOG, Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
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
                _rollback_failed(_log, rollback_error)
            # Neo4j without explicit transactions commits each write, so rollback may leave it.
            if cloned_template_id:
                try:
                    if await graph_provider.get_document(cloned_template_id, CollectionNames.AGENT_TEMPLATES.value):
                        await graph_provider.delete_nodes([cloned_template_id], CollectionNames.AGENT_TEMPLATES.value)
                except Exception as cleanup_error:
                    _cleanup_failed(_log, cleanup_error, template_id=cloned_template_id)
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
        raise _route_failure(request, e, "copy this template") from e


@router.delete("/template/{template_id}", dependencies=[_TEMPLATE_ID_LOG, Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def delete_agent_template(request: Request, template_id: str) -> JSONResponse:
    """Delete an agent template"""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)

        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])
        template = await services["graph_provider"].get_template(template_id, user_doc["_key"])
        if not template:
            raise AgentTemplateNotFoundError(template_id)
        if not template.get("can_delete", False):
            raise PermissionDeniedError("delete this template (only owner can delete)")

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
        raise _route_failure(request, e, "delete this template") from e


@router.put("/template/{template_id}", dependencies=[_TEMPLATE_ID_LOG, Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def update_agent_template(request: Request, template_id: str) -> JSONResponse:
    """Update an agent template"""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)

        body = _parse_request_body(await request.body())
        user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])
        if "memory" in body and body["memory"] is None:
            body["memory"] = {"type": []}

        template = await services["graph_provider"].get_template(template_id, user_doc["_key"])
        if not template:
            raise AgentTemplateNotFoundError(template_id)
        if not template.get("can_edit", False):
            raise PermissionDeniedError("edit this template (only owner can edit)")

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
        raise _route_failure(request, e, "save this template", writes_body=True) from e


# ============================================================================
# Agent CRUD Endpoints
# ============================================================================

async def _create_content(
    request: Request,
    *,
    origin: AgentOrigin,
    provenance: ChatProvenance | None = None,
    planned: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Creates the agent ``planned`` describes, else the request's body, through the agent service, and makes it
    its first version. Answers the create route's response."""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)

        body = planned if planned is not None else _parse_request_body(await request.body())
        _validate_required_fields(body, ["name"], ("description", "startMessage", "systemPrompt", "instructions"))

        actor = await _build_agent_actor(user_context, services)
        agent_service = _agent_service(
            services, request, hooks=_write_hooks(request, services, body, actor.user_key),
        )
        try:
            spec = agent_service.spec_model.model_validate(body)
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=action_failed("create this agent")) from e

        created = await agent_service.create(actor, spec, origin=origin, provenance=provenance)
        bind_request_log_fields(request, agent_id=created.agent_key)
        org_key = actor.org_id
        graph_provider = services["graph_provider"]
        await _sync_agent(services, org_key, created.agent_key)
        try:
            created_agent = await graph_provider.get_agent(created.agent_key, org_key)
            if created_agent:
                await AgentVersionStore(graph_provider).publish(
                    org_key, created.agent_key, created_agent, published_by=user_context["userId"],
                )
        except Exception as e:
            # Its first run or read makes version 1 instead (AgentVersionStore.current).
            error_class.log_failure(services["logger"], "agent.route.failed", e, op="first_version", include_error_text=False)

        response_agent = created.agent
        decode_agent_json_fields(response_agent)
        status = "partial_success" if created.warnings else "success"
        message = (
            f"Agent created with warnings: {len(created.warnings)} attachment(s) failed"
            if created.warnings else "Agent created successfully"
        )
        return {
            "status": status,
            "message": message,
            "agent": response_agent,
            "warnings": created.warnings or None,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise _route_failure(request, e, "create this agent", writes_body=True) from e


async def _create_from_body(
    request: Request,
    *,
    origin: AgentOrigin,
    provenance: ChatProvenance | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=200, content=await _create_content(request, origin=origin, provenance=provenance),
    )


def _write_hooks(
    request: Request, services: dict[str, Any], body: dict[str, Any], user_key: str,
) -> AgentWriteHooks:
    """What this API checks and adds around a save of ``body``: the MCP tools the build lets an agent attach, and
    the engine settings, role models, run gates, environment and owner team the body asks for."""

    async def check_mcp_tools(mcp_servers_with_tools: dict[str, dict[str, Any]]) -> None:
        await _refuse_unapproved_mcp_tools(services, mcp_servers_with_tools)

    async def document_fields(org_key: str, agent_key: str, stored: dict[str, Any] | None) -> dict[str, Any]:
        return await _agent_document_fields(request, services, org_key, agent_key, body, stored, user_key)

    return AgentWriteHooks(check_mcp_tools=check_mcp_tools, document_fields=document_fields)


async def _agent_document_fields(
    request: Request,
    services: dict[str, Any],
    org_key: str,
    agent_key: str,
    body: dict[str, Any],
    stored: dict[str, Any] | None,
    user_key: str,
) -> dict[str, Any]:
    """The agent document fields a save of ``body`` sets: its engine settings, role models, run gates, environment
    and owner team, each checked. On a create (``stored`` None) only what the body sets, in stored form; on an
    update each field the body names, where an empty value clears the stored one."""
    fields: dict[str, Any] = {}
    engine_settings = await _agent_engine_settings(request, services, org_key, agent_key, body, stored)
    role_models = await _agent_role_models(services, agent_key, body)
    gates = _agent_gates(body)
    environment = _agent_environment(body)
    if stored is None:
        if engine_settings is not None and engine_settings.engine_profile is not None:
            fields[AGENT_ENGINE_PROFILE_FIELD] = engine_settings.engine_profile
            if engine_settings.model_account_id is not None:
                fields[AGENT_MODEL_ACCOUNT_FIELD] = engine_settings.model_account_id
        if role_models:
            fields[AGENT_ROLE_MODELS_FIELD] = role_models
        if gates:
            fields[AGENT_GATES_FIELD] = gates
        if environment:
            fields[AGENT_ENVIRONMENT_FIELD] = environment
        encode_agent_json_fields(fields)
        owner_team = await _owner_team(request, body, services, org_key, user_key)
        if owner_team is not None:
            fields[AGENT_OWNER_TEAM_FIELD] = owner_team
        return fields
    if engine_settings is not None:
        fields.update(engine_settings.document_fields())
    if role_models is not None:
        # An empty map clears the stored one: every sub-agent runs on its parent's model again.
        fields[AGENT_ROLE_MODELS_FIELD] = role_models or None
    if gates is not None:
        # An empty object clears them: the agent's runs are held to no gate of its own again.
        fields[AGENT_GATES_FIELD] = gates or None
    if environment is not None:
        # An empty object clears it: the agent's engine runs set nothing up again.
        fields[AGENT_ENVIRONMENT_FIELD] = environment or None
    if AGENT_OWNER_TEAM_FIELD in body:
        fields[AGENT_OWNER_TEAM_FIELD] = await _owner_team(request, body, services, org_key, user_key)
    return fields


async def _owner_team(
    request: Request, body: dict[str, Any], services: dict[str, Any], org_key: str, user_key: str
) -> str | None:
    """The ``ownerTeam`` a create or update asks for (CPD-9): a team of the org, given by an org admin or its OWNER."""
    try:
        return await owner_team_from(
            request, body.get(AGENT_OWNER_TEAM_FIELD), graph=services["graph_provider"], org_id=org_key, user_key=user_key
        )
    except OwnerTeamRefused as exc:
        if exc.reason_code == "owner_team_forbidden":
            raise PermissionDeniedError("make this team the agent's owner team") from exc
        raise InvalidRequestError(str(exc)) from exc


async def _create_agent(request: Request, planned: dict[str, Any] | None) -> dict[str, Any]:
    """Creates the agent ``planned`` describes (an import's body), else the request's body: owned by the caller,
    in the caller's org, whatever the body says. Answers the create route's response."""
    return await _create_content(request, origin="ui", planned=planned)


@router.post("/create", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def create_agent(request: Request) -> JSONResponse:
    """Create a new agent using graph-based architecture."""
    return await _create_from_body(request, origin="ui")


@router.get(
    "/{agent_id}/internal/service-account",
    dependencies=[
        _AGENT_ID_LOG,
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
        raise _route_failure(request, e, "load this agent") from e


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
        raise _route_failure(request, e, "check where this web search provider is used") from e


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
        raise _route_failure(request, e, "check where this model is used") from e


# agent:write is admitted too: the gateway may read the agent, as the caller, to decide which
# permission an update or delete needs, and a token allowed to change an agent may read it.
@router.get(
    "/{agent_id}",
    dependencies=[_AGENT_ID_LOG, Depends(require_scopes(OAuthScopes.AGENT_READ, OAuthScopes.AGENT_WRITE))],
)
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
        try:
            versioning = await _versioning(AgentVersionStore(services["graph_provider"]), org_key, agent_id, agent)
        except Exception as e:
            # The builder still opens the draft; only its version summary is missing.
            services["logger"].warning(f"Versions of agent {agent_id} could not be read: {e}")
            versioning = None

        agent.update(perm)
        if versioning is not None:
            agent["versioning"] = versioning
        from app.edition_services import skills_shown_with_an_agent

        agent["skills"] = await skills_shown_with_an_agent(
            agent.get("skills") or [], org_key, user_doc["_key"], services["graph_provider"],
        )

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
        raise _route_failure(request, e, "load this agent") from e


async def _versioning(store: AgentVersionStore, org_key: str, agent_id: str, draft: dict[str, Any]) -> dict[str, Any]:
    """The builder's view of an agent's versions: the latest one, and whether the draft differs from it."""
    latest = await store.current(org_key, agent_id, draft)
    return {
        "latestVersion": latest.version,
        "latestDigest": latest.digest,
        "unpublishedChanges": definition_digest(agent_definition(draft)) != latest.digest,
    }


async def _publish_draft(
    request: Request, services: dict[str, Any], org_key: str, agent_id: str, publisher: str, note: object,
) -> tuple[AgentVersion, bool]:
    """Freezes the agent as it is saved now as its next version; a draft equal to the latest publishes nothing."""
    graph = services["graph_provider"]
    draft = await graph.get_agent(agent_id, org_key)
    if not draft:
        raise AgentNotFoundError(agent_id)
    version, created = await AgentVersionStore(graph).publish(org_key, agent_id, draft, published_by=publisher, note=note)
    if created:
        await directory_events(request).agent_changed(org_key, agent_id, get_epoch_timestamp_in_ms())
    return version, created


async def _agent_access(request: Request, agent_id: str, *, edit: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    """The caller's services and context once they may read (or, with ``edit``, change) the agent; else 404/403."""
    services = await get_services(request)
    user_context = _get_user_context(request)
    user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], services["logger"])
    perm = await services["graph_provider"].check_agent_permission(agent_id, user_doc["_key"], current_org_id())
    if not perm:
        raise AgentNotFoundError(agent_id)
    if edit and not perm.get("can_edit", False):
        raise PermissionDeniedError("publish this agent (only its editors can)")
    return services, user_context


def _version_note(body: bytes) -> object:
    return _parse_request_body(body).get("note") if body else None


def _version_conflict() -> HTTPException:
    return HTTPException(status_code=409, detail="Another version was published at the same moment. Try again.")


@router.get("/{agent_id}/versions", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_READ))])
async def list_agent_versions(request: Request, agent_id: str) -> JSONResponse:
    """An agent's published versions, newest first, without their definitions; and whether its draft differs."""
    try:
        services, user_context = await _agent_access(request, agent_id, edit=False)
        org_key = current_org_id()
        graph = services["graph_provider"]
        draft = await graph.get_agent(agent_id, org_key)
        if not draft:
            raise AgentNotFoundError(agent_id)
        store = AgentVersionStore(graph)
        versioning = await _versioning(store, org_key, agent_id, draft)
        versions = await store.history(org_key, agent_id)
        return JSONResponse(
            status_code=200,
            content={"versions": [v.summary() for v in versions], **versioning},
        )
    except HTTPException:
        raise
    except Exception as e:
        _log.error(f"Error listing agent versions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=action_failed("load this agent's versions")) from e


@router.get("/{agent_id}/versions/{version}", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_READ))])
async def get_agent_version(request: Request, agent_id: str, version: int) -> JSONResponse:
    """One published version with the definition it froze."""
    try:
        services, user_context = await _agent_access(request, agent_id, edit=False)
        found = await AgentVersionStore(services["graph_provider"]).get(current_org_id(), agent_id, version)
        if found is None:
            raise HTTPException(status_code=404, detail="This agent has no such version")
        return JSONResponse(status_code=200, content={"version": found.wire()})
    except HTTPException:
        raise
    except Exception as e:
        _log.error(f"Error reading agent version: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=action_failed("load this version")) from e


@router.post("/{agent_id}/versions", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def publish_agent_version(request: Request, agent_id: str) -> JSONResponse:
    """Publishes the draft as the agent's next version (OWD-14). Chats run it at once; a schedule keeps the
    version it pinned until its owner moves it. Nothing to publish answers the latest version, 200."""
    try:
        services, user_context = await _agent_access(request, agent_id, edit=True)
        version, created = await _publish_draft(
            request, services, current_org_id(), agent_id, user_context["userId"],
            _version_note(await request.body()),
        )
        return JSONResponse(
            status_code=201 if created else 200,
            content={"version": version.summary(), "created": created},
        )
    except AgentVersionConflict as e:
        raise _version_conflict() from e
    except HTTPException:
        raise
    except Exception as e:
        _log.error(f"Error publishing agent: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=action_failed("publish this agent")) from e


# What a rollback writes back over the draft: everything a version froze that a save can set. Owner, sharing,
# deletion and the owner team are the agent's identity, never part of a version, and are left as they are.
_DRAFT_SCALAR_FIELDS = (
    "name", "description", "startMessage", "systemPrompt", "instructions", "tags", "sendUserContext",
    "webSearch", "defaultReasoningEffort",
)
_DRAFT_LINKED_FIELDS = ("toolsets", "mcpServers", "knowledge", "skills")


async def _draft_body(services: dict[str, Any], definition: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    """A save body that makes the draft ``definition``: every field it sets is named, and a field the draft has
    but the version does not is cleared, so nothing of the draft survives. Linked items are replaced whole."""
    body: dict[str, Any] = {
        field: definition.get(field) for field in _DRAFT_SCALAR_FIELDS if field in definition or field in draft
    }
    for field in _DRAFT_LINKED_FIELDS:
        body[field] = list(definition.get(field) or [])
    # The save path wants each model with whether it reasons, as the builder sends it; a version stores keys.
    models = {"models": list(definition.get("models") or [])}
    await _enrich_agent_models(models, services["config_service"], services["logger"])
    body["models"] = models["models"]
    # Engine settings and role models are read and checked only when one side has them (a loop agent reads no
    # engine ceiling); null and an empty map clear them.
    for field in AGENT_ENGINE_FIELDS:
        if definition.get(field) is not None or draft.get(field) is not None:
            body[field] = definition.get(field)
    if definition.get(AGENT_ROLE_MODELS_FIELD) or draft.get(AGENT_ROLE_MODELS_FIELD):
        body[AGENT_ROLE_MODELS_FIELD] = definition.get(AGENT_ROLE_MODELS_FIELD) or {}
    if definition.get(AGENT_GATES_FIELD) or draft.get(AGENT_GATES_FIELD):
        body[AGENT_GATES_FIELD] = definition.get(AGENT_GATES_FIELD) or {}
    if definition.get(AGENT_ENVIRONMENT_FIELD) or draft.get(AGENT_ENVIRONMENT_FIELD):
        body[AGENT_ENVIRONMENT_FIELD] = definition.get(AGENT_ENVIRONMENT_FIELD) or {}
    return body


@router.post(
    "/{agent_id}/versions/{version}/rollback", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))]
)
async def rollback_agent_version(request: Request, agent_id: str, version: int) -> JSONResponse:
    """Rolls the agent back to ``version``: the draft is rewritten to that version's
    definition, discarding unpublished changes, and then published as the next version. History only grows, and
    runs that recorded a later version still name what they ran.

    The draft is written first and published only once written, from the draft as it reads back, so the live
    version never moves ahead of the draft and a later save cannot bring discarded edits back. A failure part-way
    leaves the live version as it was; the rollback rewrites whole sections, so trying again finishes it."""
    try:
        services, user_context = await _agent_access(request, agent_id, edit=True)
        org_key = current_org_id()
        graph = services["graph_provider"]
        store = AgentVersionStore(graph)
        target = await store.get(org_key, agent_id, version)
        if target is None:
            raise HTTPException(status_code=404, detail="This agent has no such version")
        draft = await graph.get_agent(agent_id, org_key)
        if not draft:
            raise AgentNotFoundError(agent_id)
        latest = await store.current(org_key, agent_id, draft)
        discarded = definition_digest(agent_definition(draft)) != latest.digest
        note = _version_note(await request.body())
        try:
            await _write_draft(request, agent_id, await _draft_body(services, target.definition, draft))
        except HTTPException:
            raise
        except Exception as e:
            _log.error(f"Rolling agent {agent_id} back to version {version} failed writing its draft: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail="The rollback did not finish and the live version is unchanged. Try again.",
            ) from e
        rewritten = await graph.get_agent(agent_id, org_key)
        if not rewritten:
            raise AgentNotFoundError(agent_id)
        published, created = await store.publish(
            org_key, agent_id, rewritten,
            published_by=user_context["userId"], note=note,
            source=VersionSource.ROLLBACK, rolled_back_from=version,
        )
        if created:
            await directory_events(request).agent_changed(org_key, agent_id, get_epoch_timestamp_in_ms())
        return JSONResponse(
            status_code=201 if created else 200,
            content={
                "version": published.summary(),
                "created": created,
                # The draft's edits that were never published, now gone, so a later save cannot bring them back.
                "discardedUnpublishedChanges": discarded,
            },
        )
    except AgentVersionConflict as e:
        raise _version_conflict() from e
    except HTTPException:
        raise
    except Exception as e:
        _log.error(f"Error rolling back agent: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=action_failed("roll back this agent")) from e


def _first_problem(error: ValidationError) -> str:
    first = error.errors()[0]
    where = ".".join(str(part) for part in first.get("loc", ())) or "document"
    return f"{where}: {first.get('msg', 'not valid')}"


async def _exported_definition(
    store: AgentVersionStore, org_key: str, agent_id: str, draft: dict[str, Any], version: str | None,
) -> tuple[dict[str, Any], int | None, str | None]:
    """The definition an export carries: the latest published version by default, a named one, or the draft."""
    if version == "draft":
        return agent_definition(draft), None, None
    if version is None:
        found = await store.current(org_key, agent_id, draft)
    else:
        if not version.isdigit() or int(version) < 1:
            raise InvalidRequestError("version is a published version number or 'draft'")
        found = await store.get(org_key, agent_id, int(version))
        if found is None:
            raise HTTPException(status_code=404, detail="This agent has no such version")
    return found.definition, found.version, found.digest


@router.get("/{agent_id}/export", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_READ))])
async def export_agent(request: Request, agent_id: str, version: str | None = None) -> JSONResponse:
    """The agent in its portable form (C4.08), for whoever may read it: every reference by name, no id, credential
    or sharing. Node wraps it with the agent's policy layer into the export document."""
    try:
        services, user_context = await _agent_access(request, agent_id, edit=False)
        org_key = current_org_id()
        graph = services["graph_provider"]
        draft = await graph.get_agent(agent_id, org_key)
        if not draft:
            raise AgentNotFoundError(agent_id)
        definition, number, digest = await _exported_definition(
            AgentVersionStore(graph), org_key, agent_id, draft, version,
        )
        llm_entries = await configured_llm_entries(services["config_service"], fresh=True)
        try:
            agent = portable_agent(definition, llm_entries)
        except ValueError as e:
            problem = _first_problem(e) if isinstance(e, ValidationError) else str(e)
            raise HTTPException(
                status_code=422, detail=f"This agent's settings cannot be exported ({problem})",
            ) from e
        return JSONResponse(status_code=200, content={
            "agent": agent,
            "source": {"agentVersion": number, "digest": digest},
            "excluded": excluded_of(draft, definition),
        })
    except HTTPException:
        raise
    except Exception as e:
        _log.error(f"Error exporting agent: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=action_failed("export this agent")) from e


async def _import_catalog(services: dict[str, Any], org_key: str, user_id: str) -> ImportCatalog:
    """What the importer can bind to: the configured models, and the actions, MCP servers and sources they see.
    A store that cannot be read fails the import rather than reading as 'nothing here'."""
    from app.agents.mcp.service import load_visible_instances
    from app.agents.toolset_store import load_user_instances
    from app.edition_config import load_instances_for_mutation

    config = services["config_service"]
    llm_entries, org_toolsets, own_toolsets, mcp_instances, connectors = await asyncio.gather(
        configured_llm_entries(config, fresh=True),
        load_instances_for_mutation(org_key, config),
        load_user_instances(config, org_key, user_id),
        load_visible_instances(config, org_key, user_id),
        services["graph_provider"].get_user_connector_instances(
            collection=CollectionNames.APPS.value, user_id=user_id, org_id=org_key,
            team_scope=ConnectorScope.TEAM.value, personal_scope=ConnectorScope.PERSONAL.value,
        ),
    )
    return ImportCatalog(
        llm_entries=llm_entries,
        # The org-wide loader is an extension point and may return more than this org's instances (it is what a
        # save writes back), so the catalog keeps only the importer's org's.
        toolset_instances=[*(ts for ts in org_toolsets if ts.get("orgId") == org_key), *own_toolsets],
        mcp_instances=mcp_instances,
        connectors=list(connectors or []),
    )


_DRAFTED_MCP_LIST_SECONDS = 10.0


async def _mcp_tool_list(instance: dict[str, Any], user_id: str, config: Any) -> list[dict[str, Any]] | None:  # noqa: ANN401
    """The tools ``instance`` offers ``user_id`` (the tool cache first, then one bounded discovery, as the MCP
    listing does), or None when they cannot be known: not signed in, unreachable, too slow."""
    from app.agents.mcp import service as mcp_service
    from app.agents.mcp.discovery import (
        cached_tools_for_owner,
        discover_tools_for_owner,
    )

    try:
        auth = await mcp_service.resolve_effective_user_auth(instance, user_id, config)
        if auth is None or (auth != {} and not auth.get("isAuthenticated")):
            return None
        owner = mcp_service.credential_owner_id(instance, user_id)
        cached = await cached_tools_for_owner(instance, auth, owner, config)
        if cached is not None:
            tools = cached[0]
        else:
            tools, _ = await asyncio.wait_for(
                discover_tools_for_owner(instance, auth, owner, config), timeout=_DRAFTED_MCP_LIST_SECONDS,
            )
    except Exception as e:
        _log.info(f"Couldn't list the tools of MCP instance {instance.get('_id')} for a drafted agent: {e}")
        return None
    return [{"name": t.name, "fullName": t.namespaced_name, "description": t.description or ""} for t in tools]


async def _drafted_tool_lists(
    request: Request, config: Any, plan: ImportPlan, catalog: ImportCatalog, user_id: str,  # noqa: ANN401
) -> tuple[dict[str, list[dict[str, Any]] | None], dict[str, list[dict[str, Any]] | None]]:
    """The real tool lists a drafted agent's tools are held to (UI-ADOPT2): each named toolset type's from the
    toolset registry (a type it does not know offers none; no registry: unknown), and each bound MCP instance's
    that names its tools."""
    registry = getattr(request.app.state, "toolset_registry", None)
    toolset_tools: dict[str, list[dict[str, Any]] | None] = {}
    for ts in plan.body.get("toolsets") or []:
        kind = str(ts.get("name") or "").lower()
        if not ts.get("tools") or kind in toolset_tools:
            continue
        if registry is None:
            toolset_tools[kind] = None
            continue
        metadata = registry.get_toolset_metadata(kind) or {}
        offered = [] if metadata.get("isInternal") else [t for t in metadata.get("tools") or [] if isinstance(t, dict)]
        toolset_tools[kind] = [
            {"name": str(t.get("name") or ""), "fullName": f"{kind}.{t.get('name')}", "description": str(t.get("description") or "")}
            for t in offered if t.get("name")
        ]
    instances = {str(i.get("_id")): i for i in catalog.mcp_instances}
    wanted = [
        s["instanceId"] for s in plan.body.get("mcpServers") or []
        if s.get("allTools") is not True and s.get("tools") and s.get("instanceId") in instances
    ]
    lists = await asyncio.gather(*(_mcp_tool_list(instances[i], user_id, config) for i in wanted))
    return toolset_tools, dict(zip(wanted, lists))


@router.post("/import", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def import_agent(request: Request) -> JSONResponse:
    """Creates a new agent from a portable form (C4.08), as the create route does: owned by the caller, in the
    caller's org, shared with no one, nothing taken from the document but the portable fields. Each name binds to
    the one object of the importer's that carries it; the rest is left out and listed in ``rebind``. ``bind:
    sole_instance`` (an agent drafted in chat, ADR-AGB-01) also binds an action named without an instance to the
    importer's one instance of its type."""
    try:
        body = _parse_request_body(await request.body())
        unknown = sorted(set(body) - {"agent", "bind"})
        if unknown or not isinstance(body.get("agent"), dict) or body.get("bind", "named") not in ("named", "sole_instance"):
            raise InvalidRequestError("the body is {'agent': <portable agent>, 'bind'?: 'named' | 'sole_instance'}")
        services = await get_services(request)
        user_context = _get_user_context(request)
        catalog = await _import_catalog(services, current_org_id(), user_context["userId"])
        try:
            plan = plan_import(body["agent"], catalog, sole_instance=body.get("bind") == "sole_instance")
        except ValueError as e:
            # A schema mismatch, or an environment the agents API would refuse (a secret-looking variable).
            problem = _first_problem(e) if isinstance(e, ValidationError) else str(e)
            raise InvalidRequestError(f"this is not an agent export ({problem})") from e
        if body.get("bind") == "sole_instance":
            # A model wrote this draft's tool names: each is held to what its action or server really offers.
            toolset_tools, mcp_tools = await _drafted_tool_lists(
                request, services["config_service"], plan, catalog, user_context["userId"],
            )
            check_drafted_tools(plan, toolset_tools=toolset_tools, mcp_tools=mcp_tools)
        created = await _create_agent(request, plan.body)
        agent = created["agent"]
        linked = [str(s.get("name")) for s in agent.get("skills") or [] if isinstance(s, dict)]
        return JSONResponse(status_code=201, content={
            "agent": {"_key": agent["_key"], "name": agent["name"]},
            "bound": plan.bound,
            "rebind": [*plan.rebind, *unlinked_skills(plan.body["skills"], linked)],
            "warnings": created.get("warnings"),
        })
    except HTTPException:
        raise
    except Exception as e:
        _log.error(f"Error importing agent: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=action_failed("import this agent")) from e


async def require_agent_execute_for_named_agents(request: Request, agent_id: str) -> None:
    """Ask an OAuth token for `agent:execute` unless the agent is the universal one.

    Node's conversation routes (agent chat mode, `conversation:chat`) run the
    universal agent through these routes with the caller's token, so the route
    admits `conversation:chat` too. A named Agent Builder agent still needs
    `agent:execute`. Session and service tokens are not scope-restricted here.
    """
    if agent_id == UNIVERSAL_AGENT_ID:
        return
    user = getattr(request.state, "user", None) or {}
    if is_service_token(user) or not user.get("isOAuth", False):
        return
    if OAuthScopes.AGENT_EXECUTE.value not in user.get("oauthScopes", []):
        raise HTTPException(
            status_code=403,
            detail=f"Insufficient scope. Required: {OAuthScopes.AGENT_EXECUTE.value}",
        )


@router.get(
    "/{agent_id}/run-definition",
    # The stream's own rule (main #4220): the universal agent with conversation:chat, a named one with agent:execute.
    dependencies=[
        Depends(
            require_scopes(
                OAuthScopes.AGENT_EXECUTE,
                OAuthScopes.CONVERSATION_CHAT,
                service_scopes=(TokenScopes.CONVERSATION_CREATE,),
            )
        ),
        Depends(require_agent_execute_for_named_agents),
    ],
)
async def get_agent_run_definition(
    request: Request, agent_id: str, version: Annotated[int | None, Query(ge=1, le=2**31 - 1)] = None,
    *, first_use: Annotated[bool, Query(alias="mcpFirstUse")] = False,
) -> JSONResponse:
    """What this agent grants a run by the caller, for Node's durable-run admission.

    ``version`` names the published version a schedule pinned (OWD-14); absent, the latest. The
    definition's ``definitionVersion`` is that version's number. A pinned version the store does not hold (not
    yet migrated, a graph restored without it) answers the latest one instead, made from the live agent if the
    agent has none: a run that ran before versions is never refused for a missing version.

    ``mcpFirstUse`` (ADR-OWD-07 D-LAZY-2): Node's claim is for a worker that lists each MCP server at the run's first use
    of it, so nothing here lists one. A saved agent's servers grant their attached tools unpinned; the default chat's
    servers are granted whole (``atFirstUse``); under an edition's approval gate the stored approvals are pinned, as
    before. Absent (an older worker): the listing pins what the servers offer now, as before.

    Execute-scoped: an ``agent:execute``-only token may run an agent without
    being able to read its full definition (prompts, sharing). Access is the
    chat route's: a service-account agent may be run by anyone of its creator's
    org (the Slack bot's service token included), any other agent needs
    ``check_agent_permission``; no access is a 404.
    """
    from app.agents.agent_loop.skills.bundle_source import GraphSkillSource
    from app.agents.mcp.service import is_mcp_enabled
    from app.agents.run_definition import (
        build_run_definition,
        pin_skill_content,
        with_offered_skills,
    )
    from app.harness.assembly.descriptor_pins import ToolDescriptors
    from app.harness.mcp_hub.run_pinning import McpListing, mcp_run_pinning
    from app.harness.mcp_hub.settings import hub_enabled as mcp_hub_enabled
    from app.services.featureflag.platform_settings import is_actions_enabled

    try:
        services = await get_services(request)
        graph_provider = services["graph_provider"]
        config_service = services["config_service"]
        user_context = _get_user_context(request)
        org_key = current_org_id()

        if agent_id == ASSISTANT_AGENT_KEY:
            definition = await _assistant_run_definition(request, services, user_context, first_use=first_use)
            return JSONResponse(
                status_code=200,
                content={"definition": definition.model_dump(by_alias=True, mode="json"), AGENT_OWNER_TEAM_FIELD: None},
            )

        agent = await graph_provider.get_agent(agent_id, org_key)
        if not agent:
            raise AgentNotFoundError(agent_id)
        user_doc: dict[str, Any] = {}
        # Whose own skills the run is offered when the agent assigns none (FIX-SKILLS): a service account has none.
        viewers: tuple[str | None, ...] = ()
        if agent.get("isServiceAccount", False):
            await _assert_agent_belongs_to_caller_org(agent, graph_provider, org_key, agent_id)
        elif is_service_token(getattr(request.state, "user", None)) or user_context["isServiceAccount"]:
            raise AgentNotFoundError(agent_id)
        else:
            user_doc = await _get_user_document(user_context["userId"], graph_provider, services["logger"])
            if not await graph_provider.check_agent_permission(agent_id, user_doc["_key"], org_key):
                raise AgentNotFoundError(agent_id)
            viewers = (user_context["userId"], user_doc.get("_key"))

        store = AgentVersionStore(graph_provider)
        pinned = await store.get(org_key, agent_id, version) if version is not None else None
        if pinned is None:
            pinned = await store.current(org_key, agent_id, agent)
            if version is not None:
                _log.warning(
                    "agent.version_unavailable agentKey=%s pinned=%s used=%s", agent_id, version, pinned.version,
                )
        agent = pinned_agent(agent, pinned)
        actions_enabled, mcp_enabled, owner = await asyncio.gather(
            is_actions_enabled(config_service), is_mcp_enabled(config_service), _owner_user_id(agent, graph_provider)
        )
        await _enrich_agent_models(agent, config_service, services["logger"])
        definition = build_run_definition(
            agent,
            agent_key=agent_id,
            definition_version=pinned.version,
            actions_enabled=actions_enabled,
            mcp_enabled=mcp_enabled,
            web_search_provider=_parse_web_search(agent.get("webSearch")),
        )
        definition = definition.model_copy(update={"owner": owner})
        definition = await with_offered_skills(
            definition, graph_provider, org_key, viewers, enabled=await _skills_on(config_service)
        )
        definition = await pin_skill_content(definition, GraphSkillSource(graph_provider), org_key)
        servers = [server for server in agent.get("mcpServers") or [] if server.get("instanceId")]

        async def list_servers() -> McpListing:
            failures: list[dict[str, Any]] = []
            live, answered = await _live_mcp_listing(
                servers, config_service, user_context, user_doc, services["logger"],
                agent_id=agent_id, is_service_account=bool(agent.get("isServiceAccount", False)),
                failures=failures,
            )
            return McpListing(live, answered, failures)

        pinning = await mcp_run_pinning(org_key, config_service, hub=mcp_hub_enabled(harness=True))
        listing_due = not first_use and mcp_enabled and bool(servers) and bool(definition.mcp_servers)
        definition = await pinning.agent_definition(
            definition, ToolDescriptors(config_service), list_servers if listing_due else None
        )
        return JSONResponse(
            status_code=200,
            content={
                "definition": definition.model_dump(by_alias=True, mode="json"),
                # The control plane's AgentDirectory.ownerTeam (CPD-9); beside the definition, whose schema is closed.
                AGENT_OWNER_TEAM_FIELD: agent.get(AGENT_OWNER_TEAM_FIELD),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        _log.error(f"Error resolving agent run definition: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=action_failed("load this agent")) from e


async def _assistant_run_definition(
    request: Request, services: dict[str, Any], user_context: dict[str, Any], *, first_use: bool = False,
) -> AgentRunDefinition:
    """The system assistant definition (ASSIST1): what Universal Agent Mode gives this caller now, from the same
    source (``get_assistant_agent``) and through the same projection as a saved agent's. The route's scope rule is the
    agent chat route's, so a token that may use Universal Agent Mode may admit it durably, and no other."""
    from app.agents.agent_loop.skills.bundle_source import GraphSkillSource
    from app.agents.mcp.service import is_mcp_enabled
    from app.agents.run_definition import (
        build_run_definition,
        pin_skill_content,
        pin_tool_descriptors,
        with_offered_skills,
    )
    from app.harness.assembly.descriptor_pins import ToolDescriptors
    from app.harness.mcp_hub.run_pinning import McpListing, mcp_run_pinning
    from app.harness.mcp_hub.settings import hub_enabled as mcp_hub_enabled
    from app.services.featureflag.platform_settings import is_actions_enabled

    logger = services["logger"]
    graph_provider = services["graph_provider"]
    config_service = services["config_service"]
    actions_enabled, mcp_enabled, user_doc = await asyncio.gather(
        is_actions_enabled(config_service),
        is_mcp_enabled(config_service),
        _get_user_document(user_context["userId"], graph_provider, logger),
    )
    assistant, _ = await get_assistant_agent(
        user_context["userId"], current_org_id(), config_service, graph_provider,
        getattr(request.app.state, "toolset_registry", None), logger,
        actions_enabled=actions_enabled, mcp_enabled=mcp_enabled, user_doc=user_doc,
    )
    servers = list(assistant.get("mcpServers") or [])
    listed = list(servers)

    async def list_servers() -> McpListing:
        failures: list[dict[str, Any]] = []
        live, answered = await _live_mcp_listing(listed, config_service, user_context, user_doc, logger, failures=failures)
        return McpListing(live, answered, failures)

    pinning = await mcp_run_pinning(current_org_id(), config_service, hub=mcp_hub_enabled(harness=True))
    servers, descriptors = await pinning.assistant_servers(
        servers,
        ToolDescriptors(config_service),
        first_use=first_use,
        list_servers=list_servers if servers and mcp_enabled else None,
    )
    web_search = await resolve_web_search(config_service, logger)
    definition = build_run_definition(
        {**assistant, "mcpServers": servers},
        agent_key=ASSISTANT_AGENT_KEY,
        actions_enabled=actions_enabled,
        mcp_enabled=mcp_enabled,
        web_search_provider=web_search["provider"] if web_search else None,
    )
    definition = await with_offered_skills(
        definition,
        graph_provider,
        current_org_id(),
        (user_context["userId"], user_doc.get("_key")),
        enabled=await _skills_on(config_service),
    )
    definition = await pin_skill_content(definition, GraphSkillSource(graph_provider), current_org_id())
    return await pin_tool_descriptors(definition, descriptors, mcp_approvals=True)


async def _skills_on(config_service: ConfigurationService) -> bool:
    """Both skills gates the loop reads (``SkillsSource``): the deployment's switch and the org's Labs flag."""
    from app.agents.agent_loop.skills_wiring import skills_enabled
    from app.services.featureflag.platform_settings import is_skills_enabled

    return skills_enabled() and await is_skills_enabled(config_service)


async def _live_mcp_listing(
    servers: list[dict[str, Any]],
    config_service: ConfigurationService,
    user_context: dict[str, Any],
    user_doc: dict[str, Any],
    logger: Logger,
    *,
    agent_id: str = ASSISTANT_AGENT_KEY,
    is_service_account: bool = False,
    failures: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, dict[str, str]], frozenset[str]]:
    """The assistant's MCP tools as a durable run's claim will discover them for this caller now, and the servers
    whose listing answered: the same credential path (the caller's own records) and the same discovery
    (``MCPToolProvider``, through the hub where it governs harness runs), bounded for admission. ``failures``, when
    given, receives why each server's listing named nothing (``{instanceId, reason}``, never a credential)."""
    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.mcp_tool_loader import MCPToolProvider
    from app.agents.mcp.failure import MCPFailureReason

    user_id = user_context["userId"]
    # The chat route's credential owner: a service-account agent's own records, else the caller's.
    owner = agent_id if is_service_account else user_id
    blocked: list[dict[str, Any]] = []
    configs, configured, _, _ = await _resolve_mcp_server_configs(
        config_service,
        servers,
        org_id=current_org_id(),
        credential_lookup_id=owner,
        executing_user_id=user_id,
        agent_id=agent_id,
        is_service_account=is_service_account,
        logger=logger,
        blocked=blocked,
    )
    if failures is not None:
        # A server whose sign-in went missing between the assistant's read and this one is never listed.
        failures.extend(
            {
                "instanceId": entry["instanceId"],
                "reason": (
                    MCPFailureReason.UNAUTHORIZED if entry["problem"] == "not_connected" else MCPFailureReason.ERROR
                ).value,
            }
            for entry in blocked
        )
    if not configured:
        return {}, frozenset()
    context = AgentContext(
        org_id=current_org_id(),
        user_id=user_id,
        user_email=user_doc.get("email") or "",
        credential_owner=owner,
        config_service=config_service,
        logger=logger,
        mcp_servers=configured,
        mcp_server_configs=configs,
        harness_run=True,
    )
    try:
        listed = await MCPToolProvider().live_listing(context)
    finally:
        await context.close_credentials()
    if failures is not None:
        failures.extend(
            {"instanceId": failure.get("instanceId"), "reason": failure.get("reason"),
             **({"tools": list(failure["tools"])} if isinstance(failure.get("tools"), list) else {})}
            for failure in context.mcp_tool_load_failures
        )
    return listed


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
        raise _route_failure(request, e, "load your agents") from e


async def _write_draft(
    request: Request, agent_id: str, body: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]:
    """Writes ``body`` over the agent (the draft) through the agent service: its document, then each of its
    toolsets, knowledge, MCP servers and skills that ``body`` names, each replaced whole. Publishes nothing.
    Answers the services, the caller, the org and the service's answer."""
    services = await get_services(request)
    user_context = _get_user_context(request)
    org_key = current_org_id()

    patch = AgentPatch.model_validate(body)
    actor = await _build_agent_actor(
        user_context, services, is_admin=await _administers_for_a_tool_edit(request, services, patch),
    )
    result = await _agent_service(
        services, request, hooks=_write_hooks(request, services, body, actor.user_key),
    ).update(actor, agent_id, patch)
    await directory_events(request).agent_changed(org_key, agent_id, get_epoch_timestamp_in_ms())
    await _sync_agent(services, org_key, agent_id)
    return services, user_context, org_key, result


@router.put("/{agent_id}", dependencies=[_AGENT_ID_LOG, Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def update_agent(request: Request, agent_id: str) -> JSONResponse:
    """Update an agent using graph-based architecture"""
    try:
        body = _parse_request_body(await request.body())
        services, user_context, org_key, result = await _write_draft(request, agent_id, body)
        logger = services["logger"]
        # A save publishes unless it asks to stay a draft (`"publish": false`): today's builder has no
        # publish step, and its saves must keep reaching chats as they always did.
        if body.get("publish") is False:
            return JSONResponse(
                status_code=200,
                content={**result, "status": "success", "message": "Draft saved", "version": None},
            )
        try:
            published, _ = await _publish_draft(
                request, services, org_key, agent_id, user_context["userId"], body.get("versionNote"),
            )
        except Exception as e:
            # The edit is saved; it stays a draft the editor can publish again from its versions.
            error_class.log_failure(logger, "agent.route.failed", e, op="publish", include_error_text=False)
            return JSONResponse(
                status_code=200,
                content={
                    **result,
                    "status": "partial_success",
                    "message": "Agent saved, but publishing it failed. Publish it again from its versions.",
                    "version": None,
                },
            )
        return JSONResponse(
            status_code=200,
            content={**result, "version": published.summary()},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _route_failure(request, e, "save this agent", writes_body=True) from e


@router.get("/{agent_id}/internal/deletion")
async def get_agent_deletion_internal(
    agent_id: str,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    claims: Mapping[str, Any] = Depends(require_service_token(TokenScopes.AGENT_ERASE)),
) -> dict[str, str]:
    """Whether the agent's delete committed, for Node's agent-erasure sweeper (H5): ``deleted``, ``live`` or ``absent``.

    Node signs the org into the service token; an agent outside it reads as absent. A graph failure is a 503, never an
    answer: Node erases an agent's memories only on ``deleted``.
    """
    org_id = (bound_org_id() or "").strip()
    if not org_id:
        raise HTTPException(status_code=400, detail="Service token must carry orgId")
    try:
        state = await agent_deletion_state(graph_provider, agent_id, org_id)
    except Exception as e:
        _log.warning("agent_deletion.lookup_failed errorType=%s", type(e).__name__)
        raise HTTPException(status_code=503, detail="The agent's state could not be read") from e
    return {"state": state.value}


class AgentNamesRequest(BaseModel):
    agent_keys: list[str] = Field(alias="agentKeys", max_length=MAX_AGENT_KEYS)
    # Absent from a Node older than ADR-UI-03, which reads only ``names``.
    include: list[Literal["latestVersion"]] = Field(default_factory=list, max_length=1)


@router.post("/internal/names")
async def get_agent_names_internal(
    body: AgentNamesRequest,
    graph_provider: IGraphDBProvider = Depends(get_graph_provider),
    claims: Mapping[str, Any] = Depends(require_service_token(TokenScopes.AGENT_NAMES)),
) -> dict[str, dict[str, Any]]:
    """The names of the token's org's agents by key, for Node's usage report, budget notices and admin lists (WP-51,
    ADR-UI-03); with ``include: ["latestVersion"]`` also ``latestVersions``, each named agent's latest published
    version (a key with none yet is absent).

    Node signs the org into the service token; a key outside it is absent from the answer, as an unknown one is. A
    graph failure is a 503, never a partial answer: Node then shows the agents unnamed.
    """
    org_id = (bound_org_id() or "").strip()
    if not org_id:
        raise HTTPException(status_code=400, detail="Service token must carry orgId")
    try:
        names = await agent_names(graph_provider, body.agent_keys, org_id)
        answer: dict[str, dict[str, Any]] = {"names": names}
        if "latestVersion" in body.include:
            answer["latestVersions"] = await latest_versions(AgentVersionStore(graph_provider), list(names), org_id)
    except Exception as e:
        _log.warning("agent_names.lookup_failed errorType=%s", type(e).__name__)
        raise HTTPException(status_code=503, detail="The agents' names could not be read") from e
    return answer


@router.delete("/{agent_id}", dependencies=[_AGENT_ID_LOG, Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
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

        # Soft-delete: marks the agent instance deleted; related toolsets/tools/knowledge remain.
        result = await services["graph_provider"].delete_agent(
            agent_id, user_doc["_key"], org_key, transaction=txn_id
        )
        if not result:
            raise HTTPException(status_code=500, detail="Failed to delete agent")  # the handler below rolls back

        # Commit transaction on success
        await services["graph_provider"].commit_transaction(txn_id)
        txn_id = None
        await _sync_agent(services, org_key, agent_id)
        await directory_events(request).agent_changed(org_key, agent_id, None)

        # A service-account agent's own MCP credentials go with it (agents can't be restored),
        # so nothing keeps refreshing their tokens.
        if agent.get("isServiceAccount"):
            try:
                from app.agents.mcp import lifecycle as mcp_lifecycle

                await mcp_lifecycle.remove_owner_credentials(services["config_service"], org_key, agent_id)
            except Exception as e:
                error_class.log_failure(
                    services["logger"], "agent.route.failed", e, op="remove_mcp_credentials", include_error_text=False,
                )

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
                            log_event(services["logger"], "agent.refresh_cancelled", level=logging.DEBUG,
                                      toolset_id=parts[2])
            except Exception as e:
                error_class.log_failure(
                    services["logger"], "agent.route.failed", e, op="cancel_refresh", include_error_text=False,
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
    except Exception as e:
        if txn_id is not None and services is not None:
            try:
                await services["graph_provider"].rollback_transaction(txn_id)
            except Exception as rb_err:
                _rollback_failed(services["logger"], rb_err)
        if isinstance(e, HTTPException):
            raise
        raise _route_failure(request, e, "delete this agent") from e


# ============================================================================
# Agent Chat Endpoints
# ============================================================================

UNIVERSAL_AGENT_ID = "agentIdPlaceholder"


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
    """Which of this agent's toolsets and MCP servers the caller can use, and whether a run would start."""
    try:
        services = await get_services(request)
        user_context = _get_user_context(request)
        org_key = current_org_id()
        graph_provider = services["graph_provider"]

        agent = await graph_provider.get_agent(agent_id, org_key)
        if not agent:
            raise AgentNotFoundError(agent_id)
        caller_policy = get_agent_caller_policy()
        # As in the run: a service-account agent that runs as itself is the org's to use and runs on the
        # credentials kept for it; any other agent answers to the caller's own permission on it.
        runs_as_agent = bool(agent.get("isServiceAccount", False)) and caller_policy.service_agents_run_as_themselves()
        if runs_as_agent:
            await _assert_agent_belongs_to_caller_org(agent, graph_provider, org_key, agent_id)
        else:
            await caller_policy.admit_agent(agent, graph_provider=graph_provider, org_id=org_key, agent_id=agent_id)
            user_doc = await _get_user_document(user_context["userId"], graph_provider, services["logger"])
            if not await graph_provider.check_agent_permission(agent_id, user_doc["_key"], org_key):
                raise AgentNotFoundError(agent_id)

        config_service = services["config_service"]
        from app.agents.mcp.service import is_mcp_enabled
        from app.services.featureflag.platform_settings import is_actions_enabled

        actions_enabled, mcp_enabled = await asyncio.gather(
            is_actions_enabled(config_service), is_mcp_enabled(config_service),
        )
        mcp_servers = (agent.get("mcpServers") or []) if mcp_enabled else []
        if mcp_servers:
            from app.edition_config import unusable_agent_mcp_server_ids

            # As in the run: a server this person wasn't given is left out, never reported or waited on.
            unusable_mcp_server_ids = await unusable_agent_mcp_server_ids(
                mcp_servers, config_service, org_key, user_context["userId"],
            )
            mcp_servers = [s for s in mcp_servers if s.get("instanceId") not in unusable_mcp_server_ids]
        return await compute_agent_readiness(
            agent,
            user_context,
            config_service=config_service,
            agent_id=agent_id,
            toolsets=agent.get("toolsets", []) if actions_enabled else [],
            mcp_servers=mcp_servers,
            runs_as_agent=runs_as_agent,
        )
    except HTTPException:
        raise
    except Exception as e:
        _log.error("get_agent_readiness failed: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=action_failed("check this agent's readiness")) from e


@router.post(
    "/{agent_id}/chat",
    dependencies=[
        Depends(require_scopes(OAuthScopes.AGENT_EXECUTE, OAuthScopes.CONVERSATION_CHAT)),
        Depends(require_agent_execute_for_named_agents),
        Depends(guard_agent_route),
    ],
)
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

    outcome = await collect_stream_outcome(
        streaming_response.body_iterator, request.is_disconnected,
        keep_events=get_chat_extension().completion_events(),
    )
    return outcome.to_response()


ToolsetConfigResolution = tuple[dict[str, Any], list[dict[str, Any]], list[str], list[str]]


async def _resolve_toolset_configs(
    config_service: ConfigurationService,
    named_toolsets: list[dict[str, Any]],
    *,
    credential_lookup_id: str,
    executing_user_id: str,
    prefetched: dict[str, dict[str, Any]],
    agent_id: str,
    is_service_account: bool,
    logger: Logger,
    bound: frozenset[str] = frozenset(),
) -> ToolsetConfigResolution:
    """Fetch every toolset's credentials concurrently (SECURITY-CRITICAL: see the
    chat route's "LOAD TOOLSET CONFIGS" block for the credential-owner rules).

    Returns ``(toolset_configs, configured_toolsets, missing_display_names,
    unauthenticated_display_names)``. Shared by the legacy chat route and the
    durable run assembler, so both resolve credentials the same way.

    ``bound``: the instances whose credential the turn's resolution already holds
    ready for ``credential_lookup_id`` (CPD-10); their record is not read here.
    """
    from app.agents.constants.toolset_constants import get_toolset_config_path
    from app.harness.credentials.tool_credentials import toolset_reference

    async def _fetch_toolset_config(toolset: dict) -> tuple[dict, Any]:
        """Return (toolset, config_or_None) without raising.

        Uses instanceId (admin-created instance) if available, otherwise falls
        back to the legacy toolset name for backward compatibility.
        For service account agents, uses agentKey as the credential owner.
        """
        instance_id = toolset.get("instanceId")
        toolset_name = toolset.get("name", "")
        lookup_key = instance_id
        if lookup_key in bound:
            return toolset, {"isAuthenticated": True}
        # `get_assistant_agent` already read this exact etcd path
        # (`/services/toolsets/{instanceId}/{userId}`) for the assistant
        # agent — reuse it rather than issuing the same read per toolset.
        cached = (
            prefetched.get(lookup_key)
            if credential_lookup_id == executing_user_id
            else None
        )
        if cached is not None:
            return toolset, cached
        try:
            etcd_path = get_toolset_config_path(lookup_key, credential_lookup_id)
            config = await config_service.get_config(etcd_path)
            return toolset, config
        except Exception as exc:
            logger.warning(f"Failed to load config for toolset '{toolset_name}' (lookup_key='{lookup_key}'): {exc}")
            return toolset, None

    # Fetch ALL toolset configs in parallel
    fetch_results = await asyncio.gather(*[_fetch_toolset_config(t) for t in named_toolsets])

    toolset_configs: dict[str, Any] = {}  # references to credentials, never their values
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
            # A reference only: the record is resolved by handle when a client is created (P2.4a).
            toolset_configs[lookup_key] = toolset_reference(lookup_key, credential_lookup_id)
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

    return (
        toolset_configs,
        configured_toolsets,
        missing_toolset_display_names,
        unauthenticated_toolset_display_names,
    )


async def _resolve_mcp_server_configs(
    config_service: ConfigurationService,
    named_mcp_servers: list[dict[str, Any]],
    *,
    org_id: str,
    credential_lookup_id: str,
    executing_user_id: str,
    agent_id: str,
    is_service_account: bool,
    logger: Logger,
    bound: frozenset[str] = frozenset(),
    prefetched: dict[str, dict[str, Any]] | None = None,
    blocked: list[dict[str, Any]] | None = None,
) -> ToolsetConfigResolution:
    """Resolve every attached MCP server's instance and effective auth concurrently
    (SECURITY-CRITICAL: see the chat route's "LOAD MCP SERVER CONFIGS" block).

    Returns ``(mcp_server_configs, configured_mcp_servers, missing_display_names,
    unauthenticated_display_names)``. Shared with the durable run assembler.

    ``bound``: as in ``_resolve_toolset_configs``; the instance definition is still
    read (it holds the transport), the credential record is not. ``prefetched``: instances and
    auth the assistant's listing already read. ``blocked``, when given, receives each missing or
    unconnected server, for the chat to offer a Connect button per server.
    """
    from app.agents.mcp import service as mcp_service
    from app.agents.mcp.instance_scope import get_instance_for_org
    from app.harness.credentials.tool_credentials import REF_KEY, mcp_reference

    async def _fetch_mcp_server_config(
        mcp_server: dict,
    ) -> tuple[dict, dict[str, Any] | None, dict[str, Any] | None]:
        """Return (mcp_server, instance_or_None, effective_auth) without raising."""
        instance_id = mcp_server["instanceId"]
        if prefetched_entry := (prefetched or {}).get(instance_id):
            instance = prefetched_entry["instance"]
            return mcp_server, instance, {} if instance_id in bound else prefetched_entry["auth"]
        try:
            # A service account's agent never reaches a user's personal server.
            instance = await get_instance_for_org(
                instance_id, config_service, org_id, None if is_service_account else executing_user_id,
            )
            if not instance:
                return mcp_server, None, None
            if instance_id in bound:
                return mcp_server, instance, {}
            effective_auth = await mcp_service.resolve_effective_user_auth(
                instance, credential_lookup_id, config_service,
            )
            return mcp_server, instance, effective_auth
        except Exception as exc:
            logger.warning(f"Failed to load MCP server config for instance '{instance_id}': {exc}")
            return mcp_server, None, None

    mcp_fetch_results = await asyncio.gather(*[_fetch_mcp_server_config(m) for m in named_mcp_servers])

    mcp_server_configs: dict[str, dict[str, Any]] = {}  # instance metadata and credential references
    configured_mcp_servers = []
    missing_mcp_server_display_names: list[str] = []          # instance no longer exists
    unauthenticated_mcp_server_display_names: list[str] = []  # instance exists, auth incomplete

    for mcp_server, instance, effective_auth in mcp_fetch_results:
        instance_id = mcp_server["instanceId"]
        display_name = mcp_server.get("displayName") or mcp_server.get("name") or instance_id

        if instance is None:
            missing_mcp_server_display_names.append(display_name)
            if blocked is not None:
                blocked.append({"instanceId": instance_id, "name": display_name, "problem": "not_found"})
            logger.warning(f"MCP server instance '{instance_id}' not found for agent {agent_id}.")
            continue

        if mcp_service.is_effective_auth_authenticated(effective_auth):
            # The auth record is resolved by handle when the session opens (P2.4a).
            mcp_server_configs[instance_id] = {
                "instance": instance,
                REF_KEY: mcp_reference(instance_id, credential_lookup_id),
                "ownerId": credential_lookup_id,
            }
            configured_mcp_servers.append(mcp_server)
        else:
            unauthenticated_mcp_server_display_names.append(display_name)
            if blocked is not None:
                blocked.append({
                    "instanceId": instance_id,
                    "name": display_name,
                    "problem": "not_connected",
                    "authMode": instance.get("authMode"),
                    "sharedCredential": mcp_service.uses_shared_credential(instance),
                })
            cred_owner = f"agent '{agent_id}'" if is_service_account else f"user '{executing_user_id}'"
            logger.warning(
                f"MCP server '{display_name}' (instance='{instance_id}') is not authenticated "
                f"for {cred_owner}."
            )

    return (
        mcp_server_configs,
        configured_mcp_servers,
        missing_mcp_server_display_names,
        unauthenticated_mcp_server_display_names,
    )


@router.post(
    "/{agent_id}/chat/stream",
    dependencies=[
        Depends(
            require_scopes(
                OAuthScopes.AGENT_EXECUTE,
                OAuthScopes.CONVERSATION_CHAT,
                service_scopes=(TokenScopes.CONVERSATION_CREATE,),
            )
        ),
        Depends(require_agent_execute_for_named_agents),
        Depends(guard_agent_route),
    ],
)
async def chat_stream(request: Request, agent_id: str) -> StreamingResponse:
    """Chat with an agent using streaming response"""
    timer = StageTimer()
    resolved = None
    try:
        services = await get_services(request)
        logger = services["logger"]

        config_service = services["config_service"]
        graph_provider = services["graph_provider"]
        retrieval_service = services["retrieval_service"]
        reranker_service = None  # reranking is not wired; the slot is kept for call signatures
        config_service = services["config_service"]
        user_context = _get_user_context(request)
        caller_policy = get_agent_caller_policy()
        caller_policy.check_caller(user_context)
        org_key = user_context["orgId"]
        body = _parse_request_body(await request.body())
        try:
            chat_query = get_chat_extension().chat_query_model(ChatQuery)(**body)
        except ValidationError as exc:
            # Say what is wrong (e.g. the unknown filter key and the allowed ones).
            raise HTTPException(
                status_code=400,
                detail="; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors()),
            ) from exc
        # Before anything is read: a header that does not verify is refused, never served from the reads below.
        resolved = await resolved_turn_or_401(
            request, agent_id=agent_id, conversation_id=chat_query.conversationId,
        )
        bind_resolved_stages(resolved)
        # Optional, and resolved here rather than in get_services: it costs a
        # vector-DB round trip, which the list/read/template routes never need.
        entity_vector_store = await load_entity_vector_store(
            request.app.container, logger, org_key,
        )

        timer.mark("services")
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
            # SENSITIVE: the assistant's MCP instances and credentials, read once.
            prefetched_mcp: dict[str, dict[str, Any]] = {}
            enriched_user_info = await _enrich_user_info(user_context, user_doc)
            _apply_user_context_gate(enriched_user_info, enabled=user_context_enabled)
            perm = {"can_edit": False, "can_share": False, "role": "viewer"}
            is_service_account = False
            artifact_owner_user_id = None

        else:
            actions_enabled = mcp_enabled = None
            prefetched_toolset_auth: dict[str, dict[str, Any]] = {}
            prefetched_mcp: dict[str, dict[str, Any]] = {}
            org_info, agent = await asyncio.gather(
                _get_org_info(user_context, graph_provider, logger),
                services["graph_provider"].get_agent(agent_id, org_key),
            )
            if not agent:
                raise AgentNotFoundError(agent_id)
            # A service-account agent runs as its own identity, unless the edition runs every agent
            # as the person asking (`AgentCallerPolicy.service_agents_run_as_themselves`).
            is_service_account = (
                bool(agent.get("isServiceAccount", False)) and caller_policy.service_agents_run_as_themselves()
            )
            artifact_owner_user_id = None

            if is_service_account:
                enriched_user_info = await _enrich_user_info_for_service_account_agent_chat(
                    agent, graph_provider, logger, org_key
                )
                enriched_user_info = await _resolve_service_account_caller_identity(
                    enriched_user_info, chat_query, user_context, graph_provider, logger,
                )
                artifact_owner_user_id = await _service_account_artifact_owner(
                    user_context, graph_provider, org_key,
                )
                perm = {"can_edit": False, "can_share": False, "role": "viewer"}
                logger.debug(
                    "Loaded service account agent %s (retrieval runs as user %s)",
                    agent_id,
                    enriched_user_info.get("userId"),
                )
            else:
                # Standard user path: look up the user document and verify permissions.
                await caller_policy.admit_agent(agent, graph_provider=graph_provider, org_id=org_key, agent_id=agent_id)
                user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], logger)
                caller_policy.admit_person(user_doc)
                enriched_user_info = await _enrich_user_info(user_context, user_doc)
                if resolved is not None and resolved.access_run:
                    # Node's agent directory holds a current "may run" for this caller (CPD-10).
                    perm = {"can_edit": False, "can_share": False, "role": "viewer"}
                else:
                    perm = await services["graph_provider"].check_agent_permission(agent_id, user_doc["_key"], org_key)
                    if not perm:
                        raise AgentNotFoundError(agent_id)
            agent = await agent_for_chat(
                agent, graph_provider, org_key, agent_id, config_service, resolved=resolved is not None,
            )
            if stored_agent_gates(agent.get(AGENT_GATES_FIELD)) is not None:
                # WP-31 D: only the agent harness holds a run to the agent's plan approval and checks; this loop
                # would run it without them, so it does not run it at all.
                raise HTTPException(status_code=409, detail=GATED_AGENT_NEEDS_HARNESS)

            _apply_user_context_gate(
                enriched_user_info,
                enabled=bool(agent.get("sendUserContext", True)),
            )

        caller_principal = principal_from_request_user(
            user_context, agent_id=None if agent_id == "agentIdPlaceholder" else agent_id,
        )
        await assert_identity_allowed(
            caller_principal, Workload.AGENT,
            config_service=config_service, container=request.app.container,
        )

        async def _run_unbound() -> AsyncGenerator[str, None]:
            """Everything below the authorization checks. Runs after the
            response headers are already on the wire, so the client sees the
            stream open immediately instead of waiting out the config fan-out.

            Failures in here surface as a terminal SSE error frame rather than
            an HTTP status — the status line is long gone by then. Genuine
            auth/not-found errors are raised before this, in `chat_stream`."""
            nonlocal agent, prefetched_toolset_auth, actions_enabled, mcp_enabled
            try:
                # Node sends every Agent-mode question of the assistant here, not to `/chat/stream`, so the
                # assistant's own answers that need no model (the edition's help card) are given here too.
                if agent_id == UNIVERSAL_AGENT_ID:
                    preflight = await get_chat_extension().preflight(
                        chat_query, graph_provider=graph_provider, config_service=config_service,
                        user_id=user_context.get("userId"), org_id=org_key, logger=logger,
                    )
                    if preflight is not None:
                        async for chunk in preflight:
                            yield chunk
                        return
                if agent is None:
                    agent, prefetched_toolset_auth = await get_assistant_agent(
                        user_context["userId"], org_key, config_service, graph_provider,
                        toolset_registry, logger,
                        actions_enabled=actions_enabled, mcp_enabled=mcp_enabled, user_doc=user_doc,
                        mcp_prefetch=prefetched_mcp,
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
                agent_mcp_servers = _mcp_servers_for_chat(
                    agent.get("mcpServers") or [],
                    set(chat_query.tools) if chat_query.tools is not None else None,
                ) if mcp_enabled else []

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

                executing_user_id = user_context["userId"]

                readiness = await compute_agent_readiness(
                    agent,
                    user_context,
                    config_service=config_service,
                    agent_id=agent_id,
                    toolsets=agent_toolsets,
                    prefetched_auth=prefetched_toolset_auth,
                    mcp_servers=agent_mcp_servers,
                    prefetched_mcp=prefetched_mcp,
                    # Credentials the turn's resolution already holds are not read again: the caller's, or the
                    # agent's for a service-account agent that runs as itself.
                    bound=bound_installations(
                        resolved,
                        agent_id if is_service_account else executing_user_id,
                        service_account=is_service_account,
                    ),
                    runs_as_agent=is_service_account,
                )
                toolset_configs = readiness.toolset_configs  # references to the caller's credentials
                # MCP instances and references to the caller's credentials. An MCP server's instance
                # (transport/url/authMode) is never stored on the graph node.
                mcp_server_configs: dict[str, dict[str, Any]] = readiness.mcp_server_configs

                # A service-account agent stops the run while one of its tools cannot be used. Everyone
                # else runs with what they have connected; the model sees the rest as stubs.
                if not readiness.can_send:
                    log_turn_end(
                        logger, "error", error_class="bad_request", code=readiness.blocked_code,
                        run_id=chat_query.runId, duration_ms=round(timer.total_ms),
                        missing=sum(1 for i in readiness.unavailable_tools if i.problem != "not_authenticated"),
                        unauthenticated=sum(1 for i in readiness.unavailable_tools if i.problem == "not_authenticated"),
                    )
                    yield _stream_error_frame(
                        protocol, readiness.blocked_message, code=readiness.blocked_code,
                        details=readiness.blocked_details(agent_id),
                    )
                    return

                if readiness.configured_toolsets is not None:
                    agent_toolsets = readiness.configured_toolsets
                if readiness.configured_mcp_servers is not None:
                    agent_mcp_servers = readiness.configured_mcp_servers
                # The run offers these as stubs (`not_connected_tools`); only an answer that calls one names it.
                unavailable_tools = [item.wire() for item in readiness.unavailable_tools]
                if unavailable_tools:
                    logger.info(
                        f"Agent {agent_id} runs without tools user {executing_user_id!r} has not connected: "
                        f"{[(i['kind'], i['instanceId'], i['problem']) for i in unavailable_tools]}"
                    )

                timer.mark("toolset_cfg")
                timer.mark("mcp_cfg")

                agent_knowledge = agent.get("knowledge", [])
                # Anyone in the org may use a service agent, so it keeps only the sources shared with the
                # org, and a collection (the caller's project collection included) only when it is bound.
                # Both flags are the agent record's own, never the request's, and the lookup runs as the
                # identity the run acts as: this only removes sources.
                service_agent = bool(agent.get("isServiceAccount", False))
                collections_allowed = not service_agent or bool(agent.get("serviceAccountUserId"))
                if service_agent:
                    agent_knowledge = await service_account_knowledge(
                        graph_provider, agent_knowledge, enriched_user_info.get("userId", ""), org_key, logger,
                        bound=collections_allowed,
                    )
                caps = _parse_agent_capabilities(chat_query.agentCapabilities)
                try:
                    filters = await _resolve_turn_filters(
                        agent_id=agent_id,
                        agent_knowledge=agent_knowledge,
                        requested_filters=chat_query.filters,
                        graph_provider=graph_provider,
                        caller_user_id=user_context.get("userId", ""),
                        org_id=org_key,
                        logger=logger,
                        project_sources=_project_sources(chat_query.allowedFilters),
                        connection_sources=connection_limit(getattr(request.state, "user", None)),
                        retrieval_user_id=enriched_user_info.get("userId"),
                        searches_knowledge=caps.internal_search,
                        admit_project_collections=collections_allowed,
                    )
                except (SelectionTooLargeError, SelectionNotReadyError) as exc:
                    code, message = selection_error(
                        exc,
                        agent_limit=agent_id != "agentIdPlaceholder" and not has_selection(chat_query.filters),
                    )
                    log_turn_end(
                        logger, "error", error_class="bad_request", code=code,
                        run_id=chat_query.runId, duration_ms=round(timer.total_ms),
                    )
                    yield _stream_error_frame(protocol, message, code=code)
                    return

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
                try:
                    # The universal agent's knowledge is every source the user
                    # reads, and the prompt names each one with its id. A turn
                    # without internal search drops its knowledge below, so a
                    # limit too large to check must not fail it.
                    agent_knowledge = await _knowledge_within_connection(
                        graph_provider,
                        enriched_user_info.get("userId") or user_context.get("userId", ""),
                        org_key,
                        agent_knowledge,
                        connection_limit(getattr(request.state, "user", None)) if caps.internal_search else None,
                    )
                except SelectionTooLargeError:
                    log_turn_end(
                        logger, "error", error_class="bad_request", code="selection_too_large",
                        run_id=chat_query.runId, duration_ms=round(timer.total_ms),
                    )
                    yield _stream_error_frame(
                        protocol, CONNECTION_LIMIT_TOO_LARGE_MESSAGE, code="selection_too_large",
                    )
                    return
                if chat_query.strictScope and caps.internal_search:
                    agent_knowledge = [
                        *agent_knowledge,
                        *await project_files_knowledge(
                            graph_provider,
                            kb_ids=filters.get("kb") or [],
                            known_ids=[
                                k.get("connectorId") for k in agent_knowledge if isinstance(k, dict)
                            ],
                            logger=logger,
                        ),
                    ]

                _stream_conn_ids = [
                    k["connectorId"] for k in agent_knowledge
                    if isinstance(k, dict) and k.get("connectorId")
                ]
                web_search_provider = _parse_web_search(agent.get("webSearch"))
                if web_search_provider:
                    web_search_coro = resolve_web_search(
                        config_service, logger, provider=web_search_provider,
                    )
                elif agent_id == "agentIdPlaceholder":
                    web_search_coro = resolve_web_search(config_service, logger)
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
                # Capabilities can only narrow, never expand beyond what the agent is configured with.
                if web_search_tool_config is None:
                    web_search_off = "not_configured"
                elif not _is_web_search_enabled(
                    chat_query.tools, is_universal_agent=agent_id == UNIVERSAL_AGENT_ID,
                ):
                    web_search_off = "tools_filter"
                elif not caps.web_search:
                    web_search_off = "capability_off"
                else:
                    web_search_off = None
                # Only the org default can be unset; an agent with no web search attached is a choice.
                # A turn that switched web search off itself (capability, tools filter) is not asked to set it up.
                web_search_unset = (
                    web_search_off == "not_configured"
                    and web_search_coro is not None
                    and caps.web_search
                    and _is_web_search_enabled(
                        chat_query.tools, is_universal_agent=agent_id == UNIVERSAL_AGENT_ID,
                    )
                )
                if web_search_off:
                    web_search_provider = None
                    web_search_tool_config = None
                # Without this line a turn that silently lost web search looks like any other.
                logger.info(
                    "chat_stream: web search %s (agent_id=%s)",
                    f"off, reason={web_search_off}" if web_search_off
                    else f"on, provider={web_search_tool_config.get('provider')}",
                    agent_id,
                )
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

                extension_fields = get_chat_extension().query_fields(chat_query)
                attachments, previous_conversations = await keep_accessible_attachments(
                    graph_provider,
                    org_id=user_context["orgId"],
                    user_id=user_context["userId"],
                    is_service_account=bool(
                        (getattr(request.state, "user", None) or {}).get("isServiceAccount"),
                    ),
                    attachments=chat_query.attachments,
                    # The edition may rewrite the history the run reads (shared-chat rows): filter that copy.
                    previous_conversations=extension_fields.pop("previous_conversations", chat_query.previousConversations),
                    logger=logger,
                    connection_sources=connection_limit(getattr(request.state, "user", None)),
                    conversation_id=chat_query.conversationId,
                    acl_version=chat_query.aclVersion,
                )
                # Build query info
                query_info = {
                    "query": chat_query.query,
                    "limit": chat_query.limit,
                    "messages": [],
                    "previous_conversations": previous_conversations,
                    "historyLabel": chat_query.historyLabel,
                    "quickMode": chat_query.quickMode,
                    "chatMode": chat_query.chatMode,
                    "retrievalMode": chat_query.retrievalMode,
                    "filters": filters,
                    "systemPrompt": agent.get("systemPrompt"),
                    "instructions": agent.get("instructions"),
                    "custom_instructions": custom_instructions,
                    "projectInstructions": chat_query.projectInstructions,
                    "projectId": chat_query.projectId,
                    "projectToolsSelected": chat_query.strictScope and chat_query.tools is not None,
                    "timezone": chat_query.timezone,
                    "currentTime": chat_query.currentTime,
                    "toolsets": agent_toolsets,
                    "mcpServers": agent_mcp_servers,
                    "mcpServerConfigs": mcp_server_configs,
                    "apiToolsetTools": _api_toolset_tools_for_chat(agent.get("apiToolsetTools") or [], chat_query.tools),
                    "knowledge": agent_knowledge,
                    # What the request itself picked: in a turn others read, the only sources a prompt may name.
                    "pickedSourceIds": picked_source_ids(chat_query.filters, filters),
                    # The chat's files it picked: with every member's, the only ones a turn others read is told of.
                    "pickedFileIds": picked_file_ids(chat_query.filters),
                    "skills": [s["name"] for s in agent.get("skills", []) if isinstance(s, dict) and s.get("name")] or None,
                    "connector_configs": connector_configs,
                    "toolsetConfigs": toolset_configs,
                    # Attached tools left out of this run (no credentials): the prompt names them.
                    "unavailableTools": unavailable_tools,
                    "conversationId": chat_query.conversationId,
                    # Set only by the trusted in-process durable worker. This
                    # never comes from request JSON and cannot let a public
                    # caller select another run's execution identity.
                    "harnessRunId": getattr(request.state, "harness_run_id", None),
                    "harnessCheckpointStore": getattr(
                        request.state, "harness_checkpoint_store", None
                    ),
                    "harnessVirtualFileSystem": getattr(
                        request.state, "harness_virtual_filesystem", None
                    ),
                    "is_service_account": is_service_account,
                    # Whose credentials the run reads, as the readiness above checked them: the caller's own, or the
                    # agent's for a service-account agent that runs as itself.
                    "credentialOwner": agent_id if is_service_account else executing_user_id,
                    "artifact_owner_user_id": artifact_owner_user_id,
                    "isPlaceholderAgent": is_placeholder,
                    "modelName": model_name,
                    "modelKey": model_key,
                    "webSearch": web_search_provider,
                    "webSearchConfig": web_search_tool_config,
                    "webSearchUnset": web_search_unset,
                    "deepResearch": caps.deep_search,
                    "attachments": attachments,
                    "enableRecordIdShortening": chat_query.enableRecordIdShortening,
                    "includeRetrievalContext": chat_query.includeRetrievalContext,
                    "keywordSearchBudget": chat_query.keywordSearchBudget,
                    "runId": chat_query.runId,
                    "resolvedFlowRules": resolved.flow if resolved is not None else None,
                    # Per-tool approvals (`tool_approvals`) and agent sign-ins: the agent, and whether
                    # a card can be answered in this request.
                    "agentKey": None if is_placeholder else agent_id,
                    "isAssistantChat": bool(is_placeholder),
                    "chatStreaming": getattr(request.state, "chat_streaming", True) is not False,
                    "toolApproval": chat_query.toolApproval,
                    "aclVersion": chat_query.aclVersion,
                    **caller_policy.run_query_fields(service_agent=service_agent),
                }
                query_info.update(extension_fields)

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
                            org_id=org_key,
                            conversation_id=chat_query.conversationId,
                        ) if is_service_account else None
                    ),
                    entity_vector_store=entity_vector_store,
                    **caller_policy.run_loop_options(service_agent=service_agent),
                )

                # Close the bridge with this generator so its producer task is
                # cancelled on disconnect, not whenever the bridge is GC'd.
                async with aclosing(generator):
                    async for _evt in generator:
                        yield _evt
            except Exception as exc:
                error_code, user_message = classify_exception(exc)
                log_turn_end(
                    logger, "error", exc=exc, code=error_code,
                    run_id=chat_query.runId, duration_ms=round(timer.total_ms),
                )
                yield _stream_error_frame(protocol, user_message, error_code)

        async def _run() -> AsyncGenerator[str, None]:
            # Bound in the task that iterates the stream; see the same wrapper in chatbot.py.
            async with call_scope(
                caller_principal, Workload.AGENT,
                config_service=config_service, container=request.app.container,
                agent_id=None if agent_id == "agentIdPlaceholder" else agent_id,
                conversation_id=chat_query.conversationId,
            ):
                # Closed with this wrapper: a disconnect closes only the outer generator,
                # and the inner one must stop its producer task now, not at GC.
                async with aclosing(_run_unbound()) as inner:
                    async for event in inner:
                        yield event

        return StreamingResponse(
            with_resolved_stages(resolved, _run()),
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
    finally:
        # The stream binds its own task; this one may be reused after the handler returns.
        release_resolved_stages(resolved)
def _stream_error_frame(
    protocol: str, message: str, code: str = "stream_error", details: dict[str, Any] | None = None,
) -> str:
    """Terminal SSE error frame, in whichever protocol the client asked for.

    Used once the response headers are already sent, where an HTTP status is no
    longer available to carry the failure. `details` is what the UI needs to offer
    a fix (for a service-account agent's missing credentials, which tools and what each needs).
    """
    from app.modules.agents.privacy.ledger import with_unopened_provenance

    extra = {"details": details} if details is not None else {}
    if protocol == "agui":
        from app.agents.agent_loop.protocol.agui import AGUIEventType, frame

        evt = frame(AGUIEventType.RUN_ERROR, **with_unopened_provenance({"message": message, "code": code, **extra}))
        return f"event: {evt['event']}\ndata: {json.dumps(evt['data'])}\n\n"
    data = with_unopened_provenance({"message": message, "type": code, **extra})
    return f"event: error\ndata: {json.dumps(data)}\n\n"


def _api_toolset_tools_for_chat(attached: list[str], chat_tools: list[str] | None) -> list[str]:
    """`chat_tools` narrows the attached operations the same way it narrows toolsets; None keeps all."""
    if chat_tools is None:
        return attached
    wanted = set(chat_tools)
    return [ref for ref in attached if ref in wanted]


async def _assistant_api_toolset_tools(
    config_service: ConfigurationService, org_id: str, user_id: str, logger: Logger,
) -> list[str]:
    from app.services.featureflag.platform_settings import is_api_toolsets_enabled

    if not await is_api_toolsets_enabled(config_service):
        return []
    try:
        return await connected_tool_refs(ApiToolsetStore(config_service), org_id, user_id)
    except Exception as e:
        # The assistant still answers without them, like it does without toolsets above.
        logger.error(f"Error fetching API toolsets for the assistant: {e}", exc_info=True)
        return []


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
    mcp_prefetch: dict[str, dict[str, Any]] | None = None,
    include_withdrawn_toolsets: bool = False,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
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
    from app.agents.mcp.instance_scope import list_instances_for_org
    from app.agents.mcp.service import (
        MCPInstancesUnavailable,
        get_authenticated_mcp_servers,
        is_mcp_enabled,
    )
    from app.api.routes.toolsets import get_authenticated_toolsets, is_actions_enabled

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
                include_withdrawn=include_withdrawn_toolsets,
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
            mcp_instances = await list_instances_for_org(config_service, org_id, user_id)
            authenticated_mcp_servers_list = await get_authenticated_mcp_servers(
                owner_id=user_id,
                config_service=config_service,
                instances=mcp_instances,
                resolved=mcp_prefetch,
            )
        except MCPInstancesUnavailable:
            # The org's servers are unknown, not absent: the run says so instead of losing their tools (FB-3).
            raise
        except Exception as e:
            logger.error(f"Error fetching authenticated MCP servers: {e}", exc_info=True)
            authenticated_mcp_servers_list = []
    else:
        authenticated_mcp_servers_list = []

    # Get all accessible connectors for knowledge sources
    knowledge_sources = []

    try:
        # Get active connector instances accessible to the user
        user = user_doc or await get_caller_user(graph_provider, user_id)
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
        "apiToolsetTools": await _assistant_api_toolset_tools(config_service, org_id, user_id, logger),
        "knowledge": knowledge_sources,
    }, toolset_auth_by_instance

