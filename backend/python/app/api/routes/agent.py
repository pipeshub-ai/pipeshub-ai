"""
Agent API Routes
Handles agent instances, templates, chat, and permissions using graph-based architecture
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable, Mapping
from contextlib import aclosing
from logging import Logger
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator

from app import edition
from app.agent_loop_lib.modules.providers.skills.base import SkillStatus
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
from app.agents.agent_loop.skills.graph_store import GraphSkillStore, SkillVisibility
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
from app.agents.openapi import attachments as api_toolset_attachments
from app.agents.openapi.service import ApiToolsetError, connected_tool_refs
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
from app.api.middlewares.admin_gate import require_admin_caller
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
    REASONING_EFFORT_VALUES,
    validate_reasoning_effort,
)
from app.config.constants.arangodb import CollectionNames, Connectors
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import OAuthScopes, TokenScopes, config_node_constants
from app.connectors.core.base.data_store.graph_data_store import (
    GraphDataStore,
    ensure_written,
)
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
from app.harness.tool_identity.settings import instance_tool_ids_enabled
from app.modules.agents.capability_summary import (
    PARTLY_SHARED_KEY,
    fetch_connector_configs,
)
from app.modules.agents.knowledge_scope import (
    NO_KB_SELECTED_FILTER,
    UnreadableSourceLimitError,
    admit_caller_project_collections,
    project_files_knowledge,
    read_source_limit,
    resolve_agent_filters,
    service_account_knowledge,
    source_reach,
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
    max_selection_nodes,
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
from app.utils.user_lookup import (
    get_caller_user,
    get_org_or_503,
    get_user_or_503,
    is_live_identity,
)
from app.utils.user_messages import action_failed
from app.web.config import resolve_web_search
from app.web.search import PROVIDERS as WEB_SEARCH_PROVIDERS

if TYPE_CHECKING:
    from app.connectors.core.base.data_store.data_store import TransactionStore

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
SPLIT_PATH_EXPECTED_PARTS = 2  # Expected parts when splitting path with "/" separator


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
        # The argument stays for the callers; the detail leaves it out, as it may match nothing.
        super().__init__(
            detail="Agent template not found or you don't have access to it",
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


def sa_forces_org_sharing() -> bool:
    """Whether service account agents are always forced to share with the org.
    """
    return True


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


_is_live_identity = is_live_identity


async def _resolve_service_account_binding(
    service_account_user_id: str,
    graph_provider: IGraphDBProvider,
    caller_org_id: str,
) -> str:
    """The `userId` of a service account an agent is being bound to.

    Takes the account's `userId` — what the service-accounts API hands the
    screen — and gives the same `userId` back, so what is stored is what a
    picker sends and what it reads afterwards. The graph node's own key is
    deliberately not used: it is minted locally and means nothing to the
    service-accounts API, so a screen could neither send it nor match an
    existing binding against the list it shows.

    Refuses anything that is not a live service account of the caller's own
    organisation. `kind` is the only thing trusted for "is this a service
    account". It is told to the graph by user management rather than worked
    out here, and without it this would have to fall back to the reserved
    address, which would let an administrator aim an agent at a colleague
    instead — the whole point of binding is to stop one person's reach being
    lent out.

    A deleted account is refused too. Soft-delete leaves the node in place and
    only clears `isActive` (`entity.py` `_handle_user_deleted`), so `kind`
    alone would still accept it and an agent would keep searching with the
    reach of an account an administrator has removed.

    The same 400 covers missing, wrong-org, not-a-service-account and deleted,
    so this cannot be used to find out which user ids exist in another
    organisation.
    """
    wanted = str(service_account_user_id or "").strip()
    if not wanted:
        raise InvalidRequestError("Choose a service account for this agent to search as.")

    # raise_on_error, so a database that is down surfaces as a failure rather
    # than as "no such account" — an administrator told the wrong one of those
    # goes looking for a problem that is not there.
    account = await graph_provider.get_user_by_user_id(wanted, raise_on_error=True)
    caller_org = str(caller_org_id or "").strip()
    account_org = str((account or {}).get("orgId") or "").strip()

    if (
        not account
        or not caller_org
        or account_org != caller_org
        or (account or {}).get("kind") != "service"
        or not _is_live_identity(account)
    ):
        raise InvalidRequestError(
            "That service account was not found in this organisation. Choose one "
            "from Workspace \u2192 Service accounts, or create one there first."
        )

    return wanted


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


async def _checked_service_account_binding(
    requested: object,
    graph_provider: IGraphDBProvider,
    caller_org_id: str,
    *,
    is_service_account: bool,
    admin_check: Callable[[], Awaitable[None]],
    current: object = None,
) -> str | None:
    """The binding to store, refusing one the agent could not use, and one
    whoever is asking is not entitled to set.

    `None` is allowed on any agent. It means "search as the creator", which is
    where every unbound agent already sits, and every agent document carries
    this field as null — so a client that reads an agent and sends it back
    unchanged must not be turned away.

    Anything else is only meaningful on a service agent, and is refused rather
    than quietly dropped: dropping it would leave an agent searching as its
    creator, the wider identity, while whoever asked believed they had narrowed
    it. On update, "is a service agent" means what the agent will be once the
    request is applied, so one request may convert an agent and bind it.
    """
    stored = str(current or "").strip() or None
    asked = None if requested is None else str(requested or "").strip()

    # Only an administrator picks the account, and that is what `admin_check`
    # refuses. A value that is not changing passes for anyone, because every
    # agent document carries this field and a client that reads an agent and
    # saves it back sends it every time — an owner editing a bound agent's
    # name would otherwise be turned away. Clearing counts as a change: it
    # returns the agent to searching as its creator, which is the wider
    # identity an administrator bound it to get away from.
    if asked != stored:
        await admin_check()

    if asked is None:
        return None
    if not is_service_account:
        raise InvalidRequestError(
            "Only a service agent searches as a service account. Make this "
            "a service agent first, or leave the binding alone."
        )
    return await _resolve_service_account_binding(
        asked, graph_provider, caller_org_id,
    )


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


def _validate_required_fields(
    data: dict[str, Any], required_fields: list[str], optional_fields: tuple[str, ...] = (),
) -> None:
    """The caller's text fields: each required one a non-blank string, each optional one a string or None. A value of
    another type is the caller's mistake (a 400), not a failure while the document is built."""
    for field in (*required_fields, *optional_fields):
        if data.get(field) is not None and not isinstance(data[field], str):
            raise InvalidRequestError(f"'{field}' must be text")
    for field in required_fields:
        if not data.get(field) or not data[field].strip():
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


_SUPPORTED_WEB_SEARCH_PROVIDERS = frozenset(WEB_SEARCH_PROVIDERS)


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


def _parse_toolsets(raw_toolsets: list[Any]) -> dict[str, dict[str, Any]]:
    """Parse toolsets with their tools.

    The key of the returned dict is the toolset name (lowercase); with
    instance-scoped tool ids on (P2.2), a toolset bound to an instance is keyed
    by name and instance, so two instances of one type stay two toolsets.
    Each value carries the parsed fields: its ``name`` and optional instanceId.
    """
    toolsets_with_tools = {}
    per_instance = instance_tool_ids_enabled()

    if not raw_toolsets or not isinstance(raw_toolsets, list):
        return toolsets_with_tools

    for toolset_data in raw_toolsets:
        if not isinstance(toolset_data, dict):
            continue

        _validate_required_fields(toolset_data, [], ("name",))
        toolset_name = (toolset_data.get("name") or "").lower().strip()
        if not toolset_name:
            continue

        display_name = toolset_data.get("displayName", toolset_name.replace("_", " ").title())
        toolset_type = toolset_data.get("type", "app")
        tools_list = toolset_data.get("tools", [])
        # New field: admin-created instance UUID
        instance_id = toolset_data.get("instanceId", None)
        instance_name = toolset_data.get("instanceName", None)

        entry_key = f"{toolset_name}@{instance_id}" if per_instance and instance_id else toolset_name
        if entry_key not in toolsets_with_tools:
            toolsets_with_tools[entry_key] = {
                "name": toolset_name,
                "displayName": display_name,
                "type": toolset_type,
                "tools": [],
                "instanceId": instance_id,
                "instanceName": instance_name,
            }
        elif instance_id and not toolsets_with_tools[entry_key].get("instanceId"):
            # Update instanceId if not yet set
            toolsets_with_tools[entry_key]["instanceId"] = instance_id
            toolsets_with_tools[entry_key]["instanceName"] = instance_name

        for tool in tools_list:
            if isinstance(tool, dict):
                tool_name = tool.get("name", "")
                if tool_name:
                    toolsets_with_tools[entry_key]["tools"].append({
                        "name": tool_name,
                        "fullName": tool.get("fullName", f"{toolset_name}.{tool_name}"),
                        "description": tool.get("description", "")
                    })

    return toolsets_with_tools


async def _refuse_limit_over_the_cap(
    knowledge_sources: dict[str, dict[str, Any]],
    graph_provider: IGraphDBProvider,
    org_id: str,
    logger: Logger,
) -> None:
    """Refuse to save sources limited to more nodes than one turn may search:
    every turn of such an agent would end in "too many records"."""
    limits = [read_source_limit(source.get("filters")) for source in knowledge_sources.values()]
    group_ids = [i for limit in limits for i in limit["recordGroups"]]
    record_ids = [i for limit in limits for i in limit["records"]]
    if not (group_ids or record_ids):
        return
    cap = max_selection_nodes()
    try:
        nodes = await graph_provider.get_selection_nodes(
            org_id, group_ids=group_ids, record_ids=record_ids, exact_record_ids=[], limit=cap,
        )
    except Exception as e:
        # The same cap is applied on every turn; a failed count must not block a save.
        error_class.log_failure(logger, "agent.route.failed", e, op="size_knowledge_limit", include_error_text=False)
        return
    if len(nodes["groups"]) + len(nodes["records"]) > cap:
        raise InvalidRequestError(
            f"The folders and items this agent is limited to hold more than {cap:,} records, "
            "too many to search at once. Limit it to fewer items, or use the whole source."
        )


def _parse_knowledge_sources(raw_knowledge: list[Any]) -> dict[str, dict[str, Any]]:
    """Parse knowledge sources"""
    knowledge_sources = {}

    if not raw_knowledge or not isinstance(raw_knowledge, list):
        return knowledge_sources

    for knowledge_data in raw_knowledge:
        if not isinstance(knowledge_data, dict):
            continue

        _validate_required_fields(knowledge_data, [], ("connectorId",))
        connector_id = (knowledge_data.get("connectorId") or "").strip()
        if not connector_id:
            continue

        filters = knowledge_data.get("filters", {})
        try:
            # A limit that cannot be read is refused: saved as "no filters" it
            # would let the agent search the whole source.
            read_source_limit(filters)
        except UnreadableSourceLimitError as exc:
            raise InvalidRequestError(
                f"The filters of knowledge source '{connector_id}' cannot be read. "
                "Give recordGroups and records as lists of ids, or leave them out "
                "to use the whole source."
            ) from exc
        if isinstance(filters, str):
            filters = json.loads(filters) if filters.strip() else {}
        if not isinstance(filters, dict):
            filters = {}

        knowledge_sources[connector_id] = {
            "connectorId": connector_id,
            "filters": filters
        }

    return knowledge_sources


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


async def _create_toolset_edges(
    agent_key: str,
    toolsets_with_tools: dict[str, dict[str, Any]],
    user_info: dict[str, Any],
    user_key: str,
    graph_provider: IGraphDBProvider,
    logger: Logger,
    transaction: str | None = None,
    written_toolset_keys: list[str] | None = None,
    written_tool_keys: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Create toolset nodes and edges for agent using batch operations.

    The agent is linked to the toolsets last, once each has its tools, so nothing reading
    the agent sees a toolset half built. ``written_toolset_keys`` and ``written_tool_keys``,
    when given, receive each node key before any write starts, so a caller can remove
    whatever this left behind if it fails midway.
    """
    from app.agents.constants.toolset_constants import normalize_app_name

    created_toolsets = []
    failed_toolsets = []
    time = get_epoch_timestamp_in_ms()

    if not toolsets_with_tools:
        return created_toolsets, failed_toolsets

    # Prepare all toolset nodes
    toolset_nodes = []
    toolset_mapping = {}  # Map toolset_name to toolset_key

    for entry_key, toolset_data in toolsets_with_tools.items():
        toolset_name = toolset_data.get("name", entry_key)
        toolset_key = str(uuid.uuid4())
        if written_toolset_keys is not None:
            written_toolset_keys.append(toolset_key)
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
        toolset_mapping[entry_key] = {
            "name": toolset_name,
            "key": toolset_key,
            "displayName": display_name,
            "tools": tools_list
        }

    # Batch create all toolset nodes
    result = await graph_provider.batch_upsert_nodes(
        toolset_nodes, CollectionNames.AGENT_TOOLSETS.value, transaction=transaction
    )
    if not result:
        return created_toolsets, [{"name": "all", "error": "Failed to create toolset nodes"}]

    # Prepare all tool nodes and edges
    tool_nodes = []
    toolset_tool_edges = []
    tool_mapping = {}  # Map full_name to tool_key

    for toolset_info in toolset_mapping.values():
        toolset_name = toolset_info["name"]
        for tool_data in toolset_info["tools"]:
            tool_name = tool_data["name"]
            full_name = tool_data["fullName"]
            description = tool_data["description"]

            tool_key = str(uuid.uuid4())
            if written_tool_keys is not None:
                written_tool_keys.append(tool_key)

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
        result = await graph_provider.batch_upsert_nodes(
            tool_nodes, CollectionNames.AGENT_TOOLS.value, transaction=transaction
        )
        if not result:
            raise RuntimeError("Failed to create tool nodes")

    # Batch create toolset -> tool edges. Re-raise for the same reason as above.
    if toolset_tool_edges:
        result = await graph_provider.batch_create_edges(
            toolset_tool_edges, CollectionNames.TOOLSET_HAS_TOOL.value, transaction=transaction
        )
        if not result:
            raise RuntimeError("Failed to link tools to their toolsets")

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
    result = await graph_provider.batch_create_edges(
        agent_toolset_edges, CollectionNames.AGENT_HAS_TOOLSET.value, transaction=transaction
    )
    if not result:
        raise RuntimeError("Failed to link toolsets to the agent")

    # Build response with created toolsets and tools
    for toolset_info in toolset_mapping.values():
        toolset_name = toolset_info["name"]
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


# Far above any real attachment; without them a request could write thousands of nodes.
MAX_MCP_SERVERS_PER_AGENT = 50
MAX_TOOLS_PER_MCP_SERVER = 500
_MAX_MCP_TOOL_NAME_CHARS = 200
_MAX_MCP_TOOL_FULL_NAME_CHARS = 300
_MAX_MCP_TOOL_DESCRIPTION_CHARS = 2000
_MAX_MCP_DISPLAY_NAME_CHARS = 200


def _has_control_chars(text: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in text)


async def _checked_api_toolset_tools(
    raw: object, services: dict[str, Any], org_id: str, *, service_account: bool,
) -> list[str]:
    """The API toolset operations to store on an agent, each one checked to exist in the org."""
    if raw == []:
        return []
    config_service: ConfigurationService = services["config_service"]
    try:
        return await api_toolset_attachments.checked_attachments(
            ApiToolsetStore(config_service), org_id, raw, service_account=service_account,
        )
    except ApiToolsetError as e:
        raise InvalidRequestError(str(e)) from e


def _parse_mcp_servers(raw_mcp_servers: list[Any]) -> dict[str, dict[str, Any]]:
    """Parse attached MCP server references with their tools.

    Unlike `_parse_toolsets` (keyed by name — built-in toolset types have a
    single instance system-wide), MCP servers are keyed by `instanceId`: an
    agent can attach several distinct instances, but never two instances of
    the same `typeId` — that's enforced here so `mcp_{server_type}_{tool}`
    tool names stay unique at chat time (see `get_authenticated_mcp_servers`
    in `app/agents/mcp/service.py`).

    `allTools` attaches whatever the server offers at chat time, tools it adds later
    included; `tools` is then only the snapshot the builder shows. Without it the agent
    gets exactly `tools`, so an empty list would silently give it nothing.
    """
    from app.agents.mcp.naming import build_namespaced_tool_name, namespace_key

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
        if type_id and not instance_tool_ids_enabled():
            existing_instance_id = seen_type_ids.get(type_id)
            if existing_instance_id and existing_instance_id != instance_id:
                raise InvalidRequestError(
                    f"Cannot attach two MCP server instances of the same type ('{type_id}') to one agent."
                )
            seen_type_ids[type_id] = instance_id

        display_name = str(mcp_data.get("displayName") or name.replace("_", " ").title())[:_MAX_MCP_DISPLAY_NAME_CHARS]
        tools_list = mcp_data.get("tools") or []

        server = mcp_servers_with_tools.setdefault(instance_id, {
            "name": name,
            "displayName": display_name,
            "typeId": type_id,
            "allTools": False,
            "tools": [],
        })
        server["allTools"] = server["allTools"] or mcp_data.get("allTools") is True

        seen_tools = {tool["name"] for tool in server["tools"]}
        for tool in tools_list if isinstance(tools_list, list) else []:
            if not isinstance(tool, dict):
                continue
            tool_name = str(tool.get("name") or "").strip()
            if not tool_name or tool_name in seen_tools:
                continue
            if len(server["tools"]) >= MAX_TOOLS_PER_MCP_SERVER:
                if server["allTools"]:
                    # The agent gets every tool anyway; the saved list is only what's on show.
                    break
                raise InvalidRequestError(
                    f"An agent can use at most {MAX_TOOLS_PER_MCP_SERVER} tools of one MCP server; "
                    "attach all of its tools instead."
                )
            full_name = str(tool.get("fullName") or build_namespaced_tool_name(namespace_key(type_id, name), tool_name))
            if (
                len(tool_name) > _MAX_MCP_TOOL_NAME_CHARS or len(full_name) > _MAX_MCP_TOOL_FULL_NAME_CHARS
                or _has_control_chars(tool_name) or _has_control_chars(full_name)
            ):
                raise InvalidRequestError(f"MCP tool name {tool_name[:40]!r} is not a valid tool name.")
            seen_tools.add(tool_name)
            server["tools"].append({
                "name": tool_name,
                # Informational since the loader matches by raw name; when the client didn't
                # send one, store what discovery would call it rather than an invented name.
                "fullName": full_name,
                "description": str(tool.get("description") or "")[:_MAX_MCP_TOOL_DESCRIPTION_CHARS],
            })

        if len(mcp_servers_with_tools) > MAX_MCP_SERVERS_PER_AGENT:
            raise InvalidRequestError(f"An agent can have at most {MAX_MCP_SERVERS_PER_AGENT} MCP servers.")

    empty = [s["displayName"] for s in mcp_servers_with_tools.values() if not s["allTools"] and not s["tools"]]
    if empty:
        raise InvalidRequestError(
            f"Choose at least one tool for MCP server(s) {', '.join(empty)}, or attach all of their tools."
        )
    return mcp_servers_with_tools


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
def _mcp_servers_for_chat(
    attached: list[dict[str, Any]], enabled_tools: set[str] | None,
) -> list[dict[str, Any]]:
    """The attachments to load, narrowed to the chat's tool selection when it has one.

    `tools: None` makes the loader discover everything a server offers — an `allTools`
    attachment without a selection, and the assistant's servers, which have no list. A
    selection matches the saved tool names and, for those two, the `mcp_{namespace}_`
    prefix as well, so a tool the server added after the save still counts.
    """
    from app.agents.mcp.naming import instance_tag, namespace_key
    from app.agents.mcp.service import server_namespace

    if enabled_tools is None:
        return [{**server, "tools": None} if server.get("allTools") else server for server in attached]

    # A tagged name (`mcp_github_ab12_x`) also starts with the plain prefix (`mcp_github_`), so
    # each selected name belongs to the server with the longest prefix it matches. A graph
    # attachment carries no namespace, and the listing a selection came from tags it whenever an
    # older instance of its type is visible, so its tagged prefix counts too.
    by_prefix: dict[str, str] = {}
    for server in attached:
        if server.get("tools") is not None and not server.get("allTools"):
            continue
        by_prefix[f"mcp_{server_namespace(server)}_"] = server["instanceId"]
        if not server.get("namespace"):
            key = namespace_key(server.get("typeId"), server.get("name"))
            by_prefix[f"mcp_{key}_{instance_tag(server['instanceId'])}_"] = server["instanceId"]
    prefixed: dict[str, list[dict[str, Any]]] = {}
    for full_name in enabled_tools:
        matches = [prefix for prefix in by_prefix if isinstance(full_name, str) and full_name.startswith(prefix)]
        if not matches:
            continue
        prefix = max(matches, key=len)
        if tool_name := full_name[len(prefix):]:
            prefixed.setdefault(by_prefix[prefix], []).append({"name": tool_name, "fullName": full_name})

    selected: list[dict[str, Any]] = []
    for server in attached:
        tools = [tool for tool in server.get("tools") or [] if tool.get("fullName") in enabled_tools]
        known = {tool.get("fullName") for tool in tools}
        tools += [tool for tool in prefixed.get(server["instanceId"], []) if tool["fullName"] not in known]
        if tools:
            selected.append({**server, "tools": tools})
    return selected


async def _assert_mcp_servers_exist(
    mcp_servers_with_tools: dict[str, dict[str, Any]],
    config_service: ConfigurationService,
    org_id: str,
    user_id: str,
) -> dict[str, dict[str, Any]]:
    """An agent may only attach instances the caller can see — the org's, or their own
    personal ones; anything else would save fine and then fail every chat as "not found".
    Returns the resolved instances by id."""
    from app.edition_config import get_mcp_instance_resolved

    instance_ids = list(mcp_servers_with_tools)
    instances = await asyncio.gather(
        *[get_mcp_instance_resolved(instance_id, config_service, org_id, user_id) for instance_id in instance_ids]
    )
    missing = [
        mcp_servers_with_tools[instance_id].get("displayName") or instance_id
        for instance_id, instance in zip(instance_ids, instances)
        if not instance
    ]
    if missing:
        raise InvalidRequestError(f"MCP server(s) not found: {', '.join(missing)}.")
    return {instance_id: instance for instance_id, instance in zip(instance_ids, instances) if instance}


def _bind_mcp_attachments_to_instances(
    mcp_servers_with_tools: dict[str, dict[str, Any]], instances: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """The attachments with their name and type taken from the stored servers, not the request:
    otherwise a request could label a server anything, or leave out its type to slip past the
    one-server-per-type rule."""
    bound: dict[str, dict[str, Any]] = {}
    seen_type_ids: dict[str, str] = {}
    for instance_id, attachment in mcp_servers_with_tools.items():
        instance = instances.get(instance_id) or {}
        type_id = instance.get("typeId") or None
        if type_id:
            other = seen_type_ids.setdefault(type_id, instance_id)
            if other != instance_id:
                raise InvalidRequestError(
                    f"Cannot attach two MCP server instances of the same type ('{type_id}') to one agent."
                )
        bound[instance_id] = {**attachment, "name": instance.get("name") or attachment["name"], "typeId": type_id}
    return bound


async def _attached_personal_instances(
    instance_ids: set[str], config_service: ConfigurationService, org_id: str,
) -> dict[str, dict[str, Any]]:
    """The personal servers among `instance_ids`, whoever owns them. Looked up as the org sees
    them, not as the editor does: a co-editor can't resolve the owner's personal server, and a
    lookup that drops what it can't see would let sharing slip past the rule."""
    from app.agents.mcp.service import find_personal_instance_for_admin

    found = await asyncio.gather(*[
        find_personal_instance_for_admin(instance_id, config_service, org_id, raise_on_error=True)
        for instance_id in instance_ids
    ])
    return {instance["_id"]: instance for instance in found if instance}


def _mcp_attachments_unreadable() -> AgentError:
    return AgentError(
        "This agent's MCP servers couldn't be checked, so it can't be shared right now. Please try again in a moment.",
        status_code=503,
    )


def _assert_no_personal_mcp_servers(
    instances: dict[str, dict[str, Any]], *, shared_with_org: bool, service_account: bool,
) -> None:
    """A personal MCP server holds one user's credentials and is visible to that user only;
    an agent other people run could never load it."""
    if not (shared_with_org or service_account):
        return
    from app.agents.mcp.service import is_personal

    personal = [instance.get("name") or instance_id for instance_id, instance in instances.items() if is_personal(instance)]
    if personal:
        raise InvalidRequestError(
            f"Personal MCP servers ({', '.join(personal)}) can't be attached to an agent that is shared "
            "with the organization or runs as a service account."
        )


async def _assert_toolsets_visible(
    toolsets_with_tools: dict[str, dict[str, Any]],
    config_service: ConfigurationService,
    org_id: str,
    user_id: str,
) -> dict[str, dict[str, Any]]:
    """An agent may only attach toolset instances the editor can see — the org's, or their own
    personal ones; anything else would save fine and then fail every chat. Attachments without an
    instance (saved before instances existed) are left as they are. Returns the instances by id."""
    from app.agents.toolset_store import is_personal
    from app.edition_config import get_toolset_by_id

    attached = {ts["instanceId"]: ts for ts in toolsets_with_tools.values() if ts.get("instanceId")}
    found = await asyncio.gather(*[get_toolset_by_id(instance_id, config_service, org_id) for instance_id in attached])
    visible = {
        instance_id: instance
        for instance_id, instance in zip(attached, found)
        if instance and (not is_personal(instance) or instance.get("createdBy") == user_id)
    }
    missing = [ts.get("instanceName") or ts.get("displayName") or instance_id for instance_id, ts in attached.items() if instance_id not in visible]
    if missing:
        raise InvalidRequestError(f"Action(s) not found: {', '.join(missing)}.")
    return visible


def _names_an_instance(toolsets_with_tools: dict[str, dict[str, Any]]) -> bool:
    return any(ts.get("instanceId") for ts in toolsets_with_tools.values())


async def _attached_personal_toolsets(
    instance_ids: set[str], config_service: ConfigurationService, org_id: str,
) -> dict[str, dict[str, Any]]:
    """The personal toolset instances among `instance_ids`, whoever owns them (as for MCP servers,
    a co-editor can't resolve the owner's, and dropping what it can't see would let sharing past)."""
    from app.agents.toolset_store import find_personal_instance

    found = await asyncio.gather(*[
        find_personal_instance(config_service, org_id, instance_id, raise_on_error=True) for instance_id in instance_ids
    ])
    return {instance["_id"]: instance for instance in found if instance}


def _toolset_attachments_unreadable() -> AgentError:
    return AgentError(
        "This agent's actions couldn't be checked, so it can't be shared right now. Please try again in a moment.",
        status_code=503,
    )


def _assert_no_personal_toolsets(
    instances: dict[str, dict[str, Any]], *, shared_with_org: bool, service_account: bool,
) -> None:
    """A personal action holds one user's sign-in and is visible to that user only; an agent other
    people run could never use it."""
    if not (shared_with_org or service_account):
        return
    from app.agents.toolset_store import is_personal

    personal = [instance.get("instanceName") or instance_id for instance_id, instance in instances.items() if is_personal(instance)]
    if personal:
        raise InvalidRequestError(
            f"Personal actions ({', '.join(personal)}) can't be attached to an agent that is shared "
            "with the organization or runs as a service account."
        )


async def _assert_service_account_knowledge(
    connector_ids: Iterable[str], graph_provider: IGraphDBProvider, org_id: str, user_id: str, *, bound: bool,
) -> None:
    """A service-account agent is run by anyone in the organisation on one identity, so it may
    only be given sources shared with the whole organisation: no personal connector, and a
    collection only when it runs as a bound service account. A source that is not the
    organisation's (or no longer exists) is refused too. The run applies the same rule."""
    ids = list(dict.fromkeys(connector_ids))
    if not ids:
        return
    try:
        apps = await graph_provider.get_org_apps(org_id, active_only=False, raise_on_error=True, app_ids=ids)
    except Exception as e:
        error_class.mark_error(e, hide_text=True)  # the store's text can quote what was sent
        raise AgentError(
            "This agent's knowledge couldn't be checked, so it can't be saved right now. Please try again in a moment.",
            status_code=503,
        ) from e
    missing = len(ids) - len(apps)
    if missing:
        raise InvalidRequestError(f"{missing} knowledge source(s) of this agent were not found in your organization.")
    kb = Connectors.KNOWLEDGE_BASE.value

    def label(app: dict[str, Any]) -> str:
        # Only the editor's own source is named.
        if app.get("createdBy") != user_id:
            return "one that someone else owns"
        return app.get("name") or str(app.get("_key") or app.get("id"))

    personal = [
        label(a) for a in apps if a.get("type") != kb and a.get("scope") == ConnectorScope.PERSONAL.value
    ]
    if personal:
        raise InvalidRequestError(
            f"Personal sources ({', '.join(personal)}) can't be attached to an agent that runs as a service account."
        )
    collections = [] if bound else [label(a) for a in apps if a.get("type") == kb]
    if collections:
        raise InvalidRequestError(
            f"Collections ({', '.join(collections)}) can be attached to a service-account agent only when it is "
            "bound to a service account."
        )


async def _read_agent_mcp_attachments(
    agent_full_id: str, graph_provider: IGraphDBProvider, transaction: str,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """The agent's MCP server nodes and their tool nodes, as (server keys, server ids, tool keys,
    tool ids)."""
    server_keys: list[str] = []
    server_ids: list[str] = []
    for edge in await graph_provider.get_edges_from_node(
        agent_full_id, CollectionNames.AGENT_HAS_MCP_SERVER.value, transaction=transaction,
    ):
        server_id = edge.get("_to")
        if server_id:
            server_ids.append(server_id)
            parts = server_id.split("/", 1)
            if len(parts) == SPLIT_PATH_EXPECTED_PARTS:
                server_keys.append(parts[1])

    tool_keys: list[str] = []
    tool_ids: list[str] = []
    for server_id in server_ids:
        for edge in await graph_provider.get_edges_from_node(
            server_id, CollectionNames.MCP_SERVER_HAS_TOOL.value, transaction=transaction,
        ):
            tool_id = edge.get("_to")
            if tool_id:
                tool_ids.append(tool_id)
                parts = tool_id.split("/", 1)
                if len(parts) == SPLIT_PATH_EXPECTED_PARTS:
                    tool_keys.append(parts[1])
    return server_keys, server_ids, tool_keys, tool_ids


def _rollback_failed(logger: Logger, exc: BaseException) -> None:
    """Rolling back a failed save failed too; the save's own failure is the request's line."""
    error_class.log_failure(logger, "agent.route.failed", exc, op="rollback", include_error_text=False)


def _cleanup_failed(logger: Logger, exc: BaseException, **fields: object) -> None:
    """A best-effort tidy-up after a failed save failed too; the save's own failure is the request's line."""
    error_class.log_failure(logger, "agent.route.failed", exc, op="cleanup", include_error_text=False, **fields)


async def _remove_mcp_servers(
    server_keys: list[str], tool_keys: list[str], graph_provider: IGraphDBProvider, logger: Logger,
) -> None:
    """Best-effort removal of MCP server nodes, their tool nodes and every link to them;
    failures are only logged."""
    for key in server_keys:
        server_id = f"{CollectionNames.AGENT_MCP_SERVERS.value}/{key}"
        for edge_collection in (CollectionNames.AGENT_HAS_MCP_SERVER.value, CollectionNames.MCP_SERVER_HAS_TOOL.value):
            try:
                await graph_provider.delete_all_edges_for_node(server_id, edge_collection)
            except Exception as cleanup_error:
                _cleanup_failed(logger, cleanup_error, collection=edge_collection)
    for keys, collection in (
        (tool_keys, CollectionNames.AGENT_TOOLS.value), (server_keys, CollectionNames.AGENT_MCP_SERVERS.value),
    ):
        if not keys:
            continue
        try:
            await graph_provider.delete_nodes(keys, collection)
        except Exception as cleanup_error:
            _cleanup_failed(logger, cleanup_error, collection=collection, nodes=len(keys))


async def _create_mcp_server_edges(
    agent_key: str,
    mcp_servers_with_tools: dict[str, dict[str, Any]],
    user_info: dict[str, Any],
    user_key: str,
    graph_provider: IGraphDBProvider,
    logger: Logger,
    transaction: str | None = None,
    *,
    raise_on_failure: bool = False,
    written_server_keys: list[str] | None = None,
    written_tool_keys: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Create MCP server nodes and edges for an agent using batch operations.

    Mirrors `_create_toolset_edges`, keyed by `instanceId` instead of name.
    MCP server nodes never carry credentials — auth is resolved at chat time
    from `/services/mcp/credentials/{instanceId}/{ownerId}` (etcd), same as
    the attach-time Node/Python validation that already rejects secrets here.
    A node write the store refuses (answers False) is returned as a failure unless
    `raise_on_failure`; one that raises always propagates. A caller running this in its
    own transaction needs `raise_on_failure`: on Neo4j a failed
    statement has already doomed that transaction, and a deadlock must reach
    the retry helper as itself.

    The agent is linked to the servers last, once each has its tools, so nothing reading the
    agent sees a server half built. `written_server_keys` and `written_tool_keys`, when given,
    receive each node key before any write starts, so a caller can remove whatever this left
    behind if it fails midway: on Neo4j without explicit transactions a rollback undoes
    nothing.
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
        if written_server_keys is not None:
            written_server_keys.append(mcp_server_key)
        name = mcp_data["name"]
        display_name = mcp_data["displayName"]
        type_id = mcp_data.get("typeId")
        tools_list = mcp_data["tools"]

        mcp_server_node = {
            "_key": mcp_server_key,
            "instanceId": instance_id,
            "name": name,
            "displayName": display_name,
            "allTools": bool(mcp_data.get("allTools")),
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
    result = await graph_provider.batch_upsert_nodes(
        mcp_server_nodes, CollectionNames.AGENT_MCP_SERVERS.value, transaction=transaction
    )
    if not result:
        if raise_on_failure:
            ensure_written(result, "the agent's MCP servers")
        return created_mcp_servers, [{"name": "all", "error": "Failed to create MCP server nodes"}]

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
            if written_tool_keys is not None:
                written_tool_keys.append(tool_key)

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
        result = await graph_provider.batch_upsert_nodes(
            tool_nodes, CollectionNames.AGENT_TOOLS.value, transaction=transaction
        )
        if not result:
            raise RuntimeError("Failed to create MCP tool nodes")

    # Batch create mcpServer -> tool edges. Re-raise: a swallowed failure here leaves tools
    # listed in the response with no MCP_SERVER_HAS_TOOL edge actually connecting them.
    if mcp_server_tool_edges:
        ensure_written(await graph_provider.batch_create_edges(
            mcp_server_tool_edges, CollectionNames.MCP_SERVER_HAS_TOOL.value, transaction=transaction
        ), "the MCP servers' tool links")

    agent_mcp_server_edges = [
        {
            "_from": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
            "_to": f"{CollectionNames.AGENT_MCP_SERVERS.value}/{mcp_info['key']}",
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
        }
        for mcp_info in mcp_server_mapping.values()
    ]

    # Batch create agent -> mcpServer edges, last. Re-raise: every caller turns it into a
    # failed save, and swallowing it would report "success" with servers never linked.
    ensure_written(await graph_provider.batch_create_edges(
        agent_mcp_server_edges, CollectionNames.AGENT_HAS_MCP_SERVER.value, transaction=transaction
    ), "the agent's MCP server links")

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


async def _unlink_failed_mcp_servers(
    agent_full_id: str,
    accounted: set[str],
    graph_provider: IGraphDBProvider,
    logger: Logger,
) -> bool:
    """After an MCP server failed to attach, remove whatever of it stayed linked to
    the agent: a rollback undoes nothing on Neo4j with NEO4J_CREATE_TRANSACTIONS=false,
    so the agent->server link may have landed before a later write failed. Links
    not in *accounted* (servers attached, or already handled) are that leftover.
    Returns whether the agent is known to be free of it."""
    agent_link = CollectionNames.AGENT_HAS_MCP_SERVER.value
    tool_link = CollectionNames.MCP_SERVER_HAS_TOOL.value
    try:
        edges = await graph_provider.get_edges_from_node_with_target_name(agent_full_id, agent_link, raise_on_error=True)
    except Exception as read_error:
        _cleanup_failed(logger, read_error, collection=agent_link)
        return False
    leftover = [e["_to"] for e in edges or [] if e.get("_to") and e["_to"] not in accounted]
    accounted.update(leftover)
    for server_id in leftover:
        try:
            await graph_provider.delete_all_edges_for_node(server_id, agent_link)
        except Exception as cleanup_error:
            _cleanup_failed(logger, cleanup_error, collection=agent_link)
            return False
        # Unlinked, the server and its tools are never read again, so this is tidy-up only.
        try:
            tool_keys = [
                e["_to"].split("/", 1)[1]
                for e in await graph_provider.get_edges_from_node(server_id, tool_link) or []
                if "/" in (e.get("_to") or "")
            ]
            await graph_provider.delete_all_edges_for_node(server_id, tool_link)
            if tool_keys:
                await graph_provider.delete_nodes(tool_keys, CollectionNames.AGENT_TOOLS.value)
            await graph_provider.delete_nodes([server_id.split("/", 1)[1]], CollectionNames.AGENT_MCP_SERVERS.value)
        except Exception as cleanup_error:
            _cleanup_failed(logger, cleanup_error, collection=CollectionNames.AGENT_MCP_SERVERS.value)
    return True


async def _attach_mcp_servers_to_new_agent(
    agent_key: str,
    mcp_servers_with_tools: dict[str, dict[str, Any]],
    user_info: dict[str, Any],
    user_key: str,
    graph_provider: IGraphDBProvider,
    logger: Logger,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Attach each MCP server to an agent whose create has committed, one
    transaction per server, so one that fails is reported and leaves the agent
    and the other servers in place (the create's "partial_success")."""
    store = GraphDataStore(logger, graph_provider)
    agent_full_id = f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}"
    created: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    accounted: set[str] = set()

    async def attach(
        tx_store: "TransactionStore", one: dict[str, dict[str, Any]], server_keys: list[str], tool_keys: list[str],
    ) -> list[dict[str, Any]]:
        attached, _ = await _create_mcp_server_edges(
            agent_key, one, user_info, user_key, graph_provider, logger,
            transaction=tx_store.txn, raise_on_failure=True,
            written_server_keys=server_keys, written_tool_keys=tool_keys,
        )
        return attached

    for instance_id, mcp_data in mcp_servers_with_tools.items():
        server_keys: list[str] = []
        tool_keys: list[str] = []
        try:
            attached = await store.execute_in_transaction(
                attach, {instance_id: mcp_data}, server_keys, tool_keys, explicit=True,
            )
        except Exception as e:
            error_class.log_failure(
                logger, "agent.route.failed", e, op="attach_mcp_server", include_error_text=False, mcp_server_id=instance_id,
            )
            name = mcp_data["displayName"]
            if await _unlink_failed_mcp_servers(agent_full_id, accounted, graph_provider, logger):
                # The agent is linked last, so a failed try leaves nodes linked to nothing; without
                # a real transaction they outlive the rollback.
                await _remove_mcp_servers(server_keys, tool_keys, graph_provider, logger)
                error = (
                    f"We couldn't add {name} to the agent. "
                    "Edit the agent to add it again; if it keeps failing, contact your admin."
                )
            else:
                error = (
                    f"We couldn't fully add {name} to the agent, so it may be partly attached. "
                    f"Edit the agent to remove {name} and add it again; if it keeps failing, contact your admin."
                )
            failed.append({"name": mcp_data["name"], "error": error})
            continue
        created.extend(attached)
        accounted.update(f"{CollectionNames.AGENT_MCP_SERVERS.value}/{server['key']}" for server in attached)
    return created, failed


async def _create_knowledge_edges(
    agent_key: str,
    knowledge_sources: dict[str, dict[str, Any]],
    user_key: str,
    graph_provider: IGraphDBProvider,
    logger: Logger,
    transaction: str | None = None,
    written_keys: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Create knowledge nodes and edges for agent using batch operations.

    ``written_keys``, when given, receives each knowledge node key before any write
    starts, so a caller can remove whatever this left behind if it fails midway.
    """
    created_knowledge = []
    time = get_epoch_timestamp_in_ms()

    if not knowledge_sources:
        return created_knowledge

    # Prepare all knowledge nodes
    knowledge_nodes = []
    knowledge_mapping = {}

    for connector_id, knowledge_data in knowledge_sources.items():
        knowledge_key = str(uuid.uuid4())
        if written_keys is not None:
            written_keys.append(knowledge_key)
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

    # Raise, as the toolset and MCP helpers do: returning quietly reported success
    # for an agent whose previous knowledge update_agent had just removed.
    result = await graph_provider.batch_upsert_nodes(
        knowledge_nodes, CollectionNames.AGENT_KNOWLEDGE.value, transaction=transaction
    )
    if not result:
        raise RuntimeError("Failed to create knowledge nodes")

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
    result = await graph_provider.batch_create_edges(
        agent_knowledge_edges, CollectionNames.AGENT_HAS_KNOWLEDGE.value, transaction=transaction
    )
    if not result:
        raise RuntimeError("Failed to link knowledge to the agent")

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


async def _remove_knowledge_nodes(keys: list[str], graph_provider: IGraphDBProvider, logger: Logger) -> None:
    """Best-effort removal of knowledge nodes and their agent links; failures are only logged."""
    for key in keys:
        try:
            await graph_provider.delete_all_edges_for_node(
                f"{CollectionNames.AGENT_KNOWLEDGE.value}/{key}", CollectionNames.AGENT_HAS_KNOWLEDGE.value,
            )
        except Exception as cleanup_error:
            _cleanup_failed(logger, cleanup_error, collection=CollectionNames.AGENT_HAS_KNOWLEDGE.value)
    try:
        await graph_provider.delete_nodes(keys, CollectionNames.AGENT_KNOWLEDGE.value)
    except Exception as cleanup_error:
        _cleanup_failed(logger, cleanup_error, collection=CollectionNames.AGENT_KNOWLEDGE.value, nodes=len(keys))


async def _finish_unlinking_old(
    agent_full_id: str,
    edge_collection: str,
    old_ids: list[str],
    deleted_old_ids: list[str],
    new_ids: set[str],
    graph_provider: IGraphDBProvider,
    logger: Logger,
) -> bool:
    """After a save failed partway through unlinking the agent's old attachments, finish
    unlinking them where the writes persisted (a rollback that undoes nothing). Returns
    whether it did, so the caller can remove the old nodes too.

    Acts only on evidence read back from the graph: a new link still present, or a
    removed old link still gone. A real rollback restores the old links and drops the
    new ones, and a failed read shows nothing, so both are left as they are. Best effort
    throughout; failures are logged.
    """
    try:
        edges = await graph_provider.get_edges_from_node(agent_full_id, edge_collection)
    except Exception as read_error:
        _cleanup_failed(logger, read_error, collection=edge_collection)
        return False
    linked = {edge.get("_to") for edge in edges or []}
    remaining_old = [old_id for old_id in old_ids if old_id in linked]
    if new_ids:
        persisted = bool(linked & new_ids)
    else:
        persisted = any(old_id not in linked for old_id in deleted_old_ids)
    if not persisted:
        return False
    for old_id in remaining_old:
        try:
            await graph_provider.delete_all_edges_for_node(old_id, edge_collection)
        except Exception as cleanup_error:
            _cleanup_failed(logger, cleanup_error, collection=edge_collection)
    return True


async def _remove_toolsets(
    toolset_keys: list[str], tool_keys: list[str], graph_provider: IGraphDBProvider, logger: Logger,
) -> None:
    """Best-effort removal of toolset nodes, their tool nodes and every link to them;
    failures are only logged."""
    for key in toolset_keys:
        toolset_id = f"{CollectionNames.AGENT_TOOLSETS.value}/{key}"
        for edge_collection in (CollectionNames.AGENT_HAS_TOOLSET.value, CollectionNames.TOOLSET_HAS_TOOL.value):
            try:
                await graph_provider.delete_all_edges_for_node(toolset_id, edge_collection)
            except Exception as cleanup_error:
                _cleanup_failed(logger, cleanup_error, collection=edge_collection)
    for keys, collection in ((tool_keys, CollectionNames.AGENT_TOOLS.value), (toolset_keys, CollectionNames.AGENT_TOOLSETS.value)):
        if not keys:
            continue
        try:
            await graph_provider.delete_nodes(keys, collection)
        except Exception as cleanup_error:
            _cleanup_failed(logger, cleanup_error, collection=collection, nodes=len(keys))


async def _finish_removing_old_knowledge(
    agent_full_id: str,
    old_ids: list[str],
    old_keys: list[str],
    deleted_old_ids: list[str],
    new_keys: list[str],
    graph_provider: IGraphDBProvider,
    logger: Logger,
) -> None:
    """After a knowledge save failed partway through removing the old links, finish
    removing them where the writes persisted (see `_finish_unlinking_old`)."""
    new_ids = {f"{CollectionNames.AGENT_KNOWLEDGE.value}/{key}" for key in new_keys}
    if not await _finish_unlinking_old(
        agent_full_id, CollectionNames.AGENT_HAS_KNOWLEDGE.value, old_ids, deleted_old_ids, new_ids,
        graph_provider, logger,
    ):
        return
    # Old knowledge nodes with no link are never read, so removing them is tidy-up only.
    if old_keys:
        try:
            await graph_provider.delete_nodes(old_keys, CollectionNames.AGENT_KNOWLEDGE.value)
        except Exception as cleanup_error:
            _cleanup_failed(logger, cleanup_error, collection=CollectionNames.AGENT_KNOWLEDGE.value, nodes=len(old_keys))


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


# What an agent shows of a skill on it that the caller cannot see: which skill it runs, and
# whether that skill still works (the builder warns on a disabled or replaced one). The skill's
# content, resources, usage and the rest of its metadata stay with its owner.
_SKILL_FIELDS_SHOWN_WITH_AN_AGENT = ("name", "description", "status", "replacedBy")


async def _skills_as_the_caller_may_see_them(
    skills: list[dict[str, Any]],
    user_key: str,
    graph_provider: IGraphDBProvider,
) -> list[dict[str, Any]]:
    """The agent's skill entries, each skill the caller cannot see (the skill store's read in
    the bound org, scoped to the caller, or no longer readable) cut to
    `_SKILL_FIELDS_SHOWN_WITH_AN_AGENT`."""
    if not skills:
        return []
    org_id = current_org_id()
    try:
        teams = [str(r["teamId"]) for r in await graph_provider.get_user_team_roles(user_key, org_id) or []]
    except Exception:
        teams = []  # an unreadable membership shows no team's skill
    store = GraphSkillStore(graph_provider, org_id, user_key, visibility=SkillVisibility.of([user_key], teams=teams))
    shown: list[dict[str, Any]] = []
    for skill in skills:
        name = skill.get("name")
        if name and await store.exists(name):
            shown.append(skill)
        else:
            shown.append({field: skill.get(field) for field in _SKILL_FIELDS_SHOWN_WITH_AN_AGENT})
    return shown


async def _create_skill_edges(
    agent_key: str,
    skill_names: list[str],
    user_key: str,
    graph_provider: IGraphDBProvider,
    logger: Logger,
    transaction: str | None = None,
) -> list[str]:
    """Create `agentHasSkill` edges from an agent to each assigned skill —
    mirrors `_create_toolset_edges`/`_create_knowledge_edges` in shape, but
    never creates a skill node: skills are owned by the Skills management
    API, this only links to ones that already exist.

    A name is only linked when the skill store, scoped to the acting user in
    the bound org, reads it as theirs or a builtin: assignment is how a custom skill is shared, so only its owner
    can share it. Any name that doesn't resolve is logged
    and skipped rather than failing the whole create/update — a stale
    skill reference in the payload should never block agent creation.
    """
    if not skill_names:
        return []

    org_id = current_org_id()
    store = GraphSkillStore(graph_provider, org_id, user_key)
    skills_collection = CollectionNames.AGENT_SKILLS.value
    time = get_epoch_timestamp_in_ms()
    edges: list[dict[str, Any]] = []
    linked_names: list[str] = []

    for name in skill_names:
        status = await store.get_status(name, transaction=transaction)
        if status is None:
            log_event(logger, "agent.skill.skipped", reason=await store.hidden_reason(name, transaction=transaction), skill=name)
            continue
        if status != SkillStatus.ACTIVE.value:
            # Mirrors the picker (`GET /skills?status=active`, see
            # `SkillsApi.listAssignableSkills`) — closes the gap where a
            # direct API call could still newly assign a disabled or
            # deprecated skill the UI never offers.
            log_event(logger, "agent.skill.skipped", reason="inactive", skill=name)
            continue
        edges.append({
            "_from": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
            "_to": f"{skills_collection}/{org_id}_{name}",
            "skillName": name,
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
        })
        linked_names.append(name)

    if edges:
        ensure_written(
            await graph_provider.batch_create_edges(edges, CollectionNames.AGENT_HAS_SKILL.value, transaction=transaction),
            "the agent's skill links",
        )
    return linked_names


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


@router.post("/create", dependencies=[Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def create_agent(request: Request) -> JSONResponse:
    """Create a new agent using graph-based architecture"""
    return JSONResponse(status_code=200, content=await _create_agent(request, None))


async def _create_agent(request: Request, planned: dict[str, Any] | None) -> dict[str, Any]:
    """Creates the agent ``planned`` describes (an import's body), else the request's body: owned by the caller,
    in the caller's org, whatever the body says. Answers the create route's response."""
    try:
        services = await get_services(request)
        logger = services["logger"]
        user_context = _get_user_context(request)

        body = planned if planned is not None else _parse_request_body(await request.body())
        _validate_required_fields(body, ["name"], ("description", "startMessage", "systemPrompt", "instructions"))

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
        attached_toolsets = (
            await _assert_toolsets_visible(toolsets_with_tools, services["config_service"], org_key, user_context["userId"])
            if _names_an_instance(toolsets_with_tools) else {}
        )
        mcp_servers_with_tools = _parse_mcp_servers(body.get("mcpServers", []))
        attached_mcp_instances = (
            await _assert_mcp_servers_exist(
                mcp_servers_with_tools, services["config_service"], org_key, user_context["userId"],
            )
            if mcp_servers_with_tools else {}
        )
        mcp_servers_with_tools = _bind_mcp_attachments_to_instances(mcp_servers_with_tools, attached_mcp_instances)
        await _refuse_unapproved_mcp_tools(services, mcp_servers_with_tools)
        knowledge_sources = _parse_knowledge_sources(body.get("knowledge", []))
        await _refuse_limit_over_the_cap(knowledge_sources, services["graph_provider"], org_key, logger)
        skill_names = _parse_skills(body.get("skills", []))
        web_search_attachment = _parse_web_search(body.get("webSearch"))

        agent_key = str(uuid.uuid4())
        engine_settings = await _agent_engine_settings(request, services, org_key, agent_key, body, None)
        role_models = await _agent_role_models(services, agent_key, body)
        gates = _agent_gates(body)
        environment = _agent_environment(body)

        # Validate shareWithOrg + toolsets combination BEFORE starting transaction
        is_service_account = bool(body.get("isServiceAccount", False))
        service_account_binding = await _checked_service_account_binding(
            body.get("serviceAccountUserId"),
            services["graph_provider"],
            org_key,
            is_service_account=is_service_account,
            # Built lazily: the gate runs only when a binding is actually
            # being set, and most creates never name one.
            admin_check=lambda: _only_an_administrator_may_bind(
                request, services["config_service"],
            ),
        )
        if sa_forces_org_sharing() and is_service_account:
            share_with_org = True
        else:
            share_with_org = bool(body.get("shareWithOrg", False))
        _assert_no_personal_mcp_servers(
            attached_mcp_instances, shared_with_org=share_with_org, service_account=is_service_account,
        )
        _assert_no_personal_toolsets(attached_toolsets, shared_with_org=share_with_org, service_account=is_service_account)
        if is_service_account:
            await _assert_service_account_knowledge(
                knowledge_sources, services["graph_provider"], org_key, user_context["userId"],
                bound=bool(service_account_binding),
            )
        api_toolset_tools = await _checked_api_toolset_tools(
            body.get("apiToolsetTools", []), services, org_key, service_account=is_service_account,
        )

        # Create agent document
        bind_request_log_fields(request, agent_id=agent_key)
        if share_with_org:
            add_request_log_fields(request, shared_with_org=True)
        agent = {
            "_key": agent_key,
            "name": body["name"].strip(),
            "description": (body.get("description") or "").strip() or "AI agent for task automation",
            "startMessage": (body.get("startMessage") or "").strip() or "Hello! How can I help you today?",
            "systemPrompt": (body.get("systemPrompt") or "").strip() or "You are a workplace productivity assistant. Help users with their connected work tools.",
            "instructions": (body.get("instructions") or "").strip() or None,
            "models": model_entries,
            "tags": body.get("tags", []) or [],
            "webSearch": web_search_attachment,
            "defaultReasoningEffort": default_reasoning_effort,
            "apiToolsetTools": api_toolset_tools,
            "isActive": True,
            "isServiceAccount": is_service_account,
            "serviceAccountUserId": service_account_binding,
            "sendUserContext": bool(body.get("sendUserContext", True)),
            "createdBy": user_key,
            "updatedBy": None,
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
            "isDeleted": False,
        }
        if engine_settings is not None and engine_settings.engine_profile is not None:
            agent[AGENT_ENGINE_PROFILE_FIELD] = engine_settings.engine_profile
            if engine_settings.model_account_id is not None:
                agent[AGENT_MODEL_ACCOUNT_FIELD] = engine_settings.model_account_id
        if role_models:
            agent[AGENT_ROLE_MODELS_FIELD] = role_models
        if gates:
            agent[AGENT_GATES_FIELD] = gates
        if environment:
            agent[AGENT_ENVIRONMENT_FIELD] = environment
        encode_agent_json_fields(agent)
        owner_team = await _owner_team(request, body, services, org_key, user_key)
        if owner_team is not None:
            agent[AGENT_OWNER_TEAM_FIELD] = owner_team

        # Everything but the MCP servers is written in one transaction; those are
        # attached after it commits (see _attach_mcp_servers_to_new_agent).
        created_toolsets = []
        failed_toolsets = []
        created_mcp_servers: list[dict[str, Any]] = []
        failed_mcp_servers: list[dict[str, Any]] = []
        created_knowledge = []
        linked_skills: list[str] = []

        try:
            graph_provider = services["graph_provider"]

            async def write_agent(tx_store: "TransactionStore") -> None:
                nonlocal linked_skills
                # A re-run follows a rollback, so it starts from nothing written.
                created_toolsets.clear()
                created_knowledge.clear()
                transaction_id = tx_store.txn

                # Step 1: Create agent node
                ensure_written(await graph_provider.batch_upsert_nodes([agent], CollectionNames.AGENT_INSTANCES.value, transaction=transaction_id), "the agent")

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

                ensure_written(await graph_provider.batch_create_edges(permission_edges, CollectionNames.PERMISSION.value, transaction=transaction_id), "the agent's permissions")

                # Step 3: Create toolsets and tools (within same transaction)
                if toolsets_with_tools:
                    toolset_mapping = {}
                    toolset_nodes = []

                    # Prepare toolset nodes
                    for entry_key, toolset_data in toolsets_with_tools.items():
                        toolset_name = toolset_data.get("name", entry_key)
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
                        toolset_mapping[entry_key] = {
                            "name": toolset_name,
                            "key": toolset_key,
                            "displayName": display_name,
                            "tools": tools_list
                        }

                    # Batch create toolset nodes
                    if toolset_nodes:
                        ensure_written(await graph_provider.batch_upsert_nodes(toolset_nodes, CollectionNames.AGENT_TOOLSETS.value, transaction=transaction_id), "the agent's toolsets")

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
                        ensure_written(await graph_provider.batch_create_edges(agent_toolset_edges, CollectionNames.AGENT_HAS_TOOLSET.value, transaction=transaction_id), "the agent's toolset links")

                    # Create tool nodes and edges
                    tool_mapping = {}
                    tool_nodes = []
                    toolset_tool_edges = []

                    for toolset_info in toolset_mapping.values():
                        toolset_name = toolset_info["name"]
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
                        ensure_written(await graph_provider.batch_upsert_nodes(tool_nodes, CollectionNames.AGENT_TOOLS.value, transaction=transaction_id), "the agent's tools")

                    # Batch create toolset -> tool edges
                    if toolset_tool_edges:
                        ensure_written(await graph_provider.batch_create_edges(toolset_tool_edges, CollectionNames.TOOLSET_HAS_TOOL.value, transaction=transaction_id), "the agent's tool links")

                    # Build response for created toolsets
                    for toolset_info in toolset_mapping.values():
                        toolset_name = toolset_info["name"]
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
                        ensure_written(await graph_provider.batch_upsert_nodes(knowledge_nodes, CollectionNames.AGENT_KNOWLEDGE.value, transaction=transaction_id), "the agent's knowledge sources")

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
                        ensure_written(await graph_provider.batch_create_edges(agent_knowledge_edges, CollectionNames.AGENT_HAS_KNOWLEDGE.value, transaction=transaction_id), "the agent's knowledge links")

                    # Build response for created knowledge
                    created_knowledge.extend(
                        {
                            "connectorId": connector_id,
                            "key": knowledge_info["key"],
                            "filters": knowledge_info["filters"],
                        }
                        for knowledge_info in knowledge_mapping.values()
                    )


                # Step 5: Link assigned skills (within same transaction) — mirrors
                # AGENT_HAS_TOOLSET/AGENT_HAS_KNOWLEDGE above but never creates a
                # skill node, only edges to skills that already exist.
                if skill_names:
                    linked_skills = await _create_skill_edges(
                        agent_key, skill_names, user_key, graph_provider, logger,
                        transaction=transaction_id,
                    )

            # explicit: all-or-nothing on Neo4j too, so a failure leaves no half-made
            # agent and a deadlock on the user or org node re-runs from a clean slate.
            # The helper rolls back on any BaseException, so a cancelled request
            # does not leave the transaction holding its locks until it times out.
            await GraphDataStore(logger, graph_provider).execute_in_transaction(write_agent, explicit=True)

        except Exception as e:
            raise _route_failure(request, e, "create this agent", writes_body=True) from e

        # Outside the agent's transaction: on Neo4j one failed statement fails the
        # whole transaction, which would turn one bad MCP server into no agent.
        if mcp_servers_with_tools:
            created_mcp_servers, failed_mcp_servers = await _attach_mcp_servers_to_new_agent(
                agent_key, mcp_servers_with_tools, user_context, user_key, graph_provider, logger,
            )
        await _sync_agent(services, org_key, agent_key)
        try:
            created_agent = await graph_provider.get_agent(agent_key, org_key)
            if created_agent:
                await AgentVersionStore(graph_provider).publish(
                    org_key, agent_key, created_agent, published_by=user_context["userId"],
                )
        except Exception as e:
            # Its first run or read makes version 1 instead (AgentVersionStore.current).
            error_class.log_failure(logger, "agent.route.failed", e, op="first_version", include_error_text=False)

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
        decode_agent_json_fields(response_agent)

        all_failed = failed_toolsets + failed_mcp_servers
        status = "partial_success" if all_failed else "success"
        message = f"Agent created with warnings: {len(all_failed)} attachment(s) failed" if all_failed else "Agent created successfully"

        return {
            "status": status,
            "message": message,
            "agent": response_agent,
            "warnings": all_failed if all_failed else None,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise _route_failure(request, e, "create this agent", writes_body=True) from e

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


# Admins only: these answer "which agents still use this?" before an admin deletes a
# web search provider or a model, and they list every agent in the org by name and
# creator, private ones included. A member must not learn about agents they can't open.
@router.get(
    "/web-search-usage/{provider}",
    dependencies=[Depends(require_scopes(OAuthScopes.AGENT_READ)), Depends(require_admin_caller)],
)
async def get_web_search_provider_usage(request: Request, provider: str) -> JSONResponse:
    """Return agents in the org that use a specific web search provider. Admins only."""
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


@router.get(
    "/model-usage/{model_key}",
    dependencies=[Depends(require_scopes(OAuthScopes.AGENT_READ)), Depends(require_admin_caller)],
)
async def get_model_usage(request: Request, model_key: str) -> JSONResponse:
    """Return agents in the org that use a specific AI model. Admins only."""
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
        agent["skills"] = await _skills_as_the_caller_may_see_them(
            agent.get("skills") or [], user_doc["_key"], services["graph_provider"],
        )
        if versioning is not None:
            agent["versioning"] = versioning

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
        wire = found.wire()
        if wire["definition"].get("skills"):
            graph = services["graph_provider"]
            user_doc = await _get_user_document(user_context["userId"], graph, services["logger"])
            wire["definition"]["skills"] = await _skills_as_the_caller_may_see_them(
                [s for s in wire["definition"]["skills"] if isinstance(s, dict)],
                user_doc["_key"], graph,
            )
        return JSONResponse(status_code=200, content={"version": wire})
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
    from app.connectors.core.registry.connector_builder import ConnectorScope
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
    # The Slack bot's service token too, unless this build runs every agent as the identified person asking.
    dependencies=[
        Depends(
            require_scopes(
                OAuthScopes.AGENT_EXECUTE,
                OAuthScopes.CONVERSATION_CHAT,
                service_scopes=() if edition.AGENTS_RUN_AS_THE_CALLER else (TokenScopes.CONVERSATION_CREATE,),
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
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Writes ``body`` over the agent (the draft): its document, then each of its toolsets, knowledge, MCP
    servers and skills that ``body`` names, each replaced whole. Publishes nothing."""
    services = await get_services(request)
    logger = services["logger"]
    user_context = _get_user_context(request)

    user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], logger)
    user_key = user_doc["_key"]
    org_key = current_org_id()

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

    # Rejecting this after update_agent below would leave the rest of the edit saved.
    mcp_servers_with_tools = (
        _parse_mcp_servers(body.get("mcpServers", [])) if "mcpServers" in body else {}
    )
    attached_mcp_instances = (
        await _assert_mcp_servers_exist(
            mcp_servers_with_tools, services["config_service"], org_key, user_context["userId"],
        )
        if mcp_servers_with_tools else {}
    )
    mcp_servers_with_tools = _bind_mcp_attachments_to_instances(mcp_servers_with_tools, attached_mcp_instances)
    await _refuse_unapproved_mcp_tools(services, mcp_servers_with_tools)
    toolsets_in_body = _parse_toolsets(body.get("toolsets", [])) if "toolsets" in body else {}
    attached_toolsets = (
        await _assert_toolsets_visible(toolsets_in_body, services["config_service"], org_key, user_context["userId"])
        if _names_an_instance(toolsets_in_body) else {}
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

    engine_settings = await _agent_engine_settings(request, services, org_key, agent_id, body, agent)
    if engine_settings is not None:
        body.update(engine_settings.document_fields())
    role_models = await _agent_role_models(services, agent_id, body)
    if role_models is not None:
        # An empty map clears the stored one: every sub-agent runs on its parent's model again.
        body[AGENT_ROLE_MODELS_FIELD] = role_models or None
    gates = _agent_gates(body)
    if gates is not None:
        # An empty object clears them: the agent's runs are held to no gate of its own again.
        body[AGENT_GATES_FIELD] = gates or None
    environment = _agent_environment(body)
    if environment is not None:
        # An empty object clears it: the agent's engine runs set nothing up again.
        body[AGENT_ENVIRONMENT_FIELD] = environment or None

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
        if sa_forces_org_sharing() and requested_is_sa and not current_is_sa:
            body["shareWithOrg"] = True

    final_shared = bool(body.get("shareWithOrg", agent.get("shareWithOrg", False)))
    final_service_account = bool(body.get("isServiceAccount", agent.get("isServiceAccount", False)))

    # A service agent searches as its creator until it is bound, and is shared
    # with the whole organisation, so an editor converting someone else's agent
    # would hand everyone the owner's access. Only the owner or an
    # administrator may decide that.
    converting = final_service_account and not agent.get("isServiceAccount")
    if converting and perm.get("user_role") != "OWNER":
        await _only_an_administrator_may_bind(
            request,
            services["config_service"],
            detail="Only the agent's owner or an administrator can turn it into a service agent.",
        )
    # A conversion shares the agent as a matter of course and is checked above.
    if final_shared and not agent.get("shareWithOrg") and not converting and not perm.get("can_share", False):
        raise PermissionDeniedError("share this agent with the organisation")
    # An unbound service agent searches as its creator, so its sources are
    # searched with the owner's access: changing what it reaches is the
    # owner's or an administrator's call, like converting it.
    if (
        "knowledge" in body
        and agent.get("isServiceAccount")
        and not agent.get("serviceAccountUserId")
        and perm.get("user_role") != "OWNER"
        and source_reach(_parse_knowledge_sources(body.get("knowledge", [])).values())
        != source_reach(agent.get("knowledge") or [])
    ):
        await _only_an_administrator_may_bind(
            request,
            services["config_service"],
            detail=(
                "Only the agent's owner or an administrator can change the knowledge "
                "of a service agent that searches as its creator."
            ),
        )

    if "serviceAccountUserId" in body:
        body["serviceAccountUserId"] = await _checked_service_account_binding(
            body["serviceAccountUserId"],
            services["graph_provider"],
            org_key,
            is_service_account=final_service_account,
            admin_check=lambda: _only_an_administrator_may_bind(
                request, services["config_service"],
            ),
            # What the agent is bound to now, so re-saving it unchanged is
            # not a change anyone needs to be an administrator to make.
            current=agent.get("serviceAccountUserId"),
        )
    sharing_turned_on = (final_shared and not agent.get("shareWithOrg")) or (
        final_service_account and not agent.get("isServiceAccount")
    )
    if sharing_turned_on and "mcpServers" not in body:
        # An empty list here may only mean the read failed; it would let any server through.
        if agent.get("mcpServersUnavailable"):
            raise _mcp_attachments_unreadable()
        existing_ids = {s["instanceId"] for s in agent.get("mcpServers") or [] if s.get("instanceId")}
        try:
            attached_mcp_instances = await _attached_personal_instances(
                existing_ids, services["config_service"], org_key,
            ) if existing_ids else {}
        except Exception as e:
            unreadable = _mcp_attachments_unreadable()
            mark_request_failure(request, unreadable, include_error_text=False)
            raise unreadable from e
    _assert_no_personal_mcp_servers(
        attached_mcp_instances, shared_with_org=final_shared, service_account=final_service_account,
    )
    if sharing_turned_on and "toolsets" not in body:
        existing_toolset_ids = {ts["instanceId"] for ts in agent.get("toolsets") or [] if ts.get("instanceId")}
        try:
            attached_toolsets = await _attached_personal_toolsets(
                existing_toolset_ids, services["config_service"], org_key,
            ) if existing_toolset_ids else {}
        except Exception as e:
            unreadable = _toolset_attachments_unreadable()
            mark_request_failure(request, unreadable, include_error_text=False)
            raise unreadable from e
    _assert_no_personal_toolsets(attached_toolsets, shared_with_org=final_shared, service_account=final_service_account)
    if final_service_account and ("knowledge" in body or converting or "serviceAccountUserId" in body):
        await _assert_service_account_knowledge(
            _parse_knowledge_sources(body.get("knowledge")) if "knowledge" in body
            else [k.get("connectorId") for k in agent.get("knowledge") or [] if k.get("connectorId")],
            services["graph_provider"],
            org_key,
            user_context["userId"],
            bound=bool(body.get("serviceAccountUserId", agent.get("serviceAccountUserId"))),
        )
    if "apiToolsetTools" in body or (final_service_account and not agent.get("isServiceAccount")):
        # Becoming a service account re-checks what is already attached.
        body["apiToolsetTools"] = await _checked_api_toolset_tools(
            body.get("apiToolsetTools", agent.get("apiToolsetTools") or []), services,
            org_key, service_account=final_service_account,
        )

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
            add_request_log_fields(request, shared_with_org=True)

        elif not new_share_with_org and current_share_with_org:
            if sa_forces_org_sharing() and bool(agent.get("isServiceAccount", False)):
                raise InvalidRequestError(
                    "Cannot disable org-wide sharing for a service account agent. "
                    "Service account agents must always be shared across the organisation."
                )
            # Turning OFF org sharing: delete the org permission edge. Not
            # delete_edge: ArangoDB's answers False when the delete fails, and the
            # agent stayed shared with the whole org while reporting that it was not.
            await services["graph_provider"].batch_delete_edges(
                [{
                    "from_id": org_key,
                    "from_collection": CollectionNames.ORGS.value,
                    "to_id": agent_id,
                    "to_collection": CollectionNames.AGENT_INSTANCES.value,
                }],
                CollectionNames.PERMISSION.value,
            )
            add_request_log_fields(request, shared_with_org=False)


    # Normalize webSearch attachment before persisting
    if "webSearch" in body:
        body["webSearch"] = _parse_web_search(body.get("webSearch"))
    if AGENT_OWNER_TEAM_FIELD in body:
        body[AGENT_OWNER_TEAM_FIELD] = await _owner_team(request, body, services, org_key, user_key)

    # Update agent document
    # Persist update (use original body to avoid changing storage format)
    result = await services["graph_provider"].update_agent(agent_id, body, user_key, org_key)
    if not result:
        raise HTTPException(status_code=500, detail="Failed to update agent")
    await directory_events(request).agent_changed(org_key, agent_id, get_epoch_timestamp_in_ms())

    # Update toolsets if provided in request (even if empty array - means delete all)
    if "toolsets" in body:
        # Parsed and checked before the update above
        toolsets_with_tools = toolsets_in_body

        graph_provider = services["graph_provider"]
        transaction_id = None
        agent_full_id = f"{CollectionNames.AGENT_INSTANCES.value}/{agent_id}"
        toolset_keys: list[str] = []
        toolset_full_ids: list[str] = []
        all_tool_keys: list[str] = []
        all_tool_full_ids: list[str] = []
        new_toolset_keys: list[str] = []
        new_tool_keys: list[str] = []
        unlinked_old_ids: list[str] = []
        try:
            transaction_id = await graph_provider.begin_transaction(
                read=[],
                write=[
                    CollectionNames.AGENT_HAS_TOOLSET.value,
                    CollectionNames.AGENT_TOOLSETS.value,
                    CollectionNames.TOOLSET_HAS_TOOL.value,
                    CollectionNames.AGENT_TOOLS.value
                ]
            )

            # ========== PHASE 1: GATHER ALL INFORMATION (READ ONLY) ==========

            # Get all toolset edges from agent
            toolset_edges = await graph_provider.get_edges_from_node(
                agent_full_id,
                CollectionNames.AGENT_HAS_TOOLSET.value,
                transaction=transaction_id
            )

            # Extract toolset keys and full IDs
            for edge in toolset_edges:
                toolset_full_id = edge.get("_to")
                if toolset_full_id:
                    toolset_full_ids.append(toolset_full_id)
                    parts = toolset_full_id.split("/", 1)
                    if len(parts) == SPLIT_PATH_EXPECTED_PARTS:
                        toolset_keys.append(parts[1])


            # Get all tool edges for each toolset
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


            # ========== PHASE 2: WRITE THE NEW TOOLSETS ==========

            # New toolsets are written, with their tools, before the old ones are unlinked:
            # on a backend whose rollback does not undo writes (Neo4j without explicit
            # transactions), a failure then leaves the agent with its old toolsets rather
            # than none, and the except block below removes what the failed attempt wrote.
            if toolsets_with_tools:
                _, failed_toolsets = await _create_toolset_edges(
                    agent_id, toolsets_with_tools, user_context, user_key, graph_provider, logger,
                    transaction=transaction_id,
                    written_toolset_keys=new_toolset_keys,
                    written_tool_keys=new_tool_keys,
                )
                if failed_toolsets:
                    raise RuntimeError(f"Toolsets could not be attached: {failed_toolsets}")

            # ========== PHASE 3: UNLINK, THEN REMOVE, THE OLD TOOLSETS ==========

            for toolset_full_id in toolset_full_ids:
                await graph_provider.delete_all_edges_for_node(
                    toolset_full_id,
                    CollectionNames.AGENT_HAS_TOOLSET.value,
                    transaction=transaction_id
                )
                unlinked_old_ids.append(toolset_full_id)

            # Nothing links to the old toolsets now; remove them from the leaves up.
            for tool_full_id in all_tool_full_ids:
                await graph_provider.delete_all_edges_for_node(
                    tool_full_id,
                    CollectionNames.TOOLSET_HAS_TOOL.value,
                    transaction=transaction_id
                )
            if all_tool_keys:
                await graph_provider.delete_nodes(
                    all_tool_keys,
                    CollectionNames.AGENT_TOOLS.value,
                    transaction=transaction_id
                )
            if toolset_keys:
                await graph_provider.delete_nodes(
                    toolset_keys,
                    CollectionNames.AGENT_TOOLSETS.value,
                    transaction=transaction_id
                )

            await graph_provider.commit_transaction(transaction_id)
            transaction_id = None

        except Exception as e:
            if transaction_id:
                try:
                    await graph_provider.rollback_transaction(transaction_id)
                except Exception as abort_error:
                    _rollback_failed(logger, abort_error)
            # Before any old toolset is unlinked the agent is still on its old set, so the
            # new writes go. After that the new set is the intended state: keep it.
            if not unlinked_old_ids:
                if new_toolset_keys or new_tool_keys:
                    await _remove_toolsets(new_toolset_keys, new_tool_keys, graph_provider, logger)
            elif await _finish_unlinking_old(
                agent_full_id, CollectionNames.AGENT_HAS_TOOLSET.value, toolset_full_ids, unlinked_old_ids,
                {f"{CollectionNames.AGENT_TOOLSETS.value}/{key}" for key in new_toolset_keys},
                graph_provider, logger,
            ):
                await _remove_toolsets(toolset_keys, all_tool_keys, graph_provider, logger)
            raise _route_failure(request, e, "save this agent", writes_body=True) from e

    # Update attached MCP servers if provided in request (even if empty array - means detach all).
    # The new servers are written, with their tools, before the old ones are unlinked: on a
    # backend whose rollback does not undo writes (Neo4j without explicit transactions), a
    # failure then leaves the agent with its old servers rather than none, and the except
    # block removes what the failed attempt wrote.
    if "mcpServers" in body:
        graph_provider = services["graph_provider"]
        transaction_id = None
        agent_full_id = f"{CollectionNames.AGENT_INSTANCES.value}/{agent_id}"
        old_server_keys: list[str] = []
        old_server_ids: list[str] = []
        old_tool_keys: list[str] = []
        new_server_keys: list[str] = []
        new_tool_keys: list[str] = []
        unlinked_old_ids: list[str] = []
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
            old_server_keys, old_server_ids, old_tool_keys, old_tool_ids = await _read_agent_mcp_attachments(
                agent_full_id, graph_provider, transaction_id,
            )

            if mcp_servers_with_tools:
                _, failed_mcp_servers = await _create_mcp_server_edges(
                    agent_id, mcp_servers_with_tools, user_context, user_key,
                    graph_provider, logger, transaction=transaction_id,
                    written_server_keys=new_server_keys, written_tool_keys=new_tool_keys,
                )
                if failed_mcp_servers:
                    raise RuntimeError(f"MCP servers could not be attached: {failed_mcp_servers}")

            for server_id in old_server_ids:
                await graph_provider.delete_all_edges_for_node(
                    server_id, CollectionNames.AGENT_HAS_MCP_SERVER.value, transaction=transaction_id,
                )
                unlinked_old_ids.append(server_id)
            # Nothing links to the old servers now; remove them from the leaves up.
            for tool_id in old_tool_ids:
                await graph_provider.delete_all_edges_for_node(
                    tool_id, CollectionNames.MCP_SERVER_HAS_TOOL.value, transaction=transaction_id,
                )
            if old_tool_keys:
                await graph_provider.delete_nodes(
                    old_tool_keys, CollectionNames.AGENT_TOOLS.value, transaction=transaction_id,
                )
            if old_server_keys:
                await graph_provider.delete_nodes(
                    old_server_keys, CollectionNames.AGENT_MCP_SERVERS.value, transaction=transaction_id,
                )

            await graph_provider.commit_transaction(transaction_id)
            transaction_id = None
        except Exception as e:
            if transaction_id:
                try:
                    await graph_provider.rollback_transaction(transaction_id)
                except Exception as abort_error:
                    _rollback_failed(logger, abort_error)
            # Before any old server is unlinked the agent is still on its old set, so the
            # new writes go. After that the new set is the intended state: keep it.
            if not unlinked_old_ids:
                if new_server_keys or new_tool_keys:
                    await _remove_mcp_servers(new_server_keys, new_tool_keys, graph_provider, logger)
            elif await _finish_unlinking_old(
                agent_full_id, CollectionNames.AGENT_HAS_MCP_SERVER.value, old_server_ids, unlinked_old_ids,
                {f"{CollectionNames.AGENT_MCP_SERVERS.value}/{key}" for key in new_server_keys},
                graph_provider, logger,
            ):
                await _remove_mcp_servers(old_server_keys, old_tool_keys, graph_provider, logger)
            raise _route_failure(request, e, "save this agent", writes_body=True) from e

    # Update knowledge if provided in request (even if empty array - means delete all)
    if "knowledge" in body:
        # Parse knowledge sources first to validate before deletion
        knowledge_sources = _parse_knowledge_sources(body.get("knowledge", []))
        await _refuse_limit_over_the_cap(
            knowledge_sources, services["graph_provider"], org_key, logger,
        )

        graph_provider = services["graph_provider"]
        transaction_id = None
        new_knowledge_keys: list[str] = []
        deleted_old_ids: list[str] = []
        agent_full_id = f"{CollectionNames.AGENT_INSTANCES.value}/{agent_id}"
        knowledge_keys: list[str] = []
        knowledge_full_ids: list[str] = []
        try:
            transaction_id = await graph_provider.begin_transaction(
                read=[],
                write=[
                    CollectionNames.AGENT_HAS_KNOWLEDGE.value,
                    CollectionNames.AGENT_KNOWLEDGE.value
                ]
            )

            # ========== PHASE 1: GATHER ALL INFORMATION (READ ONLY) ==========

            # Get all knowledge edges from agent
            knowledge_edges = await graph_provider.get_edges_from_node(
                agent_full_id,
                CollectionNames.AGENT_HAS_KNOWLEDGE.value,
                transaction=transaction_id
            )

            # Extract knowledge keys and full IDs
            for edge in knowledge_edges:
                knowledge_full_id = edge.get("_to")
                if knowledge_full_id:
                    knowledge_full_ids.append(knowledge_full_id)
                    parts = knowledge_full_id.split("/", 1)
                    if len(parts) == SPLIT_PATH_EXPECTED_PARTS:
                        knowledge_keys.append(parts[1])


            # New knowledge is written before the old is removed: on a backend whose
            # rollback does not undo writes (Neo4j without explicit transactions), a
            # failure then leaves the agent with its old knowledge rather than none,
            # and the except block below removes what the failed attempt wrote.
            if knowledge_sources:
                await _create_knowledge_edges(
                    agent_id, knowledge_sources, user_key, graph_provider, logger,
                    transaction=transaction_id, written_keys=new_knowledge_keys,
                )

            for knowledge_full_id in knowledge_full_ids:
                await graph_provider.delete_all_edges_for_node(
                    knowledge_full_id,
                    CollectionNames.AGENT_HAS_KNOWLEDGE.value,
                    transaction=transaction_id
                )
                deleted_old_ids.append(knowledge_full_id)

            if knowledge_keys:
                await graph_provider.delete_nodes(
                    knowledge_keys,
                    CollectionNames.AGENT_KNOWLEDGE.value,
                    transaction=transaction_id
                )

            await graph_provider.commit_transaction(transaction_id)
            transaction_id = None

        except Exception as e:
            if transaction_id:
                try:
                    await graph_provider.rollback_transaction(transaction_id)
                except Exception as abort_error:
                    _rollback_failed(logger, abort_error)
            # Before any old link is removed the agent is still on its old set, so the
            # new writes go. After that the new set is the intended state: keep it.
            if not deleted_old_ids:
                if new_knowledge_keys:
                    await _remove_knowledge_nodes(new_knowledge_keys, graph_provider, logger)
            else:
                await _finish_removing_old_knowledge(
                    agent_full_id, knowledge_full_ids, knowledge_keys, deleted_old_ids,
                    new_knowledge_keys, graph_provider, logger,
                )
            raise _route_failure(request, e, "save this agent", writes_body=True) from e

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
            await graph_provider.delete_all_edges_for_node(
                agent_full_id, CollectionNames.AGENT_HAS_SKILL.value, transaction=transaction_id,
            )
            if skill_names:
                await _create_skill_edges(
                    agent_id, skill_names, user_key, graph_provider, logger,
                    transaction=transaction_id,
                )
            await graph_provider.commit_transaction(transaction_id)
            transaction_id = None
        except Exception as e:
            if transaction_id:
                try:
                    await graph_provider.rollback_transaction(transaction_id)
                except Exception as abort_error:
                    _rollback_failed(logger, abort_error)
            raise _route_failure(request, e, "save this agent", writes_body=True) from e

    await _sync_agent(services, org_key, agent_id)
    return services, user_context, org_key


@router.put("/{agent_id}", dependencies=[_AGENT_ID_LOG, Depends(require_scopes(OAuthScopes.AGENT_WRITE))])
async def update_agent(request: Request, agent_id: str) -> JSONResponse:
    """Update an agent using graph-based architecture"""
    try:
        body = _parse_request_body(await request.body())
        services, user_context, org_key = await _write_draft(request, agent_id, body)
        logger = services["logger"]
        # A save publishes unless it asks to stay a draft (`"publish": false`): today's builder has no
        # publish step, and its saves must keep reaching chats as they always did.
        if body.get("publish") is False:
            return JSONResponse(
                status_code=200,
                content={"status": "success", "message": "Draft saved", "version": None},
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
                    "status": "partial_success",
                    "message": "Agent saved, but publishing it failed. Publish it again from its versions.",
                    "version": None,
                },
            )
        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "Agent updated successfully", "version": published.summary()},
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


@router.post(
    "/{agent_id}/chat",
    dependencies=[
        Depends(require_scopes(OAuthScopes.AGENT_EXECUTE, OAuthScopes.CONVERSATION_CHAT)),
        Depends(require_agent_execute_for_named_agents),
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

    outcome = await collect_stream_outcome(streaming_response.body_iterator, request.is_disconnected)
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
        org_key = user_context["orgId"]
        body = _parse_request_body(await request.body())
        try:
            chat_query = ChatQuery(**body)
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
            is_service_account = agent.get("isServiceAccount", False)
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
                user_doc = await _get_user_document(user_context["userId"], services["graph_provider"], logger)
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
            nonlocal agent, prefetched_toolset_auth, actions_enabled, mcp_enabled, is_service_account
            try:
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

                is_service_account = bool(agent.get("isServiceAccount", False))
                executing_user_id = user_context["userId"]
                # For service account agents, credentials are keyed by agentKey not userId
                credential_lookup_id = agent_id if is_service_account else executing_user_id
                toolset_configs: dict = {}  # SENSITIVE: Contains user/agent credentials
                bound = bound_installations(resolved, credential_lookup_id, service_account=is_service_account)

                # Filter to toolsets that actually have a name or instanceId before the concurrent fetch
                named_toolsets = [t for t in agent_toolsets if t.get("instanceId") or t.get("name")]

                if named_toolsets:
                    (
                        toolset_configs,
                        configured_toolsets,
                        missing_toolset_display_names,
                        unauthenticated_toolset_display_names,
                    ) = await _resolve_toolset_configs(
                        services["config_service"],
                        named_toolsets,
                        credential_lookup_id=credential_lookup_id,
                        executing_user_id=executing_user_id,
                        prefetched=prefetched_toolset_auth,
                        agent_id=agent_id,
                        is_service_account=is_service_account,
                        logger=logger,
                        bound=bound,
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
                        log_turn_end(
                            logger, "error", error_class="bad_request", code="toolset_config_missing",
                            run_id=chat_query.runId, duration_ms=round(timer.total_ms),
                            missing=len(missing_toolset_display_names),
                            unauthenticated=len(unauthenticated_toolset_display_names),
                        )
                        yield _stream_error_frame(protocol, error_message, code="toolset_config_missing")
                        return

                    agent_toolsets = configured_toolsets

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
                    # The same, for the chat to offer a Connect button per server.
                    blocked_mcp_servers: list[dict[str, Any]] = []
                    (
                        mcp_server_configs,
                        configured_mcp_servers,
                        missing_mcp_server_display_names,
                        unauthenticated_mcp_server_display_names,
                    ) = await _resolve_mcp_server_configs(
                        services["config_service"],
                        named_mcp_servers,
                        org_id=org_key,
                        credential_lookup_id=credential_lookup_id,
                        executing_user_id=executing_user_id,
                        agent_id=agent_id,
                        is_service_account=is_service_account,
                        logger=logger,
                        bound=bound,
                        prefetched=prefetched_mcp,
                        blocked=blocked_mcp_servers,
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
                        log_turn_end(
                            logger, "error", error_class="bad_request", code="mcp_server_config_missing",
                            run_id=chat_query.runId, duration_ms=round(timer.total_ms),
                            missing=len(missing_mcp_server_display_names),
                            unauthenticated=len(unauthenticated_mcp_server_display_names),
                        )
                        yield _stream_error_frame(
                            protocol, error_message, code="mcp_server_config_missing",
                            details={
                                "agentId": agent_id,
                                "serviceAccount": bool(is_service_account),
                                "servers": blocked_mcp_servers,
                            },
                        )
                        return

                    agent_mcp_servers = configured_mcp_servers

                timer.mark("mcp_cfg")

                agent_knowledge = agent.get("knowledge", [])
                # An unbound service agent would read a collection as its creator, so it reads none,
                # the caller's project collection included (project_files_knowledge reads it from filters).
                collections_allowed = not is_service_account or bool(agent.get("serviceAccountUserId"))
                if is_service_account:
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

                attachments, previous_conversations = await keep_accessible_attachments(
                    graph_provider,
                    org_id=user_context["orgId"],
                    user_id=user_context["userId"],
                    is_service_account=bool(
                        (getattr(request.state, "user", None) or {}).get("isServiceAccount"),
                    ),
                    attachments=chat_query.attachments,
                    previous_conversations=chat_query.previousConversations,
                    logger=logger,
                    connection_sources=connection_limit(getattr(request.state, "user", None)),
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
                    "projectToolsSelected": chat_query.strictScope and chat_query.tools is not None,
                    "timezone": chat_query.timezone,
                    "currentTime": chat_query.currentTime,
                    "toolsets": agent_toolsets,
                    "mcpServers": agent_mcp_servers,
                    "mcpServerConfigs": mcp_server_configs,
                    "apiToolsetTools": _api_toolset_tools_for_chat(agent.get("apiToolsetTools") or [], chat_query.tools),
                    "knowledge": agent_knowledge,
                    "skills": [s["name"] for s in agent.get("skills", []) if isinstance(s, dict) and s.get("name")] or None,
                    "connector_configs": connector_configs,
                    "toolsetConfigs": toolset_configs,
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
                    "credentialOwner": credential_lookup_id,
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
    a fix (for `mcp_server_config_missing`, which servers and what each needs).
    """
    extra = {"details": details} if details is not None else {}
    if protocol == "agui":
        from app.agents.agent_loop.protocol.agui import AGUIEventType, frame

        evt = frame(AGUIEventType.RUN_ERROR, message=message, code=code, **extra)
        return f"event: {evt['event']}\ndata: {json.dumps(evt['data'])}\n\n"
    return f"event: error\ndata: {json.dumps({'message': message, 'type': code, **extra})}\n\n"


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
