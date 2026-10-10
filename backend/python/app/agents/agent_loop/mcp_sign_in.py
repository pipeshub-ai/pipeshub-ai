"""Sign in again from the chat: the MCP servers whose sign-in lacked a scope this turn.

A server that refuses a request with 403 `insufficient_scope` names the scopes it needs, which
the next sign-in asks for (`app.agents.mcp.step_up`). This keeps, per request, which servers
those were, so the reply carries a card that signs in again right there: one `mcp_sign_in`
part, added when the answer is finalized (`AnswerFinalizer._attach_parts`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from app.agents.agent_loop.tool_approvals import WEB_CLIENT_NAME
from app.agents.mcp.errors import MCPInsufficientScopeError
from app.agents.mcp.www_authenticate import MAX_SCOPES

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.mcp_access import ResolvedMCPServer

SIGN_IN_PART_TYPE = "mcp_sign_in"
# Told to the model alongside the server's own message when the card shows.
BUTTON_HINT = "They can also sign in again with the button shown under this answer."


def shows_sign_in_card(context: Optional[AgentContext]) -> bool:
    """The web chat, streaming the AG-UI protocol, in a conversation: the only place the card
    is drawn. The legacy protocol keeps no transcript, so the part would never arrive."""
    return bool(
        context is not None and context.client_name == WEB_CLIENT_NAME and context.chat_streaming
        and context.conversation_id and context.protocol == "agui"
    )


def note_sign_in_needed(context: Optional[AgentContext], server: ResolvedMCPServer, exc: BaseException) -> bool:
    """Remembers `server` for the card when `exc` is a refusal for scope and the card can show.
    One entry per server, its scopes merged. An agent's own sign-in (a service-account agent's,
    kept under its agentKey) carries that key: only someone who can edit the agent may redo it,
    which the client knows and the route checks."""
    if context is None or not isinstance(exc, MCPInsufficientScopeError) or not exc.scopes or not shows_sign_in_card(context):
        return False
    agent_key = server.owner_id if server.owner_id != context.user_id else None
    for entry in context.mcp_sign_in_needed:
        if entry["instanceId"] == server.instance_id and entry.get("agentKey") == agent_key:
            entry["scopes"] = list(dict.fromkeys([*entry["scopes"], *exc.scopes]))[:MAX_SCOPES]
            return True
    entry: dict[str, Any] = {"instanceId": server.instance_id, "serverName": server.display_name, "scopes": list(exc.scopes)}
    if agent_key:
        entry["agentKey"] = agent_key
    context.mcp_sign_in_needed.append(entry)
    return True


def sign_in_part(context: Optional[AgentContext]) -> Optional[dict[str, Any]]:
    if context is None or not context.mcp_sign_in_needed or not shows_sign_in_card(context):
        return None
    return {
        "type": SIGN_IN_PART_TYPE,
        "servers": [{**entry, "scopes": list(entry["scopes"])} for entry in context.mcp_sign_in_needed],
    }
