"""stop() cancels the loops that start() runs in the background."""

import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import MagicMock

import pytest

from app.connectors.core.base.token_service.token_refresh_service import (
    TokenRefreshService,
)
from app.connectors.core.base.token_service.toolset_token_refresh_service import (
    ToolsetTokenRefreshService,
)


def _park(service, names: list[str]) -> dict[str, asyncio.Event]:
    """Replace each loop with one that waits until it is cancelled, and record the cancellation."""
    cancelled = {name: asyncio.Event() for name in names}

    def parked(name: str) -> Callable[[], Awaitable[None]]:
        async def run() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled[name].set()
                raise

        return run

    for name in names:
        setattr(service, name, parked(name))
    return cancelled


@pytest.mark.parametrize(
    ("make", "loops"),
    [
        (
            lambda: TokenRefreshService(MagicMock(), MagicMock()),
            ["_refresh_all_tokens", "_periodic_refresh_check"],
        ),
        (
            lambda: ToolsetTokenRefreshService(MagicMock()),
            ["_refresh_all_tokens", "_periodic_refresh_check", "_cleanup_old_locks"],
        ),
    ],
    ids=["connector", "toolset"],
)
async def test_stop_cancels_background_loops(make, loops) -> None:
    service = make()
    cancelled = _park(service, loops)

    await service.start(wait_for_initial_refresh=False)
    await asyncio.sleep(0)  # let the loops reach their first await
    await service.stop()

    await asyncio.wait_for(
        asyncio.gather(*(e.wait() for e in cancelled.values())), timeout=1
    )
    assert service._background_tasks == []
