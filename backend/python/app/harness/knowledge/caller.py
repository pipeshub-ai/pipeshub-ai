"""Who a search answers when it runs as someone else (P2.13, D21). Import-light: the agent tools read it."""

from __future__ import annotations

from typing import cast

#: A service-account request without a user: no user of the org matches it, so its scope is empty (fail closed).
UNRESOLVED_CALLER = "\u0000unresolved-caller"


def caller_user_id_of(user_info: object) -> str | None:
    """The real caller of a service-account agent's request, whose retrieval runs as the agent's creator."""
    if not isinstance(user_info, dict):
        return None
    caller = cast("dict[str, object]", user_info).get("callerUserId")
    return caller if isinstance(caller, str) and caller else None


__all__ = ["UNRESOLVED_CALLER", "caller_user_id_of"]
