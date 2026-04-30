"""
Tool Router - Groups and assigns tools to sub-agents by domain.

Ensures each sub-agent only sees tools relevant to its task,
reducing LLM confusion and improving accuracy.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.modules.agents.tool_domain import DOMAIN_ALIASES, derive_tool_domain

if TYPE_CHECKING:
    from app.modules.agents.deep.state import DeepAgentState, SubAgentTask

logger = logging.getLogger(__name__)

# Utility tools always included for every sub-agent
UTILITY_DOMAINS = {"calculator", "datetime", "utility"}


# Backwards-compatible alias for any in-tree consumers that still import
# `_DOMAIN_ALIASES` from this module — the canonical map now lives in
# `app.modules.agents.tool_domain`.
_DOMAIN_ALIASES = DOMAIN_ALIASES

# Max description length per tool in orchestrator prompt
_MAX_TOOL_DESC_LEN = 150
# Max param description length
_MAX_PARAM_DESC_LEN = 80
# When a domain has more tools than this, filter by task relevance
_MAX_TOOLS_PER_TASK = 8


def group_tools_by_domain(state: DeepAgentState) -> dict[str, list[str]]:
    """
    Group available tools by their domain (app name).

    Loads tools WITH schemas ONCE and caches them in state for reuse by
    sub-agents (via get_tools_for_sub_agent) and the orchestrator prompt
    (via build_domain_description).

    Returns:
        {
            "outlook": ["outlook.search_events", "outlook.create_event", ...],
            "slack": ["slack.send_message", ...],
            "retrieval": ["retrieval.search_internal_knowledge"],
            "utility": ["calculator.calculate", "datetime.get_current_datetime"],
        }
    """
    from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

    log = state.get("logger", logger)

    # Load StructuredTool objects ONCE — these have sanitized names + args_schema
    try:
        schema_tools = get_agent_tools_with_schemas(state)
    except Exception as e:
        log.error("Failed to load tools with schemas: %s", e)
        schema_tools = []

    # Cache the full list in state so sub-agents can reuse without re-loading
    state["cached_structured_tools"] = schema_tools

    # Build groups using the ORIGINAL (dot-separated) names for domain extraction,
    # and build the schema map for build_domain_description
    groups: dict[str, list[str]] = {}
    schema_tool_map: dict[str, Any] = {}

    for tool in schema_tools:
        sanitized_name = getattr(tool, "name", "")
        original_name = getattr(tool, "_original_name", sanitized_name)

        # Single source of truth — see app.modules.agents.tool_domain.
        domain, _ = derive_tool_domain(tool)

        # Tool-router-specific: collapse known utility domains so they
        # share one bucket and aren't planned as standalone sub-agent tasks.
        if domain in UTILITY_DOMAINS:
            domain = "utility"

        groups.setdefault(domain, []).append(original_name)

        # Map both original and sanitized names to the StructuredTool
        schema_tool_map[original_name] = tool
        if sanitized_name != original_name:
            schema_tool_map[sanitized_name] = tool

    state["schema_tool_map"] = schema_tool_map

    log.debug(
        "Tool domains: %s",
        {d: len(t) for d, t in groups.items()},
    )

    return groups


def assign_tools_to_tasks(
    tasks: list[SubAgentTask],
    tool_groups: dict[str, list[str]],
    state: DeepAgentState,
) -> list[SubAgentTask]:
    """
    Assign relevant tools to each task based on its domain.

    Each task should have exactly ONE domain (enforced by orchestrator prompt).
    This function:
    1. Maps the task's domain to actual tool names
    2. Filters tools by relevance to the task description (reduces LLM confusion)
    3. Always includes utility tools
    4. Includes retrieval tools if knowledge is configured and domain is retrieval
    """
    log = state.get("logger", logger)
    has_knowledge = state.get("has_knowledge", False)

    # Get schema tool map for description-based filtering
    schema_tool_map = state.get("schema_tool_map", {})

    for task in tasks:
        task_domains = {d.lower() for d in task.get("domains", [])}
        assigned: list[str] = []

        # Add domain-specific tools
        # Multi-step tasks get ALL domain tools (each step may need different
        # tools, and keyword-based filtering against the combined description
        # can incorrectly exclude tools needed by individual steps).
        is_multi_step = bool(task.get("multi_step") and task.get("sub_steps"))

        for domain in task_domains:
            normalized = _DOMAIN_ALIASES.get(domain, domain)
            domain_tools = []
            if normalized in tool_groups:
                domain_tools = tool_groups[normalized]
            elif domain in tool_groups:
                domain_tools = tool_groups[domain]

            # Filter tools by relevance when a domain has many tools
            # Skip filtering for multi-step tasks — they need the full set
            if not is_multi_step and len(domain_tools) > _MAX_TOOLS_PER_TASK:
                filtered = _filter_tools_by_relevance(
                    domain_tools, task, schema_tool_map, log,
                )
                assigned.extend(filtered)
            else:
                assigned.extend(domain_tools)

        # Always add utility tools
        if "utility" in tool_groups:
            assigned.extend(tool_groups["utility"])

        # Add retrieval if domain requests it OR if it's a knowledge task
        if has_knowledge and ("retrieval" in task_domains or _is_knowledge_task(task)) and "retrieval" in tool_groups:
            assigned.extend(tool_groups["retrieval"])

        # Add knowledgehub if domain requests it OR if it's a knowledge listing task
        if has_knowledge and ("knowledgehub" in task_domains) and "knowledgehub" in tool_groups:
            assigned.extend(tool_groups["knowledgehub"])

        # De-duplicate while preserving order
        seen: set[str] = set()
        unique_tools: list[str] = []
        for name in assigned:
            if name not in seen:
                seen.add(name)
                unique_tools.append(name)

        task["tools"] = unique_tools
        log.debug(
            "Task %s: assigned %d tools from domains %s",
            task.get("task_id"),
            len(unique_tools),
            task_domains,
        )

    return tasks


def get_tools_for_sub_agent(
    assigned_tool_names: list[str],
    state: DeepAgentState,
) -> list:
    """
    Get StructuredTool objects for a sub-agent, filtered to its assigned tools.

    Uses cached tools from group_tools_by_domain() to avoid redundant loading.
    Returns LangChain StructuredTool objects ready for create_agent().
    These include args_schema (Pydantic models) so the LLM knows
    exact parameter names, types, and which are required.
    """
    # Use cached tools from group_tools_by_domain (loaded once in orchestrator)
    all_tools = state.get("cached_structured_tools")
    if not all_tools:
        # Fallback: load if cache is missing (shouldn't happen in normal flow)
        from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas
        log = state.get("logger", logger)
        log.warning("No cached structured tools — loading fresh (this should not happen)")
        all_tools = get_agent_tools_with_schemas(state)
        state["cached_structured_tools"] = all_tools

    assigned_set = set(assigned_tool_names)

    # Match by both original name (dots) and sanitized name (underscores)
    filtered = []
    for tool in all_tools:
        sanitized_name = getattr(tool, "name", "")
        original_name = getattr(tool, "_original_name", sanitized_name)

        if original_name in assigned_set or sanitized_name in assigned_set:
            filtered.append(tool)
        elif sanitized_name.replace("_", ".") in assigned_set:
            filtered.append(tool)

    return filtered


def build_domain_description(
    tool_groups: dict[str, list[str]],
    state: DeepAgentState | None = None,
) -> str:
    """
    Build a detailed description of available tool domains for the
    orchestrator prompt, including tool descriptions AND parameter schemas.

    This gives the orchestrator LLM the information it needs to:
    1. Understand what each tool can do
    2. Know what parameters are required/optional
    3. Make intelligent decomposition decisions
    4. Ask the user for missing required parameters
    """
    # Get schema tool map stashed by group_tools_by_domain
    schema_tool_map: dict[str, Any] = {}
    if state:
        schema_tool_map = state.get("schema_tool_map", {})

    lines = []
    for domain, tools in sorted(tool_groups.items()):
        if domain == "utility":
            continue  # Don't clutter with utility tools

        lines.append(f"### {domain}")

        for tool_name in tools[:12]:
            short_name = tool_name.split(".", 1)[1] if "." in tool_name else tool_name

            # Look up the StructuredTool with schema
            schema_tool = schema_tool_map.get(tool_name)
            if not schema_tool:
                # Try sanitized name
                schema_tool = schema_tool_map.get(tool_name.replace(".", "_"))

            if schema_tool:
                desc = getattr(schema_tool, "description", "")
                if desc:
                    desc_short = desc[:_MAX_TOOL_DESC_LEN] + "..." if len(desc) > _MAX_TOOL_DESC_LEN else desc
                    lines.append(f"  - **`{short_name}`**: {desc_short}")
                else:
                    lines.append(f"  - **`{short_name}`**")

                # Extract and format parameter schema
                params_text = _format_tool_params(schema_tool)
                if params_text:
                    lines.append(params_text)
            else:
                lines.append(f"  - **`{short_name}`**")

        if len(tools) > 12:
            lines.append(f"  - ... and {len(tools) - 12} more tools")

        lines.append("")  # blank line between domains

    if not lines:
        lines.append("No external tool domains configured.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Parameter schema formatting
# ---------------------------------------------------------------------------

def _format_tool_params(tool: object) -> str:
    """
    Format the parameter schema of a StructuredTool for the orchestrator prompt.

    Returns a compact multi-line string showing required and optional params.
    """
    schema = getattr(tool, "args_schema", None)
    if not schema:
        return ""

    params = _extract_params(schema)
    if not params:
        return ""

    parts = []
    for param_name, info in params.items():
        required_tag = "**required**" if info["required"] else "optional"
        ptype = info["type"].upper()
        desc = info["description"]
        if desc:
            desc_short = desc[:_MAX_PARAM_DESC_LEN] + "..." if len(desc) > _MAX_PARAM_DESC_LEN else desc
            parts.append(f"      - `{param_name}` ({required_tag}, {ptype}): {desc_short}")
        else:
            parts.append(f"      - `{param_name}` ({required_tag}, {ptype})")

    if parts:
        return "    Parameters:\n" + "\n".join(parts)
    return ""


def _extract_params(
    schema: dict[str, Any] | type,
) -> dict[str, dict[str, Any]]:
    """
    Extract parameter info from a Pydantic schema (v1 or v2) or dict schema.

    Returns:
        {
            "param_name": {
                "type": "string",
                "required": True,
                "description": "..."
            }
        }
    """
    try:
        # Pydantic v2
        if hasattr(schema, "model_fields"):
            fields = schema.model_fields
            required_fields = getattr(schema, "__required_fields__", set())
            params = {}
            for name, field_info in fields.items():
                is_required = (
                    name in required_fields
                    or (hasattr(field_info, "is_required") and field_info.is_required())
                    or (not hasattr(field_info, "default") or field_info.default is None)
                )
                params[name] = {
                    "required": is_required,
                    "description": getattr(field_info, "description", "") or "",
                    "type": _get_type_name(field_info),
                }
            return params

        # Pydantic v1
        if hasattr(schema, "__fields__"):
            params = {}
            for name, field_info in schema.__fields__.items():
                params[name] = {
                    "required": field_info.required,
                    "description": getattr(getattr(field_info, "field_info", None), "description", "") or "",
                    "type": _get_type_name_v1(field_info),
                }
            return params

        # Dict/JSON schema
        if isinstance(schema, dict):
            properties = schema.get("properties", {})
            required = set(schema.get("required", []))
            params = {}
            for name, prop in properties.items():
                params[name] = {
                    "required": name in required,
                    "description": prop.get("description", ""),
                    "type": prop.get("type", "any"),
                }
            return params

    except Exception:
        pass

    return {}


def _get_type_name(field_info: object) -> str:
    """Get type name from Pydantic v2 field."""
    try:
        from typing import Union

        annotation = field_info.annotation
        if hasattr(annotation, "__origin__") and annotation.__origin__ is Union:
            args = [a for a in annotation.__args__ if a is not type(None)]
            if args:
                annotation = args[0]
        if hasattr(annotation, "__name__"):
            return annotation.__name__.lower()
        return str(annotation).lower().replace("<class '", "").replace("'>", "")
    except Exception:
        return "any"


def _get_type_name_v1(field_info: object) -> str:
    """Get type name from Pydantic v1 field."""
    try:
        from typing import Union

        type_ = field_info.outer_type_
        if hasattr(type_, "__origin__") and type_.__origin__ is Union:
            args = [a for a in type_.__args__ if a is not type(None)]
            if args:
                type_ = args[0]
        if hasattr(type_, "__name__"):
            return type_.__name__.lower()
        return str(type_).lower()
    except Exception:
        return "any"


def _filter_tools_by_relevance(
    domain_tools: list[str],
    task: SubAgentTask,
    schema_tool_map: dict[str, Any],
    log: logging.Logger,
) -> list[str]:
    """
    Filter tools within a large domain to only those relevant to the task.

    Uses keyword matching between the task description and tool names/descriptions
    to reduce the number of tools sent to the sub-agent. This prevents the LLM
    from making erroneous calls to irrelevant tools.

    Always returns exactly _MAX_TOOLS_PER_TASK tools (top-scored), capped
    to prevent tool overload in the sub-agent's context.
    """
    desc_lower = (task.get("description") or "").lower()
    # Extract multi-word phrases for bonus scoring (e.g., "recurring events")
    desc_words = [w for w in desc_lower.split() if len(w) > 2]

    # Score each tool by relevance to the task description
    scored: list[tuple] = []
    for tool_name in domain_tools:
        score = 0
        # Extract the action part (e.g., "get_recurring_events_ending" from "outlook.get_recurring_events_ending")
        action = tool_name.split(".", 1)[1] if "." in tool_name else tool_name
        action_words = set(action.lower().replace("_", " ").split())
        # Also keep the full action as a phrase for multi-word matching
        action_phrase = action.lower().replace("_", " ")

        # Score based on action name keywords in task description
        # Higher weight for specific/uncommon words (length > 5)
        for word in action_words:
            if len(word) <= 2:
                continue
            if word in desc_lower:
                # Specific words (longer) get higher scores
                score += 4 if len(word) > 5 else 2

        # Bonus: consecutive word matches (e.g., "recurring events" matches tool "get_recurring_events_ending")
        for i in range(len(desc_words) - 1):
            bigram = f"{desc_words[i]} {desc_words[i + 1]}"
            if bigram in action_phrase:
                score += 6  # Strong signal — multi-word match

        # Score based on tool description keywords
        schema_tool = schema_tool_map.get(tool_name)
        if schema_tool:
            tool_desc = (getattr(schema_tool, "description", "") or "").lower()
            for word in desc_words:
                if len(word) > 3 and word in tool_desc:
                    score += 1

        scored.append((tool_name, score))

    # Sort by score descending, then take top tools
    scored.sort(key=lambda x: x[1], reverse=True)

    # Take top _MAX_TOOLS_PER_TASK tools — always cap to prevent tool overload
    relevant = [name for name, s in scored[:_MAX_TOOLS_PER_TASK]]

    # If we have fewer than _MAX_TOOLS_PER_TASK with score > 0,
    # still fill up to the limit from remaining tools
    if len([s for _, s in scored[:_MAX_TOOLS_PER_TASK] if s > 0]) == 0:
        # No keyword matches at all — just return top N by original order
        relevant = domain_tools[:_MAX_TOOLS_PER_TASK]

    if len(relevant) < len(domain_tools):
        top_scores = [(n.split(".")[-1], s) for n, s in scored[:5]]
        log.info(
            "Task %s: filtered %d → %d tools (top: %s)",
            task.get("task_id"),
            len(domain_tools),
            len(relevant),
            top_scores,
        )

    return relevant


def _is_knowledge_task(task: SubAgentTask) -> bool:
    """Check if a task likely needs retrieval based on its description."""
    desc = task.get("description", "").lower()
    knowledge_indicators = [
        "search knowledge", "find information", "look up", "what is",
        "internal knowledge", "retrieval", "knowledge base",
        "search for", "find documents", "search documents",
    ]
    return any(indicator in desc for indicator in knowledge_indicators)
