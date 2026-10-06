"""AgentBuilder toolset: lets the default assistant propose an agent in a chat.

`draft_agent` only returns a draft for the user to review; it never writes to the
graph or calls an external service. The agent is created later, from the user's
own request, by `AgentService`. Loaded only for the assistant with
`ENABLE_CHAT_AGENT_BUILDER` on (see `factory.agent_builder_skip_apps`).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.agent_builder.models import AgentDraft
from app.agents.actions.agent_builder.rate_limit import allow_draft
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder, ToolsetCategory
from app.modules.agents.collaboration.write_guard import (
    ProvenanceIndex,
    extract_literals,
)
from app.modules.agents.handles import slugify

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

_MAX_NAME = 80
_MAX_DESCRIPTION = 500
_MAX_INSTRUCTIONS = 8000
_MAX_SUGGESTIONS = 20
_MAX_ITEM = 200


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

    async def _accessible_knowledge(self, suggested: list[str]) -> list[str]:
        """Keeps only ids the requester can reach; unverifiable means dropped."""
        if not suggested:
            return []
        graph = self.chat_state.get("graph_provider")
        if graph is None:
            return []
        try:
            containers = await graph.get_accessible_containers(
                self.chat_state.get("user_id", ""), self.chat_state.get("org_id", ""),
            )
        except Exception:
            logger.warning("draft_agent: could not read accessible knowledge; dropping suggestions", exc_info=True)
            return []
        if containers.fallback_reason:
            logger.info("draft_agent: knowledge suggestions dropped (%s)", containers.fallback_reason)
            return []
        return [k for k in suggested if k in containers.app_ids]

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
        short_description="Propose a new custom agent for the user to review and create",
        description=(
            "Use when the user asks you to create, build or set up an agent. This only shows the "
            "user a draft card; nothing is created until they review it and click Create. Do not "
            "claim the agent exists. Tools are never enabled by a draft; suggested_tools are only "
            "hints the user can tick."
        ),
        parameters=[
            ToolParameter(name="name", type=ParameterType.STRING, required=True,
                          description="Short agent name, e.g. 'Offer drafter'."),
            ToolParameter(name="purpose", type=ParameterType.STRING, required=True,
                          description="One or two sentences on what the agent is for."),
            ToolParameter(name="instructions", type=ParameterType.STRING, required=False,
                          description="System instructions for the agent. Omit to reuse the purpose."),
            ToolParameter(name="suggested_knowledge", type=ParameterType.ARRAY, required=False, items={"type": "string"},
                          description="Ids of knowledge sources or collections the user named that the agent should use."),
            ToolParameter(name="suggested_tools", type=ParameterType.ARRAY, required=False, items={"type": "string"},
                          description="Names of tools the agent would need. These are suggestions only."),
        ],
        tags=[Tag(key="category", value="utility"), Tag(key="type", value="utility")],
    )
    async def draft_agent(
        self,
        name: str,
        purpose: str,
        instructions: str | None = None,
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

        tool_args = {
            "name": name, "purpose": purpose, "instructions": instructions,
            "suggested_knowledge": suggested_knowledge, "suggested_tools": suggested_tools,
        }
        draft = AgentDraft(
            name=name,
            handleSuggestion=slugify(name),
            description=purpose,
            instructions=(instructions or purpose).strip()[:_MAX_INSTRUCTIONS],
            knowledge=await self._accessible_knowledge(_clean_list(suggested_knowledge)),
            suggestedTools=_clean_list(suggested_tools),
            provenance=self._provenance(tool_args),
            requestedBy=user_id,
        )
        return _result({
            "status": "drafted",
            "draft": draft.model_dump(),
            "message": "A draft card is shown to the user. Tell them to review it and click Create; the agent does not exist yet.",
        })
