"""Create and update agents. The routes in `app/api/routes/agent.py` only
translate HTTP into `AgentActor` / `AgentSpec` / `AgentPatch`; the rules, the
graph writes and their transaction boundaries live here."""

import json
import uuid
from collections.abc import Mapping
from logging import Logger
from typing import TYPE_CHECKING, Any, ClassVar

from fastapi import HTTPException

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_store.graph_data_store import (
    GraphDataStore,
    ensure_written,
)
from app.modules.agents.service.access_validator import AccessValidator
from app.modules.agents.service.builders import (
    SPLIT_PATH_EXPECTED_PARTS,
    _assert_mcp_servers_exist,
    _assert_no_personal_mcp_servers,
    _assert_no_personal_toolsets,
    _assert_toolsets_visible,
    _attach_mcp_servers_to_new_agent,
    _attached_personal_instances,
    _attached_personal_toolsets,
    _bind_mcp_attachments_to_instances,
    _checked_api_toolset_tools,
    _create_knowledge_edges,
    _create_mcp_server_edges,
    _create_skill_edges,
    _create_toolset_edges,
    _finish_removing_old_knowledge,
    _finish_unlinking_old,
    _format_web_search_for_response,
    _mcp_attachments_unreadable,
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
    _remove_toolsets,
    _toolset_attachments_unreadable,
    sa_forces_org_sharing,
)
from app.modules.agents.service.errors import (
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
    CreatedAgent,
)
from app.services.graph_db.errors import UniqueConstraintViolation
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.user_messages import action_failed

if TYPE_CHECKING:
    from app.connectors.core.base.data_store.data_store import TransactionStore


class AgentService:
    spec_model: ClassVar[type[AgentSpec]] = AgentSpec

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
        spec = await self._access.enforce_create(actor, spec, origin)
        created = await self._create_once(actor, spec)
        self._audit_create(created.agent_key, actor, origin, provenance)
        return created

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

    async def _create_once(
        self,
        actor: AgentActor,
        spec: AgentSpec,
        *,
        extra_fields: Mapping[str, Any] | None = None,
    ) -> CreatedAgent:
        """Write the agent and its attachments. `extra_fields` go on the agent document."""
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
        attached_toolsets = (
            await _assert_toolsets_visible(toolsets_with_tools, self._config, org_key, actor.user_id)
            if _names_an_instance(toolsets_with_tools) else {}
        )
        mcp_servers_with_tools = _parse_mcp_servers(spec.mcp_servers)
        attached_mcp_instances = (
            await _assert_mcp_servers_exist(mcp_servers_with_tools, self._config, org_key, actor.user_id)
            if mcp_servers_with_tools else {}
        )
        mcp_servers_with_tools = _bind_mcp_attachments_to_instances(mcp_servers_with_tools, attached_mcp_instances)
        knowledge_sources = _parse_knowledge_sources(spec.knowledge)
        await _refuse_limit_over_the_cap(knowledge_sources, graph_provider, org_key, logger)
        skill_names = _parse_skills(spec.skills)
        web_search_attachment = _parse_web_search(spec.web_search)

        # Validate shareWithOrg + toolsets combination BEFORE starting transaction
        is_service_account = spec.is_service_account
        if sa_forces_org_sharing() and is_service_account:
            share_with_org = True
        else:
            share_with_org = spec.share_with_org
        _assert_no_personal_mcp_servers(
            attached_mcp_instances, shared_with_org=share_with_org, service_account=is_service_account,
        )
        _assert_no_personal_toolsets(attached_toolsets, shared_with_org=share_with_org, service_account=is_service_account)
        api_toolset_tools = await _checked_api_toolset_tools(
            spec.api_toolset_tools, self._config, org_key, service_account=is_service_account,
        )

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
            "apiToolsetTools": api_toolset_tools,
            "isActive": True,
            "isServiceAccount": is_service_account,
            "sendUserContext": spec.send_user_context,
            "createdBy": user_key,
            "updatedBy": None,
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
            "isDeleted": False,
            "orgId": org_key,
            **(extra_fields or {}),
        }

        # Everything but the MCP servers is written in one transaction; those are
        # attached after it commits (see _attach_mcp_servers_to_new_agent).
        created_toolsets = []
        failed_toolsets = []
        created_mcp_servers: list[dict[str, Any]] = []
        failed_mcp_servers: list[dict[str, Any]] = []
        created_knowledge = []
        linked_skills: list[str] = []

        try:
            async def write_agent(tx_store: "TransactionStore") -> None:
                nonlocal linked_skills
                # A re-run follows a rollback, so it starts from nothing written.
                created_toolsets.clear()
                created_knowledge.clear()
                transaction_id = tx_store.txn
                logger.debug(f"Started transaction for agent creation: {agent_key}")

                # Step 1: Create agent node
                ensure_written(await graph_provider.batch_upsert_nodes([agent], CollectionNames.AGENT_INSTANCES.value, transaction=transaction_id), "the agent")
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

                ensure_written(await graph_provider.batch_create_edges(permission_edges, CollectionNames.PERMISSION.value, transaction=transaction_id), "the agent's permissions")
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
                        ensure_written(await graph_provider.batch_upsert_nodes(tool_nodes, CollectionNames.AGENT_TOOLS.value, transaction=transaction_id), "the agent's tools")

                    # Batch create toolset -> tool edges
                    if toolset_tool_edges:
                        ensure_written(await graph_provider.batch_create_edges(toolset_tool_edges, CollectionNames.TOOLSET_HAS_TOOL.value, transaction=transaction_id), "the agent's tool links")

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

            # explicit: all-or-nothing on Neo4j too, so a failure leaves no half-made
            # agent and a deadlock on the user or org node re-runs from a clean slate.
            # The helper rolls back on any BaseException, so a cancelled request
            # does not leave the transaction holding its locks until it times out.
            await GraphDataStore(logger, graph_provider).execute_in_transaction(write_agent, explicit=True)
            logger.info(f"✅ Successfully created agent {agent_key}")

        except Exception as e:
            if isinstance(e, UniqueConstraintViolation):
                raise
            logger.error(f"Failed to create agent {agent_key}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=action_failed("create this agent")
            ) from e

        # Outside the agent's transaction: on Neo4j one failed statement fails the
        # whole transaction, which would turn one bad MCP server into no agent.
        if mcp_servers_with_tools:
            created_mcp_servers, failed_mcp_servers = await _attach_mcp_servers_to_new_agent(
                agent_key, mcp_servers_with_tools, user_context, user_key, graph_provider, logger,
            )
            logger.debug(f"Created {len(created_mcp_servers)} MCP server(s) for agent: {agent_key}")

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
            agent=response_agent,
            warnings=failed_toolsets + failed_mcp_servers,
        )


    async def _before_update_write(
        self, agent: dict[str, Any], agent_id: str, actor: AgentActor, body: dict[str, Any],
    ) -> None:
        """Runs once an update is validated, before anything is written; may consume keys of `body`."""

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
        attached_mcp_instances = (
            await _assert_mcp_servers_exist(mcp_servers_with_tools, self._config, org_key, actor.user_id)
            if mcp_servers_with_tools else {}
        )
        mcp_servers_with_tools = _bind_mcp_attachments_to_instances(mcp_servers_with_tools, attached_mcp_instances)
        toolsets_in_body = _parse_toolsets(body.get("toolsets", [])) if "toolsets" in body else {}
        attached_toolsets = (
            await _assert_toolsets_visible(toolsets_in_body, self._config, org_key, actor.user_id)
            if _names_an_instance(toolsets_in_body) else {}
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
                    existing_ids, self._config, org_key,
                ) if existing_ids else {}
            except Exception as e:
                logger.error(f"Could not check the MCP servers of agent {agent_id} before sharing it: {e}")
                raise _mcp_attachments_unreadable() from e
        _assert_no_personal_mcp_servers(
            attached_mcp_instances, shared_with_org=final_shared, service_account=final_service_account,
        )
        if sharing_turned_on and "toolsets" not in body:
            existing_toolset_ids = {ts["instanceId"] for ts in agent.get("toolsets") or [] if ts.get("instanceId")}
            try:
                attached_toolsets = await _attached_personal_toolsets(
                    existing_toolset_ids, self._config, org_key,
                ) if existing_toolset_ids else {}
            except Exception as e:
                logger.error(f"Could not check the actions of agent {agent_id} before sharing it: {e}")
                raise _toolset_attachments_unreadable() from e
        _assert_no_personal_toolsets(attached_toolsets, shared_with_org=final_shared, service_account=final_service_account)
        if "apiToolsetTools" in body or (final_service_account and not agent.get("isServiceAccount")):
            # Becoming a service account re-checks what is already attached.
            body["apiToolsetTools"] = await _checked_api_toolset_tools(
                body.get("apiToolsetTools", agent.get("apiToolsetTools") or []), self._config,
                org_key, service_account=final_service_account,
            )

        # After every validation, before the first other write.
        await self._before_update_write(agent, agent_id, actor, body)

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
                # Turning OFF org sharing: delete the org permission edge. Not
                # delete_edge: ArangoDB's answers False when the delete fails, and the
                # agent stayed shared with the whole org while reporting that it was not.
                await graph_provider.batch_delete_edges(
                    [{
                        "from_id": org_key,
                        "from_collection": CollectionNames.ORGS.value,
                        "to_id": agent_id,
                        "to_collection": CollectionNames.AGENT_INSTANCES.value,
                    }],
                    CollectionNames.PERMISSION.value,
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
            # Parsed and checked before the update above
            toolsets_with_tools = toolsets_in_body

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
                logger.debug(f"Started transaction for toolset update on agent {agent_id}")

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

                logger.debug(f"Found {len(toolset_keys)} toolset(s) connected to agent {agent_id}")

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

                logger.debug(f"Found {len(all_tool_keys)} tool(s) connected to toolsets")

                # ========== PHASE 2: WRITE THE NEW TOOLSETS ==========

                # New toolsets are written, with their tools, before the old ones are unlinked:
                # on a backend whose rollback does not undo writes (Neo4j without explicit
                # transactions), a failure then leaves the agent with its old toolsets rather
                # than none, and the except block below removes what the failed attempt wrote.
                if toolsets_with_tools:
                    created_toolsets, failed_toolsets = await _create_toolset_edges(
                        agent_id, toolsets_with_tools, user_context, user_key, graph_provider, logger,
                        transaction=transaction_id,
                        written_toolset_keys=new_toolset_keys,
                        written_tool_keys=new_tool_keys,
                    )
                    if failed_toolsets:
                        raise RuntimeError(f"Toolsets could not be attached: {failed_toolsets}")
                    logger.info(f"Created {len(created_toolsets)} toolset(s) for agent {agent_id}")
                else:
                    logger.info(f"All toolsets removed for agent {agent_id}")

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

                logger.info(
                    f"Removed {len(toolset_keys)} old toolset(s) and {len(all_tool_keys)} old tool(s) "
                    f"from agent {agent_id}"
                )

                await graph_provider.commit_transaction(transaction_id)
                transaction_id = None
                logger.debug(f"Committed transaction for toolset update on agent {agent_id}")

            except Exception as e:
                if transaction_id:
                    try:
                        await graph_provider.rollback_transaction(transaction_id)
                        logger.warning(f"Aborted transaction for toolset update on agent {agent_id}")
                    except Exception as abort_error:
                        logger.error(f"Failed to abort transaction: {abort_error}")
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
                logger.error(f"Failed to replace toolsets for agent {agent_id}: {e}", exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail=action_failed("save this agent")
                ) from e

        # Update attached MCP servers if provided in request (even if empty array - means detach all).
        # The new servers are written, with their tools, before the old ones are unlinked: on a
        # backend whose rollback does not undo writes (Neo4j without explicit transactions), a
        # failure then leaves the agent with its old servers rather than none, and the except
        # block removes what the failed attempt wrote.
        if "mcpServers" in body:
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
                    created_mcp_servers, failed_mcp_servers = await _create_mcp_server_edges(
                        agent_id, mcp_servers_with_tools, user_context, user_key,
                        graph_provider, logger, transaction=transaction_id,
                        written_server_keys=new_server_keys, written_tool_keys=new_tool_keys,
                    )
                    if failed_mcp_servers:
                        raise RuntimeError(f"MCP servers could not be attached: {failed_mcp_servers}")
                    logger.info(f"Attached {len(created_mcp_servers)} MCP server(s) to agent {agent_id}")
                else:
                    logger.info(f"All MCP servers detached for agent {agent_id}")

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
                        logger.warning(f"Aborted transaction for MCP server update on agent {agent_id}")
                    except Exception as abort_error:
                        logger.error(f"Failed to abort transaction: {abort_error}")
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
                logger.error(f"Failed to update the MCP servers of agent {agent_id}: {e}", exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail=action_failed("save this agent")
                ) from e

        # Update knowledge if provided in request (even if empty array - means delete all)
        if "knowledge" in body:
            # Parse knowledge sources first to validate before deletion
            knowledge_sources = _parse_knowledge_sources(body.get("knowledge", []))
            await _refuse_limit_over_the_cap(knowledge_sources, graph_provider, org_key, logger)

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
