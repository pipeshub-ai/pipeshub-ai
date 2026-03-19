"""
Capability Summary Builder - Shared across all agent modes.

Lists configured knowledge sources and user-configured service tools.
Internal utility tools (calculator, date_calculator, etc.) are excluded
automatically — only tools from the agent's configured toolsets are shown.
Adding new internal tools requires no changes here.
"""

from typing import Any


def build_capability_summary(state: dict[str, Any]) -> str:
    """
    Build a capability summary for the LLM to answer "what can you do?" questions.

    Shows:
    - Configured knowledge sources (from agent_knowledge)
    - User-configured service tools grouped by domain (from state["tools"] / agent_toolsets)
    - Retrieval capability (when knowledge is configured)

    Internal utility tools (calculator, date_calculator, etc.) are NOT shown —
    they are implementation details, not user-facing capabilities.
    """
    parts: list[str] = ["## Capability Summary", ""]

    has_knowledge = state.get("has_knowledge", False)

    _build_knowledge_section(state=state, has_knowledge=has_knowledge, parts=parts)
    _build_actions_section(state=state, has_knowledge=has_knowledge, parts=parts)

    parts.append(
        "When users ask about your capabilities, what you can do, what tools or "
        "knowledge you have, answer based on this summary. Do not call tools to "
        "answer capability questions."
    )

    return "\n".join(parts)


def _build_knowledge_section(
    state: dict[str, Any],
    *,
    has_knowledge: bool,
    parts: list[str],
) -> None:
    """Append knowledge sources section to parts."""
    parts.append("### Knowledge Sources")

    if not has_knowledge:
        parts.append("- No knowledge sources configured")
        parts.append("")
        return

    knowledge_list = state.get("agent_knowledge", []) or []
    if knowledge_list:
        for kb in knowledge_list:
            if not isinstance(kb, dict):
                continue
            kb_name = kb.get("name") or kb.get("displayName") or "Unnamed"
            kb_type = kb.get("type", "")
            connector_id = kb.get("connectorId", "")
            display_type = "Collection" if kb_type == "KB" else kb_type
            is_kb = kb_type == "KB"

            label = f"- {kb_name} ({display_type})" if display_type else f"- {kb_name}"

            # Extract record group IDs from filters
            filters_data = kb.get("filters", {})
            if isinstance(filters_data, str):
                import json as _json
                try:
                    filters_data = _json.loads(filters_data)
                except (ValueError, _json.JSONDecodeError):
                    filters_data = {}
            record_groups = filters_data.get("recordGroups", []) if isinstance(filters_data, dict) else []

            # Build browse and filter hints:
            # - KB sources: use record group IDs directly (connector ID
            #   returns ALL KBs, not just configured ones)
            # - App connectors: use connector ID with parent_type=app
            browse_hints: list[str] = []
            if is_kb and record_groups:
                for rg_id in record_groups:
                    browse_hints.append(
                        f"list files with `list_files(parent_id=\"{rg_id}\", parent_type=\"recordGroup\")`"
                    )
                    browse_hints.append(
                        f"filter search to this KB with `record_group_ids=[\"{rg_id}\"]`"
                    )
            elif connector_id:
                browse_hints.append(
                    f"browse with `list_files(parent_id=\"{connector_id}\", parent_type=\"app\")`"
                )
                browse_hints.append(
                    f"filter search to this connector with `connector_ids=[\"{connector_id}\"]`"
                )

            if browse_hints:
                label += " — " + "; ".join(browse_hints)
            parts.append(label)
    else:
        # has_knowledge is True but no detailed metadata available
        parts.append("- Internal knowledge sources configured")

    parts.append("- Can search indexed documents, policies, and organizational information")

    # If agent has BOTH knowledge sources and service tools for the same app,
    # guide the LLM to use both for comprehensive results
    service_tools = state.get("tools") or []
    if knowledge_list and service_tools:
        # Check if any knowledge source overlaps with a service tool domain
        knowledge_types = {
            (kb.get("type", "")).lower()
            for kb in knowledge_list
            if isinstance(kb, dict)
        }
        tool_domains = {
            t.split(".", 1)[0].lower()
            for t in service_tools
            if isinstance(t, str) and "." in t
        }
        if knowledge_types & tool_domains or knowledge_types - {"kb", ""}:
            parts.append(
                "\n**IMPORTANT**: When listing or browsing files/documents, use BOTH:\n"
                "  - `knowledge_hub.list_files` for indexed files with metadata from the Knowledge Hub\n"
                "  - Service search/list tools for live data directly from connectors\n"
                "  This gives the most complete picture."
            )

    parts.append("")


def _build_actions_section(
    state: dict[str, Any],
    *,
    has_knowledge: bool,
    parts: list[str],
) -> None:
    """Append available actions section to parts.

    Only shows user-configured service tools (from toolsets) and retrieval.
    Internal utility tools are excluded automatically.
    """
    service_domains = _get_service_tool_domains(state)

    # Add knowledge tools as visible actions when knowledge is configured
    if has_knowledge:
        service_domains.setdefault("retrieval", []).append(
            "retrieval.search_internal_knowledge",
        )
        service_domains.setdefault("knowledge_hub", []).append(
            "knowledge_hub.list_files",
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


def _get_service_tool_domains(state: dict[str, Any]) -> dict[str, list[str]]:
    """
    Get user-configured service tools grouped by domain.

    Only returns tools from the agent's configured toolsets — NOT internal
    utility tools. This means new internal tools (calculator, date_calculator,
    web_search, etc.) are automatically excluded without code changes.

    Sources (in priority order):
    1. state["tools"] — canonical flat list from build_initial_state
    2. state["agent_toolsets"] — raw toolset metadata fallback
    """
    domains: dict[str, list[str]] = {}

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

        tool_names: list[str] = []

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
