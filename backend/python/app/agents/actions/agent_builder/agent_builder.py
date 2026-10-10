"""AgentBuilder toolset: lets the default assistant propose an agent in a chat.

`draft_agent` only returns a draft for the user to review; it reads the requester's
knowledge, toolsets and web search config but never writes to the graph or calls an external service. The agent is created later, from the user's
own request, by `AgentService`. Loaded only for the assistant with
`ENABLE_CHAT_AGENT_BUILDER` on (see `factory.agent_builder_skip_apps`).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.agent_builder.models import (
    AgentDraft,
    DraftKnowledge,
    DraftToolset,
    DraftUnresolved,
    DraftWebSearch,
)
from app.agents.actions.agent_builder.rate_limit import allow_draft
from app.agents.actions.agent_builder.resolve import (
    KnowledgeEntry,
    external_toolsets,
    resolve_knowledge,
    resolve_tools,
    resolve_web_search,
    summarize_unresolved,
    toolset_label,
)
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder, ToolsetCategory
from app.modules.agents.collaboration.write_guard import (
    ProvenanceIndex,
    extract_literals,
)
from app.modules.agents.handles import slugify

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.modules.agents.qna.chat_state import ChatState
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

_MAX_NAME = 80
_MAX_DESCRIPTION = 500
_MAX_INSTRUCTIONS = 8000
_MAX_SUGGESTIONS = 20
_MAX_ITEM = 200
_KB_PAGE_SIZE = 200
_MAX_KB_PAGES = 10
_MAX_OPTION_KNOWLEDGE = 100
_MAX_OPTION_TOOLSETS = 30
_MAX_OPTION_TOOLS = 40


def _result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _error(code: str, message: str) -> str:
    return _result({"status": "error", "code": code, "message": message})


def _clean_list(values: list[str] | None) -> list[str]:
    seen: dict[str, None] = {}
    for value in values or []:
        item = str(value).strip()[:_MAX_ITEM]
        if item:
            seen.setdefault(item)
    return list(seen)[:_MAX_SUGGESTIONS]


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


@ToolsetBuilder("AgentBuilder")\
    .in_group("Agent Builder")\
    .with_description("Draft a custom agent for the user to review - no authentication required, nothing is created")\
    .with_category(ToolsetCategory.UTILITY)\
    .with_auth([
        AuthBuilder.type("NONE").fields([])
    ])\
    .as_internal()\
    .build_decorator()
class AgentBuilder:
    def __init__(self, state: ChatState) -> None:
        self.chat_state = state

    async def _knowledge_catalog(self) -> list[KnowledgeEntry]:
        """Collections and connectors the requester can reach; anything unverifiable is left out."""
        graph = self.chat_state.get("graph_provider")
        user_id = str(self.chat_state.get("user_id", ""))
        org_id = str(self.chat_state.get("org_id", ""))
        if graph is None or not user_id:
            return []
        try:
            containers = await graph.get_accessible_containers(user_id, org_id)
            if containers.fallback_reason:
                logger.info("agent builder: knowledge unavailable (%s)", containers.fallback_reason)
                return []
            user = await graph.get_user_by_user_id(user_id=user_id)
            user_key = (user.get("id") or user.get("_key")) if user else None
            if not user_key:
                return []
            collections, options = await asyncio.gather(
                self._list_collections(graph, user_key, org_id),
                graph.get_knowledge_hub_filter_options(user_key=user_key, org_id=org_id),
            )
        except Exception:
            logger.warning("agent builder: could not read the requester's knowledge", exc_info=True)
            return []
        entries = [
            KnowledgeEntry(id=str(kb["id"]), name=(kb.get("name") or "").strip() or "Untitled", kind="collection")
            for kb in collections
        ]
        entries += [
            KnowledgeEntry(
                id=str(app["id"]), name=(app.get("name") or "").strip() or str(app.get("type") or app["id"]),
                kind="connector", connector_type=app.get("type") or None,
            )
            for app in (options or {}).get("apps") or []
            if isinstance(app, dict) and app.get("id")
        ]
        return [e for e in entries if e.id in containers.app_ids]

    @staticmethod
    async def _list_collections(graph: IGraphDBProvider, user_key: str, org_id: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for page in range(_MAX_KB_PAGES):
            kbs, total, _ = await graph.list_user_knowledge_bases(
                user_id=user_key, org_id=org_id, skip=page * _KB_PAGE_SIZE, limit=_KB_PAGE_SIZE,
            )
            found += [kb for kb in kbs if kb.get("id")]
            if not kbs or (page + 1) * _KB_PAGE_SIZE >= total:
                break
        return found

    async def _toolset_catalog(self) -> tuple[list[Mapping[str, Any]], list[dict[str, Any]]]:
        """The requester's signed-in external toolsets, and every registered one (for "not connected")."""
        config_service = self.chat_state.get("config_service")
        user_id = str(self.chat_state.get("user_id", ""))
        org_id = str(self.chat_state.get("org_id", ""))
        try:
            from app.agents.registry.toolset_registry import get_toolset_registry
            from app.api.routes.toolsets import get_authenticated_toolsets
            from app.services.featureflag.platform_settings import is_actions_enabled

            if not await is_actions_enabled(config_service):
                return [], []
            registry = get_toolset_registry()
            toolsets, _ = await get_authenticated_toolsets(user_id, org_id, config_service, registry)
        except Exception:
            logger.warning("agent builder: could not read the requester's toolsets", exc_info=True)
            return [], []
        registered = [
            {"name": meta.get("name"), "display_name": meta.get("display_name"), "internal": meta.get("isInternal", False)}
            for meta in registry.get_all_toolsets().values()
        ]
        internal = [str(r["name"]) for r in registered if r["internal"]]
        return external_toolsets(toolsets, internal), [r for r in registered if not r["internal"]]

    async def _web_search_config(self) -> dict[str, Any] | None:
        from app.agents.chat_modes.bridge import _resolve_web_search_config

        config_service = self.chat_state.get("config_service")
        if config_service is None:
            return None
        return await _resolve_web_search_config(config_service, logger)

    def _provenance(self, tool_args: dict[str, Any]) -> str:
        index = self.chat_state.get("provenance_index")
        if isinstance(index, ProvenanceIndex):
            foreign = (extract_literals(tool_args) & index.others) - index.sender
            if foreign:
                return "content"
        # Anything retrieved from documents this turn may have steered the request.
        return "content" if self.chat_state.get("final_results") else "sender"

    @tool(
        path="/tools/agent_builder/draft_agent",
        short_description="Propose a new custom agent, with knowledge, tools and web search, for the user to review and create",
        description=(
            "Use when the user asks you to create, build or set up an agent, or to change a draft you "
            "already showed. This only shows the user a draft card; nothing is created until they review "
            "it and click Create, so never say the agent exists. Pass knowledge, tools and web search by "
            "the names the user used (for example 'HR Policies', 'Jira', 'create Jira issues'), never "
            "ids. The server matches them against what this user can access and pre-selects what it "
            "found. The result lists anything it could not add under `unresolved`: tell the user about "
            "each one plainly, and never claim something was added that was not. If the user asks to "
            "change a draft, call this again with the full new setup and set revises_draft_id to the "
            "earlier draft's draftId. Use list_agent_options first when you are unsure what is available."
        ),
        parameters=[
            ToolParameter(name="name", type=ParameterType.STRING, required=True,
                          description="Short agent name, e.g. 'Offer drafter'."),
            ToolParameter(name="purpose", type=ParameterType.STRING, required=True,
                          description="One or two sentences on what the agent is for."),
            ToolParameter(name="instructions", type=ParameterType.STRING, required=False,
                          description="System instructions for the agent. Omit to reuse the purpose."),
            ToolParameter(name="knowledge", type=ParameterType.ARRAY, required=False, items={"type": "string"},
                          description="Names of the collections or connected sources the agent should search, as the user said them."),
            ToolParameter(name="tools", type=ParameterType.ARRAY, required=False, items={"type": "string"},
                          description="Apps or actions the agent may use, as the user said them: an app ('Jira', "
                                      "'Slack') for all its actions, or one action ('create Jira issue', 'jira.create_issue')."),
            ToolParameter(name="web_search", type=ParameterType.BOOLEAN, required=False,
                          description="True when the agent should be able to search the web."),
            ToolParameter(name="revises_draft_id", type=ParameterType.STRING, required=False,
                          description="The draftId of the earlier draft this one replaces."),
        ],
        tags=[Tag(key="category", value="utility"), Tag(key="type", value="utility")],
    )
    async def draft_agent(
        self,
        name: str,
        purpose: str,
        instructions: str | None = None,
        knowledge: list[str] | None = None,
        tools: list[str] | None = None,
        web_search: bool | str | None = None,
        revises_draft_id: str | None = None,
        suggested_knowledge: list[str] | None = None,
        suggested_tools: list[str] | None = None,
    ) -> str:
        if self.chat_state.get("invocation") != "assistant":
            return _error("NOT_AVAILABLE", "Agents can only be drafted from the default assistant.")
        name = (name or "").strip()[:_MAX_NAME]
        purpose = (purpose or "").strip()[:_MAX_DESCRIPTION]
        if not name or not purpose:
            return _error("INVALID_INPUT", "name and purpose are required.")

        user_id = str(self.chat_state.get("user_id", ""))
        org_id = str(self.chat_state.get("org_id", ""))
        if not await allow_draft(self.chat_state.get("config_service"), org_id, user_id):
            return _error("RATE_LIMITED", "Daily agent draft limit reached. Try again tomorrow.")

        wanted_knowledge = _clean_list([*(knowledge or []), *(suggested_knowledge or [])])
        wanted_tools = _clean_list([*(tools or []), *(suggested_tools or [])])
        wants_web = _truthy(web_search)
        tool_args = {
            "name": name, "purpose": purpose, "instructions": instructions,
            "knowledge": wanted_knowledge, "tools": wanted_tools, "web_search": wants_web,
        }

        knowledge_sources, knowledge_missing = await self._resolve_knowledge(wanted_knowledge)
        actions, tools_missing = await self._resolve_tools(wanted_tools)
        web, web_missing = await self._resolve_web_search(wanted=wants_web)
        unresolved = [*knowledge_missing, *tools_missing, *([web_missing] if web_missing else [])]

        draft = AgentDraft(
            name=name,
            handleSuggestion=slugify(name),
            description=purpose,
            instructions=(instructions or purpose).strip()[:_MAX_INSTRUCTIONS],
            knowledge=[k.id for k in knowledge_sources],
            knowledgeSources=knowledge_sources,
            actions=actions,
            webSearch=web,
            unresolved=unresolved,
            revisesDraftId=(revises_draft_id or "").strip()[:_MAX_ITEM] or None,
            provenance=self._provenance(tool_args),
            requestedBy=user_id,
        )
        message = "A draft card is shown to the user. Tell them to review it and click Create; the agent does not exist yet."
        if unresolved:
            message += f" These could not be added, so say so: {summarize_unresolved(unresolved)}."
        return _result({"status": "drafted", "draft": draft.model_dump(), "message": message})

    async def _resolve_knowledge(self, wanted: list[str]) -> tuple[list[DraftKnowledge], list[DraftUnresolved]]:
        if not wanted:
            return [], []
        return resolve_knowledge(wanted, await self._knowledge_catalog())

    async def _resolve_tools(self, wanted: list[str]) -> tuple[list[DraftToolset], list[DraftUnresolved]]:
        if not wanted:
            return [], []
        toolsets, registered = await self._toolset_catalog()
        return resolve_tools(wanted, toolsets, registered)

    async def _resolve_web_search(self, *, wanted: bool) -> tuple[DraftWebSearch | None, DraftUnresolved | None]:
        if not wanted:
            return None, None
        from app.modules.agents.service.builders import _SUPPORTED_WEB_SEARCH_PROVIDERS

        return resolve_web_search(await self._web_search_config(), _SUPPORTED_WEB_SEARCH_PROVIDERS)

    @tool(
        path="/tools/agent_builder/list_agent_options",
        short_description="List the knowledge, action tools and web search the user can give a new agent",
        description=(
            "Lists what this user can attach to a new agent: their collections and connected sources, "
            "the apps they are signed in to with each app's actions, and whether web search is available. "
            "Use it to answer 'what can I give my agent?' or to pick the right names before draft_agent. "
            "Read-only."
        ),
        parameters=[
            ToolParameter(name="query", type=ParameterType.STRING, required=False,
                          description="Only list options whose name contains this text."),
        ],
        tags=[Tag(key="category", value="utility"), Tag(key="type", value="utility")],
    )
    async def list_agent_options(self, query: str | None = None) -> str:
        if self.chat_state.get("invocation") != "assistant":
            return _error("NOT_AVAILABLE", "Agent options are only available from the default assistant.")
        needle = (query or "").strip().casefold()

        def keep(*texts: object) -> bool:
            return not needle or any(needle in str(t).casefold() for t in texts)

        catalog, (toolsets, _), web_config = await asyncio.gather(
            self._knowledge_catalog(), self._toolset_catalog(), self._web_search_config(),
        )
        from app.modules.agents.service.builders import _SUPPORTED_WEB_SEARCH_PROVIDERS

        web, _ = resolve_web_search(web_config, _SUPPORTED_WEB_SEARCH_PROVIDERS)
        return _result({
            "status": "ok",
            "knowledge": [
                {"name": e.name, "kind": e.kind} for e in catalog if keep(e.name)
            ][:_MAX_OPTION_KNOWLEDGE],
            "actionToolsets": [
                {
                    "displayName": toolset_label(ts),
                    "tools": [t["fullName"] for t in ts.get("tools") or [] if t.get("fullName")][:_MAX_OPTION_TOOLS],
                }
                for ts in toolsets
                if keep(ts.get("displayName"), ts.get("name"), ts.get("instanceName"),
                        *(t.get("fullName") for t in ts.get("tools") or []))
            ][:_MAX_OPTION_TOOLSETS],
            "webSearch": {"available": web is not None, "provider": web.providerLabel if web else None},
        })
