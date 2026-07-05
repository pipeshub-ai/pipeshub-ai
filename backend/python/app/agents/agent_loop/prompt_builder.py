"""`PipesHubPromptBuilder`: agent-loop's `SystemPromptBuilder` implementation
that reproduces the ReAct system prompt `nodes.py::_build_react_system_prompt`
assembles today (Phase 4 of the agent-loop migration).

Reuses every prompt fragment Phase 0 already extracted into standalone
modules — `REACT_BASE_PROMPT`, `GUIDANCE_MAP`, `_build_knowledge_context`,
`_format_user_context`, `_build_workflow_patterns`, `build_capability_summary`,
`build_llm_time_context`, `_get_cached_tool_descriptions` — plus Phase 3's
`ToolGuidanceProvider`, so none of that prompt text is duplicated here. The
sections `nodes.py` has never extracted (tool-schema reference fallback,
citation rules, web-search rules, hybrid-search-strategy guidance) are small,
static, state-gated blocks kept local to this file since it is the only
agent-loop-side consumer; `nodes.py` keeps its own copies on the legacy path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.modules.agents.capability_summary import build_capability_summary
from app.modules.agents.context.connector_detection import (
    _has_clickup_tools,
    _has_confluence_tools,
    _has_github_tools,
    _has_jira_tools,
    _has_onedrive_tools,
    _has_outlook_tools,
    _has_slack_tools,
    _has_teams_tools,
)
from app.modules.agents.context.knowledge_context import _build_knowledge_context
from app.modules.agents.context.tool_descriptions import _get_cached_tool_descriptions
from app.modules.agents.context.user_context import _format_user_context
from app.modules.agents.context.workflow_patterns import _build_workflow_patterns
from app.modules.agents.qna.chat_state import is_custom_agent_system_prompt
from app.utils.time_conversion import build_llm_time_context

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec
    from app.agent_loop_lib.core.types import Goal, Todo
    from app.agent_loop_lib.runtime.runtime import AgentRuntime
    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.tool_guidance import ToolGuidanceProvider

_TOOL_REFERENCE_HEADER = (
    "\n## Available Tools (VALIDATE EVERY WRITE TOOL CALL AGAINST THESE SCHEMAS)\n\n"
    "Each tool below lists its **required** and **optional** parameters with types.\n"
    "Before calling any WRITE tool, find it here and verify ALL **required** parameters\n"
    "have concrete values from the user or context — not guesses, not placeholders.\n"
    "For READ tools, required params usually have reasonable defaults — proceed directly.\n\n"
)

_CITATION_RULES = """
## Citation Rules

When you have internal knowledge from retrieval tools:
1. Cite key facts inline: "Revenue grew 29% [source](ref5)." Focus on the most important claims — do NOT cite every sentence.
2. Use the EXACT Citation ID from the context as a markdown link: [source](ref1). Do NOT manually number citations — the system assigns numbers automatically.
3. One citation per markdown link. Do NOT club multiple Citation IDs in one link.
4. Limit to the most relevant citations overall.
5. Do NOT put citations at end of paragraph — inline after the specific fact
6. If you cannot find the Citation ID for a fact, omit the citation rather than guessing.
"""

_WEB_SEARCH_RULES = """
## Web Search Rules

- Prefer `web_search` over training data for anything that may have changed: news, prices, weather, sports, stocks, software versions, docs, regulations, current events.
- Also prefer `web_search` when user asks for "latest", "current", or "up-to-date" info.
- Prefer `web_search` for general/public knowledge queries: product recommendations, comparisons, reviews, health/medical info, consumer advice, market research, "best X" queries, travel, recipes, scientific research.
- Use training data only for timeless knowledge (math, science, core concepts). When in doubt, prefer `web_search`.
- When a query could have BOTH internal AND external relevance, use BOTH `retrieval.search_internal_knowledge` AND `web_search` in parallel.
- **MANDATORY**: If the available context or retrieval results do NOT contain sufficient information to answer the user's question, you MUST use `web_search` to find relevant information BEFORE telling the user that you don't have enough information or context.
- Cite web results as [source](URL/citation id). Use EXACTLY the URL/citation id shown.
"""

_HYBRID_STRATEGY_HEADER = """
## Hybrid Search Strategy (MANDATORY DEFAULT)

You have BOTH a knowledge base (`retrieval.search_internal_knowledge`) AND live service API tools.
**Default behavior for ANY topic / information query: call BOTH in PARALLEL on your first turn.**
This is not optional — indexed snapshots and live API data are complementary, and combining them
gives users both historical context and current state in one answer. Treat single-source answers
as a degraded fallback only used when one of the rules below explicitly applies.
"""

_HYBRID_STRATEGY_RULES = """
### When to use BOTH retrieval + service tools (DEFAULT for topic queries):
- **Any topic about an indexed service** — e.g., "holiday policy", "Project X updates", "onboarding doc".
  Call `retrieval.search_internal_knowledge` AND the matching service search tool (e.g.
  `confluence.search_content`, `jira.search_issues`) IN PARALLEL.
- **Query mentions a service AND a topic** — e.g., "holidays from Confluence", "Jira tickets about login".
  Service mention narrows the API tool; it does NOT excuse you from also calling retrieval.
- **Benefit**: Indexed content covers historical and cross-service context; the live API has the most
  current data. The user gets the union.

**Live-only exceptions:** Slack, Outlook, Gmail, and Calendar are live-only services. Do NOT pair them with retrieval — for those, use the service tool alone (see the per-service rules later in this prompt: R-SLACK-1, R-OUT-1, etc.).

### When to use ONLY service tools (no retrieval):
- **Live data requests**: "Show my calendar for today", "List my unread emails", "Get my Jira tickets".
  Real-time-only data — retrieval has nothing to add.
- **Action requests**: "Create a page", "Send an email", "Update a ticket". Write operations.
- **Specific resource requests**: "Get page 12345", "Show event details for tomorrow's standup".

### When to use ONLY retrieval (no service tools):
- The agent has no service tool that matches the query's domain.
- Cross-service summaries where no single live API would have the full picture.
"""

_HYBRID_WEB_SEARCH_NOTE = """
### When to use `web_search`:
- Current/changing public info (news, prices, weather, software versions, regulations) or "latest"/"current" requests.
- When you suspect internal knowledge is incomplete on a public-knowledge question — combine with retrieval.
"""

_SERVICE_ONLY_STRATEGY = """
## Service-Tool Search Strategy (MANDATORY DEFAULT)

This agent has live service search tools available but **no knowledge base** is configured
(`retrieval.search_internal_knowledge` is unavailable). Treat the available service search tools
as your **primary search surface** for any topic, information, or org-knowledge query.

### Default behavior for ANY topic / information / org-knowledge query:
- Call the matching service search tool(s) on your **first turn**. Do NOT ask the user which
  app or source — they typically don't know which system holds the answer, and you should
  search proactively. Pick tools by matching the query against each tool's `when_to_use`
  description in the Available Tools section.
- If multiple tools could plausibly contain the answer, call them **IN PARALLEL** in the same
  turn — the union gives the user the best result.

### Specifically forbidden when service search tools are available:
- ❌ Asking "which app / source / system did you mean?" before searching. Search first; ask
  for clarification ONLY after a search returns ambiguous or empty results.
- ❌ Concluding "I don't have that information" or "no knowledge base is configured" without
  first attempting a search with the available service tools.
- ❌ Requiring the user to mention an app by name. A query about org-knowledge is implicitly
  a search query — each tool's `when_to_use` description determines whether it applies, not
  whether the user typed the app name.

### Skip the search ONLY for:
- Pure greetings or thanks ("hi", "thanks").
- Simple arithmetic or date calculations.
- User asking about their own identity / profile.
- Write actions where you already have all required parameters.

If a search returns nothing useful, state that plainly and offer to broaden the query — do
not retreat to ambiguity-clarification.
"""


class PipesHubPromptBuilder:
    """Reproduces PipesHub's multi-layer ReAct prompt for agent-loop.

    Constructed once per request with the same `AgentContext` used by the
    tool adapters (`tool_adapter.py`) so identity/knowledge/toolset fields
    stay in sync between prompt assembly and tool execution.
    """

    def __init__(self, context: AgentContext, guidance: ToolGuidanceProvider) -> None:
        self._context = context
        self._guidance = guidance

    def build(
        self,
        spec: AgentSpec,
        runtime: AgentRuntime,
        goal: Goal,
        todos: list[Todo],
        extra_sections: dict[str, str],
    ) -> str:
        state = self._context.tool_state
        log = self._context.logger
        parts: list[str] = []

        if self._context.instructions and self._context.instructions.strip():
            parts.append(f"## Agent Instructions\n{self._context.instructions.strip()}")

        if is_custom_agent_system_prompt(self._context.system_prompt):
            parts.append(self._context.system_prompt.strip())  # type: ignore[union-attr]

        base_system_prompt = spec.system_prompt if isinstance(spec.system_prompt, str) else ""
        if base_system_prompt:
            parts.append(base_system_prompt)

        # Populated by Phase 5's `conversation_enrichment` PRE_TURN hook
        # (`hooks/memory.py`) when the current query looks like a follow-up
        # — the one place a hook can still influence this builder, since
        # `Goal` (not `AgentContext`) is the run-scoped object hooks mutate.
        if goal is not None and goal.constraints:
            parts.append("## Additional Context\n" + "\n".join(f"- {c}" for c in goal.constraints))

        parts.append(self._build_tool_reference_section(state, log))

        has_retrieval = bool(state.get("final_results"))
        has_attachments = bool(state.get("attachments"))
        has_web_search = bool(state.get("web_search_config"))

        if has_retrieval or has_attachments:
            parts.append(_CITATION_RULES)
        if has_web_search:
            parts.append(_WEB_SEARCH_RULES)

        parts.append(self._build_hybrid_strategy_section(state, has_web_search=has_web_search))

        for guidance_text in self._guidance.get_active_guidance(self._context).values():
            parts.append(guidance_text)

        workflow_patterns = _build_workflow_patterns(state)
        if workflow_patterns:
            parts.append(workflow_patterns)

        knowledge_context = _build_knowledge_context(state, log)
        if knowledge_context:
            parts.append(knowledge_context)

        time_block = build_llm_time_context(
            current_time=self._context.current_time,
            time_zone=self._context.timezone,
        )
        if time_block:
            parts.append(time_block)

        parts.append(build_capability_summary(state))

        user_context = _format_user_context(state)
        if user_context:
            parts.append(user_context)

        return "\n\n".join(part for part in parts if part)

    @staticmethod
    def _build_tool_reference_section(state: dict[str, Any], log: Any) -> str:  # noqa: ANN401
        """Mirrors `nodes.py::_build_react_system_prompt`'s "Available Tools"
        section: full per-tool schemas/guidance from the shared description
        cache, falling back to the compact schema-only reference (still
        defined in `nodes.py`, imported lazily here to avoid a needless
        module-load-time dependency) only if the cache is empty."""
        tool_descriptions = _get_cached_tool_descriptions(state, log)
        if not tool_descriptions:
            from app.modules.agents.qna.nodes import _build_tool_schema_reference

            tool_descriptions = _build_tool_schema_reference(state, log)
        if not tool_descriptions:
            return ""
        return _TOOL_REFERENCE_HEADER + tool_descriptions

    @staticmethod
    def _build_hybrid_strategy_section(state: dict[str, Any], *, has_web_search: bool) -> str:
        has_knowledge = bool(state.get("has_knowledge"))
        has_service_tools = any([
            _has_jira_tools(state),
            _has_confluence_tools(state),
            _has_onedrive_tools(state),
            _has_outlook_tools(state),
            _has_slack_tools(state),
            _has_teams_tools(state),
            _has_github_tools(state),
            _has_clickup_tools(state),
        ])

        if has_knowledge and has_service_tools:
            section = _HYBRID_STRATEGY_HEADER + _HYBRID_STRATEGY_RULES
            if has_web_search:
                section += _HYBRID_WEB_SEARCH_NOTE
            section += "\n### How to merge hybrid results:\n1. Call the appropriate tools (retrieval + service API"
            if has_web_search:
                section += " + web_search as needed"
            section += (
                ") — IN PARALLEL where possible.\n"
                "2. Present a unified answer combining insights from all sources.\n"
                "3. For internal knowledge (retrieval): cite as [source](ref1) using the Citation ID from the context blocks.\n"
            )
            if has_web_search:
                section += (
                    "4. For web search/fetch_url results: cite as [source](URL/citation id) using the URL/citation id.\n"
                    "5. Clearly attribute live API data (e.g., \"According to your Outlook calendar...\" or \"From Confluence...\").\n"
                )
            else:
                section += "4. Clearly attribute live API data (e.g., \"According to your Outlook calendar...\" or \"From Confluence...\").\n"
            return section

        if has_service_tools and not has_knowledge:
            return _SERVICE_ONLY_STRATEGY

        return ""


__all__ = ["PipesHubPromptBuilder"]
