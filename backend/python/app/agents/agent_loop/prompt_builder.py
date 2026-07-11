"""`PipesHubPromptBuilder`: agent-loop's `SystemPromptBuilder` implementation
that reproduces the ReAct system prompt `nodes.py::_build_react_system_prompt`
assembles today (Phase 4 of the agent-loop migration).

Reuses every prompt fragment Phase 0 already extracted into standalone
modules — `REACT_BASE_PROMPT`, `GUIDANCE_MAP`, `_build_knowledge_context`,
`_format_user_context`, `_build_workflow_patterns`, `build_capability_summary`,
`build_llm_time_context` — plus Phase 3's `ToolGuidanceProvider`, so none of
that prompt text is duplicated here. The sections `nodes.py` has never
extracted (tool reference, citation rules, web-search rules,
hybrid-search-strategy guidance) are small, static, state-gated blocks kept
local to this file since it is the only agent-loop-side consumer; `nodes.py`
keeps its own copies on the legacy path.

The "Available Tools" section is rendered from `spec.tool_names` +
`runtime.tool_registry` (what the agent was ACTUALLY granted this turn),
never from the legacy flat ChatState tool list — when domain-agent
composition (`domain_agents.py`) is active, `spec.tool_names` holds a
handful of `AgentTool` delegates (`coding_agent`, `web_agent`, ...) plus
residual tools, and describing the ~30 tools those delegates claimed would
both blow up prompt size and describe tools the model can no longer call
directly. The steering sections below (code execution, web search, tool
selection, hybrid strategy) follow the same rule: reference whichever of
`run_code`/`coding_agent`, `web_search`+`fetch_url`/`web_agent`,
`retrieval.search_internal_knowledge`/`internal_search_agent` is actually
in `spec.tool_names`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.errors import ToolNotFoundError
from app.agents.agent_loop.sandbox_bridge import sandbox_network_enabled
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


def _composed_code_tool(tool_names: list[str]) -> str | None:
    """`"coding_agent"` if the domain-agent catalog (`domain_agents.py`)
    claimed `run_code` for this request, else `"run_code"` if it's granted
    directly, else `None` if code execution isn't available this turn."""
    if "coding_agent" in tool_names:
        return "coding_agent"
    if "run_code" in tool_names:
        return "run_code"
    return None


def _web_reference(tool_names: list[str]) -> str:
    """Markdown-formatted reference to whichever web-research surface is
    actually granted: the composed `web_agent` delegate, or the flat
    `web_search`/`fetch_url` pair otherwise — used as a fallback even when
    neither is literally in `tool_names`, mirroring the legacy prompt's
    assumption that the caller already gated on `has_web_search` before
    rendering any text that mentions this reference."""
    return "`web_agent`" if "web_agent" in tool_names else "`web_search`/`fetch_url`"


def _internal_knowledge_reference(tool_names: list[str]) -> str:
    """`"internal_search_agent"` when the domain-agent catalog claimed
    retrieval, else the flat `retrieval.search_internal_knowledge` tool
    name — mirrors `_web_reference`/`_composed_code_tool` above."""
    return "internal_search_agent" if "internal_search_agent" in tool_names else "retrieval.search_internal_knowledge"


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

_WEB_SEARCH_RULES_COMPOSED = """
## Web Search Rules

- Prefer delegating to `web_agent` over training data for anything that may have changed: news, prices, weather, sports, stocks, software versions, docs, regulations, current events.
- Also delegate to `web_agent` when the user asks for "latest", "current", or "up-to-date" info.
- Prefer delegating to `web_agent` for general/public knowledge queries: product recommendations, comparisons, reviews, health/medical info, consumer advice, market research, "best X" queries, travel, recipes, scientific research.
- Use training data only for timeless knowledge (math, science, core concepts). When in doubt, delegate to `web_agent`.
- When a query could have BOTH internal AND external relevance, delegate to your internal-knowledge tool AND `web_agent` in parallel.
- **MANDATORY**: If the available context or retrieval results do NOT contain sufficient information to answer the user's question, you MUST delegate to `web_agent` to find relevant information BEFORE telling the user that you don't have enough information or context.
- Cite web results as [source](URL/citation id). Use EXACTLY the URL/citation id shown.
"""


def _hybrid_strategy_header(internal_ref: str) -> str:
    return f"""
## Hybrid Search Strategy (MANDATORY DEFAULT)

You have BOTH a knowledge base (`{internal_ref}`) AND live service API tools.
**Default behavior for ANY topic / information query: call BOTH in PARALLEL on your first turn.**
This is not optional — indexed snapshots and live API data are complementary, and combining them
gives users both historical context and current state in one answer. Treat single-source answers
as a degraded fallback only used when one of the rules below explicitly applies.
"""


def _hybrid_strategy_rules(internal_ref: str) -> str:
    composed = internal_ref == "internal_search_agent"
    imperative = "Delegate to" if composed else "Call"
    gerund = "delegating to" if composed else "calling"
    return f"""
### When to use BOTH retrieval + service tools (DEFAULT for topic queries):
- **Any topic about an indexed service** — e.g., "holiday policy", "Project X updates", "onboarding doc".
  {imperative} `{internal_ref}` AND the matching service search tool (e.g.
  `confluence.search_content`, `jira.search_issues`) IN PARALLEL.
- **Query mentions a service AND a topic** — e.g., "holidays from Confluence", "Jira tickets about login".
  Service mention narrows the API tool; it does NOT excuse you from also {gerund} `{internal_ref}`.
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


def _hybrid_web_search_note(web_ref: str) -> str:
    return f"""
### When to use {web_ref}:
- Current/changing public info (news, prices, weather, software versions, regulations) or "latest"/"current" requests.
- When you suspect internal knowledge is incomplete on a public-knowledge question — combine with retrieval.
"""

_CODE_EXECUTION_STEERING_NO_NETWORK = """
## Code Execution (MANDATORY for file generation; NO network access)

When a task requires ANY of the following, you MUST use `run_code` — never answer in text alone, and never claim you cannot do it:
- Generating a downloadable file (PDF, DOCX, XLSX, CSV, image, chart, presentation).
- Data processing that produces structured output (tables, aggregations, transformations) beyond what a single tool call returns.
- Any computation the user explicitly asks you to "run", "execute", or "code".

`run_code`'s sandbox has NO network access, ever — code that tries to call an external API, fetch a URL, or scrape a page (via `requests`/`fetch`/`urllib`/anything) will fail. For any task needing live/external data (a REST API, a webpage, search results, ...), call `web_search` or `fetch_url` FIRST to get that data, THEN pass the already-fetched data into `run_code`'s `code` as a literal for processing/file generation — never write network calls inside `run_code`.

Do NOT describe file contents in markdown as a substitute for producing the file. Write a complete program with `run_code`, have it write the output file(s) to the working directory (or `$OUTPUT_DIR`), and let the resulting `artifacts` be delivered automatically — do not print file paths in your answer.
"""

_CODE_EXECUTION_STEERING_NETWORK = """
## Code Execution (MANDATORY for file generation; network access available)

When a task requires ANY of the following, you MUST use `run_code` — never answer in text alone, and never claim you cannot do it:
- Generating a downloadable file (PDF, DOCX, XLSX, CSV, image, chart, presentation).
- Data processing that produces structured output (tables, aggregations, transformations) beyond what a single tool call returns.
- Any computation the user explicitly asks you to "run", "execute", or "code".
- Getting live, structured data from a well-known public REST API and analyzing/filtering/aggregating it — write ONE `run_code` program that calls the API (e.g. with `requests`/`httpx`/`fetch`) and processes the response, rather than eyeballing raw API output yourself.

`run_code`'s sandbox CAN reach the network — prefer it over `web_search` whenever a public, unauthenticated API serves the exact data the query needs: an API call returns the current answer, while `web_search` only surfaces articles describing the topic (a stale snapshot). Still use `web_search`/`fetch_url` for discovery, research, or reading a specific known page — see "Tool Selection Strategy" below.

Do NOT describe file contents in markdown as a substitute for producing the file. Write a complete program with `run_code`, have it write the output file(s) to the working directory (or `$OUTPUT_DIR`), and let the resulting `artifacts` be delivered automatically — do not print file paths in your answer.
"""

_CODE_EXECUTION_STEERING_COMPOSED_NO_NETWORK = """
## Code Execution (MANDATORY for file generation; NO network access)

When a task requires ANY of the following, you MUST delegate to `coding_agent` — never answer in text alone, and never claim you cannot do it:
- Generating a downloadable file (PDF, DOCX, XLSX, CSV, image, chart, presentation).
- Data processing that produces structured output (tables, aggregations, transformations) beyond what a single tool call returns.
- Any computation the user explicitly asks you to "run", "execute", or "code".

`coding_agent`'s sandbox has NO network access, ever. For any task needing live/external data (a REST API, a webpage, search results, ...), delegate to {web_ref} FIRST to fetch that data, then include the already-fetched data directly in the goal you give `coding_agent` — it cannot make network calls itself.

Do NOT describe file contents in markdown as a substitute for producing the file. Give `coding_agent` a goal stating exactly what to compute/produce and what the output file should contain — it writes and runs a complete program, and the resulting file is delivered automatically as an `artifact`; do not print file paths in your answer.
"""

_CODE_EXECUTION_STEERING_COMPOSED_NETWORK = """
## Code Execution (MANDATORY for file generation; network access available)

When a task requires ANY of the following, you MUST delegate to `coding_agent` — never answer in text alone, and never claim you cannot do it:
- Generating a downloadable file (PDF, DOCX, XLSX, CSV, image, chart, presentation).
- Data processing that produces structured output (tables, aggregations, transformations) beyond what a single tool call returns.
- Any computation the user explicitly asks you to "run", "execute", or "code".
- Getting live, structured data from a well-known public REST API and analyzing/filtering/aggregating it — give `coding_agent` a goal to call the API directly (e.g. with `requests`/`httpx`/`fetch`) and process the response, rather than eyeballing raw API output yourself.

`coding_agent`'s sandbox CAN reach the network — prefer delegating to it over {web_ref} whenever a public, unauthenticated API serves the exact data the query needs: an API call returns the current answer, while {web_ref} only surfaces articles describing the topic (a stale snapshot). Still delegate to {web_ref} for discovery, research, or reading a specific known page — see "Tool Selection Strategy" below.

Do NOT describe file contents in markdown as a substitute for producing the file. Give `coding_agent` a goal stating exactly what to compute/produce and what the output file should contain — it writes and runs a complete program, and the resulting file is delivered automatically as an `artifact`; do not print file paths in your answer.
"""

_TOOL_SELECTION_STRATEGY_HEADER = "\n## Tool Selection Strategy\n\n"

_TOOL_SELECTION_MATCH_SOURCE_LIVE_API = (
    "- Match the tool to the data source the question actually needs: a live, "
    "structured, real-time fact that a well-known public API serves is best "
    "answered by writing a `run_code` program that calls that API directly and "
    "analyzes the response; `web_search` is best for discovery, opinions, and "
    "questions with no single authoritative source; `fetch_url` is best for "
    "reading one specific already-known page.\n"
    "- Prefer live data over a stale snapshot: if a public REST API can give "
    "you the current, exact answer, prefer calling it from `run_code` over "
    "`web_search` — search results describe the data, an API call IS the data.\n"
)
_TOOL_SELECTION_MATCH_SOURCE_NO_API = (
    "- Match the tool to the data source the question actually needs: "
    "`web_search` for discovery, opinions, and current public information; "
    "`fetch_url` for reading one specific already-known page.\n"
)


def _tool_selection_match_source_live_api_composed(code_tool: str, web_ref: str) -> str:
    call_action = (
        f"delegating to `{code_tool}` with a goal to call that API directly and analyze the response"
        if code_tool != "run_code"
        else f"writing a `{code_tool}` program that calls that API directly and analyzes the response"
    )
    prefer_action = f"delegating to `{code_tool}`" if code_tool != "run_code" else f"calling it from `{code_tool}`"
    return (
        "- Match the tool to the data source the question actually needs: a live, "
        f"structured, real-time fact that a well-known public API serves is best answered by {call_action}; "
        f"{web_ref} is best for discovery, opinions, and questions with no single authoritative source, or "
        "for reading one specific already-known page.\n"
        "- Prefer live data over a stale snapshot: if a public REST API can give you the current, exact "
        f"answer, prefer {prefer_action} over {web_ref} — search results describe the data, an API call IS "
        "the data.\n"
    )


def _tool_selection_match_source_no_api_composed(web_ref: str) -> str:
    return (
        "- Match the tool to the data source the question actually needs: "
        f"{web_ref} for discovery, opinions, current public information, and reading one specific "
        "already-known page.\n"
    )


_TOOL_SELECTION_ARBITRATION = (
    "- When it is genuinely unclear which available tool is the better fit, or "
    "two tools would return complementary information, call BOTH in parallel "
    "rather than guessing — then either combine their results into one answer "
    "or prefer the more authoritative/live source, stating any discrepancy "
    "rather than silently picking one.\n"
)

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

        tool_names = spec.tool_names or []
        parts.append(self._build_tool_reference_section(tool_names, runtime))

        has_retrieval = bool(state.get("final_results"))
        has_attachments = bool(state.get("attachments"))
        has_web_search = bool(state.get("web_search_config"))
        web_ref = _web_reference(tool_names)

        if has_retrieval or has_attachments:
            parts.append(_CITATION_RULES)
        if has_web_search:
            parts.append(_WEB_SEARCH_RULES_COMPOSED if "web_agent" in tool_names else _WEB_SEARCH_RULES)

        parts.append(self._build_hybrid_strategy_section(state, tool_names, has_web_search=has_web_search))

        code_tool = _composed_code_tool(tool_names)
        sandbox_networked = code_tool is not None and sandbox_network_enabled()
        if code_tool is not None:
            composed_code = code_tool != "run_code"
            if composed_code:
                template = (
                    _CODE_EXECUTION_STEERING_COMPOSED_NETWORK if sandbox_networked
                    else _CODE_EXECUTION_STEERING_COMPOSED_NO_NETWORK
                )
                steering = template.format(web_ref=web_ref)
            else:
                steering = (
                    _CODE_EXECUTION_STEERING_NETWORK if sandbox_networked
                    else _CODE_EXECUTION_STEERING_NO_NETWORK
                )
            parts.append(steering)

        if has_web_search:
            # `sandbox_networked` (not just `code_tool is not None`) gates the
            # "call a live API from run_code"/"coding_agent" bullet — that
            # guidance would contradict the NO_NETWORK steering above if the
            # sandbox has no network access this request.
            parts.append(self._build_tool_selection_section(
                code_tool=code_tool if sandbox_networked else None, web_ref=web_ref,
            ))

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
    def _build_tool_reference_section(tool_names: list[str], runtime: AgentRuntime) -> str:
        """"Available Tools" section rendered from what the agent was
        ACTUALLY granted this turn (`spec.tool_names`, resolved against
        `runtime.tool_registry`) — never the legacy flat ChatState tool
        list, which described every registered tool regardless of whether
        domain-agent composition (`domain_agents.py`) had since narrowed
        the agent's real grant to a handful of delegates + residual tools.
        Per-tool guidance (`llm_description`/when-to-use/when-not-to-use)
        is already on `Tool.description` (see `tool_adapter.py`), so no
        separate cache/formatting layer is needed — this just renders it."""
        lines: list[str] = []
        for name in tool_names:
            try:
                tool = runtime.tool_registry.resolve_by_name(name)
            except ToolNotFoundError:
                continue
            lines.append(f"### {name}")
            description = tool.description or tool.short_description
            if description:
                lines.append(f"  {description}")
            params = tool.parameters
            if params:
                lines.append("")
                lines.append("  **Parameters:**")
                for param in params:
                    required_marker = "**required**" if param.required else "optional"
                    param_type = param.type.value.upper()
                    if param.description:
                        lines.append(f"  - `{param.name}` ({required_marker}): {param.description} [{param_type}]")
                    else:
                        lines.append(f"  - `{param.name}` ({required_marker}) [{param_type}]")
            lines.append("")
        if not lines:
            return ""
        return _TOOL_REFERENCE_HEADER + "\n".join(lines)

    @staticmethod
    def _build_hybrid_strategy_section(
        state: dict[str, Any], tool_names: list[str], *, has_web_search: bool,
    ) -> str:
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
            internal_ref = _internal_knowledge_reference(tool_names)
            web_ref = _web_reference(tool_names)
            section = _hybrid_strategy_header(internal_ref) + _hybrid_strategy_rules(internal_ref)
            if has_web_search:
                section += _hybrid_web_search_note(web_ref)
            section += f"\n### How to merge hybrid results:\n1. Call the appropriate tools ({internal_ref} + service API"
            if has_web_search:
                section += f" + {web_ref} as needed"
            section += (
                ") — IN PARALLEL where possible.\n"
                "2. Present a unified answer combining insights from all sources.\n"
                f"3. For internal knowledge ({internal_ref}): cite as [source](ref1) using the Citation ID from the context blocks.\n"
            )
            if has_web_search:
                section += (
                    f"4. For {web_ref} results: cite as [source](URL/citation id) using the URL/citation id.\n"
                    "5. Clearly attribute live API data (e.g., \"According to your Outlook calendar...\" or \"From Confluence...\").\n"
                )
            else:
                section += "4. Clearly attribute live API data (e.g., \"According to your Outlook calendar...\" or \"From Confluence...\").\n"
            return section

        if has_service_tools and not has_knowledge:
            return _SERVICE_ONLY_STRATEGY

        return ""

    @staticmethod
    def _build_tool_selection_section(*, code_tool: str | None, web_ref: str) -> str:
        """Generic tool-arbitration guidance: distinct from
        `_build_hybrid_strategy_section` above (which only covers internal
        retrieval vs. connector service tools) — this covers picking
        between the web-research surface (`web_search`/`fetch_url` or
        composed `web_agent`) and, when a networked code-execution surface
        is available, calling a live API directly from it."""
        section = _TOOL_SELECTION_STRATEGY_HEADER
        if code_tool is None:
            section += (
                _tool_selection_match_source_no_api_composed(web_ref) if web_ref == "`web_agent`"
                else _TOOL_SELECTION_MATCH_SOURCE_NO_API
            )
        elif code_tool == "run_code" and web_ref != "`web_agent`":
            section += _TOOL_SELECTION_MATCH_SOURCE_LIVE_API
        else:
            section += _tool_selection_match_source_live_api_composed(code_tool, web_ref)
        section += _TOOL_SELECTION_ARBITRATION
        return section


__all__ = ["PipesHubPromptBuilder"]
