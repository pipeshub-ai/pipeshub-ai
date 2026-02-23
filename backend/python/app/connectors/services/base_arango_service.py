"""ArangoDB service for interacting with the database"""

# pylint: disable=E1101, W0718
import asyncio
import datetime
import json
import unicodedata
import uuid
from collections import defaultdict
from io import BytesIO
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp  # type: ignore
from arango import ArangoClient  # type: ignore
from arango.database import TransactionDatabase  # type: ignore
from fastapi import Request  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    RECORD_TYPE_COLLECTION_MAPPING,
    CollectionNames,
    Connectors,
    DepartmentNames,
    GraphNames,
    LegacyGraphNames,
    OriginTypes,
    ProgressStatus,
    RecordRelations,
    RecordTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import config_node_constants
from app.connectors.services.kafka_service import KafkaService
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
    RecordType,
    TicketRecord,
    User,
    WebpageRecord,
)
from app.schema.arango.documents import (
    agent_schema,
    agent_template_schema,
    app_role_schema,
    app_schema,
    comment_record_schema,
    department_schema,
    file_record_schema,
    knowledge_schema,
    link_record_schema,
    mail_record_schema,
    orgs_schema,
    people_schema,
    project_record_schema,
    pull_request_record_schema,
    record_group_schema,
    record_schema,
    team_schema,
    ticket_record_schema,
    tool_schema,
    toolset_schema,
    user_schema,
    webpage_record_schema,
)
from app.schema.arango.edges import (
    agent_has_knowledge_schema,
    agent_has_toolset_schema,
    basic_edge_schema,
    belongs_to_schema,
    entity_relations_schema,
    inherit_permissions_schema,
    is_of_type_schema,
    permissions_schema,
    record_relations_schema,
    toolset_has_tool_schema,
    user_app_relation_schema,
    user_drive_relation_schema,
)
from app.schema.arango.graph import EDGE_DEFINITIONS
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Constants
MAX_REINDEX_DEPTH = 100  # Maximum depth for reindexing records (unlimited depth is capped at this value)

# Collection definitions with their schemas
NODE_COLLECTIONS = [
    (CollectionNames.RECORDS.value, record_schema),
    (CollectionNames.DRIVES.value, None),
    (CollectionNames.FILES.value, file_record_schema),
    (CollectionNames.LINKS.value, link_record_schema),
    (CollectionNames.MAILS.value, mail_record_schema),
    (CollectionNames.WEBPAGES.value, webpage_record_schema),
    (CollectionNames.COMMENTS.value, comment_record_schema),
    (CollectionNames.PEOPLE.value, people_schema),
    (CollectionNames.USERS.value, user_schema),
    (CollectionNames.GROUPS.value, None),
    (CollectionNames.ROLES.value, app_role_schema),
    (CollectionNames.ORGS.value, orgs_schema),
    (CollectionNames.ANYONE.value, None),
    (CollectionNames.CHANNEL_HISTORY.value, None),
    (CollectionNames.PAGE_TOKENS.value, None),
    (CollectionNames.APPS.value, app_schema),
    (CollectionNames.DEPARTMENTS.value, department_schema),
    (CollectionNames.CATEGORIES.value, None),
    (CollectionNames.LANGUAGES.value, None),
    (CollectionNames.TOPICS.value, None),
    (CollectionNames.SUBCATEGORIES1.value, None),
    (CollectionNames.SUBCATEGORIES2.value, None),
    (CollectionNames.SUBCATEGORIES3.value, None),
    (CollectionNames.BLOCKS.value, None),
    (CollectionNames.RECORD_GROUPS.value, record_group_schema),
    (CollectionNames.AGENT_INSTANCES.value, agent_schema),
    (CollectionNames.AGENT_TEMPLATES.value, agent_template_schema),
    (CollectionNames.AGENT_KNOWLEDGE.value, knowledge_schema),
    (CollectionNames.AGENT_TOOLSETS.value, toolset_schema),
    (CollectionNames.AGENT_TOOLS.value, tool_schema),
    (CollectionNames.TICKETS.value, ticket_record_schema),
    (CollectionNames.PROJECTS.value, project_record_schema),
    (CollectionNames.PULLREQUESTS.value,pull_request_record_schema),
    (CollectionNames.SYNC_POINTS.value, None),
    (CollectionNames.TEAMS.value, team_schema),
    (CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value, None)

]

EDGE_COLLECTIONS = [
    (CollectionNames.IS_OF_TYPE.value, is_of_type_schema),
    (CollectionNames.RECORD_RELATIONS.value, record_relations_schema),
    (CollectionNames.ENTITY_RELATIONS.value, entity_relations_schema),
    (CollectionNames.USER_DRIVE_RELATION.value, user_drive_relation_schema),
    (CollectionNames.BELONGS_TO_DEPARTMENT.value, basic_edge_schema),
    (CollectionNames.ORG_DEPARTMENT_RELATION.value, basic_edge_schema),
    (CollectionNames.BELONGS_TO.value, belongs_to_schema),
    (CollectionNames.INHERIT_PERMISSIONS.value, inherit_permissions_schema),
    (CollectionNames.ORG_APP_RELATION.value, basic_edge_schema),
    (CollectionNames.USER_APP_RELATION.value, user_app_relation_schema),
    (CollectionNames.BELONGS_TO_CATEGORY.value, basic_edge_schema),
    (CollectionNames.BELONGS_TO_LANGUAGE.value, basic_edge_schema),
    (CollectionNames.BELONGS_TO_TOPIC.value, basic_edge_schema),
    (CollectionNames.BELONGS_TO_RECORD_GROUP.value, basic_edge_schema),
    (CollectionNames.INTER_CATEGORY_RELATIONS.value, basic_edge_schema),
    (CollectionNames.PERMISSION.value, permissions_schema),
    (CollectionNames.AGENT_HAS_KNOWLEDGE.value, agent_has_knowledge_schema),
    (CollectionNames.AGENT_HAS_TOOLSET.value, agent_has_toolset_schema),
    (CollectionNames.TOOLSET_HAS_TOOL.value, toolset_has_tool_schema),
]

class BaseArangoService:
    """Base ArangoDB service class for interacting with the database"""

    # ========== NAME NORMALIZATION HELPERS ==========
    def _normalize_name(self, name: Optional[str]) -> Optional[str]:
        """Normalize a file/folder name to NFC and trim whitespace."""
        if name is None:
            return None
        try:
            return unicodedata.normalize("NFC", str(name)).strip()
        except Exception:
            # Fallback: best-effort string conversion
            return str(name).strip()

    def _normalized_name_variants_lower(self, name: str) -> List[str]:
        """Provide lowercase variants for equality comparisons (NFC and NFD)."""
        nfc = self._normalize_name(name) or ""
        try:
            nfd = unicodedata.normalize("NFD", nfc)
        except Exception:
            nfd = nfc
        return [nfc.lower(), nfd.lower()]

    def __init__(
        self,
        logger,
        arango_client: ArangoClient,
        config_service: ConfigurationService,
        kafka_service: Optional[KafkaService] = None,
        enable_schema_init: bool = False,
    ) -> None:
        self.logger = logger
        self.config_service = config_service
        self.client = arango_client
        self.kafka_service = kafka_service
        self.db = None
        # Controls whether this instance is allowed to create/update Arango collections/graphs.
        # Connector service should set this to True; other processes should pass False.
        self.enable_schema_init = enable_schema_init

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
                    CollectionNames.RECORD_GROUPS.value,
                ]
            }
        }

        # Initialize collections dictionary
        self._collections = {
            collection_name: None
            for collection_name, _ in NODE_COLLECTIONS + EDGE_COLLECTIONS
        }

    async def _initialize_new_collections(self) -> None:
        """Initialize all collections (both nodes and edges)"""
        try:
            self.logger.info("🚀 Initializing collections...")
            # Initialize all collections (both nodes and edges)
            for collection_name, schema in NODE_COLLECTIONS + EDGE_COLLECTIONS:
                self.logger.debug(f"Processing collection: {collection_name}")
                is_edge = (collection_name, schema) in EDGE_COLLECTIONS

                collection = self._collections[collection_name] = (
                    self.db.collection(collection_name)
                    if self.db.has_collection(collection_name)
                    else self.db.create_collection(
                        collection_name,
                        edge=is_edge,
                        schema=schema
                    )
                )

                # Update schema if collection exists and has a schema
                if self.db.has_collection(collection_name) and schema:
                    try:
                        self.logger.info(f"Updating schema for collection {collection_name}")
                        collection.configure(schema=schema)
                    except Exception as e:
                        error_msg = str(e)
                        if "1207" in error_msg or "duplicate" in error_msg.lower():
                            # Schema already applied - this is expected on restarts
                            self.logger.info(
                                f"✅ Schema for '{collection_name}' already configured, skipping"
                            )
                        else:
                            self.logger.warning(
                                f"Failed to update schema for {collection_name}: {error_msg}"
                            )

            self.logger.info("✅ Collections initialized successfully")

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize collections: {str(e)}")
            raise

    async def _create_graph(self) -> None:
        """Create the knowledge base graph with all required edge definitions"""
        graph_name = GraphNames.KNOWLEDGE_GRAPH.value

        try:
            self.logger.info("🚀 Creating knowledge base graph...")
            graph = self.db.create_graph(graph_name)

            # Create all edge definitions
            created_count = 0
            for edge_def in EDGE_DEFINITIONS:
                try:
                    # Check if edge collection exists before creating edge definition
                    if self.db.has_collection(edge_def["edge_collection"]):
                        graph.create_edge_definition(**edge_def)
                        created_count += 1
                        self.logger.info(f"✅ Created edge definition for {edge_def['edge_collection']}")
                    else:
                        self.logger.warning(f"⚠️ Skipping edge definition for non-existent collection: {edge_def['edge_collection']}")
                except Exception as e:
                    self.logger.error(f"❌ Failed to create edge definition for {edge_def['edge_collection']}: {str(e)}")
                    # Continue with other edge definitions

            self.logger.info(f"✅ Knowledge base graph created successfully with {created_count} edge definitions")

        except Exception as e:
            self.logger.error(f"❌ Failed to create knowledge base graph: {str(e)}")
            raise

    async def connect(self) -> bool:
        """Connect to ArangoDB. Schema initialization is controlled separately."""
        try:
            self.logger.info("🚀 Connecting to ArangoDB...")
            arangodb_config = await self.config_service.get_config(
                config_node_constants.ARANGODB.value
            )
            arango_url = arangodb_config["url"]
            arango_user = arangodb_config["username"]
            arango_password = arangodb_config["password"]
            arango_db = arangodb_config["db"]

            if not isinstance(arango_url, str):
                raise ValueError("ArangoDB URL must be a string")
            if not self.client:
                return False

            # Connect to system db to ensure our db exists
            self.logger.debug("Connecting to system db")
            sys_db = self.client.db(
                "_system", username=arango_user, password=arango_password, verify=True
            )
            self.logger.debug("System DB: %s", sys_db)

            # Check if database exists, but don't try to create if it does
            self.logger.debug("Checking if our database exists")
            if not sys_db.has_database(arango_db):
                try:
                    self.logger.info(
                        "🚀 Database %s does not exist. Creating...", arango_db
                    )
                    sys_db.create_database(arango_db)
                    self.logger.info("✅ Database created successfully")
                except Exception as e:
                    # If database creation fails but database exists, we can continue
                    if "duplicate database name" not in str(e):
                        raise
                    self.logger.warning(
                        "Database already exists, continuing with connection"
                    )

            # Connect to our database
            self.logger.debug("Connecting to our database")
            self.db = self.client.db(
                arango_db, username=arango_user, password=arango_password, verify=True
            )
            self.logger.debug("Our DB: %s", self.db)

            if self.enable_schema_init:
                await self.initialize_schema()

            return True

        except Exception as e:
            self.logger.error("❌ Failed to connect to ArangoDB: %s", str(e))
            self.client = None
            self.db = None
            # Reset collections
            for collection in self._collections:
                self._collections[collection] = None
            return False

    async def _initialize_departments(self) -> None:
        """Initialize departments collection with predefined department types"""
        departments = [
            {
                "_key": str(uuid.uuid4()),
                "departmentName": dept.value,
                "orgId": None,
            }
            for dept in DepartmentNames
        ]

        # Bulk insert departments if not already present
        existing_department_names = set(
            doc["departmentName"]
            for doc in self._collections[CollectionNames.DEPARTMENTS.value].all()
        )

        new_departments = [
            dept
            for dept in departments
            if dept["departmentName"] not in existing_department_names
        ]

        if new_departments:
            self.logger.info(f"🚀 Inserting {len(new_departments)} departments")
            self._collections[CollectionNames.DEPARTMENTS.value].insert_many(
                new_departments
            )
            self.logger.info("✅ Departments initialized successfully")

    async def initialize_schema(self) -> None:
        """
        Initialize ArangoDB schema (collections, graph, departments).
        Should be called only from the connector service during startup.
        """
        if not self.enable_schema_init:
            self.logger.info("📦 Schema initialization disabled for this service instance. Skipping.")
            return

        if not self.db:
            raise RuntimeError("Cannot initialize schema: database connection is not established.")

        try:
            self.logger.info("🚀 Initializing ArangoDB schema (collections, graph, departments)...")

            # Initialize all collections (both nodes and edges)
            await self._initialize_new_collections()

            # Initialize or update the knowledge graph
            if not self.db.has_graph(LegacyGraphNames.FILE_ACCESS_GRAPH.value) and not self.db.has_graph(
                GraphNames.KNOWLEDGE_GRAPH.value
            ):
                await self._create_graph()
            else:
                self.logger.info("Knowledge base graph already exists - skipping creation")

            # Initialize departments
            try:
                await self._initialize_departments()
            except Exception as e:
                self.logger.error("❌ Error initializing departments: %s", str(e))
                raise

            self.logger.info("✅ ArangoDB schema initialization completed successfully")

        except Exception as e:
            self.logger.error("❌ Error during schema initialization: %s", str(e))
            raise

    async def disconnect(self) -> bool | None:
        """Disconnect from ArangoDB"""
        try:
            self.logger.info("🚀 Disconnecting from ArangoDB")
            if self.client:
                self.client.close()
            self.logger.info("✅ Disconnected from ArangoDB successfully")
        except Exception as e:
            self.logger.error("❌ Failed to disconnect from ArangoDB: %s", str(e))
            return False

    async def get_org_apps(self, org_id: str) -> list:
        """Get all apps associated with an organization"""
        try:
            query = f"""
            FOR app IN OUTBOUND
                '{CollectionNames.ORGS.value}/{org_id}'
                {CollectionNames.ORG_APP_RELATION.value}
            FILTER app.isActive == true
            RETURN app
            """
            cursor = self.db.aql.execute(query)
            return list(cursor)
        except Exception as e:
            self.logger.error(f"Failed to get org apps: {str(e)}")
            raise

    async def get_user_apps(self, user_id: str) -> list:
        """Get all apps associated with a user"""
        try:
            query = f"""
            FOR app IN OUTBOUND
                '{CollectionNames.USERS.value}/{user_id}'
                {CollectionNames.USER_APP_RELATION.value}
            RETURN app
            """
            cursor = self.db.aql.execute(query)
            return list(cursor)
        except Exception as e:
            self.logger.error(f"Failed to get user apps: {str(e)}")
            raise

    async def _get_user_app_ids(self, user_id: str) -> List[str]:
        """Gets a list of accessible app connector IDs for a user."""
        try:
            user_app_docs = await self.get_user_apps(user_id)
            # Filter out None values and apps without _key before accessing _key
            user_apps = [app['_key'] for app in user_app_docs if app and app.get('_key')]
            self.logger.debug(f"User has access to {len(user_apps)} apps: {user_apps}")
            return user_apps
        except Exception as e:
            self.logger.error(f"Failed to get user app ids: {str(e)}")
            raise

    async def get_all_orgs(self, active: bool = True) -> list:
        """Get all organizations, optionally filtering by active status."""
        try:
            query = f"""
            FOR org IN {CollectionNames.ORGS.value}
            FILTER @active == false || org.isActive == true
            RETURN org
            """

            bind_vars = {"active": active}

            cursor = self.db.aql.execute(query, bind_vars=bind_vars)
            return list(cursor)
        except Exception as e:
            self.logger.error(f"Failed to get organizations: {str(e)}")
            raise

    async def get_document(self, document_key: str, collection: str, transaction: Optional[TransactionDatabase] = None) -> Optional[Dict]:
        """Get a document by its key"""
        try:
            query = """
            FOR doc IN @@collection
                FILTER doc._key == @document_key
                RETURN doc
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query,
                bind_vars={"document_key": document_key, "@collection": collection},
            )
            result = list(cursor)
            return result[0] if result else None
        except Exception as e:
            self.logger.error("❌ Error getting document: %s", str(e))
            return None

    async def get_connector_stats(
        self,
        org_id: str,
        connector_id: str,
    ) -> Dict:
        """
        Get connector statistics for a specific connector or knowledge base

        Args:
            org_id: Organization ID
            connector: Specific connector name (e.g., "GOOGLE_DRIVE", "SLACK").
                        If None, returns Knowledge Base stats
        """
        try:
            self.logger.info(f"Getting connector stats for organization: {org_id}, connector: {connector_id or 'KNOWLEDGE_BASE'}")

            db = self.db

            query = """
            LET org_id = @org_id
            LET connector = FIRST(
                FOR doc IN @@apps
                    FILTER doc._key == @connector_id
                    RETURN doc
            )

            // Get all records for the specific connector (excluding folders)
            LET records = (
                FOR doc IN @@records
                    FILTER doc.orgId == org_id
                    FILTER doc.origin == "CONNECTOR"
                    FILTER doc.connectorId == @connector_id
                    FILTER doc.recordType != @drive_record_type
                    FILTER doc.isDeleted != true

                    // Filter out folders by checking the connected file document via isOfType edge
                    LET targetDoc = FIRST(
                        FOR v IN 1..1 OUTBOUND doc._id @@is_of_type
                            LIMIT 1
                            RETURN v
                    )

                    // A record is valid if it's not a file, or if it is a file and not a folder.
                    FILTER targetDoc == null OR NOT IS_SAME_COLLECTION("files", targetDoc._id) OR targetDoc.isFile == true
                    RETURN doc
            )

            // Overall stats
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

            // Record type breakdown
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

            # Execute the query
            cursor = db.aql.execute(query, bind_vars=bind_vars)
            result = next(cursor, None)

            if result:
                self.logger.info(f"Retrieved stats for {connector_id} in organization: {org_id}")
                return {
                    "success": True,
                    "data": result
                }
            else:
                self.logger.warning(f"No data found for connector: {connector_id} in organization: {org_id}")
                return {
                    "success": False,
                    "message": "No data found for the specified connector",
                    "data": {
                        "org_id": org_id,
                        "connector_id": connector_id,
                        "origin": "CONNECTOR",
                        "stats": {
                            "total": 0,
                            "indexing_status": {
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
                            }
                        },
                        "by_record_type": []
                    }
                }

        except Exception as e:
            self.logger.error(f"Error getting connector stats: {str(e)}")
            return {
                "success": False,
                "message": str(e),
                "data": None
            }

    # TODO: Update group permission fetch
    async def check_record_access_with_details(
        self, user_id: str, org_id: str, record_id: str
    ) -> Optional[Dict]:
        """
        Check record access and return record details if accessible
        Args:
            user_id (str): The userId field value in users collection
            org_id (str): The organization ID
            record_id (str): The record ID to check access for
        Returns:
            dict: Record details with permissions if accessible, None if not
        """
        try:

            # Get user document to extract _key
            user = await self.get_user_by_user_id(user_id)
            if not user:
                self.logger.warning(f"User not found for userId: {user_id}")
                return None

            user_key=user.get('_key')

            # Get user's accessible apps and extract connector IDs (_key)
            user_apps_ids = await self._get_user_app_ids(user_key)

            # Build app record filter for connector records
            app_record_filter = 'FILTER record.origin != "CONNECTOR" OR record.connectorId IN @user_apps_ids'

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
                FOR record, edge IN 1..1 ANY userDoc._id {CollectionNames.PERMISSION.value}
                FILTER record._key == @recordId
                {app_record_filter}
                RETURN {{
                    type: 'DIRECT',
                    source: userDoc,
                    role: edge.role
                }}
            )
            LET groupAccessPermissionEdge = (
                FOR group, belongsEdge IN 1..1 ANY userDoc._id {CollectionNames.PERMISSION.value}
                FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)
                FOR record, permEdge IN 1..1 ANY group._id {CollectionNames.PERMISSION.value}
                FILTER record._key == @recordId
                {app_record_filter}
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
                {app_record_filter}

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
                    {app_record_filter}

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
                        {app_record_filter}

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
                FOR record, permEdge IN 1..1 ANY org._id {CollectionNames.PERMISSION.value}
                FILTER record._key == @recordId
                {app_record_filter}
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
                            {app_record_filter}

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
                "user_apps_ids": user_apps_ids,
                "@users": CollectionNames.USERS.value,
                "records": CollectionNames.RECORDS.value,
                "files": CollectionNames.FILES.value,
                "@anyone": CollectionNames.ANYONE.value,
                "@belongs_to": CollectionNames.BELONGS_TO.value,
                "@permission": CollectionNames.PERMISSION.value,
                "@record_relations": CollectionNames.RECORD_RELATIONS.value,
            }

            cursor = self.db.aql.execute(access_query, bind_vars=bind_vars)
            access_result = next(cursor, None)

            if not access_result:
                return None

            # If we have access, get the complete record details
            record = await self.get_document(record_id, CollectionNames.RECORDS.value)
            if not record:
                return None

            user = await self.get_user_by_user_id(user_id)

            # Get file or mail details based on record type
            additional_data = None
            if record["recordType"] == RecordTypes.FILE.value:
                additional_data = await self.get_document(
                    record_id, CollectionNames.FILES.value
                )
            elif record["recordType"] == RecordTypes.MAIL.value:
                additional_data = await self.get_document(
                    record_id, CollectionNames.MAILS.value
                )
                message_id = record["externalRecordId"]
                # Format the webUrl with the user's email
                additional_data["webUrl"] = (
                    f"https://mail.google.com/mail?authuser={user['email']}#all/{message_id}"
                )
            elif record["recordType"] == RecordTypes.TICKET.value:
                additional_data = await self.get_document(
                    record_id, CollectionNames.TICKETS.value
                )

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
            metadata_cursor = self.db.aql.execute(
                metadata_query, bind_vars={"recordId": record_id}
            )
            metadata_result = next(metadata_cursor, None)

            # Get knowledge base info if record is in a KB
            kb_info = None
            folder_info = None
            for access in access_result:
                if access["type"] in ["KNOWLEDGE_BASE", "KNOWLEDGE_BASE_TEAM"]:
                    kb = access["source"]
                    kb_info = {
                        "id": kb["_key"],
                        "name": kb.get("groupName"),
                        "orgId": kb["orgId"],
                    }
                    if access.get("folder"):
                        folder = access["folder"]
                        folder_info = {
                            "id": folder["_key"],
                            "name": folder["name"]
                        }
                    break

            # Format permissions from access paths
            permissions = []
            for access in access_result:
                permission = {
                    "id": record["_key"],
                    "name": record["recordName"],
                    "type": record["recordType"],
                    "relationship": access["role"],
                    "accessType": access["type"],
                }
                permissions.append(permission)

            return {
                "record": {
                    **record,
                    "fileRecord": (
                        additional_data
                        if record["recordType"] == RecordTypes.FILE.value
                        else None
                    ),
                    "mailRecord": (
                        additional_data
                        if record["recordType"] == RecordTypes.MAIL.value
                        else None
                    ),
                    "ticketRecord": (
                        additional_data
                        if record["recordType"] == RecordTypes.TICKET.value
                        else None
                    ),
                },
                "knowledgeBase": kb_info,
                "folder": folder_info,
                "metadata": metadata_result,
                "permissions": permissions,
            }

        except Exception as e:
            self.logger.error(
                f"Failed to check record access and get details: {str(e)}"
            )
            raise

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
        List all records the user can access directly via belongs_to edges.
        Returns (records, total_count, available_filters)
        """
        try:
            self.logger.info(f"🔍 Listing all records for user {user_id}, source: {source}")

            # Determine what data sources to include
            include_kb_records = source in ['all', 'local']
            include_connector_records = source in ['all', 'connector']

            # Get user's accessible apps and extract connector IDs (_key)
            user_apps_ids = await self._get_user_app_ids(user_id)

            # Build filter conditions function
            def build_record_filters(include_filter_vars: bool = True) -> str:
                conditions = []
                if search and include_filter_vars:
                    conditions.append("(LIKE(LOWER(record.recordName), @search) OR LIKE(LOWER(record.externalRecordId), @search))")
                if record_types and include_filter_vars:
                    conditions.append("record.recordType IN @record_types")
                if origins and include_filter_vars:
                    conditions.append("record.origin IN @origins")
                if connectors and include_filter_vars:
                    conditions.append("record.connectorId IN @connectors")
                if indexing_status and include_filter_vars:
                    conditions.append("record.indexingStatus IN @indexing_status")
                if date_from and include_filter_vars:
                    conditions.append("record.createdAtTimestamp >= @date_from")
                if date_to and include_filter_vars:
                    conditions.append("record.createdAtTimestamp <= @date_to")

                return " AND " + " AND ".join(conditions) if conditions else ""

            base_kb_roles = {"OWNER", "READER", "FILEORGANIZER", "WRITER", "COMMENTER", "ORGANIZER"}
            if permissions:
                final_kb_roles = list(base_kb_roles.intersection(set(permissions)))
                if not final_kb_roles:
                    include_kb_records = False
            else:
                final_kb_roles = list(base_kb_roles)

            # Build filters
            record_filter = build_record_filters(True)
            permission_filter = "FILTER permissionEdge.role IN @role_filter" if permissions else ""
            perm_edge_filter = "FILTER permEdge.role IN @role_filter" if permissions else ""
            record_group_edge_filter = "FILTER recordGroupToRecordEdge.role IN @role_filter" if permissions else ""
            child_rg_edge_filter = "FILTER childRgToRecordEdge.role IN @role_filter" if permissions else ""
            #filter folders out of the all records query
            folder_filter = '''
                LET targetDoc = FIRST(
                    FOR v IN 1..1 OUTBOUND record._id isOfType
                        LIMIT 1
                        RETURN v
                )

                // If the record connects to a file collection, verify isFile == true
                // For any other type (webpage, ticket, etc.), automatically accept
                LET isValidRecord = (
                    targetDoc != null AND IS_SAME_COLLECTION("files", targetDoc._id)
                        ? targetDoc.isFile == true
                        : true  // Not a file (webpage, ticket, etc.) - accept it
                )

                FILTER isValidRecord
            '''

            #filter records that match the user's app connector_ids
            app_record_filter = 'FILTER record.connectorId IN @user_apps_ids'

            main_query = f"""
            LET user_from = @user_from
            LET org_id = @org_id

            // Direct user permissions to KBs
            LET directKbAccess = (
                FOR kbEdge IN @@permission
                    FILTER kbEdge._from == user_from
                    FILTER kbEdge.type == "USER"
                    FILTER STARTS_WITH(kbEdge._to, "recordGroups/")
                    LET kb = DOCUMENT(kbEdge._to)
                    FILTER kb != null AND kb.orgId == org_id
                    RETURN {{
                        kb_id: kb._key,
                        kb_doc: kb,
                        role: kbEdge.role,
                        access_type: "direct"
                    }}
            )

            // Team-based access to KBs: User -> Team -> KB
            LET teamKbAccess = (
                FOR teamKbPerm IN @@permission
                    FILTER teamKbPerm.type == "TEAM"
                    FILTER STARTS_WITH(teamKbPerm._to, "recordGroups/")
                    LET kb = DOCUMENT(teamKbPerm._to)
                    FILTER kb != null AND kb.orgId == org_id
                    LET team_id = SPLIT(teamKbPerm._from, '/')[1]

                    // Check if user is a member of this team
                    LET user_team_role = FIRST(
                        FOR userTeamPerm IN @@permission
                            FILTER userTeamPerm._from == user_from
                            FILTER userTeamPerm._to == CONCAT('teams/', team_id)
                            FILTER userTeamPerm.type == "USER"
                            RETURN userTeamPerm.role
                    )

                    FILTER user_team_role != null

                    RETURN {{
                        kb_id: kb._key,
                        kb_doc: kb,
                        role: user_team_role,
                        access_type: "team"
                    }}
            )

            // Combine direct and team access, prioritizing direct access
            LET allKbAccess = UNION(
                (FOR access IN directKbAccess RETURN access),
                (FOR teamAccess IN teamKbAccess
                    LET hasDirect = LENGTH(
                        FOR direct IN directKbAccess
                            FILTER direct.kb_id == teamAccess.kb_id
                            RETURN 1
                    ) > 0
                    FILTER NOT hasDirect
                    RETURN teamAccess)
            )

            // Filter by requested permission roles
            LET filteredKbAccess = (
                FOR access IN allKbAccess
                    FILTER access.role IN @kb_permissions
                    RETURN access
            )

            // KB Records Section - Get records from all accessible KBs
            LET kbRecords = {
                f'''(
                    FOR access IN filteredKbAccess
                        LET kb = access.kb_doc
                        // Get records that belong directly to the KB
                        FOR belongsEdge IN @@belongs_to
                            FILTER belongsEdge._to == kb._id
                            LET record = DOCUMENT(belongsEdge._from)
                            FILTER record != null
                            FILTER record.isDeleted != true
                            FILTER record.orgId == org_id OR record.orgId == null
                            FILTER record.origin == "UPLOAD"

                            // Only include actual records (not folders) - check FILES document via IS_OF_TYPE edge
                            LET targetDoc = FIRST(
                                FOR v IN 1..1 OUTBOUND record._id @@is_of_type
                                    LIMIT 1
                                    RETURN v
                            )

                            // If the record connects to a file collection, verify isFile == true
                            // For any other type (webpage, ticket, etc.), automatically accept
                            LET isValidRecord = (
                                targetDoc != null AND IS_SAME_COLLECTION("files", targetDoc._id)
                                    ? targetDoc.isFile == true
                                    : true  // Not a file (webpage, ticket, etc.) - accept it
                            )

                            FILTER isValidRecord
                            {record_filter}
                            RETURN {{
                                record: record,
                                permission: {{ role: access.role, type: access.access_type == "team" ? "TEAM" : "USER" }},
                                kb_id: kb._key,
                                kb_name: kb.groupName
                            }}
                )''' if include_kb_records else '[]'
            }

            // Connector Records Section - Direct connector permissions

            LET connectorRecordsNewPermission = {
                f'''(
                    FOR permissionEdge IN @@permission
                        FILTER permissionEdge._from == user_from
                        FILTER permissionEdge.type == "USER"
                        {permission_filter}
                        LET record = DOCUMENT(permissionEdge._to)
                        FILTER record != null
                        FILTER record.recordType != @drive_record_type
                        FILTER record.isDeleted != true
                        FILTER record.orgId == org_id OR record.orgId == null
                        FILTER record.origin == "CONNECTOR"

                        {app_record_filter}
                        {folder_filter}
                        {record_filter}
                        RETURN {{
                            record: record,
                            permission: {{ role: permissionEdge.role, type: permissionEdge.type }}
                        }}
                )''' if include_connector_records else '[]'
            }

            LET groupConnectorRecordsNewPermission = {
                f'''(
                    FOR group, userToGroupEdge IN 1..1 ANY user_from @@permission
                        FILTER userToGroupEdge.type == "USER"
                        FILTER IS_SAME_COLLECTION("groups", group) or IS_SAME_COLLECTION("roles", group)

                        FOR record, permissionEdge IN 1..1 ANY group._id @@permission
                            FILTER permissionEdge.type == "GROUP" or permissionEdge.type == "ROLE"
                            {permission_filter}

                            FILTER record != null
                            FILTER record.recordType != @drive_record_type
                            FILTER record.isDeleted != true
                            FILTER record.orgId == org_id OR record.orgId == null
                            FILTER record.origin == "CONNECTOR"

                            {app_record_filter}
                            {folder_filter}
                            {record_filter}

                            RETURN {{
                                record: record,
                                permission: {{ role: permissionEdge.role, type: permissionEdge.type }}
                            }}
                )''' if include_connector_records else '[]'
            }

            LET orgAccessPermission = {
                f'''(
                    FOR org, belongsEdge IN 1..1 ANY user_from @@belongs_to
                        FILTER belongsEdge.entityType == "ORGANIZATION"
                        FOR record, permEdge IN 1..1 ANY org._id @@permission
                            FILTER permEdge.type == "ORG"
                            {perm_edge_filter}
                            FILTER record != null
                            FILTER record.recordType != @drive_record_type
                            FILTER record.isDeleted != true
                            FILTER record.orgId == org_id OR record.orgId == null
                            FILTER record.origin == "CONNECTOR"

                            {app_record_filter}
                            {folder_filter}
                            {record_filter}
                            RETURN {{
                                record: record,
                                permission: {{ role: permEdge.role, type: permEdge.type }}
                            }}
                )''' if include_connector_records else '[]'
            }

            LET orgRecordGroupAccess = {
                f'''(
                    // User -> Organization -> Record Group -> Record (with nested record groups support)
                    FOR org, belongsEdge IN 1..1 ANY user_from @@belongs_to
                        FILTER belongsEdge.entityType == "ORGANIZATION"

                        // Org -> record_group permission
                        FOR recordGroup, orgToRgEdge IN 1..1 ANY org._id @@permission
                            FILTER orgToRgEdge.type == "ORG"
                            FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)

                            // Record group -> nested record groups (0 to 2 levels) -> record
                            FOR record, edge, path IN 0..2 INBOUND recordGroup._id @@inherit_permissions
                                // Only process if final vertex is a record (not another record group)
                                FILTER IS_SAME_COLLECTION("records", record)

                                FILTER record != null
                                FILTER record.recordType != @drive_record_type
                                FILTER record.isDeleted != true
                                FILTER record.orgId == org_id OR record.orgId == null
                                FILTER record.origin == "CONNECTOR"

                                {app_record_filter}
                                {folder_filter}
                                {record_filter}

                                // Get the role from the last edge in the path (the one connecting to the record)
                                LET finalEdge = LENGTH(path.edges) > 0 ? path.edges[LENGTH(path.edges) - 1] : edge

                                RETURN {{
                                    record: record,
                                    permission: {{
                                        role: finalEdge.role,
                                        type: finalEdge.type
                                    }}
                                }}
                )''' if include_connector_records else '[]'
            }

            LET recordGroupConnectorRecords = {
                f'''(
                    // First hop: user -> group
                    FOR group, userToGroupEdge IN 1..1 ANY user_from @@permission
                        FILTER userToGroupEdge.type == "USER"
                        FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)

                        // Second hop: group -> recordgroup
                        FOR recordGroup, groupToRecordGroupEdge IN 1..1 ANY group._id @@permission
                            FILTER groupToRecordGroupEdge.type == "GROUP" or groupToRecordGroupEdge.type == "ROLE"

                            // Third hop: recordgroup -> record
                            FOR record, recordGroupToRecordEdge IN 1..1 INBOUND recordGroup._id @@inherit_permissions
                                // Assuming the edge from recordgroup to record should also be filtered
                                {record_group_edge_filter}

                                FILTER record != null
                                FILTER record.recordType != @drive_record_type
                                FILTER record.isDeleted != true
                                FILTER record.orgId == org_id OR record.orgId == null
                                FILTER record.origin == "CONNECTOR"

                                {app_record_filter}
                                {folder_filter}
                                {record_filter}

                                RETURN {{
                                    record: record,
                                    permission: {{
                                        role: recordGroupToRecordEdge.role,
                                        type: recordGroupToRecordEdge.type
                                    }}
                                }}
                )''' if include_connector_records else '[]'
            }

            LET inheritedRecordGroupConnectorRecords = {
                f'''(
                    // Hop 1: user -> group
                    FOR group, userToGroupEdge IN 1..1 ANY user_from @@permission
                        FILTER userToGroupEdge.type == "USER"
                        FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)

                    // Hop 2: group -> parent record_group
                    FOR parentRecordGroup, groupToRgEdge IN 1..1 ANY group._id @@permission
                        FILTER groupToRgEdge.type == "GROUP" or groupToRgEdge.type == "ROLE"

                    // Hop 3: parent record_group -> child record_group
                    FOR childRecordGroup, rgToRgEdge IN 1..1 INBOUND parentRecordGroup._id @@inherit_permissions

                    // Hop 4: child record_group -> record
                    FOR record, childRgToRecordEdge IN 1..1 INBOUND childRecordGroup._id @@inherit_permissions
                        {child_rg_edge_filter}

                        FILTER record != null
                        FILTER record.recordType != @drive_record_type
                        FILTER record.isDeleted != true
                        FILTER record.orgId == org_id OR record.orgId == null
                        FILTER record.origin == "CONNECTOR"

                        {app_record_filter}
                        {folder_filter}
                        {record_filter}

                        RETURN {{
                            record: record,
                            permission: {{
                                role: childRgToRecordEdge.role,
                                type: childRgToRecordEdge.type
                            }}
                        }}
                )''' if include_connector_records else '[]'
            }

            LET directUserToRecordGroupRecords = {
                f'''(
                    // Direct user -> record_group permission (with nested record groups support)
                    FOR recordGroup, userToRgEdge IN 1..1 ANY user_from @@permission
                        FILTER userToRgEdge.type == "USER"
                        FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)

                        // Record group -> nested record groups (0 to 5 levels) -> record
                        FOR record, edge, path IN 0..5 INBOUND recordGroup._id @@inherit_permissions
                            // Only process if final vertex is a record (not another record group)
                            FILTER IS_SAME_COLLECTION("records", record)

                            FILTER record != null
                            FILTER record.recordType != @drive_record_type
                            FILTER record.isDeleted != true
                            FILTER record.orgId == org_id OR record.orgId == null
                            FILTER record.origin == "CONNECTOR"

                            {app_record_filter}
                            {folder_filter}
                            {record_filter}

                            // Get the role from the last edge in the path (the one connecting to the record)
                            LET finalEdge = LENGTH(path.edges) > 0 ? path.edges[LENGTH(path.edges) - 1] : edge

                            RETURN {{
                                record: record,
                                permission: {{
                                    role: finalEdge.role,
                                    type: finalEdge.type
                                }}
                            }}
                )''' if include_connector_records else '[]'
            }

            // Combine all connector/app record sources
            // A record can appear in multiple permission paths:
            // - Direct user permission
            // - Group/role permission
            // - Organization permission
            // - Record group permissions (direct, nested, inherited)
            LET allConnectorRecordsNewPermission = UNION_DISTINCT(
                connectorRecordsNewPermission,
                groupConnectorRecordsNewPermission,
                orgAccessPermission,
                orgRecordGroupAccess,
                recordGroupConnectorRecords,
                inheritedRecordGroupConnectorRecords,
                directUserToRecordGroupRecords
            )
            // Deduplicate connector/app records by record key
            // Even if a record appears via multiple permission paths with different permission metadata,
            // it should only appear once in the final result
            LET allConnectorRecordsDistinct = (
                FOR item IN allConnectorRecordsNewPermission
                    COLLECT recordKey = item.record._key
                    INTO groups
                    RETURN FIRST(groups[*].item)
            )

            // Deduplicate KB records (a record can appear in multiple KBs)
            LET kbRecordsDistinct = (
                FOR item IN kbRecords
                    COLLECT recordKey = item.record._key
                    INTO groups
                    RETURN FIRST(groups[*].item)
            )

            // Combine KB and connector records
            // Note: These are mutually exclusive (KB records have origin="UPLOAD", connector records have origin="CONNECTOR")
            // So no deduplication needed when combining them
            LET allRecords = APPEND(kbRecordsDistinct, allConnectorRecordsDistinct)

            // Sort with secondary key for deterministic ordering
            // When sort_by values are equal, use record._key as tiebreaker
            // For string fields (recordName), use case-insensitive sorting for consistent ordering across pages
            LET sortedRecords = (
                FOR item IN allRecords
                    LET record = item.record
                    {f'LET sortValue = LOWER(record.{sort_by})' if sort_by == "recordName" else f'LET sortValue = record.{sort_by}'}
                    SORT sortValue {sort_order.upper()}, record._key ASC
                    RETURN item
            )

            FOR item IN sortedRecords
                LIMIT @skip, @limit
                LET record = item.record

                // Get file record for FILE type records
                LET fileRecord = (
                    record.recordType == "FILE" ? (
                        FOR fileEdge IN @@is_of_type
                            FILTER fileEdge._from == record._id
                            LET file = DOCUMENT(fileEdge._to)
                            FILTER file != null
                            RETURN {{
                                id: file._key,
                                name: file.name,
                                extension: file.extension,
                                mimeType: file.mimeType,
                                sizeInBytes: file.sizeInBytes,
                                isFile: file.isFile,
                                webUrl: file.webUrl
                            }}
                    ) : []
                )

                // Get mail record for MAIL type records
                LET mailRecord = (
                    record.recordType == "MAIL" ? (
                        FOR mailEdge IN @@is_of_type
                            FILTER mailEdge._from == record._id
                            LET mail = DOCUMENT(mailEdge._to)
                            FILTER mail != null
                            RETURN {{
                                id: mail._key,
                                messageId: mail.messageId,
                                threadId: mail.threadId,
                                subject: mail.subject,
                                from: mail.from,
                                to: mail.to,
                                cc: mail.cc,
                                bcc: mail.bcc,
                                body: mail.body,
                                webUrl: mail.webUrl
                            }}
                    ) : []
                )

                LET ticketRecord = (
                    record.recordType == "TICKET" ? (
                        FOR ticketEdge IN @@is_of_type
                            FILTER ticketEdge._from == record._id
                            LET ticket = DOCUMENT(ticketEdge._to)
                            FILTER ticket != null
                            RETURN ticket
                    ) : []
                )

                RETURN {{
                    id: record._key,
                    externalRecordId: record.externalRecordId,
                    externalRevisionId: record.externalRevisionId,
                    recordName: record.recordName,
                    recordType: record.recordType,
                    origin: record.origin,
                    connectorName: record.connectorName || "KNOWLEDGE_BASE",
                    indexingStatus: record.indexingStatus,
                    createdAtTimestamp: record.createdAtTimestamp,
                    updatedAtTimestamp: record.updatedAtTimestamp,
                    sourceCreatedAtTimestamp: record.sourceCreatedAtTimestamp,
                    sourceLastModifiedTimestamp: record.sourceLastModifiedTimestamp,
                    orgId: record.orgId,
                    version: record.version,
                    isDeleted: record.isDeleted,
                    deletedByUserId: record.deletedByUserId,
                    isLatestVersion: record.isLatestVersion != null ? record.isLatestVersion : true,
                    webUrl: record.webUrl,
                    sizeInBytes: record.sizeInBytes,
                    fileRecord: LENGTH(fileRecord) > 0 ? fileRecord[0] : null,
                    mailRecord: LENGTH(mailRecord) > 0 ? mailRecord[0] : null,
                    ticketRecord: LENGTH(ticketRecord) > 0 ? ticketRecord[0] : null,
                    permission: {{role: item.permission.role, type: item.permission.type}},
                    kb: {{id: item.kb_id || null, name: item.kb_name || null }}
                }}
            """

            # ===== COUNT QUERY (Fixed) =====
            count_query = f"""
            LET user_from = @user_from
            LET org_id = @org_id

            // Direct user permissions to KBs
            LET directKbAccess = (
                FOR kbEdge IN @@permission
                    FILTER kbEdge._from == user_from
                    FILTER kbEdge.type == "USER"
                    FILTER STARTS_WITH(kbEdge._to, "recordGroups/")
                    LET kb = DOCUMENT(kbEdge._to)
                    FILTER kb != null AND kb.orgId == org_id
                    RETURN {{
                        kb_id: kb._key,
                        kb_doc: kb,
                        role: kbEdge.role
                    }}
            )

            // Team-based access to KBs
            LET teamKbAccess = (
                FOR teamKbPerm IN @@permission
                    FILTER teamKbPerm.type == "TEAM"
                    FILTER STARTS_WITH(teamKbPerm._to, "recordGroups/")
                    LET kb = DOCUMENT(teamKbPerm._to)
                    FILTER kb != null AND kb.orgId == org_id
                    LET team_id = SPLIT(teamKbPerm._from, '/')[1]

                    LET user_team_role = FIRST(
                        FOR userTeamPerm IN @@permission
                            FILTER userTeamPerm._from == user_from
                            FILTER userTeamPerm._to == CONCAT('teams/', team_id)
                            FILTER userTeamPerm.type == "USER"
                            RETURN userTeamPerm.role
                    )

                    FILTER user_team_role != null

                    RETURN {{
                        kb_id: kb._key,
                        kb_doc: kb,
                        role: user_team_role
                    }}
            )

            // Combine and filter by role
            LET allKbAccess = UNION(
                (FOR access IN directKbAccess RETURN access),
                (FOR teamAccess IN teamKbAccess
                    LET hasDirect = LENGTH(
                        FOR direct IN directKbAccess
                            FILTER direct.kb_id == teamAccess.kb_id
                            RETURN 1
                    ) > 0
                    FILTER NOT hasDirect
                    RETURN teamAccess)
            )

            LET filteredKbAccess = (
                FOR access IN allKbAccess
                    FILTER access.role IN @kb_permissions
                    RETURN access
            )

            // Get unique KB record keys (deduplicate records that appear in multiple KBs)
            LET kbRecordKeys = {
                f'''UNIQUE(
                    FOR access IN filteredKbAccess
                        LET kb = access.kb_doc
                        FOR belongsEdge IN @@belongs_to
                            FILTER belongsEdge._to == kb._id
                            LET record = DOCUMENT(belongsEdge._from)
                            FILTER record != null
                            FILTER record.isDeleted != true
                            FILTER record.orgId == org_id OR record.orgId == null
                            FILTER record.origin == "UPLOAD"

                            // Only include actual records (not folders) - check FILES document via IS_OF_TYPE edge
                            LET targetDoc = FIRST(
                                FOR v IN 1..1 OUTBOUND record._id @@is_of_type
                                    LIMIT 1
                                    RETURN v
                            )

                            // If the record connects to a file collection, verify isFile == true
                            // For any other type (webpage, ticket, etc.), automatically accept
                            LET isValidRecord = (
                                targetDoc != null AND IS_SAME_COLLECTION("files", targetDoc._id)
                                    ? targetDoc.isFile == true
                                    : true  // Not a file (webpage, ticket, etc.) - accept it
                            )

                            FILTER isValidRecord
                            {record_filter}
                            RETURN record._key
                )''' if include_kb_records else '[]'
            }

            LET connectorKeysNewPermission = {
                f'''(
                    FOR permissionEdge IN @@permission
                        FILTER permissionEdge._from == user_from
                        FILTER permissionEdge.type == "USER"
                        {permission_filter}
                        LET record = DOCUMENT(permissionEdge._to)
                        FILTER record != null
                        FILTER record.recordType != @drive_record_type
                        FILTER record.isDeleted != true
                        FILTER record.orgId == org_id OR record.orgId == null
                        FILTER record.origin == "CONNECTOR"

                        {app_record_filter}
                        {folder_filter}
                        {record_filter}
                        RETURN record._key
                )''' if include_connector_records else '[]'
            }

            LET groupConnectorKeysNewPermission = {
                f'''(
                    FOR group, userToGroupEdge IN 1..1 ANY user_from @@permission
                        FILTER userToGroupEdge.type == "USER"
                        FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)

                        FOR record, permissionEdge IN 1..1 ANY group._id @@permission
                            FILTER permissionEdge.type == "GROUP" or permissionEdge.type == "ROLE"
                            {permission_filter}

                            FILTER record != null
                            FILTER record.recordType != @drive_record_type
                            FILTER record.isDeleted != true
                            FILTER record.orgId == org_id OR record.orgId == null
                            FILTER record.origin == "CONNECTOR"

                            {app_record_filter}
                            {folder_filter}
                            {record_filter}
                            RETURN record._key
                )''' if include_connector_records else '[]'
            }

            LET orgAccessKeys = {
                f'''(
                    FOR org, belongsEdge IN 1..1 ANY user_from @@belongs_to
                        FILTER belongsEdge.entityType == "ORGANIZATION"
                        FOR record, permEdge IN 1..1 ANY org._id @@permission
                            FILTER permEdge.type == "ORG"
                            {perm_edge_filter}
                            FILTER record != null
                            FILTER record.recordType != @drive_record_type
                            FILTER record.isDeleted != true
                            FILTER record.orgId == org_id OR record.orgId == null
                            FILTER record.origin == "CONNECTOR"

                            {app_record_filter}
                            {folder_filter}
                            {record_filter}
                            RETURN record._key
                )''' if include_connector_records else '[]'
            }

            LET orgRecordGroupKeys = {
                f'''(
                    // User -> Organization -> Record Group -> Record (with nested record groups support)
                    FOR org, belongsEdge IN 1..1 ANY user_from @@belongs_to
                        FILTER belongsEdge.entityType == "ORGANIZATION"

                        // Org -> record_group permission
                        FOR recordGroup, orgToRgEdge IN 1..1 ANY org._id @@permission
                            FILTER orgToRgEdge.type == "ORG"
                            FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)

                            // Record group -> nested record groups (0 to 2 levels) -> record
                            FOR record, edge, path IN 0..2 INBOUND recordGroup._id @@inherit_permissions
                                // Only process if final vertex is a record (not another record group)
                                FILTER IS_SAME_COLLECTION("records", record)

                                FILTER record != null
                                FILTER record.recordType != @drive_record_type
                                FILTER record.isDeleted != true
                                FILTER record.orgId == org_id OR record.orgId == null
                                FILTER record.origin == "CONNECTOR"

                                {app_record_filter}
                                {folder_filter}
                                {record_filter}

                                RETURN record._key
                )''' if include_connector_records else '[]'
            }

            LET recordGroupConnectorRecordsCount = {
                f'''(
                    // First hop: user -> group
                    FOR group, userToGroupEdge IN 1..1 ANY user_from @@permission
                        FILTER userToGroupEdge.type == "USER"
                        FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)

                        // Second hop: group -> recordgroup
                        FOR recordGroup, groupToRecordGroupEdge IN 1..1 ANY group._id @@permission
                            FILTER groupToRecordGroupEdge.type == "GROUP" or groupToRecordGroupEdge.type == "ROLE"

                            // Third hop: recordgroup -> record
                            FOR record, recordGroupToRecordEdge IN 1..1 INBOUND recordGroup._id @@inherit_permissions
                                {record_group_edge_filter}

                                FILTER record != null
                                FILTER record.recordType != @drive_record_type
                                FILTER record.isDeleted != true
                                FILTER record.orgId == org_id OR record.orgId == null
                                FILTER record.origin == "CONNECTOR"

                                {app_record_filter}
                                {folder_filter}
                                {record_filter}

                                RETURN record._key
                )''' if include_connector_records else '[]'
            }

            LET inheritedRecordGroupConnectorRecordsCount = {
                f'''(
                    // Hop 1: user -> group
                    FOR group, userToGroupEdge IN 1..1 ANY user_from @@permission
                        FILTER userToGroupEdge.type == "USER"
                        FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)

                    // Hop 2: group -> parent record_group
                    FOR parentRecordGroup, groupToRgEdge IN 1..1 ANY group._id @@permission
                        FILTER groupToRgEdge.type == "GROUP" or groupToRgEdge.type == "ROLE"

                    // Hop 3: parent record_group -> child record_group (inheritance)
                    FOR childRecordGroup, rgToRgEdge IN 1..1 INBOUND parentRecordGroup._id @@inherit_permissions

                    // Hop 4: child record_group -> record
                    FOR record, childRgToRecordEdge IN 1..1 INBOUND childRecordGroup._id @@inherit_permissions
                        {child_rg_edge_filter}

                        FILTER record != null
                        FILTER record.recordType != @drive_record_type
                        FILTER record.isDeleted != true
                        FILTER record.orgId == org_id OR record.orgId == null
                        FILTER record.origin == "CONNECTOR"

                        {app_record_filter}
                        {folder_filter}
                        {record_filter}

                        RETURN record._key
                )''' if include_connector_records else '[]'
            }

            LET directUserToRecordGroupKeys = {
                f'''(
                    // Direct user -> record_group permission (with nested record groups support)
                    FOR recordGroup, userToRgEdge IN 1..1 ANY user_from @@permission
                        FILTER userToRgEdge.type == "USER"
                        FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)

                        // Record group -> nested record groups (0 to 5 levels) -> record
                        FOR record, edge, path IN 0..5 INBOUND recordGroup._id @@inherit_permissions
                            // Only process if final vertex is a record (not another record group)
                            FILTER IS_SAME_COLLECTION("records", record)

                            FILTER record != null
                            FILTER record.recordType != @drive_record_type
                            FILTER record.isDeleted != true
                            FILTER record.orgId == org_id OR record.orgId == null
                            FILTER record.origin == "CONNECTOR"

                            {app_record_filter}
                            {folder_filter}
                            {record_filter}

                            RETURN record._key
                )''' if include_connector_records else '[]'
            }

            // Combine all connector record keys and deduplicate
            LET allNewPermissionKeys = UNION_DISTINCT(
                connectorKeysNewPermission,
                groupConnectorKeysNewPermission,
                orgAccessKeys, orgRecordGroupKeys,
                recordGroupConnectorRecordsCount,
                inheritedRecordGroupConnectorRecordsCount,
                directUserToRecordGroupKeys
            )
            LET uniqueNewPermissionCount = LENGTH(UNIQUE(allNewPermissionKeys))

            // Combine KB and connector record keys
            // Note: These are mutually exclusive (KB records have origin="UPLOAD", connector records have origin="CONNECTOR")
            // So we can just add the counts directly
            LET kbCount = LENGTH(kbRecordKeys)
            LET totalCount = kbCount + uniqueNewPermissionCount

            RETURN totalCount
            """

            # ===== FILTERS QUERY (Fixed) =====
            filters_query = f"""
            LET user_from = @user_from
            LET org_id = @org_id

            LET allKbRecords = {
                '''(
                    FOR kbEdge IN @@permission
                        FILTER kbEdge._from == user_from
                        FILTER kbEdge.type == "USER"
                        FILTER kbEdge.role IN ["OWNER", "READER", "FILEORGANIZER", "WRITER", "COMMENTER", "ORGANIZER"]
                        LET kb = DOCUMENT(kbEdge._to)
                        FILTER kb != null AND kb.orgId == org_id
                        FOR belongsEdge IN @@belongs_to
                            FILTER belongsEdge._to == kb._id
                            LET record = DOCUMENT(belongsEdge._from)
                            FILTER record != null
                            FILTER record.isDeleted != true
                            FILTER record.orgId == org_id OR record.orgId == null
                            FILTER record.origin == "UPLOAD"

                            // Only include actual records (not folders) - check FILES document via IS_OF_TYPE edge
                            LET targetDoc = FIRST(
                                FOR v IN 1..1 OUTBOUND record._id @@is_of_type
                                    LIMIT 1
                                    RETURN v
                            )

                            // If the record connects to a file collection, verify isFile == true
                            // For any other type (webpage, ticket, etc.), automatically accept
                            LET isValidRecord = (
                                targetDoc != null AND IS_SAME_COLLECTION("files", targetDoc._id)
                                    ? targetDoc.isFile == true
                                    : true  // Not a file (webpage, ticket, etc.) - accept it
                            )

                            FILTER isValidRecord
                            RETURN {
                                record: record,
                                permission: { role: kbEdge.role }
                            }
                )''' if include_kb_records else '[]'
            }


            LET allConnectorRecordsNewPermission = {
                f'''(
                    FOR permissionEdge IN @@permission
                        FILTER permissionEdge._from == user_from
                        FILTER permissionEdge.type == "USER"
                        LET record = DOCUMENT(permissionEdge._to)
                        FILTER record != null
                        FILTER record.recordType != @drive_record_type
                        FILTER record.isDeleted != true
                        FILTER record.orgId == org_id OR record.orgId == null
                        FILTER record.origin == "CONNECTOR"

                        {folder_filter}
                        RETURN {{
                            record: record,
                            permission: {{ role: permissionEdge.role }}
                        }}
                )''' if include_connector_records else '[]'
            }

            LET allGroupConnectorRecordsNewPermission = {
                f'''(
                    FOR group, userToGroupEdge IN 1..1 ANY user_from @@permission
                        FILTER userToGroupEdge.type == "USER"
                        FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)

                        FOR record, permissionEdge IN 1..1 ANY group._id @@permission
                            FILTER permissionEdge.type == "GROUP" or permissionEdge.type == "ROLE"
                            {permission_filter}

                            FILTER record != null
                            FILTER record.recordType != @drive_record_type
                            FILTER record.isDeleted != true
                            FILTER record.orgId == org_id OR record.orgId == null
                            FILTER record.origin == "CONNECTOR"

                            {app_record_filter}
                            {folder_filter}
                            {record_filter}

                            RETURN {{
                                record: record,
                                permission: {{ role: permissionEdge.role, type: permissionEdge.type }}
                            }}
                )''' if include_connector_records else '[]'
            }

            LET allOrgAccessRecords = {
                '''(
                    FOR org, belongsEdge IN 1..1 ANY user_from @@belongs_to
                        FILTER belongsEdge.entityType == "ORGANIZATION"
                        FOR record, permEdge IN 1..1 ANY org._id @@permission
                            FILTER permEdge.type == "ORG"
                            FILTER record != null
                            FILTER record.recordType != @drive_record_type
                            FILTER record.isDeleted != true
                            FILTER record.orgId == org_id OR record.orgId == null
                            FILTER record.origin == "CONNECTOR"

                            LET targetDoc = FIRST(
                                FOR v IN 1..1 OUTBOUND record._id isOfType
                                    LIMIT 1
                                    RETURN v
                            )

                            LET isValidRecord = (
                                targetDoc != null AND IS_SAME_COLLECTION("files", targetDoc._id)
                                    ? targetDoc.isFile == true
                                    : true
                            )

                            FILTER isValidRecord
                            RETURN {
                                record: record,
                                permission: { role: permEdge.role, type: permEdge.type }
                            }
                )''' if include_connector_records else '[]'
            }

            LET orgRecordGroupRecordsFilter = {
                f'''(
                    // User -> Organization -> Record Group -> Record (with nested record groups support)
                    FOR org, belongsEdge IN 1..1 ANY user_from @@belongs_to
                        FILTER belongsEdge.entityType == "ORGANIZATION"

                        // Org -> record_group permission
                        FOR recordGroup, orgToRgEdge IN 1..1 ANY org._id @@permission
                            FILTER orgToRgEdge.type == "ORG"
                            FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)

                            // Record group -> nested record groups (0 to 2 levels) -> record
                            FOR record, edge, path IN 0..2 INBOUND recordGroup._id @@inherit_permissions
                                // Only process if final vertex is a record (not another record group)
                                FILTER IS_SAME_COLLECTION("records", record)

                                FILTER record != null
                                FILTER record.recordType != @drive_record_type
                                FILTER record.isDeleted != true
                                FILTER record.orgId == org_id OR record.orgId == null
                                FILTER record.origin == "CONNECTOR"

                                {folder_filter}

                                // Get the role from the last edge in the path
                                LET finalEdge = LENGTH(path.edges) > 0 ? path.edges[LENGTH(path.edges) - 1] : edge

                                RETURN {{
                                    record: record,
                                    permission: {{
                                        role: finalEdge.role,
                                        type: finalEdge.type
                                    }}
                                }}
                )''' if include_connector_records else '[]'
            }

            LET recordGroupConnectorRecordsFilter = {
                f'''(
                    FOR group, userToGroupEdge IN 1..1 ANY user_from @@permission
                        FILTER userToGroupEdge.type == "USER"
                        FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)

                        FOR recordGroup, groupToRecordGroupEdge IN 1..1 ANY group._id @@permission
                            FILTER groupToRecordGroupEdge.type == "GROUP" or groupToRecordGroupEdge.type == "ROLE"

                            FOR belongsEdge IN @@inherit_permissions
                                FILTER belongsEdge._to == recordGroup._id
                                LET record = DOCUMENT(belongsEdge._from)

                                FILTER record != null
                                FILTER record.recordType != @drive_record_type
                                FILTER record.isDeleted != true
                                FILTER record.orgId == org_id OR record.orgId == null
                                FILTER record.origin == "CONNECTOR"

                                {app_record_filter}
                                {folder_filter}

                                RETURN {{
                                    record: record,
                                    permission: {{
                                        role: groupToRecordGroupEdge.role,
                                        type: groupToRecordGroupEdge.type
                                    }}
                                }}
                )''' if include_connector_records else '[]'
            }

            LET inheritedRecordGroupConnectorRecordsFilter = {
                f'''(
                    FOR group, userToGroupEdge IN 1..1 ANY user_from @@permission
                        FILTER userToGroupEdge.type == "USER"
                        FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)

                    FOR parentRecordGroup, groupToRgEdge IN 1..1 ANY group._id @@permission
                        FILTER groupToRgEdge.type == "GROUP" or groupToRgEdge.type == "ROLE"

                    FOR childRecordGroup, rgToRgEdge IN 1..1 INBOUND parentRecordGroup._id @@inherit_permissions

                    FOR record, childRgToRecordEdge IN 1..1 INBOUND childRecordGroup._id @@inherit_permissions
                        {child_rg_edge_filter}

                        FILTER record != null
                        FILTER record.recordType != @drive_record_type
                        FILTER record.isDeleted != true
                        FILTER record.orgId == org_id OR record.orgId == null
                        FILTER record.origin == "CONNECTOR"

                        {app_record_filter}
                        {folder_filter}
                        {record_filter}

                        RETURN {{
                            record: record,
                            permission: {{
                                role: childRgToRecordEdge.role,
                                type: childRgToRecordEdge.type
                            }}
                        }}
                )''' if include_connector_records else '[]'
            }

            LET directUserToRecordGroupRecordsFilter = {
                f'''(
                    // Direct user -> record_group permission (with nested record groups support)
                    FOR recordGroup, userToRgEdge IN 1..1 ANY user_from @@permission
                        FILTER userToRgEdge.type == "USER"
                        FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)

                        // Record group -> nested record groups (0 to 5 levels) -> record
                        FOR record, edge, path IN 0..5 INBOUND recordGroup._id @@inherit_permissions
                            // Only process if final vertex is a record (not another record group)
                            FILTER IS_SAME_COLLECTION("records", record)

                            FILTER record != null
                            FILTER record.recordType != @drive_record_type
                            FILTER record.isDeleted != true
                            FILTER record.orgId == org_id OR record.orgId == null
                            FILTER record.origin == "CONNECTOR"

                            {app_record_filter}
                            {folder_filter}

                            // Get the role from the last edge in the path
                            LET finalEdge = LENGTH(path.edges) > 0 ? path.edges[LENGTH(path.edges) - 1] : edge

                            RETURN {{
                                record: record,
                                permission: {{
                                    role: finalEdge.role,
                                    type: finalEdge.type
                                }}
                            }}
                )''' if include_connector_records else '[]'
            }

            LET ConnectorRecords = UNION_DISTINCT(
                allConnectorRecordsNewPermission,
                allGroupConnectorRecordsNewPermission,
                allOrgAccessRecords,
                orgRecordGroupRecordsFilter,
                recordGroupConnectorRecordsFilter,
                inheritedRecordGroupConnectorRecordsFilter,
                directUserToRecordGroupRecordsFilter
            )
            LET allConnectorRecordsDistinct = (
                FOR item IN ConnectorRecords
                    COLLECT recordKey = item.record._key
                    INTO groups
                    RETURN FIRST(groups[*].item)
            )

            //LET mergeRecords = APPEND(allKbRecords, allConnectorRecords)
            //LET mergeRecordsNewPermission = APPEND(mergeRecords, connectorRecordsNewPermission)
            // LET allRecords = APPEND(mergeRecords, allConnectorRecordsDistinct)
            LET allRecords = APPEND(allKbRecords, allConnectorRecordsDistinct)

            LET flatRecords = (
                FOR item IN allRecords
                    RETURN item.record
            )

            LET permissionValues = (
                FOR item IN allRecords
                    FILTER item.permission != null
                    RETURN item.permission.role
            )

            LET connectorValues = (
                FOR record IN flatRecords
                    FILTER record.connectorName != null
                    RETURN record.connectorName
            )

            RETURN {{
                recordTypes: UNIQUE(flatRecords[*].recordType) || [],
                origins: UNIQUE(flatRecords[*].origin) || [],
                connectors: UNIQUE(connectorValues) || [],
                indexingStatus: UNIQUE(flatRecords[*].indexingStatus) || [],
                permissions: UNIQUE(permissionValues) || []
            }}
            """

            # Build bind variables
            filter_bind_vars = {}
            if search:
                filter_bind_vars["search"] = f"%{search.lower()}%"
            if record_types:
                filter_bind_vars["record_types"] = record_types
            if origins:
                filter_bind_vars["origins"] = origins
            if connectors:
                filter_bind_vars["connectors"] = connectors
            if indexing_status:
                filter_bind_vars["indexing_status"] = indexing_status
            if permissions:
                filter_bind_vars["role_filter"] = permissions
            if date_from:
                filter_bind_vars["date_from"] = date_from
            if date_to:
                filter_bind_vars["date_to"] = date_to

            main_bind_vars = {
                "user_from": f"users/{user_id}",
                "org_id": org_id,
                "skip": skip,
                "limit": limit,
                "kb_permissions": final_kb_roles,
                "user_apps_ids": user_apps_ids,
                "@permission": CollectionNames.PERMISSION.value,
                "@belongs_to": CollectionNames.BELONGS_TO.value,
                "@inherit_permissions": CollectionNames.INHERIT_PERMISSIONS.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                "drive_record_type": RecordTypes.DRIVE.value,
                **filter_bind_vars,
            }

            count_bind_vars = {
                "user_from": f"users/{user_id}",
                "org_id": org_id,
                "kb_permissions": final_kb_roles,
                "user_apps_ids": user_apps_ids,
                "@permission": CollectionNames.PERMISSION.value,
                "@belongs_to": CollectionNames.BELONGS_TO.value,
                "@inherit_permissions": CollectionNames.INHERIT_PERMISSIONS.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                "drive_record_type": RecordTypes.DRIVE.value,
                **filter_bind_vars,
            }

            filters_bind_vars = {
                "user_from": f"users/{user_id}",
                "org_id": org_id,
                "user_apps_ids": user_apps_ids,
                "@permission": CollectionNames.PERMISSION.value,
                "@belongs_to": CollectionNames.BELONGS_TO.value,
                "@inherit_permissions": CollectionNames.INHERIT_PERMISSIONS.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                "drive_record_type": RecordTypes.DRIVE.value,
                **filter_bind_vars,
            }

            # Execute queries
            db = self.db
            records = list(db.aql.execute(main_query, bind_vars=main_bind_vars))
            count = list(db.aql.execute(count_query, bind_vars=count_bind_vars))[0]
            available_filters = list(db.aql.execute(filters_query, bind_vars=filters_bind_vars))[0]

            # Ensure filter structure
            if not available_filters:
                available_filters = {}
            available_filters.setdefault("recordTypes", [])
            available_filters.setdefault("origins", [])
            available_filters.setdefault("connectors", [])
            available_filters.setdefault("indexingStatus", [])
            available_filters.setdefault("permissions", [])

            self.logger.info(f"✅ Listed {len(records)} records out of {count} total")
            return records, count, available_filters

        except Exception as e:
            self.logger.error(f"❌ Failed to list all records: {str(e)}")
            return [], 0, {
                "recordTypes": [],
                "origins": [],
                "connectors": [],
                "indexingStatus": [],
                "permissions": []
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
                if not user_role:
                    return {
                        "success": False,
                        "code": 403,
                        "reason": f"Insufficient KB permissions. User role: {user_role}. Required: OWNER, WRITER, READER"
                    }

                connector_type = Connectors.KNOWLEDGE_BASE.value


            elif origin == OriginTypes.CONNECTOR.value:
                # Connector record - check connector-specific permissions
                permission_result = await self._check_record_permissions(record_id, user_key)
                user_role = permission_result.get("permission")

                if not user_role:
                    return {
                        "success": False,
                        "code": 403,
                        "reason": f"Insufficient permissions. User role: {user_role}. Required: OWNER, WRITER, READER"
                    }

                # Check if connector is enabled before allowing reindex
                if connector_id:
                    connector_instance = await self.get_document(connector_id, CollectionNames.APPS.value)
                    if not connector_instance:
                        return {
                            "success": False,
                            "code": 404,
                            "reason": f"Connector not found: {connector_id}"
                        }
                    if not connector_instance.get("isActive", False):
                        return {
                            "success": False,
                            "code": 400,
                            "reason": f"Cannot reindex: connector '{connector_instance.get('name', connector_name)}' is currently disabled. Please enable the connector first."
                        }

                connector_type = connector_name
            else:
                return {
                    "success": False,
                    "code": 400,
                    "reason": f"Unsupported record origin: {origin}"
                }

            # Get file record for event payload
            file_record = await self.get_document(record_id, CollectionNames.FILES.value) if record.get("recordType") == "FILE" else await self.get_document(record_id, CollectionNames.MAILS.value)

            self.logger.info(f"📋 File record: {file_record}")

            # Determine if we should use batch reindex (depth > 0)
            use_batch_reindex = depth != 0

            # Reset indexing status to QUEUED before reindexing
            # This ensures the record will be properly queued and re-indexed
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
                    "recordName": record.get("recordName"),
                    "connector": connector_type,
                    "eventPublished": True,
                    "userRole": user_role
                }

            except Exception as event_error:
                self.logger.error(f"❌ Failed to publish reindex event: {str(event_error)}")
                return {
                    "success": False,
                    "code": 500,
                    "reason": f"Failed to publish reindex event: {str(event_error)}"
                }

        except Exception as e:
            self.logger.error(f"❌ Failed to reindex record {record_id}: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": f"Internal error: {str(e)}"
            }

    async def reindex_failed_connector_records(self, user_id: str, org_id: str, connector: str, origin: str) -> Dict:
        """
        Reindex all failed records for a specific connector with permission check
        Just validates permissions and publishes a single reindexFailed event
        Args:
            user_id: External user ID doing the reindex
            org_id: Organization ID
            connector: Connector name (GOOGLE_DRIVE, GOOGLE_MAIL, KNOWLEDGE_BASE)
            origin: Origin type (CONNECTOR, UPLOAD)
        Returns:
            Dict: Result with success status and event publication info
        """
        try:
            self.logger.info(f"🔄 Starting failed records reindex for {connector} by user {user_id}")

            # Get user
            user = await self.get_user_by_user_id(user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found: {user_id}"
                }

            user_key = user.get('_key')

            # Check if user has permission to reindex connector records
            permission_check = await self._check_connector_reindex_permissions(
                user_key, org_id, connector, origin
            )

            if not permission_check["allowed"]:
                return {
                    "success": False,
                    "code": 403,
                    "reason": permission_check["reason"]
                }

            try:
                connector_normalized = connector.replace("_", "").lower()
                event_type = f"{connector_normalized}.reindex"

                payload = {
                    "orgId": org_id,
                    "statusFilters": ["FAILED"]
                }

                await self._publish_sync_event(event_type, payload)

                self.logger.info(f"✅ Published {event_type} event for {connector}")

                return {
                    "success": True,
                    "connector": connector,
                    "origin": origin,
                    "user_permission_level": permission_check["permission_level"],
                    "event_published": True,
                    "message": f"Successfully initiated reindex of failed {connector} records"
                }

            except Exception as event_error:
                self.logger.error(f"❌ Failed to publish reindex event: {str(event_error)}")
                return {
                    "success": False,
                    "code": 500,
                    "reason": f"Failed to publish reindex event: {str(event_error)}"
                }

        except Exception as e:
            self.logger.error(f"❌ Failed to reindex failed connector records: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": f"Internal error: {str(e)}"
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

            user_key = user.get('_key')

            # Check if user has permission to access the record group
            permission_check = await self._check_record_group_permissions(
                record_group_id, user_key, org_id
            )

            if not permission_check["allowed"]:
                return {
                    "success": False,
                    "code": 403,
                    "reason": permission_check["reason"]
                }

            # Return success with connector information (caller will publish event)
            return {
                "success": True,
                "connectorId": connector_id,
                "connectorName": connector_name,
                "depth": depth,
                "recordGroupId": record_group_id
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to validate record group reindex: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": f"Internal error: {str(e)}"
            }

    async def get_records_by_record_group(
        self,
        record_group_id: str,
        connector_id: str,
        org_id: str,
        depth: int,
        limit: Optional[int] = None,
        offset: int = 0,
        transaction: Optional[TransactionDatabase] = None
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
            transaction: Optional database transaction

        Returns:
            List[Record]: List of properly typed Record instances
        """
        try:
            self.logger.info(
                f"Retrieving records for record group {record_group_id}, "
                f"connector {connector_id}, org {org_id}, depth {depth}, "
                f"limit: {limit}, offset: {offset}"
            )

            # Validate depth - must be >= -1
            if depth < -1:
                raise ValueError(
                    f"Depth must be >= -1 (where -1 means unlimited). Got: {depth}"
                )

            # Determine max traversal depth (use 100 as practical unlimited)
            # For depth=0, we set max_depth=0 so nested groups traversal returns nothing
            max_depth = 100 if depth == -1 else (0 if depth < 0 else depth)

            # Handle limit/offset for pagination
            # Note: ArangoDB LIMIT syntax requires both offset and count: LIMIT offset, count
            limit_clause = ""
            if limit is not None:
                limit_clause = "LIMIT @offset, @limit"
            elif offset > 0:
                self.logger.warning(
                    f"Offset {offset} provided without limit - offset will be ignored. "
                    "Provide a limit value to use pagination."
                )

            collection_to_types = defaultdict(list)
            for record_type, collection in RECORD_TYPE_COLLECTION_MAPPING.items():
                collection_to_types[collection].append(record_type)

            # Build dynamic typeDoc conditions
            type_doc_conditions = []
            bind_vars = {
                "record_group_id": record_group_id,
                "connector_id": connector_id,
                "org_id": org_id,
                "max_depth": max_depth,
            }

            folder_filter = '''
                LET targetDoc = FIRST(
                    FOR v IN 1..1 OUTBOUND record._id @@is_of_type
                        LIMIT 1
                        RETURN v
                )

                // If the record connects to a file collection, verify isFile == true
                // For any other type (webpage, ticket, etc.), automatically accept
                LET isValidRecord = (
                    targetDoc != null AND IS_SAME_COLLECTION("files", targetDoc._id)
                        ? targetDoc.isFile == true
                        : true  // Not a file (webpage, ticket, etc.) - accept it
                )

                FILTER isValidRecord
            '''

            # Build dynamic typeDoc conditions
            for collection, record_types in collection_to_types.items():
                if len(record_types) == 1:
                    type_check = f"record.recordType == @type_{record_types[0].lower()}"
                    bind_vars[f"type_{record_types[0].lower()}"] = record_types[0]
                else:
                    type_checks = []
                    for rt in record_types:
                        type_checks.append(f"record.recordType == @type_{rt.lower()}")
                        bind_vars[f"type_{rt.lower()}"] = rt
                    type_check = " || ".join(type_checks)

                condition = f"""({type_check}) ? (
                        FOR edge IN {CollectionNames.IS_OF_TYPE.value}
                            FILTER edge._from == record._id
                            LET doc = DOCUMENT(edge._to)
                            FILTER doc != null
                            RETURN doc
                    )[0]"""
                type_doc_conditions.append(condition)

            type_doc_expr = " :\n                    ".join(type_doc_conditions)
            if type_doc_expr:
                type_doc_expr += " :\n                    null"
            else:
                type_doc_expr = "null"

            # Main query: Unified traversal approach
            # Collect all record groups (starting + nested) then get records from all groups
            query = f"""
            LET recordGroup = DOCUMENT(@@record_group_collection, @record_group_id)
            FILTER recordGroup != null
            FILTER recordGroup.orgId == @org_id

            // Collect all record groups: starting group + nested groups up to max_depth
            // When max_depth is 0, only include the starting record group
            // When max_depth > 0, traverse nested groups (1..@max_depth)
            LET allRecordGroups = @max_depth > 0 ? UNION_DISTINCT(
                [recordGroup],
                FOR nestedRg, rgEdge, path IN 1..@max_depth INBOUND recordGroup._id @@inherit_permissions
                    FILTER IS_SAME_COLLECTION("recordGroups", nestedRg)
                    FILTER nestedRg.orgId == @org_id OR nestedRg.orgId == null
                    RETURN nestedRg
            ) : [recordGroup]

            // Get all records from all record groups in a single unified traversal
            // Using OPTIONS for better performance with uniqueVertices
            // Note: A record could be connected to multiple record groups, so we need deduplication
            LET allRecordsRaw = (
                FOR rg IN allRecordGroups
                    FOR record, edge IN 1..1 INBOUND rg._id @@inherit_permissions
                        OPTIONS {{bfs: true, uniqueVertices: "global"}}
                        FILTER IS_SAME_COLLECTION("records", record)
                        FILTER record.connectorId == @connector_id
                        FILTER record.isDeleted != true
                        FILTER record.orgId == @org_id OR record.orgId == null
                        FILTER record.origin == "CONNECTOR"
                        {folder_filter}
                        RETURN record
            )

            // Deduplicate records by _id (equivalent to UNION_DISTINCT in original)
            LET allRecords = (
                FOR record IN allRecordsRaw
                    COLLECT recordId = record._id INTO groups
                    RETURN groups[0].record
            )

            // Sort and paginate
            FOR record IN allRecords
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

            bind_vars.update({
                "@record_group_collection": CollectionNames.RECORD_GROUPS.value,
                "@inherit_permissions": CollectionNames.INHERIT_PERMISSIONS.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
            })

            if limit is not None:
                bind_vars["limit"] = limit
                bind_vars["offset"] = offset

            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars=bind_vars)

            # Convert to typed records
            typed_records = []
            for result in cursor:
                record = self._create_typed_record_from_arango(
                    result["record"],
                    result.get("typeDoc")
                )
                typed_records.append(record)

            self.logger.info(
                f"✅ Successfully retrieved {len(typed_records)} typed records "
                f"for record group {record_group_id}, connector {connector_id}"
            )
            return typed_records

        except Exception as e:
            self.logger.error(
                f"❌ Failed to retrieve records by record group {record_group_id}, connector {connector_id}: {str(e)}",
                exc_info=True
            )
            return []

    async def get_records_by_parent_record(
        self,
        parent_record_id: str,
        connector_id: str,
        org_id: str,
        depth: int,
        limit: Optional[int] = None,
        offset: int = 0,
        transaction: Optional[TransactionDatabase] = None
    ) -> List[Record]:
        """
        Get all child records of a parent record (folder) up to a specified depth.
        Uses graph traversal on recordRelations edge collection. Parent record is always included.

        Args:
            parent_record_id: Record ID of the parent (folder)
            connector_id: Connector ID (all records should be from same connector)
            org_id: Organization ID (for security filtering)
            depth: Depth for traversing children (-1 = unlimited, 0 = only parent,
                   1 = direct children, 2 = children + grandchildren, etc.)
            limit: Maximum number of records to return (for pagination)
            offset: Number of records to skip (for pagination)
            transaction: Optional database transaction

        Returns:
            List[Record]: List of properly typed Record instances
        """
        try:
            self.logger.info(
                f"Retrieving child records for parent {parent_record_id}, "
                f"connector {connector_id}, org {org_id}, depth {depth}, "
                f"limit: {limit}, offset: {offset}"
            )

            # Validate depth - must be >= -1
            if depth < -1:
                raise ValueError(
                    f"Depth must be >= -1 (where -1 means unlimited). Got: {depth}"
                )

            # Handle limit/offset for pagination
            limit_clause = ""
            if limit is not None:
                limit_clause = "LIMIT @offset, @limit"
            elif offset > 0:
                self.logger.warning(
                    f"Offset {offset} provided without limit - offset will be ignored."
                )

            # Determine max traversal depth (use 100 as practical unlimited)
            max_depth = 100 if depth == -1 else depth

            bind_vars = {
                "record_id": f"{CollectionNames.RECORDS.value}/{parent_record_id}",
                "max_depth": max_depth,
                "connector_id": connector_id,
                "org_id": org_id,
            }

            if limit is not None:
                bind_vars["limit"] = limit
                bind_vars["offset"] = offset

            # Single unified query using 0..max_depth traversal
            # Depth 0 = parent, Depth 1+ = children at various levels
            query = f"""
            LET startRecord = DOCUMENT(@record_id)
            FILTER startRecord != null

            // Single traversal for parent (depth 0) and all children (depth 1+)
            FOR v, e, p IN 0..@max_depth OUTBOUND startRecord {CollectionNames.RECORD_RELATIONS.value}
                OPTIONS {{bfs: true, uniqueVertices: "global"}}

                FILTER v.connectorId == @connector_id
                FILTER v.orgId == @org_id OR v.orgId == null
                FILTER v.isDeleted != true

                LET typedRecord = FIRST(
                    FOR rec IN 1..1 OUTBOUND v {CollectionNames.IS_OF_TYPE.value}
                        LIMIT 1
                        RETURN rec
                )

                FILTER typedRecord != null

                LET result = {{
                    record: v,
                    typedRecord: typedRecord,
                    depth: LENGTH(p.edges)
                }}

                SORT result.depth, result.record._key
                {limit_clause}
                RETURN result
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars=bind_vars)

            # Convert to typed records
            typed_records = []
            for result in cursor:
                record = self._create_typed_record_from_arango(
                    result["record"],
                    result.get("typedRecord")
                )
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

    async def _check_record_group_permissions(
        self,
        record_group_id: str,
        user_key: str,
        org_id: str
    ) -> Dict:
        """
        Check if user has permission to access a record group

        Returns:
            Dict with 'allowed' (bool) and 'reason' (str) keys
        """
        try:
            # Query to check if user has permission to the record group
            # Check multiple paths: direct, via groups, via org, etc.
            query = """
            LET userDoc = DOCUMENT(@@user_collection, @user_key)
            FILTER userDoc != null

            LET recordGroup = DOCUMENT(@@record_group_collection, @record_group_id)
            FILTER recordGroup != null
            FILTER recordGroup.orgId == @org_id

            // Direct user -> record group permission
            LET directPermission = (
                FOR perm IN @@permission
                    FILTER perm._from == userDoc._id
                    FILTER perm._to == recordGroup._id
                    FILTER perm.type == "USER"
                    RETURN perm.role
            )

            // User -> group -> record group permission
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

            // User -> org -> record group permission
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

            // Get the highest role (OWNER > WRITER > READER > COMMENTER)
            // Priority order: OWNER (highest) > WRITER > READER > COMMENTER (lowest)
            LET rolePriority = {
                "OWNER": 4,
                "WRITER": 3,
                "READER": 2,
                "COMMENTER": 1
            }

            LET userRole = LENGTH(allPermissions) > 0 ? (
                // Find the role with highest priority
                FIRST(
                    FOR perm IN allPermissions
                        SORT rolePriority[perm] DESC
                        LIMIT 1
                        RETURN perm
                )
            ) : null

            RETURN {
                allowed: hasPermission,
                role: userRole
            }
            """

            bind_vars = {
                "@user_collection": CollectionNames.USERS.value,
                "@record_group_collection": CollectionNames.RECORD_GROUPS.value,
                "@permission": CollectionNames.PERMISSION.value,
                "@belongs_to": CollectionNames.BELONGS_TO.value,
                "user_key": user_key,
                "record_group_id": record_group_id,
                "org_id": org_id
            }

            cursor = self.db.aql.execute(query, bind_vars=bind_vars)
            result = next(cursor, None)

            if result and result.get("allowed"):
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

        except Exception as e:
            self.logger.error(f"Error checking record group permissions: {str(e)}")
            return {
                "allowed": False,
                "role": None,
                "reason": f"Error checking permissions: {str(e)}"
            }

    # Todo: This implementation should work irrespective of the connector type. It should not depend on the connector type.
    # We need to remove Record node, all edges coming to this record or going from this record
    # also, delete node of isOfType Record
    # if this record has children, we need to delete them as well
    # a flag should be passed whether children should be deleted or not
    # it should also return the records that were deleted
    async def delete_record(self, record_id: str, user_id: str) -> Dict:
        """
        Main entry point for record deletion - routes to connector-specific methods
        """
        try:
            self.logger.info(f"🚀 Starting record deletion for {record_id} by user {user_id}")

            # Get record to determine connector type
            record = await self.get_document(record_id, CollectionNames.RECORDS.value)
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
                return await self.delete_knowledge_base_record(record_id, user_id, record)
            elif connector_name == Connectors.GOOGLE_DRIVE.value:
                return await self.delete_google_drive_record(record_id, user_id, record)
            elif connector_name == Connectors.GOOGLE_MAIL.value:
                return await self.delete_gmail_record(record_id, user_id, record)
            elif connector_name == Connectors.OUTLOOK.value:
                return await self.delete_outlook_record(record_id, user_id, record)
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

    async def delete_connector_instance(self, connector_id: str, org_id: str) -> Dict:
        """
        Delete a connector instance and ALL its related data using a generic graph-based approach.

        This method dynamically discovers and deletes all edges connected to nodes,
        making it future-proof when new collections/edges are added.

        Flow:
        1. Collect all node IDs (records, record groups, roles, groups, drives, app)
        2. Get all edge collections from the graph definition
        3. Collect isOfType target nodes (files, mails, etc.) BEFORE deleting edges
        4. Delete all edges connected to these nodes (dynamically)
        5. Delete isOfType target nodes using pre-collected targets
        6. Delete the nodes themselves
        7. Return virtualRecordIds for Qdrant cleanup

        Classification nodes (departments, categories, topics, languages) are NOT deleted
        as they are shared resources.
        Users are NOT deleted - only userAppRelation edges are removed.

        Args:
            connector_id: The connector instance ID (_key in apps collection)
            org_id: The organization ID for validation

        Returns:
            Dict with success status, deletion counts, and virtualRecordIds for Qdrant
        """
        try:
            self.logger.info(f"🗑️ Starting connector instance deletion for {connector_id}")

            # Step 1: Verify connector exists
            connector = await self.get_document(connector_id, CollectionNames.APPS.value)
            if not connector:
                return {
                    "success": False,
                    "error": f"Connector instance {connector_id} not found"
                }

            # Step 2: Collect all entities for this connector
            collected = await self._collect_connector_entities(connector_id)

            # Step 3: Get all edge collections from graph definition
            edge_collections = await self._get_all_edge_collections()

            # Step 4: Define all node collections that might have documents to delete
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
                CollectionNames.APPS.value,
                CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value,
            ]

            # Start transaction
            # Note: lock_timeout is set to 60 seconds (60000ms).
            # For very large connector instances with millions of records, this may need to be increased.
            # Consider implementing a more granular deletion strategy for extremely large datasets if timeouts occur.
            transaction = self.db.begin_transaction(
                read=edge_collections + node_collections,
                write=edge_collections + node_collections,
                exclusive=[],
                allow_implicit=True,
                lock_timeout=60000,  # 60 seconds - may need adjustment for very large deletions
            )

            try:
                # Step 5: Collect isOfType targets BEFORE deleting edges
                isoftype_targets = await self._collect_isoftype_targets(
                    transaction,
                    collected["record_ids"]
                )

                # Step 6: Delete all edges connected to nodes
                await self._delete_all_edges_for_nodes(
                    transaction,
                    collected["all_node_ids"],
                    edge_collections
                )

                # Step 7: Delete isOfType target nodes (files, mails, etc.)
                await self._delete_isoftype_targets_from_collected(
                    transaction,
                    isoftype_targets,
                    edge_collections
                )

                # Step 8: Delete records
                await self._delete_nodes_by_keys(
                    transaction,
                    collected["record_keys"],
                    CollectionNames.RECORDS.value
                )

                # Step 9: Delete record groups
                await self._delete_nodes_by_keys(
                    transaction,
                    collected["record_group_keys"],
                    CollectionNames.RECORD_GROUPS.value
                )

                # Step 10: Delete roles
                await self._delete_nodes_by_keys(
                    transaction,
                    collected["role_keys"],
                    CollectionNames.ROLES.value
                )

                # Step 11: Delete groups
                await self._delete_nodes_by_keys(
                    transaction,
                    collected["group_keys"],
                    CollectionNames.GROUPS.value
                )

                # Step 12: Delete syncPoints and blocks
                await self._delete_nodes_by_connector_id(
                    transaction,
                    connector_id,
                    CollectionNames.SYNC_POINTS.value
                )


                # Step 13: Delete the app itself
                await self._delete_nodes_by_keys(
                    transaction,
                    [connector_id],
                    CollectionNames.APPS.value
                )

                # Commit the transaction
                transaction.commit_transaction()

                self.logger.info(
                    f"✅ Connector instance {connector_id} deleted successfully. "
                    f"Records: {len(collected['record_keys'])}, "
                    f"RecordGroups: {len(collected['record_group_keys'])}, "
                    f"Roles: {len(collected['role_keys'])}, "
                    f"Groups: {len(collected['group_keys'])}, "
                    f"Drives: {len(collected['drive_keys'])}"
                )

                return {
                    "success": True,
                    "deleted_records_count": len(collected["record_keys"]),
                    "deleted_record_groups_count": len(collected["record_group_keys"]),
                    "deleted_roles_count": len(collected["role_keys"]),
                    "deleted_groups_count": len(collected["group_keys"]),
                    "deleted_drives_count": len(collected["drive_keys"]),
                    "virtual_record_ids": collected["virtual_record_ids"],
                    "connector_id": connector_id
                }

            except Exception as tx_error:
                try:
                    transaction.abort_transaction()
                except Exception as abort_error:
                    self.logger.error(
                        f"❌ Failed to abort transaction during connector deletion: {abort_error}. "
                        f"Original error: {tx_error}"
                    )
                raise tx_error

        except Exception as e:
            self.logger.error(f"❌ Failed to delete connector instance {connector_id}: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to delete connector instance: {str(e)}"
            }

    async def _collect_connector_entities(self, connector_id: str) -> Dict:
        """
        Collect all entity IDs for a connector in a single pass.
        Returns record keys, virtual record IDs, and full node IDs for edge deletion.
        """
        result = {
            "record_keys": [],
            "record_ids": [],
            "virtual_record_ids": [],
            "record_group_keys": [],
            "role_keys": [],
            "group_keys": [],
            "drive_keys": [],
            "all_node_ids": []
        }

        # Collect records
        query = """
        FOR r IN @@collection FILTER r.connectorId == @connector_id
        RETURN { _key: r._key, virtualRecordId: r.virtualRecordId }
        """
        cursor = self.db.aql.execute(query, bind_vars={
            "@collection": CollectionNames.RECORDS.value,
            "connector_id": connector_id
        })
        for doc in cursor:
            result["record_keys"].append(doc["_key"])
            result["record_ids"].append(f"records/{doc['_key']}")
            if doc.get("virtualRecordId"):
                result["virtual_record_ids"].append(doc["virtualRecordId"])

        # Collect record groups
        query = "FOR rg IN @@collection FILTER rg.connectorId == @connector_id RETURN rg._key"
        cursor = self.db.aql.execute(query, bind_vars={
            "@collection": CollectionNames.RECORD_GROUPS.value,
            "connector_id": connector_id
        })
        for key in cursor:
            result["record_group_keys"].append(key)

        # Collect roles
        query = "FOR role IN @@collection FILTER role.connectorId == @connector_id RETURN role._key"
        cursor = self.db.aql.execute(query, bind_vars={
            "@collection": CollectionNames.ROLES.value,
            "connector_id": connector_id
        })
        for key in cursor:
            result["role_keys"].append(key)

        # Collect groups
        query = "FOR grp IN @@collection FILTER grp.connectorId == @connector_id RETURN grp._key"
        cursor = self.db.aql.execute(query, bind_vars={
            "@collection": CollectionNames.GROUPS.value,
            "connector_id": connector_id
        })
        for key in cursor:
            result["group_keys"].append(key)

        # Collect drives
        query = "FOR drive IN @@collection FILTER drive.connectorId == @connector_id RETURN drive._key"
        cursor = self.db.aql.execute(query, bind_vars={
            "@collection": CollectionNames.DRIVES.value,
            "connector_id": connector_id
        })
        for key in cursor:
            result["drive_keys"].append(key)

        # Build all_node_ids list for edge deletion
        result["all_node_ids"].extend(result["record_ids"])
        result["all_node_ids"].extend([f"recordGroups/{k}" for k in result["record_group_keys"]])
        result["all_node_ids"].extend([f"roles/{k}" for k in result["role_keys"]])
        result["all_node_ids"].extend([f"groups/{k}" for k in result["group_keys"]])
        result["all_node_ids"].extend([f"drives/{k}" for k in result["drive_keys"]])
        result["all_node_ids"].append(f"apps/{connector_id}")

        self.logger.info(
            f"📊 Collected entities for connector {connector_id}: "
            f"records={len(result['record_keys'])}, "
            f"recordGroups={len(result['record_group_keys'])}, "
            f"roles={len(result['role_keys'])}, "
            f"groups={len(result['group_keys'])}, "
            f"drives={len(result['drive_keys'])}"
        )

        return result

    async def _get_all_edge_collections(self) -> List[str]:
        """
        Get all edge collection names from the graph definition.
        This makes the deletion future-proof when new edge types are added.

        Raises:
            Exception: If the graph or edge definitions cannot be retrieved.
        """
        graph = self.db.graph(GraphNames.KNOWLEDGE_GRAPH.value)
        edge_definitions = graph.edge_definitions()
        edge_collections = [ed['edge_collection'] for ed in edge_definitions]
        self.logger.debug(f"📊 Found {len(edge_collections)} edge collections from graph")
        return edge_collections

    async def _delete_all_edges_for_nodes(
        self,
        transaction,
        node_ids: List[str],
        edge_collections: List[str]
    ) -> int:
        """
        Delete all edges where _from or _to matches any of the node_ids.
        Iterates through each edge collection dynamically.
        """
        if not node_ids:
            return 0

        total_deleted = 0

        deletion_query = """
        FOR edge IN @@edge_collection
            FILTER edge._from IN @node_ids OR edge._to IN @node_ids
            REMOVE edge IN @@edge_collection
            RETURN 1
        """

        for edge_collection in edge_collections:
            try:
                cursor = transaction.aql.execute(deletion_query, bind_vars={
                    "@edge_collection": edge_collection,
                    "node_ids": node_ids
                })
                deleted_count = len(list(cursor))
                total_deleted += deleted_count

                if deleted_count > 0:
                    self.logger.debug(f"🗑️ Deleted {deleted_count} edges from {edge_collection}")

            except Exception as e:
                self.logger.warning(f"⚠️ Error deleting from {edge_collection}: {e}")
                # Continue with other collections

        self.logger.info(f"✅ Deleted {total_deleted} total edges across all collections")
        return total_deleted

    async def _collect_isoftype_targets(self, transaction, record_ids: List[str]) -> List[Dict]:
        """
        Collect isOfType target nodes (files, mails, etc.) BEFORE deleting edges.
        This must be called before _delete_all_edges_for_nodes to ensure we can find the targets.

        Returns:
            List of target dictionaries with keys: collection, key, full_id
        """
        if not record_ids:
            return []

        collect_query = """
        FOR edge IN @@isOfType
            FILTER edge._from IN @record_ids
            LET target = PARSE_IDENTIFIER(edge._to)
            RETURN DISTINCT { collection: target.collection, key: target.key, full_id: edge._to }
        """

        try:
            cursor = transaction.aql.execute(collect_query, bind_vars={
                "@isOfType": CollectionNames.IS_OF_TYPE.value,
                "record_ids": record_ids
            })
            targets = list(cursor)
            self.logger.debug(f"📊 Collected {len(targets)} isOfType targets before edge deletion")
            return targets
        except Exception as e:
            self.logger.warning(f"⚠️ Error collecting isOfType targets: {e}")
            return []

    async def _delete_isoftype_targets_from_collected(
        self,
        transaction,
        targets: List[Dict],
        edge_collections: List[str]
    ) -> int:
        """
        Delete isOfType target nodes using pre-collected targets.
        This is called after edges are deleted, using targets collected before deletion.

        Args:
            transaction: The transaction object
            targets: List of target dicts with keys: collection, key, full_id (from _collect_isoftype_targets)
            edge_collections: List of edge collection names for cleanup
        """
        if not targets:
            return 0

        # Build list of type node IDs for edge cleanup
        type_node_ids = [target["full_id"] for target in targets]

        # Delete edges connected to type nodes BEFORE deleting the nodes
        await self._delete_all_edges_for_nodes(transaction, type_node_ids, edge_collections)

        # Group targets by collection
        targets_by_collection: Dict[str, List[str]] = {}
        for target in targets:
            coll = target["collection"]
            key = target["key"]
            if coll not in targets_by_collection:
                targets_by_collection[coll] = []
            targets_by_collection[coll].append(key)

        # Delete from each collection
        total_deleted = 0
        for collection, keys in targets_by_collection.items():
            deleted = await self._delete_nodes_by_keys(transaction, keys, collection)
            total_deleted += deleted

        self.logger.info(f"✅ Deleted {total_deleted} isOfType target documents")
        return total_deleted

    async def _delete_isoftype_targets(self, transaction, record_ids: List[str], edge_collections: List[str]) -> int:
        """
        Delete nodes that records point to via isOfType edges (files, mails, etc.).
        Dynamically discovers target collection from edge._to.
        Also deletes any edges connected to these type nodes first.
        """
        if not record_ids:
            return 0

        # First, collect all targets from isOfType edges
        collect_query = """
        FOR edge IN @@isOfType
            FILTER edge._from IN @record_ids
            LET target = PARSE_IDENTIFIER(edge._to)
            RETURN DISTINCT { collection: target.collection, key: target.key, full_id: edge._to }
        """

        try:
            cursor = transaction.aql.execute(collect_query, bind_vars={
                "@isOfType": CollectionNames.IS_OF_TYPE.value,
                "record_ids": record_ids
            })
            targets = list(cursor)
        except Exception as e:
            self.logger.warning(f"⚠️ Error collecting isOfType targets: {e}")
            return 0

        if not targets:
            return 0

        # Build list of type node IDs for edge cleanup
        type_node_ids = [target["full_id"] for target in targets]

        # Step 6.5: Delete edges connected to type nodes BEFORE deleting the nodes
        await self._delete_all_edges_for_nodes(transaction, type_node_ids, edge_collections)

        # Group targets by collection
        targets_by_collection: Dict[str, List[str]] = {}
        for target in targets:
            coll = target["collection"]
            key = target["key"]
            if coll not in targets_by_collection:
                targets_by_collection[coll] = []
            targets_by_collection[coll].append(key)

        # Delete from each collection
        total_deleted = 0
        for collection, keys in targets_by_collection.items():
            deleted = await self._delete_nodes_by_keys(transaction, keys, collection)
            total_deleted += deleted

        self.logger.info(f"✅ Deleted {total_deleted} isOfType target documents")
        return total_deleted

    async def _delete_nodes_by_keys(self, transaction, keys: List[str], collection: str, batch_size: int = 1000) -> int:
        """Delete documents by their _key values using batching."""
        if not keys:
            return 0

        total_deleted = 0

        # Process in batches to avoid query size limits
        for i in range(0, len(keys), batch_size):
            batch_keys = keys[i:i + batch_size]

            query = """
            FOR doc IN @@collection
                FILTER doc._key IN @keys
                REMOVE doc IN @@collection
                RETURN 1
            """

            try:
                cursor = transaction.aql.execute(query, bind_vars={
                    "@collection": collection,
                    "keys": batch_keys
                })
                deleted = len(list(cursor))
                total_deleted += deleted
            except Exception as e:
                self.logger.warning(f"⚠️ Error deleting batch from {collection}: {e}")
                # Continue with next batch even if one fails

        if total_deleted > 0:
            self.logger.debug(f"🗑️ Deleted {total_deleted} documents from {collection}")

        return total_deleted

    async def _delete_nodes_by_connector_id(self, transaction, connector_id: str, collection: str) -> int:
        """Delete all documents with matching connectorId."""
        query = """
        FOR doc IN @@collection
            FILTER doc.connectorId == @connector_id
            REMOVE doc IN @@collection
            RETURN 1
        """

        try:
            cursor = transaction.aql.execute(query, bind_vars={
                "@collection": collection,
                "connector_id": connector_id
            })
            deleted = len(list(cursor))
            if deleted > 0:
                self.logger.debug(f"🗑️ Deleted {deleted} documents from {collection}")
            return deleted
        except Exception as e:
            self.logger.warning(f"⚠️ Error deleting from {collection}: {e}")
            return 0



    async def delete_record_by_external_id(self, connector_id: str, external_id: str, user_id: str, transaction: Optional[TransactionDatabase] = None) -> None:
        """
        Delete a record by external ID
        """
        try:
            self.logger.info(f"🗂️ Deleting record {external_id} from {connector_id}")

            # Get record
            record = await self.get_record_by_external_id(connector_id, external_id, transaction=transaction)
            if not record:
                self.logger.warning(f"⚠️ Record {external_id} not found in {connector_id}")
                return

            # Delete record using the record's internal ID and user_id
            deletion_result = await self.delete_record(record.id, user_id)

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

    async def remove_user_access_to_record(self, connector_id: str, external_id: str, user_id: str, transaction: Optional[TransactionDatabase] = None) -> None:
        """
        Remove a user's access to a record (for inbox-based deletions)
        This removes the user's permissions and belongsTo edges without deleting the record itself
        """
        try:
            self.logger.info(f"🔄 Removing user access: {external_id} from {connector_id} for user {user_id}")

            # Get record
            record = await self.get_record_by_external_id(connector_id, external_id, transaction=transaction)
            if not record:
                self.logger.warning(f"⚠️ Record {external_id} not found in {connector_id}")
                return

            # Remove user's access instead of deleting the entire record
            result = await self._remove_user_access_from_record(record.id, user_id)

            if result.get("success"):
                self.logger.info(f"✅ User access removed: {external_id} from {connector_id}")
            else:
                self.logger.error(f"❌ Failed to remove user access: {result.get('reason', 'Unknown error')}")
                raise Exception(f"Failed to remove user access: {result.get('reason', 'Unknown error')}")

        except Exception as e:
            self.logger.error(f"❌ Failed to remove user access {external_id} from {connector_id}: {str(e)}")
            raise

    async def _remove_user_access_from_record(self, record_id: str, user_id: str) -> Dict:
        """Remove a specific user's access to a record"""
        try:
            self.logger.info(f"🚀 Removing user {user_id} access to record {record_id}")

            # Remove user's permission edges
            user_removal_query = """
            FOR perm IN permission
                FILTER perm._from == @user_to
                FILTER perm._to == @record_from
                REMOVE perm IN permission
                RETURN OLD
            """

            cursor = self.db.aql.execute(user_removal_query, bind_vars={
                "record_from": f"records/{record_id}",
                "user_to": f"users/{user_id}"
            })

            removed_permissions = list(cursor)

            if removed_permissions:
                self.logger.info(f"✅ Removed {len(removed_permissions)} permission(s) for user {user_id} on record {record_id}")
                return {"success": True, "removed_permissions": len(removed_permissions)}
            else:
                self.logger.warning(f"⚠️ No permissions found for user {user_id} on record {record_id}")
                return {"success": True, "removed_permissions": 0}

        except Exception as e:
            self.logger.error(f"❌ Failed to remove user access: {str(e)}")
            return {
                "success": False,
                "reason": f"Access removal failed: {str(e)}"
            }

    async def delete_knowledge_base_record(self, record_id: str, user_id: str, record: Dict) -> Dict:
        """
        Delete a Knowledge Base record - handles uploads and KB-specific logic
        """
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
            kb_context = await self._get_kb_context_for_record(record_id)
            if not kb_context:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"Knowledge base context not found for record {record_id}"
                }

            # Check KB permissions
            user_role = await self.get_user_kb_permission(kb_context["kb_id"], user_key)
            if user_role not in self.connector_delete_permissions[Connectors.KNOWLEDGE_BASE.value]["allowed_roles"]:
                return {
                    "success": False,
                    "code": 403,
                    "reason": f"Insufficient permissions. User role: {user_role}"
                }

            # Execute KB-specific deletion
            return await self._execute_kb_record_deletion(record_id, record, kb_context)

        except Exception as e:
            self.logger.error(f"❌ Failed to delete KB record: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": f"KB record deletion failed: {str(e)}"
            }

    async def _get_kb_context_for_record(self, record_id: str) -> Optional[Dict]:
        """
        Get KB context for a record
        """
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

            cursor = self.db.aql.execute(kb_query, bind_vars={
                "record_id": record_id,
                "@belongs_to": CollectionNames.BELONGS_TO.value,
            })

            result = next(cursor, None)

            if result:
                self.logger.info(f"✅ Found KB context: {result['kb_name']}")
                return result
            else:
                self.logger.warning(f"⚠️ No KB context found for record {record_id}")
                return None

        except Exception as e:
            self.logger.error(f"❌ Failed to get KB context for record {record_id}: {str(e)}")
            return None

    async def _execute_kb_record_deletion(self, record_id: str, record: Dict, kb_context: Dict) -> Dict:
        """Execute KB record deletion with transaction"""
        try:
            transaction = self.db.begin_transaction(
                write=self.connector_delete_permissions[Connectors.KNOWLEDGE_BASE.value]["document_collections"] +
                      self.connector_delete_permissions[Connectors.KNOWLEDGE_BASE.value]["edge_collections"]
            )

            try:
                # Get file record for event publishing before deletion
                file_record = await self.get_document(record_id, CollectionNames.FILES.value)

                # Delete KB-specific edges
                await self._delete_kb_specific_edges(transaction, record_id)

                # Delete file record
                if file_record:
                    await self._delete_file_record(transaction, record_id)

                # Delete main record
                await self._delete_main_record(transaction, record_id)

                # Commit transaction
                await asyncio.to_thread(lambda: transaction.commit_transaction())

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
                await asyncio.to_thread(lambda: transaction.abort_transaction())
                raise e

        except Exception as e:
            self.logger.error(f"❌ KB record deletion transaction failed: {str(e)}")
            return {
                "success": False,
                "reason": f"Transaction failed: {str(e)}"
            }

    async def _delete_kb_specific_edges(self, transaction, record_id: str) -> None:
        """Delete KB-specific edges"""
        kb_edge_collections = self.connector_delete_permissions[Connectors.KNOWLEDGE_BASE.value]["edge_collections"]

        for edge_collection in kb_edge_collections:
            edge_deletion_query = """
            FOR edge IN @@edge_collection
                FILTER edge._from == @record_from OR edge._to == @record_to
                REMOVE edge IN @@edge_collection
                RETURN OLD
            """

            transaction.aql.execute(edge_deletion_query, bind_vars={
                "record_from": f"records/{record_id}",
                "record_to": f"records/{record_id}",
                "@edge_collection": edge_collection,
            })

    async def delete_google_drive_record(self, record_id: str, user_id: str, record: Dict) -> Dict:
        """
        Delete a Google Drive record - handles Drive-specific permissions and logic
        """
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
            user_role = await self._check_drive_permissions(record_id, user_key)
            if not user_role or user_role not in self.connector_delete_permissions[Connectors.GOOGLE_DRIVE.value]["allowed_roles"]:
                return {
                    "success": False,
                    "code": 403,
                    "reason": f"Insufficient Drive permissions. Role: {user_role}"
                }

            # Execute Drive-specific deletion
            return await self._execute_drive_record_deletion(record_id, record, user_role)

        except Exception as e:
            self.logger.error(f"❌ Failed to delete Drive record: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": f"Drive record deletion failed: {str(e)}"
            }

    async def _execute_drive_record_deletion(self, record_id: str, record: Dict, user_role: str) -> Dict:
        """Execute Drive record deletion with transaction"""
        try:
            transaction = self.db.begin_transaction(
                write=self.connector_delete_permissions[Connectors.GOOGLE_DRIVE.value]["document_collections"] +
                      self.connector_delete_permissions[Connectors.GOOGLE_DRIVE.value]["edge_collections"]
            )

            try:
                # Get file record for event publishing
                file_record = await self.get_document(record_id, CollectionNames.FILES.value)

                # Delete Drive-specific edges
                await self._delete_drive_specific_edges(transaction, record_id)

                # Delete 'anyone' permissions specific to Drive
                await self._delete_drive_anyone_permissions(transaction, record_id)

                # Delete file record
                if file_record:
                    await self._delete_file_record(transaction, record_id)

                # Delete main record
                await self._delete_main_record(transaction, record_id)

                # Commit transaction
                await asyncio.to_thread(lambda: transaction.commit_transaction())

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
                await asyncio.to_thread(lambda: transaction.abort_transaction())
                raise e

        except Exception as e:
            self.logger.error(f"❌ Drive record deletion transaction failed: {str(e)}")
            return {
                "success": False,
                "reason": f"Transaction failed: {str(e)}"
            }

    async def _delete_drive_specific_edges(self, transaction, record_id: str) -> None:
        """Delete Google Drive specific edges with optimized queries"""
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
                self.logger.debug(f"🔍 Bind vars: {bind_vars}")

                # Execute deletion
                result = transaction.aql.execute(deletion_query, bind_vars=bind_vars)
                deleted_count = len(list(result))
                total_deleted += deleted_count

                if deleted_count > 0:
                    self.logger.info(f"🗑️ Deleted {deleted_count} {strategy['description']} from {edge_collection}")
                else:
                    self.logger.debug(f"📝 No {strategy['description']} found in {edge_collection}")

            except Exception as e:
                self.logger.error(f"❌ Failed to delete edges from {edge_collection}: {str(e)}")
                self.logger.error(f"❌ Strategy: {strategy}")
                self.logger.error(f"❌ Bind vars: {bind_vars}")
                raise

        self.logger.info(f"✅ Drive edge deletion completed: {total_deleted} total edges deleted for record {record_id}")

    async def _delete_drive_anyone_permissions(self, transaction, record_id: str) -> None:
        """Delete Drive-specific 'anyone' permissions"""
        anyone_deletion_query = """
        FOR anyone_perm IN @@anyone
            FILTER anyone_perm.file_key == @record_id
            REMOVE anyone_perm IN @@anyone
            RETURN OLD
        """

        transaction.aql.execute(anyone_deletion_query, bind_vars={
            "record_id": record_id,
            "@anyone": CollectionNames.ANYONE.value,
        })

    async def delete_gmail_record(self, record_id: str, user_id: str, record: Dict) -> Dict:
        """
        Delete a Gmail record - handles Gmail-specific permissions and logic
        """
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
            user_role = await self._check_gmail_permissions(record_id, user_key)
            if not user_role or user_role not in self.connector_delete_permissions[Connectors.GOOGLE_MAIL.value]["allowed_roles"]:
                return {
                    "success": False,
                    "code": 403,
                    "reason": f"Insufficient Gmail permissions. Role: {user_role}"
                }

            # Execute Gmail-specific deletion
            return await self._execute_gmail_record_deletion(record_id, record, user_role)

        except Exception as e:
            self.logger.error(f"❌ Failed to delete Gmail record: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": f"Gmail record deletion failed: {str(e)}"
            }

    async def _execute_gmail_record_deletion(self, record_id: str, record: Dict, user_role: str) -> Dict:
        """Execute Gmail record deletion with transaction"""
        try:
            transaction = self.db.begin_transaction(
                write=self.connector_delete_permissions[Connectors.GOOGLE_MAIL.value]["document_collections"] +
                      self.connector_delete_permissions[Connectors.GOOGLE_MAIL.value]["edge_collections"]
            )

            try:
                # Get mail and file records for event publishing
                mail_record = await self.get_document(record_id, CollectionNames.MAILS.value)
                file_record = await self.get_document(record_id, CollectionNames.FILES.value) if record.get("recordType") == "FILE" else None

                # Delete Gmail-specific edges (including thread relationships)
                await self._delete_gmail_specific_edges(transaction, record_id)

                # Delete mail record
                if mail_record:
                    await self._delete_mail_record(transaction, record_id)

                # Delete file record if it's an attachment
                if file_record:
                    await self._delete_file_record(transaction, record_id)

                # Delete main record
                await self._delete_main_record(transaction, record_id)

                # Commit transaction
                await asyncio.to_thread(lambda: transaction.commit_transaction())

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
                await asyncio.to_thread(lambda: transaction.abort_transaction())
                raise e

        except Exception as e:
            self.logger.error(f"❌ Gmail record deletion transaction failed: {str(e)}")
            return {
                "success": False,
                "reason": f"Transaction failed: {str(e)}"
            }

    async def _delete_gmail_specific_edges(self, transaction, record_id: str) -> None:
        """Delete Gmail specific edges with optimized queries"""
        gmail_edge_collections = self.connector_delete_permissions[Connectors.GOOGLE_MAIL.value]["edge_collections"]

        # Define edge deletion strategies - maps collection to query config
        edge_deletion_strategies = {
            CollectionNames.IS_OF_TYPE.value: {
                "filter": "edge._from == @record_from",
                "bind_vars": {"record_from": f"records/{record_id}"},
                "description": "IS_OF_TYPE edges"
            },
            CollectionNames.RECORD_RELATIONS.value: {
                "filter": "(edge._from == @record_from OR edge._to == @record_to) AND edge.relationType IN @relation_types",
                "bind_vars": {
                    "record_from": f"records/{record_id}",
                    "record_to": f"records/{record_id}",
                    "relation_types": ["SIBLING", "ATTACHMENT"]  # Gmail-specific relation types
                },
                "description": "Gmail record relations (SIBLING/ATTACHMENT)"
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
            # Default strategy for any other collections
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

        for edge_collection in gmail_edge_collections:
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
                self.logger.debug(f"🔍 Bind vars: {bind_vars}")

                # Execute deletion
                result = transaction.aql.execute(deletion_query, bind_vars=bind_vars)
                deleted_count = len(list(result))
                total_deleted += deleted_count

                if deleted_count > 0:
                    self.logger.info(f"🗑️ Deleted {deleted_count} {strategy['description']} from {edge_collection}")
                else:
                    self.logger.debug(f"📝 No {strategy['description']} found in {edge_collection}")

            except Exception as e:
                self.logger.error(f"❌ Failed to delete edges from {edge_collection}: {str(e)}")
                self.logger.error(f"❌ Strategy: {strategy}")
                self.logger.error(f"❌ Bind vars: {bind_vars}")
                raise

        self.logger.info(f"✅ Gmail edge deletion completed: {total_deleted} total edges deleted for record {record_id}")

    async def delete_outlook_record(self, record_id: str, user_id: str, record: Dict) -> Dict:
        """
        Delete an Outlook record - handles email and its attachments.
        """
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
            user_role = await self._check_record_permission(record_id, user_key)
            if user_role != "OWNER":
                return {
                    "success": False,
                    "code": 403,
                    "reason": f"Only mailbox owner can delete emails. Role: {user_role}"
                }

            # Execute deletion
            return await self._execute_outlook_record_deletion(record_id, record)

        except Exception as e:
            self.logger.error(f"❌ Failed to delete Outlook record: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": f"Outlook record deletion failed: {str(e)}"
            }

    async def _execute_outlook_record_deletion(self, record_id: str, record: Dict) -> Dict:
        """Execute Outlook record deletion - deletes email and all attachments"""
        try:
            # Define collections
            outlook_edge_collections = [
                CollectionNames.IS_OF_TYPE.value,
                CollectionNames.RECORD_RELATIONS.value,
                CollectionNames.PERMISSION.value,
                CollectionNames.BELONGS_TO.value,
            ]
            outlook_doc_collections = [
                CollectionNames.RECORDS.value,
                CollectionNames.MAILS.value,
                CollectionNames.FILES.value,
            ]

            transaction = self.db.begin_transaction(
                write=outlook_doc_collections + outlook_edge_collections
            )

            try:
                # Get attachments (child records with ATTACHMENT relation)
                attachments_query = f"""
                FOR edge IN {CollectionNames.RECORD_RELATIONS.value}
                    FILTER edge._from == @record_from
                        AND edge.relationshipType == 'ATTACHMENT'
                    RETURN PARSE_IDENTIFIER(edge._to).key
                """

                cursor = transaction.aql.execute(attachments_query, bind_vars={
                    "record_from": f"records/{record_id}"
                })
                attachment_ids = list(cursor)

                # Delete all attachments first
                for attachment_id in attachment_ids:
                    self.logger.info(f"Deleting attachment {attachment_id} of email {record_id}")
                    await self._delete_outlook_edges(transaction, attachment_id)
                    await self._delete_file_record(transaction, attachment_id)
                    await self._delete_main_record(transaction, attachment_id)

                # Delete the email itself
                await self._delete_outlook_edges(transaction, record_id)

                # Delete mail record
                await self._delete_mail_record(transaction, record_id)

                # Delete main record
                await self._delete_main_record(transaction, record_id)

                # Commit transaction
                await asyncio.to_thread(lambda: transaction.commit_transaction())

                self.logger.info(f"✅ Deleted Outlook record {record_id} with {len(attachment_ids)} attachments")

                return {
                    "success": True,
                    "record_id": record_id,
                    "attachments_deleted": len(attachment_ids)
                }

            except Exception as e:
                await asyncio.to_thread(lambda: transaction.abort_transaction())
                raise e

        except Exception as e:
            self.logger.error(f"❌ Outlook deletion transaction failed: {str(e)}")
            return {
                "success": False,
                "reason": f"Transaction failed: {str(e)}"
            }

    async def _delete_outlook_edges(self, transaction, record_id: str) -> None:
        """Delete Outlook specific edges"""
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

                cursor = transaction.aql.execute(query, bind_vars=bind_vars)
                deleted_count = len(list(cursor))
                total_deleted += deleted_count

                if deleted_count > 0:
                    self.logger.debug(f"Deleted {deleted_count} edges from {collection}")

            except Exception as e:
                self.logger.error(f"Failed to delete edges from {collection}: {e}")
                raise

        self.logger.info(f"Total edges deleted for record {record_id}: {total_deleted}")

    async def _check_record_permission(self, record_id: str, user_key: str) -> Optional[str]:
        """Check user's permission role on a record"""
        try:
            query = f"""
            FOR edge IN {CollectionNames.PERMISSION.value}
                FILTER edge._to == @record_to
                    AND edge._from == @user_from
                    AND edge.type == 'USER'
                LIMIT 1
                RETURN edge.role
            """

            cursor = self.db.aql.execute(query, bind_vars={
                "record_to": f"records/{record_id}",
                "user_from": f"users/{user_key}"
            })

            return next(cursor, None)

        except Exception as e:
            self.logger.error(f"Failed to check record permission: {e}")
            return None

    async def _delete_file_record(self, transaction, record_id: str) -> None:
        """Delete file record from files collection"""
        file_deletion_query = """
        REMOVE @record_id IN @@files_collection
        RETURN OLD
        """

        transaction.aql.execute(file_deletion_query, bind_vars={
            "record_id": record_id,
            "@files_collection": CollectionNames.FILES.value,
        })

    async def _delete_mail_record(self, transaction, record_id: str) -> None:
        """Delete mail record from mails collection"""
        mail_deletion_query = """
        REMOVE @record_id IN @@mails_collection
        RETURN OLD
        """

        transaction.aql.execute(mail_deletion_query, bind_vars={
            "record_id": record_id,
            "@mails_collection": CollectionNames.MAILS.value,
        })

    async def _delete_main_record(self, transaction, record_id: str) -> None:
        """Delete main record from records collection"""
        record_deletion_query = """
        REMOVE @record_id IN @@records_collection
        RETURN OLD
        """

        transaction.aql.execute(record_deletion_query, bind_vars={
            "record_id": record_id,
            "@records_collection": CollectionNames.RECORDS.value,
        })

    async def _check_record_permissions(self, record_id: str, user_key: str, check_drive_inheritance: bool = True) -> Dict:
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
            self.logger.info(f"🔍 Checking permissions for record {record_id} and user {user_key}")

            permission_query = """
            LET user_from = CONCAT('users/', @user_key)
            LET record_from = CONCAT('records/', @record_id)

            // 1. Check direct user permissions on the record


            LET direct_permission = FIRST(
                FOR perm IN @@permission
                    FILTER perm._from == user_from
                    FILTER perm._to == record_from
                    FILTER perm.type == "USER"
                    RETURN perm.role
            )


            // 2. Check group permissions

            LET group_permission = FIRST(
                FOR permission IN @@permission
                    FILTER permission._from == user_from
                    LET group = DOCUMENT(permission._to)
                    FILTER group != null
                    FOR perm IN @@permission
                        FILTER perm._from == group._id
                        FILTER perm._to == record_from
                        RETURN perm.role
            )


            // 2.5 Check inherited group->record_group permissions
            LET record_group_permission = FIRST(
                // First hop: user -> group
                FOR group, userToGroupEdge IN 1..1 ANY user_from @@permission
                    FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)

                    // Second hop: group -> recordgroup
                    FOR recordGroup, groupToRecordGroupEdge IN 1..1 ANY group._id @@permission

                        // Third hop: recordgroup -> record
                        FOR rec, recordGroupToRecordEdge IN 1..1 INBOUND recordGroup._id @@inherit_permissions
                            FILTER rec._id == record_from

                            // The role is on the final edge from the record group to the record
                            RETURN groupToRecordGroupEdge.role
            )

            LET nested_record_group_permission = FIRST(
                // First hop: user -> group/role
                FOR group, userToGroupEdge IN 1..1 ANY user_from @@permission
                    FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)

                // Second hop: group -> recordgroup
                FOR recordGroup, groupToRgEdge IN 1..1 ANY group._id @@permission
                    FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)

                // Third hop: recordgroup -> nested record groups (0 to 5 levels) -> record
                FOR record, edge, path IN 0..5 INBOUND recordGroup._id @@inherit_permissions
                    FILTER record._id == record_from
                    FILTER IS_SAME_COLLECTION("records", record)

                    RETURN groupToRgEdge.role
            )

            LET direct_user_record_group_permission = FIRST(
                // Direct user -> record_group (with nested record groups support)
                FOR recordGroup, userToRgEdge IN 1..1 ANY user_from @@permission
                    FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)

                    // Record group -> nested record groups (0 to 5 levels) -> record
                    FOR record, edge, path IN 0..5 INBOUND recordGroup._id @@inherit_permissions
                        // Only process if final vertex is the target record
                        FILTER record._id == record_from
                        FILTER IS_SAME_COLLECTION("records", record)

                        LET finalEdge = LENGTH(path.edges) > 0 ? path.edges[LENGTH(path.edges) - 1] : edge
                        RETURN userToRgEdge.role
            )

            // 2.6 Check inherited recordGroup permissions (record -> recordGroup hierarchy via inherit_permissions)
            // This handles any recordGroup hierarchy (spaces, folders, etc.) where permissions are inherited
            LET inherited_record_group_permission = FIRST(
                // Traverse up the recordGroup hierarchy (0 to 5 levels) from record
                FOR recordGroup, inheritEdge, path IN 0..5 OUTBOUND record_from @@inherit_permissions
                    FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)

                    // Check if user has direct permission on any recordGroup in the hierarchy
                    FOR perm IN @@permission
                        FILTER perm._from == user_from
                        FILTER perm._to == recordGroup._id
                        FILTER perm.type == "USER"
                        RETURN perm.role
            )

            // 2.7 Check group -> inherited recordGroup permission
            LET group_inherited_record_group_permission = FIRST(
                // Traverse up the recordGroup hierarchy from record
                FOR recordGroup, inheritEdge, path IN 0..5 OUTBOUND record_from @@inherit_permissions
                    FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)

                    // Check if user's group has permission on any recordGroup in the hierarchy
                    FOR group, userToGroupEdge IN 1..1 ANY user_from @@permission
                        FILTER userToGroupEdge.type == "USER"
                        FILTER IS_SAME_COLLECTION("groups", group) OR IS_SAME_COLLECTION("roles", group)

                        FOR perm IN @@permission
                            FILTER perm._from == group._id
                            FILTER perm._to == recordGroup._id
                            FILTER perm.type IN ["GROUP", "ROLE"]
                            RETURN perm.role
            )

            // 3. Check domain/organization permissions

            LET domain_permission = FIRST(
                FOR belongs_edge IN @@belongs_to
                    FILTER belongs_edge._from == user_from
                    FILTER belongs_edge.entityType == "ORGANIZATION"
                    LET org = DOCUMENT(belongs_edge._to)
                    FILTER org != null
                    FOR perm IN @@permission
                        FILTER perm._from == org._id
                        FILTER perm._to == record_from
                        FILTER perm.type IN ["DOMAIN", "ORG"]
                        RETURN perm.role
            )

            // 4. Check 'anyone' permissions (public sharing)
            LET user_org_id = FIRST(
                FOR belongs_edge IN @@belongs_to
                    FILTER belongs_edge._from == user_from
                    FILTER belongs_edge.entityType == "ORGANIZATION"
                    LET org = DOCUMENT(belongs_edge._to)
                    FILTER org != null
                    RETURN org._key
            )

            LET anyone_permission = user_org_id ? FIRST(
                FOR anyone_perm IN @@anyone
                    FILTER anyone_perm.file_key == @record_id
                    FILTER anyone_perm.organization == user_org_id
                    FILTER anyone_perm.active == true
                    RETURN anyone_perm.role
            ) : null

            LET anyone_record_group_permission = FIRST(
                // User -> Organization -> RecordGroup -> Record (with nested record groups support)
                FOR belongs_edge IN @@belongs_to
                    FILTER belongs_edge._from == user_from
                    FILTER belongs_edge.entityType == "ORGANIZATION"
                    LET org = DOCUMENT(belongs_edge._to)
                    FILTER org != null

                    // Org -> record_group permission
                    FOR recordGroup, orgToRgEdge IN 1..1 ANY org._id @@permission
                        FILTER IS_SAME_COLLECTION("recordGroups", recordGroup)

                        // Record group -> nested record groups (0 to 2 levels) -> record
                        FOR record, edge, path IN 0..2 INBOUND recordGroup._id @@inherit_permissions
                            FILTER record._id == record_from
                            FILTER IS_SAME_COLLECTION("records", record)

                            LET finalEdge = LENGTH(path.edges) > 0 ? path.edges[LENGTH(path.edges) - 1] : edge
                            RETURN orgToRgEdge.role
            )

            // 5. Check Drive-level access (if enabled)
            LET drive_access = @check_drive_inheritance ? FIRST(
                // Get the file record to find its drive
                FOR record IN @@records
                    FILTER record._key == @record_id
                    FOR file_edge IN @@is_of_type
                        FILTER file_edge._from == record._id
                        LET file = DOCUMENT(file_edge._to)
                        FILTER file != null
                        // Get the drive this file belongs to
                        LET file_drive_id = file.driveId
                        FILTER file_drive_id != null
                        // Check if user has access to this drive
                        FOR drive_edge IN @@user_drive_relation
                            FILTER drive_edge._from == user_from
                            LET drive = DOCUMENT(drive_edge._to)
                            FILTER drive != null
                            FILTER drive._key == file_drive_id OR drive.driveId == file_drive_id
                            // Map drive access level to permission role
                            LET drive_role = (
                                drive_edge.access_level == "owner" ? "OWNER" :
                                drive_edge.access_level IN ["writer", "fileOrganizer"] ? "WRITER" :
                                drive_edge.access_level IN ["commenter", "reader"] ? "READER" :
                                null
                            )
                            RETURN drive_role
            ) : null

            // Return the highest permission level found (in order of precedence)
            LET final_permission = (
                direct_permission ? direct_permission :
                inherited_record_group_permission ? inherited_record_group_permission :
                group_inherited_record_group_permission ? group_inherited_record_group_permission :
                group_permission ? group_permission :
                record_group_permission ? record_group_permission :
                direct_user_record_group_permission ? direct_user_record_group_permission :
                nested_record_group_permission ? nested_record_group_permission :
                domain_permission ? domain_permission :
                anyone_permission ? anyone_permission :
                anyone_record_group_permission ? anyone_record_group_permission :
                drive_access ? drive_access :
                null
            )

            RETURN {
                permission: final_permission,
                source: (
                    direct_permission ? "DIRECT" :
                    inherited_record_group_permission ? "INHERITED_RECORD_GROUP" :
                    group_inherited_record_group_permission ? "GROUP_INHERITED_RECORD_GROUP" :
                    group_permission ? "GROUP" :
                    record_group_permission ? "RECORD_GROUP" :
                    direct_user_record_group_permission ? "DIRECT_USER_RECORD_GROUP" :
                    nested_record_group_permission ? "NESTED_RECORD_GROUP" :
                    domain_permission ? "DOMAIN" :
                    anyone_permission ? "ANYONE" :
                    anyone_record_group_permission ? "ANYONE_RECORD_GROUP" :
                    drive_access ? "DRIVE_ACCESS" :
                    "NONE"
                )
            }
            """

            cursor = self.db.aql.execute(permission_query, bind_vars={
                "record_id": record_id,
                "user_key": user_key,
                "check_drive_inheritance": check_drive_inheritance,
                "@permission": CollectionNames.PERMISSION.value,
                "@belongs_to": CollectionNames.BELONGS_TO.value,
                "@inherit_permissions": CollectionNames.INHERIT_PERMISSIONS.value,
                "@anyone": CollectionNames.ANYONE.value,
                "@records": CollectionNames.RECORDS.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                "@user_drive_relation": CollectionNames.USER_DRIVE_RELATION.value,
            })

            result = next(cursor, None)

            if result and result.get("permission"):
                permission = result["permission"]
                source = result["source"]
                self.logger.info(f"✅ Permission found: {permission} (via {source})")
                return {
                    "permission": permission,
                    "source": source
                }
            else:
                self.logger.warning(f"⚠️ No permissions found for user {user_key} on record {record_id}")
                return {
                    "permission": None,
                    "source": "NONE"
                }

        except Exception as e:
            self.logger.error(f"❌ Failed to check permissions: {str(e)}")
            return {
                "permission": None,
                "source": "ERROR",
                "error": str(e)
            }

    async def _check_drive_permissions(self, record_id: str, user_key: str) -> Optional[str]:
        """
        Check Google Drive specific permissions
        Checks: Direct permissions, Group permissions, Domain permissions, Anyone permissions, Drive-level access
        """
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
                        FILTER perm.type == "GROUP"
                        RETURN perm.role
            )
            // 3. Check domain/organization permissions
            LET domain_permission = FIRST(
                FOR belongs_edge IN @@belongs_to
                    FILTER belongs_edge._from == user_from
                    FILTER belongs_edge.entityType == "ORGANIZATION"
                    LET org = DOCUMENT(belongs_edge._to)
                    FILTER org != null
                    FOR perm IN @@permission
                        FILTER perm._to == record_from
                        FILTER perm._from == org._id
                        FILTER perm.type IN ["DOMAIN", "ORG"]
                        RETURN perm.role
            )
            // 4. Check 'anyone' permissions (Drive-specific)
            LET user_org_id = FIRST(
                FOR belongs_edge IN @@belongs_to
                    FILTER belongs_edge._from == user_from
                    FILTER belongs_edge.entityType == "ORGANIZATION"
                    LET org = DOCUMENT(belongs_edge._to)
                    FILTER org != null
                    RETURN org._key
            )
            LET anyone_permission = user_org_id ? FIRST(
                FOR anyone_perm IN @@anyone
                    FILTER anyone_perm.file_key == @record_id
                    FILTER anyone_perm.organization == user_org_id
                    FILTER anyone_perm.active == true
                    RETURN anyone_perm.role
            ) : null
            // 5. Check Drive-level access (user-drive relationship)
            LET drive_access = FIRST(
                // Get the file record to find its drive
                FOR record IN @@records
                    FILTER record._key == @record_id
                    FOR file_edge IN @@is_of_type
                        FILTER file_edge._from == record._id
                        LET file = DOCUMENT(file_edge._to)
                        FILTER file != null
                        // Get the drive this file belongs to
                        LET file_drive_id = file.driveId
                        FILTER file_drive_id != null
                        // Check if user has access to this drive
                        FOR drive_edge IN @@user_drive_relation
                            FILTER drive_edge._from == user_from
                            LET drive = DOCUMENT(drive_edge._to)
                            FILTER drive != null
                            FILTER drive._key == file_drive_id OR drive.driveId == file_drive_id
                            // Map drive access level to permission role
                            LET drive_role = (
                                drive_edge.access_level == "owner" ? "OWNER" :
                                drive_edge.access_level IN ["writer", "fileOrganizer"] ? "WRITER" :
                                drive_edge.access_level IN ["commenter", "reader"] ? "READER" :
                                null
                            )
                            RETURN drive_role
            )
            // Return the highest permission level found (in order of precedence)
            LET final_permission = (
                direct_permission ? direct_permission :
                group_permission ? group_permission :
                domain_permission ? domain_permission :
                anyone_permission ? anyone_permission :
                drive_access ? drive_access :
                null
            )
            RETURN {
                permission: final_permission,
                source: (
                    direct_permission ? "DIRECT" :
                    group_permission ? "GROUP" :
                    domain_permission ? "DOMAIN" :
                    anyone_permission ? "ANYONE" :
                    drive_access ? "DRIVE_ACCESS" :
                    "NONE"
                )
            }
            """

            cursor = self.db.aql.execute(drive_permission_query, bind_vars={
                "record_id": record_id,
                "user_key": user_key,
                "@permission": CollectionNames.PERMISSION.value,
                "@belongs_to": CollectionNames.BELONGS_TO.value,
                "@anyone": CollectionNames.ANYONE.value,
                "@records": CollectionNames.RECORDS.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                "@user_drive_relation": CollectionNames.USER_DRIVE_RELATION.value,
            })

            result = next(cursor, None)

            if result and result.get("permission"):
                permission = result["permission"]
                source = result["source"]
                self.logger.info(f"✅ Drive permission found: {permission} (via {source})")
                return permission
            else:
                self.logger.warning(f"⚠️ No Drive permissions found for user {user_key} on record {record_id}")
                return None

        except Exception as e:
            self.logger.error(f"❌ Failed to check Drive permissions: {str(e)}")
            return None

    async def _check_gmail_permissions(self, record_id: str, user_key: str) -> Optional[str]:
        """
        Check Gmail specific permissions
        Gmail permission model: User must be sender, recipient (to/cc/bcc), or have explicit permissions
        """
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
                        // Return role based on relationship
                        RETURN is_sender ? "OWNER" : "READER"
            ) : []
            LET email_permission = LENGTH(email_access) > 0 ? FIRST(email_access) : null
            // 2. Check direct user permissions on the record
            LET direct_permission = FIRST(
                FOR perm IN @@permission
                    FILTER perm._to == record_from
                    FILTER perm._from == user_from
                    FILTER perm.type == "USER"
                    RETURN perm.role
            )
            // 3. Check group permissions
            LET group_permission = FIRST(
                FOR belongs_edge IN @@belongs_to
                    FILTER belongs_edge._from == user_from
                    FILTER belongs_edge.entityType == "GROUP"
                    LET group = DOCUMENT(belongs_edge._to)
                    FILTER group != null
                    FOR perm IN @@permission
                        FILTER perm._to == record_from
                        FILTER perm._from == group._id
                        FILTER perm.type == "GROUP"
                        RETURN perm.role
            )
            // 4. Check domain/organization permissions
            LET domain_permission = FIRST(
                FOR belongs_edge IN @@belongs_to
                    FILTER belongs_edge._from == user_from
                    FILTER belongs_edge.entityType == "ORGANIZATION"
                    LET org = DOCUMENT(belongs_edge._to)
                    FILTER org != null
                    FOR perm IN @@permission
                        FILTER perm._to == record_from
                        FILTER perm._from == org._id
                        FILTER perm.type IN ["DOMAIN", "ORG"]
                        RETURN perm.role
            )
            // 5. Check 'anyone' permissions
            LET user_org_id = FIRST(
                FOR belongs_edge IN @@belongs_to
                    FILTER belongs_edge._from == user_from
                    FILTER belongs_edge.entityType == "ORGANIZATION"
                    LET org = DOCUMENT(belongs_edge._to)
                    FILTER org != null
                    RETURN org._key
            )
            LET anyone_permission = user_org_id ? FIRST(
                FOR anyone_perm IN @@anyone
                    FILTER anyone_perm.file_key == @record_id
                    FILTER anyone_perm.organization == user_org_id
                    FILTER anyone_perm.active == true
                    RETURN anyone_perm.role
            ) : null
            // Return the highest permission level found (email access takes precedence)
            LET final_permission = (
                email_permission ? email_permission :
                direct_permission ? direct_permission :
                group_permission ? group_permission :
                domain_permission ? domain_permission :
                anyone_permission ? anyone_permission :
                null
            )
            RETURN {
                permission: final_permission,
                source: (
                    email_permission ? "EMAIL_ACCESS" :
                    direct_permission ? "DIRECT" :
                    group_permission ? "GROUP" :
                    domain_permission ? "DOMAIN" :
                    anyone_permission ? "ANYONE" :
                    "NONE"
                ),
                user_email: user_email,
                is_sender: email_permission == "OWNER",
                is_recipient: email_permission == "READER"
            }
            """

            cursor = self.db.aql.execute(gmail_permission_query, bind_vars={
                "record_id": record_id,
                "user_key": user_key,
                "@records": CollectionNames.RECORDS.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                "@permission": CollectionNames.PERMISSION.value,
                "@belongs_to": CollectionNames.BELONGS_TO.value,
                "@anyone": CollectionNames.ANYONE.value,
            })

            result = next(cursor, None)

            if result and result.get("permission"):
                permission = result["permission"]
                source = result["source"]
                user_email = result.get("user_email", "unknown")

                if source == "EMAIL_ACCESS":
                    role_type = "sender" if result.get("is_sender") else "recipient"
                    self.logger.info(f"✅ Gmail permission found: {permission} (user {user_email} is {role_type})")
                else:
                    self.logger.info(f"✅ Gmail permission found: {permission} (via {source})")

                return permission
            else:
                self.logger.warning(f"⚠️ No Gmail permissions found for user {user_key} on record {record_id}")
                return None

        except Exception as e:
            self.logger.error(f"❌ Failed to check Gmail permissions: {str(e)}")
            return None

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
                "recordId": record.get("_key"),
                "version": record.get("version", 1),
                "extension": extension,
                "mimeType": mime_type,
                "summaryDocumentId": record.get("summaryDocumentId"),
                "virtualRecordId": record.get("virtualRecordId"),
            }
        except Exception as e:
            self.logger.error(f"❌ Failed to create deleted record event payload: {str(e)}")
            return {}

    async def _download_from_signed_url(
        self, signed_url: str, request: Request
    ) -> bytes:
        """
        Download file from signed URL with exponential backoff retry

        Args:
            signed_url: The signed URL to download from
            record_id: Record ID for logging
        Returns:
            bytes: The downloaded file content
        """
        chunk_size = 1024 * 1024 * 3  # 3MB chunks
        max_retries = 3
        base_delay = 1  # Start with 1 second delay

        timeout = aiohttp.ClientTimeout(
            total=1200,  # 20 minutes total
            connect=120,  # 2 minutes for initial connection
            sock_read=1200,  # 20 minutes per chunk read
        )
        self.logger.info(f"Downloading file from signed URL: {signed_url}")
        for attempt in range(max_retries):
            delay = base_delay * (2**attempt)  # Exponential backoff
            file_buffer = BytesIO()
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    try:
                        async with session.get(signed_url, headers=request.headers) as response:
                            if response.status != HttpStatusCode.SUCCESS.value:
                                raise aiohttp.ClientError(
                                    f"Failed to download file: {response.status}"
                                )
                            self.logger.info(f"Response {response}")

                            content_length = response.headers.get("Content-Length")
                            if content_length:
                                self.logger.info(
                                    f"Expected file size: {int(content_length) / (1024*1024):.2f} MB"
                                )

                            last_logged_size = 0
                            total_size = 0
                            log_interval = chunk_size

                            self.logger.info("Starting chunked download...")
                            try:
                                async for chunk in response.content.iter_chunked(
                                    chunk_size
                                ):
                                    file_buffer.write(chunk)
                                    total_size += len(chunk)
                                    if total_size - last_logged_size >= log_interval:
                                        self.logger.debug(
                                            f"Total size so far: {total_size / (1024*1024):.2f} MB"
                                        )
                                        last_logged_size = total_size
                            except IOError as io_err:
                                raise aiohttp.ClientError(
                                    f"IO error during chunk download: {str(io_err)}"
                                )

                            file_content = file_buffer.getvalue()
                            self.logger.info(
                                f"✅ Download complete. Total size: {total_size / (1024*1024):.2f} MB"
                            )
                            return file_content

                    except aiohttp.ServerDisconnectedError as sde:
                        raise aiohttp.ClientError(f"Server disconnected: {str(sde)}")
                    except aiohttp.ClientConnectorError as cce:
                        raise aiohttp.ClientError(f"Connection error: {str(cce)}")

            except (aiohttp.ClientError, asyncio.TimeoutError, IOError) as e:
                error_type = type(e).__name__
                self.logger.warning(
                    f"Download attempt {attempt + 1} failed with {error_type}: {str(e)}. "
                    f"Retrying in {delay} seconds..."
                )

                await asyncio.sleep(delay)

            finally:
                if not file_buffer.closed:
                    file_buffer.close()

    async def _create_reindex_event_payload(self, record: Dict, file_record: Optional[Dict], user_id: Optional[str] = None, request: Optional[Request] = None) -> Dict:
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


            file_content = ""
            if record.get("origin") != OriginTypes.UPLOAD.value:

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
                        # Fallback: encode as base64 string for binary content
                        # import base64
                        # file_content = base64.b64encode(file_content_bytes).decode('utf-8')



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

        except Exception as e:
            self.logger.error(f"❌ Failed to create reindex event payload: {str(e)}")
            raise


    async def _publish_sync_event(self, event_type: str, payload: Dict) -> None:
        """Publish record event to Kafka"""
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

    async def batch_upsert_nodes(
        self,
        nodes: List[Dict],
        collection: str,
        transaction: Optional[TransactionDatabase] = None,
    ) -> bool | None:
        """Batch upsert multiple nodes using Python-Arango SDK methods"""
        try:

            self.logger.info("🚀 Batch upserting nodes: %s", collection)

            batch_query = """
            FOR node IN @nodes
                UPSERT { _key: node._key }
                INSERT node
                UPDATE node
                IN @@collection
                RETURN NEW
            """

            bind_vars = {"nodes": nodes, "@collection": collection}

            db = transaction if transaction else self.db

            cursor = db.aql.execute(batch_query, bind_vars=bind_vars)
            results = list(cursor)

            self.logger.info(
                "✅ Successfully upserted %d nodes in collection '%s'.",
                len(results),
                collection,
            )
            return True

        except Exception as e:
            self.logger.error("❌ Batch upsert failed: %s", str(e))
            if transaction:
                raise
            return False

    async def batch_create_edges(
        self,
        edges: List[Dict],
        collection: str,
        transaction: Optional[TransactionDatabase] = None,
    ) -> bool | None:
        """Batch create PARENT_CHILD relationships"""
        try:
            self.logger.info("🚀 Batch creating edges: %s", collection)

            batch_query = """
            FOR edge IN @edges
                UPSERT { _from: edge._from, _to: edge._to }
                INSERT edge
                UPDATE edge
                IN @@collection
                RETURN NEW
            """
            bind_vars = {"edges": edges, "@collection": collection}

            db = transaction if transaction else self.db

            cursor = db.aql.execute(batch_query, bind_vars=bind_vars)
            results = list(cursor)
            self.logger.info(
                "✅ Successfully created %d edges in collection '%s'.",
                len(results),
                collection,
            )
            return True
        except Exception as e:
            self.logger.error("❌ Batch edge creation failed: %s", str(e))
            if transaction:
                raise
            return False

    async def get_record_by_conversation_index(
        self,
        connector_id: str,
        conversation_index: str,
        thread_id: str,
        org_id: str,
        user_id: str,
        transaction: Optional[TransactionDatabase] = None,
    ) -> Optional[Record]:
        """
        Get mail record by conversation_index and thread_id for a specific user

        Args:
            connector_id: Connector ID
            conversation_index: The conversation index to look up
            thread_id: The thread ID to match
            org_id: The organization ID
            user_id: User's id to filter results
            transaction: Optional database transaction

        Returns:
            Optional[Record]: Mail record if found, None otherwise
        """
        try:

            # Query that joins records, mails, and permissions to find mail by owner
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
                        LET user_key = SPLIT(edge._to, '/')[1]
                        LET user = DOCUMENT('{CollectionNames.USERS.value}', user_key)
                        FILTER user.userId == @user_id
                        LIMIT 1
                    RETURN record
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query,
                bind_vars={
                    "conversation_index": conversation_index,
                    "thread_id": thread_id,
                    "connector_id": connector_id,
                    "org_id": org_id,
                    "user_id": user_id,
                },
            )
            result = next(cursor, None)

            if result:
                return Record.from_arango_base_record(result)
            else:
                return None

        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve mail record for conversation_index %s in thread %s for user %s: %s",
                conversation_index,
                thread_id,
                user_id,
                str(e),
            )
            return None

    async def get_record_owner_source_user_email(
        self,
        record_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[str]:
        """
        Get the owner's source_user_id (Graph User ID) from permission edges.

        Args:
            record_id: The record ID
            transaction: Optional database transaction

        Returns:
            Optional[str]: source_user_id (Graph User ID) of the owner, None if not found
        """
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

            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"record_id": record_id})
            result = next(cursor, None)
            return result

        except Exception as e:
            self.logger.error(f"Failed to get owner source_user_id for record {record_id}: {e}")
            return None


    async def get_record_by_path(
        self, connector_id: str, path: str, transaction: Optional[TransactionDatabase] = None
    ) -> Dict:
        """
        Get a record from the FILES collection using its path.

        Args:
            connector_id (str): The ID of the connector.
            path (str): The path of the file to look up.
            transaction (Optional[TransactionDatabase]): Optional database transaction.

        Returns:
            Optional[Record]: The Record object if found, otherwise None.
        """
        try:
            self.logger.info(
                "🚀 Retrieving record by path for connector %s and path %s", connector_id, path
            )

            query = f"""
            FOR fileRecord IN {CollectionNames.FILES.value}
                FILTER fileRecord.path == @path
                RETURN fileRecord
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query, bind_vars={"path": path}
            )
            result = next(cursor, None)

            if result:

                self.logger.info(
                    "✅ Successfully retrieved file record for path: %s", path
                )
                # TODO:
                # The file record might return multiple file_records as they might have same path (but have different connectors instance)
                # Now fetch all the correosponding records of these file_records and then filter them based on connector_id

                # record = await self.get_record_by_id(result["_key"])

                # return record.id
                return result
            else:
                self.logger.warning(
                    "⚠️ No record found for path: %s", path
                )
                return None

        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve record for path %s: %s", path, str(e)
            )
            return None

    async def get_record_by_external_id(
        self, connector_id: str, external_id: str, transaction: Optional[TransactionDatabase] = None
    ) -> Optional[Record]:
        """
        Get internal file key using the external file ID

        Args:
            connector_id: Connector ID
            external_file_id (str): External file ID to look up
            transaction (Optional[TransactionDatabase]): Optional database transaction

        Returns:
            Optional[str]: Internal file key if found, None otherwise
        """
        try:
            self.logger.info(
                "🚀 Retrieving internal key for external file ID %s %s", connector_id, external_id
            )

            query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record.externalRecordId == @external_id AND record.connectorId == @connector_id
                RETURN record
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query, bind_vars={"external_id": external_id, "connector_id": connector_id}
            )
            result = next(cursor, None)

            if result:
                self.logger.info(
                    "✅ Successfully retrieved internal key for external file ID %s %s", connector_id, external_id
                )
                return Record.from_arango_base_record(result)
            else:
                self.logger.warning(
                    "⚠️ No internal key found for external file ID %s %s", connector_id, external_id
                )
                return None

        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve internal key for external file ID %s %s: %s", connector_id, external_id, str(e)
            )
            return None

    async def get_record_by_external_revision_id(
        self, connector_id: str, external_revision_id: str, transaction: Optional[TransactionDatabase] = None
    ) -> Optional[Record]:
        """
        Get record using the external revision ID (e.g., etag for S3).

        Args:
            connector_id: Connector ID
            external_revision_id (str): External revision ID to look up (e.g., etag)
            transaction (Optional[TransactionDatabase]): Optional database transaction

        Returns:
            Optional[Record]: Record object if found, None otherwise
        """
        try:
            self.logger.debug(
                "🚀 Retrieving record by external revision ID %s for connector %s", external_revision_id, connector_id
            )

            query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record.externalRevisionId == @external_revision_id AND record.connectorId == @connector_id
                LIMIT 1
                RETURN record
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query, bind_vars={"external_revision_id": external_revision_id, "connector_id": connector_id}
            )
            result = next(cursor, None)

            if result:
                self.logger.debug(
                    "✅ Successfully retrieved record by external revision ID %s for connector %s", external_revision_id, connector_id
                )
                return Record.from_arango_base_record(result)
            else:
                self.logger.debug(
                    "⚠️ No record found for external revision ID %s for connector %s", external_revision_id, connector_id
                )
                return None

        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve record by external revision ID %s for connector %s: %s", external_revision_id, connector_id, str(e)
            )
            return None

    async def get_record_by_issue_key(
        self, connector_id: str, issue_key: str, transaction: Optional[TransactionDatabase] = None
    ) -> Optional[Record]:
        """
        Get Jira issue record by issue key (e.g., PROJ-123) by searching weburl pattern.

        Args:
            connector_id (str): Connector ID
            issue_key (str): Jira issue key (e.g., "PROJ-123")
            transaction (Optional[TransactionDatabase]): Optional database transaction

        Returns:
            Optional[Record]: Record if found, None otherwise
        """
        try:
            self.logger.info(
                "🚀 Retrieving record for Jira issue key %s %s", connector_id, issue_key
            )

            # Search for record where weburl contains "/browse/{issue_key}" and record_type is TICKET
            query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record.connectorId == @connector_id
                    AND record.recordType == @record_type
                    AND record.webUrl != null
                    AND CONTAINS(record.webUrl, @browse_pattern)
                RETURN record
            """

            browse_pattern = f"/browse/{issue_key}"
            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query,
                bind_vars={
                    "connector_id": connector_id,
                    "record_type": "TICKET",
                    "browse_pattern": browse_pattern
                }
            )
            result = next(cursor, None)

            if result:
                self.logger.info(
                    "✅ Successfully retrieved record for Jira issue key %s %s", connector_id, issue_key
                )
                return Record.from_arango_base_record(result)
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

    async def get_records_by_parent(
        self,
        connector_id: str,
        parent_external_record_id: str,
        record_type: Optional[str] = None,
        transaction: Optional[TransactionDatabase] = None
    ) -> List[Record]:
        """
        Get all child records for a parent record by parent_external_record_id.
        Optionally filter by record_type.

        Args:
            connector_id (str): Connector ID
            parent_external_record_id (str): Parent record's external ID
            record_type (Optional[str]): Optional filter by record type (e.g., "COMMENT", "FILE", "TICKET")
            transaction (Optional[TransactionDatabase]): Optional database transaction

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

            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars=bind_vars)
            results = list(cursor)

            records = [Record.from_arango_base_record(result) for result in results]

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

    # TODO: expand this method for specific users list
    async def get_records_by_status(
        self,
        org_id: str,
        connector_id: str,
        status_filters: List[str],
        limit: Optional[int] = None,
        offset: int = 0,
        transaction: Optional[TransactionDatabase] = None
    ) -> List[Record]:
        """
        Get records by their indexing status with pagination support.
        Returns properly typed Record instances (FileRecord, MailRecord, etc.)

        Args:
            org_id (str): Organization ID
            connector_id (str): Connector ID
            status_filters (List[str]): List of status values to filter (e.g., ["FAILED", "COMPLETED"])
            limit (Optional[int]): Maximum number of records to return (for pagination)
            offset (int): Number of records to skip (for pagination)
            transaction (Optional[TransactionDatabase]): Optional database transaction

        Returns:
            List[Record]: List of properly typed Record instances
        """
        try:
            self.logger.info(f"Retrieving records for connector {connector_id} with status filters: {status_filters}, limit: {limit}, offset: {offset}")

            # Handle limit/offset for pagination
            # Note: ArangoDB LIMIT syntax requires both offset and count: LIMIT offset, count
            limit_clause = ""
            if limit is not None:
                limit_clause = "LIMIT @offset, @limit"
            elif offset > 0:
                self.logger.warning(
                    f"Offset {offset} provided without limit - offset will be ignored. "
                    "Provide a limit value to use pagination."
                )

            # Group record types by their collection
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
                    # Multiple types map to same collection (e.g., WEBPAGE, CONFLUENCE_PAGE, CONFLUENCE_BLOGPOST)
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

            if limit is not None:
                bind_vars["limit"] = limit
                bind_vars["offset"] = offset

            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars=bind_vars)

            # Convert raw DB results to properly typed Record instances
            typed_records = []
            for result in cursor:
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
            elif collection == CollectionNames.LINKS.value:
                return LinkRecord.from_arango_record(type_doc, record_dict)
            elif collection == CollectionNames.PROJECTS.value:
                return ProjectRecord.from_arango_record(type_doc, record_dict)
            else:
                # Unknown collection - fallback to base Record
                return Record.from_arango_base_record(record_dict)
        except Exception as e:
            self.logger.warning(f"Failed to create typed record for {record_type}, falling back to base Record: {str(e)}")
            return Record.from_arango_base_record(record_dict)

    async def get_record_by_id(
        self, id: str, transaction: Optional[TransactionDatabase] = None
    ) -> Optional[Record]:
        """
        Get internal file key using the id

        Args:
            id (str): The internal record ID (_key) to look up
            transaction (Optional[TransactionDatabase]): Optional database transaction

        Returns:
            Optional[str]: Internal file key if found, None otherwise
        """
        try:
            self.logger.info(
                "🚀 Retrieving internal key for id %s", id
            )

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

            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query, bind_vars={"id": id}
            )
            result = next(cursor, None)

            if result:
                self.logger.info(
                    "✅ Successfully retrieved internal key for id %s", id
                )
                return self._create_typed_record_from_arango(
                    result["record"],
                    result.get("typeDoc")
                )
            else:
                self.logger.warning(
                    "⚠️ No internal key found for id %s", id
                )
                return None

        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve internal key for id %s: %s", id, str(e)
            )
            return None

    async def get_record_group_by_external_id(self, connector_id: str, external_id: str, transaction: Optional[TransactionDatabase] = None) -> Optional[RecordGroup]:
        """1
        Get internal record group key using the external record group ID
        """
        try:
            self.logger.info(
                "🚀 Retrieving internal key for external record group ID %s %s", connector_id, external_id
            )
            query = f"""
            FOR record_group IN {CollectionNames.RECORD_GROUPS.value}
                FILTER record_group.externalGroupId == @external_id AND record_group.connectorId == @connector_id
                RETURN record_group
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"external_id": external_id, "connector_id": connector_id})
            result = next(cursor, None)
            if result:
                self.logger.info(
                    "✅ Successfully retrieved internal key for external record group ID %s %s", connector_id, external_id
                )
                return RecordGroup.from_arango_base_record_group(result)
            else:
                self.logger.warning(
                    "⚠️ No internal key found for external record group ID %s %s", connector_id, external_id
                )
                return None
        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve internal key for external record group ID %s %s: %s", connector_id, external_id, str(e)
            )
            return None

    async def get_user_group_by_external_id(
        self,
        connector_id: str,
        external_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[AppUserGroup]:
        """
        Get a user group from the GROUPS collection using its external (source) ID.
        """
        try:
            self.logger.info(
                "🚀 Retrieving user group for external ID %s %s", connector_id, external_id
            )

            # Query the GROUPS collection using the schema fields
            query = f"""
            FOR group IN {CollectionNames.GROUPS.value}
                FILTER group.externalGroupId == @external_id AND group.connectorId == @connector_id
                LIMIT 1
                RETURN group
            """

            db = transaction if transaction else self.db


            cursor = db.aql.execute(query,
                bind_vars={"external_id": external_id, "connector_id": connector_id}
            )

            result = next(cursor, None)


            if result:
                self.logger.info(
                    "✅ Successfully retrieved user group for external ID %s %s", connector_id, external_id
                )
                return AppUserGroup.from_arango_base_user_group(result)
            else:
                self.logger.warning(
                    "⚠️ No user group found for external ID %s %s", connector_id, external_id
                )
                return None
        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve user group for external ID %s %s: %s", connector_id, external_id, str(e)
            )
            return None

    async def get_app_role_by_external_id(
        self,
        connector_id: str,
        external_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[AppRole]:
        """
        Get an app role from the ROLES collection using its external (source) ID.
        """
        try:
            self.logger.info(
                "🚀 Retrieving Role for external ID %s %s", connector_id, external_id
            )

            # Query the GROUPS collection using the schema fields
            query = f"""
            FOR role IN {CollectionNames.ROLES.value}
                FILTER role.externalRoleId == @external_id AND role.connectorId == @connector_id
                LIMIT 1
                RETURN role
            """

            db = transaction if transaction else self.db


            cursor = db.aql.execute(query,
                bind_vars={"external_id": external_id, "connector_id": connector_id}
            )

            result = next(cursor, None)


            if result:
                self.logger.info(
                    "✅ Successfully retrieved Role for external ID %s %s", connector_id, external_id
                )
                return AppRole.from_arango_base_role(result)
            else:
                self.logger.warning(
                    "⚠️ No Role found for external ID %s %s", connector_id, external_id
                )
                return None
        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve Role for external ID %s %s: %s", connector_id, external_id, str(e)
            )
            return None

    async def get_user_by_email(self, email: str, transaction: Optional[TransactionDatabase] = None) -> Optional[User]:
        """
        Get internal user key using the email
        """
        try:
            self.logger.info(
                "🚀 Retrieving internal key for email %s", email
            )
            query = f"""
            FOR user IN {CollectionNames.USERS.value}
                FILTER LOWER(user.email) == LOWER(@email)
                RETURN user
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"email": email})
            result = next(cursor, None)
            if result:
                self.logger.info(
                    "✅ Successfully retrieved internal key for email %s", email
                )
                return User.from_arango_user(result)
            else:
                self.logger.warning(
                    "⚠️ No internal key found for email %s", email
                )
                return None
        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve internal key for email %s: %s", email, str(e)
            )
            return None

    async def get_app_user_by_email(
        self,
        email: str,
        connector_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[AppUser]:
        """
        Get app user by email and app name, including sourceUserId from edge
        """
        try:
            self.logger.info(
                "🚀 Retrieving user for email %s and app %s", email, connector_id
            )

            query = """
                // First find the app
                LET app = FIRST(
                    FOR a IN @@apps
                        FILTER a._key == @connector_id
                        RETURN a
                )

                // Then find the user by email
                LET user = FIRST(
                    FOR u IN @@users
                        FILTER LOWER(u.email) == LOWER(@email)
                        RETURN u
                )

                // Find the edge connecting user to app
                LET edge = FIRST(
                    FOR e IN @@user_app_relation
                        FILTER e._from == user._id
                        FILTER e._to == app._id
                        RETURN e
                )

                // Return user merged with sourceUserId if edge exists
                RETURN edge != null ? MERGE(user, {
                    sourceUserId: edge.sourceUserId
                }) : null
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={
                "email": email,
                "connector_id": connector_id,
                "@apps": CollectionNames.APPS.value,
                "@users": CollectionNames.USERS.value,
                "@user_app_relation": CollectionNames.USER_APP_RELATION.value
            })

            result = next(cursor, None)
            if result:
                self.logger.info("✅ Successfully retrieved user for email %s and app %s", email, connector_id)
                result["connectorId"] = connector_id
                return AppUser.from_arango_user(result)
            else:
                self.logger.warning("⚠️ No user found for email %s and app %s", email, connector_id)
                return None
        except Exception as e:
            self.logger.error("❌ Failed to retrieve user for email %s and app %s: %s", email, connector_id, str(e))
            return None

    async def get_user_by_source_id(
            self,
            source_user_id: str,
            connector_id: str,
            transaction: Optional[TransactionDatabase] = None
        ) -> Optional[User]:
            """
            Get a user by their source system ID (sourceUserId field in userAppRelation edge).

            Args:
                source_user_id: The user ID from the source system
                connector_id: Connector ID
                transaction: Optional transaction database

            Returns:
                User object if found, None otherwise
            """
            try:
                self.logger.info(
                    "🚀 Retrieving user by source_id %s for connector %s",
                    source_user_id, connector_id
                )

                user_query = """
                // First find the app
                LET app = FIRST(
                    FOR a IN @@apps
                        FILTER a._key == @connector_id
                        RETURN a
                )

                // Then find user connected via userAppRelation with matching sourceUserId
                FOR edge IN @@user_app_relation
                    FILTER edge._to == app._id
                    FILTER edge.sourceUserId == @source_user_id
                    LET user = DOCUMENT(edge._from)
                    FILTER user != null
                    LIMIT 1
                    RETURN user
                """

                db = transaction if transaction else self.db
                cursor = db.aql.execute(
                    user_query,
                    bind_vars={
                        "@apps": CollectionNames.APPS.value,
                        "@user_app_relation": CollectionNames.USER_APP_RELATION.value,
                        "connector_id": connector_id,
                        "source_user_id": source_user_id,
                    },
                )
                user_doc = next(cursor, None)

                if user_doc:
                    self.logger.info("✅ Successfully retrieved user by source_id %s", source_user_id)
                    return User.from_arango_user(user_doc)
                else:
                    self.logger.warning("⚠️ No user found for source_id %s", source_user_id)
                    return None

            except Exception as e:
                self.logger.error(
                    "❌ Failed to get user by source_id %s: %s",
                    source_user_id, str(e),
                    exc_info=True
                )
                return None

    async def get_users(self, org_id, active=True) -> List[Dict]:
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

            query = """
                FOR edge IN belongsTo
                    FILTER edge._to == CONCAT('organizations/', @org_id)
                    AND edge.entityType == 'ORGANIZATION'
                    LET user = DOCUMENT(edge._from)
                    FILTER @active == false OR user.isActive == true
                    RETURN user
                """

            # Execute query with organization parameter
            cursor = self.db.aql.execute(query, bind_vars={"org_id": org_id, "active": active})
            users = list(cursor)

            self.logger.info("✅ Successfully fetched %s users", len(users))
            return users

        except Exception as e:
            self.logger.error("❌ Failed to fetch users: %s", str(e))
            return []

    async def get_app_users(self, org_id, connector_id: str) -> List[Dict]:
        """
        Fetch all users from the database who belong to the organization
        and are connected to the specified app via userAppRelation edge.

        Args:
            org_id (str): Organization ID
            connector_id (str): App connector ID

        Returns:
            List[Dict]: List of user documents with their details and sourceUserId
        """
        try:
            self.logger.info(f"🚀 Fetching users connected to {connector_id} app")

            query = """
                // First find the app
                LET app = FIRST(
                    FOR a IN @@apps
                        FILTER a._key == @connector_id
                        RETURN a
                )

                // Then find users connected via userAppRelation
                FOR edge IN @@user_app_relation
                    FILTER edge._to == app._id
                    LET user = DOCUMENT(edge._from)
                    FILTER user != null

                    // Verify user belongs to the organization
                    LET belongs_to_org = FIRST(
                        FOR org_edge IN @@belongs_to
                            FILTER org_edge._from == user._id
                            FILTER org_edge._to == CONCAT('organizations/', @org_id)
                            FILTER org_edge.entityType == 'ORGANIZATION'
                            RETURN true
                    )
                    FILTER belongs_to_org == true

                    RETURN MERGE(user, {
                        sourceUserId: edge.sourceUserId,
                        appName: UPPER(app.type),
                        connectorId: app._key
                    })
            """

            cursor = self.db.aql.execute(query, bind_vars={
                "org_id": org_id,
                "connector_id": connector_id,
                "@apps": CollectionNames.APPS.value,
                "@user_app_relation": CollectionNames.USER_APP_RELATION.value,
                "@belongs_to": CollectionNames.BELONGS_TO.value
            })

            users  = list(cursor)
            self.logger.info(f"✅ Successfully fetched {len(users)} users for {connector_id}")
            return users

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch users for {connector_id}: {str(e)}")
            return []

    async def get_user_groups(
        self,
        connector_id: str,
        org_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> List[AppUserGroup]:
        """
        Get all user groups for a specific connector and organization.

        Args:
            connector_id: Connector ID
            org_id: Organization ID
            transaction: Optional transaction database context

        Returns:
            List[AppUserGroup]: List of user group entities
        """
        try:
            self.logger.info(
                "🚀 Retrieving user groups for connector %s and org %s", connector_id, org_id
            )

            query = f"""
            FOR group IN {CollectionNames.GROUPS.value}
                FILTER group.connectorId == @connector_id
                    AND group.orgId == @org_id
                RETURN group
            """

            db = transaction if transaction else self.db

            cursor = db.aql.execute(
                query,
                bind_vars={
                    "connector_id": connector_id,
                    "org_id": org_id
                }
            )

            groups = [AppUserGroup.from_arango_base_user_group(group_data) for group_data in cursor]

            self.logger.info(
                "✅ Successfully retrieved %d user groups for connector %s", len(groups), connector_id
            )
            return groups

        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve user groups for connector %s: %s", connector_id, str(e)
            )
            return []

    async def upsert_sync_point(self, sync_point_key: str, sync_point_data: Dict, collection: str, transaction: Optional[TransactionDatabase] = None) -> bool:
        """
        Upsert a sync point node based on sync_point_key
        """
        try:
            self.logger.info("🚀 Upserting sync point node: %s", sync_point_key)

            # Prepare the document data with the sync_point_key included
            document_data = {
                **sync_point_data,
                "syncPointKey": sync_point_key  # Ensure the key is in the document
            }
            # this is done (REPLACE MERGE(UNSET(OLD, 'syncPointData'), @document_data)) because we want to remove the syncPointData key if it exists (from old nested structure)
            query = """
            UPSERT { syncPointKey: @sync_point_key }
            INSERT @document_data
            REPLACE MERGE(UNSET(OLD, 'syncPointData'), @document_data)
            IN @@collection
            RETURN { action: OLD ? "updated" : "inserted", key: NEW._key }
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={
                "sync_point_key": sync_point_key,
                "document_data": document_data,
                "@collection": collection
            })
            result = next(cursor, None)

            if result:
                action = result.get("action", "unknown")
                self.logger.info("✅ Successfully %s sync point node: %s", action, sync_point_key)
                return True
            else:
                self.logger.warning("⚠️ Failed to upsert sync point node: %s", sync_point_key)
                return False

        except Exception as e:
            self.logger.error("❌ Failed to upsert sync point node: %s: %s", sync_point_key, str(e))
            return False

    async def get_sync_point(self, key: str, collection: str, transaction: Optional[TransactionDatabase] = None) -> Optional[Dict]:
        """
        Get a node by key
        """
        try:
            self.logger.info("🚀 Retrieving node by key: %s", key)
            query = """
            FOR node IN @@collection
                FILTER node.syncPointKey == @key
                RETURN node
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"key": key, "@collection": collection})
            result = next(cursor, None)
            if result:
                self.logger.info("✅ Successfully retrieved node by key: %s", key)
                return result
            else:
                self.logger.warning("⚠️ No node found by key: %s", key)
                return None
        except Exception as e:
            self.logger.error("❌ Failed to retrieve node by key: %s: %s", key, str(e))
            return None

    async def remove_sync_point(self, key: str, collection: str, transaction: Optional[TransactionDatabase] = None) -> bool:
        """
        Remove a node by key
        """
        try:
            self.logger.info("🚀 Removing node by key: %s", key)
            query = """
            FOR node IN @@collection
                FILTER node.syncPointKey == @key
                REMOVE node IN @@collection
                RETURN 1
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"key": key, "@collection": collection})
            result = next(cursor, None)
            if result:
                self.logger.info("✅ Successfully removed node by key: %s", key)
                return True
            else:
                self.logger.warning("⚠️ No node found by key: %s", key)
                return False
        except Exception as e:
            self.logger.error("❌ Failed to remove node by key: %s: %s", key, str(e))
            return False

    async def get_all_documents(self, collection: str, transaction: Optional[TransactionDatabase] = None) -> List[Dict]:
        """
        Get all documents from a collection
        """
        try:
            self.logger.info("🚀 Getting all documents from collection: %s", collection)
            query = """
            FOR doc IN @@collection
                RETURN doc
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"@collection": collection})
            result = list(cursor)
            return result
        except Exception as e:
            self.logger.error("❌ Failed to get all documents from collection: %s: %s", collection, str(e))
            return []

    async def get_app_by_name(self, name: str, transaction: Optional[TransactionDatabase] = None) -> Optional[Dict]:
        """
        Get an app by its name (case-insensitive, ignoring spaces)
        """
        try:
            self.logger.info("🚀 Getting app by name: %s", name)
            query = """
            FOR app IN @@collection
                FILTER LOWER(SUBSTITUTE(app.name, ' ', '')) == LOWER(SUBSTITUTE(@name, ' ', ''))
                RETURN app
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"name": name, "@collection": CollectionNames.APPS.value})
            result = next(cursor, None)
            if result:
                self.logger.info("✅ Successfully retrieved app by name: %s", name)
                return result
            else:
                self.logger.warning("⚠️ No app found by name: %s", name)
                return None
        except Exception as e:
            self.logger.error("❌ Failed to get app by name: %s: %s", name, str(e))
            return None

    async def delete_nodes(self, keys: List[str], collection: str, transaction: Optional[TransactionDatabase] = None) -> bool:
        """
        Delete a list of nodes by key
        """
        try:
            self.logger.info(f"🚀 Deleting nodes by keys: {keys} from collection: {collection}")
            query = """
            FOR node IN @@collection
                FILTER node._key IN @keys
                REMOVE node IN @@collection
                RETURN OLD
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"keys": keys, "@collection": collection})

            # Collect all deleted nodes
            deleted_nodes = list(cursor)

            if deleted_nodes:
                self.logger.info("✅ Successfully deleted %d nodes by keys: %s", len(deleted_nodes), keys)
                return True
            else:
                self.logger.warning("⚠️ No nodes found by keys: %s", keys)
                return False
        except Exception as e:
            self.logger.error("❌ Failed to delete nodes by keys: %s: %s", keys, str(e))
            return False

    async def delete_nodes_and_edges(
        self,
        keys: List[str],
        collection: str,
        graph_name: str = GraphNames.KNOWLEDGE_GRAPH.value,
        transaction: Optional[TransactionDatabase] = None
    ) -> bool:
        """
        Deletes a list of nodes by key and all their connected edges within a named graph.

        This method is efficient for bulk deletions by first discovering all edge
        collections in the graph and then running one bulk-delete query per collection.
        """
        if not keys:
            self.logger.info("No keys provided for deletion. Skipping.")
            return True

        # Use the provided transaction or the main DB connection
        db = transaction if transaction else self.db
        if not db:
            self.logger.error("❌ Database connection is not available.")
            return False

        try:
            self.logger.info(f"🚀 Starting deletion of nodes {keys} from '{collection}' and their edges in graph '{graph_name}'.")

            # --- Step 1: Get all edge collections from the named graph definition ---
            graph = db.graph(graph_name)
            edge_definitions = graph.edge_definitions()
            edge_collections = [e['edge_collection'] for e in edge_definitions]

            if not edge_collections:
                self.logger.warning(f"⚠️ Graph '{graph_name}' has no edge collections defined.")
            else:
                self.logger.info(f"🔎 Found edge collections in graph: {edge_collections}")

            # --- Step 2: Delete all edges connected to the target nodes ---
            # Construct the full node IDs to match against _from and _to fields
            node_ids = [f"{collection}/{key}" for key in keys]

            edge_delete_query = """
            FOR edge IN @@edge_collection
                FILTER edge._from IN @node_ids OR edge._to IN @node_ids
                REMOVE edge IN @@edge_collection
                OPTIONS { ignoreErrors: true }
            """

            for edge_collection in edge_collections:
                db.aql.execute(
                    edge_delete_query,
                    bind_vars={
                        "node_ids": node_ids,
                        "@edge_collection": edge_collection
                    }
                )
            self.logger.info(f"🔥 Successfully ran edge cleanup for nodes: {keys}")

            # --- Step 3: Delete the nodes themselves use delete node here---
            # node_delete_query = """
            # FOR node IN @@collection
            #     FILTER node._key IN @keys
            #     REMOVE node IN @@collection
            #     RETURN OLD
            # """
            # cursor = db.aql.execute(
            #     node_delete_query,
            #     bind_vars={"keys": keys, "@collection": collection}
            # )

            # deleted_nodes = [item for item in cursor]

            deleted_nodes = await self.delete_nodes(keys, collection)

            if deleted_nodes:
                self.logger.info(f"✅ Successfully deleted nodes and their associated edges: {keys}")
                return True
            else:
                self.logger.warning(f"⚠️ No nodes found in '{collection}' with keys: {keys}")
                return False

        except Exception as e:
            self.logger.error(f"❌ Failed to delete nodes and edges for keys {keys}: {e}", exc_info=True)
            return False

    async def delete_record_generic(
        self,
        record_id: str,
        graph_name: str = GraphNames.KNOWLEDGE_GRAPH.value,
        transaction: Optional[TransactionDatabase] = None
    ) -> bool:
        """
        Deletes a record node, all its connected edges, and the node
        connected via the 'isOfType' edge.

        This method:
        1. Finds the node connected to the record via 'isOfType' edge (record -> type node)
        2. Deletes all edges connected to the record node
        3. Deletes the record node itself
        4. Deletes the connected type node (and its edges)

        Args:
            record_id: The record node key to delete
            graph_name: The name of the graph
            transaction: Optional transaction database connection

        Returns:
            bool: True if deletion was successful, False otherwise
        """
        if not record_id:
            self.logger.info("No record_id provided for deletion. Skipping.")
            return True

        db = transaction if transaction else self.db
        if not db:
            self.logger.error("❌ Database connection is not available.")
            return False

        try:
            self.logger.info(f"🚀 Starting deletion of record '{record_id}' and its isOfType connected node.")

            record_full_id = f"records/{record_id}"

            # --- Step 1: Find the node connected via isOfType edge (record -> type node) ---
            find_type_node_query = """
            FOR edge IN isOfType
                FILTER edge._from == @record_id
                LIMIT 1
                RETURN edge._to
            """

            cursor = db.aql.execute(
                find_type_node_query,
                bind_vars={"record_id": record_full_id}
            )

            # Get the single connected type node (if exists)
            connected_type_node_id = next(cursor, None)

            if connected_type_node_id:
                self.logger.info(f"🔎 Found connected type node via isOfType: {connected_type_node_id}")
            else:
                self.logger.info("ℹ️ No node connected via isOfType edge found.")

            # --- Step 2: Delete the record node and all its edges ---
            record_deleted = await self.delete_nodes_and_edges(
                keys=[record_id],
                collection="records",
                graph_name=graph_name,
                transaction=transaction
            )

            if not record_deleted:
                self.logger.warning(f"⚠️ Failed to delete record node: {record_id}")
                return False

            # --- Step 3: Delete the connected type node and its edges ---
            if connected_type_node_id:
                # connected_type_node_id format: "CollectionName/key"
                parts = connected_type_node_id.split("/", 1)
                parts_length = 2
                if len(parts) == parts_length:
                    type_collection, type_node_key = parts
                    self.logger.info(f"🗑️ Deleting connected type node from '{type_collection}': {type_node_key}")
                    type_node_deleted = await self.delete_nodes_and_edges(
                        keys=[type_node_key],
                        collection=type_collection,
                        graph_name=graph_name,
                        transaction=transaction
                    )
                    if not type_node_deleted:
                        self.logger.error(f"❌ Failed to delete connected type node {connected_type_node_id}. The main record {record_id} was deleted, but this node may be orphaned.")
                        return False

            self.logger.info(f"✅ Successfully deleted record '{record_id}' and its connected type node.")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to delete record '{record_id}': {str(e)}")
            return False

    async def delete_edge(self, from_key: str, to_key: str, collection: str, transaction: Optional[TransactionDatabase] = None) -> bool:
        """
        Delete an edge by from_key and to_key
        """
        try:
            self.logger.info("🚀 Deleting edge by from_key: %s and to_key: %s", from_key, to_key)
            query = """
            FOR edge IN @@collection
                FILTER edge._from == @from_key AND edge._to == @to_key
                REMOVE edge IN @@collection
                RETURN OLD
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"from_key": from_key, "to_key": to_key, "@collection": collection})
            result = next(cursor, None)
            if result:
                self.logger.info("✅ Successfully deleted edge by from_key: %s and to_key: %s", from_key, to_key)
                return True
            else:
                self.logger.warning("⚠️ No edge found by from_key: %s and to_key: %s", from_key, to_key)
                return False
        except Exception as e:
            self.logger.error("❌ Failed to delete edge by from_key: %s and to_key: %s: %s", from_key, to_key, str(e))
            return False

    async def delete_edges_from(self, from_key: str, collection: str, transaction: Optional[TransactionDatabase] = None) -> int:
        """
        Delete all edges originating from a specific source node

        Args:
            from_key: The source node key (e.g., "groups/12345")
            collection: The edge collection name
            transaction: Optional transaction database

        Returns:
            int: Number of edges deleted
        """
        try:
            self.logger.info("🚀 Deleting all edges from source: %s in collection: %s", from_key, collection)
            query = """
            FOR edge IN @@collection
                FILTER edge._from == @from_key
                REMOVE edge IN @@collection
                RETURN OLD
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"from_key": from_key, "@collection": collection})
            deleted_edges = list(cursor)
            count = len(deleted_edges)

            if count > 0:
                self.logger.info("✅ Successfully deleted %d edges from source: %s", count, from_key)
            else:
                self.logger.warning("⚠️ No edges found from source: %s in collection: %s", from_key, collection)

            return count
        except Exception as e:
            self.logger.error("❌ Failed to delete edges from source: %s in collection: %s: %s", from_key, collection, str(e))
            return 0

    async def delete_parent_child_edges_to(self, to_key: str, transaction: Optional[TransactionDatabase] = None) -> int:
        """
        Delete PARENT_CHILD edges pointing to a specific target record.

        Args:
            to_key: The target node key (e.g., "records/12345")
            transaction: Optional transaction database

        Returns:
            int: Number of edges deleted
        """
        try:
            self.logger.debug("🚀 Deleting PARENT_CHILD edges to target: %s", to_key)
            query = f"""
            FOR edge IN {CollectionNames.RECORD_RELATIONS.value}
                FILTER edge._to == @to_key
                FILTER edge.relationshipType == @relationship_type
                REMOVE edge IN {CollectionNames.RECORD_RELATIONS.value}
                RETURN OLD
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query,
                bind_vars={
                    "to_key": to_key,
                    "relationship_type": RecordRelations.PARENT_CHILD.value,
                },
            )
            deleted_edges = list(cursor)
            deleted_count = len(deleted_edges)
            if deleted_count > 0:
                self.logger.debug("✅ Deleted %d PARENT_CHILD edge(s) to target: %s", deleted_count, to_key)
            return deleted_count
        except Exception as e:
            self.logger.error("❌ Failed to delete PARENT_CHILD edges to target %s: %s", to_key, str(e))
            if transaction:
                raise
            return 0

    async def delete_edges_to(self, to_key: str, collection: str, transaction: Optional[TransactionDatabase] = None) -> int:
        """
        Delete all edges pointing to a specific target node

        Args:
            to_key: The target node key (e.g., "groups/12345")
            collection: The edge collection name
            transaction: Optional transaction database

        Returns:
            int: Number of edges deleted
        """
        try:
            self.logger.info("🚀 Deleting all edges to target: %s in collection: %s", to_key, collection)
            query = """
            FOR edge IN @@collection
                FILTER edge._to == @to_key
                REMOVE edge IN @@collection
                RETURN OLD
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"to_key": to_key, "@collection": collection})
            deleted_edges = list(cursor)
            count = len(deleted_edges)

            if count > 0:
                self.logger.info("✅ Successfully deleted %d edges to target: %s", count, to_key)
            else:
                self.logger.warning("⚠️ No edges found to target: %s in collection: %s", to_key, collection)

            return count
        except Exception as e:
            self.logger.error("❌ Failed to delete edges to target: %s in collection: %s: %s", to_key, collection, str(e))
            return 0

    # This has been replaced by the next fucntion: delete_edges_between_collections
    async def delete_edges_to_groups(self, from_key: str, collection: str, transaction: Optional[TransactionDatabase] = None) -> int:
        """
        Delete all edges from the given node if those edges are pointing to nodes in the groups collection

        Args:
            from_key: The source node key (e.g., "users/12345")
            collection: The edge collection name to search in
            transaction: Optional transaction database

        Returns:
            int: Number of edges deleted
        """
        try:
            self.logger.info("🚀 Deleting edges from %s to groups collection in %s", from_key, collection)

            query = """
            FOR edge IN @@collection
                FILTER edge._from == @from_key
                FILTER IS_SAME_COLLECTION("groups", edge._to)
                REMOVE edge IN @@collection
                RETURN OLD
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={
                "from_key": from_key,
                "@collection": collection
            })

            deleted_edges = list(cursor)
            count = len(deleted_edges)

            if count > 0:
                self.logger.info("✅ Successfully deleted %d edges from %s to groups", count, from_key)
            else:
                self.logger.warning("⚠️ No edges found from %s to groups in collection: %s", from_key, collection)

            return count

        except Exception as e:
            self.logger.error("❌ Failed to delete edges from %s to groups in %s: %s", from_key, collection, str(e))
            return 0

    async def delete_edges_between_collections(
        self,
        from_key: str,
        edge_collection: str,
        to_collection: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> int:
        """
        Delete all edges from a specific node to any nodes in the target collection.

        Args:
            from_key: The source node key (e.g., "users/12345")
            edge_collection: The edge collection name to search in
            to_collection: The target collection name (edges pointing to nodes in this collection will be deleted)
            transaction: Optional transaction database

        Returns:
            int: Number of edges deleted
        """
        try:
            self.logger.info(
                "🚀 Deleting edges from %s to %s collection in %s",
                from_key, to_collection, edge_collection
            )

            query = """
            FOR edge IN @@edge_collection
                FILTER edge._from == @from_key
                FILTER IS_SAME_COLLECTION(@to_collection, edge._to)
                REMOVE edge IN @@edge_collection
                RETURN OLD
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={
                "from_key": from_key,
                "@edge_collection": edge_collection,
                "to_collection": to_collection
            })

            deleted_edges = list(cursor)
            count = len(deleted_edges)

            if count > 0:
                self.logger.info(
                    "✅ Successfully deleted %d edges from %s to %s",
                    count, from_key, to_collection
                )
            else:
                self.logger.warning(
                    "⚠️ No edges found from %s to %s in collection: %s",
                    from_key, to_collection, edge_collection
                )

            return count

        except Exception as e:
            self.logger.error(
                "❌ Failed to delete edges from %s to %s in %s: %s",
                from_key, to_collection, edge_collection, str(e)
            )
            return 0

    async def delete_all_edges_for_node(self, node_key: str, collection: str, transaction: Optional[TransactionDatabase] = None) -> int:
        """
        Delete all edges connected to a node (both incoming and outgoing)

        Args:
            node_key: The node key (e.g., "groups/12345")
            collection: The edge collection name
            transaction: Optional transaction database

        Returns:
            int: Total number of edges deleted
        """
        try:
            self.logger.info("🚀 Deleting all edges for node: %s in collection: %s", node_key, collection)

            # Delete both incoming and outgoing edges in a single query
            query = """
            FOR edge IN @@collection
                FILTER edge._from == @node_key OR edge._to == @node_key
                REMOVE edge IN @@collection
                RETURN OLD
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"node_key": node_key, "@collection": collection})
            deleted_edges = list(cursor)
            count = len(deleted_edges)

            if count > 0:
                self.logger.info("✅ Successfully deleted %d edges for node: %s", count, node_key)
            else:
                self.logger.warning("⚠️ No edges found for node: %s in collection: %s", node_key, collection)

            return count
        except Exception as e:
            self.logger.error("❌ Failed to delete edges for node: %s in collection: %s: %s", node_key, collection, str(e))
            return 0

    async def get_edge(self, from_key: str, to_key: str, collection: str, transaction: Optional[TransactionDatabase] = None) -> Optional[Dict]:
        """
        Get an edge by from_key and to_key
        """
        try:
            self.logger.info("🚀 Getting permission by from_key: %s and to_key: %s", from_key, to_key)
            query = """
            FOR edge IN @@collection
                FILTER edge._from == @from_key AND edge._to == @to_key
                RETURN edge
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"from_key": from_key, "to_key": to_key, "@collection": collection})
            result = next(cursor, None)
            if result:
                self.logger.info("✅ Successfully got edge by from_key: %s and to_key: %s", from_key, to_key)
                return result
            else:
                self.logger.warning("⚠️ No edge found by from_key: %s and to_key: %s", from_key, to_key)
                return None
        except Exception as e:
            self.logger.error("❌ Failed to get edge by from_key: %s and to_key: %s: %s", from_key, to_key, str(e))
            return None

    async def get_edges_from_node(
        self,
        from_key: str,
        collection: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> List[Dict]:
        """
        Get all edges originating from a specific node.

        Args:
            from_key: Source node key (e.g., "groups/12345")
            collection: Edge collection name
            transaction: Optional transaction database

        Returns:
            List[Dict]: List of edge documents
        """
        try:
            self.logger.info("🚀 Getting edges from node: %s in collection: %s", from_key, collection)
            query = """
            FOR edge IN @@collection
                FILTER edge._from == @from_key
                RETURN edge
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"from_key": from_key, "@collection": collection})
            edges = list(cursor)
            count = len(edges)

            if count > 0:
                self.logger.info("✅ Successfully got %d edges from node: %s", count, from_key)
            else:
                self.logger.warning("⚠️ No edges found from node: %s in collection: %s", from_key, collection)

            return edges
        except Exception as e:
            self.logger.error("❌ Failed to get edges from node: %s in collection: %s: %s", from_key, collection, str(e))
            return []

    async def update_node(self, key: str, node_updates: Dict, collection: str, transaction: Optional[TransactionDatabase] = None) -> bool:
        """
        Update a node by key
        """
        try:
            self.logger.info("🚀 Updating node by key: %s", key)
            query = """
            FOR node IN @@collection
                FILTER node._key == @key
                UPDATE node WITH @node_updates IN @@collection
                RETURN NEW
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"key": key, "node_updates": node_updates, "@collection": collection})
            result_list = list(cursor)
            result = result_list[0] if result_list else None
            if result:
                self.logger.info("✅ Successfully updated node by key: %s", key)
                return True
            else:
                self.logger.warning("⚠️ No node found by key: %s", key)
                return False
        except Exception as e:
            self.logger.error("❌ Failed to update node by key: %s: %s", key, str(e))
            return False

    async def update_edge(self, from_key: str, to_key: str, edge_updates: Dict, collection: str, transaction: Optional[TransactionDatabase] = None) -> bool:
        """
        Update an edge by from_key and to_key
        """
        try:
            self.logger.info("🚀 Updating edge by from_key: %s and to_key: %s", from_key, to_key)
            query = """
            FOR edge IN @@collection
                FILTER edge._from == @from_key AND edge._to == @to_key
                UPDATE edge WITH @edge_updates IN @@collection
                RETURN NEW
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"from_key": from_key, "to_key": to_key, "edge_updates": edge_updates, "@collection": collection})
            result_list = list(cursor)
            result = result_list[0] if result_list else None
            if result:
                self.logger.info("✅ Successfully updated edge by from_key: %s and to_key: %s", from_key, to_key)
                return True
            else:
                self.logger.warning("⚠️ No edge found by from_key: %s and to_key: %s", from_key, to_key)
                return False
        except Exception as e:
            self.logger.error("❌ Failed to update edge by from_key: %s and to_key: %s: %s", from_key, to_key, str(e))
            return False

    async def update_edge_by_key(self, key: str, edge_updates: Dict, collection: str, transaction: Optional[TransactionDatabase] = None) -> bool:
        """
        Update an edge by key
        """
        try:
            self.logger.info("🚀 Updating edge by key: %s", key)
            query = """
            FOR edge IN @@collection
                FILTER edge._key == @key
                UPDATE edge WITH @edge_updates IN @@collection
                RETURN NEW
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"key": key, "edge_updates": edge_updates, "@collection": collection})
            result_list = list(cursor)
            result = result_list[0] if result_list else None
            if result:
                self.logger.info("✅ Successfully updated edge by key: %s", key)
                return True
            else:
                self.logger.warning("⚠️ No edge found by key: %s", key)
                return False
        except Exception as e:
            self.logger.error("❌ Failed to update edge by key: %s: %s", key, str(e))
            return False

    async def store_page_token(
        self,
        channel_id: str,
        resource_id: str,
        user_email: str,
        token: str,
        expiration: Optional[str] = None,
        connector_id: Optional[str] = None,
    ) -> Optional[Dict]:
        """Store page token with user channel information"""
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

            if not self.db.has_collection(CollectionNames.PAGE_TOKENS.value):
                self.db.create_collection(CollectionNames.PAGE_TOKENS.value)

            token_doc = {
                "channelId": channel_id,
                "resourceId": resource_id,
                "userEmail": user_email,
                "token": token,
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "expiration": expiration,
            }

            # Add connector_id if provided
            if connector_id:
                token_doc["connectorId"] = connector_id

            # Upsert to handle updates to existing channel tokens
            # Use connector_id in upsert condition if provided
            if connector_id:
                query = """
                UPSERT { userEmail: @userEmail, connectorId: @connectorId }
                INSERT @token_doc
                UPDATE @token_doc
                IN @@pageTokens
                RETURN NEW
                """
                bind_vars = {
                    "userEmail": user_email,
                    "connectorId": connector_id,
                    "token_doc": token_doc,
                    "@pageTokens": CollectionNames.PAGE_TOKENS.value,
                }
            else:
                query = """
                UPSERT { userEmail: @userEmail }
                INSERT @token_doc
                UPDATE @token_doc
                IN @@pageTokens
                RETURN NEW
                """
                bind_vars = {
                    "userEmail": user_email,
                    "token_doc": token_doc,
                    "@pageTokens": CollectionNames.PAGE_TOKENS.value,
                }

            list(
                self.db.aql.execute(
                    query,
                    bind_vars=bind_vars,
                )
            )

            self.logger.info("✅ Page token stored successfully")

        except Exception as e:
            self.logger.error("❌ Error storing page token: %s", str(e))

    async def get_page_token_db(
        self, channel_id: str = None, resource_id: str = None, user_email: str = None, connector_id: Optional[str] = None
    ) -> Optional[Dict]:
        """Get page token for specific channel"""
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
            bind_vars = {"@pageTokens": CollectionNames.PAGE_TOKENS.value}

            if channel_id is not None:
                filters.append("token.channelId == @channel_id")
                bind_vars["channel_id"] = channel_id
            if resource_id is not None:
                filters.append("token.resourceId == @resource_id")
                bind_vars["resource_id"] = resource_id
            if user_email is not None:
                filters.append("token.userEmail == @user_email")
                bind_vars["user_email"] = user_email
            if connector_id is not None:
                filters.append("token.connectorId == @connector_id")
                bind_vars["connector_id"] = connector_id

            if not filters:
                self.logger.warning("⚠️ No filter params provided for page token query")
                return None

            filter_clause = " AND ".join(filters)

            query = f"""
            FOR token IN @@pageTokens
            FILTER {filter_clause}
            SORT token.createdAtTimestamp DESC
            LIMIT 1
            RETURN token
            """

            result = list(self.db.aql.execute(query, bind_vars=bind_vars))

            if result:
                self.logger.info("✅ Found token for channel")
                return result[0]

            self.logger.warning("⚠️ No token found for channel")
            return None

        except Exception as e:
            self.logger.error("❌ Error getting page token: %s", str(e))
            return None

    async def get_all_channel_tokens(self) -> List[Dict]:
        """Get all active channel tokens"""
        try:
            self.logger.info("🚀 Getting all channel tokens")
            query = """
            FOR token IN pageTokens
            RETURN {
                user_email: token.user_email,
                channel_id: token.channel_id,
                resource_id: token.resource_id,
                token: token.token,
                updatedAt: token.updatedAt
            }
            """

            result = list(self.db.aql.execute(query))
            self.logger.info("✅ Retrieved %d channel tokens", len(result))
            return result

        except Exception as e:
            self.logger.error("❌ Error getting all channel tokens: %s", str(e))
            return []

    async def store_channel_history_id(
        self, history_id: str, expiration: str, user_email: str, connector_id: Optional[str] = None
    ) -> None:
        """
        Store the latest historyId for a user's channel watch

        Args:
            user_email (str): Email of the user
            history_id (str): Latest historyId from channel watch
            channel_id (str, optional): Channel ID associated with the watch
            resource_id (str, optional): Resource ID associated with the watch
        """
        try:
            self.logger.info(f"🚀 Storing historyId for user {user_email}")

            # Use connector_id in upsert condition if provided
            if connector_id:
                query = """
                UPSERT { userEmail: @userEmail, connectorId: @connectorId }
                INSERT {
                    userEmail: @userEmail,
                    connectorId: @connectorId,
                    historyId: @historyId,
                    expiration: @expiration,
                    updatedAt: DATE_NOW()
                }
                UPDATE {
                    historyId: @historyId,
                    expiration: @expiration,
                    updatedAt: DATE_NOW()
                } IN channelHistory
                RETURN NEW
                """
                bind_vars = {
                    "userEmail": user_email,
                    "connectorId": connector_id,
                    "historyId": history_id,
                    "expiration": expiration,
                }
            else:
                query = """
                UPSERT { userEmail: @userEmail }
                INSERT {
                    userEmail: @userEmail,
                    historyId: @historyId,
                    expiration: @expiration,
                    updatedAt: DATE_NOW()
                }
                UPDATE {
                    historyId: @historyId,
                    expiration: @expiration,
                    updatedAt: DATE_NOW()
                } IN channelHistory
                RETURN NEW
                """
                bind_vars = {
                    "userEmail": user_email,
                    "historyId": history_id,
                    "expiration": expiration,
                }

            result = list(
                self.db.aql.execute(
                    query,
                    bind_vars=bind_vars,
                )
            )

            if result:
                self.logger.info(f"✅ Successfully stored historyId for {user_email}")

            self.logger.warning(f"⚠️ Failed to store historyId for {user_email}")

        except Exception as e:
            self.logger.error(f"❌ Error storing historyId: {str(e)}")

    async def get_channel_history_id(self, user_email: str, connector_id: Optional[str] = None) -> Optional[str]:
        """
        Retrieve the latest historyId for a user

        Args:
            user_email (str): Email of the user
            connector_id (str): Connector ID (optional)

        Returns:
            Optional[str]: Latest historyId if found, None otherwise
        """
        try:
            self.logger.info(f"🚀 Retrieving historyId for user {user_email}")

            bind_vars = {
                "userEmail": user_email,
            }

            # Use connector_id in filter if provided
            if connector_id:
                query = """
                FOR history IN channelHistory
                FILTER history.userEmail == @userEmail AND history.connectorId == @connectorId
                RETURN history
                """
                bind_vars["connectorId"] = connector_id
            else:
                query = """
                FOR history IN channelHistory
                FILTER history.userEmail == @userEmail
                RETURN history
                """

            result = list(
                self.db.aql.execute(query, bind_vars=bind_vars)
            )

            if result:
                self.logger.info(f"✅ Found historyId for {user_email}")
                return result[0]

            self.logger.warning(f"⚠️ No historyId found for {user_email}")
            return None

        except Exception as e:
            self.logger.error(f"❌ Error retrieving historyId: {str(e)}")
            return None

    async def cleanup_expired_tokens(self, expiry_hours: int = 24) -> int:
        """Clean up tokens that haven't been updated recently"""
        try:
            expiry_time = datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=expiry_hours)

            query = """
            FOR token IN pageTokens
            FILTER token.updatedAt < @expiry_time
            REMOVE token IN pageTokens
            RETURN OLD
            """

            removed = list(
                self.db.aql.execute(query, bind_vars={"expiry_time": expiry_time})
            )

            self.logger.info("🧹 Cleaned up %d expired tokens", len(removed))
            return len(removed)

        except Exception as e:
            self.logger.error("❌ Error cleaning up tokens: %s", str(e))
            return 0

    async def get_file_parents(self, file_key: str, transaction) -> List[Dict]:
        try:
            if not file_key:
                raise ValueError("File ID is required")

            self.logger.info("🚀 Getting parents for record %s", file_key)

            query = """
            LET relations = (
                FOR rel IN @@recordRelations
                    FILTER rel._to == @record_id
                    RETURN rel._from
            )

            LET parent_keys = (
                FOR rel IN relations
                    LET key = PARSE_IDENTIFIER(rel).key
                    RETURN {
                        original_id: rel,
                        parsed_key: key
                    }
            )

            LET parent_files = (
                FOR parent IN parent_keys
                    FOR record IN @@records
                        FILTER record._key == parent.parsed_key
                        RETURN {
                            key: record._key,
                            externalRecordId: record.externalRecordId
                        }
            )

            RETURN {
                input_file_key: @file_key,
                found_relations: relations,
                parsed_parent_keys: parent_keys,
                found_parent_files: parent_files
            }
            """

            bind_vars = {
                "file_key": file_key,
                "record_id": CollectionNames.RECORDS.value + "/" + file_key,
                "@records": CollectionNames.RECORDS.value,
                "@recordRelations": CollectionNames.RECORD_RELATIONS.value,
            }

            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars=bind_vars)
            result = list(cursor)

            if not result or not result[0]["found_relations"]:
                self.logger.warning("⚠️ No relations found for record %s", file_key)
            if not result or not result[0]["parsed_parent_keys"]:
                self.logger.warning("⚠️ No parent keys parsed for record %s", file_key)
            if not result or not result[0]["found_parent_files"]:
                self.logger.warning("⚠️ No parent files found for record %s", file_key)

            # Return just the external file IDs if everything worked
            return (
                [
                    record["externalRecordId"]
                    for record in result[0]["found_parent_files"]
                ]
                if result
                else []
            )

        except ValueError as ve:
            self.logger.error(f"❌ Validation error: {str(ve)}")
            return []
        except Exception as e:
            self.logger.error(
                "❌ Error getting parents for record %s: %s", file_key, str(e)
            )
            return []

    async def get_entity_id_by_email(
        self, email: str, transaction: Optional[TransactionDatabase] = None
    ) -> Optional[str]:
        """
        Get user or group ID by email address

        Args:
            email (str): Email address to look up

        Returns:
            Optional[str]: Entity ID (_key) if found, None otherwise
        """
        try:
            self.logger.info("🚀 Getting Entity Key by mail")

            # First check users collection
            query = """
            FOR doc IN users
                FILTER doc.email == @email
                RETURN doc._key
            """
            db = transaction if transaction else self.db
            result = db.aql.execute(query, bind_vars={"email": email})
            user_id = next(result, None)
            if user_id:
                self.logger.info("✅ Got User ID: %s", user_id)
                return user_id

            # If not found in users, check groups collection
            query = """
            FOR doc IN groups
                FILTER doc.email == @email
                RETURN doc._key
            """
            result = db.aql.execute(query, bind_vars={"email": email})
            group_id = next(result, None)
            if group_id:
                self.logger.info("✅ Got group ID: %s", group_id)
                return group_id

            query = """
            FOR doc IN people
                FILTER doc.email == @email
                RETURN doc._key
            """
            result = db.aql.execute(query, bind_vars={"email": email})
            people_id = next(result, None)
            if people_id:
                self.logger.info("✅ Got People ID: %s", people_id)
                return people_id

            return None

        except Exception as e:
            self.logger.error(
                "❌ Failed to get entity ID for email %s: %s", email, str(e)
            )
            return None

    async def bulk_get_entity_ids_by_email(
        self,
        emails: List[str],
        transaction: Optional[TransactionDatabase] = None
    ) -> Dict[str, Tuple[str, str, str]]:
        """
        Bulk get entity IDs for multiple emails across users, groups, and people collections

        Args:
            emails (List[str]): List of email addresses to look up
            transaction: Optional transaction database context
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
            self.logger.info("🚀 Bulk getting Entity Keys for %d emails", len(emails))

            result_map = {}
            db = transaction if transaction else self.db

            # Deduplicate emails to avoid redundant queries
            unique_emails = list(set(emails))

            # ===================================================
            # QUERY 1: Check users collection
            # ===================================================
            user_query = """
            FOR doc IN users
                FILTER doc.email IN @emails
                RETURN {email: doc.email, id: doc._key}
            """
            try:
                users = list(db.aql.execute(user_query, bind_vars={"emails": unique_emails}))
                for user in users:
                    result_map[user["email"]] = (
                        user["id"],
                        CollectionNames.USERS.value,
                        "USER"
                    )
                self.logger.info("✅ Found %d users", len(users))
            except Exception as e:
                self.logger.error("❌ Error querying users: %s", str(e))

            # ===================================================
            # QUERY 2: Check groups collection (only for remaining emails)
            # ===================================================
            remaining_emails = [e for e in unique_emails if e not in result_map]
            if remaining_emails:
                group_query = """
                FOR doc IN groups
                    FILTER doc.email IN @emails
                    RETURN {email: doc.email, id: doc._key}
                """
                try:
                    groups = list(db.aql.execute(group_query, bind_vars={"emails": remaining_emails}))
                    for group in groups:
                        result_map[group["email"]] = (
                            group["id"],
                            CollectionNames.GROUPS.value,
                            "GROUP"
                        )
                    self.logger.info("✅ Found %d groups", len(groups))
                except Exception as e:
                    self.logger.error("❌ Error querying groups: %s", str(e))

            # ===================================================
            # QUERY 3: Check people collection (only for remaining emails)
            # ===================================================
            remaining_emails = [e for e in unique_emails if e not in result_map]
            if remaining_emails:
                people_query = """
                FOR doc IN people
                    FILTER doc.email IN @emails
                    RETURN {email: doc.email, id: doc._key}
                """
                try:
                    people = list(db.aql.execute(people_query, bind_vars={"emails": remaining_emails}))
                    for person in people:
                        result_map[person["email"]] = (
                            person["id"],
                            CollectionNames.PEOPLE.value,
                            "USER"
                        )
                    self.logger.info("✅ Found %d people", len(people))
                except Exception as e:
                    self.logger.error("❌ Error querying people: %s", str(e))

            self.logger.info(
                "✅ Bulk lookup complete: found %d/%d entities",
                len(result_map),
                len(unique_emails)
            )

            return result_map

        except Exception as e:
            self.logger.error("❌ Failed to bulk get entity IDs: %s", str(e))
            return {}

    async def organization_exists(self, organization_name: str) -> bool:
        """Check if the organization exists in the database"""
        self.logger.info("🚀 Checking whether the organization exists")
        query = """
        FOR doc IN @@orgs
            FILTER doc.name == @organization_name
            RETURN doc._key
        """
        result = self.db.aql.execute(
            query,
            bind_vars={
                "organization_name": organization_name,
                "@orgs": CollectionNames.ORGS.value,
            },
        )
        response = bool(next(result, None))
        self.logger.info("Does Organization exist?: %s", response)
        return response

    async def get_group_members(self, group_id: str) -> List[Dict]:
        """Get all users in a group"""
        try:
            self.logger.info("🚀 Getting group members for %s", group_id)
            query = """
            FOR user, membership IN 1..1 OUTBOUND @group_id GRAPH 'fileAccessGraph'
                FILTER membership.type == 'membership'
                RETURN DISTINCT user
            """

            cursor = self.db.aql.execute(
                query, bind_vars={"group_id": f"groups/{group_id}"}
            )
            self.logger.info("✅ Group members retrieved successfully")
            return list(cursor)

        except Exception as e:
            self.logger.error("❌ Failed to get group members: %s", str(e))
            return []

    async def get_file_permissions(
        self, file_key: str, transaction: Optional[TransactionDatabase] = None
    ) -> List[Dict]:
        """Get current permissions for a file"""
        try:
            self.logger.info("🚀 Getting file permissions for %s", file_key)
            query = """
            FOR perm IN @@permission
                FILTER perm._to == @file_key
                RETURN perm
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query,
                bind_vars={
                    "file_key": f"{CollectionNames.RECORDS.value}/{file_key}",
                    "@permission": CollectionNames.PERMISSION.value,
                },
            )
            self.logger.info("✅ File permissions retrieved successfully")
            return list(cursor)

        except Exception as e:
            self.logger.error("❌ Failed to get file permissions: %s", str(e))
            return []

    async def store_permission(
        self,
        file_key: str,
        entity_key: str,
        permission_data: Dict,
        transaction: Optional[TransactionDatabase] = None,
    ) -> bool:
        """Store or update permission relationship with change detection"""
        try:
            self.logger.info(
                "🚀 Storing permission for file %s and entity %s", file_key, entity_key
            )

            if not entity_key:
                self.logger.warning("⚠️ Cannot store permission - missing entity_key")
                return False

            # Use transaction if provided, otherwise use self.db
            db = transaction if transaction else self.db

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

            self.logger.info("Permission data is %s", permission_data)
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
            self.logger.debug("Creating edge document: %s", edge)

            # Check if permission edge exists using AQL (works with transactions)
            try:
                # Use AQL query to get existing edge instead of direct collection access
                get_edge_query = """
                    FOR edge IN @@permission
                        FILTER edge._key == @edge_key
                        RETURN edge
                """
                cursor = db.aql.execute(
                    get_edge_query,
                    bind_vars={
                        "@permission": CollectionNames.PERMISSION.value,
                        "edge_key": edge_key,
                    },
                )
                existing_edge_list = list(cursor)
                existing_edge = existing_edge_list[0] if existing_edge_list else None

                if not existing_edge:
                    # New permission - use batch_upsert_nodes which handles transactions properly
                    self.logger.info("✅ Creating new permission edge: %s", edge_key)
                    await self.batch_upsert_nodes(
                        [edge],
                        collection=CollectionNames.PERMISSION.value,
                        transaction=transaction
                    )
                    self.logger.info("✅ Created new permission edge: %s", edge_key)
                elif self._permission_needs_update(existing_edge, permission_data):
                    # Update existing permission
                    self.logger.info("✅ Updating permission edge: %s", edge_key)
                    await self.batch_upsert_nodes(
                        [edge],
                        collection=CollectionNames.PERMISSION.value,
                        transaction=transaction
                    )
                    self.logger.info("✅ Updated permission edge: %s", edge_key)
                else:
                    self.logger.info(
                        "✅ No update needed for permission edge: %s", edge_key
                    )

                return True

            except Exception as e:
                self.logger.error(
                    "❌ Failed to access permissions collection: %s", str(e)
                )
                if transaction:
                    raise
                return False

        except Exception as e:
            self.logger.error("❌ Failed to store permission: %s", str(e))
            if transaction:
                raise
            return False

    async def store_membership(
        self, group_id: str, user_id: str, role: str = "member"
    ) -> bool:
        """Store group membership"""
        try:
            self.logger.info(
                "🚀 Storing membership for group %s and user %s", group_id, user_id
            )
            edge = {
                "_from": f"groups/{group_id}",
                "_to": f"users/{user_id}",
                "type": "membership",
                "role": role,
            }
            self._collections[CollectionNames.BELONGS_TO.value].insert(
                edge, overwrite=True
            )
            self.logger.info("✅ Membership stored successfully")
            return True
        except Exception as e:
            self.logger.error("❌ Failed to store membership: %s", str(e))
            return False

    async def process_file_permissions(
        self,
        org_id: str,
        file_key: str,
        permissions_data: List[Dict],
        transaction: Optional[TransactionDatabase] = None,
    ) -> bool:
        """
        Process file permissions by comparing new permissions with existing ones.
        Assumes all entities and files already exist in the database.
        """
        try:
            self.logger.info("🚀 Processing permissions for file %s", file_key)
            timestamp = get_epoch_timestamp_in_ms()

            db = transaction if transaction else self.db

            # Get existing permissions for comparison
            # Remove 'anyone' permission for this file if it exists
            query = """
            FOR a IN anyone
                FILTER a.file_key == @file_key
                FILTER a.organization == @org_id
                REMOVE a IN anyone
            """
            db.aql.execute(query, bind_vars={"file_key": file_key, "org_id": org_id})
            self.logger.info("🗑️ Removed 'anyone' permission for file %s", file_key)

            existing_permissions = await self.get_file_permissions(
                file_key, transaction=transaction
            )
            self.logger.info("🚀 Existing permissions: %s", existing_permissions)

            # Get all permission IDs from new permissions
            new_permission_ids = list({p.get("id") for p in permissions_data})
            self.logger.info("🚀 New permission IDs: %s", new_permission_ids)
            # Find permissions that exist but are not in new permissions
            permissions_to_remove = [
                perm
                for perm in existing_permissions
                if perm.get("externalPermissionId") not in new_permission_ids
            ]

            # Remove permissions that no longer exist
            if permissions_to_remove:
                self.logger.info(
                    "🗑️ Removing %d obsolete permissions", len(permissions_to_remove)
                )
                # Check if 'anyone' type permissions exist in new permissions

                for perm in permissions_to_remove:
                    query = """
                    FOR p IN permission
                        FILTER p._key == @perm_key
                        REMOVE p IN permission
                    """
                    db.aql.execute(query, bind_vars={"perm_key": perm["_key"]})

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
                                    new_perm.get("emailAddress")
                                )
                                if not entity_key:
                                    self.logger.warning(
                                        f"⚠️ Skipping permission for non-existent user or group: {entity_key}"
                                    )
                                    pass
                            elif perm_type == "domain":
                                entity_key = org_id
                                if not entity_key:
                                    self.logger.warning(
                                        f"⚠️ Skipping permission for non-existent domain: {entity_key}"
                                    )
                                    pass
                            else:
                                entity_key = None
                                # Skip if entity doesn't exist
                                if not entity_key:
                                    self.logger.warning(
                                        f"⚠️ Skipping permission for non-existent entity: {entity_key}"
                                    )
                                    pass
                            if entity_key != "anyone" and entity_key:
                                self.logger.info(
                                    "🚀 Storing permission for file %s and entity %s: %s",
                                    file_key,
                                    entity_key,
                                    new_perm,
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
                            [permission_data], collection=CollectionNames.ANYONE.value
                        )

            self.logger.info(
                "✅ Successfully processed all permissions for file %s", file_key
            )
            return True

        except Exception as e:
            self.logger.error("❌ Failed to process permissions: %s", str(e))
            if transaction:
                raise
            return False


    def _get_access_level(self, role: str) -> int:
        """Convert role to numeric access level for easy comparison"""
        role_levels = {
            "owner": 100,
            "organizer": 90,
            "fileorganizer": 80,
            "writer": 70,
            "commenter": 60,
            "reader": 50,
            "none": 0,
        }
        return role_levels.get(role.lower(), 0)

    async def _cleanup_old_permissions(
        self, file_id: str, current_entities: Set[Tuple[str, str]]
    ) -> None:
        """Mark old access edges as inactive"""
        try:
            self.logger.info("🚀 Cleaning up old permissions for file %s", file_id)

            query = """
            FOR edge IN permission
                FILTER edge._to == @file_id
                AND NOT ([PARSE_IDENTIFIER(edge._from).key, PARSE_IDENTIFIER(edge._from).collection] IN @current_entities)
                UPDATE edge WITH {
                    hasAccess: false,
                } IN permission
            """

            self.db.aql.execute(
                query,
                bind_vars={
                    "file_id": f"records/{file_id}",
                    "current_entities": list(current_entities),
                },
            )
            self.logger.info("✅ Old access edges cleaned up successfully")

        except Exception as e:
            self.logger.error("❌ Failed to cleanup old access edges: %s", str(e))
            return

    def _permission_needs_update(self, existing: Dict, new: Dict) -> bool:
        """Check if permission data needs to be updated"""
        self.logger.info("🚀 Checking if permission data needs to be updated")
        relevant_fields = ["role", "permissionDetails", "active"]

        for field in relevant_fields:
            if field in new:
                if field == "permissionDetails":
                    if json.dumps(new[field], sort_keys=True) != json.dumps(
                        existing.get(field, {}), sort_keys=True
                    ):
                        self.logger.info("✅ Permission data needs to be updated. Field %s", field)
                        return True
                elif new[field] != existing.get(field):
                    self.logger.info("✅ Permission data needs to be updated. Field %s", field)
                    return True

        self.logger.info("✅ Permission data does not need to be updated")
        return False

    async def get_file_access_history(
        self, file_id: str, transaction: Optional[TransactionDatabase] = None
    ) -> List[Dict]:
        """Get historical access information for a file"""
        try:
            self.logger.info(
                "🚀 Getting historical access information for file %s", file_id
            )
            query = """
            FOR perm IN permission
                FILTER perm._to == @file_id
                SORT perm.lastUpdatedTimestampAtSource DESC
                RETURN {
                    entity: DOCUMENT(perm._from),
                    permission: perm
                }
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"file_id": f"records/{file_id}"})
            self.logger.info("✅ File access history retrieved successfully")
            return list(cursor)

        except Exception as e:
            self.logger.error("❌ Failed to get file access history: %s", str(e))
            return []

    async def delete_records_and_relations(
        self,
        node_key: str,
        hard_delete: bool = False,
        transaction: Optional[TransactionDatabase] = None,
    ) -> bool:
        """Delete a node and its edges from all edge collections (Records, Files)."""
        try:
            self.logger.info(
                "🚀 Deleting node %s from collection Records, Files (hard_delete=%s)",
                node_key,
                hard_delete,
            )

            db = transaction if transaction else self.db

            record = await self.get_document(node_key, CollectionNames.RECORDS.value)
            if not record:
                self.logger.warning(
                    "⚠️ Record %s not found in Records collection", node_key
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
                    db.aql.execute(edge_removal_query, bind_vars=bind_vars)
                    self.logger.info(
                        f"✅ Edges from {edge_collection} deleted for node {node_key}"
                    )
                except Exception as e:
                    self.logger.warning(
                        f"⚠️ Could not delete edges from {edge_collection} for node {node_key}: {str(e)}"
                    )

            # Step 2: Delete node from `records` and `files` collections
            delete_query = """
            LET removed_record = (
                FOR doc IN @@records
                    FILTER doc._key == @node_key
                    REMOVE doc IN @@records
                    RETURN OLD
            )

            LET removed_file = (
                FOR doc IN @@files
                    FILTER doc._key == @node_key
                    REMOVE doc IN @@files
                    RETURN OLD
            )

            LET removed_mail = (
                FOR doc IN @@mails
                    FILTER doc._key == @node_key
                    REMOVE doc IN @@mails
                    RETURN OLD
            )

            RETURN {
                record_removed: LENGTH(removed_record) > 0,
                file_removed: LENGTH(removed_file) > 0,
                mail_removed: LENGTH(removed_mail) > 0
            }
            """
            bind_vars = {
                "node_key": node_key,
                "@records": CollectionNames.RECORDS.value,
                "@files": CollectionNames.FILES.value,
                "@mails": CollectionNames.MAILS.value,
            }

            cursor = db.aql.execute(delete_query, bind_vars=bind_vars)
            result = list(cursor)

            self.logger.info(
                "✅ Node %s and its edges %s deleted: %s",
                node_key,
                "hard" if hard_delete else "soft",
                result,
            )
            return True

        except Exception as e:
            self.logger.error("❌ Failed to delete node %s: %s", node_key, str(e))
            if transaction:
                raise
            return False

    async def get_orgs(self) -> List[Dict]:
        """Get all organizations"""
        try:
            query = "FOR org IN organizations RETURN org"
            cursor = self.db.aql.execute(query)
            return list(cursor)
        except Exception as e:
            self.logger.error("❌ Failed to get organizations: %s", str(e))
            return []


    async def save_to_people_collection(self, entity_id: str, email: str) -> Optional[Dict]:
        """Save an entity to the people collection if it doesn't already exist"""
        try:
            self.logger.info(
                "🚀 Checking if entity %s exists in people collection", entity_id
            )
            # has() checks document _key, not field values
            # Need to query by entity_id field instead
            query = "FOR doc IN people FILTER doc.email == @email RETURN doc"
            exists = list(self.db.aql.execute(query, bind_vars={"email": email}))
            if not exists:
                self.logger.info(
                    "➕ Entity does not exist, saving to people collection"
                )
                timestamp = get_epoch_timestamp_in_ms()
                self.db.collection(CollectionNames.PEOPLE.value).insert(
                    {
                        "_key": entity_id,
                        "email": email,
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp,
                    }
                )
                self.logger.info("✅ Entity %s saved to people collection", entity_id)
                return {
                    "_key": entity_id,
                    "email": email,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                }
            else:
                self.logger.info(
                    "⏩ Entity %s already exists in people collection", entity_id
                )
                return exists[0]
        except Exception as e:
            self.logger.error("❌ Error saving entity to people collection: %s", str(e))
            return None

    async def get_all_pageTokens(self) -> List[Dict]:
        """Get all page tokens from the pageTokens collection.

        Returns:
            list: List of page token documents, or empty list if none found or error occurs
        """
        try:
            if not self.db.has_collection(CollectionNames.PAGE_TOKENS.value):
                self.logger.warning("❌ pageTokens collection does not exist")
                return []

            query = """
            FOR doc IN pageTokens
                RETURN doc
            """

            result = list(self.db.aql.execute(query))

            self.logger.info("✅ Retrieved %d page tokens", len(result))
            return result

        except Exception as e:
            self.logger.error("❌ Error retrieving page tokens: %s", str(e))
            return []

    async def get_key_by_external_file_id(
        self, external_file_id: str, transaction: Optional[TransactionDatabase] = None
    ) -> Optional[str]:
        """
        Get internal file key using the external file ID

        Args:
            external_file_id (str): External file ID to look up
            transaction (Optional[TransactionDatabase]): Optional database transaction

        Returns:
            Optional[str]: Internal file key if found, None otherwise
        """
        try:
            self.logger.info(
                "🚀 Retrieving internal key for external file ID %s", external_file_id
            )

            query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record.externalRecordId == @external_file_id
                RETURN record._key
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query, bind_vars={"external_file_id": external_file_id}
            )
            result = next(cursor, None)

            if result:
                self.logger.info(
                    "✅ Successfully retrieved internal key for external file ID %s",
                    external_file_id,
                )
                return result
            else:
                self.logger.warning(
                    "⚠️ No internal key found for external file ID %s", external_file_id
                )
                return None

        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve internal key for external file ID %s: %s",
                external_file_id,
                str(e),
            )
            return None

    async def get_key_by_attachment_id(
        self,
        external_attachment_id: str,
        transaction: Optional[TransactionDatabase] = None,
    ) -> Optional[str]:
        """
        Get internal attachment key using the external attachment ID

        Args:
            external_attachment_id (str): External attachment ID to look up
            transaction (Optional[TransactionDatabase]): Optional database transaction

        Returns:
            Optional[str]: Internal attachment key if found, None otherwise
        """
        try:
            self.logger.info(
                "🚀 Retrieving internal key for external attachment ID %s",
                external_attachment_id,
            )

            query = """
            FOR record IN records
                FILTER record.externalRecordId == @external_attachment_id
                RETURN record._key
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query, bind_vars={"external_attachment_id": external_attachment_id}
            )
            result = next(cursor, None)

            if result:
                self.logger.info(
                    "✅ Successfully retrieved internal key for external attachment ID %s",
                    external_attachment_id,
                )
                return result
            else:
                self.logger.warning(
                    "⚠️ No internal key found for external attachment ID %s",
                    external_attachment_id,
                )
                return None

        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve internal key for external attachment ID %s: %s",
                external_attachment_id,
                str(e),
            )
            return None

    async def get_account_type(self, org_id: str) -> str:
        """Get account type for an organization

        Args:
            org_id (str): Organization ID

        Returns:
            str: Account type ('individual' or 'business')
        """
        try:
            query = """
                FOR org IN organizations
                    FILTER org._key == @org_id
                    RETURN org.accountType
            """
            cursor = self.db.aql.execute(query, bind_vars={"org_id": org_id})
            result = next(cursor, None)
            return result
        except Exception as e:
            self.logger.error(f"Error getting account type: {str(e)}")
            return None

    async def update_user_sync_state(
        self,
        user_email: str,
        state: str,
        service_type: str = Connectors.GOOGLE_DRIVE.value,
        connector_id: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        Update user's sync state in USER_APP_RELATION collection for specific service

        Args:
            user_email (str): Email of the user
            state (str): Sync state (NOT_STARTED, RUNNING, PAUSED, COMPLETED)
            service_type (str): Type of service
            connector_id (str): Connector ID (optional)

        Returns:
            Optional[Dict]: Updated relation document if successful, None otherwise
        """
        try:
            self.logger.info(
                "🚀 Updating %s sync state for user %s to %s",
                service_type,
                user_email,
                state,
            )

            user_key = await self.get_entity_id_by_email(user_email)

            # Update edge scoped by connector instance when provided; otherwise by service name
            if connector_id:
                query = f"""
                LET edge = FIRST(
                    FOR rel in {CollectionNames.USER_APP_RELATION.value}
                        FILTER rel._from == CONCAT('users/', @user_key)
                        FILTER rel._to == CONCAT('apps/', @connector_id)
                        UPDATE rel WITH {{ syncState: @state, lastSyncUpdate: @lastSyncUpdate }} IN {CollectionNames.USER_APP_RELATION.value}
                        RETURN NEW
                )
                RETURN edge
                """
                bind_vars = {
                    "user_key": user_key,
                    "connector_id": connector_id,
                    "state": state,
                    "lastSyncUpdate": get_epoch_timestamp_in_ms(),
                }
            else:
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
                bind_vars = {
                    "user_key": user_key,
                    "service_type": service_type,
                    "state": state,
                    "lastSyncUpdate": get_epoch_timestamp_in_ms(),
                }

            cursor = self.db.aql.execute(query, bind_vars=bind_vars)

            result = next(cursor, None)
            if result:
                self.logger.info(
                    "✅ Successfully updated %s sync state for user %s to %s",
                    service_type,
                    user_email,
                    state,
                )
                return result

            self.logger.warning(
                "⚠️ UPDATE:No user-app relation found for email %s and service %s",
                user_email,
                service_type,
            )
            return None

        except Exception as e:
            self.logger.error(
                "❌ Failed to update user %s sync state: %s", service_type, str(e)
            )
            return None

    async def get_user_sync_state(
        self,
        user_email: str,
        service_type: str = Connectors.GOOGLE_DRIVE.value,
        connector_id: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        Get user's sync state from USER_APP_RELATION collection for specific service

        Args:
            user_email (str): Email of the user
            service_type (str): Type of service

        Returns:
            Optional[Dict]: Relation document containing sync state if found, None otherwise
        """
        try:
            self.logger.info(
                "🔍 Getting %s sync state for user %s", service_type, user_email
            )

            user_key = await self.get_entity_id_by_email(user_email)

            if connector_id:
                query = f"""
                RETURN FIRST(
                  FOR rel in {CollectionNames.USER_APP_RELATION.value}
                    FILTER rel._from == CONCAT('users/', @user_key)
                    FILTER rel._to == CONCAT('apps/', @connector_id)
                    RETURN rel
                )
                """
                bind_vars = {
                    "user_key": user_key,
                    "connector_id": connector_id,
                }
            else:
                query = f"""
                LET app = FIRST(FOR a IN {CollectionNames.APPS.value}
                              FILTER LOWER(a.name) == LOWER(@service_type)
                              RETURN {{
                                  _key: a._key,
                                  name: a.name
                              }})

                RETURN FIRST(
                  FOR rel in {CollectionNames.USER_APP_RELATION.value}
                    FILTER rel._from == CONCAT('users/', @user_key)
                    FILTER rel._to == CONCAT('apps/', app._key)
                    RETURN rel
                )
                """
                bind_vars = {
                    "user_key": user_key,
                    "service_type": service_type,
                }

            cursor = self.db.aql.execute(query, bind_vars=bind_vars)

            result = next(cursor, None)
            if result:
                self.logger.info("Result: %s", result)
                self.logger.info(
                    "✅ Found %s sync state for user %s: %s",
                    service_type,
                    user_email,
                    result["syncState"],
                )
                return result

            self.logger.warning(
                "⚠️ GET:No user-app relation found for email %s and service %s",
                user_email,
                service_type,
            )
            return None

        except Exception as e:
            self.logger.error(
                "❌ Failed to get user %s sync state: %s", service_type, str(e)
            )
            return None

    async def update_drive_sync_state(
        self, drive_id: str, state: str, connector_id: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Update drive's sync state in drives collection

        Args:
            drive_id (str): ID of the drive
            state (str): Sync state (NOT_STARTED, RUNNING, PAUSED, COMPLETED)
            connector_id (Optional[str]): Connector ID (optional)

        Returns:
            Optional[Dict]: Updated drive document if successful, None otherwise
        """
        try:
            self.logger.info(
                "🚀 Updating sync state for drive %s to %s", drive_id, state
            )

            update_data = {
                "sync_state": state,
                "last_sync_update": get_epoch_timestamp_in_ms(),
            }

            if connector_id:
                update_data["connectorId"] = connector_id
                query = """
                FOR drive IN drives
                    FILTER drive.id == @drive_id AND drive.connectorId == @connector_id
                    UPDATE drive WITH @update IN drives
                    RETURN NEW
                """
                bind_vars = {"drive_id": drive_id, "connector_id": connector_id, "update": update_data}
            else:
                query = """
                FOR drive IN drives
                    FILTER drive.id == @drive_id
                    UPDATE drive WITH @update IN drives
                    RETURN NEW
                """
                bind_vars = {"drive_id": drive_id, "update": update_data}

            cursor = self.db.aql.execute(query, bind_vars=bind_vars)

            result = next(cursor, None)
            if result:
                self.logger.info(
                    "✅ Successfully updated sync state for drive %s to %s",
                    drive_id,
                    state,
                )
                return result

            self.logger.warning("⚠️ No drive found with ID %s", drive_id)
            return None

        except Exception as e:
            self.logger.error("❌ Failed to update drive sync state: %s", str(e))
            return None

    async def get_drive_sync_state(self, drive_id: str, connector_id: Optional[str] = None) -> Optional[str]:
        """Get sync state for a specific drive

        Args:
            drive_id (str): ID of the drive to check
            connector_id (Optional[str]): Connector ID (optional)

        Returns:
            Optional[str]: Current sync state of the drive ('NOT_STARTED', 'IN_PROGRESS', 'PAUSED', 'COMPLETED', 'FAILED') or None if drive not found
        """
        try:
            self.logger.info("🔍 Getting sync state for drive %s", drive_id)

            if connector_id:
                query = """
                FOR drive IN drives
                    FILTER drive.id == @drive_id AND drive.connectorId == @connector_id
                    RETURN drive.sync_state
                """
                bind_vars = {"drive_id": drive_id, "connector_id": connector_id}
            else:
                query = """
                FOR drive IN drives
                    FILTER drive.id == @drive_id
                    RETURN drive.sync_state
                """
                bind_vars = {"drive_id": drive_id}

            result = list(self.db.aql.execute(query, bind_vars=bind_vars))

            if result:
                self.logger.debug(
                    "✅ Found sync state for drive %s: %s", drive_id, result[0]
                )
                return result[0]

            self.logger.debug(
                "No sync state found for drive %s, assuming NOT_STARTED", drive_id
            )
            return "NOT_STARTED"

        except Exception as e:
            self.logger.error("❌ Error getting drive sync state: %s", str(e))
            return None

    async def check_edge_exists(
        self, from_id: str, to_id: str, collection: str
    ) -> bool:
        """Check if an edge exists between two nodes in a specified collection."""
        try:
            self.logger.info(
                "🔍 Checking if edge exists from %s to %s in collection %s",
                from_id,
                to_id,
                collection,
            )

            query = """
            FOR edge IN @@collection
                FILTER edge._from == @from_id AND edge._to == @to_id
                RETURN edge
            """

            cursor = self.db.aql.execute(
                query,
                bind_vars={
                    "from_id": from_id,
                    "to_id": to_id,
                    "@collection": collection,
                },
            )

            result = next(cursor, None)
            exists = result is not None
            self.logger.info("✅ Edge exists: %s", exists)
            return exists

        except Exception as e:
            self.logger.error("❌ Error checking edge existence: %s", str(e))
            return False

    async def _create_new_record_event_payload(self, record_doc: Dict, file_doc: Dict) -> Dict:
        """
        Creates  NewRecordEvent to Kafka,
        """
        try:
            record_id = record_doc["_key"]
            self.logger.info(f"🚀 Preparing NewRecordEvent for record_id: {record_id}")


            timestamp = get_epoch_timestamp_in_ms()

            # Construct the payload matching the Node.js NewRecordEvent interface
            payload = {
                "orgId": record_doc.get("orgId"),
                "recordId": record_id,
                "recordName": record_doc.get("recordName"),
                "recordType": record_doc.get("recordType"),
                "version": record_doc.get("version", 1),
                "origin": record_doc.get("origin"),
                "extension": file_doc.get("extension", ""),
                "mimeType": file_doc.get("mimeType", ""),
                "createdAtTimestamp": str(record_doc.get("createdAtTimestamp",timestamp)),
                "updatedAtTimestamp": str(record_doc.get("updatedAtTimestamp",timestamp)),
                "sourceCreatedAtTimestamp": str(record_doc.get("sourceCreatedAtTimestamp",record_doc.get("createdAtTimestamp", timestamp))),
            }

            return payload
        except Exception:
            self.logger.error(
                f"❌ Failed to publish NewRecordEvent for record_id: {record_doc.get('_key', 'N/A')}",
                exc_info=True
            )
            return {}

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
                            record_doc, file_doc
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
                    self.logger.error(f"❌ Failed to publish event for record: {str(event_error)}")
                    failed_events += 1

            self.logger.info(f"📊 Event publishing summary: {successful_events} successful, {failed_events} failed")

        except Exception as e:
            self.logger.error(f"❌ Critical error in event publishing for KB {kb_id}: {str(e)}", exc_info=True)


    async def _create_update_record_event_payload(
        self,
        record: Dict,
        file_record: Optional[Dict] = None,
        content_changed: bool = True
    ) -> Dict:
        """Create update record event payload matching Node.js format"""
        try:
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
                "updatedAtTimestamp": str(record.get("updatedAtTimestamp", get_epoch_timestamp_in_ms())),
                "sourceLastModifiedTimestamp": str(record.get("sourceLastModifiedTimestamp", record.get("updatedAtTimestamp", get_epoch_timestamp_in_ms()))),
                "virtualRecordId": record.get("virtualRecordId"),
                "summaryDocumentId": record.get("summaryDocumentId"),
                "contentChanged": content_changed,
            }
        except Exception as e:
            self.logger.error(f"❌ Failed to create update record event payload: {str(e)}")
            return {}

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
                "recordId": record.get("_key"),
                "version": record.get("version", 1),
                "extension": extension,
                "mimeType": mime_type,
                "summaryDocumentId": record.get("summaryDocumentId"),
                "virtualRecordId": record.get("virtualRecordId"),
            }
        except Exception as e:
            self.logger.error(f"❌ Failed to create deleted record event payload: {str(e)}")
            return {}

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
                "_key": record_id,
                "indexingStatus": IndexingStatus.QUEUED.value,
            }

            await self.batch_upsert_nodes([doc], CollectionNames.RECORDS.value)
            self.logger.debug(f"✅ Reset record {record_id} status from {current_status} to QUEUED")
        except Exception as e:
            # Log but don't fail the main operation if status update fails
            self.logger.error(f"❌ Failed to reset record {record_id} to QUEUED: {str(e)}")

    def _validation_error(self, code: int, reason: str) -> Dict:
        """Helper to create validation error response"""
        return {"valid": False, "success": False, "code": code, "reason": reason}

    def _analyze_upload_structure(self, files: List[Dict], validation_result: Dict) -> Dict:
        """
        Updated structure analysis - creates folder hierarchy map based on file paths
        but uses names for validation
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
                    "folder_name": folder_parts[-1],  # Only the immediate parent folder name
                    "folder_hierarchy_path": current_path,  # Full hierarchy path for creation
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

        # Add parent folder context to the analysis
        parent_folder_id = None
        if validation_result["upload_target"] == "folder":
            parent_folder_id = validation_result["parent_folder"].get("_key") if validation_result.get("parent_folder") else None

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

    async def _ensure_folders_exist(
        self,
        kb_id: str,
        org_id: str,
        folder_analysis: Dict,
        validation_result: Dict,
        transaction,
        timestamp: int
    ) -> Dict[str, str]:
        """
        Updated folder creation - uses name-based validation instead of path
        """
        folder_map = {}  # hierarchy_path -> folder_id
        upload_parent_folder_id = None
        if validation_result["upload_target"] == "folder":
            upload_parent_folder_id = validation_result["parent_folder"].get("_key") if validation_result.get("parent_folder") else None

        for hierarchy_path in folder_analysis["sorted_folder_paths"]:
            folder_info = folder_analysis["folder_hierarchy"][hierarchy_path]
            folder_name = folder_info["name"]
            parent_hierarchy_path = folder_info["parent_path"]

            # Determine parent folder ID
            parent_folder_id = None
            if parent_hierarchy_path:
                # Has a parent folder in the hierarchy
                parent_folder_id = folder_map.get(parent_hierarchy_path)
                if parent_folder_id is None:
                    self.logger.error(f"❌ Parent folder not found in map for path: {parent_hierarchy_path}")
                    raise Exception(f"Parent folder creation failed for path: {parent_hierarchy_path}")
            elif upload_parent_folder_id:
                # First level folder under the upload target folder
                parent_folder_id = upload_parent_folder_id
            # else: parent_folder_id remains None (KB root)

            # Check if folder already exists using name-based lookup
            existing_folder = await self.find_folder_by_name_in_parent(
                kb_id=kb_id,
                folder_name=folder_name,
                parent_folder_id=parent_folder_id
            )

            if existing_folder:
                folder_map[hierarchy_path] = existing_folder["_key"]
                self.logger.debug(f"✅ Folder exists: {folder_name} in parent {parent_folder_id or 'KB root'}")
            else:
                # Create new folder
                folder = await self.create_folder(
                    kb_id=kb_id,
                    org_id=org_id,
                    folder_name=folder_name,
                    parent_folder_id=parent_folder_id
                )
                folder_id = folder['id']
                if folder_id:
                    folder_map[hierarchy_path] = folder_id
                    self.logger.info(f"✅ Created folder: {folder_name} -> {folder_id} in parent {parent_folder_id or 'KB root'}")
                else:
                    raise Exception(f"Failed to create folder: {folder_name}")

        return folder_map

    async def _create_folder(
        self,
        kb_id: str,
        org_id: str,
        folder_path: str,
        folder_map: Dict[str, str],
        validation_result: Dict,
        transaction,
        timestamp: int
    ) -> str:
        """Unified folder creation logic"""
        folder_id = str(uuid.uuid4())
        folder_name = folder_path.split("/")[-1]

        # Determine parent folder ID
        parent_folder_id = None
        if "/" in folder_path:
            parent_path = "/".join(folder_path.split("/")[:-1])
            parent_folder_id = folder_map.get(parent_path)

        # If no parent in map, check if we're uploading to a specific folder
        if not parent_folder_id and validation_result["upload_target"] == "folder":
            # For first-level subfolders of the upload target
            upload_parent_path = validation_result["parent_path"]
            if folder_path.startswith(f"{upload_parent_path}/") and folder_path.count("/") == upload_parent_path.count("/") + 1:
                parent_folder_id = validation_result["parent_folder"]["_key"]

        # Create folder document
        folder_data = {
            "_key": folder_id,
            "orgId": org_id,
            "name": folder_name,
            "isFile": False,
            "extension": None,
        }

        # Create folder
        await self.batch_upsert_nodes([folder_data], CollectionNames.FILES.value, transaction)

        # Create relationships
        edges_to_create = []

        # KB relationship (always needed)
        edges_to_create.append({
            "_from": f"{CollectionNames.FILES.value}/{folder_id}",
            "_to": f"{CollectionNames.RECORD_GROUPS.value}/{kb_id}",
            "entityType": Connectors.KNOWLEDGE_BASE.value,
            "createdAtTimestamp": timestamp,
            "updatedAtTimestamp": timestamp,
        })

        # Parent-child relationship (if has parent)
        if parent_folder_id:
            edges_to_create.append({
                "_from": f"{CollectionNames.FILES.value}/{parent_folder_id}",
                "_to": f"{CollectionNames.FILES.value}/{folder_id}",
                "relationshipType": "PARENT_CHILD",
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            })
        else:
            # record relations edge between folder and kb
            edges_to_create.append({
                    "_from": f"{CollectionNames.RECORD_GROUPS.value}/{kb_id}",
                    "_to": f"{CollectionNames.FILES.value}/{folder_id}",
                    "relationshipType": "PARENT_CHILD",
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                })

        # Create edges
        for edge in edges_to_create:
            collection = (
                CollectionNames.BELONGS_TO.value
                if edge.get("entityType") == Connectors.KNOWLEDGE_BASE.value
                else CollectionNames.RECORD_RELATIONS.value
            )
            await self.batch_create_edges([edge], collection, transaction)

        return folder_id

    def _populate_file_destinations(self, folder_analysis: Dict, folder_map: Dict[str, str]) -> None:
        """
        Update file destinations with resolved folder IDs using hierarchy paths
        """
        for index, destination in folder_analysis["file_destinations"].items():
            if destination["type"] == "folder":
                hierarchy_path = destination["folder_hierarchy_path"]
                if hierarchy_path in folder_map:
                    destination["folder_id"] = folder_map[hierarchy_path]
                else:
                    self.logger.error(f"❌ Folder ID not found in map for hierarchy path: {hierarchy_path}")
                    # Don't set folder_id, which will cause the file to be added to failed_files

    # ========== UNIFIED RECORD CREATION ==========

    async def _create_records(
        self,
        kb_id: str,
        files: List[Dict],
        folder_analysis: Dict,
        transaction,
        timestamp: int
    ) -> Dict:
        total_created = 0
        failed_files = []

        # Group files by destination
        root_files = []
        folder_files = {}  # folder_id -> files
        created_files_data = []
        for index, file_data in enumerate(files):
            destination = folder_analysis["file_destinations"][index]

            if destination["type"] == "root":
                # File goes to root (KB root or parent folder)
                parent_folder_id = folder_analysis.get("parent_folder_id")  # Use the validated parent folder ID
                root_files.append((file_data, parent_folder_id))
            else:
                # File goes to subfolder
                folder_id = destination["folder_id"]
                if folder_id:
                    if folder_id not in folder_files:
                        folder_files[folder_id] = []
                    folder_files[folder_id].append(file_data)
                else:
                    self.logger.error(f"❌ No folder ID found for file: {file_data['filePath']}")
                    failed_files.append(file_data["filePath"])

        # Create root files
        if root_files:
            try:
                # Separate by target (KB root vs parent folder)
                kb_root_files = [f for f, folder_id in root_files if folder_id is None]
                parent_folder_files = {}

                for file_data, folder_id in root_files:
                    if folder_id is not None:
                        if folder_id not in parent_folder_files:
                            parent_folder_files[folder_id] = []
                        parent_folder_files[folder_id].append(file_data)

                # Create KB root files
                if kb_root_files:
                    successful_files = await self._create_files_in_kb_root(
                        kb_id=kb_id,
                        files=kb_root_files,
                        transaction=transaction,
                        timestamp=timestamp
                    )
                    created_files_data.extend(successful_files)
                    total_created += len(successful_files)
                    self.logger.info(f"✅ Created {len(successful_files)} files in KB root")

                # Create parent folder files
                for folder_id, folder_file_list in parent_folder_files.items():
                    successful_files = await self._create_files_in_folder(
                        kb_id=kb_id,
                        folder_id=folder_id,
                        files=folder_file_list,
                        transaction=transaction,
                        timestamp=timestamp
                    )
                    created_files_data.extend(successful_files)
                    total_created += len(successful_files)
                    self.logger.info(f"✅ Created {len(successful_files)} files in parent folder")

            except Exception as e:
                self.logger.error(f"❌ Failed to create root files: {str(e)}")
                failed_files.extend([f[0]["filePath"] for f in root_files])

        # Create subfolder files
        for folder_id, folder_file_list in folder_files.items():
            try:
                successful_files = await self._create_files_in_folder(
                    kb_id=kb_id,
                    folder_id=folder_id,
                    files=folder_file_list,
                    transaction=transaction,
                    timestamp=timestamp
                )
                created_files_data.extend(successful_files)
                total_created += len(successful_files)
                self.logger.info(f"✅ Created {len(successful_files)} files in subfolder {folder_id}")

            except Exception as e:
                self.logger.error(f"❌ Failed to create files in subfolder {folder_id}: {str(e)}")
                failed_files.extend([f["filePath"] for f in folder_file_list])

        return {"total_created": total_created, "failed_files": failed_files,"created_files_data": created_files_data}

    # ========== SHARED RECORD CREATION HELPERS ==========

    async def _create_files_in_kb_root(self, kb_id: str, files: List[Dict], transaction, timestamp: int) -> int:
        """Create files directly in KB root"""
        return await self._create_files_batch(
            kb_id=kb_id,
            files=files,
            parent_folder_id=None,  # No parent = KB root
            transaction=transaction,
            timestamp=timestamp
        )

    async def _create_files_in_folder(self, kb_id: str, folder_id: str, files: List[Dict], transaction, timestamp: int) -> int:
        """Create files in a specific folder"""
        return await self._create_files_batch(
            kb_id=kb_id,
            files=files,
            parent_folder_id=folder_id,
            transaction=transaction,
            timestamp=timestamp
        )

    async def _create_files_batch(
        self,
        kb_id: str,
        files: List[Dict],
        parent_folder_id: Optional[str],
        transaction,
        timestamp: int
    ) -> List[Dict]:
        """
        Updated batch file creation with proper conflict handling
        Skips files with name conflicts instead of creating duplicates
        """
        if not files:
            return []

        valid_files = []
        skipped_files = []

        # Step 1: Filter out files with name conflicts
        for file_data in files:
            _normalized = self._normalize_name(file_data["fileRecord"].get("name"))
            file_name = _normalized if _normalized is not None else ""
            mime_type = file_data["fileRecord"].get("mimeType")

            # Check for name conflicts using the updated validation
            conflict_result = await self._check_name_conflict_in_parent(
                kb_id=kb_id,
                parent_folder_id=parent_folder_id,
                item_name=file_name,
                mime_type=mime_type,
                transaction=transaction
            )

            if conflict_result["has_conflict"]:
                conflicts = conflict_result["conflicts"]
                conflict_names = [c["name"] for c in conflicts]
                self.logger.warning(f"⚠️ Skipping file due to name conflict: '{file_name}' conflicts with {conflict_names}")
                skipped_files.append({
                    "file_name": file_name,
                    "reason": f"Name conflict with existing items: {conflict_names}",
                    "conflicts": conflicts
                })
            else:
                # persist normalized name back into payload for consistency
                file_data["fileRecord"]["name"] = file_name
                valid_files.append(file_data)

        # Log skipping summary
        if skipped_files:
            self.logger.info(f"📋 Skipped {len(skipped_files)} files due to name conflicts, processing {len(valid_files)} files")

        # If no valid files, return early
        if not valid_files:
            self.logger.warning("⚠️ No valid files to create after conflict filtering")
            return []

        # Step 2: Extract and enrich records and file records from valid files only
        records = []
        file_records = [f["fileRecord"] for f in valid_files]

        for file_data in valid_files:
            record = file_data["record"].copy()  # Create a copy to avoid modifying original

            # Enrich record with missing KB-specific fields
            # Determine externalParentId: null for immediate children of KB, parent_folder_id for nested
            external_parent_id = parent_folder_id if parent_folder_id else None

            # Add missing fields (using setdefault to only add if not already present)
            record.setdefault("externalGroupId", kb_id)
            record.setdefault("externalParentId", external_parent_id)
            record.setdefault("externalRootGroupId", kb_id)
            record.setdefault("connectorName", Connectors.KNOWLEDGE_BASE.value)
            record.setdefault("lastSyncTimestamp", timestamp)
            record.setdefault("isVLMOcrProcessed", False)
            record.setdefault("extractionStatus", "NOT_STARTED")  # Files need extraction, unlike folders
            record.setdefault("isLatestVersion", True)
            record.setdefault("isDirty", False)

            records.append(record)

        # Step 3: Create records and file records
        await self.batch_upsert_nodes(records, CollectionNames.RECORDS.value, transaction)
        await self.batch_upsert_nodes(file_records, CollectionNames.FILES.value, transaction)

        # Step 4: Create relationships for valid files only
        edges_to_create = []

        for file_data in valid_files:
            record_id = file_data["record"]["_key"]
            file_id = file_data["fileRecord"]["_key"]

            # Parent -> Record relationship (if has parent)
            # Folders are now represented by RECORDS documents, so edge is from records/{parent_folder_id}
            if parent_folder_id:
                edges_to_create.append({
                    "_from": f"records/{parent_folder_id}",
                    "_to": f"records/{record_id}",
                    "relationshipType": "PARENT_CHILD",
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                })

            # Record -> File relationship
            edges_to_create.append({
                "_from": f"records/{record_id}",
                "_to": f"files/{file_id}",
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            })

            # Record -> KB relationship (belongs to KB)
            edges_to_create.append({
                "_from": f"records/{record_id}",
                "_to": f"recordGroups/{kb_id}",
                "entityType": Connectors.KNOWLEDGE_BASE.value,
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            })

            # Record -> KB inheritPermission edge
            # KB records inherit permissions from KB by default
            edges_to_create.append({
                "_from": f"records/{record_id}",
                "_to": f"recordGroups/{kb_id}",
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            })

        # Step 5: Batch create edges by type
        parent_child_edges = [e for e in edges_to_create if e.get("relationshipType") == "PARENT_CHILD"]
        is_of_type_edges = [e for e in edges_to_create if e["_to"].startswith("files/") and not e.get("relationshipType")]
        # belongsTo edges have entityType set (e.g., Connectors.KNOWLEDGE_BASE.value)
        belongs_to_kb_edges = [e for e in edges_to_create if e["_to"].startswith("recordGroups/") and e.get("entityType") == Connectors.KNOWLEDGE_BASE.value]
        # inheritPermission edges point to recordGroups but don't have entityType
        inherit_permission_edges = [e for e in edges_to_create if e["_to"].startswith("recordGroups/") and not e.get("entityType")]

        if parent_child_edges:
            await self.batch_create_edges(parent_child_edges, CollectionNames.RECORD_RELATIONS.value, transaction)
        if is_of_type_edges:
            await self.batch_create_edges(is_of_type_edges, CollectionNames.IS_OF_TYPE.value, transaction)
        if belongs_to_kb_edges:
            await self.batch_create_edges(belongs_to_kb_edges, CollectionNames.BELONGS_TO.value, transaction)
        if inherit_permission_edges:
            await self.batch_create_edges(inherit_permission_edges, CollectionNames.INHERIT_PERMISSIONS.value, transaction)

        # Step 6: Store skipped files for reporting (optional)
        if hasattr(self, '_current_upload_skipped_files'):
            self._current_upload_skipped_files.extend(skipped_files)

        self.logger.info(f"✅ Successfully created {len(valid_files)} files, skipped {len(skipped_files)} due to conflicts")

        return valid_files

    # ========== HELPER METHODS ==========

    async def _validate_folder_creation(self, kb_id: str, user_id: str) -> Dict:
        """Shared validation logic for folder creation"""
        try:
            # Get user
            user = await self.get_user_by_user_id(user_id=user_id)
            if not user:
                return {"valid": False, "success": False, "code": 404, "reason": f"User not found: {user_id}"}

            user_key = user.get('_key')

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


    async def _execute_upload_transaction(
        self,
        kb_id: str,
        user_id: str,
        org_id: str,
        files: List[Dict],
        folder_analysis: Dict,
        validation_result: Dict
    ) -> Dict:
        """Unified transaction execution for all upload scenarios"""
        try:
            # Start transaction
            transaction = self.db.begin_transaction(
                write=[
                    CollectionNames.FILES.value,
                    CollectionNames.RECORDS.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.BELONGS_TO.value,
                    CollectionNames.INHERIT_PERMISSIONS.value,
                ]
            )

            try:
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
                    files=files,
                    folder_analysis=folder_analysis,
                    transaction=transaction,
                    timestamp=timestamp
                )

                if creation_result["total_created"] > 0 or len(folder_map) > 0:
                    # Step 4: Commit transaction BEFORE event publishing
                    await asyncio.to_thread(lambda: transaction.commit_transaction())
                    self.logger.info("✅ Upload transaction committed successfully")
                    # Step 5: Publish events AFTER successful commit
                    try:
                        await self._publish_upload_events(kb_id, {
                            "created_files_data": creation_result["created_files_data"],
                            "total_created": creation_result["total_created"]
                        })
                        self.logger.info(f"✅ Published events for {creation_result['total_created']} records")
                    except Exception as event_error:
                        self.logger.error(f"❌ Event publishing failed (records still created): {str(event_error)}")
                        # Don't fail the main operation - records were successfully created

                    return {
                        "success": True,
                        "total_created": creation_result["total_created"],
                        "folders_created": len(folder_map),
                        "created_folders": [
                            {"id": folder_id}
                            for folder_id in folder_map.values()
                        ],
                        "failed_files": creation_result["failed_files"],
                        "created_files_data": creation_result["created_files_data"]
                    }
                else:
                    # Nothing was created - abort transaction
                    await asyncio.to_thread(lambda: transaction.abort_transaction())
                    self.logger.info("🔄 Transaction aborted - no items to create")
                    return {
                        "success": True,
                        "total_created": 0,
                        "folders_created": 0,
                        "created_folders": [],
                        "failed_files": creation_result["failed_files"],
                        "created_files_data": []
                    }

            except Exception as e:
                if transaction:
                    try:
                        await asyncio.to_thread(lambda: transaction.abort_transaction())
                        self.logger.info("🔄 Transaction aborted due to error")
                    except Exception as abort_error:
                        self.logger.error(f"❌ Failed to abort transaction: {str(abort_error)}")

                self.logger.error(f"❌ Upload transaction failed: {str(e)}")
                return {"success": False, "reason": f"Transaction failed: {str(e)}", "code": 500}


        except Exception as e:
            return {"success": False, "reason": f"Transaction failed: {str(e)}", "code": 500}



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
                return self._validation_error(404, f"User not found: {user_id}")

            user_key = user.get('_key')

            # Check KB permissions
            user_role = await self.get_user_kb_permission(kb_id, user_key)
            if user_role not in ["OWNER", "WRITER"]:
                return self._validation_error(403, f"Insufficient permissions. Role: {user_role}")

            # Validate folder if specified
            parent_folder = None
            parent_path = "/"  # Default for KB root

            if parent_folder_id:
                # Get and validate folder in a single query (optimized)
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
                "upload_target": "folder" if parent_folder_id else "kb_root"
            }

        except Exception as e:
            return self._validation_error(500, f"Validation failed: {str(e)}")

    async def create_knowledge_base(
        self,
        kb_data:Dict,
        permission_edge:Dict,
        belongs_to_edge:Dict,
        transaction:Optional[TransactionDatabase]=None
    )-> Dict:
        """Create knowledge base with permissions"""
        try:
            kb_name = kb_data.get('groupName', 'Unknown')
            self.logger.info(f"🚀 Creating knowledge base: '{kb_name}' in ArangoDB")

            # KB record group creation
            await self.batch_upsert_nodes(
                [kb_data], CollectionNames.RECORD_GROUPS.value,transaction=transaction
            )

            # user KB permission edge
            await self.batch_create_edges(
                [permission_edge],
                CollectionNames.PERMISSION.value,transaction=transaction
            )
            # belongs to edge between kb and app
            await self.batch_create_edges(
                [belongs_to_edge],
                CollectionNames.BELONGS_TO.value,transaction=transaction
            )

            self.logger.info(f"✅ Knowledge base created successfully: {kb_data['_key']}")
            return {
                "id": kb_data["_key"],
                "name": kb_data["groupName"],
                "success": True
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to create knowledge base: {str(e)}")
            raise

    async def get_user_kb_permission(
        self,
        kb_id: str,
        user_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[str]:
        """
        Get user's permission on a KB.
        Optimized: Single query checks both direct and team permissions.
        First checks for direct USER permission, then checks via team membership.
        For team-based access, returns the highest role from all common teams.
        Role hierarchy: OWNER > WRITER > READER > COMMENTER

        Recommended indexes:
        - permission collection: [ "_from", "_to", "type" ] (persistent index)
        - permission collection: [ "_to", "type" ] (persistent index)
        """
        try:
            self.logger.info(f"🔍 Checking permissions for user {user_id} on KB {kb_id}")
            db = transaction if transaction else self.db

            # Optimized: Single query that checks both direct and team permissions
            query = """
            LET user_from = CONCAT('users/', @user_id)
            LET kb_to = CONCAT('recordGroups/', @kb_id)
            LET role_priority = {
                "OWNER": 4,
                "WRITER": 3,
                "READER": 2,
                "COMMENTER": 1
            }

            // Check for direct user permission first (fastest path)
            LET direct_perm = FIRST(
                FOR perm IN @@permissions_collection
                    FILTER perm._from == user_from
                    FILTER perm._to == kb_to
                    FILTER perm.type == "USER"
                    RETURN perm.role
            )

            // If direct permission exists, return it immediately
            FILTER direct_perm != null
            RETURN direct_perm
            """

            cursor = db.aql.execute(
                query,
                bind_vars={
                    "kb_id": kb_id,
                    "user_id": user_id,
                    "@permissions_collection": CollectionNames.PERMISSION.value,
                },
            )

            direct_role = next(cursor, None)
            if direct_role:
                self.logger.info(f"✅ Found direct permission: user {user_id} has role '{direct_role}' on KB {kb_id}")
                return direct_role

            # If no direct permission, check via teams (optimized with single query)
            team_query = """
            LET user_from = CONCAT('users/', @user_id)
            LET kb_to = CONCAT('recordGroups/', @kb_id)
            LET role_priority = {
                "OWNER": 4,
                "WRITER": 3,
                "READER": 2,
                "COMMENTER": 1
            }

            // Get KB's teams and user's role in those teams in one optimized query
            FOR kb_team_perm IN @@permissions_collection
                FILTER kb_team_perm._to == kb_to
                FILTER kb_team_perm.type == "TEAM"
                LET team_id = SPLIT(kb_team_perm._from, '/')[1]

                // Check if user is a member of this team (using index on _from, _to, type)
                FOR user_team_perm IN @@permissions_collection
                    FILTER user_team_perm._from == user_from
                    FILTER user_team_perm._to == CONCAT('teams/', team_id)
                    FILTER user_team_perm.type == "USER"

                    RETURN {
                        role: user_team_perm.role,
                        priority: role_priority[user_team_perm.role] || 0
                    }
            """

            team_cursor = db.aql.execute(
                team_query,
                bind_vars={
                    "kb_id": kb_id,
                    "user_id": user_id,
                    "@permissions_collection": CollectionNames.PERMISSION.value,
                },
            )

            # Get all team roles and find the highest priority
            team_roles = list(team_cursor)
            if team_roles:
                # Sort by priority and return the highest role
                highest_role_data = max(team_roles, key=lambda x: x.get('priority', 0))
                team_role = highest_role_data.get('role')
                if team_role:
                    self.logger.info(f"✅ Found team-based permission: user {user_id} has role '{team_role}' on KB {kb_id} via teams")
                    return team_role

            self.logger.warning(f"⚠️ No permission found for user {user_id} on KB {kb_id} (neither direct nor via teams)")
            return None

        except Exception as e:
            self.logger.error(f"❌ Failed to validate knowledge base permission for user {user_id}: {str(e)}")
            raise

    async def get_knowledge_base(
        self,
        kb_id: str,
        user_id: str,
        transaction: Optional[TransactionDatabase] = None,
    ) -> Optional[Dict]:
        """Get knowledge base with user permissions"""
        try:
            db = transaction if transaction else self.db

            #  Get the KB and folders
            # First check user permissions (includes team-based access)
            user_role = await self.get_user_kb_permission(kb_id, user_id, transaction=db)

            query = """
            FOR kb IN @@recordGroups_collection
                FILTER kb._key == @kb_id
                LET user_role = @user_role

                // Get folders
                // Folders are now represented by RECORDS documents
                LET folders = (
                    FOR edge IN @@kb_to_folder_edges
                        FILTER edge._to == kb._id
                        // Folders are now RECORDS documents, so edge._from should be records/{folder_id}
                        FILTER STARTS_WITH(edge._from, 'records/')
                        LET folder_record = DOCUMENT(edge._from)
                        FILTER folder_record != null
                        // Verify it's a folder by checking associated FILES document
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
            cursor = db.aql.execute(query, bind_vars={
                "kb_id": kb_id,
                "user_role": user_role,  # Pass the role from get_user_kb_permission
                "@recordGroups_collection": CollectionNames.RECORD_GROUPS.value,
                "@kb_to_folder_edges": CollectionNames.BELONGS_TO.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
            })
            result = next(cursor, None)
            if result:
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

    async def get_knowledge_base_by_id(
        self,
        kb_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[Dict]:
        """Get knowledge base by ID"""
        try:
            db = transaction if transaction else self.db

            query = """
            FOR kb IN @@recordGroups_collection
                FILTER kb._key == @kb_id
                FILTER kb.groupType == @kb_type
                FILTER kb.connectorName == @kb_connector
                RETURN kb
            """

            cursor = db.aql.execute(query, bind_vars={
                "kb_id": kb_id,
                "@recordGroups_collection": CollectionNames.RECORD_GROUPS.value,
                "kb_type": Connectors.KNOWLEDGE_BASE.value,
                "kb_connector": Connectors.KNOWLEDGE_BASE.value,
            })

            return next(cursor, None)

        except Exception as e:
            self.logger.error(f"❌ Failed to get knowledge base by ID: {str(e)}")
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
        transaction: Optional[TransactionDatabase] = None,
    ) -> Tuple[List[Dict], int, Dict]:
        """
        List knowledge bases with pagination, search, and filtering.
        Includes both direct user permissions and team-based permissions.
        For team-based access, returns the highest role from all common teams.
        """
        try:
            db = transaction if transaction else self.db

            # Build filter conditions
            filter_conditions = []

            # Search filter
            if search:
                filter_conditions.append("LIKE(LOWER(kb.groupName), LOWER(@search_term))")

            # Permission filter (will be applied after role resolution)
            permission_filter = ""
            if permissions:
                permission_filter = "FILTER final_role IN @permissions"

            # Build WHERE clause for KB filtering
            additional_filters = ""
            if filter_conditions:
                additional_filters = "AND " + " AND ".join(filter_conditions)

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
            role_priority_map = {
                "OWNER": 4,
                "WRITER": 3,
                "READER": 2,
                "COMMENTER": 1
            }

            # Optimized main query: Reduced DOCUMENT() calls and improved folder fetching
            # Recommended indexes:
            # - permission collection: [ "_from", "type" ] (persistent index)
            # - permission collection: [ "_to", "type" ] (persistent index)
            # - permission collection: [ "_from", "_to", "type" ] (persistent index)
            # - recordGroups collection: [ "orgId", "groupType", "connectorName" ] (persistent index)
            # - belongs_to edges: [ "_to" ] (persistent index)
            main_query = f"""
            // Direct user permissions - optimized with early filtering
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

            // Team-based permissions - optimized with better join strategy
            LET team_perms = (
                // First, get all teams the user is a member of (one-time lookup)
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

                // Now find KBs that have permissions for these teams
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

            // Combine and deduplicate: for each KB, get the highest role
            LET all_perms = UNION(direct_perms, team_perms)

            LET kb_roles = (
                FOR perm IN all_perms
                    COLLECT kb_id = perm.kb_id, kb_doc = perm.kb_doc INTO roles = perm
                    // Get the highest priority role (prefer direct if same priority)
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

            // Batch fetch all folders for all KBs at once (more efficient than per-KB)
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

            // Build final result with folders grouped by KB
            FOR kb_role IN kb_roles
                LET kb = kb_role.kb_doc
                LET folders = all_folders[* FILTER CURRENT.kb_id == kb._id].folder
                SORT {sort_field} {sort_direction}
                LIMIT @skip, @limit
                RETURN {{
                    id: kb._key,
                    name: kb.groupName,
                    connectorId: kb.connectorId,
                    createdAtTimestamp: kb.createdAtTimestamp,
                    updatedAtTimestamp: kb.updatedAtTimestamp,
                    createdBy: kb.createdBy,
                    userRole: kb_role.userRole,
                    folders: folders
                }}
            """

            # Optimized count query - same optimizations as main query
            count_query = f"""
            // Direct user permissions
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

            // Team-based permissions - optimized
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

            # Optimized filters query - same optimizations as main query
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

            # Bind variables for main query
            main_bind_vars = {
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

            # Add search term if provided
            if search:
                main_bind_vars["search_term"] = f"%{search}%"

            # Add permissions filter if provided
            if permissions:
                main_bind_vars["permissions"] = permissions

            # Bind variables for count query
            count_bind_vars = {
                "count_user_from": f"users/{user_id}",
                "count_org_id": org_id,
                "count_kb_type": Connectors.KNOWLEDGE_BASE.value,
                "count_kb_connector": Connectors.KNOWLEDGE_BASE.value,
                "count_role_priority": role_priority_map,
                "@count_permissions_collection": CollectionNames.PERMISSION.value,
            }

            # Add search term if provided for count query
            if search:
                count_bind_vars["count_search_term"] = f"%{search}%"

            # Add permissions filter if provided for count query
            if permissions:
                count_bind_vars["count_permissions"] = permissions

            # Bind variables for filters query
            filters_bind_vars = {
                "filters_user_from": f"users/{user_id}",
                "filters_org_id": org_id,
                "filters_kb_type": Connectors.KNOWLEDGE_BASE.value,
                "filters_kb_connector": Connectors.KNOWLEDGE_BASE.value,
                "filters_role_priority": role_priority_map,
                "@filters_permissions_collection": CollectionNames.PERMISSION.value,
            }

            # Execute queries
            kbs_cursor = db.aql.execute(main_query, bind_vars=main_bind_vars)
            count_cursor = db.aql.execute(count_query, bind_vars=count_bind_vars)
            filters_cursor = db.aql.execute(filters_query, bind_vars=filters_bind_vars)

            # Get results
            kbs = list(kbs_cursor)
            total_count = next(count_cursor, 0)
            filter_data = list(filters_cursor)

            # Build available filters
            available_permissions = list(set(item["permission"] for item in filter_data if item.get("permission")))

            available_filters = {
                "permissions": available_permissions,
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
        transaction: Optional[TransactionDatabase] = None,
    ) -> bool:
        """Update knowledge base"""
        try:
            self.logger.info(f"🚀 Updating knowledge base {kb_id}")

            db = transaction if transaction else self.db

            query = """
            FOR kb IN @@kb_collection
                FILTER kb._key == @kb_id
                UPDATE kb WITH @updates IN @@kb_collection
                RETURN NEW
            """

            cursor = db.aql.execute(query, bind_vars={
                "kb_id": kb_id,
                "updates": updates,
                "@kb_collection": CollectionNames.RECORD_GROUPS.value,
            })

            result = next(cursor, None)

            if result:
                self.logger.info("✅ Knowledge base updated successfully")
                return True
            else:
                self.logger.warning("⚠️ Knowledge base not found")
                return False

        except Exception as e:
            self.logger.error(f"❌ Failed to update knowledge base: {str(e)}")
            raise

    async def get_folder_record_by_id(self, folder_id: str, transaction: Optional[TransactionDatabase] = None) -> Optional[Dict]:
        """
        Get folder by ID. Folders are represented by RECORDS documents with associated FILES documents.
        Returns combined folder data from both collections.
        """
        try:
            db = transaction if transaction else self.db
            query = """
            // Get folder RECORDS document
            LET folder_record = DOCUMENT(@@records_collection, @folder_id)
            FILTER folder_record != null

            // Get associated FILES document via IS_OF_TYPE edge
            LET folder_file = FIRST(
                FOR isEdge IN @@is_of_type
                    FILTER isEdge._from == folder_record._id
                    LET f = DOCUMENT(isEdge._to)
                    FILTER f != null AND f.isFile == false
                    RETURN f
            )

            FILTER folder_file != null

            // Return combined folder data
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
            cursor = db.aql.execute(query, bind_vars={
                "folder_id": folder_id,
                "@records_collection": CollectionNames.RECORDS.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
            })
            return next(cursor, None)
        except Exception as e:
            self.logger.error(f"❌ Failed to fetch folder record {folder_id}: {str(e)}")
            return None

    async def find_folder_by_name_in_parent(
        self,
        kb_id: str,
        folder_name: str,
        parent_folder_id: Optional[str] = None,
        exclude_folder_id: Optional[str] = None,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[Dict]:
        """
        Find a folder by name within a specific parent (KB root or folder)

        Args:
            kb_id: Knowledge base ID
            folder_name: Name of the folder to find
            parent_folder_id: Parent folder ID (None for KB root)
            exclude_folder_id: Optional folder ID to exclude from search (useful for rename operations)
            transaction: Optional transaction
        """
        try:
            db = transaction if transaction else self.db

            # Prepare normalized lowercase variants for robust comparison (handles Unicode diacritics)
            name_variants = self._normalized_name_variants_lower(folder_name)

            # Determine the parent reference based on whether we're in a folder or KB root
            # Folders are now represented by RECORDS documents, so use records/{parent_folder_id}
            # For KB root, use recordGroups/{kb_id}
            parent_from = f"records/{parent_folder_id}" if parent_folder_id else f"recordGroups/{kb_id}"

            if parent_folder_id is None:
                query = """
                FOR edge IN @@belongs_to
                    FILTER edge._to == CONCAT('recordGroups/', @kb_id)
                    FILTER edge.entityType == @entity_type
                    LET folder_record = DOCUMENT(edge._from)
                    FILTER folder_record != null
                    FILTER folder_record.isDeleted != true
                    // Check if this folder is a child of any other folder
                    LET isChild = LENGTH(
                        FOR relEdge IN @@record_relations
                            FILTER relEdge._to == folder_record._id
                            FILTER relEdge.relationshipType == "PARENT_CHILD"
                            RETURN 1
                    ) > 0
                    // Only include if NOT a child (immediate child)
                    FILTER isChild == false
                    // Verify it's a folder by checking associated FILES document via IS_OF_TYPE edge
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
                cursor = db.aql.execute(query, bind_vars={
                    "name_variants": name_variants,
                    "kb_id": kb_id,
                    "@belongs_to": CollectionNames.BELONGS_TO.value,
                    "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                    "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                    "entity_type": Connectors.KNOWLEDGE_BASE.value,
                })

                result = next(cursor, None)
                if result:
                    self.logger.debug(f"✅ Found folder '{folder_name}' in parent")
                else:
                    self.logger.debug(f"📝 Folder '{folder_name}' not found in parent (will be created if needed)")
                return result

            else:
                query = """
                FOR edge IN @@record_relations
                    FILTER edge._from == @parent_from
                    FILTER edge.relationshipType == "PARENT_CHILD"
                    LET folder_record = DOCUMENT(edge._to)
                    FILTER folder_record != null
                    // Verify it's a folder by checking associated FILES document via IS_OF_TYPE edge
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

                cursor = db.aql.execute(query, bind_vars={
                    "parent_from": parent_from,
                    "name_variants": name_variants,
                    "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                    "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                })

                result = next(cursor, None)

                if result:
                    self.logger.debug(f"✅ Found folder '{folder_name}' in parent")
                else:
                    self.logger.debug(f"📝 Folder '{folder_name}' not found in parent (will be created if needed)")
                return result

        except Exception as e:
            self.logger.error(f"❌ Failed to find folder by name: {str(e)}")
            return None

    async def navigate_to_folder_by_path(
        self,
        kb_id: str,
        folder_path: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[Dict]:
        """
        Navigate to a folder using folder names in the path
        Example: "/folder1/subfolder2" -> Find folder1 in KB root, then subfolder2 in folder1
        """
        try:
            if not folder_path or folder_path == "/":
                return None

            # Remove leading slash and split path
            path_parts = folder_path.strip("/").split("/")
            current_parent_id = None  # Start from KB root

            for folder_name in path_parts:
                if not folder_name:  # Skip empty parts
                    continue

                folder = await self.find_folder_by_name_in_parent(
                    kb_id=kb_id,
                    folder_name=folder_name,
                    parent_folder_id=current_parent_id,
                    transaction=transaction
                )

                if not folder:
                    self.logger.debug(f"❌ Folder '{folder_name}' not found in path '{folder_path}'")
                    return None

                current_parent_id = folder["_key"]

            # Return the final folder if we successfully navigated the entire path
            return await self.get_folder_record_by_id(current_parent_id, transaction)

        except Exception as e:
            self.logger.error(f"❌ Failed to navigate to folder by path: {str(e)}")
            return None

    async def create_folder(
        self,
        kb_id: str,
        folder_name: str,
        org_id: str,
        parent_folder_id: Optional[str] = None,
        transaction: Optional[TransactionDatabase] = None
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

            # Create transaction if not provided
            should_commit = False
            if transaction is None:
                should_commit = True
                transaction = self.db.begin_transaction(
                    write=[
                        CollectionNames.RECORDS.value,
                        CollectionNames.FILES.value,
                        CollectionNames.IS_OF_TYPE.value,
                        CollectionNames.BELONGS_TO.value,
                        CollectionNames.RECORD_RELATIONS.value,
                        CollectionNames.INHERIT_PERMISSIONS.value,
                    ]
                )

            location = "KB root" if parent_folder_id is None else f"folder {parent_folder_id}"
            self.logger.info(f"🚀 Creating folder '{folder_name}' in {location}")

            try:
                # Step 1: Validate parent folder exists (if nested)
                if parent_folder_id:
                    parent_folder = await self.get_and_validate_folder_in_kb(kb_id, parent_folder_id, transaction)
                    if not parent_folder:
                        raise ValueError(f"Parent folder {parent_folder_id} not found in KB {kb_id}")

                    self.logger.info(f"✅ Validated parent folder: {parent_folder.get('name')}")

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
                        "folderId": existing_folder["_key"],
                        "name": existing_folder["name"],
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
                    "_key": folder_id,
                    "orgId": org_id,
                    "recordName": folder_name,
                    "externalRecordId": f"kb_folder_{folder_id}",
                    "connectorId": kb_connector_id,  # Always KB ID
                    "externalGroupId": kb_id,  # Always KB ID (the knowledge base)
                    "externalParentId": external_parent_id,  # KB ID for root, parent folder ID for nested
                    "externalRootGroupId": kb_id,  # Always KB ID (the root knowledge base)
                    "recordType": RecordType.FILE.value,
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
                    # Note: inheritPermissions is not stored on the record document (not in schema)
                    # but we default to True for KB folders when creating inheritPermission edges
                }

                self.logger.debug(
                    f"Creating folder RECORDS: root={not parent_folder_id}, "
                    f"parent={external_parent_id}, kb={kb_id}"
                )

                # Step 4: Create FILES document for folder (file metadata)
                folder_data = {
                    "_key": folder_id,
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
                    "_from": f"{CollectionNames.RECORDS.value}/{folder_id}",
                    "_to": f"{CollectionNames.FILES.value}/{folder_id}",
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                }
                await self.batch_create_edges([is_of_type_edge], CollectionNames.IS_OF_TYPE.value, transaction)

                # Step 7: Create relationships
                edges_to_create = []

                # Always create KB relationship (RECORDS -> KB)
                kb_relationship_edge = {
                    "_from": f"{CollectionNames.RECORDS.value}/{folder_id}",
                    "_to": f"{CollectionNames.RECORD_GROUPS.value}/{kb_id}",
                    "entityType": Connectors.KNOWLEDGE_BASE.value,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                }
                edges_to_create.append((kb_relationship_edge, CollectionNames.BELONGS_TO.value))

                # Create inheritPermission edge (RECORDS -> KB)
                # KB folders inherit permissions from KB by default
                inherit_permission_edge = {
                    "_from": f"{CollectionNames.RECORDS.value}/{folder_id}",
                    "_to": f"{CollectionNames.RECORD_GROUPS.value}/{kb_id}",
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                }
                edges_to_create.append((inherit_permission_edge, CollectionNames.INHERIT_PERMISSIONS.value))

                # Create parent-child relationship (RECORDS -> RECORDS)
                if parent_folder_id:
                    # Nested folder: Parent Record -> Child Record
                    parent_child_edge = {
                        "_from": f"{CollectionNames.RECORDS.value}/{parent_folder_id}",
                        "_to": f"{CollectionNames.RECORDS.value}/{folder_id}",
                        "relationshipType": "PARENT_CHILD",
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp,
                    }
                    edges_to_create.append((parent_child_edge, CollectionNames.RECORD_RELATIONS.value))

                # Step 8: Create all edges
                for edge_data, collection in edges_to_create:
                    await self.batch_create_edges([edge_data], collection, transaction)

                # Step 9: Commit transaction
                if should_commit:
                    await asyncio.to_thread(lambda: transaction.commit_transaction())

                self.logger.info(f"✅ Folder '{folder_name}' created successfully with RECORDS document")
                return {
                    "id": folder_id,
                    "name": folder_name,
                    "webUrl": record_data["webUrl"],
                    "exists": False,
                    "success": True
                }

            except Exception as inner_error:
                if should_commit:
                    try:
                        await asyncio.to_thread(lambda: transaction.abort_transaction())
                        self.logger.info("🔄 Transaction aborted after error")
                    except Exception as abort_error:
                        self.logger.error(f"❌ Transaction abort failed: {str(abort_error)}")
                raise inner_error

        except Exception as e:
            self.logger.error(f"❌ Failed to create folder '{folder_name}': {str(e)}")
            raise

    async def update_folder(
        self,
        folder_id:str,
        updates:Dict,
        transaction: Optional[TransactionDatabase]= None
    )-> bool:
        """ Update folder """
        try:
            self.logger.info(f"🚀 Updating folder {folder_id}")

            db = transaction if transaction else self.db

            query = """
            FOR folder IN @@folder_collection
                FILTER folder._key == @folder_id
                UPDATE folder WITH @updates IN @@folder_collection
                RETURN NEW
            """

            cursor = db.aql.execute(query, bind_vars={
                "folder_id": folder_id,
                "updates": updates,
                "@folder_collection": CollectionNames.FILES.value,
            })

            result = next(cursor, None)

            updates_for_record = {
                "_key": folder_id,
                "recordName": updates.get("name"),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }
            await self.batch_upsert_nodes([updates_for_record], CollectionNames.RECORDS.value, transaction)

            if result:
                self.logger.info("✅ Folder updated successfully")
                return True
            else:
                self.logger.warning("⚠️ Folder not found")
                return False

        except Exception as e:
            self.logger.error(f"❌ Failed to update Folder : {str(e)}")
            raise

    async def validate_folder_in_kb(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> bool:
        """
        Validate that a folder exists, is valid, and belongs to a specific knowledge base.
        Folders are now represented by RECORDS documents.
        """
        try:
            db = transaction if transaction else self.db

            query = """
            // Folders are now represented by RECORDS documents
            LET folder_record = DOCUMENT(@@records_collection, @folder_id)
            FILTER folder_record != null
            // Verify it's a folder by checking associated FILES document via IS_OF_TYPE edge
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

            cursor = db.aql.execute(query, bind_vars={
                "folder_id": folder_id,
                "folder_from": f"records/{folder_id}",
                "kb_to": f"recordGroups/{kb_id}",
                "entity_type": Connectors.KNOWLEDGE_BASE.value,
                "@records_collection": CollectionNames.RECORDS.value,
                "@belongs_to_collection": CollectionNames.BELONGS_TO.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
            })

            result = next(cursor, False)

            if not result:
                self.logger.warning(f"⚠️ Folder {folder_id} validation failed for KB {kb_id}")

            return result

        except Exception as e:
            self.logger.error(f"❌ Failed to validate folder in KB: {str(e)}")
            return False

    async def get_and_validate_folder_in_kb(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[Dict]:
        """
        Get folder by ID and validate it belongs to the specified KB in a single query.
        This combines validate_folder_in_kb() and get_folder_record_by_id() for better performance.

        Returns:
            Dict with folder data if valid and belongs to KB, None otherwise
        """
        try:
            db = transaction if transaction else self.db

            query = """
            // Get folder RECORDS document
            LET folder_record = DOCUMENT(@@records_collection, @folder_id)
            FILTER folder_record != null

            // Get associated FILES document via IS_OF_TYPE edge
            LET folder_file = FIRST(
                FOR isEdge IN @@is_of_type
                    FILTER isEdge._from == folder_record._id
                    LET f = DOCUMENT(isEdge._to)
                    FILTER f != null AND f.isFile == false
                    RETURN f
            )

            FILTER folder_file != null

            // Verify BELONGS_TO relationship
            LET relationship = FIRST(
                FOR edge IN @@belongs_to_collection
                    FILTER edge._from == @folder_from
                    FILTER edge._to == @kb_to
                    FILTER edge.entityType == @entity_type
                    RETURN 1
            )

            // Return folder data only if all validations pass
            FILTER relationship != null

            // Return combined folder data
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

            cursor = db.aql.execute(query, bind_vars={
                "folder_id": folder_id,
                "folder_from": f"records/{folder_id}",
                "kb_to": f"recordGroups/{kb_id}",
                "entity_type": Connectors.KNOWLEDGE_BASE.value,
                "@records_collection": CollectionNames.RECORDS.value,
                "@belongs_to_collection": CollectionNames.BELONGS_TO.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
            })

            result = next(cursor, None)

            if not result:
                self.logger.warning(f"⚠️ Folder {folder_id} validation failed for KB {kb_id}")

            return result

        except Exception as e:
            self.logger.error(f"❌ Failed to get and validate folder in KB: {str(e)}")
            return None

    async def validate_record_in_folder(self, folder_id: str, record_id: str, transaction: Optional[TransactionDatabase] = None) -> bool:
        """Check if a record is a child of a folder via PARENT_CHILD edge.
        Folders are now represented by RECORDS documents, so edge is from records/{folder_id} to records/{record_id}"""
        try:
            db = transaction if transaction else self.db
            query = """
            FOR edge IN @@record_relations
                FILTER edge._from == @folder_from
                FILTER edge._to == @record_to
                FILTER edge.relationshipType == "PARENT_CHILD"
                RETURN edge
            """
            cursor = db.aql.execute(query, bind_vars={
                "folder_from": f"records/{folder_id}",
                "record_to": f"records/{record_id}",
                "@record_relations": CollectionNames.RECORD_RELATIONS.value,
            })
            result = next(cursor, None)
            return result is not None
        except Exception as e:
            self.logger.error(f"❌ Failed to validate record {record_id} in folder {folder_id}: {str(e)}")
            return False

    async def validate_record_in_kb(self, kb_id: str, record_id: str, transaction: Optional[TransactionDatabase] = None) -> bool:
        """Check if a record belongs to a KB via BELONGS_TO_KB edge"""
        try:
            db = transaction if transaction else self.db
            query = """
            FOR edge IN @@belongs_to_kb
                FILTER edge._from == @record_from
                FILTER edge._to == @kb_to
                RETURN edge
            """
            cursor = db.aql.execute(query, bind_vars={
                "record_from": f"records/{record_id}",
                "kb_to": f"recordGroups/{kb_id}",
                "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
            })
            result = next(cursor, None)
            return result is not None
        except Exception as e:
            self.logger.error(f"❌ Failed to validate record {record_id} in KB {kb_id}: {str(e)}")
            return False

    async def update_record(
        self,
        record_id: str,
        user_id: str,
        updates: Dict,
        file_metadata: Optional[Dict] = None,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[Dict]:
        """
        Update a record by ID with automatic KB and permission detection
        This method automatically:
        1. Validates user exists
        2. Gets the record and finds its KB via belongs_to_kb edge
        3. Checks user permissions on that KB
        4. Determines if record is in a folder or KB root
        5. Updates the record directly (no redundant validation)
        Args:
            record_id: The record to update
            user_id: External user ID doing the update
            updates: Dictionary of fields to update
            file_metadata: Optional file metadata for file uploads
            transaction: Optional existing transaction
        """
        try:
            self.logger.info(f"🚀 Updating record {record_id} by user {user_id}")
            self.logger.info(f"Record updates: {updates}")
            self.logger.info(f"File metadata: {file_metadata}")

            # Create transaction if not provided
            should_commit = False
            if transaction is None:
                should_commit = True
                try:
                    transaction = self.db.begin_transaction(
                        write=[
                            CollectionNames.RECORDS.value,
                            CollectionNames.FILES.value,
                        ]
                    )
                    self.logger.info("🔄 Transaction created for record update")
                except Exception as tx_error:
                    self.logger.error(f"❌ Failed to create transaction: {str(tx_error)}")
                    return {
                        "success": False,
                        "code": 500,
                        "reason": f"Transaction creation failed: {str(tx_error)}"
                    }

            try:
                # Step 1: Get user by external user ID
                user = await self.get_user_by_user_id(user_id=user_id)
                if not user:
                    self.logger.error(f"❌ User not found: {user_id}")
                    if should_commit:
                        await asyncio.to_thread(lambda: transaction.abort_transaction())
                    return {
                        "success": False,
                        "code": 404,
                        "reason": f"User not found: {user_id}"
                    }

                user_key = user.get('_key')
                self.logger.info(f"✅ Found user: {user_key}")

                # Step 2: Get record, KB context, permissions, and file record in one query
                context_query = """
                LET record = DOCUMENT("records", @record_id)
                FILTER record != null
                FILTER record.isDeleted != true
                // Find KB via belongs_to_kb edge
                LET kb_edge = FIRST(
                    FOR edge IN @@belongs_to_kb
                        FILTER edge._from == record._id
                        FILTER STARTS_WITH(edge._to, 'recordGroups/')
                        RETURN edge
                )
                LET kb = kb_edge ? DOCUMENT(kb_edge._to) : null
                // Check if record is in a folder via PARENT_CHILD relationship
                // Folders are now represented by RECORDS documents, so edge is from records/{folder_id}
                LET folder_edge = FIRST(
                    FOR fld_edge IN @@record_relations
                        FILTER fld_edge._to == record._id
                        FILTER fld_edge.relationshipType == "PARENT_CHILD"
                        FILTER STARTS_WITH(fld_edge._from, 'records/')
                        // Verify the parent is actually a folder by checking its FILES document
                        LET parent_record = DOCUMENT(fld_edge._from)
                        FILTER parent_record != null
                        LET parent_folder_file = FIRST(
                            FOR isEdge IN @@is_of_type
                                FILTER isEdge._from == parent_record._id
                                LET f = DOCUMENT(isEdge._to)
                                FILTER f != null AND f.isFile == false
                                RETURN 1
                        )
                        FILTER parent_folder_file != null
                        RETURN fld_edge
                )
                LET parent_folder = folder_edge ? DOCUMENT(folder_edge._from) : null
                // Check user permissions on the KB
                LET user_permission = kb ? FIRST(
                    FOR perm IN @@permission
                        FILTER perm._from == CONCAT('users/', @user_key)
                        FILTER perm._to == kb._id
                        FILTER perm.type == "USER"
                        RETURN perm.role
                ) : null
                // Get associated file record
                LET file_record = FIRST(
                    FOR isEdge IN @@is_of_type
                        FILTER isEdge._from == record._id
                        LET fileRecord = DOCUMENT(isEdge._to)
                        FILTER fileRecord != null
                        RETURN fileRecord
                )
                RETURN {
                    record_exists: record != null,
                    record: record,
                    kb_exists: kb != null,
                    kb: kb,
                    user_permission: user_permission,
                    has_permission: user_permission != null AND user_permission IN ["OWNER", "WRITER"],
                    parent_folder: parent_folder,
                    folder_id: parent_folder ? parent_folder._key : null,
                    file_record: file_record,
                    validation_passed: record != null AND kb != null AND user_permission != null AND user_permission IN ["OWNER", "WRITER"]
                }
                """

                cursor = transaction.aql.execute(context_query, bind_vars={
                    "record_id": record_id,
                    "user_key": user_key,
                    "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                    "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                    "@permission": CollectionNames.PERMISSION.value,
                    "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                })

                context = next(cursor, {})

                # Step 3: Validate context and permissions
                if not context.get("validation_passed"):
                    if should_commit:
                        await asyncio.to_thread(lambda: transaction.abort_transaction())

                    if not context.get("record_exists"):
                        error_reason = f"Record {record_id} not found or deleted"
                        error_code = 404
                    elif not context.get("kb_exists"):
                        error_reason = f"Knowledge base not found for record {record_id}"
                        error_code = 500
                    elif not context.get("has_permission"):
                        user_perm = context.get("user_permission")
                        if user_perm is None:
                            error_reason = f"User {user_id} has no access to the knowledge base containing record {record_id}"
                            error_code = 403
                        else:
                            error_reason = f"User {user_id} has insufficient permissions ({user_perm}) to update record {record_id}. Required: OWNER or WRITER"
                            error_code = 403
                    else:
                        error_reason = "Unknown validation error"
                        error_code = 500

                    self.logger.error(f"❌ Validation failed: {error_reason}")
                    return {
                        "success": False,
                        "code": error_code,
                        "reason": error_reason
                    }

                # Step 4: Extract validated context
                current_record = context["record"]
                current_file_record = context.get("file_record")
                kb = context["kb"]
                kb_id = kb["_key"]
                folder_id = context.get("folder_id")
                user_permission = context["user_permission"]

                self.logger.info("✅ Context validated:")
                self.logger.info(f"   📚 KB: {kb_id} ({kb.get('groupName', 'Unknown')})")
                self.logger.info(f"   📁 Folder: {folder_id or 'KB Root'}")
                self.logger.info(f"   🔐 Permission: {user_permission}")

                # Step 5: Prepare update data (no redundant validation needed)
                timestamp = get_epoch_timestamp_in_ms()
                # Check SHA256 to determine if version should increment
                # Only increment version if file is being uploaded with new content
                increment_version = False  # Default to False, only True if file content changes
                if file_metadata and current_file_record:
                    new_sha256 = file_metadata.get("sha256Hash")
                    current_sha256 = current_file_record.get("sha256Hash")

                    if new_sha256 and current_sha256 and new_sha256 == current_sha256:
                        increment_version = False
                        self.logger.info(f"File content unchanged (SHA256 match). Keeping version {current_record.get('version', 0)}")
                    else:
                        # File content changed - increment version
                        increment_version = True
                        self.logger.info(f"File content changed. Incrementing version to {current_record.get('version', 0) + 1}")

                version = (current_record.get("version", 0)) + (1 if increment_version else 0)
                processed_updates = {
                    **updates,
                    "version": version,
                    "updatedAtTimestamp": timestamp
                }

                # Handle file upload case
                if file_metadata:
                    sourceLastModifiedTimestamp = file_metadata.get("lastModified", timestamp)
                    processed_updates.update({
                        "sourceCreatedAtTimestamp": sourceLastModifiedTimestamp,
                        "sourceLastModifiedTimestamp": sourceLastModifiedTimestamp,
                    })

                    # Set record name from file if provided
                    original_name = self._normalize_name(file_metadata.get("originalname", ""))
                    if original_name and "." in original_name:
                        last_dot = original_name.rfind(".")
                        if last_dot > 0:
                            processed_updates["recordName"] = original_name[:last_dot]

                # Step 6: Update the record directly
                record_update_query = """
                FOR record IN @@records_collection
                    FILTER record._key == @record_id
                    UPDATE record WITH @updates IN @@records_collection
                    RETURN NEW
                """

                cursor = transaction.aql.execute(record_update_query, bind_vars={
                    "record_id": record_id,
                    "updates": processed_updates,
                    "@records_collection": CollectionNames.RECORDS.value,
                })

                updated_record = next(cursor, None)

                if not updated_record:
                    if should_commit:
                        await asyncio.to_thread(lambda: transaction.abort_transaction())
                    return {
                        "success": False,
                        "code": 500,
                        "reason": f"Failed to update record {record_id}"
                    }

                # Step 7: Update file record if file metadata provided
                file_updated = False
                updated_file = current_file_record

                if file_metadata and current_file_record:
                    file_updates = {}

                    if "originalname" in file_metadata:
                        file_updates["name"] = self._normalize_name(file_metadata["originalname"])

                    if "size" in file_metadata:
                        file_updates["sizeInBytes"] = file_metadata["size"]

                    if "md5Checksum" in file_metadata:
                        file_updates["md5Checksum"] = file_metadata["md5Checksum"]

                    if file_updates:
                        file_update_query = """
                        FOR file IN @@files_collection
                            FILTER file._key == @file_key
                            UPDATE file WITH @file_updates IN @@files_collection
                            RETURN NEW
                        """

                        try:
                            file_cursor = transaction.aql.execute(file_update_query, bind_vars={
                                "file_key": current_file_record["_key"],
                                "file_updates": file_updates,
                                "@files_collection": CollectionNames.FILES.value,
                            })

                            updated_file = next(file_cursor, None)
                            if updated_file:
                                file_updated = True
                                self.logger.info(f"✅ File metadata updated for record {record_id}")

                        except Exception as file_error:
                            self.logger.error(f"❌ Failed to update file metadata: {str(file_error)}")
                            # Continue without failing the entire operation

                # Step 8: Commit transaction
                if should_commit:
                    self.logger.info("💾 Committing record update transaction...")
                    try:
                        await asyncio.to_thread(lambda: transaction.commit_transaction())
                        self.logger.info("✅ Transaction committed successfully!")
                    except Exception as commit_error:
                        self.logger.error(f"❌ Transaction commit failed: {str(commit_error)}")
                        try:
                            await asyncio.to_thread(lambda: transaction.abort_transaction())
                            self.logger.info("🔄 Transaction aborted after commit failure")
                        except Exception as abort_error:
                            self.logger.error(f"❌ Transaction abort failed: {str(abort_error)}")
                        return {
                            "success": False,
                            "code": 500,
                            "reason": f"Transaction commit failed: {str(commit_error)}"
                        }

                # Step 9: Publish update event (after successful commit)
                try:
                    # Only trigger reindex if file content changed (not for name-only updates)
                    content_changed = increment_version and file_metadata is not None
                    update_payload = await self._create_update_record_event_payload(
                        updated_record, updated_file, content_changed=content_changed
                    )
                    if update_payload:
                        await self._publish_record_event("updateRecord", update_payload)
                except Exception as event_error:
                    self.logger.error(f"❌ Failed to publish update event: {str(event_error)}")
                    # Don't fail the main operation for event publishing errors

                self.logger.info(f"✅ Record {record_id} updated successfully with auto-detected context")

                return {
                    "success": True,
                    "updatedRecord": updated_record,
                    "updatedFile": updated_file,
                    "fileUpdated": file_updated,
                    "recordId": record_id,
                    "timestamp": timestamp,
                    "location": "folder" if folder_id else "kb_root",
                    "folderId": folder_id,
                    "kb": kb,
                    "userPermission": user_permission,
                }

            except Exception as db_error:
                self.logger.error(f"❌ Database error during record update: {str(db_error)}")
                if should_commit and transaction:
                    try:
                        await asyncio.to_thread(lambda: transaction.abort_transaction())
                        self.logger.info("🔄 Transaction aborted due to error")
                    except Exception as abort_error:
                        self.logger.error(f"❌ Transaction abort failed: {str(abort_error)}")
                return {
                    "success": False,
                    "code": 500,
                    "reason": f"Database error: {str(db_error)}"
                }

        except Exception as e:
            self.logger.error(f"❌ Failed to update record {record_id}: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": f"Service error: {str(e)}"
            }

    async def delete_records(
        self,
        record_ids: List[str],
        kb_id: str,
        folder_id: Optional[str] = None,
        transaction: Optional[TransactionDatabase] = None
    ) -> Dict:
        """
        Delete multiple records and publish delete events for each
        Returns details about successfully deleted records for event publishing
        """
        try:
            if not record_ids:
                return {
                    "success": True,
                    "deleted_records": [],
                    "failed_records": [],
                    "total_requested": 0,
                    "successfully_deleted": 0,
                    "failed_count": 0
                }

            self.logger.info(f"🚀 Bulk deleting {len(record_ids)} records from {'folder ' + folder_id if folder_id else 'KB root'}")

            # Create transaction if not provided
            should_commit = False
            if transaction is None:
                should_commit = True
                try:
                    transaction = self.db.begin_transaction(
                        write=[
                            CollectionNames.RECORDS.value,
                            CollectionNames.FILES.value,
                            CollectionNames.RECORD_RELATIONS.value,
                            CollectionNames.IS_OF_TYPE.value,
                            CollectionNames.BELONGS_TO.value,
                        ]
                    )
                    self.logger.info("🔄 Transaction created for bulk record deletion")
                except Exception as tx_error:
                    self.logger.error(f"❌ Failed to create transaction: {str(tx_error)}")
                    return {
                        "success": False,
                        "reason": f"Transaction creation failed: {str(tx_error)}"
                    }

            try:
                # Step 1: Get complete record details for validation and event publishing
                self.logger.info("🔍 Step 1: Getting record details for event publishing...")

                validation_query = """
                LET records_with_details = (
                    FOR rid IN @record_ids
                        LET record = DOCUMENT("records", rid)
                        LET record_exists = record != null
                        LET record_not_deleted = record_exists ? record.isDeleted != true : false
                        // Check KB relationship
                        LET kb_relationship = record_exists ? FIRST(
                            FOR edge IN @@belongs_to_kb
                                FILTER edge._from == CONCAT('records/', rid)
                                FILTER edge._to == CONCAT('recordGroups/', @kb_id)
                                RETURN edge
                        ) : null
                        // Check folder relationship if folder_id provided
                        // Folders are now represented by RECORDS documents, so edge is from records/{folder_id}
                        LET folder_relationship = @folder_id ? (
                            record_exists ? FIRST(
                                FOR edge_rel IN @@record_relations
                                    FILTER edge_rel._to == CONCAT('records/', rid)
                                    FILTER edge_rel._from == CONCAT('records/', @folder_id)
                                    FILTER edge_rel.relationshipType == "PARENT_CHILD"
                                    RETURN edge_rel
                            ) : null
                        ) : true
                        // Get associated file record
                        LET file_record = record_exists ? FIRST(
                            FOR isEdge IN @@is_of_type
                                FILTER isEdge._from == CONCAT('records/', rid)
                                LET fileRec = DOCUMENT(isEdge._to)
                                FILTER fileRec != null
                                RETURN fileRec
                        ) : null
                        LET is_valid = record_exists AND record_not_deleted AND kb_relationship != null AND folder_relationship != null
                        RETURN {
                            record_id: rid,
                            record: record,
                            file_record: file_record,
                            is_valid: is_valid,
                            validation_errors: is_valid ? [] : [
                                !record_exists ? "Record not found" : null,
                                record_exists AND !record_not_deleted ? "Record already deleted" : null,
                                kb_relationship == null ? "Not in specified KB" : null,
                                folder_relationship == null ? "Not in specified folder" : null
                            ]
                        }
                )
                LET valid_records = records_with_details[* FILTER CURRENT.is_valid]
                LET invalid_records = records_with_details[* FILTER !CURRENT.is_valid]
                RETURN {
                    valid_records: valid_records,
                    invalid_records: invalid_records,
                    total_valid: LENGTH(valid_records),
                    total_invalid: LENGTH(invalid_records)
                }
                """

                cursor = transaction.aql.execute(validation_query, bind_vars={
                    "record_ids": record_ids,
                    "kb_id": kb_id,
                    "folder_id": folder_id,
                    "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                    "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                    "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                })

                validation_result = next(cursor, {})

                valid_records = validation_result.get("valid_records", [])
                invalid_records = validation_result.get("invalid_records", [])

                self.logger.info(f"📋 Validation complete: {len(valid_records)} valid, {len(invalid_records)} invalid")

                deleted_records = []
                failed_records = []

                # Add invalid records to failed list
                for invalid_record in invalid_records:
                    errors = [err for err in invalid_record["validation_errors"] if err]
                    failed_records.append({
                        "record_id": invalid_record["record_id"],
                        "reason": ", ".join(errors) if errors else "Record validation failed"
                    })

                # If no valid records found, return early
                if not valid_records:
                    if should_commit:
                        try:
                            await asyncio.to_thread(lambda: transaction.commit_transaction())
                            self.logger.info("✅ Transaction committed (no records to delete)")
                        except Exception as commit_error:
                            self.logger.error(f"❌ Transaction commit failed: {str(commit_error)}")
                            try:
                                await asyncio.to_thread(lambda: transaction.abort_transaction())
                            except Exception as abort_error:
                                self.logger.error(f"❌ Transaction abort failed: {str(abort_error)}")

                    self.logger.info("✅ No valid records found to delete")
                    return {
                        "success": True,
                        "deleted_records": [],
                        "failed_records": failed_records,
                        "total_requested": len(record_ids),
                        "successfully_deleted": 0,
                        "failed_count": len(failed_records),
                        "files_deleted": 0,
                        "location": "folder" if folder_id else "kb_root",
                        "folder_id": folder_id,
                        "kb_id": kb_id,
                        "message": "Records already deleted or not found"
                    }

                # Store records for event publishing before deletion
                records_for_events = []
                for valid_record in valid_records:
                    records_for_events.append({
                        "record": valid_record["record"],
                        "file_record": valid_record["file_record"]
                    })

                # Step 2: Delete edges, file records, and records
                self.logger.info("🗑️ Step 2: Deleting records and associated data...")

                valid_record_ids = [r["record_id"] for r in valid_records]
                file_record_ids = [r["file_record"]["_key"] for r in valid_records if r["file_record"]]

                # Delete edges
                if valid_record_ids:
                    # Delete all edges related to these records
                    edges_cleanup_query = """
                    FOR record_id IN @record_ids
                        // Delete record relations edges
                        FOR rec_rel_edge IN @@record_relations
                            FILTER rec_rel_edge._from == CONCAT('records/', record_id) OR rec_rel_edge._to == CONCAT('records/', record_id)
                            REMOVE rec_rel_edge IN @@record_relations
                        // Delete is_of_type edges
                        FOR iot_edge IN @@is_of_type
                            FILTER iot_edge._from == CONCAT('records/', record_id)
                            REMOVE iot_edge IN @@is_of_type
                        // Delete belongs_to_kb edges
                        FOR btk_edge IN @@belongs_to_kb
                            FILTER btk_edge._from == CONCAT('records/', record_id)
                            REMOVE btk_edge IN @@belongs_to_kb
                    """
                    transaction.aql.execute(edges_cleanup_query, bind_vars={
                        "record_ids": valid_record_ids,
                        "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                        "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                        "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                    })
                    self.logger.info(f"✅ Deleted edges for {len(valid_record_ids)} records")

                # Delete file records
                if file_record_ids:
                    file_records_delete_query = """
                    FOR file_key IN @file_keys
                        REMOVE file_key IN @@files_collection
                    """
                    transaction.aql.execute(file_records_delete_query, bind_vars={
                        "file_keys": file_record_ids,
                        "@files_collection": CollectionNames.FILES.value,
                    })
                    self.logger.info(f"✅ Deleted {len(file_record_ids)} file records")

                # Delete records and track successful deletions
                if valid_record_ids:
                    records_delete_query = """
                    FOR record_key IN @record_keys
                        LET record_doc = DOCUMENT(@@records_collection, record_key)
                        FILTER record_doc != null
                        REMOVE record_doc IN @@records_collection
                        RETURN OLD
                    """

                    cursor = transaction.aql.execute(records_delete_query, bind_vars={
                        "record_keys": valid_record_ids,
                        "@records_collection": CollectionNames.RECORDS.value,
                    })

                    actually_deleted = list(cursor)
                    deleted_records = [
                        {"record_id": record["_key"], "name": record.get("recordName", "Unknown")}
                        for record in actually_deleted
                    ]

                    self.logger.info(f"✅ Deleted {len(deleted_records)} records")

                # Step 3: Commit transaction
                if should_commit:
                    self.logger.info("💾 Committing bulk deletion transaction...")
                    try:
                        await asyncio.to_thread(lambda: transaction.commit_transaction())
                        self.logger.info("✅ Transaction committed successfully!")
                    except Exception as commit_error:
                        self.logger.error(f"❌ Transaction commit failed: {str(commit_error)}")
                        try:
                            await asyncio.to_thread(lambda: transaction.abort_transaction())
                            self.logger.info("🔄 Transaction aborted after commit failure")
                        except Exception as abort_error:
                            self.logger.error(f"❌ Transaction abort failed: {str(abort_error)}")
                        return {
                            "success": False,
                            "reason": f"Transaction commit failed: {str(commit_error)}"
                        }

                # Step 4: Publish delete events for successfully deleted records
                try:
                    delete_event_tasks = []
                    for record_data in records_for_events:
                        if any(d["record_id"] == record_data["record"]["_key"] for d in deleted_records):
                            # Only publish events for actually deleted records
                            delete_payload = await self._create_deleted_record_event_payload(
                                record_data["record"], record_data["file_record"]
                            )
                            if delete_payload:
                                delete_event_tasks.append(
                                    self._publish_record_event("deleteRecord", delete_payload)
                                )

                    if delete_event_tasks:
                        await asyncio.gather(*delete_event_tasks, return_exceptions=True)
                        self.logger.info(f"✅ Published delete events for {len(delete_event_tasks)} records")

                except Exception as event_error:
                    self.logger.error(f"❌ Failed to publish delete events: {str(event_error)}")
                    # Don't fail the main operation for event publishing errors

                success_count = len(deleted_records)
                failed_count = len(failed_records)

                self.logger.info(f"🎉 Bulk deletion completed: {success_count} deleted, {failed_count} failed")

                return {
                    "success": True,
                    "deleted_records": deleted_records,
                    "failed_records": failed_records,
                    "total_requested": len(record_ids),
                    "successfully_deleted": success_count,
                    "failed_count": failed_count,
                    "files_deleted": len(file_record_ids),
                    "location": "folder" if folder_id else "kb_root",
                    "folder_id": folder_id,
                    "kb_id": kb_id
                }

            except Exception as db_error:
                self.logger.error(f"❌ Database error during bulk deletion: {str(db_error)}")
                if should_commit and transaction:
                    try:
                        await asyncio.to_thread(lambda: transaction.abort_transaction())
                        self.logger.info("🔄 Transaction aborted due to error")
                    except Exception as abort_error:
                        self.logger.error(f"❌ Transaction abort failed: {str(abort_error)}")
                raise db_error

        except Exception as e:
            self.logger.error(f"❌ Failed bulk record deletion: {str(e)}")
            return {
                "success": False,
                "reason": f"Service error: {str(e)}"
            }

    async def validate_users_exist(
        self,
        user_ids: List[str],
        transaction: Optional[TransactionDatabase] = None
    ) -> List[str]:
        """Validate which users exist in the database"""
        try:
            db = transaction if transaction else self.db

            query = """
            FOR user_id IN @user_ids
                LET user = FIRST(
                    FOR user IN @@users_collection
                        FILTER user._key == user_id
                        RETURN user._key
                )
                FILTER user != null
                RETURN user
            """

            cursor = db.aql.execute(query, bind_vars={
                "user_ids": user_ids,
                "@users_collection": CollectionNames.USERS.value,
            })

            return list(cursor)

        except Exception as e:
            self.logger.error(f"❌ Failed to validate users exist: {str(e)}")
            return []

    async def get_existing_kb_permissions(
        self,
        kb_id: str,
        user_ids: List[str],
        transaction: Optional[TransactionDatabase] = None
    ) -> Dict[str, str]:
        """Get existing permissions for specified users on a KB"""
        try:
            db = transaction if transaction else self.db

            query = """
            FOR user_id IN @user_ids
                FOR perm IN @@permissions_collection
                    FILTER perm._from == CONCAT('users/', user_id)
                    FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                    RETURN {
                        user_id: user_id,
                        role: perm.role
                    }
            """

            cursor = db.aql.execute(query, bind_vars={
                "user_ids": user_ids,
                "kb_id": kb_id,
                "@permissions_collection": CollectionNames.PERMISSION.value,
            })

            result = {}
            for perm in cursor:
                result[perm["user_id"]] = perm["role"]

            return result

        except Exception as e:
            self.logger.error(f"❌ Failed to get existing KB permissions: {str(e)}")
            return {}

    async def create_kb_permissions(
        self,
        kb_id: str,
        requester_id: str,
        user_ids: List[str],
        team_ids: List[str],
        role: str
    ) -> Dict:
        """Create kb permissions for users and teams - Optimized version"""
        try:
            timestamp = get_epoch_timestamp_in_ms()

            # Single comprehensive query to validate, check existing permissions, and prepare data
            main_query = """
            // Get requester info and validate ownership in one go
            LET requester_info = FIRST(
                FOR user IN @@users_collection
                FILTER user.userId == @requester_id
                FOR perm IN @@permissions_collection
                    FILTER perm._from == CONCAT('users/', user._key)
                    FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                    FILTER perm.type == "USER"
                    FILTER perm.role == "OWNER"
                RETURN {user_key: user._key, is_owner: true}
            )

            // Quick KB existence check
            LET kb_exists = LENGTH(FOR kb IN @@recordGroups_collection FILTER kb._key == @kb_id LIMIT 1 RETURN 1) > 0

            // Process all users and their current permissions in one pass
            LET user_operations = (
                FOR user_id IN @user_ids
                    LET user = FIRST(FOR u IN @@users_collection FILTER u._key == user_id RETURN u)
                    LET current_perm = user ? FIRST(
                        FOR perm IN @@permissions_collection
                        FILTER perm._from == CONCAT('users/', user._key)
                        FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                        FILTER perm.type == "USER"
                        RETURN perm
                    ) : null

                    FILTER user != null  // Skip non-existent users

                    LET operation = current_perm == null ? "insert" :
                                (current_perm.role != @role ? "update" : "skip")

                    RETURN {
                        user_id: user_id,
                        user_key: user._key,
                        userId: user.userId,
                        name: user.fullName,
                        operation: operation,
                        current_role: current_perm ? current_perm.role : null,
                        perm_key: current_perm ? current_perm._key : null
                    }
            )

            // Process all teams and their current permissions in one pass
            // Teams don't have roles - they just have access, roles come from team membership
            LET team_operations = (
                FOR team_id IN @team_ids
                    LET team = FIRST(FOR t IN @@teams_collection FILTER t._key == team_id RETURN t)
                    LET current_perm = team ? FIRST(
                        FOR perm IN @@permissions_collection
                        FILTER perm._from == CONCAT('teams/', team._key)
                        FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                        FILTER perm.type == "TEAM"
                        RETURN perm
                    ) : null

                    FILTER team != null  // Skip non-existent teams

                    // Teams either exist or don't - no role comparison needed
                    LET operation = current_perm == null ? "insert" : "skip"

                    RETURN {
                        team_id: team_id,
                        team_key: team._key,
                        name: team.name,
                        operation: operation,
                        perm_key: current_perm ? current_perm._key : null
                    }
            )

            RETURN {
                is_valid: requester_info != null AND kb_exists,
                requester_found: requester_info != null,
                kb_exists: kb_exists,
                user_operations: user_operations,
                team_operations: team_operations,
                users_to_insert: user_operations[* FILTER CURRENT.operation == "insert"],
                users_to_update: user_operations[* FILTER CURRENT.operation == "update"],
                users_skipped: user_operations[* FILTER CURRENT.operation == "skip"],
                teams_to_insert: team_operations[* FILTER CURRENT.operation == "insert"],
                teams_skipped: team_operations[* FILTER CURRENT.operation == "skip"]
            }
            """

            cursor = self.db.aql.execute(main_query, bind_vars={
                "kb_id": kb_id,
                "requester_id": requester_id,
                "user_ids": user_ids,
                "team_ids": team_ids,
                "role": role,
                "@users_collection": CollectionNames.USERS.value,
                "@teams_collection": CollectionNames.TEAMS.value,
                "@permissions_collection": CollectionNames.PERMISSION.value,
                "@recordGroups_collection": CollectionNames.RECORD_GROUPS.value,
            })

            result = next(cursor, {})

            # Fast validation
            if not result.get("is_valid"):
                if not result.get("requester_found"):
                    return {"success": False, "reason": "Requester not found or not owner", "code": 403}
                if not result.get("kb_exists"):
                    return {"success": False, "reason": "Knowledge base not found", "code": 404}

            users_to_insert = result.get("users_to_insert", [])
            users_skipped = result.get("users_skipped", [])
            teams_to_insert = result.get("teams_to_insert", [])
            teams_skipped = result.get("teams_skipped", [])

            # Prepare batch operations
            operations = []

            # Batch insert new permissions
            if users_to_insert or teams_to_insert:
                insert_docs = []

                for user_data in users_to_insert:
                    insert_docs.append({
                        "_from": f"users/{user_data['user_key']}",
                        "_to": f"recordGroups/{kb_id}",
                        "externalPermissionId": "",
                        "type": "USER",
                        "role": role,
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp,
                        "lastUpdatedTimestampAtSource": timestamp,
                    })

                for team_data in teams_to_insert:
                    insert_docs.append({
                        "_from": f"teams/{team_data['team_key']}",
                        "_to": f"recordGroups/{kb_id}",
                        "externalPermissionId": "",
                        "type": "TEAM",
                        # Teams don't have roles - access is determined by user's role in the team
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp,
                        "lastUpdatedTimestampAtSource": timestamp,
                    })

                if insert_docs:
                    operations.append((
                        "FOR doc IN @docs INSERT doc INTO @@permissions_collection",
                        {"docs": insert_docs, "@permissions_collection": CollectionNames.PERMISSION.value}
                    ))

            # Execute all operations in sequence (could be made parallel if needed)
            for query, bind_vars in operations:
                self.db.aql.execute(query, bind_vars=bind_vars)

            # Build optimized response
            granted_count = len(users_to_insert) + len(teams_to_insert)

            final_result = {
                "success": True,
                "grantedCount": granted_count,
                "grantedUsers": [u["user_id"] for u in users_to_insert],
                "grantedTeams": [t["team_id"] for t in teams_to_insert],
                "role": role,
                "kbId": kb_id,
                "details": {
                    "granted": {
                        "users": [{"user_key": u["user_id"], "userId": u["userId"], "name": u["name"]} for u in users_to_insert],
                        "teams": [{"team_key": t["team_id"], "name": t["name"]} for t in teams_to_insert]
                    },
                    "skipped": {
                        "users": [{"user_key": u["user_id"], "userId": u["userId"], "name": u["name"], "role": u["current_role"]} for u in users_skipped],
                        "teams": [{"team_key": t["team_id"], "name": t["name"], "role": t["current_role"]} for t in teams_skipped]
                    }
                }
            }

            self.logger.info(f"Optimized batch operation: {granted_count} granted, {len(users_skipped + teams_skipped)} skipped")
            return final_result

        except Exception as e:
            self.logger.error(f"Failed optimized batch operation: {str(e)}")
            return {"success": False, "reason": f"Database error: {str(e)}", "code": 500}

    async def update_kb_permission(
        self,
        kb_id: str,
        requester_id: str,
        user_ids: List[str],
        team_ids: List[str],
        new_role: str
    ) -> Optional[Dict]:
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

            # Single atomic operation: check requester permission + get current permissions + update
            bind_vars = {
                "kb_id": kb_id,
                "requester_id": requester_id,
                "new_role": new_role,
                "timestamp": get_epoch_timestamp_in_ms(),
                "@permissions_collection": CollectionNames.PERMISSION.value,
            }

            # Build conditions for targets
            target_conditions = []
            if user_ids:
                target_conditions.append("(perm._from IN @user_froms AND perm.type == 'USER')")
                bind_vars["user_froms"] = [f"users/{user_id}" for user_id in user_ids]

            # Teams don't have roles - they just have access or not
            # So we skip team updates in this method
            # if team_ids:
            #     target_conditions.append("(perm._from IN @team_froms AND perm.type == 'TEAM')")
            #     bind_vars["team_froms"] = [f"teams/{team_id}" for team_id in team_ids]

            # Atomic query that does everything in one go
            atomic_query = f"""
            LET requester_perm = FIRST(
                FOR perm IN @@permissions_collection
                    FILTER perm._from == CONCAT('users/', @requester_id)
                    FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                    FILTER perm.type == 'USER'
                    RETURN perm.role
            )

            LET current_perms = (
                FOR perm IN @@permissions_collection
                    FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                    FILTER ({' OR '.join(target_conditions)})
                    RETURN {{
                        _key: perm._key,
                        id: SPLIT(perm._from, '/')[1],
                        type: perm.type,
                        current_role: perm.role,
                        _from: perm._from
                    }}
            )

            LET validation_result = (
                requester_perm != "OWNER" ? {{error: "Only KB owners can update permissions", code: "403"}} :
                null
            )

            LET updated_perms = (
                validation_result == null ? (
                    FOR perm IN @@permissions_collection
                        FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                        FILTER ({' OR '.join(target_conditions)})
                        UPDATE perm WITH {{
                            role: @new_role,
                            updatedAtTimestamp: @timestamp,
                            lastUpdatedTimestampAtSource: @timestamp
                        }} IN @@permissions_collection
                        RETURN {{
                            _key: NEW._key,
                            id: SPLIT(NEW._from, '/')[1],
                            type: NEW.type,
                            old_role: OLD.role,
                            new_role: NEW.role
                        }}
                ) : []
            )
            RETURN {{
                validation_error: validation_result,
                current_permissions: current_perms,
                updated_permissions: updated_perms,
                requester_role: requester_perm
            }}
            """

            cursor = self.db.aql.execute(atomic_query, bind_vars=bind_vars)
            result = next(cursor, None)

            if not result:
                return {"success": False, "reason": "Query execution failed", "code": "500"}

            # Log the raw result for debugging
            self.logger.info(f"🔍 Update query result: {result}")

            # Check for validation errors
            if result["validation_error"]:
                error = result["validation_error"]
                return {"success": False, "reason": error["error"], "code": error["code"]}

            updated_permissions = result["updated_permissions"]

            # Count updates by type (only users can be updated, teams don't have roles)
            updated_users = sum(1 for perm in updated_permissions if perm["type"] == "USER")
            updated_teams = 0  # Teams don't have roles to update

            # Build detailed response
            updates_by_type = {"users": {}, "teams": {}}
            for perm in updated_permissions:
                if perm["type"] == "USER":
                    updates_by_type["users"][perm["id"]] = {
                        "old_role": perm["old_role"],
                        "new_role": perm["new_role"]
                    }
                # Teams don't have roles, so we don't update them

            self.logger.info(f"✅ Optimistically updated {len(updated_permissions)} permissions for KB {kb_id}")

            return {
                "success": True,
                "kb_id": kb_id,
                "new_role": new_role,
                "updated_permissions": len(updated_permissions),
                "updated_users": updated_users,
                "updated_teams": updated_teams,
                "updates_detail": updates_by_type,
                "requester_role": result["requester_role"]
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
        transaction: Optional[TransactionDatabase] = None
    ) -> bool:
        """Remove permissions for multiple users and teams from a KB (internal method)"""
        try:
            db = transaction if transaction else self.db

            # Build conditions for batch removal
            conditions = []
            bind_vars = {
                "kb_id": kb_id,
                "@permissions_collection": CollectionNames.PERMISSION.value,
            }

            if user_ids:
                conditions.append("(perm._from IN @user_froms AND perm.type == 'USER')")
                bind_vars["user_froms"] = [f"users/{user_id}" for user_id in user_ids]

            if team_ids:
                conditions.append("(perm._from IN @team_froms AND perm.type == 'TEAM')")
                bind_vars["team_froms"] = [f"teams/{team_id}" for team_id in team_ids]

            if not conditions:
                return False

            query = f"""
            FOR perm IN @@permissions_collection
                FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                FILTER ({' OR '.join(conditions)})
                REMOVE perm IN @@permissions_collection
                RETURN {{
                    _key: OLD._key,
                    _from: OLD._from,
                    type: OLD.type,
                    role: OLD.role
                }}
            """

            cursor = db.aql.execute(query, bind_vars=bind_vars)
            results = list(cursor)

            if results:
                removed_users = sum(1 for perm in results if perm["type"] == "USER")
                removed_teams = sum(1 for perm in results if perm["type"] == "TEAM")
                self.logger.info(f"✅ Removed {len(results)} permissions from KB {kb_id} ({removed_users} users, {removed_teams} teams)")
                return True
            else:
                self.logger.warning(f"⚠️ No permissions found to remove from KB {kb_id}")
                return False

        except Exception as e:
            self.logger.error(f"❌ Failed to remove KB permissions: {str(e)}")
            return False


    async def count_kb_owners(
        self,
        kb_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> int:
        """Count the number of owners for a knowledge base"""
        try:
            db = transaction if transaction else self.db

            query = """
            FOR perm IN @@permissions_collection
                FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                FILTER perm.role == 'OWNER'
                COLLECT WITH COUNT INTO owner_count
                RETURN owner_count
            """

            cursor = db.aql.execute(query, bind_vars={
                "kb_id": kb_id,
                "@permissions_collection": CollectionNames.PERMISSION.value,
            })

            count = next(cursor, 0)
            self.logger.info(f"📊 KB {kb_id} has {count} owners")
            return count

        except Exception as e:
            self.logger.error(f"❌ Failed to count KB owners: {str(e)}")
            return 0

    async def get_kb_permissions(
        self,
        kb_id: str,
        user_ids: Optional[List[str]] = None,
        team_ids: Optional[List[str]] = None,
        transaction: Optional[TransactionDatabase] = None
    ) -> Dict[str, Dict[str, str]]:
        """Get current roles for multiple users and teams on a knowledge base in a single query"""
        try:
            db = transaction if transaction else self.db

            # Build conditions for batch query
            conditions = []
            bind_vars = {
                "kb_id": kb_id,
                "@permissions_collection": CollectionNames.PERMISSION.value,
            }

            if user_ids:
                conditions.append("(perm._from IN @user_froms AND perm.type == 'USER')")
                bind_vars["user_froms"] = [f"users/{user_id}" for user_id in user_ids]

            if team_ids:
                conditions.append("(perm._from IN @team_froms AND perm.type == 'TEAM')")
                bind_vars["team_froms"] = [f"teams/{team_id}" for team_id in team_ids]

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

            cursor = db.aql.execute(query, bind_vars=bind_vars)
            permissions = list(cursor)

            # Organize results by type
            result = {"users": {}, "teams": {}}

            for perm in permissions:
                if perm["type"] == "USER":
                    result["users"][perm["id"]] = perm["role"]
                elif perm["type"] == "TEAM":
                    # Teams don't have roles - they just have access
                    result["teams"][perm["id"]] = None

            self.logger.info(f"✅ Retrieved {len(permissions)} permissions for KB {kb_id}")
            return result

        except Exception as e:
            self.logger.error(f"❌ Failed to get KB permissions batch: {str(e)}")
            raise

    async def list_kb_permissions(
        self,
        kb_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> List[Dict]:
        """
        List all permissions for a KB with user details.
        Optimized: Batch fetches entities instead of individual DOCUMENT() calls.
        Recommended indexes:
        - permission collection: [ "_to", "type" ] (persistent index)
        - users collection: [ "_key" ] (primary index)
        - teams collection: [ "_key" ] (primary index)
        """
        try:
            db = transaction if transaction else self.db

            # Optimized query: Collect all entity IDs first, then batch fetch
            query = """
            // Collect all permission edges and entity IDs
            LET perms_with_ids = (
                FOR perm IN @@permissions_collection
                    FILTER perm._to == @kb_to
                    RETURN {
                        perm: perm,
                        entity_id: perm._from
                    }
            )

            // Batch fetch all users
            LET user_ids = UNIQUE(perms_with_ids[* FILTER STARTS_WITH(CURRENT.entity_id, "users/")].entity_id)
            LET users = (
                FOR user_id IN user_ids
                    LET user = DOCUMENT(user_id)
                    FILTER user != null
                    RETURN {
                        _id: user._id,
                        _key: user._key,
                        fullName: user.fullName,
                        name: user.name,
                        userName: user.userName,
                        userId: user.userId,
                        email: user.email
                    }
            )

            // Batch fetch all teams
            LET team_ids = UNIQUE(perms_with_ids[* FILTER STARTS_WITH(CURRENT.entity_id, "teams/")].entity_id)
            LET teams = (
                FOR team_id IN team_ids
                    LET team = DOCUMENT(team_id)
                    FILTER team != null
                    RETURN {
                        _id: team._id,
                        _key: team._key,
                        name: team.name
                    }
            )

            // Join permissions with entities
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
                    role: perm.type == "TEAM" ? null : perm.role,  // Teams don't have roles
                    type: perm.type,
                    createdAtTimestamp: perm.createdAtTimestamp,
                    updatedAtTimestamp: perm.updatedAtTimestamp
                }
            """

            cursor = db.aql.execute(query, bind_vars={
                "kb_to": f"recordGroups/{kb_id}",
                "@permissions_collection": CollectionNames.PERMISSION.value,
            })

            return list(cursor)

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
        """
        List all records the user can access directly via belongs_to_kb edges.
        Returns (records, total_count, available_filters)
        """
        try:
            self.logger.info(f"🔍 Listing all records for user {user_id}, source: {source}")

            # Determine what data sources to include
            include_kb_records = source in ['all', 'local']
            include_connector_records = source in ['all', 'connector']

            # Build filter conditions function
            def build_record_filters(include_filter_vars: bool = True) -> str:
                conditions = []
                if search and include_filter_vars:
                    conditions.append("(LIKE(LOWER(record.recordName), @search) OR LIKE(LOWER(record.externalRecordId), @search))")
                if record_types and include_filter_vars:
                    conditions.append("record.recordType IN @record_types")
                if origins and include_filter_vars:
                    conditions.append("record.origin IN @origins")
                if connectors and include_filter_vars:
                    conditions.append("record.connectorName IN @connectors")
                if indexing_status and include_filter_vars:
                    conditions.append("record.indexingStatus IN @indexing_status")
                if date_from and include_filter_vars:
                    conditions.append("record.createdAtTimestamp >= @date_from")
                if date_to and include_filter_vars:
                    conditions.append("record.createdAtTimestamp <= @date_to")

                return " AND " + " AND ".join(conditions) if conditions else ""

            base_kb_roles = {"OWNER", "READER", "FILEORGANIZER", "WRITER", "COMMENTER", "ORGANIZER"}
            if permissions:
                # This ensures we only filter by roles that are valid for KBs AND requested by the user.
                final_kb_roles = list(base_kb_roles.intersection(set(permissions)))
                # If the intersection is empty, no KB records will match, which is correct.
                if not final_kb_roles:
                    # To prevent an empty `IN []` which can be inefficient, we can just disable the kbRecords part.
                    include_kb_records = False
            else:
                final_kb_roles = list(base_kb_roles)

            # Build permission filter for connector records
            def build_permission_filter(include_filter_vars: bool = True) -> str:
                if permissions and include_filter_vars:
                    return " AND permissionEdge.role IN @permissions"
                return ""

            # ===== MAIN QUERY (with pagination and filters and file records) =====
            record_filter = build_record_filters(True)
            permission_filter = build_permission_filter(True)

            main_query = f"""
            LET user_from = @user_from
            LET org_id = @org_id
            LET user_key = SPLIT(user_from, '/')[1]

            // Direct user permissions
            LET directKbAccess = (
                FOR kbEdge IN @@permission
                    FILTER kbEdge._from == user_from
                    FILTER kbEdge.type == "USER"
                    FILTER kbEdge.role IN @kb_permissions
                    LET kb = DOCUMENT(kbEdge._to)
                    FILTER kb != null AND kb.orgId == org_id
                    RETURN {{
                        kb_id: kb._key,
                        kb_doc: kb,
                        role: kbEdge.role,
                        access_type: "direct"
                    }}
            )

            // Team-based access: Get KBs with team permissions, find common teams, get highest role
            LET teamKbAccess = (
                // Get KBs with team permissions
                FOR teamKbPerm IN @@permission
                    FILTER teamKbPerm.type == "TEAM"
                    FILTER STARTS_WITH(teamKbPerm._to, "recordGroups/")
                    LET kb = DOCUMENT(teamKbPerm._to)
                    FILTER kb != null AND kb.orgId == org_id
                    LET team_id = SPLIT(teamKbPerm._from, '/')[1]

                    // Check if user is a member of this team
                    LET user_team_perm = FIRST(
                        FOR userTeamPerm IN @@permission
                            FILTER userTeamPerm._from == user_from
                            FILTER userTeamPerm._to == CONCAT('teams/', team_id)
                            FILTER userTeamPerm.type == "USER"
                            RETURN userTeamPerm.role
                    )

                    FILTER user_team_perm != null

                    RETURN {{
                        kb_id: kb._key,
                        kb_doc: kb,
                        role: user_team_perm,
                        access_type: "team"
                    }}
            )

            // Combine direct and team access, prioritizing direct access
            LET allKbAccess = (
                // First add direct access
                FOR access IN directKbAccess
                    RETURN access

                // Then add team access that doesn't already have direct access
                FOR teamAccess IN teamKbAccess
                    LET hasDirect = LENGTH(
                        FOR direct IN directKbAccess
                            FILTER direct.kb_id == teamAccess.kb_id
                            RETURN 1
                    ) > 0
                    FILTER NOT hasDirect
                    RETURN teamAccess
            )

            // KB Records Section - Get records from all accessible KBs
            LET kbRecords = {
                f'''(
                    FOR access IN allKbAccess
                        LET kb = access.kb_doc
                        // Get records that belong directly to the KB
                        FOR belongsEdge IN @@belongs_to_kb
                            FILTER belongsEdge._to == kb._id
                            LET record = DOCUMENT(belongsEdge._from)
                            FILTER record != null
                            FILTER record.isDeleted != true
                            FILTER record.orgId == org_id
                            FILTER record.origin == "UPLOAD"
                            // Only include actual records (not folders)
                            FILTER record.isFile != false
                            {record_filter}
                            RETURN {{
                                record: record,
                                permission: {{ role: access.role, type: "USER" }},
                                kb_id: kb._key,
                                kb_name: kb.groupName
                            }}
                )''' if include_kb_records else '[]'
            }
            // Connector Records Section - Direct connector permissions
            LET connectorRecords = {
                f'''(
                    FOR permissionEdge IN @@permission
                        FILTER permissionEdge._from == user_from
                        FILTER permissionEdge.type == "USER"
                        {permission_filter}
                        LET record = DOCUMENT(permissionEdge._to)
                        FILTER record != null
                        FILTER record.isDeleted != true
                        FILTER record.orgId == org_id
                        FILTER record.origin == "CONNECTOR"
                        {record_filter}
                        RETURN {{
                            record: record,
                            permission: {{ role: permissionEdge.role, type: permissionEdge.type }}
                        }}
                )''' if include_connector_records else '[]'
            }
            LET allRecords = APPEND(kbRecords, connectorRecords)
            FOR item IN allRecords
                LET record = item.record
                SORT record.{sort_by} {sort_order.upper()}
                LIMIT @skip, @limit
                LET fileRecord = FIRST(
                    FOR fileEdge IN @@is_of_type
                        FILTER fileEdge._from == record._id
                        LET file = DOCUMENT(fileEdge._to)
                        FILTER file != null
                        RETURN {{
                            id: file._key,
                            name: file.name,
                            extension: file.extension,
                            mimeType: file.mimeType,
                            sizeInBytes: file.sizeInBytes,
                            isFile: file.isFile,
                            webUrl: file.webUrl
                        }}
                )
                RETURN {{
                    id: record._key,
                    externalRecordId: record.externalRecordId,
                    externalRevisionId: record.externalRevisionId,
                    recordName: record.recordName,
                    recordType: record.recordType,
                    origin: record.origin,
                    connectorName: record.connectorName || "KNOWLEDGE_BASE",
                    indexingStatus: record.indexingStatus,
                    createdAtTimestamp: record.createdAtTimestamp,
                    updatedAtTimestamp: record.updatedAtTimestamp,
                    sourceCreatedAtTimestamp: record.sourceCreatedAtTimestamp,
                    sourceLastModifiedTimestamp: record.sourceLastModifiedTimestamp,
                    orgId: record.orgId,
                    version: record.version,
                    isDeleted: record.isDeleted,
                    deletedByUserId: record.deletedByUserId,
                    isLatestVersion: record.isLatestVersion != null ? record.isLatestVersion : true,
                    webUrl: record.webUrl,
                    fileRecord: fileRecord,
                    permission: {{role: item.permission.role, type: item.permission.type}},
                    kb: {{id: item.kb_id || null, name: item.kb_name || null }}
                }}
            """

            # ===== COUNT QUERY =====
            count_query = f"""
            LET user_from = @user_from
            LET org_id = @org_id
            LET user_key = SPLIT(user_from, '/')[1]

            // Direct user permissions
            LET directKbAccess = (
                FOR kbEdge IN @@permission
                    FILTER kbEdge._from == user_from
                    FILTER kbEdge.type == "USER"
                    FILTER kbEdge.role IN @kb_permissions
                    LET kb = DOCUMENT(kbEdge._to)
                    FILTER kb != null AND kb.orgId == org_id
                    RETURN {{
                        kb_id: kb._key,
                        kb_doc: kb
                    }}
            )

            // Team-based access
            LET teamKbAccess = (
                FOR teamKbPerm IN @@permission
                    FILTER teamKbPerm.type == "TEAM"
                    FILTER STARTS_WITH(teamKbPerm._to, "recordGroups/")
                    LET kb = DOCUMENT(teamKbPerm._to)
                    FILTER kb != null AND kb.orgId == org_id
                    LET team_id = SPLIT(teamKbPerm._from, '/')[1]

                    LET user_team_perm = FIRST(
                        FOR userTeamPerm IN @@permission
                            FILTER userTeamPerm._from == user_from
                            FILTER userTeamPerm._to == CONCAT('teams/', team_id)
                            FILTER userTeamPerm.type == "USER"
                            RETURN 1
                    )

                    FILTER user_team_perm != null

                    RETURN {{
                        kb_id: kb._key,
                        kb_doc: kb
                    }}
            )

            // Combine direct and team access
            LET allKbAccess = (
                FOR access IN directKbAccess
                    RETURN access

                FOR teamAccess IN teamKbAccess
                    LET hasDirect = LENGTH(
                        FOR direct IN directKbAccess
                            FILTER direct.kb_id == teamAccess.kb_id
                            RETURN 1
                    ) > 0
                    FILTER NOT hasDirect
                    RETURN teamAccess
            )

            LET kbCount = {
                f'''LENGTH(
                    FOR access IN allKbAccess
                        LET kb = access.kb_doc
                        FOR belongsEdge IN @@belongs_to_kb
                            FILTER belongsEdge._to == kb._id
                            LET record = DOCUMENT(belongsEdge._from)
                            FILTER record != null
                            FILTER record.isDeleted != true
                            FILTER record.orgId == org_id
                            FILTER record.origin == "UPLOAD"
                            FILTER record.isFile != false
                            {record_filter}
                            RETURN 1
                )''' if include_kb_records else '0'
            }
            LET connectorCount = {
                f'''LENGTH(
                    FOR permissionEdge IN @@permission
                        FILTER permissionEdge._from == user_from
                        FILTER permissionEdge.type == "USER"
                        {permission_filter}
                        LET record = DOCUMENT(permissionEdge._to)
                        FILTER record != null
                        FILTER record.isDeleted != true
                        FILTER record.orgId == org_id
                        FILTER record.origin == "CONNECTOR"
                        {record_filter}
                        RETURN 1
                )''' if include_connector_records else '0'
            }
            RETURN kbCount + connectorCount
            """

            # ===== FILTERS QUERY =====
            filters_query = f"""
            LET user_from = @user_from
            LET org_id = @org_id
            LET user_key = SPLIT(user_from, '/')[1]

            // Direct user permissions
            LET directKbAccess = (
                FOR kbEdge IN @@permission
                    FILTER kbEdge._from == user_from
                    FILTER kbEdge.type == "USER"
                    FILTER kbEdge.role IN ["OWNER", "READER", "FILEORGANIZER", "WRITER", "COMMENTER", "ORGANIZER"]
                    LET kb = DOCUMENT(kbEdge._to)
                    FILTER kb != null AND kb.orgId == org_id
                    RETURN {{
                        kb_id: kb._key,
                        kb_doc: kb,
                        role: kbEdge.role
                    }}
            )

            // Team-based access
            LET teamKbAccess = (
                FOR teamKbPerm IN @@permission
                    FILTER teamKbPerm.type == "TEAM"
                    FILTER STARTS_WITH(teamKbPerm._to, "recordGroups/")
                    LET kb = DOCUMENT(teamKbPerm._to)
                    FILTER kb != null AND kb.orgId == org_id
                    LET team_id = SPLIT(teamKbPerm._from, '/')[1]

                    LET user_team_perm = FIRST(
                        FOR userTeamPerm IN @@permission
                            FILTER userTeamPerm._from == user_from
                            FILTER userTeamPerm._to == CONCAT('teams/', team_id)
                            FILTER userTeamPerm.type == "USER"
                            RETURN userTeamPerm.role
                    )

                    FILTER user_team_perm != null

                    RETURN {{
                        kb_id: kb._key,
                        kb_doc: kb,
                        role: user_team_perm
                    }}
            )

            // Combine direct and team access
            LET allKbAccess = (
                FOR access IN directKbAccess
                    RETURN access

                FOR teamAccess IN teamKbAccess
                    LET hasDirect = LENGTH(
                        FOR direct IN directKbAccess
                            FILTER direct.kb_id == teamAccess.kb_id
                            RETURN 1
                    ) > 0
                    FILTER NOT hasDirect
                    RETURN teamAccess
            )

            LET allKbRecords = {
                '''(
                    FOR access IN allKbAccess
                        LET kb = access.kb_doc
                        FOR belongsEdge IN @@belongs_to_kb
                            FILTER belongsEdge._to == kb._id
                            LET record = DOCUMENT(belongsEdge._from)
                            FILTER record != null
                            FILTER record.isDeleted != true
                            FILTER record.orgId == org_id
                            FILTER record.origin == "UPLOAD"
                            FILTER record.isFile != false
                            RETURN {{
                                record: record,
                                permission: {{ role: access.role }}
                            }}
                )''' if include_kb_records else '[]'
            }
            LET allConnectorRecords = {
                '''(
                    FOR permissionEdge IN @@permission
                        FILTER permissionEdge._from == user_from
                        FILTER permissionEdge.type == "USER"
                        LET record = DOCUMENT(permissionEdge._to)
                        FILTER record != null
                        FILTER record.isDeleted != true
                        FILTER record.orgId == org_id
                        FILTER record.origin == "CONNECTOR"
                        RETURN {
                            record: record,
                            permission: { role: permissionEdge.role }
                        }
                )''' if include_connector_records else '[]'
            }
            LET allRecords = APPEND(allKbRecords, allConnectorRecords)
            LET flatRecords = (
                FOR item IN allRecords
                    RETURN item.record
            )
            LET permissionValues = (
                FOR item IN allRecords
                    FILTER item.permission != null
                    RETURN item.permission.role
            )
            LET connectorValues = (
                FOR record IN flatRecords
                    FILTER record.connectorName != null
                    RETURN record.connectorName
            )
            RETURN {{
                recordTypes: UNIQUE(flatRecords[*].recordType) || [],
                origins: UNIQUE(flatRecords[*].origin) || [],
                connectors: UNIQUE(connectorValues) || [],
                indexingStatus: UNIQUE(flatRecords[*].indexingStatus) || [],
                permissions: UNIQUE(permissionValues) || []
            }}
            """

            # Build bind variables
            filter_bind_vars = {}
            if search:
                filter_bind_vars["search"] = f"%{search.lower()}%"
            if record_types:
                filter_bind_vars["record_types"] = record_types
            if origins:
                filter_bind_vars["origins"] = origins
            if connectors:
                filter_bind_vars["connectors"] = connectors
            if indexing_status:
                filter_bind_vars["indexing_status"] = indexing_status
            if permissions:
                filter_bind_vars["permissions"] = permissions
            if date_from:
                filter_bind_vars["date_from"] = date_from
            if date_to:
                filter_bind_vars["date_to"] = date_to

            main_bind_vars = {
                "user_from": f"users/{user_id}",
                "org_id": org_id,
                "skip": skip,
                "limit": limit,
                "kb_permissions": final_kb_roles,
                "@permission": CollectionNames.PERMISSION.value,
                "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                **filter_bind_vars,
            }

            count_bind_vars = {
                "user_from": f"users/{user_id}",
                "org_id": org_id,
                "kb_permissions": final_kb_roles,
                "@permission": CollectionNames.PERMISSION.value,
                "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                **filter_bind_vars,
            }

            filters_bind_vars = {
                "user_from": f"users/{user_id}",
                "org_id": org_id,
                "@permission": CollectionNames.PERMISSION.value,
                "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
            }

            # Execute queries
            db = self.db
            records = list(db.aql.execute(main_query, bind_vars=main_bind_vars))
            count = list(db.aql.execute(count_query, bind_vars=count_bind_vars))[0]
            available_filters = list(db.aql.execute(filters_query, bind_vars=filters_bind_vars))[0]

            # Ensure filter structure
            if not available_filters:
                available_filters = {}
            available_filters.setdefault("recordTypes", [])
            available_filters.setdefault("origins", [])
            available_filters.setdefault("connectors", [])
            available_filters.setdefault("indexingStatus", [])
            available_filters.setdefault("permissions", [])

            self.logger.info(f"✅ Listed {len(records)} records out of {count} total")
            return records, count, available_filters

        except Exception as e:
            self.logger.error(f"❌ Failed to list all records: {str(e)}")
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
        search: Optional[str],
        record_types: Optional[List[str]],
        origins: Optional[List[str]],
        connectors: Optional[List[str]],
        indexing_status: Optional[List[str]],
        date_from: Optional[int],
        date_to: Optional[int],
        sort_by: str,
        sort_order: str,
        folder_id: Optional[str] = None,  # Add folder filter parameter
    ) -> Tuple[List[Dict], int, Dict]:
        """
        List all records in a specific KB through folder structure for better folder-based filtering.
        """
        try:
            self.logger.info(f"🔍 Listing records for KB {kb_id} (folder-based)")

            db = self.db

            # Check user permissions first (includes team-based access)
            user_permission = await self.get_user_kb_permission(kb_id, user_id, transaction=db)
            if not user_permission:
                self.logger.warning(f"⚠️ User {user_id} has no access to KB {kb_id} (neither direct nor via teams)")
                return [], 0, {
                    "recordTypes": [],
                    "origins": [],
                    "connectors": [],
                    "indexingStatus": [],
                    "permissions": []
                }

            # Build filter conditions
            def build_record_filters(include_filter_vars: bool = True) -> str:
                conditions = []
                if search and include_filter_vars:
                    conditions.append("(LIKE(LOWER(record.recordName), @search) OR LIKE(LOWER(record.externalRecordId), @search))")
                if record_types and include_filter_vars:
                    conditions.append("record.recordType IN @record_types")
                if origins and include_filter_vars:
                    conditions.append("record.origin IN @origins")
                if connectors and include_filter_vars:
                    conditions.append("record.connectorName IN @connectors")
                if indexing_status and include_filter_vars:
                    conditions.append("record.indexingStatus IN @indexing_status")
                if date_from and include_filter_vars:
                    conditions.append("record.createdAtTimestamp >= @date_from")
                if date_to and include_filter_vars:
                    conditions.append("record.createdAtTimestamp <= @date_to")

                return " AND " + " AND ".join(conditions) if conditions else ""

            def build_folder_filter(include_filter_vars: bool = True) -> str:
                if folder_id and include_filter_vars:
                    return " AND folder._key == @folder_id"
                return ""

            # ===== MAIN QUERY =====
            record_filter = build_record_filters(True)
            folder_filter = build_folder_filter(True)

            # Optimized query using graph traversal and batch fetching
            # Recommended indexes:
            # - belongs_to edges: [ "_to" ] (persistent index)
            # - record_relations edges: [ "_from", "relationshipType" ] (persistent index)
            # - is_of_type edges: [ "_from" ] (persistent index)
            # - records collection: [ "orgId", "isDeleted", "isFile" ] (persistent index)
            main_query = f"""
            LET kb = DOCUMENT("recordGroups", @kb_id)
            FILTER kb != null
            LET user_permission = @user_permission

            // Get all folders in the KB (optimized with early filtering)
            LET kbFolders = (
                FOR belongsEdge IN @@belongs_to_kb
                    FILTER belongsEdge._to == kb._id
                    LET folder = DOCUMENT(belongsEdge._from)
                    FILTER folder != null
                    FILTER folder.isFile == false
                    {folder_filter}
                    RETURN {{
                        folder: folder,
                        folder_id: folder._key,
                        folder_name: folder.name
                    }}
            )

            // Batch fetch all folder IDs
            LET folder_ids = kbFolders[*].folder._id

            // Get all records from folders via PARENT_CHILD relationships (optimized)
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
                    // Find which folder this record belongs to
                    LET folder_info = FIRST(
                        FOR f IN kbFolders
                            FILTER f.folder._id == relEdge._from
                            RETURN f
                    )
                    RETURN {{
                        record: record,
                        folder_id: folder_info.folder_id,
                        folder_name: folder_info.folder_name,
                        permission: {{ role: user_permission, type: "USER" }},
                        kb_id: @kb_id
                    }}
            )

            // Batch fetch all record IDs for file lookups
            LET record_ids = all_records_data[*].record._id

            // Batch fetch all file records
            LET all_files = (
                FOR fileEdge IN @@is_of_type
                    FILTER fileEdge._from IN record_ids
                    LET file = DOCUMENT(fileEdge._to)
                    FILTER file != null
                    RETURN {{
                        record_id: fileEdge._from,
                        file: {{
                            id: file._key,
                            name: file.name,
                            extension: file.extension,
                            mimeType: file.mimeType,
                            sizeInBytes: file.sizeInBytes,
                            isFile: file.isFile,
                            webUrl: file.webUrl
                        }}
                    }}
            )

            // Build final result with files joined
            FOR item IN all_records_data
                LET record = item.record
                LET fileRecord = FIRST(
                    FOR f IN all_files
                        FILTER f.record_id == record._id
                        RETURN f.file
                )
                SORT record.{sort_by} {sort_order.upper()}
                LIMIT @skip, @limit
                RETURN {{
                    id: record._key,
                    externalRecordId: record.externalRecordId,
                    externalRevisionId: record.externalRevisionId,
                    recordName: record.recordName,
                    recordType: record.recordType,
                    origin: record.origin,
                    connectorName: record.connectorName || "KNOWLEDGE_BASE",
                    indexingStatus: record.indexingStatus,
                    createdAtTimestamp: record.createdAtTimestamp,
                    updatedAtTimestamp: record.updatedAtTimestamp,
                    sourceCreatedAtTimestamp: record.sourceCreatedAtTimestamp,
                    sourceLastModifiedTimestamp: record.sourceLastModifiedTimestamp,
                    orgId: record.orgId,
                    version: record.version,
                    isDeleted: record.isDeleted,
                    deletedByUserId: record.deletedByUserId,
                    isLatestVersion: record.isLatestVersion != null ? record.isLatestVersion : true,
                    webUrl: record.webUrl,
                    fileRecord: fileRecord,
                    permission: {{role: item.permission.role, type: item.permission.type}},
                    kb_id: item.kb_id,
                    folder: {{id: item.folder_id, name: item.folder_name}},
                }}
            """

            # ===== OPTIMIZED COUNT QUERY =====
            count_query = f"""
            LET kb = DOCUMENT("recordGroups", @kb_id)
            FILTER kb != null

            // Get folder IDs only (no need to fetch full documents)
            LET folder_ids = (
                FOR belongsEdge IN @@belongs_to_kb
                    FILTER belongsEdge._to == kb._id
                    LET folder = DOCUMENT(belongsEdge._from)
                    FILTER folder != null
                    FILTER folder.isFile == false
                    {folder_filter}
                    RETURN belongsEdge._from
            )

            // Count records directly without fetching documents until needed
            LET record_count = (
                FOR relEdge IN @@record_relations
                    FILTER relEdge._from IN folder_ids
                    FILTER relEdge.relationshipType == "PARENT_CHILD"
                    LET record = DOCUMENT(relEdge._to)
                    FILTER record != null
                    FILTER record.isDeleted != true
                    FILTER record.orgId == @org_id
                    FILTER record.isFile != false
                    {record_filter}
                    COLLECT WITH COUNT INTO count
                    RETURN count
            )

            RETURN FIRST(record_count) || 0
            """

            # ===== OPTIMIZED FILTERS QUERY =====
            filters_query = """
            LET kb = DOCUMENT("recordGroups", @kb_id)
            FILTER kb != null
            LET user_permission = @user_permission

            // Get folder IDs only
            LET folder_ids = (
                FOR belongsEdge IN @@belongs_to_kb
                    FILTER belongsEdge._to == kb._id
                    LET folder = DOCUMENT(belongsEdge._from)
                    FILTER folder != null
                    FILTER folder.isFile == false
                    RETURN belongsEdge._from
            )

            // Get all records (optimized with batch fetching)
            LET allRecords = (
                FOR relEdge IN @@record_relations
                    FILTER relEdge._from IN folder_ids
                    FILTER relEdge.relationshipType == "PARENT_CHILD"
                    LET record = DOCUMENT(relEdge._to)
                    FILTER record != null
                    FILTER record.isDeleted != true
                    FILTER record.orgId == @org_id
                    FILTER record.isFile != false
                    RETURN record
            )
            LET connectorValues = (
                FOR record IN allRecords
                    FILTER record.connectorName != null
                    RETURN record.connectorName
            )
            // Get available folders for filtering
            LET availableFolders = (
                FOR folder IN kbFolders
                    RETURN {
                        id: folder._key,
                        name: folder.name
                    }
            )
            RETURN {
                recordTypes: UNIQUE(allRecords[*].recordType) || [],
                origins: UNIQUE(allRecords[*].origin) || [],
                connectors: UNIQUE(connectorValues) || [],
                indexingStatus: UNIQUE(allRecords[*].indexingStatus) || [],
                permissions: [user_permission] || [],
                folders: availableFolders || []
            }
            """

            # Build bind variables
            filter_bind_vars = {}
            if search:
                filter_bind_vars["search"] = f"%{search.lower()}%"
            if record_types:
                filter_bind_vars["record_types"] = record_types
            if origins:
                filter_bind_vars["origins"] = origins
            if connectors:
                filter_bind_vars["connectors"] = connectors
            if indexing_status:
                filter_bind_vars["indexing_status"] = indexing_status
            if date_from:
                filter_bind_vars["date_from"] = date_from
            if date_to:
                filter_bind_vars["date_to"] = date_to
            if folder_id:
                filter_bind_vars["folder_id"] = folder_id

            main_bind_vars = {
                "kb_id": kb_id,
                "org_id": org_id,
                "user_permission": user_permission,
                "skip": skip,
                "limit": limit,
                "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                **filter_bind_vars,
            }

            count_bind_vars = {
                "kb_id": kb_id,
                "org_id": org_id,
                "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                **filter_bind_vars,
            }

            filters_bind_vars = {
                "kb_id": kb_id,
                "org_id": org_id,
                "user_permission": user_permission,
                "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                "@record_relations": CollectionNames.RECORD_RELATIONS.value,
            }

            # Execute queries
            records = list(db.aql.execute(main_query, bind_vars=main_bind_vars))
            count = list(db.aql.execute(count_query, bind_vars=count_bind_vars))[0]
            available_filters = list(db.aql.execute(filters_query, bind_vars=filters_bind_vars))[0]

            # Ensure filter structure
            if not available_filters:
                available_filters = {}
            available_filters.setdefault("recordTypes", [])
            available_filters.setdefault("origins", [])
            available_filters.setdefault("connectors", [])
            available_filters.setdefault("indexingStatus", [])
            available_filters.setdefault("permissions", [user_permission] if user_permission else [])
            available_filters.setdefault("folders", [])

            self.logger.info(f"✅ Listed {len(records)} KB records out of {count} total")
            return records, count, available_filters

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
        transaction: Optional[TransactionDatabase] = None
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
            db = transaction if transaction else self.db

            # Build filter conditions
            def build_filters() -> Tuple[str, str, Dict]:
                folder_conditions = []
                record_conditions = []
                bind_vars = {}

                if search:
                    folder_conditions.append("LIKE(LOWER(folder_record.recordName), @search_term)")
                    record_conditions.append("(LIKE(LOWER(record.recordName), @search_term) OR LIKE(LOWER(record.externalRecordId), @search_term))")
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
                    record_conditions.append("record.indexingStatus IN @indexing_status")
                    bind_vars["indexing_status"] = indexing_status

                folder_filter = " AND " + " AND ".join(folder_conditions) if folder_conditions else ""
                record_filter = " AND " + " AND ".join(record_conditions) if record_conditions else ""

                return folder_filter, record_filter, bind_vars

            folder_filter, record_filter, filter_vars = build_filters()

            # Sort field mapping for records (folders always sorted by name)
            record_sort_map = {
                "name": "record.recordName",
                "created_at": "record.createdAtTimestamp",
                "updated_at": "record.updatedAtTimestamp",
                "size": "fileRecord.sizeInBytes"
            }
            record_sort_field = record_sort_map.get(sort_by, "record.recordName")
            sort_direction = sort_order.upper()

            main_query = f"""
            LET kb = DOCUMENT("recordGroups", @kb_id)
            FILTER kb != null
            // Get immediate children of KB using belongs_to edges
            // A record is an immediate child if:
            // 1. It has a belongs_to edge TO KB record group (record belongs to KB)
            // 2. It is NOT a parent of any other record (no outgoing record_relations edge)
            LET allImmediateChildren = (
                FOR belongsEdge IN @@belongs_to
                    FILTER belongsEdge._to == kb._id
                    FILTER belongsEdge.entityType == @kb_connector_type
                    LET record = DOCUMENT(belongsEdge._from)
                    FILTER IS_SAME_COLLECTION("records", record._id)
                    FILTER record != null
                    FILTER record.isDeleted != true
                    // Check if this record is a child of any other record
                    LET isChild = LENGTH(
                        FOR relEdge IN @@record_relations
                            FILTER relEdge._to == record._id
                            FILTER relEdge.relationshipType == "PARENT_CHILD"
                            RETURN 1
                    ) > 0
                    // Only include if NOT a parent (immediate child)
                    FILTER isChild == false
                    RETURN record
            )
            // Separate folders and records from immediate children
            // Folders are identified by FILES document's isFile == false
            LET allFolders = (
                FOR record IN allImmediateChildren
                    // Get associated FILES document via IS_OF_TYPE edge
                    // Check if it's a folder based on FILES document's isFile property
                    LET folder_file = FIRST(
                        FOR isEdge IN @@is_of_type
                            FILTER isEdge._from == record._id
                            LET f = DOCUMENT(isEdge._to)
                            FILTER f != null AND f.isFile == false
                            RETURN f
                    )
                    // Only include if it's a folder (isFile == false)
                    FILTER folder_file != null
                    {folder_filter}
                    // Get counts for this folder (check child RECORDS via record_relations)
                    LET direct_subfolders = LENGTH(
                        FOR relEdge IN @@record_relations
                            FILTER relEdge._from == record._id
                            FILTER relEdge.relationshipType == "PARENT_CHILD"
                            LET child_record = DOCUMENT(relEdge._to)
                            FILTER child_record != null
                            // Check if it's a folder by verifying FILES document's isFile == false
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
                            // Exclude folders by checking FILES document's isFile property
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
            // Get ALL records directly in KB root (excluding folders)
            // Folders are identified by FILES document's isFile == false
            LET allRecords = (
                FOR record IN allImmediateChildren
                    // Exclude folders by checking FILES document's isFile property
                    LET record_file = FIRST(
                        FOR isEdge IN @@is_of_type
                            FILTER isEdge._from == record._id
                            LET f = DOCUMENT(isEdge._to)
                            FILTER f != null AND f.isFile == false
                            RETURN 1
                    )
                    FILTER record_file == null
                    {record_filter}
                    // Get associated file record
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
            // Pagination Logic for Folders First:
            // 1. If skip < totalFolders: Show folders from skip position
            // 2. Fill remaining limit with records
            // 3. If skip >= totalFolders: Show only records (skip folders entirely)
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
            // Available filter values from all records (not just paginated)
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

            bind_vars = {
                "kb_id": kb_id,
                "skip": skip,
                "limit": limit,
                "level": level,
                "kb_connector_type": Connectors.KNOWLEDGE_BASE.value,
                "@belongs_to": CollectionNames.BELONGS_TO.value,
                "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                **filter_vars
            }

            cursor = db.aql.execute(main_query, bind_vars=bind_vars)
            result = next(cursor, None)

            if not result:
                return {"success": False, "reason": "Knowledge base not found"}

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
        transaction: Optional[TransactionDatabase] = None
    ) -> Dict:
        """
        Get folder contents with folders_first pagination and level order traversal
        """
        try:
            db = transaction if transaction else self.db

            # Build filter conditions (same as KB version)
            def build_filters() -> Tuple[str, str, Dict]:
                folder_conditions = []
                record_conditions = []
                bind_vars = {}

                if search:
                    folder_conditions.append("LIKE(LOWER(subfolder_record.recordName), @search_term)")
                    record_conditions.append("(LIKE(LOWER(record.recordName), @search_term) OR LIKE(LOWER(record.externalRecordId), @search_term))")
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
                    record_conditions.append("record.indexingStatus IN @indexing_status")
                    bind_vars["indexing_status"] = indexing_status

                folder_filter = " AND " + " AND ".join(folder_conditions) if folder_conditions else ""
                record_filter = " AND " + " AND ".join(record_conditions) if record_conditions else ""

                return folder_filter, record_filter, bind_vars

            folder_filter, record_filter, filter_vars = build_filters()

            # Sort field mapping for records
            record_sort_map = {
                "name": "record.recordName",
                "created_at": "record.createdAtTimestamp",
                "updated_at": "record.updatedAtTimestamp",
                "size": "fileRecord.sizeInBytes"
            }
            record_sort_field = record_sort_map.get(sort_by, "record.recordName")
            sort_direction = sort_order.upper()

            main_query = f"""
            // Get folder from RECORDS collection
            // Folders are identified by FILES document's isFile == false, not by recordType
            LET folder_record = DOCUMENT("records", @folder_id)
            FILTER folder_record != null
            // Get folder file metadata via IS_OF_TYPE edge
            // Verify it's a folder by checking FILES document's isFile property
            LET folder_file = FIRST(
                FOR isEdge IN @@is_of_type
                    FILTER isEdge._from == folder_record._id
                    LET f = DOCUMENT(isEdge._to)
                    FILTER f != null AND f.isFile == false
                    RETURN f
            )
            // Only proceed if it's actually a folder (isFile == false)
            FILTER folder_file != null
            // Get ALL subfolders with level traversal
            // Folders are identified by FILES document's isFile == false, not by recordType
            LET allSubfolders = (
                FOR v, e, p IN 1..@level OUTBOUND folder_record._id @@record_relations
                    FILTER e.relationshipType == "PARENT_CHILD"
                    LET subfolder_record = v
                    // Get subfolder file via IS_OF_TYPE
                    // Check if it's a folder based on FILES document's isFile property
                    LET subfolder_file = FIRST(
                        FOR isEdge IN @@is_of_type
                            FILTER isEdge._from == subfolder_record._id
                            LET f = DOCUMENT(isEdge._to)
                            FILTER f != null AND f.isFile == false
                            RETURN f
                    )
                    // Only include if it's a folder (isFile == false)
                    FILTER subfolder_file != null
                    LET current_level = LENGTH(p.edges)
                    {folder_filter}
                    LET direct_subfolders = LENGTH(
                        FOR relEdge IN @@record_relations
                            FILTER relEdge._from == subfolder_record._id
                            FILTER relEdge.relationshipType == "PARENT_CHILD"
                            LET child_record = DOCUMENT(relEdge._to)
                            FILTER child_record != null
                            // Check if it's a folder by verifying FILES document's isFile == false
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
                            // Exclude folders by checking FILES document's isFile property
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
            // Get ALL records in this folder (excluding folders)
            // Folders are identified by FILES document's isFile == false
            LET allRecords = (
                FOR edge IN @@record_relations
                    FILTER edge._from == folder_record._id
                    FILTER edge.relationshipType == "PARENT_CHILD"
                    LET record = DOCUMENT(edge._to)
                    FILTER record != null
                    FILTER record.isDeleted != true
                    // Exclude folders by checking FILES document's isFile property
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
            // Folders First Pagination Logic
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
                available_filters: availableFilters,
                pagination_mode: "folders_first"
            }}
            """

            bind_vars = {
                "folder_id": folder_id,
                "skip": skip,
                "limit": limit,
                "level": level,
                "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                **filter_vars
            }

            cursor = db.aql.execute(main_query, bind_vars=bind_vars)
            result = next(cursor, None)

            if not result:
                return {"success": False, "reason": "Folder not found"}

            self.logger.info(f"✅ Retrieved folder children with folders_first pagination: {result['counts']['totalItems']} items")
            return result

        except Exception as e:
            self.logger.error(f"❌ Failed to get folder children with folders_first pagination: {str(e)}")
            return {"success": False, "reason": str(e)}

    async def delete_knowledge_base(
        self,
        kb_id: str,
        transaction: Optional[TransactionDatabase] = None,
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
                    transaction = self.db.begin_transaction(
                        write=[
                            CollectionNames.RECORD_GROUPS.value,
                            CollectionNames.FILES.value,
                            CollectionNames.RECORDS.value,
                            CollectionNames.RECORD_RELATIONS.value,
                            CollectionNames.BELONGS_TO.value,
                            CollectionNames.IS_OF_TYPE.value,
                            CollectionNames.PERMISSION.value,
                        ]
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

                cursor = transaction.aql.execute(inventory_query, bind_vars={
                    "kb_id": kb_id,
                    "@records_collection": CollectionNames.RECORDS.value,
                    "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                    "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                })

                inventory = next(cursor, {})

                if not inventory.get("kb_exists"):
                    self.logger.warning(f"⚠️ KB {kb_id} not found, deletion considered successful.")
                    if should_commit:
                        # No need to abort, just commit the empty transaction
                        await asyncio.to_thread(lambda: transaction.commit_transaction())
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
                cursor = transaction.aql.execute(edges_cleanup_query, bind_vars={
                    "kb_id": kb_id,
                    "record_keys": all_record_keys,
                    "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                    "@permission": CollectionNames.PERMISSION.value,
                    "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                    "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                })
                # Fully consume the cursor to release the transaction lock
                results = list(cursor)
                edge_deletion_result = results[0] if results else {}

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
                cursor = transaction.aql.execute(
                    "REMOVE @kb_id IN @@recordGroups_collection OPTIONS { ignoreErrors: true } RETURN OLD",
                    bind_vars={
                        "kb_id": kb_id,
                        "@recordGroups_collection": CollectionNames.RECORD_GROUPS.value
                    }
                )
                list(cursor)  # Consume cursor to ensure query completes and locks are released
                # Step 6: Commit transaction
                if should_commit:
                    self.logger.info("💾 Committing complete deletion transaction...")
                    await asyncio.to_thread(lambda: transaction.commit_transaction())
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
                    await asyncio.to_thread(lambda: transaction.abort_transaction())
                    self.logger.info("🔄 Transaction aborted due to error")
                raise db_error

        except Exception as e:
            self.logger.error(f"❌ Failed to delete KB {kb_id} completely: {str(e)}")
            return False

    async def delete_folder(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[TransactionDatabase] = None,
    ) -> bool:
        """
        Delete a folder with ALL nested content and publish delete events for all records
        Fixed to handle edge deletion properly and avoid "document not found" errors
        """
        try:
            # Create transaction if not provided
            should_commit = False
            if transaction is None:
                should_commit = True
                try:
                    transaction = self.db.begin_transaction(
                        write=[
                            CollectionNames.FILES.value,
                            CollectionNames.RECORDS.value,
                            CollectionNames.RECORD_RELATIONS.value,
                            CollectionNames.BELONGS_TO.value,
                            CollectionNames.IS_OF_TYPE.value,
                        ]
                    )
                    self.logger.info(f"🔄 Transaction created for complete folder {folder_id} deletion")
                except Exception as tx_error:
                    self.logger.error(f"❌ Failed to create transaction: {str(tx_error)}")
                    return False

            try:
                # Step 1: Get complete inventory including all records for event publishing
                # Folders are now represented by RECORDS documents
                inventory_query = """
                LET target_folder_record = DOCUMENT("records", @folder_id)
                FILTER target_folder_record != null
                // Verify it's a folder by checking associated FILES document
                LET target_folder_file = FIRST(
                    FOR isEdge IN @@is_of_type
                        FILTER isEdge._from == target_folder_record._id
                        LET f = DOCUMENT(isEdge._to)
                        FILTER f != null AND f.isFile == false
                        RETURN f
                )
                FILTER target_folder_file != null
                // Get ALL subfolders recursively using traversal from RECORDS document
                LET all_subfolders = (
                    FOR v, e, p IN 1..20 OUTBOUND target_folder_record._id @@record_relations
                        FILTER e.relationshipType == "PARENT_CHILD"
                        // Verify v is a folder by checking its FILES document
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
                // All folders to delete (target + subfolders) - these are RECORDS document keys
                LET all_folders = APPEND([target_folder_record._key], all_subfolders)
                // Get ALL records recursively from target folder and all subfolders using graph traversal
                // First collect all vertices (both folders and records), then filter to get only records
                LET all_folder_records_with_details = (
                    // Traverse from target folder to get ALL nested vertices (folders and records)
                    FOR v, e, p IN 1..20 OUTBOUND target_folder_record._id @@record_relations
                        FILTER e.relationshipType == "PARENT_CHILD"
                        LET vertex = v
                        FILTER vertex != null
                        // Check if this vertex is a folder or a record by checking its FILES document
                        LET vertex_file = FIRST(
                            FOR isEdge IN @@is_of_type
                                FILTER isEdge._from == vertex._id
                                LET f = DOCUMENT(isEdge._to)
                                FILTER f != null
                                RETURN f
                        )
                        // Only include if it's a record (has FILES document with isFile == true)
                        // Exclude folders (which have isFile == false)
                        FILTER vertex_file != null AND vertex_file.isFile == true
                        RETURN {
                            record: vertex,
                            file_record: vertex_file
                        }
                )
                // Get file records associated with all these records
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

                cursor = transaction.aql.execute(inventory_query, bind_vars={
                    "folder_id": folder_id,
                    "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                    "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                })

                inventory = next(cursor, {})

                if not inventory.get("folder_exists"):
                    self.logger.warning(f"⚠️ Folder {folder_id} not found in KB {kb_id}")
                    if should_commit:
                        await asyncio.to_thread(lambda: transaction.abort_transaction())
                    return False

                records_with_details = inventory.get("records_with_details", [])
                self.logger.info(f"📋 Folder {folder_id} deletion inventory:")
                self.logger.info(f"   📁 Total folders (including target): {inventory['total_folders']}")
                self.logger.info(f"   📄 Records: {inventory['total_records']}")
                self.logger.info(f"   🗂️ File records: {inventory['total_file_records']}")

                # Step 2: Delete ALL edges in separate queries to avoid "access after data-modification" error
                self.logger.info("🗑️ Step 1: Deleting all edges...")

                all_record_keys = [rd["record"]["_key"] for rd in records_with_details]

                # Delete record relation edges
                if all_record_keys or inventory["all_folders"]:
                    record_relations_delete = """
                    LET record_edges = (
                        FOR record_key IN @all_records
                            FOR rec_edge IN @@record_relations
                                FILTER rec_edge._from == CONCAT('records/', record_key) OR rec_edge._to == CONCAT('records/', record_key)
                                RETURN rec_edge._key
                    )
                    LET folder_edges = (
                        FOR folder_key IN @all_folders
                            FOR folder_edge IN @@record_relations
                                // Folders are now RECORDS documents, so edges are from/to records/{folder_key}
                                FILTER folder_edge._from == CONCAT('records/', folder_key) OR folder_edge._to == CONCAT('records/', folder_key)
                                RETURN folder_edge._key
                    )
                    LET all_relation_edges = APPEND(record_edges, folder_edges)
                    FOR edge_key IN all_relation_edges
                        REMOVE edge_key IN @@record_relations OPTIONS { ignoreErrors: true }
                    """

                    transaction.aql.execute(record_relations_delete, bind_vars={
                        "all_records": all_record_keys,
                        "all_folders": inventory["all_folders"],
                        "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                    })
                    self.logger.info("✅ Deleted record relation edges")

                # Delete is_of_type edges for both records and folders
                if all_record_keys or inventory["all_folders"]:
                    is_of_type_delete = """
                    LET record_type_edges = (
                        FOR record_key IN @all_records
                            FOR type_edge IN @@is_of_type
                                FILTER type_edge._from == CONCAT('records/', record_key)
                                RETURN type_edge._key
                    )
                    LET folder_type_edges = (
                        FOR folder_key IN @all_folders
                            FOR type_edge IN @@is_of_type
                                FILTER type_edge._from == CONCAT('records/', folder_key)
                                RETURN type_edge._key
                    )
                    LET all_type_edges = APPEND(record_type_edges, folder_type_edges)
                    FOR edge_key IN all_type_edges
                        REMOVE edge_key IN @@is_of_type OPTIONS { ignoreErrors: true }
                    """

                    transaction.aql.execute(is_of_type_delete, bind_vars={
                        "all_records": all_record_keys,
                        "all_folders": inventory["all_folders"],
                        "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                    })
                    self.logger.info("✅ Deleted is_of_type edges")

                # Delete belongs_to_kb edges
                if all_record_keys or inventory["all_folders"]:
                    belongs_to_kb_delete = """
                    LET record_kb_edges = (
                        FOR record_key IN @all_records
                            FOR record_kb_edge IN @@belongs_to_kb
                                FILTER record_kb_edge._from == CONCAT('records/', record_key)
                                RETURN record_kb_edge._key
                    )
                    LET folder_kb_edges = (
                        FOR folder_key IN @all_folders
                            FOR folder_kb_edge IN @@belongs_to_kb
                                // Folders are now RECORDS documents, so edges are from records/{folder_key}
                                FILTER folder_kb_edge._from == CONCAT('records/', folder_key)
                                RETURN folder_kb_edge._key
                    )
                    LET all_kb_edges = APPEND(record_kb_edges, folder_kb_edges)
                    FOR edge_key IN all_kb_edges
                        REMOVE edge_key IN @@belongs_to_kb OPTIONS { ignoreErrors: true }
                    """

                    transaction.aql.execute(belongs_to_kb_delete, bind_vars={
                        "all_records": all_record_keys,
                        "all_folders": inventory["all_folders"],
                        "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                    })
                    self.logger.info("✅ Deleted belongs_to_kb edges")

                # Step 3: Delete all file records with error handling
                if inventory["file_records"]:
                    self.logger.info(f"🗑️ Step 2: Deleting {len(inventory['file_records'])} file records...")

                    file_records_delete_query = """
                    FOR file_key IN @file_keys
                        REMOVE file_key IN @@files_collection OPTIONS { ignoreErrors: true }
                    """

                    transaction.aql.execute(file_records_delete_query, bind_vars={
                        "file_keys": inventory["file_records"],
                        "@files_collection": CollectionNames.FILES.value,
                    })

                    self.logger.info(f"✅ Deleted {len(inventory['file_records'])} file records")

                # Step 4: Delete all records with error handling
                if all_record_keys:
                    self.logger.info(f"🗑️ Step 3: Deleting {len(all_record_keys)} records...")

                    records_delete_query = """
                    FOR record_key IN @record_keys
                        REMOVE record_key IN @@records_collection OPTIONS { ignoreErrors: true }
                    """

                    transaction.aql.execute(records_delete_query, bind_vars={
                        "record_keys": all_record_keys,
                        "@records_collection": CollectionNames.RECORDS.value,
                    })

                    self.logger.info(f"✅ Deleted {len(all_record_keys)} records")

                # Step 5: Delete all folder RECORDS documents and their associated FILES documents
                if inventory["all_folders"]:
                    self.logger.info(f"🗑️ Step 4: Deleting {len(inventory['all_folders'])} folders...")

                    # First, get all folder FILES document keys
                    folder_files_query = """
                    FOR folder_key IN @folder_keys
                        LET folder_record = DOCUMENT("records", folder_key)
                        FILTER folder_record != null
                        LET folder_file = FIRST(
                            FOR isEdge IN @@is_of_type
                                FILTER isEdge._from == folder_record._id
                                LET f = DOCUMENT(isEdge._to)
                                FILTER f != null AND f.isFile == false
                                RETURN f._key
                        )
                        FILTER folder_file != null
                        RETURN folder_file
                    """

                    folder_files_cursor = transaction.aql.execute(folder_files_query, bind_vars={
                        "folder_keys": inventory["all_folders"],
                        "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                    })
                    folder_file_keys = [f for f in folder_files_cursor]

                    # Delete folder FILES documents
                    if folder_file_keys:
                        folders_files_delete_query = """
                        FOR file_key IN @file_keys
                            REMOVE file_key IN @@files_collection OPTIONS { ignoreErrors: true }
                        """
                        transaction.aql.execute(folders_files_delete_query, bind_vars={
                            "file_keys": folder_file_keys,
                            "@files_collection": CollectionNames.FILES.value,
                        })
                        self.logger.info(f"✅ Deleted {len(folder_file_keys)} folder FILES documents")

                    # Delete folder RECORDS documents (reverse order to delete children before parents)
                    folders_delete_query = """
                    FOR folder_key IN @folder_keys
                        REMOVE folder_key IN @@records_collection OPTIONS { ignoreErrors: true }
                    """
                    reversed_folders = list(reversed(inventory["all_folders"]))
                    transaction.aql.execute(folders_delete_query, bind_vars={
                        "folder_keys": reversed_folders,
                        "@records_collection": CollectionNames.RECORDS.value,
                    })

                    self.logger.info(f"✅ Deleted {len(inventory['all_folders'])} folder RECORDS documents")

                # Step 6: Commit transaction
                if should_commit:
                    self.logger.info("💾 Committing complete folder deletion transaction...")
                    try:
                        await asyncio.to_thread(lambda: transaction.commit_transaction())
                        self.logger.info("✅ Transaction committed successfully!")
                    except Exception as commit_error:
                        self.logger.error(f"❌ Transaction commit failed: {str(commit_error)}")
                        try:
                            await asyncio.to_thread(lambda: transaction.abort_transaction())
                            self.logger.info("🔄 Transaction aborted after commit failure")
                        except Exception as abort_error:
                            self.logger.error(f"❌ Transaction abort failed: {str(abort_error)}")
                        return False

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
                        self.logger.info(f"✅ Published delete events for {len(delete_event_tasks)} records from folder deletion")

                except Exception as event_error:
                    self.logger.error(f"❌ Failed to publish folder deletion events: {str(event_error)}")
                    # Don't fail the main operation for event publishing errors

                self.logger.info(f"🎉 Folder {folder_id} and ALL contents deleted successfully:")
                self.logger.info(f"   📁 {inventory['total_folders']} folders (including target)")
                self.logger.info(f"   📄 {inventory['total_records']} records")
                self.logger.info(f"   🗂️ {inventory['total_file_records']} file records")
                self.logger.info("   🔗 All associated edges")

                return True

            except Exception as db_error:
                self.logger.error(f"❌ Database error during folder deletion: {str(db_error)}")
                if should_commit and transaction:
                    try:
                        await asyncio.to_thread(lambda: transaction.abort_transaction())
                        self.logger.info("🔄 Transaction aborted due to error")
                    except Exception as abort_error:
                        self.logger.error(f"❌ Transaction abort failed: {str(abort_error)}")
                raise db_error

        except Exception as e:
            self.logger.error(f"❌ Failed to delete folder {folder_id} completely: {str(e)}")
            return False

    async def _check_name_conflict_in_parent(
        self,
        kb_id: str,
        parent_folder_id: Optional[str],
        item_name: str,
        mime_type: Optional[str] = None,
        transaction: Optional[TransactionDatabase] = None
    ) -> Dict:
        """
        Check if an item (folder or file) name already exists in the target parent location.
        Handles different field names: folders have 'name', records have 'recordName'.

        For files: Only conflicts if same name AND same MIME type (allows same name with different MIME types).
        For folders: Conflicts if same name (folders must have unique names).

        Args:
            kb_id: Knowledge base ID
            parent_folder_id: Parent folder ID (None for KB root)
            item_name: Name of the item to check
            mime_type: Optional MIME type - if provided, checking a file; if None, checking a folder
            transaction: Optional transaction database context

        Returns:
            Dict with 'has_conflict' (bool) and 'conflicts' (list) keys
        """
        try:
            db = transaction if transaction else self.db

            # Prepare normalized lowercase variants (NFC/NFD) to catch visually-identical names
            name_variants = self._normalized_name_variants_lower(item_name)

            # Determine the parent reference based on whether we're in a folder or KB root
            # Folders are now represented by RECORDS documents, so use records/{parent_folder_id}
            parent_from = f"records/{parent_folder_id}" if parent_folder_id else f"recordGroups/{kb_id}"

            bind_vars = {
                "parent_from": parent_from,
                "name_variants": name_variants,
                "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                "@files_collection": CollectionNames.FILES.value,
            }

            if mime_type:
                # Checking a FILE: Only conflict if same name AND same MIME type
                # Files are stored as records, and we can filter by mimeType on the record before expensive lookups
                # edge._to for files points to records/{record_id}
                query = """
                FOR edge IN @@record_relations
                    FILTER edge._from == @parent_from
                    FILTER edge.relationshipType == "PARENT_CHILD"
                    // edge._to for files points to records/{record_id}
                    FILTER edge._to LIKE "records/%"
                    LET child = DOCUMENT(edge._to)
                    FILTER child != null
                    // Verify it's a record and filter by mimeType early to reduce lookups
                    FILTER child.recordName != null
                    FILTER child.mimeType == @mime_type
                    LET child_name_l = LOWER(child.recordName)
                    FILTER child_name_l IN @name_variants
                    // Get the associated file document to confirm it's a file
                    // Records and files share the same _key
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
                # Checking a FOLDER: Conflict if same name (folders must be unique)
                # Folders are now represented by RECORDS documents, so edge._to points to records/{folder_id}
                query = """
                FOR edge IN @@record_relations
                    FILTER edge._from == @parent_from
                    FILTER edge.relationshipType == "PARENT_CHILD"
                    // edge._to for folders points to records/{folder_id}
                    FILTER edge._to LIKE "records/%"
                    LET folder_record = DOCUMENT(edge._to)
                    FILTER folder_record != null
                    // Verify it's a folder by checking associated FILES document via IS_OF_TYPE edge
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

            cursor = db.aql.execute(query, bind_vars=bind_vars)
            conflicts = list(cursor)

            return {
                "has_conflict": len(conflicts) > 0,
                "conflicts": conflicts
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to check name conflict: {str(e)}")
            return {"has_conflict": False, "conflicts": []}

    async def get_folder_by_kb_and_path(
        self,
        kb_id: str,
        folder_path: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[Dict]:
        """
        Get folder by KB ID + path (much faster than edge traversal)
        Uses the kbId field stored directly in folder records
        """
        try:
            db = transaction if transaction else self.db

            # Direct query using KB ID + path - very fast with proper indexing
            query = """
            FOR folder IN @@files_collection
                FILTER folder.recordGroupId == @kb_id
                FILTER folder.path == @folder_path
                FILTER folder.isFile == false
                RETURN folder
            """

            cursor = db.aql.execute(query, bind_vars={
                "kb_id": kb_id,
                "folder_path": folder_path,
                "@files_collection": CollectionNames.FILES.value,
            })

            result = next(cursor, None)

            if result:
                self.logger.debug(f"✅ Found folder: {folder_path} in KB {kb_id}")
            else:
                self.logger.debug(f"❌ Folder not found: {folder_path} in KB {kb_id}")

            return result

        except Exception as e:
            self.logger.error(f"❌ Failed to get folder by KB and path: {str(e)}")
            return None

    async def validate_folder_exists_in_kb(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> bool:
        """
        Validate folder exists in specific KB
        Uses direct KB ID check instead of edge traversal
        """
        try:
            db = transaction if transaction else self.db

            query = """
            FOR folder IN @@files_collection
                FILTER folder._key == @folder_id
                FILTER folder.recordGroupId == @kb_id
                FILTER folder.isFile == false
                RETURN true
            """

            cursor = db.aql.execute(query, bind_vars={
                "folder_id": folder_id,
                "kb_id": kb_id,
                "@files_collection": CollectionNames.FILES.value,
            })

            return next(cursor, False)

        except Exception as e:
            self.logger.error(f"❌ Failed to validate folder in KB: {str(e)}")
            return False

    async def upload_records(
        self,
        kb_id: str,
        user_id: str,
        org_id: str,
        files: List[Dict],
        parent_folder_id: Optional[str] = None,  # None = KB root, str = specific folder
    ) -> Dict:
        """
        - KB root upload (parent_folder_id=None)
        - Folder upload (parent_folder_id=folder_id)
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
                }
            else:
                return result

        except Exception as e:
            self.logger.error(f"❌ Unified upload failed: {str(e)}")
            return {"success": False, "reason": f"Upload failed: {str(e)}", "code": 500}

    async def get_user_by_user_id(self, user_id: str) -> Optional[Dict]:
        """
        Get user by user ID.
        Recommended index: users collection: [ "userId" ] (persistent index)
        """
        try:
            query = f"""
                FOR user IN {CollectionNames.USERS.value}
                    FILTER user.userId == @user_id
                    LIMIT 1
                    RETURN user
            """
            cursor = self.db.aql.execute(query, bind_vars={"user_id": user_id})
            result = next(cursor, None)
            return result
        except Exception as e:
            self.logger.error(f"Error getting user by user ID: {str(e)}")
            return None

    async def copy_document_relationships(self, source_key: str, target_key: str) -> None:
        """
        Copy all relationships (edges) from source document to target document.
        This includes departments, categories, subcategories, languages, and topics.

        Args:
            source_key (str): Key of the source document
            target_key (str): Key of the target document
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

                cursor = self.db.aql.execute(
                    query,
                    bind_vars={
                        "source_doc": f"{CollectionNames.RECORDS.value}/{source_key}"
                    }
                )

                edges = list(cursor)

                if edges:
                    # Create new edges for target document
                    new_edges = []
                    for edge in edges:
                        new_edge = {
                            "_from": f"{CollectionNames.RECORDS.value}/{target_key}",
                            "_to": edge["to"],
                            "createdAtTimestamp": get_epoch_timestamp_in_ms()
                        }
                        new_edges.append(new_edge)

                    # Batch create the new edges
                    await self.batch_create_edges(new_edges, collection)
                    self.logger.info(
                        f"✅ Copied {len(new_edges)} relationships from collection {collection}"
                    )

            self.logger.info(f"✅ Successfully copied all relationships to {target_key}")

        except Exception as e:
            self.logger.error(
                f"❌ Error copying relationships from {source_key} to {target_key}: {str(e)}"
            )
            raise


    async def get_key_by_external_message_id(
        self,
        external_message_id: str,
        transaction: Optional[TransactionDatabase] = None,
    ) -> Optional[str]:
        """
        Get internal message key using the external message ID

        Args:
            external_message_id (str): External message ID to look up
            transaction (Optional[TransactionDatabase]): Optional database transaction

        Returns:
            Optional[str]: Internal message key if found, None otherwise
        """
        try:
            self.logger.info(
                "🚀 Retrieving internal key for external message ID %s",
                external_message_id,
            )

            query = f"""
            FOR doc IN {CollectionNames.RECORDS.value}
                FILTER doc.externalRecordId == @external_message_id
                RETURN doc._key
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query, bind_vars={"external_message_id": external_message_id}
            )
            result = next(cursor, None)

            if result:
                self.logger.info(
                    "✅ Successfully retrieved internal key for external message ID %s",
                    external_message_id,
                )
                return result
            else:
                self.logger.warning(
                    "⚠️ No internal key found for external message ID %s",
                    external_message_id,
                )
                return None

        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve internal key for external message ID %s: %s",
                external_message_id,
                str(e),
            )
            return None

    async def get_departments(self, org_id: Optional[str] = None) -> List[str]:
        """
        Get all departments that either have no org_id or match the given org_id

        Args:
            org_id (Optional[str]): Organization ID to filter departments

        Returns:
            List[str]: List of department names
        """
        query = f"""
            FOR department IN {CollectionNames.DEPARTMENTS.value}
                FILTER department.orgId == null OR department.orgId == '{org_id}'
                RETURN department.departmentName
        """
        cursor = self.db.aql.execute(query)
        return list(cursor)


    async def find_duplicate_records(
        self,
        record_key: str,
        md5_checksum: str,
        record_type: Optional[str] = None,
        size_in_bytes: Optional[int] = None,
        transaction: Optional[TransactionDatabase] = None,
    ) -> List[Dict]:
        """
        Find duplicate records based on MD5 checksum.
        This method queries the RECORDS collection and works for all record types.

        Args:
            record_key (str): The key of the current record to exclude from results
            md5_checksum (str): MD5 checksum of the record content
            record_type (Optional[str]): Optional record type to filter by
            size_in_bytes (Optional[int]): Optional file size in bytes to filter by
            transaction (Optional[TransactionDatabase]): Optional database transaction

        Returns:
            List[Dict]: List of duplicate records that match both criteria
        """
        try:
            self.logger.info(
                "🔍 Finding duplicate records with MD5: %s",
                md5_checksum,
            )

            # Build query with optional record type filter
            query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record.md5Checksum == @md5_checksum
                AND record._key != @record_key
            """

            bind_vars = {
                "md5_checksum": md5_checksum,
                "record_key": record_key,
            }

            if record_type:
                query += """
                AND record.recordType == @record_type
                """
                bind_vars["record_type"] = record_type

            if size_in_bytes is not None:
                query += """
                AND record.sizeInBytes == @size_in_bytes
                """
                bind_vars["size_in_bytes"] = size_in_bytes

            query += """
                RETURN record
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars=bind_vars)

            duplicate_records = list(cursor)

            if duplicate_records:
                self.logger.info(
                    "✅ Found %d duplicate record(s) matching criteria",
                    len(duplicate_records)
                )
            else:
                self.logger.info("✅ No duplicate records found")

            return duplicate_records

        except Exception as e:
            self.logger.error(
                "Failed to find duplicate records: %s",
                str(e)
            )
            if transaction:
                raise
            return []

    async def get_records_by_virtual_record_id(
        self,
        virtual_record_id: str,
        accessible_record_ids: Optional[List[str]] = None
    ) -> List[str]:
        """
        Get all record keys that have the given virtualRecordId.
        Optionally filter by a list of record IDs.

        Args:
            virtual_record_id (str): Virtual record ID to look up
            record_ids (Optional[List[str]]): Optional list of record IDs to filter by

        Returns:
            List[str]: List of record keys that match the criteria
        """
        try:
            self.logger.info(
                "🔍 Finding records with virtualRecordId: %s", virtual_record_id
            )

            # Base query
            query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record.virtualRecordId == @virtual_record_id
            """

            # Add optional filter for record IDs
            if accessible_record_ids:
                query += """
                AND record._key IN @accessible_record_ids
                """

            query += """
                RETURN record._key
            """

            bind_vars = {"virtual_record_id": virtual_record_id}
            if accessible_record_ids:
                bind_vars["accessible_record_ids"] = accessible_record_ids

            cursor = self.db.aql.execute(query, bind_vars=bind_vars)
            results = list(cursor)

            self.logger.info(
                "✅ Found %d records with virtualRecordId %s",
                len(results),
                virtual_record_id
            )
            return results

        except Exception as e:
            self.logger.error(
                "❌ Error finding records with virtualRecordId %s: %s",
                virtual_record_id,
                str(e)
            )
            return []

    async def get_documents_by_status(self, collection: str, status: str) -> List[Dict]:
        """
        Get all documents with a specific indexing status

        Args:
            collection (str): Collection name
            status (str): Status to filter by

        Returns:
            List[Dict]: List of matching documents
        """
        query = """
        FOR doc IN @@collection
            FILTER doc.indexingStatus == @status
            RETURN doc
        """

        bind_vars = {
            "@collection": collection,
            "status": status
        }

        cursor = self.db.aql.execute(query, bind_vars=bind_vars)
        return list(cursor)


    async def update_queued_duplicates_status(
        self,
        record_id: str,
        new_indexing_status: str,
        virtual_record_id: Optional[str|None] = None,
        transaction: Optional[TransactionDatabase] = None,
    ) -> int:
        """
        Find all QUEUED duplicate records with the same md5 hash and update their status.
        Works with all record types by querying the RECORDS collection directly.

        Args:
            record_id (str): The record ID to use as reference for finding duplicates
            new_indexing_status (str): The new indexing status to set
            virtual_record_id (Optional[str]): The virtual record ID to set on duplicates
            transaction (Optional[TransactionDatabase]): Optional database transaction

        Returns:
            int: Number of records updated
        """
        try:
            self.logger.info(
                f"🔍 Finding QUEUED duplicate records for record {record_id}"
            )

            # First get the record info for the reference record
            record_query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record._key == @record_id
                RETURN record
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                record_query,
                bind_vars={"record_id": record_id}
            )

            ref_record = None
            try:
                ref_record = cursor.next()
            except StopIteration:
                self.logger.info(f"No record found for {record_id}, skipping queued duplicate update")
                return 0

            if not ref_record:
                self.logger.info(f"No record found for {record_id}, skipping queued duplicate update")
                return 0

            md5_checksum = ref_record.get("md5Checksum")
            size_in_bytes = ref_record.get("sizeInBytes")

            if not md5_checksum:
                self.logger.warning(f"Record {record_id} missing md5Checksum")
                return 0

            # Find all queued duplicate records directly from RECORDS collection
            query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record.md5Checksum == @md5_checksum
                AND record._key != @record_id
                AND record.indexingStatus == @queued_status
            """

            bind_vars = {
                "md5_checksum": md5_checksum,
                "record_id": record_id,
                "queued_status": "QUEUED"
            }

            if size_in_bytes is not None:
                query += """
                AND record.sizeInBytes == @size_in_bytes
                """
                bind_vars["size_in_bytes"] = size_in_bytes

            query += """
                RETURN record
            """

            cursor = db.aql.execute(
                query,
                bind_vars=bind_vars
            )

            queued_records = list(cursor)

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
            await self.batch_upsert_nodes(updated_records, CollectionNames.RECORDS.value,transaction)

            self.logger.info(
                f"✅ Successfully updated {len(queued_records)} QUEUED duplicate record(s) to status {new_indexing_status}"
            )

            return len(queued_records)

        except Exception as e:
            self.logger.error(
                f"❌ Failed to update queued duplicates status: {str(e)}"
            )
            return -1


    async def find_next_queued_duplicate(
        self,
        record_id: str,
        transaction: Optional[TransactionDatabase] = None,
    ) -> Optional[dict]:
        """
        Find the next QUEUED duplicate record with the same md5 hash.
        Works with all record types by querying the RECORDS collection directly.

        Args:
            record_id (str): The record ID to use as reference for finding duplicates
            transaction (Optional[TransactionDatabase]): Optional database transaction

        Returns:
            Optional[dict]: The next queued record if found, None otherwise
        """
        try:
            self.logger.info(
                f"🔍 Finding next QUEUED duplicate record for record {record_id}"
            )

            # First get the record info for the reference record
            record_query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record._key == @record_id
                RETURN record
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                record_query,
                bind_vars={"record_id": record_id}
            )

            ref_record = None
            try:
                ref_record = cursor.next()
            except StopIteration:
                self.logger.info(f"No record found for {record_id}, skipping queued duplicate search")
                return None

            if not ref_record:
                self.logger.info(f"No record found for {record_id}, skipping queued duplicate search")
                return None

            md5_checksum = ref_record.get("md5Checksum")
            size_in_bytes = ref_record.get("sizeInBytes")

            if not md5_checksum:
                self.logger.warning(f"Record {record_id} missing md5Checksum")
                return None

            # Find the first queued duplicate record directly from RECORDS collection
            query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record.md5Checksum == @md5_checksum
                AND record._key != @record_id
                AND record.indexingStatus == @queued_status
            """

            bind_vars = {
                "md5_checksum": md5_checksum,
                "record_id": record_id,
                "queued_status": "QUEUED"
            }

            if size_in_bytes is not None:
                query += """
                AND record.sizeInBytes == @size_in_bytes
                """
                bind_vars["size_in_bytes"] = size_in_bytes

            query += """
                LIMIT 1
                RETURN record
            """

            cursor = db.aql.execute(
                query,
                bind_vars=bind_vars
            )

            queued_record = None
            try:
                queued_record = cursor.next()
            except StopIteration:
                self.logger.info("✅ No QUEUED duplicate record found")
                return None

            if queued_record:
                self.logger.info(
                    f"✅ Found QUEUED duplicate record: {queued_record.get('_key')}"
                )
                return dict(queued_record)

            return None

        except Exception as e:
            self.logger.error(
                f"❌ Failed to find next queued duplicate: {str(e)}"
            )
            return None

    async def get_accessible_records(
        self, user_id: str, org_id: str, filters: dict = None
    ) -> list:
        """
        Get all records accessible to a user based on their permissions and apply filters

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

            user_key = user.get('_key')
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

                # # Get all KB records (no KB filter)
                # query += f"""
                # LET kbRecords = (
                #     FOR kb IN 1..1 ANY userDoc._id {CollectionNames.PERMISSIONS_TO_KB.value}
                #     FOR records IN 1..1 ANY kb._id {CollectionNames.BELONGS_TO.value}
                #     RETURN DISTINCT records
                # )
                # """
                # unions.append("kbRecords")

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
            cursor = self.db.aql.execute(
                query,
                bind_vars=bind_vars,
                profile=2,
                fail_on_warning=False,
                stream=True
            )
            result = list(cursor)

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

    async def validate_user_kb_access(
        self,
        user_id: str,
        org_id: str,
        kb_ids: List[str]
    ) -> Dict[str, List[str]]:
        """
        OPTIMIZED: Validate which KB IDs the user has access to using fast lookups
        Args:
            user_id: External user ID
            org_id: Organization ID
            kb_ids: List of KB IDs to check access for

        Returns:
            Dict with 'accessible' and 'inaccessible' KB IDs
        """
        try:
            self.logger.info(f"🚀 Fast KB access validation for user {user_id} on {len(kb_ids)} KBs")

            if not kb_ids:
                return {"accessible": [], "inaccessible": [], "total_user_kbs": 0}

            user = await self.get_user_by_user_id(user_id=user_id)
            if not user:
                self.logger.warning(f"⚠️ User not found: {user_id}")
                return {
                    "accessible": [],
                    "inaccessible": kb_ids,
                    "error": f"User not found: {user_id}"
                }

            user_key = user.get('_key')

            validation_query = """
            // Convert requested KB list to a set for fast lookup
            LET requested_kb_set = @kb_ids
            LET user_from = @user_from
            LET org_id = @org_id

            // Get user's accessible KBs in this org with direct filtering
            // Using FILTER early to reduce data processing
            LET user_accessible_kbs = (
                FOR perm IN @@permission
                    FILTER perm._from == user_from
                    FILTER perm.type == "USER"
                    // Fast role check using IN operator
                    FILTER perm.role IN ["OWNER", "READER", "FILEORGANIZER", "WRITER", "COMMENTER", "ORGANIZER"]
                    // Extract KB key directly from _to field (faster than DOCUMENT lookup)
                    LET kb_key = PARSE_IDENTIFIER(perm._to).key
                    // Early filter: only check KBs that were requested OR get all for org validation
                    LET kb_doc = DOCUMENT(CONCAT("recordGroups/", kb_key))
                    FILTER kb_doc != null
                    FILTER kb_doc.orgId == org_id
                    FILTER kb_doc.groupType == "KB"
                    FILTER kb_doc.connectorName == "KB"
                    RETURN kb_key
            )

            // Convert to sets for O(1) lookup complexity
            LET accessible_set = user_accessible_kbs
            LET accessible_requested = (
                FOR kb_id IN requested_kb_set
                    FILTER kb_id IN accessible_set
                    RETURN kb_id
            )

            LET inaccessible_requested = (
                FOR kb_id IN requested_kb_set
                    FILTER kb_id NOT IN accessible_set
                    RETURN kb_id
            )

            // Return minimal result set
            RETURN {
                accessible: accessible_requested,
                inaccessible: inaccessible_requested,
                total_user_kbs: LENGTH(accessible_set)
            }
            """

            bind_vars = {
                "user_from": f"users/{user_key}",
                "org_id": org_id,
                "kb_ids": kb_ids,
                "@permission": CollectionNames.PERMISSION.value,
            }

            cursor = self.db.aql.execute(
                validation_query,
                bind_vars=bind_vars,
                count=False,           # Don't count results
                batch_size=1000,       # Larger batch size for faster transfer
                cache=True,            # Enable query result caching
                memory_limit=0,        # No memory limit for faster execution
                max_runtime=30.0,      # 30 second timeout
                fail_on_warning=False, # Don't fail on warnings
                profile=False,         # Disable profiling for speed
                stream=True            # Stream results for memory efficiency
            )

            result = next(cursor, {})

            accessible = result.get("accessible", [])
            inaccessible = result.get("inaccessible", [])


            self.logger.info(f"KB validation complete: {len(accessible)}/{len(kb_ids)} accessible")

            if inaccessible:
                self.logger.warning(f"⚠️ User {user_id} lacks access to {len(inaccessible)} KBs")

            return {
                "accessible": accessible,
                "inaccessible": inaccessible,
                "total_user_kbs": result.get("total_user_kbs", 0)
            }

        except Exception as e:
            self.logger.error(f"❌ KB access validation error: {str(e)}")
            return {
                "accessible": [],
                "inaccessible": kb_ids,
                "error": str(e)
            }


    async def get_all_agent_templates(self, user_id: str) -> List[Dict]:
        """Get all agent templates accessible to a user via individual or team access"""
        try:
            query = f"""
            LET user_key = @user_id

            // Get user's teams
            LET user_teams = (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', user_key)
                    FILTER perm.type == "USER"
                    FILTER STARTS_WITH(perm._to, '{CollectionNames.TEAMS.value}/')
                    RETURN perm._to
            )

            // Get templates with individual access
            LET individual_templates = (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', user_key)
                    FILTER perm.type == "USER"
                    FILTER STARTS_WITH(perm._to, '{CollectionNames.AGENT_TEMPLATES.value}/')
                    LET template = DOCUMENT(perm._to)
                    FILTER template != null
                    FILTER template.isDeleted != true
                    RETURN MERGE(template, {{
                        access_type: perm.type,
                        user_role: perm.role,
                        permission_id: perm._key,
                        permission_from: perm._from,
                        permission_to: perm._to,
                        permission_created_at: perm.createdAtTimestamp,
                        permission_updated_at: perm.updatedAtTimestamp,
                        can_edit: perm.role IN ["OWNER", "WRITER", "ORGANIZER"],
                        can_delete: perm.role == "OWNER",
                        can_share: perm.role IN ["OWNER", "ORGANIZER"],
                        can_view: true
                    }})
            )

            // Get templates with team access (excluding those already found via individual access)
            LET individual_template_ids = (FOR ind_template IN individual_templates RETURN ind_template._id)

            LET team_templates = (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from IN user_teams
                    FILTER perm.type == "TEAM"
                    FILTER STARTS_WITH(perm._to, '{CollectionNames.AGENT_TEMPLATES.value}/')
                    FILTER perm._to NOT IN individual_template_ids
                    LET template = DOCUMENT(perm._to)
                    FILTER template != null
                    FILTER template.isDeleted != true
                    RETURN MERGE(template, {{
                        access_type: perm.type,
                        user_role: perm.role,
                        permission_id: perm._key,
                        permission_from: perm._from,
                        permission_to: perm._to,
                        permission_created_at: perm.createdAtTimestamp,
                        permission_updated_at: perm.updatedAtTimestamp,
                        can_edit: perm.role IN ["OWNER", "WRITER", "ORGANIZER"],
                        can_delete: perm.role == "OWNER",
                        can_share: perm.role IN ["OWNER", "ORGANIZER"],
                        can_view: true
                    }})
            )

            // Flatten and return all templates
            FOR template_result IN APPEND(individual_templates, team_templates)
                RETURN template_result
            """

            bind_vars = {
                "user_id": user_id,
            }

            self.logger.info(f"Getting all agent templates accessible by user {user_id}")
            cursor = self.db.aql.execute(query, bind_vars=bind_vars)
            return list(cursor)

        except Exception as e:
            self.logger.error("❌ Failed to get all agent templates: %s", str(e))
            return []


    async def get_template(self, template_id: str, user_id: str) -> Optional[Dict]:
        """Get a template by ID with user permissions"""
        try:
            query = f"""
            LET user_key = @user_id
            LET template_path = CONCAT('{CollectionNames.AGENT_TEMPLATES.value}/', @template_id)

            // Get user's teams first
            LET user_teams = (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', user_key)
                    FILTER perm.type == "USER"
                    FILTER STARTS_WITH(perm._to, '{CollectionNames.TEAMS.value}/')
                    RETURN perm._to
            )

            // Check individual user permissions on the template
            LET individual_access = (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', user_key)
                    FILTER perm._to == template_path
                    FILTER perm.type == "USER"
                    LET template = DOCUMENT(template_path)
                    FILTER template != null
                    FILTER template.isDeleted != true
                    RETURN MERGE(template, {{
                        access_type: "INDIVIDUAL",
                        user_role: perm.role,
                        permission_id: perm._key,
                        permission_from: perm._from,
                        permission_to: perm._to,
                        permission_created_at: perm.createdAtTimestamp,
                        permission_updated_at: perm.updatedAtTimestamp,
                        can_edit: perm.role IN ["OWNER", "WRITER", "ORGANIZER"],
                        can_delete: perm.role == "OWNER",
                        can_share: perm.role IN ["OWNER", "ORGANIZER"],
                        can_view: true
                    }})
            )

            // Check team permissions on the template (only if no individual access)
            LET team_access = LENGTH(individual_access) == 0 ? (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from IN user_teams
                    FILTER perm._to == template_path
                    FILTER perm.type == "TEAM"
                    LET template = DOCUMENT(template_path)
                    FILTER template != null
                    FILTER template.isDeleted != true
                    RETURN MERGE(template, {{
                        access_type: "TEAM",
                        user_role: perm.role,
                        permission_id: perm._key,
                        permission_from: perm._from,
                        permission_to: perm._to,
                        permission_created_at: perm.createdAtTimestamp,
                        permission_updated_at: perm.updatedAtTimestamp,
                        can_edit: perm.role IN ["OWNER", "WRITER", "ORGANIZER"],
                        can_delete: perm.role == "OWNER",
                        can_share: perm.role IN ["OWNER", "ORGANIZER"],
                        can_view: true
                    }})
            ) : []

            // Return individual access first, then team access
            LET final_result = LENGTH(individual_access) > 0 ?
                FIRST(individual_access) :
                (LENGTH(team_access) > 0 ? FIRST(team_access) : null)

            RETURN final_result
            """

            bind_vars = {
                "template_id": template_id,
                "user_id": user_id,
            }

            self.logger.info(f"Getting template {template_id} accessible by user {user_id}")
            cursor = self.db.aql.execute(query, bind_vars=bind_vars)
            result = list(cursor)

            if len(result) == 0 or result[0] is None:
                return None

            return result[0]

        except Exception as e:
            self.logger.error("❌ Failed to get template access: %s", str(e))
            return None

    async def share_agent_template(self, template_id: str, user_id: str, user_ids: Optional[List[str]] = None, team_ids: Optional[List[str]] = None) -> Optional[bool]:
        """Share an agent template with users"""
        try:
            self.logger.info(f"Sharing agent template {template_id} with users {user_ids}")

            user_owner_access_query = f"""
            FOR perm IN {CollectionNames.PERMISSION.value}
                FILTER perm._to == CONCAT('{CollectionNames.AGENT_TEMPLATES.value}/', @template_id)
                FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', @user_id)
                FILTER STARTS_WITH(perm._to, '{CollectionNames.AGENT_TEMPLATES.value}/')
                FILTER DOCUMENT(perm._to).isDeleted == false
                LIMIT 1
                RETURN DOCUMENT(perm._to)
            """
            bind_vars = {
                "template_id": template_id,
                "user_id": user_id,
            }
            cursor = self.db.aql.execute(user_owner_access_query, bind_vars=bind_vars)
            user_owner_access = list(cursor)
            if len(user_owner_access) == 0:
                return False
            user_owner_access = user_owner_access[0]
            if user_owner_access.get("role") != "OWNER":
                return False

            if user_ids is None and team_ids is None:
                return False

            #  users to be given access
            user_template_accesses = []
            if user_ids:
                for user_id in user_ids:
                    user = await self.get_user_by_user_id(user_id)
                    if user is None:
                        return False
                    edge = {
                        "_from": f"{CollectionNames.USERS.value}/{user.get('_key')}",
                        "_to": f"{CollectionNames.AGENT_TEMPLATES.value}/{template_id}",
                        "type": "USER",
                        "role": "READER",
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    }
                    user_template_accesses.append(edge)

            if team_ids:
                for team_id in team_ids:
                    edge = {
                        "_from": f"{CollectionNames.TEAMS.value}/{team_id}",
                        "_to": f"{CollectionNames.AGENT_TEMPLATES.value}/{template_id}",
                        "type": "TEAM",
                        "role": "READER",
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    }
                    user_template_accesses.append(edge)

            result = await self.batch_create_edges(user_template_accesses, CollectionNames.PERMISSION.value)
            if not result:
                return False
            return True
        except Exception as e:
            self.logger.error("❌ Failed to share agent template: %s", str(e))
            return False

    async def clone_agent_template(self, template_id: str) -> Optional[str]:
        """Clone an agent template"""
        try:
            template = await self.get_document(template_id, CollectionNames.AGENT_TEMPLATES.value)
            if template is None:
                return None
            template_key = str(uuid.uuid4())
            template["_key"] = template_key
            template["isActive"] = True
            template["isDeleted"] = False
            template["deletedAtTimestamp"] = None
            template["deletedByUserId"] = None
            template["updatedAtTimestamp"] = get_epoch_timestamp_in_ms()
            template["updatedByUserId"] = None
            template["createdAtTimestamp"] = get_epoch_timestamp_in_ms()
            template["createdBy"] = None
            template["deletedByUserId"] = None
            template["deletedAtTimestamp"] = None
            template["isDeleted"] = False
            result = await self.batch_upsert_nodes([template], CollectionNames.AGENT_TEMPLATES.value)
            if not result:
                return None
            return template_key
        except Exception as e:
            self.logger.error("❌ Failed to close agent template: %s", str(e))
            return False


    async def delete_agent_template(self, template_id: str, user_id: str) -> Optional[bool]:
        """Delete an agent template"""
        try:
            template_document_id = f"{CollectionNames.AGENT_TEMPLATES.value}/{template_id}"
            user_document_id = f"{CollectionNames.USERS.value}/{user_id}"

            permission_query = f"""
            FOR perm IN {CollectionNames.PERMISSION.value}
                FILTER perm._to == @template_document_id
                FILTER perm._from == @user_document_id
                FILTER perm.role == "OWNER"
                FILTER STARTS_WITH(perm._to, '{CollectionNames.AGENT_TEMPLATES.value}/')
                FILTER DOCUMENT(perm._to).isDeleted == false
                LIMIT 1
                RETURN perm
            """

            bind_vars = {
                "template_document_id": template_document_id,
                "user_document_id": user_document_id,
            }

            cursor = self.db.aql.execute(permission_query, bind_vars=bind_vars)
            permissions = list(cursor)

            if len(permissions) == 0:
                self.logger.warning(f"No permission found for user {user_id} on template {template_id}")
                return False
            permission = permissions[0]
            if permission.get("role") != "OWNER":
                self.logger.warning(f"User {user_id} is not the owner of template {template_id}")
                return False

            # Check if template exists
            template = await self.get_document(template_id, CollectionNames.AGENT_TEMPLATES.value)
            if template is None:
                self.logger.warning(f"Template {template_id} not found")
                return False

            # Prepare update data for soft delete
            update_data = {
                "isDeleted": True,
                "deletedAtTimestamp": get_epoch_timestamp_in_ms(),
                "deletedByUserId": user_id
            }

            # Soft delete the template using AQL UPDATE
            update_query = f"""
            UPDATE @template_key
            WITH @update_data
            IN {CollectionNames.AGENT_TEMPLATES.value}
            RETURN NEW
            """

            bind_vars = {
                "template_key": template_id,
                "update_data": update_data,
            }

            cursor = self.db.aql.execute(update_query, bind_vars=bind_vars)
            result = list(cursor)

            if not result or len(result) == 0:
                self.logger.error(f"Failed to delete template {template_id}")
                return False

            self.logger.info(f"Successfully deleted template {template_id}")
            return True

        except Exception as e:
            self.logger.error("❌ Failed to delete agent template: %s", str(e), exc_info=True)
            return False


    async def update_agent_template(self, template_id: str, template_updates: Dict[str, Any], user_id: str) -> Optional[bool]:
        """Update an agent template"""
        try:
            # Check if user is the owner of the template
            template_document_id = f"{CollectionNames.AGENT_TEMPLATES.value}/{template_id}"
            user_document_id = f"{CollectionNames.USERS.value}/{user_id}"

            permission_query = f"""
            FOR perm IN {CollectionNames.PERMISSION.value}
                FILTER perm._to == @template_document_id
                FILTER perm._from == @user_document_id
                FILTER perm.role == "OWNER"
                FILTER STARTS_WITH(perm._to, '{CollectionNames.AGENT_TEMPLATES.value}/')
                FILTER DOCUMENT(perm._to).isDeleted == false
                LIMIT 1
                RETURN perm
            """

            bind_vars = {
                "template_document_id": template_document_id,
                "user_document_id": user_document_id,
            }

            cursor = self.db.aql.execute(permission_query, bind_vars=bind_vars)
            permissions = list(cursor)

            if len(permissions) == 0:
                self.logger.warning(f"No permission found for user {user_id} on template {template_id}")
                return False
            permission = permissions[0]
            if permission.get("role") != "OWNER":
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

            # Update the template - use the collection and document key
            update_query = f"""
            UPDATE @template_key
            WITH @update_data
            IN {CollectionNames.AGENT_TEMPLATES.value}
            RETURN NEW
            """

            bind_vars = {
                "template_key": template_id,  # Use just the key, not the full document ID
                "update_data": update_data,
            }

            cursor = self.db.aql.execute(update_query, bind_vars=bind_vars)
            result = list(cursor)

            if not result or len(result) == 0:
                self.logger.error(f"Failed to update template {template_id}")
                return False

            self.logger.info(f"Successfully updated template {template_id}")
            return True

        except Exception as e:
            self.logger.error("❌ Failed to update agent template: %s", str(e), exc_info=True)
            return False


    async def get_agent(self, agent_id: str, user_id: str) -> Optional[Dict]:
        """
        Get an agent by ID with user permissions and linked graph data.

        Includes:
        - Agent document with permissions
        - Linked toolsets with their tools (via agentHasToolset -> toolsetHasTool)
        - Linked knowledge with filters (via agentHasKnowledge)
        """
        try:
            query = f"""
            LET user_key = @user_id
            LET agent_path = CONCAT('{CollectionNames.AGENT_INSTANCES.value}/', @agent_id)

            // Get user's teams first
            LET user_teams = (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', user_key)
                    FILTER perm.type == "USER"
                    FILTER STARTS_WITH(perm._to, '{CollectionNames.TEAMS.value}/')
                    RETURN perm._to
            )

            // Check individual user permissions on the agent
            LET individual_access = (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', user_key)
                    FILTER perm._to == agent_path
                    FILTER perm.type == "USER"
                    LET agent = DOCUMENT(agent_path)
                    FILTER agent != null
                    FILTER agent.isDeleted != true
                    RETURN MERGE(agent, {{
                        access_type: "INDIVIDUAL",
                        user_role: perm.role,
                        can_edit: perm.role IN ["OWNER", "WRITER", "ORGANIZER"],
                        can_delete: perm.role == "OWNER",
                        can_share: perm.role IN ["OWNER", "ORGANIZER"],
                        can_view: true
                    }})
            )

            // Check team permissions on the agent (only if no individual access)
            LET team_access = LENGTH(individual_access) == 0 ? (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from IN user_teams
                    FILTER perm._to == agent_path
                    FILTER perm.type == "TEAM"
                    LET agent = DOCUMENT(agent_path)
                    FILTER agent != null
                    FILTER agent.isDeleted != true
                    RETURN MERGE(agent, {{
                        access_type: "TEAM",
                        user_role: perm.role,
                        can_edit: perm.role IN ["OWNER", "WRITER", "ORGANIZER"],
                        can_delete: perm.role == "OWNER",
                        can_share: perm.role IN ["OWNER", "ORGANIZER"],
                        can_view: true
                    }})
            ) : []

            // Get base agent with permissions
            LET base_agent = LENGTH(individual_access) > 0 ?
                FIRST(individual_access) :
                (LENGTH(team_access) > 0 ? FIRST(team_access) : null)

            // Get linked toolsets with their tools
            LET linked_toolsets = base_agent != null ? (
                FOR edge IN {CollectionNames.AGENT_HAS_TOOLSET.value}
                    FILTER edge._from == agent_path
                    LET toolset = DOCUMENT(edge._to)
                    FILTER toolset != null

                    // Get tools linked to this toolset
                    LET toolset_tools = (
                        FOR tool_edge IN {CollectionNames.TOOLSET_HAS_TOOL.value}
                            FILTER tool_edge._from == edge._to
                            LET tool = DOCUMENT(tool_edge._to)
                            FILTER tool != null
                            RETURN {{
                                _key: tool._key,
                                name: tool.toolName,
                                fullName: tool.fullName,
                                toolsetName: tool.toolsetName,
                                description: tool.description
                            }}
                    )

                    RETURN {{
                        _key: toolset._key,
                        name: toolset.name,
                        displayName: toolset.displayName,
                        type: toolset.type,
                        userId: toolset.userId,
                        selectedTools: toolset.selectedTools,
                        tools: toolset_tools
                    }}
            ) : []

            // Get linked knowledge with filters and enrich with names
            LET linked_knowledge = base_agent != null ? (
                FOR edge IN {CollectionNames.AGENT_HAS_KNOWLEDGE.value}
                    FILTER edge._from == agent_path
                    LET knowledge = DOCUMENT(edge._to)
                    FILTER knowledge != null

                    // Parse filters to check if this is a KB or app
                    LET filters_parsed = TYPENAME(knowledge.filters) == "string" ?
                        JSON_PARSE(knowledge.filters) : knowledge.filters
                    LET record_groups = filters_parsed.recordGroups || []
                    LET is_kb = LENGTH(record_groups) > 0

                    // For KBs: Get KB name from record group document
                    // KBs have recordGroups in filters, and we look up the KB document
                    LET kb_info = is_kb && LENGTH(record_groups) > 0 ? (
                        LET first_kb_id = record_groups[0]
                        LET kb_doc = DOCUMENT(CONCAT('{CollectionNames.RECORD_GROUPS.value}/', first_kb_id))
                        FILTER kb_doc != null
                        FILTER kb_doc.groupType == @kb_type
                        RETURN {{
                            name: kb_doc.groupName,
                            type: "KB",
                            displayName: kb_doc.groupName,
                            // Use KB connector instance ID from KB document, fallback to knowledge.connectorId
                            connectorId: kb_doc.connectorId || knowledge.connectorId
                        }}
                    ) : []

                    // For apps: Get connector instance name from APPS collection
                    // Apps have empty recordGroups or no recordGroups
                    LET app_info = !is_kb ? (
                        LET connector_instance = DOCUMENT(CONCAT('{CollectionNames.APPS.value}/', knowledge.connectorId))
                        FILTER connector_instance != null
                        RETURN {{
                            name: connector_instance.name,
                            type: connector_instance.type || "APP",
                            displayName: connector_instance.name
                        }}
                    ) : []

                    // Determine the display info
                    LET display_info = LENGTH(kb_info) > 0 ? FIRST(kb_info) :
                                      (LENGTH(app_info) > 0 ? FIRST(app_info) : {{
                                          name: knowledge.connectorId,
                                          type: "UNKNOWN",
                                          displayName: knowledge.connectorId
                                      }})

                    RETURN {{
                        _key: knowledge._key,
                        // For KBs, use connectorId from KB document (KB connector instance ID)
                        // For apps, use the connectorId from knowledge (connector instance ID)
                        connectorId: LENGTH(kb_info) > 0 ? FIRST(kb_info).connectorId : knowledge.connectorId,
                        filters: knowledge.filters,
                        name: display_info.name,
                        type: display_info.type,
                        displayName: display_info.displayName || display_info.name
                    }}
            ) : []

            // Return agent with linked data
            RETURN base_agent != null ? MERGE(base_agent, {{
                toolsets: linked_toolsets,
                knowledge: linked_knowledge
            }}) : null
            """

            bind_vars = {
                "agent_id": agent_id,
                "user_id": user_id,
                "kb_type": Connectors.KNOWLEDGE_BASE.value,
            }

            cursor = self.db.aql.execute(query, bind_vars=bind_vars)
            result = list(cursor)

            if len(result) == 0 or result[0] is None:
                self.logger.warning(f"No permissions found for user {user_id} on agent {agent_id}")
                return None

            agent = result[0]

            # Parse knowledge filters from JSON strings
            if agent.get("knowledge"):
                for knowledge in agent["knowledge"]:
                    filters_str = knowledge.get("filters", "{}")
                    if isinstance(filters_str, str):
                        try:
                            knowledge["filtersParsed"] = json.loads(filters_str)
                        except json.JSONDecodeError:
                            knowledge["filtersParsed"] = {}
                    else:
                        knowledge["filtersParsed"] = filters_str

            return agent

        except Exception as e:
            self.logger.error(f"Failed to get agent: {str(e)}")
            return None

    async def check_toolset_in_use(self, toolset_name: str, user_id: str) -> List[str]:
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
            cursor = self.db.aql.execute(toolset_query, bind_vars={
                "name": toolset_name,
                "user_id": user_id
            })
            toolset_ids = list(cursor)

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
            agent_cursor = self.db.aql.execute(agent_query, bind_vars={"toolset_ids": toolset_ids})
            agents = list(agent_cursor)

            if agents:
                agent_names = list(set(a.get("agentName", "Unknown") for a in agents))
                return agent_names

            return []

        except Exception as e:
            self.logger.error(f"Failed to check toolset usage: {str(e)}")
            raise

    async def get_all_agents(self, user_id: str) -> List[Dict]:
        """Get all agents accessible to a user via individual or team access - flattened response"""
        try:
            query = f"""
            LET user_key = @user_id

            // Get user's teams
            LET user_teams = (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', user_key)
                    FILTER perm.type == "USER"
                    FILTER STARTS_WITH(perm._to, '{CollectionNames.TEAMS.value}/')
                    RETURN perm._to
            )

            // Get agents with individual access
            LET individual_agents = (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', user_key)
                    FILTER perm.type == "USER"
                    FILTER STARTS_WITH(perm._to, '{CollectionNames.AGENT_INSTANCES.value}/')
                    LET agent = DOCUMENT(perm._to)
                    FILTER agent != null
                    FILTER agent.isDeleted != true
                    RETURN MERGE(agent, {{
                        access_type: "INDIVIDUAL",
                        user_role: perm.role,
                        can_edit: perm.role IN ["OWNER", "WRITER", "ORGANIZER"],
                        can_delete: perm.role == "OWNER",
                        can_share: perm.role IN ["OWNER", "ORGANIZER"],
                        can_view: true
                    }})
            )

            // Get agents with team access (excluding those already found via individual access)
            LET individual_agent_ids = (FOR ind_agent IN individual_agents RETURN ind_agent._id)

            LET team_agents = (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from IN user_teams
                    FILTER perm.type == "TEAM"
                    FILTER STARTS_WITH(perm._to, '{CollectionNames.AGENT_INSTANCES.value}/')
                    FILTER perm._to NOT IN individual_agent_ids
                    LET agent = DOCUMENT(perm._to)
                    FILTER agent != null
                    FILTER agent.isDeleted != true
                    RETURN MERGE(agent, {{
                        access_type: "TEAM",
                        user_role: perm.role,
                        can_edit: perm.role IN ["OWNER", "WRITER", "ORGANIZER"],
                        can_delete: perm.role == "OWNER",
                        can_share: perm.role IN ["OWNER", "ORGANIZER"],
                        can_view: true
                    }})
            )

            // Flatten and return all agents
            FOR agent_result IN APPEND(individual_agents, team_agents)
                RETURN agent_result
            """

            bind_vars = {
                "user_id": user_id,
            }

            cursor = self.db.aql.execute(query, bind_vars=bind_vars)
            return list(cursor)

        except Exception as e:
            self.logger.error(f"Failed to get all agents: {str(e)}")
            return []

    async def update_agent(self, agent_id: str, agent_updates: Dict[str, Any], user_id: str) -> Optional[bool]:
        """
        Update an agent.

        New schema only allows: name, description, startMessage, systemPrompt, models (as keys), tags
        Tools, connectors, kb, vectorDBs are handled via edges, not in agent document.
        """
        try:
            # Check if user has permission to update the agent using the new method
            agent_with_permission = await self.get_agent(agent_id, user_id)
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
            # Note: tools, connectors, kb, vectorDBs are handled via edges, not agent document
            allowed_fields = ["name", "description", "startMessage", "systemPrompt", "tags", "isActive"]
            for field in allowed_fields:
                if field in agent_updates:
                    update_data[field] = agent_updates[field]

            # Handle models specially - convert model objects to model keys and deduplicate
            if "models" in agent_updates:
                raw_models = agent_updates["models"]
                if isinstance(raw_models, list):
                    model_entries = []
                    seen_entries = set()  # Track seen full entries to prevent duplicates

                    for model in raw_models:
                        model_key = None
                        model_name = None

                        # Extract model key and name from different input formats
                        if isinstance(model, dict):
                            model_key = model.get("modelKey")
                            model_name = model.get("modelName", "")
                        elif isinstance(model, str):
                            # Handle both "modelKey" and "modelKey_modelName" formats
                            if "_" in model:
                                parts = model.split("_", 1)
                                model_key = parts[0]
                                model_name = parts[1] if len(parts) > 1 else ""
                            else:
                                model_key = model
                                model_name = ""

                        # Build the entry string
                        if model_key:
                            # Store in same format as create: "modelKey_modelName" or just "modelKey"
                            if model_name:
                                entry = f"{model_key}_{model_name}"
                            else:
                                entry = model_key

                            # Only add if we haven't seen this exact entry before
                            # This allows same modelKey with different modelNames
                            if entry not in seen_entries:
                                model_entries.append(entry)
                                seen_entries.add(entry)

                    # Always set models array (even if empty) to completely replace existing one
                    update_data["models"] = model_entries
                elif raw_models is None:
                    # Explicitly set to empty array if None is provided
                    update_data["models"] = []

            # Update the agent using AQL UPDATE statement - Fixed to use proper collection name
            update_query = f"""
            UPDATE @agent_key
            WITH @update_data
            IN {CollectionNames.AGENT_INSTANCES.value}
            RETURN NEW
            """

            bind_vars = {
                "agent_key": agent_id,  # Use just the key, not the full document ID
                "update_data": update_data,
            }

            cursor = self.db.aql.execute(update_query, bind_vars=bind_vars)
            result = list(cursor)

            if not result or len(result) == 0:
                self.logger.error(f"Failed to update agent {agent_id}")
                return False

            self.logger.info(f"Successfully updated agent {agent_id}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to update agent: {str(e)}")
            return False

    async def delete_agent(self, agent_id: str, user_id: str) -> Optional[bool]:
        """Delete an agent"""
        try:
            # Check if agent exists
            agent = await self.get_document(agent_id, CollectionNames.AGENT_INSTANCES.value)
            if agent is None:
                self.logger.warning(f"Agent {agent_id} not found")
                return False

            # Check if user has permission to delete the agent using the new method
            agent_with_permission = await self.get_agent(agent_id, user_id)
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

            # Soft delete the agent using AQL UPDATE - Fixed to use f-string
            update_query = f"""
            UPDATE @agent_key
            WITH @update_data
            IN {CollectionNames.AGENT_INSTANCES.value}
            RETURN NEW
            """

            bind_vars = {
                "agent_key": agent_id,
                "update_data": update_data,
            }

            cursor = self.db.aql.execute(update_query, bind_vars=bind_vars)
            result = list(cursor)

            if not result or len(result) == 0:
                self.logger.error(f"Failed to delete agent {agent_id}")
                return False

            self.logger.info(f"Successfully deleted agent {agent_id}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to delete agent: {str(e)}")
            return False

    async def share_agent(self, agent_id: str, user_id: str, user_ids: Optional[List[str]], team_ids: Optional[List[str]]) -> Optional[bool]:
        """Share an agent to users and teams"""
        try:
            # Check if agent exists and user has permission to share it
            agent_with_permission = await self.get_agent(agent_id, user_id)
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
                    edge = {
                        "_from": f"{CollectionNames.USERS.value}/{user.get('_key')}",
                        "_to": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_id}",
                        "role": "READER",
                        "type": "USER",
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                    }
                    user_agent_edges.append(edge)

                result = await self.batch_create_edges(user_agent_edges, CollectionNames.PERMISSION.value)
                if not result:
                    self.logger.error(f"Failed to share agent {agent_id} to user {user_id_to_share}")
                    return False

            # Share the agent to teams
            team_agent_edges = []
            if team_ids:
                for team_id in team_ids:
                    team = await self.get_document(team_id, CollectionNames.TEAMS.value)
                    if team is None:
                        self.logger.warning(f"Team {team_id} not found")
                        continue
                    edge = {
                        "_from": f"{CollectionNames.TEAMS.value}/{team.get('_key')}",
                        "_to": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_id}",
                        "role": "READER",
                        "type": "TEAM",
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                    }
                    team_agent_edges.append(edge)
                result = await self.batch_create_edges(team_agent_edges, CollectionNames.PERMISSION.value)
                if not result:
                    self.logger.error(f"Failed to share agent {agent_id} to team {team_id}")
                    return False
            return True
        except Exception as e:
            self.logger.error("❌ Failed to share agent: %s", str(e), exc_info=True)
            return False

    async def unshare_agent(self, agent_id: str, user_id: str, user_ids: Optional[List[str]], team_ids: Optional[List[str]]) -> Optional[Dict]:
        """Unshare an agent from users and teams - direct deletion without validation"""
        try:
            # Check if user has permission to unshare the agent
            agent_with_permission = await self.get_agent(agent_id, user_id)
            if agent_with_permission is None or not agent_with_permission.get("can_share", False):
                return {"success": False, "reason": "Insufficient permissions to unshare agent"}

            # Build conditions for batch delete
            conditions = []
            bind_vars = {"agent_id": agent_id}

            if user_ids:
                conditions.append("(perm._from IN @user_froms AND perm.type == 'USER' AND perm.role != 'OWNER')")
                bind_vars["user_froms"] = [f"{CollectionNames.USERS.value}/{user_id}" for user_id in user_ids]

            if team_ids:
                conditions.append("(perm._from IN @team_froms AND perm.type == 'TEAM')")
                bind_vars["team_froms"] = [f"{CollectionNames.TEAMS.value}/{team_id}" for team_id in team_ids]

            if not conditions:
                return {"success": False, "reason": "No users or teams provided"}

            # Single batch delete query
            batch_delete_query = f"""
            FOR perm IN {CollectionNames.PERMISSION.value}
                FILTER perm._to == CONCAT('{CollectionNames.AGENT_INSTANCES.value}/', @agent_id)
                FILTER ({' OR '.join(conditions)})
                REMOVE perm IN {CollectionNames.PERMISSION.value}
                RETURN OLD._key
            """

            cursor = self.db.aql.execute(batch_delete_query, bind_vars=bind_vars)
            deleted_permissions = list(cursor)

            self.logger.info(f"Unshared agent {agent_id}: removed {len(deleted_permissions)} permissions")

            return {
                "success": True,
                "agent_id": agent_id,
                "deleted_permissions": len(deleted_permissions)
            }

        except Exception as e:
            self.logger.error("Failed to unshare agent: %s", str(e), exc_info=True)
            return {"success": False, "reason": f"Internal error: {str(e)}"}

    async def update_agent_permission(self, agent_id: str, owner_user_id: str, user_ids: Optional[List[str]], team_ids: Optional[List[str]], role: str) -> Optional[Dict]:
        """Update permission role for users and teams on an agent (only OWNER can do this)"""
        try:
            # Check if the requesting user is the OWNER of the agent
            agent_with_permission = await self.get_agent(agent_id, owner_user_id)
            if agent_with_permission is None:
                self.logger.warning(f"No permission found for user {owner_user_id} on agent {agent_id}")
                return {"success": False, "reason": "Agent not found or no permission"}

            # Only OWNER can update permissions - Fixed to use the flattened structure
            if agent_with_permission.get("user_role") != "OWNER":
                self.logger.warning(f"User {owner_user_id} is not the OWNER of agent {agent_id}")
                return {"success": False, "reason": "Only OWNER can update permissions"}

            # Build conditions for batch update
            conditions = []
            bind_vars = {
                "agent_id": agent_id,
                "new_role": role,
                "timestamp": get_epoch_timestamp_in_ms(),
            }

            if user_ids:
                conditions.append("(perm._from IN @user_froms AND perm.type == 'USER' AND perm.role != 'OWNER')")
                bind_vars["user_froms"] = [f"{CollectionNames.USERS.value}/{user_id}" for user_id in user_ids]

            if team_ids:
                conditions.append("(perm._from IN @team_froms AND perm.type == 'TEAM')")
                bind_vars["team_froms"] = [f"{CollectionNames.TEAMS.value}/{team_id}" for team_id in team_ids]

            if not conditions:
                return {"success": False, "reason": "No users or teams provided"}

            # Single batch update query
            batch_update_query = f"""
            FOR perm IN {CollectionNames.PERMISSION.value}
                FILTER perm._to == CONCAT('{CollectionNames.AGENT_INSTANCES.value}/', @agent_id)
                FILTER ({' OR '.join(conditions)})
                UPDATE perm WITH {{
                    role: @new_role,
                    updatedAtTimestamp: @timestamp
                }} IN {CollectionNames.PERMISSION.value}
                RETURN {{
                    _key: NEW._key,
                    _from: NEW._from,
                    type: NEW.type,
                    role: NEW.role
                }}
            """

            cursor = self.db.aql.execute(batch_update_query, bind_vars=bind_vars)
            updated_permissions = list(cursor)

            if not updated_permissions:
                self.logger.warning(f"No permission edges found to update for agent {agent_id}")
                return {"success": False, "reason": "No permissions found to update"}

            # Count updates by type
            updated_users = sum(1 for perm in updated_permissions if perm["type"] == "USER")
            updated_teams = sum(1 for perm in updated_permissions if perm["type"] == "TEAM")

            self.logger.info(f"Successfully updated {len(updated_permissions)} permissions for agent {agent_id} to role {role}")

            return {
                "success": True,
                "agent_id": agent_id,
                "new_role": role,
                "updated_permissions": len(updated_permissions),
                "updated_users": updated_users,
                "updated_teams": updated_teams
            }

        except Exception as e:
            self.logger.error(f"Failed to update agent permission: {str(e)}")
            return {"success": False, "reason": f"Internal error: {str(e)}"}

    async def get_agent_permissions(self, agent_id: str, user_id: str) -> Optional[List[Dict]]:
        """Get all permissions for an agent (only OWNER can view all permissions)"""
        try:
            # Check if user has access to the agent
            agent_with_permission = await self.get_agent(agent_id, user_id)
            if agent_with_permission is None:
                self.logger.warning(f"No permission found for user {user_id} on agent {agent_id}")
                return None

            # Only OWNER can view all permissions - Fixed to use the flattened structure
            if agent_with_permission.get("user_role") != "OWNER":
                self.logger.warning(f"User {user_id} is not the OWNER of agent {agent_id}")
                return None

            # Get all permissions for the agent
            query = f"""
            FOR perm IN {CollectionNames.PERMISSION.value}
                FILTER perm._to == CONCAT('{CollectionNames.AGENT_INSTANCES.value}/', @agent_id)
                LET entity = DOCUMENT(perm._from)
                FILTER entity != null
                RETURN {{
                    id: entity._key,
                    name: entity.fullName || entity.name || entity.userName,
                    userId: entity.userId,
                    email: entity.email,
                    role: perm.role,
                    type: perm.type,
                    createdAtTimestamp: perm.createdAtTimestamp,
                    updatedAtTimestamp: perm.updatedAtTimestamp
                }}
            """

            bind_vars = {
                "agent_id": agent_id,
            }
            cursor = self.db.aql.execute(query, bind_vars=bind_vars)
            result = list(cursor)

            return result

        except Exception as e:
            self.logger.error(f"Failed to get agent permissions: {str(e)}")
            return None


    async def get_users_with_permission_to_node(
        self,
        node_key: str,
        collection: str =  CollectionNames.PERMISSION.value,
        transaction: Optional[TransactionDatabase] = None
    ) -> List[User]:
        """
        Get all users that have permission edges to a specific node/record

        Args:
            node_key: The record/node key (e.g., "records/12345")
            collection: The edge collection name (defaults to "permission")
            transaction: Optional transaction database

        Returns:
            List[str]: List of user keys that have permissions to the node
        """
        try:
            self.logger.info("🚀 Getting users with permissions to node: %s from collection: %s", node_key, collection)

            query = f"""
            FOR edge IN @@collection
                FILTER edge._to == @node_key
                FOR user IN {CollectionNames.USERS.value}
                    FILTER user._id == edge._from
                    RETURN user
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"node_key": node_key, "@collection": collection})
            users = [User.from_arango_user(user_data) for user_data in cursor]

            if users:
                self.logger.info("✅ Found %d user(s) with permissions to node: %s", len(users), node_key)
            else:
                self.logger.warning("⚠️ No users found with permissions to node: %s in collection: %s", node_key, collection)

            return users

        except Exception as e:
            self.logger.error("❌ Failed to get users with permissions to node: %s in collection: %s: %s",
                            node_key, collection, str(e))
            return []


    async def get_first_user_with_permission_to_node(
        self,
        node_key: str,
        collection: str = CollectionNames.PERMISSION.value,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[User]:
        """
        Get the first user that has a permission edge to a specific node/record

        Args:
            node_key: The record/node key (e.g., "records/12345")
            collection: The edge collection name (defaults to "permission")
            transaction: Optional transaction database

        Returns:
            Optional[User]: User with permission to the node, or None if not found
        """
        try:
            self.logger.info("🚀 Getting first user with permission to node: %s from collection: %s", node_key, collection)

            query = f"""
            FOR edge IN @@collection
                FILTER edge._to == @node_key
                FOR user IN {CollectionNames.USERS.value}
                    FILTER user._id == edge._from
                    LIMIT 1
                    RETURN user
            """


            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"node_key": node_key, "@collection": collection})
            result = next(cursor, None)

            if result:
                user = User.from_arango_user(result)
                self.logger.info("✅ Found user with permission to node: %s -> %s", node_key, user.email)
                return user
            else:
                self.logger.warning("⚠️ No user found with permission to node: %s in collection: %s", node_key, collection)
                return None

        except Exception as e:
            self.logger.error("❌ Failed to get user with permission to node: %s in collection: %s: %s",
                            node_key, collection, str(e))
            return None

    async def get_first_user_with_permission_to_node2(
        self,
        node_id: str,
        graph_name: str = "knowledgeGraph",
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[User]:
        """
        Get the first user that has a permission edge to a specific node using a graph traversal.

        Args:
            node_id: The full record/node ID (e.g., "records/12345").
            graph_name: The name of the graph to traverse.
            transaction: Optional transaction database.

        Returns:
            Optional[User]: A User object with permission to the node, or None if not found.
        """
        try:
            self.logger.info("🚀 Getting first user with permission to node: %s in graph: %s", node_id, graph_name)

            # The graph name is safely injected via an f-string because it's a controlled identifier.
            # The collection name for filtering is passed as a bind parameter for best practice.
            query = f"""
            FOR user IN 1..1 INBOUND @node_id GRAPH '{graph_name}'
                OPTIONS {{ bfs: true, uniqueVertices: 'global' }}
                FILTER IS_SAME_COLLECTION(@users_collection, user)
                LIMIT 1
                RETURN user
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query,
                bind_vars={
                    "node_id": node_id,
                    "users_collection": CollectionNames.USERS.value
                }
            )
            result = next(cursor, None)

            if result:
                user = User.from_arango_user(result)
                self.logger.info("✅ Found user with permission to node: %s -> %s", node_id, user.email)
                return user
            else:
                self.logger.warning("⚠️ No user found with permission to node: %s in graph: %s", node_id, graph_name)
                return None

        except Exception as e:
            self.logger.error("❌ Failed to get user with permission to node: %s in graph: %s: %s",
                            node_id, graph_name, str(e))
            return None

    async def get_file_record_by_id(
        self, id: str, transaction: Optional[TransactionDatabase] = None
    ) -> Optional[FileRecord]:
        """
        Get file record using the id

        Args:
            id (str): The internal record ID (_key) to look up
            transaction (Optional[TransactionDatabase]): Optional database transaction

        Returns:
            Optional[FileRecord]: FileRecord object if found, None otherwise
        """
        try:
            self.logger.info("🚀 Retrieving file record for id %s", id)

            # Query both the file record and base record
            query = f"""
            LET file = DOCUMENT("{CollectionNames.FILES.value}", @id)
            LET record = DOCUMENT("{CollectionNames.RECORDS.value}", @id)
            FILTER file != null AND record != null
            RETURN {{
                file: file,
                record: record
            }}
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"id": id})
            result = next(cursor, None)
            if result and result.get("file") and result.get("record"):
                self.logger.info("✅ Successfully retrieved file record for id %s", id)
                return FileRecord.from_arango_record(
                    arango_base_file_record=result["file"],
                    arango_base_record=result["record"]
                )
            else:
                self.logger.warning("⚠️ No file record found for id %s", id)
                return None

        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve file record for id %s: %s", id, str(e)
            )
            return None

    # ========================================================================
    # Move Record API Methods
    # ========================================================================

    def is_record_descendant_of(
        self,
        ancestor_id: str,
        potential_descendant_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> bool:
        """
        Check if potential_descendant_id is a descendant of ancestor_id.
        Used to prevent circular references when moving folders.

        Args:
            ancestor_id: The folder being moved (record key)
            potential_descendant_id: The target destination (record key)
            transaction: Optional transaction

        Returns:
            bool: True if potential_descendant_id is under ancestor_id
        """
        query = """
        LET ancestor_doc_id = CONCAT("records/", @ancestor_id)

        // Traverse down from ancestor to find if descendant is reachable
        FOR v IN 1..100 OUTBOUND ancestor_doc_id recordRelations
            OPTIONS { bfs: true, uniqueVertices: "global" }
            FILTER v._key == @descendant_id
            LIMIT 1
            RETURN 1
        """
        try:
            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query,
                bind_vars={
                    "ancestor_id": ancestor_id,
                    "descendant_id": potential_descendant_id,
                }
            )
            result = list(cursor)
            is_descendant = len(result) > 0
            self.logger.debug(
                f"Circular reference check: {potential_descendant_id} is "
                f"{'a descendant' if is_descendant else 'not a descendant'} of {ancestor_id}"
            )
            return is_descendant
        except Exception as e:
            self.logger.error(f"Failed to check descendant relationship: {e}")
            return False

    def get_record_parent_info(
        self,
        record_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[Dict]:
        """
        Get the current parent information for a record.

        Args:
            record_id: The record key
            transaction: Optional transaction

        Returns:
            Dict with parentId, parentType ('record' or 'recordGroup'), or None if at root
        """
        query = """
        LET record_doc_id = CONCAT("records/", @record_id)

        // Find the incoming PARENT_CHILD edge
        LET parent_edge = FIRST(
            FOR edge IN recordRelations
                FILTER edge._to == record_doc_id
                FILTER edge.relationshipType == "PARENT_CHILD"
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
            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"record_id": record_id})
            result = next(cursor, None)
            return result if result else None
        except Exception as e:
            self.logger.error(f"Failed to get record parent info: {e}")
            return None

    def delete_parent_child_edge_to_record(
        self,
        record_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> int:
        """
        Delete all PARENT_CHILD edges pointing to a record.

        Args:
            record_id: The record key (target of the edge)
            transaction: Optional transaction

        Returns:
            int: Number of edges deleted
        """
        query = """
        LET record_doc_id = CONCAT("records/", @record_id)

        FOR edge IN recordRelations
            FILTER edge._to == record_doc_id
            FILTER edge.relationshipType == "PARENT_CHILD"
            REMOVE edge IN recordRelations
            RETURN OLD
        """
        try:
            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"record_id": record_id})
            result = list(cursor)
            deleted_count = len(result)
            self.logger.debug(f"Deleted {deleted_count} PARENT_CHILD edge(s) to record {record_id}")
            return deleted_count
        except Exception as e:
            self.logger.error(f"Failed to delete parent-child edge: {e}")
            if transaction:
                raise
            return 0

    def create_parent_child_edge(
        self,
        parent_id: str,
        child_id: str,
        parent_is_kb: bool,
        transaction: Optional[TransactionDatabase] = None
    ) -> bool:
        """
        Create a PARENT_CHILD edge from parent to child.

        Args:
            parent_id: The parent key (folder or KB)
            child_id: The child key (record being moved)
            parent_is_kb: True if parent is a KB (recordGroups), False if folder (records)
            transaction: Optional transaction

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
        } INTO recordRelations
        RETURN NEW
        """
        try:
            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query,
                bind_vars={
                    "parent_collection": parent_collection,
                    "parent_id": parent_id,
                    "child_id": child_id,
                    "timestamp": timestamp,
                }
            )
            result = list(cursor)
            success = len(result) > 0
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

    def update_record_external_parent_id(
        self,
        record_id: str,
        new_parent_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> bool:
        """
        Update the externalParentId field of a record.

        Args:
            record_id: The record key
            new_parent_id: The new parent ID (folder ID or KB ID)
            transaction: Optional transaction

        Returns:
            bool: True if updated successfully
        """
        timestamp = get_epoch_timestamp_in_ms()
        query = """
        UPDATE { _key: @record_id } WITH {
            externalParentId: @new_parent_id,
            updatedAtTimestamp: @timestamp
        } IN records
        RETURN NEW
        """
        try:
            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query,
                bind_vars={
                    "record_id": record_id,
                    "new_parent_id": new_parent_id,
                    "timestamp": timestamp,
                }
            )
            result = list(cursor)
            success = len(result) > 0
            if success:
                self.logger.debug(f"Updated externalParentId for record {record_id} to {new_parent_id}")
            return success
        except Exception as e:
            self.logger.error(f"Failed to update record externalParentId: {e}")
            if transaction:
                raise
            return False

    def is_record_folder(
        self,
        record_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> bool:
        """
        Check if a record is a folder (isFile=false in FILES collection).

        Args:
            record_id: The record key
            transaction: Optional transaction

        Returns:
            bool: True if the record is a folder
        """
        query = """
        LET record = DOCUMENT("records", @record_id)
        FILTER record != null

        LET file_info = FIRST(
            FOR edge IN isOfType
                FILTER edge._from == record._id
                LET f = DOCUMENT(edge._to)
                FILTER f != null AND f.isFile == false
                RETURN true
        )

        RETURN file_info == true
        """
        try:
            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"record_id": record_id})
            result = next(cursor, None)
            return result if result else False
        except Exception as e:
            self.logger.error(f"Failed to check if record is folder: {e}")
            return False
