"""The Gmail suites' PipesHub users, ready before a connector is created for them."""

from __future__ import annotations

from typing import Any

from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import async_poll_until  # type: ignore[import-not-found]
from helper.pipeshub_users import ensure_pipeshub_user_exists  # type: ignore[import-not-found]

USER_GRAPH_TIMEOUT_SEC = 120


async def wait_for_active_graph_user(graph_provider: GraphProviderProtocol, email: str) -> None:
    """A new PipesHub user reaches the graph through entity events after the API call returns. A sync started
    before then reads no Workspace mailbox for them, and grants a personal mailbox to no one."""

    async def active() -> dict[str, Any] | None:
        user = await graph_provider.graph_find_user_by_email(email)
        return user if user and user.get("isActive") is True else None

    await async_poll_until(active, timeout=USER_GRAPH_TIMEOUT_SEC, interval=2,
                           description=f"active PipesHub graph user for {email}")


async def ensure_active_user(users_client: Any, graph_provider: GraphProviderProtocol, email: str) -> str:
    """The id of the PipesHub user ``email``, created if missing, once it is active in the graph."""
    user_id, _ = ensure_pipeshub_user_exists(users_client, email)
    await wait_for_active_graph_user(graph_provider, email)
    return user_id
