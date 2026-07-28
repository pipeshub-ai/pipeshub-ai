"""Tool-description formatting for the ReAct/planner prompt, extracted
from `modules/agents/qna/nodes.py` (Phase 0 of the agent-loop migration).
"""

from __future__ import annotations

import logging
from typing import Any, Union

from app.modules.agents.qna.chat_state import ChatState

# Tool description caching
_tool_description_cache: dict[str, str] = {}


def _get_cached_tool_descriptions(state: ChatState, log: logging.Logger) -> str:
    """Get tool descriptions with caching"""
    org_id = state.get("org_id", "default")
    agent_toolsets = state.get("agent_toolsets", [])
    llm = state.get("llm")

    has_knowledge = state.get("has_knowledge", False)

    from app.modules.agents.qna.tool_system import (
        _requires_sanitized_tool_names,
        get_agent_tools_with_schemas,
    )

    llm_type = "anthropic" if llm and _requires_sanitized_tool_names(llm) else "other"
    toolset_names = sorted([ts.get("name", "") for ts in agent_toolsets if isinstance(ts, dict)])
    # Include has_knowledge in cache key — a change in knowledge config must bust the cache
    cache_key = f"{org_id}_{hash(tuple(toolset_names))}_{llm_type}_{has_knowledge}"

    if cache_key in _tool_description_cache:
        return _tool_description_cache[cache_key]

    try:
        tools = get_agent_tools_with_schemas(state)
        if not tools:
            fallback_name = "retrieval_search_internal_knowledge" if llm_type == "anthropic" else "retrieval.search_internal_knowledge"
            return f"### {fallback_name}\n  ✅ Use: Questions about company info, policies\n  ❌ Don't: External API calls"

        result = _format_tool_descriptions(tools, log)
        _tool_description_cache[cache_key] = result
        return result

    except Exception as e:
        log.warning(f"Tool load failed: {e}")
        return "### retrieval.search_internal_knowledge\n  ✅ Use: Search company knowledge"


def _get_field_type_name(field_info: object) -> str:
    """Get type name from Pydantic v2 field"""
    try:
        annotation = field_info.annotation

        # Handle Optional types
        if hasattr(annotation, '__origin__'):
            origin = annotation.__origin__
            if origin is Union:
                # Get non-None type
                args = [arg for arg in annotation.__args__ if arg is not type(None)]
                if args:
                    annotation = args[0]

        # Get type name
        if hasattr(annotation, '__name__'):
            return annotation.__name__.lower()
        else:
            type_str = str(annotation).lower()
            # Clean up common type representations
            return type_str.replace('<class ', '').replace('>', '').replace("'", "")
    except Exception:
        return "any"


def _get_field_type_name_v1(field_info: object) -> str:
    """Get type name from Pydantic v1 field"""
    try:
        type_ = field_info.outer_type_

        # Handle Optional
        if hasattr(type_, '__origin__') and type_.__origin__ is Union:
            args = [arg for arg in type_.__args__ if arg is not type(None)]
            if args:
                type_ = args[0]

        if hasattr(type_, '__name__'):
            return type_.__name__.lower()
        else:
            return str(type_).lower()
    except Exception:
        return "any"


def _extract_parameters_from_schema(schema: dict[str, Any] | type, log: logging.Logger) -> dict[str, dict[str, Any]]:
    """
    Extract parameter information from Pydantic schema.

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
        # Handle Pydantic v2 schema
        if hasattr(schema, 'model_fields'):
            fields = schema.model_fields
            required_fields = getattr(schema, '__required_fields__', set())

            params = {}
            for field_name, field_info in fields.items():
                # Check if field is required
                is_required = (
                    field_name in required_fields or
                    (hasattr(field_info, 'is_required') and field_info.is_required()) or
                    (not hasattr(field_info, 'default') or field_info.default is None)
                )

                param_info = {
                    "required": is_required,
                    "description": getattr(field_info, 'description', '') or "",
                    "type": _get_field_type_name(field_info)
                }
                params[field_name] = param_info

            return params

        # Handle Pydantic v1 schema
        elif hasattr(schema, '__fields__'):
            fields = schema.__fields__
            params = {}

            for field_name, field_info in fields.items():
                param_info = {
                    "required": field_info.required,
                    "description": getattr(field_info.field_info, 'description', '') or "",
                    "type": _get_field_type_name_v1(field_info)
                }
                params[field_name] = param_info

            return params

        # Handle dict schema (JSON schema)
        elif isinstance(schema, dict):
            properties = schema.get("properties", {})
            required = schema.get("required", [])

            params = {}
            for param_name, param_schema in properties.items():
                param_info = {
                    "required": param_name in required,
                    "description": param_schema.get("description", ""),
                    "type": param_schema.get("type", "any")
                }
                params[param_name] = param_info

            return params

    except Exception as e:
        log.debug(f"Schema extraction failed: {e}")

    return {}


def _format_tool_descriptions(tools: list, log: logging.Logger) -> str:
    """
    Format tool descriptions for planner with parameter schemas.

    Includes:
    - Tool name
    - Description
    - Required parameters with types
    - Optional parameters (if space allows)
    """
    lines = []

    for tool in tools[:30]:  # Limit to prevent prompt bloat
        name = getattr(tool, 'name', str(tool))
        description = getattr(tool, 'description', '')

        # Start with name and description
        lines.append(f"### {name}")
        if description:
            # Truncate long descriptions
            desc_text = description
            lines.append(f"  {desc_text}")

        # Extract parameter schema
        try:
            schema = getattr(tool, 'args_schema', None)
            if schema:
                params_info = _extract_parameters_from_schema(schema, log)
                if params_info:
                    # Add blank line between description and parameters
                    lines.append("")
                    lines.append("  **Parameters:**")
                    for param_name, param_info in params_info.items():
                        required_marker = "**required**" if param_info.get("required") else "optional"
                        param_type = param_info.get("type", "any").upper()
                        param_desc = param_info.get("description", "")

                        # Format: - param_name (required): description [TYPE]
                        if param_desc:
                            lines.append(f"  - `{param_name}` ({required_marker}): {param_desc} [{param_type}]")
                        else:
                            lines.append(f"  - `{param_name}` ({required_marker}) [{param_type}]")
        except Exception as e:
            log.debug(f"Could not extract schema for {name}: {e}")

        lines.append("")

    return "\n".join(lines)
