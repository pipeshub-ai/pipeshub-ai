"""Tests for DataStore abstract classes: DataStoreProvider, BaseDataStore, TransactionStore."""

import logging
from typing import AsyncContextManager, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.data_store.data_store import (
    BaseDataStore,
    DataStoreProvider,
    TransactionStore,
)


# ------------------------------------------------------------------ #
# Concrete implementations for testing abstract classes
# ------------------------------------------------------------------ #

class ConcreteDataStoreProvider(DataStoreProvider):
    """Minimal concrete implementation of DataStoreProvider for testing."""

    def __init__(self, logger):
        super().__init__(logger)
        self._mock_tx = MagicMock()

    async def transaction(self) -> AsyncContextManager["TransactionStore"]:
        return self._mock_tx

    async def execute_in_transaction(self, func, *args, **kwargs) -> None:
        return await func(*args, **kwargs)


class ConcreteTransactionStore(TransactionStore):
    """Minimal concrete implementation of TransactionStore for testing."""

    def __init__(self):
        self._records = {}
        self._committed = False
        self._rolled_back = False

    async def commit(self):
        self._committed = True

    async def rollback(self):
        self._rolled_back = True

    async def get_record_by_key(self, key):
        return self._records.get(key)

    async def get_record_by_external_id(self, connector_id, external_id):
        return None

    async def get_record_by_external_revision_id(self, connector_id, external_revision_id):
        return None

    async def get_record_by_issue_key(self, connector_id, issue_key):
        return None

    async def get_records_by_parent(self, connector_id, parent_external_record_id, record_type=None):
        return []

    async def get_record_path(self, record_id):
        return None

    async def get_records_by_status(self, org_id, connector_id, status_filters, limit=None, offset=0):
        return []

    async def get_record_group_by_external_id(self, connector_id, external_id):
        return None

    async def create_record_groups_relation(self, child_id, parent_id):
        pass

    async def create_user_group_membership(self, user_source_id, group_external_id, connector_id, org_id):
        return True

    async def get_user_by_email(self, email):
        return None

    async def get_user_by_source_id(self, source_user_id, connector_id):
        return None

    async def get_app_user_by_email(self, email):
        return None

    async def batch_upsert_people(self, people):
        pass

    async def get_users(self, org_id, active=True):
        return []

    async def get_user_groups(self, connector_id, org_id):
        return []

    async def delete_record_by_key(self, key):
        self._records.pop(key, None)

    async def delete_record_by_external_id(self, connector_id, external_id):
        pass

    async def get_record_by_conversation_index(self, connector_id, conversation_index, thread_id, org_id, user_id):
        return None

    async def remove_user_access_to_record(self, connector_id, external_id, user_id):
        pass

    async def delete_record_group_by_external_id(self, connector_id, external_id):
        pass

    async def delete_user_group_by_id(self, group_id):
        pass

    async def get_record_owner_source_user_email(self, record_id):
        return None

    async def batch_upsert_records(self, records):
        pass

    async def batch_upsert_record_groups(self, record_groups):
        pass

    async def batch_upsert_record_permissions(self, record_id, permissions):
        pass

    async def batch_upsert_record_group_permissions(self, record_group_id, permissions, connector_id):
        pass

    async def batch_upsert_user_groups(self, user_groups):
        pass

    async def batch_upsert_app_users(self, users):
        pass

    async def batch_upsert_orgs(self, orgs):
        pass

    async def batch_upsert_domains(self, domains):
        pass

    async def batch_upsert_anyone(self, anyone):
        pass

    async def batch_upsert_anyone_with_link(self, anyone_with_link):
        pass

    async def batch_upsert_anyone_same_org(self, anyone_same_org):
        pass

    async def create_record_relation(self, from_record_id, to_record_id, relation_type):
        pass

    async def create_record_group_relation(self, record_id, record_group_id):
        pass

    async def create_sync_point(self, sync_point):
        pass

    async def delete_sync_point(self, sync_point):
        pass

    async def read_sync_point(self, sync_point):
        pass

    async def update_sync_point(self, sync_point):
        pass

    async def get_edges_from_node(self, from_node_id, edge_collection):
        return []
    
    async def get_edges_from_node_with_target_name(self, from_node_id, edge_collection):
        return []

    async def get_edge(self, from_id, from_collection, to_id, to_collection, collection):
        return None

    async def delete_edge(self, from_id, from_collection, to_id, to_collection, collection):
        pass

    async def delete_edges_by_relationship_types(self, from_id, from_collection, collection, relationship_types):
        return 0

    async def delete_parent_child_edge_to_record(self, record_id):
        return 0

    async def ensure_team_app_edge(self, connector_id: str, org_id: str) -> None:
        pass


class TestDataStoreProvider:
    """Tests for DataStoreProvider base class."""

    def test_init_stores_logger(self):
        logger = logging.getLogger("test")
        provider = ConcreteDataStoreProvider(logger)
        assert provider.logger is logger

    @pytest.mark.asyncio
    async def test_execute_in_transaction(self):
        logger = logging.getLogger("test")
        provider = ConcreteDataStoreProvider(logger)

        async def my_func(x, y):
            return x + y

        result = await provider.execute_in_transaction(my_func, 3, 5)
        assert result == 8


class TestTransactionStore:
    """Tests for the TransactionStore concrete implementation."""

    @pytest.mark.asyncio
    async def test_commit(self):
        store = ConcreteTransactionStore()
        await store.commit()
        assert store._committed is True

    @pytest.mark.asyncio
    async def test_rollback(self):
        store = ConcreteTransactionStore()
        await store.rollback()
        assert store._rolled_back is True

    @pytest.mark.asyncio
    async def test_get_record_by_key_found(self):
        store = ConcreteTransactionStore()
        store._records["rec1"] = {"name": "Record 1"}
        result = await store.get_record_by_key("rec1")
        assert result == {"name": "Record 1"}

    @pytest.mark.asyncio
    async def test_get_record_by_key_not_found(self):
        store = ConcreteTransactionStore()
        result = await store.get_record_by_key("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_record_by_key(self):
        store = ConcreteTransactionStore()
        store._records["rec1"] = {"name": "Record 1"}
        await store.delete_record_by_key("rec1")
        assert "rec1" not in store._records

    @pytest.mark.asyncio
    async def test_get_record_by_external_id_returns_none(self):
        store = ConcreteTransactionStore()
        result = await store.get_record_by_external_id("conn1", "ext1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_record_by_external_revision_id_returns_none(self):
        store = ConcreteTransactionStore()
        result = await store.get_record_by_external_revision_id("conn1", "rev1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_records_by_status_returns_empty(self):
        store = ConcreteTransactionStore()
        result = await store.get_records_by_status("org1", "conn1", ["active"])
        assert result == []

    @pytest.mark.asyncio
    async def test_get_users_returns_empty(self):
        store = ConcreteTransactionStore()
        result = await store.get_users("org1")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_user_by_email_returns_none(self):
        store = ConcreteTransactionStore()
        result = await store.get_user_by_email("test@example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_create_user_group_membership(self):
        store = ConcreteTransactionStore()
        result = await store.create_user_group_membership("user1", "group1", "conn1", "org1")
        assert result is True

    @pytest.mark.asyncio
    async def test_get_edges_from_node_returns_empty(self):
        store = ConcreteTransactionStore()
        result = await store.get_edges_from_node("node1", "edge_coll")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_edges_from_node_with_target_name_returns_empty(self):
        store = ConcreteTransactionStore()
        result = await store.get_edges_from_node_with_target_name("node1", "edge_coll")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_edge_returns_none(self):
        store = ConcreteTransactionStore()
        result = await store.get_edge("from1", "from_coll", "to1", "to_coll", "edge_coll")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_edges_by_relationship_types_returns_zero(self):
        store = ConcreteTransactionStore()
        result = await store.delete_edges_by_relationship_types("from1", "from_coll", "edge_coll", ["TYPE_A"])
        assert result == 0

    @pytest.mark.asyncio
    async def test_delete_parent_child_edge_to_record_returns_zero(self):
        store = ConcreteTransactionStore()
        result = await store.delete_parent_child_edge_to_record("rec1")
        assert result == 0


class TestDataStoreProviderTransaction:
    """Tests for DataStoreProvider.transaction() method."""

    @pytest.mark.asyncio
    async def test_transaction_returns_context_manager(self):
        logger = logging.getLogger("test")
        provider = ConcreteDataStoreProvider(logger)
        result = await provider.transaction()
        assert result is provider._mock_tx

    @pytest.mark.asyncio
    async def test_execute_in_transaction_with_kwargs(self):
        logger = logging.getLogger("test")
        provider = ConcreteDataStoreProvider(logger)

        async def my_func(x, y=10):
            return x + y

        result = await provider.execute_in_transaction(my_func, 5, y=20)
        assert result == 25


class TestBaseDataStoreCannotBeInstantiated:
    """Verify that BaseDataStore and DataStoreProvider cannot be instantiated directly."""

    def test_base_data_store_is_abstract(self):
        with pytest.raises(TypeError):
            BaseDataStore()

    def test_data_store_provider_is_abstract(self):
        with pytest.raises(TypeError):
            DataStoreProvider(logging.getLogger("test"))

    def test_transaction_store_is_abstract(self):
        with pytest.raises(TypeError):
            TransactionStore()
