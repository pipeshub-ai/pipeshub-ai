"""`PipesHubToolLoader` — registers PipesHub tools into an ``agent_loop_lib``
`ToolRegistry` for each request.

Connector toolsets (Jira, Confluence, Slack, ...) are loaded by creating
authenticated instances via `ToolInstanceCreator` and scanning their
``@tool``-decorated methods with `ToolsetBuilder`.

Dynamic tools (web_search, fetch_url, SQL, Slack context, fetch_full_record)
are built per-request and wrapped with `PipesHubStructuredToolAdapter`.

The loader's only responsibility is loading and registration. Filtering,
gating, and policy enforcement are separate concerns. It does, however,
record WHY a toolset failed to load into `context.toolset_load_failures`
(`"not_authenticated"` vs `"error"`) and WHICH loaded toolsets are
`essential=True` metadata into `context.essential_toolset_names` — both are
facts only this loop can observe, and downstream consumers (the global
search fallback, the capability summary, `factory.py`'s pinned-toolsets
derivation) need them without re-deriving.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR
from app.agent_loop_lib.tools.errors import (
    DuplicateToolNameError,
    DuplicateToolPathError,
)
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.tools.toolset import ToolsetBuilder as AgentLoopToolsetBuilder
from app.agents.agent_loop.instance_creator import ToolInstanceCreator
from app.agents.agent_loop.tool_adapter import PipesHubStructuredToolAdapter, split_original_tool_name
from app.agents.tools.factories.base import ToolsetAuthError
from app.agents.tools.factories.registry import ClientFactoryRegistry

if TYPE_CHECKING:
    from langchain_core.tools import StructuredTool

    from app.agent_loop_lib.tools.base import Tool
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

# Normalized (`ToolsetRegistry._normalize_toolset_name`) names of the two
# internal toolsets ("Retrieval", "KnowledgeHub") whose tools are useless —
# and actively misleading, since every call fails with "No knowledge sources
# configured" — when the agent has no knowledge attached. `.as_internal()`
# exempts them from the external-toolset "configured on this agent" check
# below, so they need their own gate on `context.has_knowledge` instead.
_KNOWLEDGE_TOOLSETS = frozenset({"retrieval", "knowledgehub"})

# Same substring heuristic `ToolInstanceCreator._create_with_factory` uses to
# decide whether to re-raise a `ValueError` as an auth-flavored message
# (`instance_creator.py`) — duplicated here (not imported) because that
# heuristic lives on the RAISING side; this is the CATCHING side deciding
# how to classify what it just caught, a distinct concern.
_AUTH_ERROR_MARKERS = ("not authenticated", "oauth", "authentication")


def _is_auth_error(exc: Exception) -> bool:
    """Whether *exc* represents a configured-but-unauthenticated toolset —
    either the library's own `ToolsetAuthError` or the auth-flavored
    `ValueError` `ToolInstanceCreator` raises for the same condition (see
    `instance_creator.py::_create_with_factory`)."""
    if isinstance(exc, ToolsetAuthError):
        return True
    if isinstance(exc, ValueError):
        message = str(exc).lower()
        return any(marker in message for marker in _AUTH_ERROR_MARKERS)
    return False


def _has_tool_decorated_methods(cls: type) -> bool:
    """Return ``True`` if *cls* has any methods annotated with ``@tool``."""
    for attr_name in dir(cls):
        if attr_name.startswith("_"):
            continue
        attr = getattr(cls, attr_name, None)
        if attr is not None and callable(attr):
            func = getattr(attr, "__func__", attr)
            if hasattr(func, TOOL_META_ATTR):
                return True
    return False


def _infer_path_prefix(cls: type, *, fallback_name: str) -> str:
    """Derive the toolset ``path_prefix`` from the ``@tool`` paths on *cls*.

    The ``@tool`` decorator's ``path`` argument is the single source of truth
    (e.g. ``/tools/date_calculator/get_exclusion_dates``).  The prefix is
    everything before the last ``/`` segment — e.g. ``/tools/date_calculator``.

    If the class has no ``@tool``-decorated methods (shouldn't happen if
    ``_has_tool_decorated_methods`` already passed), falls back to
    ``/tools/{fallback_name}``.
    """
    from app.agent_loop_lib.tools.decorators import ToolMeta

    for attr_name in dir(cls):
        if attr_name.startswith("_"):
            continue
        attr = getattr(cls, attr_name, None)
        if attr is None or not callable(attr):
            continue
        func = getattr(attr, "__func__", attr)
        meta: ToolMeta | None = getattr(func, TOOL_META_ATTR, None)
        if meta is not None:
            return meta.path.rsplit("/", 1)[0]
    return f"/tools/{fallback_name}"


def _build_dynamic_tools(context: "AgentContext") -> list["Tool"]:
    """Build per-request dynamic tools and wrap them as ``Tool`` instances.

    Dynamic tools are LangChain ``StructuredTool`` objects created by
    factory functions, wrapped with ``PipesHubStructuredToolAdapter``.
    """
    state = context.tool_state
    state_logger = state.get("logger")
    tools: list[Tool] = []

    config_service = state.get("config_service")

    if config_service and state.get("has_sql_connector") and state.get("has_sql_knowledge"):
        try:
            from app.utils.execute_query import create_execute_query_tool
            execute_query_tool = create_execute_query_tool(
                config_service=config_service,
                graph_provider=state.get("graph_provider"),
                org_id=state.get("org_id"),
                conversation_id=state.get("conversation_id"),
                blob_store=state.get("blob_store"),
            )
            setattr(execute_query_tool, "_original_name", "sql.execute_sql_query")
            app_name, tool_name = split_original_tool_name(execute_query_tool)
            tools.append(PipesHubStructuredToolAdapter(execute_query_tool, app_name, tool_name))
            if state_logger:
                state_logger.debug("Added execute_sql_query tool")
        except Exception as e:
            if state_logger:
                state_logger.warning("Failed to add execute_sql_query tool: %s", e)

    if config_service and state.get("has_slack_connector") and state.get("has_slack_knowledge"):
        try:
            from app.utils.fetch_slack_nearby_messages import create_fetch_slack_nearby_messages_tool
            from app.utils.fetch_slack_thread import create_fetch_slack_thread_tool

            slack_thread_tool = create_fetch_slack_thread_tool(
                virtual_record_id_to_result=state.get("virtual_record_id_to_result", {}),
                org_id=state.get("org_id", ""),
                graph_provider=state.get("graph_provider"),
                blob_store=state.get("blob_store"),
                config_service=config_service,
            )
            setattr(slack_thread_tool, "_original_name", "slack.fetch_slack_thread")
            a, t = split_original_tool_name(slack_thread_tool)
            tools.append(PipesHubStructuredToolAdapter(slack_thread_tool, a, t))

            slack_nearby_tool = create_fetch_slack_nearby_messages_tool(config_service=config_service)
            setattr(slack_nearby_tool, "_original_name", "slack.fetch_slack_nearby_messages")
            a, t = split_original_tool_name(slack_nearby_tool)
            tools.append(PipesHubStructuredToolAdapter(slack_nearby_tool, a, t))

            if state_logger:
                state_logger.debug("Added Slack context tools (fetch_slack_thread, fetch_slack_nearby_messages)")
        except Exception as e:
            if state_logger:
                state_logger.warning("Failed to add Slack context tools: %s", e)

    web_search_config = state.get("web_search_config")
    if web_search_config:
        ref_mapper = state.get("citation_ref_mapper")
        if ref_mapper is None:
            from app.utils.chat_helpers import CitationRefMapper
            ref_mapper = CitationRefMapper()
            state["citation_ref_mapper"] = ref_mapper

        try:
            from app.utils.web_search_tool import create_web_search_tool
            ws_tool = create_web_search_tool(config=web_search_config)
            a, t = split_original_tool_name(ws_tool)
            tools.append(PipesHubStructuredToolAdapter(ws_tool, a, t))
        except Exception as e:
            if state_logger:
                state_logger.warning("Failed to create web_search tool: %s", e)

        try:
            from app.utils.fetch_url_tool import create_fetch_url_tool
            fu_tool = create_fetch_url_tool(ref_mapper=ref_mapper)
            a, t = split_original_tool_name(fu_tool)
            tools.append(PipesHubStructuredToolAdapter(fu_tool, a, t))
        except Exception as e:
            if state_logger:
                state_logger.warning("Failed to create fetch_url tool: %s", e)

    return tools


class PipesHubToolLoader:
    """Registers PipesHub tools into an agent-loop ``ToolRegistry`` per request.

    The loader's responsibility is strictly loading and registration.
    It does not filter, gate, or enforce policy on which tools are available.
    """

    async def load(self, context: "AgentContext", *, skip_apps: set[str] | None = None) -> ToolRegistry:
        """Load all tools into a fresh ``ToolRegistry``.

        ``skip_apps`` excludes every tool whose app name is in the set
        (used by the caller for mutually exclusive tool groups, e.g.
        coding_sandbox vs agent_loop_lib's built-in code execution).
        """
        registry = ToolRegistry()
        skip_apps = skip_apps or set()

        state = context.tool_state
        state_logger = state.get("logger")

        state.setdefault("tool_results", [])
        state.setdefault("all_tool_results", [])

        # ── Connector toolsets ────────────────────────────────────────────
        from app.agents.registry.toolset_registry import get_toolset_registry
        toolset_reg = get_toolset_registry()

        instance_creator = ToolInstanceCreator(context)
        loaded_apps: set[str] = set()
        tool_count = 0

        configured_apps = self._build_configured_apps_set(context.agent_toolsets)

        for ts_name, ts_meta in toolset_reg.get_all_toolsets().items():
            ts_class = ts_meta.get("class")
            if ts_class is None:
                continue
            if not _has_tool_decorated_methods(ts_class):
                continue

            # The group name exposed to the LLM via list_toolsets/fetch_tools
            # must match the prefix segment the LLM sees in tool names
            # (e.g. "image_generator" from "image_generator__generate_image"),
            # not the ToolsetRegistry's aggressively normalized key (which
            # strips underscores: "imagegenerator"). Computed early so the
            # skip_apps check below matches either form.
            ts_path_prefix = _infer_path_prefix(ts_class, fallback_name=ts_name)
            group_name = ts_path_prefix.rsplit("/", 1)[-1] if "/" in ts_path_prefix else ts_name

            if ts_name in skip_apps or group_name in skip_apps:
                continue

            is_internal = ts_meta.get("isInternal", False)
            has_factory = ClientFactoryRegistry.get_factory(ts_name) is not None

            if has_factory and not is_internal and not self._is_configured(ts_name, configured_apps):
                if state_logger:
                    state_logger.debug("Skipping unconfigured external toolset: %s", ts_name)
                continue

            if ts_name in _KNOWLEDGE_TOOLSETS and not context.has_knowledge:
                if state_logger:
                    state_logger.debug("Skipping knowledge toolset with no knowledge configured: %s", ts_name)
                continue

            try:
                instance = await instance_creator.create_instance_async(ts_class, ts_name)

                ts_description = ts_meta.get("description", "")

                toolset = AgentLoopToolsetBuilder(
                    instance,
                    name=group_name,
                    description=ts_description,
                    path_prefix=ts_path_prefix,
                )

                for t in toolset.tools:
                    try:
                        registry.register_tool(t)
                        tool_count += 1
                    except (DuplicateToolPathError, DuplicateToolNameError):
                        if state_logger:
                            state_logger.warning("Skipping duplicate: %s", t.name)

                registered_names = [t.name for t in toolset.tools if registry.has(t.name)]
                if registered_names:
                    try:
                        registry.register_toolset(group_name, ts_description, registered_names)
                        if ts_meta.get("essential", False) and group_name not in context.essential_toolset_names:
                            context.essential_toolset_names.append(group_name)
                    except Exception:
                        pass

                loaded_apps.add(ts_name)
            except Exception as e:
                # Distinguish "configured but not authenticated" (the
                # toolset exists — the user just needs to complete OAuth/API
                # key setup) from a genuine load failure, so callers
                # downstream (search_tools' global fallback, the capability
                # summary) can proactively tell the user to authenticate
                # instead of the toolset silently vanishing with no signal
                # (see AgentContext.toolset_load_failures's docstring).
                reason = "not_authenticated" if _is_auth_error(e) else "error"
                context.toolset_load_failures[ts_name] = reason
                if state_logger:
                    log_fn = state_logger.warning if reason == "not_authenticated" else state_logger.error
                    log_fn(
                        "Failed to load toolset %s (reason=%s): %s", ts_name, reason, e,
                        exc_info=reason != "not_authenticated",
                    )
                continue

        # ── Dynamic tools ─────────────────────────────────────────────────
        dynamic_tools = _build_dynamic_tools(context)
        dynamic_count = 0
        for dt in dynamic_tools:
            try:
                registry.register_tool(dt)
                dynamic_count += 1
            except (DuplicateToolPathError, DuplicateToolNameError):
                if state_logger:
                    state_logger.warning("Skipping duplicate dynamic tool: %s", dt.name)

        logger.info(
            "PipesHubToolLoader: %d connector tool(s) from %d toolset(s) + "
            "%d dynamic tool(s) = %d total (skip_apps=%s)",
            tool_count, len(loaded_apps), dynamic_count,
            len(registry.names()), sorted(skip_apps),
        )
        return registry

    @staticmethod
    def _build_configured_apps_set(agent_toolsets: list[dict]) -> set[str]:
        """Build a set of normalized app names from the agent's configured toolsets.

        Used to skip external toolsets the user hasn't configured on this agent,
        avoiding noisy factory errors for unconfigured connectors.
        """
        configured: set[str] = set()
        for ts in agent_toolsets:
            name = (ts.get("name") or "").lower().replace(" ", "").replace("_", "")
            if name:
                configured.add(name)
        return configured

    @staticmethod
    def _is_configured(ts_name: str, configured_apps: set[str]) -> bool:
        """Check if a toolset registry name matches any configured app.

        Handles the naming gap between the toolset registry (e.g. ``"drive"``,
        ``"calendar"``) and the agent's configured toolset names from the graph
        DB (e.g. ``"googledrive"``, ``"googlecalendar"``).
        """
        if ts_name in configured_apps:
            return True
        return any(cfg_name.endswith(ts_name) for cfg_name in configured_apps)

    @staticmethod
    def _build_adapter(structured_tool: "StructuredTool", context: "AgentContext") -> "Tool":
        app_name, tool_name = split_original_tool_name(structured_tool)
        return PipesHubStructuredToolAdapter(structured_tool, app_name, tool_name)


__all__ = ["PipesHubToolLoader"]
