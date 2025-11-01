from abc import ABC, abstractmethod
from logging import Logger
from typing import TYPE_CHECKING, AsyncContextManager, List, Optional

from app.config.constants.arangodb import Connectors
from app.models.entities import (
    Anyone,
    AnyoneSameOrg,
    AnyoneWithLink,
    AppUser,
    Domain,
    Org,
    Record,
    RecordGroup,
    User,
    UserGroup,
)
from app.models.permission import Permission

if TYPE_CHECKING:
    from app.connectors.core.base.sync_point.sync_point import SyncPoint

class DataStoreProvider(ABC):
    logger: Logger

    def __init__(self, logger: Logger) -> None:
        self.logger = logger

    """Base class for all data store providers"""
    @abstractmethod
    async def transaction(self) -> AsyncContextManager["TransactionStore"]:
        """
        Return a transaction store context manager.

        Usage:
            async with datastore.transaction() as tx_store:
                tx_store.batch_upsert_records(records)
                tx_store.batch_upsert_record_permissions(record_id, permissions)
                # Automatically commits on success, rolls back on exception
        """
        pass

    @abstractmethod
    async def execute_in_transaction(self, func, *args, **kwargs) -> None:
        """
        Execute a function within a transaction.

        Usage:
            async def bulk_update(tx_store):
                tx_store.batch_upsert_records(records)
                tx_store.batch_upsert_record_permissions(record_id, permissions)

            await datastore.execute_in_transaction(bulk_update)
        """
        pass

class BaseDataStore(ABC):
    """Base class for all data stores"""

    @abstractmethod
    async def get_record_by_key(self, key: str) -> Optional[Record]:
        pass

    @abstractmethod
    async def get_record_by_external_id(self, connector_name: Connectors, external_id: str) -> Optional[Record]:
        pass

    @abstractmethod
    async def get_record_group_by_external_id(self, connector_name: Connectors, external_id: str) -> Optional[RecordGroup]:
        pass

    @abstractmethod
    async def create_record_groups_relation(self, child_id: str, parent_id: str) -> None:
        pass

    @abstractmethod
    async def create_user_group_hierarchy(self, child_external_id: str, parent_external_id: str, connector_name: Connectors) -> bool:
        pass

    @abstractmethod
    async def create_user_group_membership(self, user_source_id: str, group_external_id: str, connector_name: Connectors, org_id: str) -> bool:
        pass

    @abstractmethod
    async def get_user_by_email(self, email: str) -> Optional[User]:
        pass

    @abstractmethod
    async def get_user_by_source_id(self, source_user_id: str, connector_name: Connectors) -> Optional[User]:
        pass

    @abstractmethod
    async def get_app_user_by_email(self, email: str) -> Optional[AppUser]:
        pass

    @abstractmethod
    async def get_users(self, org_id: str, active: bool = True) -> List[User]:
        pass

    @abstractmethod
    async def get_user_groups(self, app_name: Connectors, org_id: str) -> List[UserGroup]:
        pass

    @abstractmethod
    async def delete_record_by_key(self, key: str) -> None:
        pass

    @abstractmethod
    async def delete_record_by_external_id(self, connector_name: Connectors, external_id: str) -> None:
        pass

    @abstractmethod
    async def get_record_by_conversation_index(self, connector_name: Connectors, conversation_index: str, thread_id: str, org_id: str, user_id: str) -> Optional[Record]:
        pass

    @abstractmethod
    async def remove_user_access_to_record(self, connector_name: Connectors, external_id: str, user_id: str) -> None:
        pass

    @abstractmethod
    async def delete_record_group_by_external_id(self, connector_name: Connectors, external_id: str) -> None:
        pass

    @abstractmethod
    async def get_record_owner_source_user_email(self, record_id: str) -> Optional[str]:
        pass

    @abstractmethod
    async def batch_upsert_records(self, records: List[Record]) -> None:
        pass

    @abstractmethod
    async def batch_upsert_record_groups(self, record_groups: List[RecordGroup]) -> None:
        pass

    @abstractmethod
    async def batch_upsert_record_permissions(self, record_id: str, permissions: List[Permission]) -> None:
        pass

    @abstractmethod
    async def batch_upsert_record_group_permissions(self, record_group_id: str, permissions: List[Permission], connector_name: Connectors) -> None:
        pass

    @abstractmethod
    async def batch_upsert_user_groups(self, user_groups: List[UserGroup]) -> None:
        pass

    @abstractmethod
    async def batch_upsert_app_users(self, users: List[AppUser]) -> None:
        pass

    @abstractmethod
    async def batch_upsert_orgs(self, orgs: List[Org]) -> None:
        pass

    @abstractmethod
    async def batch_upsert_domains(self, domains: List[Domain]) -> None:
        pass

    @abstractmethod
    async def batch_upsert_anyone(self, anyone: List[Anyone]) -> None:
        pass

    @abstractmethod
    async def batch_upsert_anyone_with_link(self, anyone_with_link: List[AnyoneWithLink]) -> None:
        pass

    @abstractmethod
    async def batch_upsert_anyone_same_org(self, anyone_same_org: List[AnyoneSameOrg]) -> None:
        pass

    @abstractmethod
    async def create_record_relation(self, from_record_id: str, to_record_id: str, relation_type: str) -> None:
        pass

    @abstractmethod
    async def create_record_group_relation(self, record_id: str, record_group_id: str) -> None:
        pass

    @abstractmethod
    async def create_sync_point(self, sync_point: "SyncPoint") -> None:
        pass

    @abstractmethod
    async def delete_sync_point(self, sync_point: "SyncPoint") -> None:
        pass

    @abstractmethod
    async def read_sync_point(self, sync_point: "SyncPoint") -> None:
        pass

    @abstractmethod
    async def update_sync_point(self, sync_point: "SyncPoint") -> None:
        pass


class TransactionStore(BaseDataStore):
    """Abstract transaction-aware data store that operates within a transaction context"""

    @abstractmethod
    async def commit(self) -> None:
        """Commit the transaction"""
        pass

    @abstractmethod
    async def rollback(self) -> None:
        """Rollback the transaction"""
        pass

