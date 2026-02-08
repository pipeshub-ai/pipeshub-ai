"""
ArangoDB HTTP Provider Implementation

Fully async implementation of IGraphDBProvider using ArangoDB REST API.
This replaces the synchronous python-arango SDK with async HTTP calls.

All operations are non-blocking and use aiohttp for async I/O.
"""
import time
import uuid
from logging import Logger
from typing import Any, Dict, List, Optional, Tuple, Union

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    RECORD_TYPE_COLLECTION_MAPPING,
    CollectionNames,
    Connectors,
    GraphNames,
    OriginTypes,
)
from app.config.constants.service import config_node_constants
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    CommentRecord,
    FileRecord,
    LinkRecord,
    MailRecord,
    Person,
    ProjectRecord,
    Record,
    RecordGroup,
    RecordType,
    TicketRecord,
    User,
    WebpageRecord,
)
from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Constants for ArangoDB document ID format
ARANGO_ID_PARTS_COUNT = 2  # ArangoDB document IDs are in format "collection/key"


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

        # Connector-specific delete permissions
        self.connector_delete_permissions = {
            Connectors.GOOGLE_DRIVE.value: {
                "allowed_roles": ["OWNER", "WRITER", "FILEORGANIZER"],
                "edge_collections": [
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.PERMISSION.value,
                    CollectionNames.USER_DRIVE_RELATION.value,
                    CollectionNames.BELONGS_TO.value,
                    CollectionNames.ANYONE.value
                ],
                "document_collections": [
                    CollectionNames.RECORDS.value,
                    CollectionNames.FILES.value,
                ]
            },
            Connectors.GOOGLE_MAIL.value: {
                "allowed_roles": ["OWNER", "WRITER"],
                "edge_collections": [
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.PERMISSION.value,
                    CollectionNames.BELONGS_TO.value,
                ],
                "document_collections": [
                    CollectionNames.RECORDS.value,
                    CollectionNames.MAILS.value,
                    CollectionNames.FILES.value,  # For attachments
                ]
            },
            Connectors.OUTLOOK.value: {
                "allowed_roles": ["OWNER", "WRITER"],
                "edge_collections": [
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.PERMISSION.value,
                    CollectionNames.BELONGS_TO.value,
                ],
                "document_collections": [
                    CollectionNames.RECORDS.value,
                    CollectionNames.MAILS.value,
                    CollectionNames.FILES.value,
                ]
            },
            Connectors.KNOWLEDGE_BASE.value: {
                "allowed_roles": ["OWNER", "WRITER", "FILEORGANIZER"],
                "edge_collections": [
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.BELONGS_TO.value,
                    CollectionNames.PERMISSION.value,
                ],
                "document_collections": [
                    CollectionNames.RECORDS.value,
                    CollectionNames.FILES.value,
                ]
            }
        }

    # ==================== Translation Layer ====================
    # Methods to translate between generic format and ArangoDB-specific format

    def _translate_node_to_arango(self, node: Dict) -> Dict:
        """
        Translate generic node format to ArangoDB format.

        Converts 'id' field to '_key' for ArangoDB storage.

        Args:
            node: Node in generic format (with 'id' field)

        Returns:
            Node in ArangoDB format (with '_key' field)
        """
        arango_node = node.copy()
        if "id" in arango_node:
            arango_node["_key"] = arango_node.pop("id")
        return arango_node

    def _translate_node_from_arango(self, arango_node: Dict) -> Dict:
        """
        Translate ArangoDB node to generic format.

        Converts '_key' field to 'id' for generic representation.

        Args:
            arango_node: Node in ArangoDB format (with '_key' field)

        Returns:
            Node in generic format (with 'id' field)
        """
        node = arango_node.copy()
        if "_key" in node:
            node["id"] = node.pop("_key")
        return node

    def _translate_edge_to_arango(self, edge: Dict) -> Dict:
        """
        Translate generic edge format to ArangoDB format.

        Converts:
        - from_id + from_collection → _from: "collection/id"
        - to_id + to_collection → _to: "collection/id"

        Handles both old format (already has _from/_to) and new generic format
        for backward compatibility during transition.

        Args:
            edge: Edge in generic format

        Returns:
            Edge in ArangoDB format
        """
        arango_edge = edge.copy()

        # Handle new generic format
        if "from_id" in edge and "from_collection" in edge:
            arango_edge["_from"] = f"{edge['from_collection']}/{edge['from_id']}"
            arango_edge.pop("from_id", None)
            arango_edge.pop("from_collection", None)

        if "to_id" in edge and "to_collection" in edge:
            arango_edge["_to"] = f"{edge['to_collection']}/{edge['to_id']}"
            arango_edge.pop("to_id", None)
            arango_edge.pop("to_collection", None)

        # If neither format is present, edge is already in old format (_from/_to)
        # Just return as-is for backward compatibility

        return arango_edge

    def _translate_edge_from_arango(self, arango_edge: Dict) -> Dict:
        """
        Translate ArangoDB edge to generic format.

        Converts:
        - _from: "collection/id" → from_collection + from_id
        - _to: "collection/id" → to_collection + to_id

        Args:
            arango_edge: Edge in ArangoDB format

        Returns:
            Edge in generic format
        """
        edge = arango_edge.copy()

        if "_from" in edge:
            from_parts = edge["_from"].split("/", 1)
            if len(from_parts) == ARANGO_ID_PARTS_COUNT:
                edge["from_collection"] = from_parts[0]
                edge["from_id"] = from_parts[1]
            edge.pop("_from", None)

        if "_to" in edge:
            to_parts = edge["_to"].split("/", 1)
            if len(to_parts) == ARANGO_ID_PARTS_COUNT:
                edge["to_collection"] = to_parts[0]
                edge["to_id"] = to_parts[1]
            edge.pop("_to", None)

        return edge

    def _translate_nodes_to_arango(self, nodes: List[Dict]) -> List[Dict]:
        """Batch translate nodes to ArangoDB format."""
        return [self._translate_node_to_arango(node) for node in nodes]

    def _translate_nodes_from_arango(self, arango_nodes: List[Dict]) -> List[Dict]:
        """Batch translate nodes from ArangoDB format."""
        return [self._translate_node_from_arango(node) for node in arango_nodes]

    def _translate_edges_to_arango(self, edges: List[Dict]) -> List[Dict]:
        """Batch translate edges to ArangoDB format."""
        return [self._translate_edge_to_arango(edge) for edge in edges]

    def _translate_edges_from_arango(self, arango_edges: List[Dict]) -> List[Dict]:
        """Batch translate edges from ArangoDB format."""
        return [self._translate_edge_from_arango(edge) for edge in arango_edges]

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
            document_key: Document key (generic 'id')
            collection: Collection name
            transaction: Optional transaction ID

        Returns:
            Optional[Dict]: Document data in generic format (with 'id' field) or None
        """
        try:
            doc = await self.http_client.get_document(
                collection, document_key, txn_id=transaction
            )
            if doc:
                # Translate from ArangoDB format to generic format
                return self._translate_node_from_arango(doc)
            return None
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
            nodes: List of node documents in generic format (with 'id' field)
            collection: Collection name
            transaction: Optional transaction ID

        Returns:
            Optional[bool]: True if successful
        """
        try:
            if not nodes:
                return True

            # Translate nodes from generic format to ArangoDB format
            arango_nodes = self._translate_nodes_to_arango(nodes)

            result = await self.http_client.batch_insert_documents(
                collection, arango_nodes, txn_id=transaction, overwrite=True
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

        Uses UPSERT to avoid duplicates - matches on _from and _to.

        Args:
            edges: List of edge documents in generic format (from_id, from_collection, to_id, to_collection)
            collection: Edge collection name
            transaction: Optional transaction ID

        Returns:
            bool: True if successful
        """
        try:
            if not edges:
                return True

            self.logger.info(f"🚀 Batch creating edges: {collection}")

            # Translate edges from generic format to ArangoDB format
            arango_edges = self._translate_edges_to_arango(edges)

            batch_query = """
            FOR edge IN @edges
                UPSERT { _from: edge._from, _to: edge._to }
                INSERT edge
                UPDATE edge
                IN @@collection
                RETURN NEW
            """
            bind_vars = {"edges": arango_edges, "@collection": collection}

            results = await self.http_client.execute_aql(
                batch_query,
                bind_vars,
                txn_id=transaction
            )

            self.logger.info(
                f"✅ Successfully created {len(results)} edges in collection '{collection}'."
            )
            return True

        except Exception as e:
            self.logger.error(f"❌ Batch edge creation failed: {str(e)}")
            raise

    async def batch_create_entity_relations(
        self,
        edges: List[Dict],
        transaction: Optional[str] = None
    ) -> bool:
        """
        Batch create entity relation edges - FULLY ASYNC.

        Uses UPSERT to avoid duplicates - matches on _from, _to, and edgeType.
        This is specialized for entityRelations collection where multiple edges
        can exist between the same entities with different edgeType values (e.g., ASSIGNED_TO, CREATED_BY, REPORTED_BY).

        Args:
            edges: List of edge documents with _from, _to, and edgeType
            transaction: Optional transaction ID

        Returns:
            bool: True if successful
        """
        try:
            if not edges:
                return True

            self.logger.info("🚀 Batch creating entity relation edges")

            # Translate edges from generic format to ArangoDB format
            arango_edges = self._translate_edges_to_arango(edges)

            # For entity relations, include edgeType in the UPSERT match condition
            batch_query = """
            FOR edge IN @edges
                UPSERT { _from: edge._from, _to: edge._to, edgeType: edge.edgeType }
                INSERT edge
                UPDATE edge
                IN @@collection
                RETURN NEW
            """
            bind_vars = {
                "edges": arango_edges,
                "@collection": CollectionNames.ENTITY_RELATIONS.value
            }

            results = await self.http_client.execute_aql(
                batch_query,
                bind_vars,
                txn_id=transaction
            )

            self.logger.info(
                f"✅ Successfully created {len(results)} entity relation edges."
            )
            return True

        except Exception as e:
            self.logger.error(f"❌ Batch entity relation creation failed: {str(e)}")
            raise

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
        Get an edge between two nodes - FULLY ASYNC.

        Args:
            from_id: Source node ID
            from_collection: Source node collection name
            to_id: Target node ID
            to_collection: Target node collection name
            collection: Edge collection name
            transaction: Optional transaction ID

        Returns:
            Optional[Dict]: Edge data in generic format or None
        """
        # Construct ArangoDB-style _from and _to values
        from_node = f"{from_collection}/{from_id}"
        to_node = f"{to_collection}/{to_id}"

        query = f"""
        FOR edge IN {collection}
            FILTER edge._from == @from_node AND edge._to == @to_node
            LIMIT 1
            RETURN edge
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                {"from_node": from_node, "to_node": to_node},
                txn_id=transaction
            )
            if results:
                # Translate from ArangoDB format to generic format
                return self._translate_edge_from_arango(results[0])
            return None
        except Exception as e:
            self.logger.error(f"❌ Get edge failed: {str(e)}")
            return None

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
        Delete an edge - FULLY ASYNC.

        Args:
            from_id: Source node ID
            from_collection: Source node collection name
            to_id: Target node ID
            to_collection: Target node collection name
            collection: Edge collection name
            transaction: Optional transaction ID

        Returns:
            bool: True if successful, False otherwise
        """
        # Construct ArangoDB-style _from and _to values
        from_node = f"{from_collection}/{from_id}"
        to_node = f"{to_collection}/{to_id}"

        return await self.http_client.delete_edge(
            collection, from_node, to_node, txn_id=transaction
        )

    async def delete_edges_from(
        self,
        from_id: str,
        from_collection: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> int:
        """
        Delete all edges from a node - FULLY ASYNC.

        Args:
            from_id: Source node ID
            from_collection: Source node collection name
            collection: Edge collection name
            transaction: Optional transaction ID

        Returns:
            int: Number of edges deleted
        """
        # Construct ArangoDB-style _from value
        from_node = f"{from_collection}/{from_id}"

        query = f"""
        FOR edge IN {collection}
            FILTER edge._from == @from_node
            REMOVE edge IN {collection}
            RETURN OLD
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                {"from_node": from_node},
                txn_id=transaction
            )
            return len(results)
        except Exception as e:
            self.logger.error(f"❌ Delete edges from failed: {str(e)}")
            raise

    async def delete_edges_by_relationship_types(
        self,
        from_id: str,
        from_collection: str,
        collection: str,
        relationship_types: List[str],
        transaction: Optional[str] = None
    ) -> int:
        """
        Delete edges by relationship types from a node - FULLY ASYNC.

        Args:
            from_id: Source node ID
            from_collection: Source node collection name
            collection: Edge collection name
            relationship_types: List of relationship type values to delete
            transaction: Optional transaction ID

        Returns:
            int: Number of edges deleted
        """
        if not relationship_types:
            return 0

        from_node = f"{from_collection}/{from_id}"

        query = f"""
        FOR edge IN {collection}
            FILTER edge._from == @from_node
            FILTER edge.relationshipType IN @relationship_types
            REMOVE edge IN {collection}
            RETURN OLD
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                {
                    "from_node": from_node,
                    "relationship_types": relationship_types
                },
                txn_id=transaction
            )
            return len(results)
        except Exception as e:
            self.logger.error(
                f"❌ Delete edges by relationship types failed: {str(e)}"
            )
            raise

    async def delete_edges_to(
        self,
        to_id: str,
        to_collection: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> int:
        """
        Delete all edges to a node - FULLY ASYNC.

        Args:
            to_id: Target node ID
            to_collection: Target node collection name
            collection: Edge collection name
            transaction: Optional transaction ID

        Returns:
            int: Number of edges deleted
        """
        # Construct ArangoDB-style _to value
        to_node = f"{to_collection}/{to_id}"

        query = f"""
        FOR edge IN {collection}
            FILTER edge._to == @to_node
            REMOVE edge IN {collection}
            RETURN OLD
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                {"to_node": to_node},
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

    async def get_edges_from_node(
        self,
        node_id: str,
        edge_collection: str,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get all edges originating from a node.

        Args:
            node_id: Source node ID (e.g., "groups/123")
            edge_collection: Edge collection name
            transaction: Optional transaction ID

        Returns:
            List[Dict]: List of edges
        """
        query = f"""
        FOR edge IN {edge_collection}
            FILTER edge._from == @node_id
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
            self.logger.error(f"❌ Get edges from node failed: {str(e)}")
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
        connector_id: str,
        external_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Record]:
        """Get record by external ID"""
        query = f"""
        FOR doc IN {CollectionNames.RECORDS.value}
            FILTER doc.externalRecordId == @external_id
            AND doc.connectorId == @connector_id
            LIMIT 1
            RETURN doc
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "external_id": external_id,
                    "connector_id": connector_id
                },
                txn_id=transaction
            )
            if results:
                record_data = self._translate_node_from_arango(results[0])
                return Record.from_arango_base_record(record_data)
            return None
        except Exception as e:
            self.logger.error(f"❌ Get record by external ID failed: {str(e)}")
            return None

    async def get_record_path(
        self,
        record_id: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """
        Get full hierarchical path for a record by traversing graph bottom to top.

        Traverses up through RECORD_RELATIONS edges (PARENT_CHILD relationship)
        to build a path like: "Folder1/Subfolder/File.txt"

        Args:
            record_id: The record key to get the path for
            transaction: Optional transaction ID

        Returns:
            Optional[str]: The full path as a string (e.g., "Folder1/Subfolder/File.txt")
                        or None if record not found

        Hardcoded depth to 100
        """
        try:
            query = """
            LET start_record = DOCUMENT(@records_collection, @record_id)
            FILTER start_record != null
            // Only follow the canonical parent (externalParentId) so duplicate/stale edges don't produce wrong paths
            LET ancestors = (
                FOR v, e, p IN 1..100 INBOUND start_record
                    GRAPH @graph_name
                    FILTER e.relationshipType == 'PARENT_CHILD'
                    FILTER v.externalRecordId == p.vertices[LENGTH(p.vertices)-2].externalParentId
                    RETURN v.recordName
            )
            LET path_order = REVERSE(ancestors)
            LET full_path_list = APPEND(path_order, start_record.recordName)
            LET clean_path = (
                FOR name IN full_path_list
                FILTER name != null AND name != ""
                RETURN name
            )
            RETURN CONCAT_SEPARATOR('/', clean_path)
            """

            result = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "record_id": record_id,
                    "records_collection": CollectionNames.RECORDS.value,
                    "graph_name": GraphNames.KNOWLEDGE_GRAPH.value,
                },
                txn_id=transaction
            )

            if result and len(result) > 0:
                path = result[0]
                self.logger.debug(f"✅ Found path for {record_id}: {path}")
                return path
            return None

        except Exception as e:
            self.logger.error(f"❌ Failed to get record path for {record_id}: {str(e)}")
            return None

    async def get_record_by_external_revision_id(
        self,
        connector_id: str,
        external_revision_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Record]:
        """Get record by external revision ID (e.g., etag)"""
        query = f"""
        FOR doc IN {CollectionNames.RECORDS.value}
            FILTER doc.externalRevisionId == @external_revision_id
            AND doc.connectorId == @connector_id
            LIMIT 1
            RETURN doc
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "external_revision_id": external_revision_id,
                    "connector_id": connector_id
                },
                txn_id=transaction
            )
            if results:
                record_data = self._translate_node_from_arango(results[0])
                return Record.from_arango_base_record(record_data)
            return None
        except Exception as e:
            self.logger.error(f"❌ Get record by external revision ID failed: {str(e)}")
            return None

    async def get_record_key_by_external_id(
        self,
        external_id: str,
        connector_id: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """Get record key by external ID"""
        try:
            query = """
            FOR record IN @@collection
                FILTER record.externalRecordId == @external_id
                AND record.connectorId == @connector_id
                LIMIT 1
                RETURN record._key
            """
            bind_vars = {
                "@collection": CollectionNames.RECORDS.value,
                "external_id": external_id,
                "connector_id": connector_id
            }

            results = await self.http_client.execute_aql(query, bind_vars, transaction)
            return results[0] if results else None

        except Exception as e:
            self.logger.error(f"❌ Get record key by external ID failed: {str(e)}")
            return None

    async def get_record_by_path(
        self,
        connector_id: str,
        path: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get a record from the FILES collection using its path.

        Args:
            connector_id (str): The ID of the connector.
            path (str): The path of the file to look up.
            transaction (Optional[str]): Optional transaction ID.

        Returns:
            Optional[Dict]: The file record if found, otherwise None.
        """
        try:
            self.logger.info(
                f"🚀 Retrieving record by path for connector {connector_id} and path {path}"
            )

            query = f"""
            FOR fileRecord IN {CollectionNames.FILES.value}
                FILTER fileRecord.path == @path
                RETURN fileRecord
            """

            results = await self.http_client.execute_aql(
                query,
                bind_vars={"path": path},
                txn_id=transaction
            )

            if results:
                self.logger.info(
                    f"✅ Successfully retrieved file record for path: {path}"
                )
                return results[0]
            else:
                self.logger.warning(
                    f"⚠️ No record found for path: {path}"
                )
                return None

        except Exception as e:
            self.logger.error(
                f"❌ Failed to retrieve record for path {path}: {str(e)}"
            )
            return None

    async def get_records_by_status(
        self,
        org_id: str,
        connector_id: str,
        status_filters: List[str],
        limit: Optional[int] = None,
        offset: int = 0,
        transaction: Optional[str] = None
    ) -> List[Record]:
        """
        Get records by their indexing status with pagination support.
        Returns properly typed Record instances (FileRecord, MailRecord, etc.)
        """
        try:
            self.logger.info(f"Retrieving records for connector {connector_id} with status filters: {status_filters}, limit: {limit}, offset: {offset}")

            limit_clause = "LIMIT @offset, @limit" if limit else ""

            # Group record types by their collection
            from collections import defaultdict
            collection_to_types = defaultdict(list)
            for record_type, collection in RECORD_TYPE_COLLECTION_MAPPING.items():
                collection_to_types[collection].append(record_type)

            # Build dynamic typeDoc conditions based on mapping
            type_doc_conditions = []
            bind_vars = {
                "org_id": org_id,
                "connector_id": connector_id,
                "status_filters": status_filters,
            }

            # Generate conditions for each collection
            for collection, record_types in collection_to_types.items():
                # Create condition for checking if record type matches any in this group
                if len(record_types) == 1:
                    type_check = f"record.recordType == @type_{record_types[0].lower()}"
                    bind_vars[f"type_{record_types[0].lower()}"] = record_types[0]
                else:
                    # Multiple types map to same collection
                    type_checks = []
                    for rt in record_types:
                        type_checks.append(f"record.recordType == @type_{rt.lower()}")
                        bind_vars[f"type_{rt.lower()}"] = rt
                    type_check = " || ".join(type_checks)

                # Add condition for this collection
                condition = f"""({type_check}) ? (
                        FOR edge IN {CollectionNames.IS_OF_TYPE.value}
                            FILTER edge._from == record._id
                            LET doc = DOCUMENT(edge._to)
                            FILTER doc != null
                            RETURN doc
                    )[0]"""
                type_doc_conditions.append(condition)

            # Build the complete typeDoc expression
            type_doc_expr = " :\n                    ".join(type_doc_conditions)
            if type_doc_expr:
                type_doc_expr += " :\n                    null"
            else:
                type_doc_expr = "null"

            query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record.orgId == @org_id
                    AND record.connectorId == @connector_id
                    AND record.indexingStatus IN @status_filters
                SORT record._key
                {limit_clause}

                LET typeDoc = (
                    {type_doc_expr}
                )

                RETURN {{
                    record: record,
                    typeDoc: typeDoc
                }}
            """

            if limit:
                bind_vars["limit"] = limit
                bind_vars["offset"] = offset

            results = await self.http_client.execute_aql(query, bind_vars, transaction)

            # Convert raw DB results to properly typed Record instances
            typed_records = []
            for result in results:
                record = self._create_typed_record_from_arango(
                    result["record"],
                    result.get("typeDoc")
                )
                typed_records.append(record)

            self.logger.info(f"✅ Successfully retrieved {len(typed_records)} typed records for connector {connector_id}")
            return typed_records

        except Exception as e:
            self.logger.error(f"❌ Failed to retrieve records by status for connector {connector_id}: {str(e)}")
            return []

    def _create_typed_record_from_arango(self, record_dict: Dict, type_doc: Optional[Dict]) -> Record:
        """
        Factory method to create properly typed Record instances from ArangoDB data.
        Uses centralized RECORD_TYPE_COLLECTION_MAPPING to determine which types have type collections.

        Args:
            record_dict: Dictionary from records collection
            type_doc: Dictionary from type-specific collection (files, mails, etc.) or None

        Returns:
            Properly typed Record instance (FileRecord, MailRecord, etc.)
        """
        record_type = record_dict.get("recordType")

        # Check if this record type has a type collection
        if not type_doc or record_type not in RECORD_TYPE_COLLECTION_MAPPING:
            # No type collection or no type doc - use base Record
            record_data = self._translate_node_from_arango(record_dict)
            return Record.from_arango_base_record(record_data)

        try:
            # Determine which collection this type uses
            collection = RECORD_TYPE_COLLECTION_MAPPING[record_type]

            # Apply translation to both documents
            type_doc_data = self._translate_node_from_arango(type_doc)
            record_data = self._translate_node_from_arango(record_dict)

            # Map collections to their corresponding Record classes
            if collection == CollectionNames.FILES.value:
                return FileRecord.from_arango_record(type_doc_data, record_data)
            elif collection == CollectionNames.MAILS.value:
                return MailRecord.from_arango_record(type_doc_data, record_data)
            elif collection == CollectionNames.WEBPAGES.value:
                return WebpageRecord.from_arango_record(type_doc_data, record_data)
            elif collection == CollectionNames.TICKETS.value:
                return TicketRecord.from_arango_record(type_doc_data, record_data)
            elif collection == CollectionNames.PROJECTS.value:
                return ProjectRecord.from_arango_record(type_doc_data, record_data)
            elif collection == CollectionNames.COMMENTS.value:
                return CommentRecord.from_arango_record(type_doc_data, record_data)
            elif collection == CollectionNames.LINKS.value:
                return LinkRecord.from_arango_record(type_doc_data, record_data)
            else:
                # Unknown collection - fallback to base Record
                return Record.from_arango_base_record(record_data)
        except Exception as e:
            self.logger.warning(f"Failed to create typed record for {record_type}, falling back to base Record: {str(e)}")
            record_data = self._translate_node_from_arango(record_dict)
            return Record.from_arango_base_record(record_data)

    async def get_record_by_conversation_index(
        self,
        connector_id: str,
        conversation_index: str,
        thread_id: str,
        org_id: str,
        user_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Record]:
        """Get record by conversation index"""
        try:

            query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record.connectorId == @connector_id
                    AND record.orgId == @org_id
                FOR mail IN {CollectionNames.MAILS.value}
                    FILTER mail._key == record._key
                        AND mail.conversationIndex == @conversation_index
                        AND mail.threadId == @thread_id
                    FOR edge IN {CollectionNames.PERMISSION.value}
                        FILTER edge._to == record._id
                            AND edge.role == 'OWNER'
                            AND edge.type == 'USER'
                        LET user_key = SPLIT(edge._from, '/')[1]
                        LET user = DOCUMENT('{CollectionNames.USERS.value}', user_key)
                        FILTER user.userId == @user_id
                        LIMIT 1
                    RETURN record
            """

            bind_vars = {
                "conversation_index": conversation_index,
                "thread_id": thread_id,
                "connector_id": connector_id,
                "org_id": org_id,
                "user_id": user_id
            }

            results = await self.http_client.execute_aql(query, bind_vars, transaction)
            if results:
                record_data = self._translate_node_from_arango(results[0])
                return Record.from_arango_base_record(record_data)
            return None

        except Exception as e:
            self.logger.error(f"❌ Get record by conversation index failed: {str(e)}")
            return None

    async def get_record_by_issue_key(
        self,
        connector_id: str,
        issue_key: str,
        transaction: Optional[str] = None
    ) -> Optional[Record]:
        """
        Get Jira issue record by issue key (e.g., PROJ-123) by searching weburl pattern.
        Returns a TicketRecord with the type field populated for proper Epic detection.

        Args:
            connector_id: Connector ID
            issue_key: Jira issue key (e.g., "PROJ-123")
            transaction: Optional transaction ID

        Returns:
            Optional[Record]: TicketRecord if found, None otherwise
        """
        try:
            self.logger.info(
                "🚀 Retrieving record for Jira issue key %s %s", connector_id, issue_key
            )

            # Search for record where weburl contains "/browse/{issue_key}" and record_type is TICKET
            # Also join with tickets collection to get the type field (for Epic detection)
            query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record.connectorId == @connector_id
                    AND record.recordType == @record_type
                    AND record.webUrl != null
                    AND CONTAINS(record.webUrl, @browse_pattern)
                LET ticket = DOCUMENT({CollectionNames.TICKETS.value}, record._key)
                LIMIT 1
                RETURN {{ record: record, ticket: ticket }}
            """

            browse_pattern = f"/browse/{issue_key}"
            bind_vars = {
                "connector_id": connector_id,
                "record_type": "TICKET",
                "browse_pattern": browse_pattern
            }

            results = await self.http_client.execute_aql(query, bind_vars, txn_id=transaction)

            if results and results[0]:
                result = results[0]
                record_dict = result.get("record")
                ticket_doc = result.get("ticket")

                self.logger.info(
                    "✅ Successfully retrieved record for Jira issue key %s %s", connector_id, issue_key
                )

                # Use the typed record factory to get a TicketRecord with the type field
                return self._create_typed_record_from_arango(record_dict, ticket_doc)
            else:
                self.logger.warning(
                    "⚠️ No record found for Jira issue key %s %s", connector_id, issue_key
                )
                return None

        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve record for Jira issue key %s %s: %s", connector_id, issue_key, str(e)
            )
            return None

    async def get_record_by_weburl(
        self,
        weburl: str,
        org_id: Optional[str] = None,
        transaction: Optional[str] = None
    ) -> Optional[Record]:
        """
        Get record by weburl (exact match).
        Skips LinkRecords and returns the first non-LinkRecord found.

        Args:
            weburl: Web URL to search for
            org_id: Optional organization ID to filter by
            transaction: Optional transaction ID

        Returns:
            Optional[Record]: First non-LinkRecord found, None otherwise
        """
        try:
            self.logger.info("🚀 Retrieving record by weburl: %s", weburl)

            # Get all records with this weburl (not just one)
            query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record.webUrl == @weburl
                {"AND record.orgId == @org_id" if org_id else ""}
                RETURN record
            """

            bind_vars = {"weburl": weburl}
            if org_id:
                bind_vars["org_id"] = org_id

            results = await self.http_client.execute_aql(query, bind_vars, txn_id=transaction)

            if results:
                # Skip LinkRecords and return the first non-LinkRecord found
                for record_dict in results:
                    record_data = self._translate_node_from_arango(record_dict)
                    record_type = record_data.get("recordType")

                    # Skip LinkRecords
                    if record_type == "LINK":
                        continue

                    # Return first non-LinkRecord found
                    self.logger.info("✅ Successfully retrieved record by weburl: %s", weburl)
                    return Record.from_arango_base_record(record_data)

                # All records were LinkRecords
                self.logger.debug("⚠️ Only LinkRecords found for weburl: %s", weburl)
                return None
            else:
                self.logger.warning("⚠️ No record found for weburl: %s", weburl)
                return None

        except Exception as e:
            self.logger.error("❌ Failed to retrieve record by weburl %s: %s", weburl, str(e))
            return None

    async def get_records_by_parent(
        self,
        connector_id: str,
        parent_external_record_id: str,
        record_type: Optional[str] = None,
        transaction: Optional[str] = None
    ) -> List[Record]:
        """
        Get all child records for a parent record by parent_external_record_id.
        Optionally filter by record_type.

        Args:
            connector_id: Connector ID
            parent_external_record_id: Parent record's external ID
            record_type: Optional filter by record type (e.g., "COMMENT", "FILE", "TICKET")
            transaction: Optional transaction ID

        Returns:
            List[Record]: List of child records
        """
        try:
            self.logger.debug(
                "🚀 Retrieving child records for parent %s %s (record_type: %s)",
                connector_id, parent_external_record_id, record_type or "all"
            )

            query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record.externalParentId != null
                    AND record.externalParentId == @parent_id
                    AND record.connectorId == @connector_id
            """

            bind_vars = {
                "parent_id": parent_external_record_id,
                "connector_id": connector_id
            }

            if record_type:
                query += " AND record.recordType == @record_type"
                bind_vars["record_type"] = record_type

            query += " RETURN record"

            results = await self.http_client.execute_aql(query, bind_vars, txn_id=transaction)

            records = [
                Record.from_arango_base_record(self._translate_node_from_arango(result))
                for result in results
            ]

            self.logger.debug(
                "✅ Successfully retrieved %d child record(s) for parent %s %s",
                len(records), connector_id, parent_external_record_id
            )
            return records

        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve child records for parent %s %s: %s",
                connector_id, parent_external_record_id, str(e)
            )
            return []

    async def get_record_group_by_external_id(
        self,
        connector_id: str,
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
            AND doc.connectorId == @connector_id
            LIMIT 1
            RETURN doc
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "external_id": external_id,
                    "connector_id": connector_id
                },
                txn_id=transaction
            )

            if results:
                # Convert to RecordGroup entity
                record_group_data = self._translate_node_from_arango(results[0])
                return RecordGroup.from_arango_base_record_group(record_group_data)

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
                file_data = self._translate_node_from_arango(file)
                record_data = self._translate_node_from_arango(record)
                return FileRecord.from_arango_record(
                    arango_base_file_record=file_data,
                    arango_base_record=record_data
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
        """
        query = f"""
        FOR user IN {CollectionNames.USERS.value}
            FILTER LOWER(user.email) == LOWER(@email)
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
                user_data = self._translate_node_from_arango(results[0])
                return User.from_arango_user(user_data)

            return None

        except Exception as e:
            self.logger.error(f"❌ Get user by email failed: {str(e)}")
            return None

    async def get_user_by_source_id(
        self,
        source_user_id: str,
        connector_id: str,
        transaction: Optional[str] = None
    ) -> Optional[User]:
        """
        Get a user by their source system ID (sourceUserId field in userAppRelation edge).

        Args:
            source_user_id: The user ID from the source system
            connector_id: Connector ID
            transaction: Optional transaction ID

        Returns:
            User object if found, None otherwise
        """
        try:
            self.logger.info(
                f"🚀 Retrieving user by source_id {source_user_id} for connector {connector_id}"
            )

            user_query = f"""
            // First find the app
            LET app = FIRST(
                FOR a IN {CollectionNames.APPS.value}
                    FILTER a._key == @connector_id
                    RETURN a
            )

            // Then find user connected via userAppRelation with matching sourceUserId
            FOR edge IN {CollectionNames.USER_APP_RELATION.value}
                FILTER edge._to == app._id
                FILTER edge.sourceUserId == @source_user_id
                LET user = DOCUMENT(edge._from)
                FILTER user != null
                LIMIT 1
                RETURN user
            """

            results = await self.http_client.execute_aql(
                user_query,
                bind_vars={
                    "connector_id": connector_id,
                    "source_user_id": source_user_id,
                },
                txn_id=transaction
            )

            if results:
                self.logger.info(f"✅ Successfully retrieved user by source_id {source_user_id}")
                user_data = self._translate_node_from_arango(results[0])
                return User.from_arango_user(user_data)
            else:
                self.logger.warning(f"⚠️ No user found for source_id {source_user_id}")
                return None

        except Exception as e:
            self.logger.error(
                f"❌ Failed to get user by source_id {source_user_id}: {str(e)}"
            )
            return None

    async def get_user_by_user_id(
        self,
        user_id: str
    ) -> Optional[Dict]:
        """
        Get user by user ID.
        Note: user_id is the userId field value, not the _key.
        """
        try:
            query = f"""
                FOR user IN {CollectionNames.USERS.value}
                    FILTER user.userId == @user_id
                    LIMIT 1
                    RETURN user
            """
            result = await self.http_client.execute_aql(
                query,
                bind_vars={"user_id": user_id}
            )
            return result[0] if result else None
        except Exception as e:
            self.logger.error(f"❌ Get user by user ID failed: {str(e)}")
            return None

    async def get_users(
        self,
        org_id: str,
        active: bool = True
    ) -> List[Dict]:
        """
        Fetch all active users from the database who belong to the organization.

        Args:
            org_id (str): Organization ID
            active (bool): Filter for active users only if True

        Returns:
            List[Dict]: List of user documents with their details
        """
        try:
            self.logger.info("🚀 Fetching all users from database")

            query = f"""
                FOR edge IN {CollectionNames.BELONGS_TO.value}
                    FILTER edge._to == CONCAT('organizations/', @org_id)
                    AND edge.entityType == 'ORGANIZATION'
                    LET user = DOCUMENT(edge._from)
                    FILTER @active == false OR user.isActive == true
                    RETURN user
                """

            # Execute query with organization parameter
            results = await self.http_client.execute_aql(
                query,
                bind_vars={"org_id": org_id, "active": active}
            )

            self.logger.info(f"✅ Successfully fetched {len(results)} users")
            return results if results else []

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch users: {str(e)}")
            return []

    async def get_app_user_by_email(
        self,
        email: str,
        connector_id: str,
        transaction: Optional[str] = None
    ) -> Optional[AppUser]:
        """
        Get app user by email and app name, including sourceUserId from edge.

        Args:
            email: User email address
            connector_id: Connector ID
            transaction: Optional transaction ID

        Returns:
            AppUser object if found, None otherwise
        """
        try:
            self.logger.info(
                f"🚀 Retrieving user for email {email} and app {connector_id}"
            )

            query = f"""
                // First find the app
                LET app = FIRST(
                    FOR a IN {CollectionNames.APPS.value}
                        FILTER a._key == @connector_id
                        RETURN a
                )

                // Then find the user by email
                LET user = FIRST(
                    FOR u IN {CollectionNames.USERS.value}
                        FILTER LOWER(u.email) == LOWER(@email)
                        RETURN u
                )

                // Find the edge connecting user to app
                LET edge = FIRST(
                    FOR e IN {CollectionNames.USER_APP_RELATION.value}
                        FILTER e._from == user._id
                        FILTER e._to == app._id
                        RETURN e
                )

                // Return user merged with sourceUserId if edge exists
                RETURN edge != null ? MERGE(user, {{
                    sourceUserId: edge.sourceUserId
                }}) : null
            """

            results = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "email": email,
                    "connector_id": connector_id
                },
                txn_id=transaction
            )

            if results and results[0]:
                self.logger.info(f"✅ Successfully retrieved user for email {email} and app {connector_id}")
                user_data = self._translate_node_from_arango(results[0])
                return AppUser.from_arango_user(user_data)
            else:
                self.logger.warning(f"⚠️ No user found for email {email} and app {connector_id}")
                return None

        except Exception as e:
            self.logger.error(f"❌ Failed to retrieve user for email {email} and app {connector_id}: {str(e)}")
            return None

    async def get_app_users(
        self,
        org_id: str,
        connector_id: str
    ) -> List[Dict]:
        """
        Fetch all users from the database who belong to the organization
        and are connected to the specified app via userAppRelation edge.

        Args:
            org_id (str): Organization ID
            connector_id (str): Connector ID

        Returns:
            List[Dict]: List of user documents with their details and sourceUserId
        """
        try:
            self.logger.info(f"🚀 Fetching users connected to {connector_id} app")

            query = f"""
                // First find the app
                LET app = FIRST(
                    FOR a IN {CollectionNames.APPS.value}
                        FILTER a._key == @connector_id
                        RETURN a
                )

                // Then find users connected via userAppRelation
                FOR edge IN {CollectionNames.USER_APP_RELATION.value}
                    FILTER edge._to == app._id
                    LET user = DOCUMENT(edge._from)
                    FILTER user != null

                    // Verify user belongs to the organization
                    LET belongs_to_org = FIRST(
                        FOR org_edge IN {CollectionNames.BELONGS_TO.value}
                            FILTER org_edge._from == user._id
                            FILTER org_edge._to == CONCAT('organizations/', @org_id)
                            FILTER org_edge.entityType == 'ORGANIZATION'
                            RETURN true
                    )
                    FILTER belongs_to_org == true

                    RETURN MERGE(user, {{
                        sourceUserId: edge.sourceUserId,
                        appName: UPPER(app.type),
                        connectorId: app._key
                    }})
            """

            results = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "connector_id": connector_id,
                    "org_id": org_id
                }
            )

            self.logger.info(f"✅ Successfully fetched {len(results)} users for {connector_id}")
            return results if results else []

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch users for {connector_id}: {str(e)}")
            return []

    async def get_user_group_by_external_id(
        self,
        connector_id: str,
        external_id: str,
        transaction: Optional[str] = None
    ) -> Optional[AppUserGroup]:
        """
        Get user group by external ID.

        Generic implementation using query.
        """
        query = f"""
        FOR group IN {CollectionNames.GROUPS.value}
            FILTER group.externalGroupId == @external_id
            AND group.connectorId == @connector_id
            LIMIT 1
            RETURN group
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "external_id": external_id,
                    "connector_id": connector_id
                },
                txn_id=transaction
            )

            if results:
                # Convert to AppUserGroup entity
                group_data = self._translate_node_from_arango(results[0])
                return AppUserGroup.from_arango_base_user_group(group_data)

            return None

        except Exception as e:
            self.logger.error(f"❌ Get user group by external ID failed: {str(e)}")
            return None

    async def get_user_groups(
        self,
        connector_id: str,
        org_id: str,
        transaction: Optional[str] = None
    ) -> List[AppUserGroup]:
        """
        Get all user groups for a specific connector and organization.
        Args:
            connector_id: Connector ID
            org_id: Organization ID
            transaction: Optional transaction ID
        Returns:
            List[AppUserGroup]: List of user group entities
        """
        try:
            self.logger.info(
                f"🚀 Retrieving user groups for connector {connector_id} and org {org_id}"
            )

            query = f"""
            FOR group IN {CollectionNames.GROUPS.value}
                FILTER group.connectorId == @connector_id
                    AND group.orgId == @org_id
                RETURN group
            """

            bind_vars = {
                "connector_id": connector_id,
                "org_id": org_id
            }

            groupData = await self.http_client.execute_aql(query, bind_vars, txn_id=transaction)
            groups = [AppUserGroup.from_arango_base_user_group(self._translate_node_from_arango(group_data_item)) for group_data_item in groupData]

            self.logger.info(
                f"✅ Successfully retrieved {len(groups)} user groups for connector {connector_id}"
            )
            return groups

        except Exception as e:
            self.logger.error(
                f"❌ Failed to retrieve user groups for connector {connector_id}: {str(e)}"
            )
            return []

    async def batch_upsert_people(
        self,
        people: List[Person],
        transaction: Optional[str] = None
    ) -> None:
        """Upsert people to PEOPLE collection."""
        try:
            if not people:
                return

            docs = [person.to_arango_person() for person in people]

            await self.batch_upsert_nodes(
                nodes=docs,
                collection=CollectionNames.PEOPLE.value,
                transaction=transaction
            )

            self.logger.debug(f"Upserted {len(people)} people records")

        except Exception as e:
            self.logger.error(f"Error upserting people: {e}")
            raise

    async def get_app_role_by_external_id(
        self,
        connector_id: str,
        external_id: str,
        transaction: Optional[str] = None
    ) -> Optional[AppRole]:
        """
        Get app role by external ID.

        Generic implementation using query.
        """
        query = f"""
        FOR role IN {CollectionNames.ROLES.value}
            FILTER role.externalRoleId == @external_id
            AND role.connectorId == @connector_id
            LIMIT 1
            RETURN role
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "external_id": external_id,
                    "connector_id": connector_id
                },
                txn_id=transaction
            )

            if results:
                # Convert to AppRole entity
                role_data = self._translate_node_from_arango(results[0])
                return AppRole.from_arango_base_role(role_data)

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
        seen = set()
        duplicates = {x for x in record_ids if x in seen or seen.add(x)}
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

        Args:
            from_record_id: Source record ID
            to_record_id: Target record ID
            relation_type: Type of relation (e.g., "BLOCKS", "CLONES", "LINKED_TO", etc.)
            transaction: Optional transaction ID
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

    async def get_all_documents(
        self,
        collection: str,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get all documents from a collection.

        Args:
            collection: Collection name
            transaction: Optional transaction ID

        Returns:
            List[Dict]: List of all documents in the collection
        """
        try:
            self.logger.info(f"🚀 Getting all documents from collection: {collection}")
            query = """
            FOR doc IN @@collection
                RETURN doc
            """
            results = await self.http_client.execute_aql(
                query,
                bind_vars={"@collection": collection},
                txn_id=transaction
            )
            return results if results else []
        except Exception as e:
            self.logger.error(f"❌ Failed to get all documents from collection: {collection}: {str(e)}")
            return []


    async def get_org_apps(
        self,
        org_id: str
    ) -> List[Dict]:
        """
        Get organization apps.
        """
        try:
            query = f"""
            FOR app IN OUTBOUND
                '{CollectionNames.ORGS.value}/{org_id}'
                {CollectionNames.ORG_APP_RELATION.value}
            FILTER app.isActive == true
            RETURN app
            """

            results = await self.http_client.execute_aql(query)
            return results if results else []
        except Exception as e:
            self.logger.error(f"❌ Get org apps failed: {str(e)}")
            return []

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
        node_id: str,
        node_collection: str,
        transaction: Optional[str] = None
    ) -> Optional[User]:
        """
        Get first user with permission to node.

        Args:
            node_id: The node ID
            node_collection: The node collection name
            transaction: Optional transaction ID

        Returns:
            Optional[User]: User with permission to the node, or None if not found
        """
        try:
            # Construct ArangoDB-specific _to value
            node_key = f"{node_collection}/{node_id}"

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
            if results:
                user_data = self._translate_node_from_arango(results[0])
                return User.from_arango_user(user_data)
            return None

        except Exception as e:
            self.logger.error(f"❌ Get first user with permission to node failed: {str(e)}")
            return None

    async def get_users_with_permission_to_node(
        self,
        node_id: str,
        node_collection: str,
        transaction: Optional[str] = None
    ) -> List[User]:
        """
        Get all users with permission to node.

        Args:
            node_id: The node ID
            node_collection: The node collection name
            transaction: Optional transaction ID

        Returns:
            List[User]: List of users with permission to the node
        """
        try:
            # Construct ArangoDB-specific _to value
            node_key = f"{node_collection}/{node_id}"

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
            return [User.from_arango_user(self._translate_node_from_arango(result)) for result in results]

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
            query = f"""
            FOR edge IN {CollectionNames.PERMISSION.value}
                FILTER edge._to == CONCAT('{CollectionNames.RECORDS.value}/', @record_id)
                FILTER edge.role == 'OWNER'
                FILTER edge.type == 'USER'
                LET user_key = SPLIT(edge._from, '/')[1]
                LET user = DOCUMENT('{CollectionNames.USERS.value}', user_key)
                LIMIT 1
                RETURN user.email
            """
            bind_vars = {
                "record_id": record_id
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
    ) -> List[Dict]:
        """
        Get parent file external IDs for a given file.

        Args:
            file_key: File key
            transaction: Optional transaction ID

        Returns:
            List[Dict]: List of parent files
        """
        try:
            if not file_key:
                raise ValueError("File ID is required")

            self.logger.info(f"🚀 Getting parents for record {file_key}")

            query = f"""
            LET relations = (
                FOR rel IN {CollectionNames.RECORD_RELATIONS.value}
                    FILTER rel._to == @record_id
                    RETURN rel._from
            )
            LET parent_keys = (
                FOR rel IN relations
                    LET key = PARSE_IDENTIFIER(rel).key
                    RETURN {{
                        original_id: rel,
                        parsed_key: key
                    }}
            )
            LET parent_files = (
                FOR parent IN parent_keys
                    FOR record IN {CollectionNames.RECORDS.value}
                        FILTER record._key == parent.parsed_key
                        RETURN {{
                            key: record._key,
                            externalRecordId: record.externalRecordId
                        }}
            )
            RETURN {{
                input_file_key: @file_key,
                found_relations: relations,
                parsed_parent_keys: parent_keys,
                found_parent_files: parent_files
            }}
            """

            bind_vars = {
                "file_key": file_key,
                "record_id": CollectionNames.RECORDS.value + "/" + file_key,
            }

            results = await self.http_client.execute_aql(query, bind_vars, txn_id=transaction)

            if not results or not results[0]["found_relations"]:
                self.logger.warning(f"⚠️ No relations found for record {file_key}")
            if not results or not results[0]["parsed_parent_keys"]:
                self.logger.warning(f"⚠️ No parent keys parsed for record {file_key}")
            if not results or not results[0]["found_parent_files"]:
                self.logger.warning(f"⚠️ No parent files found for record {file_key}")

            # Return just the external file IDs if everything worked
            return (
                [
                    record["externalRecordId"]
                    for record in results[0]["found_parent_files"]
                ]
                if results
                else []
            )

        except ValueError as ve:
            self.logger.error(f"❌ Validation error: {str(ve)}")
            return []
        except Exception as e:
            self.logger.error(
                f"❌ Error getting parents for record {file_key}: {str(e)}"
            )
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
                        "syncPointKey": sync_point_key,
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
                        "syncPointKey": sync_point_key,
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
        key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> None:
        """
        Remove sync point by syncPointKey field.
        """
        try:
            query = """
            FOR doc IN @@collection
                FILTER doc.syncPointKey == @key
                REMOVE doc IN @@collection
            """
            bind_vars = {
                "@collection": collection,
                "key": key
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
            if not users:
                return

            # Get org_id
            orgs = await self.get_all_orgs()
            if not orgs:
                raise Exception("No organizations found in the database")
            org_id = orgs[0]["_key"]
            connector_id = users[0].connector_id

            app = await self.get_document(connector_id, CollectionNames.APPS.value)
            if not app:
                raise Exception(f"Failed to get/create app: {connector_id}")

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
            if results:
                return results[0]

            query = """
            FOR doc IN {CollectionNames.PEOPLE.value}
                FILTER doc.email == @email
                LIMIT 1
                RETURN doc._key
            """

            results = await self.http_client.execute_aql(
                query,
                bind_vars={"email": email},
                txn_id=transaction
            )
            if results:
                return results[0]

            return None
        except Exception as e:
            self.logger.error(f"❌ Get entity ID by email failed: {str(e)}")
            return None

    async def bulk_get_entity_ids_by_email(
        self,
        emails: List[str],
        transaction: Optional[str] = None
    ) -> Dict[str, Tuple[str, str, str]]:
        """
        Bulk get entity IDs for multiple emails across users, groups, and people collections.

        Args:
            emails (List[str]): List of email addresses to look up
            transaction: Optional transaction ID

        Returns:
            Dict[email, (entity_id, collection_name, permission_type)]

            Example:
            {
                "user@example.com": ("123abc", "users", "USER"),
                "group@example.com": ("456def", "groups", "GROUP"),
                "external@example.com": ("789ghi", "people", "USER")
            }
        """
        if not emails:
            return {}

        try:
            self.logger.info(f"🚀 Bulk getting Entity Keys for {len(emails)} emails")

            result_map = {}

            # Deduplicate emails to avoid redundant queries
            unique_emails = list(set(emails))

            # QUERY 1: Check users collection
            user_query = f"""
            FOR doc IN {CollectionNames.USERS.value}
                FILTER doc.email IN @emails
                RETURN {{email: doc.email, id: doc._key}}
            """
            try:
                users = await self.http_client.execute_aql(
                    user_query,
                    bind_vars={"emails": unique_emails},
                    txn_id=transaction
                )
                for user in users:
                    result_map[user["email"]] = (
                        user["id"],
                        CollectionNames.USERS.value,
                        "USER"
                    )
                self.logger.info(f"✅ Found {len(users)} users")
            except Exception as e:
                self.logger.error(f"❌ Error querying users: {str(e)}")

            # QUERY 2: Check groups collection (only for remaining emails)
            remaining_emails = [e for e in unique_emails if e not in result_map]
            if remaining_emails:
                group_query = f"""
                FOR doc IN {CollectionNames.GROUPS.value}
                    FILTER doc.email IN @emails
                    RETURN {{email: doc.email, id: doc._key}}
                """
                try:
                    groups = await self.http_client.execute_aql(
                        group_query,
                        bind_vars={"emails": remaining_emails},
                        txn_id=transaction
                    )
                    for group in groups:
                        result_map[group["email"]] = (
                            group["id"],
                            CollectionNames.GROUPS.value,
                            "GROUP"
                        )
                    self.logger.info(f"✅ Found {len(groups)} groups")
                except Exception as e:
                    self.logger.error(f"❌ Error querying groups: {str(e)}")

            # QUERY 3: Check people collection (only for remaining emails)
            remaining_emails = [e for e in unique_emails if e not in result_map]
            if remaining_emails:
                people_query = f"""
                FOR doc IN {CollectionNames.PEOPLE.value}
                    FILTER doc.email IN @emails
                    RETURN {{email: doc.email, id: doc._key}}
                """
                try:
                    people = await self.http_client.execute_aql(
                        people_query,
                        bind_vars={"emails": remaining_emails},
                        txn_id=transaction
                    )
                    for person in people:
                        result_map[person["email"]] = (
                            person["id"],
                            CollectionNames.PEOPLE.value,
                            "USER"
                        )
                    self.logger.info(f"✅ Found {len(people)} people")
                except Exception as e:
                    self.logger.error(f"❌ Error querying people: {str(e)}")

            self.logger.info(
                f"✅ Bulk lookup complete: found {len(result_map)}/{len(unique_emails)} entities"
            )

            return result_map

        except Exception as e:
            self.logger.error(f"❌ Failed to bulk get entity IDs: {str(e)}")
            return {}

    async def store_permission(
        self,
        file_key: str,
        entity_key: str,
        permission_data: Dict,
        transaction: Optional[str] = None,
    ) -> bool:
        """Store or update permission relationship with change detection."""
        try:
            self.logger.info(
                f"🚀 Storing permission for file {file_key} and entity {entity_key}"
            )

            if not entity_key:
                self.logger.warning("⚠️ Cannot store permission - missing entity_key")
                return False

            timestamp = get_epoch_timestamp_in_ms()

            # Determine the correct collection for the _from field (User/Group/Org)
            entityType = permission_data.get("type", "user").lower()
            if entityType == "domain":
                from_collection = CollectionNames.ORGS.value
            else:
                from_collection = f"{entityType}s"

            existing_permissions = await self.get_file_permissions(file_key, transaction)
            if existing_permissions:
                # With reversed direction: User/Group/Org → Record, so check _from
                existing_perm = next((p for p in existing_permissions if p.get("_from") == f"{from_collection}/{entity_key}"), None)
                if existing_perm:
                    edge_key = existing_perm.get("_key")
                else:
                    edge_key = str(uuid.uuid4())
            else:
                edge_key = str(uuid.uuid4())

            self.logger.info(f"Permission data is {permission_data}")

            # Create edge document with proper formatting
            # Direction: User/Group/Org → Record (reversed from old direction)
            edge = {
                "_key": edge_key,
                "_from": f"{from_collection}/{entity_key}",
                "_to": f"{CollectionNames.RECORDS.value}/{file_key}",
                "type": permission_data.get("type").upper(),
                "role": permission_data.get("role", "READER").upper(),
                "externalPermissionId": permission_data.get("id"),
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
                "lastUpdatedTimestampAtSource": timestamp,
            }

            # Log the edge document for debugging
            self.logger.debug(f"Creating edge document: {edge}")

            # Check if permission edge exists using AQL (works with transactions)
            try:
                # Use AQL query to get existing edge instead of direct collection access
                get_edge_query = f"""
                    FOR edge IN {CollectionNames.PERMISSION.value}
                        FILTER edge._key == @edge_key
                        RETURN edge
                """
                existing_edge_results = await self.http_client.execute_aql(
                    get_edge_query,
                    bind_vars={"edge_key": edge_key},
                    txn_id=transaction
                )
                existing_edge = existing_edge_results[0] if existing_edge_results else None

                if not existing_edge:
                    # New permission - use batch_upsert_nodes which handles transactions properly
                    self.logger.info(f"✅ Creating new permission edge: {edge_key}")
                    await self.batch_upsert_nodes(
                        [edge],
                        collection=CollectionNames.PERMISSION.value,
                        transaction=transaction
                    )
                    self.logger.info(f"✅ Created new permission edge: {edge_key}")
                elif self._permission_needs_update(existing_edge, permission_data):
                    # Update existing permission
                    self.logger.info(f"✅ Updating permission edge: {edge_key}")
                    await self.batch_upsert_nodes(
                        [edge],
                        collection=CollectionNames.PERMISSION.value,
                        transaction=transaction
                    )
                    self.logger.info(f"✅ Updated permission edge: {edge_key}")
                else:
                    self.logger.info(
                        f"✅ No update needed for permission edge: {edge_key}"
                    )

                return True

            except Exception as e:
                self.logger.error(
                    f"❌ Failed to access permissions collection: {str(e)}"
                )
                if transaction:
                    raise
                return False

        except Exception as e:
            self.logger.error(f"❌ Failed to store permission: {str(e)}")
            if transaction:
                raise
            return False

    def _permission_needs_update(self, existing: Dict, new: Dict) -> bool:
        """Check if permission data needs to be updated"""
        self.logger.info("🚀 Checking if permission data needs to be updated")
        relevant_fields = ["role", "permissionDetails", "active"]

        for field in relevant_fields:
            if field in new:
                if field == "permissionDetails":
                    import json
                    if json.dumps(new[field], sort_keys=True) != json.dumps(
                        existing.get(field, {}), sort_keys=True
                    ):
                        self.logger.info(f"✅ Permission data needs to be updated. Field {field}")
                        return True
                elif new[field] != existing.get(field):
                    self.logger.info(f"✅ Permission data needs to be updated. Field {field}")
                    return True

        self.logger.info("✅ Permission data does not need to be updated")
        return False

    async def process_file_permissions(
        self,
        org_id: str,
        file_key: str,
        permissions_data: List[Dict],
        transaction: Optional[str] = None,
    ) -> bool:
        """
        Process file permissions by comparing new permissions with existing ones.
        Assumes all entities and files already exist in the database.
        """
        try:
            self.logger.info(f"🚀 Processing permissions for file {file_key}")
            timestamp = get_epoch_timestamp_in_ms()

            # Remove 'anyone' permission for this file if it exists
            query = f"""
            FOR a IN {CollectionNames.ANYONE.value}
                FILTER a.file_key == @file_key
                FILTER a.organization == @org_id
                REMOVE a IN {CollectionNames.ANYONE.value}
            """
            await self.http_client.execute_aql(
                query,
                bind_vars={"file_key": file_key, "org_id": org_id},
                txn_id=transaction
            )
            self.logger.info(f"🗑️ Removed 'anyone' permission for file {file_key}")

            existing_permissions = await self.get_file_permissions(
                file_key, transaction=transaction
            )
            self.logger.info(f"🚀 Existing permissions: {existing_permissions}")

            # Get all permission IDs from new permissions
            new_permission_ids = list({p.get("id") for p in permissions_data})
            self.logger.info(f"🚀 New permission IDs: {new_permission_ids}")

            # Find permissions that exist but are not in new permissions
            permissions_to_remove = [
                perm
                for perm in existing_permissions
                if perm.get("externalPermissionId") not in new_permission_ids
            ]

            # Remove permissions that no longer exist
            if permissions_to_remove:
                self.logger.info(
                    f"🗑️ Removing {len(permissions_to_remove)} obsolete permissions"
                )
                for perm in permissions_to_remove:
                    query = f"""
                    FOR p IN {CollectionNames.PERMISSION.value}
                        FILTER p._key == @perm_key
                        REMOVE p IN {CollectionNames.PERMISSION.value}
                    """
                    await self.http_client.execute_aql(
                        query,
                        bind_vars={"perm_key": perm["_key"]},
                        txn_id=transaction
                    )

            # Process permissions by type
            for perm_type in ["user", "group", "domain", "anyone"]:
                # Filter new permissions for current type
                new_perms = [
                    p
                    for p in permissions_data
                    if p.get("type", "").lower() == perm_type
                ]
                # Filter existing permissions for current type
                existing_perms = [
                    p
                    for p in existing_permissions
                    if p.get("type").lower() == perm_type
                ]

                # Compare and update permissions
                if perm_type == "user" or perm_type == "group" or perm_type == "domain":
                    for new_perm in new_perms:
                        perm_id = new_perm.get("id")
                        if existing_perms:
                            existing_perm = next(
                                (
                                    p
                                    for p in existing_perms
                                    if p.get("externalPermissionId") == perm_id
                                ),
                                None,
                            )
                        else:
                            existing_perm = None

                        if existing_perm:
                            entity_key = existing_perm.get("_from")
                            entity_key = entity_key.split("/")[1]
                            # Update existing permission
                            await self.store_permission(
                                file_key,
                                entity_key,
                                new_perm,
                                transaction,
                            )
                        else:
                            # Get entity key from email for user/group
                            # Create new permission
                            if perm_type == "user" or perm_type == "group":
                                entity_key = await self.get_entity_id_by_email(
                                    new_perm.get("emailAddress"), transaction
                                )
                                if not entity_key:
                                    self.logger.warning(
                                        f"⚠️ Skipping permission for non-existent user or group: {new_perm.get('emailAddress')}"
                                    )
                                    continue
                            elif perm_type == "domain":
                                entity_key = org_id
                                if not entity_key:
                                    self.logger.warning(
                                        f"⚠️ Skipping permission for non-existent domain: {entity_key}"
                                    )
                                    continue
                            else:
                                entity_key = None
                                # Skip if entity doesn't exist
                                if not entity_key:
                                    self.logger.warning(
                                        f"⚠️ Skipping permission for non-existent entity: {entity_key}"
                                    )
                                    continue
                            if entity_key != "anyone" and entity_key:
                                self.logger.info(
                                    f"🚀 Storing permission for file {file_key} and entity {entity_key}: {new_perm}"
                                )
                                await self.store_permission(
                                    file_key, entity_key, new_perm, transaction
                                )

                if perm_type == "anyone":
                    # For anyone type, add permission directly to anyone collection
                    for new_perm in new_perms:
                        permission_data = {
                            "type": "anyone",
                            "file_key": file_key,
                            "organization": org_id,
                            "role": new_perm.get("role", "READER"),
                            "externalPermissionId": new_perm.get("id"),
                            "lastUpdatedTimestampAtSource": timestamp,
                            "active": True,
                        }
                        # Store/update permission
                        await self.batch_upsert_nodes(
                            [permission_data],
                            collection=CollectionNames.ANYONE.value,
                            transaction=transaction
                        )

            self.logger.info(
                f"✅ Successfully processed all permissions for file {file_key}"
            )
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to process permissions: {str(e)}")
            if transaction:
                raise
            return False

    async def delete_records_and_relations(
        self,
        node_key: str,
        hard_delete: bool = False,
        transaction: Optional[str] = None,
    ) -> bool:
        """Delete a node and its edges from all edge collections (Records, Files)."""
        try:
            self.logger.info(
                f"🚀 Deleting node {node_key} from collection Records, Files (hard_delete={hard_delete})"
            )

            record = await self.http_client.get_document(
                CollectionNames.RECORDS.value,
                node_key,
                txn_id=transaction
            )
            if not record:
                self.logger.warning(
                    f"⚠️ Record {node_key} not found in Records collection"
                )
                return False

            # Define all edge collections used in the graph
            EDGE_COLLECTIONS = [
                CollectionNames.RECORD_RELATIONS.value,
                CollectionNames.BELONGS_TO.value,
                CollectionNames.BELONGS_TO_DEPARTMENT.value,
                CollectionNames.BELONGS_TO_CATEGORY.value,
                CollectionNames.BELONGS_TO_LANGUAGE.value,
                CollectionNames.BELONGS_TO_TOPIC.value,
                CollectionNames.IS_OF_TYPE.value,
            ]

            # Step 1: Remove edges from all edge collections
            for edge_collection in EDGE_COLLECTIONS:
                try:
                    edge_removal_query = """
                    LET record_id_full = CONCAT('records/', @node_key)
                    FOR edge IN @@edge_collection
                        FILTER edge._from == record_id_full OR edge._to == record_id_full
                        REMOVE edge IN @@edge_collection
                    """
                    bind_vars = {
                        "node_key": node_key,
                        "@edge_collection": edge_collection,
                    }
                    await self.http_client.execute_aql(edge_removal_query, bind_vars, txn_id=transaction)
                    self.logger.info(
                        f"✅ Edges from {edge_collection} deleted for node {node_key}"
                    )
                except Exception as e:
                    self.logger.warning(
                        f"⚠️ Could not delete edges from {edge_collection} for node {node_key}: {str(e)}"
                    )

            # Step 2: Delete node from `records`, `files`, and `mails` collections
            delete_query = f"""
            LET removed_record = (
                FOR doc IN {CollectionNames.RECORDS.value}
                    FILTER doc._key == @node_key
                    REMOVE doc IN {CollectionNames.RECORDS.value}
                    RETURN OLD
            )

            LET removed_file = (
                FOR doc IN {CollectionNames.FILES.value}
                    FILTER doc._key == @node_key
                    REMOVE doc IN {CollectionNames.FILES.value}
                    RETURN OLD
            )

            LET removed_mail = (
                FOR doc IN {CollectionNames.MAILS.value}
                    FILTER doc._key == @node_key
                    REMOVE doc IN {CollectionNames.MAILS.value}
                    RETURN OLD
            )

            RETURN {{
                record_removed: LENGTH(removed_record) > 0,
                file_removed: LENGTH(removed_file) > 0,
                mail_removed: LENGTH(removed_mail) > 0
            }}
            """
            bind_vars = {
                "node_key": node_key,
            }

            result = await self.http_client.execute_aql(delete_query, bind_vars, txn_id=transaction)

            self.logger.info(
                f"✅ Node {node_key} and its edges {'hard' if hard_delete else 'soft'} deleted: {result}"
            )
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to delete node {node_key}: {str(e)}")
            if transaction:
                raise
            return False

    async def delete_record(
        self,
        record_id: str,
        user_id: str,
        transaction: Optional[str] = None
    ) -> Dict:
        """
        Main entry point for record deletion - routes to connector-specific methods.

        Args:
            record_id: Record ID to delete
            user_id: User ID performing the deletion
            transaction: Optional transaction ID

        Returns:
            Dict: Result with success status and reason
        """
        try:
            self.logger.info(f"🚀 Starting record deletion for {record_id} by user {user_id}")

            # Get record to determine connector type
            record = await self.http_client.get_document(
                collection=CollectionNames.RECORDS.value,
                key=record_id,
                txn_id=transaction
            )
            if not record:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"Record not found: {record_id}"
                }

            connector_name = record.get("connectorName", "")
            origin = record.get("origin", "")

            # Route to connector-specific deletion method
            if origin == OriginTypes.UPLOAD.value or connector_name == Connectors.KNOWLEDGE_BASE.value:
                return await self.delete_knowledge_base_record(record_id, user_id, record, transaction)
            elif connector_name == Connectors.GOOGLE_DRIVE.value:
                return await self.delete_google_drive_record(record_id, user_id, record, transaction)
            elif connector_name == Connectors.GOOGLE_MAIL.value:
                return await self.delete_gmail_record(record_id, user_id, record, transaction)
            elif connector_name == Connectors.OUTLOOK.value:
                return await self.delete_outlook_record(record_id, user_id, record, transaction)
            else:
                return {
                    "success": False,
                    "code": 400,
                    "reason": f"Unsupported connector: {connector_name}"
                }

        except Exception as e:
            self.logger.error(f"❌ Failed to delete record {record_id}: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": f"Internal error: {str(e)}"
            }

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
            connector_id: Connector ID
            external_id: External record ID
            user_id: User ID performing the deletion
            transaction: Optional transaction ID
        """
        try:
            self.logger.info(f"🗂️ Deleting record {external_id} from {connector_id}")

            # Get record
            record = await self.get_record_by_external_id(
                connector_id,
                external_id,
                transaction=transaction
            )
            if not record:
                self.logger.warning(f"⚠️ Record {external_id} not found in {connector_id}")
                return

            # Delete record using the record's internal ID and user_id
            deletion_result = await self.delete_record(record.id, user_id, transaction=transaction)

            # Check if deletion was successful
            if deletion_result.get("success"):
                self.logger.info(f"✅ Record {external_id} deleted from {connector_id}")
            else:
                error_reason = deletion_result.get("reason", "Unknown error")
                self.logger.error(f"❌ Failed to delete record {external_id}: {error_reason}")
                raise Exception(f"Deletion failed: {error_reason}")

        except Exception as e:
            self.logger.error(f"❌ Failed to delete record {external_id} from {connector_id}: {str(e)}")
            raise

    async def remove_user_access_to_record(
        self,
        connector_id: str,
        external_id: str,
        user_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """
        Remove a user's access to a record (for inbox-based deletions).
        This removes the user's permissions and belongsTo edges without deleting the record itself.

        Args:
            connector_id: Connector ID
            external_id: External record ID
            user_id: User ID to remove access from
            transaction: Optional transaction ID
        """
        try:
            self.logger.info(f"🔄 Removing user access: {external_id} from {connector_id} for user {user_id}")

            # Get record
            record = await self.get_record_by_external_id(
                connector_id,
                external_id,
                transaction=transaction
            )
            if not record:
                self.logger.warning(f"⚠️ Record {external_id} not found in {connector_id}")
                return

            # Remove user's permission edges
            user_removal_query = """
            FOR perm IN permission
                FILTER perm._from == @user_to
                FILTER perm._to == @record_from
                REMOVE perm IN permission
                RETURN OLD
            """

            result = await self.http_client.execute_aql(
                query=user_removal_query,
                bind_vars={
                    "record_from": f"records/{record.id}",
                    "user_to": f"users/{user_id}"
                },
                txn_id=transaction
            )

            removed_permissions = result if result else []

            if removed_permissions:
                self.logger.info(f"✅ Removed {len(removed_permissions)} permission(s) for user {user_id} on record {record.id}")
            else:
                self.logger.info(f"ℹ️ No permissions found for user {user_id} on record {record.id}")

        except Exception as e:
            self.logger.error(f"❌ Failed to remove user access {external_id} from {connector_id}: {str(e)}")
            raise

    # ==================== Connector-Specific Delete Methods ====================

    async def delete_knowledge_base_record(
        self,
        record_id: str,
        user_id: str,
        record: Dict,
        transaction: Optional[str] = None
    ) -> Dict:
        """Delete a Knowledge Base record - handles uploads and KB-specific logic."""
        try:
            self.logger.info(f"🗂️ Deleting Knowledge Base record {record_id}")

            # Get user
            user = await self.get_user_by_user_id(user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found: {user_id}"
                }

            user_key = user.get('_key')

            # Find KB context for this record
            kb_context = await self._get_kb_context_for_record(record_id, transaction)
            if not kb_context:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"Knowledge base context not found for record {record_id}"
                }

            # Check KB permissions
            user_role = await self.get_user_kb_permission(kb_context["kb_id"], user_key, transaction)
            if user_role not in self.connector_delete_permissions[Connectors.KNOWLEDGE_BASE.value]["allowed_roles"]:
                return {
                    "success": False,
                    "code": 403,
                    "reason": f"Insufficient permissions. User role: {user_role}"
                }

            # Execute KB-specific deletion
            return await self._execute_kb_record_deletion(record_id, record, kb_context, transaction)

        except Exception as e:
            self.logger.error(f"❌ Failed to delete KB record: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": f"KB record deletion failed: {str(e)}"
            }

    async def delete_google_drive_record(
        self,
        record_id: str,
        user_id: str,
        record: Dict,
        transaction: Optional[str] = None
    ) -> Dict:
        """Delete a Google Drive record - handles Drive-specific permissions and logic."""
        try:
            self.logger.info(f"🔌 Deleting Google Drive record {record_id}")

            # Get user
            user = await self.get_user_by_user_id(user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found: {user_id}"
                }

            user_key = user.get('_key')

            # Check Drive-specific permissions
            user_role = await self._check_drive_permissions(record_id, user_key, transaction)
            if not user_role or user_role not in self.connector_delete_permissions[Connectors.GOOGLE_DRIVE.value]["allowed_roles"]:
                return {
                    "success": False,
                    "code": 403,
                    "reason": f"Insufficient Drive permissions. Role: {user_role}"
                }

            # Execute Drive-specific deletion
            return await self._execute_drive_record_deletion(record_id, record, user_role, transaction)

        except Exception as e:
            self.logger.error(f"❌ Failed to delete Drive record: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": f"Drive record deletion failed: {str(e)}"
            }

    async def delete_gmail_record(
        self,
        record_id: str,
        user_id: str,
        record: Dict,
        transaction: Optional[str] = None
    ) -> Dict:
        """Delete a Gmail record - handles Gmail-specific permissions and logic."""
        try:
            self.logger.info(f"📧 Deleting Gmail record {record_id}")

            # Get user
            user = await self.get_user_by_user_id(user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found: {user_id}"
                }

            user_key = user.get('_key')

            # Check Gmail-specific permissions
            user_role = await self._check_gmail_permissions(record_id, user_key, transaction)
            if not user_role or user_role not in self.connector_delete_permissions[Connectors.GOOGLE_MAIL.value]["allowed_roles"]:
                return {
                    "success": False,
                    "code": 403,
                    "reason": f"Insufficient Gmail permissions. Role: {user_role}"
                }

            # Execute Gmail-specific deletion
            return await self._execute_gmail_record_deletion(record_id, record, user_role, transaction)

        except Exception as e:
            self.logger.error(f"❌ Failed to delete Gmail record: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": f"Gmail record deletion failed: {str(e)}"
            }

    async def delete_outlook_record(
        self,
        record_id: str,
        user_id: str,
        record: Dict,
        transaction: Optional[str] = None
    ) -> Dict:
        """Delete an Outlook record - handles email and its attachments."""
        try:
            self.logger.info(f"📧 Deleting Outlook record {record_id}")

            # Get user
            user = await self.get_user_by_user_id(user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found: {user_id}"
                }

            user_key = user.get('_key')

            # Check if user has OWNER permission
            user_role = await self._check_record_permission(record_id, user_key, transaction)
            if user_role != "OWNER":
                return {
                    "success": False,
                    "code": 403,
                    "reason": f"Only mailbox owner can delete emails. Role: {user_role}"
                }

            # Execute deletion
            return await self._execute_outlook_record_deletion(record_id, record, transaction)

        except Exception as e:
            self.logger.error(f"❌ Failed to delete Outlook record: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": f"Outlook record deletion failed: {str(e)}"
            }

    async def get_key_by_external_file_id(
        self,
        external_file_id: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """
        Get internal file key using the external file ID.

        Args:
            external_file_id (str): External file ID to look up
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Optional[str]: Internal file key if found, None otherwise
        """
        try:
            self.logger.info(
                f"🚀 Retrieving internal key for external file ID {external_file_id}"
            )

            query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record.externalRecordId == @external_file_id
                RETURN record._key
            """

            results = await self.http_client.execute_aql(
                query,
                bind_vars={"external_file_id": external_file_id},
                txn_id=transaction
            )

            if results:
                self.logger.info(
                    f"✅ Successfully retrieved internal key for external file ID {external_file_id}"
                )
                return results[0]
            else:
                self.logger.warning(
                    f"⚠️ No internal key found for external file ID {external_file_id}"
                )
                return None

        except Exception as e:
            self.logger.error(
                f"❌ Failed to retrieve internal key for external file ID {external_file_id}: {str(e)}"
            )
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
        state: str,
        service_type: str = Connectors.GOOGLE_DRIVE.value
    ) -> Optional[Dict]:
        """
        Update user's sync state in USER_APP_RELATION collection for specific service.

        Args:
            user_email (str): Email of the user
            state (str): Sync state (NOT_STARTED, RUNNING, PAUSED, COMPLETED)
            service_type (str): Type of service (defaults to "DRIVE")

        Returns:
            Optional[Dict]: Updated relation document if successful, None otherwise
        """
        try:
            self.logger.info(
                f"🚀 Updating {service_type} sync state for user {user_email} to {state}"
            )

            user_key = await self.get_entity_id_by_email(user_email)
            if not user_key:
                self.logger.warning(f"⚠️ User not found for email {user_email}")
                return None

            # Get user key and app key based on service type and update the sync state
            query = f"""
            LET app = FIRST(FOR a IN {CollectionNames.APPS.value}
                          FILTER LOWER(a.name) == LOWER(@service_type)
                          RETURN {{
                              _key: a._key,
                              name: a.name
                          }})

            LET edge = FIRST(
                FOR rel in {CollectionNames.USER_APP_RELATION.value}
                    FILTER rel._from == CONCAT('users/', @user_key)
                    FILTER rel._to == CONCAT('apps/', app._key)
                    UPDATE rel WITH {{ syncState: @state, lastSyncUpdate: @lastSyncUpdate }} IN {CollectionNames.USER_APP_RELATION.value}
                    RETURN NEW
            )

            RETURN edge
            """

            results = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "user_key": user_key,
                    "service_type": service_type,
                    "state": state,
                    "lastSyncUpdate": get_epoch_timestamp_in_ms(),
                }
            )

            result = results[0] if results else None
            if result:
                self.logger.info(
                    f"✅ Successfully updated {service_type} sync state for user {user_email} to {state}"
                )
                return result

            self.logger.warning(
                f"⚠️ UPDATE:No user-app relation found for email {user_email} and service {service_type}"
            )
            return None

        except Exception as e:
            self.logger.error(
                f"❌ Failed to update user {service_type} sync state: {str(e)}"
            )
            return None

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
        return "NOT_STARTED"

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
        expiration: Optional[str] = None,
    ) -> Optional[Dict]:
        """Store page token with user channel information."""
        try:
            self.logger.info(
                """
            🚀 Storing page token:

            - Channel: %s
            - Resource: %s
            - User Email: %s
            - Token: %s
            - Expiration: %s
            """,
                channel_id,
                resource_id,
                user_email,
                token,
                expiration,
            )

            token_doc = {
                "channelId": channel_id,
                "resourceId": resource_id,
                "userEmail": user_email,
                "token": token,
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "expiration": expiration,
            }

            # Upsert to handle updates to existing channel tokens
            query = f"""
            UPSERT {{ userEmail: @userEmail }}
            INSERT @token_doc
            UPDATE @token_doc
            IN {CollectionNames.PAGE_TOKENS.value}
            RETURN NEW
            """

            results = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "userEmail": user_email,
                    "token_doc": token_doc,
                }
            )

            self.logger.info("✅ Page token stored successfully")
            return results[0] if results else None

        except Exception as e:
            self.logger.error(f"❌ Error storing page token: {str(e)}")
            return None

    async def get_page_token_db(
        self,
        channel_id: str = None,
        resource_id: str = None,
        user_email: str = None
    ) -> Optional[Dict]:
        """Get page token for specific channel."""
        try:
            self.logger.info(
                """
            🔍 Getting page token for:
            - Channel: %s
            - Resource: %s
            - User Email: %s
            """,
                channel_id,
                resource_id,
                user_email,
            )

            filters = []
            bind_vars = {}

            if channel_id is not None:
                filters.append("token.channelId == @channel_id")
                bind_vars["channel_id"] = channel_id
            if resource_id is not None:
                filters.append("token.resourceId == @resource_id")
                bind_vars["resource_id"] = resource_id
            if user_email is not None:
                filters.append("token.userEmail == @user_email")
                bind_vars["user_email"] = user_email

            if not filters:
                self.logger.warning("⚠️ No filter params provided for page token query")
                return None

            filter_clause = " OR ".join(filters)

            query = f"""
            FOR token IN {CollectionNames.PAGE_TOKENS.value}
            FILTER {filter_clause}
            SORT token.createdAtTimestamp DESC
            LIMIT 1
            RETURN token
            """

            results = await self.http_client.execute_aql(query, bind_vars)

            if results:
                self.logger.info("✅ Found token for channel")
                return results[0]

            self.logger.warning("⚠️ No token found for channel")
            return None

        except Exception as e:
            self.logger.error(f"❌ Error getting page token: {str(e)}")
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
        connector_id: str
    ) -> List[Dict]:
        """
        Get failed records along with active users who have permissions.

        Generic method that can be used for any connector.
        """
        query = """
        FOR doc IN records
            FILTER doc.orgId == @org_id
            AND doc.indexingStatus == "FAILED"
            AND doc.connectorId == @connector_id

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
                    "connector_id": connector_id
                }
            )
            return results if results else []
        except Exception as e:
            self.logger.error(f"❌ Get failed records with active users failed: {str(e)}")
            return []

    async def get_failed_records_by_org(
        self,
        org_id: str,
        connector_id: str
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
                "connectorId": connector_id
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
        from_id: str,
        from_collection: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> int:
        """
        Delete all edges from the given node if those edges are pointing to nodes in the groups or roles collection.

        Args:
            from_id: The source node ID
            from_collection: The source node collection name
            collection: The edge collection name to search in
            transaction: Optional transaction ID

        Returns:
            int: Number of edges deleted
        """
        try:
            # Construct ArangoDB-specific _from value
            from_key = f"{from_collection}/{from_id}"

            self.logger.info(f"🚀 Deleting edges from {from_key} to groups/roles collection in {collection}")

            query = """
            FOR edge IN @@collection
                FILTER edge._from == @from_key
                FILTER IS_SAME_COLLECTION("groups", edge._to) OR IS_SAME_COLLECTION("roles", edge._to)
                REMOVE edge IN @@collection
                RETURN OLD
            """

            results = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "from_key": from_key,
                    "@collection": collection
                },
                txn_id=transaction
            )

            count = len(results) if results else 0

            if count > 0:
                self.logger.info(f"✅ Successfully deleted {count} edges from {from_key} to groups")
            else:
                self.logger.warning(f"⚠️ No edges found from {from_key} to groups in collection: {collection}")

            return count

        except Exception as e:
            self.logger.error(f"❌ Failed to delete edges from {from_key} to groups in {collection}: {str(e)}")
            return 0

    async def delete_edges_between_collections(
        self,
        from_id: str,
        from_collection: str,
        edge_collection: str,
        to_collection: str,
        transaction: Optional[str] = None
    ) -> int:
        """
        Delete all edges from a specific node to any nodes in the target collection.

        Args:
            from_id: The source node ID
            from_collection: The source node collection name
            edge_collection: The edge collection name to search in
            to_collection: The target collection name (edges pointing to nodes in this collection will be deleted)
            transaction: Optional transaction ID

        Returns:
            int: Number of edges deleted
        """
        try:
            # Construct ArangoDB-specific _from value
            from_key = f"{from_collection}/{from_id}"

            self.logger.info(
                f"🚀 Deleting edges from {from_key} to {to_collection} collection in {edge_collection}"
            )

            query = """
            FOR edge IN @@edge_collection
                FILTER edge._from == @from_key
                FILTER IS_SAME_COLLECTION(@to_collection, edge._to)
                REMOVE edge IN @@edge_collection
                RETURN OLD
            """

            results = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "from_key": from_key,
                    "@edge_collection": edge_collection,
                    "to_collection": to_collection
                },
                txn_id=transaction
            )

            count = len(results) if results else 0

            if count > 0:
                self.logger.info(
                    f"✅ Successfully deleted {count} edges from {from_key} to {to_collection}"
                )
            else:
                self.logger.warning(
                    f"⚠️ No edges found from {from_key} to {to_collection} in collection: {edge_collection}"
                )

            return count

        except Exception as e:
            self.logger.error(
                f"❌ Failed to delete edges from {from_key} to {to_collection} in {edge_collection}: {str(e)}"
            )
            return 0

    async def delete_nodes_and_edges(
        self,
        keys: List[str],
        collection: str,
        graph_name: str = GraphNames.KNOWLEDGE_GRAPH.value,
        transaction: Optional[str] = None
    ) -> None:
        """
        Delete nodes and all their connected edges.

        This method dynamically discovers all edge collections in the graph
        and deletes edges from all of them, matching the behavior of base_arango_service.

        Steps:
        1. Get all edge collections from the graph definition
        2. Delete all edges FROM the nodes (in all edge collections)
        3. Delete all edges TO the nodes (in all edge collections)
        4. Delete the nodes themselves
        """
        if not keys:
            self.logger.info("No keys provided for deletion. Skipping.")
            return

        try:
            self.logger.info(f"🚀 Starting deletion of nodes {keys} from '{collection}' and their edges in graph '{graph_name}'.")

            # Step 1: Get all edge collections from the named graph definition
            graph_info = await self.http_client.get_graph(graph_name)

            if not graph_info:
                self.logger.warning(f"⚠️ Graph '{graph_name}' not found. Using fallback edge collections.")
                # Fallback to known edge collections if graph not found
                edge_collections = [
                    CollectionNames.PERMISSION.value,
                    CollectionNames.BELONGS_TO.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.INHERIT_PERMISSIONS.value,
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.USER_APP_RELATION.value,
                ]
            else:
                # ArangoDB REST API returns graph info with 'graph' key containing the definition
                graph_def = graph_info.get('graph', graph_info)  # Handle both nested and direct formats
                edge_definitions = graph_def.get('edgeDefinitions', [])
                edge_collections = [e.get('collection') for e in edge_definitions if e.get('collection')]

                if not edge_collections:
                    self.logger.warning(f"⚠️ Graph '{graph_name}' has no edge collections defined.")
                else:
                    self.logger.info(f"🔎 Found {len(edge_collections)} edge collections in graph: {edge_collections}")

            # Step 2: Delete all edges connected to the target nodes
            # Construct the full node IDs to match against _from and _to fields
            node_ids = [f"{collection}/{key}" for key in keys]

            edge_delete_query = """
            FOR edge IN @@edge_collection
                FILTER edge._from IN @node_ids OR edge._to IN @node_ids
                REMOVE edge IN @@edge_collection
                OPTIONS { ignoreErrors: true }
            """

            for edge_collection in edge_collections:
                try:
                    await self.http_client.execute_aql(
                        edge_delete_query,
                        bind_vars={
                            "node_ids": node_ids,
                            "@edge_collection": edge_collection
                        },
                        txn_id=transaction
                    )
                except Exception as e:
                    # Log but continue with other edge collections
                    self.logger.warning(f"⚠️ Failed to delete edges from {edge_collection}: {str(e)}")

            self.logger.info(f"🔥 Successfully ran edge cleanup for nodes: {keys}")

            # Step 3: Delete the nodes themselves
            await self.delete_nodes(keys, collection, transaction)

            self.logger.info(f"✅ Successfully deleted {len(keys)} nodes and their associated edges from '{collection}'")

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

    # ==================== Helper Methods for Deletion ====================

    async def _check_record_permission(
        self,
        record_id: str,
        user_key: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """Check user's permission role on a record."""
        try:
            query = f"""
            FOR edge IN {CollectionNames.PERMISSION.value}
                FILTER edge._to == @record_to
                    AND edge._from == @user_from
                    AND edge.type == 'USER'
                LIMIT 1
                RETURN edge.role
            """

            result = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "record_to": f"records/{record_id}",
                    "user_from": f"users/{user_key}"
                },
                txn_id=transaction
            )

            return result[0] if result else None

        except Exception as e:
            self.logger.error(f"Failed to check record permission: {e}")
            return None

    async def _check_drive_permissions(
        self,
        record_id: str,
        user_key: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """Check Google Drive specific permissions."""
        try:
            self.logger.info(f"🔍 Checking Drive permissions for record {record_id} and user {user_key}")

            drive_permission_query = """
            LET user_from = CONCAT('users/', @user_key)
            LET record_from = CONCAT('records/', @record_id)

            // 1. Check direct user permissions on the record
            LET direct_permission = FIRST(
                FOR perm IN @@permission
                    FILTER perm._to == record_from
                    FILTER perm._from == user_from
                    FILTER perm.type == "USER"
                    RETURN perm.role
            )

            // 2. Check group permissions
            LET group_permission = FIRST(
                FOR belongs_edge IN @@belongs_to
                    FILTER belongs_edge._from == user_from
                    FILTER belongs_edge.entityType == "GROUP"
                    LET group = DOCUMENT(belongs_edge._to)
                    FILTER group != null
                    FOR perm IN @@permission
                        FILTER perm._to == record_from
                        FILTER perm._from == group._id
                        FILTER perm.type == "GROUP" OR perm.type == "ROLE"
                        RETURN perm.role
            )

            // 3. Check domain permissions
            LET domain_permission = FIRST(
                FOR perm IN @@permission
                    FILTER perm._to == record_from
                    FILTER perm.type == "DOMAIN"
                    RETURN perm.role
            )

            // 4. Check anyone permissions
            LET anyone_permission = FIRST(
                FOR perm IN @@anyone
                    FILTER perm._to == record_from
                    RETURN perm.role
            )

            // Return the highest permission found
            RETURN direct_permission || group_permission || domain_permission || anyone_permission
            """

            result = await self.http_client.execute_aql(
                drive_permission_query,
                bind_vars={
                    "record_id": record_id,
                    "user_key": user_key,
                    "@permission": CollectionNames.PERMISSION.value,
                    "@belongs_to": CollectionNames.BELONGS_TO.value,
                    "@anyone": CollectionNames.ANYONE.value,
                },
                txn_id=transaction
            )

            return result[0] if result else None

        except Exception as e:
            self.logger.error(f"Failed to check Drive permissions: {e}")
            return None

    async def _check_gmail_permissions(
        self,
        record_id: str,
        user_key: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """Check Gmail specific permissions."""
        try:
            self.logger.info(f"🔍 Checking Gmail permissions for record {record_id} and user {user_key}")

            gmail_permission_query = """
            LET user_from = CONCAT('users/', @user_key)
            LET record_from = CONCAT('records/', @record_id)

            // Get user details
            LET user = DOCUMENT(user_from)
            LET user_email = user ? user.email : null

            // 1. Check if user is sender/recipient of the email
            LET email_access = user_email ? (
                FOR record IN @@records
                    FILTER record._key == @record_id
                    FILTER record.recordType == "MAIL"
                    // Get the mail record
                    FOR mail_edge IN @@is_of_type
                        FILTER mail_edge._from == record._id
                        LET mail = DOCUMENT(mail_edge._to)
                        FILTER mail != null
                        // Check if user is sender
                        LET is_sender = mail.from == user_email OR mail.senderEmail == user_email
                        // Check if user is in recipients (to, cc, bcc)
                        LET is_in_to = user_email IN (mail.to || [])
                        LET is_in_cc = user_email IN (mail.cc || [])
                        LET is_in_bcc = user_email IN (mail.bcc || [])
                        LET is_recipient = is_in_to OR is_in_cc OR is_in_bcc

                        FILTER is_sender OR is_recipient
                        RETURN is_sender ? "OWNER" : "WRITER"
            ) : null

            // 2. Check direct permissions
            LET direct_permission = FIRST(
                FOR perm IN @@permission
                    FILTER perm._to == record_from
                    FILTER perm._from == user_from
                    FILTER perm.type == "USER"
                    RETURN perm.role
            )

            // Return email access or direct permission
            RETURN FIRST(email_access) || direct_permission
            """

            result = await self.http_client.execute_aql(
                gmail_permission_query,
                bind_vars={
                    "record_id": record_id,
                    "user_key": user_key,
                    "@records": CollectionNames.RECORDS.value,
                    "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                    "@permission": CollectionNames.PERMISSION.value,
                },
                txn_id=transaction
            )

            return result[0] if result else None

        except Exception as e:
            self.logger.error(f"Failed to check Gmail permissions: {e}")
            return None

    async def _get_kb_context_for_record(
        self,
        record_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Get KB context for a record."""
        try:
            self.logger.info(f"🔍 Finding KB context for record {record_id}")

            kb_query = """
            LET record_from = CONCAT('records/', @record_id)
            // Find KB via belongs_to edge
            LET kb_edge = FIRST(
                FOR btk_edge IN @@belongs_to
                    FILTER btk_edge._from == record_from
                    RETURN btk_edge
            )
            LET kb = kb_edge ? DOCUMENT(kb_edge._to) : null
            RETURN kb ? {
                kb_id: kb._key,
                kb_name: kb.groupName,
                org_id: kb.orgId
            } : null
            """

            result = await self.http_client.execute_aql(
                kb_query,
                bind_vars={
                    "record_id": record_id,
                    "@belongs_to": CollectionNames.BELONGS_TO.value,
                },
                txn_id=transaction
            )

            return result[0] if result else None

        except Exception as e:
            self.logger.error(f"Failed to get KB context: {e}")
            return None

    async def get_user_kb_permission(
        self,
        kb_id: str,
        user_id: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """Get user's permission on a KB."""
        try:
            self.logger.info(f"🔍 Checking permissions for user {user_id} on KB {kb_id}")

            # Check for direct user permission
            query = """
            LET user_from = CONCAT('users/', @user_id)
            LET kb_to = CONCAT('recordGroups/', @kb_id)

            // Check for direct user permission
            LET direct_perm = FIRST(
                FOR perm IN @@permissions_collection
                    FILTER perm._from == user_from
                    FILTER perm._to == kb_to
                    FILTER perm.type == "USER"
                    RETURN perm.role
            )

            RETURN direct_perm
            """

            result = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "kb_id": kb_id,
                    "user_id": user_id,
                    "@permissions_collection": CollectionNames.PERMISSION.value,
                },
                txn_id=transaction
            )

            role = result[0] if result else None
            if role:
                self.logger.info(f"✅ Found permission: user {user_id} has role '{role}' on KB {kb_id}")
            return role

        except Exception as e:
            self.logger.error(f"Failed to check KB permission: {e}")
            return None

    async def _get_attachment_ids(
        self,
        record_id: str,
        transaction: Optional[str] = None
    ) -> List[str]:
        """Get attachment IDs for a record."""
        attachments_query = f"""
        FOR edge IN {CollectionNames.RECORD_RELATIONS.value}
            FILTER edge._from == @record_from
                AND edge.relationshipType == 'ATTACHMENT'
            RETURN PARSE_IDENTIFIER(edge._to).key
        """

        attachment_ids = await self.http_client.execute_aql(
            attachments_query,
            bind_vars={"record_from": f"records/{record_id}"},
            txn_id=transaction
        )
        return attachment_ids if attachment_ids else []

    async def _delete_record_with_type(
        self,
        record_id: str,
        type_collections: List[str],
        transaction: Optional[str] = None
    ) -> None:
        """Delete a record and its type-specific documents using existing generic methods."""
        record_key = record_id

        # Delete all edges FROM this record
        await self.delete_edges_from(record_key, CollectionNames.RECORDS.value, CollectionNames.RECORD_RELATIONS.value, transaction)
        await self.delete_edges_from(record_key, CollectionNames.RECORDS.value, CollectionNames.IS_OF_TYPE.value, transaction)
        await self.delete_edges_from(record_key, CollectionNames.RECORDS.value, CollectionNames.BELONGS_TO.value, transaction)

        # Delete all edges TO this record
        await self.delete_edges_to(record_key, CollectionNames.RECORDS.value, CollectionNames.RECORD_RELATIONS.value, transaction)
        await self.delete_edges_to(record_key, CollectionNames.RECORDS.value, CollectionNames.PERMISSION.value, transaction)

        # Delete type-specific documents (files, mails, etc.)
        for collection in type_collections:
            try:
                await self.delete_nodes([record_key], collection, transaction)
            except Exception:
                pass  # Collection might not have this document

        # Delete main record
        await self.delete_nodes([record_key], CollectionNames.RECORDS.value, transaction)

    async def _execute_outlook_record_deletion(
        self,
        record_id: str,
        record: Dict,
        transaction: Optional[str] = None
    ) -> Dict:
        """Execute Outlook record deletion - deletes email and all attachments."""
        try:
            # Get attachments (child records with ATTACHMENT relation)
            attachments_query = f"""
            FOR edge IN {CollectionNames.RECORD_RELATIONS.value}
                FILTER edge._from == @record_from
                    AND edge.relationshipType == 'ATTACHMENT'
                RETURN PARSE_IDENTIFIER(edge._to).key
            """

            attachment_ids = await self.http_client.execute_aql(
                attachments_query,
                bind_vars={"record_from": f"records/{record_id}"},
                txn_id=transaction
            )
            attachment_ids = attachment_ids if attachment_ids else []

            # Delete all attachments first
            for attachment_id in attachment_ids:
                self.logger.info(f"Deleting attachment {attachment_id} of email {record_id}")
                await self._delete_outlook_edges(attachment_id, transaction)
                await self._delete_file_record(attachment_id, transaction)
                await self._delete_main_record(attachment_id, transaction)

            # Delete the email itself
            await self._delete_outlook_edges(record_id, transaction)

            # Delete mail record
            await self._delete_mail_record(record_id, transaction)

            # Delete main record
            await self._delete_main_record(record_id, transaction)

            self.logger.info(f"✅ Deleted Outlook record {record_id} with {len(attachment_ids)} attachments")

            return {
                "success": True,
                "record_id": record_id,
                "attachments_deleted": len(attachment_ids)
            }

        except Exception as e:
            self.logger.error(f"❌ Outlook deletion failed: {str(e)}")
            return {
                "success": False,
                "reason": f"Transaction failed: {str(e)}"
            }

    async def _delete_outlook_edges(
        self,
        record_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """Delete Outlook specific edges."""
        edge_strategies = {
            CollectionNames.IS_OF_TYPE.value: {
                "filter": "edge._from == @record_from",
                "bind_vars": {"record_from": f"records/{record_id}"},
            },
            CollectionNames.RECORD_RELATIONS.value: {
                "filter": "(edge._from == @record_from OR edge._to == @record_to)",
                "bind_vars": {
                    "record_from": f"records/{record_id}",
                    "record_to": f"records/{record_id}",
                },
            },
            CollectionNames.PERMISSION.value: {
                "filter": "edge._to == @record_to",
                "bind_vars": {"record_to": f"records/{record_id}"},
            },
            CollectionNames.BELONGS_TO.value: {
                "filter": "edge._from == @record_from",
                "bind_vars": {"record_from": f"records/{record_id}"},
            },
        }

        query_template = """
        FOR edge IN @@edge_collection
            FILTER {filter}
            REMOVE edge IN @@edge_collection
            RETURN OLD
        """

        total_deleted = 0
        for collection, strategy in edge_strategies.items():
            try:
                query = query_template.format(filter=strategy["filter"])
                bind_vars = {"@edge_collection": collection}
                bind_vars.update(strategy["bind_vars"])

                result = await self.http_client.execute_aql(query, bind_vars, txn_id=transaction)
                deleted_count = len(result) if result else 0
                total_deleted += deleted_count

                if deleted_count > 0:
                    self.logger.debug(f"Deleted {deleted_count} edges from {collection}")

            except Exception as e:
                self.logger.error(f"Failed to delete edges from {collection}: {e}")
                raise

        self.logger.debug(f"Total edges deleted for record {record_id}: {total_deleted}")

    async def _delete_file_record(
        self,
        record_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """Delete file record from files collection."""
        file_deletion_query = """
        REMOVE @record_id IN @@files_collection
        RETURN OLD
        """

        await self.http_client.execute_aql(
            file_deletion_query,
            bind_vars={
                "record_id": record_id,
                "@files_collection": CollectionNames.FILES.value,
            },
            txn_id=transaction
        )

    async def _delete_mail_record(
        self,
        record_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """Delete mail record from mails collection."""
        mail_deletion_query = """
        REMOVE @record_id IN @@mails_collection
        RETURN OLD
        """

        await self.http_client.execute_aql(
            mail_deletion_query,
            bind_vars={
                "record_id": record_id,
                "@mails_collection": CollectionNames.MAILS.value,
            },
            txn_id=transaction
        )

    async def _delete_main_record(
        self,
        record_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """Delete main record from records collection."""
        record_deletion_query = """
        REMOVE @record_id IN @@records_collection
        RETURN OLD
        """

        await self.http_client.execute_aql(
            record_deletion_query,
            bind_vars={
                "record_id": record_id,
                "@records_collection": CollectionNames.RECORDS.value,
            },
            txn_id=transaction
        )

    async def _delete_drive_specific_edges(
        self,
        record_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """Delete Google Drive specific edges with optimized queries."""
        drive_edge_collections = self.connector_delete_permissions[Connectors.GOOGLE_DRIVE.value]["edge_collections"]

        # Define edge deletion strategies - maps collection to query config
        edge_deletion_strategies = {
            CollectionNames.USER_DRIVE_RELATION.value: {
                "filter": "edge._to == CONCAT('drives/', @record_id)",
                "bind_vars": {"record_id": record_id},
                "description": "Drive user relations"
            },
            CollectionNames.IS_OF_TYPE.value: {
                "filter": "edge._from == @record_from",
                "bind_vars": {"record_from": f"records/{record_id}"},
                "description": "IS_OF_TYPE edges"
            },
            CollectionNames.PERMISSION.value: {
                "filter": "edge._to == @record_to",
                "bind_vars": {"record_to": f"records/{record_id}"},
                "description": "Permission edges"
            },
            CollectionNames.BELONGS_TO.value: {
                "filter": "edge._from == @record_from",
                "bind_vars": {"record_from": f"records/{record_id}"},
                "description": "Belongs to edges"
            },
            # Default strategy for bidirectional edges
            "default": {
                "filter": "edge._from == @record_from OR edge._to == @record_to",
                "bind_vars": {
                    "record_from": f"records/{record_id}",
                    "record_to": f"records/{record_id}"
                },
                "description": "Bidirectional edges"
            }
        }

        # Single query template for all edge collections
        deletion_query_template = """
        FOR edge IN @@edge_collection
            FILTER {filter}
            REMOVE edge IN @@edge_collection
            RETURN OLD
        """

        total_deleted = 0

        for edge_collection in drive_edge_collections:
            try:
                # Get strategy for this collection or use default
                strategy = edge_deletion_strategies.get(edge_collection, edge_deletion_strategies["default"])

                # Build query with specific filter
                deletion_query = deletion_query_template.format(filter=strategy["filter"])

                # Prepare bind variables
                bind_vars = {
                    "@edge_collection": edge_collection,
                    **strategy["bind_vars"]
                }

                self.logger.debug(f"🔍 Deleting {strategy['description']} from {edge_collection}")

                # Execute deletion
                result = await self.http_client.execute_aql(deletion_query, bind_vars, txn_id=transaction)
                deleted_count = len(result) if result else 0
                total_deleted += deleted_count

                if deleted_count > 0:
                    self.logger.info(f"🗑️ Deleted {deleted_count} {strategy['description']} from {edge_collection}")
                else:
                    self.logger.debug(f"📝 No {strategy['description']} found in {edge_collection}")

            except Exception as e:
                self.logger.error(f"❌ Failed to delete edges from {edge_collection}: {str(e)}")
                raise

        self.logger.info(f"Total Drive edges deleted for record {record_id}: {total_deleted}")

    async def _delete_drive_anyone_permissions(
        self,
        record_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """Delete Drive-specific 'anyone' permissions."""
        anyone_deletion_query = """
        FOR anyone_perm IN @@anyone
            FILTER anyone_perm.file_key == @record_id
            REMOVE anyone_perm IN @@anyone
            RETURN OLD
        """

        await self.http_client.execute_aql(
            anyone_deletion_query,
            bind_vars={
                "record_id": record_id,
                "@anyone": CollectionNames.ANYONE.value,
            },
            txn_id=transaction
        )

    async def _delete_kb_specific_edges(
        self,
        record_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """Delete KB-specific edges."""
        kb_edge_collections = self.connector_delete_permissions[Connectors.KNOWLEDGE_BASE.value]["edge_collections"]

        edge_deletion_query = """
        FOR edge IN @@edge_collection
            FILTER edge._from == @record_from OR edge._to == @record_to
            REMOVE edge IN @@edge_collection
            RETURN OLD
        """

        total_deleted = 0
        for edge_collection in kb_edge_collections:
            try:
                bind_vars = {
                    "@edge_collection": edge_collection,
                    "record_from": f"records/{record_id}",
                    "record_to": f"records/{record_id}"
                }

                result = await self.http_client.execute_aql(edge_deletion_query, bind_vars, txn_id=transaction)
                deleted_count = len(result) if result else 0
                total_deleted += deleted_count

                if deleted_count > 0:
                    self.logger.debug(f"Deleted {deleted_count} edges from {edge_collection}")

            except Exception as e:
                self.logger.error(f"Failed to delete KB edges from {edge_collection}: {e}")
                raise

        self.logger.info(f"Total KB edges deleted for record {record_id}: {total_deleted}")

    async def _execute_gmail_record_deletion(
        self,
        record_id: str,
        record: Dict,
        user_role: str,
        transaction: Optional[str] = None
    ) -> Dict:
        """Execute Gmail record deletion."""
        try:
            # Get attachments (child records with ATTACHMENT relation)
            attachments_query = f"""
            FOR edge IN {CollectionNames.RECORD_RELATIONS.value}
                FILTER edge._from == @record_from
                    AND edge.relationshipType == 'ATTACHMENT'
                RETURN PARSE_IDENTIFIER(edge._to).key
            """

            attachment_ids = await self.http_client.execute_aql(
                attachments_query,
                bind_vars={"record_from": f"records/{record_id}"},
                txn_id=transaction
            )
            attachment_ids = attachment_ids if attachment_ids else []

            # Delete all attachments first
            for attachment_id in attachment_ids:
                self.logger.info(f"Deleting attachment {attachment_id} of email {record_id}")
                await self._delete_outlook_edges(attachment_id, transaction)
                await self._delete_file_record(attachment_id, transaction)
                await self._delete_main_record(attachment_id, transaction)

            # Delete the email itself
            await self._delete_outlook_edges(record_id, transaction)

            # Delete mail record
            await self._delete_mail_record(record_id, transaction)

            # Delete main record
            await self._delete_main_record(record_id, transaction)

            self.logger.info(f"✅ Deleted Gmail record {record_id} with {len(attachment_ids)} attachments")

            return {
                "success": True,
                "record_id": record_id,
                "attachments_deleted": len(attachment_ids)
            }

        except Exception as e:
            self.logger.error(f"❌ Gmail deletion failed: {str(e)}")
            return {
                "success": False,
                "reason": f"Transaction failed: {str(e)}"
            }

    async def _execute_drive_record_deletion(
        self,
        record_id: str,
        record: Dict,
        user_role: str,
        transaction: Optional[str] = None
    ) -> Dict:
        """Execute Drive record deletion."""
        try:
            # Delete Drive-specific edges
            await self._delete_drive_specific_edges(record_id, transaction)

            # Delete 'anyone' permissions specific to Drive
            await self._delete_drive_anyone_permissions(record_id, transaction)

            # Delete file record
            await self._delete_file_record(record_id, transaction)

            # Delete main record
            await self._delete_main_record(record_id, transaction)

            self.logger.info(f"✅ Deleted Drive record {record_id}")

            return {
                "success": True,
                "record_id": record_id,
                "connector": Connectors.GOOGLE_DRIVE.value,
                "user_role": user_role
            }

        except Exception as e:
            self.logger.error(f"❌ Drive deletion failed: {str(e)}")
            return {
                "success": False,
                "reason": f"Transaction failed: {str(e)}"
            }

    async def _execute_kb_record_deletion(
        self,
        record_id: str,
        record: Dict,
        kb_context: Dict,
        transaction: Optional[str] = None
    ) -> Dict:
        """Execute KB record deletion."""
        try:
            # Delete KB-specific edges
            await self._delete_kb_specific_edges(record_id, transaction)

            # Delete file record
            await self._delete_file_record(record_id, transaction)

            # Delete main record
            await self._delete_main_record(record_id, transaction)

            self.logger.info(f"✅ Deleted KB record {record_id}")

            return {
                "success": True,
                "record_id": record_id,
                "connector": Connectors.KNOWLEDGE_BASE.value,
                "kb_context": kb_context
            }

        except Exception as e:
            self.logger.error(f"❌ KB deletion failed: {str(e)}")
            return {
                "success": False,
                "reason": f"Transaction failed: {str(e)}"
            }

    # ==================== Knowledge Hub Operations ====================

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
        """Get root level nodes (KBs and Apps) for Knowledge Hub."""
        start = time.perf_counter()
        query = """
        LET user_doc = DOCUMENT(CONCAT("users/", @user_key))
        LET user_id = user_doc != null ? user_doc.userId : null

        LET kbs = @include_kbs ? (
            FOR kb IN recordGroups
                FILTER kb.orgId == @org_id
                FILTER kb.connectorName == "KB"

                LET has_direct_user_perm = (LENGTH(
                    FOR perm IN permission
                        FILTER perm._from == CONCAT("users/", @user_key)
                        FILTER perm._to == kb._id
                        FILTER perm.type == "USER"
                        RETURN 1
                ) > 0)

                LET has_team_perm = (LENGTH(
                    FOR user_team_perm IN permission
                        FILTER user_team_perm._from == CONCAT("users/", @user_key)
                        FILTER user_team_perm.type == "USER"
                        FILTER STARTS_WITH(user_team_perm._to, "teams/")
                        FOR team_kb_perm IN permission
                            FILTER team_kb_perm._from == user_team_perm._to
                            FILTER team_kb_perm._to == kb._id
                            FILTER team_kb_perm.type == "TEAM"
                            RETURN 1
                ) > 0)

                LET has_permission = has_direct_user_perm OR has_team_perm
                FILTER has_permission
                // Check for children via belongsTo edges
                LET has_record_children = (LENGTH(
                    // KB: Use belongsTo edges (record -> belongsTo -> recordGroup)
                    // Only direct children: externalParentId must be null
                    FOR edge IN belongsTo
                        FILTER edge._to == kb._id AND STARTS_WITH(edge._from, "records/")
                        LET record = DOCUMENT(edge._from)
                        FILTER record != null
                        FILTER record.externalParentId == null
                        RETURN 1
                ) > 0)

                LET has_children = has_record_children

                LET is_creator = kb.createdBy == @user_key OR kb.createdBy == user_id
                LET user_perms = (
                    FOR perm IN permission
                        FILTER perm._to == kb._id
                        FILTER perm.type == "USER"
                        RETURN perm
                )
                LET team_perms = (
                    FOR perm IN permission
                        FILTER perm._to == kb._id
                        FILTER perm.type == "TEAM"
                        RETURN perm
                )
                LET has_other_users = (
                    LENGTH(user_perms) > (is_creator ? 1 : 0) OR
                    LENGTH(team_perms) > 0
                )
                LET sharingStatus = is_creator AND NOT has_other_users ? "private" : "shared"

                RETURN {
                    id: kb._key,
                    name: kb.groupName,
                    nodeType: "kb",
                    parentId: null,
                    origin: "KB",
                    connector: "KB",
                    createdAt: kb.sourceCreatedAtTimestamp != null ? kb.sourceCreatedAtTimestamp : 0,
                    updatedAt: kb.sourceLastModifiedTimestamp != null ? kb.sourceLastModifiedTimestamp : 0,
                    webUrl: CONCAT("/kb/", kb._key),
                    hasChildren: has_children,
                    sharingStatus: sharingStatus
                }
        ) : []

        // Get Apps
        LET apps = @include_apps ? (
            FOR app IN apps
                FILTER app._key IN @user_app_ids
                FILTER app.type != "KB"  // Exclude KB app
                LET has_children = (LENGTH(
                    FOR rg IN recordGroups
                        FILTER rg.connectorId == app._key
                        RETURN 1
                ) > 0)

                LET sharingStatus = app.scope != null ? app.scope : "personal"

                RETURN {
                    id: app._key,
                    name: app.name,
                    nodeType: "app",
                    parentId: null,
                    origin: "CONNECTOR",
                    connector: app.type,
                    createdAt: app.createdAtTimestamp || 0,
                    updatedAt: app.updatedAtTimestamp || 0,
                    webUrl: CONCAT("/app/", app._key),
                    hasChildren: has_children,
                    sharingStatus: sharingStatus
                }
        ) : []

        LET all_nodes = APPEND(kbs, apps)
        // KBs and Apps are always containers, so include all when only_containers is true
        LET filtered_nodes = all_nodes
        LET sorted_nodes = (
            FOR node IN filtered_nodes
                SORT node[@sort_field] @sort_dir
                RETURN node
        )

        LET total_count = LENGTH(sorted_nodes)
        LET paginated_nodes = SLICE(sorted_nodes, @skip, @limit)

        RETURN { nodes: paginated_nodes, total: total_count }
        """

        bind_vars = {
            "org_id": org_id,
            "user_key": user_key,
            "user_app_ids": user_app_ids,
            "include_kbs": include_kbs,
            "include_apps": include_apps,
            "skip": skip,
            "limit": limit,
            "sort_field": sort_field,
            "sort_dir": sort_dir,
        }

        result = await self.http_client.execute_aql(query, bind_vars=bind_vars, txn_id=transaction)
        elapsed = time.perf_counter() - start
        self.logger.info(f"get_knowledge_hub_root_nodes finished in {elapsed * 1000} ms")
        return result[0] if result else {"nodes": [], "total": 0}

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
        Get direct children of a node for tree navigation (browse mode).

        For filtered/searched results, use get_knowledge_hub_search with parent_id instead.

        Args:
            parent_id: The ID of the parent node.
            parent_type: The type of parent: 'app', 'kb', 'recordGroup', 'folder', 'record'.
            org_id: The organization ID.
            user_key: The user's key for permission filtering.
            skip: Number of items to skip for pagination.
            limit: Maximum number of items to return.
            sort_field: Field to sort by.
            sort_dir: Sort direction ('ASC' or 'DESC').
            only_containers: If True, only return nodes that can have children.
            transaction: Optional transaction ID.
        """
        start = time.perf_counter()

        # Use optimized split query for recordGroup types
        if parent_type in ("kb", "recordGroup"):
            result = await self._get_record_group_children_split(
                parent_id, user_key, skip, limit, sort_field, sort_dir, only_containers, transaction
            )
            elapsed = time.perf_counter() - start
            self.logger.info(f"get_knowledge_hub_children finished in {elapsed * 1000} ms")
            return result

        # Generate the sub-query based on parent type
        if parent_type == "app":
            sub_query, parent_bind_vars = self._get_app_children_subquery(parent_id, org_id, user_key)
        elif parent_type in ("folder", "record"):
            sub_query, parent_bind_vars = self._get_record_children_subquery(parent_id, org_id, user_key)
        else:
            return {"nodes": [], "total": 0}

        # Build bind variables (no filters - just sorting and pagination)
        # Note: org_id is only included by subqueries that actually use it
        bind_vars = {
            "user_key": user_key,
            "skip": skip,
            "limit": limit,
            "sort_field": sort_field,
            "sort_dir": sort_dir,
            "only_containers": only_containers,
            **parent_bind_vars,
        }

        # Simple query for direct children with sorting and pagination
        query = f"""
        {sub_query}

        LET filtered_children = (
            FOR node IN raw_children
                // Include all container types (app, kb, recordGroup, folder) even if empty
                FILTER @only_containers == false OR node.hasChildren == true OR node.nodeType IN ["app", "kb", "recordGroup", "folder"]
                RETURN node
        )
        LET sorted_children = (FOR child IN filtered_children SORT child[@sort_field] @sort_dir RETURN child)
        LET total_count = LENGTH(sorted_children)
        LET paginated_children = SLICE(sorted_children, @skip, @limit)

        RETURN {{ nodes: paginated_children, total: total_count }}
        """

        result = await self.http_client.execute_aql(query, bind_vars=bind_vars, txn_id=transaction)
        elapsed = time.perf_counter() - start
        self.logger.info(f"get_knowledge_hub_children finished in {elapsed * 1000} ms")
        return result[0] if result else {"nodes": [], "total": 0}

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
        parent_id: Optional[str] = None,  # For scoped search
        parent_type: Optional[str] = None,  # Type of parent (app/kb/recordGroup/record)
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
        """
        start = time.perf_counter()

        # Build filters using existing helper
        filter_conditions_list, filter_params = self._build_knowledge_hub_filter_conditions(
            search_query=search_query,
            node_types=node_types,
            record_types=record_types,
            indexing_status=indexing_status,
            created_at=created_at,
            updated_at=updated_at,
            size=size,
            origins=origins,
            connector_ids=connector_ids,
            kb_ids=kb_ids,
            only_containers=only_containers,
        )

        # Convert to AQL FILTER statements - add FILTER keyword before each condition
        if filter_conditions_list:
            filter_conditions = "\n        ".join([f"FILTER {cond}" for cond in filter_conditions_list])
        else:
            filter_conditions = ""

        # Build scope filters
        parent_connector_id = None
        # Determine parent_connector_id when parent_type is "record" or "folder"
        # This is needed because _build_scope_filters uses @parent_connector_id for these types
        if parent_id and parent_type in ("record", "folder"):
            try:
                record_doc = await self.get_document(
                    document_key=parent_id,
                    collection="records",
                    transaction=transaction
                )
                if record_doc:
                    parent_connector_id = record_doc.get("connectorId")
            except Exception as e:
                self.logger.warning(f"Failed to fetch parent record connectorId: {str(e)}")
                parent_connector_id = None

        # For children-first approach (kb/recordGroup/record/folder), skip scope filters
        # The intersection will handle scoping instead
        if parent_id and parent_type in ("kb", "recordGroup", "record", "folder"):
            # Don't apply scope filters - let children intersection handle it
            scope_filter_rg = ""
            scope_filter_record = ""
            scope_filter_rg_inline = "true"
            scope_filter_record_inline = "true"
        else:
            # For app-level scope or global search, apply scope filters as before
            scope_filter_rg, scope_filter_record, scope_filter_rg_inline, scope_filter_record_inline = self._build_scope_filters(
                parent_id, parent_type, parent_connector_id
            )

        # Build bind variables
        bind_vars = {
            "org_id": org_id,
            "user_key": user_key,
            "skip": skip,
            "limit": limit,
            "sort_field": sort_field,
            "sort_dir": sort_dir,
        }

        # Add bind variables based on parent_type
        if parent_id:
            if parent_type in ("kb", "recordGroup", "record", "folder"):
                # Children-first approach: only need parent_doc_id
                parent_doc_id = f"recordGroups/{parent_id}" if parent_type in ("kb", "recordGroup") else f"records/{parent_id}"
                bind_vars["parent_doc_id"] = parent_doc_id
            elif parent_type == "app":
                # App-level scope: use parent_id for scope filters
                bind_vars["parent_id"] = parent_id

        # Merge filter params
        bind_vars.update(filter_params)

        # Build children intersection AQL (only for recordGroup/kb/record/folder parents)
        children_intersection_aql = self._build_children_intersection_aql(parent_id, parent_type)


        query = f"""
        LET user_from = CONCAT("users/", @user_key)

        // Get user's accessible apps (for filtering by app access)
        LET user_accessible_apps = (
            FOR app IN OUTBOUND user_from userAppRelation
                FILTER app != null
                RETURN app._key
        )

        // ========== UNIFIED TRAVERSAL: RecordGroups + Nested RecordGroups + Records ==========

        // Path 1: User -> RecordGroup (direct) + Nested RecordGroups + Records
        LET user_direct_rg_data = (
            FOR perm IN permission
                FILTER perm._from == user_from AND perm.type == "USER"
                FILTER STARTS_WITH(perm._to, "recordGroups/")
                LET rg = DOCUMENT(perm._to)
                FILTER rg != null AND rg.orgId == @org_id
                // Only include recordGroups from apps user has access to
                FILTER rg.connectorName == "KB" OR rg.connectorId IN user_accessible_apps
                {scope_filter_rg}

                // Get all child recordGroups + records via inheritPermissions (recursive)
                LET inherited_data = (
                    FOR inherited_node, edge IN 0..100 INBOUND rg._id inheritPermissions
                        FILTER inherited_node != null AND inherited_node.orgId == @org_id

                        // Separate recordGroups from records
                        LET is_rg = IS_SAME_COLLECTION("recordGroups", inherited_node)
                        LET is_record = IS_SAME_COLLECTION("records", inherited_node)

                        FILTER is_rg OR is_record

                        // For records, check if connectorId points to accessible app or accessible recordGroup
                        LET record_app = is_record ? DOCUMENT(CONCAT("apps/", inherited_node.connectorId)) : null
                        LET record_rg = is_record ? DOCUMENT(CONCAT("recordGroups/", inherited_node.connectorId)) : null

                        // Filter by app access: recordGroups must be from accessible apps
                        FILTER (
                            (is_rg AND (inherited_node.connectorName == "KB" OR inherited_node.connectorId IN user_accessible_apps)) OR
                            (is_record AND (
                                (record_app != null AND record_app._key IN user_accessible_apps) OR
                                (record_rg != null AND (record_rg.connectorName == "KB" OR record_rg.connectorId IN user_accessible_apps))
                            ))
                        )

                        // Apply scope filters
                        FILTER (
                            (is_rg AND ({scope_filter_rg_inline})) OR
                            (is_record AND ({scope_filter_record_inline}))
                        )

                        RETURN {{
                            node: inherited_node,
                            type: is_rg ? "recordGroup" : "record"
                        }}
                )

                // Extract recordGroups and records separately
                LET nested_rgs = (
                    FOR item IN inherited_data
                        FILTER item.type == "recordGroup"
                        RETURN item.node
                )

                LET nested_records = (
                    FOR item IN inherited_data
                        FILTER item.type == "record"
                        RETURN item.node
                )

                RETURN {{
                    recordGroup: rg,
                    nestedRecordGroups: nested_rgs,
                    records: nested_records
                }}
        )

        // Path 2: User -> Group/Role -> RecordGroup + Nested RecordGroups + Records
        LET user_group_rg_data = (
            FOR group, userEdge IN 1..1 ANY user_from permission
                FILTER userEdge.type == "USER"
                FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                FOR rg, groupEdge IN 1..1 ANY group._id permission
                    FILTER groupEdge.type == "GROUP" OR groupEdge.type == "ROLE"
                    FILTER IS_SAME_COLLECTION("recordGroups", rg)
                    FILTER rg.orgId == @org_id
                    // Only include recordGroups from apps user has access to
                    FILTER rg.connectorName == "KB" OR rg.connectorId IN user_accessible_apps
                    {scope_filter_rg}

                    // Get all child recordGroups + records via inheritPermissions (recursive)
                    LET inherited_data = (
                        FOR inherited_node, edge IN 0..100 INBOUND rg._id inheritPermissions
                            FILTER inherited_node != null AND inherited_node.orgId == @org_id

                            LET is_rg = IS_SAME_COLLECTION("recordGroups", inherited_node)
                            LET is_record = IS_SAME_COLLECTION("records", inherited_node)

                            FILTER is_rg OR is_record

                            // For records, check if connectorId points to accessible app or accessible recordGroup
                            LET record_app = is_record ? DOCUMENT(CONCAT("apps/", inherited_node.connectorId)) : null
                            LET record_rg = is_record ? DOCUMENT(CONCAT("recordGroups/", inherited_node.connectorId)) : null

                            // Filter by app access: recordGroups must be from accessible apps
                            FILTER (
                                (is_rg AND (inherited_node.connectorName == "KB" OR inherited_node.connectorId IN user_accessible_apps)) OR
                                (is_record AND (
                                    (record_app != null AND record_app._key IN user_accessible_apps) OR
                                    (record_rg != null AND (record_rg.connectorName == "KB" OR record_rg.connectorId IN user_accessible_apps))
                                ))
                            )

                            FILTER (
                                (is_rg AND ({scope_filter_rg_inline})) OR
                                (is_record AND ({scope_filter_record_inline}))
                            )

                            RETURN {{
                                node: inherited_node,
                                type: is_rg ? "recordGroup" : "record"
                            }}
                    )

                    LET nested_rgs = (
                        FOR item IN inherited_data
                            FILTER item.type == "recordGroup"
                            RETURN item.node
                    )

                    LET nested_records = (
                        FOR item IN inherited_data
                            FILTER item.type == "record"
                            RETURN item.node
                    )

                    RETURN {{
                        recordGroup: rg,
                        nestedRecordGroups: nested_rgs,
                        records: nested_records
                    }}
        )

        // Path 3: User -> Org -> RecordGroup + Nested RecordGroups + Records
        LET user_org_rg_data = (
            FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                FILTER belongsEdge.entityType == "ORGANIZATION"
                FOR rg, orgPerm IN 1..1 ANY org._id permission
                    FILTER orgPerm.type == "ORG"
                    FILTER IS_SAME_COLLECTION("recordGroups", rg)
                    FILTER rg.orgId == @org_id
                    // Only include recordGroups from apps user has access to
                    FILTER rg.connectorName == "KB" OR rg.connectorId IN user_accessible_apps
                    {scope_filter_rg}

                    LET inherited_data = (
                        FOR inherited_node, edge IN 0..100 INBOUND rg._id inheritPermissions
                            FILTER inherited_node != null AND inherited_node.orgId == @org_id

                            LET is_rg = IS_SAME_COLLECTION("recordGroups", inherited_node)
                            LET is_record = IS_SAME_COLLECTION("records", inherited_node)

                            FILTER is_rg OR is_record

                            // For records, check if connectorId points to accessible app or accessible recordGroup
                            LET record_app = is_record ? DOCUMENT(CONCAT("apps/", inherited_node.connectorId)) : null
                            LET record_rg = is_record ? DOCUMENT(CONCAT("recordGroups/", inherited_node.connectorId)) : null

                            // Filter by app access: recordGroups must be from accessible apps
                            FILTER (
                                (is_rg AND (inherited_node.connectorName == "KB" OR inherited_node.connectorId IN user_accessible_apps)) OR
                                (is_record AND (
                                    (record_app != null AND record_app._key IN user_accessible_apps) OR
                                    (record_rg != null AND (record_rg.connectorName == "KB" OR record_rg.connectorId IN user_accessible_apps))
                                ))
                            )

                            FILTER (
                                (is_rg AND ({scope_filter_rg_inline})) OR
                                (is_record AND ({scope_filter_record_inline}))
                            )

                            RETURN {{
                                node: inherited_node,
                                type: is_rg ? "recordGroup" : "record"
                            }}
                    )

                    LET nested_rgs = (
                        FOR item IN inherited_data
                            FILTER item.type == "recordGroup"
                            RETURN item.node
                    )

                    LET nested_records = (
                        FOR item IN inherited_data
                            FILTER item.type == "record"
                            RETURN item.node
                    )

                    RETURN {{
                        recordGroup: rg,
                        nestedRecordGroups: nested_rgs,
                        records: nested_records
                    }}
        )

        // Path 4: User -> Team -> RecordGroup + Nested RecordGroups + Records (for KB)
        LET user_team_rg_data = (
            FOR teamPerm IN permission
                FILTER teamPerm.type == "TEAM"
                FILTER STARTS_WITH(teamPerm._to, "recordGroups/")
                LET rg = DOCUMENT(teamPerm._to)
                FILTER rg != null AND rg.orgId == @org_id
                // Only include recordGroups from apps user has access to
                FILTER rg.connectorName == "KB" OR rg.connectorId IN user_accessible_apps
                LET team_id = SPLIT(teamPerm._from, "/")[1]
                LET is_member = (LENGTH(
                    FOR userPerm IN permission
                        FILTER userPerm._from == user_from
                        FILTER userPerm._to == CONCAT("teams/", team_id)
                        RETURN 1
                ) > 0)
                FILTER is_member
                {scope_filter_rg}

                LET inherited_data = (
                    FOR inherited_node, edge IN 0..100 INBOUND rg._id inheritPermissions
                        FILTER inherited_node != null AND inherited_node.orgId == @org_id

                        LET is_rg = IS_SAME_COLLECTION("recordGroups", inherited_node)
                        LET is_record = IS_SAME_COLLECTION("records", inherited_node)

                        FILTER is_rg OR is_record

                        // For records, check if connectorId points to accessible app or accessible recordGroup
                        LET record_app = is_record ? DOCUMENT(CONCAT("apps/", inherited_node.connectorId)) : null
                        LET record_rg = is_record ? DOCUMENT(CONCAT("recordGroups/", inherited_node.connectorId)) : null

                        // Filter by app access: recordGroups must be from accessible apps
                        FILTER (
                            (is_rg AND (inherited_node.connectorName == "KB" OR inherited_node.connectorId IN user_accessible_apps)) OR
                            (is_record AND (
                                (record_app != null AND record_app._key IN user_accessible_apps) OR
                                (record_rg != null AND (record_rg.connectorName == "KB" OR record_rg.connectorId IN user_accessible_apps))
                            ))
                        )

                        FILTER (
                            (is_rg AND ({scope_filter_rg_inline})) OR
                            (is_record AND ({scope_filter_record_inline}))
                        )

                        RETURN {{
                            node: inherited_node,
                            type: is_rg ? "recordGroup" : "record"
                        }}
                )

                LET nested_rgs = (
                    FOR item IN inherited_data
                        FILTER item.type == "recordGroup"
                        RETURN item.node
                )

                LET nested_records = (
                    FOR item IN inherited_data
                        FILTER item.type == "record"
                        RETURN item.node
                )

                RETURN {{
                    recordGroup: rg,
                    nestedRecordGroups: nested_rgs,
                    records: nested_records
                }}
        )

        // Combine all recordGroup+records data
        LET all_rg_data = UNION(user_direct_rg_data, user_group_rg_data, user_org_rg_data, user_team_rg_data)

        // Extract unique recordGroups (parent + nested)
        LET parent_rgs = (
            FOR data IN all_rg_data
                RETURN data.recordGroup
        )

        LET nested_rgs = FLATTEN(
            FOR data IN all_rg_data
                RETURN data.nestedRecordGroups
        )

        LET accessible_rgs = UNION_DISTINCT(parent_rgs, nested_rgs)

        // Extract unique records from recordGroups
        LET rg_inherited_records = FLATTEN(
            FOR data IN all_rg_data
                RETURN data.records
        )

        // ========== DIRECT RECORD ACCESS (not via recordGroup) ==========

        // Path 5: User -> Record (direct, no recordGroup)
        LET user_direct_records = (
            FOR perm IN permission
                FILTER perm._from == user_from AND perm.type == "USER"
                FILTER STARTS_WITH(perm._to, "records/")
                LET record = DOCUMENT(perm._to)
                FILTER record != null AND record.orgId == @org_id
                // Check if record's connectorId points to accessible app or accessible recordGroup
                LET record_app = DOCUMENT(CONCAT("apps/", record.connectorId))
                LET record_rg = DOCUMENT(CONCAT("recordGroups/", record.connectorId))
                FILTER (
                    (record_app != null AND record_app._key IN user_accessible_apps) OR
                    (record_rg != null AND (record_rg.connectorName == "KB" OR record_rg.connectorId IN user_accessible_apps))
                )
                {scope_filter_record}
                RETURN record
        )

        // Path 6: User -> Group/Role -> Record (direct, no recordGroup)
        LET user_group_records = (
            FOR group, userEdge IN 1..1 ANY user_from permission
                FILTER userEdge.type == "USER"
                FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                FOR record, groupEdge IN 1..1 ANY group._id permission
                    FILTER groupEdge.type == "GROUP" OR groupEdge.type == "ROLE"
                    FILTER IS_SAME_COLLECTION("records", record)
                    FILTER record.orgId == @org_id
                    // Check if record's connectorId points to accessible app or accessible recordGroup
                    LET record_app = DOCUMENT(CONCAT("apps/", record.connectorId))
                    LET record_rg = DOCUMENT(CONCAT("recordGroups/", record.connectorId))
                    FILTER (
                        (record_app != null AND record_app._key IN user_accessible_apps) OR
                        (record_rg != null AND (record_rg.connectorName == "KB" OR record_rg.connectorId IN user_accessible_apps))
                    )
                    {scope_filter_record}
                    RETURN record
        )

        // Path 7: User -> Org -> Record (direct, no recordGroup)
        LET user_org_records = (
            FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                FILTER belongsEdge.entityType == "ORGANIZATION"
                FOR record, orgPerm IN 1..1 ANY org._id permission
                    FILTER orgPerm.type == "ORG"
                    FILTER IS_SAME_COLLECTION("records", record)
                    FILTER record.orgId == @org_id
                    // Check if record's connectorId points to accessible app or accessible recordGroup
                    LET record_app = DOCUMENT(CONCAT("apps/", record.connectorId))
                    LET record_rg = DOCUMENT(CONCAT("recordGroups/", record.connectorId))
                    FILTER (
                        (record_app != null AND record_app._key IN user_accessible_apps) OR
                        (record_rg != null AND (record_rg.connectorName == "KB" OR record_rg.connectorId IN user_accessible_apps))
                    )
                    {scope_filter_record}
                    RETURN record
        )

        // Combine all record sources and deduplicate
        LET accessible_records = UNION_DISTINCT(
            rg_inherited_records,
            user_direct_records,
            user_group_records,
            user_org_records
        )

        // ========== CHILDREN TRAVERSAL & INTERSECTION (for recordGroup/kb/record/folder parents) ==========
        // If parent_type is recordGroup/kb/record/folder, traverse children and intersect with accessible nodes

        {children_intersection_aql}

        // ========== BUILD RECORDGROUP NODES ==========
        LET rg_nodes = (
            FOR rg IN final_accessible_rgs
                // Check hasChildren via belongsTo edge (uses edge index on _to)
                LET has_children = LENGTH(
                    FOR edge IN belongsTo
                        FILTER edge._to == rg._id
                        LIMIT 1
                        RETURN 1
                ) > 0

                RETURN {{
                    id: rg._key,
                    name: rg.groupName,
                    nodeType: "recordGroup",
                    parentId: rg.parentId,
                    origin: rg.connectorName == "KB" ? "KB" : "CONNECTOR",
                    connector: rg.connectorName,
                    connectorId: rg.connectorName != "KB" ? rg.connectorId : null,
                    kbId: rg.connectorName == "KB" ? rg._key : null,
                    externalGroupId: rg.externalGroupId,
                    recordType: null,
                    recordGroupType: rg.groupType,
                    indexingStatus: null,
                    createdAt: rg.sourceCreatedAtTimestamp != null ? rg.sourceCreatedAtTimestamp : 0,
                    updatedAt: rg.sourceLastModifiedTimestamp != null ? rg.sourceLastModifiedTimestamp : 0,
                    sizeInBytes: null,
                    mimeType: null,
                    extension: null,
                    webUrl: rg.webUrl,
                    hasChildren: has_children,
                    previewRenderable: true
                }}
        )

        // ========== BUILD RECORD NODES ==========
        LET record_nodes = (
            FOR record IN final_accessible_records
                LET file_info = FIRST(
                    FOR file_edge IN isOfType FILTER file_edge._from == record._id
                    LET file = DOCUMENT(file_edge._to) RETURN file
                )
                LET is_folder = file_info != null AND file_info.isFile == false

                // Check hasChildren via recordRelations edge (uses edge index on _from)
                LET has_children = LENGTH(
                    FOR edge IN recordRelations
                        FILTER edge._from == record._id
                        FILTER edge.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                        LIMIT 1
                        RETURN 1
                ) > 0

                LET record_connector = DOCUMENT(CONCAT("recordGroups/", record.connectorId)) || DOCUMENT(CONCAT("apps/", record.connectorId))
                LET source = (record_connector != null AND record_connector.connectorName == "KB") ? "KB" : "CONNECTOR"

                RETURN {{
                    id: record._key,
                    name: record.recordName,
                    nodeType: is_folder ? "folder" : "record",
                    parentId: record.parentId,
                    origin: source,
                    connector: record.connectorName,
                    connectorId: source == "CONNECTOR" ? record.connectorId : null,
                    kbId: source == "KB" ? record.externalGroupId : null,
                    externalGroupId: record.externalGroupId,
                    recordType: record.recordType,
                    recordGroupType: null,
                    indexingStatus: record.indexingStatus,
                    createdAt: record.sourceCreatedAtTimestamp != null ? record.sourceCreatedAtTimestamp : 0,
                    updatedAt: record.sourceLastModifiedTimestamp != null ? record.sourceLastModifiedTimestamp : 0,
                    sizeInBytes: record.sizeInBytes != null ? record.sizeInBytes : (file_info ? file_info.fileSizeInBytes : null),
                    mimeType: record.mimeType,
                    extension: file_info ? file_info.extension : null,
                    webUrl: record.webUrl,
                    hasChildren: has_children,
                    previewRenderable: record.previewRenderable != null ? record.previewRenderable : true
                }}
        )

        // ========== COMBINE & FILTER ==========
        LET all_nodes = UNION(rg_nodes, record_nodes)

        // Apply search and filter conditions
        LET filtered_nodes = (
            FOR node IN all_nodes
                {filter_conditions}
                RETURN node
        )

        LET sorted_nodes = (FOR node IN filtered_nodes SORT node[@sort_field] @sort_dir RETURN node)
        LET total_count = LENGTH(sorted_nodes)
        LET paginated_nodes = SLICE(sorted_nodes, @skip, @limit)

        RETURN {{ nodes: paginated_nodes, total: total_count }}
        """

        try:
            result = await self.http_client.execute_aql(query, bind_vars=bind_vars, txn_id=transaction)
            duration = time.perf_counter() - start
            self.logger.info(f"Knowledge hub unified search completed in {duration:.3f}s")
            return result[0] if result else {"nodes": [], "total": 0}
        except Exception as e:
            self.logger.error(f"Error in knowledge hub unified search: {str(e)}")
            self.logger.error(f"Query: {query}")
            self.logger.error(f"Bind vars: {bind_vars}")
            raise

    async def get_knowledge_hub_breadcrumbs(
        self,
        node_id: str,
        transaction: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get breadcrumb trail for a node.

        NOTE(N+1 Queries): Uses iterative parent lookup (one query per level) because a single
        AQL graph traversal isn't feasible here. Parent relationships are stored via multiple
        edge types: recordRelations (record->record) and belongsTo (record->recordGroup,
        recordGroup->recordGroup, recordGroup->app).

        Traversal logic:
        - Records: Check recordRelations edge from another record first, then belongsTo to recordGroup
        - RecordGroups: Check belongsTo edge to another recordGroup, then to app (excluding KB apps)
        - Apps: No parent (root level)
        """
        start = time.perf_counter()
        breadcrumbs = []
        current_id = node_id
        visited = set()
        max_depth = 20

        while current_id and len(visited) < max_depth:
            if current_id in visited:
                break
            visited.add(current_id)

            # Get node info and parent in one query
            query = """
            // Try to find document in each collection
            LET record = DOCUMENT("records", @id)
            LET rg = record == null ? DOCUMENT("recordGroups", @id) : null
            LET app = record == null AND rg == null ? DOCUMENT("apps", @id) : null

            // For records, determine if it's a folder by checking the isOfType edge (for nodeType display only)
            LET is_folder = record != null ? (
                FIRST(
                    FOR edge IN isOfType
                        FILTER edge._from == record._id
                        LET f = DOCUMENT(edge._to)
                        FILTER f != null AND f.isFile == false
                        RETURN true
                ) == true
            ) : false

            // Determine node type based on which collection and properties
            LET node_type = record != null ? (
                is_folder ? "folder" : "record"
            ) : (
                rg != null ? (
                    rg.connectorName == "KB" ? "kb" : "recordGroup"
                ) : (
                    app != null ? "app" : null
                )
            )

            // Find parent ID - REFACTORED LOGIC:
            // For Records:
            //   1. Check recordRelations edge from another RECORD only (at one hop)
            //   2. If no record parent, check belongsTo edge to recordGroup
            // For RecordGroups:
            //   1. Check belongsTo edge to another recordGroup
            //   2. If no parent recordGroup, check belongsTo edge to app (exclude KB apps)
            // For Apps: No parent
            LET parent_id = record != null ? (
                // For records: Step 1 - Check for recordRelations edge from another record only
                // Edge direction: parent -> child (edge._from = parent, edge._to = current record)
                (
                    LET record_parent = FIRST(
                        FOR edge IN recordRelations
                            FILTER edge._to == record._id
                            AND edge.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                            LET parent_doc = DOCUMENT(edge._from)
                            FILTER parent_doc != null
                            // Ensure parent is a record, not recordGroup
                            FILTER PARSE_IDENTIFIER(edge._from).collection == "records"
                            RETURN PARSE_IDENTIFIER(edge._from).key
                    )
                    // Step 2: If no record parent, check belongsTo edge to recordGroup
                    RETURN record_parent != null ? record_parent : FIRST(
                        FOR edge IN belongsTo
                            FILTER edge._from == record._id
                            LET parent_rg = DOCUMENT(edge._to)
                            FILTER parent_rg != null
                            FILTER PARSE_IDENTIFIER(edge._to).collection == "recordGroups"
                            RETURN PARSE_IDENTIFIER(edge._to).key
                    )
                )[0]
            ) : (
                rg != null ? (
                    // For recordGroups: Step 1 - Check belongsTo edge to another recordGroup
                    (
                        LET parent_rg = FIRST(
                            FOR edge IN belongsTo
                                FILTER edge._from == rg._id
                                LET parent_doc = DOCUMENT(edge._to)
                                FILTER parent_doc != null
                                FILTER PARSE_IDENTIFIER(edge._to).collection == "recordGroups"
                                RETURN PARSE_IDENTIFIER(edge._to).key
                        )
                        // Step 2: If no parent recordGroup, check belongsTo edge to app
                        RETURN parent_rg != null ? parent_rg : FIRST(
                            FOR edge IN belongsTo
                                FILTER edge._from == rg._id
                                LET app_doc = DOCUMENT(edge._to)
                                FILTER app_doc != null
                                FILTER PARSE_IDENTIFIER(edge._to).collection == "apps"
                                // Exclude KB apps from breadcrumbs
                                FILTER app_doc.type != "KB"
                                RETURN PARSE_IDENTIFIER(edge._to).key
                        )
                    )[0]
                ) : null
            )

            // Build result based on which document type
            LET result = record != null ? {
                id: record._key,
                name: record.recordName,
                nodeType: node_type,
                subType: record.recordType,
                parentId: parent_id
            } : (rg != null ? {
                id: rg._key,
                name: rg.groupName,
                nodeType: node_type,
                subType: rg.connectorName == "KB" ? "KB" : (rg.groupType || rg.connectorName),
                parentId: parent_id
            } : (app != null ? {
                id: app._key,
                name: app.name,
                nodeType: node_type,
                subType: app.type,
                parentId: parent_id
            } : null))

            RETURN result
            """

            result = await self.http_client.execute_aql(query, bind_vars={"id": current_id}, txn_id=transaction)
            if not result or not result[0]:
                break

            node_info = result[0]
            breadcrumbs.append({
                "id": node_info["id"],
                "name": node_info["name"],
                "nodeType": node_info["nodeType"],
                "subType": node_info.get("subType")
            })

            current_id = node_info.get("parentId")

        # Reverse to get root -> leaf order
        breadcrumbs.reverse()
        elapsed = time.perf_counter() - start
        self.logger.info(f"get_knowledge_hub_breadcrumbs finished in {elapsed * 1000} ms")
        return breadcrumbs

    async def get_user_app_ids(
        self,
        user_key: str,
        transaction: Optional[str] = None
    ) -> List[str]:
        """Get list of app IDs the user has access to."""
        query = """
        FOR app IN OUTBOUND CONCAT("users/", @user_key) userAppRelation
            FILTER app != null
            RETURN app._key
        """
        result = await self.http_client.execute_aql(query, bind_vars={"user_key": user_key}, txn_id=transaction)
        return result if result else []

    async def get_knowledge_hub_context_permissions(
        self,
        user_key: str,
        org_id: str,
        parent_id: Optional[str],
        transaction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get user's context-level permissions.
        Supports both direct user permissions and team-based permissions.
        If multiple permissions exist, returns the highest role.
        """
        start = time.perf_counter()
        # Validate parent_id if provided
        if parent_id:
            if not parent_id.strip():
                parent_id = None
            elif parent_id.startswith(('records/', 'recordGroups/', 'apps/')) and len(parent_id.split('/')) < ARANGO_ID_PARTS_COUNT:
                # Malformed document handle - return no access
                return {
                    "role": None,
                    "canUpload": False,
                    "canCreateFolders": False,
                    "canEdit": False,
                    "canDelete": False,
                    "canManagePermissions": False
                }

        if not parent_id:
            query = """
            LET user = DOCUMENT("users", @user_key)
            FILTER user != null
            LET is_admin = user.role == "ADMIN" OR user.orgRole == "ADMIN"
            RETURN {
                role: is_admin ? "ADMIN" : "MEMBER",
                canUpload: is_admin, canCreateFolders: is_admin, canEdit: is_admin,
                canDelete: is_admin, canManagePermissions: is_admin
            }
            """
            results = await self.http_client.execute_aql(query, bind_vars={"user_key": user_key}, txn_id=transaction)
        else:
            query = """
            // Validate parent_id and construct node_id safely
            LET node_id_raw = CONTAINS(@parent_id, "/") ? @parent_id : (
                FIRST(UNION(
                    (FOR doc IN records FILTER doc._key == @parent_id AND doc._key != null AND doc._key != "" RETURN doc._id),
                    (FOR doc IN apps FILTER doc._key == @parent_id AND doc._key != null AND doc._key != "" RETURN doc._id),
                    (FOR doc IN recordGroups FILTER doc._key == @parent_id AND doc._key != null AND doc._key != "" RETURN doc._id)
                ))
            )

            // Validate node_id is not empty or malformed
            LET node_id_valid = (node_id_raw != null AND node_id_raw != "" AND LENGTH(node_id_raw) > 0)
            LET node_id = node_id_valid ? node_id_raw : null

            // Role priority: OWNER > ADMIN > EDITOR > WRITER > COMMENTER > READER
            LET role_priority = {
                "OWNER": 6,
                "ADMIN": 5,
                "EDITOR": 4,
                "WRITER": 3,
                "COMMENTER": 2,
                "READER": 1
            }

            // Step 1: Get permission target (node itself or its parent via inheritPermissions)
            // Only proceed if node_id is valid
            LET permission_target = node_id_valid ? node_id : null

            // For records, check if they inherit from a parent (KB or record group)
            LET inherited_from = (node_id_valid AND STARTS_WITH(node_id, "records/")) ? FIRST(
                FOR edge IN inheritPermissions
                    FILTER edge._from == node_id
                    RETURN edge._to
            ) : null

            // Use inherited parent for permission check if it exists, otherwise use node itself
            LET final_permission_target = node_id_valid ? (inherited_from != null ? inherited_from : permission_target) : null

            // Determine if this is a KB-related node (for root KB fallback)
            LET target_doc = (final_permission_target != null) ? DOCUMENT(final_permission_target) : null
            LET is_record = (node_id_valid AND STARTS_WITH(node_id, "records/"))
            LET record_doc = (is_record AND node_id != null) ? DOCUMENT(node_id) : null
            LET record_connector_id = record_doc != null ? record_doc.connectorId : null
            LET record_connector = (record_connector_id != null AND record_connector_id != "" AND LENGTH(record_connector_id) > 0) ? (
                DOCUMENT(CONCAT("recordGroups/", record_connector_id)) ||
                DOCUMENT(CONCAT("apps/", record_connector_id))
            ) : null
            LET is_direct_kb = record_connector != null AND record_connector.connectorName == "KB"
            LET is_nested_under_kb = is_direct_kb ? false : (
                record_connector != null ? (
                    LENGTH(
                        FOR v IN 0..10 INBOUND CONCAT("recordGroups/", record_connector._key) belongsTo
                            FILTER v != null AND v.connectorName == "KB"
                            RETURN 1
                    ) > 0
                ) : false
            )
            LET is_kb_record = is_record AND (is_direct_kb OR is_nested_under_kb)

            // Also check if target is a recordGroup under KB
            LET is_rg = STARTS_WITH(final_permission_target, "recordGroups/")
            LET rg_doc = is_rg ? target_doc : null
            LET is_kb = rg_doc != null AND rg_doc.connectorName == "KB"
            LET is_nested_rg_under_kb = (is_rg AND NOT is_kb) ? (
                LENGTH(
                    FOR v IN 0..10 INBOUND final_permission_target belongsTo
                        FILTER v != null AND v.connectorName == "KB"
                        RETURN 1
                ) > 0
            ) : false
            LET needs_kb_fallback = is_kb_record OR is_nested_rg_under_kb

            // Step 2: Get direct user permission on the target
            LET direct_user_perm = FIRST(
                FOR perm IN permission
                    FILTER perm._from == CONCAT("users/", @user_key)
                    FILTER perm._to == final_permission_target
                    FILTER perm.type == "USER"
                    RETURN {
                        role: perm.role || "READER",
                        priority: role_priority[perm.role] || 1,
                        source: "direct_user"
                    }
            )

            // Step 3: Get team-based permissions on the target
            LET team_perms = (
                // Get all teams the user belongs to
                FOR user_team_perm IN permission
                    FILTER user_team_perm._from == CONCAT("users/", @user_key)
                    FILTER user_team_perm.type == "USER"
                    FILTER STARTS_WITH(user_team_perm._to, "teams/")
                    // Check if those teams have permission to the target node
                    FOR team_node_perm IN permission
                        FILTER team_node_perm._from == user_team_perm._to
                        FILTER team_node_perm._to == final_permission_target
                        FILTER team_node_perm.type == "TEAM"
                        RETURN {
                            role: user_team_perm.role || "READER",
                            priority: role_priority[user_team_perm.role] || 1,
                            source: "team"
                        }
            )

            // Step 4: Get group-based permissions on the target
            LET group_perms = (
                // Get all groups the user belongs to
                FOR user_group_perm IN permission
                    FILTER user_group_perm._from == CONCAT("users/", @user_key)
                    FILTER user_group_perm.type == "USER"
                    FILTER STARTS_WITH(user_group_perm._to, "groups/")
                    // Check if those groups have permission to the target node
                    FOR group_node_perm IN permission
                        FILTER group_node_perm._from == user_group_perm._to
                        FILTER group_node_perm._to == final_permission_target
                        FILTER group_node_perm.type == "GROUP"
                        RETURN {
                            role: user_group_perm.role || "READER",
                            priority: role_priority[user_group_perm.role] || 1,
                            source: "group"
                        }
            )

            // Step 5: Check org-level and domain-level permissions
            LET user_doc = DOCUMENT("users", @user_key)
            LET org_perm = user_doc != null ? FIRST(
                FOR perm IN permission
                    FILTER perm._to == final_permission_target
                    FILTER perm.type == "ORG"
                    FILTER perm._from == CONCAT("organizations/", @org_id)
                    RETURN {
                        role: perm.role || "READER",
                        priority: role_priority[perm.role] || 1,
                        source: "org"
                    }
            ) : null

            // Step 6: Check ANYONE permissions
            LET anyone_perm = FIRST(
                FOR perm IN permission
                    FILTER perm._to == final_permission_target
                    FILTER perm.type == "ANYONE"
                    RETURN {
                        role: perm.role || "READER",
                        priority: role_priority[perm.role] || 1,
                        source: "anyone"
                    }
            )

            // Step 7: For KB-related nodes, find root KB and check permission (fallback)
            LET start_connector_id = is_kb_record ? record_connector_id : (
                is_nested_rg_under_kb AND rg_doc != null AND rg_doc._key != null AND rg_doc._key != "" ? rg_doc._key : null
            )
            LET start_connector = (start_connector_id != null AND start_connector_id != "" AND LENGTH(start_connector_id) > 0) ? DOCUMENT(CONCAT("recordGroups/", start_connector_id)) : null
            LET is_start_kb = start_connector != null AND start_connector.connectorName == "KB"
            LET root_kb_from_traversal = (start_connector != null AND NOT is_start_kb AND start_connector._key != null AND start_connector._key != "") ? (
                FOR v IN 0..10 INBOUND CONCAT("recordGroups/", start_connector._key) belongsTo
                    FILTER v != null AND v.connectorName == "KB"
                    LIMIT 1
                    RETURN v
            ) : []
            LET root_kb = is_start_kb ? start_connector : (
                (LENGTH(root_kb_from_traversal) > 0) ? root_kb_from_traversal[0] : null
            )
            LET root_kb_to = (root_kb != null AND root_kb._key != null AND root_kb._key != "" AND LENGTH(root_kb._key) > 0) ? CONCAT("recordGroups/", root_kb._key) : null

            // Check direct user permission on root KB
            LET root_kb_direct = (needs_kb_fallback AND root_kb_to != null) ? FIRST(
                FOR perm IN permission
                    FILTER perm._from == CONCAT("users/", @user_key)
                    FILTER perm._to == root_kb_to
                    FILTER perm.type == "USER"
                    FILTER perm.role != null AND perm.role != ""
                    RETURN {
                        role: perm.role,
                        priority: role_priority[perm.role] || 1,
                        source: "root_kb_direct"
                    }
            ) : null

            // Check team permission on root KB
            LET root_kb_team = (needs_kb_fallback AND root_kb_to != null) ? FIRST(
                FOR user_team_perm IN permission
                    FILTER user_team_perm._from == CONCAT("users/", @user_key)
                    FILTER user_team_perm.type == "USER"
                    FILTER STARTS_WITH(user_team_perm._to, "teams/")
                    FOR team_kb_perm IN permission
                        FILTER team_kb_perm._from == user_team_perm._to
                        FILTER team_kb_perm._to == root_kb_to
                        FILTER team_kb_perm.type == "TEAM"
                        RETURN {
                            role: user_team_perm.role || "READER",
                            priority: role_priority[user_team_perm.role] || 1,
                            source: "root_kb_team"
                        }
            ) : null

            // Check group permission on root KB
            LET root_kb_group = (needs_kb_fallback AND root_kb_to != null) ? FIRST(
                FOR kb_group_perm IN permission
                    FILTER kb_group_perm._to == root_kb_to
                    FILTER kb_group_perm.type == "GROUP"
                    FILTER kb_group_perm.role != null AND kb_group_perm.role != ""
                    LET group_to = kb_group_perm._from
                    FOR user_group_perm IN permission
                        FILTER user_group_perm._from == CONCAT("users/", @user_key)
                        FILTER user_group_perm._to == group_to
                        RETURN {
                            role: kb_group_perm.role,
                            priority: role_priority[kb_group_perm.role] || 1,
                            source: "root_kb_group"
                        }
            ) : null

            // Step 8: Combine ALL permissions and get the highest role
            LET all_perms = REMOVE_VALUE(
                FLATTEN([
                    direct_user_perm != null ? [direct_user_perm] : [],
                    team_perms,
                    group_perms,
                    org_perm != null ? [org_perm] : [],
                    anyone_perm != null ? [anyone_perm] : [],
                    root_kb_direct != null ? [root_kb_direct] : [],
                    root_kb_team != null ? [root_kb_team] : [],
                    root_kb_group != null ? [root_kb_group] : []
                ]),
                null
            )

            LET highest_perm = (LENGTH(all_perms) > 0) ? (
                FIRST(
                    FOR p IN all_perms
                        SORT p.priority DESC
                        LIMIT 1
                        RETURN p
                )
            ) : null

            // Only return permissions if user actually has access (don't default to READER)
            LET final_role = (node_id_valid AND highest_perm != null) ? highest_perm.role : null
            LET can_edit = (final_role != null AND final_role IN ["ADMIN", "EDITOR", "WRITER", "OWNER"])
            LET can_upload = (final_role != null AND final_role IN ["ADMIN", "EDITOR", "WRITER", "OWNER"])
            LET can_create = (final_role != null AND final_role IN ["ADMIN", "EDITOR", "WRITER", "OWNER"])
            LET can_delete = (final_role != null AND final_role IN ["ADMIN", "OWNER"])
            LET can_manage = (final_role != null AND final_role IN ["ADMIN", "OWNER"])

            RETURN {
                role: final_role,
                canUpload: can_upload,
                canCreateFolders: can_create,
                canEdit: can_edit,
                canDelete: can_delete,
                canManagePermissions: can_manage
            }
            """
            try:
                results = await self.http_client.execute_aql(
                    query,
                    bind_vars={"user_key": user_key, "org_id": org_id, "parent_id": parent_id},
                    txn_id=transaction
                )
                elapsed = time.perf_counter() - start
                self.logger.info(f"get_knowledge_hub_context_permissions finished in {elapsed * 1000} ms")
            except Exception:
                # Return no access on error (don't grant READER by default)
                return {
                    "role": None,
                    "canUpload": False,
                    "canCreateFolders": False,
                    "canEdit": False,
                    "canDelete": False,
                    "canManagePermissions": False
                }

        if results and results[0]:
            result = results[0]
            # If no permission found (role is null), return no access
            if result.get("role") is None:
                return {
                    "role": None,
                    "canUpload": False,
                    "canCreateFolders": False,
                    "canEdit": False,
                    "canDelete": False,
                    "canManagePermissions": False
                }
            return result
        elapsed = time.perf_counter() - start
        self.logger.info(f"get_knowledge_hub_context_permissions finished in {elapsed * 1000} ms")
        # No results means no access
        return {
            "role": None,
            "canUpload": False,
            "canCreateFolders": False,
            "canEdit": False,
            "canDelete": False,
            "canManagePermissions": False
        }

    async def get_knowledge_hub_node_info(
        self,
        node_id: str,
        folder_mime_types: List[str],
        transaction: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get node information including type and subtype."""
        start = time.perf_counter()
        query = """
        LET record = DOCUMENT("records", @node_id)
        LET rg = record == null ? DOCUMENT("recordGroups", @node_id) : null
        LET app = record == null AND rg == null ? DOCUMENT("apps", @node_id) : null

        LET result = record != null AND record._key != null AND record.recordName != null ? {
            id: record._key,
            name: record.recordName,
            nodeType: record.mimeType IN @folder_mime_types ? "folder" : "record",
            subType: record.recordType
        } : (rg != null AND rg._key != null AND rg.groupName != null ? {
            id: rg._key,
            name: rg.groupName,
            nodeType: rg.connectorName == "KB" ? "kb" : "recordGroup",
            subType: rg.connectorName == "KB" ? "KB" : (rg.groupType || rg.connectorName)
        } : (app != null AND app._key != null AND app.name != null ? {
            id: app._key,
            name: app.name,
            nodeType: "app",
            subType: app.type
        } : null))

        RETURN result
        """
        results = await self.http_client.execute_aql(query, bind_vars={"node_id": node_id, "folder_mime_types": folder_mime_types}, txn_id=transaction)
        elapsed = time.perf_counter() - start
        self.logger.info(f"get_knowledge_hub_node_info finished in {elapsed * 1000} ms")
        return results[0] if results and results[0] else None

    async def get_knowledge_hub_parent_node(
        self,
        node_id: str,
        folder_mime_types: List[str],
        transaction: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get the parent node of a given node in a single query."""
        start = time.perf_counter()
        query = """
        LET record = DOCUMENT("records", @node_id)
        LET rg = record == null ? DOCUMENT("recordGroups", @node_id) : null
        LET app = record == null AND rg == null ? DOCUMENT("apps", @node_id) : null

        // Determine if record is KB record
        LET record_connector_doc = record != null ? (DOCUMENT(CONCAT("recordGroups/", record.connectorId)) || DOCUMENT(CONCAT("apps/", record.connectorId))) : null
        LET is_kb_record = record != null AND ((record.connectorName == "KB") OR (record_connector_doc != null AND record_connector_doc.type == "KB"))

        // Apps have no parent
        LET parent_id = app != null ? null : (
            rg != null ? (
                // For KB record groups: check belongsTo edge to find parent (could be another KB record group or KB app)
                rg.connectorName == "KB" ? FIRST(
                    FOR edge IN belongsTo
                        FILTER edge._from == rg._id
                        LET parent_doc = DOCUMENT(edge._to)
                        FILTER parent_doc != null
                        // If parent is KB app, return null (KB apps shouldn't be shown)
                        // If parent is another KB record group, return its key
                        RETURN parent_doc.type == "KB" ? null : PARSE_IDENTIFIER(edge._to).key
                ) : (
                    // For connector record groups: use parentId or connectorId (app)
                    rg.parentId != null ? rg.parentId : rg.connectorId
                )
            ) : (
                // Records: For KB records, check recordRelations first (to find parent folder/record for nested items),
                // then fallback to belongsTo (to find parent KB record group for immediate children)
                // For connector records, check recordRelations edge first
                record != null ? (
                    is_kb_record ? (
                        // First check recordRelations for nested folders/records
                        FIRST(
                            FOR edge IN recordRelations
                                FILTER edge._to == record._id AND edge.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                                RETURN PARSE_IDENTIFIER(edge._from).key
                        ) ||
                        // Fallback to belongsTo for immediate children of KB record group
                        FIRST(
                            FOR edge IN belongsTo
                                FILTER edge._from == record._id
                                LET parent_doc = DOCUMENT(edge._to)
                                FILTER parent_doc != null AND IS_SAME_COLLECTION("recordGroups", parent_doc)
                                RETURN PARSE_IDENTIFIER(edge._to).key
                        )
                    ) : (
                        // For connector records, check recordRelations first (for nested folders/records),
                        // then belongsTo (for immediate children of record groups),
                        // then inheritPermissions (alternative way records can be connected to record groups)
                        LET parent_from_rel = FIRST(
                            FOR edge IN recordRelations
                                FILTER edge._to == record._id AND edge.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                                LET parent_record = DOCUMENT(edge._from)
                                // Ensure the parent is actually a record (folder), not a record group
                                FILTER parent_record != null AND IS_SAME_COLLECTION("records", parent_record)
                                RETURN PARSE_IDENTIFIER(edge._from).key
                        )
                        LET parent_from_belongs = parent_from_rel == null ? FIRST(
                            FOR edge IN belongsTo
                                FILTER edge._from == record._id
                                LET parent_doc = DOCUMENT(edge._to)
                                FILTER parent_doc != null
                                // Check if parent is a recordGroup OR a record (for projects/folders)
                                FILTER IS_SAME_COLLECTION("recordGroups", parent_doc) OR IS_SAME_COLLECTION("records", parent_doc)
                                RETURN PARSE_IDENTIFIER(edge._to).key
                        ) : null
                        LET parent_from_inherit = (parent_from_rel == null AND parent_from_belongs == null) ? FIRST(
                            FOR edge IN inheritPermissions
                                FILTER edge._from == record._id
                                LET parent_doc = DOCUMENT(edge._to)
                                // Ensure it's pointing to a record group, not another record
                                FILTER parent_doc != null AND IS_SAME_COLLECTION("recordGroups", parent_doc)
                                RETURN PARSE_IDENTIFIER(edge._to).key
                        ) : null
                        RETURN parent_from_rel || parent_from_belongs || parent_from_inherit
                    )
                ) : null
            )
        )

        // No fallback needed - all cases are handled above
        LET final_parent_id = parent_id

        // Now get full parent info in the same query
        LET parent_record = final_parent_id != null ? DOCUMENT("records", final_parent_id) : null
        LET parent_rg = parent_record == null AND final_parent_id != null ? DOCUMENT("recordGroups", final_parent_id) : null
        LET parent_app = parent_record == null AND parent_rg == null AND final_parent_id != null ? DOCUMENT("apps", final_parent_id) : null

        LET parent_info = parent_record != null AND parent_record._key != null AND parent_record.recordName != null ? {
            id: parent_record._key,
            name: parent_record.recordName,
            nodeType: parent_record.mimeType IN @folder_mime_types ? "folder" : "record",
            subType: parent_record.recordType
        } : (parent_rg != null AND parent_rg._key != null AND parent_rg.groupName != null ? {
            id: parent_rg._key,
            name: parent_rg.groupName,
            nodeType: parent_rg.connectorName == "KB" ? "kb" : "recordGroup",
            subType: parent_rg.connectorName == "KB" ? "KB" : (parent_rg.groupType || parent_rg.connectorName)
        } : (parent_app != null AND parent_app._key != null AND parent_app.name != null ? {
            id: parent_app._key,
            name: parent_app.name,
            nodeType: "app",
            subType: parent_app.type
        } : null))

        RETURN parent_info
        """
        results = await self.http_client.execute_aql(
            query, bind_vars={"node_id": node_id, "folder_mime_types": folder_mime_types}, txn_id=transaction
        )
        elapsed = time.perf_counter() - start
        self.logger.info(f"get_knowledge_hub_parent_node finished in {elapsed * 1000} ms")
        return results[0] if results and results[0] else None

    async def get_knowledge_hub_filter_options(
        self,
        user_key: str,
        org_id: str,
        transaction: Optional[str] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get available filter options (KBs and Apps) for a user.
        Returns only KBs and Connectors that the user has access to.
        """
        self.logger.info(f"🔍 Getting filter options for user_key={user_key}, org_id={org_id}")
        start = time.perf_counter()
        query = """
        // Get KBs the user has access to (via direct or team/group permissions)
        LET user_from = CONCAT("users/", @user_key)

        // Direct KB permissions
        LET direct_kb_perms = (
            FOR perm IN permission
                FILTER perm._from == user_from
                FILTER perm.type == "USER"
                FILTER STARTS_WITH(perm._to, "recordGroups/")
                LET kb = DOCUMENT(perm._to)
                FILTER kb != null AND kb.isDeleted != true
                FILTER kb.groupType == "KB" AND kb.connectorName == "KB"
                FILTER kb.orgId == @org_id
                RETURN kb._key
        )

        // Team-based KB permissions
        LET team_kb_perms = (
            FOR user_team_perm IN permission
                FILTER user_team_perm._from == user_from
                FILTER user_team_perm.type == "USER"
                FILTER STARTS_WITH(user_team_perm._to, "teams/")
                FOR team_kb_perm IN permission
                    FILTER team_kb_perm._from == user_team_perm._to
                    FILTER team_kb_perm.type == "TEAM"
                    FILTER STARTS_WITH(team_kb_perm._to, "recordGroups/")
                    LET kb = DOCUMENT(team_kb_perm._to)
                    FILTER kb != null AND kb.isDeleted != true
                    FILTER kb.groupType == "KB" AND kb.connectorName == "KB"
                    FILTER kb.orgId == @org_id
                    RETURN kb._key
        )

        // Group-based KB permissions
        LET group_kb_perms = (
            FOR user_group_perm IN permission
                FILTER user_group_perm._from == user_from
                FILTER user_group_perm.type == "USER"
                FILTER STARTS_WITH(user_group_perm._to, "groups/")
                FOR group_kb_perm IN permission
                    FILTER group_kb_perm._from == user_group_perm._to
                    FILTER group_kb_perm.type == "GROUP"
                    FILTER STARTS_WITH(group_kb_perm._to, "recordGroups/")
                    LET kb = DOCUMENT(group_kb_perm._to)
                    FILTER kb != null AND kb.isDeleted != true
                    FILTER kb.groupType == "KB" AND kb.connectorName == "KB"
                    FILTER kb.orgId == @org_id
                    RETURN kb._key
        )

        // Combine and deduplicate KB IDs
        LET all_kb_ids = UNIQUE(UNION(direct_kb_perms, team_kb_perms, group_kb_perms))

        LET kbs = (
            FOR kb_id IN all_kb_ids
                LET kb = DOCUMENT("recordGroups", kb_id)
                FILTER kb != null
                RETURN { id: kb._key, name: kb.groupName }
        )

        // Get connector apps the user has access to
        // Apps don't have orgId field - they're scoped via user relationship
        LET apps = (
            FOR app IN OUTBOUND CONCAT("users/", @user_key) userAppRelation
                FILTER app != null
                RETURN { id: app._key, name: app.name, type: app.type }
        )

        RETURN { kbs: kbs, apps: apps }
        """

        try:
            results = await self.http_client.execute_aql(
                query,
                bind_vars={"user_key": user_key, "org_id": org_id},
                txn_id=transaction
            )
            elapsed = time.perf_counter() - start
            self.logger.info(f"get_knowledge_hub_filter_options finished in {elapsed * 1000} ms")
            return results[0] if results else {"kbs": [], "apps": []}
        except Exception:
            # self.logger.error(f"Failed to get filter options: {e}")
            return {"kbs": [], "apps": []}

    def _get_app_children_subquery(self, app_id: str, org_id: str, user_key: str) -> Tuple[str, Dict[str, Any]]:
        """Generate AQL sub-query to fetch RecordGroups for an App.

        Simplified unified approach:
        - Gets ALL recordGroups connected to app via belongsTo edge (KB and Connector unified)
        - Uses _get_permission_role_aql for comprehensive permission checking (all 10 paths)
        - Returns only recordGroups where user has permission
        - Includes userRole field in results
        """
        # Get the permission role AQL for recordGroup permission checking
        permission_role_aql = self._get_permission_role_aql("recordGroup", "node", "u")

        sub_query = f"""
        LET app = DOCUMENT("apps", @app_id)
        FILTER app != null

        LET u = DOCUMENT("users", @user_key)
        FILTER u != null

        // Get all recordGroups connected to app via belongsTo edge
        LET all_rgs = (
            FOR edge IN belongsTo
                FILTER edge._to == app._id
                AND STARTS_WITH(edge._from, "recordGroups/")
                AND edge.isDeleted != true
                LET rg = DOCUMENT(edge._from)
                FILTER rg != null AND rg.isDeleted != true
                RETURN rg
        )

        LET raw_children = (
            FOR node IN all_rgs
                // Calculate user's permission role on this recordGroup using helper
                {permission_role_aql}

                // Normalize permission_role: handle both array and string cases
                LET normalized_role = IS_ARRAY(permission_role)
                    ? (LENGTH(permission_role) > 0 ? permission_role[0] : null)
                    : permission_role

                // Only include recordGroups where user has permission
                FILTER normalized_role != null AND normalized_role != ""

                // Check if recordGroup has children for hasChildren flag
                LET has_child_rgs = (LENGTH(
                    FOR edge IN belongsTo
                        FILTER edge._to == node._id
                        AND STARTS_WITH(edge._from, "recordGroups/")
                        AND edge.isDeleted != true
                        LIMIT 1
                        RETURN 1
                ) > 0)

                LET has_records = (LENGTH(
                    FOR edge IN belongsTo
                        FILTER edge._to == node._id
                        AND STARTS_WITH(edge._from, "records/")
                        AND edge.isDeleted != true
                        LIMIT 1
                        RETURN 1
                ) > 0)

                RETURN MERGE(node, {{
                    id: node._key,
                    name: node.groupName,
                    nodeType: "recordGroup",
                    parentId: CONCAT("apps/", @app_id),
                    origin: node.connectorName == "KB" ? "KB" : "CONNECTOR",
                    connector: node.connectorName,
                    recordType: null,
                    recordGroupType: node.groupType,
                    indexingStatus: null,
                    createdAt: node.sourceCreatedAtTimestamp != null ? node.sourceCreatedAtTimestamp : 0,
                    updatedAt: node.sourceLastModifiedTimestamp != null ? node.sourceLastModifiedTimestamp : 0,
                    sizeInBytes: null,
                    mimeType: null,
                    extension: null,
                    webUrl: node.webUrl,
                    hasChildren: has_child_rgs OR has_records,
                    userRole: normalized_role
                }})
        )
        """
        return sub_query, {"app_id": app_id, "user_key": user_key}

    async def _get_record_group_children_split(
        self,
        parent_id: str,
        user_key: str,
        skip: int,
        limit: int,
        sort_field: str,
        sort_dir: str,
        only_containers: bool,
        transaction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get children of a recordGroup by executing separate queries for child recordGroups
        and direct records, then combining results in Python.
        """
        rg_doc_id = f"recordGroups/{parent_id}"
        rg_permission_role_aql = self._get_permission_role_aql("recordGroup", "node", "u")
        record_permission_role_aql = self._get_permission_role_aql("record", "record", "u")

        child_rgs_query = f"""
        LET rg = DOCUMENT(@rg_doc_id)
        FILTER rg != null
        LET u = DOCUMENT("users", @user_key)
        FILTER u != null

        LET child_rgs = rg.isInternal == true ? [] : (
            FOR edge IN belongsTo
                FILTER edge._to == rg._id AND STARTS_WITH(edge._from, "recordGroups/")
                LET node = DOCUMENT(edge._from)
                FILTER node != null AND node.isDeleted != true

                {rg_permission_role_aql}

                LET normalized_role = IS_ARRAY(permission_role)
                    ? (LENGTH(permission_role) > 0 ? permission_role[0] : null)
                    : permission_role

                FILTER normalized_role != null AND normalized_role != ""

                LET has_child_rgs = (LENGTH(
                    FOR edge2 IN belongsTo
                        FILTER edge2._to == node._id
                        AND STARTS_WITH(edge2._from, "recordGroups/")
                        AND edge2.isDeleted != true
                        LIMIT 1
                        RETURN 1
                ) > 0)

                LET has_records = (LENGTH(
                    FOR edge2 IN belongsTo
                        FILTER edge2._to == node._id
                        AND STARTS_WITH(edge2._from, "records/")
                        AND edge2.isDeleted != true
                        LIMIT 1
                        RETURN 1
                ) > 0)

                RETURN {{
                    id: node._key,
                    name: node.groupName,
                    nodeType: "recordGroup",
                    parentId: @rg_doc_id,
                    origin: node.connectorName == "KB" ? "KB" : "CONNECTOR",
                    connector: node.connectorName,
                    connectorId: node.connectorName != "KB" ? node.connectorId : null,
                    kbId: node.connectorName == "KB" ? PARSE_IDENTIFIER(@rg_doc_id).key : null,
                    externalGroupId: node.externalGroupId,
                    recordType: null,
                    recordGroupType: node.groupType,
                    indexingStatus: null,
                    createdAt: node.sourceCreatedAtTimestamp != null ? node.sourceCreatedAtTimestamp : 0,
                    updatedAt: node.sourceLastModifiedTimestamp != null ? node.sourceLastModifiedTimestamp : 0,
                    sizeInBytes: null,
                    mimeType: null,
                    extension: null,
                    webUrl: node.webUrl,
                    hasChildren: has_child_rgs OR has_records,
                    userRole: normalized_role
                }}
        )
        RETURN child_rgs
        """
        child_rgs_result = await self.http_client.execute_aql(
            child_rgs_query,
            bind_vars={"rg_doc_id": rg_doc_id, "user_key": user_key},
            txn_id=transaction
        )
        child_rgs = child_rgs_result[0] if child_rgs_result else []

        direct_records_query = f"""
        LET rg = DOCUMENT(@rg_doc_id)
        FILTER rg != null
        LET u = DOCUMENT("users", @user_key)
        FILTER u != null

        LET direct_records = rg.isInternal == true ? [] : (
            FOR edge IN belongsTo
                FILTER edge._to == @rg_doc_id AND STARTS_WITH(edge._from, "records/")
                LET record = DOCUMENT(edge._from)
                FILTER record != null AND record.isDeleted != true
                FILTER record.externalParentId == null

                {record_permission_role_aql}

                LET normalized_role = IS_ARRAY(permission_role)
                    ? (LENGTH(permission_role) > 0 ? permission_role[0] : null)
                    : permission_role

                FILTER normalized_role != null AND normalized_role != ""

                LET file_info = FIRST(FOR fe IN isOfType FILTER fe._from == record._id LET f = DOCUMENT(fe._to) RETURN f)
                LET is_folder = file_info != null AND file_info.isFile == false
                LET has_children = (LENGTH(
                    FOR ce IN recordRelations
                        FILTER ce._from == record._id
                        AND ce.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                        AND ce.isDeleted != true
                        LIMIT 1
                        RETURN 1
                ) > 0)

                RETURN {{
                    id: record._key,
                    name: record.recordName,
                    nodeType: is_folder ? "folder" : "record",
                    parentId: @rg_doc_id,
                    origin: record.connectorName == "KB" ? "KB" : "CONNECTOR",
                    connector: record.connectorName,
                    connectorId: record.connectorName != "KB" ? record.connectorId : null,
                    kbId: record.connectorName == "KB" ? record.externalGroupId : null,
                    externalGroupId: record.externalGroupId,
                    recordType: record.recordType,
                    recordGroupType: null,
                    indexingStatus: record.indexingStatus,
                    createdAt: record.sourceCreatedAtTimestamp != null ? record.sourceCreatedAtTimestamp : 0,
                    updatedAt: record.sourceLastModifiedTimestamp != null ? record.sourceLastModifiedTimestamp : 0,
                    sizeInBytes: record.sizeInBytes != null ? record.sizeInBytes : file_info.fileSizeInBytes,
                    mimeType: record.mimeType,
                    extension: file_info.extension,
                    webUrl: record.webUrl,
                    hasChildren: has_children,
                    previewRenderable: record.previewRenderable != null ? record.previewRenderable : true,
                    userRole: normalized_role
                }}
        )
        RETURN direct_records
        """
        direct_records_result = await self.http_client.execute_aql(
            direct_records_query,
            bind_vars={"rg_doc_id": rg_doc_id, "user_key": user_key},
            txn_id=transaction
        )
        direct_records = direct_records_result[0] if direct_records_result else []

        all_children = child_rgs + direct_records

        filtered_children = [
            node for node in all_children
            if not only_containers or node.get("hasChildren") or node.get("nodeType") in ["app", "kb", "recordGroup", "folder"]
        ]

        reverse = (sort_dir == "DESC")
        sorted_children = sorted(filtered_children, key=lambda x: x.get(sort_field, ""), reverse=reverse)

        total_count = len(sorted_children)
        paginated_children = sorted_children[skip:skip + limit]

        return {"nodes": paginated_children, "total": total_count}

    def _get_record_group_children_subquery(self, rg_id: str, org_id: str, parent_type: str, user_key: str) -> Tuple[str, Dict[str, Any]]:
        """Generate AQL sub-query to fetch children of a KB or RecordGroup with permission filtering.

        Simplified unified approach:
        - Uses belongsTo edges for both KB and Connector recordGroups
        - Uses _get_permission_role_aql for comprehensive permission checking (all 10 paths)
        - Applies permission checks to both KB and Connector children
        - Returns only children where user has permission
        - Includes userRole field in results
        - Special handling for internal recordGroups (fetches all records with permission check)
        """
        rg_doc_id = f"recordGroups/{rg_id}"

        # Get the permission role AQL for recordGroups and records
        rg_permission_role_aql = self._get_permission_role_aql("recordGroup", "node", "u")
        record_permission_role_aql = self._get_permission_role_aql("record", "record", "u")


        sub_query = f"""
        LET rg = DOCUMENT(@rg_doc_id)
        FILTER rg != null

        LET u = DOCUMENT("users", @user_key)
        FILTER u != null

        // Special case: Internal recordGroups get all records with permission check
        LET internal_records = rg.isInternal == true ? (
            FOR edge IN belongsTo
                FILTER edge._to == @rg_doc_id AND STARTS_WITH(edge._from, "records/")
                LET record = DOCUMENT(edge._from)
                FILTER record != null AND record.isDeleted != true

                // Use permission role helper for records
                {record_permission_role_aql}

                // Normalize permission_role: handle both array and string cases
                LET normalized_role = IS_ARRAY(permission_role)
                    ? (LENGTH(permission_role) > 0 ? permission_role[0] : null)
                    : permission_role

                FILTER normalized_role != null AND normalized_role != ""

                // Calculate hasChildren and format record
                LET file_info = FIRST(FOR fe IN isOfType FILTER fe._from == record._id LET f = DOCUMENT(fe._to) RETURN f)
                LET is_folder = file_info != null AND file_info.isFile == false
                LET has_children = (LENGTH(
                    FOR ce IN recordRelations
                        FILTER ce._from == record._id
                        AND ce.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                        AND ce.isDeleted != true
                        LIMIT 1
                        RETURN 1
                ) > 0)

                RETURN {{
                    id: record._key,
                    name: record.recordName,
                    nodeType: is_folder ? "folder" : "record",
                    parentId: @rg_doc_id,
                    origin: record.connectorName == "KB" ? "KB" : "CONNECTOR",
                    connector: record.connectorName,
                    connectorId: record.connectorName != "KB" ? record.connectorId : null,
                    kbId: record.connectorName == "KB" ? record.externalGroupId : null,
                    externalGroupId: record.externalGroupId,
                    recordType: record.recordType,
                    recordGroupType: null,
                    indexingStatus: record.indexingStatus,
                    createdAt: record.sourceCreatedAtTimestamp != null ? record.sourceCreatedAtTimestamp : 0,
                    updatedAt: record.sourceLastModifiedTimestamp != null ? record.sourceLastModifiedTimestamp : 0,
                    sizeInBytes: record.sizeInBytes != null ? record.sizeInBytes : file_info.fileSizeInBytes,
                    mimeType: record.mimeType,
                    extension: file_info.extension,
                    webUrl: record.webUrl,
                    hasChildren: has_children,
                    previewRenderable: record.previewRenderable != null ? record.previewRenderable : true,
                    userRole: normalized_role
                }}
        ) : []

        // Normal case: Get child recordGroups with permission checks
        LET child_rgs = rg.isInternal == true ? [] : (
            FOR edge IN belongsTo
                FILTER edge._to == rg._id AND STARTS_WITH(edge._from, "recordGroups/")
                LET node = DOCUMENT(edge._from)
                FILTER node != null AND node.isDeleted != true

                // Use permission role helper for recordGroups (unified for KB and Connector)
                {rg_permission_role_aql}

                // Normalize permission_role: handle both array and string cases
                LET normalized_role = IS_ARRAY(permission_role)
                    ? (LENGTH(permission_role) > 0 ? permission_role[0] : null)
                    : permission_role

                FILTER normalized_role != null AND normalized_role != ""

                // Calculate hasChildren: check for nested recordGroups and records
                LET has_child_rgs = (LENGTH(
                    FOR edge2 IN belongsTo
                        FILTER edge2._to == node._id
                        AND STARTS_WITH(edge2._from, "recordGroups/")
                        AND edge2.isDeleted != true
                        LIMIT 1
                        RETURN 1
                ) > 0)

                LET has_records = (LENGTH(
                    FOR edge2 IN belongsTo
                        FILTER edge2._to == node._id
                        AND STARTS_WITH(edge2._from, "records/")
                        AND edge2.isDeleted != true
                        LIMIT 1
                        RETURN 1
                ) > 0)

                RETURN {{
                    id: node._key,
                    name: node.groupName,
                    nodeType: "recordGroup",
                    parentId: @rg_doc_id,
                    origin: node.connectorName == "KB" ? "KB" : "CONNECTOR",
                    connector: node.connectorName,
                    connectorId: node.connectorName != "KB" ? node.connectorId : null,
                    kbId: node.connectorName == "KB" ? PARSE_IDENTIFIER(@rg_doc_id).key : null,
                    externalGroupId: node.externalGroupId,
                    recordType: null,
                    recordGroupType: node.groupType,
                    indexingStatus: null,
                    createdAt: node.sourceCreatedAtTimestamp != null ? node.sourceCreatedAtTimestamp : 0,
                    updatedAt: node.sourceLastModifiedTimestamp != null ? node.sourceLastModifiedTimestamp : 0,
                    sizeInBytes: null,
                    mimeType: null,
                    extension: null,
                    webUrl: node.webUrl,
                    hasChildren: has_child_rgs OR has_records,
                    userRole: normalized_role
                }}
        )

        // Get direct child records with permission checks
        LET direct_records = rg.isInternal == true ? [] : (
            FOR edge IN belongsTo
                FILTER edge._to == @rg_doc_id AND STARTS_WITH(edge._from, "records/")
                LET record = DOCUMENT(edge._from)
                FILTER record != null AND record.isDeleted != true
                FILTER record.externalParentId == null  // Immediate children only

                // Use permission role helper for records
                {record_permission_role_aql}

                // Normalize permission_role: handle both array and string cases
                LET normalized_role = IS_ARRAY(permission_role)
                    ? (LENGTH(permission_role) > 0 ? permission_role[0] : null)
                    : permission_role

                FILTER normalized_role != null AND normalized_role != ""

                // Calculate hasChildren via recordRelations
                LET file_info = FIRST(FOR fe IN isOfType FILTER fe._from == record._id LET f = DOCUMENT(fe._to) RETURN f)
                LET is_folder = file_info != null AND file_info.isFile == false
                LET has_children = (LENGTH(
                    FOR ce IN recordRelations
                        FILTER ce._from == record._id
                        AND ce.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                        AND ce.isDeleted != true
                        LIMIT 1
                        RETURN 1
                ) > 0)

                RETURN {{
                    id: record._key,
                    name: record.recordName,
                    nodeType: is_folder ? "folder" : "record",
                    parentId: @rg_doc_id,
                    origin: record.connectorName == "KB" ? "KB" : "CONNECTOR",
                    connector: record.connectorName,
                    connectorId: record.connectorName != "KB" ? record.connectorId : null,
                    kbId: record.connectorName == "KB" ? record.externalGroupId : null,
                    externalGroupId: record.externalGroupId,
                    recordType: record.recordType,
                    recordGroupType: null,
                    indexingStatus: record.indexingStatus,
                    createdAt: record.sourceCreatedAtTimestamp != null ? record.sourceCreatedAtTimestamp : 0,
                    updatedAt: record.sourceLastModifiedTimestamp != null ? record.sourceLastModifiedTimestamp : 0,
                    sizeInBytes: record.sizeInBytes != null ? record.sizeInBytes : file_info.fileSizeInBytes,
                    mimeType: record.mimeType,
                    extension: file_info.extension,
                    webUrl: record.webUrl,
                    hasChildren: has_children,
                    previewRenderable: record.previewRenderable != null ? record.previewRenderable : true,
                    userRole: normalized_role
                }}
        )

        LET raw_children = rg.isInternal == true ? internal_records : UNION(child_rgs, direct_records)
        """
        return sub_query, {"rg_doc_id": rg_doc_id, "user_key": user_key}

    def _get_record_children_subquery(self, record_id: str, org_id: str, user_key: str) -> Tuple[str, Dict[str, Any]]:
        """Generate AQL sub-query to fetch children of a Folder/Record.

        Simplified unified approach:
        - Uses recordRelations edge with relationshipType filter (PARENT_CHILD, ATTACHMENT)
        - Uses _get_permission_role_aql for comprehensive permission checking (all 10 paths)
        - Applies permission checks to both KB and Connector records
        - Returns only children where user has permission
        - Includes userRole field in results
        - Simplified hasChildren calculation (no permission filtering on grandchildren)
        """
        record_doc_id = f"records/{record_id}"

        # Get the permission role AQL for records
        record_permission_role_aql = self._get_permission_role_aql("record", "record", "u")

        sub_query = f"""
        LET parent_record = DOCUMENT(@record_doc_id)
        FILTER parent_record != null

        LET u = DOCUMENT("users", @user_key)
        FILTER u != null

        LET raw_children = (
            FOR edge IN recordRelations
                FILTER edge._from == @record_doc_id
                AND edge.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                LET record = DOCUMENT(edge._to)
                FILTER record != null
                AND record.isDeleted != true
                AND record.orgId == @org_id

                // Use permission role helper for records (unified for KB and Connector)
                {record_permission_role_aql}

                // Normalize permission_role: handle both array and string cases
                LET normalized_role = IS_ARRAY(permission_role)
                    ? (LENGTH(permission_role) > 0 ? permission_role[0] : null)
                    : permission_role

                FILTER normalized_role != null AND normalized_role != ""

                // Get file info for folder detection
                LET file_info = FIRST(
                    FOR fe IN isOfType
                        FILTER fe._from == record._id
                        LET f = DOCUMENT(fe._to)
                        RETURN f
                )
                LET is_folder = file_info != null AND file_info.isFile == false

                // Simple hasChildren check (no permission filtering on grandchildren)
                LET has_children = (LENGTH(
                    FOR ce IN recordRelations
                        FILTER ce._from == record._id
                        AND ce.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                        LET c = DOCUMENT(ce._to)
                        FILTER c != null AND c.isDeleted != true
                        LIMIT 1
                        RETURN 1
                ) > 0)

                RETURN {{
                    id: record._key,
                    name: record.recordName,
                    nodeType: is_folder ? "folder" : "record",
                    parentId: @record_doc_id,
                    origin: record.connectorName == "KB" ? "KB" : "CONNECTOR",
                    connector: record.connectorName,
                    connectorId: record.connectorName != "KB" ? record.connectorId : null,
                    kbId: record.connectorName == "KB" ? record.externalGroupId : null,
                    externalGroupId: record.externalGroupId,
                    recordType: record.recordType,
                    recordGroupType: null,
                    indexingStatus: record.indexingStatus,
                    createdAt: record.sourceCreatedAtTimestamp != null ? record.sourceCreatedAtTimestamp : 0,
                    updatedAt: record.sourceLastModifiedTimestamp != null ? record.sourceLastModifiedTimestamp : 0,
                    sizeInBytes: record.sizeInBytes != null ? record.sizeInBytes : file_info.fileSizeInBytes,
                    mimeType: record.mimeType,
                    extension: file_info.extension,
                    webUrl: record.webUrl,
                    hasChildren: has_children,
                    previewRenderable: record.previewRenderable != null ? record.previewRenderable : true,
                    userRole: normalized_role
                }}
        )
        """
        return sub_query, {"record_doc_id": record_doc_id, "org_id": org_id, "user_key": user_key}

    def _build_knowledge_hub_filter_conditions(
        self,
        search_query: Optional[str] = None,
        node_types: Optional[List[str]] = None,
        record_types: Optional[List[str]] = None,
        indexing_status: Optional[List[str]] = None,
        created_at: Optional[Dict[str, Optional[int]]] = None,
        updated_at: Optional[Dict[str, Optional[int]]] = None,
        size: Optional[Dict[str, Optional[int]]] = None,
        origins: Optional[List[str]] = None,
        connector_ids: Optional[List[str]] = None,
        kb_ids: Optional[List[str]] = None,
        only_containers: bool = False,
    ) -> tuple[List[str], Dict[str, Any]]:
        """
        Build filter conditions and parameters for knowledge hub search queries.
        Translates Neo4j filter logic to AQL syntax.

        Returns:
            Tuple of (filter_conditions, filter_params)
        """
        filter_conditions = []
        filter_params = {}

        # Search query filter - will be combined with other conditions
        if search_query:
            filter_params["search_query"] = search_query.lower()

        # Node type filter
        if node_types:
            type_conditions = []
            for nt in node_types:
                if nt == "folder":
                    type_conditions.append('node.nodeType == "folder"')
                elif nt == "record":
                    type_conditions.append('node.nodeType == "record"')
                elif nt == "recordGroup":
                    type_conditions.append('node.nodeType == "recordGroup"')
                elif nt == "app":
                    type_conditions.append('node.nodeType == "app"')
                elif nt == "kb":
                    type_conditions.append('node.nodeType == "kb"')
            if type_conditions:
                filter_conditions.append(f"({' OR '.join(type_conditions)})")

        # Record-specific filters - only apply to record nodes (not folders)
        if record_types:
            filter_params["record_types"] = record_types
            filter_conditions.append('(node.nodeType == "record" AND node.recordType != null AND node.recordType IN @record_types)')

        if indexing_status:
            filter_params["indexing_status"] = indexing_status
            filter_conditions.append('(node.nodeType == "record" AND node.indexingStatus != null AND node.indexingStatus IN @indexing_status)')

        if created_at:
            if created_at.get("gte"):
                filter_params["created_at_gte"] = created_at["gte"]
                filter_conditions.append("node.createdAt >= @created_at_gte")
            if created_at.get("lte"):
                filter_params["created_at_lte"] = created_at["lte"]
                filter_conditions.append("node.createdAt <= @created_at_lte")

        if updated_at:
            if updated_at.get("gte"):
                filter_params["updated_at_gte"] = updated_at["gte"]
                filter_conditions.append("node.updatedAt >= @updated_at_gte")
            if updated_at.get("lte"):
                filter_params["updated_at_lte"] = updated_at["lte"]
                filter_conditions.append("node.updatedAt <= @updated_at_lte")

        if size:
            if size.get("gte"):
                filter_params["size_gte"] = size["gte"]
                filter_conditions.append("(node.sizeInBytes == null OR node.sizeInBytes >= @size_gte)")
            if size.get("lte"):
                filter_params["size_lte"] = size["lte"]
                filter_conditions.append("(node.sizeInBytes == null OR node.sizeInBytes <= @size_lte)")

        if origins:
            filter_params["origins"] = origins
            filter_conditions.append("node.origin IN @origins")

        if connector_ids and kb_ids:
            filter_params["connector_ids"] = connector_ids
            filter_params["kb_ids"] = kb_ids
            filter_conditions.append(
                '((node.nodeType == "app" AND node.id IN @connector_ids) OR (node.connectorId IN @connector_ids) OR '
                '(node.nodeType == "kb" AND node.id IN @kb_ids) OR (node.externalGroupId IN @kb_ids))'
            )
        elif connector_ids:
            filter_params["connector_ids"] = connector_ids
            filter_conditions.append('((node.nodeType == "app" AND node.id IN @connector_ids) OR (node.connectorId IN @connector_ids))')
        elif kb_ids:
            filter_params["kb_ids"] = kb_ids
            filter_conditions.append('((node.nodeType == "kb" AND node.id IN @kb_ids) OR (node.externalGroupId IN @kb_ids))')

        # Add search condition to filter conditions if present
        if search_query:
            filter_conditions.insert(0, "LOWER(node.name) LIKE CONCAT('%', @search_query, '%')")

        # Add only_containers filter
        if only_containers:
            filter_conditions.append("(node.hasChildren == true OR node.nodeType IN ['app', 'kb', 'recordGroup', 'folder'])")

        return filter_conditions, filter_params

    def _get_permission_role_aql(
        self,
        node_type: str,
        node_var: str = "node",
        user_var: str = "u",
    ) -> str:
        """
        Generate an AQL LET subquery that returns the user's highest permission role on a node.
        Translates Neo4j's CALL subquery pattern to AQL LET subquery.

        This function generates a reusable AQL LET block that checks all permission paths
        and returns the highest priority role for the user on the specified node.

        Args:
            node_type: Type of node - 'record', 'recordGroup', 'app', or 'kb'
            node_var: Variable name of the node in the outer query (default: 'node')
            user_var: Variable name of the user in the outer query (default: 'u')

        Returns:
            AQL LET subquery string that computes permission_role variable

        Permission model (10 paths for record/recordGroup):
            1. user-[PERMISSION]->node (direct)
            2. user-[PERMISSION]->ancestorRG (via INHERIT_PERMISSIONS chain)
            3. user-[PERMISSION]->group-[PERMISSION]->node
            4. user-[PERMISSION]->group-[PERMISSION]->ancestorRG
            5. user-[PERMISSION]->role-[PERMISSION]->node
            6. user-[PERMISSION]->role-[PERMISSION]->ancestorRG
            7. user-[PERMISSION]->team-[PERMISSION]->node
            8. user-[PERMISSION]->team-[PERMISSION]->ancestorRG
            9. user-[BELONGS_TO]->org-[PERMISSION]->node
            10. user-[BELONGS_TO]->org-[PERMISSION]->ancestorRG

        Node type specific behavior:
            - record: Checks all 10 paths (node + ancestors via INHERIT_PERMISSIONS)
            - recordGroup: Checks all paths (node + ancestors via INHERIT_PERMISSIONS)
            - kb: Same as recordGroup (KB is a root RecordGroup, no ancestors found)
            - app: Uses USER_APP_RELATION based permission (different model)

        Highest priority role wins: OWNER > ADMIN > EDITOR > WRITER > COMMENTER > READER
        """
        # Role priority map used for determining highest role
        role_priority_map = '{"OWNER": 6, "ADMIN": 5, "EDITOR": 4, "WRITER": 3, "COMMENTER": 2, "READER": 1}'

        if node_type == "record":
            return self._get_record_permission_role_aql(node_var, user_var, role_priority_map)
        elif node_type in ("recordGroup", "kb"):
            # KB is a RecordGroup at root level, same permission logic applies
            # INHERIT_PERMISSIONS query works for both - KB just won't have ancestors
            return self._get_record_group_permission_role_aql(node_var, user_var, role_priority_map)
        elif node_type == "app":
            return self._get_app_permission_role_aql(node_var, user_var, role_priority_map)
        else:
            raise ValueError(f"Unsupported node_type: {node_type}. Must be 'record', 'recordGroup', 'app', or 'kb'")

    def _get_record_permission_role_aql(
        self,
        node_var: str,
        user_var: str,
        role_priority_map: str,
    ) -> str:
        """
        Generate AQL LET subquery for Record permission role.
        Translates Neo4j's CALL subquery to AQL LET subquery.

        Implements all 10 permission paths:
        1. user-[PERMISSION]->record (direct)
        2. user-[PERMISSION]->recordGroup<-[INHERIT_PERMISSIONS*]-record
        3. user-[PERMISSION]->group-[PERMISSION]->record
        4. user-[PERMISSION]->group-[PERMISSION]->recordGroup<-[INHERIT_PERMISSIONS*]-record
        5. user-[PERMISSION]->role-[PERMISSION]->record
        6. user-[PERMISSION]->role-[PERMISSION]->recordGroup<-[INHERIT_PERMISSIONS*]-record
        7. user-[PERMISSION]->team-[PERMISSION]->record
        8. user-[PERMISSION]->team-[PERMISSION]->recordGroup<-[INHERIT_PERMISSIONS*]-record
        9. user-[BELONGS_TO]->org-[PERMISSION]->record
        10. user-[BELONGS_TO]->org-[PERMISSION]->recordGroup<-[INHERIT_PERMISSIONS*]-record

        Checks permissions on the record AND all ancestor RecordGroups via INHERIT_PERMISSIONS chain.
        Returns highest priority role across all paths.
        """
        return f"""
        LET permission_role = (
            LET role_priority = {role_priority_map}

            // All 10 Permission Paths:
            // Direct paths (1, 3, 5, 7, 9): User -> target (via direct, group, role, team, org)
            // Inherited paths (2, 4, 6, 8, 10): User -> RecordGroup (via inheritPermissions) -> target
            // Paths 2, 4, 6, 8, 10 traverse through parent RecordGroups via INHERIT_PERMISSIONS chain

            // Step 1: Get all permission targets (record + ancestor RGs via INHERIT_PERMISSIONS)
            // The record itself is a permission target
            // Plus all RecordGroups reachable via INHERIT_PERMISSIONS chain
            LET parent_rgs = (
                FOR ancestor_rg, inherit_edge, path IN 1..20 OUTBOUND {node_var}._id inheritPermissions
                    FILTER IS_SAME_COLLECTION("recordGroups", ancestor_rg)
                    RETURN ancestor_rg._id
            )

            // Build permission targets: [record] + ancestor RGs
            LET permission_targets = APPEND([{node_var}._id], parent_rgs, true)

            // Step 2: Check all 10 permission paths across all targets
            // Note: All 10 paths are covered by checking paths 1,3,5,7,9 on all targets:
            // - Path 1 on record = Path 1 (direct user permission on record)
            // - Path 1 on ancestor RG = Path 2 (user permission on RG → record via INHERIT_PERMISSIONS)
            // - Path 3 on record = Path 3 (user→group→record)
            // - Path 3 on ancestor RG = Path 4 (user→group→RG → record via INHERIT_PERMISSIONS)
            // - Path 5 on record = Path 5 (user→role→record)
            // - Path 5 on ancestor RG = Path 6 (user→role→RG → record via INHERIT_PERMISSIONS)
            // - Path 7 on record = Path 7 (user→team→record)
            // - Path 7 on ancestor RG = Path 8 (user→team→RG → record via INHERIT_PERMISSIONS)
            // - Path 9 on record = Path 9 (user→org→record)
            // - Path 9 on ancestor RG = Path 10 (user→org→RG → record via INHERIT_PERMISSIONS)
            // Direct paths (1, 3, 5, 7, 9):
            // Path 1: Direct user permission on target
            LET path1_roles = (
                FOR target_id IN permission_targets
                    FOR perm IN permission
                        FILTER perm._from == {user_var}._id
                        AND perm._to == target_id
                        AND perm.type == "USER"
                        AND perm.role != null
                        AND perm.role != ""
                        RETURN perm.role
            )

            // Path 3: User -> Group -> target (MIN of user->group and group->target roles)
            LET path3_roles = (
                FOR target_id IN permission_targets
                    FOR user_group_perm IN permission
                        FILTER user_group_perm._from == {user_var}._id
                        AND user_group_perm.type == "USER"
                        AND STARTS_WITH(user_group_perm._to, "groups/")
                        AND user_group_perm.role != null
                        AND user_group_perm.role != ""
                        FOR group_target_perm IN permission
                            FILTER group_target_perm._from == user_group_perm._to
                            AND group_target_perm._to == target_id
                            AND (group_target_perm.type == "GROUP" OR group_target_perm.type == "ROLE")
                            AND group_target_perm.role != null
                            AND group_target_perm.role != ""
                            // MIN of user->group role and group->target role
                            RETURN (role_priority[user_group_perm.role] < role_priority[group_target_perm.role])
                                ? user_group_perm.role
                                : group_target_perm.role
            )

            // Path 5: User -> Role -> target (MIN of user->role and role->target roles)
            LET path5_roles = (
                FOR target_id IN permission_targets
                    FOR user_role_perm IN permission
                        FILTER user_role_perm._from == {user_var}._id
                        AND user_role_perm.type == "USER"
                        AND STARTS_WITH(user_role_perm._to, "roles/")
                        AND user_role_perm.role != null
                        AND user_role_perm.role != ""
                        FOR role_target_perm IN permission
                            FILTER role_target_perm._from == user_role_perm._to
                            AND role_target_perm._to == target_id
                            AND role_target_perm.type == "ROLE"
                            AND role_target_perm.role != null
                            AND role_target_perm.role != ""
                            // MIN of user->role and role->target roles
                            RETURN (role_priority[user_role_perm.role] < role_priority[role_target_perm.role])
                                ? user_role_perm.role
                                : role_target_perm.role
            )

            // Path 7: User -> Team -> target (uses user->team role only)
            LET path7_roles = (
                FOR target_id IN permission_targets
                    FOR user_team_perm IN permission
                        FILTER user_team_perm._from == {user_var}._id
                        AND user_team_perm.type == "USER"
                        AND STARTS_WITH(user_team_perm._to, "teams/")
                        AND user_team_perm.role != null
                        AND user_team_perm.role != ""
                        FOR team_target_perm IN permission
                            FILTER team_target_perm._from == user_team_perm._to
                            AND team_target_perm._to == target_id
                            AND team_target_perm.type == "TEAM"
                            RETURN user_team_perm.role
            )

            // Path 9: User -> Org -> target (direct org permission)
            LET path9_roles = (
                FOR target_id IN permission_targets
                    FOR belongs_edge IN belongsTo
                        FILTER belongs_edge._from == {user_var}._id
                        AND belongs_edge.entityType == "ORGANIZATION"
                        LET org_id = belongs_edge._to
                        FOR org_target_perm IN permission
                            FILTER org_target_perm._from == org_id
                            AND org_target_perm._to == target_id
                            AND org_target_perm.type == "ORG"
                            AND org_target_perm.role != null
                            AND org_target_perm.role != ""
                            RETURN org_target_perm.role
            )

            // Combine all roles from all paths
            LET all_roles = UNION(
                NOT_NULL(path1_roles, []),
                NOT_NULL(path3_roles, []),
                NOT_NULL(path5_roles, []),
                NOT_NULL(path7_roles, []),
                NOT_NULL(path9_roles, [])
            )

            // Get the MAX priority role (highest permission)
            RETURN (LENGTH(all_roles) > 0)
                ? FIRST(
                    FOR r IN all_roles
                        FILTER r != null AND r != ""
                        FILTER role_priority[r] != null
                        SORT role_priority[r] DESC
                        LIMIT 1
                        RETURN r
                )
                : null
        )
        """

    def _get_record_group_permission_role_aql(
        self,
        node_var: str,
        user_var: str,
        role_priority_map: str,
    ) -> str:
        """
        Generate AQL LET subquery for RecordGroup/KB permission role.
        Translates Neo4j's CALL subquery to AQL LET subquery.

        Used for both RecordGroup and KB (KB is a RecordGroup at root level).
        Checks permissions on the node itself AND all ancestor RecordGroups
        via INHERIT_PERMISSIONS chain. For KB, no ancestors will be found (as expected).

        Permission paths checked (applied to node and ancestors):
        1. user-[PERMISSION]->node (direct)
        2. user-[PERMISSION]->ancestorRG (via INHERIT_PERMISSIONS chain)
        3. user-[PERMISSION]->group-[PERMISSION]->node/ancestorRG
        4. user-[PERMISSION]->role-[PERMISSION]->node/ancestorRG
        5. user-[PERMISSION]->team-[PERMISSION]->node/ancestorRG
        6. user-[BELONGS_TO]->org-[PERMISSION]->node/ancestorRG

        Returns highest priority role across all paths.
        """
        return f"""
        LET permission_role = (
            LET role_priority = {role_priority_map}

            // Step 1: Get all permission targets (this RG + ancestor RGs via INHERIT_PERMISSIONS)
            LET parent_rgs = (
                FOR ancestor_rg, inherit_edge, path IN 1..20 OUTBOUND {node_var}._id inheritPermissions
                    FILTER IS_SAME_COLLECTION("recordGroups", ancestor_rg)
                    RETURN ancestor_rg._id
            )

            // Build permission targets: [this RG] + ancestor RGs
            LET permission_targets = APPEND([{node_var}._id], parent_rgs, true)

            // Step 2: Check all permission paths across all targets
            // Direct paths (1, 2, 3, 4, 5):
            // Path 1: Direct user permission on target
            LET path1_roles = (
                FOR target_id IN permission_targets
                    FOR perm IN permission
                        FILTER perm._from == {user_var}._id
                        AND perm._to == target_id
                        AND perm.type == "USER"
                        AND perm.role != null
                        AND perm.role != ""
                        RETURN perm.role
            )

            // Path 2: User -> Group -> target (MIN of user->group and group->target roles)
            LET path2_roles = (
                FOR target_id IN permission_targets
                    FOR user_group_perm IN permission
                        FILTER user_group_perm._from == {user_var}._id
                        AND user_group_perm.type == "USER"
                        AND STARTS_WITH(user_group_perm._to, "groups/")
                        AND user_group_perm.role != null
                        AND user_group_perm.role != ""
                        FOR group_target_perm IN permission
                            FILTER group_target_perm._from == user_group_perm._to
                            AND group_target_perm._to == target_id
                            AND (group_target_perm.type == "GROUP" OR group_target_perm.type == "ROLE")
                            AND group_target_perm.role != null
                            AND group_target_perm.role != ""
                            // MIN of user->group and group->target roles
                            RETURN (role_priority[user_group_perm.role] < role_priority[group_target_perm.role])
                                ? user_group_perm.role
                                : group_target_perm.role
            )

            // Path 3: User -> Role -> target
            LET path3_roles = (
                FOR target_id IN permission_targets
                    FOR user_role_perm IN permission
                        FILTER user_role_perm._from == {user_var}._id
                        AND user_role_perm.type == "USER"
                        AND STARTS_WITH(user_role_perm._to, "roles/")
                        AND user_role_perm.role != null
                        AND user_role_perm.role != ""
                        FOR role_target_perm IN permission
                            FILTER role_target_perm._from == user_role_perm._to
                            AND role_target_perm._to == target_id
                            AND role_target_perm.type == "ROLE"
                            AND role_target_perm.role != null
                            AND role_target_perm.role != ""
                            // MIN of user->role and role->target roles
                            RETURN (role_priority[user_role_perm.role] < role_priority[role_target_perm.role])
                                ? user_role_perm.role
                                : role_target_perm.role
            )

            // Path 4: User -> Team -> target (MIN of user->team and team->target roles)
            LET path4_roles = (
                FOR target_id IN permission_targets
                    FOR user_team_perm IN permission
                        FILTER user_team_perm._from == {user_var}._id
                        AND user_team_perm.type == "USER"
                        AND STARTS_WITH(user_team_perm._to, "teams/")
                        AND user_team_perm.role != null
                        AND user_team_perm.role != ""
                        FOR team_target_perm IN permission
                            FILTER team_target_perm._from == user_team_perm._to
                            AND team_target_perm._to == target_id
                            AND team_target_perm.type == "TEAM"
                            AND team_target_perm.role != null
                            AND team_target_perm.role != ""
                            // MIN of user->team and team->target roles
                            RETURN (role_priority[user_team_perm.role] < role_priority[team_target_perm.role])
                                ? user_team_perm.role
                                : team_target_perm.role
            )

            // Path 5: User -> Org -> target
            LET path5_roles = (
                FOR target_id IN permission_targets
                    FOR belongs_edge IN belongsTo
                        FILTER belongs_edge._from == {user_var}._id
                        AND belongs_edge.entityType == "ORGANIZATION"
                        LET org_id = belongs_edge._to
                        FOR org_target_perm IN permission
                            FILTER org_target_perm._from == org_id
                            AND org_target_perm._to == target_id
                            AND org_target_perm.type == "ORG"
                            AND org_target_perm.role != null
                            AND org_target_perm.role != ""
                            RETURN org_target_perm.role
            )

            // Combine all roles from all paths
            LET all_roles = UNION(
                NOT_NULL(path1_roles, []),
                NOT_NULL(path2_roles, []),
                NOT_NULL(path3_roles, []),
                NOT_NULL(path4_roles, []),
                NOT_NULL(path5_roles, [])
            )

            // Get the MAX priority role (highest permission)
            RETURN (LENGTH(all_roles) > 0)
                ? FIRST(
                    FOR r IN all_roles
                        FILTER r != null AND r != ""
                        FILTER role_priority[r] != null
                        SORT role_priority[r] DESC
                        LIMIT 1
                        RETURN r
                )
                : null
        )
        """

    def _get_app_permission_role_aql(
        self,
        node_var: str,
        user_var: str,
        role_priority_map: str,
    ) -> str:
        """
        Generate AQL LET subquery for App permission role.
        Translates Neo4j's CALL subquery to AQL LET subquery.

        - Checks USER_APP_RELATION edge
        - If USER_APP_RELATION exists:
        - Admin users:
            - Team apps: EDITOR role
            - Personal apps: OWNER role
        - Team app creator: OWNER role (createdBy matches userId - MongoDB ID)
        - Otherwise: READER role
        - If USER_APP_RELATION doesn't exist: returns null (no access)

        Note: createdBy stores MongoDB userId, so we compare with user.userId, not user.id
        """
        return f"""
        LET permission_role = (
            // Check if user has USER_APP_RELATION to app
            LET user_app_rel = FIRST(
                FOR rel IN userAppRelation
                    FILTER rel._from == {user_var}._id
                    AND rel._to == {node_var}._id
                    RETURN rel
            )

            // Check if user is admin
            LET is_admin = ({user_var}.role == "ADMIN" OR {user_var}.orgRole == "ADMIN")

            // Get app scope and check if user is creator
            // createdBy stores MongoDB userId, so compare with user.userId (not user.id)
            LET app_scope = {node_var}.scope != null ? {node_var}.scope : "personal"
            LET is_creator = ({node_var}.createdBy == {user_var}.userId OR {node_var}.createdBy == {user_var}._key)

            // Determine role based on conditions
            RETURN CASE
                // If no USER_APP_RELATION, no access
                WHEN user_app_rel == null THEN null
                // Admin users: Team apps get EDITOR, Personal apps get OWNER
                WHEN is_admin == true AND app_scope == "team" THEN "EDITOR"
                WHEN is_admin == true AND app_scope == "personal" THEN "OWNER"
                // Team app creator gets OWNER
                WHEN app_scope == "team" AND is_creator == true THEN "OWNER"
                // Otherwise READER
                ELSE "READER"
            END
        )
        """

    def _build_scope_filters(
        self,
        parent_id: Optional[str],
        parent_type: Optional[str],
        parent_connector_id: Optional[str] = None
    ) -> tuple[str, str, str, str]:
        """
        Build scope filter clauses for recordGroups and records.

        Returns:
            (scope_filter_rg, scope_filter_record, scope_filter_rg_inline, scope_filter_record_inline)

            The "inline" versions are boolean expressions (not FILTER statements)
            used inside FILTER conditions with OR logic.
        """
        if not parent_id or not parent_type:
            # Global search - no scope filter
            return ("", "", "true", "true")

        if parent_type == "app":
            # Filter by connectorId
            return (
                "FILTER rg.connectorId == @parent_id",
                "FILTER record.connectorId == @parent_id",
                "inherited_node.connectorId == @parent_id",
                "inherited_node.connectorId == @parent_id"
            )
        elif parent_type in ("kb", "recordGroup"):
            # Filter by parent relationship
            # RecordGroups: match if parent is this recordGroup
            # Records: match if belong to this recordGroup OR nested within
            return (
                "FILTER (rg.parentId == @parent_id OR rg._key == @parent_id)",
                """FILTER (
                record.connectorId == @parent_id
                OR LENGTH(
                    FOR ip IN inheritPermissions
                        FILTER ip._from == record._id
                        FILTER ip._to == CONCAT('recordGroups/', @parent_id)
                        RETURN 1
                ) > 0
            )""",
                "(inherited_node.parentId == @parent_id OR inherited_node._key == @parent_id)",
                """(
                inherited_node.connectorId == @parent_id
                OR LENGTH(
                    FOR ip IN inheritPermissions
                        FILTER ip._from == inherited_node._id
                        FILTER ip._to == CONCAT('recordGroups/', @parent_id)
                        RETURN 1
                ) > 0
            )"""
            )
        elif parent_type in ("record", "folder"):
            # For record parents, scope by same connector then filter in post-processing
            return (
                "FILTER rg.connectorId == @parent_connector_id",
                "FILTER record.connectorId == @parent_connector_id",
                "inherited_node.connectorId == @parent_connector_id",
                "inherited_node.connectorId == @parent_connector_id"
            )
        else:
            return ("", "", "true", "true")

    def _build_children_intersection_aql(
        self,
        parent_id: str,
        parent_type: str,
    ) -> str:
        """
        Build AQL to traverse children from parent and intersect with accessible nodes.

        This ensures scoped search only returns nodes that are:
        1. Within the parent's hierarchy (via belongsTo or recordRelations)
        2. Accessible to the user (from permission traversal)

        Returns:
            AQL string that produces:
            - final_accessible_rgs: Intersection of accessible_rgs and parent's descendant recordGroups
            - final_accessible_records: Intersection of accessible_records and parent's descendant records
        """
        if parent_type in ("kb", "recordGroup"):
            return """
        // Traverse children of recordGroup/kb parent via belongsTo edge
        LET parent_rg = DOCUMENT(@parent_doc_id)

        LET parent_descendant_rg_ids = parent_rg != null ? (
            FOR v, e, p IN 1..100 INBOUND parent_rg._id belongsTo
                FILTER IS_SAME_COLLECTION("recordGroups", v)
                FILTER v != null AND v.isDeleted != true
                RETURN v._id
        ) : []

        LET parent_descendant_record_ids = parent_rg != null ? (
            FOR rg_id IN APPEND([parent_rg._id], parent_descendant_rg_ids)
                FOR v, e IN 1..1 INBOUND rg_id belongsTo
                    FILTER IS_SAME_COLLECTION("records", v)
                    FILTER v != null AND v.isDeleted != true
                    RETURN v._id
        ) : []

        // Intersect with accessible nodes
        LET final_accessible_rgs = (
            FOR rg IN accessible_rgs
                FILTER rg._id IN parent_descendant_rg_ids
                RETURN rg
        )

        LET final_accessible_records = (
            FOR record IN accessible_records
                FILTER record._id IN parent_descendant_record_ids
                RETURN record
        )
        """
        elif parent_type in ("record", "folder"):
            return """
        // Traverse children of record/folder parent via recordRelations edge
        LET parent_record = DOCUMENT(@parent_doc_id)

        LET parent_descendant_record_ids = parent_record != null ? (
            FOR v, e, p IN 1..100 OUTBOUND parent_record._id recordRelations
                FILTER e.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                FILTER IS_SAME_COLLECTION("records", v)
                FILTER v != null AND v.isDeleted != true
                RETURN v._id
        ) : []

        // No recordGroups for record/folder parents
        LET final_accessible_rgs = []

        // Intersect records with accessible nodes
        LET final_accessible_records = (
            FOR record IN accessible_records
                FILTER record._id IN parent_descendant_record_ids
                RETURN record
        )
        """
        else:
            return """
        LET final_accessible_rgs = accessible_rgs
        LET final_accessible_records = accessible_records
        """

    # ========================================================================
    # Move Record API Methods
    # ========================================================================

    async def is_record_descendant_of(
        self,
        ancestor_id: str,
        potential_descendant_id: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Check if potential_descendant_id is a descendant of ancestor_id.
        Used to prevent circular references when moving folders.

        Args:
            ancestor_id: The folder being moved (record key)
            potential_descendant_id: The target destination (record key)
            transaction: Optional transaction ID

        Returns:
            bool: True if potential_descendant_id is under ancestor_id
        """
        query = """
        LET ancestor_doc_id = CONCAT("records/", @ancestor_id)

        // Traverse down from ancestor to find if descendant is reachable
        FOR v IN 1..100 OUTBOUND ancestor_doc_id @@record_relations
            OPTIONS { bfs: true, uniqueVertices: "global" }
            FILTER v._key == @descendant_id
            LIMIT 1
            RETURN 1
        """
        try:
            result = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "ancestor_id": ancestor_id,
                    "descendant_id": potential_descendant_id,
                    "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                },
                txn_id=transaction
            )
            is_descendant = len(result) > 0 if result else False
            self.logger.debug(
                f"Circular reference check: {potential_descendant_id} is "
                f"{'a descendant' if is_descendant else 'not a descendant'} of {ancestor_id}"
            )
            return is_descendant
        except Exception as e:
            self.logger.error(f"Failed to check descendant relationship: {e}")
            return False

    async def get_record_parent_info(
        self,
        record_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get the current parent information for a record.

        Args:
            record_id: The record key
            transaction: Optional transaction ID

        Returns:
            Dict with parent_id, parent_type ('record' or 'recordGroup'), or None if at root
        """
        query = """
        LET record_doc_id = CONCAT("records/", @record_id)

        // Find the incoming PARENT_CHILD or ATTACHMENT edge
        LET parent_edge = FIRST(
            FOR edge IN @@record_relations
                FILTER edge._to == record_doc_id
                FILTER edge.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                RETURN edge
        )

        LET parent_id = parent_edge != null ? PARSE_IDENTIFIER(parent_edge._from).key : null
        LET parent_collection = parent_edge != null ? PARSE_IDENTIFIER(parent_edge._from).collection : null
        LET parent_type = parent_collection == "recordGroups" ? "recordGroup" : (
            parent_collection == "records" ? "record" : null
        )

        RETURN parent_id != null ? {
            parentId: parent_id,
            parentType: parent_type,
            edgeKey: parent_edge._key
        } : null
        """
        try:
            result = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "record_id": record_id,
                    "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                },
                txn_id=transaction
            )
            return result[0] if result and result[0] else None
        except Exception as e:
            self.logger.error(f"Failed to get record parent info: {e}")
            return None

    async def delete_parent_child_edge_to_record(
        self,
        record_id: str,
        transaction: Optional[str] = None
    ) -> int:
        """
        Delete all PARENT_CHILD edges pointing to a record.

        Args:
            record_id: The record key (target of the edge)
            transaction: Optional transaction ID

        Returns:
            int: Number of edges deleted
        """
        query = """
        LET record_doc_id = CONCAT("records/", @record_id)

        FOR edge IN @@record_relations
            FILTER edge._to == record_doc_id
            FILTER edge.relationshipType == "PARENT_CHILD"
            REMOVE edge IN @@record_relations
            RETURN OLD
        """
        try:
            result = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "record_id": record_id,
                    "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                },
                txn_id=transaction
            )
            deleted_count = len(result) if result else 0
            self.logger.debug(f"Deleted {deleted_count} PARENT_CHILD edge(s) to record {record_id}")
            return deleted_count
        except Exception as e:
            self.logger.error(f"Failed to delete parent-child edge: {e}")
            if transaction:
                raise
            return 0

    async def create_parent_child_edge(
        self,
        parent_id: str,
        child_id: str,
        parent_is_kb: bool,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Create a PARENT_CHILD edge from parent to child.

        Args:
            parent_id: The parent key (folder or KB)
            child_id: The child key (record being moved)
            parent_is_kb: True if parent is a KB (recordGroups), False if folder (records)
            transaction: Optional transaction ID

        Returns:
            bool: True if edge created successfully
        """
        parent_collection = "recordGroups" if parent_is_kb else "records"
        timestamp = get_epoch_timestamp_in_ms()

        query = """
        INSERT {
            _from: CONCAT(@parent_collection, "/", @parent_id),
            _to: CONCAT("records/", @child_id),
            relationshipType: "PARENT_CHILD",
            createdAtTimestamp: @timestamp,
            updatedAtTimestamp: @timestamp
        } INTO @@record_relations
        RETURN NEW
        """
        try:
            result = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "parent_collection": parent_collection,
                    "parent_id": parent_id,
                    "child_id": child_id,
                    "timestamp": timestamp,
                    "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                },
                txn_id=transaction
            )
            success = len(result) > 0 if result else False
            if success:
                self.logger.debug(
                    f"Created PARENT_CHILD edge: {parent_collection}/{parent_id} -> records/{child_id}"
                )
            return success
        except Exception as e:
            self.logger.error(f"Failed to create parent-child edge: {e}")
            if transaction:
                raise
            return False

    async def update_record_external_parent_id(
        self,
        record_id: str,
        new_parent_id: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Update the externalParentId field of a record.

        Args:
            record_id: The record key
            new_parent_id: The new parent ID (folder ID or KB ID)
            transaction: Optional transaction ID

        Returns:
            bool: True if updated successfully
        """
        timestamp = get_epoch_timestamp_in_ms()
        query = """
        UPDATE { _key: @record_id } WITH {
            externalParentId: @new_parent_id,
            updatedAtTimestamp: @timestamp
        } IN @@records
        RETURN NEW
        """
        try:
            result = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "record_id": record_id,
                    "new_parent_id": new_parent_id,
                    "timestamp": timestamp,
                    "@records": CollectionNames.RECORDS.value,
                },
                txn_id=transaction
            )
            success = len(result) > 0 if result else False
            if success:
                self.logger.debug(f"Updated externalParentId for record {record_id} to {new_parent_id}")
            return success
        except Exception as e:
            self.logger.error(f"Failed to update record externalParentId: {e}")
            if transaction:
                raise
            return False

    async def is_record_folder(
        self,
        record_id: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Check if a record is a folder (isFile=false in FILES collection).

        Args:
            record_id: The record key
            transaction: Optional transaction ID

        Returns:
            bool: True if the record is a folder
        """
        query = """
        LET record = DOCUMENT("records", @record_id)
        FILTER record != null

        LET file_info = FIRST(
            FOR edge IN @@is_of_type
                FILTER edge._from == record._id
                LET f = DOCUMENT(edge._to)
                FILTER f != null AND f.isFile == false
                RETURN true
        )

        RETURN file_info == true
        """
        try:
            result = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "record_id": record_id,
                    "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                },
                txn_id=transaction
            )
            return result[0] if result else False
        except Exception as e:
            self.logger.error(f"Failed to check if record is folder: {e}")
            return False

    async def check_toolset_in_use(self, toolset_name: str, user_id: str, transaction: Optional[str] = None) -> List[str]:
        """
        Check if a toolset is currently in use by any active agents.

        Args:
            toolset_name: Normalized toolset name
            user_id: User ID who owns the toolset

        Returns:
            List of agent names that are using the toolset. Empty list if not in use.
        """
        try:
            # Find toolset nodes
            toolset_query = f"""
            FOR ts IN {CollectionNames.AGENT_TOOLSETS.value}
                FILTER ts.name == @name AND ts.userId == @user_id
                RETURN ts._id
            """
            toolset_ids = await self.http_client.execute_aql(toolset_query, bind_vars={
                "name": toolset_name,
                "user_id": user_id
            }, txn_id=transaction)

            if not toolset_ids:
                return []

            # Check for active agents using this toolset
            agent_query = f"""
            FOR edge IN {CollectionNames.AGENT_HAS_TOOLSET.value}
                FILTER edge._to IN @toolset_ids
                LET agent = DOCUMENT(edge._from)
                FILTER agent != null AND agent.isDeleted != true AND agent.deleted != true
                RETURN DISTINCT {{agentId: agent._id, agentName: agent.name}}
            """
            agents = await self.http_client.execute_aql(agent_query, bind_vars={"toolset_ids": toolset_ids}, txn_id=transaction)

            if agents:
                agent_names = list(set(a.get("agentName", "Unknown") for a in agents if a))
                return agent_names

            return []

        except Exception as e:
            self.logger.error(f"Failed to check toolset usage: {str(e)}")
            raise
