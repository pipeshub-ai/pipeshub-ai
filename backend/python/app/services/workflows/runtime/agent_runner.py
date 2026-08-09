"""Concrete ``IWorkflowAgentRunner`` — runs an Agent Builder agent by id
within a sandboxed workflow execution.

Called by ``AgentRunHandler`` (in the broker) whenever workflow code does::

    agent = await ctx.agent("some-agent-id")
    result = await agent.run(goal="...")

or::

    agent = await ctx.create_agent("triager", tools=[...], instructions="...")
    result = await agent.run(goal="...")

The runner looks up the agent in the graph database, resolves its tools and
credentials using the *parent workflow's* user identity (the org-scoped
isolation boundary), builds a headless ``Agent``, runs it, and returns the
output.

Deliberately reuses the same ``build_headless_context`` / ``PipesHubToolLoader``
/ ``AgentSpec`` / ``AgentRuntime`` machinery that ``TaskSpecAssembler`` uses for
scheduled task runs — one tool-resolution implementation, not a parallel path
that could drift.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agents.agent_loop.tool_loader import PipesHubToolLoader
from app.agents.constants.toolset_constants import get_toolset_config_path
from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import config_node_constants
from app.services.tasks.runtime.headless_context import (
    build_headless_context,
    build_transport_registry,
)
from app.utils.llm import get_llm

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.modules.transformers.blob_storage import BlobStorage
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["WorkflowAgentRunner"]

logger = logging.getLogger(__name__)

_SUB_AGENT_MAX_TURNS = 20
_SUB_AGENT_SYSTEM_PROMPT = (
    "You are an autonomous agent created by a workflow to accomplish a "
    "specific task. Complete the goal described below using the tools "
    "available to you. Return your result as your final answer."
)


class WorkflowAgentRunner:
    """Runs an agent (looked up by id) within the scope of a workflow execution.

    Thread-safe: no mutable state; every ``run()`` call is self-contained.
    Satisfies the ``IWorkflowAgentRunner`` protocol.
    """

    def __init__(
        self,
        graph_provider: "IGraphDBProvider",
        config_service: "ConfigurationService",
        blob_store: "BlobStorage | None" = None,
    ) -> None:
        self._graph = graph_provider
        self._config = config_service
        self._blob_store = blob_store
        self._tool_loader = PipesHubToolLoader()

    async def run(
        self,
        *,
        agent_id: str,
        org_id: str,
        user_id: str,
        goal: str,
        arguments: dict[str, Any],
    ) -> Any:
        agent_doc = await self._graph.get_agent(agent_id)
        if not agent_doc:
            raise ValueError(
                f"Agent '{agent_id}' not found or deleted. "
                "Verify the agent_id passed to ctx.agent()."
            )

        tool_names = _extract_tool_names(agent_doc)
        llm, model_name = await self._resolve_llm(agent_doc)

        agent_toolsets, toolset_configs = await self._resolve_credentials(
            tool_names, user_id=user_id,
        )

        context = build_headless_context(
            org_id=org_id,
            user_id=user_id,
            user_email="",
            graph_provider=self._graph,
            config_service=self._config,
            blob_store=self._blob_store,
            llm=llm,
            agent_toolsets=agent_toolsets,
            toolset_configs=toolset_configs,
            instructions=agent_doc.get("instructions"),
            system_prompt=agent_doc.get("systemPrompt") or _SUB_AGENT_SYSTEM_PROMPT,
        )

        tool_registry = await self._tool_loader.load(
            context, skip_apps={"coding_sandbox", "database_sandbox"},
        )

        # Grant ALL tools from the agent's apps, not just the explicitly
        # named ones.  An agent given ["jira__search_issues"] likely needs
        # jira__list_projects (to resolve a project name) or jira__get_issue
        # (to fetch details) before it can accomplish its goal.  The tool
        # loader already loaded every tool for each authenticated app; we
        # just need to not filter them back out.
        available = tool_registry.names()
        granted_apps = {n.split("__", 1)[0] for n in tool_names if "__" in n}
        if granted_apps:
            resolved = [n for n in available if n.split("__", 1)[0] in granted_apps
                        or "__" not in n]
        else:
            resolved = list(available)

        spec = AgentSpec(
            name=f"wf-agent:{agent_id[:12]}",
            description=agent_doc.get("name", "Workflow sub-agent"),
            system_prompt=agent_doc.get("systemPrompt") or _SUB_AGENT_SYSTEM_PROMPT,
            tool_names=list(resolved),
            model=ModelSpec(provider="langchain", model=model_name),
            loop=ReActLoop(),
            max_turns=_SUB_AGENT_MAX_TURNS,
        )
        runtime = build_transport_registry(llm, model_name=model_name)
        from app.agent_loop_lib.runtime.runtime import AgentRuntime

        agent_runtime = AgentRuntime(
            transport_registry=runtime,
            tool_registry=tool_registry,
            hooks=HookRegistry(),
        )
        agent = Agent(spec, agent_runtime, session_id=f"wf-agent-{agent_id}")
        result = await agent.run(Goal(description=goal))

        logger.info(
            "WorkflowAgentRunner: agent=%s goal=%r success=%s",
            agent_id, goal[:80], result.success,
        )

        if result.success:
            return result.output
        raise RuntimeError(
            f"Agent '{agent_id}' failed: {result.error or 'unknown error'}"
        )

    async def _resolve_llm(
        self, agent_doc: dict[str, Any],
    ) -> tuple[Any, str]:
        """Resolve the LLM from the agent's ``models`` field, falling back to
        the org's default model."""
        llm_configs = None
        models = agent_doc.get("models") or []
        if models:
            ai_models = await self._config.get_config(
                config_node_constants.AI_MODELS.value, use_cache=False,
            )
            all_llm = (ai_models or {}).get("llm", [])
            for model_entry in models:
                model_key = model_entry.split("_", 1)[0] if "_" in model_entry else model_entry
                candidates = [c for c in all_llm if c.get("modelKey") == model_key]
                if candidates:
                    llm_configs = candidates
                    break
        llm, config = await get_llm(self._config, llm_configs)
        raw = (config.get("configuration") or {}).get("model") or config.get("modelName") or ""
        return llm, str(raw)

    async def _resolve_credentials(
        self, tool_names: list[str], *, user_id: str,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """Map tool names to toolset instances and load user credentials."""
        needed_apps = {name.split("__", 1)[0] for name in tool_names if "__" in name}
        if not needed_apps:
            return [], {}

        all_instances: list[dict[str, Any]] = []
        try:
            all_instances = (
                await self._config.get_config("/services/toolset-instances", default=[])
            ) or []
        except Exception:
            logger.warning("WorkflowAgentRunner: could not load toolset instances", exc_info=True)
            return [], {}

        agent_toolsets: list[dict[str, Any]] = []
        toolset_configs: dict[str, dict[str, Any]] = {}

        for instance in all_instances:
            instance_id = instance.get("_id")
            toolset_type = instance.get("toolsetType", "")
            if not instance_id or toolset_type not in needed_apps:
                continue
            try:
                config = await self._config.get_config(
                    get_toolset_config_path(instance_id, user_id),
                )
            except Exception:
                continue
            if not config or not config.get("isAuthenticated", False):
                continue

            toolset_configs[instance_id] = config
            agent_toolsets.append({
                "instanceId": instance_id,
                "instanceName": instance.get("instanceName") or toolset_type,
                "name": toolset_type,
                "displayName": instance.get("instanceName") or toolset_type,
                "type": toolset_type,
                "tools": instance.get("tools", []),
                "selectedTools": instance.get("selectedTools", []),
            })

        logger.info(
            "WorkflowAgentRunner: requested_apps=%s, authenticated=%d",
            needed_apps, len(agent_toolsets),
        )
        return agent_toolsets, toolset_configs


def _extract_tool_names(agent_doc: dict[str, Any]) -> list[str]:
    """Pull tool names from the agent document returned by ``get_agent()``."""
    names: list[str] = []
    for toolset in agent_doc.get("toolsets") or []:
        for tool in toolset.get("tools") or []:
            name = tool.get("name") or tool.get("fullName") or ""
            if name:
                names.append(name)
    return names
