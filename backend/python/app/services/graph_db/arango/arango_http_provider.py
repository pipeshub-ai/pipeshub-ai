"""
ArangoDB HTTP Provider Implementation

Fully async implementation of IGraphDBProvider using ArangoDB REST API.
This replaces the synchronous python-arango SDK with async HTTP calls.

All operations are non-blocking and use aiohttp for async I/O.
"""

from logging import Logger
from typing import Any, Dict, List, Optional, Union

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    RECORD_TYPE_COLLECTION_MAPPING,
    CollectionNames,
    Connectors,
)
from app.config.constants.service import config_node_constants
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    FileRecord,
    Record,
    RecordGroup,
    RecordType,
    User,
)
from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class ArangoHTTPProvider(IGraphDBProvider):
    """
    ArangoDB implementation using REST API for fully async operations.

    This provider uses HTTP REST API calls instead of the python-arango SDK
    to avoid blocking the event loop.
    """

    def __init__(
        self,
        logger: Logger,
        config_service: ConfigurationService,
    ) -> None:
        """
        Initialize ArangoDB HTTP provider.

        Args:
            logger: Logger instance
            config_service: Configuration service for database credentials
        """
        self.logger = logger
        self.config_service = config_service
        self.http_client: Optional[ArangoHTTPClient] = None

    # ==================== Connection Management ====================

    async def connect(self) -> bool:
        """
        Connect to ArangoDB via REST API.

        Returns:
            bool: True if connection successful
        """
        try:
            self.logger.info("🚀 Connecting to ArangoDB via HTTP API...")

            # Get ArangoDB configuration
            arangodb_config = await self.config_service.get_config(
                config_node_constants.ARANGODB.value
            )

            if not arangodb_config or not isinstance(arangodb_config, dict):
                raise ValueError("ArangoDB configuration not found or invalid")

            arango_url = str(arangodb_config.get("url"))
            arango_user = str(arangodb_config.get("username"))
            arango_password = str(arangodb_config.get("password"))
            arango_db = str(arangodb_config.get("db"))

            if not all([arango_url, arango_user, arango_password, arango_db]):
                raise ValueError("Missing required ArangoDB configuration values")

            # Create HTTP client
            self.http_client = ArangoHTTPClient(
                base_url=arango_url,
                username=arango_user,
                password=arango_password,
                database=arango_db,
                logger=self.logger
            )

            # Connect to ArangoDB
            if not await self.http_client.connect():
                raise Exception("Failed to connect to ArangoDB")

            # Ensure database exists
            if not await self.http_client.database_exists(arango_db):
                self.logger.info(f"Database '{arango_db}' does not exist, creating it...")
                if not await self.http_client.create_database(arango_db):
                    raise Exception(f"Failed to create database '{arango_db}'")

            self.logger.info("✅ ArangoDB HTTP provider connected successfully")


            # Check if collections exist
            # for collection in CollectionNames:
            #     if await self.http_client.collection_exists(collection.value):
            #         self.logger.info(f"Collection '{collection.value}' exists")

            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to connect to ArangoDB via HTTP: {str(e)}")
            self.http_client = None
            return False

    async def disconnect(self) -> bool:
        """
        Disconnect from ArangoDB.

        Returns:
            bool: True if disconnection successful
        """
        try:
            self.logger.info("🚀 Disconnecting from ArangoDB via HTTP API")
            if self.http_client:
                await self.http_client.disconnect()
            self.http_client = None
            self.logger.info("✅ Disconnected from ArangoDB via HTTP API")
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to disconnect: {str(e)}")
            return False

    # ==================== Transaction Management ====================

    async def begin_transaction(self, read: List[str], write: List[str]) -> str:
        """
        Begin a database transaction - FULLY ASYNC.

        Args:
            read: Collections to read from
            write: Collections to write to

        Returns:
            str: Transaction ID (e.g., "123456789")
        """
        try:
            return await self.http_client.begin_transaction(read, write)
        except Exception as e:
            self.logger.error(f"❌ Failed to begin transaction: {str(e)}")
            raise

    async def commit_transaction(self, transaction: str) -> None:
        """
        Commit a transaction - FULLY ASYNC.

        Args:
            transaction: Transaction ID (string)
        """
        try:
            await self.http_client.commit_transaction(transaction)
        except Exception as e:
            self.logger.error(f"❌ Failed to commit transaction: {str(e)}")
            raise

    async def rollback_transaction(self, transaction: str) -> None:
        """
        Rollback a transaction - FULLY ASYNC.

        Args:
            transaction: Transaction ID (string)
        """
        try:
            await self.http_client.abort_transaction(transaction)
        except Exception as e:
            self.logger.error(f"❌ Failed to rollback transaction: {str(e)}")
            raise

    # ==================== Document Operations ====================

    async def get_document(
        self,
        document_key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get a document by key - FULLY ASYNC.

        Args:
            document_key: Document key
            collection: Collection name
            transaction: Optional transaction ID

        Returns:
            Optional[Dict]: Document data or None
        """
        try:
            return await self.http_client.get_document(
                collection, document_key, txn_id=transaction
            )
        except Exception as e:
            self.logger.error(f"❌ Failed to get document: {str(e)}")
            return None

    async def batch_upsert_nodes(
        self,
        nodes: List[Dict],
        collection: str,
        transaction: Optional[str] = None
    ) -> Optional[bool]:
        """
        Batch upsert nodes - FULLY ASYNC.

        Args:
            nodes: List of node documents
            collection: Collection name
            transaction: Optional transaction ID

        Returns:
            Optional[bool]: True if successful
        """
        try:
            if not nodes:
                return True

            result = await self.http_client.batch_insert_documents(
                collection, nodes, txn_id=transaction, overwrite=True
            )

            success = result.get("errors", 0) == 0
            return success

        except Exception as e:
            self.logger.error(f"❌ Batch upsert failed: {str(e)}")
            raise

    async def delete_nodes(
        self,
        keys: List[str],
        collection: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Delete multiple nodes - FULLY ASYNC.

        Args:
            keys: List of document keys
            collection: Collection name
            transaction: Optional transaction ID

        Returns:
            bool: True if successful
        """
        try:
            deleted = await self.http_client.batch_delete_documents(
                collection, keys, txn_id=transaction
            )
            return deleted == len(keys)
        except Exception as e:
            self.logger.error(f"❌ Delete nodes failed: {str(e)}")
            raise

    async def update_node(
        self,
        key: str,
        collection: str,
        updates: Dict,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Update a single node - FULLY ASYNC.

        Args:
            key: Document key
            collection: Collection name
            updates: Fields to update
            transaction: Optional transaction ID

        Returns:
            bool: True if successful
        """
        try:
            result = await self.http_client.update_document(
                collection, key, updates, txn_id=transaction
            )
            return result is not None
        except Exception as e:
            self.logger.error(f"❌ Update node failed: {str(e)}")
            raise

    # ==================== Edge Operations ====================

    async def batch_create_edges(
        self,
        edges: List[Dict],
        collection: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Batch create edges - FULLY ASYNC.

        Args:
            edges: List of edge documents (must have _from and _to)
            collection: Edge collection name
            transaction: Optional transaction ID

        Returns:
            bool: True if successful
        """
        try:
            if not edges:
                return True

            result = await self.http_client.batch_insert_documents(
                collection, edges, txn_id=transaction, overwrite=True
            )

            return result.get("errors", 0) == 0

        except Exception as e:
            self.logger.error(f"❌ Batch create edges failed: {str(e)}")
            raise

    async def get_edge(
        self,
        from_key: str,
        to_key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get an edge between two nodes - FULLY ASYNC.

        Args:
            from_key: Source node key
            to_key: Target node key
            collection: Edge collection name
            transaction: Optional transaction ID

        Returns:
            Optional[Dict]: Edge data or None
        """
        from_id = f"{from_key.split('/')[0]}/{from_key.split('/')[1]}" if "/" in from_key else from_key
        to_id = f"{to_key.split('/')[0]}/{to_key.split('/')[1]}" if "/" in to_key else to_key

        query = f"""
        FOR edge IN {collection}
            FILTER edge._from == @from_id AND edge._to == @to_id
            LIMIT 1
            RETURN edge
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                {"from_id": from_id, "to_id": to_id},
                txn_id=transaction
            )
            return results[0] if results else None
        except Exception as e:
            self.logger.error(f"❌ Get edge failed: {str(e)}")
            return None

    async def delete_edge(
        self,
        from_key: str,
        to_key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> bool:
        """Delete an edge - FULLY ASYNC"""
        from_id = f"{from_key.split('/')[0]}/{from_key.split('/')[1]}" if "/" in from_key else from_key
        to_id = f"{to_key.split('/')[0]}/{to_key.split('/')[1]}" if "/" in to_key else to_key

        return await self.http_client.delete_edge(
            collection, from_id, to_id, txn_id=transaction
        )

    async def delete_edges_from(
        self,
        from_key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> int:
        """
        Delete all edges from a node - FULLY ASYNC.

        Args:
            from_key: Source node key
            collection: Edge collection name
            transaction: Optional transaction ID

        Returns:
            int: Number of edges deleted
        """
        from_id = f"{from_key.split('/')[0]}/{from_key.split('/')[1]}" if "/" in from_key else from_key

        query = f"""
        FOR edge IN {collection}
            FILTER edge._from == @from_id
            REMOVE edge IN {collection}
            RETURN OLD
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                {"from_id": from_id},
                txn_id=transaction
            )
            return len(results)
        except Exception as e:
            self.logger.error(f"❌ Delete edges from failed: {str(e)}")
            raise

    async def delete_edges_to(
        self,
        to_key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> int:
        """
        Delete all edges to a node - FULLY ASYNC.

        Args:
            to_key: Target node key
            collection: Edge collection name
            transaction: Optional transaction ID

        Returns:
            int: Number of edges deleted
        """
        to_id = f"{to_key.split('/')[0]}/{to_key.split('/')[1]}" if "/" in to_key else to_key

        query = f"""
        FOR edge IN {collection}
            FILTER edge._to == @to_id
            REMOVE edge IN {collection}
            RETURN OLD
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                {"to_id": to_id},
                txn_id=transaction
            )
            return len(results)
        except Exception as e:
            self.logger.error(f"❌ Delete edges to failed: {str(e)}")
            raise

    # ==================== Query Operations ====================

    async def execute_query(
        self,
        query: str,
        bind_vars: Optional[Dict] = None,
        transaction: Optional[str] = None
    ) -> Optional[List[Dict]]:
        """
        Execute AQL query - FULLY ASYNC.

        Args:
            query: AQL query string
            bind_vars: Query bind variables
            transaction: Optional transaction ID

        Returns:
            Optional[List[Dict]]: Query results
        """
        try:
            return await self.http_client.execute_aql(
                query, bind_vars, txn_id=transaction
            )
        except Exception as e:
            self.logger.error(f"❌ Query execution failed: {str(e)}")
            raise

    async def get_nodes_by_filters(
        self,
        collection: str,
        filters: Dict[str, Any],
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get nodes by field filters - FULLY ASYNC.

        Args:
            collection: Collection name
            filters: Field filters as dict
            transaction: Optional transaction ID

        Returns:
            List[Dict]: Matching nodes
        """
        # Build filter conditions
        filter_conditions = " AND ".join([
            f"doc.{field} == @{field}" for field in filters
        ])

        query = f"""
        FOR doc IN {collection}
            FILTER {filter_conditions}
            RETURN doc
        """

        try:
            results = await self.http_client.execute_aql(
                query, bind_vars=filters, txn_id=transaction
            )
            return results or []
        except Exception as e:
            self.logger.error(f"❌ Get nodes by filters failed: {str(e)}")
            return []

    async def get_nodes_by_field_in(
        self,
        collection: str,
        field: str,
        values: List[Any],
        return_fields: Optional[List[str]] = None,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get nodes where field value is in list - FULLY ASYNC.

        Args:
            collection: Collection name
            field: Field name to check
            values: List of values
            return_fields: Optional list of fields to return
            transaction: Optional transaction ID

        Returns:
            List[Dict]: Matching nodes
        """
        if return_fields:
            return_expr = "{" + ", ".join([f"{f}: doc.{f}" for f in return_fields]) + "}"
        else:
            return_expr = "doc"

        query = f"""
        FOR doc IN {collection}
            FILTER doc.{field} IN @values
            RETURN {return_expr}
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={"values": values},
                txn_id=transaction
            )
            return results or []
        except Exception as e:
            self.logger.error(f"❌ Get nodes by field in failed: {str(e)}")
            return []

    async def remove_nodes_by_field(
        self,
        collection: str,
        field: str,
        value: Union[str, int, bool, None],
        transaction: Optional[str] = None
    ) -> int:
        """
        Remove nodes matching field value - FULLY ASYNC.

        Args:
            collection: Collection name
            field: Field name
            value: Field value to match
            transaction: Optional transaction ID

        Returns:
            int: Number of nodes removed
        """
        query = f"""
        FOR doc IN {collection}
            FILTER doc.{field} == @value
            REMOVE doc IN {collection}
            RETURN OLD
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={"value": value},
                txn_id=transaction
            )
            return len(results)
        except Exception as e:
            self.logger.error(f"❌ Remove nodes by field failed: {str(e)}")
            raise

    async def get_edges_to_node(
        self,
        node_id: str,
        edge_collection: str,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get all edges pointing to a node - FULLY ASYNC.

        Args:
            node_id: Target node ID (e.g., "records/123")
            edge_collection: Edge collection name
            transaction: Optional transaction ID

        Returns:
            List[Dict]: List of edges
        """
        query = f"""
        FOR edge IN {edge_collection}
            FILTER edge._to == @node_id
            RETURN edge
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={"node_id": node_id},
                txn_id=transaction
            )
            return results or []
        except Exception as e:
            self.logger.error(f"❌ Get edges to node failed: {str(e)}")
            return []

    async def get_related_nodes(
        self,
        node_id: str,
        edge_collection: str,
        target_collection: str,
        direction: str = "outbound",
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get related nodes through an edge - FULLY ASYNC.

        Args:
            node_id: Source/target node ID
            edge_collection: Edge collection name
            target_collection: Target node collection
            direction: "outbound" or "inbound"
            transaction: Optional transaction ID

        Returns:
            List[Dict]: Related nodes
        """
        if direction == "outbound":
            query = f"""
            FOR edge IN {edge_collection}
                FILTER edge._from == @node_id
                FOR node IN {target_collection}
                    FILTER node._id == edge._to
                    RETURN node
            """
        else:  # inbound
            query = f"""
            FOR edge IN {edge_collection}
                FILTER edge._to == @node_id
                FOR node IN {target_collection}
                    FILTER node._id == edge._from
                    RETURN node
            """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={"node_id": node_id},
                txn_id=transaction
            )
            return results or []
        except Exception as e:
            self.logger.error(f"❌ Get related nodes failed: {str(e)}")
            return []

    async def get_related_node_field(
        self,
        node_id: str,
        edge_collection: str,
        target_collection: str,
        field: str,
        direction: str = "outbound",
        transaction: Optional[str] = None
    ) -> List[Any]:
        """
        Get specific field from related nodes - FULLY ASYNC.

        Args:
            node_id: Source/target node ID
            edge_collection: Edge collection name
            target_collection: Target node collection
            field: Field name to return
            direction: "outbound" or "inbound"
            transaction: Optional transaction ID

        Returns:
            List[Any]: List of field values
        """
        if direction == "outbound":
            query = f"""
            FOR edge IN {edge_collection}
                FILTER edge._from == @node_id
                FOR node IN {target_collection}
                    FILTER node._id == edge._to
                    RETURN node.{field}
            """
        else:  # inbound
            query = f"""
            FOR edge IN {edge_collection}
                FILTER edge._to == @node_id
                FOR node IN {target_collection}
                    FILTER node._id == edge._from
                    RETURN node.{field}
            """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={"node_id": node_id},
                txn_id=transaction
            )
            return results or []
        except Exception as e:
            self.logger.error(f"❌ Get related node field failed: {str(e)}")
            return []

    # ==================== Placeholder Methods ====================
    # These will be implemented similar to ArangoDBProvider



    async def get_record_by_external_id(
        self,
        connector_name: Connectors,
        external_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Record]:
        """Get record by external ID"""
        query = f"""
        FOR doc IN {CollectionNames.RECORDS.value}
            FILTER doc.externalRecordId == @external_id
            AND doc.connectorName == @connector_name
            LIMIT 1
            RETURN doc
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "external_id": external_id,
                    "connector_name": connector_name.value
                },
                txn_id=transaction
            )
            return Record.from_arango_base_record(results[0]) if results else None
        except Exception as e:
            self.logger.error(f"❌ Get record by external ID failed: {str(e)}")
            return None

    async def get_record_key_by_external_id(
        self,
        external_id: str,
        connector_name: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """Get record key by external ID"""
        try:
            query = """
            FOR record IN @@collection
                FILTER record.externalRecordId == @external_id
                AND record.connectorName == @connector_name
                LIMIT 1
                RETURN record._key
            """
            bind_vars = {
                "@collection": CollectionNames.RECORDS.value,
                "external_id": external_id,
                "connector_name": connector_name
            }

            results = await self.http_client.execute_aql(query, bind_vars, transaction)
            return results[0] if results else None

        except Exception as e:
            self.logger.error(f"❌ Get record key by external ID failed: {str(e)}")
            return None

    async def get_record_by_path(
        self,
        connector_name: Connectors,
        path: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Get record by path"""
        try:
            query = """
            FOR file IN @@file_collection
                FILTER file.path == @path
                FOR record IN @@record_collection
                    FILTER record._key == file._key
                    AND record.connectorName == @connector_name
                    LIMIT 1
                    RETURN record
            """
            bind_vars = {
                "@file_collection": CollectionNames.FILES.value,
                "@record_collection": CollectionNames.RECORDS.value,
                "path": path,
                "connector_name": connector_name.value
            }

            results = await self.http_client.execute_aql(query, bind_vars, transaction)
            return results[0] if results else None

        except Exception as e:
            self.logger.error(f"❌ Get record by path failed: {str(e)}")
            return None

    async def get_records_by_status(
        self,
        org_id: str,
        connector_name: Connectors,
        status_filters: List[str],
        limit: Optional[int] = None,
        offset: int = 0,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """Get records by indexing status"""
        try:
            limit_clause = f"LIMIT {offset}, {limit}" if limit else ""
            query = f"""
            FOR record IN @@collection
                FILTER record.indexingStatus IN @status_filters
                AND record.orgId == @org_id
                AND record.connectorName == @connector_name
                {limit_clause}
                RETURN record
            """
            bind_vars = {
                "@collection": CollectionNames.RECORDS.value,
                "status_filters": status_filters,
                "org_id": org_id,
                "connector_name": connector_name.value
            }

            return await self.http_client.execute_aql(query, bind_vars, transaction)

        except Exception as e:
            self.logger.error(f"❌ Get records by status failed: {str(e)}")
            return []

    async def get_record_by_conversation_index(
        self,
        connector_name: Connectors,
        conversation_index: str,
        thread_id: str,
        org_id: str,
        user_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Get record by conversation index"""
        try:
            query = """
            FOR mail IN @@mail_collection
                FILTER mail.conversationIndex == @conversation_index
                AND mail.threadId == @thread_id
                FOR record IN @@record_collection
                    FILTER record._key == mail._key
                    AND record.connectorName == @connector_name
                    AND record.orgId == @org_id
                    LIMIT 1
                    RETURN record
            """
            bind_vars = {
                "@mail_collection": CollectionNames.MAILS.value,
                "@record_collection": CollectionNames.RECORDS.value,
                "conversation_index": conversation_index,
                "thread_id": thread_id,
                "connector_name": connector_name.value,
                "org_id": org_id
            }

            results = await self.http_client.execute_aql(query, bind_vars, transaction)
            return results[0] if results else None

        except Exception as e:
            self.logger.error(f"❌ Get record by conversation index failed: {str(e)}")
            return None

    async def get_record_group_by_external_id(
        self,
        connector_name: Connectors,
        external_id: str,
        transaction: Optional[str] = None
    ) -> Optional[RecordGroup]:
        """
        Get record group by external ID.

        Generic implementation using filters.
        """
        query = f"""
        FOR doc IN {CollectionNames.RECORD_GROUPS.value}
            FILTER doc.externalGroupId == @external_id
            AND doc.connectorName == @connector_name
            LIMIT 1
            RETURN doc
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "external_id": external_id,
                    "connector_name": connector_name.value
                },
                txn_id=transaction
            )

            if results:
                # Convert to RecordGroup entity
                return RecordGroup.from_arango_base_record_group(results[0])

            return None

        except Exception as e:
            self.logger.error(f"❌ Get record group by external ID failed: {str(e)}")
            return None

    async def get_record_group_by_id(
        self,
        id: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Get record group by ID"""
        try:
            return await self.http_client.get_document(
                CollectionNames.RECORD_GROUPS.value,
                id,
                txn_id=transaction
            )
        except Exception as e:
            self.logger.error(f"❌ Get record group by ID failed: {str(e)}")
            return None

    async def get_file_record_by_id(
        self,
        id: str,
        transaction: Optional[str] = None
    ) -> Optional[FileRecord]:
        """Get file record by ID"""
        try:
            file = await self.http_client.get_document(
                CollectionNames.FILES.value,
                id,
                txn_id=transaction
            )
            record = await self.http_client.get_document(
                CollectionNames.RECORDS.value,
                id,
                txn_id=transaction
            )
            if file and record:
                return FileRecord.from_arango_record(
                    arango_base_file_record=file,
                    arango_base_record=record
                )
            return None
        except Exception as e:
            self.logger.error(f"❌ Get file record by ID failed: {str(e)}")
            return None

    async def get_user_by_email(
        self,
        email: str,
        transaction: Optional[str] = None
    ) -> Optional[User]:
        """
        Get user by email.

        Generic implementation using query.
        """
        query = f"""
        FOR user IN {CollectionNames.USERS.value}
            FILTER user.email == @email
            LIMIT 1
            RETURN user
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={"email": email},
                txn_id=transaction
            )

            if results:
                # Convert to User entity
                return User.from_arango_user(results[0])

            return None

        except Exception as e:
            self.logger.error(f"❌ Get user by email failed: {str(e)}")
            return None

    async def get_user_by_source_id(
        self,
        source_user_id: str,
        connector_name: Connectors,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Get user by source ID"""
        try:
            query = """
            FOR user IN @@collection
                FILTER user.sourceUserId == @source_user_id
                AND user.connectorName == @connector_name
                LIMIT 1
                RETURN user
            """
            bind_vars = {
                "@collection": CollectionNames.USERS.value,
                "source_user_id": source_user_id,
                "connector_name": connector_name.value
            }

            results = await self.http_client.execute_aql(query, bind_vars, transaction)
            return results[0] if results else None

        except Exception as e:
            self.logger.error(f"❌ Get user by source ID failed: {str(e)}")
            return None

    async def get_user_by_user_id(
        self,
        user_id: str
    ) -> Optional[Dict]:
        """Get user by user ID"""
        try:
            return await self.http_client.get_document(
                CollectionNames.USERS.value,
                user_id
            )
        except Exception as e:
            self.logger.error(f"❌ Get user by user ID failed: {str(e)}")
            return None

    async def get_users(
        self,
        org_id: str,
        active: bool = True
    ) -> List[Dict]:
        """
        Get users by organization.

        Generic method using filters.
        """
        filters = {"orgId": org_id}
        if active:
            filters["isActive"] = True
        else:
            filters["isActive"] = False

        return await self.get_nodes_by_filters(
            collection=CollectionNames.USERS.value,
            filters=filters
        )

    async def get_app_user_by_email(
        self,
        email: str,
        app_name: Connectors,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Get app user by email"""
        try:
            query = """
            FOR user IN @@collection
                FILTER user.email == @email
                AND user.appName == @app_name
                LIMIT 1
                RETURN user
            """
            bind_vars = {
                "@collection": CollectionNames.USERS.value,
                "email": email,
                "app_name": app_name.value
            }

            results = await self.http_client.execute_aql(query, bind_vars, transaction)
            return results[0] if results else None

        except Exception as e:
            self.logger.error(f"❌ Get app user by email failed: {str(e)}")
            return None

    async def get_app_users(
        self,
        org_id: str,
        app_name: str
    ) -> List[Dict]:
        """
        Get all users for a specific app.

        Generic implementation using query.
        """
        query = f"""
        FOR rel IN {CollectionNames.USER_APP_RELATION.value}
            FILTER rel._to == CONCAT('apps/', (
                FOR app IN {CollectionNames.APPS.value}
                    FILTER app.name == @app_name
                    AND app.orgId == @org_id
                    LIMIT 1
                    RETURN app._key
            )[0])
            FOR user IN {CollectionNames.USERS.value}
                FILTER user._id == rel._from
                RETURN user
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "app_name": app_name,
                    "org_id": org_id
                }
            )
            return results if results else []
        except Exception as e:
            self.logger.error(f"❌ Get app users failed: {str(e)}")
            return []

    async def get_user_group_by_external_id(
        self,
        connector_name: Connectors,
        external_id: str,
        transaction: Optional[str] = None
    ) -> Optional[AppUserGroup]:
        """
        Get user group by external ID.

        Generic implementation using query.
        """
        query = f"""
        FOR group IN {CollectionNames.GROUPS.value}
            FILTER group.sourceUserGroupId == @external_id
            AND group.appName == @connector_name
            LIMIT 1
            RETURN group
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "external_id": external_id,
                    "connector_name": connector_name.value
                },
                txn_id=transaction
            )

            if results:
                # Convert to AppUserGroup entity
                return AppUserGroup.from_arango_base_user_group(results[0])

            return None

        except Exception as e:
            self.logger.error(f"❌ Get user group by external ID failed: {str(e)}")
            return None

    async def get_user_groups(
        self,
        app_name: Connectors,
        org_id: str,
        transaction: Optional[str] = None
    ) -> List[AppUserGroup]:
        """Get user groups"""
        try:
            query = """
            FOR group IN @@collection
                FILTER group.appName == @app_name
                AND group.orgId == @org_id
                RETURN group
            """
            bind_vars = {
                "@collection": CollectionNames.GROUPS.value,
                "app_name": app_name.value,
                "org_id": org_id
            }

            groupData = await self.http_client.execute_aql(query, bind_vars, transaction)
            groups = [AppUserGroup.from_arango_base_user_group(group_data) for group_data in groupData]

            return groups

        except Exception as e:
            self.logger.error(f"❌ Get user groups failed: {str(e)}")
            return []

    async def get_app_role_by_external_id(
        self,
        connector_name: Connectors,
        external_id: str,
        transaction: Optional[str] = None
    ) -> Optional[AppRole]:
        """
        Get app role by external ID.

        Generic implementation using query.
        """
        query = f"""
        FOR role IN {CollectionNames.ROLES.value}
            FILTER role.sourceRoleId == @external_id
            AND role.appName == @connector_name
            LIMIT 1
            RETURN role
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "external_id": external_id,
                    "connector_name": connector_name.value
                },
                txn_id=transaction
            )

            if results:
                # Convert to AppRole entity
                return AppRole.from_arango_base_role(results[0])

            return None

        except Exception as e:
            self.logger.error(f"❌ Get app role by external ID failed: {str(e)}")
            return None

    async def get_all_orgs(
        self,
        active: bool = True
    ) -> List[Dict]:
        """
        Get all organizations.

        Uses generic get_nodes_by_filters if filtering by active,
        or returns all orgs if no filter.
        """
        if active:
            return await self.get_nodes_by_filters(
                collection=CollectionNames.ORGS.value,
                filters={"isActive": True}
            )
        else:
            # Get all orgs using execute_aql
            query = f"""
            FOR org IN {CollectionNames.ORGS.value}
                RETURN org
            """

            try:
                results = await self.http_client.execute_aql(query)
                return results if results else []
            except Exception as e:
                self.logger.error(f"❌ Get all orgs failed: {str(e)}")
                return []

    async def batch_upsert_records(
        self,
        records: List[Record],
        transaction: Optional[str] = None
    ) -> None:
        """
        Batch upsert records (base + specific type + IS_OF_TYPE edges).

        Handles the complete record upsert logic:
        1. Upserts base record to 'records' collection
        2. Upserts specific type to type-specific collection (files, mails, etc.)
        3. Creates IS_OF_TYPE edge
        """
        record_ids = [r.id for r in records]
        duplicates = [x for x in record_ids if record_ids.count(x) > 1]
        if duplicates:
            self.logger.warning(f"DUPLICATE RECORD IDS IN BATCH: {duplicates}")

        try:
            for record in records:
                # Define record type configurations
                record_type_config = {
                    RecordType(record_type_str): {"collection": collection}
                    for record_type_str, collection in RECORD_TYPE_COLLECTION_MAPPING.items()
                }

                # Get the configuration for the current record type
                record_type = record.record_type
                if record_type not in record_type_config:
                    self.logger.error(f"❌ Unsupported record type: {record_type}")
                    continue

                config = record_type_config[record_type]

                # Create the IS_OF_TYPE edge
                is_of_type_record = {
                    "_from": f"{CollectionNames.RECORDS.value}/{record.id}",
                    "_to": f"{config['collection']}/{record.id}",
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                }

                # Upsert base record
                await self.batch_upsert_nodes(
                    [record.to_arango_base_record()],
                    collection=CollectionNames.RECORDS.value,
                    transaction=transaction
                )

                # Upsert specific record type
                await self.batch_upsert_nodes(
                    [record.to_arango_record()],
                    collection=config["collection"],
                    transaction=transaction
                )

                # Create IS_OF_TYPE edge
                await self.batch_create_edges(
                    [is_of_type_record],
                    collection=CollectionNames.IS_OF_TYPE.value,
                    transaction=transaction
                )

            self.logger.info("✅ Successfully upserted records")
            return True

        except Exception as e:
            self.logger.error(f"❌ Batch upsert records failed: {str(e)}")
            raise

    async def create_record_relation(
        self,
        from_record_id: str,
        to_record_id: str,
        relation_type: str,
        transaction: Optional[str] = None
    ) -> None:
        """
        Create a relation edge between two records.

        Generic implementation that creates RECORD_RELATIONS edge.
        """
        record_edge = {
            "_from": f"{CollectionNames.RECORDS.value}/{from_record_id}",
            "_to": f"{CollectionNames.RECORDS.value}/{to_record_id}",
            "relationshipType": relation_type,
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        }

        await self.batch_create_edges(
            [record_edge],
            collection=CollectionNames.RECORD_RELATIONS.value,
            transaction=transaction
        )

    async def batch_upsert_record_groups(
        self,
        record_groups: List[RecordGroup],
        transaction: Optional[str] = None
    ) -> None:
        """
        Batch upsert record groups.

        Converts RecordGroup entities to database format and upserts.
        """
        try:
            nodes = [record_group.to_arango_base_record_group() for record_group in record_groups]
            await self.batch_upsert_nodes(
                nodes,
                collection=CollectionNames.RECORD_GROUPS.value,
                transaction=transaction
            )
        except Exception as e:
            self.logger.error(f"❌ Batch upsert record groups failed: {str(e)}")
            raise

    async def create_record_group_relation(
        self,
        record_id: str,
        record_group_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """
        Create BELONGS_TO edge from record to record group.

        Generic implementation.
        """
        record_edge = {
            "_from": f"{CollectionNames.RECORDS.value}/{record_id}",
            "_to": f"{CollectionNames.RECORD_GROUPS.value}/{record_group_id}",
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        }

        await self.batch_create_edges(
            [record_edge],
            collection=CollectionNames.BELONGS_TO.value,
            transaction=transaction
        )

    async def create_record_groups_relation(
        self,
        child_id: str,
        parent_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """
        Create BELONGS_TO edge from child record group to parent record group.

        Generic implementation for folder hierarchy.
        """
        edge = {
            "_from": f"{CollectionNames.RECORD_GROUPS.value}/{child_id}",
            "_to": f"{CollectionNames.RECORD_GROUPS.value}/{parent_id}",
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        }

        await self.batch_create_edges(
            [edge],
            collection=CollectionNames.BELONGS_TO.value,
            transaction=transaction
        )

    async def create_inherit_permissions_relation_record_group(
        self,
        record_id: str,
        record_group_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """
        Create INHERIT_PERMISSIONS edge from record to record group.

        Generic implementation.
        """
        record_edge = {
            "_from": f"{CollectionNames.RECORDS.value}/{record_id}",
            "_to": f"{CollectionNames.RECORD_GROUPS.value}/{record_group_id}",
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        }

        await self.batch_create_edges(
            [record_edge],
            collection=CollectionNames.INHERIT_PERMISSIONS.value,
            transaction=transaction
        )

    async def get_or_create_app_by_name(
        self,
        app_name: str,
        org_id: str
    ) -> Optional[Dict]:
        """
        Get app by name, create if doesn't exist.

        If creating, also creates ORG_APP_RELATION edge.
        """
        try:
            # First try to get the app
            existing_app = await self.get_app_by_name(app_name)

            if existing_app:
                return existing_app

            # App doesn't exist, create it
            self.logger.info(f"📝 Creating new app: {app_name} for org {org_id}")

            import hashlib

            # Generate app key and group
            app_key = f"{org_id}_{app_name.replace(' ', '_').upper()}"
            app_group = app_name  # Can be customized per connector
            app_group_id = hashlib.sha256(app_group.encode()).hexdigest()

            app_doc = {
                "_key": app_key,
                "name": app_name,
                "type": app_name.upper().replace(' ', '_'),
                "appGroup": app_group,
                "appGroupId": app_group_id,
                "authType": "oauth",
                "isActive": False,
                "isConfigured": False,
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            # Create app document
            await self.batch_upsert_nodes(
                [app_doc],
                collection=CollectionNames.APPS.value
            )

            # Create ORG_APP_RELATION edge
            org_app_edge = {
                "_from": f"{CollectionNames.ORGS.value}/{org_id}",
                "_to": f"{CollectionNames.APPS.value}/{app_key}",
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            await self.batch_create_edges(
                [org_app_edge],
                collection=CollectionNames.ORG_APP_RELATION.value
            )

            # Return app with _id
            app_doc["_id"] = f"{CollectionNames.APPS.value}/{app_key}"

            self.logger.info(f"✅ Created new app: {app_name}")
            return app_doc

        except Exception as e:
            self.logger.error(f"❌ Get or create app by name failed: {str(e)}")
            return None

    async def get_org_apps(
        self,
        org_id: str
    ) -> List[Dict]:
        """
        Get organization apps.

        Generic method using filters.
        """
        return await self.get_nodes_by_filters(
            collection=CollectionNames.APPS.value,
            filters={"orgId": org_id}
        )

    async def batch_upsert_record_permissions(
        self,
        record_id: str,
        permissions: List[Dict],
        transaction: Optional[str] = None
    ) -> None:
        """Batch upsert record permissions"""
        try:
            if not permissions:
                return

            await self.batch_create_edges(
                permissions,
                collection=CollectionNames.PERMISSION.value,
                transaction=transaction
            )

        except Exception as e:
            self.logger.error(f"❌ Batch upsert record permissions failed: {str(e)}")
            raise

    async def get_file_permissions(
        self,
        file_key: str,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """Get file permissions"""
        try:
            query = """
            FOR edge IN @@collection
                FILTER edge._to == @file_key
                RETURN edge
            """
            bind_vars = {
                "@collection": CollectionNames.PERMISSION.value,
                "file_key": file_key
            }

            return await self.http_client.execute_aql(query, bind_vars, transaction)

        except Exception as e:
            self.logger.error(f"❌ Get file permissions failed: {str(e)}")
            return []

    async def get_first_user_with_permission_to_node(
        self,
        node_key: str,
        transaction: Optional[str] = None
    ) -> Optional[User]:
        """Get first user with permission to node"""
        try:
            query = """
            FOR edge IN @@edge_collection
                FILTER edge._to == @node_key
                FOR user IN @@user_collection
                    FILTER user._id == edge._from
                    LIMIT 1
                    RETURN user
            """
            bind_vars = {
                "@edge_collection": CollectionNames.PERMISSION.value,
                "@user_collection": CollectionNames.USERS.value,
                "node_key": node_key
            }

            results = await self.http_client.execute_aql(query, bind_vars, transaction)
            return User.from_arango_user(results[0]) if results else None

        except Exception as e:
            self.logger.error(f"❌ Get first user with permission to node failed: {str(e)}")
            return None

    async def get_users_with_permission_to_node(
        self,
        node_key: str,
        transaction: Optional[str] = None
    ) -> List[User]:
        """Get users with permission to node"""
        try:
            query = """
            FOR edge IN @@edge_collection
                FILTER edge._to == @node_key
                FOR user IN @@user_collection
                    FILTER user._id == edge._from
                    RETURN user
            """
            bind_vars = {
                "@edge_collection": CollectionNames.PERMISSION.value,
                "@user_collection": CollectionNames.USERS.value,
                "node_key": node_key
            }

            results = await self.http_client.execute_aql(query, bind_vars, transaction)
            return [User.from_arango_user(result) for result in results]

        except Exception as e:
            self.logger.error(f"❌ Get users with permission to node failed: {str(e)}")
            return []

    async def get_record_owner_source_user_email(
        self,
        record_id: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """Get record owner source user email"""
        try:
            query = """
            FOR edge IN @@edge_collection
                FILTER edge._to == @record_key
                AND edge.role == "OWNER"
                FOR user IN @@user_collection
                    FILTER user._id == edge._from
                    LIMIT 1
                    RETURN user.email
            """
            bind_vars = {
                "@edge_collection": CollectionNames.PERMISSION.value,
                "@user_collection": CollectionNames.USERS.value,
                "record_key": f"{CollectionNames.RECORDS.value}/{record_id}"
            }

            results = await self.http_client.execute_aql(query, bind_vars, transaction)
            return results[0] if results else None

        except Exception as e:
            self.logger.error(f"❌ Get record owner source user email failed: {str(e)}")
            return None

    async def get_file_parents(
        self,
        file_key: str,
        transaction: Optional[str] = None
    ) -> List[str]:
        """Get file parents"""
        try:
            query = """
            FOR edge IN @@edge_collection
                FILTER edge._from == @file_key
                RETURN edge._to
            """
            bind_vars = {
                "@edge_collection": CollectionNames.PARENT.value,
                "file_key": f"{CollectionNames.FILES.value}/{file_key}"
            }

            return await self.http_client.execute_aql(query, bind_vars, transaction)

        except Exception as e:
            self.logger.error(f"❌ Get file parents failed: {str(e)}")
            return []

    async def get_sync_point(
        self,
        key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get sync point by syncPointKey field.
        """
        try:
            query = """
            FOR doc IN @@collection
                FILTER doc.syncPointKey == @key
                LIMIT 1
                RETURN doc
            """
            bind_vars = {
                "@collection": collection,
                "key": key
            }

            results = await self.http_client.execute_aql(query, bind_vars, transaction)
            return results[0] if results else None

        except Exception as e:
            self.logger.error(f"❌ Get sync point failed: {str(e)}")
            return None

    async def upsert_sync_point(
        self,
        sync_point_key: str,
        sync_point_data: Dict,
        collection: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Upsert sync point by syncPointKey field.
        """
        try:
            # First check if document exists
            existing = await self.get_sync_point(sync_point_key, collection, transaction)

            if existing:
                # Update existing document
                query = """
                FOR doc IN @@collection
                    FILTER doc.syncPointKey == @key
                    UPDATE doc WITH @data IN @@collection
                    RETURN NEW
                """
                bind_vars = {
                    "@collection": collection,
                    "key": sync_point_key,
                    "data": {
                        **sync_point_data,
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                    }
                }
            else:
                # Insert new document
                query = """
                INSERT @doc INTO @@collection
                RETURN NEW
                """
                bind_vars = {
                    "@collection": collection,
                    "doc": {
                        **sync_point_data,
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms()
                    }
                }

            await self.http_client.execute_aql(query, bind_vars, transaction)
            return True

        except Exception as e:
            self.logger.error(f"❌ Upsert sync point failed: {str(e)}")
            raise

    async def remove_sync_point(
        self,
        key: List[str],
        collection: str,
        transaction: Optional[str] = None
    ) -> None:
        """
        Remove sync points by syncPointKey field.
        """
        try:
            query = """
            FOR doc IN @@collection
                FILTER doc.syncPointKey IN @keys
                REMOVE doc IN @@collection
            """
            bind_vars = {
                "@collection": collection,
                "keys": key
            }

            await self.http_client.execute_aql(query, bind_vars, transaction)

        except Exception as e:
            self.logger.error(f"❌ Remove sync point failed: {str(e)}")
            raise

    async def batch_upsert_app_users(
        self,
        users: List[AppUser],
        transaction: Optional[str] = None
    ) -> None:
        """
        Batch upsert app users.

        Creates users if they don't exist, creates org relation and user-app relation.
        """
        try:
            # Get org_id
            orgs = await self.get_all_orgs()
            if not orgs:
                raise Exception("No organizations found in the database")
            org_id = orgs[0]["_key"]

            if not users:
                return

            app_name = users[0].app_name.value

            # Get or create app
            app = await self.get_or_create_app_by_name(app_name, org_id)
            if not app:
                raise Exception(f"Failed to get/create app: {app_name}")

            app_id = app["_id"]

            for user in users:
                # Check if user exists
                user_record = await self.get_user_by_email(user.email, transaction)

                if not user_record:
                    # Create new user
                    await self.batch_upsert_nodes(
                        [{**user.to_arango_base_user(), "orgId": org_id, "isActive": False}],
                        collection=CollectionNames.USERS.value,
                        transaction=transaction
                    )

                    user_record = await self.get_user_by_email(user.email, transaction)

                    # Create org relation
                    user_org_relation = {
                        "_from": f"{CollectionNames.USERS.value}/{user.id}",
                        "_to": f"{CollectionNames.ORGS.value}/{org_id}",
                        "createdAtTimestamp": user.created_at,
                        "updatedAtTimestamp": user.updated_at,
                        "entityType": "ORGANIZATION",
                    }
                    await self.batch_create_edges(
                        [user_org_relation],
                        collection=CollectionNames.BELONGS_TO.value,
                        transaction=transaction
                    )

                # Create user-app relation
                user_key = user_record.id
                user_app_relation = {
                    "_from": f"{CollectionNames.USERS.value}/{user_key}",
                    "_to": app_id,
                    "sourceUserId": user.source_user_id,
                    "syncState": "NOT_STARTED",
                    "lastSyncUpdate": get_epoch_timestamp_in_ms(),
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                }

                await self.batch_create_edges(
                    [user_app_relation],
                    collection=CollectionNames.USER_APP_RELATION.value,
                    transaction=transaction
                )

        except Exception as e:
            self.logger.error(f"❌ Batch upsert app users failed: {str(e)}")
            raise

    async def batch_upsert_user_groups(
        self,
        user_groups: List[AppUserGroup],
        transaction: Optional[str] = None
    ) -> None:
        """
        Batch upsert user groups.

        Converts AppUserGroup entities to database format and upserts.
        """
        try:
            nodes = [user_group.to_arango_base_user_group() for user_group in user_groups]
            await self.batch_upsert_nodes(
                nodes,
                collection=CollectionNames.GROUPS.value,
                transaction=transaction
            )
        except Exception as e:
            self.logger.error(f"❌ Batch upsert user groups failed: {str(e)}")
            raise

    async def batch_upsert_app_roles(
        self,
        app_roles: List[AppRole],
        transaction: Optional[str] = None
    ) -> None:
        """
        Batch upsert app roles.

        Converts AppRole entities to database format and upserts.
        """
        try:
            nodes = [app_role.to_arango_base_role() for app_role in app_roles]
            await self.batch_upsert_nodes(
                nodes,
                collection=CollectionNames.ROLES.value,
                transaction=transaction
            )
        except Exception as e:
            self.logger.error(f"❌ Batch upsert app roles failed: {str(e)}")
            raise

    async def batch_upsert_orgs(
        self,
        orgs: List[Dict],
        transaction: Optional[str] = None
    ) -> None:
        """Batch upsert organizations"""
        try:
            if not orgs:
                return

            await self.batch_upsert_nodes(
                orgs,
                collection=CollectionNames.ORGS.value,
                transaction=transaction
            )

        except Exception as e:
            self.logger.error(f"❌ Batch upsert orgs failed: {str(e)}")
            raise

    async def batch_upsert_domains(
        self,
        domains: List[Dict],
        transaction: Optional[str] = None
    ) -> None:
        """Batch upsert domains"""
        try:
            if not domains:
                return

            await self.batch_upsert_nodes(
                domains,
                collection=CollectionNames.DOMAINS.value,
                transaction=transaction
            )

        except Exception as e:
            self.logger.error(f"❌ Batch upsert domains failed: {str(e)}")
            raise

    async def batch_upsert_anyone(
        self,
        anyone: List[Dict],
        transaction: Optional[str] = None
    ) -> None:
        """Batch upsert anyone entities"""
        try:
            if not anyone:
                return

            await self.batch_upsert_nodes(
                anyone,
                collection=CollectionNames.ANYONE.value,
                transaction=transaction
            )

        except Exception as e:
            self.logger.error(f"❌ Batch upsert anyone failed: {str(e)}")
            raise

    async def batch_upsert_anyone_with_link(
        self,
        anyone_with_link: List[Dict],
        transaction: Optional[str] = None
    ) -> None:
        """Batch upsert anyone with link"""
        try:
            if not anyone_with_link:
                return

            await self.batch_upsert_nodes(
                anyone_with_link,
                collection=CollectionNames.ANYONE_WITH_LINK.value,
                transaction=transaction
            )

        except Exception as e:
            self.logger.error(f"❌ Batch upsert anyone with link failed: {str(e)}")
            raise

    async def batch_upsert_anyone_same_org(
        self,
        anyone_same_org: List[Dict],
        transaction: Optional[str] = None
    ) -> None:
        """Batch upsert anyone same org"""
        try:
            if not anyone_same_org:
                return

            await self.batch_upsert_nodes(
                anyone_same_org,
                collection=CollectionNames.ANYONE_SAME_ORG.value,
                transaction=transaction
            )

        except Exception as e:
            self.logger.error(f"❌ Batch upsert anyone same org failed: {str(e)}")
            raise

    async def batch_create_user_app_edges(
        self,
        edges: List[Dict]
    ) -> int:
        """Batch create user app edges"""
        try:
            if not edges:
                return 0

            await self.batch_create_edges(
                edges,
                collection=CollectionNames.USER_APP.value
            )
            return len(edges)

        except Exception as e:
            self.logger.error(f"❌ Batch create user app edges failed: {str(e)}")
            raise

    async def get_entity_id_by_email(
        self,
        email: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """
        Get entity ID by email (searches users and groups).

        Generic method that works for both users and groups.

        Args:
            email: Email address
            transaction: Optional transaction ID

        Returns:
            Optional[str]: Entity key (_key) or None
        """
        # First check users
        query = f"""
        FOR doc IN {CollectionNames.USERS.value}
            FILTER doc.email == @email
            LIMIT 1
            RETURN doc._key
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={"email": email},
                txn_id=transaction
            )
            if results:
                return results[0]

            # If not found in users, check groups
            query = f"""
            FOR doc IN {CollectionNames.GROUPS.value}
                FILTER doc.email == @email
                LIMIT 1
                RETURN doc._key
            """

            results = await self.http_client.execute_aql(
                query,
                bind_vars={"email": email},
                txn_id=transaction
            )
            return results[0] if results else None

        except Exception as e:
            self.logger.error(f"❌ Get entity ID by email failed: {str(e)}")
            return None

    async def bulk_get_entity_ids_by_email(
        self,
        emails: List[str],
        transaction: Optional[str] = None
    ) -> Dict[str, Optional[str]]:
        """Bulk get entity IDs by email"""
        try:
            if not emails:
                return {}

            query = """
            LET result = (
                FOR email IN @emails
                    LET user = FIRST(
                        FOR u IN @@user_collection
                            FILTER u.email == email
                            LIMIT 1
                            RETURN u._key
                    )
                    RETURN {[email]: user}
            )
            RETURN MERGE(result)
            """
            bind_vars = {
                "@user_collection": CollectionNames.USERS.value,
                "emails": emails
            }

            results = await self.http_client.execute_aql(query, bind_vars, transaction)
            return results[0] if results else {}

        except Exception as e:
            self.logger.error(f"❌ Bulk get entity IDs by email failed: {str(e)}")
            return {}

    async def process_file_permissions(
        self,
        org_id: str,
        file_key: str,
        permissions: List[Dict],
        transaction: Optional[str] = None
    ) -> None:
        """
        Process file permissions by comparing new with existing.

        Generic implementation that:
        1. Removes 'anyone' permissions for this file
        2. Removes obsolete permission edges
        3. Creates/updates permission edges for users/groups
        4. Creates 'anyone' nodes if needed

        Args:
            org_id: Organization ID
            file_key: File key
            permissions: List of permission dictionaries from source
            transaction: Optional transaction ID
        """
        try:
            self.logger.info(f"🚀 Processing permissions for file {file_key}")
            timestamp = get_epoch_timestamp_in_ms()

            # 1. Remove 'anyone' permissions for this file using generic method
            removed = await self.remove_nodes_by_field(
                collection=CollectionNames.ANYONE.value,
                field="file_key",
                value=file_key,
                transaction=transaction
            )
            self.logger.info(f"🗑️ Removed {removed} 'anyone' permissions")

            # 2. Get existing permissions using generic method
            node_id = f"{CollectionNames.RECORDS.value}/{file_key}"
            existing_permissions = await self.get_edges_to_node(
                node_id=node_id,
                edge_collection=CollectionNames.PERMISSION.value,
                transaction=transaction
            )

            # 3. Find obsolete permissions
            new_permission_ids = {p.get("id") for p in permissions}

            permissions_to_remove = [
                perm for perm in existing_permissions
                if perm.get("externalPermissionId") not in new_permission_ids
            ]

            # 4. Remove obsolete permissions using generic method
            if permissions_to_remove:
                self.logger.info(f"🗑️ Removing {len(permissions_to_remove)} obsolete permissions")
                keys_to_remove = [perm["_key"] for perm in permissions_to_remove]
                await self.delete_nodes(
                    keys=keys_to_remove,
                    collection=CollectionNames.PERMISSION.value,
                    transaction=transaction
                )

            # 5. Process new permissions by type
            for perm_type in ["user", "group", "domain", "anyone"]:
                # Filter new permissions for current type
                new_perms = [
                    p for p in permissions
                    if p.get("type", "").lower() == perm_type
                ]

                # Filter existing permissions for current type
                existing_perms = [
                    p for p in existing_permissions
                    if p.get("type", "").lower() == perm_type
                ]

                # Process user, group, domain permissions
                if perm_type in ["user", "group", "domain"]:
                    for new_perm in new_perms:
                        perm_id = new_perm.get("id")

                        # Check if permission already exists
                        existing_perm = next(
                            (p for p in existing_perms if p.get("externalPermissionId") == perm_id),
                            None
                        )

                        if existing_perm:
                            # Update existing permission edge
                            edge_key = existing_perm.get("_key")
                            await self.update_node(
                                key=edge_key,
                                collection=CollectionNames.PERMISSION.value,
                                updates={
                                    "role": new_perm.get("role", "READER").upper(),
                                    "updatedAtTimestamp": timestamp,
                                    "lastUpdatedTimestampAtSource": timestamp,
                                },
                                transaction=transaction
                            )
                        else:
                            # Create new permission edge
                            if perm_type in ["user", "group"]:
                                email = new_perm.get("emailAddress")
                                if not email:
                                    continue

                                entity_id = await self.get_entity_id_by_email(email, transaction)
                                if not entity_id:
                                    self.logger.warning(f"⚠️ Entity not found for email: {email}")
                                    continue

                                collection = CollectionNames.USERS.value if perm_type == "user" else CollectionNames.GROUPS.value
                                from_id = f"{collection}/{entity_id}"
                            elif perm_type == "domain":
                                from_id = f"{CollectionNames.ORGS.value}/{org_id}"
                            else:
                                continue

                            # Create permission edge
                            await self.batch_create_edges(
                                edges=[{
                                    "_from": from_id,
                                    "_to": node_id,
                                    "role": new_perm.get("role", "READER").upper(),
                                    "type": perm_type.upper(),
                                    "externalPermissionId": new_perm.get("id"),
                                    "createdAtTimestamp": timestamp,
                                    "updatedAtTimestamp": timestamp,
                                    "lastUpdatedTimestampAtSource": timestamp,
                                }],
                                collection=CollectionNames.PERMISSION.value,
                                transaction=transaction
                            )

                elif perm_type == "anyone":
                    # Create 'anyone' nodes
                    anyone_nodes = []
                    for new_perm in new_perms:
                        anyone_nodes.append({
                            "type": "anyone",
                            "file_key": file_key,
                            "organization": org_id,
                            "role": new_perm.get("role", "READER").upper(),
                            "externalPermissionId": new_perm.get("id"),
                            "lastUpdatedTimestampAtSource": timestamp,
                            "active": True,
                        })

                    if anyone_nodes:
                        await self.batch_upsert_nodes(
                            nodes=anyone_nodes,
                            collection=CollectionNames.ANYONE.value,
                            transaction=transaction
                        )

            self.logger.info(f"✅ Processed {len(permissions)} permissions for file {file_key}")

        except Exception as e:
            self.logger.error(f"❌ Process file permissions failed: {str(e)}")
            raise

    async def delete_records_and_relations(
        self,
        record_key: str,
        hard_delete: bool = False,
        transaction: Optional[str] = None
    ) -> None:
        """
        Delete a record and all its relations.

        Generic implementation that:
        1. Deletes all edges from the record
        2. Deletes all edges to the record
        3. Deletes the IS_OF_TYPE edge
        4. Deletes the specific type node (e.g., files/{key})
        5. Optionally hard deletes the record node

        Args:
            record_key: Record key to delete
            hard_delete: If True, deletes the record node; if False, marks as deleted
            transaction: Optional transaction ID
        """
        try:
            self.logger.info(f"🚀 Deleting record and relations for {record_key}")

            record_id = f"{CollectionNames.RECORDS.value}/{record_key}"

            # 1. Delete all edges FROM this record using generic method
            deleted_from = await self.delete_edges_from(
                from_key=record_id,
                collection=CollectionNames.RECORD_RELATIONS.value,
                transaction=transaction
            )
            self.logger.info(f"🗑️ Deleted {deleted_from} edges from record")

            # 2. Delete all edges TO this record using generic method
            deleted_to = await self.delete_edges_to(
                to_key=record_id,
                collection=CollectionNames.RECORD_RELATIONS.value,
                transaction=transaction
            )
            self.logger.info(f"🗑️ Deleted {deleted_to} edges to record")

            # 3. Delete permission edges using generic method
            perm_deleted = await self.delete_edges_to(
                to_key=record_id,
                collection=CollectionNames.PERMISSION.value,
                transaction=transaction
            )
            self.logger.info(f"🗑️ Deleted {perm_deleted} permission edges")

            # 4. Delete IS_OF_TYPE edges using generic method
            type_deleted = await self.delete_edges_from(
                from_key=record_id,
                collection=CollectionNames.IS_OF_TYPE.value,
                transaction=transaction
            )
            self.logger.info(f"🗑️ Deleted {type_deleted} IS_OF_TYPE edges")

            # 5. Delete from specific type collections (files, emails, etc.)
            for collection in [CollectionNames.FILES.value, CollectionNames.MAILS.value]:
                try:
                    await self.delete_nodes(
                        keys=[record_key],
                        collection=collection,
                        transaction=transaction
                    )
                except Exception:
                    pass  # Collection might not have this document

            # 6. Delete or mark the record node
            if hard_delete:
                # Hard delete the record node
                await self.delete_nodes(
                    keys=[record_key],
                    collection=CollectionNames.RECORDS.value,
                    transaction=transaction
                )
                self.logger.info(f"🗑️ Hard deleted record {record_key}")
            else:
                # Soft delete - mark as deleted
                await self.update_node(
                    key=record_key,
                    collection=CollectionNames.RECORDS.value,
                    updates={"isDeleted": True, "updatedAtTimestamp": get_epoch_timestamp_in_ms()},
                    transaction=transaction
                )
                self.logger.info(f"🗑️ Soft deleted record {record_key}")

            self.logger.info(f"✅ Successfully deleted record and relations for {record_key}")

        except Exception as e:
            self.logger.error(f"❌ Delete records and relations failed: {str(e)}")
            raise

    async def get_key_by_external_file_id(
        self,
        external_file_id: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """
        Get key by external file ID.

        Uses get_record_by_external_id internally.
        """
        record = await self.get_record_by_external_id(
            connector_name=Connectors.GOOGLE_DRIVE,
            external_id=external_file_id,
            transaction=transaction
        )
        return record.get("_key") if record else None

    async def get_app_by_name(
        self,
        app_name: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get app by name.

        Generic method using filters.
        """
        query = f"""
        FOR app IN {CollectionNames.APPS.value}
            FILTER LOWER(app.name) == LOWER(@app_name)
            LIMIT 1
            RETURN app
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={"app_name": app_name},
                txn_id=transaction
            )
            return results[0] if results else None
        except Exception as e:
            self.logger.error(f"❌ Get app by name failed: {str(e)}")
            return None

    async def get_user_sync_state(
        self,
        user_email: str,
        service_type: str
    ) -> Optional[Dict]:
        """
        Get user's sync state for a specific service.

        Queries the user-app relation edge to get sync state.
        """
        try:
            user_key = await self.get_entity_id_by_email(user_email)
            if not user_key:
                return None

            query = f"""
            LET app = FIRST(FOR a IN {CollectionNames.APPS.value}
                          FILTER LOWER(a.name) == LOWER(@service_type)
                          RETURN {{ _key: a._key, name: a.name }})

            LET edge = FIRST(
                FOR rel in {CollectionNames.USER_APP_RELATION.value}
                    FILTER rel._from == CONCAT('users/', @user_key)
                    FILTER rel._to == CONCAT('apps/', app._key)
                    RETURN rel
            )

            RETURN edge
            """

            results = await self.http_client.execute_aql(
                query,
                bind_vars={"user_key": user_key, "service_type": service_type}
            )

            return results[0] if results else None
        except Exception as e:
            self.logger.error(f"❌ Get user sync state failed: {str(e)}")
            return None

    async def update_user_sync_state(
        self,
        user_email: str,
        sync_state: str,
        service_type: str = "drive"
    ) -> None:
        """
        Update user's sync state for a specific service.

        Updates the user-app relation edge.
        """
        try:
            user_key = await self.get_entity_id_by_email(user_email)
            if not user_key:
                return

            query = f"""
            LET app = FIRST(FOR a IN {CollectionNames.APPS.value}
                          FILTER LOWER(a.name) == LOWER(@service_type)
                          RETURN {{ _key: a._key }})

            FOR rel in {CollectionNames.USER_APP_RELATION.value}
                FILTER rel._from == CONCAT('users/', @user_key)
                FILTER rel._to == CONCAT('apps/', app._key)
                UPDATE rel WITH {{ syncState: @state, lastSyncUpdate: @lastSyncUpdate }}
                IN {CollectionNames.USER_APP_RELATION.value}
                RETURN NEW
            """

            await self.http_client.execute_aql(
                query,
                bind_vars={
                    "user_key": user_key,
                    "service_type": service_type,
                    "state": sync_state,
                    "lastSyncUpdate": get_epoch_timestamp_in_ms()
                }
            )
        except Exception as e:
            self.logger.error(f"❌ Update user sync state failed: {str(e)}")

    async def get_drive_sync_state(
        self,
        drive_id: str
    ) -> Optional[str]:
        """
        Get drive's sync state.

        Uses generic get_nodes_by_filters.
        """
        drives = await self.get_nodes_by_filters(
            collection=CollectionNames.DRIVES.value,
            filters={"id": drive_id}
        )

        if drives:
            return drives[0].get("sync_state")
        return None

    async def update_drive_sync_state(
        self,
        drive_id: str,
        sync_state: str
    ) -> None:
        """
        Update drive's sync state.

        Uses generic update_node.
        """
        try:
            # Get the drive first to get its key
            drives = await self.get_nodes_by_filters(
                collection=CollectionNames.DRIVES.value,
                filters={"id": drive_id}
            )

            if not drives:
                self.logger.warning(f"⚠️ Drive not found: {drive_id}")
                return

            drive_key = drives[0].get("_key")

            # Update using update_node
            await self.update_node(
                key=drive_key,
                collection=CollectionNames.DRIVES.value,
                updates={
                    "sync_state": sync_state,
                    "last_sync_update": get_epoch_timestamp_in_ms()
                }
            )
        except Exception as e:
            self.logger.error(f"❌ Update drive sync state failed: {str(e)}")

    async def store_page_token(
        self,
        channel_id: str,
        resource_id: str,
        user_email: str,
        token: str,
        expiration: str
    ) -> None:
        """
        Store page token for a channel/resource.

        Uses generic batch_upsert_nodes.
        """
        try:
            token_doc = {
                "_key": user_email.replace("@", "_at_").replace(".", "_"),
                "channelId": channel_id,
                "resourceId": resource_id,
                "userEmail": user_email,
                "token": token,
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "expiration": expiration,
            }

            await self.batch_upsert_nodes(
                nodes=[token_doc],
                collection=CollectionNames.PAGE_TOKENS.value
            )
        except Exception as e:
            self.logger.error(f"❌ Store page token failed: {str(e)}")

    async def get_page_token_db(
        self,
        channel_id: Optional[str] = None,
        resource_id: Optional[str] = None,
        user_email: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get page token from database.

        Uses generic get_nodes_by_filters.
        """
        try:
            filters = {}
            if channel_id is not None:
                filters["channelId"] = channel_id
            if resource_id is not None:
                filters["resourceId"] = resource_id
            if user_email is not None:
                filters["userEmail"] = user_email

            if not filters:
                return None

            tokens = await self.get_nodes_by_filters(
                collection=CollectionNames.PAGE_TOKENS.value,
                filters=filters
            )

            if tokens:
                # Sort by timestamp descending and return first
                tokens.sort(key=lambda x: x.get("createdAtTimestamp", 0), reverse=True)
                return tokens[0]

            return None
        except Exception as e:
            self.logger.error(f"❌ Get page token failed: {str(e)}")
            return None

    async def check_collection_has_document(
        self,
        collection_name: str,
        document_id: str
    ) -> bool:
        """
        Check if collection has document.

        Uses get_document internally.
        """
        doc = await self.get_document(document_id, collection_name)
        return doc is not None

    async def check_edge_exists(
        self,
        from_id: str,
        to_id: str,
        collection: str
    ) -> bool:
        """
        Check if edge exists between two nodes.

        Generic method that works with any edge collection.
        """
        query = f"""
        FOR edge IN {collection}
            FILTER edge._from == @from_id
            AND edge._to == @to_id
            LIMIT 1
            RETURN edge
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={"from_id": from_id, "to_id": to_id}
            )
            return len(results) > 0
        except Exception as e:
            self.logger.error(f"❌ Check edge exists failed: {str(e)}")
            return False

    async def get_failed_records_with_active_users(
        self,
        org_id: str,
        connector_name: Connectors
    ) -> List[Dict]:
        """
        Get failed records along with active users who have permissions.

        Generic method that can be used for any connector.
        """
        query = """
        FOR doc IN records
            FILTER doc.orgId == @org_id
            AND doc.indexingStatus == "FAILED"
            AND doc.connectorName == @connector_name

            LET active_users = (
                FOR perm IN permission
                    FILTER perm._to == doc._id
                    FOR user IN users
                        FILTER perm._from == user._id
                        AND user.isActive == true
                    RETURN DISTINCT user
            )

            FILTER LENGTH(active_users) > 0

            RETURN {
                record: doc,
                users: active_users
            }
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "org_id": org_id,
                    "connector_name": connector_name.value
                }
            )
            return results if results else []
        except Exception as e:
            self.logger.error(f"❌ Get failed records with active users failed: {str(e)}")
            return []

    async def get_failed_records_by_org(
        self,
        org_id: str,
        connector_name: Connectors
    ) -> List[Dict]:
        """
        Get all failed records for an organization and connector.

        Generic method using filters instead of embedded AQL.
        """
        # Use generic get_nodes_by_filters method
        return await self.get_nodes_by_filters(
            collection=CollectionNames.RECORDS.value,
            filters={
                "orgId": org_id,
                "indexingStatus": "FAILED",
                "connectorName": connector_name.value
            }
        )

    async def organization_exists(
        self,
        organization_name: str
    ) -> bool:
        """Check if organization exists"""
        try:
            query = """
            FOR org IN @@collection
                FILTER org.name == @organization_name
                LIMIT 1
                RETURN org
            """
            bind_vars = {
                "@collection": CollectionNames.ORGS.value,
                "organization_name": organization_name
            }

            results = await self.http_client.execute_aql(query, bind_vars)
            return len(results) > 0

        except Exception as e:
            self.logger.error(f"❌ Organization exists check failed: {str(e)}")
            return False

    async def delete_edges_to_groups(
        self,
        from_key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> int:
        """Delete edges to groups"""
        try:
            query = """
            FOR edge IN @@edge_collection
                FILTER edge._from == @from_key
                FOR group IN @@group_collection
                    FILTER group._id == edge._to
                    REMOVE edge IN @@edge_collection
                    RETURN OLD
            """
            bind_vars = {
                "@edge_collection": collection,
                "@group_collection": CollectionNames.GROUPS.value,
                "from_key": from_key
            }

            results = await self.http_client.execute_aql(query, bind_vars, transaction)
            return len(results)

        except Exception as e:
            self.logger.error(f"❌ Delete edges to groups failed: {str(e)}")
            raise

    async def delete_edges_between_collections(
        self,
        from_key: str,
        edge_collection: str,
        to_collection: str,
        transaction: Optional[str] = None
    ) -> None:
        """Delete edges between collections"""
        try:
            query = """
            FOR edge IN @@edge_collection
                FILTER edge._from == @from_key
                AND STARTS_WITH(edge._to, @to_collection)
                REMOVE edge IN @@edge_collection
            """
            bind_vars = {
                "@edge_collection": edge_collection,
                "from_key": from_key,
                "to_collection": f"{to_collection}/"
            }

            await self.http_client.execute_aql(query, bind_vars, transaction)

        except Exception as e:
            self.logger.error(f"❌ Delete edges between collections failed: {str(e)}")
            raise

    async def delete_nodes_and_edges(
        self,
        keys: List[str],
        collection: str,
        graph_name: str = "knowledgeGraph",
        transaction: Optional[str] = None
    ) -> None:
        """
        Delete nodes and all their connected edges.

        Generic implementation that:
        1. Deletes all edges FROM the nodes
        2. Deletes all edges TO the nodes
        3. Deletes the nodes themselves
        """
        try:
            for key in keys:
                node_id = f"{collection}/{key}"

                # Delete all edges FROM this node (in all edge collections)
                for edge_collection in [
                    CollectionNames.PERMISSION.value,
                    CollectionNames.BELONGS_TO.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.INHERIT_PERMISSIONS.value,
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.USER_APP_RELATION.value,
                ]:
                    await self.delete_edges_from(node_id, edge_collection, transaction)

                # Delete all edges TO this node (in all edge collections)
                for edge_collection in [
                    CollectionNames.PERMISSION.value,
                    CollectionNames.BELONGS_TO.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.INHERIT_PERMISSIONS.value,
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.USER_APP_RELATION.value,
                ]:
                    await self.delete_edges_to(node_id, edge_collection, transaction)

                # Delete the node itself
                await self.delete_nodes([key], collection, transaction)

            self.logger.info(f"✅ Deleted {len(keys)} nodes and their edges from {collection}")

        except Exception as e:
            self.logger.error(f"❌ Delete nodes and edges failed: {str(e)}")
            raise

    async def update_edge(
        self,
        from_key: str,
        to_key: str,
        edge_updates: Dict,
        collection: str,
        transaction: Optional[str] = None
    ) -> bool:
        """Update edge"""
        try:
            query = """
            FOR edge IN @@collection
                FILTER edge._from == @from_key
                AND edge._to == @to_key
                UPDATE edge WITH @updates IN @@collection
                RETURN NEW
            """
            bind_vars = {
                "@collection": collection,
                "from_key": from_key,
                "to_key": to_key,
                "updates": edge_updates
            }

            results = await self.http_client.execute_aql(query, bind_vars, transaction)
            return len(results) > 0

        except Exception as e:
            self.logger.error(f"❌ Update edge failed: {str(e)}")
            return False

