"""A mocked data store provider whose ``execute_in_transaction`` behaves like the real one."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from unittest.mock import MagicMock


def runs_in_its_transaction(provider: MagicMock) -> MagicMock:
    """execute_in_transaction runs the block in the mock's own transaction()."""
    async def execute_in_transaction(func, *args, **kwargs) -> object:
        async with provider.transaction() as tx_store:
            return await func(tx_store, *args, **kwargs)
    provider.execute_in_transaction = execute_in_transaction
    return provider
