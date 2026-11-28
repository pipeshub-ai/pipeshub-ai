"""The source-account setting and the account ids, without a store."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_processor.source_accounts import (
    SourceAccountGraph,
    account_node_id,
    graph_mode_key,
)
from tests.support.in_memory_config import InMemoryConfigService


def _processor(config) -> DataSourceEntitiesProcessor:
    processor = DataSourceEntitiesProcessor(MagicMock(), MagicMock(), config)
    processor.org_id = "org1"
    return processor


async def test_the_setting_is_off_unless_it_says_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PERM_SOURCE_ACCOUNTS", raising=False)      # with it, an absent setting reads as on
    config = InMemoryConfigService()
    processor = _processor(config)
    assert await processor._account_graph("c1") is None
    await config.set_config(graph_mode_key("c2"), {"enabled": "yes"})
    assert await processor._account_graph("c2") is None
    await config.set_config(graph_mode_key("c3"), {"enabled": True})
    graph = await processor._account_graph("c3")
    assert isinstance(graph, SourceAccountGraph) and graph.connector_id == "c3" and graph.org_id == "org1"


async def test_an_unreadable_setting_reads_as_off_and_is_read_again() -> None:
    config = InMemoryConfigService()
    await config.set_config(graph_mode_key("c1"), {"enabled": True})
    real = config.get_config
    config.get_config = AsyncMock(side_effect=RuntimeError("kv down"))
    processor = _processor(config)
    assert await processor._account_graph("c1") is None
    config.get_config = real
    assert await processor._account_graph("c1") is not None


def test_an_account_node_is_named_by_org_connector_and_source_id() -> None:
    assert account_node_id("o", "c", "s") == account_node_id("o", "c", "s")
    assert len({account_node_id("o", "c", "s"), account_node_id("o2", "c", "s"), account_node_id("o", "c2", "s"), account_node_id("o", "c", "s2")}) == 4


async def test_a_creator_stand_in_or_an_unknown_account_holds_no_grant() -> None:
    graph = SourceAccountGraph("c1", "org1", MagicMock())
    store = MagicMock(get_nodes_by_field_in=AsyncMock(return_value=[]))
    assert graph.node_id("creator_u1") is None
    assert await graph.principal(store, "creator_u1") is None
    assert await graph.principal(store, "s-unknown") is None
    store.get_nodes_by_field_in = AsyncMock(return_value=[{"_key": account_node_id("org1", "c1", "s1")}])
    assert await graph.principal(store, "s1") == (account_node_id("org1", "c1", "s1"), "sourceAccounts")



async def test_an_account_write_that_fails_leaves_no_account_known() -> None:
    """The transaction rolls the accounts back, so a later grant must read the store again rather than trust them."""
    from app.config.constants.arangodb import Connectors
    from app.models.entities import AppUser

    graph = SourceAccountGraph("c1", "org1", MagicMock())
    tx = MagicMock(get_nodes_by_field_in=AsyncMock(return_value=[]), batch_upsert_nodes=AsyncMock(),
                   get_users_by_emails=AsyncMock(side_effect=RuntimeError("graph down")))
    user = AppUser(app_name=Connectors.CONFLUENCE, connector_id="c1", source_user_id="s-1", email="a@x.io", full_name="A")
    with pytest.raises(RuntimeError):
        await graph.upsert(tx, [user])
    assert graph._known == set()
