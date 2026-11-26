"""Where a person connects an item a run could not use, as the model is told it (WS-09, QA23-F1)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.interactions.connect import ConnectNeed
    from harness_core.kernel.core.messages import ToolCall

ItemKind = Literal["toolset", "mcp"]

NEEDS_AUTHENTICATION: Final = "needs_authentication"
# Relative app paths, the same the chat's resource panel links to: never a host, never a token.
CONNECT_PATHS: Final[dict[ItemKind, str]] = {
    "toolset": "/workspace/actions",
    "mcp": "/workspace/mcp-servers/personal",
}
_PLACES: Final[dict[ItemKind, str]] = {"toolset": "Workspace > Actions", "mcp": "Workspace > MCP servers"}


def connect_text(item: str, kind: ItemKind, reason: str = NEEDS_AUTHENTICATION) -> str:
    """The answer to a call of an unconnected item: what, why, and the link the person opens."""
    return (
        f"{item} is not connected for you ({reason}): connect it under {_PLACES[kind]} "
        f"({CONNECT_PATHS[kind]}), then ask again"
    )


def declined_text(item: str, kind: ItemKind) -> str:
    """A call that waited for its item and the person did not connect it: not made, and the next step."""
    return (
        f"interaction_denied: {item} was not connected, so this call was not made. Tell the user they can connect "
        f"it under {_PLACES[kind]} ({CONNECT_PATHS[kind]}) and ask again; do not call it again until they have."
    )


class CatalogConnectGate:
    """The attempt's unconnected items by tool name (G-12): assembled per attempt, so a resumed run sees a
    credential its owner added while it waited.

    ``reconnects``: each connected toolset tool's item, waited on once ``rejected`` (the attempt's live record of
    toolsets whose saved sign-in the vendor rejected and that were withdrawn) names its instance (WP-64). The call
    then waits for a reconnect instead of being refused for the rest of the attempt."""

    def __init__(
        self,
        needs: Mapping[str, ConnectNeed],
        reconnects: Mapping[str, ConnectNeed] | None = None,
        rejected: Mapping[str, str] | None = None,
    ) -> None:
        self._needs = dict(needs)
        self._reconnects = dict(reconnects or {})
        self._rejected = rejected if rejected is not None else {}

    def need(self, call: ToolCall) -> ConnectNeed | None:
        need = self._needs.get(call.name)
        if need is not None:
            return need
        reconnect = self._reconnects.get(call.name)
        return reconnect if reconnect is not None and reconnect.instance_id in self._rejected else None

    def declined(self, call: ToolCall) -> str:
        need = self.need(call)
        if need is None:
            return f"interaction_denied: {call.name} needed a connection the user did not make, so it was not called."
        return declined_text(need.item, need.item_kind)


__all__ = ["CONNECT_PATHS", "NEEDS_AUTHENTICATION", "CatalogConnectGate", "ItemKind", "connect_text", "declined_text"]
