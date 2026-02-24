"""
Neo4j Provider Implementation

Minimal implementation of IGraphDBProvider using Neo4j for testing OneDrive connector compatibility.
Maps ArangoDB concepts (collections, _key, edges) to Neo4j concepts (labels, properties, relationships).
"""

import hashlib
import json
import os
import re
import time
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
    EDGE_COLLECTION_TO_RELATIONSHIP,
    Neo4jLabel,
    build_node_id,
    collection_to_label,
    edge_collection_to_relationship,
    parse_node_id,
)
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    CommentRecord,
    FileRecord,
    IndexingStatus,
    LinkRecord,
    MailRecord,
    ProjectRecord,
    Record,
    RecordGroup,
    TicketRecord,
    User,
    WebpageRecord,
)
from app.schema.node_schema_registry import NODE_SCHEMA_REGISTRY, get_required_fields
from app.schema.node_validator import NodeSchemaValidator
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
        self.validator = NodeSchemaValidator()

    # ==================== Connection Management ====================

    async def connect(self) -> bool:
        """
        Connect to Neo4j and initialize schema.

        Returns:
            bool: True if connection successful
        """
        try:
            self.logger.info("🚀 Connecting to Neo4j...")

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

            self.logger.info("✅ Neo4j provider connected successfully")
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
        """Initialize Neo4j schema (delegates to ensure_schema)."""
        await self.ensure_schema()

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

    def _generate_unique_id_constraints(self) -> List[str]:
        """
        Generate Neo4j unique constraints on 'id' property for all collections.

        Automatically generates unique constraints for all collections in the schema registry,
        ensuring every node type has a unique identifier constraint.

        Returns:
            List of Cypher CREATE CONSTRAINT queries for unique id fields
        """
        constraints = []

        # Iterate through ALL collections (even those without schemas need unique id)
        for collection in NODE_SCHEMA_REGISTRY:
            # Get the Neo4j label for this collection
            label = collection_to_label(collection)

            # Create a safe constraint name
            constraint_name = f"{label.lower()}_id_unique".replace(".", "_")

            # Create unique constraint on id property
            constraint_query = (
                f"CREATE CONSTRAINT {constraint_name} IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
            )

            constraints.append(constraint_query)

        return constraints

    def _generate_performance_indexes(self) -> List[str]:
        """
        Generate strategic performance indexes based on query pattern analysis.

        Only creates composite indexes where fields are ALWAYS queried together.
        Single-field indexes are preferred when fields are queried independently.

        Composite indexes created (3 total):
        - Record: externalRecordId + connectorId (always queried together in MATCH)
        - RecordGroup: externalGroupId + connectorId (always queried together in MATCH)
        - Mail: threadId + conversationIndex (always queried together)

        Returns:
            List of Cypher CREATE INDEX queries
        """
        indexes = []

        # ==================== RECORD INDEXES (Highest Priority) ====================
        # Records are the most queried entity, especially in permission checks

        # COMPOSITE: externalRecordId + connectorId (ALWAYS queried together)
        # Pattern: MATCH (r:Record {externalRecordId: $id, connectorId: $cid})
        # Frequency: 3+ queries use this exact pattern
        indexes.append(
            "CREATE INDEX record_external_id IF NOT EXISTS "
            "FOR (n:Record) ON (n.externalRecordId, n.connectorId)"
        )

        # SINGLE: orgId (queried independently and with other fields)
        indexes.append(
            "CREATE INDEX record_org_id IF NOT EXISTS "
            "FOR (n:Record) ON (n.orgId)"
        )

        # SINGLE: connectorId (queried independently in many patterns)
        indexes.append(
            "CREATE INDEX record_connector_id IF NOT EXISTS "
            "FOR (n:Record) ON (n.connectorId)"
        )

        # SINGLE: indexingStatus (pipeline queries)
        indexes.append(
            "CREATE INDEX record_indexing_status IF NOT EXISTS "
            "FOR (n:Record) ON (n.indexingStatus)"
        )

        # SINGLE: origin (heavily used in permission WHERE clauses)
        indexes.append(
            "CREATE INDEX record_origin IF NOT EXISTS "
            "FOR (n:Record) ON (n.origin)"
        )

        # SINGLE: md5Checksum (duplicate detection)
        indexes.append(
            "CREATE INDEX record_md5_checksum IF NOT EXISTS "
            "FOR (n:Record) ON (n.md5Checksum)"
        )

        # ==================== USER INDEXES (High Priority) ====================

        # SINGLE: email (authentication, lookups)
        indexes.append(
            "CREATE INDEX user_email IF NOT EXISTS "
            "FOR (n:User) ON (n.email)"
        )

        # SINGLE: userId (user identification)
        indexes.append(
            "CREATE INDEX user_user_id IF NOT EXISTS "
            "FOR (n:User) ON (n.userId)"
        )

        # SINGLE: orgId (org-scoped queries)
        indexes.append(
            "CREATE INDEX user_org_id IF NOT EXISTS "
            "FOR (n:User) ON (n.orgId)"
        )

        # ==================== RECORDGROUP INDEXES (High Priority) ====================

        # COMPOSITE: externalGroupId + connectorId (ALWAYS queried together)
        # Pattern: MATCH (rg:RecordGroup {externalGroupId: $id, connectorId: $cid})
        # Frequency: 2+ queries use this exact pattern
        indexes.append(
            "CREATE INDEX record_group_external_id IF NOT EXISTS "
            "FOR (n:RecordGroup) ON (n.externalGroupId, n.connectorId)"
        )

        # SINGLE: orgId (org-scoped queries)
        indexes.append(
            "CREATE INDEX record_group_org_id IF NOT EXISTS "
            "FOR (n:RecordGroup) ON (n.orgId)"
        )

        # SINGLE: connectorId (cleanup operations, queried independently)
        indexes.append(
            "CREATE INDEX record_group_connector_id IF NOT EXISTS "
            "FOR (n:RecordGroup) ON (n.connectorId)"
        )

        # SINGLE: groupType (KB filtering)
        indexes.append(
            "CREATE INDEX record_group_type IF NOT EXISTS "
            "FOR (n:RecordGroup) ON (n.groupType)"
        )

        # ==================== APP INDEXES (Medium Priority) ====================

        # SINGLE: orgId (org-scoped queries)
        indexes.append(
            "CREATE INDEX app_org_id IF NOT EXISTS "
            "FOR (n:App) ON (n.orgId)"
        )

        # ==================== MAIL INDEXES (Medium Priority) ====================

        # COMPOSITE: threadId + conversationIndex (ALWAYS queried together)
        # Pattern: MATCH (m:Mail {threadId: $tid, conversationIndex: $ci})
        indexes.append(
            "CREATE INDEX mail_thread IF NOT EXISTS "
            "FOR (n:Mail) ON (n.threadId, n.conversationIndex)"
        )

        return indexes

    def _generate_required_field_constraints(self) -> List[str]:
        """
        Generate Neo4j property existence constraints for required fields from schemas.

        Returns:
            List of Cypher CREATE CONSTRAINT queries for required fields
        """
        constraints = []

        # Iterate through all collections that have schemas
        for collection, schema in NODE_SCHEMA_REGISTRY.items():
            if schema is None:
                continue

            # Get the Neo4j label for this collection
            label = collection_to_label(collection)

            # Get required fields from the schema
            required_fields = get_required_fields(collection)

            if not required_fields:
                continue

            # Generate a constraint for each required field
            for field in required_fields:
                # Skip 'id' field as it already has a unique constraint
                if field == "id":
                    continue

                # Create a safe constraint name (Neo4j constraint names can't have special chars)
                constraint_name = f"{label.lower()}_{field}_exists".replace(".", "_")

                # Neo4j 4.4+ syntax for property existence constraints
                # Note: Property existence constraints require Neo4j Enterprise Edition
                constraint_query = (
                    f"CREATE CONSTRAINT {constraint_name} IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.{field} IS NOT NULL"
                )

                constraints.append(constraint_query)

        return constraints

    async def ensure_schema(self) -> bool:
        """Ensure Neo4j schema (constraints, indexes, and departments seed)."""
        try:
            self.logger.info("🔧 Ensuring Neo4j schema...")

            # Create unique constraints on 'id' property for all collections
            # Auto-generated from schema registry to ensure complete coverage
            self.logger.info("🔧 Creating unique id constraints...")
            unique_constraints = self._generate_unique_id_constraints()

            for constraint_query in unique_constraints:
                try:
                    await self.client.execute_query(constraint_query)
                except Exception as e:
                    self.logger.debug(f"Unique constraint creation (may already exist): {str(e)}")

            self.logger.info(f"✅ Created {len(unique_constraints)} unique id constraints")

            # Create property existence constraints for required fields from schemas
            self.logger.info("🔧 Creating property existence constraints for required fields...")
            property_constraints = self._generate_required_field_constraints()

            for constraint_query in property_constraints:
                try:
                    await self.client.execute_query(constraint_query)
                except Exception as e:
                    # Some Neo4j versions/editions may not support property existence constraints
                    self.logger.debug(f"Property constraint creation (may already exist or not supported): {str(e)}")

            self.logger.info(f"✅ Created {len(property_constraints)} property existence constraints")

            # Create indexes for common queries
            # Indexes are strategically placed based on query pattern analysis
            self.logger.info("🔧 Creating performance indexes...")
            indexes = self._generate_performance_indexes()

            for index_query in indexes:
                try:
                    await self.client.execute_query(index_query)
                except Exception as e:
                    self.logger.debug(f"Index creation (may already exist): {str(e)}")

            self.logger.info(f"✅ Created {len(indexes)} performance indexes")
            self.logger.info("✅ Neo4j schema initialized (constraints and indexes)")

            # Seed departments collection with predefined department types
            try:
                await self._initialize_departments()
            except Exception as e:
                self.logger.error(f"❌ Error initializing departments: {str(e)}")
                import traceback
                self.logger.error(f"Traceback: {traceback.format_exc()}")

            self.logger.info("✅ Neo4j schema ensured")
            return True
        except Exception as e:
            self.logger.error(f"❌ Ensure schema failed: {str(e)}")
            return False

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
                # Validate nodes before writing
                self.validator.validate_node_update(collection, neo4j_node)
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
        collection: str,
        node_updates: Dict,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Update a single node.

        Args:
            key: Document key
            collection: Collection name
            node_updates: Fields to update
            transaction: Optional transaction ID

        Returns:
            bool: True if successful
        """
        try:
            label = collection_to_label(collection)

            # Convert updates to Neo4j format
            updates = self._arango_to_neo4j_node(node_updates, collection)
            # Validate updates before writing
            self.validator.validate_node_update(collection, updates)

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

            # Process edges - support both ArangoDB format (_from, _to) and generic format (from_id, to_id)
            edge_data = []
            for edge in edges:
                # Try ArangoDB format first (_from, _to)
                if "_from" in edge and "_to" in edge:
                    from_collection, from_key = self._parse_arango_id(edge["_from"])
                    to_collection, to_key = self._parse_arango_id(edge["_to"])
                # Fallback to generic format
                elif "from_id" in edge and "to_id" in edge:
                    from_key = edge["from_id"]
                    to_key = edge["to_id"]
                    from_collection = edge.get("from_collection", "")
                    to_collection = edge.get("to_collection", "")
                else:
                    self.logger.warning(f"Skipping invalid edge (missing _from/_to or from_id/to_id): {edge}")
                    continue

                if not from_key or not to_key or not from_collection or not to_collection:
                    self.logger.warning(f"Skipping invalid edge (missing required fields): {edge}")
                    continue

                from_label = collection_to_label(from_collection)
                to_label = collection_to_label(to_collection)

                # Extract edge properties (excluding format-specific fields)
                props = {k: v for k, v in edge.items() if k not in [
                    "_from", "_to", "from_id", "to_id", "from_collection", "to_collection"
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

    async def batch_create_entity_relations(
        self,
        edges: List[Dict],
        transaction: Optional[str] = None
    ) -> bool:
        """
        Batch create entity relation edges - FULLY ASYNC.

        Uses MERGE to avoid duplicates - matches on from node, to node, and edgeType.
        This is specialized for entityRelations collection where multiple edges
        can exist between the same entities with different edgeType values (e.g., ASSIGNED_TO, CREATED_BY, REPORTED_BY).

        Args:
            edges: List of edge documents with _from, _to, and edgeType (ArangoDB format)
            transaction: Optional transaction ID

        Returns:
            bool: True if successful
        """
        try:
            if not edges:
                return True

            self.logger.info("🚀 Batch creating entity relation edges")

            relationship_type = edge_collection_to_relationship(CollectionNames.ENTITY_RELATIONS.value)

            # Process edges - handle ArangoDB format (_from, _to) or generic format
            edge_data = []
            for edge in edges:
                # Check for ArangoDB format first (_from, _to)
                if "_from" in edge and "_to" in edge:
                    from_collection, from_id = self._parse_arango_id(edge["_from"])
                    to_collection, to_id = self._parse_arango_id(edge["_to"])
                # Fallback to generic format
                elif "from_id" in edge and "to_id" in edge:
                    from_id = edge["from_id"]
                    to_id = edge["to_id"]
                    from_collection = edge.get("from_collection", "")
                    to_collection = edge.get("to_collection", "")
                else:
                    self.logger.warning(f"Skipping invalid edge (missing _from/_to or from_id/to_id): {edge}")
                    continue

                if not from_id or not to_id or not from_collection or not to_collection:
                    self.logger.warning(f"Skipping invalid edge (missing required fields): {edge}")
                    continue

                from_label = collection_to_label(from_collection)
                to_label = collection_to_label(to_collection)

                # Extract edgeType (required for entity relations)
                edge_type = edge.get("edgeType")
                if not edge_type:
                    self.logger.warning(f"Skipping invalid edge (missing edgeType): {edge}")
                    continue

                # Extract all edge properties (excluding format-specific fields)
                props = {k: v for k, v in edge.items() if k not in [
                    "_from", "_to", "from_id", "to_id", "from_collection", "to_collection"
                ]}

                edge_data.append({
                    "from_id": from_id,
                    "to_id": to_id,
                    "from_label": from_label,
                    "to_label": to_label,
                    "edge_type": edge_type,
                    "props": props
                })

            if not edge_data:
                return True

            # Group edges by label combination and edgeType for efficient batch processing
            from collections import defaultdict
            grouped_edges = defaultdict(list)
            for edge in edge_data:
                key = (edge["from_label"], edge["to_label"], edge["edge_type"])
                grouped_edges[key].append(edge)

            # Process each group separately
            for (from_label, to_label, edge_type), group_edges in grouped_edges.items():
                query = f"""
                UNWIND $edges AS edge
                MATCH (from:{from_label} {{id: edge.from_id}})
                MATCH (to:{to_label} {{id: edge.to_id}})
                MERGE (from)-[r:{relationship_type} {{edgeType: edge.edge_type}}]->(to)
                SET r = edge.props
                RETURN count(r) AS created
                """

                await self.client.execute_query(
                    query,
                    parameters={"edges": group_edges},
                    txn_id=transaction
                )

            self.logger.info(
                f"✅ Successfully created {len(edge_data)} entity relation edges."
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

            # Build filter conditions - handle NULL values specially in Cypher
            if filters:
                filter_conditions = []
                parameters = {}

                for field, value in filters.items():
                    if value is None:
                        # Use IS NULL for None values in Cypher
                        filter_conditions.append(f"n.{field} IS NULL")
                    else:
                        # Use parameterized query for non-null values
                        filter_conditions.append(f"n.{field} = ${field}")
                        parameters[field] = value

                where_clause = f"WHERE {' AND '.join(filter_conditions)}"
            else:
                where_clause = ""
                parameters = {}

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
                parameters=parameters,
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

    async def get_documents_by_status(
        self,
        collection: str,
        status: str,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get all documents with a specific indexing status.

        Args:
            collection: Collection name
            status: Status to filter by
            transaction: Optional transaction context

        Returns:
            List[Dict]: List of matching documents
        """
        try:
            label = collection_to_label(collection)

            query = f"""
            MATCH (n:{label})
            WHERE n.indexingStatus = $status
            RETURN n
            """

            results = await self.client.execute_query(
                query,
                parameters={"status": status},
                txn_id=transaction
            )

            # Convert results
            documents = []
            for record in results:
                node = record.get("n", {})
                documents.append(node)

            return documents

        except Exception as e:
            self.logger.error(f"❌ Get documents by status failed: {str(e)}")
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

    async def get_records_by_virtual_record_id(
        self,
        virtual_record_id: str,
        accessible_record_ids: Optional[List[str]] = None,
        transaction: Optional[str] = None
    ) -> List[str]:
        """
        Get all record keys that have the given virtualRecordId.
        Optionally filter by a list of record IDs.

        Args:
            virtual_record_id (str): Virtual record ID to look up
            accessible_record_ids (Optional[List[str]]): Optional list of record IDs to filter by
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[str]: List of record keys that match the criteria
        """
        try:
            self.logger.info(
                "🔍 Finding records with virtualRecordId: %s", virtual_record_id
            )

            # Base query
            query = """
            MATCH (r:Record {virtualRecordId: $virtual_record_id})
            """

            # Add optional filter for record IDs
            if accessible_record_ids:
                query += """
            WHERE r.id IN $accessible_record_ids
            """

            query += """
            RETURN r.id AS record_key
            """

            parameters = {"virtual_record_id": virtual_record_id}
            if accessible_record_ids:
                parameters["accessible_record_ids"] = accessible_record_ids

            results = await self.client.execute_query(
                query,
                parameters=parameters,
                txn_id=transaction
            )

            # Extract record keys from results
            record_keys = [result["record_key"] for result in results if result.get("record_key")]

            self.logger.info(
                "✅ Found %d records with virtualRecordId %s",
                len(record_keys),
                virtual_record_id
            )
            return record_keys

        except Exception as e:
            self.logger.error(
                "❌ Error finding records with virtualRecordId %s: %s",
                virtual_record_id,
                str(e)
            )
            return []

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
        """
        Factory method to create properly typed Record instances from Neo4j data.
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
            return Record.from_arango_base_record(record_dict)

        try:
            # Determine which collection this type uses
            collection = RECORD_TYPE_COLLECTION_MAPPING[record_type]

            # Map collections to their corresponding Record classes
            if collection == CollectionNames.FILES.value:
                return FileRecord.from_arango_record(type_doc, record_dict)
            elif collection == CollectionNames.MAILS.value:
                return MailRecord.from_arango_record(type_doc, record_dict)
            elif collection == CollectionNames.WEBPAGES.value:
                return WebpageRecord.from_arango_record(type_doc, record_dict)
            elif collection == CollectionNames.TICKETS.value:
                return TicketRecord.from_arango_record(type_doc, record_dict)
            elif collection == CollectionNames.PROJECTS.value:
                return ProjectRecord.from_arango_record(type_doc, record_dict)
            elif collection == CollectionNames.COMMENTS.value:
                return CommentRecord.from_arango_record(type_doc, record_dict)
            elif collection == CollectionNames.LINKS.value:
                return LinkRecord.from_arango_record(type_doc, record_dict)
            else:
                # Unknown collection - fallback to base Record
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
    ) -> List[Record]:
        """
        Get all records belonging to a record group up to a specified depth.
        Includes:
        - Records directly in the group
        - Records in nested record groups up to depth levels

        Args:
            record_group_id: Record group ID
            connector_id: Connector ID (all records in group are from same connector)
            org_id: Organization ID (for security filtering)
            depth: Depth for traversing children and nested record groups (-1 = unlimited,
                   0 = only direct records, 1 = direct + 1 level nested, etc.)
            limit: Maximum number of records to return (for pagination)
            offset: Number of records to skip (for pagination)
            transaction: Optional transaction ID

        Returns:
            List[Record]: List of properly typed Record instances
        """
        try:
            self.logger.info(
                f"Retrieving records for record group {record_group_id}, "
                f"connector {connector_id}, org {org_id}, depth {depth}, "
                f"user_key: {user_key}, limit: {limit}, offset: {offset}"
            )

            # Validate depth - must be >= -1
            if depth < -1:
                raise ValueError(
                    f"Depth must be >= -1 (where -1 means unlimited). Got: {depth}"
                )

            # Determine max traversal depth (use 100 as practical unlimited)
            max_depth = 100 if depth == -1 else (0 if depth < 0 else depth)

            reindex_tree_depth = MAX_REINDEX_DEPTH

            # Build permission check fragments conditionally
            if user_key:
                rg_permission_cypher = self._get_permission_role_cypher(
                    "recordGroup", "nestedRg", "u"
                )
                record_permission_cypher = self._get_permission_role_cypher(
                    "record", "record", "u"
                )
                # User match clause
                user_match = "MATCH (u:User {id: $user_key})"
                # Permission filter for nested record groups
                rg_permission_block = f"""
                    // Permission check on each nested record group
                    WITH nestedRg, u
                    {rg_permission_cypher}
                    WITH nestedRg, u, permission_role
                    WHERE permission_role IS NOT NULL AND permission_role <> ''
                """
                # Permission filter for records
                record_permission_block = f"""
                    // Permission check on each record
                    WITH record, u, candidateRecords
                    {record_permission_cypher}
                    WITH record, candidateRecords, permission_role
                    WHERE permission_role IS NOT NULL AND permission_role <> ''
                """
                # Carry user variable through
                user_with = ", u"
            else:
                user_match = ""
                rg_permission_block = ""
                record_permission_block = ""
                user_with = ""

            query = f"""
                // 1) Starting group + nested record groups via BELONGS_TO edges
                MATCH (rg:RecordGroup {{id: $record_group_id, orgId: $org_id}})
                {user_match}

                // Find nested record groups using BELONGS_TO (child -> parent)
                OPTIONAL MATCH path = (nestedRg:RecordGroup)-[:BELONGS_TO*0..{max_depth}]->(rg)
                WHERE nestedRg.orgId = $org_id OR nestedRg.orgId IS NULL

                WITH COLLECT(DISTINCT nestedRg) AS nestedGroups{user_with}
                UNWIND nestedGroups AS nestedRg

                {rg_permission_block}

                WITH collect(DISTINCT nestedRg) AS permittedGroups{user_with}
                UNWIND permittedGroups AS recordGroup

                // 2) Get records belonging to each group via BELONGS_TO
                MATCH (record:Record)-[:BELONGS_TO]->(recordGroup)
                WHERE record.connectorId = $connector_id
                AND record.isDeleted <> true
                AND (record.orgId = $org_id OR record.orgId IS NULL)
                AND record.origin = 'CONNECTOR'

                WITH collect(DISTINCT record) AS candidateRecords{user_with}
                UNWIND candidateRecords AS record

                {record_permission_block}

                WITH collect(DISTINCT record) AS candidateRecords
                UNWIND candidateRecords AS record

                // 3) Root = no parent in group linked by PARENT_CHILD/ATTACHMENT
                OPTIONAL MATCH (parent:Record)-[r:RECORD_RELATION]->(record)
                WHERE r.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT']
                AND parent IN candidateRecords

                WITH record, candidateRecords WHERE parent IS NULL
                WITH collect(record) AS roots, candidateRecords
                UNWIND roots AS root

                // 4) Roots + descendants via PARENT_CHILD/ATTACHMENT only (*0.. = include root)
                MATCH treePath = (root)-[:RECORD_RELATION*0..{reindex_tree_depth}]->(record:Record)
                WHERE all(rel IN relationships(treePath) WHERE rel.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT'])
                AND record IN candidateRecords
                AND record.connectorId = $connector_id
                AND record.isDeleted <> true
                AND (record.orgId = $org_id OR record.orgId IS NULL)

                WITH DISTINCT record
                OPTIONAL MATCH (record)-[:IS_OF_TYPE]->(typeDoc)
                WHERE typeDoc.isFile = true OR NOT typeDoc:File
                WITH record, typeDoc
                ORDER BY record.id
                """

            # Add pagination
            if limit is not None:
                query += "\nSKIP $offset LIMIT $limit"
            elif offset > 0:
                self.logger.warning(
                    f"Offset {offset} provided without limit - offset will be ignored. "
                    "Provide a limit value to use pagination."
                )

            query += "\nRETURN record, typeDoc"

            parameters = {
                "record_group_id": record_group_id,
                "connector_id": connector_id,
                "org_id": org_id,
            }

            if user_key:
                parameters["user_key"] = user_key

            if limit is not None:
                parameters["limit"] = limit
                parameters["offset"] = offset

            results = await self.client.execute_query(
                query,
                parameters=parameters,
                txn_id=transaction
            )

            # Convert to typed records
            typed_records = []
            for result in results:
                record_data = self._neo4j_to_arango_node(
                    dict(result["record"]),
                    CollectionNames.RECORDS.value
                )
                type_doc = dict(result["typeDoc"]) if result.get("typeDoc") else None
                if type_doc:
                    type_doc = self._neo4j_to_arango_node(type_doc, "")

                record = self._create_typed_record_from_neo4j(record_data, type_doc)
                typed_records.append(record)

            self.logger.info(
                f"✅ Successfully retrieved {len(typed_records)} typed records "
                f"for record group {record_group_id}, connector {connector_id}"
            )
            return typed_records

        except Exception as e:
            self.logger.error(
                f"❌ Failed to retrieve records by record group {record_group_id}: {str(e)}",
                exc_info=True
            )
            return []

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
    ) -> List[Record]:
        """
        Get all child records of a parent record (folder) up to a specified depth.
        Uses graph traversal on RECORD_RELATIONS relationship. Parent record is always included.

        Args:
            parent_record_id: Record ID of the parent (folder)
            connector_id: Connector ID (all records should be from same connector)
            org_id: Organization ID (for security filtering)
            depth: Depth for traversing children (-1 = unlimited, 0 = only parent,
                   1 = direct children, 2 = children + grandchildren, etc.)
            user_key: Optional user key for permission filtering
            limit: Maximum number of records to return (for pagination)
            offset: Number of records to skip (for pagination)
            transaction: Optional transaction ID

        Returns:
            List[Record]: List of properly typed Record instances
        """
        try:
            self.logger.info(
                f"Retrieving child records for parent {parent_record_id}, "
                f"connector {connector_id}, org {org_id}, depth {depth}, "
                f"user_key: {user_key}, limit: {limit}, offset: {offset}"
            )

            # Validate depth - must be >= -1
            if depth < -1:
                raise ValueError(
                    f"Depth must be >= -1 (where -1 means unlimited). Got: {depth}"
                )

            # Determine max traversal depth (use 100 as practical unlimited)
            max_depth = 100 if depth == -1 else depth

            # Build permission check fragments conditionally
            if user_key:
                record_permission_cypher = self._get_permission_role_cypher(
                    "record", "record", "u"
                )
                user_match = "MATCH (u:User {id: $user_key})"
                permission_block = f"""
                    // Permission check on each record
                    WITH record, u, typeDoc, recordDepth
                    {record_permission_cypher}
                    WITH record, typeDoc, recordDepth, permission_role
                    WHERE permission_role IS NOT NULL AND permission_role <> ''
                """
                user_with = ", u"
            else:
                user_match = ""
                permission_block = ""
                user_with = ""

            # Single unified query using *0..max_depth traversal
            # Depth 0 = parent, Depth 1+ = children at various levels
            query = f"""
                MATCH (startRecord:Record {{id: $parent_record_id}})
                WHERE startRecord IS NOT NULL
                {user_match}

                // Single traversal for parent (depth 0) and all children (depth 1+)
                OPTIONAL MATCH path = (startRecord)-[:RECORD_RELATION*0..{max_depth}]->(record:Record)
                WHERE (length(path) = 0 OR all(rel IN relationships(path) WHERE rel.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT']))
                AND record.connectorId = $connector_id
                AND (record.orgId = $org_id OR record.orgId IS NULL)
                AND record.isDeleted <> true

                OPTIONAL MATCH (record)-[:IS_OF_TYPE]->(typeDoc)
                WHERE typeDoc IS NOT NULL

                WITH record, typeDoc, length(path) AS recordDepth{user_with}

                {permission_block}

                WITH record, typeDoc, recordDepth
                ORDER BY recordDepth, record.id
                """

            # Add pagination
            if limit is not None:
                query += "\nSKIP $offset LIMIT $limit"
            elif offset > 0:
                self.logger.warning(
                    f"Offset {offset} provided without limit - offset will be ignored."
                )

            query += "\nRETURN record, typeDoc"

            parameters = {
                "parent_record_id": parent_record_id,
                "connector_id": connector_id,
                "org_id": org_id,
            }

            if user_key:
                parameters["user_key"] = user_key

            if limit is not None:
                parameters["limit"] = limit
                parameters["offset"] = offset

            results = await self.client.execute_query(
                query,
                parameters=parameters,
                txn_id=transaction
            )

            # Convert to typed records
            typed_records = []
            for result in results:
                record_data = self._neo4j_to_arango_node(
                    dict(result["record"]),
                    CollectionNames.RECORDS.value
                )
                type_doc = dict(result["typeDoc"]) if result.get("typeDoc") else None
                if type_doc:
                    type_doc = self._neo4j_to_arango_node(type_doc, "")
                record = self._create_typed_record_from_neo4j(record_data, type_doc)
                typed_records.append(record)

            self.logger.info(
                f"✅ Successfully retrieved {len(typed_records)} typed records "
                f"for parent record {parent_record_id} with depth {depth}"
            )
            return typed_records

        except Exception as e:
            self.logger.error(
                f"❌ Failed to retrieve records by parent record {parent_record_id}: {str(e)}",
                exc_info=True
            )
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
            RETURN u, r.sourceUserId AS sourceUserId, app.type AS appName
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
        try:
            self.logger.info(
                f"🔍 Finding duplicate records with MD5: {md5_checksum}"
            )

            # Build query with optional filters
            query = """
            MATCH (r:Record)
            WHERE r.md5Checksum = $md5_checksum
            AND r.id <> $record_key
            """

            params = {
                "md5_checksum": md5_checksum,
                "record_key": record_key,
            }

            if record_type:
                query += """
                AND r.recordType = $record_type
                """
                params["record_type"] = record_type

            if size_in_bytes is not None:
                query += """
                AND r.sizeInBytes = $size_in_bytes
                """
                params["size_in_bytes"] = size_in_bytes

            query += """
            RETURN r
            """

            results = await self.client.execute_query(
                query,
                parameters=params,
                txn_id=transaction
            )

            duplicate_records = []
            for record in results:
                if record.get("r"):
                    record_dict = dict(record["r"])
                    duplicate_records.append(self._neo4j_to_arango_node(record_dict, CollectionNames.RECORDS.value))

            if duplicate_records:
                self.logger.info(f"✅ Found {len(duplicate_records)} duplicate record(s)")
            else:
                self.logger.info("✅ No duplicate records found")

            return duplicate_records

        except Exception as e:
            self.logger.error(f"❌ Error finding duplicate records: {str(e)}")
            return []

    async def find_next_queued_duplicate(
        self,
        record_id: str,
        transaction: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Find the next QUEUED duplicate record with the same md5 hash.
        Works with all record types by querying the RECORDS collection directly.

        Args:
            record_id (str): The record ID to use as reference for finding duplicates
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Optional[dict]: The next queued record if found, None otherwise
        """
        try:
            self.logger.info(
                f"🔍 Finding next QUEUED duplicate record for record {record_id}"
            )

            # First get the record info for the reference record
            record_query = """
            MATCH (record:Record {id: $record_id})
            RETURN record
            """

            results = await self.client.execute_query(
                record_query,
                parameters={"record_id": record_id},
                txn_id=transaction
            )

            if not results or not results[0].get("record"):
                self.logger.info(f"No record found for {record_id}, skipping queued duplicate search")
                return None

            ref_record = dict(results[0]["record"])
            md5_checksum = ref_record.get("md5Checksum")
            size_in_bytes = ref_record.get("sizeInBytes")

            if not md5_checksum:
                self.logger.warning(f"Record {record_id} missing md5Checksum")
                return None

            # Find the first queued duplicate record
            query = """
            MATCH (record:Record)
            WHERE record.md5Checksum = $md5_checksum
            AND record.id <> $record_id
            AND record.indexingStatus = $queued_status
            """

            params = {
                "md5_checksum": md5_checksum,
                "record_id": record_id,
                "queued_status": "QUEUED"
            }

            if size_in_bytes is not None:
                query += """
                AND record.sizeInBytes = $size_in_bytes
                """
                params["size_in_bytes"] = size_in_bytes

            query += """
            RETURN record
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters=params,
                txn_id=transaction
            )

            if results and results[0].get("record"):
                queued_record = dict(results[0]["record"])
                queued_record_dict = self._neo4j_to_arango_node(queued_record, CollectionNames.RECORDS.value)
                self.logger.info(
                    f"✅ Found QUEUED duplicate record: {queued_record_dict.get('_key')}"
                )
                return queued_record_dict

            self.logger.info("✅ No QUEUED duplicate record found")
            return None

        except Exception as e:
            self.logger.error(
                f"❌ Failed to find next queued duplicate: {str(e)}"
            )
            return None

    async def update_queued_duplicates_status(
        self,
        record_id: str,
        new_indexing_status: str,
        virtual_record_id: Optional[str] = None,
        transaction: Optional[str] = None,
    ) -> int:
        """
        Find all QUEUED duplicate records with the same md5 hash and update their status.
        Works with all record types by querying the RECORDS collection directly.

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

            # First get the record info for the reference record
            record_query = """
            MATCH (record:Record {id: $record_id})
            RETURN record
            """

            results = await self.client.execute_query(
                record_query,
                parameters={"record_id": record_id},
                txn_id=transaction
            )

            if not results or not results[0].get("record"):
                self.logger.info(f"No record found for {record_id}, skipping queued duplicate update")
                return 0

            ref_record = dict(results[0]["record"])
            md5_checksum = ref_record.get("md5Checksum")
            size_in_bytes = ref_record.get("sizeInBytes")

            if not md5_checksum:
                self.logger.warning(f"Record {record_id} missing md5Checksum")
                return 0

            # Find all queued duplicate records directly from RECORDS collection
            query = """
            MATCH (record:Record)
            WHERE record.md5Checksum = $md5_checksum
            AND record.id <> $record_id
            AND record.indexingStatus = $queued_status
            """

            params = {
                "md5_checksum": md5_checksum,
                "record_id": record_id,
                "queued_status": "QUEUED"
            }

            if size_in_bytes is not None:
                query += """
                AND record.sizeInBytes = $size_in_bytes
                """
                params["size_in_bytes"] = size_in_bytes

            query += """
            RETURN record
            """

            results = await self.client.execute_query(
                query,
                parameters=params,
                txn_id=transaction
            )

            queued_records = []
            for record in results:
                if record.get("record"):
                    record_dict = dict(record["record"])
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

    async def get_user_apps(self, user_id: str) -> list:
        """Get all apps associated with a user"""
        try:
            query = """
            MATCH (user:User {id: $user_id})-[:USER_APP_RELATION]->(app:App)
            RETURN app
            """
            results = await self.client.execute_query(
                query,
                parameters={"user_id": user_id}
            )

            apps = []
            for r in results:
                if r.get("app"):
                    app_dict = dict(r["app"])
                    apps.append(self._neo4j_to_arango_node(app_dict, CollectionNames.APPS.value))

            return apps
        except Exception as e:
            self.logger.error(f"Failed to get user apps: {str(e)}")
            raise

    async def _get_user_app_ids(self, user_id: str) -> List[str]:
        """Gets a list of accessible app connector IDs for a user."""
        try:
            user_app_docs = await self.get_user_apps(user_id)
            # Filter out None values and apps without id/_key before accessing
            user_apps = [app.get('id') or app.get('_key') for app in user_app_docs if app and (app.get('id') or app.get('_key'))]
            self.logger.debug(f"User has access to {len(user_apps)} apps: {user_apps}")
            return user_apps
        except Exception as e:
            self.logger.error(f"Failed to get user app ids: {str(e)}")
            raise

    async def get_accessible_records(
        self, user_id: str, org_id: str, filters: dict = None
    ) -> list:
        """
        Get all records accessible to a user based on their permissions and apply filters.

        Args:
            user_id (str): The userId field value in users collection
            org_id (str): The org_id to filter anyone collection
            filters (dict): Optional filters for departments, categories, languages, topics etc.
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
        """
        self.logger.info(
            f"Getting accessible records for user {user_id} in org {org_id} with filters {filters}"
        )

        try:
            user = await self.get_user_by_user_id(user_id)
            if not user:
                self.logger.warning(f"User not found for userId: {user_id}")
                return None

            user_key = user.get('id') or user.get('_key')
            # Get user's accessible app connector ids
            user_apps_ids = await self._get_user_app_ids(user_key)

            # Extract filters
            kb_ids = filters.get("kb") if filters else None
            connector_ids = filters.get("apps") if filters else None

            # Determine filter case
            has_kb_filter = kb_ids is not None and len(kb_ids) > 0
            has_app_filter = connector_ids is not None and len(connector_ids) > 0

            self.logger.info(
                f"🔍 Filter analysis - KB filter: {has_kb_filter} (IDs: {kb_ids}), "
                f"App filter: {has_app_filter} (Connector IDs: {connector_ids})"
            )

            # Build a simplified Cypher query
            # Collect all accessible records in one go
            query = """
            MATCH (userDoc:User {userId: $userId})

            // Collect all accessible records from different paths
            CALL {
                WITH userDoc
                // User -> Direct Records
                OPTIONAL MATCH (userDoc)-[:PERMISSION]->(r:Record)
                WHERE r.origin = "UPLOAD" OR (r.origin = "CONNECTOR" AND r.connectorId IN $user_apps_ids)
                RETURN collect(DISTINCT r) AS records1
            }

            CALL {
                WITH userDoc
                // User -> Group -> Records (via BELONGS_TO)
                OPTIONAL MATCH (userDoc)-[:BELONGS_TO]->(g:Group)-[:PERMISSION]->(r:Record)
                WHERE r.origin = "UPLOAD" OR (r.origin = "CONNECTOR" AND r.connectorId IN $user_apps_ids)
                RETURN collect(DISTINCT r) AS records2
            }

            CALL {
                WITH userDoc
                // User -> Group -> Records (via PERMISSION)
                OPTIONAL MATCH (userDoc)-[:PERMISSION]->(g:Group)-[:PERMISSION]->(r:Record)
                WHERE r.origin = "UPLOAD" OR (r.origin = "CONNECTOR" AND r.connectorId IN $user_apps_ids)
                RETURN collect(DISTINCT r) AS records3
            }

            CALL {
                WITH userDoc
                // User -> Organization -> Records
                OPTIONAL MATCH (userDoc)-[:BELONGS_TO]->(o:Organization)-[:PERMISSION]->(r:Record)
                WHERE r.origin = "UPLOAD" OR (r.origin = "CONNECTOR" AND r.connectorId IN $user_apps_ids)
                RETURN collect(DISTINCT r) AS records4
            }

            CALL {
                WITH userDoc
                // User -> Organization -> RecordGroup -> Records
                OPTIONAL MATCH (userDoc)-[:BELONGS_TO]->(o:Organization)-[:PERMISSION]->(rg:RecordGroup)
                OPTIONAL MATCH (r:Record)-[:INHERIT_PERMISSIONS*0..2]->(rg)
                WHERE r.origin = "UPLOAD" OR (r.origin = "CONNECTOR" AND r.connectorId IN $user_apps_ids)
                RETURN collect(DISTINCT r) AS records5
            }

            CALL {
                WITH userDoc
                // User -> Group/Role -> RecordGroup -> Records
                OPTIONAL MATCH (userDoc)-[:PERMISSION]->(gr)
                WHERE gr:Group OR gr:Role
                OPTIONAL MATCH (gr)-[:PERMISSION]->(rg:RecordGroup)
                OPTIONAL MATCH (r:Record)-[:INHERIT_PERMISSIONS*0..5]->(rg)
                WHERE r.origin = "UPLOAD" OR (r.origin = "CONNECTOR" AND r.connectorId IN $user_apps_ids)
                RETURN collect(DISTINCT r) AS records6
            }

            CALL {
                WITH userDoc
                // User -> RecordGroup -> Records
                OPTIONAL MATCH (userDoc)-[:PERMISSION]->(rg:RecordGroup)
                OPTIONAL MATCH (r:Record)-[:INHERIT_PERMISSIONS*0..5]->(rg)
                WHERE r.origin = "UPLOAD" OR (r.origin = "CONNECTOR" AND r.connectorId IN $user_apps_ids)
                RETURN collect(DISTINCT r) AS records7
            }

            CALL {
                // Anyone records
                OPTIONAL MATCH (anyone:Anyone {organization: $orgId})
                OPTIONAL MATCH (r:Record)
                WHERE r.id = anyone.file_key
                  AND (r.origin = "UPLOAD" OR (r.origin = "CONNECTOR" AND r.connectorId IN $user_apps_ids))
                RETURN collect(DISTINCT r) AS records8
            }

            WITH userDoc, records1 + records2 + records3 + records4 + records5 + records6 + records7 + records8 AS directAndGroupRecords
            """

            query_parts = [query]

            # Now handle the 4 cases based on filters

            # Case 1: Both KB and App filters applied
            if has_kb_filter and has_app_filter:
                self.logger.info("🔍 Case 1: Both KB and App filters applied")

                # Get KB records with filter
                query_parts.append("""
                CALL {
                    WITH userDoc
                    // Direct user-KB permissions
                    OPTIONAL MATCH (userDoc)-[:PERMISSION]->(kb:RecordGroup)
                    WHERE kb.id IN $kb_ids
                    OPTIONAL MATCH (r:Record)-[:BELONGS_TO]->(kb)
                    RETURN collect(DISTINCT r) AS directKbRecords
                }

                CALL {
                    WITH userDoc
                    // Team-based KB permissions
                    OPTIONAL MATCH (userDoc)-[ute:PERMISSION]->(team:Teams)
                    WHERE ute.type = "USER"
                    OPTIONAL MATCH (team)-[tke:PERMISSION]->(kb:RecordGroup)
                    WHERE tke.type = "TEAM" AND kb.id IN $kb_ids
                    OPTIONAL MATCH (r:Record)-[:BELONGS_TO]->(kb)
                    RETURN collect(DISTINCT r) AS teamKbRecords
                }

                WITH directKbRecords + teamKbRecords AS kbRecords, directAndGroupRecords
                WITH kbRecords, [r IN directAndGroupRecords WHERE r.connectorId IN $connector_ids] AS appFilteredRecords
                WITH kbRecords + appFilteredRecords AS allAccessibleRecords
                """)

            # Case 2: Only KB filter applied
            elif has_kb_filter and not has_app_filter:
                self.logger.info("🔍 Case 2: Only KB filter applied")

                # Get only filtered KB records
                query_parts.append("""
                CALL {
                    WITH userDoc
                    // Direct user-KB permissions with filter
                    OPTIONAL MATCH (userDoc)-[:PERMISSION]->(kb:RecordGroup)
                    WHERE kb.id IN $kb_ids
                    OPTIONAL MATCH (r:Record)-[:BELONGS_TO]->(kb)
                    RETURN collect(DISTINCT r) AS directKbRecords
                }

                CALL {
                    WITH userDoc
                    // Team-based KB permissions with filter
                    OPTIONAL MATCH (userDoc)-[ute:PERMISSION]->(team:Teams)
                    WHERE ute.type = "USER"
                    OPTIONAL MATCH (team)-[tke:PERMISSION]->(kb:RecordGroup)
                    WHERE tke.type = "TEAM" AND kb.id IN $kb_ids
                    OPTIONAL MATCH (r:Record)-[:BELONGS_TO]->(kb)
                    RETURN collect(DISTINCT r) AS teamKbRecords
                }

                WITH directKbRecords + teamKbRecords AS allAccessibleRecords
                """)

            # Case 3: Only App filter applied
            elif not has_kb_filter and has_app_filter:
                self.logger.info("🔍 Case 3: Only App filter applied")

                # Get app-filtered records from direct, group, org, and anyone
                query_parts.append("""
                WITH [r IN directAndGroupRecords WHERE r.connectorId IN $connector_ids] AS allAccessibleRecords
                """)

            # Case 4: No KB or App filters - return all accessible records
            else:
                self.logger.info("🔍 Case 4: No KB or App filters - returning all accessible records")

                # Get all KB records
                query_parts.append("""
                CALL {
                    WITH userDoc
                    // Direct user-KB permissions
                    OPTIONAL MATCH (userDoc)-[:PERMISSION]->(kb:RecordGroup)
                    OPTIONAL MATCH (r:Record)-[:BELONGS_TO]->(kb)
                    RETURN collect(DISTINCT r) AS directKbRecords
                }

                CALL {
                    WITH userDoc
                    // Team-based KB permissions
                    OPTIONAL MATCH (userDoc)-[ute:PERMISSION]->(team:Teams)
                    WHERE ute.type = "USER"
                    OPTIONAL MATCH (team)-[tke:PERMISSION]->(kb:RecordGroup)
                    WHERE tke.type = "TEAM"
                    OPTIONAL MATCH (r:Record)-[:BELONGS_TO]->(kb)
                    RETURN collect(DISTINCT r) AS teamKbRecords
                }

                WITH directKbRecords + teamKbRecords + directAndGroupRecords AS allAccessibleRecords
                """)

            # Add additional filter conditions (departments, categories, etc.)
            filter_conditions = []
            if filters:
                if filters.get("departments"):
                    filter_conditions.append("""
                    EXISTS {
                        MATCH (record)-[:BELONGS_TO_DEPARTMENT]->(dept:Department)
                        WHERE dept.departmentName IN $departmentNames
                    }
                    """)

                if filters.get("categories"):
                    filter_conditions.append("""
                    EXISTS {
                        MATCH (record)-[:BELONGS_TO_CATEGORY]->(cat:Category)
                        WHERE cat.name IN $categoryNames
                    }
                    """)

                if filters.get("subcategories1"):
                    filter_conditions.append("""
                    EXISTS {
                        MATCH (record)-[:BELONGS_TO_CATEGORY]->(subcat:Category)
                        WHERE subcat.name IN $subcat1Names
                    }
                    """)

                if filters.get("subcategories2"):
                    filter_conditions.append("""
                    EXISTS {
                        MATCH (record)-[:BELONGS_TO_CATEGORY]->(subcat:Category)
                        WHERE subcat.name IN $subcat2Names
                    }
                    """)

                if filters.get("subcategories3"):
                    filter_conditions.append("""
                    EXISTS {
                        MATCH (record)-[:BELONGS_TO_CATEGORY]->(subcat:Category)
                        WHERE subcat.name IN $subcat3Names
                    }
                    """)

                if filters.get("languages"):
                    filter_conditions.append("""
                    EXISTS {
                        MATCH (record)-[:BELONGS_TO_LANGUAGE]->(lang:Language)
                        WHERE lang.name IN $languageNames
                    }
                    """)

                if filters.get("topics"):
                    filter_conditions.append("""
                    EXISTS {
                        MATCH (record)-[:BELONGS_TO_TOPIC]->(topic:Topic)
                        WHERE topic.name IN $topicNames
                    }
                    """)

            # Apply additional filters if any
            if filter_conditions:
                query_parts.append("""
                UNWIND allAccessibleRecords AS record
                WITH record
                WHERE record IS NOT NULL
                  AND """ + " AND ".join(filter_conditions) + """
                RETURN DISTINCT record
                """)
            else:
                query_parts.append("""
                UNWIND allAccessibleRecords AS record
                WITH record
                WHERE record IS NOT NULL
                RETURN DISTINCT record
                """)

            # Combine all query parts
            query = "\n".join(query_parts)

            # Prepare parameters
            parameters = {
                "userId": user_id,
                "orgId": org_id,
                "user_apps_ids": user_apps_ids,
            }

            # Add conditional parameters
            if has_kb_filter:
                parameters["kb_ids"] = kb_ids

            if has_app_filter:
                parameters["connector_ids"] = connector_ids

            # Add filter parameters
            if filters:
                if filters.get("departments"):
                    parameters["departmentNames"] = filters["departments"]
                if filters.get("categories"):
                    parameters["categoryNames"] = filters["categories"]
                if filters.get("subcategories1"):
                    parameters["subcat1Names"] = filters["subcategories1"]
                if filters.get("subcategories2"):
                    parameters["subcat2Names"] = filters["subcategories2"]
                if filters.get("subcategories3"):
                    parameters["subcat3Names"] = filters["subcategories3"]
                if filters.get("languages"):
                    parameters["languageNames"] = filters["languages"]
                if filters.get("topics"):
                    parameters["topicNames"] = filters["topics"]

            # Execute query
            self.logger.debug(f"🔍 Executing query with parameters keys: {list(parameters.keys())}")
            results = await self.client.execute_query(query, parameters=parameters)

            # Process results
            record_list = []
            if results:
                for r in results:
                    if r.get("record"):
                        record_dict = dict(r["record"])
                        record_list.append(self._neo4j_to_arango_node(record_dict, CollectionNames.RECORDS.value))

            record_count = len(record_list)
            self.logger.info(f"✅ Query completed - found {record_count} accessible records")

            if has_kb_filter:
                self.logger.info(f"✅ KB filtering applied for {len(kb_ids)} KBs")
            if has_app_filter:
                self.logger.info(f"✅ App filtering applied for {len(connector_ids)} connector IDs")
            if not has_kb_filter and not has_app_filter:
                self.logger.info("✅ No KB/App filters - returned all accessible records")

            return record_list

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

    async def delete_inherit_permissions_relation_record_group(
        self,
        record_id: str,
        record_group_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """Delete INHERIT_PERMISSIONS edge from record to record group."""
        await self.delete_edge(
            from_id=record_id,
            from_collection=CollectionNames.RECORDS.value,
            to_id=record_group_id,
            to_collection=CollectionNames.RECORD_GROUPS.value,
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
        """Main entry point for record deletion. KB records require OWNER, WRITER, or FILEORGANIZER."""
        try:
            # Get record to determine connector type
            record = await self.get_document(record_id, CollectionNames.RECORDS.value, transaction)
            if not record:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"Record not found: {record_id}"
                }

            connector_name = record.get("connectorName", "")
            origin = record.get("origin", "")

            # KB records: require OWNER, WRITER, or FILEORGANIZER (not READER/COMMENTER)
            is_kb_record = (
                origin == OriginTypes.UPLOAD.value
                or connector_name == Connectors.KNOWLEDGE_BASE.value
            )
            if is_kb_record:
                kb_context = await self._get_kb_context_for_record(record_id, transaction)
                if not kb_context:
                    return {
                        "success": False,
                        "code": 404,
                        "reason": f"Knowledge base context not found for record {record_id}",
                    }
                user = await self.get_user_by_user_id(user_id)
                if not user:
                    return {
                        "success": False,
                        "code": 404,
                        "reason": f"User not found: {user_id}",
                    }
                user_key = user.get("id") or user.get("_key")
                user_role = await self.get_user_kb_permission(
                    kb_context.get("kb_id"), user_key, transaction
                )
                if user_role not in ["OWNER", "WRITER", "FILEORGANIZER"]:
                    return {
                        "success": False,
                        "code": 403,
                        "reason": "User lacks permission to delete records",
                    }

            # Get file record for event publishing before deletion
            file_record = None
            try:
                file_record = await self.get_document(record_id, CollectionNames.FILES.value, transaction)
            except Exception as e:
                self.logger.debug(f"File record not found for record {record_id}: {e}")

            # For Neo4j, use generic delete
            await self.delete_records_and_relations(record_id, hard_delete=True, transaction=transaction)

            # Create event payload for router to publish
            event_data = None
            try:
                payload = await self._create_deleted_record_event_payload(record, file_record)
                if payload:
                    connector_name = record.get("connectorName", "")
                    origin = record.get("origin", "")

                    # Set connector and origin info
                    if connector_name:
                        payload["connectorName"] = connector_name
                    if origin:
                        payload["origin"] = origin

                    event_data = {
                        "eventType": "deleteRecord",
                        "topic": "record-events",
                        "payload": payload
                    }
            except Exception as e:
                self.logger.error(f"❌ Failed to create deletion event payload: {str(e)}")
                event_data = None

            return {
                "success": True,
                "record_id": record_id,
                "message": "Record deleted successfully",
                "eventData": event_data
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

    # ==================== Connector Deletion Helper Methods ====================

    async def _collect_connector_entities(self, connector_id: str, transaction: Optional[str] = None) -> Dict:
        """Collect all entity IDs for a connector."""
        if not self.client:
            raise RuntimeError("Neo4j client not connected")

        query = """
        MATCH (r:Record {connectorId: $connector_id})
        WITH collect(r.id) AS record_ids,
             [vid IN collect(r.virtualRecordId) WHERE vid IS NOT NULL] AS virtual_record_ids

        OPTIONAL MATCH (rg:RecordGroup {connectorId: $connector_id})
        WITH record_ids, virtual_record_ids, collect(rg.id) AS record_group_ids

        OPTIONAL MATCH (role:Role {connectorId: $connector_id})
        WITH record_ids, virtual_record_ids, record_group_ids, collect(role.id) AS role_ids

        OPTIONAL MATCH (grp:Group {connectorId: $connector_id})
        WITH record_ids, virtual_record_ids, record_group_ids, role_ids, collect(grp.id) AS group_ids

        RETURN {
          record_keys: record_ids,
          record_ids: [id IN record_ids | 'records/' + id],
          virtual_record_ids: virtual_record_ids,
          record_group_keys: record_group_ids,
          role_keys: role_ids,
          group_keys: group_ids,
          all_node_ids:
            [id IN record_ids | 'records/' + id] +
            [id IN record_group_ids | 'recordGroups/' + id] +
            [id IN role_ids | 'roles/' + id] +
            [id IN group_ids | 'groups/' + id] +
            ['apps/' + $connector_id]
        } AS result
        """

        results = await self.client.execute_query(
            query,
            parameters={"connector_id": connector_id},
            txn_id=transaction
        )

        if not results or len(results) == 0:
            return {
                "record_keys": [],
                "record_ids": [],
                "virtual_record_ids": [],
                "record_group_keys": [],
                "role_keys": [],
                "group_keys": [],
                "all_node_ids": []
            }

        result = results[0]["result"]

        self.logger.info(
            f"📊 Collected entities for connector {connector_id}: "
            f"records={len(result['record_keys'])}, "
            f"recordGroups={len(result['record_group_keys'])}, "
            f"roles={len(result['role_keys'])}, "
            f"groups={len(result['group_keys'])}"
        )

        return result

    async def _get_all_edge_collections(self) -> List[str]:
        """Get all relationship types."""
        edge_collections = list(EDGE_COLLECTION_TO_RELATIONSHIP.keys())
        self.logger.debug(f"📋 Retrieved {len(edge_collections)} edge collection types")
        return edge_collections

    async def _delete_all_edges_for_nodes(
        self,
        transaction: str,
        node_ids: List[str],
        edge_collections: List[str]
    ) -> Tuple[int, List[str]]:
        """Delete all relationships connected to nodes."""
        if not node_ids:
            return (0, [])

        if not self.client:
            raise RuntimeError("Neo4j client not connected")

        query = """
        UNWIND $node_ids AS node_id_str
        WITH split(node_id_str, '/')[1] AS node_id
        MATCH (n {id: node_id})-[r]-()
        DELETE r
        RETURN count(DISTINCT r) AS deleted_count
        """

        try:
            results = await self.client.execute_query(
                query,
                parameters={"node_ids": node_ids},
                txn_id=transaction
            )

            deleted_count = results[0]["deleted_count"] if results else 0

            self.logger.info(f"✅ Deleted {deleted_count} relationships for {len(node_ids)} nodes")

            return (deleted_count, [])

        except Exception as e:
            self.logger.error(f"❌ Failed to delete relationships: {str(e)}")
            return (0, edge_collections)

    async def _collect_isoftype_targets(self, transaction: str, record_ids: List[str]) -> Tuple[List[Dict], bool]:
        """Collect isOfType target nodes before deleting relationships."""
        if not record_ids:
            return ([], True)

        if not self.client:
            raise RuntimeError("Neo4j client not connected")

        query = """
        UNWIND $record_ids AS record_id_str
        WITH split(record_id_str, '/') AS parts
        WHERE size(parts) = 2 AND parts[0] = 'records'
        WITH parts[1] AS record_id

        MATCH (record:Record {id: record_id})-[:IS_OF_TYPE]->(typeNode)
        WITH DISTINCT typeNode, labels(typeNode) AS nodeLabels
        WHERE any(label IN nodeLabels WHERE label IN ['File', 'Mail', 'Webpage', 'Comment', 'Ticket', 'Link', 'Project'])
        RETURN {
          collection: head([label IN nodeLabels WHERE label IN ['File', 'Mail', 'Webpage', 'Comment', 'Ticket', 'Link', 'Project']]),
          key: typeNode.id,
          full_id: typeNode.id
        } AS target
        """

        try:
            results = await self.client.execute_query(
                query,
                parameters={"record_ids": record_ids},
                txn_id=transaction
            )

            targets = [result["target"] for result in results] if results else []

            self.logger.info(f"📋 Collected {len(targets)} isOfType target nodes")

            return (targets, True)

        except Exception as e:
            self.logger.error(f"❌ Failed to collect isOfType targets: {str(e)}")
            return ([], False)

    async def _delete_isoftype_targets_from_collected(
        self,
        transaction: str,
        targets: List[Dict],
        edge_collections: List[str]
    ) -> Tuple[int, List[str]]:
        """Delete isOfType target nodes using pre-collected targets."""
        if not targets:
            return (0, [])

        if not self.client:
            raise RuntimeError("Neo4j client not connected")

        type_node_ids = [target["full_id"] for target in targets]
        _, failed_edge_collections = await self._delete_all_edges_for_nodes(transaction, type_node_ids, edge_collections)
        if failed_edge_collections:
            raise Exception(
                f"CRITICAL: Failed to delete edges from isOfType target nodes in {len(failed_edge_collections)} collections: {failed_edge_collections}. "
                f"Transaction will be rolled back."
            )

        targets_by_collection: Dict[str, List[str]] = {}
        for target in targets:
            coll = target["collection"]
            key = target["key"]
            if coll not in targets_by_collection:
                targets_by_collection[coll] = []
            targets_by_collection[coll].append(key)

        total_deleted = 0
        total_expected = len(targets)
        failed_collections = []

        for collection, keys in targets_by_collection.items():
            expected_count = len(keys)
            deleted, failed_batches = await self._delete_nodes_by_keys(transaction, keys, collection)
            total_deleted += deleted

            if failed_batches > 0:
                failed_collections.append(f"{collection} (failed batches: {failed_batches})")
            elif deleted < expected_count:
                failed_collections.append(f"{collection} (deleted {deleted}/{expected_count})")

        if failed_collections:
            raise Exception(
                f"CRITICAL: Failed to delete isOfType targets from {len(failed_collections)} collections: {failed_collections}. "
                f"Expected {total_expected} but deleted {total_deleted}. Transaction will be rolled back."
            )

        if total_deleted < total_expected:
            raise Exception(
                f"CRITICAL: Partial deletion of isOfType targets. Expected {total_expected} but deleted {total_deleted}. "
                f"Transaction will be rolled back."
            )

        self.logger.info(f"✅ Deleted {total_deleted} isOfType target documents")
        return (total_deleted, [])

    async def _delete_nodes_by_keys(
        self,
        transaction: str,
        keys: List[str],
        collection: str,
        batch_size: int = 1000
    ) -> Tuple[int, int]:
        """Delete documents by their ID values using batching."""
        if not keys:
            return (0, 0)

        if not self.client:
            raise RuntimeError("Neo4j client not connected")

        # Convert collection name to Neo4j label
        label = collection_to_label(collection)

        total_deleted = 0
        failed_batches = 0
        total_batches = (len(keys) + batch_size - 1) // batch_size

        for i in range(0, len(keys), batch_size):
            batch_keys = keys[i:i + batch_size]
            batch_num = (i // batch_size) + 1

            query = f"""
            MATCH (n:{label})
            WHERE n.id IN $keys
            DETACH DELETE n
            RETURN count(n) AS deleted_count
            """

            try:
                results = await self.client.execute_query(
                    query,
                    parameters={"keys": batch_keys},
                    txn_id=transaction
                )

                deleted_in_batch = results[0]["deleted_count"] if results else 0
                total_deleted += deleted_in_batch

                if deleted_in_batch < len(batch_keys):
                    self.logger.warning(
                        f"⚠️ Batch {batch_num}/{total_batches}: Only deleted {deleted_in_batch}/{len(batch_keys)} nodes from {collection}"
                    )

            except Exception as e:
                self.logger.error(f"❌ Failed to delete batch {batch_num}/{total_batches} from {collection}: {str(e)}")
                failed_batches += 1

        if failed_batches == 0:
            self.logger.info(f"✅ Deleted {total_deleted} nodes from {collection} in {total_batches} batch(es)")
        else:
            self.logger.error(f"❌ Failed {failed_batches}/{total_batches} batches for {collection}")

        return (total_deleted, failed_batches)

    async def _delete_nodes_by_connector_id(
        self,
        transaction: str,
        connector_id: str,
        collection: str
    ) -> Tuple[int, bool]:
        """Delete all nodes with matching connectorId."""
        if not self.client:
            raise RuntimeError("Neo4j client not connected")

        # Convert collection name to Neo4j label
        label = collection_to_label(collection)

        query = f"""
        MATCH (n:{label} {{connectorId: $connector_id}})
        WITH count(n) AS expected_count

        MATCH (n:{label} {{connectorId: $connector_id}})
        DETACH DELETE n
        RETURN count(n) AS deleted_count
        """

        try:
            results = await self.client.execute_query(
                query,
                parameters={"connector_id": connector_id},
                txn_id=transaction
            )

            deleted_count = results[0]["deleted_count"] if results else 0

            if deleted_count > 0:
                self.logger.info(f"✅ Deleted {deleted_count} nodes from {collection} by connectorId")

            return (deleted_count, True)

        except Exception as e:
            self.logger.error(f"❌ Failed to delete nodes from {collection} by connectorId: {str(e)}")
            return (0, False)

    async def delete_sync_points_by_connector_id(
        self,
        connector_id: str,
        transaction: Optional[str] = None
    ) -> Tuple[int, bool]:
        """
        Delete all sync points for a given connector.

        Args:
            connector_id: The connector ID to delete sync points for
            transaction: Optional transaction context

        Returns:
            Tuple of (deleted_count, success_flag)
        """
        return await self._delete_nodes_by_connector_id(
            transaction=transaction,
            connector_id=connector_id,
            collection=CollectionNames.SYNC_POINTS.value
        )

    async def delete_connector_instance(
        self,
        connector_id: str,
        org_id: str,
        transaction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Delete a connector instance and all its related data with strict transaction handling.
        Validates all deletion counts and rolls back on any failure.
        """
        created_transaction = False

        try:
            self.logger.info(f"🗑️ Starting connector instance deletion for {connector_id}")

            connector = await self.get_document(
                document_key=connector_id,
                collection=CollectionNames.APPS.value
            )

            if not connector:
                return {
                    "success": False,
                    "error": f"Connector instance {connector_id} not found"
                }

            collected = await self._collect_connector_entities(connector_id, transaction)
            edge_collections = await self._get_all_edge_collections()

            node_collections = [
                CollectionNames.RECORDS.value,
                CollectionNames.RECORD_GROUPS.value,
                CollectionNames.ROLES.value,
                CollectionNames.GROUPS.value,
                CollectionNames.SYNC_POINTS.value,
                CollectionNames.FILES.value,
                CollectionNames.MAILS.value,
                CollectionNames.WEBPAGES.value,
                CollectionNames.COMMENTS.value,
                CollectionNames.TICKETS.value,
                CollectionNames.LINKS.value,
                CollectionNames.PROJECTS.value,
                CollectionNames.APPS.value,
            ]

            if transaction is None:
                transaction = await self.begin_transaction(
                    read=edge_collections + node_collections,
                    write=edge_collections + node_collections
                )
                created_transaction = True

            try:
                isoftype_targets, isoftype_collect_success = await self._collect_isoftype_targets(
                    transaction,
                    collected["record_ids"]
                )
                if not isoftype_collect_success:
                    raise Exception(
                        "CRITICAL: Failed to collect isOfType targets. "
                        "Cannot safely delete type nodes (files, mails, etc.). Transaction will be rolled back."
                    )

                deleted_edges, failed_edge_collections = await self._delete_all_edges_for_nodes(
                    transaction,
                    collected["all_node_ids"],
                    edge_collections
                )
                if failed_edge_collections:
                    raise Exception(
                        f"CRITICAL: Failed to delete edges from {len(failed_edge_collections)} collections: {failed_edge_collections}. "
                        f"Transaction will be rolled back to maintain data consistency."
                    )

                deleted_isoftype, _ = await self._delete_isoftype_targets_from_collected(
                    transaction,
                    isoftype_targets,
                    edge_collections
                )

                deleted_records, failed_record_batches = await self._delete_nodes_by_keys(
                    transaction,
                    collected["record_keys"],
                    CollectionNames.RECORDS.value
                )
                if len(collected["record_keys"]) > 0:
                    if deleted_records == 0:
                        raise Exception(
                            f"CRITICAL: Failed to delete any records. Expected {len(collected['record_keys'])} but deleted 0. "
                            f"Transaction will be rolled back."
                        )
                    elif deleted_records < len(collected["record_keys"]):
                        raise Exception(
                            f"CRITICAL: Partial deletion failure. Only deleted {deleted_records}/{len(collected['record_keys'])} records. "
                            f"Transaction will be rolled back to maintain data consistency."
                        )
                    if failed_record_batches > 0:
                        raise Exception(
                            f"CRITICAL: Failed to delete {failed_record_batches} batch(es) of records. "
                            f"Transaction will be rolled back."
                        )

                deleted_rg, failed_rg_batches = await self._delete_nodes_by_keys(
                    transaction,
                    collected["record_group_keys"],
                    CollectionNames.RECORD_GROUPS.value
                )
                if len(collected["record_group_keys"]) > 0:
                    if deleted_rg < len(collected["record_group_keys"]):
                        raise Exception(
                            f"CRITICAL: Partial deletion failure. Only deleted {deleted_rg}/{len(collected['record_group_keys'])} record groups. "
                            f"Transaction will be rolled back."
                        )
                    if failed_rg_batches > 0:
                        raise Exception(
                            f"CRITICAL: Failed to delete {failed_rg_batches} batch(es) of record groups. "
                            f"Transaction will be rolled back."
                        )

                deleted_roles, failed_roles_batches = await self._delete_nodes_by_keys(
                    transaction,
                    collected["role_keys"],
                    CollectionNames.ROLES.value
                )
                if len(collected["role_keys"]) > 0:
                    if deleted_roles < len(collected["role_keys"]):
                        raise Exception(
                            f"CRITICAL: Partial deletion failure. Only deleted {deleted_roles}/{len(collected['role_keys'])} roles. "
                            f"Transaction will be rolled back."
                        )
                    if failed_roles_batches > 0:
                        raise Exception(
                            f"CRITICAL: Failed to delete {failed_roles_batches} batch(es) of roles. "
                            f"Transaction will be rolled back."
                        )

                deleted_groups, failed_groups_batches = await self._delete_nodes_by_keys(
                    transaction,
                    collected["group_keys"],
                    CollectionNames.GROUPS.value
                )
                if len(collected["group_keys"]) > 0:
                    if deleted_groups < len(collected["group_keys"]):
                        raise Exception(
                            f"CRITICAL: Partial deletion failure. Only deleted {deleted_groups}/{len(collected['group_keys'])} groups. "
                            f"Transaction will be rolled back."
                        )
                    if failed_groups_batches > 0:
                        raise Exception(
                            f"CRITICAL: Failed to delete {failed_groups_batches} batch(es) of groups. "
                            f"Transaction will be rolled back."
                        )

                deleted_sync, sync_success = await self._delete_nodes_by_connector_id(
                    transaction,
                    connector_id,
                    CollectionNames.SYNC_POINTS.value
                )
                if not sync_success:
                    raise Exception(
                        "CRITICAL: Failed to delete sync points. Transaction will be rolled back."
                    )

                deleted_app, failed_app_batches = await self._delete_nodes_by_keys(
                    transaction,
                    [connector_id],
                    CollectionNames.APPS.value
                )
                if deleted_app == 0:
                    raise Exception(
                        f"CRITICAL: Failed to delete the connector app itself. Connector {connector_id} may still exist. "
                        f"Transaction will be rolled back."
                    )
                if failed_app_batches > 0:
                    raise Exception(
                        f"CRITICAL: Failed to delete app in {failed_app_batches} batch(es). "
                        f"Transaction will be rolled back."
                    )

                if created_transaction:
                    await self.commit_transaction(transaction)

                self.logger.info(
                    f"✅ Connector instance {connector_id} deleted successfully. "
                    f"Records: {deleted_records}/{len(collected['record_keys'])}, "
                    f"RecordGroups: {deleted_rg}/{len(collected['record_group_keys'])}, "
                    f"Roles: {deleted_roles}/{len(collected['role_keys'])}, "
                    f"Groups: {deleted_groups}/{len(collected['group_keys'])}, "
                    f"Edges: {deleted_edges}, "
                    f"isOfType targets: {deleted_isoftype}"
                )

                return {
                    "success": True,
                    "deleted_records_count": deleted_records,
                    "deleted_record_groups_count": deleted_rg,
                    "deleted_roles_count": deleted_roles,
                    "deleted_groups_count": deleted_groups,
                    "virtual_record_ids": collected["virtual_record_ids"],
                    "connector_id": connector_id
                }

            except Exception as tx_error:
                if created_transaction:
                    self.logger.error(f"🔄 Rolling back transaction due to error: {str(tx_error)}")
                    await self.rollback_transaction(transaction)
                raise tx_error

        except Exception as e:
            self.logger.error(f"❌ Failed to delete connector instance {connector_id}: {str(e)}", exc_info=True)
            return {
                "success": False,
                "error": f"Failed to delete connector instance: {str(e)}"
            }

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
        Get a record by its internal ID with associated type document (file/mail/etc.).

        Args:
            record_id (str): The internal record ID to look up
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Optional[Record]: Typed Record instance (FileRecord, MailRecord, etc.) or None
        """
        try:
            self.logger.info(f"🚀 Retrieving record for id {record_id}")

            query = """
            MATCH (record:Record {id: $record_id})
            OPTIONAL MATCH (record)-[:IS_OF_TYPE]->(typeDoc)
            RETURN record, typeDoc
            LIMIT 1
            """

            results = await self.client.execute_query(
                query,
                parameters={"record_id": record_id},
                txn_id=transaction
            )

            if results and len(results) > 0:
                result = results[0]
                record_dict = dict(result["record"])
                record_dict = self._neo4j_to_arango_node(record_dict, CollectionNames.RECORDS.value)

                type_doc = dict(result["typeDoc"]) if result.get("typeDoc") else None
                if type_doc:
                    type_doc = self._neo4j_to_arango_node(type_doc, "")

                typed_record = self._create_typed_record_from_neo4j(record_dict, type_doc)
                self.logger.info(f"✅ Successfully retrieved record for id {record_id}")
                return typed_record
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

            # Get user's accessible app connector ids
            user_apps_ids = await self._get_user_app_ids(user_key)

            self.logger.info(f"🚀 User apps ids: {user_apps_ids}")

            # Get record
            record = await self.get_document(record_id, CollectionNames.RECORDS.value, transaction)
            if not record:
                self.logger.warning(f"⚠️ Record not found: {record_id}")
                return None

            # Build comprehensive access query matching BaseArangoService
            # Check all access paths: direct, group, record group, nested record groups, org, KB, anyone
            access_query = """
            MATCH (u:User {id: $user_key})
            MATCH (rec:Record {id: $record_id})
            WHERE rec.origin <> "CONNECTOR" OR rec.connectorId IN $user_apps_ids

            // Direct access
            OPTIONAL MATCH (u)-[directPerm:PERMISSION {type: "USER"}]->(rec)
            WITH u, rec,
                 [x IN COLLECT({type: "DIRECT", source: u, role: directPerm.role}) WHERE x.role IS NOT NULL] AS directAccess

            // Group/Role access: User -> Group or Role -> Record
            OPTIONAL MATCH (u)-[userGroupPerm:PERMISSION {type: "USER"}]->(g)-[groupRecPerm:PERMISSION]->(rec)
            WHERE (g:Group OR g:Role)
            WITH u, rec, directAccess,
                 [x IN COLLECT({type: "GROUP", source: g, role: groupRecPerm.role}) WHERE x.role IS NOT NULL] AS groupAccess

            // Record Group access: User -> Group or Role -> RecordGroup <- Record (INHERIT_PERMISSIONS)
            OPTIONAL MATCH (u)-[userGroupPerm2:PERMISSION {type: "USER"}]->(g2)-[groupRgPerm:PERMISSION]->(rg:RecordGroup)<-[:INHERIT_PERMISSIONS]-(rec2:Record {id: $record_id})
            WHERE (g2:Group OR g2:Role) AND groupRgPerm.type IN ["GROUP", "ROLE"] AND (rec2.origin <> "CONNECTOR" OR rec2.connectorId IN $user_apps_ids)
            WITH u, rec, directAccess, groupAccess,
                 [x IN COLLECT({type: "RECORD_GROUP", source: rg, role: groupRgPerm.role}) WHERE x.source IS NOT NULL AND x.role IS NOT NULL] AS recordGroupAccess

            // Nested Record Group access: User -> Group or Role -> RecordGroup -> (nested RGs 2-5 levels) -> Record
            OPTIONAL MATCH (u)-[userGroupPerm3:PERMISSION {type: "USER"}]->(g3)-[groupParentRgPerm:PERMISSION]->(parentRg:RecordGroup)<-[:INHERIT_PERMISSIONS*2..5]-(rec3:Record {id: $record_id})
            WHERE (g3:Group OR g3:Role) AND groupParentRgPerm.type IN ["GROUP", "ROLE"] AND (rec3.origin <> "CONNECTOR" OR rec3.connectorId IN $user_apps_ids)
            WITH u, rec, directAccess, groupAccess, recordGroupAccess,
                 [x IN COLLECT(DISTINCT {type: "NESTED_RECORD_GROUP", source: parentRg, role: groupParentRgPerm.role}) WHERE x.source IS NOT NULL AND x.role IS NOT NULL] AS nestedRgAccess

            // Direct User to Record Group access (with nested support)
            // Combine into single pattern to ensure path exists
            OPTIONAL MATCH path = (u)-[userRgPerm:PERMISSION {type: "USER"}]->(rg2:RecordGroup)<-[:INHERIT_PERMISSIONS*1..5]-(rec4:Record {id: $record_id})
            WHERE (rec4.origin <> "CONNECTOR" OR rec4.connectorId IN $user_apps_ids)
            WITH u, rec, directAccess, groupAccess, recordGroupAccess, nestedRgAccess,
                 [x IN COLLECT(DISTINCT {type: "DIRECT_USER_RECORD_GROUP", source: rg2, role: userRgPerm.role}) WHERE x.source IS NOT NULL AND x.role IS NOT NULL] AS directUserRgAccess

            // Inherited RecordGroup permission: Record -> RecordGroup hierarchy (OUTBOUND), then User -> RecordGroup
            // Traverse UP from record to find RecordGroups in hierarchy, then check if user has direct permission
            OPTIONAL MATCH (rec)-[:INHERIT_PERMISSIONS*0..5]->(inheritedRg:RecordGroup)
            OPTIONAL MATCH (u)-[inheritedRgPerm:PERMISSION {type: "USER"}]->(inheritedRg)
            WITH u, rec, directAccess, groupAccess, recordGroupAccess, nestedRgAccess, directUserRgAccess,
                 [x IN COLLECT({type: "INHERITED_RECORD_GROUP", source: inheritedRg, role: inheritedRgPerm.role}) WHERE x.source IS NOT NULL AND x.role IS NOT NULL] AS inheritedRgAccess

            // Group/Role Inherited RecordGroup permission: Record -> RecordGroup hierarchy (OUTBOUND), then User -> Group or Role -> RecordGroup
            OPTIONAL MATCH (rec)-[:INHERIT_PERMISSIONS*2..5]->(inheritedRg2:RecordGroup)
            OPTIONAL MATCH (u)-[userGroupPerm4:PERMISSION {type: "USER"}]->(g4)-[groupInheritedRgPerm:PERMISSION]->(inheritedRg2)
            WHERE (g4:Group OR g4:Role) AND groupInheritedRgPerm.type IN ["GROUP", "ROLE"]
            WITH u, rec, directAccess, groupAccess, recordGroupAccess, nestedRgAccess, directUserRgAccess, inheritedRgAccess,
                 [x IN COLLECT({type: "GROUP_INHERITED_RECORD_GROUP", source: inheritedRg2, role: groupInheritedRgPerm.role}) WHERE x.source IS NOT NULL AND x.role IS NOT NULL] AS groupInheritedRgAccess

            // Organization access: User -> Organization -> Record
            OPTIONAL MATCH (u)-[:BELONGS_TO]->(org:Organization {id: $org_id})-[orgRecPerm:PERMISSION]->(rec5:Record {id: $record_id})
            WHERE rec5.origin <> "CONNECTOR" OR rec5.connectorId IN $user_apps_ids
            WITH u, rec, directAccess, groupAccess, recordGroupAccess, nestedRgAccess, directUserRgAccess, inheritedRgAccess, groupInheritedRgAccess,
                 [x IN COLLECT({type: "ORGANIZATION", source: org, role: orgRecPerm.role}) WHERE x.source IS NOT NULL AND x.role IS NOT NULL] AS orgAccess

            // Organization Record Group access: User -> Organization -> RecordGroup -> Record
            // Combine into single pattern to ensure path exists
            OPTIONAL MATCH path2 = (u)-[belongsTo:BELONGS_TO {entityType: "ORGANIZATION"}]->(org2:Organization {id: $org_id})-[orgRgPerm:PERMISSION {type: "ORG"}]->(rg3:RecordGroup)<-[:INHERIT_PERMISSIONS*1..2]-(rec6:Record {id: $record_id})
            WHERE (rec6.origin <> "CONNECTOR" OR rec6.connectorId IN $user_apps_ids)
            WITH u, rec, directAccess, groupAccess, recordGroupAccess, nestedRgAccess, directUserRgAccess, inheritedRgAccess, groupInheritedRgAccess, orgAccess,
                 [x IN COLLECT(DISTINCT {type: "ORG_RECORD_GROUP", source: rg3, role: orgRgPerm.role}) WHERE x.source IS NOT NULL AND x.role IS NOT NULL] AS orgRgAccess

            // Knowledge Base access: Only for KB records (connectorName = KB), not connector records
            OPTIONAL MATCH (kb:RecordGroup)<-[:BELONGS_TO]-(rec7:Record {id: $record_id}),
                           (u)-[kbPerm:PERMISSION {type: "USER"}]->(kb)
            WHERE rec7.connectorName = $kb_connector_name
            OPTIONAL MATCH (rec7)<-[:PARENT_CHILD]-(folder:File)
            WHERE folder.isFile = false
            WITH u, rec, directAccess, groupAccess, recordGroupAccess, nestedRgAccess, directUserRgAccess, inheritedRgAccess, groupInheritedRgAccess, orgAccess, orgRgAccess,
                 [x IN COLLECT({type: "KNOWLEDGE_BASE", source: kb, role: kbPerm.role, folder: folder}) WHERE x.source IS NOT NULL AND x.role IS NOT NULL] AS kbDirectAccess

            // KB Team access: Only for KB records, not connector records
            OPTIONAL MATCH (kb2:RecordGroup)<-[:BELONGS_TO]-(rec8:Record {id: $record_id}),
                           (team:Teams)-[teamKbPerm:PERMISSION {type: "TEAM"}]->(kb2),
                           (u)-[userTeamPerm:PERMISSION {type: "USER"}]->(team)
            WHERE rec8.connectorName = $kb_connector_name
            OPTIONAL MATCH (rec8)<-[:PARENT_CHILD]-(folder2:File)
            WHERE folder2.isFile = false
            WITH u, rec, directAccess, groupAccess, recordGroupAccess, nestedRgAccess, directUserRgAccess, inheritedRgAccess, groupInheritedRgAccess, orgAccess, orgRgAccess, kbDirectAccess,
                 [x IN COLLECT({
                     type: "KNOWLEDGE_BASE_TEAM",
                     source: kb2,
                     role: userTeamPerm.role,
                     folder: folder2
                 }) WHERE x.source IS NOT NULL AND x.role IS NOT NULL] AS kbTeamAccess

            // Anyone access
            OPTIONAL MATCH (anyone:Anyone {organization: $org_id, file_key: $record_id})
            WITH u, rec, directAccess, groupAccess, recordGroupAccess, nestedRgAccess, directUserRgAccess, inheritedRgAccess, groupInheritedRgAccess, orgAccess, orgRgAccess, kbDirectAccess, kbTeamAccess,
                 [x IN COLLECT({type: "ANYONE", source: null, role: anyone.role}) WHERE x.role IS NOT NULL] AS anyoneAccess

            // For KB records, collect KB RecordGroup source IDs to deduplicate generic RG access paths
            WITH directAccess, groupAccess, recordGroupAccess, nestedRgAccess, directUserRgAccess, inheritedRgAccess, groupInheritedRgAccess, orgAccess, orgRgAccess, kbDirectAccess, kbTeamAccess, anyoneAccess,
                 [kb IN (kbDirectAccess + kbTeamAccess) WHERE kb.source IS NOT NULL | kb.source.id] AS kbSourceIds

            // Filter out generic RecordGroup entries that redundantly match the same KB RecordGroup
            WITH directAccess, groupAccess,
                 [x IN recordGroupAccess WHERE NOT x.source.id IN kbSourceIds] AS recordGroupAccess,
                 [x IN nestedRgAccess WHERE NOT x.source.id IN kbSourceIds] AS nestedRgAccess,
                 [x IN directUserRgAccess WHERE NOT x.source.id IN kbSourceIds] AS directUserRgAccess,
                 [x IN inheritedRgAccess WHERE NOT x.source.id IN kbSourceIds] AS inheritedRgAccess,
                 [x IN groupInheritedRgAccess WHERE NOT x.source.id IN kbSourceIds] AS groupInheritedRgAccess,
                 orgAccess, orgRgAccess, kbDirectAccess, kbTeamAccess, anyoneAccess

            // Combine all access paths
            WITH directAccess, groupAccess, recordGroupAccess, nestedRgAccess, directUserRgAccess, inheritedRgAccess, groupInheritedRgAccess, orgAccess, orgRgAccess, kbDirectAccess, kbTeamAccess, anyoneAccess,
                 directAccess + groupAccess + recordGroupAccess + nestedRgAccess + directUserRgAccess + inheritedRgAccess + groupInheritedRgAccess + orgAccess + orgRgAccess + kbDirectAccess + kbTeamAccess + anyoneAccess AS allAccess
            WHERE size([a IN allAccess WHERE a.source IS NOT NULL OR a.type = "ANYONE"]) > 0
            RETURN allAccess, directAccess, groupAccess, recordGroupAccess, nestedRgAccess, directUserRgAccess, inheritedRgAccess, groupInheritedRgAccess, orgAccess, orgRgAccess, kbDirectAccess, kbTeamAccess, anyoneAccess
            """

            access_results = await self.client.execute_query(
                access_query,
                parameters={
                    "user_key": user_key,
                    "record_id": record_id,
                    "org_id": org_id,
                    "user_apps_ids": user_apps_ids,
                    "kb_connector_name": Connectors.KNOWLEDGE_BASE.value,
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
            # Note: Labels use capitalized collection names (Departments, Categories, etc.)
            metadata_query = """
            MATCH (rec:Record {id: $record_id})

            OPTIONAL MATCH (rec)-[:BELONGS_TO_DEPARTMENT]->(dept:Departments)
            WITH rec, COLLECT(DISTINCT {id: dept.id, name: dept.departmentName}) AS departments

            OPTIONAL MATCH (rec)-[:BELONGS_TO_CATEGORY]->(cat:Categories)
            WITH rec, departments, COLLECT(DISTINCT {id: cat.id, name: cat.name}) AS categories

            OPTIONAL MATCH (rec)-[:BELONGS_TO_CATEGORY]->(subcat1:Subcategories1)
            WITH rec, departments, categories, COLLECT(DISTINCT {id: subcat1.id, name: subcat1.name}) AS subcategories1

            OPTIONAL MATCH (rec)-[:BELONGS_TO_CATEGORY]->(subcat2:Subcategories2)
            WITH rec, departments, categories, subcategories1, COLLECT(DISTINCT {id: subcat2.id, name: subcat2.name}) AS subcategories2

            OPTIONAL MATCH (rec)-[:BELONGS_TO_CATEGORY]->(subcat3:Subcategories3)
            WITH rec, departments, categories, subcategories1, subcategories2, COLLECT(DISTINCT {id: subcat3.id, name: subcat3.name}) AS subcategories3

            OPTIONAL MATCH (rec)-[:BELONGS_TO_TOPIC]->(topic:Topics)
            WITH rec, departments, categories, subcategories1, subcategories2, subcategories3, COLLECT(DISTINCT {id: topic.id, name: topic.name}) AS topics

            OPTIONAL MATCH (rec)-[:BELONGS_TO_LANGUAGE]->(lang:Languages)
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

            # Select the highest permission from all access paths
            role_priority = {
                "OWNER": 6,
                "ORGANIZER": 5,
                "FILEORGANIZER": 4,
                "WRITER": 3,
                "COMMENTER": 2,
                "READER": 1,
            }

            best_access = max(
                access_result,
                key=lambda a: role_priority.get(a.get("role", ""), 0)
            )

            permissions = [{
                "id": record.get("id") or record.get("_key"),
                "name": record.get("recordName"),
                "type": record.get("recordType"),
                "relationship": best_access.get("role"),
                "accessType": best_access.get("type"),
            }]

            record["id"] = record.pop("_key")
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
            AND NOT EXISTS {
                MATCH (r)-[:IS_OF_TYPE]->(f:File)
                WHERE f.isFile = false
            }
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
        Depth comes from caller: 0 = only this record (record-details, collections/KB);
        >0 = include children (e.g. all-records tree uses 100). KB (UPLOAD) records use
        record-events and ignore depth; connector records use sync-events with depth.

        Args:
            record_id: Record ID to reindex
            user_id: External user ID doing the reindex
            org_id: Organization ID
            request: FastAPI request object
            depth: Depth for children (0 = only this record; -1 or >MAX = normalized to MAX_REINDEX_DEPTH)

        Returns:
            Dict: success, recordId, recordName, connector, eventPublished, userRole; or error code/reason
        """
        try:
            self.logger.info(f"🔄 Starting reindex for record {record_id} by user {user_id} with depth {depth}")

            # Normalize depth only when including children (-1 or >MAX -> MAX_REINDEX_DEPTH); depth 0 stays 0
            if depth != 0 and (depth == -1 or depth > MAX_REINDEX_DEPTH):
                depth = MAX_REINDEX_DEPTH

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

            user_key = user.get('_key') or user.get('id')
            if not user_key:
                return {"success": False, "code": 404, "reason": "User key not found"}

            user_role = None

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

                user_role = await self.get_user_kb_permission(kb_context.get("kb_id") or kb_context.get("id"), user_key)
                if not user_role:
                    return {
                        "success": False,
                        "code": 403,
                        "reason": "Insufficient KB permissions. Required: OWNER, WRITER, READER"
                    }

            elif origin == OriginTypes.CONNECTOR.value:
                # Connector record - check connector-specific permissions
                perm_result = await self._check_record_permissions(record_id, user_key)
                user_role = perm_result.get("permission")
                if not user_role:
                    return {
                        "success": False,
                        "code": 403,
                        "reason": "Insufficient permissions. Required: OWNER, WRITER, READER"
                    }

                # Check if connector is enabled before allowing reindex
                if connector_id:
                    connector_doc = await self.get_document(connector_id, CollectionNames.APPS.value)
                    if connector_doc and not connector_doc.get("isActive", False):
                        return {
                            "success": False,
                            "code": 400,
                            "reason": "Connector is disabled. Please enable the connector first."
                        }
            else:
                return {
                    "success": False,
                    "code": 400,
                    "reason": f"Unsupported record origin: {origin}"
                }

            # Reset indexing status to QUEUED before reindexing
            await self._reset_indexing_status_to_queued(record_id)

            # Create event data for router to publish
            try:
                # KB records should always use record-events, not sync-events
                # They don't need connector-based batch reindexing
                if origin == OriginTypes.UPLOAD.value:
                    # Get file record for KB reindex event payload
                    # KB uploads are always FILE records
                    file_record = None
                    if record.get("recordType") == "FILE":
                        file_record = await self.get_document(record_id, CollectionNames.FILES.value)

                    # Create reindex event payload for KB records
                    payload = await self._create_reindex_event_payload(record, file_record, user_id, request, record_id=record_id)
                    event_data = {
                        "eventType": "newRecord",  # Use newRecord for KB reindex (same as single record reindex)
                        "topic": "record-events",
                        "payload": payload
                    }
                else:
                    # For connector records, use sync-events with connector reindex event
                    connector_for_event = connector_name.replace(" ", "").lower() if connector_name else "unknown"
                    event_type = f"{connector_for_event}.reindex"

                    payload = {
                        "orgId": org_id,
                        "recordId": record_id,
                        "depth": depth,
                        "connectorId": connector_id,
                        "connector": connector_for_event,  # Add connector field for consumer fallback
                        "userKey": user_key
                    }

                    event_data = {
                        "eventType": event_type,
                        "topic": "sync-events",
                        "payload": payload
                    }

                return {
                    "success": True,
                    "recordId": record_id,
                    "recordName": record.get("recordName"),
                    "connector": connector_name if origin == OriginTypes.CONNECTOR.value else Connectors.KNOWLEDGE_BASE.value,
                    "userRole": user_role,
                    "eventData": event_data,
                    "useBatchReindex": origin != OriginTypes.UPLOAD.value  # KB records don't use batch reindex
                }

            except Exception as event_error:
                self.logger.error(f"❌ Failed to create reindex event data: {str(event_error)}")
                # Return success but indicate event data creation failed
                return {
                    "success": True,
                    "recordId": record_id,
                    "recordName": record.get("recordName"),
                    "connector": connector_name if origin == OriginTypes.CONNECTOR.value else Connectors.KNOWLEDGE_BASE.value,
                    "userRole": user_role,
                    "eventData": None,
                    "eventError": str(event_error)
                }

        except Exception as e:
            self.logger.error(f"❌ Failed to reindex record {record_id}: {str(e)}")
            return {"success": False, "code": 500, "reason": str(e)}

    async def _check_record_group_permissions(
        self,
        record_group_id: str,
        user_key: str,
        org_id: str
    ) -> Dict:
        """
        Check if user has permission to access a record group

        Returns:
            Dict with 'allowed' (bool), 'role' (str), and 'reason' (str) keys
        """
        try:
            # Query to check if user has permission to the record group
            # Check multiple paths: direct, via groups, via org
            # This matches the ArangoDB implementation logic
            query = """
            MATCH (userDoc:User {id: $user_key})
            MATCH (recordGroup:RecordGroup {id: $record_group_id})
            WHERE recordGroup.orgId = $org_id

            // Direct user -> record group permission (including parent hierarchy 0-10 levels)
            OPTIONAL MATCH (recordGroup)-[:INHERIT_PERMISSIONS*0..10]->(rg:RecordGroup)
            OPTIONAL MATCH (userDoc)-[directPerm:PERMISSION]->(rg)
            WHERE directPerm.type = 'USER'
            WITH userDoc, recordGroup, collect(directPerm.role) AS directPermissions

            // User -> group/role -> record group permission (including parent hierarchy)
            OPTIONAL MATCH (recordGroup)-[:INHERIT_PERMISSIONS*0..10]->(rg2:RecordGroup)
            OPTIONAL MATCH (userDoc)-[userToGroup:PERMISSION]->(grp)
            WHERE userToGroup.type = 'USER' AND (grp:Group OR grp:Role)
            WITH userDoc, recordGroup, directPermissions, collect(grp) AS userGroups, collect(DISTINCT rg2) AS recordGroupHierarchy

            UNWIND CASE WHEN size(userGroups) > 0 THEN userGroups ELSE [null] END AS grp
            UNWIND CASE WHEN size(recordGroupHierarchy) > 0 THEN recordGroupHierarchy ELSE [null] END AS rg2
            OPTIONAL MATCH (grp)-[grpPerm:PERMISSION]->(rg2)
            WHERE grpPerm.type IN ['GROUP', 'ROLE']
            WITH userDoc, recordGroup, directPermissions, collect(grpPerm.role) AS groupPermissions

            // User -> org -> record group permission (including parent hierarchy)
            OPTIONAL MATCH (recordGroup)-[:INHERIT_PERMISSIONS*0..10]->(rg3:RecordGroup)
            OPTIONAL MATCH (userDoc)-[belongsTo:BELONGS_TO]->(org)
            WHERE belongsTo.entityType = 'ORGANIZATION'
            WITH userDoc, recordGroup, directPermissions, groupPermissions, collect(org) AS userOrgs, collect(DISTINCT rg3) AS recordGroupHierarchy2

            UNWIND CASE WHEN size(userOrgs) > 0 THEN userOrgs ELSE [null] END AS org
            UNWIND CASE WHEN size(recordGroupHierarchy2) > 0 THEN recordGroupHierarchy2 ELSE [null] END AS rg3
            OPTIONAL MATCH (org)-[orgPerm:PERMISSION]->(rg3)
            WHERE orgPerm.type = 'ORG'
            WITH directPermissions, groupPermissions, collect(orgPerm.role) AS orgPermissions

            // Combine all permissions and filter out nulls
            WITH directPermissions + groupPermissions + orgPermissions AS allPermissions
            WITH [p IN allPermissions WHERE p IS NOT NULL] AS validPermissions

            WITH size(validPermissions) > 0 AS hasPermission,
                 validPermissions,
                 CASE
                     WHEN 'OWNER' IN validPermissions THEN 'OWNER'
                     WHEN 'WRITER' IN validPermissions THEN 'WRITER'
                     WHEN 'READER' IN validPermissions THEN 'READER'
                     WHEN 'COMMENTER' IN validPermissions THEN 'COMMENTER'
                     ELSE null
                 END AS userRole

            RETURN {
                allowed: hasPermission,
                role: userRole
            } AS result
            """

            results = await self.client.execute_query(
                query,
                parameters={
                    "user_key": user_key,
                    "record_group_id": record_group_id,
                    "org_id": org_id
                }
            )

            if results and results[0].get("result"):
                result = results[0]["result"]
                if result.get("allowed"):
                    return {
                        "allowed": True,
                        "role": result.get("role"),
                        "reason": "User has permission to access record group"
                    }
                else:
                    return {
                        "allowed": False,
                        "role": None,
                        "reason": "User does not have permission to access this record group"
                    }
            else:
                return {
                    "allowed": False,
                    "role": None,
                    "reason": "Permission check failed"
                }

        except Exception as e:
            self.logger.error(f"❌ Failed to check record group permissions: {str(e)}")
            return {
                "allowed": False,
                "role": None,
                "reason": f"Error checking permissions: {str(e)}"
            }

    async def reindex_record_group_records(
        self,
        record_group_id: str,
        depth: int,
        user_id: str,
        org_id: str
    ) -> Dict:
        """
        Get record group data and validate permissions for reindexing.
        Does NOT publish events - that should be done by the caller (router).

        Args:
            record_group_id: Record group ID
            depth: Depth for traversing children (0 = only direct records)
            user_id: External user ID doing the reindex
            org_id: Organization ID

        Returns:
            Dict: Result with success status and connector information
        """
        try:
            self.logger.info(f"🔄 Validating record group reindex for {record_group_id} with depth {depth} by user {user_id}")

            # Handle negative depth: -1 means unlimited (set to MAX_REINDEX_DEPTH), other negatives are invalid (set to 0)
            if depth == -1:
                depth = MAX_REINDEX_DEPTH
                self.logger.info(f"Depth was -1 (unlimited), setting to maximum limit: {depth}")
            elif depth < 0:
                self.logger.warning(f"Invalid negative depth {depth}, setting to 0 (direct records only)")
                depth = 0

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

            # Get user
            user = await self.get_user_by_user_id(user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found: {user_id}"
                }

            user_key = user.get('_key') or user.get('id')
            if not user_key:
                return {"success": False, "code": 404, "reason": "User key not found"}

            # Check if user has permission to access the record group
            permission_check = await self._check_record_group_permissions(
                record_group_id, user_key, org_id
            )

            if not permission_check.get("allowed"):
                return {
                    "success": False,
                    "code": 403,
                    "reason": permission_check.get("reason", "Permission denied")
                }

            # Return success with connector information (caller will publish event)
            return {
                "success": True,
                "connectorId": connector_id,
                "connectorName": connector_name,
                "depth": depth,
                "recordGroupId": record_group_id,
                "userKey": user_key
            }

        except Exception as e:
            self.logger.error("❌ Failed to validate record group reindex: %s", str(e))
            return {"success": False, "code": 500, "reason": str(e)}

    async def _check_record_permissions(
        self,
        record_id: str,
        user_key: str,
        check_drive_inheritance: bool = True,
    ) -> Dict:
        """
        Generic permission checker for any record type.
        Checks: Direct permissions, Group permissions, Domain permissions, Anyone permissions, and optionally Drive-level access

        Args:
            record_id: The record to check permissions for
            user_key: The user to check permissions for
            check_drive_inheritance: Whether to check for Drive-level inherited permissions

        Returns:
            Dict with 'permission' (role) and 'source' (where permission came from)
        """
        try:
            query = """
            MATCH (user:User {id: $user_key})
            MATCH (record:Record {id: $record_id})

            // 1. Check direct user permissions on the record
            OPTIONAL MATCH (user)-[direct_perm:PERMISSION {type: "USER"}]->(record)
            WITH user, record, direct_perm.role AS direct_permission

            // 2. Check group permissions (user -> group -> record)
            OPTIONAL MATCH (user)-[:PERMISSION]->(group)
            WHERE group:Group OR group:Role
            OPTIONAL MATCH (group)-[group_perm:PERMISSION]->(record)
            WITH user, record, direct_permission,
                 head(collect(group_perm.role)) AS group_permission

            // 2.5 Check inherited group->record_group permissions
            OPTIONAL MATCH (user)-[:PERMISSION]->(group2)
            WHERE group2:Group OR group2:Role
            OPTIONAL MATCH (group2)-[g_to_rg:PERMISSION]->(rg:RecordGroup)
            OPTIONAL MATCH (record)-[:INHERIT_PERMISSIONS]->(rg)
            WITH user, record, direct_permission, group_permission,
                 head(collect(g_to_rg.role)) AS record_group_permission

            // 2.6 Check nested record group permissions (0-5 levels)
            OPTIONAL MATCH (user)-[:PERMISSION]->(group3)
            WHERE group3:Group OR group3:Role
            OPTIONAL MATCH (group3)-[nested_perm:PERMISSION]->(rgNested:RecordGroup)
            OPTIONAL MATCH path = (record)-[:INHERIT_PERMISSIONS*0..5]->(rgNested)
            WITH user, record, direct_permission, group_permission, record_group_permission,
                 head(collect(nested_perm.role)) AS nested_record_group_permission

            // 2.7 Check direct user -> record_group permissions (with nesting)
            OPTIONAL MATCH (user)-[user_to_rg:PERMISSION]->(rgDirect:RecordGroup)
            OPTIONAL MATCH path2 = (record)-[:INHERIT_PERMISSIONS*0..5]->(rgDirect)
            WITH user, record, direct_permission, group_permission, record_group_permission,
                 nested_record_group_permission,
                 head(collect(user_to_rg.role)) AS direct_user_record_group_permission

            // 2.8 Check inherited recordGroup permissions (record -> recordGroup hierarchy backwards)
            OPTIONAL MATCH path3 = (record)-[:INHERIT_PERMISSIONS*0..5]->(inheritedRg:RecordGroup)
            OPTIONAL MATCH (user)-[inherited_perm:PERMISSION {type: "USER"}]->(inheritedRg)
            WITH user, record, direct_permission, group_permission, record_group_permission,
                 nested_record_group_permission, direct_user_record_group_permission,
                 head(collect(inherited_perm.role)) AS inherited_record_group_permission

            // 2.9 Check group -> inherited recordGroup permission
            OPTIONAL MATCH path4 = (record)-[:INHERIT_PERMISSIONS*0..5]->(inheritedRg2:RecordGroup)
            OPTIONAL MATCH (user)-[u_to_g:PERMISSION {type: "USER"}]->(groupInherited)
            WHERE groupInherited:Group OR groupInherited:Role
            OPTIONAL MATCH (groupInherited)-[g_to_inherited:PERMISSION]->(inheritedRg2)
            WHERE g_to_inherited.type IN ["GROUP", "ROLE"]
            WITH user, record, direct_permission, group_permission, record_group_permission,
                 nested_record_group_permission, direct_user_record_group_permission,
                 inherited_record_group_permission,
                 head(collect(g_to_inherited.role)) AS group_inherited_record_group_permission

            // 3. Check domain/organization permissions
            OPTIONAL MATCH (user)-[belongs:BELONGS_TO {entityType: "ORGANIZATION"}]->(org:Organization)
            OPTIONAL MATCH (org)-[domain_perm:PERMISSION]->(record)
            WHERE domain_perm.type IN ["DOMAIN", "ORG"]
            WITH user, record, direct_permission, group_permission, record_group_permission,
                 nested_record_group_permission, direct_user_record_group_permission,
                 inherited_record_group_permission, group_inherited_record_group_permission,
                 head(collect(domain_perm.role)) AS domain_permission,
                 head(collect(org.id)) AS user_org_id

            // 4. Check 'anyone' permissions (public sharing)
            OPTIONAL MATCH (anyone:Anyone {file_key: $record_id, organization: user_org_id, active: true})
            WITH user, record, direct_permission, group_permission, record_group_permission,
                 nested_record_group_permission, direct_user_record_group_permission,
                 inherited_record_group_permission, group_inherited_record_group_permission,
                 domain_permission, anyone.role AS anyone_permission

            // 4.5 Check org -> recordGroup -> record permissions (with nesting 0-2 levels)
            OPTIONAL MATCH (user)-[belongs2:BELONGS_TO {entityType: "ORGANIZATION"}]->(org2:Organization)
            OPTIONAL MATCH (org2)-[org_to_rg:PERMISSION]->(rgOrg:RecordGroup)
            OPTIONAL MATCH path5 = (record)-[:INHERIT_PERMISSIONS*0..2]->(rgOrg)
            WITH direct_permission, group_permission, record_group_permission,
                 nested_record_group_permission, direct_user_record_group_permission,
                 inherited_record_group_permission, group_inherited_record_group_permission,
                 domain_permission, anyone_permission, record,
                 head(collect(org_to_rg.role)) AS org_record_group_permission,
                 $check_drive_inheritance AS check_drive_inheritance,
                 $user_key AS user_key

            // 5. Check Drive-level access (if enabled)
            OPTIONAL MATCH (record)-[:IS_OF_TYPE]->(file:File)
            WHERE check_drive_inheritance AND file.driveId IS NOT NULL
            OPTIONAL MATCH (userForDrive:User {id: user_key})-[drive_rel:USER_DRIVE_RELATION]->(drive:Drive)
            WHERE drive.id = file.driveId OR drive.driveId = file.driveId
            WITH direct_permission, group_permission, record_group_permission,
                 nested_record_group_permission, direct_user_record_group_permission,
                 inherited_record_group_permission, group_inherited_record_group_permission,
                 domain_permission, anyone_permission, org_record_group_permission,
                 CASE drive_rel.access_level
                     WHEN "owner" THEN "OWNER"
                     WHEN "writer" THEN "WRITER"
                     WHEN "fileOrganizer" THEN "WRITER"
                     WHEN "commenter" THEN "READER"
                     WHEN "reader" THEN "READER"
                     ELSE null
                 END AS drive_access

            // Return the highest permission level found (in order of precedence)
            WITH CASE
                WHEN direct_permission IS NOT NULL THEN direct_permission
                WHEN inherited_record_group_permission IS NOT NULL THEN inherited_record_group_permission
                WHEN group_inherited_record_group_permission IS NOT NULL THEN group_inherited_record_group_permission
                WHEN group_permission IS NOT NULL THEN group_permission
                WHEN record_group_permission IS NOT NULL THEN record_group_permission
                WHEN direct_user_record_group_permission IS NOT NULL THEN direct_user_record_group_permission
                WHEN nested_record_group_permission IS NOT NULL THEN nested_record_group_permission
                WHEN domain_permission IS NOT NULL THEN domain_permission
                WHEN anyone_permission IS NOT NULL THEN anyone_permission
                WHEN org_record_group_permission IS NOT NULL THEN org_record_group_permission
                WHEN drive_access IS NOT NULL THEN drive_access
                ELSE null
            END AS final_permission,
            CASE
                WHEN direct_permission IS NOT NULL THEN "DIRECT"
                WHEN inherited_record_group_permission IS NOT NULL THEN "INHERITED_RECORD_GROUP"
                WHEN group_inherited_record_group_permission IS NOT NULL THEN "GROUP_INHERITED_RECORD_GROUP"
                WHEN group_permission IS NOT NULL THEN "GROUP"
                WHEN record_group_permission IS NOT NULL THEN "RECORD_GROUP"
                WHEN direct_user_record_group_permission IS NOT NULL THEN "DIRECT_USER_RECORD_GROUP"
                WHEN nested_record_group_permission IS NOT NULL THEN "NESTED_RECORD_GROUP"
                WHEN domain_permission IS NOT NULL THEN "DOMAIN"
                WHEN anyone_permission IS NOT NULL THEN "ANYONE"
                WHEN org_record_group_permission IS NOT NULL THEN "ORG_RECORD_GROUP"
                WHEN drive_access IS NOT NULL THEN "DRIVE_ACCESS"
                ELSE "NONE"
            END AS source

            RETURN final_permission AS permission, source
            """

            parameters = {
                "user_key": user_key,
                "record_id": record_id,
                "check_drive_inheritance": check_drive_inheritance
            }

            results = await self.client.execute_query(query, parameters=parameters)
            result = results[0] if results else None

            if result and result.get("permission"):
                return {"permission": result["permission"], "source": result.get("source", "NONE")}
            return {"permission": None, "source": "NONE"}

        except Exception as e:
            self.logger.error("❌ Failed to check record permissions: %s", str(e))
            return {"permission": None, "source": "ERROR", "error": str(e)}

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

            # If no direct permission, check via teams (Neo4j label is "Teams")
            team_query = """
            MATCH (u:User {id: $user_id})-[r1:PERMISSION {type: "USER"}]->(team:Teams)
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
                name: COALESCE(kb.groupName, 'Untitled'),
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
            OPTIONAL MATCH (u)-[r1:PERMISSION {{type: "USER"}}]->(team:Teams)
            OPTIONAL MATCH (team)-[r2:PERMISSION {{type: "TEAM"}}]->(kb2:RecordGroup)
            WHERE kb2.orgId = $org_id
                AND kb2.groupType = $kb_type
                AND kb2.connectorName = $kb_connector
                {additional_filters}

            // Emit both direct and team KBs so team-only KBs are not lost (COALESCE would drop them)
            WITH kb, kb2, direct_role, direct_priority, is_direct,
                 r1.role AS team_role,
                 CASE WHEN r1.role IS NOT NULL THEN
                     CASE r1.role
                         WHEN "OWNER" THEN 4
                         WHEN "WRITER" THEN 3
                         WHEN "READER" THEN 2
                         WHEN "COMMENTER" THEN 1
                         ELSE 0
                     END
                 ELSE 0 END AS team_priority
            UNWIND (
                CASE WHEN kb IS NOT NULL AND direct_role IS NOT NULL
                    THEN [{{kb_node: kb, role: direct_role, priority: direct_priority, is_direct: true}}]
                    ELSE []
                END +
                CASE WHEN kb2 IS NOT NULL AND team_role IS NOT NULL
                    THEN [{{kb_node: kb2, role: team_role, priority: team_priority, is_direct: false}}]
                    ELSE []
                END
            ) AS item
            WITH item.kb_node AS kb, item.role AS role, item.priority AS priority, item.is_direct AS is_direct

            // Resolve highest role per KB (one row per distinct KB)
            WITH kb,
                 COLLECT(DISTINCT {{role: role, priority: priority, is_direct: is_direct}}) AS all_roles

            WITH kb,
                 [role_info IN all_roles WHERE role_info.role IS NOT NULL] AS valid_roles

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
                name: COALESCE(kb.groupName, 'Untitled'),
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

            // Team-based permissions
            OPTIONAL MATCH (u)-[r1:PERMISSION {{type: "USER"}}]->(team:Teams)
            OPTIONAL MATCH (team)-[r2:PERMISSION {{type: "TEAM"}}]->(kb2:RecordGroup)
            WHERE kb2.orgId = $org_id
                AND kb2.groupType = $kb_type
                AND kb2.connectorName = $kb_connector
                {additional_filters}
            WITH kb, kb2, direct_role, direct_priority, is_direct,
                 r1.role AS team_role,
                 CASE WHEN r1.role IS NOT NULL THEN
                     CASE r1.role
                         WHEN "OWNER" THEN 4
                         WHEN "WRITER" THEN 3
                         WHEN "READER" THEN 2
                         WHEN "COMMENTER" THEN 1
                         ELSE 0
                     END
                 ELSE 0 END AS team_priority
            UNWIND (
                CASE WHEN kb IS NOT NULL AND direct_role IS NOT NULL
                    THEN [{{kb_node: kb, role: direct_role, priority: direct_priority, is_direct: true}}]
                    ELSE []
                END +
                CASE WHEN kb2 IS NOT NULL AND team_role IS NOT NULL
                    THEN [{{kb_node: kb2, role: team_role, priority: team_priority, is_direct: false}}]
                    ELSE []
                END
            ) AS item
            WITH item.kb_node AS kb, item.role AS role, item.priority AS priority, item.is_direct AS is_direct
            WHERE kb IS NOT NULL AND role IS NOT NULL

            // Resolve highest role per KB (same as main query)
            WITH kb,
                 COLLECT(DISTINCT {{role: role, priority: priority, is_direct: is_direct}}) AS all_roles
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

            OPTIONAL MATCH (u)-[r1:PERMISSION {type: "USER"}]->(team:Teams)
            OPTIONAL MATCH (team)-[r2:PERMISSION {type: "TEAM"}]->(kb2:RecordGroup)
            WHERE kb2.orgId = $org_id
                AND kb2.groupType = $kb_type
                AND kb2.connectorName = $kb_connector
            WITH kb, kb2, direct_role, direct_priority, is_direct,
                 r1.role AS team_role,
                 CASE WHEN r1.role IS NOT NULL THEN
                     CASE r1.role
                         WHEN "OWNER" THEN 4
                         WHEN "WRITER" THEN 3
                         WHEN "READER" THEN 2
                         WHEN "COMMENTER" THEN 1
                         ELSE 0
                     END
                 ELSE 0 END AS team_priority
            UNWIND (
                CASE WHEN kb IS NOT NULL AND direct_role IS NOT NULL
                    THEN [{kb_node: kb, role: direct_role, priority: direct_priority, is_direct: true}]
                    ELSE []
                END +
                CASE WHEN kb2 IS NOT NULL AND team_role IS NOT NULL
                    THEN [{kb_node: kb2, role: team_role, priority: team_priority, is_direct: false}]
                    ELSE []
                END
            ) AS item
            WITH item.kb_node AS kb, item.role AS role, item.priority AS priority, item.is_direct AS is_direct
            WHERE kb IS NOT NULL AND role IS NOT NULL

            WITH kb,
                 COLLECT(DISTINCT {role: role, priority: priority, is_direct: is_direct}) AS all_roles
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


    async def get_and_validate_folder_in_kb(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get folder by ID and validate it belongs to the specified KB in a single query.
        This combines validate_folder_in_kb() and get_folder_record_by_id() for better performance.

        Returns:
            Dict with folder data if valid and belongs to KB, None otherwise
        """
        try:
            query = """
            MATCH (folder_record:Record {id: $folder_id})
            MATCH (folder_record)-[:IS_OF_TYPE]->(folder_file:File)
            WHERE folder_file.isFile = false
            MATCH (folder_record)-[:BELONGS_TO {entityType: $entity_type}]->(kb:RecordGroup {id: $kb_id})
            RETURN folder_record {
                .*,
                name: folder_file.name,
                isFile: folder_file.isFile,
                extension: folder_file.extension,
                recordGroupId: folder_record.connectorId
            } AS folder
            """

            results = await self.client.execute_query(
                query,
                parameters={
                    "folder_id": folder_id,
                    "kb_id": kb_id,
                    "entity_type": Connectors.KNOWLEDGE_BASE.value
                },
                txn_id=transaction
            )

            if results and len(results) > 0:
                folder_dict = dict(results[0]["folder"])
                return self._neo4j_to_arango_node(folder_dict, CollectionNames.RECORDS.value)

            self.logger.warning(f"⚠️ Folder {folder_id} validation failed for KB {kb_id}")
            return None

        except Exception as e:
            self.logger.error(f"❌ Failed to get and validate folder in KB: {str(e)}")
            return None

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
                parent_folder = await self.get_and_validate_folder_in_kb(kb_id, parent_folder_id, transaction)
                if not parent_folder:
                    raise ValueError(f"Parent folder {parent_folder_id} not found in KB {kb_id}")

                self.logger.info(f"✅ Validated parent folder: {parent_folder.get('name') or parent_folder.get('recordName')}")

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
            # Determine parent: for immediate children of record group, externalParentId should be null
            # For nested folders (under another folder), use parent folder ID
            # Note: externalParentId is used to distinguish immediate children (null) from nested children (parent folder ID)
            external_parent_id = parent_folder_id if parent_folder_id else None
            kb_connector_id = f"knowledgeBase_{org_id}"

            record_data = {
                "id": folder_id,
                "orgId": org_id,
                "recordName": folder_name,
                "externalRecordId": f"kb_folder_{folder_id}",
                "connectorId": kb_connector_id,  # KB connector ID (knowledgeBase_{org_id})
                "externalGroupId": kb_id,  # Always KB ID (the knowledge base)
                "externalParentId": external_parent_id,  # None for root, parent folder ID for nested
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

            self.logger.debug(
                f"Creating folder RECORDS: root={not parent_folder_id}, "
                f"parent={external_parent_id}, kb={kb_id}"
            )

            # Step 4: Create FILES document for folder (file metadata)
            folder_data = {
                "id": folder_id,
                "orgId": org_id,
                "name": folder_name,
                "isFile": False,
                "extension": None,
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
            # Always create KB relationship (RECORDS -> KB) via BELONGS_TO edge
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

            # Record -> KB inheritPermission edge
            # KB records inherit permissions from KB by default
            inherit_permission_edge = {
                "from_id": folder_id,
                "from_collection": CollectionNames.RECORDS.value,
                "to_id": kb_id,
                "to_collection": CollectionNames.RECORD_GROUPS.value,
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            }
            await self.batch_create_edges([inherit_permission_edge], CollectionNames.INHERIT_PERMISSIONS.value, transaction)

            # Create parent-child relationship ONLY for nested folders (NOT for root folders)
            # Root folders are identified by BELONGS_TO edge + absence of RECORD_RELATIONS edge
            if parent_folder_id:
                # Nested folder: Parent Record -> Child Record via RECORD_RELATIONS
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

            self.logger.info(f"✅ Folder '{folder_name}' created successfully with RECORDS document")
            return {
                "id": folder_id,
                "name": folder_name,
                "webUrl": record_data["webUrl"],
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
        """
        Update folder details in both FILES and RECORDS collections.

        Updates FILES.name and RECORDS.recordName + updatedAtTimestamp
        """
        try:
            self.logger.info(f"🚀 Updating folder {folder_id}")

            # First, get existing File and Record nodes to check if they exist and get current names
            get_nodes_query = """
            MATCH (file:File {id: $folder_id})
            OPTIONAL MATCH (record:Record {id: $folder_id})
            RETURN file.name AS file_name, record.recordName AS record_name
            """
            nodes_check = await self.client.execute_query(
                get_nodes_query,
                parameters={"folder_id": folder_id},
                txn_id=transaction
            )

            if not nodes_check:
                self.logger.warning(f"⚠️ File node not found for folder {folder_id}")
                return False

            # Get existing File name to preserve if not in updates (required by Neo4j constraint)
            file_name = nodes_check[0].get("file_name") if nodes_check else None

            # Step 1: Update FILES collection with updates (matching Arango: UPDATE folder WITH @updates)
            # Build SET clause dynamically based on what's in updates
            set_clauses = []
            params = {"folder_id": folder_id}

            for key, value in updates.items():
                set_clauses.append(f"file.{key} = ${key}")
                params[key] = value

            # If name is not in updates, preserve existing name (required by Neo4j constraint)
            # This ensures the constraint is satisfied even when name is not being updated
            if "name" not in updates and file_name:
                set_clauses.append("file.name = $existing_name")
                params["existing_name"] = file_name

            # Only update if there are changes or if we need to preserve name
            if set_clauses:
                query_file = """
                MATCH (file:File {id: $folder_id})
                SET """ + ", ".join(set_clauses) + """
                RETURN file
                """
                file_result = await self.client.execute_query(
                    query_file,
                    parameters=params,
                    txn_id=transaction
                )

                if not file_result:
                    self.logger.warning(f"⚠️ Failed to update File node for folder {folder_id}")
                    return False

            # Step 2: Update RECORDS collection (matching Arango behavior)
            # Arango does: recordName = updates.get("name") - can be None if name not in updates
            updated_at_timestamp = get_epoch_timestamp_in_ms()

            if "name" in updates:
                # Update both recordName and updatedAtTimestamp (matching Arango: updates.get("name"))
                query_record = """
                MATCH (record:Record {id: $folder_id})
                SET record.recordName = $recordName,
                    record.updatedAtTimestamp = $updatedAtTimestamp
                RETURN record
                """
                results = await self.client.execute_query(
                    query_record,
                    parameters={
                        "folder_id": folder_id,
                        "recordName": updates.get("name"),  # Can be None/empty, matching Arango behavior
                        "updatedAtTimestamp": updated_at_timestamp
                    },
                    txn_id=transaction
                )
            else:
                # Only update updatedAtTimestamp (name not in updates, so don't change recordName)
                query_record = """
                MATCH (record:Record {id: $folder_id})
                SET record.updatedAtTimestamp = $updatedAtTimestamp
                RETURN record
                """
                results = await self.client.execute_query(
                    query_record,
                    parameters={
                        "folder_id": folder_id,
                        "updatedAtTimestamp": updated_at_timestamp
                    },
                    txn_id=transaction
                )

            if results:
                self.logger.info("✅ Folder updated successfully")
                return True

            self.logger.warning("⚠️ Record node not found")
            return False

        except Exception as e:
            self.logger.error(f"❌ Failed to update folder: {str(e)}")
            raise

    async def delete_folder(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[str] = None,
    ) -> Dict:
        """Delete a folder with ALL nested content."""
        try:
            txn_id = transaction
            if transaction is None:
                txn_id = await self.begin_transaction(
                    read=[],
                    write=[
                        CollectionNames.FILES.value,
                        CollectionNames.RECORDS.value,
                        CollectionNames.RECORD_RELATIONS.value,
                        CollectionNames.BELONGS_TO.value,
                        CollectionNames.IS_OF_TYPE.value,
                    ],
                )
            try:
                # Step 1: Collect inventory - target folder + all nested content
                inventory_query = """
                MATCH (target_folder:Record {id: $folder_id})
                MATCH (target_folder)-[:IS_OF_TYPE]->(target_file:File {isFile: false})

                // Get all subfolders via RECORD_RELATION traversal (up to 20 levels deep)
                OPTIONAL MATCH path = (target_folder)-[:RECORD_RELATION*1..20]->(subfolder:Record)
                WHERE all(rel IN relationships(path) WHERE rel.relationshipType = 'PARENT_CHILD')
                OPTIONAL MATCH (subfolder)-[:IS_OF_TYPE]->(subfolder_file:File {isFile: false})
                WHERE subfolder_file IS NOT NULL

                WITH target_folder, target_file,
                     collect(DISTINCT subfolder.id) AS all_subfolders

                WITH target_folder, target_file,
                     [target_folder.id] + all_subfolders AS all_folders

                // Get all file records (non-folders) nested in these folders
                // Only match records that have IS_OF_TYPE -> File {isFile: true}
                OPTIONAL MATCH path2 = (target_folder)-[:RECORD_RELATION*1..20]->(file_record:Record)
                WHERE all(rel IN relationships(path2) WHERE rel.relationshipType = 'PARENT_CHILD')
                OPTIONAL MATCH (file_record)-[:IS_OF_TYPE]->(file_rec_file:File)
                WHERE file_rec_file IS NOT NULL AND file_rec_file.isFile = true

                WITH target_folder, all_folders,
                     collect(DISTINCT CASE
                         WHEN file_record IS NOT NULL AND file_rec_file IS NOT NULL
                         THEN {record: file_record, file_record: file_rec_file}
                         ELSE null
                     END) AS records_with_details_raw,
                     collect(DISTINCT file_rec_file.id) AS file_record_ids

                // Filter out null entries from records_with_details
                WITH target_folder, all_folders, file_record_ids,
                     [item IN records_with_details_raw WHERE item IS NOT NULL] AS records_with_details

                // Get File node IDs for all folders (BEFORE deleting edges)
                UNWIND all_folders AS folder_id
                OPTIONAL MATCH (f:Record {id: folder_id})-[:IS_OF_TYPE]->(folder_file:File {isFile: false})

                WITH target_folder, all_folders, file_record_ids, records_with_details,
                     collect(DISTINCT folder_file.id) AS folder_file_node_ids

                RETURN {
                    folder_exists: target_folder IS NOT NULL,
                    target_folder: target_folder.id,
                    all_folders: all_folders,
                    subfolders: all_folders[1..],
                    records_with_details: records_with_details,
                    file_records: file_record_ids,
                    folder_file_nodes: folder_file_node_ids,
                    total_folders: size(all_folders),
                    total_subfolders: size(all_folders) - 1,
                    total_records: size(records_with_details),
                    total_file_records: size(file_record_ids)
                } AS inventory
                """

                inv_results = await self.client.execute_query(
                    inventory_query,
                    parameters={"folder_id": folder_id},
                    txn_id=txn_id
                )

                inventory = inv_results[0]["inventory"] if inv_results else {}

                self.logger.info(f"inventory: {inventory}")

                if not inventory.get("folder_exists"):
                    if transaction is None and txn_id:
                        await self.rollback_transaction(txn_id)
                    return {"success": False, "eventData": None}

                records_with_details = inventory.get("records_with_details", [])
                all_record_keys = [rd["record"]["id"] for rd in records_with_details if rd.get("record")]
                all_folders = inventory.get("all_folders", [])
                file_records = inventory.get("file_records", [])
                folder_file_nodes = inventory.get("folder_file_nodes", [])

                # Step 2: Delete ALL edges connected to records and folders
                all_ids = all_record_keys + all_folders
                if all_ids:
                    self.logger.info(f"🗑️ Step 2: Deleting all relationships for {len(all_ids)} nodes...")
                    edges_cleanup = """
                    UNWIND $all_ids AS node_id
                    MATCH (node:Record {id: node_id})
                    OPTIONAL MATCH (node)-[r]-()
                    WITH collect(DISTINCT r) AS all_edges
                    UNWIND all_edges AS edge
                    WITH edge WHERE edge IS NOT NULL
                    DELETE edge
                    RETURN count(edge) AS edges_deleted
                    """

                    edge_results = await self.client.execute_query(
                        edges_cleanup,
                        parameters={"all_ids": all_ids},
                        txn_id=txn_id
                    )

                    edges_deleted = edge_results[0]["edges_deleted"] if edge_results else 0
                    self.logger.info(f"✅ Deleted {edges_deleted} relationships")

                # Step 3: Delete ALL File nodes (both files and folders)
                # Combine File node IDs from both files and folders (collected in inventory)
                all_file_node_ids = file_records + folder_file_nodes

                if all_file_node_ids:
                    self.logger.info(f"🗑️ Step 3: Deleting {len(all_file_node_ids)} File nodes ({len(file_records)} files + {len(folder_file_nodes)} folders)...")
                    delete_all_file_nodes = """
                    MATCH (file:File)
                    WHERE file.id IN $file_node_ids
                    DELETE file
                    """
                    await self.client.execute_query(
                        delete_all_file_nodes,
                        parameters={"file_node_ids": all_file_node_ids},
                        txn_id=txn_id
                    )
                    self.logger.info(f"✅ Deleted {len(all_file_node_ids)} File nodes")

                # Step 4: Delete RECORD nodes for files
                if all_record_keys:
                    self.logger.info(f"🗑️ Step 4: Deleting {len(all_record_keys)} file Record nodes...")
                    delete_file_records = """
                    MATCH (record:Record)
                    WHERE record.id IN $record_ids
                    DELETE record
                    """
                    await self.client.execute_query(
                        delete_file_records,
                        parameters={"record_ids": all_record_keys},
                        txn_id=txn_id
                    )
                    self.logger.info(f"✅ Deleted {len(all_record_keys)} file Record nodes")

                # Step 5: Delete RECORD nodes for folders (in reverse order - deepest first)
                if all_folders:
                    self.logger.info(f"🗑️ Step 5: Deleting {len(all_folders)} folder Record nodes...")
                    # Reverse to delete deepest folders first
                    reversed_folders = list(reversed(all_folders))
                    delete_folder_records = """
                    MATCH (folder:Record)
                    WHERE folder.id IN $folder_ids
                    DELETE folder
                    """
                    await self.client.execute_query(
                        delete_folder_records,
                        parameters={"folder_ids": reversed_folders},
                        txn_id=txn_id
                    )
                    self.logger.info(f"✅ Deleted {len(all_folders)} folder Record nodes")

                if transaction is None and txn_id:
                    await self.commit_transaction(txn_id)

                self.logger.info(f"✅ Folder {folder_id} and nested content deleted.")

                # Step: Prepare event data for all deleted file records (router will publish)
                event_payloads = []
                try:
                    for record_data in records_with_details:  # Already contains only file records
                        delete_payload = await self._create_deleted_record_event_payload(
                            record_data["record"], record_data.get("file_record")
                        )
                        if delete_payload:
                            delete_payload["connectorName"] = Connectors.KNOWLEDGE_BASE.value
                            delete_payload["origin"] = OriginTypes.UPLOAD.value
                            event_payloads.append(delete_payload)
                except Exception as e:
                    self.logger.error(f"❌ Failed to prepare deletion event payloads: {str(e)}")

                event_data = {
                    "eventType": "deleteRecord",
                    "topic": "record-events",
                    "payloads": event_payloads
                } if event_payloads else None

                return {
                    "success": True,
                    "eventData": event_data
                }

            except Exception as db_error:
                if transaction is None and txn_id:
                    await self.rollback_transaction(txn_id)
                raise db_error

        except Exception as e:
            self.logger.error(f"❌ Failed to delete folder: {str(e)}")
            return {"success": False, "eventData": None}

    async def find_folder_by_name_in_parent(
        self,
        kb_id: str,
        folder_name: str,
        parent_folder_id: Optional[str] = None,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Find a folder by name within a specific parent (KB root or folder).

        New logic:
        - For KB root: Find folders with BELONGS_TO edge to KB that have NO incoming RECORD_RELATION edges
        - For nested folders: Find folders with RECORD_RELATION edge from parent
        """
        try:
            if parent_folder_id is None:
                # KB root: Find immediate children (no incoming RECORD_RELATION edges)
                query = """
                MATCH (folder:Record)-[:BELONGS_TO]->(kb:RecordGroup {id: $kb_id})
                MATCH (folder)-[:IS_OF_TYPE]->(file:File {isFile: false})
                WHERE toLower(folder.recordName) = toLower($folder_name)
                  AND folder.isDeleted <> true
                  AND NOT EXISTS {
                      MATCH (folder)<-[:RECORD_RELATION {relationshipType: "PARENT_CHILD"}]-(:Record)
                  }
                RETURN folder
                LIMIT 1
                """
                params = {"kb_id": kb_id, "folder_name": folder_name}
            else:
                # Nested folder: Find children via RECORD_RELATION edge
                query = """
                MATCH (parent:Record {id: $parent_folder_id})-[:RECORD_RELATION {relationshipType: "PARENT_CHILD"}]->(folder:Record)
                MATCH (folder)-[:IS_OF_TYPE]->(file:File {isFile: false})
                WHERE toLower(folder.recordName) = toLower($folder_name)
                RETURN folder
                LIMIT 1
                """
                params = {"parent_folder_id": parent_folder_id, "folder_name": folder_name}

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
        parent_folder_id: Optional[str] = None,  # None = KB root, str = specific folder
    ) -> Dict:
        """
        Upload records/files to a knowledge base.
        - KB root upload (parent_folder_id=None)
        - Folder upload (parent_folder_id=folder_id)

        This method follows the same structure as BaseArangoService:
        1. Validate user permissions and target location
        2. Analyze folder structure relative to upload target
        3. Execute upload in single transaction
        """
        try:
            upload_type = "folder" if parent_folder_id else "KB root"
            self.logger.info(f"🚀 Starting unified upload to {upload_type} in KB {kb_id}")
            self.logger.info(f"📊 Processing {len(files)} files")

            # Step 1: Validate user permissions and target location
            validation_result = await self._validate_upload_context(
                kb_id=kb_id,
                user_id=user_id,
                org_id=org_id,
                parent_folder_id=parent_folder_id
            )
            if not validation_result["valid"]:
                return validation_result

            # Step 2: Analyze folder structure relative to upload target
            folder_analysis = self._analyze_upload_structure(files, validation_result)
            self.logger.info(f"📁 Structure analysis: {folder_analysis['summary']}")

            # Step 3: Execute upload in single transaction
            result = await self._execute_upload_transaction(
                kb_id=kb_id,
                user_id=user_id,
                org_id=org_id,
                files=files,
                folder_analysis=folder_analysis,
                validation_result=validation_result
            )

            if result["success"]:
                return {
                    "success": True,
                    "message": self._generate_upload_message(result, upload_type),
                    "totalCreated": result["total_created"],
                    "foldersCreated": result["folders_created"],
                    "createdFolders": result["created_folders"],
                    "failedFiles": result["failed_files"],
                    "kbId": kb_id,
                    "parentFolderId": parent_folder_id,
                    "eventData": result.get("eventData"),
                }
            else:
                return result

        except Exception as e:
            self.logger.error(f"❌ Unified upload failed: {str(e)}")
            return {"success": False, "reason": f"Upload failed: {str(e)}", "code": 500}

    # ==================== Upload Helper Methods ====================

    async def _validate_upload_context(
        self,
        kb_id: str,
        user_id: str,
        org_id: str,
        parent_folder_id: Optional[str] = None
    ) -> Dict:
        """Unified validation for all upload scenarios"""
        try:
            # Get user
            user = await self.get_user_by_user_id(user_id=user_id)
            if not user:
                return {"valid": False, "success": False, "code": 404, "reason": f"User not found: {user_id}"}

            user_key = user.get('id') or user.get('_key')

            # Check KB permissions
            user_role = await self.get_user_kb_permission(kb_id, user_key)
            if user_role not in ["OWNER", "WRITER"]:
                return {
                    "valid": False,
                    "success": False,
                    "code": 403,
                    "reason": f"Insufficient permissions. Role: {user_role}"
                }

            # Validate target location
            if parent_folder_id:
                # Validate folder exists and belongs to KB
                parent_folder = await self.get_and_validate_folder_in_kb(kb_id, parent_folder_id)
                if not parent_folder:
                    return {
                        "valid": False,
                        "success": False,
                        "code": 404,
                        "reason": f"Parent folder {parent_folder_id} not found in KB {kb_id}"
                    }
                return {
                    "valid": True,
                    "upload_target": "folder",
                    "parent_folder": parent_folder,
                    "user": user,
                    "user_key": user_key,
                    "user_role": user_role
                }
            else:
                # KB root upload
                return {
                    "valid": True,
                    "upload_target": "kb_root",
                    "user": user,
                    "user_key": user_key,
                    "user_role": user_role
                }

        except Exception as e:
            self.logger.error(f"❌ Upload validation failed: {str(e)}")
            return {"valid": False, "success": False, "code": 500, "reason": str(e)}

    def _analyze_upload_structure(self, files: List[Dict], validation_result: Dict) -> Dict:
        """
        Analyze folder structure - creates folder hierarchy map based on file paths
        """
        folder_hierarchy = {}  # path -> {name: str, parent_path: str, level: int}
        file_destinations = {}  # file_index -> {type: "root"|"folder", folder_name: str|None, folder_hierarchy_path: str|None}

        for index, file_data in enumerate(files):
            file_path = file_data["filePath"]

            if "/" in file_path:
                # File is in a subfolder - analyze the hierarchy
                path_parts = file_path.split("/")
                folder_parts = path_parts[:-1]

                # Build folder hierarchy
                current_path = ""
                for i, folder_name in enumerate(folder_parts):
                    parent_path = current_path if current_path else None
                    current_path = f"{current_path}/{folder_name}" if current_path else folder_name

                    if current_path not in folder_hierarchy:
                        folder_hierarchy[current_path] = {
                            "name": folder_name,
                            "parent_path": parent_path,
                            "level": i + 1
                        }

                # File goes to the deepest folder
                file_destinations[index] = {
                    "type": "folder",
                    "folder_name": folder_parts[-1],
                    "folder_hierarchy_path": current_path,
                }
            else:
                # File goes to upload target (KB root or parent folder)
                file_destinations[index] = {
                    "type": "root",
                    "folder_name": None,
                    "folder_hierarchy_path": None,
                }

        # Sort folders by level (create parents first)
        sorted_folder_paths = sorted(folder_hierarchy.keys(), key=lambda x: folder_hierarchy[x]["level"])

        # Add parent folder context
        parent_folder_id = None
        if validation_result["upload_target"] == "folder":
            parent_folder_id = validation_result["parent_folder"].get("id") or validation_result["parent_folder"].get("_key")

        return {
            "folder_hierarchy": folder_hierarchy,
            "sorted_folder_paths": sorted_folder_paths,
            "file_destinations": file_destinations,
            "upload_target": validation_result["upload_target"],
            "parent_folder_id": parent_folder_id,
            "summary": {
                "total_folders": len(folder_hierarchy),
                "root_files": len([d for d in file_destinations.values() if d["type"] == "root"]),
                "folder_files": len([d for d in file_destinations.values() if d["type"] == "folder"])
            }
        }

    async def _execute_upload_transaction(
        self,
        kb_id: str,
        user_id: str,
        org_id: str,
        files: List[Dict],
        folder_analysis: Dict,
        validation_result: Dict
    ) -> Dict:
        """Execute upload in single transaction"""
        transaction = None
        try:
            # Start transaction
            transaction = await self.begin_transaction(
                read=[],
                write=[
                    CollectionNames.RECORDS.value,
                    CollectionNames.FILES.value,
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.BELONGS_TO.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.INHERIT_PERMISSIONS.value,
                ]
            )
            timestamp = get_epoch_timestamp_in_ms()

            # Step 1: Ensure all needed folders exist
            folder_map = await self._ensure_folders_exist(
                kb_id=kb_id,
                org_id=org_id,
                folder_analysis=folder_analysis,
                validation_result=validation_result,
                transaction=transaction,
                timestamp=timestamp
            )

            # Step 2: Update file destinations with folder IDs
            self._populate_file_destinations(folder_analysis, folder_map)

            # Step 3: Create all records and relationships
            creation_result = await self._create_records(
                kb_id=kb_id,
                org_id=org_id,
                files=files,
                folder_analysis=folder_analysis,
                transaction=transaction,
                timestamp=timestamp
            )

            if creation_result["total_created"] > 0 or len(folder_map) > 0:
                # Commit transaction; event publishing is done by the router
                await self.commit_transaction(transaction)
                self.logger.info("✅ Upload transaction committed successfully")

                # Build event payloads for router to publish (no publishing here)
                event_payloads = await self._build_upload_event_payloads(kb_id, {
                    "created_files_data": creation_result["created_files_data"],
                    "total_created": creation_result["total_created"]
                })
                event_data = None
                if event_payloads:
                    event_data = {
                        "topic": "record-events",
                        "eventType": "newRecord",
                        "payloads": event_payloads,
                    }

                return {
                    "success": True,
                    "total_created": creation_result["total_created"],
                    "folders_created": len(folder_map),
                    "created_folders": [
                        {"id": folder_id}
                        for folder_id in folder_map.values()
                    ],
                    "failed_files": creation_result["failed_files"],
                    "eventData": event_data,
                }
            else:
                # Nothing created - rollback
                await self.rollback_transaction(transaction)
                self.logger.info("🔄 Transaction rolled back - no items to create")
                return {
                    "success": True,
                    "total_created": 0,
                    "folders_created": 0,
                    "created_folders": [],
                    "failed_files": creation_result["failed_files"],
                }

        except Exception as e:
            if transaction:
                try:
                    await self.rollback_transaction(transaction)
                    self.logger.info("🔄 Transaction rolled back due to error")
                except Exception as abort_error:
                    self.logger.error(f"❌ Failed to rollback transaction: {str(abort_error)}")

            self.logger.error(f"❌ Upload transaction failed: {str(e)}")
            return {"success": False, "reason": f"Transaction failed: {str(e)}", "code": 500}

    async def _ensure_folders_exist(
        self,
        kb_id: str,
        org_id: str,
        folder_analysis: Dict,
        validation_result: Dict,
        transaction: str,
        timestamp: int
    ) -> Dict[str, str]:
        """Ensure all folders in hierarchy exist, creating them if needed"""
        folder_map = {}  # hierarchy_path -> folder_id
        upload_parent_folder_id = None
        if validation_result["upload_target"] == "folder":
            upload_parent_folder_id = validation_result["parent_folder"].get("id") or validation_result["parent_folder"].get("_key")

        for hierarchy_path in folder_analysis["sorted_folder_paths"]:
            folder_info = folder_analysis["folder_hierarchy"][hierarchy_path]
            folder_name = folder_info["name"]
            parent_hierarchy_path = folder_info["parent_path"]

            # Determine parent folder ID
            parent_folder_id = None
            if parent_hierarchy_path:
                parent_folder_id = folder_map.get(parent_hierarchy_path)
                if parent_folder_id is None:
                    raise Exception(f"Parent folder creation failed for path: {parent_hierarchy_path}")
            elif upload_parent_folder_id:
                parent_folder_id = upload_parent_folder_id

            # Check if folder already exists
            existing_folder = await self.find_folder_by_name_in_parent(
                kb_id=kb_id,
                folder_name=folder_name,
                parent_folder_id=parent_folder_id,
                transaction=transaction
            )

            if existing_folder:
                folder_map[hierarchy_path] = existing_folder.get("id") or existing_folder.get("_key")
                self.logger.debug(f"✅ Folder exists: {folder_name}")
            else:
                # Create new folder
                folder = await self.create_folder(
                    kb_id=kb_id,
                    org_id=org_id,
                    folder_name=folder_name,
                    parent_folder_id=parent_folder_id,
                    transaction=transaction
                )
                folder_id = folder['id']
                folder_map[hierarchy_path] = folder_id
                self.logger.info(f"✅ Created folder: {folder_name} -> {folder_id}")

        return folder_map

    def _populate_file_destinations(self, folder_analysis: Dict, folder_map: Dict[str, str]) -> None:
        """Update file destinations with resolved folder IDs"""
        for index, destination in folder_analysis["file_destinations"].items():
            if destination["type"] == "folder":
                hierarchy_path = destination["folder_hierarchy_path"]
                if hierarchy_path in folder_map:
                    destination["folder_id"] = folder_map[hierarchy_path]

    async def _create_records(
        self,
        kb_id: str,
        org_id: str,
        files: List[Dict],
        folder_analysis: Dict,
        transaction: str,
        timestamp: int
    ) -> Dict:
        """Create all records and relationships"""
        total_created = 0
        failed_files = []
        created_files_data = []  # For event publishing

        kb_connector_id = f"knowledgeBase_{org_id}"

        for index, file_data in enumerate(files):
            try:
                destination = folder_analysis["file_destinations"][index]
                parent_folder_id = None

                if destination["type"] == "root":
                    parent_folder_id = folder_analysis.get("parent_folder_id")
                else:
                    parent_folder_id = destination.get("folder_id")
                    if not parent_folder_id:
                        failed_files.append(file_data["filePath"])
                        continue

                # Extract data from file_data
                record_data = file_data["record"].copy()
                file_record_data = file_data["fileRecord"].copy()

                # Enrich record with KB-specific fields
                external_parent_id = parent_folder_id if parent_folder_id else None
                record_data.setdefault("externalGroupId", kb_id)
                record_data.setdefault("externalParentId", external_parent_id)
                record_data.setdefault("externalRootGroupId", kb_id)
                record_data.setdefault("connectorId", kb_connector_id)
                record_data.setdefault("connectorName", Connectors.KNOWLEDGE_BASE.value)
                record_data.setdefault("lastSyncTimestamp", timestamp)
                record_data.setdefault("isVLMOcrProcessed", False)
                record_data.setdefault("extractionStatus", "NOT_STARTED")
                record_data.setdefault("isLatestVersion", True)
                record_data.setdefault("isDirty", False)

                # Convert _key to id for Neo4j
                record_id = record_data.get("_key") or record_data.get("id")
                file_id = file_record_data.get("_key") or file_record_data.get("id")

                record_data["id"] = record_id
                file_record_data["id"] = file_id

                # Also ensure _key is set for event publishing (Kafka expects _key)
                record_data["_key"] = record_id
                file_record_data["_key"] = file_id

                # Create nodes
                await self.batch_upsert_nodes([record_data], CollectionNames.RECORDS.value, transaction)
                await self.batch_upsert_nodes([file_record_data], CollectionNames.FILES.value, transaction)

                # Create edges
                edges = []

                # IS_OF_TYPE edge
                edges.append({
                    "from_id": record_id,
                    "from_collection": CollectionNames.RECORDS.value,
                    "to_id": file_id,
                    "to_collection": CollectionNames.FILES.value,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                })

                # BELONGS_TO edge
                edges.append({
                    "from_id": record_id,
                    "from_collection": CollectionNames.RECORDS.value,
                    "to_id": kb_id,
                    "to_collection": CollectionNames.RECORD_GROUPS.value,
                    "entityType": Connectors.KNOWLEDGE_BASE.value,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                })

                # Record -> KB inheritPermission edge
                # KB records inherit permissions from KB by default
                edges.append({
                    "from_id": record_id,
                    "from_collection": CollectionNames.RECORDS.value,
                    "to_id": kb_id,
                    "to_collection": CollectionNames.RECORD_GROUPS.value,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                })

                # RECORD_RELATIONS edge (if has parent)
                if parent_folder_id:
                    edges.append({
                        "from_id": parent_folder_id,
                        "from_collection": CollectionNames.RECORDS.value,
                        "to_id": record_id,
                        "to_collection": CollectionNames.RECORDS.value,
                        "relationshipType": "PARENT_CHILD",
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp,
                    })

                # Create edges by type
                is_of_type_edges = [e for e in edges if e.get("to_collection") == CollectionNames.FILES.value]
                # belongsTo edges have entityType set (e.g., Connectors.KNOWLEDGE_BASE.value)
                belongs_to_edges = [e for e in edges if e.get("entityType") == Connectors.KNOWLEDGE_BASE.value]
                # inheritPermission edges point to recordGroups but don't have entityType
                inherit_permission_edges = [e for e in edges if e.get("to_collection") == CollectionNames.RECORD_GROUPS.value and not e.get("entityType")]
                parent_child_edges = [e for e in edges if e.get("relationshipType") == "PARENT_CHILD"]

                if is_of_type_edges:
                    await self.batch_create_edges(is_of_type_edges, CollectionNames.IS_OF_TYPE.value, transaction)
                if belongs_to_edges:
                    await self.batch_create_edges(belongs_to_edges, CollectionNames.BELONGS_TO.value, transaction)
                if inherit_permission_edges:
                    await self.batch_create_edges(inherit_permission_edges, CollectionNames.INHERIT_PERMISSIONS.value, transaction)
                if parent_child_edges:
                    await self.batch_create_edges(parent_child_edges, CollectionNames.RECORD_RELATIONS.value, transaction)

                # Store created file data for event publishing
                created_files_data.append({
                    "record": record_data,
                    "fileRecord": file_record_data
                })

                total_created += 1

            except Exception as e:
                self.logger.error(f"Failed to create record for {file_data.get('filePath')}: {str(e)}")
                failed_files.append(file_data["filePath"])

        return {
            "total_created": total_created,
            "failed_files": failed_files,
            "created_files_data": created_files_data
        }

    def _generate_upload_message(self, result: Dict, upload_type: str) -> str:
        """Generate success message"""
        total_created = result["total_created"]
        folders_created = result["folders_created"]
        failed_files = len(result.get("failed_files", []))

        message = f"Successfully uploaded {total_created} file{'s' if total_created != 1 else ''} to {upload_type}"

        if folders_created > 0:
            message += f" with {folders_created} new subfolder{'s' if folders_created != 1 else ''} created"

        if failed_files > 0:
            message += f". {failed_files} file{'s' if failed_files != 1 else ''} failed to upload"

        return message + "."

    async def _build_upload_event_payloads(self, kb_id: str, result: Dict) -> List[Dict]:
        """
        Build event payloads for uploaded records. Does NOT publish; caller (router) publishes.
        Returns list of payloads for eventData.payloads (topic=record-events, eventType=newRecord).
        """
        try:
            created_files_data = result.get("created_files_data", [])

            if not created_files_data:
                self.logger.debug("No new records were created, no event payloads.")
                return []

            # Get storage endpoint
            try:
                endpoints = await self.config_service.get_config(config_node_constants.ENDPOINTS.value)
                if endpoints and isinstance(endpoints, dict):
                    storage_url = endpoints.get("storage", {}).get("endpoint", "http://localhost:3000")
                else:
                    self.logger.warning("⚠️ Endpoints config not found, using default storage URL")
                    storage_url = "http://localhost:3000"
            except Exception as config_error:
                self.logger.error(f"❌ Failed to get storage config: {str(config_error)}")
                storage_url = "http://localhost:3000"  # Fallback

            payloads: List[Dict] = []
            for file_data in created_files_data:
                try:
                    record_doc = file_data.get("record")
                    file_doc = file_data.get("fileRecord")

                    if record_doc and file_doc:
                        create_payload = await self._create_new_record_event_payload(
                            record_doc, file_doc, storage_url
                        )
                        if create_payload:
                            payloads.append(create_payload)
                        else:
                            self.logger.warning(f"⚠️ Skipping payload for record {record_doc.get('_key')} - payload creation failed")
                    else:
                        self.logger.warning(f"⚠️ Incomplete file data, skipping payload: {file_data}")
                except Exception as payload_error:
                    self.logger.error(f"❌ Failed to build event payload for record: {str(payload_error)}")

            return payloads
        except Exception as e:
            self.logger.error(f"❌ Critical error building upload event payloads for KB {kb_id}: {str(e)}", exc_info=True)
            return []

    async def _create_new_record_event_payload(self, record_doc: Dict, file_doc: Dict, storage_url: str) -> Dict:
        """
        Creates NewRecordEvent payload to publish to Kafka.
        """
        try:
            record_id = record_doc.get("_key") or record_doc.get("id")
            self.logger.info(f"🚀 Preparing NewRecordEvent for record_id: {record_id}")

            signed_url_route = (
                f"{storage_url}/api/v1/document/internal/{record_doc['externalRecordId']}/download"
            )
            timestamp = get_epoch_timestamp_in_ms()

            # Construct the payload matching the Node.js NewRecordEvent interface
            payload = {
                "orgId": record_doc.get("orgId"),
                "recordId": record_id,
                "recordName": record_doc.get("recordName"),
                "recordType": record_doc.get("recordType"),
                "version": record_doc.get("version", 1),
                "signedUrlRoute": signed_url_route,
                "origin": record_doc.get("origin"),
                "extension": file_doc.get("extension", ""),
                "mimeType": file_doc.get("mimeType", ""),
                "createdAtTimestamp": str(record_doc.get("createdAtTimestamp", timestamp)),
                "updatedAtTimestamp": str(record_doc.get("updatedAtTimestamp", timestamp)),
                "sourceCreatedAtTimestamp": str(record_doc.get("sourceCreatedAtTimestamp", record_doc.get("createdAtTimestamp", timestamp))),
            }

            return payload
        except Exception:
            self.logger.error(
                f"❌ Failed to publish NewRecordEvent for record_id: {record_doc.get('_key', 'N/A')}",
                exc_info=True
            )
            return {}



    async def _reset_indexing_status_to_queued(self, record_id: str) -> None:
        """
        Reset indexing status to QUEUED before sending update/reindex events.
        Only resets if status is not already QUEUED or EMPTY.
        """
        try:
            # Get the record
            record = await self.get_document(record_id, CollectionNames.RECORDS.value)
            if not record:
                self.logger.warning(f"Record {record_id} not found for status reset")
                return

            current_status = record.get("indexingStatus")

            # Only reset if not already QUEUED or EMPTY
            if current_status in [IndexingStatus.QUEUED.value, IndexingStatus.EMPTY.value]:
                self.logger.debug(f"Record {record_id} already has status {current_status}, skipping reset")
                return

            # Update indexing status to QUEUED
            doc = {
                "id": record_id,
                "indexingStatus": IndexingStatus.QUEUED.value,
            }

            await self.batch_upsert_nodes([doc], CollectionNames.RECORDS.value)
            self.logger.debug(f"✅ Reset record {record_id} status from {current_status} to QUEUED")
        except Exception as e:
            # Log but don't fail the main operation if status update fails
            self.logger.error(f"❌ Failed to reset record {record_id} to QUEUED: {str(e)}")

    async def _create_reindex_event_payload(self, record: Dict, file_record: Optional[Dict], user_id: Optional[str] = None, request: Optional["Request"] = None, record_id: Optional[str] = None) -> Dict:
        """Create reindex event payload"""
        try:
            # Handle both translated (_key -> id) and untranslated document formats
            record_key = record.get('_key') or record.get('id') or record_id or ''
            # Get extension and mimeType from file record
            extension = ""
            mime_type = ""
            if file_record:
                extension = file_record.get("extension", "")
                mime_type = file_record.get("mimeType", "")

            # Fallback: check if mimeType is in the record itself (for WebpageRecord, CommentRecord, etc.)
            if not mime_type:
                mime_type = record.get("mimeType", "")

            endpoints = await self.config_service.get_config(
                config_node_constants.ENDPOINTS.value
            )
            signed_url_route = ""
            file_content = ""

            if record.get("origin") == OriginTypes.UPLOAD.value:
                storage_url = endpoints.get("storage", {}).get("endpoint", DefaultEndpoints.STORAGE_ENDPOINT.value)
                signed_url_route = f"{storage_url}/api/v1/document/internal/{record['externalRecordId']}/download"
            else:
                connector_url = endpoints.get("connectors", {}).get("endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value)
                signed_url_route = f"{connector_url}/api/v1/{record.get('orgId')}/{user_id}/{record.get('connectorName', '').lower()}/record/{record_key}/signedUrl"

                if record.get("recordType") == "MAIL":
                    mime_type = "text/gmail_content"
                    try:
                        # Return early for MAIL records with special payload
                        return {
                            "orgId": record.get("orgId"),
                            "recordId": record_key,
                            "recordName": record.get("recordName", ""),
                            "recordType": record.get("recordType", ""),
                            "version": record.get("version", 1),
                            "origin": record.get("origin", ""),
                            "extension": extension,
                            "mimeType": mime_type,
                            "body": file_content,
                            "connectorId": record.get("connectorId", ""),
                            "createdAtTimestamp": str(record.get("createdAtTimestamp", get_epoch_timestamp_in_ms())),
                            "updatedAtTimestamp": str(get_epoch_timestamp_in_ms()),
                            "sourceCreatedAtTimestamp": str(record.get("sourceCreatedAtTimestamp", record.get("createdAtTimestamp", get_epoch_timestamp_in_ms())))
                        }
                    except Exception as decode_error:
                        self.logger.warning(f"Failed to decode file content as UTF-8: {str(decode_error)}")

            # Standard payload for non-MAIL records
            return {
                "orgId": record.get("orgId"),
                "recordId": record_key,
                "recordName": record.get("recordName", ""),
                "recordType": record.get("recordType", ""),
                "version": record.get("version", 1),
                "signedUrlRoute": signed_url_route,
                "origin": record.get("origin", ""),
                "extension": extension,
                "mimeType": mime_type,
                "body": file_content,
                "connectorId": record.get("connectorId", ""),
                "createdAtTimestamp": str(record.get("createdAtTimestamp", get_epoch_timestamp_in_ms())),
                "updatedAtTimestamp": str(get_epoch_timestamp_in_ms()),
                "sourceCreatedAtTimestamp": str(record.get("sourceCreatedAtTimestamp", record.get("createdAtTimestamp", get_epoch_timestamp_in_ms())))
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to create reindex event payload: {str(e)}")
            raise

    async def delete_records(
        self,
        record_ids: List[str],
        kb_id: str,
        folder_id: Optional[str] = None,
        transaction: Optional[str] = None,
    ) -> Dict:
        """Delete multiple records."""
        try:
            if not record_ids:
                return {
                    "success": True,
                    "deleted_records": [],
                    "failed_records": [],
                    "total_requested": 0,
                    "successfully_deleted": 0,
                    "failed_count": 0,
                }

            txn_id = transaction
            if transaction is None:
                txn_id = await self.begin_transaction(
                    read=[],
                    write=[
                        CollectionNames.RECORDS.value,
                        CollectionNames.FILES.value,
                        CollectionNames.RECORD_RELATIONS.value,
                        CollectionNames.IS_OF_TYPE.value,
                        CollectionNames.BELONGS_TO.value,
                    ],
                )

            try:
                # Step 1: Validate records
                validation_query = """
                UNWIND $record_ids AS rid

                OPTIONAL MATCH (record:Record {id: rid})

                WITH rid, record,
                     record IS NOT NULL AS record_exists,
                     CASE WHEN record IS NOT NULL THEN coalesce(record.isDeleted, false) <> true ELSE false END AS record_not_deleted

                // Check KB relationship
                OPTIONAL MATCH (record)-[kb_rel:BELONGS_TO]->(kb:RecordGroup {id: $kb_id})
                WHERE record IS NOT NULL

                // Check folder relationship (if folder_id provided)
                OPTIONAL MATCH (parent:Record {id: $folder_id})-[folder_rel:RECORD_RELATION {relationshipType: 'PARENT_CHILD'}]->(record)
                WHERE record IS NOT NULL AND $folder_id IS NOT NULL

                // Get file record
                OPTIONAL MATCH (record)-[:IS_OF_TYPE]->(file:File)
                WHERE record IS NOT NULL

                WITH rid, record, file,
                     record_exists AND record_not_deleted AND kb_rel IS NOT NULL AND
                     (CASE WHEN $folder_id IS NOT NULL THEN folder_rel IS NOT NULL ELSE true END) AS is_valid

                RETURN {
                    record_id: rid,
                    record: record,
                    file_record: file,
                    is_valid: is_valid
                } AS record_data
                """

                val_results = await self.client.execute_query(
                    validation_query,
                    parameters={
                        "record_ids": record_ids,
                        "kb_id": kb_id,
                        "folder_id": folder_id
                    },
                    txn_id=txn_id
                )

                # Separate valid and invalid records
                valid_records = []
                invalid_records = []

                for result in val_results:
                    record_data = result["record_data"]
                    if record_data["is_valid"]:
                        valid_records.append(record_data)
                    else:
                        invalid_records.append(record_data)

                failed_records = [{"record_id": r["record_id"], "reason": "Validation failed"} for r in invalid_records]

                if not valid_records:
                    if transaction is None and txn_id:
                        await self.commit_transaction(txn_id)
                    return {
                        "success": True,
                        "deleted_records": [],
                        "failed_records": failed_records,
                        "total_requested": len(record_ids),
                        "successfully_deleted": 0,
                        "failed_count": len(failed_records),
                    }

                valid_record_ids = [r["record_id"] for r in valid_records]
                file_record_ids = [r["file_record"]["id"] for r in valid_records if r.get("file_record")]

                # Step 2: Delete edges
                edges_cleanup = """
                UNWIND $record_ids AS record_id

                // Delete RECORD_RELATION edges
                OPTIONAL MATCH (r1:Record {id: record_id})-[rr:RECORD_RELATION]-()
                DELETE rr

                WITH record_id
                OPTIONAL MATCH ()-[rr2:RECORD_RELATION]->(r2:Record {id: record_id})
                DELETE rr2

                WITH record_id
                // Delete IS_OF_TYPE edges
                OPTIONAL MATCH (record:Record {id: record_id})-[iot:IS_OF_TYPE]->()
                DELETE iot

                WITH record_id
                // Delete BELONGS_TO edges
                OPTIONAL MATCH (record:Record {id: record_id})-[bt:BELONGS_TO]->()
                DELETE bt
                """

                await self.client.execute_query(
                    edges_cleanup,
                    parameters={"record_ids": valid_record_ids},
                    txn_id=txn_id
                )

                # Step 3: Delete FILE nodes
                if file_record_ids:
                    delete_files = """
                    MATCH (file:File)
                    WHERE file.id IN $file_ids
                    DELETE file
                    """
                    await self.client.execute_query(
                        delete_files,
                        parameters={"file_ids": file_record_ids},
                        txn_id=txn_id
                    )

                # Step 4: Delete RECORD nodes
                delete_records_query = """
                MATCH (record:Record)
                WHERE record.id IN $record_ids
                DELETE record
                """
                await self.client.execute_query(
                    delete_records_query,
                    parameters={"record_ids": valid_record_ids},
                    txn_id=txn_id
                )

                deleted_records = [
                    {
                        "record_id": r["record_id"],
                        "name": r.get("record", {}).get("recordName", "Unknown")
                    } for r in valid_records
                ]

                if transaction is None and txn_id:
                    await self.commit_transaction(txn_id)

                return {
                    "success": True,
                    "deleted_records": deleted_records,
                    "failed_records": failed_records,
                    "total_requested": len(record_ids),
                    "successfully_deleted": len(deleted_records),
                    "failed_count": len(failed_records),
                }

            except Exception as db_error:
                if transaction is None and txn_id:
                    await self.rollback_transaction(txn_id)
                raise db_error

        except Exception as e:
            self.logger.error(f"❌ Failed to delete records: {str(e)}")
            return {
                "success": False,
                "deleted_records": [],
                "failed_records": [{"record_id": rid, "reason": str(e)} for rid in record_ids],
                "total_requested": len(record_ids),
                "successfully_deleted": 0,
                "failed_count": len(record_ids),
            }

    async def _create_deleted_record_event_payload(
        self,
        record: Dict,
        file_record: Optional[Dict] = None
    ) -> Dict:
        """Create deleted record event payload matching Node.js format"""
        try:
            # Get extension and mimeType from file record
            extension = ""
            mime_type = ""
            if file_record:
                extension = file_record.get("extension", "")
                mime_type = file_record.get("mimeType", "")

            return {
                "orgId": record.get("orgId"),
                "recordId": record.get("id"),
                "version": record.get("version", 1),
                "extension": extension,
                "mimeType": mime_type,
                "summaryDocumentId": record.get("summaryDocumentId"),
                "virtualRecordId": record.get("virtualRecordId"),
            }
        except Exception as e:
            self.logger.error(f"❌ Failed to create deleted record event payload: {str(e)}")
            return {}

    async def delete_knowledge_base(
        self,
        kb_id: str,
        transaction: Optional[str] = None,
    ) -> Dict:
        """
        Delete a knowledge base with ALL nested content
        - All folders (recursive, any depth)
        - All records in all folders
        - All file records
        - All edges (belongs_to, record_relations, is_of_type, permissions)
        - The KB document itself
        """
        try:
            # Create transaction if not provided
            should_commit = False
            if transaction is None:
                should_commit = True
                try:
                    transaction = await self.begin_transaction(
                        read=[],
                        write=[
                            CollectionNames.RECORD_GROUPS.value,
                            CollectionNames.FILES.value,
                            CollectionNames.RECORDS.value,
                            CollectionNames.RECORD_RELATIONS.value,
                            CollectionNames.BELONGS_TO.value,
                            CollectionNames.IS_OF_TYPE.value,
                            CollectionNames.PERMISSION.value,
                        ],
                    )
                    self.logger.info(f"🔄 Transaction created for complete KB {kb_id} deletion")
                except Exception as tx_error:
                    self.logger.error(f"❌ Failed to create transaction: {str(tx_error)}")
                    return {"success": False}

            try:
                # Step 1: Get complete inventory of what we're deleting using graph traversal
                # This collects ALL records/folders at any depth via BELONGS_TO edges BEFORE deletion
                inventory_query = """
                MATCH (kb:RecordGroup {id: $kb_id})

                // Get all records/folders that belong to this KB
                OPTIONAL MATCH (record:Record)-[:BELONGS_TO]->(kb)

                // Get file details for each record
                OPTIONAL MATCH (record)-[:IS_OF_TYPE]->(file:File)

                WITH kb,
                     collect(DISTINCT record) AS all_records,
                     collect(DISTINCT {
                         file_id: file.id,
                         is_folder: file.isFile = false,
                         record_id: record.id,
                         record: record,
                         file_doc: file
                     }) AS all_files_with_details

                // Separate folders and file records
                WITH kb,
                     all_records,
                     [item IN all_files_with_details WHERE item.is_folder = true | item.record_id] AS folder_keys,
                     [item IN all_files_with_details WHERE item.is_folder = false | {
                         record: item.record,
                         file_record: item.file_doc
                     }] AS file_records,
                     [item IN all_files_with_details | item.file_id] AS file_keys

                RETURN {
                    kb_exists: kb IS NOT NULL,
                    record_ids: [r IN all_records | r.id],
                    file_ids: file_keys,
                    folder_keys: folder_keys,
                    records_with_details: file_records,
                    total_folders: size(folder_keys),
                    total_records: size(all_records)
                } AS inventory
                """

                inv_results = await self.client.execute_query(
                    inventory_query,
                    parameters={"kb_id": kb_id},
                    txn_id=transaction
                )

                inventory = inv_results[0]["inventory"] if inv_results else {}

                self.logger.info(f"inventory: {inventory}")
                # return False

                if not inventory.get("kb_exists"):
                    self.logger.warning(f"⚠️ KB {kb_id} not found, deletion considered successful.")
                    if should_commit:
                        await self.commit_transaction(transaction)
                    return {"success": True, "eventData": None}

                records_with_details = inventory.get("records_with_details", [])
                all_record_ids = inventory.get("record_ids", [])
                all_file_ids = inventory.get("file_ids", [])

                self.logger.info(f"folder_keys: {inventory.get('folder_keys', [])}")
                self.logger.info(f"total_folders: {inventory.get('total_folders', 0)}")
                self.logger.info(f"total_records: {inventory.get('total_records', 0)}")

                # Step 2: Delete ALL relationships first (prevents orphaned edges)
                self.logger.info("🗑️ Step 2: Deleting all relationships...")

                if all_record_ids:
                    # First, delete all relationships connected to the records (comprehensive approach)
                    edges_cleanup_query = """
                    UNWIND $record_ids AS record_id
                    MATCH (record:Record {id: record_id})
                    OPTIONAL MATCH (record)-[r]-()
                    WITH collect(DISTINCT r) AS all_edges
                    UNWIND all_edges AS edge
                    WITH edge WHERE edge IS NOT NULL
                    DELETE edge
                    RETURN count(edge) AS edges_deleted
                    """

                    edge_results = await self.client.execute_query(
                        edges_cleanup_query,
                        parameters={
                            "record_ids": all_record_ids
                        },
                        txn_id=transaction
                    )

                    edges_deleted = edge_results[0]["edges_deleted"] if edge_results else 0
                    self.logger.info(f"✅ Deleted {edges_deleted} edges for records")

                    # Also delete KB-related edges
                    kb_edges_query = """
                    MATCH (kb:RecordGroup {id: $kb_id})
                    OPTIONAL MATCH (kb)-[r]-()
                    WITH collect(DISTINCT r) AS kb_edges
                    UNWIND kb_edges AS edge
                    WITH edge WHERE edge IS NOT NULL
                    DELETE edge
                    RETURN count(edge) AS kb_edges_deleted
                    """

                    kb_edge_results = await self.client.execute_query(
                        kb_edges_query,
                        parameters={"kb_id": kb_id},
                        txn_id=transaction
                    )

                    kb_edges_deleted = kb_edge_results[0]["kb_edges_deleted"] if kb_edge_results else 0
                    self.logger.info(f"✅ Deleted {kb_edges_deleted} edges for KB {kb_id}")

                # Step 3: Delete all FILE nodes
                if all_file_ids:
                    self.logger.info(f"🗑️ Step 3: Deleting {len(all_file_ids)} FILE nodes...")
                    delete_files_query = """
                    MATCH (file:File)
                    WHERE file.id IN $file_ids
                    DELETE file
                    RETURN count(file) AS deleted
                    """

                    file_results = await self.client.execute_query(
                        delete_files_query,
                        parameters={"file_ids": all_file_ids},
                        txn_id=transaction
                    )

                    files_deleted = file_results[0]["deleted"] if file_results else 0
                    self.logger.info(f"✅ Deleted {files_deleted} FILE nodes")

                # Step 4: Delete all RECORD nodes
                if all_record_ids:
                    self.logger.info(f"🗑️ Step 4: Deleting {len(all_record_ids)} RECORD nodes...")
                    delete_records_query = """
                    MATCH (record:Record)
                    WHERE record.id IN $record_ids
                    DELETE record
                    RETURN count(record) AS deleted
                    """

                    record_results = await self.client.execute_query(
                        delete_records_query,
                        parameters={"record_ids": all_record_ids},
                        txn_id=transaction
                    )

                    records_deleted = record_results[0]["deleted"] if record_results else 0
                    self.logger.info(f"✅ Deleted {records_deleted} RECORD nodes")

                # Step 5: Delete any remaining relationships on the KB node, then delete the KB RecordGroup itself
                self.logger.info(f"🗑️ Step 5: Deleting remaining KB relationships and KB RecordGroup {kb_id}...")
                delete_kb_query = """
                MATCH (kb:RecordGroup {id: $kb_id})
                OPTIONAL MATCH (kb)-[r]-()
                DELETE r, kb
                RETURN count(kb) AS deleted
                """

                kb_results = await self.client.execute_query(
                    delete_kb_query,
                    parameters={"kb_id": kb_id},
                    txn_id=transaction
                )

                kb_deleted = kb_results[0]["deleted"] if kb_results else 0
                self.logger.info(f"✅ Deleted KB RecordGroup: {kb_deleted}")

                # Step 6: Commit transaction
                if should_commit:
                    self.logger.info("💾 Committing complete deletion transaction...")
                    await self.commit_transaction(transaction)
                    self.logger.info("✅ Transaction committed successfully!")

                # Step 7: Prepare event data for all deleted records (router will publish)
                event_payloads = []
                try:
                    for record_data in records_with_details:
                        delete_payload = await self._create_deleted_record_event_payload(
                            record_data["record"], record_data.get("file_record")
                        )
                        if delete_payload:
                            delete_payload["connectorName"] = Connectors.KNOWLEDGE_BASE.value
                            delete_payload["origin"] = OriginTypes.UPLOAD.value
                            event_payloads.append(delete_payload)
                except Exception as e:
                    self.logger.error(f"❌ Failed to prepare deletion event payloads: {str(e)}")

                event_data = {
                    "eventType": "deleteRecord",
                    "topic": "record-events",
                    "payloads": event_payloads
                } if event_payloads else None

                self.logger.info(f"🎉 KB {kb_id} and ALL contents deleted successfully.")
                return {
                    "success": True,
                    "eventData": event_data
                }

            except Exception as db_error:
                self.logger.error(f"❌ Database error during KB deletion: {str(db_error)}")
                if should_commit and transaction:
                    await self.rollback_transaction(transaction)
                    self.logger.info("🔄 Transaction aborted due to error")
                raise db_error

        except Exception as e:
            self.logger.error(f"❌ Failed to delete KB {kb_id}: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
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
                "grantedCount": granted_count,
                "grantedUsers": list(user_ids),
                "grantedTeams": list(team_ids),
                "role": role,
                "kbId": kb_id,
                "details": {},
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
        """Optimistically update permissions for users and teams on a knowledge base"""
        try:
            self.logger.info(f"🚀 Optimistic update: {len(user_ids or [])} users and {len(team_ids or [])} teams on KB {kb_id} to {new_role}")

            # Quick validation of inputs
            if not user_ids and not team_ids:
                return {"success": False, "reason": "No users or teams provided", "code": "400"}

            # Validate new role
            valid_roles = ["OWNER", "ORGANIZER", "FILEORGANIZER", "WRITER", "COMMENTER", "READER"]
            if new_role not in valid_roles:
                return {
                    "success": False,
                    "reason": f"Invalid role. Must be one of: {', '.join(valid_roles)}",
                    "code": "400"
                }

            timestamp = get_epoch_timestamp_in_ms()

            # First, verify requester has OWNER permission
            requester_check_query = """
            MATCH (u:User {id: $requester_id})-[r:PERMISSION {type: "USER"}]->(kb:RecordGroup {id: $kb_id})
            RETURN r.role as role
            """
            requester_result = await self.client.execute_query(
                requester_check_query,
                parameters={"requester_id": requester_id, "kb_id": kb_id},
                txn_id=transaction
            )

            requester_role = requester_result[0].get("role") if requester_result else None
            if requester_role != "OWNER":
                return {
                    "success": False,
                    "reason": "Only KB owners can update permissions",
                    "code": "403"
                }

            # Update user permissions and collect details
            updated_users = 0
            updates_by_type = {"users": {}, "teams": {}}

            for user_id in user_ids:
                # Get current role before update
                get_current_query = """
                MATCH (u:User {id: $user_id})-[r:PERMISSION {type: "USER"}]->(kb:RecordGroup {id: $kb_id})
                RETURN r.role as old_role
                """
                current_result = await self.client.execute_query(
                    get_current_query,
                    parameters={"user_id": user_id, "kb_id": kb_id},
                    txn_id=transaction
                )

                if current_result:
                    old_role = current_result[0].get("old_role")

                    # Update the permission
                    update_query = """
                    MATCH (u:User {id: $user_id})-[r:PERMISSION {type: "USER"}]->(kb:RecordGroup {id: $kb_id})
                    SET r.role = $new_role, r.updatedAtTimestamp = $timestamp, r.lastUpdatedTimestampAtSource = $timestamp
                    RETURN r
                    """
                    await self.client.execute_query(
                        update_query,
                        parameters={"user_id": user_id, "kb_id": kb_id, "new_role": new_role, "timestamp": timestamp},
                        txn_id=transaction
                    )

                    updated_users += 1
                    updates_by_type["users"][user_id] = {
                        "old_role": old_role,
                        "new_role": new_role
                    }

            # Teams don't have roles - they just have access or not
            # So we don't update team permissions
            updated_teams = 0

            self.logger.info(f"✅ Optimistically updated {updated_users} user permissions for KB {kb_id}")

            return {
                "success": True,
                "kb_id": kb_id,
                "new_role": new_role,
                "updated_permissions": updated_users + updated_teams,
                "updated_users": updated_users,
                "updated_teams": updated_teams,
                "updates_detail": updates_by_type,
                "requester_role": requester_role
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to update KB permission optimistically: {str(e)}")
            return {
                "success": False,
                "reason": str(e),
                "code": "500"
            }

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

            # Remove team permissions (Neo4j label is "Teams")
            for team_id in team_ids:
                query = """
                MATCH (t:Teams {id: $team_id})-[r:PERMISSION {type: "TEAM"}]->(kb:RecordGroup {id: $kb_id})
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
        """List all permissions for a knowledge base with entity details"""
        try:
            query = """
            MATCH (entity)-[r:PERMISSION]->(kb:RecordGroup {id: $kb_id})
            RETURN
                properties(entity) as entity_props,
                labels(entity) as entity_labels,
                properties(r) as rel_props,
                id(r) as rel_id
            """

            results = await self.client.execute_query(
                query,
                parameters={"kb_id": kb_id},
                txn_id=transaction
            )

            permissions = []
            for r in results:
                entity_props = r.get("entity_props", {})
                entity_labels = r.get("entity_labels", [])
                rel_props = r.get("rel_props", {})
                r.get("rel_id")

                entity_type = "USER" if "User" in entity_labels else "TEAM"
                is_user = entity_type == "USER"

                # Build permission object matching the response model
                permission = {
                    "id": entity_props.get("_key") or entity_props.get("id"),
                    "type": rel_props.get("type", entity_type),
                    "createdAtTimestamp": rel_props.get("createdAtTimestamp", 0),
                    "updatedAtTimestamp": rel_props.get("updatedAtTimestamp", 0),
                }

                # Add user-specific fields
                if is_user:
                    permission["userId"] = entity_props.get("userId")
                    permission["email"] = entity_props.get("email")
                    permission["name"] = (
                        entity_props.get("fullName") or
                        entity_props.get("name") or
                        entity_props.get("userName")
                    )
                    permission["role"] = rel_props.get("role")
                else:
                    # Team permissions
                    permission["name"] = entity_props.get("name")
                    permission["role"] = None  # Teams don't have roles
                    permission["userId"] = None
                    permission["email"] = None

                permissions.append(permission)

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

                OPTIONAL MATCH (u)-[userTeamPerm:PERMISSION {{type: "USER"}}]->(team:Teams)
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

                OPTIONAL MATCH (u)-[userTeamPerm:PERMISSION {{type: "USER"}}]->(team:Teams)
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

                OPTIONAL MATCH (u)-[userTeamPerm:PERMISSION {type: "USER"}]->(team:Teams)
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

            # Main query - get all records from folders AND KB root
            # Uses UNION to combine folder-based records and root-level records
            main_query = f"""
            // Part 1: Records in folders
            MATCH (kb:RecordGroup {{id: $kb_id}})
            MATCH (folder:Record)-[:BELONGS_TO]->(kb)
            WHERE folder.isFile = false{folder_match}
            MATCH (folder)-[rel:RECORD_RELATION {{relationshipType: "PARENT_CHILD"}}]->(record:Record)
            WHERE record.isDeleted <> true
            AND record.orgId = $org_id
            AND record.recordType = "FILE"
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

            UNION

            // Part 2: Records at KB root (no parent folder)
            MATCH (kb:RecordGroup {{id: $kb_id}})
            MATCH (record:Record)-[:BELONGS_TO]->(kb)
            WHERE record.isDeleted <> true
            AND record.orgId = $org_id
            AND record.recordType = "FILE"
            AND NOT EXISTS {{
                MATCH (parentFolder:Record)-[:RECORD_RELATION {{relationshipType: "PARENT_CHILD"}}]->(record)
                WHERE parentFolder.recordType <> "FILE"
            }}
            {record_filter}
            OPTIONAL MATCH (record)-[:IS_OF_TYPE]->(file:File)

            WITH record, file, $user_permission AS user_permission, $kb_id AS kb_id

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
                folder: null
            }} AS result

            ORDER BY result.{sort_by} {sort_order.upper()}
            SKIP $skip
            LIMIT $limit
            """

            results = await self.client.execute_query(main_query, parameters=params, txn_id=transaction)
            records = [r["result"] for r in results if r.get("result")]

            # Count query - includes both folder-based and root-level records
            count_params = {k: v for k, v in params.items() if k not in ["skip", "limit", "user_permission"]}
            count_query = f"""
            // Count records in folders
            MATCH (kb:RecordGroup {{id: $kb_id}})
            OPTIONAL MATCH (folder:Record)-[:BELONGS_TO]->(kb)
            WHERE folder.isFile = false{folder_match}
            OPTIONAL MATCH (folder)-[:RECORD_RELATION {{relationshipType: "PARENT_CHILD"}}]->(folderRecord:Record)
            WHERE folderRecord.isDeleted <> true
            AND folderRecord.orgId = $org_id
            AND folderRecord.recordType = "FILE"
            {record_filter.replace('record.', 'folderRecord.')}

            // Count records at KB root
            OPTIONAL MATCH (rootRecord:Record)-[:BELONGS_TO]->(kb)
            WHERE rootRecord.isDeleted <> true
            AND rootRecord.orgId = $org_id
            AND rootRecord.recordType = "FILE"
            AND NOT EXISTS {{
                MATCH (parentFolder:Record)-[:RECORD_RELATION {{relationshipType: "PARENT_CHILD"}}]->(rootRecord)
                WHERE parentFolder.recordType <> "FILE"
            }}
            {record_filter.replace('record.', 'rootRecord.')}

            WITH collect(DISTINCT folderRecord) + collect(DISTINCT rootRecord) AS allRecords
            UNWIND allRecords AS record
            WITH DISTINCT record WHERE record IS NOT NULL
            RETURN count(record) AS total
            """

            count_results = await self.client.execute_query(count_query, parameters=count_params, txn_id=transaction)
            total_count = count_results[0]["total"] if count_results else 0

            # Filters query - get available filter values (includes root-level records)
            filters_params = {
                "kb_id": kb_id,
                "org_id": org_id,
                "user_permission": user_permission
            }
            filters_query = """
            MATCH (kb:RecordGroup {id: $kb_id})

            // Get records from folders
            OPTIONAL MATCH (folder:Record)-[:BELONGS_TO]->(kb)
            WHERE folder.isFile = false
            OPTIONAL MATCH (folder)-[:RECORD_RELATION {relationshipType: "PARENT_CHILD"}]->(folderRecord:Record)
            WHERE folderRecord.isDeleted <> true
            AND folderRecord.orgId = $org_id
            AND folderRecord.recordType = "FILE"

            // Get records at KB root
            OPTIONAL MATCH (rootRecord:Record)-[:BELONGS_TO]->(kb)
            WHERE rootRecord.isDeleted <> true
            AND rootRecord.orgId = $org_id
            AND rootRecord.recordType = "FILE"
            AND NOT EXISTS {
                MATCH (pf:Record)-[:RECORD_RELATION {relationshipType: "PARENT_CHILD"}]->(rootRecord)
                WHERE pf.recordType <> "FILE"
            }

            WITH collect(DISTINCT folderRecord) + collect(DISTINCT rootRecord) AS allRecords,
                 collect(DISTINCT folder) AS allFolders
            UNWIND allRecords AS record
            WITH DISTINCT record, allFolders WHERE record IS NOT NULL

            WITH collect(DISTINCT record.recordType) AS recordTypes,
                 collect(DISTINCT record.origin) AS origins,
                 collect(DISTINCT record.connectorName) AS connectors,
                 collect(DISTINCT record.indexingStatus) AS indexingStatus,
                 allFolders

            RETURN {
                recordTypes: [r IN recordTypes WHERE r IS NOT NULL],
                origins: [o IN origins WHERE o IS NOT NULL],
                connectors: [c IN connectors WHERE c IS NOT NULL],
                indexingStatus: [i IN indexingStatus WHERE i IS NOT NULL],
                permissions: [$user_permission],
                folders: [f IN allFolders WHERE f IS NOT NULL AND f.id IS NOT NULL | {id: f.id, name: f.recordName}]
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
            # NEW LOGIC: Immediate children are identified by:
            # 1. BELONGS_TO edge to KB
            # 2. NO incoming RECORD_RELATION edges (not a child of another folder)
            folders_query = f"""
            MATCH (kb:RecordGroup {{id: $kb_id}})
            // Get immediate children (folders with BELONGS_TO but no incoming RECORD_RELATION)
            MATCH (folder_record:Record)-[:BELONGS_TO]->(kb)
            WHERE folder_record.isDeleted <> true
              AND NOT EXISTS {{
                  MATCH (folder_record)<-[:RECORD_RELATION {{relationshipType: "PARENT_CHILD"}}]-(:Record)
              }}
            MATCH (folder_record)-[:IS_OF_TYPE]->(folder_file:File)
            WHERE folder_file.isFile = false
            {folder_filter}
            WITH folder_record, folder_file, 1 AS current_level
            // Get counts for this folder (direct children only)
            OPTIONAL MATCH (folder_record)-[:RECORD_RELATION {{relationshipType: "PARENT_CHILD"}}]->(child_record:Record)
            OPTIONAL MATCH (child_record)-[:IS_OF_TYPE]->(child_file:File)
            WITH folder_record, folder_file, current_level,
                 sum(CASE WHEN child_file IS NOT NULL AND child_file.isFile = false THEN 1 ELSE 0 END) AS direct_subfolders,
                 sum(CASE WHEN child_record IS NOT NULL AND child_record.isDeleted <> true AND (child_file IS NULL OR child_file.isFile <> false) THEN 1 ELSE 0 END) AS direct_records
            ORDER BY folder_record.recordName ASC
            RETURN {{
                id: folder_record.id,
                name: folder_record.recordName,
                path: folder_file.path,
                level: current_level,
                parent_id: null,
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
            # NEW LOGIC: Immediate children with BELONGS_TO but no incoming RECORD_RELATION
            records_query = f"""
            MATCH (kb:RecordGroup {{id: $kb_id}})
            MATCH (record:Record)-[:BELONGS_TO]->(kb)
            WHERE record.isDeleted <> true
              AND NOT EXISTS {{
                  MATCH (record)<-[:RECORD_RELATION {{relationshipType: "PARENT_CHILD"}}]-(:Record)
              }}
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
        """
        Get folder contents with folders_first pagination and level order traversal.

        NEW LOGIC: Children are identified via RECORD_RELATION edges with relationshipType="PARENT_CHILD"
        """
        try:
            self.logger.info(f"🔍 Getting folder {folder_id} children with folders_first pagination (skip={skip}, limit={limit}, level={level})")

            # Build filter conditions
            folder_conditions = []
            record_conditions = []
            params = {
                "folder_id": folder_id,
                "kb_id": kb_id,
                "skip": skip,
                "limit": limit,
                "level": level
            }

            if search:
                folder_conditions.append("toLower(subfolder_record.recordName) CONTAINS toLower($search)")
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

            # Sort field mapping for records
            record_sort_map = {
                "name": "record.recordName",
                "created_at": "record.createdAtTimestamp",
                "updated_at": "record.updatedAtTimestamp",
                "size": "file.sizeInBytes"
            }
            record_sort_field = record_sort_map.get(sort_by, "record.recordName")
            sort_direction = sort_order.upper() if sort_order.upper() in ["ASC", "DESC"] else "ASC"

            # Query to get all subfolders (direct children via RECORD_RELATION)
            folders_query = f"""
            MATCH (folder_record:Record {{id: $folder_id}})
            MATCH (folder_record)-[:IS_OF_TYPE]->(folder_file:File)
            WHERE folder_file.isFile = false
            // Get direct subfolders via RECORD_RELATION edges
            MATCH (folder_record)-[:RECORD_RELATION {{relationshipType: "PARENT_CHILD"}}]->(subfolder_record:Record)
            MATCH (subfolder_record)-[:IS_OF_TYPE]->(subfolder_file:File)
            WHERE subfolder_file.isFile = false
            {folder_filter}
            WITH subfolder_record, subfolder_file, 1 AS current_level
            // Get counts for this subfolder
            OPTIONAL MATCH (subfolder_record)-[:RECORD_RELATION {{relationshipType: "PARENT_CHILD"}}]->(child_record:Record)
            OPTIONAL MATCH (child_record)-[:IS_OF_TYPE]->(child_file:File)
            WITH subfolder_record, subfolder_file, current_level,
                 sum(CASE WHEN child_file IS NOT NULL AND child_file.isFile = false THEN 1 ELSE 0 END) AS direct_subfolders,
                 sum(CASE WHEN child_record IS NOT NULL AND child_record.isDeleted <> true AND (child_file IS NULL OR child_file.isFile <> false) THEN 1 ELSE 0 END) AS direct_records
            ORDER BY subfolder_record.recordName ASC
            RETURN {{
                id: subfolder_record.id,
                name: subfolder_record.recordName,
                path: subfolder_file.path,
                level: current_level,
                parent_id: $folder_id,
                webUrl: subfolder_record.webUrl,
                recordGroupId: subfolder_record.connectorId,
                type: "folder",
                createdAtTimestamp: subfolder_record.createdAtTimestamp,
                updatedAtTimestamp: subfolder_record.updatedAtTimestamp,
                counts: {{
                    subfolders: direct_subfolders,
                    records: direct_records,
                    totalItems: direct_subfolders + direct_records
                }},
                hasChildren: direct_subfolders > 0 OR direct_records > 0
            }} AS folder
            """

            # Query to get all records directly in folder (excluding folders)
            records_query = f"""
            MATCH (folder_record:Record {{id: $folder_id}})-[:RECORD_RELATION {{relationshipType: "PARENT_CHILD"}}]->(record:Record)
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
                "folders": paginated_folders,
                "records": paginated_records,
                "counts": {
                    "folders": total_folders,
                    "records": total_records,
                    "totalItems": total_count,
                    "foldersShown": len(paginated_folders),
                    "recordsShown": len(paginated_records)
                },
                "totalCount": total_count,
                "availableFilters": available_filters
            }

            self.logger.info(
                f"✅ Folder children retrieved: {len(paginated_folders)} folders, {len(paginated_records)} records "
                f"(total: {total_folders} folders, {total_records} records)"
            )
            return result

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
            RETURN properties(r) AS r, labels(target) AS target_labels, target.id AS target_id
            """

            results = await self.client.execute_query(
                query,
                parameters={"key": key},
                txn_id=transaction
            )

            # Create reverse mapping from label to collection
            label_to_collection = {v: k for k, v in COLLECTION_TO_LABEL.items()}

            edges = []
            for result in results:
                edge_dict = result.get("r", {})  # properties(r) already returns a dict
                target_labels = result.get("target_labels", [])
                target_id = result.get("target_id")

                # Determine target collection from labels
                target_collection = "records"  # Default
                for label in target_labels:
                    # Use reverse mapping to get collection from label
                    if label in label_to_collection:
                        target_collection = label_to_collection[label]
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
        collection: str,
        instance_name: str,
        scope: str,
        org_id: Optional[str] = None,
        user_id: Optional[str] = None,
        transaction: Optional[str] = None,
    ) -> bool:
        """Check if a connector instance name already exists for the given scope."""
        try:
            normalized_name = instance_name.strip().lower()
            label = self._get_label(collection)

            if scope == "personal":
                # For personal scope: check existence within user's personal connectors
                query = f"""
                MATCH (doc:{label})
                WHERE doc.scope = $scope
                AND doc.createdBy = $user_id
                AND toLower(trim(doc.name)) = $normalized_name
                RETURN doc.id AS id
                LIMIT 1
                """
                parameters = {
                    "scope": scope,
                    "user_id": user_id,
                    "normalized_name": normalized_name,
                }
            else:  # team scope
                # For team scope: check existence within organization's team connectors
                edge_collection = CollectionNames.ORG_APP_RELATION.value
                rel_type = self._get_relationship_type(edge_collection)
                query = f"""
                MATCH (org:Organization {{id: $org_id}})-[r:{rel_type}]->(doc:{label})
                WHERE doc.scope = $scope
                AND toLower(trim(doc.name)) = $normalized_name
                RETURN doc.id AS id
                LIMIT 1
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
            return len(results) > 0

        except Exception as e:
            self.logger.error(f"Failed to check connector name exists: {e}")
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
        parent_collection: str = "records",
        child_collection: str = "records",
        collection: str = "recordRelations",
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
            MERGE (parent)-[r:{rel_type} {{relationshipType: "PARENT_CHILD"}}]->(child)
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
        from_id: str,
        from_collection: str,
        collection: str,
        relationship_types: List[str],
        transaction: Optional[str] = None
    ) -> int:
        """Delete edges by relationship types."""
        try:
            rel_type = edge_collection_to_relationship(collection)
            from_label = collection_to_label(from_collection)
            query = f"""
            MATCH (r:{from_label} {{id: $from_id}})-[rel:{rel_type}]->()
            WHERE rel.relationshipType IN $relationship_types
            DELETE rel
            RETURN count(rel) as deleted_count
            """
            results = await self.client.execute_query(
                query,
                parameters={"from_id": from_id, "relationship_types": relationship_types},
                txn_id=transaction
            )
            total = sum(row.get("deleted_count", 0) for row in results) if results else 0
            return total
        except Exception as e:
            self.logger.error(f"❌ Delete edges by relationship types failed: {str(e)}")
            return 0

    async def delete_parent_child_edge_to_record(
        self,
        record_id: str,
        transaction: Optional[str] = None
    ) -> bool:
        """Delete parent-child edge to a record."""
        try:
            rel_type = edge_collection_to_relationship(CollectionNames.RECORD_RELATIONS.value)
            query = f"""
            MATCH ()-[r:{rel_type} {{relationshipType: "PARENT_CHILD"}}]->(child:Record {{id: $record_id}})
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
        """Get filtered connector instances with pagination and scope counts."""
        try:
            label = self._get_label(collection)

            # Build WHERE conditions
            conditions = ["doc.id IS NOT NULL"]
            params = {}

            # Exclude KB if requested
            if exclude_kb and kb_connector_type:
                conditions.append("doc.type <> $kb_connector_type")
                params["kb_connector_type"] = kb_connector_type

            # Scope filter
            if scope == "personal":
                conditions.append("doc.scope = $scope")
                conditions.append("doc.createdBy = $user_id")
                params["scope"] = scope
                params["user_id"] = user_id
            elif scope == "team":
                conditions.append("(doc.scope = $team_scope OR doc.createdBy = $user_id)")
                params["team_scope"] = "team"
                params["user_id"] = user_id

            # Search filter
            if search:
                search_pattern = f"(?i).*{search}.*"
                conditions.append("(doc.name =~ $search OR doc.type =~ $search OR doc.appGroup =~ $search)")
                params["search"] = search_pattern

            where_clause = " AND ".join(conditions)

            # Count query
            count_query = f"""
            MATCH (doc:{label})
            WHERE {where_clause}
            RETURN count(doc) as total
            """
            count_result = await self.client.execute_query(count_query, parameters=params, txn_id=transaction)
            total_count = count_result[0]["total"] if count_result else 0

            # Scope counts (personal and team)
            scope_counts = {"personal": 0, "team": 0}

            # Personal count
            personal_query = f"""
            MATCH (doc:{label})
            WHERE doc.id IS NOT NULL
            AND doc.scope = $personal_scope
            AND doc.createdBy = $user_id
            AND doc.isConfigured = true
            RETURN count(doc) as total
            """
            personal_result = await self.client.execute_query(
                personal_query,
                parameters={"personal_scope": "personal", "user_id": user_id},
                txn_id=transaction
            )
            scope_counts["personal"] = personal_result[0]["total"] if personal_result else 0

            # Team count (if admin or has team access)
            if is_admin or scope == "team":
                team_query = f"""
                MATCH (doc:{label})
                WHERE doc.id IS NOT NULL
                AND doc.type <> $kb_connector_type
                AND doc.scope = $team_scope
                AND doc.isConfigured = true
                RETURN count(doc) as total
                """
                team_result = await self.client.execute_query(
                    team_query,
                    parameters={"kb_connector_type": kb_connector_type or "", "team_scope": "team"},
                    txn_id=transaction
                )
                scope_counts["team"] = team_result[0]["total"] if team_result else 0

            # Main query with pagination
            main_query = f"""
            MATCH (doc:{label})
            WHERE {where_clause}
            RETURN doc
            SKIP $skip
            LIMIT $limit
            """
            params["skip"] = skip
            params["limit"] = limit

            results = await self.client.execute_query(main_query, parameters=params, txn_id=transaction)
            documents = [self._neo4j_to_arango_node(dict(r["doc"]), collection) for r in results] if results else []

            self.logger.info(f"✅ Found {len(documents)} connector instances (total: {total_count})")
            return documents, total_count, scope_counts

        except Exception as e:
            self.logger.error(f"❌ Get filtered connector instances failed: {str(e)}")
            return [], 0, {"personal": 0, "team": 0}

    async def get_kb_permissions(
        self,
        kb_id: str,
        user_ids: Optional[List[str]] = None,
        team_ids: Optional[List[str]] = None,
        transaction: Optional[str] = None,
    ) -> Dict[str, Dict[str, str]]:
        """Get current roles for users and teams on a KB. Returns {users: {id: role}, teams: {id: None}}."""
        try:
            result = {"users": {}, "teams": {}}
            if not user_ids and not team_ids:
                return result

            user_label = collection_to_label(CollectionNames.USERS.value)
            team_label = collection_to_label(CollectionNames.TEAMS.value)
            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)

            conditions = []
            params: Dict[str, Any] = {"kb_id": kb_id}
            if user_ids:
                conditions.append(f"(entity:{user_label} AND entity.id IN $user_ids)")
                params["user_ids"] = user_ids
            if team_ids:
                conditions.append(f"(entity:{team_label} AND entity.id IN $team_ids)")
                params["team_ids"] = team_ids
            if not conditions:
                return result

            query = f"""
            MATCH (entity)-[p:{permission_rel}]->(kb:RecordGroup {{id: $kb_id}})
            WHERE {' OR '.join(conditions)}
            RETURN entity.id AS id, labels(entity)[0] AS entityLabel, p.role AS role, p.type AS type
            """
            results = await self.client.execute_query(
                query,
                parameters=params,
                txn_id=transaction
            )
            for r in (results or []):
                eid = r.get("id")
                label = r.get("entityLabel", "")
                role = r.get("role")
                if eid is None:
                    continue
                if label == user_label or r.get("type") == "USER":
                    result["users"][eid] = role or ""
                elif label == team_label or r.get("type") == "TEAM":
                    result["teams"][eid] = None
            return result
        except Exception as e:
            self.logger.error(f"❌ Get KB permissions failed: {str(e)}")
            raise

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
                return self._create_typed_record_from_neo4j_simple(record_data)
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
                return self._create_typed_record_from_neo4j_simple(record_data)
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
            MATCH (parent:Record)-[:RECORD_RELATION {relationshipType: "PARENT_CHILD"}]->(r:Record {id: $record_id})
            RETURN {
                id: parent.id,
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
                typed_record = self._create_typed_record_from_neo4j_simple(record_data)
                if typed_record:
                    records.append(typed_record)
            return records
        except Exception as e:
            self.logger.error(f"❌ Get records failed: {str(e)}")
            return []

    async def get_user_connector_instances(
        self,
        collection: str,
        user_id: str,
        org_id: str,
        team_scope: str,
        personal_scope: str,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """Get all connector instances accessible to a user (personal + team)."""
        try:
            # Map collection name to Neo4j label
            label = collection_to_label(collection)

            query = f"""
            MATCH (n:{label})
            WHERE n.id IS NOT NULL
              AND (
                n.scope = $team_scope OR
                (n.scope = $personal_scope AND n.createdBy = $user_id)
              )
            RETURN n
            """
            results = await self.client.execute_query(
                query,
                parameters={
                    "team_scope": team_scope,
                    "personal_scope": personal_scope,
                    "user_id": user_id
                },
                txn_id=transaction
            )

            # Convert Neo4j nodes to ArangoDB format
            return [self._neo4j_to_arango_node(dict(r.get("n", {})), collection) for r in results]
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
            MATCH path = (ancestor:Record {id: $ancestor_id})-[:RECORD_RELATION*1..20 {relationshipType: "PARENT_CHILD"}]->(r:Record {id: $record_id})
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
        transaction: Optional[str] = None
    ) -> bool:
        """Check if record is a folder (isFile == false on its linked File node)."""
        try:
            query = """
            MATCH (r:Record {id: $record_id})-[:IS_OF_TYPE]->(f:File)
            WHERE f.isFile = false
            RETURN count(f) > 0 as is_folder
            """
            results = await self.client.execute_query(
                query,
                parameters={"record_id": record_id},
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
        new_parent_id: Optional[str] = None,
        external_parent_id: Optional[str] = None,
        transaction: Optional[str] = None
    ) -> bool:
        """Update record's external parent ID."""
        try:
            # Accept both param names for compatibility (interface uses new_parent_id)
            parent_val = new_parent_id if new_parent_id is not None else external_parent_id
            query = """
            MATCH (r:Record {id: $record_id})
            SET r.externalParentId = $parent_val
            RETURN count(r) > 0 AS updated
            """
            results = await self.client.execute_query(
                query,
                parameters={"record_id": record_id, "parent_val": parent_val},
                txn_id=transaction
            )
            updated = results[0].get("updated", False) if results else False
            return updated
        except Exception as e:
            self.logger.error(f"❌ Update record external parent ID failed for {record_id}: {str(e)}")
            raise

    def _create_typed_record_from_neo4j_simple(self, record_data: Dict) -> Optional[Record]:
        """
        Create base Record instance from Neo4j data (without type_doc).
        Matches ArangoDB behavior where methods like get_record_by_external_revision_id
        and get_record_by_weburl return base Record objects, not typed records.
        """
        if not record_data:
            return None

        try:
            # Convert Neo4j format to ArangoDB-compatible format
            record_dict = self._neo4j_to_arango_node(record_data, CollectionNames.RECORDS.value)
            # Return base Record (matching ArangoDB pattern)
            return Record.from_arango_base_record(record_dict)
        except Exception as e:
            self.logger.warning(f"Failed to create record from Neo4j data: {str(e)}")
            return None

    # ==================== Knowledge Hub API Methods ====================

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
            // Get KBs only if include_kbs is true
            OPTIONAL MATCH (kb:RecordGroup)
            WHERE $include_kbs = true
              AND kb.orgId = $org_id
              AND kb.connectorName = 'KB'

            // Check direct user permission
            OPTIONAL MATCH (user_doc)-[direct_perm:PERMISSION {type: 'USER'}]->(kb)

            // Check team permission
            OPTIONAL MATCH (user_doc)-[:PERMISSION {type: 'USER'}]->(team:Teams)-[:PERMISSION {type: 'TEAM'}]->(kb)

            WITH kb, user_doc, user_id,
                 (direct_perm IS NOT NULL OR team IS NOT NULL) AS has_permission
            WHERE kb IS NULL OR has_permission

            // Check for children via BELONGS_TO edges (only records, not nested RecordGroups)
            OPTIONAL MATCH (record:Record)-[:BELONGS_TO]->(kb)
            WHERE record.externalParentId IS NULL

            WITH kb, user_doc, user_id,
                 count(DISTINCT record) > 0 AS has_children

            // Determine sharing status - collect permissions
            OPTIONAL MATCH (user_perm_user:User)-[user_perm:PERMISSION {type: 'USER'}]->(kb)

            OPTIONAL MATCH ()-[team_perm:PERMISSION {type: 'TEAM'}]->(kb)

            WITH kb, has_children, user_doc, user_id,
                 (kb.createdBy = $user_key OR kb.createdBy = user_id) AS is_creator,
                 collect(DISTINCT user_perm) AS user_perms,
                 collect(DISTINCT team_perm) AS team_perms

            WITH kb, has_children, user_doc, user_id, is_creator,
                 (size(user_perms) > (CASE WHEN is_creator THEN 1 ELSE 0 END) OR size(team_perms) > 0) AS has_other_users

            WITH user_doc, user_id,
                 CASE WHEN kb IS NOT NULL
                      THEN {
                          id: kb.id,
                          name: kb.groupName,
                          nodeType: 'kb',
                          parentId: null,
                          origin: 'KB',
                          connector: 'KB',
                          createdAt: kb.createdAtTimestamp,
                          updatedAt: kb.updatedAtTimestamp,
                          webUrl: '/kb/' + kb.id,
                          hasChildren: has_children,
                          sharingStatus: CASE WHEN is_creator AND NOT has_other_users
                                              THEN 'private' ELSE 'shared' END
                      }
                      ELSE null
                 END AS kb_node

            WITH collect(kb_node) AS kb_nodes_raw
            WITH [n IN kb_nodes_raw WHERE n IS NOT NULL] AS kb_nodes

            // ==================== Get Apps ====================
            // Get apps only if include_apps is true
            OPTIONAL MATCH (app:App)
            WHERE $include_apps = true
              AND app.id IN $user_app_ids
              AND app.type <> 'KB'

            // Check for children (record groups)
            OPTIONAL MATCH (rg:RecordGroup)
            WHERE rg.connectorId = app.id

            WITH kb_nodes, app, count(rg) > 0 AS has_children

            WITH kb_nodes,
                 CASE WHEN app IS NOT NULL
                      THEN {
                          id: app.id,
                          name: app.name,
                          nodeType: 'app',
                          parentId: null,
                          origin: 'CONNECTOR',
                          connector: app.type,
                          createdAt: coalesce(app.createdAtTimestamp, 0),
                          updatedAt: coalesce(app.updatedAtTimestamp, 0),
                          webUrl: '/app/' + app.id,
                          hasChildren: has_children,
                          sharingStatus: coalesce(app.scope, 'personal')
                      }
                      ELSE null
                 END AS app_node

            WITH kb_nodes, collect(app_node) AS app_nodes_raw
            WITH kb_nodes, [n IN app_nodes_raw WHERE n IS NOT NULL] AS app_nodes
            WITH kb_nodes + app_nodes AS all_nodes

            // Apply sorting with explicit field mapping (Neo4j doesn't support dynamic property access)
            UNWIND all_nodes AS node
            WITH node,
                 CASE $sort_field
                     WHEN 'name' THEN node.name
                     WHEN 'createdAt' THEN node.createdAt
                     WHEN 'updatedAt' THEN node.updatedAt
                     WHEN 'nodeType' THEN node.nodeType
                     WHEN 'origin' THEN node.origin
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

        # Generate query based on parent type
        if parent_type == "app":
            sub_query = self._get_app_children_cypher()
            params = {
                "parent_id": parent_id,
                "org_id": org_id,
                "user_key": user_key,
                "skip": skip,
                "limit": limit,
                "sort_field": sort_field,
                "sort_dir": sort_dir.upper(),
                "only_containers": only_containers,
                "source": "CONNECTOR",
            }
        elif parent_type in ("kb", "recordGroup"):
            sub_query = self._get_record_group_children_cypher(parent_type)
            params = {
                "parent_id": parent_id,
                "org_id": org_id,
                "user_key": user_key,
                "skip": skip,
                "limit": limit,
                "sort_field": sort_field,
                "sort_dir": sort_dir.upper(),
                "only_containers": only_containers,
                "source": "KB" if parent_type == "kb" else "CONNECTOR",
            }
        elif parent_type in ("folder", "record"):
            sub_query = self._get_record_children_cypher()
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
        else:
            return {"nodes": [], "total": 0}

        # Simple query for direct children with sorting and pagination (no filters)
        query = f"""
        CALL {{
            {sub_query}
        }}

        // Apply only_containers filter
        UNWIND raw_children AS node
        WITH node WHERE
            ($only_containers IN [false, 'False', 'false'] OR $only_containers = false)
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

        result = await self.client.execute_query(query, parameters=params, txn_id=transaction)
        elapsed = time.perf_counter() - start
        self.logger.info(f"get_knowledge_hub_children finished in {elapsed * 1000} ms")
        if result and result[0].get("result"):
            return result[0]["result"]
        return {"nodes": [], "total": 0}

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
        - Nested recordGroups via INHERIT_PERMISSIONS edges (recursive)
        - Records via INHERIT_PERMISSIONS from accessible recordGroups
        - Direct user/group/org permissions on records
        """
        start = time.perf_counter()

        try:
            self.logger.info(f"🔍 Starting knowledge hub search with parent_id={parent_id}, parent_type={parent_type}, only_containers={only_containers}, search_query={search_query}, node_types={node_types}, record_types={record_types}, origins={origins}, connector_ids={connector_ids}, kb_ids={kb_ids}, indexing_status={indexing_status}, created_at={created_at}, updated_at={updated_at}, size={size}")
            # Build filter conditions using helper
            filter_conditions, filter_params = self._build_knowledge_hub_filter_conditions(
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

            # Build scope filters
            parent_connector_id = None
            # Determine parent_connector_id when parent_type is "record" or "folder"
            if parent_id and parent_type in ("record", "folder"):
                try:
                    query = "MATCH (record:Record {id: $parent_id}) RETURN record.connectorId AS connectorId"
                    result = await self.client.execute_query(query, parameters={"parent_id": parent_id}, txn_id=transaction)
                    if result and result[0].get("connectorId"):
                        parent_connector_id = result[0]["connectorId"]
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
                scope_filter_rg, scope_filter_record, scope_filter_rg_inline, scope_filter_record_inline = self._build_scope_filters_cypher(
                    parent_id, parent_type, parent_connector_id
                )

            # Build bind variables
            params = {
                "org_id": org_id,
                "user_key": user_key,
                "skip": skip,
                "limit": limit,
                "sort_field": sort_field,
                "sort_dir": sort_dir.upper(),
                "only_containers": only_containers,
            }

            # Add bind variables based on parent_type
            if parent_id:
                if parent_type in ("kb", "recordGroup"):
                    # Children-first approach: need parent_doc_id (RecordGroup ID)
                    params["parent_doc_id"] = parent_id
                elif parent_type in ("record", "folder"):
                    # Children-first approach: need parent_doc_id (Record ID)
                    params["parent_doc_id"] = parent_id
                elif parent_type == "app":
                    # App-level scope: use parent_id for scope filters
                    params["parent_id"] = parent_id
                    if parent_connector_id:
                        params["parent_connector_id"] = parent_connector_id

            # Merge filter params
            params.update(filter_params)

            # Build filter clause
            filter_clause = " AND ".join(filter_conditions) if filter_conditions else "true"

            # Get user accessible apps
            user_apps_query = """
            MATCH (u:User {id: $user_key})-[:USER_APP_RELATION]->(app:App)
            RETURN collect(app.id) AS app_ids
            """
            user_apps_result = await self.client.execute_query(user_apps_query, parameters={"user_key": user_key}, txn_id=transaction)
            user_accessible_app_ids = user_apps_result[0].get("app_ids", []) if user_apps_result else []
            params["user_accessible_app_ids"] = user_accessible_app_ids

            # Build children intersection cypher (only for kb/recordGroup/record/folder parents)
            children_intersection_cypher = self._build_children_intersection_cypher(parent_id, parent_type)

            # Build the unified query with 7 permission paths
            # Note: filter_clause and children_intersection_cypher will be inserted via string replacement
            query_template = """
            MATCH (u:User {id: $user_key})
            WITH u

            // Get user's accessible apps (already in params)
            WITH u, $user_accessible_app_ids AS user_accessible_app_ids

            // ========== RECORDGROUP-BASED ACCESS (Paths 1-4) ==========
            // Strategy: Find accessible RecordGroups, recursively find nested RecordGroups,
            // then find all Records that inherit from any of those RecordGroups

            // Path 1: User -> RecordGroup
            CALL {
                WITH u, user_accessible_app_ids
                MATCH (u)-[:PERMISSION {type: 'USER'}]->(rg:RecordGroup)
                WHERE rg.orgId = $org_id
                  AND (rg.connectorName = 'KB' OR rg.connectorId IN user_accessible_app_ids)
                  {scope_filter_rg}
                RETURN collect(rg) AS path1_rgs
            }

            // Path 2: User -> Group/Role -> RecordGroup
            CALL {
                WITH u, user_accessible_app_ids
                MATCH (u)-[:PERMISSION {type: 'USER'}]->(grp)
                WHERE grp:Group OR grp:Role
                MATCH (grp)-[:PERMISSION]->(rg:RecordGroup)
                WHERE rg.orgId = $org_id
                  AND (rg.connectorName = 'KB' OR rg.connectorId IN user_accessible_app_ids)
                  {scope_filter_rg}
                RETURN collect(rg) AS path2_rgs
            }

            // Path 3: User -> Org -> RecordGroup
            CALL {
                WITH u, user_accessible_app_ids
                MATCH (u)-[:BELONGS_TO {entityType: 'ORGANIZATION'}]->(org)
                MATCH (org)-[:PERMISSION {type: 'ORG'}]->(rg:RecordGroup)
                WHERE rg.orgId = $org_id
                  AND (rg.connectorName = 'KB' OR rg.connectorId IN user_accessible_app_ids)
                  {scope_filter_rg}
                RETURN collect(rg) AS path3_rgs
            }

            // Path 4: User -> Team -> RecordGroup
            CALL {
                WITH u, user_accessible_app_ids
                MATCH (u)-[:PERMISSION {type: 'USER'}]->(team:Teams)
                MATCH (team)-[:PERMISSION {type: 'TEAM'}]->(rg:RecordGroup)
                WHERE rg.orgId = $org_id
                  AND (rg.connectorName = 'KB' OR rg.connectorId IN user_accessible_app_ids)
                  {scope_filter_rg}
                RETURN collect(rg) AS path4_rgs
            }

            // Combine all accessible RecordGroups (parent level)
            WITH u, user_accessible_app_ids,
                 path1_rgs + path2_rgs + path3_rgs + path4_rgs AS all_parent_rgs

            // Recursively find nested RecordGroups via INHERIT_PERMISSIONS (if parent RecordGroups exist)
            WITH u, user_accessible_app_ids, all_parent_rgs

            // Find all nested RecordGroups that inherit from parent RecordGroups via INHERIT_PERMISSIONS
            // This handles multiple levels of nesting (RecordGroup -> RecordGroup -> RecordGroup...)
            CALL {
                WITH all_parent_rgs, user_accessible_app_ids
                UNWIND all_parent_rgs AS parent_rg
                MATCH (parent_rg)<-[:INHERIT_PERMISSIONS*1..5]-(rg:RecordGroup)
                WHERE rg.orgId = $org_id
                  AND (rg.connectorName = 'KB' OR rg.connectorId IN user_accessible_app_ids)
                  {scope_filter_rg}
                RETURN collect(DISTINCT rg) AS nested_rgs
            }

            // Combine parent and nested RecordGroups
            WITH u, user_accessible_app_ids,
                 all_parent_rgs + (CASE WHEN nested_rgs IS NOT NULL THEN nested_rgs ELSE [] END) AS all_accessible_rgs

            // Find all Records that inherit from both parent AND nested RecordGroups
            WITH u, user_accessible_app_ids, all_accessible_rgs,
                 [rg IN all_accessible_rgs |
                   [(record:Record)-[:INHERIT_PERMISSIONS]->(rg)
                    WHERE record.orgId = $org_id
                   | record]
                 ] AS records_lists

            // Flatten and deduplicate records from RecordGroups
            WITH u, user_accessible_app_ids,
                 all_accessible_rgs AS accessible_rgs,
                 reduce(acc = [], list IN records_lists | acc + list) AS rg_inherited_records

            // ========== DIRECT RECORD ACCESS (not via recordGroup) ==========

            // Path 5: User -> Record (direct, no recordGroup)
            CALL {
                WITH u, user_accessible_app_ids
                MATCH (u)-[:PERMISSION {type: 'USER'}]->(record:Record)
                WHERE record.orgId = $org_id
                  {scope_filter_record}

                // Check if record's connectorId points to accessible app or accessible recordGroup
                OPTIONAL MATCH (record_app:App {id: record.connectorId})
                OPTIONAL MATCH (record_rg:RecordGroup {id: record.connectorId})
                WITH record, record_app, record_rg, user_accessible_app_ids
                WHERE (record_app IS NOT NULL AND record_app.id IN user_accessible_app_ids)
                   OR (record_rg IS NOT NULL AND (record_rg.connectorName = 'KB' OR record_rg.connectorId IN user_accessible_app_ids))

                RETURN collect(record) AS user_direct_records
            }

            // Path 6: User -> Group/Role -> Record (direct, no recordGroup)
            CALL {
                WITH u, user_accessible_app_ids
                MATCH (u)-[:PERMISSION {type: 'USER'}]->(grp)
                WHERE grp:Group OR grp:Role
                MATCH (grp)-[:PERMISSION]->(record:Record)
                WHERE record.orgId = $org_id
                  {scope_filter_record}

                OPTIONAL MATCH (record_app:App {id: record.connectorId})
                OPTIONAL MATCH (record_rg:RecordGroup {id: record.connectorId})
                WITH record, record_app, record_rg, user_accessible_app_ids
                WHERE (record_app IS NOT NULL AND record_app.id IN user_accessible_app_ids)
                   OR (record_rg IS NOT NULL AND (record_rg.connectorName = 'KB' OR record_rg.connectorId IN user_accessible_app_ids))

                RETURN collect(record) AS user_group_records
            }

            // Path 7: User -> Org -> Record (direct, no recordGroup)
            CALL {
                WITH u, user_accessible_app_ids
                MATCH (u)-[:BELONGS_TO {entityType: 'ORGANIZATION'}]->(org)
                MATCH (org)-[:PERMISSION {type: 'ORG'}]->(record:Record)
                WHERE record.orgId = $org_id
                  {scope_filter_record}

                OPTIONAL MATCH (record_app:App {id: record.connectorId})
                OPTIONAL MATCH (record_rg:RecordGroup {id: record.connectorId})
                WITH record, record_app, record_rg, user_accessible_app_ids
                WHERE (record_app IS NOT NULL AND record_app.id IN user_accessible_app_ids)
                   OR (record_rg IS NOT NULL AND (record_rg.connectorName = 'KB' OR record_rg.connectorId IN user_accessible_app_ids))

                RETURN collect(record) AS user_org_records
            }

            // Combine all record sources and deduplicate
            WITH accessible_rgs, rg_inherited_records,
                 user_direct_records, user_group_records, user_org_records


            WITH accessible_rgs,
                 rg_inherited_records +
                 (CASE WHEN user_direct_records IS NOT NULL THEN user_direct_records ELSE [] END) +
                 (CASE WHEN user_group_records IS NOT NULL THEN user_group_records ELSE [] END) +
                 (CASE WHEN user_org_records IS NOT NULL THEN user_org_records ELSE [] END) AS all_records_raw

            WITH accessible_rgs, [r IN all_records_raw WHERE r IS NOT NULL] AS all_records_raw_filtered
            // Deduplicate records (matching Arango's UNION_DISTINCT) - SAFE approach without UNWIND
            WITH accessible_rgs, all_records_raw_filtered AS accessible_records

            // ========== CHILDREN TRAVERSAL & INTERSECTION (for recordGroup/kb/record/folder parents) ==========
            // If parent_type is recordGroup/kb/record/folder, traverse children and intersect with accessible nodes
            {children_intersection_cypher}

            // ========== BUILD NODES (using list comprehensions to handle empty lists) ==========
            // This approach handles empty lists gracefully without UNWIND issues
            WITH final_accessible_rgs, final_accessible_records,
                 CASE WHEN size(final_accessible_rgs) > 0 THEN final_accessible_rgs ELSE [null] END AS rgs_with_fallback,
                 CASE WHEN size(final_accessible_records) > 0 THEN final_accessible_records ELSE [null] END AS records_with_fallback

            UNWIND rgs_with_fallback AS rg_data
            WITH rg_data, final_accessible_rgs, final_accessible_records, records_with_fallback

            // Re-match the actual RecordGroup node by ID (OPTIONAL to preserve row even if rg_data is null)
            OPTIONAL MATCH (rg:RecordGroup)
            WHERE rg_data IS NOT NULL AND rg.id = rg_data.id

            WITH final_accessible_rgs, final_accessible_records, records_with_fallback,
                 collect(
                   CASE WHEN rg IS NOT NULL THEN
                     {
                       id: rg.id,
                       name: rg.groupName,
                       nodeType: CASE WHEN rg.connectorName = 'KB' OR rg.groupType = 'KB' THEN 'kb' ELSE 'recordGroup' END,
                       parentId: null,
                       origin: CASE WHEN rg.connectorName = 'KB' THEN 'KB' ELSE 'CONNECTOR' END,
                       connector: rg.connectorName,
                       connectorId: CASE WHEN rg.connectorName <> 'KB' THEN rg.connectorId ELSE null END,
                       kbId: CASE WHEN rg.connectorName = 'KB' THEN rg.id ELSE null END,
                       externalGroupId: rg.externalGroupId,
                       recordType: null,
                       recordGroupType: rg.groupType,
                       indexingStatus: null,
                       createdAt: COALESCE(rg.sourceCreatedAtTimestamp, 0),
                       updatedAt: COALESCE(rg.sourceLastModifiedTimestamp, 0),
                       sizeInBytes: null,
                       mimeType: null,
                       extension: null,
                       webUrl: rg.webUrl,
                       hasChildren: EXISTS((rg)<-[:BELONGS_TO]-(:RecordGroup)) OR EXISTS((rg)<-[:BELONGS_TO]-(:Record)),
                       previewRenderable: true
                     }
                   ELSE null END
                 ) AS rg_nodes_with_nulls

            WITH final_accessible_records, records_with_fallback,
                 [n IN rg_nodes_with_nulls WHERE n IS NOT NULL] AS rg_nodes

            // Build record nodes
            UNWIND records_with_fallback AS rec_data
            WITH rec_data, final_accessible_records, rg_nodes

            // Re-match the actual Record node by ID (OPTIONAL to preserve row even if rec_data is null)
            OPTIONAL MATCH (record:Record)
            WHERE rec_data IS NOT NULL AND record.id = rec_data.id

            OPTIONAL MATCH (record)-[:IS_OF_TYPE]->(file_info:File)
            WITH record, final_accessible_records, rg_nodes, file_info

            OPTIONAL MATCH (record)-[rr:RECORD_RELATION]->(child:Record)
            WHERE record IS NOT NULL
              AND rr.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT']
            WITH record, rg_nodes, file_info,
                 count(DISTINCT child) > 0 AS has_children

            OPTIONAL MATCH (record_connector:RecordGroup {id: record.connectorId})
            WHERE record IS NOT NULL
            OPTIONAL MATCH (record_app:App {id: record.connectorId})
            WHERE record IS NOT NULL
            WITH record, rg_nodes, file_info, has_children, record_connector, record_app,
                 CASE
                   WHEN record IS NOT NULL AND record_connector IS NOT NULL AND record_connector.connectorName = 'KB' THEN 'KB'
                   WHEN record IS NOT NULL AND record_app IS NOT NULL THEN 'CONNECTOR'
                   WHEN record IS NOT NULL THEN 'CONNECTOR'
                   ELSE null
                 END AS source

            WITH rg_nodes,
                 collect(
                   CASE WHEN record IS NOT NULL THEN
         {
           id: record.id,
           name: record.recordName,
           nodeType: CASE WHEN file_info IS NOT NULL AND file_info.isFile = false THEN 'folder' ELSE 'record' END,
           parentId: null,
           origin: source,
           connector: record.connectorName,
           connectorId: CASE WHEN source = 'CONNECTOR' THEN record.connectorId ELSE null END,
           kbId: CASE WHEN source = 'KB' THEN record.externalGroupId ELSE null END,
           externalGroupId: record.externalGroupId,
           recordType: record.recordType,
           recordGroupType: null,
           indexingStatus: record.indexingStatus,
           createdAt: COALESCE(record.sourceCreatedAtTimestamp, 0),
           updatedAt: COALESCE(record.sourceLastModifiedTimestamp, 0),
           sizeInBytes: COALESCE(record.sizeInBytes,
                                 CASE WHEN file_info IS NOT NULL THEN file_info.sizeInBytes ELSE null END),
           mimeType: record.mimeType,
           extension: CASE WHEN file_info IS NOT NULL THEN file_info.extension ELSE null END,
           webUrl: record.webUrl,
           hasChildren: has_children,
           previewRenderable: COALESCE(record.previewRenderable, true)
         }
                   ELSE null END
                 ) AS record_nodes_with_nulls

            WITH rg_nodes,
                 [n IN record_nodes_with_nulls WHERE n IS NOT NULL] AS record_nodes

            // Combine RG and record nodes
            WITH rg_nodes + record_nodes AS all_nodes

            // Apply search and filter conditions
            WITH CASE WHEN size(all_nodes) > 0 THEN all_nodes ELSE [null] END AS all_nodes_safe
            UNWIND all_nodes_safe AS node
            WITH node
            WHERE node IS NOT NULL AND ({filter_clause})

            // Apply only_containers filter
            WITH node
            WHERE (toBoolean($only_containers) = false)
               OR node.hasChildren = true
               OR node.nodeType IN ['app', 'kb', 'recordGroup', 'folder']

            // Deduplicate by node.id
            WITH node.id AS node_id, collect(node)[0] AS node

            // Sort with explicit field mapping
            WITH node,
                 CASE $sort_field
                     WHEN 'name' THEN node.name
                     WHEN 'createdAt' THEN node.createdAt
                     WHEN 'updatedAt' THEN node.updatedAt
                     WHEN 'nodeType' THEN node.nodeType
                     WHEN 'source' THEN node.origin
                     WHEN 'origin' THEN node.origin
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

            RETURN {
                nodes: sorted_nodes[$skip..($skip + $limit)],
                total: size(sorted_nodes)
            } AS result
            """

            # Replace all placeholders
            query = query_template.replace("{filter_clause}", filter_clause)
            query = query.replace("{scope_filter_rg}", scope_filter_rg)
            query = query.replace("{scope_filter_record}", scope_filter_record)
            query = query.replace("{scope_filter_rg_inline}", scope_filter_rg_inline)
            query = query.replace("{scope_filter_record_inline}", scope_filter_record_inline)
            query = query.replace("{children_intersection_cypher}", children_intersection_cypher)

            result = await self.client.execute_query(query, parameters=params, txn_id=transaction)
            elapsed = time.perf_counter() - start
            self.logger.info(f"get_knowledge_hub_search finished in {elapsed * 1000:.2f} ms")

            if result and result[0].get("result"):
                return result[0]["result"]
            return {"nodes": [], "total": 0}

        except Exception as e:
            elapsed = time.perf_counter() - start
            self.logger.error(f"Error in get_knowledge_hub_search: {str(e)}")
            self.logger.error(f"Query execution time: {elapsed * 1000:.2f} ms")
            import traceback
            self.logger.error(traceback.format_exc())
            return {"nodes": [], "total": 0}

    async def get_knowledge_hub_breadcrumbs(
        self,
        node_id: str,
        transaction: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get breadcrumb trail for a node using iterative parent lookup.

        NOTE(N+1 Queries): Uses iterative parent lookup (one query per level) because a single
        graph traversal isn't feasible here. Parent relationships are stored via multiple
        edge types: RECORD_RELATION (record->record) and BELONGS_TO (record->recordGroup,
        recordGroup->recordGroup, recordGroup->app).

        Traversal logic:
        - Records: Check RECORD_RELATION edge from another record first, then BELONGS_TO to recordGroup
        - RecordGroups: Check BELONGS_TO edge to another recordGroup, then to app (excluding KB apps)
        - Apps: No parent (root level)
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

                // Get parent for records - REFACTORED LOGIC:
                // Step 1: Check RECORD_RELATION edge from another RECORD only
                // Edge direction: parent -> child (edge from parent, to current record)
                // Use LIMIT 1 to ensure only one parent
                OPTIONAL MATCH (parent_rec:Record)-[rr:RECORD_RELATION]->(record:Record)
                WHERE record IS NOT NULL
                      AND rr IS NOT NULL
                      AND rr.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT']
                WITH node, node_type, record, rg, app, f, is_folder, head(collect(parent_rec)) AS parent_rec

                // Step 2: If no record parent, check BELONGS_TO edge to recordGroup
                // Use LIMIT 1 to ensure only one parent
                OPTIONAL MATCH (record)-[:BELONGS_TO]->(rg_parent_from_record:RecordGroup)
                WHERE record IS NOT NULL AND parent_rec IS NULL
                WITH node, node_type, record, rg, app, f, is_folder, parent_rec, head(collect(rg_parent_from_record)) AS rg_parent_from_record

                // Get parent for recordGroups - REFACTORED LOGIC:
                // Step 1: Check BELONGS_TO edge to another recordGroup (for ALL recordGroups)
                // Use LIMIT 1 to ensure only one parent
                OPTIONAL MATCH (rg)-[:BELONGS_TO]->(rg_parent:RecordGroup)
                WHERE rg IS NOT NULL
                WITH node, node_type, record, rg, app, f, is_folder, parent_rec, rg_parent_from_record, head(collect(rg_parent)) AS rg_parent

                // Step 2: If no parent recordGroup, check BELONGS_TO edge to app
                // Use LIMIT 1 to ensure only one parent (KB filter applied in CASE statement)
                OPTIONAL MATCH (rg)-[:BELONGS_TO]->(app_parent:App)
                WHERE rg IS NOT NULL
                      AND rg_parent IS NULL
                WITH node, node_type, record, rg, app, f, is_folder, parent_rec, rg_parent_from_record, rg_parent, head(collect(app_parent)) AS app_parent

                WITH node, node_type, record, rg, app, f, parent_rec, rg_parent_from_record, rg_parent, app_parent

                // Determine parent ID (matching ArangoDB logic exactly)
                // For Records:
                //   1. Check RECORD_RELATION edge from another RECORD only
                //   2. If no record parent, check BELONGS_TO edge to recordGroup
                // For RecordGroups:
                //   1. Check BELONGS_TO edge to another recordGroup
                //   2. If no parent recordGroup, check BELONGS_TO edge to app (exclude KB apps)
                // For Apps: No parent
                WITH node, node_type, record, rg, app,
                     CASE
                         // Apps have no parent
                         WHEN app IS NOT NULL THEN null

                         // Records: Step 1 - Check RECORD_RELATION parent, Step 2 - Check BELONGS_TO to recordGroup
                         WHEN record IS NOT NULL THEN CASE
                             WHEN parent_rec IS NOT NULL THEN parent_rec.id
                             WHEN rg_parent_from_record IS NOT NULL THEN rg_parent_from_record.id
                             ELSE null
                         END

                         // RecordGroups: Step 1 - Check BELONGS_TO to recordGroup, Step 2 - Check BELONGS_TO to app
                        WHEN rg IS NOT NULL THEN CASE
                            WHEN rg_parent IS NOT NULL THEN rg_parent.id
                            WHEN app_parent IS NOT NULL AND app_parent.type <> 'KB' THEN app_parent.id
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

    async def get_knowledge_hub_context_permissions(
        self,
        user_key: str,
        org_id: str,
        parent_id: Optional[str],
        transaction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get user's context-level permissions.

        Uses the generic _get_permission_role_cypher method which provides:
        - Complete permission checking (all 10 paths including role-based)
        - INHERIT_PERMISSIONS chain traversal (up to 20 levels)
        - Organization permissions

        Returns permissions with context-specific flags (canUpload, canCreateFolders, etc.)
        """
        try:
            if not parent_id:
                # Root level - check if user is admin
                query = """
                MATCH (u:User {id: $user_key})
                WITH u, (coalesce(u.role, '') = 'ADMIN' OR coalesce(u.orgRole, '') = 'ADMIN') AS is_admin
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
                # Node level - use generic permission checker
                # Generate permission calls for all node types
                record_permission_call = self._get_permission_role_cypher(
                    node_type="record",
                    node_var="record",
                    user_var="u"
                )
                rg_permission_call = self._get_permission_role_cypher(
                    node_type="kb",  # Works for both KB and RecordGroup
                    node_var="rg",
                    user_var="u"
                )
                app_permission_call = self._get_permission_role_cypher(
                    node_type="app",
                    node_var="app",
                    user_var="u"
                )

                query = f"""
                MATCH (u:User {{id: $user_key}})

                // Find the node (could be Record, App, or RecordGroup)
                OPTIONAL MATCH (record:Record {{id: $parent_id}})
                OPTIONAL MATCH (app:App {{id: $parent_id}})
                OPTIONAL MATCH (rg:RecordGroup {{id: $parent_id}})

                WITH u, record, app, rg,
                     coalesce(record, app, rg) AS node,
                     record IS NOT NULL AS is_record,
                     app IS NOT NULL AS is_app,
                     rg IS NOT NULL AS is_rg

                WHERE node IS NOT NULL

                // Get permission for Records
                CALL {{
                    WITH u, record, is_record
                    WITH u, record
                    WHERE is_record
                    {record_permission_call}
                    RETURN permission_role AS record_perm
                }}

                // Get permission for RecordGroups
                CALL {{
                    WITH u, rg, is_rg
                    WITH u, rg
                    WHERE is_rg
                    {rg_permission_call}
                    RETURN permission_role AS rg_perm
                }}

                // Get permission for Apps
                CALL {{
                    WITH u, app, is_app
                    WITH u, app
                    WHERE is_app
                    {app_permission_call}
                    RETURN permission_role AS app_perm
                }}

                WITH coalesce(record_perm, rg_perm, app_perm, 'READER') AS final_role

                RETURN {{
                    role: final_role,
                    canUpload: final_role IN ['OWNER', 'ADMIN', 'EDITOR', 'WRITER'],
                    canCreateFolders: final_role IN ['OWNER', 'ADMIN', 'EDITOR', 'WRITER'],
                    canEdit: final_role IN ['OWNER', 'ADMIN', 'EDITOR', 'WRITER'],
                    canDelete: final_role IN ['OWNER', 'ADMIN'],
                    canManagePermissions: final_role IN ['OWNER', 'ADMIN']
                }} AS result
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
            OPTIONAL MATCH (u)-[:PERMISSION {type: 'USER'}]->(team:Teams)-[:PERMISSION {type: 'TEAM'}]->(kb_team:RecordGroup)
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

    def _get_app_children_cypher(self) -> str:
        """Generate Cypher sub-query to fetch RecordGroups for an App.

        Simplified unified approach:
        - Gets ALL recordGroups connected to app via BELONGS_TO edge (KB and Connector unified)
        - Uses _get_permission_role_cypher for comprehensive permission checking (all 10 paths)
        - Returns only recordGroups where user has permission
        - Includes userRole field in results
        """
        # Get the permission role Cypher for recordGroup permission checking
        permission_role_cypher = self._get_permission_role_cypher("recordGroup", "rg", "u")

        return f"""
        MATCH (app:App {{id: $parent_id}})
        MATCH (u:User {{id: $user_key}})

        // Determine if this is a KB app
        WITH app, u, $parent_id AS parent_id, (app.type = 'KB') AS is_kb_app

        // Get all recordGroups connected to app via BELONGS_TO edge (KB and Connector unified)
        OPTIONAL MATCH (rg:RecordGroup)-[bt:BELONGS_TO]->(app)
        WHERE (is_kb_app AND rg.connectorName = 'KB')
              OR (NOT is_kb_app AND rg.connectorId = app.id)

        WITH app, u, parent_id, is_kb_app, collect(DISTINCT rg) AS all_rgs

        // For each recordGroup, check comprehensive permissions using helper
        UNWIND all_rgs AS rg
        WITH app, u, parent_id, is_kb_app, rg
        WHERE rg IS NOT NULL

        // Use comprehensive permission checking (all 10 paths)
        {permission_role_cypher}

        // Bring permission_role into scope after CALL subquery
        WITH app, u, parent_id, is_kb_app, rg, permission_role

        // Only include recordGroups where user has permission
        WHERE permission_role IS NOT NULL AND permission_role <> ''

        // Check if recordGroup has children for hasChildren flag
        OPTIONAL MATCH (rg)<-[:BELONGS_TO]-(child_rg:RecordGroup)
        WITH app, u, parent_id, is_kb_app, rg, permission_role,
             count(DISTINCT child_rg) > 0 AS has_child_rgs

        OPTIONAL MATCH (rg)<-[:BELONGS_TO]-(child_record:Record)
        WITH app, u, parent_id, is_kb_app, rg, permission_role, has_child_rgs,
             count(DISTINCT child_record) > 0 AS has_records

        // Build result nodes
        WITH collect({{
            id: rg.id,
            name: rg.groupName,
            nodeType: 'recordGroup',
            parentId: 'apps/' + parent_id,
            origin: CASE WHEN rg.connectorName = 'KB' THEN 'KB' ELSE 'CONNECTOR' END,
            connector: rg.connectorName,
            recordType: null,
            recordGroupType: rg.groupType,
            indexingStatus: null,
            createdAt: coalesce(rg.sourceCreatedAtTimestamp, 0),
            updatedAt: coalesce(rg.sourceLastModifiedTimestamp, 0),
            sizeInBytes: null,
            mimeType: null,
            extension: null,
            webUrl: rg.webUrl,
            hasChildren: has_child_rgs OR has_records,
            userRole: permission_role
        }}) AS raw_children

        RETURN raw_children
        """

    def _get_record_group_children_cypher(self, parent_type: str) -> str:
        """Generate Cypher sub-query to fetch children of a KB or RecordGroup.

        Simplified unified approach:
        - Uses BELONGS_TO edges for both KB and Connector recordGroups
        - Uses _get_permission_role_cypher for comprehensive permission checking (all 10 paths)
        - Applies permission checks to both KB and Connector children
        - Returns only children where user has permission
        - Includes userRole field in results
        - Special handling for internal recordGroups (fetches all records with permission check)
        """
        # Get the permission role Cypher for recordGroups and records
        rg_permission_role_cypher = self._get_permission_role_cypher("recordGroup", "node", "u")
        record_permission_role_cypher = self._get_permission_role_cypher("record", "record", "u")

        return f"""
        MATCH (rg:RecordGroup {{id: $parent_id}})
        MATCH (u:User {{id: $user_key}})

        WITH rg, u, $parent_id AS parent_id, $org_id AS org_id, (rg.connectorName = 'KB') AS is_kb_rg,
             coalesce(rg.isInternal, false) AS is_internal

        // ============================================
        // SPECIAL CASE: Internal RecordGroups
        // ============================================
        // If internal, get all records with permission checks (no nested recordGroups)
        CALL {{
            WITH rg, u, parent_id, org_id, is_kb_rg, is_internal
            WITH rg, u, parent_id, org_id, is_kb_rg, is_internal
            WHERE is_internal = true

            OPTIONAL MATCH (rg)<-[:BELONGS_TO]-(internal_record:Record)
            WHERE internal_record.orgId = org_id

            WITH collect(DISTINCT internal_record) AS internal_records_raw, u, parent_id

            UNWIND internal_records_raw AS record
            WITH record, u, parent_id
            WHERE record IS NOT NULL

            // Use comprehensive permission checking (all 10 paths)
            {record_permission_role_cypher}

            // Bring permission_role into scope after CALL subquery
            WITH record, u, parent_id, permission_role

            // Only include records where user has permission
            WHERE permission_role IS NOT NULL AND permission_role <> ''

            // Get file info for folder detection
            OPTIONAL MATCH (record)-[:IS_OF_TYPE]->(file_info:File)
            WITH record, parent_id, permission_role, file_info,
                 CASE WHEN file_info IS NOT NULL AND file_info.isFile = false THEN true ELSE false END AS is_folder

            // Simple hasChildren check
            OPTIONAL MATCH (record)-[rr:RECORD_RELATION]->(child:Record)
            WHERE rr.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT']
            AND child IS NOT NULL
            WITH record, parent_id, permission_role, file_info, is_folder,
                 count(DISTINCT child) > 0 AS has_children

            RETURN collect({{
                id: record.id,
                name: record.recordName,
                nodeType: CASE WHEN is_folder THEN 'folder' ELSE 'record' END,
                parentId: 'recordGroups/' + parent_id,
                origin: CASE WHEN record.connectorName = 'KB' THEN 'KB' ELSE 'CONNECTOR' END,
                connector: record.connectorName,
                connectorId: CASE WHEN record.connectorName <> 'KB' THEN record.connectorId ELSE null END,
                kbId: CASE WHEN record.connectorName = 'KB' THEN record.externalGroupId ELSE null END,
                externalGroupId: record.externalGroupId,
                recordType: record.recordType,
                recordGroupType: null,
                indexingStatus: record.indexingStatus,
                createdAt: coalesce(record.sourceCreatedAtTimestamp, record.createdAtTimestamp, 0),
                updatedAt: coalesce(record.sourceLastModifiedTimestamp, record.updatedAtTimestamp, 0),
                sizeInBytes: coalesce(record.sizeInBytes, file_info.fileSizeInBytes),
                mimeType: record.mimeType,
                extension: file_info.extension,
                webUrl: record.webUrl,
                hasChildren: has_children,
                previewRenderable: coalesce(record.previewRenderable, true),
                userRole: permission_role
            }}) AS internal_records
        }}

        WITH rg, u, parent_id, org_id, is_kb_rg, is_internal,
             coalesce(internal_records, []) AS internal_records

        // ============================================
        // NORMAL CASE: Child RecordGroups
        // ============================================
        // Get child recordGroups via BELONGS_TO (skip if internal)
        CALL {{
            WITH rg, u, parent_id, org_id, is_kb_rg, is_internal
            WITH rg, u, parent_id, org_id, is_kb_rg, is_internal
            WHERE is_internal = false

            OPTIONAL MATCH (child_rg:RecordGroup)-[:BELONGS_TO]->(rg)
            WHERE ((is_kb_rg AND child_rg.connectorName = 'KB' AND child_rg.orgId = org_id)
                   OR (NOT is_kb_rg AND child_rg.connectorId = rg.connectorId))

            WITH collect(DISTINCT child_rg) AS all_nested_rgs_raw, u, parent_id
            WITH [x IN all_nested_rgs_raw WHERE x IS NOT NULL] AS all_nested_rgs, u, parent_id

            UNWIND all_nested_rgs AS node
            WITH node, u, parent_id
            WHERE node IS NOT NULL

            // Use comprehensive permission checking (all 10 paths)
            {rg_permission_role_cypher}

            // Bring permission_role into scope after CALL subquery
            WITH node, u, parent_id, permission_role

            // Only include recordGroups where user has permission
            WHERE permission_role IS NOT NULL AND permission_role <> ''

            // Check if recordGroup has children
            OPTIONAL MATCH (node)<-[:BELONGS_TO]-(child_rg_check:RecordGroup)
            WITH node, parent_id, permission_role,
                 count(DISTINCT child_rg_check) > 0 AS has_child_rgs

            OPTIONAL MATCH (node)<-[:BELONGS_TO]-(child_record_check:Record)
            WITH node, parent_id, permission_role, has_child_rgs,
                 count(DISTINCT child_record_check) > 0 AS has_records

            RETURN collect({{
                id: node.id,
                name: node.groupName,
                nodeType: 'recordGroup',
                parentId: 'recordGroups/' + parent_id,
                origin: CASE WHEN node.connectorName = 'KB' THEN 'KB' ELSE 'CONNECTOR' END,
                connector: node.connectorName,
                connectorId: CASE WHEN node.connectorName <> 'KB' THEN node.connectorId ELSE null END,
                kbId: CASE WHEN node.connectorName = 'KB' THEN parent_id ELSE null END,
                externalGroupId: node.externalGroupId,
                recordType: null,
                recordGroupType: node.groupType,
                indexingStatus: null,
                createdAt: coalesce(node.sourceCreatedAtTimestamp, node.createdAtTimestamp, 0),
                updatedAt: coalesce(node.sourceLastModifiedTimestamp, node.updatedAtTimestamp, 0),
                sizeInBytes: null,
                mimeType: null,
                extension: null,
                webUrl: node.webUrl,
                hasChildren: has_child_rgs OR has_records,
                userRole: permission_role
            }}) AS child_rgs
        }}

        WITH rg, u, parent_id, org_id, is_kb_rg, is_internal, internal_records,
             coalesce(child_rgs, []) AS child_rgs

        // ============================================
        // NORMAL CASE: Direct Child Records
        // ============================================
        // Get direct child records via BELONGS_TO (skip if internal)
        CALL {{
            WITH rg, u, parent_id, org_id, is_kb_rg, is_internal
            WITH rg, u, parent_id, org_id, is_kb_rg, is_internal
            WHERE is_internal = false

            OPTIONAL MATCH (record:Record)-[:BELONGS_TO]->(rg)
            WHERE record.orgId = org_id
                  AND record.externalParentId IS NULL

            WITH collect(DISTINCT record) AS all_direct_records, u, parent_id

            UNWIND all_direct_records AS record
            WITH record, u, parent_id
            WHERE record IS NOT NULL

            // Use comprehensive permission checking (all 10 paths)
            {record_permission_role_cypher}

            // Bring permission_role into scope after CALL subquery
            WITH record, u, parent_id, permission_role

            // Only include records where user has permission
            WHERE permission_role IS NOT NULL AND permission_role <> ''

            // Get file info for folder detection
            OPTIONAL MATCH (record)-[:IS_OF_TYPE]->(file_info:File)
            WITH record, parent_id, permission_role, file_info,
                 CASE WHEN file_info IS NOT NULL AND file_info.isFile = false THEN true ELSE false END AS is_folder

            // Simple hasChildren check
            OPTIONAL MATCH (record)-[rr:RECORD_RELATION]->(child:Record)
            WHERE rr.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT']
            AND child IS NOT NULL
            WITH record, parent_id, permission_role, file_info, is_folder,
                 count(DISTINCT child) > 0 AS has_children

            RETURN collect({{
                id: record.id,
                name: record.recordName,
                nodeType: CASE WHEN is_folder THEN 'folder' ELSE 'record' END,
                parentId: 'recordGroups/' + parent_id,
                origin: CASE WHEN record.connectorName = 'KB' THEN 'KB' ELSE 'CONNECTOR' END,
                connector: record.connectorName,
                connectorId: CASE WHEN record.connectorName <> 'KB' THEN record.connectorId ELSE null END,
                kbId: CASE WHEN record.connectorName = 'KB' THEN record.externalGroupId ELSE null END,
                externalGroupId: record.externalGroupId,
                recordType: record.recordType,
                recordGroupType: null,
                indexingStatus: record.indexingStatus,
                createdAt: coalesce(record.sourceCreatedAtTimestamp, record.createdAtTimestamp, 0),
                updatedAt: coalesce(record.sourceLastModifiedTimestamp, record.updatedAtTimestamp, 0),
                sizeInBytes: coalesce(record.sizeInBytes, file_info.fileSizeInBytes),
                mimeType: record.mimeType,
                extension: file_info.extension,
                webUrl: record.webUrl,
                hasChildren: has_children,
                previewRenderable: coalesce(record.previewRenderable, true),
                userRole: permission_role
            }}) AS direct_records
        }}

        WITH rg, u, parent_id, org_id, is_kb_rg, is_internal, internal_records, child_rgs,
             coalesce(direct_records, []) AS direct_records

        // Combine results: if internal, return only internal_records, otherwise combine child_rgs and direct_records
        WITH CASE WHEN is_internal = true THEN internal_records ELSE child_rgs + direct_records END AS raw_children

        RETURN raw_children
        """

    def _get_record_children_cypher(self) -> str:
        """Generate Cypher sub-query to fetch children of a Folder/Record.

        Simplified unified approach:
        - Uses RECORD_RELATION edge with relationshipType filter (PARENT_CHILD, ATTACHMENT)
        - Uses _get_permission_role_cypher for comprehensive permission checking (all 10 paths)
        - Applies permission checks to both KB and Connector records
        - Returns only children where user has permission
        - Includes userRole field in results
        - Simplified hasChildren calculation (no permission filtering on grandchildren)
        """
        # Get the permission role Cypher for record permission checking
        permission_role_cypher = self._get_permission_role_cypher("record", "record", "u")

        return f"""
        MATCH (parent_record:Record {{id: $parent_id}})
        MATCH (u:User {{id: $user_key}})

        WITH parent_record, u, $parent_id AS parent_id, $org_id AS org_id

        // Get children via RECORD_RELATION (direction: parent -> child)
        OPTIONAL MATCH (parent_record)-[rr:RECORD_RELATION]->(record:Record)
        WHERE rr.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT']

        WITH parent_record, u, parent_id, org_id, collect(DISTINCT record) AS all_children

        // For each record, check comprehensive permissions using helper
        UNWIND all_children AS record
        WITH parent_record, u, parent_id, org_id, record
        WHERE record IS NOT NULL
              AND record.orgId = org_id

        // Use comprehensive permission checking (all 10 paths)
        {permission_role_cypher}

        // Bring permission_role into scope after CALL subquery
        WITH parent_record, u, parent_id, org_id, record, permission_role

        // Only include records where user has permission
        WHERE permission_role IS NOT NULL AND permission_role <> ''

        // Get file info for folder detection
        OPTIONAL MATCH (record)-[:IS_OF_TYPE]->(file_info:File)
        WITH parent_record, u, parent_id, record, permission_role, file_info,
             CASE WHEN file_info IS NOT NULL AND file_info.isFile = false THEN true ELSE false END AS is_folder

        // Simple hasChildren check (no permission filtering on grandchildren)
        OPTIONAL MATCH (record)-[rr:RECORD_RELATION]->(child:Record)
        WHERE rr.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT']
          AND child IS NOT NULL
        WITH parent_record, u, parent_id, record, permission_role, file_info, is_folder,
             count(DISTINCT child) > 0 AS has_children

        // Build result nodes
        WITH collect({{
            id: record.id,
            name: record.recordName,
            nodeType: CASE WHEN is_folder THEN 'folder' ELSE 'record' END,
            parentId: 'records/' + parent_id,
            origin: CASE WHEN record.connectorName = 'KB' THEN 'KB' ELSE 'CONNECTOR' END,
            connector: record.connectorName,
            connectorId: CASE WHEN record.connectorName <> 'KB' THEN record.connectorId ELSE null END,
            kbId: CASE WHEN record.connectorName = 'KB' THEN record.externalGroupId ELSE null END,
            externalGroupId: record.externalGroupId,
            recordType: record.recordType,
            recordGroupType: null,
            indexingStatus: record.indexingStatus,
            createdAt: coalesce(record.sourceCreatedAtTimestamp, 0),
            updatedAt: coalesce(record.sourceLastModifiedTimestamp, 0),
            sizeInBytes: coalesce(record.sizeInBytes, file_info.fileSizeInBytes),
            mimeType: record.mimeType,
            extension: file_info.extension,
            webUrl: record.webUrl,
            hasChildren: has_children,
            previewRenderable: coalesce(record.previewRenderable, true),
            userRole: permission_role
        }}) AS raw_children

        RETURN raw_children
        """

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

        # Record-specific filters - only apply to record/folder nodes
        if record_types:
            filter_params["record_types"] = record_types
            filter_conditions.append("(node.nodeType = 'record' AND node.recordType IS NOT NULL AND node.recordType IN $record_types)")

        if indexing_status:
            filter_params["indexing_status"] = indexing_status
            filter_conditions.append("(node.nodeType = 'record' AND node.indexingStatus IS NOT NULL AND node.indexingStatus IN $indexing_status)")

        if created_at:
            if created_at.get("gte"):
                filter_params["created_at_gte"] = created_at["gte"]
                filter_conditions.append("node.createdAt >= $created_at_gte")
            if created_at.get("lte"):
                filter_params["created_at_lte"] = created_at["lte"]
                filter_conditions.append("node.createdAt <= $created_at_lte")

        if updated_at:
            if updated_at.get("gte"):
                filter_params["updated_at_gte"] = updated_at["gte"]
                filter_conditions.append("node.updatedAt >= $updated_at_gte")
            if updated_at.get("lte"):
                filter_params["updated_at_lte"] = updated_at["lte"]
                filter_conditions.append("node.updatedAt <= $updated_at_lte")

        if size:
            if size.get("gte"):
                filter_params["size_gte"] = size["gte"]
                filter_conditions.append("(node.sizeInBytes IS NULL OR node.sizeInBytes >= $size_gte)")
            if size.get("lte"):
                filter_params["size_lte"] = size["lte"]
                filter_conditions.append("(node.sizeInBytes IS NULL OR node.sizeInBytes <= $size_lte)")

        if origins:
            filter_params["origins"] = origins
            filter_conditions.append("node.origin IN $origins")

        if connector_ids and kb_ids:
            filter_params["connector_ids"] = connector_ids
            filter_params["kb_ids"] = kb_ids
            filter_conditions.append(
                "((node.nodeType = 'app' AND node.id IN $connector_ids) OR (node.connectorId IN $connector_ids) OR "
                "(node.nodeType = 'kb' AND node.id IN $kb_ids) OR (node.kbId IN $kb_ids))"
            )
        elif connector_ids:
            filter_params["connector_ids"] = connector_ids
            filter_conditions.append("((node.nodeType = 'app' AND node.id IN $connector_ids) OR (node.connectorId IN $connector_ids))")
        elif kb_ids:
            filter_params["kb_ids"] = kb_ids
            filter_conditions.append("((node.nodeType = 'kb' AND node.id IN $kb_ids) OR (node.kbId IN $kb_ids))")

        # Add search condition to filter conditions if present
        if search_query:
            filter_conditions.insert(0, "toLower(node.name) CONTAINS $search_query")

        return filter_conditions, filter_params

    def _get_permission_role_cypher(
        self,
        node_type: str,
        node_var: str = "node",
        user_var: str = "u",
    ) -> str:
        """
        Generate a CALL subquery that returns the user's highest permission role on a node.

        This function generates a reusable Cypher CALL block that checks all permission paths
        and returns the highest priority role for the user on the specified node.

        Args:
            node_type: Type of node - 'record', 'recordGroup', 'app', or 'kb'
            node_var: Variable name of the node in the outer query (default: 'node')
            user_var: Variable name of the user in the outer query (default: 'u')

        Required outer query parameters:
            - $org_id: Organization ID parameter

        Returns:
            Cypher CALL block string that yields: permission_role (string)

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

        The permission model is the same for KB and connector - no special handling.
        Highest priority role wins: OWNER > ADMIN > EDITOR > WRITER > COMMENTER > READER

        Usage example:
            permission_call = self._get_permission_role_cypher(
                node_type="record",
                node_var="record",
                user_var="u"
            )
            query = f'''
            MATCH (record:Record {{id: $record_id}})
            MATCH (u:User {{id: $user_key}})
            {permission_call}
            RETURN record, permission_role
            '''
        """
        # Role priority map used for determining highest role
        role_priority_map = "{OWNER: 6, ADMIN: 5, EDITOR: 4, WRITER: 3, COMMENTER: 2, READER: 1}"

        if node_type == "record":
            return self._get_record_permission_role_cypher(node_var, user_var, role_priority_map)
        elif node_type in ("recordGroup", "kb"):
            # KB is a RecordGroup at root level, same permission logic applies
            # INHERIT_PERMISSIONS query works for both - KB just won't have ancestors
            return self._get_record_group_permission_role_cypher(node_var, user_var, role_priority_map)
        elif node_type == "app":
            return self._get_app_permission_role_cypher(node_var, user_var, role_priority_map)
        else:
            raise ValueError(f"Unsupported node_type: {node_type}. Must be 'record', 'recordGroup', 'app', or 'kb'")

    def _get_record_permission_role_cypher(
        self,
        node_var: str,
        user_var: str,
        role_priority_map: str,
    ) -> str:
        """
        Generate CALL subquery for Record permission role.

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
        CALL {{
            WITH {node_var}, {user_var}

            // Role priority map
            WITH {node_var}, {user_var}, {role_priority_map} AS role_priority

            // Step 1: Get all permission targets (record + ancestor RGs via INHERIT_PERMISSIONS)
            // The record itself is a permission target
            // Plus all RecordGroups reachable via INHERIT_PERMISSIONS chain
            OPTIONAL MATCH ({node_var})-[:INHERIT_PERMISSIONS*1..20]->(ancestor_rg:RecordGroup)

            WITH {node_var}, {user_var}, role_priority,
                 [{node_var}] + collect(DISTINCT ancestor_rg) AS permission_targets_raw

            // Filter out nulls
            WITH {node_var}, {user_var}, role_priority,
                 [t IN permission_targets_raw WHERE t IS NOT NULL] AS permission_targets

            // Step 2: Check all 10 permission paths across all targets
            // Unwind targets to check each one
            UNWIND permission_targets AS target

            // Path 1: Direct user permission on target
            OPTIONAL MATCH ({user_var})-[p1:PERMISSION {{type: 'USER'}}]->(target)
            WHERE p1.role IS NOT NULL AND p1.role <> ''

            // Path 3: User -> Group -> target
            OPTIONAL MATCH ({user_var})-[ug:PERMISSION {{type: 'USER'}}]->(grp:Group)-[p3:PERMISSION]->(target)
            WHERE p3.role IS NOT NULL AND p3.role <> ''

            // Path 5: User -> Role -> target
            OPTIONAL MATCH ({user_var})-[ur:PERMISSION {{type: 'USER'}}]->(role:Role)-[p5:PERMISSION]->(target)
            WHERE p5.role IS NOT NULL AND p5.role <> ''

            // Path 7: User -> Team -> target
            // Use role from User->Team permission edge (ut.role), not Team->target edge
            OPTIONAL MATCH ({user_var})-[ut:PERMISSION {{type: 'USER'}}]->(team:Teams)-[p7:PERMISSION {{type: 'TEAM'}}]->(target)
            WHERE ut.role IS NOT NULL AND ut.role <> ''

            // Path 9: User -> Org -> target
            OPTIONAL MATCH ({user_var})-[:BELONGS_TO {{entityType: 'ORGANIZATION'}}]->(org:Organization)-[p9:PERMISSION {{type: 'ORG'}}]->(target)
            WHERE p9.role IS NOT NULL AND p9.role <> ''

            // Collect all found permissions for this target
            WITH {node_var}, {user_var}, role_priority, permission_targets,
                 collect(DISTINCT p1.role) + collect(DISTINCT p3.role) + collect(DISTINCT p5.role) +
                 collect(DISTINCT ut.role) + collect(DISTINCT p9.role) AS target_roles

            // Flatten all roles across all targets
            WITH role_priority, target_roles
            UNWIND target_roles AS found_role

            // Filter valid roles and map to priorities
            WITH role_priority, found_role
            WHERE found_role IS NOT NULL AND found_role <> ''

            WITH role_priority, found_role, role_priority[toString(found_role)] AS priority
            WHERE priority IS NOT NULL

            // Get highest priority role
            WITH found_role, priority
            ORDER BY priority DESC
            LIMIT 1

            RETURN found_role AS permission_role
        }}
        """

    def _get_record_group_permission_role_cypher(
        self,
        node_var: str,
        user_var: str,
        role_priority_map: str,
    ) -> str:
        """
        Generate CALL subquery for RecordGroup/KB permission role.

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
        CALL {{
            WITH {node_var}, {user_var}

            // Role priority map
            WITH {node_var}, {user_var}, {role_priority_map} AS role_priority

            // Step 1: Get all permission targets (this RG + ancestor RGs via INHERIT_PERMISSIONS)
            OPTIONAL MATCH ({node_var})-[:INHERIT_PERMISSIONS*1..20]->(ancestor_rg:RecordGroup)

            WITH {node_var}, {user_var}, role_priority,
                 [{node_var}] + collect(DISTINCT ancestor_rg) AS permission_targets_raw

            // Filter out nulls
            WITH {node_var}, {user_var}, role_priority,
                 [t IN permission_targets_raw WHERE t IS NOT NULL] AS permission_targets

            // Step 2: Check all permission paths across all targets
            UNWIND permission_targets AS target

            // Path 1: Direct user permission on target
            OPTIONAL MATCH ({user_var})-[p1:PERMISSION {{type: 'USER'}}]->(target)
            WHERE p1.role IS NOT NULL AND p1.role <> ''

            // Path 2: User -> Group -> target
            OPTIONAL MATCH ({user_var})-[ug:PERMISSION {{type: 'USER'}}]->(grp:Group)-[p3:PERMISSION]->(target)
            WHERE p3.role IS NOT NULL AND p3.role <> ''

            // Path 3: User -> Role -> target
            OPTIONAL MATCH ({user_var})-[ur:PERMISSION {{type: 'USER'}}]->(role:Role)-[p5:PERMISSION]->(target)
            WHERE p5.role IS NOT NULL AND p5.role <> ''

            // Path 4: User -> Team -> target
            // Use role from User->Team permission edge (ut.role), not Team->target edge
            OPTIONAL MATCH ({user_var})-[ut:PERMISSION {{type: 'USER'}}]->(team:Teams)-[p7:PERMISSION {{type: 'TEAM'}}]->(target)
            WHERE ut.role IS NOT NULL AND ut.role <> ''

            // Path 5: User -> Org -> target
            OPTIONAL MATCH ({user_var})-[:BELONGS_TO {{entityType: 'ORGANIZATION'}}]->(org:Organization)-[p9:PERMISSION {{type: 'ORG'}}]->(target)
            WHERE p9.role IS NOT NULL AND p9.role <> ''

            // Collect all found permissions for this target
            WITH {node_var}, {user_var}, role_priority, permission_targets,
                 collect(DISTINCT p1.role) + collect(DISTINCT p3.role) + collect(DISTINCT p5.role) +
                 collect(DISTINCT ut.role) + collect(DISTINCT p9.role) AS target_roles

            // Flatten all roles across all targets
            WITH role_priority, target_roles
            UNWIND target_roles AS found_role

            // Filter valid roles and map to priorities
            WITH role_priority, found_role
            WHERE found_role IS NOT NULL AND found_role <> ''

            WITH role_priority, found_role, role_priority[toString(found_role)] AS priority
            WHERE priority IS NOT NULL

            // Get highest priority role
            WITH found_role, priority
            ORDER BY priority DESC
            LIMIT 1

            RETURN found_role AS permission_role
        }}
        """

    def _get_app_permission_role_cypher(
        self,
        node_var: str,
        user_var: str,
        role_priority_map: str,
    ) -> str:
        """
        Generate CALL subquery for App permission role.

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
        CALL {{
            WITH {node_var}, {user_var}

            // Check if user has USER_APP_RELATION to app
            OPTIONAL MATCH ({user_var})-[user_app_rel:USER_APP_RELATION]->({node_var})

            // Check if user is admin
            WITH {node_var}, {user_var}, user_app_rel,
                (coalesce({user_var}.role, '') = 'ADMIN' OR coalesce({user_var}.orgRole, '') = 'ADMIN') AS is_admin

            // Get app scope and check if user is creator
            // createdBy stores MongoDB userId, so compare with user.userId (not user.id)
            WITH {node_var}, {user_var}, user_app_rel, is_admin,
                coalesce({node_var}.scope, 'personal') AS app_scope,
                ({node_var}.createdBy = {user_var}.userId OR {node_var}.createdBy = {user_var}.id) AS is_creator

            // Determine role based on conditions
            RETURN CASE
                // If no USER_APP_RELATION, no access
                WHEN user_app_rel IS NULL THEN null

                // Admin users: EDITOR for team apps, OWNER for personal apps
                WHEN is_admin AND app_scope = 'team' THEN 'EDITOR'
                WHEN is_admin AND app_scope = 'personal' THEN 'OWNER'

                // Team app creator gets OWNER role
                WHEN app_scope = 'team' AND is_creator THEN 'OWNER'

                // Default: READER for regular users with USER_APP_RELATION
                ELSE 'READER'
            END AS permission_role
        }}
        """

    def _build_scope_filters_cypher(
        self,
        parent_id: Optional[str],
        parent_type: Optional[str],
        parent_connector_id: Optional[str] = None
    ) -> Tuple[str, str, str, str]:
        """
        Generate Cypher scope filter conditions based on parent_id and parent_type.

        Returns tuple of:
        - scope_filter_rg: WHERE clause for RecordGroup filtering (for use in MATCH/WHERE)
        - scope_filter_record: WHERE clause for Record filtering (for use in MATCH/WHERE)
        - scope_filter_rg_inline: Inline condition for RecordGroups (for use in FILTER expressions with inherited_node)
        - scope_filter_record_inline: Inline condition for Records (for use in FILTER expressions with inherited_node)

        Args:
            parent_id: Optional parent node ID for scoped search
            parent_type: Optional type of parent: 'app', 'kb', 'recordGroup', 'folder', 'record'
            parent_connector_id: Optional connector ID of parent (needed for record/folder types)
        """
        if not parent_id or not parent_type:
            # Global search - no scope filters
            return ("", "", "true", "true")

        if parent_type == "app":
            # Filter RecordGroups by connectorId
            scope_filter_rg = "AND rg.connectorId = $parent_id"
            scope_filter_rg_inline = "inherited_node.connectorId = $parent_id"
            # Records inherit scope from their RecordGroups, so filter by connectorId
            scope_filter_record = "AND record.connectorId = $parent_id"
            scope_filter_record_inline = "inherited_node.connectorId = $parent_id"
        elif parent_type in ("kb", "recordGroup"):
            # Filter RecordGroups by hierarchy via BELONGS_TO or parentId
            # For KB: use BELONGS_TO edges
            # For connector: use parentId or BELONGS_TO
            scope_filter_rg = """AND (
                rg.parentId = $parent_id
                OR EXISTS((rg)-[:BELONGS_TO]->(:RecordGroup {id: $parent_id}))
            )"""
            scope_filter_rg_inline = """(
                inherited_node.parentId = $parent_id
                OR EXISTS((inherited_node)-[:BELONGS_TO]->(:RecordGroup {id: $parent_id}))
            )"""
            # Records belong to RecordGroups, so filter by parent RecordGroup hierarchy
            scope_filter_record = """AND (
                EXISTS((record)-[:BELONGS_TO]->(:RecordGroup {id: $parent_id}))
                OR EXISTS((record)-[:BELONGS_TO]->(:RecordGroup)-[:BELONGS_TO*]->(:RecordGroup {id: $parent_id}))
                OR EXISTS((record)-[:INHERIT_PERMISSIONS*]->(:RecordGroup {id: $parent_id}))
            )"""
            scope_filter_record_inline = """(
                EXISTS((inherited_node)-[:BELONGS_TO]->(:RecordGroup {id: $parent_id}))
                OR EXISTS((inherited_node)-[:BELONGS_TO]->(:RecordGroup)-[:BELONGS_TO*]->(:RecordGroup {id: $parent_id}))
                OR EXISTS((inherited_node)-[:INHERIT_PERMISSIONS*]->(:RecordGroup {id: $parent_id}))
            )"""
        elif parent_type in ("record", "folder"):
            # Filter by same connector (parent_connector_id)
            if parent_connector_id:
                scope_filter_rg = "AND rg.connectorId = $parent_connector_id"
                scope_filter_rg_inline = "inherited_node.connectorId = $parent_connector_id"
                scope_filter_record = "AND record.connectorId = $parent_connector_id"
                scope_filter_record_inline = "inherited_node.connectorId = $parent_connector_id"
            else:
                # No connector ID available - can't scope properly
                scope_filter_rg = ""
                scope_filter_rg_inline = "true"
                scope_filter_record = ""
                scope_filter_record_inline = "true"
        else:
            # Unknown parent type - no scope filters
            return ("", "", "true", "true")

        return (scope_filter_rg, scope_filter_record, scope_filter_rg_inline, scope_filter_record_inline)

    def _build_children_intersection_cypher(
        self,
        parent_id: Optional[str],
        parent_type: Optional[str]
    ) -> str:
        """
        Generate Cypher subquery for children-first traversal and intersection.

        When parent_type is kb/recordGroup/record/folder, this generates Cypher that:
        1. Traverses children from the parent node
        2. Intersects found children with accessible nodes
        3. Returns final_accessible_rgs and final_accessible_records

        For global search (no parent), simply passes through accessible nodes unchanged.

        Args:
            parent_id: Optional parent node ID
            parent_type: Optional type of parent ('kb', 'recordGroup', 'record', 'folder')

        Returns:
            Cypher string to insert into the main query
        """
        if not parent_id or parent_type not in ("kb", "recordGroup", "record", "folder"):
            # No children intersection needed - use accessible nodes as-is
            return """
            // No children intersection - use accessible nodes directly
            WITH accessible_rgs AS final_accessible_rgs,
                 accessible_records AS final_accessible_records
            """

        if parent_type in ("kb", "recordGroup"):
            # For KB/RecordGroup: traverse INHERIT_PERMISSIONS to find all children
            return """
            // ========== CHILDREN TRAVERSAL & INTERSECTION (kb/recordGroup parent) ==========
            // Get parent RecordGroup
            MATCH (parent_rg:RecordGroup {id: $parent_doc_id})

            // Find all child RecordGroups via INHERIT_PERMISSIONS (recursive)
            OPTIONAL MATCH (child_rg:RecordGroup)-[:INHERIT_PERMISSIONS*1..100]->(parent_rg)
            WHERE child_rg.orgId = $org_id
            WITH accessible_rgs, accessible_records, parent_rg,
                 collect(DISTINCT child_rg) AS all_child_rgs

            // Find all child Records via INHERIT_PERMISSIONS (recursive)
            OPTIONAL MATCH (child_record:Record)-[:INHERIT_PERMISSIONS*1..100]->(parent_rg)
            WHERE child_record.orgId = $org_id
            WITH accessible_rgs, accessible_records, parent_rg, all_child_rgs,
                 collect(DISTINCT child_record) AS inherited_child_records

            // Also find records directly belonging to parent via BELONGS_TO
            OPTIONAL MATCH (direct_record:Record)-[:BELONGS_TO]->(parent_rg)
            WHERE direct_record.orgId = $org_id
            WITH accessible_rgs, accessible_records, all_child_rgs, inherited_child_records,
                 collect(DISTINCT direct_record) AS direct_child_records

            // Combine all child records
            WITH accessible_rgs, accessible_records, all_child_rgs,
                 [r IN inherited_child_records + direct_child_records WHERE r IS NOT NULL] AS all_child_records

            // Intersect children with accessible nodes
            // final_accessible_rgs = child_rgs that are in accessible_rgs
            WITH accessible_rgs, accessible_records, all_child_rgs, all_child_records,
                 [rg IN all_child_rgs WHERE rg IN accessible_rgs] AS final_accessible_rgs_list

            // final_accessible_records = child_records that are in accessible_records
            WITH accessible_rgs, accessible_records, final_accessible_rgs_list,
                 [r IN all_child_records WHERE r IN accessible_records] AS final_accessible_records_list

            // Convert to final format
            WITH [rg IN final_accessible_rgs_list WHERE rg IS NOT NULL] AS final_accessible_rgs,
                 [r IN final_accessible_records_list WHERE r IS NOT NULL] AS final_accessible_records
            """

        elif parent_type in ("record", "folder"):
            # For Record/Folder: traverse RECORD_RELATION to find child records
            return """
            // ========== CHILDREN TRAVERSAL & INTERSECTION (record/folder parent) ==========
            // Get parent Record
            MATCH (parent_record:Record {id: $parent_doc_id})

            // Find all child Records via RECORD_RELATION (recursive)
            OPTIONAL MATCH (parent_record)-[rr:RECORD_RELATION*1..100]->(child_record:Record)
            WHERE child_record.orgId = $org_id
              AND ALL(rel IN rr WHERE rel.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT'])
            WITH accessible_rgs, accessible_records, parent_record,
                 collect(DISTINCT child_record) AS all_child_records

            // For record/folder parent, no child RecordGroups
            WITH accessible_rgs, accessible_records, all_child_records,
                 [] AS all_child_rgs

            // Intersect children with accessible nodes
            // final_accessible_rgs = empty for record/folder parent (no RecordGroups should be returned)
            WITH accessible_rgs, accessible_records, all_child_records,
                 [] AS final_accessible_rgs

            // final_accessible_records = child_records that are in accessible_records
            WITH final_accessible_rgs, all_child_records, accessible_records,
                 [r IN all_child_records WHERE r IN accessible_records] AS final_accessible_records_list

            // Convert to final format
            WITH final_accessible_rgs,
                 [r IN final_accessible_records_list WHERE r IS NOT NULL] AS final_accessible_records
            """


    # ==================== Team Operations ====================

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
        """
        try:
            offset = (page - 1) * limit
            team_label = collection_to_label(CollectionNames.TEAMS.value)
            user_label = collection_to_label(CollectionNames.USERS.value)
            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)

            # Build search filter (case-insensitive)
            search_where = ""
            if search:
                search_where = "AND toLower(team.name) CONTAINS toLower($search)"

            # Query to get teams with current user's permission and team members
            teams_query = f"""
            MATCH (team:{team_label} {{orgId: $orgId}})
            WHERE 1=1 {search_where}
            OPTIONAL MATCH (current_user:{user_label} {{id: $user_key}})-[current_permission:{permission_rel}]->(team)
            OPTIONAL MATCH (member_user:{user_label})-[member_permission:{permission_rel}]->(team)
            WHERE member_user IS NOT NULL
            WITH team,
                 collect(DISTINCT properties(current_permission))[0] AS current_user_permission,
                 collect(DISTINCT {{
                     id: member_user.id,
                     userId: member_user.userId,
                     userName: member_user.fullName,
                     userEmail: member_user.email,
                     role: member_permission.role,
                     joinedAt: member_permission.createdAtTimestamp,
                     isOwner: member_permission.role = 'OWNER'
                 }}) AS team_members
            WITH team, current_user_permission, team_members, size(team_members) AS user_count
            ORDER BY team.createdAtTimestamp DESC
            SKIP $offset
            LIMIT $limit
            RETURN {{
                id: team.id,
                name: team.name,
                description: team.description,
                createdBy: team.createdBy,
                orgId: team.orgId,
                createdAtTimestamp: team.createdAtTimestamp,
                updatedAtTimestamp: team.updatedAtTimestamp,
                currentUserPermission: CASE WHEN current_user_permission IS NOT NULL THEN current_user_permission ELSE null END,
                members: team_members,
                memberCount: user_count,
                canEdit: CASE WHEN current_user_permission IS NOT NULL AND current_user_permission.role IN ['OWNER'] THEN true ELSE false END,
                canDelete: CASE WHEN current_user_permission IS NOT NULL AND current_user_permission.role = 'OWNER' THEN true ELSE false END,
                canManageMembers: CASE WHEN current_user_permission IS NOT NULL AND current_user_permission.role IN ['OWNER'] THEN true ELSE false END
            }} AS result
            """

            # Count total teams for pagination
            count_query = f"""
            MATCH (team:{team_label} {{orgId: $orgId}})
            WHERE 1=1 {search_where}
            RETURN count(team) AS total_count
            """

            # Get total count
            count_params = {"orgId": org_id}
            if search:
                count_params["search"] = search

            count_results = await self.client.execute_query(
                count_query,
                parameters=count_params,
                txn_id=transaction
            )
            total_count = count_results[0]["total_count"] if count_results else 0

            # Get teams with pagination
            teams_params = {
                "orgId": org_id,
                "user_key": user_key,
                "offset": offset,
                "limit": limit
            }
            if search:
                teams_params["search"] = search

            result_list = await self.client.execute_query(
                teams_query,
                parameters=teams_params,
                txn_id=transaction
            )

            # Convert results to ArangoDB format
            converted_results = []
            for row in result_list:
                result = row.get("result", {})
                # Convert team node
                team_data = self._neo4j_to_arango_node(result, CollectionNames.TEAMS.value)
                # Convert permission if present
                if result.get("currentUserPermission"):
                    perm = result["currentUserPermission"]
                    # Add from_id and to_id for edge conversion
                    perm["from_id"] = user_key
                    perm["to_id"] = result.get("id")
                    perm["from_collection"] = CollectionNames.USERS.value
                    perm["to_collection"] = CollectionNames.TEAMS.value
                    perm_data = self._neo4j_to_arango_edge(perm, CollectionNames.PERMISSION.value)
                    team_data["currentUserPermission"] = perm_data
                else:
                    team_data["currentUserPermission"] = None
                converted_results.append(team_data)

            return converted_results if converted_results else [], total_count

        except Exception as e:
            self.logger.error(f"Error in get_teams: {str(e)}", exc_info=True)
            return [], 0

    async def get_team_with_users(
        self,
        team_id: str,
        user_key: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get a single team with its members and permissions.
        """
        try:
            team_label = collection_to_label(CollectionNames.TEAMS.value)
            user_label = collection_to_label(CollectionNames.USERS.value)
            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)

            team_query = f"""
            MATCH (team:{team_label} {{id: $teamId}})
            OPTIONAL MATCH (current_user:{user_label} {{id: $user_key}})-[current_permission:{permission_rel}]->(team)
            OPTIONAL MATCH (member_user:{user_label})-[member_permission:{permission_rel}]->(team)
            WHERE member_user IS NOT NULL
            WITH team,
                 collect(DISTINCT properties(current_permission))[0] AS current_user_permission,
                 collect(DISTINCT {{
                     id: member_user.id,
                     userId: member_user.userId,
                     userName: member_user.fullName,
                     userEmail: member_user.email,
                     role: member_permission.role,
                     joinedAt: member_permission.createdAtTimestamp,
                     isOwner: member_permission.role = 'OWNER'
                 }}) AS team_members
            RETURN {{
                id: team.id,
                name: team.name,
                description: team.description,
                createdBy: team.createdBy,
                orgId: team.orgId,
                createdAtTimestamp: team.createdAtTimestamp,
                updatedAtTimestamp: team.updatedAtTimestamp,
                currentUserPermission: CASE WHEN current_user_permission IS NOT NULL THEN current_user_permission ELSE null END,
                members: team_members,
                memberCount: size(team_members),
                canEdit: CASE WHEN current_user_permission IS NOT NULL AND current_user_permission.role IN ['OWNER'] THEN true ELSE false END,
                canDelete: CASE WHEN current_user_permission IS NOT NULL AND current_user_permission.role = 'OWNER' THEN true ELSE false END,
                canManageMembers: CASE WHEN current_user_permission IS NOT NULL AND current_user_permission.role IN ['OWNER'] THEN true ELSE false END
            }} AS result
            """

            results = await self.client.execute_query(
                team_query,
                parameters={
                    "teamId": team_id,
                    "user_key": user_key
                },
                txn_id=transaction
            )

            if not results:
                return None

            result = results[0].get("result", {})
            # Convert team node
            team_data = self._neo4j_to_arango_node(result, CollectionNames.TEAMS.value)
            # Convert permission if present
            if result.get("currentUserPermission"):
                perm = result["currentUserPermission"]
                # Add from_id and to_id for edge conversion
                perm["from_id"] = user_key
                perm["to_id"] = result.get("id")
                perm["from_collection"] = CollectionNames.USERS.value
                perm["to_collection"] = CollectionNames.TEAMS.value
                perm_data = self._neo4j_to_arango_edge(perm, CollectionNames.PERMISSION.value)
                team_data["currentUserPermission"] = perm_data
            else:
                team_data["currentUserPermission"] = None

            return team_data

        except Exception as e:
            self.logger.error(f"Error in get_team_with_users: {str(e)}", exc_info=True)
            return None

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
        """
        try:
            offset = (page - 1) * limit
            team_label = collection_to_label(CollectionNames.TEAMS.value)
            user_label = collection_to_label(CollectionNames.USERS.value)
            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)

            # Build search filter (case-insensitive)
            search_where = ""
            if search:
                search_where = "AND (toLower(team.name) CONTAINS toLower($search) OR toLower(team.description) CONTAINS toLower($search))"

            # Query to get all teams user is a member of with pagination
            user_teams_query = f"""
            MATCH (u:{user_label} {{id: $user_key}})-[p:{permission_rel}]->(team:{team_label})
            WHERE 1=1 {search_where}
            OPTIONAL MATCH (member_user:{user_label})-[member_permission:{permission_rel}]->(team)
            WHERE member_user IS NOT NULL
            WITH team, properties(p) AS current_user_permission,
                 collect(DISTINCT {{
                     id: member_user.id,
                     userId: member_user.userId,
                     userName: member_user.fullName,
                     userEmail: member_user.email,
                     role: member_permission.role,
                     joinedAt: member_permission.createdAtTimestamp,
                     isOwner: member_permission.role = 'OWNER'
                 }}) AS team_members
            WITH team, current_user_permission, team_members, size(team_members) AS member_count
            ORDER BY team.createdAtTimestamp DESC
            SKIP $offset
            LIMIT $limit
            RETURN {{
                id: team.id,
                name: team.name,
                description: team.description,
                createdBy: team.createdBy,
                orgId: team.orgId,
                createdAtTimestamp: team.createdAtTimestamp,
                updatedAtTimestamp: team.updatedAtTimestamp,
                currentUserPermission: current_user_permission,
                members: team_members,
                memberCount: member_count,
                canEdit: CASE WHEN current_user_permission IS NOT NULL AND current_user_permission.role IN ['OWNER'] THEN true ELSE false END,
                canDelete: CASE WHEN current_user_permission IS NOT NULL AND current_user_permission.role = 'OWNER' THEN true ELSE false END,
                canManageMembers: CASE WHEN current_user_permission IS NOT NULL AND current_user_permission.role IN ['OWNER'] THEN true ELSE false END
            }} AS result
            """

            # Count query for pagination
            count_query = f"""
            MATCH (u:{user_label} {{id: $user_key}})-[p:{permission_rel}]->(team:{team_label})
            WHERE 1=1 {search_where}
            RETURN count(DISTINCT team) AS total_count
            """

            # Get total count
            count_params = {"user_key": user_key}
            if search:
                count_params["search"] = search

            count_results = await self.client.execute_query(
                count_query,
                parameters=count_params,
                txn_id=transaction
            )
            total_count = count_results[0]["total_count"] if count_results else 0

            # Get teams with pagination
            teams_params = {
                "user_key": user_key,
                "offset": offset,
                "limit": limit
            }
            if search:
                teams_params["search"] = search

            result_list = await self.client.execute_query(
                user_teams_query,
                parameters=teams_params,
                txn_id=transaction
            )

            # Convert results to ArangoDB format
            converted_results = []
            for row in result_list:
                result = row.get("result", {})
                # Convert team node
                team_data = self._neo4j_to_arango_node(result, CollectionNames.TEAMS.value)
                # Convert permission
                if result.get("currentUserPermission"):
                    perm = result["currentUserPermission"]
                    # Add from_id and to_id for edge conversion
                    perm["from_id"] = user_key
                    perm["to_id"] = result.get("id")
                    perm["from_collection"] = CollectionNames.USERS.value
                    perm["to_collection"] = CollectionNames.TEAMS.value
                    perm_data = self._neo4j_to_arango_edge(perm, CollectionNames.PERMISSION.value)
                    team_data["currentUserPermission"] = perm_data
                converted_results.append(team_data)

            return converted_results if converted_results else [], total_count

        except Exception as e:
            self.logger.error(f"Error in get_user_teams: {str(e)}", exc_info=True)
            return [], 0

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
        """
        try:
            offset = (page - 1) * limit
            team_label = collection_to_label(CollectionNames.TEAMS.value)
            user_label = collection_to_label(CollectionNames.USERS.value)
            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)

            # Build search filter (case-insensitive)
            search_where = ""
            if search:
                search_where = "AND (toLower(team.name) CONTAINS toLower($search) OR toLower(team.description) CONTAINS toLower($search))"

            # Optimized query: Batch fetch team members
            created_teams_query = f"""
            MATCH (team:{team_label} {{orgId: $orgId, createdBy: $userKey}})
            WHERE 1=1 {search_where}
            WITH team
            ORDER BY team.createdAtTimestamp DESC
            SKIP $offset
            LIMIT $limit
            WITH collect(team) AS teams
            UNWIND teams AS team
            OPTIONAL MATCH (current_user:{user_label} {{id: $user_key}})-[current_permission:{permission_rel}]->(team)
            OPTIONAL MATCH (member_user:{user_label})-[member_permission:{permission_rel}]->(team)
            WHERE member_user IS NOT NULL
            WITH team,
                 collect(DISTINCT properties(current_permission))[0] AS current_user_permission,
                 collect(DISTINCT {{
                     id: member_user.id,
                     userId: member_user.userId,
                     userName: member_user.fullName,
                     userEmail: member_user.email,
                     role: member_permission.role,
                     joinedAt: member_permission.createdAtTimestamp,
                     isOwner: member_permission.role = 'OWNER'
                 }}) AS team_members
            RETURN {{
                id: team.id,
                name: team.name,
                description: team.description,
                createdBy: team.createdBy,
                orgId: team.orgId,
                createdAtTimestamp: team.createdAtTimestamp,
                updatedAtTimestamp: team.updatedAtTimestamp,
                currentUserPermission: CASE WHEN current_user_permission IS NOT NULL THEN current_user_permission ELSE null END,
                members: team_members,
                memberCount: size(team_members),
                canEdit: true,
                canDelete: true,
                canManageMembers: true
            }} AS result
            """

            # Count total teams for pagination
            count_query = f"""
            MATCH (team:{team_label} {{orgId: $orgId, createdBy: $userKey}})
            WHERE 1=1 {search_where}
            RETURN count(team) AS total_count
            """

            count_params = {
                "orgId": org_id,
                "userKey": user_key
            }
            if search:
                count_params["search"] = search

            count_results = await self.client.execute_query(
                count_query,
                parameters=count_params,
                txn_id=transaction
            )
            total_count = count_results[0]["total_count"] if count_results else 0

            # Get teams with pagination
            teams_params = {
                "orgId": org_id,
                "userKey": user_key,
                "user_key": user_key,
                "offset": offset,
                "limit": limit
            }
            if search:
                teams_params["search"] = search

            result_list = await self.client.execute_query(
                created_teams_query,
                parameters=teams_params,
                txn_id=transaction
            )

            # Convert results to ArangoDB format
            converted_results = []
            for row in result_list:
                result = row.get("result", {})
                # Convert team node
                team_data = self._neo4j_to_arango_node(result, CollectionNames.TEAMS.value)
                # Convert permission if present
                if result.get("currentUserPermission"):
                    perm = result["currentUserPermission"]
                    # Add from_id and to_id for edge conversion
                    perm["from_id"] = user_key
                    perm["to_id"] = result.get("id")
                    perm["from_collection"] = CollectionNames.USERS.value
                    perm["to_collection"] = CollectionNames.TEAMS.value
                    perm_data = self._neo4j_to_arango_edge(perm, CollectionNames.PERMISSION.value)
                    team_data["currentUserPermission"] = perm_data
                else:
                    team_data["currentUserPermission"] = None
                converted_results.append(team_data)

            return converted_results if converted_results else [], total_count

        except Exception as e:
            self.logger.error(f"Error in get_user_created_teams: {str(e)}", exc_info=True)
            return [], 0

    async def get_team_users(
        self,
        team_id: str,
        org_id: str,
        user_key: str,
        transaction: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get all users in a specific team.
        """
        try:
            team_label = collection_to_label(CollectionNames.TEAMS.value)
            user_label = collection_to_label(CollectionNames.USERS.value)
            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)

            team_users_query = f"""
            MATCH (team:{team_label} {{id: $teamId, orgId: $orgId}})
            OPTIONAL MATCH (current_user:{user_label} {{id: $user_key}})-[current_permission:{permission_rel}]->(team)
            OPTIONAL MATCH (member_user:{user_label})-[member_permission:{permission_rel}]->(team)
            WHERE member_user IS NOT NULL
            WITH team,
                 collect(DISTINCT properties(current_permission))[0] AS current_user_permission,
                 collect(DISTINCT {{
                     id: member_user.id,
                     userId: member_user.userId,
                     userName: member_user.fullName,
                     userEmail: member_user.email,
                     role: member_permission.role,
                     joinedAt: member_permission.createdAtTimestamp,
                     isOwner: member_permission.role = 'OWNER'
                 }}) AS team_members
            RETURN {{
                id: team.id,
                name: team.name,
                description: team.description,
                createdBy: team.createdBy,
                orgId: team.orgId,
                createdAtTimestamp: team.createdAtTimestamp,
                updatedAtTimestamp: team.updatedAtTimestamp,
                currentUserPermission: CASE WHEN current_user_permission IS NOT NULL THEN current_user_permission ELSE null END,
                members: team_members,
                memberCount: size(team_members),
                canEdit: CASE WHEN current_user_permission IS NOT NULL AND current_user_permission.role IN ['OWNER'] THEN true ELSE false END,
                canDelete: CASE WHEN current_user_permission IS NOT NULL AND current_user_permission.role = 'OWNER' THEN true ELSE false END,
                canManageMembers: CASE WHEN current_user_permission IS NOT NULL AND current_user_permission.role IN ['OWNER'] THEN true ELSE false END
            }} AS result
            """

            results = await self.client.execute_query(
                team_users_query,
                parameters={
                    "teamId": team_id,
                    "orgId": org_id,
                    "user_key": user_key
                },
                txn_id=transaction
            )

            if not results:
                return None

            result = results[0].get("result", {})
            # Convert team node
            team_data = self._neo4j_to_arango_node(result, CollectionNames.TEAMS.value)
            # Convert permission if present
            if result.get("currentUserPermission"):
                perm = result["currentUserPermission"]
                # Add from_id and to_id for edge conversion
                perm["from_id"] = user_key
                perm["to_id"] = result.get("id")
                perm["from_collection"] = CollectionNames.USERS.value
                perm["to_collection"] = CollectionNames.TEAMS.value
                perm_data = self._neo4j_to_arango_edge(perm, CollectionNames.PERMISSION.value)
                team_data["currentUserPermission"] = perm_data
            else:
                team_data["currentUserPermission"] = None

            return team_data

        except Exception as e:
            self.logger.error(f"Error in get_team_users: {str(e)}", exc_info=True)
            return None

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
        """
        try:
            team_label = collection_to_label(CollectionNames.TEAMS.value)
            user_label = collection_to_label(CollectionNames.USERS.value)
            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)

            search_query = f"""
            MATCH (team:{team_label} {{orgId: $orgId}})
            WHERE toLower(team.name) CONTAINS toLower($query) OR toLower(team.description) CONTAINS toLower($query)
            OPTIONAL MATCH (current_user:{user_label} {{id: $user_key}})-[current_permission:{permission_rel}]->(team)
            OPTIONAL MATCH (member_user:{user_label})-[member_permission:{permission_rel}]->(team)
            WHERE member_user IS NOT NULL
            WITH team,
                 collect(DISTINCT properties(current_permission))[0] AS current_user_permission,
                 collect(DISTINCT {{
                     id: member_user.id,
                     userId: member_user.userId,
                     userName: member_user.fullName,
                     userEmail: member_user.email,
                     role: member_permission.role,
                     joinedAt: member_permission.createdAtTimestamp,
                     isOwner: member_user.id = team.createdBy
                 }}) AS team_members
            SKIP $offset
            LIMIT $limit
            RETURN {{
                team: {{
                    id: team.id,
                    name: team.name,
                    description: team.description,
                    createdBy: team.createdBy,
                    orgId: team.orgId,
                    createdAtTimestamp: team.createdAtTimestamp,
                    updatedAtTimestamp: team.updatedAtTimestamp
                }},
                currentUserPermission: CASE WHEN current_user_permission IS NOT NULL THEN current_user_permission ELSE null END,
                members: team_members,
                memberCount: size(team_members),
                canEdit: CASE WHEN current_user_permission IS NOT NULL AND current_user_permission.role IN ['OWNER'] THEN true ELSE false END,
                canDelete: CASE WHEN current_user_permission IS NOT NULL AND current_user_permission.role = 'OWNER' THEN true ELSE false END,
                canManageMembers: CASE WHEN current_user_permission IS NOT NULL AND current_user_permission.role IN ['OWNER'] THEN true ELSE false END
            }} AS result
            """

            results = await self.client.execute_query(
                search_query,
                parameters={
                    "orgId": org_id,
                    "query": query,
                    "limit": limit,
                    "offset": offset,
                    "user_key": user_key
                },
                txn_id=transaction
            )

            # Convert results to ArangoDB format
            converted_results = []
            for row in results:
                result = row.get("result", {})
                # Convert team node within the wrapped structure
                if result.get("team"):
                    team_data = self._neo4j_to_arango_node(result["team"], CollectionNames.TEAMS.value)
                    result["team"] = team_data
                # Convert permission if present
                if result.get("currentUserPermission"):
                    perm = result["currentUserPermission"]
                    # Add from_id and to_id for edge conversion
                    perm["from_id"] = user_key
                    perm["to_id"] = result.get("team", {}).get("id")
                    perm["from_collection"] = CollectionNames.USERS.value
                    perm["to_collection"] = CollectionNames.TEAMS.value
                    perm_data = self._neo4j_to_arango_edge(perm, CollectionNames.PERMISSION.value)
                    result["currentUserPermission"] = perm_data
                else:
                    result["currentUserPermission"] = None
                converted_results.append(result)

            return converted_results if converted_results else []

        except Exception as e:
            self.logger.error(f"Error in search_teams: {str(e)}", exc_info=True)
            return []

    async def delete_team_member_edges(
        self,
        team_id: str,
        user_ids: List[str],
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Delete edges to remove team members.
        """
        try:
            team_label = collection_to_label(CollectionNames.TEAMS.value)
            user_label = collection_to_label(CollectionNames.USERS.value)
            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)

            delete_query = f"""
            MATCH (u:{user_label})-[p:{permission_rel}]->(team:{team_label} {{id: $team_id}})
            WHERE u.id IN $userIds
            WITH p, properties(p) AS old_props, u.id AS from_id, team.id AS to_id
            DELETE p
            RETURN old_props, from_id, to_id
            """

            results = await self.client.execute_query(
                delete_query,
                parameters={
                    "userIds": user_ids,
                    "team_id": team_id
                },
                txn_id=transaction
            )

            # Convert results to ArangoDB format
            deleted_list = []
            for row in results:
                old_props = row.get("old_props", {})
                edge_data = {
                    **old_props,
                    "from_id": row.get("from_id"),
                    "to_id": row.get("to_id"),
                    "from_collection": CollectionNames.USERS.value,
                    "to_collection": CollectionNames.TEAMS.value
                }
                deleted_list.append(self._neo4j_to_arango_edge(edge_data, CollectionNames.PERMISSION.value))

            return deleted_list if deleted_list else []

        except Exception as e:
            self.logger.error(f"Error in delete_team_member_edges: {str(e)}", exc_info=True)
            return []

    async def batch_update_team_member_roles(
        self,
        team_id: str,
        user_roles: List[Dict[str, str]],
        timestamp: int,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Batch update user roles in a team.
        """
        try:
            team_label = collection_to_label(CollectionNames.TEAMS.value)
            user_label = collection_to_label(CollectionNames.USERS.value)
            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)

            batch_update_query = f"""
            UNWIND $user_roles AS user_role
            MATCH (u:{user_label} {{id: user_role.userId}})-[p:{permission_rel}]->(team:{team_label} {{id: $team_id}})
            SET p.role = user_role.role, p.updatedAtTimestamp = $timestamp
            RETURN properties(p) AS updated_props, u.id AS from_id, team.id AS to_id
            """

            results = await self.client.execute_query(
                batch_update_query,
                parameters={
                    "team_id": team_id,
                    "user_roles": user_roles,
                    "timestamp": timestamp
                },
                txn_id=transaction
            )

            # Convert results to ArangoDB format
            updated_permissions = []
            for row in results:
                updated_props = row.get("updated_props", {})
                edge_data = {
                    **updated_props,
                    "from_id": row.get("from_id"),
                    "to_id": row.get("to_id"),
                    "from_collection": CollectionNames.USERS.value,
                    "to_collection": CollectionNames.TEAMS.value
                }
                updated_permissions.append(self._neo4j_to_arango_edge(edge_data, CollectionNames.PERMISSION.value))

            return updated_permissions if updated_permissions else []

        except Exception as e:
            self.logger.error(f"Error in batch_update_team_member_roles: {str(e)}", exc_info=True)
            return []

    async def delete_all_team_permissions(
        self,
        team_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """
        Delete all permissions for a team.
        """
        try:
            team_label = collection_to_label(CollectionNames.TEAMS.value)
            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)

            delete_query = f"""
            MATCH (u)-[p:{permission_rel}]->(team:{team_label} {{id: $team_id}})
            DELETE p
            """

            await self.client.execute_query(
                delete_query,
                parameters={"team_id": team_id},
                txn_id=transaction
            )

        except Exception as e:
            self.logger.error(f"Error in delete_all_team_permissions: {str(e)}", exc_info=True)
            raise

    async def get_team_owner_removal_info(
        self,
        team_id: str,
        user_ids: List[str],
        transaction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get information about owners being removed and total owner count for a team.
        """
        try:
            team_label = collection_to_label(CollectionNames.TEAMS.value)
            user_label = collection_to_label(CollectionNames.USERS.value)
            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)

            # Get owners being removed
            owners_query = f"""
            MATCH (u:{user_label})-[p:{permission_rel}]->(team:{team_label} {{id: $team_id}})
            WHERE u.id IN $user_ids AND p.role = 'OWNER'
            RETURN u.id as userId
            """

            owners_result = await self.client.execute_query(
                owners_query,
                parameters={"team_id": team_id, "user_ids": user_ids},
                txn_id=transaction
            )

            owners_being_removed = [r.get("userId") for r in owners_result]

            # Get total owner count
            count_query = f"""
            MATCH (u)-[p:{permission_rel}]->(team:{team_label} {{id: $team_id}})
            WHERE p.role = 'OWNER'
            RETURN count(p) as count
            """

            count_result = await self.client.execute_query(
                count_query,
                parameters={"team_id": team_id},
                txn_id=transaction
            )

            total_owner_count = count_result[0].get("count", 0) if count_result else 0

            return {
                "owners_being_removed": owners_being_removed,
                "total_owner_count": total_owner_count
            }

        except Exception as e:
            self.logger.error(f"Error in get_team_owner_removal_info: {str(e)}", exc_info=True)
            raise

    async def get_team_permissions_and_owner_count(
        self,
        team_id: str,
        user_ids: List[str],
        transaction: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get team info, current permissions for specific users, and total owner count.
        """
        try:
            team_label = collection_to_label(CollectionNames.TEAMS.value)
            user_label = collection_to_label(CollectionNames.USERS.value)
            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)

            # Get team info
            team_query = f"""
            MATCH (team:{team_label} {{id: $team_id}})
            RETURN properties(team) as team
            """

            team_result = await self.client.execute_query(
                team_query,
                parameters={"team_id": team_id},
                txn_id=transaction
            )

            team = team_result[0].get("team") if team_result else None

            # Get current permissions for specific users
            perms_query = f"""
            MATCH (u:{user_label})-[p:{permission_rel}]->(team:{team_label} {{id: $team_id}})
            WHERE u.id IN $user_ids
            RETURN u.id as userId, p.role as role
            """

            perms_result = await self.client.execute_query(
                perms_query,
                parameters={"team_id": team_id, "user_ids": user_ids},
                txn_id=transaction
            )

            permissions = {r.get("userId"): r.get("role") for r in perms_result}

            # Get total owner count
            count_query = f"""
            MATCH (u)-[p:{permission_rel}]->(team:{team_label} {{id: $team_id}})
            WHERE p.role = 'OWNER'
            RETURN count(p) as count
            """

            count_result = await self.client.execute_query(
                count_query,
                parameters={"team_id": team_id},
                txn_id=transaction
            )

            owner_count = count_result[0].get("count", 0) if count_result else 0

            return {
                "team": team,
                "permissions": permissions,
                "owner_count": owner_count
            }

        except Exception as e:
            self.logger.error(f"Error in get_team_permissions_and_owner_count: {str(e)}", exc_info=True)
            raise

    # ==================== User Operations ====================

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
        """
        try:
            offset = (page - 1) * limit
            user_label = collection_to_label(CollectionNames.USERS.value)
            org_label = collection_to_label(CollectionNames.ORGS.value)
            belongs_to_rel = edge_collection_to_relationship(CollectionNames.BELONGS_TO.value)

            # Build search filter (case-insensitive)
            search_where = ""
            if search:
                search_where = "AND (toLower(u.fullName) CONTAINS toLower($search) OR toLower(u.email) CONTAINS toLower($search))"

            # Query to get users with their team memberships
            users_query = f"""
            MATCH (u:{user_label})-[b:{belongs_to_rel}]->(org:{org_label} {{id: $orgId}})
            WHERE b.entityType = 'ORGANIZATION' AND u.isActive = true {search_where}
            ORDER BY u.fullName ASC
            SKIP $offset
            LIMIT $limit
            RETURN {{
                id: u.id,
                userId: u.userId,
                name: u.fullName,
                email: u.email,
                isActive: u.isActive,
                createdAtTimestamp: u.createdAtTimestamp,
                updatedAtTimestamp: u.updatedAtTimestamp
            }} AS result
            """

            # Count total users for pagination
            count_query = f"""
            MATCH (u:{user_label})-[b:{belongs_to_rel}]->(org:{org_label} {{id: $orgId}})
            WHERE b.entityType = 'ORGANIZATION' AND u.isActive = true {search_where}
            RETURN count(DISTINCT u) AS total_count
            """

            # Get total count
            count_params = {"orgId": org_id}
            if search:
                count_params["search"] = search

            count_results = await self.client.execute_query(
                count_query,
                parameters=count_params,
                txn_id=transaction
            )
            total_count = count_results[0]["total_count"] if count_results else 0

            # Get users with pagination
            users_params = {
                "orgId": org_id,
                "offset": offset,
                "limit": limit
            }
            if search:
                users_params["search"] = search

            result_list = await self.client.execute_query(
                users_query,
                parameters=users_params,
                txn_id=transaction
            )

            # Convert results to ArangoDB format
            converted_results = []
            for row in result_list:
                result = row.get("result", {})
                user_data = self._neo4j_to_arango_node(result, CollectionNames.USERS.value)
                converted_results.append(user_data)

            return converted_results if converted_results else [], total_count

        except Exception as e:
            self.logger.error(f"Error in get_organization_users: {str(e)}", exc_info=True)
            return [], 0

    async def delete_all_edges_for_node(
        self,
        node_key: str,
        collection: str,
        transaction: Optional[str] = None
    ) -> int:
        """
        Delete all edges connected to a node (both incoming and outgoing).

        Args:
            node_key: The node key (e.g., "groups/12345" or full _id)
            collection: The edge collection name
            transaction: Optional transaction ID

        Returns:
            int: Total number of edges deleted
        """
        try:
            self.logger.info(f"🚀 Deleting all edges for node: {node_key} in collection: {collection}")

            # Parse node_key to get collection and key
            collection_name, key = self._parse_arango_id(node_key)
            if not collection_name:
                self.logger.warning(f"Could not parse collection from node_key: {node_key}")
                return 0

            node_label = collection_to_label(collection_name)
            relationship_type = edge_collection_to_relationship(collection)

            # Delete both incoming and outgoing edges
            # CRITICAL: Filter out null values before unwinding
            query = f"""
            MATCH (n:{node_label} {{id: $key}})
            OPTIONAL MATCH (n)-[r1:{relationship_type}]->()
            OPTIONAL MATCH ()-[r2:{relationship_type}]->(n)
            WITH n, [r IN collect(DISTINCT r1) + collect(DISTINCT r2) WHERE r IS NOT NULL] AS all_rels
            UNWIND all_rels AS rel
            DELETE rel
            RETURN count(rel) AS deleted
            """

            results = await self.client.execute_query(
                query,
                parameters={"key": key},
                txn_id=transaction
            )

            count = results[0]["deleted"] if results else 0

            if count > 0:
                self.logger.info(f"✅ Successfully deleted {count} edges for node: {node_key}")
            else:
                self.logger.warning(f"⚠️ No edges found for node: {node_key} in collection: {collection}")

            return count

        except Exception as e:
            self.logger.error(f"❌ Delete all edges for node failed: {str(e)}")
            raise


    async def check_toolset_in_use(self, toolset_name: str, user_id: str, transaction: Optional[str] = None) -> List[str]:
        """
        Check if a toolset is currently in use by any active agents.

        Args:
            toolset_name: Normalized toolset name
            user_id: User ID who owns the toolset
            transaction: Optional transaction ID

        Returns:
            List of agent names that are using the toolset. Empty list if not in use.
        """
        try:
            toolset_label = collection_to_label(CollectionNames.AGENT_TOOLSETS.value)
            agent_label = collection_to_label(CollectionNames.AGENT_INSTANCES.value)
            agent_has_toolset_rel = edge_collection_to_relationship(CollectionNames.AGENT_HAS_TOOLSET.value)

            # Find toolsets matching name and user_id, then check for agents using them
            query = f"""
            MATCH (ts:{toolset_label} {{name: $name, userId: $user_id}})
            MATCH (agent:{agent_label})-[r:{agent_has_toolset_rel}]->(ts)
            WHERE (agent.isDeleted IS NULL OR agent.isDeleted = false)
            AND (agent.deleted IS NULL OR agent.deleted = false)
            RETURN DISTINCT agent.name AS agentName
            """

            results = await self.client.execute_query(
                query,
                parameters={"name": toolset_name, "user_id": user_id},
                txn_id=transaction
            )

            if results:
                agent_names = list(set(r.get("agentName", "Unknown") for r in results if r))
                return agent_names

            return []

        except Exception as e:
            self.logger.error(f"Failed to check toolset usage: {str(e)}")
            raise


    async def get_agent(self, agent_id: str, user_id: str, transaction: Optional[str] = None) -> Optional[Dict]:
        """
        Get an agent by ID with user permissions and linked graph data.

        Includes:
        - Agent document with permissions
        - Linked toolsets with their tools (via agentHasToolset -> toolsetHasTool)
        - Linked knowledge with filters (via agentHasKnowledge)
        """
        try:
            agent_label = collection_to_label(CollectionNames.AGENT_INSTANCES.value)
            user_label = collection_to_label(CollectionNames.USERS.value)
            team_label = collection_to_label(CollectionNames.TEAMS.value)
            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)
            agent_has_toolset_rel = edge_collection_to_relationship(CollectionNames.AGENT_HAS_TOOLSET.value)
            toolset_has_tool_rel = edge_collection_to_relationship(CollectionNames.TOOLSET_HAS_TOOL.value)
            agent_has_knowledge_rel = edge_collection_to_relationship(CollectionNames.AGENT_HAS_KNOWLEDGE.value)
            toolset_label = collection_to_label(CollectionNames.AGENT_TOOLSETS.value)
            tool_label = collection_to_label(CollectionNames.AGENT_TOOLS.value)
            knowledge_label = collection_to_label(CollectionNames.AGENT_KNOWLEDGE.value)

            # user_id is the user's _key (internal ID), not userId (external ID)
            # This matches ArangoHTTPProvider behavior where user_id is used directly as user_key
            user_key = user_id

            # Get user document by id (which is the _key) to extract userId for team queries
            user_query = f"""
            MATCH (u:{user_label} {{id: $user_key}})
            RETURN u
            LIMIT 1
            """
            user_result = await self.client.execute_query(
                user_query,
                parameters={"user_key": user_key},
                txn_id=transaction
            )

            # Get user's teams - need userId (external ID) for team lookup
            user_team_ids = []
            if user_result:
                try:
                    user_dict = dict(user_result[0]["u"])
                    user_doc = self._neo4j_to_arango_node(user_dict, CollectionNames.USERS.value)
                    actual_user_id = user_doc.get("userId")

                    if actual_user_id:
                        # Get user's teams using userId (external ID)
                        user_teams_query = f"""
                        MATCH (u:{user_label} {{userId: $user_id}})-[p:{permission_rel}]->(t:{team_label})
                        WHERE p.type = 'USER'
                        RETURN t.id AS team_id
                        """
                        user_teams_result = await self.client.execute_query(
                            user_teams_query,
                            parameters={"user_id": actual_user_id},
                            txn_id=transaction
                        )
                        user_team_ids = [r["team_id"] for r in user_teams_result] if user_teams_result else []
                except Exception as e:
                    self.logger.warning(f"Error getting user teams: {str(e)}")
                    # Continue without teams - individual permission check will still work

            # Check individual user permissions on the agent
            individual_query = f"""
            MATCH (u:{user_label} {{id: $user_key}})-[p:{permission_rel}]->(agent:{agent_label} {{id: $agent_id}})
            WHERE p.type = 'USER'
            AND (agent.isDeleted IS NULL OR agent.isDeleted = false)
            RETURN agent, p.role AS role, 'INDIVIDUAL' AS access_type
            LIMIT 1
            """

            individual_result = await self.client.execute_query(
                individual_query,
                parameters={"user_key": user_key, "agent_id": agent_id},
                txn_id=transaction
            )

            agent_data = None
            user_role = None
            access_type = None

            if individual_result:
                agent_data = dict(individual_result[0]["agent"])
                user_role = individual_result[0]["role"]
                access_type = "INDIVIDUAL"
            elif user_team_ids:
                # Check team permissions
                team_query = f"""
                MATCH (t:{team_label})-[p:{permission_rel}]->(agent:{agent_label} {{id: $agent_id}})
                WHERE t.id IN $team_ids
                AND p.type = 'TEAM'
                AND (agent.isDeleted IS NULL OR agent.isDeleted = false)
                RETURN agent, p.role AS role, 'TEAM' AS access_type
                LIMIT 1
                """

                team_result = await self.client.execute_query(
                    team_query,
                    parameters={"team_ids": user_team_ids, "agent_id": agent_id},
                    txn_id=transaction
                )

                if team_result:
                    agent_data = dict(team_result[0]["agent"])
                    user_role = team_result[0]["role"]
                    access_type = "TEAM"

            if not agent_data:
                self.logger.warning(f"No permissions found for user {user_key} on agent {agent_id}")
                return None

            # Convert to ArangoDB format
            agent = self._neo4j_to_arango_node(agent_data, CollectionNames.AGENT_INSTANCES.value)
            agent["access_type"] = access_type
            agent["user_role"] = user_role
            agent["can_edit"] = user_role in ["OWNER", "WRITER", "ORGANIZER"]
            agent["can_delete"] = user_role == "OWNER"
            agent["can_share"] = user_role in ["OWNER", "ORGANIZER"]
            agent["can_view"] = True

            # Get linked toolsets with their tools
            toolsets_query = f"""
            MATCH (agent:{agent_label} {{id: $agent_id}})-[r:{agent_has_toolset_rel}]->(ts:{toolset_label})
            OPTIONAL MATCH (ts)-[tr:{toolset_has_tool_rel}]->(tool:{tool_label})
            WITH agent, ts, collect(DISTINCT CASE
                WHEN tool IS NOT NULL THEN {{
                    _key: tool.id,
                    name: tool.name,
                    fullName: tool.fullName,
                    toolsetName: tool.toolsetName,
                    description: tool.description
                }}
                ELSE null
            END) AS tools_raw
            WITH agent, ts, [t IN tools_raw WHERE t IS NOT NULL] AS tools
            RETURN {{
                _key: ts.id,
                name: ts.name,
                displayName: ts.displayName,
                type: ts.type,
                userId: ts.userId,
                selectedTools: ts.selectedTools,
                tools: tools
            }} AS toolset
            """

            toolsets_result = await self.client.execute_query(
                toolsets_query,
                parameters={"agent_id": agent_id},
                txn_id=transaction
            )

            agent["toolsets"] = [r["toolset"] for r in toolsets_result] if toolsets_result else []

            # Get linked knowledge with filters
            knowledge_query = f"""
            MATCH (agent:{agent_label} {{id: $agent_id}})-[r:{agent_has_knowledge_rel}]->(k:{knowledge_label})
            RETURN k.id AS _key, k.connectorId AS connectorId, k.filters AS filters
            """

            knowledge_result = await self.client.execute_query(
                knowledge_query,
                parameters={"agent_id": agent_id},
                txn_id=transaction
            )

            knowledge_list = []
            if knowledge_result:
                import json
                for k_row in knowledge_result:
                    knowledge_item = {
                        "_key": k_row["_key"],
                        "connectorId": k_row["connectorId"],
                        "filters": k_row["filters"]
                    }

                    # Parse filters to determine if this is a KB or app
                    filters_str = knowledge_item.get("filters", "{}")
                    if isinstance(filters_str, str):
                        try:
                            filters_parsed = json.loads(filters_str)
                        except json.JSONDecodeError:
                            filters_parsed = {}
                    else:
                        filters_parsed = filters_str

                    knowledge_item["filtersParsed"] = filters_parsed

                    # Check if this is a KB (has recordGroups) or app
                    record_groups = filters_parsed.get("recordGroups", [])
                    is_kb = len(record_groups) > 0

                    if is_kb and record_groups:
                        # For KBs: Get KB name from record group document
                        try:
                            first_kb_id = record_groups[0]
                            kb_doc = await self.get_document(first_kb_id, CollectionNames.RECORD_GROUPS.value, transaction=transaction)
                            if kb_doc and kb_doc.get("groupType") == Connectors.KNOWLEDGE_BASE.value:
                                knowledge_item["name"] = kb_doc.get("groupName", "")
                                knowledge_item["type"] = "KB"
                                knowledge_item["displayName"] = kb_doc.get("groupName", "")
                                knowledge_item["connectorId"] = kb_doc.get("connectorId") or knowledge_item["connectorId"]
                            else:
                                knowledge_item["name"] = knowledge_item["connectorId"]
                                knowledge_item["type"] = "UNKNOWN"
                                knowledge_item["displayName"] = knowledge_item["connectorId"]
                        except Exception as e:
                            self.logger.warning(f"Error getting KB document {first_kb_id}: {str(e)}")
                            knowledge_item["name"] = knowledge_item["connectorId"]
                            knowledge_item["type"] = "UNKNOWN"
                            knowledge_item["displayName"] = knowledge_item["connectorId"]
                    else:
                        # For apps: Get connector instance name from APPS collection
                        try:
                            app_doc = await self.get_document(knowledge_item["connectorId"], CollectionNames.APPS.value, transaction=transaction)
                            if app_doc:
                                knowledge_item["name"] = app_doc.get("name", "")
                                knowledge_item["type"] = app_doc.get("type", "APP")
                                knowledge_item["displayName"] = app_doc.get("name", "")
                            else:
                                knowledge_item["name"] = knowledge_item["connectorId"]
                                knowledge_item["type"] = "UNKNOWN"
                                knowledge_item["displayName"] = knowledge_item["connectorId"]
                        except Exception as e:
                            self.logger.warning(f"Error getting app document {knowledge_item['connectorId']}: {str(e)}")
                            knowledge_item["name"] = knowledge_item["connectorId"]
                            knowledge_item["type"] = "UNKNOWN"
                            knowledge_item["displayName"] = knowledge_item["connectorId"]

                    knowledge_list.append(knowledge_item)

            agent["knowledge"] = knowledge_list

            return agent

        except Exception as e:
            self.logger.error(f"Failed to get agent: {str(e)}")
            return None

    async def get_all_agents(self, user_id: str, transaction: Optional[str] = None) -> List[Dict]:
        """Get all agents accessible to a user via individual or team access - flattened response"""
        try:
            agent_label = collection_to_label(CollectionNames.AGENT_INSTANCES.value)
            user_label = collection_to_label(CollectionNames.USERS.value)
            team_label = collection_to_label(CollectionNames.TEAMS.value)
            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)

            # user_id is the internal key (_key in ArangoDB, id in Neo4j)
            # Use it directly to match the user node
            user_key = user_id

            # Get user's teams
            user_teams_query = f"""
            MATCH (u:{user_label} {{id: $user_key}})-[p:{permission_rel}]->(t:{team_label})
            WHERE p.type = 'USER'
            RETURN t.id AS team_id
            """

            user_teams_result = await self.client.execute_query(
                user_teams_query,
                parameters={"user_key": user_key},
                txn_id=transaction
            )
            user_team_ids = [r["team_id"] for r in user_teams_result] if user_teams_result else []

            # Get agents with individual access
            individual_query = f"""
            MATCH (u:{user_label} {{id: $user_key}})-[p:{permission_rel}]->(agent:{agent_label})
            WHERE p.type = 'USER'
            AND (agent.isDeleted IS NULL OR agent.isDeleted = false)
            RETURN agent, p.role AS role, 'INDIVIDUAL' AS access_type
            """

            individual_result = await self.client.execute_query(
                individual_query,
                parameters={"user_key": user_key},
                txn_id=transaction
            )

            agents = []
            individual_agent_keys = set()

            if individual_result:
                for row in individual_result:
                    agent_data = dict(row["agent"])
                    agent = self._neo4j_to_arango_node(agent_data, CollectionNames.AGENT_INSTANCES.value)
                    agent["access_type"] = "INDIVIDUAL"
                    agent["user_role"] = row["role"]
                    agent["can_edit"] = row["role"] in ["OWNER", "WRITER", "ORGANIZER"]
                    agent["can_delete"] = row["role"] == "OWNER"
                    agent["can_share"] = row["role"] in ["OWNER", "ORGANIZER"]
                    agent["can_view"] = True
                    agents.append(agent)
                    # Store just the key for exclusion
                    individual_agent_keys.add(agent_data.get("id", ""))

            # Get agents with team access (excluding those already found)
            if user_team_ids:
                team_query = f"""
                MATCH (t:{team_label})-[p:{permission_rel}]->(agent:{agent_label})
                WHERE t.id IN $team_ids
                AND p.type = 'TEAM'
                AND (agent.isDeleted IS NULL OR agent.isDeleted = false)
                AND NOT agent.id IN $excluded_ids
                RETURN agent, p.role AS role, 'TEAM' AS access_type
                """

                team_result = await self.client.execute_query(
                    team_query,
                    parameters={
                        "team_ids": user_team_ids,
                        "excluded_ids": list(individual_agent_keys)  # Convert set to list
                    },
                    txn_id=transaction
                )

                if team_result:
                    for row in team_result:
                        agent_data = dict(row["agent"])
                        agent = self._neo4j_to_arango_node(agent_data, CollectionNames.AGENT_INSTANCES.value)
                        agent["access_type"] = "TEAM"
                        agent["user_role"] = row["role"]
                        agent["can_edit"] = row["role"] in ["OWNER", "WRITER", "ORGANIZER"]
                        agent["can_delete"] = row["role"] == "OWNER"
                        agent["can_share"] = row["role"] in ["OWNER", "ORGANIZER"]
                        agent["can_view"] = True
                        agents.append(agent)

            return agents

        except Exception as e:
            self.logger.error(f"Failed to get all agents: {str(e)}")
            return []


    async def update_agent(self, agent_id: str, agent_updates: Dict[str, Any], user_id: str, transaction: Optional[str] = None) -> Optional[bool]:
        """
        Update an agent.

        New schema only allows: name, description, startMessage, systemPrompt, models (as keys), tags
        Tools, connectors, kb, vectorDBs are handled via edges, not in agent document.
        """
        try:
            # Check if user has permission to update the agent using the new method
            agent_with_permission = await self.get_agent(agent_id, user_id, transaction=transaction)
            if agent_with_permission is None:
                self.logger.warning(f"No permission found for user {user_id} on agent {agent_id}")
                return False

            # Check if user can edit the agent
            if not agent_with_permission.get("can_edit", False):
                self.logger.warning(f"User {user_id} does not have edit permission on agent {agent_id}")
                return False

            # Prepare update data
            update_data = {
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedBy": user_id
            }

            # Add only schema-allowed fields
            allowed_fields = ["name", "description", "startMessage", "systemPrompt", "tags", "isActive"]
            for field in allowed_fields:
                if field in agent_updates:
                    update_data[field] = agent_updates[field]

            # Handle models specially - convert model objects to model keys and deduplicate
            if "models" in agent_updates:
                raw_models = agent_updates["models"]
                if isinstance(raw_models, list):
                    model_entries = []
                    seen_entries = set()

                    for model in raw_models:
                        model_key = None
                        model_name = None

                        if isinstance(model, dict):
                            model_key = model.get("modelKey")
                            model_name = model.get("modelName", "")
                        elif isinstance(model, str):
                            if "_" in model:
                                parts = model.split("_", 1)
                                model_key = parts[0]
                                model_name = parts[1] if len(parts) > 1 else ""
                            else:
                                model_key = model
                                model_name = ""

                        if model_key:
                            if model_name:
                                entry = f"{model_key}_{model_name}"
                            else:
                                entry = model_key

                            if entry not in seen_entries:
                                model_entries.append(entry)
                                seen_entries.add(entry)

                    update_data["models"] = model_entries
                elif raw_models is None:
                    update_data["models"] = []

            # Update the agent using update_node method
            result = await self.update_node(agent_id, CollectionNames.AGENT_INSTANCES.value, update_data, transaction=transaction)

            if not result:
                self.logger.error(f"Failed to update agent {agent_id}")
                return False

            self.logger.info(f"Successfully updated agent {agent_id}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to update agent: {str(e)}")
            return False


    async def delete_agent(self, agent_id: str, user_id: str, transaction: Optional[str] = None) -> Optional[bool]:
        """Delete an agent (soft delete)"""
        try:
            # Check if agent exists
            agent = await self.get_document(agent_id, CollectionNames.AGENT_INSTANCES.value, transaction=transaction)
            if agent is None:
                self.logger.warning(f"Agent {agent_id} not found")
                return False

            # Check if user has permission to delete the agent using the new method
            agent_with_permission = await self.get_agent(agent_id, user_id, transaction=transaction)
            if agent_with_permission is None:
                self.logger.warning(f"No permission found for user {user_id} on agent {agent_id}")
                return False

            # Check if user can delete the agent
            if not agent_with_permission.get("can_delete", False):
                self.logger.warning(f"User {user_id} does not have delete permission on agent {agent_id}")
                return False

            # Prepare update data for soft delete
            update_data = {
                "isDeleted": True,
                "deletedAtTimestamp": get_epoch_timestamp_in_ms(),
                "deletedByUserId": user_id
            }

            # Soft delete the agent using update_node
            result = await self.update_node(agent_id, CollectionNames.AGENT_INSTANCES.value, update_data, transaction=transaction)

            if not result:
                self.logger.error(f"Failed to delete agent {agent_id}")
                return False

            self.logger.info(f"Successfully deleted agent {agent_id}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to delete agent: {str(e)}")
            return False


    async def share_agent(self, agent_id: str, user_id: str, user_ids: Optional[List[str]], team_ids: Optional[List[str]], transaction: Optional[str] = None) -> Optional[bool]:
        """Share an agent to users and teams"""
        try:
            # Check if agent exists and user has permission to share it
            agent_with_permission = await self.get_agent(agent_id, user_id, transaction=transaction)
            if agent_with_permission is None:
                self.logger.warning(f"No permission found for user {user_id} on agent {agent_id}")
                return False

            # Check if user can share the agent
            if not agent_with_permission.get("can_share", False):
                self.logger.warning(f"User {user_id} does not have share permission on agent {agent_id}")
                return False

            # Share the agent to users
            user_agent_edges = []
            if user_ids:
                for user_id_to_share in user_ids:
                    user = await self.get_user_by_user_id(user_id_to_share)
                    if user is None:
                        self.logger.warning(f"User {user_id_to_share} not found")
                        continue
                    user_key = user.get("id") or user.get("_key")
                    edge = {
                        "_from": f"{CollectionNames.USERS.value}/{user_key}",
                        "_to": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_id}",
                        "role": "READER",
                        "type": "USER",
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                    }
                    user_agent_edges.append(edge)

                result = await self.batch_create_edges(user_agent_edges, CollectionNames.PERMISSION.value, transaction=transaction)
                if not result:
                    self.logger.error(f"Failed to share agent {agent_id} to users")
                    return False

            # Share the agent to teams
            team_agent_edges = []
            if team_ids:
                for team_id in team_ids:
                    team = await self.get_document(team_id, CollectionNames.TEAMS.value, transaction=transaction)
                    if team is None:
                        self.logger.warning(f"Team {team_id} not found")
                        continue
                    team_key = team.get("id") or team.get("_key")
                    edge = {
                        "_from": f"{CollectionNames.TEAMS.value}/{team_key}",
                        "_to": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_id}",
                        "role": "READER",
                        "type": "TEAM",
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                    }
                    team_agent_edges.append(edge)
                result = await self.batch_create_edges(team_agent_edges, CollectionNames.PERMISSION.value, transaction=transaction)
                if not result:
                    self.logger.error(f"Failed to share agent {agent_id} to teams")
                    return False
            return True
        except Exception as e:
            self.logger.error("❌ Failed to share agent: %s", str(e), exc_info=True)
            return False


    async def unshare_agent(self, agent_id: str, user_id: str, user_ids: Optional[List[str]], team_ids: Optional[List[str]], transaction: Optional[str] = None) -> Optional[Dict]:
        """Unshare an agent from users and teams - direct deletion without validation"""
        try:
            # Check if user has permission to unshare the agent
            agent_with_permission = await self.get_agent(agent_id, user_id, transaction=transaction)
            if agent_with_permission is None or not agent_with_permission.get("can_share", False):
                return {"success": False, "reason": "Insufficient permissions to unshare agent"}

            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)
            user_label = collection_to_label(CollectionNames.USERS.value)
            team_label = collection_to_label(CollectionNames.TEAMS.value)
            agent_label = collection_to_label(CollectionNames.AGENT_INSTANCES.value)

            deleted_count = 0

            # Delete user permissions
            if user_ids:
                user_keys = []
                for uid in user_ids:
                    user = await self.get_user_by_user_id(uid)
                    if user:
                        user_keys.append(user.get("id") or user.get("_key"))

                if user_keys:
                    delete_user_query = f"""
                    MATCH (u:{user_label})-[p:{permission_rel}]->(agent:{agent_label} {{id: $agent_id}})
                    WHERE u.id IN $user_keys
                    AND p.type = 'USER'
                    AND p.role <> 'OWNER'
                    DELETE p
                    RETURN count(p) AS deleted
                    """

                    result = await self.client.execute_query(
                        delete_user_query,
                        parameters={"agent_id": agent_id, "user_keys": user_keys},
                        txn_id=transaction
                    )
                    if result:
                        deleted_count += result[0].get("deleted", 0)

            # Delete team permissions
            if team_ids:
                delete_team_query = f"""
                MATCH (t:{team_label})-[p:{permission_rel}]->(agent:{agent_label} {{id: $agent_id}})
                WHERE t.id IN $team_ids
                AND p.type = 'TEAM'
                DELETE p
                RETURN count(p) AS deleted
                """

                result = await self.client.execute_query(
                    delete_team_query,
                    parameters={"agent_id": agent_id, "team_ids": team_ids},
                    txn_id=transaction
                )
                if result:
                    deleted_count += result[0].get("deleted", 0)

            self.logger.info(f"Unshared agent {agent_id}: removed {deleted_count} permissions")

            return {
                "success": True,
                "agent_id": agent_id,
                "deleted_permissions": deleted_count
            }

        except Exception as e:
            self.logger.error("Failed to unshare agent: %s", str(e), exc_info=True)
            return {"success": False, "reason": f"Internal error: {str(e)}"}


    async def update_agent_permission(self, agent_id: str, owner_user_id: str, user_ids: Optional[List[str]], team_ids: Optional[List[str]], role: str, transaction: Optional[str] = None) -> Optional[Dict]:
        """Update permission role for users and teams on an agent (only OWNER can do this)"""
        try:
            # Check if the requesting user is the OWNER of the agent
            agent_with_permission = await self.get_agent(agent_id, owner_user_id, transaction=transaction)
            if agent_with_permission is None:
                self.logger.warning(f"No permission found for user {owner_user_id} on agent {agent_id}")
                return {"success": False, "reason": "Agent not found or no permission"}

            # Only OWNER can update permissions
            if agent_with_permission.get("user_role") != "OWNER":
                self.logger.warning(f"User {owner_user_id} is not the OWNER of agent {agent_id}")
                return {"success": False, "reason": "Only OWNER can update permissions"}

            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)
            user_label = collection_to_label(CollectionNames.USERS.value)
            team_label = collection_to_label(CollectionNames.TEAMS.value)
            agent_label = collection_to_label(CollectionNames.AGENT_INSTANCES.value)
            timestamp = get_epoch_timestamp_in_ms()

            updated_count = 0
            updated_users = 0
            updated_teams = 0

            # Update user permissions
            if user_ids:
                user_keys = []
                for uid in user_ids:
                    user = await self.get_user_by_user_id(uid)
                    if user:
                        user_keys.append(user.get("id") or user.get("_key"))

                if user_keys:
                    update_user_query = f"""
                    MATCH (u:{user_label})-[p:{permission_rel}]->(agent:{agent_label} {{id: $agent_id}})
                    WHERE u.id IN $user_keys
                    AND p.type = 'USER'
                    AND p.role <> 'OWNER'
                    SET p.role = $new_role, p.updatedAtTimestamp = $timestamp
                    RETURN count(p) AS updated
                    """

                    result = await self.client.execute_query(
                        update_user_query,
                        parameters={"agent_id": agent_id, "user_keys": user_keys, "new_role": role, "timestamp": timestamp},
                        txn_id=transaction
                    )
                    if result:
                        updated_users = result[0].get("updated", 0)
                        updated_count += updated_users

            # Update team permissions
            if team_ids:
                update_team_query = f"""
                MATCH (t:{team_label})-[p:{permission_rel}]->(agent:{agent_label} {{id: $agent_id}})
                WHERE t.id IN $team_ids
                AND p.type = 'TEAM'
                SET p.role = $new_role, p.updatedAtTimestamp = $timestamp
                RETURN count(p) AS updated
                """

                result = await self.client.execute_query(
                    update_team_query,
                    parameters={"agent_id": agent_id, "team_ids": team_ids, "new_role": role, "timestamp": timestamp},
                    txn_id=transaction
                )
                if result:
                    updated_teams = result[0].get("updated", 0)
                    updated_count += updated_teams

            if updated_count == 0:
                self.logger.warning(f"No permission edges found to update for agent {agent_id}")
                return {"success": False, "reason": "No permissions found to update"}

            self.logger.info(f"Successfully updated {updated_count} permissions for agent {agent_id} to role {role}")

            return {
                "success": True,
                "agent_id": agent_id,
                "new_role": role,
                "updated_permissions": updated_count,
                "updated_users": updated_users,
                "updated_teams": updated_teams
            }

        except Exception as e:
            self.logger.error(f"Failed to update agent permission: {str(e)}")
            return {"success": False, "reason": f"Internal error: {str(e)}"}


    async def get_agent_permissions(self, agent_id: str, user_id: str, transaction: Optional[str] = None) -> Optional[List[Dict]]:
        """Get all permissions for an agent (only OWNER can view all permissions)"""
        try:
            # Check if user has access to the agent
            agent_with_permission = await self.get_agent(agent_id, user_id, transaction=transaction)
            if agent_with_permission is None:
                self.logger.warning(f"No permission found for user {user_id} on agent {agent_id}")
                return None

            # Only OWNER can view all permissions
            if agent_with_permission.get("user_role") != "OWNER":
                self.logger.warning(f"User {user_id} is not the OWNER of agent {agent_id}")
                return None

            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)
            agent_label = collection_to_label(CollectionNames.AGENT_INSTANCES.value)

            # Get all permissions for the agent
            query = f"""
            MATCH (entity)-[p:{permission_rel}]->(agent:{agent_label} {{id: $agent_id}})
            RETURN {{
                id: entity.id,
                name: COALESCE(entity.fullName, entity.name, entity.userName),
                userId: entity.userId,
                email: entity.email,
                role: p.role,
                type: p.type,
                createdAtTimestamp: p.createdAtTimestamp,
                updatedAtTimestamp: p.updatedAtTimestamp
            }} AS permission
            """

            result = await self.client.execute_query(
                query,
                parameters={"agent_id": agent_id},
                txn_id=transaction
            )

            return [r["permission"] for r in result] if result else []

        except Exception as e:
            self.logger.error(f"Failed to get agent permissions: {str(e)}")
            return None

    async def get_all_agent_templates(self, user_id: str, transaction: Optional[str] = None) -> List[Dict]:
        """Get all agent templates accessible to a user via individual or team access"""
        try:
            template_label = collection_to_label(CollectionNames.AGENT_TEMPLATES.value)
            user_label = collection_to_label(CollectionNames.USERS.value)
            team_label = collection_to_label(CollectionNames.TEAMS.value)
            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)

            # user_id is the internal key (_key in ArangoDB, id in Neo4j)
            # Use it directly to match the user node
            user_key = user_id

            # Get user's teams
            user_teams_query = f"""
            MATCH (u:{user_label} {{id: $user_key}})-[p:{permission_rel}]->(t:{team_label})
            WHERE p.type = 'USER'
            RETURN t.id AS team_id
            """

            user_teams_result = await self.client.execute_query(
                user_teams_query,
                parameters={"user_key": user_key},
                txn_id=transaction
            )
            user_team_ids = [r["team_id"] for r in user_teams_result] if user_teams_result else []

            # Get templates with individual access
            individual_query = f"""
            MATCH (u:{user_label} {{id: $user_key}})-[p:{permission_rel}]->(template:{template_label})
            WHERE p.type = 'USER'
            AND (template.isDeleted IS NULL OR template.isDeleted = false)
            RETURN template, p.role AS role, p.type AS access_type, p.id AS permission_id,
                   p.createdAtTimestamp AS permission_created_at, p.updatedAtTimestamp AS permission_updated_at
            """

            individual_result = await self.client.execute_query(
                individual_query,
                parameters={"user_key": user_key},
                txn_id=transaction
            )

            templates = []
            individual_template_ids = set()

            if individual_result:
                for row in individual_result:
                    template_data = dict(row["template"])
                    template = self._neo4j_to_arango_node(template_data, CollectionNames.AGENT_TEMPLATES.value)
                    template["access_type"] = row["access_type"]
                    template["user_role"] = row["role"]
                    template["permission_id"] = row.get("permission_id")
                    template["permission_created_at"] = row.get("permission_created_at")
                    template["permission_updated_at"] = row.get("permission_updated_at")
                    template["can_edit"] = row["role"] in ["OWNER", "WRITER", "ORGANIZER"]
                    template["can_delete"] = row["role"] == "OWNER"
                    template["can_share"] = row["role"] in ["OWNER", "ORGANIZER"]
                    template["can_view"] = True
                    templates.append(template)
                    individual_template_ids.add(template.get("_id") or template.get("id"))

            # Get templates with team access (excluding those already found)
            if user_team_ids:
                excluded_keys = [tid.split("/")[-1] if "/" in tid else tid for tid in individual_template_ids]

                team_query = f"""
                MATCH (t:{team_label})-[p:{permission_rel}]->(template:{template_label})
                WHERE t.id IN $team_ids
                AND p.type = 'TEAM'
                AND (template.isDeleted IS NULL OR template.isDeleted = false)
                AND NOT template.id IN $excluded_ids
                RETURN template, p.role AS role, p.type AS access_type, p.id AS permission_id,
                    p.createdAtTimestamp AS permission_created_at, p.updatedAtTimestamp AS permission_updated_at
                """

                team_result = await self.client.execute_query(
                    team_query,
                    parameters={"team_ids": user_team_ids, "excluded_ids": excluded_keys},
                    txn_id=transaction
                )

                if team_result:
                    for row in team_result:
                        template_data = dict(row["template"])
                        template = self._neo4j_to_arango_node(template_data, CollectionNames.AGENT_TEMPLATES.value)
                        template["access_type"] = row["access_type"]
                        template["user_role"] = row["role"]
                        template["permission_id"] = row.get("permission_id")
                        template["permission_created_at"] = row.get("permission_created_at")
                        template["permission_updated_at"] = row.get("permission_updated_at")
                        template["can_edit"] = row["role"] in ["OWNER", "WRITER", "ORGANIZER"]
                        template["can_delete"] = row["role"] == "OWNER"
                        template["can_share"] = row["role"] in ["OWNER", "ORGANIZER"]
                        template["can_view"] = True
                        templates.append(template)

            return templates

        except Exception as e:
            self.logger.error("❌ Failed to get all agent templates: %s", str(e))
            return []

    async def get_template(self, template_id: str, user_id: str, transaction: Optional[str] = None) -> Optional[Dict]:
        """Get a template by ID with user permissions"""
        try:
            template_label = collection_to_label(CollectionNames.AGENT_TEMPLATES.value)
            user_label = collection_to_label(CollectionNames.USERS.value)
            team_label = collection_to_label(CollectionNames.TEAMS.value)
            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)

            # user_id is the internal key (_key in ArangoDB, id in Neo4j)
            # Use it directly to match the user node
            user_key = user_id

            # Get user document by id to extract userId for team queries
            user_query = f"""
            MATCH (u:{user_label} {{id: $user_key}})
            RETURN u
            LIMIT 1
            """
            user_result = await self.client.execute_query(
                user_query,
                parameters={"user_key": user_key},
                txn_id=transaction
            )

            # Get user's teams - need userId (external ID) for team lookup
            user_team_ids = []
            if user_result:
                try:
                    user_dict = dict(user_result[0]["u"])
                    user_doc = self._neo4j_to_arango_node(user_dict, CollectionNames.USERS.value)
                    actual_user_id = user_doc.get("userId")

                    if actual_user_id:
                        # Get user's teams using userId (external ID)
                        user_teams_query = f"""
                        MATCH (u:{user_label} {{userId: $user_id}})-[p:{permission_rel}]->(t:{team_label})
                        WHERE p.type = 'USER'
                        RETURN t.id AS team_id
                        """
                        user_teams_result = await self.client.execute_query(
                            user_teams_query,
                            parameters={"user_id": actual_user_id},
                            txn_id=transaction
                        )
                        user_team_ids = [r["team_id"] for r in user_teams_result] if user_teams_result else []
                except Exception as e:
                    self.logger.warning(f"Error getting user teams: {str(e)}")
                    # Continue without teams - individual permission check will still work

            # Check individual user permissions on the template
            individual_query = f"""
            MATCH (u:{user_label} {{id: $user_key}})-[p:{permission_rel}]->(template:{template_label} {{id: $template_id}})
            WHERE p.type = 'USER'
            AND (template.isDeleted IS NULL OR template.isDeleted = false)
            RETURN template, p.role AS role, 'INDIVIDUAL' AS access_type, p.id AS permission_id,
                   p.createdAtTimestamp AS permission_created_at, p.updatedAtTimestamp AS permission_updated_at
            LIMIT 1
            """

            individual_result = await self.client.execute_query(
                individual_query,
                parameters={"user_key": user_key, "template_id": template_id},
                txn_id=transaction
            )

            if individual_result:
                row = individual_result[0]
                template_data = dict(row["template"])
                template = self._neo4j_to_arango_node(template_data, CollectionNames.AGENT_TEMPLATES.value)
                template["access_type"] = "INDIVIDUAL"
                template["user_role"] = row["role"]
                template["permission_id"] = row.get("permission_id")
                template["permission_created_at"] = row.get("permission_created_at")
                template["permission_updated_at"] = row.get("permission_updated_at")
                template["can_edit"] = row["role"] in ["OWNER", "WRITER", "ORGANIZER"]
                template["can_delete"] = row["role"] == "OWNER"
                template["can_share"] = row["role"] in ["OWNER", "ORGANIZER"]
                template["can_view"] = True
                return template

            # Check team permissions
            if user_team_ids:
                team_query = f"""
                MATCH (t:{team_label})-[p:{permission_rel}]->(template:{template_label} {{id: $template_id}})
                WHERE t.id IN $team_ids
                AND p.type = 'TEAM'
                AND (template.isDeleted IS NULL OR template.isDeleted = false)
                RETURN template, p.role AS role, 'TEAM' AS access_type, p.id AS permission_id,
                       p.createdAtTimestamp AS permission_created_at, p.updatedAtTimestamp AS permission_updated_at
                LIMIT 1
                """

                team_result = await self.client.execute_query(
                    team_query,
                    parameters={"team_ids": user_team_ids, "template_id": template_id},
                    txn_id=transaction
                )

                if team_result:
                    row = team_result[0]
                    template_data = dict(row["template"])
                    template = self._neo4j_to_arango_node(template_data, CollectionNames.AGENT_TEMPLATES.value)
                    template["access_type"] = "TEAM"
                    template["user_role"] = row["role"]
                    template["permission_id"] = row.get("permission_id")
                    template["permission_created_at"] = row.get("permission_created_at")
                    template["permission_updated_at"] = row.get("permission_updated_at")
                    template["can_edit"] = row["role"] in ["OWNER", "WRITER", "ORGANIZER"]
                    template["can_delete"] = row["role"] == "OWNER"
                    template["can_share"] = row["role"] in ["OWNER", "ORGANIZER"]
                    template["can_view"] = True
                    return template

            return None

        except Exception as e:
            self.logger.error("❌ Failed to get template access: %s", str(e))
            return None

    async def share_agent_template(self, template_id: str, user_id: str, user_ids: Optional[List[str]] = None, team_ids: Optional[List[str]] = None, transaction: Optional[str] = None) -> Optional[bool]:
        """Share an agent template with users"""
        try:
            self.logger.info(f"Sharing agent template {template_id} with users {user_ids}")

            template_label = collection_to_label(CollectionNames.AGENT_TEMPLATES.value)
            user_label = collection_to_label(CollectionNames.USERS.value)
            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)

            # user_id is the internal key (_key in ArangoDB, id in Neo4j)
            # Use it directly to match the user node
            user_key = user_id

            # Check if user is OWNER
            owner_check_query = f"""
            MATCH (u:{user_label} {{id: $user_key}})-[p:{permission_rel}]->(template:{template_label} {{id: $template_id}})
            WHERE (template.isDeleted IS NULL OR template.isDeleted = false)
            RETURN p.role AS role
            LIMIT 1
            """

            owner_result = await self.client.execute_query(
                owner_check_query,
                parameters={"user_key": user_key, "template_id": template_id},
                txn_id=transaction
            )

            if not owner_result or owner_result[0].get("role") != "OWNER":
                return False

            if user_ids is None and team_ids is None:
                return False

            # Create edges for users and teams
            edges = []
            if user_ids:
                for user_id_to_share in user_ids:
                    user = await self.get_user_by_user_id(user_id_to_share)
                    if user is None:
                        return False
                    user_key_to_share = user.get("id") or user.get("_key")
                    edge = {
                        "_from": f"{CollectionNames.USERS.value}/{user_key_to_share}",
                        "_to": f"{CollectionNames.AGENT_TEMPLATES.value}/{template_id}",
                        "type": "USER",
                        "role": "READER",
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    }
                    edges.append(edge)

            if team_ids:
                for team_id in team_ids:
                    team_key = team_id  # Assuming team_id is already the key
                    edge = {
                        "_from": f"{CollectionNames.TEAMS.value}/{team_key}",
                        "_to": f"{CollectionNames.AGENT_TEMPLATES.value}/{template_id}",
                        "type": "TEAM",
                        "role": "READER",
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    }
                    edges.append(edge)

            result = await self.batch_create_edges(edges, CollectionNames.PERMISSION.value, transaction=transaction)
            return result is not False

        except Exception as e:
            self.logger.error("❌ Failed to share agent template: %s", str(e))
            return False

    async def clone_agent_template(self, template_id: str, transaction: Optional[str] = None) -> Optional[str]:
        """Clone an agent template"""
        try:
            template = await self.get_document(template_id, CollectionNames.AGENT_TEMPLATES.value, transaction=transaction)
            if template is None:
                return None
            template_key = str(uuid.uuid4())
            template["_key"] = template_key
            template["id"] = template_key
            template["isActive"] = True
            template["isDeleted"] = False
            template["deletedAtTimestamp"] = None
            template["deletedByUserId"] = None
            template["updatedAtTimestamp"] = get_epoch_timestamp_in_ms()
            template["updatedByUserId"] = None
            template["createdAtTimestamp"] = get_epoch_timestamp_in_ms()
            template["createdBy"] = None
            result = await self.batch_upsert_nodes([template], CollectionNames.AGENT_TEMPLATES.value, transaction=transaction)
            if not result:
                return None
            return template_key
        except Exception as e:
            self.logger.error("❌ Failed to clone agent template: %s", str(e))
            return None

    async def delete_agent_template(self, template_id: str, user_id: str, transaction: Optional[str] = None) -> Optional[bool]:
        """Delete an agent template"""
        try:
            template_label = collection_to_label(CollectionNames.AGENT_TEMPLATES.value)
            user_label = collection_to_label(CollectionNames.USERS.value)
            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)

            # user_id is the internal key (_key in ArangoDB, id in Neo4j)
            # Use it directly to match the user node
            user_key = user_id

            # Check if user is OWNER
            permission_query = f"""
            MATCH (u:{user_label} {{id: $user_key}})-[p:{permission_rel}]->(template:{template_label} {{id: $template_id}})
            WHERE (template.isDeleted IS NULL OR template.isDeleted = false)
            RETURN p.role AS role
            LIMIT 1
            """

            permissions = await self.client.execute_query(
                permission_query,
                parameters={"user_key": user_key, "template_id": template_id},
                txn_id=transaction
            )

            if not permissions or permissions[0].get("role") != "OWNER":
                self.logger.warning(f"User {user_id} is not the owner of template {template_id}")
                return False

            # Check if template exists
            template = await self.get_document(template_id, CollectionNames.AGENT_TEMPLATES.value, transaction=transaction)
            if template is None:
                self.logger.warning(f"Template {template_id} not found")
                return False

            # Prepare update data for soft delete
            update_data = {
                "isDeleted": True,
                "deletedAtTimestamp": get_epoch_timestamp_in_ms(),
                "deletedByUserId": user_id
            }

            # Soft delete the template using update_node
            result = await self.update_node(template_id, CollectionNames.AGENT_TEMPLATES.value, update_data, transaction=transaction)

            if not result:
                self.logger.error(f"Failed to delete template {template_id}")
                return False

            self.logger.info(f"Successfully deleted template {template_id}")
            return True

        except Exception as e:
            self.logger.error("❌ Failed to delete agent template: %s", str(e), exc_info=True)
            return False

    async def update_agent_template(self, template_id: str, template_updates: Dict[str, Any], user_id: str, transaction: Optional[str] = None) -> Optional[bool]:
        """Update an agent template"""
        try:
            template_label = collection_to_label(CollectionNames.AGENT_TEMPLATES.value)
            user_label = collection_to_label(CollectionNames.USERS.value)
            permission_rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)

            # user_id is the internal key (_key in ArangoDB, id in Neo4j)
            # Use it directly to match the user node
            user_key = user_id

            # Check if user is OWNER
            permission_query = f"""
            MATCH (u:{user_label} {{id: $user_key}})-[p:{permission_rel}]->(template:{template_label} {{id: $template_id}})
            WHERE (template.isDeleted IS NULL OR template.isDeleted = false)
            RETURN p.role AS role
            LIMIT 1
            """

            permissions = await self.client.execute_query(
                permission_query,
                parameters={"user_key": user_key, "template_id": template_id},
                txn_id=transaction
            )

            if not permissions or permissions[0].get("role") != "OWNER":
                self.logger.warning(f"User {user_id} is not the owner of template {template_id}")
                return False

            # Prepare update data
            update_data = {
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedByUserId": user_id
            }

            # Add only the fields that are provided
            allowed_fields = ["name", "description", "startMessage", "systemPrompt", "tools", "models", "memory", "tags"]
            for field in allowed_fields:
                if field in template_updates:
                    update_data[field] = template_updates[field]

            # Update the template using update_node
            result = await self.update_node(template_id, CollectionNames.AGENT_TEMPLATES.value, update_data, transaction=transaction)

            if not result:
                self.logger.error(f"Failed to update template {template_id}")
                return False

            self.logger.info(f"Successfully updated template {template_id}")
            return True

        except Exception as e:
            self.logger.error("❌ Failed to update agent template: %s", str(e))
            return False
