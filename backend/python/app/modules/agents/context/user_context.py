"""User-context prompt formatter, extracted from
`modules/agents/qna/nodes.py` (Phase 0 of the agent-loop migration).
"""


from __future__ import annotations

from app.modules.agents.context.connector_detection import _has_jira_tools
from app.modules.agents.qna.chat_state import ChatState


def _format_user_context(state: ChatState) -> str:
    """Format user information for planner"""
    user_info = state.get("user_info", {})
    org_info = state.get("org_info", {})

    user_email = state.get("user_email") or user_info.get("userEmail") or user_info.get("email") or ""
    user_name = (
        user_info.get("fullName") or
        user_info.get("name") or
        user_info.get("displayName") or
        (f"{user_info.get('firstName', '')} {user_info.get('lastName', '')}".strip()
         if user_info.get("firstName") or user_info.get("lastName") else "")
    )

    if not user_email and not user_name:
        return ""

    parts = ["## Current User Information", ""]

    if user_name:
        parts.append(f"- **Name**: {user_name}")
    if user_email:
        parts.append(f"- **Email**: {user_email}")

    if org_info.get("accountType"):
        parts.append(f"- **Account Type**: {org_info['accountType']}")

    if user_email or user_name:
        parts.append("")
        parts.append("### Usage:")
        parts.append("")

        if _has_jira_tools(state):
            parts.append("**Jira (current user):**")
            parts.append("- ✅ Use `currentUser()` in JQL: `assignee = currentUser()`")
            parts.append("- ❌ DON'T call `jira.search_users` for yourself")
            parts.append("")

        parts.append("**General:**")
        parts.append("- **When user asks about themselves**: answer using this info directly — no tools needed")
        parts.append("")

    return "\n".join(parts)

