"""The trusted environment the tool sources build on: an ``AgentContext`` from the pin.

The existing toolset, MCP, knowledge, skills and artifact code reads a legacy
``AgentContext`` (``ChatState`` as ``tool_state``). This module builds one the
way the agent route does, with the same helpers and credential resolution,
but from what the run pinned rather than from the request: the agent comes
from ``x.pipeshub.agent``, tools are narrowed to ``grants.tools`` before
anything loads, and knowledge is the pinned scope. Credentials enter here and
stay in the context; nothing in it reaches the kernel, the journal or the
``RunContext``.
"""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal, cast

from app.agents.run_definition import ASSISTANT_AGENT_KEY
from app.harness.classification import in_tool_call
from app.utils.run_model_scope import RunModelScope, run_model_scope
from harness_core.driver import RunRefused
from harness_core.logs import HarnessLog
from harness_core.tool_identity import BoundInstance, ToolIdentityRefused, match_grant

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import HTTPException

    from app.agents.agent_loop.context import AgentContext
    from app.harness.assembly.chat import ChatTurn
    from app.harness.assembly.context import AgentRef, Attachment, RunContext
    from app.harness.assembly.model import ResolvedModel
    from app.harness.assembly.services import RunServices

NO_KB_SELECTED = "NO_KB_SELECTED"
WEB_SOURCE = "web"
MCP_SOURCE = "mcp"


def parse_tool_id(tool_id: str) -> tuple[str, str, str]:
    """``source@instance/tool`` -> (source, instance, tool)."""
    source, rest = tool_id.split("@", 1)
    instance, tool = rest.split("/", 1)
    return source, instance, tool


def toolset_tool_id(toolset: dict[str, Any], tool_name: str) -> str:
    """The id S3's resolver pins for an agent toolset's tool."""
    return f"{toolset.get('name')}@{toolset.get('instanceId') or 'default'}/{tool_name}"


@dataclass(frozen=True)
class UnavailableItem:
    """A granted toolset or MCP server whose credential the run's owner has not connected (QA23-F1)."""

    kind: Literal["toolset", "mcp"]
    instance: str
    name: str
    # An MCP server's granted tools as (the name discovery gives it, its name on the server): listed masked (WS-09).
    tools: tuple[tuple[str, str], ...] = ()


UNAVAILABLE_STEP = "unavailable_items"


class UnavailableItemsStep:
    """The items the run goes on without, journaled once as a pre-run slot: the presenter tells the person
    (``x.pipeshub.run.notice`` ``credential_missing``) and a resumed attempt does not tell them again."""

    key = UNAVAILABLE_STEP

    def __init__(self, items: tuple[UnavailableItem, ...]) -> None:
        self._items = items

    async def run(self, input_text: str) -> list[dict[str, str]]:
        return [{"item": item.name, "kind": item.kind} for item in self._items]


@dataclass
class ToolEnvironment:
    context: AgentContext
    agent: dict[str, Any] | None
    web_search_provider: str | None
    # (toolset name, instance) of every connector toolset the grant names.
    granted_toolsets: dict[tuple[str, str], frozenset[str]] = field(
        default_factory=dict[tuple[str, str], frozenset[str]]
    )
    # MCP instance -> granted tool names.
    granted_mcp: dict[str, frozenset[str]] = field(default_factory=dict[str, frozenset[str]])
    # The built-in assistant's turn (its prefetch and attachments), for the goal step.
    chat: ChatTurn | None = None
    unavailable: tuple[UnavailableItem, ...] = ()
    # Where a chat model a tool's helper builds for itself goes (``helper_model_scope``): through the run's ledger
    # and provider slot. Set by the assembler once the run's metering is known.
    helper_models: RunModelScope | None = None

    def __post_init__(self) -> None:
        # Every tool of this run, the engines' through the Tool Gateway included, runs in this context.
        self.context.harness_run = True

    def tool_call_scope(self) -> AbstractContextManager[object]:
        """Entered around each of the run's tool calls (``RegistryToolPort``'s ``around``), in whichever task runs
        it: a model a helper builds there is the run's (WP-49), and every model call made there is classified as
        reading the records the call reads (``in_tool_call``, WP-55)."""
        return _tool_call(self.helper_models)

    def narrowed(self, run: RunContext) -> RunContext:
        """``run`` without the MCP servers it could not connect as servers to load. Their granted tools stay granted,
        listed masked (``UnconnectedMcpTool``, OWD-7 "mask, don't remove"), with nothing pinned of them to load: the
        server cannot be asked for its tools without its credential. An unconnected toolset stays whole: its tools
        answer ``credential_missing``."""
        from app.harness.assembly.items.pins import Offer

        gone = {item.instance for item in self.unavailable if item.kind == MCP_SOURCE}
        if not gone:
            return run
        servers = tuple(i for i in run.mcp_instances if i not in gone)
        kept = Offer(run.tool_ids, servers, run.skills, ()).narrowed(run)
        pins = tuple(
            pin
            for pin in kept.descriptor_pins
            if not (pin.kind == "tool" and pin.ref.startswith(f"{MCP_SOURCE}@") and parse_tool_id(pin.ref)[1] in gone)
        )
        return kept.model_copy(update={"descriptor_pins": pins})


def _grants(run: RunContext) -> tuple[
    dict[tuple[str, str], frozenset[str]], dict[str, frozenset[str]], str | None
]:
    toolsets: dict[tuple[str, str], set[str]] = {}
    mcp: dict[str, set[str]] = {}
    web: str | None = None
    for tool_id in run.tool_ids:
        source, instance, tool = parse_tool_id(tool_id)
        if source == MCP_SOURCE:
            mcp.setdefault(instance, set()).add(tool)
        elif source == WEB_SOURCE:
            web = instance
        else:
            toolsets.setdefault((source, instance), set()).add(tool)
    return (
        {key: frozenset(tools) for key, tools in toolsets.items()},
        {key: frozenset(tools) for key, tools in mcp.items()},
        web,
    )


def bound_instances(agent_toolsets: list[dict[str, Any]]) -> list[BoundInstance]:
    return [BoundInstance(str(t.get("name")), t.get("instanceId") or None) for t in agent_toolsets]


def _narrow_toolsets(
    agent_toolsets: list[dict[str, Any]],
    granted: dict[tuple[str, str], frozenset[str]],
    log: HarnessLog,
) -> list[dict[str, Any]]:
    """The agent's toolsets the grant names. A grant resolves to its binding with ``match_grant``: a
    type-keyed pin (``jira@default``) finds the toolset its agent was migrated to; one that two
    bindings could answer finds none (the catalog then reports the tool lost), never a guess."""
    bound = bound_instances(agent_toolsets)
    names_by_binding: dict[int, set[str]] = {}
    for (kind, instance), names in granted.items():
        try:
            match = match_grant(kind, instance, bound)
        except ToolIdentityRefused as refused:
            log.warning("run.tool_grant_unresolved", reasonCode=refused.reason_code, toolset=kind)
            continue
        names_by_binding.setdefault(bound.index(match), set()).update(names)
    kept: list[dict[str, Any]] = []
    for index, toolset in enumerate(agent_toolsets):
        names = names_by_binding.get(index)
        if not names:
            continue
        tools = [t for t in toolset.get("tools") or [] if t.get("name") in names]
        if tools:
            kept.append({**toolset, "tools": tools})
    return kept


GITHUB_TOOLSET = "github"
GITHUB_PULL_REQUEST_TOOL = "create_pull_request"


def with_repository_toolset(
    toolsets: list[dict[str, Any]],
    granted: dict[tuple[str, str], frozenset[str]],
    instance: str | None,
) -> list[dict[str, Any]]:
    """GIT-FLOW (WP-26): the chat's repository's GitHub ``create_pull_request``, which the run's grant holds on that
    instance (Node joined it when it pinned the repository), even where the agent's saved toolsets do not list the
    instance or the tool. Node checked the instance as one the person may use; the grant is the authority here, as it
    is for every other tool, and the instance's own sign-in is resolved with the rest."""
    if instance is None or GITHUB_PULL_REQUEST_TOOL not in granted.get((GITHUB_TOOLSET, instance), frozenset()):
        return toolsets
    tool = {
        "name": GITHUB_PULL_REQUEST_TOOL,
        "fullName": f"{GITHUB_TOOLSET}.{GITHUB_PULL_REQUEST_TOOL}",
        "toolsetName": GITHUB_TOOLSET,
    }
    for index, toolset in enumerate(toolsets):
        if str(toolset.get("instanceId") or "") != instance:
            continue
        tools = list(toolset.get("tools") or [])
        if any(t.get("name") == GITHUB_PULL_REQUEST_TOOL for t in tools):
            return toolsets
        return [*toolsets[:index], {**toolset, "tools": [*tools, tool]}, *toolsets[index + 1 :]]
    return [
        *toolsets,
        {
            "instanceId": instance,
            "name": GITHUB_TOOLSET,
            "toolsetType": GITHUB_TOOLSET,
            "displayName": "GitHub",
            "tools": [tool],
        },
    ]


def _narrow_mcp(
    agent_servers: list[dict[str, Any]], granted: dict[str, frozenset[str]], whole: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for server in agent_servers:
        if str(server.get("instanceId")) in whole:
            # Granted whole (D-LAZY-2): attached unfiltered, its tools whatever its first listing offers.
            kept.append({k: v for k, v in server.items() if k != "tools"})
            continue
        names = granted.get(str(server.get("instanceId")))
        if not names:
            continue
        tools = [t for t in server.get("tools") or [] if t.get("name") in names]
        if tools:
            kept.append({**server, "tools": tools})
    return kept


def _masked_tools(server: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    from app.agents.mcp.service import mcp_tool_full_name

    tools = cast("list[dict[str, Any]]", server.get("tools") or [])
    return tuple(
        sorted(
            (str(t.get("fullName") or mcp_tool_full_name(server, str(t["name"]))), str(t["name"]))
            for t in tools
            if t.get("name")
        )
    )


def _attachment(attachment: Attachment) -> dict[str, str]:
    """Back to the admitted wire shape the attachment resolver reads."""
    wire = {
        "recordId": attachment.record_id,
        "recordName": attachment.record_name,
        "mimeType": attachment.mime_type,
        "extension": attachment.extension,
        "virtualRecordId": attachment.virtual_record_id,
        "source": attachment.source,
    }
    return {key: value for key, value in wire.items() if value is not None}


def _refused(error_type: str, exc: HTTPException) -> RunRefused:
    return RunRefused(error_type, str(exc.detail))


# A run admitted before agent versions (OWD-14) pinned the agent's edit stamp, epoch milliseconds, which
# names no version: it runs the live definition as it always did. Any smaller number is a version.
EDIT_STAMP_FLOOR = 10**12


async def pinned_agent_version(
    graph: Any, run: RunContext, ref: AgentRef, live: dict[str, Any]  # noqa: ANN401 - IGraphDBProvider
) -> dict[str, Any]:
    """The agent as its pinned version defines it, with the live agent's identity (deletion, owner, sharing).

    A pinned version the store does not hold (versions not yet migrated, a graph restored from a backup without
    them) is never a reason to refuse a run that ran before versions: the run takes the latest published version
    instead, and an agent with none gets version 1 from its live definition, as its first read would give it.
    What the run may use is still the pin's grant, which narrows whichever definition it reads."""
    from app.agents.versions import AgentVersionStore, pinned_agent

    if ref.definition_version >= EDIT_STAMP_FLOOR:
        return live
    store = AgentVersionStore(graph)
    version = await store.get(run.org_id, ref.agent_key, ref.definition_version)
    if version is None:
        version = await store.current(run.org_id, ref.agent_key, live)
        _log(run).warning(
            "run.agent_version_unavailable",
            agentKey=ref.agent_key, pinned=ref.definition_version, used=version.version,
        )
    return pinned_agent(live, version)


def _log(run: RunContext) -> HarnessLog:
    return HarnessLog.named(
        "app.harness.assembly", orgId=run.org_id, sessionId=run.session_id, runId=run.run_id
    )


def _admit_without(run: RunContext, item: dict[str, Any], name: str, refusal: str) -> None:
    """The run goes on without an item its owner has not connected, unless the item is marked ``required``
    (nothing marks one yet: the hook for a grant that must refuse admission instead)."""
    _log(run).warning("run.item_unavailable", reason="credential_missing", instance=item.get("instanceId"))
    if item.get("required"):
        raise RunRefused(refusal, f"{name} must be connected before this agent can run")


def is_system_assistant(run: RunContext) -> bool:
    """The run pins the system assistant definition (ASSIST1): the built-in assistant's Agent mode."""
    return run.agent is not None and run.agent.agent_key == ASSISTANT_AGENT_KEY


def _granted_mcp_tools(
    servers: list[dict[str, Any]], granted: dict[str, frozenset[str]], whole: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """The assistant's servers carry no tool list (live discovery names them); the pin's grant does, unless it
    grants the server whole (D-LAZY-2)."""
    from app.agents.mcp.service import mcp_tool_full_name

    return [
        server
        if str(server.get("instanceId")) in whole
        else {
            **server,
            "tools": [
                {"name": name, "fullName": mcp_tool_full_name(server, name)}
                for name in sorted(granted.get(str(server.get("instanceId")), frozenset()))
            ],
        }
        for server in servers
    ]


async def _system_assistant(
    run: RunContext, services: RunServices, graph: Any, granted_mcp: dict[str, frozenset[str]]  # noqa: ANN401
) -> dict[str, Any]:
    """What Universal Agent Mode gives the actor now (``get_assistant_agent``); the pin narrows it below, as a
    saved agent read live is narrowed."""
    from app.agents.mcp.service import MCPInstancesUnavailable
    from app.agents.registry.toolset_registry import get_toolset_registry
    from app.api.routes.agent import get_assistant_agent

    catalog = get_toolset_registry()
    if not catalog.get_all_toolsets():
        # The query service discovers at startup; a harness worker on its first run (as the toolset source does).
        await asyncio.to_thread(catalog.auto_discover_toolsets)
    try:
        # A toolset whose saved sign-in was withdrawn stays the actor's (WP-64): the pin's grant of it resolves, and
        # its tools wait on the connect card, as an agent's unconnected toolset's do, never a lost capability.
        agent, _ = await get_assistant_agent(
            run.actor_id, run.org_id, services.config_service(), graph, catalog, services.logger(),
            include_withdrawn_toolsets=True,
        )
    except MCPInstancesUnavailable as exc:
        raise RunRefused("mcp_server_config_missing", "the organization's MCP servers could not be read") from exc
    return {
        **agent,
        "mcpServers": _granted_mcp_tools(
            list(agent.get("mcpServers") or []), granted_mcp, frozenset(run.mcp_at_first_use)
        ),
    }


async def _identity(
    run: RunContext,
    agent: dict[str, Any] | None,
    graph: Any,  # noqa: ANN401
    logger: Any,  # noqa: ANN401
    *,
    user_context_enabled: bool | None = None,
) -> tuple[dict[str, Any], bool]:
    """The user info the tools act as, and whether it is the agent's service account. ``user_context_enabled``:
    the Labs switch the built-in assistant honours (the system assistant definition); an agent has its own."""
    from fastapi import HTTPException

    from app.api.routes.agent import (
        AgentNotFoundError,
        _apply_user_context_gate,
        _enrich_user_info,
        _enrich_user_info_for_service_account_agent_chat,
        _get_user_document,
        _resolve_service_account_caller_identity,
    )

    user_context = {"userId": run.actor_id, "orgId": run.org_id}
    try:
        if agent is not None and agent.get("isServiceAccount", False):
            enriched = await _enrich_user_info_for_service_account_agent_chat(
                agent, graph, logger, run.org_id
            )
            caller = SimpleNamespace(
                callerDisplayName=run.caller_display_name, callerEmail=run.caller_email
            )
            enriched = await _resolve_service_account_caller_identity(
                enriched, caller, user_context, graph, logger  # type: ignore[arg-type]
            )
            return enriched, True
        user_doc = await _get_user_document(run.actor_id, graph, logger)
        enriched = await _enrich_user_info(user_context, user_doc)
        if agent is not None and run.agent is not None and not is_system_assistant(run):
            permitted = await graph.check_agent_permission(
                run.agent.agent_key, user_doc["_key"], run.org_id
            )
            if not permitted:
                raise RunRefused("agent_access_revoked", "the actor can no longer use this agent")
            _apply_user_context_gate(enriched, enabled=bool(agent.get("sendUserContext", True)))
        elif user_context_enabled is not None:
            _apply_user_context_gate(enriched, enabled=user_context_enabled)
    except AgentNotFoundError as exc:
        raise RunRefused("agent_not_found", str(exc)) from exc
    except HTTPException as exc:
        raise _refused("identity_unavailable", exc) from exc
    return enriched, False


async def _artifact_owner(run: RunContext, graph: Any) -> str | None:  # noqa: ANN401
    """Who owns what a service-account agent makes in this run: the person chatting (main #4433), as the agent
    route decides it; a service identity (``via`` service: the Slack bot) leaves it the agent creator's."""
    from fastapi import HTTPException

    from app.api.routes.agent import _service_account_artifact_owner

    caller = {"userId": run.actor_id, "orgId": run.org_id, "isServiceAccount": run.via == "service"}
    try:
        return await _service_account_artifact_owner(caller, graph, run.org_id)
    except HTTPException as exc:
        raise _refused("identity_unavailable", exc) from exc


async def _turn_scope(
    run: RunContext,
    agent: dict[str, Any] | None,
    user_info: dict[str, Any],
    graph: Any,  # noqa: ANN401
    logger: Any,  # noqa: ANN401
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """This attempt's filters and knowledge, as main's agent route builds them (``_resolve_turn_filters``) from the
    pin: the agent's live sources bounded by the pinned limits, the selection resolved again as the identity that
    searches, the agent's sources bound for reads by id. Projects route legacy, so no project bound."""
    from app.api.routes.agent import (
        _filter_knowledge_by_enabled_sources,
        _resolve_turn_filters,
    )
    from app.harness.assembly.scope import (
        bounded_agent_knowledge,
        requested_filters,
        selection_refused,
    )
    from app.modules.retrieval.selection_scope import (
        SelectionNotReadyError,
        SelectionTooLargeError,
        has_selection,
    )

    agent_id = run.agent.agent_key if run.agent is not None else ASSISTANT_AGENT_KEY
    universal = agent_id == ASSISTANT_AGENT_KEY
    knowledge = list((agent or {}).get("knowledge") or [])
    if not universal:
        knowledge = await bounded_agent_knowledge(knowledge, run.knowledge.source_limits, graph, run.org_id)
    requested = requested_filters(run.knowledge)
    try:
        filters = await _resolve_turn_filters(
            agent_id=agent_id,
            agent_knowledge=knowledge,
            requested_filters=requested,
            graph_provider=graph,
            caller_user_id=run.actor_id,
            org_id=run.org_id,
            logger=logger,
            project_sources=None,
            retrieval_user_id=user_info.get("userId"),
        )
    except (SelectionTooLargeError, SelectionNotReadyError) as exc:
        raise selection_refused(exc, agent_limit=not universal and not has_selection(requested)) from exc
    if not filters.get("kb") and not universal:
        filters["kb"] = [NO_KB_SELECTED]
    if run.knowledge.strict_scope:
        filters["strictScope"] = True
    return filters, _filter_knowledge_by_enabled_sources(knowledge, filters)


async def build_tool_environment(
    run: RunContext, services: RunServices, model: ResolvedModel
) -> ToolEnvironment:
    if run.chat is not None:
        from app.harness.assembly.chat import build_chat_environment

        return await build_chat_environment(run, services, model)
    return await _agent_environment(run, services, model)


async def _agent_environment(
    run: RunContext, services: RunServices, model: ResolvedModel
) -> ToolEnvironment:
    from fastapi import HTTPException

    from app.agents.agent_loop.context import AgentContext
    from app.agents.chat_modes.custom_instructions import resolve_custom_instructions
    from app.agents.chat_modes.policy import AGENT_POLICY
    from app.agents.chat_modes.setup import TurnScopeRefused, refuse_unreadable_scope
    from app.api.routes.agent import (
        _get_org_info,
        _resolve_mcp_server_configs,
        _resolve_toolset_configs,
    )
    from app.api.routes.chatbot import load_system_prompts
    from app.harness.assembly.items.credentials import bound_installations
    from app.harness.assembly.items.pins import pinned_items
    from app.harness.credentials.tool_credentials import unavailable_reference
    from app.modules.agents.capability_summary import fetch_connector_configs
    from app.modules.agents.qna.chat_state import build_initial_state
    from app.modules.code_graph.connectors import connector_instances_have_code
    from app.modules.demo_data.chat import (
        demo_exclusions_for_run,
        exclude_from_query,
        exclude_from_state,
        note_org_real_data,
    )
    from app.services.featureflag.platform_settings import is_user_context_enabled
    from app.utils.attachment_utils import keep_accessible_attachments
    from app.utils.connector_instances import fetch_user_connector_instances
    from app.utils.execute_query import connector_instances_have_sql
    from app.utils.fetch_slack_thread import connector_instances_have_slack
    from app.web.config import resolve_web_search

    logger = services.logger()
    config_service = services.config_service()
    graph = await services.graph_provider()
    retrieval = await services.retrieval_service()

    granted_toolsets, granted_mcp, web_provider = _grants(run)
    system_assistant = is_system_assistant(run)
    agent: dict[str, Any] | None = None
    if system_assistant:
        agent = await _system_assistant(run, services, graph, granted_mcp)
    elif run.agent is not None:
        agent = await graph.get_agent(run.agent.agent_key, run.org_id)
        if not agent:
            raise RunRefused("agent_not_found", f"agent {run.agent.agent_key} is gone")
        agent = await pinned_agent_version(graph, run, run.agent, agent)
    user_info, is_service_account = await _identity(
        run, agent, graph, logger,
        user_context_enabled=await is_user_context_enabled(config_service) if system_assistant else None,
    )
    try:
        org_info = await _get_org_info({"orgId": run.org_id}, graph, logger)
    except HTTPException as exc:
        raise _refused("org_unavailable", exc) from exc

    toolsets = with_repository_toolset(
        _narrow_toolsets(list((agent or {}).get("toolsets") or []), granted_toolsets, _log(run)),
        granted_toolsets,
        run.repository_toolset,
    )
    mcp_servers = _narrow_mcp(
        list((agent or {}).get("mcpServers") or []), granted_mcp, frozenset(run.mcp_at_first_use)
    )
    credential_owner = (
        run.agent.agent_key if is_service_account and run.agent is not None else run.actor_id
    )
    # An installation whose record is its pinned binding's (CPD-10) is leased when its item loads: its old
    # record is neither read nor required here.
    bound = bound_installations(pinned_items(run))
    agent_label = run.agent.agent_key if run.agent is not None and not system_assistant else "assistant"

    unavailable: list[UnavailableItem] = []
    toolset_configs: dict[str, Any] = {}
    if toolsets:
        toolset_configs, ready, _, _ = await _resolve_toolset_configs(
            config_service,
            toolsets,
            credential_lookup_id=credential_owner,
            executing_user_id=run.actor_id,
            prefetched={},
            agent_id=agent_label,
            is_service_account=is_service_account,
            logger=logger,
            bound=bound,
        )
        for toolset in (t for t in toolsets if t not in ready):
            name = (
                toolset.get("instanceName")
                or toolset.get("displayName")
                or str(toolset.get("name", "")).replace("_", " ").title()
            )
            _admit_without(run, toolset, name, "toolset_config_missing")
            unavailable.append(UnavailableItem("toolset", str(toolset.get("instanceId")), name))
            toolset_configs[toolset.get("instanceId")] = unavailable_reference(name)
    mcp_server_configs: dict[str, Any] = {}
    if mcp_servers:
        mcp_server_configs, ready, _, _ = await _resolve_mcp_server_configs(
            config_service,
            mcp_servers,
            org_id=run.org_id,
            credential_lookup_id=credential_owner,
            executing_user_id=run.actor_id,
            agent_id=agent_label,
            is_service_account=is_service_account,
            logger=logger,
            bound=bound,
        )
        for server in (s for s in mcp_servers if s not in ready):
            name = server.get("displayName") or server.get("name") or str(server.get("instanceId"))
            _admit_without(run, server, name, "mcp_server_config_missing")
            unavailable.append(UnavailableItem(MCP_SOURCE, str(server.get("instanceId")), name, _masked_tools(server)))
        mcp_servers = ready

    filters, agent_knowledge = await _turn_scope(run, agent, user_info, graph, logger)
    attachments, previous_conversations = await keep_accessible_attachments(
        graph,
        org_id=run.org_id,
        user_id=run.actor_id,
        is_service_account=False,
        attachments=[_attachment(a) for a in run.attachments],
        previous_conversations=[dict(turn) for turn in run.previous_conversations],
        logger=logger,
    )
    connector_ids = [k["connectorId"] for k in agent_knowledge if k.get("connectorId")]
    web_search_config = (
        await resolve_web_search(config_service, logger, provider=web_provider)
        if web_provider
        else None
    )
    artifact_owner = await _artifact_owner(run, graph) if is_service_account else None
    query_info: dict[str, Any] = {
        "query": run.query,
        "messages": [],
        "previous_conversations": previous_conversations,
        "chatMode": run.chat_mode,
        "filters": filters,
        "systemPrompt": (agent or {}).get("systemPrompt"),
        "instructions": (agent or {}).get("instructions"),
        # Universal Agent Mode is still the assistant: the org's Agent custom instructions apply, as legacy's.
        "custom_instructions": (
            resolve_custom_instructions(await load_system_prompts(config_service, logger), AGENT_POLICY)
            if system_assistant
            else None
        ),
        "timezone": run.timezone,
        "currentTime": run.prompt_time,
        "locale": run.locale,
        "includeRetrievalContext": run.include_retrieval_context,
        "keywordSearchBudget": run.keyword_search_budget,
        "toolsets": toolsets,
        "mcpServers": mcp_servers,
        "mcpServerConfigs": mcp_server_configs,
        "knowledge": agent_knowledge,
        "skills": list(run.skills) or None,
        "connector_configs": await fetch_connector_configs(config_service, connector_ids),
        "toolsetConfigs": toolset_configs,
        "conversationId": run.session_id,
        "is_service_account": is_service_account,
        "credentialOwner": credential_owner,
        "artifact_owner_user_id": artifact_owner,
        "isPlaceholderAgent": agent is None or system_assistant,
        "modelName": run.model.model_name,
        "modelKey": run.model.model_key,
        "webSearch": web_provider,
        "webSearchConfig": web_search_config,
        "deepResearch": run.deep_research,
        "attachments": attachments,
        "runId": run.run_id,
    }
    connector_instances = await fetch_user_connector_instances(
        graph, user_info["userId"], run.org_id, logger
    )
    excluded = await demo_exclusions_for_run(graph, config_service, user_info, logger)
    query_info = exclude_from_query(query_info, excluded)
    entity_store = await services.entity_vector_store(run.org_id)
    state = build_initial_state(
        query_info,
        user_info,
        model.chat_model(),
        logger,
        retrieval,
        graph,
        None,
        config_service,
        run.model.model_name,
        run.model.model_key,
        org_info,
        "react",
        has_sql_connector=connector_instances_have_sql(connector_instances),
        is_multimodal_llm=model.is_multimodal,
        has_slack_connector=connector_instances_have_slack(connector_instances),
        # Main #3841: the code-graph toolset loads where a repo connector is configured and the agent knows it.
        has_code_connector=connector_instances_have_code(connector_instances),
        entity_vector_store=entity_store,
    )
    # Agent mode is the assistant's bridge on main, which refuses a search-only turn over an unreadable scope;
    # main's saved-agent route does not.
    if system_assistant and state.get("has_knowledge") and web_provider is None and not attachments:
        try:
            await refuse_unreadable_scope(graph, user_info, run.org_id, filters, logger)
        except TurnScopeRefused as refused:
            raise RunRefused(refused.code, refused.message) from refused
    exclude_from_state(state, excluded)
    await note_org_real_data(state, graph, run.org_id, logger)
    # A durable run answers a question whatever its surface: it ends on needs_input and the reply resumes it.
    state["has_ui_client"] = True
    context = AgentContext.from_chat_state(
        state,
        protocol="agui",
        llm_provider=model.provider,
        context_length=model.context_length,
        configured_context_window=model.context_length,
        is_reasoning_model=model.is_reasoning,
        run_id=run.run_id,
    )
    _log(run).info(
        "run.tool_environment",
        toolsets=len(toolsets),
        mcpServers=len(mcp_servers),
        knowledgeSources=len(agent_knowledge),
        webSearch=web_search_config is not None,
        serviceAccount=is_service_account,
    )
    return ToolEnvironment(
        context=context,
        agent=agent,
        web_search_provider=web_provider if web_search_config else None,
        granted_toolsets=granted_toolsets,
        granted_mcp=granted_mcp,
        unavailable=tuple(unavailable),
    )


@contextmanager
def _tool_call(helper_models: RunModelScope | None) -> Iterator[None]:
    with in_tool_call():
        if helper_models is None:
            yield
            return
        with run_model_scope(helper_models):
            yield


__all__ = [
    "MCP_SOURCE",
    "WEB_SOURCE",
    "ToolEnvironment",
    "UnavailableItem",
    "bound_instances",
    "build_tool_environment",
    "parse_tool_id",
    "toolset_tool_id",
]
