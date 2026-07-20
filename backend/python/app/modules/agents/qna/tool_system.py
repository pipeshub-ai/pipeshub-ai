"""Tool System — retained utilities.

Most tool loading logic has moved to ``app.agents.agent_loop.tool_loader``.
This module retains only:

- ``code_execution_enabled`` — deployment-level gate for sandbox tools
- Tool-result normalisation helpers (used by ``nodes.py``)
- ``get_tool_results_summary`` — debugging helper
"""

import json
import logging

from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

MAX_RESULT_PREVIEW_LENGTH = 150


def _normalise_tool_result(value: object) -> str:
    """Normalise a tool's return value into the string ``ToolMessage.content``
    expects."""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, default=str)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _flatten_success_into_payload(success: bool, data: object) -> str:
    """Inject ``success`` as a top-level key into the data JSON."""
    if isinstance(data, dict):
        return json.dumps({**data, "success": bool(success)}, default=str)
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
            if isinstance(parsed, dict):
                return json.dumps({**parsed, "success": bool(success)}, default=str)
        except (json.JSONDecodeError, ValueError):
            pass
    return json.dumps(
        {"success": bool(success), "content": _normalise_tool_result(data)},
        default=str,
    )


def code_execution_enabled(state: ChatState) -> bool:
    """Return whether this deployment/caller has access to code-execution tools.

    Source of truth is the ``ENABLE_CODE_EXECUTION`` platform feature flag.
    Defaults to ENABLED so the feature works out of the box.

    Resolution order (first hit wins):
    1. ``state["enable_code_execution"]`` — per-request override
    2. ``PIPESHUB_ENABLE_CODE_EXECUTION`` env var
    3. ``FeatureFlagService`` — platform settings
    4. Default: ``True``
    """
    state_flag = state.get("enable_code_execution")
    if isinstance(state_flag, bool):
        return state_flag

    import os as _os
    env_val = _os.environ.get("PIPESHUB_ENABLE_CODE_EXECUTION")
    if env_val is not None:
        raw = env_val.strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False

    try:
        from app.services.featureflag.config.config import CONFIG
        from app.services.featureflag.featureflag import FeatureFlagService

        return bool(
            FeatureFlagService.get_service().is_feature_enabled(
                CONFIG.ENABLE_CODE_EXECUTION, default=True
            )
        )
    except Exception:
        return True


def get_tool_results_summary(state: ChatState) -> str:
    """Get summary of tool execution results."""
    all_results = state.get("all_tool_results", [])
    if not all_results:
        return "No tools executed yet."

    categories: dict[str, dict] = {}
    for result in all_results:
        tool_name = result.get("tool_name", "unknown")
        category = tool_name.split('.')[0] if '.' in tool_name else "utility"

        if category not in categories:
            categories[category] = {"success": 0, "error": 0, "tools": {}}

        status = result.get("status", "unknown")
        if status in ("success", "error"):
            categories[category][status] += 1

        if tool_name not in categories[category]["tools"]:
            categories[category]["tools"][tool_name] = {"success": 0, "error": 0}

        if status in ("success", "error"):
            categories[category]["tools"][tool_name][status] += 1

    lines = [f"Tool Execution Summary (Total: {len(all_results)}):"]
    for category, stats in sorted(categories.items()):
        lines.append(f"\n## {category.title()} Tools:")
        lines.append(f"  Success: {stats['success']}, Failed: {stats['error']}")
        for tool_name, tool_stats in stats["tools"].items():
            lines.append(f"  - {tool_name}: {tool_stats['success']} ok, {tool_stats['error']} err")

    return "\n".join(lines)
