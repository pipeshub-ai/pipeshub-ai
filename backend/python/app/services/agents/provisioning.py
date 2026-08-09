"""AgentProvisioningService — single home for the nine-collection Arango
transaction that creates an Agent Builder agent.

Consolidates the duplicated graph-write sequence shared by:
  1. `api/routes/agent.py::create_agent()` (interactive Agent Builder UI)
  2. `tasks/application/promote_to_agent.py::create_agent_from_task()`
  3. `workflows/runtime/broker.py::AgentCreateHandler` (ctx.create_agent SDK)

All three callers converge on a common `AgentSpec` DTO and this service
handles the graph writes, leaving each caller responsible only for
assembling the spec from its own vocabulary.

Hard constraint: agents require at least one configured LLM model.  The
service raises `PrerequisiteError` rather than creating a dead node (per
the original `promote_to_agent.py` comment).
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import config_node_constants
from app.services.tasks.domain.errors import PrerequisiteError
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["AgentSpec", "AgentProvisioningService"]

logger = logging.getLogger(__name__)


class AgentSpec:
    """Input DTO for creating a new agent."""

    def __init__(
        self,
        *,
        name: str,
        description: str = "",
        instructions: str = "",
        system_prompt: str = "",
        org_id: str,
        created_by_user_id: str,
        is_service_account: bool = False,
        # Tool names (bare names; the provisioner wraps them in a single toolset)
        tool_names: list[str] | None = None,
        # Connector/collection IDs for knowledge
        connector_ids: list[str] | None = None,
        collection_ids: list[str] | None = None,
        # Skill names (looked up in agentSkills by org+name)
        skill_names: list[str] | None = None,
        # Explicit model entry strings (e.g. "openai_gpt-4o"); if empty the
        # service loads all configured LLM models from etcd.
        models: list[str] | None = None,
        # Whether to persist the agent (default True for create_agent).
        # Pass False for ephemeral sub-agent runs — not yet implemented (D8).
        persist: bool = True,
        tags: list[str] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.instructions = instructions
        self.system_prompt = system_prompt
        self.org_id = org_id
        self.created_by_user_id = created_by_user_id
        self.is_service_account = is_service_account
        self.tool_names = tool_names or []
        self.connector_ids = connector_ids or []
        self.collection_ids = collection_ids or []
        self.skill_names = skill_names or []
        self.models = models or []
        self.persist = persist
        self.tags = tags or []


class AgentProvisioningService:
    """Creates Agent Builder agents in the graph database.

    Thread-safe: no mutable state; every call is a self-contained graph
    transaction.
    """

    def __init__(
        self,
        graph_provider: "IGraphDBProvider",
        config_service: "ConfigurationService",
    ) -> None:
        self._graph = graph_provider
        self._config = config_service

    async def create(self, spec: AgentSpec) -> str:
        """Create the agent described by `spec`.  Returns the new `agentInstances` _key.

        Raises:
            PrerequisiteError: if no AI model is configured.
        """
        models = spec.models or await self._resolve_default_models()
        if not models:
            raise PrerequisiteError(
                f"Cannot create agent {spec.name!r}: org {spec.org_id!r} has no AI model configured."
            )

        agent_key = str(uuid.uuid4())
        time = get_epoch_timestamp_in_ms()

        agent_doc = {
            "_key": agent_key,
            "name": spec.name.strip() or f"Workflow Agent {agent_key[:8]}",
            "description": spec.description.strip() or spec.name.strip() or f"Workflow Agent {agent_key[:8]}",
            "startMessage": "Hello! How can I help you today?",
            "systemPrompt": spec.system_prompt or (
                "You are a workplace productivity assistant."
            ),
            "instructions": spec.instructions.strip() or None,
            "models": models,
            "tags": spec.tags or [],
            "webSearch": None,
            "defaultReasoningEffort": None,
            "isActive": True,
            "isServiceAccount": spec.is_service_account,
            "createdBy": spec.created_by_user_id,
            "updatedBy": None,
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
            "isDeleted": False,
        }

        write_collections = [
            CollectionNames.AGENT_INSTANCES.value,
            CollectionNames.PERMISSION.value,
            CollectionNames.AGENT_TOOLSETS.value,
            CollectionNames.AGENT_TOOLS.value,
            CollectionNames.AGENT_HAS_TOOLSET.value,
            CollectionNames.TOOLSET_HAS_TOOL.value,
            CollectionNames.AGENT_KNOWLEDGE.value,
            CollectionNames.AGENT_HAS_KNOWLEDGE.value,
            CollectionNames.AGENT_HAS_SKILL.value,
        ]
        txn = await self._graph.begin_transaction(
            read=[CollectionNames.AGENT_SKILLS.value],
            write=write_collections,
        )
        try:
            await self._graph.batch_upsert_nodes(
                [agent_doc], CollectionNames.AGENT_INSTANCES.value, transaction=txn,
            )
            await self._graph.batch_create_edges(
                [{
                    "_from": f"{CollectionNames.USERS.value}/{spec.created_by_user_id}",
                    "_to": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
                    "role": "OWNER",
                    "type": "USER",
                    "createdAtTimestamp": time,
                    "updatedAtTimestamp": time,
                }],
                CollectionNames.PERMISSION.value, transaction=txn,
            )
            if spec.tool_names:
                await self._create_toolset(agent_key, spec, time, txn)
            if spec.connector_ids or spec.collection_ids:
                await self._create_knowledge_links(agent_key, spec, time, txn)
            if spec.skill_names:
                await self._create_skill_links(agent_key, spec, time, txn)
            await self._graph.commit_transaction(txn)
        except Exception:
            await self._graph.rollback_transaction(txn)
            raise

        logger.info(
            "AgentProvisioningService: created agent %s for org %s user %s",
            agent_key, spec.org_id, spec.created_by_user_id,
        )
        return agent_key

    # -------------------------------------------------------------------------
    # Internal graph helpers
    # -------------------------------------------------------------------------

    async def _create_toolset(
        self, agent_key: str, spec: AgentSpec, time: int, txn: str,
    ) -> None:
        toolset_key = str(uuid.uuid4())
        await self._graph.batch_upsert_nodes(
            [{
                "_key": toolset_key,
                "name": "workflow-agent-tools",
                "displayName": "Workflow Agent Tools",
                "type": "app",
                "userId": spec.created_by_user_id,
                "createdBy": spec.created_by_user_id,
                "createdAtTimestamp": time,
                "updatedAtTimestamp": time,
            }],
            CollectionNames.AGENT_TOOLSETS.value, transaction=txn,
        )
        await self._graph.batch_create_edges(
            [{
                "_from": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
                "_to": f"{CollectionNames.AGENT_TOOLSETS.value}/{toolset_key}",
                "createdAtTimestamp": time,
                "updatedAtTimestamp": time,
            }],
            CollectionNames.AGENT_HAS_TOOLSET.value, transaction=txn,
        )
        tool_nodes = []
        tool_edges = []
        for tool_name in spec.tool_names:
            tool_key = str(uuid.uuid4())
            tool_nodes.append({
                "_key": tool_key,
                "name": tool_name,
                "fullName": tool_name,
                "toolsetName": "workflow-agent-tools",
                "description": "",
                "createdBy": spec.created_by_user_id,
                "createdAtTimestamp": time,
                "updatedAtTimestamp": time,
            })
            tool_edges.append({
                "_from": f"{CollectionNames.AGENT_TOOLSETS.value}/{toolset_key}",
                "_to": f"{CollectionNames.AGENT_TOOLS.value}/{tool_key}",
                "createdAtTimestamp": time,
                "updatedAtTimestamp": time,
            })
        await self._graph.batch_upsert_nodes(
            tool_nodes, CollectionNames.AGENT_TOOLS.value, transaction=txn,
        )
        await self._graph.batch_create_edges(
            tool_edges, CollectionNames.TOOLSET_HAS_TOOL.value, transaction=txn,
        )

    async def _create_knowledge_links(
        self, agent_key: str, spec: AgentSpec, time: int, txn: str,
    ) -> None:
        ids = [*spec.collection_ids, *spec.connector_ids]
        nodes = []
        edges = []
        for cid in ids:
            key = str(uuid.uuid4())
            nodes.append({
                "_key": key,
                "connectorId": cid,
                "filters": json.dumps({}),
                "createdBy": spec.created_by_user_id,
                "createdAtTimestamp": time,
                "updatedAtTimestamp": time,
            })
            edges.append({
                "_from": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
                "_to": f"{CollectionNames.AGENT_KNOWLEDGE.value}/{key}",
                "createdAtTimestamp": time,
                "updatedAtTimestamp": time,
            })
        await self._graph.batch_upsert_nodes(
            nodes, CollectionNames.AGENT_KNOWLEDGE.value, transaction=txn,
        )
        await self._graph.batch_create_edges(
            edges, CollectionNames.AGENT_HAS_KNOWLEDGE.value, transaction=txn,
        )

    async def _create_skill_links(
        self, agent_key: str, spec: AgentSpec, time: int, txn: str,
    ) -> None:
        skills_collection = CollectionNames.AGENT_SKILLS.value
        edges: list[dict[str, Any]] = []
        for name in spec.skill_names:
            skill_key = f"{spec.org_id}_{name}"
            doc = await self._graph.get_document(skill_key, skills_collection, transaction=txn)
            if not doc or doc.get("orgId") != spec.org_id:
                continue
            if doc.get("source") != "builtin" and doc.get("createdBy") != spec.created_by_user_id:
                continue
            edges.append({
                "_from": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_key}",
                "_to": f"{skills_collection}/{skill_key}",
                "skillName": name,
                "createdAtTimestamp": time,
                "updatedAtTimestamp": time,
            })
        if edges:
            await self._graph.batch_create_edges(
                edges, CollectionNames.AGENT_HAS_SKILL.value, transaction=txn,
            )

    async def _resolve_default_models(self) -> list[str]:
        ai_models = await self._config.get_config(
            config_node_constants.AI_MODELS.value, use_cache=False,
        )
        llm_configs = (ai_models or {}).get("llm", [])
        entries: list[str] = []
        for config in llm_configs:
            model_key = config.get("modelKey")
            if not model_key:
                continue
            model_name = config.get("modelName", "")
            entries.append(f"{model_key}_{model_name}" if model_name else model_key)
        return entries
