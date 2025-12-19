"""
Comprehensive Graph Database Provider Interface

This interface defines all database operations needed by the application,
abstracting away the specific database implementation (ArangoDB, Neo4j, etc.).

All methods support optional transaction parameter for atomic operations.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from app.config.constants.arangodb import Connectors

if TYPE_CHECKING:
    from app.models.entities import (
        AppRole,
        AppUserGroup,
        FileRecord,
        Record,
        RecordGroup,
        User,
    )


class IGraphDBProvider(ABC):
    """
    Comprehensive interface for graph database operations.

    This interface abstracts all database operations used throughout the application,
    allowing for multiple database implementations (ArangoDB, Neo4j, etc.) to be
    swapped via configuration.

    Design Principles:
    - All methods are database-agnostic (generic terms like 'document', 'collection', 'edge')
    - Transaction support is optional but consistent across all operations
    - Methods return Python native types (Dict, List) not database-specific objects
    - Error handling returns None/False rather than raising exceptions (where appropriate)
    """

    # ==================== Connection Management ====================

    @abstractmethod
    async def connect(self) -> bool:
        """
        Connect to the database and initialize collections/tables.

        Returns:
            bool: True if connection successful, False otherwise
        """
        pass

    @abstractmethod
    async def disconnect(self) -> bool:
        """
        Disconnect from the database and clean up resources.

        Returns:
            bool: True if disconnection successful, False otherwise
        """
        pass

    # ==================== Transaction Management ====================

    @abstractmethod
    def begin_transaction(self, read: List[str], write: List[str]) -> str:
        """
        Begin a database transaction.

        Args:
            read (List[str]): Collections/tables to read from
            write (List[str]): Collections/tables to write to

        Returns:
            Any: Transaction object (database-specific)
        """
        pass

    # ==================== Document Operations ====================

    @abstractmethod
    async def get_document(
        self,
        document_key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get a document by its key from a collection.

        Args:
            document_key (str): The document's unique key (_key)
            collection (str): Collection/table name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Document data if found, None otherwise
        """
        pass

    @abstractmethod
    async def batch_upsert_nodes(
        self,
        nodes: List[Dict],
        collection: str,
        transaction: Optional[str] = None,
    ) -> Optional[bool]:
        """
        Batch upsert (insert or update) multiple nodes/documents.

        Args:
            nodes (List[Dict]): List of documents to upsert (must have _key)
            collection (str): Collection/table name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[bool]: True if successful, False otherwise, None on error
        """
        pass

    @abstractmethod
    async def delete_nodes(
        self,
        keys: List[str],
        collection: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Delete multiple nodes/documents by their keys.

        Args:
            keys (List[str]): List of document keys to delete
            collection (str): Collection/table name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def update_node(
        self,
        key: str,
        node_updates: Dict,
        collection: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Update a single node/document.

        Args:
            key (str): Document key to update
            node_updates (Dict): Fields to update
            collection (str): Collection/table name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    # ==================== Edge/Relationship Operations ====================

    @abstractmethod
    async def batch_create_edges(
        self,
        edges: List[Dict],
        collection: str,
        transaction: Optional[str] = None,
    ) -> bool:
        """
        Batch create edges/relationships between nodes.

        Args:
            edges (List[Dict]): List of edges with _from and _to fields
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def get_edge(
        self,
        from_key: str,
        to_key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get an edge/relationship between two nodes.

        Args:
            from_key (str): Source node key
            to_key (str): Target node key
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Edge data if found, None otherwise
        """
        pass

    @abstractmethod
    async def delete_edge(
        self,
        from_key: str,
        to_key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Delete an edge/relationship between two nodes.

        Args:
            from_key (str): Source node key
            to_key (str): Target node key
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def delete_edges_from(
        self,
        from_key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> int:
        """
        Delete all edges originating from a node.

        Args:
            from_key (str): Source node key
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            int: Number of edges deleted
        """
        pass

    @abstractmethod
    async def delete_edges_to(
        self,
        to_key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> int:
        """
        Delete all edges pointing to a node.

        Args:
            to_key (str): Target node key
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            int: Number of edges deleted
        """
        pass

    @abstractmethod
    async def delete_edges_to_groups(
        self,
        from_key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> int:
        """
        Delete edges from a node to group nodes.

        Args:
            from_key (str): Source node key
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            int: Number of edges deleted
        """
        pass

    @abstractmethod
    async def delete_edges_between_collections(
        self,
        from_key: str,
        edge_collection: str,
        to_collection: str,
        transaction: Optional[str] = None
    ) -> None:
        """
        Delete edges between a node and nodes in a specific collection.

        Args:
            from_key (str): Source node key
            edge_collection (str): Edge collection name
            to_collection (str): Target collection name
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def delete_nodes_and_edges(
        self,
        keys: List[str],
        collection: str,
        graph_name: str = "knowledgeGraph",
        transaction: Optional[str] = None
    ) -> None:
        """
        Delete nodes and all their connected edges.

        Args:
            keys (List[str]): List of node keys to delete
            collection (str): Collection name
            graph_name (str): Graph name (default: "knowledgeGraph")
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def update_edge(
        self,
        from_key: str,
        to_key: str,
        edge_updates: Dict,
        collection: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Update an edge/relationship.

        Args:
            from_key (str): Source node key
            to_key (str): Target node key
            edge_updates (Dict): Fields to update
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    # ==================== Generic Filter Operations ====================

    @abstractmethod
    async def remove_nodes_by_field(
        self,
        collection: str,
        field_name: str,
        field_value: str,
        transaction: Optional[str] = None
    ) -> int:
        """
        Remove nodes from a collection matching a field value.

        Generic method that can be used for any collection and field.

        Args:
            collection (str): Collection name
            field_name (str): Field name to filter on
            field_value (str): Field value to match
            transaction (Optional[Any]): Optional transaction context

        Returns:
            int: Number of nodes removed

        Example:
            # Remove 'anyone' permissions for a file
            await provider.remove_nodes_by_field("anyone", "file_key", file_key)
        """
        pass

    @abstractmethod
    async def get_edges_to_node(
        self,
        node_id: str,
        edge_collection: str,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get all edges pointing to a specific node.

        Generic method that works with any edge collection.

        Args:
            node_id (str): Full node ID (e.g., "records/123")
            edge_collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            List[Dict]: List of edge documents
        """
        pass

    @abstractmethod
    async def get_related_nodes(
        self,
        node_id: str,
        edge_collection: str,
        target_collection: str,
        direction: str = "inbound",
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get related nodes through an edge collection.

        Generic traversal method for any edge/node combination.

        Args:
            node_id (str): Full node ID to start from
            edge_collection (str): Edge collection to traverse
            target_collection (str): Target node collection
            direction (str): "inbound" or "outbound"
            transaction (Optional[Any]): Optional transaction context

        Returns:
            List[Dict]: List of related node documents
        """
        pass

    @abstractmethod
    async def get_related_node_field(
        self,
        node_id: str,
        edge_collection: str,
        target_collection: str,
        field_name: str,
        direction: str = "inbound",
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get a specific field from related nodes.

        Generic method to get specific fields from related nodes.

        Args:
            node_id (str): Full node ID to start from
            edge_collection (str): Edge collection to traverse
            target_collection (str): Target node collection
            field_name (str): Field to extract from related nodes
            direction (str): "inbound" or "outbound"
            transaction (Optional[Any]): Optional transaction context

        Returns:
            List[Any]: List of field values from related nodes
        """
        pass

    # ==================== Query Operations ====================

    @abstractmethod
    async def execute_query(
        self,
        query: str,
        bind_vars: Optional[Dict] = None,
        transaction: Optional[str] = None
    ) -> Optional[List[Dict]]:
        """
        Execute a database-specific query (AQL for ArangoDB, Cypher for Neo4j).

        Args:
            query (str): Query string in database-specific language
            bind_vars (Optional[Dict]): Query parameters/variables
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[List[Dict]]: Query results if successful, None otherwise
        """
        pass

    @abstractmethod
    async def get_nodes_by_filters(
        self,
        collection: str,
        filters: Dict[str, Any],
        return_fields: Optional[List[str]] = None,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get nodes from a collection matching multiple field filters.

        Generic method to query nodes by any combination of fields.

        Args:
            collection (str): Collection name
            filters (Dict[str, Any]): Dictionary of field_name: value pairs to filter on
            return_fields (Optional[List[str]]): Optional list of fields to return (None = all fields)
            transaction (Optional[Any]): Optional transaction context

        Returns:
            List[Dict]: List of matching node documents
        """
        pass

    @abstractmethod
    async def get_nodes_by_field_in(
        self,
        collection: str,
        field_name: str,
        field_values: List[Any],
        return_fields: Optional[List[str]] = None,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get nodes from a collection where a field value is in a list.

        Generic method for IN queries.

        Args:
            collection (str): Collection name
            field_name (str): Field name to filter on
            field_values (List[Any]): List of values to match
            return_fields (Optional[List[str]]): Optional list of fields to return
            transaction (Optional[Any]): Optional transaction context

        Returns:
            List[Dict]: List of matching node documents
        """
        pass

    # ==================== Record Operations ====================

    @abstractmethod
    async def get_record_by_path(
        self,
        connector_name: Connectors,
        path: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get a record by its file path.

        Args:
            connector_name (Connectors): Connector type
            path (str): File/record path
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Record data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_record_by_external_id(
        self,
        connector_name: Connectors,
        external_id: str,
        transaction: Optional[str] = None
    ) -> Optional['Record']:
        """
        Get a record by its external ID from the source system.

        Args:
            connector_name (Connectors): Connector type
            external_id (str): External record ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Record data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_record_key_by_external_id(
        self,
        external_id: str,
        connector_name: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """
        Get a record's internal key by its external ID.

        Args:
            external_id (str): External record ID
            connector_name (str): Connector name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[str]: Record key if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_records_by_status(
        self,
        org_id: str,
        connector_name: Connectors,
        status_filters: List[str],
        limit: Optional[int] = None,
        offset: int = 0,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get records by their indexing status.

        Args:
            org_id (str): Organization ID
            connector_name (Connectors): Connector type
            status_filters (List[str]): List of status values to filter by
            limit (Optional[int]): Maximum number of records to return
            offset (int): Number of records to skip
            transaction (Optional[Any]): Optional transaction context

        Returns:
            List[Dict]: List of records matching the status filters
        """
        pass

    @abstractmethod
    async def get_record_by_conversation_index(
        self,
        connector_name: Connectors,
        conversation_index: str,
        thread_id: str,
        org_id: str,
        user_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get a record by conversation index (for email/chat connectors).

        Args:
            connector_name (Connectors): Connector type
            conversation_index (str): Conversation index
            thread_id (str): Thread ID
            org_id (str): Organization ID
            user_id (str): User ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Record data if found, None otherwise
        """
        pass

    # ==================== Record Group Operations ====================

    @abstractmethod
    async def get_record_group_by_external_id(
        self,
        connector_name: Connectors,
        external_id: str,
        transaction: Optional[str] = None
    ) -> Optional['RecordGroup']:
        """
        Get a record group by its external ID.

        Args:
            connector_name (Connectors): Connector type
            external_id (str): External record group ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Record group data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_record_group_by_id(
        self,
        id: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get a record group by its internal ID.

        Args:
            id (str): Internal record group ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Record group data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_file_record_by_id(
        self,
        id: str,
        transaction: Optional[str] = None
    ) -> Optional['FileRecord']:
        """
        Get a file record by its internal ID.

        Args:
            id (str): Internal file record ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: File record data if found, None otherwise
        """
        pass

    # ==================== User Operations ====================

    @abstractmethod
    async def get_user_by_email(
        self,
        email: str,
        transaction: Optional[str] = None
    ) -> Optional['User']:
        """
        Get a user by email address.

        Args:
            email (str): User email
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: User data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_user_by_source_id(
        self,
        source_user_id: str,
        connector_name: Connectors,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get a user by their source system ID.

        Args:
            source_user_id (str): User ID in source system
            connector_name (Connectors): Connector type
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: User data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_user_by_user_id(
        self,
        user_id: str
    ) -> Optional[Dict]:
        """
        Get a user by their internal user ID.

        Args:
            user_id (str): Internal user ID

        Returns:
            Optional[Dict]: User data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_users(
        self,
        org_id: str,
        active: bool = True
    ) -> List[Dict]:
        """
        Get all users in an organization.

        Args:
            org_id (str): Organization ID
            active (bool): Filter by active status

        Returns:
            List[Dict]: List of users
        """
        pass

    @abstractmethod
    async def get_app_user_by_email(
        self,
        email: str,
        app_name: Connectors,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get an app-specific user by email.

        Args:
            email (str): User email
            app_name (Connectors): App/connector name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: App user data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_app_users(
        self,
        org_id: str,
        app_name: str
    ) -> List[Dict]:
        """
        Get all users for a specific app in an organization.

        Args:
            org_id (str): Organization ID
            app_name (str): App/connector name

        Returns:
            List[Dict]: List of app users
        """
        pass

    # ==================== Group Operations ====================

    @abstractmethod
    async def get_user_group_by_external_id(
        self,
        connector_name: Connectors,
        external_id: str,
        transaction: Optional[str] = None
    ) -> Optional['AppUserGroup']:
        """
        Get a user group by external ID.

        Args:
            connector_name (Connectors): Connector type
            external_id (str): External group ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Group data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_user_groups(
        self,
        app_name: Connectors,
        org_id: str,
        transaction: Optional[str] = None
    ) -> List['AppUserGroup']:
        """
        Get all user groups for an app in an organization.

        Args:
            app_name (Connectors): App/connector name
            org_id (str): Organization ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            List[Dict]: List of user groups
        """
        pass

    @abstractmethod
    async def get_app_role_by_external_id(
        self,
        connector_name: Connectors,
        external_id: str,
        transaction: Optional[str] = None
    ) -> Optional['AppRole']:
        """
        Get an app role by external ID.

        Args:
            connector_name (Connectors): Connector type
            external_id (str): External role ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Role data if found, None otherwise
        """
        pass

    # ==================== Organization Operations ====================

    @abstractmethod
    async def get_all_orgs(
        self,
        active: bool = True
    ) -> List[Dict]:
        """
        Get all organizations.

        Args:
            active (bool): Filter by active status

        Returns:
            List[Dict]: List of organizations
        """
        pass

    @abstractmethod
    async def get_or_create_app_by_name(
        self,
        app_name: str,
        org_id: str
    ) -> Optional[Dict]:
        """
        Get or create an app by name for an organization.

        Args:
            app_name (str): App name
            org_id (str): Organization ID

        Returns:
            Optional[Dict]: App data if successful, None otherwise
        """
        pass

    @abstractmethod
    async def get_org_apps(
        self,
        org_id: str
    ) -> List[Dict]:
        """
        Get all apps for an organization.

        Args:
            org_id (str): Organization ID

        Returns:
            List[Dict]: List of apps
        """
        pass

    # ==================== Permission Operations ====================

    @abstractmethod
    async def batch_upsert_records(
        self,
        records: List,
        transaction: Optional[str] = None
    ) -> None:
        """
        Batch upsert records (base record + specific type + IS_OF_TYPE edge).

        High-level method that handles:
        1. Upserting base record to records collection
        2. Upserting specific type (files, mails, etc.)
        3. Creating IS_OF_TYPE edges

        Args:
            records (List[Record]): List of Record objects
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def create_record_relation(
        self,
        from_record_id: str,
        to_record_id: str,
        relation_type: str,
        transaction: Optional[str] = None
    ) -> None:
        """
        Create a relation edge between two records.

        Args:
            from_record_id (str): Source record ID
            to_record_id (str): Target record ID
            relation_type (str): Type of relation (e.g., 'PARENT_CHILD', 'ATTACHMENT')
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def batch_upsert_record_groups(
        self,
        record_groups: List,
        transaction: Optional[str] = None
    ) -> None:
        """
        Batch upsert record groups (folders/spaces/categories).

        Args:
            record_groups (List[RecordGroup]): List of RecordGroup objects
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def create_record_group_relation(
        self,
        record_id: str,
        record_group_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """
        Create BELONGS_TO edge from record to record group.

        Args:
            record_id (str): Record ID
            record_group_id (str): Record group ID
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def create_record_groups_relation(
        self,
        child_id: str,
        parent_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """
        Create BELONGS_TO edge from child record group to parent record group.

        Args:
            child_id (str): Child record group ID
            parent_id (str): Parent record group ID
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def create_inherit_permissions_relation_record_group(
        self,
        record_id: str,
        record_group_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """
        Create INHERIT_PERMISSIONS edge from record to record group.

        Args:
            record_id (str): Record ID
            record_group_id (str): Record group ID
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def batch_upsert_record_permissions(
        self,
        record_id: str,
        permissions: List[Dict],
        transaction: Optional[str] = None
    ) -> None:
        """
        Batch upsert permissions for a record.

        Args:
            record_id (str): Record ID
            permissions (List[Dict]): List of permission data
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def get_file_permissions(
        self,
        file_key: str,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get all permissions for a file.

        Args:
            file_key (str): File key
            transaction (Optional[Any]): Optional transaction context

        Returns:
            List[Dict]: List of permissions
        """
        pass

    @abstractmethod
    async def get_first_user_with_permission_to_node(
        self,
        node_key: str,
        transaction: Optional[str] = None
    ) -> Optional['User']:
        """
        Get the first user with permission to a node.

        Args:
            node_key (str): Node key
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[str]: User ID if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_users_with_permission_to_node(
        self,
        node_key: str,
        transaction: Optional[str] = None
    ) -> List['User']:
        """
        Get all users with permission to a node.

        Args:
            node_key (str): Node key
            transaction (Optional[Any]): Optional transaction context

        Returns:
            List[str]: List of user IDs
        """
        pass

    @abstractmethod
    async def get_record_owner_source_user_email(
        self,
        record_id: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """
        Get the owner's source email for a record.

        Args:
            record_id (str): Record ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[str]: Owner email if found, None otherwise
        """
        pass

    # ==================== File/Parent Operations ====================

    @abstractmethod
    async def get_file_parents(
        self,
        file_key: str,
        transaction: Optional[str] = None
    ) -> List[str]:
        """
        Get all parent IDs for a file.

        Args:
            file_key (str): File key
            transaction (Optional[Any]): Optional transaction context

        Returns:
            List[str]: List of parent external IDs
        """
        pass

    # ==================== Sync Point Operations ====================

    @abstractmethod
    async def get_sync_point(
        self,
        key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get a sync point by key.

        Args:
            key (str): Sync point key
            collection (str): Collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Sync point data if found, None otherwise
        """
        pass

    @abstractmethod
    async def upsert_sync_point(
        self,
        sync_point_key: str,
        sync_point_data: Dict,
        collection: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Upsert a sync point.

        Args:
            sync_point_key (str): Sync point key
            sync_point_data (Dict): Sync point data
            collection (str): Collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def remove_sync_point(
        self,
        key: List[str],
        collection: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Remove sync points by keys.

        Args:
            key (List[str]): List of sync point keys to remove
            collection (str): Collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    # ==================== Batch/Bulk Operations ====================

    @abstractmethod
    async def batch_upsert_app_users(
        self,
        users: List,
        transaction: Optional[str] = None
    ) -> None:
        """
        Batch upsert app users with org and app relations.

        Creates users if they don't exist, creates org relation and user-app relation.

        Args:
            users (List[AppUser]): List of AppUser objects
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def batch_upsert_user_groups(
        self,
        user_groups: List,
        transaction: Optional[str] = None
    ) -> None:
        """
        Batch upsert user groups.

        Args:
            user_groups (List[AppUserGroup]): List of AppUserGroup objects
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def batch_upsert_app_roles(
        self,
        app_roles: List,
        transaction: Optional[str] = None
    ) -> None:
        """
        Batch upsert app roles.

        Args:
            app_roles (List[AppRole]): List of AppRole objects
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def batch_upsert_orgs(
        self,
        orgs: List[Dict],
        transaction: Optional[str] = None
    ) -> None:
        """
        Batch upsert organizations.

        Args:
            orgs (List[Dict]): List of organization data
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def batch_upsert_domains(
        self,
        domains: List[Dict],
        transaction: Optional[str] = None
    ) -> None:
        """
        Batch upsert domains.

        Args:
            domains (List[Dict]): List of domain data
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def batch_upsert_anyone(
        self,
        anyone: List[Dict],
        transaction: Optional[str] = None
    ) -> None:
        """
        Batch upsert 'anyone' permission entities.

        Args:
            anyone (List[Dict]): List of anyone entities
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def batch_upsert_anyone_with_link(
        self,
        anyone_with_link: List[Dict],
        transaction: Optional[str] = None
    ) -> None:
        """
        Batch upsert 'anyone with link' permission entities.

        Args:
            anyone_with_link (List[Dict]): List of anyone with link entities
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def batch_upsert_anyone_same_org(
        self,
        anyone_same_org: List[Dict],
        transaction: Optional[str] = None
    ) -> None:
        """
        Batch upsert 'anyone same org' permission entities.

        Args:
            anyone_same_org (List[Dict]): List of anyone same org entities
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def batch_create_user_app_edges(
        self,
        edges: List[Dict]
    ) -> int:
        """
        Batch create user-app relationship edges.

        Args:
            edges (List[Dict]): List of edge data

        Returns:
            int: Number of edges created
        """
        pass

    # ==================== Entity ID Operations ====================

    @abstractmethod
    async def get_entity_id_by_email(
        self,
        email: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """
        Get entity ID (user or group) by email.

        Args:
            email (str): Email address
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[str]: Entity ID if found, None otherwise
        """
        pass

    @abstractmethod
    async def bulk_get_entity_ids_by_email(
        self,
        emails: List[str],
        transaction: Optional[str] = None
    ) -> Dict[str, Tuple[str, str, str]]:
        """
        Bulk get entity IDs for multiple emails.

        Args:
            emails (List[str]): List of email addresses
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Dict[str, Tuple[str, str, str]]: Map of email to (entity_id, collection, type)
        """
        pass

    # ==================== Connector-Specific Operations ====================

    @abstractmethod
    async def process_file_permissions(
        self,
        org_id: str,
        file_key: str,
        permissions: List[Dict],
        transaction: Optional[str] = None
    ) -> None:
        """
        Process and upsert file permissions.

        Args:
            org_id (str): Organization ID
            file_key (str): File key
            permissions (List[Dict]): List of permission data
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def delete_records_and_relations(
        self,
        record_key: str,
        hard_delete: bool = False,
        transaction: Optional[str] = None
    ) -> None:
        """
        Delete a record and all its relations.

        Args:
            record_key (str): Record key to delete
            hard_delete (bool): Whether to permanently delete or mark as deleted
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def delete_record(
        self,
        record_id: str,
        user_id: str,
        transaction: Optional[str] = None
    ) -> Dict:
        """
        Main entry point for record deletion - routes to connector-specific methods.

        Args:
            record_id (str): Record ID to delete
            user_id (str): User ID performing the deletion
            transaction (Optional[str]): Optional transaction context

        Returns:
            Dict: Result with success status and reason
        """
        pass

    @abstractmethod
    async def delete_record_by_external_id(
        self,
        connector_name: Connectors,
        external_id: str,
        user_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """
        Delete a record by external ID.

        Args:
            connector_name (Connectors): Connector type
            external_id (str): External record ID
            user_id (str): User ID performing the deletion
            transaction (Optional[str]): Optional transaction context
        """
        pass

    @abstractmethod
    async def remove_user_access_to_record(
        self,
        connector_name: Connectors,
        external_id: str,
        user_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """
        Remove a user's access to a record (for inbox-based deletions).

        Args:
            connector_name (Connectors): Connector type
            external_id (str): External record ID
            user_id (str): User ID to remove access from
            transaction (Optional[str]): Optional transaction context
        """
        pass

    @abstractmethod
    async def get_key_by_external_file_id(
        self,
        external_file_id: str
    ) -> Optional[str]:
        """
        Get internal key by external file ID.

        Args:
            external_file_id (str): External file ID

        Returns:
            Optional[str]: Internal key if found, None otherwise
        """
        pass

    @abstractmethod
    async def organization_exists(
        self,
        organization_name: str
    ) -> bool:
        """
        Check if an organization exists.

        Args:
            organization_name (str): Organization name

        Returns:
            bool: True if exists, False otherwise
        """
        pass

    # ==================== App Operations ====================

    @abstractmethod
    async def get_app_by_name(
        self,
        name: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get an app by its name (case-insensitive).

        Args:
            name (str): App name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: App data if found, None otherwise
        """
        pass

    # ==================== Sync State Operations ====================

    @abstractmethod
    async def get_user_sync_state(
        self,
        user_email: str,
        service_type: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get user's sync state for a specific service.

        Args:
            user_email (str): User email
            service_type (str): Service/connector type
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Sync state relation document if found, None otherwise
        """
        pass

    @abstractmethod
    async def update_user_sync_state(
        self,
        user_email: str,
        state: str,
        service_type: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Update user's sync state for a specific service.

        Args:
            user_email (str): User email
            state (str): Sync state (NOT_STARTED, RUNNING, PAUSED, COMPLETED)
            service_type (str): Service/connector type
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Updated relation document if successful, None otherwise
        """
        pass

    @abstractmethod
    async def get_drive_sync_state(
        self,
        drive_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get drive's sync state.

        Args:
            drive_id (str): Drive ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Drive document with sync state if found, None otherwise
        """
        pass

    @abstractmethod
    async def update_drive_sync_state(
        self,
        drive_id: str,
        state: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Update drive's sync state.

        Args:
            drive_id (str): Drive ID
            state (str): Sync state (NOT_STARTED, RUNNING, PAUSED, COMPLETED)
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Updated drive document if successful, None otherwise
        """
        pass

    # ==================== Page Token Operations ====================

    @abstractmethod
    async def store_page_token(
        self,
        channel_id: str,
        resource_id: str,
        user_email: str,
        token: str,
        expiration: Optional[str] = None,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Store page token for a channel/resource.

        Args:
            channel_id (str): Channel ID
            resource_id (str): Resource ID
            user_email (str): User email
            token (str): Page token
            expiration (Optional[str]): Token expiration
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Stored token document if successful, None otherwise
        """
        pass

    @abstractmethod
    async def get_page_token_db(
        self,
        channel_id: Optional[str] = None,
        resource_id: Optional[str] = None,
        user_email: Optional[str] = None,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get page token for specific channel/resource/user.

        Args:
            channel_id (Optional[str]): Channel ID filter
            resource_id (Optional[str]): Resource ID filter
            user_email (Optional[str]): User email filter
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Token document if found, None otherwise
        """
        pass

    # ==================== Utility Operations ====================

    @abstractmethod
    async def check_collection_has_document(
        self,
        collection_name: str,
        document_id: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Check if a document exists in a collection.

        Args:
            collection_name (str): Collection name
            document_id (str): Document ID/key
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if document exists, False otherwise
        """
        pass

    @abstractmethod
    async def check_edge_exists(
        self,
        from_key: str,
        to_key: str,
        edge_collection: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Check if an edge exists between two nodes.

        Args:
            from_key (str): Source node key
            to_key (str): Target node key
            edge_collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if edge exists, False otherwise
        """
        pass

    @abstractmethod
    async def get_failed_records_with_active_users(
        self,
        org_id: str,
        connector_name: Connectors
    ) -> List[Dict]:
        """
        Get failed records along with their active users who have permissions.

        Generic method for getting records with indexing status FAILED and their permitted active users.

        Args:
            org_id (str): Organization ID
            connector_name (Connectors): Connector name

        Returns:
            List[Dict]: List of dictionaries with 'record' and 'users' keys
        """
        pass

    @abstractmethod
    async def get_failed_records_by_org(
        self,
        org_id: str,
        connector_name: Connectors
    ) -> List[Dict]:
        """
        Get all failed records for an organization and connector.

        Generic method for getting records with indexing status FAILED.

        Args:
            org_id (str): Organization ID
            connector_name (Connectors): Connector name

        Returns:
            List[Dict]: List of failed record documents
        """
        pass

