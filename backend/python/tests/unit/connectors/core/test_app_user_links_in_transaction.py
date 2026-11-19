"""The store reads the user-app links inside the transaction it holds."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.data_store.graph_data_store import GraphTransactionStore


@pytest.mark.asyncio
async def test_the_store_reads_the_links_inside_its_transaction() -> None:
    provider = MagicMock(get_app_user_links=AsyncMock(return_value=[]))
    store = GraphTransactionStore(provider, "txn-7")
    store.graph_provider = provider
    await store.get_app_user_links("org", "app")
    provider.get_app_user_links.assert_awaited_once_with("org", "app", transaction="txn-7")
