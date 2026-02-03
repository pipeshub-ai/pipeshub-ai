"""
Neo4j Provider Implementation

Minimal implementation of IGraphDBProvider using Neo4j for testing OneDrive connector compatibility.
Maps ArangoDB concepts (collections, _key, edges) to Neo4j concepts (labels, properties, relationships).
"""

import hashlib
import json
import os
import re
import uuid
from datetime import datetime
from logging import Logger
from typing import Any, Dict, List, Optional, Tuple, Union

from fastapi import Request

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    RECORD_TYPE_COLLECTION_MAPPING,
    CollectionNames,
    Connectors,
    DepartmentNames,
    OriginTypes,
    ProgressStatus,
    RecordTypes,
)
from app.config.constants.neo4j import (
    COLLECTION_TO_LABEL,
    Neo4jLabel,
    build_node_id,
    collection_to_label,
    edge_collection_to_relationship,
    parse_node_id,
)
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    CommentRecord,
    FileRecord,
    MailRecord,
    Record,
    RecordGroup,
    TicketRecord,
    User,
    WebpageRecord,
)
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.graph_db.neo4j.neo4j_client import Neo4jClient
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Constants
MAX_REINDEX_DEPTH = 100  # Maximum depth for reindexing records (unlimited depth is capped at this value)


class Neo4jProvider(IGraphDBProvider):
    """
    Neo4j implementation of IGraphDBProvider.

    This provider maps ArangoDB concepts to Neo4j:
    - Collections → Labels
    - _key → id property
    - Edges → Relationships
    """

    def __init__(
        self,
        logger: Logger,
        config_service: ConfigurationService,
    ) -> None:
        """
        Initialize Neo4j provider.

        Args:
            logger: Logger instance
            config_service: Configuration service for database credentials
        """
        self.logger = logger
        self.config_service = config_service
        self.client: Optional[Neo4jClient] = None

    # ==================== Connection Management ====================

    async def connect(self) -> bool:
        """
        Connect to Neo4j and initialize schema.

        Returns:
            bool: True if connection successful
        """
        try:
            self.logger.info("🚀 Connecting to Neo4j...")

            # Get Neo4j configuration from etcd, fallback to environment variables
            # try:
            #     neo4j_config = await self.config_service.get_config(
            #         config_node_constants.NEO4J.value
            #     )
            # except Exception:
            #     neo4j_config = None

            # # If config not found in etcd, read from environment variables
            # if not neo4j_config or not isinstance(neo4j_config, dict):
            #     self.logger.info("📝 Neo4j configuration not found in etcd, reading from environment variables...")
            #     neo4j_config = {
            #         "uri": os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            #         "username": os.getenv("NEO4J_USERNAME", "neo4j"),
            #         "password": os.getenv("NEO4J_PASSWORD", "neo4j"),
            #         "database": os.getenv("NEO4J_DATABASE", "neo4j"),
            #     }
            #     # Store in etcd for future use
            #     await self.config_service.set_config(
            #         config_node_constants.NEO4J.value,
            #         neo4j_config
            #     )
            #     self.logger.info("✅ Neo4j configuration loaded from environment variables and stored in etcd")

            uri = str(os.getenv("NEO4J_URI", "bolt://localhost:7687"))
            username = str(os.getenv("NEO4J_USERNAME", "neo4j"))
            password = str(os.getenv("NEO4J_PASSWORD", ""))
            database = str(os.getenv("NEO4J_DATABASE", "neo4j"))

            if not password:
                raise ValueError("Neo4j password is required (set NEO4J_PASSWORD environment variable or configure in etcd)")

            # Create client
            self.client = Neo4jClient(
                uri=uri,
                username=username,
                password=password,
                database=database,
                logger=self.logger
            )

            # Connect
            if not await self.client.connect():
                raise Exception("Failed to connect to Neo4j")

            # Initialize schema (constraints and indexes)
            # await self._initialize_schema()

            self.logger.info("✅ Neo4j provider connected successfully")

            # Populate tools collections on first startup
            # await self._populate_tools_collections()

            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to connect to Neo4j: {str(e)}")
            self.client = None
            return False

    async def disconnect(self) -> bool:
        """
        Disconnect from Neo4j.

        Returns:
            bool: True if disconnection successful
        """
        try:
            self.logger.info("🚀 Disconnecting from Neo4j...")
            if self.client:
                await self.client.disconnect()
            self.client = None
            self.logger.info("✅ Disconnected from Neo4j")
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to disconnect: {str(e)}")
            return False

    def _get_label(self, collection: str) -> str:
        """Get Neo4j label from collection name"""
        return collection_to_label(collection)

    def _get_relationship_type(self, edge_collection: str) -> str:
        """Get Neo4j relationship type from edge collection name"""
        return edge_collection_to_relationship(edge_collection)

    async def _initialize_schema(self) -> None:
        """Initialize Neo4j schema with constraints and indexes"""
        try:
            self.logger.info("🔧 Initializing Neo4j schema...")

            # Create unique constraints on 'id' property for each label
            # Neo4j requires one statement per query, so execute each separately
            constraints = [
                "CREATE CONSTRAINT record_id_unique IF NOT EXISTS FOR (n:Record) REQUIRE n.id IS UNIQUE",
                "CREATE CONSTRAINT user_id_unique IF NOT EXISTS FOR (n:User) REQUIRE n.id IS UNIQUE",
                "CREATE CONSTRAINT group_id_unique IF NOT EXISTS FOR (n:Group) REQUIRE n.id IS UNIQUE",
                "CREATE CONSTRAINT org_id_unique IF NOT EXISTS FOR (n:Organization) REQUIRE n.id IS UNIQUE",
                "CREATE CONSTRAINT app_id_unique IF NOT EXISTS FOR (n:App) REQUIRE n.id IS UNIQUE",
                "CREATE CONSTRAINT tool_id_unique IF NOT EXISTS FOR (n:Tool) REQUIRE n.id IS UNIQUE",
                "CREATE CONSTRAINT tool_ctag_id_unique IF NOT EXISTS FOR (n:ToolCtag) REQUIRE n.id IS UNIQUE",
                "CREATE CONSTRAINT department_id_unique IF NOT EXISTS FOR (n:Departments) REQUIRE n.id IS UNIQUE",
            ]

            for constraint_query in constraints:
                try:
                    await self.client.execute_query(constraint_query)
                except Exception as e:
                    # Constraint may already exist, log as warning
                    self.logger.debug(f"Constraint creation (may already exist): {str(e)}")

            # Create indexes for common queries
            # Neo4j requires one statement per query
            indexes = [
                "CREATE INDEX record_external_id IF NOT EXISTS FOR (n:Record) ON (n.externalRecordId, n.connectorId)",
                "CREATE INDEX user_email IF NOT EXISTS FOR (n:User) ON (n.email)",
                "CREATE INDEX user_user_id IF NOT EXISTS FOR (n:User) ON (n.userId)",
                "CREATE INDEX file_path IF NOT EXISTS FOR (n:File) ON (n.path)",
                "CREATE INDEX tool_app_name_tool_name IF NOT EXISTS FOR (n:Tool) ON (n.app_name, n.tool_name)",
                "CREATE INDEX tool_ctag_connector_name IF NOT EXISTS FOR (n:ToolCtag) ON (n.connector_name)",
            ]

            for index_query in indexes:
                try:
                    await self.client.execute_query(index_query)
                except Exception as e:
                    # Index may already exist, log as warning
                    self.logger.debug(f"Index creation (may already exist): {str(e)}")

            self.logger.info("✅ Neo4j schema initialized (including tools and tools_ctags)")

        except Exception as e:
            self.logger.warning(f"⚠️ Schema initialization warning (may already exist): {str(e)}")

        # Always try to initialize departments, even if schema initialization had warnings
        try:
            await self._initialize_departments()
        except Exception as e:
            self.logger.error(f"❌ Error initializing departments: {str(e)}")
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")

    async def _initialize_departments(self) -> None:
        """Initialize departments collection with predefined department types"""
        try:
            self.logger.info("🚀 Initializing departments...")

            # Get the label for departments collection
            dept_label = collection_to_label(CollectionNames.DEPARTMENTS.value)

            # Get existing department names
            existing_query = f"""
            MATCH (d:{dept_label})
            WHERE d.orgId IS NULL
            RETURN d.departmentName AS departmentName
            """

            existing_results = await self.client.execute_query(existing_query)
            existing_department_names = {
                result["departmentName"]
                for result in existing_results
                if result.get("departmentName")
            }

            # Create departments from DepartmentNames enum
            departments = [
                {
                    "id": str(uuid.uuid4()),
                    "departmentName": dept.value,
                    "orgId": None,
                }
                for dept in DepartmentNames
            ]

            # Filter out departments that already exist
            new_departments = [
                dept
                for dept in departments
                if dept["departmentName"] not in existing_department_names
            ]

            if new_departments:
                self.logger.info(f"🚀 Inserting {len(new_departments)} departments")

                # Use batch_upsert_nodes to create departments (more efficient)
                await self.batch_upsert_nodes(
                    new_departments,
                    CollectionNames.DEPARTMENTS.value
                )

                self.logger.info("✅ Departments initialized successfully")
            else:
                self.logger.info("✅ All departments already exist, skipping initialization")

        except Exception as e:
            self.logger.error(f"❌ Error initializing departments: {str(e)}")
            raise

    async def _populate_tools_collections(self) -> None:
        """Populate tools and tools_ctags collections from the tools registry"""
        try:
            # Lazy import to avoid circular dependencies
            try:
                from app.agents.tools.discovery import discover_tools
                from app.agents.tools.registry import _global_tools_registry
            except ImportError:
                self.logger.debug("Tools registry not available, skipping tools population")
                return

            # Discover and register tools
            self.logger.info("🔍 Discovering tools for Neo4j...")
            discover_tools(self.logger)

            tool_registry = _global_tools_registry
            if not tool_registry:
                self.logger.debug("No tools registry available, skipping tools population")
                return

            all_tools = tool_registry.get_all_tools()
            if not all_tools:
                self.logger.info("No tools found in registry")
                return

            self.logger.info(f"📦 Populating {len(all_tools)} tools into Neo4j...")

            tools_to_upsert = []
            ctags_to_upsert = []

            for tool in all_tools.values():
                tool_id = f"{tool.app_name}_{tool.tool_name}"

                # Generate ctag
                content = json.dumps({
                    "description": tool.description,
                    "parameters": [param.to_json_serializable_dict() for param in tool.parameters],
                    "returns": tool.returns,
                    "examples": tool.examples,
                    "tags": tool.tags
                }, sort_keys=True)
                ctag = hashlib.md5(content.encode()).hexdigest()

                # Check if tool exists
                existing_tool = await self.get_document(tool_id, "tools")

                if existing_tool and existing_tool.get("ctag") == ctag:
                    # Tool hasn't changed, skip update
                    continue

                # Prepare tool node (convert _key to id for Neo4j)
                tool_node = {
                    "id": tool_id,
                    "app_name": tool.app_name,
                    "tool_name": tool.tool_name,
                    "description": tool.description,
                    "parameters": [param.to_json_serializable_dict() for param in tool.parameters],
                    "returns": tool.returns,
                    "examples": tool.examples,
                    "tags": tool.tags,
                    "ctag": ctag,
                    "created_at": existing_tool.get("created_at") if existing_tool else datetime.utcnow().isoformat(),
                    "updated_at": datetime.utcnow().isoformat()
                }
                tools_to_upsert.append(tool_node)

                # Prepare ctag node
                ctag_node = {
                    "id": tool.app_name,
                    "connector_name": tool.app_name,
                    "ctag": ctag,
                    "last_updated": datetime.utcnow().isoformat()
                }
                ctags_to_upsert.append(ctag_node)

            # Batch upsert tools
            if tools_to_upsert:
                await self.batch_upsert_nodes(tools_to_upsert, "tools")
                self.logger.info(f"✅ Upserted {len(tools_to_upsert)} tools into Neo4j")

            # Batch upsert ctags
            if ctags_to_upsert:
                await self.batch_upsert_nodes(ctags_to_upsert, "tools_ctags")
                self.logger.info(f"✅ Upserted {len(ctags_to_upsert)} tool ctags into Neo4j")

            self.logger.info(f"✅ Successfully populated {len(all_tools)} tools into Neo4j")

        except Exception as e:
            self.logger.warning(f"⚠️ Failed to populate tools collections: {str(e)}")
            # Don't raise - tools population is not critical for provider initialization

    # ==================== Transaction Management ====================

    async def begin_transaction(self, read: List[str], write: List[str]) -> str:
        """
        Begin a Neo4j transaction.

        Args:
            read: Collections to read from (for compatibility)
            write: Collections to write to (for compatibility)

        Returns:
            str: Transaction ID
        """
        if not self.client:
            raise RuntimeError("Neo4j client not connected")

        return await self.client.begin_transaction(read, write)

    async def commit_transaction(self, transaction: str) -> None:
        """
        Commit a transaction.

        Args:
            transaction: Transaction ID
        """
        if not self.client:
            raise RuntimeError("Neo4j client not connected")

        await self.client.commit_transaction(transaction)

    async def rollback_transaction(self, transaction: str) -> None:
        """
        Rollback a transaction.

        Args:
            transaction: Transaction ID
        """
        if not self.client:
            raise RuntimeError("Neo4j client not connected")

        await self.client.abort_transaction(transaction)

    # ==================== Helper Methods ====================

    def _arango_to_neo4j_node(self, arango_node: Dict, collection: str) -> Dict:
        """
        Convert ArangoDB node format to Neo4j format.

        Args:
            arango_node: Node from ArangoDB (may have _key, _id)
            collection: Collection name

        Returns:
            Node in Neo4j format (with id, label)
        """
        neo4j_node = arango_node.copy()

        # Convert _key to id
        if "_key" in neo4j_node:
            neo4j_node["id"] = neo4j_node.pop("_key")

        # Remove _id if present (we'll reconstruct it if needed)
        neo4j_node.pop("_id", None)

        return neo4j_node

    def _neo4j_to_arango_node(self, neo4j_node: Dict, collection: str) -> Dict:
        """
        Convert Neo4j node format to ArangoDB-compatible format.

        Args:
            neo4j_node: Node from Neo4j (has id, label)
            collection: Collection name

        Returns:
            Node in ArangoDB format (with _key, _id)
        """
        arango_node = neo4j_node.copy()

        # Convert id to _key
        if "id" in arango_node:
            arango_node["_key"] = arango_node["id"]
            # Also create _id for compatibility
            arango_node["_id"] = f"{collection}/{arango_node['id']}"

        return arango_node

    def _neo4j_to_arango_edge(self, neo4j_edge: Dict, edge_collection: str) -> Dict:
        """
        Convert Neo4j relationship format to ArangoDB-compatible format.

        Args:
            neo4j_edge: Relationship from Neo4j
            edge_collection: Edge collection name

        Returns:
            Edge in ArangoDB format (with _key, _from, _to)
        """
        arango_edge = neo4j_edge.copy()

        # If we have from_id and to_id, construct _from and _to
        if "from_id" in arango_edge and "to_id" in arango_edge:
            from_collection = arango_edge.get("from_collection", "")
            to_collection = arango_edge.get("to_collection", "")
            arango_edge["_from"] = f"{from_collection}/{arango_edge['from_id']}"
            arango_edge["_to"] = f"{to_collection}/{arango_edge['to_id']}"

        # Ensure _key exists
        if "id" in arango_edge and "_key" not in arango_edge:
            arango_edge["_key"] = arango_edge["id"]

        return arango_edge

    def _parse_arango_id(self, node_id: str) -> Tuple[str, str]:
        """Parse ArangoDB node ID (collection/key) to (collection, key)"""
        return parse_node_id(node_id)

    def _build_arango_id(self, collection: str, key: str) -> str:
        """Build ArangoDB node ID from collection and key"""
        return build_node_id(collection, key)

    # ==================== Document Operations ====================

    async def get_document(
        self,
        document_key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get a document by its key from a collection.

        Args:
            document_key: Document key (id)
            collection: Collection name
            transaction: Optional transaction ID

        Returns:
            Optional[Dict]: Document data if found, None otherwise
        """
        try:
            label = collection_to_label(collection)

            query = f"""
            MATCH (n:{label} {{id: $key}})
            RETURN n
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"key": document_key},
                txn_id=transaction
            )

            if results:
                node_data = dict(results[0]["n"])
                return self._neo4j_to_arango_node(node_data, collection)

            return None

        except Exception as e:
            self.logger.error(f"❌ Get document failed: {str(e)}")
            return None

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
            label = collection_to_label(collection)

            query = f"""
            MATCH (n:{label})
            RETURN n
            """

            results = await self.client.execute_query(
                query,
                parameters={},
                txn_id=transaction
            )

            if results:
                documents = []
                for record in results:
                    node_dict = dict(record["n"])
                    documents.append(self._neo4j_to_arango_node(node_dict, collection))
                return documents

            return []

        except Exception as e:
            self.logger.error(f"❌ Get all documents failed for collection {collection}: {str(e)}")
            return []

    async def batch_upsert_nodes(
        self,
        nodes: List[Dict],
        collection: str,
        transaction: Optional[str] = None
    ) -> Optional[bool]:
        """
        Batch upsert nodes.

        Args:
            nodes: List of node documents (must have id or _key)
            collection: Collection name
            transaction: Optional transaction ID

        Returns:
            Optional[bool]: True if successful
        """
        try:
            if not nodes:
                return True

            label = collection_to_label(collection)

            # Convert nodes to Neo4j format
            neo4j_nodes = []
            for node in nodes:
                neo4j_node = self._arango_to_neo4j_node(node, collection)
                # Ensure id exists
                if "id" not in neo4j_node:
                    if "_key" in neo4j_node:
                        neo4j_node["id"] = neo4j_node.pop("_key")
                    else:
                        neo4j_node["id"] = str(uuid.uuid4())
                neo4j_nodes.append(neo4j_node)

            # Use UNWIND for batch upsert
            query = f"""
            UNWIND $nodes AS node
            MERGE (n:{label} {{id: node.id}})
            SET n += node
            RETURN n.id
            """

            await self.client.execute_query(
                query,
                parameters={"nodes": neo4j_nodes},
                txn_id=transaction
            )

            return True

        except Exception as e:
            self.logger.error(f"❌ Batch upsert nodes failed: {str(e)}")
            raise

    async def delete_nodes(
        self,
        keys: List[str],
        collection: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Delete multiple nodes by their keys.

        Args:
            keys: List of document keys
            collection: Collection name
            transaction: Optional transaction ID

        Returns:
            bool: True if successful
        """
        try:
            if not keys:
                return True

            label = collection_to_label(collection)

            query = f"""
            UNWIND $keys AS key
            MATCH (n:{label} {{id: key}})
            DETACH DELETE n
            RETURN count(n) AS deleted
            """

            results = await self.client.execute_query(
                query,
                parameters={"keys": keys},
                txn_id=transaction
            )

            deleted_count = results[0]["deleted"] if results else 0
            return deleted_count == len(keys)

        except Exception as e:
            self.logger.error(f"❌ Delete nodes failed: {str(e)}")
            raise

    async def update_node(
        self,
        key: str,
        node_updates: Dict,
        collection: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Update a single node.

        Args:
            key: Document key
            node_updates: Fields to update
            collection: Collection name
            transaction: Optional transaction ID

        Returns:
            bool: True if successful
        """
        try:
            label = collection_to_label(collection)

            # Convert updates to Neo4j format
            updates = self._arango_to_neo4j_node(node_updates, collection)

            query = f"""
            MATCH (n:{label} {{id: $key}})
            SET n += $updates
            RETURN n
            """

            results = await self.client.execute_query(
                query,
                parameters={"key": key, "updates": updates},
                txn_id=transaction
            )

            return len(results) > 0

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
        Batch create edges/relationships.

        Args:
            edges: List of edges with _from and _to fields
            collection: Edge collection name
            transaction: Optional transaction ID

        Returns:
            bool: True if successful
        """
        try:
            if not edges:
                return True

            relationship_type = edge_collection_to_relationship(collection)

            # Process edges - only generic format is supported
            edge_data = []
            for edge in edges:
                # Generic format: from_id, to_id, from_collection, to_collection
                from_key = edge.get("from_id")
                to_key = edge.get("to_id")
                from_collection = edge.get("from_collection", "")
                to_collection = edge.get("to_collection", "")

                if not from_key or not to_key or not from_collection or not to_collection:
                    self.logger.warning(f"Skipping invalid edge (missing required fields): {edge}")
                    continue

                from_label = collection_to_label(from_collection)
                to_label = collection_to_label(to_collection)

                # Extract edge properties (excluding format-specific fields)
                props = {k: v for k, v in edge.items() if k not in [
                    "from_id", "to_id", "from_collection", "to_collection"
                ]}

                edge_data.append({
                    "from_key": from_key,
                    "to_key": to_key,
                    "from_label": from_label,
                    "to_label": to_label,
                    "props": props
                })

            if not edge_data:
                return True

            # Group edges by label combination for efficient batch processing
            from collections import defaultdict
            grouped_edges = defaultdict(list)
            for edge in edge_data:
                key = (edge["from_label"], edge["to_label"])
                grouped_edges[key].append(edge)

            # Process each group separately
            for (from_label, to_label), group_edges in grouped_edges.items():
                query = f"""
                UNWIND $edges AS edge
                MATCH (from:{from_label} {{id: edge.from_key}})
                MATCH (to:{to_label} {{id: edge.to_key}})
                MERGE (from)-[r:{relationship_type}]->(to)
                SET r = edge.props
                RETURN count(r) AS created
                """

                await self.client.execute_query(
                    query,
                    parameters={"edges": group_edges},
                    txn_id=transaction
                )

            return True

        except Exception as e:
            self.logger.error(f"❌ Batch create edges failed: {str(e)}")
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
        Get an edge between two nodes.

        Args:
            from_id: Source node ID
            from_collection: Source node collection name
            to_id: Target node ID
            to_collection: Target node collection name
            collection: Edge collection name
            transaction: Optional transaction ID

        Returns:
            Optional[Dict]: Edge data in generic format if found, None otherwise
        """
        try:
            relationship_type = edge_collection_to_relationship(collection)

            from_label = collection_to_label(from_collection)
            to_label = collection_to_label(to_collection)

            query = f"""
            MATCH (from:{from_label} {{id: $from_id}})-[r:{relationship_type}]->(to:{to_label} {{id: $to_id}})
            RETURN properties(r) AS r
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"from_id": from_id, "to_id": to_id},
                txn_id=transaction
            )

            if results:
                # properties(r) returns a dict directly, so we can use it as-is
                edge_data = results[0].get("r", {})
                if not isinstance(edge_data, dict):
                    edge_data = {}
                # Return in generic format
                edge_data["from_id"] = from_id
                edge_data["from_collection"] = from_collection
                edge_data["to_id"] = to_id
                edge_data["to_collection"] = to_collection
                return edge_data

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
        """Delete an edge"""
        try:
            relationship_type = edge_collection_to_relationship(collection)
            from_label = collection_to_label(from_collection)
            to_label = collection_to_label(to_collection)

            query = f"""
            MATCH (from:{from_label} {{id: $from_id}})-[r:{relationship_type}]->(to:{to_label} {{id: $to_id}})
            DELETE r
            RETURN count(r) AS deleted
            """

            results = await self.client.execute_query(
                query,
                parameters={"from_id": from_id, "to_id": to_id},
                txn_id=transaction
            )

            return results[0]["deleted"] > 0 if results else False

        except Exception as e:
            self.logger.error(f"❌ Delete edge failed: {str(e)}")
            raise

    async def delete_edges_from(
        self,
        from_id: str,
        from_collection: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> int:
        """Delete all edges from a node"""
        try:
            relationship_type = edge_collection_to_relationship(collection)
            from_label = collection_to_label(from_collection)

            query = f"""
            MATCH (from:{from_label} {{id: $from_id}})-[r:{relationship_type}]->()
            DELETE r
            RETURN count(r) AS deleted
            """

            results = await self.client.execute_query(
                query,
                parameters={"from_id": from_id},
                txn_id=transaction
            )

            return results[0]["deleted"] if results else 0

        except Exception as e:
            self.logger.error(f"❌ Delete edges from failed: {str(e)}")
            raise

    async def delete_edges_to(
        self,
        to_id: str,
        to_collection: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> int:
        """Delete all edges to a node"""
        try:
            relationship_type = edge_collection_to_relationship(collection)
            to_label = collection_to_label(to_collection)

            query = f"""
            MATCH ()-[r:{relationship_type}]->(to:{to_label} {{id: $to_id}})
            DELETE r
            RETURN count(r) AS deleted
            """

            results = await self.client.execute_query(
                query,
                parameters={"to_id": to_id},
                txn_id=transaction
            )

            return results[0]["deleted"] if results else 0

        except Exception as e:
            self.logger.error(f"❌ Delete edges to failed: {str(e)}")
            raise

    async def delete_edges_to_groups(
        self,
        from_id: str,
        from_collection: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> int:
        """Delete edges from a node to group nodes"""
        try:
            relationship_type = edge_collection_to_relationship(collection)
            from_label = collection_to_label(from_collection)

            query = f"""
            MATCH (from:{from_label} {{id: $from_id}})-[r:{relationship_type}]->(to:Group)
            DELETE r
            RETURN count(r) AS deleted
            """

            results = await self.client.execute_query(
                query,
                parameters={"from_id": from_id},
                txn_id=transaction
            )

            return results[0]["deleted"] if results else 0

        except Exception as e:
            self.logger.error(f"❌ Delete edges to groups failed: {str(e)}")
            raise

    async def delete_edges_between_collections(
        self,
        from_id: str,
        from_collection: str,
        edge_collection: str,
        to_collection: str,
        transaction: Optional[str] = None
    ) -> int:
        """Delete edges between a node and nodes in a specific collection"""
        try:
            relationship_type = edge_collection_to_relationship(edge_collection)
            from_label = collection_to_label(from_collection)
            to_label = collection_to_label(to_collection)

            query = f"""
            MATCH (from:{from_label} {{id: $from_id}})-[r:{relationship_type}]->(to:{to_label})
            DELETE r
            RETURN count(r) AS deleted
            """

            results = await self.client.execute_query(
                query,
                parameters={"from_id": from_id},
                txn_id=transaction
            )

            return results[0]["deleted"] if results else 0

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
        """Delete nodes and all their connected edges"""
        try:
            if not keys:
                return

            label = collection_to_label(collection)

            query = f"""
            UNWIND $keys AS key
            MATCH (n:{label} {{id: key}})
            DETACH DELETE n
            RETURN count(n) AS deleted
            """

            await self.client.execute_query(
                query,
                parameters={"keys": keys},
                txn_id=transaction
            )

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
        """Update an edge"""
        try:
            relationship_type = edge_collection_to_relationship(collection)
            from_collection, from_id = self._parse_arango_id(from_key)
            to_collection, to_id = self._parse_arango_id(to_key)

            from_label = collection_to_label(from_collection)
            to_label = collection_to_label(to_collection)

            query = f"""
            MATCH (from:{from_label} {{id: $from_id}})-[r:{relationship_type}]->(to:{to_label} {{id: $to_id}})
            SET r += $updates
            RETURN r
            """

            results = await self.client.execute_query(
                query,
                parameters={"from_id": from_id, "to_id": to_id, "updates": edge_updates},
                txn_id=transaction
            )

            return len(results) > 0

        except Exception as e:
            self.logger.error(f"❌ Update edge failed: {str(e)}")
            raise

    # ==================== Query Operations ====================

    async def execute_query(
        self,
        query: str,
        bind_vars: Optional[Dict] = None,
        transaction: Optional[str] = None
    ) -> Optional[List[Dict]]:
        """
        Execute a Cypher query.

        Args:
            query: Cypher query string
            bind_vars: Query parameters
            transaction: Optional transaction ID

        Returns:
            Optional[List[Dict]]: Query results
        """
        try:
            results = await self.client.execute_query(
                query,
                parameters=bind_vars or {},
                txn_id=transaction
            )
            return results
        except Exception as e:
            self.logger.error(f"❌ Query execution failed: {str(e)}")
            raise

    async def get_nodes_by_filters(
        self,
        collection: str,
        filters: Dict[str, Any],
        return_fields: Optional[List[str]] = None,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """Get nodes by field filters"""
        try:
            label = collection_to_label(collection)

            # Build filter conditions
            if filters:
                filter_conditions = " AND ".join([
                    f"n.{field} = ${field}" for field in filters
                ])
                where_clause = f"WHERE {filter_conditions}"
            else:
                where_clause = ""

            # Build return clause
            if return_fields:
                return_expr = ", ".join([f"n.{field} AS {field}" for field in return_fields])
            else:
                return_expr = "n"

            if where_clause:
                query = f"""
                MATCH (n:{label})
                {where_clause}
                RETURN {return_expr}
                """
            else:
                query = f"""
                MATCH (n:{label})
                RETURN {return_expr}
                """

            results = await self.client.execute_query(
                query,
                parameters=filters,
                txn_id=transaction
            )

            # Convert results
            nodes = []
            for record in results:
                if return_fields:
                    node = {field: record.get(field) for field in return_fields}
                else:
                    node = dict(record.get("n", {}))
                nodes.append(self._neo4j_to_arango_node(node, collection))

            return nodes

        except Exception as e:
            self.logger.error(f"❌ Get nodes by filters failed: {str(e)}")
            return []

    async def get_nodes_by_field_in(
        self,
        collection: str,
        field_name: str,
        field_values: List[Any],
        return_fields: Optional[List[str]] = None,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """Get nodes where field value is in list"""
        try:
            label = collection_to_label(collection)

            if return_fields:
                return_expr = ", ".join([f"n.{field} AS {field}" for field in return_fields])
            else:
                return_expr = "n"

            query = f"""
            MATCH (n:{label})
            WHERE n.{field_name} IN $values
            RETURN {return_expr}
            """

            results = await self.client.execute_query(
                query,
                parameters={"values": field_values},
                txn_id=transaction
            )

            nodes = []
            for record in results:
                if return_fields:
                    node = {field: record.get(field) for field in return_fields}
                else:
                    node = dict(record.get("n", {}))
                nodes.append(self._neo4j_to_arango_node(node, collection))

            return nodes

        except Exception as e:
            self.logger.error(f"❌ Get nodes by field in failed: {str(e)}")
            return []

    async def remove_nodes_by_field(
        self,
        collection: str,
        field_name: str,
        field_value: Union[str, int, bool, None],
        transaction: Optional[str] = None
    ) -> int:
        """Remove nodes matching field value"""
        try:
            label = collection_to_label(collection)

            query = f"""
            MATCH (n:{label})
            WHERE n.{field_name} = $value
            DETACH DELETE n
            RETURN count(n) AS deleted
            """

            results = await self.client.execute_query(
                query,
                parameters={"value": field_value},
                txn_id=transaction
            )

            return results[0]["deleted"] if results else 0

        except Exception as e:
            self.logger.error(f"❌ Remove nodes by field failed: {str(e)}")
            raise

    async def get_edges_to_node(
        self,
        node_id: str,
        edge_collection: str,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """Get all edges pointing to a node"""
        try:
            relationship_type = edge_collection_to_relationship(edge_collection)
            collection, key = self._parse_arango_id(node_id)
            label = collection_to_label(collection)

            query = f"""
            MATCH (from)-[r:{relationship_type}]->(n:{label} {{id: $key}})
            RETURN properties(r) AS r,
                   from.id AS from_id,
                   labels(from) AS from_labels,
                   n.id AS to_id,
                   labels(n) AS to_labels
            """

            results = await self.client.execute_query(
                query,
                parameters={"key": key},
                txn_id=transaction
            )

            edges = []
            for record in results:
                # properties(r) returns a dict directly
                edge = record.get("r", {})
                if not isinstance(edge, dict):
                    edge = {}
                from_id = record.get("from_id", "")
                to_id = record.get("to_id", "")
                from_labels = record.get("from_labels", [])
                to_labels = record.get("to_labels", [])

                # Find collection name from label (reverse lookup)
                from_collection = ""
                to_collection = ""
                for coll, lbl_enum in COLLECTION_TO_LABEL.items():
                    if lbl_enum.value in from_labels:
                        from_collection = coll
                        break

                for coll, lbl_enum in COLLECTION_TO_LABEL.items():
                    if lbl_enum.value in to_labels:
                        to_collection = coll
                        break

                # Return in generic format only
                edge["from_id"] = from_id
                edge["from_collection"] = from_collection
                edge["to_id"] = to_id
                edge["to_collection"] = to_collection
                edges.append(edge)

            return edges

        except Exception as e:
            self.logger.error(f"❌ Get edges to node failed: {str(e)}")
            return []

    async def get_related_nodes(
        self,
        node_id: str,
        edge_collection: str,
        target_collection: str,
        direction: str = "inbound",
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """Get related nodes through an edge collection"""
        try:
            relationship_type = edge_collection_to_relationship(edge_collection)
            collection, key = self._parse_arango_id(node_id)
            source_label = collection_to_label(collection)
            target_label = collection_to_label(target_collection)

            if direction == "outbound":
                query = f"""
                MATCH (from:{source_label} {{id: $key}})-[r:{relationship_type}]->(to:{target_label})
                RETURN to
                """
            else:  # inbound
                query = f"""
                MATCH (from:{target_label})-[r:{relationship_type}]->(to:{source_label} {{id: $key}})
                RETURN from AS to
                """

            results = await self.client.execute_query(
                query,
                parameters={"key": key},
                txn_id=transaction
            )

            nodes = []
            for record in results:
                node = dict(record["to"])
                nodes.append(self._neo4j_to_arango_node(node, target_collection))

            return nodes

        except Exception as e:
            self.logger.error(f"❌ Get related nodes failed: {str(e)}")
            return []

    async def get_related_node_field(
        self,
        node_id: str,
        edge_collection: str,
        target_collection: str,
        field_name: str,
        direction: str = "inbound",
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """Get specific field from related nodes"""
        try:
            relationship_type = edge_collection_to_relationship(edge_collection)
            collection, key = self._parse_arango_id(node_id)
            source_label = collection_to_label(collection)
            target_label = collection_to_label(target_collection)

            if direction == "outbound":
                query = f"""
                MATCH (from:{source_label} {{id: $key}})-[r:{relationship_type}]->(to:{target_label})
                RETURN to.{field_name} AS value
                """
            else:  # inbound
                query = f"""
                MATCH (from:{target_label})-[r:{relationship_type}]->(to:{source_label} {{id: $key}})
                RETURN from.{field_name} AS value
                """

            results = await self.client.execute_query(
                query,
                parameters={"key": key},
                txn_id=transaction
            )

            return [record["value"] for record in results]

        except Exception as e:
            self.logger.error(f"❌ Get related node field failed: {str(e)}")
            return []

    # ==================== Record Operations ====================

    async def get_record_by_external_id(
        self,
        connector_id: str,
        external_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Record]:
        """Get record by external ID"""
        try:
            query = """
            MATCH (r:Record {externalRecordId: $external_id, connectorId: $connector_id})
            RETURN r
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"external_id": external_id, "connector_id": connector_id},
                txn_id=transaction
            )

            if results:
                record_dict = dict(results[0]["r"])
                record_dict = self._neo4j_to_arango_node(record_dict, CollectionNames.RECORDS.value)
                return Record.from_arango_base_record(record_dict)

            return None

        except Exception as e:
            self.logger.error(f"❌ Get record by external ID failed: {str(e)}")
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
            MATCH (r:Record {externalRecordId: $external_id, connectorId: $connector_id})
            RETURN r.id AS key
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"external_id": external_id, "connector_id": connector_id},
                txn_id=transaction
            )

            return results[0]["key"] if results else None

        except Exception as e:
            self.logger.error(f"❌ Get record key by external ID failed: {str(e)}")
            return None

    async def get_record_by_path(
        self,
        connector_id: str,
        path: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Get record by path"""
        try:
            query = """
            MATCH (f:File {path: $path})
            RETURN f
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"path": path},
                txn_id=transaction
            )

            if results:
                file_dict = dict(results[0]["f"])
                return self._neo4j_to_arango_node(file_dict, CollectionNames.FILES.value)

            return None

        except Exception as e:
            self.logger.error(f"❌ Get record by path failed: {str(e)}")
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
        """Get records by indexing status"""
        try:
            limit_clause = f"SKIP {offset} LIMIT {limit}" if limit else ""

            query = f"""
            MATCH (r:Record)
            WHERE r.orgId = $org_id
              AND r.connectorId = $connector_id
              AND r.indexingStatus IN $status_filters
            OPTIONAL MATCH (r)-[:IS_OF_TYPE]->(typeDoc)
            RETURN r, typeDoc
            ORDER BY r.id
            {limit_clause}
            """

            results = await self.client.execute_query(
                query,
                parameters={
                    "org_id": org_id,
                    "connector_id": connector_id,
                    "status_filters": status_filters
                },
                txn_id=transaction
            )

            typed_records = []
            for record in results:
                record_dict = dict(record["r"])
                record_dict = self._neo4j_to_arango_node(record_dict, CollectionNames.RECORDS.value)

                type_doc = dict(record["typeDoc"]) if record.get("typeDoc") else None
                if type_doc:
                    type_doc = self._neo4j_to_arango_node(type_doc, "")

                typed_record = self._create_typed_record_from_neo4j(record_dict, type_doc)
                typed_records.append(typed_record)

            return typed_records

        except Exception as e:
            self.logger.error(f"❌ Get records by status failed: {str(e)}")
            return []

    def _create_typed_record_from_neo4j(self, record_dict: Dict, type_doc: Optional[Dict]) -> Record:
        """Create typed Record instance from Neo4j data"""
        record_type = record_dict.get("recordType")

        if not type_doc or record_type not in RECORD_TYPE_COLLECTION_MAPPING:
            return Record.from_arango_base_record(record_dict)

        try:
            collection = RECORD_TYPE_COLLECTION_MAPPING[record_type]

            if collection == CollectionNames.FILES.value:
                return FileRecord.from_arango_record(type_doc, record_dict)
            elif collection == CollectionNames.MAILS.value:
                return MailRecord.from_arango_record(type_doc, record_dict)
            elif collection == CollectionNames.WEBPAGES.value:
                return WebpageRecord.from_arango_record(type_doc, record_dict)
            elif collection == CollectionNames.TICKETS.value:
                return TicketRecord.from_arango_record(type_doc, record_dict)
            elif collection == CollectionNames.COMMENTS.value:
                return CommentRecord.from_arango_record(type_doc, record_dict)
            else:
                return Record.from_arango_base_record(record_dict)
        except Exception as e:
            self.logger.warning(f"Failed to create typed record for {record_type}, falling back to base Record: {str(e)}")
            return Record.from_arango_base_record(record_dict)

    async def get_records_by_parent(
        self,
        connector_id: str,
        parent_external_record_id: str,
        record_type: Optional[str] = None,
        transaction: Optional[str] = None
    ) -> List[Record]:
        """Get all child records for a parent record by parent_external_record_id"""
        try:
            self.logger.debug(
                f"🚀 Retrieving child records for parent {connector_id} {parent_external_record_id} (record_type: {record_type or 'all'})"
            )

            query = """
            MATCH (record:Record)
            WHERE record.externalParentId = $parent_id
            AND record.connectorId = $connector_id
            """

            parameters = {
                "parent_id": parent_external_record_id,
                "connector_id": connector_id
            }

            if record_type:
                query += " AND record.recordType = $record_type"
                parameters["record_type"] = record_type

            query += " RETURN record"

            results = await self.client.execute_query(
                query,
                parameters=parameters,
                txn_id=transaction
            )

            records = [
                Record.from_arango_base_record(self._neo4j_to_arango_node(dict(r["record"]), CollectionNames.RECORDS.value))
                for r in results
            ]

            self.logger.info(f"✅ Retrieved {len(records)} child records for parent {parent_external_record_id}")
            return records

        except Exception as e:
            self.logger.error(f"❌ Get records by parent failed: {str(e)}")
            return []

    async def get_record_by_issue_key(
        self,
        connector_id: str,
        issue_key: str,
        transaction: Optional[str] = None
    ) -> Optional[Record]:
        """
        Get Jira issue record by issue key (e.g., PROJ-123) by searching weburl pattern.

        Args:
            connector_id: Connector ID
            issue_key: Jira issue key (e.g., "PROJ-123")
            transaction: Optional transaction ID

        Returns:
            Optional[Record]: Record if found, None otherwise
        """
        try:
            self.logger.info(
                f"🚀 Retrieving record for Jira issue key {connector_id} {issue_key}"
            )

            # Search for record where weburl contains "/browse/{issue_key}" and record_type is TICKET
            # Neo4j uses regex pattern matching with =~ operator for string contains
            browse_pattern = f"/browse/{issue_key}"
            # Escape special regex characters in the pattern
            escaped_pattern = re.escape(browse_pattern)
            browse_pattern_regex = f".*{escaped_pattern}.*"

            query = """
            MATCH (record:Record)
            WHERE record.connectorId = $connector_id
            AND record.recordType = $record_type
            AND record.webUrl IS NOT NULL
            AND record.webUrl =~ $browse_pattern_regex
            RETURN record
            LIMIT 1
            """

            parameters = {
                "connector_id": connector_id,
                "record_type": "TICKET",
                "browse_pattern_regex": browse_pattern_regex
            }

            results = await self.client.execute_query(
                query,
                parameters=parameters,
                txn_id=transaction
            )

            if results:
                self.logger.info(
                    f"✅ Successfully retrieved record for Jira issue key {connector_id} {issue_key}"
                )
                record_data = self._neo4j_to_arango_node(dict(results[0]["record"]), CollectionNames.RECORDS.value)
                return Record.from_arango_base_record(record_data)
            else:
                self.logger.warning(
                    f"⚠️ No record found for Jira issue key {connector_id} {issue_key}"
                )
                return None

        except Exception as e:
            self.logger.error(
                f"❌ Failed to retrieve record for Jira issue key {connector_id} {issue_key}: {str(e)}"
            )
            return None

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
            query = """
            MATCH (r:Record {connectorId: $connector_id, orgId: $org_id})
            MATCH (r)-[:IS_OF_TYPE]->(m:Mail {conversationIndex: $conversation_index, threadId: $thread_id})
            MATCH (u:User)-[:PERMISSION {role: 'OWNER', type: 'USER'}]->(r)
            WHERE u.userId = $user_id
            RETURN r
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={
                    "connector_id": connector_id,
                    "org_id": org_id,
                    "conversation_index": conversation_index,
                    "thread_id": thread_id,
                    "user_id": user_id
                },
                txn_id=transaction
            )

            if results:
                record_dict = dict(results[0]["r"])
                record_dict = self._neo4j_to_arango_node(record_dict, CollectionNames.RECORDS.value)
                return Record.from_arango_base_record(record_dict)

            return None

        except Exception as e:
            self.logger.error(f"❌ Get record by conversation index failed: {str(e)}")
            return None

    # ==================== Record Group Operations ====================

    async def get_record_group_by_external_id(
        self,
        connector_id: str,
        external_id: str,
        transaction: Optional[str] = None
    ) -> Optional[RecordGroup]:
        """Get record group by external ID"""
        try:
            query = """
            MATCH (rg:RecordGroup {externalGroupId: $external_id, connectorId: $connector_id})
            RETURN rg
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"external_id": external_id, "connector_id": connector_id},
                txn_id=transaction
            )

            if results:
                group_dict = dict(results[0]["rg"])
                group_dict = self._neo4j_to_arango_node(group_dict, CollectionNames.RECORD_GROUPS.value)
                return RecordGroup.from_arango_base_record_group(group_dict)

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
        return await self.get_document(id, CollectionNames.RECORD_GROUPS.value, transaction)

    async def get_file_record_by_id(
        self,
        id: str,
        transaction: Optional[str] = None
    ) -> Optional[FileRecord]:
        """Get file record by ID"""
        try:
            # Get file node
            file = await self.get_document(id, CollectionNames.FILES.value, transaction)
            if not file:
                return None

            # Get record node
            record = await self.get_document(id, CollectionNames.RECORDS.value, transaction)
            if not record:
                return None

            return FileRecord.from_arango_record(file, record)

        except Exception as e:
            self.logger.error(f"❌ Get file record by ID failed: {str(e)}")
            return None

    # ==================== User Operations ====================

    async def get_user_by_email(
        self,
        email: str,
        transaction: Optional[str] = None
    ) -> Optional[User]:
        """Get user by email"""
        try:
            query = """
            MATCH (u:User)
            WHERE toLower(u.email) = toLower($email)
            RETURN u
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"email": email},
                txn_id=transaction
            )

            if results:
                user_dict = dict(results[0]["u"])
                user_dict = self._neo4j_to_arango_node(user_dict, CollectionNames.USERS.value)
                return User.from_arango_user(user_dict)

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
        """Get user by source ID"""
        try:
            query = """
            MATCH (app:App {id: $connector_id})
            MATCH (u:User)-[r:USER_APP_RELATION {sourceUserId: $source_user_id}]->(app)
            RETURN u
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"source_user_id": source_user_id, "connector_id": connector_id},
                txn_id=transaction
            )

            if results:
                user_dict = dict(results[0]["u"])
                user_dict = self._neo4j_to_arango_node(user_dict, CollectionNames.USERS.value)
                return User.from_arango_user(user_dict)

            return None

        except Exception as e:
            self.logger.error(f"❌ Get user by source ID failed: {str(e)}")
            return None

    async def get_user_by_user_id(
        self,
        user_id: str
    ) -> Optional[Dict]:
        """Get user by user ID"""
        try:
            query = """
            MATCH (u:User {userId: $user_id})
            RETURN u
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"user_id": user_id}
            )

            if results:
                user_dict = dict(results[0]["u"])
                return self._neo4j_to_arango_node(user_dict, CollectionNames.USERS.value)

            return None

        except Exception as e:
            self.logger.error(f"❌ Get user by user ID failed: {str(e)}")
            return None

    async def get_users(
        self,
        org_id: str,
        active: bool = True
    ) -> List[Dict]:
        """Get all users in an organization"""
        try:
            query = """
            MATCH (u:User)-[:BELONGS_TO {entityType: 'ORGANIZATION'}]->(o:Organization {id: $org_id})
            WHERE $active = false OR u.isActive = true
            RETURN u
            """

            results = await self.client.execute_query(
                query,
                parameters={"org_id": org_id, "active": active}
            )

            users = []
            for record in results:
                user_dict = dict(record["u"])
                users.append(self._neo4j_to_arango_node(user_dict, CollectionNames.USERS.value))

            return users

        except Exception as e:
            self.logger.error(f"❌ Get users failed: {str(e)}")
            return []

    async def get_app_user_by_email(
        self,
        email: str,
        connector_id: str,
        transaction: Optional[str] = None
    ) -> Optional[AppUser]:
        """Get app user by email"""
        try:
            query = """
            MATCH (app:App {id: $connector_id})
            MATCH (u:User)
            WHERE toLower(u.email) = toLower($email)
            MATCH (u)-[r:USER_APP_RELATION]->(app)
            RETURN u, r.sourceUserId AS sourceUserId
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"email": email, "connector_id": connector_id},
                txn_id=transaction
            )

            if results:
                user_dict = dict(results[0]["u"])
                user_dict = self._neo4j_to_arango_node(user_dict, CollectionNames.USERS.value)
                user_dict["sourceUserId"] = results[0].get("sourceUserId")
                return AppUser.from_arango_user(user_dict)

            return None

        except Exception as e:
            self.logger.error(f"❌ Get app user by email failed: {str(e)}")
            return None

    async def get_app_users(
        self,
        org_id: str,
        connector_id: str
    ) -> List[Dict]:
        """Get all users for a connector in an organization"""
        try:
            query = """
            MATCH (app:App {id: $connector_id})
            MATCH (u:User)-[r:USER_APP_RELATION]->(app)
            MATCH (u)-[:BELONGS_TO {entityType: 'ORGANIZATION'}]->(o:Organization {id: $org_id})
            RETURN u, r.sourceUserId AS sourceUserId, app.name AS appName
            """

            results = await self.client.execute_query(
                query,
                parameters={"connector_id": connector_id, "org_id": org_id}
            )

            users = []
            for record in results:
                user_dict = dict(record["u"])
                user_dict = self._neo4j_to_arango_node(user_dict, CollectionNames.USERS.value)
                user_dict["sourceUserId"] = record.get("sourceUserId")
                user_dict["appName"] = record.get("appName", "").upper()
                users.append(user_dict)

            return users

        except Exception as e:
            self.logger.error(f"❌ Get app users failed: {str(e)}")
            return []

    # ==================== Group Operations ====================

    async def get_user_group_by_external_id(
        self,
        connector_id: str,
        external_id: str,
        transaction: Optional[str] = None
    ) -> Optional[AppUserGroup]:
        """Get user group by external ID"""
        try:
            query = """
            MATCH (g:Group {externalGroupId: $external_id, connectorId: $connector_id})
            RETURN g
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"external_id": external_id, "connector_id": connector_id},
                txn_id=transaction
            )

            if results:
                group_dict = dict(results[0]["g"])
                group_dict = self._neo4j_to_arango_node(group_dict, CollectionNames.GROUPS.value)
                return AppUserGroup.from_arango_base_user_group(group_dict)

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
        """Get all user groups for a connector"""
        try:
            query = """
            MATCH (g:Group {connectorId: $connector_id, orgId: $org_id})
            RETURN g
            """

            results = await self.client.execute_query(
                query,
                parameters={"connector_id": connector_id, "org_id": org_id},
                txn_id=transaction
            )

            groups = []
            for record in results:
                group_dict = dict(record["g"])
                group_dict = self._neo4j_to_arango_node(group_dict, CollectionNames.GROUPS.value)
                groups.append(AppUserGroup.from_arango_base_user_group(group_dict))

            return groups

        except Exception as e:
            self.logger.error(f"❌ Get user groups failed: {str(e)}")
            return []

    async def get_app_role_by_external_id(
        self,
        connector_id: str,
        external_id: str,
        transaction: Optional[str] = None
    ) -> Optional[AppRole]:
        """Get app role by external ID"""
        try:
            query = """
            MATCH (r:Role {externalRoleId: $external_id, connectorId: $connector_id})
            RETURN r
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"external_id": external_id, "connector_id": connector_id},
                txn_id=transaction
            )

            if results:
                role_dict = dict(results[0]["r"])
                role_dict = self._neo4j_to_arango_node(role_dict, CollectionNames.ROLES.value)
                return AppRole.from_arango_base_role(role_dict)

            return None

        except Exception as e:
            self.logger.error(f"❌ Get app role by external ID failed: {str(e)}")
            return None

    # ==================== Organization Operations ====================

    async def get_all_orgs(
        self,
        active: bool = True,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """Get all organizations"""
        try:
            if active:
                query = """
                MATCH (o:Organization {isActive: true})
                RETURN o
                """
            else:
                query = """
                MATCH (o:Organization)
                RETURN o
                """

            results = await self.client.execute_query(query, txn_id=transaction)

            orgs = []
            for record in results:
                org_dict = dict(record["o"])
                orgs.append(self._neo4j_to_arango_node(org_dict, CollectionNames.ORGS.value))

            return orgs

        except Exception as e:
            self.logger.error(f"❌ Get all orgs failed: {str(e)}")
            return []

    async def get_org_apps(
        self,
        org_id: str
    ) -> List[Dict]:
        """Get all apps for an organization"""
        try:
            query = """
            MATCH (o:Organization {id: $org_id})-[:ORG_APP_RELATION]->(app:App)
            WHERE app.isActive = true
            RETURN app
            """

            results = await self.client.execute_query(
                query,
                parameters={"org_id": org_id}
            )

            apps = []
            for record in results:
                app_dict = dict(record["app"])
                apps.append(self._neo4j_to_arango_node(app_dict, CollectionNames.APPS.value))

            return apps

        except Exception as e:
            self.logger.error(f"❌ Get org apps failed: {str(e)}")
            return []

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
        try:
            if org_id:
                query = """
                MATCH (d:Department)
                WHERE d.orgId IS NULL OR d.orgId = $org_id
                RETURN d.departmentName
                """
                parameters = {"org_id": org_id}
            else:
                query = """
                MATCH (d:Department)
                RETURN d.departmentName
                """
                parameters = {}

            results = await self.client.execute_query(
                query,
                parameters=parameters,
                txn_id=transaction
            )

            return [record["d.departmentName"] for record in results] if results else []

        except Exception as e:
            self.logger.error(f"❌ Get departments failed: {str(e)}")
            return []

    async def find_duplicate_files(
        self,
        file_key: str,
        md5_checksum: str,
        size_in_bytes: int,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Find duplicate files based on MD5 checksum and file size.

        Args:
            file_key (str): Key of the file to exclude from results
            md5_checksum (str): MD5 checksum of the file
            size_in_bytes (int): Size of the file in bytes
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[Dict]: List of record documents that match both criteria (excluding the file_key)
        """
        try:
            self.logger.info(
                f"🔍 Finding duplicate files with MD5: {md5_checksum} and size: {size_in_bytes} bytes"
            )

            query = """
            MATCH (f:File)
            WHERE f.md5Checksum = $md5_checksum
            AND f.sizeInBytes = $size_in_bytes
            AND f.id <> $file_key
            MATCH (r:Record {id: f.id})
            RETURN r
            """

            results = await self.client.execute_query(
                query,
                parameters={
                    "md5_checksum": md5_checksum,
                    "size_in_bytes": size_in_bytes,
                    "file_key": file_key
                },
                txn_id=transaction
            )

            duplicate_records = []
            for record in results:
                if record.get("r"):
                    record_dict = dict(record["r"])
                    duplicate_records.append(self._neo4j_to_arango_node(record_dict, CollectionNames.RECORDS.value))

            if duplicate_records:
                self.logger.info(
                    f"✅ Found {len(duplicate_records)} duplicate record(s) matching criteria"
                )
            else:
                self.logger.info("✅ No duplicate records found")

            return duplicate_records

        except Exception as e:
            self.logger.error(f"❌ Find duplicate files failed: {str(e)}")
            return []

    async def update_queued_duplicates_status(
        self,
        record_id: str,
        new_indexing_status: str,
        virtual_record_id: Optional[str] = None,
        transaction: Optional[str] = None,
    ) -> int:
        """
        Find all QUEUED duplicate records with the same file md5 hash and update their status.

        Args:
            record_id (str): The record ID to use as reference for finding duplicates
            new_indexing_status (str): The new indexing status to set
            virtual_record_id (Optional[str]): Optional virtual record ID to set
            transaction (Optional[str]): Optional transaction ID

        Returns:
            int: Number of records updated
        """
        try:
            self.logger.info(
                f"🔍 Finding QUEUED duplicate records for record {record_id}"
            )

            # First get the file info for the reference record
            file_query = """
            MATCH (f:File {id: $record_id})
            RETURN f
            """

            results = await self.client.execute_query(
                file_query,
                parameters={"record_id": record_id},
                txn_id=transaction
            )

            if not results or not results[0].get("f"):
                self.logger.info(f"No file found for record {record_id}, skipping queued duplicate update")
                return 0

            file_doc = dict(results[0]["f"])
            md5_checksum = file_doc.get("md5Checksum")
            size_in_bytes = file_doc.get("sizeInBytes")

            if not md5_checksum or size_in_bytes is None:
                self.logger.warning(f"File {record_id} missing md5Checksum or sizeInBytes")
                return 0

            # Find all queued duplicate records
            query = """
            MATCH (f:File)
            WHERE f.md5Checksum = $md5_checksum
            AND f.sizeInBytes = $size_in_bytes
            AND f.id <> $record_id
            MATCH (r:Record {id: f.id})
            WHERE r.indexingStatus = $queued_status
            RETURN r
            """

            results = await self.client.execute_query(
                query,
                parameters={
                    "md5_checksum": md5_checksum,
                    "size_in_bytes": size_in_bytes,
                    "record_id": record_id,
                    "queued_status": "QUEUED"
                },
                txn_id=transaction
            )

            queued_records = []
            for record in results:
                if record.get("r"):
                    record_dict = dict(record["r"])
                    queued_records.append(self._neo4j_to_arango_node(record_dict, CollectionNames.RECORDS.value))

            if not queued_records:
                self.logger.info("✅ No QUEUED duplicate records found")
                return 0

            self.logger.info(
                f"✅ Found {len(queued_records)} QUEUED duplicate record(s) to update"
            )

            # Update all queued records
            current_timestamp = get_epoch_timestamp_in_ms()
            updated_records = []

            for queued_record in queued_records:
                doc = dict(queued_record)

                # Map indexing status to extraction status
                # For EMPTY status, extraction status should also be EMPTY, not FAILED
                if new_indexing_status == ProgressStatus.COMPLETED.value:
                    extraction_status = ProgressStatus.COMPLETED.value
                elif new_indexing_status == ProgressStatus.EMPTY.value:
                    extraction_status = ProgressStatus.EMPTY.value
                else:
                    extraction_status = ProgressStatus.FAILED.value

                update_data = {
                    "indexingStatus": new_indexing_status,
                    "lastIndexTimestamp": current_timestamp,
                    "isDirty": False,
                    "virtualRecordId": virtual_record_id,
                    "extractionStatus": extraction_status,
                }

                doc.update(update_data)
                updated_records.append(doc)

            # Batch update all queued records
            await self.batch_upsert_nodes(updated_records, CollectionNames.RECORDS.value, transaction)

            self.logger.info(
                f"✅ Successfully updated {len(queued_records)} QUEUED duplicate record(s) to status {new_indexing_status}"
            )

            return len(queued_records)

        except Exception as e:
            self.logger.error(
                f"❌ Failed to update queued duplicates status: {str(e)}"
            )
            return -1

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
        try:
            self.logger.info(f"🚀 Copying relationships from {source_key} to {target_key}")

            # Define relationship types to copy
            relationship_types = [
                "BELONGS_TO_DEPARTMENT",
                "BELONGS_TO_CATEGORY",
                "BELONGS_TO_LANGUAGE",
                "BELONGS_TO_TOPIC"
            ]

            for rel_type in relationship_types:
                # Find all relationships from source document
                query = f"""
                MATCH (source:Record {{id: $source_key}})-[r:{rel_type}]->(target)
                RETURN target.id as target_id, r.createdAtTimestamp as timestamp
                """

                results = await self.client.execute_query(
                    query,
                    parameters={"source_key": source_key},
                    txn_id=transaction
                )

                relationships = list(results) if results else []

                if relationships:
                    # Create new relationships for target document
                    new_edges = []
                    for rel in relationships:
                        target_id = rel.get("target_id")
                        if target_id:
                            # Determine target collection based on relationship type
                            if rel_type == "BELONGS_TO_DEPARTMENT":
                                target_collection = CollectionNames.DEPARTMENTS.value
                            elif rel_type == "BELONGS_TO_CATEGORY":
                                # Could be categories or subcategories - need to check
                                target_collection = CollectionNames.CATEGORIES.value
                            elif rel_type == "BELONGS_TO_LANGUAGE":
                                target_collection = CollectionNames.LANGUAGES.value
                            elif rel_type == "BELONGS_TO_TOPIC":
                                target_collection = CollectionNames.TOPICS.value
                            else:
                                target_collection = CollectionNames.CATEGORIES.value

                            new_edge = {
                                "from_id": target_key,
                                "from_collection": CollectionNames.RECORDS.value,
                                "to_id": target_id,
                                "to_collection": target_collection,
                                "createdAtTimestamp": get_epoch_timestamp_in_ms()
                            }
                            new_edges.append(new_edge)

                    # Batch create the new edges
                    if new_edges:
                        await self.batch_create_edges(new_edges, rel_type, transaction=transaction)
                        self.logger.info(
                            f"✅ Copied {len(new_edges)} relationships of type {rel_type}"
                        )

            self.logger.info(f"✅ Successfully copied all relationships to {target_key}")
            return True

        except Exception as e:
            self.logger.error(
                f"❌ Error copying relationships from {source_key} to {target_key}: {str(e)}"
            )
            return False

    async def get_accessible_records(
        self,
        user_id: str,
        org_id: str,
        filters: Optional[Dict[str, List[str]]] = None,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get all records accessible to a user based on their permissions and apply filters.

        This is a complex method that traverses permission graphs using Cypher.
        Note: This is a simplified implementation. The full version would need
        all the complex graph traversal logic from the ArangoDB version.
        """
        try:
            self.logger.info(
                f"Getting accessible records for user {user_id} in org {org_id} with filters {filters}"
            )

            # Extract filters
            filters = filters or {}
            kb_ids = filters.get("kb")
            connector_ids = filters.get("apps")

            # Determine filter case
            has_kb_filter = kb_ids is not None and len(kb_ids) > 0
            has_app_filter = connector_ids is not None and len(connector_ids) > 0

            self.logger.info(
                f"🔍 Filter analysis - KB filter: {has_kb_filter} (IDs: {kb_ids}), "
                f"App filter: {has_app_filter} (Connector IDs: {connector_ids})"
            )

            # Build Cypher query - simplified version
            # This would need to be expanded to match the full ArangoDB AQL logic
            query = """
            MATCH (user:User {userId: $user_id})

            // Direct user -> records via permission
            OPTIONAL MATCH (user)-[:PERMISSION]->(directRecord:Record)

            // User -> Group -> Records
            OPTIONAL MATCH (user)-[:BELONGS_TO]->(group:Group)-[:PERMISSION]->(groupRecord:Record)

            // User -> Organization -> Records
            OPTIONAL MATCH (user)-[:BELONGS_TO]->(org:Organization)-[:PERMISSION]->(orgRecord:Record)

            // User -> Group/Role -> RecordGroup -> Record
            OPTIONAL MATCH (user)-[:PERMISSION]->(groupOrRole)
            WHERE groupOrRole:Group OR groupOrRole:Role
            OPTIONAL MATCH (groupOrRole)-[:PERMISSION]->(recordGroup:RecordGroup)
            OPTIONAL MATCH (recordGroup)<-[:INHERIT_PERMISSIONS*0..5]-(recordGroupRecord:Record)

            // Anyone records
            OPTIONAL MATCH (anyone:Anyone {organization: $org_id})-[:PERMISSION]->(anyoneRecord:Record)

            WITH collect(DISTINCT directRecord) +
                 collect(DISTINCT groupRecord) +
                 collect(DISTINCT orgRecord) +
                 collect(DISTINCT recordGroupRecord) +
                 collect(DISTINCT anyoneRecord) AS allRecords

            UNWIND allRecords AS record
            WITH record
            WHERE record IS NOT NULL
            """

            parameters = {
                "user_id": user_id,
                "org_id": org_id
            }

            # Add KB filter
            if has_kb_filter:
                query += """
            // KB filtering logic would go here
            """
                parameters["kb_ids"] = kb_ids

            # Add App filter
            if has_app_filter:
                query += """
            AND record.connectorId IN $connector_ids
            """
                parameters["connector_ids"] = connector_ids

            # Add metadata filters
            if filters:
                if filters.get("departments"):
                    query += """
            AND EXISTS {
                MATCH (record)-[:BELONGS_TO_DEPARTMENT]->(dept:Department)
                WHERE dept.departmentName IN $departmentNames
            }
            """
                    parameters["departmentNames"] = filters["departments"]

                if filters.get("categories"):
                    query += """
            AND EXISTS {
                MATCH (record)-[:BELONGS_TO_CATEGORY]->(cat:Category)
                WHERE cat.name IN $categoryNames
            }
            """
                    parameters["categoryNames"] = filters["categories"]

                if filters.get("languages"):
                    query += """
            AND EXISTS {
                MATCH (record)-[:BELONGS_TO_LANGUAGE]->(lang:Language)
                WHERE lang.name IN $languageNames
            }
            """
                    parameters["languageNames"] = filters["languages"]

                if filters.get("topics"):
                    query += """
            AND EXISTS {
                MATCH (record)-[:BELONGS_TO_TOPIC]->(topic:Topic)
                WHERE topic.name IN $topicNames
            }
            """
                    parameters["topicNames"] = filters["topics"]

            query += """
            RETURN DISTINCT record
            """

            results = await self.client.execute_query(
                query,
                parameters=parameters,
                txn_id=transaction
            )

            records = []
            for record in results:
                if record.get("record"):
                    record_dict = dict(record["record"])
                    records.append(self._neo4j_to_arango_node(record_dict, CollectionNames.RECORDS.value))

            self.logger.info(f"✅ Found {len(records)} accessible records")
            return records

        except Exception as e:
            self.logger.error(f"❌ Get accessible records failed: {str(e)}")
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return []

    # ==================== Permission Operations ====================

    async def batch_upsert_records(
        self,
        records: List[Record],
        transaction: Optional[str] = None
    ) -> None:
        """Batch upsert records (base + specific type + IS_OF_TYPE edge)"""
        try:
            for record in records:
                # Upsert base record
                record_dict = record.to_arango_base_record()
                await self.batch_upsert_nodes(
                    [record_dict],
                    collection=CollectionNames.RECORDS.value,
                    transaction=transaction
                )

                # Upsert specific type if applicable
                if record.record_type in RECORD_TYPE_COLLECTION_MAPPING:
                    collection = RECORD_TYPE_COLLECTION_MAPPING[record.record_type]
                    type_dict = record.to_arango_record()
                    await self.batch_upsert_nodes(
                        [type_dict],
                        collection=collection,
                        transaction=transaction
                    )

                    # Create IS_OF_TYPE edge
                    is_of_type_edge = {
                        "from_id": record.id,
                        "from_collection": CollectionNames.RECORDS.value,
                        "to_id": record.id,
                        "to_collection": collection,
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                    }
                    await self.batch_create_edges(
                        [is_of_type_edge],
                        collection=CollectionNames.IS_OF_TYPE.value,
                        transaction=transaction
                    )

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
        """Create a relation edge between two records"""
        edge = {
            "from_id": from_record_id,
            "from_collection": CollectionNames.RECORDS.value,
            "to_id": to_record_id,
            "to_collection": CollectionNames.RECORDS.value,
            "relationshipType": relation_type,
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        }

        await self.batch_create_edges(
            [edge],
            collection=CollectionNames.RECORD_RELATIONS.value,
            transaction=transaction
        )

    async def batch_upsert_record_groups(
        self,
        record_groups: List[RecordGroup],
        transaction: Optional[str] = None
    ) -> None:
        """Batch upsert record groups"""
        try:
            nodes = [rg.to_arango_base_record_group() for rg in record_groups]
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
        """Create BELONGS_TO edge from record to record group"""
        edge = {
            "from_id": record_id,
            "from_collection": CollectionNames.RECORDS.value,
            "to_id": record_group_id,
            "to_collection": CollectionNames.RECORD_GROUPS.value,
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        }

        await self.batch_create_edges(
            [edge],
            collection=CollectionNames.BELONGS_TO.value,
            transaction=transaction
        )

    async def create_record_groups_relation(
        self,
        child_id: str,
        parent_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """Create BELONGS_TO edge from child record group to parent record group"""
        edge = {
            "from_id": child_id,
            "from_collection": CollectionNames.RECORD_GROUPS.value,
            "to_id": parent_id,
            "to_collection": CollectionNames.RECORD_GROUPS.value,
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
        """Create INHERIT_PERMISSIONS edge from record to record group"""
        edge = {
            "from_id": record_id,
            "from_collection": CollectionNames.RECORDS.value,
            "to_id": record_group_id,
            "to_collection": CollectionNames.RECORD_GROUPS.value,
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        }

        await self.batch_create_edges(
            [edge],
            collection=CollectionNames.INHERIT_PERMISSIONS.value,
            transaction=transaction
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

            # Permissions should already be in generic format (from_id, to_id, from_collection, to_collection)
            # Just ensure to_id and to_collection are set correctly
            edges = []
            for perm in permissions:
                edge = perm.copy()
                # Ensure to_id and to_collection are set
                edge["to_id"] = record_id
                edge["to_collection"] = CollectionNames.RECORDS.value
                edges.append(edge)

            await self.batch_create_edges(
                edges,
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
            return await self.get_edges_to_node(
                f"{CollectionNames.RECORDS.value}/{file_key}",
                CollectionNames.PERMISSION.value,
                transaction
            )
        except Exception as e:
            self.logger.error(f"❌ Get file permissions failed: {str(e)}")
            return []

    async def get_first_user_with_permission_to_node(
        self,
        node_key: str,
        collection: str = CollectionNames.PERMISSION.value,
        transaction: Optional[str] = None
    ) -> Optional[User]:
        """Get first user with permission to node"""
        try:
            query = """
            MATCH (u:User)-[r:PERMISSION]->(n)
            WHERE n.id = $node_key
            RETURN u
            LIMIT 1
            """

            # Extract key from node_key (may be "records/123" or just "123")
            collection_name, key = self._parse_arango_id(node_key)
            if not key:
                key = node_key

            results = await self.client.execute_query(
                query,
                parameters={"node_key": key},
                txn_id=transaction
            )

            if results:
                user_dict = dict(results[0]["u"])
                user_dict = self._neo4j_to_arango_node(user_dict, CollectionNames.USERS.value)
                return User.from_arango_user(user_dict)

            return None

        except Exception as e:
            self.logger.error(f"❌ Get first user with permission failed: {str(e)}")
            return None

    async def get_users_with_permission_to_node(
        self,
        node_key: str,
        collection: str = CollectionNames.PERMISSION.value,
        transaction: Optional[str] = None
    ) -> List[User]:
        """Get users with permission to node"""
        try:
            query = """
            MATCH (u:User)-[r:PERMISSION]->(n)
            WHERE n.id = $node_key
            RETURN u
            """

            collection_name, key = self._parse_arango_id(node_key)
            if not key:
                key = node_key

            results = await self.client.execute_query(
                query,
                parameters={"node_key": key},
                txn_id=transaction
            )

            users = []
            for record in results:
                user_dict = dict(record["u"])
                user_dict = self._neo4j_to_arango_node(user_dict, CollectionNames.USERS.value)
                users.append(User.from_arango_user(user_dict))

            return users

        except Exception as e:
            self.logger.error(f"❌ Get users with permission failed: {str(e)}")
            return []

    async def get_record_owner_source_user_email(
        self,
        record_id: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """Get record owner source user email"""
        try:
            query = """
            MATCH (u:User)-[r:PERMISSION {role: 'OWNER', type: 'USER'}]->(rec:Record {id: $record_id})
            RETURN u.email AS email
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"record_id": record_id},
                txn_id=transaction
            )

            return results[0]["email"] if results else None

        except Exception as e:
            self.logger.error(f"❌ Get record owner email failed: {str(e)}")
            return None

    # ==================== File/Parent Operations ====================

    async def get_file_parents(
        self,
        file_key: str,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """Get parent file external IDs"""
        try:
            query = """
            MATCH (parent:Record)-[:RECORD_RELATION]->(child:Record {id: $file_key})
            RETURN parent.externalRecordId AS externalRecordId
            """

            results = await self.client.execute_query(
                query,
                parameters={"file_key": file_key},
                txn_id=transaction
            )

            return [{"externalRecordId": record["externalRecordId"]} for record in results]

        except Exception as e:
            self.logger.error(f"❌ Get file parents failed: {str(e)}")
            return []

    # ==================== Sync Point Operations ====================

    async def get_sync_point(
        self,
        key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Get sync point by syncPointKey"""
        try:
            label = collection_to_label(collection)

            query = f"""
            MATCH (sp:{label} {{syncPointKey: $key}})
            RETURN sp
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"key": key},
                txn_id=transaction
            )

            if results:
                sync_point = dict(results[0]["sp"])
                return self._neo4j_to_arango_node(sync_point, collection)

            return None

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
        """Upsert sync point by syncPointKey"""
        try:
            label = collection_to_label(collection)

            # Check if exists
            existing = await self.get_sync_point(sync_point_key, collection, transaction)

            sync_point_data["syncPointKey"] = sync_point_key
            sync_point_data["updatedAtTimestamp"] = get_epoch_timestamp_in_ms()

            if existing:
                # Update
                query = f"""
                MATCH (sp:{label} {{syncPointKey: $key}})
                SET sp += $data
                RETURN sp
                """
                parameters = {"key": sync_point_key, "data": sync_point_data}
            else:
                # Create
                query = f"""
                CREATE (sp:{label} $data)
                RETURN sp
                """
                sync_point_data["createdAtTimestamp"] = get_epoch_timestamp_in_ms()
                parameters = {"data": sync_point_data}

            await self.client.execute_query(query, parameters, txn_id=transaction)
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
        """Remove sync point by syncPointKey"""
        try:
            label = collection_to_label(collection)

            query = f"""
            MATCH (sp:{label} {{syncPointKey: $key}})
            DETACH DELETE sp
            """

            await self.client.execute_query(
                query,
                parameters={"key": key},
                txn_id=transaction
            )

        except Exception as e:
            self.logger.error(f"❌ Remove sync point failed: {str(e)}")
            raise

    # ==================== Batch/Bulk Operations ====================

    async def batch_upsert_app_users(
        self,
        users: List[AppUser],
        transaction: Optional[str] = None
    ) -> None:
        """Batch upsert app users with org and app relations"""
        try:
            if not users:
                return

            # Get org_id
            orgs = await self.get_all_orgs(transaction=transaction)
            if not orgs:
                raise Exception("No organizations found in the database")
            org_id = orgs[0].get("id") or orgs[0].get("_key")
            connector_id = users[0].connector_id

            # Get or create app
            app = await self.get_document(connector_id, CollectionNames.APPS.value, transaction)
            if not app:
                # Create minimal app node
                app_data = {
                    "id": connector_id,
                    "name": connector_id,
                    "isActive": True
                }
                await self.batch_upsert_nodes(
                    [app_data],
                    collection=CollectionNames.APPS.value,
                    transaction=transaction
                )

            for user in users:
                # Check if user exists
                user_record = await self.get_user_by_email(user.email, transaction)

                if not user_record:
                    # Create new user
                    user_data = user.to_arango_base_user()
                    user_data["id"] = user.id
                    user_data["orgId"] = org_id
                    user_data["isActive"] = False

                    await self.batch_upsert_nodes(
                        [user_data],
                        collection=CollectionNames.USERS.value,
                        transaction=transaction
                    )

                    user_record = await self.get_user_by_email(user.email, transaction)

                    # Create org relation
                    user_org_edge = {
                        "from_id": user.id,
                        "from_collection": CollectionNames.USERS.value,
                        "to_id": org_id,
                        "to_collection": CollectionNames.ORGS.value,
                        "createdAtTimestamp": user.created_at,
                        "updatedAtTimestamp": user.updated_at,
                        "entityType": "ORGANIZATION",
                    }
                    await self.batch_create_edges(
                        [user_org_edge],
                        collection=CollectionNames.BELONGS_TO.value,
                        transaction=transaction
                    )

                # Create user-app relation
                user_key = user_record.id
                user_app_edge = {
                    "from_id": user_key,
                    "from_collection": CollectionNames.USERS.value,
                    "to_id": connector_id,
                    "to_collection": CollectionNames.APPS.value,
                    "sourceUserId": user.source_user_id,
                    "syncState": "NOT_STARTED",
                    "lastSyncUpdate": get_epoch_timestamp_in_ms(),
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                }

                await self.batch_create_edges(
                    [user_app_edge],
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
        """Batch upsert user groups"""
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
        """Batch upsert app roles"""
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
        """Batch create user-app relationship edges"""
        try:
            if not edges:
                return 0

            await self.batch_create_edges(
                edges,
                collection=CollectionNames.USER_APP_RELATION.value
            )
            return len(edges)

        except Exception as e:
            self.logger.error(f"❌ Batch create user-app edges failed: {str(e)}")
            raise

    # ==================== Entity ID Operations ====================

    async def get_entity_id_by_email(
        self,
        email: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """Get entity ID (user or group) by email"""
        try:
            query = """
            MATCH (n)
            WHERE (n:User OR n:Group OR n:Person)
            AND toLower(n.email) = toLower($email)
            RETURN n.id AS id
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"email": email},
                txn_id=transaction
            )

            return results[0]["id"] if results else None

        except Exception as e:
            self.logger.error(f"❌ Get entity ID by email failed: {str(e)}")
            return None

    async def bulk_get_entity_ids_by_email(
        self,
        emails: List[str],
        transaction: Optional[str] = None
    ) -> Dict[str, Tuple[str, str, str]]:
        """Bulk get entity IDs for multiple emails"""
        try:
            if not emails:
                return {}

            unique_emails = list(set(emails))

            query = """
            MATCH (n)
            WHERE (n:User OR n:Group OR n:Person)
            AND toLower(n.email) IN [e IN $emails | toLower(e)]
            RETURN n.email AS email, n.id AS id, labels(n) AS labels
            """

            results = await self.client.execute_query(
                query,
                parameters={"emails": unique_emails},
                txn_id=transaction
            )

            result_map = {}
            for r in results:
                email = r["email"]
                entity_id = r["id"]
                labels = r["labels"]

                collection_name = ""
                permission_type = ""

                if Neo4jLabel.USERS.value in labels:
                    collection_name = CollectionNames.USERS.value
                    permission_type = "USER"
                elif Neo4jLabel.GROUPS.value in labels:
                    collection_name = CollectionNames.GROUPS.value
                    permission_type = "GROUP"
                elif Neo4jLabel.PEOPLE.value in labels:
                    collection_name = CollectionNames.PEOPLE.value
                    permission_type = "USER"

                if collection_name:
                    result_map[email] = (entity_id, collection_name, permission_type)

            return result_map

        except Exception as e:
            self.logger.error(f"❌ Bulk get entity IDs by email failed: {str(e)}")
            return {}

    # ==================== Connector-Specific Operations ====================

    async def process_file_permissions(
        self,
        org_id: str,
        file_key: str,
        permissions: List[Dict],
        transaction: Optional[str] = None
    ) -> None:
        """Process and upsert file permissions"""
        try:
            self.logger.info(f"🚀 Processing permissions for file {file_key}")
            timestamp = get_epoch_timestamp_in_ms()

            # Remove 'anyone' permission for this file
            query = """
            MATCH (a:Anyone {file_key: $file_key, organization: $org_id})
            DETACH DELETE a
            """
            await self.client.execute_query(
                query,
                parameters={"file_key": file_key, "org_id": org_id},
                txn_id=transaction
            )

            existing_permissions = await self.get_file_permissions(file_key, transaction)

            # Get all permission IDs from new permissions
            new_permission_ids = list({p.get("id") for p in permissions})

            # Find permissions that exist but are not in new permissions
            permissions_to_remove = [
                perm
                for perm in existing_permissions
                if perm.get("externalPermissionId") not in new_permission_ids
            ]

            # Remove obsolete permissions
            if permissions_to_remove:
                for perm in permissions_to_remove:
                    # Get from_id and from_collection from permission
                    from_id = perm.get("from_id") or perm.get("_from", "").split("/")[-1] if perm.get("_from") else ""
                    from_collection = perm.get("from_collection") or (perm.get("_from", "").split("/")[0] if "/" in perm.get("_from", "") else "")

                    if from_id and from_collection:
                        await self.delete_edge(
                            from_id=from_id,
                            from_collection=from_collection,
                            to_id=file_key,
                            to_collection=CollectionNames.RECORDS.value,
                            collection=CollectionNames.PERMISSION.value,
                            transaction=transaction
                        )

            # Process permissions by type
            for perm_type in ["user", "group", "domain", "anyone"]:
                new_perms = [
                    p for p in permissions
                    if p.get("type", "").lower() == perm_type
                ]
                existing_perms = [
                    p for p in existing_permissions
                    if p.get("type", "").lower() == perm_type
                ]

                if perm_type in ["user", "group", "domain"]:
                    for new_perm in new_perms:
                        perm_id = new_perm.get("id")
                        existing_perm = next(
                            (p for p in existing_perms if p.get("externalPermissionId") == perm_id),
                            None
                        )

                        if existing_perm:
                            # Update existing permission
                            entity_key = existing_perm.get("from_id")
                            await self.batch_upsert_record_permissions(
                                file_key,
                                [new_perm],
                                transaction
                            )
                        else:
                            # Get entity key from email
                            if perm_type in ["user", "group"]:
                                entity_key = await self.get_entity_id_by_email(
                                    new_perm.get("emailAddress"), transaction
                                )
                                if not entity_key:
                                    self.logger.warning(
                                        f"⚠️ Skipping permission for non-existent entity: {new_perm.get('emailAddress')}"
                                    )
                                    continue
                            elif perm_type == "domain":
                                entity_key = org_id
                            else:
                                continue

                            await self.batch_upsert_record_permissions(
                                file_key,
                                [new_perm],
                                transaction
                            )

                elif perm_type == "anyone":
                    # For anyone type, add permission directly to anyone collection
                    for new_perm in new_perms:
                        permission_data = {
                            "id": f"anyone_{file_key}",
                            "type": "anyone",
                            "file_key": file_key,
                            "organization": org_id,
                            "role": new_perm.get("role", "READER"),
                            "externalPermissionId": new_perm.get("id"),
                            "lastUpdatedTimestampAtSource": timestamp,
                            "active": True,
                        }
                        await self.batch_upsert_nodes(
                            [permission_data],
                            collection=CollectionNames.ANYONE.value,
                            transaction=transaction
                        )

            self.logger.info(f"✅ Successfully processed all permissions for file {file_key}")

        except Exception as e:
            self.logger.error(f"❌ Failed to process permissions: {str(e)}")
            if transaction:
                raise

    async def delete_records_and_relations(
        self,
        record_key: str,
        hard_delete: bool = False,
        transaction: Optional[str] = None
    ) -> None:
        """Delete a record and all its relations"""
        try:
            self.logger.info(f"🚀 Deleting record {record_key} (hard_delete={hard_delete})")

            # In Neo4j, DETACH DELETE removes node and all relationships
            record_label = collection_to_label(CollectionNames.RECORDS.value)

            query = f"""
            MATCH (r:{record_label} {{id: $record_key}})
            DETACH DELETE r
            """

            await self.client.execute_query(
                query,
                parameters={"record_key": record_key},
                txn_id=transaction
            )

            # Also delete from type-specific collections
            type_labels = [
                Neo4jLabel.FILES.value,
                Neo4jLabel.MAILS.value,
                Neo4jLabel.WEBPAGES.value,
                Neo4jLabel.COMMENTS.value,
                Neo4jLabel.TICKETS.value,
            ]

            for label in type_labels:
                delete_query = f"""
                MATCH (n:{label} {{id: $record_key}})
                DETACH DELETE n
                """
                try:
                    await self.client.execute_query(
                        delete_query,
                        parameters={"record_key": record_key},
                        txn_id=transaction
                    )
                except Exception as e:
                    self.logger.debug(f"Could not delete node from {label} for record {record_key}: {e}")

        except Exception as e:
            self.logger.error(f"❌ Delete records and relations failed: {str(e)}")
            raise

    async def delete_record(
        self,
        record_id: str,
        user_id: str,
        transaction: Optional[str] = None
    ) -> Dict:
        """Main entry point for record deletion"""
        try:
            # Get record to determine connector type
            record = await self.get_document(record_id, CollectionNames.RECORDS.value, transaction)
            if not record:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"Record not found: {record_id}"
                }

            # For Neo4j, use generic delete
            await self.delete_records_and_relations(record_id, hard_delete=True, transaction=transaction)

            return {
                "success": True,
                "record_id": record_id,
                "message": "Record deleted successfully"
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to delete record {record_id}: {e}")
            return {
                "success": False,
                "code": 500,
                "reason": f"Neo4j record deletion failed: {e}"
            }

    async def delete_record_by_external_id(
        self,
        connector_id: str,
        external_id: str,
        user_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """Delete a record by external ID"""
        try:
            record = await self.get_record_by_external_id(connector_id, external_id, transaction)
            if not record:
                self.logger.warning(f"⚠️ Record {external_id} not found for connector {connector_id}")
                return

            await self.delete_record(record.id, user_id, transaction)

        except Exception as e:
            self.logger.error(f"❌ Delete record by external ID failed: {str(e)}")
            raise

    async def remove_user_access_to_record(
        self,
        connector_id: str,
        external_id: str,
        user_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """Remove a user's access to a record"""
        try:
            record = await self.get_record_by_external_id(connector_id, external_id, transaction)
            if not record:
                self.logger.warning(f"⚠️ Record {external_id} not found for connector {connector_id}")
                return

            # Delete the permission relationship
            await self.delete_edge(
                from_id=user_id,
                from_collection=CollectionNames.USERS.value,
                to_id=record.id,
                to_collection=CollectionNames.RECORDS.value,
                collection=CollectionNames.PERMISSION.value,
                transaction=transaction
            )

        except Exception as e:
            self.logger.error(f"❌ Remove user access to record failed: {str(e)}")
            raise

    async def get_key_by_external_file_id(
        self,
        external_file_id: str
    ) -> Optional[str]:
        """Get internal key by external file ID"""
        try:
            query = """
            MATCH (r:Record {externalRecordId: $external_file_id})
            RETURN r.id AS id
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"external_file_id": external_file_id}
            )

            return results[0]["id"] if results else None

        except Exception as e:
            self.logger.error(f"❌ Get key by external file ID failed: {str(e)}")
            return None

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
        try:
            self.logger.info(
                f"🚀 Retrieving internal key for external message ID {external_message_id}"
            )

            query = """
            MATCH (r:Record {externalRecordId: $external_message_id})
            RETURN r.id AS id
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"external_message_id": external_message_id},
                txn_id=transaction
            )

            if results:
                self.logger.info(
                    f"✅ Successfully retrieved internal key for external message ID {external_message_id}"
                )
                return results[0]["id"]
            else:
                self.logger.warning(
                    f"⚠️ No internal key found for external message ID {external_message_id}"
                )
                return None

        except Exception as e:
            self.logger.error(
                f"❌ Failed to retrieve internal key for external message ID {external_message_id}: {str(e)}"
            )
            return None

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
            edge_collection (str): Edge collection name (relationship type in Neo4j)
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[Dict]: List of related records with messageId, id/key, and relationType
        """
        try:
            self.logger.info(
                f"🚀 Getting related records for {record_id} with relation type {relation_type}"
            )

            # Map edge collection to Neo4j relationship type
            rel_type = self._get_relationship_type(edge_collection)

            query = f"""
            MATCH (source:Record {{id: $record_id}})-[r:{rel_type}]->(target:Record)
            WHERE r.relationType = $relation_type
            RETURN {{
                messageId: target.externalRecordId,
                _key: target.id,
                id: target.id,
                relationType: r.relationType
            }} AS result
            """

            results = await self.client.execute_query(
                query,
                parameters={
                    "record_id": record_id,
                    "relation_type": relation_type
                },
                txn_id=transaction
            )

            if results:
                self.logger.info(
                    f"✅ Found {len(results)} related records for {record_id}"
                )
                return [dict(r["result"]) for r in results]
            else:
                self.logger.info(
                    f"ℹ️ No related records found for {record_id} with relation type {relation_type}"
                )
                return []

        except Exception as e:
            self.logger.error(
                f"❌ Failed to get related records for {record_id}: {str(e)}"
            )
            return []

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
        try:
            self.logger.info(
                f"🚀 Getting messageIdHeader for record {record_key} in collection {collection}"
            )

            # Map collection to Neo4j label
            label = self._get_label(collection)

            query = f"""
            MATCH (r:{label} {{id: $record_key}})
            RETURN r.messageIdHeader AS messageIdHeader
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"record_key": record_key},
                txn_id=transaction
            )

            if results and results[0].get("messageIdHeader") is not None:
                self.logger.info(
                    f"✅ Found messageIdHeader for record {record_key}"
                )
                return results[0]["messageIdHeader"]
            else:
                self.logger.warning(
                    f"⚠️ No messageIdHeader found for record {record_key}"
                )
                return None

        except Exception as e:
            self.logger.error(
                f"❌ Failed to get messageIdHeader for record {record_key}: {str(e)}"
            )
            return None

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
        try:
            self.logger.info(
                f"🚀 Finding related mails with messageIdHeader {message_id_header}, excluding {exclude_key}"
            )

            # Map collection to Neo4j label
            label = self._get_label(collection)

            query = f"""
            MATCH (r:{label})
            WHERE r.messageIdHeader = $message_id_header
            AND r.id <> $exclude_key
            RETURN r.id AS id
            """

            results = await self.client.execute_query(
                query,
                parameters={
                    "message_id_header": message_id_header,
                    "exclude_key": exclude_key
                },
                txn_id=transaction
            )

            if results:
                self.logger.info(
                    f"✅ Found {len(results)} related mails with messageIdHeader {message_id_header}"
                )
                return [r["id"] for r in results]
            else:
                self.logger.info(
                    f"ℹ️ No related mails found with messageIdHeader {message_id_header}"
                )
                return []

        except Exception as e:
            self.logger.error(
                f"❌ Failed to get related mails by messageIdHeader: {str(e)}"
            )
            return []

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
        try:
            self.logger.info(
                f"🚀 Checking name uniqueness for '{instance_name}' with scope {scope}"
            )

            normalized_name = instance_name.strip().lower()
            label = self._get_label(collection)

            if scope == "personal":
                # For personal scope: check uniqueness within user's personal connectors
                query = f"""
                MATCH (doc:{label})
                WHERE doc.scope = $scope
                AND doc.createdBy = $user_id
                AND toLower(trim(doc.name)) = $normalized_name
                RETURN doc.id AS id
                """
                parameters = {
                    "scope": scope,
                    "user_id": user_id,
                    "normalized_name": normalized_name,
                }
            else:  # TEAM scope
                # For team scope: check uniqueness within organization's team connectors
                rel_type = self._get_relationship_type(edge_collection)
                query = f"""
                MATCH (org:Organization {{id: $org_id}})-[r:{rel_type}]->(doc:{label})
                WHERE doc.scope = $scope
                AND toLower(trim(doc.name)) = $normalized_name
                RETURN doc.id AS id
                """
                parameters = {
                    "org_id": org_id,
                    "scope": scope,
                    "normalized_name": normalized_name,
                }

            results = await self.client.execute_query(
                query,
                parameters=parameters,
                txn_id=transaction
            )

            existing = list(results) if results else []
            is_unique = len(existing) == 0

            self.logger.info(
                f"✅ Name uniqueness check: '{instance_name}' is {'unique' if is_unique else 'not unique'}"
            )
            return is_unique

        except Exception as e:
            self.logger.error(f"❌ Error checking name uniqueness: {str(e)}")
            # On error, allow the operation (fail-open to avoid blocking)
            return True

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
        try:
            self.logger.info(f"🚀 Batch updating {len(node_ids)} nodes in {collection}")

            label = self._get_label(collection)

            # Build SET clause from updates
            set_clauses = []
            parameters = {"node_ids": node_ids}
            for i, (key, value) in enumerate(updates.items()):
                param_name = f"update_{i}"
                set_clauses.append(f"doc.{key} = ${param_name}")
                parameters[param_name] = value

            set_clause = ", ".join(set_clauses)

            query = f"""
            MATCH (doc:{label})
            WHERE doc.id IN $node_ids
            SET {set_clause}
            RETURN doc.id AS id
            """

            results = await self.client.execute_query(
                query,
                parameters=parameters,
                txn_id=transaction
            )

            if results:
                self.logger.info(f"✅ Successfully batch updated {len(results)} nodes")
                return True
            else:
                self.logger.warning("⚠️ No nodes were updated")
                return False

        except Exception as e:
            self.logger.error(f"❌ Failed to batch update nodes: {str(e)}")
            return False

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
        try:
            self.logger.info(
                f"🚀 Getting connector instances with filters: scope={scope}, search={search}, page={page}"
            )

            label = self._get_label(collection)

            # Build WHERE clauses
            where_clauses = ["doc.id IS NOT NULL"]
            parameters = {}

            # Add scope filter if specified
            if scope:
                where_clauses.append("doc.scope = $scope")
                parameters["scope"] = scope

            # Add access control
            if not is_admin:
                # Non-admins can only see their own connectors
                where_clauses.append("doc.createdBy = $user_id")
                parameters["user_id"] = user_id
            else:
                # Admins can see all team connectors + their personal connectors
                where_clauses.append("(doc.scope = $team_scope OR doc.createdBy = $user_id)")
                parameters["team_scope"] = "team"
                parameters["user_id"] = user_id

            # Add search filter if specified
            if search:
                search_pattern = f".*{search.lower()}.*"
                where_clauses.append(
                    "(toLower(doc.name) =~ $search OR toLower(doc.type) =~ $search OR toLower(doc.appGroup) =~ $search)"
                )
                parameters["search"] = search_pattern

            where_clause = " AND ".join(where_clauses)

            # Get total count
            count_query = f"""
            MATCH (doc:{label})
            WHERE {where_clause}
            RETURN count(doc) AS total
            """
            count_results = await self.client.execute_query(
                count_query,
                parameters=parameters,
                txn_id=transaction
            )
            total_count = count_results[0]["total"] if count_results else 0

            # Get paginated results
            offset = (page - 1) * limit
            parameters["offset"] = offset
            parameters["limit"] = limit

            query = f"""
            MATCH (doc:{label})
            WHERE {where_clause}
            RETURN doc
            ORDER BY doc.createdAtTimestamp DESC
            SKIP $offset
            LIMIT $limit
            """

            results = await self.client.execute_query(
                query,
                parameters=parameters,
                txn_id=transaction
            )

            documents = [self._neo4j_to_arango_node(dict(r["doc"]), collection) for r in results] if results else []

            self.logger.info(f"✅ Found {len(documents)} connector instances (total: {total_count})")
            return documents, total_count

        except Exception as e:
            self.logger.error(f"❌ Failed to get connector instances with filters: {str(e)}")
            return [], 0

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
        try:
            self.logger.info(f"🚀 Counting connector instances for scope {scope}")

            label = self._get_label(collection)

            where_clauses = ["doc.id IS NOT NULL", "doc.scope = $scope", "doc.isConfigured = true"]
            parameters = {"scope": scope}

            # Add user filter for personal scope
            if scope == "personal" and user_id:
                where_clauses.append("doc.createdBy = $user_id")
                parameters["user_id"] = user_id

            where_clause = " AND ".join(where_clauses)

            query = f"""
            MATCH (doc:{label})
            WHERE {where_clause}
            RETURN count(doc) AS total
            """

            results = await self.client.execute_query(
                query,
                parameters=parameters,
                txn_id=transaction
            )

            count = results[0]["total"] if results else 0
            self.logger.info(f"✅ Found {count} connector instances for scope {scope}")
            return count

        except Exception as e:
            self.logger.error(f"❌ Failed to count connector instances by scope: {str(e)}")
            return 0

    async def get_connector_instances_by_scope_and_user(
        self,
        collection: str,
        user_id: str,
        team_scope: str,
        personal_scope: str,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get connector instances by scope and user (for _get_all_connector_instances).

        Args:
            collection (str): Collection name
            user_id (str): User ID
            team_scope (str): Team scope value
            personal_scope (str): Personal scope value
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[Dict]: List of connector instance documents
        """
        try:
            self.logger.info(f"🚀 Getting connector instances for user {user_id}")

            label = self._get_label(collection)

            query = f"""
            MATCH (doc:{label})
            WHERE doc.id IS NOT NULL
            AND (doc.scope = $team_scope OR (doc.scope = $personal_scope AND doc.createdBy = $user_id))
            RETURN doc
            """

            parameters = {
                "team_scope": team_scope,
                "personal_scope": personal_scope,
                "user_id": user_id,
            }

            results = await self.client.execute_query(
                query,
                parameters=parameters,
                txn_id=transaction
            )

            documents = [self._neo4j_to_arango_node(dict(r["doc"]), collection) for r in results] if results else []
            self.logger.info(f"✅ Found {len(documents)} connector instances")
            return documents

        except Exception as e:
            self.logger.error(f"❌ Failed to get connector instances by scope and user: {str(e)}")
            return []

    async def get_record_by_id(
        self,
        record_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get a record by its internal ID.

        Args:
            record_id (str): The internal record ID to look up
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Optional[Dict]: Record data if found, None otherwise
        """
        try:
            self.logger.info(f"🚀 Retrieving record for id {record_id}")

            query = """
            MATCH (record:Record {id: $record_id})
            RETURN record
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"record_id": record_id},
                txn_id=transaction
            )

            if results and len(results) > 0:
                record_dict = dict(results[0]["record"])
                self.logger.info(f"✅ Successfully retrieved record for id {record_id} Record: {record_dict}")
                record = Record.from_arango_base_record(record_dict)
                return record
            else:
                self.logger.warning(f"⚠️ No record found for id {record_id}")
                return None

        except Exception as e:
            self.logger.error(f"❌ Failed to retrieve record for id {record_id}: {str(e)}")
            return None

    async def check_record_access_with_details(
        self,
        user_id: str,
        org_id: str,
        record_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Check record access and return record details if accessible.

        Args:
            user_id (str): User ID (userId field value)
            org_id (str): Organization ID
            record_id (str): Record ID to check access for
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Optional[Dict]: Record details with permissions if accessible, None otherwise
        """
        try:
            self.logger.info(f"🚀 Checking record access for user {user_id}, record {record_id}")

            # Get user by userId field
            user_query = """
            MATCH (u:User {userId: $user_id})
            RETURN u
            LIMIT 1
            """
            user_results = await self.client.execute_query(
                user_query,
                parameters={"user_id": user_id},
                txn_id=transaction
            )

            if not user_results:
                self.logger.warning(f"⚠️ User not found: {user_id}")
                return None

            user = dict(user_results[0]["u"])
            user_key = user.get("id")

            # Get record
            record = await self.get_record_by_id(record_id, transaction)
            record = record.to_arango_base_record()
            if not record:
                self.logger.warning(f"⚠️ Record not found: {record_id}")
                return None

            # Build comprehensive access query matching BaseArangoService
            # Check all access paths: direct, group, record group, nested record groups, org, KB, anyone
            access_query = """
            MATCH (u:User {id: $user_key})
            MATCH (rec:Record {id: $record_id})

            // Direct access
            OPTIONAL MATCH (u)-[directPerm:PERMISSION {type: "USER"}]->(rec)
            WITH u, rec, COLLECT({type: "DIRECT", source: u, role: directPerm.role}) AS directAccess

            // Group access: User -> Group -> Record
            OPTIONAL MATCH (u)-[userGroupPerm:PERMISSION {type: "USER"}]->(g:Group)
            OPTIONAL MATCH (g)-[groupRecPerm:PERMISSION]->(rec)
            WITH u, rec, directAccess, COLLECT({type: "GROUP", source: g, role: groupRecPerm.role}) AS groupAccess

            // Record Group access: User -> Group -> RecordGroup -> Record
            OPTIONAL MATCH (u)-[userGroupPerm2:PERMISSION {type: "USER"}]->(g2:Group)
            OPTIONAL MATCH (g2)-[groupRgPerm:PERMISSION]->(rg:RecordGroup)
            WHERE groupRgPerm.type IN ["GROUP", "ROLE"]
            OPTIONAL MATCH (rg)<-[:BELONGS_TO]-(rec2:Record {id: $record_id})
            WITH u, rec, directAccess, groupAccess, COLLECT({type: "RECORD_GROUP", source: rg, role: groupRgPerm.role}) AS recordGroupAccess

            // Nested Record Group access: User -> Group -> Parent RG -> Child RG -> Record
            OPTIONAL MATCH (u)-[userGroupPerm3:PERMISSION {type: "USER"}]->(g3:Group)
            OPTIONAL MATCH (g3)-[groupParentRgPerm:PERMISSION]->(parentRg:RecordGroup)
            WHERE groupParentRgPerm.type IN ["GROUP", "ROLE"]
            OPTIONAL MATCH (parentRg)<-[:BELONGS_TO]-(childRg:RecordGroup)
            OPTIONAL MATCH (childRg)<-[:BELONGS_TO]-(rec3:Record {id: $record_id})
            WITH u, rec, directAccess, groupAccess, recordGroupAccess,
                 COLLECT({type: "NESTED_RECORD_GROUP", source: childRg, role: groupParentRgPerm.role}) AS nestedRgAccess

            // Direct User to Record Group access (with nested support)
            OPTIONAL MATCH (u)-[userRgPerm:PERMISSION {type: "USER"}]->(rg2:RecordGroup)
            OPTIONAL MATCH path = (rg2)<-[:BELONGS_TO*0..5]-(rec4:Record {id: $record_id})
            WITH u, rec, directAccess, groupAccess, recordGroupAccess, nestedRgAccess,
                 COLLECT({type: "DIRECT_USER_RECORD_GROUP", source: rg2, role: userRgPerm.role, depth: length(path)}) AS directUserRgAccess

            // Organization access: User -> Organization -> Record
            OPTIONAL MATCH (u)-[:BELONGS_TO]->(org:Organization {id: $org_id})
            OPTIONAL MATCH (org)-[orgRecPerm:PERMISSION]->(rec5:Record {id: $record_id})
            WITH u, rec, directAccess, groupAccess, recordGroupAccess, nestedRgAccess, directUserRgAccess,
                 COLLECT({type: "ORGANIZATION", source: org, role: orgRecPerm.role}) AS orgAccess

            // Organization Record Group access: User -> Organization -> RecordGroup -> Record
            OPTIONAL MATCH (u)-[belongsTo:BELONGS_TO {entityType: "ORGANIZATION"}]->(org2:Organization {id: $org_id})
            OPTIONAL MATCH (org2)-[orgRgPerm:PERMISSION {type: "ORG"}]->(rg3:RecordGroup)
            OPTIONAL MATCH path2 = (rg3)<-[:BELONGS_TO*0..2]-(rec6:Record {id: $record_id})
            WITH u, rec, directAccess, groupAccess, recordGroupAccess, nestedRgAccess, directUserRgAccess, orgAccess,
                 COLLECT({type: "ORG_RECORD_GROUP", source: rg3, role: orgRgPerm.role, depth: length(path2)}) AS orgRgAccess

            // Knowledge Base access: Check if record belongs to KB
            OPTIONAL MATCH (kb:RecordGroup)<-[:BELONGS_TO]-(rec7:Record {id: $record_id})
            OPTIONAL MATCH (u)-[kbPerm:PERMISSION {type: "USER"}]->(kb)
            OPTIONAL MATCH (rec7)<-[:PARENT_CHILD]-(folder:File)
            WHERE folder.isFile = false
            WITH u, rec, directAccess, groupAccess, recordGroupAccess, nestedRgAccess, directUserRgAccess, orgAccess, orgRgAccess,
                 COLLECT({type: "KNOWLEDGE_BASE", source: kb, role: kbPerm.role, folder: folder}) AS kbDirectAccess

            // KB Team access: User -> Team -> KB -> Record
            OPTIONAL MATCH (kb2:RecordGroup)<-[:BELONGS_TO]-(rec8:Record {id: $record_id})
            OPTIONAL MATCH (team:Team)-[teamKbPerm:PERMISSION {type: "TEAM"}]->(kb2)
            OPTIONAL MATCH (u)-[userTeamPerm:PERMISSION {type: "USER"}]->(team)
            OPTIONAL MATCH (rec8)<-[:PARENT_CHILD]-(folder2:File)
            WHERE folder2.isFile = false
            WITH u, rec, directAccess, groupAccess, recordGroupAccess, nestedRgAccess, directUserRgAccess, orgAccess, orgRgAccess, kbDirectAccess,
                 COLLECT({
                     type: "KNOWLEDGE_BASE_TEAM",
                     source: kb2,
                     role: userTeamPerm.role,
                     folder: folder2
                 }) AS kbTeamAccess

            // Anyone access
            OPTIONAL MATCH (anyone:Anyone {organization: $org_id, file_key: $record_id})
            WITH u, rec, directAccess, groupAccess, recordGroupAccess, nestedRgAccess, directUserRgAccess, orgAccess, orgRgAccess, kbDirectAccess, kbTeamAccess,
                 COLLECT({type: "ANYONE", source: null, role: anyone.role}) AS anyoneAccess

            // Combine all access paths
            WITH directAccess + groupAccess + recordGroupAccess + nestedRgAccess + directUserRgAccess + orgAccess + orgRgAccess + kbDirectAccess + kbTeamAccess + anyoneAccess AS allAccess
            WHERE size([a IN allAccess WHERE a.source IS NOT NULL OR a.type = "ANYONE"]) > 0
            RETURN allAccess
            """

            access_results = await self.client.execute_query(
                access_query,
                parameters={
                    "user_key": user_key,
                    "record_id": record_id,
                    "org_id": org_id
                },
                txn_id=transaction
            )

            if not access_results or not access_results[0].get("allAccess"):
                return None

            access_result = access_results[0]["allAccess"]
            # Filter out None entries
            access_result = [a for a in access_result if a.get("source") is not None or a.get("type") == "ANYONE"]

            if not access_result:
                return None

            # Get additional data based on record type
            additional_data = None
            record_type = record.get("recordType")

            if record_type == RecordTypes.FILE.value:
                additional_data = await self.get_document(
                    record_id, CollectionNames.FILES.value, transaction
                )
            elif record_type == RecordTypes.MAIL.value:
                additional_data = await self.get_document(
                    record_id, CollectionNames.MAILS.value, transaction
                )
                if additional_data and user.get("email"):
                    message_id = record.get("externalRecordId")
                    additional_data["webUrl"] = (
                        f"https://mail.google.com/mail?authuser={user['email']}#all/{message_id}"
                    )
            elif record_type == RecordTypes.TICKET.value:
                additional_data = await self.get_document(
                    record_id, CollectionNames.TICKETS.value, transaction
                )

            # Get metadata (departments, categories, topics, languages)
            # Use separate queries to avoid aggregation conflicts
            metadata_query = """
            MATCH (rec:Record {id: $record_id})

            OPTIONAL MATCH (rec)-[:BELONGS_TO_DEPARTMENT]->(dept:Department)
            WITH rec, COLLECT(DISTINCT {id: dept.id, name: dept.departmentName}) AS departments

            OPTIONAL MATCH (rec)-[:BELONGS_TO_CATEGORY]->(cat:Category)
            WITH rec, departments, COLLECT(DISTINCT {id: cat.id, name: cat.name}) AS categories

            OPTIONAL MATCH (rec)-[:BELONGS_TO_CATEGORY]->(subcat1:Subcategory1)
            WITH rec, departments, categories, COLLECT(DISTINCT {id: subcat1.id, name: subcat1.name}) AS subcategories1

            OPTIONAL MATCH (rec)-[:BELONGS_TO_CATEGORY]->(subcat2:Subcategory2)
            WITH rec, departments, categories, subcategories1, COLLECT(DISTINCT {id: subcat2.id, name: subcat2.name}) AS subcategories2

            OPTIONAL MATCH (rec)-[:BELONGS_TO_CATEGORY]->(subcat3:Subcategory3)
            WITH rec, departments, categories, subcategories1, subcategories2, COLLECT(DISTINCT {id: subcat3.id, name: subcat3.name}) AS subcategories3

            OPTIONAL MATCH (rec)-[:BELONGS_TO_TOPIC]->(topic:Topic)
            WITH rec, departments, categories, subcategories1, subcategories2, subcategories3, COLLECT(DISTINCT {id: topic.id, name: topic.name}) AS topics

            OPTIONAL MATCH (rec)-[:BELONGS_TO_LANGUAGE]->(lang:Language)
            WITH departments, categories, subcategories1, subcategories2, subcategories3, topics, COLLECT(DISTINCT {id: lang.id, name: lang.name}) AS languages

            RETURN {
                departments: [d IN departments WHERE d.id IS NOT NULL],
                categories: [c IN categories WHERE c.id IS NOT NULL],
                subcategories1: [s1 IN subcategories1 WHERE s1.id IS NOT NULL],
                subcategories2: [s2 IN subcategories2 WHERE s2.id IS NOT NULL],
                subcategories3: [s3 IN subcategories3 WHERE s3.id IS NOT NULL],
                topics: [t IN topics WHERE t.id IS NOT NULL],
                languages: [l IN languages WHERE l.id IS NOT NULL]
            } AS metadata
            """

            metadata_results = await self.client.execute_query(
                metadata_query,
                parameters={"record_id": record_id},
                txn_id=transaction
            )
            metadata_result = metadata_results[0].get("metadata") if metadata_results else None

            # Get knowledge base info if record is in a KB
            kb_info = None
            folder_info = None
            for access in access_result:
                if access.get("type") in ["KNOWLEDGE_BASE", "KNOWLEDGE_BASE_TEAM"]:
                    kb = access.get("source")
                    if kb:
                        kb_info = {
                            "id": kb.get("id") or kb.get("_key"),
                            "name": kb.get("groupName"),
                            "orgId": kb.get("orgId"),
                        }
                    folder = access.get("folder")
                    if folder:
                        folder_info = {
                            "id": folder.get("id") or folder.get("_key"),
                            "name": folder.get("name")
                        }
                    break

            # Format permissions from access paths
            permissions = []
            for access in access_result:
                permission = {
                    "id": record.get("id") or record.get("_key"),
                    "name": record.get("recordName"),
                    "type": record.get("recordType"),
                    "relationship": access.get("role"),
                    "accessType": access.get("type"),
                }
                permissions.append(permission)

            return {
                "record": {
                    **record,
                    "fileRecord": (
                        additional_data
                        if record_type == RecordTypes.FILE.value
                        else None
                    ),
                    "mailRecord": (
                        additional_data
                        if record_type == RecordTypes.MAIL.value
                        else None
                    ),
                    "ticketRecord": (
                        additional_data
                        if record_type == RecordTypes.TICKET.value
                        else None
                    ),
                },
                "knowledgeBase": kb_info,
                "folder": folder_info,
                "metadata": metadata_result,
                "permissions": permissions,
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to check record access: {str(e)}")
            raise

    async def get_account_type(
        self,
        org_id: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """
        Get account type for an organization.

        Args:
            org_id (str): Organization ID
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Optional[str]: Account type (e.g., "INDIVIDUAL", "ENTERPRISE") or None
        """
        try:
            self.logger.info(f"🚀 Getting account type for organization {org_id}")

            query = """
            MATCH (o:Organization {id: $org_id})
            RETURN o.accountType AS accountType
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"org_id": org_id},
                txn_id=transaction
            )

            if results:
                account_type = results[0].get("accountType")
                self.logger.info(f"✅ Found account type: {account_type}")
                return account_type
            else:
                self.logger.warning(f"⚠️ Organization not found: {org_id}")
                return None

        except Exception as e:
            self.logger.error(f"❌ Failed to get account type: {str(e)}")
            return None

    async def get_connector_stats(
        self,
        org_id: str,
        connector_id: str,
        transaction: Optional[str] = None
    ) -> Dict:
        """
        Get connector statistics for a specific connector.

        Args:
            org_id (str): Organization ID
            connector_id (str): Connector ID
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Dict: Statistics data with success status
        """
        try:
            self.logger.info(f"🚀 Getting connector stats for org {org_id}, connector {connector_id}")

            # Get all records for the connector (excluding folders)
            query = """
            MATCH (r:Record)
            WHERE r.orgId = $org_id
            AND r.origin = "CONNECTOR"
            AND r.connectorId = $connector_id
            AND r.isDeleted <> true
            OPTIONAL MATCH (r)-[:IS_OF_TYPE]->(f:File)
            WHERE f.isFile = true OR f IS NULL
            WITH r
            WHERE f IS NULL OR f.isFile = true
            RETURN r
            """

            results = await self.client.execute_query(
                query,
                parameters={"org_id": org_id, "connector_id": connector_id},
                txn_id=transaction
            )

            records = [dict(r["r"]) for r in results] if results else []

            # Calculate stats
            total = len(records)
            indexing_status_counts = {}
            record_type_counts = {}

            statuses = ["NOT_STARTED", "IN_PROGRESS", "COMPLETED", "FAILED", "FILE_TYPE_NOT_SUPPORTED",
                       "AUTO_INDEX_OFF", "ENABLE_MULTIMODAL_MODELS", "EMPTY", "QUEUED", "PAUSED"]

            for status in statuses:
                indexing_status_counts[status] = sum(1 for r in records if r.get("indexingStatus") == status)

            # Group by record type
            record_types = set(r.get("recordType") for r in records if r.get("recordType"))
            for record_type in record_types:
                type_records = [r for r in records if r.get("recordType") == record_type]
                record_type_counts[record_type] = {
                    "recordType": record_type,
                    "total": len(type_records),
                    "indexingStatus": {
                        status: sum(1 for r in type_records if r.get("indexingStatus") == status)
                        for status in statuses
                    }
                }

            result = {
                "orgId": org_id,
                "connectorId": connector_id,
                "origin": "CONNECTOR",
                "stats": {
                    "total": total,
                    "indexingStatus": indexing_status_counts
                },
                "byRecordType": list(record_type_counts.values())
            }

            self.logger.info(f"✅ Retrieved stats for connector {connector_id}")
            return {
                "success": True,
                "data": result
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to get connector stats: {str(e)}")
            return {
                "success": False,
                "message": str(e),
                "data": None
            }

    async def reindex_single_record(self, record_id: str, user_id: str, org_id: str, request: Request, depth: int = 0) -> Dict:
        """
        Reindex a single record with permission checks and event publishing.
        If the record is a folder and depth > 0, also reindex children up to specified depth.

        Args:
            record_id: Record ID to reindex
            user_id: External user ID doing the reindex
            org_id: Organization ID
            request: FastAPI request object
            depth: Depth of children to reindex (-1 = unlimited/max 100, other negatives = 0,
                   0 = only this record, 1 = direct children, etc.)
        """
        try:
            self.logger.info(f"🔄 Starting reindex for record {record_id} by user {user_id} with depth {depth}")

            # Handle negative depth: -1 means unlimited (set to MAX_REINDEX_DEPTH), other negatives are invalid (set to 0)
            if depth == -1:
                depth = MAX_REINDEX_DEPTH
                self.logger.info(f"Depth was -1 (unlimited), setting to maximum limit: {depth}")
            elif depth < 0:
                self.logger.warning(f"Invalid negative depth {depth}, setting to 0 (single record only)")
                depth = 0

            # Get record to determine connector type
            record = await self.get_document(record_id, CollectionNames.RECORDS.value)
            if not record:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"Record not found: {record_id}"
                }

            if record.get("isDeleted"):
                return {
                    "success": False,
                    "code": 400,
                    "reason": "Cannot reindex deleted record"
                }

            connector_name = record.get("connectorName", "")
            connector_id = record.get("connectorId", "")
            origin = record.get("origin", "")

            self.logger.info(f"📋 Record details - Origin: {origin}, Connector: {connector_name}, ConnectorId: {connector_id}")

            # Get user
            user = await self.get_user_by_user_id(user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found: {user_id}"
                }

            user_key = user.get('_key')

            # Check permissions based on origin type
            if origin == OriginTypes.UPLOAD.value:
                # KB record - check KB permissions
                kb_context = await self._get_kb_context_for_record(record_id)
                if not kb_context:
                    return {
                        "success": False,
                        "code": 404,
                        "reason": f"Knowledge base context not found for record {record_id}"
                    }

                user_role = await self.get_user_kb_permission(kb_context["kb_id"], user_key)
                if user_role not in ["OWNER", "WRITER", "READER"]:
                    return {
                        "success": False,
                        "code": 403,
                        "reason": f"Insufficient KB permissions. User role: {user_role}. Required: OWNER, WRITER, READER"
                    }

                connector_type = Connectors.KNOWLEDGE_BASE.value


            elif origin == OriginTypes.CONNECTOR.value:
                # Connector record - check connector-specific permissions
                # Note: _check_record_permissions is not implemented in graph providers
                # For now, we'll allow the operation and return basic info
                user_role = "READER"  # Default role for validation

                connector_type = connector_name
            else:
                return {
                    "success": False,
                    "code": 400,
                    "reason": f"Unsupported record origin: {origin}"
                }

            # Note: Graph providers don't implement event publishing
            # This is a simplified version that validates the record and returns info
            # The actual event publishing would be handled by the connector service layer

            self.logger.info(f"✅ Record {record_id} validated for reindexing")
            return {
                "success": True,
                "recordId": record_id,
                "recordName": record.get("recordName"),
                "connector": connector_type,
                "eventPublished": False,  # Graph providers don't publish events
                "userRole": user_role
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to reindex record {record_id}: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": f"Internal error: {str(e)}"
            }

    async def reindex_record_group_records(
        self,
        record_group_id: str,
        user_id: str,
        depth: int = 1,
        transaction: Optional[str] = None
    ) -> Dict:
        """
        Reindex all records in a record group.

        Args:
            record_group_id (str): Record group ID
            user_id (str): User ID performing the reindex
            depth (int): Depth of children to reindex
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Dict: Result with success status
        """
        try:
            self.logger.info(f"🚀 Reindexing record group {record_group_id} with depth {depth}")

            # Get record group
            record_group = await self.get_document(record_group_id, CollectionNames.RECORD_GROUPS.value)
            if not record_group:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"Record group not found: {record_group_id}"
                }

            connector_id = record_group.get("connectorId", "")
            connector_name = record_group.get("connectorName", "")

            if not connector_id or not connector_name:
                return {
                    "success": False,
                    "code": 400,
                    "reason": "Record group does not have a connector id or name"
                }

            # For now, return success - actual reindexing logic would be handled by event service
            # This method is mainly for validation and returning connector info
            self.logger.info(f"✅ Record group {record_group_id} validated for reindexing")
            return {
                "success": True,
                "code": 200,
                "record_group_id": record_group_id,
                "connector_id": connector_id,
                "connector_name": connector_name
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to reindex record group: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": str(e)
            }

    async def organization_exists(
        self,
        organization_name: str
    ) -> bool:
        """Check if an organization exists"""
        try:
            query = """
            MATCH (o:Organization {name: $organization_name})
            RETURN o.id
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"organization_name": organization_name}
            )

            return bool(results)

        except Exception as e:
            self.logger.error(f"❌ Organization exists check failed: {str(e)}")
            return False

    async def get_user_sync_state(
        self,
        user_email: str,
        service_type: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Get user's sync state for a specific service"""
        try:
            query = """
            MATCH (u:User {email: $user_email})-[rel:USER_APP_RELATION]->(app:App {name: $service_type})
            RETURN rel, u.id AS from_id, app.id AS to_id
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"user_email": user_email, "service_type": service_type},
                txn_id=transaction
            )

            if results:
                rel = dict(results[0]["rel"])
                # Return in generic format
                rel["from_id"] = results[0]["from_id"]
                rel["from_collection"] = CollectionNames.USERS.value
                rel["to_id"] = results[0]["to_id"]
                rel["to_collection"] = CollectionNames.APPS.value
                return rel

            return None

        except Exception as e:
            self.logger.error(f"❌ Get user sync state failed: {str(e)}")
            return None

    async def update_user_sync_state(
        self,
        user_email: str,
        state: str,
        service_type: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Update user's sync state for a specific service"""
        try:
            updated_timestamp = get_epoch_timestamp_in_ms()

            query = """
            MATCH (u:User {email: $user_email})-[rel:USER_APP_RELATION]->(app:App {name: $service_type})
            SET rel.syncState = $state, rel.lastSyncUpdate = $updated_timestamp
            RETURN rel, u.id AS from_id, app.id AS to_id
            """

            results = await self.client.execute_query(
                query,
                parameters={
                    "user_email": user_email,
                    "service_type": service_type,
                    "state": state,
                    "updated_timestamp": updated_timestamp
                },
                txn_id=transaction
            )

            if results:
                rel = dict(results[0]["rel"])
                # Return in generic format
                rel["from_id"] = results[0]["from_id"]
                rel["from_collection"] = CollectionNames.USERS.value
                rel["to_id"] = results[0]["to_id"]
                rel["to_collection"] = CollectionNames.APPS.value
                return rel

            return None

        except Exception as e:
            self.logger.error(f"❌ Update user sync state failed: {str(e)}")
            return None

    async def get_drive_sync_state(
        self,
        drive_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Get drive's sync state"""
        try:
            query = """
            MATCH (d:Drive {id: $drive_id})
            RETURN d
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"drive_id": drive_id},
                txn_id=transaction
            )

            if results:
                drive = dict(results[0]["d"])
                return self._neo4j_to_arango_node(drive, CollectionNames.DRIVES.value)

            return None

        except Exception as e:
            self.logger.error(f"❌ Get drive sync state failed: {str(e)}")
            return None

    async def update_drive_sync_state(
        self,
        drive_id: str,
        state: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Update drive's sync state"""
        try:
            updated_timestamp = get_epoch_timestamp_in_ms()

            query = """
            MATCH (d:Drive {id: $drive_id})
            SET d.sync_state = $state, d.last_sync_update = $updated_timestamp
            RETURN d
            """

            results = await self.client.execute_query(
                query,
                parameters={
                    "drive_id": drive_id,
                    "state": state,
                    "updated_timestamp": updated_timestamp
                },
                txn_id=transaction
            )

            if results:
                drive = dict(results[0]["d"])
                return self._neo4j_to_arango_node(drive, CollectionNames.DRIVES.value)

            return None

        except Exception as e:
            self.logger.error(f"❌ Update drive sync state failed: {str(e)}")
            return None

    # ==================== Page Token Operations ====================

    async def store_page_token(
        self,
        channel_id: str,
        resource_id: str,
        user_email: str,
        token: str,
        expiration: Optional[str] = None,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Store page token for a channel/resource"""
        try:
            created_timestamp = get_epoch_timestamp_in_ms()
            page_token_id = f"page_token_{user_email}_{channel_id}_{resource_id}"

            token_data = {
                "id": page_token_id,
                "channelId": channel_id,
                "resourceId": resource_id,
                "userEmail": user_email,
                "token": token,
                "createdAtTimestamp": created_timestamp,
                "expiration": expiration,
            }

            label = collection_to_label(CollectionNames.PAGE_TOKENS.value)

            query = f"""
            MERGE (pt:{label} {{id: $id}})
            SET pt += $token_data
            RETURN pt
            """

            results = await self.client.execute_query(
                query,
                parameters={"id": page_token_id, "token_data": token_data},
                txn_id=transaction
            )

            if results:
                pt = dict(results[0]["pt"])
                return self._neo4j_to_arango_node(pt, CollectionNames.PAGE_TOKENS.value)

            return None

        except Exception as e:
            self.logger.error(f"❌ Store page token failed: {str(e)}")
            return None

    async def get_page_token_db(
        self,
        channel_id: Optional[str] = None,
        resource_id: Optional[str] = None,
        user_email: Optional[str] = None,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Get page token for specific channel/resource/user"""
        try:
            label = collection_to_label(CollectionNames.PAGE_TOKENS.value)

            filter_clauses = []
            parameters = {}

            if channel_id:
                filter_clauses.append("pt.channelId = $channel_id")
                parameters["channel_id"] = channel_id
            if resource_id:
                filter_clauses.append("pt.resourceId = $resource_id")
                parameters["resource_id"] = resource_id
            if user_email:
                filter_clauses.append("pt.userEmail = $user_email")
                parameters["user_email"] = user_email

            where_clause = "WHERE " + " AND ".join(filter_clauses) if filter_clauses else ""

            query = f"""
            MATCH (pt:{label})
            {where_clause}
            RETURN pt
            ORDER BY pt.createdAtTimestamp DESC
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters=parameters,
                txn_id=transaction
            )

            if results:
                pt = dict(results[0]["pt"])
                return self._neo4j_to_arango_node(pt, CollectionNames.PAGE_TOKENS.value)

            return None

        except Exception as e:
            self.logger.error(f"❌ Get page token failed: {str(e)}")
            return None

    # ==================== Utility Operations ====================

    async def check_collection_has_document(
        self,
        collection_name: str,
        document_id: str,
        transaction: Optional[str] = None
    ) -> bool:
        """Check if a document exists in a collection"""
        try:
            label = collection_to_label(collection_name)

            query = f"""
            MATCH (n:{label} {{id: $document_id}})
            RETURN n.id
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"document_id": document_id},
                txn_id=transaction
            )

            return bool(results)

        except Exception as e:
            self.logger.error(f"❌ Check collection has document failed: {str(e)}")
            return False

    async def check_edge_exists(
        self,
        from_key: str,
        to_key: str,
        edge_collection: str,
        transaction: Optional[str] = None
    ) -> bool:
        """Check if an edge exists between two nodes"""
        try:
            relationship_type = edge_collection_to_relationship(edge_collection)

            query = f"""
            MATCH (fromNode {{id: $from_key}})-[r:{relationship_type}]->(toNode {{id: $to_key}})
            RETURN r
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"from_key": from_key, "to_key": to_key},
                txn_id=transaction
            )

            return bool(results)

        except Exception as e:
            self.logger.error(f"❌ Check edge exists failed: {str(e)}")
            return False

    async def get_failed_records_with_active_users(
        self,
        org_id: str,
        connector_id: str,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """Get failed records along with their active users who have permissions"""
        try:
            query = """
            MATCH (record:Record {orgId: $org_id, indexingStatus: 'FAILED', connectorId: $connector_id})
            OPTIONAL MATCH (user:User)-[:PERMISSION]->(record)
            WHERE user.isActive = true
            WITH record, COLLECT(DISTINCT user) AS active_users
            WHERE SIZE(active_users) > 0
            RETURN {record: record, users: active_users}
            """

            results = await self.client.execute_query(
                query,
                parameters={"org_id": org_id, "connector_id": connector_id},
                txn_id=transaction
            )

            formatted_results = []
            for r in results:
                record_data = self._neo4j_to_arango_node(dict(r["record"]), CollectionNames.RECORDS.value)
                users_data = [self._neo4j_to_arango_node(dict(u), CollectionNames.USERS.value) for u in r["users"]]
                formatted_results.append({"record": record_data, "users": users_data})

            return formatted_results

        except Exception as e:
            self.logger.error(f"❌ Get failed records with active users failed: {str(e)}")
            return []

    async def get_failed_records_by_org(
        self,
        org_id: str,
        connector_id: str,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """Get all failed records for an organization and connector"""
        try:
            return await self.get_nodes_by_filters(
                collection=CollectionNames.RECORDS.value,
                filters={
                    "orgId": org_id,
                    "indexingStatus": "FAILED",
                    "connectorId": connector_id
                },
                transaction=transaction
            )

        except Exception as e:
            self.logger.error(f"❌ Get failed records by org failed: {str(e)}")
            return []

    # ==================== Knowledge Base Operations ====================

    async def create_knowledge_base(
        self,
        kb_data: Dict,
        permission_edge: Dict,
        transaction: Optional[str] = None
    ) -> Dict:
        """Create a knowledge base with permissions"""
        try:
            kb_name = kb_data.get('groupName', 'Unknown')
            self.logger.info(f"🚀 Creating knowledge base: '{kb_name}' in Neo4j")

            # Create KB record group
            await self.batch_upsert_nodes(
                [kb_data],
                CollectionNames.RECORD_GROUPS.value,
                transaction=transaction
            )

            # Create permission edge
            await self.batch_create_edges(
                [permission_edge],
                CollectionNames.PERMISSION.value,
                transaction=transaction
            )

            kb_id = kb_data.get('id') or kb_data.get('_key')
            self.logger.info(f"✅ Knowledge base created successfully: {kb_id}")
            return {
                "id": kb_id,
                "name": kb_data.get("groupName"),
                "success": True
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to create knowledge base: {str(e)}")
            raise

    async def _get_kb_context_for_record(
        self,
        record_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Get KB context for a record."""
        try:
            self.logger.info(f"🔍 Finding KB context for record {record_id}")

            # Find KB via belongs_to edge
            query = """
            MATCH (r:Record {id: $record_id})-[b:BELONGS_TO]->(kb:RecordGroup)
            RETURN {
                kb_id: kb.id,
                kb_name: kb.groupName,
                org_id: kb.orgId
            } AS kb_context
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"record_id": record_id},
                txn_id=transaction
            )

            if results and len(results) > 0:
                return results[0].get("kb_context")

            return None

        except Exception as e:
            self.logger.error(f"Failed to get KB context: {e}")
            return None

    async def get_user_kb_permission(
        self,
        kb_id: str,
        user_id: str,
        transaction: Optional[str] = None
    ) -> Optional[str]:
        """Get user's permission role on a knowledge base"""
        try:
            self.logger.info(f"🔍 Checking permissions for user {user_id} on KB {kb_id}")

            # Check for direct user permission first
            query = """
            MATCH (u:User {id: $user_id})-[r:PERMISSION {type: "USER"}]->(kb:RecordGroup {id: $kb_id})
            RETURN r.role AS role
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"user_id": user_id, "kb_id": kb_id},
                txn_id=transaction
            )

            if results:
                role = results[0].get("role")
                self.logger.info(f"✅ Found direct permission: user {user_id} has role '{role}' on KB {kb_id}")
                return role

            # If no direct permission, check via teams
            team_query = """
            MATCH (u:User {id: $user_id})-[r1:PERMISSION {type: "USER"}]->(team:Team)
            MATCH (team)-[r2:PERMISSION {type: "TEAM"}]->(kb:RecordGroup {id: $kb_id})
            WITH r1.role AS team_role,
                 CASE r1.role
                     WHEN "OWNER" THEN 4
                     WHEN "WRITER" THEN 3
                     WHEN "READER" THEN 2
                     WHEN "COMMENTER" THEN 1
                     ELSE 0
                 END AS priority
            ORDER BY priority DESC
            RETURN team_role
            LIMIT 1
            """

            team_results = await self.client.execute_query(
                team_query,
                parameters={"user_id": user_id, "kb_id": kb_id},
                txn_id=transaction
            )

            if team_results:
                team_role = team_results[0].get("team_role")
                self.logger.info(f"✅ Found team-based permission: user {user_id} has role '{team_role}' on KB {kb_id} via teams")
                return team_role

            self.logger.warning(f"⚠️ No permission found for user {user_id} on KB {kb_id}")
            return None

        except Exception as e:
            self.logger.error(f"❌ Failed to get user KB permission: {str(e)}")
            raise

    async def get_knowledge_base(
        self,
        kb_id: str,
        user_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Get knowledge base with user permissions"""
        try:
            # First check user permissions (includes team-based access)
            user_role = await self.get_user_kb_permission(kb_id, user_id, transaction)

            # Get the KB and folders
            query = """
            MATCH (kb:RecordGroup {id: $kb_id})

            // Get folders
            // Folders are represented by RECORDS documents connected via BELONGS_TO
            // Verify it's a folder by checking associated FILES document via IS_OF_TYPE where isFile = false
            OPTIONAL MATCH (folderRecord:Record)-[:BELONGS_TO]->(kb)
            WHERE folderRecord.recordType = "FILE"
            OPTIONAL MATCH (folderRecord)-[:IS_OF_TYPE]->(folderFile:File)
            WHERE folderFile.isFile = false

            WITH kb,
                 COLLECT(DISTINCT CASE
                     WHEN folderRecord IS NOT NULL AND folderFile IS NOT NULL THEN {
                         id: folderRecord.id,
                         name: folderRecord.recordName,
                         createdAtTimestamp: folderRecord.createdAtTimestamp,
                         updatedAtTimestamp: folderRecord.updatedAtTimestamp,
                         path: folderFile.path,
                         webUrl: folderRecord.webUrl,
                         mimeType: folderRecord.mimeType,
                         sizeInBytes: folderFile.sizeInBytes
                     }
                     ELSE null
                 END) AS allFolders

            WITH kb, [f IN allFolders WHERE f IS NOT NULL] AS folders

            RETURN {
                id: kb.id,
                name: kb.groupName,
                createdAtTimestamp: kb.createdAtTimestamp,
                updatedAtTimestamp: kb.updatedAtTimestamp,
                createdBy: kb.createdBy,
                userRole: $user_role,
                folders: folders
            } AS result
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={
                    "kb_id": kb_id,
                    "user_role": user_role
                },
                txn_id=transaction
            )

            self.logger.info(f"🔍 Results: {results}")

            if results:
                result = results[0]["result"]
                # If user has no permission (neither direct nor via teams), return None
                if not user_role:
                    self.logger.warning(f"⚠️ User {user_id} has no access to KB {kb_id}")
                    return None
                self.logger.info("✅ Knowledge base retrieved successfully")
                return result
            else:
                self.logger.warning("⚠️ Knowledge base not found")
                return None

        except Exception as e:
            self.logger.error(f"❌ Failed to get knowledge base: {str(e)}")
            raise

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
        transaction: Optional[str] = None
    ) -> Tuple[List[Dict], int, Dict]:
        """
        List knowledge bases with pagination, search, and filtering.
        Includes both direct user permissions and team-based permissions.
        For team-based access, returns the highest role from all common teams.
        """
        try:
            # Build filter conditions
            filter_conditions = []

            # Search filter (using CONTAINS for LIKE-like behavior)
            if search:
                filter_conditions.append("toLower(kb.groupName) CONTAINS toLower($search_term)")

            # Permission filter (will be applied after role resolution)
            permission_filter = ""
            if permissions:
                permission_filter = " AND final_role IN $permissions"

            # Build WHERE clause for KB filtering
            additional_filters = ""
            if filter_conditions:
                additional_filters = " AND " + " AND ".join(filter_conditions)

            # Sort field mapping
            sort_field_map = {
                "name": "kb.groupName",
                "createdAtTimestamp": "kb.createdAtTimestamp",
                "updatedAtTimestamp": "kb.updatedAtTimestamp",
                "userRole": "final_role"
            }
            sort_field = sort_field_map.get(sort_by, "kb.groupName")
            sort_direction = sort_order.upper()

            # Role priority for resolving highest role

            # Main query: Get KBs with user permissions (direct and team-based)
            query = f"""
            MATCH (u:User {{id: $user_id}})

            // Get direct permissions
            OPTIONAL MATCH (u)-[r:PERMISSION {{type: "USER"}}]->(kb:RecordGroup)
            WHERE kb.orgId = $org_id
                AND kb.groupType = $kb_type
                AND kb.connectorName = $kb_connector
                {additional_filters}
            WITH u, kb, r.role AS direct_role,
                 CASE r.role
                     WHEN "OWNER" THEN 4
                     WHEN "WRITER" THEN 3
                     WHEN "READER" THEN 2
                     WHEN "COMMENTER" THEN 1
                     ELSE 0
                 END AS direct_priority,
                 true AS is_direct

            // Get team-based permissions
            OPTIONAL MATCH (u)-[r1:PERMISSION {{type: "USER"}}]->(team:Team)
            OPTIONAL MATCH (team)-[r2:PERMISSION {{type: "TEAM"}}]->(kb2:RecordGroup)
            WHERE kb2.orgId = $org_id
                AND kb2.groupType = $kb_type
                AND kb2.connectorName = $kb_connector
                {additional_filters}

            // Combine KBs (direct or team-based)
            WITH COALESCE(kb, kb2) AS kb,
                 direct_role, direct_priority, is_direct,
                 r1.role AS team_role,
                 CASE r1.role
                     WHEN "OWNER" THEN 4
                     WHEN "WRITER" THEN 3
                     WHEN "READER" THEN 2
                     WHEN "COMMENTER" THEN 1
                     ELSE 0
                 END AS team_priority

            WHERE kb IS NOT NULL

            // Resolve highest role per KB
            WITH kb,
                 COLLECT(DISTINCT {{
                     role: COALESCE(direct_role, team_role),
                     priority: COALESCE(direct_priority, team_priority),
                     is_direct: COALESCE(is_direct, false)
                 }}) AS all_roles

            WITH kb,
                 [role_info IN all_roles WHERE role_info.role IS NOT NULL] AS valid_roles

            // Sort roles by priority (highest first), then by is_direct (direct first)
            WITH kb,
                 [role_info IN valid_roles | role_info] AS sorted_roles
            ORDER BY sorted_roles[0].priority DESC, sorted_roles[0].is_direct DESC

            WITH kb, sorted_roles[0].role AS final_role

            WHERE final_role IS NOT NULL {permission_filter}

            // Get folders for all KBs
            OPTIONAL MATCH (folderRecord:Record)-[:BELONGS_TO]->(kb)
            WHERE folderRecord.recordType = "FILE"
            OPTIONAL MATCH (folderRecord)-[:IS_OF_TYPE]->(folderFile:File)
            WHERE folderFile.isFile = false

            WITH kb, final_role,
                 COLLECT(DISTINCT CASE
                     WHEN folderRecord.id IS NOT NULL AND folderFile.id IS NOT NULL THEN {{
                         id: folderRecord.id,
                         name: folderRecord.recordName,
                         createdAtTimestamp: folderRecord.createdAtTimestamp,
                         path: folderFile.path,
                         webUrl: folderRecord.webUrl
                     }}
                     ELSE null
                 END) AS allFolders

            WITH kb, final_role, [f IN allFolders WHERE f IS NOT NULL] AS folders

            ORDER BY {sort_field} {sort_direction}
            SKIP $skip
            LIMIT $limit

            RETURN {{
                id: kb.id,
                name: kb.groupName,
                createdAtTimestamp: kb.createdAtTimestamp,
                updatedAtTimestamp: kb.updatedAtTimestamp,
                createdBy: kb.createdBy,
                userRole: final_role,
                folders: folders
            }} AS result
            """

            # Count query
            count_query = f"""
            // Direct user permissions
            MATCH (u:User {{id: $user_id}})
            OPTIONAL MATCH (u)-[r:PERMISSION {{type: "USER"}}]->(kb:RecordGroup)
            WHERE kb.orgId = $org_id
                AND kb.groupType = $kb_type
                AND kb.connectorName = $kb_connector
                {additional_filters}
            WITH kb, r.role AS direct_role,
                 CASE r.role
                     WHEN "OWNER" THEN 4
                     WHEN "WRITER" THEN 3
                     WHEN "READER" THEN 2
                     WHEN "COMMENTER" THEN 1
                     ELSE 0
                 END AS direct_priority,
                 true AS is_direct
            WHERE direct_role IS NOT NULL

            // Team-based permissions
            OPTIONAL MATCH (u)-[r1:PERMISSION {{type: "USER"}}]->(team:Team)
            OPTIONAL MATCH (team)-[r2:PERMISSION {{type: "TEAM"}}]->(kb2:RecordGroup)
            WHERE kb2.orgId = $org_id
                AND kb2.groupType = $kb_type
                AND kb2.connectorName = $kb_connector
                {additional_filters}
            WITH COALESCE(kb, kb2) AS kb,
                 direct_role, direct_priority, is_direct,
                 r1.role AS team_role,
                 CASE r1.role
                     WHEN "OWNER" THEN 4
                     WHEN "WRITER" THEN 3
                     WHEN "READER" THEN 2
                     WHEN "COMMENTER" THEN 1
                     ELSE 0
                 END AS team_priority,
                 false AS is_team_direct

            // Combine and resolve highest role
            WITH kb,
                 COLLECT({{role: direct_role, priority: direct_priority, is_direct: is_direct}}) +
                 COLLECT({{role: team_role, priority: team_priority, is_direct: is_team_direct}}) AS all_roles

            WITH kb,
                 [role_info IN all_roles WHERE role_info.role IS NOT NULL] AS valid_roles

            WITH kb,
                 [role_info IN valid_roles | role_info] AS sorted_roles
            ORDER BY sorted_roles[0].priority DESC, sorted_roles[0].is_direct DESC
            WITH kb, sorted_roles[0].role AS final_role

            WHERE final_role IS NOT NULL {permission_filter}

            RETURN count(DISTINCT kb) AS total
            """

            # Filters query to get available permissions
            filters_query = """
            MATCH (u:User {id: $user_id})
            OPTIONAL MATCH (u)-[r:PERMISSION {type: "USER"}]->(kb:RecordGroup)
            WHERE kb.orgId = $org_id
                AND kb.groupType = $kb_type
                AND kb.connectorName = $kb_connector
            WITH kb, r.role AS direct_role,
                 CASE r.role
                     WHEN "OWNER" THEN 4
                     WHEN "WRITER" THEN 3
                     WHEN "READER" THEN 2
                     WHEN "COMMENTER" THEN 1
                     ELSE 0
                 END AS direct_priority,
                 true AS is_direct
            WHERE direct_role IS NOT NULL

            OPTIONAL MATCH (u)-[r1:PERMISSION {type: "USER"}]->(team:Team)
            OPTIONAL MATCH (team)-[r2:PERMISSION {type: "TEAM"}]->(kb2:RecordGroup)
            WHERE kb2.orgId = $org_id
                AND kb2.groupType = $kb_type
                AND kb2.connectorName = $kb_connector
            WITH COALESCE(kb, kb2) AS kb,
                 direct_role, direct_priority, is_direct,
                 r1.role AS team_role,
                 CASE r1.role
                     WHEN "OWNER" THEN 4
                     WHEN "WRITER" THEN 3
                     WHEN "READER" THEN 2
                     WHEN "COMMENTER" THEN 1
                     ELSE 0
                 END AS team_priority,
                 false AS is_team_direct

            WITH kb,
                 COLLECT({role: direct_role, priority: direct_priority, is_direct: is_direct}) +
                 COLLECT({role: team_role, priority: team_priority, is_direct: is_team_direct}) AS all_roles

            WITH kb,
                 [role_info IN all_roles WHERE role_info.role IS NOT NULL] AS valid_roles

            WITH kb,
                 [role_info IN valid_roles | role_info] AS sorted_roles
            ORDER BY sorted_roles[0].priority DESC, sorted_roles[0].is_direct DESC
            WITH kb, sorted_roles[0].role AS permission

            RETURN DISTINCT permission
            """

            params = {
                "user_id": user_id,
                "org_id": org_id,
                "kb_type": Connectors.KNOWLEDGE_BASE.value,
                "kb_connector": Connectors.KNOWLEDGE_BASE.value,
                "skip": skip,
                "limit": limit
            }

            if search:
                params["search_term"] = search
            if permissions:
                params["permissions"] = permissions

            # Execute queries
            results = await self.client.execute_query(query, parameters=params, txn_id=transaction)
            count_results = await self.client.execute_query(count_query, parameters=params, txn_id=transaction)
            filter_results = await self.client.execute_query(filters_query, parameters=params, txn_id=transaction)

            total_count = count_results[0]["total"] if count_results else 0

            # Format results
            kbs = []
            for r in results:
                result = r["result"]
                # Map groupName to name for API response compatibility
                if "name" not in result and "groupName" in result:
                    result["name"] = result["groupName"]
                kbs.append(result)

            # Build available filters
            available_permissions = [item["permission"] for item in filter_results if item.get("permission")]

            available_filters = {
                "permissions": list(set(available_permissions)),
                "sortFields": ["name", "createdAtTimestamp", "updatedAtTimestamp", "userRole"],
                "sortOrders": ["asc", "desc"]
            }

            self.logger.info(f"✅ Found {len(kbs)} knowledge bases out of {total_count} total (including team-based access)")
            return kbs, total_count, available_filters

        except Exception as e:
            self.logger.error(f"❌ Failed to list knowledge bases with pagination: {str(e)}")
            return [], 0, {
                "permissions": [],
                "sortFields": ["name", "createdAtTimestamp", "updatedAtTimestamp", "userRole"],
                "sortOrders": ["asc", "desc"]
            }

    async def update_knowledge_base(
        self,
        kb_id: str,
        updates: Dict,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Update knowledge base details"""
        try:
            self.logger.info(f"🚀 Updating knowledge base {kb_id}")

            # Remove id from updates if present
            updates_clean = {k: v for k, v in updates.items() if k != "id" and k != "_key"}

            query = """
            MATCH (kb:RecordGroup {id: $kb_id})
            SET kb += $updates
            RETURN kb
            """

            results = await self.client.execute_query(
                query,
                parameters={"kb_id": kb_id, "updates": updates_clean},
                txn_id=transaction
            )

            if results:
                kb_dict = dict(results[0]["kb"])
                self.logger.info("✅ Knowledge base updated successfully")
                return self._neo4j_to_arango_node(kb_dict, CollectionNames.RECORD_GROUPS.value)

            self.logger.warning("⚠️ Knowledge base not found")
            return None

        except Exception as e:
            self.logger.error(f"❌ Failed to update knowledge base: {str(e)}")
            raise

    async def delete_knowledge_base(
        self,
        kb_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Delete a knowledge base and all its contents"""
        try:
            self.logger.info(f"🚀 Deleting knowledge base {kb_id}")

            # Delete all records, folders, and relationships
            query = """
            MATCH (kb:RecordGroup {id: $kb_id})
            OPTIONAL MATCH (kb)<-[:BELONGS_TO]-(record:Record)
            OPTIONAL MATCH (record)-[*]-(related)
            DETACH DELETE kb, record, related
            RETURN count(kb) AS deleted
            """

            results = await self.client.execute_query(
                query,
                parameters={"kb_id": kb_id},
                txn_id=transaction
            )

            deleted_count = results[0]["deleted"] if results else 0

            if deleted_count > 0:
                self.logger.info(f"✅ Knowledge base {kb_id} deleted successfully")
                return {"success": True, "deleted": deleted_count}

            return None

        except Exception as e:
            self.logger.error(f"❌ Failed to delete knowledge base: {str(e)}")
            raise

    async def create_folder(
        self,
        kb_id: str,
        folder_name: str,
        org_id: str,
        parent_folder_id: Optional[str] = None,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Create folder with proper RECORDS document and IS_OF_TYPE edge.

        Creates:
        1. RECORDS document (recordType="FILES")
        2. FILES document (isFile=False)
        3. IS_OF_TYPE edge (RECORDS -> FILES)
        4. RECORD_RELATIONS edges (RECORDS -> RECORDS for parent-child)
        5. BELONGS_TO edge (RECORDS -> RECORD_GROUPS)
        """
        try:
            folder_id = str(uuid.uuid4())
            timestamp = get_epoch_timestamp_in_ms()

            location = "KB root" if parent_folder_id is None else f"folder {parent_folder_id}"
            self.logger.info(f"🚀 Creating folder '{folder_name}' in {location}")

            # Step 1: Validate parent folder exists (if nested)
            if parent_folder_id:
                parent_folder = await self.get_folder_record_by_id(parent_folder_id, transaction)
                if not parent_folder:
                    raise ValueError(f"Parent folder {parent_folder_id} not found")
                # Check if parent is actually a folder
                parent_file = await self.get_document(CollectionNames.FILES.value, parent_folder_id, transaction)
                if parent_file and parent_file.get("isFile") is not False:
                    raise ValueError(f"Parent {parent_folder_id} is not a folder")
                if parent_folder.get("connectorId") != kb_id:
                    raise ValueError(f"Parent folder does not belong to KB {kb_id}")

                self.logger.info(f"✅ Validated parent folder: {parent_folder.get('recordName')}")

            # Step 2: Check for name conflicts in the target location
            existing_folder = await self.find_folder_by_name_in_parent(
                kb_id=kb_id,
                folder_name=folder_name,
                parent_folder_id=parent_folder_id,
                transaction=transaction
            )

            if existing_folder:
                self.logger.warning(f"⚠️ Name conflict: '{folder_name}' already exists in {location}")
                return {
                    "id": existing_folder.get("id") or existing_folder.get("_key"),
                    "name": existing_folder.get("recordName") or existing_folder.get("name"),
                    "webUrl": existing_folder.get("webUrl", ""),
                    "parent_folder_id": parent_folder_id,
                    "exists": True,
                    "success": True
                }

            # Step 3: Create RECORDS document for folder
            # Determine parent: for root folders use KB ID, for nested folders use parent folder ID
            external_parent_id = parent_folder_id if parent_folder_id else kb_id

            record_data = {
                "id": folder_id,
                "orgId": org_id,
                "recordName": folder_name,
                "externalRecordId": f"kb_folder_{folder_id}",
                "connectorId": kb_id,  # Always KB ID
                "externalGroupId": kb_id,  # Always KB ID (the knowledge base)
                "externalParentId": external_parent_id,  # KB ID for root, parent folder ID for nested
                "externalRootGroupId": kb_id,  # Always KB ID (the root knowledge base)
                "recordType": RecordTypes.FILE.value,
                "version": 0,
                "origin": OriginTypes.UPLOAD.value,  # KB folders are uploaded/created locally
                "connectorName": Connectors.KNOWLEDGE_BASE.value,
                "mimeType": "application/vnd.folder",
                "webUrl": f"/kb/{kb_id}/folder/{folder_id}",
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
                "lastSyncTimestamp": timestamp,
                "sourceCreatedAtTimestamp": timestamp,
                "sourceLastModifiedTimestamp": timestamp,
                "isDeleted": False,
                "isArchived": False,
                "isVLMOcrProcessed": False,  # Required field with default
                "indexingStatus": "COMPLETED",
                "extractionStatus": "COMPLETED",
                "isLatestVersion": True,
                "isDirty": False,
            }

            self.logger.debug(
                f"Creating folder RECORDS: root={not parent_folder_id}, "
                f"parent={external_parent_id}, kb={kb_id}"
            )

            # Step 4: Create FILES document for folder (file metadata)
            folder_data = {
                "id": folder_id,
                "orgId": org_id,
                "recordGroupId": kb_id,
                "name": folder_name,
                "isFile": False,
                "extension": None,
                "mimeType": "application/vnd.folder",
                "sizeInBytes": 0,
                "webUrl": f"/kb/{kb_id}/folder/{folder_id}"
            }

            # Step 5: Insert both documents
            await self.batch_upsert_nodes([record_data], CollectionNames.RECORDS.value, transaction)
            await self.batch_upsert_nodes([folder_data], CollectionNames.FILES.value, transaction)

            # Step 6: Create IS_OF_TYPE edge (RECORDS -> FILES)
            is_of_type_edge = {
                "from_id": folder_id,
                "from_collection": CollectionNames.RECORDS.value,
                "to_id": folder_id,
                "to_collection": CollectionNames.FILES.value,
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            }
            await self.batch_create_edges([is_of_type_edge], CollectionNames.IS_OF_TYPE.value, transaction)

            # Step 7: Create relationships
            # Always create KB relationship (RECORDS -> KB)
            kb_relationship_edge = {
                "from_id": folder_id,
                "from_collection": CollectionNames.RECORDS.value,
                "to_id": kb_id,
                "to_collection": CollectionNames.RECORD_GROUPS.value,
                "entityType": Connectors.KNOWLEDGE_BASE.value,
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            }
            await self.batch_create_edges([kb_relationship_edge], CollectionNames.BELONGS_TO.value, transaction)

            # Create parent-child relationship (RECORDS -> RECORDS)
            if parent_folder_id:
                # Nested folder: Parent Record -> Child Record
                parent_child_edge = {
                    "from_id": parent_folder_id,
                    "from_collection": CollectionNames.RECORDS.value,
                    "to_id": folder_id,
                    "to_collection": CollectionNames.RECORDS.value,
                    "relationshipType": "PARENT_CHILD",
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                }
                await self.batch_create_edges([parent_child_edge], CollectionNames.RECORD_RELATIONS.value, transaction)
            else:
                # Root folder: KB -> Folder Record
                kb_parent_edge = {
                    "from_id": kb_id,
                    "from_collection": CollectionNames.RECORD_GROUPS.value,
                    "to_id": folder_id,
                    "to_collection": CollectionNames.RECORDS.value,
                    "relationshipType": "PARENT_CHILD",
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                }
                await self.batch_create_edges([kb_parent_edge], CollectionNames.RECORD_RELATIONS.value, transaction)

            self.logger.info(f"✅ Folder '{folder_name}' created successfully with RECORDS document")
            return {
                "id": folder_id,
                "name": folder_name,
                "webUrl": folder_data["webUrl"],
                "exists": False,
                "success": True
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to create folder '{folder_name}': {str(e)}")
            raise

    async def get_folder_contents(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Get contents of a folder"""
        try:
            query = """
            MATCH (folder:Record {id: $folder_id})-[:IS_OF_TYPE]->(file:File {isFile: false})
            MATCH (folder)-[:BELONGS_TO]->(kb:RecordGroup {id: $kb_id})
            OPTIONAL MATCH (folder)<-[:RECORD_RELATION {relationshipType: "PARENT_CHILD"}]-(child:Record)
            OPTIONAL MATCH (child)-[:IS_OF_TYPE]->(child_file:File)
            RETURN folder, file, collect(DISTINCT {record: child, file: child_file}) AS children
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"folder_id": folder_id, "kb_id": kb_id},
                txn_id=transaction
            )

            if results:
                r = results[0]
                folder_dict = self._neo4j_to_arango_node(dict(r["folder"]), CollectionNames.RECORDS.value)
                file_dict = self._neo4j_to_arango_node(dict(r["file"]), CollectionNames.FILES.value)

                children = []
                for child_data in r.get("children", []):
                    if child_data.get("record"):
                        child_record = self._neo4j_to_arango_node(dict(child_data["record"]), CollectionNames.RECORDS.value)
                        child_file = self._neo4j_to_arango_node(dict(child_data["file"]), CollectionNames.FILES.value) if child_data.get("file") else None
                        children.append({"record": child_record, "file": child_file})

                return {
                    "folder": folder_dict,
                    "file": file_dict,
                    "children": children
                }

            return None

        except Exception as e:
            self.logger.error(f"❌ Failed to get folder contents: {str(e)}")
            return None

    async def update_folder(
        self,
        folder_id: str,
        updates: Dict,
        transaction: Optional[str] = None
    ) -> bool:
        """Update folder details"""
        try:
            self.logger.info(f"🚀 Updating folder {folder_id}")

            updates_clean = {k: v for k, v in updates.items() if k != "id" and k != "_key"}

            # Update both Record and File nodes
            query = """
            MATCH (folder:Record {id: $folder_id})-[:IS_OF_TYPE]->(file:File)
            SET folder += $updates
            SET file += $updates
            RETURN folder
            """

            results = await self.client.execute_query(
                query,
                parameters={"folder_id": folder_id, "updates": updates_clean},
                txn_id=transaction
            )

            if results:
                self.logger.info("✅ Folder updated successfully")
                return True

            self.logger.warning("⚠️ Folder not found")
            return False

        except Exception as e:
            self.logger.error(f"❌ Failed to update folder: {str(e)}")
            raise

    async def delete_folder(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Delete a folder and all its contents"""
        try:
            self.logger.info(f"🚀 Deleting folder {folder_id} from KB {kb_id}")

            query = """
            MATCH (folder:Record {id: $folder_id})-[:BELONGS_TO]->(kb:RecordGroup {id: $kb_id})
            OPTIONAL MATCH (folder)-[*]-(related)
            DETACH DELETE folder, related
            RETURN count(folder) AS deleted
            """

            results = await self.client.execute_query(
                query,
                parameters={"folder_id": folder_id, "kb_id": kb_id},
                txn_id=transaction
            )

            deleted_count = results[0]["deleted"] if results else 0

            if deleted_count > 0:
                self.logger.info(f"✅ Folder {folder_id} deleted successfully")
                return {"success": True, "deleted": deleted_count}

            return None

        except Exception as e:
            self.logger.error(f"❌ Failed to delete folder: {str(e)}")
            raise

    async def find_folder_by_name_in_parent(
        self,
        kb_id: str,
        folder_name: str,
        parent_folder_id: Optional[str] = None,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Find a folder by name within a parent"""
        try:
            if parent_folder_id:
                query = """
                MATCH (parent:Record {id: $parent_folder_id})-[r:RECORD_RELATION {relationshipType: "PARENT_CHILD"}]->(folder:Record)
                MATCH (folder)-[:IS_OF_TYPE]->(file:File {isFile: false})
                WHERE folder.connectorId = $kb_id
                  AND toLower(folder.recordName) = toLower($folder_name)
                RETURN folder
                LIMIT 1
                """
                params = {"parent_folder_id": parent_folder_id, "kb_id": kb_id, "folder_name": folder_name}
            else:
                query = """
                MATCH (kb:RecordGroup {id: $kb_id})-[r:RECORD_RELATION {relationshipType: "PARENT_CHILD"}]->(folder:Record)
                MATCH (folder)-[:IS_OF_TYPE]->(file:File {isFile: false})
                WHERE toLower(folder.recordName) = toLower($folder_name)
                RETURN folder
                LIMIT 1
                """
                params = {"kb_id": kb_id, "folder_name": folder_name}

            results = await self.client.execute_query(query, parameters=params, txn_id=transaction)

            if results:
                folder_dict = dict(results[0]["folder"])
                return self._neo4j_to_arango_node(folder_dict, CollectionNames.RECORDS.value)

            return None

        except Exception as e:
            self.logger.error(f"❌ Failed to find folder by name: {str(e)}")
            return None

    async def validate_folder_exists_in_kb(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[str] = None
    ) -> bool:
        """Validate that a folder exists in a knowledge base"""
        try:
            query = """
            MATCH (folder:Record {id: $folder_id})-[:BELONGS_TO]->(kb:RecordGroup {id: $kb_id})
            MATCH (folder)-[:IS_OF_TYPE]->(file:File {isFile: false})
            RETURN count(folder) AS count
            """

            results = await self.client.execute_query(
                query,
                parameters={"folder_id": folder_id, "kb_id": kb_id},
                txn_id=transaction
            )

            return results[0]["count"] > 0 if results else False

        except Exception as e:
            self.logger.error(f"❌ Failed to validate folder exists: {str(e)}")
            return False

    async def validate_folder_in_kb(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[str] = None
    ) -> bool:
        """Validate that a folder exists and belongs to a knowledge base"""
        return await self.validate_folder_exists_in_kb(kb_id, folder_id, transaction)

    async def _validate_folder_creation(
        self,
        kb_id: str,
        user_id: str
    ) -> Dict:
        """Validate user permissions for folder creation"""
        try:
            # Get user
            user = await self.get_user_by_user_id(user_id=user_id)
            if not user:
                return {"valid": False, "success": False, "code": 404, "reason": f"User not found: {user_id}"}

            user_key = user.get('id') or user.get('_key')

            # Check permissions
            user_role = await self.get_user_kb_permission(kb_id, user_key)
            if user_role not in ["OWNER", "WRITER"]:
                return {
                    "valid": False,
                    "success": False,
                    "code": 403,
                    "reason": f"Insufficient permissions. Role: {user_role}"
                }

            return {
                "valid": True,
                "user": user,
                "user_key": user_key,
                "user_role": user_role
            }

        except Exception as e:
            return {"valid": False, "success": False, "code": 500, "reason": str(e)}

    async def upload_records(
        self,
        kb_id: str,
        user_id: str,
        org_id: str,
        files: List[Dict],
        parent_folder_id: Optional[str] = None,
        transaction: Optional[str] = None
    ) -> Dict:
        """Upload records/files to a knowledge base"""
        try:
            # This is a complex method - simplified implementation
            # Full implementation would handle folder structure, file validation, etc.
            self.logger.info(f"🚀 Uploading {len(files)} files to KB {kb_id}")

            timestamp = get_epoch_timestamp_in_ms()
            total_created = 0
            failed_files = []

            for file_data in files:
                try:
                    file_id = str(uuid.uuid4())

                    # Create record
                    record = {
                        "id": file_id,
                        "recordName": file_data.get("name", "Untitled"),
                        "connectorId": kb_id,
                        "orgId": org_id,
                        "recordType": "FILES",
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp,
                    }

                    # Create file
                    file_doc = {
                        "id": file_id,
                        "isFile": True,
                        "name": file_data.get("name", "Untitled"),
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp,
                    }

                    await self.batch_upsert_nodes([record], CollectionNames.RECORDS.value, transaction=transaction)
                    await self.batch_upsert_nodes([file_doc], CollectionNames.FILES.value, transaction=transaction)

                    # Create edges
                    is_of_type_edge = {
                        "from_id": file_id,
                        "from_collection": CollectionNames.RECORDS.value,
                        "to_id": file_id,
                        "to_collection": CollectionNames.FILES.value,
                    }
                    await self.batch_create_edges([is_of_type_edge], CollectionNames.IS_OF_TYPE.value, transaction=transaction)

                    belongs_to_edge = {
                        "from_id": file_id,
                        "from_collection": CollectionNames.RECORDS.value,
                        "to_id": kb_id,
                        "to_collection": CollectionNames.RECORD_GROUPS.value,
                        "entityType": Connectors.KNOWLEDGE_BASE.value,
                    }
                    await self.batch_create_edges([belongs_to_edge], CollectionNames.BELONGS_TO.value, transaction=transaction)

                    if parent_folder_id:
                        parent_child_edge = {
                            "from_id": parent_folder_id,
                            "from_collection": CollectionNames.RECORDS.value,
                            "to_id": file_id,
                            "to_collection": CollectionNames.RECORDS.value,
                            "relationshipType": "PARENT_CHILD",
                        }
                        await self.batch_create_edges([parent_child_edge], CollectionNames.RECORD_RELATIONS.value, transaction=transaction)

                    total_created += 1
                except Exception as e:
                    self.logger.error(f"Failed to upload file {file_data.get('name')}: {str(e)}")
                    failed_files.append({"name": file_data.get("name"), "error": str(e)})

            return {
                "success": True,
                "total_created": total_created,
                "failed_files": failed_files
            }

        except Exception as e:
            self.logger.error(f"❌ Upload records failed: {str(e)}")
            return {"success": False, "reason": str(e), "code": 500}

    async def delete_records(
        self,
        record_ids: List[str],
        kb_id: str,
        folder_id: Optional[str] = None,
        transaction: Optional[str] = None
    ) -> Dict:
        """Delete multiple records from a knowledge base"""
        try:
            self.logger.info(f"🚀 Deleting {len(record_ids)} records from KB {kb_id}")

            query = """
            MATCH (record:Record)
            WHERE record.id IN $record_ids
              AND record.connectorId = $kb_id
            OPTIONAL MATCH (record)-[*]-(related)
            DETACH DELETE record, related
            RETURN count(record) AS deleted
            """

            results = await self.client.execute_query(
                query,
                parameters={"record_ids": record_ids, "kb_id": kb_id},
                txn_id=transaction
            )

            deleted_count = results[0]["deleted"] if results else 0

            return {
                "success": True,
                "deleted": deleted_count
            }

        except Exception as e:
            self.logger.error(f"❌ Delete records failed: {str(e)}")
            return {"success": False, "reason": str(e)}

    async def create_kb_permissions(
        self,
        kb_id: str,
        requester_id: str,
        user_ids: List[str],
        team_ids: List[str],
        role: str,
        transaction: Optional[str] = None
    ) -> Dict:
        """Create permissions for users and teams on a knowledge base"""
        try:
            timestamp = get_epoch_timestamp_in_ms()
            granted_count = 0

            # Create user permissions
            for user_id in user_ids:
                edge = {
                    "from_id": user_id,
                    "from_collection": CollectionNames.USERS.value,
                    "to_id": kb_id,
                    "to_collection": CollectionNames.RECORD_GROUPS.value,
                    "type": "USER",
                    "role": role,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                }
                await self.batch_create_edges([edge], CollectionNames.PERMISSION.value, transaction=transaction)
                granted_count += 1

            # Create team permissions
            for team_id in team_ids:
                edge = {
                    "from_id": team_id,
                    "from_collection": CollectionNames.TEAMS.value,
                    "to_id": kb_id,
                    "to_collection": CollectionNames.RECORD_GROUPS.value,
                    "type": "TEAM",
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                }
                await self.batch_create_edges([edge], CollectionNames.PERMISSION.value, transaction=transaction)
                granted_count += 1

            return {
                "success": True,
                "grantedCount": granted_count
            }

        except Exception as e:
            self.logger.error(f"❌ Create KB permissions failed: {str(e)}")
            return {"success": False, "reason": str(e)}

    async def update_kb_permission(
        self,
        kb_id: str,
        requester_id: str,
        user_ids: List[str],
        team_ids: List[str],
        new_role: str,
        transaction: Optional[str] = None
    ) -> Dict:
        """Update permissions for users and teams on a knowledge base"""
        try:
            timestamp = get_epoch_timestamp_in_ms()

            # Update user permissions
            for user_id in user_ids:
                query = """
                MATCH (u:User {id: $user_id})-[r:PERMISSION {type: "USER"}]->(kb:RecordGroup {id: $kb_id})
                SET r.role = $new_role, r.updatedAtTimestamp = $timestamp
                RETURN r
                """
                await self.client.execute_query(
                    query,
                    parameters={"user_id": user_id, "kb_id": kb_id, "new_role": new_role, "timestamp": timestamp},
                    txn_id=transaction
                )

            return {"success": True}

        except Exception as e:
            self.logger.error(f"❌ Update KB permission failed: {str(e)}")
            return {"success": False, "reason": str(e)}

    async def remove_kb_permission(
        self,
        kb_id: str,
        user_ids: List[str],
        team_ids: List[str],
        transaction: Optional[str] = None
    ) -> Dict:
        """Remove permissions for users and teams from a knowledge base"""
        try:
            # Remove user permissions
            for user_id in user_ids:
                query = """
                MATCH (u:User {id: $user_id})-[r:PERMISSION {type: "USER"}]->(kb:RecordGroup {id: $kb_id})
                DELETE r
                """
                await self.client.execute_query(
                    query,
                    parameters={"user_id": user_id, "kb_id": kb_id},
                    txn_id=transaction
                )

            # Remove team permissions
            for team_id in team_ids:
                query = """
                MATCH (t:Team {id: $team_id})-[r:PERMISSION {type: "TEAM"}]->(kb:RecordGroup {id: $kb_id})
                DELETE r
                """
                await self.client.execute_query(
                    query,
                    parameters={"team_id": team_id, "kb_id": kb_id},
                    txn_id=transaction
                )

            return {"success": True}

        except Exception as e:
            self.logger.error(f"❌ Remove KB permission failed: {str(e)}")
            return {"success": False, "reason": str(e)}

    async def list_kb_permissions(
        self,
        kb_id: str,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """List all permissions for a knowledge base"""
        try:
            query = """
            MATCH (entity)-[r:PERMISSION]->(kb:RecordGroup {id: $kb_id})
            RETURN entity, r
            """

            results = await self.client.execute_query(
                query,
                parameters={"kb_id": kb_id},
                txn_id=transaction
            )

            permissions = []
            for r in results:
                entity = dict(r["entity"])
                rel = dict(r["r"])

                entity_type = "USER" if "User" in r["entity"].labels else "TEAM"
                entity_id = entity.get("id")

                permissions.append({
                    "entityId": entity_id,
                    "entityType": entity_type,
                    "role": rel.get("role"),
                    "type": rel.get("type")
                })

            return permissions

        except Exception as e:
            self.logger.error(f"❌ List KB permissions failed: {str(e)}")
            return []

    async def list_all_records(
        self,
        user_id: str,
        org_id: str,
        skip: int,
        limit: int,
        search: Optional[str] = None,
        record_types: Optional[List[str]] = None,
        origins: Optional[List[str]] = None,
        connectors: Optional[List[str]] = None,
        indexing_status: Optional[List[str]] = None,
        permissions: Optional[List[str]] = None,
        date_from: Optional[int] = None,
        date_to: Optional[int] = None,
        sort_by: str = "createdAtTimestamp",
        sort_order: str = "desc",
        source: str = "all",
        transaction: Optional[str] = None
    ) -> Tuple[List[Dict], int, Dict]:
        """
        List all records the user can access directly via belongs_to_kb edges.
        Returns (records, total_count, available_filters)
        """
        try:
            self.logger.info(f"🔍 Listing all records for user {user_id}, source: {source}")

            # Determine what data sources to include
            include_kb_records = source in ['all', 'local']
            include_connector_records = source in ['all', 'connector']

            # Build filter conditions - use placeholder that will be replaced with actual variable name
            def build_record_filters(var_name: str = "record") -> str:
                conditions = []
                if search:
                    conditions.append(f"(toLower({var_name}.recordName) CONTAINS toLower($search) OR toLower({var_name}.externalRecordId) CONTAINS toLower($search))")
                if record_types:
                    conditions.append(f"{var_name}.recordType IN $record_types")
                if origins:
                    conditions.append(f"{var_name}.origin IN $origins")
                if connectors:
                    conditions.append(f"{var_name}.connectorName IN $connectors")
                if indexing_status:
                    conditions.append(f"{var_name}.indexingStatus IN $indexing_status")
                if date_from:
                    conditions.append(f"{var_name}.createdAtTimestamp >= $date_from")
                if date_to:
                    conditions.append(f"{var_name}.createdAtTimestamp <= $date_to")
                return " AND " + " AND ".join(conditions) if conditions else ""

            # Build filters for KB records (using kbRecord variable)
            kb_record_filter = build_record_filters("kbRecord")
            # Build filters for connector records (using connectorRecord variable)
            connector_record_filter = build_record_filters("connectorRecord")

            base_kb_roles = {"OWNER", "READER", "FILEORGANIZER", "WRITER", "COMMENTER", "ORGANIZER"}
            if permissions:
                final_kb_roles = list(base_kb_roles.intersection(set(permissions)))
                if not final_kb_roles:
                    include_kb_records = False
            else:
                final_kb_roles = list(base_kb_roles)

            # Build permission filter for connector records
            permission_filter = ""
            if permissions:
                permission_filter = " AND permissionEdge.role IN $permissions"

            # Build a single query that handles both KB and connector records using COLLECT and UNWIND
            query = """
            MATCH (u:User {id: $user_id})

            // Collect KB records
            """

            if include_kb_records:
                query += f"""
                OPTIONAL MATCH (u)-[kbEdge:PERMISSION {{type: "USER"}}]->(kb:RecordGroup)
                WHERE kb.orgId = $org_id
                    AND kbEdge.role IN $kb_permissions
                WITH u, COLLECT({{kb: kb, role: kbEdge.role}}) AS directKbs

                OPTIONAL MATCH (u)-[userTeamPerm:PERMISSION {{type: "USER"}}]->(team:Team)
                OPTIONAL MATCH (team)-[teamKbPerm:PERMISSION {{type: "TEAM"}}]->(kb2:RecordGroup)
                WHERE kb2.orgId = $org_id
                WITH u, directKbs, COLLECT({{kb: kb2, role: userTeamPerm.role}}) AS teamKbs

                WITH u, directKbs + teamKbs AS allKbAccess
                UNWIND [access IN allKbAccess WHERE access.kb IS NOT NULL] AS kbAccess
                WITH DISTINCT u, kbAccess.kb AS kb, kbAccess.role AS kb_role

                OPTIONAL MATCH (kb)<-[:BELONGS_TO]-(kbRecord:Record)
                WHERE kbRecord.orgId = $org_id
                    AND kbRecord.isDeleted <> true
                    AND kbRecord.origin = "UPLOAD"
                    AND (kbRecord.isFile IS NULL OR kbRecord.isFile <> false)
                    {kb_record_filter}

                OPTIONAL MATCH (kbRecord)-[:IS_OF_TYPE]->(kbFile:File)

                WITH u, COLLECT({{
                    record: kbRecord,
                    permission: {{role: kb_role, type: "USER"}},
                    kb_id: kb.id,
                    kb_name: kb.groupName,
                    file: kbFile
                }}) AS kbRecords
                """
            else:
                query += """
                WITH u, [] AS kbRecords
                """

            if include_connector_records:
                query += f"""
                // Collect connector records
                OPTIONAL MATCH (u)-[permissionEdge:PERMISSION {{type: "USER"}}]->(connectorRecord:Record)
                WHERE connectorRecord.orgId = $org_id
                    AND connectorRecord.isDeleted <> true
                    AND connectorRecord.origin = "CONNECTOR"
                    {permission_filter}
                    {connector_record_filter}

                OPTIONAL MATCH (connectorRecord)-[:IS_OF_TYPE]->(connectorFile:File)

                WITH u, kbRecords, COLLECT({{
                    record: connectorRecord,
                    permission: {{role: permissionEdge.role, type: permissionEdge.type}},
                    kb_id: null,
                    kb_name: null,
                    file: connectorFile
                }}) AS connectorRecords
                """
            else:
                query += """
                WITH u, kbRecords, [] AS connectorRecords
                """

            query += f"""
            // Combine all records
            WITH kbRecords + connectorRecords AS allRecords
            UNWIND [item IN allRecords WHERE item.record IS NOT NULL] AS item

            WITH item.record AS record, item.permission AS permission, item.kb_id AS kb_id, item.kb_name AS kb_name, item.file AS file
            ORDER BY record.{sort_by} {sort_order.upper()}
            SKIP $skip
            LIMIT $limit

            RETURN {{
                id: record.id,
                externalRecordId: record.externalRecordId,
                externalRevisionId: record.externalRevisionId,
                recordName: record.recordName,
                recordType: record.recordType,
                origin: record.origin,
                connectorName: COALESCE(record.connectorName, "KNOWLEDGE_BASE"),
                indexingStatus: record.indexingStatus,
                createdAtTimestamp: record.createdAtTimestamp,
                updatedAtTimestamp: record.updatedAtTimestamp,
                sourceCreatedAtTimestamp: record.sourceCreatedAtTimestamp,
                sourceLastModifiedTimestamp: record.sourceLastModifiedTimestamp,
                orgId: record.orgId,
                version: record.version,
                isDeleted: record.isDeleted,
                deletedByUserId: record.deletedByUserId,
                isLatestVersion: COALESCE(record.isLatestVersion, true),
                webUrl: record.webUrl,
                fileRecord: CASE WHEN file IS NOT NULL THEN {{
                    id: file.id,
                    name: file.name,
                    extension: file.extension,
                    mimeType: file.mimeType,
                    sizeInBytes: file.sizeInBytes,
                    isFile: file.isFile,
                    webUrl: file.webUrl
                }} ELSE null END,
                permission: permission,
                kb: {{id: kb_id, name: kb_name}}
            }} AS result
            """

            # Count query - match BaseArangoService structure
            count_query = """
            MATCH (u:User {id: $user_id})

            // Collect KB access (direct and team-based)
            """

            if include_kb_records:
                count_query += f"""
                OPTIONAL MATCH (u)-[kbEdge:PERMISSION {{type: "USER"}}]->(kb:RecordGroup)
                WHERE kb.orgId = $org_id
                    AND kbEdge.role IN $kb_permissions
                WITH u, COLLECT({{kb: kb}}) AS directKbs

                OPTIONAL MATCH (u)-[userTeamPerm:PERMISSION {{type: "USER"}}]->(team:Team)
                OPTIONAL MATCH (team)-[teamKbPerm:PERMISSION {{type: "TEAM"}}]->(kb2:RecordGroup)
                WHERE kb2.orgId = $org_id
                WITH u, directKbs, COLLECT({{kb: kb2}}) AS teamKbs

                WITH u, directKbs + teamKbs AS allKbAccess
                UNWIND [access IN allKbAccess WHERE access.kb IS NOT NULL] AS kbAccess
                WITH DISTINCT u, kbAccess.kb AS kb

                OPTIONAL MATCH (kb)<-[:BELONGS_TO]-(kbRecord:Record)
                WHERE kbRecord.orgId = $org_id
                    AND kbRecord.isDeleted <> true
                    AND kbRecord.origin = "UPLOAD"
                    AND (kbRecord.isFile IS NULL OR kbRecord.isFile <> false)
                    {kb_record_filter}

                WITH u, count(DISTINCT kbRecord) AS kbCount
                """
            else:
                count_query += """
                WITH u, 0 AS kbCount
                """

            if include_connector_records:
                count_query += f"""
                // Count connector records
                OPTIONAL MATCH (u)-[permissionEdge:PERMISSION {{type: "USER"}}]->(connectorRecord:Record)
                WHERE connectorRecord.orgId = $org_id
                    AND connectorRecord.isDeleted <> true
                    AND connectorRecord.origin = "CONNECTOR"
                    {permission_filter}
                    {connector_record_filter}

                WITH u, kbCount, count(DISTINCT connectorRecord) AS connectorCount
                """
            else:
                count_query += """
                WITH u, kbCount, 0 AS connectorCount
                """

            count_query += """
            RETURN kbCount + connectorCount AS total
            """

            # Filters query - simplified to avoid aggregation issues
            filters_query = """
            MATCH (u:User {id: $user_id})

            // Collect KB records
            """

            if include_kb_records:
                filters_query += """
                OPTIONAL MATCH (u)-[kbEdge:PERMISSION {type: "USER"}]->(kb:RecordGroup)
                WHERE kb.orgId = $org_id
                    AND kbEdge.role IN ["OWNER", "READER", "FILEORGANIZER", "WRITER", "COMMENTER", "ORGANIZER"]
                WITH u, COLLECT({kb: kb, role: kbEdge.role}) AS directKbs

                OPTIONAL MATCH (u)-[userTeamPerm:PERMISSION {type: "USER"}]->(team:Team)
                OPTIONAL MATCH (team)-[teamKbPerm:PERMISSION {type: "TEAM"}]->(kb2:RecordGroup)
                WHERE kb2.orgId = $org_id
                WITH u, directKbs, COLLECT({kb: kb2, role: userTeamPerm.role}) AS teamKbs

                WITH u, directKbs + teamKbs AS allKbAccess
                UNWIND [access IN allKbAccess WHERE access.kb IS NOT NULL] AS kbAccess
                WITH DISTINCT u, kbAccess.kb AS kb, kbAccess.role AS kb_role

                OPTIONAL MATCH (kb)<-[:BELONGS_TO]-(kbRecord:Record)
                WHERE kbRecord.orgId = $org_id
                    AND kbRecord.isDeleted <> true
                    AND kbRecord.origin = "UPLOAD"
                    AND (kbRecord.isFile IS NULL OR kbRecord.isFile <> false)

                WITH u, COLLECT({record: kbRecord, role: kb_role}) AS kbRecords
                """
            else:
                filters_query += """
                WITH u, [] AS kbRecords
                """

            if include_connector_records:
                filters_query += """
                // Collect connector records
                OPTIONAL MATCH (u)-[permissionEdge:PERMISSION {type: "USER"}]->(connectorRecord:Record)
                WHERE connectorRecord.orgId = $org_id
                    AND connectorRecord.isDeleted <> true
                    AND connectorRecord.origin = "CONNECTOR"

                WITH u, kbRecords, COLLECT({record: connectorRecord, role: permissionEdge.role}) AS connectorRecords
                """
            else:
                filters_query += """
                WITH u, kbRecords, [] AS connectorRecords
                """

            filters_query += """
            // Combine all records
            WITH kbRecords + connectorRecords AS allRecords
            UNWIND [item IN allRecords WHERE item.record IS NOT NULL] AS item

            WITH item.record AS record, item.role AS role

            WITH COLLECT(DISTINCT record.recordType) AS recordTypes,
                 COLLECT(DISTINCT record.origin) AS origins,
                 COLLECT(DISTINCT record.connectorName) AS connectors,
                 COLLECT(DISTINCT record.indexingStatus) AS indexingStatus,
                 COLLECT(DISTINCT role) AS permissions

            RETURN {
                recordTypes: [r IN recordTypes WHERE r IS NOT NULL],
                origins: [r IN origins WHERE r IS NOT NULL],
                connectors: [r IN connectors WHERE r IS NOT NULL],
                indexingStatus: [r IN indexingStatus WHERE r IS NOT NULL],
                permissions: [r IN permissions WHERE r IS NOT NULL]
            } AS filters
            """

            # Build parameters
            params = {
                "user_id": user_id,
                "org_id": org_id,
                "skip": skip,
                "limit": limit,
                "kb_permissions": final_kb_roles
            }

            if search:
                params["search"] = search.lower()
            if record_types:
                params["record_types"] = record_types
            if origins:
                params["origins"] = origins
            if connectors:
                params["connectors"] = connectors
            if indexing_status:
                params["indexing_status"] = indexing_status
            if permissions:
                params["permissions"] = permissions
            if date_from:
                params["date_from"] = date_from
            if date_to:
                params["date_to"] = date_to

            # Execute queries
            results = await self.client.execute_query(query, parameters=params, txn_id=transaction)
            count_results = await self.client.execute_query(count_query, parameters=params, txn_id=transaction)
            filter_results = await self.client.execute_query(filters_query, parameters=params, txn_id=transaction)

            # Handle None results
            if results is None:
                results = []
            if count_results is None:
                count_results = []
            if filter_results is None:
                filter_results = []

            # Format records
            records = []
            for r in results:
                if r and "result" in r:
                    result = r["result"]
                    # Convert Neo4j node format to Arango format
                    if "id" in result:
                        result = self._neo4j_to_arango_node(result, CollectionNames.RECORDS.value)
                    records.append(result)

            total_count = count_results[0]["total"] if count_results and len(count_results) > 0 else 0

            # Format available filters
            available_filters = filter_results[0]["filters"] if filter_results and len(filter_results) > 0 else {}
            if not available_filters:
                available_filters = {}
            available_filters.setdefault("recordTypes", [])
            available_filters.setdefault("origins", [])
            available_filters.setdefault("connectors", [])
            available_filters.setdefault("indexingStatus", [])
            available_filters.setdefault("permissions", [])

            self.logger.info(f"✅ Found {len(records)} records out of {total_count} total")
            return records, total_count, available_filters

        except Exception as e:
            self.logger.error(f"❌ List all records failed: {str(e)}")
            return [], 0, {
                "recordTypes": [],
                "origins": [],
                "connectors": [],
                "indexingStatus": [],
                "permissions": []
            }

    async def list_kb_records(
        self,
        kb_id: str,
        user_id: str,
        org_id: str,
        skip: int,
        limit: int,
        search: Optional[str] = None,
        record_types: Optional[List[str]] = None,
        origins: Optional[List[str]] = None,
        connectors: Optional[List[str]] = None,
        indexing_status: Optional[List[str]] = None,
        date_from: Optional[int] = None,
        date_to: Optional[int] = None,
        sort_by: str = "createdAtTimestamp",
        sort_order: str = "desc",
        folder_id: Optional[str] = None,
        transaction: Optional[str] = None
    ) -> Tuple[List[Dict], int, Dict]:
        """
        List all records in a specific KB through folder structure for better folder-based filtering.
        """
        try:
            self.logger.info(f"🔍 Listing records for KB {kb_id} (folder-based)")

            # Check user permissions first (includes team-based access)
            user_permission = await self.get_user_kb_permission(kb_id, user_id, transaction)
            if not user_permission:
                self.logger.warning(f"⚠️ User {user_id} has no access to KB {kb_id} (neither direct nor via teams)")
                return [], 0, {
                    "recordTypes": [],
                    "origins": [],
                    "connectors": [],
                    "indexingStatus": [],
                    "permissions": [],
                    "folders": []
                }

            # Build filter conditions
            record_conditions = []
            params = {
                "kb_id": kb_id,
                "org_id": org_id,
                "user_permission": user_permission,
                "skip": skip,
                "limit": limit
            }

            if search:
                record_conditions.append("(toLower(record.recordName) CONTAINS toLower($search) OR toLower(record.externalRecordId) CONTAINS toLower($search))")
                params["search"] = search.lower()
            if record_types:
                record_conditions.append("record.recordType IN $record_types")
                params["record_types"] = record_types
            if origins:
                record_conditions.append("record.origin IN $origins")
                params["origins"] = origins
            if connectors:
                record_conditions.append("record.connectorName IN $connectors")
                params["connectors"] = connectors
            if indexing_status:
                record_conditions.append("record.indexingStatus IN $indexing_status")
                params["indexing_status"] = indexing_status
            if date_from:
                record_conditions.append("record.createdAtTimestamp >= $date_from")
                params["date_from"] = date_from
            if date_to:
                record_conditions.append("record.createdAtTimestamp <= $date_to")
                params["date_to"] = date_to

            record_filter = " AND " + " AND ".join(record_conditions) if record_conditions else ""

            folder_match = ""
            if folder_id:
                folder_match = " AND folder.id = $folder_id"
                params["folder_id"] = folder_id

            # Main query - get all records from folders
            main_query = f"""
            MATCH (kb:RecordGroup {{id: $kb_id}})
            MATCH (folder:Record)-[:BELONGS_TO]->(kb)
            WHERE folder.isFile = false{folder_match}
            MATCH (folder)-[rel:RECORD_RELATION {{relationshipType: "PARENT_CHILD"}}]->(record:Record)
            WHERE record.isDeleted <> true
            AND record.orgId = $org_id
            AND record.isFile <> false
            {record_filter}
            OPTIONAL MATCH (record)-[:IS_OF_TYPE]->(file:File)

            WITH folder, record, file, $user_permission AS user_permission, $kb_id AS kb_id

            RETURN {{
                id: record.id,
                externalRecordId: record.externalRecordId,
                externalRevisionId: record.externalRevisionId,
                recordName: record.recordName,
                recordType: record.recordType,
                origin: record.origin,
                connectorName: COALESCE(record.connectorName, "KNOWLEDGE_BASE"),
                indexingStatus: record.indexingStatus,
                createdAtTimestamp: record.createdAtTimestamp,
                updatedAtTimestamp: record.updatedAtTimestamp,
                sourceCreatedAtTimestamp: record.sourceCreatedAtTimestamp,
                sourceLastModifiedTimestamp: record.sourceLastModifiedTimestamp,
                orgId: record.orgId,
                version: record.version,
                isDeleted: record.isDeleted,
                deletedByUserId: record.deletedByUserId,
                isLatestVersion: COALESCE(record.isLatestVersion, true),
                webUrl: record.webUrl,
                fileRecord: CASE WHEN file IS NOT NULL THEN {{
                    id: file.id,
                    name: file.name,
                    extension: file.extension,
                    mimeType: file.mimeType,
                    sizeInBytes: file.sizeInBytes,
                    isFile: file.isFile,
                    webUrl: file.webUrl
                }} ELSE null END,
                permission: {{role: user_permission, type: "USER"}},
                kb_id: kb_id,
                folder: {{id: folder.id, name: folder.recordName}}
            }} AS result

            ORDER BY result.{sort_by} {sort_order.upper()}
            SKIP $skip
            LIMIT $limit
            """

            results = await self.client.execute_query(main_query, parameters=params, txn_id=transaction)
            records = [r["result"] for r in results if r.get("result")]

            # Count query
            count_params = {k: v for k, v in params.items() if k not in ["skip", "limit", "user_permission"]}
            count_query = f"""
            MATCH (kb:RecordGroup {{id: $kb_id}})
            MATCH (folder:Record)-[:BELONGS_TO]->(kb)
            WHERE folder.isFile = false{folder_match}
            MATCH (folder)-[:RECORD_RELATION {{relationshipType: "PARENT_CHILD"}}]->(record:Record)
            WHERE record.isDeleted <> true
            AND record.orgId = $org_id
            AND record.isFile <> false
            {record_filter}
            RETURN count(DISTINCT record) AS total
            """

            count_results = await self.client.execute_query(count_query, parameters=count_params, txn_id=transaction)
            total_count = count_results[0]["total"] if count_results else 0

            # Filters query - get available filter values
            filters_params = {
                "kb_id": kb_id,
                "org_id": org_id,
                "user_permission": user_permission
            }
            filters_query = """
            MATCH (kb:RecordGroup {id: $kb_id})
            MATCH (folder:Record)-[:BELONGS_TO]->(kb)
            WHERE folder.isFile = false
            MATCH (folder)-[:RECORD_RELATION {relationshipType: "PARENT_CHILD"}]->(record:Record)
            WHERE record.isDeleted <> true
            AND record.orgId = $org_id
            AND record.isFile <> false

            WITH DISTINCT record, folder

            WITH collect(DISTINCT record.recordType) AS recordTypes,
                 collect(DISTINCT record.origin) AS origins,
                 collect(DISTINCT record.connectorName) AS connectors,
                 collect(DISTINCT record.indexingStatus) AS indexingStatus,
                 collect(DISTINCT {id: folder.id, name: folder.recordName}) AS folders

            RETURN {
                recordTypes: [r IN recordTypes WHERE r IS NOT NULL],
                origins: [o IN origins WHERE o IS NOT NULL],
                connectors: [c IN connectors WHERE c IS NOT NULL],
                indexingStatus: [i IN indexingStatus WHERE i IS NOT NULL],
                permissions: [$user_permission],
                folders: [f IN folders WHERE f.id IS NOT NULL]
            } AS filters
            """

            filters_results = await self.client.execute_query(filters_query, parameters=filters_params, txn_id=transaction)
            available_filters = filters_results[0]["filters"] if filters_results else {}

            # Ensure filter structure
            if not available_filters:
                available_filters = {}
            available_filters.setdefault("recordTypes", [])
            available_filters.setdefault("origins", [])
            available_filters.setdefault("connectors", [])
            available_filters.setdefault("indexingStatus", [])
            available_filters.setdefault("permissions", [user_permission] if user_permission else [])
            available_filters.setdefault("folders", [])

            self.logger.info(f"✅ Listed {len(records)} KB records out of {total_count} total")
            return records, total_count, available_filters

        except Exception as e:
            self.logger.error(f"❌ Failed to list KB records: {str(e)}")
            return [], 0, {
                "recordTypes": [],
                "origins": [],
                "connectors": [],
                "indexingStatus": [],
                "permissions": [],
                "folders": []
            }

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
        transaction: Optional[str] = None
    ) -> Dict:
        """
        Get KB root contents with folders_first pagination and level order traversal
        Folders First Logic:
        - Show ALL folders first (within page limits)
        - Then show records in remaining space
        - If folders exceed page limit, paginate folders only
        - If folders fit in page, fill remaining space with records
        """
        try:
            self.logger.info(f"🔍 Getting KB {kb_id} children with folders_first pagination (skip={skip}, limit={limit}, level={level})")

            # Get KB info first
            kb = await self.get_document(kb_id, CollectionNames.RECORD_GROUPS.value)
            if not kb:
                return {"success": False, "reason": "Knowledge base not found"}

            # Build filter conditions
            folder_conditions = []
            record_conditions = []
            params = {
                "kb_id": kb_id,
                "skip": skip,
                "limit": limit,
                "level": level
            }

            if search:
                folder_conditions.append("toLower(folder_record.recordName) CONTAINS toLower($search)")
                record_conditions.append("(toLower(record.recordName) CONTAINS toLower($search) OR toLower(record.externalRecordId) CONTAINS toLower($search))")
                params["search"] = search.lower()
            if record_types:
                record_conditions.append("record.recordType IN $record_types")
                params["record_types"] = record_types
            if origins:
                record_conditions.append("record.origin IN $origins")
                params["origins"] = origins
            if connectors:
                record_conditions.append("record.connectorName IN $connectors")
                params["connectors"] = connectors
            if indexing_status:
                record_conditions.append("record.indexingStatus IN $indexing_status")
                params["indexing_status"] = indexing_status

            folder_filter = " AND " + " AND ".join(folder_conditions) if folder_conditions else ""
            record_filter = " AND " + " AND ".join(record_conditions) if record_conditions else ""

            # Sort field mapping for records (folders always sorted by name)
            record_sort_map = {
                "name": "record.recordName",
                "created_at": "record.createdAtTimestamp",
                "updated_at": "record.updatedAtTimestamp",
                "size": "file.sizeInBytes"
            }
            record_sort_field = record_sort_map.get(sort_by, "record.recordName")
            sort_direction = sort_order.upper() if sort_order.upper() in ["ASC", "DESC"] else "ASC"

            # Query to get all folders (with level traversal)
            # Note: Neo4j doesn't support parameterized variable-length relationships, so we build it dynamically
            folders_query = f"""
            MATCH (kb:RecordGroup {{id: $kb_id}})
            // Get folders at different levels using variable-length path
            MATCH path = (kb)-[rels:RECORD_RELATION*1..{level}]->(folder_record:Record)
            WHERE ALL(rel IN rels WHERE rel.relationshipType = "PARENT_CHILD")
            MATCH (folder_record)-[:IS_OF_TYPE]->(folder_file:File)
            WHERE folder_file.isFile = false
            {folder_filter}
            WITH folder_record, folder_file, path, size(relationships(path)) AS current_level
            // Get counts for this folder (direct children only)
            OPTIONAL MATCH (folder_record)-[:RECORD_RELATION {{relationshipType: "PARENT_CHILD"}}]->(child_record:Record)
            OPTIONAL MATCH (child_record)-[:IS_OF_TYPE]->(child_file:File)
            WITH folder_record, folder_file, current_level, path,
                 sum(CASE WHEN child_file IS NOT NULL AND child_file.isFile = false THEN 1 ELSE 0 END) AS direct_subfolders,
                 sum(CASE WHEN child_record IS NOT NULL AND child_record.isDeleted <> true AND (child_file IS NULL OR child_file.isFile <> false) THEN 1 ELSE 0 END) AS direct_records
            // Get parent_id from path (last Record node before folder_record)
            WITH folder_record, folder_file, current_level, path, direct_subfolders, direct_records,
                 CASE
                     WHEN size(nodes(path)) > 2
                     THEN [n IN nodes(path)[0..-1] WHERE n:Record | n.id][-1]
                     ELSE null
                 END AS parent_id
            ORDER BY folder_record.recordName ASC
            RETURN {{
                id: folder_record.id,
                name: folder_record.recordName,
                path: folder_file.path,
                level: current_level,
                parent_id: parent_id,
                webUrl: folder_record.webUrl,
                recordGroupId: folder_record.connectorId,
                type: "folder",
                createdAtTimestamp: folder_record.createdAtTimestamp,
                updatedAtTimestamp: folder_record.updatedAtTimestamp,
                counts: {{
                    subfolders: direct_subfolders,
                    records: direct_records,
                    totalItems: direct_subfolders + direct_records
                }},
                hasChildren: direct_subfolders > 0 OR direct_records > 0
            }} AS folder
            """

            # Query to get all records directly in KB root (excluding folders)
            records_query = f"""
            MATCH (kb:RecordGroup {{id: $kb_id}})-[:RECORD_RELATION {{relationshipType: "PARENT_CHILD"}}]->(record:Record)
            WHERE record.isDeleted <> true
            // Exclude folders by checking if there's a File with isFile = false
            OPTIONAL MATCH (record)-[:IS_OF_TYPE]->(check_file:File)
            WHERE check_file.isFile = false
            WITH record, check_file
            WHERE check_file IS NULL
            {record_filter}
            OPTIONAL MATCH (record)-[:IS_OF_TYPE]->(file:File)
            WITH record, file
            ORDER BY {record_sort_field} {sort_direction}
            RETURN {{
                id: record.id,
                recordName: record.recordName,
                name: record.recordName,
                recordType: record.recordType,
                externalRecordId: record.externalRecordId,
                origin: record.origin,
                connectorName: COALESCE(record.connectorName, "KNOWLEDGE_BASE"),
                indexingStatus: record.indexingStatus,
                version: record.version,
                isLatestVersion: COALESCE(record.isLatestVersion, true),
                createdAtTimestamp: record.createdAtTimestamp,
                updatedAtTimestamp: record.updatedAtTimestamp,
                sourceCreatedAtTimestamp: record.sourceCreatedAtTimestamp,
                sourceLastModifiedTimestamp: record.sourceLastModifiedTimestamp,
                webUrl: record.webUrl,
                orgId: record.orgId,
                type: "record",
                fileRecord: CASE WHEN file IS NOT NULL THEN {{
                    id: file.id,
                    name: file.name,
                    extension: file.extension,
                    mimeType: file.mimeType,
                    sizeInBytes: file.sizeInBytes,
                    webUrl: file.webUrl,
                    path: file.path,
                    isFile: file.isFile
                }} ELSE null END
            }} AS record
            """

            # Execute queries
            folders_results = await self.client.execute_query(folders_query, parameters=params, txn_id=transaction)
            records_results = await self.client.execute_query(records_query, parameters=params, txn_id=transaction)

            all_folders = [r["folder"] for r in folders_results if r.get("folder")]
            all_records = [r["record"] for r in records_results if r.get("record")]

            total_folders = len(all_folders)
            total_records = len(all_records)
            total_count = total_folders + total_records

            # Folders First Pagination Logic
            if skip < total_folders:
                # Show folders from skip position
                paginated_folders = all_folders[skip:skip + limit]
                folders_shown = len(paginated_folders)
                remaining_limit = limit - folders_shown
                record_skip = 0
                record_limit = remaining_limit if remaining_limit > 0 else 0
            else:
                # Skip folders entirely, show only records
                paginated_folders = []
                folders_shown = 0
                record_skip = skip - total_folders
                record_limit = limit

            paginated_records = all_records[record_skip:record_skip + record_limit] if record_limit > 0 else []

            # Get available filters from all records
            available_filters = {
                "recordTypes": list(set([r.get("recordType") for r in all_records if r.get("recordType")])),
                "origins": list(set([r.get("origin") for r in all_records if r.get("origin")])),
                "connectors": list(set([r.get("connectorName") for r in all_records if r.get("connectorName")])),
                "indexingStatus": list(set([r.get("indexingStatus") for r in all_records if r.get("indexingStatus")]))
            }

            # Build response
            result = {
                "success": True,
                "container": {
                    "id": kb.get("id") or kb.get("_key"),
                    "name": kb.get("groupName") or kb.get("name"),
                    "path": "/",
                    "type": "kb",
                    "webUrl": f"/kb/{kb.get('id') or kb.get('_key')}",
                    "recordGroupId": kb.get("id") or kb.get("_key")
                },
                "folders": paginated_folders,
                "records": paginated_records,
                "level": level,
                "totalCount": total_count,
                "counts": {
                    "folders": len(paginated_folders),
                    "records": len(paginated_records),
                    "totalItems": len(paginated_folders) + len(paginated_records),
                    "totalFolders": total_folders,
                    "totalRecords": total_records
                },
                "availableFilters": available_filters,
                "paginationMode": "folders_first"
            }

            self.logger.info(f"✅ Retrieved KB children with folders_first pagination: {result['counts']['totalItems']} items")
            return result

        except Exception as e:
            self.logger.error(f"❌ Failed to get KB children with folders_first pagination: {str(e)}")
            return {"success": False, "reason": str(e)}

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
        transaction: Optional[str] = None
    ) -> Dict:
        """Get folder contents with pagination and filters"""
        try:
            where_clauses = []
            if search:
                where_clauses.append("toLower(item.recordName) CONTAINS toLower($search)")

            where_clause = " AND ".join(where_clauses) if where_clauses else "true"

            query = f"""
            MATCH (folder:Record {{id: $folder_id}})-[:RECORD_RELATION {{relationshipType: "PARENT_CHILD"}}]->(item:Record)
            WHERE {where_clause}
            OPTIONAL MATCH (item)-[:IS_OF_TYPE]->(file:File)
            RETURN item, file
            SKIP $skip
            LIMIT $limit
            """

            params = {"folder_id": folder_id, "skip": skip, "limit": limit}
            if search:
                params["search"] = search

            results = await self.client.execute_query(query, parameters=params, txn_id=transaction)

            folders = []
            records = []

            for r in results:
                item = self._neo4j_to_arango_node(dict(r["item"]), CollectionNames.RECORDS.value)
                file = self._neo4j_to_arango_node(dict(r["file"]), CollectionNames.FILES.value) if r.get("file") else None

                if file and not file.get("isFile", True):
                    folders.append({"record": item, "file": file})
                else:
                    records.append({"record": item, "file": file})

            return {
                "success": True,
                "folders": folders,
                "records": records,
                "counts": {"folders": len(folders), "records": len(records)},
                "totalCount": len(folders) + len(records)
            }

        except Exception as e:
            self.logger.error(f"❌ Get folder children failed: {str(e)}")
            return {"success": False, "reason": str(e)}

    # ==================== Knowledge Hub Operations ====================

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
        try:
            # Parse node_id to get collection and key
            if "/" in node_id:
                collection, key = node_id.split("/", 1)
            else:
                # Try to determine collection from context
                collection = "records"  # Default fallback
                key = node_id

            label = collection_to_label(collection)
            rel_type = self._get_relationship_type(edge_collection)

            query = f"""
            MATCH (source:{label} {{id: $key}})-[r:{rel_type}]->(target)
            RETURN r, labels(target) AS target_labels, target.id AS target_id
            """

            results = await self.client.execute_query(
                query,
                parameters={"key": key},
                txn_id=transaction
            )

            edges = []
            for result in results:
                edge_dict = dict(result.get("r", {}))
                target_labels = result.get("target_labels", [])
                target_id = result.get("target_id")

                # Determine target collection from labels
                target_collection = "records"  # Default
                for label in target_labels:
                    if label in ["RecordGroup", "App", "User", "Group", "Team", "File"]:
                        # Map label back to collection
                        label_to_collection = {
                            "RecordGroup": "recordGroups",
                            "App": "apps",
                            "User": "users",
                            "Group": "groups",
                            "Team": "teams",
                            "File": "files"
                        }
                        target_collection = label_to_collection.get(label, "records")
                        break

                # Convert to ArangoDB edge format
                edge_dict["_from"] = node_id
                edge_dict["_to"] = f"{target_collection}/{target_id}"
                edges.append(edge_dict)

            return edges

        except Exception as e:
            self.logger.error(f"❌ Get edges from node failed: {str(e)}")
            return []

    # ==================== Missing Abstract Methods Implementation ====================

    async def batch_update_connector_status(
        self,
        connector_ids: List[str],
        is_active: bool,
        transaction: Optional[str] = None
    ) -> int:
        """Batch update connector status."""
        try:
            query = """
            UNWIND $connector_ids AS connector_id
            MATCH (app:App {id: connector_id})
            SET app.isActive = $is_active
            RETURN count(app) as updated_count
            """
            results = await self.client.execute_query(
                query,
                parameters={"connector_ids": connector_ids, "is_active": is_active},
                txn_id=transaction
            )
            return results[0].get("updated_count", 0) if results else 0
        except Exception as e:
            self.logger.error(f"❌ Batch update connector status failed: {str(e)}")
            return 0

    async def batch_upsert_people(
        self,
        people: List[Dict],
        collection: str,
        transaction: Optional[str] = None
    ) -> Optional[bool]:
        """Batch upsert people nodes."""
        try:
            label = collection_to_label(collection)
            query = f"""
            UNWIND $people AS person
            MERGE (p:{label} {{id: person.id}})
            SET p += person
            RETURN count(p) as count
            """
            await self.client.execute_query(
                query,
                parameters={"people": people},
                txn_id=transaction
            )
            return True
        except Exception as e:
            self.logger.error(f"❌ Batch upsert people failed: {str(e)}")
            return False

    async def check_connector_name_exists(
        self,
        connector_name: str,
        org_id: str,
        exclude_connector_id: Optional[str] = None,
        transaction: Optional[str] = None
    ) -> bool:
        """Check if connector name exists in org."""
        try:
            query = """
            MATCH (app:App {name: $connector_name, orgId: $org_id})
            WHERE $exclude_connector_id IS NULL OR app.id <> $exclude_connector_id
            RETURN count(app) > 0 as exists
            """
            results = await self.client.execute_query(
                query,
                parameters={
                    "connector_name": connector_name,
                    "org_id": org_id,
                    "exclude_connector_id": exclude_connector_id
                },
                txn_id=transaction
            )
            return results[0].get("exists", False) if results else False
        except Exception as e:
            self.logger.error(f"❌ Check connector name exists failed: {str(e)}")
            return False

    async def count_kb_owners(
        self,
        kb_id: str,
        transaction: Optional[str] = None
    ) -> int:
        """Count number of owners for a KB."""
        try:
            query = """
            MATCH (u:User)-[p:PERMISSION {role: "OWNER"}]->(kb:RecordGroup {id: $kb_id})
            RETURN count(DISTINCT u) as owner_count
            """
            results = await self.client.execute_query(
                query,
                parameters={"kb_id": kb_id},
                txn_id=transaction
            )
            return results[0].get("owner_count", 0) if results else 0
        except Exception as e:
            self.logger.error(f"❌ Count KB owners failed: {str(e)}")
            return 0

    async def create_parent_child_edge(
        self,
        parent_id: str,
        child_id: str,
        parent_collection: str,
        child_collection: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> bool:
        """Create parent-child relationship edge."""
        try:
            parent_label = collection_to_label(parent_collection)
            child_label = collection_to_label(child_collection)
            rel_type = edge_collection_to_relationship(collection)

            query = f"""
            MATCH (parent:{parent_label} {{id: $parent_id}})
            MATCH (child:{child_label} {{id: $child_id}})
            MERGE (child)-[r:{rel_type} {{relationshipType: "PARENT_CHILD"}}]->(parent)
            RETURN count(r) > 0 as created
            """
            results = await self.client.execute_query(
                query,
                parameters={"parent_id": parent_id, "child_id": child_id},
                txn_id=transaction
            )
            return results[0].get("created", False) if results else False
        except Exception as e:
            self.logger.error(f"❌ Create parent-child edge failed: {str(e)}")
            return False

    async def delete_edges_by_relationship_types(
        self,
        record_id: str,
        relationship_types: List[str],
        collection: str,
        transaction: Optional[str] = None
    ) -> int:
        """Delete edges by relationship types."""
        try:
            rel_type = edge_collection_to_relationship(collection)
            query = f"""
            MATCH (r:Record {{id: $record_id}})-[rel:{rel_type}]-()
            WHERE rel.relationshipType IN $relationship_types
            DELETE rel
            RETURN count(rel) as deleted_count
            """
            results = await self.client.execute_query(
                query,
                parameters={"record_id": record_id, "relationship_types": relationship_types},
                txn_id=transaction
            )
            return results[0].get("deleted_count", 0) if results else 0
        except Exception as e:
            self.logger.error(f"❌ Delete edges by relationship types failed: {str(e)}")
            return 0

    async def delete_parent_child_edge_to_record(
        self,
        record_id: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> bool:
        """Delete parent-child edge to a record."""
        try:
            rel_type = edge_collection_to_relationship(collection)
            query = f"""
            MATCH (child:Record {{id: $record_id}})-[r:{rel_type} {{relationshipType: "PARENT_CHILD"}}]->()
            DELETE r
            RETURN count(r) > 0 as deleted
            """
            results = await self.client.execute_query(
                query,
                parameters={"record_id": record_id},
                txn_id=transaction
            )
            return results[0].get("deleted", False) if results else False
        except Exception as e:
            self.logger.error(f"❌ Delete parent-child edge failed: {str(e)}")
            return False

    async def ensure_schema(self) -> bool:
        """Ensure Neo4j schema (indexes and constraints)."""
        try:
            self.logger.info("🔧 Ensuring Neo4j schema...")

            # Create constraints and indexes
            constraints = [
                "CREATE CONSTRAINT IF NOT EXISTS FOR (u:User) REQUIRE u.id IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (r:Record) REQUIRE r.id IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (rg:RecordGroup) REQUIRE rg.id IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (a:App) REQUIRE a.id IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (o:Org) REQUIRE o.id IS UNIQUE",
            ]

            indexes = [
                "CREATE INDEX IF NOT EXISTS FOR (r:Record) ON (r.orgId)",
                "CREATE INDEX IF NOT EXISTS FOR (r:Record) ON (r.connectorId)",
                "CREATE INDEX IF NOT EXISTS FOR (r:Record) ON (r.externalRecordId)",
                "CREATE INDEX IF NOT EXISTS FOR (rg:RecordGroup) ON (rg.orgId)",
                "CREATE INDEX IF NOT EXISTS FOR (a:App) ON (a.orgId)",
            ]

            for constraint in constraints:
                try:
                    await self.client.execute_query(constraint, parameters={})
                except Exception as e:
                    self.logger.warning(f"Constraint creation warning: {str(e)}")

            for index in indexes:
                try:
                    await self.client.execute_query(index, parameters={})
                except Exception as e:
                    self.logger.warning(f"Index creation warning: {str(e)}")

            self.logger.info("✅ Neo4j schema ensured")
            return True
        except Exception as e:
            self.logger.error(f"❌ Ensure schema failed: {str(e)}")
            return False

    async def get_filtered_connector_instances(
        self,
        org_id: str,
        connector_name: Optional[str] = None,
        is_active: Optional[bool] = None,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """Get filtered connector instances."""
        try:
            conditions = ["app.orgId = $org_id"]
            params = {"org_id": org_id}

            if connector_name:
                conditions.append("app.name = $connector_name")
                params["connector_name"] = connector_name

            if is_active is not None:
                conditions.append("app.isActive = $is_active")
                params["is_active"] = is_active

            where_clause = " AND ".join(conditions)

            query = f"""
            MATCH (app:App)
            WHERE {where_clause}
            RETURN app
            """
            results = await self.client.execute_query(
                query,
                parameters=params,
                txn_id=transaction
            )
            return [r.get("app", {}) for r in results]
        except Exception as e:
            self.logger.error(f"❌ Get filtered connector instances failed: {str(e)}")
            return []

    async def get_kb_permissions(
        self,
        kb_id: str,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """Get all permissions for a KB."""
        try:
            query = """
            MATCH (entity)-[p:PERMISSION]->(kb:RecordGroup {id: $kb_id})
            RETURN {
                entityId: entity.id,
                entityType: labels(entity)[0],
                role: p.role,
                type: p.type
            } as permission
            """
            results = await self.client.execute_query(
                query,
                parameters={"kb_id": kb_id},
                txn_id=transaction
            )
            return [r.get("permission", {}) for r in results]
        except Exception as e:
            self.logger.error(f"❌ Get KB permissions failed: {str(e)}")
            return []

    async def get_record_by_external_revision_id(
        self,
        connector_id: str,
        external_revision_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Record]:
        """Get record by external revision ID."""
        try:
            query = """
            MATCH (r:Record {connectorId: $connector_id, externalRevisionId: $external_revision_id})
            RETURN r
            LIMIT 1
            """
            results = await self.client.execute_query(
                query,
                parameters={
                    "connector_id": connector_id,
                    "external_revision_id": external_revision_id
                },
                txn_id=transaction
            )
            if results:
                record_data = results[0].get("r", {})
                return self._create_typed_record_from_neo4j(record_data)
            return None
        except Exception as e:
            self.logger.error(f"❌ Get record by external revision ID failed: {str(e)}")
            return None

    async def get_record_by_weburl(
        self,
        connector_id: str,
        web_url: str,
        transaction: Optional[str] = None
    ) -> Optional[Record]:
        """Get record by web URL."""
        try:
            query = """
            MATCH (r:Record {connectorId: $connector_id, webUrl: $web_url})
            RETURN r
            LIMIT 1
            """
            results = await self.client.execute_query(
                query,
                parameters={"connector_id": connector_id, "web_url": web_url},
                txn_id=transaction
            )
            if results:
                record_data = results[0].get("r", {})
                return self._create_typed_record_from_neo4j(record_data)
            return None
        except Exception as e:
            self.logger.error(f"❌ Get record by web URL failed: {str(e)}")
            return None

    async def get_record_parent_info(
        self,
        record_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Get parent information for a record."""
        try:
            query = """
            MATCH (r:Record {id: $record_id})-[:RECORD_RELATION {relationshipType: "PARENT_CHILD"}]->(parent:Record)
            RETURN {
                id: parent.id,
                name: parent.recordName,
                type: parent.recordType
            } as parent_info
            LIMIT 1
            """
            results = await self.client.execute_query(
                query,
                parameters={"record_id": record_id},
                txn_id=transaction
            )
            return results[0].get("parent_info") if results else None
        except Exception as e:
            self.logger.error(f"❌ Get record parent info failed: {str(e)}")
            return None

    async def get_records(
        self,
        record_ids: List[str],
        transaction: Optional[str] = None
    ) -> List[Record]:
        """Get multiple records by IDs."""
        try:
            query = """
            UNWIND $record_ids AS record_id
            MATCH (r:Record {id: record_id})
            RETURN r
            """
            results = await self.client.execute_query(
                query,
                parameters={"record_ids": record_ids},
                txn_id=transaction
            )
            records = []
            for result in results:
                record_data = result.get("r", {})
                typed_record = self._create_typed_record_from_neo4j(record_data)
                if typed_record:
                    records.append(typed_record)
            return records
        except Exception as e:
            self.logger.error(f"❌ Get records failed: {str(e)}")
            return []

    async def get_user_connector_instances(
        self,
        user_key: str,
        org_id: str,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """Get connector instances accessible by user."""
        try:
            query = """
            MATCH (u:User {id: $user_key})-[:PERMISSION]->(app:App {orgId: $org_id})
            RETURN app
            """
            results = await self.client.execute_query(
                query,
                parameters={"user_key": user_key, "org_id": org_id},
                txn_id=transaction
            )
            return [r.get("app", {}) for r in results]
        except Exception as e:
            self.logger.error(f"❌ Get user connector instances failed: {str(e)}")
            return []

    async def is_record_descendant_of(
        self,
        record_id: str,
        ancestor_id: str,
        transaction: Optional[str] = None
    ) -> bool:
        """Check if record is descendant of ancestor."""
        try:
            query = """
            MATCH path = (r:Record {id: $record_id})-[:RECORD_RELATION*1..20 {relationshipType: "PARENT_CHILD"}]->(ancestor:Record {id: $ancestor_id})
            RETURN count(path) > 0 as is_descendant
            """
            results = await self.client.execute_query(
                query,
                parameters={"record_id": record_id, "ancestor_id": ancestor_id},
                txn_id=transaction
            )
            return results[0].get("is_descendant", False) if results else False
        except Exception as e:
            self.logger.error(f"❌ Is record descendant check failed: {str(e)}")
            return False

    async def is_record_folder(
        self,
        record_id: str,
        folder_mime_types: List[str],
        transaction: Optional[str] = None
    ) -> bool:
        """Check if record is a folder."""
        try:
            query = """
            MATCH (r:Record {id: $record_id})
            RETURN r.mimeType IN $folder_mime_types as is_folder
            """
            results = await self.client.execute_query(
                query,
                parameters={"record_id": record_id, "folder_mime_types": folder_mime_types},
                txn_id=transaction
            )
            return results[0].get("is_folder", False) if results else False
        except Exception as e:
            self.logger.error(f"❌ Is record folder check failed: {str(e)}")
            return False

    async def update_record(
        self,
        record_id: str,
        user_id: str,
        updates: Dict,
        file_metadata: Optional[Dict] = None,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """Update a record."""
        try:
            # Add timestamp
            updates["updatedAtTimestamp"] = get_epoch_timestamp_in_ms()

            query = """
            MATCH (r:Record {id: $record_id})
            SET r += $updates
            RETURN r
            """
            results = await self.client.execute_query(
                query,
                parameters={"record_id": record_id, "updates": updates},
                txn_id=transaction
            )
            if results:
                return {
                    "success": True,
                    "updatedRecord": results[0].get("r", {}),
                    "recordId": record_id
                }
            return {"success": False, "code": 404, "reason": "Record not found"}
        except Exception as e:
            self.logger.error(f"❌ Update record failed: {str(e)}")
            return {"success": False, "code": 500, "reason": str(e)}

    async def update_record_external_parent_id(
        self,
        record_id: str,
        external_parent_id: Optional[str],
        transaction: Optional[str] = None
    ) -> bool:
        """Update record's external parent ID."""
        try:
            query = """
            MATCH (r:Record {id: $record_id})
            SET r.externalParentId = $external_parent_id
            RETURN count(r) > 0 as updated
            """
            results = await self.client.execute_query(
                query,
                parameters={"record_id": record_id, "external_parent_id": external_parent_id},
                txn_id=transaction
            )
            return results[0].get("updated", False) if results else False
        except Exception as e:
            self.logger.error(f"❌ Update record external parent ID failed: {str(e)}")
            return False

    def _create_typed_record_from_neo4j(self, record_data: Dict) -> Optional[Record]:
        """Create typed Record instance from Neo4j data."""
        if not record_data:
            return None

        try:
            record_type = record_data.get("recordType", "FILE")

            # Map to appropriate Record subclass
            if record_type == "FILE":
                return FileRecord(**record_data)
            elif record_type == "MAIL":
                return MailRecord(**record_data)
            elif record_type == "COMMENT":
                return CommentRecord(**record_data)
            elif record_type == "WEBPAGE":
                return WebpageRecord(**record_data)
            elif record_type == "TICKET":
                return TicketRecord(**record_data)
            else:
                return Record(**record_data)
        except Exception as e:
            self.logger.warning(f"Failed to create typed record: {str(e)}")
            return Record(**record_data)

    # ==================== Knowledge Hub API Methods ====================

    async def get_user_app_ids(
        self,
        user_key: str,
        transaction: Optional[str] = None
    ) -> List[str]:
        """Get list of app IDs the user has access to."""
        try:
            query = """
            MATCH (u:User {id: $user_key})-[:USER_APP_RELATION]->(app:App)
            WHERE app IS NOT NULL
            RETURN app.id AS app_id
            """
            results = await self.client.execute_query(
                query,
                parameters={"user_key": user_key},
                txn_id=transaction
            )
            return [r["app_id"] for r in results if r.get("app_id")] if results else []
        except Exception as e:
            self.logger.error(f"❌ Get user app IDs failed: {str(e)}")
            return []

    async def is_knowledge_hub_folder(
        self,
        record_id: str,
        folder_mime_types: List[str],
        transaction: Optional[str] = None
    ) -> bool:
        """Check if a record is a folder."""
        try:
            query = """
            MATCH (r:Record {id: $record_id})
            OPTIONAL MATCH (r)-[:IS_OF_TYPE]->(f:File)
            WITH r, f,
                 r.mimeType IN $folder_mime_types AS is_folder_by_mimetype,
                 CASE WHEN f IS NOT NULL AND f.isFile = false THEN true ELSE false END AS is_folder_by_file
            RETURN is_folder_by_mimetype OR is_folder_by_file AS is_folder
            """
            results = await self.client.execute_query(
                query,
                parameters={"record_id": record_id, "folder_mime_types": folder_mime_types},
                txn_id=transaction
            )
            return results[0].get("is_folder", False) if results else False
        except Exception as e:
            self.logger.error(f"❌ Is knowledge hub folder check failed: {str(e)}")
            return False

    async def get_knowledge_hub_node_info(
        self,
        node_id: str,
        folder_mime_types: List[str],
        transaction: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get node information including type and subtype."""
        try:
            query = """
            // Try to find as Record first (with property validation)
            OPTIONAL MATCH (record:Record {id: $node_id})
            WHERE record.recordName IS NOT NULL

            // Try to find as RecordGroup (with property validation)
            OPTIONAL MATCH (rg:RecordGroup {id: $node_id})
            WHERE rg.groupName IS NOT NULL

            // Try to find as App (with property validation)
            OPTIONAL MATCH (app:App {id: $node_id})
            WHERE app.name IS NOT NULL

            WITH record, rg, app

            // Determine result based on which node was found
            RETURN CASE
                WHEN record IS NOT NULL THEN {
                    id: record.id,
                    name: record.recordName,
                    nodeType: CASE
                        WHEN record.mimeType IN $folder_mime_types THEN 'folder'
                        ELSE 'record'
                    END,
                    subType: record.recordType
                }
                WHEN rg IS NOT NULL THEN {
                    id: rg.id,
                    name: rg.groupName,
                    nodeType: CASE
                        WHEN rg.connectorName = 'KB' THEN 'kb'
                        ELSE 'recordGroup'
                    END,
                    subType: CASE
                        WHEN rg.connectorName = 'KB' THEN 'KB'
                        ELSE coalesce(rg.groupType, rg.connectorName)
                    END
                }
                WHEN app IS NOT NULL THEN {
                    id: app.id,
                    name: app.name,
                    nodeType: 'app',
                    subType: app.type
                }
                ELSE null
            END AS result
            """
            results = await self.client.execute_query(
                query,
                parameters={"node_id": node_id, "folder_mime_types": folder_mime_types},
                txn_id=transaction
            )
            if results and results[0].get("result"):
                return results[0]["result"]
            return None
        except Exception as e:
            self.logger.error(f"❌ Get knowledge hub node info failed: {str(e)}")
            return None

    async def get_knowledge_hub_parent_node(
        self,
        node_id: str,
        folder_mime_types: List[str],
        transaction: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get the parent node of a given node in a single query."""
        try:
            query = """
            // Try to find node in each type
            OPTIONAL MATCH (record:Record {id: $node_id})
            OPTIONAL MATCH (rg:RecordGroup {id: $node_id})
            OPTIONAL MATCH (app:App {id: $node_id})

            WITH record, rg, app

            // Determine if record is a KB record (check connectorName or connector document type)
            OPTIONAL MATCH (record_connector:RecordGroup {id: record.connectorId})
            WHERE record IS NOT NULL
            OPTIONAL MATCH (record_app:App {id: record.connectorId})
            WHERE record IS NOT NULL AND record_connector IS NULL

            WITH record, rg, app, record_connector, record_app,
                 record IS NOT NULL AND (
                     record.connectorName = 'KB' OR
                     (record_connector IS NOT NULL AND record_connector.type = 'KB') OR
                     (record_app IS NOT NULL AND record_app.type = 'KB')
                 ) AS is_kb_record

            // ==================== Record Parent Logic ====================
            // For KB records: check RECORD_RELATION, then BELONGS_TO to recordGroup
            // For connector records: check RECORD_RELATION, then BELONGS_TO (to recordGroup OR record), then INHERIT_PERMISSIONS

            // Step 1: Check RECORD_RELATION edge (parent folder/record)
            OPTIONAL MATCH (parent_from_rel:Record)-[rr:RECORD_RELATION]->(record)
            WHERE record IS NOT NULL AND rr.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT']

            // Step 2: Check BELONGS_TO edge (can point to RecordGroup or Record)
            OPTIONAL MATCH (record)-[:BELONGS_TO]->(belongs_parent)
            WHERE record IS NOT NULL AND parent_from_rel IS NULL
                  AND (belongs_parent:RecordGroup OR belongs_parent:Record)

            // Step 3: For connector records, fallback to INHERIT_PERMISSIONS edge
            OPTIONAL MATCH (record)-[:INHERIT_PERMISSIONS]->(inherit_parent:RecordGroup)
            WHERE record IS NOT NULL AND NOT is_kb_record
                  AND parent_from_rel IS NULL AND belongs_parent IS NULL

            // ==================== RecordGroup Parent Logic ====================
            // For KB record groups: traverse BELONGS_TO edge
            // For connector record groups: use parentId or connectorId property
            OPTIONAL MATCH (rg)-[:BELONGS_TO]->(rg_parent)
            WHERE rg IS NOT NULL AND rg.connectorName = 'KB'

            // For connector RGs, fetch parent by parentId property
            OPTIONAL MATCH (rg_parent_by_id:RecordGroup {id: rg.parentId})
            WHERE rg IS NOT NULL AND rg.connectorName <> 'KB' AND rg.parentId IS NOT NULL

            // For connector RGs, fetch app by connectorId property (if no parentId)
            OPTIONAL MATCH (rg_app_by_id:App {id: rg.connectorId})
            WHERE rg IS NOT NULL AND rg.connectorName <> 'KB' AND rg.parentId IS NULL AND rg.connectorId IS NOT NULL

            WITH record, rg, app, is_kb_record,
                 parent_from_rel, belongs_parent, inherit_parent,
                 rg_parent, rg_parent_by_id, rg_app_by_id

            // Determine final parent_id for records
            WITH record, rg, app, is_kb_record,
                 rg_parent, rg_parent_by_id, rg_app_by_id,
                 CASE
                     WHEN parent_from_rel IS NOT NULL THEN parent_from_rel.id
                     WHEN belongs_parent IS NOT NULL THEN belongs_parent.id
                     WHEN inherit_parent IS NOT NULL THEN inherit_parent.id
                     ELSE null
                 END AS record_parent_id,
                 parent_from_rel, belongs_parent, inherit_parent

            // Fetch the actual parent node for records (needed for complete info)
            OPTIONAL MATCH (final_parent_record:Record {id: record_parent_id})
            WHERE record IS NOT NULL AND record_parent_id IS NOT NULL

            OPTIONAL MATCH (final_parent_rg:RecordGroup {id: record_parent_id})
            WHERE record IS NOT NULL AND record_parent_id IS NOT NULL AND final_parent_record IS NULL

            WITH record, rg, app, is_kb_record,
                 rg_parent, rg_parent_by_id, rg_app_by_id,
                 final_parent_record, final_parent_rg,
                 parent_from_rel, belongs_parent, inherit_parent

            // Build final result with null-safety checks on required properties
            RETURN CASE
                // App has no parent
                WHEN app IS NOT NULL THEN null

                // RecordGroup parent
                WHEN rg IS NOT NULL THEN CASE
                    // KB record groups: use BELONGS_TO edge result
                    WHEN rg.connectorName = 'KB' THEN CASE
                        WHEN rg_parent IS NULL THEN null
                        WHEN rg_parent.type = 'KB' THEN null  // KB app shouldn't be shown
                        WHEN rg_parent:RecordGroup AND rg_parent.id IS NOT NULL AND rg_parent.groupName IS NOT NULL THEN {
                            id: rg_parent.id,
                            name: rg_parent.groupName,
                            nodeType: CASE WHEN rg_parent.connectorName = 'KB' THEN 'kb' ELSE 'recordGroup' END,
                            subType: CASE WHEN rg_parent.connectorName = 'KB' THEN 'KB' ELSE coalesce(rg_parent.groupType, rg_parent.connectorName) END
                        }
                        WHEN rg_parent:App AND rg_parent.id IS NOT NULL AND rg_parent.name IS NOT NULL THEN {
                            id: rg_parent.id,
                            name: rg_parent.name,
                            nodeType: 'app',
                            subType: rg_parent.type
                        }
                        ELSE null
                    END
                    // Connector record groups: use property-based lookup
                    WHEN rg_parent_by_id IS NOT NULL AND rg_parent_by_id.id IS NOT NULL AND rg_parent_by_id.groupName IS NOT NULL THEN {
                        id: rg_parent_by_id.id,
                        name: rg_parent_by_id.groupName,
                        nodeType: 'recordGroup',
                        subType: coalesce(rg_parent_by_id.groupType, rg_parent_by_id.connectorName)
                    }
                    WHEN rg_app_by_id IS NOT NULL AND rg_app_by_id.id IS NOT NULL AND rg_app_by_id.name IS NOT NULL THEN {
                        id: rg_app_by_id.id,
                        name: rg_app_by_id.name,
                        nodeType: 'app',
                        subType: rg_app_by_id.type
                    }
                    ELSE null
                END

                // Record parent (with null-safety checks)
                WHEN record IS NOT NULL THEN CASE
                    WHEN final_parent_record IS NOT NULL AND final_parent_record.id IS NOT NULL AND final_parent_record.recordName IS NOT NULL THEN {
                        id: final_parent_record.id,
                        name: final_parent_record.recordName,
                        nodeType: CASE
                            WHEN final_parent_record.mimeType IN $folder_mime_types THEN 'folder'
                            ELSE 'record'
                        END,
                        subType: final_parent_record.recordType
                    }
                    WHEN final_parent_rg IS NOT NULL AND final_parent_rg.id IS NOT NULL AND final_parent_rg.groupName IS NOT NULL THEN {
                        id: final_parent_rg.id,
                        name: final_parent_rg.groupName,
                        nodeType: CASE
                            WHEN final_parent_rg.connectorName = 'KB' THEN 'kb'
                            ELSE 'recordGroup'
                        END,
                        subType: CASE
                            WHEN final_parent_rg.connectorName = 'KB' THEN 'KB'
                            ELSE coalesce(final_parent_rg.groupType, final_parent_rg.connectorName)
                        END
                    }
                    ELSE null
                END

                ELSE null
            END AS result
            """
            results = await self.client.execute_query(
                query,
                parameters={"node_id": node_id, "folder_mime_types": folder_mime_types},
                txn_id=transaction
            )
            if results and results[0].get("result"):
                return results[0]["result"]
            return None
        except Exception as e:
            self.logger.error(f"❌ Get knowledge hub parent node failed: {str(e)}")
            return None

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
        try:
            query = """
            // Get KBs the user has access to via direct, team, or group permissions
            MATCH (u:User {id: $user_key})

            // Direct KB permissions
            OPTIONAL MATCH (u)-[perm:PERMISSION {type: 'USER'}]->(kb:RecordGroup)
            WHERE kb.groupType = 'KB' AND kb.connectorName = 'KB'
                  AND kb.orgId = $org_id AND NOT coalesce(kb.isDeleted, false)

            // Team-based KB permissions
            OPTIONAL MATCH (u)-[:PERMISSION {type: 'USER'}]->(team:Team)-[:PERMISSION {type: 'TEAM'}]->(kb_team:RecordGroup)
            WHERE kb_team.groupType = 'KB' AND kb_team.connectorName = 'KB'
                  AND kb_team.orgId = $org_id AND NOT coalesce(kb_team.isDeleted, false)

            // Group-based KB permissions
            OPTIONAL MATCH (u)-[:PERMISSION {type: 'USER'}]->(grp:Group)-[:PERMISSION {type: 'GROUP'}]->(kb_group:RecordGroup)
            WHERE kb_group.groupType = 'KB' AND kb_group.connectorName = 'KB'
                  AND kb_group.orgId = $org_id AND NOT coalesce(kb_group.isDeleted, false)

            // Collect and combine all KBs (nulls automatically excluded by DISTINCT on node)
            WITH u, collect(DISTINCT kb) + collect(DISTINCT kb_team) + collect(DISTINCT kb_group) AS all_kbs_raw

            // Get apps via USER_APP_RELATION
            OPTIONAL MATCH (u)-[:USER_APP_RELATION]->(app:App)

            WITH all_kbs_raw, collect(DISTINCT app) AS all_apps_raw

            // Filter nulls and transform using list comprehensions (avoids UNWIND empty list issue)
            WITH
                [kb IN all_kbs_raw WHERE kb IS NOT NULL AND kb.id IS NOT NULL AND kb.groupName IS NOT NULL |
                    {id: kb.id, name: kb.groupName}
                ] AS kbs,
                [app IN all_apps_raw WHERE app IS NOT NULL AND app.id IS NOT NULL |
                    {id: app.id, name: app.name, type: app.type}
                ] AS apps

            RETURN {kbs: kbs, apps: apps} AS result
            """
            results = await self.client.execute_query(
                query,
                parameters={"user_key": user_key, "org_id": org_id},
                txn_id=transaction
            )
            if results and results[0].get("result"):
                return results[0]["result"]
            return {"kbs": [], "apps": []}
        except Exception as e:
            self.logger.error(f"❌ Get knowledge hub filter options failed: {str(e)}")
            return {"kbs": [], "apps": []}

    async def get_knowledge_hub_context_permissions(
        self,
        user_key: str,
        org_id: str,
        parent_id: Optional[str],
        transaction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get user's context-level permissions.
        Supports direct user, team, group, org, and ANYONE permissions.
        For KB-related nodes, falls back to root KB permissions.
        If multiple permissions exist, returns the highest role.
        """
        try:
            if not parent_id:
                # Root level - check if user is admin
                query = """
                MATCH (u:User {id: $user_key})
                WITH u, (u.role = 'ADMIN' OR u.orgRole = 'ADMIN') AS is_admin
                RETURN {
                    role: CASE WHEN is_admin THEN 'ADMIN' ELSE 'MEMBER' END,
                    canUpload: is_admin,
                    canCreateFolders: is_admin,
                    canEdit: is_admin,
                    canDelete: is_admin,
                    canManagePermissions: is_admin
                } AS result
                """
                results = await self.client.execute_query(
                    query,
                    parameters={"user_key": user_key},
                    txn_id=transaction
                )
            else:
                # Node level - comprehensive permission check
                query = """
                // Role priority map
                WITH {OWNER: 6, ADMIN: 5, EDITOR: 4, WRITER: 3, COMMENTER: 2, READER: 1} AS role_priority

                // Find the node (could be Record, App, or RecordGroup)
                OPTIONAL MATCH (record:Record {id: $parent_id})
                OPTIONAL MATCH (app:App {id: $parent_id})
                OPTIONAL MATCH (rg:RecordGroup {id: $parent_id})

                WITH role_priority, record, app, rg,
                     coalesce(record, app, rg) AS node,
                     record IS NOT NULL AS is_record,
                     rg IS NOT NULL AS is_rg

                // Step 1: For records, check inheritPermissions to find permission target
                OPTIONAL MATCH (record)-[:INHERIT_PERMISSIONS]->(inherited_target:RecordGroup)
                WHERE record IS NOT NULL

                WITH role_priority, node, record, rg, is_record, is_rg,
                     CASE WHEN inherited_target IS NOT NULL THEN inherited_target ELSE node END AS permission_target

                // Step 2: Determine if this is KB-related (for root KB fallback)
                // Check record's connector
                OPTIONAL MATCH (record_connector:RecordGroup {id: record.connectorId})
                WHERE record IS NOT NULL

                WITH role_priority, node, record, rg, is_record, is_rg, permission_target, record_connector,
                     record IS NOT NULL AND (
                         record.connectorName = 'KB' OR
                         (record_connector IS NOT NULL AND record_connector.connectorName = 'KB')
                     ) AS is_kb_record,
                     rg IS NOT NULL AND rg.connectorName = 'KB' AS is_kb_rg

                // For nested RGs under KB, traverse up to check
                OPTIONAL MATCH (rg)-[:BELONGS_TO*1..10]->(ancestor_kb:RecordGroup)
                WHERE rg IS NOT NULL AND NOT is_kb_rg AND ancestor_kb.connectorName = 'KB'

                WITH role_priority, node, record, rg, is_record, is_rg, permission_target,
                     record_connector, is_kb_record, is_kb_rg,
                     ancestor_kb IS NOT NULL AS is_nested_rg_under_kb,
                     is_kb_record OR (ancestor_kb IS NOT NULL) AS needs_kb_fallback

                // Find root KB for fallback
                OPTIONAL MATCH (start_connector:RecordGroup {id: record.connectorId})
                WHERE is_kb_record AND record IS NOT NULL

                // For records under KB connector
                OPTIONAL MATCH (start_connector)-[:BELONGS_TO*0..10]->(root_kb:RecordGroup)
                WHERE is_kb_record AND root_kb.connectorName = 'KB'

                // For nested RGs
                OPTIONAL MATCH (rg)-[:BELONGS_TO*0..10]->(root_kb_from_rg:RecordGroup)
                WHERE is_nested_rg_under_kb AND root_kb_from_rg.connectorName = 'KB'

                WITH role_priority, node, permission_target, needs_kb_fallback,
                     CASE
                         WHEN is_kb_record AND start_connector IS NOT NULL AND start_connector.connectorName = 'KB'
                              THEN start_connector
                         WHEN is_kb_record AND root_kb IS NOT NULL THEN root_kb
                         WHEN is_nested_rg_under_kb AND root_kb_from_rg IS NOT NULL THEN root_kb_from_rg
                         WHEN is_kb_rg THEN rg
                         ELSE null
                     END AS root_kb

                // Now get user and collect all permissions
                MATCH (u:User {id: $user_key})

                // Step 3: Direct user permission on permission_target
                OPTIONAL MATCH (u)-[direct_perm:PERMISSION {type: 'USER'}]->(permission_target)
                WHERE direct_perm.role IS NOT NULL AND direct_perm.role <> ''

                // Step 4: Team permissions (user's role IN the team, not team's role on node)
                OPTIONAL MATCH (u)-[user_team_perm:PERMISSION {type: 'USER'}]->(team:Team)-[team_node_perm:PERMISSION {type: 'TEAM'}]->(permission_target)
                WHERE user_team_perm.role IS NOT NULL AND user_team_perm.role <> ''

                // Step 5: Group permissions (user's role in group)
                OPTIONAL MATCH (u)-[user_group_perm:PERMISSION {type: 'USER'}]->(grp:Group)-[group_node_perm:PERMISSION {type: 'GROUP'}]->(permission_target)
                WHERE user_group_perm.role IS NOT NULL AND user_group_perm.role <> ''

                // Step 6: Org permissions
                OPTIONAL MATCH (org:Organization {id: $org_id})-[org_perm:PERMISSION {type: 'ORG'}]->(permission_target)
                WHERE org_perm.role IS NOT NULL AND org_perm.role <> ''

                // Step 7: ANYONE permissions
                OPTIONAL MATCH ()-[anyone_perm:PERMISSION {type: 'ANYONE'}]->(permission_target)
                WHERE anyone_perm.role IS NOT NULL AND anyone_perm.role <> ''

                // Step 8: Root KB fallback permissions (if needed)
                OPTIONAL MATCH (u)-[root_kb_direct:PERMISSION {type: 'USER'}]->(root_kb)
                WHERE needs_kb_fallback AND root_kb IS NOT NULL
                      AND root_kb_direct.role IS NOT NULL AND root_kb_direct.role <> ''

                OPTIONAL MATCH (u)-[user_team_kb:PERMISSION {type: 'USER'}]->(team_kb:Team)-[:PERMISSION {type: 'TEAM'}]->(root_kb)
                WHERE needs_kb_fallback AND root_kb IS NOT NULL
                      AND user_team_kb.role IS NOT NULL AND user_team_kb.role <> ''

                OPTIONAL MATCH (u)-[user_group_kb:PERMISSION {type: 'USER'}]->(grp_kb:Group)-[:PERMISSION {type: 'GROUP'}]->(root_kb)
                WHERE needs_kb_fallback AND root_kb IS NOT NULL
                      AND user_group_kb.role IS NOT NULL AND user_group_kb.role <> ''

                // Collect all permission objects with priorities
                WITH role_priority,
                     [
                         CASE WHEN direct_perm IS NOT NULL THEN {role: direct_perm.role, priority: role_priority[direct_perm.role]} ELSE null END,
                         CASE WHEN user_team_perm IS NOT NULL THEN {role: user_team_perm.role, priority: role_priority[user_team_perm.role]} ELSE null END,
                         CASE WHEN user_group_perm IS NOT NULL THEN {role: user_group_perm.role, priority: role_priority[user_group_perm.role]} ELSE null END,
                         CASE WHEN org_perm IS NOT NULL THEN {role: org_perm.role, priority: role_priority[org_perm.role]} ELSE null END,
                         CASE WHEN anyone_perm IS NOT NULL THEN {role: anyone_perm.role, priority: role_priority[anyone_perm.role]} ELSE null END,
                         CASE WHEN root_kb_direct IS NOT NULL THEN {role: root_kb_direct.role, priority: role_priority[root_kb_direct.role]} ELSE null END,
                         CASE WHEN user_team_kb IS NOT NULL THEN {role: user_team_kb.role, priority: role_priority[user_team_kb.role]} ELSE null END,
                         CASE WHEN user_group_kb IS NOT NULL THEN {role: user_group_kb.role, priority: role_priority[user_group_kb.role]} ELSE null END
                     ] AS all_perms_raw

                // Filter nulls and find highest priority
                WITH [p IN all_perms_raw WHERE p IS NOT NULL AND p.priority IS NOT NULL] AS all_perms

                // Get highest priority role - unwind, sort, and pick first
                // (Cannot use ORDER BY inside list comprehensions in Cypher)
                UNWIND CASE WHEN size(all_perms) = 0 THEN [{role: 'READER', priority: 0}] ELSE all_perms END AS perm
                WITH perm ORDER BY perm.priority DESC LIMIT 1
                WITH perm.role AS final_role

                RETURN {
                    role: final_role,
                    canUpload: final_role IN ['OWNER', 'ADMIN', 'EDITOR', 'WRITER'],
                    canCreateFolders: final_role IN ['OWNER', 'ADMIN', 'EDITOR', 'WRITER'],
                    canEdit: final_role IN ['OWNER', 'ADMIN', 'EDITOR', 'WRITER'],
                    canDelete: final_role IN ['OWNER', 'ADMIN'],
                    canManagePermissions: final_role IN ['OWNER', 'ADMIN']
                } AS result
                """
                results = await self.client.execute_query(
                    query,
                    parameters={"user_key": user_key, "org_id": org_id, "parent_id": parent_id},
                    txn_id=transaction
                )

            if results and results[0].get("result"):
                return results[0]["result"]
            return {
                "role": "READER",
                "canUpload": False,
                "canCreateFolders": False,
                "canEdit": False,
                "canDelete": False,
                "canManagePermissions": False
            }
        except Exception as e:
            self.logger.error(f"❌ Get knowledge hub context permissions failed: {str(e)}")
            return {
                "role": "READER",
                "canUpload": False,
                "canCreateFolders": False,
                "canEdit": False,
                "canDelete": False,
                "canManagePermissions": False
            }

    async def get_knowledge_hub_breadcrumbs(
        self,
        node_id: str,
        transaction: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get breadcrumb trail for a node using iterative parent lookup.

        NOTE: Uses iterative parent lookup (one query per level) because parent relationships
        are stored via multiple mechanisms: parentId field (recordGroups), RECORD_RELATION edges
        (records), and connectorId field (linking to apps).
        """
        breadcrumbs = []
        current_id = node_id
        visited = set()
        max_depth = 20

        try:
            while current_id and len(visited) < max_depth:
                if current_id in visited:
                    break
                visited.add(current_id)

                # Get node info and parent
                query = """
                // Try to find in each collection
                OPTIONAL MATCH (record:Record {id: $id})
                OPTIONAL MATCH (rg:RecordGroup {id: $id})
                OPTIONAL MATCH (app:App {id: $id})

                WITH record, rg, app

                // For records, check isOfType to determine folder
                OPTIONAL MATCH (record)-[:IS_OF_TYPE]->(f:File)
                WHERE record IS NOT NULL

                WITH record, rg, app, f,
                     CASE WHEN f IS NOT NULL AND f.isFile = false THEN true ELSE false END AS is_folder

                // Determine node type
                WITH record, rg, app, f, is_folder,
                     CASE
                         WHEN record IS NOT NULL THEN CASE WHEN is_folder THEN 'folder' ELSE 'record' END
                         WHEN rg IS NOT NULL THEN CASE WHEN rg.connectorName = 'KB' THEN 'kb' ELSE 'recordGroup' END
                         WHEN app IS NOT NULL THEN 'app'
                         ELSE null
                     END AS node_type,
                     coalesce(record, rg, app) AS node

                // Get parent for records - ONLY via RECORD_RELATION edges
                // Edge direction: parent -> child (edge from parent, to current record)
                OPTIONAL MATCH (parent_rec)-[rr:RECORD_RELATION]->(record)
                WHERE record IS NOT NULL
                      AND rr IS NOT NULL
                      AND rr.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT']

                // Get parent for KB record groups via BELONGS_TO
                OPTIONAL MATCH (rg)-[:BELONGS_TO]->(rg_parent)
                WHERE rg IS NOT NULL AND rg.connectorName = 'KB'

                WITH node, node_type, record, rg, app, f, parent_rec, rg_parent

                // Determine parent ID (matching ArangoDB logic exactly)
                WITH node, node_type, record, rg, app,
                     CASE
                         // Apps have no parent
                         WHEN app IS NOT NULL THEN null

                         // Records: ONLY use RECORD_RELATION parent (no BELONGS_TO or externalParentId)
                         WHEN record IS NOT NULL THEN CASE
                             WHEN parent_rec IS NOT NULL THEN parent_rec.id
                             ELSE null
                         END

                         // RecordGroups
                         WHEN rg IS NOT NULL THEN CASE
                             // For KB record groups: check belongsTo edge
                             WHEN rg.connectorName = 'KB' THEN CASE
                                 WHEN rg_parent IS NULL THEN null
                                 // If parent is App with type='KB', return null (KB apps shouldn't be shown)
                                 WHEN rg_parent:App AND rg_parent.type = 'KB' THEN null
                                 // Otherwise return parent's id
                                 ELSE rg_parent.id
                             END
                             // For connector record groups: use parentId or connectorId
                             WHEN rg.parentId IS NOT NULL THEN rg.parentId
                             WHEN rg.connectorId IS NOT NULL THEN rg.connectorId
                             ELSE null
                         END

                         ELSE null
                     END AS parent_id,
                     // Extract subType based on node type
                     CASE
                         WHEN record IS NOT NULL THEN record.recordType
                         WHEN rg IS NOT NULL THEN CASE
                             WHEN rg.connectorName = 'KB' THEN 'KB'
                             ELSE coalesce(rg.groupType, rg.connectorName)
                         END
                         WHEN app IS NOT NULL THEN app.type
                         ELSE null
                     END AS sub_type

                RETURN {
                    id: node.id,
                    name: coalesce(node.recordName, node.groupName, node.name),
                    nodeType: node_type,
                    subType: sub_type,
                    parentId: parent_id
                } AS result
                """
                results = await self.client.execute_query(
                    query,
                    parameters={"id": current_id},
                    txn_id=transaction
                )

                if not results or not results[0].get("result"):
                    break

                node_info = results[0]["result"]
                if not node_info.get("id") or not node_info.get("name"):
                    break

                # Append to breadcrumbs (will reverse at end)
                breadcrumbs.append({
                    "id": node_info["id"],
                    "name": node_info["name"],
                    "nodeType": node_info["nodeType"],
                    "subType": node_info.get("subType")
                })

                # Move to parent
                current_id = node_info.get("parentId")

            # Reverse to get root -> leaf order (matching ArangoDB behavior)
            breadcrumbs.reverse()
            return breadcrumbs

        except Exception as e:
            self.logger.error(f"❌ Get knowledge hub breadcrumbs failed: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            return []

    async def get_knowledge_hub_node_permissions(
        self,
        user_key: str,
        node_ids: List[str],
        node_types: List[str],
        transaction: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Get user permissions for multiple nodes in batch."""
        if not node_ids:
            return {}

        try:
            # Separate by type
            record_group_ids = [nid for nid, ntype in zip(node_ids, node_types) if ntype in ['kb', 'recordGroup']]
            app_ids = [nid for nid, ntype in zip(node_ids, node_types) if ntype == 'app']
            record_ids = [nid for nid, ntype in zip(node_ids, node_types) if ntype not in ['kb', 'recordGroup', 'app']]

            permissions = {}

            # Query record group permissions
            if record_group_ids:
                query = """
                MATCH (u:User {id: $user_key})
                WITH u, {OWNER: 6, ADMIN: 5, EDITOR: 4, WRITER: 3, COMMENTER: 2, READER: 1} AS role_priority

                UNWIND $record_group_ids AS rg_id
                MATCH (rg:RecordGroup {id: rg_id})

                // Check if this is a KB or nested under KB
                WITH u, rg, role_priority,
                     (rg.connectorName = 'KB') AS is_kb

                // Check if nested under KB by traversing up BELONGS_TO
                OPTIONAL MATCH (rg)-[:BELONGS_TO*1..10]->(ancestor_kb:RecordGroup)
                WHERE NOT is_kb AND ancestor_kb.connectorName = 'KB'

                WITH u, rg, role_priority, is_kb,
                     ancestor_kb IS NOT NULL AS is_nested_under_kb

                // Priority 1: Direct user permission on record group
                OPTIONAL MATCH (u)-[direct_perm:PERMISSION {type: 'USER'}]->(rg)
                WHERE NOT coalesce(rg.isDeleted, false)
                      AND direct_perm.role IS NOT NULL AND direct_perm.role <> ''

                // Priority 2: Team permissions (return user's role IN the team, not team's role on node)
                OPTIONAL MATCH (u)-[user_team_perm:PERMISSION {type: 'USER'}]->(team:Team)-[:PERMISSION {type: 'TEAM'}]->(rg)
                WHERE user_team_perm.role IS NOT NULL AND user_team_perm.role <> ''

                // Priority 3: Group/Role permissions (return group's role on target)
                OPTIONAL MATCH (u)-[:PERMISSION {type: 'USER'}]->(grp)-[grp_perm:PERMISSION]->(rg)
                WHERE (grp:Group OR grp:Role) AND grp_perm.type IN ['GROUP', 'ROLE']
                      AND grp_perm.role IS NOT NULL AND grp_perm.role <> ''

                // Collect Priority 1-3 permissions
                WITH u, rg, role_priority, is_kb, is_nested_under_kb, direct_perm, user_team_perm, grp_perm,
                     [
                         CASE WHEN direct_perm IS NOT NULL THEN {role: direct_perm.role, priority: role_priority[direct_perm.role]} ELSE null END,
                         CASE WHEN user_team_perm IS NOT NULL THEN {role: user_team_perm.role, priority: role_priority[user_team_perm.role]} ELSE null END,
                         CASE WHEN grp_perm IS NOT NULL THEN {role: grp_perm.role, priority: role_priority[grp_perm.role]} ELSE null END
                     ] AS direct_perms

                // Priority 4: For nested KB record groups, find root KB and check permissions
                // Find root KB by traversing up
                OPTIONAL MATCH (rg)-[:BELONGS_TO*0..10]->(root_kb:RecordGroup)
                WHERE is_nested_under_kb AND root_kb.connectorName = 'KB'

                // Check direct user permission on root KB
                OPTIONAL MATCH (u)-[root_kb_direct:PERMISSION {type: 'USER'}]->(root_kb)
                WHERE is_nested_under_kb AND root_kb IS NOT NULL
                      AND root_kb_direct.role IS NOT NULL AND root_kb_direct.role <> ''

                // Check team permission on root KB (user's role in team)
                OPTIONAL MATCH (u)-[user_team_kb:PERMISSION {type: 'USER'}]->(team_kb:Team)-[:PERMISSION {type: 'TEAM'}]->(root_kb)
                WHERE is_nested_under_kb AND root_kb IS NOT NULL
                      AND user_team_kb.role IS NOT NULL AND user_team_kb.role <> ''

                // Check group permission on root KB (group's role on KB)
                OPTIONAL MATCH (u)-[:PERMISSION {type: 'USER'}]->(grp_kb)-[kb_grp_perm:PERMISSION]->(root_kb)
                WHERE is_nested_under_kb AND root_kb IS NOT NULL
                      AND (grp_kb:Group OR grp_kb:Role) AND kb_grp_perm.type IN ['GROUP', 'ROLE']
                      AND kb_grp_perm.role IS NOT NULL AND kb_grp_perm.role <> ''

                // Collect root KB permissions
                WITH rg, role_priority, direct_perms, is_nested_under_kb,
                     [
                         CASE WHEN root_kb_direct IS NOT NULL THEN {role: root_kb_direct.role, priority: role_priority[root_kb_direct.role]} ELSE null END,
                         CASE WHEN user_team_kb IS NOT NULL THEN {role: user_team_kb.role, priority: role_priority[user_team_kb.role]} ELSE null END,
                         CASE WHEN kb_grp_perm IS NOT NULL THEN {role: kb_grp_perm.role, priority: role_priority[kb_grp_perm.role]} ELSE null END
                     ] AS root_kb_perms

                // Combine all permissions
                WITH rg,
                     [p IN direct_perms + CASE WHEN is_nested_under_kb THEN root_kb_perms ELSE [] END
                      WHERE p IS NOT NULL AND p.priority IS NOT NULL] AS all_perms

                // Get highest priority role using subquery (ORDER BY not supported in list comprehension)
                CALL {
                    WITH all_perms
                    UNWIND CASE WHEN size(all_perms) = 0 THEN [{role: 'READER', priority: 0}] ELSE all_perms END AS perm
                    WITH perm
                    ORDER BY perm.priority DESC
                    LIMIT 1
                    RETURN perm.role AS final_role
                }
                WITH rg, final_role

                RETURN rg.id AS node_id, {
                    role: final_role,
                    canEdit: final_role IN ['OWNER', 'ADMIN', 'EDITOR', 'WRITER'],
                    canDelete: final_role IN ['OWNER', 'ADMIN']
                } AS perm
                """
                results = await self.client.execute_query(
                    query,
                    parameters={"user_key": user_key, "record_group_ids": record_group_ids},
                    txn_id=transaction
                )
                for r in results or []:
                    if r.get("node_id") and r.get("perm"):
                        permissions[r["node_id"]] = r["perm"]

            # Apps - generally read-only (matching ArangoDB behavior)
            for app_id in app_ids:
                permissions[app_id] = {
                    "role": "READER",
                    "canEdit": False,
                    "canDelete": False
                }

            # Query record permissions
            if record_ids:
                query = """
                MATCH (u:User {id: $user_key})
                WITH u, {OWNER: 6, ADMIN: 5, EDITOR: 4, WRITER: 3, COMMENTER: 2, READER: 1} AS role_priority

                UNWIND $record_ids AS rec_id
                MATCH (record:Record {id: rec_id})

                // Determine if record belongs to KB
                OPTIONAL MATCH (record_connector:RecordGroup {id: record.connectorId})
                WITH u, record, role_priority, record_connector,
                     record_connector IS NOT NULL AND record_connector.connectorName = 'KB' AS is_direct_kb

                // Check if nested under KB by traversing connector's hierarchy
                OPTIONAL MATCH (record_connector)-[:BELONGS_TO*1..10]->(ancestor_kb:RecordGroup)
                WHERE NOT is_direct_kb AND record_connector IS NOT NULL AND ancestor_kb.connectorName = 'KB'

                WITH u, record, role_priority, record_connector, is_direct_kb,
                     is_direct_kb OR (ancestor_kb IS NOT NULL) AS is_kb_record

                // Priority 1: Direct user permission on record
                OPTIONAL MATCH (u)-[direct_perm:PERMISSION {type: 'USER'}]->(record)
                WHERE direct_perm.role IS NOT NULL AND direct_perm.role <> ''

                // Priority 2: Team permissions (return user's role IN the team)
                OPTIONAL MATCH (u)-[user_team_perm:PERMISSION {type: 'USER'}]->(team:Team)-[:PERMISSION {type: 'TEAM'}]->(record)
                WHERE user_team_perm.role IS NOT NULL AND user_team_perm.role <> ''

                // Priority 3: Group permissions (return group's role on record)
                OPTIONAL MATCH (u)-[:PERMISSION {type: 'USER'}]->(grp)-[grp_perm:PERMISSION {type: 'GROUP'}]->(record)
                WHERE grp:Group AND grp_perm.role IS NOT NULL AND grp_perm.role <> ''

                // Collect Priority 1-3 permissions
                WITH u, record, role_priority, record_connector, is_direct_kb, is_kb_record,
                     [
                         CASE WHEN direct_perm IS NOT NULL THEN {role: direct_perm.role, priority: role_priority[direct_perm.role]} ELSE null END,
                         CASE WHEN user_team_perm IS NOT NULL THEN {role: user_team_perm.role, priority: role_priority[user_team_perm.role]} ELSE null END,
                         CASE WHEN grp_perm IS NOT NULL THEN {role: grp_perm.role, priority: role_priority[grp_perm.role]} ELSE null END
                     ] AS direct_perms

                // Priority 4: For KB records, find root KB and check permissions
                // If connector is root KB, use it; otherwise traverse up
                OPTIONAL MATCH (record_connector)-[:BELONGS_TO*0..10]->(root_kb:RecordGroup)
                WHERE is_kb_record AND root_kb.connectorName = 'KB'

                WITH u, record, role_priority, is_kb_record, direct_perms,
                     CASE WHEN is_direct_kb THEN record_connector ELSE root_kb END AS final_root_kb

                // Check direct user permission on root KB
                OPTIONAL MATCH (u)-[root_kb_direct:PERMISSION {type: 'USER'}]->(final_root_kb)
                WHERE is_kb_record AND final_root_kb IS NOT NULL
                      AND root_kb_direct.role IS NOT NULL AND root_kb_direct.role <> ''

                // Check team permission on root KB (user's role in team)
                OPTIONAL MATCH (u)-[user_team_kb:PERMISSION {type: 'USER'}]->(team_kb:Team)-[:PERMISSION {type: 'TEAM'}]->(final_root_kb)
                WHERE is_kb_record AND final_root_kb IS NOT NULL
                      AND user_team_kb.role IS NOT NULL AND user_team_kb.role <> ''

                // Check group permission on root KB (group's role on KB)
                OPTIONAL MATCH (u)-[:PERMISSION {type: 'USER'}]->(grp_kb:Group)-[kb_grp_perm:PERMISSION {type: 'GROUP'}]->(final_root_kb)
                WHERE is_kb_record AND final_root_kb IS NOT NULL
                      AND kb_grp_perm.role IS NOT NULL AND kb_grp_perm.role <> ''

                // Collect root KB permissions
                WITH record, role_priority, direct_perms, is_kb_record,
                     [
                         CASE WHEN root_kb_direct IS NOT NULL THEN {role: root_kb_direct.role, priority: role_priority[root_kb_direct.role]} ELSE null END,
                         CASE WHEN user_team_kb IS NOT NULL THEN {role: user_team_kb.role, priority: role_priority[user_team_kb.role]} ELSE null END,
                         CASE WHEN kb_grp_perm IS NOT NULL THEN {role: kb_grp_perm.role, priority: role_priority[kb_grp_perm.role]} ELSE null END
                     ] AS root_kb_perms

                // Combine all permissions
                WITH record,
                     [p IN direct_perms + CASE WHEN is_kb_record THEN root_kb_perms ELSE [] END
                      WHERE p IS NOT NULL AND p.priority IS NOT NULL] AS all_perms

                // Get highest priority role using subquery (ORDER BY not supported in list comprehension)
                CALL {
                    WITH all_perms
                    UNWIND CASE WHEN size(all_perms) = 0 THEN [{role: 'READER', priority: 0}] ELSE all_perms END AS perm
                    WITH perm
                    ORDER BY perm.priority DESC
                    LIMIT 1
                    RETURN perm.role AS final_role
                }
                WITH record, final_role

                RETURN record.id AS node_id, {
                    role: final_role,
                    canEdit: final_role IN ['OWNER', 'ADMIN', 'EDITOR', 'WRITER'],
                    canDelete: final_role IN ['OWNER', 'ADMIN']
                } AS perm
                """
                results = await self.client.execute_query(
                    query,
                    parameters={"user_key": user_key, "record_ids": record_ids},
                    txn_id=transaction
                )
                for r in results or []:
                    if r.get("node_id") and r.get("perm"):
                        permissions[r["node_id"]] = r["perm"]

            return permissions

        except Exception as e:
            self.logger.error(f"❌ Get knowledge hub node permissions failed: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {}

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
        try:
            query = """
            MATCH (user_doc:User {id: $user_key})
            WITH user_doc, user_doc.userId AS user_id

            // ==================== Get Knowledge Bases ====================
            CALL {
                WITH user_doc, user_id
                // Only process if include_kbs is true
                WITH user_doc, user_id WHERE $include_kbs = true

                MATCH (kb:RecordGroup)
                WHERE kb.orgId = $org_id AND kb.connectorName = 'KB'
                      AND NOT coalesce(kb.isDeleted, false)

                // Check direct user permission
                OPTIONAL MATCH (user_doc)-[direct_perm:PERMISSION {type: 'USER'}]->(kb)

                // Check team permission
                OPTIONAL MATCH (user_doc)-[:PERMISSION {type: 'USER'}]->(team:Team)-[:PERMISSION {type: 'TEAM'}]->(kb)

                WITH kb, user_doc, user_id,
                     (direct_perm IS NOT NULL OR team IS NOT NULL) AS has_permission
                WHERE has_permission

                // Check for children via BELONGS_TO edges
                OPTIONAL MATCH (record:Record)-[:BELONGS_TO]->(kb)
                WHERE NOT coalesce(record.isDeleted, false) AND record.externalParentId IS NULL

                OPTIONAL MATCH (child_rg:RecordGroup)-[:BELONGS_TO]->(kb)
                WHERE child_rg.connectorName = 'KB' AND NOT coalesce(child_rg.isDeleted, false)

                WITH kb, user_doc, user_id,
                     count(DISTINCT record) > 0 OR count(DISTINCT child_rg) > 0 AS has_children

                // Determine sharing status - count other users with permissions (not the current user)
                OPTIONAL MATCH (other_user:User)-[other_user_perm:PERMISSION {type: 'USER'}]->(kb)
                WHERE other_user <> user_doc

                OPTIONAL MATCH ()-[team_perm:PERMISSION {type: 'TEAM'}]->(kb)

                WITH kb, has_children, user_doc, user_id,
                     (kb.createdBy = $user_key OR kb.createdBy = user_id) AS is_creator,
                     count(DISTINCT other_user_perm) AS other_user_count,
                     count(DISTINCT team_perm) AS team_perm_count

                WITH kb, has_children,
                     CASE WHEN is_creator AND other_user_count = 0 AND team_perm_count = 0
                          THEN 'private' ELSE 'shared' END AS sharingStatus

                RETURN {
                    id: kb.id,
                    name: kb.groupName,
                    nodeType: 'kb',
                    parentId: null,
                    source: 'KB',
                    connector: 'KB',
                    createdAt: kb.createdAtTimestamp,
                    updatedAt: kb.updatedAtTimestamp,
                    webUrl: '/kb/' + kb.id,
                    hasChildren: has_children,
                    sharingStatus: sharingStatus
                } AS node
            }

            WITH collect(node) AS kb_nodes

            // ==================== Get Apps ====================
            CALL {
                WITH kb_nodes
                // Only process if include_apps is true
                WITH kb_nodes WHERE $include_apps = true

                MATCH (app:App)
                WHERE app.id IN $user_app_ids AND app.type <> 'KB'

                // Check for children (record groups)
                OPTIONAL MATCH (rg:RecordGroup)
                WHERE rg.connectorId = app.id

                WITH app, count(rg) > 0 AS has_children

                RETURN {
                    id: app.id,
                    name: app.name,
                    nodeType: 'app',
                    parentId: null,
                    source: 'CONNECTOR',
                    connector: app.type,
                    createdAt: coalesce(app.createdAtTimestamp, 0),
                    updatedAt: coalesce(app.updatedAtTimestamp, 0),
                    webUrl: '/app/' + app.id,
                    hasChildren: has_children,
                    sharingStatus: coalesce(app.scope, 'personal')
                } AS node
            }

            WITH kb_nodes + collect(node) AS all_nodes

            // Apply sorting with explicit field mapping (Neo4j doesn't support dynamic property access)
            UNWIND all_nodes AS node
            WITH node,
                 CASE $sort_field
                     WHEN 'name' THEN node.name
                     WHEN 'createdAt' THEN node.createdAt
                     WHEN 'updatedAt' THEN node.updatedAt
                     WHEN 'nodeType' THEN node.nodeType
                     WHEN 'source' THEN node.source
                     WHEN 'connector' THEN node.connector
                     ELSE node.name
                 END AS sort_value
            ORDER BY
                CASE WHEN $sort_dir = 'ASC' THEN sort_value ELSE null END ASC,
                CASE WHEN $sort_dir = 'DESC' THEN sort_value ELSE null END DESC

            WITH collect(node) AS sorted_nodes

            RETURN {
                nodes: sorted_nodes[$skip..$skip + $limit],
                total: size(sorted_nodes)
            } AS result
            """

            results = await self.client.execute_query(
                query,
                parameters={
                    "user_key": user_key,
                    "org_id": org_id,
                    "user_app_ids": user_app_ids,
                    "include_kbs": include_kbs,
                    "include_apps": include_apps,
                    "skip": skip,
                    "limit": limit,
                    "sort_field": sort_field,
                    "sort_dir": sort_dir.upper(),
                },
                txn_id=transaction
            )

            if results and results[0].get("result"):
                return results[0]["result"]
            return {"nodes": [], "total": 0}

        except Exception as e:
            self.logger.error(f"❌ Get knowledge hub root nodes failed: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {"nodes": [], "total": 0}

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
        transaction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Unified method to get children of any node type.
        Neo4j implementation: Converts structured parameters to Cypher.
        """
        try:
            # Build filter conditions
            filter_conditions = []
            params = {
                "parent_id": parent_id,
                "org_id": org_id,
                "user_key": user_key,
                "skip": skip,
                "limit": limit,
                "sort_field": sort_field,
                "sort_dir": sort_dir.upper(),
                "only_containers": only_containers,
            }

            if search_query:
                params["search_query"] = search_query.lower()
                filter_conditions.append("toLower(node.name) CONTAINS $search_query")

            # Use parameterized node_types filter instead of string building
            if node_types:
                params["node_types"] = node_types
                filter_conditions.append("node.nodeType IN $node_types")

            if record_types:
                params["record_types"] = record_types
                filter_conditions.append("(node.recordType IS NOT NULL AND node.recordType IN $record_types)")

            if indexing_status:
                params["indexing_status"] = indexing_status
                filter_conditions.append("(node.indexingStatus IS NULL OR node.indexingStatus IN $indexing_status)")

            if created_at:
                if created_at.get("gte"):
                    params["created_at_gte"] = created_at["gte"]
                    filter_conditions.append("node.createdAt >= $created_at_gte")
                if created_at.get("lte"):
                    params["created_at_lte"] = created_at["lte"]
                    filter_conditions.append("node.createdAt <= $created_at_lte")

            if updated_at:
                if updated_at.get("gte"):
                    params["updated_at_gte"] = updated_at["gte"]
                    filter_conditions.append("node.updatedAt >= $updated_at_gte")
                if updated_at.get("lte"):
                    params["updated_at_lte"] = updated_at["lte"]
                    filter_conditions.append("node.updatedAt <= $updated_at_lte")

            if size:
                if size.get("gte"):
                    params["size_gte"] = size["gte"]
                    filter_conditions.append("(node.sizeInBytes IS NULL OR node.sizeInBytes >= $size_gte)")
                if size.get("lte"):
                    params["size_lte"] = size["lte"]
                    filter_conditions.append("(node.sizeInBytes IS NULL OR node.sizeInBytes <= $size_lte)")

            if origins:
                params["origins"] = origins
                filter_conditions.append("node.source IN $origins")

            if connector_ids and kb_ids:
                params["connector_ids"] = connector_ids
                params["kb_ids"] = kb_ids
                filter_conditions.append("(node.appId IN $connector_ids OR node.kbId IN $kb_ids)")
            elif connector_ids:
                params["connector_ids"] = connector_ids
                filter_conditions.append("node.appId IN $connector_ids")
            elif kb_ids:
                params["kb_ids"] = kb_ids
                filter_conditions.append("node.kbId IN $kb_ids")

            filter_clause = " AND ".join(filter_conditions) if filter_conditions else "true"

            # Generate query based on parent type
            if parent_type == "app":
                sub_query = self._get_app_children_cypher()
                params["source"] = "CONNECTOR"
            elif parent_type in ("kb", "recordGroup"):
                sub_query = self._get_record_group_children_cypher(parent_type)
                params["source"] = "KB" if parent_type == "kb" else "CONNECTOR"
            elif parent_type in ("folder", "record"):
                sub_query = self._get_record_children_cypher()
            else:
                return {"nodes": [], "total": 0}

            query = f"""
            {sub_query}

            // Apply filters using UNWIND for better performance
            UNWIND raw_children AS node
            WITH node WHERE {filter_clause}

            // Apply only_containers filter
            WITH node WHERE
                $only_containers = false
                OR node.hasChildren = true
                OR node.nodeType IN ['app', 'kb', 'recordGroup', 'folder']

            // Sort with explicit field mapping (Neo4j doesn't support dynamic property access)
            WITH node,
                 CASE $sort_field
                     WHEN 'name' THEN node.name
                     WHEN 'createdAt' THEN node.createdAt
                     WHEN 'updatedAt' THEN node.updatedAt
                     WHEN 'nodeType' THEN node.nodeType
                     WHEN 'source' THEN node.source
                     WHEN 'connector' THEN node.connector
                     WHEN 'recordType' THEN node.recordType
                     WHEN 'sizeInBytes' THEN node.sizeInBytes
                     WHEN 'indexingStatus' THEN node.indexingStatus
                     ELSE node.name
                 END AS sort_value
            ORDER BY
                CASE WHEN $sort_dir = 'ASC' THEN sort_value END ASC,
                CASE WHEN $sort_dir = 'DESC' THEN sort_value END DESC

            // Collect after sorting preserves order in Neo4j 5.x+
            WITH collect(node) AS sorted_nodes

            RETURN {{
                nodes: sorted_nodes[$skip..($skip + $limit)],
                total: size(sorted_nodes)
            }} AS result
            """

            results = await self.client.execute_query(query, parameters=params, txn_id=transaction)
            if results and results[0].get("result"):
                return results[0]["result"]
            return {"nodes": [], "total": 0}

        except Exception as e:
            self.logger.error(f"❌ Get knowledge hub children failed: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {"nodes": [], "total": 0}

    def _get_app_children_cypher(self) -> str:
        """Generate Cypher sub-query to fetch RecordGroups for an App.

        For connector apps, we return "root" record groups that the user has permission to access.
        A "root" is defined as a record group where either:
        1. It has no parent (parentExternalGroupId is null)
        2. OR its parent exists but the user does NOT have permission to access the parent
        3. OR its parent does not exist in our DB
        """
        return """
        MATCH (app:App {id: $parent_id})
        MATCH (u:User {id: $user_key})

        // Determine if this is a KB app
        WITH app, u, $parent_id AS parent_id, (app.type = 'KB') AS is_kb_app

        // ============================================
        // PATH 1: Direct user -> recordGroup permission
        // ============================================
        // For KB apps: Find via BELONGS_TO edge from recordGroup to app
        // For Connector apps: Use connectorId field
        OPTIONAL MATCH (u)-[:PERMISSION {type: 'USER'}]->(rg_direct:RecordGroup)
        WHERE NOT coalesce(rg_direct.isDeleted, false)
              AND CASE
                  WHEN is_kb_app THEN
                      rg_direct.connectorName = 'KB' AND EXISTS((rg_direct)-[:BELONGS_TO]->(app))
                  ELSE
                      rg_direct.connectorId = app.id
              END

        WITH app, u, parent_id, is_kb_app, collect(DISTINCT rg_direct) AS direct_rgs

        // ============================================
        // PATH 2: User -> Group/Role -> recordGroup permission
        // ============================================
        OPTIONAL MATCH (u)-[:PERMISSION {type: 'USER'}]->(grp)
        WHERE grp:Group OR grp:Role
        OPTIONAL MATCH (grp)-[grp_perm:PERMISSION]->(rg_group:RecordGroup)
        WHERE (grp_perm.type = 'GROUP' OR grp_perm.type = 'ROLE')
              AND NOT coalesce(rg_group.isDeleted, false)
              AND CASE
                  WHEN is_kb_app THEN
                      rg_group.connectorName = 'KB' AND EXISTS((rg_group)-[:BELONGS_TO]->(app))
                  ELSE
                      rg_group.connectorId = app.id
              END

        WITH app, u, parent_id, is_kb_app, direct_rgs, collect(DISTINCT rg_group) AS group_rgs

        // ============================================
        // PATH 3: User -> Organization -> recordGroup permission
        // ============================================
        OPTIONAL MATCH (u)-[belongs:BELONGS_TO {entityType: 'ORGANIZATION'}]->(org)
        OPTIONAL MATCH (org)-[:PERMISSION {type: 'ORG'}]->(rg_org:RecordGroup)
        WHERE NOT coalesce(rg_org.isDeleted, false)
              AND CASE
                  WHEN is_kb_app THEN
                      rg_org.connectorName = 'KB' AND EXISTS((rg_org)-[:BELONGS_TO]->(app))
                  ELSE
                      rg_org.connectorId = app.id
              END

        WITH app, u, parent_id, is_kb_app,
             direct_rgs + group_rgs + collect(DISTINCT rg_org) AS all_rgs_raw

        // Filter out nulls and get unique record groups
        WITH app, u, parent_id, is_kb_app,
             [rg IN all_rgs_raw WHERE rg IS NOT NULL] AS accessible_rgs

        // Get unique accessible record group IDs for parent checking
        WITH app, u, parent_id, is_kb_app, accessible_rgs,
             [rg IN accessible_rgs | rg.id] AS accessible_rg_ids

        // ============================================
        // Identify "root" record groups relative to user's access
        // ============================================
        UNWIND CASE WHEN size(accessible_rgs) = 0 THEN [null] ELSE accessible_rgs END AS rg
        WITH app, u, parent_id, is_kb_app, accessible_rg_ids, rg
        WHERE rg IS NOT NULL

        // Check if parent is accessible based on app type
        // KB: Check via BELONGS_TO edge from rg to parent_rg
        // Connector: Check via parentExternalGroupId field
        OPTIONAL MATCH (rg)-[:BELONGS_TO]->(parent_rg_kb:RecordGroup)
        WHERE is_kb_app AND parent_rg_kb.connectorName = 'KB' AND parent_rg_kb.id IN accessible_rg_ids

        OPTIONAL MATCH (parent_rg_conn:RecordGroup)
        WHERE NOT is_kb_app
              AND rg.parentExternalGroupId IS NOT NULL
              AND parent_rg_conn.connectorId = app.id
              AND parent_rg_conn.externalGroupId = rg.parentExternalGroupId
              AND parent_rg_conn.id IN accessible_rg_ids

        WITH app, u, parent_id, is_kb_app, accessible_rg_ids, rg,
             (parent_rg_kb IS NOT NULL OR parent_rg_conn IS NOT NULL) AS parent_is_accessible

        // Keep only root record groups (those without accessible parent)
        WHERE NOT parent_is_accessible

        // ============================================
        // Check for children - KB aware
        // ============================================
        // Check for nested record groups
        // KB: Use BELONGS_TO edge from child_rg to rg
        // Connector: Use BELONGS_TO edge OR parentExternalGroupId field
        OPTIONAL MATCH (child_rg:RecordGroup)-[:BELONGS_TO]->(rg)
        WHERE NOT coalesce(child_rg.isDeleted, false)
              AND CASE
                  WHEN is_kb_app THEN child_rg.connectorName = 'KB'
                  ELSE child_rg.connectorId = app.id
              END

        // Also check connector child RGs via parentExternalGroupId
        OPTIONAL MATCH (child_rg_field:RecordGroup)
        WHERE NOT is_kb_app
              AND NOT coalesce(child_rg_field.isDeleted, false)
              AND child_rg_field.connectorId = app.id
              AND child_rg_field.parentExternalGroupId IS NOT NULL
              AND child_rg_field.parentExternalGroupId = rg.externalGroupId

        WITH app, u, parent_id, is_kb_app, rg,
             collect(DISTINCT child_rg) + collect(DISTINCT child_rg_field) AS all_child_rgs

        WITH app, u, parent_id, is_kb_app, rg,
             size([c IN all_child_rgs WHERE c IS NOT NULL]) > 0 AS has_child_rgs

        // Check for records via BELONGS_TO (KB and Connector)
        OPTIONAL MATCH (record:Record)-[:BELONGS_TO]->(rg)
        WHERE NOT coalesce(record.isDeleted, false)
              AND record.externalParentId IS NULL

        // For Connector apps, also check records via recordGroupId field and inheritPermissions
        OPTIONAL MATCH (record_field:Record)
        WHERE NOT is_kb_app
              AND NOT coalesce(record_field.isDeleted, false)
              AND record_field.recordGroupId = rg.id

        OPTIONAL MATCH (record_inherit:Record)-[:INHERIT_PERMISSIONS]->(rg)
        WHERE NOT is_kb_app
              AND NOT coalesce(record_inherit.isDeleted, false)

        WITH parent_id, rg,
             has_child_rgs OR
             count(DISTINCT record) > 0 OR
             count(DISTINCT record_field) > 0 OR
             count(DISTINCT record_inherit) > 0 AS has_children

        // Build result
        RETURN collect({
            id: rg.id,
            name: rg.groupName,
            nodeType: 'recordGroup',
            parentId: 'apps/' + parent_id,
            source: 'CONNECTOR',
            connector: rg.connectorName,
            recordType: null,
            recordGroupType: rg.groupType,
            indexingStatus: null,
            createdAt: rg.createdAtTimestamp,
            updatedAt: rg.updatedAtTimestamp,
            sizeInBytes: null,
            mimeType: null,
            extension: null,
            webUrl: rg.webUrl,
            hasChildren: has_children
        }) AS raw_children
        """

    def _get_record_group_children_cypher(self, parent_type: str) -> str:
        """Generate Cypher sub-query to fetch children of a KB or RecordGroup.

        For KB: Children are nested via BELONGS_TO edges with connectorName='KB'
        For Connector: Children are nested via parentExternalGroupId field or BELONGS_TO edges

        Permission checking is applied for connector record groups.
        """
        source = "KB" if parent_type == "kb" else "CONNECTOR"
        return f"""
        MATCH (rg:RecordGroup {{id: $parent_id}})
        MATCH (u:User {{id: $user_key}})

        WITH rg, u, $parent_id AS parent_id, (rg.connectorName = 'KB') AS is_kb_rg

        // ============================================
        // GET NESTED RECORD GROUPS
        // ============================================
        // For KB: use BELONGS_TO edges
        // For Connector: use BELONGS_TO edges OR parentExternalGroupId field
        OPTIONAL MATCH (child_rg_edge:RecordGroup)-[:BELONGS_TO]->(rg)
        WHERE NOT coalesce(child_rg_edge.isDeleted, false)
              AND ((is_kb_rg AND child_rg_edge.connectorName = 'KB' AND child_rg_edge.orgId = $org_id)
                   OR (NOT is_kb_rg AND child_rg_edge.connectorId = rg.connectorId))

        // For Connector: also check parentExternalGroupId field
        OPTIONAL MATCH (child_rg_field:RecordGroup)
        WHERE NOT is_kb_rg
              AND NOT coalesce(child_rg_field.isDeleted, false)
              AND child_rg_field.connectorId = rg.connectorId
              AND child_rg_field.parentExternalGroupId = rg.externalGroupId

        WITH rg, u, parent_id, is_kb_rg,
             collect(DISTINCT child_rg_edge) + collect(DISTINCT child_rg_field) AS all_nested_rgs_raw

        // Filter nulls
        WITH rg, u, parent_id, is_kb_rg,
             [c IN all_nested_rgs_raw WHERE c IS NOT NULL] AS all_nested_rgs

        // ============================================
        // PERMISSION CHECK FOR CONNECTOR NESTED RGs
        // ============================================
        // For KB: Allow all (KB-level permission applies)
        // For Connector: Check direct, group, and org permissions
        UNWIND CASE WHEN size(all_nested_rgs) = 0 THEN [null] ELSE all_nested_rgs END AS child_rg
        WITH rg, u, parent_id, is_kb_rg, child_rg
        WHERE child_rg IS NOT NULL

        // Check permissions for connector nested RGs
        // Must capture relationship variables to check if permission edge exists
        OPTIONAL MATCH (u)-[direct_perm_edge:PERMISSION {{type: 'USER'}}]->(child_rg)
        WHERE NOT is_kb_rg
        WITH rg, u, parent_id, is_kb_rg, child_rg,
             CASE WHEN NOT is_kb_rg THEN direct_perm_edge IS NOT NULL ELSE false END AS has_direct_perm

        OPTIONAL MATCH (u)-[:PERMISSION {{type: 'USER'}}]->(grp)-[grp_perm:PERMISSION]->(child_rg)
        WHERE NOT is_kb_rg
              AND (grp:Group OR grp:Role)
              AND (grp_perm.type = 'GROUP' OR grp_perm.type = 'ROLE')
        WITH rg, u, parent_id, is_kb_rg, child_rg, has_direct_perm,
             CASE WHEN NOT is_kb_rg THEN grp_perm IS NOT NULL ELSE false END AS has_group_perm

        OPTIONAL MATCH (u)-[:BELONGS_TO {{entityType: 'ORGANIZATION'}}]->(org)-[org_perm:PERMISSION {{type: 'ORG'}}]->(child_rg)
        WHERE NOT is_kb_rg
        WITH rg, u, parent_id, is_kb_rg, child_rg, has_direct_perm, has_group_perm,
             CASE WHEN NOT is_kb_rg THEN org_perm IS NOT NULL ELSE false END AS has_org_perm

        // Filter: KB allows all, Connector requires permission
        // Keep permission variables in scope for WHERE clause
        WITH rg, u, parent_id, is_kb_rg, child_rg, has_direct_perm, has_group_perm, has_org_perm
        WHERE child_rg IS NOT NULL
              AND (is_kb_rg OR has_direct_perm OR has_group_perm OR has_org_perm)

        // ============================================
        // CHECK HAS_CHILDREN FOR NESTED RGs
        // ============================================
        // Check for sub-record groups
        OPTIONAL MATCH (sub_rg:RecordGroup)-[:BELONGS_TO]->(child_rg)
        WHERE NOT coalesce(sub_rg.isDeleted, false)
              AND ((is_kb_rg AND sub_rg.connectorName = 'KB')
                   OR (NOT is_kb_rg AND sub_rg.connectorId = rg.connectorId))

        // Check for records in nested RG (for KB: belongsTo, for Connector: multiple methods)
        OPTIONAL MATCH (sub_record:Record)-[:BELONGS_TO]->(child_rg)
        WHERE NOT coalesce(sub_record.isDeleted, false) AND sub_record.externalParentId IS NULL

        // For Connector: also check recordGroupId field and inheritPermissions
        OPTIONAL MATCH (sub_record_field:Record)
        WHERE NOT is_kb_rg
              AND NOT coalesce(sub_record_field.isDeleted, false)
              AND sub_record_field.recordGroupId = child_rg.id

        OPTIONAL MATCH (sub_record_inherit:Record)-[:INHERIT_PERMISSIONS]->(child_rg)
        WHERE NOT is_kb_rg
              AND NOT coalesce(sub_record_inherit.isDeleted, false)

        // Calculate has_children BEFORE collect
        WITH rg, u, parent_id, is_kb_rg, child_rg,
             count(DISTINCT sub_rg) AS sub_rg_count,
             count(DISTINCT sub_record) + count(DISTINCT sub_record_field) + count(DISTINCT sub_record_inherit) AS sub_record_count

        WITH rg, u, parent_id, is_kb_rg,
             collect({{
                 id: child_rg.id,
                 name: child_rg.groupName,
                 nodeType: 'recordGroup',
                 parentId: 'recordGroups/' + parent_id,
                 source: '{source}',
                 connector: child_rg.connectorName,
                 connectorId: CASE WHEN '{source}' = 'CONNECTOR' THEN child_rg.connectorId ELSE null END,
                 kbId: CASE WHEN '{source}' = 'KB' THEN parent_id ELSE null END,
                 recordType: null,
                 recordGroupType: child_rg.groupType,
                 indexingStatus: null,
                 createdAt: child_rg.createdAtTimestamp,
                 updatedAt: child_rg.updatedAtTimestamp,
                 sizeInBytes: null,
                 mimeType: null,
                 extension: null,
                 webUrl: child_rg.webUrl,
                 hasChildren: sub_rg_count > 0 OR sub_record_count > 0
             }}) AS rg_nodes

        // ============================================
        // GET DIRECT CHILDREN RECORDS
        // ============================================
        // For KB: Use BELONGS_TO edges
        // For Connector: Use BELONGS_TO edges (permission checking via 6 paths)

        // Get records via BELONGS_TO
        OPTIONAL MATCH (record:Record)-[:BELONGS_TO]->(rg)
        WHERE NOT coalesce(record.isDeleted, false)
              AND record.orgId = $org_id
              AND record.externalParentId IS NULL

        WITH rg, u, parent_id, is_kb_rg, rg_nodes, collect(DISTINCT record) AS records_from_belongs

        // ============================================
        // PERMISSION CHECK FOR CONNECTOR RECORDS (6 Paths)
        // ============================================
        UNWIND CASE WHEN size(records_from_belongs) = 0 THEN [null] ELSE records_from_belongs END AS record
        WITH rg, u, parent_id, is_kb_rg, rg_nodes, record
        WHERE record IS NOT NULL

        // For KB: Allow all records (KB-level permission applies)
        // For Connector: Check 6 permission paths

        // Path 1: user -> org -> record (direct org permission)
        OPTIONAL MATCH (u)-[:BELONGS_TO {{entityType: 'ORGANIZATION'}}]->(org1)-[:PERMISSION {{type: 'ORG'}}]->(record)
        WHERE NOT is_kb_rg AND record IS NOT NULL

        // Path 2: user -> org -> recordGroup -> (nested RGs) -> record (via inheritPermissions)
        OPTIONAL MATCH (u)-[:BELONGS_TO {{entityType: 'ORGANIZATION'}}]->(org2)-[:PERMISSION {{type: 'ORG'}}]->(perm_rg2:RecordGroup)
        OPTIONAL MATCH (record)-[:INHERIT_PERMISSIONS]->(perm_rg2)
        WHERE NOT is_kb_rg AND record IS NOT NULL

        // Path 3: user -> record (direct user permission)
        OPTIONAL MATCH (u)-[:PERMISSION {{type: 'USER'}}]->(record)
        WHERE NOT is_kb_rg AND record IS NOT NULL

        // Path 4: user -> recordGroup -> (nested RGs) -> record (via inheritPermissions)
        OPTIONAL MATCH (u)-[:PERMISSION {{type: 'USER'}}]->(perm_rg4:RecordGroup)
        OPTIONAL MATCH (record)-[:INHERIT_PERMISSIONS]->(perm_rg4)
        WHERE NOT is_kb_rg AND record IS NOT NULL

        // Path 5: user -> group -> record (group permission)
        OPTIONAL MATCH (u)-[:PERMISSION {{type: 'USER'}}]->(grp5)
        WHERE grp5:Group OR grp5:Role
        OPTIONAL MATCH (grp5)-[grp5_perm:PERMISSION]->(record)
        WHERE NOT is_kb_rg AND record IS NOT NULL
              AND (grp5_perm.type = 'GROUP' OR grp5_perm.type = 'ROLE')

        // Path 6: user -> group -> recordGroup -> (nested RGs) -> record (via inheritPermissions)
        OPTIONAL MATCH (u)-[:PERMISSION {{type: 'USER'}}]->(grp6)
        WHERE grp6:Group OR grp6:Role
        OPTIONAL MATCH (grp6)-[grp6_perm:PERMISSION]->(perm_rg6:RecordGroup)
        WHERE NOT is_kb_rg AND (grp6_perm.type = 'GROUP' OR grp6_perm.type = 'ROLE')
        OPTIONAL MATCH (record)-[:INHERIT_PERMISSIONS]->(perm_rg6)
        WHERE NOT is_kb_rg AND record IS NOT NULL

        WITH rg, u, parent_id, is_kb_rg, rg_nodes, record,
             org1 IS NOT NULL AS path1,
             (org2 IS NOT NULL AND perm_rg2 IS NOT NULL) AS path2,
             record IS NOT NULL AS path3_check,
             (perm_rg4 IS NOT NULL) AS path4,
             (grp5 IS NOT NULL AND grp5_perm IS NOT NULL) AS path5,
             (grp6 IS NOT NULL AND perm_rg6 IS NOT NULL) AS path6

        // Filter: KB allows all, Connector requires at least one permission path
        // Keep path variables in scope for WHERE clause
        WITH rg, u, parent_id, is_kb_rg, rg_nodes, record, path1, path2, path3_check, path4, path5, path6
        WHERE record IS NOT NULL
              AND (is_kb_rg OR path1 OR path2 OR path3_check OR path4 OR path5 OR path6)

        // ============================================
        // BUILD RECORD NODES
        // ============================================
        // Check if record is folder
        OPTIONAL MATCH (record)-[:IS_OF_TYPE]->(f:File)

        WITH rg, u, parent_id, is_kb_rg, rg_nodes, record, f,
             CASE WHEN f IS NOT NULL AND f.isFile = false THEN true ELSE false END AS is_folder

        // Check for record children (via RECORD_RELATION)
        OPTIONAL MATCH (record)-[:RECORD_RELATION]->(child_record:Record)
        WHERE NOT coalesce(child_record.isDeleted, false)

        // Calculate child count BEFORE collect
        WITH rg, parent_id, is_kb_rg, rg_nodes, record, f, is_folder,
             count(DISTINCT child_record) AS child_count

        WITH rg_nodes,
             collect({{
                 id: record.id,
                 name: record.recordName,
                 nodeType: CASE WHEN is_folder THEN 'folder' ELSE 'record' END,
                 parentId: 'recordGroups/' + rg.id,
                 source: '{source}',
                 connector: record.connectorName,
                 connectorId: CASE WHEN '{source}' = 'CONNECTOR' THEN record.connectorId ELSE null END,
                 kbId: CASE WHEN '{source}' = 'KB' THEN record.connectorId ELSE null END,
                 recordType: record.recordType,
                 recordGroupType: null,
                 indexingStatus: record.indexingStatus,
                 createdAt: record.createdAtTimestamp,
                 updatedAt: record.updatedAtTimestamp,
                 sizeInBytes: coalesce(record.sizeInBytes, f.fileSizeInBytes),
                 mimeType: record.mimeType,
                 extension: f.extension,
                 webUrl: record.webUrl,
                 hasChildren: child_count > 0,
                 previewRenderable: coalesce(record.previewRenderable, true)
             }}) AS record_nodes

        // Filter out records with null IDs (from empty UNWIND)
        WITH rg_nodes, [r IN record_nodes WHERE r.id IS NOT NULL] AS filtered_record_nodes

        // Combine results
        WITH [n IN rg_nodes WHERE n.id IS NOT NULL] + filtered_record_nodes AS raw_children

        RETURN raw_children
        """

    def _get_record_children_cypher(self) -> str:
        """Generate Cypher sub-query to fetch children of a Folder/Record.

        Children are found via RECORD_RELATION edges with relationshipType
        IN ['PARENT_CHILD', 'ATTACHMENT'].

        For connector records, permission checking is applied:
        1. inheritPermissions edge (record -> recordGroup)
        2. Direct user -> record permission
        3. User -> group -> record permission
        4. User -> org -> record permission
        5. User -> org -> recordGroup -> record (via inheritPermissions)

        For KB records, all children are visible (KB-level permission applies).
        """
        return """
        MATCH (parent_record:Record {id: $parent_id})
        MATCH (u:User {id: $user_key})

        // Determine if parent is from KB or connector
        OPTIONAL MATCH (parent_connector:RecordGroup {id: parent_record.connectorId})
        OPTIONAL MATCH (parent_app:App {id: parent_record.connectorId})

        WITH parent_record, u, $parent_id AS parent_id,
             (parent_record.connectorName = 'KB' OR
              (parent_connector IS NOT NULL AND parent_connector.type = 'KB')) AS is_kb_parent

        // Get children via RECORD_RELATION
        OPTIONAL MATCH (parent_record)-[rr:RECORD_RELATION]->(record:Record)
        WHERE rr.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT']
              AND NOT coalesce(record.isDeleted, false)
              AND record.orgId = $org_id

        WITH parent_record, u, parent_id, is_kb_parent, collect(DISTINCT record) AS records

        // Process each record
        UNWIND CASE WHEN size(records) = 0 THEN [null] ELSE records END AS record
        WITH u, parent_id, is_kb_parent, record
        WHERE record IS NOT NULL

        // ============================================
        // PERMISSION CHECKING FOR CONNECTOR RECORDS
        // ============================================
        // For KB: Allow all (KB-level permission applies)
        // For Connector: Check 5 permission paths

        // Path 1: inheritPermissions edge (record -> recordGroup)
        OPTIONAL MATCH (record)-[:INHERIT_PERMISSIONS]->(inherit_rg:RecordGroup)
        WHERE NOT is_kb_parent

        // Path 2: Direct user -> record permission
        OPTIONAL MATCH (u)-[direct_perm:PERMISSION {type: 'USER'}]->(record)
        WHERE NOT is_kb_parent

        // Path 3: User -> group/role -> record permission
        OPTIONAL MATCH (u)-[:PERMISSION {type: 'USER'}]->(grp)
        WHERE NOT is_kb_parent AND (grp:Group OR grp:Role)
        OPTIONAL MATCH (grp)-[grp_perm:PERMISSION]->(record)
        WHERE NOT is_kb_parent AND (grp_perm.type = 'GROUP' OR grp_perm.type = 'ROLE')

        // Path 4: User -> org -> record permission
        OPTIONAL MATCH (u)-[:BELONGS_TO {entityType: 'ORGANIZATION'}]->(org)
        OPTIONAL MATCH (org)-[org_perm:PERMISSION {type: 'ORG'}]->(record)
        WHERE NOT is_kb_parent

        // Path 5: User -> org -> recordGroup -> record (via inheritPermissions)
        OPTIONAL MATCH (u)-[:BELONGS_TO {entityType: 'ORGANIZATION'}]->(org2)
        OPTIONAL MATCH (org2)-[:PERMISSION {type: 'ORG'}]->(perm_rg:RecordGroup)
        WHERE NOT is_kb_parent
        OPTIONAL MATCH (record)-[:INHERIT_PERMISSIONS]->(perm_rg)
        WHERE NOT is_kb_parent

        WITH u, parent_id, is_kb_parent, record,
             inherit_rg IS NOT NULL AS has_inherit_perm,
             direct_perm IS NOT NULL AS has_direct_perm,
             grp_perm IS NOT NULL AS has_group_perm,
             org_perm IS NOT NULL AS has_org_perm,
             perm_rg IS NOT NULL AS has_org_rg_perm

        // Filter: KB allows all, Connector requires at least one permission path
        // Keep permission variables in scope for WHERE clause
        WITH u, parent_id, is_kb_parent, record, has_inherit_perm, has_direct_perm, has_group_perm, has_org_perm, has_org_rg_perm
        WHERE is_kb_parent OR has_inherit_perm OR has_direct_perm OR has_group_perm OR has_org_perm OR has_org_rg_perm

        // ============================================
        // BUILD RECORD INFO
        // ============================================
        // Check if record is folder
        OPTIONAL MATCH (record)-[:IS_OF_TYPE]->(f:File)

        WITH u, parent_id, is_kb_parent, record, f,
             CASE WHEN f IS NOT NULL AND f.isFile = false THEN true ELSE false END AS is_folder

        // Determine source
        OPTIONAL MATCH (record_connector:RecordGroup {id: record.connectorId})
        WITH u, parent_id, is_kb_parent, record, f, is_folder,
             CASE WHEN record_connector IS NOT NULL AND record_connector.connectorName = 'KB'
                  THEN 'KB' ELSE 'CONNECTOR' END AS source

        // ============================================
        // CHECK HAS_CHILDREN WITH PERMISSION CHECKING
        // ============================================
        // Get potential children
        OPTIONAL MATCH (record)-[child_rr:RECORD_RELATION]->(child:Record)
        WHERE child_rr.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT']
              AND NOT coalesce(child.isDeleted, false)

        WITH u, parent_id, is_kb_parent, record, f, is_folder, source, collect(DISTINCT child) AS children

        // For KB: All children count
        // For Connector: Need permission-aware child counting
        // Calculate child count based on permission (simplified - counting all visible children)
        WITH u, parent_id, is_kb_parent, record, f, is_folder, source,
             CASE
                 WHEN is_kb_parent THEN size(children)
                 ELSE size([c IN children WHERE c IS NOT NULL])
             END AS child_count

        // For connector children, we should ideally check permissions on each child
        // However, for performance, we simplify to count all children when parent has permission
        // (If user can see parent, they can see children based on folder structure)

        WITH collect({
            id: record.id,
            name: record.recordName,
            nodeType: CASE WHEN is_folder THEN 'folder' ELSE 'record' END,
            parentId: 'records/' + parent_id,
            source: source,
            connector: record.connectorName,
            connectorId: CASE WHEN source = 'CONNECTOR' THEN record.connectorId ELSE null END,
            kbId: CASE WHEN source = 'KB' THEN record.connectorId ELSE null END,
            recordType: record.recordType,
            recordGroupType: null,
            indexingStatus: record.indexingStatus,
            createdAt: record.createdAtTimestamp,
            updatedAt: record.updatedAtTimestamp,
            sizeInBytes: coalesce(record.sizeInBytes, f.fileSizeInBytes),
            mimeType: record.mimeType,
            extension: f.extension,
            webUrl: record.webUrl,
            hasChildren: child_count > 0,
            previewRenderable: coalesce(record.previewRenderable, true)
        }) AS raw_children

        RETURN raw_children
        """

    async def get_knowledge_hub_recursive_search(
        self,
        parent_id: str,
        parent_type: str,
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
        transaction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Search recursively within a parent node and all its descendants.
        Uses graph traversal to find all nested children.
        """
        try:
            # Build filter conditions
            filter_conditions = []
            params = {
                "parent_id": parent_id,
                "org_id": org_id,
                "user_key": user_key,
                "skip": skip,
                "limit": limit,
                "sort_field": sort_field,
                "sort_dir": sort_dir.upper(),
                "only_containers": only_containers,
            }

            # Search query filter - will be combined with other conditions
            if search_query:
                params["search_query"] = search_query.lower()

            # Node type filter
            if node_types:
                type_conditions = []
                for nt in node_types:
                    if nt == "folder":
                        type_conditions.append("node.nodeType = 'folder'")
                    elif nt == "record":
                        type_conditions.append("node.nodeType = 'record'")
                    elif nt == "recordGroup":
                        type_conditions.append("node.nodeType = 'recordGroup'")
                if type_conditions:
                    filter_conditions.append(f"({' OR '.join(type_conditions)})")

            if record_types:
                params["record_types"] = record_types
                filter_conditions.append("(node.recordType IS NOT NULL AND node.recordType IN $record_types)")

            if indexing_status:
                params["indexing_status"] = indexing_status
                filter_conditions.append("(node.indexingStatus IS NULL OR node.indexingStatus IN $indexing_status)")

            if created_at:
                if created_at.get("gte"):
                    params["created_at_gte"] = created_at["gte"]
                    filter_conditions.append("node.createdAt >= $created_at_gte")
                if created_at.get("lte"):
                    params["created_at_lte"] = created_at["lte"]
                    filter_conditions.append("node.createdAt <= $created_at_lte")

            if updated_at:
                if updated_at.get("gte"):
                    params["updated_at_gte"] = updated_at["gte"]
                    filter_conditions.append("node.updatedAt >= $updated_at_gte")
                if updated_at.get("lte"):
                    params["updated_at_lte"] = updated_at["lte"]
                    filter_conditions.append("node.updatedAt <= $updated_at_lte")

            if size:
                if size.get("gte"):
                    params["size_gte"] = size["gte"]
                    filter_conditions.append("(node.sizeInBytes IS NULL OR node.sizeInBytes >= $size_gte)")
                if size.get("lte"):
                    params["size_lte"] = size["lte"]
                    filter_conditions.append("(node.sizeInBytes IS NULL OR node.sizeInBytes <= $size_lte)")

            if origins:
                params["origins"] = origins
                filter_conditions.append("node.source IN $origins")

            if connector_ids and kb_ids:
                params["connector_ids"] = connector_ids
                params["kb_ids"] = kb_ids
                filter_conditions.append(
                    "((node.nodeType = 'app' AND node.id IN $connector_ids) OR (node.connectorId IN $connector_ids) OR "
                    "(node.nodeType = 'kb' AND node.id IN $kb_ids) OR (node.kbId IN $kb_ids))"
                )
            elif connector_ids:
                params["connector_ids"] = connector_ids
                filter_conditions.append("(node.nodeType = 'app' AND node.id IN $connector_ids) OR (node.connectorId IN $connector_ids)")
            elif kb_ids:
                params["kb_ids"] = kb_ids
                filter_conditions.append("(node.nodeType = 'kb' AND node.id IN $kb_ids) OR (node.kbId IN $kb_ids)")

            # Add search condition to filter conditions if present
            if search_query:
                filter_conditions.insert(0, "toLower(node.name) CONTAINS $search_query")

            filter_clause = " AND ".join(filter_conditions) if filter_conditions else "true"

            # Determine traversal based on parent type
            if parent_type in ("kb", "recordGroup"):
                source_value = "KB" if parent_type == "kb" else "CONNECTOR"
                query = f"""
                MATCH (parent:RecordGroup {{id: $parent_id}})
                MATCH (u:User {{id: $user_key}})

                WITH parent, u, (parent.connectorName = 'KB') AS is_kb_parent

                // ==================== GET ALL RECORDS ====================
                // Use separate CALL blocks to avoid dependent OPTIONAL MATCH issue
                CALL {{
                    WITH parent, u, is_kb_parent
                    // Direct children records
                    OPTIONAL MATCH (direct_record:Record)-[:BELONGS_TO]->(parent)
                    WHERE NOT coalesce(direct_record.isDeleted, false)
                          AND direct_record.orgId = $org_id
                          AND direct_record.externalParentId IS NULL
                    RETURN collect(DISTINCT direct_record) AS direct_records
                }}

                CALL {{
                    WITH parent, u, is_kb_parent
                    // Get direct records first, then traverse nested
                    MATCH (dr:Record)-[:BELONGS_TO]->(parent)
                    WHERE NOT coalesce(dr.isDeleted, false)
                          AND dr.orgId = $org_id
                          AND dr.externalParentId IS NULL
                    OPTIONAL MATCH (dr)-[:RECORD_RELATION*1..10]->(nested:Record)
                    WHERE NOT coalesce(nested.isDeleted, false) AND nested.orgId = $org_id
                    RETURN collect(DISTINCT nested) AS nested_records
                }}

                WITH parent, u, is_kb_parent, direct_records + nested_records AS all_records_raw
                WITH parent, u, is_kb_parent, [r IN all_records_raw WHERE r IS NOT NULL] AS all_records

                // ==================== GET NESTED RECORD GROUPS ====================
                OPTIONAL MATCH (child_rg:RecordGroup)-[:BELONGS_TO]->(parent)
                WHERE NOT coalesce(child_rg.isDeleted, false)

                WITH parent, u, is_kb_parent, all_records, collect(DISTINCT child_rg) AS nested_rgs

                // ==================== PROCESS RECORDS ====================
                UNWIND CASE WHEN size(all_records) = 0 THEN [null] ELSE all_records END AS record
                WITH parent, u, nested_rgs, record
                WHERE record IS NOT NULL

                OPTIONAL MATCH (record)-[:IS_OF_TYPE]->(f:File)
                OPTIONAL MATCH (record)-[:RECORD_RELATION]->(child:Record)
                WHERE NOT coalesce(child.isDeleted, false)

                // Calculate child count BEFORE collect
                WITH parent, nested_rgs, record, f,
                     CASE WHEN f IS NOT NULL AND f.isFile = false THEN 'folder' ELSE 'record' END AS nodeType,
                     count(DISTINCT child) AS child_count

                OPTIONAL MATCH (record_connector:RecordGroup {{id: record.connectorId}})
                WITH parent, nested_rgs,
                     collect(DISTINCT {{
                         id: record.id,
                         name: record.recordName,
                         nodeType: nodeType,
                         parentId: record.externalParentId,
                         source: CASE WHEN record_connector IS NOT NULL AND record_connector.connectorName = 'KB'
                                      THEN 'KB' ELSE 'CONNECTOR' END,
                         connector: record.connectorName,
                         connectorId: record.connectorId,
                         kbId: CASE WHEN record_connector IS NOT NULL AND record_connector.connectorName = 'KB'
                                    THEN record.connectorId ELSE null END,
                         recordType: record.recordType,
                         recordGroupType: null,
                         indexingStatus: record.indexingStatus,
                         createdAt: record.createdAtTimestamp,
                         updatedAt: record.updatedAtTimestamp,
                         sizeInBytes: coalesce(record.sizeInBytes, f.fileSizeInBytes),
                         mimeType: record.mimeType,
                         extension: f.extension,
                         webUrl: record.webUrl,
                         hasChildren: child_count > 0,
                         previewRenderable: coalesce(record.previewRenderable, true)
                     }}) AS record_nodes

                // ==================== PROCESS NESTED RECORD GROUPS ====================
                UNWIND CASE WHEN size(nested_rgs) = 0 THEN [null] ELSE nested_rgs END AS rg
                WITH record_nodes, rg
                WHERE rg IS NOT NULL

                OPTIONAL MATCH (sub_rg:RecordGroup)-[:BELONGS_TO]->(rg)
                WHERE NOT coalesce(sub_rg.isDeleted, false)
                OPTIONAL MATCH (sub_record:Record)-[:BELONGS_TO]->(rg)
                WHERE NOT coalesce(sub_record.isDeleted, false)

                // Calculate children counts BEFORE collect
                WITH record_nodes, rg,
                     count(DISTINCT sub_rg) AS sub_rg_count,
                     count(DISTINCT sub_record) AS sub_record_count

                WITH record_nodes,
                     collect(DISTINCT {{
                         id: rg.id,
                         name: rg.groupName,
                         nodeType: 'recordGroup',
                         parentId: $parent_id,
                         source: '{source_value}',
                         connector: rg.connectorName,
                         connectorId: CASE WHEN '{source_value}' = 'CONNECTOR' THEN rg.connectorId ELSE null END,
                         kbId: CASE WHEN '{source_value}' = 'KB' THEN $parent_id ELSE null END,
                         recordType: null,
                         recordGroupType: rg.groupType,
                         indexingStatus: null,
                         createdAt: rg.createdAtTimestamp,
                         updatedAt: rg.updatedAtTimestamp,
                         sizeInBytes: null,
                         mimeType: null,
                         extension: null,
                         webUrl: rg.webUrl,
                         hasChildren: sub_rg_count > 0 OR sub_record_count > 0
                     }}) AS rg_nodes

                // ==================== COMBINE AND FILTER ====================
                WITH record_nodes + rg_nodes AS all_nodes_raw
                WITH [n IN all_nodes_raw WHERE n.id IS NOT NULL] AS all_nodes

                // Deduplicate
                UNWIND all_nodes AS node
                WITH DISTINCT node.id AS id, node
                WITH collect(node) AS unique_nodes

                // Apply filters
                UNWIND unique_nodes AS node
                WITH node WHERE {filter_clause}

                // Apply only_containers filter
                WITH node WHERE $only_containers = false
                     OR node.hasChildren = true
                     OR node.nodeType IN ['app', 'kb', 'recordGroup', 'folder']

                // Sort with explicit field mapping (Neo4j doesn't support dynamic property access)
                WITH node,
                     CASE $sort_field
                         WHEN 'name' THEN node.name
                         WHEN 'createdAt' THEN node.createdAt
                         WHEN 'updatedAt' THEN node.updatedAt
                         WHEN 'nodeType' THEN node.nodeType
                         WHEN 'source' THEN node.source
                         WHEN 'connector' THEN node.connector
                         WHEN 'recordType' THEN node.recordType
                         WHEN 'sizeInBytes' THEN node.sizeInBytes
                         WHEN 'indexingStatus' THEN node.indexingStatus
                         ELSE node.name
                     END AS sort_value
                ORDER BY
                    CASE WHEN $sort_dir = 'ASC' THEN sort_value END ASC,
                    CASE WHEN $sort_dir = 'DESC' THEN sort_value END DESC

                WITH collect(node) AS sorted_nodes

                RETURN {{
                    nodes: sorted_nodes[$skip..($skip + $limit)],
                    total: size(sorted_nodes)
                }} AS result
                """
            elif parent_type in ("folder", "record"):
                query = f"""
                MATCH (parent:Record {{id: $parent_id}})
                MATCH (u:User {{id: $user_key}})

                // Traverse via RECORD_RELATION
                OPTIONAL MATCH (parent)-[:RECORD_RELATION*1..10]->(v:Record)
                WHERE NOT coalesce(v.isDeleted, false) AND v.orgId = $org_id

                WITH collect(DISTINCT v) AS all_records

                // Process each record
                UNWIND CASE WHEN size(all_records) = 0 THEN [null] ELSE all_records END AS v
                WITH v WHERE v IS NOT NULL

                OPTIONAL MATCH (v)-[:IS_OF_TYPE]->(f:File)
                OPTIONAL MATCH (v)-[:RECORD_RELATION]->(child:Record)
                WHERE NOT coalesce(child.isDeleted, false)

                // Calculate child count BEFORE collect
                WITH v, f,
                     CASE WHEN f IS NOT NULL AND f.isFile = false THEN 'folder' ELSE 'record' END AS nodeType,
                     count(DISTINCT child) AS child_count

                OPTIONAL MATCH (record_connector:RecordGroup {{id: v.connectorId}})
                WITH collect(DISTINCT {{
                    id: v.id,
                    name: v.recordName,
                    nodeType: nodeType,
                    parentId: v.externalParentId,
                    source: CASE WHEN record_connector IS NOT NULL AND record_connector.connectorName = 'KB'
                                 THEN 'KB' ELSE 'CONNECTOR' END,
                    connector: v.connectorName,
                    connectorId: v.connectorId,
                    kbId: CASE WHEN record_connector IS NOT NULL AND record_connector.connectorName = 'KB'
                               THEN v.connectorId ELSE null END,
                    recordType: v.recordType,
                    recordGroupType: null,
                    indexingStatus: v.indexingStatus,
                    createdAt: v.createdAtTimestamp,
                    updatedAt: v.updatedAtTimestamp,
                    sizeInBytes: coalesce(v.sizeInBytes, f.fileSizeInBytes),
                    mimeType: v.mimeType,
                    extension: f.extension,
                    webUrl: v.webUrl,
                    hasChildren: child_count > 0,
                    previewRenderable: coalesce(v.previewRenderable, true)
                }}) AS all_nodes

                // Apply filters
                UNWIND all_nodes AS node
                WITH node WHERE {filter_clause}

                // Apply only_containers filter
                WITH node WHERE $only_containers = false
                     OR node.hasChildren = true
                     OR node.nodeType IN ['folder']

                // Sort with explicit field mapping (Neo4j doesn't support dynamic property access)
                WITH node,
                     CASE $sort_field
                         WHEN 'name' THEN node.name
                         WHEN 'createdAt' THEN node.createdAt
                         WHEN 'updatedAt' THEN node.updatedAt
                         WHEN 'nodeType' THEN node.nodeType
                         WHEN 'source' THEN node.source
                         WHEN 'connector' THEN node.connector
                         WHEN 'recordType' THEN node.recordType
                         WHEN 'sizeInBytes' THEN node.sizeInBytes
                         WHEN 'indexingStatus' THEN node.indexingStatus
                         ELSE node.name
                     END AS sort_value
                ORDER BY
                    CASE WHEN $sort_dir = 'ASC' THEN sort_value END ASC,
                    CASE WHEN $sort_dir = 'DESC' THEN sort_value END DESC

                WITH collect(node) AS sorted_nodes

                RETURN {{
                    nodes: sorted_nodes[$skip..($skip + $limit)],
                    total: size(sorted_nodes)
                }} AS result
                """
            elif parent_type == "app":
                query = f"""
                MATCH (app:App {{id: $parent_id}})
                MATCH (u:User {{id: $user_key}})

                // ==================== GET ALL DATA ====================
                // Get record groups for this app
                CALL {{
                    WITH app
                    OPTIONAL MATCH (rg:RecordGroup)
                    WHERE rg.connectorId = app.id AND NOT coalesce(rg.isDeleted, false)
                    RETURN collect(DISTINCT rg) AS rgs
                }}

                // Get records for these record groups (use separate CALL to avoid dependent match)
                CALL {{
                    WITH app
                    MATCH (rg:RecordGroup)
                    WHERE rg.connectorId = app.id AND NOT coalesce(rg.isDeleted, false)
                    OPTIONAL MATCH (record:Record)-[:BELONGS_TO]->(rg)
                    WHERE NOT coalesce(record.isDeleted, false) AND record.orgId = $org_id
                    RETURN collect(DISTINCT record) AS direct_records
                }}

                CALL {{
                    WITH app
                    MATCH (rg:RecordGroup)
                    WHERE rg.connectorId = app.id AND NOT coalesce(rg.isDeleted, false)
                    MATCH (record:Record)-[:BELONGS_TO]->(rg)
                    WHERE NOT coalesce(record.isDeleted, false) AND record.orgId = $org_id
                    OPTIONAL MATCH (record)-[:RECORD_RELATION*1..10]->(nested:Record)
                    WHERE NOT coalesce(nested.isDeleted, false) AND nested.orgId = $org_id
                    RETURN collect(DISTINCT nested) AS nested_records
                }}

                WITH rgs, direct_records + nested_records AS all_records_raw
                WITH rgs, [r IN all_records_raw WHERE r IS NOT NULL] AS all_records

                // ==================== PROCESS RECORD GROUPS ====================
                UNWIND CASE WHEN size(rgs) = 0 THEN [null] ELSE rgs END AS rg
                WITH all_records, rg
                WHERE rg IS NOT NULL

                OPTIONAL MATCH (sub_rg:RecordGroup)-[:BELONGS_TO]->(rg)
                WHERE NOT coalesce(sub_rg.isDeleted, false)
                OPTIONAL MATCH (sub_record:Record)-[:BELONGS_TO]->(rg)
                WHERE NOT coalesce(sub_record.isDeleted, false)

                // Calculate children counts BEFORE collect
                WITH all_records, rg,
                     count(DISTINCT sub_rg) AS sub_rg_count,
                     count(DISTINCT sub_record) AS sub_record_count

                WITH all_records, collect(DISTINCT {{
                    id: rg.id,
                    name: rg.groupName,
                    nodeType: 'recordGroup',
                    parentId: $parent_id,
                    source: 'CONNECTOR',
                    connector: rg.connectorName,
                    connectorId: rg.connectorId,
                    kbId: null,
                    recordType: null,
                    recordGroupType: rg.groupType,
                    indexingStatus: null,
                    createdAt: rg.createdAtTimestamp,
                    updatedAt: rg.updatedAtTimestamp,
                    sizeInBytes: null,
                    mimeType: null,
                    extension: null,
                    webUrl: rg.webUrl,
                    hasChildren: sub_rg_count > 0 OR sub_record_count > 0
                }}) AS rg_nodes

                // ==================== PROCESS RECORDS ====================
                UNWIND CASE WHEN size(all_records) = 0 THEN [null] ELSE all_records END AS record
                WITH rg_nodes, record
                WHERE record IS NOT NULL

                OPTIONAL MATCH (record)-[:IS_OF_TYPE]->(f:File)
                OPTIONAL MATCH (record)-[:RECORD_RELATION]->(child:Record)
                WHERE NOT coalesce(child.isDeleted, false)

                // Calculate child count BEFORE collect
                WITH rg_nodes, record, f,
                     CASE WHEN f IS NOT NULL AND f.isFile = false THEN 'folder' ELSE 'record' END AS nodeType,
                     count(DISTINCT child) AS child_count

                WITH rg_nodes + collect(DISTINCT {{
                    id: record.id,
                    name: record.recordName,
                    nodeType: nodeType,
                    parentId: record.externalParentId,
                    source: 'CONNECTOR',
                    connector: record.connectorName,
                    connectorId: record.connectorId,
                    kbId: null,
                    recordType: record.recordType,
                    recordGroupType: null,
                    indexingStatus: record.indexingStatus,
                    createdAt: record.createdAtTimestamp,
                    updatedAt: record.updatedAtTimestamp,
                    sizeInBytes: coalesce(record.sizeInBytes, f.fileSizeInBytes),
                    mimeType: record.mimeType,
                    extension: f.extension,
                    webUrl: record.webUrl,
                    hasChildren: child_count > 0,
                    previewRenderable: coalesce(record.previewRenderable, true)
                }}) AS all_nodes_raw

                // Filter nulls and deduplicate
                WITH [n IN all_nodes_raw WHERE n.id IS NOT NULL] AS all_nodes
                UNWIND all_nodes AS node
                WITH DISTINCT node.id AS id, node
                WITH collect(node) AS unique_nodes

                // Apply filters
                UNWIND unique_nodes AS node
                WITH node WHERE {filter_clause}

                // Apply only_containers filter
                WITH node WHERE $only_containers = false
                     OR node.hasChildren = true
                     OR node.nodeType IN ['app', 'kb', 'recordGroup', 'folder']

                // Sort with explicit field mapping (Neo4j doesn't support dynamic property access)
                WITH node,
                     CASE $sort_field
                         WHEN 'name' THEN node.name
                         WHEN 'createdAt' THEN node.createdAt
                         WHEN 'updatedAt' THEN node.updatedAt
                         WHEN 'nodeType' THEN node.nodeType
                         WHEN 'source' THEN node.source
                         WHEN 'connector' THEN node.connector
                         WHEN 'recordType' THEN node.recordType
                         WHEN 'sizeInBytes' THEN node.sizeInBytes
                         WHEN 'indexingStatus' THEN node.indexingStatus
                         ELSE node.name
                     END AS sort_value
                ORDER BY
                    CASE WHEN $sort_dir = 'ASC' THEN sort_value END ASC,
                    CASE WHEN $sort_dir = 'DESC' THEN sort_value END DESC

                WITH collect(node) AS sorted_nodes

                RETURN {{
                    nodes: sorted_nodes[$skip..($skip + $limit)],
                    total: size(sorted_nodes)
                }} AS result
                """
            else:
                return {"nodes": [], "total": 0}

            results = await self.client.execute_query(query, parameters=params, txn_id=transaction)
            if results and results[0].get("result"):
                return results[0]["result"]
            return {"nodes": [], "total": 0}

        except Exception as e:
            self.logger.error(f"❌ Get knowledge hub recursive search failed: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {"nodes": [], "total": 0}

    async def get_knowledge_hub_search_nodes(
        self,
        user_key: str,
        org_id: str,
        user_app_ids: List[str],
        skip: int,
        limit: int,
        sort_field: str,
        sort_dir: str,
        search_query: Optional[str],
        node_types: Optional[List[str]],
        record_types: Optional[List[str]],
        only_containers: bool,
        origins: Optional[List[str]] = None,
        connector_ids: Optional[List[str]] = None,
        kb_ids: Optional[List[str]] = None,
        indexing_status: Optional[List[str]] = None,
        created_at: Optional[Dict[str, Optional[int]]] = None,
        updated_at: Optional[Dict[str, Optional[int]]] = None,
        size: Optional[Dict[str, Optional[int]]] = None,
        transaction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Search across all nodes (KBs, Apps, RecordGroups, Records) with comprehensive filters.
        Combines results from multiple sources with permission filtering.
        """
        try:
            # Build filter conditions
            filter_conditions = []
            params = {
                "user_key": user_key,
                "org_id": org_id,
                "user_app_ids": user_app_ids,
                "skip": skip,
                "limit": limit,
                "sort_field": sort_field,
                "sort_dir": sort_dir.upper(),
                "only_containers": only_containers,
            }

            # Search query filter - will be combined with other conditions
            if search_query:
                params["search_query"] = search_query.lower()

            # Node type filter - handle include/exclude logic
            include_kbs = True
            include_apps = True
            include_record_groups = True
            include_records = True

            if node_types:
                include_kbs = "kb" in node_types
                include_apps = "app" in node_types
                include_record_groups = "recordGroup" in node_types
                include_records = "folder" in node_types or "record" in node_types or "file" in node_types

                type_conditions = []
                for nt in node_types:
                    if nt == "folder":
                        type_conditions.append("node.nodeType = 'folder'")
                    elif nt == "record":
                        type_conditions.append("node.nodeType = 'record'")
                    elif nt == "recordGroup":
                        type_conditions.append("node.nodeType = 'recordGroup'")
                    elif nt == "app":
                        type_conditions.append("node.nodeType = 'app'")
                    elif nt == "kb":
                        type_conditions.append("node.nodeType = 'kb'")
                if type_conditions:
                    filter_conditions.append(f"({' OR '.join(type_conditions)})")

            if record_types:
                params["record_types"] = record_types
                filter_conditions.append("(node.recordType IS NULL OR node.recordType IN $record_types)")

            if indexing_status:
                params["indexing_status"] = indexing_status
                filter_conditions.append("(node.indexingStatus IS NULL OR node.indexingStatus IN $indexing_status)")

            if created_at:
                if created_at.get("gte"):
                    params["created_at_gte"] = created_at["gte"]
                    filter_conditions.append("node.createdAt >= $created_at_gte")
                if created_at.get("lte"):
                    params["created_at_lte"] = created_at["lte"]
                    filter_conditions.append("node.createdAt <= $created_at_lte")

            if updated_at:
                if updated_at.get("gte"):
                    params["updated_at_gte"] = updated_at["gte"]
                    filter_conditions.append("node.updatedAt >= $updated_at_gte")
                if updated_at.get("lte"):
                    params["updated_at_lte"] = updated_at["lte"]
                    filter_conditions.append("node.updatedAt <= $updated_at_lte")

            if size:
                if size.get("gte"):
                    params["size_gte"] = size["gte"]
                    filter_conditions.append("(node.sizeInBytes IS NULL OR node.sizeInBytes >= $size_gte)")
                if size.get("lte"):
                    params["size_lte"] = size["lte"]
                    filter_conditions.append("(node.sizeInBytes IS NULL OR node.sizeInBytes <= $size_lte)")

            # Origin filter (KB vs CONNECTOR)
            if origins:
                params["origins"] = origins
                filter_conditions.append("node.source IN $origins")
                include_kbs = include_kbs and ("KB" in origins)
                include_apps = include_apps and ("CONNECTOR" in origins)

            # Connector/KB ID filters
            if connector_ids and kb_ids:
                params["connector_ids"] = connector_ids
                params["kb_ids"] = kb_ids
                filter_conditions.append(
                    "((node.nodeType = 'app' AND node.id IN $connector_ids) OR "
                    "(node.nodeType IN ['recordGroup', 'folder', 'record'] AND node.connectorId IN $connector_ids) OR "
                    "(node.nodeType = 'kb' AND node.id IN $kb_ids) OR "
                    "(node.kbId IN $kb_ids))"
                )
            elif connector_ids:
                params["connector_ids"] = connector_ids
                filter_conditions.append(
                    "((node.nodeType = 'app' AND node.id IN $connector_ids) OR "
                    "(node.nodeType IN ['recordGroup', 'folder', 'record'] AND node.connectorId IN $connector_ids))"
                )
            elif kb_ids:
                params["kb_ids"] = kb_ids
                filter_conditions.append(
                    "((node.nodeType = 'kb' AND node.id IN $kb_ids) OR (node.kbId IN $kb_ids))"
                )

            # Add search condition to filter conditions if present
            if search_query:
                filter_conditions.insert(0, "toLower(node.name) CONTAINS $search_query")

            filter_clause = " AND ".join(filter_conditions) if filter_conditions else "true"

            params["include_kbs"] = include_kbs
            params["include_apps"] = include_apps
            params["include_record_groups"] = include_record_groups
            params["include_records"] = include_records

            query = f"""
            MATCH (u:User {{id: $user_key}})
            WITH u

            // ==================== Get Knowledge Bases ====================
            // Note: Boolean filtering done via CASE in results, not in WHERE
            CALL {{
                WITH u
                MATCH (kb:RecordGroup)
                WHERE kb.orgId = $org_id AND kb.connectorName = 'KB' AND NOT coalesce(kb.isDeleted, false)

                // Check user has permission (direct, team, group, or org)
                OPTIONAL MATCH (u)-[direct_perm:PERMISSION {{type: 'USER'}}]->(kb)
                OPTIONAL MATCH (u)-[:PERMISSION {{type: 'USER'}}]->(team:Team)-[:PERMISSION {{type: 'TEAM'}}]->(kb)
                OPTIONAL MATCH (u)-[:PERMISSION {{type: 'USER'}}]->(grp)-[grp_perm:PERMISSION]->(kb)
                WHERE (grp:Group OR grp:Role) AND (grp_perm.type = 'GROUP' OR grp_perm.type = 'ROLE')
                OPTIONAL MATCH (u)-[:BELONGS_TO {{entityType: 'ORGANIZATION'}}]->(org)-[:PERMISSION {{type: 'ORG'}}]->(kb)

                WITH kb, (direct_perm IS NOT NULL OR team IS NOT NULL OR grp IS NOT NULL OR org IS NOT NULL) AS has_permission
                WHERE has_permission

                // Check for children - calculate count before building map
                // Include both direct children via BELONGS_TO and via externalParentId IS NULL
                OPTIONAL MATCH (child_record:Record)-[:BELONGS_TO]->(kb)
                WHERE NOT coalesce(child_record.isDeleted, false) AND child_record.externalParentId IS NULL
                OPTIONAL MATCH (child_rg:RecordGroup)-[:BELONGS_TO]->(kb)
                WHERE NOT coalesce(child_rg.isDeleted, false)

                WITH kb, count(DISTINCT child_record) AS child_record_count, count(DISTINCT child_rg) AS child_rg_count

                RETURN {{
                    id: kb.id,
                    name: kb.groupName,
                    nodeType: 'kb',
                    parentId: null,
                    source: 'KB',
                    connector: 'KB',
                    connectorId: null,
                    kbId: kb.id,
                    recordType: null,
                    recordGroupType: 'KB',
                    indexingStatus: null,
                    createdAt: kb.createdAtTimestamp,
                    updatedAt: kb.updatedAtTimestamp,
                    sizeInBytes: null,
                    mimeType: null,
                    extension: null,
                    webUrl: '/kb/' + kb.id,
                    hasChildren: child_record_count > 0 OR child_rg_count > 0
                }} AS node
            }}

            // Filter KB nodes based on include flag, then collect
            WITH [n IN collect(node) WHERE $include_kbs = true OR n IS NULL] AS kb_nodes_filtered
            WITH CASE WHEN $include_kbs THEN kb_nodes_filtered ELSE [] END AS kb_nodes

            // ==================== Get Apps ====================
            CALL {{
                MATCH (app:App)
                WHERE app.id IN $user_app_ids AND app.type <> 'KB'

                OPTIONAL MATCH (rg:RecordGroup)
                WHERE rg.connectorId = app.id AND NOT coalesce(rg.isDeleted, false)

                WITH app, count(rg) AS rg_count

                RETURN {{
                    id: app.id,
                    name: app.name,
                    nodeType: 'app',
                    parentId: null,
                    source: 'CONNECTOR',
                    connector: app.type,
                    connectorId: app.id,
                    kbId: null,
                    recordType: null,
                    recordGroupType: null,
                    indexingStatus: null,
                    createdAt: app.createdAtTimestamp,
                    updatedAt: app.updatedAtTimestamp,
                    sizeInBytes: null,
                    mimeType: null,
                    extension: null,
                    webUrl: '/app/' + app.id,
                    hasChildren: rg_count > 0
                }} AS node
            }}

            // Filter app nodes based on include flag
            WITH kb_nodes, collect(node) AS app_nodes_raw
            WITH kb_nodes + CASE WHEN $include_apps THEN app_nodes_raw ELSE [] END AS kb_and_app_nodes

            // ==================== Get Record Groups ====================
            CALL {{
                MATCH (u:User {{id: $user_key}})
                MATCH (rg:RecordGroup)
                WHERE rg.orgId = $org_id AND NOT coalesce(rg.isDeleted, false)
                      AND (rg.connectorId IN $user_app_ids OR rg.connectorName = 'KB')

                // Check permissions for KB record groups (direct, team, group, org)
                OPTIONAL MATCH (u)-[direct_perm:PERMISSION {{type: 'USER'}}]->(rg)
                OPTIONAL MATCH (u)-[:PERMISSION {{type: 'USER'}}]->(team:Team)-[:PERMISSION {{type: 'TEAM'}}]->(rg)
                OPTIONAL MATCH (u)-[:PERMISSION {{type: 'USER'}}]->(grp)-[grp_perm:PERMISSION]->(rg)
                WHERE (grp:Group OR grp:Role) AND (grp_perm.type = 'GROUP' OR grp_perm.type = 'ROLE')
                OPTIONAL MATCH (u)-[:BELONGS_TO {{entityType: 'ORGANIZATION'}}]->(org)-[:PERMISSION {{type: 'ORG'}}]->(rg)

                WITH rg, u,
                     (rg.connectorName <> 'KB' OR direct_perm IS NOT NULL OR team IS NOT NULL OR grp IS NOT NULL OR org IS NOT NULL) AS has_permission,
                     (rg.connectorName = 'KB') AS is_kb_rg
                WHERE has_permission AND (NOT is_kb_rg OR rg.groupType <> 'KB')  // Exclude root KBs

                // Check for children via BELONGS_TO edge
                OPTIONAL MATCH (child_record:Record)-[:BELONGS_TO]->(rg)
                WHERE NOT coalesce(child_record.isDeleted, false) AND child_record.externalParentId IS NULL

                // Check for children record groups via BELONGS_TO edge
                OPTIONAL MATCH (child_rg_edge:RecordGroup)-[:BELONGS_TO]->(rg)
                WHERE NOT coalesce(child_rg_edge.isDeleted, false)

                // For connector RGs, also check parentExternalGroupId field
                OPTIONAL MATCH (child_rg_field:RecordGroup)
                WHERE NOT is_kb_rg
                      AND NOT coalesce(child_rg_field.isDeleted, false)
                      AND child_rg_field.connectorId = rg.connectorId
                      AND child_rg_field.parentExternalGroupId = rg.externalGroupId

                WITH rg, is_kb_rg,
                     count(DISTINCT child_record) AS child_record_count,
                     count(DISTINCT child_rg_edge) + count(DISTINCT child_rg_field) AS child_rg_count

                RETURN {{
                    id: rg.id,
                    name: rg.groupName,
                    nodeType: 'recordGroup',
                    parentId: rg.parentId,
                    source: CASE WHEN is_kb_rg THEN 'KB' ELSE 'CONNECTOR' END,
                    connector: rg.connectorName,
                    connectorId: CASE WHEN NOT is_kb_rg THEN rg.connectorId ELSE null END,
                    kbId: CASE WHEN is_kb_rg THEN rg.kbId ELSE null END,
                    recordType: null,
                    recordGroupType: rg.groupType,
                    indexingStatus: null,
                    createdAt: rg.createdAtTimestamp,
                    updatedAt: rg.updatedAtTimestamp,
                    sizeInBytes: null,
                    mimeType: null,
                    extension: null,
                    webUrl: rg.webUrl,
                    hasChildren: child_record_count > 0 OR child_rg_count > 0
                }} AS node
            }}

            // Filter record group nodes based on include flag
            WITH kb_and_app_nodes, collect(node) AS rg_nodes_raw
            WITH kb_and_app_nodes + CASE WHEN $include_record_groups THEN rg_nodes_raw ELSE [] END AS all_non_records

            // ==================== Get Records ====================
            CALL {{
                MATCH (u:User {{id: $user_key}})
                MATCH (record:Record)
                WHERE record.orgId = $org_id AND NOT coalesce(record.isDeleted, false)
                      AND (record.connectorId IN $user_app_ids OR record.connectorName = 'KB')

                // Check permission via inheritPermissions for KB records
                // Path 1: Direct permission on record
                OPTIONAL MATCH (u)-[direct_rec_perm:PERMISSION {{type: 'USER'}}]->(record)

                // Path 2-4: Permission via recordGroup (inheritPermissions)
                OPTIONAL MATCH (record)-[:INHERIT_PERMISSIONS]->(rg:RecordGroup)
                OPTIONAL MATCH (u)-[direct_perm:PERMISSION {{type: 'USER'}}]->(rg)
                OPTIONAL MATCH (u)-[:PERMISSION {{type: 'USER'}}]->(team:Team)-[:PERMISSION {{type: 'TEAM'}}]->(rg)
                OPTIONAL MATCH (u)-[:PERMISSION {{type: 'USER'}}]->(grp)-[grp_perm:PERMISSION]->(rg)
                WHERE (grp:Group OR grp:Role) AND (grp_perm.type = 'GROUP' OR grp_perm.type = 'ROLE')
                OPTIONAL MATCH (u)-[:BELONGS_TO {{entityType: 'ORGANIZATION'}}]->(org)-[:PERMISSION {{type: 'ORG'}}]->(rg)

                WITH record, u,
                     (record.connectorName <> 'KB' OR
                      direct_rec_perm IS NOT NULL OR
                      direct_perm IS NOT NULL OR team IS NOT NULL OR grp IS NOT NULL OR org IS NOT NULL) AS has_permission
                WHERE has_permission

                // Get file info and check if folder
                OPTIONAL MATCH (record)-[:IS_OF_TYPE]->(f:File)

                // Check for children - calculate count before using
                OPTIONAL MATCH (record)-[:RECORD_RELATION]->(child:Record)
                WHERE NOT coalesce(child.isDeleted, false)

                // Determine source
                OPTIONAL MATCH (record_connector:RecordGroup {{id: record.connectorId}})

                WITH record, f,
                     CASE WHEN f IS NOT NULL AND f.isFile = false THEN 'folder' ELSE 'record' END AS nodeType,
                     count(DISTINCT child) AS child_count,
                     CASE WHEN record_connector IS NOT NULL AND record_connector.connectorName = 'KB'
                          THEN 'KB' ELSE 'CONNECTOR' END AS source

                RETURN {{
                    id: record.id,
                    name: record.recordName,
                    nodeType: nodeType,
                    parentId: record.externalParentId,
                    source: source,
                    connector: record.connectorName,
                    connectorId: CASE WHEN source = 'CONNECTOR' THEN record.connectorId ELSE null END,
                    kbId: CASE WHEN source = 'KB' THEN record.connectorId ELSE null END,
                    recordType: record.recordType,
                    recordGroupType: null,
                    indexingStatus: record.indexingStatus,
                    createdAt: record.createdAtTimestamp,
                    updatedAt: record.updatedAtTimestamp,
                    sizeInBytes: coalesce(record.sizeInBytes, f.fileSizeInBytes),
                    mimeType: record.mimeType,
                    extension: f.extension,
                    webUrl: record.webUrl,
                    hasChildren: child_count > 0,
                    previewRenderable: coalesce(record.previewRenderable, true)
                }} AS node
            }}

            // Filter record nodes based on include flag
            WITH all_non_records, collect(node) AS record_nodes_raw
            WITH all_non_records + CASE WHEN $include_records THEN record_nodes_raw ELSE [] END AS all_nodes

            // Deduplicate
            UNWIND all_nodes AS node
            WITH DISTINCT node.id AS id, node
            WITH collect(node) AS unique_nodes

            // Apply all filters (including search)
            UNWIND unique_nodes AS node
            WITH node WHERE {filter_clause}

            // Apply only_containers filter
            WITH node WHERE $only_containers = false
                 OR node.hasChildren = true
                 OR node.nodeType IN ['app', 'kb', 'recordGroup', 'folder']

            // Sort with explicit field mapping (Neo4j doesn't support dynamic property access)
            WITH node,
                 CASE $sort_field
                     WHEN 'name' THEN node.name
                     WHEN 'createdAt' THEN node.createdAt
                     WHEN 'updatedAt' THEN node.updatedAt
                     WHEN 'nodeType' THEN node.nodeType
                     WHEN 'source' THEN node.source
                     WHEN 'connector' THEN node.connector
                     WHEN 'recordType' THEN node.recordType
                     WHEN 'sizeInBytes' THEN node.sizeInBytes
                     WHEN 'indexingStatus' THEN node.indexingStatus
                     ELSE node.name
                 END AS sort_value
            ORDER BY
                CASE WHEN $sort_dir = 'ASC' THEN sort_value END ASC,
                CASE WHEN $sort_dir = 'DESC' THEN sort_value END DESC

            WITH collect(node) AS sorted_nodes

            RETURN {{
                nodes: sorted_nodes[$skip..($skip + $limit)],
                total: size(sorted_nodes)
            }} AS result
            """

            results = await self.client.execute_query(query, parameters=params, txn_id=transaction)
            if results and results[0].get("result"):
                return results[0]["result"]
            return {"nodes": [], "total": 0}

        except Exception as e:
            self.logger.error(f"❌ Get knowledge hub search nodes failed: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {"nodes": [], "total": 0}
