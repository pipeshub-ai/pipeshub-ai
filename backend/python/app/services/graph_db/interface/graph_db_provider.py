"""
Comprehensive Graph Database Provider Interface

This interface defines all database operations needed by the application,
abstracting away the specific database implementation (ArangoDB, Neo4j, etc.).

All methods support optional transaction parameter for atomic operations.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from app.models.entities import Person

if TYPE_CHECKING:
    from fastapi import Request

    from app.models.entities import (
        AppRole,
        AppUser,
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

    Data Format Specifications:

    1. Node/Document Format:
       Nodes use a generic 'id' field for identification (not database-specific like '_key').
       Example:
       {
           "id": "user123",              # Generic node identifier
           "orgId": "org456",
           "email": "user@example.com",
           # ... other node properties
       }

       Implementation Note: Providers translate 'id' to their native field:
       - ArangoDB: 'id' → '_key'
       - Neo4j: 'id' → 'id' (native)

    2. Edge/Relationship Format:
       Edges use a generic format with separate fields for source/target nodes:
       {
           "from_id": "user123",           # Source node ID (without collection prefix)
           "from_collection": "users",     # Source collection/label name
           "to_id": "record456",           # Target node ID (without collection prefix)
           "to_collection": "records",     # Target collection/label name
           "role": "READER",               # Edge property example
           "type": "PERMISSION",           # Edge property example
           "createdAtTimestamp": 1234567890,
           # ... other edge properties
       }

       Implementation Note: Providers translate to their native format:
       - ArangoDB: Combines into '_from': "users/user123", '_to': "records/record456"
       - Neo4j: Creates relationship with startNode and endNode references

    3. Collection/Label Names:
       Collection names are database-agnostic strings (e.g., "users", "records", "permissions").
       Providers map these to their native concepts (collections in Arango, labels in Neo4j).

    4. Backward Compatibility:
       During transition, providers should handle both old format (with _key, _from, _to)
       and new generic format to ensure smooth migration.
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

    @abstractmethod
    async def ensure_schema(self) -> bool:
        """
        Ensure database schema is initialized (collections, graphs, and any
        required seed data). Should be called only from the connector service
        during startup when schema init is enabled.

        Returns:
            bool: True if schema was ensured successfully, False otherwise
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
            str: Transaction ID
        """
        pass

    @abstractmethod
    async def commit_transaction(self, transaction: str) -> None:
        """Commit a database transaction."""
        pass

    @abstractmethod
    async def rollback_transaction(self, transaction: str) -> None:
        """Roll back a database transaction."""
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
            document_key (str): The document's unique identifier (generic 'id')
            collection (str): Collection/table name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Document data with 'id' field if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_record_by_id(
        self,
        id: str,
        transaction: Optional[str] = None,
    ) -> Optional["Record"]:
        """
        Get record by internal ID (_key) with associated type document (file/mail/etc.).

        Args:
            id: Internal record ID (_key)
            transaction: Optional transaction ID

        Returns:
            Optional[Record]: Typed Record instance (FileRecord, MailRecord, etc.) or None
        """
        pass

    @abstractmethod
    async def get_all_documents(
        self,
        collection: str,
        transaction: Optional[str] = None,
    ) -> List[Dict]:
        """
        Get all documents from a collection.

        Args:
            collection: Collection name
            transaction: Optional transaction ID

        Returns:
            List[Dict]: List of all documents in the collection
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
            nodes (List[Dict]): List of documents to upsert. Each document should have 'id' field:
                {
                    "id": "user123",           # Generic node identifier
                    "orgId": "org456",
                    # ... other node properties
                }
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
        collection: str,
        node_updates: Dict,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Update a single node/document.

        Args:
            key (str): Document key to update
            collection (str): Collection/table name
            node_updates (Dict): Fields to update
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
            edges (List[Dict]): List of edges in generic format:
                {
                    "from_id": "user123",           # Source node ID
                    "from_collection": "users",     # Source collection
                    "to_id": "record456",           # Target node ID
                    "to_collection": "records",     # Target collection
                    # ... additional edge properties
                }
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def get_edge(
        self,
        from_id: str,
        from_collection: str,
        to_id: str,
        to_collection: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get an edge/relationship between two nodes.

        Args:
            from_id (str): Source node ID
            from_collection (str): Source node collection name
            to_id (str): Target node ID
            to_collection (str): Target node collection name
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Edge data in generic format if found, None otherwise
        """
        pass

    @abstractmethod
    async def delete_edge(
        self,
        from_id: str,
        from_collection: str,
        to_id: str,
        to_collection: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Delete an edge/relationship between two nodes.

        Args:
            from_id (str): Source node ID
            from_collection (str): Source node collection name
            to_id (str): Target node ID
            to_collection (str): Target node collection name
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def delete_edges_from(
        self,
        from_id: str,
        from_collection: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> int:
        """
        Delete all edges originating from a node.

        Args:
            from_id (str): Source node ID
            from_collection (str): Source node collection name
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            int: Number of edges deleted
        """
        pass

    @abstractmethod
    async def delete_edges_by_relationship_types(
        self,
        from_id: str,
        from_collection: str,
        collection: str,
        relationship_types: List[str],
        transaction: Optional[str] = None
    ) -> int:
        """
        Delete edges from a node by relationship types.

        Args:
            from_id (str): Source node ID
            from_collection (str): Source node collection name
            collection (str): Edge collection name
            relationship_types (List[str]): List of relationship type values to delete
            transaction (Optional[Any]): Optional transaction context

        Returns:
            int: Number of edges deleted
        """
        pass

    @abstractmethod
    async def delete_edges_to(
        self,
        to_id: str,
        to_collection: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> int:
        """
        Delete all edges pointing to a node.

        Args:
            to_id (str): Target node ID
            to_collection (str): Target node collection name
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            int: Number of edges deleted
        """
        pass

    @abstractmethod
    async def delete_edges_to_groups(
        self,
        from_id: str,
        from_collection: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> int:
        """
        Delete edges from a node to group nodes.

        Args:
            from_id (str): Source node ID
            from_collection (str): Source node collection name
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            int: Number of edges deleted
        """
        pass

    @abstractmethod
    async def delete_edges_between_collections(
        self,
        from_id: str,
        from_collection: str,
        edge_collection: str,
        to_collection: str,
        transaction: Optional[str] = None
    ) -> int:
        """
        Delete edges between a node and nodes in a specific collection.

        Args:
            from_id (str): Source node ID
            from_collection (str): Source node collection name
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
    async def get_edges_from_node(
        self,
        node_id: str,
        edge_collection: str,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get all edges originating from a specific node.

        Generic method that works with any edge collection.

        Args:
            node_id (str): Source node ID (e.g., "groups/123")
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
        connector_id: str,
        path: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get a record by its file path.

        Args:
            connector_id (str): Connector ID
            path (str): File/record path
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Record data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_record_by_external_id(
        self,
        connector_id: str,
        external_id: str,
        transaction: Optional[str] = None
    ) -> Optional['Record']:
        """
        Get a record by its external ID from the source system.

        Args:
            connector_id (str): Connector ID
            external_id (str): External record ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Record data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_record_by_external_revision_id(
        self,
        connector_id: str,
        external_revision_id: str,
        transaction: Optional[str] = None
    ) -> Optional['Record']:
        """
        Get a record by its external revision ID (e.g., etag for S3).

        Args:
            connector_id (str): Connector ID
            external_revision_id (str): External revision ID (e.g., etag)
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Record]: Record data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_record_key_by_external_id(
        self,
        external_id: str,
        connector_id: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """
        Get a record's internal key by its external ID.

        Args:
            external_id (str): External record ID
            connector_id (str): Connector ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[str]: Record key if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_records_by_status(
        self,
        org_id: str,
        connector_id: str,
        status_filters: List[str],
        limit: Optional[int] = None,
        offset: int = 0,
        transaction: Optional[str] = None
    ) -> List['Record']:
        """
        Get records by their indexing status.

        Args:
            org_id (str): Organization ID
            connector_id (str): Connector ID
            status_filters (List[str]): List of status values to filter by
            limit (Optional[int]): Maximum number of records to return
            offset (int): Number of records to skip
            transaction (Optional[Any]): Optional transaction context

        Returns:
            List[Dict]: List of records matching the status filters
        """
        pass

    @abstractmethod
    async def get_records(
        self,
        user_id: str,
        org_id: str,
        skip: int,
        limit: int,
        search: Optional[str],
        record_types: Optional[List[str]],
        origins: Optional[List[str]],
        connectors: Optional[List[str]],
        indexing_status: Optional[List[str]],
        permissions: Optional[List[str]],
        date_from: Optional[int],
        date_to: Optional[int],
        sort_by: str,
        sort_order: str,
        source: str,
    ) -> Tuple[List[Dict], int, Dict]:
        """
        List all records the user can access.

        Args:
            user_id: External user ID
            org_id: Organization ID
            skip: Number of records to skip (pagination)
            limit: Maximum records to return
            search: Optional search string
            record_types: Optional list of record types to filter
            origins: Optional list of origins to filter
            connectors: Optional list of connector IDs to filter
            indexing_status: Optional list of indexing statuses to filter
            permissions: Optional list of permission roles to filter
            date_from: Optional start timestamp
            date_to: Optional end timestamp
            sort_by: Field to sort by
            sort_order: Sort order (ASC/DESC)
            source: Data source filter ('all', 'local', 'connector')

        Returns:
            Tuple of (records list, total count, available_filters dict)
        """
        pass

    @abstractmethod
    async def reindex_single_record(
        self,
        record_id: str,
        user_id: str,
        org_id: str,
        request: Optional["Request"] = None,
        depth: int = 0,
    ) -> Dict:
        """
        Validate and prepare reindex for a single record (permission checks, reset status).
        Does NOT publish events; caller should publish after success.

        Args:
            record_id: Record ID to reindex
            user_id: External user ID
            org_id: Organization ID
            request: Optional request (for signature compatibility)
            depth: Depth for children (0 = only this record)

        Returns:
            Dict: success, recordId, recordName, connector, userRole; or error code/reason
        """
        pass

    @abstractmethod
    async def reindex_record_group_records(
        self,
        record_group_id: str,
        depth: int,
        user_id: str,
        org_id: str,
    ) -> Dict:
        """
        Validate record group and user permissions for reindexing.
        Does NOT publish events; caller should publish.

        Args:
            record_group_id: Record group ID
            depth: Depth for traversing children
            user_id: External user ID
            org_id: Organization ID

        Returns:
            Dict: success, connectorId, connectorName, depth, recordGroupId; or error code/reason
        """
        pass

    @abstractmethod
    async def get_documents_by_status(
        self,
        collection: str,
        status: str,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get all documents with a specific indexing status.

        Args:
            collection (str): Collection name
            status (str): Status to filter by
            transaction (Optional[str]): Optional transaction context

        Returns:
            List[Dict]: List of matching documents
        """
        pass

    @abstractmethod
    async def get_record_by_conversation_index(
        self,
        connector_id: str,
        conversation_index: str,
        thread_id: str,
        org_id: str,
        user_id: str,
        transaction: Optional[str] = None
    ) -> Optional['Record']:
        """
        Get a record by conversation index (for email/chat connectors).

        Args:
            connector_id (str): Connector ID
            conversation_index (str): Conversation index
            thread_id (str): Thread ID
            org_id (str): Organization ID
            user_id (str): User ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Record data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_record_by_issue_key(
        self,
        connector_id: str,
        issue_key: str,
        transaction: Optional[str] = None
    ) -> Optional['Record']:
        """
        Get record by Jira issue key (e.g., PROJ-123) by searching weburl pattern.

        Args:
            connector_id: Connector ID
            issue_key: Jira issue key (e.g., "PROJ-123")
            transaction: Optional transaction ID

        Returns:
            Optional[Record]: Record if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_record_by_weburl(
        self,
        weburl: str,
        org_id: Optional[str] = None,
        transaction: Optional[str] = None
    ) -> Optional['Record']:
        """
        Get record by weburl (exact match).

        Args:
            weburl: Web URL to search for
            org_id: Optional organization ID to filter by
            transaction: Optional transaction ID

        Returns:
            Optional[Record]: Record if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_records_by_parent(
        self,
        connector_id: str,
        parent_external_record_id: str,
        record_type: Optional[str] = None,
        transaction: Optional[str] = None
    ) -> List['Record']:
        """
        Get all child records for a parent record by parent_external_record_id.
        Optionally filter by record_type.

        Args:
            connector_id (str): Connector ID
            parent_external_record_id (str): Parent record's external ID
            record_type (Optional[str]): Optional filter by record type (e.g., "COMMENT", "FILE", "TICKET")
            transaction (Optional[Any]): Optional transaction context

        Returns:
            List[Dict]: List of child records
        """
        pass

    @abstractmethod
    async def get_records_by_record_group(
        self,
        record_group_id: str,
        connector_id: str,
        org_id: str,
        depth: int,
        user_key: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        transaction: Optional[str] = None
    ) -> List['Record']:
        """
        Get all records belonging to a record group up to a specified depth.
        Uses belongsTo edges for nested record group traversal and optional
        permission checks via the knowledge hub permission model.

        Includes:
        - Records directly in the group (via belongsTo edges)
        - Records in nested record groups up to depth levels (via belongsTo edges)

        Args:
            record_group_id (str): Record group ID
            connector_id (str): Connector ID (all records in group are from same connector)
            org_id (str): Organization ID (for security filtering)
            depth (int): Depth for traversing children and nested record groups
                        (-1 = unlimited, 0 = only direct records, 1 = direct + 1 level nested, etc.)
            user_key (Optional[str]): User key for permission filtering. When provided,
                        only records the user has permission to access are returned.
                        Uses the same permission model as knowledge hub (10 permission paths).
            limit (Optional[int]): Maximum number of records to return (for pagination)
            offset (int): Number of records to skip (for pagination)
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[Record]: List of properly typed Record instances
        """
        pass

    @abstractmethod
    async def get_records_by_parent_record(
        self,
        parent_record_id: str,
        connector_id: str,
        org_id: str,
        depth: int,
        user_key: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        transaction: Optional[str] = None
    ) -> List['Record']:
        """
        Get all child records of a parent record (folder) up to a specified depth.
        Uses graph traversal on record relations. Parent record is always included.

        Args:
            parent_record_id (str): Record ID of the parent (folder)
            connector_id (str): Connector ID (all records should be from same connector)
            org_id (str): Organization ID (for security filtering)
            depth (int): Depth for traversing children
                        (-1 = unlimited, 0 = only parent, 1 = direct children,
                         2 = children + grandchildren, etc.)
            user_key (Optional[str]): User key for permission filtering. When provided,
                        only records the user has permission to access are returned.
                        Uses the same permission model as knowledge hub (10 permission paths).
            limit (Optional[int]): Maximum number of records to return (for pagination)
            offset (int): Number of records to skip (for pagination)
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[Record]: List of properly typed Record instances
        """
        pass

    # ==================== Record Group Operations ====================

    @abstractmethod
    async def get_record_group_by_external_id(
        self,
        connector_id: str,
        external_id: str,
        transaction: Optional[str] = None
    ) -> Optional['RecordGroup']:
        """
        Get a record group by its external ID.

        Args:
            connector_id (str): Connector ID
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
        connector_id: str,
        transaction: Optional[str] = None
    ) -> Optional['User']:
        """
        Get a user by their source system ID.

        Args:
            source_user_id (str): User ID in source system
            connector_id (str): Connector ID
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
    async def get_account_type(self, org_id: str) -> Optional[str]:
        """
        Get account type for an organization.

        Args:
            org_id: Organization ID

        Returns:
            Optional[str]: Account type ('individual' or 'business'), or None
        """
        pass

    @abstractmethod
    async def get_connector_stats(
        self,
        org_id: str,
        connector_id: str,
    ) -> Dict:
        """
        Get connector statistics for a specific connector.

        Args:
            org_id: Organization ID
            connector_id: Connector (app) ID

        Returns:
            Dict: success, message, data (stats and byRecordType)
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
        connector_id: str,
        transaction: Optional[str] = None
    ) -> Optional['AppUser']:
        """
        Get an app-specific user by email.

        Args:
            email (str): User email
            connector_id (str): Connector ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: App user data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_app_users(
        self,
        org_id: str,
        connector_id: str
    ) -> List[Dict]:
        """
        Get all users for a specific connector in an organization.

        Args:
            org_id (str): Organization ID
            connector_id (str): Connector ID

        Returns:
            List[Dict]: List of app users
        """
        pass

    @abstractmethod
    async def list_user_knowledge_bases(
        self,
        user_id: str,
        org_id: str,
        skip: int,
        limit: int,
        search: Optional[str] = None,
        permissions: Optional[List[str]] = None,
        sort_by: str = "name",
        sort_order: str = "asc",
        transaction: Optional[str] = None,
    ) -> Tuple[List[Dict], int, Dict]:
        """
        List knowledge bases with pagination, search, and filtering.
        Includes both direct user permissions and team-based permissions.

        Args:
            user_id: User ID
            org_id: Organization ID
            skip: Pagination skip
            limit: Pagination limit
            search: Optional search term for KB name
            permissions: Optional filter by permission roles
            sort_by: Sort field (name, createdAtTimestamp, updatedAtTimestamp, userRole)
            sort_order: Sort direction (asc, desc)
            transaction: Optional transaction ID

        Returns:
            Tuple of (list of KB dicts, total count, available_filters dict)
        """
        pass

    @abstractmethod
    async def get_kb_children(
        self,
        kb_id: str,
        skip: int,
        limit: int,
        level: int = 1,
        search: Optional[str] = None,
        record_types: Optional[List[str]] = None,
        origins: Optional[List[str]] = None,
        connectors: Optional[List[str]] = None,
        indexing_status: Optional[List[str]] = None,
        sort_by: str = "name",
        sort_order: str = "asc",
        transaction: Optional[str] = None,
    ) -> Dict:
        """
        Get KB root contents with folders_first pagination.

        Returns:
            Dict with success, container, folders, records, totalCount, counts,
            availableFilters, paginationMode; or { success: False, reason: str }.
        """
        pass

    @abstractmethod
    async def get_folder_children(
        self,
        kb_id: str,
        folder_id: str,
        skip: int,
        limit: int,
        level: int = 1,
        search: Optional[str] = None,
        record_types: Optional[List[str]] = None,
        origins: Optional[List[str]] = None,
        connectors: Optional[List[str]] = None,
        indexing_status: Optional[List[str]] = None,
        sort_by: str = "name",
        sort_order: str = "asc",
        transaction: Optional[str] = None,
    ) -> Dict:
        """
        Get folder contents with folders_first pagination.

        Returns:
            Dict with success, container, folders, records, totalCount, counts,
            availableFilters, paginationMode; or { success: False, reason: str }.
        """
        pass

    @abstractmethod
    async def get_knowledge_base(
        self,
        kb_id: str,
        user_id: str,
        transaction: Optional[str] = None,
    ) -> Optional[Dict]:
        """Get knowledge base with user permissions."""
        pass

    @abstractmethod
    async def update_knowledge_base(
        self,
        kb_id: str,
        updates: Dict,
        transaction: Optional[str] = None,
    ) -> bool:
        """Update knowledge base."""
        pass

    @abstractmethod
    async def delete_knowledge_base(
        self,
        kb_id: str,
        transaction: Optional[str] = None,
    ) -> bool:
        """Delete a knowledge base and all nested content."""
        pass

    @abstractmethod
    async def _validate_folder_creation(self, kb_id: str, user_id: str) -> Dict:
        """Shared validation logic for folder creation."""
        pass

    @abstractmethod
    async def find_folder_by_name_in_parent(
        self,
        kb_id: str,
        folder_name: str,
        parent_folder_id: Optional[str] = None,
        transaction: Optional[str] = None,
    ) -> Optional[Dict]:
        """Find a folder by name within a specific parent (KB root or folder)."""
        pass

    @abstractmethod
    async def create_folder(
        self,
        kb_id: str,
        folder_name: str,
        org_id: str,
        parent_folder_id: Optional[str] = None,
        transaction: Optional[str] = None,
    ) -> Optional[Dict]:
        """Create folder with proper RECORDS document and edges."""
        pass

    @abstractmethod
    async def get_folder_contents(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[str] = None,
    ) -> Optional[Dict]:
        """Get folder contents (container, folders, records)."""
        pass

    @abstractmethod
    async def validate_folder_in_kb(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[str] = None,
    ) -> bool:
        """Validate that a folder exists and belongs to the KB."""
        pass

    @abstractmethod
    async def update_folder(
        self,
        folder_id: str,
        updates: Dict,
        transaction: Optional[str] = None,
    ) -> bool:
        """Update folder."""
        pass

    @abstractmethod
    async def delete_folder(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Delete a folder and all nested content."""
        pass

    @abstractmethod
    async def update_record(
        self,
        record_id: str,
        user_id: str,
        updates: Dict,
        file_metadata: Optional[Dict] = None,
        transaction: Optional[str] = None,
    ) -> Optional[Dict]:
        """Update a record by ID with automatic KB and permission detection."""
        pass

    @abstractmethod
    async def delete_records(
        self,
        record_ids: List[str],
        kb_id: str,
        folder_id: Optional[str] = None,
        transaction: Optional[str] = None,
    ) -> Dict:
        """Delete multiple records and publish delete events."""
        pass

    @abstractmethod
    async def create_kb_permissions(
        self,
        kb_id: str,
        requester_id: str,
        user_ids: List[str],
        team_ids: List[str],
        role: str,
    ) -> Dict:
        """Create KB permissions for users and teams."""
        pass

    @abstractmethod
    async def count_kb_owners(
        self,
        kb_id: str,
        transaction: Optional[str] = None,
    ) -> int:
        """Count the number of owners for a knowledge base."""
        pass

    @abstractmethod
    async def remove_kb_permission(
        self,
        kb_id: str,
        user_ids: List[str],
        team_ids: List[str],
        transaction: Optional[str] = None,
    ) -> bool:
        """Remove permissions for multiple users and teams from a KB."""
        pass

    @abstractmethod
    async def get_user_kb_permission(
        self,
        kb_id: str,
        user_id: str,
        transaction: Optional[str] = None,
    ) -> Optional[str]:
        """Get user's permission role on a KB (direct or via team)."""
        pass

    @abstractmethod
    async def upload_records(
        self,
        kb_id: str,
        user_id: str,
        org_id: str,
        files: List[Dict],
        parent_folder_id: Optional[str] = None,
    ) -> Dict:
        """Upload records to KB root or a folder."""
        pass

    @abstractmethod
    async def is_record_folder(self, record_id: str, transaction: Optional[str] = None) -> bool:
        """Return True if the record is a folder (has FILES doc with isFile false)."""
        pass

    @abstractmethod
    async def get_record_parent_info(
        self,
        record_id: str,
        transaction: Optional[str] = None,
    ) -> Optional[Dict]:
        """Get parent folder/kb info for a record."""
        pass

    @abstractmethod
    async def is_record_descendant_of(
        self,
        record_id: str,
        ancestor_id: str,
        transaction: Optional[str] = None,
    ) -> bool:
        """Return True if record is a descendant of ancestor (folder)."""
        pass

    @abstractmethod
    async def delete_parent_child_edge_to_record(
        self,
        record_id: str,
        transaction: Optional[str] = None,
    ) -> bool:
        """Delete the incoming PARENT_CHILD edge to a record."""
        pass

    @abstractmethod
    async def create_parent_child_edge(
        self,
        parent_id: str,
        child_id: str,
        transaction: Optional[str] = None,
    ) -> bool:
        """Create PARENT_CHILD edge from parent to child record."""
        pass

    @abstractmethod
    async def update_record_external_parent_id(
        self,
        record_id: str,
        new_parent_id: str,
        transaction: Optional[str] = None,
    ) -> bool:
        """Update record's externalParentId."""
        pass

    @abstractmethod
    async def get_kb_permissions(
        self,
        kb_id: str,
        user_ids: Optional[List[str]] = None,
        team_ids: Optional[List[str]] = None,
        transaction: Optional[str] = None,
    ) -> Dict[str, Dict[str, str]]:
        """Get current roles for users and teams on a KB."""
        pass

    @abstractmethod
    async def update_kb_permission(
        self,
        kb_id: str,
        requester_id: str,
        user_ids: List[str],
        team_ids: List[str],
        new_role: str,
    ) -> Optional[Dict]:
        """Update permissions for users/teams on a KB."""
        pass

    @abstractmethod
    async def list_kb_permissions(
        self,
        kb_id: str,
        transaction: Optional[str] = None,
    ) -> List[Dict]:
        """List all permissions for a KB with entity details."""
        pass

    @abstractmethod
    async def list_all_records(
        self,
        user_id: str,
        org_id: str,
        skip: int,
        limit: int,
        search: Optional[str],
        record_types: Optional[List[str]],
        origins: Optional[List[str]],
        connectors: Optional[List[str]],
        indexing_status: Optional[List[str]],
        permissions: Optional[List[str]],
        date_from: Optional[int],
        date_to: Optional[int],
        sort_by: str,
        sort_order: str,
        source: str,
    ) -> Tuple[List[Dict], int, Dict]:
        """List all records the user can access. Returns (records, total_count, available_filters)."""
        pass

    @abstractmethod
    async def list_kb_records(
        self,
        kb_id: str,
        user_id: str,
        org_id: str,
        skip: int,
        limit: int,
        search: Optional[str],
        record_types: Optional[List[str]],
        origins: Optional[List[str]],
        connectors: Optional[List[str]],
        indexing_status: Optional[List[str]],
        date_from: Optional[int],
        date_to: Optional[int],
        sort_by: str,
        sort_order: str,
        folder_id: Optional[str] = None,
    ) -> Tuple[List[Dict], int, Dict]:
        """List records in a KB. Returns (records, total_count, available_filters)."""
        pass

    # ==================== Group Operations ====================

    @abstractmethod
    async def get_user_group_by_external_id(
        self,
        connector_id: str,
        external_id: str,
        transaction: Optional[str] = None
    ) -> Optional['AppUserGroup']:
        """
        Get a user group by external ID.

        Args:
            connector_id (str): Connector ID
            external_id (str): External group ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Group data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_user_groups(
        self,
        connector_id: str,
        org_id: str,
        transaction: Optional[str] = None
    ) -> List['AppUserGroup']:
        """
        Get all user groups for a connector in an organization.

        Args:
            connector_id (str): Connector ID
            org_id (str): Organization ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            List[Dict]: List of user groups
        """
        pass

    @abstractmethod
    async def batch_upsert_people(
        self,
        people: List[Person],
        transaction: Optional[str] = None
    ) -> None:
        """
        Upsert people to PEOPLE collection.

        Args:
            people (List[Person]): List of Person entities
            transaction (Optional[Any]): Optional transaction context

        Returns:
            None
        """
        pass

    @abstractmethod
    async def get_app_role_by_external_id(
        self,
        connector_id: str,
        external_id: str,
        transaction: Optional[str] = None
    ) -> Optional['AppRole']:
        """
        Get an app role by external ID.

        Args:
            connector_id (str): Connector ID
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
        active: bool = True,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get all organizations.

        Args:
            active (bool): Filter by active status
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[Dict]: List of organizations
        """
        pass

    @abstractmethod
    async def get_departments(
        self,
        org_id: Optional[str] = None,
        transaction: Optional[str] = None
    ) -> List[str]:
        """
        Get all departments that either have no org_id or match the given org_id.

        Args:
            org_id (Optional[str]): Organization ID to filter departments
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[str]: List of department names
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

    @abstractmethod
    async def find_duplicate_records(
        self,
        record_key: str,
        md5_checksum: str,
        record_type: Optional[str] = None,
        size_in_bytes: Optional[int] = None,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Find duplicate records based on MD5 checksum.
        This method queries the RECORDS collection and works for all record types.

        Args:
            record_key (str): The key of the current record to exclude from results
            md5_checksum (str): MD5 checksum of the record content
            record_type (Optional[str]): Optional record type to filter by
            size_in_bytes (Optional[int]): Optional file size in bytes to filter by
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[Dict]: List of duplicate records that match the criteria
        """
        pass

    @abstractmethod
    async def find_next_queued_duplicate(
        self,
        record_id: str,
        transaction: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        Find the next QUEUED duplicate record with the same md5 hash.
        Works with all record types by querying the RECORDS collection directly.

        Args:
            record_id (str): The record ID to use as reference for finding duplicates
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Optional[Dict]: The next queued record if found, None otherwise
        """
        pass

    @abstractmethod
    async def update_queued_duplicates_status(
        self,
        record_id: str,
        new_indexing_status: str,
        virtual_record_id: Optional[str] = None,
        transaction: Optional[str] = None,
    ) -> int:
        """
        Find all QUEUED duplicate records with the same md5 hash and update their status.

        Args:
            record_id (str): The record ID to use as reference for finding duplicates
            new_indexing_status (str): The new indexing status to set
            virtual_record_id (Optional[str]): Optional virtual record ID to set
            transaction (Optional[str]): Optional transaction ID

        Returns:
            int: Number of records updated
        """
        pass

    @abstractmethod
    async def copy_document_relationships(
        self,
        source_key: str,
        target_key: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Copy all relationships (edges) from source document to target document.
        This includes departments, categories, subcategories, languages, and topics.

        Args:
            source_key (str): Key/ID of the source document
            target_key (str): Key/ID of the target document
            transaction (Optional[str]): Optional transaction ID

        Returns:
            bool: True if successful, False otherwise
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
            relation_type (str): Type of relation (e.g., "PARENT_CHILD", "ATTACHMENT", "SIBLING", "BLOCKS", etc.)
            transaction (Optional[str]): Optional transaction ID
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
    async def get_accessible_records(
        self,
        user_id: str,
        org_id: str,
        filters: Optional[Dict[str, List[str]]] = None,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get all records accessible to a user based on their permissions and apply filters.

        Args:
            user_id (str): The userId field value in users collection
            org_id (str): The org_id to filter anyone collection
            filters (Optional[Dict[str, List[str]]]): Optional filters for departments, categories, languages, topics etc.
                Format: {
                    'departments': [dept_ids],
                    'categories': [cat_ids],
                    'subcategories1': [subcat1_ids],
                    'subcategories2': [subcat2_ids],
                    'subcategories3': [subcat3_ids],
                    'languages': [language_ids],
                    'topics': [topic_ids],
                    'kb': [kb_ids],
                    'apps': [connector_ids]
                }
            transaction (Optional[str]): Optional transaction context

        Returns:
            List[Dict]: List of accessible records
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
        node_id: str,
        node_collection: str,
        transaction: Optional[str] = None
    ) -> Optional['User']:
        """
        Get the first user with permission to a node.

        Args:
            node_id (str): Node ID
            node_collection (str): Node collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[User]: User object if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_users_with_permission_to_node(
        self,
        node_id: str,
        node_collection: str,
        transaction: Optional[str] = None
    ) -> List['User']:
        """
        Get all users with permission to a node.

        Args:
            node_id (str): Node ID
            node_collection (str): Node collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            List[User]: List of user objects
        """
        pass

    @abstractmethod
    async def check_record_access_with_details(
        self,
        user_id: str,
        org_id: str,
        record_id: str,
    ) -> Optional[Dict]:
        """
        Check record access and return record details if accessible.

        Args:
            user_id: The userId field value in users collection
            org_id: The organization ID
            record_id: The record ID to check access for

        Returns:
            Dict with record, knowledgeBase, folder, metadata, permissions if accessible;
            None if not.
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
    ) -> List[Dict]:
        """
        Get all parent IDs for a file.

        Args:
            file_key (str): File key
            transaction (Optional[Any]): Optional transaction context

        Returns:
            List[Dict]: List of parent files
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
        key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> None:
        """
        Remove sync point by syncPointKey field.

        Args:
            key (str): Sync point key
            collection (str): Collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def delete_sync_points_by_connector_id(
        self,
        connector_id: str,
        transaction: Optional[str] = None
    ) -> Tuple[int, bool]:
        """
        Delete all sync points for a given connector.

        Args:
            connector_id (str): The connector ID to delete sync points for
            transaction (Optional[str]): Optional transaction context

        Returns:
            Tuple[int, bool]: Tuple of (deleted_count, success_flag)
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
        connector_id: str,
        external_id: str,
        user_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """
        Delete a record by external ID.

        Args:
            connector_id (str): Connector ID
            external_id (str): External record ID
            user_id (str): User ID performing the deletion
            transaction (Optional[str]): Optional transaction context
        """
        pass

    @abstractmethod
    async def remove_user_access_to_record(
        self,
        connector_id: str,
        external_id: str,
        user_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """
        Remove a user's access to a record (for inbox-based deletions).

        Args:
            connector_id (str): Connector ID
            external_id (str): External record ID
            user_id (str): User ID to remove access from
            transaction (Optional[str]): Optional transaction context
        """
        pass

    @abstractmethod
    async def delete_connector_instance(
        self,
        connector_id: str,
        org_id: str,
        transaction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Delete a connector instance and all its related data.

        This method performs a comprehensive deletion of:
        - All records associated with the connector
        - All record groups, roles, groups, drives
        - All edges (permissions, relations, classifications)
        - The connector app node itself
        - Org-app relation edges

        Classification nodes (departments, categories, topics, languages) are NOT deleted
        as they are shared resources across connectors.
        Users are NOT deleted - only userAppRelation edges are removed.

        Args:
            connector_id (str): The connector instance ID
            org_id (str): The organization ID for validation
            transaction (Optional[str]): Optional transaction context

        Returns:
            Dict[str, Any]: Dictionary containing:
                - success (bool): Whether deletion was successful
                - virtual_record_ids (List[str]): List of virtual record IDs for Qdrant cleanup
                - deleted_records_count (int): Number of records deleted
                - deleted_record_groups_count (int): Number of record groups deleted
                - deleted_roles_count (int): Number of roles deleted
                - deleted_groups_count (int): Number of groups deleted
                - deleted_drives_count (int): Number of drives deleted
                - error (str, optional): Error message if deletion failed
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

    # ==================== Connector Registry Operations ====================

    @abstractmethod
    async def check_connector_name_exists(
        self,
        collection: str,
        instance_name: str,
        scope: str,
        org_id: Optional[str] = None,
        user_id: Optional[str] = None,
        transaction: Optional[str] = None,
    ) -> bool:
        """
        Check if a connector instance name already exists for the given scope.

        Args:
            collection: Collection name (e.g., "apps")
            instance_name: Name to check (will be normalized: lowercase, trimmed)
            scope: Connector scope ("personal" or "team")
            org_id: Organization ID (required for team scope)
            user_id: User ID (required for personal scope)
            transaction: Optional transaction ID

        Returns:
            bool: True if name exists, False if available
        """
        pass

    @abstractmethod
    async def batch_update_connector_status(
        self,
        collection: str,
        connector_keys: List[str],
        is_active: bool,
        is_agent_active: bool,
        transaction: Optional[str] = None,
    ) -> int:
        """
        Batch update isActive and isAgentActive status for multiple connectors.

        Args:
            collection: Collection name (e.g., "apps")
            connector_keys: List of connector instance keys to update
            is_active: New isActive value
            is_agent_active: New isAgentActive value
            transaction: Optional transaction ID

        Returns:
            int: Number of connectors updated
        """
        pass

    @abstractmethod
    async def get_user_connector_instances(
        self,
        collection: str,
        user_id: str,
        org_id: str,
        team_scope: str,
        personal_scope: str,
        transaction: Optional[str] = None,
    ) -> List[Dict]:
        """
        Get all connector instances accessible to a user (personal + team).

        Args:
            collection: Collection name (e.g., "apps")
            user_id: User ID
            org_id: Organization ID
            team_scope: Team scope value (e.g., "team")
            personal_scope: Personal scope value (e.g., "personal")
            transaction: Optional transaction ID

        Returns:
            List[Dict]: List of connector instance documents
        """
        pass

    @abstractmethod
    async def get_filtered_connector_instances(
        self,
        collection: str,
        edge_collection: str,
        org_id: str,
        user_id: str,
        scope: Optional[str] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 20,
        exclude_kb: bool = True,
        kb_connector_type: Optional[str] = None,
        is_admin: bool = False,
        transaction: Optional[str] = None,
    ) -> Tuple[List[Dict], int, Dict[str, int]]:
        """
        Get filtered connector instances with pagination and scope counts.

        Args:
            collection: Collection name (e.g., "apps")
            edge_collection: Edge collection for org-app relation
            org_id: Organization ID
            user_id: User ID
            scope: Optional scope filter ("personal" or "team")
            search: Optional search query (searches name, type, appGroup)
            skip: Number of items to skip
            limit: Maximum number of items to return
            exclude_kb: Whether to exclude KB connector
            kb_connector_type: KB connector type to exclude
            is_admin: Whether user is admin (affects team scope access)
            transaction: Optional transaction ID

        Returns:
            Tuple[List[Dict], int, Dict[str, int]]:
                - List of connector documents
                - Total count
                - Scope counts dict with "personal" and "team" keys
        """
        pass

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
        connector_id: str
    ) -> List[Dict]:
        """
        Get failed records along with their active users who have permissions.

        Generic method for getting records with indexing status FAILED and their permitted active users.

        Args:
            org_id (str): Organization ID
            connector_id (str): Connector ID

        Returns:
            List[Dict]: List of dictionaries with 'record' and 'users' keys
        """
        pass

    @abstractmethod
    async def get_failed_records_by_org(
        self,
        org_id: str,
        connector_id: str
    ) -> List[Dict]:
        """
        Get all failed records for an organization and connector.

        Generic method for getting records with indexing status FAILED.

        Args:
            org_id (str): Organization ID
            connector_id (str): Connector ID

        Returns:
            List[Dict]: List of failed record documents
        """
        pass

    # ==================== Knowledge Hub Operations ====================

    @abstractmethod
    async def get_knowledge_hub_root_nodes(
        self,
        user_key: str,
        org_id: str,
        user_app_ids: List[str],
        skip: int,
        limit: int,
        sort_field: str,
        sort_dir: str,
        include_kbs: bool,
        include_apps: bool,
        only_containers: bool,
        transaction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get root level nodes (KBs and Apps) for Knowledge Hub.

        Args:
            user_key: User's internal key
            org_id: Organization ID
            user_app_ids: List of app IDs user has access to
            skip: Number of items to skip
            limit: Maximum items to return
            sort_field: Field to sort by
            sort_dir: Sort direction (ASC/DESC)
            include_kbs: Whether to include Knowledge Bases
            include_apps: Whether to include Apps
            only_containers: Only return nodes with children
            transaction: Optional transaction context

        Returns:
            Dict with 'nodes' list and 'total' count
        """
        pass

    @abstractmethod
    async def get_knowledge_hub_children(
        self,
        parent_id: str,
        parent_type: str,
        org_id: str,
        user_key: str,
        skip: int,
        limit: int,
        sort_field: str,
        sort_dir: str,
        only_containers: bool = False,
        transaction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get direct children of a parent node for tree navigation (browse mode).

        For filtered/searched results, use get_knowledge_hub_search with parent_id instead.

        Provider-agnostic: Each provider converts these parameters to its query language.

        Args:
            parent_id: The ID of the parent node
            parent_type: The type of parent: 'app', 'kb', 'recordGroup', 'folder', 'record'
            org_id: The organization ID
            user_key: The user's key for permission filtering
            skip: Number of items to skip for pagination
            limit: Maximum number of items to return
            sort_field: Field to sort by
            sort_dir: Sort direction ('ASC' or 'DESC')
            only_containers: If True, only return nodes that can have children
            transaction: Optional transaction ID

        Returns:
            Dict with 'nodes' list and 'total' count
        """
        pass

    @abstractmethod
    async def get_knowledge_hub_search(
        self,
        org_id: str,
        user_key: str,
        skip: int,
        limit: int,
        sort_field: str,
        sort_dir: str,
        search_query: Optional[str] = None,
        node_types: Optional[List[str]] = None,
        record_types: Optional[List[str]] = None,
        origins: Optional[List[str]] = None,
        connector_ids: Optional[List[str]] = None,
        kb_ids: Optional[List[str]] = None,
        indexing_status: Optional[List[str]] = None,
        created_at: Optional[Dict[str, Optional[int]]] = None,
        updated_at: Optional[Dict[str, Optional[int]]] = None,
        size: Optional[Dict[str, Optional[int]]] = None,
        only_containers: bool = False,
        parent_id: Optional[str] = None,
        parent_type: Optional[str] = None,
        transaction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Unified search for knowledge hub nodes with permission-first traversal.

        Supports both:
        - Global search (parent_id=None): Search across all accessible nodes
        - Scoped search (parent_id set): Search within a specific parent's hierarchy

        Includes:
        - RecordGroups with direct permissions
        - Nested recordGroups via inheritPermissions edges (recursive)
        - Records via inheritPermissions from accessible recordGroups
        - Direct user/group/org permissions on records

        Provider-agnostic: Each provider converts these parameters to its query language.

        Args:
            org_id: The organization ID
            user_key: The user's key for permission filtering
            skip: Number of items to skip for pagination
            limit: Maximum number of items to return
            sort_field: Field to sort by
            sort_dir: Sort direction ('ASC' or 'DESC')
            search_query: Optional search query to filter by name
            node_types: Optional list of node types to filter by
            record_types: Optional list of record types to filter by
            origins: Optional list of origins to filter by (KB/CONNECTOR)
            connector_ids: Optional list of connector IDs to filter by
            kb_ids: Optional list of KB IDs to filter by
            indexing_status: Optional list of indexing statuses to filter by
            created_at: Optional date range filter for creation date
            updated_at: Optional date range filter for update date
            size: Optional size range filter
            only_containers: If True, only return nodes that can have children
            parent_id: Optional parent node ID for scoped search
            parent_type: Optional type of parent: 'app', 'kb', 'recordGroup', 'folder', 'record'
            transaction: Optional transaction ID

        Returns:
            Dict with 'nodes' list and 'total' count
        """
        pass

    # ==================== Knowledge Base Operations ====================

    @abstractmethod
    async def get_knowledge_hub_breadcrumbs(
        self,
        node_id: str,
        transaction: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get breadcrumb trail for a node.

        Args:
            node_id: Node ID to get breadcrumbs for
            transaction: Optional transaction context

        Returns:
            List of breadcrumb items from root to current node
        """
        pass

    @abstractmethod
    async def get_knowledge_hub_context_permissions(
        self,
        user_key: str,
        org_id: str,
        parent_id: Optional[str],
        transaction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get user's context-level permissions (for upload, create folder, etc.).

        Args:
            user_key: User's internal key
            org_id: Organization ID
            parent_id: Parent node ID (None for root)
            transaction: Optional transaction context

        Returns:
            Dict with role and capability flags
        """
        pass

    @abstractmethod
    async def get_knowledge_hub_filter_options(
        self,
        user_key: str,
        org_id: str,
        transaction: Optional[str] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get available filter options (KBs and Apps) for a user.

        Args:
            user_key: User's internal key
            org_id: Organization ID
            transaction: Optional transaction context

        Returns:
            Dict with 'kbs' and 'apps' lists containing {id, name}
        """
        pass

    @abstractmethod
    async def get_knowledge_hub_node_info(
        self,
        node_id: str,
        folder_mime_types: List[str],
        transaction: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get node information including type and subtype.

        Args:
            node_id: Node ID
            folder_mime_types: List of MIME types that indicate folders
            transaction: Optional transaction context

        Returns:
            Dict with id, name, nodeType, subType or None if not found
        """
        pass

    @abstractmethod
    async def get_knowledge_hub_parent_node(
        self,
        node_id: str,
        folder_mime_types: List[str],
        transaction: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get the parent node of a given node.

        Args:
            node_id: Node ID
            folder_mime_types: List of MIME types that indicate folders
            transaction: Optional transaction context

        Returns:
            Dict with parent node info or None if at root
        """
        pass

    @abstractmethod
    async def validate_folder_exists_in_kb(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Validate that a folder exists in a knowledge base.

        Args:
            kb_id (str): Knowledge base ID
            folder_id (str): Folder ID
            transaction (Optional[str]): Optional transaction ID

        Returns:
            bool: True if folder exists in KB, False otherwise
        """
        pass

    @abstractmethod
    async def _validate_folder_creation(
        self,
        kb_id: str,
        user_id: str
    ) -> Dict:
        """
        Validate user permissions for folder creation.

        Args:
            kb_id (str): Knowledge base ID
            user_id (str): User ID (internal key)

        Returns:
            Dict: Validation result with 'valid' key and user info
        """
        pass

    @abstractmethod
    async def get_key_by_external_message_id(
        self,
        external_message_id: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """
        Get internal key by external message ID.

        Args:
            external_message_id (str): External message ID
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Optional[str]: Internal key if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_related_records_by_relation_type(
        self,
        record_id: str,
        relation_type: str,
        edge_collection: str,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get related records connected via a specific relation type.

        Args:
            record_id (str): Source record ID
            relation_type (str): Relation type to filter by (e.g., "ATTACHMENT")
            edge_collection (str): Edge collection name
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[Dict]: List of related records with messageId, id/key, and relationType
        """
        pass

    @abstractmethod
    async def get_message_id_header_by_key(
        self,
        record_key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """
        Get messageIdHeader field from a mail record by its key.

        Args:
            record_key (str): Record key (_key or id)
            collection (str): Collection name (e.g., "records" or "mails")
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Optional[str]: messageIdHeader value if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_related_mails_by_message_id_header(
        self,
        message_id_header: str,
        exclude_key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> List[str]:
        """
        Find all mail records with the same messageIdHeader, excluding a specific key.

        Args:
            message_id_header (str): messageIdHeader value to search for
            exclude_key (str): Record key to exclude from results
            collection (str): Collection name (e.g., "records" or "mails")
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[str]: List of record keys (_key or id) matching the criteria
        """
        pass

    @abstractmethod
    async def check_connector_name_uniqueness(
        self,
        instance_name: str,
        scope: str,
        org_id: str,
        user_id: str,
        collection: str,
        edge_collection: Optional[str] = None,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Check if connector instance name is unique based on scope.

        Args:
            instance_name (str): Name to check
            scope (str): Connector scope (personal/team)
            org_id (str): Organization ID
            user_id (str): User ID (for personal scope)
            collection (str): Collection name for connector instances
            edge_collection (Optional[str]): Edge collection for org-connector relationship (for team scope)
            transaction (Optional[str]): Optional transaction ID

        Returns:
            bool: True if name is unique, False if already exists
        """
        pass

    @abstractmethod
    async def batch_update_nodes(
        self,
        node_ids: List[str],
        updates: Dict[str, Any],
        collection: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Batch update multiple nodes with the same updates.

        Args:
            node_ids (List[str]): List of node IDs to update
            updates (Dict[str, Any]): Dictionary of fields to update
            collection (str): Collection name
            transaction (Optional[str]): Optional transaction ID

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def get_connector_instances_with_filters(
        self,
        collection: str,
        scope: Optional[str] = None,
        user_id: Optional[str] = None,
        is_admin: bool = False,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
        transaction: Optional[str] = None
    ) -> Tuple[List[Dict], int]:
        """
        Get connector instances with filters, pagination, and access control.

        Args:
            collection (str): Collection name
            scope (Optional[str]): Scope filter (personal/team)
            user_id (Optional[str]): User ID for access control
            is_admin (bool): Whether the user is an admin
            search (Optional[str]): Search query
            page (int): Page number (1-indexed)
            limit (int): Number of items per page
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Tuple[List[Dict], int]: (List of connector instances, total count)
        """
        pass

    @abstractmethod
    async def count_connector_instances_by_scope(
        self,
        collection: str,
        scope: str,
        user_id: Optional[str] = None,
        is_admin: bool = False,
        transaction: Optional[str] = None
    ) -> int:
        """
        Count connector instances by scope with access control.

        Args:
            collection (str): Collection name
            scope (str): Scope filter (personal/team)
            user_id (Optional[str]): User ID for access control
            is_admin (bool): Whether the user is an admin
            transaction (Optional[str]): Optional transaction ID

        Returns:
            int: Count of connector instances
        """
        pass

    # ==================== Team Operations ====================

    @abstractmethod
    async def get_teams(
        self,
        org_id: str,
        user_key: str,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 10,
        transaction: Optional[str] = None
    ) -> Tuple[List[Dict], int]:
        """
        Get teams for an organization with pagination, search, members, and permissions.

        Args:
            org_id (str): Organization ID
            user_key (str): Current user's key (for permission checking)
            search (Optional[str]): Search query for team name
            page (int): Page number (1-indexed)
            limit (int): Number of items per page
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Tuple[List[Dict], int]: (List of teams with members and permissions, total count)
        """
        pass

    @abstractmethod
    async def get_team_with_users(
        self,
        team_id: str,
        user_key: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get a single team with its members and permissions.

        Args:
            team_id (str): Team ID
            user_key (str): Current user's key (for permission checking)
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Optional[Dict]: Team data with members and permissions, None if not found
        """
        pass

    @abstractmethod
    async def get_user_teams(
        self,
        user_key: str,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 100,
        transaction: Optional[str] = None
    ) -> Tuple[List[Dict], int]:
        """
        Get all teams that a user is a member of.

        Args:
            user_key (str): User's key
            search (Optional[str]): Search query for team name or description
            page (int): Page number (1-indexed)
            limit (int): Number of items per page
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Tuple[List[Dict], int]: (List of teams with members and permissions, total count)
        """
        pass

    @abstractmethod
    async def get_user_created_teams(
        self,
        org_id: str,
        user_key: str,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 100,
        transaction: Optional[str] = None
    ) -> Tuple[List[Dict], int]:
        """
        Get all teams created by a user.

        Args:
            org_id (str): Organization ID
            user_key (str): User's key
            search (Optional[str]): Search query for team name or description
            page (int): Page number (1-indexed)
            limit (int): Number of items per page
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Tuple[List[Dict], int]: (List of teams with members and permissions, total count)
        """
        pass

    @abstractmethod
    async def get_team_users(
        self,
        team_id: str,
        org_id: str,
        user_key: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get all users in a specific team.

        Args:
            team_id (str): Team ID
            org_id (str): Organization ID
            user_key (str): Current user's key (for permission checking)
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Optional[Dict]: Team data with all members, None if not found
        """
        pass

    @abstractmethod
    async def search_teams(
        self,
        org_id: str,
        user_key: str,
        query: str,
        limit: int = 10,
        offset: int = 0,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Search teams by name or description.

        Args:
            org_id (str): Organization ID
            user_key (str): Current user's key (for permission checking)
            query (str): Search query string
            limit (int): Maximum number of results
            offset (int): Offset for pagination
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[Dict]: List of matching teams with members and permissions
        """
        pass

    @abstractmethod
    async def delete_team_member_edges(
        self,
        team_id: str,
        user_ids: List[str],
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Delete edges to remove team members.

        Args:
            team_id (str): Team ID
            user_ids (List[str]): List of user IDs to remove from team
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[Dict]: List of deleted permission edges (OLD values)
        """
        pass

    @abstractmethod
    async def batch_update_team_member_roles(
        self,
        team_id: str,
        user_roles: List[Dict[str, str]],
        timestamp: int,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Batch update user roles in a team.

        Args:
            team_id (str): Team ID
            user_roles (List[Dict[str, str]]): List of {userId: str, role: str} dictionaries
            timestamp (int): Timestamp for updatedAtTimestamp field
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[Dict]: List of updated permission edges
        """
        pass

    @abstractmethod
    async def delete_all_team_permissions(
        self,
        team_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """
        Delete all permissions for a team.

        Args:
            team_id (str): Team ID
            transaction (Optional[str]): Optional transaction ID

        Returns:
            None
        """
        pass

    @abstractmethod
    async def get_team_owner_removal_info(
        self,
        team_id: str,
        user_ids: List[str],
        transaction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get information about owners being removed and total owner count for a team.

        Args:
            team_id (str): Team ID
            user_ids (List[str]): List of user IDs to check
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Dict with keys:
                - owners_being_removed (List[str]): User IDs of owners being removed
                - total_owner_count (int): Total number of owners in the team
        """
        pass

    @abstractmethod
    async def get_team_permissions_and_owner_count(
        self,
        team_id: str,
        user_ids: List[str],
        transaction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get team info, current permissions for specific users, and total owner count.

        Args:
            team_id (str): Team ID
            user_ids (List[str]): List of user IDs to get permissions for
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Dict with keys:
                - team (Dict): Team document
                - permissions (Dict[str, str]): Map of user_id -> role
                - owner_count (int): Total number of owners in the team
        """
        pass

    # ==================== User Operations ====================

    @abstractmethod
    async def get_organization_users(
        self,
        org_id: str,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 100,
        transaction: Optional[str] = None
    ) -> Tuple[List[Dict], int]:
        """
        Get users in an organization with pagination and search.

        Args:
            org_id (str): Organization ID
            search (Optional[str]): Search query for user name or email
            page (int): Page number (1-indexed)
            limit (int): Number of items per page
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Tuple[List[Dict], int]: (List of users, total count)
        """
        pass
