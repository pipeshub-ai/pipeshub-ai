"""ArangoDB service for interacting with the database"""

# pylint: disable=E1101, W0718
import uuid

from arango import ArangoClient

from app.config.configuration_service import ConfigurationService, config_node_constants
from app.config.utils.named_constants.arangodb_constants import (
    CollectionNames,
    DepartmentNames,
)
from app.schema.arango.documents import (
    app_schema,
    department_schema,
    file_record_schema,
    kb_schema,
    mail_record_schema,
    orgs_schema,
    record_schema,
    user_schema,
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

# Collection definitions with their schemas
NODE_COLLECTIONS = [
    (CollectionNames.RECORDS.value, record_schema),
    (CollectionNames.DRIVES.value, None),
    (CollectionNames.FILES.value, file_record_schema),
    (CollectionNames.LINKS.value, None),
    (CollectionNames.MAILS.value, mail_record_schema),
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
    (CollectionNames.KNOWLEDGE_BASE.value, kb_schema),
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
    (CollectionNames.INTER_CATEGORY_RELATIONS.value, basic_edge_schema),
    (CollectionNames.BELONGS_TO_KNOWLEDGE_BASE.value, belongs_to_schema),
    (CollectionNames.PERMISSIONS_TO_KNOWLEDGE_BASE.value, permissions_schema),
]

class BaseArangoService:
    """Base ArangoDB service class for interacting with the database"""

    def __init__(
        self, logger, arango_client: ArangoClient, config: ConfigurationService
    ):
        self.logger = logger
        self.config_service = config
        self.client = arango_client
        self.db = None

        # Initialize collections dictionary
        self._collections = {
            collection_name: None
            for collection_name, _ in NODE_COLLECTIONS + EDGE_COLLECTIONS
        }

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

                # Create the permissions graph if it doesn't exist
                if not self.db.has_graph(CollectionNames.FILE_ACCESS_GRAPH.value):
                    self.logger.info("🚀 Creating file access graph...")
                    graph = self.db.create_graph(CollectionNames.FILE_ACCESS_GRAPH.value)

                    # Define edge definitions
                    edge_definitions = [
                        {
                            "edge_collection": CollectionNames.PERMISSIONS.value,
                            "from_vertex_collections": [CollectionNames.RECORDS.value],
                            "to_vertex_collections": [
                                CollectionNames.USERS.value,
                                CollectionNames.GROUPS.value,
                                CollectionNames.ORGS.value,
                            ],
                        },
                        {
                            "edge_collection": CollectionNames.BELONGS_TO.value,
                            "from_vertex_collections": [CollectionNames.USERS.value],
                            "to_vertex_collections": [
                                CollectionNames.GROUPS.value,
                                CollectionNames.ORGS.value,
                            ],
                        },
                        {
                            "edge_collection": CollectionNames.ORG_DEPARTMENT_RELATION.value,
                            "from_vertex_collections": [CollectionNames.ORGS.value],
                            "to_vertex_collections": [CollectionNames.DEPARTMENTS.value],
                        },
                        {
                            "edge_collection": CollectionNames.BELONGS_TO_DEPARTMENT.value,
                            "from_vertex_collections": [CollectionNames.RECORDS.value],
                            "to_vertex_collections": [CollectionNames.DEPARTMENTS.value],
                        },
                        {
                            "edge_collection": CollectionNames.BELONGS_TO_CATEGORY.value,
                            "from_vertex_collections": [CollectionNames.RECORDS.value],
                            "to_vertex_collections": [
                                CollectionNames.CATEGORIES.value,
                                CollectionNames.SUBCATEGORIES1.value,
                                CollectionNames.SUBCATEGORIES2.value,
                                CollectionNames.SUBCATEGORIES3.value,
                            ],
                        },
                        {
                            "edge_collection": CollectionNames.BELONGS_TO_TOPIC.value,
                            "from_vertex_collections": [CollectionNames.RECORDS.value],
                            "to_vertex_collections": [CollectionNames.TOPICS.value],
                        },
                        {
                            "edge_collection": CollectionNames.BELONGS_TO_LANGUAGE.value,
                            "from_vertex_collections": [CollectionNames.RECORDS.value],
                            "to_vertex_collections": [CollectionNames.LANGUAGES.value],
                        },
                        {
                            "edge_collection": CollectionNames.INTER_CATEGORY_RELATIONS.value,
                            "from_vertex_collections": [CollectionNames.CATEGORIES.value, CollectionNames.SUBCATEGORIES1.value, CollectionNames.SUBCATEGORIES2.value, CollectionNames.SUBCATEGORIES3.value],
                            "to_vertex_collections": [CollectionNames.CATEGORIES.value, CollectionNames.SUBCATEGORIES1.value, CollectionNames.SUBCATEGORIES2.value, CollectionNames.SUBCATEGORIES3.value],
                        },
                        {
                            "edge_collection": CollectionNames.BELONGS_TO_KNOWLEDGE_BASE.value,
                            "from_vertex_collections": [CollectionNames.RECORDS.value],
                            "to_vertex_collections": [CollectionNames.KNOWLEDGE_BASE.value],
                        },
                        {
                            "edge_collection": CollectionNames.PERMISSIONS_TO_KNOWLEDGE_BASE.value,
                            "from_vertex_collections": [CollectionNames.USERS.value],
                            "to_vertex_collections": [CollectionNames.KNOWLEDGE_BASE.value],
                        },
                        {
                            "edge_collection": CollectionNames.IS_OF_TYPE.value,
                            "from_vertex_collections": [CollectionNames.RECORDS.value],
                            "to_vertex_collections": [CollectionNames.FILES.value],
                        },
                        {
                            "edge_collection": CollectionNames.RECORD_RELATIONS.value,
                            "from_vertex_collections": [CollectionNames.RECORDS.value],
                            "to_vertex_collections": [CollectionNames.RECORDS.value],
                        },
                        {
                            "edge_collection": CollectionNames.USER_DRIVE_RELATION.value,
                            "from_vertex_collections": [CollectionNames.USERS.value],
                            "to_vertex_collections": [CollectionNames.DRIVES.value],
                        },
                        {
                            "edge_collection": CollectionNames.USER_APP_RELATION.value,
                            "from_vertex_collections": [CollectionNames.USERS.value],
                            "to_vertex_collections": [CollectionNames.APPS.value],
                        },
                        {
                            "edge_collection": CollectionNames.ORG_APP_RELATION.value,
                            "from_vertex_collections": [CollectionNames.ORGS.value],
                            "to_vertex_collections": [CollectionNames.APPS.value],
                        },
                    ]

                    # Create all edge definitions
                    for edge_def in edge_definitions:
                        graph.create_edge_definition(**edge_def)

                    self.logger.info("✅ File access graph created successfully")

                self.logger.info("✅ Collections initialized successfully")

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

    async def _initialize_departments(self):
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

    async def disconnect(self):
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
