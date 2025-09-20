"""ArangoDB service for interacting with the database"""

# pylint: disable=E1101, W0718
import asyncio
import uuid
from io import BytesIO
from typing import Dict, List, Optional, Tuple

import aiohttp
from arango import ArangoClient
from arango.database import TransactionDatabase
from fastapi import Request

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    DepartmentNames,
    GraphNames,
    LegacyGraphNames,
    OriginTypes,
    RecordTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.connectors.services.kafka_service import KafkaService
from app.models.entities import Record, RecordGroup, User
from app.schema.arango.documents import (
    agent_schema,
    agent_template_schema,
    app_schema,
    department_schema,
    file_record_schema,
    mail_record_schema,
    orgs_schema,
    record_group_schema,
    record_schema,
    team_schema,
    ticket_record_schema,
    user_schema,
    webpage_record_schema,
)
from app.schema.arango.edges import (
    basic_edge_schema,
    belongs_to_schema,
    is_of_type_schema,
    permissions_schema,
    record_relations_schema,
    user_app_relation_schema,
    user_drive_relation_schema,
)
from app.schema.arango.graph import EDGE_DEFINITIONS
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Collection definitions with their schemas
NODE_COLLECTIONS = [
    (CollectionNames.RECORDS.value, record_schema),
    (CollectionNames.DRIVES.value, None),
    (CollectionNames.FILES.value, file_record_schema),
    (CollectionNames.LINKS.value, None),
    (CollectionNames.MAILS.value, mail_record_schema),
    (CollectionNames.WEBPAGES.value, webpage_record_schema),
    (CollectionNames.PEOPLE.value, None),
    (CollectionNames.USERS.value, user_schema),
    (CollectionNames.GROUPS.value, None),
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
    (CollectionNames.TICKETS.value, ticket_record_schema),
    (CollectionNames.SYNC_POINTS.value, None),
    (CollectionNames.TEAMS.value, team_schema),
    (CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value, None)

]

EDGE_COLLECTIONS = [
    (CollectionNames.IS_OF_TYPE.value, is_of_type_schema),
    (CollectionNames.RECORD_RELATIONS.value, record_relations_schema),
    (CollectionNames.USER_DRIVE_RELATION.value, user_drive_relation_schema),
    (CollectionNames.BELONGS_TO_DEPARTMENT.value, basic_edge_schema),
    (CollectionNames.ORG_DEPARTMENT_RELATION.value, basic_edge_schema),
    (CollectionNames.BELONGS_TO.value, belongs_to_schema),
    (CollectionNames.PERMISSIONS.value, permissions_schema),
    (CollectionNames.ORG_APP_RELATION.value, basic_edge_schema),
    (CollectionNames.USER_APP_RELATION.value, user_app_relation_schema),
    (CollectionNames.BELONGS_TO_CATEGORY.value, basic_edge_schema),
    (CollectionNames.BELONGS_TO_LANGUAGE.value, basic_edge_schema),
    (CollectionNames.BELONGS_TO_TOPIC.value, basic_edge_schema),
    (CollectionNames.BELONGS_TO_RECORD_GROUP.value, basic_edge_schema),
    (CollectionNames.INTER_CATEGORY_RELATIONS.value, basic_edge_schema),
    (CollectionNames.PERMISSIONS_TO_KB.value, permissions_schema),
    (CollectionNames.PERMISSION.value, permissions_schema),
]

class BaseArangoService:
    """Base ArangoDB service class for interacting with the database"""

    def __init__(
        self, logger, arango_client: ArangoClient, config_service: ConfigurationService, kafka_service: KafkaService,
    ) -> None:
        self.logger = logger
        self.config_service = config_service
        self.client = arango_client
        self.kafka_service = kafka_service
        self.db = None

        self.connector_delete_permissions = {
            Connectors.GOOGLE_DRIVE.value: {
                "allowed_roles": ["OWNER", "WRITER", "FILEORGANIZER"],
                "edge_collections": [
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.PERMISSIONS.value,
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
                    CollectionNames.PERMISSIONS.value,
                    CollectionNames.BELONGS_TO.value,
                ],
                "document_collections": [
                    CollectionNames.RECORDS.value,
                    CollectionNames.MAILS.value,
                    CollectionNames.FILES.value,  # For attachments
                ]
            },
            Connectors.KNOWLEDGE_BASE.value: {
                "allowed_roles": ["OWNER", "WRITER", "FILEORGANIZER"],
                "edge_collections": [
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.BELONGS_TO.value,
                    CollectionNames.PERMISSIONS_TO_KB.value,
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
                        self.logger.warning(
                            f"Failed to update schema for {collection_name}: {str(e)}"
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
        """Connect to ArangoDB and initialize collections"""
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

            # Initialize collections with schema update handling
            try:
                # Initialize all collections (both nodes and edges)
                await self._initialize_new_collections()

                # Initialize or update the file access graph
                if not self.db.has_graph(LegacyGraphNames.FILE_ACCESS_GRAPH.value) and not self.db.has_graph(GraphNames.KNOWLEDGE_GRAPH.value):
                    # No graph exists, create new graph (Knowledge Graph)
                    await self._create_graph()
                else:
                    self.logger.info("Knowledge base graph already exists - skipping creation")

                # Initialize departments
                try:
                    await self._initialize_departments()
                except Exception as e:
                    self.logger.error("❌ Error initializing departments: %s", str(e))
                    raise

                return True

            except Exception as e:
                self.logger.error("❌ Error initializing collections: %s", str(e))
                raise

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

    async def get_document(self, document_key: str, collection: str) -> Optional[Dict]:
            """Get a document by its key"""
            try:
                query = """
                FOR doc IN @@collection
                    FILTER doc._key == @document_key
                    RETURN doc
                """
                cursor = self.db.aql.execute(
                    query,
                    bind_vars={"document_key": document_key, "@collection": collection},
                )
                result = list(cursor)
                return result[0] if result else None
            except Exception as e:
                self.logger.error("❌ Error getting document: %s", str(e))
                return None

    async def get_user_by_user_id(self, user_id: str) -> Optional[Dict]:
        """Get user by user ID"""
        try:
            query = f"""
                FOR user IN {CollectionNames.USERS.value}
                    FILTER user.userId == @user_id
                    RETURN user
            """
            cursor = self.db.aql.execute(query, bind_vars={"user_id": user_id})
            result = next(cursor, None)
            return result
        except Exception as e:
            self.logger.error(f"Error getting user by user ID: {str(e)}")
            return None

    async def get_connector_stats(
        self,
        org_id: str,
        connector: str,
    ) -> Dict:
        """
        Get connector statistics for a specific connector or knowledge base

        Args:
            org_id: Organization ID
            connector: Specific connector name (e.g., "GOOGLE_DRIVE", "SLACK").
                        If None, returns Knowledge Base stats
        """
        try:
            self.logger.info(f"Getting connector stats for organization: {org_id}, connector: {connector or 'KNOWLEDGE_BASE'}")

            db = self.db

            # Determine if we're querying Knowledge Base or a specific connector
            is_knowledge_base = connector == "KB"

            if is_knowledge_base:
                # Query for Knowledge Base (UPLOAD origin)
                query = """
                LET org_id = @org_id

                // Get all upload records for the organization
                LET records = (
                    FOR doc IN @@records
                        FILTER doc.orgId == org_id
                        FILTER doc.origin == "UPLOAD"
                        FILTER doc.recordType != @drive_record_type
                        FILTER doc.isDeleted != true
                        RETURN doc
                )

                // Overall stats
                LET total_stats = {
                    total: LENGTH(records),
                    indexing_status: {
                        NOT_STARTED: LENGTH(records[* FILTER CURRENT.indexingStatus == "NOT_STARTED"]),
                        IN_PROGRESS: LENGTH(records[* FILTER CURRENT.indexingStatus == "IN_PROGRESS"]),
                        COMPLETED: LENGTH(records[* FILTER CURRENT.indexingStatus == "COMPLETED"]),
                        FAILED: LENGTH(records[* FILTER CURRENT.indexingStatus == "FAILED"]),
                        FILE_TYPE_NOT_SUPPORTED: LENGTH(records[* FILTER CURRENT.indexingStatus == "FILE_TYPE_NOT_SUPPORTED"]),
                        AUTO_INDEX_OFF: LENGTH(records[* FILTER CURRENT.indexingStatus == "AUTO_INDEX_OFF"])
                    }
                }

                // Record type breakdown
                LET by_record_type = (
                    FOR record_type IN UNIQUE(records[*].recordType)
                        FILTER record_type != null
                        LET type_records = records[* FILTER CURRENT.recordType == record_type]
                        RETURN {
                            record_type: record_type,
                            total: LENGTH(type_records),
                            indexing_status: {
                                NOT_STARTED: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "NOT_STARTED"]),
                                IN_PROGRESS: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "IN_PROGRESS"]),
                                COMPLETED: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "COMPLETED"]),
                                FAILED: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "FAILED"]),
                                FILE_TYPE_NOT_SUPPORTED: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "FILE_TYPE_NOT_SUPPORTED"]),
                                AUTO_INDEX_OFF: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "AUTO_INDEX_OFF"])
                            }
                        }
                )

                RETURN {
                    org_id: org_id,
                    connector: "KNOWLEDGE_BASE",
                    origin: "UPLOAD",
                    stats: total_stats,
                    by_record_type: by_record_type
                }
                """

                bind_vars = {
                    "org_id": org_id,
                    "@records": CollectionNames.RECORDS.value,
                    "drive_record_type": RecordTypes.DRIVE.value,
                }
            else:
                connector = connector.upper()
                # Query for specific connector (CONNECTOR origin)
                query = """
                LET org_id = @org_id
                LET connector = @connector

                // Get all records for the specific connector
                LET records = (
                    FOR doc IN @@records
                        FILTER doc.orgId == org_id
                        FILTER doc.origin == "CONNECTOR"
                        FILTER doc.connectorName == connector
                        FILTER doc.recordType != @drive_record_type
                        FILTER doc.isDeleted != true
                        RETURN doc
                )

                // Overall stats
                LET total_stats = {
                    total: LENGTH(records),
                    indexing_status: {
                        NOT_STARTED: LENGTH(records[* FILTER CURRENT.indexingStatus == "NOT_STARTED"]),
                        IN_PROGRESS: LENGTH(records[* FILTER CURRENT.indexingStatus == "IN_PROGRESS"]),
                        COMPLETED: LENGTH(records[* FILTER CURRENT.indexingStatus == "COMPLETED"]),
                        FAILED: LENGTH(records[* FILTER CURRENT.indexingStatus == "FAILED"]),
                        FILE_TYPE_NOT_SUPPORTED: LENGTH(records[* FILTER CURRENT.indexingStatus == "FILE_TYPE_NOT_SUPPORTED"]),
                        AUTO_INDEX_OFF: LENGTH(records[* FILTER CURRENT.indexingStatus == "AUTO_INDEX_OFF"])
                    }
                }

                // Record type breakdown
                LET by_record_type = (
                    FOR record_type IN UNIQUE(records[*].recordType)
                        FILTER record_type != null
                        LET type_records = records[* FILTER CURRENT.recordType == record_type]
                        RETURN {
                            record_type: record_type,
                            total: LENGTH(type_records),
                            indexing_status: {
                                NOT_STARTED: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "NOT_STARTED"]),
                                IN_PROGRESS: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "IN_PROGRESS"]),
                                COMPLETED: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "COMPLETED"]),
                                FAILED: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "FAILED"]),
                                FILE_TYPE_NOT_SUPPORTED: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "FILE_TYPE_NOT_SUPPORTED"]),
                                AUTO_INDEX_OFF: LENGTH(type_records[* FILTER CURRENT.indexingStatus == "AUTO_INDEX_OFF"])
                            }
                        }
                )

                RETURN {
                    org_id: org_id,
                    connector: connector,
                    origin: "CONNECTOR",
                    stats: total_stats,
                    by_record_type: by_record_type
                }
                """

                bind_vars = {
                    "org_id": org_id,
                    "connector": connector,
                    "@records": CollectionNames.RECORDS.value,
                    "drive_record_type": RecordTypes.DRIVE.value,
                }

            # Execute the query
            cursor = db.aql.execute(query, bind_vars=bind_vars)
            result = next(cursor, None)

            if result:
                connector_display = connector or "KNOWLEDGE_BASE"
                self.logger.info(f"Retrieved stats for {connector_display} in organization: {org_id}")
                return {
                    "success": True,
                    "data": result
                }
            else:
                self.logger.warning(f"No data found for connector: {connector or 'KNOWLEDGE_BASE'} in organization: {org_id}")
                return {
                    "success": False,
                    "message": "No data found for the specified connector",
                    "data": {
                        "org_id": org_id,
                        "connector": connector or "KNOWLEDGE_BASE",
                        "origin": "UPLOAD" if is_knowledge_base else "CONNECTOR",
                        "stats": {
                            "total": 0,
                            "indexing_status": {
                                "NOT_STARTED": 0,
                                "IN_PROGRESS": 0,
                                "COMPLETED": 0,
                                "FAILED": 0,
                                "FILE_TYPE_NOT_SUPPORTED": 0,
                                "AUTO_INDEX_OFF": 0
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
            LET directAccess = (
                FOR records, edge IN 1..1 ANY userDoc._id {CollectionNames.PERMISSIONS.value}
                FILTER records._key == @recordId
                RETURN {{
                    type: 'DIRECT',
                    source: userDoc,
                    role: edge.role
                }}
            )
            LET groupAccess = (
                FOR group, belongsEdge IN 1..1 ANY userDoc._id {CollectionNames.BELONGS_TO.value}
                FILTER belongsEdge.entityType == 'GROUP'
                FOR records, permEdge IN 1..1 ANY group._id {CollectionNames.PERMISSIONS.value}
                FILTER records._key == @recordId
                RETURN {{
                    type: 'GROUP',
                    source: group,
                    role: permEdge.role
                }}
            )
            LET orgAccess = (
                FOR org, belongsEdge IN 1..1 ANY userDoc._id {CollectionNames.BELONGS_TO.value}
                FILTER belongsEdge.entityType == 'ORGANIZATION'
                FOR records, permEdge IN 1..1 ANY org._id {CollectionNames.PERMISSIONS.value}
                FILTER records._key == @recordId
                RETURN {{
                    type: 'ORGANIZATION',
                    source: org,
                    role: permEdge.role
                }}
            )
            LET kbAccess = kb ? (
                FOR permEdge IN @@permissions_to_kb
                    FILTER permEdge._from == userDoc._id AND permEdge._to == kb._id
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
                directAccess,
                groupAccess,
                orgAccess,
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
                "@permissions_to_kb": CollectionNames.PERMISSIONS_TO_KB.value,
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
                if access["type"] == "KNOWLEDGE_BASE":
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
                final_kb_roles = list(base_kb_roles.intersection(set(permissions)))
                if not final_kb_roles:
                    include_kb_records = False
            else:
                final_kb_roles = list(base_kb_roles)

            # Build permission filter for connector records
            def build_permission_filter(include_filter_vars: bool = True) -> str:
                if permissions and include_filter_vars:
                    return " AND permissionEdge.role IN @permissions"
                return ""

            # ===== MAIN QUERY (with pagination and filters and file/mail records) =====
            record_filter = build_record_filters(True)
            permission_filter = build_permission_filter(True)

            main_query = f"""
            LET user_from = @user_from
            LET org_id = @org_id

            // KB Records Section - Get records DIRECTLY from belongs_to edges (not through folders)
            LET kbRecords = {
                f'''(
                    FOR kbEdge IN @@permissions_to_kb
                        FILTER kbEdge._from == user_from
                        FILTER kbEdge.type == "USER"
                        FILTER kbEdge.role IN @kb_permissions
                        LET kb = DOCUMENT(kbEdge._to)
                        FILTER kb != null AND kb.orgId == org_id
                        // Get records that belong directly to the KB
                        FOR belongsEdge IN @@belongs_to
                            FILTER belongsEdge._to == kb._id
                            LET record = DOCUMENT(belongsEdge._from)
                            FILTER record != null
                            FILTER record.isDeleted != true
                            FILTER record.orgId == org_id OR record.orgId == null
                            FILTER record.origin == "UPLOAD"
                            // Only include actual records (not folders)
                            FILTER record.isFile != false
                            {record_filter}
                            RETURN {{
                                record: record,
                                permission: {{ role: kbEdge.role, type: kbEdge.type }},
                                kb_id: kb._key,
                                kb_name: kb.groupName
                            }}
                )''' if include_kb_records else '[]'
            }

            // Connector Records Section - Direct connector permissions
            LET connectorRecords = {
                f'''(
                    FOR permissionEdge IN @@permissions
                        FILTER permissionEdge._to == user_from
                        FILTER permissionEdge.type == "USER"
                        {permission_filter}
                        LET record = DOCUMENT(permissionEdge._from)
                        FILTER record != null
                        FILTER record.recordType != @drive_record_type
                        FILTER record.isDeleted != true
                        FILTER record.orgId == org_id OR record.orgId == null
                        FILTER record.origin == "CONNECTOR"
                        {record_filter}
                        RETURN {{
                            record: record,
                            permission: {{ role: permissionEdge.role, type: permissionEdge.type }}
                        }}
                )''' if include_connector_records else '[]'
            }

            LET allRecords = APPEND(kbRecords, connectorRecords)
            LET sortedRecords = (
                FOR item IN allRecords
                    LET record = item.record
                    SORT record.{sort_by} {sort_order.upper()}
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
                    fileRecord: LENGTH(fileRecord) > 0 ? fileRecord[0] : null,
                    mailRecord: LENGTH(mailRecord) > 0 ? mailRecord[0] : null,
                    permission: {{role: item.permission.role, type: item.permission.type}},
                    kb: {{id: item.kb_id || null, name: item.kb_name || null }}
                }}
            """

            # ===== COUNT QUERY (Fixed) =====
            count_query = f"""
            LET user_from = @user_from
            LET org_id = @org_id

            LET kbCount = {
                f'''LENGTH(
                    FOR kbEdge IN @@permissions_to_kb
                        FILTER kbEdge._from == user_from
                        FILTER kbEdge.type == "USER"
                        FILTER kbEdge.role IN @kb_permissions
                        LET kb = DOCUMENT(kbEdge._to)
                        FILTER kb != null AND kb.orgId == org_id
                        FOR belongsEdge IN @@belongs_to
                            FILTER belongsEdge._to == kb._id
                            LET record = DOCUMENT(belongsEdge._from)
                            FILTER record != null
                            FILTER record.isDeleted != true
                            FILTER record.orgId == org_id OR record.orgId == null
                            FILTER record.origin == "UPLOAD"
                            FILTER record.isFile != false
                            {record_filter}
                            RETURN 1
                )''' if include_kb_records else '0'
            }

            LET connectorCount = {
                f'''LENGTH(
                    FOR permissionEdge IN @@permissions
                        FILTER permissionEdge._to == user_from
                        FILTER permissionEdge.type == "USER"
                        {permission_filter}
                        LET record = DOCUMENT(permissionEdge._from)
                        FILTER record != null
                        FILTER record.recordType != @drive_record_type
                        FILTER record.isDeleted != true
                        FILTER record.orgId == org_id OR record.orgId == null
                        FILTER record.origin == "CONNECTOR"
                        {record_filter}
                        RETURN 1
                )''' if include_connector_records else '0'
            }

            RETURN kbCount + connectorCount
            """

            # ===== FILTERS QUERY (Fixed) =====
            filters_query = f"""
            LET user_from = @user_from
            LET org_id = @org_id

            LET allKbRecords = {
                '''(
                    FOR kbEdge IN @@permissions_to_kb
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
                            FILTER record.isFile != false
                            RETURN {
                                record: record,
                                permission: { role: kbEdge.role }
                            }
                )''' if include_kb_records else '[]'
            }

            LET allConnectorRecords = {
                '''(
                    FOR permissionEdge IN @@permissions
                        FILTER permissionEdge._to == user_from
                        FILTER permissionEdge.type == "USER"
                        LET record = DOCUMENT(permissionEdge._from)
                        FILTER record != null
                        FILTER record.recordType != @drive_record_type
                        FILTER record.isDeleted != true
                        FILTER record.orgId == org_id OR record.orgId == null
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
                "@permissions_to_kb": CollectionNames.PERMISSIONS_TO_KB.value,
                "@permissions": CollectionNames.PERMISSIONS.value,
                "@belongs_to": CollectionNames.BELONGS_TO.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                "drive_record_type": RecordTypes.DRIVE.value,
                **filter_bind_vars,
            }

            count_bind_vars = {
                "user_from": f"users/{user_id}",
                "org_id": org_id,
                "kb_permissions": final_kb_roles,
                "@permissions_to_kb": CollectionNames.PERMISSIONS_TO_KB.value,
                "@permissions": CollectionNames.PERMISSIONS.value,
                "@belongs_to": CollectionNames.BELONGS_TO.value,
                "drive_record_type": RecordTypes.DRIVE.value,
                **filter_bind_vars,
            }

            filters_bind_vars = {
                "user_from": f"users/{user_id}",
                "org_id": org_id,
                "@permissions_to_kb": CollectionNames.PERMISSIONS_TO_KB.value,
                "@permissions": CollectionNames.PERMISSIONS.value,
                "@belongs_to": CollectionNames.BELONGS_TO.value,
                "drive_record_type": RecordTypes.DRIVE.value,
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

    async def get_user_kb_permission(
        self,
        kb_id: str,
        user_id: str,
    ) -> Optional[str]:
        """Validate user knowledge permission"""
        try:
            self.logger.info(f"🔍 Checking permissions for user {user_id} on KB {kb_id}")

            query = """
            FOR perm IN @@permissions_collection
                FILTER perm._from == CONCAT('users/', @user_id)
                FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                RETURN perm
            """

            cursor = self.db.aql.execute(
                query,
                bind_vars={
                    "kb_id": kb_id,
                    "user_id": user_id,
                    "@permissions_collection": CollectionNames.PERMISSIONS_TO_KB.value,
                },
            )

            permission = next(cursor, None)

            if permission:
                role = permission.get("role")
                self.logger.info(f"✅ Found permission: user {user_id} has role '{role}' on KB {kb_id}")
                return role
            else:
                self.logger.warning(f"⚠️ No permission found for user {user_id} on KB {kb_id}")

                # Debug: Let's see what permissions exist for this KB
                debug_query = """
                FOR perm IN @@permissions_collection
                    FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                    RETURN {
                        from: perm._from,
                        role: perm.role,
                        type: perm.type
                    }
                """
                debug_cursor = self.db.aql.execute(
                    debug_query,
                    bind_vars={
                        "kb_id": kb_id,
                        "@permissions_collection": CollectionNames.PERMISSIONS_TO_KB.value,
                    },
                )
                existing_perms = list(debug_cursor)
                self.logger.info(f"🔍 Debug - All permissions for KB {kb_id}: {existing_perms}")

                return None

        except Exception as e:
            self.logger.error(f"❌ Failed to validate knowledge base permission for user {user_id}: {str(e)}")
            raise

    async def reindex_single_record(self, record_id: str, user_id: str, org_id: str, request: Request) -> Dict:
        """
        Reindex a single record with permission checks and event publishing
        """
        try:
            self.logger.info(f"🔄 Starting reindex for record {record_id} by user {user_id}")

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
            origin = record.get("origin", "")

            self.logger.info(f"📋 Record details - Origin: {origin}, Connector: {connector_name}")

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
                if connector_name == Connectors.GOOGLE_DRIVE.value:
                    user_role = await self._check_drive_permissions(record_id, user_key)
                elif connector_name == Connectors.GOOGLE_MAIL.value:
                    user_role = await self._check_gmail_permissions(record_id, user_key)
                elif connector_name in (Connectors.ONEDRIVE.value, Connectors.SHAREPOINT_ONLINE.value):
                    user_role = await self._check_drive_permissions(record_id, user_key)
                else:
                    user_role = None

                if not user_role or user_role not in ["OWNER", "WRITER","READER"]:
                    return {
                        "success": False,
                        "code": 403,
                        "reason": f"Insufficient permissions. User role: {user_role}. Required: OWNER, WRITER, READER"
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
            # Create and publish reindex event
            try:
                payload = await self._create_reindex_event_payload(record, file_record,user_id,request)
                await self._publish_record_event("newRecord",payload)

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

            # Create and publish single reindexFailed event
            try:
                payload = await self._create_reindex_failed_event_payload(
                    org_id, connector, origin
                )
                await self._publish_sync_event("reindexFailed", payload)

                self.logger.info(f"✅ Published reindexFailed event for {connector}")

                return {
                    "success": True,
                    "connector": connector,
                    "origin": origin,
                    "user_permission_level": permission_check["permission_level"],
                    "event_published": True,
                    "message": f"Successfully initiated reindex of failed {connector} records"
                }

            except Exception as event_error:
                self.logger.error(f"❌ Failed to publish reindexFailed event: {str(event_error)}")
                return {
                    "success": False,
                    "code": 500,
                    "reason": f"Failed to publish reindexFailed event: {str(event_error)}"
                }

        except Exception as e:
            self.logger.error(f"❌ Failed to reindex failed connector records: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": f"Internal error: {str(e)}"
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

    async def delete_record_by_external_id(self, connector_name: Connectors, external_id: str) -> None:
        """
        Delete a record by external ID
        """
        try:
            self.logger.info(f"🗂️ Deleting record {external_id} from {connector_name}")

            # Get record
            record = await self.get_record_by_external_id(connector_name, external_id)
            if not record:
                self.logger.warning(f"⚠️ Record {external_id} not found in {connector_name}")
                return

            # Delete record
            await self.delete_record(record["key"])

            self.logger.info(f"✅ Record {external_id} deleted from {connector_name}")
        except Exception as e:
            self.logger.error(f"❌ Failed to delete record {external_id} from {connector_name}: {str(e)}")
            raise

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
            CollectionNames.PERMISSIONS.value: {
                "filter": "edge._from == @record_from",
                "bind_vars": {"record_from": f"records/{record_id}"},
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
            CollectionNames.PERMISSIONS.value: {
                "filter": "edge._from == @record_from",
                "bind_vars": {"record_from": f"records/{record_id}"},
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

    async def _check_connector_reindex_permissions(self, user_key: str, org_id: str, connector: str, origin: str) -> Dict:
        """
        Simple permission check for connector reindex operations
        Permission rules:
        1. Organization OWNER - Can reindex any connector
        2. Knowledge Base OWNER - Can reindex KB records only
        3. Users with significant connector access (≥50% of records)
        """
        try:
            self.logger.info(f"🔍 Checking connector reindex permissions for user {user_key}")

            permission_query = """
            LET user = DOCUMENT("users", @user_key)
            FILTER user != null
            // Check organization ownership
            LET org_owner = FIRST(
                FOR edge IN @@belongs_to
                    FILTER edge._from == user._id
                    FILTER edge._to == CONCAT('organizations/', @org_id)
                    FILTER edge.entityType == 'ORGANIZATION'
                    FILTER edge.role == 'OWNER'
                    RETURN edge
            )
            // Check if user is KB owner (for KB connectors)
            LET kb_owner_count = @origin == 'UPLOAD' ? (
                LENGTH(
                    FOR perm IN @@permissions_to_kb
                        FILTER perm._from == user._id
                        FILTER perm.role == 'OWNER'
                        LET kb = DOCUMENT(perm._to)
                        FILTER kb != null AND kb.orgId == @org_id
                        RETURN perm
                )
            ) : 0
            // Check connector-specific permissions (simplified count)
            LET connector_access_count = @origin == 'CONNECTOR' ? (
                LENGTH(
                    FOR perm IN @@permissions
                        FILTER perm._to == user._id
                        FILTER perm.role IN ['OWNER', 'WRITER']
                        LET record = DOCUMENT(perm._from)
                        FILTER record != null
                        FILTER record.orgId == @org_id
                        FILTER record.connectorName == @connector
                        FILTER record.origin == @origin
                        RETURN perm
                )
            ) : 0
            // Get total connector records for percentage calculation
            LET total_connector_records = @origin == 'CONNECTOR' ? (
                LENGTH(
                    FOR record IN @@records
                        FILTER record.orgId == @org_id
                        FILTER record.connectorName == @connector
                        FILTER record.origin == @origin
                        FILTER record.isDeleted != true
                        RETURN record
                )
            ) : (
                LENGTH(
                    FOR record IN @@records
                        FILTER record.orgId == @org_id
                        FILTER record.origin == @origin
                        FILTER record.isDeleted != true
                        RETURN record
                )
            )
            LET access_percentage = total_connector_records > 0 ?
                (connector_access_count * 100.0 / total_connector_records) : 0
            // Determine permission level
            LET permission_level = (
                org_owner ? 'ORGANIZATION_OWNER' :
                kb_owner_count > 0 ? 'KB_OWNER' :
                access_percentage >= 50 ? 'SUFFICIENT_ACCESS' :
                'INSUFFICIENT_ACCESS'
            )
            // Simple permission logic
            LET allowed = (
                org_owner != null OR
                (kb_owner_count > 0 AND @origin == 'UPLOAD') OR
                (@origin == 'CONNECTOR' AND access_percentage >= 50)
            )
            RETURN {
                allowed: allowed,
                permission_level: permission_level,
                access_percentage: access_percentage,
                total_records: total_connector_records,
                accessible_records: connector_access_count,
                reason: !allowed ? (
                    @origin == 'UPLOAD' ? 'User must be a Knowledge Base owner to reindex KB records' :
                    access_percentage < 50 ? 'User has insufficient access to connector records (less than 50%)' :
                    'User has no permission to reindex connector records'
                ) : 'Permission granted'
            }
            """

            cursor = self.db.aql.execute(permission_query, bind_vars={
                "user_key": user_key,
                "org_id": org_id,
                "connector": connector,
                "origin": origin,
                "@belongs_to": CollectionNames.BELONGS_TO.value,
                "@permissions_to_kb": CollectionNames.PERMISSIONS_TO_KB.value,
                "@permissions": CollectionNames.PERMISSIONS.value,
                "@records": CollectionNames.RECORDS.value,
            })

            result = next(cursor, {})

            if result.get("allowed"):
                self.logger.info(f"✅ Permission granted for connector reindex: {result['permission_level']}")
            else:
                self.logger.warning(f"⚠️ Permission denied for connector reindex: {result.get('reason')}")

            return result

        except Exception as e:
            self.logger.error(f"❌ Error checking connector reindex permissions: {str(e)}")
            return {
                "allowed": False,
                "reason": f"Permission check failed: {str(e)}",
                "permission_level": "ERROR"
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
                FOR perm IN @@permissions
                    FILTER perm._from == record_from
                    FILTER perm._to == user_from
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
                    FOR perm IN @@permissions
                        FILTER perm._from == record_from
                        FILTER perm._to == group._id
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
                    FOR perm IN @@permissions
                        FILTER perm._from == record_from
                        FILTER perm._to == org._id
                        FILTER perm.type == "DOMAIN"
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
                "@permissions": CollectionNames.PERMISSIONS.value,
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
                FOR perm IN @@permissions
                    FILTER perm._from == record_from
                    FILTER perm._to == user_from
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
                    FOR perm IN @@permissions
                        FILTER perm._from == record_from
                        FILTER perm._to == group._id
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
                    FOR perm IN @@permissions
                        FILTER perm._from == record_from
                        FILTER perm._to == org._id
                        FILTER perm.type == "DOMAIN"
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
                "@permissions": CollectionNames.PERMISSIONS.value,
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
                    url = f"{connector_url}/api/v1/stream/record/{record['_key']}"
                    file_content_bytes = await self._download_from_signed_url(url,request)
                    mime_type = "text/gmail_content"
                    # Convert bytes to string for JSON serialization
                    try:
                        # For mail content, decode as UTF-8 text
                        file_content = file_content_bytes.decode('utf-8', errors='replace')
                    except Exception as decode_error:
                        self.logger.warning(f"Failed to decode file content as UTF-8: {str(decode_error)}")
                        # Fallback: encode as base64 string for binary content
                        import base64
                        file_content = base64.b64encode(file_content_bytes).decode('utf-8')



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
                "createdAtTimestamp": str(record.get("createdAtTimestamp", get_epoch_timestamp_in_ms())),
                "updatedAtTimestamp": str(get_epoch_timestamp_in_ms()),
                "sourceCreatedAtTimestamp": str(record.get("sourceCreatedAtTimestamp", record.get("createdAtTimestamp", get_epoch_timestamp_in_ms())))
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to create reindex event payload: {str(e)}")
            raise

    async def _create_reindex_failed_event_payload(self, orgId:str, connector: str, origin: str) -> Dict:
        """Create reindex connector records event payload"""
        try:

            return {
                "orgId": orgId,
                "origin": origin,
                "connector": connector,
                "createdAtTimestamp": str(get_epoch_timestamp_in_ms()),
                "updatedAtTimestamp": str(get_epoch_timestamp_in_ms()),
                "sourceCreatedAtTimestamp": str(get_epoch_timestamp_in_ms())
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

            await self.kafka_service.publish_event("sync-events", event)
            self.logger.info(f"✅ Published {event_type} event for record {payload.get('recordId')}")

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

            await self.kafka_service.publish_event("record-events", event)
            self.logger.info(f"✅ Published {event_type} event for record {payload.get('recordId')}")

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

    async def get_record_by_external_id(
        self, connector_name: Connectors, external_id: str, transaction: Optional[TransactionDatabase] = None
    ) -> Optional[Record]:
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
                "🚀 Retrieving internal key for external file ID %s %s", connector_name, external_id
            )

            query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record.externalRecordId == @external_id AND record.connectorName == @connector_name
                RETURN record
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query, bind_vars={"external_id": external_id, "connector_name": connector_name.value}
            )
            result = next(cursor, None)

            if result:
                self.logger.info(
                    "✅ Successfully retrieved internal key for external file ID %s %s", connector_name, external_id
                )
                return Record.from_arango_base_record(result)
            else:
                self.logger.warning(
                    "⚠️ No internal key found for external file ID %s %s", connector_name, external_id
                )
                return None

        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve internal key for external file ID %s %s: %s", connector_name, external_id, str(e)
            )
            return None

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
                RETURN record
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
                return Record.from_arango_base_record(result)
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

    async def get_record_group_by_external_id(self, connector_name: Connectors, external_id: str, transaction: Optional[TransactionDatabase] = None) -> Optional[RecordGroup]:
        """
        Get internal record group key using the external record group ID
        """
        try:
            self.logger.info(
                "🚀 Retrieving internal key for external record group ID %s %s", connector_name, external_id
            )
            query = f"""
            FOR record_group IN {CollectionNames.RECORD_GROUPS.value}
                FILTER record_group.externalGroupId == @external_id AND record_group.connectorName == @connector_name
                RETURN record_group
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars={"external_id": external_id, "connector_name": connector_name.value})
            result = next(cursor, None)
            if result:
                self.logger.info(
                    "✅ Successfully retrieved internal key for external record group ID %s %s", connector_name, external_id
                )
                return RecordGroup.from_arango_base_record_group(result)
            else:
                self.logger.warning(
                    "⚠️ No internal key found for external record group ID %s %s", connector_name, external_id
                )
                return None
        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve internal key for external record group ID %s %s: %s", connector_name, external_id, str(e)
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

            query = """
            UPSERT { syncPointKey: @sync_point_key }
            INSERT @document_data
            UPDATE @document_data
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
            self.logger.info("🚀 Deleting nodes by keys: %s", keys)
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
