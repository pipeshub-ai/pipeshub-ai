"""`OpenAPIToolProvider`: registers an agent's attached API toolset operations for one request.

One group per toolset, top-level like a connector toolset, so lazy disclosure files it with
the other app integrations. A toolset that can't load (removed, or not connected for this
user) is skipped and named in `context.toolset_load_failures`, which the capability summary
turns into "tell the user to connect it". The request is never blocked.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.tools.errors import (
    DuplicateToolNameError,
    DuplicateToolPathError,
)
from app.agents.agent_loop.openapi_tool_adapter import OpenAPIToolAdapter, connect_hint
from app.agents.agent_loop.tool_approvals import TOOLSET_TOOLS_BY_PATH, ApprovalTarget
from app.agents.mcp.naming import build_namespaced_tool_name, namespace_key, unique_keys
from app.agents.openapi import attachments, service
from app.agents.openapi.auth import ApiToolsetAuthError
from app.agents.openapi.models import AuthType
from app.agents.openapi.store import ApiToolsetStore
from app.services.featureflag.platform_settings import is_api_toolsets_enabled

if TYPE_CHECKING:
    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agents.agent_loop.context import AgentContext
    from app.agents.openapi.models import ApiToolsetInstance

logger = logging.getLogger(__name__)

__all__ = ["OpenAPIToolProvider"]

_TOOL_PREFIX = "api_"


class OpenAPIToolProvider:
    async def load_into(self, registry: ToolRegistry, context: AgentContext) -> None:
        selected = attachments.by_instance(context.api_toolset_tools)
        if not selected or not await is_api_toolsets_enabled(context.config_service):
            return
        state_logger = context.logger or logger
        store = ApiToolsetStore(context.config_service)
        instances: list[ApiToolsetInstance] = []
        for instance_id in selected:
            try:
                instance = await store.get_instance(context.org_id, instance_id)
            except Exception:
                # The other attachments and the rest of the agent still load.
                state_logger.exception("OpenAPIToolProvider: couldn't read API action %s", instance_id)
                context.toolset_load_failures[f"API action {instance_id}"] = "error"
                continue
            if instance is None:
                state_logger.warning("OpenAPIToolProvider: attached API action %s no longer exists", instance_id)
                continue
            instances.append(instance)
        keys = unique_keys(
            instances, key_of=lambda i: namespace_key(None, i.name), id_of=lambda i: i.id, created_of=lambda i: i.created_at,
        )
        for instance in instances:
            try:
                await self._load_one(registry, context, store, instance, selected[instance.id], keys[instance.id])
            except Exception:
                state_logger.exception("OpenAPIToolProvider: couldn't load API action %s", instance.name)
                context.toolset_load_failures[f"{instance.name} (API action)"] = "error"

    async def _load_one(
        self,
        registry: ToolRegistry,
        context: AgentContext,
        store: ApiToolsetStore,
        instance: ApiToolsetInstance,
        tool_names: list[str],
        key: str,
    ) -> None:
        failure_key = f"{instance.name} (API action; {connect_hint(instance)})"
        needs_own_credential = not instance.use_shared_credential and instance.auth_scheme.type is not AuthType.NONE
        if context.is_service_account and needs_own_credential:
            # Caught when the agent is saved; an older attachment can still get here.
            context.toolset_load_failures[failure_key] = "not_authenticated"
            return
        try:
            executor = await service.build_executor(store, instance, service.credential_owner(instance, context.user_id))
        except ApiToolsetAuthError:
            context.toolset_load_failures[failure_key] = "not_authenticated"
            return

        wanted = set(tool_names)
        approvals = context.tool_state.setdefault(TOOLSET_TOOLS_BY_PATH, {})
        registered: list[str] = []
        for op in instance.operations:
            if op.tool_name not in wanted:
                continue
            adapter = OpenAPIToolAdapter(
                instance, op, executor, name=build_namespaced_tool_name(key, op.tool_name, prefix=_TOOL_PREFIX),
            )
            try:
                registry.register_tool(adapter)
            except (DuplicateToolNameError, DuplicateToolPathError):
                (context.logger or logger).warning("OpenAPIToolProvider: skipping duplicate tool %s", adapter.name)
                continue
            # Reads run, writes ask first, deletes are refused unless a rule says otherwise.
            approvals[adapter.path] = ApprovalTarget(
                source="toolset", instance_id=instance.id, tool_name=op.tool_name, owner=instance.name,
                kind=op.kind, tool=adapter, defaults="mcp",
            )
            registered.append(adapter.name)
        if registered:
            # `api_`-prefixed so a toolset named "slack" can't replace the native Slack group.
            registry.register_toolset(
                f"{_TOOL_PREFIX}{key}", f"{instance.name}: the organization's {instance.spec_title} API.", registered,
            )
