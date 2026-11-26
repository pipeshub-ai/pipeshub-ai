"""`PipesHubToolLoader` — registers PipesHub tools into an ``agent_loop_lib``
`ToolRegistry` for each request.

Connector toolsets (Jira, Confluence, Slack, ...) are loaded by creating
authenticated instances via `ToolInstanceCreator` and scanning their
``@tool``-decorated methods with `ToolsetBuilder`.

Dynamic tools (web_search, fetch_url, SQL, Slack context, fetch_full_record)
are built per-request and wrapped with `PipesHubStructuredToolAdapter` —
except `web_search`/`fetch_url`, which get the `WebToolAdapter` subclass so
their output is rendered through `ToolHandlerRegistry.format_message`
(restores Citation IDs — see `web_tool_adapter.py`).

The loader's responsibility is loading and registration: of each attached
toolset, only the tools picked for the agent (`agent_toolsets[].tools`).
Policy on how tools are called is a separate concern. It does, however,
record WHY a toolset failed to load into `context.toolset_load_failures`
(`"not_authenticated"` vs `"error"`) and WHICH loaded toolsets are
`essential=True` metadata into `context.essential_toolset_names`, and which
instance each external toolset tool runs on, for the approval gate
(`tool_approvals.TOOLSET_TOOLS_BY_PATH`) — all facts only this loop can
observe, and downstream consumers (the global search fallback, the capability
summary, `factory.py`'s pinned-toolsets derivation, the gate) need them
without re-deriving.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import Tag
from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR
from app.agent_loop_lib.tools.errors import (
    DuplicateToolNameError,
    DuplicateToolPathError,
)
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.tools.tags import (
    PROVIDER_TAG_KEY,
    TAG_EFFECT_READ,
    TAG_EFFECT_WRITE,
    TAG_PROVIDER_PLATFORM,
    TAG_SINK_NONE,
    TAG_UI_ONLY,
)
from app.agent_loop_lib.tools.toolset import ToolsetBuilder as AgentLoopToolsetBuilder
from app.agents.agent_loop.instance_creator import (
    ToolInstanceCreator,
    configured_name_matches,
)
from app.agents.agent_loop.tool_adapter import (
    PipesHubStructuredToolAdapter,
    split_original_tool_name,
)
from app.agents.agent_loop.tool_approvals import TOOLSET_TOOLS_BY_PATH, ApprovalTarget
from app.agents.agent_loop.web_tool_adapter import WebToolAdapter
from app.agents.mcp.tool_kind import toolset_tool_kind
from app.agents.tools.factories.base import ToolsetAuthError
from app.agents.tools.factories.registry import ClientFactoryRegistry
from app.connectors.core.registry.tool_builder import ToolProvider
from app.harness.tool_identity.settings import instance_tool_ids_enabled
from app.services.artifact_registry.models import artifact_owner_id
from app.services.graph_db.interface.graph_db_provider import CONNECTION_APPS_FILTER_KEY
from app.utils.error_class import log_failure
from app.utils.logging_fields import log_event, log_once
from harness_core.mcp_hub.names import export_description
from harness_core.tool_identity import ToolBinding, scoped_group, scoped_tool

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.agent_loop_lib.tools.base import Tool
    from app.agents.agent_loop.context import AgentContext
    from app.web.models import CallerContext
    from app.web.services import WebFetchService

logger = logging.getLogger(__name__)

# Normalized (`ToolsetRegistry._normalize_toolset_name`) names of the two
# internal toolsets ("Retrieval", "KnowledgeHub") whose tools are useless —
# and actively misleading, since every call fails with "No knowledge sources
# configured" — when the agent has no knowledge attached. `.as_internal()`
# exempts them from the external-toolset "configured on this agent" check
# below, so they need their own gate on `context.has_knowledge` instead.
_KNOWLEDGE_TOOLSETS = frozenset({"knowledgegraph", "retrieval", "knowledgehub"})

# Code graph is a view over files a repo connector ingested. Without both
# a configured code connector (org-wide) and attached code knowledge
# (agent-scoped), the tools serve no purpose.
_CODE_GRAPH_TOOLSETS = frozenset({"codegraph"})
# Group names of the legacy internal toolsets that execute model-generated
# code through `app.sandbox.manager.get_executor()`. `.as_internal()` exempts
# them from the "configured on this agent" check, so without this gate they
# load into every chat even when no sandbox backend is configured.
_SANDBOX_TOOLSETS = frozenset({"coding_sandbox", "database_sandbox"})

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


def _record_for_approvals(
    context: "AgentContext", tool: "Tool", instance: tuple[str, str] | None, *, fallback_owner: str,
    personal: bool = False,
) -> None:
    """Records an external toolset's tool for the approval gate: the instance its calls run on
    (whose rules apply, and whether it is the person's own) and what it does to data."""
    instance_id, owner = instance or ("", fallback_owner)
    tool_name = tool.path.rsplit("/", 1)[-1]
    type_tag = next((tag.value for tag in tool.tags if tag.key == "type"), None)
    context.tool_state.setdefault(TOOLSET_TOOLS_BY_PATH, {})[tool.path] = ApprovalTarget(
        source="toolset", instance_id=instance_id, tool_name=tool_name, owner=owner,
        kind=toolset_tool_kind(tool_name, type_tag), tool=tool, personal=personal,
    )


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


def note_sandbox_unavailable(exc: Exception) -> None:
    """Code execution is off because of the deployment's sandbox env (`SandboxUnavailableError`): a
    process fact, warned once per process rather than on every turn."""
    if log_once("agent_wiring.sandbox_unavailable"):
        log_event(logger, "code_execution.unavailable", level=logging.WARNING, error_type=type(exc).__name__)


def _sandbox_executor_available() -> bool:
    """Whether `SANDBOX_MODE` names a backend the legacy executor can build.

    Only resolves the mode; nothing is instantiated. A missing or unknown
    mode is logged once per load and the sandbox toolsets are skipped, so a
    misconfigured deployment loses code execution rather than running it
    in-process or failing the chat.
    """
    from app.sandbox.manager import SandboxUnavailableError, get_sandbox_mode

    try:
        get_sandbox_mode()
    except SandboxUnavailableError as exc:
        note_sandbox_unavailable(exc)
        return False
    return True


_SLACK_CONTEXT_TAGS: dict[str, Tag] = {
    "effect": TAG_EFFECT_READ, "sink": TAG_SINK_NONE, "provider": TAG_PROVIDER_PLATFORM,
}


def _build_dynamic_tools(context: "AgentContext") -> list["Tool"]:
    """Build per-request dynamic tools and wrap them as ``Tool`` instances.

    Dynamic tools are LangChain ``StructuredTool`` objects created by
    factory functions, wrapped with ``PipesHubStructuredToolAdapter``.
    """
    state = context.tool_state
    tools: list[Tool] = []

    config_service = state.get("config_service")

    allowed_sql_connector_ids = frozenset(state.get("allowed_sql_connector_ids") or ())
    connection_apps = (state.get("filters") or {}).get(CONNECTION_APPS_FILTER_KEY)
    if connection_apps is not None:
        # A query runs against a whole database, so a connected app reaches
        # only the SQL connectors its user picked whole.
        allowed_sql_connector_ids &= frozenset(connection_apps)
    if (
        config_service
        and state.get("has_sql_connector")
        and state.get("has_sql_knowledge")
        and allowed_sql_connector_ids
    ):
        try:
            from app.utils.execute_query import create_execute_query_tool
            execute_query_tool = create_execute_query_tool(
                config_service=config_service,
                graph_provider=state.get("graph_provider"),
                org_id=state.get("org_id"),
                conversation_id=state.get("conversation_id"),
                blob_store=state.get("blob_store"),
                user_id=state.get("user_id"),
                allowed_connector_ids=allowed_sql_connector_ids,
                artifact_owner_user_id=artifact_owner_id(state),
            )
            setattr(execute_query_tool, "_original_name", "sql.execute_sql_query")
            app_name, tool_name = split_original_tool_name(execute_query_tool)
            # Saves a CSV of the result to conversation storage.
            tools.append(PipesHubStructuredToolAdapter(
                execute_query_tool, app_name, tool_name, effect=TAG_EFFECT_WRITE, provider=TAG_PROVIDER_PLATFORM,
            ))
        except Exception as e:
            log_failure(logger, "tool.build_failed", e, tool="execute_sql_query", include_error_text=False)

    if config_service and state.get("has_slack_connector") and state.get("has_slack_knowledge"):
        try:
            from app.harness.knowledge.caller import caller_user_id_of
            from app.utils.fetch_slack_nearby_messages import (
                create_fetch_slack_nearby_messages_tool,
            )
            from app.utils.fetch_slack_thread import create_fetch_slack_thread_tool

            slack_thread_tool = create_fetch_slack_thread_tool(
                virtual_record_id_to_result=state.get("virtual_record_id_to_result", {}),
                org_id=state.get("org_id", ""),
                graph_provider=state.get("graph_provider"),
                blob_store=state.get("blob_store"),
                config_service=config_service,
                tool_state=state,
                user_id=state.get("user_id"),
                caller_user_id=caller_user_id_of(state.get("user_info")),
            )
            setattr(slack_thread_tool, "_original_name", "slack.fetch_slack_thread")
            a, t = split_original_tool_name(slack_thread_tool)
            # Platform knowledge reads behind the record-access gate (QA27-F1): both only call conversations.* reads,
            # on the indexed record's own connector, and send nothing the model chose anywhere.
            tools.append(PipesHubStructuredToolAdapter(slack_thread_tool, a, t, **_SLACK_CONTEXT_TAGS))

            slack_nearby_tool = create_fetch_slack_nearby_messages_tool(
                config_service=config_service,
                graph_provider=state.get("graph_provider"),
                org_id=state.get("org_id", ""),
                user_id=state.get("user_id"),
                caller_user_id=caller_user_id_of(state.get("user_info")),
                tool_state=state,
            )
            setattr(slack_nearby_tool, "_original_name", "slack.fetch_slack_nearby_messages")
            a, t = split_original_tool_name(slack_nearby_tool)
            tools.append(PipesHubStructuredToolAdapter(slack_nearby_tool, a, t, **_SLACK_CONTEXT_TAGS))
        except Exception as e:
            log_failure(logger, "tool.build_failed", e, tool="slack_context", include_error_text=False)

    web_search_config = state.get("web_search_config")
    if web_search_config:
        ref_mapper = state.get("citation_ref_mapper")
        if ref_mapper is None:
            from app.utils.chat_helpers import CitationRefMapper
            ref_mapper = CitationRefMapper()
            state["citation_ref_mapper"] = ref_mapper

        try:
            from app.edition_services import get_web_access_gate
            from app.web.budget import WEB_BUDGET_KEY, WebBudget
            from app.web.container import build_web_services
            from app.web.models import CallerContext
            from app.web.own_files import ISSUED_DOWNLOAD_LINKS_KEY
            from app.web.tools.fetch_url import create_fetch_url_tool
            from app.web.tools.web_search import create_web_search_tool

            budget = WebBudget.for_context(
                scope=getattr(context, "web_scope", "answer"), deep_research=context.deep_research,
            )
            # The web tools' adapter reads what is left, so a long run's model sees its allowance (WP-62).
            state[WEB_BUDGET_KEY] = budget
            services = build_web_services(web_search_config, get_web_access_gate(), budget)
            caller = CallerContext(org_id=context.org_id, user_id=context.user_id, conversation_id=context.conversation_id)
            fetch_url = create_fetch_url_tool(
                services.fetch, caller, ref_mapper=ref_mapper,
                issued_links=state.setdefault(ISSUED_DOWNLOAD_LINKS_KEY, {}),
            )
            web_tools = [create_web_search_tool(services.search, caller, scope=budget.scope), fetch_url]
            image_options = _page_image_options(web_search_config, services.fetch, caller)
        except Exception as e:
            web_tools, fetch_url, image_options = [], None, {}
            log_failure(logger, "tool.build_failed", e, tool="web", include_error_text=False)
        for web_tool in web_tools:
            a, t = split_original_tool_name(web_tool)
            tools.append(WebToolAdapter(web_tool, a, t, context, **(image_options if web_tool is fetch_url else {})))

    return tools


def toolset_provider_tags(ts_meta: Mapping[str, object]) -> tuple[Tag, ...]:
    """The provider a toolset's registration declares, as the tag each of its tools is registered with; none when it
    declares none (such a tool is granted to no durable or engine run)."""
    provider = ts_meta.get("provider")
    return (Tag(PROVIDER_TAG_KEY, str(provider)),) if provider else ()


def _page_image_options(web_search_config: dict, fetch_service: "WebFetchService", caller: "CallerContext") -> dict:
    """``WebToolAdapter`` options that send a fetched page's images to the model: none unless an
    admin turned "Send images to LLM" on (``settings.includeImages``), then up to
    ``settings.maxImages`` per page, read through the run's gated fetch service."""
    from app.web.config import image_settings

    settings = image_settings(web_search_config.get("settings"))
    if not settings["includeImages"]:
        return {}

    async def load(urls: list[str]) -> list[str | None]:
        return await fetch_service.fetch_images(urls, caller)

    return {"image_loader": load, "max_images": settings["maxImages"]}


def _normalized_toolset_name(toolset: dict) -> str:
    """An agent toolset entry's name in the form `configured_name_matches` compares."""
    return (toolset.get("name") or "").lower().replace(" ", "").replace("_", "")


class PipesHubToolLoader:
    """Registers PipesHub tools into an agent-loop ``ToolRegistry`` per request.

    Of each toolset attached to the agent it registers only the tools picked
    for it; it does not enforce policy on how those tools are called.
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

            external = ts_meta.get("provider") == ToolProvider.CONNECTOR.value

            if external and not self._is_configured(ts_name, configured_apps):
                log_event(logger, "toolset.skipped", level=logging.DEBUG, toolset=ts_name, reason="unconfigured")
                continue

            if group_name in _SANDBOX_TOOLSETS and not _sandbox_executor_available():
                continue

            if ts_name in _KNOWLEDGE_TOOLSETS and not context.has_knowledge:
                # The condition behind "the agent answered as if it had searched and found nothing"
                # (`prompt_builder._NO_KNOWLEDGE_SOURCES`): greppable at INFO as the turn's
                # `agent.build.end has_knowledge=False`; per toolset at DEBUG.
                log_event(logger, "toolset.skipped", level=logging.DEBUG, toolset=ts_name, reason="no_knowledge")
                continue

            if ts_name in _CODE_GRAPH_TOOLSETS and not (
                context.has_code_connector and context.has_code_knowledge
            ):
                log_event(logger, "toolset.skipped", level=logging.DEBUG, toolset=ts_name, reason="no_code_knowledge")
                continue

            # One toolset per configured instance when the agent binds several of this type (P2.2).
            instances = (
                instance_creator.bindings_for_app(ts_name)
                if external and instance_tool_ids_enabled()
                else []
            )
            targets: list[dict | None] = list(instances) if len(instances) > 1 else [None]
            try:
                for target in targets:
                    tool_count += await self._load_toolset(
                        context,
                        registry,
                        instance_creator,
                        ts_class,
                        ts_name,
                        ts_meta,
                        group_name,
                        ts_path_prefix,
                        target=target,
                        external=external,
                    )
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
                # Once, here (the instance creator raises): no error text, a provider's answer can quote the request.
                log_failure(
                    logger, "toolset.load_failed", e, toolset=ts_name, outcome="failure", reason=reason,
                    error_class="auth_expired" if reason == "not_authenticated" else None, include_error_text=False,
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
                log_event(logger, "tool.duplicate", level=logging.DEBUG, tool=dt.name)

        log_event(
            logger, "toolset.load.summary", level=logging.DEBUG, tools=tool_count, toolsets=len(loaded_apps),
            dynamic_tools=dynamic_count, skipped_apps=len(skip_apps),
        )
        return registry

    @staticmethod
    async def _load_toolset(
        context: "AgentContext",
        registry: ToolRegistry,
        instance_creator: ToolInstanceCreator,
        ts_class: type,
        ts_name: str,
        ts_meta: dict[str, Any],
        group_name: str,
        ts_path_prefix: str,
        *,
        target: dict | None,
        external: bool,
    ) -> int:
        """Registers one toolset: the type's (``target`` None, today's names) or one configured
        instance's, under its own group and paths; records each tool's binding. Returns the count."""
        ts_description = ts_meta.get("description", "")
        if target is None:
            instance_id = None
            group, prefix, rebase, label = group_name, ts_path_prefix, None, None
            binding = instance_creator.binding_for_app(ts_name) if external else None
        else:
            instance_id = str(target["instanceId"])
            group = scoped_group(group_name, instance_id, shared=True)
            parent = ts_path_prefix.rsplit("/", 1)[0]
            prefix = f"{parent}/{group}"

            def rebase(path: str, instance: str = instance_id) -> str:
                scoped, method = scoped_tool(group_name, instance, path.rsplit("/", 1)[-1])
                return f"{parent}/{scoped}/{method}"

            label = export_description(
                str(target.get("instanceName") or target.get("displayName") or instance_id), limit=64
            )
            ts_description = f"{ts_description} ({label})" if ts_description else label
            binding = target
        instance = await instance_creator.create_instance_async(
            ts_class, ts_name, instance_id=instance_id
        )
        provider_tags = toolset_provider_tags(ts_meta)
        toolset = AgentLoopToolsetBuilder(
            instance,
            name=group,
            description=ts_description,
            path_prefix=prefix,
            rebase=rebase,
            label=label,
            gate=instance_creator.call_gate(ts_name, binding.get("instanceId") if binding is not None else None),
            call_scope=instance_creator.call_scope(
                ts_name, binding.get("instanceId") if binding is not None else None
            ),
        )
        tools = toolset.tools
        if callable(getattr(ts_class, "tools_for_request", None)):
            # A toolset may narrow or specialize its tools for this
            # request (KnowledgeGraph: tools a deployment switch adds).
            tools = instance.tools_for_request(tools)
        if not context.has_ui_client:
            # With no UI to render the question, the model's filler
            # text becomes the final answer and the run just ends.
            ui_only = [t.name for t in tools if TAG_UI_ONLY in t.tags]
            if ui_only:
                tools = [t for t in tools if TAG_UI_ONLY not in t.tags]
                log_event(logger, "toolset.ui_tools_withheld", level=logging.DEBUG, toolset=ts_name, tools=len(ui_only))
        is_internal = ts_meta.get("isInternal", False)
        selected = None if is_internal else PipesHubToolLoader._selected_tool_names(ts_name, context.agent_toolsets)
        if selected is not None:
            # Same name the agent builder lists and saves: the last segment of the @tool path.
            tools = [t for t in tools if t.declared_path.rsplit("/", 1)[-1] in selected]
            # Agents saved before a tool was renamed still pick the old name,
            # and the builder shows it as picked.
            stale = selected - {t.declared_path.rsplit("/", 1)[-1] for t in toolset.tools}
            if stale:
                log_event(logger, "toolset.tools_missing", level=logging.WARNING, toolset=ts_name, missing=len(stale))
        bound_instance = binding.get("instanceId") if binding is not None else None
        if bound_instance:
            from app.harness.write_identity.ports import write_identity_port

            # OWD-18: who this instance's writes are from is the write identity port's (this build: the user's own).
            tools = await write_identity_port().tools(
                context,
                instance_creator,
                ts_class,
                ts_name,
                str(bound_instance),
                tools,
                lambda client, gate: AgentLoopToolsetBuilder(
                    client,
                    name=group,
                    description=ts_description,
                    path_prefix=prefix,
                    rebase=rebase,
                    label=label,
                    gate=gate,
                    call_scope=instance_creator.call_scope(ts_name, str(bound_instance)),
                ).tools,
            )
        # A toolset with a client factory (an external service) passes the approval gate, under its instance's rules.
        gated = ClientFactoryRegistry.get_factory(ts_name) is not None and not is_internal
        approval_instance = (
            None if not gated
            else (instance_id, label or instance_id) if instance_id is not None
            else instance_creator.instance_for_app(ts_name)
        )
        personal = bool(approval_instance) and instance_creator.instance_is_personal(approval_instance[0])
        count = 0
        for t in tools:
            try:
                registry.register_tool(t, extra_tags=provider_tags)
                count += 1
            except (DuplicateToolPathError, DuplicateToolNameError):
                log_event(logger, "tool.duplicate", level=logging.DEBUG, tool=t.name)
                continue
            if gated:
                _record_for_approvals(context, t, approval_instance, fallback_owner=group, personal=personal)
            if binding is not None:
                context.tool_bindings[t.name] = ToolBinding(
                    str(binding.get("name") or ts_name),
                    binding.get("instanceId") or None,
                    t.declared_path.rsplit("/", 1)[-1],
                )

        registered_names = [t.name for t in toolset.tools if registry.has(t.name)]
        if registered_names:
            try:
                registry.register_toolset(group, ts_description, registered_names)
                if ts_meta.get("essential", False) and group not in context.essential_toolset_names:
                    context.essential_toolset_names.append(group)
            except Exception as e:
                # The toolset still loads; it just drops out of lazy `fetch_tools`.
                log_failure(logger, "toolset.group_failed", e, toolset=ts_name, include_error_text=False)
        return count

    @staticmethod
    def _build_configured_apps_set(agent_toolsets: list[dict]) -> set[str]:
        """Build a set of normalized app names from the agent's configured toolsets.

        Used to skip external toolsets the user hasn't configured on this agent,
        avoiding noisy factory errors for unconfigured connectors.
        """
        return {name for ts in agent_toolsets if (name := _normalized_toolset_name(ts))}

    @staticmethod
    def _is_configured(ts_name: str, configured_apps: set[str]) -> bool:
        """Check if a toolset registry name matches any configured app.

        Handles the naming gap between the toolset registry (e.g. ``"drive"``,
        ``"calendar"``) and the agent's configured toolset names from the graph
        DB (e.g. ``"googledrive"``, ``"googlecalendar"``).
        """
        return any(configured_name_matches(ts_name, cfg_name) for cfg_name in configured_apps)

    @staticmethod
    def _selected_tool_names(ts_name: str, agent_toolsets: list[dict]) -> set[str] | None:
        """The tool names picked for toolset *ts_name* — in the agent builder,
        narrowed by the chat composer's picks — across every entry for it (the
        assistant has one per instance). ``None`` when any entry lists no tool:
        an entry saved without tools has always loaded all of them.
        """
        selected: set[str] = set()
        for ts in agent_toolsets:
            name = _normalized_toolset_name(ts)
            if not name or not configured_name_matches(ts_name, name):
                continue
            picked = {
                tool_name for t in ts.get("tools") or []
                if isinstance(t, dict) and isinstance(tool_name := t.get("name"), str) and tool_name
            }
            if not picked:
                return None
            selected |= picked
        return selected or None


__all__ = ["PipesHubToolLoader", "toolset_provider_tags"]
