"""
ArangoDB HTTP Provider Implementation

Fully async implementation of IGraphDBProvider using ArangoDB REST API.
This replaces the synchronous python-arango SDK with async HTTP calls.

All operations are non-blocking and use aiohttp for async I/O.
"""
import asyncio
import unicodedata
import uuid
from logging import Logger
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

from app.config.configuration_service import ConfigurationService
from app.connectors.services.kafka_service import KafkaService

if TYPE_CHECKING:
    from fastapi import Request


from app.config.constants.arangodb import (
    RECORD_TYPE_COLLECTION_MAPPING,
    CollectionNames,
    Connectors,
    DepartmentNames,
    GraphNames,
    OriginTypes,
    ProgressStatus,
    RecordTypes,
)
from app.config.constants.service import DefaultEndpoints, config_node_constants
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
from app.schema.arango.graph import EDGE_DEFINITIONS
from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Constants for ArangoDB document ID format
ARANGO_ID_PARTS_COUNT = 2  # ArangoDB document IDs are in format "collection/key"
MAX_REINDEX_DEPTH = 100  # Maximum depth for reindexing records (unlimited depth is capped at this value)


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
        kafka_service: Optional[KafkaService] = None,
    ) -> None:
        """
        Initialize ArangoDB HTTP provider.

        Args:
            logger: Logger instance
            config_service: Configuration service for database credentials
            kafka_service: Optional Kafka service for event publishing
        """
        self.logger = logger
        self.config_service = config_service
        self.kafka_service = kafka_service
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

    async def ensure_schema(self) -> bool:
        """
        Ensure database schema is initialized (collections, graph, and departments seed).
        Should be called only from the connector service during startup when schema init is enabled.
        """
        if not self.http_client:
            self.logger.error("Cannot ensure schema: not connected")
            return False

        try:
            self.logger.info("🚀 Ensuring ArangoDB schema (collections, graph, departments)...")

            # 1. Create all collections (node + edge)
            edge_collection_names = {ed["edge_collection"] for ed in EDGE_DEFINITIONS}
            for col in CollectionNames:
                name = col.value
                is_edge = name in edge_collection_names
                if not await self.http_client.has_collection(name):
                    if not await self.http_client.create_collection(name, edge=is_edge):
                        self.logger.warning(f"Failed to create collection '{name}', continuing")
                else:
                    self.logger.debug(f"Collection '{name}' already exists")

            # 2. Create knowledge graph if it doesn't exist
            has_knowledge = await self.http_client.has_graph(GraphNames.KNOWLEDGE_GRAPH.value)
            if not has_knowledge:
                # Only add edge definitions whose collections exist
                valid_definitions = [
                    ed for ed in EDGE_DEFINITIONS
                    if await self.http_client.has_collection(ed["edge_collection"])
                ]
                if valid_definitions:
                    if not await self.http_client.create_graph(
                        GraphNames.KNOWLEDGE_GRAPH.value,
                        valid_definitions,
                    ):
                        self.logger.warning("Failed to create knowledge graph, continuing")
                else:
                    self.logger.warning("No edge collections found for graph creation")
            else:
                self.logger.info("Knowledge graph already exists, skipping creation")

            # 3. Seed departments collection with predefined department types
            await self._ensure_departments_seed()

            self.logger.info("✅ ArangoDB schema ensured successfully")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error ensuring schema: {str(e)}")
            return False

    async def _ensure_departments_seed(self) -> None:
        """Initialize departments collection with predefined department types if missing."""
        try:
            existing = await self.execute_query(
                f"FOR d IN {CollectionNames.DEPARTMENTS.value} RETURN d.departmentName",
                {},
            )
            existing_names = set() if not existing else {r for r in existing if r is not None}

            new_departments = [
                {"id": str(uuid.uuid4()), "departmentName": dept.value, "orgId": None}
                for dept in DepartmentNames
                if dept.value not in existing_names
            ]

            if new_departments:
                self.logger.info(f"🚀 Inserting {len(new_departments)} department(s)")
                await self.batch_upsert_nodes(
                    new_departments,
                    CollectionNames.DEPARTMENTS.value,
                )
                self.logger.info("✅ Departments seed completed")
        except Exception as e:
            self.logger.warning(f"Departments seed failed (non-fatal): {str(e)}")

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

    def _create_typed_record_from_arango(
        self, record_dict: Dict, type_doc: Optional[Dict]
    ) -> Record:
        """
        Build a typed Record (FileRecord, MailRecord, etc.) from Arango record + type doc.
        Matches BaseArangoService._create_typed_record_from_arango for same return type.
        """
        record_type = record_dict.get("recordType")

        if not type_doc or record_type not in RECORD_TYPE_COLLECTION_MAPPING:
            return Record.from_arango_base_record(record_dict)

        try:
            collection = RECORD_TYPE_COLLECTION_MAPPING[record_type]

            if collection == CollectionNames.FILES.value:
                return FileRecord.from_arango_record(type_doc, record_dict)
            if collection == CollectionNames.MAILS.value:
                return MailRecord.from_arango_record(type_doc, record_dict)
            if collection == CollectionNames.WEBPAGES.value:
                return WebpageRecord.from_arango_record(type_doc, record_dict)
            if collection == CollectionNames.TICKETS.value:
                return TicketRecord.from_arango_record(type_doc, record_dict)
            if collection == CollectionNames.COMMENTS.value:
                return CommentRecord.from_arango_record(type_doc, record_dict)
            if collection == CollectionNames.LINKS.value:
                return LinkRecord.from_arango_record(type_doc, record_dict)
            if collection == CollectionNames.PROJECTS.value:
                return ProjectRecord.from_arango_record(type_doc, record_dict)
            return Record.from_arango_base_record(record_dict)
        except Exception as e:
            self.logger.warning(
                "Failed to create typed record for %s, falling back to base Record: %s",
                record_type,
                str(e),
            )
            return Record.from_arango_base_record(record_dict)

    async def get_record_by_id(
        self,
        id: str,
        transaction: Optional[str] = None,
    ) -> Optional[Record]:
        """
        Get record by internal ID (_key) with associated type document (file/mail/etc.).

        Args:
            id: Internal record ID (_key)
            transaction: Optional transaction ID

        Returns:
            Optional[Record]: Typed Record instance (FileRecord, MailRecord, etc.) or None
        """
        try:
            query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record._key == @id
                LET typeDoc = (
                    FOR edge IN {CollectionNames.IS_OF_TYPE.value}
                        FILTER edge._from == record._id
                        LET doc = DOCUMENT(edge._to)
                        FILTER doc != null
                        RETURN doc
                )[0]
                RETURN {{
                    record: record,
                    typeDoc: typeDoc
                }}
            """
            results = await self.execute_query(
                query,
                bind_vars={"id": id},
                transaction=transaction,
            )
            if not results:
                return None
            result = results[0]
            return self._create_typed_record_from_arango(
                result["record"],
                result.get("typeDoc"),
            )
        except Exception as e:
            self.logger.error("❌ Failed to get record by id %s: %s", id, str(e))
            return None

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
        try:
            query = """
            LET org_id = @org_id
            LET connector = FIRST(
                FOR doc IN @@apps
                    FILTER doc._key == @connector_id
                    RETURN doc
            )
            LET records = (
                FOR doc IN @@records
                    FILTER doc.orgId == org_id
                    FILTER doc.origin == "CONNECTOR"
                    FILTER doc.connectorId == @connector_id
                    FILTER doc.recordType != @drive_record_type
                    FILTER doc.isDeleted != true
                    LET targetDoc = FIRST(
                        FOR v IN 1..1 OUTBOUND doc._id @@is_of_type
                            LIMIT 1
                            RETURN v
                    )
                    FILTER targetDoc == null OR NOT IS_SAME_COLLECTION("files", targetDoc._id) OR targetDoc.isFile == true
                    RETURN doc
            )
            LET total_stats = {
                total: LENGTH(records),
                indexingStatus: {
                    NOT_STARTED: LENGTH(records[* FILTER CURRENT.indexingStatus == "NOT_STARTED"]),
                    IN_PROGRESS: LENGTH(records[* FILTER CURRENT.indexingStatus == "IN_PROGRESS"]),
                    COMPLETED: LENGTH(records[* FILTER CURRENT.indexingStatus == "COMPLETED"]),
                    FAILED: LENGTH(records[* FILTER CURRENT.indexingStatus == "FAILED"]),
                    FILE_TYPE_NOT_SUPPORTED: LENGTH(records[* FILTER CURRENT.indexingStatus == "FILE_TYPE_NOT_SUPPORTED"]),
                    AUTO_INDEX_OFF: LENGTH(records[* FILTER CURRENT.indexingStatus == "AUTO_INDEX_OFF"]),
                    ENABLE_MULTIMODAL_MODELS: LENGTH(records[* FILTER CURRENT.indexingStatus == "ENABLE_MULTIMODAL_MODELS"]),
                    EMPTY: LENGTH(records[* FILTER CURRENT.indexingStatus == "EMPTY"]),
                    QUEUED: LENGTH(records[* FILTER CURRENT.indexingStatus == "QUEUED"]),
                    PAUSED: LENGTH(records[* FILTER CURRENT.indexingStatus == "PAUSED"]),
                    CONNECTOR_DISABLED: LENGTH(records[* FILTER CURRENT.indexingStatus == "CONNECTOR_DISABLED"]),
                }
            }
            LET by_record_type = (
                FOR record_type IN UNIQUE(records[*].recordType)
                    FILTER record_type != null
                    LET type_records = records[* FILTER CURRENT.recordType == record_type]
                    RETURN {
                        recordType: record_type,
                        total: LENGTH(type_records),
                        indexingStatus: {
                            NOT_STARTED: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "NOT_STARTED"]),
                            IN_PROGRESS: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "IN_PROGRESS"]),
                            COMPLETED: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "COMPLETED"]),
                            FAILED: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "FAILED"]),
                            FILE_TYPE_NOT_SUPPORTED: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "FILE_TYPE_NOT_SUPPORTED"]),
                            AUTO_INDEX_OFF: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "AUTO_INDEX_OFF"]),
                            ENABLE_MULTIMODAL_MODELS: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "ENABLE_MULTIMODAL_MODELS"]),
                            EMPTY: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "EMPTY"]),
                            QUEUED: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "QUEUED"]),
                            PAUSED: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "PAUSED"]),
                            CONNECTOR_DISABLED: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "CONNECTOR_DISABLED"]),
                        }
                    }
            )
            RETURN {
                orgId: org_id,
                connectorId: @connector_id,
                origin: "CONNECTOR",
                stats: total_stats,
                byRecordType: by_record_type
            }
            """
            bind_vars = {
                "org_id": org_id,
                "connector_id": connector_id,
                "@records": CollectionNames.RECORDS.value,
                "drive_record_type": RecordTypes.DRIVE.value,
                "@apps": CollectionNames.APPS.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
            }
            results = await self.execute_query(query, bind_vars=bind_vars)
            if results:
                return {
                    "success": True,
                    "data": results[0],
                }
            return {
                "success": False,
                "message": "No data found for the specified connector",
                "data": {
                    "org_id": org_id,
                    "connector_id": connector_id,
                    "origin": "CONNECTOR",
                    "stats": {
                        "total": 0,
                        "indexingStatus": {
                            "NOT_STARTED": 0,
                            "IN_PROGRESS": 0,
                            "COMPLETED": 0,
                            "FAILED": 0,
                            "FILE_TYPE_NOT_SUPPORTED": 0,
                            "AUTO_INDEX_OFF": 0,
                            "ENABLE_MULTIMODAL_MODELS": 0,
                            "EMPTY": 0,
                            "QUEUED": 0,
                            "PAUSED": 0,
                            "CONNECTOR_DISABLED": 0,
                        },
                    },
                    "byRecordType": [],
                },
            }
        except Exception as e:
            self.logger.error("❌ Error getting connector stats: %s", str(e))
            return {
                "success": False,
                "message": str(e),
                "data": None,
            }

    async def _check_record_group_permissions(
        self,
        record_group_id: str,
        user_key: str,
        org_id: str,
    ) -> Dict:
        """
        Check if user has permission to access a record group.

        Returns:
            Dict with 'allowed' (bool), 'reason' (str), and optionally 'role'
        """
        try:
            query = """
            LET userDoc = DOCUMENT(@@user_collection, @user_key)
            FILTER userDoc != null
            LET recordGroup = DOCUMENT(@@record_group_collection, @record_group_id)
            FILTER recordGroup != null
            FILTER recordGroup.orgId == @org_id
            LET directPermission = (
                FOR perm IN @@permission
                    FILTER perm._from == userDoc._id
                    FILTER perm._to == recordGroup._id
                    FILTER perm.type == "USER"
                    RETURN perm.role
            )
            LET groupPermission = (
                FOR group, userToGroupEdge IN 1..1 ANY userDoc._id @@permission
                    FILTER userToGroupEdge.type == "USER"
                    FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                    FOR perm IN @@permission
                        FILTER perm._from == group._id
                        FILTER perm._to == recordGroup._id
                        FILTER perm.type IN ["GROUP", "ROLE"]
                        RETURN perm.role
            )
            LET orgPermission = (
                FOR org, belongsEdge IN 1..1 ANY userDoc._id @@belongs_to
                    FILTER belongsEdge.entityType == "ORGANIZATION"
                    FOR perm IN @@permission
                        FILTER perm._from == org._id
                        FILTER perm._to == recordGroup._id
                        FILTER perm.type == "ORG"
                        RETURN perm.role
            )
            LET allPermissions = UNION_DISTINCT(directPermission, groupPermission, orgPermission)
            LET hasPermission = LENGTH(allPermissions) > 0
            LET rolePriority = { "OWNER": 4, "WRITER": 3, "READER": 2, "COMMENTER": 1 }
            LET userRole = LENGTH(allPermissions) > 0 ? (
                FIRST(FOR perm IN allPermissions SORT rolePriority[perm] DESC LIMIT 1 RETURN perm)
            ) : null
            RETURN { allowed: hasPermission, role: userRole }
            """
            bind_vars = {
                "@user_collection": CollectionNames.USERS.value,
                "@record_group_collection": CollectionNames.RECORD_GROUPS.value,
                "@permission": CollectionNames.PERMISSION.value,
                "@belongs_to": CollectionNames.BELONGS_TO.value,
                "user_key": user_key,
                "record_group_id": record_group_id,
                "org_id": org_id,
            }
            results = await self.execute_query(query, bind_vars=bind_vars)
            result = results[0] if results else None
            if result and result.get("allowed"):
                return {
                    "allowed": True,
                    "role": result.get("role"),
                    "reason": "User has permission to access record group",
                }
            return {
                "allowed": False,
                "role": None,
                "reason": "User does not have permission to access this record group",
            }
        except Exception as e:
            self.logger.error("❌ Error checking record group permissions: %s", str(e))
            return {"allowed": False, "role": None, "reason": str(e)}

    # ==================== Connector Registry Operations ====================

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

            if scope == "personal":
                query = """
                FOR doc IN @@collection
                    FILTER doc.scope == @scope
                    FILTER doc.createdBy == @user_id
                    FILTER LOWER(TRIM(doc.name)) == @normalized_name
                    LIMIT 1
                    RETURN doc._key
                """
                bind_vars = {
                    "@collection": collection,
                    "scope": scope,
                    "user_id": user_id,
                    "normalized_name": normalized_name,
                }
            else:  # team scope
                query = """
                FOR edge IN @@edge_collection
                    FILTER edge._from == @org_id
                    FOR doc IN @@collection
                        FILTER doc._id == edge._to
                        FILTER doc.scope == @scope
                        FILTER LOWER(TRIM(doc.name)) == @normalized_name
                        LIMIT 1
                        RETURN doc._key
                """
                bind_vars = {
                    "@collection": collection,
                    "@edge_collection": CollectionNames.ORG_APP_RELATION.value,
                    "org_id": f"{CollectionNames.ORGS.value}/{org_id}",
                    "scope": scope,
                    "normalized_name": normalized_name,
                }

            results = await self.execute_query(query, bind_vars=bind_vars, transaction=transaction)
            return len(results) > 0

        except Exception as e:
            self.logger.error(f"Failed to check connector name exists: {e}")
            return False

    async def batch_update_connector_status(
        self,
        collection: str,
        connector_keys: List[str],
        is_active: bool,
        is_agent_active: bool,
        transaction: Optional[str] = None,
    ) -> int:
        """Batch update isActive and isAgentActive status for multiple connectors."""
        try:
            if not connector_keys:
                return 0

            current_timestamp = get_epoch_timestamp_in_ms()

            query = """
            FOR doc IN @@collection
                FILTER doc._key IN @keys
                UPDATE doc WITH {
                    isActive: @is_active,
                    isAgentActive: @is_agent_active,
                    updatedAtTimestamp: @timestamp
                } IN @@collection
                RETURN NEW
            """
            bind_vars = {
                "@collection": collection,
                "keys": connector_keys,
                "is_active": is_active,
                "is_agent_active": is_agent_active,
                "timestamp": current_timestamp,
            }
            results = await self.execute_query(query, bind_vars=bind_vars, transaction=transaction)
            return len(results)

        except Exception as e:
            self.logger.error(f"Failed to batch update connector status: {e}")
            return 0

    async def get_user_connector_instances(
        self,
        collection: str,
        user_id: str,
        org_id: str,
        team_scope: str,
        personal_scope: str,
        transaction: Optional[str] = None,
    ) -> List[Dict]:
        """Get all connector instances accessible to a user (personal + team)."""
        try:
            query = """
            FOR doc IN @@collection
                FILTER doc._id != null
                FILTER (
                    doc.scope == @team_scope OR
                    (doc.scope == @personal_scope AND doc.createdBy == @user_id)
                )
                RETURN doc
            """
            bind_vars = {
                "@collection": collection,
                "team_scope": team_scope,
                "personal_scope": personal_scope,
                "user_id": user_id,
            }
            results = await self.execute_query(query, bind_vars=bind_vars, transaction=transaction)
            return results or []

        except Exception as e:
            self.logger.error(f"Failed to get user connector instances: {e}")
            return []

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
            # Build base query
            query = """
            FOR doc IN @@collection
                FILTER doc._id != null
            """
            bind_vars = {
                "@collection": collection,
            }

            # Exclude KB if requested
            if exclude_kb and kb_connector_type:
                query += " FILTER doc.type != @kb_connector_type\n"
                bind_vars["kb_connector_type"] = kb_connector_type

            # Scope filter
            if scope == "personal":
                query += " FILTER doc.scope == @scope\n"
                query += " FILTER (doc.createdBy == @user_id)\n"
                bind_vars["scope"] = scope
                bind_vars["user_id"] = user_id
            elif scope == "team":
                query += " FILTER (doc.scope == @team_scope) OR (doc.createdBy == @user_id)\n"
                bind_vars["team_scope"] = "team"
                bind_vars["user_id"] = user_id

            # Search filter
            if search:
                query += " FILTER (LOWER(doc.name) LIKE @search) OR (LOWER(doc.type) LIKE @search) OR (LOWER(doc.appGroup) LIKE @search)\n"
                bind_vars["search"] = f"%{search.lower()}%"

            # Count query
            count_query = query + " COLLECT WITH COUNT INTO total RETURN total"
            count_result = await self.execute_query(count_query, bind_vars=bind_vars, transaction=transaction)
            total_count = count_result[0] if count_result else 0

            # Scope counts (personal and team)
            scope_counts = {"personal": 0, "team": 0}

            # Personal count
            personal_count_query = """
            FOR doc IN @@collection
                FILTER doc._id != null
                FILTER doc.scope == @personal_scope
                FILTER doc.createdBy == @user_id
                FILTER doc.isConfigured == true
                COLLECT WITH COUNT INTO total
                RETURN total
            """
            personal_bind_vars = {
                "@collection": collection,
                "personal_scope": "personal",
                "user_id": user_id,
            }
            personal_result = await self.execute_query(personal_count_query, bind_vars=personal_bind_vars, transaction=transaction)
            scope_counts["personal"] = personal_result[0] if personal_result else 0

            # Team count (if admin or has team access)
            if is_admin or scope == "team":
                team_count_query = """
                FOR doc IN @@collection
                    FILTER doc._id != null
                    FILTER doc.type != @kb_connector_type
                    FILTER doc.scope == @team_scope
                    FILTER doc.isConfigured == true
                    COLLECT WITH COUNT INTO total
                    RETURN total
                """
                team_bind_vars = {
                    "@collection": collection,
                    "kb_connector_type": kb_connector_type or "",
                    "team_scope": "team",
                }
                team_result = await self.execute_query(team_count_query, bind_vars=team_bind_vars, transaction=transaction)
                scope_counts["team"] = team_result[0] if team_result else 0

            # Main query with pagination
            query += " LIMIT @skip, @limit\n RETURN doc"
            bind_vars["skip"] = skip
            bind_vars["limit"] = limit

            documents = await self.execute_query(query, bind_vars=bind_vars, transaction=transaction) or []

            return documents, total_count, scope_counts

        except Exception as e:
            self.logger.error(f"Failed to get filtered connector instances: {e}")
            return [], 0, {"personal": 0, "team": 0}

    async def reindex_record_group_records(
        self,
        record_group_id: str,
        depth: int,
        user_id: str,
        org_id: str,
    ) -> Dict:
        """
        Validate record group and user permissions for reindexing.
        Does NOT publish events; caller (router/service) should publish.

        Returns:
            Dict with success, connectorId, connectorName, depth, recordGroupId, or error code/reason
        """
        try:
            if depth == -1:
                depth = MAX_REINDEX_DEPTH
            elif depth < 0:
                depth = 0
            record_group = await self.get_document(record_group_id, CollectionNames.RECORD_GROUPS.value)
            if not record_group:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"Record group not found: {record_group_id}",
                }
            rg = record_group if isinstance(record_group, dict) else {}
            connector_id = rg.get("connectorId") or rg.get("id") or ""
            connector_name = rg.get("connectorName") or ""
            if not connector_id or not connector_name:
                return {
                    "success": False,
                    "code": 400,
                    "reason": "Record group does not have a connector id or name",
                }
            user = await self.get_user_by_user_id(user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found: {user_id}",
                }
            user_key = user.get("_key") or user.get("id")
            if not user_key:
                return {"success": False, "code": 404, "reason": "User key not found"}
            permission_check = await self._check_record_group_permissions(
                record_group_id, user_key, org_id
            )
            if not permission_check.get("allowed"):
                return {
                    "success": False,
                    "code": 403,
                    "reason": permission_check.get("reason", "Permission denied"),
                }
            return {
                "success": True,
                "connectorId": connector_id,
                "connectorName": connector_name,
                "depth": depth,
                "recordGroupId": record_group_id,
            }
        except Exception as e:
            self.logger.error("❌ Failed to validate record group reindex: %s", str(e))
            return {"success": False, "code": 500, "reason": str(e)}

    async def _reset_indexing_status_to_queued(self, record_id: str) -> None:
        """Reset record indexing status to QUEUED (only if not already QUEUED or EMPTY)."""
        try:
            record = await self.get_document(record_id, CollectionNames.RECORDS.value)
            if not record:
                return
            current_status = record.get("indexingStatus")
            if current_status in ("QUEUED", "EMPTY"):
                return
            doc = {"_key": record_id, "indexingStatus": "QUEUED"}
            await self.batch_upsert_nodes([doc], CollectionNames.RECORDS.value)
        except Exception as e:
            self.logger.error("❌ Failed to reset record %s to QUEUED: %s", record_id, str(e))

    async def _check_record_permissions(
        self,
        record_id: str,
        user_key: str,
        check_drive_inheritance: bool = True,
    ) -> Dict:
        """
        Check user permission on a record (direct, group, record group, domain, anyone, drive).
        Returns Dict with 'permission' (role) and 'source'.
        """
        try:
            user_from = f"{CollectionNames.USERS.value}/{user_key}"
            record_from = f"{CollectionNames.RECORDS.value}/{record_id}"
            permission_query = """
            LET user_from = @user_from
            LET record_from = @record_from
            LET direct_permission = FIRST(
                FOR perm IN @@permission
                    FILTER perm._from == user_from AND perm._to == record_from AND perm.type == "USER"
                    RETURN perm.role
            )
            LET group_permission = FIRST(
                FOR permission IN @@permission
                    FILTER permission._from == user_from
                    LET group = DOCUMENT(permission._to)
                    FILTER group != null
                    FOR perm IN @@permission
                        FILTER perm._from == group._id AND perm._to == record_from
                        RETURN perm.role
            )
            LET record_group_permission = FIRST(
                FOR group, userToGroupEdge IN 1..1 ANY user_from @@permission
                    FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                    FOR recordGroup, groupToRecordGroupEdge IN 1..1 ANY group._id @@permission
                    FOR rec, recordGroupToRecordEdge IN 1..1 INBOUND recordGroup._id @@inherit_permissions
                        FILTER rec._id == record_from
                        RETURN groupToRecordGroupEdge.role
            )
            LET direct_user_record_group_permission = FIRST(
                FOR recordGroup, userToRgEdge IN 1..1 ANY user_from @@permission
                    FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                    FOR record, edge, path IN 0..5 INBOUND recordGroup._id @@inherit_permissions
                        FILTER record._id == record_from AND IS_SAME_COLLECTION("records", record)
                        LET finalEdge = LENGTH(path.edges) > 0 ? path.edges[LENGTH(path.edges) - 1] : edge
                        RETURN userToRgEdge.role
            )
            LET inherited_record_group_permission = FIRST(
                FOR recordGroup, inheritEdge, path IN 0..5 OUTBOUND record_from @@inherit_permissions
                    FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                    FOR perm IN @@permission
                        FILTER perm._from == user_from AND perm._to == recordGroup._id AND perm.type == "USER"
                        RETURN perm.role
            )
            LET domain_permission = FIRST(
                FOR belongs_edge IN @@belongs_to
                    FILTER belongs_edge._from == user_from AND belongs_edge.entityType == "ORGANIZATION"
                    LET org = DOCUMENT(belongs_edge._to)
                    FILTER org != null
                    FOR perm IN @@permission
                        FILTER perm._from == org._id AND perm._to == record_from AND perm.type IN ["DOMAIN", "ORG"]
                        RETURN perm.role
            )
            LET final_permission = (
                direct_permission ? direct_permission :
                inherited_record_group_permission ? inherited_record_group_permission :
                group_permission ? group_permission :
                record_group_permission ? record_group_permission :
                direct_user_record_group_permission ? direct_user_record_group_permission :
                domain_permission ? domain_permission : null
            )
            RETURN {
                permission: final_permission,
                source: (
                    direct_permission ? "DIRECT" :
                    inherited_record_group_permission ? "INHERITED_RECORD_GROUP" :
                    group_permission ? "GROUP" :
                    record_group_permission ? "RECORD_GROUP" :
                    direct_user_record_group_permission ? "DIRECT_USER_RECORD_GROUP" :
                    domain_permission ? "DOMAIN" : "NONE"
                )
            }
            """
            bind_vars = {
                "user_from": user_from,
                "record_from": record_from,
                "@permission": CollectionNames.PERMISSION.value,
                "@belongs_to": CollectionNames.BELONGS_TO.value,
                "@inherit_permissions": CollectionNames.INHERIT_PERMISSIONS.value,
            }
            results = await self.execute_query(permission_query, bind_vars=bind_vars)
            result = results[0] if results else None
            if result and result.get("permission"):
                return {"permission": result["permission"], "source": result.get("source", "NONE")}
            return {"permission": None, "source": "NONE"}
        except Exception as e:
            self.logger.error("❌ Failed to check record permissions: %s", str(e))
            return {"permission": None, "source": "ERROR", "error": str(e)}

    async def reindex_single_record(
        self,
        record_id: str,
        user_id: str,
        org_id: str,
        request: Optional["Request"] = None,
        depth: int = 0,
    ) -> Dict:
        """
        Reindex a single record with permission checks and event publishing.
        If the record is a folder and depth > 0, also reindex children up to specified depth.

        Args:
            record_id: Record ID to reindex
            user_id: External user ID
            org_id: Organization ID
            request: Optional request (unused in provider; for signature compatibility)
            depth: Depth for children (0 = only this record, -1 = unlimited/max 100)

        Returns:
            Dict: success, recordId, recordName, connector, eventPublished, userRole; or error code/reason
        """
        try:
            if depth == -1:
                depth = MAX_REINDEX_DEPTH
            elif depth < 0:
                depth = 0
            record = await self.get_document(record_id, CollectionNames.RECORDS.value)
            if not record:
                return {"success": False, "code": 404, "reason": f"Record not found: {record_id}"}
            rec = record if isinstance(record, dict) else {}
            if rec.get("isDeleted"):
                return {"success": False, "code": 400, "reason": "Cannot reindex deleted record"}
            connector_name = rec.get("connectorName", "")
            connector_id = rec.get("connectorId", "")
            origin = rec.get("origin", "")
            user = await self.get_user_by_user_id(user_id)
            if not user:
                return {"success": False, "code": 404, "reason": f"User not found: {user_id}"}
            user_key = user.get("_key") or user.get("id")
            if not user_key:
                return {"success": False, "code": 404, "reason": "User key not found"}
            user_role = None
            if origin == OriginTypes.UPLOAD.value:
                kb_context = await self._get_kb_context_for_record(record_id)
                if not kb_context:
                    return {
                        "success": False,
                        "code": 404,
                        "reason": f"Knowledge base context not found for record {record_id}",
                    }
                user_role = await self.get_user_kb_permission(kb_context.get("kb_id") or kb_context.get("id"), user_key)
                if not user_role:
                    return {
                        "success": False,
                        "code": 403,
                        "reason": "Insufficient KB permissions. Required: OWNER, WRITER, READER",
                    }
            elif origin == OriginTypes.CONNECTOR.value:
                perm_result = await self._check_record_permissions(record_id, user_key)
                user_role = perm_result.get("permission")
                if not user_role:
                    return {
                        "success": False,
                        "code": 403,
                        "reason": "Insufficient permissions. Required: OWNER, WRITER, READER",
                    }
                if connector_id:
                    connector_doc = await self.get_document(connector_id, CollectionNames.APPS.value)
                    if connector_doc and not connector_doc.get("isActive", False):
                        return {
                            "success": False,
                            "code": 400,
                            "reason": "Connector is disabled. Please enable the connector first.",
                        }
            else:
                return {"success": False, "code": 400, "reason": f"Unsupported record origin: {origin}"}

            # Get file record for event payload
            file_record = await self.get_document(record_id, CollectionNames.FILES.value) if rec.get("recordType") == "FILE" else await self.get_document(record_id, CollectionNames.MAILS.value)

            # Determine if we should use batch reindex (depth > 0)
            use_batch_reindex = depth != 0

            # Reset indexing status to QUEUED before reindexing
            await self._reset_indexing_status_to_queued(record_id)

            # Create and publish reindex event
            try:
                if use_batch_reindex:
                    # Publish connector reindex event for batch processing
                    connector_normalized = connector_name.replace(" ", "").lower()
                    event_type = f"{connector_normalized}.reindex"

                    payload = {
                        "orgId": org_id,
                        "recordId": record_id,
                        "depth": depth,
                        "connectorId": connector_id
                    }

                    await self._publish_sync_event(event_type, payload)
                    self.logger.info(f"✅ Published {event_type} event for record {record_id} with depth {depth}")
                else:
                    # Single record reindex - use existing newRecord event
                    payload = await self._create_reindex_event_payload(record, file_record, user_id, request)
                    await self._publish_record_event("newRecord", payload)
                    self.logger.info(f"✅ Published reindex event for record {record_id}")

                return {
                    "success": True,
                    "recordId": record_id,
                    "recordName": rec.get("recordName"),
                    "connector": connector_name if origin == OriginTypes.CONNECTOR.value else Connectors.KNOWLEDGE_BASE.value,
                    "eventPublished": True,
                    "userRole": user_role,
                }

            except Exception as event_error:
                self.logger.error(f"❌ Failed to publish reindex event: {str(event_error)}")
                # Return success but indicate event wasn't published
                return {
                    "success": True,
                    "recordId": record_id,
                    "recordName": rec.get("recordName"),
                    "connector": connector_name if origin == OriginTypes.CONNECTOR.value else Connectors.KNOWLEDGE_BASE.value,
                    "eventPublished": False,
                    "userRole": user_role,
                    "eventError": str(event_error)
                }
        except Exception as e:
            self.logger.error("❌ Failed to reindex record %s: %s", record_id, str(e))
            return {"success": False, "code": 500, "reason": str(e)}

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
        return_fields: Optional[List[str]] = None,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get nodes by field filters - FULLY ASYNC.

        Args:
            collection: Collection name
            filters: Field filters as dict
            return_fields: Optional list of fields to return (None = all fields)
            transaction: Optional transaction ID

        Returns:
            List[Dict]: Matching nodes
        """
        # Build filter conditions
        filter_conditions = " AND ".join([
            f"doc.{field} == @{field}" for field in filters
        ])

        # Build return clause
        if return_fields:
            return_clause = "{ " + ", ".join([f'"{field}": doc.{field}' for field in return_fields]) + " }"
        else:
            return_clause = "doc"

        query = f"""
        FOR doc IN {collection}
            FILTER {filter_conditions}
            RETURN {return_clause}
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
        try:
            query = """
            FOR doc IN @@collection
                FILTER doc.indexingStatus == @status
                RETURN doc
            """

            bind_vars = {
                "@collection": collection,
                "status": status
            }

            results = await self.execute_query(query, bind_vars=bind_vars, transaction=transaction)
            return results if results else []

        except Exception as e:
            self.logger.error(f"❌ Failed to get documents by status from {collection}: {str(e)}")
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

    async def get_user_apps(self, user_key: str) -> List[Dict]:
        """Get all apps (connectors) associated with a user by user document key (_key)."""
        try:
            query = f"""
            FOR app IN OUTBOUND CONCAT('{CollectionNames.USERS.value}/', @user_key) {CollectionNames.USER_APP_RELATION.value}
                RETURN app
            """
            results = await self.execute_query(
                query,
                bind_vars={"user_key": user_key},
            )
            return list(results) if results else []
        except Exception as e:
            self.logger.error("❌ Failed to get user apps: %s", str(e))
            return []

    async def _get_user_app_ids(self, user_id: str) -> List[str]:
        """Get list of accessible app connector IDs for a user (user_id = external userId)."""
        try:
            user = await self.get_user_by_user_id(user_id)
            if not user:
                return []
            user_key = user.get("_key") or user.get("id")
            if not user_key:
                return []
            apps = await self.get_user_apps(user_key)
            return [a.get("_key") or a.get("id") for a in apps if a and (a.get("_key") or a.get("id"))]
        except Exception as e:
            self.logger.error("❌ Failed to get user app ids: %s", str(e))
            return []

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
        active: bool = True,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Get all organizations.

        Uses generic get_nodes_by_filters if filtering by active,
        or returns all orgs if no filter.
        """
        if active:
            return await self.get_nodes_by_filters(
                collection=CollectionNames.ORGS.value,
                filters={"isActive": True},
                transaction=transaction
            )
        else:
            # Get all orgs using execute_aql
            query = f"""
            FOR org IN {CollectionNames.ORGS.value}
                RETURN org
            """

            try:
                results = await self.http_client.execute_aql(query, txn_id=transaction)
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
                query = f"""
                FOR department IN {CollectionNames.DEPARTMENTS.value}
                    FILTER department.orgId == null OR department.orgId == @org_id
                    RETURN department.departmentName
                """
                bind_vars = {"org_id": org_id}
            else:
                query = f"""
                FOR department IN {CollectionNames.DEPARTMENTS.value}
                    RETURN department.departmentName
                """
                bind_vars = {}

            results = await self.http_client.execute_aql(query, bind_vars, txn_id=transaction)
            return [dept for dept in results] if results else []
        except Exception as e:
            self.logger.error(f"❌ Get departments failed: {str(e)}")
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
            file_query = f"""
            FOR file IN {CollectionNames.FILES.value}
                FILTER file._key == @record_id
                RETURN file
            """

            results = await self.http_client.execute_aql(
                file_query,
                bind_vars={"record_id": record_id},
                txn_id=transaction
            )

            file_doc = None
            if results:
                try:
                    file_doc = results[0]
                except (IndexError, StopIteration):
                    pass

            if not file_doc:
                self.logger.info(f"No file found for record {record_id}, skipping queued duplicate update")
                return 0

            md5_checksum = file_doc.get("md5Checksum")
            size_in_bytes = file_doc.get("sizeInBytes")

            if not md5_checksum or size_in_bytes is None:
                self.logger.warning(f"File {record_id} missing md5Checksum or sizeInBytes")
                return 0

            # Find all queued duplicate records
            query = f"""
            FOR file IN {CollectionNames.FILES.value}
                FILTER file.md5Checksum == @md5_checksum
                AND file.sizeInBytes == @size_in_bytes
                AND file._key != @record_id
                LET record = (
                    FOR r IN {CollectionNames.RECORDS.value}
                        FILTER r._key == file._key
                        AND r.indexingStatus == @queued_status
                        RETURN r
                )[0]
                FILTER record != null
                RETURN record
            """

            results = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "md5_checksum": md5_checksum,
                    "size_in_bytes": size_in_bytes,
                    "record_id": record_id,
                    "queued_status": "QUEUED"
                },
                txn_id=transaction
            )

            queued_records = list(results) if results else []

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

            query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record.externalRecordId == @external_message_id
                RETURN record._key
            """

            results = await self.http_client.execute_aql(
                query,
                bind_vars={"external_message_id": external_message_id},
                txn_id=transaction
            )

            if results:
                self.logger.info(
                    f"✅ Successfully retrieved internal key for external message ID {external_message_id}"
                )
                return results[0]
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
            edge_collection (str): Edge collection name
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[Dict]: List of related records with messageId, id/key, and relationType
        """
        try:
            self.logger.info(
                f"🚀 Getting related records for {record_id} with relation type {relation_type}"
            )

            query = f"""
            FOR v, e IN 1..1 ANY '{CollectionNames.RECORDS.value}/{record_id}' {edge_collection}
                FILTER e.relationType == @relation_type
                RETURN {{
                    messageId: v.externalRecordId,
                    _key: v._key,
                    id: v._key,
                    relationType: e.relationType
                }}
            """

            results = await self.http_client.execute_aql(
                query,
                bind_vars={"relation_type": relation_type},
                txn_id=transaction
            )

            if results:
                self.logger.info(
                    f"✅ Found {len(results)} related records for {record_id}"
                )
                return results
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

            query = f"""
            FOR record IN {collection}
                FILTER record._key == @record_key
                RETURN record.messageIdHeader
            """

            results = await self.http_client.execute_aql(
                query,
                bind_vars={"record_key": record_key},
                txn_id=transaction
            )

            if results and results[0] is not None:
                self.logger.info(
                    f"✅ Found messageIdHeader for record {record_key}"
                )
                return results[0]
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

            query = f"""
            FOR record IN {collection}
                FILTER record.messageIdHeader == @message_id_header
                AND record._key != @exclude_key
                RETURN record._key
            """

            results = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "message_id_header": message_id_header,
                    "exclude_key": exclude_key
                },
                txn_id=transaction
            )

            if results:
                self.logger.info(
                    f"✅ Found {len(results)} related mails with messageIdHeader {message_id_header}"
                )
                return results
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

            query = f"""
            FOR doc IN {collection}
                FILTER doc._key IN @keys
                UPDATE doc WITH @updates IN {collection}
                RETURN NEW
            """

            bind_vars = {
                "keys": node_ids,
                "updates": updates
            }

            results = await self.http_client.execute_aql(
                query,
                bind_vars=bind_vars,
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

            query = f"""
            FOR doc IN {collection}
                FILTER doc._id != null
                FILTER doc.scope == @scope
                FILTER doc.isConfigured == true
            """

            bind_vars = {"scope": scope}

            # Add user filter for personal scope
            if scope == "personal" and user_id:
                query += " FILTER doc.createdBy == @user_id"
                bind_vars["user_id"] = user_id

            query += " COLLECT WITH COUNT INTO total RETURN total"

            results = await self.http_client.execute_aql(
                query,
                bind_vars=bind_vars,
                txn_id=transaction
            )

            count = results[0] if results else 0
            self.logger.info(f"✅ Found {count} connector instances for scope {scope}")
            return count

        except Exception as e:
            self.logger.error(f"❌ Failed to count connector instances by scope: {str(e)}")
            return 0

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

            if scope == "personal":
                # For personal scope: check uniqueness within user's personal connectors
                query = f"""
                FOR doc IN {collection}
                    FILTER doc.scope == @scope
                    FILTER doc.createdBy == @user_id
                    FILTER LOWER(TRIM(doc.name)) == @normalized_name
                    RETURN doc._key
                """
                bind_vars = {
                    "scope": scope,
                    "user_id": user_id,
                    "normalized_name": normalized_name,
                }
            else:  # TEAM scope
                # For team scope: check uniqueness within organization's team connectors
                query = f"""
                FOR edge IN {edge_collection}
                    FILTER edge._from == @org_id
                    FOR doc IN {collection}
                        FILTER doc._id == edge._to
                        FILTER doc.scope == @scope
                        FILTER LOWER(TRIM(doc.name)) == @normalized_name
                        RETURN doc._key
                """
                bind_vars = {
                    "org_id": f"{CollectionNames.ORGS.value}/{org_id}",
                    "scope": scope,
                    "normalized_name": normalized_name,
                }

            results = await self.http_client.execute_aql(
                query,
                bind_vars=bind_vars,
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

            # Build base query
            query = f"""
            FOR doc IN {collection}
                FILTER doc._id != null
            """

            bind_vars = {}

            # Add scope filter if specified
            if scope:
                query += " FILTER doc.scope == @scope"
                bind_vars["scope"] = scope

            # Add access control
            if not is_admin:
                # Non-admins can only see their own connectors
                query += " FILTER (doc.createdBy == @user_id)"
                bind_vars["user_id"] = user_id
            else:
                # Admins can see all team connectors + their personal connectors
                query += " FILTER (doc.scope == @team_scope) OR (doc.createdBy == @user_id)"
                bind_vars["team_scope"] = "team"
                bind_vars["user_id"] = user_id

            # Add search filter if specified
            if search:
                query += " FILTER (LOWER(doc.name) LIKE @search) OR (LOWER(doc.type) LIKE @search) OR (LOWER(doc.appGroup) LIKE @search)"
                bind_vars["search"] = f"%{search.lower()}%"

            # Get total count
            count_query = query + " COLLECT WITH COUNT INTO total RETURN total"
            count_results = await self.http_client.execute_aql(
                count_query,
                bind_vars=bind_vars,
                txn_id=transaction
            )
            total_count = count_results[0] if count_results else 0

            # Add pagination
            query += """
                SORT doc.createdAtTimestamp DESC
                LIMIT @offset, @limit
                RETURN doc
            """
            bind_vars["offset"] = (page - 1) * limit
            bind_vars["limit"] = limit

            results = await self.http_client.execute_aql(
                query,
                bind_vars=bind_vars,
                txn_id=transaction
            )

            documents = list(results) if results else []

            self.logger.info(f"✅ Found {len(documents)} connector instances (total: {total_count})")
            return documents, total_count

        except Exception as e:
            self.logger.error(f"❌ Failed to get connector instances with filters: {str(e)}")
            return [], 0

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

            query = f"""
            FOR doc IN {collection}
                FILTER doc._id != null
                FILTER (
                    doc.scope == @team_scope OR
                    (doc.scope == @personal_scope AND doc.createdBy == @user_id)
                )
                RETURN doc
            """

            bind_vars = {
                "team_scope": team_scope,
                "personal_scope": personal_scope,
                "user_id": user_id,
            }

            results = await self.http_client.execute_aql(
                query,
                bind_vars=bind_vars,
                txn_id=transaction
            )

            documents = list(results) if results else []
            self.logger.info(f"✅ Found {len(documents)} connector instances")
            return documents

        except Exception as e:
            self.logger.error(f"❌ Failed to get connector instances by scope and user: {str(e)}")
            return []

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

    # ==================== Connector Registry Operations ====================

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
        For team-based access, returns the highest role from all common teams.
        """
        try:
            # Build filter conditions
            filter_conditions = []
            if search:
                filter_conditions.append("LIKE(LOWER(kb.groupName), LOWER(@search_term))")
            permission_filter = ""
            if permissions:
                permission_filter = "FILTER final_role IN @permissions"
            additional_filters = ""
            if filter_conditions:
                additional_filters = "AND " + " AND ".join(filter_conditions)

            sort_field_map = {
                "name": "kb.groupName",
                "createdAtTimestamp": "kb.createdAtTimestamp",
                "updatedAtTimestamp": "kb.updatedAtTimestamp",
                "userRole": "final_role"
            }
            sort_field = sort_field_map.get(sort_by, "kb.groupName")
            sort_direction = sort_order.upper()
            role_priority_map = {
                "OWNER": 4,
                "WRITER": 3,
                "READER": 2,
                "COMMENTER": 1
            }

            main_query = f"""
            LET direct_perms = (
                FOR perm IN @@permissions_collection
                    FILTER perm._from == @user_from
                    FILTER perm.type == "USER"
                    FILTER STARTS_WITH(perm._to, "recordGroups/")
                    LET kb = DOCUMENT(perm._to)
                    FILTER kb != null
                    FILTER kb.orgId == @org_id
                    FILTER kb.groupType == @kb_type
                    FILTER kb.connectorName == @kb_connector
                    {additional_filters}
                    RETURN {{
                        kb_id: kb._key,
                        kb_doc: kb,
                        role: perm.role,
                        priority: @role_priority[perm.role] || 0,
                        is_direct: true
                    }}
            )

            LET team_perms = (
                LET user_teams = (
                    FOR user_team_perm IN @@permissions_collection
                        FILTER user_team_perm._from == @user_from
                        FILTER user_team_perm.type == "USER"
                        FILTER STARTS_WITH(user_team_perm._to, "teams/")
                        RETURN {{
                            team_id: SPLIT(user_team_perm._to, '/')[1],
                            role: user_team_perm.role,
                            priority: @role_priority[user_team_perm.role] || 0
                        }}
                )

                FOR team_info IN user_teams
                    FOR kb_team_perm IN @@permissions_collection
                        FILTER kb_team_perm._from == CONCAT('teams/', team_info.team_id)
                        FILTER kb_team_perm.type == "TEAM"
                        FILTER STARTS_WITH(kb_team_perm._to, "recordGroups/")
                        LET kb = DOCUMENT(kb_team_perm._to)
                        FILTER kb != null
                        FILTER kb.orgId == @org_id
                        FILTER kb.groupType == @kb_type
                        FILTER kb.connectorName == @kb_connector
                        {additional_filters}
                        RETURN {{
                            kb_id: kb._key,
                            kb_doc: kb,
                            role: team_info.role,
                            priority: team_info.priority,
                            is_direct: false
                        }}
            )

            LET all_perms = UNION(direct_perms, team_perms)

            LET kb_roles = (
                FOR perm IN all_perms
                    COLLECT kb_id = perm.kb_id, kb_doc = perm.kb_doc INTO roles = perm
                    LET sorted_roles = (
                        FOR r IN roles
                            SORT r.priority DESC, r.is_direct DESC
                            LIMIT 1
                            RETURN r.role
                    )
                    LET final_role = FIRST(sorted_roles)
                    {permission_filter}
                    RETURN {{
                        kb_id: kb_id,
                        kb_doc: kb_doc,
                        userRole: final_role
                    }}
            )

            LET kb_ids = kb_roles[*].kb_doc._id
            LET all_folders = (
                FOR edge IN @@belongs_to_kb
                    FILTER edge._to IN kb_ids
                    LET folder = DOCUMENT(edge._from)
                    FILTER folder != null && folder.isFile == false
                    RETURN {{
                        kb_id: edge._to,
                        folder: {{
                            id: folder._key,
                            name: folder.name,
                            createdAtTimestamp: edge.createdAtTimestamp,
                            path: folder.path,
                            webUrl: folder.webUrl
                        }}
                    }}
            )

            FOR kb_role IN kb_roles
                LET kb = kb_role.kb_doc
                LET folders = all_folders[* FILTER CURRENT.kb_id == kb._id].folder
                SORT {sort_field} {sort_direction}
                LIMIT @skip, @limit
                RETURN {{
                    id: kb._key,
                    name: kb.groupName,
                    createdAtTimestamp: kb.createdAtTimestamp,
                    updatedAtTimestamp: kb.updatedAtTimestamp,
                    createdBy: kb.createdBy,
                    userRole: kb_role.userRole,
                    folders: folders
                }}
            """

            count_query = f"""
            LET direct_perms = (
                FOR perm IN @@count_permissions_collection
                    FILTER perm._from == @count_user_from
                    FILTER perm.type == "USER"
                    FILTER STARTS_WITH(perm._to, "recordGroups/")
                    LET kb = DOCUMENT(perm._to)
                    FILTER kb != null
                    FILTER kb.orgId == @count_org_id
                    FILTER kb.groupType == @count_kb_type
                    FILTER kb.connectorName == @count_kb_connector
                    {additional_filters.replace('@search_term', '@count_search_term') if additional_filters else ''}
                    RETURN {{
                        kb_id: kb._key,
                        role: perm.role,
                        priority: @count_role_priority[perm.role] || 0,
                        is_direct: true
                    }}
            )

            LET team_perms = (
                LET user_teams = (
                    FOR user_team_perm IN @@count_permissions_collection
                        FILTER user_team_perm._from == @count_user_from
                        FILTER user_team_perm.type == "USER"
                        FILTER STARTS_WITH(user_team_perm._to, "teams/")
                        RETURN {{
                            team_id: SPLIT(user_team_perm._to, '/')[1],
                            role: user_team_perm.role,
                            priority: @count_role_priority[user_team_perm.role] || 0
                        }}
                )

                FOR team_info IN user_teams
                    FOR kb_team_perm IN @@count_permissions_collection
                        FILTER kb_team_perm._from == CONCAT('teams/', team_info.team_id)
                        FILTER kb_team_perm.type == "TEAM"
                        FILTER STARTS_WITH(kb_team_perm._to, "recordGroups/")
                        LET kb = DOCUMENT(kb_team_perm._to)
                        FILTER kb != null
                        FILTER kb.orgId == @count_org_id
                        FILTER kb.groupType == @count_kb_type
                        FILTER kb.connectorName == @count_kb_connector
                        {additional_filters.replace('@search_term', '@count_search_term') if additional_filters else ''}
                        RETURN {{
                            kb_id: kb._key,
                            role: team_info.role,
                            priority: team_info.priority,
                            is_direct: false
                        }}
            )

            LET all_perms = UNION(direct_perms, team_perms)

            LET kb_roles = (
                FOR perm IN all_perms
                    COLLECT kb_id = perm.kb_id INTO roles = perm
                    LET sorted_roles = (
                        FOR r IN roles
                            SORT r.priority DESC, r.is_direct DESC
                            LIMIT 1
                            RETURN r.role
                    )
                    LET final_role = FIRST(sorted_roles)
                    {permission_filter.replace('@permissions', '@count_permissions') if permission_filter else ''}
                    RETURN kb_id
            )

            RETURN LENGTH(kb_roles)
            """

            filters_query = """
            LET direct_perms = (
                FOR perm IN @@filters_permissions_collection
                    FILTER perm._from == @filters_user_from
                    FILTER perm.type == "USER"
                    FILTER STARTS_WITH(perm._to, "recordGroups/")
                    LET kb = DOCUMENT(perm._to)
                    FILTER kb != null
                    FILTER kb.orgId == @filters_org_id
                    FILTER kb.groupType == @filters_kb_type
                    FILTER kb.connectorName == @filters_kb_connector
                    RETURN {
                        kb_id: kb._key,
                        permission: perm.role,
                        kb_name: kb.groupName,
                        priority: @filters_role_priority[perm.role] || 0,
                        is_direct: true
                    }
            )

            LET team_perms = (
                LET user_teams = (
                    FOR user_team_perm IN @@filters_permissions_collection
                        FILTER user_team_perm._from == @filters_user_from
                        FILTER user_team_perm.type == "USER"
                        FILTER STARTS_WITH(user_team_perm._to, "teams/")
                        RETURN {
                            team_id: SPLIT(user_team_perm._to, '/')[1],
                            role: user_team_perm.role,
                            priority: @filters_role_priority[user_team_perm.role] || 0
                        }
                )

                FOR team_info IN user_teams
                    FOR kb_team_perm IN @@filters_permissions_collection
                        FILTER kb_team_perm._from == CONCAT('teams/', team_info.team_id)
                        FILTER kb_team_perm.type == "TEAM"
                        FILTER STARTS_WITH(kb_team_perm._to, "recordGroups/")
                        LET kb = DOCUMENT(kb_team_perm._to)
                        FILTER kb != null
                        FILTER kb.orgId == @filters_org_id
                        FILTER kb.groupType == @filters_kb_type
                        FILTER kb.connectorName == @filters_kb_connector
                        RETURN {
                            kb_id: kb._key,
                            permission: team_info.role,
                            kb_name: kb.groupName,
                            priority: team_info.priority,
                            is_direct: false
                        }
            )

            LET all_perms = UNION(direct_perms, team_perms)

            FOR perm IN all_perms
                COLLECT kb_id = perm.kb_id INTO roles = perm
                LET sorted_roles = (
                    FOR r IN roles
                        SORT r.priority DESC, r.is_direct DESC
                        LIMIT 1
                        RETURN r.permission
                )
                RETURN {
                    permission: FIRST(sorted_roles),
                    kb_name: FIRST(roles).kb_name
                }
            """

            main_bind_vars: Dict[str, Any] = {
                "user_from": f"users/{user_id}",
                "org_id": org_id,
                "kb_type": Connectors.KNOWLEDGE_BASE.value,
                "kb_connector": Connectors.KNOWLEDGE_BASE.value,
                "skip": skip,
                "limit": limit,
                "role_priority": role_priority_map,
                "@permissions_collection": CollectionNames.PERMISSION.value,
                "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
            }
            if search:
                main_bind_vars["search_term"] = f"%{search}%"
            if permissions:
                main_bind_vars["permissions"] = permissions

            count_bind_vars: Dict[str, Any] = {
                "count_user_from": f"users/{user_id}",
                "count_org_id": org_id,
                "count_kb_type": Connectors.KNOWLEDGE_BASE.value,
                "count_kb_connector": Connectors.KNOWLEDGE_BASE.value,
                "count_role_priority": role_priority_map,
                "@count_permissions_collection": CollectionNames.PERMISSION.value,
            }
            if search:
                count_bind_vars["count_search_term"] = f"%{search}%"
            if permissions:
                count_bind_vars["count_permissions"] = permissions

            filters_bind_vars = {
                "filters_user_from": f"users/{user_id}",
                "filters_org_id": org_id,
                "filters_kb_type": Connectors.KNOWLEDGE_BASE.value,
                "filters_kb_connector": Connectors.KNOWLEDGE_BASE.value,
                "filters_role_priority": role_priority_map,
                "@filters_permissions_collection": CollectionNames.PERMISSION.value,
            }

            kbs = await self.execute_query(main_query, main_bind_vars, transaction) or []
            count_result = await self.execute_query(count_query, count_bind_vars, transaction) or []
            total_count = count_result[0] if count_result and len(count_result) > 0 else 0
            filter_data = await self.execute_query(filters_query, filters_bind_vars, transaction) or []

            available_permissions = list(set(item["permission"] for item in filter_data if item.get("permission")))
            available_filters = {
                "permissions": available_permissions,
                "sortFields": ["name", "createdAtTimestamp", "updatedAtTimestamp", "userRole"],
                "sortOrders": ["asc", "desc"]
            }

            self.logger.info(
                f"✅ Found {len(kbs)} knowledge bases out of {total_count} total (including team-based access)"
            )
            return kbs, total_count, available_filters

        except Exception as e:
            self.logger.error(f"❌ Failed to list knowledge bases with pagination: {str(e)}")
            return [], 0, {
                "permissions": [],
                "sortFields": ["name", "createdAtTimestamp", "updatedAtTimestamp", "userRole"],
                "sortOrders": ["asc", "desc"]
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
        transaction: Optional[str] = None,
    ) -> Dict:
        """
        Get KB root contents with folders_first pagination and level order traversal.
        """
        try:
            def build_filters() -> Tuple[str, str, Dict]:
                folder_conditions = []
                record_conditions = []
                bind_vars: Dict[str, Any] = {}

                if search:
                    folder_conditions.append(
                        "LIKE(LOWER(folder_record.recordName), @search_term)"
                    )
                    record_conditions.append(
                        "(LIKE(LOWER(record.recordName), @search_term) OR "
                        "LIKE(LOWER(record.externalRecordId), @search_term))"
                    )
                    bind_vars["search_term"] = f"%{search.lower()}%"

                if record_types:
                    record_conditions.append("record.recordType IN @record_types")
                    bind_vars["record_types"] = record_types

                if origins:
                    record_conditions.append("record.origin IN @origins")
                    bind_vars["origins"] = origins

                if connectors:
                    record_conditions.append("record.connectorName IN @connectors")
                    bind_vars["connectors"] = connectors

                if indexing_status:
                    record_conditions.append(
                        "record.indexingStatus IN @indexing_status"
                    )
                    bind_vars["indexing_status"] = indexing_status

                folder_filter = (
                    " AND " + " AND ".join(folder_conditions) if folder_conditions else ""
                )
                record_filter = (
                    " AND " + " AND ".join(record_conditions) if record_conditions else ""
                )
                return folder_filter, record_filter, bind_vars

            folder_filter, record_filter, filter_vars = build_filters()

            record_sort_map = {
                "name": "record.recordName",
                "created_at": "record.createdAtTimestamp",
                "updated_at": "record.updatedAtTimestamp",
                "size": "fileRecord.sizeInBytes",
            }
            record_sort_field = record_sort_map.get(sort_by, "record.recordName")
            sort_direction = sort_order.upper()

            main_query = f"""
            LET kb = DOCUMENT("recordGroups", @kb_id)
            FILTER kb != null
            LET allImmediateChildren = (
                FOR belongsEdge IN @@belongs_to
                    FILTER belongsEdge._to == kb._id
                    FILTER belongsEdge.entityType == @kb_connector_type
                    LET record = DOCUMENT(belongsEdge._from)
                    FILTER IS_SAME_COLLECTION("records", record._id)
                    FILTER record != null
                    FILTER record.isDeleted != true
                    LET isChild = LENGTH(
                        FOR relEdge IN @@record_relations
                            FILTER relEdge._to == record._id
                            FILTER relEdge.relationshipType == "PARENT_CHILD"
                            RETURN 1
                    ) > 0
                    FILTER isChild == false
                    RETURN record
            )
            LET allFolders = (
                FOR record IN allImmediateChildren
                    LET folder_file = FIRST(
                        FOR isEdge IN @@is_of_type
                            FILTER isEdge._from == record._id
                            LET f = DOCUMENT(isEdge._to)
                            FILTER f != null AND f.isFile == false
                            RETURN f
                    )
                    FILTER folder_file != null
                    {folder_filter}
                    LET direct_subfolders = LENGTH(
                        FOR relEdge IN @@record_relations
                            FILTER relEdge._from == record._id
                            FILTER relEdge.relationshipType == "PARENT_CHILD"
                            LET child_record = DOCUMENT(relEdge._to)
                            FILTER child_record != null
                            LET child_file = FIRST(
                                FOR isEdge IN @@is_of_type
                                    FILTER isEdge._from == child_record._id
                                    LET f = DOCUMENT(isEdge._to)
                                    FILTER f != null AND f.isFile == false
                                    RETURN 1
                            )
                            FILTER child_file != null
                            RETURN 1
                    )
                    LET direct_records = LENGTH(
                        FOR relEdge IN @@record_relations
                            FILTER relEdge._from == record._id
                            FILTER relEdge.relationshipType == "PARENT_CHILD"
                            LET child_record = DOCUMENT(relEdge._to)
                            FILTER child_record != null AND child_record.isDeleted != true
                            LET child_file = FIRST(
                                FOR isEdge IN @@is_of_type
                                    FILTER isEdge._from == child_record._id
                                    LET f = DOCUMENT(isEdge._to)
                                    FILTER f != null AND f.isFile == false
                                    RETURN 1
                            )
                            FILTER child_file == null
                            RETURN 1
                    )
                    SORT record.recordName ASC
                    RETURN {{
                        id: record._key,
                        name: record.recordName,
                        path: folder_file.path,
                        level: 1,
                        parent_id: null,
                        webUrl: record.webUrl,
                        recordGroupId: record.connectorId,
                        type: "folder",
                        createdAtTimestamp: record.createdAtTimestamp,
                        updatedAtTimestamp: record.updatedAtTimestamp,
                        counts: {{
                            subfolders: direct_subfolders,
                            records: direct_records,
                            totalItems: direct_subfolders + direct_records
                        }},
                        hasChildren: direct_subfolders > 0 OR direct_records > 0
                    }}
            )
            LET allRecords = (
                FOR record IN allImmediateChildren
                    LET record_file = FIRST(
                        FOR isEdge IN @@is_of_type
                            FILTER isEdge._from == record._id
                            LET f = DOCUMENT(isEdge._to)
                            FILTER f != null AND f.isFile == false
                            RETURN 1
                    )
                    FILTER record_file == null
                    {record_filter}
                    LET fileEdge = FIRST(
                        FOR isEdge IN @@is_of_type
                            FILTER isEdge._from == record._id
                            RETURN isEdge
                    )
                    LET fileRecord = fileEdge ? DOCUMENT(fileEdge._to) : null
                    SORT {record_sort_field} {sort_direction}
                    RETURN {{
                        id: record._key,
                        recordName: record.recordName,
                        name: record.recordName,
                        recordType: record.recordType,
                        externalRecordId: record.externalRecordId,
                        origin: record.origin,
                        connectorName: record.connectorName || "KNOWLEDGE_BASE",
                        indexingStatus: record.indexingStatus,
                        version: record.version,
                        isLatestVersion: record.isLatestVersion,
                        createdAtTimestamp: record.createdAtTimestamp,
                        updatedAtTimestamp: record.updatedAtTimestamp,
                        sourceCreatedAtTimestamp: record.sourceCreatedAtTimestamp,
                        sourceLastModifiedTimestamp: record.sourceLastModifiedTimestamp,
                        webUrl: record.webUrl,
                        orgId: record.orgId,
                        type: "record",
                        fileRecord: fileRecord ? {{
                            id: fileRecord._key,
                            name: fileRecord.name,
                            extension: fileRecord.extension,
                            mimeType: fileRecord.mimeType,
                            sizeInBytes: fileRecord.sizeInBytes,
                            webUrl: fileRecord.webUrl,
                            path: fileRecord.path,
                            isFile: fileRecord.isFile
                        }} : null
                    }}
            )
            LET totalFolders = LENGTH(allFolders)
            LET totalRecords = LENGTH(allRecords)
            LET totalCount = totalFolders + totalRecords
            LET paginatedFolders = (
                @skip < totalFolders ?
                    SLICE(allFolders, @skip, @limit)
                : []
            )
            LET foldersShown = LENGTH(paginatedFolders)
            LET remainingLimit = @limit - foldersShown
            LET recordSkip = @skip >= totalFolders ? (@skip - totalFolders) : 0
            LET recordLimit = @skip >= totalFolders ? @limit : remainingLimit
            LET paginatedRecords = (
                recordLimit > 0 ?
                    SLICE(allRecords, recordSkip, recordLimit)
                : []
            )
            LET availableFilters = {{
                recordTypes: UNIQUE(allRecords[*].recordType) || [],
                origins: UNIQUE(allRecords[*].origin) || [],
                connectors: UNIQUE(allRecords[*].connectorName) || [],
                indexingStatus: UNIQUE(allRecords[*].indexingStatus) || []
            }}
            RETURN {{
                success: true,
                container: {{
                    id: kb._key,
                    name: kb.groupName,
                    path: "/",
                    type: "kb",
                    webUrl: CONCAT("/kb/", kb._key),
                    recordGroupId: kb._key
                }},
                folders: paginatedFolders,
                records: paginatedRecords,
                level: @level,
                totalCount: totalCount,
                counts: {{
                    folders: LENGTH(paginatedFolders),
                    records: LENGTH(paginatedRecords),
                    totalItems: LENGTH(paginatedFolders) + LENGTH(paginatedRecords),
                    totalFolders: totalFolders,
                    totalRecords: totalRecords
                }},
                availableFilters: availableFilters,
                paginationMode: "folders_first"
            }}
            """

            bind_vars: Dict[str, Any] = {
                "kb_id": kb_id,
                "skip": skip,
                "limit": limit,
                "level": level,
                "kb_connector_type": Connectors.KNOWLEDGE_BASE.value,
                "@belongs_to": CollectionNames.BELONGS_TO.value,
                "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                **filter_vars,
            }

            results = await self.execute_query(main_query, bind_vars=bind_vars, transaction=transaction)
            result = results[0] if results else None

            if not result:
                return {"success": False, "reason": "Knowledge base not found"}

            self.logger.info(
                f"✅ Retrieved KB children with folders_first pagination: "
                f"{result['counts']['totalItems']} items"
            )
            return result

        except Exception as e:
            self.logger.error(
                f"❌ Failed to get KB children with folders_first pagination: {str(e)}"
            )
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
        transaction: Optional[str] = None,
    ) -> Dict:
        """
        Get folder contents with folders_first pagination and level order traversal.
        """
        try:
            def build_filters() -> Tuple[str, str, Dict]:
                folder_conditions = []
                record_conditions = []
                bind_vars: Dict[str, Any] = {}

                if search:
                    folder_conditions.append(
                        "LIKE(LOWER(subfolder_record.recordName), @search_term)"
                    )
                    record_conditions.append(
                        "(LIKE(LOWER(record.recordName), @search_term) OR "
                        "LIKE(LOWER(record.externalRecordId), @search_term))"
                    )
                    bind_vars["search_term"] = f"%{search.lower()}%"

                if record_types:
                    record_conditions.append("record.recordType IN @record_types")
                    bind_vars["record_types"] = record_types

                if origins:
                    record_conditions.append("record.origin IN @origins")
                    bind_vars["origins"] = origins

                if connectors:
                    record_conditions.append("record.connectorName IN @connectors")
                    bind_vars["connectors"] = connectors

                if indexing_status:
                    record_conditions.append(
                        "record.indexingStatus IN @indexing_status"
                    )
                    bind_vars["indexing_status"] = indexing_status

                folder_filter = (
                    " AND " + " AND ".join(folder_conditions) if folder_conditions else ""
                )
                record_filter = (
                    " AND " + " AND ".join(record_conditions) if record_conditions else ""
                )
                return folder_filter, record_filter, bind_vars

            folder_filter, record_filter, filter_vars = build_filters()

            record_sort_map = {
                "name": "record.recordName",
                "created_at": "record.createdAtTimestamp",
                "updated_at": "record.updatedAtTimestamp",
                "size": "fileRecord.sizeInBytes",
            }
            record_sort_field = record_sort_map.get(sort_by, "record.recordName")
            sort_direction = sort_order.upper()

            main_query = f"""
            LET folder_record = DOCUMENT("records", @folder_id)
            FILTER folder_record != null
            LET folder_file = FIRST(
                FOR isEdge IN @@is_of_type
                    FILTER isEdge._from == folder_record._id
                    LET f = DOCUMENT(isEdge._to)
                    FILTER f != null AND f.isFile == false
                    RETURN f
            )
            FILTER folder_file != null
            LET allSubfolders = (
                FOR v, e, p IN 1..@level OUTBOUND folder_record._id @@record_relations
                    FILTER e.relationshipType == "PARENT_CHILD"
                    LET subfolder_record = v
                    LET subfolder_file = FIRST(
                        FOR isEdge IN @@is_of_type
                            FILTER isEdge._from == subfolder_record._id
                            LET f = DOCUMENT(isEdge._to)
                            FILTER f != null AND f.isFile == false
                            RETURN f
                    )
                    FILTER subfolder_file != null
                    LET current_level = LENGTH(p.edges)
                    {folder_filter}
                    LET direct_subfolders = LENGTH(
                        FOR relEdge IN @@record_relations
                            FILTER relEdge._from == subfolder_record._id
                            FILTER relEdge.relationshipType == "PARENT_CHILD"
                            LET child_record = DOCUMENT(relEdge._to)
                            FILTER child_record != null
                            LET child_file = FIRST(
                                FOR isEdge IN @@is_of_type
                                    FILTER isEdge._from == child_record._id
                                    LET f = DOCUMENT(isEdge._to)
                                    FILTER f != null AND f.isFile == false
                                    RETURN 1
                            )
                            FILTER child_file != null
                            RETURN 1
                    )
                    LET direct_records = LENGTH(
                        FOR relEdge IN @@record_relations
                            FILTER relEdge._from == subfolder_record._id
                            FILTER relEdge.relationshipType == "PARENT_CHILD"
                            LET record = DOCUMENT(relEdge._to)
                            FILTER record != null AND record.isDeleted != true
                            LET child_file = FIRST(
                                FOR isEdge IN @@is_of_type
                                    FILTER isEdge._from == record._id
                                    LET f = DOCUMENT(isEdge._to)
                                    FILTER f != null AND f.isFile == false
                                    RETURN 1
                            )
                            FILTER child_file == null
                            RETURN 1
                    )
                    SORT subfolder_record.recordName ASC
                    RETURN {{
                        id: subfolder_record._key,
                        name: subfolder_record.recordName,
                        path: subfolder_file.path,
                        level: current_level,
                        parentId: p.edges[-1] ? PARSE_IDENTIFIER(p.edges[-1]._from).key : null,
                        webUrl: subfolder_record.webUrl,
                        type: "folder",
                        createdAtTimestamp: subfolder_record.createdAtTimestamp,
                        updatedAtTimestamp: subfolder_record.updatedAtTimestamp,
                        counts: {{
                            subfolders: direct_subfolders,
                            records: direct_records,
                            totalItems: direct_subfolders + direct_records
                        }},
                        hasChildren: direct_subfolders > 0 OR direct_records > 0
                    }}
            )
            LET allRecords = (
                FOR edge IN @@record_relations
                    FILTER edge._from == folder_record._id
                    FILTER edge.relationshipType == "PARENT_CHILD"
                    LET record = DOCUMENT(edge._to)
                    FILTER record != null
                    FILTER record.isDeleted != true
                    LET record_file = FIRST(
                        FOR isEdge IN @@is_of_type
                            FILTER isEdge._from == record._id
                            LET f = DOCUMENT(isEdge._to)
                            FILTER f != null AND f.isFile == false
                            RETURN 1
                    )
                    FILTER record_file == null
                    {record_filter}
                    LET fileEdge = FIRST(
                        FOR isEdge IN @@is_of_type
                            FILTER isEdge._from == record._id
                            RETURN isEdge
                    )
                    LET fileRecord = fileEdge ? DOCUMENT(fileEdge._to) : null
                    SORT {record_sort_field} {sort_direction}
                    RETURN {{
                        id: record._key,
                        recordName: record.recordName,
                        name: record.recordName,
                        recordType: record.recordType,
                        externalRecordId: record.externalRecordId,
                        origin: record.origin,
                        connectorName: record.connectorName || "KNOWLEDGE_BASE",
                        indexingStatus: record.indexingStatus,
                        version: record.version,
                        isLatestVersion: record.isLatestVersion,
                        createdAtTimestamp: record.createdAtTimestamp,
                        updatedAtTimestamp: record.updatedAtTimestamp,
                        sourceCreatedAtTimestamp: record.sourceCreatedAtTimestamp,
                        sourceLastModifiedTimestamp: record.sourceLastModifiedTimestamp,
                        webUrl: record.webUrl,
                        orgId: record.orgId,
                        type: "record",
                        parent_folder_id: @folder_id,
                        sizeInBytes: fileRecord ? fileRecord.sizeInBytes : 0,
                        fileRecord: fileRecord ? {{
                            id: fileRecord._key,
                            name: fileRecord.name,
                            extension: fileRecord.extension,
                            mimeType: fileRecord.mimeType,
                            sizeInBytes: fileRecord.sizeInBytes,
                            webUrl: fileRecord.webUrl,
                            path: fileRecord.path,
                            isFile: fileRecord.isFile
                        }} : null
                    }}
            )
            LET totalSubfolders = LENGTH(allSubfolders)
            LET totalRecords = LENGTH(allRecords)
            LET totalCount = totalSubfolders + totalRecords
            LET paginatedSubfolders = (
                @skip < totalSubfolders ?
                    SLICE(allSubfolders, @skip, @limit)
                : []
            )
            LET subfoldersShown = LENGTH(paginatedSubfolders)
            LET remainingLimit = @limit - subfoldersShown
            LET recordSkip = @skip >= totalSubfolders ? (@skip - totalSubfolders) : 0
            LET recordLimit = @skip >= totalSubfolders ? @limit : remainingLimit
            LET paginatedRecords = (
                recordLimit > 0 ?
                    SLICE(allRecords, recordSkip, recordLimit)
                : []
            )
            LET availableFilters = {{
                recordTypes: UNIQUE(allRecords[*].recordType) || [],
                origins: UNIQUE(allRecords[*].origin) || [],
                connectors: UNIQUE(allRecords[*].connectorName) || [],
                indexingStatus: UNIQUE(allRecords[*].indexingStatus) || []
            }}
            RETURN {{
                success: true,
                container: {{
                    id: folder_record._key,
                    name: folder_record.recordName,
                    path: folder_file.path,
                    type: "folder",
                    webUrl: folder_record.webUrl,
                }},
                folders: paginatedSubfolders,
                records: paginatedRecords,
                level: @level,
                totalCount: totalCount,
                counts: {{
                    folders: LENGTH(paginatedSubfolders),
                    records: LENGTH(paginatedRecords),
                    totalItems: LENGTH(paginatedSubfolders) + LENGTH(paginatedRecords),
                    totalFolders: totalSubfolders,
                    totalRecords: totalRecords
                }},
                availableFilters: availableFilters,
                paginationMode: "folders_first"
            }}
            """

            bind_vars = {
                "folder_id": folder_id,
                "skip": skip,
                "limit": limit,
                "level": level,
                "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                **filter_vars,
            }

            results = await self.execute_query(
                main_query, bind_vars=bind_vars, transaction=transaction
            )
            result = results[0] if results else None

            if not result:
                return {"success": False, "reason": "Folder not found"}

            self.logger.info(
                f"✅ Retrieved folder children with folders_first pagination: "
                f"{result['counts']['totalItems']} items"
            )
            return result

        except Exception as e:
            self.logger.error(
                f"❌ Failed to get folder children with folders_first pagination: {str(e)}"
            )
            return {"success": False, "reason": str(e)}

    def _normalize_name(self, name: Optional[str]) -> Optional[str]:
        """Normalize a file/folder name to NFC and trim whitespace."""
        if name is None:
            return None
        try:
            return unicodedata.normalize("NFC", str(name)).strip()
        except Exception:
            return str(name).strip()

    def _normalized_name_variants_lower(self, name: str) -> List[str]:
        """Provide lowercase variants for equality comparisons (NFC and NFD)."""
        nfc = self._normalize_name(name) or ""
        try:
            nfd = unicodedata.normalize("NFD", nfc)
        except Exception:
            nfd = nfc
        return [nfc.lower(), nfd.lower()]

    async def _check_name_conflict_in_parent(
        self,
        kb_id: str,
        parent_folder_id: Optional[str],
        item_name: str,
        mime_type: Optional[str] = None,
        transaction: Optional[str] = None,
    ) -> Dict:
        """Check if an item (folder or file) name already exists in the target parent."""
        try:
            name_variants = self._normalized_name_variants_lower(item_name)
            parent_from = (
                f"{CollectionNames.RECORDS.value}/{parent_folder_id}"
                if parent_folder_id
                else f"{CollectionNames.RECORD_GROUPS.value}/{kb_id}"
            )
            bind_vars: Dict[str, Any] = {
                "parent_from": parent_from,
                "name_variants": name_variants,
                "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                "@files_collection": CollectionNames.FILES.value,
            }
            if mime_type:
                query = """
                FOR edge IN @@record_relations
                    FILTER edge._from == @parent_from
                    FILTER edge.relationshipType == "PARENT_CHILD"
                    FILTER edge._to LIKE "records/%"
                    LET child = DOCUMENT(edge._to)
                    FILTER child != null
                    FILTER child.recordName != null
                    FILTER child.mimeType == @mime_type
                    LET child_name_l = LOWER(child.recordName)
                    FILTER child_name_l IN @name_variants
                    LET file_doc = DOCUMENT(@@files_collection, child._key)
                    FILTER file_doc != null AND file_doc.isFile == true
                    RETURN {
                        id: child._key,
                        name: child.recordName,
                        type: "record",
                        document_type: "records",
                        mimeType: file_doc.mimeType
                    }
                """
                bind_vars["mime_type"] = mime_type
            else:
                query = """
                FOR edge IN @@record_relations
                    FILTER edge._from == @parent_from
                    FILTER edge.relationshipType == "PARENT_CHILD"
                    FILTER edge._to LIKE "records/%"
                    LET folder_record = DOCUMENT(edge._to)
                    FILTER folder_record != null
                    LET folder_file = FIRST(
                        FOR isEdge IN @@is_of_type
                            FILTER isEdge._from == folder_record._id
                            LET f = DOCUMENT(isEdge._to)
                            FILTER f != null AND f.isFile == false
                            RETURN f
                    )
                    FILTER folder_file != null
                    LET child_name = folder_record.recordName
                    FILTER child_name != null
                    LET child_name_l = LOWER(child_name)
                    FILTER child_name_l IN @name_variants
                    RETURN {
                        id: folder_record._key,
                        name: child_name,
                        type: "folder",
                        document_type: "records"
                    }
                """
                bind_vars["@is_of_type"] = CollectionNames.IS_OF_TYPE.value
            results = await self.execute_query(query, bind_vars=bind_vars, transaction=transaction)
            conflicts = list(results) if results else []
            return {"has_conflict": len(conflicts) > 0, "conflicts": conflicts}
        except Exception as e:
            self.logger.error(f"❌ Failed to check name conflict: {str(e)}")
            return {"has_conflict": False, "conflicts": []}

    async def get_knowledge_base(
        self,
        kb_id: str,
        user_id: str,
        transaction: Optional[str] = None,
    ) -> Optional[Dict]:
        """Get knowledge base with user permissions."""
        try:
            user_role = await self.get_user_kb_permission(kb_id, user_id, transaction=transaction)
            query = """
            FOR kb IN @@recordGroups_collection
                FILTER kb._key == @kb_id
                LET user_role = @user_role
                LET folders = (
                    FOR edge IN @@kb_to_folder_edges
                        FILTER edge._to == kb._id
                        FILTER STARTS_WITH(edge._from, 'records/')
                        LET folder_record = DOCUMENT(edge._from)
                        FILTER folder_record != null
                        LET folder_file = FIRST(
                            FOR isEdge IN @@is_of_type
                                FILTER isEdge._from == folder_record._id
                                LET f = DOCUMENT(isEdge._to)
                                FILTER f != null AND f.isFile == false
                                RETURN f
                        )
                        FILTER folder_file != null
                        RETURN {
                            id: folder_record._key,
                            name: folder_record.recordName,
                            createdAtTimestamp: folder_record.createdAtTimestamp,
                            updatedAtTimestamp: folder_record.updatedAtTimestamp,
                            path: folder_file.path,
                            webUrl: folder_record.webUrl,
                            mimeType: folder_record.mimeType,
                            sizeInBytes: folder_file.sizeInBytes
                        }
                )
                RETURN {
                    id: kb._key,
                    name: kb.groupName,
                    createdAtTimestamp: kb.createdAtTimestamp,
                    updatedAtTimestamp: kb.updatedAtTimestamp,
                    createdBy: kb.createdBy,
                    userRole: user_role,
                    folders: folders
                }
            """
            results = await self.execute_query(
                query,
                bind_vars={
                    "kb_id": kb_id,
                    "user_role": user_role,
                    "@recordGroups_collection": CollectionNames.RECORD_GROUPS.value,
                    "@kb_to_folder_edges": CollectionNames.BELONGS_TO.value,
                    "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                },
                transaction=transaction,
            )
            result = results[0] if results else None
            if result and not user_role:
                self.logger.warning(f"⚠️ User {user_id} has no access to KB {kb_id}")
                return None
            if result:
                self.logger.info("✅ Knowledge base retrieved successfully")
            return result
        except Exception as e:
            self.logger.error(f"❌ Failed to get knowledge base: {str(e)}")
            raise

    async def update_knowledge_base(
        self,
        kb_id: str,
        updates: Dict,
        transaction: Optional[str] = None,
    ) -> bool:
        """Update knowledge base."""
        try:
            query = """
            FOR kb IN @@kb_collection
                FILTER kb._key == @kb_id
                UPDATE kb WITH @updates IN @@kb_collection
                RETURN NEW
            """
            results = await self.execute_query(
                query,
                bind_vars={
                    "kb_id": kb_id,
                    "updates": updates,
                    "@kb_collection": CollectionNames.RECORD_GROUPS.value,
                },
                transaction=transaction,
            )
            result = results[0] if results else None
            if result:
                self.logger.info("✅ Knowledge base updated successfully")
                return True
            self.logger.warning("⚠️ Knowledge base not found")
            return False
        except Exception as e:
            self.logger.error(f"❌ Failed to update knowledge base: {str(e)}")
            raise

    async def delete_knowledge_base(
        self,
        kb_id: str,
        transaction: Optional[str] = None,
    ) -> bool:
        """
        Delete a knowledge base with ALL nested content
        - All folders (recursive, any depth)
        - All records in all folders
        - All file records
        - All edges (belongs_to_kb, record_relations, is_of_type, permissions)
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
                    return False

            try:
                # Step 1: Get complete inventory of what we're deleting using graph traversal
                # This collects ALL records/folders at any depth and FILES documents BEFORE edge deletion
                inventory_query = """
                LET kb = DOCUMENT("recordGroups", @kb_id)
                FILTER kb != null
                LET kb_id_full = CONCAT('recordGroups/', @kb_id)
                LET all_records_and_folders = (
                    FOR edge IN @@belongs_to_kb
                        FILTER edge._to == kb_id_full
                        LET record = DOCUMENT(edge._from)
                        FILTER record != null
                        FILTER IS_SAME_COLLECTION(@@records_collection, record._id)
                        RETURN record
                )
                LET all_files_with_details = (
                    FOR record IN all_records_and_folders
                        FOR edge IN @@is_of_type
                            FILTER edge._from == record._id
                            LET file = DOCUMENT(edge._to)
                            FILTER file != null
                            RETURN {
                                file_key: file._key,
                                is_folder: file.isFile == false,
                                record_key: record._key,
                                record: record,
                                file_doc: file
                            }
                )
                // Separate folders and file records
                LET folders = (
                    FOR item IN all_files_with_details
                        FILTER item.is_folder == true
                        RETURN item.record_key
                )
                LET file_records = (
                    FOR item IN all_files_with_details
                        FILTER item.is_folder == false
                        RETURN {
                            record: item.record,
                            file_record: item.file_doc
                        }
                )
                RETURN {
                    kb_exists: true,
                    record_keys: all_records_and_folders[*]._key,
                    file_keys: all_files_with_details[*].file_key,
                    folder_keys: folders,
                    records_with_details: file_records,
                    total_folders: LENGTH(folders),
                    total_records: LENGTH(all_records_and_folders)
                }
                """

                inv_results = await self.execute_query(
                    inventory_query,
                    bind_vars={
                        "kb_id": kb_id,
                        "@records_collection": CollectionNames.RECORDS.value,
                        "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                        "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                    },
                    transaction=transaction,
                )

                inventory = inv_results[0] if inv_results else {}

                if not inventory.get("kb_exists"):
                    self.logger.warning(f"⚠️ KB {kb_id} not found, deletion considered successful.")
                    if should_commit:
                        await self.commit_transaction(transaction)
                    return True

                records_with_details = inventory.get("records_with_details", [])
                all_record_keys = inventory.get("record_keys", [])

                self.logger.info(f"folder_keys: {inventory.get('folder_keys', [])}")
                self.logger.info(f"total_folders: {inventory.get('total_folders', 0)}")

                # Step 2: Delete ALL edges first (prevents foreign key issues)
                self.logger.info("🗑️ Step 2: Deleting all edges...")
                edges_cleanup_query = """
                LET record_ids = (FOR k IN @record_keys RETURN CONCAT('records/', k))
                LET kb_id_full = CONCAT('recordGroups/', @kb_id)

                // Collect ALL edge keys in one pass
                // Edges TO the KB (records/folders -> record group)
                LET belongs_to_keys = (
                    FOR e IN @@belongs_to_kb
                        FILTER e._to == kb_id_full
                        RETURN e._key
                )
                // Edge FROM KB record group TO KB app (record group -> app)
                LET belongs_to_kb_app_keys = (
                    FOR e IN @@belongs_to_kb
                        FILTER e._from == kb_id_full
                        RETURN e._key
                )
                LET all_belongs_to_keys = APPEND(belongs_to_keys, belongs_to_kb_app_keys)

                LET is_of_type_keys = (
                    FOR e IN @@is_of_type
                        FILTER e._from IN record_ids
                        RETURN e._key
                )

                LET permission_keys = (
                    FOR e IN @@permission
                        FILTER e._to == kb_id_full OR e._to IN record_ids
                        RETURN e._key
                )

                LET relation_keys = (
                    FOR e IN @@record_relations
                        FILTER e._from IN record_ids OR e._to IN record_ids
                        RETURN e._key
                )

                // Delete all edges (using different variable names to avoid AQL error)
                FOR btk_key IN all_belongs_to_keys REMOVE btk_key IN @@belongs_to_kb OPTIONS { ignoreErrors: true }
                FOR iot_key IN is_of_type_keys REMOVE iot_key IN @@is_of_type OPTIONS { ignoreErrors: true }
                FOR perm_key IN permission_keys REMOVE perm_key IN @@permission OPTIONS { ignoreErrors: true }
                FOR rel_key IN relation_keys REMOVE rel_key IN @@record_relations OPTIONS { ignoreErrors: true }

                RETURN {
                    belongs_to_deleted: LENGTH(all_belongs_to_keys),
                    is_of_type_deleted: LENGTH(is_of_type_keys),
                    permission_deleted: LENGTH(permission_keys),
                    relation_deleted: LENGTH(relation_keys)
                }
                """
                edge_results = await self.execute_query(
                    edges_cleanup_query,
                    bind_vars={
                        "kb_id": kb_id,
                        "record_keys": all_record_keys,
                        "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                        "@permission": CollectionNames.PERMISSION.value,
                        "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                        "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                    },
                    transaction=transaction,
                )
                edge_deletion_result = edge_results[0] if edge_results else {}

                self.logger.info(f"✅ All edges deleted for KB {kb_id}: "
                               f"belongs_to={edge_deletion_result.get('belongs_to_deleted', 0)}, "
                               f"is_of_type={edge_deletion_result.get('is_of_type_deleted', 0)}, "
                               f"permission={edge_deletion_result.get('permission_deleted', 0)}, "
                               f"relations={edge_deletion_result.get('relation_deleted', 0)}")

                # Step 3: Delete all FILES documents (folders + files) using helper method
                file_keys = inventory.get("file_keys", [])
                if file_keys:
                    self.logger.info(f"🗑️ Step 3: Deleting {len(file_keys)} FILES documents (folders + files)...")
                    await self.delete_nodes(file_keys, CollectionNames.FILES.value, transaction=transaction)
                    self.logger.info(f"✅ Deleted {len(file_keys)} FILES documents")

                # Step 4: Delete all RECORDS documents (folders + files) using helper method
                if all_record_keys:
                    self.logger.info(f"🗑️ Step 4: Deleting {len(all_record_keys)} RECORDS documents (folders + files)...")
                    await self.delete_nodes(all_record_keys, CollectionNames.RECORDS.value, transaction=transaction)
                    self.logger.info(f"✅ Deleted {len(all_record_keys)} RECORDS documents")

                # Step 5: Delete the KB document itself
                self.logger.info(f"🗑️ Step 5: Deleting KB document {kb_id}...")
                await self.execute_query(
                    "REMOVE @kb_id IN @@recordGroups_collection OPTIONS { ignoreErrors: true } RETURN OLD",
                    bind_vars={
                        "kb_id": kb_id,
                        "@recordGroups_collection": CollectionNames.RECORD_GROUPS.value
                    },
                    transaction=transaction,
                )

                # Step 6: Commit transaction
                if should_commit:
                    self.logger.info("💾 Committing complete deletion transaction...")
                    await self.commit_transaction(transaction)
                    self.logger.info("✅ Transaction committed successfully!")

                # Step 7: Publish delete events for all records (after successful transaction)
                try:
                    delete_event_tasks = []
                    for record_data in records_with_details:
                        delete_payload = await self._create_deleted_record_event_payload(
                            record_data["record"], record_data["file_record"]
                        )
                        if delete_payload:
                            delete_event_tasks.append(
                                self._publish_record_event("deleteRecord", delete_payload)
                            )

                    if delete_event_tasks:
                        await asyncio.gather(*delete_event_tasks, return_exceptions=True)
                        self.logger.info(f"✅ Published delete events for {len(delete_event_tasks)} records from KB deletion")

                except Exception as event_error:
                    self.logger.error(f"❌ Failed to publish KB deletion events: {str(event_error)}")
                    # Don't fail the main operation for event publishing errors

                self.logger.info(f"🎉 KB {kb_id} and ALL contents deleted successfully.")
                return True

            except Exception as db_error:
                self.logger.error(f"❌ Database error during KB deletion: {str(db_error)}")
                if should_commit and transaction:
                    await self.rollback_transaction(transaction)
                    self.logger.info("🔄 Transaction aborted due to error")
                raise db_error

        except Exception as e:
            self.logger.error(f"❌ Failed to delete KB {kb_id} completely: {str(e)}")
            return False

    # ==================== Event Publishing Methods ====================

    async def _publish_sync_event(self, event_type: str, payload: Dict) -> None:
        """Publish sync event to Kafka"""
        try:
            timestamp = get_epoch_timestamp_in_ms()

            event = {
                "eventType": event_type,
                "timestamp": timestamp,
                "payload": payload
            }

            if self.kafka_service:
                await self.kafka_service.publish_event("sync-events", event)
                self.logger.info(f"✅ Published {event_type} event for record {payload.get('recordId')}")
            else:
                self.logger.debug("Skipping Kafka publish for sync-events: kafka_service is not configured")

        except Exception as e:
            self.logger.error(f"❌ Failed to publish {event_type} event: {str(e)}")

    async def _publish_record_event(self, event_type: str, payload: Dict) -> None:
        """Publish record event to Kafka"""
        try:
            timestamp = get_epoch_timestamp_in_ms()

            event = {
                "eventType": event_type,
                "timestamp": timestamp,
                "payload": payload
            }

            if self.kafka_service:
                await self.kafka_service.publish_event("record-events", event)
                self.logger.info(f"✅ Published {event_type} event for record {payload.get('recordId')}")
            else:
                self.logger.debug("Skipping Kafka publish for record-events: kafka_service is not configured")

        except Exception as e:
            self.logger.error(f"❌ Failed to publish {event_type} event: {str(e)}")

    async def _create_deleted_record_event_payload(
        self,
        record: Dict,
        file_record: Optional[Dict],
    ) -> Optional[Dict]:
        """Create event payload for deleted record."""
        try:
            return {
                "recordId": record.get("_key"),
                "recordName": record.get("recordName"),
                "connectorId": record.get("connectorId"),
                "orgId": record.get("orgId"),
                "createdBy": record.get("createdBy"),
                "mimeType": record.get("mimeType"),
                "path": file_record.get("path") if file_record else None,
                "webUrl": record.get("webUrl"),
                "sizeInBytes": file_record.get("sizeInBytes") if file_record else 0,
            }
        except Exception as e:
            self.logger.error(f"Error creating delete event payload: {e}")
            return None

    async def _publish_kb_deletion_event(self, record: Dict, file_record: Optional[Dict]) -> None:
        """Publish KB-specific deletion event"""
        try:
            payload = await self._create_deleted_record_event_payload(record, file_record)
            if payload:
                # Add KB-specific metadata
                payload["connectorName"] = Connectors.KNOWLEDGE_BASE.value
                payload["origin"] = OriginTypes.UPLOAD.value

                await self._publish_record_event("deleteRecord", payload)
        except Exception as e:
            self.logger.error(f"❌ Failed to publish KB deletion event: {str(e)}")

    async def _publish_drive_deletion_event(self, record: Dict, file_record: Optional[Dict]) -> None:
        """Publish Drive-specific deletion event"""
        try:
            payload = await self._create_deleted_record_event_payload(record, file_record)
            if payload:
                # Add Drive-specific metadata
                payload["connectorName"] = Connectors.GOOGLE_DRIVE.value
                payload["origin"] = OriginTypes.CONNECTOR.value

                # Add Drive-specific fields if available
                if file_record:
                    payload["driveId"] = file_record.get("driveId", "")
                    payload["parentId"] = file_record.get("parentId", "")
                    payload["webViewLink"] = file_record.get("webViewLink", "")

                await self._publish_record_event("deleteRecord", payload)
        except Exception as e:
            self.logger.error(f"❌ Failed to publish Drive deletion event: {str(e)}")

    async def _publish_gmail_deletion_event(self, record: Dict, mail_record: Optional[Dict], file_record: Optional[Dict]) -> None:
        """Publish Gmail-specific deletion event"""
        try:
            # Use mail_record or file_record for attachment info
            data_record = mail_record or file_record
            payload = await self._create_deleted_record_event_payload(record, data_record)

            if payload:
                # Add Gmail-specific metadata
                payload["connectorName"] = Connectors.GOOGLE_MAIL.value
                payload["origin"] = OriginTypes.CONNECTOR.value

                # Add Gmail-specific fields if available
                if mail_record:
                    payload["messageId"] = mail_record.get("messageId", "")
                    payload["threadId"] = mail_record.get("threadId", "")
                    payload["subject"] = mail_record.get("subject", "")
                    payload["from"] = mail_record.get("from", "")
                    payload["isAttachment"] = False
                elif file_record:
                    # This is an email attachment
                    payload["isAttachment"] = True
                    payload["attachmentId"] = file_record.get("attachmentId", "")

                await self._publish_record_event("deleteRecord", payload)
        except Exception as e:
            self.logger.error(f"❌ Failed to publish Gmail deletion event: {str(e)}")

    async def _create_new_record_event_payload(self, record_doc: Dict, file_doc: Dict, storage_url: str) -> Optional[Dict]:
        """
        Creates NewRecordEvent payload for Kafka.
        """
        try:
            record_id = record_doc["_key"]
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
        except Exception as e:
            self.logger.error(f"❌ Failed to create new record event payload: {str(e)}")
            return None

    async def _create_update_record_event_payload(
        self,
        record: Dict,
        file_record: Optional[Dict] = None,
        content_changed: bool = True
    ) -> Optional[Dict]:
        """Create update record event payload matching Node.js format"""
        try:
            endpoints = await self.config_service.get_config(
                config_node_constants.ENDPOINTS.value
            )
            storage_url = endpoints.get("storage").get("endpoint", DefaultEndpoints.STORAGE_ENDPOINT.value)

            signed_url_route = f"{storage_url}/api/v1/document/internal/{record['externalRecordId']}/download"

            # Get extension and mimeType from file record
            extension = ""
            mime_type = ""
            if file_record:
                extension = file_record.get("extension", "")
                mime_type = file_record.get("mimeType", "")

            return {
                "orgId": record.get("orgId"),
                "recordId": record.get("_key"),
                "version": record.get("version", 1),
                "extension": extension,
                "mimeType": mime_type,
                "signedUrlRoute": signed_url_route,
                "updatedAtTimestamp": str(record.get("updatedAtTimestamp", get_epoch_timestamp_in_ms())),
                "sourceLastModifiedTimestamp": str(record.get("sourceLastModifiedTimestamp", record.get("updatedAtTimestamp", get_epoch_timestamp_in_ms()))),
                "contentChanged": content_changed,
            }
        except Exception as e:
            self.logger.error(f"❌ Failed to create update record event payload: {str(e)}")
            return None

    async def _publish_upload_events(self, kb_id: str, result: Dict) -> None:
        """
        Enhanced event publishing with better error handling
        """
        try:
            self.logger.info(f"This is the result passed to publish record events {result}")
            # Get the full data of created files directly from the transaction result
            created_files_data = result.get("created_files_data", [])

            if not created_files_data:
                self.logger.info("No new records were created, skipping event publishing.")
                return

            self.logger.info(f"🚀 Publishing creation events for {len(created_files_data)} new records.")

            # Get storage endpoint
            try:
                endpoints = await self.config_service.get_config(
                    config_node_constants.ENDPOINTS.value
                )
                self.logger.info(f"This the the endpoint {endpoints}")
                storage_url = endpoints.get("storage").get("endpoint", DefaultEndpoints.STORAGE_ENDPOINT.value)
            except Exception as config_error:
                self.logger.error(f"❌ Failed to get storage config: {str(config_error)}")
                storage_url = "http://localhost:3000"  # Fallback

            # Create events with enhanced error handling
            successful_events = 0
            failed_events = 0

            for file_data in created_files_data:
                try:
                    record_doc = file_data.get("record")
                    file_doc = file_data.get("fileRecord")

                    if record_doc and file_doc:
                        # Create payload with error handling
                        create_payload = await self._create_new_record_event_payload(
                            record_doc, file_doc, storage_url
                        )

                        if create_payload:  # Only publish if payload creation succeeded
                            await self._publish_record_event("newRecord", create_payload)
                            successful_events += 1
                        else:
                            self.logger.warning(f"⚠️ Skipping event for record {record_doc.get('_key')} - payload creation failed")
                            failed_events += 1
                    else:
                        self.logger.warning(f"⚠️ Incomplete file data found, cannot publish event: {file_data}")
                        failed_events += 1

                except Exception as event_error:
                    self.logger.error(f"❌ Failed to publish event for file: {str(event_error)}")
                    failed_events += 1

            self.logger.info(f"✅ Event publishing complete: {successful_events} successful, {failed_events} failed")

        except Exception as e:
            self.logger.error(f"❌ Failed to publish upload events: {str(e)}")

    async def _create_reindex_event_payload(self, record: Dict, file_record: Optional[Dict], user_id: Optional[str] = None, request: Optional["Request"] = None) -> Dict:
        """Create reindex event payload"""
        try:
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
                storage_url = endpoints.get("storage").get("endpoint", DefaultEndpoints.STORAGE_ENDPOINT.value)
                signed_url_route = f"{storage_url}/api/v1/document/internal/{record['externalRecordId']}/download"
            else:
                connector_url = endpoints.get("connectors").get("endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value)
                signed_url_route = f"{connector_url}/api/v1/{record['orgId']}/{user_id}/{record['connectorName'].lower()}/record/{record['_key']}/signedUrl"

                if record.get("recordType") == "MAIL":
                    mime_type = "text/gmail_content"
                    try:
                        return {
                            "orgId": record.get("orgId"),
                            "recordId": record.get("_key"),
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

            return {
                "orgId": record.get("orgId"),
                "recordId": record.get("_key"),
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

    async def _validate_folder_creation(self, kb_id: str, user_id: str) -> Dict:
        """Shared validation logic for folder creation."""
        try:
            user = await self.get_user_by_user_id(user_id)
            if not user:
                return {"valid": False, "success": False, "code": 404, "reason": f"User not found: {user_id}"}
            user_key = user.get("_key")
            user_role = await self.get_user_kb_permission(kb_id, user_key)
            if user_role not in ["OWNER", "WRITER"]:
                return {
                    "valid": False,
                    "success": False,
                    "code": 403,
                    "reason": f"Insufficient permissions. Role: {user_role}",
                }
            return {"valid": True, "user": user, "user_key": user_key, "user_role": user_role}
        except Exception as e:
            return {"valid": False, "success": False, "code": 500, "reason": str(e)}

    async def find_folder_by_name_in_parent(
        self,
        kb_id: str,
        folder_name: str,
        parent_folder_id: Optional[str] = None,
        transaction: Optional[str] = None,
    ) -> Optional[Dict]:
        """Find a folder by name within a specific parent (KB root or folder)."""
        try:
            name_variants = self._normalized_name_variants_lower(folder_name)
            parent_from = f"records/{parent_folder_id}" if parent_folder_id else f"recordGroups/{kb_id}"
            if parent_folder_id is None:
                query = """
                FOR edge IN @@belongs_to
                    FILTER edge._to == CONCAT('recordGroups/', @kb_id)
                    FILTER edge.entityType == @entity_type
                    LET folder_record = DOCUMENT(edge._from)
                    FILTER folder_record != null
                    FILTER folder_record.isDeleted != true
                    LET isChild = LENGTH(
                        FOR relEdge IN @@record_relations
                            FILTER relEdge._to == folder_record._id
                            FILTER relEdge.relationshipType == "PARENT_CHILD"
                            RETURN 1
                    ) > 0
                    FILTER isChild == false
                    LET folder_file = FIRST(
                        FOR isEdge IN @@is_of_type
                            FILTER isEdge._from == folder_record._id
                            LET f = DOCUMENT(isEdge._to)
                            FILTER f != null AND f.isFile == false
                            RETURN f
                    )
                    FILTER folder_file != null
                    LET folder_name_l = LOWER(folder_record.recordName)
                    FILTER folder_name_l IN @name_variants
                    RETURN {
                        _key: folder_record._key,
                        name: folder_record.recordName,
                        recordGroupId: folder_record.connectorId,
                        orgId: folder_record.orgId
                    }
                """
                results = await self.execute_query(
                    query,
                    bind_vars={
                        "name_variants": name_variants,
                        "kb_id": kb_id,
                        "@belongs_to": CollectionNames.BELONGS_TO.value,
                        "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                        "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                        "entity_type": Connectors.KNOWLEDGE_BASE.value,
                    },
                    transaction=transaction,
                )
            else:
                query = """
                FOR edge IN @@record_relations
                    FILTER edge._from == @parent_from
                    FILTER edge.relationshipType == "PARENT_CHILD"
                    LET folder_record = DOCUMENT(edge._to)
                    FILTER folder_record != null
                    LET folder_file = FIRST(
                        FOR isEdge IN @@is_of_type
                            FILTER isEdge._from == folder_record._id
                            LET f = DOCUMENT(isEdge._to)
                            FILTER f != null AND f.isFile == false
                            RETURN f
                    )
                    FILTER folder_file != null
                    LET folder_name_l = LOWER(folder_record.recordName)
                    FILTER folder_name_l IN @name_variants
                    RETURN {
                        _key: folder_record._key,
                        name: folder_record.recordName,
                        recordGroupId: folder_record.connectorId,
                        orgId: folder_record.orgId
                    }
                """
                results = await self.execute_query(
                    query,
                    bind_vars={
                        "parent_from": parent_from,
                        "name_variants": name_variants,
                        "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                        "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                    },
                    transaction=transaction,
                )
            return results[0] if results else None
        except Exception as e:
            self.logger.error(f"❌ Failed to find folder by name: {str(e)}")
            return None

    async def get_and_validate_folder_in_kb(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[str] = None,
    ) -> Optional[Dict]:
        """Get folder by ID and validate it belongs to the specified KB."""
        try:
            query = """
            LET folder_record = DOCUMENT(@@records_collection, @folder_id)
            FILTER folder_record != null
            LET folder_file = FIRST(
                FOR isEdge IN @@is_of_type
                    FILTER isEdge._from == folder_record._id
                    LET f = DOCUMENT(isEdge._to)
                    FILTER f != null AND f.isFile == false
                    RETURN f
            )
            FILTER folder_file != null
            LET relationship = FIRST(
                FOR edge IN @@belongs_to_collection
                    FILTER edge._from == @folder_from
                    FILTER edge._to == @kb_to
                    FILTER edge.entityType == @entity_type
                    RETURN 1
            )
            FILTER relationship != null
            RETURN MERGE(
                folder_record,
                {
                    name: folder_file.name,
                    isFile: folder_file.isFile,
                    extension: folder_file.extension,
                    recordGroupId: folder_record.connectorId
                }
            )
            """
            results = await self.execute_query(
                query,
                bind_vars={
                    "folder_id": folder_id,
                    "folder_from": f"records/{folder_id}",
                    "kb_to": f"recordGroups/{kb_id}",
                    "entity_type": Connectors.KNOWLEDGE_BASE.value,
                    "@records_collection": CollectionNames.RECORDS.value,
                    "@belongs_to_collection": CollectionNames.BELONGS_TO.value,
                    "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                },
                transaction=transaction,
            )
            return results[0] if results else None
        except Exception as e:
            self.logger.error(f"❌ Failed to get and validate folder in KB: {str(e)}")
            return None

    async def create_folder(
        self,
        kb_id: str,
        folder_name: str,
        org_id: str,
        parent_folder_id: Optional[str] = None,
        transaction: Optional[str] = None,
    ) -> Optional[Dict]:
        """Create folder with proper RECORDS document and edges."""
        try:
            folder_id = str(uuid.uuid4())
            timestamp = get_epoch_timestamp_in_ms()
            txn_id = transaction
            if transaction is None:
                txn_id = await self.begin_transaction(
                    read=[],
                    write=[
                        CollectionNames.RECORDS.value,
                        CollectionNames.FILES.value,
                        CollectionNames.IS_OF_TYPE.value,
                        CollectionNames.BELONGS_TO.value,
                        CollectionNames.RECORD_RELATIONS.value,
                    ],
                )
            try:
                if parent_folder_id:
                    parent_folder = await self.get_and_validate_folder_in_kb(kb_id, parent_folder_id, transaction=txn_id)
                    if not parent_folder:
                        raise ValueError(f"Parent folder {parent_folder_id} not found in KB {kb_id}")
                existing_folder = await self.find_folder_by_name_in_parent(
                    kb_id=kb_id,
                    folder_name=folder_name,
                    parent_folder_id=parent_folder_id,
                    transaction=txn_id,
                )
                if existing_folder:
                    return {
                        "folderId": existing_folder["_key"],
                        "name": existing_folder["name"],
                        "webUrl": existing_folder.get("webUrl", ""),
                        "parent_folder_id": parent_folder_id,
                        "exists": True,
                        "success": True,
                    }
                external_parent_id = parent_folder_id if parent_folder_id else None
                kb_connector_id = f"knowledgeBase_{org_id}"
                record_data = {
                    "_key": folder_id,
                    "orgId": org_id,
                    "recordName": folder_name,
                    "externalRecordId": f"kb_folder_{folder_id}",
                    "connectorId": kb_connector_id,
                    "externalGroupId": kb_id,
                    "externalParentId": external_parent_id,
                    "externalRootGroupId": kb_id,
                    "recordType": RecordType.FILE.value,
                    "version": 0,
                    "origin": OriginTypes.UPLOAD.value,
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
                    "isVLMOcrProcessed": False,
                    "indexingStatus": "COMPLETED",
                    "extractionStatus": "COMPLETED",
                    "isLatestVersion": True,
                    "isDirty": False,
                }
                folder_data = {
                    "_key": folder_id,
                    "orgId": org_id,
                    "name": folder_name,
                    "isFile": False,
                    "extension": None,
                }
                await self.batch_upsert_nodes([record_data], CollectionNames.RECORDS.value, transaction=txn_id)
                await self.batch_upsert_nodes([folder_data], CollectionNames.FILES.value, transaction=txn_id)
                is_of_type_edge = {
                    "from_id": folder_id,
                    "from_collection": CollectionNames.RECORDS.value,
                    "to_id": folder_id,
                    "to_collection": CollectionNames.FILES.value,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                }
                await self.batch_create_edges([is_of_type_edge], CollectionNames.IS_OF_TYPE.value, transaction=txn_id)
                kb_relationship_edge = {
                    "from_id": folder_id,
                    "from_collection": CollectionNames.RECORDS.value,
                    "to_id": kb_id,
                    "to_collection": CollectionNames.RECORD_GROUPS.value,
                    "entityType": Connectors.KNOWLEDGE_BASE.value,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                }
                await self.batch_create_edges([kb_relationship_edge], CollectionNames.BELONGS_TO.value, transaction=txn_id)
                if parent_folder_id:
                    parent_child_edge = {
                        "from_id": parent_folder_id,
                        "from_collection": CollectionNames.RECORDS.value,
                        "to_id": folder_id,
                        "to_collection": CollectionNames.RECORDS.value,
                        "relationshipType": "PARENT_CHILD",
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp,
                    }
                    await self.batch_create_edges([parent_child_edge], CollectionNames.RECORD_RELATIONS.value, transaction=txn_id)
                if transaction is None and txn_id:
                    await self.commit_transaction(txn_id)
                return {
                    "id": folder_id,
                    "name": folder_name,
                    "webUrl": record_data["webUrl"],
                    "exists": False,
                    "success": True,
                }
            except Exception as inner_error:
                if transaction is None and txn_id:
                    await self.rollback_transaction(txn_id)
                raise inner_error
        except Exception as e:
            self.logger.error(f"❌ Failed to create folder '{folder_name}': {str(e)}")
            raise

    async def get_folder_contents(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[str] = None,
    ) -> Optional[Dict]:
        """Get folder contents (container, folders, records)."""
        result = await self.get_folder_children(
            kb_id=kb_id,
            folder_id=folder_id,
            skip=0,
            limit=10000,
            level=1,
            transaction=transaction,
        )
        return result if result.get("success") else None

    async def validate_folder_in_kb(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[str] = None,
    ) -> bool:
        """Validate that a folder exists and belongs to the KB."""
        try:
            query = """
            LET folder_record = DOCUMENT(@@records_collection, @folder_id)
            FILTER folder_record != null
            LET folder_file = FIRST(
                FOR isEdge IN @@is_of_type
                    FILTER isEdge._from == folder_record._id
                    LET f = DOCUMENT(isEdge._to)
                    FILTER f != null AND f.isFile == false
                    RETURN f
            )
            LET folder_valid = folder_record != null AND folder_file != null
            LET relationship = folder_valid ? FIRST(
                FOR edge IN @@belongs_to_collection
                    FILTER edge._from == @folder_from
                    FILTER edge._to == @kb_to
                    FILTER edge.entityType == @entity_type
                    RETURN 1
            ) : null
            RETURN folder_valid AND relationship != null
            """
            results = await self.execute_query(
                query,
                bind_vars={
                    "folder_id": folder_id,
                    "folder_from": f"records/{folder_id}",
                    "kb_to": f"recordGroups/{kb_id}",
                    "entity_type": Connectors.KNOWLEDGE_BASE.value,
                    "@records_collection": CollectionNames.RECORDS.value,
                    "@belongs_to_collection": CollectionNames.BELONGS_TO.value,
                    "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                },
                transaction=transaction,
            )
            return bool(results and results[0])
        except Exception as e:
            self.logger.error(f"❌ Failed to validate folder in KB: {str(e)}")
            return False

    async def validate_folder_exists_in_kb(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[str] = None
    ) -> bool:
        """
        Validate folder exists in specific KB.
        Uses direct KB ID check instead of edge traversal.
        """
        try:
            query = """
            FOR folder IN @@files_collection
                FILTER folder._key == @folder_id
                FILTER folder.recordGroupId == @kb_id
                FILTER folder.isFile == false
                RETURN true
            """

            results = await self.execute_query(
                query,
                bind_vars={
                    "folder_id": folder_id,
                    "kb_id": kb_id,
                    "@files_collection": CollectionNames.FILES.value,
                },
                transaction=transaction,
            )

            return bool(results and results[0])

        except Exception as e:
            self.logger.error(f"❌ Failed to validate folder exists in KB: {str(e)}")
            return False

    async def update_folder(
        self,
        folder_id: str,
        updates: Dict,
        transaction: Optional[str] = None,
    ) -> bool:
        """Update folder."""
        try:
            query = """
            FOR folder IN @@folder_collection
                FILTER folder._key == @folder_id
                UPDATE folder WITH @updates IN @@folder_collection
                RETURN NEW
            """
            results = await self.execute_query(
                query,
                bind_vars={
                    "folder_id": folder_id,
                    "updates": updates,
                    "@folder_collection": CollectionNames.FILES.value,
                },
                transaction=transaction,
            )
            result = results[0] if results else None
            if result:
                updates_for_record = {
                    "_key": folder_id,
                    "recordName": updates.get("name"),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                }
                await self.batch_upsert_nodes([updates_for_record], CollectionNames.RECORDS.value, transaction=transaction)
            return bool(result)
        except Exception as e:
            self.logger.error(f"❌ Failed to update folder: {str(e)}")
            raise

    async def delete_folder(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[str] = None,
    ) -> bool:
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
                inventory_query = """
                LET target_folder_record = DOCUMENT("records", @folder_id)
                FILTER target_folder_record != null
                LET target_folder_file = FIRST(
                    FOR isEdge IN @@is_of_type
                        FILTER isEdge._from == target_folder_record._id
                        LET f = DOCUMENT(isEdge._to)
                        FILTER f != null AND f.isFile == false
                        RETURN f
                )
                FILTER target_folder_file != null
                LET all_subfolders = (
                    FOR v, e, p IN 1..20 OUTBOUND target_folder_record._id @@record_relations
                        FILTER e.relationshipType == "PARENT_CHILD"
                        LET subfolder_file = FIRST(
                            FOR isEdge IN @@is_of_type
                                FILTER isEdge._from == v._id
                                LET f = DOCUMENT(isEdge._to)
                                FILTER f != null AND f.isFile == false
                                RETURN 1
                        )
                        FILTER subfolder_file != null
                        RETURN v._key
                )
                LET all_folders = APPEND([target_folder_record._key], all_subfolders)
                LET all_folder_records_with_details = (
                    FOR v, e, p IN 1..20 OUTBOUND target_folder_record._id @@record_relations
                        FILTER e.relationshipType == "PARENT_CHILD"
                        LET vertex = v
                        FILTER vertex != null
                        LET vertex_file = FIRST(
                            FOR isEdge IN @@is_of_type
                                FILTER isEdge._from == vertex._id
                                LET f = DOCUMENT(isEdge._to)
                                FILTER f != null
                                RETURN f
                        )
                        FILTER vertex_file != null AND vertex_file.isFile == true
                        RETURN { record: vertex, file_record: vertex_file }
                )
                LET all_file_records = (
                    FOR record_data IN all_folder_records_with_details
                        FILTER record_data.file_record != null
                        RETURN record_data.file_record._key
                )
                RETURN {
                    folder_exists: target_folder_record != null AND target_folder_file != null,
                    target_folder: target_folder_record._key,
                    all_folders: all_folders,
                    subfolders: all_subfolders,
                    records_with_details: all_folder_records_with_details,
                    file_records: all_file_records,
                    total_folders: LENGTH(all_folders),
                    total_subfolders: LENGTH(all_subfolders),
                    total_records: LENGTH(all_folder_records_with_details),
                    total_file_records: LENGTH(all_file_records)
                }
                """
                inv_results = await self.execute_query(
                    inventory_query,
                    bind_vars={
                        "folder_id": folder_id,
                        "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                        "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                    },
                    transaction=txn_id,
                )
                inventory = inv_results[0] if inv_results else {}
                if not inventory.get("folder_exists"):
                    if transaction is None and txn_id:
                        await self.rollback_transaction(txn_id)
                    return False
                records_with_details = inventory.get("records_with_details", [])
                all_record_keys = [rd["record"]["_key"] for rd in records_with_details]
                all_folders = inventory.get("all_folders", [])
                file_records = inventory.get("file_records", [])

                if all_record_keys or all_folders:
                    rel_delete = """
                    LET record_edges = (FOR record_key IN @all_records FOR rec_edge IN @@record_relations FILTER rec_edge._from == CONCAT('records/', record_key) OR rec_edge._to == CONCAT('records/', record_key) RETURN rec_edge._key)
                    LET folder_edges = (FOR folder_key IN @all_folders FOR folder_edge IN @@record_relations FILTER folder_edge._from == CONCAT('records/', folder_key) OR folder_edge._to == CONCAT('records/', folder_key) RETURN folder_edge._key)
                    LET all_relation_edges = APPEND(record_edges, folder_edges)
                    FOR edge_key IN all_relation_edges REMOVE edge_key IN @@record_relations OPTIONS { ignoreErrors: true }
                    """
                    await self.execute_query(rel_delete, bind_vars={"all_records": all_record_keys, "all_folders": all_folders, "@record_relations": CollectionNames.RECORD_RELATIONS.value}, transaction=txn_id)
                    iot_delete = """
                    LET record_type_edges = (FOR record_key IN @all_records FOR type_edge IN @@is_of_type FILTER type_edge._from == CONCAT('records/', record_key) RETURN type_edge._key)
                    LET folder_type_edges = (FOR folder_key IN @all_folders FOR type_edge IN @@is_of_type FILTER type_edge._from == CONCAT('records/', folder_key) RETURN type_edge._key)
                    LET all_type_edges = APPEND(record_type_edges, folder_type_edges)
                    FOR edge_key IN all_type_edges REMOVE edge_key IN @@is_of_type OPTIONS { ignoreErrors: true }
                    """
                    await self.execute_query(iot_delete, bind_vars={"all_records": all_record_keys, "all_folders": all_folders, "@is_of_type": CollectionNames.IS_OF_TYPE.value}, transaction=txn_id)
                    btk_delete = """
                    LET record_kb_edges = (FOR record_key IN @all_records FOR record_kb_edge IN @@belongs_to_kb FILTER record_kb_edge._from == CONCAT('records/', record_key) RETURN record_kb_edge._key)
                    LET folder_kb_edges = (FOR folder_key IN @all_folders FOR folder_kb_edge IN @@belongs_to_kb FILTER folder_kb_edge._from == CONCAT('records/', folder_key) RETURN folder_kb_edge._key)
                    LET all_kb_edges = APPEND(record_kb_edges, folder_kb_edges)
                    FOR edge_key IN all_kb_edges REMOVE edge_key IN @@belongs_to_kb OPTIONS { ignoreErrors: true }
                    """
                    await self.execute_query(btk_delete, bind_vars={"all_records": all_record_keys, "all_folders": all_folders, "@belongs_to_kb": CollectionNames.BELONGS_TO.value}, transaction=txn_id)
                if file_records:
                    await self.execute_query("FOR file_key IN @file_keys REMOVE file_key IN @@files_collection OPTIONS { ignoreErrors: true }", bind_vars={"file_keys": file_records, "@files_collection": CollectionNames.FILES.value}, transaction=txn_id)
                if all_record_keys:
                    await self.execute_query("FOR record_key IN @record_keys REMOVE record_key IN @@records_collection OPTIONS { ignoreErrors: true }", bind_vars={"record_keys": all_record_keys, "@records_collection": CollectionNames.RECORDS.value}, transaction=txn_id)
                if all_folders:
                    ff_query = """
                    FOR folder_key IN @folder_keys
                        LET folder_record = DOCUMENT("records", folder_key)
                        FILTER folder_record != null
                        LET folder_file = FIRST(FOR isEdge IN @@is_of_type FILTER isEdge._from == folder_record._id LET f = DOCUMENT(isEdge._to) FILTER f != null AND f.isFile == false RETURN f._key)
                        FILTER folder_file != null
                        RETURN folder_file
                    """
                    ff_res = await self.execute_query(ff_query, bind_vars={"folder_keys": all_folders, "@is_of_type": CollectionNames.IS_OF_TYPE.value}, transaction=txn_id)
                    folder_file_keys = list(ff_res) if ff_res else []
                    if folder_file_keys:
                        await self.execute_query("FOR file_key IN @file_keys REMOVE file_key IN @@files_collection OPTIONS { ignoreErrors: true }", bind_vars={"file_keys": folder_file_keys, "@files_collection": CollectionNames.FILES.value}, transaction=txn_id)
                    reversed_folders = list(reversed(all_folders))
                    await self.execute_query("FOR folder_key IN @folder_keys REMOVE folder_key IN @@records_collection OPTIONS { ignoreErrors: true }", bind_vars={"folder_keys": reversed_folders, "@records_collection": CollectionNames.RECORDS.value}, transaction=txn_id)
                if transaction is None and txn_id:
                    await self.commit_transaction(txn_id)
                self.logger.info(f"✅ Folder {folder_id} and nested content deleted.")
                return True
            except Exception as db_error:
                if transaction is None and txn_id:
                    await self.rollback_transaction(txn_id)
                raise db_error
        except Exception as e:
            self.logger.error(f"❌ Failed to delete folder: {str(e)}")
            return False

    async def update_record(
        self,
        record_id: str,
        user_id: str,
        updates: Dict,
        file_metadata: Optional[Dict] = None,
        transaction: Optional[str] = None,
    ) -> Optional[Dict]:
        """Update a record by ID with automatic KB and permission detection."""
        try:
            user = await self.get_user_by_user_id(user_id)
            if not user:
                return {"success": False, "code": 404, "reason": f"User not found: {user_id}"}
            timestamp = get_epoch_timestamp_in_ms()
            processed_updates = {**updates, "updatedAtTimestamp": timestamp}
            if file_metadata:
                processed_updates.setdefault("sourceLastModifiedTimestamp", file_metadata.get("lastModified", timestamp))
            update_query = """
            FOR record IN @@records_collection
                FILTER record._key == @record_id
                UPDATE record WITH @updates IN @@records_collection
                RETURN NEW
            """
            results = await self.execute_query(
                update_query,
                bind_vars={
                    "record_id": record_id,
                    "updates": processed_updates,
                    "@records_collection": CollectionNames.RECORDS.value,
                },
                transaction=transaction,
            )
            updated_record = results[0] if results else None
            if not updated_record:
                return {"success": False, "code": 500, "reason": f"Failed to update record {record_id}"}

            # Publish update event (after successful update)
            try:
                # Get file record for event payload
                file_record = await self.get_document(record_id, CollectionNames.FILES.value)

                # Determine if content changed (if file metadata provided, content likely changed)
                content_changed = file_metadata is not None

                update_payload = await self._create_update_record_event_payload(
                    updated_record, file_record, content_changed=content_changed
                )
                if update_payload:
                    await self._publish_record_event("updateRecord", update_payload)
            except Exception as event_error:
                self.logger.error(f"❌ Failed to publish update event: {str(event_error)}")
                # Don't fail the main operation for event publishing errors

            return {
                "success": True,
                "updatedRecord": updated_record,
                "recordId": record_id,
                "timestamp": timestamp,
            }
        except Exception as e:
            self.logger.error(f"❌ Failed to update record: {str(e)}")
            return {"success": False, "code": 500, "reason": str(e)}

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
                validation_query = """
                LET records_with_details = (
                    FOR rid IN @record_ids
                        LET record = DOCUMENT("records", rid)
                        LET record_exists = record != null
                        LET record_not_deleted = record_exists ? record.isDeleted != true : false
                        LET kb_relationship = record_exists ? FIRST(FOR edge IN @@belongs_to_kb FILTER edge._from == CONCAT('records/', rid) FILTER edge._to == CONCAT('recordGroups/', @kb_id) RETURN edge) : null
                        LET folder_relationship = @folder_id ? (record_exists ? FIRST(FOR edge_rel IN @@record_relations FILTER edge_rel._to == CONCAT('records/', rid) FILTER edge_rel._from == CONCAT('records/', @folder_id) FILTER edge_rel.relationshipType == "PARENT_CHILD" RETURN edge_rel) : null) : true
                        LET file_record = record_exists ? FIRST(FOR isEdge IN @@is_of_type FILTER isEdge._from == CONCAT('records/', rid) LET fileRec = DOCUMENT(isEdge._to) FILTER fileRec != null RETURN fileRec) : null
                        LET is_valid = record_exists AND record_not_deleted AND kb_relationship != null AND folder_relationship != null
                        RETURN { record_id: rid, record: record, file_record: file_record, is_valid: is_valid }
                )
                LET valid_records = records_with_details[* FILTER CURRENT.is_valid]
                LET invalid_records = records_with_details[* FILTER !CURRENT.is_valid]
                RETURN { valid_records: valid_records, invalid_records: invalid_records }
                """
                val_results = await self.execute_query(
                    validation_query,
                    bind_vars={
                        "record_ids": record_ids,
                        "kb_id": kb_id,
                        "folder_id": folder_id,
                        "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                        "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                        "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                    },
                    transaction=txn_id,
                )
                val = val_results[0] if val_results else {}
                valid_records = val.get("valid_records", [])
                invalid_records = val.get("invalid_records", [])
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
                file_record_ids = [r["file_record"]["_key"] for r in valid_records if r.get("file_record")]

                edges_cleanup = """
                FOR record_id IN @record_ids
                    FOR rec_rel_edge IN @@record_relations
                        FILTER rec_rel_edge._from == CONCAT('records/', record_id) OR rec_rel_edge._to == CONCAT('records/', record_id)
                        REMOVE rec_rel_edge IN @@record_relations
                    FOR iot_edge IN @@is_of_type
                        FILTER iot_edge._from == CONCAT('records/', record_id)
                        REMOVE iot_edge IN @@is_of_type
                    FOR btk_edge IN @@belongs_to_kb
                        FILTER btk_edge._from == CONCAT('records/', record_id)
                        REMOVE btk_edge IN @@belongs_to_kb
                """
                await self.execute_query(edges_cleanup, bind_vars={"record_ids": valid_record_ids, "@record_relations": CollectionNames.RECORD_RELATIONS.value, "@is_of_type": CollectionNames.IS_OF_TYPE.value, "@belongs_to_kb": CollectionNames.BELONGS_TO.value}, transaction=txn_id)
                if file_record_ids:
                    await self.execute_query("FOR file_key IN @file_keys REMOVE file_key IN @@files_collection OPTIONS { ignoreErrors: true }", bind_vars={"file_keys": file_record_ids, "@files_collection": CollectionNames.FILES.value}, transaction=txn_id)
                await self.execute_query("FOR record_key IN @record_keys REMOVE record_key IN @@records_collection OPTIONS { ignoreErrors: true }", bind_vars={"record_keys": valid_record_ids, "@records_collection": CollectionNames.RECORDS.value}, transaction=txn_id)
                deleted_records = [{"record_id": r["record_id"], "name": r.get("record", {}).get("recordName", "Unknown")} for r in valid_records]
                if transaction is None and txn_id:
                    await self.commit_transaction(txn_id)
                return {
                    "success": True,
                    "deleted_records": deleted_records,
                    "failed_records": failed_records,
                    "total_requested": len(record_ids),
                    "successfully_deleted": len(deleted_records),
                    "failed_count": len(failed_records),
                    "folder_id": folder_id,
                    "kb_id": kb_id,
                }
            except Exception as db_error:
                if transaction is None and txn_id:
                    await self.rollback_transaction(txn_id)
                raise db_error
        except Exception as e:
            self.logger.error(f"❌ Failed bulk record deletion: {str(e)}")
            return {"success": False, "reason": str(e)}

    async def create_kb_permissions(
        self,
        kb_id: str,
        requester_id: str,
        user_ids: List[str],
        team_ids: List[str],
        role: str,
    ) -> Dict:
        """Create KB permissions for users and teams."""
        try:
            timestamp = get_epoch_timestamp_in_ms()
            main_query = """
            LET requester_info = FIRST(
                FOR user IN @@users_collection
                FILTER user.userId == @requester_id
                FOR perm IN @@permissions_collection
                    FILTER perm._from == CONCAT('users/', user._key)
                    FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                    FILTER perm.type == "USER"
                    FILTER perm.role == "OWNER"
                RETURN { user_key: user._key, is_owner: true }
            )
            LET kb_exists = LENGTH(FOR kb IN @@recordGroups_collection FILTER kb._key == @kb_id LIMIT 1 RETURN 1) > 0
            LET user_operations = (
                FOR user_id IN @user_ids
                    LET user = FIRST(FOR u IN @@users_collection FILTER u._key == user_id RETURN u)
                    LET current_perm = user ? FIRST(FOR perm IN @@permissions_collection FILTER perm._from == CONCAT('users/', user._key) FILTER perm._to == CONCAT('recordGroups/', @kb_id) FILTER perm.type == "USER" RETURN perm) : null
                    FILTER user != null
                    LET operation = current_perm == null ? "insert" : (current_perm.role != @role ? "update" : "skip")
                    RETURN { user_id: user_id, user_key: user._key, userId: user.userId, name: user.fullName, operation: operation, current_role: current_perm ? current_perm.role : null, perm_key: current_perm ? current_perm._key : null }
            )
            LET team_operations = (
                FOR team_id IN @team_ids
                    LET team = FIRST(FOR t IN @@teams_collection FILTER t._key == team_id RETURN t)
                    LET current_perm = team ? FIRST(FOR perm IN @@permissions_collection FILTER perm._from == CONCAT('teams/', team._key) FILTER perm._to == CONCAT('recordGroups/', @kb_id) FILTER perm.type == "TEAM" RETURN perm) : null
                    FILTER team != null
                    LET operation = current_perm == null ? "insert" : "skip"
                    RETURN { team_id: team_id, team_key: team._key, name: team.name, operation: operation, perm_key: current_perm ? current_perm._key : null }
            )
            RETURN {
                is_valid: requester_info != null AND kb_exists,
                requester_found: requester_info != null,
                kb_exists: kb_exists,
                user_operations: user_operations,
                team_operations: team_operations,
                users_to_insert: user_operations[* FILTER CURRENT.operation == "insert"],
                teams_to_insert: team_operations[* FILTER CURRENT.operation == "insert"],
            }
            """
            results = await self.execute_query(
                main_query,
                bind_vars={
                    "kb_id": kb_id,
                    "requester_id": requester_id,
                    "user_ids": user_ids,
                    "team_ids": team_ids,
                    "role": role,
                    "@users_collection": CollectionNames.USERS.value,
                    "@teams_collection": CollectionNames.TEAMS.value,
                    "@permissions_collection": CollectionNames.PERMISSION.value,
                    "@recordGroups_collection": CollectionNames.RECORD_GROUPS.value,
                },
            )
            result = results[0] if results else {}
            if not result.get("is_valid"):
                if not result.get("requester_found"):
                    return {"success": False, "reason": "Requester not found or not owner", "code": 403}
                if not result.get("kb_exists"):
                    return {"success": False, "reason": "Knowledge base not found", "code": 404}
            users_to_insert = result.get("users_to_insert", [])
            teams_to_insert = result.get("teams_to_insert", [])
            insert_docs = []
            for u in users_to_insert:
                insert_docs.append({
                    "from_id": u["user_key"],
                    "from_collection": CollectionNames.USERS.value,
                    "to_id": kb_id,
                    "to_collection": CollectionNames.RECORD_GROUPS.value,
                    "externalPermissionId": "",
                    "type": "USER",
                    "role": role,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                    "lastUpdatedTimestampAtSource": timestamp,
                })
            for t in teams_to_insert:
                insert_docs.append({
                    "from_id": t["team_key"],
                    "from_collection": CollectionNames.TEAMS.value,
                    "to_id": kb_id,
                    "to_collection": CollectionNames.RECORD_GROUPS.value,
                    "externalPermissionId": "",
                    "type": "TEAM",
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                    "lastUpdatedTimestampAtSource": timestamp,
                })
            if insert_docs:
                await self.batch_create_edges(insert_docs, CollectionNames.PERMISSION.value)
            granted_count = len(users_to_insert) + len(teams_to_insert)
            return {
                "success": True,
                "grantedCount": granted_count,
                "grantedUsers": [u["user_id"] for u in users_to_insert],
                "grantedTeams": [t["team_id"] for t in teams_to_insert],
                "role": role,
                "kbId": kb_id,
            }
        except Exception as e:
            self.logger.error(f"❌ Failed to create KB permissions: {str(e)}")
            return {"success": False, "reason": str(e), "code": 500}

    async def count_kb_owners(
        self,
        kb_id: str,
        transaction: Optional[str] = None,
    ) -> int:
        """Count the number of owners for a knowledge base."""
        try:
            query = """
            FOR perm IN @@permissions_collection
                FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                FILTER perm.role == 'OWNER'
                COLLECT WITH COUNT INTO owner_count
                RETURN owner_count
            """
            results = await self.execute_query(
                query,
                bind_vars={
                    "kb_id": kb_id,
                    "@permissions_collection": CollectionNames.PERMISSION.value,
                },
                transaction=transaction,
            )
            count = results[0] if results else 0
            return count
        except Exception as e:
            self.logger.error(f"❌ Failed to count KB owners: {str(e)}")
            return 0

    async def remove_kb_permission(
        self,
        kb_id: str,
        user_ids: List[str],
        team_ids: List[str],
        transaction: Optional[str] = None,
    ) -> bool:
        """Remove permissions for multiple users and teams from a KB."""
        try:
            conditions = []
            bind_vars: Dict[str, Any] = {
                "kb_id": kb_id,
                "@permissions_collection": CollectionNames.PERMISSION.value,
            }
            if user_ids:
                conditions.append("(perm._from IN @user_froms AND perm.type == 'USER')")
                bind_vars["user_froms"] = [f"users/{uid}" for uid in user_ids]
            if team_ids:
                conditions.append("(perm._from IN @team_froms AND perm.type == 'TEAM')")
                bind_vars["team_froms"] = [f"teams/{tid}" for tid in team_ids]
            if not conditions:
                return False
            query = f"""
            FOR perm IN @@permissions_collection
                FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                FILTER ({' OR '.join(conditions)})
                REMOVE perm IN @@permissions_collection
                RETURN OLD._key
            """
            results = await self.execute_query(query, bind_vars=bind_vars, transaction=transaction)
            if results:
                self.logger.info(f"✅ Removed {len(results)} permissions from KB {kb_id}")
                return True
            self.logger.warning(f"⚠️ No permissions found to remove from KB {kb_id}")
            return False
        except Exception as e:
            self.logger.error(f"❌ Failed to remove KB permissions: {str(e)}")
            return False

    async def get_kb_permissions(
        self,
        kb_id: str,
        user_ids: Optional[List[str]] = None,
        team_ids: Optional[List[str]] = None,
        transaction: Optional[str] = None,
    ) -> Dict[str, Dict[str, str]]:
        """Get current roles for multiple users and teams on a KB."""
        try:
            conditions = []
            bind_vars: Dict[str, Any] = {
                "kb_id": kb_id,
                "@permissions_collection": CollectionNames.PERMISSION.value,
            }
            if user_ids:
                conditions.append("(perm._from IN @user_froms AND perm.type == 'USER')")
                bind_vars["user_froms"] = [f"users/{uid}" for uid in user_ids]
            if team_ids:
                conditions.append("(perm._from IN @team_froms AND perm.type == 'TEAM')")
                bind_vars["team_froms"] = [f"teams/{tid}" for tid in team_ids]
            if not conditions:
                return {"users": {}, "teams": {}}
            query = f"""
            FOR perm IN @@permissions_collection
                FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                FILTER ({' OR '.join(conditions)})
                RETURN {{
                    id: SPLIT(perm._from, '/')[1],
                    type: perm.type,
                    role: perm.role
                }}
            """
            results = await self.execute_query(query, bind_vars=bind_vars, transaction=transaction)
            result = {"users": {}, "teams": {}}
            for perm in results or []:
                if perm.get("type") == "USER":
                    result["users"][perm["id"]] = perm.get("role", "")
                elif perm.get("type") == "TEAM":
                    result["teams"][perm["id"]] = None
            return result
        except Exception as e:
            self.logger.error(f"❌ Failed to get KB permissions: {str(e)}")
            raise

    async def update_kb_permission(
        self,
        kb_id: str,
        requester_id: str,
        user_ids: List[str],
        team_ids: List[str],
        new_role: str,
    ) -> Optional[Dict]:
        """Update permissions for users/teams on a KB (only users; teams don't have roles)."""
        try:
            if not user_ids and not team_ids:
                return {"success": False, "reason": "No users or teams provided", "code": "400"}
            valid_roles = ["OWNER", "ORGANIZER", "FILEORGANIZER", "WRITER", "COMMENTER", "READER"]
            if new_role not in valid_roles:
                return {"success": False, "reason": f"Invalid role. Must be one of: {', '.join(valid_roles)}", "code": "400"}
            user = await self.get_user_by_user_id(requester_id)
            if not user:
                return {"success": False, "reason": "Requester not found", "code": 403}
            requester_key = user.get("_key")
            requester_perm = await self.get_user_kb_permission(kb_id, requester_key)
            if requester_perm != "OWNER":
                return {"success": False, "reason": "Only KB owners can update permissions", "code": "403"}
            timestamp = get_epoch_timestamp_in_ms()
            target_conditions = []
            bind_vars: Dict[str, Any] = {
                "kb_id": kb_id,
                "new_role": new_role,
                "timestamp": timestamp,
                "@permission": CollectionNames.PERMISSION.value,
            }
            if user_ids:
                target_conditions.append("(perm._from IN @user_froms AND perm.type == 'USER' AND perm.role != 'OWNER')")
                bind_vars["user_froms"] = [f"users/{uid}" for uid in user_ids]
            if not target_conditions:
                return {"success": True, "kb_id": kb_id, "new_role": new_role, "updated_permissions": 0, "updated_users": 0, "updated_teams": 0}
            update_query = f"""
            FOR perm IN @@permission
                FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                FILTER ({' OR '.join(target_conditions)})
                UPDATE perm WITH {{ role: @new_role, updatedAtTimestamp: @timestamp, lastUpdatedTimestampAtSource: @timestamp }} IN @@permission
                RETURN {{ _key: NEW._key, id: SPLIT(NEW._from, '/')[1], type: NEW.type, old_role: OLD.role, new_role: NEW.role }}
            """
            results = await self.execute_query(update_query, bind_vars=bind_vars)
            updated = results or []
            return {
                "success": True,
                "kb_id": kb_id,
                "new_role": new_role,
                "updated_permissions": len(updated),
                "updated_users": len(updated),
                "updated_teams": 0,
            }
        except Exception as e:
            self.logger.error(f"❌ Failed to update KB permission: {str(e)}")
            return {"success": False, "reason": str(e), "code": "500"}

    async def list_kb_permissions(
        self,
        kb_id: str,
        transaction: Optional[str] = None,
    ) -> List[Dict]:
        """List all permissions for a KB with entity details."""
        try:
            query = """
            LET perms_with_ids = (
                FOR perm IN @@permissions_collection
                    FILTER perm._to == @kb_to
                    RETURN { perm: perm, entity_id: perm._from }
            )
            LET user_ids = UNIQUE(perms_with_ids[* FILTER STARTS_WITH(CURRENT.entity_id, "users/")].entity_id)
            LET users = (
                FOR user_id IN user_ids
                    LET user = DOCUMENT(user_id)
                    FILTER user != null
                    RETURN { _id: user._id, _key: user._key, fullName: user.fullName, name: user.name, userName: user.userName, userId: user.userId, email: user.email }
            )
            LET team_ids = UNIQUE(perms_with_ids[* FILTER STARTS_WITH(CURRENT.entity_id, "teams/")].entity_id)
            LET teams = (
                FOR team_id IN team_ids
                    LET team = DOCUMENT(team_id)
                    FILTER team != null
                    RETURN { _id: team._id, _key: team._key, name: team.name }
            )
            FOR perm_data IN perms_with_ids
                LET perm = perm_data.perm
                LET entity = STARTS_WITH(perm_data.entity_id, "users/")
                    ? FIRST(FOR u IN users FILTER u._id == perm_data.entity_id RETURN u)
                    : FIRST(FOR t IN teams FILTER t._id == perm_data.entity_id RETURN t)
                FILTER entity != null
                RETURN {
                    id: entity._key,
                    name: entity.fullName || entity.name || entity.userName,
                    userId: entity.userId,
                    email: entity.email,
                    role: perm.type == "TEAM" ? null : perm.role,
                    type: perm.type,
                    createdAtTimestamp: perm.createdAtTimestamp,
                    updatedAtTimestamp: perm.updatedAtTimestamp
                }
            """
            results = await self.execute_query(
                query,
                bind_vars={"kb_to": f"recordGroups/{kb_id}", "@permissions_collection": CollectionNames.PERMISSION.value},
                transaction=transaction,
            )
            return results or []
        except Exception as e:
            self.logger.error(f"❌ Failed to list KB permissions: {str(e)}")
            return []

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
        try:
            include_kb = source in ("all", "local")
            include_connector = source in ("all", "connector")
            base_roles = {"OWNER", "READER", "FILEORGANIZER", "WRITER", "COMMENTER", "ORGANIZER"}
            final_kb_roles = list(base_roles.intersection(set(permissions or []))) if permissions else list(base_roles)
            if permissions and not final_kb_roles:
                include_kb = False
            user_from = f"users/{user_id}"
            filter_conditions = []
            filter_bind: Dict[str, Any] = {}
            if search:
                filter_conditions.append("(LIKE(LOWER(record.recordName), @search) OR LIKE(LOWER(record.externalRecordId), @search))")
                filter_bind["search"] = f"%{(search or '').lower()}%"
            if record_types:
                filter_conditions.append("record.recordType IN @record_types")
                filter_bind["record_types"] = record_types
            if origins:
                filter_conditions.append("record.origin IN @origins")
                filter_bind["origins"] = origins
            if connectors:
                filter_conditions.append("record.connectorName IN @connectors")
                filter_bind["connectors"] = connectors
            if indexing_status:
                filter_conditions.append("record.indexingStatus IN @indexing_status")
                filter_bind["indexing_status"] = indexing_status
            if date_from:
                filter_conditions.append("record.createdAtTimestamp >= @date_from")
                filter_bind["date_from"] = date_from
            if date_to:
                filter_conditions.append("record.createdAtTimestamp <= @date_to")
                filter_bind["date_to"] = date_to
            record_filter = " AND " + " AND ".join(filter_conditions) if filter_conditions else ""
            perm_filter = " AND permissionEdge.role IN @permissions" if permissions else ""
            sort_field = sort_by if sort_by in ("recordName", "createdAtTimestamp", "updatedAtTimestamp", "recordType") else "recordName"
            main_query = f"""
            LET user_from = @user_from
            LET org_id = @org_id
            LET directKbAccess = (
                FOR kbEdge IN @@permission
                    FILTER kbEdge._from == user_from
                    FILTER kbEdge.type == "USER"
                    FILTER kbEdge.role IN @kb_permissions
                    LET kb = DOCUMENT(kbEdge._to)
                    FILTER kb != null AND kb.orgId == org_id
                    RETURN {{ kb_id: kb._key, kb_doc: kb, role: kbEdge.role }}
            )
            LET teamKbAccess = (
                FOR teamKbPerm IN @@permission
                    FILTER teamKbPerm.type == "TEAM"
                    FILTER STARTS_WITH(teamKbPerm._to, "recordGroups/")
                    LET kb = DOCUMENT(teamKbPerm._to)
                    FILTER kb != null AND kb.orgId == org_id
                    LET team_id = SPLIT(teamKbPerm._from, '/')[1]
                    LET user_team_perm = FIRST(FOR userTeamPerm IN @@permission FILTER userTeamPerm._from == user_from FILTER userTeamPerm._to == CONCAT('teams/', team_id) FILTER userTeamPerm.type == "USER" RETURN userTeamPerm.role)
                    FILTER user_team_perm != null
                    RETURN {{ kb_id: kb._key, kb_doc: kb, role: user_team_perm }}
            )
            LET allKbAccess = APPEND(directKbAccess, (FOR t IN teamKbAccess FILTER LENGTH(FOR d IN directKbAccess FILTER d.kb_id == t.kb_id RETURN 1) == 0 RETURN t))
            LET kbRecords = {'(FOR access IN directKbAccess LET kb = access.kb_doc FOR belongsEdge IN @@belongs_to_kb FILTER belongsEdge._to == kb._id LET record = DOCUMENT(belongsEdge._from) FILTER record != null FILTER record.isDeleted != true FILTER record.orgId == org_id FILTER record.origin == "UPLOAD" ' + ('FILTER record.isFile != false ' if include_kb else '') + record_filter + ' RETURN { record: record, permission: { role: access.role, type: "USER" }, kb_id: kb._key, kb_name: kb.groupName })' if include_kb else '[]'}
            LET connectorRecords = {'(FOR permissionEdge IN @@permission FILTER permissionEdge._from == user_from FILTER permissionEdge.type == "USER" ' + perm_filter + ' LET record = DOCUMENT(permissionEdge._to) FILTER record != null FILTER record.isDeleted != true FILTER record.orgId == org_id FILTER record.origin == "CONNECTOR" ' + record_filter + ' RETURN { record: record, permission: { role: permissionEdge.role, type: permissionEdge.type } })' if include_connector else '[]'}
            LET allRecords = APPEND(kbRecords, connectorRecords)
            FOR item IN allRecords
                LET record = item.record
                SORT record.{sort_field} {sort_order.upper()}
                LIMIT @skip, @limit
                LET fileRecord = FIRST(FOR fileEdge IN @@is_of_type FILTER fileEdge._from == record._id LET file = DOCUMENT(fileEdge._to) FILTER file != null RETURN {{ id: file._key, name: file.name, extension: file.extension, mimeType: file.mimeType, sizeInBytes: file.sizeInBytes, isFile: file.isFile, webUrl: file.webUrl }})
                RETURN {{ id: record._key, externalRecordId: record.externalRecordId, externalRevisionId: record.externalRevisionId, recordName: record.recordName, recordType: record.recordType, origin: record.origin, connectorName: record.connectorName || "KNOWLEDGE_BASE", indexingStatus: record.indexingStatus, createdAtTimestamp: record.createdAtTimestamp, updatedAtTimestamp: record.updatedAtTimestamp, sourceCreatedAtTimestamp: record.sourceCreatedAtTimestamp, sourceLastModifiedTimestamp: record.sourceLastModifiedTimestamp, orgId: record.orgId, version: record.version, isDeleted: record.isDeleted, isLatestVersion: record.isLatestVersion != null ? record.isLatestVersion : true, webUrl: record.webUrl, fileRecord: fileRecord, permission: {{ role: item.permission.role, type: item.permission.type }}, kb: {{ id: item.kb_id || null, name: item.kb_name || null }} }}
            """
            bind = {
                "user_from": user_from,
                "org_id": org_id,
                "skip": skip,
                "limit": limit,
                "kb_permissions": final_kb_roles,
                "@permission": CollectionNames.PERMISSION.value,
                "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                **filter_bind,
            }
            if permissions:
                bind["permissions"] = permissions
            records = await self.execute_query(main_query, bind_vars=bind)
            count_query = """
            LET user_from = @user_from
            LET org_id = @org_id
            LET directKbAccess = (FOR kbEdge IN @@permission FILTER kbEdge._from == user_from FILTER kbEdge.type == "USER" FILTER kbEdge.role IN @kb_permissions LET kb = DOCUMENT(kbEdge._to) FILTER kb != null AND kb.orgId == org_id RETURN { kb_doc: kb })
            LET teamKbAccess = (FOR teamKbPerm IN @@permission FILTER teamKbPerm.type == "TEAM" FILTER STARTS_WITH(teamKbPerm._to, "recordGroups/") LET kb = DOCUMENT(teamKbPerm._to) FILTER kb != null AND kb.orgId == org_id LET team_id = SPLIT(teamKbPerm._from, '/')[1] LET user_team_perm = FIRST(FOR userTeamPerm IN @@permission FILTER userTeamPerm._from == user_from FILTER userTeamPerm._to == CONCAT('teams/', team_id) FILTER userTeamPerm.type == "USER" RETURN 1) FILTER user_team_perm != null RETURN { kb_doc: kb })
            LET allKbAccess = APPEND(directKbAccess, (FOR t IN teamKbAccess FILTER LENGTH(FOR d IN directKbAccess FILTER d.kb_doc._key == t.kb_doc._key RETURN 1) == 0 RETURN t))
            LET kbCount = LENGTH(FOR access IN allKbAccess LET kb = access.kb_doc FOR belongsEdge IN @@belongs_to_kb FILTER belongsEdge._to == kb._id LET record = DOCUMENT(belongsEdge._from) FILTER record != null FILTER record.isDeleted != true FILTER record.orgId == org_id FILTER record.origin == "UPLOAD" RETURN 1)
            LET connectorCount = LENGTH(FOR permissionEdge IN @@permission FILTER permissionEdge._from == user_from FILTER permissionEdge.type == "USER" LET record = DOCUMENT(permissionEdge._to) FILTER record != null FILTER record.isDeleted != true FILTER record.orgId == org_id FILTER record.origin == "CONNECTOR" RETURN 1)
            RETURN kbCount + connectorCount
            """
            count_results = await self.execute_query(count_query, bind_vars={**bind, "kb_permissions": final_kb_roles, "@permission": CollectionNames.PERMISSION.value, "@belongs_to_kb": CollectionNames.BELONGS_TO.value, **filter_bind})
            total = count_results[0] if count_results else 0
            available = {"recordTypes": [], "origins": [], "connectors": [], "indexingStatus": [], "permissions": []}
            return records or [], total, available
        except Exception as e:
            self.logger.error(f"❌ Failed to list all records: {str(e)}")
            return [], 0, {"recordTypes": [], "origins": [], "connectors": [], "indexingStatus": [], "permissions": []}

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
        Resolves external user_id to user key and delegates to list_all_records.
        Returns (records, total_count, available_filters).
        """
        try:
            user = await self.get_user_by_user_id(user_id)
            if not user:
                return [], 0, {"recordTypes": [], "origins": [], "connectors": [], "indexingStatus": [], "permissions": []}
            user_key = user.get("_key") or user.get("id")
            if not user_key:
                return [], 0, {"recordTypes": [], "origins": [], "connectors": [], "indexingStatus": [], "permissions": []}
            return await self.list_all_records(
                user_key,
                org_id,
                skip,
                limit,
                search,
                record_types,
                origins,
                connectors,
                indexing_status,
                permissions,
                date_from,
                date_to,
                sort_by,
                sort_order,
                source,
            )
        except Exception as e:
            self.logger.error("❌ Failed to get records: %s", str(e))
            return [], 0, {"recordTypes": [], "origins": [], "connectors": [], "indexingStatus": [], "permissions": []}

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
        try:
            user_perm = await self.get_user_kb_permission(kb_id, user_id)
            if not user_perm:
                return [], 0, {"recordTypes": [], "origins": [], "connectors": [], "indexingStatus": [], "permissions": [], "folders": []}
            filter_conditions = []
            filter_bind: Dict[str, Any] = {"kb_id": kb_id, "org_id": org_id, "user_permission": user_perm, "skip": skip, "limit": limit, "@belongs_to_kb": CollectionNames.BELONGS_TO.value, "@record_relations": CollectionNames.RECORD_RELATIONS.value, "@is_of_type": CollectionNames.IS_OF_TYPE.value}
            if search:
                filter_conditions.append("(LIKE(LOWER(record.recordName), @search) OR LIKE(LOWER(record.externalRecordId), @search))")
                filter_bind["search"] = f"%{(search or '').lower()}%"
            if record_types:
                filter_conditions.append("record.recordType IN @record_types")
                filter_bind["record_types"] = record_types
            if origins:
                filter_conditions.append("record.origin IN @origins")
                filter_bind["origins"] = origins
            if connectors:
                filter_conditions.append("record.connectorName IN @connectors")
                filter_bind["connectors"] = connectors
            if indexing_status:
                filter_conditions.append("record.indexingStatus IN @indexing_status")
                filter_bind["indexing_status"] = indexing_status
            if date_from:
                filter_conditions.append("record.createdAtTimestamp >= @date_from")
                filter_bind["date_from"] = date_from
            if date_to:
                filter_conditions.append("record.createdAtTimestamp <= @date_to")
                filter_bind["date_to"] = date_to
            record_filter = " AND " + " AND ".join(filter_conditions) if filter_conditions else ""
            folder_filter = " AND folder_record._key == @folder_id" if folder_id else ""
            if folder_id:
                filter_bind["folder_id"] = folder_id
            main_query = f"""
            LET kb = DOCUMENT("recordGroups", @kb_id)
            FILTER kb != null
            LET kbFolders = (
                FOR belongsEdge IN @@belongs_to_kb
                    FILTER belongsEdge._to == kb._id
                    LET folder_record = DOCUMENT(belongsEdge._from)
                    FILTER folder_record != null
                    LET folder_file = FIRST(FOR isEdge IN @@is_of_type FILTER isEdge._from == folder_record._id LET f = DOCUMENT(isEdge._to) FILTER f != null AND f.isFile == false RETURN f)
                    FILTER folder_file != null
                    {folder_filter}
                    RETURN {{ folder: folder_record, folder_id: folder_record._key, folder_name: folder_file.name }}
            )
            LET folder_ids = kbFolders[*].folder._id
            LET all_records_data = (
                FOR relEdge IN @@record_relations
                    FILTER relEdge._from IN folder_ids
                    FILTER relEdge.relationshipType == "PARENT_CHILD"
                    LET record = DOCUMENT(relEdge._to)
                    FILTER record != null
                    FILTER record.isDeleted != true
                    FILTER record.orgId == @org_id
                    FILTER record.isFile != false
                    {record_filter}
                    LET folder_info = FIRST(FOR f IN kbFolders FILTER f.folder._id == relEdge._from RETURN f)
                    RETURN {{ record: record, folder_id: folder_info.folder_id, folder_name: folder_info.folder_name, permission: {{ role: user_permission, type: "USER" }}, kb_id: @kb_id }}
            )
            LET record_ids = all_records_data[*].record._id
            LET all_files = (FOR fileEdge IN @@is_of_type FILTER fileEdge._from IN record_ids LET file = DOCUMENT(fileEdge._to) FILTER file != null RETURN {{ record_id: fileEdge._from, file: {{ id: file._key, name: file.name, extension: file.extension, mimeType: file.mimeType, sizeInBytes: file.sizeInBytes, isFile: file.isFile, webUrl: file.webUrl }} }})
            FOR item IN all_records_data
                LET record = item.record
                LET fileRecord = FIRST(FOR f IN all_files FILTER f.record_id == record._id RETURN f.file)
                SORT record.{sort_by or "recordName"} {(sort_order or "asc").upper()}
                LIMIT @skip, @limit
                RETURN {{ id: record._key, externalRecordId: record.externalRecordId, externalRevisionId: record.externalRevisionId, recordName: record.recordName, recordType: record.recordType, origin: record.origin, connectorName: record.connectorName || "KNOWLEDGE_BASE", indexingStatus: record.indexingStatus, createdAtTimestamp: record.createdAtTimestamp, updatedAtTimestamp: record.updatedAtTimestamp, sourceCreatedAtTimestamp: record.sourceCreatedAtTimestamp, sourceLastModifiedTimestamp: record.sourceLastModifiedTimestamp, orgId: record.orgId, version: record.version, isDeleted: record.isDeleted, isLatestVersion: record.isLatestVersion != null ? record.isLatestVersion : true, webUrl: record.webUrl, fileRecord: fileRecord, permission: {{ role: item.permission.role, type: item.permission.type }}, kb_id: item.kb_id, folder: {{ id: item.folder_id, name: item.folder_name }} }}
            """
            records = await self.execute_query(main_query, bind_vars=filter_bind)
            count_query = f"""
            LET kb = DOCUMENT("recordGroups", @kb_id)
            FILTER kb != null
            LET folder_ids = (
                FOR belongsEdge IN @@belongs_to_kb
                    FILTER belongsEdge._to == kb._id
                    LET folder_record = DOCUMENT(belongsEdge._from)
                    FILTER folder_record != null
                    LET folder_file = FIRST(FOR isEdge IN @@is_of_type FILTER isEdge._from == folder_record._id LET f = DOCUMENT(isEdge._to) FILTER f != null AND f.isFile == false RETURN 1)
                    FILTER folder_file != null
                    {folder_filter}
                    RETURN belongsEdge._from
            )
            LET record_count = (FOR relEdge IN @@record_relations FILTER relEdge._from IN folder_ids FILTER relEdge.relationshipType == "PARENT_CHILD" LET record = DOCUMENT(relEdge._to) FILTER record != null FILTER record.isDeleted != true FILTER record.orgId == @org_id {record_filter} COLLECT WITH COUNT INTO c RETURN c)
            RETURN FIRST(record_count) || 0
            """
            count_results = await self.execute_query(count_query, bind_vars=filter_bind)
            total = count_results[0] if count_results else 0
            folders_query = """
            LET kb = DOCUMENT("recordGroups", @kb_id)
            FILTER kb != null
            LET folder_list = (
                FOR belongsEdge IN @@belongs_to_kb
                    FILTER belongsEdge._to == kb._id
                    LET folder_record = DOCUMENT(belongsEdge._from)
                    FILTER folder_record != null
                    LET folder_file = FIRST(FOR isEdge IN @@is_of_type FILTER isEdge._from == folder_record._id LET f = DOCUMENT(isEdge._to) FILTER f != null AND f.isFile == false RETURN f)
                    FILTER folder_file != null
                    RETURN { id: folder_record._key, name: folder_file.name }
            )
            RETURN folder_list
            """
            folders_result = await self.execute_query(folders_query, bind_vars={"kb_id": kb_id, "@belongs_to_kb": CollectionNames.BELONGS_TO.value, "@is_of_type": CollectionNames.IS_OF_TYPE.value})
            folder_list = folders_result[0] if folders_result and isinstance(folders_result[0], list) else []
            available = {"recordTypes": [], "origins": [], "connectors": [], "indexingStatus": [], "permissions": [user_perm] if user_perm else [], "folders": folder_list}
            return records or [], total, available
        except Exception as e:
            self.logger.error(f"❌ Failed to list KB records: {str(e)}")
            return [], 0, {"recordTypes": [], "origins": [], "connectors": [], "indexingStatus": [], "permissions": [], "folders": []}

    def _validation_error(self, code: int, reason: str) -> Dict:
        """Helper to create validation error response."""
        return {"valid": False, "success": False, "code": code, "reason": reason}

    async def _validate_upload_context(
        self,
        kb_id: str,
        user_id: str,
        org_id: str,
        parent_folder_id: Optional[str] = None,
    ) -> Dict:
        """Unified validation for all upload scenarios."""
        try:
            user = await self.get_user_by_user_id(user_id=user_id)
            if not user:
                return self._validation_error(404, f"User not found: {user_id}")
            user_key = user.get("_key") or user.get("id")
            if not user_key:
                return self._validation_error(404, "User key not found")
            user_role = await self.get_user_kb_permission(kb_id, user_key)
            if user_role not in ["OWNER", "WRITER"]:
                return self._validation_error(403, f"Insufficient permissions. Role: {user_role}")
            parent_folder = None
            parent_path = "/"
            if parent_folder_id:
                parent_folder = await self.get_and_validate_folder_in_kb(kb_id, parent_folder_id)
                if not parent_folder:
                    return self._validation_error(404, f"Folder {parent_folder_id} not found in KB {kb_id}")
                parent_path = parent_folder.get("path", "/")
            return {
                "valid": True,
                "user": user,
                "user_key": user_key,
                "user_role": user_role,
                "parent_folder": parent_folder,
                "parent_path": parent_path,
                "upload_target": "folder" if parent_folder_id else "kb_root",
            }
        except Exception as e:
            return self._validation_error(500, f"Validation failed: {str(e)}")

    def _analyze_upload_structure(self, files: List[Dict], validation_result: Dict) -> Dict:
        """Analyze folder hierarchy from file paths for upload."""
        folder_hierarchy: Dict[str, Dict[str, Any]] = {}
        file_destinations: Dict[int, Dict[str, Any]] = {}
        for index, file_data in enumerate(files):
            file_path = file_data.get("filePath", "")
            if "/" in file_path:
                path_parts = file_path.split("/")
                folder_parts = path_parts[:-1]
                current_path = ""
                for i, folder_name in enumerate(folder_parts):
                    parent_path = current_path if current_path else None
                    current_path = f"{current_path}/{folder_name}" if current_path else folder_name
                    if current_path not in folder_hierarchy:
                        folder_hierarchy[current_path] = {
                            "name": folder_name,
                            "parent_path": parent_path,
                            "level": i + 1,
                        }
                file_destinations[index] = {
                    "type": "folder",
                    "folder_name": folder_parts[-1],
                    "folder_hierarchy_path": current_path,
                }
            else:
                file_destinations[index] = {
                    "type": "root",
                    "folder_name": None,
                    "folder_hierarchy_path": None,
                }
        sorted_folder_paths = sorted(
            folder_hierarchy.keys(),
            key=lambda x: folder_hierarchy[x]["level"],
        )
        parent_folder_id = None
        if validation_result.get("upload_target") == "folder" and validation_result.get("parent_folder"):
            parent_folder_id = validation_result["parent_folder"].get("_key") or validation_result["parent_folder"].get("id")
        return {
            "folder_hierarchy": folder_hierarchy,
            "sorted_folder_paths": sorted_folder_paths,
            "file_destinations": file_destinations,
            "upload_target": validation_result.get("upload_target", "kb_root"),
            "parent_folder_id": parent_folder_id,
            "summary": {
                "total_folders": len(folder_hierarchy),
                "root_files": sum(1 for d in file_destinations.values() if d["type"] == "root"),
                "folder_files": sum(1 for d in file_destinations.values() if d["type"] == "folder"),
            },
        }

    async def _ensure_folders_exist(
        self,
        kb_id: str,
        org_id: str,
        folder_analysis: Dict,
        validation_result: Dict,
        txn_id: str,
    ) -> Dict[str, str]:
        """Ensure all needed folders exist; return hierarchy_path -> folder_id map."""
        folder_map: Dict[str, str] = {}
        upload_parent_folder_id = None
        if validation_result.get("upload_target") == "folder" and validation_result.get("parent_folder"):
            upload_parent_folder_id = validation_result["parent_folder"].get("_key") or validation_result["parent_folder"].get("id")
        for hierarchy_path in folder_analysis["sorted_folder_paths"]:
            folder_info = folder_analysis["folder_hierarchy"][hierarchy_path]
            folder_name = folder_info["name"]
            parent_hierarchy_path = folder_info["parent_path"]
            parent_folder_id = None
            if parent_hierarchy_path:
                parent_folder_id = folder_map.get(parent_hierarchy_path)
                if parent_folder_id is None:
                    raise ValueError(f"Parent folder creation failed for path: {parent_hierarchy_path}")
            elif upload_parent_folder_id:
                parent_folder_id = upload_parent_folder_id
            existing_folder = await self.find_folder_by_name_in_parent(
                kb_id=kb_id,
                folder_name=folder_name,
                parent_folder_id=parent_folder_id,
                transaction=txn_id,
            )
            if existing_folder:
                folder_map[hierarchy_path] = existing_folder.get("_key") or existing_folder.get("id", "")
            else:
                folder = await self.create_folder(
                    kb_id=kb_id,
                    folder_name=folder_name,
                    org_id=org_id,
                    parent_folder_id=parent_folder_id,
                    transaction=txn_id,
                )
                folder_id = folder and (folder.get("id") or folder.get("folderId"))
                if folder_id:
                    folder_map[hierarchy_path] = folder_id
                else:
                    raise ValueError(f"Failed to create folder: {folder_name}")
        return folder_map

    def _populate_file_destinations(self, folder_analysis: Dict, folder_map: Dict[str, str]) -> None:
        """Update file destinations with resolved folder IDs."""
        for destination in folder_analysis["file_destinations"].values():
            if destination["type"] == "folder":
                hierarchy_path = destination.get("folder_hierarchy_path")
                if hierarchy_path and hierarchy_path in folder_map:
                    destination["folder_id"] = folder_map[hierarchy_path]

    def _generate_upload_message(self, result: Dict, upload_type: str) -> str:
        """Generate success message for upload."""
        total_created = result.get("total_created", 0)
        folders_created = result.get("folders_created", 0)
        failed_count = len(result.get("failed_files", []))
        message = f"Successfully uploaded {total_created} file{'s' if total_created != 1 else ''} to {upload_type}"
        if folders_created > 0:
            message += f" with {folders_created} new subfolder{'s' if folders_created != 1 else ''} created"
        if failed_count > 0:
            message += f". {failed_count} file{'s' if failed_count != 1 else ''} failed to upload"
        return message + "."

    async def _create_files_batch(
        self,
        kb_id: str,
        files: List[Dict],
        parent_folder_id: Optional[str],
        transaction: Optional[str],
        timestamp: int,
    ) -> List[Dict]:
        """Create a batch of file records and edges; skip name conflicts."""
        if not files:
            return []
        valid_files: List[Dict] = []
        for file_data in files:
            file_record = file_data.get("fileRecord") or {}
            record = file_data.get("record") or {}
            file_name = self._normalize_name(file_record.get("name") or record.get("recordName")) or ""
            mime_type = file_record.get("mimeType")
            conflict_result = await self._check_name_conflict_in_parent(
                kb_id=kb_id,
                parent_folder_id=parent_folder_id,
                item_name=file_name,
                mime_type=mime_type,
                transaction=transaction,
            )
            if conflict_result.get("has_conflict"):
                conflicts = conflict_result.get("conflicts", [])
                conflict_names = [c.get("name", "") for c in conflicts]
                self.logger.warning(
                    "⚠️ Skipping file due to name conflict: '%s' conflicts with %s",
                    file_name,
                    conflict_names,
                )
                continue
            file_record["name"] = file_name
            if record and "recordName" not in record:
                record["recordName"] = file_name
            valid_files.append(file_data)
        if not valid_files:
            return []
        records = [f["record"] for f in valid_files]
        file_records = [f["fileRecord"] for f in valid_files]
        await self.batch_upsert_nodes(records, CollectionNames.RECORDS.value, transaction=transaction)
        await self.batch_upsert_nodes(file_records, CollectionNames.FILES.value, transaction=transaction)
        edges_to_create: List[Dict] = []
        for file_data in valid_files:
            record_id = (file_data.get("record") or {}).get("_key")
            file_id = (file_data.get("fileRecord") or {}).get("_key")
            if not record_id or not file_id:
                continue
            if parent_folder_id:
                edges_to_create.append({
                    "from_id": parent_folder_id,
                    "from_collection": CollectionNames.RECORDS.value,
                    "to_id": record_id,
                    "to_collection": CollectionNames.RECORDS.value,
                    "relationshipType": "PARENT_CHILD",
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                })
            edges_to_create.append({
                "from_id": record_id,
                "from_collection": CollectionNames.RECORDS.value,
                "to_id": file_id,
                "to_collection": CollectionNames.FILES.value,
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            })
            edges_to_create.append({
                "from_id": record_id,
                "from_collection": CollectionNames.RECORDS.value,
                "to_id": kb_id,
                "to_collection": CollectionNames.RECORD_GROUPS.value,
                "entityType": Connectors.KNOWLEDGE_BASE.value,
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            })
        parent_child = [e for e in edges_to_create if e.get("relationshipType") == "PARENT_CHILD"]
        is_of_type = [e for e in edges_to_create if e.get("to_collection") == CollectionNames.FILES.value]
        belongs_to = [e for e in edges_to_create if e.get("to_collection") == CollectionNames.RECORD_GROUPS.value]
        if parent_child:
            await self.batch_create_edges(parent_child, CollectionNames.RECORD_RELATIONS.value, transaction=transaction)
        if is_of_type:
            await self.batch_create_edges(is_of_type, CollectionNames.IS_OF_TYPE.value, transaction=transaction)
        if belongs_to:
            await self.batch_create_edges(belongs_to, CollectionNames.BELONGS_TO.value, transaction=transaction)
        return valid_files

    async def _create_files_in_kb_root(
        self,
        kb_id: str,
        files: List[Dict],
        transaction: Optional[str],
        timestamp: int,
    ) -> List[Dict]:
        """Create files directly in KB root."""
        return await self._create_files_batch(
            kb_id=kb_id,
            files=files,
            parent_folder_id=None,
            transaction=transaction,
            timestamp=timestamp,
        )

    async def _create_files_in_folder(
        self,
        kb_id: str,
        folder_id: str,
        files: List[Dict],
        transaction: Optional[str],
        timestamp: int,
    ) -> List[Dict]:
        """Create files in a specific folder."""
        return await self._create_files_batch(
            kb_id=kb_id,
            files=files,
            parent_folder_id=folder_id,
            transaction=transaction,
            timestamp=timestamp,
        )

    async def _create_records(
        self,
        kb_id: str,
        files: List[Dict],
        folder_analysis: Dict,
        transaction: Optional[str],
        timestamp: int,
    ) -> Dict:
        """Create all file records and relationships from upload."""
        total_created = 0
        failed_files: List[str] = []
        created_files_data: List[Dict] = []
        root_files: List[Tuple[Dict, Optional[str]]] = []
        folder_files: Dict[str, List[Dict]] = {}
        parent_folder_id = folder_analysis.get("parent_folder_id")
        for index, file_data in enumerate(files):
            destination = folder_analysis["file_destinations"].get(index, {})
            if destination.get("type") == "root":
                root_files.append((file_data, parent_folder_id))
            else:
                folder_id = destination.get("folder_id")
                if folder_id:
                    folder_files.setdefault(folder_id, []).append(file_data)
                else:
                    failed_files.append(file_data.get("filePath", ""))
        kb_root_files = [f for f, fid in root_files if fid is None]
        parent_folder_files_map: Dict[str, List[Dict]] = {}
        for file_data, fid in root_files:
            if fid is not None:
                parent_folder_files_map.setdefault(fid, []).append(file_data)
        if kb_root_files:
            try:
                successful = await self._create_files_in_kb_root(
                    kb_id=kb_id,
                    files=kb_root_files,
                    transaction=transaction,
                    timestamp=timestamp,
                )
                created_files_data.extend(successful)
                total_created += len(successful)
            except Exception as e:
                self.logger.error("❌ Failed to create root files: %s", str(e))
                failed_files.extend(f[0].get("filePath", "") for f in root_files if f[1] is None)
        for fid, file_list in parent_folder_files_map.items():
            try:
                successful = await self._create_files_in_folder(
                    kb_id=kb_id,
                    folder_id=fid,
                    files=file_list,
                    transaction=transaction,
                    timestamp=timestamp,
                )
                created_files_data.extend(successful)
                total_created += len(successful)
            except Exception as e:
                self.logger.error("❌ Failed to create parent folder files: %s", str(e))
                failed_files.extend(f.get("filePath", "") for f in file_list)
        for folder_id, file_list in folder_files.items():
            try:
                successful = await self._create_files_in_folder(
                    kb_id=kb_id,
                    folder_id=folder_id,
                    files=file_list,
                    transaction=transaction,
                    timestamp=timestamp,
                )
                created_files_data.extend(successful)
                total_created += len(successful)
            except Exception as e:
                self.logger.error("❌ Failed to create subfolder files: %s", str(e))
                failed_files.extend(f.get("filePath", "") for f in file_list)
        return {
            "total_created": total_created,
            "failed_files": failed_files,
            "created_files_data": created_files_data,
        }

    async def _execute_upload_transaction(
        self,
        kb_id: str,
        user_id: str,
        org_id: str,
        files: List[Dict],
        folder_analysis: Dict,
        validation_result: Dict,
    ) -> Dict:
        """Run upload in a single transaction: folders, then records."""
        try:
            txn_id = await self.begin_transaction(
                read=[],
                write=[
                    CollectionNames.FILES.value,
                    CollectionNames.RECORDS.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.BELONGS_TO.value,
                ],
            )
            try:
                timestamp = get_epoch_timestamp_in_ms()
                folder_map = await self._ensure_folders_exist(
                    kb_id=kb_id,
                    org_id=org_id,
                    folder_analysis=folder_analysis,
                    validation_result=validation_result,
                    txn_id=txn_id,
                )
                self._populate_file_destinations(folder_analysis, folder_map)
                creation_result = await self._create_records(
                    kb_id=kb_id,
                    files=files,
                    folder_analysis=folder_analysis,
                    transaction=txn_id,
                    timestamp=timestamp,
                )
                if creation_result["total_created"] > 0 or len(folder_map) > 0:
                    await self.commit_transaction(txn_id)
                    return {
                        "success": True,
                        "total_created": creation_result["total_created"],
                        "folders_created": len(folder_map),
                        "created_folders": [{"id": fid} for fid in folder_map.values()],
                        "failed_files": creation_result["failed_files"],
                        "created_files_data": creation_result["created_files_data"],
                    }
                await self.rollback_transaction(txn_id)
                return {
                    "success": True,
                    "total_created": 0,
                    "folders_created": 0,
                    "created_folders": [],
                    "failed_files": creation_result["failed_files"],
                    "created_files_data": [],
                }
            except Exception as e:
                try:
                    await self.rollback_transaction(txn_id)
                except Exception as abort_err:
                    self.logger.error("❌ Failed to rollback transaction: %s", str(abort_err))
                self.logger.error("❌ Upload transaction failed: %s", str(e))
                return {"success": False, "reason": f"Transaction failed: {str(e)}", "code": 500}
        except Exception as e:
            return {"success": False, "reason": f"Transaction failed: {str(e)}", "code": 500}

    async def upload_records(
        self,
        kb_id: str,
        user_id: str,
        org_id: str,
        files: List[Dict],
        parent_folder_id: Optional[str] = None,
    ) -> Dict:
        """Upload records to KB root or a folder. Full flow: validate, analyze structure, run transaction."""
        try:
            upload_type = "folder" if parent_folder_id else "KB root"
            self.logger.info("🚀 Starting unified upload to %s in KB %s", upload_type, kb_id)
            self.logger.info("📊 Processing %s files", len(files))
            validation_result = await self._validate_upload_context(
                kb_id=kb_id,
                user_id=user_id,
                org_id=org_id,
                parent_folder_id=parent_folder_id,
            )
            if not validation_result.get("valid"):
                return validation_result
            folder_analysis = self._analyze_upload_structure(files, validation_result)
            self.logger.info("📁 Structure analysis: %s", folder_analysis.get("summary", {}))
            result = await self._execute_upload_transaction(
                kb_id=kb_id,
                user_id=user_id,
                org_id=org_id,
                files=files,
                folder_analysis=folder_analysis,
                validation_result=validation_result,
            )
            if result.get("success"):
                # Publish events AFTER successful commit
                try:
                    await self._publish_upload_events(kb_id, {
                        "created_files_data": result.get("created_files_data", []),
                        "total_created": result["total_created"]
                    })
                    self.logger.info(f"✅ Published events for {result['total_created']} records")
                except Exception as event_error:
                    self.logger.error(f"❌ Event publishing failed (records still created): {str(event_error)}")
                    # Don't fail the main operation - records were successfully created

                return {
                    "success": True,
                    "message": self._generate_upload_message(result, upload_type),
                    "totalCreated": result["total_created"],
                    "foldersCreated": result["folders_created"],
                    "createdFolders": result["created_folders"],
                    "failedFiles": result["failed_files"],
                    "kbId": kb_id,
                    "parentFolderId": parent_folder_id,
                }
            return result
        except Exception as e:
            self.logger.error("❌ Upload records failed: %s", str(e))
            return {"success": False, "reason": str(e), "code": 500}

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
            # Get mail and file records for event publishing before deletion
            mail_record = await self.get_document(record_id, CollectionNames.MAILS.value)
            file_record = await self.get_document(record_id, CollectionNames.FILES.value) if record.get("recordType") == "FILE" else None

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
            if mail_record:
                await self._delete_mail_record(record_id, transaction)

            # Delete file record if it's an attachment
            if file_record:
                await self._delete_file_record(record_id, transaction)

            # Delete main record
            await self._delete_main_record(record_id, transaction)

            self.logger.info(f"✅ Deleted Gmail record {record_id} with {len(attachment_ids)} attachments")

            # Publish Gmail deletion event
            try:
                await self._publish_gmail_deletion_event(record, mail_record, file_record)
            except Exception as event_error:
                self.logger.error(f"❌ Failed to publish Gmail deletion event: {str(event_error)}")

            return {
                "success": True,
                "record_id": record_id,
                "connector": Connectors.GOOGLE_MAIL.value,
                "user_role": user_role
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
            # Get file record for event publishing before deletion
            file_record = await self.get_document(record_id, CollectionNames.FILES.value)

            # Delete Drive-specific edges
            await self._delete_drive_specific_edges(record_id, transaction)

            # Delete 'anyone' permissions specific to Drive
            await self._delete_drive_anyone_permissions(record_id, transaction)

            # Delete file record
            await self._delete_file_record(record_id, transaction)

            # Delete main record
            await self._delete_main_record(record_id, transaction)

            self.logger.info(f"✅ Deleted Drive record {record_id}")

            # Publish Drive deletion event
            try:
                await self._publish_drive_deletion_event(record, file_record)
            except Exception as event_error:
                self.logger.error(f"❌ Failed to publish Drive deletion event: {str(event_error)}")

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
            # Get file record for event publishing before deletion
            file_record = await self.get_document(record_id, CollectionNames.FILES.value)

            # Delete KB-specific edges
            await self._delete_kb_specific_edges(record_id, transaction)

            # Delete file record
            await self._delete_file_record(record_id, transaction)

            # Delete main record
            await self._delete_main_record(record_id, transaction)

            self.logger.info(f"✅ Deleted KB record {record_id}")

            # Publish KB deletion event
            try:
                await self._publish_kb_deletion_event(record, file_record)
            except Exception as event_error:
                self.logger.error(f"❌ Failed to publish KB deletion event: {str(event_error)}")

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
        query = """
        LET user_doc = DOCUMENT(CONCAT("users/", @user_key))
        LET user_id = user_doc != null ? user_doc.userId : null

        LET kbs = @include_kbs ? (
            FOR kb IN recordGroups
                FILTER kb.orgId == @org_id
                FILTER kb.connectorName == "KB"

                LET has_direct_user_perm = LENGTH(
                    FOR perm IN permission
                        FILTER perm._from == CONCAT("users/", @user_key)
                        FILTER perm._to == kb._id
                        FILTER perm.type == "USER"
                        RETURN 1
                ) > 0

                LET has_team_perm = LENGTH(
                    FOR user_team_perm IN permission
                        FILTER user_team_perm._from == CONCAT("users/", @user_key)
                        FILTER user_team_perm.type == "USER"
                        FILTER STARTS_WITH(user_team_perm._to, "teams/")
                        FOR team_kb_perm IN permission
                            FILTER team_kb_perm._from == user_team_perm._to
                            FILTER team_kb_perm._to == kb._id
                            FILTER team_kb_perm.type == "TEAM"
                            RETURN 1
                ) > 0

                LET has_permission = has_direct_user_perm OR has_team_perm
                FILTER has_permission
                // Check for children via belongsTo edges
                LET has_record_children = LENGTH(
                    // KB: Use belongsTo edges (record -> belongsTo -> recordGroup)
                    // Only direct children: externalParentId must be null
                    FOR edge IN belongsTo
                        FILTER edge._to == kb._id AND STARTS_WITH(edge._from, "records/")
                        LET record = DOCUMENT(edge._from)
                        FILTER record != null AND record.isDeleted != true
                        FILTER record.externalParentId == null
                        RETURN 1
                ) > 0
                LET has_nested_rgs = LENGTH(
                    // KB: Use belongsTo edges (child_rg -> belongsTo -> kb)
                    FOR edge IN belongsTo
                        FILTER edge._to == kb._id AND STARTS_WITH(edge._from, "recordGroups/")
                        LET child_rg = DOCUMENT(edge._from)
                        FILTER child_rg != null AND child_rg.connectorName == "KB" AND child_rg.isDeleted != true
                        RETURN 1
                ) > 0
                LET has_children = has_record_children OR has_nested_rgs

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
                    source: "KB",
                    connector: "KB",
                    createdAt: kb.createdAtTimestamp,
                    updatedAt: kb.updatedAtTimestamp,
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
                LET has_children = LENGTH(
                    FOR rg IN recordGroups
                        FILTER rg.connectorId == app._key
                        RETURN 1
                ) > 0

                LET sharingStatus = app.scope != null ? app.scope : "personal"

                RETURN {
                    id: app._key,
                    name: app.name,
                    nodeType: "app",
                    parentId: null,
                    source: "CONNECTOR",
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

        ArangoDB implementation: Converts structured parameters to AQL.

        Args:
            parent_id: The ID of the parent node.
            parent_type: The type of parent: 'app', 'kb', 'recordGroup', 'folder', 'record'.
            org_id: The organization ID.
            user_key: The user's key for permission filtering.
            skip: Number of items to skip for pagination.
            limit: Maximum number of items to return.
            sort_field: Field to sort by.
            sort_dir: Sort direction ('ASC' or 'DESC').
            search_query: Optional search query to filter by name.
            node_types: Optional list of node types to filter by.
            record_types: Optional list of record types to filter by.
            origins: Optional list of origins to filter by (KB/CONNECTOR).
            connector_ids: Optional list of connector IDs to filter by.
            kb_ids: Optional list of KB IDs to filter by.
            indexing_status: Optional list of indexing statuses to filter by.
            created_at: Optional date range filter for creation date.
            updated_at: Optional date range filter for update date.
            size: Optional size range filter.
            only_containers: If True, only return nodes that can have children.
            transaction: Optional transaction ID.
        """
        # Generate the sub-query based on parent type
        if parent_type == "app":
            sub_query, parent_bind_vars = self._get_app_children_subquery(parent_id, org_id, user_key)
        elif parent_type in ("kb", "recordGroup"):
            sub_query, parent_bind_vars = self._get_record_group_children_subquery(parent_id, org_id, parent_type, user_key)
        elif parent_type in ("folder", "record"):
            sub_query, parent_bind_vars = self._get_record_children_subquery(parent_id, org_id, user_key)
        else:
            return {"nodes": [], "total": 0}

        # Build AQL filter conditions from structured parameters
        filter_conditions = []
        bind_vars = {
            "org_id": org_id,
            "user_key": user_key,
            "skip": skip,
            "limit": limit,
            "sort_field": sort_field,
            "sort_dir": sort_dir,
            "only_containers": only_containers,
            **parent_bind_vars,
        }

        # Search query filter
        if search_query:
            bind_vars["search_query"] = search_query.lower()
            filter_conditions.append("LOWER(node.name) LIKE CONCAT('%', @search_query, '%')")

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
            if type_conditions:
                filter_conditions.append(f"({' OR '.join(type_conditions)})")

        # Record type filter
        if record_types:
            bind_vars["record_types"] = record_types
            filter_conditions.append("(node.recordType != null AND node.recordType IN @record_types)")

        # Indexing status filter
        if indexing_status:
            bind_vars["indexing_status"] = indexing_status
            filter_conditions.append("(node.indexingStatus == null OR node.indexingStatus IN @indexing_status)")

        # Created date filter
        if created_at:
            if created_at.get("gte"):
                bind_vars["created_at_gte"] = created_at["gte"]
                filter_conditions.append("node.createdAt >= @created_at_gte")
            if created_at.get("lte"):
                bind_vars["created_at_lte"] = created_at["lte"]
                filter_conditions.append("node.createdAt <= @created_at_lte")

        # Updated date filter
        if updated_at:
            if updated_at.get("gte"):
                bind_vars["updated_at_gte"] = updated_at["gte"]
                filter_conditions.append("node.updatedAt >= @updated_at_gte")
            if updated_at.get("lte"):
                bind_vars["updated_at_lte"] = updated_at["lte"]
                filter_conditions.append("node.updatedAt <= @updated_at_lte")

        # Size filter
        if size:
            if size.get("gte"):
                bind_vars["size_gte"] = size["gte"]
                filter_conditions.append("(node.sizeInBytes == null OR node.sizeInBytes >= @size_gte)")
            if size.get("lte"):
                bind_vars["size_lte"] = size["lte"]
                filter_conditions.append("(node.sizeInBytes == null OR node.sizeInBytes <= @size_lte)")

        # Origins filter
        if origins:
            bind_vars["origins"] = origins
            filter_conditions.append("node.source IN @origins")

        # Connector/KB IDs filter
        if connector_ids and kb_ids:
            bind_vars["connector_ids"] = connector_ids
            bind_vars["kb_ids"] = kb_ids
            filter_conditions.append("(node.appId IN @connector_ids OR node.kbId IN @kb_ids)")
        elif connector_ids:
            bind_vars["connector_ids"] = connector_ids
            filter_conditions.append("node.appId IN @connector_ids")
        elif kb_ids:
            bind_vars["kb_ids"] = kb_ids
            filter_conditions.append("node.kbId IN @kb_ids")

        # Build final filter clause
        filter_clause = " AND ".join(filter_conditions) if filter_conditions else "true"

        # Common template for filtering, sorting, pagination
        query = f"""
        {sub_query}

        LET filtered_children = (
            FOR node IN raw_children
                FILTER {filter_clause}
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
        return result[0] if result else {"nodes": [], "total": 0}

    def _get_app_children_subquery(self, app_id: str, org_id: str, user_key: str) -> Tuple[str, Dict[str, Any]]:
        """Generate AQL sub-query to fetch RecordGroups for an App.

        For connector apps, we return "root" record groups that the user has permission to access.
        A "root" is defined as a record group where either:
        1. It has no parent (parentExternalGroupId is null)
        2. OR its parent exists but the user does NOT have permission to access the parent (so this RG becomes a root for the user)
        3. OR its parent does not exist in our DB (orphaned/top of sync)
        """
        sub_query = """
        LET app = DOCUMENT("apps", @app_id)
        FILTER app != null

        LET user_from = CONCAT("users/", @user_key)

        // org_id is passed from parent - use it to verify app belongs to org
        LET _org_check = @org_id

        // Check if this is a KB app
        LET is_kb_app = app.type == "KB"

        // Permission Path 1: Direct user -> recordGroup permission
        LET direct_rg_ids = is_kb_app ? (
            // KB: Find via belongsTo edge
            FOR edge IN belongsTo
                FILTER edge._to == app._id AND STARTS_WITH(edge._from, "recordGroups/")
                LET rg = DOCUMENT(edge._from)
                FILTER rg != null AND rg.connectorName == "KB"
                FOR perm IN permission
                    FILTER perm._from == user_from AND perm._to == rg._id AND perm.type == "USER"
                    RETURN rg._key
        ) : (
            // Connector: Existing logic
            FOR perm IN permission
                FILTER perm._from == user_from
                FILTER STARTS_WITH(perm._to, "recordGroups/")
                LET rg = DOCUMENT(perm._to)
                FILTER rg != null AND rg.connectorId == @app_id
                RETURN rg._key
        )

        // Permission Path 2: User -> Group -> recordGroup permission
        LET group_rg_ids = is_kb_app ? (
            // KB: Find via belongsTo edge
            FOR group, userEdge IN 1..1 ANY user_from permission
                FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                FOR edge IN belongsTo
                    FILTER edge._to == app._id AND STARTS_WITH(edge._from, "recordGroups/")
                    LET rg = DOCUMENT(edge._from)
                    FILTER rg != null AND rg.connectorName == "KB"
                    FOR perm IN permission
                        FILTER perm._from == group._id AND perm._to == rg._id AND (perm.type == "GROUP" OR perm.type == "ROLE")
                        RETURN rg._key
        ) : (
            // Connector: Existing logic
            FOR group, userEdge IN 1..1 ANY user_from permission
                FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                FOR rg, groupEdge IN 1..1 ANY group._id permission
                    FILTER groupEdge.type == "GROUP" OR groupEdge.type == "ROLE"
                    FILTER IS_SAME_COLLECTION("recordGroups", rg)
                    FILTER rg.connectorId == @app_id
                    RETURN rg._key
        )

        // Permission Path 3: User -> Org -> recordGroup permission
        LET org_rg_ids = is_kb_app ? (
            // KB: Find via belongsTo edge
            FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                FILTER belongsEdge.entityType == "ORGANIZATION"
                FOR edge IN belongsTo
                    FILTER edge._to == app._id AND STARTS_WITH(edge._from, "recordGroups/")
                    LET rg = DOCUMENT(edge._from)
                    FILTER rg != null AND rg.connectorName == "KB"
                    FOR perm IN permission
                        FILTER perm._from == org._id AND perm._to == rg._id AND perm.type == "ORG"
                        RETURN rg._key
        ) : (
            // Connector: Existing logic
            FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                FILTER belongsEdge.entityType == "ORGANIZATION"
                FOR rg, orgPerm IN 1..1 ANY org._id permission
                    FILTER orgPerm.type == "ORG"
                    FILTER IS_SAME_COLLECTION("recordGroups", rg)
                    FILTER rg.connectorId == @app_id
                    RETURN rg._key
        )

        LET accessible_rg_ids = UNION_DISTINCT(direct_rg_ids, group_rg_ids, org_rg_ids)

        // Identify "root" record groups relative to user's access
        // If I have access to C, but not B (parent of C), then C is a root for me.
        LET root_rgs = (
            FOR rg_id IN accessible_rg_ids
                LET rg = DOCUMENT(CONCAT("recordGroups/", rg_id))
                FILTER rg != null

                // Check if parent exists AND matches a recordGroup I have access to
                LET parent_is_accessible = is_kb_app ? (
                    // KB: Check via belongsTo edge
                    LENGTH(
                        FOR edge IN belongsTo
                            FILTER edge._from == rg._id AND STARTS_WITH(edge._to, "recordGroups/")
                            LET p = DOCUMENT(edge._to)
                            FILTER p != null AND p.connectorName == "KB"
                            FILTER p._key IN accessible_rg_ids
                            LIMIT 1
                            RETURN 1
                    ) > 0
                ) : (
                    // Connector: Existing logic
                    rg.parentExternalGroupId != null ? LENGTH(
                        FOR p in recordGroups
                            FILTER p.connectorId == @app_id
                            FILTER p.externalGroupId == rg.parentExternalGroupId
                            FILTER p._key IN accessible_rg_ids
                            LIMIT 1
                            RETURN 1
                    ) > 0 : false
                )

                // Keep RG if it has no accessible parent (it is a root for this user)
                FILTER NOT parent_is_accessible
                RETURN rg
        )

        LET raw_children = (
            FOR rg IN root_rgs
                // Check for nested record groups using belongsTo edges or parentExternalGroupId field
                LET has_child_rgs = is_kb_app ? (
                    // KB: Use belongsTo edges
                    LENGTH(
                        FOR edge IN belongsTo
                            FILTER edge._to == rg._id AND STARTS_WITH(edge._from, "recordGroups/")
                            LET child_rg = DOCUMENT(edge._from)
                            FILTER child_rg != null AND child_rg.connectorName == "KB" AND child_rg.isDeleted != true
                            // Check if user has permission to this nested record group
                            LET child_has_direct = LENGTH(FOR perm IN permission FILTER perm._from == user_from AND perm._to == child_rg._id AND perm.type == "USER" RETURN 1) > 0
                            LET child_has_group = LENGTH(
                                FOR group, userEdge IN 1..1 ANY user_from permission
                                    FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                                    FOR perm IN permission FILTER perm._from == group._id AND perm._to == child_rg._id FILTER perm.type == "GROUP" OR perm.type == "ROLE" RETURN 1
                            ) > 0
                            LET child_has_org = LENGTH(
                                FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                                    FILTER belongsEdge.entityType == "ORGANIZATION"
                                    FOR perm IN permission FILTER perm._from == org._id AND perm._to == child_rg._id FILTER perm.type == "ORG" RETURN 1
                            ) > 0
                            FILTER child_has_direct OR child_has_group OR child_has_org
                            RETURN 1
                    ) > 0
                ) : (
                    // Connector: Existing logic with parentExternalGroupId
                    LENGTH(
                        FOR child_rg IN recordGroups
                            FILTER child_rg.connectorId == @app_id
                            FILTER (
                                // Option 1: Connected via belongsTo edge (child_rg -> belongsTo -> rg)
                                LENGTH(FOR edge IN belongsTo FILTER edge._from == child_rg._id AND edge._to == rg._id RETURN 1) > 0
                                OR
                                // Option 2: Connected via parentExternalGroupId field
                                (child_rg.parentExternalGroupId != null AND child_rg.parentExternalGroupId == rg.externalGroupId)
                            )
                            // Check if user has permission to this nested record group
                            LET child_has_direct = LENGTH(FOR perm IN permission FILTER perm._from == user_from AND perm._to == child_rg._id AND perm.type == "USER" RETURN 1) > 0
                            LET child_has_group = LENGTH(
                                FOR group, userEdge IN 1..1 ANY user_from permission
                                    FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                                    FOR perm IN permission FILTER perm._from == group._id AND perm._to == child_rg._id FILTER perm.type == "GROUP" OR perm.type == "ROLE" RETURN 1
                            ) > 0
                            LET child_has_org = LENGTH(
                                FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                                    FILTER belongsEdge.entityType == "ORGANIZATION"
                                    FOR perm IN permission FILTER perm._from == org._id AND perm._to == child_rg._id FILTER perm.type == "ORG" RETURN 1
                            ) > 0
                            FILTER child_has_direct OR child_has_group OR child_has_org
                            RETURN 1
                    ) > 0
                )
                // Check for records using belongsTo edges (record -> belongsTo -> recordGroup), then check permissions
                // Also check recordGroupId field and inheritPermissions edges as fallbacks
                // IMPORTANT: Only count records that are ACTUALLY connected to this record group, not just same connectorId
                // Note: For hasChildren, we check ALL records (including nested ones), not just direct children
                LET all_potential_records = is_kb_app ? (
                    // KB: Use belongsTo edges only
                    // Only direct children: externalParentId must be null
                    FOR edge IN belongsTo
                        FILTER edge._from LIKE "records/%" AND edge._to == rg._id
                        LET record = DOCUMENT(edge._from)
                        FILTER record != null AND record.isDeleted != true
                        FILTER record.externalParentId == null
                        RETURN record._id
                ) : (
                    // Connector: Existing UNION_DISTINCT logic
                    UNION_DISTINCT(
                        // Method 1: belongsTo edges (record -> belongsTo -> recordGroup)
                        FOR edge IN belongsTo
                            FILTER edge._from LIKE "records/%" AND edge._to == rg._id
                            LET record = DOCUMENT(edge._from)
                            FILTER record != null AND record.isDeleted != true
                            RETURN record._id
                        ,
                        // Method 2: recordGroupId field matches this record group
                        FOR record IN records
                            FILTER record.recordGroupId == rg._key
                            FILTER record != null AND record.isDeleted != true
                            RETURN record._id
                        ,
                        // Method 3: inheritPermissions edges (record -> inheritPermissions -> recordGroup)
                        FOR edge IN inheritPermissions
                            FILTER edge._from LIKE "records/%" AND edge._to == rg._id
                            LET record = DOCUMENT(edge._from)
                            FILTER record != null AND record.isDeleted != true
                            RETURN record._id
                    )
                )
                LET has_records = LENGTH(
                    FOR record_id IN all_potential_records
                        LET record = DOCUMENT(record_id)
                        FILTER record != null
                        // Check if user has permission to this record
                        // Path 1: Direct user -> record permission
                        LET rec_has_direct = LENGTH(FOR perm IN permission FILTER perm._from == user_from AND perm._to == record._id AND perm.type == "USER" RETURN 1) > 0
                        // Path 2: User -> group -> record permission
                        LET rec_has_group = LENGTH(
                            FOR group, userEdge IN 1..1 ANY user_from permission
                                FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                                FOR perm IN permission FILTER perm._from == group._id AND perm._to == record._id FILTER perm.type == "GROUP" OR perm.type == "ROLE" RETURN 1
                        ) > 0
                        // Path 3: User -> org -> record permission
                        LET rec_has_org = LENGTH(
                            FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                                FILTER belongsEdge.entityType == "ORGANIZATION"
                                FOR perm IN permission FILTER perm._from == org._id AND perm._to == record._id FILTER perm.type == "ORG" RETURN 1
                        ) > 0
                        // Path 4: User -> recordGroup (via inheritPermissions) -> record
                        LET rec_has_inherit_rg = LENGTH(
                            FOR recordGroup, userToRgEdge IN 1..1 ANY user_from permission
                                FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                                FOR inheritEdge IN inheritPermissions
                                    FILTER inheritEdge._to == recordGroup._id AND inheritEdge._from == record._id
                                    RETURN 1
                        ) > 0
                        // Path 5: User -> group -> recordGroup (via inheritPermissions) -> record
                        LET rec_has_group_rg = LENGTH(
                            FOR group, userEdge IN 1..1 ANY user_from permission
                                FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                                FOR recordGroup, groupToRgEdge IN 1..1 ANY group._id permission
                                    FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                                    FOR inheritEdge IN inheritPermissions
                                        FILTER inheritEdge._to == recordGroup._id AND inheritEdge._from == record._id
                                        RETURN 1
                        ) > 0
                        // Path 6: User -> org -> recordGroup (via inheritPermissions) -> record
                        LET rec_has_org_rg = LENGTH(
                            FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                                FILTER belongsEdge.entityType == "ORGANIZATION"
                                FOR recordGroup, orgPerm IN 1..1 ANY org._id permission
                                    FILTER orgPerm.type == "ORG"
                                    FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                                    FOR inheritEdge IN inheritPermissions
                                        FILTER inheritEdge._to == recordGroup._id AND inheritEdge._from == record._id
                                        RETURN 1
                        ) > 0
                        FILTER rec_has_direct OR rec_has_group OR rec_has_org OR rec_has_inherit_rg OR rec_has_group_rg OR rec_has_org_rg
                        RETURN 1
                ) > 0
                RETURN {
                    id: rg._key, name: rg.groupName, nodeType: "recordGroup",
                    parentId: CONCAT("apps/", @app_id),
                    source: "CONNECTOR", connector: rg.connectorName,
                    recordType: null, recordGroupType: rg.groupType, indexingStatus: null,
                    createdAt: rg.createdAtTimestamp, updatedAt: rg.updatedAtTimestamp,
                    sizeInBytes: null, mimeType: null, extension: null,
                    webUrl: rg.webUrl, hasChildren: has_child_rgs OR has_records
                }
        )
        """
        return sub_query, {"app_id": app_id, "user_key": user_key}

    def _get_record_group_children_subquery(self, rg_id: str, org_id: str, parent_type: str, user_key: str) -> Tuple[str, Dict[str, Any]]:
        """Generate AQL sub-query to fetch children of a KB or RecordGroup with permission filtering."""
        rg_doc_id = f"recordGroups/{rg_id}"
        source = "KB" if parent_type == "kb" else "CONNECTOR"

        sub_query = f"""
        LET rg = DOCUMENT(@rg_doc_id)
        FILTER rg != null
        // Note: Connector recordGroups may have empty orgId, so we don't filter on it

        // Determine if this is a KB or App record group
        LET is_kb_rg = rg.connectorName == "KB"

        // Get all child record groups
        // For KB: use belongsTo edges
        // For Connector: use parentExternalGroupId to find children
        LET all_nested_rgs = is_kb_rg ? (
            // KB: Use belongsTo edges
            FOR edge IN belongsTo
                FILTER edge._to == rg._id AND STARTS_WITH(edge._from, "recordGroups/")
                LET child_rg = DOCUMENT(edge._from)
                FILTER child_rg != null AND child_rg.connectorName == "KB" AND child_rg.orgId == @org_id
                RETURN child_rg
        ) : (
            FOR child_rg IN recordGroups
                FILTER child_rg.connectorId == rg.connectorId
                FILTER child_rg.parentExternalGroupId == rg.externalGroupId
                RETURN child_rg
        )

        // For connector record groups, check permissions; for KB, allow all (KB-level permission applies)
        // For connector record groups:
        // Since we already verified access to the parent RG (in the query calling this),
        // we can assume access to its children for Drive-like connectors.
        // For KB, we also allow all children (KB permission applies).
        LET accessible_nested_rg_ids = (
            FOR child_rg IN all_nested_rgs RETURN child_rg._key
        )

        LET user_from = CONCAT("users/", @user_key)

        // For connector record groups, check permissions for nested record groups
        LET accessible_nested_rg_ids_with_perm = is_kb_rg ? accessible_nested_rg_ids : (
            FOR child_rg IN all_nested_rgs
                // Check if user has permission to this nested record group
                LET has_direct_perm = LENGTH(
                    FOR perm IN permission
                        FILTER perm._from == user_from AND perm._to == child_rg._id AND perm.type == "USER"
                        RETURN 1
                ) > 0
                LET has_group_perm = LENGTH(
                    FOR group, userEdge IN 1..1 ANY user_from permission
                        FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                        FOR perm IN permission
                            FILTER perm._from == group._id AND perm._to == child_rg._id
                            FILTER perm.type == "GROUP" OR perm.type == "ROLE"
                            RETURN 1
                ) > 0
                LET has_org_perm = LENGTH(
                    FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                        FILTER belongsEdge.entityType == "ORGANIZATION"
                        FOR perm IN permission
                            FILTER perm._from == org._id AND perm._to == child_rg._id
                            FILTER perm.type == "ORG"
                            RETURN 1
                ) > 0
                FILTER has_direct_perm OR has_group_perm OR has_org_perm
                RETURN child_rg._key
        )

        LET nested_rgs = (
            FOR child_rg IN all_nested_rgs
                FILTER is_kb_rg OR child_rg._key IN accessible_nested_rg_ids_with_perm
                // Check if user has permission to see nested record groups
                LET has_child_rgs = is_kb_rg ? (
                    // KB: Use belongsTo edges
                    LENGTH(
                        FOR edge IN belongsTo
                            FILTER edge._to == child_rg._id AND STARTS_WITH(edge._from, "recordGroups/")
                            LET sub_rg = DOCUMENT(edge._from)
                            FILTER sub_rg != null AND sub_rg.connectorName == "KB" AND sub_rg.isDeleted != true
                            RETURN 1
                    ) > 0
                ) : (
                    LENGTH(
                        FOR sub_rg IN recordGroups
                            FILTER sub_rg.parentId == child_rg._key AND sub_rg.isDeleted != true
                            // Check permission for nested record groups
                            LET sub_has_direct = LENGTH(FOR perm IN permission FILTER perm._from == user_from AND perm._to == sub_rg._id AND perm.type == "USER" RETURN 1) > 0
                            LET sub_has_group = LENGTH(
                                FOR group, userEdge IN 1..1 ANY user_from permission
                                    FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                                    FOR perm IN permission FILTER perm._from == group._id AND perm._to == sub_rg._id FILTER perm.type == "GROUP" OR perm.type == "ROLE" RETURN 1
                            ) > 0
                            LET sub_has_org = LENGTH(
                                FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                                    FILTER belongsEdge.entityType == "ORGANIZATION"
                                    FOR perm IN permission FILTER perm._from == org._id AND perm._to == sub_rg._id FILTER perm.type == "ORG" RETURN 1
                            ) > 0
                            FILTER sub_has_direct OR sub_has_group OR sub_has_org
                            RETURN 1
                    ) > 0
                )
                // For nested record groups, check records based on whether it's KB or App
                // Note: For hasChildren, we check ALL records (including nested ones), not just direct children
                LET has_records = is_kb_rg ? (
                    // KB: Use belongsTo edges
                    // Only direct children: externalParentId must be null
                    LENGTH(
                        FOR edge IN belongsTo
                            FILTER edge._from LIKE "records/%" AND edge._to == child_rg._id
                            LET r = DOCUMENT(edge._from)
                            FILTER r != null AND r.isDeleted != true
                            FILTER r.externalParentId == null
                            RETURN 1
                    ) > 0
                ) : (
                    // Connector: Find records using belongsTo edges, recordGroupId field, and inheritPermissions edges
                    // Check all three methods and combine with UNION_DISTINCT, then check permissions
                    LENGTH(
                        FOR record_id IN UNION_DISTINCT(
                            // Method 1: belongsTo edges
                            FOR edge IN belongsTo
                                FILTER edge._from LIKE "records/%" AND edge._to == child_rg._id
                                LET r = DOCUMENT(edge._from)
                                FILTER r != null AND r.isDeleted != true
                                RETURN r._id
                            ,
                            // Method 2: recordGroupId field
                            FOR r IN records
                                FILTER r.recordGroupId == child_rg._key
                                FILTER r != null AND r.isDeleted != true
                                RETURN r._id
                            ,
                            // Method 3: inheritPermissions edges
                            FOR edge IN inheritPermissions
                                FILTER edge._from LIKE "records/%" AND edge._to == child_rg._id
                                LET r = DOCUMENT(edge._from)
                                FILTER r != null AND r.isDeleted != true
                                RETURN r._id
                        )
                        LET r = DOCUMENT(record_id)
                        FILTER r != null
                        // Check if user has permission to this record
                        // Path 1: Direct user -> record permission
                        LET r_has_direct = LENGTH(FOR perm IN permission FILTER perm._from == user_from AND perm._to == r._id AND perm.type == "USER" RETURN 1) > 0
                        // Path 2: User -> group -> record permission
                        LET r_has_group = LENGTH(
                            FOR group, userEdge IN 1..1 ANY user_from permission
                                FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                                FOR perm IN permission FILTER perm._from == group._id AND perm._to == r._id FILTER perm.type == "GROUP" OR perm.type == "ROLE" RETURN 1
                        ) > 0
                        // Path 3: User -> org -> record permission
                        LET r_has_org = LENGTH(
                            FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                                FILTER belongsEdge.entityType == "ORGANIZATION"
                                FOR perm IN permission FILTER perm._from == org._id AND perm._to == r._id FILTER perm.type == "ORG" RETURN 1
                        ) > 0
                        // Path 4: User -> recordGroup (via inheritPermissions) -> record
                        LET r_has_inherit_rg = LENGTH(
                            FOR recordGroup, userToRgEdge IN 1..1 ANY user_from permission
                                FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                                FOR inheritEdge IN inheritPermissions
                                    FILTER inheritEdge._to == recordGroup._id AND inheritEdge._from == r._id
                                    RETURN 1
                        ) > 0
                        // Path 5: User -> group -> recordGroup (via inheritPermissions) -> record
                        LET r_has_group_rg = LENGTH(
                            FOR group, userEdge IN 1..1 ANY user_from permission
                                FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                                FOR recordGroup, groupToRgEdge IN 1..1 ANY group._id permission
                                    FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                                    FOR inheritEdge IN inheritPermissions
                                        FILTER inheritEdge._to == recordGroup._id AND inheritEdge._from == r._id
                                        RETURN 1
                        ) > 0
                        // Path 6: User -> org -> recordGroup (via inheritPermissions) -> record
                        LET r_has_org_rg = LENGTH(
                            FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                                FILTER belongsEdge.entityType == "ORGANIZATION"
                                FOR recordGroup, orgPerm IN 1..1 ANY org._id permission
                                    FILTER orgPerm.type == "ORG"
                                    FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                                    FOR inheritEdge IN inheritPermissions
                                        FILTER inheritEdge._to == recordGroup._id AND inheritEdge._from == r._id
                                        RETURN 1
                        ) > 0
                        FILTER r_has_direct OR r_has_group OR r_has_org OR r_has_inherit_rg OR r_has_group_rg OR r_has_org_rg
                        RETURN 1
                    ) > 0
                )
                RETURN {{
                    id: child_rg._key, name: child_rg.groupName, nodeType: "recordGroup",
                    parentId: @rg_doc_id, source: "{source}",
                    connector: child_rg.connectorName,
                    connectorId: "{source}" == "CONNECTOR" ? child_rg.connectorId : null,
                    kbId: "{source}" == "KB" ? PARSE_IDENTIFIER(@rg_doc_id).key : null,
                    recordType: null, recordGroupType: child_rg.groupType, indexingStatus: null,
                    createdAt: child_rg.createdAtTimestamp, updatedAt: child_rg.updatedAtTimestamp,
                    sizeInBytes: null, mimeType: null, extension: null,
                    webUrl: child_rg.webUrl, hasChildren: has_child_rgs OR has_records
                }}
        )

        // Find direct children records
        // KB: Use belongsTo edges (FROM record TO recordGroup)
        // App: Use belongsTo edges (FROM record TO recordGroup)
        LET records = is_kb_rg ? (
            // KB: Use belongsTo edges (record -> belongsTo -> recordGroup)
            // Only direct children: externalParentId must be null
            FOR edge IN belongsTo
                FILTER edge._from LIKE "records/%" AND edge._to == @rg_doc_id
                LET record = DOCUMENT(edge._from)
                FILTER record != null AND record.isDeleted != true AND record.orgId == @org_id
                FILTER record.externalParentId == null
                LET file_info = FIRST(FOR fe IN isOfType FILTER fe._from == record._id LET f = DOCUMENT(fe._to) RETURN f)
                LET is_folder = file_info != null AND file_info.isFile == false
                // For KB records, nested records still use recordRelations
                LET has_children = LENGTH(FOR ce IN recordRelations FILTER ce._from == record._id AND ce.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"] LET c = DOCUMENT(ce._to) FILTER c != null AND c.isDeleted != true RETURN 1) > 0
                RETURN {{
                    id: record._key, name: record.recordName, nodeType: is_folder ? "folder" : "record",
                    parentId: @rg_doc_id,
                    source: "{source}",
                    connector: record.connectorName,
                    connectorId: "{source}" == "CONNECTOR" ? record.connectorId : null,
                    kbId: "{source}" == "KB" ? record.connectorId : null,
                    recordType: record.recordType, recordGroupType: null, indexingStatus: record.indexingStatus,
                    createdAt: record.createdAtTimestamp, updatedAt: record.updatedAtTimestamp,
                    sizeInBytes: record.sizeInBytes != null ? record.sizeInBytes : file_info.fileSizeInBytes, mimeType: record.mimeType,
                    extension: file_info.extension, webUrl: record.webUrl,
                    hasChildren: has_children,
                    previewRenderable: record.previewRenderable != null ? record.previewRenderable : true
                }}
        ) : (
            // Connector: Find records belonging to this record group using belongsTo edges only
            // Only direct children: externalParentId must be null
            FOR edge IN belongsTo
                FILTER edge._from LIKE "records/%" AND edge._to == @rg_doc_id
                LET record = DOCUMENT(edge._from)
                FILTER record != null AND record.isDeleted != true AND record.externalParentId == null

                // Check if user has permission to this record via 6 paths:
                // Path 1: user->org<-record (direct org permission to record)
                LET path1_org_record = LENGTH(
                    FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                        FILTER belongsEdge.entityType == "ORGANIZATION"
                        FOR perm IN permission
                            FILTER perm._from == org._id AND perm._to == record._id AND perm.type == "ORG"
                            RETURN 1
                ) > 0

                // Path 2: user->org<-recordGroup<-..nested recordGroup..<-record (org permission to recordGroup, then nested recordGroups via belongsTo, then record via inheritPermissions)
                LET path2_org_rg_record = LENGTH(
                    FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                        FILTER belongsEdge.entityType == "ORGANIZATION"
                        FOR recordGroup, orgPerm IN 1..1 ANY org._id permission
                            FILTER orgPerm.type == "ORG"
                            FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                            // Traverse nested recordGroups via belongsTo edges, then check inheritPermissions to record
                            FOR nested_rg, rgEdge IN 0..10 OUTBOUND recordGroup._id belongsTo
                                FILTER IS_SAME_COLLECTION("recordGroups", nested_rg)
                                FOR inheritEdge IN inheritPermissions
                                    FILTER inheritEdge._to == nested_rg._id AND inheritEdge._from == record._id
                                    RETURN 1
                ) > 0

                // Path 3: user->record (direct user permission to record)
                LET path3_user_record = LENGTH(
                    FOR perm IN permission
                        FILTER perm._from == user_from AND perm._to == record._id AND perm.type == "USER"
                        RETURN 1
                ) > 0

                // Path 4: user->recordGroup->..nested recordGroup..->record (user permission to recordGroup, then nested recordGroups via belongsTo, then record via inheritPermissions)
                LET path4_user_rg_record = LENGTH(
                    FOR recordGroup, userPerm IN 1..1 ANY user_from permission
                        FILTER userPerm.type == "USER"
                        FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                        // Traverse nested recordGroups via belongsTo edges, then check inheritPermissions to record
                        FOR nested_rg, rgEdge IN 0..10 OUTBOUND recordGroup._id belongsTo
                            FILTER IS_SAME_COLLECTION("recordGroups", nested_rg)
                            FOR inheritEdge IN inheritPermissions
                                FILTER inheritEdge._to == nested_rg._id AND inheritEdge._from == record._id
                                RETURN 1
                ) > 0

                // Path 5: user->group->record (group permission to record)
                LET path5_group_record = LENGTH(
                    FOR group, userEdge IN 1..1 ANY user_from permission
                        FILTER userEdge.type == "USER"
                        FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                        FOR perm IN permission
                            FILTER perm._from == group._id AND perm._to == record._id AND (perm.type == "GROUP" OR perm.type == "ROLE")
                            RETURN 1
                ) > 0

                // Path 6: user->group->recordGroup->..nested recordGroup..->record (group permission to recordGroup, then nested recordGroups via belongsTo, then record via inheritPermissions)
                LET path6_group_rg_record = LENGTH(
                    FOR group, userEdge IN 1..1 ANY user_from permission
                        FILTER userEdge.type == "USER"
                        FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                        FOR recordGroup, groupPerm IN 1..1 ANY group._id permission
                            FILTER groupPerm.type == "GROUP" OR groupPerm.type == "ROLE"
                            FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                            // Traverse nested recordGroups via belongsTo edges, then check inheritPermissions to record
                            FOR nested_rg, rgEdge IN 0..10 OUTBOUND recordGroup._id belongsTo
                                FILTER IS_SAME_COLLECTION("recordGroups", nested_rg)
                                FOR inheritEdge IN inheritPermissions
                                    FILTER inheritEdge._to == nested_rg._id AND inheritEdge._from == record._id
                                    RETURN 1
                ) > 0

                FILTER path1_org_record OR path2_org_rg_record OR path3_user_record OR path4_user_rg_record OR path5_group_record OR path6_group_rg_record

                LET file_info = FIRST(FOR fe IN isOfType FILTER fe._from == record._id LET f = DOCUMENT(fe._to) RETURN f)
                LET is_folder = file_info != null AND file_info.isFile == false
                // For nested records, use recordRelations (Record -> Record) with both PARENT_CHILD and ATTACHMENT
                LET has_children = LENGTH(FOR ce IN recordRelations FILTER ce._from == record._id AND ce.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"] LET c = DOCUMENT(ce._to) FILTER c != null AND c.isDeleted != true RETURN 1) > 0
                RETURN {{
                    id: record._key, name: record.recordName, nodeType: is_folder ? "folder" : "record",
                    parentId: @rg_doc_id,
                    source: "{source}",
                    connector: record.connectorName,
                    connectorId: "{source}" == "CONNECTOR" ? record.connectorId : null,
                    kbId: "{source}" == "KB" ? record.connectorId : null,
                    recordType: record.recordType, recordGroupType: null, indexingStatus: record.indexingStatus,
                    createdAt: record.createdAtTimestamp, updatedAt: record.updatedAtTimestamp,
                    sizeInBytes: record.sizeInBytes != null ? record.sizeInBytes : file_info.fileSizeInBytes, mimeType: record.mimeType,
                    extension: file_info.extension, webUrl: record.webUrl,
                    hasChildren: has_children,
                    previewRenderable: record.previewRenderable != null ? record.previewRenderable : true
                }}
        )

        LET raw_children = UNION(nested_rgs, records)
        """
        return sub_query, {"rg_doc_id": rg_doc_id, "org_id": org_id, "user_key": user_key}

    def _get_record_children_subquery(self, record_id: str, org_id: str, user_key: str) -> Tuple[str, Dict[str, Any]]:
        """Generate AQL sub-query to fetch children of a Folder/Record."""
        record_doc_id = f"records/{record_id}"

        sub_query = """
        LET parent_record = DOCUMENT(@record_doc_id)
        FILTER parent_record != null

        LET parent_connector_doc = DOCUMENT(CONCAT("recordGroups/", parent_record.connectorId)) || DOCUMENT(CONCAT("apps/", parent_record.connectorId))
        // For KB records, check connectorName directly on the record, or check if app type is KB
        LET is_kb_parent = (parent_record.connectorName == "KB") OR (parent_connector_doc != null AND parent_connector_doc.type == "KB")

        LET user_from = CONCAT("users/", @user_key)

        LET raw_children = (
            FOR edge IN recordRelations
                FILTER edge._from == @record_doc_id AND edge.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                LET record = DOCUMENT(edge._to)
                FILTER record != null AND record.isDeleted != true AND record.orgId == @org_id
                LET record_connector_doc = DOCUMENT(CONCAT("recordGroups/", record.connectorId)) || DOCUMENT(CONCAT("apps/", record.connectorId))
                LET source = (record_connector_doc != null AND record_connector_doc.connectorName == "KB") ? "KB" : "CONNECTOR"
                // For connector records, check permission through:
                // 1. inheritPermissions edge (record -> recordGroup)
                // 2. Direct user -> record permission
                // 3. User -> group -> record permission
                // 4. User -> org -> record permission
                // 5. User -> org -> recordGroup -> record (via inheritPermissions)
                LET has_inherit_perm = LENGTH(
                    FOR ip IN inheritPermissions FILTER ip._from == record._id RETURN 1
                ) > 0
                LET has_direct_perm = LENGTH(
                    FOR perm IN permission
                        FILTER perm._from == user_from AND perm._to == record._id AND perm.type == "USER"
                        RETURN 1
                ) > 0
                LET has_group_perm = LENGTH(
                    FOR group, userEdge IN 1..1 ANY user_from permission
                        FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                        FOR perm IN permission
                            FILTER perm._from == group._id AND perm._to == record._id
                            FILTER perm.type == "GROUP" OR perm.type == "ROLE"
                            RETURN 1
                ) > 0
                LET has_org_perm = LENGTH(
                    FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                        FILTER belongsEdge.entityType == "ORGANIZATION"
                        FOR perm IN permission
                            FILTER perm._from == org._id AND perm._to == record._id
                            FILTER perm.type == "ORG"
                            RETURN 1
                ) > 0
                LET has_org_rg_perm = LENGTH(
                    FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                        FILTER belongsEdge.entityType == "ORGANIZATION"
                        FOR recordGroup, orgPerm IN 1..1 ANY org._id permission
                            FILTER orgPerm.type == "ORG"
                            FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                            FOR inheritEdge IN inheritPermissions
                                FILTER inheritEdge._to == recordGroup._id AND inheritEdge._from == record._id
                                RETURN 1
                ) > 0
                LET has_permission = is_kb_parent ? true : (has_inherit_perm OR has_direct_perm OR has_group_perm OR has_org_perm OR has_org_rg_perm)
                FILTER has_permission
                LET file_info = FIRST(FOR fe IN isOfType FILTER fe._from == record._id LET f = DOCUMENT(fe._to) RETURN f)
                LET is_folder = file_info != null AND file_info.isFile == false
                // For hasChildren, also check all permission paths for connector records
                LET has_children = is_kb_parent ? (
                    LENGTH(FOR ce IN recordRelations FILTER ce._from == record._id AND ce.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"] LET c = DOCUMENT(ce._to) FILTER c != null AND c.isDeleted != true RETURN 1) > 0
                ) : (
                    LENGTH(
                        FOR ce IN recordRelations
                            FILTER ce._from == record._id AND ce.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                            LET c = DOCUMENT(ce._to)
                            FILTER c != null AND c.isDeleted != true
                            LET c_has_ip = LENGTH(FOR ip IN inheritPermissions FILTER ip._from == c._id RETURN 1) > 0
                            LET c_has_direct = LENGTH(FOR perm IN permission FILTER perm._from == user_from AND perm._to == c._id AND perm.type == "USER" RETURN 1) > 0
                            LET c_has_group = LENGTH(
                                FOR grp, ue IN 1..1 ANY user_from permission
                                    FILTER IS_SAME_COLLECTION("groups", grp) OR IS_SAME_COLLECTION("roles", grp)
                                    FOR perm IN permission FILTER perm._from == grp._id AND perm._to == c._id FILTER perm.type == "GROUP" OR perm.type == "ROLE" RETURN 1
                            ) > 0
                            LET c_has_org = LENGTH(
                                FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                                    FILTER belongsEdge.entityType == "ORGANIZATION"
                                    FOR perm IN permission
                                        FILTER perm._from == org._id AND perm._to == c._id
                                        FILTER perm.type == "ORG"
                                        RETURN 1
                            ) > 0
                            LET c_has_org_rg = LENGTH(
                                FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                                    FILTER belongsEdge.entityType == "ORGANIZATION"
                                    FOR recordGroup, orgPerm IN 1..1 ANY org._id permission
                                        FILTER orgPerm.type == "ORG"
                                        FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                                        FOR inheritEdge IN inheritPermissions
                                            FILTER inheritEdge._to == recordGroup._id AND inheritEdge._from == c._id
                                            RETURN 1
                            ) > 0
                            FILTER c_has_ip OR c_has_direct OR c_has_group OR c_has_org OR c_has_org_rg
                            RETURN 1
                    ) > 0
                )
                RETURN {
                    id: record._key, name: record.recordName,
                    nodeType: is_folder ? "folder" : "record",
                    parentId: @record_doc_id,
                    source: source,
                    connector: record.connectorName,
                    connectorId: source == "CONNECTOR" ? record.connectorId : null,
                    kbId: source == "KB" ? record.connectorId : null,
                    recordType: record.recordType, recordGroupType: null, indexingStatus: record.indexingStatus,
                    createdAt: record.createdAtTimestamp, updatedAt: record.updatedAtTimestamp,
                    sizeInBytes: record.sizeInBytes != null ? record.sizeInBytes : file_info.fileSizeInBytes, mimeType: record.mimeType,
                    extension: file_info.extension, webUrl: record.webUrl,
                    hasChildren: has_children,
                    previewRenderable: record.previewRenderable != null ? record.previewRenderable : true
                }
        )
        """
        return sub_query, {"record_doc_id": record_doc_id, "user_key": user_key}

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

        ArangoDB implementation: Converts structured parameters to AQL.
        """
        # Determine the starting node ID
        if parent_type in ("kb", "recordGroup"):
            parent_doc_id = f"recordGroups/{parent_id}"
        elif parent_type in ("folder", "record"):
            parent_doc_id = f"records/{parent_id}"
        elif parent_type == "app":
            parent_doc_id = f"apps/{parent_id}"
        else:
            return {"nodes": [], "total": 0}

        # Build AQL filter conditions from structured parameters
        filter_conditions = []
        bind_vars = {
            "parent_doc_id": parent_doc_id,
            "org_id": org_id,
            "user_key": user_key,
            "skip": skip,
            "limit": limit,
            "sort_field": sort_field,
            "sort_dir": sort_dir,
            "only_containers": only_containers,
        }

        # Search query filter (handled separately in recursive search)
        search_filter = ""
        if search_query:
            bind_vars["search_query"] = search_query.lower()
            search_filter = "FILTER LOWER(node.name) LIKE CONCAT('%', @search_query, '%')"

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
            if type_conditions:
                filter_conditions.append(f"({' OR '.join(type_conditions)})")

        # Record type filter
        if record_types:
            bind_vars["record_types"] = record_types
            filter_conditions.append("(node.recordType != null AND node.recordType IN @record_types)")

        # Indexing status filter
        if indexing_status:
            bind_vars["indexing_status"] = indexing_status
            filter_conditions.append("(node.indexingStatus == null OR node.indexingStatus IN @indexing_status)")

        # Created date filter
        if created_at:
            if created_at.get("gte"):
                bind_vars["created_at_gte"] = created_at["gte"]
                filter_conditions.append("node.createdAt >= @created_at_gte")
            if created_at.get("lte"):
                bind_vars["created_at_lte"] = created_at["lte"]
                filter_conditions.append("node.createdAt <= @created_at_lte")

        # Updated date filter
        if updated_at:
            if updated_at.get("gte"):
                bind_vars["updated_at_gte"] = updated_at["gte"]
                filter_conditions.append("node.updatedAt >= @updated_at_gte")
            if updated_at.get("lte"):
                bind_vars["updated_at_lte"] = updated_at["lte"]
                filter_conditions.append("node.updatedAt <= @updated_at_lte")

        # Size filter
        if size:
            if size.get("gte"):
                bind_vars["size_gte"] = size["gte"]
                filter_conditions.append("(node.sizeInBytes == null OR node.sizeInBytes >= @size_gte)")
            if size.get("lte"):
                bind_vars["size_lte"] = size["lte"]
                filter_conditions.append("(node.sizeInBytes == null OR node.sizeInBytes <= @size_lte)")

        # Origins filter
        if origins:
            bind_vars["origins"] = origins
            filter_conditions.append("node.source IN @origins")

        # Connector/KB IDs filter
        if connector_ids and kb_ids:
            bind_vars["connector_ids"] = connector_ids
            bind_vars["kb_ids"] = kb_ids
            filter_conditions.append(
                "((node.nodeType == 'app' AND node.id IN @connector_ids) OR (node.connectorId IN @connector_ids) OR "
                "(node.nodeType == 'kb' AND node.id IN @kb_ids) OR (node.kbId IN @kb_ids))"
            )
        elif connector_ids:
            bind_vars["connector_ids"] = connector_ids
            filter_conditions.append("(node.nodeType == 'app' AND node.id IN @connector_ids) OR (node.connectorId IN @connector_ids)")
        elif kb_ids:
            bind_vars["kb_ids"] = kb_ids
            filter_conditions.append("(node.nodeType == 'kb' AND node.id IN @kb_ids) OR (node.kbId IN @kb_ids)")

        # Build final filter clause
        filter_clause = " AND ".join(filter_conditions) if filter_conditions else "true"

        # Build recordGroup query based on parent type
        source_value = "KB" if parent_type == "kb" else "CONNECTOR"

        if parent_type in ("kb", "recordGroup"):
            bind_vars["parent_id_for_rg"] = parent_id
            # For KB, use belongsTo edges; for connector, use parentId (though connector uses parentExternalGroupId)
            # We'll check connectorName in the query to determine which method to use
            rg_parent_filter = "rg.parentId == @parent_id_for_rg OR LENGTH(FOR edge IN belongsTo FILTER edge._from == rg._id AND edge._to == CONCAT('recordGroups/', @parent_id_for_rg) RETURN 1) > 0"
        elif parent_type == "app":
            bind_vars["parent_id_for_rg"] = parent_id
            rg_parent_filter = "rg.connectorId == @parent_id_for_rg"
        else:
            # For folder/record types, set dummy value (won't be used but required by AQL)
            bind_vars["parent_id_for_rg"] = parent_id
            rg_parent_filter = "false"

        query = f"""
        LET parent = DOCUMENT(@parent_doc_id)
        FILTER parent != null

        LET user_from = CONCAT("users/", @user_key)

        // Determine traversal strategy:
        // - Records/Folders: Always use recordRelations (children are connected via PARENT_CHILD edges)
        // - KB RecordGroups: Use belongsTo for direct children, then recordRelations for nested records
        // - App RecordGroups: Use belongsTo edges (records belong to the recordGroup)
        LET is_record_parent = STARTS_WITH(@parent_doc_id, "records/")
        LET is_kb_parent = parent.connectorName == "KB"
        LET use_record_relations = is_record_parent

        // Determine if parent record is from KB or connector
        LET parent_record_connector = is_record_parent ? (
            DOCUMENT(CONCAT("recordGroups/", parent.connectorId)) || DOCUMENT(CONCAT("apps/", parent.connectorId))
        ) : null
        LET is_kb_record_parent = parent_record_connector != null AND parent_record_connector.connectorName == "KB"

        // Traverse recursively from parent to find all descendants (records)
        // KB RecordGroups: Get direct children via belongsTo, then traverse nested records via recordRelations
        // Records: Use recordRelations OUTBOUND
        // App recordGroups: Use belongsTo INBOUND to get direct children, then recordRelations OUTBOUND for nested
        LET all_descendants = is_kb_parent ? (
            // KB: Get direct children via belongsTo, then traverse nested records via recordRelations
            UNION_DISTINCT(
                // Direct children (records) via belongsTo
                // Only direct children: externalParentId must be null
                FOR edge IN belongsTo
                    FILTER edge._to == @parent_doc_id AND STARTS_WITH(edge._from, "records/")
                    LET v = DOCUMENT(edge._from)
                    FILTER v != null AND v.isDeleted != true AND v.orgId == @org_id
                    FILTER v.externalParentId == null
                    RETURN v
                ,
                // Nested records via recordRelations (from direct children)
                FOR direct_edge IN belongsTo
                    FILTER direct_edge._to == @parent_doc_id AND STARTS_WITH(direct_edge._from, "records/")
                    LET direct_record = DOCUMENT(direct_edge._from)
                    FILTER direct_record != null AND direct_record.isDeleted != true AND direct_record.orgId == @org_id
                    FILTER direct_record.externalParentId == null
                    FOR v, e, p IN 1..10 OUTBOUND direct_record._id recordRelations
                        OPTIONS {{bfs: true}}
                        FILTER e.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                        FILTER v.isDeleted != true AND v.orgId == @org_id
                        RETURN v
            )
        ) : use_record_relations ? (
            // Records: Traverse via recordRelations OUTBOUND
            // For connector records, check all permission paths
            FOR v, e, p IN 1..10 OUTBOUND @parent_doc_id recordRelations
                OPTIONS {{bfs: true}}
                FILTER e.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                FILTER v.isDeleted != true AND v.orgId == @org_id
                // For connector records, check permission through:
                // 1. inheritPermissions edge (record -> recordGroup)
                // 2. Direct user -> record permission
                // 3. User -> group -> record permission
                // 4. User -> org -> record permission
                // 5. User -> org -> recordGroup -> record (via inheritPermissions)
                LET has_inherit_perm = LENGTH(FOR ip IN inheritPermissions FILTER ip._from == v._id RETURN 1) > 0
                LET has_direct_perm = LENGTH(
                    FOR perm IN permission FILTER perm._from == user_from AND perm._to == v._id AND perm.type == "USER" RETURN 1
                ) > 0
                LET has_group_perm = LENGTH(
                    FOR grp, ue IN 1..1 ANY user_from permission
                        FILTER IS_SAME_COLLECTION("groups", grp) OR IS_SAME_COLLECTION("roles", grp)
                        FOR perm IN permission FILTER perm._from == grp._id AND perm._to == v._id FILTER perm.type == "GROUP" OR perm.type == "ROLE" RETURN 1
                ) > 0
                LET has_org_perm = LENGTH(
                    FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                        FILTER belongsEdge.entityType == "ORGANIZATION"
                        FOR perm IN permission
                            FILTER perm._from == org._id AND perm._to == v._id
                            FILTER perm.type == "ORG"
                            RETURN 1
                ) > 0
                LET has_org_rg_perm = LENGTH(
                    FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                        FILTER belongsEdge.entityType == "ORGANIZATION"
                        FOR recordGroup, orgPerm IN 1..1 ANY org._id permission
                            FILTER orgPerm.type == "ORG"
                            FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                            FOR inheritEdge IN inheritPermissions
                                FILTER inheritEdge._to == recordGroup._id AND inheritEdge._from == v._id
                                RETURN 1
                ) > 0
                LET has_permission = (is_kb_parent OR is_kb_record_parent) ? true : (has_inherit_perm OR has_direct_perm OR has_group_perm OR has_org_perm OR has_org_rg_perm)
                FILTER has_permission
                LET file_info = FIRST(FOR fe IN isOfType FILTER fe._from == v._id LET f = DOCUMENT(fe._to) RETURN f)
                LET is_folder = file_info != null AND file_info.isFile == false
                LET has_children = (is_kb_parent OR is_kb_record_parent) ? (
                    LENGTH(
                        FOR ce IN recordRelations
                        FILTER ce._from == v._id AND ce.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                        LET c = DOCUMENT(ce._to)
                        FILTER c != null AND c.isDeleted != true
                        RETURN 1
                    ) > 0
                ) : (
                    LENGTH(
                        FOR ce IN recordRelations
                        FILTER ce._from == v._id AND ce.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                        LET c = DOCUMENT(ce._to)
                        FILTER c != null AND c.isDeleted != true
                        LET c_has_ip = LENGTH(FOR ip IN inheritPermissions FILTER ip._from == c._id RETURN 1) > 0
                        LET c_has_direct = LENGTH(FOR perm IN permission FILTER perm._from == user_from AND perm._to == c._id AND perm.type == "USER" RETURN 1) > 0
                        LET c_has_grp = LENGTH(FOR grp2, ue2 IN 1..1 ANY user_from permission FILTER IS_SAME_COLLECTION("groups", grp2) OR IS_SAME_COLLECTION("roles", grp2) FOR perm IN permission FILTER perm._from == grp2._id AND perm._to == c._id FILTER perm.type == "GROUP" OR perm.type == "ROLE" RETURN 1) > 0
                        LET c_has_org = LENGTH(
                            FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                                FILTER belongsEdge.entityType == "ORGANIZATION"
                                FOR perm IN permission
                                    FILTER perm._from == org._id AND perm._to == c._id
                                    FILTER perm.type == "ORG"
                                    RETURN 1
                        ) > 0
                        LET c_has_org_rg = LENGTH(
                            FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                                FILTER belongsEdge.entityType == "ORGANIZATION"
                                FOR recordGroup, orgPerm IN 1..1 ANY org._id permission
                                    FILTER orgPerm.type == "ORG"
                                    FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                                    FOR inheritEdge IN inheritPermissions
                                        FILTER inheritEdge._to == recordGroup._id AND inheritEdge._from == c._id
                                        RETURN 1
                        ) > 0
                        FILTER c_has_ip OR c_has_direct OR c_has_grp OR c_has_org OR c_has_org_rg
                        RETURN 1
                    ) > 0
                )
                LET record_connector = DOCUMENT(CONCAT("recordGroups/", v.connectorId)) || DOCUMENT(CONCAT("apps/", v.connectorId))
                LET source = (record_connector != null AND record_connector.connectorName == "KB") ? "KB" : "CONNECTOR"
                LET parent_edge = p.edges[-1]
                RETURN {{
                    id: v._key,
                    name: v.recordName || v.groupName,
                    nodeType: is_folder ? "folder" : "record",
                    parentId: parent_edge ? PARSE_IDENTIFIER(parent_edge._from).key : null,
                    source: source,
                    connector: v.connectorName,
                    connectorId: source == "CONNECTOR" ? v.connectorId : null,
                    kbId: source == "KB" ? v.connectorId : null,
                    recordType: v.recordType,
                    recordGroupType: null,
                    indexingStatus: v.indexingStatus,
                    createdAt: v.createdAtTimestamp,
                    updatedAt: v.updatedAtTimestamp,
                    sizeInBytes: v.sizeInBytes != null ? v.sizeInBytes : (file_info ? file_info.fileSizeInBytes : null),
                    mimeType: file_info ? file_info.mimeType : null,
                    extension: file_info ? file_info.extension : null,
                    webUrl: v.webUrl,
                    hasChildren: has_children,
                    previewRenderable: v.previewRenderable != null ? v.previewRenderable : true
                }}
        ) : (
            // Connector recordGroup: Get direct children via inheritPermissions edges (FROM record TO recordGroup)
            // Only records with inheritPermissions edge can be accessed through recordGroup
            // Then traverse nested via recordRelations
            LET direct_children = (
                FOR edge IN inheritPermissions
                    FILTER edge._to == @parent_doc_id AND STARTS_WITH(edge._from, "records/")
                    LET record = DOCUMENT(edge._from)
                    FILTER record != null AND record.isDeleted != true AND record.orgId == @org_id
                    // Only direct children: externalParentId must be null (not nested under another record)
                    FILTER record.externalParentId == null
                    RETURN record._id
            )
            // Step 2: Traverse nested records from direct children via recordRelations
            // Each nested record must have permission through inheritPermissions, direct, or group
            FOR direct_child_id IN direct_children
                FOR v, e, p IN 0..10 OUTBOUND direct_child_id recordRelations
                    OPTIONS {{bfs: true}}
                    FILTER e.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"] OR e == null
                    FILTER v.isDeleted != true AND v.orgId == @org_id
                    // Check all permission paths
                    LET has_inherit_perm = LENGTH(FOR ip IN inheritPermissions FILTER ip._from == v._id RETURN 1) > 0
                    LET has_direct_perm = LENGTH(FOR perm IN permission FILTER perm._from == user_from AND perm._to == v._id AND perm.type == "USER" RETURN 1) > 0
                    LET has_group_perm = LENGTH(FOR grp, ue IN 1..1 ANY user_from permission FILTER IS_SAME_COLLECTION("groups", grp) OR IS_SAME_COLLECTION("roles", grp) FOR perm IN permission FILTER perm._from == grp._id AND perm._to == v._id FILTER perm.type == "GROUP" OR perm.type == "ROLE" RETURN 1) > 0
                    LET has_org_perm = LENGTH(
                        FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                            FILTER belongsEdge.entityType == "ORGANIZATION"
                            FOR perm IN permission
                                FILTER perm._from == org._id AND perm._to == v._id
                                FILTER perm.type == "ORG"
                                RETURN 1
                    ) > 0
                    LET has_org_rg_perm = LENGTH(
                        FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                            FILTER belongsEdge.entityType == "ORGANIZATION"
                            FOR recordGroup, orgPerm IN 1..1 ANY org._id permission
                                FILTER orgPerm.type == "ORG"
                                FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                                FOR inheritEdge IN inheritPermissions
                                    FILTER inheritEdge._to == recordGroup._id AND inheritEdge._from == v._id
                                    RETURN 1
                    ) > 0
                    FILTER has_inherit_perm OR has_direct_perm OR has_group_perm OR has_org_perm OR has_org_rg_perm
                    LET file_info = FIRST(FOR fe IN isOfType FILTER fe._from == v._id LET f = DOCUMENT(fe._to) RETURN f)
                    LET is_folder = file_info != null AND file_info.isFile == false
                    LET has_children = LENGTH(
                        FOR ce IN recordRelations
                        FILTER ce._from == v._id AND ce.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                        LET c = DOCUMENT(ce._to)
                        FILTER c != null AND c.isDeleted != true
                        LET c_has_ip = LENGTH(FOR ip IN inheritPermissions FILTER ip._from == c._id RETURN 1) > 0
                        LET c_has_direct = LENGTH(FOR perm IN permission FILTER perm._from == user_from AND perm._to == c._id AND perm.type == "USER" RETURN 1) > 0
                        LET c_has_grp = LENGTH(FOR grp2, ue2 IN 1..1 ANY user_from permission FILTER IS_SAME_COLLECTION("groups", grp2) OR IS_SAME_COLLECTION("roles", grp2) FOR perm IN permission FILTER perm._from == grp2._id AND perm._to == c._id FILTER perm.type == "GROUP" OR perm.type == "ROLE" RETURN 1) > 0
                        LET c_has_org = LENGTH(
                            FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                                FILTER belongsEdge.entityType == "ORGANIZATION"
                                FOR perm IN permission
                                    FILTER perm._from == org._id AND perm._to == c._id
                                    FILTER perm.type == "ORG"
                                    RETURN 1
                        ) > 0
                        LET c_has_org_rg = LENGTH(
                            FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                                FILTER belongsEdge.entityType == "ORGANIZATION"
                                FOR recordGroup, orgPerm IN 1..1 ANY org._id permission
                                    FILTER orgPerm.type == "ORG"
                                    FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                                    FOR inheritEdge IN inheritPermissions
                                        FILTER inheritEdge._to == recordGroup._id AND inheritEdge._from == c._id
                                        RETURN 1
                        ) > 0
                        FILTER c_has_ip OR c_has_direct OR c_has_grp OR c_has_org OR c_has_org_rg
                        RETURN 1
                    ) > 0
                    LET record_connector = DOCUMENT(CONCAT("recordGroups/", v.connectorId)) || DOCUMENT(CONCAT("apps/", v.connectorId))
                    LET source = (record_connector != null AND record_connector.connectorName == "KB") ? "KB" : "CONNECTOR"
                    LET parent_edge = p.edges != null AND LENGTH(p.edges) > 0 ? p.edges[-1] : null
                    RETURN {{
                        id: v._key,
                        name: v.recordName || v.groupName,
                        nodeType: is_folder ? "folder" : "record",
                        parentId: parent_edge != null ? PARSE_IDENTIFIER(parent_edge._from).key : (direct_child_id == v._id ? PARSE_IDENTIFIER(@parent_doc_id).key : null),
                        source: source,
                        connector: v.connectorName,
                        connectorId: source == "CONNECTOR" ? v.connectorId : null,
                        kbId: source == "KB" ? v.connectorId : null,
                        recordType: v.recordType,
                        recordGroupType: null,
                        indexingStatus: v.indexingStatus,
                        createdAt: v.createdAtTimestamp,
                        updatedAt: v.updatedAtTimestamp,
                        sizeInBytes: file_info ? file_info.fileSizeInBytes : null,
                        mimeType: file_info ? file_info.mimeType : null,
                        extension: file_info ? file_info.extension : null,
                        webUrl: v.webUrl,
                        hasChildren: has_children,
                        previewRenderable: v.previewRenderable != null ? v.previewRenderable : true
                    }}
        )

        // Also include direct child recordGroups for KB/App parents (and recursively their descendants)
        LET nested_record_groups = (
            FOR rg IN recordGroups
                FILTER rg.orgId == @org_id AND rg.isDeleted != true
                FILTER {rg_parent_filter}
                // Recursively get all descendants of this record group
                // KB: Use recordRelations OUTBOUND
                // App: Use belongsTo INBOUND for direct children, then recordRelations OUTBOUND for nested
                LET rg_descendants = rg.connectorName == "KB" ? (
                    // KB: Traverse via recordRelations OUTBOUND
                    FOR v2, e2, p2 IN 1..10 OUTBOUND rg._id recordRelations
                        OPTIONS {{bfs: true}}
                        FILTER e2.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                        FILTER v2.isDeleted != true AND v2.orgId == @org_id
                        LET file_info2 = FIRST(FOR fe2 IN isOfType FILTER fe2._from == v2._id LET f2 = DOCUMENT(fe2._to) RETURN f2)
                        LET is_folder2 = file_info2 != null AND file_info2.isFile == false
                        LET has_children2 = LENGTH(
                            FOR ce2 IN recordRelations
                            FILTER ce2._from == v2._id AND ce2.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                            LET c2 = DOCUMENT(ce2._to)
                            FILTER c2 != null AND c2.isDeleted != true
                            RETURN 1
                        ) > 0
                        LET record_connector2 = DOCUMENT(CONCAT("recordGroups/", v2.connectorId)) || DOCUMENT(CONCAT("apps/", v2.connectorId))
                        LET source2 = (record_connector2 != null AND record_connector2.connectorName == "KB") ? "KB" : "CONNECTOR"
                        RETURN {{
                            id: v2._key,
                            name: v2.recordName || v2.groupName,
                            nodeType: is_folder2 ? "folder" : "record",
                            parentId: PARSE_IDENTIFIER(e2._from).key,
                            source: source2,
                            connector: v2.connectorName,
                            connectorId: source2 == "CONNECTOR" ? v2.connectorId : null,
                            kbId: source2 == "KB" ? v2.connectorId : null,
                            recordType: v2.recordType,
                            recordGroupType: null,
                            indexingStatus: v2.indexingStatus,
                            createdAt: v2.createdAtTimestamp,
                            updatedAt: v2.updatedAtTimestamp,
                            sizeInBytes: file_info2 ? file_info2.fileSizeInBytes : null,
                            mimeType: file_info2 ? file_info2.mimeType : null,
                            extension: file_info2 ? file_info2.extension : null,
                            webUrl: v2.webUrl,
                            hasChildren: has_children2,
                            previewRenderable: v2.previewRenderable != null ? v2.previewRenderable : true
                        }}
                ) : (
                    // Connector: Get direct children via inheritPermissions, then traverse nested via recordRelations
                    // Each nested record must have permission through inheritPermissions, direct, or group
                    LET direct_children_rg = (
                        FOR edge IN inheritPermissions
                            FILTER edge._to == rg._id AND STARTS_WITH(edge._from, "records/")
                            LET record = DOCUMENT(edge._from)
                            FILTER record != null AND record.isDeleted != true AND record.orgId == @org_id
                            FILTER record.externalParentId == null
                            RETURN record._id
                    )
                    FOR direct_child_id IN direct_children_rg
                        FOR v2, e2, p2 IN 0..10 OUTBOUND direct_child_id recordRelations
                            OPTIONS {{bfs: true}}
                            FILTER e2.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"] OR e2 == null
                            FILTER v2.isDeleted != true AND v2.orgId == @org_id
                            // Check all permission paths
                            LET has_inherit_perm2 = LENGTH(FOR ip IN inheritPermissions FILTER ip._from == v2._id RETURN 1) > 0
                            LET has_direct_perm2 = LENGTH(FOR perm IN permission FILTER perm._from == user_from AND perm._to == v2._id AND perm.type == "USER" RETURN 1) > 0
                            LET has_group_perm2 = LENGTH(FOR grp, ue IN 1..1 ANY user_from permission FILTER IS_SAME_COLLECTION("groups", grp) OR IS_SAME_COLLECTION("roles", grp) FOR perm IN permission FILTER perm._from == grp._id AND perm._to == v2._id FILTER perm.type == "GROUP" OR perm.type == "ROLE" RETURN 1) > 0
                            FILTER has_inherit_perm2 OR has_direct_perm2 OR has_group_perm2
                            LET file_info2 = FIRST(FOR fe2 IN isOfType FILTER fe2._from == v2._id LET f2 = DOCUMENT(fe2._to) RETURN f2)
                            LET is_folder2 = file_info2 != null AND file_info2.isFile == false
                            LET has_children2 = LENGTH(
                                FOR ce2 IN recordRelations
                                FILTER ce2._from == v2._id AND ce2.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                                LET c2 = DOCUMENT(ce2._to)
                                FILTER c2 != null AND c2.isDeleted != true
                                LET c2_has_ip = LENGTH(FOR ip IN inheritPermissions FILTER ip._from == c2._id RETURN 1) > 0
                                LET c2_has_direct = LENGTH(FOR perm IN permission FILTER perm._from == user_from AND perm._to == c2._id AND perm.type == "USER" RETURN 1) > 0
                                LET c2_has_grp = LENGTH(FOR grp2, ue2 IN 1..1 ANY user_from permission FILTER IS_SAME_COLLECTION("groups", grp2) OR IS_SAME_COLLECTION("roles", grp2) FOR perm IN permission FILTER perm._from == grp2._id AND perm._to == c2._id FILTER perm.type == "GROUP" OR perm.type == "ROLE" RETURN 1) > 0
                                FILTER c2_has_ip OR c2_has_direct OR c2_has_grp
                                RETURN 1
                            ) > 0
                            LET record_connector2 = DOCUMENT(CONCAT("recordGroups/", v2.connectorId)) || DOCUMENT(CONCAT("apps/", v2.connectorId))
                            LET source2 = (record_connector2 != null AND record_connector2.connectorName == "KB") ? "KB" : "CONNECTOR"
                            LET parent_edge2 = p2.edges != null AND LENGTH(p2.edges) > 0 ? p2.edges[-1] : null
                            RETURN {{
                                id: v2._key,
                                name: v2.recordName || v2.groupName,
                                nodeType: is_folder2 ? "folder" : "record",
                                parentId: parent_edge2 != null ? PARSE_IDENTIFIER(parent_edge2._from).key : (direct_child_id == v2._id ? rg._key : null),
                                source: source2,
                                connector: v2.connectorName,
                                connectorId: source2 == "CONNECTOR" ? v2.connectorId : null,
                                kbId: source2 == "KB" ? v2.connectorId : null,
                                recordType: v2.recordType,
                                recordGroupType: null,
                                indexingStatus: v2.indexingStatus,
                                createdAt: v2.createdAtTimestamp,
                                updatedAt: v2.updatedAtTimestamp,
                                sizeInBytes: file_info2 ? file_info2.fileSizeInBytes : null,
                                mimeType: file_info2 ? file_info2.mimeType : null,
                                extension: file_info2 ? file_info2.extension : null,
                                webUrl: v2.webUrl,
                                hasChildren: has_children2,
                                previewRenderable: v2.previewRenderable != null ? v2.previewRenderable : true
                            }}
                )
                // Return the record group itself plus its descendants
                // Check if user has permission to see nested record groups
                LET has_child_rgs = rg.connectorName == "KB" ? (
                    LENGTH(FOR sub_rg IN recordGroups FILTER sub_rg.parentId == rg._key AND sub_rg.isDeleted != true RETURN 1) > 0
                ) : (
                    LENGTH(
                        FOR sub_rg IN recordGroups
                            FILTER sub_rg.parentId == rg._key AND sub_rg.isDeleted != true
                            // Check permission for nested record groups
                            LET sub_has_direct = LENGTH(FOR perm IN permission FILTER perm._from == user_from AND perm._to == sub_rg._id AND perm.type == "USER" RETURN 1) > 0
                            LET sub_has_group = LENGTH(
                                FOR group, userEdge IN 1..1 ANY user_from permission
                                    FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                                    FOR perm IN permission FILTER perm._from == group._id AND perm._to == sub_rg._id FILTER perm.type == "GROUP" OR perm.type == "ROLE" RETURN 1
                            ) > 0
                            LET sub_has_org = LENGTH(
                                FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                                    FILTER belongsEdge.entityType == "ORGANIZATION"
                                    FOR perm IN permission FILTER perm._from == org._id AND perm._to == sub_rg._id FILTER perm.type == "ORG" RETURN 1
                            ) > 0
                            FILTER sub_has_direct OR sub_has_group OR sub_has_org
                            RETURN 1
                    ) > 0
                )
                // has_records is already filtered by permissions in rg_descendants traversal
                LET has_records = LENGTH(rg_descendants) > 0
                LET rg_node = {{
                    id: rg._key,
                    name: rg.groupName,
                    nodeType: "recordGroup",
                    parentId: @parent_id_for_rg,
                    source: "{source_value}",
                    connector: rg.connectorName,
                    connectorId: "{source_value}" == "CONNECTOR" ? rg.connectorId : null,
                    kbId: "{source_value}" == "KB" ? @parent_id_for_rg : null,
                    recordType: null,
                    recordGroupType: rg.groupType,
                    indexingStatus: null,
                    createdAt: rg.createdAtTimestamp,
                    updatedAt: rg.updatedAtTimestamp,
                    sizeInBytes: null,
                    mimeType: null,
                    extension: null,
                    webUrl: rg.webUrl,
                    hasChildren: has_child_rgs OR has_records
                }}
                RETURN UNION([rg_node], rg_descendants)
        )

        // For apps, also get records directly (not just through recordGroups)
        // This matches the global search behavior where records are collected directly
        LET direct_app_records = STARTS_WITH(@parent_doc_id, "apps/") ? (
            // Path 1: Direct user -> record permission
            LET direct_records = (
                FOR perm IN permission
                    FILTER perm._from == user_from AND perm.type == "USER"
                    FILTER STARTS_WITH(perm._to, "records/")
                    LET record = DOCUMENT(perm._to)
                    FILTER record != null AND record.isDeleted != true AND record.orgId == @org_id
                    FILTER record.connectorId == @parent_id_for_rg
                    RETURN record
            )
            // Path 2: User -> Group -> record permission
            LET group_records = (
                FOR group, userEdge IN 1..1 ANY user_from permission
                    FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                    FOR perm IN permission
                        FILTER perm._from == group._id AND perm._to LIKE "records/%"
                        FILTER perm.type == "GROUP" OR perm.type == "ROLE"
                        LET record = DOCUMENT(perm._to)
                        FILTER record != null AND record.isDeleted != true AND record.orgId == @org_id
                        FILTER record.connectorId == @parent_id_for_rg
                        RETURN record
            )
            // Path 3: User -> RecordGroup (via inheritPermissions) -> record
            LET user_rg_records = (
                FOR recordGroup, userToRgEdge IN 1..1 ANY user_from permission
                    FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                    FILTER recordGroup.connectorId == @parent_id_for_rg
                    FOR record, edge, path IN 0..5 INBOUND recordGroup._id inheritPermissions
                        FILTER IS_SAME_COLLECTION("records", record)
                        FILTER record != null AND record.isDeleted != true AND record.orgId == @org_id
                        RETURN record
            )
            // Path 4: User -> Group -> RecordGroup (via inheritPermissions) -> record
            LET group_rg_records = (
                FOR group, userEdge IN 1..1 ANY user_from permission
                    FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                    FOR recordGroup, groupToRgEdge IN 1..1 ANY group._id permission
                        FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                        FILTER recordGroup.connectorId == @parent_id_for_rg
                        FOR record, edge, path IN 0..5 INBOUND recordGroup._id inheritPermissions
                            FILTER IS_SAME_COLLECTION("records", record)
                            FILTER record != null AND record.isDeleted != true AND record.orgId == @org_id
                            RETURN record
            )
            // Path 5: User -> Org -> record permission
            LET org_records = (
                FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                    FILTER belongsEdge.entityType == "ORGANIZATION"
                    FOR perm IN permission
                        FILTER perm._from == org._id AND perm._to LIKE "records/%"
                        FILTER perm.type == "ORG"
                        LET record = DOCUMENT(perm._to)
                        FILTER record != null AND record.isDeleted != true AND record.orgId == @org_id
                        FILTER record.connectorId == @parent_id_for_rg
                        RETURN record
            )
            // Path 6: User -> Org -> RecordGroup (via inheritPermissions) -> record
            LET org_rg_records = (
                FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                    FILTER belongsEdge.entityType == "ORGANIZATION"
                    FOR recordGroup, orgPerm IN 1..1 ANY org._id permission
                        FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                        FILTER recordGroup.connectorId == @parent_id_for_rg
                        FOR record, edge, path IN 0..5 INBOUND recordGroup._id inheritPermissions
                            FILTER IS_SAME_COLLECTION("records", record)
                            FILTER record != null AND record.isDeleted != true AND record.orgId == @org_id
                            RETURN record
            )
            LET all_direct_records = UNION_DISTINCT(direct_records, group_records, user_rg_records, group_rg_records, org_records, org_rg_records)
            FOR record IN all_direct_records
                LET file_info = FIRST(FOR fe IN isOfType FILTER fe._from == record._id LET f = DOCUMENT(fe._to) RETURN f)
                LET is_folder = file_info != null AND file_info.isFile == false
                LET has_children = LENGTH(
                    FOR ce IN recordRelations
                        FILTER ce._from == record._id AND ce.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                        LET c = DOCUMENT(ce._to)
                        FILTER c != null AND c.isDeleted != true
                        RETURN 1
                ) > 0
                RETURN {{
                    id: record._key, name: record.recordName, nodeType: is_folder ? "folder" : "record",
                    parentId: record.parentId, source: "CONNECTOR",
                    connector: record.connectorName, recordType: record.recordType,
                    recordGroupType: null, indexingStatus: record.indexingStatus,
                    createdAt: record.createdAtTimestamp, updatedAt: record.updatedAtTimestamp,
                    sizeInBytes: file_info ? file_info.fileSizeInBytes : null,
                    mimeType: record.mimeType, webUrl: record.webUrl,
                    hasChildren: has_children,
                    connectorId: record.connectorId, kbId: null,
                    previewRenderable: record.previewRenderable != null ? record.previewRenderable : true
                }}
        ) : []

        // Flatten all_descendants since App traversal may return nested arrays
        LET flat_descendants = FLATTEN(all_descendants, 2)
        LET all_nodes_union = UNION(flat_descendants, FLATTEN(nested_record_groups, 2), direct_app_records)

        // Deduplicate by ID - keep the first occurrence of each record
        // This prevents duplicates when the same record appears in multiple query paths
        LET all_nodes = (
            FOR node IN all_nodes_union
                COLLECT id = node.id INTO groups
                RETURN groups[0].node
        )

        // Apply search and other filters
        LET filtered_nodes = (
            FOR node IN all_nodes
                {search_filter}
                FILTER {filter_clause if filter_clause else "true"}
                // Include all container types (app, kb, recordGroup, folder) even if empty
                FILTER @only_containers == false OR node.hasChildren == true OR node.nodeType IN ["app", "kb", "recordGroup", "folder"]
                RETURN node
        )

        LET sorted_nodes = (FOR node IN filtered_nodes SORT node[@sort_field] @sort_dir RETURN node)
        LET total_count = LENGTH(sorted_nodes)
        LET paginated_nodes = SLICE(sorted_nodes, @skip, @limit)

        RETURN {{ nodes: paginated_nodes, total: total_count }}
        """

        all_bind_vars = {
            "parent_doc_id": parent_doc_id,
            "org_id": org_id,
            "skip": skip,
            "limit": limit,
            "sort_field": sort_field,
            "sort_dir": sort_dir,
            "only_containers": only_containers,
            **bind_vars,
        }

        result = await self.http_client.execute_aql(query, bind_vars=all_bind_vars, txn_id=transaction)
        return result[0] if result else {"nodes": [], "total": 0}

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
        Search across all nodes with filters.

        TODO(Performance): This query currently fetches ALL matching documents from KBs, Apps, and Records,
        unions them, and THEN sorts and slices. This is O(N) where N is total matches, which is not scalable.
        OPTIMIZATION: Push down the SORT and LIMIT into the individual sub-queries (kb_nodes, app_nodes, record_nodes).
        Calculate `union_limit = skip + limit` and apply `LIMIT union_limit` to each sub-query to drastically
        reduce the number of documents merged and sorted in memory.
        """
        # Build filter conditions
        filters = []
        bind_vars = {
            "org_id": org_id, "user_key": user_key, "user_apps_ids": user_app_ids,
            "skip": skip, "limit": limit, "sort_field": sort_field, "sort_dir": sort_dir,
        }

        if search_query:
            bind_vars["search_query"] = search_query.lower()
            filters.append("FILTER LOWER(node.name) LIKE CONCAT('%', @search_query, '%')")
        if node_types:
            bind_vars["node_types"] = node_types
            filters.append("FILTER node.nodeType IN @node_types")
        if record_types:
            bind_vars["record_types"] = record_types
            filters.append("FILTER node.recordType != null AND node.recordType IN @record_types")
        if origins:
            bind_vars["origins"] = origins
            filters.append("FILTER node.source IN @origins")
        # Handle connector_ids and kb_ids with OR logic when both are provided
        # This ensures we get results from both connectors AND knowledge bases (union)
        if connector_ids and kb_ids:
            bind_vars["connector_ids"] = connector_ids
            bind_vars["kb_ids"] = kb_ids
            # Filter Apps/KBs by ID OR Records by connectorId/kbId
            filters.append(
                "FILTER ((node.nodeType == 'app' AND node.id IN @connector_ids) OR (node.connectorId IN @connector_ids) OR "
                "(node.nodeType == 'kb' AND node.id IN @kb_ids) OR (node.kbId IN @kb_ids))"
            )
        elif connector_ids:
            bind_vars["connector_ids"] = connector_ids
            # Filter Apps by ID OR Records by connectorId
            filters.append("FILTER (node.nodeType == 'app' AND node.id IN @connector_ids) OR (node.connectorId IN @connector_ids)")
        elif kb_ids:
            bind_vars["kb_ids"] = kb_ids
            filters.append("FILTER (node.nodeType == 'kb' AND node.id IN @kb_ids) OR (node.kbId IN @kb_ids)")
        if indexing_status:
            bind_vars["indexing_status"] = indexing_status
            filters.append("FILTER node.indexingStatus != null AND node.indexingStatus IN @indexing_status")
        if created_at:
            if created_at.get("gte"):
                bind_vars["created_at_gte"] = created_at["gte"]
                filters.append("FILTER node.createdAt >= @created_at_gte")
            if created_at.get("lte"):
                bind_vars["created_at_lte"] = created_at["lte"]
                filters.append("FILTER node.createdAt <= @created_at_lte")
        if updated_at:
            if updated_at.get("gte"):
                bind_vars["updated_at_gte"] = updated_at["gte"]
                filters.append("FILTER node.updatedAt >= @updated_at_gte")
            if updated_at.get("lte"):
                bind_vars["updated_at_lte"] = updated_at["lte"]
                filters.append("FILTER node.updatedAt <= @updated_at_lte")
        if size:
            if size.get("gte"):
                bind_vars["size_gte"] = size["gte"]
                filters.append("FILTER node.sizeInBytes != null AND node.sizeInBytes >= @size_gte")
            if size.get("lte"):
                bind_vars["size_lte"] = size["lte"]
                filters.append("FILTER node.sizeInBytes != null AND node.sizeInBytes <= @size_lte")
        if only_containers:
            filters.append("FILTER node.hasChildren == true")

        filter_clause = "\n                    ".join(filters) if filters else ""

        query = f"""
        LET user_from = CONCAT("users/", @user_key)

        // ==================== KB NODES (with team permission) ====================
        LET direct_kb_access = (
            FOR perm IN permission
                FILTER perm._from == user_from
                FILTER perm.type == "USER"
                FILTER STARTS_WITH(perm._to, "recordGroups/")
                LET kb = DOCUMENT(perm._to)
                FILTER kb != null AND kb.orgId == @org_id AND kb.connectorName == "KB"
                RETURN kb
        )

        LET team_kb_access = (
            FOR teamPerm IN permission
                FILTER teamPerm.type == "TEAM"
                FILTER STARTS_WITH(teamPerm._to, "recordGroups/")
                LET kb = DOCUMENT(teamPerm._to)
                FILTER kb != null AND kb.orgId == @org_id AND kb.connectorName == "KB"
                LET team_id = SPLIT(teamPerm._from, "/")[1]
                LET is_member = LENGTH(
                    FOR userPerm IN permission
                        FILTER userPerm._from == user_from
                        FILTER userPerm._to == CONCAT("teams/", team_id)
                        RETURN 1
                ) > 0
                FILTER is_member
                RETURN kb
        )

        LET accessible_kbs = UNION_DISTINCT(direct_kb_access, team_kb_access)

        LET kb_nodes = (
            FOR kb IN accessible_kbs
                LET has_record_children = LENGTH(
                    FOR edge IN recordRelations
                        FILTER edge._from == kb._id AND edge.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                        LET child = DOCUMENT(edge._to)
                        FILTER child != null AND child.isDeleted != true
                        RETURN 1
                ) > 0
                LET has_nested_rgs = LENGTH(
                    FOR child_rg IN recordGroups
                        FILTER child_rg.parentId == kb._key AND child_rg.isDeleted != true
                        RETURN 1
                ) > 0
                LET has_children = has_record_children OR has_nested_rgs
                RETURN {{
                    id: kb._key, name: kb.groupName, nodeType: "kb",
                    parentId: null, source: "KB", connector: "KB", recordType: null,
                    recordGroupType: null, indexingStatus: null, createdAt: kb.createdAtTimestamp, updatedAt: kb.updatedAtTimestamp,
                    sizeInBytes: null, webUrl: CONCAT("/kb/", kb._key), hasChildren: has_children,
                    connectorId: null, kbId: kb._key
                }}
        )

        // ==================== APP NODES ====================
        LET app_nodes = (
            FOR app IN apps
                FILTER app.orgId == @org_id AND app._key IN @user_apps_ids
                FILTER app.type != "KB"  // Exclude KB app
                LET has_children = LENGTH(
                    FOR rg IN recordGroups FILTER rg.connectorId == app._key RETURN 1
                ) > 0
                RETURN {{
                    id: app._key, name: app.name, nodeType: "app",
                    parentId: null, source: "CONNECTOR", connector: app.type, recordType: null,
                    recordGroupType: null, indexingStatus: null, createdAt: app.createdAtTimestamp || 0, updatedAt: app.updatedAtTimestamp || 0,
                    sizeInBytes: null, webUrl: CONCAT("/app/", app._key), hasChildren: has_children,
                    connectorId: app._key, kbId: null
                }}
        )

        // ==================== KB RECORD NODES (with team permission) ====================
        LET kb_record_nodes = (
            FOR kb IN accessible_kbs
                FOR edge IN belongsTo
                    FILTER edge._to == kb._id AND STARTS_WITH(edge._from, "records/")
                    LET record = DOCUMENT(edge._from)
                    FILTER record != null AND record.isDeleted != true AND record.orgId == @org_id
                    // Only direct children: externalParentId must be null
                    FILTER record.externalParentId == null
                    LET file_info = FIRST(
                        FOR file_edge IN isOfType FILTER file_edge._from == record._id
                        LET file = DOCUMENT(file_edge._to) RETURN file
                    )
                    LET is_folder = file_info != null AND file_info.isFile == false
                    LET has_children = LENGTH(
                        FOR child_edge IN recordRelations
                            FILTER child_edge._from == record._id AND child_edge.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                            LET child = DOCUMENT(child_edge._to)
                            FILTER child != null AND child.isDeleted != true
                            RETURN 1
                    ) > 0
                    RETURN {{
                        id: record._key, name: record.recordName, nodeType: is_folder ? "folder" : "record",
                        parentId: record.parentId, source: "KB",
                        connector: "KB", recordType: record.recordType,
                        recordGroupType: null, indexingStatus: record.indexingStatus, createdAt: record.createdAtTimestamp,
                        updatedAt: record.updatedAtTimestamp, sizeInBytes: record.sizeInBytes != null ? record.sizeInBytes : file_info.fileSizeInBytes,
                        mimeType: record.mimeType, webUrl: record.webUrl, hasChildren: has_children,
                        connectorId: null, kbId: kb._key
                    }}
        )

        // ==================== CONNECTOR RECORD NODES (path-based permission) ====================
        // Path 1: Direct user -> record permission
        LET direct_records = (
            FOR perm IN permission
                FILTER perm._from == user_from AND perm.type == "USER"
                FILTER STARTS_WITH(perm._to, "records/")
                LET record = DOCUMENT(perm._to)
                FILTER record != null AND record.isDeleted != true AND record.orgId == @org_id
                FILTER record.origin == "CONNECTOR"
                FILTER record.connectorId IN @user_apps_ids
                RETURN record
        )

        // Path 2: User -> Group -> record permission
        LET group_records = (
            FOR group, userEdge IN 1..1 ANY user_from permission
                FILTER userEdge.type == "USER"
                FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                FOR record, groupEdge IN 1..1 ANY group._id permission
                    FILTER groupEdge.type == "GROUP" OR groupEdge.type == "ROLE"
                    FILTER IS_SAME_COLLECTION("records", record)
                    FILTER record != null AND record.isDeleted != true AND record.orgId == @org_id
                    FILTER record.origin == "CONNECTOR"
                    FILTER record.connectorId IN @user_apps_ids
                    RETURN record
        )

        // Path 3: User -> RecordGroup -> record (via inheritPermissions edge iteration)
        LET user_rg_records = (
            FOR recordGroup, userEdge IN 1..1 ANY user_from permission
                FILTER userEdge.type == "USER"
                FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                FILTER recordGroup.connectorName != "KB"
                FOR inheritEdge IN inheritPermissions
                    FILTER inheritEdge._to == recordGroup._id
                    LET record = DOCUMENT(inheritEdge._from)
                    FILTER record != null AND record.isDeleted != true AND record.orgId == @org_id
                    FILTER record.origin == "CONNECTOR"
                    FILTER record.connectorId IN @user_apps_ids
                    RETURN record
        )

        // Path 4: User -> Group -> RecordGroup -> record (via inheritPermissions edge iteration)
        LET group_rg_records = (
            FOR group, userEdge IN 1..1 ANY user_from permission
                FILTER userEdge.type == "USER"
                FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                FOR recordGroup, groupEdge IN 1..1 ANY group._id permission
                    FILTER groupEdge.type == "GROUP" OR groupEdge.type == "ROLE"
                    FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                    FILTER recordGroup.connectorName != "KB"
                    FOR inheritEdge IN inheritPermissions
                        FILTER inheritEdge._to == recordGroup._id
                        LET record = DOCUMENT(inheritEdge._from)
                        FILTER record != null AND record.isDeleted != true AND record.orgId == @org_id
                        FILTER record.origin == "CONNECTOR"
                        FILTER record.connectorId IN @user_apps_ids
                        RETURN record
        )

        // Path 5: User -> Org -> record permission
        LET org_records = (
            FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                FILTER belongsEdge.entityType == "ORGANIZATION"
                FOR record, orgPerm IN 1..1 ANY org._id permission
                    FILTER orgPerm.type == "ORG"
                    FILTER IS_SAME_COLLECTION("records", record)
                    FILTER record != null AND record.isDeleted != true AND record.orgId == @org_id
                    FILTER record.origin == "CONNECTOR"
                    FILTER record.connectorId IN @user_apps_ids
                    RETURN record
        )

        // Path 6: User -> Org -> RecordGroup -> record (via inheritPermissions edge iteration)
        LET org_rg_records = (
            FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                FILTER belongsEdge.entityType == "ORGANIZATION"
                FOR recordGroup, orgPerm IN 1..1 ANY org._id permission
                    FILTER orgPerm.type == "ORG"
                    FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                    FILTER recordGroup.connectorName != "KB"
                    FOR inheritEdge IN inheritPermissions
                        FILTER inheritEdge._to == recordGroup._id
                        LET record = DOCUMENT(inheritEdge._from)
                        FILTER record != null AND record.isDeleted != true AND record.orgId == @org_id
                        FILTER record.origin == "CONNECTOR"
                        FILTER record.connectorId IN @user_apps_ids
                        RETURN record
        )

        LET all_connector_records = UNION_DISTINCT(direct_records, group_records, user_rg_records, group_rg_records, org_records, org_rg_records)

        LET connector_record_nodes = (
            FOR record IN all_connector_records
                LET connector = DOCUMENT(CONCAT("apps/", record.connectorId))
                LET file_info = FIRST(
                    FOR file_edge IN isOfType FILTER file_edge._from == record._id
                    LET file = DOCUMENT(file_edge._to) RETURN file
                )
                LET is_folder = file_info != null AND file_info.isFile == false
                LET has_children = LENGTH(
                    FOR child_edge IN recordRelations
                        FILTER child_edge._from == record._id AND child_edge.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                        LET child = DOCUMENT(child_edge._to)
                        FILTER child != null AND child.isDeleted != true
                        RETURN 1
                ) > 0
                RETURN {{
                    id: record._key, name: record.recordName, nodeType: is_folder ? "folder" : "record",
                    parentId: record.parentId, source: "CONNECTOR",
                    connector: record.connectorName, recordType: record.recordType,
                    recordGroupType: null, indexingStatus: record.indexingStatus, createdAt: record.createdAtTimestamp,
                    updatedAt: record.updatedAtTimestamp, sizeInBytes: record.sizeInBytes != null ? record.sizeInBytes : file_info.fileSizeInBytes,
                    mimeType: record.mimeType, webUrl: record.webUrl, hasChildren: has_children,
                    connectorId: record.connectorId, kbId: null
                }}
        )

        // ==================== RECORD GROUP NODES (with permission checks) ====================
        // Path 1: Direct user -> recordGroup permission
        LET direct_rg = (
            FOR perm IN permission
                FILTER perm._from == user_from AND perm.type == "USER"
                FILTER STARTS_WITH(perm._to, "recordGroups/")
                LET rg = DOCUMENT(perm._to)
                FILTER rg != null AND rg.orgId == @org_id AND rg.connectorName != "KB" AND rg.isDeleted != true
                RETURN rg
        )

        // Path 2: User -> Group -> recordGroup permission (matches check_record_access_with_details pattern)
        LET group_rg = (
            FOR group, userEdge IN 1..1 ANY user_from permission
                FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                FOR rg, groupEdge IN 1..1 ANY group._id permission
                    FILTER groupEdge.type == "GROUP" OR groupEdge.type == "ROLE"
                    FILTER IS_SAME_COLLECTION("recordGroups", rg)
                    FILTER rg != null AND rg.orgId == @org_id AND rg.connectorName != "KB" AND rg.isDeleted != true
                    RETURN rg
        )

        // Path 3: User -> Org -> recordGroup permission
        LET org_rg = (
            FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                FILTER belongsEdge.entityType == "ORGANIZATION"
                FOR rg, orgPerm IN 1..1 ANY org._id permission
                    FILTER orgPerm.type == "ORG"
                    FILTER IS_SAME_COLLECTION("recordGroups", rg)
                    FILTER rg != null AND rg.orgId == @org_id AND rg.connectorName != "KB" AND rg.isDeleted != true
                    RETURN rg
        )

        LET all_record_groups = UNION_DISTINCT(direct_rg, group_rg, org_rg)

        LET record_group_nodes = (
            FOR rg IN all_record_groups
                // Check if user has permission to see nested record groups
                // For KB: use belongsTo edges, for Connector: use belongsTo edges or parentExternalGroupId
                LET has_child_rgs = rg.connectorName == "KB" ? (
                    // KB: Use belongsTo edges
                    LENGTH(
                        FOR edge IN belongsTo
                            FILTER edge._to == rg._id AND STARTS_WITH(edge._from, "recordGroups/")
                            LET child_rg = DOCUMENT(edge._from)
                            FILTER child_rg != null AND child_rg.connectorName == "KB" AND child_rg.isDeleted != true
                            // Check permission for nested record groups
                            LET child_has_direct = LENGTH(FOR perm IN permission FILTER perm._from == user_from AND perm._to == child_rg._id AND perm.type == "USER" RETURN 1) > 0
                            LET child_has_group = LENGTH(
                                FOR group, userEdge IN 1..1 ANY user_from permission
                                    FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                                    FOR perm IN permission FILTER perm._from == group._id AND perm._to == child_rg._id FILTER perm.type == "GROUP" OR perm.type == "ROLE" RETURN 1
                            ) > 0
                            LET child_has_org = LENGTH(
                                FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                                    FILTER belongsEdge.entityType == "ORGANIZATION"
                                    FOR perm IN permission FILTER perm._from == org._id AND perm._to == child_rg._id FILTER perm.type == "ORG" RETURN 1
                            ) > 0
                            FILTER child_has_direct OR child_has_group OR child_has_org
                            RETURN 1
                    ) > 0
                ) : (
                    LENGTH(
                        FOR child_rg IN recordGroups
                            FILTER child_rg.isDeleted != true
                            FILTER (
                                // Option 1: Connected via belongsTo edge (child_rg -> belongsTo -> rg)
                                LENGTH(FOR edge IN belongsTo FILTER edge._from == child_rg._id AND edge._to == rg._id RETURN 1) > 0
                                OR
                                // Option 2: Connected via parentExternalGroupId field
                                (child_rg.parentExternalGroupId != null AND child_rg.parentExternalGroupId == rg.externalGroupId)
                            )
                            // Check permission for nested record groups
                            LET child_has_direct = LENGTH(FOR perm IN permission FILTER perm._from == user_from AND perm._to == child_rg._id AND perm.type == "USER" RETURN 1) > 0
                            LET child_has_group = LENGTH(
                                FOR group, userEdge IN 1..1 ANY user_from permission
                                    FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                                    FOR perm IN permission FILTER perm._from == group._id AND perm._to == child_rg._id FILTER perm.type == "GROUP" OR perm.type == "ROLE" RETURN 1
                            ) > 0
                            LET child_has_org = LENGTH(
                                FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                                    FILTER belongsEdge.entityType == "ORGANIZATION"
                                    FOR perm IN permission FILTER perm._from == org._id AND perm._to == child_rg._id FILTER perm.type == "ORG" RETURN 1
                            ) > 0
                            FILTER child_has_direct OR child_has_group OR child_has_org
                            RETURN 1
                    ) > 0
                )
                // Check if user has permission to see records
                // Find records using belongsTo edges, recordGroupId field, or inheritPermissions edges
                // Note: For hasChildren, we check ALL records (including nested ones), not just direct children
                LET all_potential_records = rg.connectorName == "KB" ? (
                    // KB: Use belongsTo edges only
                    // Only direct children: externalParentId must be null
                    FOR edge IN belongsTo
                        FILTER edge._from LIKE "records/%" AND edge._to == rg._id
                        LET rec = DOCUMENT(edge._from)
                        FILTER rec != null AND rec.isDeleted != true
                        FILTER rec.externalParentId == null
                        RETURN rec._id
                ) : (
                    // Connector: Existing UNION_DISTINCT logic
                    UNION_DISTINCT(
                        // Method 1: belongsTo edges
                        FOR edge IN belongsTo
                            FILTER edge._from LIKE "records/%" AND edge._to == rg._id
                            LET rec = DOCUMENT(edge._from)
                            FILTER rec != null AND rec.isDeleted != true
                            RETURN rec._id
                        ,
                        // Method 2: recordGroupId field
                        FOR rec IN records
                            FILTER rec.recordGroupId == rg._key
                            FILTER rec != null AND rec.isDeleted != true
                            RETURN rec._id
                        ,
                        // Method 3: inheritPermissions edges
                        FOR edge IN inheritPermissions
                            FILTER edge._from LIKE "records/%" AND edge._to == rg._id
                            LET rec = DOCUMENT(edge._from)
                            FILTER rec != null AND rec.isDeleted != true
                            RETURN rec._id
                    )
                )
                LET has_records = LENGTH(
                    FOR rec_id IN all_potential_records
                        LET rec = DOCUMENT(rec_id)
                        FILTER rec != null
                        // Check if user has permission to this record
                        // Path 1: Direct user -> record permission
                        LET rec_has_direct = LENGTH(FOR perm IN permission FILTER perm._from == user_from AND perm._to == rec._id AND perm.type == "USER" RETURN 1) > 0
                        // Path 2: User -> group -> record permission
                        LET rec_has_group = LENGTH(
                            FOR group, userEdge IN 1..1 ANY user_from permission
                                FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                                FOR perm IN permission FILTER perm._from == group._id AND perm._to == rec._id FILTER perm.type == "GROUP" OR perm.type == "ROLE" RETURN 1
                        ) > 0
                        // Path 3: User -> org -> record permission
                        LET rec_has_org = LENGTH(
                            FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                                FILTER belongsEdge.entityType == "ORGANIZATION"
                                FOR perm IN permission FILTER perm._from == org._id AND perm._to == rec._id FILTER perm.type == "ORG" RETURN 1
                        ) > 0
                        // Path 4: User -> recordGroup (via inheritPermissions) -> record
                        LET rec_has_inherit_rg = LENGTH(
                            FOR recordGroup, userToRgEdge IN 1..1 ANY user_from permission
                                FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                                FOR inheritEdge IN inheritPermissions
                                    FILTER inheritEdge._to == recordGroup._id AND inheritEdge._from == rec._id
                                    RETURN 1
                        ) > 0
                        // Path 5: User -> group -> recordGroup (via inheritPermissions) -> record
                        LET rec_has_group_rg = LENGTH(
                            FOR group, userEdge IN 1..1 ANY user_from permission
                                FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                                FOR recordGroup, groupToRgEdge IN 1..1 ANY group._id permission
                                    FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                                    FOR inheritEdge IN inheritPermissions
                                        FILTER inheritEdge._to == recordGroup._id AND inheritEdge._from == rec._id
                                        RETURN 1
                        ) > 0
                        // Path 6: User -> org -> recordGroup (via inheritPermissions) -> record
                        LET rec_has_org_rg = LENGTH(
                            FOR org, belongsEdge IN 1..1 ANY user_from belongsTo
                                FILTER belongsEdge.entityType == "ORGANIZATION"
                                FOR recordGroup, orgPerm IN 1..1 ANY org._id permission
                                    FILTER orgPerm.type == "ORG"
                                    FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)
                                    FOR inheritEdge IN inheritPermissions
                                        FILTER inheritEdge._to == recordGroup._id AND inheritEdge._from == rec._id
                                        RETURN 1
                        ) > 0
                        FILTER rec_has_direct OR rec_has_group OR rec_has_org OR rec_has_inherit_rg OR rec_has_group_rg OR rec_has_org_rg
                        RETURN 1
                ) > 0
                LET has_children = has_child_rgs OR has_records
                RETURN {{
                    id: rg._key, name: rg.groupName, nodeType: "recordGroup",
                    parentId: rg.parentId != null ? CONCAT("recordGroups/", rg.parentId) : CONCAT("apps/", rg.connectorId),
                    source: "CONNECTOR", connector: rg.connectorName, recordType: null,
                    recordGroupType: rg.groupType, indexingStatus: null, createdAt: rg.createdAtTimestamp, updatedAt: rg.updatedAtTimestamp,
                    sizeInBytes: null, webUrl: rg.webUrl, hasChildren: has_children,
                    connectorId: rg.connectorId, kbId: null
                }}
        )

        // ==================== COMBINE ALL NODES ====================
        LET all_nodes = UNION(kb_nodes, app_nodes, kb_record_nodes, connector_record_nodes, record_group_nodes)
        LET filtered_nodes = (
            FOR node IN all_nodes
                {filter_clause}
                RETURN node
        )
        LET sorted_nodes = (FOR node IN filtered_nodes SORT node[@sort_field] @sort_dir RETURN node)
        LET total_count = LENGTH(sorted_nodes)
        LET paginated_nodes = SLICE(sorted_nodes, @skip, @limit)

        RETURN {{ nodes: paginated_nodes, total: total_count }}
        """

        result = await self.http_client.execute_aql(query, bind_vars=bind_vars, txn_id=transaction)
        return result[0] if result else {"nodes": [], "total": 0}

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

        # Separate by type
        record_group_ids = [nid for nid, ntype in zip(node_ids, node_types) if ntype in ['kb', 'recordGroup']]
        app_ids = [nid for nid, ntype in zip(node_ids, node_types) if ntype == 'app']
        record_ids = [nid for nid, ntype in zip(node_ids, node_types) if ntype not in ['kb', 'recordGroup', 'app']]

        permissions = {}

        # Query record group permissions
        if record_group_ids:
            query = """
            LET user_from = CONCAT('users/', @user_key)
            LET role_priority = {
                "OWNER": 6,
                "ADMIN": 5,
                "EDITOR": 4,
                "WRITER": 3,
                "COMMENTER": 2,
                "READER": 1
            }
            FOR rg_id IN @record_group_ids
                LET rg_doc = DOCUMENT(CONCAT('recordGroups/', rg_id))
                LET rg_to = CONCAT('recordGroups/', rg_id)

                // Check if this is a root KB
                LET is_kb = rg_doc != null AND rg_doc.connectorName == "KB"

                // Check if this record group is nested under a KB (for fallback permission)
                LET is_nested_under_kb = is_kb ? false : (
                    // Check if traversing up finds a KB
                    LENGTH(
                        FOR v, e, p IN 0..10 INBOUND CONCAT("recordGroups/", rg_doc._key) belongsTo
                            FILTER v != null AND v.connectorName == "KB"
                            RETURN 1
                    ) > 0
                )

                // Priority 1: Direct user permission on record group
                LET direct_perm = FIRST(
                    FOR perm IN permission
                        FILTER perm._from == user_from AND perm._to == rg_to AND perm.type == "USER"
                        FILTER perm.role != null AND perm.role != ""
                        RETURN {
                            role: perm.role,
                            priority: role_priority[perm.role] || 1
                        }
                )

                // Priority 2: Team permissions on record group
                LET team_perms = (
                    FOR user_team_perm IN permission
                        FILTER user_team_perm._from == user_from
                        FILTER user_team_perm.type == "USER"
                        FILTER STARTS_WITH(user_team_perm._to, "teams/")
                        FILTER user_team_perm.role != null AND user_team_perm.role != ""
                        FOR team_rg_perm IN permission
                            FILTER team_rg_perm._from == user_team_perm._to
                            FILTER team_rg_perm._to == rg_to
                            FILTER team_rg_perm.type == "TEAM"
                            RETURN {
                                role: user_team_perm.role,
                                priority: role_priority[user_team_perm.role] || 1
                            }
                )

                // Priority 3: Group permissions on record group
                LET group_perms = (
                    FOR rg_group_perm IN permission
                        FILTER rg_group_perm._to == rg_to AND (rg_group_perm.type == "GROUP" OR rg_group_perm.type == "ROLE")
                        FILTER rg_group_perm.role != null AND rg_group_perm.role != ""
                        LET group_to = rg_group_perm._from
                        FOR user_group_perm IN permission
                            FILTER user_group_perm._from == user_from AND user_group_perm._to == group_to
                            RETURN {
                                role: rg_group_perm.role,
                                priority: role_priority[rg_group_perm.role] || 1
                            }
                )

                // Priority 4: For nested KB record groups, find root KB and check permission
                // Compute root KB unconditionally (will be null for non-nested or root KBs)
                LET root_kb_from_traversal = (rg_doc != null AND NOT is_kb) ? (
                    FOR v, e, p IN 0..10 INBOUND CONCAT("recordGroups/", rg_doc._key) belongsTo
                        FILTER v != null AND v.connectorName == "KB"
                        LIMIT 1
                        RETURN v
                ) : []
                LET root_kb = LENGTH(root_kb_from_traversal) > 0 ? root_kb_from_traversal[0] : null
                LET root_kb_to = root_kb != null ? CONCAT("recordGroups/", root_kb._key) : null

                // Check direct user permission on root KB
                LET root_kb_direct = root_kb_to != null ? FIRST(
                    FOR perm IN permission
                        FILTER perm._from == user_from AND perm._to == root_kb_to AND perm.type == "USER"
                        FILTER perm.role != null AND perm.role != ""
                        RETURN {
                            role: perm.role,
                            priority: role_priority[perm.role] || 1
                        }
                ) : null

                // Check team permissions on root KB
                LET root_kb_team = root_kb_to != null ? FIRST(
                    FOR user_team_perm IN permission
                        FILTER user_team_perm._from == user_from
                        FILTER user_team_perm.type == "USER"
                        FILTER STARTS_WITH(user_team_perm._to, "teams/")
                        FOR team_kb_perm IN permission
                            FILTER team_kb_perm._from == user_team_perm._to
                            FILTER team_kb_perm._to == root_kb_to
                            FILTER team_kb_perm.type == "TEAM"
                            FILTER team_kb_perm.role != null AND team_kb_perm.role != ""
                            RETURN {
                                role: team_kb_perm.role,
                                priority: role_priority[team_kb_perm.role] || 1
                            }
                ) : null

                // Check group permissions on root KB
                LET root_kb_group = root_kb_to != null ? FIRST(
                    FOR kb_group_perm IN permission
                        FILTER kb_group_perm._to == root_kb_to AND (kb_group_perm.type == "GROUP" OR kb_group_perm.type == "ROLE")
                        FILTER kb_group_perm.role != null AND kb_group_perm.role != ""
                        LET group_to = kb_group_perm._from
                        FOR user_group_perm IN permission
                            FILTER user_group_perm._from == user_from AND user_group_perm._to == group_to
                            RETURN {
                                role: kb_group_perm.role,
                                priority: role_priority[kb_group_perm.role] || 1
                            }
                ) : null

                // Get highest priority root KB permission (only for nested KB record groups)
                LET all_root_kb_perms = REMOVE_VALUE(
                    FLATTEN([
                        root_kb_direct != null ? [root_kb_direct] : [],
                        root_kb_team != null ? [root_kb_team] : [],
                        root_kb_group != null ? [root_kb_group] : []
                    ]),
                    null
                )
                LET root_kb_perm = (is_nested_under_kb AND LENGTH(all_root_kb_perms) > 0) ? (
                    FIRST(
                        FOR p IN all_root_kb_perms
                            SORT p.priority DESC
                            LIMIT 1
                            RETURN p
                    )
                ) : null

                // Combine all permissions and get highest priority role
                LET all_perms = REMOVE_VALUE(
                    FLATTEN([
                        direct_perm != null ? [direct_perm] : [],
                        team_perms,
                        group_perms,
                        root_kb_perm != null ? [root_kb_perm] : []
                    ]),
                    null
                )
                LET highest_perm = LENGTH(all_perms) > 0 ? (
                    FIRST(
                        FOR p IN all_perms
                            SORT p.priority DESC
                            LIMIT 1
                            RETURN p
                    )
                ) : null
                // Only default to READER if NO permissions were found
                LET role = highest_perm != null ? highest_perm.role : "READER"
                RETURN { id: rg_id, role: role }
            """
            results = await self.http_client.execute_aql(query, bind_vars={"user_key": user_key, "record_group_ids": record_group_ids}, txn_id=transaction)
            for r in (results or []):
                role = r.get('role') or 'READER'  # Ensure role is never None
                permissions[r['id']] = {"role": role, "canEdit": role in ['OWNER', 'WRITER', 'ADMIN', 'EDITOR'], "canDelete": role in ['OWNER', 'ADMIN']}

        # Apps - generally read-only
        for app_id in app_ids:
            permissions[app_id] = {"role": "READER", "canEdit": False, "canDelete": False}

        # Query record permissions
        if record_ids:
            query = """
            LET user_from = CONCAT('users/', @user_key)
            LET role_priority = {
                "OWNER": 6,
                "ADMIN": 5,
                "EDITOR": 4,
                "WRITER": 3,
                "COMMENTER": 2,
                "READER": 1
            }
            FOR rec_id IN @record_ids
                LET record_doc = DOCUMENT(CONCAT('records/', rec_id))
                LET record_from = CONCAT('records/', rec_id)

                // Determine if record belongs to KB (either directly or through nested record groups)
                LET record_connector = record_doc != null ? (
                    DOCUMENT(CONCAT("recordGroups/", record_doc.connectorId)) ||
                    DOCUMENT(CONCAT("apps/", record_doc.connectorId))
                ) : null
                LET is_direct_kb = record_connector != null AND record_connector.connectorName == "KB"
                LET is_nested_under_kb = is_direct_kb ? false : (
                    record_connector != null ? (
                        // Check if traversing up from connector finds a KB
                        LENGTH(
                            FOR v, e, p IN 0..10 INBOUND CONCAT("recordGroups/", record_connector._key) belongsTo
                                FILTER v != null AND v.connectorName == "KB"
                                RETURN 1
                        ) > 0
                    ) : false
                )
                LET is_kb_record = is_direct_kb OR is_nested_under_kb

                // Priority 1: Direct user permission on record
                LET direct_perm = FIRST(
                    FOR perm IN permission
                        FILTER perm._from == user_from AND perm._to == record_from AND perm.type == "USER"
                        FILTER perm.role != null AND perm.role != ""
                        RETURN {
                            role: perm.role,
                            priority: role_priority[perm.role] || 1
                        }
                )

                // Priority 2: Team permissions on record
                LET team_perms = (
                    FOR user_team_perm IN permission
                        FILTER user_team_perm._from == user_from
                        FILTER user_team_perm.type == "USER"
                        FILTER STARTS_WITH(user_team_perm._to, "teams/")
                        FILTER user_team_perm.role != null AND user_team_perm.role != ""
                        FOR team_record_perm IN permission
                            FILTER team_record_perm._from == user_team_perm._to
                            FILTER team_record_perm._to == record_from
                            FILTER team_record_perm.type == "TEAM"
                            RETURN {
                                role: user_team_perm.role,
                                priority: role_priority[user_team_perm.role] || 1
                            }
                )

                // Priority 3: Group permissions on record
                LET group_perms = (
                    FOR record_group_perm IN permission
                        FILTER record_group_perm._to == record_from AND record_group_perm.type == "GROUP"
                        FILTER record_group_perm.role != null AND record_group_perm.role != ""
                        LET group_to = record_group_perm._from
                        FOR user_group_perm IN permission
                            FILTER user_group_perm._from == user_from AND user_group_perm._to == group_to
                            RETURN {
                                role: record_group_perm.role,
                                priority: role_priority[record_group_perm.role] || 1
                            }
                )

                // Priority 4: For KB records, find root KB and check permission
                // Compute root KB unconditionally (will be null for non-KB records)
                LET start_rg_id = record_doc != null ? record_doc.connectorId : null
                LET connector_rg = start_rg_id != null ? DOCUMENT(CONCAT("recordGroups/", start_rg_id)) : null
                LET is_connector_kb = connector_rg != null AND connector_rg.connectorName == "KB"

                // If connector is root KB, use it; otherwise traverse up to find KB
                LET root_kb_from_traversal = (connector_rg != null AND NOT is_connector_kb) ? (
                    FOR v, e, p IN 0..10 INBOUND CONCAT("recordGroups/", connector_rg._key) belongsTo
                        FILTER v != null AND v.connectorName == "KB"
                        LIMIT 1
                        RETURN v
                ) : []
                LET root_kb = is_connector_kb ? connector_rg : (
                    LENGTH(root_kb_from_traversal) > 0 ? root_kb_from_traversal[0] : null
                )
                LET root_kb_to = root_kb != null ? CONCAT("recordGroups/", root_kb._key) : null

                // Check direct user permission on root KB
                LET root_kb_direct = root_kb_to != null ? FIRST(
                    FOR perm IN permission
                        FILTER perm._from == user_from AND perm._to == root_kb_to AND perm.type == "USER"
                        FILTER perm.role != null AND perm.role != ""
                        RETURN {
                            role: perm.role,
                            priority: role_priority[perm.role] || 1
                        }
                ) : null

                // Check team permissions on root KB
                LET root_kb_team = root_kb_to != null ? FIRST(
                    FOR user_team_perm IN permission
                        FILTER user_team_perm._from == user_from
                        FILTER user_team_perm.type == "USER"
                        FILTER STARTS_WITH(user_team_perm._to, "teams/")
                        FOR team_kb_perm IN permission
                            FILTER team_kb_perm._from == user_team_perm._to
                            FILTER team_kb_perm._to == root_kb_to
                            FILTER team_kb_perm.type == "TEAM"
                            FILTER team_kb_perm.role != null AND team_kb_perm.role != ""
                            RETURN {
                                role: team_kb_perm.role,
                                priority: role_priority[team_kb_perm.role] || 1
                            }
                ) : null

                // Check group permissions on root KB
                LET root_kb_group = root_kb_to != null ? FIRST(
                    FOR kb_group_perm IN permission
                        FILTER kb_group_perm._to == root_kb_to AND kb_group_perm.type == "GROUP"
                        FILTER kb_group_perm.role != null AND kb_group_perm.role != ""
                        LET group_to = kb_group_perm._from
                        FOR user_group_perm IN permission
                            FILTER user_group_perm._from == user_from AND user_group_perm._to == group_to
                            RETURN {
                                role: kb_group_perm.role,
                                priority: role_priority[kb_group_perm.role] || 1
                            }
                ) : null

                // Get highest priority root KB permission
                LET all_root_kb_perms = REMOVE_VALUE(
                    FLATTEN([
                        root_kb_direct != null ? [root_kb_direct] : [],
                        root_kb_team != null ? [root_kb_team] : [],
                        root_kb_group != null ? [root_kb_group] : []
                    ]),
                    null
                )
                LET root_kb_perm = (is_kb_record AND LENGTH(all_root_kb_perms) > 0) ? (
                    FIRST(
                        FOR p IN all_root_kb_perms
                            SORT p.priority DESC
                            LIMIT 1
                            RETURN p
                    )
                ) : null

                // Combine all permissions and get highest priority role
                LET all_perms = REMOVE_VALUE(
                    FLATTEN([
                        direct_perm != null ? [direct_perm] : [],
                        team_perms,
                        group_perms,
                        root_kb_perm != null ? [root_kb_perm] : []
                    ]),
                    null
                )
                LET highest_perm = LENGTH(all_perms) > 0 ? (
                    FIRST(
                        FOR p IN all_perms
                            SORT p.priority DESC
                            LIMIT 1
                            RETURN p
                    )
                ) : null
                // Only default to READER if NO permissions were found
                LET role = highest_perm != null ? highest_perm.role : "READER"
                RETURN { id: rec_id, role: role }
            """
            results = await self.http_client.execute_aql(query, bind_vars={"user_key": user_key, "record_ids": record_ids}, txn_id=transaction)
            for r in (results or []):
                role = r.get('role') or 'READER'  # Ensure role is never None
                permissions[r['id']] = {"role": role, "canEdit": role in ['OWNER', 'WRITER', 'ADMIN', 'EDITOR'], "canDelete": role in ['OWNER', 'ADMIN']}

        return permissions

    async def get_knowledge_hub_breadcrumbs(
        self,
        node_id: str,
        transaction: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get breadcrumb trail for a node.

        NOTE(N+1 Queries): Uses iterative parent lookup (one query per level) because a single
        AQL graph traversal isn't feasible here. Parent relationships are stored via multiple
        mechanisms: parentId field (recordGroups), PARENT_CHILD edges (records),
        inheritPermissions edges, and connectorId field (linking to apps). AQL graph traversal
        requires consistent edge-based relationships, but our hierarchy uses mixed field/edge
        patterns across different collections (records, recordGroups, apps).
        """
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

            // Find parent ID - SIMPLIFIED LOGIC:
            // For Records: Only use recordRelations edges (inbound edges with PARENT_CHILD or ATTACHMENT)
            //   Traverse up until we find a recordGroup or app, or no more edges
            // For RecordGroups: Use belongsTo (KB) or parentId/connectorId (connector)
            // For Apps: No parent
            LET parent_id = record != null ? (
                // For records: find parent via recordRelations edges only
                // Edge direction: parent -> child (edge._from = parent, edge._to = current record)
                FIRST(
                    FOR edge IN recordRelations
                        FILTER edge._to == record._id AND edge.relationshipType IN ["PARENT_CHILD", "ATTACHMENT"]
                        LET parent_doc = DOCUMENT(edge._from)
                        FILTER parent_doc != null
                        // Parent can be a record (for nested folders/files) or recordGroup (for immediate children)
                        // Return the parent ID regardless of type - we'll traverse up in next iteration
                        RETURN PARSE_IDENTIFIER(edge._from).key
                )
            ) : (
                rg != null ? (
                    // For KB record groups: check belongsTo edge
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
            LET node_id = CONTAINS(@parent_id, "/") ? @parent_id : (
                FIRST(UNION(
                    (FOR doc IN records FILTER doc._key == @parent_id RETURN doc._id),
                    (FOR doc IN apps FILTER doc._key == @parent_id RETURN doc._id),
                    (FOR doc IN recordGroups FILTER doc._key == @parent_id RETURN doc._id)
                ))
            )

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
            LET permission_target = node_id

            // For records, check if they inherit from a parent (KB or record group)
            LET inherited_from = STARTS_WITH(node_id, "records/") ? FIRST(
                FOR edge IN inheritPermissions
                    FILTER edge._from == node_id
                    RETURN edge._to
            ) : null

            // Use inherited parent for permission check if it exists, otherwise use node itself
            LET final_permission_target = inherited_from != null ? inherited_from : permission_target

            // Determine if this is a KB-related node (for root KB fallback)
            LET target_doc = DOCUMENT(final_permission_target)
            LET is_record = STARTS_WITH(node_id, "records/")
            LET record_doc = is_record ? DOCUMENT(node_id) : null
            LET record_connector_id = record_doc != null ? record_doc.connectorId : null
            LET record_connector = record_connector_id != null ? (
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
                is_nested_rg_under_kb ? rg_doc._key : null
            )
            LET start_connector = start_connector_id != null ? DOCUMENT(CONCAT("recordGroups/", start_connector_id)) : null
            LET is_start_kb = start_connector != null AND start_connector.connectorName == "KB"
            LET root_kb_from_traversal = (start_connector != null AND NOT is_start_kb) ? (
                FOR v IN 0..10 INBOUND CONCAT("recordGroups/", start_connector._key) belongsTo
                    FILTER v != null AND v.connectorName == "KB"
                    LIMIT 1
                    RETURN v
            ) : []
            LET root_kb = is_start_kb ? start_connector : (
                LENGTH(root_kb_from_traversal) > 0 ? root_kb_from_traversal[0] : null
            )
            LET root_kb_to = root_kb != null ? CONCAT("recordGroups/", root_kb._key) : null

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

            LET highest_perm = LENGTH(all_perms) > 0 ? (
                FIRST(
                    FOR p IN all_perms
                        SORT p.priority DESC
                        LIMIT 1
                        RETURN p
                )
            ) : null

            LET final_role = highest_perm != null ? highest_perm.role : "READER"
            LET can_edit = final_role IN ["ADMIN", "EDITOR", "WRITER", "OWNER"]
            LET can_upload = final_role IN ["ADMIN", "EDITOR", "WRITER", "OWNER"]
            LET can_create = final_role IN ["ADMIN", "EDITOR", "WRITER", "OWNER"]
            LET can_delete = final_role IN ["ADMIN", "OWNER"]
            LET can_manage = final_role IN ["ADMIN", "OWNER"]

            RETURN {
                role: final_role,
                canUpload: can_upload,
                canCreateFolders: can_create,
                canEdit: can_edit,
                canDelete: can_delete,
                canManagePermissions: can_manage
            }
            """
            results = await self.http_client.execute_aql(
                query,
                bind_vars={"user_key": user_key, "org_id": org_id, "parent_id": parent_id},
                txn_id=transaction
            )

        if results and results[0]:
            return results[0]
        return {
            "role": "READER",
            "canUpload": False,
            "canCreateFolders": False,
            "canEdit": False,
            "canDelete": False,
            "canManagePermissions": False
        }

    async def is_knowledge_hub_folder(
        self,
        record_id: str,
        folder_mime_types: List[str],
        transaction: Optional[str] = None
    ) -> bool:
        """Check if a record is a folder."""
        query = """
        LET record = DOCUMENT("records", @record_id)
        FILTER record != null
        LET is_folder_by_mimetype = record.mimeType IN @folder_mime_types
        LET is_folder_by_file = FIRST(
            FOR edge IN isOfType
                FILTER edge._from == record._id
                LET f = DOCUMENT(edge._to)
                FILTER f != null AND f.isFile == false
                RETURN true
        ) == true
        RETURN is_folder_by_mimetype OR is_folder_by_file
        """
        results = await self.http_client.execute_aql(query, bind_vars={"record_id": record_id, "folder_mime_types": folder_mime_types}, txn_id=transaction)
        return results[0] if results else False

    async def get_knowledge_hub_node_info(
        self,
        node_id: str,
        folder_mime_types: List[str],
        transaction: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get node information including type and subtype."""
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
        return results[0] if results and results[0] else None

    async def get_knowledge_hub_parent_node(
        self,
        node_id: str,
        folder_mime_types: List[str],
        transaction: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get the parent node of a given node in a single query."""
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
            return results[0] if results else {"kbs": [], "apps": []}
        except Exception:
            # self.logger.error(f"Failed to get filter options: {e}")
            return {"kbs": [], "apps": []}

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

            from app.config.constants.arangodb import RecordTypes

            # First check access and get permission paths
            access_query = f"""
            LET userDoc = FIRST(
                FOR user IN @@users
                FILTER user.userId == @userId
                RETURN user
            )
            LET recordDoc = DOCUMENT(CONCAT(@records, '/', @recordId))
            LET kb = FIRST(
                FOR k IN 1..1 OUTBOUND recordDoc._id @@belongs_to
                RETURN k
            )
            LET directAccessPermissionEdge = (
                FOR records, edge IN 1..1 ANY userDoc._id {CollectionNames.PERMISSION.value}
                FILTER records._key == @recordId
                RETURN {{
                    type: 'DIRECT',
                    source: userDoc,
                    role: edge.role
                }}
            )
            LET groupAccessPermissionEdge = (
                FOR group, belongsEdge IN 1..1 ANY userDoc._id {CollectionNames.PERMISSION.value}
                FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                FOR records, permEdge IN 1..1 ANY group._id {CollectionNames.PERMISSION.value}
                FILTER records._key == @recordId
                RETURN {{
                    type: 'GROUP',
                    source: group,
                    role: permEdge.role
                }}
            )
            LET recordGroupAccess = (
                // Hop 1: User -> Group
                FOR group, userToGroupEdge IN 1..1 ANY userDoc._id {CollectionNames.PERMISSION.value}
                FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)

                // Hop 2: Group -> RecordGroup
                FOR recordGroup, groupToRecordGroupEdge IN 1..1 ANY group._id {CollectionNames.PERMISSION.value}
                FILTER groupToRecordGroupEdge.type == 'GROUP' or groupToRecordGroupEdge.type == 'ROLE'

                // Hop 3: RecordGroup -> Record
                FOR record, recordGroupToRecordEdge IN 1..1 INBOUND recordGroup._id {CollectionNames.INHERIT_PERMISSIONS.value}
                FILTER record._key == @recordId

                RETURN {{
                    type: 'RECORD_GROUP',
                    source: recordGroup,
                    role: groupToRecordGroupEdge.role
                }}
            )
            LET inheritedRecordGroupAccess = (
                // Hop 1: User -> Group (permission)
                FOR group, userToGroupEdge IN 1..1 ANY userDoc._id {CollectionNames.PERMISSION.value}
                    FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)

                // Hop 2: Group -> Parent RecordGroup (permission)
                FOR parentRecordGroup, groupToRgEdge IN 1..1 ANY group._id {CollectionNames.PERMISSION.value}
                    FILTER groupToRgEdge.type == 'GROUP' or groupToRgEdge.type == 'ROLE'

                // Hop 3: Parent RecordGroup -> Child RecordGroup (belongs_to)
                FOR childRecordGroup, rgToRgEdge IN 1..1 INBOUND parentRecordGroup._id {CollectionNames.INHERIT_PERMISSIONS.value}

                // Hop 4: Child RecordGroup -> Record (belongs_to)
                FOR record, childRgToRecordEdge IN 1..1 INBOUND childRecordGroup._id {CollectionNames.INHERIT_PERMISSIONS.value}
                    FILTER record._key == @recordId

                    RETURN {{
                        type: 'NESTED_RECORD_GROUP',
                        source: childRecordGroup,
                        role: groupToRgEdge.role
                    }}
            )
            LET directUserToRecordGroupAccess = (
                // Direct user -> record_group permission (with nested record groups support)
                FOR recordGroup, userToRgEdge IN 1..1 ANY userDoc._id {CollectionNames.PERMISSION.value}
                    FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)

                    // Record group -> nested record groups (0 to 5 levels) -> record
                    FOR record, edge, path IN 0..5 INBOUND recordGroup._id {CollectionNames.INHERIT_PERMISSIONS.value}
                        // Only process if final vertex is the target record
                        FILTER record._key == @recordId
                        FILTER IS_SAME_COLLECTION("records", record)

                        LET finalEdge = LENGTH(path.edges) > 0 ? path.edges[LENGTH(path.edges) - 1] : edge

                        RETURN {{
                            type: 'DIRECT_USER_RECORD_GROUP',
                            source: recordGroup,
                            role: userToRgEdge.role,
                            depth: LENGTH(path.edges)
                        }}
            )
            LET orgAccessPermissionEdge = (
                FOR org, belongsEdge IN 1..1 ANY userDoc._id {CollectionNames.BELONGS_TO.value}
                FOR records, permEdge IN 1..1 ANY org._id {CollectionNames.PERMISSION.value}
                FILTER records._key == @recordId
                RETURN {{
                    type: 'ORGANIZATION',
                    source: org,
                    role: permEdge.role
                }}
            )
            LET orgRecordGroupAccess = (
                FOR org, belongsEdge IN 1..1 ANY userDoc._id {CollectionNames.BELONGS_TO.value}
                    FILTER belongsEdge.entityType == 'ORGANIZATION'

                    FOR recordGroup, orgToRgEdge IN 1..1 ANY org._id {CollectionNames.PERMISSION.value}
                        FILTER orgToRgEdge.type == 'ORG'
                        FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)

                        FOR record, edge, path IN 0..2 INBOUND recordGroup._id {CollectionNames.INHERIT_PERMISSIONS.value}
                            FILTER record._key == @recordId
                            FILTER IS_SAME_COLLECTION("records", record)

                            LET finalEdge = LENGTH(path.edges) > 0 ? path.edges[LENGTH(path.edges) - 1] : edge

                            RETURN {{
                                type: 'ORG_RECORD_GROUP',
                                source: recordGroup,
                                role: orgToRgEdge.role,
                                depth: LENGTH(path.edges)
                            }}
            )
            LET kbDirectAccess = kb ? (
                FOR permEdge IN @@permission
                    FILTER permEdge._from == userDoc._id AND permEdge._to == kb._id
                    FILTER permEdge.type == "USER"
                    LIMIT 1
                    LET parentFolder = FIRST(
                        FOR parent, relEdge IN 1..1 INBOUND recordDoc._id @@record_relations
                            FILTER relEdge.relationshipType == 'PARENT_CHILD'
                            FILTER PARSE_IDENTIFIER(parent._id).collection == @files
                            RETURN parent
                    )
                    RETURN {{
                        type: 'KNOWLEDGE_BASE',
                        source: kb,
                        role: permEdge.role,
                        folder: parentFolder
                    }}
            ) : []
            LET kbTeamAccess = kb ? (
                // Check team-based KB access: User -> Team -> KB
                LET role_priority = {{
                    "OWNER": 4,
                    "WRITER": 3,
                    "READER": 2,
                    "COMMENTER": 1
                }}
                LET team_roles = (
                    FOR kb_team_perm IN @@permission
                        FILTER kb_team_perm._to == kb._id
                        FILTER kb_team_perm.type == "TEAM"
                        LET team_id = PARSE_IDENTIFIER(kb_team_perm._from).key
                        // Check if user is a member of this team
                        FOR user_team_perm IN @@permission
                            FILTER user_team_perm._from == userDoc._id
                            FILTER user_team_perm._to == CONCAT('teams/', team_id)
                            FILTER user_team_perm.type == "USER"
                            RETURN {{
                                role: user_team_perm.role,
                                priority: role_priority[user_team_perm.role]
                            }}
                )
                LET highest_role = LENGTH(team_roles) > 0 ? FIRST(
                    FOR r IN team_roles
                        SORT r.priority DESC
                        LIMIT 1
                        RETURN r.role
                ) : null
                FILTER highest_role != null
                LET parentFolder = FIRST(
                    FOR parent, relEdge IN 1..1 INBOUND recordDoc._id @@record_relations
                        FILTER relEdge.relationshipType == 'PARENT_CHILD'
                        FILTER PARSE_IDENTIFIER(parent._id).collection == @files
                        RETURN parent
                )
                RETURN {{
                    type: 'KNOWLEDGE_BASE_TEAM',
                    source: kb,
                    role: highest_role,
                    folder: parentFolder
                }}
            ) : []
            LET kbAccess = UNION_DISTINCT(kbDirectAccess, kbTeamAccess)
            LET anyoneAccess = (
                FOR records IN @@anyone
                FILTER records.organization == @orgId
                    AND records.file_key == @recordId
                RETURN {{
                    type: 'ANYONE',
                    source: null,
                    role: records.role
                }}
            )
            LET allAccess = UNION_DISTINCT(
                directAccessPermissionEdge,
                recordGroupAccess,
                groupAccessPermissionEdge,
                inheritedRecordGroupAccess,
                directUserToRecordGroupAccess,
                orgAccessPermissionEdge,
                orgRecordGroupAccess,
                kbAccess,
                anyoneAccess
            )
            RETURN LENGTH(allAccess) > 0 ? allAccess : null
            """

            bind_vars = {
                "userId": user_id,
                "orgId": org_id,
                "recordId": record_id,
                "@users": CollectionNames.USERS.value,
                "records": CollectionNames.RECORDS.value,
                "files": CollectionNames.FILES.value,
                "@anyone": CollectionNames.ANYONE.value,
                "@belongs_to": CollectionNames.BELONGS_TO.value,
                "@permission": CollectionNames.PERMISSION.value,
                "@record_relations": CollectionNames.RECORD_RELATIONS.value,
            }

            results = await self.http_client.execute_aql(
                access_query,
                bind_vars=bind_vars,
                txn_id=transaction
            )
            access_result = next(iter(results), None) if results else None

            if not access_result:
                return None

            # If we have access, get the complete record details
            record = await self.get_document(record_id, CollectionNames.RECORDS.value, transaction)
            if not record:
                return None

            user = await self.get_user_by_user_id(user_id)
            if not user:
                return None

            # Get file or mail details based on record type
            additional_data = None
            if record.get("recordType") == RecordTypes.FILE.value:
                additional_data = await self.get_document(
                    record_id, CollectionNames.FILES.value, transaction
                )
            elif record.get("recordType") == RecordTypes.MAIL.value:
                additional_data = await self.get_document(
                    record_id, CollectionNames.MAILS.value, transaction
                )
                if additional_data and user.get("email"):
                    message_id = record.get("externalRecordId")
                    additional_data["webUrl"] = (
                        f"https://mail.google.com/mail?authuser={user['email']}#all/{message_id}"
                    )
            elif record.get("recordType") == RecordTypes.TICKET.value:
                additional_data = await self.get_document(
                    record_id, CollectionNames.TICKETS.value, transaction
                )

            # Get metadata
            metadata_query = f"""
            LET record = DOCUMENT(CONCAT('{CollectionNames.RECORDS.value}/', @recordId))

            LET departments = (
                FOR dept IN OUTBOUND record._id {CollectionNames.BELONGS_TO_DEPARTMENT.value}
                RETURN {{
                    id: dept._key,
                    name: dept.departmentName
                }}
            )

            LET categories = (
                FOR cat IN OUTBOUND record._id {CollectionNames.BELONGS_TO_CATEGORY.value}
                FILTER PARSE_IDENTIFIER(cat._id).collection == '{CollectionNames.CATEGORIES.value}'
                RETURN {{
                    id: cat._key,
                    name: cat.name
                }}
            )

            LET subcategories1 = (
                FOR subcat IN OUTBOUND record._id {CollectionNames.BELONGS_TO_CATEGORY.value}
                FILTER PARSE_IDENTIFIER(subcat._id).collection == '{CollectionNames.SUBCATEGORIES1.value}'
                RETURN {{
                    id: subcat._key,
                    name: subcat.name
                }}
            )

            LET subcategories2 = (
                FOR subcat IN OUTBOUND record._id {CollectionNames.BELONGS_TO_CATEGORY.value}
                FILTER PARSE_IDENTIFIER(subcat._id).collection == '{CollectionNames.SUBCATEGORIES2.value}'
                RETURN {{
                    id: subcat._key,
                    name: subcat.name
                }}
            )

            LET subcategories3 = (
                FOR subcat IN OUTBOUND record._id {CollectionNames.BELONGS_TO_CATEGORY.value}
                FILTER PARSE_IDENTIFIER(subcat._id).collection == '{CollectionNames.SUBCATEGORIES3.value}'
                RETURN {{
                    id: subcat._key,
                    name: subcat.name
                }}
            )

            LET topics = (
                FOR topic IN OUTBOUND record._id {CollectionNames.BELONGS_TO_TOPIC.value}
                RETURN {{
                    id: topic._key,
                    name: topic.name
                }}
            )

            LET languages = (
                FOR lang IN OUTBOUND record._id {CollectionNames.BELONGS_TO_LANGUAGE.value}
                RETURN {{
                    id: lang._key,
                    name: lang.name
                }}
            )

            RETURN {{
                departments: departments,
                categories: categories,
                subcategories1: subcategories1,
                subcategories2: subcategories2,
                subcategories3: subcategories3,
                topics: topics,
                languages: languages
            }}
            """
            metadata_results = await self.http_client.execute_aql(
                metadata_query,
                bind_vars={"recordId": record_id},
                txn_id=transaction
            )
            metadata_result = next(iter(metadata_results), None) if metadata_results else None

            # Get knowledge base info if record is in a KB
            kb_info = None
            folder_info = None
            for access in access_result:
                if access.get("type") in ["KNOWLEDGE_BASE", "KNOWLEDGE_BASE_TEAM"]:
                    kb = access.get("source")
                    if kb:
                        kb_info = {
                            "id": kb.get("_key") or kb.get("id"),
                            "name": kb.get("groupName"),
                            "orgId": kb.get("orgId"),
                        }
                    if access.get("folder"):
                        folder = access.get("folder")
                        folder_info = {
                            "id": folder.get("_key") or folder.get("id"),
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
                        if record.get("recordType") == RecordTypes.FILE.value
                        else None
                    ),
                    "mailRecord": (
                        additional_data
                        if record.get("recordType") == RecordTypes.MAIL.value
                        else None
                    ),
                    "ticketRecord": (
                        additional_data
                        if record.get("recordType") == RecordTypes.TICKET.value
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

            query = f"""
            FOR org IN {CollectionNames.ORGS.value}
                FILTER org._key == @org_id
                RETURN org.accountType
            """

            results = await self.http_client.execute_aql(
                query,
                bind_vars={"org_id": org_id},
                txn_id=transaction
            )

            if results:
                account_type = results[0]
                self.logger.info(f"✅ Found account type: {account_type}")
                return account_type
            else:
                self.logger.warning(f"⚠️ Organization not found: {org_id}")
                return None

        except Exception as e:
            self.logger.error(f"❌ Failed to get account type: {str(e)}")
            return None

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

    # ==================== Duplicate Detection & Relationship Management ====================

    async def find_duplicate_files(
        self,
        file_key: str,
        md5_checksum: str,
        size_in_bytes: int,
        transaction: Optional[str] = None
    ) -> List[Dict]:
        """
        Find duplicate files based on MD5 checksum and file size.

        This method works for all record types despite the name "files" for backward compatibility.

        Args:
            file_key: Key of the file to exclude from results
            md5_checksum: MD5 checksum of the file
            size_in_bytes: Size of the file in bytes
            transaction: Optional transaction ID

        Returns:
            List of record documents that match both criteria (excluding the file_key)
        """
        try:
            self.logger.info(
                f"🔍 Finding duplicate files with MD5: {md5_checksum} and size: {size_in_bytes} bytes"
            )

            query = f"""
            FOR file IN {CollectionNames.FILES.value}
                FILTER file.md5Checksum == @md5_checksum
                AND file.sizeInBytes == @size_in_bytes
                AND file._key != @file_key
                LET record = (
                    FOR r IN {CollectionNames.RECORDS.value}
                        FILTER r._key == file._key
                        RETURN r
                )[0]
                RETURN record
            """

            results = await self.http_client.execute_aql(
                query,
                bind_vars={
                    "md5_checksum": md5_checksum,
                    "size_in_bytes": size_in_bytes,
                    "file_key": file_key
                },
                txn_id=transaction
            )

            duplicate_records = [r for r in results if r is not None] if results else []

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
            source_key: Key/ID of the source document
            target_key: Key/ID of the target document
            transaction: Optional transaction ID

        Returns:
            True if successful, False otherwise
        """
        try:
            self.logger.info(f"🚀 Copying relationships from {source_key} to {target_key}")

            # Define collections to copy relationships from
            edge_collections = [
                CollectionNames.BELONGS_TO_DEPARTMENT.value,
                CollectionNames.BELONGS_TO_CATEGORY.value,
                CollectionNames.BELONGS_TO_LANGUAGE.value,
                CollectionNames.BELONGS_TO_TOPIC.value
            ]

            source_doc = f"{CollectionNames.RECORDS.value}/{source_key}"
            target_doc = f"{CollectionNames.RECORDS.value}/{target_key}"

            for collection in edge_collections:
                # Find all edges from source document
                query = f"""
                FOR edge IN {collection}
                    FILTER edge._from == @source_doc
                    RETURN {{
                        from: edge._from,
                        to: edge._to,
                        timestamp: edge.createdAtTimestamp
                    }}
                """

                bind_vars = {"source_doc": source_doc}
                edges = await self.http_client.execute_aql(query, bind_vars, txn_id=transaction)

                if edges:
                    # Create new edges for target document
                    for edge in edges:
                        new_edge = {
                            "_from": target_doc,
                            "_to": edge["to"],
                            "createdAtTimestamp": edge.get("timestamp", get_epoch_timestamp_in_ms())
                        }
                        await self.http_client.create_document(
                            collection,
                            new_edge,
                            txn_id=transaction
                        )

                    self.logger.info(
                        f"✅ Copied {len(edges)} edges from {collection}"
                    )

            self.logger.info(f"✅ Successfully copied all relationships from {source_key} to {target_key}")
            return True

        except Exception as e:
            self.logger.error(
                f"❌ Failed to copy document relationships: {str(e)}"
            )
            return False

    async def _get_user_app_ids(self, user_id: str) -> List[str]:
        """Gets a list of accessible app connector IDs for a user."""
        try:
            query = f"""
            FOR app IN OUTBOUND
                '{CollectionNames.USERS.value}/{user_id}'
                {CollectionNames.USER_APP_RELATION.value}
            RETURN app
            """
            user_app_docs = await self.execute_query(query)
            # Filter out None values and apps without _key before accessing _key
            user_apps = [app['_key'] for app in (user_app_docs or []) if app and app.get('_key')]
            self.logger.debug(f"User has access to {len(user_apps)} apps: {user_apps}")
            return user_apps
        except Exception as e:
            self.logger.error(f"Failed to get user app ids: {str(e)}")
            raise

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
            transaction (Optional[str]): Optional transaction context

        Returns:
            List[Dict]: List of accessible records
        """
        self.logger.info(
            f"Getting accessible records for user {user_id} in org {org_id} with filters {filters}"
        )

        try:
            user = await self.get_user_by_user_id(user_id)
            if not user:
                self.logger.warning(f"User not found for userId: {user_id}")
                return []

            user_key = user.get('_key')
            # Get user's accessible app connector ids
            user_apps_ids = await self._get_user_app_ids(user_key)

            # Extract filters
            filters = filters or {}
            kb_ids = filters.get("kb") if filters else None
            connector_ids = filters.get("apps") if filters else None

            # Determine filter case
            has_kb_filter = kb_ids is not None and len(kb_ids) > 0
            has_app_filter = connector_ids is not None and len(connector_ids) > 0

            self.logger.info(
                f"🔍 Filter analysis - KB filter: {has_kb_filter} (IDs: {kb_ids}), "
                f"App filter: {has_app_filter} (Connector IDs: {connector_ids})"
            )

            # App filter condition - only filter connector records by user's accessible apps
            app_filter_condition = '''
                FILTER (
                    record.origin == "UPLOAD" OR
                    (record.origin == "CONNECTOR" AND record.connectorId IN @user_apps_ids)
                )
            '''

            # Build base query with common parts
            query = f"""
            LET userDoc = FIRST(
                FOR user IN @@users
                FILTER user.userId == @userId
                RETURN user
            )


            // User -> Direct Records (via permission edges)
            LET directRecords = (
                FOR record IN 1..1 ANY userDoc._id {CollectionNames.PERMISSION.value}
                    {app_filter_condition}
                    RETURN DISTINCT record
            )

            // User -> Group -> Records (via belongs_to edges)
            LET groupRecords = (
                FOR group, edge IN 1..1 ANY userDoc._id {CollectionNames.BELONGS_TO.value}
                FOR record IN 1..1 ANY group._id {CollectionNames.PERMISSION.value}
                    {app_filter_condition}
                    RETURN DISTINCT record
            )

            // User -> Group -> Records (via permission edges)
            LET groupRecordsPermissionEdge = (
                FOR group, edge IN 1..1 ANY userDoc._id {CollectionNames.PERMISSION.value}
                FOR record IN 1..1 ANY group._id {CollectionNames.PERMISSION.value}
                    {app_filter_condition}
                    RETURN DISTINCT record
            )

            // User -> Organization -> Records (direct)
            LET orgRecords = (
                FOR org, edge IN 1..1 ANY userDoc._id {CollectionNames.BELONGS_TO.value}
                FOR record IN 1..1 ANY org._id {CollectionNames.PERMISSION.value}
                    {app_filter_condition}
                    RETURN DISTINCT record
            )

            // User -> Organization -> RecordGroup -> Records (direct and inherited)
            LET orgRecordGroupRecords = (
                FOR org, belongsEdge IN 1..1 ANY userDoc._id {CollectionNames.BELONGS_TO.value}

                    FOR recordGroup, orgToRgEdge IN 1..1 ANY org._id {CollectionNames.PERMISSION.value}
                        FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)

                        FOR record, edge, path IN 0..2 INBOUND recordGroup._id {CollectionNames.INHERIT_PERMISSIONS.value}
                            FILTER IS_SAME_COLLECTION("records", record)
                            {app_filter_condition}
                            RETURN DISTINCT record
            )

            // User -> Group/Role -> RecordGroup -> Record
            LET recordGroupRecords = (

                FOR group, userToGroupEdge IN 1..1 ANY userDoc._id {CollectionNames.PERMISSION.value}
                FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)

                FOR recordGroup, groupToRecordGroupEdge IN 1..1 ANY group._id {CollectionNames.PERMISSION.value}

                // Support nested RecordGroups (0..5 levels)
                FOR record, edge, path IN 0..5 INBOUND recordGroup._id {CollectionNames.INHERIT_PERMISSIONS.value}
                FILTER IS_SAME_COLLECTION("records", record)
                {app_filter_condition}
                RETURN DISTINCT record
            )

            // User -> Group/Role -> RecordGroup -> Records (inherited)
            LET inheritedRecordGroupRecords = (
                FOR recordGroup, userToRgEdge IN 1..1 ANY userDoc._id {CollectionNames.PERMISSION.value}
                FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)

                FOR record, edge, path IN 0..5 INBOUND recordGroup._id {CollectionNames.INHERIT_PERMISSIONS.value}
                FILTER IS_SAME_COLLECTION("records", record)
                {app_filter_condition}
                RETURN DISTINCT record
            )

            LET directAndGroupRecords = UNION_DISTINCT(
                directRecords,
                groupRecords,
                orgRecords,
                groupRecordsPermissionEdge,
                recordGroupRecords,
                inheritedRecordGroupRecords,
                orgRecordGroupRecords
            )

            LET anyoneRecords = (
                FOR records IN @@anyone
                    FILTER records.organization == @orgId
                    FOR record IN @@records
                        FILTER record != null AND record._key == records.file_key
                        {app_filter_condition}
                        RETURN record
            )
            """

            unions = []

            # Case 1: Both KB and App filters applied
            if has_kb_filter and has_app_filter:
                self.logger.info("🔍 Case 1: Both KB and App filters applied")

                # Get KB records with filter
                query += f"""
                // Direct user-KB permissions
                LET directKbRecords = (
                    FOR kb IN 1..1 ANY userDoc._id {CollectionNames.PERMISSION.value}
                        FILTER IS_SAME_COLLECTION("recordGroups", kb)
                        FILTER kb._key IN @kb_ids
                    FOR records IN 1..1 ANY kb._id {CollectionNames.BELONGS_TO.value}
                    RETURN DISTINCT records
                )

                // Team-based KB permissions: User -> Team -> KB -> Records
                LET teamKbRecords = (
                    FOR team, userTeamEdge IN 1..1 OUTBOUND userDoc._id {CollectionNames.PERMISSION.value}
                        FILTER IS_SAME_COLLECTION("teams", team)
                        FILTER userTeamEdge.type == "USER"
                    FOR kb, teamKbEdge IN 1..1 OUTBOUND team._id {CollectionNames.PERMISSION.value}
                        FILTER IS_SAME_COLLECTION("recordGroups", kb)
                        FILTER teamKbEdge.type == "TEAM"
                        FILTER kb._key IN @kb_ids
                    FOR records IN 1..1 ANY kb._id {CollectionNames.BELONGS_TO.value}
                    RETURN DISTINCT records
                )

                LET kbRecords = UNION_DISTINCT(directKbRecords, teamKbRecords)
                """
                unions.append("kbRecords")

                # Get app-filtered records from direct, group, org, and anyone
                query += """
                LET baseAccessible = UNION_DISTINCT(directAndGroupRecords, anyoneRecords)
                LET appFilteredRecords = (
                    FOR record IN baseAccessible
                        FILTER record.connectorId IN @connector_ids
                        RETURN DISTINCT record
                )
                """
                unions.append("appFilteredRecords")

            # Case 2: Only KB filter applied
            elif has_kb_filter and not has_app_filter:
                self.logger.info("🔍 Case 2: Only KB filter applied")

                # Get only filtered KB records
                query += f"""
                // Direct user-KB permissions with filter
                LET directKbRecords = (
                    FOR kb IN 1..1 ANY userDoc._id {CollectionNames.PERMISSION.value}
                        FILTER IS_SAME_COLLECTION("recordGroups", kb)
                        FILTER kb._key IN @kb_ids
                    FOR records IN 1..1 ANY kb._id {CollectionNames.BELONGS_TO.value}
                    RETURN DISTINCT records
                )

                // Team-based KB permissions with filter: User -> Team -> KB -> Records
                LET teamKbRecords = (
                    FOR team, userTeamEdge IN 1..1 OUTBOUND userDoc._id {CollectionNames.PERMISSION.value}
                        FILTER IS_SAME_COLLECTION("teams", team)
                        FILTER userTeamEdge.type == "USER"
                    FOR kb, teamKbEdge IN 1..1 OUTBOUND team._id {CollectionNames.PERMISSION.value}
                        FILTER IS_SAME_COLLECTION("recordGroups", kb)
                        FILTER teamKbEdge.type == "TEAM"
                        FILTER kb._key IN @kb_ids
                    FOR records IN 1..1 ANY kb._id {CollectionNames.BELONGS_TO.value}
                    RETURN DISTINCT records
                )

                LET kbRecords = UNION_DISTINCT(directKbRecords, teamKbRecords)
                """
                unions.append("kbRecords")

            # Case 3: Only App filter applied
            elif not has_kb_filter and has_app_filter:
                self.logger.info("🔍 Case 3: Only App filter applied")

                # Get app-filtered records from direct, group, org, and anyone
                query += """
                LET baseAccessible = UNION_DISTINCT(directAndGroupRecords, anyoneRecords)
                LET appFilteredRecords = (
                    FOR record IN baseAccessible
                        FILTER record.connectorId IN @connector_ids
                        RETURN DISTINCT record
                )
                """
                unions.append("appFilteredRecords")

            # Case 4: No KB or App filters - return all accessible records
            else:
                self.logger.info("🔍 Case 4: No KB or App filters - returning all accessible records")

                # Get all KB records
                query += f"""
                // Direct user-KB permissions
                LET directKbRecords = (
                    FOR kb IN 1..1 ANY userDoc._id {CollectionNames.PERMISSION.value}
                        FILTER IS_SAME_COLLECTION("recordGroups", kb)
                    FOR records IN 1..1 ANY kb._id {CollectionNames.BELONGS_TO.value}
                    RETURN DISTINCT records
                )

                // Team-based KB permissions: User -> Team -> KB -> Records
                LET teamKbRecords = (
                    FOR team, userTeamEdge IN 1..1 OUTBOUND userDoc._id {CollectionNames.PERMISSION.value}
                        FILTER IS_SAME_COLLECTION("teams", team)
                        FILTER userTeamEdge.type == "USER"
                    FOR kb, teamKbEdge IN 1..1 OUTBOUND team._id {CollectionNames.PERMISSION.value}
                        FILTER IS_SAME_COLLECTION("recordGroups", kb)
                        FILTER teamKbEdge.type == "TEAM"
                    FOR records IN 1..1 ANY kb._id {CollectionNames.BELONGS_TO.value}
                    RETURN DISTINCT records
                )

                LET kbRecords = UNION_DISTINCT(directKbRecords, teamKbRecords)

                """
                unions.append("kbRecords")
                unions.append("directAndGroupRecords")
                unions.append("anyoneRecords")

            # Combine all unions
            if len(unions) == 1:
                query += f"""
                LET allAccessibleRecords = {unions[0]}
                """
            else:
                query += f"""
                LET allAccessibleRecords = UNION_DISTINCT({", ".join(unions)})
                """

            # Add additional filter conditions (departments, categories, etc.)
            filter_conditions = []
            if filters:
                if filters.get("departments"):
                    filter_conditions.append(
                        f"""
                    LENGTH(
                        FOR dept IN OUTBOUND record._id {CollectionNames.BELONGS_TO_DEPARTMENT.value}
                        FILTER dept.departmentName IN @departmentNames
                        LIMIT 1
                        RETURN 1
                    ) > 0
                    """
                    )

                if filters.get("categories"):
                    filter_conditions.append(
                        f"""
                    LENGTH(
                        FOR cat IN OUTBOUND record._id {CollectionNames.BELONGS_TO_CATEGORY.value}
                        FILTER cat.name IN @categoryNames
                        LIMIT 1
                        RETURN 1
                    ) > 0
                    """
                    )

                if filters.get("subcategories1"):
                    filter_conditions.append(
                        f"""
                    LENGTH(
                        FOR subcat IN OUTBOUND record._id {CollectionNames.BELONGS_TO_CATEGORY.value}
                        FILTER subcat.name IN @subcat1Names
                        LIMIT 1
                        RETURN 1
                    ) > 0
                    """
                    )

                if filters.get("subcategories2"):
                    filter_conditions.append(
                        f"""
                    LENGTH(
                        FOR subcat IN OUTBOUND record._id {CollectionNames.BELONGS_TO_CATEGORY.value}
                        FILTER subcat.name IN @subcat2Names
                        LIMIT 1
                        RETURN 1
                    ) > 0
                    """
                    )

                if filters.get("subcategories3"):
                    filter_conditions.append(
                        f"""
                    LENGTH(
                        FOR subcat IN OUTBOUND record._id {CollectionNames.BELONGS_TO_CATEGORY.value}
                        FILTER subcat.name IN @subcat3Names
                        LIMIT 1
                        RETURN 1
                    ) > 0
                    """
                    )

                if filters.get("languages"):
                    filter_conditions.append(
                        f"""
                    LENGTH(
                        FOR lang IN OUTBOUND record._id {CollectionNames.BELONGS_TO_LANGUAGE.value}
                        FILTER lang.name IN @languageNames
                        LIMIT 1
                        RETURN 1
                    ) > 0
                    """
                    )

                if filters.get("topics"):
                    filter_conditions.append(
                        f"""
                    LENGTH(
                        FOR topic IN OUTBOUND record._id {CollectionNames.BELONGS_TO_TOPIC.value}
                        FILTER topic.name IN @topicNames
                        LIMIT 1
                        RETURN 1
                    ) > 0
                    """
                    )

            # Apply additional filters if any
            if filter_conditions:
                query += (
                    """
                FOR record IN allAccessibleRecords
                    FILTER """
                    + " AND ".join(filter_conditions)
                    + """
                    RETURN DISTINCT record
                """
                )
            else:
                query += """
                RETURN allAccessibleRecords
                """

            # Prepare bind variables
            bind_vars = {
                "userId": user_id,
                "orgId": org_id,
                "user_apps_ids": user_apps_ids,
                "@users": CollectionNames.USERS.value,
                "@records": CollectionNames.RECORDS.value,
                "@anyone": CollectionNames.ANYONE.value,
            }

            # Add conditional bind variables
            if has_kb_filter:
                bind_vars["kb_ids"] = kb_ids

            if has_app_filter:
                bind_vars["connector_ids"] = connector_ids

            # Add filter bind variables
            if filters:
                if filters.get("departments"):
                    bind_vars["departmentNames"] = filters["departments"]
                if filters.get("categories"):
                    bind_vars["categoryNames"] = filters["categories"]
                if filters.get("subcategories1"):
                    bind_vars["subcat1Names"] = filters["subcategories1"]
                if filters.get("subcategories2"):
                    bind_vars["subcat2Names"] = filters["subcategories2"]
                if filters.get("subcategories3"):
                    bind_vars["subcat3Names"] = filters["subcategories3"]
                if filters.get("languages"):
                    bind_vars["languageNames"] = filters["languages"]
                if filters.get("topics"):
                    bind_vars["topicNames"] = filters["topics"]

            # Execute query
            self.logger.debug(f"🔍 Executing query with bind_vars keys: {list(bind_vars.keys())}")
            result = await self.execute_query(query, bind_vars=bind_vars, transaction=transaction)

            # Log results
            record_count = 0
            if result:
                if isinstance(result[0], list):
                    record_count = len(result[0])
                    result = result[0]
                else:
                    record_count = len(result)

            self.logger.info(f"✅ Query completed - found {record_count} accessible records")

            if has_kb_filter:
                self.logger.info(f"✅ KB filtering applied for {len(kb_ids)} KBs")
            if has_app_filter:
                self.logger.info(
                    f"✅ App filtering applied for {len(connector_ids)} connector IDs"
                )
            if not has_kb_filter and not has_app_filter:
                self.logger.info("✅ No KB/App filters - returned all accessible records")

            return result if result else []

        except Exception as e:
            self.logger.error(f"❌ Failed to get accessible records: {str(e)}")
            raise

