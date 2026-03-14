"""
Capability Summary Builder - Shared across all agent modes.

Lists configured knowledge sources and user-configured service tools.
Internal utility tools (calculator, date_calculator, etc.) are excluded
automatically — only tools from the agent's configured toolsets are shown.
Adding new internal tools requires no changes here.
"""

from typing import Any, Dict, List


def build_capability_summary(state: Dict[str, Any]) -> str:
    """
    Build a capability summary for the LLM to answer "what can you do?" questions.

    Shows:
    - Configured knowledge sources (from agent_knowledge)
    - User-configured service tools grouped by domain (from state["tools"] / agent_toolsets)
    - Retrieval capability (when knowledge is configured)

    Internal utility tools (calculator, date_calculator, etc.) are NOT shown —
    they are implementation details, not user-facing capabilities.
    """
    parts: List[str] = ["## Capability Summary", ""]

    has_knowledge = bool(
        state.get("kb") or state.get("apps") or state.get("agent_knowledge")
    )

    _build_knowledge_section(state, has_knowledge, parts)
    _build_actions_section(state, has_knowledge, parts)

    parts.append(
        "When users ask about your capabilities, what you can do, what tools or "
        "knowledge you have, answer based on this summary. Do not call tools to "
        "answer capability questions."
    )

    return "\n".join(parts)


def _build_knowledge_section(
    state: Dict[str, Any],
    has_knowledge: bool,
    parts: List[str],
) -> None:
    """Append knowledge sources section to parts."""
    parts.append("### Knowledge Sources")

    if not has_knowledge:
        parts.append("- No knowledge sources configured")
        parts.append("")
        return

    agent_knowledge = state.get("agent_knowledge", []) or []
    if agent_knowledge:
        for kb in agent_knowledge:
            if not isinstance(kb, dict):
                continue
            kb_name = kb.get("name", "Unnamed")
            kb_type = kb.get("type", "")
            if kb_type == "KB":
                kb_type = "Collection"
            if kb_type:
                parts.append(f"- {kb_name} ({kb_type})")
            else:
                parts.append(f"- {kb_name}")
    else:
        parts.append("- Internal knowledge base configured")

    parts.append("- Can search indexed documents, policies, and organizational information")
    parts.append("")


def _build_actions_section(
    state: Dict[str, Any],
    has_knowledge: bool,
    parts: List[str],
) -> None:
    """Append available actions section to parts.

    Only shows user-configured service tools (from toolsets) and retrieval.
    Internal utility tools are excluded automatically.
    """
    service_domains = _get_service_tool_domains(state)

    # Add retrieval as a visible action when knowledge is configured
    if has_knowledge:
        service_domains.setdefault("retrieval", []).append(
            "retrieval.search_internal_knowledge"
        )

    parts.append("### Available Actions")

    if not service_domains:
        parts.append("- No tools configured")
        parts.append("")
        return

    for domain, tool_names in sorted(service_domains.items()):
        display = domain.replace("_", " ").title()
        actions = [
            (t.split(".", 1)[1] if "." in t else t).replace("_", " ")
            for t in tool_names
        ]
        parts.append(f"- **{display}**: {', '.join(actions)}")

    parts.append("")


def _get_service_tool_domains(state: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    Get user-configured service tools grouped by domain.

    Only returns tools from the agent's configured toolsets — NOT internal
    utility tools. This means new internal tools (calculator, date_calculator,
    web_search, etc.) are automatically excluded without code changes.

    Sources (in priority order):
    1. state["tools"] — canonical flat list from build_initial_state
    2. state["agent_toolsets"] — raw toolset metadata fallback
    """
    domains: Dict[str, List[str]] = {}

    # Primary: state["tools"] — populated by build_initial_state from toolsets
    for tool_name in (state.get("tools") or []):
        if not isinstance(tool_name, str) or "." not in tool_name:
            continue
        domain = tool_name.split(".", 1)[0]
        domains.setdefault(domain, []).append(tool_name)

    if domains:
        return domains

    # Fallback: agent_toolsets metadata from graph DB
    for toolset in (state.get("agent_toolsets") or []):
        if not isinstance(toolset, dict):
            continue
        toolset_name = toolset.get("name", "")
        if not toolset_name:
            continue

        tool_names: List[str] = []

        for t in (toolset.get("tools") or []):
            if isinstance(t, dict):
                name = t.get("fullName") or f"{toolset_name}.{t.get('name', '')}"
                tool_names.append(name)

        if not tool_names:
            for t in (toolset.get("selectedTools") or []):
                if isinstance(t, str):
                    name = t if "." in t else f"{toolset_name}.{t}"
                    tool_names.append(name)

        if tool_names:
            domains.setdefault(toolset_name, []).extend(tool_names)

    return domains
