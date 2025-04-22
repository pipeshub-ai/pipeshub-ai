"""ArangoDB service for interacting with the database"""

# pylint: disable=E1101, W0718
import uuid

from arango import ArangoClient

from app.config.configuration_service import config_node_constants, ConfigurationService
from app.config.utils.named_constants.arangodb_constants import CollectionNames, DepartmentNames
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


class BaseArangoService():
    """Base ArangoDB service class for interacting with the database"""

    def __init__(self, logger, arango_client: ArangoClient, config: ConfigurationService):
        self.logger = logger
        self.config_service = config
        self.client = arango_client
        self.db = None

        # Collections
        self._collections = {
            # Records and Record relations
            CollectionNames.RECORDS.value: None,
            CollectionNames.RECORD_RELATIONS.value: None,
            CollectionNames.IS_OF_TYPE.value: None,

            # Drive related
            CollectionNames.DRIVES.value: None,
            CollectionNames.USER_DRIVE_RELATION.value: None,

            # Record types
            CollectionNames.FILES.value: None,
            CollectionNames.LINKS.value: None,
            CollectionNames.MAILS.value: None,

            # Users and groups
            CollectionNames.PEOPLE.value: None,
            CollectionNames.USERS.value: None,
            CollectionNames.GROUPS.value: None,
            CollectionNames.ORGS.value: None,
            CollectionNames.ANYONE.value: None,
            CollectionNames.BELONGS_TO.value: None,
            CollectionNames.PERMISSIONS.value: None,

            # History and tokens
            CollectionNames.CHANNEL_HISTORY.value: None,
            CollectionNames.PAGE_TOKENS.value: None,

            # Apps and relations
            CollectionNames.APPS.value: None,
            CollectionNames.ORG_APP_RELATION.value: None,
            CollectionNames.USER_APP_RELATION.value: None,

            # Departments
            CollectionNames.DEPARTMENTS.value: None,
            CollectionNames.BELONGS_TO_DEPARTMENT.value: None,
            CollectionNames.ORG_DEPARTMENT_RELATION.value: None,

            # Knowledge base
            CollectionNames.KNOWLEDGE_BASE.value: None,
            CollectionNames.BELONGS_TO_KNOWLEDGE_BASE.value: None,
            CollectionNames.PERMISSIONS_TO_KNOWLEDGE_BASE.value: None,

            # Categories and Classifications
            CollectionNames.CATEGORIES.value: None,
            CollectionNames.BELONGS_TO_CATEGORY.value: None,
            CollectionNames.LANGUAGES.value: None,
            CollectionNames.BELONGS_TO_LANGUAGE.value: None,
            CollectionNames.TOPICS.value: None,
            CollectionNames.BELONGS_TO_TOPIC.value: None,
            CollectionNames.SUBCATEGORIES1.value: None,
            CollectionNames.SUBCATEGORIES2.value: None,
            CollectionNames.SUBCATEGORIES3.value: None,
            CollectionNames.INTER_CATEGORY_RELATIONS.value: None,
        }

    async def connect(self) -> bool:
        """Connect to ArangoDB and initialize collections"""
        try:
            self.logger.info("🚀 Connecting to ArangoDB...")
            arangodb_config = await self.config_service.get_config(config_node_constants.ARANGODB.value)
            arango_url = arangodb_config['url']
            arango_user = arangodb_config['username']
            arango_password = arangodb_config['password']
            arango_db = arangodb_config['db']

            if not isinstance(arango_url, str):
                raise ValueError("ArangoDB URL must be a string")
            if not self.client:
                return False

            # Connect to system db to ensure our db exists
            self.logger.debug("Connecting to system db")
            sys_db = self.client.db(
                '_system',
                username=arango_user,
                password=arango_password,
                verify=True
            )
            self.logger.debug("System DB: %s", sys_db)

            # Check if database exists, but don't try to create if it does
            self.logger.debug("Checking if our database exists")
            if not sys_db.has_database(arango_db):
                try:
                    self.logger.info(
                        "🚀 Database %s does not exist. Creating...",
                        arango_db
                    )
                    sys_db.create_database(arango_db)
                    self.logger.info("✅ Database created successfully")
                except Exception as e:
                    # If database creation fails but database exists, we can continue
                    if "duplicate database name" not in str(e):
                        raise
                    self.logger.warning("Database already exists, continuing with connection")

            # Connect to our database
            self.logger.debug("Connecting to our database")
            self.db = self.client.db(
                arango_db,
                username=arango_user,
                password=arango_password,
                verify=True
            )
            self.logger.debug("Our DB: %s", self.db)

            # Initialize collections
            try:
                self._collections[CollectionNames.RECORDS.value] = (
                    self.db.collection(CollectionNames.RECORDS.value)
                    if self.db.has_collection(CollectionNames.RECORDS.value)
                    else self.db.create_collection(CollectionNames.RECORDS.value, schema=record_schema)
                )
                self._collections[CollectionNames.IS_OF_TYPE.value] = (
                    self.db.collection(CollectionNames.IS_OF_TYPE.value)
                    if self.db.has_collection(CollectionNames.IS_OF_TYPE.value)
                    else self.db.create_collection(CollectionNames.IS_OF_TYPE.value, edge=True, schema=is_of_type_schema)
                )
                self._collections[CollectionNames.RECORD_RELATIONS.value] = (
                    self.db.collection(CollectionNames.RECORD_RELATIONS.value)
                    if self.db.has_collection(CollectionNames.RECORD_RELATIONS.value)
                    else self.db.create_collection(CollectionNames.RECORD_RELATIONS.value, edge=True, schema=record_relations_schema)
                )
                self._collections[CollectionNames.DRIVES.value] = (
                    self.db.collection(CollectionNames.DRIVES.value)
                    if self.db.has_collection(CollectionNames.DRIVES.value)
                    else self.db.create_collection(CollectionNames.DRIVES.value)
                )
                # Relation between user and drive
                self._collections[CollectionNames.USER_DRIVE_RELATION.value] = (
                    self.db.collection(CollectionNames.USER_DRIVE_RELATION.value)
                    if self.db.has_collection(CollectionNames.USER_DRIVE_RELATION.value)
                    else self.db.create_collection(CollectionNames.USER_DRIVE_RELATION.value, edge=True, schema=user_drive_relation_schema)
                )
                self._collections[CollectionNames.DEPARTMENTS.value] = (
                    self.db.collection(CollectionNames.DEPARTMENTS.value)
                    if self.db.has_collection(CollectionNames.DEPARTMENTS.value)
                    else self.db.create_collection(CollectionNames.DEPARTMENTS.value, schema=department_schema)
                )
                self._collections[CollectionNames.BELONGS_TO_DEPARTMENT.value] = (
                    self.db.collection(CollectionNames.BELONGS_TO_DEPARTMENT.value)
                    if self.db.has_collection(CollectionNames.BELONGS_TO_DEPARTMENT.value)
                    else self.db.create_collection(CollectionNames.BELONGS_TO_DEPARTMENT.value, edge=True, schema=basic_edge_schema)
                )
                self._collections[CollectionNames.ORG_DEPARTMENT_RELATION.value] = (
                    self.db.collection(CollectionNames.ORG_DEPARTMENT_RELATION.value)
                    if self.db.has_collection(CollectionNames.ORG_DEPARTMENT_RELATION.value)
                    else self.db.create_collection(CollectionNames.ORG_DEPARTMENT_RELATION.value, edge=True, schema=basic_edge_schema)
                )
                self._collections[CollectionNames.BELONGS_TO.value] = (
                    self.db.collection(CollectionNames.BELONGS_TO.value)
                    if self.db.has_collection(CollectionNames.BELONGS_TO.value)
                    else self.db.create_collection(CollectionNames.BELONGS_TO.value, edge=True, schema=belongs_to_schema)
                )
                self._collections[CollectionNames.FILES.value] = (
                    self.db.collection(CollectionNames.FILES.value)
                    if self.db.has_collection(CollectionNames.FILES.value)
                    else self.db.create_collection(CollectionNames.FILES.value, schema=file_record_schema)
                )
                self._collections[CollectionNames.LINKS.value] = (
                    self.db.collection(CollectionNames.LINKS.value)
                    if self.db.has_collection(CollectionNames.LINKS.value)
                    else self.db.create_collection(CollectionNames.LINKS.value)
                )
                self._collections[CollectionNames.MAILS.value] = (
                    self.db.collection(CollectionNames.MAILS.value)
                    if self.db.has_collection(CollectionNames.MAILS.value)
                    else self.db.create_collection(CollectionNames.MAILS.value, schema=mail_record_schema)
                )
                self._collections[CollectionNames.PEOPLE.value] = (
                    self.db.collection(CollectionNames.PEOPLE.value)
                    if self.db.has_collection(CollectionNames.PEOPLE.value)
                    else self.db.create_collection(CollectionNames.PEOPLE.value)
                )
                self._collections[CollectionNames.USERS.value] = (
                    self.db.collection(CollectionNames.USERS.value)
                    if self.db.has_collection(CollectionNames.USERS.value)
                    else self.db.create_collection(CollectionNames.USERS.value, schema=user_schema)
                )
                self._collections[CollectionNames.GROUPS.value] = (
                    self.db.collection(CollectionNames.GROUPS.value)
                    if self.db.has_collection(CollectionNames.GROUPS.value)
                    else self.db.create_collection(CollectionNames.GROUPS.value)
                )
                self._collections[CollectionNames.ORGS.value] = (
                    self.db.collection(CollectionNames.ORGS.value)
                    if self.db.has_collection(CollectionNames.ORGS.value)
                    else self.db.create_collection(CollectionNames.ORGS.value, schema=orgs_schema)
                )
                self._collections[CollectionNames.ANYONE.value] = (
                    self.db.collection(CollectionNames.ANYONE.value)
                    if self.db.has_collection(CollectionNames.ANYONE.value)
                    else self.db.create_collection(CollectionNames.ANYONE.value)
                )
                self._collections[CollectionNames.PERMISSIONS.value] = (
                    self.db.collection(CollectionNames.PERMISSIONS.value)
                    if self.db.has_collection(CollectionNames.PERMISSIONS.value)
                    else self.db.create_collection(CollectionNames.PERMISSIONS.value, edge=True, schema=permissions_schema)
                )
                self._collections[CollectionNames.CHANNEL_HISTORY.value] = (
                    self.db.collection(CollectionNames.CHANNEL_HISTORY.value)
                    if self.db.has_collection(CollectionNames.CHANNEL_HISTORY.value)
                    else self.db.create_collection(CollectionNames.CHANNEL_HISTORY.value)
                )
                self._collections[CollectionNames.PAGE_TOKENS.value] = (
                    self.db.collection(CollectionNames.PAGE_TOKENS.value)
                    if self.db.has_collection(CollectionNames.PAGE_TOKENS.value)
                    else self.db.create_collection(CollectionNames.PAGE_TOKENS.value)
                )

                self._collections[CollectionNames.APPS.value] = (
                    self.db.collection(CollectionNames.APPS.value)
                    if self.db.has_collection(CollectionNames.APPS.value)
                    else self.db.create_collection(CollectionNames.APPS.value, schema=app_schema)
                )
                self._collections[CollectionNames.ORG_APP_RELATION.value] = (
                    self.db.collection(CollectionNames.ORG_APP_RELATION.value)
                    if self.db.has_collection(CollectionNames.ORG_APP_RELATION.value)
                    else self.db.create_collection(CollectionNames.ORG_APP_RELATION.value, edge=True, schema=basic_edge_schema)
                )
                self._collections[CollectionNames.USER_APP_RELATION.value] = (
                    self.db.collection(CollectionNames.USER_APP_RELATION.value)
                    if self.db.has_collection(CollectionNames.USER_APP_RELATION.value)
                    else self.db.create_collection(CollectionNames.USER_APP_RELATION.value, edge=True, schema=user_app_relation_schema)
                )

                self._collections[CollectionNames.CATEGORIES.value] = (
                    self.db.collection(CollectionNames.CATEGORIES.value)
                    if self.db.has_collection(CollectionNames.CATEGORIES.value)
                    else self.db.create_collection(CollectionNames.CATEGORIES.value)
                )
                self._collections[CollectionNames.BELONGS_TO_CATEGORY.value] = (
                    self.db.collection(CollectionNames.BELONGS_TO_CATEGORY.value)
                    if self.db.has_collection(CollectionNames.BELONGS_TO_CATEGORY.value)
                    else self.db.create_collection(CollectionNames.BELONGS_TO_CATEGORY.value, edge=True, schema=basic_edge_schema)
                )
                self._collections[CollectionNames.LANGUAGES.value] = (
                    self.db.collection(CollectionNames.LANGUAGES.value)
                    if self.db.has_collection(CollectionNames.LANGUAGES.value)
                    else self.db.create_collection(CollectionNames.LANGUAGES.value)
                )
                self._collections[CollectionNames.BELONGS_TO_LANGUAGE.value] = (
                    self.db.collection(CollectionNames.BELONGS_TO_LANGUAGE.value)
                    if self.db.has_collection(CollectionNames.BELONGS_TO_LANGUAGE.value)
                    else self.db.create_collection(CollectionNames.BELONGS_TO_LANGUAGE.value, edge=True, schema=basic_edge_schema)
                )
                self._collections[CollectionNames.TOPICS.value] = (
                    self.db.collection(CollectionNames.TOPICS.value)
                    if self.db.has_collection(CollectionNames.TOPICS.value)
                    else self.db.create_collection(CollectionNames.TOPICS.value)
                )
                self._collections[CollectionNames.BELONGS_TO_TOPIC.value] = (
                    self.db.collection(CollectionNames.BELONGS_TO_TOPIC.value)
                    if self.db.has_collection(CollectionNames.BELONGS_TO_TOPIC.value)
                    else self.db.create_collection(CollectionNames.BELONGS_TO_TOPIC.value, edge=True, schema=basic_edge_schema)
                )
                self._collections[CollectionNames.SUBCATEGORIES1.value] = (
                    self.db.collection(CollectionNames.SUBCATEGORIES1.value)
                    if self.db.has_collection(CollectionNames.SUBCATEGORIES1.value)
                    else self.db.create_collection(CollectionNames.SUBCATEGORIES1.value)
                )
                self._collections[CollectionNames.SUBCATEGORIES2.value] = (
                    self.db.collection(CollectionNames.SUBCATEGORIES2.value)
                    if self.db.has_collection(CollectionNames.SUBCATEGORIES2.value)
                    else self.db.create_collection(CollectionNames.SUBCATEGORIES2.value)
                )
                self._collections[CollectionNames.SUBCATEGORIES3.value] = (
                    self.db.collection(CollectionNames.SUBCATEGORIES3.value)
                    if self.db.has_collection(CollectionNames.SUBCATEGORIES3.value)
                    else self.db.create_collection(CollectionNames.SUBCATEGORIES3.value)
                )
                self._collections[CollectionNames.INTER_CATEGORY_RELATIONS.value] = (
                    self.db.collection(CollectionNames.INTER_CATEGORY_RELATIONS.value)
                    if self.db.has_collection(CollectionNames.INTER_CATEGORY_RELATIONS.value)
                    else self.db.create_collection(CollectionNames.INTER_CATEGORY_RELATIONS.value, edge=True, schema=basic_edge_schema)
                )
                self._collections[CollectionNames.KNOWLEDGE_BASE.value]=(
                    self.db.collection(CollectionNames.KNOWLEDGE_BASE.value)
                    if self.db.has_collection(CollectionNames.KNOWLEDGE_BASE.value)
                    else self.db.create_collection(CollectionNames.KNOWLEDGE_BASE.value, schema=kb_schema)
                )
                self._collections[CollectionNames.BELONGS_TO_KNOWLEDGE_BASE.value] = (
                    self.db.collection(CollectionNames.BELONGS_TO_KNOWLEDGE_BASE.value)
                    if self.db.has_collection(CollectionNames.BELONGS_TO_KNOWLEDGE_BASE.value)
                    else self.db.create_collection(CollectionNames.BELONGS_TO_KNOWLEDGE_BASE.value, edge=True, schema=belongs_to_schema)
                )
                self._collections[CollectionNames.PERMISSIONS_TO_KNOWLEDGE_BASE.value] = (
                    self.db.collection(CollectionNames.PERMISSIONS_TO_KNOWLEDGE_BASE.value)
                    if self.db.has_collection(CollectionNames.PERMISSIONS_TO_KNOWLEDGE_BASE.value)
                    else self.db.create_collection(CollectionNames.PERMISSIONS_TO_KNOWLEDGE_BASE.value, edge=True, schema=permissions_schema)
                )

                # Create the permissions graph
                if not self.db.has_graph(CollectionNames.FILE_ACCESS_GRAPH.value):
                    self.logger.info("🚀 Creating file access graph...")
                    graph = self.db.create_graph(CollectionNames.FILE_ACCESS_GRAPH.value)

                    # Define edge definitions for permissions and group membership
                    graph.create_edge_definition(
                        edge_collection=CollectionNames.PERMISSIONS.value,
                        from_vertex_collections=[CollectionNames.RECORDS.value],
                        to_vertex_collections=[CollectionNames.USERS.value, CollectionNames.GROUPS.value, CollectionNames.ORGS.value]
                    )

                    graph.create_edge_definition(
                        edge_collection=CollectionNames.BELONGS_TO.value,
                        from_vertex_collections=[CollectionNames.USERS.value],
                        to_vertex_collections=[CollectionNames.GROUPS.value, CollectionNames.ORGS.value]
                    )

                    # Define edge definitions for record classifications
                    graph.create_edge_definition(
                        edge_collection=CollectionNames.BELONGS_TO_DEPARTMENT.value,
                        from_vertex_collections=[CollectionNames.RECORDS.value],
                        to_vertex_collections=[CollectionNames.DEPARTMENTS.value]
                    )

                    graph.create_edge_definition(
                        edge_collection=CollectionNames.BELONGS_TO_CATEGORY.value,
                        from_vertex_collections=[CollectionNames.RECORDS.value],
                        to_vertex_collections=[CollectionNames.CATEGORIES.value, CollectionNames.SUBCATEGORIES1.value, CollectionNames.SUBCATEGORIES2.value, CollectionNames.SUBCATEGORIES3.value]
                    )

                    graph.create_edge_definition(
                        edge_collection=CollectionNames.BELONGS_TO_KNOWLEDGE_BASE.value,
                        from_vertex_collections=[CollectionNames.RECORDS.value],
                        to_vertex_collections=[CollectionNames.KNOWLEDGE_BASE.value]
                    )

                    graph.create_edge_definition(
                        edge_collection=CollectionNames.PERMISSIONS_TO_KNOWLEDGE_BASE.value,
                        from_vertex_collections=[CollectionNames.USERS.value],
                        to_vertex_collections=[CollectionNames.KNOWLEDGE_BASE.value]
                    )

                    graph.create_edge_definition(
                        edge_collection=CollectionNames.IS_OF_TYPE.value,
                        from_vertex_collections=[CollectionNames.RECORDS.value],
                        to_vertex_collections=[CollectionNames.FILES.value]
                    )

                    self.logger.info("✅ File access graph created successfully")

                self.logger.info("✅ Collections initialized successfully")

                # Initialize departments collection with predefined department types
                departments = [
                    {
                        "_key": str(uuid.uuid4()),
                        'departmentName': dept.value,
                        "orgId": None
                    }
                    for dept in DepartmentNames
                ]

                # Bulk insert departments if not already present
                existing_department_names = set(
                    doc['departmentName'] for doc in self._collections[CollectionNames.DEPARTMENTS.value].all()
                )

                new_departments = [
                    dept for dept in departments
                    if dept['departmentName'] not in existing_department_names
                ]

                if new_departments:
                    self.logger.info(f"🚀 Inserting {len(new_departments)} departments")
                    self._collections[CollectionNames.DEPARTMENTS.value].insert_many(new_departments)
                    self.logger.info("✅ Departments initialized successfully")

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

            bind_vars = {
                'active': active
            }

            cursor = self.db.aql.execute(query, bind_vars=bind_vars)
            return list(cursor)
        except Exception as e:
            self.logger.error(f"Failed to get organizations: {str(e)}")
            raise
