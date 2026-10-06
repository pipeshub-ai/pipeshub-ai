"""Create and update agents. The routes in `app/api/routes/agent.py` only
translate HTTP into `AgentActor` / `AgentSpec` / `AgentPatch`; the rules, the
graph writes and their transaction boundaries live here."""

import json
import uuid
from collections.abc import Awaitable
from logging import Logger
from typing import Any

from fastapi import HTTPException

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.modules.agents import handles
from app.modules.agents.handle_allocator import HandlesExhaustedError, claim, suggest
from app.modules.agents.service.access_validator import AccessValidator
from app.modules.agents.service.builders import (
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
    AgentNotFoundError,
    HandleInvalidError,
    HandleReservedError,
    HandleTakenError,
    InvalidRequestError,
    PermissionDeniedError,
)
from app.modules.agents.service.models import (
    AgentActor,
    AgentOrigin,
    AgentPatch,
    AgentSpec,
    ChatProvenance,
    CreatedAgent,
)
from app.services.graph_db.errors import UniqueConstraintViolation
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.user_messages import action_failed


def _normalize_handle(raw: str | None) -> str | None:
    cleaned = (raw or "").strip().removeprefix("@")
    return cleaned or None


def _assert_valid_handle(handle: str) -> None:
    if not handles.is_valid_format(handle):
        raise HandleInvalidError(handle)
    if handles.is_reserved(handle):
        raise HandleReservedError(handle)


class AgentService:
    def __init__(
        self,
        graph: IGraphDBProvider,
        config: ConfigurationService,
        logger: Logger,
    ) -> None:
        self._graph = graph
        self._config = config
        self._logger = logger
        self._access = AccessValidator(graph, config, logger)

    async def create(
        self,
        actor: AgentActor,
        spec: AgentSpec,
        *,
        origin: AgentOrigin = "ui",
        provenance: ChatProvenance | None = None,
    ) -> CreatedAgent:
        if provenance is not None:
            # A second click, a reload of the card or a retried request returns the agent the draft already made.
            existing = await self._created_from(actor, provenance)
            if existing is not None:
                return existing
        requested = _normalize_handle(spec.handle)
        if requested is not None:
            _assert_valid_handle(requested)
        spec = await self._access.enforce_create(actor, spec, origin)

        def write(handle: str) -> Awaitable[CreatedAgent]:
            return self._create_once(actor, spec, origin=origin, provenance=provenance, handle=handle)

        if requested is not None:
            try:
                created = await write(requested)
            except UniqueConstraintViolation as e:
                raise HandleTakenError(requested, await suggest(self._graph, actor.org_id, requested)) from e
        else:
            try:
                _, created = await claim(self._graph, actor.org_id, handles.slugify(spec.name), write)
            except HandlesExhaustedError as e:
                raise HTTPException(status_code=500, detail=action_failed("create this agent")) from e
        self._audit_create(created.agent_key, actor, origin, provenance)
        return created

    async def _created_from(self, actor: AgentActor, provenance: ChatProvenance) -> CreatedAgent | None:
        """The live agent this actor already created from that draft, if any."""
        rows = await self._graph.get_nodes_by_filters(
            CollectionNames.AGENT_INSTANCES.value,
            {
                "sourceMessageId": provenance.message_id,
                "sourceConversationId": provenance.conversation_id,
                "createdBy": actor.user_key,
                "orgId": actor.org_id,
                "isDeleted": False,
            },
        )
        agent = rows[0] if rows else None
        key = (agent or {}).get("_key") or (agent or {}).get("id")
        if not agent or not isinstance(key, str):
            return None
        handle = agent.get("handle")
        return CreatedAgent(
            agent_key=key,
            handle=handle if isinstance(handle, str) else None,
            agent={**agent, "_key": key, "createdBy": actor.user_id},
        )

    def _audit_create(
        self, agent_key: str, actor: AgentActor, origin: AgentOrigin, provenance: ChatProvenance | None,
    ) -> None:
        """Ids only: no name, prompt or handle ever goes in the log."""
        self._logger.info(
            "agent.audit %s",
            json.dumps({
                "action": "create",
                "agentKey": agent_key,
                "actor": actor.user_id,
                "orgId": actor.org_id,
                "createdVia": origin,
                "sourceConversationId": provenance.conversation_id if provenance else None,
            }),
        )

    async def _change_handle(
        self, agent: dict[str, Any], agent_id: str, org_key: str, user_key: str, raw: object,
    ) -> None:
        requested = _normalize_handle(raw if isinstance(raw, str) else None)
        if requested is None:
            return
        _assert_valid_handle(requested)
        if agent.get("handle") == requested and agent.get("orgId") == org_key:
            return
        try:
            await self._graph.update_node(
                agent_id,
                CollectionNames.AGENT_INSTANCES.value,
                {
                    "handle": requested,
                    # Agents created before orgId existed get it with their first handle.
                    "orgId": org_key,
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                    "updatedBy": user_key,
                },
            )
        except UniqueConstraintViolation as e:
            raise HandleTakenError(requested, await suggest(self._graph, org_key, requested)) from e
        agent["handle"] = requested

    async def _create_once(
        self,
        actor: AgentActor,
        spec: AgentSpec,
        *,
        origin: AgentOrigin,
        provenance: ChatProvenance | None,
        handle: str,
    ) -> CreatedAgent:
        graph_provider = self._graph
        logger = self._logger
        user_key = actor.user_key
        org_key = actor.org_id
        user_context = {"userId": actor.user_id, "orgId": actor.org_id}
        time = get_epoch_timestamp_in_ms()

        # Parse and validate models
        raw_models = spec.models
        model_entries, has_reasoning_model = _parse_models(raw_models, logger)
        default_reasoning_effort = _parse_default_reasoning_effort(spec.default_reasoning_effort)

        # Models are optional: an agent created without any models falls back
        # to the organization's default LLM at chat time (see get_llm_for_chat).
        # When models ARE specified, at least one must be a reasoning model so
        # reasoning-effort settings behave predictably.
        if model_entries and not has_reasoning_model:
            raise InvalidRequestError(
                "When models are specified, at least one reasoning model is required."
            )

        # Parse toolsets, knowledge, skills, and MCP servers BEFORE starting transaction
        toolsets_with_tools = _parse_toolsets(spec.toolsets)
        mcp_servers_with_tools = _parse_mcp_servers(spec.mcp_servers)
        knowledge_sources = _parse_knowledge_sources(spec.knowledge)
        skill_names = _parse_skills(spec.skills)
        web_search_attachment = _parse_web_search(spec.web_search)

        # Validate shareWithOrg + toolsets combination BEFORE starting transaction
        is_service_account = spec.is_service_account
        if sa_forces_org_sharing() and is_service_account:
            share_with_org = True
        else:
            share_with_org = spec.share_with_org

        # Create agent document
        agent_key = str(uuid.uuid4())
        agent = {
            "_key": agent_key,
            "name": spec.name.strip(),
            "description": spec.description.strip() or "AI agent for task automation",
            "startMessage": spec.start_message.strip() or "Hello! How can I help you today?",
            "systemPrompt": spec.system_prompt.strip() or "You are a workplace productivity assistant. Help users with their connected work tools.",
            "instructions": spec.instructions.strip() or None,
            "models": model_entries,
            "tags": spec.tags or [],
            "webSearch": web_search_attachment,
            "defaultReasoningEffort": default_reasoning_effort,
            "isActive": True,
            "isServiceAccount": is_service_account,
            "sendUserContext": spec.send_user_context,
            "createdBy": user_key,
            "updatedBy": None,
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
            "isDeleted": False,
            "orgId": org_key,
            "handle": handle,
            "createdVia": origin,
        }
        if provenance is not None:
            agent["sourceConversationId"] = provenance.conversation_id
            agent["sourceMessageId"] = provenance.message_id

        # Wrap ALL creation operations in a single transaction
        created_toolsets = []
        failed_toolsets = []
        created_mcp_servers: list[dict[str, Any]] = []
        failed_mcp_servers: list[dict[str, Any]] = []
        created_knowledge = []
        linked_skills: list[str] = []

        try:
            # Start transaction for ALL agent creation operations
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
                        "userId": actor.user_id,
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

            if isinstance(e, UniqueConstraintViolation):
                raise
            logger.error(f"Failed to create agent {agent_key}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=action_failed("create this agent")
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
        response_agent["createdBy"] = actor.user_id

        return CreatedAgent(
            agent_key=agent_key,
            handle=handle,
            agent=response_agent,
            warnings=failed_toolsets + failed_mcp_servers,
        )


    async def update(
        self,
        actor: AgentActor,
        agent_id: str,
        patch: AgentPatch,
    ) -> dict[str, Any]:
        graph_provider = self._graph
        logger = self._logger
        user_key = actor.user_key
        org_key = actor.org_id
        user_context = {"userId": actor.user_id, "orgId": actor.org_id}
        body = patch.to_update_body()

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

        # Check permissions first, then fetch full agent data
        perm = await graph_provider.check_agent_permission(agent_id, user_key, org_key)
        if not perm:
            raise AgentNotFoundError(agent_id)

        if not perm.get("can_edit", False):
            raise PermissionDeniedError("edit this agent (only owner can edit)")

        agent = await graph_provider.get_agent(agent_id, org_key)
        if not agent:
            raise AgentNotFoundError(agent_id)

        agent.update(perm)
        await self._access.enforce_update(actor, body, agent)

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

        # After every validation, before the first other write: a taken handle must not leave a partial save.
        if "handle" in body:
            await self._change_handle(agent, agent_id, org_key, user_key, body.pop("handle"))

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
                await graph_provider.batch_create_edges(
                    [org_permission_edge], CollectionNames.PERMISSION.value
                )
                logger.info(f"Created org permission edge for agent {agent_id}")

            elif not new_share_with_org and current_share_with_org:
                if sa_forces_org_sharing() and bool(agent.get("isServiceAccount", False)):
                    raise InvalidRequestError(
                        "Cannot disable org-wide sharing for a service account agent. "
                        "Service account agents must always be shared across the organisation."
                    )
                # Turning OFF org sharing: delete the org permission edge
                await graph_provider.delete_edge(
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
        result = await graph_provider.update_agent(agent_id, body, user_key, org_key)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to update agent")

        # Update toolsets if provided in request (even if empty array - means delete all)
        if "toolsets" in body:
            # Parse toolsets first to validate before deletion
            toolsets_with_tools = _parse_toolsets(body.get("toolsets", []))

            # Use transaction for atomic delete-then-create operation
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
                    detail=action_failed("save this agent")
                ) from e

            # Create new toolset nodes, tool nodes, and edges only if there are toolsets to create
            if toolsets_with_tools:
                try:
                    created_toolsets, failed_toolsets = await _create_toolset_edges(
                        agent_id, toolsets_with_tools, user_context, user_key,
                        graph_provider, logger
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
                        detail=action_failed("save this agent")
                    ) from e
            else:
                logger.info(f"All toolsets removed for agent {agent_id}")

        # Update attached MCP servers if provided in request (even if empty array - means detach all)
        if "mcpServers" in body:
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
                    detail=action_failed("save this agent")
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
                        graph_provider, logger, transaction=create_transaction_id
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
                        detail=action_failed("save this agent")
                    ) from e
            else:
                logger.info(f"All MCP servers detached for agent {agent_id}")

        # Update knowledge if provided in request (even if empty array - means delete all)
        if "knowledge" in body:
            # Parse knowledge sources first to validate before deletion
            knowledge_sources = _parse_knowledge_sources(body.get("knowledge", []))

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
                logger.debug(f"Started transaction for knowledge update on agent {agent_id}")

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

                logger.debug(f"Found {len(knowledge_keys)} knowledge node(s) connected to agent {agent_id}")

                # New knowledge is written before the old is removed: on a backend whose
                # rollback does not undo writes (Neo4j without explicit transactions), a
                # failure then leaves the agent with its old knowledge rather than none,
                # and the except block below removes what the failed attempt wrote.
                if knowledge_sources:
                    created_knowledge = await _create_knowledge_edges(
                        agent_id, knowledge_sources, user_key, graph_provider, logger,
                        transaction=transaction_id, written_keys=new_knowledge_keys,
                    )
                    logger.info(f"Created {len(created_knowledge)} knowledge source(s) for agent {agent_id}")
                else:
                    logger.info(f"All knowledge sources removed for agent {agent_id}")

                total_knowledge_edges_deleted = 0
                for knowledge_full_id in knowledge_full_ids:
                    count = await graph_provider.delete_all_edges_for_node(
                        knowledge_full_id,
                        CollectionNames.AGENT_HAS_KNOWLEDGE.value,
                        transaction=transaction_id
                    )
                    deleted_old_ids.append(knowledge_full_id)
                    total_knowledge_edges_deleted += count

                deleted_knowledge_nodes = 0
                if knowledge_keys:
                    result = await graph_provider.delete_nodes(
                        knowledge_keys,
                        CollectionNames.AGENT_KNOWLEDGE.value,
                        transaction=transaction_id
                    )
                    deleted_knowledge_nodes = len(knowledge_keys) if result else 0

                logger.info(
                    f"Deleted for agent {agent_id}: "
                    f"{deleted_knowledge_nodes} knowledge node(s), {total_knowledge_edges_deleted} edge(s)"
                )

                await graph_provider.commit_transaction(transaction_id)
                transaction_id = None
                logger.debug(f"Committed transaction for knowledge update on agent {agent_id}")

            except Exception as e:
                if transaction_id:
                    try:
                        await graph_provider.rollback_transaction(transaction_id)
                        logger.warning(f"Aborted transaction for knowledge update on agent {agent_id}")
                    except Exception as abort_error:
                        logger.error(f"Failed to abort transaction: {abort_error}")
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
                logger.error(f"Failed to replace knowledge for agent {agent_id}: {e}", exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail=action_failed("save this agent")
                ) from e

        # Update skill assignments if provided in request (even if empty array - means unassign all).
        # Unlike toolsets/knowledge, this never deletes NODES — only this agent's
        # AGENT_HAS_SKILL edges — since skills are owned by the Skills
        # management API, not by whichever agent happens to reference them.
        if "skills" in body:
            skill_names = _parse_skills(body.get("skills", []))
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
                    status_code=500, detail=action_failed("save this agent"),
                ) from e

        return {"status": "success", "message": "Agent updated successfully"}
