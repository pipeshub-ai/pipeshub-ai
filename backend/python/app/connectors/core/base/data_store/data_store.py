from abc import ABC, abstractmethod
from logging import Logger
from typing import TYPE_CHECKING, AsyncContextManager, Dict, List, Optional

from app.models.entities import (
    Anyone,
    AnyoneSameOrg,
    AnyoneWithLink,
    AppUser,
    Domain,
    Org,
    Person,
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
    async def get_record_by_external_id(self, connector_id: str, external_id: str) -> Optional[Record]:
        pass

    @abstractmethod
    async def get_record_by_external_revision_id(self, connector_id: str, external_revision_id: str) -> Optional[Record]:
        """Get record by external revision ID (e.g., etag for S3)."""
        pass

    @abstractmethod
    async def get_record_by_issue_key(self, connector_id: str, issue_key: str) -> Optional[Record]:
        """Get record by Jira issue key (e.g., PROJ-123) by searching weburl pattern."""
        pass

    @abstractmethod
    async def get_records_by_parent(
        self,
        connector_id: str,
        parent_external_record_id: str,
        record_type: Optional[str] = None
    ) -> List[Record]:
        """Get all child records for a parent record by parent_external_record_id. Optionally filter by record_type."""
        pass

    @abstractmethod
    async def get_record_path(self, record_id: str) -> Optional[str]:
        """
        Get full hierarchical path for a record by traversing parent-child edges.

        Args:
            record_id: The record _key to get the path for.

        Returns:
            Optional[str]: Path string (e.g. "Folder1/Subfolder/File.txt") or None.
        """
        pass

    @abstractmethod
    async def get_records_by_status(self, org_id: str, connector_id: str, status_filters: List[str], limit: Optional[int] = None, offset: int = 0) -> List[Record]:
        """Get records by their indexing status with pagination support. Returns typed Record instances."""
        pass

    @abstractmethod
    async def get_record_group_by_external_id(self, connector_id: str, external_id: str) -> Optional[RecordGroup]:
        pass

    @abstractmethod
    async def create_record_groups_relation(self, child_id: str, parent_id: str) -> None:
        pass


    @abstractmethod
    async def create_user_group_membership(self, user_source_id: str, group_external_id: str, connector_id: str, org_id: str) -> bool:
        pass

    @abstractmethod
    async def get_user_by_email(self, email: str) -> Optional[User]:
        pass

    @abstractmethod
    async def get_user_by_source_id(self, source_user_id: str, connector_id: str) -> Optional[User]:
        pass

    @abstractmethod
    async def get_app_user_by_email(self, email: str) -> Optional[AppUser]:
        pass

    @abstractmethod
    async def batch_upsert_people(self, people: List[Person]) -> None:
        pass

    @abstractmethod
    async def get_users(self, org_id: str, active: bool = True) -> List[User]:
        pass

    @abstractmethod
    async def get_user_groups(self, connector_id: str, org_id: str) -> List[UserGroup]:
        pass

    @abstractmethod
    async def delete_record_by_key(self, key: str) -> None:
        pass

    @abstractmethod
    async def delete_record_by_external_id(self, connector_id: str, external_id: str) -> None:
        pass

    @abstractmethod
    async def get_record_by_conversation_index(self, connector_id: str, conversation_index: str, thread_id: str, org_id: str, user_id: str) -> Optional[Record]:
        pass

    @abstractmethod
    async def remove_user_access_to_record(self, connector_id: str, external_id: str, user_id: str) -> None:
        pass

    @abstractmethod
    async def delete_record_group_by_external_id(self, connector_id: str, external_id: str) -> None:
        pass

    @abstractmethod
    async def delete_user_group_by_id(self, group_id: str) -> None:
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
    async def batch_upsert_record_group_permissions(self, record_group_id: str, permissions: List[Permission], connector_id: str) -> None:
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
    async def create_record_relation(
        self,
        from_record_id: str,
        to_record_id: str,
        relation_type: str
    ) -> None:
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

    @abstractmethod
    async def get_edges_from_node(self, from_node_id: str, edge_collection: str) -> List[Dict]:
        pass

    @abstractmethod
    async def get_edge(self, from_id: str, from_collection: str, to_id: str, to_collection: str, collection: str) -> Optional[Dict]:
        pass

    @abstractmethod
    async def delete_edge(self, from_id: str, from_collection: str, to_id: str, to_collection: str, collection: str) -> None:
        pass

    @abstractmethod
    async def delete_edges_from(self, from_id: str, from_collection: str, collection: str) -> int:
        """Delete all outbound edges from a node in the given edge collection. Returns number deleted."""
        pass

    @abstractmethod
    async def delete_edges_by_relationship_types(
        self,
        from_id: str,
        from_collection: str,
        collection: str,
        relationship_types: List[str]
    ) -> int:
        """
        Delete edges from a record by relationship types.

        Args:
            from_id: Source record ID
            from_collection: Source record collection name
            collection: Edge collection name
            relationship_types: List of relationship type values to delete

        Returns:
            int: Number of edges deleted
        """
        pass

    @abstractmethod
    async def delete_parent_child_edge_to_record(self, record_id: str) -> int:
        """
        Delete PARENT_CHILD edges pointing to a specific target record.

        Args:
            record_id: The record_id for which the parent_child edge has to be deleted

        Returns:
            int: Number of edges deleted
        """
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

